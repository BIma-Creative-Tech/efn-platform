"""SEC-136 / SEC-153: wizard merge kontak tidak menyalin/menulis data partner terlindungi dalam plaintext.

- `_update_values(src_partners, dst_partner)` (base 19.0) membaca nilai sumber lewat ORM (terdekripsi) lalu
  `dst_partner.write(values)`; field `copy=False` (termasuk `efn_pii_protected`) dilewati. Bila salah satu partner
  terlindungi, tujuan dijadikan terlindungi lebih dulu (enkripsi nilai lama + turunannya).
- `_log_merge_operation(src_partners, dst_partner)`: base hanya `_logger.info` id; `mail` (19.0,
  `mail/wizard/base_partner_merge_automatic_wizard.py`) memanggil super lalu `dst_partner.message_post(body=
  "Merged with the following partners: <nama> <<email>> (ID n), ...")` -> nama & email sumber tersimpan plaintext di
  `mail.message.body`. Bila ada partner terlindungi, pesan diganti versi tanpa nama/email (hanya id). Override ini
  tidak memanggil super (agar pesan mail tidak terbentuk); log id base ditulis ulang di sini.
"""

import logging

from odoo import models
from odoo.tools import SQL

from .fields_pii import PROTECTED_FIELD

_logger = logging.getLogger("odoo.addons.base.wizard.base_partner_merge")


class MergePartnerAutomatic(models.TransientModel):
    _inherit = "base.partner.merge.automatic.wizard"

    def _update_values(self, src_partners, dst_partner):
        involved = (src_partners | dst_partner).sudo()
        if any(involved.mapped(PROTECTED_FIELD)) and not dst_partner.sudo()[PROTECTED_FIELD]:
            dst_partner._efn_pii_protect()
        return super()._update_values(src_partners, dst_partner)

    def _efn_pii_merge_involves_protected(self, src_partners, dst_partner):
        ids = tuple((src_partners | dst_partner).ids)
        if not ids:
            return False
        # sumber masih ada (unlink terjadi setelah log); penanda tujuan bisa masih tertunda di cache -> flush
        self.env["res.partner"].flush_model([PROTECTED_FIELD])
        self.env.cr.execute(SQL("SELECT 1 FROM res_partner WHERE id IN %s AND efn_pii_protected LIMIT 1", ids))
        return bool(self.env.cr.fetchone())

    def _log_merge_operation(self, src_partners, dst_partner):
        if not self._efn_pii_merge_involves_protected(src_partners, dst_partner):
            return super()._log_merge_operation(src_partners, dst_partner)
        _logger.info("(uid = %s) merged the partners %r with %s", self.env.uid, src_partners.ids, dst_partner.id)
        dst_partner.message_post(
            body=self.env._(
                "Digabung dengan kontak: %s (data pribadi terlindungi tidak dicatat di pesan ini).",
                ", ".join(f"ID {partner_id}" for partner_id in src_partners.ids),
            )
        )
        return None
