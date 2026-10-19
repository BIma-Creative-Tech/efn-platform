"""E-47 pengecualian SoD (docs/08 §12.3 butir 5; L-65; test wajib 9)."""

from datetime import timedelta

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged

from .common import REASON, EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnSodException(EfnRoleCase):
    def _activities(self, record, user):
        return (
            self.env["mail.activity"]
            .sudo()
            .search([("res_model", "=", record._name), ("res_id", "=", record.id), ("user_id", "=", user.id)])
        )

    def _grant(self, user, granter=None, **vals):
        values = {"rule_id": self.rule_ab.id, "user_id": user.id, "reason": REASON}
        values.update(vals)
        return self.Exc.with_user(granter or self.superadmin).create(values)

    def test_09a_exception_by_superadmin_allows_and_notifies_auditor(self):
        user = self._make_user("efn_role_exc_ok", extra=self.g_a)
        exc = self._grant(user)
        self.assertEqual(exc.granted_by_id, self.superadmin)
        self.assertEqual(exc.state, "effective")
        self.assertEqual(exc.company_id, user.company_id)
        self.assertEqual((exc.date_to - exc.date_from).days, 30, "default 30 hari")
        user.write({"group_ids": [Command.link(self.g_b.id)]})
        self.assertIn(self.g_b, user.group_ids)
        # L-65: notifikasi otomatis ke grup Auditor (auditor bukan admin hak akses).
        self.assertTrue(self._activities(exc, self.auditor))
        self.assertFalse(self._activities(exc, self.superadmin2), "Superadmin bukan penerima notifikasi auditor")

    def test_09b_exception_rejections(self):
        user = self._make_user("efn_role_exc_bad", extra=self.g_a)
        with self.assertRaises(ValidationError):
            self._grant(self.superadmin)  # diri sendiri
        with self.assertRaises(AccessError):
            self._grant(user, granter=self.access_admin)  # bukan Superadmin
        with self.assertRaises(ValidationError):
            self._grant(user, reason="")
        with self.assertRaises(ValidationError):
            self._grant(user, reason="terlalu pendek")
        with self.assertRaises(ValidationError):
            self._grant(user, date_to=self.today + timedelta(days=91))
        with self.assertRaises(ValidationError):
            self._grant(user, date_from=self.today - timedelta(days=1), date_to=self.today + timedelta(days=5))
        with self.assertRaises(ValidationError):
            self._grant(user, granted_by_id=self.superadmin2.id)
        # Batas 90 hari tepat diterima; diberikan Superadmin lain.
        exc = self._grant(user, granter=self.superadmin2, date_to=self.today + timedelta(days=90))
        self.assertEqual(exc.granted_by_id, self.superadmin2)

    def test_09c_exception_immutable_revocable_not_deletable(self):
        user = self._make_user("efn_role_exc_imm", extra=self.g_a)
        exc = self._grant(user)
        with self.assertRaises(UserError):
            exc.with_user(self.superadmin).write({"reason": REASON + " diubah"})
        with self.assertRaises(UserError):
            exc.with_user(self.superadmin).write({"date_to": self.today + timedelta(days=60)})
        with self.assertRaises(UserError):
            exc.with_user(self.superadmin).unlink()
        exc.with_user(self.superadmin).action_archive()
        self.assertFalse(exc.active)
        self.assertEqual(exc.revoked_by_id, self.superadmin)
        self.assertEqual(exc.state, "revoked")
        with self.assertRaises(UserError):
            exc.with_user(self.superadmin).write({"active": True})
        # Dicabut -> pelanggaran tidak lagi dikecualikan.
        with self.assertRaises(ValidationError):
            user.write({"group_ids": [Command.link(self.g_b.id)]})

    def test_09d_expired_exception_activity_and_next_write_rejected(self):
        user = self._make_user("efn_role_exc_exp", extra=self.g_a)
        exc = self._grant(user)
        user.write({"group_ids": [Command.link(self.g_b.id)]})
        # Simulasi waktu berlalu (constraint ORM menolak tanggal lampau; SQL hanya di test).
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_sod_exception SET date_from = %s, date_to = %s WHERE id = %s",
            [self.today - timedelta(days=40), self.today - timedelta(days=10), exc.id],
        )
        exc.invalidate_recordset()
        self.assertEqual(exc.state, "expired")
        notified = self.Exc._efn_notify_expired()
        self.assertIn(exc, notified)
        self.assertTrue(exc.expiry_notified)
        self.assertTrue(self._activities(exc, self.superadmin), "aktivitas untuk Superadmin")
        # Grup tidak dicabut otomatis.
        self.assertIn(self.g_b, user.group_ids)
        # Sekali saja.
        self.assertFalse(self.Exc._efn_notify_expired())
        # Penulisan grup berikutnya untuk user itu ditolak sampai diselesaikan.
        with self.assertRaises(ValidationError):
            user.write({"group_ids": [Command.link(self.g_other.id)]})
        # Menyelesaikan (cabut salah satu grup) diterima.
        user.write({"group_ids": [Command.unlink(self.g_b.id)]})
        self.assertNotIn(self.g_b, user.group_ids)
