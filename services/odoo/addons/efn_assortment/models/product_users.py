from odoo import api, fields, models

from .stock import check_assortment_writer

PRODUCT_GUARDED_FIELDS = frozenset({"efn_required_capability_ids"})


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # SEC-208: syarat capability produk hanya ditulis Assortment Manager. Audit nilai lama/baru = aturan
    # efn_setup_audit.audit_rule_product_template (semua field; efn.audit.rule unik per model).
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            check_assortment_writer(self.env, vals, PRODUCT_GUARDED_FIELDS, creating=True)
        return super().create(vals_list)

    def write(self, vals):
        check_assortment_writer(self.env, vals, PRODUCT_GUARDED_FIELDS)
        return super().write(vals)

    efn_required_capability_ids = fields.Many2many(
        "efn.assortment.capability",
        "efn_assortment_product_capability_rel",
        "product_tmpl_id",
        "capability_id",
        string="Kemampuan outlet yang dibutuhkan",
        help="Mis. freezer, chiller, izin alkohol, boleh non-halal (ADR-045). Kosong = tanpa syarat.",
    )


class ProductSupplierinfo(models.Model):
    _inherit = "product.supplierinfo"

    efn_region_ids = fields.Many2many(
        "efn.region",
        "efn_assortment_supplierinfo_region_rel",
        "supplierinfo_id",
        "region_id",
        string="Region kontrak",
        help="Kosong = tanpa batas wilayah. Diisi = supplier hanya boleh memasok outlet di kab/kota ini "
        "(region outlet = alamat gudang, efn_region_id).",
    )


class ResUsers(models.Model):
    _inherit = "res.users"

    # Cakupan outlet kepala toko: record rule efn_replenishment & sumber `wids` Insight (docs/04 §28.11, ADR-041 §3.3).
    efn_outlet_warehouse_ids = fields.Many2many(
        "stock.warehouse",
        "efn_res_users_outlet_warehouse_rel",
        "user_id",
        "warehouse_id",
        string="Outlet dikelola (kepala toko)",
    )
