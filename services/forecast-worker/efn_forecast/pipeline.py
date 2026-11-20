"""Pipeline forecast per PT (ADR-046 §3.3–§3.6). Logika murni: input baris data (dari ClickHouse atau
sintetis), keluaran
ramalan per seri, baris kontrak §28.2, backtest WAPE, dan min/max. Model dieksekusi lewat `ModelRunner` (injeksi).

Alur per gudang:
1. Panel harian SKU x gudang (float32), hari OOS ditandai *censored* (stok akhir hari <= 0 padahal SKU ber-stok dalam
   14 hari sebelumnya) -> bukan permintaan nol.
2. Faktor kalender diestimasi pada agregat gudang x kategori (fallback gudang, lalu PT) -> seri dibagi faktor.
3. Model per seri pada seri yang sudah disesuaikan (lihat models.py); hasil dikali faktor hari depan.
4. p10/p90 = yhat ∓ z0,9 · sigma (dipotong di 0). DC (FORECAST_DC_CODES) = permintaan langsung DC + Σ ramalan outlet.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from multiprocessing import get_context
from statistics import NormalDist

import numpy as np

from efn_forecast.calendar import CalendarConfig, CalendarEntry, build_features, build_spec, for_warehouse
from efn_forecast.contract import SERVICE_LEVEL_MAX, SERVICE_LEVEL_MIN
from efn_forecast.factors import Factors, estimate_factors, merge_factors, multipliers
from efn_forecast.models import DEFAULT_MODELS, Z80, ModelRunner, classify, model_for_class, select_model

log = logging.getLogger(__name__)
# G46b: label internal seri tidak aktif (ramalan 0). TIDAK dikirim ke Odoo (forecast_lines melewatinya), tetap dihitung
# di backtest agar WAPE jujur.
INACTIVE = "inactive"


@dataclass(frozen=True)
class PipelineConfig:
    horizon_days: int = 28
    min_history_days: int = 56
    intermittent_zero_ratio: float = 0.6
    candidates: tuple[str, ...] = DEFAULT_MODELS
    cv_windows: int = 4
    fit_days: int = 365
    shrink_days: float = 5.0
    factor_min_count: int = 3
    censor_lookback_days: int = 14
    level_days: int = 28
    jobs: int = 1  # G46b: proses paralel per gudang (FORECAST_JOBS); 1 = di proses yang sama
    inactive_days: int = 28  # G46b: tanpa jual (dan tanpa hari OOS) >= N hari terakhir -> ramalan 0; 0 = mati


@dataclass
class Series:
    warehouse_id: int
    warehouse_code: str
    product_id: int
    category: str
    y: np.ndarray  # float32, panjang panel
    censored: np.ndarray  # bool


@dataclass(frozen=True)
class Panel:
    start: date
    end: date  # hari histori terakhir (inklusif)
    series: tuple[Series, ...]

    @property
    def n(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class SeriesForecast:
    warehouse_id: int
    warehouse_code: str
    product_id: int
    yhat: np.ndarray
    sigma: np.ndarray
    model: str
    failed: bool = False


@dataclass(frozen=True)
class ForecastResult:
    as_of: date
    forecasts: tuple[SeriesForecast, ...]
    failed_series: int
    model_counts: Mapping[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------- panel
def build_panel(
    start: date,
    end: date,
    sales: Iterable[tuple[int, int, date, float]],
    stock: Iterable[tuple[int, int, date, float]],
    products: Mapping[int, str],
    warehouses: Mapping[int, str],
    censor_lookback_days: int = 14,
) -> Panel:
    """`sales`/`stock`: (warehouse_id, product_id, hari, qty/on_hand). `products`: produk layak forecast -> kategori.
    `warehouses`: id -> kode (hanya gudang yang di-forecast). SKU tanpa penjualan tetapi ber-stok di snapshot terakhir
    gudangnya = SKU baru (seri nol, model category_mean)."""
    if end < start:
        raise ValueError("end < start")
    n = (end - start).days + 1
    y: dict[tuple[int, int], np.ndarray] = {}
    for wh, pid, day, qty in sales:
        if wh not in warehouses or pid not in products or not start <= day <= end:
            continue
        arr = y.get((wh, pid))
        if arr is None:
            arr = y[(wh, pid)] = np.zeros(n, dtype=np.float32)
        arr[(day - start).days] += max(float(qty), 0.0)
    on_hand: dict[tuple[int, int], dict[int, float]] = defaultdict(dict)
    snapshot_days: dict[int, set[int]] = defaultdict(set)
    for wh, pid, day, qty in stock:
        if wh not in warehouses or not start <= day <= end:
            continue
        idx = (day - start).days
        snapshot_days[wh].add(idx)
        if pid in products:
            on_hand[(wh, pid)][idx] = on_hand[(wh, pid)].get(idx, 0.0) + float(qty)
    # SKU baru: ber-stok di snapshot terakhir gudang, belum pernah terjual.
    for (wh, pid), days in on_hand.items():
        if (wh, pid) in y or not snapshot_days[wh]:
            continue
        last = max(snapshot_days[wh])
        if days.get(last, 0.0) > 0:
            y[(wh, pid)] = np.zeros(n, dtype=np.float32)
    series: list[Series] = []
    for (wh, pid), arr in sorted(y.items()):
        censored = _censor_mask(n, snapshot_days.get(wh, set()), on_hand.get((wh, pid), {}), censor_lookback_days)
        series.append(Series(wh, warehouses[wh], pid, products[pid], arr, censored))
    return Panel(start=start, end=end, series=tuple(series))


def _censor_mask(n: int, snap_days: set[int], on_hand: Mapping[int, float], lookback: int) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    if not snap_days or not on_hand:
        return mask
    oh = np.full(n, np.nan)
    for idx in snap_days:
        oh[idx] = on_hand.get(idx, 0.0)
    positive = np.nan_to_num(oh, nan=0.0) > 0
    csum = np.concatenate([[0], np.cumsum(positive)])
    idx = np.arange(n)
    recent = (csum[idx] - csum[np.maximum(idx - lookback, 0)]) > 0  # stok > 0 pada `lookback` hari sebelumnya
    return np.isfinite(oh) & (oh <= 0) & recent


def truncate(panel: Panel, end: date) -> Panel:
    k = (end - panel.start).days + 1
    if k <= 0:
        raise ValueError("histori terlalu pendek untuk dipotong")
    return Panel(
        start=panel.start,
        end=end,
        series=tuple(replace(s, y=s.y[:k], censored=s.censored[:k]) for s in panel.series),
    )


# ---------------------------------------------------------------- faktor kalender
def _aggregate(series: Sequence[Series], n: int) -> tuple[np.ndarray, np.ndarray]:
    total = np.zeros(n)
    cens = np.zeros(n)
    for s in series:
        total += np.where(s.censored, 0.0, s.y.astype(float))
        cens += s.censored
    valid = cens <= 0.5 * max(len(series), 1)
    return total, valid


def _impute(adj: np.ndarray, censored: np.ndarray, window: int = 28) -> np.ndarray:
    out = adj.copy()
    valid_all = out[~censored]
    fallback = float(np.mean(valid_all)) if valid_all.size else 0.0
    for t in np.flatnonzero(censored):
        lo = max(0, t - window)
        prev = out[lo:t][~censored[lo:t]]
        out[t] = float(np.mean(prev)) if prev.size else fallback
    return out


# ---------------------------------------------------------------- forecast
def forecast_panel(
    panel: Panel,
    entries: Sequence[CalendarEntry],
    cal_cfg: CalendarConfig,
    cfg: PipelineConfig,
    runner: ModelRunner,
) -> ForecastResult:
    n, h = panel.n, cfg.horizon_days
    company_feats = build_features(panel.start, n + h, build_spec(for_warehouse(entries, 0), cal_cfg), cal_cfg)
    y_all, valid_all = _aggregate(panel.series, n)
    company_factors = estimate_factors(y_all, valid_all, company_feats.slice(0, n), cfg.shrink_days)
    by_wh: dict[int, list[Series]] = defaultdict(list)
    for s in panel.series:
        by_wh[s.warehouse_id].append(s)
    out: list[SeriesForecast] = []
    failed = 0
    counts: dict[str, int] = defaultdict(int)
    items = sorted(by_wh.items())
    jobs = max(1, min(cfg.jobs, len(items)))
    tasks = [(wh, members, panel.start, n, h, entries, cal_cfg, cfg, runner, company_factors) for wh, members in items]
    started = time.monotonic()
    executor: ProcessPoolExecutor | None = None
    try:
        if jobs == 1:
            results: Iterable[_WarehouseResult] = (_forecast_warehouse(*t) for t in tasks)
        else:
            # G46b: paralel per gudang (proses terpisah, `spawn` = aman terhadap thread klien HTTP/ClickHouse di
            # proses induk). Urutan hasil = urutan gudang -> keluaran deterministik, sama dengan jobs=1.
            executor = ProcessPoolExecutor(max_workers=jobs, mp_context=get_context("spawn"))
            results = executor.map(_forecast_warehouse_packed, tasks)
        for i, ((wh, members), (wh_out, wh_failed, wh_counts)) in enumerate(zip(items, results, strict=True), 1):
            out.extend(wh_out)
            failed += wh_failed
            for k, v in wh_counts.items():
                counts[k] += v
            # G46b: progres per gudang (run 112 hari PB = jam-an; tanpa log ini tidak bisa dipantau). Tanpa nama
            # produk/angka bisnis: hanya id gudang, jumlah seri, durasi.
            log.info(
                "forecast gudang %d/%d wh=%s seri=%d gagal=%d jobs=%d durasi=%.0fs",
                i,
                len(items),
                wh,
                len(members),
                wh_failed,
                jobs,
                time.monotonic() - started,
            )
    finally:
        if executor is not None:
            executor.shutdown(cancel_futures=True)
    return ForecastResult(as_of=panel.end, forecasts=tuple(out), failed_series=failed, model_counts=dict(counts))


_WarehouseResult = tuple[list["SeriesForecast"], int, dict[str, int]]


def _forecast_warehouse_packed(task: tuple) -> _WarehouseResult:
    return _forecast_warehouse(*task)


def _forecast_warehouse(
    wh: int,
    members: Sequence[Series],
    start: date,
    n: int,
    h: int,
    entries: Sequence[CalendarEntry],
    cal_cfg: CalendarConfig,
    cfg: PipelineConfig,
    runner: ModelRunner,
    company_factors: Factors,
) -> _WarehouseResult:
    out: list[SeriesForecast] = []
    failed = 0
    counts: dict[str, int] = defaultdict(int)
    feats = build_features(start, n + h, build_spec(for_warehouse(entries, wh), cal_cfg), cal_cfg)
    hist_f, fut_f = feats.slice(0, n), feats.slice(n, n + h)
    y_wh, valid_wh = _aggregate(members, n)
    wh_factors = merge_factors(
        estimate_factors(y_wh, valid_wh, hist_f, cfg.shrink_days), company_factors, cfg.factor_min_count
    )
    by_cat: dict[str, list[Series]] = defaultdict(list)
    for s in members:
        by_cat[s.category].append(s)
    cat_factors: dict[str, Factors] = {}
    for cat, cat_members in by_cat.items():
        y_cat, valid_cat = _aggregate(cat_members, n)
        cat_factors[cat] = merge_factors(
            estimate_factors(y_cat, valid_cat, hist_f, cfg.shrink_days), wh_factors, cfg.factor_min_count
        )
    done: list[tuple[Series, float, float]] = []  # (seri, level 28 hari, sigma) untuk category_mean
    pending: list[tuple[Series, np.ndarray, np.ndarray, bool]] = []
    for s in members:
        fac = cat_factors[s.category]
        mult_h, mult_f = multipliers(hist_f, fac), multipliers(fut_f, fac)
        adj = _impute(s.y.astype(float) / mult_h, s.censored)
        nz = np.flatnonzero(s.y > 0)
        if cfg.inactive_days > 0 and nz.size:
            # G46b (uji backtest data nyata PB 20 Nov 2026): seri yang berhenti terjual (menu diganti/dihentikan)
            # tetap diramal dengan laju lamanya (Croston pada seri tanpa jual >= 28 hari: ramalan 66x aktual). Hari
            # tersensor (stok habis) dihitung "masih aktif" -> SKU yang sedang OOS tidak di-nol-kan.
            last_active = int(max(nz[-1], np.flatnonzero(s.censored)[-1] if s.censored.any() else -1))
            if n - 1 - last_active >= cfg.inactive_days:
                out.append(_finish(s, np.zeros(h), np.zeros(h), mult_f, INACTIVE))
                counts[INACTIVE] += 1
                continue
        history = adj[nz[0] :] if nz.size else adj[:0]
        klass = classify(history, cfg.min_history_days, cfg.intermittent_zero_ratio)
        fixed = model_for_class(klass.kind)
        if fixed == "category_mean":
            pending.append((s, history, mult_f, False))
            continue
        try:
            mean, sigma, model = _fit(history[-cfg.fit_days :], fixed, cfg, runner, h)
        except Exception:  # model gagal -> category_mean, dihitung sebagai failed_series
            log.debug("seri gagal: wh=%s product=%s", s.warehouse_code, s.product_id)
            pending.append((s, history, mult_f, True))
            continue
        level = float(np.mean(history[-cfg.level_days :]))
        done.append((s, level, float(np.mean(sigma))))
        out.append(_finish(s, mean, sigma, mult_f, model))
        counts[model] += 1
    out.extend(_category_mean(pending, done, cfg, h))
    counts["category_mean"] += len(pending)
    failed += sum(1 for p in pending if p[3])
    return out, failed, dict(counts)


def _fit(
    y: np.ndarray, fixed: str | None, cfg: PipelineConfig, runner: ModelRunner, h: int
) -> tuple[np.ndarray, np.ndarray, str]:
    if fixed is None:
        sel = select_model(y, cfg.candidates, runner, cfg.cv_windows)
        model, residual = sel.model, sel.residual_sigma
    else:
        model, residual = fixed, None
    mean, sigma = runner.fit_predict(model, y, h)
    mean = np.asarray(mean, dtype=float)[:h]
    if mean.shape[0] != h or not np.all(np.isfinite(mean)):
        raise ValueError("ramalan tidak valid")
    if sigma is None or not np.all(np.isfinite(sigma)):
        base = residual if residual is not None else float(np.std(y[-56:])) if y.size else 0.0
        sigma = np.full(h, base)
    return np.maximum(mean, 0.0), np.maximum(np.asarray(sigma, dtype=float)[:h], 0.0), model


def _finish(s: Series, mean: np.ndarray, sigma: np.ndarray, mult_f: np.ndarray, model: str) -> SeriesForecast:
    return SeriesForecast(
        s.warehouse_id, s.warehouse_code, s.product_id, np.maximum(mean, 0.0) * mult_f, sigma * mult_f, model
    )


def _category_mean(
    pending: Sequence[tuple[Series, np.ndarray, np.ndarray, bool]],
    done: Sequence[tuple[Series, float, float]],
    cfg: PipelineConfig,
    h: int,
) -> list[SeriesForecast]:
    """Rata-rata per SKU kategori di gudang (fallback rata-rata gudang), dicampur level seri sendiri sebanding panjang
    histori (bobot n / min_history_days)."""
    by_cat: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s, level, sigma in done:
        by_cat[s.category].append((level, sigma))
    everything = [v for vals in by_cat.values() for v in vals]
    out: list[SeriesForecast] = []
    for s, history, mult_f, _failed in pending:
        pool = by_cat.get(s.category) or everything
        # G46b: median (bukan mean) -> tidak ditarik menu terlaris; backtest PB: category_mean 2,0x di atas aktual.
        cat_level = float(np.median([v[0] for v in pool])) if pool else 0.0
        cat_sigma = float(np.median([v[1] for v in pool])) if pool else 0.0
        m = int(history.shape[0])
        if not pool and m > 0:
            # G46 (uji E2E data nyata PB 19 Nov 2026): tanpa seri pembanding di gudang (outlet baru / PT yang baru
            # dimigrasi dengan histori pendek) penyusutan ke level 0 memotong ramalan menjadi m/56 (7 hari -> 1/8).
            # Pakai level seri sendiri tanpa penyusutan.
            level = float(np.mean(history[-cfg.level_days :]))
            sigma = float(np.std(history)) if m > 1 else 0.0
        elif m >= 7:
            w = min(1.0, m / max(cfg.min_history_days, 1))
            level = w * float(np.mean(history[-cfg.level_days :])) + (1 - w) * cat_level
            sigma = float(np.std(history))
        else:
            level, sigma = cat_level, cat_sigma
        if sigma <= 0 and level > 0:
            sigma = math.sqrt(level)  # Poisson kasar bila tidak ada variasi teramati
        out.append(_finish(s, np.full(h, max(level, 0.0)), np.full(h, sigma), mult_f, "category_mean"))
    return out


def aggregate_dc(forecasts: Sequence[SeriesForecast], dc_warehouses: Mapping[int, str]) -> tuple[SeriesForecast, ...]:
    """Ramalan DC = permintaan langsung DC (bila ada) + Σ ramalan outlet per produk (ADR-046 §3.3). Hanya bila tepat
    satu DC di PT (relasi resupply outlet -> DC belum ada di mart; ADR-045 DC satu PT). Label model = kontributor
    terbesar."""
    if len(dc_warehouses) != 1:
        if dc_warehouses:
            log.warning("lebih dari satu DC (%d): agregasi DC dilewati", len(dc_warehouses))
        return tuple(forecasts)
    dc_id, dc_code = next(iter(dc_warehouses.items()))
    outlets: dict[int, list[SeriesForecast]] = defaultdict(list)
    own: dict[int, SeriesForecast] = {}
    rest: list[SeriesForecast] = []
    for f in forecasts:
        if f.warehouse_id == dc_id:
            own[f.product_id] = f
        else:
            outlets[f.product_id].append(f)
            rest.append(f)
    out = list(rest)
    for pid in sorted(set(outlets) | set(own)):
        parts = outlets.get(pid, []) + ([own[pid]] if pid in own else [])
        yhat = np.sum([p.yhat for p in parts], axis=0)
        sigma = np.sqrt(np.sum([np.square(p.sigma) for p in parts], axis=0))
        top = max(parts, key=lambda p: float(np.sum(p.yhat)))
        out.append(SeriesForecast(dc_id, dc_code, pid, yhat, sigma, top.model, failed=any(p.failed for p in parts)))
    return tuple(out)


# ---------------------------------------------------------------- baris kontrak §28.2
def forecast_lines(forecasts: Sequence[SeriesForecast], as_of: date, z: float = Z80) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    for f in forecasts:
        if f.model == INACTIVE:  # G46b: tanpa baris = tanpa usulan di Odoo (selection model Odoo tidak berubah)
            continue
        for i in range(f.yhat.shape[0]):
            yhat = max(float(f.yhat[i]), 0.0)
            sigma = max(float(f.sigma[i]), 0.0)
            lines.append(
                {
                    "warehouse_code": f.warehouse_code,
                    "product_id": int(f.product_id),
                    "date": (as_of + timedelta(days=i + 1)).isoformat(),
                    "yhat": round(yhat, 3),
                    "p10": round(max(yhat - z * sigma, 0.0), 3),
                    "p90": round(yhat + z * sigma, 3),
                    "sigma": round(sigma, 3),
                    "model": f.model,
                }
            )
    return lines


# ---------------------------------------------------------------- backtest
@dataclass(frozen=True)
class BacktestResult:
    days: int
    series: int
    wape: float | None
    wape_mean56: float | None  # baseline v1 (rata-rata 56 hari, efn_demand_forecast G28)
    wape_seasonal_naive: float | None  # minggu lalu diulang


def _wape(err: float, actual: float) -> float | None:
    return err / actual if actual > 0 else None


def backtest(
    panel: Panel,
    entries: Sequence[CalendarEntry],
    cal_cfg: CalendarConfig,
    cfg: PipelineConfig,
    runner: ModelRunner,
    days: int = 28,
) -> BacktestResult:
    """Holdout `days` hari terakhir: latih sampai `end - days`, bandingkan dengan aktual (hari tidak tersensor)."""
    if panel.n <= days + 7:
        return BacktestResult(days, 0, None, None, None)
    train = truncate(panel, panel.end - timedelta(days=days))
    result = forecast_panel(train, entries, cal_cfg, replace(cfg, horizon_days=days), runner)
    k = train.n
    actual_by_key = {(s.warehouse_id, s.product_id): s for s in panel.series}
    err = err_mean = err_naive = total = 0.0
    for f in result.forecasts:
        s = actual_by_key[(f.warehouse_id, f.product_id)]
        actual = s.y[k : k + days].astype(float)
        valid = ~s.censored[k : k + days]
        hist = s.y[:k].astype(float)
        mean56 = np.full(days, float(np.mean(hist[-56:])) if hist.size else 0.0)
        naive = np.resize(hist[-7:], days) if hist.size >= 7 else mean56
        total += float(np.sum(actual[valid]))
        err += float(np.sum(np.abs(f.yhat[valid] - actual[valid])))
        err_mean += float(np.sum(np.abs(mean56[valid] - actual[valid])))
        err_naive += float(np.sum(np.abs(naive[valid] - actual[valid])))
    return BacktestResult(
        days, len(result.forecasts), _wape(err, total), _wape(err_mean, total), _wape(err_naive, total)
    )


# ---------------------------------------------------------------- min/max (keluaran 1)
@dataclass(frozen=True)
class MinMaxConfig:
    service_level: float = 0.95
    service_level_min: float = 0.85
    waste_high_ratio: float = 0.05
    lead_days: int = 2
    dc_lead_days: int = 7
    review_days: int = 7
    basis_days: int = 7


def service_level(base: float, waste_ratio: float | None, high: float, minimum: float) -> float:
    """Waste historis kategori > ambang menurunkan service level linear sampai `minimum` (pada 2 x ambang).

    Hasil selalu dalam [0,5; 1) = rentang yang diterima Odoo (`invalid_service_level`)."""
    if not SERVICE_LEVEL_MIN <= minimum <= base < SERVICE_LEVEL_MAX:
        raise ValueError("0.5 <= service_level_min <= service_level < 1")
    if waste_ratio is None or high <= 0 or waste_ratio <= high:
        return base
    excess = min(1.0, (waste_ratio - high) / high)
    return round(base - (base - minimum) * excess, 4)


def waste_ratios(
    scrap: Iterable[tuple[int, str, float]], sales: Iterable[tuple[int, str, float]]
) -> dict[tuple[int, str], float]:
    """(gudang, kategori) -> scrap / (scrap + terjual) pada jendela yang sama."""
    s_scrap: dict[tuple[int, str], float] = defaultdict(float)
    s_sales: dict[tuple[int, str], float] = defaultdict(float)
    for wh, cat, qty in scrap:
        s_scrap[(wh, cat)] += max(float(qty), 0.0)
    for wh, cat, qty in sales:
        s_sales[(wh, cat)] += max(float(qty), 0.0)
    out = {}
    for key, waste in s_scrap.items():
        denom = waste + s_sales.get(key, 0.0)
        if denom > 0:
            out[key] = waste / denom
    return out


def minmax_lines(
    forecasts: Sequence[SeriesForecast],
    categories: Mapping[int, str],
    waste: Mapping[tuple[int, str], float],
    cfg: MinMaxConfig,
    dc_ids: Iterable[int] = (),
) -> list[dict[str, object]]:
    """min = d·L + z·σ·√L (stok pengaman + permintaan selama lead time); max = min + d·R (periode review mingguan).
    d, σ = rata-rata ramalan/sigma `basis_days` hari pertama."""
    dcs = set(dc_ids)
    lines: list[dict[str, object]] = []
    for f in forecasts:
        k = min(cfg.basis_days, f.yhat.shape[0])
        if k == 0 or f.model == INACTIVE:  # G46b: seri tidak aktif = tanpa usulan (bukan min/max 0)
            continue
        d = max(float(np.mean(f.yhat[:k])), 0.0)
        sd = float(np.sqrt(np.mean(np.square(f.sigma[:k]))))
        lead = cfg.dc_lead_days if f.warehouse_id in dcs else cfg.lead_days
        sl = service_level(
            cfg.service_level,
            waste.get((f.warehouse_id, categories.get(f.product_id, ""))),
            cfg.waste_high_ratio,
            cfg.service_level_min,
        )
        z = NormalDist().inv_cdf(sl)
        min_qty = max(d * lead + z * sd * math.sqrt(lead), 0.0)
        max_qty = max(min_qty + d * cfg.review_days, min_qty)
        lines.append(
            {
                "warehouse_code": f.warehouse_code,
                "product_id": int(f.product_id),
                "min_qty": round(min_qty, 3),
                "max_qty": round(max_qty, 3),
                "service_level": sl,
                "basis_daily_demand": round(d, 3),
            }
        )
    return lines
