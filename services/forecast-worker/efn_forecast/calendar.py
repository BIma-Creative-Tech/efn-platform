"""Kalender budaya untuk forecast (ADR-046 §3.2, docs/04 §28.3). Logika murni kecuali `lib_entries` (library holidays).

- Minggu gajian DIHITUNG, tidak disimpan: `payday_tail_days` hari terakhir + `payday_head_days` hari pertama bulan
  (default 7 + 7, parameter per company di Odoo; worker memakai env yang sama nilainya, lihat README).
- Libur nasional & cuti bersama: library `holidays` kode `ID` (kategori public -> `holiday`, government ->
`cuti_bersama`)
  + entri Odoo `efn.forecast.calendar` (turun ke ClickHouse `efn_forecast.v_calendar`). Entri `manual` selalu dipakai;
  entri `lib` yang dinonaktifkan user di Odoo juga dibuang dari hasil library lokal (kunci `date, kind, name`).
- Lebaran (1 Syawal) = libur "Idul Fitri" paling awal per tahun; jendela Ramadan default = 30 hari sebelum Lebaran.
  Override: entri manual `lebaran_window` (1 Syawal = libur pertama di dalam jendela manual, bila tidak ada = tanggal
  manual paling awal), entri manual `ramadan` (menggantikan jendela default tahun itu), env FORECAST_LEBARAN_DATES.
"""

from __future__ import annotations

import re
from calendar import monthrange
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

UPSERT_KINDS = ("holiday", "cuti_bersama", "ramadan", "lebaran_window", "custom")
ALL_KINDS = ("weekend", "payday", *UPSERT_KINDS)
SOURCES = ("lib", "manual")
MAX_NAME = 64
_LEBARAN_RE = re.compile(r"idul\s*fitri|eid\s*al[-\s]*fitr", re.IGNORECASE)
# Bucket hari relatif terhadap 1 Syawal (ADR-046 §3.2: days_to_lebaran −14..+7).
LEBARAN_BUCKETS: tuple[tuple[int, int], ...] = ((-14, -8), (-7, -4), (-3, -1), (0, 2), (3, 7))
RAMADAN_DAYS = 30


@dataclass(frozen=True)
class CalendarEntry:
    day: date
    kind: str
    name: str
    source: str = "lib"
    active: bool = True
    warehouse_id: int = 0  # 0 = semua gudang company

    @property
    def key(self) -> tuple[date, str, str]:
        return (self.day, self.kind, self.name)


@dataclass(frozen=True)
class CalendarConfig:
    payday_tail_days: int = 7
    payday_head_days: int = 7
    weekend_days: tuple[int, ...] = (5, 6)  # Sabtu, Minggu (date.weekday)
    lebaran_overrides: tuple[date, ...] = ()


@dataclass(frozen=True)
class CalendarSpec:
    holidays: frozenset[date]
    cuti_bersama: frozenset[date]
    custom: frozenset[date]
    ramadan: frozenset[date]
    lebaran: tuple[date, ...]


@dataclass(frozen=True)
class Features:
    """Fitur kalender harian (panjang sama, mulai `start`)."""

    start: date
    dow: np.ndarray  # 0=Senin..6=Minggu
    weekend: np.ndarray
    payday: np.ndarray
    holiday: np.ndarray
    cuti_bersama: np.ndarray
    custom: np.ndarray
    ramadan: np.ndarray
    lebaran_bucket: np.ndarray  # -1 = di luar jendela, 0..len(LEBARAN_BUCKETS)-1

    def __len__(self) -> int:
        return int(self.dow.shape[0])

    def slice(self, lo: int, hi: int) -> Features:
        return Features(
            start=self.start + timedelta(days=lo),
            dow=self.dow[lo:hi],
            weekend=self.weekend[lo:hi],
            payday=self.payday[lo:hi],
            holiday=self.holiday[lo:hi],
            cuti_bersama=self.cuti_bersama[lo:hi],
            custom=self.custom[lo:hi],
            ramadan=self.ramadan[lo:hi],
            lebaran_bucket=self.lebaran_bucket[lo:hi],
        )


def in_payday_window(day: date, tail_days: int = 7, head_days: int = 7) -> bool:
    """Hari ke-1..head_days atau `tail_days` hari terakhir bulan (Februari 28/29 diperhitungkan)."""
    if tail_days < 0 or head_days < 0:
        raise ValueError("payday tail/head harus >= 0")
    last = monthrange(day.year, day.month)[1]
    return day.day <= head_days or day.day > last - tail_days


def _clean_name(name: object) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME]


def lib_entries(years: Iterable[int]) -> list[CalendarEntry]:
    """Libur nasional (`holiday`) + cuti bersama (`cuti_bersama`) dari library `holidays` (ID), source `lib`.

    Offline (data di paket). Cakupan cuti bersama per tahun berbeda antar versi library -> override manual di Odoo wajib
    diverifikasi dari SKB pemerintah tiap tahun (ADR-046 §2).
    """
    import holidays

    yrs = sorted(set(years))
    out: dict[tuple[date, str, str], CalendarEntry] = {}

    def _add(calendar: object, kind: str) -> None:
        for day in sorted(calendar):  # type: ignore[call-overload]
            for name in calendar.get_list(day):  # type: ignore[attr-defined]
                entry = CalendarEntry(day=day, kind=kind, name=_clean_name(name), source="lib")
                out.setdefault(entry.key, entry)

    try:
        public = holidays.country_holidays("ID", years=yrs, categories=("public",))
    except (TypeError, ValueError, NotImplementedError):  # versi tanpa parameter categories
        public = holidays.country_holidays("ID", years=yrs)
    _add(public, "holiday")
    try:
        government = holidays.country_holidays("ID", years=yrs, categories=("government",))
    except (TypeError, ValueError, NotImplementedError):
        government = {}
    _add(government, "cuti_bersama")
    return sorted(out.values(), key=lambda e: (e.day, e.kind, e.name))


def merge_entries(lib: Iterable[CalendarEntry], odoo: Iterable[CalendarEntry]) -> list[CalendarEntry]:
    """Gabung hasil library lokal dengan entri Odoo (sumber kebenaran).

    - Entri Odoo nonaktif (lib maupun manual) membuang entri dengan kunci sama (date, kind, name).
    - Entri Odoo aktif ditambahkan (manual tidak pernah ditimpa library).
    """
    odoo_list = list(odoo)
    disabled = {e.key for e in odoo_list if not e.active}
    merged: dict[tuple[date, str, str, int], CalendarEntry] = {}
    for e in lib:
        if e.key not in disabled:
            merged[(*e.key, e.warehouse_id)] = e
    for e in odoo_list:
        if e.active and e.kind in UPSERT_KINDS:
            merged[(*e.key, e.warehouse_id)] = e
    return sorted(merged.values(), key=lambda e: (e.day, e.kind, e.name, e.warehouse_id))


def for_warehouse(entries: Iterable[CalendarEntry], warehouse_id: int) -> list[CalendarEntry]:
    return [e for e in entries if e.warehouse_id in (0, warehouse_id)]


def lebaran_days(entries: Sequence[CalendarEntry], overrides: Sequence[date] = ()) -> tuple[date, ...]:
    """1 Syawal per tahun. Prioritas: env override > jendela manual `lebaran_window` > libur Idul Fitri (lib/manual)."""
    by_year: dict[int, date] = {}
    for e in entries:
        if e.kind == "holiday" and _LEBARAN_RE.search(e.name):
            current = by_year.get(e.day.year)
            if current is None or e.day < current:
                by_year[e.day.year] = e.day
    holidays_set = {e.day for e in entries if e.kind == "holiday"}
    manual_windows: dict[int, list[date]] = {}
    for e in entries:
        if e.kind == "lebaran_window" and e.source == "manual":
            manual_windows.setdefault(e.day.year, []).append(e.day)
    for year, days in manual_windows.items():
        inside = sorted(d for d in days if d in holidays_set)
        by_year[year] = inside[0] if inside else min(days)
    for d in overrides:
        by_year[d.year] = d
    return tuple(sorted(by_year.values()))


def build_spec(entries: Sequence[CalendarEntry], cfg: CalendarConfig) -> CalendarSpec:
    active = [e for e in entries if e.active]
    lebaran = lebaran_days(active, cfg.lebaran_overrides)
    manual_ramadan: dict[int, set[date]] = {}
    for e in active:
        if e.kind == "ramadan":
            manual_ramadan.setdefault(e.day.year, set()).add(e.day)
    ramadan: set[date] = set()
    for syawal in lebaran:
        if syawal.year in manual_ramadan:
            continue
        ramadan.update(syawal - timedelta(days=k) for k in range(1, RAMADAN_DAYS + 1))
    for days in manual_ramadan.values():
        ramadan.update(days)
    return CalendarSpec(
        holidays=frozenset(e.day for e in active if e.kind == "holiday"),
        cuti_bersama=frozenset(e.day for e in active if e.kind == "cuti_bersama"),
        custom=frozenset(e.day for e in active if e.kind == "custom"),
        ramadan=frozenset(ramadan),
        lebaran=lebaran,
    )


def lebaran_bucket(day: date, lebaran: Sequence[date]) -> int:
    for syawal in lebaran:
        offset = (day - syawal).days
        for idx, (lo, hi) in enumerate(LEBARAN_BUCKETS):
            if lo <= offset <= hi:
                return idx
    return -1


def build_features(start: date, n: int, spec: CalendarSpec, cfg: CalendarConfig) -> Features:
    days = [start + timedelta(days=i) for i in range(n)]
    dow = np.array([d.weekday() for d in days], dtype=np.int8)
    bucket = np.array([lebaran_bucket(d, spec.lebaran) for d in days], dtype=np.int8)
    in_window = bucket >= 0
    # Efek libur/cuti bersama di sekitar Lebaran diserap bucket Lebaran (tidak dihitung dua kali).
    holiday = np.array([d in spec.holidays for d in days], dtype=bool) & ~in_window
    cuti = np.array([d in spec.cuti_bersama for d in days], dtype=bool) & ~in_window
    return Features(
        start=start,
        dow=dow,
        weekend=np.isin(dow, np.array(cfg.weekend_days, dtype=np.int8)),
        payday=np.array([in_payday_window(d, cfg.payday_tail_days, cfg.payday_head_days) for d in days], dtype=bool),
        holiday=holiday,
        cuti_bersama=cuti,
        custom=np.array([d in spec.custom for d in days], dtype=bool),
        ramadan=np.array([d in spec.ramadan for d in days], dtype=bool),
        lebaran_bucket=bucket,
    )


def seed_entries(entries: Iterable[CalendarEntry], years: Iterable[int]) -> list[dict[str, str]]:
    """Payload `entries` efn_forecast_calendar_upsert (§28.2): hanya hasil library (source lib), tahun diminta."""
    wanted = set(years)
    out = []
    for e in entries:
        if e.source != "lib" or e.day.year not in wanted or e.kind not in ("holiday", "cuti_bersama"):
            continue
        out.append({"date": e.day.isoformat(), "kind": e.kind, "name": e.name, "source": "lib"})
    return out
