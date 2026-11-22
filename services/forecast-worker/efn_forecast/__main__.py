"""Entry point forecast-worker.

    python -m efn_forecast run --company SMD             # loop terjadwal (FORECAST_DAILY_CRON, zona FORECAST_TZ)
    python -m efn_forecast run --company SMD --once      # satu batch sekarang (butuh FORECAST_ENABLED=true)
    python -m efn_forecast run --company SMD --dry-run   # satu batch tanpa memanggil Odoo (hanya baca ClickHouse)
    python -m efn_forecast run --company SMD --once --kind weekly_minmax
    python -m efn_forecast seed-calendar --company SMD [--dry-run]
    python -m efn_forecast run --company SMD --as-of 2026-09-28 --out /out/asof.json   # G51 A6, tanpa RPC
    python -m efn_forecast run --company SMD,PB          # G46d: beberapa PT bergiliran (as-of: satu PT saja)

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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from efn_forecast import asof
from efn_forecast.config import ClickHouseSettings, OdooSettings, Params, company_codes, forecast_enabled
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


def batch_as_of(company: str, as_of: date, out: Path, env: dict[str, str]) -> RunReport:
    """G51 A6: hitung batch daily as-of (tanpa RPC Odoo) lalu tulis payload ke `out` (efn_forecast/asof.py)."""
    params = asof.as_of_params(Params.from_env(env))
    rec = asof.RecordingOdoo()
    runner = StatsforecastRunner(season_length=7)
    report = run_batch(
        "daily",
        _source(env),
        rec,
        company,
        params,
        runner,
        now=asof.as_of_now(as_of),
        run_id=asof.as_of_run_id(company, as_of),
    )
    if report.status == "ok":
        asof.write_document(out, asof.export_document(company, as_of, rec))
        log.info("as-of %s: %d baris, %d chunk -> %s", as_of.isoformat(), report.lines, report.chunks, out.name)
    return report


def run_all(kind: str, companies: tuple[str, ...], dry_run: bool, env: dict[str, str]) -> bool:
    """Batch `kind` untuk tiap PT berurutan; PT yang gagal tidak menghentikan PT berikutnya. True = semua sukses."""
    ok = True
    for company in companies:
        try:
            report = batch(kind, company, dry_run, env)
            ok = ok and report.status in ("ok", "dry_run")
        except Exception as exc:  # konfigurasi/secret salah -> log, PT berikutnya tetap jalan
            # format awal dipertahankan: dicocokkan alert Loki EfnForecastRunFailed (loki-rules/efn-forecast.yml)
            log.error("batch %s tidak dapat dimulai: %s company=%s", kind, type(exc).__name__, company)
            ok = False
    return ok


def serve(companies: tuple[str, ...], env: dict[str, str], stop: threading.Event) -> int:
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
        ",".join(companies),
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
            run_all(kind, companies, False, env)  # gagal -> sudah di-log; coba lagi di jadwal berikutnya
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
    # G51 A6: histori akurasi demo. Hanya hitung + tulis file (tanpa RPC); dimuat ke Odoo oleh
    # scripts/dev/load_forecast_asof.py. Tanpa --as-of perilaku tidak berubah.
    run.add_argument("--as-of", dest="as_of", help="Senin YYYY-MM-DD: batch daily seolah Minggu sebelumnya 23:00 WIB")
    run.add_argument("--out", help="file JSON keluaran mode --as-of")
    seed = sub.add_parser("seed-calendar", help="seed libur nasional/cuti bersama ke Odoo")
    seed.add_argument("--company", default=os.getenv("FORECAST_COMPANY", "SMD"))
    seed.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    companies = company_codes(args.company)

    if args.cmd == "seed-calendar":
        if not args.dry_run and not forecast_enabled(env):
            log.error("FORECAST_ENABLED=false: seed-calendar ditolak (gunakan --dry-run)")
            return 2
        year = datetime.now(UTC).year
        params = Params.from_env(env)
        for company in companies:
            n = seed_calendar(None if args.dry_run else _odoo(company, env), company, (year, year + 1), params)
            log.info("seed kalender %s: %d entri (%s)", company, n, "dry-run" if args.dry_run else "terkirim")
        return 0

    if args.as_of is not None or args.out is not None:
        if not (args.as_of and args.out) or args.once or args.dry_run or args.kind != "daily" or len(companies) != 1:
            log.error("--as-of wajib bersama --out, satu PT, hanya kind daily, tanpa --once/--dry-run")
            return 2
        company = companies[0]
        try:
            as_of = asof.parse_as_of(args.as_of, datetime.now(UTC).astimezone(asof.WIB).date())
        except ValueError as exc:
            log.error("%s", exc)
            return 2
        report = batch_as_of(company, as_of, Path(args.out), env)
        return 0 if report.status == "ok" else 1

    if args.dry_run or args.once:
        if not args.dry_run and not forecast_enabled(env):
            log.error("FORECAST_ENABLED=false: batch ditolak (gunakan --dry-run)")
            return 2
        return 0 if run_all(args.kind, companies, args.dry_run, env) else 1

    stop = threading.Event()

    def _stop(*_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return serve(companies, env, stop)


if __name__ == "__main__":
    sys.exit(main())
