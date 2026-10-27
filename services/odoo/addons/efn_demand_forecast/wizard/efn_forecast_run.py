from odoo import fields, models


class EfnForecastRun(models.TransientModel):
    _name = "efn.forecast.run"
    _description = "EFN jalankan forecast"

    warehouse_ids = fields.Many2many("stock.warehouse", string="Gudang", required=True)
    product_ids = fields.Many2many("product.product", string="Produk", help="Kosong = semua produk ber-reorder rule.")
    history_days = fields.Integer(default=56, required=True)
    lead_days = fields.Integer(default=2, required=True)
    review_days = fields.Integer(default=7, required=True)
    service_level = fields.Selection(
        [("90", "90 %"), ("95", "95 %"), ("98", "98 %"), ("99", "99 %")], default="95", required=True
    )

    def action_run(self):
        self.ensure_one()
        self.env["efn.demand.forecast"].check_access("create")
        records = self.env["efn.demand.forecast"]._efn_generate(
            self.warehouse_ids,
            self.product_ids or None,
            history_days=self.history_days,
            lead_days=self.lead_days,
            review_days=self.review_days,
            service_level=self.service_level,
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Usulan forecast",
            "res_model": "efn.demand.forecast",
            "view_mode": "list,form",
            "domain": [("id", "in", records.ids)],
        }
