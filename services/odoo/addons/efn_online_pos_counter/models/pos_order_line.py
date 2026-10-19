"""Baris pelunasan pesanan online: label pesanan (nomor SO, bukan PII) dari lookup, dikirim balik saat sync."""

from odoo import api, fields, models


class PosOrderLine(models.Model):
    _inherit = "pos.order.line"

    efn_online_order_label = fields.Char(
        string="Pesanan online (pelunasan)",
        copy=False,
        help="E-45: nomor pesanan online yang dilunasi baris ini (dari lookup kode bayar). Divalidasi server saat "
        "sync: hanya produk pelunasan PT, qty 1, nominal = tagihan pesanan, klaim lookup sah.",
    )

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        # Daftar kosong = semua field dimuat (pos.load.mixin); jangan dipersempit.
        if fields_list and "efn_online_order_label" not in fields_list:
            fields_list = [*fields_list, "efn_online_order_label"]
        return fields_list
