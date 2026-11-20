"""Panel (censoring OOS, SKU baru), category_mean, agregasi DC, min/max & service level dari waste."""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest
from conftest import FakeRunner

from efn_forecast.calendar import CalendarConfig
from efn_forecast.pipeline import (
    MinMaxConfig,
    PipelineConfig,
    SeriesForecast,
    aggregate_dc,
    build_panel,
    forecast_lines,
    forecast_panel,
    minmax_lines,
    service_level,
    truncate,
    waste_ratios,
)

START = date(2026, 1, 1)


def test_build_panel_censors_oos_and_adds_new_sku() -> None:
    end = START + timedelta(days=29)
    sales = [(2, 1, START + timedelta(days=i), 4.0) for i in range(30) if i not in (20, 21)]
    sales.append((2, 1, START - timedelta(days=1), 99.0))  # di luar rentang -> diabaikan
    sales.append((3, 1, START, 5.0))  # gudang tidak di-forecast
    sales.append((2, 77, START, 5.0))  # produk tidak layak (nonaktif) -> diabaikan
    stock = [(2, 1, START + timedelta(days=i), 10.0) for i in range(30) if i not in (20, 21)]
    stock += [(2, 1, START + timedelta(days=20), 0.0), (2, 1, START + timedelta(days=21), 0.0)]  # snapshot: habis
    stock += [(2, 5, end, 3.0)]  # SKU baru
    panel = build_panel(START, end, sales, stock, {1: "A", 5: "A"}, {2: "SMD01"})
    by_key = {(s.warehouse_id, s.product_id): s for s in panel.series}
    assert set(by_key) == {(2, 1), (2, 5)}
    s1 = by_key[(2, 1)]
    assert s1.y.dtype == np.float32 and float(s1.y.sum()) == 4.0 * 28
    assert np.flatnonzero(s1.censored).tolist() == [20, 21]  # stok habis -> bukan permintaan nol
    assert not by_key[(2, 5)].censored.any() and float(by_key[(2, 5)].y.sum()) == 0.0
    assert truncate(panel, START + timedelta(days=9)).series[0].y.shape == (10,)
    with pytest.raises(ValueError):
        build_panel(end, START, [], [], {}, {})


def test_category_mean_and_failed_series_fallback() -> None:
    end = START + timedelta(days=99)
    sales = [(2, 1, START + timedelta(days=i), 10.0) for i in range(100)]
    sales += [(2, 2, START + timedelta(days=i), 20.0) for i in range(100)]
    sales += [(2, 3, end - timedelta(days=i), 6.0) for i in range(10)]  # histori 10 hari -> short
    panel = build_panel(START, end, sales, [(2, 4, end, 1.0)], {1: "A", 2: "A", 3: "A", 4: "A"}, {2: "SMD01"})
    cfg = PipelineConfig(candidates=("AutoETS",), cv_windows=0, horizon_days=7)
    result = forecast_panel(panel, [], CalendarConfig(), cfg, FakeRunner())
    fc = {f.product_id: f for f in result.forecasts}
    assert fc[1].model == fc[2].model == "AutoETS"
    assert fc[4].model == "category_mean" and fc[3].model == "category_mean"
    assert float(np.mean(fc[4].yhat)) == pytest.approx(15.0, rel=0.05)  # rata-rata per SKU kategori
    # short: bobot 10/56 seri sendiri (6) + 46/56 kategori (15)
    assert float(np.mean(fc[3].yhat)) == pytest.approx(10 / 56 * 6 + 46 / 56 * 15, rel=0.05)
    assert result.failed_series == 0 and result.model_counts["category_mean"] == 2
    failing = forecast_panel(panel, [], CalendarConfig(), cfg, FakeRunner({"AutoETS"}))
    assert failing.failed_series == 2 and all(f.model == "category_mean" for f in failing.forecasts)
    assert all(np.all(f.yhat >= 0) and np.all(f.sigma >= 0) for f in failing.forecasts)


def _fc(wh: int, code: str, pid: int, level: float, sigma: float = 1.0, model: str = "AutoETS") -> SeriesForecast:
    return SeriesForecast(wh, code, pid, np.full(7, level), np.full(7, sigma), model)


def test_aggregate_dc_sums_outlets_and_own_demand() -> None:
    fcs = [_fc(2, "SMD01", 1, 4.0, 3.0), _fc(3, "SMD02", 1, 6.0, 4.0, "ADIDA"), _fc(1, "SMDDC", 1, 2.0, 0.0)]
    out = {(f.warehouse_code, f.product_id): f for f in aggregate_dc(fcs, {1: "SMDDC"})}
    dc = out[("SMDDC", 1)]
    assert np.allclose(dc.yhat, 12.0) and np.allclose(dc.sigma, 5.0) and dc.model == "ADIDA"
    assert len(out) == 3
    assert aggregate_dc(fcs, {}) == tuple(fcs)
    assert aggregate_dc(fcs, {1: "SMDDC", 9: "SMDDC2"}) == tuple(fcs)  # >1 DC -> tidak diagregasi


def test_service_level_reduced_by_waste() -> None:
    assert service_level(0.95, None, 0.05, 0.85) == 0.95
    assert service_level(0.95, 0.04, 0.05, 0.85) == 0.95
    assert service_level(0.95, 0.075, 0.05, 0.85) == pytest.approx(0.90)
    assert service_level(0.95, 0.5, 0.05, 0.85) == 0.85
    with pytest.raises(ValueError):
        service_level(0.8, 0.1, 0.05, 0.85)


def test_waste_ratios() -> None:
    ratios = waste_ratios([(2, "A", 10.0), (2, "B", 5.0), (3, "A", 0.0)], [(2, "A", 90.0), (3, "A", 10.0)])
    assert ratios == {(2, "A"): pytest.approx(0.1), (2, "B"): 1.0, (3, "A"): 0.0}


def test_minmax_formula() -> None:
    cfg = MinMaxConfig(service_level=0.95, lead_days=2, dc_lead_days=7, review_days=7)
    lines = minmax_lines(
        [_fc(2, "SMD01", 1, 3.0, 1.0), _fc(1, "SMDDC", 1, 3.0, 1.0), _fc(2, "SMD01", 2, 0.0, 0.0)],
        {1: "A", 2: "A"},
        {},
        cfg,
        dc_ids=[1],
    )
    outlet, dc, zero = lines
    z = 1.6448536269514722
    assert outlet["min_qty"] == pytest.approx(round(3 * 2 + z * 1 * 2**0.5, 3))
    assert outlet["max_qty"] == pytest.approx(round(outlet["min_qty"] + 21, 3), abs=0.002)
    assert outlet["basis_daily_demand"] == 3.0 and outlet["service_level"] == 0.95
    assert dc["min_qty"] > outlet["min_qty"]
    assert zero["min_qty"] == zero["max_qty"] == 0.0
    assert set(outlet) == {"warehouse_code", "product_id", "min_qty", "max_qty", "service_level", "basis_daily_demand"}


def test_category_mean_without_peers_uses_own_level() -> None:
    """G46: semua seri pendek (PT baru dimigrasi, 7 hari) -> tanpa pembanding, level seri sendiri (bukan 7/56 x)."""
    end = START + timedelta(days=99)
    sales = [(2, 1, end - timedelta(days=i), 12.0) for i in range(7)]
    sales += [(2, 2, end - timedelta(days=i), 3.0) for i in range(3)]  # < 7 hari
    panel = build_panel(START, end, sales, [], {1: "A", 2: "B"}, {2: "PB01"})
    cfg = PipelineConfig(candidates=("AutoETS",), cv_windows=0, horizon_days=7)
    result = forecast_panel(panel, [], CalendarConfig(), cfg, FakeRunner())
    fc = {f.product_id: f for f in result.forecasts}
    assert fc[1].model == fc[2].model == "category_mean"
    assert float(np.mean(fc[1].yhat)) == pytest.approx(12.0, rel=0.05)
    assert float(np.mean(fc[2].yhat)) == pytest.approx(3.0, rel=0.05)
    assert all(np.all(f.sigma >= 0) for f in result.forecasts)


def test_parallel_jobs_match_sequential_and_log_progress(caplog: pytest.LogCaptureFixture) -> None:
    """G46b: FORECAST_JOBS > 1 (proses spawn per gudang) = hasil identik & urutan sama dengan jobs=1; progres per
    gudang tercatat tanpa angka bisnis."""
    end = START + timedelta(days=99)
    sales = []
    for wh in (2, 3, 4):
        sales += [(wh, 1, START + timedelta(days=i), 10.0 + wh) for i in range(100)]
        sales += [(wh, 2, end - timedelta(days=i), 4.0) for i in range(10)]
    panel = build_panel(START, end, sales, [], {1: "A", 2: "A"}, {2: "PB01", 3: "PB02", 4: "PB03"})
    seq_cfg = PipelineConfig(candidates=("AutoETS",), cv_windows=0, horizon_days=7)
    with caplog.at_level("INFO", logger="efn_forecast.pipeline"):
        seq = forecast_panel(panel, [], CalendarConfig(), seq_cfg, FakeRunner())
    progress = [r.getMessage() for r in caplog.records if r.getMessage().startswith("forecast gudang")]
    assert len(progress) == 3 and progress[-1].startswith("forecast gudang 3/3 wh=4 seri=2")
    par = forecast_panel(panel, [], CalendarConfig(), replace(seq_cfg, jobs=2), FakeRunner())
    assert [(f.warehouse_id, f.product_id, f.model) for f in par.forecasts] == [
        (f.warehouse_id, f.product_id, f.model) for f in seq.forecasts
    ]
    pairs = zip(par.forecasts, seq.forecasts, strict=True)
    assert all(np.allclose(a.yhat, b.yhat) and np.allclose(a.sigma, b.sigma) for a, b in pairs)
    assert par.model_counts == seq.model_counts and par.failed_series == seq.failed_series


def test_inactive_series_zero_not_sent_but_counted_in_backtest() -> None:
    """G46b: seri tanpa jual >= inactive_days (bukan karena OOS) -> ramalan 0 berlabel `inactive`, tidak menjadi baris
    kontrak/min-max; SKU yang sedang OOS (censored) tetap diramal."""
    end = START + timedelta(days=99)
    sales = [(2, 1, START + timedelta(days=i), 10.0) for i in range(100)]
    sales += [(2, 2, START + timedelta(days=i), 8.0) for i in range(60)]  # berhenti terjual 40 hari terakhir
    sales += [(2, 3, START + timedelta(days=i), 6.0) for i in range(60)]  # berhenti karena stok habis
    stock = [(2, 3, START + timedelta(days=i), 5.0) for i in range(60)]
    stock += [(2, 3, START + timedelta(days=i), 0.0) for i in range(60, 100)]
    panel = build_panel(START, end, sales, stock, {1: "A", 2: "A", 3: "A"}, {2: "PB01"})
    cfg = PipelineConfig(candidates=("AutoETS",), cv_windows=0, horizon_days=7)
    result = forecast_panel(panel, [], CalendarConfig(), cfg, FakeRunner())
    fc = {f.product_id: f for f in result.forecasts}
    assert fc[2].model == "inactive" and float(fc[2].yhat.sum()) == 0.0
    assert fc[1].model != "inactive" and fc[3].model != "inactive"
    assert result.model_counts["inactive"] == 1
    lines = forecast_lines(result.forecasts, end)
    assert {ln["product_id"] for ln in lines} == {1, 3}
    mm = minmax_lines(result.forecasts, {1: "A", 2: "A", 3: "A"}, {}, MinMaxConfig(), ())
    assert 2 not in {ln["product_id"] for ln in mm}
    off = forecast_panel(panel, [], CalendarConfig(), replace(cfg, inactive_days=0), FakeRunner())
    assert all(f.model != "inactive" for f in off.forecasts)
