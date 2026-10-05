from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from efn_etl.jobs import Job
from efn_etl.runner import run_job, run_once
from efn_etl.watermark import Cursor

OVERLAP = timedelta(minutes=5)
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


# Mensimulasikan query keyset `(ts, key) > cursor ORDER BY ts, key LIMIT n`.
class FakeSource:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = sorted(rows, key=lambda r: (r["_cursor_ts"], r["_cursor_key"]))
        self.cursors: list[Cursor] = []

    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]:
        self.cursors.append(cursor)
        matching = [r for r in self.rows if (r["_cursor_ts"], r["_cursor_key"]) > (cursor.ts, cursor.key)]
        return matching[:limit]


class FakeSink:
    def __init__(self, fail: bool = False) -> None:
        self.inserted: list[list[Any]] = []
        self.fail = fail

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if self.fail:
            raise ConnectionError("clickhouse down")
        self.inserted.extend(rows)


class FakeStore:
    def __init__(self, watermark: datetime | None = None) -> None:
        self.watermark = watermark
        self.sets: list[datetime] = []

    def get(self, job: str) -> datetime | None:
        return self.watermark

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None:
        self.watermark = watermark
        self.sets.append(watermark)


def _job(transform=None) -> Job:
    return Job(
        name="t",
        source="platform",
        sql="-- fake",
        target_table="t",
        columns=("id",),
        transform=transform or (lambda row: [row["_cursor_key"]]),
    )


def _rows(n: int, same_ts: bool = False) -> list[dict[str, Any]]:
    return [{"_cursor_ts": T0 if same_ts else T0 + timedelta(seconds=i), "_cursor_key": f"{i:04d}"} for i in range(n)]


def test_pages_until_exhausted_and_advances_watermark() -> None:
    source, sink, store = FakeSource(_rows(25)), FakeSink(), FakeStore()
    result = run_job(_job(), source, sink, store, OVERLAP, batch_size=10, max_pages=100)
    assert result.rows == 25
    assert result.pages == 3
    assert len(sink.inserted) == 25
    assert store.watermark == T0 + timedelta(seconds=24)


def test_identical_timestamps_do_not_loop_forever() -> None:
    source, sink, store = FakeSource(_rows(35, same_ts=True)), FakeSink(), FakeStore()
    result = run_job(_job(), source, sink, store, OVERLAP, batch_size=10, max_pages=100)
    assert result.rows == 35
    assert [c.key for c in source.cursors] == ["", "0009", "0019", "0029"]


def test_rerun_starts_from_watermark_minus_overlap() -> None:
    store = FakeStore(watermark=T0 + timedelta(minutes=10))
    source = FakeSource(_rows(3))
    run_job(_job(), source, FakeSink(), store, OVERLAP, batch_size=10, max_pages=100)
    assert source.cursors[0] == Cursor(ts=T0 + timedelta(minutes=5), key="")


def test_sink_failure_does_not_advance_watermark() -> None:
    store = FakeStore()
    sink = FakeSink(fail=True)
    results = run_once(
        [_job()],
        {"platform": FakeSource(_rows(5))},
        sink,
        store,
        OVERLAP,
        10,
        100,
    )
    assert results[0].skipped == "error"
    assert store.sets == []


def test_max_pages_limits_run() -> None:
    store = FakeStore()
    result = run_job(_job(), FakeSource(_rows(50)), FakeSink(), store, OVERLAP, batch_size=10, max_pages=2)
    assert result.rows == 20
    assert store.watermark == T0 + timedelta(seconds=19)


def test_non_hash_identity_aborts_page_fail_closed() -> None:
    """Satu baris dengan identitas bukan hash -> seluruh halaman tidak dimuat dan watermark tidak maju."""

    def transform(row: dict[str, Any]) -> list[Any]:
        if row["_cursor_key"] == "0003":
            raise ValueError("user_hash bukan hash")
        return [row["_cursor_key"]]

    sink, store = FakeSink(), FakeStore()
    results = run_once([_job(transform)], {"platform": FakeSource(_rows(5))}, sink, store, OVERLAP, 10, 10)
    assert results[0].skipped == "error"
    assert sink.inserted == []
    assert store.sets == []
