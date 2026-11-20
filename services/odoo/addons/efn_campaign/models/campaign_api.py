"""Helper bersama RPC bot kampanye -> Odoo (G50, docs/04 §28.7, ADR-047 §3.4, L-155/L-156). Bukan model.

- Pemanggil = user teknis ``efn_campaign_<kode>`` (segment-worker untuk usulan, BFF campaign-sync untuk klaim/ack):
  Internal User + ``group_campaign_bot`` SAJA, aktif, bukan portal, **tepat satu company** (pola
  ``efn_promo._efn_promo_check_caller`` / ``efn_demand_forecast.forecast_api.check_caller``). Grup tambahan -> ditolak.
- Galat validasi -> ``UserError("efn_campaign:<kode>")`` (json2 Odoo 19 = HTTP 422, L-15). Bukan bot ->
  ``AccessError("efn_campaign:access_denied")`` (403); ``company_code`` PT lain -> ``AccessError(
  "efn_campaign:company_mismatch")`` (403). Pesan tanpa detail internal.
- Sakelar ``efn_campaign.enabled`` (default ``false``, docs/04 §28.10 ``PROMO_CAMPAIGN_ENABLED``) dengan override
  opsional per PT ``efn_campaign.enabled.<KODE>``; mati -> ``UserError("efn_campaign:disabled")`` (422, tidak di-retry).
"""

import json
import logging
import math
import re

from odoo.exceptions import AccessError, UserError
from odoo.tools import frozendict

_logger = logging.getLogger(__name__)

GROUP_BOT = "efn_campaign.group_campaign_bot"
GROUP_USER = "efn_campaign.group_campaign_user"
GROUP_APPROVER = "efn_campaign.group_campaign_approver"
ENABLED_PARAM = "efn_campaign.enabled"
SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024
UUID4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
COMPANY_CODE_RE = re.compile(r"^[A-Z]{2,8}$")
SEGMENT_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
METRIC_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
# Ringkasan teks dari worker tidak boleh memuat pengenal member (L-155): rangkaian >= 6 digit (HP/ID) atau email.
PII_RE = re.compile(r"\d{6,}|[^@\s]+@[^@\s]+\.[^@\s]+")
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
ERROR_CODES = frozenset(
    {
        "invalid_input",
        "unsupported_schema",
        "payload_too_large",
        "too_many_items",
        "pii_suspected",
        "disabled",
    }
)


def campaign_error(code):
    assert code in ERROR_CODES, code
    return UserError(f"efn_campaign:{code}")


def company_mismatch():
    return AccessError("efn_campaign:company_mismatch")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def is_enabled(env, company):
    """Sakelar global + override per PT (kunci bersufiks kode, menang atas global)."""
    params = env["ir.config_parameter"].sudo()
    value = None
    if company and company.efn_brand_code:
        value = params.get_param(f"{ENABLED_PARAM}.{company.efn_brand_code}")
    if value is None or value is False:
        value = params.get_param(ENABLED_PARAM, "false")
    return str(value).strip().lower() in TRUE_VALUES


def check_enabled(env, company):
    if not is_enabled(env, company):
        raise campaign_error("disabled")


def check_payload(payload, allowed_keys, required_keys):
    """Validasi bentuk dasar (``additionalProperties: false``, ``schema_version``, ukuran)."""
    if not isinstance(payload, dict) or set(payload) - set(allowed_keys):
        raise campaign_error("invalid_input")
    if set(required_keys) - set(payload):
        raise campaign_error("invalid_input")
    if not is_int(payload.get("schema_version")) or payload.get("schema_version") != SCHEMA_VERSION:
        raise campaign_error("unsupported_schema")
    try:
        size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise campaign_error("invalid_input") from None
    if size > MAX_PAYLOAD_BYTES:
        raise campaign_error("payload_too_large")


def allowed_groups(env):
    base = env.ref("base.group_user") | env.ref(GROUP_BOT)
    return base | base.all_implied_ids


def check_caller(env):
    """User teknis kampanye (Internal User + group_campaign_bot saja, tepat satu company).

    Kembalikan ``(env_sudo, company)``; env sudo dengan context bersih (context klien dibuang)."""
    bare = env(context=frozendict())
    denied = AccessError("efn_campaign:access_denied")
    user = bare.user
    if not user.has_group(GROUP_BOT) or user.share or not user.active:
        raise denied
    user_sudo = user.sudo()
    companies = user_sudo.company_ids
    if len(companies) != 1:
        raise denied
    if user_sudo.all_group_ids - allowed_groups(bare(su=True)):
        _logger.warning("EFN campaign: user teknis #%s punya grup tambahan; RPC ditolak", user.id)
        raise denied
    clean = env(
        context=frozendict(active_test=True, tz=user_sudo.tz or "Asia/Jakarta", allowed_company_ids=companies.ids),
        su=True,
    )
    return clean, companies


def check_company_code(company, code):
    if not isinstance(code, str) or not COMPANY_CODE_RE.match(code):
        raise campaign_error("invalid_input")
    if code != (company.efn_brand_code or ""):
        raise company_mismatch()


def check_int_list(values, max_items):
    """Daftar id integer > 0, tanpa duplikat (urutan dipertahankan)."""
    if not isinstance(values, list) or not values:
        raise campaign_error("invalid_input")
    if len(values) > max_items:
        raise campaign_error("too_many_items")
    if any(not is_int(v) or v < 1 for v in values):
        raise campaign_error("invalid_input")
    return list(dict.fromkeys(values))
