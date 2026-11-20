from odoo import fields, models


class LoyaltyCard(models.Model):
    """Penanda kampanye pembuat voucher (G50). Tidak mengubah perilaku CE kartu."""

    _inherit = "loyalty.card"

    efn_campaign_id = fields.Many2one(
        "efn.campaign", string="Kampanye EFN", index=True, readonly=True, copy=False, ondelete="set null"
    )
