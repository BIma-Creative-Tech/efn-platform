"""Rincian estimasi pengajuan (opsional).

Port dari `custom_petty_cash/models/petty_cash_request_line.py` (ADR-049). Perubahan: `company_id` related stored
(rule multi-company); setiap perubahan baris = penyuntingan pengajuan (penyunting dicatat untuk empat mata) dan
hanya saat pengajuan draft.
"""

from odoo import api, fields, models


class EfnPettyCashRequestLine(models.Model):
    _name = "efn.petty.cash.request.line"
    _description = "EFN Rincian Estimasi Pengajuan Kas Kecil"
    _order = "sequence, id"
    _check_company_auto = True

    request_id = fields.Many2one(
        "efn.petty.cash.request",
        string="Pengajuan",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="request_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Keterangan", required=True)
    account_id = fields.Many2one("account.account", string="Perkiraan Akun", check_company=True)
    amount = fields.Monetary(string="Estimasi", currency_field="currency_id")
    currency_id = fields.Many2one(related="request_id.currency_id")

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.request_id._efn_mark_editor()
        return lines

    def write(self, vals):
        requests = self.request_id
        res = super().write(vals)
        (requests | self.request_id)._efn_mark_editor()
        return res

    def unlink(self):
        requests = self.request_id
        res = super().unlink()
        requests.exists()._efn_mark_editor()
        return res
