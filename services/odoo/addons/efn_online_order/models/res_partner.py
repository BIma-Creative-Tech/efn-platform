"""Partner generik kanal online tetap tanpa PII walau diedit setelah dipasang di kanal (UU PDP, ADR-029).

G21 E-39 (docs/04 §13.5): penanda ``efn_online_invoice_buyer`` = partner pembeli yang dibuat
``efn_online_set_invoice_party`` (selalu terlindungi efn_pii). Hanya partner bertanda ini yang dipakai ulang oleh
pencarian pembeli berikutnya.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    efn_online_invoice_buyer = fields.Boolean(
        string="EFN: Pembeli faktur online",
        readonly=True,
        copy=False,
        index=True,
        help="Dibuat oleh kanal online untuk faktur atas nama pembeli (E-39). Data pribadi terenkripsi efn_pii.",
    )

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
