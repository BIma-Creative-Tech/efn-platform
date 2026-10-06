"""SEC-43: parameter retensi `efn.audit.retention_days` divalidasi di kode (batas minimum).

Nilai di bawah `MIN_RETENTION_DAYS` (kecuali 0 = simpan selamanya) atau bukan bilangan bulat
ditolak dengan pesan jelas. Perubahan parameter `efn.audit.*` selalu diaudit (spesifikasi bawaan
`CONTROL_SPECS`, models/audit_policy.py) dan lognya tidak ikut GC.
"""

from odoo import api, models
from odoo.exceptions import ValidationError

from .audit_policy import RETENTION_PARAM, parse_retention, retention_error


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.constrains("key", "value")
    def _check_efn_audit_retention(self):
        for param in self:
            if param.key != RETENTION_PARAM:
                continue
            message = retention_error(parse_retention(param.value))
            if message:
                raise ValidationError(message)
