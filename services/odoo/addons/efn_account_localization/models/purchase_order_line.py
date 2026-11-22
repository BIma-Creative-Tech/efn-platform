"""G60-C opsional (Q-G60-6, default "ya"): baris PO ke gudang outlet membawa analitik Outlet -> mengalir ke tagihan.

Hapus file ini (dan dependency ``purchase_stock``) bila Finance menjawab Q-G60-6 "tidak perlu".
"""

from odoo import api, models

from .outlet_analytic import merge_outlet_distribution


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    # Mendeklarasikan ulang @api.depends MENGGANTI set warisan -> pemicu CE (product_id, order_id.partner_id) diulang.
    @api.depends("product_id", "order_id.partner_id", "order_id.picking_type_id")
    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        Account = self.env["account.analytic.account"].sudo().with_context(active_test=False)
        for line in self:
            if line.display_type:
                continue
            outlet = line.order_id.picking_type_id.warehouse_id.sudo().efn_analytic_account_id
            if not outlet:
                continue
            plan_ids = set(Account.search([("root_plan_id", "=", outlet.root_plan_id.id)]).ids)
            line.analytic_distribution = merge_outlet_distribution(line.analytic_distribution, outlet.id, plan_ids)
