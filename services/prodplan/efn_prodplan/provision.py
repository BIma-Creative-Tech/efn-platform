"""Provisioning JIT identitas ESB ke Odoo (docs/04 §30.17.11; rancangan Odoo fit-gap G66b, modul `efn_esb_identity`).

- Flag `PRODPLAN_ODOO_PROVISION_ENABLED` (default `false`): mati -> tidak ada antrean, thread, maupun koneksi.
- Non-blocking: login hanya memanggil `enqueue` (antrean memori `maxsize=100`, penuh -> `dropped`); satu thread
  `odoo-provision` mengirim satu percobaan per login, timeout 5 dtk. Gagal -> log + metrik, diulang alami pada login
  berikutnya. Tidak pernah memengaruhi respons login (Odoo mati -> login tetap 201).
- Dedup memori: payload identik yang sukses < 24 jam -> `skipped`.
- mTLS verify-full ke `odoo-rpc-gateway:8443` (CA internal, sertifikat klien `prodplan-odoo`), header
  `Authorization: bearer <API key efn_esb_provisioner>`. Payload tertutup 7 kunci, TANPA password/token.
"""

import hashlib
import http.client
import json
import logging
import queue
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from efn_prodplan import metrics
from efn_prodplan.config import OdooProvisionSettings
from efn_prodplan.logs import log_event

PROVISION_PATH = "/json/2/efn.esb.identity/efn_esb_provision"
QUEUE_MAX = 100
TIMEOUT_SECONDS = 5.0
DEDUP_SECONDS = 86_400.0
RESPONSE_MAX_BYTES = 16 * 1024
BODY_MAX_BYTES = 16_384
PAYLOAD_KEYS = ("esb_account", "username", "full_name", "company_code", "role_id", "pp_roles", "branch_ids")

Sender = Callable[[bytes], int]


def _clean_name(full_name: str | None) -> str:
    """Odoo (`validate_payload`) menolak seluruh payload bila `full_name` memuat karakter kontrol (<32 atau 127):
    ganti dengan spasi agar identitas tidak gagal dibuat hanya karena nama ESB berisi tab/newline (QA G66b)."""
    text = "".join(" " if ord(ch) < 32 or ord(ch) == 127 else ch for ch in (full_name or ""))
    return " ".join(text.split())[:128]


def provision_payload(
    esb_account: str,
    username_norm: str,
    full_name: str,
    company_code: str,
    role_id: int | None,
    pp_roles: Iterable[str],
    branch_ids: Iterable[int],
) -> dict[str, Any]:
    """Body `/json/2` (§30.17.11): `{"payload": {...tepat 7 kunci...}}`. Bentuk `/json/2` belum terverifikasi."""
    return {
        "payload": {
            "esb_account": esb_account,
            "username": username_norm,
            "full_name": _clean_name(full_name),
            "company_code": company_code,
            "role_id": role_id,
            "pp_roles": sorted(set(pp_roles)),
            "branch_ids": sorted({int(b) for b in branch_ids})[:200],
        }
    }


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class OdooProvisioner:
    def __init__(self, send: Sender | None, *, enabled: bool, clock: Callable[[], float] = time.monotonic) -> None:
        self._send = send
        self._enabled = bool(enabled and send is not None)
        self._clock = clock
        self._queue: queue.Queue[tuple[bytes, int]] = queue.Queue(maxsize=QUEUE_MAX)
        self._done: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enqueue(self, payload: Mapping[str, Any], actor_id: int) -> None:
        if not self._enabled:
            return
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        if len(body) > BODY_MAX_BYTES:  # gateway haproxy menolak body > 16 KiB
            metrics.ODOO_PROVISION.inc("dropped")
            log_event("odoo_provision_dropped", logging.WARNING, actor_id=actor_id, reason="body_too_large")
            return
        now = self._clock()
        with self._lock:
            done_at = self._done.get(_digest(body))
            if done_at is not None and now - done_at < DEDUP_SECONDS:
                metrics.ODOO_PROVISION.inc("skipped")
                return
        try:
            self._queue.put_nowait((body, actor_id))
        except queue.Full:
            metrics.ODOO_PROVISION.inc("dropped")
            log_event("odoo_provision_dropped", logging.WARNING, actor_id=actor_id)

    def run_once(self, timeout: float | None = None) -> bool:
        """Kirim satu item antrean. False bila antrean kosong (setelah `timeout`)."""
        try:
            body, actor_id = self._queue.get(timeout=timeout) if timeout else self._queue.get_nowait()
        except queue.Empty:
            return False
        status = 0
        reason = "ok"
        try:
            status = self._send(body) if self._send is not None else 0
            if status != 200:
                reason = "http_error"
        except Exception as exc:  # noqa: BLE001 - Odoo mati/timeout tidak boleh mematikan thread
            reason = type(exc).__name__
        if reason == "ok":
            metrics.ODOO_PROVISION.inc("ok")
            now = self._clock()
            with self._lock:
                self._done[_digest(body)] = now
                if len(self._done) > 10_000:
                    self._done = {k: v for k, v in self._done.items() if now - v < DEDUP_SECONDS}
        else:
            metrics.ODOO_PROVISION.inc("failed")
            log_event(
                "odoo_provision_failed", logging.WARNING, actor_id=actor_id, http_status=status, reason_class=reason
            )
        return True

    def run_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once(timeout=1.0)


def odoo_ssl_context(settings: OdooProvisionSettings) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=settings.ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(settings.cert_file, settings.key_file)
    return context


def https_sender(settings: OdooProvisionSettings) -> Sender:
    """POST mTLS ke gateway RPC Odoo; kembalikan status HTTP. API key hanya di header (tidak dilog)."""
    parts = urllib.parse.urlsplit(settings.url)
    host, port = parts.hostname or "", parts.port or 443
    context = odoo_ssl_context(settings)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"bearer {settings.api_key}",
        "User-Agent": "efn-prodplan/0.1",
    }

    def send(body: bytes) -> int:
        conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT_SECONDS, context=context)
        try:
            conn.request("POST", PROVISION_PATH, body=body, headers=headers)
            response = conn.getresponse()
            response.read(RESPONSE_MAX_BYTES)  # isi tidak dipakai/dilog (tanpa PII menurut kontrak)
            return int(response.status)
        finally:
            conn.close()

    return send


def build_provisioner(settings: OdooProvisionSettings) -> OdooProvisioner:
    if not settings.enabled:
        return OdooProvisioner(None, enabled=False)
    return OdooProvisioner(https_sender(settings), enabled=True)
