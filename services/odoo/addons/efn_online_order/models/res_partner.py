"""Partner generik kanal online tetap tanpa PII walau diedit setelah dipasang di kanal (UU PDP, ADR-029)."""

from odoo import _, api, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.constrains("phone", "email", "vat")
    def _check_efn_online_generic_partner(self):
        filled = self.filtered(lambda p: p.phone or p.email or p.vat)
        if not filled:
            return
        channels = (
            self.env["efn.online.channel"]
            .sudo()
            .with_context(active_test=False)
            .search([("partner_id", "in", filled.ids)], limit=1)
        )
        if channels:
            raise ValidationError(
                _(
                    "Partner generik kanal online %(code)s tidak boleh memiliki HP, email, atau NPWP.",
                    code=channels.code,
                )
            )
