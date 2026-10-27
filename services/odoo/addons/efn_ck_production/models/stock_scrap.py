"""Waste (K25-08): alasan wajib + nilai waste per scrap.

Alasan memakai model CE ``stock.scrap.reason.tag`` (data: susut, rusak, gagal masak, kedaluwarsa, sisa
produksi). Nilai = nilai valuasi stock.move scrap (stock_account) bila ada, selain itu qty x biaya produk.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    efn_warehouse_id = fields.Many2one(
        "stock.warehouse", string="Gudang", compute="_compute_efn_warehouse_id", store=True, index=True
    )
    efn_waste_value = fields.Monetary(
        string="Nilai waste",
        currency_field="efn_currency_id",
        compute="_compute_efn_waste_value",
        store=True,
    )
    efn_currency_id = fields.Many2one(related="company_id.currency_id")

    @api.depends("location_id")
    def _compute_efn_warehouse_id(self):
        for scrap in self:
            scrap.efn_warehouse_id = scrap.location_id.warehouse_id

    @api.depends("state", "move_ids.value", "scrap_qty", "product_id")
    def _compute_efn_waste_value(self):
        for scrap in self:
            moved = sum(abs(v) for v in scrap.move_ids.mapped("value")) if scrap.state == "done" else 0.0
            if moved:
                scrap.efn_waste_value = moved
            else:
                cost = scrap.product_id.with_company(scrap.company_id).standard_price
                qty = (
                    scrap.product_uom_id._compute_quantity(scrap.scrap_qty, scrap.product_id.uom_id)
                    if scrap.product_uom_id
                    else scrap.scrap_qty
                )
                scrap.efn_waste_value = scrap.company_id.currency_id.round(cost * qty)

    def do_scrap(self):
        missing = self.filtered(lambda s: not s.scrap_reason_tag_ids)
        if missing and not self.env.context.get("efn_scrap_reason_optional"):
            raise UserError(
                _("efn_ck_production:reason_required — isi alasan waste (susut, rusak, gagal masak, kedaluwarsa, ...).")
            )
        return super().do_scrap()
