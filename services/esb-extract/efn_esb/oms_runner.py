"""Orkestrasi ESB OMS (G69, docs/04 §33.3-§33.4). I/O diinjeksi (store, post) agar bisa diuji tanpa service.

Desain (paling sederhana yang independen dari loop Core): loop OMS jalan di THREAD sendiri di proses `run`
(`oms_loop`), dengan klien HTTP, token bucket, budget, circuit breaker auth, dan klien ClickHouse SENDIRI (klien
ClickHouse tidak dipakai bersama antar-thread). Exception OMS ditangkap di loop OMS -> loop Core tidak terpengaruh;
gagal Core tidak menyentuh thread OMS. `ESB_OMS_ENABLED=false` -> thread tidak dibuat (nol panggilan OMS).

Satu siklus OMS per brand (`oms_cycle`):
1. snapshot harian (sekali per hari WIB): visit purpose + payment method (entitas `oms_master`) dan pemetaan menu ->
   produk FIN `dim_esb_oms_menu_fin` (entitas `oms_menu_fin`, dari resep mart G43 yang sudah ada; gagal = error dicatat,
   tidak menghentikan siklus);
2. hari berjalan (hanya di `ESB_OMS_INTRADAY_HOURS` WIB): `sales-branch-summary` (1 panggilan) -> slice
   `oms_branch_daily`; lalu SETIAP cabang yang punya struk (atau sudah punya slice hari itu) ditarik ulang utuh
   (slice `oms_sales` = cabang x hari diganti);
3. hari final: H-n (`ESB_OMS_CORRECTION_DAYS`) setelah `ESB_SALES_READY_HOUR_WIB` + gap fill `ESB_GAP_FILL_DAYS`
   (planning.incremental_days, penanda hari = entitas `oms_day` scope '*', ditulis hanya bila semua cabang hari itu
   sukses). Cabang yang sudah final hari ini (checkpoint `oms_sales` >= jam siap) dilewati -> retry hanya cabang gagal.
Urutan tulis per slice = pola §25.4: raw_doc -> mart -> extract_checkpoint (terakhir). Slice yang menyentuh batas
halaman (`ESB_OMS_MAX_PAGES`) = gagal (`partial`), tanpa checkpoint (§33.4 butir 4).
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from efn_esb.config import BrandConfig, EsbSettings, OmsSettings
from efn_esb.extract import RawDoc
from efn_esb.http import CallBudgetExceeded, EsbAuthError
from efn_esb.margin import render
from efn_esb.oms_http import (
    BRANCH_CODE,
    DATE_FROM,
    DATE_TO,
    P_BRANCH,
    P_BRANCH_SUMMARY,
    P_PAYMENT_METHOD,
    P_SALES_HEAD,
    P_VISIT_PURPOSE,
)
from efn_esb.oms_transform import (
    MENU_FIN_COLUMNS,
    OMS_TABLES,
    PAGE_SIZE,
    BomHeaderRow,
    BomLine,
    OmsBill,
    SliceKey,
    build_menu_fin,
    oms_rows,
    parse_day,
    prepare_bill,
    strip_oms,
    transform_bills,
    transform_branch_daily,
    transform_payment_methods,
    transform_visit_purposes,
)
from efn_esb.paging import PagingError
from efn_esb.planning import BRAND_SCOPE, WIB, SliceTask, date_range, in_window, incremental_days, wib_now, wib_today
from efn_esb.records import CHECKPOINT_COLUMNS, RAW_COLUMNS, RunRecord, checkpoint_row, raw_rows, version_now
from efn_esb.runner import AuthBreaker, Outcome, _finish

log = logging.getLogger("efn_esb.oms")

E_SALES = "oms_sales"
E_DAILY = "oms_branch_daily"
E_DAY = "oms_day"
E_MASTER = "oms_master"
E_MENU_FIN = "oms_menu_fin"
EP_SALES = "oms_sales_head"
EP_SUMMARY = "oms_branch_summary"
EP_VISIT = "oms_visit_purpose"
EP_PAYMENT = "oms_payment_method"
SUMMARY_CHUNK_DAYS = 31  # backfill: satu panggilan summary per <= 31 hari

Poster = Callable[..., Any]  # post(path, body, page=None) -> JSON

BRANCH_SQL = """
SELECT branch_code, branch_id
FROM $mart.dim_esb_branch FINAL
WHERE brand = {brand:String} AND branch_code != ''
"""
BOM_HEADER_SQL = """
SELECT bom_id, bom_code, bom_name, bom_type_name, product_code, product_name, active
FROM $mart.dim_esb_bom FINAL
WHERE brand = {brand:String}
  AND _version = (SELECT max(_version) FROM $mart.dim_esb_bom WHERE brand = {brand:String})
"""
BOM_LINE_SQL = """
SELECT bom_id, product_code, product_name, qty, uom
FROM $mart.dim_esb_bom_line FINAL
WHERE brand = {brand:String}
  AND _version = (SELECT max(_version) FROM $mart.dim_esb_bom_line WHERE brand = {brand:String})
"""
MENU_PRICE_SQL = """
SELECT menu_code, bom_name
FROM $mart.dim_esb_menu_price FINAL
WHERE brand = {brand:String} AND menu_code != '' AND bom_name != ''
  AND _version = (SELECT max(_version) FROM $mart.dim_esb_menu_price WHERE brand = {brand:String})
"""
FIN_PRODUCT_SQL = """
SELECT product_code
FROM $mart.dim_esb_product FINAL
WHERE brand = {brand:String} AND is_fin = 1
"""


class OmsParseError(RuntimeError):
    """Respons OMS terbaca tetapi bentuknya tidak dikenal -> siklus error, hari tidak ditandai final."""


class OmsPageLimit(PagingError):
    """Batas keras halaman `ESB_OMS_MAX_PAGES` tercapai -> slice gagal tanpa checkpoint."""


class OmsStore(Protocol):
    @property
    def mart_db(self) -> str: ...

    @property
    def raw_db(self) -> str: ...

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...

    def checkpoints(self, brand: str, since: date) -> dict[tuple[str, str, date], datetime]: ...

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[Sequence[Any]]: ...


class OmsApi(Protocol):
    @property
    def calls(self) -> int: ...

    def reset_budget(self, max_calls: int | None) -> None: ...

    def post(self, path: str, body: Mapping[str, Any] | None = None, page: int | None = None) -> Any: ...


@dataclass
class OmsSlice:
    task: SliceTask
    version: datetime
    raw: list[RawDoc] = field(default_factory=list)
    tables: dict[str, list[list[Any]]] = field(default_factory=dict)
    calls: int = 0
    errors: int = 0

    @property
    def rows(self) -> int:
        return sum(len(rows) for rows in self.tables.values())


# ================================================================== paginasi + slice (murni, post diinjeksi)
def fetch_sales_head(post: Poster, day: date, branch_code: str, max_pages: int) -> tuple[list[dict[str, Any]], int]:
    """Semua struk satu cabang x hari. Berhenti bila halaman < 20 baris, halaman kosong, atau halaman tanpa salesNum
    baru (halaman sesudah terakhir MENGULANG halaman terakhir -> dibuang). Melewati `max_pages` -> `OmsPageLimit`.
    Mengembalikan (struk mentah unik per salesNum, jumlah panggilan)."""
    body = {DATE_FROM: day.isoformat(), DATE_TO: day.isoformat(), BRANCH_CODE: branch_code}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 0
    while True:
        page += 1
        if page > max_pages:
            raise OmsPageLimit(f"sales-head: melebihi {max_pages} halaman")
        data = oms_rows(post(P_SALES_HEAD, body, page=page))
        if not data:
            return rows, page
        fresh_rows: list[dict[str, Any]] = []
        for row in data:
            num = str(row.get("salesNum") or "").strip()
            if num:
                if num in seen:
                    continue
                seen.add(num)
            fresh_rows.append(row)
        if not any(str(row.get("salesNum") or "").strip() for row in fresh_rows):
            return rows, page  # halaman berulang (tanpa salesNum baru) dibuang utuh
        rows.extend(fresh_rows)
        if len(data) < PAGE_SIZE:
            return rows, page
        log.debug("oms sales-head page=%d rows=%d", page, len(data))


def extract_sales_slice(
    post: Poster,
    cfg: BrandConfig,
    branch_code: str,
    branch_id: int,
    day: date,
    version: datetime,
    max_pages: int,
) -> OmsSlice:
    raw, calls = fetch_sales_head(post, day, branch_code, max_pages)
    bills: list[OmsBill] = [prepare_bill(row) for row in raw]  # strip PII/PCI SEBELUM raw & mart (§33.6)
    key = SliceKey(cfg.key, cfg.company_for(branch_id), branch_id, branch_code, day, version)
    tables = transform_bills(bills, key)
    result = OmsSlice(SliceTask(day, E_SALES, branch_code), version, calls=calls, errors=tables.errors)
    result.tables = tables.tables
    result.raw.append(RawDoc(EP_SALES, f"{day.isoformat()}|{branch_code}", day, [b.payload for b in bills]))
    return result


def write_oms_slice(store: OmsStore, brand: str, result: OmsSlice, fetched_at: datetime) -> None:
    """raw_doc -> mart -> checkpoint (TERAKHIR)."""
    store.insert_control("raw_doc", RAW_COLUMNS, raw_rows(brand, result.raw, fetched_at))
    for table, rows in result.tables.items():
        store.insert(table, OMS_TABLES[table], rows)
    store.insert_control(
        "extract_checkpoint",
        CHECKPOINT_COLUMNS,
        [checkpoint_row(brand, result.task, result.version, result.rows, result.calls)],
    )


def load_branch_ids(store: OmsStore, brand: str) -> dict[str, int]:
    rows = store.query_rows(render(BRANCH_SQL, store), {"brand": brand})
    return {str(code): int(branch_id) for code, branch_id in rows}


# ================================================================== langkah siklus
@dataclass
class OmsContext:
    settings: EsbSettings
    oms: OmsSettings
    cfg: BrandConfig
    api: OmsApi
    store: OmsStore
    stop: threading.Event
    branch_ids: dict[str, int] = field(default_factory=dict)
    checkpoints: dict[tuple[str, str, date], datetime] = field(default_factory=dict)


def _summary_slices(ctx: OmsContext, start: date, end: date) -> dict[date, list[list[Any]]]:
    """Satu panggilan `sales-branch-summary` [start, end] -> slice `oms_branch_daily` per hari (ditulis)."""
    version = version_now()
    result = ctx.api.post(P_BRANCH_SUMMARY, {DATE_FROM: start.isoformat(), DATE_TO: end.isoformat()})
    rows = [strip_oms(row) for row in oms_rows(result)]
    by_day = transform_branch_daily(rows, ctx.cfg.key, ctx.cfg.company_for, ctx.branch_ids, version)
    dropped = len(rows) - sum(len(v) for v in by_day.values())
    if dropped:
        # Baris ringkasan yang tak terbaca (tanggal/kode) -> JANGAN lanjut menandai hari final dengan data kosong.
        raise OmsParseError(f"sales-branch-summary: {dropped} dari {len(rows)} baris tak terbaca (format tanggal/kode)")
    fetched = datetime.now(UTC)
    for day in date_range(start, end):
        day_rows = by_day.get(day, [])
        raw_day = [r for r in rows if parse_day(r.get("salesDate")) == day]
        result_slice = OmsSlice(SliceTask(day, E_DAILY, BRAND_SCOPE), version, calls=1 if day == end else 0)
        result_slice.tables = {"fact_esb_oms_branch_daily": day_rows}
        result_slice.raw.append(RawDoc(EP_SUMMARY, day.isoformat(), day, raw_day))
        write_oms_slice(ctx.store, ctx.cfg.key, result_slice, fetched)
    return by_day


def _branches_for_day(ctx: OmsContext, day: date, daily_rows: Iterable[list[Any]]) -> list[str]:
    """Cabang dengan struk (bill_total > 0) + cabang yang sudah punya slice hari itu (agar void total ikut terlihat)."""
    codes = {str(row[3]) for row in daily_rows if int(row[6]) > 0}
    codes |= {scope for (entity, scope, d) in ctx.checkpoints if entity == E_SALES and d == day}
    return sorted(codes)


def pull_branches(
    ctx: OmsContext,
    day: date,
    codes: Sequence[str],
    skip: Callable[[str], bool],
    gate: Callable[[], bool] | None = None,
) -> Outcome:
    """Tarik slice `oms_sales` per cabang. Error per cabang tidak menghentikan cabang lain; budget/auth menghentikan."""
    outcome = Outcome()
    for code in codes:
        if ctx.stop.is_set():
            break
        if gate is not None and not gate():
            outcome.window_closed = True
            break
        if skip(code):
            continue
        try:
            result = extract_sales_slice(
                ctx.api.post, ctx.cfg, code, ctx.branch_ids.get(code, 0), day, version_now(), ctx.oms.max_pages
            )
        except (CallBudgetExceeded, EsbAuthError):
            raise
        except OmsPageLimit:
            outcome.errors += 1
            log.error("brand=%s entity=%s day=%s status=partial (batas halaman)", ctx.cfg.key, E_SALES, day)
            continue
        except Exception as exc:  # satu cabang gagal (mis. angka rusak) -> tanpa checkpoint, cabang lain lanjut
            outcome.errors += 1
            name = type(exc).__name__
            log.error("brand=%s entity=%s day=%s status=error error=%s", ctx.cfg.key, E_SALES, day, name)
            continue
        write_oms_slice(ctx.store, ctx.cfg.key, result, datetime.now(UTC))
        outcome.slices += 1
        outcome.rows += result.rows
        outcome.errors += result.errors
        log.info(
            "brand=%s entity=%s day=%s status=done rows=%d calls=%d", ctx.cfg.key, E_SALES, day, result.rows,
            result.calls,
        )  # fmt: skip
    return outcome


def _mark_day(ctx: OmsContext, day: date, rows: int) -> None:
    task = SliceTask(day, E_DAY, BRAND_SCOPE)
    row = checkpoint_row(ctx.cfg.key, task, version_now(), rows, 0)
    ctx.store.insert_control("extract_checkpoint", CHECKPOINT_COLUMNS, [row])


def pull_day(ctx: OmsContext, day: date, final: bool, now: datetime) -> Outcome:
    by_day = _summary_slices(ctx, day, day)
    codes = _branches_for_day(ctx, day, by_day.get(day, []))
    today = wib_today(now)
    ready_at = datetime(today.year, today.month, today.day, ctx.settings.sales_ready_hour, tzinfo=WIB)

    def skip(code: str) -> bool:
        if not final:
            return False
        last = ctx.checkpoints.get((E_SALES, code, day))
        return last is not None and wib_now(last) >= ready_at

    outcome = pull_branches(ctx, day, codes, skip)
    outcome.slices += 1  # slice oms_branch_daily
    if final and not outcome.errors and not ctx.stop.is_set():
        _mark_day(ctx, day, outcome.rows)
    return outcome


def run_master(ctx: OmsContext, today: date) -> Outcome:
    """Visit purpose + payment method (snapshot harian, entitas `oms_master`)."""
    version = version_now()
    visits = [strip_oms(r) for r in oms_rows(ctx.api.post(P_VISIT_PURPOSE, {}))]
    branches = sorted(str(r.get("branchCode") or "") for r in oms_rows(ctx.api.post(P_BRANCH, {})))
    codes = [c for c in branches if c]
    if not codes:
        raise OmsParseError("get-branch: tidak ada branchCode (metode bayar butuh filterBranchCode)")
    methods = strip_oms(ctx.api.post(P_PAYMENT_METHOD, {BRANCH_CODE: codes[0]}))
    cfg = ctx.cfg
    result = OmsSlice(SliceTask(today, E_MASTER, BRAND_SCOPE), version, calls=3)
    result.tables = {
        "dim_esb_oms_visit_purpose": transform_visit_purposes(visits, cfg.key, cfg.company_code, version),
        "dim_esb_oms_payment_method": transform_payment_methods(methods, cfg.key, cfg.company_code, version),
    }
    key = today.isoformat()
    result.raw = [RawDoc(EP_VISIT, key, today, visits), RawDoc(EP_PAYMENT, key, today, methods)]
    write_oms_slice(ctx.store, cfg.key, result, datetime.now(UTC))
    return Outcome(slices=1, rows=result.rows)


def refresh_menu_fin(store: OmsStore, cfg: BrandConfig, today: date) -> int:
    """Pemetaan menu -> FIN dari resep mart G43 (tanpa panggilan ESB). Versi-insert; view = versi terakhir per brand.
    Resep kosong (snapshot bom_detail belum pernah jalan) -> tidak menulis apa pun (view tetap versi lama/kosong)."""
    params = {"brand": cfg.key}

    def query(template: str) -> list[Sequence[Any]]:
        return store.query_rows(render(template, store), params)

    headers = [
        BomHeaderRow(int(i), str(c), str(n), str(t), str(pc), str(pn), bool(a))
        for i, c, n, t, pc, pn, a in query(BOM_HEADER_SQL)
    ]
    if not headers:
        return 0
    lines = [BomLine(int(i), str(pc), str(pn), Decimal(str(q)), str(u)) for i, pc, pn, q, u in query(BOM_LINE_SQL)]
    prices = [(str(c), str(n)) for c, n in query(MENU_PRICE_SQL)]
    fin = frozenset(str(row[0]) for row in query(FIN_PRODUCT_SQL))
    version = version_now()
    rows = build_menu_fin(headers, lines, prices, fin, cfg.key, cfg.company_code, version)
    store.insert("dim_esb_oms_menu_fin", MENU_FIN_COLUMNS, rows)
    task = SliceTask(today, E_MENU_FIN, BRAND_SCOPE)
    marker = checkpoint_row(cfg.key, task, version, len(rows), 0)
    store.insert_control("extract_checkpoint", CHECKPOINT_COLUMNS, [marker])
    return len(rows)


def plan_days(
    now: datetime,
    settings: EsbSettings,
    oms: OmsSettings,
    checkpoints: Mapping[tuple[str, str, date], datetime],
) -> list[tuple[date, bool]]:
    """(hari, final). Hari berjalan (final=False) hanya di jendela intraday WIB; hari final = H-n setelah jam siap +
    gap fill (penanda `oms_day`)."""
    local = wib_now(now)
    out: list[tuple[date, bool]] = []
    low, high = oms.intraday_hours
    if low <= local.hour < high:
        out.append((local.date(), False))
    finished = {day: ts for (entity, scope, day), ts in checkpoints.items() if entity == E_DAY and scope == BRAND_SCOPE}
    for day in incremental_days(now, finished, oms.correction_days, settings.gap_fill_days, settings.sales_ready_hour):
        out.append((day, True))
    return out


def oms_cycle(
    settings: EsbSettings,
    oms: OmsSettings,
    cfg: BrandConfig,
    api: OmsApi,
    store: OmsStore,
    now: datetime,
    stop: threading.Event,
) -> RunRecord:
    started = time.monotonic()
    run = RunRecord(cfg.key, "oms", datetime.now(UTC), uuid.uuid4())
    outcome = Outcome()
    api.reset_budget(oms.max_calls_per_run)
    try:
        today = wib_today(now)
        lookback = max(max(oms.correction_days), settings.gap_fill_days) + 1
        ctx = OmsContext(settings, oms, cfg, api, store, stop)
        ctx.checkpoints = store.checkpoints(cfg.key, today - timedelta(days=lookback))
        ctx.branch_ids = load_branch_ids(store, cfg.key)
        if (E_MASTER, BRAND_SCOPE, today) not in ctx.checkpoints:
            outcome.merge(run_master(ctx, today))
        if (E_MENU_FIN, BRAND_SCOPE, today) not in ctx.checkpoints:
            try:
                refresh_menu_fin(store, cfg, today)
            except Exception as exc:  # resep/ClickHouse: dicatat, penjualan tetap ditarik
                outcome.errors += 1
                log.error("brand=%s entity=%s status=error error=%s", cfg.key, E_MENU_FIN, type(exc).__name__)
        for day, final in plan_days(now, settings, oms, ctx.checkpoints):
            if stop.is_set():
                break
            outcome.merge(pull_day(ctx, day, final, now))
    except CallBudgetExceeded:
        outcome.budget = True
    except EsbAuthError:
        outcome.auth = True
        outcome.errors += 1
        log.error("brand=%s entity=oms status=auth_failed", cfg.key)
    except Exception as exc:  # brand OMS gagal tidak menghentikan loop OMS maupun Core
        log.error("brand=%s oms gagal error=%s", cfg.key, type(exc).__name__)
        run.status = "error"
    return _finish(run, api.calls, outcome, store, started)  # type: ignore[arg-type]


def run_oms_backfill(
    settings: EsbSettings,
    oms: OmsSettings,
    cfg: BrandConfig,
    api: OmsApi,
    store: OmsStore,
    start: date,
    end: date,
    stop: threading.Event,
    *,
    force: bool = False,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunRecord:
    """Backfill OMS resumable satu brand, hari terbaru dulu, hanya di `ESB_BACKFILL_WINDOW` (berhenti di batas cabang
    berikutnya bila jendela tutup). Slice `oms_sales` yang sudah done dilewati kecuali `force`."""
    started = time.monotonic()
    run = RunRecord(cfg.key, "oms_backfill", datetime.now(UTC), uuid.uuid4())
    outcome = Outcome()
    api.reset_budget(oms.backfill_max_calls)

    def gate() -> bool:
        return in_window(now_fn(), settings.backfill_window)

    try:
        if not gate():
            log.warning("brand=%s oms-backfill di luar ESB_BACKFILL_WINDOW; tidak ada panggilan", cfg.key)
            outcome.window_closed = True
            return _finish(run, api.calls, outcome, store, started)  # type: ignore[arg-type]
        ctx = OmsContext(settings, oms, cfg, api, store, stop)
        ctx.checkpoints = store.checkpoints(cfg.key, start)
        ctx.branch_ids = load_branch_ids(store, cfg.key)
        days = sorted(date_range(start, end), reverse=True)
        for index in range(0, len(days), SUMMARY_CHUNK_DAYS):
            chunk = days[index : index + SUMMARY_CHUNK_DAYS]
            if stop.is_set() or outcome.window_closed:
                break
            if not gate():
                outcome.window_closed = True
                break
            by_day = _summary_slices(ctx, min(chunk), max(chunk))
            outcome.slices += len(chunk)
            for day in chunk:

                def skip(code: str, day: date = day) -> bool:
                    return not force and (E_SALES, code, day) in ctx.checkpoints

                part = pull_branches(ctx, day, _branches_for_day(ctx, day, by_day.get(day, [])), skip, gate)
                outcome.merge(part)
                if part.window_closed or stop.is_set():
                    break
                if not part.errors:
                    _mark_day(ctx, day, part.rows)
    except CallBudgetExceeded:
        outcome.budget = True
    except EsbAuthError:
        outcome.auth = True
        outcome.errors += 1
        log.error("brand=%s entity=oms status=auth_failed", cfg.key)
    except Exception as exc:
        log.error("brand=%s oms-backfill gagal error=%s", cfg.key, type(exc).__name__)
        run.status = "error"
    return _finish(run, api.calls, outcome, store, started)  # type: ignore[arg-type]


def oms_loop(
    settings: EsbSettings,
    oms: OmsSettings,
    stop: threading.Event,
    store_factory: Callable[[], OmsStore],
    api_factory: Callable[[BrandConfig], OmsApi],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Loop OMS (thread terpisah). Tidak pernah melempar: semua exception dicatat lalu dicoba di interval berikutnya."""
    try:
        store = store_factory()
        brands = [settings.brand(key) for key in oms.brands]
        apis = {cfg.key: api_factory(cfg) for cfg in brands}
    except Exception as exc:
        log.error("oms loop tidak bisa start error=%s", type(exc).__name__)
        return
    breaker = AuthBreaker()  # SEC-186 untuk OMS (Basic auth gagal berulang bisa mengunci akun ESB)
    warned: set[str] = set()
    log.info("esb-extract oms: brands=%s interval=%ds", list(apis), oms.interval_seconds)
    while not stop.is_set():
        for cfg in brands:
            if stop.is_set():
                break
            if breaker.suspended(cfg.key):
                if cfg.key not in warned:
                    warned.add(cfg.key)
                    log.warning("brand=%s oms status=auth_suspended sampai proses di-restart", cfg.key)
                continue
            try:
                run = oms_cycle(settings, oms, cfg, apis[cfg.key], store, clock(), stop)
                breaker.record(cfg.key, run.status)
            except Exception as exc:  # pertahanan berlapis
                log.error("brand=%s oms siklus gagal error=%s", cfg.key, type(exc).__name__)
        if settings.run_once:
            break
        stop.wait(oms.interval_seconds)
