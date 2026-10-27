from odoo import fields, models


class StockWarehouseOrderpoint(models.Model):
    _inherit = "stock.warehouse.orderpoint"

    efn_forecast_id = fields.Many2one("efn.demand.forecast", string="Forecast terakhir", readonly=True, copy=False)


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    efn_forecast_enabled = fields.Boolean(
        string="Ikut forecast mingguan",
        help="Cron mingguan membuat usulan min/max untuk semua reorder rule gudang ini.",
    )
