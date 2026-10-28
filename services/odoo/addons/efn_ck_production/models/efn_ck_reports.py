"""Laporan Odoo (SQL view, read-only) untuk dashboard waste & yield di Odoo.

Sumber kebenaran tetap tabel CE (stock_scrap, mrp_production). Mart ClickHouse (``fact_scrap``, ``fact_mo_yield``)
mengikuti kolom yang sama lewat ETL; view ini membuat demo dan pemantauan harian bisa langsung dari Odoo.
"""

from odoo import fields, models, tools
from odoo.tools import SQL


class EfnCkWasteReport(models.Model):
    _name = "efn.ck.waste.report"
    _description = "EFN laporan waste (scrap) per gudang, produk, alasan, hari"
    _auto = False
    _order = "date desc"
    _depends = {
        "stock.scrap": [
            "date_done",
            "company_id",
            "efn_warehouse_id",
            "product_id",
            "scrap_qty",
            "efn_waste_value",
            "state",
        ],
    }

    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Gudang", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    categ_id = fields.Many2one("product.category", string="Kategori", readonly=True)
    reason_id = fields.Many2one("stock.scrap.reason.tag", string="Alasan", readonly=True)
    qty = fields.Float(string="Qty", readonly=True)
    value = fields.Float(string="Nilai waste", readonly=True)
    scrap_count = fields.Integer(string="Jumlah scrap", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            SQL(
                """
            CREATE OR REPLACE VIEW %s AS (
                -- Satu baris per scrap; alasan = tag pertama (scrap dengan beberapa tag tidak dihitung ganda).
                SELECT s.id,
                       (s.date_done AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jakarta')::date AS date,
                       s.company_id, s.efn_warehouse_id AS warehouse_id, s.product_id, pt.categ_id,
                       (SELECT MIN(rel.stock_scrap_reason_tag_id)
                          FROM stock_scrap_stock_scrap_reason_tag_rel rel
                         WHERE rel.stock_scrap_id = s.id) AS reason_id,
                       s.scrap_qty AS qty,
                       s.efn_waste_value AS value,
                       1 AS scrap_count
                  FROM stock_scrap s
                  JOIN product_product pp ON pp.id = s.product_id
                  JOIN product_template pt ON pt.id = pp.product_tmpl_id
                 WHERE s.state = 'done'
            )
            """,
                SQL.identifier(self._table),
            )
        )


class EfnCkYieldReport(models.Model):
    _name = "efn.ck.yield.report"
    _description = "EFN laporan yield MO per gudang, produk, hari"
    _auto = False
    _order = "date desc"
    _depends = {
        "mrp.production": [
            "date_finished",
            "company_id",
            "picking_type_id",
            "product_id",
            "product_qty",
            "state",
            "efn_yield_pct",
            "efn_byproduct_qty",
            "efn_variance_value",
            "efn_yield_alert",
        ],
    }

    date = fields.Date(readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    production_id = fields.Many2one("mrp.production", string="MO", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Gudang", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    planned_qty = fields.Float(string="Rencana", readonly=True)
    produced_qty = fields.Float(string="Aktual", readonly=True)
    yield_pct = fields.Float(string="Yield (%)", readonly=True, aggregator="avg")
    byproduct_qty = fields.Float(string="By-product", readonly=True)
    variance_value = fields.Float(string="Varians konsumsi", readonly=True)
    alert = fields.Boolean(string="Di luar ambang", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            SQL(
                """
            CREATE OR REPLACE VIEW %s AS (
                SELECT mo.id, mo.id AS production_id,
                       (mo.date_finished AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jakarta')::date AS date,
                       mo.company_id, pt.warehouse_id, mo.product_id,
                       mo.product_qty AS planned_qty,
                       mo.product_qty * mo.efn_yield_pct / 100.0 AS produced_qty,
                       mo.efn_yield_pct AS yield_pct,
                       mo.efn_byproduct_qty AS byproduct_qty,
                       mo.efn_variance_value AS variance_value,
                       mo.efn_yield_alert AS alert
                  FROM mrp_production mo
             LEFT JOIN stock_picking_type pt ON pt.id = mo.picking_type_id
                 WHERE mo.state = 'done'
            )
            """,
                SQL.identifier(self._table),
            )
        )
