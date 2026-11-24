"""CLI: `python -m efn_esb {run,backfill,probe,health}` (docs/04 §25.4).

- `run`: loop incremental semua brand di `ESB_BRANDS` (satu per satu) tiap `ESB_INTERVAL_SECONDS`.
  `ESB_ENABLED=false` -> proses TIDUR (tidak exit, `restart: unless-stopped`), tanpa login/panggilan/tulis apa pun.
- `backfill --brand B --from D --to D [--entity E ...] [--force] [--ignore-window]`: satu brand per proses (L-111).
  SEC-185: `--ignore-window` di production hanya dengan `ESB_ALLOW_IGNORE_WINDOW=true` dan rentang 1 hari (exit 2).
- SEC-186: di `run`, brand yang `auth_failed` 2 siklus berturut-turut ditangguhkan (`auth_suspended`) sampai restart.
- `snapshot --brand B --entity E [--entity E ...] [--date D] [--allow-unverified]` (G43, docs/04 §27.11): entitas
  migrasi (bom_detail, menu_price, customer, location; P2 stock_opname/ap_open/ar_open/coa hanya dengan
  `--allow-unverified`). Bukan bagian loop `run`. G65 (docs/04 §27.13.1): `--entity stock_opening --date <cutover>`
  (satu slice per cabang; `--date` WAJIB, tanpa `--allow-unverified`). `purchase_line` = entitas harian (`backfill`).
- `probe --brand B`: login + /branch/list/user, cetak JUMLAH cabang saja.
- `health`: exit 0 bila file heartbeat masih segar (opsional; compose memakai cek PID).
CLI selain `run` dengan `ESB_ENABLED=false` -> exit 0 dengan pesan "ESB dinonaktifkan".
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

from efn_esb.config import DAILY_ENTITIES, ENTITIES, BrandConfig, ClickHouseSettings, EsbSettings, esb_enabled
from efn_esb.extract import BRANCH_LIMIT, BrandRuntime
from efn_esb.extract_g65 import G65_SNAPSHOT_ENTITIES
from efn_esb.extract_mig import P2_ENTITIES, SNAPSHOT_ENTITIES
from efn_esb.heartbeat import is_fresh, touch
from efn_esb.http import EsbClient, TokenBucket, UrllibTransport
from efn_esb.paging import fetch_all
from efn_esb.planning import validate_backfill_range, validate_ignore_window, wib_today

log = logging.getLogger("efn_esb")
IDLE_TICK_SECONDS = 60


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m efn_esb", description="Ekstraksi ESB Core (GET saja)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="loop incremental")
    backfill = sub.add_parser("backfill", help="backfill resumable satu brand")
    backfill.add_argument("--brand", required=True)
    backfill.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    backfill.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    backfill.add_argument("--entity", action="append", choices=[e for e in ENTITIES if e in DAILY_ENTITIES])
    backfill.add_argument("--force", action="store_true", help="tarik ulang slice yang sudah done")
    backfill.add_argument(
        "--ignore-window",
        action="store_true",
        help="abaikan ESB_BACKFILL_WINDOW (dev/uji; production: ESB_ALLOW_IGNORE_WINDOW=true + maks 1 hari)",
    )
    snapshot = sub.add_parser("snapshot", help="G43: tarik entitas migrasi satu brand (bukan bagian loop run)")
    snapshot.add_argument("--brand", required=True)
    snapshot.add_argument(
        "--entity", action="append", required=True, choices=[*SNAPSHOT_ENTITIES, *G65_SNAPSHOT_ENTITIES]
    )
    snapshot.add_argument(
        "--date",
        dest="day",
        type=date.fromisoformat,
        help="hari snapshot (P2: cutover - 1; stock_opening: tanggal cutover, wajib); default hari ini WIB",
    )
    snapshot.add_argument(
        "--allow-unverified",
        action="store_true",
        help=f"izinkan entitas P2 yang belum diverifikasi ({', '.join(P2_ENTITIES)})",
    )
    snapshot.add_argument("--opname-window", type=int, default=3, help="stock_opname: hari ke belakang (0..31)")
    probe = sub.add_parser("probe", help="login + hitung cabang")
    probe.add_argument("--brand", required=True)
    health = sub.add_parser("health", help="cek file heartbeat")
    health.add_argument("--max-age", type=int, default=int(os.getenv("ESB_HEALTH_MAX_AGE_SECONDS", "7800")))
    return parser.parse_args(argv)


def _client(settings: EsbSettings, cfg: BrandConfig, max_calls: int) -> EsbClient:
    return EsbClient(
        settings.base_url,
        cfg.user,
        cfg.password,
        UrllibTransport(max_response_bytes=settings.max_response_bytes),
        TokenBucket(settings.rate_per_sec),
        max_retries=settings.max_retries,
        timeout=settings.timeout_seconds,
        max_calls=max_calls,
    )


def _stop_event() -> threading.Event:
    stop = threading.Event()

    def _stop(*_args: Any) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return stop


def _idle(stop: threading.Event, heartbeat_file: str) -> int:
    log.warning("ESB_ENABLED=false: esb-extract idle (tidak login, tidak memanggil ESB, tidak menulis ClickHouse)")
    while not stop.is_set():
        touch(heartbeat_file)
        stop.wait(IDLE_TICK_SECONDS)
    return 0


def _store(env: Any) -> Any:
    from efn_esb.store import ClickHouseStore, build_clickhouse_client

    ch = ClickHouseSettings.from_env(env)
    return ClickHouseStore(build_clickhouse_client(ch), ch.database, ch.raw_database)


def _cmd_run(settings: EsbSettings, stop: threading.Event) -> int:
    from efn_esb.runner import AuthBreaker, guarded_cycle

    store = _store(os.environ)
    breaker = AuthBreaker()  # SEC-186: di memori; restart proses = cabut penangguhan
    runtimes = {
        cfg.key: (
            BrandRuntime(cfg, max_pages=settings.max_pages, include_inactive=False),
            _client(settings, cfg, settings.max_calls_per_run),
        )
        for cfg in settings.brands
    }
    log.info(
        "esb-extract run: brands=%s entities=%s interval=%ds",
        list(runtimes),
        settings.entities,
        settings.interval_seconds,
    )

    def beat(_result: Any) -> None:
        touch(settings.heartbeat_file)

    while not stop.is_set():
        for rt, client in runtimes.values():
            if stop.is_set():
                break
            guarded_cycle(settings, rt, client, store, breaker, datetime.now(UTC), stop, on_written=beat)
        touch(settings.heartbeat_file)
        if settings.run_once:
            break
        stop.wait(settings.interval_seconds)
    log.info("esb-extract berhenti")
    return 0


def _cmd_backfill(settings: EsbSettings, args: argparse.Namespace, stop: threading.Event) -> int:
    from efn_esb.runner import run_backfill

    cfg = settings.brand(args.brand.strip().lower())
    try:
        validate_backfill_range(args.start, args.end, wib_today(datetime.now(UTC)))
        if args.ignore_window:  # SEC-185: dicek sebelum koneksi ClickHouse/ESB dibuka
            validate_ignore_window(args.start, args.end, settings.production, settings.allow_ignore_window)
    except ValueError as exc:
        print(f"backfill ditolak: {exc}", file=sys.stderr)
        return 2
    entities = args.entity or [e for e in settings.entities if e in DAILY_ENTITIES]
    store = _store(os.environ)
    rt = BrandRuntime(cfg, max_pages=settings.max_pages, include_inactive=settings.backfill_include_inactive)
    run = run_backfill(
        settings,
        rt,
        _client(settings, cfg, settings.backfill_max_calls),
        store,
        args.start,
        args.end,
        entities,
        stop,
        force=args.force,
        ignore_window=args.ignore_window,
        on_written=lambda _r: touch(settings.heartbeat_file),
    )
    print(f"backfill brand={cfg.key} status={run.status} slices={run.slices} calls={run.calls} errors={run.errors}")
    return 0 if run.status in ("ok", "window_closed", "limit") else 1


def validate_snapshot_args(
    entities: Sequence[str], allow_unverified: bool, opname_window: int, day: date | None = None
) -> None:
    """G43: entitas P2 default MATI (belum diverifikasi) -> wajib `--allow-unverified`.
    G65 (L-251): `stock_opening` wajib `--date` eksplisit (tanggal cutover; tidak ada default hari ini)."""
    if any(e in G65_SNAPSHOT_ENTITIES for e in entities) and day is None:
        raise ValueError("stock_opening wajib --date eksplisit (= tanggal cutover, setelah freeze ESB)")
    blocked = [e for e in entities if e in P2_ENTITIES]
    if blocked and not allow_unverified:
        raise ValueError(
            f"entitas {', '.join(blocked)} belum diverifikasi terhadap ESB nyata (default mati); "
            "tambahkan --allow-unverified setelah persetujuan lead"
        )
    if not 0 <= opname_window <= 31:
        raise ValueError("--opname-window di luar rentang 0..31")


def _cmd_snapshot(settings: EsbSettings, args: argparse.Namespace, stop: threading.Event) -> int:
    from efn_esb.runner import run_snapshot

    cfg = settings.brand(args.brand.strip().lower())
    entities = list(dict.fromkeys(args.entity))
    today = wib_today(datetime.now(UTC))
    day = args.day or today
    try:
        validate_snapshot_args(entities, args.allow_unverified, args.opname_window, args.day)
        if day > today:
            raise ValueError("--date tidak boleh di masa depan")
    except ValueError as exc:
        print(f"snapshot ditolak: {exc}", file=sys.stderr)
        return 2
    store = _store(os.environ)
    rt = BrandRuntime(
        cfg,
        max_pages=settings.max_pages,
        include_inactive=settings.backfill_include_inactive,
        opname_window_days=args.opname_window,
    )
    run = run_snapshot(
        settings,
        rt,
        _client(settings, cfg, settings.backfill_max_calls),
        store,
        entities,
        day,
        stop,
        on_written=lambda _r: touch(settings.heartbeat_file),
    )
    print(f"snapshot brand={cfg.key} status={run.status} slices={run.slices} calls={run.calls} errors={run.errors}")
    return 0 if run.status == "ok" else 1


def _cmd_probe(settings: EsbSettings, args: argparse.Namespace) -> int:
    cfg = settings.brand(args.brand.strip().lower())
    client = _client(settings, cfg, 200)
    rows = fetch_all(client.get, "/branch/list/user", {}, limit=BRANCH_LIMIT, limit_params=("limit", "pageSize"))
    print(f"probe brand={cfg.key} cabang={len(rows)} calls={client.calls}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("clickhouse_connect").setLevel(logging.WARNING)
    args = _parse_args(argv)
    if args.cmd == "health":
        return 0 if is_fresh(os.getenv("ESB_HEARTBEAT_FILE", "/tmp/efn-esb.heartbeat"), args.max_age) else 1  # noqa: S108
    stop = _stop_event()
    if not esb_enabled():
        if args.cmd == "run":
            return _idle(stop, os.getenv("ESB_HEARTBEAT_FILE", "/tmp/efn-esb.heartbeat"))  # noqa: S108
        print("ESB dinonaktifkan (ESB_ENABLED=false); tidak ada panggilan")
        return 0
    settings = EsbSettings.from_env()
    if args.cmd == "run":
        return _cmd_run(settings, stop)
    if args.cmd == "backfill":
        return _cmd_backfill(settings, args, stop)
    if args.cmd == "snapshot":
        return _cmd_snapshot(settings, args, stop)
    return _cmd_probe(settings, args)


if __name__ == "__main__":
    sys.exit(main())
