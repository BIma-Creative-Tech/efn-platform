"""Adapter I/O: PostgreSQL (read-only) dan ClickHouse (sink + watermark)."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import clickhouse_connect
import psycopg
from clickhouse_connect.driver.client import Client
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from efn_etl.audit import ChainHead, ChainRow, CopiedLog, EpochRecord
from efn_etl.config import PgTarget, Settings
from efn_etl.erasure import count_sql, delete_sql
from efn_etl.heartbeat import JobRun
from efn_etl.watermark import Cursor, normalize_watermark

WATERMARK_TABLE = "etl_watermark"
SQL_GET_WATERMARK = (
    "SELECT argMax(watermark, updated_at) AS wm, count() AS n FROM etl_watermark WHERE job = {job:String}"
)


# Satu koneksi per halaman: transaksi read-only, TimeZone UTC, statement_timeout (R-O03).
class PgSource:
    def __init__(self, target: PgTarget, statement_timeout_ms: int, application_name: str = "efn-etl") -> None:
        options = (
            f"-c default_transaction_read_only=on -c statement_timeout={int(statement_timeout_ms)} -c TimeZone=UTC"
        )
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

    @contextmanager
    def snapshot(self) -> Iterator["PgSnapshot"]:
        """Satu transaksi REPEATABLE READ READ ONLY untuk snapshot konsisten multi-halaman (stok, G13)."""
        with psycopg.connect(self._conninfo, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            try:
                yield PgSnapshot(conn)
            finally:
                conn.rollback()


class PgSnapshot:
    def __init__(self, conn: psycopg.Connection[dict[str, Any]]) -> None:
        self._conn = conn

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._conn.execute(sql, params).fetchall()


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


class ClickHouseRunStore:
    """Riwayat run job (`etl_job_run`, heartbeat SEC-52) dan penanda snapshot stok terakhir."""

    COLUMNS = ("job", "run_ts", "status", "rows", "pages", "duration_ms", "watermark")

    def __init__(self, client: Client, database: str) -> None:
        self._client = client
        self._database = database

    def record(self, run: JobRun) -> None:
        self._client.insert(
            "etl_job_run",
            [[run.job, run.run_ts, run.status, run.rows, run.pages, run.duration_ms, run.watermark]],
            column_names=list(self.COLUMNS),
            database=self._database,
        )

    def last_stock_snapshot(self) -> datetime | None:
        rows = self._client.query("SELECT max(snapshot_ts), count() FROM stock_snapshot_run").result_rows
        if not rows or not rows[0][1]:
            return None
        return normalize_watermark(rows[0][0])

    def last_loyalty_recon(self) -> datetime | None:
        """Run rekonsiliasi loyalty terakhir yang selesai (penanda `loyalty_recon_run`, G14)."""
        rows = self._client.query("SELECT max(run_at), count() FROM loyalty_recon_run").result_rows
        if not rows or not rows[0][1]:
            return None
        return normalize_watermark(rows[0][0])

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            self._client.insert(table, rows, column_names=list(columns), database=self._database)


class ClickHouseAuditStore:
    """Salinan audit append-only (Gelombang 12). Hak efn_etl: SELECT+INSERT saja pada tabel audit_* (tanpa ALTER/
    DELETE/TRUNCATE; etl-access.sql). Semua query berparameter (tidak ada interpolasi nilai)."""

    def __init__(self, client: Client, database: str) -> None:
        self._client = client
        self._database = database

    def _rows(self, sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        return list(self._client.query(sql, parameters=params or {}).result_rows)

    def epochs(self) -> list[EpochRecord]:
        """Riwayat chain epoch (07-audit-epoch.sql). Kosong = belum terdaftar (salinan lama = epoch 0)."""
        rows = self._rows("SELECT epoch, marker, reason, started_at FROM audit_chain_epoch ORDER BY epoch, started_at")
        return [EpochRecord(int(e), str(m), str(r), s) for e, m, r, s in rows]

    def heads(self, epoch: int = 0) -> dict[int, ChainHead]:
        rows = self._rows(
            "SELECT chain_company, max(chain_seq), argMax(row_hash, chain_seq) FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} GROUP BY chain_company",
            {"e": epoch},
        )
        return {int(c): ChainHead(seq=int(s), row_hash=str(h)) for c, s, h in rows}

    def max_log_id(self, epoch: int = 0) -> int:
        rows = self._rows("SELECT max(log_id) FROM audit_log_copy WHERE chain_epoch = {e:UInt32}", {"e": epoch})
        return int(rows[0][0] or 0) if rows else 0

    def min_log_id_since(self, since: datetime, epoch: int = 0) -> int | None:
        rows = self._rows(
            "SELECT min(log_id), count() FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} AND create_date >= {since:DateTime64(6)}",
            {"since": since, "e": epoch},
        )
        return int(rows[0][0]) if rows and rows[0][1] else None

    def log_ids_between(self, lo: int, hi: int, epoch: int = 0) -> set[int]:
        rows = self._rows(
            "SELECT log_id FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} AND log_id > {lo:UInt64} AND log_id <= {hi:UInt64}",
            {"lo": lo, "hi": hi, "e": epoch},
        )
        return {int(r[0]) for r in rows}

    def logs_with_lines(self, log_ids: Sequence[int], epoch: int = 0) -> set[int]:
        if not log_ids:
            return set()
        rows = self._rows(
            "SELECT DISTINCT log_id FROM audit_log_line_copy "
            "WHERE chain_epoch = {e:UInt32} AND log_id IN {ids:Array(UInt64)}",
            {"ids": list(log_ids), "e": epoch},
        )
        return {int(r[0]) for r in rows}

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if rows:
            self._client.insert(table, rows, column_names=list(columns), database=self._database)

    def copied_range(self, lo: int, hi: int, epoch: int = 0) -> list[CopiedLog]:
        rows = self._rows(
            "SELECT log_id, create_date, model_name, operation, payload_hash FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} AND log_id > {lo:UInt64} AND log_id <= {hi:UInt64}",
            {"lo": lo, "hi": hi, "e": epoch},
        )
        return [CopiedLog(int(a), b, str(c), str(d), str(e)) for a, b, c, d, e in rows]

    def recent_copies(self, epoch: int, limit: int) -> list[CopiedLog]:
        """K log tersalin terakhir (id terbesar) epoch ini — anchor deteksi restore."""
        rows = self._rows(
            "SELECT log_id, create_date, model_name, operation, payload_hash FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} ORDER BY log_id DESC LIMIT {n:UInt32}",
            {"e": epoch, "n": limit},
        )
        return [CopiedLog(int(a), b, str(c), str(d), str(e)) for a, b, c, d, e in rows]

    def line_hashes(self, log_ids: Sequence[int], epoch: int = 0) -> dict[int, dict[int, str]]:
        if not log_ids:
            return {}
        rows = self._rows(
            "SELECT log_id, line_id, line_hash FROM audit_log_line_copy "
            "WHERE chain_epoch = {e:UInt32} AND log_id IN {ids:Array(UInt64)}",
            {"ids": list(log_ids), "e": epoch},
        )
        out: dict[int, dict[int, str]] = {}
        for log_id, line_id, digest in rows:
            out.setdefault(int(log_id), {})[int(line_id)] = str(digest)
        return out

    def chain_rows(self, company: int, after_seq: int, limit: int, epoch: int = 0) -> list[ChainRow]:
        rows = self._rows(
            "SELECT chain_company, chain_seq, log_id, payload_hash, prev_hash, row_hash FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} AND chain_company = {c:UInt32} AND chain_seq > {s:UInt64} "
            "ORDER BY chain_seq LIMIT {n:UInt32}",
            {"c": company, "s": after_seq, "n": limit, "e": epoch},
        )
        return [ChainRow(int(a), int(b), int(c), str(d), str(e), str(f)) for a, b, c, d, e, f in rows]

    def copied_log_ids(self, log_ids: Sequence[int]) -> set[int]:
        """Id yang pernah tersalin di epoch MANA PUN (verifikasi sink G14: log sah dihapus retensi, atau log yang hilang
        karena restore, tetap punya jejak salinan)."""
        if not log_ids:
            return set()
        rows = self._rows(
            "SELECT DISTINCT log_id FROM audit_log_copy WHERE log_id IN {ids:Array(UInt64)}", {"ids": list(log_ids)}
        )
        return {int(r[0]) for r in rows}

    def chain_hashes(self, company: int, seqs: Sequence[int], epoch: int = 0) -> dict[int, str]:
        """row_hash salinan untuk (epoch, company, seq) yang dijangkarkan di Loki (SEC-52 butir 3)."""
        if not seqs:
            return {}
        rows = self._rows(
            "SELECT chain_seq, row_hash FROM audit_log_copy "
            "WHERE chain_epoch = {e:UInt32} AND chain_company = {c:UInt32} AND chain_seq IN {s:Array(UInt64)}",
            {"c": company, "s": list(seqs), "e": epoch},
        )
        return {int(a): str(b) for a, b in rows}

    def last_recon_at(self) -> datetime | None:
        rows = self._rows("SELECT max(run_at), count() FROM audit_recon_run")
        if not rows or not rows[0][1]:
            return None
        return normalize_watermark(rows[0][0])
