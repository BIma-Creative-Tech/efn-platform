"""Heartbeat per job ETL (SEC-52 butir 1: dead-man alert). Logika murni + emitter; I/O diinjeksi.

Setiap job (incremental, snapshot stok, audit_copy, audit_recon, privacy_erasure) dan setiap siklus (`job=cycle`)
menghasilkan dua jejak:

1. Baris log satu baris, format logfmt agar mudah di-parse LogQL (`| logfmt`):
   `EFN_ETL_HEARTBEAT job=<job> status=<ok|error|partial> rows=<n> pages=<n> duration_ms=<n> watermark=<iso|-> ts=<iso>`
   Alert dead-man (devsecops, Loki ruler): absennya `status=ok` untuk job X selama ambang tertentu.
2. Baris tabel ClickHouse `efn_dwh.etl_job_run` (riwayat 90 hari) -> kartu "kesegaran data" di Metabase
   (`v_etl_freshness`).

Kegagalan menulis tabel tidak menghentikan ETL (log tetap keluar; alert berbasis log).
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from efn_etl.watermark import to_utc

HEARTBEAT_MARKER = "EFN_ETL_HEARTBEAT"
STATUSES = frozenset({"ok", "error", "partial"})
_JOB_RE = re.compile(r"^[a-z0-9_]{1,64}$")

log = logging.getLogger("efn_etl.heartbeat")


@dataclass(frozen=True)
class JobRun:
    job: str
    run_ts: datetime
    status: str
    rows: int = 0
    pages: int = 0
    duration_ms: int = 0
    watermark: datetime | None = None

    def __post_init__(self) -> None:
        # Nama job & status masuk ke baris log yang dibaca alert: hanya karakter aman (anti log injection, SEC-51).
        if not _JOB_RE.fullmatch(self.job):
            raise ValueError("nama job heartbeat tidak valid")
        if self.status not in STATUSES:
            raise ValueError("status heartbeat tidak valid")


def _iso(value: datetime | None) -> str:
    return "-" if value is None else to_utc(value).isoformat().replace("+00:00", "Z")


def heartbeat_line(run: JobRun) -> str:
    return (
        f"{HEARTBEAT_MARKER} job={run.job} status={run.status} rows={int(run.rows)} pages={int(run.pages)} "
        f"duration_ms={int(run.duration_ms)} watermark={_iso(run.watermark)} ts={_iso(run.run_ts)}"
    )


class RunStore(Protocol):
    def record(self, run: JobRun) -> None: ...


Heartbeat = Callable[[JobRun], None]


def make_heartbeat(store: RunStore | None) -> Heartbeat:
    """Emitter: log dulu (jalur alert), lalu tabel (best effort)."""

    def emit(run: JobRun) -> None:
        level = logging.INFO if run.status == "ok" else logging.WARNING
        log.log(level, heartbeat_line(run))
        if store is None:
            return
        try:
            store.record(run)
        except Exception:
            log.exception("job=%s heartbeat tidak tersimpan ke etl_job_run", run.job)

    return emit
