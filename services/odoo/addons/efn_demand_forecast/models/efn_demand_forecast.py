"""Forecast v1 per (gudang, produk): usulan min/max reorder rule.

Rumus (docs ARSITEKTUR §2.6, tanpa ML):
* Permintaan dasar harian ``d`` = keluar stok (gudang -> pelanggan/produksi/gudang lain) selama ``history_days`` hari
  dibagi jumlah hari, setelah setiap hari dinormalkan dengan faktor musim hari itu (de-seasonalized).
* ``sigma`` = simpangan baku permintaan harian ternormalisasi.
* Horizon = ``lead_days`` (lead time pasok/produksi) ; faktor musim horizon = rata-rata faktor hari-hari ke depan.
* ``min`` = d x faktor x lead_days + z x sigma x sqrt(lead_days)   (safety stock, z dari tingkat layanan)
* ``max`` = min + d x faktor x review_days
Usulan berstatus ``draft``; planner menekan *Setujui & Terapkan* untuk menimpa ``product_min_qty/max_qty`` orderpoint
(dibuat bila belum ada). Riwayat usulan tersimpan untuk audit dan pembandingan akurasi.
"""

import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

Z_BY_SERVICE = {"90": 1.28, "95": 1.65, "98": 2.05, "99": 2.33}


class EfnDemandForecast(models.Model):
    _name = "efn.demand.forecast"
    _description = "EFN usulan forecast min/max"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    date = fields.Date(string="Tanggal hitung", required=True, default=fields.Date.context_today)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True)
    product_id = fields.Many2one("product.product", required=True, check_company=True)
    product_uom_id = fields.Many2one(related="product_id.uom_id")
    history_days = fields.Integer(default=56)
    lead_days = fields.Integer(default=2)
    review_days = fields.Integer(default=7)
    service_level = fields.Selection(
        [("90", "90 %"), ("95", "95 %"), ("98", "98 %"), ("99", "99 %")], default="95", required=True
    )
    history_qty = fields.Float(string="Keluar stok (histori)", readonly=True)
    base_daily = fields.Float(string="Permintaan dasar/hari", readonly=True, digits=(16, 3))
    sigma_daily = fields.Float(string="Simpangan/hari", readonly=True, digits=(16, 3))
    season_factor = fields.Float(string="Faktor musim horizon", readonly=True, digits=(6, 4))
    forecast_daily = fields.Float(string="Forecast/hari", readonly=True, digits=(16, 3))
    proposed_min = fields.Float(string="Usulan min", readonly=True)
    proposed_max = fields.Float(string="Usulan max", readonly=True)
    current_min = fields.Float(string="Min saat ini", readonly=True)
    current_max = fields.Float(string="Max saat ini", readonly=True)
    approved_min = fields.Float(string="Min disetujui", help="Planner boleh menyesuaikan sebelum menyetujui.")
    approved_max = fields.Float(string="Max disetujui")
    orderpoint_id = fields.Many2one("stock.warehouse.orderpoint", readonly=True, check_company=True)
    state = fields.Selection(
        [("draft", "Usulan"), ("applied", "Diterapkan"), ("rejected", "Ditolak"), ("superseded", "Digantikan")],
        default="draft",
        tracking=True,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)

    @api.depends("warehouse_id", "product_id", "date")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.warehouse_id.code or '?'} / {rec.product_id.display_name or '?'} / {rec.date or ''}"

    # ------------------------------------------------------------------ perhitungan
    @api.model
    def _efn_daily_outflow(self, warehouse, product, date_from, date_to):
        """{tanggal: qty keluar} dari stock.move done yang keluar dari lokasi gudang (UoM produk)."""
        self.env["stock.move"].flush_model()
        self.env.cr.execute(
            """
            SELECT (m.date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jakarta')::date AS d, SUM(m.product_qty)
              FROM stock_move m
              JOIN stock_location src ON src.id = m.location_id
              JOIN stock_location dst ON dst.id = m.location_dest_id
             WHERE m.state = 'done'
               AND m.product_id = %(product)s
               AND src.warehouse_id = %(wh)s
               AND (dst.warehouse_id IS NULL OR dst.warehouse_id != %(wh)s)
               AND dst.usage IN ('customer', 'production', 'internal', 'transit')
               AND (m.date AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Jakarta')::date BETWEEN %(d0)s AND %(d1)s
          GROUP BY 1
            """,
            {"product": product.id, "wh": warehouse.id, "d0": date_from, "d1": date_to},
        )
        return dict(self.env.cr.fetchall())

    def _efn_compute(self):
        Season = self.env["efn.forecast.season"]
        for rec in self:
            seasons = Season.search(["|", ("company_id", "=", False), ("company_id", "=", rec.company_id.id)])
            end = rec.date - timedelta(days=1)
            start = end - timedelta(days=max(rec.history_days, 7) - 1)
            outflow = rec._efn_daily_outflow(rec.warehouse_id, rec.product_id, start, end)
            normalized, total = [], 0.0
            day = start
            while day <= end:
                qty = outflow.get(day, 0.0)
                total += qty
                normalized.append(qty / Season._efn_factor(seasons, day, rec.product_id))
                day += timedelta(days=1)
            n = len(normalized)
            base = sum(normalized) / n if n else 0.0
            sigma = math.sqrt(sum((x - base) ** 2 for x in normalized) / (n - 1)) if n > 1 else 0.0
            horizon = max(rec.lead_days, 1) + max(rec.review_days, 0)
            factors = [
                Season._efn_factor(seasons, rec.date + timedelta(days=i), rec.product_id) for i in range(horizon)
            ]
            factor = sum(factors) / len(factors)
            daily = base * factor
            z = Z_BY_SERVICE[rec.service_level]
            lead = max(rec.lead_days, 1)
            # Satuan unit/porsi dibulatkan ke bilangan bulat (15,65 paket tidak masuk akal); satuan berat ikut UoM.
            uom = rec.product_id.uom_id
            unit_uom = self.env.ref("uom.product_uom_unit", raise_if_not_found=False)
            rounding = 1.0 if uom == unit_uom else (uom.rounding or 1.0)
            proposed_min = (
                math.ceil(round((daily * lead + z * sigma * factor * math.sqrt(lead)) / rounding, 6)) * rounding
            )
            proposed_max = math.ceil(round((proposed_min + daily * rec.review_days) / rounding, 6)) * rounding
            op = rec._efn_orderpoint()
            rec.write(
                {
                    "history_qty": total,
                    "base_daily": base,
                    "sigma_daily": sigma,
                    "season_factor": factor,
                    "forecast_daily": daily,
                    "proposed_min": proposed_min,
                    "proposed_max": max(proposed_max, proposed_min),
                    "approved_min": proposed_min,
                    "approved_max": max(proposed_max, proposed_min),
                    "orderpoint_id": op.id,
                    "current_min": op.product_min_qty,
                    "current_max": op.product_max_qty,
                }
            )

    def _efn_orderpoint(self):
        self.ensure_one()
        return self.env["stock.warehouse.orderpoint"].search(
            [
                ("warehouse_id", "=", self.warehouse_id.id),
                ("product_id", "=", self.product_id.id),
                ("location_id", "=", self.warehouse_id.lot_stock_id.id),
                ("trigger", "in", ("auto", "manual")),
            ],
            limit=1,
        )

    # ------------------------------------------------------------------ aksi planner
    def _efn_check_planner(self):
        if not self.env.user.has_group("stock.group_stock_manager"):
            raise UserError(
                _("efn_demand_forecast:planner_only — hanya Inventory Administrator yang boleh menyetujui.")
            )

    def action_efn_apply(self):
        self._efn_check_planner()
        self.check_access("write")
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("efn_demand_forecast:state — hanya usulan berstatus 'Usulan' yang bisa diterapkan."))
            if rec.approved_max < rec.approved_min or rec.approved_min < 0:
                raise UserError(_("efn_demand_forecast:minmax — max harus >= min >= 0."))
            op = rec._efn_orderpoint()
            vals = {"product_min_qty": rec.approved_min, "product_max_qty": rec.approved_max}
            if op:
                op.write(vals)
            else:
                op = self.env["stock.warehouse.orderpoint"].create(
                    dict(
                        vals,
                        product_id=rec.product_id.id,
                        warehouse_id=rec.warehouse_id.id,
                        location_id=rec.warehouse_id.lot_stock_id.id,
                        company_id=rec.company_id.id,
                        trigger="manual",
                    )
                )
            op.efn_forecast_id = rec.id
            rec.write(
                {
                    "state": "applied",
                    "orderpoint_id": op.id,
                    "approved_by_id": self.env.uid,
                    "approved_at": fields.Datetime.now(),
                }
            )
            rec.message_post(
                body=_(
                    "Diterapkan ke reorder rule: min %(min)s, max %(max)s (sebelumnya %(cmin)s / %(cmax)s).",
                    min=rec.approved_min,
                    max=rec.approved_max,
                    cmin=rec.current_min,
                    cmax=rec.current_max,
                )
            )

    def action_efn_reject(self):
        self._efn_check_planner()
        self.check_access("write")
        self.filtered(lambda r: r.state == "draft").write({"state": "rejected"})

    def action_efn_recompute(self):
        self.check_access("write")
        drafts = self.filtered(lambda r: r.state == "draft")
        drafts._efn_compute()

    # ------------------------------------------------------------------ pembuatan massal
    @api.model
    def _efn_generate(self, warehouses, products=None, **params):
        """Buat usulan baru (usulan draft lama untuk pasangan yang sama -> 'superseded')."""
        created = self.browse()
        for warehouse in warehouses:
            prods = products
            if prods is None:
                ops = self.env["stock.warehouse.orderpoint"].search([("warehouse_id", "=", warehouse.id)])
                prods = ops.product_id
            for product in prods:
                self.search(
                    [("warehouse_id", "=", warehouse.id), ("product_id", "=", product.id), ("state", "=", "draft")]
                ).write({"state": "superseded"})
                created |= self.create(
                    dict(params, company_id=warehouse.company_id.id, warehouse_id=warehouse.id, product_id=product.id)
                )
        created._efn_compute()
        return created

    @api.model
    def _cron_efn_weekly(self):
        """Cron mingguan: usulan baru untuk semua orderpoint gudang yang ditandai ikut forecast."""
        for warehouse in self.env["stock.warehouse"].sudo().search([("efn_forecast_enabled", "=", True)]):
            self.sudo().with_company(warehouse.company_id)._efn_generate(warehouse)
