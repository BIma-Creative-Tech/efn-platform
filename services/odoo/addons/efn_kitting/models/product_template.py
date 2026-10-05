"""Penanda produk paket Central Kitchen (ADR-018, mitigasi R-T01).

Paket kitting = produk STORABLE hasil MO (BoM tipe `normal`). Paket tidak boleh memakai
BoM Phantom/Kit, karena kit tidak punya stok sendiri dan akan memotong bahan mentah
(ayam/tepung dalam kg) di outlet — kebalikan dari tujuan bisnis.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    efn_is_kitting_package = fields.Boolean(
        string="Paket Kitting Central Kitchen",
        copy=False,
        help="Produk paket siap olah yang diproduksi Central Kitchen via Manufacturing Order "
        "(BoM tipe Manufacture) dan distok di CK/outlet. Wajib storable dan tidak boleh "
        "memakai BoM Kit/Phantom (ADR-018).",
    )

    @api.constrains("efn_is_kitting_package", "is_storable", "type")
    def _check_efn_kitting_package(self):
        packages = self.filtered("efn_is_kitting_package")
        if not packages:
            return
        for template in packages:
            if not template.is_storable:
                raise ValidationError(
                    _(
                        "Paket kitting '%(name)s' harus produk yang dilacak stoknya (storable).",
                        name=template.display_name,
                    )
                )
        phantom_count = (
            self.env["mrp.bom"]
            .sudo()
            .with_context(active_test=False)
            .search_count([("product_tmpl_id", "in", packages.ids), ("type", "=", "phantom")])
        )
        if phantom_count:
            raise ValidationError(
                _(
                    "Paket kitting tidak boleh memiliki BoM Kit/Phantom. Gunakan BoM tipe "
                    "Manufacture di Central Kitchen (ADR-018)."
                )
            )
