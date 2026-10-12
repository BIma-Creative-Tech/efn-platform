"""Gelombang 18 (docs/08 §9): W-22 default stasiun KDS, W-25 grup kasir pembuat pelanggan + kunci HP member.

Nomor HP di bawah fiktif khusus test (bukan data pribadi nyata).
"""

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestEfnG18Config(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("efn_setup_company.company_fnb")
        cls.group = cls.env.ref("efn_setup_pos.group_efn_cashier_contact_creation")
        cls.cashier = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Kasir Uji W-25",
                    "login": "kasir.uji.w25",
                    "company_id": cls.company.id,
                    "company_ids": [Command.set(cls.company.ids)],
                    "group_ids": [Command.set([cls.env.ref("base.group_user").id, cls.group.id])],
                }
            )
        )

    # ------------------------------------------------------------------ W-22
    def test_kds_station_defaults_warn_5_late_10(self):
        station = (
            self.env["efn.kds.station"]
            .with_company(self.company)
            .create({"name": "Stasiun Uji W-22", "company_id": self.company.id})
        )
        self.assertEqual(station.warn_minutes, 5)
        self.assertEqual(station.late_minutes, 10)

    def test_kds_retention_90_days(self):
        value = self.env["ir.config_parameter"].sudo().get_param("efn.kds.retention_days")
        self.assertEqual(value, "90")

    # ------------------------------------------------------------------ W-25
    def test_cashier_group_implies_pos_user_and_contact_creation(self):
        self.assertTrue(self.cashier.has_group("point_of_sale.group_pos_user"))
        self.assertTrue(self.cashier.has_group("base.group_partner_manager"))
        self.assertFalse(self.cashier.has_group("point_of_sale.group_pos_manager"))

    def test_cashier_can_create_customer(self):
        partner = (
            self.env["res.partner"]
            .with_user(self.cashier)
            .create({"name": "Pelanggan Baru W-25", "phone": "0812-0000-2525"})
        )
        self.assertTrue(partner)

    def test_member_phone_stays_locked_for_cashier(self):
        """SEC-07/SEC-44: partner dengan riwayat OTP -> HP tidak bisa diubah kasir walau punya Contact Creation."""
        member = self.env["res.partner"].create({"name": "Member Uji W-25", "phone": "0812-3456-2525"})
        config = self.env["pos.config"].search([("company_id", "=", self.company.id)], limit=1) or self.env[
            "pos.config"
        ].with_company(self.company).create({"name": "POS Uji W-25", "company_id": self.company.id})
        self.env["efn.pos.otp.challenge"].create(
            {
                "company_id": self.company.id,
                "config_id": config.id,
                "order_ref": "w25-lock-test",
                "partner_id": member.id,
                "member_ref_masked": "62812****525",
                "state": "verified",
            }
        )
        with self.assertRaises(AccessError):
            member.with_user(self.cashier).write({"phone": "0813-9999-2525"})
        # Field lain tetap boleh (koreksi nama).
        member.with_user(self.cashier).write({"name": "Member Uji W-25 (koreksi)"})
