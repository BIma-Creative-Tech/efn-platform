"""G44 tahap A efn_setup_supermarket (docs/04 §28.1; fit-gap G45 S-01..S-07; ADR-045, R-L14).

Data (selalu dimuat): company SMD, CoA/PPN, nomenklatur GS1 + fallback, gudang DC, kategori FEFO.
Demo (hanya DB --with-demo): 5 outlet SMD01..SMD05 dipasok DC lewat resupply_wh_ids CE.
"""

import json

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
