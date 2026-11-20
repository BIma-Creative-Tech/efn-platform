"""Konstanta & helper bersama efn_expiry_monitor (bukan model, tanpa RPC).

Flag utama ``efn_expiry_monitor.enabled`` (docs/04 §28.10): default ``false``; hanya nilai persis ``true``/``1``
yang menyalakan (fail closed: modul terpasang tanpa ambang kategori tidak mengubah perilaku CE).
"""

import math

from odoo import fields

ENABLED_PARAM = "efn_expiry_monitor.enabled"
# Q-SM4 (default sampai klien menjawab): kepala toko <= 50 %, Markdown Manager <= 90 %.
STORE_MAX_PARAM = "efn_expiry_monitor.max_discount_store_head"
MANAGER_MAX_PARAM = "efn_expiry_monitor.max_discount_markdown_manager"
DEFAULT_STORE_MAX = 50.0
DEFAULT_MANAGER_MAX = 90.0
# Batas baris lot per sesi POS (ukuran data preload; sisa lot tetap bisa diambil online per produk).
POS_LOT_LIMIT_PARAM = "efn_expiry_monitor.pos_lot_limit"
DEFAULT_POS_LOT_LIMIT = 20000
# TTL persetujuan override supervisor POS (detik).
OVERRIDE_TTL_PARAM = "efn_expiry_monitor.override_ttl_seconds"
DEFAULT_OVERRIDE_TTL = 1800

GROUP_MARKDOWN_MANAGER = "efn_expiry_monitor.group_markdown_manager"
GROUP_OVERRIDE = "efn_expiry_monitor.group_expiry_override"
GROUP_STOCK_USER = "stock.group_stock_user"
GROUP_STOCK_MANAGER = "stock.group_stock_manager"

STAGES = [("near", "Mendekati kedaluwarsa"), ("markdown", "Perlu markdown"), ("expired", "Kedaluwarsa")]
STAGE_RANK = {"near": 1, "markdown": 2, "expired": 3}
LOT_SOURCES = [
    ("gs1", "Pindaian GS1 (01)(10)"),
    ("manual", "Dipilih kasir"),
    ("fefo", "FEFO otomatis"),
    ("auto", "Otomatis (core POS)"),
]
EXPLICIT_SOURCES = ("gs1", "manual")


def is_enabled(env):
    value = (env["ir.config_parameter"].sudo().get_param(ENABLED_PARAM) or "false").strip().lower()
    return value in ("true", "1")


def param_float(env, key, default, low, high):
    raw = env["ir.config_parameter"].sudo().get_param(key)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or not (low <= value <= high):
        return default
    return value


def param_int(env, key, default, low, high):
    return int(param_float(env, key, default, low, high))


def lot_blocked(lot, at):
    """True bila lot sudah melewati ``expiration_date`` atau ``removal_date`` pada waktu ``at`` (UTC naif)."""
    return bool((lot.expiration_date and lot.expiration_date <= at) or (lot.removal_date and lot.removal_date <= at))


def days_left(record, lot, today):
    """Sisa hari sampai ``expiration_date`` (tanggal lokal TZ konteks) relatif ``today``; None bila tanpa tanggal."""
    if not lot.expiration_date:
        return None
    local = fields.Datetime.context_timestamp(record, lot.expiration_date).date()
    return (local - today).days


def finite_number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
