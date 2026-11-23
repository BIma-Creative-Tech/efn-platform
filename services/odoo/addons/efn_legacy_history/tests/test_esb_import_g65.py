"""Importer ESB -> Odoo, tambahan G65 (docs/04 §27.13.4–27.13.8, fit-gap G65): supplierinfo, cost_as_of, stok awal
(`stock`, quant absolut + tanggal cutover − 1), histori pembelian (irisan company × bulan), geser cutover.

Company uji BARU (`ZTS`) + record & xmlid dibuat langsung (tanpa impor master) agar gerbang `stock` (stock.move /
sesi POS PT) tidak terpengaruh data demo PB. Bundel SINTETIS, kunci uji acak per kelas."""

import secrets
import uuid
from datetime import datetime

from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_legacy_history import bulk
from odoo.addons.efn_legacy_history.esb_import import importer as imp
from odoo.addons.efn_legacy_history.esb_import.efn_mig.bundle import (
    IntegrityError,
    build_bundle,
    derive_keys,
    file_entry,
    gzip_bytes,
    jsonl_bytes,
    open_bundle,
)
from odoo.addons.efn_legacy_history.esb_import.efn_mig.xmlid import xmlid

BRAND = "zzt"
CODE = "BCF"  # kode Selection valid, bukan PB/WTZ (test G43) dan bukan IMD/CUP (saldo_blocked default)
CUTOVER = "2026-12-01"
CREATED = "2026-12-02T03:00:00.000Z"


def X(entity, code):  # noqa: N802
    return xmlid(BRAND, CODE, entity, code)


@tagged("post_install", "-at_install", "efn")
class TestEsbImportG65(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        secret = secrets.token_hex(32)
        cls.keys = derive_keys(secret)
        cls.val_key = imp.derive_validation_key(secret)
        Company = cls.env["res.company"]
        cls.company = Company.search([("efn_brand_code", "=", CODE)], limit=1) or Company.create(
            {"name": "PT uji G65", "efn_brand_code": CODE}
        )
        cls.mig_user = new_test_user(cls.env, login="efn_mig_g65_test", groups="base.group_user")
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + CODE, False)
        cls.ICP.set_param(imp.PARAM_COST_AS_OF_PREFIX + CODE, False)
        Warehouse = cls.env["stock.warehouse"].with_company(cls.company)
        cls.wh1 = Warehouse.create({"name": "ZT Toko 1", "code": "ZS1", "company_id": cls.company.id})
        cls.wh2 = Warehouse.create({"name": "ZT Toko 2", "code": "ZS2", "company_id": cls.company.id})
        cls.sub = cls.env["stock.location"].create(
            {"name": "ZT Gudang Kering", "usage": "internal", "location_id": cls.wh1.lot_stock_id.id,
             "company_id": cls.company.id}
        )  # fmt: skip
        Template = cls.env["product.template"]
        common = {"type": "consu", "company_id": cls.company.id}
        cls.fin = Template.create({**common, "name": "ZT Roti", "is_storable": True,
                                   "uom_id": cls.env.ref("uom.product_uom_unit").id})  # fmt: skip
        cls.rm = Template.create({**common, "name": "ZT Tepung", "is_storable": True,
                                  "uom_id": cls.env.ref("uom.product_uom_kgm").id})  # fmt: skip
        cls.svc = Template.create({**common, "name": "ZT Perlengkapan", "is_storable": False})
        cls.supplier = cls.env["res.partner"].create(
            {"name": "PT Pemasok G65", "is_company": True, "company_id": cls.company.id}
        )
        for full, rec in (
            (X("branch_wh", 1), cls.wh1),
            (X("branch_wh", 2), cls.wh2),
            (X("branch_loc", 30), cls.sub),
            (X("product", "ZFIN"), cls.fin),
            (X("product", "ZRM"), cls.rm),
            (X("product", "ZSVC"), cls.svc),
            (X("supplier", "ZSUP"), cls.supplier),
        ):
            cls._bind(full, rec)

    @classmethod
    def _bind(cls, full, rec):
        cls.env["ir.model.data"].sudo().create(
            {"module": "efn_mig_esb", "name": full.split(".", 1)[1], "model": rec._name, "res_id": rec.id,
             "noupdate": True}
        )  # fmt: skip

    # ------------------------------------------------------------------------------------------ util
    def _bundle(self, files, **manifest):
        members, entries = {}, []
        for entity, (name, rows, rng) in files.items():
            blob, count = jsonl_bytes(rows)
            if name.endswith(".gz"):
                blob = gzip_bytes(blob)
            entry = file_entry(name, entity, blob, count)
            if rng:
                entry["range"] = list(rng)
            entries.append(entry)
            members[name] = blob
        base = {
            "run_id": str(uuid.uuid4()),
            "brand": BRAND,
            "company_codes": [CODE],
            "steps": [],
            "cutover_date": CUTOVER,
            "legacy_days": 400,
            "sales_line_months": 24,
            "parent_run_id": None,
            "segment": None,
            "created_at": CREATED,
            "files": entries,
            "maps": [],
        }
        base.update(manifest)
        return open_bundle(build_bundle(base, members, self.keys), self.keys, expect_kind="bundle")

    def _run(self, bundle, **kwargs):
        report = {"run_id": bundle.manifest["run_id"], "manifest_sha256": bundle.manifest_sha256, "errors": 0}
        values = {
            "companies": frozenset({CODE}),
            "dry_run": False,
            "app_env": "development",
            "migration_login": self.mig_user.login,
            "validation": imp.sign_validation_report(report, self.val_key),
            "validation_key": self.val_key,
        }
        values.update(kwargs)
        return imp.run_import(self.env, bundle, imp.Options(**values))

    # ------------------------------------------------------------------------------------------ supplierinfo
    def _si_row(self, sup, prod, **data):
        values = {"partner_key": X("supplier", sup), "product_key": X("product", prod), "supplier_code": sup,
                  "product_code": prod, "price": 12000, "discount": 5, "currency": "IDR", "min_qty": 0,
                  "sequence": 1, "date_start": "2026-11-20", "uom_esb": "KG", "uom_xmlid": None,
                  "source_purchase_num": "ZPO-1"}  # fmt: skip
        values.update(data)
        return {"entity": "supplierinfo", "company_code": CODE, "esb_key": f"{sup}|{prod}",
                "xmlid": X("supplierinfo", f"{sup}_{prod}"), "data": values}  # fmt: skip

    def test_supplierinfo_upsert_conflict_and_missing(self):
        rows = [self._si_row("ZSUP", "ZRM"), self._si_row("ZSUP", "ZFIN", price=3000, discount=0,
                                                            uom_xmlid="uom.product_uom_unit", sequence=2)]  # fmt: skip
        result = self._run(self._bundle({"supplierinfo": ("supplierinfo.jsonl", rows, None)}))
        self.assertEqual(result.exit_code, imp.EXIT_OK, result.report["errors"])
        self.assertEqual(result.report["entities"]["supplierinfo"]["created"], 2)
        info = self.env["product.supplierinfo"].search([("product_tmpl_id", "=", self.rm.id)])
        self.assertEqual((info.partner_id, info.company_id), (self.supplier, self.company))
        self.assertAlmostEqual(info.price, 12000)
        self.assertAlmostEqual(info.discount, 5)
        self.assertEqual(info.date_start.isoformat(), "2026-11-20")
        self.assertEqual(info.product_uom_id, self.rm.uom_id, "uom_xmlid kosong -> UoM produk")
        self.assertEqual((info.currency_id.name, info.sequence, info.min_qty), ("IDR", 1, 0))
        again = self._run(self._bundle({"supplierinfo": ("supplierinfo.jsonl", rows, None)}))
        self.assertEqual(again.report["entities"]["supplierinfo"]["skipped"], 2)
        editor = new_test_user(self.env, login="efn_mig_g65_editor", groups="base.group_user")
        info.with_user(editor).sudo().write({"price": 11000})
        rows[0]["data"]["price"] = 12500
        third = self._run(self._bundle({"supplierinfo": ("supplierinfo.jsonl", rows[:1], None)}))
        counts = third.report["entities"]["supplierinfo"]
        self.assertEqual((counts["conflict"], counts["missing_in_source"]), (1, 1), counts)
        self.assertAlmostEqual(info.price, 11000, msg="diubah pengguna -> tidak ditimpa (L-131)")
        self.assertTrue(self.env["product.supplierinfo"].search([("product_tmpl_id", "=", self.fin.id)]))
        bad = self._si_row("ZSUP", "ZRM")
        bad["esb_key"] = "ZSUP_ZRM"
        with self.assertRaises(IntegrityError):
            self._run(self._bundle({"supplierinfo": ("supplierinfo.jsonl", [bad], None)}))

    # ------------------------------------------------------------------------------------------ cost_as_of
    def _cost_row(self, code, as_of=CUTOVER, source="stock_opening"):
        return {"entity": "cost", "company_code": CODE, "esb_key": code, "xmlid": None,
                "data": {"product_key": X("product", code), "product_code": code, "standard_price": 3500,
                         "source_uom": None, "source": source, "as_of": as_of}}  # fmt: skip

    def test_cost_writes_cost_as_of_on_real_run_only(self):
        key = imp.PARAM_COST_AS_OF_PREFIX + CODE
        rows = [self._cost_row("ZFIN"), self._cost_row("ZRM", source="purchase_last")]
        dry = self._run(self._bundle({"cost": ("cost.jsonl", rows, None)}), dry_run=True)
        self.assertEqual(dry.exit_code, imp.EXIT_OK, dry.report["errors"])
        self.assertFalse(self.ICP.get_param(key), "dry-run tidak menulis parameter")
        real = self._run(self._bundle({"cost": ("cost.jsonl", rows, None)}))
        self.assertEqual(real.report["cost_sources"], {"stock_opening": 1, "purchase_last": 1})
        self.assertEqual(self.ICP.get_param(key), CUTOVER)
        self.assertAlmostEqual(self.fin.product_variant_id.with_company(self.company).standard_price, 3500)
        self.ICP.set_param(key, False)
        failing = [self._cost_row("ZFIN"), self._cost_row("ZNOPE")]  # produk tak ada -> PT tidak dianggap lengkap
        partial = self._run(self._bundle({"cost": ("cost.jsonl", failing, None)}))
        self.assertEqual(partial.exit_code, imp.EXIT_DATA)
        self.assertFalse(self.ICP.get_param(key))
        mixed = [self._cost_row("ZFIN"), self._cost_row("ZRM", as_of="2026-11-25")]
        self._run(self._bundle({"cost": ("cost.jsonl", mixed, None)}))
        self.assertFalse(self.ICP.get_param(key), "as_of tidak tunggal -> tidak ditulis")

    # ------------------------------------------------------------------------------------------ stock
    def _quant(self, branch_id, loc_key, code, qty, uom_esb=None, uom_xmlid=None):
        return {"entity": "quant", "company_code": CODE, "esb_key": f"{branch_id}|{code}", "xmlid": None,
                "data": {"location_key": loc_key, "product_key": X("product", code), "product_code": code,
                         "quantity": qty, "uom_esb": uom_esb, "uom_xmlid": uom_xmlid, "esb_branch_id": branch_id,
                         "esb_locations": ["Gudang Utama"], "amount": 0, "accounting_date": "2026-11-30"}}  # fmt: skip

    def _stock_bundle(self, rows, opening=CUTOVER, **manifest):
        opening_meta = {"opening_date": opening, "max_version": "x", "branches_checkpointed": 3}
        snapshot = {"fact_esb_stock_opening": opening_meta}
        return self._bundle({"quant": ("quant.jsonl", rows, None)}, source={"snapshot": snapshot}, **manifest)

    def _stock_rows(self, fin_qty=10):
        return [
            self._quant(1, X("branch_wh", 1), "ZFIN", fin_qty),
            self._quant(30, X("branch_loc", 30), "ZRM", 2500, "G", "uom.product_uom_gram"),
            self._quant(1, X("branch_wh", 1), "ZSVC", 3),
        ]

    def _inventory_moves(self):
        products = (self.fin | self.rm).product_variant_ids
        return self.env["stock.move"].search([("product_id", "in", products.ids), ("is_inventory", "=", True)])

    def test_stock_absolute_dated_and_idempotent(self):
        self.ICP.set_param(imp.PARAM_COST_AS_OF_PREFIX + CODE, CUTOVER)
        result = self._run(self._stock_bundle(self._stock_rows()))
        self.assertEqual(result.exit_code, imp.EXIT_OK, result.report["errors"])
        counts = result.report["entities"]["quant"]
        self.assertEqual((counts["created"], counts["skipped"]), (2, 1), counts)
        self.assertEqual(result.report["stock_skipped"], {"non_storable": 1})
        Quant = self.env["stock.quant"]
        fin_q = Quant.search([("product_id", "=", self.fin.product_variant_id.id),
                              ("location_id", "=", self.wh1.lot_stock_id.id)])  # fmt: skip
        self.assertAlmostEqual(fin_q.quantity, 10)
        rm_q = Quant.search([("product_id", "=", self.rm.product_variant_id.id), ("location_id", "=", self.sub.id)])
        self.assertAlmostEqual(rm_q.quantity, 2.5, msg="2500 G -> 2,5 KG (UoM produk)")
        # odoo_totals.quant (kontrak W-B): qty dalam satuan baris bundel (G), satu baris per baris quant.
        totals = {(r["location_key"], r["product_key"]): r for r in result.totals["quant"]}
        self.assertEqual(len(totals), 3)
        self.assertAlmostEqual(totals[(X("branch_loc", 30), X("product", "ZRM"))]["quantity"], 2500)
        fin_total = totals[(X("branch_wh", 1), X("product", "ZFIN"))]
        self.assertEqual((fin_total["company_code"], fin_total["quantity"]), (CODE, 10))
        self.assertEqual(totals[(X("branch_wh", 1), X("product", "ZSVC"))]["quantity"], 0, "non-storable")
        moves = self._inventory_moves()
        self.assertEqual(len(moves), 2)
        # cutover − 1 23:59:59 WIB = 2026-11-30 16:59:59 UTC (stock.move.date naif UTC)
        self.assertEqual(set(moves.mapped("date")), {datetime(2026, 11, 30, 16, 59, 59)})
        self.assertEqual(set(moves.mapped("state")), {"done"})
        self.assertFalse(fin_q.accounting_date, "accounting_date dikosongkan stock_account setelah apply")
        # Ulang data sama = 0 move baru.
        again = self._run(self._stock_bundle(self._stock_rows()))
        self.assertEqual(again.report["entities"]["quant"]["skipped"], 3)
        self.assertEqual(len(self._inventory_moves()), 2)
        # Target absolut baru -> hanya selisih.
        third = self._run(self._stock_bundle(self._stock_rows(fin_qty=12)))
        self.assertEqual(third.report["entities"]["quant"]["updated"], 1)
        self.assertAlmostEqual(fin_q.quantity, 12)
        self.assertEqual(len(self._inventory_moves()), 3)
        # Produk hilang dari snapshot: dilaporkan; zero_missing (non-production) -> 0.
        only_fin = self._stock_rows(fin_qty=12)[:1]
        reported = self._run(self._stock_bundle(only_fin))
        self.assertEqual(reported.report["stock_missing_in_snapshot"], 1)
        self.assertAlmostEqual(rm_q.quantity, 2.5)
        zeroed = self._run(self._stock_bundle(only_fin), zero_missing=True)
        self.assertEqual(zeroed.report["stock_zeroed"], 1)
        rm_left = Quant.search([("product_id", "=", self.rm.product_variant_id.id), ("location_id", "=", self.sub.id)])
        self.assertAlmostEqual(sum(rm_left.mapped("quantity")), 0)
        with self.assertRaisesRegex(imp.ConfigError, "zero_missing"):
            self._run(self._stock_bundle(only_fin), zero_missing=True, app_env="production", confirm_brand=BRAND)

    def test_stock_gates(self):
        rows = self._stock_rows()
        with self.assertRaisesRegex(imp.GateError, "cost_as_of"):
            self._run(self._stock_bundle(rows))
        self.ICP.set_param(imp.PARAM_COST_AS_OF_PREFIX + CODE, CUTOVER)
        with self.assertRaisesRegex(imp.GateError, "opening_date"):
            self._run(self._stock_bundle(rows, opening="2026-11-30"))
        with self.assertRaisesRegex(imp.GateError, "SALDO_BLOCKED"):
            self._run(self._stock_bundle(rows), saldo_blocked=frozenset({CODE}))
        foreign = self._quant(1, xmlid(BRAND, "PB", "branch_wh", 1), "ZFIN", 1)
        with self.assertRaises(IntegrityError):
            self._run(self._stock_bundle([foreign]))
        with self.assertRaises(IntegrityError):  # qty <= 0 tidak boleh ada di bundel
            self._run(self._stock_bundle([self._quant(1, X("branch_wh", 1), "ZFIN", 0)]))
        lot_tmpl = self.env["product.template"].create(
            {"name": "ZT Ragi lot", "type": "consu", "is_storable": True, "tracking": "lot",
             "company_id": self.company.id}
        )  # fmt: skip
        self._bind(X("product", "ZLOT"), lot_tmpl)
        with self.assertRaisesRegex(imp.GateError, "tracking"):
            self._run(self._stock_bundle([*rows, self._quant(1, X("branch_wh", 1), "ZLOT", 4)]))
        # Transaksi nyata pada/setelah cutover di lokasi PT -> stok awal ditolak (Q-ESB29).
        product = self.fin.product_variant_id
        env = self.env(context=dict(self.env.context, allowed_company_ids=self.company.ids))
        env["stock.quant"]._update_available_quantity(product, self.wh2.lot_stock_id, 5)
        move = env["stock.move"].create(
            {"product_id": product.id, "product_uom_qty": 1, "location_id": self.wh2.lot_stock_id.id,
             "location_dest_id": self.env.ref("stock.stock_location_customers").id, "company_id": self.company.id}
        )  # fmt: skip
        move._action_confirm()
        move._action_assign()
        move.quantity = 1
        move.picked = True
        move._action_done()
        move.date = datetime(2026, 12, 1, 2, 0, 0)  # 09:00 WIB 1 Des
        with self.assertRaisesRegex(imp.GateError, "stock.move"):
            self._run(self._stock_bundle(rows))
        self.assertFalse(self._inventory_moves(), "gerbang ditolak sebelum DB ditulis")

    # ------------------------------------------------------------------------------------------ histori pembelian
    def _po_row(self, day, num="ZPO-1", line_no=1, mapped=True, **extra):
        row = {"purchase_num": num, "line_no": line_no, "date": day, "date_required": None,
               "branch_key": X("branch_wh", 1) if mapped else None, "branch_name": "ZT Toko 1" if mapped else "HO",
               "supplier_key": X("supplier", "ZSUP") if mapped else None, "supplier_name": "PT Pemasok G65",
               "product_key": X("product", "ZRM") if mapped else None, "esb_branch_id": 1 if mapped else 0,
               "esb_supplier_code": "ZSUP", "esb_product_code": "ZRM", "uom_name": "KG", "qty": 10,
               "receipt_qty": 10, "pricelist_price": 0, "price_unit": 12000, "discount": 0, "vat_value": 13200,
               "total": 133200, "status": "Finished", "map_source": "header" if mapped else "none"}  # fmt: skip
        row.update(extra)
        return row

    def _po_bundle(self, month, rows, rng, part=1, **manifest):
        segment = {"company_code": CODE, "month": month, "part": part, "parts": part}
        files = {"legacy_purchase_line": ("legacy_purchase_line.jsonl.gz", rows, rng)}
        values = {"parent_run_id": str(uuid.uuid4()), "segment": segment}
        values.update(manifest)
        return self._bundle(files, **values)

    def _po_lines(self):
        return self.env["efn.legacy.purchase.line"].sudo().search([("company_id", "=", self.company.id)])

    def test_purchase_history_segment(self):
        rows = [
            self._po_row("2026-10-03", product_name="ZT Tepung"),
            self._po_row("2026-10-03", line_no=2, mapped=False),
        ]
        bundle = self._po_bundle("2026-10", rows, ("2026-10-01", "2026-11-01"))
        result = self._run(bundle)
        self.assertEqual(result.exit_code, imp.EXIT_OK, (result.report["errors"], result.report["slices"]))
        lines = self._po_lines()
        self.assertEqual(len(lines), 2)
        full = lines.filtered(lambda r: r.line_no == 1)
        self.assertEqual((full.warehouse_id, full.supplier_id), (self.wh1, self.supplier))
        self.assertEqual(full.product_id, self.rm.product_variant_id)
        self.assertEqual(full.product_name, "ZT Tepung")
        ho = lines.filtered(lambda r: r.line_no == 2)
        self.assertFalse(ho.warehouse_id or ho.supplier_id or ho.product_id)
        (item,) = result.report["slices"]
        self.assertEqual((item["rows_inserted"], item["rows_unmapped_branch"]), (2, 1))
        totals = {r["branch_name"]: r for r in result.totals["legacy_purchase_line"]}
        self.assertEqual((totals["HO"]["rows"], totals["ZT Toko 1"]["total"]), (1, 133200))
        self.assertEqual({r["company_code"] for r in totals.values()}, {CODE})
        (again,) = self._run(bundle).report["slices"]
        self.assertEqual((again["rows_deleted"], again["rows_inserted"]), (2, 2))
        self.assertEqual(len(self._po_lines()), 2)
        with self.assertRaises(IntegrityError):  # part > 1 akan mengosongkan irisan company × bulan
            self._run(self._po_bundle("2026-10", rows, ("2026-10-01", "2026-11-01"), part=2))
        with self.assertRaises(IntegrityError):  # (purchase_num, line_no) ganda
            self._run(self._po_bundle("2026-10", [rows[0], rows[0]], ("2026-10-01", "2026-11-01")))
        with self.assertRaises(IntegrityError):  # supplier PT lain
            bad = self._po_row("2026-10-04", supplier_key=xmlid(BRAND, "PB", "supplier", "ZSUP"))
            self._run(self._po_bundle("2026-10", [bad], ("2026-10-01", "2026-11-01")))

    # ------------------------------------------------------------------------------------------ geser cutover
    def test_cutover_param_shift(self):
        key = bulk.PARAM_CUTOVER_PREFIX + CODE
        self.ICP.set_param(key, "2026-11-17")
        october = self._po_bundle("2026-10", [self._po_row("2026-10-03")], ("2026-10-01", "2026-11-01"))
        self.assertEqual(self._run(october).exit_code, imp.EXIT_OK)
        param_only = self._bundle({})
        with self.assertRaisesRegex(imp.GateError, "production"):
            self._run(october, app_env="production", confirm_brand=BRAND)
        with self.assertRaisesRegex(imp.GateError, "histori belum dimuat"):
            self._run(param_only, steps=("cutover-param",))
        self.assertEqual(self.ICP.get_param(key), "2026-11-17")
        # November ulang (termasuk 17–30 Nov) dengan cutover baru -> histori lengkap -> parameter boleh digeser.
        november = self._po_bundle(
            "2026-11", [self._po_row("2026-11-05"), self._po_row("2026-11-20", num="ZPO-2")],
            ("2026-11-01", "2026-12-01"),
        )  # fmt: skip
        result = self._run(november, steps=("legacy_purchase_line", "cutover-param"))
        self.assertEqual(result.exit_code, imp.EXIT_OK, (result.report["errors"], result.report["slices"]))
        self.assertEqual(self.ICP.get_param(key), CUTOVER)
        backward = self._bundle({}, cutover_date="2026-11-17")
        with self.assertRaisesRegex(imp.GateError, "MUNDUR"):
            self._run(backward, steps=("cutover-param",))
