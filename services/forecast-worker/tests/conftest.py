"""Fixture bersama: data sintetis supermarket dengan lonjakan Lebaran, minggu gajian, Ramadan, akhir pekan (G48 akan
memakai pola yang sama untuk seed demo). Deterministik (numpy Generator ber-seed)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import numpy as np
import pytest

from efn_forecast.calendar import CalendarEntry, in_payday_window

# 1 Syawal (SKB pemerintah): 10 Apr 2024, 31 Mar 2025, 20 Mar 2026.
LEBARAN = (date(2024, 4, 10), date(2025, 3, 31), date(2026, 3, 20))
LEBARAN_PROFILE = ((-14, -8, 1.4), (-7, -4, 1.9), (-3, -1, 2.6), (0, 2, 0.5), (3, 7, 1.25))
WEEKEND = {5: 1.3, 6: 1.2}
PAYDAY = 1.35
RAMADAN = 1.15


def true_multiplier(day: date) -> float:
    m = WEEKEND.get(day.weekday(), 1.0)
    if in_payday_window(day, 7, 7):
        m *= PAYDAY
    for syawal in LEBARAN:
        offset = (day - syawal).days
        if -30 <= offset <= -1:
            m *= RAMADAN
        for lo, hi, f in LEBARAN_PROFILE:
            if lo <= offset <= hi:
                m *= f
    return m


def synthetic_sales(
    start: date, end: date, levels: dict[int, float], warehouse_id: int = 2, seed: int = 44, noise: bool = True
) -> list[tuple[int, int, date, float]]:
    rng = np.random.default_rng(seed)
    rows = []
    n = (end - start).days + 1
    for pid, level in levels.items():
        for i in range(n):
            day = start + timedelta(days=i)
            mu = level * (1 + 0.0002 * i) * true_multiplier(day)
            qty = float(rng.poisson(mu)) if noise else mu
            if qty > 0:
                rows.append((warehouse_id, pid, day, qty))
    return rows


def lebaran_entries() -> list[CalendarEntry]:
    return [CalendarEntry(day=d, kind="holiday", name="Hari Raya Idul Fitri", source="manual") for d in LEBARAN]


@pytest.fixture
def make_sales() -> Callable[..., list[tuple[int, int, date, float]]]:
    return synthetic_sales


class FakeRunner:
    """Model palsu cepat: rata-rata 28 hari terakhir, sigma 1. Mencatat panggilan."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.fail = fail or set()

    def fit_predict(self, name: str, y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None]:
        self.calls.append((name, int(y.shape[0]), h))
        if name in self.fail:
            raise RuntimeError("model gagal")
        level = float(np.mean(y[-28:])) if y.size else 0.0
        return np.full(h, level), (np.ones(h) if name in ("AutoETS", "AutoARIMA") else None)


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()
