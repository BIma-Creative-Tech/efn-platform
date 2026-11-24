"""Orkestrasi run/backfill per brand (docs/04 §25.4). I/O diinjeksi (store, get) agar bisa diuji tanpa service.

Worker (thread) hanya memanggil ESB + transform; semua tulisan ClickHouse di thread utama, urutan per slice:
raw_doc -> tabel mart -> bom_ratio -> extract_checkpoint (terakhir). Slice yang gagal tidak punya checkpoint, jadi
ditarik ulang di run berikutnya; baris fakta "yatim" (versi tanpa checkpoint) tidak terlihat di view.
Batasan (diuji di ClickHouse 24.8): baris yatim dengan kunci yang SAMA dengan versi ber-checkpoint menimpanya lewat
FINAL/merge, sehingga bila tulis checkpoint gagal setelah fakta tertulis, hari itu tampak kurang sampai ditarik ulang.
Jalur harian pulih sendiri (H-1/H-7 diulang sampai ada checkpoint baru); backfill `--force` harus dijalankan ulang.
Brand diproses satu per satu (L-111); backfill satu brand per proses.
SEC-186: `AuthBreaker` + `guarded_cycle` menangguhkan brand setelah `AUTH_SUSPEND_AFTER` siklus `auth_failed`
berturut-turut (di memori; hanya restart proses yang mencabutnya) agar akun ESB produksi yang dipakai kasir
tidak terkunci.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from efn_esb.aggregates import TRIGGER_ENTITIES, RefreshResult, RefreshStore, error_label, refresh_all
from efn_esb.config import DAILY_ENTITIES, DELIVERY_REF_ENTITY, PL_ENTITY, EsbSettings, MarginSettings
from efn_esb.extract import BrandRuntime, SliceResult, run_slice
from efn_esb.heartbeat import heartbeat_line
from efn_esb.http import CallBudgetExceeded, EsbAuthError
from efn_esb.margin import is_due, last_ok, record, refresh_margin, refresh_name
from efn_esb.paging import Getter
from efn_esb.planning import (
    BRAND_SCOPE,
    SliceTask,
    backfill_tasks,
    date_range,
    in_window,
    incremental_tasks,
    pl_backfill_tasks,
    pl_refresh_years,
    pl_run_tasks,
    snapshot_due,
    snapshot_tasks,
    wib_today,
    year_start,
)
from efn_esb.records import (
    BOM_RATIO_COLUMNS,
    CHECKPOINT_COLUMNS,
    RAW_COLUMNS,
    RUN_COLUMNS,
    RunRecord,
    bom_ratio_rows,
    checkpoint_row,
    raw_rows,
    version_now,
)
from efn_esb.transform import DIM_SUPPLIER_COLUMNS, MART_TABLES, BomRatio
from efn_esb.transform_g65 import G65_TABLES
from efn_esb.transform_g68 import (
    DIM_PRODUCT_ID_COLUMNS,
    G68_TABLES,
    RawMissing,
    delivery_details_from_raw,
    raw_delivery_nums,
    transform_delivery_ref,
)
from efn_esb.transform_mig import MIG_TABLES

log = logging.getLogger("efn_esb.runner")
# Tabel mart G41 + migrasi G43 (14-g43-migration.sql) + G65 (23-g65-…sql) + G68 (25-g68-esb-pl-waste-fillrate.sql).
ALL_TABLES: dict[str, tuple[str, ...]] = {**MART_TABLES, **MIG_TABLES, **G65_TABLES, **G68_TABLES}
# G65: entitas yang memetakan supplier lewat dim_esb_supplier.supplier_id (docs/04 §27.13.1).
SUPPLIER_ID_ENTITIES = frozenset({"purchase_line"})
# G68: entitas yang memetakan productID -> productCode lewat dim_esb_product_id (docs/04 §32.1.3).
PRODUCT_ID_ENTITIES = frozenset({"transfer_request"})
_PID = DIM_PRODUCT_ID_COLUMNS.index("product_id")
_PCODE = DIM_PRODUCT_ID_COLUMNS.index("product_code")
# Raw goods-delivery G41 (extract.extract_delivery) yang dibaca ulang mode retransform.
GD_LIST_ENDPOINT = "inventory/goods-delivery"
GD_DETAIL_ENDPOINT = "inventory/goods-delivery/{num}"
_SUP_ID = DIM_SUPPLIER_COLUMNS.index("supplier_id")
_SUP_CODE = DIM_SUPPLIER_COLUMNS.index("supplier_code")
_SUP_NAME = DIM_SUPPLIER_COLUMNS.index("supplier_name")

# SEC-186: jumlah siklus `auth_failed` berturut-turut sebelum brand ditangguhkan.
AUTH_SUSPEND_AFTER = 2
# Status yang membuktikan login ESB berhasil -> hitungan kegagalan direset. `error` (mis. jaringan/ClickHouse) tidak
# membuktikan apa pun tentang kredensial, jadi tidak mereset dan tidak menambah hitungan.
_LOGIN_OK_STATUSES = frozenset({"ok", "partial", "limit"})


class Store(Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...

    def checkpoints(self, brand: str, since: date) -> dict[tuple[str, str, date], datetime]: ...

    def load_aliases(self, brand: str) -> dict[str, str]: ...

    def load_bom_ratios(self, brand: str) -> dict[int, BomRatio]: ...

    def load_suppliers(self, brand: str) -> list[tuple[int, str, str]]: ...

    def load_product_ids(self, brand: str) -> dict[int, str]: ...

    def load_raw(self, brand: str, endpoint: str, doc_keys: Sequence[str]) -> dict[str, tuple[date, Any]]: ...


class Budgeted(Protocol):
    @property
    def calls(self) -> int: ...

    def reset_budget(self, max_calls: int | None) -> None: ...

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@dataclass
class Outcome:
    slices: int = 0
    rows: int = 0
    errors: int = 0
    budget: bool = False
    auth: bool = False
    window_closed: bool = False

    def status(self) -> str:
        if self.auth:
            return "auth_failed"
        if self.budget:
            return "limit"
        if self.window_closed:
            return "window_closed"
        return "partial" if self.errors else "ok"

    def merge(self, other: "Outcome") -> None:
        self.slices += other.slices
        self.rows += other.rows
        self.errors += other.errors
        self.budget = self.budget or other.budget
        self.auth = self.auth or other.auth
        self.window_closed = self.window_closed or other.window_closed


def write_slice(store: Store, brand: str, result: SliceResult, fetched_at: datetime) -> None:
    """Urutan tulis satu slice; checkpoint TERAKHIR (lihat docstring modul)."""
    store.insert_control("raw_doc", RAW_COLUMNS, raw_rows(brand, result.raw, fetched_at))
    for table, rows in result.tables.items():
        store.insert(table, ALL_TABLES[table], rows)
    if result.bom_ratios:
        store.insert_control("bom_ratio", BOM_RATIO_COLUMNS, bom_ratio_rows(brand, result.bom_ratios, result.version))
    store.insert_control(
        "extract_checkpoint",
        CHECKPOINT_COLUMNS,
        [checkpoint_row(brand, result.task, result.version, result.rows, result.calls)],
    )
    if result.derived:  # G68: checkpoint entitas turunan (delivery_ref) SETELAH checkpoint utama, versi sama
        store.insert_control(
            "extract_checkpoint",
            CHECKPOINT_COLUMNS,
            [checkpoint_row(brand, task, result.version, rows, 0) for task, rows in result.derived],
        )


def _absorb(rt: BrandRuntime, result: SliceResult) -> None:
    """Alias/cabang hasil slice -> status runtime (untuk slice berikutnya di proses yang sama)."""
    with rt.lock:
        rt.aliases.update(result.aliases)
        for row in result.tables.get("dim_esb_product_id", []):  # G68: pemetaan productID dari slice GTR
            rt.product_ids[int(row[_PID])] = str(row[_PCODE])
    if result.task.entity == "branch":
        rt.branch_rows = result.tables.get("dim_esb_branch", [])
    if result.task.entity == "master":
        suppliers = [
            (int(row[_SUP_ID]), str(row[_SUP_CODE]), str(row[_SUP_NAME]))
            for row in result.tables.get("dim_esb_supplier", [])
        ]
        with rt.lock:
            rt.suppliers = suppliers


def execute(
    tasks: Iterable[SliceTask],
    rt: BrandRuntime,
    store: Store,
    get: Getter,
    workers: int,
    stop: threading.Event,
    gate: Callable[[], bool] | None = None,
    clock: Callable[[], datetime] = version_now,
    on_written: Callable[[SliceResult], None] | None = None,
) -> Outcome:
    """Jalankan slice paralel (maks `workers`, antrean terbatas). `gate()` False -> berhenti menjadwalkan (jendela
    backfill tertutup); slice yang sedang jalan diselesaikan dan ditulis."""
    outcome = Outcome()
    pending = iter(tasks)
    inflight: dict[Future[SliceResult], SliceTask] = {}
    exhausted = False
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix=f"esb-{rt.cfg.key}") as pool:
        while True:
            while not exhausted and len(inflight) < workers * 2:
                if stop.is_set() or outcome.budget or outcome.auth:
                    exhausted = True
                    break
                if gate is not None and not gate():
                    outcome.window_closed = True
                    exhausted = True
                    break
                task = next(pending, None)
                if task is None:
                    exhausted = True
                    break
                inflight[pool.submit(run_slice, rt, task, clock(), get)] = task
            if not inflight:
                return outcome
            done, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for future in done:
                task = inflight.pop(future)
                try:
                    result = future.result()
                except CallBudgetExceeded:
                    outcome.budget = True
                    continue
                except EsbAuthError:
                    outcome.auth = True
                    outcome.errors += 1
                    log.error("brand=%s entity=%s day=%s status=auth_failed", rt.cfg.key, task.entity, task.day)
                    continue
                except Exception as exc:  # satu slice gagal tidak menghentikan slice lain
                    outcome.errors += 1
                    log.error(
                        "brand=%s entity=%s scope=%s day=%s status=error error=%s",
                        rt.cfg.key,
                        task.entity,
                        task.scope,
                        task.day,
                        type(exc).__name__,
                    )
                    continue
                write_slice(store, rt.cfg.key, result, datetime.now(UTC))
                _absorb(rt, result)
                outcome.slices += 1
                outcome.rows += result.rows
                log.info(
                    "brand=%s entity=%s scope=%s day=%s status=done rows=%d calls=%d",
                    rt.cfg.key,
                    task.entity,
                    task.scope,
                    task.day,
                    result.rows,
                    result.calls,
                )
                if on_written is not None:
                    on_written(result)


def prepare_brand(
    rt: BrandRuntime,
    store: Store,
    get: Getter,
    today: date,
    checkpoints: dict[tuple[str, str, date], datetime],
    stop: threading.Event,
    force_master: bool = False,
    need_supplier_ids: bool = False,
    need_product_ids: bool = False,
) -> Outcome:
    """Cabang (setiap run, dibutuhkan daftar toko) + alias & rasio dari ClickHouse + master bila jatuh tempo.

    G65 `need_supplier_ids` (entitas `purchase_line` aktif): supplier dimuat dari dim_esb_supplier; bila belum ada satu
    pun `supplier_id` > 0 (kolom baru, master belum jalan sejak deploy) master dipaksa SEKALI per proses agar join
    header PO -> kode supplier tidak jatuh ke fallback nama.
    G68 `need_product_ids` (entitas `transfer_request` aktif): pemetaan productID -> kode dari dim_esb_product_id."""
    rt.aliases = store.load_aliases(rt.cfg.key)
    rt.ratios = store.load_bom_ratios(rt.cfg.key)
    if need_product_ids:
        product_ids = store.load_product_ids(rt.cfg.key)
        with rt.lock:
            rt.product_ids = product_ids
    if need_supplier_ids:
        suppliers = store.load_suppliers(rt.cfg.key)
        with rt.lock:
            rt.suppliers = suppliers
        if not rt.master_forced_for_ids and not any(supplier_id > 0 for supplier_id, _, _ in suppliers):
            rt.master_forced_for_ids = True
            force_master = True
    outcome = execute([SliceTask(today, "branch", BRAND_SCOPE)], rt, store, get, 1, stop)
    if outcome.slices == 0:
        return outcome  # tanpa daftar cabang slice lain tidak bisa direncanakan
    if force_master or snapshot_due("master", checkpoints, today):
        outcome.merge(execute([SliceTask(today, "master", BRAND_SCOPE)], rt, store, get, 1, stop))
    return outcome


def _finish(run: RunRecord, calls: int, outcome: Outcome, store: Store, started: float) -> RunRecord:
    run.calls = calls
    run.rows, run.errors, run.slices = outcome.rows, outcome.errors, outcome.slices
    run.status = outcome.status() if run.status == "ok" else run.status
    finished = datetime.now(UTC)
    try:
        store.insert_control("extract_run", RUN_COLUMNS, [run.row(finished)])
    except Exception as exc:  # riwayat run best effort, log heartbeat tetap keluar
        log.error("brand=%s extract_run tidak tersimpan error=%s", run.brand, type(exc).__name__)
    level = logging.INFO if run.status == "ok" else logging.WARNING
    log.log(level, heartbeat_line(run, int((time.monotonic() - started) * 1000), finished))
    return run


def run_cycle(
    settings: EsbSettings,
    rt: BrandRuntime,
    client: Budgeted,
    store: Store,
    now: datetime,
    stop: threading.Event,
    on_written: Callable[[SliceResult], None] | None = None,
) -> RunRecord:
    """Satu siklus incremental satu brand: cabang, master harian, H-1 + koreksi H-7 + gap fill.
    G68: + slice tahun `profit_loss` (sekali/hari, `planning.pl_run_tasks`) bila tercantum di ESB_ENTITIES."""
    started = time.monotonic()
    run = RunRecord(rt.cfg.key, "run", datetime.now(UTC), uuid.uuid4())
    outcome = Outcome()
    client.reset_budget(settings.max_calls_per_run)
    try:
        today = wib_today(now)
        lookback = max(max(settings.correction_days), settings.gap_fill_days) + 1
        since = today - timedelta(days=lookback)
        with_pl = PL_ENTITY in settings.entities
        if with_pl:  # slice P&L bertanggal 1 Januari tahun refresh
            since = min(since, year_start(min(pl_refresh_years(today, settings.pl_refresh_months))))
        checkpoints = store.checkpoints(rt.cfg.key, since)
        entities = [e for e in settings.entities if e in DAILY_ENTITIES]
        rt.delivery_ref = DELIVERY_REF_ENTITY in settings.entities and "delivery" in entities
        need_ids = bool(SUPPLIER_ID_ENTITIES & set(entities))
        need_pids = bool(PRODUCT_ID_ENTITIES & set(entities))
        outcome = prepare_brand(
            rt, store, client.get, today, checkpoints, stop, need_supplier_ids=need_ids, need_product_ids=need_pids
        )
        if outcome.slices and not (outcome.auth or outcome.budget):
            tasks = incremental_tasks(
                entities,
                rt.stores(include_inactive=False),
                now,
                checkpoints,
                settings.correction_days,
                settings.gap_fill_days,
                settings.sales_ready_hour,
            )
            if with_pl:
                tasks += pl_run_tasks(
                    rt.pl_branches(include_inactive=False),
                    now,
                    checkpoints,
                    settings.pl_refresh_months,
                    settings.sales_ready_hour,
                )
            outcome.merge(execute(tasks, rt, store, client.get, settings.workers, stop, on_written=on_written))
        elif not outcome.slices and not (outcome.auth or outcome.budget):
            run.status = "error"
    except Exception as exc:  # brand gagal tidak menghentikan brand lain
        log.error("brand=%s run gagal error=%s", rt.cfg.key, type(exc).__name__)
        run.status = "error"
    return _finish(run, client.calls, outcome, store, started)


class AuthBreaker:
    """Circuit breaker `auth_failed` per brand (SEC-186). Murni, di memori, tidak pernah pulih sendiri.

    `record()` dipanggil setelah setiap siklus: `auth_failed` menambah hitungan; status yang membuktikan
    login berhasil (`_LOGIN_OK_STATUSES`) mereset hitungan; status lain (`error`) tidak mengubah apa pun.
    Setelah `threshold` kegagalan berturut-turut brand ditangguhkan sampai proses di-restart (operator
    memperbaiki secret lalu restart).
    """

    def __init__(self, threshold: int = AUTH_SUSPEND_AFTER) -> None:
        if threshold < 1:
            raise ValueError("threshold harus >= 1")
        self.threshold = threshold
        self._failures: dict[str, int] = {}
        self._suspended: set[str] = set()

    def suspended(self, brand: str) -> bool:
        return brand in self._suspended

    def failures(self, brand: str) -> int:
        return self._failures.get(brand, 0)

    def record(self, brand: str, status: str) -> bool:
        """True tepat sekali: pada siklus yang membuat brand ditangguhkan."""
        if status == "auth_failed":
            count = self._failures.get(brand, 0) + 1
            self._failures[brand] = count
            if count >= self.threshold and brand not in self._suspended:
                self._suspended.add(brand)
                return True
        elif status in _LOGIN_OK_STATUSES:
            self._failures[brand] = 0
        return False


def record_suspended(brand: str, store: Store) -> RunRecord:
    """Siklus brand yang ditangguhkan: tanpa login/panggilan ESB; hanya baris `extract_run` (status `auth_suspended`,
    terlihat di `v_esb_data_quality` sebagai `last_run_not_ok`) + heartbeat INFO (WARNING hanya sekali saat
    penangguhan dimulai, lihat `guarded_cycle`)."""
    run = RunRecord(brand, "run", datetime.now(UTC), uuid.uuid4(), status="auth_suspended")
    finished = datetime.now(UTC)
    try:
        store.insert_control("extract_run", RUN_COLUMNS, [run.row(finished)])
    except Exception as exc:  # riwayat run best effort
        log.error("brand=%s extract_run tidak tersimpan error=%s", brand, type(exc).__name__)
    log.info(heartbeat_line(run, 0, finished))
    return run


def guarded_cycle(
    settings: EsbSettings,
    rt: BrandRuntime,
    client: Budgeted,
    store: Store,
    breaker: AuthBreaker,
    now: datetime,
    stop: threading.Event,
    on_written: Callable[[SliceResult], None] | None = None,
) -> RunRecord:
    """`run_cycle` dengan circuit breaker auth (SEC-186). Brand yang ditangguhkan tidak login lagi."""
    brand = rt.cfg.key
    if breaker.suspended(brand):
        return record_suspended(brand, store)
    run = run_cycle(settings, rt, client, store, now, stop, on_written=on_written)
    if breaker.record(brand, run.status):
        # Tanpa user/password/token: hanya kunci brand (sudah divalidasi regex) dan nama secret.
        log.warning(
            "brand=%s status=auth_suspended: %d siklus auth_failed berturut-turut; login ESB dihentikan sampai "
            "proses di-restart. Perbaiki secret esb_core_password_%s (atau keluarkan brand dari ESB_BRANDS) lalu "
            "restart esb-extract",
            brand,
            breaker.threshold,
            brand,
        )
    return run


def refresh_aggregates(store: RefreshStore, allow_shrink: bool = False) -> bool:
    """Bangun ulang tabel agregat Insight (efn_esb/aggregates.py). Tidak pernah melempar: gagal = log error + False,
    data ekstraksi yang sudah tersimpan tidak terpengaruh. `allow_shrink` hanya dari CLI manual (SEC-AG-03)."""
    try:
        results = refresh_all(store, allow_shrink=allow_shrink)
    except Exception as exc:
        log.error("refresh agregat gagal error=%s", error_label(exc))
        return False
    return all(r.status == "ok" for r in results)


class RefreshTrigger:
    """Tandai perlu refresh bila slice entitas pengisi agregat (TRIGGER_ENTITIES) ditulis; refresh di akhir siklus.
    Gagal/busy -> tetap ditandai, dicoba lagi di akhir siklus berikutnya."""

    def __init__(self) -> None:
        self.dirty = False

    def note(self, result: SliceResult) -> None:
        if result.task.entity in TRIGGER_ENTITIES:
            self.dirty = True

    def maybe_refresh(self, store: RefreshStore) -> bool | None:
        """None = tidak ada data baru (tidak ada query); True/False = hasil refresh."""
        if not self.dirty:
            return None
        if refresh_aggregates(store):
            self.dirty = False
            return True
        return False


class MarginSchedule:
    """G67 (docs/04 §31.2): refresh margin bahan mingguan di loop `run`. Per brand di ESB_MARGIN_BRANDS yang jatuh
    tempo (margin.is_due): (1) snapshot `bom_detail` brand itu, (2) hitung + tulis (margin.refresh_margin). Paling
    banyak satu percobaan per brand per hari WIB (di memori). Tidak pernah melempar: gagal = log + aggregate_refresh
    `error`, ekstraksi harian tidak terpengaruh (pola agregat §25.7)."""

    def __init__(self, margin: MarginSettings) -> None:
        self.margin = margin
        self.attempted: dict[str, date] = {}

    def run_due(
        self,
        settings: EsbSettings,
        runtimes: Mapping[str, tuple[BrandRuntime, Budgeted]],
        store: Any,
        breaker: AuthBreaker,
        now: datetime,
        stop: threading.Event,
        on_written: Callable[[SliceResult], None] | None = None,
    ) -> list[RefreshResult]:
        results: list[RefreshResult] = []
        for brand in self.margin.brands:
            pair = runtimes.get(brand)
            if stop.is_set() or pair is None or breaker.suspended(brand):
                continue
            try:
                previous = last_ok(store, brand)
            except Exception as exc:  # ClickHouse tidak terjangkau: coba lagi siklus berikutnya
                log.error("margin=%s cek jadwal gagal error=%s", brand, error_label(exc))
                continue
            if not is_due(now, self.margin.weekday, self.margin.hour_wib, previous, self.attempted.get(brand)):
                continue
            today = wib_today(now)
            self.attempted[brand] = today
            try:
                results.append(self._refresh(settings, pair, store, brand, today, stop, on_written))
            except Exception as exc:  # pertahanan berlapis: loop ekstraksi tidak boleh berhenti karena margin
                log.error("margin=%s refresh gagal error=%s", brand, error_label(exc))
        return results

    def _refresh(
        self,
        settings: EsbSettings,
        pair: tuple[BrandRuntime, Budgeted],
        store: Any,
        brand: str,
        today: date,
        stop: threading.Event,
        on_written: Callable[[SliceResult], None] | None,
    ) -> RefreshResult:
        rt, client = pair
        started = version_now()
        snap = run_snapshot(settings, rt, client, store, ["bom_detail"], today, stop, on_written=on_written)
        if snap.status != "ok":
            # Resep basi tidak dihitung diam-diam: refresh berikutnya minggu depan atau `refresh-margin` manual.
            label = f"snapshot_{snap.status}"
            result = RefreshResult(refresh_name(brand), "error", started, version_now(), error=label)
            record(store, result)
            return result
        return refresh_margin(store, brand, self.margin.months, today)


def run_backfill(
    settings: EsbSettings,
    rt: BrandRuntime,
    client: Budgeted,
    store: Store,
    start: date,
    end: date,
    entities: Sequence[str],
    stop: threading.Event,
    *,
    force: bool = False,
    ignore_window: bool = False,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_written: Callable[[SliceResult], None] | None = None,
) -> RunRecord:
    """Backfill resumable satu brand. Di luar `ESB_BACKFILL_WINDOW` (WIB) berhenti di batas slice berikutnya
    (`status=window_closed`); jalankan ulang perintah yang sama untuk melanjutkan.
    G68: `profit_loss` = slice tahun yang beririsan [start, end] x semua cabang (+ nonaktif bila
    ESB_BACKFILL_INCLUDE_INACTIVE) + total brand; `delivery_ref` hanya bersama `delivery` (turunan, tanpa GET)."""
    started = time.monotonic()
    run = RunRecord(rt.cfg.key, "backfill", datetime.now(UTC), uuid.uuid4())
    outcome = Outcome()
    client.reset_budget(settings.backfill_max_calls)
    window = settings.backfill_window

    def gate() -> bool:
        return in_window(now_fn(), window)

    try:
        if not ignore_window and not gate():
            log.warning("brand=%s backfill di luar ESB_BACKFILL_WINDOW; tidak ada panggilan", rt.cfg.key)
            outcome.window_closed = True
            return _finish(run, client.calls, outcome, store, started)
        today = wib_today(now_fn())
        with_pl = PL_ENTITY in entities
        checkpoints = store.checkpoints(rt.cfg.key, min(year_start(start.year) if with_pl else start, today))
        daily = [e for e in entities if e in DAILY_ENTITIES]
        rt.delivery_ref = DELIVERY_REF_ENTITY in entities and "delivery" in daily
        need_ids = bool(SUPPLIER_ID_ENTITIES & set(daily))
        need_pids = bool(PRODUCT_ID_ENTITIES & set(daily))
        outcome = prepare_brand(
            rt, store, client.get, today, checkpoints, stop, need_supplier_ids=need_ids, need_product_ids=need_pids
        )
        if outcome.slices and not (outcome.auth or outcome.budget):
            tasks = backfill_tasks(daily, date_range(start, end), rt.stores(), checkpoints, force)
            if with_pl:
                tasks += pl_backfill_tasks(rt.pl_branches(), start, end, checkpoints, force)
            log.info("brand=%s backfill slices=%d (%s..%s)", rt.cfg.key, len(tasks), start, end)
            outcome.merge(
                execute(
                    tasks,
                    rt,
                    store,
                    client.get,
                    settings.workers,
                    stop,
                    gate=None if ignore_window else gate,
                    on_written=on_written,
                )
            )
        elif not outcome.slices and not (outcome.auth or outcome.budget):
            run.status = "error"
    except Exception as exc:
        log.error("brand=%s backfill gagal error=%s", rt.cfg.key, type(exc).__name__)
        run.status = "error"
    return _finish(run, client.calls, outcome, store, started)


def run_snapshot(
    settings: EsbSettings,
    rt: BrandRuntime,
    client: Budgeted,
    store: Store,
    entities: Sequence[str],
    day: date,
    stop: threading.Event,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    on_written: Callable[[SliceResult], None] | None = None,
) -> RunRecord:
    """Snapshot entitas migrasi G43 (`extract_mig.SNAPSHOT_ENTITIES`) + G65 `stock_opening` satu brand: cabang (+ master
    bila jatuh tempo) lalu satu slice per entitas (scope '*', `day`); `stock_opening` satu slice per cabang
    (`planning.snapshot_tasks`, semua cabang dim_esb_branch). Selalu ditarik ulang (bukan resumable): snapshot = keadaan
    ESB saat perintah dijalankan. Mode `extract_run` = `snapshot`. Anggaran panggilan = `ESB_BACKFILL_MAX_CALLS`."""
    started = time.monotonic()
    run = RunRecord(rt.cfg.key, "snapshot", datetime.now(UTC), uuid.uuid4())
    outcome = Outcome()
    client.reset_budget(settings.backfill_max_calls)
    try:
        today = wib_today(now_fn())
        checkpoints = store.checkpoints(rt.cfg.key, today)
        outcome = prepare_brand(rt, store, client.get, today, checkpoints, stop)
        if outcome.slices and not (outcome.auth or outcome.budget):
            branch_ids = [int(row[2]) for row in rt.branch_rows]
            tasks = snapshot_tasks(entities, day, branch_ids)
            outcome.merge(execute(tasks, rt, store, client.get, 1, stop, on_written=on_written))
        elif not outcome.slices and not (outcome.auth or outcome.budget):
            run.status = "error"
    except Exception as exc:
        log.error("brand=%s snapshot gagal error=%s", rt.cfg.key, type(exc).__name__)
        run.status = "error"
    return _finish(run, client.calls, outcome, store, started)


def run_retransform(
    rt: BrandRuntime,
    store: Store,
    start: date,
    end: date,
    stop: threading.Event,
    *,
    clock: Callable[[], datetime] = version_now,
) -> tuple[RunRecord, list[date]]:
    """G68 §32.1.4: isi ulang `fact_esb_delivery_ref_line` dari raw_doc goods-delivery G41 TANPA panggilan/login ESB.

    Per hari D: list raw (`doc_key=D`) -> nomor dokumen -> detail raw (`doc_key=<nomor>`, wajib `doc_date=D`). Lengkap
    -> slice versi baru (tabel + checkpoint `delivery_ref`, tanpa raw baru). Tidak lengkap -> slice TIDAK ditulis,
    log `reason=raw_missing`, hari dikembalikan agar ditarik ulang lewat `backfill --entity delivery,delivery_ref
    --force` di jendela malam. Boleh di luar ESB_BACKFILL_WINDOW (tanpa beban ESB). Mode extract_run `retransform`."""
    started = time.monotonic()
    cfg = rt.cfg
    run = RunRecord(cfg.key, "retransform", datetime.now(UTC), uuid.uuid4())
    outcome = Outcome()
    missing_days: list[date] = []
    try:
        for day in date_range(start, end):
            if stop.is_set():
                break
            listed = store.load_raw(cfg.key, GD_LIST_ENDPOINT, [day.isoformat()]).get(day.isoformat())
            payload = listed[1] if listed is not None else None
            details = store.load_raw(cfg.key, GD_DETAIL_ENDPOINT, raw_delivery_nums(payload))
            try:
                docs = delivery_details_from_raw(day, payload, details)
            except RawMissing as exc:
                outcome.errors += 1
                missing_days.append(day)
                log.warning(
                    "brand=%s entity=delivery_ref day=%s status=error reason=raw_missing docs=%d",
                    cfg.key,
                    day,
                    len(exc.missing),
                )
                continue
            version = clock()
            rows = transform_delivery_ref(docs, cfg.key, cfg.company_for, day, version)
            task = SliceTask(day, DELIVERY_REF_ENTITY, BRAND_SCOPE)
            write_slice(store, cfg.key, SliceResult(task, version, tables={"fact_esb_delivery_ref_line": rows}),
                        datetime.now(UTC))  # fmt: skip
            outcome.slices += 1
            outcome.rows += len(rows)
    except Exception as exc:
        log.error("brand=%s retransform gagal error=%s", cfg.key, type(exc).__name__)
        run.status = "error"
    return _finish(run, 0, outcome, store, started), missing_days
