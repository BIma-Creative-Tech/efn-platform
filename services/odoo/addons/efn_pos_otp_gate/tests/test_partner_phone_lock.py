"""SEC-07 sisa: HP partner yang sudah dipakai member hanya bisa diubah manajer POS.

Nomor di bawah fiktif khusus test (bukan data pribadi nyata).
"""

from odoo import SUPERUSER_ID, Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.efn_pos_otp_gate.models.res_partner import ResPartner as EfnResPartner
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

PHONE_MEMBER = "0812-3456-7890"
PHONE_NEW = "0813-1111-2222"


@tagged("post_install", "-at_install", "efn")
class TestEfnPartnerPhoneLock(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        company = cls.config.company_id
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        base_groups = [cls.env.ref("base.group_user").id, cls.env.ref("base.group_partner_manager").id]
        cls.cashier = Users.create(
            {
                "name": "Kasir Uji SEC07",
                "login": "kasir.uji.sec07",
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
                "group_ids": [Command.set(base_groups + [cls.env.ref("point_of_sale.group_pos_user").id])],
            }
        )
        cls.manager = Users.create(
            {
                "name": "Manajer Uji SEC07",
                "login": "manajer.uji.sec07",
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
                "group_ids": [Command.set(base_groups + [cls.env.ref("point_of_sale.group_pos_manager").id])],
            }
        )

    def setUp(self):
        super().setUp()
        Partner = self.env["res.partner"]
        self.member = Partner.create({"name": "Member OTP SEC07", "phone": PHONE_MEMBER})
        self.plain = Partner.create({"name": "Pelanggan Biasa SEC07", "phone": "0812-9999-0000"})
        self.env["efn.pos.otp.challenge"].create(
            {
                "company_id": self.config.company_id.id,
                "config_id": self.config.id,
                "order_ref": "sec07-lock-test",
                "partner_id": self.member.id,
                "member_ref_masked": "62812****890",
                "state": "verified",
            }
        )

    def _notes(self, partner):
        return self.env["mail.message"].search(
            [("model", "=", "res.partner"), ("res_id", "=", partner.id), ("body", "ilike", "SEC-07")]
        )

    def test_cashier_cannot_change_member_phone(self):
        self.assertFalse(self.member.with_user(self.cashier)._efn_can_change_member_phone())
        self.assertEqual(self.member._efn_otp_member_partners(), self.member)
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).write({"phone": PHONE_NEW})
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).write({"phone": False})
        # Lapis 1 (override write) juga menolak bila dipanggil langsung (lihat docstring modul).
        with self.assertRaises(AccessError):
            EfnResPartner.write(self.member.with_user(self.cashier), {"phone": PHONE_NEW})
        self.member.invalidate_recordset()
        self.assertEqual(self.member.phone, PHONE_MEMBER)

    def test_cashier_can_edit_other_fields_and_non_member_phone(self):
        self.member.with_user(self.cashier).write({"email": "member.sec07@example.com"})
        self.plain.with_user(self.cashier).write({"phone": PHONE_NEW})
        self.assertEqual(self.plain.phone, PHONE_NEW)
        created = self.env["res.partner"].with_user(self.cashier).create({"name": "Baru", "phone": "0812-5555-6666"})
        self.assertTrue(created)

    def test_manager_change_is_recorded(self):
        self.member.with_user(self.manager).write({"phone": PHONE_NEW})
        self.assertEqual(self.member.phone, PHONE_NEW)
        recorded = bool(self._notes(self.member))
        if not recorded and "auditlog.log" in self.env:
            Log = self.env["auditlog.log"].sudo()
            recorded = bool(Log.search([("res_id", "=", self.member.id), ("method", "=", "write")]))
        self.assertTrue(recorded, "perubahan HP member wajib tercatat (chatter atau auditlog)")

    def test_manager_change_is_logged_masked(self):
        EfnResPartner.write(self.member.with_user(self.manager), {"phone": PHONE_NEW})
        self.assertEqual(self.member.phone, PHONE_NEW)
        notes = self._notes(self.member)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes.author_id, self.manager.partner_id)
        body = str(notes.body)
        self.assertIn("62812****890", body)
        self.assertIn("62813****222", body)
        for raw in ("3456-7890", "81234567890", "1111-2222", "81311112222"):
            self.assertNotIn(raw, body)

    def test_consumed_order_history_locks_phone(self):
        partner = self.env["res.partner"].create({"name": "Member Order SEC07", "phone": "0812-7777-8888"})
        self.open_new_session()
        data = self.create_ui_order_data(
            [(self.create_product("Produk SEC07", self.categ_basic, 10.0), 1)], customer=partner
        )
        self.env["pos.order"].sync_from_ui([data])
        order = self.env["pos.order"].search([("uuid", "=", data["uuid"])])
        order.sudo().write({"efn_otp_gate_state": "consumed"})
        with self.assertRaises(AccessError):
            partner.with_user(self.cashier).write({"phone": PHONE_NEW})

    def test_erasure_cron_user_not_locked(self):
        # Cron erasure UU PDP (efn_privacy) berjalan sebagai OdooBot + sudo dan wajib bisa
        # mengosongkan HP member.
        self.member.with_user(SUPERUSER_ID).sudo().write({"phone": False})
        self.assertFalse(self.member.phone)

    def test_safety_net_constraint_uses_real_user(self):
        # Lapis 2 dijalankan Odoo dengan sudo; keputusan tetap dari uid asli.
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).sudo()._efn_check_member_phone_change()
        self.member.with_user(self.manager).sudo()._efn_check_member_phone_change()
        self.plain.with_user(self.cashier).sudo()._efn_check_member_phone_change()
        # Context dari klien RPC tidak bisa melewati lapis 2.
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).with_context(
                efn_member_phone_checked=True
            )._efn_check_member_phone_change()
