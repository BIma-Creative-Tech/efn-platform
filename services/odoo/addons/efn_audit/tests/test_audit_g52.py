"""G52 (docs/04 §28.15.3): cabang ESB kepala toko F&B = hak akses -> model/field kontrol efn_audit.

Model `efn.insight.esb.branch` dan field `res.users.efn_insight_esb_branch_ids` milik efn_insight (bukan dependency
efn_audit): bila belum terpasang, spesifikasi dilewati dan test di-skip.
"""

from odoo import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_audit.models.audit_policy import CONTROL_SPECS, PROTECTED_LOG_MODELS

from .common import EfnAuditCase

BRANCH_MODEL = "efn.insight.esb.branch"
BRANCH_FIELDS = ("company_id", "esb_brand", "esb_branch_id", "is_store", "active")


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditG52(EfnAuditCase):
    def test_control_specs(self):
        self.assertIn("efn_insight_esb_branch_ids", CONTROL_SPECS["res.users"][0])
        self.assertEqual(CONTROL_SPECS[BRANCH_MODEL], (BRANCH_FIELDS, None))
        self.assertIn(BRANCH_MODEL, PROTECTED_LOG_MODELS)
        rule = self.Rule._efn_get_rule(BRANCH_MODEL)
        if BRANCH_MODEL not in self.env:
            self.assertIsNone(rule)
            return
        self.assertTrue(rule.control)
        self.assertEqual(rule.tracked, frozenset(BRANCH_FIELDS))

    def _branch(self):
        if BRANCH_MODEL not in self.env:
            self.skipTest("efn_insight tidak terpasang")
        Company = self.env["res.company"]
        company = Company.search([("efn_brand_code", "=", "PB")], limit=1)
        if not company:
            company = Company.with_context(active_test=False).search([("efn_brand_code", "=", "PB")], limit=1)
            if company:
                self.skipTest("PT PB diarsipkan")
            company = Company.create({"name": "PT Uji Audit G52 PB", "efn_brand_code": "PB"})
        self.env[BRANCH_MODEL].with_context(active_test=False).search([("esb_branch_id", "=", 9301)]).unlink()
        return self.env[BRANCH_MODEL].create(
            {"company_id": company.id, "esb_brand": "pb", "esb_branch_id": 9301, "name": "Audit G52"}
        )

    def test_branch_changes_logged(self):
        branch = self._branch()
        self.assertTrue(self._logs(branch, "create"))
        branch.write({"is_store": False})
        line = self._line(self._logs(branch, "write").sorted("id")[-1], "is_store")
        self.assertEqual(len(line), 1)
        branch.write({"name": "Nama baru"})  # name tidak dilacak
        names = self._logs(branch, "write").line_ids.filtered(lambda r: r.field_name == "name")
        self.assertFalse(names)

    def test_user_branch_assignment_logged(self):
        branch = self._branch()
        user = new_test_user(self.env, login="efn_audit_g52_user", groups="base.group_user")
        user.write({"efn_insight_esb_branch_ids": [Command.link(branch.id)]})
        line = self._logs(user, "write").line_ids.filtered(lambda r: r.field_name == "efn_insight_esb_branch_ids")
        self.assertEqual(len(line), 1)
        self.assertTrue(line.new_value.startswith("ditambah: "))
