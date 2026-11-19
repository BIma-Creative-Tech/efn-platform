"""Orkestrasi satu batch forecast (ADR-046 §3.1, docs/04 §28.2). Dependensi diinjeksi (sumber data, klien Odoo, model)
agar bisa diuji tanpa ClickHouse/Odoo.

Urutan batch `daily`: seed kalender (non-fatal) -> baca data -> forecast + backtest -> validasi SEMUA payload lokal ->
run_open(chunk_total) -> ingest chunk berurutan (idempoten run_id/chunk_no) -> run_close(ok, metrik).
Batch `weekly_minmax`: sama, tetapi baris min/max (efn_minmax_ingest).
Gagal di titik mana pun -> run_close(status failed) (run_open dulu bila belum, chunk_total 1) lalu kembali normal; tidak
ada crash/restart berulang. Odoo menandai run tak tertutup > 6 jam sebagai failed; usulan & reorder rule terakhir tetap
berlaku (ADR-010). Log tanpa data pribadi (memang tidak ada di input) dan tanpa isi payload.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from efn_forecast import MODEL_VERSION
from efn_forecast.calendar import CalendarEntry, lib_entries, merge_entries, seed_entries
from efn_forecast.config import Params
from efn_forecast.contract import (
    FORECAST_VALUE_KEYS,
    MINMAX_VALUE_KEYS,
    SCHEMA_VERSION,
    ContractError,
    chunk,
    validate_calendar_upsert,
    validate_ingest,
    validate_minmax,
    validate_run_close,
    validate_run_open,
    within_cap,
)
from efn_forecast.models import ModelRunner
from efn_forecast.odoo import OdooError
from efn_forecast.pipeline import (
    BacktestResult,
    Panel,
    SeriesForecast,
    aggregate_dc,
    backtest,
    build_panel,
    forecast_lines,
    forecast_panel,
    minmax_lines,
    waste_ratios,
)
from efn_forecast.schedule import WIB
from efn_forecast.source import Warehouse

log = logging.getLogger(__name__)
KINDS = ("daily", "weekly_minmax")


class DataSource(Protocol):
    def warehouses(self, company: str) -> list[Warehouse]: ...
    def sales(self, company: str, start: date, end: date) -> list[tuple[int, int, date, float]]: ...
    def stock(self, company_id: int, start: date, end: date) -> list[tuple[int, int, date, float]]: ...
    def scrap(self, company_id: int, start: date, end: date) -> list[tuple[int, str, float]]: ...
    def calendar(self, company_id: int, start: date, end: date) -> list[CalendarEntry]: ...
    def products(self, company_id: int) -> dict[int, str]: ...


class OdooApi(Protocol):
    def calendar_upsert(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def run_open(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def minmax_ingest(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def run_close(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class NoData(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RunReport:
    run_id: str
    kind: str
    status: str  # ok | failed | dry_run
    series: int = 0
    failed_series: int = 0
    lines: int = 0
    chunks: int = 0
    rejected: int = 0
    rejected_local: int = 0  # baris > plafon nilai per baris, dibuang sebelum dikirim
    wape_backtest: float | None = None
    wape_baseline_mean56: float | None = None
    wape_seasonal_naive: float | None = None
    duration_s: float = 0.0
    error: str = ""


@dataclass(frozen=True)
class Computation:
    company_id: int
    as_of: date
    panel: Panel
    forecasts: tuple[SeriesForecast, ...]
    failed_series: int
    backtest: BacktestResult | None
    products: dict[int, str]
    dc_ids: tuple[int, ...]


def business_today(now: datetime) -> date:
    return now.astimezone(WIB).date()


def rfc3339(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_calendar(
    odoo: OdooApi | None,
    company: str,
    years: Iterable[int],
    params: Params,
    lib: Callable[[Iterable[int]], list[CalendarEntry]] = lib_entries,
) -> int:
    """efn_forecast_calendar_upsert untuk libur nasional/cuti bersama (tahun berjalan + 1, source lib)."""
    yrs = sorted(set(years))
    entries = seed_entries(lib(yrs), yrs)
    payloads = []
    for part in chunk(entries, params.chunk_size):
        payload = {"schema_version": SCHEMA_VERSION, "company_code": company, "entries": part}
        validate_calendar_upsert(payload)
        payloads.append(payload)
    if odoo is not None:
        for payload in payloads:
            odoo.calendar_upsert(payload)
    return len(entries)


def compute(
    source: DataSource,
    company: str,
    params: Params,
    runner: ModelRunner,
    now: datetime,
    lib: Callable[[Iterable[int]], list[CalendarEntry]] = lib_entries,
) -> Computation:
    warehouses = source.warehouses(company)
    if params.warehouse_codes:
        warehouses = [w for w in warehouses if w.code in params.warehouse_codes]
    if not warehouses:
        raise NoData("no_warehouses")
    company_id = warehouses[0].company_id
    today = business_today(now)
    as_of = today if params.include_today else today - timedelta(days=1)
    start = as_of - timedelta(days=params.history_days - 1)
    products = source.products(company_id)
    sales = source.sales(company, start, as_of)
    stock_start = max(start, as_of - timedelta(days=params.stock_days - 1))
    stock = source.stock(company_id, stock_start, as_of) if params.stock_days > 0 else []
    panel = build_panel(start, as_of, sales, stock, products, {w.warehouse_id: w.code for w in warehouses})
    if not panel.series:
        raise NoData("no_series")
    years = range(start.year, as_of.year + 2)
    odoo_entries = source.calendar(company_id, date(start.year, 1, 1), date(as_of.year + 1, 12, 31))
    entries = merge_entries(lib(years), odoo_entries)
    result = forecast_panel(panel, entries, params.calendar, params.pipeline, runner)
    dc = {w.warehouse_id: w.code for w in warehouses if w.code in params.dc_codes}
    forecasts = aggregate_dc(result.forecasts, dc)
    bt = None
    if params.backtest_enabled:
        bt = backtest(panel, entries, params.calendar, params.pipeline, runner, params.backtest_days)
    return Computation(company_id, as_of, panel, forecasts, result.failed_series, bt, products, tuple(dc))


def _minmax(comp: Computation, source: DataSource, params: Params) -> list[dict[str, object]]:
    lookback = params.waste_lookback_days
    start = comp.as_of - timedelta(days=lookback - 1)
    scrap = source.scrap(comp.company_id, start, comp.as_of)
    sold: dict[tuple[int, str], float] = defaultdict(float)
    k = min(lookback, comp.panel.n)
    for s in comp.panel.series:
        sold[(s.warehouse_id, s.category)] += float(s.y[-k:].sum())
    waste = waste_ratios(scrap, [(wh, cat, q) for (wh, cat), q in sold.items()])
    return minmax_lines(comp.forecasts, comp.products, waste, params.minmax, comp.dc_ids)


def _apply_cap(lines: list[dict[str, object]], keys: Sequence[str], cap: float) -> tuple[list[dict[str, object]], int]:
    """Baris dengan nilai > plafon `efn_forecast.max_qty_per_line` dibuang di worker (Odoo akan menolaknya
    `value_too_large`); jumlahnya dicatat `rejected_local` (log tanpa nilai/identitas baris)."""
    kept = [line for line in lines if within_cap(line, keys, cap)]
    dropped = len(lines) - len(kept)
    if dropped:
        log.warning("%d baris melebihi plafon %.0f per baris; tidak dikirim (value_too_large)", dropped, cap)
    return kept, dropped


def _payloads(run_id: str, lines: Sequence[dict[str, object]], params: Params) -> list[dict[str, Any]]:
    return [
        {"schema_version": SCHEMA_VERSION, "run_id": run_id, "chunk_no": i, "lines": part}
        for i, part in enumerate(chunk(lines, params.chunk_size))
    ]


def run_batch(
    kind: str,
    source: DataSource,
    odoo: OdooApi | None,
    company: str,
    params: Params,
    runner: ModelRunner,
    now: datetime | None = None,
    lib: Callable[[Iterable[int]], list[CalendarEntry]] = lib_entries,
    clock: Callable[[], float] = time.monotonic,
) -> RunReport:
    """Satu batch. `odoo=None` = dry-run (tidak ada panggilan Odoo, hanya hitung + validasi + log ringkasan)."""
    if kind not in KINDS:
        raise ValueError("kind harus daily|weekly_minmax")
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())
    started = clock()
    opened = False
    comp: Computation | None = None
    try:
        today = business_today(now)
        if kind == "daily" and params.seed_calendar and odoo is not None:
            try:
                seed_calendar(odoo, company, (today.year, today.year + 1), params, lib)
            except Exception as exc:  # seed gagal tidak menghentikan forecast (kalender library tetap dipakai lokal)
                log.warning("seed kalender gagal (%s); forecast tetap jalan", _code(exc))
        comp = compute(source, company, params, runner, now, lib)
        cap = params.max_qty_per_line
        if kind == "daily":
            lines, capped = _apply_cap(forecast_lines(comp.forecasts, comp.as_of), FORECAST_VALUE_KEYS, cap)
            payloads = _payloads(run_id, lines, params)
            for p in payloads:
                validate_ingest(p, today, params.pipeline.horizon_days, cap)
        else:
            lines, capped = _apply_cap(_minmax(comp, source, params), MINMAX_VALUE_KEYS, cap)
            payloads = _payloads(run_id, lines, params)
            for p in payloads:
                validate_minmax(p, cap)
        if not payloads:
            raise NoData("no_lines")
        open_payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "company_code": company,
            "model_version": MODEL_VERSION,
            "kind": kind,
            "generated_at": rfc3339(now),
            "horizon_days": params.pipeline.horizon_days,
            "chunk_total": len(payloads),
        }
        validate_run_open(open_payload)
        bt = comp.backtest
        wape = _r4(bt.wape) if bt else None
        report = RunReport(
            run_id=run_id,
            kind=kind,
            status="dry_run" if odoo is None else "ok",
            series=len(comp.forecasts),
            failed_series=comp.failed_series,
            lines=len(lines),
            chunks=len(payloads),
            rejected_local=capped,
            wape_backtest=wape,
            wape_baseline_mean56=_r4(bt.wape_mean56) if bt else None,
            wape_seasonal_naive=_r4(bt.wape_seasonal_naive) if bt else None,
        )
        if odoo is None:
            report = _with_duration(report, clock() - started)
            _log_report(report)
            return report
        odoo.run_open(open_payload)
        opened = True
        rejected = 0
        send = odoo.ingest if kind == "daily" else odoo.minmax_ingest
        for p in payloads:  # berurutan (gateway maxconn 1); retry di klien
            result = send(p)
            rejected += len(result.get("rejected") or [])
        close_payload = _close_payload(run_id, "ok", report.series, report.failed_series, clock() - started, wape)
        validate_run_close(close_payload)
        odoo.run_close(close_payload)
        report = _with_duration(replace(report, rejected=rejected), clock() - started)
        _log_report(report)
        return report
    except Exception as exc:
        code = _code(exc)
        log.error("forecast run=%s kind=%s gagal: %s", run_id, kind, code)
        series = len(comp.forecasts) if comp is not None else 0
        failed = comp.failed_series if comp is not None else 0
        if odoo is not None:
            _close_failed(odoo, run_id, kind, company, params, now, opened, series, failed, clock() - started)
        report = RunReport(
            run_id=run_id,
            kind=kind,
            status="failed",
            series=series,
            failed_series=failed,
            duration_s=round(clock() - started, 1),
            error=code,
        )
        _log_report(report)
        return report


def _code(exc: BaseException) -> str:
    if isinstance(exc, ContractError | NoData):
        return exc.code
    if isinstance(exc, OdooError):
        return f"odoo_{exc.kind}" + (f":{exc.code}" if exc.code else "")
    return type(exc).__name__  # tanpa pesan exception mentah (bisa memuat potongan SQL/nilai)


def _close_payload(
    run_id: str, status: str, series: int, failed: int, duration: float, wape: float | None
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "series": int(series),
        "duration_s": round(max(duration, 0.0), 1),
        "failed_series": int(failed),
    }
    if wape is not None:  # Odoo menolak null; tanpa backtest -> kunci dihilangkan (Odoo menyimpan 0)
        metrics["wape_backtest"] = wape
    return {"schema_version": SCHEMA_VERSION, "run_id": run_id, "status": status, "metrics": metrics}


def _close_failed(
    odoo: OdooApi,
    run_id: str,
    kind: str,
    company: str,
    params: Params,
    now: datetime,
    opened: bool,
    series: int,
    failed: int,
    duration: float,
) -> None:
    try:
        if not opened:
            odoo.run_open(
                {
                    "schema_version": SCHEMA_VERSION,
                    "run_id": run_id,
                    "company_code": company,
                    "model_version": MODEL_VERSION,
                    "kind": kind,
                    "generated_at": rfc3339(now),
                    "horizon_days": params.pipeline.horizon_days,
                    "chunk_total": 1,  # Odoo: >= 1; run failed tidak menuntut chunk lengkap
                }
            )
        odoo.run_close(_close_payload(run_id, "failed", series, failed, duration, None))
    except Exception as exc:  # Odoo tidak terjangkau -> cron Odoo menandai failed setelah 6 jam
        log.error("run_close failed tidak terkirim run=%s: %s", run_id, _code(exc))


def _with_duration(report: RunReport, duration: float) -> RunReport:
    return replace(report, duration_s=round(duration, 1))


def _log_report(r: RunReport) -> None:
    log.info(
        'EFN_FORECAST_RUN {"run_id":"%s","kind":"%s","status":"%s","series":%d,"failed_series":%d,"lines":%d,'
        '"chunks":%d,"rejected":%d,"rejected_local":%d,"wape":%s,"wape_mean56":%s,"wape_naive":%s,'
        '"duration_s":%.1f,"error":"%s"}',
        r.run_id,
        r.kind,
        r.status,
        r.series,
        r.failed_series,
        r.lines,
        r.chunks,
        r.rejected,
        r.rejected_local,
        _num(r.wape_backtest),
        _num(r.wape_baseline_mean56),
        _num(r.wape_seasonal_naive),
        r.duration_s,
        r.error,
    )


def _r4(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _num(value: float | None) -> str:
    return "null" if value is None else f"{value:.4f}"
