"""Backtest pada data sintetis dengan lonjakan Lebaran + minggu gajian + Ramadan + akhir pekan (ADR-046 §3.6, G48):
WAPE model G44 harus lebih baik dari baseline v1 (rata-rata 56 hari, efn_demand_forecast G28) dan seasonal naive.

Memakai statsforecast sungguhan (AutoETS); kompilasi JIT numba pertama kali memakan waktu (marker `slow`). Membuktikan
MEKANISME, bukan akurasi produksi (R-T21: histori nyata supermarket belum ada, Q-SM27).
"""

from datetime import date

import numpy as np
import pytest
from conftest import lebaran_entries, synthetic_sales

from efn_forecast.calendar import CalendarConfig
from efn_forecast.models import StatsforecastRunner
from efn_forecast.pipeline import (
    Panel,
    PipelineConfig,
    backtest,
    build_panel,
    forecast_lines,
    forecast_panel,
    truncate,
)

START = date(2024, 1, 1)
# Holdout 28 hari = 2 Mar..29 Mar 2026: Ramadan, gajian awal & akhir bulan, H-14..H+7 Lebaran 20 Mar 2026.
END = date(2026, 3, 29)
LEVELS = {101: 40.0, 102: 18.0, 103: 65.0, 104: 9.0}

pytestmark = pytest.mark.slow


def _panel() -> Panel:
    sales = synthetic_sales(START, END, LEVELS, warehouse_id=2, seed=44)
    products = dict.fromkeys(LEVELS, "Kering / Sembako")
    return build_panel(START, END, sales, [], products, {2: "SMD01"})


def test_backtest_beats_mean56_and_seasonal_naive_through_lebaran() -> None:
    panel = _panel()
    cfg = PipelineConfig(candidates=("AutoETS",), cv_windows=0, fit_days=365)
    result = backtest(panel, lebaran_entries(), CalendarConfig(), cfg, StatsforecastRunner(), days=28)
    assert result.series == len(LEVELS)
    assert result.wape is not None and result.wape_mean56 is not None and result.wape_seasonal_naive is not None
    assert result.wape < result.wape_mean56 * 0.8, result
    assert result.wape < result.wape_seasonal_naive, result


def test_forecast_lines_capture_lebaran_peak_with_valid_intervals() -> None:
    panel = _panel()
    cut = date(2026, 3, 1)  # ramal 2..29 Mar
    train = truncate(panel, cut)
    cfg = PipelineConfig(candidates=("AutoETS",), cv_windows=0, fit_days=365)
    result = forecast_panel(train, lebaran_entries(), CalendarConfig(), cfg, StatsforecastRunner())
    assert result.failed_series == 0 and result.model_counts.get("AutoETS") == len(LEVELS)
    lines = forecast_lines(result.forecasts, cut)
    assert all(line["p10"] <= line["yhat"] <= line["p90"] and line["p10"] >= 0 for line in lines)
    big = {line["date"]: line["yhat"] for line in lines if line["product_id"] == 103}
    peak = np.mean([big[f"2026-03-{d:02d}"] for d in (17, 18, 19)])  # H-3..H-1 (Selasa-Kamis, faktor nyata 2,6)
    early = np.mean([big[f"2026-03-{d:02d}"] for d in (10, 11, 12)])  # H-10..H-8 (Selasa-Kamis, faktor nyata 1,4)
    lebaran_day = big["2026-03-20"]
    assert peak > 1.3 * early  # bentuk lonjakan menjelang Lebaran tertangkap (faktor disusutkan, 2 Lebaran histori)
    assert lebaran_day < peak  # hari H toko sepi
