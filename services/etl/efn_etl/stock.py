"""Gelombang 13 (Fase 1): snapshot stok & kedaluwarsa dari Odoo -> ClickHouse.

Mengapa snapshot penuh (bukan incremental write_date): Odoo MENGHAPUS `stock_quant` yang kuantitasnya nol
(`_quant_tasks`), sehingga incremental tidak pernah melihat stok yang habis. Volume quant lokasi internal untuk ritel
pilot = ribuan baris, jadi snapshot penuh per jam murah.

Model:
- Satu snapshot = semua quant lokasi `usage='internal'` + semua orderpoint aktif, dibaca dalam SATU transaksi
  `REPEATABLE READ READ ONLY` (konsisten antar-halaman), keyset `id`.
- Baris ditulis ke `fact_stock_snapshot` / `fact_orderpoint_snapshot` dengan `snapshot_ts` yang sama; SETELAH semua
  halaman sukses, satu baris penanda ditulis ke `stock_snapshot_run`. View hanya membaca snapshot yang punya penanda
  (snapshot setengah jadi akibat crash tidak pernah tampil).
- Mart harian = snapshot terakhir per tanggal (Asia/Jakarta). `ReplacingMergeTree(snapshot_ts)` merapatkan snapshot
  per jam dalam satu hari menjadi satu versi per quant; quant yang terhapus di tengah hari tersaring oleh penanda.

Kolom Odoo CE 19.0 (diverifikasi DB dev, 7 Okt 2026): `stock_quant` (id, company_id, location_id, product_id, lot_id,
quantity, reserved_quantity, in_date), `stock_lot` (id, name, expiration_date — modul product_expiry), `stock_location`
(id, complete_name, usage, warehouse_id), `stock_warehouse` (id, name), `stock_warehouse_orderpoint` (id, company_id,
warehouse_id, location_id, product_id, product_min_qty, product_max_qty, trigger, active),
`product_product.standard_price` (jsonb per company). `stock_quant.owner_id` (partner pemilik konsinyasi) dan
`user_id` TIDAK dibaca.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from efn_etl.watermark import to_utc

# WIB = UTC+7 tetap (Indonesia tanpa DST); tidak bergantung paket tzdata di image slim.
JAKARTA = timezone(timedelta(hours=7), "WIB")
STOCK_JOB = "stock_snapshot"

SQL_STOCK_QUANTS = """
SELECT q.id AS quant_id, q.company_id, COALESCE(c.name, '') AS company_name,
       COALESCE(loc.warehouse_id, 0) AS warehouse_id, COALESCE(wh.name, '') AS warehouse_name,
       q.location_id, COALESCE(loc.complete_name, '') AS location_name, q.product_id,
       COALESCE(q.lot_id, 0) AS lot_id, COALESCE(lot.name, '') AS lot_name, lot.expiration_date,
       q.quantity, q.reserved_quantity, (pp.standard_price ->> q.company_id::text) AS unit_cost, q.in_date
FROM stock_quant q
JOIN stock_location loc ON loc.id = q.location_id
JOIN product_product pp ON pp.id = q.product_id
LEFT JOIN res_company c ON c.id = q.company_id
LEFT JOIN stock_warehouse wh ON wh.id = loc.warehouse_id
LEFT JOIN stock_lot lot ON lot.id = q.lot_id
WHERE loc.usage = 'internal'
  AND q.id > %(after_id)s
ORDER BY q.id
LIMIT %(limit)s
"""
STOCK_COLUMNS = (
    "snapshot_date",
    "snapshot_ts",
    "quant_id",
    "company_id",
    "company_name",
    "warehouse_id",
    "warehouse_name",
    "location_id",
    "location_name",
    "product_id",
    "lot_id",
    "lot_name",
    "expiration_date",
    "quantity",
    "reserved_quantity",
    "unit_cost",
    "in_date",
)

SQL_ORDERPOINTS = """
SELECT op.id AS orderpoint_id, op.company_id, COALESCE(c.name, '') AS company_name,
       COALESCE(op.warehouse_id, 0) AS warehouse_id, COALESCE(wh.name, '') AS warehouse_name, op.location_id,
       op.product_id, op.product_min_qty, op.product_max_qty, COALESCE(op.trigger, '') AS trigger
FROM stock_warehouse_orderpoint op
LEFT JOIN res_company c ON c.id = op.company_id
LEFT JOIN stock_warehouse wh ON wh.id = op.warehouse_id
WHERE op.active
  AND op.id > %(after_id)s
ORDER BY op.id
LIMIT %(limit)s
"""
ORDERPOINT_COLUMNS = (
    "snapshot_date",
    "snapshot_ts",
    "orderpoint_id",
    "company_id",
    "company_name",
    "warehouse_id",
    "warehouse_name",
    "location_id",
    "product_id",
    "min_qty",
    "max_qty",
    "trigger",
)
RUN_COLUMNS = ("snapshot_date", "snapshot_ts", "quants", "orderpoints")


class SnapshotIncomplete(RuntimeError):
    """Batas halaman tercapai: snapshot tidak ditandai selesai (view tidak menampilkannya)."""


class QueryTx(Protocol):
    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class Sink(Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


@dataclass(frozen=True)
class SnapshotResult:
    snapshot_ts: datetime
    quants: int
    orderpoints: int
    pages: int


def snapshot_date(ts: datetime) -> date:
    """Tanggal bisnis (Asia/Jakarta) sebuah snapshot."""
    return to_utc(ts).astimezone(JAKARTA).date()


def snapshot_due(last_ok: datetime | None, now: datetime, interval: timedelta) -> bool:
    """Snapshot berikutnya jatuh tempo bila belum pernah ada, sudah lewat interval, atau tanggal Jakarta berganti."""
    if last_ok is None:
        return True
    if snapshot_date(last_ok) != snapshot_date(now):
        return True
    return to_utc(now) - to_utc(last_ok) >= interval


def _dec(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _cost(value: Any) -> Decimal | None:
    """`standard_price` jsonb per company -> teks; nilai kosong/aneh = None (nilai stok tidak dihitung)."""
    if value is None or value == "":
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() else None


def _opt_dt(value: Any) -> datetime | None:
    return to_utc(value) if isinstance(value, datetime) else None


def transform_quant(row: dict[str, Any], ts: datetime) -> list[Any]:
    return [
        snapshot_date(ts),
        ts,
        int(row["quant_id"]),
        int(row["company_id"] or 0),
        str(row["company_name"] or ""),
        int(row["warehouse_id"] or 0),
        str(row["warehouse_name"] or ""),
        int(row["location_id"]),
        str(row["location_name"] or ""),
        int(row["product_id"]),
        int(row["lot_id"] or 0),
        str(row["lot_name"] or ""),
        _opt_dt(row["expiration_date"]),
        _dec(row["quantity"]),
        _dec(row["reserved_quantity"]),
        _cost(row["unit_cost"]),
        _opt_dt(row["in_date"]),
    ]


def transform_orderpoint(row: dict[str, Any], ts: datetime) -> list[Any]:
    return [
        snapshot_date(ts),
        ts,
        int(row["orderpoint_id"]),
        int(row["company_id"] or 0),
        str(row["company_name"] or ""),
        int(row["warehouse_id"] or 0),
        str(row["warehouse_name"] or ""),
        int(row["location_id"] or 0),
        int(row["product_id"]),
        _dec(row["product_min_qty"]),
        _dec(row["product_max_qty"]),
        str(row["trigger"] or ""),
    ]


def _copy(
    tx: QueryTx,
    sink: Sink,
    sql: str,
    key: str,
    table: str,
    columns: Sequence[str],
    transform: Callable[[dict[str, Any]], list[Any]],
    batch_size: int,
    max_pages: int,
) -> tuple[int, int]:
    after = 0
    total = 0
    pages = 0
    while True:
        if pages >= max_pages:
            raise SnapshotIncomplete(f"{table}: batas {max_pages} halaman tercapai")
        rows = tx.query(sql, {"after_id": after, "limit": batch_size})
        if not rows:
            break
        sink.insert(table, columns, [transform(r) for r in rows])
        after = int(rows[-1][key])
        total += len(rows)
        pages += 1
        if len(rows) < batch_size:
            break
    return total, pages


def run_stock_snapshot(tx: QueryTx, sink: Sink, ts: datetime, batch_size: int, max_pages: int) -> SnapshotResult:
    """Salin quant + orderpoint dalam transaksi `tx` (konsisten), lalu tulis penanda selesai (terakhir)."""
    ts = to_utc(ts)
    quants, quant_pages = _copy(
        tx,
        sink,
        SQL_STOCK_QUANTS,
        "quant_id",
        "fact_stock_snapshot",
        STOCK_COLUMNS,
        lambda r: transform_quant(r, ts),
        batch_size,
        max_pages,
    )
    orderpoints, op_pages = _copy(
        tx,
        sink,
        SQL_ORDERPOINTS,
        "orderpoint_id",
        "fact_orderpoint_snapshot",
        ORDERPOINT_COLUMNS,
        lambda r: transform_orderpoint(r, ts),
        batch_size,
        max_pages,
    )
    sink.insert("stock_snapshot_run", RUN_COLUMNS, [[snapshot_date(ts), ts, quants, orderpoints]])
    return SnapshotResult(snapshot_ts=ts, quants=quants, orderpoints=orderpoints, pages=quant_pages + op_pages)
