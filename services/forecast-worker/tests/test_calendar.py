"""Kalender gajian, libur nasional (library holidays ID), Lebaran/Ramadan, gabungan entri Odoo (ADR-046 §3.2)."""

from datetime import date, timedelta

import numpy as np
import pytest

from efn_forecast.calendar import (
    LEBARAN_BUCKETS,
    CalendarConfig,
    CalendarEntry,
    build_features,
    build_spec,
    for_warehouse,
    in_payday_window,
    lebaran_bucket,
    lebaran_days,
    lib_entries,
    merge_entries,
    seed_entries,
)


def _days(year: int, month: int, tail: int = 7, head: int = 7) -> list[int]:
    out = []
    d = date(year, month, 1)
    while d.month == month:
        if in_payday_window(d, tail, head):
            out.append(d.day)
        d += timedelta(days=1)
    return out


def test_payday_window_default_seven_plus_seven() -> None:
    assert _days(2026, 1) == [*range(1, 8), *range(25, 32)]  # 31 hari
    assert _days(2026, 4) == [*range(1, 8), *range(24, 31)]  # 30 hari
    assert _days(2026, 2) == [*range(1, 8), *range(22, 29)]  # 28 hari
    assert _days(2028, 2) == [*range(1, 8), *range(23, 30)]  # kabisat
    assert not in_payday_window(date(2026, 1, 8)) and not in_payday_window(date(2026, 1, 24))


def test_payday_window_configurable_and_validated() -> None:
    assert _days(2026, 1, tail=3, head=2) == [1, 2, 29, 30, 31]
    assert _days(2026, 1, tail=0, head=0) == []
    with pytest.raises(ValueError):
        in_payday_window(date(2026, 1, 1), -1, 7)


def test_lib_entries_indonesia_has_lebaran_and_national_holidays() -> None:
    entries = lib_entries([2026, 2027])
    assert entries and all(e.source == "lib" and e.kind in ("holiday", "cuti_bersama") for e in entries)
    assert all(1 <= len(e.name) <= 64 for e in entries)
    days = {e.day for e in entries if e.kind == "holiday"}
    assert date(2026, 8, 17) in days  # Hari Kemerdekaan
    assert date(2026, 1, 1) in days
    syawal = [d for d in lebaran_days(entries) if d.year == 2026]
    # Idul Fitri 1447 H: 20-21 Mar 2026 (SKB); library boleh berbeda +-1 hari (perkiraan hisab) -> override manual.
    assert syawal and date(2026, 3, 19) <= syawal[0] <= date(2026, 3, 22)
    # Tanpa duplikat kunci (date, kind, name)
    assert len({e.key for e in entries}) == len(entries)


def test_merge_respects_odoo_disabled_and_manual_entries() -> None:
    lib = [
        CalendarEntry(date(2027, 1, 1), "holiday", "Tahun Baru Masehi"),
        CalendarEntry(date(2027, 3, 9), "cuti_bersama", "Cuti Bersama Idul Fitri"),
    ]
    odoo = [
        CalendarEntry(date(2027, 3, 9), "cuti_bersama", "Cuti Bersama Idul Fitri", "lib", active=False),
        CalendarEntry(date(2027, 5, 2), "custom", "Ulang tahun kota", "manual", warehouse_id=7),
        CalendarEntry(date(2027, 6, 1), "weekend", "bukan kind upsert", "manual"),
    ]
    merged = merge_entries(lib, odoo)
    keys = {(e.day, e.kind) for e in merged}
    assert (date(2027, 1, 1), "holiday") in keys
    assert (date(2027, 3, 9), "cuti_bersama") not in keys  # dinonaktifkan user -> tetap nonaktif
    assert (date(2027, 6, 1), "weekend") not in keys
    assert [e.day for e in for_warehouse(merged, 7) if e.kind == "custom"] == [date(2027, 5, 2)]
    assert not [e for e in for_warehouse(merged, 8) if e.kind == "custom"]


def test_lebaran_override_priority() -> None:
    entries = [
        CalendarEntry(date(2027, 3, 10), "holiday", "Hari Raya Idul Fitri"),
        CalendarEntry(date(2027, 3, 11), "holiday", "Hari kedua Hari Raya Idul Fitri"),
        CalendarEntry(date(2028, 2, 27), "holiday", "Eid al-Fitr (estimated)"),
    ]
    assert lebaran_days(entries) == (date(2027, 3, 10), date(2028, 2, 27))
    # Jendela manual: 1 Syawal = libur pertama di dalam jendela manual
    manual = [CalendarEntry(date(2027, 3, d), "lebaran_window", "Jendela Lebaran", "manual") for d in range(3, 15)]
    manual.append(CalendarEntry(date(2027, 3, 9), "holiday", "Libur lain"))
    assert lebaran_days(entries + manual)[0] == date(2027, 3, 9)
    # Env override menang
    assert lebaran_days(entries, (date(2027, 3, 11),))[0] == date(2027, 3, 11)


def test_spec_ramadan_default_and_manual() -> None:
    entries = [CalendarEntry(date(2027, 3, 10), "holiday", "Hari Raya Idul Fitri")]
    spec = build_spec(entries, CalendarConfig())
    assert len(spec.ramadan) == 30
    assert min(spec.ramadan) == date(2027, 2, 8) and max(spec.ramadan) == date(2027, 3, 9)
    manual = [CalendarEntry(date(2027, 2, 9) + timedelta(days=i), "ramadan", "Ramadan", "manual") for i in range(29)]
    spec2 = build_spec(entries + manual, CalendarConfig())
    assert len(spec2.ramadan) == 29 and date(2027, 2, 8) not in spec2.ramadan
    inactive = [CalendarEntry(date(2027, 3, 10), "holiday", "Hari Raya Idul Fitri", active=False)]
    assert build_spec(inactive, CalendarConfig()).lebaran == ()


def test_features_buckets_weekend_and_no_double_counting() -> None:
    syawal = date(2027, 3, 10)
    entries = [
        CalendarEntry(syawal, "holiday", "Hari Raya Idul Fitri"),
        CalendarEntry(syawal + timedelta(days=1), "cuti_bersama", "Cuti Bersama"),
        CalendarEntry(date(2027, 5, 1), "holiday", "Hari Buruh"),
    ]
    cfg = CalendarConfig(weekend_days=(4, 5))  # Jumat-Sabtu (configurable)
    start = date(2027, 2, 20)
    feats = build_features(start, 120, build_spec(entries, cfg), cfg)
    idx = (syawal - start).days
    assert feats.lebaran_bucket[idx] == 3 and feats.lebaran_bucket[idx - 14] == 0 and feats.lebaran_bucket[idx + 8] == 5
    assert feats.lebaran_bucket[idx + 21] == 5 and feats.lebaran_bucket[idx + 22] == -1  # pasca-Lebaran H+8..H+21
    assert not feats.holiday[idx] and not feats.cuti_bersama[idx + 1]  # diserap bucket Lebaran
    assert feats.holiday[(date(2027, 5, 1) - start).days]
    assert bool(feats.weekend[(date(2027, 2, 26) - start).days])  # Jumat
    assert not bool(feats.weekend[(date(2027, 2, 28) - start).days])  # Minggu
    assert feats.ramadan[idx - 1] and not feats.ramadan[idx]
    assert int(np.sum(feats.payday)) > 0
    part = feats.slice(10, 20)
    assert len(part) == 10 and part.start == start + timedelta(days=10)
    assert lebaran_bucket(syawal + timedelta(days=LEBARAN_BUCKETS[-1][1]), [syawal]) == len(LEBARAN_BUCKETS) - 1


def test_seed_entries_only_lib_kinds_and_years() -> None:
    entries = [
        CalendarEntry(date(2026, 12, 25), "holiday", "Hari Raya Natal"),
        CalendarEntry(date(2027, 12, 25), "holiday", "Hari Raya Natal"),
        CalendarEntry(date(2027, 12, 26), "cuti_bersama", "Cuti Bersama Natal"),
        CalendarEntry(date(2027, 5, 2), "custom", "Manual", "manual"),
        CalendarEntry(date(2025, 12, 25), "holiday", "Hari Raya Natal"),
    ]
    out = seed_entries(entries, [2026, 2027])
    assert [o["date"] for o in out] == ["2026-12-25", "2027-12-25", "2027-12-26"]
    assert all(o["source"] == "lib" and set(o) == {"date", "kind", "name", "source"} for o in out)
