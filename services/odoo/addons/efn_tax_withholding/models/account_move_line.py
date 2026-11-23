"""G59-A: nomor & tanggal bukti potong (dari Coretax) pada baris pajak PPh + kolom Daftar Pemotongan PPh.

Field bukti potong = informasi, bukan nilai akuntansi: boleh diisi setelah posted (bukan field lock date / hash CE,
`account_move_line.write` 19.0 hanya memeriksa field terlindung). Penulisan dibatasi ke user akuntansi.
"""

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

BUPOT_FIELDS = frozenset({"efn_bupot_number", "efn_bupot_date"})
ACCOUNTING_GROUPS = "account.group_account_user,account.group_account_manager"


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    efn_bupot_number = fields.Char(string="Nomor Bukti Potong", copy=False)
    efn_bupot_date = fields.Date(string="Tanggal Bukti Potong", copy=False)
    efn_pph_kind = fields.Selection(related="tax_line_id.efn_pph_kind", string="Jenis PPh")
    efn_pph_object_code = fields.Char(related="tax_line_id.efn_bupot_object_code", string="Kode Objek Pajak")
    # Dibaca lewat ORM -> NPWP terdekripsi oleh efn_pii_base_vat (kolom `vat` berisi ciphertext; jangan SQL view).
    efn_pph_partner_vat = fields.Char(
        related="partner_id.commercial_partner_id.vat",
        string="NPWP",
        readonly=True,
        groups=ACCOUNTING_GROUPS,
    )

    @api.constrains("efn_bupot_number", "efn_bupot_date", "tax_line_id")
    def _check_efn_bupot_line(self):
        for line in self:
            if (line.efn_bupot_number or line.efn_bupot_date) and not line.tax_line_id.efn_pph_kind:
                raise ValidationError(self.env._("Nomor/tanggal bukti potong hanya untuk baris pajak PPh."))

    def write(self, vals):
        if BUPOT_FIELDS.intersection(vals) and not self.env.su:
            user = self.env.user
            if not any(user.has_group(group) for group in ACCOUNTING_GROUPS.split(",")):
                raise AccessError(self.env._("Hanya user Akuntansi yang boleh mengisi bukti potong PPh."))
        return super().write(vals)
