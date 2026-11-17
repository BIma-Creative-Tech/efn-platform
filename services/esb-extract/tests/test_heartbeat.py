"""Heartbeat: format logfmt, anti log injection, kesegaran file, mode idle tidak memanggil apa pun."""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from efn_esb.__main__ import main
from efn_esb.heartbeat import heartbeat_line, is_fresh, touch
from efn_esb.records import RunRecord

TS = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)


def test_heartbeat_line() -> None:
    run = RunRecord("pb", "run", TS, uuid.uuid4(), calls=10, rows=200, errors=0, status="ok", slices=3)
    assert heartbeat_line(run, 1500, TS) == (
        "EFN_ESB_HEARTBEAT brand=pb mode=run status=ok calls=10 rows=200 errors=0 slices=3 duration_ms=1500 "
        "ts=2026-11-17T03:00:00Z"
    )


def test_heartbeat_line_rejects_injection() -> None:
    run = RunRecord("pb status=ok\nX", "run", TS, uuid.uuid4())
    assert "brand=invalid" in heartbeat_line(run, 0, TS)


def test_run_record_status_validated() -> None:
    with pytest.raises(ValueError):
        RunRecord("pb", "run", TS, uuid.uuid4(), status="bogus").row(TS)


def test_freshness(tmp_path: Path) -> None:
    file = tmp_path / "hb"
    assert not is_fresh(str(file), 60)
    touch(str(file))
    assert is_fresh(str(file), 60)
    old = file.stat().st_mtime - 1000
    os.utime(file, (old, old))
    assert not is_fresh(str(file), 60)


def test_disabled_cli_exits_zero_without_config(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in list(os.environ):
        if key.startswith(("ESB_", "CH_")):
            monkeypatch.delenv(key, raising=False)
    assert main(["probe", "--brand", "pb"]) == 0
    assert "ESB dinonaktifkan" in capsys.readouterr().out
