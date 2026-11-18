"""Validasi skema lokal kontrak forecast-worker -> Odoo (docs/04 §28.2) SEBELUM dikirim + pemecahan chunk.

Semua objek `additionalProperties: false` -> kunci harus persis. Batas: entries/lines <= 500 per panggilan, body
<= 512 KB, angka >= 0 & hingga, p10 <= yhat <= p90, tanggal dalam [hari ini, hari ini + horizon]. Kegagalan validasi
= `ContractError(code)` (kode aman untuk log, tanpa nilai data).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

SCHEMA_VERSION = 1
MAX_ITEMS = 500
MAX_BODY_BYTES = 512 * 1024
MODELS = frozenset({"AutoETS", "AutoARIMA", "CrostonOptimized", "ADIDA", "category_mean"})
CALENDAR_KINDS = frozenset({"holiday", "cuti_bersama", "ramadan", "lebaran_window", "custom"})
RUN_KINDS = frozenset({"daily", "weekly_minmax"})
RUN_STATUSES = frozenset({"ok", "failed"})
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
COMPANY_RE = re.compile(r"^[A-Z]{2,8}$")
WAREHOUSE_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
MODEL_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CALENDAR_KEYS = frozenset({"schema_version", "company_code", "entries"})
CALENDAR_ENTRY_KEYS = frozenset({"date", "kind", "name", "source"})
RUN_OPEN_KEYS = frozenset(
    {"schema_version", "run_id", "company_code", "model_version", "kind", "generated_at", "horizon_days", "chunk_total"}
)
INGEST_KEYS = frozenset({"schema_version", "run_id", "chunk_no", "lines"})
FORECAST_LINE_KEYS = frozenset({"warehouse_code", "product_id", "date", "yhat", "p10", "p90", "sigma", "model"})
MINMAX_LINE_KEYS = frozenset(
    {"warehouse_code", "product_id", "min_qty", "max_qty", "service_level", "basis_daily_demand"}
)
RUN_CLOSE_KEYS = frozenset({"schema_version", "run_id", "status", "metrics"})
METRIC_KEYS = frozenset({"series", "duration_s", "wape_backtest", "failed_series"})
METRIC_REQUIRED = frozenset({"series", "duration_s", "failed_series"})
# Paritas Odoo (`efn_demand_forecast.py`, kode invalid_service_level): service level min/max v2 dalam [0,5; 1).
SERVICE_LEVEL_MIN = 0.5
SERVICE_LEVEL_MAX = 1.0  # eksklusif
# Plafon nilai per baris = parameter Odoo `efn_forecast.max_qty_per_line` (default 100000, kode `value_too_large`);
# worker memakai env FORECAST_MAX_QTY_PER_LINE dengan default sama.
MAX_QTY_PER_LINE = 100_000
FORECAST_VALUE_KEYS = ("yhat", "p10", "p90", "sigma")
MINMAX_VALUE_KEYS = ("min_qty", "max_qty", "basis_daily_demand")


class ContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _keys(obj: Any, expected: frozenset[str], where: str) -> Mapping[str, Any]:
    if not isinstance(obj, Mapping) or set(obj) != expected:
        raise ContractError(f"{where}_keys")
    return obj


def _int(value: Any, lo: int, hi: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ContractError(code)
    return value


def _num(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise ContractError(code)
    return float(value)


def _match(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContractError(code)
    return value


def _schema(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION or isinstance(payload.get("schema_version"), bool):
        raise ContractError("schema_version")


def _items(value: Any, code: str) -> Sequence[Any]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ITEMS:
        raise ContractError(code)
    return value


def body_bytes(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _size(payload: Mapping[str, Any]) -> None:
    if body_bytes(payload) > MAX_BODY_BYTES:
        raise ContractError("body_too_large")


def validate_calendar_upsert(payload: Mapping[str, Any]) -> None:
    p = _keys(payload, CALENDAR_KEYS, "calendar")
    _schema(p)
    _match(p["company_code"], COMPANY_RE, "company_code")
    for e in _items(p["entries"], "entries"):
        e = _keys(e, CALENDAR_ENTRY_KEYS, "entry")
        _match(e["date"], DATE_RE, "entry_date")
        date.fromisoformat(e["date"])
        if e["kind"] not in CALENDAR_KINDS:
            raise ContractError("entry_kind")
        if not isinstance(e["name"], str) or not 1 <= len(e["name"]) <= 64:
            raise ContractError("entry_name")
        if e["source"] != "lib":
            raise ContractError("entry_source")
    _size(p)


def validate_run_open(payload: Mapping[str, Any]) -> None:
    p = _keys(payload, RUN_OPEN_KEYS, "run_open")
    _schema(p)
    _match(p["run_id"], UUID4_RE, "run_id")
    _match(p["company_code"], COMPANY_RE, "company_code")
    _match(p["model_version"], MODEL_VERSION_RE, "model_version")
    if p["kind"] not in RUN_KINDS:
        raise ContractError("run_kind")
    _match(p["generated_at"], RFC3339_UTC_RE, "generated_at")
    _int(p["horizon_days"], 1, 120, "horizon_days")
    # Odoo menolak chunk_total < 1; run yang gagal sebelum data siap dibuka dengan 1 lalu ditutup failed.
    _int(p["chunk_total"], 1, 10_000, "chunk_total")


def within_cap(line: Mapping[str, Any], keys: Sequence[str], cap: float) -> bool:
    """True bila semua nilai kuantitas baris <= plafon (nilai non-angka dibiarkan untuk validator)."""
    return all(not isinstance(line.get(k), int | float) or line[k] <= cap for k in keys)


def _cap(line: Mapping[str, Any], keys: Sequence[str], cap: float) -> None:
    if not within_cap(line, keys, cap):
        raise ContractError("value_too_large")


def validate_ingest(
    payload: Mapping[str, Any], today: date, horizon_days: int, max_qty: float = MAX_QTY_PER_LINE
) -> None:
    p = _keys(payload, INGEST_KEYS, "ingest")
    _schema(p)
    _match(p["run_id"], UUID4_RE, "run_id")
    _int(p["chunk_no"], 0, 100_000, "chunk_no")
    last = today + timedelta(days=horizon_days)
    for line in _items(p["lines"], "lines"):
        line = _keys(line, FORECAST_LINE_KEYS, "line")
        _match(line["warehouse_code"], WAREHOUSE_RE, "warehouse_code")
        _int(line["product_id"], 1, 2**31 - 1, "product_id")
        _match(line["date"], DATE_RE, "line_date")
        if not today <= date.fromisoformat(line["date"]) <= last:
            raise ContractError("line_date_range")
        yhat, p10, p90 = (_num(line[k], f"line_{k}") for k in ("yhat", "p10", "p90"))
        _num(line["sigma"], "line_sigma")
        if not p10 <= yhat <= p90:
            raise ContractError("line_interval")
        _cap(line, FORECAST_VALUE_KEYS, max_qty)
        if line["model"] not in MODELS:
            raise ContractError("line_model")
    _size(p)


def validate_minmax(payload: Mapping[str, Any], max_qty: float = MAX_QTY_PER_LINE) -> None:
    p = _keys(payload, INGEST_KEYS, "minmax")
    _schema(p)
    _match(p["run_id"], UUID4_RE, "run_id")
    _int(p["chunk_no"], 0, 100_000, "chunk_no")
    for line in _items(p["lines"], "lines"):
        line = _keys(line, MINMAX_LINE_KEYS, "minmax_line")
        _match(line["warehouse_code"], WAREHOUSE_RE, "warehouse_code")
        _int(line["product_id"], 1, 2**31 - 1, "product_id")
        lo, hi = _num(line["min_qty"], "min_qty"), _num(line["max_qty"], "max_qty")
        if lo > hi:
            raise ContractError("minmax_order")
        sl = _num(line["service_level"], "service_level")
        if not SERVICE_LEVEL_MIN <= sl < SERVICE_LEVEL_MAX:
            raise ContractError("service_level")
        _num(line["basis_daily_demand"], "basis_daily_demand")
        _cap(line, MINMAX_VALUE_KEYS, max_qty)
    _size(p)


def validate_run_close(payload: Mapping[str, Any]) -> None:
    p = _keys(payload, RUN_CLOSE_KEYS, "run_close")
    _schema(p)
    _match(p["run_id"], UUID4_RE, "run_id")
    if p["status"] not in RUN_STATUSES:
        raise ContractError("run_status")
    m = p["metrics"]
    # wape_backtest opsional: dihilangkan bila backtest tidak dijalankan / tanpa aktual (Odoo menolak null).
    if not isinstance(m, Mapping) or not METRIC_REQUIRED <= set(m) <= METRIC_KEYS:
        raise ContractError("metrics_keys")
    _int(m["series"], 0, 10_000_000, "metric_series")
    _int(m["failed_series"], 0, 10_000_000, "metric_failed_series")
    _num(m["duration_s"], "metric_duration_s")
    if "wape_backtest" in m:
        _num(m["wape_backtest"], "metric_wape_backtest")


def chunk(items: Sequence[dict[str, Any]], max_items: int = MAX_ITEMS, max_bytes: int = MAX_BODY_BYTES - 4096) -> list:
    """Pecah daftar baris menjadi chunk <= max_items dan <= max_bytes (perkiraan ukuran JSON baris + pemisah;
    4 KB dicadangkan untuk amplop). Urutan dipertahankan -> chunk_no deterministik untuk input yang sama."""
    if not 1 <= max_items <= MAX_ITEMS:
        raise ValueError("max_items di luar 1..500")
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for item in items:
        item_size = body_bytes(item) + 1
        if item_size > max_bytes:
            raise ContractError("line_too_large")
        if current and (len(current) >= max_items or size + item_size > max_bytes):
            out.append(current)
            current, size = [], 0
        current.append(item)
        size += item_size
    if current:
        out.append(current)
    return out
