"""efn_expiry_monitor 19.0.1.1.0 (G45 SEC-218): cabut grup supervisor override dari rule "semua outlet" yang
memberi tulis/hapus (data noupdate). Rule baca-saja untuk grup itu dibuat oleh XML. Idempoten."""

import logging

from odoo import SUPERUSER_ID, Command, api

_logger = logging.getLogger(__name__)

RULES = (
    "efn_expiry_monitor.efn_expiry_alert_rule_all_outlets",
    "efn_expiry_monitor.efn_lot_markdown_rule_all_outlets",
)


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    group = env.ref("efn_expiry_monitor.group_expiry_override", raise_if_not_found=False)
    if not group:
        return
    for xmlid in RULES:
        rule = env.ref(xmlid, raise_if_not_found=False)
        if rule and group in rule.groups:
            rule.write({"groups": [Command.unlink(group.id)]})
            _logger.info("efn_expiry_monitor: grup override dicabut dari %s (SEC-218)", xmlid)
