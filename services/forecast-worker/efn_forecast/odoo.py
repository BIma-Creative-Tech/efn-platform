"""Klien /json/2 ke odoo-rpc-gateway untuk 5 metode forecast (docs/04 §28.2, L-145).

- `POST <ODOO_RPC_URL>/json/2/<model>/<metode>`, TLS verify-full CA internal + sertifikat klien CN `forecast-worker`,
  `Authorization: bearer <API key PT>`. Body = argumen bernama = objek payload §28.2 apa adanya (pola BFF json2-client:
  argumen bernama; tanpa `context` agar `additionalProperties: false` tetap terpenuhi).
- Retry HANYA untuk jaringan/timeout/429/5xx (gateway maxconn 1 + antrean -> 503 saat penuh), backoff eksponensial +
  jitter. Aman: setiap metode idempoten di Odoo (`run_id`, `(run_id, chunk_no)`, kunci kalender). 4xx lain (422 galat
  validasi `efn_forecast:<kode>`, 403 company_mismatch/access_denied) tidak diulang -> run ditandai failed.
- Respons divalidasi longgar: hanya field yang dipakai (`ok`, `run_id`, `chunk_no`, `rejected`); field tambahan Odoo
  (`state`, `is_active`, `created`, `duplicate_close`, `unchanged`, `skipped_manual`, ...) diabaikan.
- Log hanya metode, status HTTP, percobaan, kode error — tidak pernah body, Authorization, atau isi respons mentah.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1024 * 1024
BACKOFF_SECONDS = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)
METHODS: Mapping[str, tuple[str, str]] = {
    "calendar_upsert": ("efn.forecast.calendar", "efn_forecast_calendar_upsert"),
    "run_open": ("efn.forecast.run", "efn_forecast_run_open"),
    "ingest": ("efn.forecast.daily", "efn_forecast_ingest"),
    "minmax_ingest": ("efn.demand.forecast.line", "efn_minmax_ingest"),
    "run_close": ("efn.forecast.run", "efn_forecast_run_close"),
}


class OdooError(RuntimeError):
    """kind: auth | company_mismatch | rejected | not_found | transport | invalid_response."""

    def __init__(self, kind: str, code: str = "", status: int = 0) -> None:
        super().__init__(f"{kind}:{code}" if code else kind)
        self.kind = kind
        self.code = code
        self.status = status


class Transport(Protocol):
    def post(self, path: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]: ...


class RequestsTransport:
    """HTTPS + mTLS lewat requests. `trust_env=False` (tanpa proxy env), tanpa redirect."""

    def __init__(self, base_url: str, ca_file: str | None, cert_file: str | None, key_file: str | None) -> None:
        import requests

        self._base = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.verify = ca_file if ca_file else True
        if cert_file and key_file:
            self._session.cert = (cert_file, key_file)

    def post(self, path: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        resp = self._session.post(
            f"{self._base}/json/2/{path}",
            data=body,
            headers=dict(headers),
            timeout=timeout,
            allow_redirects=False,
            stream=True,
        )
        try:
            content = resp.raw.read(MAX_RESPONSE_BYTES + 1, decode_content=True)
        finally:
            resp.close()
        if len(content) > MAX_RESPONSE_BYTES:
            raise OdooError("invalid_response", "too_large", resp.status_code)
        return resp.status_code, content


_PREFIX = "efn_forecast:"


def _error_code(body: bytes) -> str:
    """Kode aman dari body error json2. Odoo `efn_demand_forecast` melempar `UserError("efn_forecast:<kode>")`
    (HTTP 422)
    / `AccessError("efn_forecast:company_mismatch|access_denied")` (403) -> body `{name, message, ...}`;
    bentuk bersarang
    `{error: {message|data.message}}` dan `{error: "<kode>"}` (§28.2) juga diterima. Teks lain (traceback) dibuang."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    candidates: list[Any] = [parsed.get("message"), parsed.get("error"), parsed.get("code")]
    err = parsed.get("error")
    if isinstance(err, dict):
        candidates.append(err.get("message"))
        data = err.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("message"))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        text = candidate.strip()
        if text.startswith(_PREFIX):
            text = text[len(_PREFIX) :]
        if text.isidentifier() and len(text) <= 64:
            return text
    return ""


@dataclass
class OdooForecastClient:
    transport: Transport
    api_key: str
    database: str = ""
    timeout_seconds: float = 25.0
    max_retries: int = 6
    sleep: Callable[[float], None] = time.sleep

    def __repr__(self) -> str:  # tanpa API key
        return "OdooForecastClient()"

    def call(self, name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        model, method = METHODS[name]
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"bearer {self.api_key}",
        }
        if self.database:
            headers["X-Odoo-Database"] = self.database
        attempt = 0
        while True:
            try:
                status, content = self.transport.post(f"{model}/{method}", body, headers, self.timeout_seconds)
            except OdooError:
                raise
            except Exception as exc:  # jaringan/timeout/TLS -> transport (diulang)
                status, content, err = 0, b"", OdooError("transport", type(exc).__name__)
            else:
                err = self._classify(status, content)
                if err is None:
                    return self._parse(content)
            if err.kind != "transport" or attempt >= self.max_retries:
                log.warning("odoo %s gagal: %s status=%s percobaan=%d", method, err.kind, status, attempt + 1)
                raise err
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)] + secrets.randbelow(500) / 1000
            log.info("odoo %s %s status=%s, ulang dalam %.1f s", method, err.code or err.kind, status, delay)
            self.sleep(delay)
            attempt += 1

    @staticmethod
    def _classify(status: int, content: bytes) -> OdooError | None:
        if status == 200:
            return None
        code = _error_code(content)
        if status == 429 or status >= 500 or status == 0:
            return OdooError("transport", code or f"http_{status}", status)
        if code == "company_mismatch":
            return OdooError("company_mismatch", code, status)
        if status in (401, 403):  # termasuk 403 efn_forecast:access_denied (bukan user bot / grup tambahan)
            return OdooError("auth", code, status)
        if status == 404:
            return OdooError("not_found", code, status)
        return OdooError("rejected", code, status)

    @staticmethod
    def _parse(content: bytes) -> dict[str, Any]:
        try:
            parsed = json.loads(content) if content else None
        except ValueError:
            raise OdooError("invalid_response", "not_json") from None
        if not isinstance(parsed, dict):
            raise OdooError("invalid_response", "not_object")
        return parsed

    # ------------------------------------------------------------ 5 metode §28.2
    def calendar_upsert(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.call("calendar_upsert", payload)

    def run_open(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.call("run_open", payload)

    def ingest(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._chunk_result(self.call("ingest", payload), payload)

    def minmax_ingest(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._chunk_result(self.call("minmax_ingest", payload), payload)

    def run_close(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.call("run_close", payload)

    @staticmethod
    def _chunk_result(result: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        """Respons §28.2: ok, run_id, chunk_no, accepted, rejected[], duplicate_chunk. Chunk duplikat = sukses
        (retry setelah timeout yang sebenarnya sudah tersimpan)."""
        if result.get("ok") is not True:
            raise OdooError("invalid_response", "not_ok")
        if result.get("run_id") not in (None, payload.get("run_id")) or result.get("chunk_no") not in (
            None,
            payload.get("chunk_no"),
        ):
            raise OdooError("invalid_response", "chunk_mismatch")
        rejected = result.get("rejected") or []
        if not isinstance(rejected, list):
            raise OdooError("invalid_response", "rejected_not_list")
        return result
