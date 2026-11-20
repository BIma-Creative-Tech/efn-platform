"""efn_setup_supermarket tahap A (G44) + tahap B (G45) (docs/04 §28.1; fit-gap G45 S-01..S-16; ADR-045/046, R-L14).

Data (selalu dimuat): company SMD, CoA/PPN, nomenklatur GS1 + fallback, gudang DC (efn_is_dc), kategori FEFO, ambang
kedaluwarsa per kategori (placeholder Q-SM28/Q-SM9), sakelar fitur tetap mati.
Demo (hanya DB --with-demo): 5 outlet SMD01..SMD05 dipasok DC; capability & kebijakan assortment; zona + cycle count +
putaway; jadwal kirim; user per persona (kepala toko berscope outlet, buyer, markdown manager, supervisor).
19.0.3.0.0 (G47): outlet demo penerimaan dua langkah (Q-G47-2) + rantai resupply runtime; persona EFN Insight di user
demo (grup efn_insight_sm, fit-gap docs/fit-gap/G47_INSIGHT_SM.md §2.2).
"""

import json
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

MODULE = "efn_setup_supermarket"

CATEGORY_XMLIDS = (
    "product_category_smd_root",
    "product_category_smd_fresh",
    "product_category_smd_fresh_vegetable",
    "product_category_smd_fresh_fruit",
    "product_category_smd_fresh_meat",
    "product_category_smd_fresh_fish",
    "product_category_smd_fresh_bakery",
    "product_category_smd_dairy_chilled",
    "product_category_smd_frozen",
    "product_category_smd_dry_grocery",
    "product_category_smd_beverage",
    "product_category_smd_snack",
    "product_category_smd_personal_care",
    "product_category_smd_household",
    "product_category_smd_alcohol",
    "product_category_smd_imported_premium",
)

OUTLET_CODES = ("SMD01", "SMD02", "SMD03", "SMD04", "SMD05")

# Tahap B: (near_expiry, markdown, min_receipt) PLACEHOLDER Q-SM28/Q-SM9 (data/product_category_expiry_data.xml).
THRESHOLDS = {
    "product_category_smd_root": (30, 14, 30),
    "product_category_smd_fresh": (2, 1, 2),
    "product_category_smd_fresh_vegetable": (2, 1, 2),
    "product_category_smd_fresh_fruit": (2, 1, 2),
    "product_category_smd_fresh_meat": (1, 1, 1),
    "product_category_smd_fresh_fish": (1, 1, 1),
    "product_category_smd_fresh_bakery": (1, 1, 1),
    "product_category_smd_dairy_chilled": (5, 3, 5),
    "product_category_smd_frozen": (30, 14, 60),
    "product_category_smd_dry_grocery": (60, 30, 90),
    "product_category_smd_beverage": (60, 30, 90),
    "product_category_smd_snack": (45, 21, 60),
    "product_category_smd_personal_care": (90, 45, 120),
    "product_category_smd_household": (90, 45, 120),
    "product_category_smd_alcohol": (90, 45, 120),
    "product_category_smd_imported_premium": (30, 14, 45),
}

# Demo: capability & kebijakan outlet (demo/efn_assortment_demo.xml).
CAPABILITIES = {
    "SMD01": {"chiller", "freezer", "alcohol_license", "non_halal_allowed"},
    "SMD02": {"chiller", "freezer"},
    "SMD03": {"chiller", "freezer"},
    "SMD04": {"chiller", "freezer", "alcohol_license", "non_halal_allowed"},
    "SMD05": {"chiller", "non_halal_allowed"},
}
POLICIES = {"SMD01": "open", "SMD02": "open", "SMD03": "closed", "SMD04": "open", "SMD05": "open"}

# Demo: kategori jadwal kirim per outlet (demo/efn_replenishment_demo.xml).
_BASE = ("fresh", "dairy_chilled", "dry_grocery", "beverage", "snack", "personal_care", "household")
SCHEDULE_CATEGORIES = {
    "SMD01": set(_BASE) | {"frozen", "alcohol", "imported_premium"},
    "SMD02": set(_BASE) | {"frozen"},
    "SMD03": set(_BASE) | {"frozen"},
    "SMD04": set(_BASE) | {"frozen", "alcohol", "imported_premium"},
    "SMD05": set(_BASE) | {"imported_premium"},
}
ZONE_FREQUENCY = {"fresh": 7, "chiller": 14, "freezer": 30, "dry": 30}

# G47 (19.0.3.0.0): persona Insight per user demo (demo/res_users_insight_demo.xml; xmlid fit-gap G47 §2.2).
PERSONA_GROUPS = {
    persona: f"efn_insight_sm.group_insight_{persona}" for persona in ("exec", "category", "store", "dc", "marketing")
}
PERSONA_USERS = {
    **{f"user_smd0{i}_store_head": {"store"} for i in range(1, 6)},
    "user_smd_buyer_dc": {"dc"},
    "user_smd_markdown_manager": {"category"},
    "user_smd_director": {"exec"},
    "user_smd_marketing": {"marketing"},
}
ACTION_GROUPS = (
    "efn_replenishment.group_replenishment_store",
    "efn_replenishment.group_replenishment_buyer",
    "efn_replenishment.group_replenishment_admin",
    "efn_expiry_monitor.group_markdown_manager",
    "efn_expiry_monitor.group_expiry_override",
    "efn_assortment.group_assortment_manager",
    "purchase.group_purchase_user",
)


@tagged("post_install", "-at_install", "efn")
class TestSetupSupermarket(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref(f"{MODULE}.company_smd")
        cls.dc = cls.env.ref(f"{MODULE}.warehouse_smd_dc")
        cls.fefo = cls.env.ref("product_expiry.removal_fefo")

    # --- S-01 company & pajak -------------------------------------------------------------------------------
    def test_company_code_and_business_type(self):
        self.assertEqual(self.company.efn_brand_code, "SMD")
        # Compute stored efn_core (pemetaan SMD -> retail ditambahkan efn-odoo-engineer, docs/04 §12.1).
        self.assertEqual(self.company.efn_business_type, "retail")
        self.assertFalse(self.company.parent_id, "company ROOT, bukan Branch (docs/04 §12.1)")
        self.assertEqual(self.company.country_id, self.env.ref("base.id"))
        self.assertEqual(self.company.currency_id, self.env.ref("base.IDR"))
        self.assertIn("PLACEHOLDER", self.company.name)
        self.assertFalse(self.company.vat_check_vies)

    def test_company_ppn_from_l10n_id(self):
        self.assertEqual(self.company.chart_template, "id")
        tax = self.company.account_sale_tax_id
        self.assertTrue(tax, "pajak penjualan default dari template l10n_id")
        self.assertEqual(tax.invoice_label, "PPN 11%")
        self.assertEqual(tax.tax_group_id.name, "PPN 11%")
        self.assertEqual(self.company.tax_calculation_rounding_method, "round_globally")
        exempt = self.env.ref(f"account.{self.company.id}_tax_ST2")
        self.assertEqual(exempt.invoice_label, "Bebas PPN")

    def test_admin_can_switch_to_company(self):
        self.assertIn(self.company, self.env.ref("base.user_admin").company_ids)

    # --- S-07 nomenklatur --------------------------------------------------------------------------------------
    def test_gs1_nomenclature_with_retail_fallback(self):
        self.assertTrue(self.company.nomenclature_id.is_gs1_nomenclature)
        default = self.env["ir.default"].search(
            [
                ("field_id.model", "=", "pos.config"),
                ("field_id.name", "=", "fallback_nomenclature_id"),
                ("company_id", "=", self.company.id),
                ("user_id", "=", False),
            ]
        )
        self.assertEqual(len(default), 1)
        self.assertEqual(
            json.loads(default.json_value),
            self.env.ref("efn_setup_pos.barcode_nomenclature_efn_retail").id,
        )

    # --- S-02 DC -------------------------------------------------------------------------------------------------
    def test_dc_warehouse(self):
        self.assertEqual(self.dc.code, "SMDDC")
        self.assertEqual(self.dc.company_id, self.company)
        self.assertEqual(self.dc.reception_steps, "two_steps")
        self.assertEqual(self.dc.delivery_steps, "pick_ship")
        self.assertTrue(self.company.internal_transit_location_id, "transit internal untuk rute resupply")

    def test_inventory_groups(self):
        implied = self.env.ref("base.group_user").implied_ids
        for xmlid in ("stock.group_production_lot", "stock.group_stock_multi_locations", "stock.group_adv_location"):
            with self.subTest(group=xmlid):
                self.assertIn(self.env.ref(xmlid), implied)

    # --- S-05/S-06 kategori FEFO ---------------------------------------------------------------------------------
    def test_categories_fefo(self):
        for xmlid in CATEGORY_XMLIDS:
            with self.subTest(category=xmlid):
                self.assertEqual(self.env.ref(f"{MODULE}.{xmlid}").removal_strategy_id, self.fefo)

    def test_removal_strategy_resolves_fefo_in_dc(self):
        # stock.quant._get_removal_strategy hanya membaca kategori langsung (tidak menelusuri induk) [V 19.0].
        product = self.env["product.product"].create(
            {
                "name": "Susu UHT uji FEFO",
                "type": "consu",
                "is_storable": True,
                "tracking": "lot",
                "use_expiration_date": True,
                "categ_id": self.env.ref(f"{MODULE}.product_category_smd_dairy_chilled").id,
                "company_id": self.company.id,
            }
        )
        method = self.env["stock.quant"]._get_removal_strategy(product, self.dc.lot_stock_id)
        self.assertEqual(method, "fefo")

    # --- Demo: outlet + resupply DC (S-03) & lot POS (§2) ---------------------------------------------------------
    def _outlets(self):
        outlets = self.env["stock.warehouse"]
        for code in OUTLET_CODES:
            outlet = self.env.ref(f"{MODULE}.warehouse_{code.lower()}", raise_if_not_found=False)
            if not outlet:
                self.skipTest("demo efn_setup_supermarket tidak dimuat")
            outlets |= outlet
        return outlets

    def test_demo_outlets_resupplied_from_dc(self):
        outlets = self._outlets()
        self.assertEqual(sorted(outlets.mapped("code")), list(OUTLET_CODES))
        transit = self.company.internal_transit_location_id
        for outlet in outlets:
            with self.subTest(outlet=outlet.code):
                self.assertEqual(outlet.company_id, self.company)
                self.assertEqual(outlet.resupply_wh_ids, self.dc)
                routes = outlet.resupply_route_ids.filtered(lambda r, dc=self.dc: r.supplier_wh_id == dc)
                self.assertEqual(len(routes), 1, "satu rute 'Supply Product from DC' per outlet")
                self.assertTrue(routes.active)
                self.assertTrue(routes.warehouse_selectable)
                self.assertEqual(routes.company_id, self.company)
                locations = routes.rule_ids.mapped("location_src_id") | routes.rule_ids.mapped("location_dest_id")
                self.assertIn(transit, locations, "DC -> transit -> outlet (stok in-transit terlihat)")

    def test_demo_outlets_distinct_cities(self):
        outlets = self._outlets()
        self.assertEqual(len(set(outlets.mapped("partner_id.city"))), 5)
        self.assertNotIn(self.dc.partner_id.city, outlets.mapped("partner_id.city"))

    def test_demo_outlet_pos_lot_flags(self):
        for outlet in self._outlets():
            with self.subTest(outlet=outlet.code):
                pos_type = outlet.pos_type_id
                self.assertTrue(pos_type)
                self.assertTrue(pos_type.use_existing_lots)
                self.assertFalse(pos_type.use_create_lots)

    def test_demo_price_include(self):
        self._outlets()  # skip bila demo tidak dimuat
        self.assertEqual(self.company.account_price_include, "tax_included")

    # ============================================ Tahap B (G45) ================================================
    def _demo_loaded(self):
        return bool(self.env.ref(f"{MODULE}.warehouse_smd01", raise_if_not_found=False))

    def _product(self, categ_xmlid, capabilities=()):
        caps = self.env["efn.assortment.capability"].search([("code", "in", list(capabilities))])
        self.assertEqual(len(caps), len(capabilities))
        template = self.env["product.template"].create(
            {
                "name": f"Produk uji {categ_xmlid}",
                "type": "consu",
                "is_storable": True,
                "tracking": "lot",
                "use_expiration_date": True,
                "categ_id": self.env.ref(f"{MODULE}.{categ_xmlid}").id,
                "company_id": self.company.id,
                "efn_required_capability_ids": [(6, 0, caps.ids)],
            }
        )
        return template.product_variant_id

    # --- Ambang per kategori (S-08, S-10; docs/04 §28.5a) --------------------------------------------------------
    def test_category_expiry_thresholds(self):
        self.assertEqual(set(THRESHOLDS), set(CATEGORY_XMLIDS), "semua 16 kategori punya ambang")
        for xmlid, (near, markdown, receipt) in THRESHOLDS.items():
            with self.subTest(category=xmlid):
                categ = self.env.ref(f"{MODULE}.{xmlid}")
                self.assertEqual(
                    (categ.efn_near_expiry_days, categ.efn_markdown_days, categ.efn_min_receipt_days),
                    (near, markdown, receipt),
                )
                self.assertGreater(markdown, 0)
                self.assertLessEqual(markdown, near, "markdown hanya untuk lot yang sudah near-expiry")
                self.assertGreaterEqual(receipt, near, "outlet tidak menerima lot yang langsung near-expiry")

    def test_replenishment_reads_min_receipt_days(self):
        # L-151: shelf-life cap efn_replenishment memakai efn_min_receipt_days kategori (bila efn_expiry_monitor ada).
        product = self._product("product_category_smd_dairy_chilled")
        self.assertEqual(self.env["efn.replenishment.proposal"]._efn_min_remaining_days(product), 5)

    # --- DC & sakelar fitur ---------------------------------------------------------------------------------------
    def test_dc_marked_for_assortment(self):
        self.assertTrue(self.dc.efn_is_dc)

    def test_feature_flags(self):
        icp = self.env["ir.config_parameter"].sudo()
        # docs/04 §28.10: tetap mati sampai Q-SM28/Q-SM9 dijawab, juga di DB demo.
        self.assertEqual((icp.get_param("efn_expiry_monitor.enabled") or "false").strip().lower(), "false")
        expected = "true" if self._demo_loaded() else "false"
        self.assertEqual((icp.get_param("efn_replenishment.enabled") or "false").strip().lower(), expected)
        self.assertEqual((icp.get_param("efn_assortment.enforce") or "true").strip().lower(), "true")

    # --- Demo: capability & assortment (S-11, L-150) --------------------------------------------------------------
    def test_demo_capability_matrix(self):
        for outlet in self._outlets():
            with self.subTest(outlet=outlet.code):
                self.assertFalse(outlet.efn_is_dc, "outlet bukan DC")
                self.assertEqual(set(outlet.efn_capability_ids.mapped("code")), CAPABILITIES[outlet.code])
                self.assertEqual(outlet.efn_assortment_policy, POLICIES[outlet.code])
        licensed = self._outlets().filtered(lambda w: "alcohol_license" in w.efn_capability_ids.mapped("code"))
        self.assertEqual(sorted(licensed.mapped("code")), ["SMD01", "SMD04"])

    def test_demo_assortment_scenarios(self):
        outlets = {w.code: w for w in self._outlets()}
        frozen = self._product("product_category_smd_frozen", ("freezer",))
        alcohol = self._product("product_category_smd_alcohol", ("alcohol_license",))
        imported = self._product("product_category_smd_imported_premium")
        dry = self._product("product_category_smd_dry_grocery")
        cases = [
            (frozen, "SMD01", (True, "ok")),
            (frozen, "SMD05", (False, "missing_capability:freezer")),
            (alcohol, "SMD04", (True, "ok")),
            (alcohol, "SMD02", (False, "missing_capability:alcohol_license")),
            (alcohol, "SMD03", (False, "not_listed")),
            (imported, "SMD02", (False, "denied")),
            (imported, "SMD05", (True, "ok")),
            (dry, "SMD03", (True, "ok")),
        ]
        for product, code, expected in cases:
            with self.subTest(product=product.categ_id.name, outlet=code):
                self.assertEqual(outlets[code]._efn_assortment_allowed(product), expected)

    # --- Demo: zona, cycle count, putaway (S-15, S-16) ------------------------------------------------------------
    def test_demo_zones_cycle_count(self):
        for outlet in self._outlets():
            for zone, frequency in ZONE_FREQUENCY.items():
                with self.subTest(outlet=outlet.code, zone=zone):
                    location = self.env.ref(f"{MODULE}.location_{outlet.code.lower()}_{zone}", raise_if_not_found=False)
                    if outlet.code == "SMD05" and zone == "freezer":
                        self.assertFalse(location, "SMD05 tanpa freezer")
                        continue
                    self.assertTrue(location)
                    self.assertEqual(location.location_id, outlet.lot_stock_id)
                    self.assertEqual(location.usage, "internal")
                    self.assertEqual(location.company_id, self.company)
                    self.assertEqual(location.cyclic_inventory_frequency, frequency)
                    self.assertTrue(location.next_inventory_date)
        self.assertEqual(self.dc.lot_stock_id.cyclic_inventory_frequency, 30)

    def test_demo_putaway_by_category(self):
        outlet = self.env.ref(f"{MODULE}.warehouse_smd01", raise_if_not_found=False)
        if not outlet:
            self.skipTest("demo efn_setup_supermarket tidak dimuat")
        cases = [
            ("product_category_smd_fresh_vegetable", "location_smd01_fresh"),  # rule induk Fresh
            ("product_category_smd_dairy_chilled", "location_smd01_chiller"),
            ("product_category_smd_frozen", "location_smd01_freezer"),
            ("product_category_smd_snack", "location_smd01_dry"),
        ]
        for categ, zone in cases:
            with self.subTest(category=categ):
                product = self._product(categ)
                self.assertEqual(outlet.lot_stock_id._get_putaway_strategy(product), self.env.ref(f"{MODULE}.{zone}"))

    # --- Demo: jadwal kirim (S-12, Q-SM11) ------------------------------------------------------------------------
    def test_demo_delivery_schedules(self):
        prefix = "product_category_smd_"
        for outlet in self._outlets():
            with self.subTest(outlet=outlet.code):
                schedules = self.env["efn.delivery.schedule"].search([("warehouse_id", "=", outlet.id)])
                keys = [
                    self.env["ir.model.data"]
                    .search(
                        [
                            ("model", "=", "product.category"),
                            ("res_id", "=", s.product_group.id),
                            ("module", "=", MODULE),
                        ],
                        limit=1,
                    )
                    .name.removeprefix(prefix)
                    for s in schedules
                ]
                self.assertEqual(len(keys), len(set(keys)), "kategori jadwal per outlet tidak boleh dobel")
                self.assertEqual(set(keys), SCHEDULE_CATEGORIES[outlet.code])
                for schedule, key in zip(schedules, keys, strict=True):
                    self.assertEqual(schedule.supply_warehouse_id, self.dc)
                    self.assertEqual(schedule.company_id, self.company)
                    self.assertEqual(schedule.lead_days, 2 if outlet.code == "SMD05" else 1)
                    self.assertEqual(schedule.tz, "Asia/Makassar" if outlet.code == "SMD05" else "Asia/Jakarta")
                    if key in ("fresh", "dairy_chilled"):
                        self.assertEqual((schedule.weekdays, schedule.cutoff_time), (127, 21.0))
                    elif key == "frozen":
                        self.assertEqual((schedule.weekdays, schedule.cutoff_time), (18, 17.0))
                    else:
                        self.assertEqual((schedule.weekdays, schedule.cutoff_time), (21, 17.0))
                    delivery_date, cutoff = schedule._efn_next_delivery()
                    self.assertTrue(delivery_date and cutoff)

    # --- Demo: user per persona & scope (§28.4, §28.11) -----------------------------------------------------------
    def test_demo_users_scope(self):
        store_group = self.env.ref("efn_replenishment.group_replenishment_store")
        buyer_group = self.env.ref("efn_replenishment.group_replenishment_buyer")
        purchase_group = self.env.ref("purchase.group_purchase_user")
        for outlet in self._outlets():
            with self.subTest(outlet=outlet.code):
                user = self.env.ref(f"{MODULE}.user_{outlet.code.lower()}_store_head")
                self.assertFalse(user.share)
                self.assertEqual(user.company_ids, self.company)
                self.assertEqual(user.efn_outlet_warehouse_ids, outlet)
                self.assertTrue(user.has_group("efn_replenishment.group_replenishment_store"))
                self.assertNotIn(buyer_group, user.all_group_ids, "SoD: kepala toko bukan buyer")
                self.assertNotIn(purchase_group, user.all_group_ids, "SoD: kepala toko tidak mengonfirmasi PO")
        buyer = self.env.ref(f"{MODULE}.user_smd_buyer_dc")
        self.assertTrue(buyer.has_group("efn_replenishment.group_replenishment_buyer"))
        self.assertTrue(buyer.has_group("purchase.group_purchase_user"))
        self.assertNotIn(store_group, buyer.all_group_ids)
        markdown = self.env.ref(f"{MODULE}.user_smd_markdown_manager")
        self.assertTrue(markdown.has_group("efn_expiry_monitor.group_markdown_manager"))
        supervisor = self.env.ref(f"{MODULE}.user_smd_supervisor_ops")
        self.assertTrue(supervisor.has_group("efn_expiry_monitor.group_expiry_override"))
        self.assertTrue(supervisor.has_group("efn_assortment.group_assortment_manager"))
        self.assertNotIn(store_group, supervisor.all_group_ids, "SoD: override bukan kepala toko penerima")

    def test_demo_store_head_sees_only_own_outlet(self):
        outlets = {w.code: w for w in self._outlets()}
        Proposal = self.env["efn.replenishment.proposal"].sudo()
        Schedule = self.env["efn.delivery.schedule"]
        fresh = self.env.ref(f"{MODULE}.product_category_smd_fresh")
        delivery = fields.Date.context_today(self.env.user) + timedelta(days=20)
        proposals = Proposal.browse()
        for code in ("SMD01", "SMD02"):
            schedule = Schedule.search([("warehouse_id", "=", outlets[code].id), ("product_group", "=", fresh.id)])
            proposals |= Proposal.create(
                {
                    "company_id": self.company.id,
                    "warehouse_id": outlets[code].id,
                    "supply_warehouse_id": self.dc.id,
                    "schedule_id": schedule.id,
                    "delivery_date": delivery,
                    "cutoff_at": fields.Datetime.now() + timedelta(days=19),
                    "state": "proposed",
                }
            )
        head01 = self.env.ref(f"{MODULE}.user_smd01_store_head")
        visible = self.env["efn.replenishment.proposal"].with_user(head01).search([("id", "in", proposals.ids)])
        self.assertEqual(visible.warehouse_id, outlets["SMD01"])
        buyer = self.env.ref(f"{MODULE}.user_smd_buyer_dc")
        self.assertEqual(
            self.env["efn.replenishment.proposal"].with_user(buyer).search([("id", "in", proposals.ids)]), proposals
        )

    # ======================================= 19.0.3.0.0 (G47) ===============================================
    # --- Outlet penerimaan dua langkah (Q-G47-2) -----------------------------------------------------------------
    def test_demo_outlets_two_step_reception(self):
        transit = self.company.internal_transit_location_id
        for outlet in self._outlets():
            with self.subTest(outlet=outlet.code):
                self.assertEqual(outlet.reception_steps, "two_steps")
                self.assertEqual(outlet.delivery_steps, "ship_only", "outlet tidak mengirim ke pelanggan lewat stock")
                input_loc = outlet.wh_input_stock_loc_id
                self.assertTrue(input_loc.active, "lokasi Input aktif untuk two_steps")
                self.assertEqual(input_loc.warehouse_id, outlet)
                self.assertFalse(
                    input_loc.parent_path.startswith(outlet.lot_stock_id.parent_path),
                    "Input bukan anak Stock: POS (child_of Stock) tidak menjual barang yang belum disimpan",
                )
                # _get_input_output_locations / _get_picking_type_update_values [V 19.0].
                self.assertEqual(outlet.in_type_id.default_location_dest_id, input_loc)
                push = self.env["stock.rule"].search(
                    [
                        ("action", "=", "push"),
                        ("location_src_id", "=", input_loc.id),
                        ("location_dest_id", "=", outlet.lot_stock_id.id),
                        ("picking_type_id", "=", outlet.store_type_id.id),
                    ]
                )
                self.assertTrue(push, "rule push Input -> Stock (get_rules_dict 'two_steps')")
                # Leg resupply transit -> outlet tetap bertujuan Stock (rute resupply tidak bergantung reception).
                route = outlet.resupply_route_ids.filtered(lambda r, dc=self.dc: r.supplier_wh_id == dc)
                leg = route.rule_ids.filtered(lambda r, t=transit: r.location_src_id == t)
                self.assertEqual(len(leg), 1)
                self.assertEqual(leg.location_dest_id, outlet.lot_stock_id)
                self.assertEqual(leg.picking_type_id, outlet.in_type_id)
                self.assertEqual(leg.action, "pull")
        # DC tidak berubah (tahap A): two_steps + pick_ship.
        self.assertEqual((self.dc.reception_steps, self.dc.delivery_steps), ("two_steps", "pick_ship"))

    def test_demo_resupply_chain_two_steps(self):
        """Usulan disetujui (jalur efn_replenishment: orderpoint manual + action_replenish) -> pick -> ship -> receipt
        ke Input -> storage (push) ke zona Stock. Mengunci asumsi fit-gap G47 §6.1 (C-3) secara runtime."""
        outlet = self.env.ref(f"{MODULE}.warehouse_smd01", raise_if_not_found=False)
        if not outlet:
            self.skipTest("demo efn_setup_supermarket tidak dimuat")
        transit = self.company.internal_transit_location_id
        product = self.env["product.product"].create(
            {
                "name": "Mi instan uji dua langkah",
                "type": "consu",
                "is_storable": True,
                "categ_id": self.env.ref(f"{MODULE}.product_category_smd_dry_grocery").id,
                "company_id": self.company.id,
            }
        )
        self.env["stock.quant"]._update_available_quantity(product, self.dc.lot_stock_id, 10)
        route = outlet.resupply_route_ids.filtered(lambda r: r.supplier_wh_id == self.dc)
        Orderpoint = self.env["stock.warehouse.orderpoint"].with_company(self.company)
        op = Orderpoint._efn_managed_orderpoint(outlet, product, route)
        op.write({"qty_to_order_manual": 4})
        op.action_replenish()

        moves = self.env["stock.move"].search([("product_id", "=", product.id)])
        pick = moves.filtered(lambda m: m.location_id == self.dc.lot_stock_id)
        ship = moves.filtered(lambda m: m.location_dest_id == transit)
        receipt = moves.filtered(lambda m: m.location_id == transit)
        self.assertEqual((len(pick), len(ship), len(receipt)), (1, 1, 1), "tiga leg: pick, ship, receipt")
        self.assertEqual(pick.location_dest_id, self.dc.wh_output_stock_loc_id)
        self.assertEqual(ship.location_id, self.dc.wh_output_stock_loc_id)
        self.assertEqual(receipt.location_dest_id, outlet.wh_input_stock_loc_id, "receipt mendarat di Input")
        self.assertEqual(receipt.location_final_id, outlet.lot_stock_id)
        self.assertEqual(receipt.picking_type_id, outlet.in_type_id)
        self.assertIn(ship, pick.move_dest_ids)
        self.assertIn(receipt, ship.move_dest_ids)
        self.assertEqual(sum(receipt.mapped("product_uom_qty")), 4)

        for move in (pick, ship, receipt):
            picking = move.picking_id
            picking.action_assign()
            picking.move_ids.picked = True
            picking.button_validate()
            self.assertEqual(picking.state, "done", f"{picking.picking_type_id.name} selesai")

        store = receipt.move_dest_ids
        self.assertEqual(len(store), 1, "push Input -> Stock dibuat setelah receipt selesai")
        self.assertEqual(
            (store.location_id, store.location_dest_id), (outlet.wh_input_stock_loc_id, outlet.lot_stock_id)
        )
        self.assertEqual(store.picking_type_id, outlet.store_type_id)
        store.picking_id.action_assign()
        self.assertEqual(
            store.move_line_ids.location_dest_id,
            self.env.ref(f"{MODULE}.location_smd01_dry"),
            "putaway kategori tetap berlaku pada langkah storage",
        )
        store.picking_id.move_ids.picked = True
        store.picking_id.button_validate()
        self.assertEqual(store.state, "done")

        # C-3: tepat satu move done yang MELINTASI batas outlet (receipt), walau dua move berujung di gudang outlet.
        done = self.env["stock.move"].search([("product_id", "=", product.id), ("state", "=", "done")])
        into_outlet = done.filtered(lambda m: m.location_dest_id.warehouse_id == outlet)
        crossing = into_outlet.filtered(lambda m: m.location_id.warehouse_id != outlet)
        self.assertEqual(len(into_outlet), 2)
        self.assertEqual(crossing, receipt)
        # efn_replenishment (_efn_create_supply_document): move DC pertama (urut id) = ship, bukan pick (fit-gap §6.1).
        self.assertEqual(moves.filtered(lambda m: m.location_id.warehouse_id == self.dc).sorted("id")[:1], ship)

    # --- Persona Insight (G47) -----------------------------------------------------------------------------------
    def test_demo_insight_personas(self):
        self._outlets()  # skip bila demo tidak dimuat
        insight_user = self.env.ref("efn_insight.group_insight_user")
        for xmlid, expected in PERSONA_USERS.items():
            with self.subTest(user=xmlid):
                user = self.env.ref(f"{MODULE}.{xmlid}")
                held = {p for p, group in PERSONA_GROUPS.items() if user.has_group(group)}
                self.assertEqual(held, expected)
                self.assertIn(insight_user, user.all_group_ids, "persona implies Insight: pengguna")
                self.assertEqual(user.company_ids, self.company)
                self.assertFalse(user.has_group("efn_insight_sm.group_insight_kpi_admin"), "kpi_admin menunggu Q-G47-1")
        # Persona tidak memberi grup tindakan: user baru hanya Internal User + persona.
        for xmlid in ("user_smd_director", "user_smd_marketing"):
            user = self.env.ref(f"{MODULE}.{xmlid}")
            for group in ACTION_GROUPS:
                with self.subTest(user=xmlid, group=group):
                    self.assertFalse(user.has_group(group))
        # Grup tindakan lama tetap (Command.link, tidak menimpa).
        self.assertTrue(self.env.ref(f"{MODULE}.user_smd01_store_head").has_group(ACTION_GROUPS[0]))
        self.assertTrue(self.env.ref(f"{MODULE}.user_smd_buyer_dc").has_group("purchase.group_purchase_user"))

    def test_demo_category_manager_categories(self):
        self._outlets()
        user = self.env.ref(f"{MODULE}.user_smd_markdown_manager")
        expected = {
            self.env.ref(f"{MODULE}.product_category_smd_{key}") for key in ("fresh", "dairy_chilled", "frozen")
        }
        self.assertEqual(set(user.efn_insight_categ_ids), expected)
        for xmlid, personas in PERSONA_USERS.items():
            if "category" not in personas:
                with self.subTest(user=xmlid):
                    self.assertFalse(self.env.ref(f"{MODULE}.{xmlid}").efn_insight_categ_ids)

    def test_demo_store_persona_scope_matches_outlet(self):
        # wids persona store = efn_outlet_warehouse_ids (docs/04 §28.13.3): satu outlet per kepala toko demo.
        for outlet in self._outlets():
            with self.subTest(outlet=outlet.code):
                user = self.env.ref(f"{MODULE}.user_{outlet.code.lower()}_store_head")
                self.assertTrue(user.has_group(PERSONA_GROUPS["store"]))
                self.assertEqual(user.efn_outlet_warehouse_ids, outlet)

    def test_persona_groups_one_per_privilege(self):
        # Fit-gap G47 §2.1 / C-2: widget hak akses Odoo 19 = satu pilihan per privilege; persona harus berada di
        # privilege sendiri agar user demo bisa memegang lebih dari satu persona tanpa hilang saat form disimpan.
        for persona, xmlid in PERSONA_GROUPS.items():
            with self.subTest(persona=persona):
                group = self.env.ref(xmlid)
                self.assertTrue(group.privilege_id)
                self.assertEqual(group.privilege_id.group_ids, group)
