import json
import ssl
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from efn_etl.amqp import PikaPublisher, build_connection_params, message_properties
from efn_etl.erasure import (
    ERASURE_TARGETS,
    QUEUE_ERASURE_COMPLETED,
    SQL_PENDING_ERASURES,
    ErasureRequest,
    OutgoingMessage,
    completed_message,
    count_sql,
    delete_sql,
    excluded_hashes,
    parse_request,
    parse_requests,
    run_erasure,
)
from efn_etl.jobs import COMPETITOR_PRICES, LOYALTY_EVENTS, SALES_FACT, Job
from efn_etl.runner import run_job

HASH_A = "a" * 64
HASH_B = "b" * 64
RID = "3f1c2d4e-5a6b-4c7d-8e9f-0a1b2c3d4e5f"


class FakeErasureStore:
    """Mensimulasikan tabel mart: {(table, column): {hash: rows}}; `stuck` = mutation tidak menghapus."""

    def __init__(self, data: dict[str, dict[str, int]], stuck: bool = False, fail: bool = False) -> None:
        self.data = data
        self.stuck = stuck
        self.fail = fail
        self.deletes: list[tuple[str, str, str]] = []

    def count(self, table: str, column: str, member_hash: str) -> int:
        if self.fail:
            raise ConnectionError("clickhouse down")
        return self.data.get(table, {}).get(member_hash, 0)

    def delete(self, table: str, column: str, member_hash: str) -> None:
        self.deletes.append((table, column, member_hash))
        if not self.stuck:
            self.data.get(table, {}).pop(member_hash, None)


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[OutgoingMessage] = []
        self.fail = fail

    def publish(self, messages: Sequence[OutgoingMessage]) -> None:
        if self.fail:
            raise ConnectionError("rabbitmq down")
        self.sent.extend(messages)


def test_pending_sql_reads_only_granted_columns() -> None:
    sql = SQL_PENDING_ERASURES
    assert "SELECT *" not in sql.upper()
    assert "status = 'processing'" in sql
    assert "'clickhouse' = ANY(stores_pending)" in sql
    assert "%(limit)s" in sql
    for forbidden in ("member_id", "member_ref", "_enc", "type"):
        assert forbidden not in sql


def test_parse_request_validates() -> None:
    assert parse_request({"request_id": RID, "member_hash": HASH_A}) == ErasureRequest(RID, HASH_A)
    for bad in (
        {"request_id": "x", "member_hash": HASH_A},
        {"request_id": RID, "member_hash": "6281234567890"},
        {"request_id": RID, "member_hash": HASH_A.upper()},
        {"request_id": RID, "member_hash": None},
    ):
        with pytest.raises(ValueError):
            parse_request(bad)
    # Baris rusak dilewati, baris valid tetap diproses.
    rows = [{"request_id": "x", "member_hash": HASH_A}, {"request_id": RID, "member_hash": HASH_B}]
    assert parse_requests(rows) == [ErasureRequest(RID, HASH_B)]


def test_sql_builders_whitelist_and_literal_safety() -> None:
    assert count_sql("efn_dwh", "competitor_prices", "user_hash", HASH_A) == (
        f"SELECT count() FROM efn_dwh.competitor_prices WHERE user_hash = '{HASH_A}'"
    )
    assert delete_sql("efn_dwh", "loyalty_events", "member_hash", HASH_A) == (
        f"ALTER TABLE efn_dwh.loyalty_events DELETE WHERE member_hash = '{HASH_A}'"
    )
    with pytest.raises(ValueError):
        delete_sql("efn_dwh", "fact_sales_line", "customer_id", HASH_A)  # bukan target
    with pytest.raises(ValueError):
        delete_sql("efn_dwh; DROP TABLE x", "loyalty_events", "member_hash", HASH_A)
    with pytest.raises(ValueError):
        delete_sql("efn_dwh", "loyalty_events", "member_hash", "' OR 1=1 --")
    with pytest.raises(ValueError):
        count_sql("efn_dwh", "loyalty_events", "member_hash", HASH_A + "'")


def test_targets_cover_all_hash_columns_in_jobs() -> None:
    """Setiap job yang memuat hash member punya target erasure (tabel mart + kolom hash)."""
    tables = {table for table, _ in ERASURE_TARGETS}
    for job in (COMPETITOR_PRICES, LOYALTY_EVENTS):
        assert job.identity_field
        assert job.target_table in tables
    assert SALES_FACT.identity_field is None


def test_completed_message_contract() -> None:
    message = completed_message(RID)
    assert message.queue == QUEUE_ERASURE_COMPLETED == "privacy.erasure.completed"
    assert message.message_id == f"erasure:{RID}:clickhouse"
    assert json.loads(message.body) == {
        "schema_version": 2,
        "idempotency_key": f"erasure:{RID}:clickhouse",
        "request_id": RID,
        "store": "clickhouse",
    }
    with pytest.raises(ValueError):
        completed_message("not-a-uuid")


def test_erasure_deletes_verifies_then_publishes() -> None:
    store = FakeErasureStore({"competitor_prices": {HASH_A: 3, HASH_B: 1}, "loyalty_events": {HASH_A: 5}})
    publisher = FakePublisher()
    results = run_erasure([ErasureRequest(RID, HASH_A)], store, publisher)
    assert results[0].status == "completed"
    assert results[0].deleted == {"competitor_prices": 3, "loyalty_events": 5}
    assert store.data["competitor_prices"] == {HASH_B: 1}  # subjek lain tidak tersentuh
    assert [m.message_id for m in publisher.sent] == [f"erasure:{RID}:clickhouse"]


def test_erasure_without_rows_skips_delete_and_still_publishes() -> None:
    """Idempoten: siklus ulang (mis. publish sebelumnya gagal) tidak menjalankan mutation lagi."""
    store = FakeErasureStore({})
    publisher = FakePublisher()
    results = run_erasure([ErasureRequest(RID, HASH_A)], store, publisher)
    assert results[0].status == "completed"
    assert store.deletes == []
    assert len(publisher.sent) == 1


def test_erasure_not_reported_when_rows_remain() -> None:
    store = FakeErasureStore({"loyalty_events": {HASH_A: 2}}, stuck=True)
    publisher = FakePublisher()
    results = run_erasure([ErasureRequest(RID, HASH_A)], store, publisher)
    assert results[0].status == "pending"
    assert publisher.sent == []


def test_erasure_errors_are_isolated_per_request() -> None:
    rid2 = str(uuid.uuid4())
    store = FakeErasureStore({"loyalty_events": {HASH_A: 1}})
    publisher = FakePublisher(fail=True)
    results = run_erasure([ErasureRequest(RID, HASH_A), ErasureRequest(rid2, HASH_B)], store, publisher)
    assert [r.status for r in results] == ["error", "error"]
    down = run_erasure([ErasureRequest(RID, HASH_A)], FakeErasureStore({}, fail=True), FakePublisher())
    assert down[0].status == "error"


def test_excluded_hashes() -> None:
    assert excluded_hashes([ErasureRequest(RID, HASH_A)]) == frozenset({HASH_A})


# ---------------------------------------------------------------- runner: subjek yang sedang dihapus tidak dimuat


class _Source:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetch(self, sql: str, cursor: Any, limit: int) -> list[dict[str, Any]]:
        return [r for r in self.rows if (r["_cursor_ts"], r["_cursor_key"]) > (cursor.ts, cursor.key)][:limit]


class _Sink:
    def __init__(self) -> None:
        self.rows: list[list[Any]] = []

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        self.rows.extend(rows)


class _Store:
    def __init__(self) -> None:
        self.watermark: datetime | None = None

    def get(self, job: str) -> datetime | None:
        return self.watermark

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
        self.watermark = watermark


def test_runner_skips_rows_of_subjects_being_erased_but_advances_watermark() -> None:
    t0 = datetime(2026, 10, 5, tzinfo=UTC)
    rows = [
        {"_cursor_ts": t0, "_cursor_key": "1", "hash": HASH_A},
        {"_cursor_ts": t0 + timedelta(seconds=1), "_cursor_key": "2", "hash": HASH_B},
    ]
    job = Job("t", "platform", "--", "t", ("h",), lambda r: [r["hash"]], identity_field="hash")
    sink, store = _Sink(), _Store()
    result = run_job(job, _Source(rows), sink, store, timedelta(0), 10, 10, frozenset({HASH_A}))
    assert sink.rows == [[HASH_B]]
    assert result.rows == 2
    assert store.watermark == t0 + timedelta(seconds=1)


# ---------------------------------------------------------------- AMQP publisher (tanpa broker)


def test_amqp_params_tls_and_user_id() -> None:
    context = ssl.create_default_context()
    params = build_connection_params("amqps://efn-etl@rabbitmq:5671", "dummy-value", "efn-etl", context)
    assert params.port == 5671
    assert params.ssl_options is not None
    assert params.ssl_options.server_hostname == "rabbitmq"
    assert params.credentials.username == "efn-etl"
    with pytest.raises(ValueError):
        build_connection_params("amqps://efn-etl@rabbitmq:5671", "", "t", None)
    with pytest.raises(ValueError):
        build_connection_params("amqps://efn-etl:pw@rabbitmq:5671", "", "t", context)
    props = message_properties(completed_message(RID), "efn-etl")
    assert props.user_id == "efn-etl"
    assert props.message_id == f"erasure:{RID}:clickhouse"
    assert props.delivery_mode == 2
    assert props.content_type == "application/json"
    with pytest.raises(ValueError):
        PikaPublisher(params, "")
