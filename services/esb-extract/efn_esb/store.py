"""Adapter ClickHouse (pola `services/etl/efn_etl/adapters.py`): HTTPS 8443 + CA internal, user `efn_esb`.

Hak `esb_writer` = SELECT + INSERT (infra/clickhouse/access/esb-access.sql). Tanpa CREATE/DROP TABLE (SEC-AG-01);
pengecualian tabel agregat Insight (efn_esb/aggregates.py): TRUNCATE pada `efn_dwh.agg_esb_*_build`, ALTER DELETE
(REPLACE/DROP PARTITION) pada `efn_dwh.agg_esb_*`, grant per tabel. Semua nilai lewat parameter.
"""

import json
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.client import Client

from efn_esb.config import ClickHouseSettings
from efn_esb.transform import BomRatio


def clickhouse_client_kwargs(settings: ClickHouseSettings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": settings.host,
        "port": settings.port,
        "username": settings.user,
        "password": settings.password,
        "database": settings.database,
        "secure": settings.secure,
        "connect_timeout": 10,
        "send_receive_timeout": 300,
    }
    if settings.secure:
        if not settings.ca_file:
            raise ValueError("ClickHouse TLS membutuhkan CH_CA_FILE")
        kwargs.update(verify=True, ca_cert=settings.ca_file)
    return kwargs


def build_clickhouse_client(settings: ClickHouseSettings) -> Client:
    return clickhouse_connect.get_client(**clickhouse_client_kwargs(settings))


class ClickHouseStore:
    """Dipakai hanya dari thread utama (worker hanya mengambil data ESB)."""

    def __init__(self, client: Client, mart_db: str, raw_db: str) -> None:
        self._client = client
        self._mart = mart_db
        self._raw = raw_db

    @property
    def mart_db(self) -> str:
        return self._mart

    @property
    def raw_db(self) -> str:
        return self._raw

    def command(
        self, sql: str, parameters: dict[str, Any] | None = None, settings: dict[str, Any] | None = None
    ) -> None:
        self._client.command(sql, parameters=parameters, settings=settings)

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[Sequence[Any]]:
        return list(self._client.query(sql, parameters=parameters).result_rows)

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            self._client.insert(table, rows, column_names=list(columns), database=self._mart)

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            self._client.insert(table, rows, column_names=list(columns), database=self._raw)

    def checkpoints(self, brand: str, since: date) -> dict[tuple[str, str, date], datetime]:
        result = self._client.query(
            f"SELECT entity, scope_key, day, max(finished_at) FROM {self._raw}.extract_checkpoint "  # noqa: S608
            "WHERE brand = {brand:String} AND day >= {since:Date} AND status = 'done' "
            "GROUP BY entity, scope_key, day",
            parameters={"brand": brand, "since": since},
        )
        return {(str(e), str(s), d): ts for e, s, d, ts in result.result_rows}

    def load_aliases(self, brand: str) -> dict[str, str]:
        result = self._client.query(
            f"SELECT alias_norm, product_code FROM {self._mart}.dim_esb_product_alias FINAL "  # noqa: S608
            "WHERE brand = {brand:String}",
            parameters={"brand": brand},
        )
        return {str(a): str(c) for a, c in result.result_rows}

    def load_bom_ratios(self, brand: str) -> dict[int, BomRatio]:
        result = self._client.query(
            f"SELECT bom_id, product_code, product_name, uom, ratio, sample_doc FROM {self._raw}.bom_ratio FINAL "  # noqa: S608
            "WHERE brand = {brand:String}",
            parameters={"brand": brand},
        )
        return {int(b): BomRatio(str(c), str(n), float(r), str(u), str(s)) for b, c, n, u, r, s in result.result_rows}

    def load_suppliers(self, brand: str) -> list[tuple[int, str, str]]:
        """G65: (supplier_id, supplier_code, supplier_name) versi terakhir per kode (kolom supplier_id: ALTER di
        23-g65-esb-purchase-stock.sql)."""
        result = self._client.query(
            f"SELECT supplier_id, supplier_code, supplier_name FROM {self._mart}.dim_esb_supplier FINAL "  # noqa: S608
            "WHERE brand = {brand:String}",
            parameters={"brand": brand},
        )
        return [(int(i), str(c), str(n)) for i, c, n in result.result_rows]

    def load_product_ids(self, brand: str) -> dict[int, str]:
        """G68: {productID: productCode} versi terakhir (dim_esb_product_id, 25-g68-esb-pl-waste-fillrate.sql)."""
        result = self._client.query(
            f"SELECT product_id, product_code FROM {self._mart}.dim_esb_product_id FINAL "  # noqa: S608
            "WHERE brand = {brand:String} AND product_code != ''",
            parameters={"brand": brand},
        )
        return {int(p): str(c) for p, c in result.result_rows}

    def load_raw(self, brand: str, endpoint: str, doc_keys: Sequence[str]) -> dict[str, tuple[date, Any]]:
        """G68 retransform: {doc_key: (doc_date, payload JSON)} versi terakhir raw_doc (tanpa panggilan ESB)."""
        if not doc_keys:
            return {}
        result = self._client.query(
            f"SELECT doc_key, doc_date, payload FROM {self._raw}.raw_doc FINAL "  # noqa: S608
            "WHERE brand = {brand:String} AND endpoint = {endpoint:String} AND doc_key IN {keys:Array(String)}",
            parameters={"brand": brand, "endpoint": endpoint, "keys": list(doc_keys)},
        )
        return {str(k): (d, json.loads(p)) for k, d, p in result.result_rows}
