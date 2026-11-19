"""Cegah duplikasi aktivitas CE ``product_expiry`` untuk lot yang dikelola efn_expiry_monitor.

CE 19 (``stock.lot._alert_date_exceeded``, cron harian) membuat SATU To-Do per lot ke ``responsible_id`` produk saat
``alert_date`` tercapai, lalu menandai ``product_expiry_reminded``. Untuk lot yang kategorinya punya ambang EFN
(``efn_near_expiry_days``/``efn_markdown_days`` > 0) dan flag modul menyala, notifikasi digantikan
``efn.expiry.alert`` (per outlet, ke kepala toko + ringkasan buyer). Caranya: sebelum ``super()``, lot terkelola yang
memenuhi syarat CE ditandai ``product_expiry_reminded = True`` TANPA aktivitas, sehingga loop CE melewatinya. Lot di
kategori tanpa ambang EFN tetap memakai perilaku CE apa adanya. Flag mati -> CE tidak berubah.
"""

from odoo import api, fields, models

from .common import is_enabled


class StockLot(models.Model):
    _inherit = "stock.lot"

    @api.model
    def _efn_expiry_managed_domain(self):
        # Kategori terkelola sendiri atau lewat induk (pewarisan ambang, product_category.py).
        return [("product_id.categ_id", "in", self.env["product.category"]._efn_managed_categ_ids())]

    @api.model
    def _alert_date_exceeded(self):
        if is_enabled(self.env):
            managed = (
                self.env["stock.lot"]
                .sudo()
                .search(
                    [
                        ("alert_date", "<=", fields.Date.today()),
                        ("product_expiry_reminded", "=", False),
                        *self._efn_expiry_managed_domain(),
                    ]
                )
            )
            if managed:
                managed.write({"product_expiry_reminded": True})
        return super()._alert_date_exceeded()
