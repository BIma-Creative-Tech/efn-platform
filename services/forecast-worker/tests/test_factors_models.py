"""Estimasi faktor kalender (tanpa statsforecast) + klasifikasi & pemilihan model (model palsu)."""

from datetime import date

import numpy as np
import pytest
from conftest import LEBARAN, PAYDAY, FakeRunner, lebaran_entries, synthetic_sales

from efn_forecast.calendar import CalendarConfig, build_features, build_spec
from efn_forecast.factors import Factors, estimate_factors, merge_factors, multipliers, rolling_median
from efn_forecast.models import classify, model_for_class, select_model

START, END = date(2024, 1, 1), date(2026, 3, 1)


def _series() -> tuple[np.ndarray, object]:
    n = (END - START).days + 1
    y = np.zeros(n)
    for _, _, day, qty in synthetic_sales(START, END, {1: 40.0}, noise=False):
        y[(day - START).days] += qty
    cfg = CalendarConfig()
    feats = build_features(START, n, build_spec(lebaran_entries(), cfg), cfg)
    return y, feats


def test_estimate_recovers_payday_weekend_and_lebaran_shape() -> None:
    y, feats = _series()
    f = estimate_factors(y, np.ones_like(y, dtype=bool), feats, shrink_days=0.0)
    assert f.get("payday") == pytest.approx(PAYDAY, rel=0.12)
    assert 1.15 < f.get("weekend") < 1.4
    assert f.get("lebaran2") > 2.0  # H-3..H-1 (2,6x sebenarnya)
    assert f.get("lebaran3") < 0.8  # hari H..H+2 toko sepi
    assert f.get("lebaran2") > f.get("lebaran1") > f.get("lebaran0") > 1.0
    assert f.counts["lebaran2"] == 6  # 3 hari x 2 Lebaran dalam histori


def test_censored_days_ignored_and_shrinkage() -> None:
    y, feats = _series()
    valid = np.ones_like(y, dtype=bool)
    idx = [(d - START).days for d in LEBARAN[:2]]
    y_bad = y.copy()
    for i in idx:
        y_bad[i - 3 : i] = 0.0  # stok habis menjelang Lebaran
        valid[i - 3 : i] = False
    f = estimate_factors(y_bad, valid, feats, shrink_days=0.0)
    assert f.get("lebaran2") == 1.0 and f.counts["lebaran2"] == 0  # tanpa bukti -> netral, bukan nol
    strong = estimate_factors(y, np.ones_like(y, dtype=bool), feats, shrink_days=50.0)
    weak = estimate_factors(y, np.ones_like(y, dtype=bool), feats, shrink_days=0.0)
    assert 1.0 < strong.get("lebaran2") < weak.get("lebaran2")


def test_merge_and_multipliers() -> None:
    _, feats = _series()
    primary = Factors({"payday": 1.5, "lebaran2": 3.0}, {"payday": 100, "lebaran2": 1})
    fallback = Factors({"payday": 1.2, "lebaran2": 2.0}, {"payday": 300, "lebaran2": 6})
    merged = merge_factors(primary, fallback, min_count=3)
    assert merged.get("payday") == 1.5 and merged.get("lebaran2") == 2.0
    mult = multipliers(feats, merged)
    assert mult.shape == (len(feats),)
    assert np.all(mult[feats.payday & (feats.lebaran_bucket < 0)] >= 1.5 - 1e-9)
    with pytest.raises(ValueError):
        estimate_factors(np.ones(3), np.ones(3, dtype=bool), feats)


def test_rolling_median_handles_nan() -> None:
    values = np.array([np.nan, 1.0, 2.0, np.nan, 100.0, 3.0, np.nan])
    med = rolling_median(values, 3)
    assert med.shape == values.shape and np.all(np.isfinite(med))
    assert rolling_median(np.full(4, np.nan), 3).tolist() == [0.0] * 4


@pytest.mark.parametrize(
    ("history", "kind"),
    [
        (np.array([]), "new"),
        (np.zeros(80), "new"),
        (np.ones(30), "short"),
        (np.array([0, 0, 0, 5] * 20, dtype=float), "intermittent"),
        (np.array([0, 0, 0, 1, 0, 0, 0, 25] * 10, dtype=float), "lumpy"),
        (np.full(90, 7.0), "regular"),
    ],
)
def test_classify(history: np.ndarray, kind: str) -> None:
    assert classify(history, 56, 0.6).kind == kind


def test_model_for_class() -> None:
    assert model_for_class("intermittent") == "CrostonOptimized"
    assert model_for_class("lumpy") == "ADIDA"
    assert model_for_class("new") == model_for_class("short") == "category_mean"
    assert model_for_class("regular") is None


class _Biased(FakeRunner):
    def fit_predict(self, name: str, y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None]:
        mean, sigma = super().fit_predict(name, y, h)
        return (mean + 5.0 if name == "AutoETS" else mean), sigma


def test_select_model_by_rolling_cv() -> None:
    y = np.full(120, 10.0)
    runner = _Biased()
    sel = select_model(y, ("AutoETS", "AutoARIMA"), runner, cv_windows=4)
    assert sel.model == "AutoARIMA" and sel.cv_mae == pytest.approx(0.0)
    assert sum(1 for c in runner.calls if c[0] == "AutoETS") == 4  # 4 jendela x 7 hari
    # Kandidat gagal dilewati; semua gagal -> ValueError
    assert select_model(y, ("AutoETS", "AutoARIMA"), FakeRunner({"AutoARIMA"}), 4).model == "AutoETS"
    with pytest.raises(ValueError):
        select_model(y, ("AutoETS", "AutoARIMA"), FakeRunner({"AutoETS", "AutoARIMA"}), 4)
    # k = 0 atau histori pendek -> kandidat pertama tanpa CV
    assert select_model(y, ("AutoARIMA", "AutoETS"), runner, cv_windows=0).cv_mae is None
    assert select_model(y[:30], ("AutoETS", "AutoARIMA"), runner, cv_windows=4).model == "AutoETS"
