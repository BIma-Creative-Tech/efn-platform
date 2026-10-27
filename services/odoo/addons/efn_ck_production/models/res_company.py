"""Ambang peringatan yield/varians/waste per PT (K25-08: "ambang peringatan configurable")."""

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_ck_yield_warn_pct = fields.Float(
        string="Ambang yield minimum (%)",
        default=95.0,
        help="MO dengan yield (hasil aktual / rencana) di bawah ambang ini ditandai 'di luar ambang' + aktivitas.",
    )
    efn_ck_variance_warn_pct = fields.Float(
        string="Ambang varians konsumsi (%)",
        default=5.0,
        help="Komponen yang dikonsumsi melebihi/kurang dari BoM lebih dari persentase ini ditandai.",
    )
    efn_ck_waste_warn_value = fields.Monetary(
        string="Ambang nilai waste per hari",
        currency_field="currency_id",
        help="Total nilai scrap per gudang per hari di atas angka ini ditandai pada laporan waste. 0 = nonaktif.",
    )
