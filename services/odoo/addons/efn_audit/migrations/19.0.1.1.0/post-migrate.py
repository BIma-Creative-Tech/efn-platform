"""efn_audit 19.0.1.1.0 (Gelombang 11, SEC-45/46): hapus record rule lama (noupdate) yang
memperlihatkan semua log tanpa company ke setiap auditor. Rule baru ada di
security/efn_audit_security.xml (noupdate=0). Idempoten."""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

OLD_RULES = ("efn_audit.efn_audit_log_rule_company", "efn_audit.efn_audit_log_line_rule_company")


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    for xmlid in OLD_RULES:
        rule = env.ref(xmlid, raise_if_not_found=False)
        if rule:
            rule.unlink()
            _logger.info("efn_audit: rule lama %s dihapus (SEC-45)", xmlid)
