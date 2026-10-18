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
