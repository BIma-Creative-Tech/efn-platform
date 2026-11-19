"""Penyesuaian kalender multiplikatif (ADR-046 §3.2 "regressor kalender"). Logika murni (numpy).

Model: y_t ≈ base_t × Π f_k^{[t ∈ k]}. Faktor diestimasi dengan backfitting (gaya X-11): base = median bergulir terpusat
dari seri yang sudah dibagi faktor saat ini; tiap efek k: f_k = Σ y / Σ (base × faktor lain) pada hari di k (hanya hari
valid, tidak tersensor OOS), lalu disusutkan ke 1 sebesar n/(n + shrink) dan dibatasi [0,2; 5]. Iterasi berulang
menghilangkan bias awal (mis. Ramadan 30 hari ikut terserap median pada iterasi pertama).

Efek: weekend; hari dalam pekan (relatif terhadap weekend/weekday, dinormalisasi rata-rata 1 per kelompok); minggu
gajian; libur nasional; cuti bersama; custom; Ramadan; 5 bucket Lebaran. Faktor diestimasi pada seri AGREGAT (gudang x
kategori, fallback gudang, fallback PT) karena Lebaran hanya muncul sekali setahun per seri.
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from efn_forecast.calendar import LEBARAN_BUCKETS, Features

BINARY_EFFECTS = ("weekend", "payday", "holiday", "cuti_bersama", "custom", "ramadan")
DOW_EFFECTS = tuple(f"dow{i}" for i in range(7))
LEBARAN_EFFECTS = tuple(f"lebaran{i}" for i in range(len(LEBARAN_BUCKETS)))
EFFECTS = ("weekend", *DOW_EFFECTS, "payday", "holiday", "cuti_bersama", "custom", "ramadan", *LEBARAN_EFFECTS)
FACTOR_MIN, FACTOR_MAX = 0.2, 5.0


@dataclass(frozen=True)
class Factors:
    values: Mapping[str, float] = field(default_factory=dict)
    counts: Mapping[str, int] = field(default_factory=dict)

    def get(self, name: str) -> float:
        return float(self.values.get(name, 1.0))


def effect_masks(features: Features) -> dict[str, np.ndarray]:
    masks: dict[str, np.ndarray] = {
        "weekend": features.weekend,
        "payday": features.payday,
        "holiday": features.holiday,
        "cuti_bersama": features.cuti_bersama,
        "custom": features.custom,
        "ramadan": features.ramadan,
    }
    for i in range(7):
        masks[f"dow{i}"] = features.dow == i
    for i in range(len(LEBARAN_BUCKETS)):
        masks[f"lebaran{i}"] = features.lebaran_bucket == i
    return masks


def multipliers(features: Features, factors: Factors) -> np.ndarray:
    mult = np.ones(len(features), dtype=float)
    for name, mask in effect_masks(features).items():
        f = factors.get(name)
        if f != 1.0:
            mult = np.where(mask, mult * f, mult)
    return mult


def rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    """Median bergulir terpusat mengabaikan NaN; tepi diisi nilai terdekat; semua NaN -> median global."""
    n = values.shape[0]
    if n == 0:
        return values.copy()
    half = window // 2
    padded = np.concatenate([np.full(half, np.nan), values, np.full(half, np.nan)])
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(view, axis=1)
        overall = np.nanmedian(values) if np.any(~np.isnan(values)) else 0.0
    med = np.where(np.isnan(med), overall, med)
    return med


def _shrink(raw: float, n: int, shrink: float) -> float:
    value = 1.0 + (raw - 1.0) * (n / (n + shrink)) if shrink > 0 else raw
    return float(min(max(value, FACTOR_MIN), FACTOR_MAX))


def estimate_factors(
    y: np.ndarray,
    valid: np.ndarray,
    features: Features,
    shrink_days: float = 5.0,
    dow_shrink_days: float = 20.0,
    iterations: int = 4,
    window: int = 29,
) -> Factors:
    """Estimasi faktor kalender dari satu seri (umumnya agregat). `valid` = hari tidak tersensor."""
    y = np.asarray(y, dtype=float)
    valid = np.asarray(valid, dtype=bool) & np.isfinite(y)
    if y.shape[0] != len(features):
        raise ValueError("panjang seri != panjang fitur")
    masks = effect_masks(features)
    values = dict.fromkeys(EFFECTS, 1.0)
    counts = {name: int((masks[name] & valid).sum()) for name in EFFECTS}

    def current_mult() -> np.ndarray:
        return multipliers(features, Factors(values))

    for _ in range(max(iterations, 1)):
        mult = current_mult()
        deseason = np.where(valid, y / mult, np.nan)
        base = rolling_median(deseason, window)
        usable = valid & (base > 0)
        for name in EFFECTS:
            mask = masks[name] & usable
            n = int(mask.sum())
            if n == 0:
                values[name] = 1.0
                continue
            mult = current_mult()
            own = values[name]
            others = mult / np.where(masks[name], own, 1.0)
            denom = float(np.sum(base[mask] * others[mask]))
            if denom <= 0:
                values[name] = 1.0
                continue
            raw = float(np.sum(y[mask])) / denom
            values[name] = _shrink(raw, n, dow_shrink_days if name in DOW_EFFECTS else shrink_days)
        _normalize_dow(values, features)
    return Factors(values=dict(values), counts=counts)


def _normalize_dow(values: dict[str, float], features: Features) -> None:
    """Rata-rata faktor hari dalam kelompok weekend dan weekday = 1 (level kelompok dibawa faktor `weekend`)."""
    weekend_days = sorted({int(d) for d, w in zip(features.dow, features.weekend, strict=True) if w})
    groups = [weekend_days, [d for d in range(7) if d not in weekend_days]]
    for group in groups:
        if not group:
            continue
        mean = float(np.mean([values[f"dow{d}"] for d in group]))
        if mean > 0:
            for d in group:
                values[f"dow{d}"] = values[f"dow{d}"] / mean


def merge_factors(primary: Factors, fallback: Factors, min_count: int) -> Factors:
    """Efek dengan bukti < `min_count` hari di `primary` memakai nilai `fallback` (agregat lebih luas)."""
    values: dict[str, float] = {}
    counts: dict[str, int] = {}
    for name in EFFECTS:
        if int(primary.counts.get(name, 0)) >= min_count:
            values[name] = primary.get(name)
            counts[name] = int(primary.counts.get(name, 0))
        else:
            values[name] = fallback.get(name)
            counts[name] = int(fallback.counts.get(name, 0))
    return Factors(values=values, counts=counts)
