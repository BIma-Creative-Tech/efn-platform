"""Importer bundel migrasi ESB -> Odoo (G43 W2b, docs/04 §27.2/§27.4/§27.5/§27.8a). Bundel SINTETIS (tanpa data
klien) dibuat dengan `efn_mig.bundle.build_bundle` (salinan terkendali) dan kunci uji acak per kelas."""

import secrets
import uuid

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

BRAND = "zzt"  # brand uji (bukan brand nyata) agar tidak bertabrakan dengan xmlid migrasi sungguhan
CUTOVER = "2026-11-01"
MONTH = "2026-10"


def X(entity, code, company="PB"):  # noqa: N802
    return xmlid(BRAND, company, entity, code)


def _row(entity, code, data, xmlid_entity=None, company="PB"):
    target = xmlid_entity if xmlid_entity is not None else entity
    return {
        "entity": entity,
        "company_code": company,
        "esb_key": str(code),
        "xmlid": X(target, code, company) if target else None,
        "data": data,
    }


@tagged("post_install", "-at_install", "efn")
class TestEsbImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.keys = derive_keys(secrets.token_hex(32))
        cls.pb = cls._company("PB")
        cls.wtz = cls._company("WTZ")
        cls.mig_user = new_test_user(cls.env, login="efn_mig_esb_test", groups="base.group_user")
        cls.has_mrp = "mrp.bom" in cls.env
        ICP = cls.env["ir.config_parameter"].sudo()
        ICP.set_param(bulk.PARAM_CUTOVER_PREFIX + "PB", False)
        ICP.set_param(bulk.PARAM_SALES_LINE_MONTHS, "24")

    @classmethod
    def _company(cls, code):
        company = cls.env["res.company"].search([("efn_brand_code", "=", code)], limit=1)
        return company or cls.env["res.company"].create({"name": f"PT uji impor {code}", "efn_brand_code": code})

    # ------------------------------------------------------------------------------------------ bundel sintetis
    def _bundle(self, files, companies=("PB",), **manifest):
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
            "company_codes": list(companies),
            "steps": [],
            "cutover_date": None,
            "legacy_days": 400,
            "sales_line_months": 24,
            "parent_run_id": None,
            "segment": None,
            "files": entries,
            "maps": [],
        }
        base.update(manifest)
        return open_bundle(build_bundle(base, members, self.keys), self.keys, expect_kind="bundle")

    def _master_rows(self, company="PB"):
        def r(entity, code, data, xe=None):
            return _row(entity, code, data, xe, company)

        def k(entity, code):
            return X(entity, code, company)

        branch = [
            r("branch", 4, {"branch_id": 4, "name": "ZT CK", "warehouse_code": "ZTCK", "type": "ck",
                            "is_central_kitchen": True, "active": True, "pos_xmlid": None}, "branch_wh"),
            r("branch", 11, {"branch_id": 11, "name": "ZT Toko 11", "warehouse_code": "ZT11", "type": "store",
                             "is_central_kitchen": False, "active": True, "pos_xmlid": k("branch_pos", 11)},
              "branch_wh"),
            r("branch", 30, {"branch_id": 30, "name": "ZT Gudang Kering", "type": "location",
                             "parent_key": k("branch_wh", 4), "active": True}, "branch_loc"),
        ]  # fmt: skip
        uom = [
            r("uom", "EA", {"esb_uom": "EA", "uom_xmlid": "uom.product_uom_unit", "ea_factor": 1}),
            r("uom", "KG", {"esb_uom": "KG", "uom_xmlid": "uom.product_uom_kgm", "ea_factor": None}),
        ]
        category = [
            r("category", "Roti", {"name": "Roti", "parent_key": None}),
            r("category", "Bahan", {"name": "Bahan", "parent_key": None}),
        ]
        pos_category = [
            r("pos_category", "Bakery", {"name": "Bakery", "parent_key": None}),
            r("pos_category", "Bakery/Roti", {"name": "Roti", "parent_key": k("pos_category", "Bakery")}),
        ]
        product = [
            r("product", "ZFIN-1", {"default_code": "ZFIN-1", "name": "ZT FIN Roti", "categ_key": k("category", "Roti"),
                                    "uom_esb": "EA", "uom_xmlid": "uom.product_uom_unit", "ea_factor": 1,
                                    "type": "consu", "is_storable": True, "purchase_ok": False, "sale_ok": False,
                                    "is_fin": True, "active": True, "batch_to_ea_ratio": 12}),
            r("product", "ZRM1", {"default_code": "ZRM1", "name": "ZT Tepung", "categ_key": k("category", "Bahan"),
                                  "uom_esb": "KG", "uom_xmlid": "uom.product_uom_kgm", "ea_factor": None,
                                  "type": "consu", "is_storable": True, "purchase_ok": True, "sale_ok": False,
                                  "is_fin": False, "active": True, "batch_to_ea_ratio": None}),
        ]  # fmt: skip
        menu_base = {"pos_categ_key": k("pos_category", "Bakery/Roti"), "available_in_pos": True, "sale_ok": True,
                     "type": "consu", "bom_name": None, "alias_of": None, "is_takeaway": False}  # fmt: skip
        menu = [
            r("menu", "ZM01", {**menu_base, "menu_code": "ZM01", "name": "ZT Roti Tawar", "list_price": 25000,
                               "active": True, "bom_name": "BOM MENU ROTI"}),
            r("menu", "ZM01T", {**menu_base, "menu_code": "ZM01T", "name": "ZT Roti Tawar T", "list_price": None,
                                "active": True, "alias_of": k("menu", "ZM01"), "is_takeaway": True}),
            r("menu", "ZOLD9", {**menu_base, "menu_code": "ZOLD9", "name": "ZT Menu Lama", "list_price": None,
                                "active": False, "available_in_pos": False}),
        ]  # fmt: skip
        payment_term = [r("payment_term", "net_30", {"days": 30, "name": "ZT 30 Hari"})]
        supplier = [r("supplier", "ZSUP1", {"ref": "ZSUP1", "name": "PT Pemasok Uji", "is_company": True,
                                             "supplier_rank": 1, "active": True})]  # fmt: skip
        customer = [r("customer", "ZC001", {"ref": "ZC001", "name": "PT Mitra Uji", "is_company": True,
                                             "customer_rank": 1, "category_name": "ZT B2B",
                                             "payment_term_key": k("payment_term", "net_30"),
                                             "active": True})]  # fmt: skip
        cost = [r("cost", "ZFIN-1", {"product_key": k("product", "ZFIN-1"), "product_code": "ZFIN-1",
                                      "standard_price": 3500, "source_uom": "EA"}, xe="")]  # fmt: skip
        files = {
            "branch": ("branch.jsonl", branch, None),
            "uom": ("uom.jsonl", uom, None),
            "category": ("category.jsonl", category, None),
            "pos_category": ("pos_category.jsonl", pos_category, None),
            "product": ("product.jsonl", product, None),
            "menu": ("menu.jsonl", menu, None),
            "payment_term": ("payment_term.jsonl", payment_term, None),
            "supplier": ("supplier.jsonl", supplier, None),
            "customer": ("customer.jsonl", customer, None),
            "cost": ("cost.jsonl", cost, None),
        }
        if self.has_mrp:
            line = {"product_code": "ZRM1", "product_key": k("product", "ZRM1"), "qty": 2.5, "uom_esb": "KG",
                    "uom_xmlid": "uom.product_uom_kgm", "yield_percent": 100}  # fmt: skip
            kit_line = {"product_code": "ZFIN-1", "product_key": k("product", "ZFIN-1"), "qty": 1, "uom_esb": "EA",
                        "uom_xmlid": "uom.product_uom_unit", "yield_percent": 100}  # fmt: skip
            common = {"bom_type_id": 1, "bom_type_name": None, "uom_esb": "EA", "uom_xmlid": "uom.product_uom_unit"}
            files["bom"] = ("bom.jsonl", [
                r("bom", 7, {**common, "code": "ZB7", "name": "BOM ROTI", "type": "normal", "product_code": "ZFIN-1",
                             "product_key": k("product", "ZFIN-1"), "menu_keys": [], "product_qty": 12,
                             "ratio_source": "bom_ratio", "lines": [line]}),
                r("bom", 8, {**common, "code": "ZB8", "name": "BOM MENU ROTI", "type": "phantom", "product_code": None,
                             "product_key": None, "menu_keys": [k("menu", "ZM01")], "product_qty": 1,
                             "ratio_source": "default_1", "lines": [kit_line]}),
            ], None)  # fmt: skip
        return files

    def _options(self, bundle, **kwargs):
        values = {
            "companies": frozenset({"PB"}),
            "dry_run": False,
            "app_env": "development",
            "migration_login": self.mig_user.login,
            "validation": {"manifest_sha256": bundle.manifest_sha256, "errors": 0},
        }
        values.update(kwargs)
        return imp.Options(**values)

    def _run(self, bundle, **kwargs):
        return imp.run_import(self.env, bundle, self._options(bundle, **kwargs))

    def _ref(self, full):
        name = full.split(".", 1)[1]
        imd = self.env["ir.model.data"].sudo().search([("module", "=", "efn_mig_esb"), ("name", "=", name)])
        return self.env[imd.model].browse(imd.res_id) if imd else None

    def _import_master(self):
        bundle = self._bundle(self._master_rows())
        result = self._run(bundle)
        self.assertEqual(result.exit_code, imp.EXIT_OK, result.report["errors"])
        return bundle, result

    # ------------------------------------------------------------------------------------------ master
    def test_master_import_creates_records(self):
        bundle, result = self._import_master()
        ent = result.report["entities"]
        self.assertEqual(ent["branch"]["created"], 3)
        self.assertEqual(ent["branch_pos"]["created"], 1)
        self.assertEqual(ent["menu"]["created"], 3)
        self.assertEqual(ent["cost"]["updated"], 1)
        wh = self._ref(X("branch_wh", 11))
        self.assertEqual((wh.code, wh.company_id), ("ZT11", self.pb))
        ck = self._ref(X("branch_wh", 4))
        if "efn_is_central_kitchen" in ck._fields:
            self.assertTrue(ck.efn_is_central_kitchen)
        loc = self._ref(X("branch_loc", 30))
        self.assertEqual((loc.location_id, loc.usage, loc.company_id), (ck.lot_stock_id, "internal", self.pb))
        pos = self._ref(X("branch_pos", 11))
        self.assertEqual((pos._name, pos.company_id, pos.picking_type_id), ("pos.config", self.pb, wh.pos_type_id))
        self.assertFalse(pos.payment_method_ids, "metode bayar milik admin, tidak dibuat migrasi")
        fin = self._ref(X("product", "ZFIN-1"))
        self.assertEqual((fin._name, fin.company_id, fin.is_storable), ("product.template", self.pb, True))
        self.assertEqual(fin.uom_id, self.env.ref("uom.product_uom_unit"))
        self.assertEqual(fin.categ_id, self._ref(X("category", "Roti")))
        self.assertEqual(fin.categ_id.parent_id.name, BRAND.upper(), "kategori induk per brand")
        self.assertAlmostEqual(fin.with_company(self.pb).standard_price, 3500)
        menu = self._ref(X("menu", "ZM01"))
        self.assertTrue(menu.available_in_pos)
        self.assertFalse(menu.is_storable)
        self.assertEqual(menu.list_price, 25000)
        self.assertEqual(menu.pos_categ_ids, self._ref(X("pos_category", "Bakery/Roti")))
        self.assertEqual(self._ref(X("menu", "ZM01T")), menu, "takeaway = xmlid kedua ke menu dasar")
        old = self._ref(X("menu", "ZOLD9"))
        self.assertFalse(old.active, "menu arsip dibuat sebagai produk arsip")
        customer = self._ref(X("customer", "ZC001"))
        self.assertEqual(customer.company_id, self.pb)
        term = self._ref(X("payment_term", "net_30"))
        self.assertEqual(customer.with_company(self.pb).property_payment_term_id, term)
        self.assertEqual((term.company_id, term.line_ids.nb_days), (self.pb, 30))
        self.assertEqual(customer.category_id.name, "ZT B2B")
        self.assertEqual(customer.write_uid, self.mig_user, "write_uid = user teknis migrasi")
        if self.has_mrp:
            normal = self._ref(X("bom", 7))
            self.assertEqual((normal.type, normal.product_tmpl_id, normal.product_qty), ("normal", fin, 12))
            self.assertEqual(normal.bom_line_ids.product_id, self._ref(X("product", "ZRM1")).product_variant_id)
            self.assertAlmostEqual(normal.bom_line_ids.product_qty, 2.5)
            self.assertEqual(normal.bom_line_ids.product_uom_id, self.env.ref("uom.product_uom_kgm"))
            kit = self._ref(X("bom", 8))
            self.assertEqual((kit.type, kit.product_tmpl_id), ("phantom", menu))
        imd = self.env["ir.model.data"].sudo().search([("module", "=", "efn_mig_esb"), ("name", "=like", f"{BRAND}_%")])
        self.assertTrue(all(imd.mapped("noupdate")))
        expected_map = 3 + 1 + 2 + 2 + 2 + 2 + 3 + 1 + 1 + 1 + (2 if self.has_mrp else 0)
        self.assertEqual(len(result.map_rows), expected_map)
        self.assertEqual(
            {r["odoo_id"] for r in result.map_rows if r["esb_key"] in ("ZM01", "ZM01T")}, {menu.id}, "dua baris map"
        )
        # Bundel hasil terverifikasi dan berisi map.jsonl (master).
        opened = open_bundle(imp.build_result(bundle, result, self.keys), self.keys, expect_kind="result")
        self.assertEqual(set(opened.files), {"import_report.json", "odoo_totals.json", "map.jsonl"})
        self.assertEqual(len(list(opened.rows("map.jsonl"))), expected_map)
        self.assertEqual(opened.manifest["run_id"], bundle.manifest["run_id"])

    def test_master_reimport_is_noop_and_respects_user_edits(self):
        self._import_master()
        again = self._run(self._bundle(self._master_rows()))
        self.assertEqual(again.exit_code, imp.EXIT_OK, again.report["errors"])
        for entity, counts in again.report["entities"].items():
            with self.subTest(entity=entity):
                self.assertEqual((counts["created"], counts["updated"], counts["failed"]), (0, 0, 0), counts)
        # Field milik migrasi diubah pengguna lain -> konflik, tidak ditimpa.
        fin = self._ref(X("product", "ZFIN-1"))
        fin.write({"name": "Diubah pengguna"})
        third = self._run(self._bundle(self._master_rows()))
        self.assertEqual(third.report["entities"]["product"]["conflict"], 1)
        self.assertEqual(fin.name, "Diubah pengguna")
        # Record ada di Odoo tetapi hilang di sumber -> dilaporkan, tidak diarsip.
        rows = self._master_rows()
        rows["supplier"] = ("supplier.jsonl", [], None)
        fourth = self._run(self._bundle(rows))
        self.assertEqual(fourth.report["entities"]["supplier"]["missing_in_source"], 1)
        self.assertTrue(self._ref(X("supplier", "ZSUP1")).active)

    def test_master_dry_run_leaves_no_data(self):
        bundle = self._bundle(self._master_rows())
        result = self._run(bundle, dry_run=True)
        self.assertTrue(result.report["dry_run"])
        self.assertEqual(result.report["entities"]["branch"]["created"], 3)
        imd = (
            self.env["ir.model.data"]
            .sudo()
            .search_count([("module", "=", "efn_mig_esb"), ("name", "=like", f"{BRAND}_%")])
        )
        self.assertEqual(imd, 0)
        self.assertFalse(self.env["stock.warehouse"].search([("code", "=", "ZT11"), ("company_id", "=", self.pb.id)]))
        Template = self.env["product.template"].with_context(active_test=False)
        self.assertFalse(Template.search([("default_code", "=", "ZFIN-1")]))

    def test_gates_reject_foreign_company_and_bad_validation(self):
        foreign = self._bundle(self._master_rows("WTZ"), companies=("WTZ",))
        with self.assertRaisesRegex(imp.GateError, "bukan target"):
            self._run(foreign)
        rows = self._master_rows()
        rows["supplier"][1][0]["company_code"] = "WTZ"  # baris company asing dalam bundel PB
        with self.assertRaises(IntegrityError):
            self._run(self._bundle(rows))
        bundle = self._bundle(self._master_rows())
        with self.assertRaisesRegex(imp.GateError, "validator"):
            self._run(bundle, validation={"manifest_sha256": "0" * 64, "errors": 0})
        with self.assertRaisesRegex(imp.GateError, "validator"):
            self._run(bundle, validation=None)
        with self.assertRaisesRegex(imp.GateError, "confirm-brand"):
            self._run(bundle, app_env="production")
        with self.assertRaisesRegex(imp.ConfigError, "default MATI"):
            self._run(bundle, steps=("stock",))
        with self.assertRaisesRegex(imp.ConfigError, "belum diimplementasikan"):
            self._run(bundle, steps=("opening",), enable_opening_balances=True)
        self.assertFalse(self._ref(X("branch_wh", 11)), "gerbang ditolak sebelum DB ditulis")

    # ------------------------------------------------------------------------------------------ histori
    def _history_bundle(self, **manifest):
        wh11, ck = X("branch_wh", 11), X("branch_wh", 4)
        fin, nope = X("product", "ZFIN-1"), X("product", "ZNOPE")
        flow_cols = ("ck_prod", "store_prod", "ck_out", "in_ck", "in_other", "st_in")

        def flow(day, branch_key, product_key, branch_id, sold, st_out=0):
            row = dict.fromkeys(flow_cols, 0)
            row.update(day=day, branch_key=branch_key, product_key=product_key, esb_branch_id=branch_id,
                       esb_product_code="ZFIN-1", sold=sold, st_out=st_out, outflow=sold + st_out)  # fmt: skip
            return row

        flows = [flow(f"2026-10-0{d}", wh11, fin, 11, 10, 1) for d in (1, 2, 3)]
        flows += [flow("2026-10-01", ck, fin, 4, 5), flow("2026-10-01", ck, fin, 30, 2)]  # lokasi -> gudang induk
        flows.append(flow("2026-10-02", wh11, nope, 11, 9))  # produk tak terpetakan -> dilewati

        def sale(code, menu_key, takeaway):
            return {"day": "2026-10-05", "branch_key": wh11, "pos_key": X("branch_pos", 11), "menu_key": menu_key,
                    "esb_branch_id": 11, "esb_menu_code": code, "is_takeaway": takeaway, "qty": 3, "gross": 75000,
                    "discount": 5000, "net": 70000, "cogs": 10500, "trx_count": 2}  # fmt: skip

        base = X("menu", "ZM01")
        sales = [sale("ZM01", base, False), sale("ZM01T", base, True), sale("ZX", None, False)]

        def line(no, code, menu_key):
            return {"sales_num": "ZS-0001", "line_no": no, "date": "2026-10-06", "branch_key": wh11,
                    "menu_key": menu_key, "esb_menu_code": code, "qty": 1, "price": 25000, "total": 25000,
                    "discount": 0, "cogs": 3500, "row_type": "ALACARTE", "is_takeaway": False}  # fmt: skip

        lines = [line(1, "ZM01", X("menu", "ZM01")), line(2, "ZM01T", X("menu", "ZM01")), line(3, "ZX", None)]
        rng = ("2026-10-01", "2026-11-01")
        files = {
            "legacy_flow_daily": ("legacy_flow_daily.jsonl.gz", flows, rng),
            "legacy_sales_daily": ("legacy_sales_daily.jsonl.gz", sales, rng),
            "legacy_sales_line": ("legacy_sales_line.jsonl.gz", lines, rng),
        }
        segment = {"company_code": "PB", "month": MONTH, "part": 1, "parts": 1}
        values = {
            "cutover_date": CUTOVER,
            "parent_run_id": str(uuid.uuid4()),
            "segment": segment,
            "branch_ids": [4, 11],
        }
        values.update(manifest)
        return self._bundle(files, **values)

    def _legacy(self, model):
        return (
            self.env[model].sudo().search([("company_id", "=", self.pb.id), ("esb_brand", "=", BRAND)])
            if (model != "efn.legacy.sales.line")
            else self.env[model].sudo().search([("company_id", "=", self.pb.id), ("sales_num", "=", "ZS-0001")])
        )

    def test_history_import_idempotent_with_totals(self):
        self._import_master()
        bundle = self._history_bundle()
        result = self._run(bundle)
        self.assertEqual(result.exit_code, imp.EXIT_OK, (result.report["errors"], result.report["slices"]))
        flows = self._legacy("efn.legacy.flow.daily")
        self.assertEqual(len(flows), 4)
        ck = self._ref(X("branch_wh", 4))
        ck_row = flows.filtered(lambda r: r.warehouse_id == ck)
        self.assertEqual(ck_row.sold, 7, "cabang lokasi diagregasi ke gudang induk")
        self.assertEqual(set(flows.mapped("run_id")), {bundle.manifest["run_id"]})
        flow_slices = [s for s in result.report["slices"] if s["model"] == "efn.legacy.flow.daily"]
        skipped = sum(s["rows_skipped_unmapped"] for s in flow_slices)
        self.assertEqual(skipped, 1)
        menu = self._ref(X("menu", "ZM01"))
        daily = self._legacy("efn.legacy.sales.daily")
        self.assertEqual(len(daily), 3)
        by_code = {r.esb_menu_code: r for r in daily}
        self.assertEqual(by_code["ZM01T"].product_id, menu.product_variant_id)
        self.assertTrue(by_code["ZM01T"].is_takeaway)
        self.assertFalse(by_code["ZX"].product_id, "menu tak terpetakan tetap tersimpan")
        self.assertEqual(by_code["ZM01"].pos_config_id, self._ref(X("branch_pos", 11)))
        lines = self._legacy("efn.legacy.sales.line")
        self.assertEqual(len(lines), 3)
        totals = result.totals
        self.assertEqual(totals["bulk_check"], {"wrong_company_rows": 0, "foreign_product_rows": 0})
        sold = sum(r["sold"] for r in totals["legacy_flow_daily"])
        self.assertAlmostEqual(sold, 37)
        self.assertEqual({r["product_key"] for r in totals["legacy_flow_daily"]}, {X("product", "ZFIN-1")})

        def _key(pair):  # menu tak terpetakan = None; urutkan aman tanpa membandingkan None dengan str
            return (pair[0] is None, pair[0] or "", pair[1])

        self.assertEqual(
            sorted(((r["menu_key"], r["is_takeaway"]) for r in totals["legacy_sales_daily"]), key=_key),
            sorted([(X("menu", "ZM01"), False), (X("menu", "ZM01"), True), (None, False)], key=_key),
        )
        self.assertEqual(totals["legacy_sales_line"][0]["rows"], 3)
        self.assertGreater(result.report["table_bytes"]["efn_legacy_sales_line"], 0)
        opened = open_bundle(imp.build_result(bundle, result, self.keys), self.keys, expect_kind="result")
        self.assertEqual(set(opened.files), {"import_report.json", "odoo_totals.json"}, "segmen tanpa map.jsonl")
        # Impor ulang: upsert = 0 perubahan; ganti irisan = jumlah baris sama.
        again = self._run(bundle)
        for item in again.report["slices"]:
            if item["model"] != "efn.legacy.sales.line":
                self.assertEqual((item["rows_inserted"], item.get("rows_updated")), (0, 0), item)
        self.assertEqual(len(self._legacy("efn.legacy.flow.daily")), 4)
        self.assertEqual(len(self._legacy("efn.legacy.sales.line")), 3)

    def test_history_dry_run_and_gates(self):
        self._import_master()
        bundle = self._history_bundle()
        result = self._run(bundle, dry_run=True)
        self.assertTrue(result.totals["legacy_sales_line"], "total dihitung sebelum rollback")
        for model in ("efn.legacy.flow.daily", "efn.legacy.sales.daily", "efn.legacy.sales.line"):
            self.assertFalse(self._legacy(model), model)
        with self.assertRaisesRegex(imp.GateError, "SALDO_BLOCKED"):
            self._run(bundle, saldo_blocked=frozenset({"PB"}))
        late = self._history_bundle(cutover_date="2026-10-01")
        with self.assertRaisesRegex(imp.GateError, "cutover"):
            self._run(late)
        self.env["ir.config_parameter"].sudo().set_param(bulk.PARAM_SALES_LINE_MONTHS, "0")
        skipped = self._run(bundle)
        self.assertEqual(skipped.report["skipped_by_param"], {"legacy_sales_line": 3})
        self.assertFalse(self._legacy("efn.legacy.sales.line"))

    def test_history_requires_imported_warehouses(self):
        with self.assertRaisesRegex(imp.GateError, "gudang"):
            self._run(self._history_bundle())
