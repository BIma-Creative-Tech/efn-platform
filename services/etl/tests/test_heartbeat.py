import logging
from datetime import UTC, datetime

import pytest

from efn_etl.heartbeat import HEARTBEAT_MARKER, JobRun, heartbeat_line, make_heartbeat

TS = datetime(2026, 10, 7, 5, 0, 1, 250000, tzinfo=UTC)


def test_heartbeat_line_is_logfmt() -> None:
    line = heartbeat_line(JobRun("sales_fact_pos", TS, "ok", rows=12, pages=1, duration_ms=40, watermark=TS))
    assert line == (
        f"{HEARTBEAT_MARKER} job=sales_fact_pos status=ok rows=12 pages=1 duration_ms=40 "
        "watermark=2026-10-07T05:00:01.250000Z ts=2026-10-07T05:00:01.250000Z"
    )
    assert "watermark=-" in heartbeat_line(JobRun("cycle", TS, "ok"))


@pytest.mark.parametrize("job", ["", "Sales", "a b", "x\nEFN_ETL_HEARTBEAT job=y status=ok", "j" * 65])
def test_invalid_job_name_rejected(job: str) -> None:
    with pytest.raises(ValueError):
        JobRun(job, TS, "ok")


def test_invalid_status_rejected() -> None:
    with pytest.raises(ValueError):
        JobRun("cycle", TS, "fine")


class Store:
    def __init__(self, fail: bool = False) -> None:
        self.runs: list[JobRun] = []
        self.fail = fail

    def record(self, run: JobRun) -> None:
        if self.fail:
            raise ConnectionError("clickhouse down")
        self.runs.append(run)


def test_emit_logs_and_records(caplog: pytest.LogCaptureFixture) -> None:
    store = Store()
    emit = make_heartbeat(store)
    with caplog.at_level(logging.INFO, logger="efn_etl.heartbeat"):
        emit(JobRun("products", TS, "ok"))
        emit(JobRun("products", TS, "error"))
    assert [r.status for r in store.runs] == ["ok", "error"]
    levels = [r.levelno for r in caplog.records if r.getMessage().startswith(HEARTBEAT_MARKER)]
    assert levels == [logging.INFO, logging.WARNING]


def test_store_failure_does_not_raise_and_log_still_emitted(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="efn_etl.heartbeat"):
        make_heartbeat(Store(fail=True))(JobRun("cycle", TS, "ok"))
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith(HEARTBEAT_MARKER) for m in messages)
    assert any("tidak tersimpan" in m for m in messages)


def test_emit_without_store(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="efn_etl.heartbeat"):
        make_heartbeat(None)(JobRun("cycle", TS, "ok"))
    assert any(r.getMessage().startswith(HEARTBEAT_MARKER) for r in caplog.records)
