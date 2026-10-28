"""Gudang CK & outlet (K25-12): penanda CK dan sumber resupply outlet.

``efn_ck_supplier_wh_id`` adalah pembungkus ramah-pengguna untuk ``resupply_wh_ids`` CE: outlet memilih SATU CK
dalam PT yang sama; Odoo CE membuat rute *Supply Product from CK* (transit internal) dan aturan MTO-nya. Reorder rule
outlet atau permintaan manual (Replenishment) membuat transfer CK -> outlet.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    efn_is_central_kitchen = fields.Boolean(string="Central Kitchen")
    efn_ck_supplier_wh_id = fields.Many2one(
        "stock.warehouse",
        string="Dipasok dari CK",
        domain="[('efn_is_central_kitchen', '=', True), ('company_id', '=', company_id), ('id', '!=', id)]",
        help="Central Kitchen pemasok outlet ini. Mengisi rute resupply CE (resupply_wh_ids).",
    )
    efn_ck_supply_route_id = fields.Many2one(
        "stock.route", string="Rute pasok CK", compute="_compute_efn_ck_supply_route_id"
    )

    @api.constrains("efn_ck_supplier_wh_id", "company_id")
    def _check_efn_ck_supplier(self):
        for warehouse in self.filtered("efn_ck_supplier_wh_id"):
            supplier = warehouse.efn_ck_supplier_wh_id
            if supplier == warehouse:
                raise ValidationError(_("efn_ck_production:self_supply — gudang tidak bisa memasok dirinya sendiri."))
            if supplier.company_id != warehouse.company_id:
                raise ValidationError(
                    _(
                        "efn_ck_production:cross_company_supply — CK beda PT memakai alur antar-PT (efn_intercompany), "
                        "bukan resupply internal."
                    )
                )
            if not supplier.efn_is_central_kitchen:
                raise ValidationError(_("efn_ck_production:not_ck — gudang pemasok harus ditandai Central Kitchen."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("efn_ck_supplier_wh_id"):
                vals["resupply_wh_ids"] = [(6, 0, [vals["efn_ck_supplier_wh_id"]])]
        return super().create(vals_list)

    def write(self, vals):
        if "efn_ck_supplier_wh_id" in vals:
            vals = dict(vals)
            vals["resupply_wh_ids"] = [(6, 0, [vals["efn_ck_supplier_wh_id"]] if vals["efn_ck_supplier_wh_id"] else [])]
        return super().write(vals)

    def _compute_efn_ck_supply_route_id(self):
        Route = self.env["stock.route"].sudo()
        for warehouse in self:
            supplier = warehouse.efn_ck_supplier_wh_id
            warehouse.efn_ck_supply_route_id = (
                Route.search([("supplied_wh_id", "=", warehouse.id), ("supplier_wh_id", "=", supplier.id)], limit=1)
                if supplier
                else Route
            )
