"""Adapter I/O: PostgreSQL (read-only) dan ClickHouse (sink + watermark)."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
import psycopg
from clickhouse_connect.driver.client import Client
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from efn_etl.config import PgTarget, Settings
from efn_etl.erasure import count_sql, delete_sql
from efn_etl.watermark import Cursor, normalize_watermark

WATERMARK_TABLE = "etl_watermark"
SQL_GET_WATERMARK = (
    "SELECT argMax(watermark, updated_at) AS wm, count() AS n FROM etl_watermark WHERE job = {job:String}"
)


# Satu koneksi per halaman: transaksi read-only, TimeZone UTC, statement_timeout (R-O03).
class PgSource:
    def __init__(self, target: PgTarget, statement_timeout_ms: int, application_name: str = "efn-etl") -> None:
        options = f"-c default_transaction_read_only=on -c statement_timeout={int(statement_timeout_ms)} -c TimeZone=UTC"
        # TLS: sslmode/sslrootcert divalidasi Settings.from_env (verify-full kecuali EFN_ALLOW_PLAINTEXT di dev).
        self._conninfo = make_conninfo(
            host=target.host,
            port=target.port,
            user=target.user,
            password=target.password,
            dbname=target.dbname,
            sslmode=target.sslmode,
            sslrootcert=target.sslrootcert,
            application_name=application_name,
            connect_timeout=10,
            options=options,
        )

    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]:
        params = {"cursor_ts": cursor.ts, "cursor_key": cursor.key, "limit": limit}
        return self.query(sql, params)

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with psycopg.connect(self._conninfo, autocommit=True, row_factory=dict_row) as conn:
            return conn.execute(sql, params).fetchall()


def clickhouse_client_kwargs(settings: Settings) -> dict[str, Any]:
    """Argumen `clickhouse_connect.get_client`. HTTPS + `verify=True` + CA internal (bukan bundle certifi)."""
    kwargs: dict[str, Any] = {
        "host": settings.ch_host,
        "port": settings.ch_port,
        "username": settings.ch_user,
        "password": settings.ch_password,
        "database": settings.ch_database,
        "secure": settings.ch_secure,
        "connect_timeout": 10,
        "send_receive_timeout": 300,
    }
    if settings.ch_secure:
        if not settings.ch_ca_file:
            raise ValueError("ClickHouse TLS membutuhkan CH_CA_FILE")
        kwargs.update(verify=True, ca_cert=settings.ch_ca_file)
    return kwargs


def build_clickhouse_client(settings: Settings) -> Client:
    return clickhouse_connect.get_client(**clickhouse_client_kwargs(settings))


class ClickHouseSink:
    def __init__(self, client: Client, database: str) -> None:
        self._client = client
        self._database = database

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            self._client.insert(table, rows, column_names=list(columns), database=self._database)


class ClickHouseErasureStore:
    """Erasure mart (docs/04 §8.4). Butuh grant `ALTER DELETE` pada tabel `ERASURE_TARGETS` (etl-access.sql).

    `mutations_sync=2`: tunggu mutation selesai; timeout -> exception -> request tetap `processing`, diulang.
    """

    def __init__(self, client: Client, database: str, mutation_timeout_seconds: int) -> None:
        self._client = client
        self._database = database
        self._timeout = int(mutation_timeout_seconds)

    def count(self, table: str, column: str, member_hash: str) -> int:
        result = self._client.query(count_sql(self._database, table, column, member_hash))
        rows = result.result_rows
        return int(rows[0][0]) if rows else 0

    def delete(self, table: str, column: str, member_hash: str) -> None:
        self._client.command(
            delete_sql(self._database, table, column, member_hash),
            settings={"mutations_sync": 2, "max_execution_time": self._timeout},
        )


class ClickHouseWatermarkStore:
    def __init__(self, client: Client, database: str) -> None:
        self._client = client
        self._database = database

    def get(self, job: str) -> datetime | None:
        result = self._client.query(SQL_GET_WATERMARK, parameters={"job": job})
        rows = result.result_rows
        if not rows or not rows[0][1]:
            return None
        return normalize_watermark(rows[0][0])

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
        self._client.insert(
            WATERMARK_TABLE,
            [[job, watermark, rows_loaded, datetime.now(UTC)]],
            column_names=["job", "watermark", "rows_loaded", "updated_at"],
            database=self._database,
        )
