"""E-47 penerapan peran (docs/08 §12.3; test wajib 1-5, 12, + perubahan grup peran & sinkron jabatan)."""

from datetime import timedelta

import psycopg2

from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from .common import EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnRoleApply(EfnRoleCase):
    def test_01_role_adds_groups_and_company_keeps_non_role_groups(self):
        user = self._make_user("efn_role_t01", extra=self.g_other)
        self.assertFalse(user.efn_role_managed)
        role = self._role("Peran Uji GL", self.g_role1)
        line = self._assign(role, user)
        self.assertTrue(line.is_effective)
        self.assertTrue(user.efn_role_managed)
        self.assertIn(self.g_role1, user.group_ids)
        self.assertIn(self.g_other, user.group_ids, "grup non-peran tidak disentuh")
        self.assertIn(self.group_user, user.group_ids)
        self.assertIn(self.gl, user.company_ids, "company peran ditambahkan")
        self.assertEqual(user.company_id, self.main, "company utama tetap")

    def test_02_revoke_line_removes_role_groups_only(self):
        user = self._make_user("efn_role_t02", extra=self.g_other)
        role = self._role("Peran Uji Cabut", self.g_role1)
        line = self._assign(role, user)
        self.assertIn(self.g_role1, user.group_ids)
        line.unlink()
        self.assertNotIn(self.g_role1, user.group_ids)
        self.assertIn(self.g_other, user.group_ids)
        self.assertIn(self.group_user, user.group_ids)
        self.assertIn(self.gl, user.company_ids, "company tidak pernah dikurangi otomatis")

    def test_02b_date_to_in_past_revokes(self):
        user = self._make_user("efn_role_t02b")
        role = self._role("Peran Uji Tanggal", self.g_role1)
        line = self._assign(role, user, date_from=self.today - timedelta(days=10))
        self.assertIn(self.g_role1, user.group_ids)
        line.write({"date_to": self.today - timedelta(days=1)})
        self.assertFalse(line.is_effective)
        self.assertNotIn(self.g_role1, user.group_ids)

    def test_03_expired_line_revoked_by_daily_cron(self):
        user = self._make_user("efn_role_t03")
        role = self._role("Peran Uji Kedaluwarsa", self.g_role1)
        line = self._assign(role, user, date_from=self.today - timedelta(days=5), date_to=self.today)
        self.assertTrue(line.is_effective)
        self.assertIn(self.g_role1, user.group_ids)
        self.Role._cron_efn_role_daily(today=self.today + timedelta(days=1))
        self.assertFalse(line.is_effective)
        self.assertNotIn(self.g_role1, user.group_ids)

    def test_03b_future_line_activated_by_cron(self):
        user = self._make_user("efn_role_t03b")
        role = self._role("Peran Uji Mendatang", self.g_role1)
        line = self._assign(role, user, date_from=self.today + timedelta(days=1))
        self.assertFalse(line.is_effective)
        self.assertNotIn(self.g_role1, user.group_ids)
        self.Role._cron_efn_role_daily(today=self.today + timedelta(days=1))
        self.assertTrue(line.is_effective)
        self.assertIn(self.g_role1, user.group_ids)

    def test_03c_is_effective_not_writable_by_client(self):
        user = self._make_user("efn_role_t03c")
        role = self._role("Peran Uji Efektif", self.g_role1)
        line = self._assign(role, user, date_from=self.today + timedelta(days=3))
        with self.assertRaises(UserError):
            line.write({"is_effective": True})

    def test_04_unmanaged_user_untouched(self):
        user = self._make_user("efn_role_t04", extra=self.g_role1 | self.g_role2)
        role = self._role("Peran Uji Tak Terkelola", self.g_role1)
        self.Role._efn_apply_roles(user)
        self.assertFalse(user.efn_role_managed)
        self.assertIn(self.g_role1, user.group_ids)
        self.assertIn(self.g_role2, user.group_ids)
        role.write({"group_ids": [Command.set(self.g_role2.ids)]})
        self.assertIn(self.g_role1, user.group_ids, "user tanpa flag tidak pernah disentuh")
        # Grup terkelola tetap boleh diberikan langsung ke user tanpa flag.
        user.write({"group_ids": [Command.unlink(self.g_role1.id)]})
        self.assertNotIn(self.g_role1, user.group_ids)

    def test_05_direct_write_of_managed_group_rejected(self):
        user = self._make_user("efn_role_t05")
        role1 = self._role("Peran Uji 1", self.g_role1)
        self._role("Peran Uji 2", self.g_role2)
        self._assign(role1, user)
        with self.assertRaises(ValidationError):
            user.write({"group_ids": [Command.link(self.g_role2.id)]})
        with self.assertRaises(ValidationError):
            user.write({"group_ids": [Command.unlink(self.g_role1.id)]})
        with self.assertRaises(ValidationError):
            self.g_role2.write({"user_ids": [Command.link(user.id)]})
        # Grup non-peran tetap bebas.
        user.write({"group_ids": [Command.link(self.g_other.id)]})
        self.assertIn(self.g_other, user.group_ids)
        self.assertIn(self.g_role1, user.group_ids)

    def test_05b_role_group_change_applies_and_revokes(self):
        user = self._make_user("efn_role_t05b")
        role = self._role("Peran Uji Ubah", self.g_role1)
        self._assign(role, user)
        role.write({"group_ids": [Command.set(self.g_role2.ids)]})
        self.assertNotIn(self.g_role1, user.group_ids, "grup yang dikeluarkan dari peran dicabut")
        self.assertIn(self.g_role2, user.group_ids)
        role.write({"active": False})
        self.assertNotIn(self.g_role2, user.group_ids, "peran diarsipkan -> grup dicabut")

    def test_05c_two_roles_union(self):
        user = self._make_user("efn_role_t05c")
        role1 = self._role("Peran Uji Gabung 1", self.g_role1)
        role2 = self._role("Peran Uji Gabung 2", self.g_role2, company=self.pb)
        line1 = self._assign(role1, user)
        self._assign(role2, user)
        self.assertIn(self.g_role1, user.group_ids)
        self.assertIn(self.g_role2, user.group_ids)
        self.assertTrue({self.gl, self.pb} <= set(user.company_ids))
        line1.unlink()
        self.assertNotIn(self.g_role1, user.group_ids)
        self.assertIn(self.g_role2, user.group_ids)

    def test_12_technical_and_portal_users_cannot_get_role(self):
        role = self._role("Peran Uji Teknis", self.g_role1)
        portal = new_test_user(self.env, login="efn_role_portal", groups="base.group_portal")
        with self.assertRaises(ValidationError):
            self._assign(role, portal)
        found = False
        for xmlid in ("efn_online_order.group_online_channel", "efn_pos_kds.group_kds_device"):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group is None:
                continue
            found = True
            tech = self._make_user(f"efn_role_tech_{group.id}", [xmlid])
            with self.subTest(group=xmlid), self.assertRaises(ValidationError):
                self._assign(role, tech)
        if not found:
            self.skipTest("modul akun teknis (efn_online_order / efn_pos_kds) tidak terpasang")

    def test_job_sync_creates_and_ends_job_lines(self):
        job = self.env["hr.job"].create({"name": "Kasir Uji Peran", "company_id": self.gl.id})
        job2 = self.env["hr.job"].create({"name": "Gudang Uji Peran", "company_id": self.gl.id})
        user = self._make_user("efn_role_job", companies=self.gl)
        employee = self.env["hr.employee"].create(
            {"name": "Karyawan Uji Peran", "company_id": self.gl.id, "user_id": user.id, "job_id": job.id}
        )
        role = self._role("Peran Jabatan Uji", self.g_role1, job_id=job.id)
        role.with_user(self.access_admin).action_efn_apply_job_roles()
        line = self.Line.search([("role_id", "=", role.id), ("user_id", "=", user.id)])
        self.assertEqual(len(line), 1)
        self.assertEqual(line.source, "job")
        self.assertIn(self.g_role1, user.group_ids)
        # Idempoten.
        role._efn_sync_job_lines(roles=role)
        self.assertEqual(self.Line.search_count([("role_id", "=", role.id), ("user_id", "=", user.id)]), 1)
        employee.write({"job_id": job2.id})
        role._efn_sync_job_lines(roles=role)
        self.assertFalse(
            self.Line.search([("role_id", "=", role.id), ("user_id", "=", user.id), ("is_effective", "=", True)])
        )
        self.assertNotIn(self.g_role1, user.group_ids)

    def test_job_sync_ends_older_line_with_yesterday(self):
        job = self.env["hr.job"].create({"name": "Kasir Uji Peran Lama", "company_id": self.gl.id})
        user = self._make_user("efn_role_job_old", companies=self.gl)
        employee = self.env["hr.employee"].create(
            {"name": "Karyawan Uji Peran Lama", "company_id": self.gl.id, "user_id": user.id, "job_id": job.id}
        )
        role = self._role("Peran Jabatan Uji Lama", self.g_role1, job_id=job.id)
        line = self._assign(role, user, source="job", date_from=self.today - timedelta(days=30))
        employee.write({"job_id": False})
        role._efn_sync_job_lines(roles=role)
        self.assertEqual(line.date_to, self.today - timedelta(days=1))
        self.assertFalse(line.is_effective)
        self.assertNotIn(self.g_role1, user.group_ids)

    def test_role_unique_job_per_company(self):
        job = self.env["hr.job"].create({"name": "Jabatan Unik Uji", "company_id": self.gl.id})
        self._role("Peran Unik 1", self.g_role1, job_id=job.id)
        with mute_logger("odoo.sql_db"), self.assertRaises(psycopg2.IntegrityError), self.cr.savepoint():
            self._role("Peran Unik 2", self.g_role2, job_id=job.id)
        # Peran tanpa jabatan tidak dibatasi.
        self._role("Terminal POS Uji 1", self.g_role1)
        self._role("Terminal POS Uji 2", self.g_role1)
