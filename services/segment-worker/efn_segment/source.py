"""Pembaca ClickHouse `efn_segment.*` (user efn_segment_reader, readonly=2; infra/clickhouse/access/segment-access.sql).

Semua query berparameter (`{name:Type}`), kolom eksplisit, selalu difilter PT (+ rentang tanggal untuk event). Agregasi
per subjek x produk dilakukan di ClickHouse (jendela beberapa hari -> satu baris per pasangan). Kolom yang dibaca =
`VIEW_COLUMNS` (diuji terhadap definisi view di segment-access.sql).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from efn_segment.segments import SubjectProduct, SubjectSearch, opt_ts

VIEW_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "v_subject_product_daily": (
        "day",
        "company_code",
        "subject_key",
        "subject_kind",
        "product_id",
        "views",
        "view_seconds",
        "oos_views",
        "cart_adds",
        "checkouts",
        "purchases",
        "last_view_at",
        "last_wishlist_add_at",
        "last_wishlist_remove_at",
        "last_purchase_at",
        "max_view_price_idr",
        "purchase_price_sum",
        "purchase_price_n",
    ),
    "v_subject_search_daily": ("day", "company_code", "subject_key", "subject_kind", "searches", "no_result_searches"),
    "v_product": ("company_code", "product_id", "category"),
    "v_market_price": ("product_id", "median_price_idr", "reports"),
}

SQL_SUBJECT_PRODUCTS = """
SELECT subject_key, any(subject_kind), product_id,
       sum(views), sum(view_seconds), sum(oos_views), sum(cart_adds), sum(checkouts), sum(purchases),
       max(last_view_at), max(last_wishlist_add_at), max(last_wishlist_remove_at), max(last_purchase_at),
       max(max_view_price_idr), sum(purchase_price_sum), sum(purchase_price_n)
FROM efn_segment.v_subject_product_daily
WHERE company_code = {code:String} AND day >= {start:Date} AND day <= {end:Date}
  AND subject_kind IN {kinds:Array(String)}
GROUP BY subject_key, product_id
"""
SQL_SUBJECT_SEARCHES = """
SELECT subject_key, any(subject_kind), sum(searches), sum(no_result_searches)
FROM efn_segment.v_subject_search_daily
WHERE company_code = {code:String} AND day >= {start:Date} AND day <= {end:Date}
  AND subject_kind IN {kinds:Array(String)}
GROUP BY subject_key
HAVING sum(no_result_searches) >= {min_nr:UInt32}
"""
SQL_PRODUCTS = """
SELECT product_id, any(category)
FROM efn_segment.v_product
WHERE company_code = {code:String}
GROUP BY product_id
"""
SQL_MARKET = """
SELECT product_id, median_price_idr
FROM efn_segment.v_market_price
WHERE product_id IN (SELECT product_id FROM efn_segment.v_product WHERE company_code = {code:String})
"""


class QueryClient(Protocol):
    def query(
        self, query: str, parameters: dict[str, Any] | None = None, settings: dict[str, Any] | None = None
    ) -> Any: ...


def _ts(value: Any) -> datetime | None:
    return opt_ts(value) if isinstance(value, datetime) else None


class ClickHouseSource:
    def __init__(self, client: QueryClient, timeout_seconds: int = 300) -> None:
        self._client = client
        self._settings = {"max_execution_time": timeout_seconds}

    def _rows(self, sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
        return list(self._client.query(sql, parameters=params, settings=self._settings).result_rows)

    def subject_products(self, company: str, start: date, end: date, kinds: Sequence[str]) -> list[SubjectProduct]:
        rows = self._rows(SQL_SUBJECT_PRODUCTS, {"code": company, "start": start, "end": end, "kinds": list(kinds)})
        return [
            SubjectProduct(
                subject_key=str(r[0]),
                subject_kind=str(r[1]),
                product_id=int(r[2]),
                views=int(r[3] or 0),
                view_seconds=int(r[4] or 0),
                oos_views=int(r[5] or 0),
                cart_adds=int(r[6] or 0),
                checkouts=int(r[7] or 0),
                purchases=int(r[8] or 0),
                last_view_at=_ts(r[9]),
                last_wishlist_add_at=_ts(r[10]),
                last_wishlist_remove_at=_ts(r[11]),
                last_purchase_at=_ts(r[12]),
                max_view_price_idr=int(r[13] or 0),
                purchase_price_sum=int(r[14] or 0),
                purchase_price_n=int(r[15] or 0),
            )
            for r in rows
        ]

    def subject_searches(
        self, company: str, start: date, end: date, kinds: Sequence[str], min_no_result: int
    ) -> list[SubjectSearch]:
        params = {"code": company, "start": start, "end": end, "kinds": list(kinds), "min_nr": min_no_result}
        return [
            SubjectSearch(str(k), str(kind), int(s or 0), int(n or 0))
            for k, kind, s, n in self._rows(SQL_SUBJECT_SEARCHES, params)
        ]

    def categories(self, company: str) -> dict[int, str]:
        return {int(p): str(c or "") for p, c in self._rows(SQL_PRODUCTS, {"code": company})}

    def market_prices(self, company: str) -> dict[int, float]:
        return {int(p): float(m) for p, m in self._rows(SQL_MARKET, {"code": company}) if m is not None}


def build_client(host: str, port: int, user: str, password: str, database: str, secure: bool, ca: str | None) -> Any:
    """clickhouse-connect (Apache-2.0): HTTPS + verify=True + CA internal (bukan bundle certifi)."""
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
