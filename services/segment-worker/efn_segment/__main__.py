"""Entry point segment-worker.

    python -m efn_segment run                       # loop terjadwal (SEGMENT_DAILY_CRON, zona SEGMENT_TZ), semua PT
    python -m efn_segment run --once                # satu run sekarang untuk semua PT (butuh SEGMENT_ENABLED=true)
    python -m efn_segment run --once --company SMD  # satu PT
    python -m efn_segment run --dry-run             # hitung + validasi, tanpa BFF/Odoo (hanya baca ClickHouse)

Loop: SEGMENT_ENABLED=false -> proses tetap hidup (idle, tanpa memanggil ClickHouse/BFF/Odoo, tanpa butuh secret) agar
`restart: unless-stopped` tidak berputar. Run gagal -> log `EFN_SEGMENT_RUN` status failed, tunggu jadwal berikutnya.
Satu PT gagal (mis. API key belum dibuat) tidak menghentikan PT lain.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from datetime import UTC, datetime
from typing import Any

from efn_segment.bff import BffSegmentClient
from efn_segment.config import (
    ClickHouseSettings,
    Endpoint,
    OdooSettings,
    Params,
    bff_endpoint,
    company_code,
    segment_enabled,
)
from efn_segment.odoo import OdooCampaignClient
from efn_segment.runner import RunReport, run_company
from efn_segment.schedule import next_fire, parse_cron, zone
from efn_segment.source import ClickHouseSource, build_client
from efn_segment.transport import RequestsTransport, RetryPolicy

log = logging.getLogger("efn_segment")
IDLE_SECONDS = 3600


def _source(env: dict[str, str]) -> ClickHouseSource:
    ch = ClickHouseSettings.from_env(env)
    client = build_client(ch.host, ch.port, ch.user, ch.password, ch.database, ch.secure, ch.ca_file)
    return ClickHouseSource(client, ch.query_timeout_seconds)


def _transport(ep: Endpoint) -> RequestsTransport:
    return RequestsTransport(ep.url, ep.ca_file, ep.client_cert_file, ep.client_key_file)


def _policy(ep: Endpoint) -> RetryPolicy:
    return RetryPolicy(timeout_seconds=ep.timeout_seconds, max_retries=ep.max_retries)


def _bff(env: dict[str, str]) -> BffSegmentClient:
    ep = bff_endpoint(env)
    return BffSegmentClient(_transport(ep), _policy(ep))


def _odoo(company: str, env: dict[str, str]) -> OdooCampaignClient:
    cfg = OdooSettings.from_env(company, env)
    return OdooCampaignClient(_transport(cfg.endpoint), cfg.api_key, cfg.database, _policy(cfg.endpoint))


def run_all(companies: tuple[str, ...], dry_run: bool, env: dict[str, str]) -> list[RunReport]:
    params = Params.from_env(env)
    source = _source(env)
    bff = None if dry_run else _bff(env)
    reports: list[RunReport] = []
    for company in companies:
        try:
            odoo = None if dry_run else _odoo(company, env)
        except Exception as exc:  # secret PT belum ada -> PT ini dilewati, PT lain tetap jalan
            log.error("segment company=%s tidak dapat dimulai: %s", company, type(exc).__name__)
            continue
        reports.append(run_company(company, source, bff, odoo, params))
    return reports


def serve(env: dict[str, str], stop: threading.Event, only: str | None) -> int:
    if not segment_enabled(env):
        log.warning("SEGMENT_ENABLED=false: segment-worker idle (tidak memanggil ClickHouse/BFF/Odoo)")
        while not stop.wait(IDLE_SECONDS):
            pass
        return 0
    params = Params.from_env(env)
    companies = (only,) if only else params.companies
    cron = parse_cron(params.daily_cron)
    tz = zone(params.tz)
    log.info("segment-worker mulai: companies=%s cron=%r tz=%s", list(companies), params.daily_cron, params.tz)
    while not stop.is_set():
        fire = next_fire(datetime.now(UTC), cron, tz)
        log.info("run berikutnya %s", fire.isoformat())
        if stop.wait(max((fire - datetime.now(UTC)).total_seconds(), 0)):
            break
        try:
            run_all(companies, False, env)
        except Exception as exc:  # konfigurasi/secret salah -> log, coba lagi di jadwal berikutnya
            log.error("run segmen tidak dapat dimulai: %s", type(exc).__name__)
    log.info("segment-worker berhenti")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    for noisy in ("urllib3", "clickhouse_connect"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="efn_segment")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="loop terjadwal atau satu run")
    run.add_argument("--company", default=None)
    run.add_argument("--once", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    only = company_code(args.company) if args.company else None

    if args.dry_run or args.once:
        if not args.dry_run and not segment_enabled(env):
            log.error("SEGMENT_ENABLED=false: run ditolak (gunakan --dry-run)")
            return 2
        companies = (only,) if only else Params.from_env(env).companies
        reports = run_all(companies, args.dry_run, env)
        ok = len(reports) == len(companies) and all(r.status in ("ok", "dry_run") for r in reports)
        return 0 if ok else 1

    stop = threading.Event()

    def _stop(*_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return serve(env, stop, only)


if __name__ == "__main__":
    sys.exit(main())
