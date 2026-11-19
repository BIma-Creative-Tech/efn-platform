"""Rentang histori & segmentasi §27.3a/§27.8."""

from datetime import date

import pytest

from efn_mig import segments as s


def test_ranges_follow_contract() -> None:
    r = s.history_ranges(date(2026, 12, 15), legacy_days=400, sales_line_months=24)
    assert r.flow == (date(2025, 11, 10), date(2026, 12, 15))
    assert r.sales_daily == (date(2024, 12, 1), date(2026, 12, 15))
    assert r.sales_line == (date(2024, 12, 1), date(2026, 12, 15))
    assert s.history_ranges(date(2026, 12, 15), 30, 0).sales_line is None


@pytest.mark.parametrize(("days", "months"), [(0, 24), (731, 24), (400, -1), (400, 25)])
def test_ranges_reject_out_of_bounds(days: int, months: int) -> None:
    with pytest.raises(ValueError):
        s.history_ranges(date(2026, 12, 15), days, months)


def test_month_windows_clip_to_cutover() -> None:
    r = s.history_ranges(date(2026, 12, 15), 30, 1)
    windows = r.for_month(date(2026, 12, 1))
    assert windows["legacy_flow_daily"] == (date(2026, 12, 1), date(2026, 12, 15))
    assert windows["legacy_sales_line"] == (date(2026, 12, 1), date(2026, 12, 15))
    older = r.for_month(date(2026, 10, 1))
    assert older["legacy_flow_daily"] is None and older["legacy_sales_line"] is None
    assert older["legacy_sales_daily"] == (date(2026, 10, 1), date(2026, 11, 1))


def test_history_months_and_selection() -> None:
    r = s.history_ranges(date(2026, 1, 1), 10, 0)  # cutover tanggal 1 -> bulan cutover tidak ikut
    months = s.history_months(r)
    assert months[0] == date(2024, 1, 1) and months[-1] == date(2025, 12, 1) and len(months) == 24
    assert s.history_months(r, ["2025-12"]) == [date(2025, 12, 1)]
    with pytest.raises(ValueError):
        s.history_months(r, ["2026-01"])
    with pytest.raises(ValueError):
        s.history_months(r, ["2025/12"])


def test_plan_parts_never_splits_a_branch() -> None:
    assert s.plan_parts({1: 300, 2: 300, 3: 100, 4: 0}, 500) == [[1], [2, 3]]
    assert s.plan_parts({7: 900}, 500) == [[7]]  # satu cabang > batas tetap satu part
    assert s.plan_parts({}, 500) == []
    with pytest.raises(ValueError):
        s.plan_parts({1: 1}, 0)


def test_segment_filename() -> None:
    assert s.segment_filename("P", "PB", date(2025, 1, 1), 1, 1) == "P_PB_202501.efnmig"
    assert s.segment_filename("P", "PB", date(2025, 1, 1), 2, 3) == "P_PB_202501_p2.efnmig"
    assert s.add_months(date(2025, 1, 31), -1) == date(2024, 12, 1)
