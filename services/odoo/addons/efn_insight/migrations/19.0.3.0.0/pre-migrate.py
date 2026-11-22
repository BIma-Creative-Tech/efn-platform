"""efn_insight 19.0.3.0.0 (G52, docs/04 §28.15.2): pindahkan xmlid 5 grup persona + privilege dari efn_insight_sm
sebelum XML grup di efn_insight/security dimuat (record sama, keanggotaan user tetap). SQL sama dengan
efn_insight_sm/migrations/19.0.2.0.0/pre-migrate.py; idempoten."""

from odoo.addons.efn_insight.lib.persona_xmlids import rename_persona_xmlids


def migrate(cr, version):
    if not version:
        return
    rename_persona_xmlids(cr)
