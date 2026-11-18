"""Pembaca ClickHouse `efn_forecast.*` (user efn_forecast_reader, readonly=2;
infra/clickhouse/access/forecast-access.sql).

Semua query berparameter (`{name:Type}`), kolom eksplisit, selalu difilter PT + rentang tanggal. Kolom yang dibaca =
`VIEW_COLUMNS` (diuji terhadap definisi view di forecast-access.sql).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from efn_forecast.calendar import CalendarEntry

VIEW_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "v_sales_daily": ("day", "company_id", "company_code", "warehouse_id", "warehouse_code", "product_id", "qty"),
    "v_stock_daily": ("day", "company_id", "warehouse_id", "product_id", "on_hand"),
    "v_scrap_daily": ("day", "company_id", "warehouse_id", "product_id", "category", "qty"),
    "v_calendar": ("calendar_id", "company_id", "warehouse_id", "date", "kind", "name", "source", "active"),
    "v_product": (
        "product_id",
        "template_id",
        "category",
        "company_id",
        "active",
        "sale_ok",
        "is_storable",
        "use_expiration_date",
    ),
    "v_warehouse": ("warehouse_id", "code", "company_id", "company_code", "active"),
}

SQL_WAREHOUSES = """
SELECT warehouse_id, code, company_id, active
FROM efn_forecast.v_warehouse
WHERE company_code = {code:String} AND active = 1 AND code != ''
ORDER BY warehouse_id
"""
SQL_SALES = """
SELECT warehouse_id, product_id, day, qty
FROM efn_forecast.v_sales_daily
WHERE company_code = {code:String} AND day >= {start:Date} AND day <= {end:Date}
"""
SQL_STOCK = """
SELECT warehouse_id, product_id, day, on_hand
FROM efn_forecast.v_stock_daily
WHERE company_id = {cid:UInt32} AND day >= {start:Date} AND day <= {end:Date}
"""
SQL_SCRAP = """
SELECT warehouse_id, category, sum(qty) AS qty
FROM efn_forecast.v_scrap_daily
WHERE company_id = {cid:UInt32} AND day >= {start:Date} AND day <= {end:Date}
GROUP BY warehouse_id, category
"""
SQL_CALENDAR = """
SELECT calendar_id, warehouse_id, date, kind, name, source, active
FROM efn_forecast.v_calendar
WHERE company_id = {cid:UInt32} AND date >= {start:Date} AND date <= {end:Date}
"""
SQL_PRODUCTS = """
SELECT product_id, category
FROM efn_forecast.v_product
WHERE (company_id = {cid:UInt32} OR company_id IS NULL) AND active = 1 AND sale_ok = 1
"""


class QueryClient(Protocol):
    def query(
        self, query: str, parameters: dict[str, Any] | None = None, settings: dict[str, Any] | None = None
    ) -> Any: ...


@dataclass(frozen=True)
class Warehouse:
    warehouse_id: int
    code: str
    company_id: int


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class ClickHouseSource:
    def __init__(self, client: QueryClient, timeout_seconds: int = 300) -> None:
        self._client = client
        self._settings = {"max_execution_time": timeout_seconds}

    def _rows(self, sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        return list(self._client.query(sql, parameters=params, settings=self._settings).result_rows)

    def warehouses(self, company: str) -> list[Warehouse]:
        # Kode persis seperti stock.warehouse.code (tanpa upper): Odoo mencocokkan warehouse_code case-sensitive.
        rows = self._rows(SQL_WAREHOUSES, {"code": company})
        return [Warehouse(int(w), str(c), int(cid)) for w, c, cid, _active in rows]

    def sales(self, company: str, start: date, end: date) -> list[tuple[int, int, date, float]]:
        rows = self._rows(SQL_SALES, {"code": company, "start": start, "end": end})
        return [(int(w), int(p), _day(d), float(q or 0)) for w, p, d, q in rows]

    def stock(self, company_id: int, start: date, end: date) -> list[tuple[int, int, date, float]]:
        rows = self._rows(SQL_STOCK, {"cid": company_id, "start": start, "end": end})
        return [(int(w), int(p), _day(d), float(q or 0)) for w, p, d, q in rows]

    def scrap(self, company_id: int, start: date, end: date) -> list[tuple[int, str, float]]:
        rows = self._rows(SQL_SCRAP, {"cid": company_id, "start": start, "end": end})
        return [(int(w), str(c or ""), float(q or 0)) for w, c, q in rows]

    def calendar(self, company_id: int, start: date, end: date) -> list[CalendarEntry]:
        rows = self._rows(SQL_CALENDAR, {"cid": company_id, "start": start, "end": end})
        return [
            CalendarEntry(
                day=_day(d), kind=str(k), name=str(n or "")[:64], source=str(s), active=bool(a), warehouse_id=int(w)
            )
            for _id, w, d, k, n, s, a in rows
        ]

    def products(self, company_id: int) -> dict[int, str]:
        return {int(p): str(c or "") for p, c in self._rows(SQL_PRODUCTS, {"cid": company_id})}


def build_client(host: str, port: int, user: str, password: str, database: str, secure: bool, ca: str | None) -> Any:
    """clickhouse-connect (Apache-2.0): HTTPS + verify=True + CA internal (bukan bundle certifi), pola services/etl."""
    import clickhouse_connect

    kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "username": user,
        "password": password,
        "database": database,
        "secure": secure,
        "connect_timeout": 10,
        "send_receive_timeout": 600,
    }
    if secure:
        if not ca:
            raise ValueError("ClickHouse TLS membutuhkan CH_CA_FILE")
        kwargs.update(verify=True, ca_cert=ca)
    return clickhouse_connect.get_client(**kwargs)
