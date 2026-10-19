"""E-47 penegakan SoD (docs/08 §12.3-§12.4; L-64; test wajib 6, 7, 8, 10, 11 + aturan bawaan)."""

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged

from .common import EfnRoleCase


@tagged("post_install", "-at_install", "efn")
class TestEfnSod(EfnRoleCase):
    def _default_rule(self, code):
        self.Rule._efn_ensure_default_rules()
        rule = self.Rule.with_context(active_test=False).search([("code", "=", code)])
        if not rule:
            return rule
        if not rule.active or rule.mode != "block":
            rule.write({"active": True, "mode": "block"})
        return rule

    # -------------------------------------------------------------- (6) SOD-02
    def test_06_sod02_cashier_with_pos_manager_direct_and_implied(self):
        pos_manager = self.env.ref("point_of_sale.group_pos_manager", raise_if_not_found=False)
        if pos_manager is None:
            self.skipTest("point_of_sale tidak terpasang")
        rule = self._default_rule("SOD-02")
        self.assertTrue(rule)
        cashier = self._make_user("efn_role_kasir", ["efn_role.group_role_cashier"])
        with self.assertRaises(ValidationError) as caught:
            cashier.write({"group_ids": [Command.link(pos_manager.id)]})
        self.assertIn("SOD-02", str(caught.exception))
        self.assertNotIn(cashier.login, str(caught.exception), "pesan tanpa login user")
        proxy = self.Groups.create({"name": "EFN Uji Proxy POS", "implied_ids": [Command.link(pos_manager.id)]})
        with self.assertRaises(ValidationError):
            cashier.write({"group_ids": [Command.link(proxy.id)]})
        with self.assertRaises(ValidationError):
            proxy.write({"user_ids": [Command.link(cashier.id)]})
        self.assertNotIn(pos_manager, cashier.all_group_ids)

    def test_06b_generic_direct_and_implied(self):
        user = self._make_user("efn_role_sod_gen", extra=self.g_a)
        with self.assertRaises(ValidationError):
            user.write({"group_ids": [Command.link(self.g_b.id)]})
        proxy = self.Groups.create({"name": "EFN Uji Proxy B", "implied_ids": [Command.link(self.g_b.id)]})
        with self.assertRaises(ValidationError):
            user.write({"group_ids": [Command.link(proxy.id)]})
        # Grup lain menjadi tersirat B setelah user memilikinya.
        holder = self.Groups.create({"name": "EFN Uji Holder"})
        user.write({"group_ids": [Command.link(holder.id)]})
        with self.assertRaises(ValidationError):
            holder.write({"implied_ids": [Command.link(self.g_b.id)]})
        # Superadmin tidak kebal.
        with self.assertRaises(ValidationError):
            self.superadmin.write({"group_ids": [Command.link(self.g_a.id), Command.link(self.g_b.id)]})

    def test_06c_role_assignment_violating_sod_rejected(self):
        user = self._make_user("efn_role_sod_role", extra=self.g_a)
        role = self._role("Peran Uji Melanggar", self.g_b)
        with self.assertRaises(ValidationError):
            self._assign(role, user)
        self.assertNotIn(self.g_b, user.group_ids)

    # ---------------------------------------------------------- (7) sisi grup
    def test_07_sod_via_group_user_ids(self):
        user = self._make_user("efn_role_sod_grp", extra=self.g_a)
        with self.assertRaises(ValidationError):
            self.g_b.write({"user_ids": [Command.link(user.id)]})
        self.assertNotIn(self.g_b, user.group_ids)

    def test_07b_sod03_loyalty_manager_via_group_side(self):
        loyalty = self.env.ref("efn_loyalty_accounting.group_loyalty_manager", raise_if_not_found=False)
        account_manager = self.env.ref("account.group_account_manager", raise_if_not_found=False)
        if loyalty is None or account_manager is None:
            self.skipTest("efn_loyalty_accounting / account tidak terpasang")
        rule = self._default_rule("SOD-03")
        self.assertTrue(rule)
        user = self._make_user("efn_role_sod03", ["account.group_account_manager"])
        with self.assertRaises(ValidationError) as caught:
            loyalty.write({"user_ids": [Command.link(user.id)]})
        self.assertIn("SOD-03", str(caught.exception))

    # ---------------------------------------------- (8) implied ke group_user
    def test_08_implied_on_base_group_user_rejected(self):
        self._make_user("efn_role_sod_impl", extra=self.g_a)
        with self.assertRaises(ValidationError):
            self.group_user.write({"implied_ids": [Command.link(self.g_b.id)]})
        self.assertNotIn(self.g_b, self.group_user.implied_ids)
        with self.assertRaises(ValidationError):
            self.g_b.write({"implied_by_ids": [Command.link(self.group_user.id)]})

    def test_08b_unrelated_group_change_not_blocked_by_old_violation(self):
        """Baseline: pelanggaran lama (report_only -> tidak relevan) / user lain tidak memblokir perubahan lain."""
        user = self._make_user("efn_role_sod_base", extra=self.g_a)
        other = self.Groups.create({"name": "EFN Uji Tak Terkait"})
        other.write({"user_ids": [Command.link(user.id)]})
        self.assertIn(other, user.group_ids)

    # ---------------------------------------------- (10) report_only -> block
    def test_10_report_only_to_block_rejected_with_count(self):
        g_c = self.Groups.create({"name": "EFN Uji SoD C"})
        g_d = self.Groups.create({"name": "EFN Uji SoD D"})
        rule = self.Rule.create(
            {"code": "UJI-CD", "name": "Uji C ⟂ D", "group_a_id": g_c.id, "group_b_id": g_d.id, "mode": "report_only"}
        )
        user = self._make_user("efn_role_sod_cd", extra=g_c | g_d)
        self.assertEqual(rule.violation_count, 1)
        with self.assertRaises(ValidationError) as caught:
            rule.write({"mode": "block"})
        message = str(caught.exception)
        self.assertIn("UJI-CD", message)
        self.assertIn("1", message)
        self.assertNotIn(user.login, message)
        # Arsip -> aktif sebagai block juga ditolak.
        rule.write({"active": False})
        with self.assertRaises(ValidationError):
            rule.write({"active": True, "mode": "block"})
        # Setelah pelanggaran selesai -> boleh.
        user.write({"group_ids": [Command.unlink(g_d.id)]})
        rule.write({"active": True, "mode": "block"})
        self.assertEqual(rule.mode, "block")

    def test_10b_rule_admin_only_and_no_delete(self):
        g_c = self.Groups.create({"name": "EFN Uji SoD E1"})
        with self.assertRaises(AccessError):
            self.Rule.with_user(self.access_admin).create(
                {"code": "UJI-X", "name": "x", "group_a_id": g_c.id, "group_b_id": self.g_a.id}
            )
        with self.assertRaises(AccessError):
            self.rule_ab.with_user(self.access_admin).write({"mode": "report_only"})
        with self.assertRaises(UserError):
            self.rule_ab.with_user(self.superadmin).unlink()
        with self.assertRaises(ValidationError):
            self.Rule.create({"code": "UJI-BA", "name": "dup", "group_a_id": self.g_b.id, "group_b_id": self.g_a.id})
        with self.assertRaises(ValidationError):
            self.Rule.create({"code": "UJI-AA", "name": "same", "group_a_id": self.g_a.id, "group_b_id": self.g_a.id})
        rule = self.Rule.with_user(self.superadmin).create(
            {"code": "UJI-SA", "name": "oleh superadmin", "group_a_id": g_c.id, "group_b_id": self.g_a.id}
        )
        self.assertTrue(rule)

    def test_10c_archived_rule_not_enforced(self):
        self.rule_ab.write({"active": False})
        user = self._make_user("efn_role_sod_arch", extra=self.g_a | self.g_b)
        self.assertIn(self.g_b, user.group_ids)

    # ---------------------------------------------- (11) aturan per company
    def test_11_company_rule_only_when_company_in_user_companies(self):
        g_e = self.Groups.create({"name": "EFN Uji SoD E"})
        g_f = self.Groups.create({"name": "EFN Uji SoD F"})
        self.Rule.create(
            {
                "code": "UJI-EF",
                "name": "Uji E ⟂ F (PB)",
                "company_id": self.pb.id,
                "group_a_id": g_e.id,
                "group_b_id": g_f.id,
            }
        )
        user = self._make_user("efn_role_sod_ef", companies=self.gl, extra=g_e | g_f)
        self.assertIn(g_f, user.group_ids, "aturan PB tidak berlaku bagi user tanpa PB")
        with self.assertRaises(ValidationError):
            user.write({"company_ids": [Command.link(self.pb.id)]})
        self.assertNotIn(self.pb, user.company_ids)

    # ------------------------------------------------------ aturan bawaan
    def test_default_rules_created_idempotent(self):
        self.Rule._efn_ensure_default_rules()
        Rule = self.Rule.with_context(active_test=False)
        sod06 = Rule.search([("code", "=", "SOD-06")])
        self.assertEqual(len(sod06), 1)
        self.assertEqual(sod06.mode, "report_only")
        self.assertEqual(sod06.group_a_id, self.env.ref("efn_audit.group_auditor"))
        before = Rule.search_count([("code", "like", "SOD-%")])
        self.Rule._efn_ensure_default_rules()
        self.assertEqual(Rule.search_count([("code", "like", "SOD-%")]), before)
        self.assertFalse(Rule.search([("code", "=", "SOD-04")]), "SOD-04 tidak dibuat (CE loyalty tanpa persetujuan)")
        if self.env.ref("sales_team.group_sale_manager", raise_if_not_found=False):
            sod01 = Rule.search([("code", "=", "SOD-01")])
            self.assertEqual(sod01.group_b_id, self.env.ref("efn_role.group_refund_approver"))

    def test_marker_groups_have_no_access(self):
        for xmlid in ("efn_role.group_role_cashier", "efn_role.group_refund_approver"):
            group = self.env.ref(xmlid)
            with self.subTest(group=xmlid):
                self.assertFalse(group.model_access)
                self.assertFalse(group.rule_groups)
                self.assertFalse(group.implied_ids)
