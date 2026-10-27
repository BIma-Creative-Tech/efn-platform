"""Prediksi panen mitra tani -> rencana pasokan (dasar PO & perencanaan harga)."""

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EfnAgriHarvest(models.Model):
    _name = "efn.agri.harvest"
    _description = "EFN prediksi panen"
    _inherit = ["mail.thread"]
    _order = "expected_date, id"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    farmer_id = fields.Many2one("efn.agri.farmer", required=True, ondelete="cascade", check_company=True)
    company_id = fields.Many2one(related="farmer_id.company_id", store=True)
    commodity_id = fields.Many2one("efn.agri.commodity", required=True)
    product_id = fields.Many2one("product.product", required=True, domain="[('id', 'in', commodity_product_ids)]")
    commodity_product_ids = fields.Many2many(related="commodity_id.product_ids")
    planting_date = fields.Date(string="Tanggal tanam")
    expected_date = fields.Date(string="Perkiraan panen", required=True)
    expected_qty = fields.Float(string="Perkiraan qty", required=True)
    uom_id = fields.Many2one(related="commodity_id.uom_id")
    land_area_ha = fields.Float(string="Luas (ha)")
    state = fields.Selection(
        [("planned", "Direncanakan"), ("ordered", "PO dibuat"), ("harvested", "Dipanen"), ("cancel", "Batal")],
        default="planned",
        tracking=True,
    )
    purchase_id = fields.Many2one("purchase.order", readonly=True)

    @api.depends("farmer_id", "commodity_id", "expected_date")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.farmer_id.code or ''} {rec.commodity_id.name or ''} {rec.expected_date or ''}"

    def action_create_purchase(self):
        """PO ke mitra tani dengan harga dasar komoditas; harga final mengikuti grade saat inspeksi."""
        self.check_access("write")
        for rec in self:
            if rec.state != "planned":
                raise UserError(_("efn_agri:harvest_state — PO hanya dari prediksi berstatus 'Direncanakan'."))
            po = self.env["purchase.order"].create(
                {
                    "partner_id": rec.farmer_id.partner_id.id,
                    "company_id": rec.company_id.id,
                    "date_planned": rec.expected_date,
                    "origin": rec.name,
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": rec.product_id.id,
                                "product_qty": rec.expected_qty,
                                "product_uom_id": rec.commodity_id.uom_id.id,
                                "price_unit": rec.commodity_id.floor_price,
                                "date_planned": rec.expected_date,
                            },
                        )
                    ],
                }
            )
            rec.write({"purchase_id": po.id, "state": "ordered"})
        return {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "view_mode": "form",
            "res_id": self[-1].purchase_id.id,
        }
