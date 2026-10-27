"""Yield & varians konsumsi MO (K25-08).

* Yield = qty hasil aktual / qty rencana (``product_qty``) x 100.
* Varians per komponen = konsumsi aktual - konsumsi menurut BoM untuk qty yang benar-benar diproduksi.
* Di luar ambang PT (``efn_ck_yield_warn_pct``, ``efn_ck_variance_warn_pct``) -> ``efn_yield_alert`` + aktivitas ke
  pembuat MO saat MO selesai. Nilai disimpan (store) agar bisa dilaporkan/ETL tanpa menghitung ulang.
"""

from odoo import _, api, fields, models


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    efn_yield_pct = fields.Float(string="Yield (%)", compute="_compute_efn_yield", store=True, digits=(5, 2))
    efn_byproduct_qty = fields.Float(string="Qty by-product", compute="_compute_efn_yield", store=True)
    efn_variance_value = fields.Monetary(
        string="Varians konsumsi (nilai)",
        currency_field="efn_currency_id",
        compute="_compute_efn_yield",
        store=True,
        help="Selisih nilai komponen aktual vs BoM (positif = boros).",
    )
    efn_currency_id = fields.Many2one(related="company_id.currency_id")
    efn_yield_alert = fields.Boolean(string="Di luar ambang", compute="_compute_efn_yield", store=True)
    efn_variance_line_ids = fields.One2many(
        "efn.mo.variance", "production_id", string="Varians komponen", compute="_compute_efn_variance_lines"
    )

    # move_byproduct_ids CE = compute non-stored (tidak boleh di depends); by-product ada di move_finished_ids.
    @api.depends(
        "state",
        "product_qty",
        "move_raw_ids.quantity",
        "move_raw_ids.state",
        "move_finished_ids.quantity",
        "move_finished_ids.state",
    )
    def _compute_efn_yield(self):
        for mo in self:
            if mo.state != "done" or not mo.product_qty:
                mo.efn_yield_pct = 0.0
                mo.efn_byproduct_qty = 0.0
                mo.efn_variance_value = 0.0
                mo.efn_yield_alert = False
                continue
            mo.efn_yield_pct = 100.0 * mo.qty_produced / mo.product_qty
            byproducts = mo.move_finished_ids.filtered(
                lambda m, p=mo.product_id: m.state == "done" and m.product_id != p
            )
            mo.efn_byproduct_qty = sum(byproducts.mapped("quantity"))
            rows = mo._efn_variance_rows()
            mo.efn_variance_value = mo.company_id.currency_id.round(sum(r["variance_value"] for r in rows))
            company = mo.company_id
            mo.efn_yield_alert = mo.efn_yield_pct < company.efn_ck_yield_warn_pct or any(
                abs(r["variance_pct"]) > company.efn_ck_variance_warn_pct for r in rows
            )

    def _efn_variance_rows(self):
        """[{product, expected, actual, variance_qty, variance_pct, variance_value}] per komponen (UoM produk)."""
        self.ensure_one()
        rows = {}
        if self.bom_id and self.qty_produced:
            ratio = self.product_uom_id._compute_quantity(self.qty_produced, self.bom_id.product_uom_id) / (
                self.bom_id.product_qty or 1.0
            )
            for line in self.bom_id.bom_line_ids:
                product = line.product_id
                expected = line.product_uom_id._compute_quantity(line.product_qty * ratio, product.uom_id)
                rows.setdefault(product, {"expected": 0.0, "actual": 0.0})["expected"] += expected
        for move in self.move_raw_ids.filtered(lambda m: m.state == "done"):
            qty = move.product_uom._compute_quantity(move.quantity, move.product_id.uom_id)
            rows.setdefault(move.product_id, {"expected": 0.0, "actual": 0.0})["actual"] += qty
        result = []
        for product, data in rows.items():
            diff = data["actual"] - data["expected"]
            pct = 100.0 * diff / data["expected"] if data["expected"] else (100.0 if diff else 0.0)
            cost = product.with_company(self.company_id).standard_price
            result.append(
                {
                    "product": product,
                    "expected": data["expected"],
                    "actual": data["actual"],
                    "variance_qty": diff,
                    "variance_pct": pct,
                    "variance_value": diff * cost,
                }
            )
        return result

    def _compute_efn_variance_lines(self):
        Line = self.env["efn.mo.variance"]
        for mo in self:
            lines = Line
            if mo.state == "done":
                for row in mo._efn_variance_rows():
                    lines |= Line.new(
                        {
                            "production_id": mo.id,
                            "product_id": row["product"].id,
                            "expected_qty": row["expected"],
                            "actual_qty": row["actual"],
                            "variance_qty": row["variance_qty"],
                            "variance_pct": row["variance_pct"],
                            "variance_value": row["variance_value"],
                        }
                    )
            mo.efn_variance_line_ids = lines

    def _post_inventory(self, cancel_backorder=False):
        res = super()._post_inventory(cancel_backorder=cancel_backorder)
        for mo in self.filtered(lambda m: m.state == "done" and m.efn_yield_alert):
            mo.activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Yield/varians MO di luar ambang"),
                note=_("Yield %(y).1f %%, varians konsumsi %(v)s.", y=mo.efn_yield_pct, v=mo.efn_variance_value),
                user_id=mo.user_id.id or self.env.uid,
            )
        return res


class EfnMoVariance(models.TransientModel):
    """Baris tampilan varians komponen (dihitung on-the-fly dari MO; tidak disimpan)."""

    _name = "efn.mo.variance"
    _description = "EFN varians konsumsi komponen MO"

    production_id = fields.Many2one("mrp.production")
    product_id = fields.Many2one("product.product", string="Komponen")
    expected_qty = fields.Float(string="Menurut BoM")
    actual_qty = fields.Float(string="Aktual")
    variance_qty = fields.Float(string="Selisih")
    variance_pct = fields.Float(string="Selisih (%)", digits=(5, 1))
    variance_value = fields.Float(string="Nilai selisih")
