"""CLI: `python -m efn_esb {run,backfill,snapshot,probe,health,refresh-aggregates,refresh-margin}` (docs/04 §25.4).

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
- `refresh-aggregates`: bangun ulang tabel agregat Insight `efn_dwh.agg_esb_*` (efn_esb/aggregates.py) dari view
  sumber. Hanya ClickHouse (tanpa ESB), jadi tetap jalan dengan `ESB_ENABLED=false` (mis. setelah salin data dari DR).
  Exit 0 bila semua agregat `ok`, 1 bila ada yang `error`/`busy`. Juga otomatis di akhir siklus `run`/`backfill` yang
  menulis slice entitas pengisi (gagal di sana hanya dicatat, tidak menggagalkan ekstraksi). `--allow-shrink` (manual
  saja): izinkan bulan yang hasilnya kosong/turun > 50% dan hapus bulan yang hilang dari sumber (SEC-AG-03).
- `refresh-margin [--brand B]` (G67, docs/04 §31.2): hitung ulang mart margin bahan (efn_esb/margin.py) dari data
  ClickHouse yang ada; tanpa ESB/snapshot (jalan dengan `ESB_ENABLED=false`). Tanpa `--brand` = semua
  `ESB_MARGIN_BRANDS` (kosong = fitur mati, exit 0). Exit 0 bila semua `ok`, 1 bila ada `error`/`busy`, 2 bila
  konfigurasi tidak sah. Jadwal mingguan (snapshot `bom_detail` lalu hitung) ada di loop `run`.
CLI selain `run`/`refresh-aggregates`/`refresh-margin` dengan `ESB_ENABLED=false` -> exit 0 dengan pesan
"ESB dinonaktifkan".
G69 ESB OMS (docs/04 §33, efn_esb/oms_runner.py): bila `ESB_OMS_ENABLED=true`, `run` menjalankan loop OMS di thread
terpisah (independen dari loop Core). `ESB_OMS_ENABLED=true` dengan `ESB_ENABLED=false` (atau brand OMS di luar
`ESB_BRANDS`) -> tidak start (exit 2, fail closed).
- `oms-probe --brand B`: 4 panggilan OMS, cetak JUMLAH cabang/kanal/tipe pembayaran/baris ringkasan H-1 saja.
- `oms-backfill --brand B --from D --to D [--force]`: hanya di `ESB_BACKFILL_WINDOW`; budget
  `ESB_OMS_BACKFILL_MAX_CALLS`.
`oms-*` dengan `ESB_OMS_ENABLED=false` -> exit 0 "ESB OMS dinonaktifkan".
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

from efn_esb.config import (
    DAILY_ENTITIES,
    ENTITIES,
    KNOWN_BRANDS,
    BrandConfig,
    ClickHouseSettings,
    EsbSettings,
    MarginSettings,
    OmsSettings,
    esb_enabled,
    oms_enabled,
    validate_margin,
)
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
    refresh = sub.add_parser(
        "refresh-aggregates", help="bangun ulang tabel agregat Insight efn_dwh.agg_esb_* (tanpa ESB)"
    )
    refresh.add_argument(
        "--allow-shrink",
        action="store_true",
        help="izinkan bulan kosong/turun > 50%% dan hapus bulan yang tidak ada lagi di sumber (reset disengaja)",
    )
    margin = sub.add_parser("refresh-margin", help="G67: hitung ulang mart margin bahan (tanpa ESB)")
    margin.add_argument("--brand", choices=KNOWN_BRANDS, help="satu brand (default: semua ESB_MARGIN_BRANDS)")
    oms_probe = sub.add_parser("oms-probe", help="G69: cek ESB OMS (jumlah saja)")
    oms_probe.add_argument("--brand", required=True)
    oms_backfill = sub.add_parser("oms-backfill", help="G69: backfill ESB OMS resumable satu brand")
    oms_backfill.add_argument("--brand", required=True)
    oms_backfill.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    oms_backfill.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    oms_backfill.add_argument("--force", action="store_true", help="tarik ulang slice yang sudah done")
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


def _oms_client(settings: EsbSettings, oms: OmsSettings, cfg: BrandConfig, max_calls: int | None) -> Any:
    from efn_esb.oms_http import OmsClient

    return OmsClient(
        oms.base_url,
        cfg.user,
        cfg.password,
        UrllibTransport(max_response_bytes=settings.max_response_bytes),
        TokenBucket(oms.rate_per_sec),  # bucket OMS terpisah dari Core
        max_retries=settings.max_retries,
        timeout=settings.timeout_seconds,
        max_calls=max_calls,
    )


def _start_oms(settings: EsbSettings, oms: OmsSettings, stop: threading.Event) -> threading.Thread | None:
    """G69: loop OMS di thread sendiri (klien HTTP + ClickHouse sendiri). Mati -> None (nol panggilan OMS)."""
    if not oms.enabled:
        return None
    from efn_esb.oms_runner import oms_loop

    unknown = [key for key in oms.brands if key not in {cfg.key for cfg in settings.brands}]
    if unknown:
        raise RuntimeError(f"ESB_OMS_BRANDS: {', '.join(unknown)} tidak ada di ESB_BRANDS")
    thread = threading.Thread(
        target=oms_loop,
        args=(settings, oms, stop, lambda: _store(os.environ), lambda cfg: _oms_client(settings, oms, cfg, None)),
        name="esb-oms",
        daemon=True,
    )
    thread.start()
    return thread


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
    from efn_esb.runner import AuthBreaker, MarginSchedule, RefreshTrigger, guarded_cycle

    margin = MarginSettings.from_env()
    validate_margin(settings, margin)  # fail-fast saat start (brand margin wajib aktif + purchase_line)
    oms = OmsSettings.from_env()  # G69: fail-fast bila ESB_OMS_* tidak sah
    store = _store(os.environ)
    oms_thread = _start_oms(settings, oms, stop)
    breaker = AuthBreaker()  # SEC-186: di memori; restart proses = cabut penangguhan
    trigger = RefreshTrigger()
    schedule = MarginSchedule(margin)  # G67: mingguan, gagal tidak menghentikan ekstraksi
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

    def beat(result: Any) -> None:
        touch(settings.heartbeat_file)
        trigger.note(result)

    while not stop.is_set():
        for rt, client in runtimes.values():
            if stop.is_set():
                break
            guarded_cycle(settings, rt, client, store, breaker, datetime.now(UTC), stop, on_written=beat)
        if not stop.is_set():
            trigger.maybe_refresh(store)  # semua brand selesai -> satu refresh per siklus, hanya bila ada data baru
            schedule.run_due(settings, runtimes, store, breaker, datetime.now(UTC), stop, on_written=beat)
        touch(settings.heartbeat_file)
        if settings.run_once:
            break
        stop.wait(settings.interval_seconds)
    if oms_thread is not None:
        if not settings.run_once:
            stop.set()
        oms_thread.join(timeout=None if settings.run_once else 30)
    log.info("esb-extract berhenti")
    return 0


def _oms_brand(settings: EsbSettings, oms: OmsSettings, brand: str) -> BrandConfig:
    key = brand.strip().lower()
    if key not in oms.brands:
        raise RuntimeError(f"brand {key!r} tidak ada di ESB_OMS_BRANDS")
    return settings.brand(key)


def _cmd_oms_probe(settings: EsbSettings, oms: OmsSettings, args: argparse.Namespace) -> int:
    from datetime import timedelta

    from efn_esb.oms_http import P_BRANCH, P_BRANCH_SUMMARY, P_PAYMENT_METHOD, P_VISIT_PURPOSE
    from efn_esb.oms_transform import oms_rows, transform_payment_methods

    cfg = _oms_brand(settings, oms, args.brand)
    client = _oms_client(settings, oms, cfg, 10)
    yesterday = (wib_today(datetime.now(UTC)) - timedelta(days=1)).isoformat()
    branches = len(oms_rows(client.post(P_BRANCH, {})))
    visits = len(oms_rows(client.post(P_VISIT_PURPOSE, {})))
    methods = len(transform_payment_methods(client.post(P_PAYMENT_METHOD, {}), cfg.key, "", datetime.now(UTC)))
    summary = client.post(P_BRANCH_SUMMARY, {"filterSalesDateFrom": yesterday, "filterSalesDateTo": yesterday})
    print(
        f"oms-probe brand={cfg.key} status=ok cabang={branches} kanal={visits} metode_bayar={methods} "
        f"ringkasan_h1={len(oms_rows(summary))} calls={client.calls}"
    )
    return 0


def _cmd_oms_backfill(
    settings: EsbSettings, oms: OmsSettings, args: argparse.Namespace, stop: threading.Event
) -> int:
    from efn_esb.oms_runner import run_oms_backfill

    cfg = _oms_brand(settings, oms, args.brand)
    try:
        validate_backfill_range(args.start, args.end, wib_today(datetime.now(UTC)))
    except ValueError as exc:
        print(f"oms-backfill ditolak: {exc}", file=sys.stderr)
        return 2
    store = _store(os.environ)
    client = _oms_client(settings, oms, cfg, oms.backfill_max_calls)
    run = run_oms_backfill(settings, oms, cfg, client, store, args.start, args.end, stop, force=args.force)
    print(
        f"oms-backfill brand={cfg.key} status={run.status} slices={run.slices} calls={run.calls} errors={run.errors}"
    )
    return 0 if run.status in ("ok", "window_closed", "limit") else 1


def _cmd_backfill(settings: EsbSettings, args: argparse.Namespace, stop: threading.Event) -> int:
    from efn_esb.runner import RefreshTrigger, run_backfill

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
    trigger = RefreshTrigger()

    def written(result: Any) -> None:
        touch(settings.heartbeat_file)
        trigger.note(result)

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
        on_written=written,
    )
    print(f"backfill brand={cfg.key} status={run.status} slices={run.slices} calls={run.calls} errors={run.errors}")
    # Exit code tetap milik backfill: refresh gagal hanya dicatat (jalankan `refresh-aggregates` manual).
    refreshed = None if stop.is_set() else trigger.maybe_refresh(store)
    if refreshed is not None:
        print(f"refresh-aggregates status={'ok' if refreshed else 'gagal'}")
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


def _cmd_refresh_aggregates(allow_shrink: bool = False) -> int:
    from efn_esb.runner import refresh_aggregates

    try:
        store = _store(os.environ)
    except Exception as exc:  # konfigurasi/koneksi ClickHouse; tanpa detail (bisa memuat path secret)
        log.error("refresh-aggregates: klien ClickHouse gagal dibuat error=%s", type(exc).__name__)
        return 1
    ok = refresh_aggregates(store, allow_shrink=allow_shrink)
    print(f"refresh-aggregates status={'ok' if ok else 'gagal'}")
    return 0 if ok else 1


def _cmd_refresh_margin(brand: str | None) -> int:
    from efn_esb.margin import refresh_margin

    try:
        margin = MarginSettings.from_env()
    except RuntimeError as exc:
        print(f"refresh-margin ditolak: {exc}", file=sys.stderr)
        return 2
    brands = [brand] if brand else list(margin.brands)
    if not brands:
        print("refresh-margin: ESB_MARGIN_BRANDS kosong (fitur mati); tidak ada yang dihitung")
        return 0
    try:
        store = _store(os.environ)
    except Exception as exc:  # konfigurasi/koneksi ClickHouse; tanpa detail (bisa memuat path secret)
        log.error("refresh-margin: klien ClickHouse gagal dibuat error=%s", type(exc).__name__)
        return 1
    today = wib_today(datetime.now(UTC))
    failed = 0
    for key in brands:
        result = refresh_margin(store, key, margin.months, today)
        print(f"refresh-margin brand={key} status={result.status} rows={result.rows}")
        failed += result.status != "ok"
    return 1 if failed else 0


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
    if args.cmd == "refresh-aggregates":
        return _cmd_refresh_aggregates(args.allow_shrink)
    if args.cmd == "refresh-margin":
        return _cmd_refresh_margin(args.brand)
    stop = _stop_event()
    if not esb_enabled():
        if oms_enabled():  # §33.3: ESB_OMS_ENABLED=true mensyaratkan ESB_ENABLED=true (fail closed)
            print("ESB_OMS_ENABLED=true mensyaratkan ESB_ENABLED=true; tidak start", file=sys.stderr)
            return 2
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
    if args.cmd in ("oms-probe", "oms-backfill"):
        oms = OmsSettings.from_env()
        if not oms.enabled:
            print("ESB OMS dinonaktifkan (ESB_OMS_ENABLED=false); tidak ada panggilan")
            return 0
        if args.cmd == "oms-probe":
            return _cmd_oms_probe(settings, oms, args)
        return _cmd_oms_backfill(settings, oms, args, stop)
    return _cmd_probe(settings, args)


if __name__ == "__main__":
    sys.exit(main())
