"""ACL, multi-company, immutability, retensi, erasure, performa dasar (docs/08 §5.1)."""

import logging
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.efn_audit.models.base import Base as EfnAuditBase

from .common import EfnAuditCase

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditSecurity(EfnAuditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "PT Audit Lain"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        cls.auditor_a = Users.create(
            {
                "name": "Auditor PT A",
                "login": "auditor.a.efn",
                "company_id": cls.company_a.id,
                "company_ids": [Command.set(cls.company_a.ids)],
                "group_ids": [
                    Command.set([cls.env.ref("base.group_user").id, cls.env.ref("efn_audit.group_auditor").id])
                ],
            }
        )
        cls.plain_user = Users.create(
            {
                "name": "User Biasa Audit",
                "login": "plain.audit.efn",
                "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
            }
        )

    def setUp(self):
        super().setUp()
        self._set_rule("res.partner", tracked=("name",))
        Partner = self.env["res.partner"]
        self.partner_a = Partner.create({"name": "Partner PT A", "company_id": self.company_a.id})
        self.partner_b = Partner.create({"name": "Partner PT B", "company_id": self.company_b.id})
        (self.partner_a | self.partner_b).write({"name": "Diubah"})
        self.log_a = self._logs(self.partner_a, "write")
        self.log_b = self._logs(self.partner_b, "write")

    # ---------------------------------------------------------------- ACL
    def test_non_auditor_cannot_read(self):
        with self.assertRaises(AccessError):
            self.env["efn.audit.log"].with_user(self.plain_user).search([])
        with self.assertRaises(AccessError):
            self.env["efn.audit.log.line"].with_user(self.plain_user).search([])

    def test_auditor_sees_own_company_only(self):
        Log = self.env["efn.audit.log"].with_user(self.auditor_a)
        found = Log.search([("id", "in", (self.log_a | self.log_b).ids)])
        self.assertEqual(found, self.log_a)
        lines = (
            self.env["efn.audit.log.line"]
            .with_user(self.auditor_a)
            .search([("log_id", "in", (self.log_a | self.log_b).ids)])
        )
        self.assertEqual(lines.log_id, self.log_a)

    def test_auditor_cannot_manage_rules(self):
        with self.assertRaises(AccessError):
            self.env["efn.audit.rule"].with_user(self.auditor_a).create(
                {"name": "x", "model_id": self.env["ir.model"]._get_id("res.country")}
            )

    def test_admin_is_manager(self):
        self.assertTrue(self.env.ref("base.user_admin").has_group("efn_audit.group_manager"))

    # ------------------------------------------------------- immutability
    def test_log_immutable_even_for_admin(self):
        line = self.log_a.line_ids[:1]
        for log in (self.log_a.sudo(), self.log_a.with_user(self.env.ref("base.user_admin"))):
            with self.assertRaises(UserError):
                log.write({"res_name": "palsu"})
            with self.assertRaises(UserError):
                log.unlink()
        with self.assertRaises(UserError):
            line.sudo().write({"new_value": "palsu"})
        with self.assertRaises(UserError):
            line.sudo().unlink()
        # Penanda purge lewat context (bisa diisi klien RPC) TIDAK berlaku.
        with self.assertRaises(UserError):
            self.log_a.sudo().with_context(efn_audit_purge=True).unlink()
        with self.assertRaises(AccessError):
            self.env["efn.audit.log"].with_user(self.auditor_a).create(
                {"model_name": "x", "user_id": self.auditor_a.id, "operation": "write"}
            )

    # ------------------------------------------------------------ retensi
    def _age(self, logs, days):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_audit_log SET create_date = %s WHERE id = ANY(%s)",
            [fields.Datetime.now() - timedelta(days=days), logs.ids],
        )
        logs.invalidate_recordset(["create_date"])

    def test_autovacuum_retention(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.audit.retention_days", "30")
        self._age(self.log_a, 40)
        self._age(self.log_b, 10)
        count = self.Log._gc_efn_audit_logs()
        self.assertGreaterEqual(count, 1)
        self.assertFalse(self.log_a.exists())
        self.assertTrue(self.log_b.exists())
        purge = self.Log.search([("operation", "=", "purge"), ("note", "=like", "retensi:%")], limit=1)
        self.assertTrue(purge)
        self.assertNotIn("Partner", purge.note)

    def test_autovacuum_rule_retention_and_forever(self):
        rule = self.Rule.search([("model_name", "=", "res.partner")])
        self._age(self.log_a | self.log_b, 50)
        self.env["ir.config_parameter"].sudo().set_param("efn.audit.retention_days", "0")
        self.Log._gc_efn_audit_logs()
        self.assertTrue(self.log_a.exists(), "0 = simpan selamanya")
        rule.retention_days = 40
        self.Log._gc_efn_audit_logs()
        self.assertFalse((self.log_a | self.log_b).exists(), "retensi per aturan menang")

    def test_global_retention_invalid_defaults(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.audit.retention_days", "abc")
        self.assertEqual(self.Log._efn_audit_global_retention(), 730)

    # ------------------------------------------------------------ erasure
    def test_purge_records(self):
        self._set_rule("res.partner.category", tracked=("partner_ids",))
        category = self.env["res.partner.category"].create({"name": "Kategori Erasure"})
        category.write({"partner_ids": [Command.set(self.partner_a.ids)]})
        ref_line = self._line(self._logs(category, "write"), "partner_ids")
        self.assertTrue(ref_line.new_ref)
        removed = self.Log._efn_audit_purge_records("res.partner", self.partner_a.ids)
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(self._logs(self.partner_a))
        self.assertTrue(self._logs(self.partner_b), "log partner lain tetap")
        self.assertFalse(ref_line.new_ref or ref_line.new_value, "ref di model lain dikosongkan")
        purge = self.Log.search([("operation", "=", "purge"), ("model_name", "=", "res.partner")], limit=1)
        self.assertTrue(purge)
        self.assertFalse(purge.res_id)
        self.assertNotIn("PT A", purge.note or "")
        self.assertEqual(self.Log._efn_audit_purge_records("res.partner", []), 0)

    # ------------------------------------------------------------ performa
    def _count_queries(self, func):
        self.env.flush_all()
        before = self.env.cr.sql_log_count
        func()
        self.env.flush_all()
        return self.env.cr.sql_log_count - before

    def test_no_rule_no_overhead(self):
        country = self.env["res.country"].search([], limit=1)
        self.Rule.search([("model_name", "=", "res.country")]).unlink()
        country.write({"name": "Pemanasan"})  # isi cache aturan
        with_hook = self._count_queries(lambda: country.write({"name": "Overhead 1"}))
        with patch.object(EfnAuditBase, "_efn_audit_rule", autospec=True, return_value=None):
            without = self._count_queries(lambda: country.write({"name": "Overhead 2"}))
        self.assertEqual(with_hook, without, "model tanpa aturan: tanpa query tambahan")

    def test_batch_write_constant_queries(self):
        partners = self.env["res.partner"].create([{"name": f"Batch {i}"} for i in range(50)])
        rule_queries = self._count_queries(lambda: partners.write({"name": "Batch Ubah"}))
        with patch.object(EfnAuditBase, "_efn_audit_rule", autospec=True, return_value=None):
            plain = self._count_queries(lambda: partners.write({"name": "Batch Ubah 2"}))
        self.assertEqual(
            len(
                self.Log.search(
                    [("model_name", "=", "res.partner"), ("res_id", "in", partners.ids), ("operation", "=", "write")]
                )
            ),
            50,
        )
        _logger.info("EFN audit perf: write 50 partner = %d query (tanpa audit %d)", rule_queries, plain)
        # Overhead tidak sebanding jumlah record (batch read + batch create log/baris).
        self.assertLess(rule_queries - plain, 25, f"overhead {rule_queries - plain} query untuk 50 record")
