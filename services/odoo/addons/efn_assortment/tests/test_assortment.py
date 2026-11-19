"""efn_assortment (G44, docs/04 §28.5, ADR-045 §3.2, L-150): kode alasan, blok transfer DC -> outlet, override."""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnAssortment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.company = env["res.company"].create({"name": "PT Uji Assortment"})
        Warehouse = env["stock.warehouse"]
        cls.dc = Warehouse.create({"name": "DC uji", "code": "ADC1", "company_id": cls.company.id, "efn_is_dc": True})
        cls.region_a = env["efn.region"].create({"code": "99.91", "name": "Kota Uji A"})
        cls.region_b = env["efn.region"].create({"code": "99.92", "name": "Kota Uji B"})
        partner = env["res.partner"].create(
            {"name": "Alamat outlet uji", "company_id": cls.company.id, "efn_region_id": cls.region_b.id}
        )
        cls.outlet = Warehouse.create(
            {
                "name": "Outlet tertutup uji",
                "code": "AOT1",
                "company_id": cls.company.id,
                "efn_assortment_policy": "closed",
                "partner_id": partner.id,
            }
        )
        cls.cap_freezer = env.ref("efn_assortment.capability_freezer")
        cls.cap_alcohol = env.ref("efn_assortment.capability_alcohol")
        cls.categ = env["product.category"].create({"name": "Uji Grocery"})
        cls.categ_child = env["product.category"].create({"name": "Uji Snack", "parent_id": cls.categ.id})
        Product = env["product.product"]

        def product(name, **vals):
            return Product.create(dict({"name": name, "is_storable": True, "categ_id": cls.categ_child.id}, **vals))

        cls.p_ok = product("Keripik (uji)")
        cls.categ_other = env["product.category"].create({"name": "Uji Lain"})
        cls.p_unlisted = product("Tidak terdaftar (uji)", categ_id=cls.categ_other.id)
        cls.p_denied = product("Dilarang (uji)")
        cls.p_frozen = product("Es krim (uji)", efn_required_capability_ids=[(6, 0, cls.cap_freezer.ids)])
        cls.p_alcohol = product("Bir (uji)", efn_required_capability_ids=[(6, 0, cls.cap_alcohol.ids)])
        cls.p_region = product("Region (uji)")
        cls.p_dsd = product("Roti DSD (uji)")
        supplier = env["res.partner"].create({"name": "Supplier uji"})
        env["product.supplierinfo"].create(
            {
                # company eksplisit: default = env.company (PT lain) -> kontrak tidak berlaku untuk outlet uji.
                "company_id": cls.company.id,
                "partner_id": supplier.id,
                "product_tmpl_id": cls.p_region.product_tmpl_id.id,
                "efn_region_ids": [(6, 0, cls.region_a.ids)],
            }
        )
        Line = env["efn.assortment.line"]
        base = {"company_id": cls.company.id, "warehouse_id": cls.outlet.id}
        Line.create(dict(base, categ_id=cls.categ.id, mode="allow"))  # izinkan seluruh kategori induk
        Line.create(dict(base, product_tmpl_id=cls.p_denied.product_tmpl_id.id, mode="deny"))
        Line.create(
            dict(
                base,
                product_tmpl_id=cls.p_dsd.product_tmpl_id.id,
                mode="allow",
                supply_mode="dsd",
                dsd_partner_id=supplier.id,
            )
        )
        cls.manager = new_test_user(
            env,
            login="efn_assort_mgr_uji",
            groups="efn_assortment.group_assortment_manager,stock.group_stock_manager",
            company_id=cls.company.id,
        )
        cls.clerk = new_test_user(env, login="efn_assort_clerk_uji", groups="stock.group_stock_user",
                                  company_id=cls.company.id)
        env["ir.config_parameter"].sudo().set_param("efn_assortment.enforce", "true")

    def allowed(self, product, warehouse=None):
        return self.env["efn.assortment.line"]._efn_assortment_allowed(product, warehouse or self.outlet)

    def test_reason_codes(self):
        self.assertEqual(self.allowed(self.p_ok), (True, "ok"))
        self.assertEqual(self.allowed(self.p_unlisted), (False, "not_listed"))
        self.assertEqual(self.allowed(self.p_denied), (False, "denied"))
        self.assertEqual(self.allowed(self.p_frozen), (False, "missing_capability:freezer"))
        self.assertEqual(self.allowed(self.p_alcohol), (False, "missing_capability:alcohol_license"))
        self.assertEqual(self.allowed(self.p_region), (False, "supplier_region"))
        self.assertEqual(self.allowed(self.p_dsd), (False, "dsd_only"))
        # Capability ditambah / region kontrak mencakup outlet -> lolos.
        self.outlet.efn_capability_ids = [(4, self.cap_freezer.id)]
        self.assertEqual(self.allowed(self.p_frozen), (True, "ok"))
        self.p_region.seller_ids.efn_region_ids = [(4, self.region_b.id)]
        self.assertEqual(self.allowed(self.p_region), (True, "ok"))

    def test_open_policy_and_deny_wins(self):
        self.outlet.efn_assortment_policy = "open"
        self.assertEqual(self.allowed(self.p_unlisted), (True, "ok"))
        self.assertEqual(self.allowed(self.p_denied), (False, "denied"))

    def test_line_constraints(self):
        Line = self.env["efn.assortment.line"]
        with self.assertRaises(ValidationError):
            Line.create({"company_id": self.company.id, "warehouse_id": self.outlet.id, "mode": "allow"})
        with self.assertRaises(ValidationError):
            Line.create(
                {
                    "company_id": self.company.id,
                    "warehouse_id": self.outlet.id,
                    "categ_id": self.categ.id,
                    "mode": "allow",
                    "supply_mode": "dsd",
                }
            )

    def _transfer(self, *products):
        # with_company + company_id per move: tanpa itu move mendapat company env (CE _check_company gagal dan
        # record rule menyembunyikan move dari user PT uji).
        picking = self.env["stock.picking"].with_company(self.company).create(
            {
                "picking_type_id": self.dc.int_type_id.id,
                "location_id": self.dc.lot_stock_id.id,
                "location_dest_id": self.outlet.lot_stock_id.id,
                "company_id": self.company.id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": p.id,
                            "company_id": self.company.id,
                            "product_uom_qty": 1,
                            "location_id": self.dc.lot_stock_id.id,
                            "location_dest_id": self.outlet.lot_stock_id.id,
                        },
                    )
                    for p in products
                ],
            }
        )
        return picking

    def test_confirm_dc_to_outlet_blocked_with_codes(self):
        ok = self._transfer(self.p_ok)
        ok.action_confirm()
        self.assertNotEqual(ok.state, "draft")
        bad = self._transfer(self.p_ok, self.p_frozen, self.p_denied)
        with self.assertRaisesRegex(UserError, "efn_assortment:blocked") as ctx:
            bad.action_confirm()
        message = str(ctx.exception)
        self.assertIn("missing_capability:freezer", message)
        self.assertIn("denied", message)
        self.assertIn("AOT1", message)

    def test_non_dc_source_not_enforced(self):
        self.dc.efn_is_dc = False
        picking = self._transfer(self.p_frozen)
        picking.action_confirm()
        self.assertNotEqual(picking.state, "draft")

    def test_enforce_param_off(self):
        self.env["ir.config_parameter"].sudo().set_param("efn_assortment.enforce", "false")
        picking = self._transfer(self.p_denied)
        picking.action_confirm()
        self.assertNotEqual(picking.state, "draft")
        # Nilai selain false/0 tetap menegakkan (fail closed).
        self.env["ir.config_parameter"].sudo().set_param("efn_assortment.enforce", "maybe")
        with self.assertRaisesRegex(UserError, "efn_assortment:blocked"):
            self._transfer(self.p_denied).action_confirm()

    def test_override_by_manager_except_protected(self):
        picking = self._transfer(self.p_frozen, self.p_alcohol)
        with self.assertRaises(AccessError):
            picking.with_user(self.clerk).action_efn_assortment_override()
        action = picking.with_user(self.manager).action_efn_assortment_override()
        self.assertEqual(action["res_model"], "efn.assortment.override.wizard")
        Wizard = self.env["efn.assortment.override.wizard"].with_user(self.manager)
        with self.assertRaisesRegex(UserError, "reason"):
            Wizard.create({"picking_id": picking.id, "reason": "pendek"}).action_confirm()
        Wizard.create({"picking_id": picking.id, "reason": "Persetujuan category manager no. 12/2026"}).action_confirm()
        overrides = self.env["efn.assortment.override"].search([("picking_id", "=", picking.id)])
        self.assertEqual(overrides.mapped("reason_code"), ["missing_capability:freezer"])
        # Alkohol tetap memblokir (tanpa override, L-150).
        with self.assertRaisesRegex(UserError, "alcohol_license"):
            picking.action_confirm()
        # Tanpa baris alkohol -> override freezer berlaku.
        picking.move_ids.filtered(lambda m: m.product_id == self.p_alcohol).unlink()
        picking.action_confirm()
        self.assertNotEqual(picking.state, "draft")
        with self.assertRaises(UserError):
            overrides.write({"reason": "ubah"})
        with self.assertRaises(ValidationError):
            self.env["efn.assortment.override"].create(
                {
                    "company_id": self.company.id,
                    "picking_id": picking.id,
                    "warehouse_id": self.outlet.id,
                    "product_id": self.p_alcohol.id,
                    "reason_code": "missing_capability:non_halal_allowed",
                    "reason": "Tidak boleh di-override sama sekali",
                }
            )

    def test_daily_violation_report(self):
        self.outlet.resupply_wh_ids = [(4, self.dc.id)]
        self.env["stock.quant"]._update_available_quantity(self.p_denied, self.outlet.lot_stock_id, 5)
        self.env["stock.quant"]._update_available_quantity(self.p_dsd, self.outlet.lot_stock_id, 5)
        self.env["stock.quant"]._update_available_quantity(self.p_ok, self.outlet.lot_stock_id, 5)
        Violation = self.env["efn.assortment.violation"]
        Violation._cron_efn_daily_report()
        rows = Violation.search([("warehouse_id", "=", self.outlet.id)])
        self.assertEqual(rows.mapped("product_id"), self.p_denied, "DSD & produk diizinkan bukan pelanggaran")
        self.assertEqual((rows.reason_code, rows.source, rows.qty), ("denied", "stock", 5))
        Violation._cron_efn_daily_report()
        self.assertEqual(Violation.search_count([("warehouse_id", "=", self.outlet.id)]), 1, "idempoten per hari")

    def test_sec208_guarded_fields_manager_only_and_audited(self):
        """SEC-208: Inventory Administrator tanpa Assortment Manager tidak bisa mengubah capability/DC/kebijakan."""
        admin = new_test_user(
            self.env, login="efn_assort_invadmin_uji", groups="stock.group_stock_manager", company_id=self.company.id
        )
        outlet = self.outlet.with_user(admin)
        for vals in (
            {"efn_capability_ids": [(4, self.cap_alcohol.id)]},
            {"efn_is_dc": True},
            {"efn_assortment_policy": "open"},
        ):
            with self.subTest(vals=vals), self.assertRaisesRegex(AccessError, "manager_only"):
                outlet.write(vals)
        with self.assertRaisesRegex(AccessError, "manager_only"):
            self.p_alcohol.product_tmpl_id.with_user(admin).write({"efn_required_capability_ids": [(5, 0, 0)]})
        with self.assertRaisesRegex(AccessError, "manager_only"):
            self.env["stock.warehouse"].with_user(admin).create(
                {"name": "Gudang baru uji", "code": "AGB1", "company_id": self.company.id, "efn_is_dc": True}
            )
        # Field lain tetap bisa ditulis Inventory Administrator.
        outlet.write({"name": "Outlet tertutup uji (ganti nama)"})
        # Assortment Manager boleh; perubahan tercatat efn_audit (nilai lama/baru).
        self.env.registry.clear_cache()
        self.outlet.with_user(self.manager).write({"efn_assortment_policy": "open"})
        logs = self.env["efn.audit.log"].sudo().search(
            [("model_name", "=", "stock.warehouse"), ("res_id", "=", self.outlet.id), ("operation", "=", "write")]
        )
        line = logs.line_ids.filtered(lambda line: line.field_name == "efn_assortment_policy")
        # efn_audit menyimpan label Selection.
        self.assertIn("Tertutup", line.old_value)
        self.assertIn("Terbuka", line.new_value)

    def test_qa1_post_init_hook_marks_smd_dc(self):
        from odoo.addons.efn_assortment import post_init_hook

        dc = self.env.ref("efn_setup_supermarket.warehouse_smd_dc", raise_if_not_found=False)
        if not dc:
            post_init_hook(self.env)  # tanpa efn_setup_supermarket: tidak gagal
            return
        dc.sudo().efn_is_dc = False
        post_init_hook(self.env)
        self.assertTrue(dc.efn_is_dc)
