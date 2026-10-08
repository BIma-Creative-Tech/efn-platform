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
