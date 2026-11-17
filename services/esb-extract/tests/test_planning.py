"""Perencanaan slice: jendela WIB, rentang backfill, H-1/H-7 + gap fill, resumable."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from efn_esb.planning import (
    WIB,
    SliceTask,
    backfill_tasks,
    date_range,
    in_window,
    incremental_days,
    incremental_tasks,
    scopes_for,
    snapshot_due,
    validate_backfill_range,
    validate_ignore_window,
    wib_today,
)

WINDOW = (time(22, 0), time(5, 0))


def wib(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=WIB)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (wib(2026, 11, 17, 22, 0), True),
        (wib(2026, 11, 17, 23, 59), True),
        (wib(2026, 11, 18, 4, 59), True),
        (wib(2026, 11, 18, 5, 0), False),
        (wib(2026, 11, 17, 12, 0), False),
        (wib(2026, 11, 17, 21, 59), False),
    ],
)
def test_window_wraps_midnight(moment: datetime, expected: bool) -> None:
    assert in_window(moment, WINDOW) is expected
    assert in_window(moment.astimezone(UTC), WINDOW) is expected  # zona input tidak berpengaruh


def test_window_same_day() -> None:
    assert in_window(wib(2026, 11, 17, 10), (time(9), time(17)))
    assert not in_window(wib(2026, 11, 17, 17), (time(9), time(17)))


def test_wib_today_uses_jakarta_date() -> None:
    assert wib_today(datetime(2026, 11, 16, 18, 0, tzinfo=UTC)) == date(2026, 11, 17)  # 01:00 WIB


def test_validate_backfill_range() -> None:
    today = date(2026, 11, 17)
    validate_backfill_range(date(2024, 11, 17), date(2026, 11, 16), today)
    with pytest.raises(ValueError):
        validate_backfill_range(date(2026, 11, 10), date(2026, 11, 1), today)
    with pytest.raises(ValueError):
        validate_backfill_range(date(2026, 11, 1), today, today)
    with pytest.raises(ValueError):
        validate_backfill_range(date(2020, 1, 1), date(2026, 11, 16), today)


def test_ignore_window_free_outside_production() -> None:
    validate_ignore_window(date(2026, 1, 1), date(2026, 11, 16), production=False, allowed=False)


def test_ignore_window_production_requires_flag() -> None:
    with pytest.raises(ValueError) as info:
        validate_ignore_window(date(2026, 11, 16), date(2026, 11, 16), production=True, allowed=False)
    assert "ESB_ALLOW_IGNORE_WINDOW=true" in str(info.value)


def test_ignore_window_production_max_one_day() -> None:
    validate_ignore_window(date(2026, 11, 16), date(2026, 11, 16), production=True, allowed=True)
    for start in (date(2026, 11, 15), date(2024, 11, 17)):
        with pytest.raises(ValueError) as info:
            validate_ignore_window(start, date(2026, 11, 16), production=True, allowed=True)
        assert "maks 1 hari" in str(info.value)


def test_scopes() -> None:
    assert scopes_for("sales", [4, 7]) == ["4", "7"]
    assert scopes_for("payment", [4, 7]) == ["*"]
    assert SliceTask(date(2026, 11, 16), "sales", "7").branch_id == 7
    assert SliceTask(date(2026, 11, 16), "payment", "*").branch_id == 0


def test_backfill_tasks_resume_and_force() -> None:
    days = date_range(date(2026, 11, 14), date(2026, 11, 15))
    done = {("sales", "4", date(2026, 11, 15)): datetime(2026, 11, 16, tzinfo=UTC)}
    tasks = backfill_tasks(["sales", "payment"], days, [4], done)
    assert [(t.day.day, t.entity, t.scope) for t in tasks] == [
        (15, "payment", "*"),
        (14, "sales", "4"),
        (14, "payment", "*"),
    ]
    assert len(backfill_tasks(["sales", "payment"], days, [4], done, force=True)) == 4


def test_incremental_before_ready_hour_only_gap_fill_from_h2() -> None:
    now = wib(2026, 11, 17, 2, 0)
    finished = {date(2026, 11, 16 - i): wib(2026, 11, 16, 4) for i in range(1, 7)}  # H-2..H-7 ada
    assert incremental_days(now, finished, (1, 7), 7, 3) == []


def test_incremental_after_ready_hour_pulls_h1_and_h7_once() -> None:
    now = wib(2026, 11, 17, 3, 30)
    old = wib(2026, 11, 16, 4)
    finished = {date(2026, 11, 17) - timedelta(days=i): old for i in range(2, 8)}
    assert incremental_days(now, finished, (1, 7), 7, 3) == [date(2026, 11, 16), date(2026, 11, 10)]
    # Setelah ditarik hari ini (lewat 03:00), tidak dijadwalkan lagi sampai besok.
    finished[date(2026, 11, 16)] = wib(2026, 11, 17, 3, 10)
    finished[date(2026, 11, 10)] = wib(2026, 11, 17, 3, 11)
    assert incremental_days(wib(2026, 11, 17, 9, 0), finished, (1, 7), 7, 3) == []


def test_incremental_gap_fill() -> None:
    now = wib(2026, 11, 17, 10, 0)
    fresh = wib(2026, 11, 17, 4)
    finished = {date(2026, 11, 16): fresh, date(2026, 11, 10): fresh}
    days = incremental_days(now, finished, (1, 7), 7, 3)
    assert days == [date(2026, 11, d) for d in (15, 14, 13, 12, 11)]


def test_incremental_tasks_per_scope() -> None:
    now = wib(2026, 11, 17, 10, 0)
    fresh = wib(2026, 11, 17, 4)
    checkpoints = {}
    for scope, entity in (("4", "sales"), ("*", "payment")):
        for lag in range(1, 8):
            checkpoints[(entity, scope, date(2026, 11, 17) - timedelta(days=lag))] = fresh
    del checkpoints[("sales", "4", date(2026, 11, 16))]
    tasks = incremental_tasks(["sales", "payment"], [4, 9], now, checkpoints, (1, 7), 7, 3)
    assert SliceTask(date(2026, 11, 16), "sales", "4") in tasks
    assert all(t.scope == "9" for t in tasks if t.scope != "4")  # toko 9 belum pernah ditarik -> 7 hari
    assert len([t for t in tasks if t.scope == "9"]) == 7
    assert not [t for t in tasks if t.entity == "payment"]


def test_snapshot_due() -> None:
    today = date(2026, 11, 17)
    assert snapshot_due("master", {}, today)
    assert not snapshot_due("master", {("master", "*", today): datetime(2026, 11, 17, tzinfo=UTC)}, today)
