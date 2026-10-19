"""E-47d (G22, L-74; docs/08 §13.6): `efn_role.sensitive_group_xmlids` menjadi parameter KONTROL efn_audit.

Spesifikasi bawaan `ir.config_parameter` (efn_audit `CONTROL_SPECS`) hanya memfilter `efn.audit.%` dan sakelar SEC-75.
Di sini filter bawaan itu DITAMBAH (OR) dengan kunci `CONTROL_PARAM_KEYS`, sehingga perubahan daftar grup sensitif
(yang menentukan peran mana boleh disinkron dari jabatan, SEC-154) selalu tercatat walau aturan admin diarsipkan, dan
lognya tidak ikut GC (`PROTECTED_LOG_MODELS` mencakup `ir.config_parameter`). Aturan admin tetap hanya bisa menambah.
"""

from odoo import api, models

from .role_policy import CONTROL_PARAM_KEYS


class EfnAuditRule(models.Model):
    _inherit = "efn.audit.rule"

    @api.model
    def _efn_builtin_rule(self, model):
        builtin = super()._efn_builtin_rule(model)
        if builtin is None or model._name != "ir.config_parameter" or not builtin.filters:
            # filters kosong = semua record sudah diaudit; tidak perlu ditambah.
            return builtin
        extra = f"[('key', 'in', {list(CONTROL_PARAM_KEYS)!r})]"
        return builtin._replace(filters=(*builtin.filters, extra))
