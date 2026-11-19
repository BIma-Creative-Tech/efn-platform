"""Proyeksi penjualan harian v2 (keluaran 4, ADR-046 §3.4; docs/04 §28.2 ``efn_forecast_ingest``).

Grain SKU x gudang x hari per run. Ditulis hanya oleh RPC user teknis forecast (sudo setelah cek pemanggil), dibaca
``efn_replenishment`` (usulan order) dan ETL (``fact_forecast_daily``). Baris run non-aktif dihapus autovacuum setelah
``efn_forecast.daily_retention_days`` (default 35) hari agar tabel tidak tumbuh tanpa batas (≈ SKU x gudang x 28 baris
per run).
"""

from datetime import timedelta

from odoo import api, fields, models

from . import forecast_api as fa

FORECAST_MODELS = [
    ("AutoETS", "AutoETS"),
    ("AutoARIMA", "AutoARIMA"),
    ("CrostonOptimized", "CrostonOptimized"),
    ("ADIDA", "ADIDA"),
    ("category_mean", "Rata-rata kategori"),
]
FORECAST_MODEL_CODES = frozenset(code for code, _label in FORECAST_MODELS)
LINE_KEYS = frozenset({"warehouse_code", "product_id", "date", "yhat", "p10", "p90", "sigma", "model"})
RETENTION_PARAM = "efn_forecast.daily_retention_days"
DEFAULT_RETENTION_DAYS = 35
GC_BATCH = 50000


class EfnForecastDaily(models.Model):
    _name = "efn.forecast.daily"
    _description = "EFN proyeksi penjualan harian (forecast v2)"
    _order = "date, warehouse_id, product_id"
    _check_company_auto = True

    run_id = fields.Many2one("efn.forecast.run", required=True, ondelete="cascade", index=True, readonly=True)
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", required=True, check_company=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True, ondelete="cascade")
    date = fields.Date(required=True, readonly=True)
    yhat = fields.Float(digits=(16, 3), readonly=True)
    p10 = fields.Float(digits=(16, 3), readonly=True)
    p90 = fields.Float(digits=(16, 3), readonly=True)
    sigma = fields.Float(digits=(16, 3), readonly=True)
    model = fields.Selection(FORECAST_MODELS, readonly=True)

    _efn_daily_key_uniq = models.Constraint(
        "UNIQUE(run_id, warehouse_id, product_id, date)",
        "Proyeksi (run, gudang, produk, tanggal) sudah ada.",
    )
    _efn_daily_lookup_idx = models.Index("(run_id, warehouse_id, product_id, date)")

    # =================================================================== RPC
    @api.model
    def efn_forecast_ingest(self, **payload):
        """docs/04 §28.2: proyeksi harian per chunk. Idempoten ``(run_id, chunk_no)``; baris tidak valid ditolak per
        baris (``rejected[{index, code}]``), bukan seluruh chunk."""
        env, company = fa.check_caller(self.env)
        Chunk = env["efn.forecast.chunk"]
        run, existing, lines = Chunk._efn_begin_chunk(env, company, payload, "daily")
        if existing:
            return Chunk._efn_duplicate_response(run, existing)
        warehouses, products = Chunk._efn_scope(env, company, lines)
        today = fa.local_today(env)
        last_day = today + timedelta(days=run.horizon_days)
        cap = fa.max_qty(env)
        rejected, values, positions, seen = [], [], [], set()
        for index, line in enumerate(lines):
            code = self._efn_validate_line(line, warehouses, products, today, last_day, cap=cap)
            if code:
                rejected.append({"index": index, "code": code})
                continue
            wh = warehouses[line["warehouse_code"]]
            day = fa.parse_date(line["date"])
            key = (wh.id, line["product_id"], day)
            if key in seen:
                rejected.append({"index": index, "code": "duplicate_line"})
                continue
            seen.add(key)
            positions.append(index)
            values.append(
                {
                    "run_id": run.id,
                    "company_id": company.id,
                    "warehouse_id": wh.id,
                    "product_id": line["product_id"],
                    "date": day,
                    "yhat": float(line["yhat"]),
                    "p10": float(line["p10"]),
                    "p90": float(line["p90"]),
                    "sigma": float(line["sigma"]),
                    "model": line["model"],
                }
            )
        if values:
            # Baris yang sudah ada di chunk lain run yang sama -> ditolak per baris (bukan galat unik seluruh chunk).
            existing_keys = set()
            stored = env["efn.forecast.daily"].search_read(
                [
                    ("run_id", "=", run.id),
                    ("warehouse_id", "in", list({v["warehouse_id"] for v in values})),
                    ("product_id", "in", list({v["product_id"] for v in values})),
                    ("date", "in", list({v["date"] for v in values})),
                ],
                ["warehouse_id", "product_id", "date"],
            )
            for row in stored:
                existing_keys.add((row["warehouse_id"][0], row["product_id"][0], row["date"]))
            if existing_keys:
                kept = []
                for position, vals in zip(positions, values, strict=True):
                    if (vals["warehouse_id"], vals["product_id"], vals["date"]) in existing_keys:
                        rejected.append({"index": position, "code": "duplicate_line"})
                    else:
                        kept.append(vals)
                values = kept
                rejected.sort(key=lambda r: r["index"])
            env["efn.forecast.daily"].create(values)
        return Chunk._efn_finish_chunk(env, run, payload["chunk_no"], len(values), rejected)

    @api.model
    def _efn_validate_line(self, line, warehouses, products, today, last_day, cap=None):
        """Kode tolak baris atau ``None``. ``cap`` = plafon nilai (SEC-211, ``efn_forecast.max_qty_per_line``)."""
        if not isinstance(line, dict) or set(line) != LINE_KEYS:
            return "invalid_line"
        if not isinstance(line["warehouse_code"], str) or line["warehouse_code"] not in warehouses:
            return "unknown_warehouse"
        if not fa.is_int(line["product_id"]) or line["product_id"] not in products:
            return "unknown_product"
        day = fa.parse_date(line["date"])
        if day is None or not today <= day <= last_day:
            return "invalid_date"
        if not all(fa.is_number(line[key]) for key in ("yhat", "p10", "p90", "sigma")):
            return "invalid_number"
        if cap is not None and any(float(line[key]) > cap for key in ("yhat", "p10", "p90", "sigma")):
            return "value_too_large"
        if not float(line["p10"]) <= float(line["yhat"]) <= float(line["p90"]):
            return "invalid_quantile"
        if line["model"] not in FORECAST_MODEL_CODES:
            return "invalid_model"
        return None

    # --------------------------------------------------------------- retensi
    @api.autovacuum
    def _gc_efn_forecast_daily(self):
        try:
            days = int(self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM) or DEFAULT_RETENTION_DAYS)
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        days = max(days, 7)
        cutoff = fields.Datetime.now() - timedelta(days=days)
        runs = (
            self.env["efn.forecast.run"]
            .sudo()
            .search([("is_active", "=", False), ("state", "!=", "open"), ("closed_at", "<", cutoff)])
        )
        if not runs:
            return 0
        old = self.sudo().search([("run_id", "in", runs.ids)], limit=GC_BATCH)
        count = len(old)
        old.unlink()
        return count
