"""Entry point forecast-worker.

    python -m efn_forecast run --company SMD             # loop terjadwal (FORECAST_DAILY_CRON, zona FORECAST_TZ)
    python -m efn_forecast run --company SMD --once      # satu batch sekarang (butuh FORECAST_ENABLED=true)
    python -m efn_forecast run --company SMD --dry-run   # satu batch tanpa memanggil Odoo (hanya baca ClickHouse)
    python -m efn_forecast run --company SMD --once --kind weekly_minmax
    python -m efn_forecast seed-calendar --company SMD [--dry-run]

Loop: FORECAST_ENABLED=false -> proses tetap hidup (idle, tanpa memanggil ClickHouse/Odoo, tanpa butuh secret) agar
`restart: unless-stopped` tidak berputar. Batch gagal -> run_close failed, log, tunggu jadwal berikutnya (tidak
crash berulang). Batch min/max mingguan dijalankan setelah batch harian pada FORECAST_MINMAX_WEEKDAY (default Minggu).
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

from efn_forecast.config import ClickHouseSettings, OdooSettings, Params, company_code, forecast_enabled
from efn_forecast.models import StatsforecastRunner
from efn_forecast.odoo import OdooForecastClient, RequestsTransport
from efn_forecast.runner import RunReport, run_batch, seed_calendar
from efn_forecast.schedule import next_fire, parse_cron, zone
from efn_forecast.source import ClickHouseSource, build_client

log = logging.getLogger("efn_forecast")
IDLE_SECONDS = 3600


def _source(env: dict[str, str]) -> ClickHouseSource:
    ch = ClickHouseSettings.from_env(env)
    client = build_client(ch.host, ch.port, ch.user, ch.password, ch.database, ch.secure, ch.ca_file)
    return ClickHouseSource(client, ch.query_timeout_seconds)


def _odoo(company: str, env: dict[str, str]) -> OdooForecastClient:
    cfg = OdooSettings.from_env(company, env)
    transport = RequestsTransport(cfg.url, cfg.ca_file, cfg.client_cert_file, cfg.client_key_file)
    return OdooForecastClient(transport, cfg.api_key, cfg.database, cfg.timeout_seconds, cfg.max_retries)


def batch(kind: str, company: str, dry_run: bool, env: dict[str, str]) -> RunReport:
    params = Params.from_env(env)
    odoo = None if dry_run else _odoo(company, env)
    # season 7 = pola mingguan sisa setelah faktor hari-dalam-pekan
    runner = StatsforecastRunner(season_length=7, ets_model=params.ets_model)
    return run_batch(kind, _source(env), odoo, company, params, runner)


def serve(company: str, env: dict[str, str], stop: threading.Event) -> int:
    if not forecast_enabled(env):
        log.warning("FORECAST_ENABLED=false: forecast-worker idle (tidak memanggil ClickHouse/Odoo)")
        while not stop.wait(IDLE_SECONDS):
            pass
        return 0
    params = Params.from_env(env)
    cron = parse_cron(params.daily_cron)
    tz = zone(params.tz)
    log.info(
        "forecast-worker mulai: company=%s cron=%r tz=%s minmax_weekday=%d",
        company,
        params.daily_cron,
        params.tz,
        params.minmax_weekday,
    )
    while not stop.is_set():
        now = datetime.now(UTC)
        fire = next_fire(now, cron, tz)
        log.info("batch berikutnya %s", fire.isoformat())
        if stop.wait(max((fire - datetime.now(UTC)).total_seconds(), 0)):
            break
        for kind in _kinds_for(fire, tz, params.minmax_weekday):
            try:
                batch(kind, company, False, env)
            except Exception as exc:  # konfigurasi/secret salah -> log, coba lagi di jadwal berikutnya
                log.error("batch %s tidak dapat dimulai: %s", kind, type(exc).__name__)
    log.info("forecast-worker berhenti")
    return 0


def _kinds_for(fire: datetime, tz: Any, minmax_weekday: int) -> list[str]:
    kinds = ["daily"]
    if minmax_weekday >= 0 and fire.astimezone(tz).weekday() == minmax_weekday:
        kinds.append("weekly_minmax")
    return kinds


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    for noisy in ("urllib3", "clickhouse_connect", "numba", "statsforecast"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="efn_forecast")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="loop terjadwal atau satu batch")
    run.add_argument("--company", default=os.getenv("FORECAST_COMPANY", "SMD"))
    run.add_argument("--once", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--kind", choices=("daily", "weekly_minmax"), default="daily")
    seed = sub.add_parser("seed-calendar", help="seed libur nasional/cuti bersama ke Odoo")
    seed.add_argument("--company", default=os.getenv("FORECAST_COMPANY", "SMD"))
    seed.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    company = company_code(args.company)

    if args.cmd == "seed-calendar":
        if not args.dry_run and not forecast_enabled(env):
            log.error("FORECAST_ENABLED=false: seed-calendar ditolak (gunakan --dry-run)")
            return 2
        year = datetime.now(UTC).year
        params = Params.from_env(env)
        n = seed_calendar(None if args.dry_run else _odoo(company, env), company, (year, year + 1), params)
        log.info("seed kalender: %d entri (%s)", n, "dry-run" if args.dry_run else "terkirim")
        return 0

    if args.dry_run or args.once:
        if not args.dry_run and not forecast_enabled(env):
            log.error("FORECAST_ENABLED=false: batch ditolak (gunakan --dry-run)")
            return 2
        report = batch(args.kind, company, args.dry_run, env)
        return 0 if report.status in ("ok", "dry_run") else 1

    stop = threading.Event()

    def _stop(*_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return serve(company, env, stop)


if __name__ == "__main__":
    sys.exit(main())
