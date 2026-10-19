"""Penanda jurnal akrual Poin EFN pada ``account.move`` (SEC-156, r14).

Idempotensi akrual tidak lagi berdasarkan ``ref`` saja: jurnal dianggap sudah ada hanya bila ``account.move`` ditautkan
lewat ``efn_loyalty_accrual_id`` (diisi kode akrual ber-sudo saat membuat jurnal) **dan** berstatus ``posted``.
Field ini tidak dapat diisi/diubah pengguna (UI maupun RPC, termasuk sudo tanpa konteks penanda kode akrual), sehingga
akuntan tidak bisa "memalsukan" jurnal akrual dengan membuat jurnal draft/manual ber-ref sama.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

ACCRUAL_LINK_FIELD = "efn_loyalty_accrual_id"
# Konteks penanda: hanya ``efn.loyalty.accrual._efn_create_move`` (sudo) yang memasangnya.
ACCRUAL_LINK_CONTEXT = "efn_loyalty_accrual_link"


class AccountMove(models.Model):
    _inherit = "account.move"

    efn_loyalty_accrual_id = fields.Many2one(
        "efn.loyalty.accrual",
        string="Akrual Poin EFN",
        readonly=True,
        copy=False,
        index="btree_not_null",
        ondelete="restrict",
        help="Diisi sistem: jurnal ini dibuat oleh akrual harian Poin EFN (SEC-156).",
    )

    def _efn_check_accrual_link_write(self):
        if not (self.env.su and self.env.context.get(ACCRUAL_LINK_CONTEXT)):
            raise AccessError(_("Tautan akrual Poin EFN pada jurnal hanya diisi sistem."))

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get(ACCRUAL_LINK_FIELD) for vals in vals_list):
            self._efn_check_accrual_link_write()
        return super().create(vals_list)

    def write(self, vals):
        if ACCRUAL_LINK_FIELD in vals:
            self._efn_check_accrual_link_write()
        return super().write(vals)
