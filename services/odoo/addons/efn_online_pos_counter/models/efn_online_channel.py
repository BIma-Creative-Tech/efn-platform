"""Kanal online: metode 'counter' hanya bila PT punya data kliring (produk pelunasan + jurnal kliring).

Saat kanal diberi metode 'counter' (create/write), data PLACEHOLDER PT itu dibuat bila belum ada (butuh chart of
accounts). Constraint memastikan konfigurasi tidak bisa setengah jadi.
"""

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.efn_online_order.models.efn_online_common import parse_payment_modes


def _wants_counter(value):
    return "counter" in (parse_payment_modes(value) or [])


class EfnOnlineChannel(models.Model):
    _inherit = "efn.online.channel"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if _wants_counter(vals.get("payment_modes")):
                company = self.env["res.company"].browse(vals.get("company_id") or self.env.company.id)
                company._efn_online_counter_ensure()
        return super().create(vals_list)

    def write(self, vals):
        if _wants_counter(vals.get("payment_modes")) or (
            "company_id" in vals and any(_wants_counter(c.payment_modes) for c in self)
        ):
            companies = self.env["res.company"].browse(vals["company_id"]) if vals.get("company_id") else None
            (companies or self.company_id)._efn_online_counter_ensure()
        return super().write(vals)

    @api.constrains("payment_modes", "company_id")
    def _check_efn_counter_setup(self):
        for channel in self:
            if "counter" in channel._efn_online_payment_modes() and not channel.company_id._efn_online_counter_ready():
                raise ValidationError(
                    _(
                        "Metode 'counter' (bayar di kasir) membutuhkan produk pelunasan dan jurnal kliring di company "
                        "%(company)s (pastikan chart of accounts terpasang).",
                        company=channel.company_id.name,
                    )
                )
