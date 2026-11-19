"""Akses ClickHouse untuk mesin migrasi (I/O). User `efn_mig` (role mig_rw), HTTPS 8443 + CA internal.

Semua query berparameter; nama tabel hanya dari konstanta di modul ini. Tidak ada `SELECT *`: kolom eksplisit, filter
brand/company/rentang tanggal. Histori dibaca streaming (`query_row_block_stream`) -> memori konstan.
Dimensi = snapshot terakhir per brand (`_version = max(_version)` brand itu): baris yang hilang di ESB tidak ikut.
"""

from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from typing import Any, Protocol

from efn_mig.settings import ClickHouseSettings

DIM_COLUMNS: dict[str, tuple[str, ...]] = {
    "dim_esb_branch": ("branch_id", "company_code", "branch_code", "branch_name", "is_ck", "is_store", "active"),
    "dim_esb_product": ("product_code", "company_code", "product_name", "category", "uom", "is_fin", "active",
                        "batch_to_ea_ratio"),
    "dim_esb_menu": ("menu_code", "menu_name", "menu_category", "menu_category_detail", "is_takeaway"),
    "dim_esb_supplier": ("supplier_code", "supplier_name", "active"),
    "dim_esb_menu_price": ("menu_code", "menu_name", "price", "bom_name", "active"),
    "dim_esb_customer": ("customer_code", "customer_name", "customer_category", "payment_due_days", "active"),
    "dim_esb_bom": ("bom_id", "bom_code", "bom_name", "bom_type_id", "bom_type_name", "product_code", "product_name",
                    "uom", "active"),
    "dim_esb_bom_line": ("bom_id", "line_id", "product_code", "product_name", "qty", "uom", "last_hpp",
                         "yield_percent"),
}  # fmt: skip
HISTORY_COLUMNS: dict[str, tuple[str, ...]] = {
    "legacy_flow_daily": ("day", "branch_id", "product_code", "product_name", "sold", "ck_prod", "store_prod",
                          "ck_out", "in_ck", "in_other", "st_in", "st_out"),
    "legacy_sales_daily": ("sales_date", "branch_id", "menu_code", "menu_name", "menu_category", "is_takeaway", "qty",
                           "gross", "discount", "cogs", "trx_count"),
    "legacy_sales_line": ("sales_date", "branch_id", "sales_num", "line_no", "menu_code", "menu_name", "qty", "price",
                          "total",
                          "discount", "cogs", "row_type", "is_takeaway"),
}  # fmt: skip
# (sumber, kolom tanggal). sales_line: versi slice ber-checkpoint (sama dengan v_esb_sales_daily).
_HISTORY_SOURCE = {
    "legacy_flow_daily": ("efn_dwh.v_esb_flow_daily", "day", ""),
    "legacy_sales_daily": ("efn_dwh.v_esb_sales_daily", "sales_date", ""),
    "legacy_sales_line": (
        "efn_dwh.fact_esb_sales_line FINAL",
        "sales_date",
        " AND (brand, toString(branch_id), sales_date, _version) IN (SELECT brand, scope_key, day, finished_at"
        " FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'sales' AND brand = {brand:String})",
    ),
}
# Urut HARI dulu: `mapping.aggregate_rows` menjumlahkan beberapa cabang -> satu gudang tujuan per hari (SEC-197).
HISTORY_ORDER = _HISTORY_ORDER = {
    "legacy_flow_daily": "day, branch_id, product_code",
    "legacy_sales_daily": "sales_date, branch_id, menu_code, is_takeaway",
    "legacy_sales_line": "branch_id, sales_date, sales_num, line_no",
}
MAP_COLUMNS = (
    "brand", "company_code", "entity", "esb_key", "odoo_model", "odoo_xmlid", "odoo_id", "run_id", "_version",
)  # fmt: skip


class Source(Protocol):
    def companies(self, brand: str) -> list[str]: ...
    def dim(self, table: str, brand: str) -> list[dict[str, Any]]: ...
    def bom_ratios(self, brand: str) -> dict[str, float]: ...
    def archive_menus(self, brand: str, start: date, end: date) -> list[dict[str, Any]]: ...
    def snapshot(self, brand: str) -> dict[str, str]: ...
    def history_counts(self, brand: str, company: str, file: str, start: date, end: date) -> dict[int, int]: ...
    def history_rows(
        self, brand: str, company: str, file: str, start: date, end: date, branch_ids: Sequence[int]
    ) -> Iterator[dict[str, Any]]: ...


def client_kwargs(settings: ClickHouseSettings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "username": settings.user,
        "password": settings.password,
        "database": "efn_dwh",
        "secure": settings.secure,
        "connect_timeout": 10,
        "send_receive_timeout": 600,
    }
    if settings.secure:
        if not settings.ca_file:
            raise ValueError("ClickHouse TLS membutuhkan CH_CA_FILE")
        kwargs.update(verify=True, ca_cert=settings.ca_file)
    return kwargs


class ClickHouseSource:
    def __init__(self, client: Any) -> None:
        self._c = client

    @classmethod
    def connect(cls, settings: ClickHouseSettings) -> "ClickHouseSource":
        import clickhouse_connect

        return cls(clickhouse_connect.get_client(**client_kwargs(settings)))

    def _rows(self, sql: str, params: Mapping[str, Any], columns: Sequence[str]) -> list[dict[str, Any]]:
        result = self._c.query(sql, parameters=dict(params))
        return [dict(zip(columns, row, strict=True)) for row in result.result_rows]

    def companies(self, brand: str) -> list[str]:
        rows = self._rows(
            "SELECT DISTINCT company_code FROM efn_dwh.dim_esb_branch FINAL WHERE brand = {brand:String} "
            "ORDER BY company_code",
            {"brand": brand},
            ("company_code",),
        )
        return [str(r["company_code"]) for r in rows if r["company_code"]]

    def dim(self, table: str, brand: str) -> list[dict[str, Any]]:
        columns = DIM_COLUMNS[table]  # KeyError untuk tabel di luar daftar (bukan input bebas)
        sql = (
            f"SELECT {', '.join(columns)} FROM efn_dwh.{table} FINAL WHERE brand = {{brand:String}} "  # noqa: S608
            f"AND _version = (SELECT max(_version) FROM efn_dwh.{table} WHERE brand = {{brand:String}})"
        )
        return self._rows(sql, {"brand": brand}, columns)

    def bom_ratios(self, brand: str) -> dict[str, float]:
        rows = self._rows(
            "SELECT product_code, argMax(ratio, _version) FROM efn_esb.bom_ratio FINAL WHERE brand = {brand:String} "
            "GROUP BY product_code",
            {"brand": brand},
            ("product_code", "ratio"),
        )
        return {str(r["product_code"]): float(r["ratio"]) for r in rows if r["product_code"]}

    def archive_menus(self, brand: str, start: date, end: date) -> list[dict[str, Any]]:
        """Menu yang muncul di penjualan rentang histori (termasuk yang sudah tidak ada di master ESB)."""
        return self._rows(
            "SELECT menu_code, any(menu_name), any(menu_category), any(menu_category_detail), max(is_takeaway) "
            "FROM efn_dwh.v_esb_sales_daily WHERE brand = {brand:String} AND sales_date >= {start:Date} "
            "AND sales_date < {end:Date} GROUP BY menu_code",
            {"brand": brand, "start": start, "end": end},
            ("menu_code", "menu_name", "menu_category", "menu_category_detail", "is_takeaway"),
        )

    def snapshot(self, brand: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for table in DIM_COLUMNS:
            result = self._c.query(
                f"SELECT max(_version) FROM efn_dwh.{table} WHERE brand = {{brand:String}}",  # noqa: S608
                parameters={"brand": brand},
            )
            value = result.result_rows[0][0] if result.result_rows else None
            out[table] = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
        return out

    def _history_where(self, file: str) -> tuple[str, str]:
        source, day_col, extra = _HISTORY_SOURCE[file]
        where = (
            f"brand = {{brand:String}} AND company_code = {{company:String}} AND {day_col} >= {{start:Date}} "
            f"AND {day_col} < {{end:Date}}{extra}"
        )
        return source, where

    def history_counts(self, brand: str, company: str, file: str, start: date, end: date) -> dict[int, int]:
        source, where = self._history_where(file)
        result = self._c.query(
            f"SELECT branch_id, count() FROM {source} WHERE {where} GROUP BY branch_id",  # noqa: S608
            parameters={"brand": brand, "company": company, "start": start, "end": end},
        )
        return {int(b): int(n) for b, n in result.result_rows}

    def history_rows(
        self, brand: str, company: str, file: str, start: date, end: date, branch_ids: Sequence[int]
    ) -> Iterator[dict[str, Any]]:
        source, where = self._history_where(file)
        columns = HISTORY_COLUMNS[file]
        sql = (
            f"SELECT {', '.join(columns)} FROM {source} WHERE {where} "  # noqa: S608
            f"AND has({{ids:Array(UInt32)}}, branch_id) ORDER BY {_HISTORY_ORDER[file]}"
        )
        params = {"brand": brand, "company": company, "start": start, "end": end, "ids": list(branch_ids)}
        with self._c.query_row_block_stream(sql, parameters=params) as stream:
            for block in stream:
                for row in block:
                    yield dict(zip(columns, row, strict=True))

    # ---------------------------------------------------------------- reconcile (butir 6, 7)
    def monthly_sales_payments(self, brand: str, start: date, end: date) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT s.month, s.branch_id, s.net, p.amount FROM "
            "(SELECT toStartOfMonth(sales_date) AS month, branch_id, toFloat64(sum(net)) AS net "
            " FROM efn_dwh.v_esb_sales_daily WHERE brand = {brand:String} AND sales_date >= {start:Date} "
            " AND sales_date < {end:Date} GROUP BY month, branch_id) AS s "
            "LEFT JOIN (SELECT toStartOfMonth(sales_date) AS month, branch_id, toFloat64(sum(amount)) AS amount "
            " FROM efn_dwh.v_esb_payment_daily WHERE brand = {brand:String} AND sales_date >= {start:Date} "
            " AND sales_date < {end:Date} GROUP BY month, branch_id) AS p "
            "ON p.month = s.month AND p.branch_id = s.branch_id ORDER BY s.month, s.branch_id",
            {"brand": brand, "start": start, "end": end},
            ("month", "branch_id", "net", "amount"),
        )

    def data_quality(self, brand: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT check_name, company_code, item, value, last_day FROM efn_dwh.v_esb_data_quality "
            "WHERE brand = {brand:String} ORDER BY check_name, item LIMIT 500",
            {"brand": brand},
            ("check_name", "company_code", "item", "value", "last_day"),
        )

    # ---------------------------------------------------------------- writeback
    def insert_map(self, rows: list[list[Any]]) -> None:
        for start in range(0, len(rows), 50_000):
            self._c.insert("map_esb_odoo", rows[start : start + 50_000], column_names=list(MAP_COLUMNS),
                           database="efn_dwh")  # fmt: skip
