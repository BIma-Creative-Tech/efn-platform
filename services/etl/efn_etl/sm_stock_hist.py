"""G51 B6 (docs/design/G51_SMD_E2E_LEAD_SPEC.md §B6): rekonstruksi stok harian historis per warehouse SM x produk.

Tujuan: GMROI dan "stok > 30 hari" butuh nilai persediaan harian, padahal snapshot stok (efn_etl/stock.py) baru ada
sejak job stok aktif. Job `sm_stock_reconstruct` (flag `ETL_SM_STOCK_RECONSTRUCT_DAYS`, default 0 = mati) menghitung
mundur dari quant SAAT INI dengan stock_move `done` (Odoo replica, PG), lalu menulis `agg_sm_stock_daily` untuk hari
yang TIDAK punya snapshot nyata (`stock_snapshot_run`). Sekali per hari bisnis WIB (idempoten: ReplacingMergeTree).

Aturan versi (keputusan lead G51): baris rekonstruksi ditulis dengan `computed_at = RECON_VERSION` (2000-01-01 UTC),
lebih tua dari baris nyata mana pun (`now64(6)` di SQL_STOCK_AGG) -> untuk kunci yang sama baris nyata SELALU menang,
apa pun urutan insert/merge; rekonstruksi ulang (versi sama) menggantikan rekonstruksi sebelumnya (RMT menyimpan insert
terakhir untuk versi sama). `is_reconstructed = 1` menandai baris ini. Hari yang punya snapshot nyata tidak ditulis.

Model per warehouse W (lokasi `usage = 'internal'` dengan `warehouse_id = W`, sama dengan snapshot):
- stok akhir hari d = Σ quant sekarang − Σ net move (masuk − keluar W) bertanggal WIB > d; move antar lokasi di dalam
  W diabaikan; qty dikonversi ke UoM produk (pola SQL_FILL_MOVES);
- nilai = qty x standard_price PT SAAT INI (jsonb per company, pola stock.py);
- umur stok = FIFO atas penerimaan (move masuk W) mundur dari hari d; sisa yang tidak tertutup jendela (91 hari sebelum
  hari pertama) -> bucket > 90 hari;
- tracked/min/max = orderpoint snapshot TERAKHIR; qty_sold_28d = POS (fact_sales_line) 28 hari sebelum d.

Keterbatasan (didokumentasikan, R-T22): tanpa lot -> semua nilai masuk `value_no_lot`, bucket kedaluwarsa 0; free_qty =
on_hand (reservasi historis tidak diketahui); konsinyasi (owner_id) tidak dibedakan; revaluasi/perubahan HPP historis
tidak diketahui (HPP sekarang); penjualan POS tanpa picking tidak mengurangi stok (seed A7 membuat picking keluar POS);
stok rekonstruksi negatif (data move tidak lengkap) ditulis 0 dan dihitung di log.
PDP: tanpa partner/pelaku; owner_id quant tidak dibaca.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Protocol

from efn_etl.sm import company_filter, wib_date
from efn_etl.stock import JAKARTA
from efn_etl.watermark import to_utc

JOB_STOCK_RECONSTRUCT: Final = "sm_stock_reconstruct"
RECON_VERSION: Final = datetime(2000, 1, 1, tzinfo=UTC)
AGE_WINDOW_DAYS: Final = 91
SALES_WINDOW_DAYS: Final = 28
MAX_DAYS: Final = 400
Q2: Final = Decimal("0.01")
Q3: Final = Decimal("0.001")
ZERO: Final = Decimal(0)

AGG_COLUMNS: Final = (
    "day",
    "snapshot_ts",
    "company_id",
    "warehouse_id",
    "product_id",
    "categ_id",
    "tracked",
    "sold_28d",
    "qty_sold_28d",
    "on_hand_qty",
    "free_qty",
    "min_qty",
    "max_qty",
    "stock_value_cost",
    "value_age_0_30",
    "value_age_31_60",
    "value_age_61_90",
    "value_age_91p",
    "value_exp_expired",
    "value_exp_0_7",
    "value_exp_8_30",
    "value_exp_31_90",
    "value_exp_91p",
    "value_no_lot",
    "computed_at",
    "is_reconstructed",
)


# ---------------------------------------------------------------- SQL Odoo (PG replica)
def sql_recon_warehouses(codes: Sequence[str]) -> str:
    return f"""
SELECT w.id AS warehouse_id, w.company_id
FROM stock_warehouse w
JOIN res_company c ON c.id = w.company_id
WHERE {company_filter("c", codes)} AND COALESCE(w.active, FALSE)
ORDER BY w.id
"""  # noqa: S608 - kode PT tervalidasi (company_filter)


# Satu statement (satu snapshot MVCC) per warehouse: baris 'move' = net harian per produk (masuk/keluar W) sejak
# %(since)s, baris 'quant' = stok sekarang. Hari = tanggal WIB (UTC+7 tetap, sama dengan efn_etl.stock.JAKARTA).
SQL_RECON_STOCK: Final = """
WITH loc AS (
    SELECT l.id FROM stock_location l WHERE l.usage = 'internal' AND l.warehouse_id = %(warehouse_id)s
),
mv AS (
    SELECT m.product_id, (m.date + INTERVAL '7 hours')::date AS day, m.quantity * mu.factor / pu.factor AS qty,
           (m.location_dest_id IN (SELECT id FROM loc)) AS into_wh,
           (m.location_id IN (SELECT id FROM loc)) AS from_wh
    FROM stock_move m
    JOIN uom_uom mu ON mu.id = m.product_uom
    JOIN product_product pp ON pp.id = m.product_id
    JOIN product_template pt ON pt.id = pp.product_tmpl_id
    JOIN uom_uom pu ON pu.id = pt.uom_id
    WHERE m.state = 'done' AND m.date >= %(since)s
      AND (m.location_dest_id IN (SELECT id FROM loc) OR m.location_id IN (SELECT id FROM loc))
)
SELECT 'move' AS kind, mv.product_id, mv.day,
       SUM(CASE WHEN mv.into_wh AND NOT mv.from_wh THEN mv.qty ELSE 0 END) AS qty_in,
       SUM(CASE WHEN mv.from_wh AND NOT mv.into_wh THEN mv.qty ELSE 0 END) AS qty_out
FROM mv
GROUP BY mv.product_id, mv.day
UNION ALL
SELECT 'quant' AS kind, q.product_id, NULL::date AS day, SUM(q.quantity) AS qty_in, 0 AS qty_out
FROM stock_quant q
WHERE q.location_id IN (SELECT id FROM loc)
GROUP BY q.product_id
"""

SQL_RECON_COST: Final = """
SELECT pp.id AS product_id, (pp.standard_price ->> %(company_key)s) AS unit_cost
FROM product_product pp
WHERE pp.id = ANY(%(ids)s)
"""

# ---------------------------------------------------------------- SQL ClickHouse (user efn_etl)
SQL_REAL_DAYS: Final = """
SELECT DISTINCT snapshot_date FROM efn_dwh.stock_snapshot_run WHERE snapshot_date >= {since:Date}
"""
SQL_LATEST_ORDERPOINTS: Final = """
SELECT warehouse_id AS wh, product_id AS pid, sum(min_qty) AS min_q, sum(max_qty) AS max_q
FROM efn_dwh.fact_orderpoint_snapshot
WHERE snapshot_ts = (SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run) AND warehouse_id != 0
GROUP BY wh, pid
"""
SQL_PRODUCT_CATEG: Final = """
SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL WHERE product_id IN {ids:Array(UInt32)}
"""
SQL_POS_SALES_DAILY: Final = """
SELECT o.warehouse_id AS wh, f.product_id AS pid, toDate(f.order_date) AS d, toFloat64(sum(f.qty)) AS q
FROM
(
    SELECT store_id, product_id, order_date, qty
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos' AND line_kind = 'product' AND is_refund = 0
      AND toDate(order_date) >= {start:Date} AND toDate(order_date) < {end:Date}
      AND company_id IN (SELECT company_id FROM efn_dwh.dim_sm_warehouse FINAL)
) AS f
INNER JOIN
(
    SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id
    FROM efn_dwh.dim_outlet FINAL WHERE isNotNull(warehouse_id)
) AS o ON o.store_id = f.store_id
GROUP BY wh, pid, d
"""


# ---------------------------------------------------------------- logika murni
def validate_days(days: int) -> int:
    if not 0 <= days <= MAX_DAYS:
        raise ValueError(f"ETL_SM_STOCK_RECONSTRUCT_DAYS di luar rentang 0..{MAX_DAYS}")
    return days


def recon_due(last_run: datetime | None, now: datetime) -> bool:
    """Sekali per hari bisnis WIB."""
    return last_run is None or wib_date(last_run) != wib_date(now)


def target_days(today: date, days: int, real_days: Iterable[date]) -> list[date]:
    """[today − days, today − 1] tanpa hari yang punya snapshot nyata (urut naik).

    Hari ini tidak pernah direkonstruksi (snapshot nyata hari ini menyusul dari job stok)."""
    real = set(real_days)
    return [today - timedelta(days=i) for i in range(days, 0, -1) if today - timedelta(days=i) not in real]


def moves_since(first_day: date) -> datetime:
    """Awal jendela move (UTC naive, seperti kolom Odoo): 00:00 WIB hari (first_day − 91)."""
    start = datetime.combine(first_day - timedelta(days=AGE_WINDOW_DAYS), time(0, 0), JAKARTA)
    return start.astimezone(UTC).replace(tzinfo=None)


def eod_snapshot_ts(day: date) -> datetime:
    """snapshot_ts penanda baris rekonstruksi = 23:59:59 WIB hari itu (UTC)."""
    return datetime.combine(day, time(23, 59, 59), JAKARTA).astimezone(UTC)


def end_of_day_stock(current: Decimal, net_by_day: Mapping[date, Decimal], days: Sequence[date]) -> dict[date, Decimal]:
    """Stok akhir hari d = current − Σ net (masuk − keluar) pada hari > d (termasuk hari ini)."""
    flow_days = sorted(net_by_day, reverse=True)
    out: dict[date, Decimal] = {}
    acc = ZERO
    i = 0
    for d in sorted(days, reverse=True):
        while i < len(flow_days) and flow_days[i] > d:
            acc += net_by_day[flow_days[i]]
            i += 1
        out[d] = current - acc
    return out


def age_bucket(age_days: int) -> int:
    """Sama dengan SQL_STOCK_AGG: <=30, 31..60, 61..90, >90."""
    if age_days <= 30:
        return 0
    if age_days <= 60:
        return 1
    if age_days <= 90:
        return 2
    return 3


def fifo_age_split(stock: Decimal, inflows_desc: Sequence[tuple[date, Decimal]], day: date) -> list[Decimal]:
    """Bagi `stock` ke 4 bucket umur dengan asumsi FIFO: stok tersisa = penerimaan TERBARU s.d. hari `day`.
    `inflows_desc` = (tanggal, qty masuk) urut turun. Sisa tak tertutup -> bucket > 90 hari."""
    buckets = [ZERO, ZERO, ZERO, ZERO]
    remaining = stock
    if remaining <= 0:
        return buckets
    for d_in, qty in inflows_desc:
        if d_in > day or qty <= 0:
            continue
        take = min(qty, remaining)
        buckets[age_bucket((day - d_in).days)] += take
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        buckets[3] += remaining
    return buckets


def sold_window(daily: Mapping[date, float], day: date, window: int = SALES_WINDOW_DAYS) -> float:
    """Σ qty POS pada [day − window, day) (sama dengan SQL_STOCK_AGG)."""
    return sum(q for d, q in daily.items() if day - timedelta(days=window) <= d < day)


def _dec(value: Any) -> Decimal:
    try:
        number = Decimal(str(value if value is not None else 0))
    except InvalidOperation:
        return ZERO
    return number if number.is_finite() else ZERO


def unit_cost(value: Any) -> Decimal | None:
    """standard_price jsonb per company -> Decimal; kosong/aneh = None (nilai 0, pola SQL ifNull(unit_cost, 0))."""
    if value is None or value == "":
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() else None


@dataclass(frozen=True)
class WarehouseData:
    current: dict[int, Decimal]
    net: dict[int, dict[date, Decimal]]
    inflows_desc: dict[int, list[tuple[date, Decimal]]]


def parse_stock_rows(rows: Iterable[Mapping[str, Any]]) -> WarehouseData:
    current: dict[int, Decimal] = defaultdict(lambda: ZERO)
    net: dict[int, dict[date, Decimal]] = defaultdict(dict)
    inflows: dict[int, list[tuple[date, Decimal]]] = defaultdict(list)
    for r in rows:
        pid = int(r["product_id"])
        if r["kind"] == "quant":
            current[pid] += _dec(r["qty_in"])
            continue
        day = r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"]))
        q_in, q_out = _dec(r["qty_in"]), _dec(r["qty_out"])
        net[pid][day] = net[pid].get(day, ZERO) + q_in - q_out
        if q_in > 0:
            inflows[pid].append((day, q_in))
    for pid in inflows:
        inflows[pid].sort(reverse=True)
    return WarehouseData(dict(current), dict(net), dict(inflows))


@dataclass(frozen=True)
class BuildResult:
    rows: list[list[Any]]
    negative: int


def build_rows(
    company_id: int,
    warehouse_id: int,
    data: WarehouseData,
    days: Sequence[date],
    costs: Mapping[int, Decimal | None],
    categ: Mapping[int, int],
    orderpoints: Mapping[int, tuple[Decimal, Decimal]],
    sales: Mapping[int, Mapping[date, float]],
) -> BuildResult:
    """Baris agg_sm_stock_daily (urutan AGG_COLUMNS) untuk produk yang punya stok > 0 atau orderpoint."""
    rows: list[list[Any]] = []
    negative = 0
    products = sorted(set(data.current) | set(data.net) | set(orderpoints))
    for pid in products:
        stock_by_day = end_of_day_stock(data.current.get(pid, ZERO), data.net.get(pid, {}), days)
        cost = costs.get(pid) or ZERO
        op = orderpoints.get(pid)
        inflows = data.inflows_desc.get(pid, [])
        for day in sorted(days):
            raw = stock_by_day[day]
            if raw < 0:
                negative += 1
            on_hand = max(raw, ZERO).quantize(Q3)
            if on_hand == 0 and op is None:
                continue
            buckets = [(b * cost).quantize(Q2) for b in fifo_age_split(on_hand, inflows, day)]
            value = (on_hand * cost).quantize(Q2)
            sold = sold_window(sales.get(pid, {}), day)
            rows.append(
                [
                    day,
                    eod_snapshot_ts(day),
                    company_id,
                    warehouse_id,
                    pid,
                    int(categ.get(pid, 0)),
                    1 if op is not None else 0,
                    1 if sold > 0 else 0,
                    Decimal(str(sold)).quantize(Q3),
                    on_hand,
                    on_hand,  # free_qty: reservasi historis tidak diketahui
                    (op[0] if op else ZERO).quantize(Q3),
                    (op[1] if op else ZERO).quantize(Q3),
                    value,
                    *buckets,
                    ZERO.quantize(Q2),  # value_exp_expired (lot tidak direkonstruksi)
                    ZERO.quantize(Q2),
                    ZERO.quantize(Q2),
                    ZERO.quantize(Q2),
                    ZERO.quantize(Q2),
                    value,  # value_no_lot
                    RECON_VERSION,
                    1,
                ]
            )
    return BuildResult(rows, negative)


# ---------------------------------------------------------------- I/O (protokol, diinjeksi)
class PgQuery(Protocol):
    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class ChStore(Protocol):
    def rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[tuple[Any, ...]]: ...


class Sink(Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


class WatermarkStore(Protocol):
    def get(self, job: str) -> datetime | None: ...

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None: ...


@dataclass(frozen=True)
class ReconResult:
    days: int
    rows: int
    negative: int
    skipped: bool = False


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def run_sm_stock_reconstruct(
    odoo: PgQuery,
    ch: ChStore,
    sink: Sink,
    store: WatermarkStore,
    codes: Sequence[str],
    now: datetime,
    days: int,
) -> ReconResult:
    validate_days(days)
    if days == 0 or not recon_due(store.get(JOB_STOCK_RECONSTRUCT), now):
        return ReconResult(0, 0, 0, skipped=True)
    today = wib_date(now)
    first = today - timedelta(days=days)
    real = [_as_date(r[0]) for r in ch.rows(SQL_REAL_DAYS, {"since": first})]
    targets = target_days(today, days, real)
    total = negative = 0
    if targets:
        since = moves_since(targets[0])
        orderpoints: dict[int, dict[int, tuple[Decimal, Decimal]]] = defaultdict(dict)
        for wh, pid, lo, hi in ch.rows(SQL_LATEST_ORDERPOINTS):
            orderpoints[int(wh)][int(pid)] = (_dec(lo), _dec(hi))
        sales: dict[int, dict[int, dict[date, float]]] = defaultdict(lambda: defaultdict(dict))
        start = targets[0] - timedelta(days=SALES_WINDOW_DAYS)
        for wh, pid, d, q in ch.rows(SQL_POS_SALES_DAILY, {"start": start, "end": today}):
            sales[int(wh)][int(pid)][_as_date(d)] = float(q or 0)
        for w in odoo.query(sql_recon_warehouses(codes), {}):
            wh, company = int(w["warehouse_id"]), int(w["company_id"])
            data = parse_stock_rows(odoo.query(SQL_RECON_STOCK, {"warehouse_id": wh, "since": since}))
            ids = sorted(set(data.current) | set(data.net) | set(orderpoints.get(wh, {})))
            if not ids:
                continue
            costs = {
                int(r["product_id"]): unit_cost(r["unit_cost"])
                for r in odoo.query(SQL_RECON_COST, {"ids": ids, "company_key": str(company)})
            }
            categ = {int(p): int(c or 0) for p, c in ch.rows(SQL_PRODUCT_CATEG, {"ids": ids})}
            built = build_rows(company, wh, data, targets, costs, categ, orderpoints.get(wh, {}), sales.get(wh, {}))
            sink.insert("agg_sm_stock_daily", AGG_COLUMNS, built.rows)
            total += len(built.rows)
            negative += built.negative
    store.set(JOB_STOCK_RECONSTRUCT, to_utc(now), total)
    return ReconResult(len(targets), total, negative)
