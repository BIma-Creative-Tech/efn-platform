"""SEC-07 sisa / SEC-115 (review r11, L-25c): HP partner member hanya bisa diubah admin teraudit
(`base.group_system`; Manajer POS tidak lagi cukup). Status member = penanda tahan lama `efn_member_locked_at`
(diisi OTP/redeem/earn) atau riwayat OTP/order.

Nomor di bawah fiktif khusus test (bukan data pribadi nyata).
"""

from odoo import SUPERUSER_ID, Command, models
from odoo.exceptions import AccessError
from odoo.tests import tagged

from odoo.addons.efn_pos_otp_gate.models.res_partner import CHECKED_KEY
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
        pos_manager_group = cls.env.ref("point_of_sale.group_pos_manager").id
        # `manager` = admin teraudit (Settings) yang sah mengubah HP member (SEC-115).
        cls.manager = Users.create(
            {
                "name": "Admin Uji SEC07",
                "login": "manajer.uji.sec07",
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
                "group_ids": [Command.set(base_groups + [pos_manager_group, cls.env.ref("base.group_system").id])],
            }
        )
        cls.pos_manager = Users.create(
            {
                "name": "Manajer POS Uji SEC115",
                "login": "manajer.pos.uji.sec115",
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
                "group_ids": [Command.set(base_groups + [pos_manager_group])],
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
        """SEC-07: perubahan HP oleh manajer tercatat di chatter DAN log efn_audit (ter-mask)."""
        Rule = self.env["efn.audit.rule"].sudo().with_context(active_test=False)
        phone_field = self.env["ir.model.fields"]._get("res.partner", "phone").id
        vals = {
            "active": True,
            "log_write": True,
            "tracked_field_ids": [Command.set([phone_field])],
            "masked_field_ids": [Command.set([phone_field])],
        }
        rule = Rule.search([("model_name", "=", "res.partner")], limit=1)
        if rule:
            rule.write(vals)
        else:
            Rule.create(dict(vals, name="Uji SEC-07", model_id=self.env["ir.model"]._get_id("res.partner")))
        self.addCleanup(self.env.registry.clear_cache)
        self.member.with_user(self.manager).write({"phone": PHONE_NEW})
        self.assertEqual(self.member.phone, PHONE_NEW)
        self.assertTrue(self._notes(self.member), "catatan chatter SEC-07")
        log = (
            self.env["efn.audit.log"]
            .sudo()
            .search([("model_name", "=", "res.partner"), ("res_id", "=", self.member.id), ("operation", "=", "write")])
        )
        self.assertEqual(len(log), 1)
        self.assertEqual(log.user_id, self.manager)
        line = log.line_ids.filtered(lambda item: item.field_name == "phone")
        self.assertEqual((line.old_value, line.new_value, line.is_masked), ("62812****890", "62813****222", True))
        # Kasir ditolak -> tidak ada log tambahan (transaksi write gagal sebelum hook).
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).write({"phone": "0812-0000-1111"})
        self.assertEqual(
            self.env["efn.audit.log"]
            .sudo()
            .search_count(
                [("model_name", "=", "res.partner"), ("res_id", "=", self.member.id), ("operation", "=", "write")]
            ),
            1,
        )

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

    # ------------------------------------------------------------- SEC-44
    def test_sudo_with_cashier_uid_rejected_via_write(self):
        """SEC-44 (review r4): `sudo()` dengan uid kasir tidak melewati kunci (lewat write sungguhan)."""
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).sudo().write({"phone": PHONE_NEW})
        with self.assertRaises(AccessError):
            self.member.with_user(self.cashier).sudo().write({"phone": False})
        # Penanda "sudah dicek" tidak tertinggal setelah penolakan.
        self.assertFalse(self.env.cr.cache.get(CHECKED_KEY))
        # Lapis 2 sendiri (write kelas dasar, melewati override lapis 1) juga menolak sudo + kasir.
        with self.assertRaises(AccessError):
            models.Model.write(self.member.with_user(self.cashier).sudo(), {"phone": PHONE_NEW})
        self.member.invalidate_recordset()
        self.assertEqual(self.member.phone, PHONE_MEMBER)
        # HP non-member tetap bebas, juga via sudo.
        self.plain.with_user(self.cashier).sudo().write({"phone": PHONE_NEW})
        self.assertEqual(self.plain.phone, PHONE_NEW)

    def test_sudo_with_manager_uid_allowed_and_recorded(self):
        self.member.with_user(self.manager).sudo().write({"phone": PHONE_NEW})
        self.assertEqual(self.member.phone, PHONE_NEW)
        notes = self._notes(self.member)
        self.assertEqual(len(notes), 1, "perubahan oleh manajer via sudo tetap tercatat di chatter")
        self.assertEqual(notes.author_id, self.manager.partner_id)

    def test_odoobot_internal_process_allowed(self):
        """Proses internal sah (OdooBot, mis. cron erasure) tetap jalan; tanpa catatan chatter."""
        self.member.with_user(SUPERUSER_ID).write({"phone": PHONE_NEW})
        self.assertEqual(self.member.phone, PHONE_NEW)
        self.member.with_user(SUPERUSER_ID).sudo().write({"phone": False})
        self.assertFalse(self.member.phone)
        self.assertFalse(self._notes(self.member))

    # ------------------------------------------------------------- SEC-115
    def test_sec115_pos_manager_denied(self):
        """Manajer POS (tanpa Settings) tidak lagi boleh mengubah HP member."""
        self.assertFalse(self.member.with_user(self.pos_manager)._efn_can_change_member_phone())
        with self.assertRaises(AccessError):
            self.member.with_user(self.pos_manager).write({"phone": PHONE_NEW})
        with self.assertRaises(AccessError):
            self.member.with_user(self.pos_manager).sudo().write({"phone": PHONE_NEW})
        self.member.invalidate_recordset()
        self.assertEqual(self.member.phone, PHONE_MEMBER)

    def test_sec115_lock_survives_challenge_purge(self):
        """Kunci tidak bergantung umur challenge OTP (autovacuum 30 hari): penanda partner tahan lama."""
        member = self.env["res.partner"].create({"name": "Member Enroll SEC115", "phone": "0812-4444-5555"})
        self.assertFalse(member._efn_otp_member_partners())
        member._efn_mark_member()
        self.assertFalse(self.env["efn.pos.otp.challenge"].sudo().search([("partner_id", "=", member.id)]))
        self.assertEqual(member._efn_otp_member_partners(), member)
        for user in (self.cashier, self.pos_manager):
            with self.assertRaises(AccessError):
                member.with_user(user).write({"phone": PHONE_NEW})
        member.with_user(self.manager).write({"phone": PHONE_NEW})
        self.assertEqual(member.phone, PHONE_NEW)
        self.assertTrue(self._notes(member), "perubahan admin tercatat di chatter")

    def test_sec115_flag_server_only_and_idempotent(self):
        member = self.env["res.partner"].create({"name": "Member Penanda SEC115", "phone": "0812-6666-7777"})
        member._efn_mark_member()
        first = member.efn_member_locked_at
        self.assertTrue(first)
        member._efn_mark_member()
        self.assertEqual(member.efn_member_locked_at, first, "idempoten (tanggal pertama dipertahankan)")
        for user in (self.cashier, self.pos_manager, self.manager):
            with self.assertRaises(AccessError):
                member.with_user(user).write({"efn_member_locked_at": False})
            with self.assertRaises(AccessError):
                self.plain.with_user(user).write({"efn_member_locked_at": "2026-01-01 00:00:00"})
        # Kasir (grup POS) boleh membaca penanda (UI), tetapi tidak pernah menulis.
        self.assertTrue(member.with_user(self.cashier).efn_member_locked_at)
