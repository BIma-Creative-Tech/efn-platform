"""Eksekusi job incremental per halaman dengan watermark. Dependensi diinjeksi agar bisa diuji tanpa DB."""

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from efn_etl.jobs import Job
from efn_etl.watermark import Cursor, cursor_after, initial_cursor, next_watermark

log = logging.getLogger(__name__)


class Source(Protocol):
    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]: ...


class Sink(Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


class WatermarkStore(Protocol):
    def get(self, job: str) -> datetime | None: ...

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None: ...


@dataclass(frozen=True)
class JobResult:
    job: str
    rows: int
    pages: int
    watermark: datetime | None
    skipped: str | None = None


def run_job(
    job: Job,
    source: Source,
    sink: Sink,
    store: WatermarkStore,
    overlap: timedelta,
    batch_size: int,
    max_pages: int,
    excluded_hashes: frozenset[str] = frozenset(),
) -> JobResult:
    watermark = store.get(job.name)
    cursor = initial_cursor(watermark, overlap)
    total = 0
    pages = 0
    while pages < max_pages:
        rows = source.fetch(job.sql, cursor, batch_size)
        if not rows:
            break
        # Erasure (§8.4): baris milik subjek yang sedang dihapus tidak dimuat ulang; watermark/cursor tetap maju
        # memakai SEMUA baris halaman.
        loadable = rows
        if excluded_hashes and job.identity_field:
            loadable = [r for r in rows if str(r.get(job.identity_field) or "").strip() not in excluded_hashes]
        # Transform melempar ValueError bila identitas bukan hash -> seluruh halaman batal (fail closed).
        sink.insert(job.target_table, job.columns, [job.transform(row) for row in loadable])
        # Watermark disimpan SETELAH insert sukses: crash di tengah = halaman dibaca ulang (idempotent).
        new_watermark = next_watermark(watermark, rows)
        if new_watermark is not None:
            store.set(job.name, new_watermark, len(rows))
            watermark = new_watermark
        cursor = cursor_after(rows)
        total += len(rows)
        pages += 1
        if len(rows) < batch_size:
            break
    else:
        log.warning("job=%s mencapai batas %d halaman; sisa dilanjutkan run berikutnya", job.name, max_pages)
    return JobResult(job=job.name, rows=total, pages=pages, watermark=watermark)


def run_once(
    jobs: Sequence[Job],
    sources: Mapping[str, Source],
    sink: Sink,
    store: WatermarkStore,
    overlap: timedelta,
    batch_size: int,
    max_pages: int,
    excluded_hashes: frozenset[str] = frozenset(),
) -> list[JobResult]:
    """Satu putaran semua job. Kegagalan satu job tidak menghentikan job lain."""
    results: list[JobResult] = []
    for job in jobs:
        started = time.monotonic()
        try:
            result = run_job(job, sources[job.source], sink, store, overlap, batch_size, max_pages, excluded_hashes)
        except Exception:
            log.exception("job=%s gagal", job.name)
            results.append(JobResult(job=job.name, rows=0, pages=0, watermark=None, skipped="error"))
            continue
        log.info(
            "job=%s rows=%d pages=%d watermark=%s ms=%d",
            result.job,
            result.rows,
            result.pages,
            result.watermark.isoformat() if result.watermark else None,
            int((time.monotonic() - started) * 1000),
        )
        results.append(result)
    return results
