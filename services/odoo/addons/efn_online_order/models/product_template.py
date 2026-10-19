"""Flag katalog online per kanal (F-55): tanpa modul ``website`` (docs/08 §8 prinsip 2)."""

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

MAX_DESCRIPTION = 2000
HTML_RE = re.compile(r"<\s*[A-Za-z/!]")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    efn_online_channel_ids = fields.Many2many(
        "efn.online.channel",
        "efn_online_channel_product_template_rel",
        "product_tmpl_id",
        "channel_id",
        string="Kanal online",
        help="Kanal Super-App yang menampilkan produk ini (harga & pajak tetap dari pricelist/pajak Odoo).",
    )
    efn_online_description = fields.Text(
        string="Deskripsi online",
        translate=True,
        help="Teks polos (tanpa HTML) maks. 2.000 karakter; dirender sebagai teks di Super-App.",
    )
    efn_online_sequence = fields.Integer(string="Urutan online", default=10)
    efn_online_sold_out = fields.Boolean(
        string="Habis (online)",
        copy=False,
        help="G22 W-04 B-3 (docs/04 §14.B): ditandai Ops saat menu habis. Stok online = 'out' dan quote/place menolak "
        "produk ini (issue out_of_stock) di semua kanal. Tidak memengaruhi POS kasir.",
    )

    @api.constrains("efn_online_channel_ids", "company_id", "sale_ok", "taxes_id")
    def _check_efn_online_channels(self):
        for tmpl in self:
            for channel in tmpl.sudo().efn_online_channel_ids:
                if tmpl.company_id and tmpl.company_id != channel.company_id:
                    raise ValidationError(
                        _("Produk milik company lain tidak dapat dipublikasikan di kanal %(code)s.", code=channel.code)
                    )
                if not tmpl.sale_ok:
                    raise ValidationError(_("Produk online harus dapat dijual (Can be Sold)."))
                taxes = tmpl.sudo().taxes_id
                if taxes and not taxes._filter_taxes_by_company(channel.company_id):
                    # Cegah produk lintas PT yang pajak jualnya hanya milik PT lain (temuan #28).
                    raise ValidationError(
                        _("Pajak jual produk tidak memuat pajak company kanal %(code)s.", code=channel.code)
                    )

    @api.constrains("efn_online_description")
    def _check_efn_online_description(self):
        for tmpl in self:
            text = tmpl.efn_online_description or ""
            if len(text) > MAX_DESCRIPTION:
                raise ValidationError(_("Deskripsi online maksimal 2.000 karakter."))
            if HTML_RE.search(text):
                raise ValidationError(_("Deskripsi online harus teks polos (tanpa HTML)."))
