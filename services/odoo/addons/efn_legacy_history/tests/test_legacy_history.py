"""efn_legacy_history (G43 W2, ADR-042, docs/04 §27.8/§27.8a v19.1): read-only, multi-company, bulk upsert & ganti
irisan idempoten, parameter, hook forecast. Data sintetis (tanpa data klien)."""

from datetime import date, timedelta
from decimal import Decimal

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_legacy_history import bulk

RUN_1 = "00000000-0000-4000-8000-000000000001"
RUN_2 = "00000000-0000-4000-8000-000000000002"
MODELS = ("efn.legacy.flow.daily", "efn.legacy.sales.daily", "efn.legacy.sales.line")


@tagged("post_install", "-at_install", "efn")
class TestEfnLegacyHistory(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["efn.forecast.season"].search([]).active = False
        cls.pb = cls._company("PB")
        cls.wtz = cls._company("WTZ")
        Warehouse = cls.env["stock.warehouse"]
        cls.wh_pb = Warehouse.with_company(cls.pb).create(
            {"name": "Outlet legacy PB", "code": "LGPB", "company_id": cls.pb.id}
        )
        cls.wh_pb2 = Warehouse.with_company(cls.pb).create(
            {"name": "Outlet legacy PB 2", "code": "LGPB2", "company_id": cls.pb.id}
        )
        cls.wh_wtz = Warehouse.with_company(cls.wtz).create(
            {"name": "Outlet legacy WTZ", "code": "LGWZ", "company_id": cls.wtz.id}
        )
        Product = cls.env["product.product"]
        cls.product = Product.create({"name": "Roti legacy (uji)", "type": "consu", "is_storable": True})
        cls.product_wtz = Product.create(
            {"name": "Pretzel legacy (uji)", "type": "consu", "is_storable": True, "company_id": cls.wtz.id}
        )
        cls.customer = cls.env.ref("stock.stock_location_customers")
        cls.today = date(2026, 11, 2)
        pb_only = {"company_id": cls.pb.id, "company_ids": [Command.set(cls.pb.ids)]}
        # POS Manager meng-imply group_legacy_sales_reader (docs/04 §27.8).
        cls.pb_manager = new_test_user(
            cls.env,
            login="efn_lg_pb_mgr",
            groups="stock.group_stock_manager,point_of_sale.group_pos_manager",
            **pb_only,
        )
        cls.pb_stock_user = new_test_user(cls.env, login="efn_lg_pb_stock", groups="stock.group_stock_user", **pb_only)
        cls.plain = new_test_user(cls.env, login="efn_lg_plain", groups="base.group_user", **pb_only)
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        for company in (cls.pb, cls.wtz):
            cls.ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + company.efn_brand_code, False)

    @classmethod
    def _company(cls, code):
        company = cls.env["res.company"].search([("efn_brand_code", "=", code)], limit=1)
        return company or cls.env["res.company"].create({"name": f"PT uji legacy {code}", "efn_brand_code": code})

    # ------------------------------------------------------------------ helper
    def _imp(self, model):
        return self.env[model].sudo().with_context(efn_legacy_import=True)

    def _flow_row(self, wh, day, product=None, **vals):
        row = {
            "day": day,
            "company_id": wh.company_id.id,
            "warehouse_id": wh.id,
            "product_id": (product or self.product).id,
            "esb_brand": "pb",
            "esb_branch_id": 7,
            "esb_product_code": "FIN_001",
            "esb_product_name": "Roti uji",
        }
        row.update(vals)
        return row

    def _sales_daily_row(self, wh, day, menu="M001", **vals):
        row = {
            "day": day,
            "company_id": wh.company_id.id,
            "warehouse_id": wh.id,
            "esb_brand": "pb",
            "esb_branch_id": 7,
            "esb_menu_code": menu,
            "menu_name": "Menu uji",
            "qty": "3",
            "gross": "75000",
            "discount": "5000.50",
            "cogs": "21000",
            "trx_count": 2,
        }
        row.update(vals)
        return row

    def _line_row(self, day, sales_num="S-0001", line_no=1, **vals):
        row = {
            "date": day,
            "sales_num": sales_num,
            "line_no": line_no,
            "esb_menu_code": "M001",
            "row_type": "ALACARTE",
            "qty": 1,
            "price": "25000",
            "total": "25000",
            "discount": 0,
            "cogs": "7000",
        }
        row.update(vals)
        return row

    def _set_cutover(self, company, value):
        self.ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + company.efn_brand_code, value)

    def _upsert(self, model, rows, run_id=RUN_1, companies=None, **kwargs):
        companies = companies or (self.pb | self.wtz)
        return self._imp(model)._efn_bulk_upsert(rows, run_id, companies.ids, **kwargs)

    def _sell(self, wh, day, qty):
        env = self.env(context=dict(self.env.context, allowed_company_ids=wh.company_id.ids))
        env["stock.quant"]._update_available_quantity(self.product, wh.lot_stock_id, qty)
        move = env["stock.move"].create(
            {
                "product_id": self.product.id,
                "product_uom_qty": qty,
                "location_id": wh.lot_stock_id.id,
                "location_dest_id": self.customer.id,
                "company_id": wh.company_id.id,
            }
        )
        move._action_confirm()
        move._action_assign()
        move.quantity = qty
        move.picked = True
        move._action_done()
        move.date = f"{day} 05:00:00"

    # ------------------------------------------------------------------ parameter
    def test_parameter_defaults(self):
        self.assertEqual(self.ICP.get_param(bulk.PARAM_SALES_LINE_MONTHS), "24")
        self.assertEqual(self.ICP.get_param(bulk.PARAM_LEGACY_DAYS), "400")
        self.assertEqual(bulk.get_sales_line_months(self.env), 24)
        self.ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, False)
        self.assertEqual(bulk.get_sales_line_months(self.env), 24)
        self.ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, "0")
        self.assertEqual(bulk.get_sales_line_months(self.env), 0)
        self.assertIsNone(bulk.sales_line_window(self.env, self.pb))
        self.ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, "abc")
        self.assertEqual(bulk.get_sales_line_months(self.env), 24)
        self.ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, "-3")
        self.assertEqual(bulk.get_sales_line_months(self.env), 24)
        self.ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, "999")
        self.assertEqual(bulk.get_sales_line_months(self.env), bulk.MAX_SALES_LINE_MONTHS)

    def test_cutover_parameter_and_sales_window(self):
        self.assertIsNone(bulk.get_cutover_date(self.env, self.pb))
        self._set_cutover(self.pb, "2027-01-15")
        self.assertEqual(bulk.get_cutover_date(self.env, self.pb), date(2027, 1, 15))
        self.assertIsNone(bulk.get_cutover_date(self.env, self.wtz))
        # [awal bulan(cutover) - N bulan, cutover); manifest menurunkan N (min).
        self.assertEqual(bulk.sales_line_window(self.env, self.pb), (date(2025, 1, 1), date(2027, 1, 15)))
        self.assertEqual(bulk.sales_line_window(self.env, self.pb, months=12), (date(2026, 1, 1), date(2027, 1, 15)))
        self.assertIsNone(bulk.sales_line_window(self.env, self.pb, months=0))
        for bad in ("15/01/2027", "2027-02-30", "besok"):
            self._set_cutover(self.pb, bad)
            self.assertIsNone(bulk.get_cutover_date(self.env, self.pb), bad)

    # ------------------------------------------------------------------ bulk upsert (flow/sales.daily)
    def test_bulk_upsert_idempotent(self):
        Flow = self._imp("efn.legacy.flow.daily")
        rows = [
            self._flow_row(self.wh_pb, self.today - timedelta(days=i), sold=10, st_out="2.5", ck_out=1, in_ck=4)
            for i in range(1, 4)
        ]
        first = self._upsert("efn.legacy.flow.daily", rows, batch_size=2)
        self.assertEqual((first["inserted"], first["updated"], first["unchanged"], first["rows"]), (3, 0, 0, 3))
        second = self._upsert("efn.legacy.flow.daily", iter(rows), run_id=RUN_2)
        self.assertEqual((second["inserted"], second["updated"], second["unchanged"]), (0, 0, 3))
        recs = Flow.search([("warehouse_id", "=", self.wh_pb.id)])
        self.assertEqual(len(recs), 3)
        self.assertEqual(set(recs.mapped("outflow")), {13.5})
        self.assertEqual(set(recs.mapped("run_id")), {RUN_1}, "baris sama persis tidak ditulis ulang")
        self.assertEqual(recs.create_uid, self.env.user, "kolom log diisi SQL bulk")
        # Nilai absolut: baris berubah -> ditimpa (bukan dijumlah), run_id ikut berganti.
        rows[0]["sold"] = 20
        third = self._upsert("efn.legacy.flow.daily", rows, run_id=RUN_2)
        self.assertEqual((third["inserted"], third["updated"], third["unchanged"]), (0, 1, 2))
        changed = recs.filtered(lambda r: r.day == self.today - timedelta(days=1))
        self.assertEqual(changed.sold, 20)
        self.assertEqual(changed.outflow, 23.5)
        self.assertEqual(changed.run_id, RUN_2)
        self.assertEqual(Flow.search_count([("warehouse_id", "=", self.wh_pb.id)]), 3)

    def test_bulk_requires_sudo_and_import_context(self):
        rows = [self._flow_row(self.wh_pb, self.today - timedelta(days=1), sold=1)]
        Flow = self.env["efn.legacy.flow.daily"]
        with self.assertRaisesRegex(AccessError, "read_only"):
            Flow.sudo()._efn_bulk_upsert(rows, RUN_1, self.pb.ids)  # su tanpa konteks
        with self.assertRaisesRegex(AccessError, "read_only"):
            Flow.with_user(self.pb_manager).with_context(efn_legacy_import=True)._efn_bulk_upsert(
                rows, RUN_1, self.pb.ids
            )
        with self.assertRaisesRegex(AccessError, "read_only"):
            self.env["efn.legacy.sales.line"].sudo()._efn_bulk_replace_slice(
                self.pb.id, self.wh_pb.id, "2026-10-01", "2026-11-01", []
            )
        self.assertFalse(Flow.sudo().search_count([]))

    def test_bulk_upsert_rejects_invalid_rows(self):
        day = self.today - timedelta(days=1)
        cases = [
            ("target", self._flow_row(self.wh_wtz, day), self.pb),
            ("gudang", self._flow_row(self.wh_wtz, day, company_id=self.pb.id), None),
            ("produk", self._flow_row(self.wh_pb, day, product=self.product_wtz), None),
            ("tidak dikenal", self._flow_row(self.wh_pb, day, outflow=5), None),
            ("wajib", self._flow_row(self.wh_pb, day, esb_product_code=""), None),
            ("YYYY-MM-DD", self._flow_row(self.wh_pb, "02/11/2026"), None),
            ("angka", self._flow_row(self.wh_pb, day, sold="sepuluh"), None),
            ("NaN", self._flow_row(self.wh_pb, day, sold=float("nan")), None),
            ("id record", self._flow_row(self.wh_pb, day, warehouse_id=str(self.wh_pb.id)), None),
        ]
        for reason, row, companies in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ValidationError, reason):
                self._upsert("efn.legacy.flow.daily", [row], companies=companies)
        with self.assertRaisesRegex(ValidationError, "ganda"):
            self._upsert("efn.legacy.flow.daily", [self._flow_row(self.wh_pb, day), self._flow_row(self.wh_pb, day)])
        with self.assertRaisesRegex(UserError, "run_id"):
            self._upsert("efn.legacy.flow.daily", [self._flow_row(self.wh_pb, day)], run_id="run-1")
        with self.assertRaisesRegex(UserError, "company_ids"):
            self._imp("efn.legacy.flow.daily")._efn_bulk_upsert([self._flow_row(self.wh_pb, day)], RUN_1, [])
        with self.assertRaisesRegex(UserError, "no_key"):
            bulk.bulk_upsert(self._imp("efn.legacy.sales.line"), [], RUN_1, company_ids=self.pb.ids)
        # Hari >= cutover company (parameter) atau >= max_day ditolak (histori hanya sebelum cutover).
        self._set_cutover(self.pb, str(day))
        with self.assertRaisesRegex(ValidationError, "cutover"):
            self._upsert("efn.legacy.flow.daily", [self._flow_row(self.wh_pb, day)])
        self._set_cutover(self.pb, False)
        with self.assertRaisesRegex(ValidationError, "cutover"):
            self._upsert("efn.legacy.flow.daily", [self._flow_row(self.wh_pb, day)], max_day=str(day))
        self.assertFalse(self._imp("efn.legacy.flow.daily").search_count([]))

    def test_sales_daily_upsert_net_and_unmapped_product(self):
        day = self.today - timedelta(days=2)
        rows = [
            self._sales_daily_row(self.wh_pb, day),
            self._sales_daily_row(
                self.wh_pb, day, menu="M001 T", is_takeaway=True, product_id=self.product.id, net="70000"
            ),
        ]
        self.assertEqual(self._upsert("efn.legacy.sales.daily", rows)["inserted"], 2)
        self.assertEqual(self._upsert("efn.legacy.sales.daily", rows)["unchanged"], 2)
        recs = self._imp("efn.legacy.sales.daily").search([("warehouse_id", "=", self.wh_pb.id)])
        self.assertEqual(len(recs), 2)
        base = recs.filtered(lambda r: r.esb_menu_code == "M001")
        self.assertFalse(base.product_id, "menu tak terpetakan tetap tersimpan dengan kode ESB")
        self.assertEqual(base.net, 69999.5, "net kosong -> gross - discount")
        self.assertEqual(base.currency_id, self.pb.currency_id)
        takeaway = recs.filtered(lambda r: r.esb_menu_code == "M001 T")
        self.assertTrue(takeaway.is_takeaway)
        self.assertEqual(takeaway.net, 70000, "net dari export disimpan apa adanya")
        # pos_config_id divalidasi milik company baris (id tak ada -> ditolak; tanpa membuat pos.config di test).
        with self.assertRaisesRegex(ValidationError, "kasir"):
            self._upsert("efn.legacy.sales.daily", [self._sales_daily_row(self.wh_pb, day, pos_config_id=2**31 - 1)])

    # ------------------------------------------------------------------ ganti irisan (sales.line)
    def test_sales_line_replace_slice_idempotent(self):
        Line = self._imp("efn.legacy.sales.line")
        self._set_cutover(self.pb, str(self.today))
        rows = [
            self._line_row(date(2026, 10, 5), line_no=1),
            self._line_row(date(2026, 10, 5), line_no=2, row_type="PACKAGE_HEADER", product_id=self.product.id),
            self._line_row(date(2026, 10, 20), sales_num="S-0002"),
        ]
        other = Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb2.id, "2026-10-01", "2026-11-01", rows[:1])
        self.assertEqual(other, {"rows_deleted": 0, "rows_inserted": 1})
        first = Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb.id, "2026-10-01", "2026-11-01", rows)
        self.assertEqual(first, {"rows_deleted": 0, "rows_inserted": 3})
        second = Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb.id, date(2026, 10, 1), date(2026, 11, 1), rows)
        self.assertEqual(second, {"rows_deleted": 3, "rows_inserted": 3})
        self.assertEqual(Line.search_count([("warehouse_id", "=", self.wh_pb.id)]), 3)
        self.assertEqual(Line.search_count([("warehouse_id", "=", self.wh_pb2.id)]), 1, "irisan lain tidak tersentuh")
        # Irisan kosong = hapus irisan itu saja.
        third = Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb.id, "2026-10-15", "2026-11-01", [])
        self.assertEqual(third, {"rows_deleted": 1, "rows_inserted": 0})
        self.assertEqual(Line.search_count([("company_id", "=", self.pb.id)]), 3)

    def test_sales_line_replace_slice_rejects(self):
        Line = self._imp("efn.legacy.sales.line")
        self._set_cutover(self.pb, str(self.today))
        ok = [self._line_row(date(2026, 10, 5))]
        Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb.id, "2026-10-01", "2026-11-01", ok)
        october = (self.pb.id, self.wh_pb.id, "2026-10-01", "2026-11-01")
        other_wh = [self._line_row(date(2026, 10, 5), warehouse_id=self.wh_pb2.id)]
        bad_type = [self._line_row(date(2026, 10, 5), row_type="X")]
        wtz_product = [self._line_row(date(2026, 10, 5), product_id=self.product_wtz.id)]
        cases = [
            (ValidationError, "irisan tanggal", (self.pb.id, self.wh_pb.id, "2026-10-01", "2026-10-05", ok)),
            (ValidationError, "di luar irisan", (*october, other_wh)),
            (UserError, "gudang", (self.pb.id, self.wh_wtz.id, "2026-10-01", "2026-11-01", [])),
            (ValidationError, "pilihan", (*october, bad_type)),
            (ValidationError, "produk", (*october, wtz_product)),
            (UserError, "slice_window", (self.pb.id, self.wh_pb.id, "2024-10-01", "2024-11-01", [])),
            (UserError, "date_from", (self.pb.id, self.wh_pb.id, "2026-11-01", "2026-10-01", [])),
        ]
        for exc, reason, args in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(exc, reason):
                Line._efn_bulk_replace_slice(*args)
        # Baris salah menolak irisan utuh: data lama tetap.
        self.assertEqual(Line.search_count([("warehouse_id", "=", self.wh_pb.id)]), 1)
        # Nilai manifest lebih kecil dari parameter -> rentang efektif menyempit (min).
        with self.assertRaisesRegex(UserError, "slice_window"):
            Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb.id, "2025-10-01", "2025-11-01", [], sales_line_months=6)
        self.ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, "0")
        with self.assertRaisesRegex(UserError, "sales_line_disabled"):
            Line._efn_bulk_replace_slice(self.pb.id, self.wh_pb.id, "2026-10-01", "2026-11-01", ok)

    # ------------------------------------------------------------------ read-only & multi-company
    def _seed_both_companies(self):
        day = self.today - timedelta(days=1)
        self._upsert(
            "efn.legacy.flow.daily",
            [self._flow_row(self.wh_pb, day, sold=1), self._flow_row(self.wh_wtz, day, sold=2, esb_brand="wtz")],
        )
        self._upsert(
            "efn.legacy.sales.daily",
            [self._sales_daily_row(self.wh_pb, day), self._sales_daily_row(self.wh_wtz, day, esb_brand="wtz")],
        )
        Line = self._imp("efn.legacy.sales.line")
        for wh in (self.wh_pb, self.wh_wtz):
            Line._efn_bulk_replace_slice(
                wh.company_id.id, wh.id, day.replace(day=1), self.today, [self._line_row(day)], max_day=self.today
            )

    def test_orm_writes_rejected(self):
        self._seed_both_companies()
        for model in MODELS:
            with self.subTest(model=model):
                rec = self.env[model].sudo().search([("company_id", "=", self.pb.id)], limit=1)
                self.assertTrue(rec)
                as_mgr = rec.with_user(self.pb_manager)
                with self.assertRaises(AccessError):
                    as_mgr.write({"qty" if model != "efn.legacy.flow.daily" else "sold": 5})
                with self.assertRaises(AccessError):
                    as_mgr.unlink()
                with self.assertRaises(AccessError):
                    self.env[model].with_user(self.pb_manager).create({"company_id": self.pb.id})
                # Superuser tanpa konteks migrasi juga ditolak.
                with self.assertRaisesRegex(AccessError, "read_only"):
                    rec.sudo().write({"company_id": self.pb.id})
                with self.assertRaisesRegex(AccessError, "read_only"):
                    rec.sudo().unlink()
                with self.assertRaisesRegex(AccessError, "read_only"):
                    rec.sudo().copy()
        # Konteks migrasi + sudo: boleh lewat ORM; outflow diisi ulang.
        flow = self._imp("efn.legacy.flow.daily").search([("company_id", "=", self.pb.id)], limit=1)
        flow.write({"sold": 9})
        self.assertEqual(flow.outflow, 9)

    def test_multi_company_record_rules(self):
        self._seed_both_companies()
        for model in MODELS:
            with self.subTest(model=model):
                self.assertEqual(self.env[model].sudo().search_count([]), 2)
                visible = self.env[model].with_user(self.pb_manager).search([])
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible.company_id, self.pb)
                wtz_rec = self.env[model].sudo().search([("company_id", "=", self.wtz.id)])
                with self.assertRaises(AccessError):
                    wtz_rec.with_user(self.pb_manager).read(["company_id"])

    def test_group_access(self):
        self._seed_both_companies()
        self.assertTrue(self.pb_manager.has_group("efn_legacy_history.group_legacy_sales_reader"))
        # Pengguna stok biasa: baca aliran (forecast), bukan penjualan.
        self.assertEqual(len(self.env["efn.legacy.flow.daily"].with_user(self.pb_stock_user).search([])), 1)
        for model in ("efn.legacy.sales.daily", "efn.legacy.sales.line"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(self.pb_stock_user).search([])
        for model in MODELS:
            with self.subTest(model=model, user="plain"), self.assertRaises(AccessError):
                self.env[model].with_user(self.plain).search([])

    # ------------------------------------------------------------------ hook forecast (docs/04 §27.8)
    def _forecast_env(self, company):
        return self.env["efn.demand.forecast"].with_company(company).with_context(tz="Asia/Jakarta")

    def _seed_legacy(self, wh, days, qty):
        rows = [self._flow_row(wh, self.today - timedelta(days=i), sold=qty) for i in days]
        self._upsert("efn.legacy.flow.daily", rows)

    def test_forecast_hook_boundary_mid_range(self):
        boundary = self.today - timedelta(days=3)
        self._seed_legacy(self.wh_pb, range(4, 15), Decimal("7"))
        for i in range(1, 8):
            self._sell(self.wh_pb, self.today - timedelta(days=i), 5)  # stock sebelum batas diabaikan
        self._set_cutover(self.pb, str(boundary))
        Forecast = self._forecast_env(self.pb)
        d0, d1 = self.today - timedelta(days=7), self.today - timedelta(days=1)
        got_boundary, legacy = Forecast._efn_legacy_outflow(self.wh_pb, self.product, d0, d1)
        self.assertEqual(got_boundary, boundary)
        self.assertEqual(sorted(legacy), [self.today - timedelta(days=i) for i in range(7, 3, -1)])
        merged = Forecast._efn_daily_outflow(self.wh_pb, self.product, d0, d1)
        for i in range(1, 8):
            day = self.today - timedelta(days=i)
            self.assertAlmostEqual(merged.get(day, 0.0), 7.0 if day < boundary else 5.0, msg=str(day))
        fc = Forecast._efn_generate(
            self.wh_pb, self.product, date=self.today, history_days=7, lead_days=1, review_days=0
        )
        self.assertAlmostEqual(fc.history_qty, 4 * 7 + 3 * 5)

    def test_forecast_hook_range_before_and_after_boundary(self):
        self._seed_legacy(self.wh_pb, range(1, 8), 6)
        for i in range(1, 8):
            self._sell(self.wh_pb, self.today - timedelta(days=i), 2)
        Forecast = self._forecast_env(self.pb)
        d0, d1 = self.today - timedelta(days=7), self.today - timedelta(days=1)
        # Seluruh rentang sebelum batas -> hanya legacy.
        self._set_cutover(self.pb, str(self.today + timedelta(days=10)))
        merged = Forecast._efn_daily_outflow(self.wh_pb, self.product, d0, d1)
        self.assertAlmostEqual(sum(merged.values()), 7 * 6)
        # Seluruh rentang sesudah batas -> hanya stock.move (legacy tidak dibaca).
        self._set_cutover(self.pb, str(self.today - timedelta(days=30)))
        merged = Forecast._efn_daily_outflow(self.wh_pb, self.product, d0, d1)
        self.assertAlmostEqual(sum(merged.values()), 7 * 2)

    def test_forecast_hook_company_without_parameter_and_bad_format(self):
        self._seed_legacy(self.wh_wtz, range(1, 8), 9)
        self._seed_legacy(self.wh_pb, range(1, 8), 9)
        for i in range(1, 8):
            self._sell(self.wh_wtz, self.today - timedelta(days=i), 3)
            self._sell(self.wh_pb, self.today - timedelta(days=i), 3)
        d0, d1 = self.today - timedelta(days=7), self.today - timedelta(days=1)
        # PB punya cutover, WTZ tidak: WTZ = perilaku lama persis (stock.move saja), PB = legacy.
        self._set_cutover(self.pb, str(self.today))
        wtz_forecast = self._forecast_env(self.wtz)
        self.assertEqual(wtz_forecast._efn_legacy_outflow(self.wh_wtz, self.product, d0, d1), (None, {}))
        self.assertAlmostEqual(sum(wtz_forecast._efn_daily_outflow(self.wh_wtz, self.product, d0, d1).values()), 21)
        pb_forecast = self._forecast_env(self.pb)
        self.assertAlmostEqual(sum(pb_forecast._efn_daily_outflow(self.wh_pb, self.product, d0, d1).values()), 63)
        # Format salah -> fail safe ke perilaku lama.
        self._set_cutover(self.pb, "besok")
        self.assertEqual(pb_forecast._efn_legacy_outflow(self.wh_pb, self.product, d0, d1), (None, {}))
        self.assertAlmostEqual(sum(pb_forecast._efn_daily_outflow(self.wh_pb, self.product, d0, d1).values()), 21)
