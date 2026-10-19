"""K-a (ADR-036 §0.2, Q-PII7): work contact karyawan = identitas kerja, tidak terlindungi.

Odoo 19.0 `hr.employee._create_work_contacts(self)` membuat `res.partner` (nama, email/telepon kerja,
company) untuk karyawan tanpa work contact (dipanggil dari `create` dan `_inverse_work_contact_details`).
Context server `efn_pii_skip_default` (`SKIP_TOKEN`, tidak bisa dibuat klien RPC) hanya dipasang di sini.
Data PRIBADI karyawan (`private_*`, `emergency_*`, `hr.version`) dienkripsi di E-38b (`efn_pii_hr` lanjutan).
"""

from odoo import models

from odoo.addons.efn_pii.models import pii_model as pm


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def _create_work_contacts(self):
        return super(HrEmployee, self.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN}))._create_work_contacts()
