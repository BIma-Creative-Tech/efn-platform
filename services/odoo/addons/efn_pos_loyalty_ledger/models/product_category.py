"""K-2 (docs/04 §9.6): kategori produk tanpa poin dikonfigurasi di Odoo, bukan env BFF.

Field `product.category.efn_loyalty_earn_excluded` juga dibaca ETL (lihat README) untuk
memisahkan penjualan yang tidak menghasilkan poin.
"""

from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    efn_loyalty_earn_excluded = fields.Boolean(
        string="Tanpa Poin EFN",
        help="Produk di kategori ini (dan semua subkategorinya) tidak dihitung dalam dasar earn "
        "Poin EFN (eligible_amount, kontrak docs/04 §9.6 K-1/K-2). Mis. rokok, pulsa, voucher.",
    )

    def _efn_loyalty_excluded_ids(self):
        """Id semua kategori yang dikecualikan, termasuk turunan kategori yang ditandai."""
        flagged = self.sudo().with_context(active_test=False).search([("efn_loyalty_earn_excluded", "=", True)])
        if not flagged:
            return set()
        return set(self.sudo().with_context(active_test=False).search([("id", "child_of", flagged.ids)]).ids)
