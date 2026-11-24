"""CLI: `python -m efn_prodplan serve` (docs/04 §30.1). Satu proses, replika 1: API + worker job + worker outbox +
purge harian sebagai thread.

- `preflight` (base URL + kunci tulis production L-250) dijalankan SELALU, juga saat nonaktif -> exit 2 bila gagal.
- `PRODPLAN_ENABLED` bukan `true` -> API tetap hidup (`/healthz` 200, `/v1/*` 503 `disabled`), worker tidak jalan,
  tidak ada panggilan ESB/PG; proses TIDAK exit (pola esb-extract, `restart: unless-stopped`).
- Konfigurasi tidak valid -> log `prodplan_config_error reason=<kode>` lalu exit 2.
"""

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from efn_prodplan import metrics
from efn_prodplan.api import App, make_server
from efn_prodplan.config import ConfigError, Settings, preflight, prodplan_enabled
from efn_prodplan.logs import log_event, setup_logging
from efn_prodplan.workflow import wib_today

PURGE_HOUR_WIB = 2
PURGE_CHECK_SECONDS = 900


def _stop_event() -> threading.Event:
    stop = threading.Event()

    def _stop(*_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return stop


def _now() -> datetime:
    return datetime.now(UTC)


def _purge_loop(db: Any, retention_days: int, stop: threading.Event) -> None:
    from efn_prodplan.store import purge

    last: date | None = None
    while not stop.is_set():
        now = _now()
        today = wib_today(now)
        hour = (now.hour + 7) % 24
        if last != today and hour >= PURGE_HOUR_WIB:
            try:
                counts = purge(db, retention_days)
                log_event("purge_done", count=sum(counts.values()))
                last = today
            except Exception as exc:  # noqa: BLE001
                log_event("purge_failed", logging.ERROR, error_class=type(exc).__name__)
        stop.wait(PURGE_CHECK_SECONDS)


def _serve_http(app: App, stop: threading.Event) -> int:
    server = make_server(app, "127.0.0.1", 8080)
    thread = threading.Thread(target=server.serve_forever, name="api", daemon=True)
    thread.start()
    stop.wait()
    server.shutdown()
    server.server_close()
    log_event("prodplan_stopped")
    return 0


def cmd_serve() -> int:
    try:
        _, esb_env = preflight()
    except ConfigError as exc:
        log_event("prodplan_config_error", logging.ERROR, reason=exc.reason)
        return 2
    stop = _stop_event()
    if not prodplan_enabled():
        log_event("prodplan_disabled", logging.WARNING, esb_env=esb_env)
        metrics.WRITE_ENABLED.set(0)
        return _serve_http(App(enabled=False, assert_key=None, service=None, ready_check=lambda: False), stop)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        log_event("prodplan_config_error", logging.ERROR, reason=exc.reason)
        return 2

    from efn_prodplan import jobs, outbox
    from efn_prodplan.esb import EsbClient, HttpsTransport, TokenBucket
    from efn_prodplan.service import Service
    from efn_prodplan.store import Database, PgJobStore, PgOutboxStore

    metrics.WRITE_ENABLED.set(1 if settings.write_enabled else 0)
    metrics.INFO.set(1, settings.esb_env, settings.mfg_mode)
    db = Database(settings.pg)
    transport = HttpsTransport(settings.max_response_bytes)

    def client(cfg: Any, bucket: TokenBucket, *, allow_write: bool, max_calls: int | None) -> EsbClient:
        return EsbClient(
            settings.base_url,
            cfg.user,
            cfg.password,
            transport,
            bucket,
            mfg_mode=settings.mfg_mode,
            allow_write=allow_write,
            timeout=float(settings.timeout_seconds),
            max_calls=max_calls,
        )

    read_clients: dict[str, EsbClient] = {}
    job_clients: dict[str, EsbClient] = {}
    outbox_clients: dict[str, EsbClient] = {}
    for cfg in settings.brands:
        bucket = TokenBucket(settings.rate_per_sec)  # satu bucket per brand, dipakai bersama ketiga klien
        read_clients[cfg.key] = client(cfg, bucket, allow_write=False, max_calls=None)
        job_clients[cfg.key] = client(cfg, bucket, allow_write=False, max_calls=settings.job_max_calls)
        outbox_clients[cfg.key] = client(cfg, bucket, allow_write=settings.write_enabled, max_calls=None)

    catalog = jobs.BomCatalog()  # cache header BOM bersama API + job
    service = Service(settings, db, read_clients, _now, catalog)
    runner = jobs.JobRunner(db, PgJobStore(db), settings, job_clients, _now, catalog)
    worker = outbox.OutboxWorker(
        PgOutboxStore(db),
        outbox_clients,
        esb_env=settings.esb_env,
        write_enabled=settings.write_enabled,
        reconcile_grace_seconds=settings.reconcile_grace_seconds,
        clock=_now,
    )
    threads = [
        threading.Thread(target=jobs.run_loop, args=(runner, stop), name="jobs", daemon=True),
        threading.Thread(
            target=outbox.run_loop, args=(worker, stop, settings.outbox_poll_seconds), name="outbox", daemon=True
        ),
        threading.Thread(target=_purge_loop, args=(db, settings.retention_days, stop), name="purge", daemon=True),
    ]
    for thread in threads:
        thread.start()
    log_event(
        "prodplan_started",
        esb_env=settings.esb_env,
        mfg_mode=settings.mfg_mode,
        write_enabled=settings.write_enabled,
        count=len(settings.brands),
    )
    app = App(enabled=True, assert_key=settings.assert_key, service=service, ready_check=db.ready)
    return _serve_http(app, stop)


def main(argv: Sequence[str] | None = None) -> int:
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(prog="python -m efn_prodplan", description="Production planning CK di atas ESB")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="API + worker job + worker outbox (satu proses)")
    sub.add_parser("check-config", help="validasi konfigurasi (tanpa ESB/PG); exit 2 bila tidak valid")
    args = parser.parse_args(argv)
    if args.cmd == "check-config":
        try:
            preflight()
            if prodplan_enabled():
                Settings.from_env()
        except ConfigError as exc:
            print(f"konfigurasi tidak valid: {exc.reason}", file=sys.stderr)
            return 2
        print("konfigurasi valid")
        return 0
    return cmd_serve()


if __name__ == "__main__":
    sys.exit(main())
