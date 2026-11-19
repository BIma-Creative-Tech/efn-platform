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
from odoo.exceptions import AccessError, UserError

from . import forecast_api as fa

Z_BY_SERVICE = {"90": 1.28, "95": 1.65, "98": 2.05, "99": 2.33}
# SEC-212: hanya ditulis lewat aksi/RPC internal (sudo setelah cek), tidak lewat write/RPC user stok.
PROTECTED_FIELDS = frozenset(
    {"state", "source", "forecast_run_id", "approved_by_id", "approved_at", "v2_service_level"}
)


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
        [
            ("draft", "Usulan"),
            ("applied", "Diterapkan"),
            ("rejected", "Ditolak"),
            ("superseded", "Digantikan"),
            # G44 (L-148): usulan v2 untuk gudang yang masih memakai engine v1 -> hanya pembanding, tidak bisa
            # diterapkan.
            ("shadow", "Pembanding v2"),
        ],
        default="draft",
        tracking=True,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    # G44 (docs/04 §28.2 efn_minmax_ingest, ADR-046 §3.4 keluaran 1): sumber usulan v1 (cron Odoo) / v2 (worker).
    source = fields.Selection([("v1", "v1 (Odoo)"), ("v2", "v2 (forecast-worker)")], default="v1", readonly=True)
    forecast_run_id = fields.Many2one("efn.forecast.run", string="Run v2", readonly=True, index="btree_not_null")
    v2_service_level = fields.Float(string="Service level v2", readonly=True, digits=(4, 3))

    @api.depends("warehouse_id", "product_id", "date")
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.warehouse_id.code or '?'} / {rec.product_id.display_name or '?'} / {rec.date or ''}"

    # ------------------------------------------------------------------ proteksi (SEC-212)
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                touched = set(vals) & PROTECTED_FIELDS
                neutral = {"state": "draft", "source": "v1"}
                if any(vals[name] not in (neutral.get(name), False, None) for name in touched):
                    raise AccessError(_("efn_demand_forecast:readonly — status/sumber usulan diatur sistem."))
        return super().create(vals_list)

    def write(self, vals):
        """Status, sumber, run v2, dan penyetuju hanya berubah lewat aksi (sudo setelah cek); shadow tidak bisa
        dihidupkan menjadi draft lewat write/RPC (L-148)."""
        if not self.env.su and set(vals) & PROTECTED_FIELDS:
            raise AccessError(_("efn_demand_forecast:readonly — status/sumber usulan hanya berubah lewat tombol."))
        return super().write(vals)

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
            # G44: with_company -> musim payday/holiday global memakai parameter & kalender PT gudang.
            seasons = Season.with_company(rec.company_id).search(
                ["|", ("company_id", "=", False), ("company_id", "=", rec.company_id.id)]
            )
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
            rec.sudo().write(
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
        self.filtered(lambda r: r.state == "draft").sudo().write({"state": "rejected"})

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
                # sudo hanya untuk transisi status (SEC-212); pencarian tetap dengan hak user (record rule).
                self.search(
                    [("warehouse_id", "=", warehouse.id), ("product_id", "=", product.id), ("state", "=", "draft")]
                ).sudo().write({"state": "superseded"})
                created |= self.create(
                    dict(params, company_id=warehouse.company_id.id, warehouse_id=warehouse.id, product_id=product.id)
                )
        created._efn_compute()
        return created

    @api.model
    def _cron_efn_weekly(self):
        """Cron mingguan: usulan baru untuk semua orderpoint gudang yang ditandai ikut forecast.

        G44 (L-148): gudang dengan ``efn_forecast_engine = v2`` dilewati (min/max datang dari forecast-worker)."""
        for warehouse in (
            self.env["stock.warehouse"]
            .sudo()
            .search([("efn_forecast_enabled", "=", True), ("efn_forecast_engine", "!=", "v2")])
        ):
            self.sudo().with_company(warehouse.company_id)._efn_generate(warehouse)


class EfnDemandForecastLine(models.Model):
    """Baris min/max v2 mentah dari forecast-worker (G44, docs/04 §28.2 ``efn.demand.forecast.line``).

    Satu baris per (run, gudang, produk) = jejak apa yang dikirim worker; setiap baris yang diterima membuat satu
    usulan ``efn.demand.forecast`` (``source = v2``) yang disetujui planner dengan alur v1 yang sama. Hanya ditulis RPC
    user teknis (sudo setelah cek pemanggil)."""

    _name = "efn.demand.forecast.line"
    _description = "EFN baris min/max forecast v2 (worker)"
    _order = "id desc"
    _check_company_auto = True

    run_id = fields.Many2one("efn.forecast.run", required=True, ondelete="cascade", index=True, readonly=True)
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True, ondelete="cascade")
    min_qty = fields.Float(readonly=True)
    max_qty = fields.Float(readonly=True)
    service_level = fields.Float(readonly=True, digits=(4, 3))
    basis_daily_demand = fields.Float(readonly=True, digits=(16, 3))
    forecast_id = fields.Many2one("efn.demand.forecast", string="Usulan", readonly=True, ondelete="set null")

    _efn_minmax_key_uniq = models.Constraint(
        "UNIQUE(run_id, warehouse_id, product_id)", "Baris min/max (run, gudang, produk) sudah ada."
    )

    @api.model
    def efn_minmax_ingest(self, **payload):
        """docs/04 §28.2 ``efn_minmax_ingest`` (keluaran 1). Idempoten ``(run_id, chunk_no)``; baris tidak valid
        ditolak per baris.

        Gudang engine ``v2`` -> usulan ``draft`` (menggantikan draft lama pasangan itu, disetujui planner seperti v1);
        gudang engine ``v1`` -> ``shadow`` (pembanding, tidak bisa diterapkan; L-148)."""
        env, company = fa.check_caller(self.env)
        Chunk = env["efn.forecast.chunk"]
        run, existing, lines = Chunk._efn_begin_chunk(env, company, payload, "weekly_minmax")
        if existing:
            return Chunk._efn_duplicate_response(run, existing)
        warehouses, products = Chunk._efn_scope(env, company, lines)
        Forecast = env["efn.demand.forecast"]
        today = fa.local_today(env)
        stored = {
            (rec.warehouse_id.id, rec.product_id.id)
            for rec in env["efn.demand.forecast.line"].search([("run_id", "=", run.id)])
        }
        cap = fa.max_qty(env)
        rejected, seen, to_create = [], set(), []
        for index, line in enumerate(lines):
            code = self._efn_validate_minmax_line(line, warehouses, products, cap=cap)
            if code is None:
                key = (warehouses[line["warehouse_code"]].id, line["product_id"])
                if key in seen or key in stored:
                    code = "duplicate_line"
            if code:
                rejected.append({"index": index, "code": code})
                continue
            seen.add(key)
            to_create.append(line)
        product_ids = list({line["product_id"] for line in to_create})
        product_map = {p.id: p for p in env["product.product"].with_context(active_test=False).browse(product_ids)}
        created = Forecast.browse()
        for line in to_create:
            wh = warehouses[line["warehouse_code"]]
            product = product_map[line["product_id"]]
            is_v2 = wh.efn_forecast_engine == "v2"
            old_states = ("draft", "shadow") if is_v2 else ("shadow",)
            Forecast.search(
                [("warehouse_id", "=", wh.id), ("product_id", "=", product.id), ("state", "in", old_states)]
            ).write({"state": "superseded"})
            level = float(line["service_level"])
            key = min(Z_BY_SERVICE, key=lambda k: abs(int(k) / 100.0 - level))
            vals = {
                "company_id": company.id,
                "warehouse_id": wh.id,
                "product_id": product.id,
                "date": today,
                "source": "v2",
                "forecast_run_id": run.id,
                "state": "draft" if is_v2 else "shadow",
                "service_level": key,
                "v2_service_level": level,
                "base_daily": float(line["basis_daily_demand"]),
                "forecast_daily": float(line["basis_daily_demand"]),
                "season_factor": 1.0,
                "proposed_min": float(line["min_qty"]),
                "proposed_max": float(line["max_qty"]),
                "approved_min": float(line["min_qty"]),
                "approved_max": float(line["max_qty"]),
            }
            rec = Forecast.create(vals)
            op = rec._efn_orderpoint()
            if op:
                rec.write(
                    {"orderpoint_id": op.id, "current_min": op.product_min_qty, "current_max": op.product_max_qty}
                )
            env["efn.demand.forecast.line"].create(
                {
                    "run_id": run.id,
                    "company_id": company.id,
                    "warehouse_id": wh.id,
                    "product_id": product.id,
                    "min_qty": float(line["min_qty"]),
                    "max_qty": float(line["max_qty"]),
                    "service_level": level,
                    "basis_daily_demand": float(line["basis_daily_demand"]),
                    "forecast_id": rec.id,
                }
            )
            created |= rec
        return Chunk._efn_finish_chunk(env, run, payload["chunk_no"], len(created), rejected)

    @api.model
    def _efn_validate_minmax_line(self, line, warehouses, products, cap=None):
        keys = {"warehouse_code", "product_id", "min_qty", "max_qty", "service_level", "basis_daily_demand"}
        if not isinstance(line, dict) or set(line) != keys:
            return "invalid_line"
        if not isinstance(line["warehouse_code"], str) or line["warehouse_code"] not in warehouses:
            return "unknown_warehouse"
        if not fa.is_int(line["product_id"]) or line["product_id"] not in products:
            return "unknown_product"
        if not all(fa.is_number(line[k]) for k in ("min_qty", "max_qty", "service_level", "basis_daily_demand")):
            return "invalid_number"
        if cap is not None and any(float(line[k]) > cap for k in ("min_qty", "max_qty", "basis_daily_demand")):
            return "value_too_large"
        if float(line["max_qty"]) < float(line["min_qty"]):
            return "invalid_minmax"
        if not 0.5 <= float(line["service_level"]) < 1.0:
            return "invalid_service_level"
        return None
