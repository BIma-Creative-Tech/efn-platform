"""G44 (docs/04 §28.2-§28.3): RPC forecast-worker -> Odoo, kalender, musim payday/holiday, engine v1|v2.

Semua data = data uji. Metode dipanggil langsung sebagai user teknis (jalur /json/2 memakai get_public_method yang
sama; gateway HAProxy di luar cakupan test ini)."""

import uuid
from datetime import date, timedelta

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_demand_forecast.models import forecast_api


def _rid():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "efn")
class TestEfnForecastApi(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        # Kode SMD mungkin dipakai efn_setup_supermarket; bebaskan di transaksi uji (rollback setelah kelas).
        Company.search([("efn_brand_code", "=", "SMD")]).write({"efn_brand_code": False})
        Company.flush_model(["efn_brand_code"])
        cls.company = Company.create({"name": "PT Uji Forecast SMD", "efn_brand_code": "SMD"})
        cls.other = Company.create({"name": "PT Uji Forecast Lain"})
        cls.wh = cls.env["stock.warehouse"].create(
            {"name": "Outlet uji F1", "code": "SMDT1", "company_id": cls.company.id}
        )
        cls.wh_other = cls.env["stock.warehouse"].create(
            {"name": "Gudang PT lain", "code": "OTHT1", "company_id": cls.other.id}
        )
        cls.product = cls.env["product.product"].create({"name": "Susu UHT (uji)", "is_storable": True})
        cls.product_other = cls.env["product.product"].create(
            {"name": "Produk PT lain (uji)", "is_storable": True, "company_id": cls.other.id}
        )
        cls.bot = new_test_user(
            cls.env,
            login="efn_forecast_smd_uji",
            groups="base.group_user,efn_demand_forecast.group_forecast_bot",
            company_id=cls.company.id,
            tz="Asia/Jakarta",
        )
        cls.today = forecast_api.local_today(cls.env(context={"tz": "Asia/Jakarta"}))

    # ------------------------------------------------------------ helper
    def as_bot(self, model, user=None):
        return self.env[model].with_user(user or self.bot)

    def open_run(self, kind="daily", chunks=2, run_id=None):
        run_id = run_id or _rid()
        res = self.as_bot("efn.forecast.run").efn_forecast_run_open(
            schema_version=1,
            run_id=run_id,
            company_code="SMD",
            model_version="g44-1.0.0",
            kind=kind,
            generated_at="2026-11-18T16:30:00Z",
            horizon_days=28,
            chunk_total=chunks,
        )
        return run_id, res

    def line(self, **over):
        vals = {
            "warehouse_code": "SMDT1",
            "product_id": self.product.id,
            "date": self.today.isoformat(),
            "yhat": 12.4,
            "p10": 8.0,
            "p90": 17.1,
            "sigma": 2.9,
            "model": "AutoETS",
        }
        vals.update(over)
        return vals

    def ingest(self, run_id, chunk_no, lines):
        return self.as_bot("efn.forecast.daily").efn_forecast_ingest(
            schema_version=1, run_id=run_id, chunk_no=chunk_no, lines=lines
        )

    def close(self, run_id, status="ok"):
        return self.as_bot("efn.forecast.run").efn_forecast_run_close(
            schema_version=1, run_id=run_id, status=status, metrics={"series": 1, "duration_s": 3.5}
        )

    # ------------------------------------------------------------ caller
    def test_caller_must_be_single_company_bot(self):
        plain = new_test_user(self.env, login="efn_fc_plain_uji", groups="base.group_user", company_id=self.company.id)
        with self.assertRaises(AccessError):
            self.as_bot("efn.forecast.run", plain).efn_forecast_run_open(schema_version=1)
        self.bot.sudo().write({"company_ids": [(4, self.other.id)]})
        with self.assertRaises(AccessError):
            self.open_run()
        self.bot.sudo().write({"company_ids": [(3, self.other.id)]})
        self.bot.sudo().write({"group_ids": [(4, self.env.ref("stock.group_stock_manager").id)]})
        with self.assertRaises(AccessError):
            self.open_run()

    def test_company_code_mismatch_403(self):
        with self.assertRaisesRegex(AccessError, "company_mismatch"):
            self.as_bot("efn.forecast.run").efn_forecast_run_open(
                schema_version=1,
                run_id=_rid(),
                company_code="PB",
                model_version="g44-1.0.0",
                kind="daily",
                generated_at="2026-11-18T16:30:00Z",
                horizon_days=28,
                chunk_total=1,
            )

    # ------------------------------------------------------------ kalender
    def test_calendar_upsert_idempotent_manual_kept(self):
        Calendar = self.env["efn.forecast.calendar"]
        manual = Calendar.create(
            {"company_id": self.company.id, "date": "2027-03-10", "kind": "holiday", "name": "Idul Fitri"}
        )
        entries = [
            {"date": "2027-03-10", "kind": "holiday", "name": "Idul Fitri", "source": "lib"},
            {"date": "2027-03-11", "kind": "cuti_bersama", "name": "Cuti Bersama Idul Fitri", "source": "lib"},
            {"date": "2027-13-01", "kind": "holiday", "name": "Salah", "source": "lib"},
            {"date": "2027-03-12", "kind": "weekend", "name": "Salah", "source": "lib"},
        ]
        upsert = self.as_bot("efn.forecast.calendar").efn_forecast_calendar_upsert
        res = upsert(schema_version=1, company_code="SMD", entries=entries)
        self.assertEqual((res["accepted"], res["skipped_manual"], res["unchanged"]), (1, 1, 0))
        self.assertEqual(res["rejected"], [{"index": 2, "code": "invalid_date"}, {"index": 3, "code": "invalid_kind"}])
        self.assertEqual(manual.source, "manual")
        lib = Calendar.search([("company_id", "=", self.company.id), ("kind", "=", "cuti_bersama")])
        self.assertEqual(lib.source, "lib")
        lib.active = False
        res = upsert(schema_version=1, company_code="SMD", entries=entries[:2])
        self.assertEqual((res["accepted"], res["unchanged"], res["skipped_manual"]), (0, 1, 1))
        self.assertFalse(lib.with_context(active_test=False).active, "entri lib nonaktif tidak diaktifkan ulang")
        with self.assertRaisesRegex(UserError, "too_many_items"):
            upsert(schema_version=1, company_code="SMD", entries=[entries[1]] * 501)
        with self.assertRaisesRegex(UserError, "invalid_input"):
            upsert(schema_version=1, company_code="SMD", entries=[], extra=1)
        with self.assertRaisesRegex(UserError, "unsupported_schema"):
            upsert(schema_version=2, company_code="SMD", entries=[])

    def test_payload_size_limit_512kb(self):
        """Batas 512 KB (kontrak §28.2) dicek sebelum batas 500 baris: 300 entri x ~2 KB nama = ~600 KB."""
        big = {"date": "2027-03-10", "kind": "holiday", "name": "x" * 2000, "source": "lib"}
        with self.assertRaisesRegex(UserError, "payload_too_large"):
            self.as_bot("efn.forecast.calendar").efn_forecast_calendar_upsert(
                schema_version=1, company_code="SMD", entries=[big] * 300
            )

    # ------------------------------------------------------------ run & ingest
    def test_run_open_validation_and_conflict(self):
        """Jalur gagal run_open: input tidak valid -> 422 invalid_input; run_id sama beda kind -> run_conflict."""
        good = {
            "schema_version": 1,
            "run_id": _rid(),
            "company_code": "SMD",
            "model_version": "g44-1.0.0",
            "kind": "daily",
            "generated_at": "2026-11-18T16:30:00Z",
            "horizon_days": 28,
            "chunk_total": 1,
        }
        bad_values = {
            "run_id": "bukan-uuid",
            "model_version": "versi dengan spasi",
            "kind": "hourly",
            "generated_at": "2026-11-18 16:30:00",
            "horizon_days": 121,
            "chunk_total": 0,
        }
        opener = self.as_bot("efn.forecast.run").efn_forecast_run_open
        for key, value in bad_values.items():
            with self.subTest(field=key), self.assertRaisesRegex(UserError, "invalid_input"):
                opener(**dict(good, **{key: value}))
        with self.assertRaisesRegex(UserError, "invalid_input"):
            opener(**dict(good, extra="x"))  # additionalProperties: false
        with self.assertRaisesRegex(UserError, "unsupported_schema"):
            opener(**dict(good, schema_version=2))
        self.assertFalse(self.env["efn.forecast.run"].search([("run_id", "=", good["run_id"])]))
        opener(**good)
        with self.assertRaisesRegex(UserError, "run_conflict"):
            opener(**dict(good, kind="weekly_minmax"))

    def test_close_metrics_validation(self):
        run_id, _res = self.open_run(chunks=1)
        closer = self.as_bot("efn.forecast.run").efn_forecast_run_close
        for metrics in ({"series": -1}, {"duration_s": float("inf")}, {"unknown": 1}, {"series": True}):
            with self.subTest(metrics=metrics), self.assertRaisesRegex(UserError, "invalid_input"):
                closer(schema_version=1, run_id=run_id, status="failed", metrics=metrics)
        with self.assertRaisesRegex(UserError, "invalid_input"):
            closer(schema_version=1, run_id=run_id, status="done", metrics={})
        self.assertEqual(self.env["efn.forecast.run"].search([("run_id", "=", run_id)]).state, "open")

    def test_run_open_reopen_returns_status(self):
        run_id, res = self.open_run()
        self.assertEqual((res["state"], res["created"]), ("open", True))
        _rid2, again = self.open_run(run_id=run_id)
        self.assertEqual((again["state"], again["created"]), ("open", False))
        self.assertEqual(self.env["efn.forecast.run"].search_count([("run_id", "=", run_id)]), 1)

    def test_ingest_chunk_idempotent_and_partial_reject(self):
        run_id, _res = self.open_run(chunks=2)
        lines = [
            self.line(),
            self.line(warehouse_code="OTHT1"),
            self.line(product_id=self.product_other.id),
            self.line(date=(self.today + timedelta(days=1)).isoformat(), yhat=20.0),  # yhat > p90
            self.line(date=(self.today + timedelta(days=40)).isoformat()),  # di luar horizon 28
            self.line(date=(self.today - timedelta(days=1)).isoformat()),
            self.line(date=(self.today + timedelta(days=2)).isoformat(), model="Prophet"),
            self.line(date=(self.today + timedelta(days=3)).isoformat(), p10=-1),
            {"warehouse_code": "SMDT1"},
        ]
        res = self.ingest(run_id, 0, lines)
        self.assertEqual(res["accepted"], 1)
        self.assertFalse(res["duplicate_chunk"])
        self.assertEqual(
            res["rejected"],
            [
                {"index": 1, "code": "unknown_warehouse"},
                {"index": 2, "code": "unknown_product"},
                {"index": 3, "code": "invalid_quantile"},
                {"index": 4, "code": "invalid_date"},
                {"index": 5, "code": "invalid_date"},
                {"index": 6, "code": "invalid_model"},
                {"index": 7, "code": "invalid_number"},
                {"index": 8, "code": "invalid_line"},
            ],
        )
        Daily = self.env["efn.forecast.daily"]
        run = self.env["efn.forecast.run"].search([("run_id", "=", run_id)])
        self.assertEqual(Daily.search_count([("run_id", "=", run.id)]), 1)
        # Ulang chunk yang sama dengan isi berbeda -> duplicate, tidak ditulis ulang.
        again = self.ingest(run_id, 0, [self.line(date=(self.today + timedelta(days=5)).isoformat())])
        self.assertTrue(again["duplicate_chunk"])
        self.assertEqual(again["accepted"], 1)
        self.assertEqual(Daily.search_count([("run_id", "=", run.id)]), 1)
        # Baris yang sama di chunk lain -> ditolak per baris.
        res2 = self.ingest(run_id, 1, [self.line(), self.line(date=(self.today + timedelta(days=1)).isoformat())])
        self.assertEqual(res2["accepted"], 1)
        self.assertEqual(res2["rejected"], [{"index": 0, "code": "duplicate_line"}])
        with self.assertRaisesRegex(UserError, "invalid_chunk"):
            self.ingest(run_id, 2, [])
        with self.assertRaisesRegex(UserError, "too_many_items"):
            self.ingest(run_id, 1, [self.line()] * 501)
        with self.assertRaisesRegex(UserError, "kind_mismatch"):
            self.as_bot("efn.demand.forecast.line").efn_minmax_ingest(
                schema_version=1, run_id=run_id, chunk_no=0, lines=[]
            )

    def test_close_requires_all_chunks_then_activates(self):
        first_id, _res = self.open_run(chunks=1)
        self.ingest(first_id, 0, [self.line()])
        self.assertTrue(self.close(first_id)["is_active"])
        run_id, _res = self.open_run(chunks=2)
        self.ingest(run_id, 0, [self.line()])
        with self.assertRaisesRegex(UserError, "incomplete_run"):
            self.close(run_id)
        self.ingest(run_id, 1, [])
        res = self.close(run_id)
        self.assertEqual((res["state"], res["is_active"], res["duplicate_close"]), ("ok", True, False))
        Run = self.env["efn.forecast.run"]
        self.assertFalse(Run.search([("run_id", "=", first_id)]).is_active, "run aktif lama dilepas")
        self.assertTrue(self.close(run_id)["duplicate_close"])
        closed_id = self.open_closed_run()
        with self.assertRaisesRegex(UserError, "run_closed"):
            self.ingest(closed_id, 0, [self.line()])

    def open_closed_run(self):
        run_id, _res = self.open_run(chunks=1)
        self.close(run_id, status="failed")
        return run_id

    def test_failed_close_keeps_previous_active(self):
        ok_id, _res = self.open_run(chunks=1)
        self.ingest(ok_id, 0, [])
        self.close(ok_id)
        failed_id = self.open_closed_run()
        Run = self.env["efn.forecast.run"]
        self.assertTrue(Run.search([("run_id", "=", ok_id)]).is_active)
        self.assertEqual(Run.search([("run_id", "=", failed_id)]).state, "failed")

    def test_foreign_run_is_company_mismatch(self):
        foreign = self.env["efn.forecast.run"].create(
            {"run_id": _rid(), "company_id": self.other.id, "kind": "daily", "chunk_total": 1, "horizon_days": 28}
        )
        with self.assertRaisesRegex(AccessError, "company_mismatch"):
            self.ingest(foreign.run_id, 0, [])
        with self.assertRaisesRegex(UserError, "unknown_run"):
            self.ingest(_rid(), 0, [])

    def test_stale_run_failed_by_cron(self):
        run_id, _res = self.open_run()
        run = self.env["efn.forecast.run"].search([("run_id", "=", run_id)])
        run.opened_at = run.opened_at - timedelta(hours=7)
        self.env["efn.forecast.run"]._cron_efn_fail_stale_runs()
        self.assertEqual((run.state, run.fail_reason), ("failed", "stale"))

    # ------------------------------------------------------------ min/max v2
    def test_minmax_ingest_engine_switch(self):
        run_id, _res = self.open_run(kind="weekly_minmax", chunks=1)
        line = {
            "warehouse_code": "SMDT1",
            "product_id": self.product.id,
            "min_qty": 10,
            "max_qty": 40,
            "service_level": 0.95,
            "basis_daily_demand": 3.2,
        }
        bad = dict(line, min_qty=50)
        res = self.as_bot("efn.demand.forecast.line").efn_minmax_ingest(
            schema_version=1, run_id=run_id, chunk_no=0, lines=[line, bad]
        )
        self.assertEqual(res["accepted"], 1)
        self.assertEqual(res["rejected"], [{"index": 1, "code": "invalid_minmax"}])
        fc = self.env["efn.demand.forecast"].search(
            [("warehouse_id", "=", self.wh.id), ("product_id", "=", self.product.id), ("source", "=", "v2")]
        )
        self.assertEqual((fc.state, fc.proposed_min, fc.proposed_max, fc.service_level), ("shadow", 10, 40, "95"))
        with self.assertRaisesRegex(UserError, "state"):
            fc.action_efn_apply()
        # Engine v2 -> usulan draft yang bisa diterapkan planner.
        self.wh.efn_forecast_engine = "v2"
        run2, _res = self.open_run(kind="weekly_minmax", chunks=1)
        self.as_bot("efn.demand.forecast.line").efn_minmax_ingest(
            schema_version=1, run_id=run2, chunk_no=0, lines=[line]
        )
        draft = self.env["efn.demand.forecast"].search(
            [("warehouse_id", "=", self.wh.id), ("product_id", "=", self.product.id), ("state", "=", "draft")]
        )
        self.assertEqual(draft.source, "v2")
        self.assertEqual(fc.state, "superseded")
        draft.action_efn_apply()
        self.assertEqual(draft.orderpoint_id.product_max_qty, 40)

    def test_minmax_chunk_idempotent_and_incomplete_close(self):
        """Retry chunk min/max (timeout lalu kirim ulang) tidak membuat usulan ganda; close butuh semua chunk."""
        run_id, _res = self.open_run(kind="weekly_minmax", chunks=2)
        line = {
            "warehouse_code": "SMDT1",
            "product_id": self.product.id,
            "min_qty": 10,
            "max_qty": 40,
            "service_level": 0.95,
            "basis_daily_demand": 3.2,
        }
        ingest = self.as_bot("efn.demand.forecast.line").efn_minmax_ingest
        first = ingest(schema_version=1, run_id=run_id, chunk_no=0, lines=[line])
        self.assertEqual((first["accepted"], first["duplicate_chunk"]), (1, False))
        Forecast = self.env["efn.demand.forecast"]
        domain = [("warehouse_id", "=", self.wh.id), ("product_id", "=", self.product.id), ("source", "=", "v2")]
        before = Forecast.search_count(domain)
        again = ingest(schema_version=1, run_id=run_id, chunk_no=0, lines=[line])
        self.assertEqual((again["accepted"], again["duplicate_chunk"]), (1, True))
        self.assertEqual(Forecast.search_count(domain), before)
        # Pasangan gudang+produk yang sama di chunk lain run yang sama -> ditolak per baris.
        other = ingest(schema_version=1, run_id=run_id, chunk_no=1, lines=[line])
        self.assertEqual((other["accepted"], other["rejected"]), (0, [{"index": 0, "code": "duplicate_line"}]))
        # service_level di luar [0.5, 1) ditolak (kontrak: probabilitas), baris lain tetap jalan.
        run2, _res = self.open_run(kind="weekly_minmax", chunks=2)
        res = ingest(schema_version=1, run_id=run2, chunk_no=0, lines=[dict(line, service_level=1.0)])
        self.assertEqual(res["rejected"], [{"index": 0, "code": "invalid_service_level"}])
        with self.assertRaisesRegex(UserError, "incomplete_run"):
            self.close(run2)
        self.close(run2, status="failed")  # worker menyerah -> run failed; chunk susulan ditolak
        with self.assertRaisesRegex(UserError, "run_closed"):
            ingest(schema_version=1, run_id=run2, chunk_no=1, lines=[line])

    def test_sec211_value_cap_rejects_lines(self):
        self.env["ir.config_parameter"].sudo().set_param("efn_forecast.max_qty_per_line", "1000")
        run_id, _res = self.open_run(chunks=1)
        res = self.ingest(
            run_id,
            0,
            [
                self.line(),
                self.line(date=(self.today + timedelta(days=1)).isoformat(), yhat=500.0, p10=400.0, p90=1500.0),
                self.line(date=(self.today + timedelta(days=2)).isoformat(), yhat=1e15, p10=1e15, p90=1e15),
            ],
        )
        self.assertEqual(res["accepted"], 1)
        self.assertEqual(
            res["rejected"], [{"index": 1, "code": "value_too_large"}, {"index": 2, "code": "value_too_large"}]
        )
        run2, _res = self.open_run(kind="weekly_minmax", chunks=1)
        line = {
            "warehouse_code": "SMDT1",
            "product_id": self.product.id,
            "min_qty": 10,
            "max_qty": 5000,
            "service_level": 0.95,
            "basis_daily_demand": 3.2,
        }
        res = self.as_bot("efn.demand.forecast.line").efn_minmax_ingest(
            schema_version=1, run_id=run2, chunk_no=0, lines=[line]
        )
        self.assertEqual(res["rejected"], [{"index": 0, "code": "value_too_large"}])

    def test_sec212_state_source_not_writable_by_stock_user(self):
        run_id, _res = self.open_run(kind="weekly_minmax", chunks=1)
        line = {
            "warehouse_code": "SMDT1",
            "product_id": self.product.id,
            "min_qty": 10,
            "max_qty": 40,
            "service_level": 0.95,
            "basis_daily_demand": 3.2,
        }
        self.as_bot("efn.demand.forecast.line").efn_minmax_ingest(
            schema_version=1, run_id=run_id, chunk_no=0, lines=[line]
        )
        shadow = self.env["efn.demand.forecast"].search(
            [("warehouse_id", "=", self.wh.id), ("product_id", "=", self.product.id), ("state", "=", "shadow")]
        )
        self.assertTrue(shadow)
        planner = new_test_user(
            self.env,
            login="efn_fc_planner_uji",
            groups="stock.group_stock_manager",
            company_id=self.company.id,
        )
        clerk = new_test_user(
            self.env, login="efn_fc_clerk2_uji", groups="stock.group_stock_user", company_id=self.company.id
        )
        for user in (clerk, planner):
            for vals in ({"state": "draft"}, {"source": "v1"}, {"approved_by_id": user.id}, {"forecast_run_id": False}):
                with self.subTest(user=user.login, vals=vals), self.assertRaisesRegex(AccessError, "readonly"):
                    shadow.with_user(user).write(vals)
        self.assertEqual(shadow.state, "shadow")
        with self.assertRaisesRegex(UserError, "state"):
            shadow.with_user(planner).action_efn_apply()
        with self.assertRaisesRegex(AccessError, "readonly"):
            self.env["efn.demand.forecast"].with_user(clerk).create(
                {"warehouse_id": self.wh.id, "product_id": self.product.id, "state": "applied"}
            )
        # Field non-status tetap bisa disesuaikan planner.
        shadow.with_user(planner).write({"approved_max": 45})
        self.assertEqual(shadow.approved_max, 45)


@tagged("post_install", "-at_install", "efn")
class TestEfnPaydaySeason(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["efn.forecast.season"].search([]).active = False
        cls.product = cls.env["product.product"].create({"name": "Beras (uji)", "is_storable": True})
        cls.Season = cls.env["efn.forecast.season"]
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("efn_forecast.payday_tail_days", "7")
        icp.set_param("efn_forecast.payday_head_days", "7")

    def test_default_payday_7_7_follows_month_length(self):
        season = self.Season.create({"name": "Gajian uji", "kind": "payday", "factor": 1.5})
        hits = {
            date(2026, 11, 23): False,  # Nov = 30 hari: 7 hari terakhir = 24..30
            date(2026, 11, 24): True,
            date(2026, 11, 30): True,
            date(2026, 12, 1): True,
            date(2026, 12, 7): True,
            date(2026, 12, 8): False,
            date(2027, 2, 21): False,  # Feb 2027 = 28 hari: 22..28
            date(2027, 2, 22): True,
        }
        for day, expected in hits.items():
            with self.subTest(day=day):
                self.assertEqual(season._efn_applies(day, self.product), expected)
        self.assertAlmostEqual(self.Season._efn_factor(season, date(2026, 11, 25), self.product), 1.5)
        self.assertAlmostEqual(self.Season._efn_factor(season, date(2026, 11, 15), self.product), 1.0)

    def test_payday_param_per_company_and_invalid_fallback(self):
        company = self.env.company
        icp = self.env["ir.config_parameter"].sudo()
        season = self.Season.create({"name": "Gajian uji", "kind": "payday", "factor": 1.2})
        if company.efn_brand_code:
            icp.set_param(f"efn_forecast.payday_tail_days.{company.efn_brand_code}", "3")
            self.assertFalse(season._efn_applies(date(2026, 11, 27), self.product))
            self.assertTrue(season._efn_applies(date(2026, 11, 28), self.product))
            icp.set_param(f"efn_forecast.payday_tail_days.{company.efn_brand_code}", "")
        icp.set_param("efn_forecast.payday_tail_days", "abc")
        self.assertEqual(self.Season._efn_payday_window(company), (7, 7))

    def test_default_season_data_is_payday(self):
        season = self.env.ref("efn_demand_forecast.season_payday").with_context(active_test=False)
        self.assertEqual(season.kind, "payday")

    def test_holiday_window_uses_calendar(self):
        company = self.env.company
        self.env["efn.forecast.calendar"].create(
            {"company_id": company.id, "date": "2027-03-10", "kind": "lebaran_window", "name": "Lebaran uji"}
        )
        season = self.Season.create(
            {
                "name": "Jelang Lebaran",
                "kind": "holiday",
                "calendar_kind": "lebaran_window",
                "days_before": 3,
                "days_after": 1,
                "factor": 1.8,
            }
        )
        self.assertFalse(season._efn_applies(date(2027, 3, 6), self.product))
        self.assertTrue(season._efn_applies(date(2027, 3, 7), self.product))
        self.assertTrue(season._efn_applies(date(2027, 3, 11), self.product))
        self.assertFalse(season._efn_applies(date(2027, 3, 12), self.product))
