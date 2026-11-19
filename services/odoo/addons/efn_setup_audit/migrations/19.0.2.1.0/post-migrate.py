"""G44 SEC-207: aturan audit ir.config_parameter (noupdate) diperluas ke kunci berawalan ``efn_``.

Kunci kill switch G44 (``efn_assortment.enforce``, ``efn_replenishment.*``, ``efn_forecast.*``) tidak cocok ``efn.%``.
Domain lama (bawaan atau sudah diubah admin) diberi cabang OR ``('key', '=like', 'efn\\_%')`` ("_" di-escape agar
bukan wildcard LIKE; evaluasi Python ``filtered_domain`` juga mendukung escape backslash). Idempoten.
"""

import logging

from odoo import SUPERUSER_ID, api
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

NEW_TERM = ("key", "=like", "efn\\_%")


def extend_domain(text):
    """Kembalikan domain baru (repr) atau ``None`` bila sudah mencakup kunci ``efn_``."""
    domain = safe_eval(text) if text else []
    if any(isinstance(term, (list, tuple)) and tuple(term) == NEW_TERM for term in domain):
        return None
    if not domain:
        return None  # kosong = semua record sudah diaudit
    return repr(["|", NEW_TERM, *domain])


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    rule = env.ref("efn_setup_audit.audit_rule_ir_config_parameter", raise_if_not_found=False)
    if not rule:
        return
    new_domain = extend_domain(rule.filter_domain)
    if new_domain:
        rule.write({"filter_domain": new_domain})
        _logger.info("EFN setup audit SEC-207: aturan ir.config_parameter kini mencakup kunci efn_*")
