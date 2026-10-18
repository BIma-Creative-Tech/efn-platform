"""SEC-121 (review r11, L-25i): pelanggan baru dari kasir ber-grup sempit selalu milik company POS aktif.

Grup ``group_efn_cashier_contact_creation`` hanya boleh membuat partner individu dengan ``company_id`` = company
aktif (record rule ``data/pos_cashier_groups.xml``). CE 19 tidak memberi default ``company_id`` pada partner
(partner bersama). Untuk kasir ber-grup sempit, default field = company aktif, sehingga form POS
(``point_of_sale.res_partner_action_edit_pos`` -> ``base.view_partner_form``, field company tersembunyi bagi user
satu company) maupun ``create`` tanpa ``company_id`` menghasilkan partner milik PT POS. Hanya DEFAULT: nilai yang
dikirim klien (mis. ``company_id = False`` / PT lain) tidak diubah dan tetap ditolak record rule. Atribut lain field
CE dipertahankan (override field Odoo hanya menimpa ``default``); tidak ada metode publik baru (efn_core
``test_rpc_surface``). User lain (termasuk Contact Creation standar) -> default kosong seperti CE.
"""

from odoo import fields, models

CASHIER_GROUP = "efn_setup_pos.group_efn_cashier_contact_creation"


class ResPartner(models.Model):
    _inherit = "res.partner"

    company_id = fields.Many2one(default=lambda self: self._efn_cashier_default_company())

    def _efn_cashier_default_company(self):
        user = self.env.user
        if user.has_group(CASHIER_GROUP) and not user.has_group("base.group_partner_manager"):
            return self.env.company
        return self.env["res.company"]
