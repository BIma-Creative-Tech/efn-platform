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
    # G44 (docs/04 §28.3, L-148, ADR-046 §3.6): sumber usulan min/max per gudang. v1 = cron Odoo (default);
    # v2 = forecast-worker (efn_minmax_ingest). Pindah ke v2 hanya bila backtest 8 minggu WAPE v2 < v1 dan < seasonal
    # naive. Usulan v2 untuk gudang v1 disimpan sebagai pembanding (state shadow).
    efn_forecast_engine = fields.Selection(
        [("v1", "v1 (rumus Odoo)"), ("v2", "v2 (forecast-worker)")],
        string="Engine forecast min/max",
        default="v1",
        required=True,
    )
