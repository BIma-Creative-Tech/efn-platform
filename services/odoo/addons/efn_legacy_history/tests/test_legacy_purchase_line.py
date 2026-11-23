"""efn.legacy.purchase.line (G65, docs/04 §27.13.6): arsip read-only, irisan company × bulan tanpa gudang, rule
multi-company, ACL baca purchase.group_purchase_user. Data sintetis."""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_legacy_history import bulk

MODEL = "efn.legacy.purchase.line"


@tagged("post_install", "-at_install", "efn")
class TestEfnLegacyPurchaseLine(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pb = cls._company("PB")
        cls.wtz = cls._company("WTZ")
        Warehouse = cls.env["stock.warehouse"]
        cls.wh_pb = Warehouse.with_company(cls.pb).create(
            {"name": "Gudang beli PB", "code": "LPB1", "company_id": cls.pb.id}
        )
        cls.wh_wtz = Warehouse.with_company(cls.wtz).create(
            {"name": "Gudang beli WTZ", "code": "LPW1", "company_id": cls.wtz.id}
        )
        Partner = cls.env["res.partner"]
        cls.sup_pb = Partner.create({"name": "PT Pemasok uji PB", "is_company": True, "company_id": cls.pb.id})
        cls.sup_wtz = Partner.create({"name": "PT Pemasok uji WTZ", "is_company": True, "company_id": cls.wtz.id})
        cls.product = cls.env["product.product"].create(
            {"name": "Tepung legacy (uji)", "type": "consu", "is_storable": True}
        )
        pb_only = {"company_id": cls.pb.id, "company_ids": [Command.set(cls.pb.ids)]}
        cls.buyer = new_test_user(cls.env, login="efn_lp_buyer", groups="purchase.group_purchase_user", **pb_only)
        cls.stock_user = new_test_user(cls.env, login="efn_lp_stock", groups="stock.group_stock_user", **pb_only)
        cls.plain = new_test_user(cls.env, login="efn_lp_plain", groups="base.group_user", **pb_only)
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        for company in (cls.pb, cls.wtz):
            cls.ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + company.efn_brand_code, "2026-12-01")

    @classmethod
    def _company(cls, code):
        company = cls.env["res.company"].search([("efn_brand_code", "=", code)], limit=1)
        return company or cls.env["res.company"].create({"name": f"PT uji beli {code}", "efn_brand_code": code})

    def _imp(self):
        return self.env[MODEL].sudo().with_context(efn_legacy_import=True)

    def _row(self, day, num="PO-0001", line_no=1, **vals):
        row = {
            "date": day,
            "purchase_num": num,
            "line_no": line_no,
            "esb_product_code": "RM001",
            "product_name": "Tepung",
            "branch_name": "Toko Uji",
            "supplier_name": "PT Pemasok",
            "uom_name": "KG",
            "qty": "10",
            "receipt_qty": "10",
            "price_unit": "12000",
            "discount": "0",
            "vat_value": "13200",
            "total": "133200",
            "status": "Finished",
            "map_source": "header",
        }
        row.update(vals)
        return row

    def _slice(self, company, rows, date_from="2026-10-01", date_to="2026-11-01", **kwargs):
        return self._imp()._efn_bulk_replace_slice(company.id, date_from, date_to, rows, **kwargs)

    def test_replace_company_month_slice_idempotent(self):
        rows = [
            self._row(date(2026, 10, 3), warehouse_id=self.wh_pb.id, supplier_id=self.sup_pb.id,
                      product_id=self.product.id),
            self._row(date(2026, 10, 3), line_no=2, map_source="none", branch_name="HO"),  # tanpa gudang/supplier
            self._row(date(2026, 10, 20), num="PO-0002", date_required="2026-10-22"),
        ]  # fmt: skip
        self._slice(self.wtz, [self._row(date(2026, 10, 3))])
        first = self._slice(self.pb, rows)
        self.assertEqual(first, {"rows_deleted": 0, "rows_inserted": 3})
        second = self._slice(self.pb, rows)
        self.assertEqual(second, {"rows_deleted": 3, "rows_inserted": 3})
        Line = self._imp()
        self.assertEqual(Line.search_count([("company_id", "=", self.pb.id)]), 3)
        self.assertEqual(Line.search_count([("company_id", "=", self.wtz.id)]), 1, "irisan PT lain tidak tersentuh")
        ho = Line.search([("company_id", "=", self.pb.id), ("line_no", "=", 2)])
        self.assertFalse(ho.warehouse_id)
        self.assertFalse(ho.supplier_id)
        self.assertFalse(ho.date_required)
        self.assertEqual(ho.map_source, "none")
        full = Line.search([("company_id", "=", self.pb.id), ("purchase_num", "=", "PO-0001"), ("line_no", "=", 1)])
        self.assertEqual((full.warehouse_id, full.supplier_id), (self.wh_pb, self.sup_pb))
        self.assertAlmostEqual(full.total, 133200)
        # Irisan kosong = hapus irisan company × rentang itu saja.
        empty = self._slice(self.pb, [], date_from="2026-10-15")
        self.assertEqual(empty, {"rows_deleted": 1, "rows_inserted": 0})

    def test_slice_rejects(self):
        day = date(2026, 10, 3)
        cases = [
            (ValidationError, "gudang", [self._row(day, warehouse_id=self.wh_wtz.id)]),
            (ValidationError, "supplier milik company lain", [self._row(day, supplier_id=self.sup_wtz.id)]),
            (ValidationError, "irisan tanggal", [self._row(date(2026, 11, 3))]),
            (ValidationError, "di luar irisan", [self._row(day, company_id=self.wtz.id)]),
            (ValidationError, "pilihan", [self._row(day, map_source="x")]),
            (ValidationError, "kolom tidak dikenal", [self._row(day, supplier_email="x@y")]),
        ]
        for exc, reason, rows in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(exc, reason):
                self._slice(self.pb, rows)
        with self.assertRaisesRegex(UserError, "warehouse_id=None"):
            bulk.bulk_replace_slice(self._imp(), self.pb.id, self.wh_pb.id, "2026-10-01", "2026-11-01", [])
        # Batas cutover parameter: baris >= cutover ditolak; shift_cutover (importer, non-production) memakai max_day.
        self.ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + "PB", "2026-11-17")
        late = [self._row(date(2026, 11, 20))]
        with self.assertRaisesRegex(ValidationError, "cutover"):
            self._slice(self.pb, late, "2026-11-01", "2026-12-01", max_day="2026-12-01")
        stats = self._slice(self.pb, late, "2026-11-01", "2026-12-01", max_day="2026-12-01", shift_cutover=True)
        self.assertEqual(stats["rows_inserted"], 1)
        with self.assertRaisesRegex(UserError, "shift_cutover"):
            self._slice(self.pb, late, "2026-11-01", "2026-12-01", shift_cutover=True)

    def test_read_only_rules_and_acl(self):
        self._slice(self.pb, [self._row(date(2026, 10, 3))])
        self._slice(self.wtz, [self._row(date(2026, 10, 3))])
        visible = self.env[MODEL].with_user(self.buyer).search([])
        self.assertEqual(visible.company_id, self.pb, "rule multi-company: hanya PT user")
        wtz_rec = self.env[MODEL].sudo().search([("company_id", "=", self.wtz.id)])
        with self.assertRaises(AccessError):
            wtz_rec.with_user(self.buyer).read(["total"])
        with self.assertRaises(AccessError):
            visible.with_user(self.buyer).write({"qty": 5})
        with self.assertRaises(AccessError):
            visible.with_user(self.buyer).unlink()
        with self.assertRaisesRegex(AccessError, "read_only"):
            visible.sudo().write({"qty": 5})
        with self.assertRaisesRegex(AccessError, "read_only"):
            visible.sudo().copy()
        for user in (self.stock_user, self.plain):
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                self.env[MODEL].with_user(user).search([])
        menu = self.env.ref("efn_legacy_history.menu_efn_legacy_purchase_line")
        self.assertEqual(menu.parent_id, self.env.ref("purchase.menu_purchase_root"))
