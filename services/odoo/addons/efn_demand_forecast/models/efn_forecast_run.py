"""Run forecast v2 dari forecast-worker (G44, docs/04 §28.2, ADR-046, L-145/L-147).

Alur: ``efn_forecast_run_open`` -> N x ``efn_forecast_ingest`` (kind ``daily``) atau ``efn_minmax_ingest`` (kind
``weekly_minmax``) per chunk -> ``efn_forecast_run_close``. Run ``ok`` menjadi run AKTIF per (company, kind); run aktif
sebelumnya dilepas (proyeksinya tetap tersimpan). Run ``failed`` tidak mengubah run aktif. Run yang tidak ditutup
dalam 6 jam -> ``failed`` oleh cron (``_cron_efn_fail_stale_runs``).

Setelah run ``daily`` ditutup ``ok``, hook ``_efn_on_run_closed_ok`` dipanggil (default: no-op). ``efn_replenishment``
meng-override-nya untuk MENANDAI run dan memicu cron antrean (perhitungan usulan TIDAK berjalan di request RPC).

Catatan nama: dulu ``efn.forecast.run`` adalah wizard "Hitung Forecast" v1 (TransientModel); sejak 19.0.2.0.0 wizard
itu bernama ``efn.forecast.run.wizard`` karena nama model ini dikunci kontrak §28.2 (path gateway).
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

from . import forecast_api as fa

_logger = logging.getLogger(__name__)

RUN_KINDS = [("daily", "Proyeksi harian"), ("weekly_minmax", "Min/max mingguan")]
STALE_HOURS = 6
METRIC_KEYS = ("series", "duration_s", "wape_backtest", "failed_series")


class EfnForecastRun(models.Model):
    _name = "efn.forecast.run"
    _description = "EFN run forecast v2 (forecast-worker)"
    # Aktivitas (mis. efn_replenishment: run ok tanpa baris diterima) ke penanggung jawab.
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "opened_at desc, id desc"
    _check_company_auto = True
    _rec_name = "run_id"

    run_id = fields.Char(string="Run ID", required=True, readonly=True, index=True, size=36)
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    model_version = fields.Char(readonly=True, size=32)
    kind = fields.Selection(RUN_KINDS, required=True, readonly=True)
    generated_at = fields.Datetime(readonly=True)
    horizon_days = fields.Integer(readonly=True)
    chunk_total = fields.Integer(readonly=True)
    state = fields.Selection(
        [("open", "Terbuka"), ("ok", "Selesai"), ("failed", "Gagal")], default="open", required=True, readonly=True
    )
    is_active = fields.Boolean(string="Run aktif", readonly=True, index=True)
    opened_at = fields.Datetime(readonly=True, default=fields.Datetime.now)
    closed_at = fields.Datetime(readonly=True)
    fail_reason = fields.Selection(
        [("worker", "Dilaporkan worker"), ("stale", "Tidak ditutup > 6 jam")], readonly=True
    )
    metric_series = fields.Integer(readonly=True)
    metric_duration_s = fields.Float(readonly=True)
    metric_wape_backtest = fields.Float(readonly=True, digits=(6, 4))
    metric_failed_series = fields.Integer(readonly=True)
    chunk_ids = fields.One2many("efn.forecast.chunk", "run_id", readonly=True)
    chunk_count = fields.Integer(compute="_compute_chunk_count")

    _efn_run_id_uniq = models.Constraint("UNIQUE(run_id)", "Run ID forecast harus unik.")

    @api.depends("chunk_ids")
    def _compute_chunk_count(self):
        for run in self:
            # Hitungan saja (tanpa isi) -> sudo agar user stok (baca run) tidak butuh ACL chunk.
            run.chunk_count = len(run.sudo().chunk_ids)

    # ------------------------------------------------------------- helper
    @api.model
    def _efn_find_run(self, env, company, run_id):
        """Run milik company pemanggil; milik PT lain -> 403; tidak ada -> unknown_run."""
        if not isinstance(run_id, str) or not fa.UUID4_RE.match(run_id):
            raise fa.forecast_error("invalid_input")
        run = env["efn.forecast.run"].search([("run_id", "=", run_id)], limit=1)
        if not run:
            raise fa.forecast_error("unknown_run")
        if run.company_id != company:
            raise fa.company_mismatch()
        return run

    def _efn_lock(self):
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute("SELECT id FROM efn_forecast_run WHERE id = %s FOR UPDATE", (self.id,))
        self.invalidate_recordset()

    def _efn_status(self, **extra):
        self.ensure_one()
        return dict(
            {"ok": True, "run_id": self.run_id, "state": self.state, "is_active": bool(self.is_active)}, **extra
        )

    def _efn_on_run_closed_ok(self):
        """Hook setelah run ditutup ``ok`` (sudah aktif). Default no-op; ``efn_replenishment`` menandai antrean."""
        return True

    # =================================================================== RPC
    @api.model
    def efn_forecast_run_open(self, **payload):
        """docs/04 §28.2: buka run. ``run_id`` sama dibuka ulang -> status run itu (``created: false``)."""
        env, company = fa.check_caller(self.env)
        fa.check_payload(
            payload,
            (
                "schema_version",
                "run_id",
                "company_code",
                "model_version",
                "kind",
                "generated_at",
                "horizon_days",
                "chunk_total",
            ),
        )
        fa.check_company_code(company, payload["company_code"])
        run_id = payload["run_id"]
        generated = fa.parse_rfc3339_utc(payload["generated_at"])
        horizon = payload["horizon_days"]
        chunks = payload["chunk_total"]
        if (
            not isinstance(run_id, str)
            or not fa.UUID4_RE.match(run_id)
            or not isinstance(payload["model_version"], str)
            or not fa.MODEL_VERSION_RE.match(payload["model_version"])
            or payload["kind"] not in dict(RUN_KINDS)
            or generated is None
            or not fa.is_int(horizon)
            or not 1 <= horizon <= fa.MAX_HORIZON_DAYS
            or not fa.is_int(chunks)
            or not 1 <= chunks <= fa.MAX_CHUNKS
        ):
            raise fa.forecast_error("invalid_input")
        Run = env["efn.forecast.run"]
        existing = Run.search([("run_id", "=", run_id)], limit=1)
        if existing:
            if existing.company_id != company:
                raise fa.company_mismatch()
            if existing.kind != payload["kind"]:
                raise fa.forecast_error("run_conflict")
            return existing._efn_status(created=False)
        run = Run.create(
            {
                "run_id": run_id,
                "company_id": company.id,
                "model_version": payload["model_version"],
                "kind": payload["kind"],
                "generated_at": generated,
                "horizon_days": horizon,
                "chunk_total": chunks,
            }
        )
        return run._efn_status(created=True)

    @api.model
    def efn_forecast_run_close(self, **payload):
        """docs/04 §28.2: tutup run + metrik. Tutup dua kali -> no-op (``duplicate_close: true``).

        ``ok`` hanya bila semua chunk (0..chunk_total-1) sudah diterima (selain itu ``incomplete_run``, run tetap
        terbuka agar worker bisa mengirim ulang chunk yang hilang)."""
        env, company = fa.check_caller(self.env)
        fa.check_payload(
            payload, ("schema_version", "run_id", "status", "metrics"), ("schema_version", "run_id", "status")
        )
        status = payload["status"]
        metrics = payload.get("metrics") or {}
        if status not in ("ok", "failed") or not isinstance(metrics, dict) or set(metrics) - set(METRIC_KEYS):
            raise fa.forecast_error("invalid_input")
        for key in ("series", "failed_series"):
            if key in metrics and not (fa.is_int(metrics[key]) and metrics[key] >= 0):
                raise fa.forecast_error("invalid_input")
        for key in ("duration_s", "wape_backtest"):
            if key in metrics and not fa.is_number(metrics[key]):
                raise fa.forecast_error("invalid_input")
        run = self._efn_find_run(env, company, payload["run_id"])
        run._efn_lock()
        if run.state != "open":
            return run._efn_status(duplicate_close=True)
        if status == "ok":
            received = set(run.chunk_ids.mapped("chunk_no"))
            if not set(range(run.chunk_total)) <= received:
                raise fa.forecast_error("incomplete_run")
        vals = {
            "state": status,
            "closed_at": fields.Datetime.now(),
            "metric_series": metrics.get("series", 0),
            "metric_duration_s": metrics.get("duration_s", 0.0),
            "metric_wape_backtest": metrics.get("wape_backtest", 0.0),
            "metric_failed_series": metrics.get("failed_series", 0),
        }
        if status == "failed":
            vals["fail_reason"] = "worker"
            run.write(vals)
            return run._efn_status(duplicate_close=False)
        previous = env["efn.forecast.run"].search(
            [("company_id", "=", company.id), ("kind", "=", run.kind), ("is_active", "=", True), ("id", "!=", run.id)]
        )
        previous.write({"is_active": False})
        vals["is_active"] = True
        run.write(vals)
        run._efn_on_run_closed_ok()
        return run._efn_status(duplicate_close=False)

    # ---------------------------------------------------------------- cron
    @api.model
    def _cron_efn_fail_stale_runs(self):
        """Run ``open`` lebih dari 6 jam -> ``failed``; run aktif sebelumnya tetap dipakai (§28.2)."""
        limit = fields.Datetime.now() - timedelta(hours=STALE_HOURS)
        stale = self.sudo().search([("state", "=", "open"), ("opened_at", "<", limit)])
        if stale:
            stale.write({"state": "failed", "fail_reason": "stale", "closed_at": fields.Datetime.now()})
            _logger.warning("EFN forecast: %d run tidak ditutup > %d jam -> failed", len(stale), STALE_HOURS)
        return len(stale)

    @api.model
    def _efn_active_run(self, company, kind="daily"):
        return self.sudo().search(
            [("company_id", "=", company.id), ("kind", "=", kind), ("is_active", "=", True), ("state", "=", "ok")],
            limit=1,
        )


class EfnForecastChunk(models.Model):
    """Log chunk yang diterima (idempotensi ``(run_id, chunk_no)``); hanya ditulis RPC via sudo."""

    _name = "efn.forecast.chunk"
    _description = "EFN chunk ingest forecast"
    _order = "run_id, chunk_no"
    _check_company_auto = True

    run_id = fields.Many2one("efn.forecast.run", required=True, ondelete="cascade", index=True, readonly=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True)
    chunk_no = fields.Integer(required=True, readonly=True)
    accepted = fields.Integer(readonly=True)
    rejected = fields.Integer(readonly=True)
    received_at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    _efn_chunk_uniq = models.Constraint("UNIQUE(run_id, chunk_no)", "Chunk forecast sudah diterima.")

    @api.model
    def _efn_begin_chunk(self, env, company, payload, kind):
        """Validasi umum ingest + kunci run. Kembalikan ``(run, existing_chunk_or_None, lines)``."""
        fa.check_payload(payload, ("schema_version", "run_id", "chunk_no", "lines"))
        lines = payload["lines"]
        fa.check_items(lines)
        chunk_no = payload["chunk_no"]
        if not fa.is_int(chunk_no) or chunk_no < 0:
            raise fa.forecast_error("invalid_chunk")
        run = env["efn.forecast.run"]._efn_find_run(env, company, payload["run_id"])
        if run.kind != kind:
            raise fa.forecast_error("kind_mismatch")
        if chunk_no >= run.chunk_total:
            raise fa.forecast_error("invalid_chunk")
        run._efn_lock()
        existing = env["efn.forecast.chunk"].search([("run_id", "=", run.id), ("chunk_no", "=", chunk_no)], limit=1)
        if existing:
            return run, existing, lines
        if run.state != "open":
            raise fa.forecast_error("run_closed")
        return run, None, lines

    @api.model
    def _efn_duplicate_response(self, run, chunk):
        return {
            "ok": True,
            "run_id": run.run_id,
            "chunk_no": chunk.chunk_no,
            "accepted": chunk.accepted,
            "rejected": [],
            "duplicate_chunk": True,
        }

    @api.model
    def _efn_finish_chunk(self, env, run, chunk_no, accepted, rejected):
        env["efn.forecast.chunk"].create(
            {"run_id": run.id, "chunk_no": chunk_no, "accepted": accepted, "rejected": len(rejected)}
        )
        return {
            "ok": True,
            "run_id": run.run_id,
            "chunk_no": chunk_no,
            "accepted": accepted,
            "rejected": rejected,
            "duplicate_chunk": False,
        }

    @api.model
    def _efn_scope(self, env, company, lines):
        """Peta ``warehouse_code -> warehouse`` dan id produk yang boleh dipakai company (milik PT atau bersama)."""
        codes = {line.get("warehouse_code") for line in lines if isinstance(line, dict)}
        codes = {code for code in codes if isinstance(code, str) and 0 < len(code) <= 16}
        warehouses = {}
        if codes:
            for wh in env["stock.warehouse"].search([("company_id", "=", company.id), ("code", "in", sorted(codes))]):
                warehouses[wh.code] = wh
        ids = {line.get("product_id") for line in lines if isinstance(line, dict)}
        ids = [pid for pid in ids if fa.is_int(pid) and pid > 0]
        products = set()
        if ids:
            products = set(
                env["product.product"]
                .with_context(active_test=False)
                .search([("id", "in", ids), ("company_id", "in", (False, company.id))])
                .ids
            )
        return warehouses, products
