"""Entry point: `python -m efn_etl`. Loop setiap ETL_INTERVAL_SECONDS (default 900 = 15 menit, ADR-003/D-02).

Urutan per siklus: (1) job `privacy_erasure` (docs/04 §8.4) bila `ETL_ERASURE_ENABLED`, (2) job incremental dengan
hash subjek yang sedang dihapus dikecualikan, (3) snapshot stok, (4) salinan audit + rekonsiliasi harian (termasuk
verifikasi sink Loki, G14), (5) rekonsiliasi loyalty ledger <-> pos.order harian (G14, sisa SEC-61).
"""

import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from efn_etl.adapters import (
    ClickHouseAuditStore,
    ClickHouseErasureStore,
    ClickHouseRunStore,
    ClickHouseSink,
    ClickHouseWatermarkStore,
    PgSource,
    build_clickhouse_client,
)
from efn_etl.amqp import PikaPublisher, build_connection_params
from efn_etl.audit_job import AuditSettings, SinkCheck, recon_due, run_audit_copy, run_audit_recon
from efn_etl.config import Settings
from efn_etl.erasure import SQL_PENDING_ERASURES, excluded_hashes, parse_requests, run_erasure
from efn_etl.gl import GL_JOBS
from efn_etl.heartbeat import Heartbeat, JobRun, make_heartbeat
from efn_etl.jobs import AgeOut, enabled_jobs
from efn_etl.loki import HttpsLokiTransport
from efn_etl.loyalty_recon import LoyaltyReconSettings, run_loyalty_recon
from efn_etl.loyalty_recon import recon_due as loyalty_recon_due
from efn_etl.runner import run_once
from efn_etl.sink import SinkCheckResult, SinkCheckSettings, run_sink_check
from efn_etl.stock import STOCK_JOB, run_stock_snapshot, snapshot_due
from efn_etl.tls import client_ssl_context

log = logging.getLogger("efn_etl")


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def run_audit_cycle(
    source: PgSource,
    audit_store: ClickHouseAuditStore,
    cfg: AuditSettings,
    heartbeat: Heartbeat,
    sink_check: SinkCheck | None = None,
) -> None:
    """Salinan audit + rekonsiliasi berkala. Gagal = log ERROR (alert Loki), tidak menghentikan job lain."""
    now = datetime.now(UTC)
    started = time.monotonic()
    try:
        result = run_audit_copy(source, audit_store, cfg, now)
        log.info(
            "job=audit_copy new=%d late=%d chains=%d epoch=%d",
            result.new_rows,
            result.late_rows,
            len(result.heads),
            result.epoch,
        )
    except Exception:
        log.exception("job=audit_copy gagal")
        heartbeat(JobRun("audit_copy", datetime.now(UTC), "error", duration_ms=_ms(started)))
        return
    heartbeat(
        JobRun("audit_copy", datetime.now(UTC), "ok", rows=result.new_rows + result.late_rows, duration_ms=_ms(started))
    )
    started = time.monotonic()
    try:
        if not recon_due(audit_store, cfg, now):
            return
        run_audit_recon(source, audit_store, cfg, now, sink_check)
    except Exception:
        log.exception("job=audit_copy gagal (rekonsiliasi)")
        heartbeat(JobRun("audit_recon", datetime.now(UTC), "error", duration_ms=_ms(started)))
        return
    heartbeat(JobRun("audit_recon", datetime.now(UTC), "ok", duration_ms=_ms(started)))


def run_stock_cycle(
    source: PgSource,
    sink: ClickHouseSink,
    run_store: ClickHouseRunStore,
    interval: timedelta,
    batch_size: int,
    max_pages: int,
    heartbeat: Heartbeat,
) -> None:
    """Snapshot stok (G13) bila jatuh tempo. Gagal = heartbeat error; snapshot tanpa penanda tidak tampil di view."""
    started = time.monotonic()
    now = datetime.now(UTC)
    try:
        if not snapshot_due(run_store.last_stock_snapshot(), now, interval):
            return
        with source.snapshot() as tx:
            result = run_stock_snapshot(tx, sink, now, batch_size, max_pages)
    except Exception:
        log.exception("job=%s gagal", STOCK_JOB)
        heartbeat(JobRun(STOCK_JOB, datetime.now(UTC), "error", duration_ms=_ms(started)))
        return
    log.info("job=%s quants=%d orderpoints=%d", STOCK_JOB, result.quants, result.orderpoints)
    heartbeat(
        JobRun(
            STOCK_JOB,
            datetime.now(UTC),
            "ok",
            rows=result.quants + result.orderpoints,
            pages=result.pages,
            duration_ms=_ms(started),
            watermark=result.snapshot_ts,
        )
    )


def make_sink_check(
    loki: HttpsLokiTransport, source: PgSource, store: ClickHouseAuditStore, key: bytes, cfg: SinkCheckSettings
) -> SinkCheck:
    def check(now: datetime) -> SinkCheckResult:
        return run_sink_check(loki, source, store, key, now, cfg)

    return check


def run_loyalty_recon_cycle(
    platform: PgSource, odoo: PgSource, run_store: ClickHouseRunStore, cfg: LoyaltyReconSettings, heartbeat: Heartbeat
) -> None:
    """Rekonsiliasi loyalty (G14) bila jatuh tempo. Gagal = heartbeat error + log ERROR EFN_LOYALTY_RECON_ETL."""
    started = time.monotonic()
    now = datetime.now(UTC)
    try:
        if not loyalty_recon_due(run_store, cfg, now):
            return
        report = run_loyalty_recon(platform, odoo, run_store, cfg, now)
    except Exception as exc:
        # Tanpa pesan exception mentah (bisa memuat potongan SQL/nilai): hanya nama tipe.
        log.error('EFN_LOYALTY_RECON_ETL {"status":"error","error":"%s"}', type(exc).__name__)
        heartbeat(JobRun("loyalty_recon", datetime.now(UTC), "error", duration_ms=_ms(started)))
        return
    heartbeat(
        JobRun(
            "loyalty_recon",
            datetime.now(UTC),
            "ok",
            rows=report.orders_checked + report.ledger_checked,
            duration_ms=_ms(started),
        )
    )


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
    run_store = ClickHouseRunStore(client, settings.ch_database)
    heartbeat = make_heartbeat(run_store)
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
        epoch_auto=settings.audit_epoch_auto,
        epoch_anchors=settings.audit_epoch_anchors,
    )
    sink_check: SinkCheck | None = None
    if settings.audit_sink_enabled:
        loki = HttpsLokiTransport(
            settings.loki_url, settings.loki_ca_file, settings.loki_client_cert_file, settings.loki_client_key_file
        )
        sink_cfg = SinkCheckSettings(
            window=timedelta(seconds=settings.audit_sink_window_seconds),
            settle=timedelta(seconds=settings.audit_settle_seconds),
            anchor_lookback=timedelta(seconds=settings.audit_anchor_lookback_seconds),
        )
        if audit_store is None:  # dijamin Settings (sink butuh audit_copy_enabled)
            raise RuntimeError("verifikasi sink butuh salinan audit")
        sink_check = make_sink_check(loki, sources["odoo"], audit_store, settings.audit_sink_key, sink_cfg)
    elif settings.audit_copy_enabled:
        log.warning("verifikasi sink audit (SEC-51) TIDAK aktif: LOKI_URL kosong atau ETL_AUDIT_SINK_ENABLED=false")
    loyalty_cfg = LoyaltyReconSettings(
        interval=timedelta(seconds=settings.loyalty_recon_interval_seconds),
        lookback=timedelta(hours=settings.loyalty_recon_lookback_hours),
        settle=timedelta(hours=settings.loyalty_recon_settle_hours),
        aged=timedelta(hours=settings.loyalty_recon_aged_hours),
        open_window=timedelta(days=settings.loyalty_recon_open_days),
        points_codes=settings.points_product_codes,
        not_before=settings.loyalty_recon_not_before,
        points_mode=settings.loyalty_points_mode,
        points_tolerance_idr=Decimal(settings.loyalty_points_tolerance_idr),
        point_value_idr=settings.loyalty_point_value_idr,
        online_enabled=settings.loyalty_recon_online_enabled,
    )
    age_out = AgeOut(geo_days=settings.geo_retention_days, pseudonym_years=settings.pseudonym_retention_years)
    jobs = enabled_jobs(
        settings.odoo_enabled,
        settings.points_product_codes,
        age_out,
        points_payment=settings.loyalty_points_mode == "payment",
        partner_region=settings.partner_region_enabled,
        kds=settings.kds_enabled,
        kds_online=settings.kds_online_enabled,
        waste=settings.waste_enabled,
    )
    if settings.gl_enabled:
        jobs.extend(GL_JOBS)
    overlap = timedelta(seconds=settings.overlap_seconds)
    log.info(
        "ETL mulai: jobs=%s stock_snapshot=%s audit_copy=%s audit_sink=%s loyalty_recon=%s (online=%s) points_mode=%s "
        "erasure=%s interval=%ds overlap=%ds",
        [j.name for j in jobs],
        settings.stock_enabled,
        settings.audit_copy_enabled,
        settings.audit_sink_enabled,
        settings.loyalty_recon_enabled,
        settings.loyalty_recon_online_enabled,
        settings.loyalty_points_mode,
        settings.erasure_enabled,
        settings.interval_seconds,
        settings.overlap_seconds,
    )

    while not stop.is_set():
        cycle_started = time.monotonic()
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
                heartbeat(JobRun("privacy_erasure", datetime.now(UTC), "error"))
            else:
                requests = parse_requests(rows)
                excluded = excluded_hashes(requests)
                erased = run_erasure(requests, erasure_store, publisher)
                status = "partial" if any(r.status == "error" for r in erased) else "ok"
                heartbeat(JobRun("privacy_erasure", datetime.now(UTC), status, rows=len(requests)))
        results = run_once(
            cycle_jobs,
            sources,
            sink,
            store,
            overlap,
            settings.batch_size,
            settings.max_pages_per_run,
            excluded,
            heartbeat,
        )
        if settings.stock_enabled:
            run_stock_cycle(
                sources["odoo"],
                sink,
                run_store,
                timedelta(seconds=settings.stock_snapshot_interval_seconds),
                settings.batch_size,
                settings.max_pages_per_run,
                heartbeat,
            )
        if audit_store is not None:
            run_audit_cycle(sources["odoo"], audit_store, audit_cfg, heartbeat, sink_check)
        if settings.loyalty_recon_enabled:
            run_loyalty_recon_cycle(sources["platform"], sources["odoo"], run_store, loyalty_cfg, heartbeat)
        # Heartbeat siklus: bukti loop hidup (status selalu ok; kegagalan job terlihat di heartbeat job masing-masing).
        heartbeat(JobRun("cycle", datetime.now(UTC), "ok", rows=len(results), duration_ms=_ms(cycle_started)))
        if settings.run_once:
            break
        stop.wait(settings.interval_seconds)
    log.info("ETL berhenti")


if __name__ == "__main__":
    main()
