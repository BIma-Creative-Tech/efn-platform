from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    efn_ic_purchase_id = fields.Many2one("purchase.order", string="PO antar-PT", copy=False, readonly=True)
    efn_ic_purchase_name = fields.Char(string="PO antar-PT", compute="_compute_efn_ic_purchase_name")

    def _compute_efn_ic_purchase_name(self):
        for order in self:
            po = order.sudo().efn_ic_purchase_id
            order.efn_ic_purchase_name = f"{po.name} ({po.company_id.name})" if po else False


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    efn_ic_purchase_line_id = fields.Many2one(
        "purchase.order.line", string="Baris PO antar-PT", copy=False, readonly=True, index="btree_not_null"
    )
