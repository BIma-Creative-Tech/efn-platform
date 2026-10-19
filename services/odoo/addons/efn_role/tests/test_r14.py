"""Perbaikan review keamanan r14 (L-67): SEC-154 sinkron jabatan, SEC-155 ekspor CSV review, SEC-158 notifikasi
perubahan aturan SoD, SEC-159 user admin & Superadmin terakhir."""

import csv
import io
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged

from odoo.addons.efn_role.models.efn_access_review import EXPORT_HEADER
from odoo.addons.efn_role.models.role_policy import SENSITIVE_PARAM

from .common import EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnRoleR14(EfnRoleCase):
    # ------------------------------------------------------------ SEC-154
    def test_sec154_hr_self_job_change_never_grants_sensitive_group(self):
        approver = self.env.ref("efn_role.group_refund_approver")
        job_fin = self.env["hr.job"].create({"name": "Finance Manager Uji r14", "company_id": self.gl.id})
        job_staff = self.env["hr.job"].create({"name": "Staf Uji r14", "company_id": self.gl.id})
        sensitive = self._role("Approver Refund Uji r14", approver, job_id=job_fin.id)
        self.assertTrue(sensitive.is_sensitive_role)
        self.assertFalse(sensitive.sync_from_job, "default False")
        # Constraint: flag ditolak pada peran sensitif (langsung, saat create, atau grup sensitif ditambahkan).
        with self.assertRaises(ValidationError), self.cr.savepoint():
            sensitive.write({"sync_from_job": True})
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self._role("Approver Sinkron Uji r14", approver, sync_from_job=True)
        staff = self._role("Staf Sinkron Uji r14", self.g_role1, job_id=job_staff.id, sync_from_job=True)
        self.assertFalse(staff.is_sensitive_role)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            staff.write({"group_ids": [Command.link(approver.id)]})
        # Tombol sinkron ditolak untuk peran tanpa flag.
        with self.assertRaises(UserError):
            sensitive.with_user(self.access_admin).action_efn_apply_job_roles()

        hr_user = self._make_user("efn_role_hr_r14", ["hr.group_hr_user"], companies=self.gl)
        employee = self.env["hr.employee"].create(
            {"name": "Karyawan HR Uji r14", "company_id": self.gl.id, "user_id": hr_user.id, "job_id": job_staff.id}
        )
        self.Role._cron_efn_role_daily()
        self.assertIn(self.g_role1, hr_user.group_ids, "peran non-sensitif ber-flag tetap disinkron")
        # User HR mengubah jabatannya sendiri menjadi jabatan peran sensitif.
        employee.with_user(hr_user).write({"job_id": job_fin.id})
        self.Role._cron_efn_role_daily()
        self.assertNotIn(approver, hr_user.group_ids)
        self.assertFalse(self.Line.search([("role_id", "=", sensitive.id)]), "tidak ada baris peran sensitif")
        self.assertNotIn(self.g_role1, hr_user.group_ids, "baris jabatan lama ditutup")

    def test_sec154_job_lines_closed_when_sync_disabled(self):
        job = self.env["hr.job"].create({"name": "Gudang Uji r14", "company_id": self.gl.id})
        user = self._make_user("efn_role_sync_off", companies=self.gl)
        self.env["hr.employee"].create(
            {"name": "Karyawan Gudang r14", "company_id": self.gl.id, "user_id": user.id, "job_id": job.id}
        )
        role = self._role("Gudang Sinkron r14", self.g_role1, job_id=job.id, sync_from_job=True)
        role.with_user(self.access_admin).action_efn_apply_job_roles()
        self.assertIn(self.g_role1, user.group_ids)
        role.write({"sync_from_job": False})
        self.Role._efn_sync_job_lines(roles=role)
        self.assertFalse(
            self.Line.search([("role_id", "=", role.id), ("user_id", "=", user.id), ("is_effective", "=", True)])
        )
        self.assertNotIn(self.g_role1, user.group_ids)

    def test_sec154_sensitive_list_extended_by_parameter(self):
        g_cfg = self.Groups.create({"name": "EFN Uji Sensitif Konfigurasi"})
        self.env["ir.model.data"].create(
            {"module": "efn_role_r14_test", "name": "g_sensitive_cfg", "model": "res.groups", "res_id": g_cfg.id}
        )
        role = self._role("Peran Konfigurasi r14", g_cfg, sync_from_job=True)
        self.assertFalse(role._efn_sensitive_roles())
        self.env["ir.config_parameter"].sudo().set_param(
            SENSITIVE_PARAM, "efn_role_r14_test.g_sensitive_cfg, bukan xmlid, ; tidak.ada_grup"
        )
        self.assertEqual(role._efn_sensitive_roles(), role.sudo())
        with self.assertRaises(ValidationError), self.cr.savepoint():
            role.write({"group_ids": [Command.link(self.g_role2.id)]})

    # ------------------------------------------------------------ SEC-155
    def test_sec155_auditor_csv_export_from_snapshot_only(self):
        user = self._make_user("efn_role_csv_r14", companies=self.gl)
        user.write({"name": '=HYPERLINK("http://contoh.invalid")'})
        role = self._role("Peran CSV r14", self.g_role1)
        self._assign(role, user)
        review = self.Review.create({"company_id": self.gl.id, "period": "2001-10"})
        self.assertFalse(self.auditor.has_group("base.group_allow_export"))
        action = review.with_user(self.auditor).action_efn_export_csv()
        attachment = self.env["ir.attachment"].search(
            [("res_model", "=", "efn.access.review"), ("res_id", "=", review.id)]
        )
        self.assertEqual(len(attachment), 1)
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertIn(f"/web/content/{attachment.id}", action["url"])
        content = attachment.raw.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(content)))
        self.assertEqual(rows[0], list(EXPORT_HEADER))
        self.assertEqual(len(rows) - 1, len(review.line_ids))
        mine = [row for row in rows[1:] if row[2] == str(user.id)]
        self.assertTrue(mine)
        # Formula injection dinetralkan; login tidak diekspor.
        self.assertTrue(all(row[3].startswith("'=") for row in mine))
        self.assertNotIn("efn_role_csv_r14", content)
        for column in ("user_login", "login_date", "totp_enabled", "email", "phone"):
            self.assertNotIn(column, rows[0])
        # Superadmin boleh; Access Rights tidak; review company lain ditolak record rule.
        review.with_user(self.superadmin).action_efn_export_csv()
        with self.assertRaises(AccessError):
            review.with_user(self.access_admin).action_efn_export_csv()
        review_pb = self.Review.create({"company_id": self.pb.id, "period": "2001-10"})
        with self.assertRaises(AccessError):
            review_pb.with_user(self.auditor).action_efn_export_csv()

    # ------------------------------------------------------------ SEC-158
    def test_sec158_rule_mode_and_archive_notify_auditors(self):
        g_c = self.Groups.create({"name": "EFN Uji r14 C"})
        g_d = self.Groups.create({"name": "EFN Uji r14 D"})
        rule = self.Rule.with_user(self.superadmin).create(
            {"code": "UJI-R14", "name": "Uji r14 C ⟂ D", "group_a_id": g_c.id, "group_b_id": g_d.id}
        )
        Activity = self.env["mail.activity"].sudo()
        domain = [("res_model", "=", "efn.sod.rule"), ("res_id", "=", rule.id), ("user_id", "=", self.auditor.id)]
        self.assertEqual(Activity.search_count(domain), 0)
        rule.with_user(self.superadmin).write({"rationale": "Perubahan teks saja"})
        self.assertEqual(Activity.search_count(domain), 0, "perubahan non-kunci tidak memberi notifikasi")
        rule.with_user(self.superadmin).write({"mode": "report_only"})
        self.assertEqual(Activity.search_count(domain), 1)
        self.assertTrue(rule.message_ids.filtered(lambda m: "report_only" in str(m.body or "")))
        rule.with_user(self.superadmin).action_archive()
        self.assertFalse(rule.active)
        self.assertEqual(Activity.search_count(domain), 2)
        # Access Rights (bukan auditor efektif) tidak menerima aktivitas.
        self.assertFalse(
            Activity.search_count(
                [("res_model", "=", "efn.sod.rule"), ("res_id", "=", rule.id), ("user_id", "=", self.access_admin.id)]
            )
        )
        Log = self.env["efn.audit.log"].sudo()
        log_domain = [("model_name", "=", "efn.sod.rule"), ("res_id", "=", rule.id), ("operation", "=", "write")]
        self.assertTrue(Log.search_count(log_domain))

    # ------------------------------------------------------------ SEC-159
    def test_sec159_lines_for_admin_users_superadmin_only(self):
        role = self._role("Peran Biasa r14", self.g_role1)
        erp_target = self._make_user("efn_role_erp_r14", ["base.group_erp_manager"], self.main | self.gl)
        plain = self._make_user("efn_role_plain_r14", companies=self.gl)
        for target in (self.superadmin2, erp_target):
            with self.subTest(target=target.login), self.assertRaises(AccessError), self.cr.savepoint():
                self.Line.with_user(self.access_admin).create({"role_id": role.id, "user_id": target.id})
        self.assertFalse(erp_target.efn_role_managed)
        self.Line.with_user(self.access_admin).create({"role_id": role.id, "user_id": plain.id})
        line = self.Line.with_user(self.superadmin).create({"role_id": role.id, "user_id": erp_target.id})
        self.assertIn(self.g_role1, erp_target.group_ids)
        with self.assertRaises(AccessError), self.cr.savepoint():
            line.with_user(self.access_admin).write({"date_to": self.today})
        with self.assertRaises(AccessError), self.cr.savepoint():
            line.with_user(self.access_admin).unlink()

    def test_sec159_job_sync_skips_admin_users(self):
        job = self.env["hr.job"].create({"name": "Admin IT Uji r14", "company_id": self.gl.id})
        self.env["hr.employee"].create(
            {"name": "Karyawan Admin r14", "company_id": self.gl.id, "user_id": self.superadmin2.id, "job_id": job.id}
        )
        role = self._role("Peran IT r14", self.g_role1, job_id=job.id, sync_from_job=True)
        role.with_user(self.access_admin).action_efn_apply_job_roles()
        self.assertFalse(self.Line.search([("role_id", "=", role.id), ("user_id", "=", self.superadmin2.id)]))

    def test_sec159_apply_never_removes_group_system_from_last_holder(self):
        system = self.env.ref("base.group_system")
        # Peran admin membuat base.group_system menjadi grup terkelola.
        self.Role.with_user(self.superadmin).create(
            {"name": "Peran Admin r14", "company_id": self.gl.id, "group_ids": [Command.set(system.ids)]}
        )
        plain = self._role("Peran Biasa Admin r14", self.g_role1)
        with patch.object(type(self.Role), "_efn_has_other_system_holder", return_value=False):
            self.Line.with_user(self.superadmin).create({"role_id": plain.id, "user_id": self.superadmin2.id})
        self.assertIn(system, self.superadmin2.group_ids, "Superadmin terakhir tetap memegang group_system")
        self.assertIn(self.g_role1, self.superadmin2.group_ids)
        # Masih ada Superadmin lain -> penetapan oleh Superadmin berlaku penuh ("nothing more, nothing less").
        sa3 = self._make_user("efn_role_sa3_r14", ["base.group_system"], self.main | self.gl | self.pb)
        self.Line.with_user(self.superadmin).create({"role_id": plain.id, "user_id": sa3.id})
        self.assertNotIn(system, sa3.group_ids)
