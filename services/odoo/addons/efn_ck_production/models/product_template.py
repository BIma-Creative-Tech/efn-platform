"""Mode produksi Central Kitchen per produk (K25-12).

* ``mto``  : pesanan outlet langsung memicu MO di CK (rute *Replenish on Order (MTO)* + Manufacture).
* ``mts``  : produksi ke stok dari reorder rule CK (min/max dari forecast, ``efn_demand_forecast``).
* ``both`` : ambil stok CK bila ada; kekurangannya memicu MO (aturan MTO gudang CK bertipe *mts_else_mto*).

Rute diterapkan otomatis pada ``route_ids`` produk; route MTO global (nonaktif di CE) diaktifkan saat dibutuhkan.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    efn_ck_production_mode = fields.Selection(
        [
            ("mts", "Produksi ke stok (forecast / reorder)"),
            ("mto", "Produksi per pesanan outlet"),
            ("both", "Stok dulu, kekurangan diproduksi"),
        ],
        string="Mode produksi CK",
        help="Kosong = bukan produk produksi CK. Mengatur rute MTO/Manufacture produk secara otomatis.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates.filtered("efn_ck_production_mode")._efn_ck_apply_routes()
        return templates

    def write(self, vals):
        res = super().write(vals)
        if "efn_ck_production_mode" in vals:
            self._efn_ck_apply_routes()
        return res

    @api.model
    def _efn_ck_routes(self):
        mto = self.env.ref("stock.route_warehouse0_mto", raise_if_not_found=False)
        manufacture = self.env.ref("mrp.route_warehouse0_manufacture", raise_if_not_found=False)
        if not mto or not manufacture:
            raise UserError(_("efn_ck_production:routes_missing — rute MTO/Manufacture CE tidak ditemukan."))
        mto = mto.sudo().with_context(active_test=False)
        if not mto.active:
            mto.active = True
        mto.rule_ids.filtered(lambda r: not r.active).write({"active": True})
        return mto, manufacture.sudo()

    def _efn_ck_apply_routes(self):
        if not self:
            return
        mto, manufacture = self._efn_ck_routes()
        for template in self.sudo():
            mode = template.efn_ck_production_mode
            routes = template.route_ids - mto - manufacture
            if mode in ("mto", "both"):
                routes |= mto | manufacture
            elif mode == "mts":
                routes |= manufacture
            template.route_ids = routes
            # mts_else_mto per aturan gudang berlaku global di CE; mode 'both' memakai procure_method aturan MTO.
            if mode == "both":
                mto.rule_ids.filtered(lambda r: r.procure_method == "make_to_order").write(
                    {"procure_method": "mts_else_mto"}
                )
