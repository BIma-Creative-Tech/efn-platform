"""Blokir pembayaran ganda dan tampilkan pembayaran belum teralokasi (port ADR-049 dari
`custom_account_reconcile/models/account_payment.py`, G58 §3.1, Q-G58-4).

CE 19 hanya memberi PERINGATAN (`duplicate_payment_ids`, state draft/in_process). EFN memblokir posting bila ada
kembaran yang sudah posted (partner, nominal, tanggal, arah, company sama) kecuali `efn_duplicate_checked`.
Pencarian kembaran memakai `_fetch_duplicate_reference` CE (bukan `search` sendiri seperti sumber).
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import formatLang


class AccountPayment(models.Model):
    _inherit = "account.payment"

    efn_duplicate_checked = fields.Boolean(
        string="Sudah Dicek Bukan Duplikat",
        copy=False,
        help="Centang untuk menyatakan pembayaran lain dengan partner, nominal, dan tanggal yang sama memang transaksi "
        "terpisah, lalu post.",
    )
    efn_is_unapplied = fields.Boolean(
        string="Belum Teralokasi",
        compute="_compute_efn_is_unapplied",
        store=True,
        help="Sudah di-post tetapi tidak melunasi faktur/tagihan apa pun — tampil di laporan umur sebagai item "
        "terbuka.",
    )

    @api.depends("state", "is_reconciled")
    def _compute_efn_is_unapplied(self):
        for payment in self:
            payment.efn_is_unapplied = payment.state in ("in_process", "paid") and not payment.is_reconciled

    def action_post(self):
        for payment in self.filtered(lambda p: not p.efn_duplicate_checked):
            twins = payment._fetch_duplicate_reference(matching_states=("in_process", "paid")).get(payment.id)
            if twins:
                raise UserError(
                    _(
                        "%(partner)s sudah punya pembayaran posted sebesar %(amount)s bertanggal %(date)s: "
                        "%(twins)s.\n\nBila ini memang pembayaran kedua yang terpisah, centang "
                        '"Sudah Dicek Bukan Duplikat" lalu post lagi. Bila transaksi yang sama terinput dua kali, '
                        "batalkan yang ini.",
                        partner=payment.partner_id.display_name,
                        amount=formatLang(self.env, payment.amount, currency_obj=payment.currency_id),
                        date=payment.date,
                        twins=", ".join(twin.name or str(twin.id) for twin in twins),
                    )
                )
        return super().action_post()
