"""Gelombang 15 (SEC-75): self-order dikunci teknis ke 'nothing'/'kiosk' (models/pos_config.py)."""

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_setup_pos.models.pos_config import SELF_ORDER_PARAM


@tagged("post_install", "-at_install", "efn")
class TestEfnSelfOrderLock(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ICP = cls.env["ir.config_parameter"].sudo()
        cls.ICP.set_param(SELF_ORDER_PARAM, False)
        cls.config = cls.env["pos.config"].create({"name": "EFN Resto Uji SEC-75", "module_pos_restaurant": True})

    def test_new_restaurant_config_disabled(self):
        self.assertEqual(self.config.self_ordering_mode, "nothing")
        default = self.env["ir.default"]._get("pos.config", "self_ordering_mode")
        self.assertEqual(default, "nothing")

    def test_mobile_and_qr_menu_rejected(self):
        for mode in ("mobile", "consultation"):
            with self.subTest(mode=mode), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.config.write({"self_ordering_mode": mode})
            with self.subTest(mode=f"create {mode}"), self.assertRaises(ValidationError), self.env.cr.savepoint():
                self.env["pos.config"].create(
                    {"name": f"EFN Resto {mode}", "module_pos_restaurant": True, "self_ordering_mode": mode}
                )

    def test_switch_allows_mobile_and_turning_off_normalizes(self):
        self.ICP.set_param(SELF_ORDER_PARAM, "True")
        self.config.write({"self_ordering_mode": "mobile"})
        self.assertEqual(self.config.self_ordering_mode, "mobile")
        # Sakelar dimatikan -> config dinormalkan seketika.
        self.ICP.set_param(SELF_ORDER_PARAM, "False")
        self.assertEqual(self.config.self_ordering_mode, "nothing")

    def test_upgrade_normalizes_existing_configs(self):
        # Simulasi DB lama: nilai ditulis langsung (melewati constraint), lalu langkah upgrade.
        self.env.flush_all()
        self.env.cr.execute("UPDATE pos_config SET self_ordering_mode = 'mobile' WHERE id = %s", [self.config.id])
        self.config.invalidate_recordset(["self_ordering_mode"])
        normalized = self.env["pos.config"]._efn_normalize_self_order_mode()
        self.assertIn(self.config, normalized)
        self.assertEqual(self.config.self_ordering_mode, "nothing")
        self.env.flush_all()
        self.env.cr.execute("SELECT count(*) FROM pos_config WHERE self_ordering_mode NOT IN ('nothing', 'kiosk')")
        self.assertEqual(self.env.cr.fetchone()[0], 0, "cek pra-go-live SEC-75")
