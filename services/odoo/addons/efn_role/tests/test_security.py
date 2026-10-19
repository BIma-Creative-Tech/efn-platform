"""E-47 keamanan: peran admin (13), record rule multi-company (14), audit efn_audit (15), flag & ACL."""

from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged

from .common import REASON, EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnRoleSecurity(EfnRoleCase):
    def test_13_admin_role_only_by_superadmin(self):
        system = self.env.ref("base.group_system")
        erp = self.env.ref("base.group_erp_manager")
        with self.assertRaises(AccessError):
            self.Role.with_user(self.access_admin).create(
                {"name": "Peran Admin Uji", "company_id": self.gl.id, "group_ids": [Command.set(system.ids)]}
            )
        with self.assertRaises(AccessError):
            self.Role.with_user(self.access_admin).create(
                {"name": "Peran Access Uji", "company_id": self.gl.id, "group_ids": [Command.set(erp.ids)]}
            )
        plain = self.Role.with_user(self.access_admin).create(
            {"name": "Peran Biasa Uji", "company_id": self.gl.id, "group_ids": [Command.set(self.g_role1.ids)]}
        )
        with self.assertRaises(AccessError):
            plain.with_user(self.access_admin).write({"group_ids": [Command.link(system.id)]})
        admin_role = self.Role.with_user(self.superadmin).create(
            {"name": "Peran Admin Uji SA", "company_id": self.gl.id, "group_ids": [Command.set(system.ids)]}
        )
        self.assertTrue(admin_role.is_admin_role)
        target = self._make_user("efn_role_admin_target")
        with self.assertRaises(AccessError):
            self.Line.with_user(self.access_admin).create(
                {"role_id": admin_role.id, "user_id": target.id, "reason": REASON}
            )
        with self.assertRaises(ValidationError):
            self.Line.with_user(self.superadmin).create({"role_id": admin_role.id, "user_id": target.id})
        line = self.Line.with_user(self.superadmin).create(
            {"role_id": admin_role.id, "user_id": target.id, "reason": REASON}
        )
        self.assertTrue(line.is_effective)
        self.assertIn(system, target.group_ids)
        with self.assertRaises(AccessError):
            line.with_user(self.access_admin).unlink()

    def test_14_record_rules_isolate_companies(self):
        gl_admin = self._make_user("efn_role_gl_admin", ["base.group_erp_manager"], companies=self.gl)
        gl_auditor = self._make_user("efn_role_gl_auditor", ["efn_audit.group_auditor"], companies=self.gl)
        role_gl = self._role("Peran GL Uji", self.g_role1, company=self.gl)
        role_pb = self._role("Peran PB Uji", self.g_role1, company=self.pb)
        ids = [role_gl.id, role_pb.id]
        self.assertEqual(self.Role.with_user(gl_admin).search([("id", "in", ids)]), role_gl)
        self.assertEqual(self.Role.with_user(gl_auditor).search([("id", "in", ids)]), role_gl)
        user = self._make_user("efn_role_rr_user", companies=self.gl | self.pb)
        line_gl = self._assign(role_gl, user)
        line_pb = self._assign(role_pb, user)
        self.assertEqual(self.Line.with_user(gl_admin).search([("id", "in", (line_gl | line_pb).ids)]), line_gl)
        review_gl = self.Review.create({"company_id": self.gl.id, "period": "2001-09"})
        review_pb = self.Review.create({"company_id": self.pb.id, "period": "2001-09"})
        reviews = self.Review.with_user(gl_auditor).search([("id", "in", (review_gl | review_pb).ids)])
        self.assertEqual(reviews, review_gl)
        ReviewLine = self.env["efn.access.review.line"].with_user(gl_auditor)
        self.assertFalse(ReviewLine.search([("review_id", "=", review_pb.id)]))
        self.assertTrue(ReviewLine.search([("review_id", "=", review_gl.id)]))
        with self.assertRaises(AccessError):
            role_pb.with_user(gl_admin).read(["name"])

    def test_14b_plain_user_no_access(self):
        plain = self._make_user("efn_role_plain")
        for model in ("efn.role", "efn.role.line", "efn.sod.rule", "efn.sod.exception", "efn.access.review"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                self.env[model].with_user(plain).search([])

    def test_15_audit_logs_for_role_models(self):
        user = self._make_user("efn_role_audit_user", extra=self.g_a)
        role = self._role("Peran Audit Uji", self.g_role1)
        line = self._assign(role, user)
        g_x = self.Groups.create({"name": "EFN Uji Audit X"})
        rule = self.Rule.create({"code": "UJI-AUD", "name": "audit", "group_a_id": g_x.id, "group_b_id": self.g_a.id})
        exc = self.Exc.with_user(self.superadmin).create(
            {"rule_id": self.rule_ab.id, "user_id": user.id, "reason": REASON}
        )
        role.write({"note": "Catatan uji audit"})
        Log = self.env["efn.audit.log"].sudo()
        for record in (role, line, rule, exc):
            with self.subTest(model=record._name):
                self.assertTrue(
                    Log.search_count(
                        [("model_name", "=", record._name), ("res_id", "=", record.id), ("operation", "=", "create")]
                    )
                )
        self.assertTrue(
            Log.search_count([("model_name", "=", "efn.role"), ("res_id", "=", role.id), ("operation", "=", "write")])
        )
        # Log penetapan tidak memuat nama/login user (relasi orang = res.users,id).
        line_log = Log.search([("model_name", "=", "efn.role.line"), ("res_id", "=", line.id)], limit=1)
        texts = " ".join(filter(None, line_log.line_ids.mapped("new_value")))
        self.assertNotIn(user.login, texts)
        self.assertNotIn(user.name, texts)

    def test_managed_flag_not_settable_by_access_admin(self):
        user = self._make_user("efn_role_flag")
        with self.assertRaises(AccessError):
            user.with_user(self.access_admin).write({"efn_role_managed": True})
        created = self.env["res.users"].create(
            {"name": "Flag Uji", "login": "efn_role_flag2", "efn_role_managed": True}
        )
        self.assertFalse(created.efn_role_managed, "flag diabaikan saat create")
