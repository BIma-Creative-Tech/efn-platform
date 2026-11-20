"""G50 (ADR-047, docs/04 §28.6/§28.7): job perilaku visitor, agregat, erasure per subject_key (tanpa DB)."""

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from efn_etl.behavior import (
    AGG_STATEMENTS,
    EVENT_COLUMNS,
    JOB_ERASURE,
    JOB_EVENTS,
    MAX_QUERY,
    REDACTED,
    SQL_BEHAVIOR_ERASURES,
    SQL_BEHAVIOR_EVENTS,
    SQL_DSAR_SUBJECT_KEYS,
    BehaviorDsarHook,
    ErasurePending,
    agg_due,
    agg_window,
    behavior_job,
    erase_keys,
    erasure_count_sql,
    erasure_delete_sql,
    run_behavior_agg,
    run_behavior_erasure,
    sanitize_query,
    transform_behavior_event,
    valid_subject_keys,
)
from efn_etl.config import Settings
from efn_etl.erasure import ErasureRequest, OutgoingMessage, erase_one
from efn_etl.jobs import enabled_jobs
from efn_etl.runner import run_job
from efn_etl.watermark import Cursor

ROOT = Path(__file__).resolve().parents[3]
DDL = (ROOT / "infra" / "clickhouse" / "init" / "16-g50-behavior.sql").read_text(encoding="utf-8")
ETL_ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "etl-access.sql").read_text(encoding="utf-8")
PG_GRANTS = (ROOT / "services" / "etl" / "sql" / "platform-behavior-grants.sql").read_text(encoding="utf-8")
KEY_A = "a" * 64
KEY_B = "b" * 64
KEY_C = "c" * 64
EID = "6f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event_id": EID,
        "subject_key": KEY_A,
        "subject_kind": "member",
        "company_code": "SMD",
        "channel": "web",
        "channel_code": "smd",
        "outlet_code": "SMD01",
        "type": "product_view",
        "product_id": 123,
        "duration_s": 42,
        "qty": None,
        "query": None,
        "result_count": None,
        "stock_status": "out",
        "price_idr": 12500,
        "received_at": datetime(2026, 11, 19, 18, 30, tzinfo=UTC),
        "ingested_at": datetime(2026, 11, 19, 18, 31, tzinfo=UTC),
        "_cursor_ts": datetime(2026, 11, 19, 18, 31, tzinfo=UTC),
        "_cursor_key": f"{KEY_A}:{EID}",
    }
    row.update(over)
    return row


def _table_columns(table: str) -> list[str]:
    body = DDL.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{table}", 1)[1].split("ENGINE", 1)[0]
    cols = re.findall(r"^\s{4}(\w+)\s", body, re.MULTILINE)
    return [c for c in cols if c not in ("_loaded_at", "INDEX")]


# ---------------------------------------------------------------- definisi job
def test_behavior_job_off_by_default_and_platform_only() -> None:
    assert JOB_EVENTS not in {j.name for j in enabled_jobs(True)}
    # Tidak butuh Odoo: sumber = DB platform.
    jobs = {j.name: j for j in enabled_jobs(False, behavior=True)}
    assert JOB_EVENTS in jobs
    job = jobs[JOB_EVENTS]
    assert job.source == "platform"
    assert job.identity_field == "subject_key"
    assert job.target_table == "fact_visitor_event"


def test_event_sql_keyset_no_select_star_no_identity_columns() -> None:
    sql = SQL_BEHAVIOR_EVENTS
    assert "SELECT *" not in sql.upper()
    for needle in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s", "_cursor_ts", "_cursor_key", "ORDER BY"):
        assert needle in sql
    for forbidden in ("device_id", "member_id", "batch_id", "client_ts", "ip", "user_agent", "device_link"):
        assert not re.search(rf"\b{forbidden}\b", sql), forbidden
    assert "ingested_at AS _cursor_ts" in sql


def test_columns_match_ddl_ttl_and_grants() -> None:
    assert _table_columns("fact_visitor_event") == list(EVENT_COLUMNS)
    fact = DDL.split("efn_dwh.fact_visitor_event", 1)[1].split(";", 1)[0]
    assert "ReplacingMergeTree(ingested_at)" in fact
    assert "TTL event_date + INTERVAL 90 DAY" in fact
    tables = ("fact_visitor_event", "agg_visitor_product_daily", "agg_visitor_funnel_daily", "agg_visitor_search_daily")
    for table in tables:
        assert f"GRANT SELECT, INSERT ON efn_dwh.{table} TO etl_writer;" in ETL_ACCESS
    assert "GRANT ALTER DELETE ON efn_dwh.fact_visitor_event TO etl_writer;" in ETL_ACCESS
    for table in ("agg_visitor_product_daily", "agg_visitor_funnel_daily", "agg_visitor_search_daily"):
        assert f"GRANT ALTER DELETE ON efn_dwh.{table}" not in ETL_ACCESS
        body = DDL.split(f"efn_dwh.{table}", 1)[1].split(";", 1)[0]
        assert "TTL day + INTERVAL 2 YEAR" in body
        assert "subject_key" not in body  # agregat anonim


def test_pg_grants_cover_sql_columns_and_exclude_link_tables() -> None:
    grant = PG_GRANTS.split("GRANT SELECT (", 1)[1].split(")", 1)[0]
    granted = {c.strip() for c in grant.replace("\n", " ").split(",")}
    for column in re.findall(r"e\.(\w+)", SQL_BEHAVIOR_EVENTS):
        assert column in granted, column
    assert "GRANT SELECT (id, subject_key, request_id, created_at) ON behavior.subject_erasure TO efn_etl;" in PG_GRANTS
    assert "efn_readonly" not in PG_GRANTS.split("GRANT USAGE", 1)[1].split("DO $$", 1)[0]
    assert "REVOKE ALL ON behavior.device_link" in PG_GRANTS


# ---------------------------------------------------------------- transformasi
def test_transform_valid_row() -> None:
    out = dict(zip(EVENT_COLUMNS, transform_behavior_event(_row()), strict=True))
    assert out["event_id"] == uuid.UUID(EID)
    assert out["subject_key"] == KEY_A
    assert out["channel_code"] == "SMD"
    assert out["event_type"] == "product_view"
    assert out["stock_status"] == "out"
    assert out["price_idr"] == 12500
    assert out["query"] == "" and out["result_count"] is None
    # 18:30 UTC = 01:30 WIB hari berikutnya.
    assert out["event_date"] == date(2026, 11, 20)
    assert out["ingested_at"] == datetime(2026, 11, 19, 18, 31, tzinfo=UTC)


@pytest.mark.parametrize(
    "override",
    [
        {"subject_key": "6f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"},  # device ID mentah, bukan HMAC
        {"subject_key": "62812345678"},  # nomor HP
        {"subject_key": "A" * 64},  # hex huruf besar = bukan keluaran BFF
        {"subject_kind": "anon"},
        {"company_code": "RTL"},
        {"channel": "pos"},
        {"channel_code": "bad code"},
        {"outlet_code": "a' OR 1=1"},
        {"type": "page_view"},
        {"event_id": "not-a-uuid"},
    ],
)
def test_transform_rejects_contract_violations(override: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        transform_behavior_event(_row(**override))


def test_transform_clamps_and_drops_query_on_non_search() -> None:
    out = dict(
        zip(
            EVENT_COLUMNS,
            transform_behavior_event(
                _row(duration_s=9999, qty=-3, query="0812-3456-7890", price_idr=-5, stock_status="weird")
            ),
            strict=True,
        )
    )
    assert out["duration_s"] == 600
    assert out["qty"] == 0
    assert out["query"] == ""  # query hanya untuk event search
    assert out["price_idr"] is None
    assert out["stock_status"] == ""


def test_transform_search_sanitizes_query() -> None:
    row = _row(type="search", product_id=None, query="Beras  PANDAN wangi budi@mail.com", result_count=0)
    out = dict(zip(EVENT_COLUMNS, transform_behavior_event(row), strict=True))
    assert out["product_id"] == 0
    assert out["query"] == f"beras pandan wangi {REDACTED}"
    assert out["result_count"] == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Susu UHT", "susu uht"),
        ("ＳＵＳＵ　ＵＨＴ", "susu uht"),  # NFKC full-width
        ("hp 081234567890", f"hp {REDACTED}"),
        ("hubungi 0812-3456-7890 ya", f"hubungi {REDACTED} ya"),
        ("+62 812 3456 789", REDACTED),
        ("nik 3171234567890001", f"nik {REDACTED}"),
        ("minyak 2 liter", "minyak 2 liter"),
        ("kode 12345", "kode 12345"),  # 5 digit tidak disensor
        ("a@b.co promo", f"{REDACTED} promo"),
        (None, ""),
    ],
)
def test_sanitize_query(raw: str | None, expected: str) -> None:
    assert sanitize_query(raw) == expected


def test_sanitize_query_truncates() -> None:
    assert len(sanitize_query("x" * 500)) == MAX_QUERY


def test_run_job_skips_excluded_subjects_but_advances_watermark() -> None:
    class Source:
        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls > 1:
                return []
            return [_row(), _row(subject_key=KEY_B, _cursor_key=f"{KEY_B}:{EID}")]

    class Sink:
        def __init__(self) -> None:
            self.rows: list[list[Any]] = []

        def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
            self.rows.extend(rows)

    class Store:
        def __init__(self) -> None:
            self.wm: datetime | None = None

        def get(self, job: str) -> datetime | None:
            return self.wm

        def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
            self.wm = watermark

    sink, store = Sink(), Store()
    result = run_job(behavior_job(), Source(), sink, store, timedelta(minutes=5), 10, 5, frozenset({KEY_A}))
    assert [r[1] for r in sink.rows] == [KEY_B]
    assert result.rows == 2 and store.wm is not None


# ---------------------------------------------------------------- agregat
def test_agg_window_and_due() -> None:
    now = datetime(2026, 11, 19, 18, 30, tzinfo=UTC)  # 20 Nov WIB
    assert agg_window(now, 3) == (date(2026, 11, 18), date(2026, 11, 21))
    with pytest.raises(ValueError):
        agg_window(now, 0)
    assert agg_due(None, now, timedelta(hours=1))
    assert not agg_due(now - timedelta(minutes=30), now, timedelta(hours=1))
    assert agg_due(now - timedelta(hours=1), now, timedelta(hours=1))


def test_run_behavior_agg_params_and_k_anonymity() -> None:
    class Store:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, Any]]] = []

        def last_agg_at(self) -> datetime | None:
            return None

        def execute(self, sql: str, parameters: dict[str, Any]) -> None:
            self.calls.append((sql, parameters))

    store = Store()
    result = run_behavior_agg(store, datetime(2026, 11, 20, 3, 0, tzinfo=UTC), 2, 10)
    assert result.tables == ("agg_visitor_product_daily", "agg_visitor_funnel_daily", "agg_visitor_search_daily")
    assert all(p["start"] == date(2026, 11, 19) and p["end"] == date(2026, 11, 21) for _, p in store.calls)
    search_sql, search_params = store.calls[2]
    assert "HAVING uniqExact(subject_key) >= {k:UInt32}" in search_sql
    assert search_params["k"] == 10
    assert "k" not in store.calls[0][1]
    with pytest.raises(ValueError):
        run_behavior_agg(store, datetime(2026, 11, 20, tzinfo=UTC), 2, 1)


def test_agg_sql_never_writes_subject_key() -> None:
    for table, sql in AGG_STATEMENTS:
        target_cols = sql.split(f"efn_dwh.{table}", 1)[1].split(")", 1)[0]
        assert "subject_key" not in target_cols
        assert _table_columns(table) == [c.strip() for c in target_cols.replace("(", "").split(",")]
        assert "{start:Date}" in sql and "{end:Date}" in sql
        assert "SELECT *" not in sql.upper()


# ---------------------------------------------------------------- erasure
def test_erasure_sql_validates_keys() -> None:
    sql = erasure_delete_sql("efn_dwh", [KEY_A, KEY_B])
    assert sql == f"ALTER TABLE efn_dwh.fact_visitor_event DELETE WHERE subject_key IN ('{KEY_A}', '{KEY_B}')"
    assert erasure_count_sql("efn_dwh", [KEY_A]).startswith("SELECT count() FROM efn_dwh.fact_visitor_event")
    for bad in (["x' OR '1'='1"], [], [KEY_A, "B" * 64]):
        with pytest.raises(ValueError):
            erasure_delete_sql("efn_dwh", bad)
    with pytest.raises(ValueError):
        erasure_count_sql("efn_dwh; DROP", [KEY_A])


def test_erasure_source_sql() -> None:
    for sql in (SQL_BEHAVIOR_ERASURES, SQL_DSAR_SUBJECT_KEYS):
        assert "SELECT *" not in sql.upper()
        assert "behavior.subject_erasure" in sql
        assert "%(limit)s" in sql
    assert "%(cursor_ts)s" in SQL_BEHAVIOR_ERASURES and "%(request_id)s" in SQL_DSAR_SUBJECT_KEYS


def test_valid_subject_keys_dedups_and_counts_invalid() -> None:
    assert valid_subject_keys([KEY_B, KEY_A, KEY_B, "nope", None]) == ([KEY_A, KEY_B], 2)


class FakeSubjectStore:
    def __init__(self, rows: dict[str, int], stuck: bool = False) -> None:
        self.rows = rows
        self.stuck = stuck
        self.deletes: list[list[str]] = []

    def count(self, keys: Sequence[str]) -> int:
        return sum(self.rows.get(k, 0) for k in keys)

    def delete(self, keys: Sequence[str]) -> None:
        self.deletes.append(list(keys))
        if not self.stuck:
            for k in keys:
                self.rows.pop(k, None)


class FakeWatermarks:
    def __init__(self, wm: datetime | None = None) -> None:
        self.wm = wm
        self.sets: list[datetime] = []

    def get(self, job: str) -> datetime | None:
        assert job == JOB_ERASURE
        return self.wm

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
        self.wm = watermark
        self.sets.append(watermark)


class FakeErasureSource:
    def __init__(self, pages: list[list[dict[str, Any]]], by_request: dict[str, list[str]] | None = None) -> None:
        self.pages = pages
        self.by_request = by_request or {}
        self.cursors: list[Cursor] = []

    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]:
        self.cursors.append(cursor)
        return self.pages.pop(0) if self.pages else []

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"subject_key": k} for k in self.by_request.get(params["request_id"], [])]


def _erasure_row(i: int, key: str, ts: datetime) -> dict[str, Any]:
    return {"erasure_id": str(i), "subject_key": key, "created_at": ts, "_cursor_ts": ts, "_cursor_key": str(i)}


def test_run_behavior_erasure_deletes_verifies_and_advances() -> None:
    t1 = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
    t2 = t1 + timedelta(minutes=1)
    wm0 = t1 - timedelta(days=3)
    source = FakeErasureSource([[_erasure_row(1, KEY_A, t1), _erasure_row(2, KEY_B, t2), _erasure_row(3, "bad", t2)]])
    store = FakeSubjectStore({KEY_A: 5, KEY_C: 7})
    marks = FakeWatermarks(wm0)
    result = run_behavior_erasure(source, store, marks, timedelta(hours=24), 100, 10)
    assert store.deletes == [[KEY_A, KEY_B]]
    assert store.rows == {KEY_C: 7}  # subjek lain tidak tersentuh
    assert result.rows_deleted == 5 and result.subjects == 2 and result.invalid == 1
    assert result.excluded == frozenset({KEY_A, KEY_B})
    assert marks.wm == t2
    # Mulai dari watermark - recheck (bukan watermark): kunci 24 jam terakhir dicek ulang setiap siklus.
    assert source.cursors[0].ts == wm0 - timedelta(hours=24)


def test_run_behavior_erasure_no_rows_no_delete() -> None:
    t1 = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
    store = FakeSubjectStore({})
    result = run_behavior_erasure(
        FakeErasureSource([[_erasure_row(1, KEY_A, t1)]]), store, FakeWatermarks(), timedelta(hours=1), 100, 10
    )
    assert store.deletes == [] and result.rows_deleted == 0


def test_run_behavior_erasure_pending_keeps_watermark() -> None:
    t1 = datetime(2026, 11, 20, 1, 0, tzinfo=UTC)
    marks = FakeWatermarks()
    with pytest.raises(ErasurePending):
        run_behavior_erasure(
            FakeErasureSource([[_erasure_row(1, KEY_A, t1)]]),
            FakeSubjectStore({KEY_A: 3}, stuck=True),
            marks,
            timedelta(hours=1),
            100,
            10,
        )
    assert marks.sets == []


def test_erase_keys_empty_is_noop() -> None:
    store = FakeSubjectStore({KEY_A: 1})
    assert erase_keys([], store) == 0 and store.deletes == []


class FakePublisher:
    def __init__(self) -> None:
        self.sent: list[OutgoingMessage] = []

    def publish(self, messages: Sequence[OutgoingMessage]) -> None:
        self.sent.extend(messages)


class EmptyMart:
    def count(self, table: str, column: str, member_hash: str) -> int:
        return 0

    def delete(self, table: str, column: str, member_hash: str) -> None:  # pragma: no cover - tidak dipanggil
        raise AssertionError


RID = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


def test_dsar_hook_blocks_completion_until_behavior_erased() -> None:
    request = ErasureRequest(RID, "d" * 64)
    stuck = FakeSubjectStore({KEY_A: 2}, stuck=True)
    hook = BehaviorDsarHook(FakeErasureSource([], {RID: [KEY_A, KEY_B]}), stuck)
    publisher = FakePublisher()
    result = erase_one(request, EmptyMart(), publisher, [hook])
    assert result.status == "pending" and publisher.sent == []

    ok_store = FakeSubjectStore({KEY_A: 2})
    hook = BehaviorDsarHook(FakeErasureSource([], {RID: [KEY_A, KEY_B]}), ok_store)
    result = erase_one(request, EmptyMart(), publisher, [hook])
    assert result.status == "completed" and len(publisher.sent) == 1
    assert ok_store.rows == {}


def test_dsar_hook_without_behavior_keys_completes() -> None:
    hook = BehaviorDsarHook(FakeErasureSource([], {}), FakeSubjectStore({KEY_A: 1}))
    assert hook.erase(RID) == 0


# ---------------------------------------------------------------- konfigurasi
@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    return {"APP_ENV": "production", "PG_SSLROOTCERT": str(ca), "CH_CA_FILE": str(ca), "AMQP_TLS_CA_FILE": str(ca)}


def test_settings_behavior_defaults(tls_env: dict[str, str]) -> None:
    s = Settings.from_env(tls_env)
    assert s.behavior_enabled is False
    assert (s.behavior_agg_days, s.behavior_k_min, s.behavior_erasure_recheck_hours) == (3, 10, 24)
    s = Settings.from_env({**tls_env, "ETL_ENABLE_BEHAVIOR": "true"})
    assert s.behavior_enabled is True and s.odoo_enabled is False


@pytest.mark.parametrize(
    "override",
    [
        {"ETL_BEHAVIOR_AGG_DAYS": "8"},
        {"ETL_BEHAVIOR_K_MIN": "1"},
        {"ETL_BEHAVIOR_AGG_INTERVAL_SECONDS": "60"},
        {"ETL_BEHAVIOR_ERASURE_BATCH_SIZE": "5000"},
    ],
)
def test_settings_behavior_ranges(tls_env: dict[str, str], override: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, **override})
