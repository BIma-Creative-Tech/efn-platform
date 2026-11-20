"""G47 (docs/04 §28.13.8c, L-200/L-201): registri definisi & ambang KPI supermarket per PT. Logika murni (tanpa I/O).

Default di sini wajib sama dengan 3 tempat lain (L-200): registri Odoo `efn_insight_sm` `KPI_REGISTRY`, fallback
view ClickHouse `efn_dwh.v_sm_kpi_config` (infra/clickhouse/init/17-g47-insight-sm.sql), dan insight-web
`lib/sm-defaults.ts`. File normatif = `docs/contracts/insight_sm_kpi_defaults.json`; kesamaan dijaga
tests/test_sm_kpi_parity.py (ETL + view) dan test sisi Odoo / Vitest.

ETL memakai registri untuk memvalidasi `efn_insight_sm_kpi.value` (Char) sebelum disalin ke `dim_sm_kpi_config`:
nilai tidak sah (tipe/rentang/pilihan) -> baris disalin dengan `active = 0` sehingga view memakai default (Odoo sudah
menolak nilai tidak sah lewat ValidationError; ini lapis kedua). Kunci tak dikenal tidak disalin.
"""

import math
from dataclasses import dataclass
from typing import Any, Final

KIND_ENUM: Final = "enum"
KIND_BOOL: Final = "bool"
KIND_FLOAT: Final = "float"
KIND_INT: Final = "int"
KINDS: Final = frozenset({KIND_ENUM, KIND_BOOL, KIND_FLOAT, KIND_INT})


@dataclass(frozen=True)
class KpiSpec:
    key: str
    kind: str
    default: float | str
    choices: tuple[str, ...] = ()
    low: float | None = None
    high: float | None = None


def _enum(key: str, default: str, *choices: str) -> KpiSpec:
    return KpiSpec(key, KIND_ENUM, default, choices=tuple(choices))


def _bool(key: str, default: int) -> KpiSpec:
    return KpiSpec(key, KIND_BOOL, float(default), low=0.0, high=1.0)


def _float(key: str, default: float, low: float, high: float) -> KpiSpec:
    return KpiSpec(key, KIND_FLOAT, float(default), low=float(low), high=float(high))


def _int(key: str, default: int, low: int, high: int) -> KpiSpec:
    return KpiSpec(key, KIND_INT, float(default), low=float(low), high=float(high))


# Urutan = tabel kontrak §28.13.8c (39 kunci).
KPI_REGISTRY: Final[tuple[KpiSpec, ...]] = (
    _enum("gmroi.period", "month", "month", "quarter", "year"),
    _enum("gmroi.stock_avg", "daily", "daily", "month_end"),
    _bool("gmroi.include_dc", 1),
    _bool("gmroi.annualize", 0),
    _enum("oos.universe", "orderpoint", "orderpoint", "orderpoint_sold_28d"),
    _enum("oos.basis", "on_hand", "on_hand", "free"),
    _float("oos.threshold_qty", 0, 0, 1000),
    _bool("oos.include_dc", 0),
    _enum("critical.rule", "min_or_cover", "min_or_cover", "cover_only", "min_only"),
    _float("critical.cover_days", 2, 0.5, 30),
    _int("critical.sales_window_days", 28, 7, 90),
    _enum("fill.basis", "shipped", "shipped", "received"),
    _enum("fill.level", "qty", "qty", "line"),
    _int("fill.grace_days", 0, 0, 7),
    _enum("waste.basis", "sales_excl_tax", "sales_excl_tax", "cogs"),
    _float("thr.oos.warn_pct", 2, 0, 100),
    _float("thr.oos.bad_pct", 5, 0, 100),
    _float("thr.fill.good_pct", 95, 0, 100),
    _float("thr.fill.warn_pct", 90, 0, 100),
    _float("thr.waste.warn_pct", 2, 0, 100),
    _float("thr.waste.bad_pct", 4, 0, 100),
    _float("thr.wape.warn_pct", 25, 0, 500),
    _float("thr.wape.bad_pct", 40, 0, 500),
    _float("thr.bias.warn_pct", 5, 0, 500),
    _float("thr.bias.bad_pct", 10, 0, 500),
    _float("thr.price.warn_pct", 10, 0, 500),
    _float("thr.price.bad_pct", 20, 0, 500),
    _float("thr.price.below_pct", 10, 0, 500),
    _int("thr.price.min_samples", 3, 1, 50),
    _int("thr.po_late.warn", 1, 0, 1000),
    _int("thr.po_late.bad", 3, 0, 1000),
    _int("thr.cutoff.urgent_min", 120, 15, 1440),
    _int("thr.stock_age.days", 30, 1, 365),
    _int("fresh.near_live.warn_min", 30, 5, 10080),
    _int("fresh.near_live.stale_min", 60, 5, 10080),
    _int("fresh.stock.warn_min", 75, 5, 10080),
    _int("fresh.stock.stale_min", 135, 5, 10080),
    _int("fresh.daily.warn_min", 1560, 60, 20160),
    _int("fresh.daily.stale_min", 2880, 60, 20160),
)
KPI_BY_KEY: Final[dict[str, KpiSpec]] = {spec.key: spec for spec in KPI_REGISTRY}

# Pasangan yang wajib konsisten (§28.13.8c "warn < bad; untuk fill rate good > warn"; kesegaran warn < stale):
# nilai(lower) < nilai(upper), ketat.
KPI_PAIRS: Final[tuple[tuple[str, str], ...]] = (
    ("thr.oos.warn_pct", "thr.oos.bad_pct"),
    ("thr.fill.warn_pct", "thr.fill.good_pct"),
    ("thr.waste.warn_pct", "thr.waste.bad_pct"),
    ("thr.wape.warn_pct", "thr.wape.bad_pct"),
    ("thr.bias.warn_pct", "thr.bias.bad_pct"),
    ("thr.price.warn_pct", "thr.price.bad_pct"),
    ("thr.po_late.warn", "thr.po_late.bad"),
    ("fresh.near_live.warn_min", "fresh.near_live.stale_min"),
    ("fresh.stock.warn_min", "fresh.stock.stale_min"),
    ("fresh.daily.warn_min", "fresh.daily.stale_min"),
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_kpi_value(key: str, raw: Any) -> tuple[float | None, str] | None:
    """`(value_num, value_str)` untuk kolom `dim_sm_kpi_config`, atau None bila kunci/nilai tidak sah.

    enum -> (None, pilihan); bool -> (0.0|1.0, ''); float/int -> (angka, ''). Teks dipangkas; int wajib bulat.
    """
    spec = KPI_BY_KEY.get(key)
    if spec is None:
        return None
    text = "" if raw is None else str(raw).strip()
    if spec.kind == KIND_ENUM:
        return (None, text) if text in spec.choices else None
    if spec.kind == KIND_BOOL:
        lowered = text.lower()
        if lowered in _TRUE:
            return (1.0, "")
        if lowered in _FALSE:
            return (0.0, "")
        return None
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if spec.kind == KIND_INT:
        if number != int(number):
            return None
        number = float(int(number))
    if spec.low is not None and number < spec.low:
        return None
    if spec.high is not None and number > spec.high:
        return None
    return (number, "")


def default_value(key: str) -> float | str:
    return KPI_BY_KEY[key].default


def defaults_document() -> dict[str, Any]:
    """Bentuk kanonik file normatif docs/contracts/insight_sm_kpi_defaults.json (dibandingkan test parity)."""
    keys: list[dict[str, Any]] = []
    for spec in KPI_REGISTRY:
        item: dict[str, Any] = {"key": spec.key, "type": spec.kind}
        if spec.kind == KIND_ENUM:
            item["choices"] = list(spec.choices)
            item["default"] = spec.default
        else:
            item["min"] = _plain(spec.low)
            item["max"] = _plain(spec.high)
            item["default"] = _plain(float(spec.default))
        keys.append(item)
    return {
        "contract": "docs/04_SERVICE_CONTRACTS.md §28.13.8c (v20.3, L-200/L-201)",
        "version": 1,
        "keys": keys,
        "pairs": [{"lower": lower, "upper": upper} for lower, upper in KPI_PAIRS],
    }


def _plain(value: float | None) -> int | float | None:
    """2.0 -> 2 (JSON tanpa .0 untuk bilangan bulat), 0.5 tetap 0.5."""
    if value is None:
        return None
    return int(value) if float(value).is_integer() else float(value)
