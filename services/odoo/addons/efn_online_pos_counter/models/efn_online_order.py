"""Pesanan bayar di kasir: faktur/refund dibayar di jurnal kliring PT penjual (docs/04 §12.2 no. 6, L-55), bukan
jurnal titipan Holding."""

from odoo import models


class EfnOnlineOrder(models.Model):
    _inherit = "efn.online.order"

    def _efn_payment_journal(self):
        self.ensure_one()
        if self.payment_mode == "counter":
            return self.company_id.sudo().efn_online_counter_journal_id
        return super()._efn_payment_journal()
