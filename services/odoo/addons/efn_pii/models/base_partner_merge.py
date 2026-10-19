"""SEC-136: wizard merge kontak (base) tidak menyalin data partner terlindungi ke tujuan plaintext.

`_update_values(src_partners, dst_partner)` (19.0) membaca nilai sumber lewat ORM (terdekripsi) lalu
`dst_partner.write(values)`; field `copy=False` (termasuk `efn_pii_protected`) dilewati. Bila salah satu
partner terlindungi, tujuan dijadikan terlindungi lebih dulu (enkripsi nilai lama + turunannya).
"""

from odoo import models

from .fields_pii import PROTECTED_FIELD


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _update_values(self, src_partners, dst_partner):
        involved = (src_partners | dst_partner).sudo()
        if any(involved.mapped(PROTECTED_FIELD)) and not dst_partner.sudo()[PROTECTED_FIELD]:
            dst_partner._efn_pii_protect()
        return super()._update_values(src_partners, dst_partner)
