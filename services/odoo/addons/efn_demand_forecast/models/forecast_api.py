"""Helper bersama RPC forecast-worker -> Odoo (G44, docs/04 §28.2, ADR-046 §3.1, L-145). Bukan model.

- Pemanggil = user teknis ``efn_forecast_<kode>``: Internal User + ``group_forecast_bot`` SAJA, aktif, bukan portal,
  **tepat satu company** (pola ``efn_promo._efn_promo_check_caller``). Grup tambahan -> ditolak (fail closed).
- Galat validasi -> ``UserError("efn_forecast:<kode>")`` (json2 Odoo 19 = HTTP 422, L-15; kontrak menulis 400 —
  penyimpangan dicatat di README/laporan). ``company_code`` / run milik PT lain -> ``AccessError(
  "efn_forecast:company_mismatch")`` (HTTP 403). Pesan tanpa detail internal.
- Batas: ≤ 500 entri/baris per panggilan, payload ≤ 512 KB (dihitung dari JSON kanonik argumen; gateway juga
  membatasi body), angka finite ≥ 0.
"""

import json
import logging
import math
import re
from datetime import date

import pytz

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tools import frozendict

_logger = logging.getLogger(__name__)

GROUP_BOT = "efn_demand_forecast.group_forecast_bot"
SCHEMA_VERSION = 1
MAX_ITEMS = 500
MAX_PAYLOAD_BYTES = 512 * 1024
MAX_HORIZON_DAYS = 120
MAX_CHUNKS = 10000
DEFAULT_TZ = "Asia/Jakarta"
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
MODEL_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
COMPANY_CODE_RE = re.compile(r"^[A-Z]{2,8}$")
ERROR_CODES = frozenset(
    {
        "invalid_input",
        "unsupported_schema",
        "too_many_items",
        "payload_too_large",
        "unknown_run",
        "run_conflict",
        "run_closed",
        "kind_mismatch",
        "invalid_chunk",
        "incomplete_run",
    }
)


MAX_QTY_PARAM = "efn_forecast.max_qty_per_line"
DEFAULT_MAX_QTY = 100000.0


def max_qty(env):
    """SEC-211: plafon nilai per baris (yhat/p10/p90/sigma/min/max/basis).

    Di atasnya baris ditolak ``value_too_large``.
    """
    try:
        value = float(env["ir.config_parameter"].sudo().get_param(MAX_QTY_PARAM) or DEFAULT_MAX_QTY)
    except (TypeError, ValueError):
        return DEFAULT_MAX_QTY
    return value if math.isfinite(value) and value > 0 else DEFAULT_MAX_QTY


def forecast_error(code):
    assert code in ERROR_CODES, code
    return UserError(f"efn_forecast:{code}")


def company_mismatch():
    return AccessError("efn_forecast:company_mismatch")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0
    )


def parse_date(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_rfc3339_utc(value):
    if not isinstance(value, str) or not RFC3339_UTC_RE.match(value):
        return None
    try:
        return fields.Datetime.to_datetime(value[:19].replace("T", " "))
    except ValueError:
        return None


def check_payload(payload, allowed_keys, required_keys=None):
    """Validasi bentuk dasar (``additionalProperties: false``, ``schema_version``, ukuran)."""
    if not isinstance(payload, dict) or set(payload) - set(allowed_keys):
        raise forecast_error("invalid_input")
    if set(required_keys if required_keys is not None else allowed_keys) - set(payload):
        raise forecast_error("invalid_input")
    if payload.get("schema_version") != SCHEMA_VERSION or not is_int(payload.get("schema_version")):
        raise forecast_error("unsupported_schema")
    try:
        size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise forecast_error("invalid_input") from None
    if size > MAX_PAYLOAD_BYTES:
        raise forecast_error("payload_too_large")


def check_items(items):
    if not isinstance(items, list):
        raise forecast_error("invalid_input")
    if len(items) > MAX_ITEMS:
        raise forecast_error("too_many_items")


def allowed_groups(env):
    base = env.ref("base.group_user") | env.ref(GROUP_BOT)
    return base | base.all_implied_ids


def check_caller(env):
    """User teknis forecast (Internal User + group_forecast_bot saja, tepat satu company).

    Kembalikan ``(env_sudo, company)``; env sudo dengan context bersih (context klien dibuang)."""
    bare = env(context=frozendict())
    denied = AccessError("efn_forecast:access_denied")
    user = bare.user
    if not user.has_group(GROUP_BOT) or user.share or not user.active:
        raise denied
    user_sudo = user.sudo()
    companies = user_sudo.company_ids
    if len(companies) != 1:
        raise denied
    if user_sudo.all_group_ids - allowed_groups(bare(su=True)):
        _logger.warning("EFN forecast: user teknis #%s punya grup tambahan; RPC ditolak", user.id)
        raise denied
    tz = user_sudo.tz or DEFAULT_TZ
    clean = env(context=frozendict(active_test=True, tz=tz, allowed_company_ids=companies.ids), su=True)
    return clean, companies


def check_company_code(company, code):
    if not isinstance(code, str) or not COMPANY_CODE_RE.match(code):
        raise forecast_error("invalid_input")
    if code != (company.efn_brand_code or ""):
        raise company_mismatch()


def local_today(env):
    tz = env.context.get("tz") or DEFAULT_TZ
    try:
        zone = pytz.timezone(tz)
    except pytz.UnknownTimeZoneError:
        zone = pytz.timezone(DEFAULT_TZ)
    return pytz.utc.localize(fields.Datetime.now()).astimezone(zone).date()
