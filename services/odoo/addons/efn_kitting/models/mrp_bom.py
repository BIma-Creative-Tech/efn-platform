"""Guard ADR-018 / R-T01 pada BoM.

Gelombang 3b: field `efn_kitting_role` (stored compute) DIHAPUS — sepenuhnya turunan dari
`type` (normal/phantom) + `product_tmpl_id.efn_is_kitting_package`, sehingga tidak perlu
disimpan (fit-gap docs/08, E-4). Kolom lama di-drop otomatis oleh Odoo saat upgrade
(ir.model.fields yang tidak lagi didefinisikan dihapus beserta kolomnya); view yang
memakainya ikut dihapus dari modul.
"""

from odoo import _, api, models
from odoo.exceptions import ValidationError


class MrpBom(models.Model):
    _inherit = "mrp.bom"

    @api.constrains("type", "product_tmpl_id", "product_id")
    def _check_efn_kitting_not_phantom(self):
        for bom in self:
            if bom.type == "phantom" and bom.product_tmpl_id.efn_is_kitting_package:
                raise ValidationError(
                    _(
                        "Produk '%(name)s' adalah paket kitting Central Kitchen; BoM-nya harus "
                        "tipe Manufacture, bukan Kit/Phantom (ADR-018, R-T01).",
                        name=bom.product_tmpl_id.display_name,
                    )
                )
