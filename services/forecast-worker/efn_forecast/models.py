"""Pemilihan & eksekusi model per seri (ADR-046 §3.3). Klasifikasi & seleksi = logika murni; statsforecast di adapter.

Klasifikasi seri (setelah penyesuaian kalender, mulai hari penjualan pertama):
- `new`          : tanpa penjualan                                     -> category_mean
- `short`        : histori < FORECAST_MIN_HISTORY_DAYS (default 56)    -> category_mean (dicampur rata-rata
seri sendiri)
- `intermittent` : hari nol > FORECAST_INTERMITTENT_ZERO_RATIO (0,6), ukuran permintaan stabil (CV² < 0,49)
                                                                        -> CrostonOptimized
- `lumpy`        : hari nol > ambang, ukuran permintaan bervariasi      -> ADIDA (klasifikasi Syntetos-Boylan)
- `regular`      : sisanya -> kandidat AutoETS / AutoARIMA, dipilih dengan rolling-origin CV k x 7 hari (MAE).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

MODEL_NAMES = ("AutoETS", "AutoARIMA", "CrostonOptimized", "ADIDA", "category_mean")
CANDIDATE_MODELS = ("AutoETS", "AutoARIMA")
# Default kandidat = AutoETS saja. Benchmark histori sintetis SMD 24 bulan (20 Nov 2026), outlet 400 seri:
# AutoETS CV 4 = WAPE 0,6496 dalam 91 s; AutoARIMA CV 1 = WAPE 0,6499 dalam 1.156 s.
# AutoARIMA tetap bisa dipilih lewat FORECAST_MODELS.
DEFAULT_MODELS = ("AutoETS",)
# G46c: ruang model AutoETS (galat/tren/musim; Z = dipilih otomatis, N = tanpa). Default ZNZ = tanpa tren.
# Benchmark data nyata PB histori 1 tahun (21 Nov 2026, 300 seri reguler, holdout 28 hari): ZZZ 0,170 dtk/seri
# WAPE 0,496; ZNZ 0,021 dtk/seri (8x lebih cepat) WAPE 0,491. Tren linier/teredam diekstrapolasi 28 hari cenderung
# over-forecast saat penjualan turun. FORECAST_ETS_MODEL=ZZZ mengembalikan perilaku lama.
DEFAULT_ETS_MODEL = "ZNZ"
ETS_MODEL_RE = re.compile(r"[AMZ][NAZ][NAMZ]")
# G46c: AutoARIMA dipercepat (benchmark yang sama): default statsforecast pada 365 hari 4,06 dtk/seri WAPE 0,539;
# orde kecil + approximation (CSS) + 182 hari terakhir 0,74 dtk/seri (5,5x lebih cepat) WAPE 0,535. Tetap lebih
# lambat dan tidak lebih akurat dari AutoETS ZNZ -> AutoARIMA bukan default (FORECAST_MODELS).
ARIMA_FIT_DAYS = 182
ARIMA_FAST = {"max_p": 2, "max_q": 2, "max_P": 1, "max_Q": 1, "max_order": 4, "approximation": True}
Z80 = 1.2815515655446004  # kuantil normal 0,9 -> interval 80 % = p10..p90
CV2_LUMPY = 0.49


class ModelRunner(Protocol):
    def fit_predict(self, name: str, y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None]:
        """Ramalan `h` langkah + sigma per langkah (None bila model tidak memberi interval)."""
        ...


@dataclass(frozen=True)
class SeriesClass:
    kind: str
    n_days: int
    zero_ratio: float
    cv2: float


def classify(history: np.ndarray, min_history_days: int = 56, intermittent_zero_ratio: float = 0.6) -> SeriesClass:
    y = np.asarray(history, dtype=float)
    y = y[np.isfinite(y)]
    n = int(y.shape[0])
    if n == 0 or float(np.sum(y)) <= 0:
        return SeriesClass("new", n, 1.0, 0.0)
    zero_ratio = float(np.mean(y <= 0))
    nonzero = y[y > 0]
    mean_nz = float(np.mean(nonzero))
    cv2 = float((np.std(nonzero) / mean_nz) ** 2) if mean_nz > 0 and nonzero.shape[0] > 1 else 0.0
    if n < min_history_days:
        return SeriesClass("short", n, zero_ratio, cv2)
    if zero_ratio > intermittent_zero_ratio:
        return SeriesClass("lumpy" if cv2 >= CV2_LUMPY else "intermittent", n, zero_ratio, cv2)
    return SeriesClass("regular", n, zero_ratio, cv2)


def model_for_class(kind: str) -> str | None:
    return {"intermittent": "CrostonOptimized", "lumpy": "ADIDA", "new": "category_mean", "short": "category_mean"}.get(
        kind
    )


@dataclass(frozen=True)
class Selection:
    model: str
    cv_mae: float | None
    residual_sigma: float | None


def select_model(
    y: np.ndarray, candidates: Sequence[str], runner: ModelRunner, cv_windows: int = 4, step: int = 7
) -> Selection:
    """Rolling-origin CV: untuk tiap jendela i, latih y[:n-(k-i)*step], uji `step` hari berikutnya. MAE terkecil menang
    (seri dengan kandidat tunggal / k = 0 / histori kurang -> kandidat pertama). Kandidat yang gagal dilewati;
    semua gagal
    -> ValueError (pemanggil jatuh ke category_mean)."""
    if not candidates:
        raise ValueError("kandidat model kosong")
    n = int(y.shape[0])
    if cv_windows <= 0 or len(candidates) == 1 or n < step * (cv_windows + 4):
        return Selection(candidates[0], None, None)
    best: Selection | None = None
    for name in candidates:
        errors: list[float] = []
        try:
            for i in range(cv_windows):
                cut = n - (cv_windows - i) * step
                mean, _ = runner.fit_predict(name, y[:cut], step)
                errors.extend(np.abs(np.asarray(mean, dtype=float)[:step] - y[cut : cut + step]).tolist())
        except Exception:  # noqa: S112 - model gagal pada seri ini -> kandidat berikutnya
            continue
        if not errors or not np.all(np.isfinite(errors)):
            continue
        mae = float(np.mean(errors))
        if best is None or best.cv_mae is None or mae < best.cv_mae:
            best = Selection(name, mae, float(np.sqrt(np.mean(np.square(errors)))))
    if best is None:
        raise ValueError("semua kandidat gagal")
    return best


class StatsforecastRunner:
    """Adapter statsforecast (Nixtla, Apache-2.0). Import lambat (numba JIT) -> hanya saat dipakai."""

    def __init__(self, season_length: int = 7, ets_model: str = DEFAULT_ETS_MODEL) -> None:
        if not ETS_MODEL_RE.fullmatch(ets_model):
            raise ValueError(f"model ETS tidak valid: {ets_model}")
        self.season_length = season_length
        self.ets_model = ets_model

    def fit_predict(self, name: str, y: np.ndarray, h: int) -> tuple[np.ndarray, np.ndarray | None]:
        from statsforecast.models import ADIDA, AutoARIMA, AutoETS, CrostonOptimized

        series = np.asarray(y, dtype=float)
        if name in ("AutoETS", "AutoARIMA"):
            if name == "AutoETS":
                model = AutoETS(season_length=self.season_length, model=self.ets_model)
            else:
                series = series[-ARIMA_FIT_DAYS:]
                model = AutoARIMA(season_length=self.season_length, **ARIMA_FAST)
            out = model.forecast(y=series, h=h, level=[80])
            mean = np.asarray(out["mean"], dtype=float)
            sigma = (np.asarray(out["hi-80"], dtype=float) - np.asarray(out["lo-80"], dtype=float)) / (2 * Z80)
            return mean, np.maximum(sigma, 0.0)
        if name == "CrostonOptimized":
            out = CrostonOptimized().forecast(y=series, h=h)
        elif name == "ADIDA":
            out = ADIDA().forecast(y=series, h=h)
        else:
            raise ValueError(f"model tidak dikenal: {name}")
        return np.asarray(out["mean"], dtype=float), None
