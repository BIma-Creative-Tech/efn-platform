"""G26 L-99: pemisahan tugas Verifikator Pembayaran (docs/04 §17.5)."""

from odoo import api, models

TRANSFER_SOD_RULES = (
    (
        "SOD-PAY-01",
        "Verifikator Pembayaran ⟂ User POS",
        "efn_online_transfer.group_payment_verifier",
        "point_of_sale.group_pos_user",
        "block",
        "Penerima/penyetuju bukti transfer bukan kasir yang memegang uang di gerai (docs/04 §17.5, L-99).",
    ),
    (
        "SOD-PAY-02",
        "Verifikator Pembayaran ⟂ User Inventory",
        "efn_online_transfer.group_payment_verifier",
        "stock.group_stock_user",
        "block",
        "Penyetuju pembayaran bukan petugas yang menyerahkan barang pesanan (docs/04 §17.5, L-99).",
    ),
)


class EfnSodRule(models.Model):
    _inherit = "efn.sod.rule"

    @api.model
    def _efn_default_rule_specs(self):
        return tuple(super()._efn_default_rule_specs()) + TRANSFER_SOD_RULES
