"""Override hook forecast G43 (ADR-042 §3.2, docs/04 §27.8, L-136).

Hari ``< efn_mig_esb.cutover_date.<KODE PT gudang>`` = Σ ``outflow`` ``efn.legacy.flow.daily`` (gudang x produk);
hari ``>= cutover`` tetap ``stock.move`` (logika di ``efn_demand_forecast._efn_daily_outflow``). Tanpa parameter /
format salah -> perilaku lama persis (fail safe).
"""

from datetime import timedelta

from odoo import api, models

from ..bulk import get_cutover_date


class EfnDemandForecast(models.Model):
    _inherit = "efn.demand.forecast"

    @api.model
    def _efn_legacy_outflow(self, warehouse, product, date_from, date_to):
        boundary, legacy = super()._efn_legacy_outflow(warehouse, product, date_from, date_to)
        cutover = get_cutover_date(self.env, warehouse.company_id)
        if cutover is None:
            return boundary, legacy
        end = min(date_to, cutover - timedelta(days=1))
        result = {}
        if date_from <= end:
            self.env["efn.legacy.flow.daily"].flush_model(
                ["company_id", "warehouse_id", "product_id", "day", "outflow"]
            )
            self.env.cr.execute(
                """
                SELECT day, SUM(outflow)
                  FROM efn_legacy_flow_daily
                 WHERE company_id = %(company)s
                   AND warehouse_id = %(wh)s
                   AND product_id = %(product)s
                   AND day BETWEEN %(d0)s AND %(d1)s
              GROUP BY day
                """,
                {
                    "company": warehouse.company_id.id,
                    "wh": warehouse.id,
                    "product": product.id,
                    "d0": date_from,
                    "d1": end,
                },
            )
            result = {day: qty or 0.0 for day, qty in self.env.cr.fetchall()}
        return cutover, result
