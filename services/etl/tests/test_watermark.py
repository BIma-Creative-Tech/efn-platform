from datetime import UTC, datetime, timedelta, timezone

import pytest

from efn_etl.watermark import (
    EPOCH,
    Cursor,
    cursor_after,
    initial_cursor,
    next_watermark,
    normalize_watermark,
    to_utc,
    window_start,
)

WIB = timezone(timedelta(hours=7))
OVERLAP = timedelta(minutes=5)


def test_to_utc_naive_is_assumed_utc() -> None:
    assert to_utc(datetime(2026, 10, 5, 10, 0)) == datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    assert to_utc(datetime(2026, 10, 5, 17, 0, tzinfo=WIB)) == datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def test_window_start_applies_overlap() -> None:
    wm = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    assert window_start(wm, OVERLAP) == datetime(2026, 10, 5, 9, 55, tzinfo=UTC)
    assert window_start(None, OVERLAP) == EPOCH
    assert window_start(datetime(1970, 1, 1, 0, 1, tzinfo=UTC), OVERLAP) == EPOCH


def test_initial_cursor_starts_with_empty_key() -> None:
    cursor = initial_cursor(datetime(2026, 10, 5, 10, 0, tzinfo=UTC), OVERLAP)
    assert cursor == Cursor(ts=datetime(2026, 10, 5, 9, 55, tzinfo=UTC), key="")


def test_normalize_watermark_treats_epoch_as_missing() -> None:
    assert normalize_watermark(None) is None
    assert normalize_watermark(datetime(1970, 1, 1)) is None
    assert normalize_watermark(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=UTC)


def test_cursor_after_uses_last_row() -> None:
    rows: list[dict[str, object]] = [
        {"_cursor_ts": datetime(2026, 10, 5, 10, 0, tzinfo=UTC), "_cursor_key": "a"},
        {"_cursor_ts": datetime(2026, 10, 5, 10, 1, tzinfo=UTC), "_cursor_key": 42},
    ]
    assert cursor_after(rows) == Cursor(ts=datetime(2026, 10, 5, 10, 1, tzinfo=UTC), key="42")
    with pytest.raises(TypeError):
        cursor_after([{"_cursor_ts": "2026-10-05", "_cursor_key": "x"}])


def test_next_watermark_never_moves_backwards() -> None:
    current = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    older: list[dict[str, object]] = [{"_cursor_ts": datetime(2026, 10, 5, 9, 57, tzinfo=UTC)}]
    newer: list[dict[str, object]] = [{"_cursor_ts": datetime(2026, 10, 5, 10, 3)}]
    assert next_watermark(current, older) == current
    assert next_watermark(current, newer) == datetime(2026, 10, 5, 10, 3, tzinfo=UTC)
    assert next_watermark(None, []) is None
