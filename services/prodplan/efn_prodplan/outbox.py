"""Worker outbox ESB (docs/04 §30.9). Polling PostgreSQL, satu dokumen per rencana pada satu waktu, urut `seq`.

Mesin status:
  pending -> sending (commit DULU) -> sent (200 + nomor) | failed (4xx) | pending (gagal sebelum kirim, backoff 30s-10m)
           | unknown (timeout/5xx/putus setelah kirim)
  unknown -> sent (rekonsiliasi GET menemukan marker) | failed `not_found_after_timeout` (lewat RECONCILE_GRACE DAN
             seluruh halaman daftar terbaca tuntas; tanpa kepastian tetap `unknown`, SEC-225)
  sent -> confirmed (GET detail cocok marker, simpan statusID). Baris ber-nomor tidak pernah turun ke unknown/failed
          (SEC-226): GET gagal / dokumen tak terlihat / marker beda -> tetap `sent` + `last_error_code` + backoff.
  delete_pending -> deleting -> deleted (DELETE 200 / dokumen sudah tidak ada) | delete_failed (ESB menolak)
TIDAK ada kirim ulang POST otomatis setelah request terkirim; `failed` hanya kembali ke `pending` lewat
`POST /v1/outbox/{id}/resend` (manusia). `PRODPLAN_WRITE_ENABLED=false` -> `pending`/`delete_pending` tidak diproses,
rekonsiliasi GET tetap jalan.

§30.17.9 (amandemen A1): setiap panggilan memakai token sesi hidup aktor `coalesce(run_as_sub, created_by_sub)`
(`client_for(actor_id, brand, min_remaining)`; 120 dtk untuk POST/DELETE, 30 dtk untuk GET). Tanpa token layak:
  pending / delete_pending -> `needs_login` TANPA panggilan ESB;
  sent / unknown / deleting -> status tetap, `last_error_code='needs_login'`, dicoba lagi 60 dtk (GET saja).
401 saat POST/DELETE -> sesi dibuang (klien) + baris `needs_login`; karena `attempts > 0`, resume -> `unknown`
(rekonsiliasi marker dulu, tidak ada POST kedua). Tidak ada login ulang otomatis.
"""

import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from efn_prodplan import metrics
from efn_prodplan.esb import (
    DOC_BASE,
    EsbAuthError,
    EsbError,
    EsbNotSent,
    EsbRejected,
    EsbSentUnknown,
    page_rows,
)
from efn_prodplan.logs import log_event
from efn_prodplan.payloads import doc_marker, doc_status_id, list_doc_numbers
from efn_prodplan.store import OutboxStore
from efn_prodplan.workflow import NEEDS_LOGIN, run_as

LEASE_SECONDS = 120
RECHECK_SECONDS = 30
NEEDS_LOGIN_RECHECK_SECONDS = 60
WRITE_MIN_REMAINING = 120
READ_MIN_REMAINING = 30
RECONCILE_PAGE_LIMIT = 100
RECONCILE_MAX_PAGES = 50
RESENDABLE_ERRORS = frozenset({"esb_rejected", "not_sent", "client_error", "not_found_after_timeout"})
BACKOFF_MIN_SECONDS = 30
BACKOFF_MAX_SECONDS = 600
# Status baris dengan seq LEBIH TINGGI yang menahan DELETE baris seq lebih rendah (urutan seq menurun).
_DELETE_BLOCKERS = frozenset(
    {"confirmed", "delete_pending", "deleting", "delete_failed", "sent", "unknown", "sending", NEEDS_LOGIN}
)


class EsbDocClient(Protocol):
    def post_doc(self, kind: str, body: Mapping[str, Any]) -> str: ...

    def delete_doc(self, kind: str, num: str) -> str: ...

    def get_doc(self, kind: str, num: str) -> dict[str, Any] | None: ...

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any: ...


def backoff_seconds(attempts: int) -> int:
    """30s, 60s, 120s, ... maks 600s."""
    return min(BACKOFF_MAX_SECONDS, BACKOFF_MIN_SECONDS * (2 ** max(0, attempts - 1)))


def pick_next(
    rows: Sequence[Mapping[str, Any]], write_enabled: bool, now: datetime
) -> tuple[Mapping[str, Any], str] | None:
    """Pilih satu (baris, aksi) yang boleh dikerjakan. Murni. `rows` = semua baris outbox rencana terkait."""
    by_plan: dict[Any, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_plan.setdefault(row["plan_id"], []).append(row)
    choices: list[tuple[datetime, str, int, Mapping[str, Any], str]] = []
    for plan_id, plan_rows in by_plan.items():
        ordered = sorted(plan_rows, key=lambda r: r["seq"])
        for row in ordered:
            if row["next_attempt_at"] > now:
                continue
            status = row["status"]
            action: str | None = None
            if status == "sent":
                action = "confirm"
            elif status == "unknown":
                action = "reconcile"
            elif status == "deleting":
                action = "delete_check"
            elif status == "pending" and write_enabled:
                if all(r["status"] == "confirmed" for r in ordered if r["seq"] < row["seq"]):
                    action = "send"
            elif status == "delete_pending" and write_enabled:
                if not any(r["status"] in _DELETE_BLOCKERS for r in ordered if r["seq"] > row["seq"]):
                    action = "delete"
            if action is not None:
                choices.append((row["next_attempt_at"], str(plan_id), row["seq"], row, action))
    if not choices:
        return None
    choices.sort(key=lambda c: (c[0], c[1], c[2]))
    _, _, _, row, action = choices[0]
    return row, action


def _date_range(production_date: date) -> dict[str, str]:
    return {
        "dateFrom": (production_date - timedelta(days=1)).isoformat(),
        "dateTo": (production_date + timedelta(days=1)).isoformat(),
    }


@dataclass(frozen=True)
class ReconcileResult:
    """`number` terisi = dokumen ber-marker ditemukan. `complete=False` = daftar tidak terbaca tuntas (batas halaman,
    galat ESB, detail tidak terbaca) sehingga "tidak ditemukan" TIDAK boleh disimpulkan (SEC-225)."""

    number: str | None
    status_id: int | None
    complete: bool


def find_by_marker(
    client: EsbDocClient,
    row: Mapping[str, Any],
    known: set[str],
    *,
    limit: int = RECONCILE_PAGE_LIMIT,
    max_pages: int = RECONCILE_MAX_PAGES,
) -> ReconcileResult:
    """Rekonsiliasi §30.9 dengan paginasi penuh. Transfer: filter `additionalInfo` (dihormati ESB, substring). Produksi:
    semua dokumen tanggal±1 + cabang, marker dicek di detail (`additionalInfo`/`notes` baris)."""
    kind = row["doc_kind"]
    marker = row["marker"]
    base: dict[str, Any] = _date_range(row["production_date"])
    if kind == "simple_manufacturing":
        base["branchID"] = row["branch_id"]
    else:
        base["additionalInfo"] = marker
    complete = True
    seen = 0
    page = 1
    while True:
        try:
            result = client.get(DOC_BASE[kind], {**base, "page": page, "limit": limit})
        except EsbAuthError:
            raise  # token pengguna ditolak -> pemanggil menandai `needs_login` / `session_expired`
        except EsbError:
            return ReconcileResult(None, None, False)
        rows = page_rows(result)
        for listed in rows:
            numbers = list_doc_numbers(kind, [listed])
            if not numbers:
                continue
            number = numbers[0]
            if number in known and number != row.get("esb_doc_number"):
                continue  # milik baris outbox lain
            if kind != "simple_manufacturing" and "additionalInfo" in listed:
                if doc_marker(listed) == marker:
                    return ReconcileResult(number, doc_status_id(listed), True)
                continue
            try:
                detail = client.get_doc(kind, number)
            except EsbAuthError:
                raise
            except EsbError:
                complete = False
                continue
            if detail is None:
                complete = False  # ada di daftar tetapi detail tak terbaca -> tidak pasti
                continue
            if doc_marker(detail) == marker:
                return ReconcileResult(number, doc_status_id(detail), True)
        seen += len(rows)
        if isinstance(result, list) or not rows:
            break
        count = result.get("count") if isinstance(result, Mapping) else None
        if isinstance(count, int) and not isinstance(count, bool):
            if seen >= count:
                break
        elif len(rows) < limit:
            break
        page += 1
        if page > max_pages:
            complete = False
            break
    return ReconcileResult(None, None, complete)


ClientFor = Callable[[int, str, int], EsbDocClient | None]


class OutboxWorker:
    def __init__(
        self,
        store: OutboxStore,
        client_for: ClientFor,
        *,
        esb_env: str,
        write_enabled: bool,
        reconcile_grace_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        """`client_for(actor_id, brand, min_remaining)` -> klien bertoken sesi hidup aktor itu, atau None."""
        self._store = store
        self._client_for = client_for
        self._env = esb_env
        self._write = write_enabled
        self._grace = timedelta(seconds=reconcile_grace_seconds)
        self._clock = clock

    def start(self) -> None:
        recovered = self._store.recover_sending()
        if recovered:
            log_event("outbox_recovered_sending", logging.WARNING, count=recovered)

    def run_once(self) -> bool:
        """Kerjakan satu baris. True bila ada yang dikerjakan."""
        now = self._clock()
        picked = pick_next(self._store.candidates(self._env, now), self._write, now)
        if picked is None:
            return False
        row, action = picked
        writes = action in ("send", "delete") or (action == "delete_check" and self._write)
        min_remaining = WRITE_MIN_REMAINING if writes else READ_MIN_REMAINING
        client = self._client_for(run_as(row), row["esb_brand"], min_remaining)
        if client is None:
            self._no_token(row, now)
            return True
        handler = {
            "send": self._send,
            "confirm": self._confirm,
            "reconcile": self._reconcile,
            "delete": self._delete,
            "delete_check": self._delete_check,
        }[action]
        handler(row, client, now)
        return True

    # ------------------------------------------------------------------ tanpa token (§30.17.9)
    def _to_needs_login(self, row: Mapping[str, Any]) -> None:
        self._finish(row, {"status": NEEDS_LOGIN, "last_error_code": NEEDS_LOGIN}, "outbox_needs_login")

    def _mark_waiting(self, row: Mapping[str, Any], now: datetime) -> None:
        """sent/unknown/deleting: status tetap; dicoba lagi (GET saja) saat aktor itu login lagi."""
        retry_at = now + timedelta(seconds=NEEDS_LOGIN_RECHECK_SECONDS)
        self._store.update(row["id"], {"last_error_code": NEEDS_LOGIN, "next_attempt_at": retry_at})

    def _no_token(self, row: Mapping[str, Any], now: datetime) -> None:
        status = row["status"]
        if status in ("pending", "delete_pending"):
            if self._store.claim(row["id"], status, None, now):
                self._to_needs_login(row)  # tanpa panggilan ESB
            return
        if self._store.claim(row["id"], status, None, now + timedelta(seconds=NEEDS_LOGIN_RECHECK_SECONDS)):
            self._mark_waiting(row, now)

    # ------------------------------------------------------------------ aksi
    def _finish(self, row: Mapping[str, Any], fields: dict[str, Any], audit_action: str | None = None) -> None:
        self._store.update(row["id"], fields)
        log_event(
            "outbox_transition",
            outbox_id=str(row["id"]),
            plan_id=str(row["plan_id"]),
            status=fields.get("status"),
            error_code=fields.get("last_error_code"),
            http_status=fields.get("last_http_status"),
        )
        if audit_action:
            detail = {
                "outbox_id": str(row["id"]),
                "doc_kind": row["doc_kind"],
                "esb_doc_number": fields.get("esb_doc_number") or row.get("esb_doc_number"),
                "error_code": fields.get("last_error_code"),
            }
            self._store.audit(row, audit_action, detail)

    def _send(self, row: Mapping[str, Any], client: EsbDocClient, now: datetime) -> None:
        # Status `sending` di-commit SEBELUM POST: crash di tengah -> `unknown` saat start (bukan kirim ulang buta).
        if not self._store.claim(row["id"], "pending", "sending", now + timedelta(seconds=LEASE_SECONDS)):
            return
        attempts = int(row.get("attempts") or 0) + 1
        try:
            number = client.post_doc(row["doc_kind"], row["request_body"])
        except EsbRejected as exc:
            self._finish(
                row,
                {"status": "failed", "last_http_status": exc.status, "last_error_code": "esb_rejected"},
                "outbox_failed",
            )
            return
        except EsbAuthError:
            # 401: sesi dibuang (klien). attempts sudah > 0 -> resume menjadikan `unknown` (rekonsiliasi dulu).
            self._to_needs_login(row)
            return
        except EsbNotSent:
            retry_at = self._clock() + timedelta(seconds=backoff_seconds(attempts))
            self._finish(row, {"status": "pending", "last_error_code": "not_sent", "next_attempt_at": retry_at})
            return
        except EsbSentUnknown:
            self._finish(
                row,
                {
                    "status": "unknown",
                    "last_error_code": "sent_unknown",
                    "next_attempt_at": self._clock() + timedelta(seconds=RECHECK_SECONDS),
                },
            )
            return
        except EsbError:
            # Kesalahan lain dari klien (mis. allowlist) -> jangan kirim ulang otomatis.
            self._finish(row, {"status": "unknown", "last_error_code": "client_error"})
            return
        try:
            self._finish(
                row,
                {
                    "status": "sent",
                    "esb_doc_number": number,
                    "last_http_status": 200,
                    "last_error_code": None,
                    "next_attempt_at": self._clock(),
                },
            )
        except Exception:
            # POST berhasil tetapi pencatatan gagal (uji staging 24 Nov 2026: ESB memakai ulang nomor dokumen yang
            # sudah dihapus -> bentrok indeks unik). Jangan tinggalkan `sending`: jadikan `unknown` agar rekonsiliasi
            # marker mengadopsi dokumen itu, bukan POST ulang.
            self._store.update(
                row["id"],
                {
                    "status": "unknown",
                    "last_error_code": "record_failed",
                    "next_attempt_at": self._clock() + timedelta(seconds=RECHECK_SECONDS),
                },
            )
            raise

    def _confirm(self, row: Mapping[str, Any], client: EsbDocClient, now: datetime) -> None:
        if not self._store.claim(row["id"], "sent", None, now + timedelta(seconds=LEASE_SECONDS)):
            return
        try:
            detail = client.get_doc(row["doc_kind"], row["esb_doc_number"])
        except EsbAuthError:
            self._mark_waiting(row, now)
            return
        except EsbError:
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})
            return
        # SEC-226: baris yang sudah memegang nomor dokumen TIDAK pernah turun ke unknown/failed; tetap `sent` + backoff.
        if detail is None:
            sent_at = row.get("sent_at") or now
            if now - sent_at >= self._grace:
                if row.get("last_error_code") != "doc_not_found":
                    self._finish(row, {"last_error_code": "doc_not_found"}, "outbox_doc_not_found")
                self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=BACKOFF_MAX_SECONDS)})
            else:
                self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})
            return
        if doc_marker(detail) != row["marker"]:
            if row.get("last_error_code") != "marker_mismatch":
                self._finish(row, {"last_error_code": "marker_mismatch"}, "outbox_marker_mismatch")
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=BACKOFF_MAX_SECONDS)})
            return
        fields = {
            "status": "confirmed",
            "esb_status_id": doc_status_id(detail),
            "confirmed_at": now,
            "last_error_code": None,
        }
        self._finish(row, fields, "outbox_confirmed")

    def _reconcile(self, row: Mapping[str, Any], client: EsbDocClient, now: datetime) -> None:
        if not self._store.claim(row["id"], "unknown", None, now + timedelta(seconds=LEASE_SECONDS)):
            return
        if row.get("esb_doc_number"):
            # Data lama: sudah punya nomor -> kembali ke `sent` untuk dikonfirmasi, tidak pernah `failed` (SEC-226).
            self._finish(row, {"status": "sent", "next_attempt_at": now})
            return
        try:
            result = find_by_marker(client, row, self._store.known_numbers(self._env, row["doc_kind"]))
        except EsbAuthError:
            self._mark_waiting(row, now)
            return
        if result.number is not None:
            self._finish(
                row,
                {
                    "status": "sent",
                    "esb_doc_number": result.number,
                    "esb_status_id": result.status_id,
                    "last_error_code": None,
                    "next_attempt_at": now,
                },
            )
            return
        sent_at = row.get("sent_at") or now
        if result.complete and now - sent_at >= self._grace:
            self._finish(row, {"status": "failed", "last_error_code": "not_found_after_timeout"}, "outbox_failed")
        elif not result.complete:
            # Tanpa kepastian -> tetap `unknown` (SEC-225), dicoba lagi dengan jeda lebih panjang.
            fields: dict[str, Any] = {"next_attempt_at": now + timedelta(seconds=LEASE_SECONDS)}
            if now - sent_at >= self._grace:
                fields["last_error_code"] = "reconcile_incomplete"
            self._store.update(row["id"], fields)
        else:
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})

    def _delete(self, row: Mapping[str, Any], client: EsbDocClient, now: datetime) -> None:
        if not self._store.claim(row["id"], "delete_pending", "deleting", now + timedelta(seconds=LEASE_SECONDS)):
            return
        self._do_delete(row, client, now)

    def _do_delete(self, row: Mapping[str, Any], client: EsbDocClient, now: datetime) -> None:
        number = row.get("esb_doc_number")
        if not number:
            self._finish(row, {"status": "deleted", "deleted_at": now}, "outbox_deleted")
            return
        # ESB memakai ulang nomor dokumen yang sudah dihapus (uji staging 24 Nov 2026): pastikan dokumen bernomor ini
        # masih milik baris ini (marker cocok) SEBELUM DELETE, agar dokumen orang lain tidak pernah terhapus.
        try:
            current = client.get_doc(row["doc_kind"], number)
        except EsbAuthError:
            self._delete_auth_lost(row, now)
            return
        except EsbError:
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})
            return
        if current is None:
            self._finish(row, {"status": "deleted", "deleted_at": now, "last_http_status": 404}, "outbox_deleted")
            return
        if doc_marker(current) != row["marker"]:
            self._finish(row, {"status": "delete_failed", "last_error_code": "marker_mismatch"}, "outbox_delete_failed")
            return
        try:
            outcome = client.delete_doc(row["doc_kind"], number)
        except EsbAuthError:
            self._delete_auth_lost(row, now)  # 401 = DELETE tidak diproses
            return
        except EsbRejected as exc:
            try:
                still_there = client.get_doc(row["doc_kind"], number) is not None
            except EsbError:
                still_there = True
            if still_there:
                self._finish(
                    row,
                    {"status": "delete_failed", "last_http_status": exc.status, "last_error_code": "esb_rejected"},
                    "outbox_delete_failed",
                )
            else:
                self._finish(row, {"status": "deleted", "deleted_at": now}, "outbox_deleted")
            return
        except EsbNotSent:
            retry_at = now + timedelta(seconds=backoff_seconds(int(row.get("attempts") or 1)))
            self._finish(row, {"status": "delete_pending", "last_error_code": "not_sent", "next_attempt_at": retry_at})
            return
        except EsbError:
            # Hasil tidak diketahui -> tetap `deleting`, dicek ulang lewat GET.
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})
            return
        fields = {
            "status": "deleted",
            "deleted_at": now,
            "last_error_code": None,
            "last_http_status": 200 if outcome == "deleted" else 404,
        }
        self._finish(row, fields, "outbox_deleted")

    def _delete_auth_lost(self, row: Mapping[str, Any], now: datetime) -> None:
        """Token ditolak di jalur hapus: dari `delete_pending` -> `needs_login` (nomor tetap; resume -> delete_pending);
        dari `deleting` (DELETE sebelumnya tak pasti) -> status tetap, tunggu login (GET dulu)."""
        if row["status"] == "delete_pending":
            self._to_needs_login(row)
        else:
            self._mark_waiting(row, now)

    def _delete_check(self, row: Mapping[str, Any], client: EsbDocClient, now: datetime) -> None:
        if not self._store.claim(row["id"], "deleting", None, now + timedelta(seconds=LEASE_SECONDS)):
            return
        number = row.get("esb_doc_number")
        try:
            exists = bool(number) and client.get_doc(row["doc_kind"], number) is not None
        except EsbAuthError:
            self._mark_waiting(row, now)
            return
        except EsbError:
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})
            return
        if not exists:
            self._finish(row, {"status": "deleted", "deleted_at": now}, "outbox_deleted")
        elif self._write:
            self._do_delete(row, client, now)
        else:
            self._store.update(row["id"], {"next_attempt_at": now + timedelta(seconds=RECHECK_SECONDS)})

    # ------------------------------------------------------------------ metrik
    def refresh_metrics(self) -> None:
        counts, oldest = self._store.status_counts(self._env)
        metrics.OUTBOX_ROWS.replace({(status,): float(n) for status, n in counts.items()})
        metrics.OUTBOX_OLDEST.set(oldest)


def run_loop(worker: OutboxWorker, stop: threading.Event, poll_seconds: int) -> None:
    worker.start()
    while not stop.is_set():
        try:
            busy = worker.run_once()
            worker.refresh_metrics()
        except Exception as exc:  # noqa: BLE001 - worker tidak boleh mati karena satu kegagalan
            log_event("outbox_loop_error", logging.ERROR, error_class=type(exc).__name__)
            busy = False
        if not busy:
            stop.wait(poll_seconds)
