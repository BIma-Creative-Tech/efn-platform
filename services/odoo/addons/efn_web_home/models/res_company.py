"""Branding beranda per PT (port dari core/custom_home_console/models/res_company.py, ADR-049; field diberi awalan efn_)."""

from odoo import fields, models

DEFAULT_ACCENT = "#0b5d4b"


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_brand_accent_color = fields.Char(
        string="Warna Aksen Beranda",
        default=DEFAULT_ACCENT,
        help="Warna hex (#RRGGBB) untuk aksen beranda EFN. Nilai tidak valid diganti warna default.",
    )
    efn_brand_logo_home = fields.Image(
        string="Logo Beranda",
        max_width=512,
        max_height=512,
        help="Logo di header beranda. Kosong = logo company standar.",
    )
    efn_home_announcement_html = fields.Html(
        string="Pengumuman Beranda",
        sanitize=True,
        help="Banner di beranda untuk user PT ini. User dapat menyembunyikannya selama 24 jam.",
    )
    efn_home_announcement_active = fields.Boolean(string="Tampilkan Pengumuman", default=False)
