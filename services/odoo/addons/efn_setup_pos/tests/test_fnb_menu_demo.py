"""G22 W-27 (docs/08 §13.3; keputusan 18-22): modifier & menu paket F&B dengan fitur standar CE 19 - demo PB & BCF.

Hanya bila demo dimuat (`--with-demo`); tanpa demo test dilewati. Bukti end-to-end stok bahan turun dari picking POS =
tour/QA (docs/08 §13.3 [B]); di sini diuji konfigurasi + ledakan BoM Kit yang dipakai pos_mrp.
"""

from odoo.tests import TransactionCase, tagged

MODULE = "efn_setup_pos"


@tagged("post_install", "-at_install", "efn")
class TestEfnFnbMenuDemo(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.has_demo = bool(cls.env.ref(f"{MODULE}.product_w27_breakfast_set_pb", raise_if_not_found=False))

    def setUp(self):
        super().setUp()
        if not self.has_demo:
            self.skipTest("demo W-27 tidak dimuat")

    def _ref(self, xmlid):
        return self.env.ref(f"{MODULE}.{xmlid}")

    def _company(self, code):
        return self.env.ref({"pb": "efn_setup_company.company_fnb", "bcf": "efn_setup_company.company_bcf"}[code])

    def _pb1(self, code):
        return self.env.ref(f"efn_setup_company.tax_pb1_10_{'fnb' if code == 'pb' else 'bcf'}")

    # ------------------------------------------------------------ pola 1-2: atribut
    def test_modifiers_are_no_variant_attributes(self):
        """Modifier tidak membuat varian (satu product.product per menu) dan tidak menyentuh stok."""
        for xmlid in ("attr_w27_serving", "attr_w27_extra_shot", "attr_sugar"):
            with self.subTest(attribute=xmlid):
                self.assertEqual(self._ref(xmlid).create_variant, "no_variant")
        for xmlid in ("product_w27_croissant_pb", "product_w27_latte_pb", "product_w27_latte_bcf"):
            with self.subTest(product=xmlid):
                template = self._ref(xmlid)
                self.assertEqual(len(template.product_variant_ids), 1)
                self.assertFalse(template.is_storable)
                self.assertTrue(template.attribute_line_ids)

    def test_priced_modifier_extra_price(self):
        """Pola 2: nilai atribut berharga (ekstra shot) -> price_extra di nilai atribut template."""
        value = self._ref("attr_w27_extra_shot_one")
        self.assertEqual(value.default_extra_price, 6000)
        for xmlid in ("product_w27_latte_pb", "product_w27_latte_bcf"):
            with self.subTest(product=xmlid):
                ptav = self._ref(xmlid).attribute_line_ids.product_template_value_ids.filtered(
                    lambda v: v.product_attribute_value_id == value
                )
                self.assertEqual(ptav.price_extra, 6000)

    # ------------------------------------------------------- pola 3: add-on berbahan
    def test_addon_with_ingredient_uses_kit_bom(self):
        """Pola 3: add-on = produk jual non-stok + BoM Kit -> pos_mrp meledakkan ke bahan storable."""
        cases = (
            ("pb", "product_w27_addon_cheese_pb", "product_w27_cheese_slice_pb", "bom_w27_addon_cheese_pb"),
            ("bcf", "product_w27_addon_oat_bcf", "product_w27_oat_milk_bcf", "bom_w27_addon_oat_bcf"),
        )
        for code, addon_xmlid, ingredient_xmlid, bom_xmlid in cases:
            with self.subTest(pt=code):
                company = self._company(code)
                addon = self._ref(addon_xmlid).product_variant_id
                ingredient = self._ref(ingredient_xmlid).product_variant_id
                self.assertTrue(addon.available_in_pos)
                self.assertFalse(addon.is_storable)
                self.assertTrue(ingredient.is_storable)
                self.assertFalse(ingredient.sale_ok)
                self.assertFalse(ingredient.available_in_pos)
                bom = self.env["mrp.bom"]._bom_find(addon, company_id=company.id, bom_type="phantom").get(addon)
                self.assertEqual(bom, self._ref(bom_xmlid))
                self.assertEqual(bom.company_id, company)
                _boms, lines = bom.explode(addon, 2)
                self.assertEqual([(line.product_id, data["qty"]) for line, data in lines], [(ingredient, 2.0)])
                self.assertEqual(addon.taxes_id, self._pb1(code))

    def test_addon_is_not_a_kitting_package(self):
        """Guard efn_kitting (paket CK tidak boleh Kit) tidak berlaku: add-on bukan paket kitting."""
        for xmlid in ("product_w27_addon_cheese_pb", "product_w27_addon_oat_bcf"):
            template = self._ref(xmlid)
            if "efn_is_kitting_package" not in template._fields:
                self.skipTest("efn_kitting tidak terpasang")
            with self.subTest(product=xmlid):
                self.assertFalse(template.efn_is_kitting_package)

    # ----------------------------------------------------------- pola 4: combo
    def test_combo_products_per_company(self):
        cases = (
            ("pb", "product_w27_breakfast_set_pb", ("combo_w27_bread_pb", "combo_w27_drink_pb")),
            ("bcf", "product_w27_coffee_set_bcf", ("combo_w27_drink_bcf", "combo_w27_cake_bcf")),
        )
        for code, product_xmlid, combo_xmlids in cases:
            with self.subTest(pt=code):
                company = self._company(code)
                product = self._ref(product_xmlid)
                self.assertEqual(product.type, "combo")
                self.assertTrue(product.available_in_pos)
                self.assertEqual(product.company_id, company)
                self.assertEqual(product.taxes_id, self._pb1(code))
                combos = self.env["product.combo"].browse([self._ref(x).id for x in combo_xmlids])
                self.assertEqual(product.combo_ids, combos)
                for item in combos.combo_item_ids:
                    self.assertEqual(item.company_id, company)
                    self.assertNotEqual(item.product_id.type, "combo")
                    self.assertTrue(item.product_id.available_in_pos)
                    self.assertTrue(item.product_id.sale_ok)
                    self.assertEqual(item.product_id.company_id, company)
                    self.assertEqual(item.product_id.taxes_id, self._pb1(code))

    def test_combo_item_extra_price(self):
        items = self._ref("combo_w27_bread_pb").combo_item_ids
        croissant = self._ref("product_w27_croissant_pb").product_variant_id
        prices = {item.product_id: item.extra_price for item in items}
        self.assertEqual(prices[croissant], 3000)
        self.assertEqual(sorted(prices.values()), [0.0, 3000.0])

    def test_combo_children_route_to_kds_stations(self):
        """Anak combo dirutekan per kategori POS ke stasiun demo (Bakery/Pastry, Bar Kopi); induk tidak ditiket."""
        bakery = self._ref("kds_station_bakery_pastry")
        bar = self._ref("kds_station_coffee_bar")
        stations = bakery | bar
        Order = self.env["pos.order"]
        expected = {
            "product_w27_croissant_pb": bakery,
            "product_w27_choco_bun_pb": bakery,
            "product_w27_jasmine_tea_pb": bar,
            "product_w27_latte_pb": bar,
            "product_w27_addon_cheese_pb": bakery,
        }
        for xmlid, station in expected.items():
            with self.subTest(product=xmlid):
                product = self._ref(xmlid).product_variant_id
                self.assertEqual(Order._efn_kds_route(product, stations), station)

    # ---------------------------------------------------------------- pajak
    def test_menu_taxes_follow_service_fiscal_positions(self):
        """Dine-in: SC + PB1 dine-in; non-dine-in: PB1 0 % tanpa SC (18-1) - untuk anak combo & add-on."""
        fps = {
            "pb": ("fiscal_position_fnb_dine_in", "fiscal_position_fnb_non_dine_in", "tax_service_charge_5_fnb"),
            "bcf": ("fiscal_position_bcf_dine_in", "fiscal_position_bcf_non_dine_in", "tax_service_charge_5_bcf"),
        }
        products = {
            "pb": ("product_w27_croissant_pb", "product_w27_addon_cheese_pb", "product_w27_breakfast_set_pb"),
            "bcf": ("product_w27_latte_bcf", "product_w27_addon_oat_bcf", "product_w27_coffee_set_bcf"),
        }
        for code, (dine_in_xmlid, non_dine_in_xmlid, sc_xmlid) in fps.items():
            dine_in = self.env.ref(f"efn_setup_company.{dine_in_xmlid}")
            non_dine_in = self.env.ref(f"efn_setup_company.{non_dine_in_xmlid}")
            sc = self.env.ref(f"efn_setup_company.{sc_xmlid}")
            for xmlid in products[code]:
                with self.subTest(pt=code, product=xmlid):
                    taxes = self._ref(xmlid).taxes_id
                    self.assertIn(sc, dine_in.map_tax(taxes))
                    mapped = non_dine_in.map_tax(taxes)
                    self.assertNotIn(sc, mapped)
                    self.assertTrue(mapped)
                    self.assertEqual(set(mapped.mapped("amount")), {0.0})
