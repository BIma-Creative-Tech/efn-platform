"""Entry point: `python -m efn_etl`. Loop setiap ETL_INTERVAL_SECONDS (default 900 = 15 menit, ADR-003/D-02).

Urutan per siklus: (1) job `privacy_erasure` (docs/04 §8.4) bila `ETL_ERASURE_ENABLED`, (2) job incremental dengan
hash subjek yang sedang dihapus dikecualikan.
"""

import logging
import os
import signal
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from efn_etl.adapters import (
    ClickHouseAuditStore,
    ClickHouseErasureStore,
    ClickHouseSink,
    ClickHouseWatermarkStore,
    PgSource,
    build_clickhouse_client,
)
from efn_etl.amqp import PikaPublisher, build_connection_params
from efn_etl.audit_job import AuditSettings, recon_due, run_audit_copy, run_audit_recon
from efn_etl.config import Settings
from efn_etl.erasure import SQL_PENDING_ERASURES, excluded_hashes, parse_requests, run_erasure
from efn_etl.gl import GL_JOBS
from efn_etl.jobs import enabled_jobs
from efn_etl.runner import run_once
from efn_etl.tls import client_ssl_context

log = logging.getLogger("efn_etl")


def run_audit_cycle(source: PgSource, audit_store: ClickHouseAuditStore, cfg: AuditSettings) -> None:
    """Salinan audit + rekonsiliasi berkala. Gagal = log ERROR (alert Loki), tidak menghentikan job lain."""
    now = datetime.now(UTC)
    try:
        result = run_audit_copy(source, audit_store, cfg, now)
        log.info("job=audit_copy new=%d late=%d chains=%d", result.new_rows, result.late_rows, len(result.heads))
        if recon_due(audit_store, cfg, now):
            run_audit_recon(source, audit_store, cfg, now)
    except Exception:
        log.exception("job=audit_copy gagal")


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for noisy in ("urllib3", "clickhouse_connect", "pika"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Gagal start (TlsConfigError) bila TLS tidak terkonfigurasi; tidak ada fallback ke plaintext.
    settings = Settings.from_env()
    if not settings.ch_secure or settings.platform_pg.sslmode != "verify-full":
        log.warning("MODE PLAINTEXT (EFN_ALLOW_PLAINTEXT=true): hanya untuk dev, jangan dipakai dengan data nyata")
    stop = threading.Event()

    def _stop(*_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    client = build_clickhouse_client(settings)
    sink = ClickHouseSink(client, settings.ch_database)
    store = ClickHouseWatermarkStore(client, settings.ch_database)
    sources = {
        "platform": PgSource(settings.platform_pg, settings.statement_timeout_ms),
        "odoo": PgSource(settings.odoo_pg, settings.statement_timeout_ms),
    }
    erasure_store = ClickHouseErasureStore(client, settings.ch_database, settings.erasure_mutation_timeout_seconds)
    publisher: PikaPublisher | None = None
    if settings.erasure_enabled:
        ssl_context = client_ssl_context(settings.amqp_ca_file) if settings.amqp_ca_file else None
        params = build_connection_params(settings.amqp_url, settings.amqp_password, "efn-etl", ssl_context)
        publisher = PikaPublisher(params, settings.amqp_user)
    else:
        log.warning("ETL_ERASURE_ENABLED=false: penghapusan DSAR di ClickHouse TIDAK berjalan (hanya untuk dev)")
    audit_store = ClickHouseAuditStore(client, settings.ch_database) if settings.audit_copy_enabled else None
    audit_cfg = AuditSettings(
        settle=timedelta(seconds=settings.audit_settle_seconds),
        late_window=timedelta(seconds=settings.audit_late_window_seconds),
        batch_size=settings.audit_batch_size,
        max_pages=settings.max_pages_per_run,
        recon_interval=timedelta(seconds=settings.audit_recon_interval_seconds),
        recon_content=settings.audit_recon_content,
    )
    jobs = enabled_jobs(settings.odoo_enabled)
    if settings.gl_enabled:
        jobs.extend(GL_JOBS)
    overlap = timedelta(seconds=settings.overlap_seconds)
    log.info(
        "ETL mulai: jobs=%s audit_copy=%s erasure=%s interval=%ds overlap=%ds",
        [j.name for j in jobs],
        settings.audit_copy_enabled,
        settings.erasure_enabled,
        settings.interval_seconds,
        settings.overlap_seconds,
    )

    while not stop.is_set():
        excluded: frozenset[str] = frozenset()
        cycle_jobs = jobs
        if publisher is not None:
            try:
                rows = sources["platform"].query(SQL_PENDING_ERASURES, {"limit": settings.erasure_batch_size})
            except Exception:
                # Fail closed: daftar subjek yang sedang dihapus tidak diketahui -> job yang memuat hash member
                # dilewati siklus ini agar tidak memuat ulang data subjek. Diulang siklus berikutnya.
                log.exception("job=privacy_erasure gagal membaca dsar_request; job ber-hash member dilewati")
                cycle_jobs = [j for j in jobs if not j.identity_field]
            else:
                requests = parse_requests(rows)
                excluded = excluded_hashes(requests)
                run_erasure(requests, erasure_store, publisher)
        run_once(cycle_jobs, sources, sink, store, overlap, settings.batch_size, settings.max_pages_per_run, excluded)
        if audit_store is not None:
            run_audit_cycle(sources["odoo"], audit_store, audit_cfg)
        if settings.run_once:
            break
        stop.wait(settings.interval_seconds)
    log.info("ETL berhenti")


if __name__ == "__main__":
    main()
