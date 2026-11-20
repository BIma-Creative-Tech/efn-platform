"""Registri tertutup definisi & ambang KPI Insight supermarket (G47, docs/04 §28.13.8c, L-200/L-201).

Fungsi murni (tanpa ORM). Default di tiga tempat (registri ini, literal fallback view ClickHouse `v_sm_kpi_config`,
insight-web `lib/thresholds.ts`) wajib sama dengan file normatif `docs/contracts/insight_sm_kpi_defaults.json`
(test parity `tests/test_kpi.py`). Menambah kunci = revisi kontrak + entri di sini + file JSON (tanpa migrasi skema).

Bentuk nilai kanonik (dipakai ETL dan semantik C-1 `v_sm_kpi_config.is_default` = baris tidak ada/nonaktif ATAU
`value` = literal default): int -> desimal tanpa tanda `+`/nol depan; float -> `repr` Python terpendek dengan bilangan
bulat ditulis tanpa `.0` (`2`, `0.5`); bool -> `0`/`1`; enum -> pilihan apa adanya. Nilai yang disimpan Odoo selalu
dikanonikkan saat create/write, jadi perbandingan string byte-exact di ClickHouse aman.
"""

import math
import re
from collections import namedtuple

KpiSpec = namedtuple("KpiSpec", "type minimum maximum choices default label")

T_ENUM, T_BOOL, T_INT, T_FLOAT = "enum", "bool", "int", "float"
TYPES = (T_ENUM, T_BOOL, T_INT, T_FLOAT)
NOTE_MAX = 128


def _enum(choices, default, label):
    return KpiSpec(T_ENUM, None, None, tuple(choices), default, label)


def _bool(default, label):
    return KpiSpec(T_BOOL, 0, 1, None, default, label)


def _int(minimum, maximum, default, label):
    return KpiSpec(T_INT, minimum, maximum, None, default, label)


def _float(minimum, maximum, default, label):
    return KpiSpec(T_FLOAT, minimum, maximum, None, default, label)


# Urutan = tabel kontrak §28.13.8c (39 kunci).
KPI_REGISTRY = {
    "gmroi.period": _enum(("month", "quarter", "year"), "month", "GMROI: periode"),
    "gmroi.stock_avg": _enum(("daily", "month_end"), "daily", "GMROI: rata-rata persediaan"),
    "gmroi.include_dc": _bool(1, "GMROI: persediaan DC ikut penyebut PT"),
    "gmroi.annualize": _bool(0, "GMROI: disetahunkan"),
    "oos.universe": _enum(("orderpoint", "orderpoint_sold_28d"), "orderpoint", "OOS: penyebut SKU"),
    "oos.basis": _enum(("on_hand", "free"), "on_hand", "OOS: stok pembanding"),
    "oos.threshold_qty": _float(0, 1000, 0, "OOS: habis bila stok <= qty"),
    "oos.include_dc": _bool(0, "OOS: DC ikut dihitung"),
    "critical.rule": _enum(("min_or_cover", "cover_only", "min_only"), "min_or_cover", "Stok kritis: aturan"),
    "critical.cover_days": _float(0.5, 30, 2, "Stok kritis: hari persediaan < N"),
    "critical.sales_window_days": _int(7, 90, 28, "Stok kritis: jendela rata-rata jual (hari)"),
    "fill.basis": _enum(("shipped", "received"), "shipped", "Fill rate: qty dikirim DC / diterima outlet"),
    "fill.level": _enum(("qty", "line"), "qty", "Fill rate: angka utama"),
    "fill.grace_days": _int(0, 7, 0, "Fill rate: toleransi hari on-time"),
    "waste.basis": _enum(("sales_excl_tax", "cogs"), "sales_excl_tax", "Waste %: penyebut"),
    "thr.oos.warn_pct": _float(0, 100, 2, "Ambang OOS rate: perlu dicek (%)"),
    "thr.oos.bad_pct": _float(0, 100, 5, "Ambang OOS rate: bermasalah (%)"),
    "thr.fill.good_pct": _float(0, 100, 95, "Ambang fill rate: baik (%)"),
    "thr.fill.warn_pct": _float(0, 100, 90, "Ambang fill rate: perlu dicek (%)"),
    "thr.waste.warn_pct": _float(0, 100, 2, "Ambang waste: perlu dicek (%)"),
    "thr.waste.bad_pct": _float(0, 100, 4, "Ambang waste: bermasalah (%)"),
    "thr.wape.warn_pct": _float(0, 500, 25, "Ambang galat forecast (WAPE): perlu dicek (%)"),
    "thr.wape.bad_pct": _float(0, 500, 40, "Ambang galat forecast (WAPE): bermasalah (%)"),
    "thr.bias.warn_pct": _float(0, 500, 5, "Ambang |bias|: perlu dicek (%)"),
    "thr.bias.bad_pct": _float(0, 500, 10, "Ambang |bias|: bermasalah (%)"),
    "thr.price.warn_pct": _float(0, 500, 10, "Ambang harga vs pasar: di atas (%)"),
    "thr.price.bad_pct": _float(0, 500, 20, "Ambang harga vs pasar: jauh di atas (%)"),
    "thr.price.below_pct": _float(0, 500, 10, "Ambang harga vs pasar: di bawah (%)"),
    "thr.price.min_samples": _int(1, 50, 3, "Harga vs pasar: minimum laporan 30 hari"),
    "thr.po_late.warn": _int(0, 1000, 1, "Ambang PO terlambat: perlu dicek"),
    "thr.po_late.bad": _int(0, 1000, 3, "Ambang PO terlambat: bermasalah"),
    "thr.cutoff.urgent_min": _int(15, 1440, 120, "Usulan belum disetujui: sisa menit = bermasalah"),
    "thr.stock_age.days": _int(1, 365, 30, "Stok > N hari (DC-1)"),
    "fresh.near_live.warn_min": _int(5, 10080, 30, "Kesegaran hampir-langsung: perlu dicek (menit)"),
    "fresh.near_live.stale_min": _int(5, 10080, 60, "Kesegaran hampir-langsung: usang (menit)"),
    "fresh.stock.warn_min": _int(5, 10080, 75, "Kesegaran stok: perlu dicek (menit)"),
    "fresh.stock.stale_min": _int(5, 10080, 135, "Kesegaran stok: usang (menit)"),
    "fresh.daily.warn_min": _int(60, 20160, 1560, "Kesegaran harian: perlu dicek (menit)"),
    "fresh.daily.stale_min": _int(60, 20160, 2880, "Kesegaran harian: usang (menit)"),
}

# Pasangan ambang (lower < upper, ketat). Fill rate: warn < good.
KPI_PAIRS = (
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

_INT_RE = re.compile(r"[+-]?[0-9]{1,12}")
_FLOAT_RE = re.compile(r"[+-]?([0-9]{1,12}(\.[0-9]{0,12})?|\.[0-9]{1,12})([eE][+-]?[0-9]{1,3})?")


def format_number(value):
    """Bilangan -> literal kanonik: bilangan bulat tanpa `.0`, selain itu repr terpendek."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return repr(number)


def default_literal(key):
    spec = KPI_REGISTRY[key]
    return spec.default if spec.type == T_ENUM else format_number(spec.default)


def parse(key, raw):
    """Nilai string -> (nilai Python, literal kanonik). ValueError(kode) dengan kode efn_insight_sm:kpi_<kode>."""
    spec = KPI_REGISTRY.get(key)
    if spec is None:
        raise ValueError("unknown_key")
    if not isinstance(raw, str):
        raise ValueError("type")
    text = raw.strip()
    if spec.type == T_ENUM:
        if text not in spec.choices:
            raise ValueError("choice")
        return text, text
    if spec.type == T_BOOL:
        if text not in ("0", "1"):
            raise ValueError("type")
        return int(text), text
    if spec.type == T_INT:
        if not _INT_RE.fullmatch(text):
            raise ValueError("type")
        number = int(text)
    else:
        if not _FLOAT_RE.fullmatch(text):
            raise ValueError("type")
        number = float(text)
        if not math.isfinite(number):
            raise ValueError("type")
    if not spec.minimum <= number <= spec.maximum:
        raise ValueError("range")
    return number, format_number(number)


def effective_value(key, raw):
    """Nilai efektif: baris aktif yang sah, selain itu default registri."""
    if raw is not None:
        try:
            return parse(key, raw)[0]
        except ValueError:
            pass
    spec = KPI_REGISTRY[key]
    return spec.default


def pair_violations(values):
    """[(lower, upper, nilai_lower, nilai_upper)] pasangan yang melanggar `lower < upper`. `values`: key -> str|None."""
    out = []
    for lower, upper in KPI_PAIRS:
        low, up = effective_value(lower, values.get(lower)), effective_value(upper, values.get(upper))
        if not low < up:
            out.append((lower, upper, low, up))
    return out


def allowed_text(key):
    """Teks bantuan UI: pilihan / rentang."""
    spec = KPI_REGISTRY[key]
    if spec.type == T_ENUM:
        return " | ".join(spec.choices)
    if spec.type == T_BOOL:
        return "0 | 1"
    return f"{format_number(spec.minimum)} .. {format_number(spec.maximum)}"
