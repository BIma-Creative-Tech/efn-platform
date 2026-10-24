"""Gelombang 14 (docs/08 §7): default preset F&B dari data/pos_fnb_defaults.xml (noupdate).

Hanya berlaku pada DB yang meng-install modul ini (blok noupdate tidak diterapkan ulang saat -u).
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnFnbDefaults(TransactionCase):
    def test_delivery_preset_not_in_self_order(self):
        preset = self.env.ref("pos_restaurant.pos_delivery_preset")
        self.assertFalse(preset.available_in_self)

    def test_takeout_preset_without_demo_time_slots(self):
        preset = self.env.ref("pos_restaurant.pos_takeout_preset")
        self.assertFalse(preset.use_timing)

    def test_dine_in_preset_untouched(self):
        preset = self.env.ref("pos_restaurant.pos_takein_preset")
        self.assertTrue(preset.available_in_self)
        self.assertEqual(preset.service_at, "table")


@tagged("post_install", "-at_install", "efn")
class TestEfnFnbServicePresets(TransactionCase):
    """G19 W-14 (docs/08 §10.4): preset F&B membawa fiscal position per layanan dari efn_setup_company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.fnb = ref("efn_setup_company.company_fnb")
        cls.pb1 = ref("efn_setup_company.tax_pb1_10_fnb")
        cls.sc = ref("efn_setup_company.tax_service_charge_5_fnb")
        cls.dine_in = ref("efn_setup_pos.preset_fnb_dine_in")
        cls.takeaway = ref("efn_setup_pos.preset_fnb_takeaway")

    def test_presets_point_to_fnb_fiscal_positions(self):
        fp_dine_in = self.env.ref("efn_setup_company.fiscal_position_fnb_dine_in")
        fp_non_dine_in = self.env.ref("efn_setup_company.fiscal_position_fnb_non_dine_in")
        self.assertEqual(self.dine_in.fiscal_position_id, fp_dine_in)
        self.assertEqual(self.takeaway.fiscal_position_id, fp_non_dine_in)
        for preset in self.dine_in | self.takeaway:
            with self.subTest(preset=preset.name):
                self.assertEqual(preset.fiscal_position_id.company_id, self.fnb)
                self.assertFalse(preset.available_in_self)  # SEC-75: self-order tetap terkunci

    def test_service_charge_only_dine_in(self):
        self.assertIn(self.sc, self.dine_in.fiscal_position_id.map_tax(self.pb1))
        self.assertNotIn(self.sc, self.takeaway.fiscal_position_id.map_tax(self.pb1))
        self.assertEqual(self.dine_in.service_at, "table")
        self.assertEqual(self.takeaway.service_at, "counter")

    def test_bacha_fnb_presets_use_bcf_fiscal_positions(self):
        """G20 W-32 (docs/04 §12.1): preset Bacha F&B memakai pajak & fiscal position company BCF sendiri."""
        ref = self.env.ref
        bcf = ref("efn_setup_company.company_bcf")
        pb1 = ref("efn_setup_company.tax_pb1_10_bcf")
        sc = ref("efn_setup_company.tax_service_charge_5_bcf")
        dine_in = ref("efn_setup_pos.preset_bcf_dine_in")
        takeaway = ref("efn_setup_pos.preset_bcf_takeaway")
        self.assertEqual(dine_in.fiscal_position_id, ref("efn_setup_company.fiscal_position_bcf_dine_in"))
        self.assertEqual(takeaway.fiscal_position_id, ref("efn_setup_company.fiscal_position_bcf_non_dine_in"))
        for preset in dine_in | takeaway:
            with self.subTest(preset=preset.name):
                self.assertEqual(preset.fiscal_position_id.company_id, bcf)
                self.assertFalse(preset.available_in_self)
        self.assertIn(sc, dine_in.fiscal_position_id.map_tax(pb1))
        self.assertNotIn(sc, takeaway.fiscal_position_id.map_tax(pb1))
        # Preset PB tidak memetakan pajak BCF (fiscal position per company).
        self.assertEqual(self.dine_in.fiscal_position_id.map_tax(pb1), pb1)

    def test_wetzels_takeaway_preset_uses_wtz_fiscal_position(self):
        """G24 W-40 (docs/04 §16.1/§16.2): preset bawa pulang WTZ = FP non-dine-in WTZ (tanpa SC, PB1 0 %)."""
        ref = self.env.ref
        wtz = ref("efn_setup_company.company_wtz")
        pb1 = ref("efn_setup_company.tax_pb1_10_wtz")
        takeaway = ref("efn_setup_pos.preset_wtz_takeaway")
        self.assertEqual(takeaway.fiscal_position_id, ref("efn_setup_company.fiscal_position_wtz_non_dine_in"))
        self.assertEqual(takeaway.fiscal_position_id.company_id, wtz)
        self.assertEqual(takeaway.service_at, "counter")
        self.assertFalse(takeaway.available_in_self)
        self.assertNotIn(ref("efn_setup_company.tax_service_charge_5_wtz"), takeaway.fiscal_position_id.map_tax(pb1))


@tagged("post_install", "-at_install", "efn")
class TestEfnG20EntityPosData(TransactionCase):
    """G20 W-32 (docs/04 §12.1): data POS per PT baru (EDC manual, nomenklatur ritel)."""

    def test_edc_method_per_pos_company(self):
        for suffix, company_xmlid in (
            ("rtl", "company_rtl"),
            ("fnb", "company_fnb"),
            ("bcr", "company_bcr"),
            ("bcf", "company_bcf"),
            ("wtz", "company_wtz"),  # G24 W-40 (docs/04 §16.1)
        ):
            with self.subTest(pt=suffix):
                method = self.env.ref(f"efn_setup_pos.pos_payment_method_edc_{suffix}")
                company = self.env.ref(f"efn_setup_company.{company_xmlid}")
                self.assertEqual(method.company_id, company)
                self.assertEqual(method.payment_method_type, "none")
                self.assertEqual(method.journal_id.type, "bank")
                self.assertEqual(method.journal_id.company_id, company)
                self.assertFalse(method.efn_is_loyalty_points)

    def test_retail_nomenclature_for_retail_companies(self):
        nomenclature = self.env.ref("efn_setup_pos.barcode_nomenclature_efn_retail")
        for xmlid in ("company_rtl", "company_bcr"):
            with self.subTest(company=xmlid):
                self.assertEqual(self.env.ref(f"efn_setup_company.{xmlid}").nomenclature_id, nomenclature)
        for xmlid in ("company_fnb", "company_bcf", "company_wtz"):
            with self.subTest(company=xmlid):
                self.assertNotEqual(self.env.ref(f"efn_setup_company.{xmlid}").nomenclature_id, nomenclature)
