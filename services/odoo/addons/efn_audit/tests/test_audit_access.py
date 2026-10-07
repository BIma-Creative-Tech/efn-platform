"""SEC-49 (review r5, Gelombang 13): perubahan hak akses = model kontrol.

Grup user (dua sisi relasi: `res.users.group_ids` dan `res.groups.user_ids`/`implied_ids`), record rule,
ACL, API key, serta aktif/login/company user selalu diaudit tanpa aturan admin, tidak ikut GC, dan
masuk sink (`audit_op` + xmlid grup + uid, tanpa nama/login).
"""

import json
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.efn_audit.models.audit_policy import PROTECTED_LOG_MODELS

from .common import EfnAuditCase

SINK_LOGGER = "odoo.addons.efn_audit.sink"


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditAccess(EfnAuditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.group_user = cls.env.ref("base.group_user")
        cls.group_system = cls.env.ref("base.group_system")
        cls.group_personal = cls.env.ref("efn_audit.group_auditor_personal")
        cls.target = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Budi Target Akses",
                    "login": "budi.target@example.com",
                    "group_ids": [Command.set([cls.group_user.id])],
                }
            )
        )

    def setUp(self):
        super().setUp()
        self.env.cr.postcommit.clear()

    def _sink_events(self):
        with self.assertLogs(SINK_LOGGER, "INFO") as captured:
            self.env.cr.postcommit.run()
        events = []
        for record in captured.records:
            message = record.getMessage()
            self.assertTrue(message.startswith("EFN_AUDIT_SINK {"), message)
            events.append(json.loads(message.split(" ", 1)[1]))
        return events, "\n".join(captured.output)

    # ------------------------------------------------------- res.users grup
    def test_specs_cover_access_models(self):
        for model in ("res.users", "res.groups", "ir.rule", "ir.model.access", "res.users.apikeys"):
            self.assertIn(model, PROTECTED_LOG_MODELS, model)
        # Tanpa aturan admin pun aktif.
        for model in ("res.groups", "ir.rule", "ir.model.access", "res.users.apikeys"):
            self.assertFalse(self.Rule.search([("model_name", "=", model)]), model)
        tracked = self.Rule._efn_get_rule("res.users").tracked
        self.assertTrue({"group_ids", "active", "login", "company_ids", "company_id"} <= tracked)
        self.assertFalse({"password", "signature"} & tracked)
        self.assertFalse(self.Rule._efn_get_rule("res.users.apikeys").tracked & {"key", "index"})
        self.assertTrue(
            {"user_ids", "implied_ids", "implied_by_ids", "name"} <= self.Rule._efn_get_rule("res.groups").tracked
        )
        self.assertTrue({"domain_force", "groups", "active", "perm_read"} <= self.Rule._efn_get_rule("ir.rule").tracked)

    def test_grant_personal_auditor_via_user(self):
        """Skenario r5 #1: admin memberi grup Auditor Data Pribadi -> log + sink critical."""
        self.target.write({"group_ids": [Command.link(self.group_personal.id)]})
        log = self._logs(self.target, "write")
        self.assertEqual(len(log), 1)
        line = self._line(log, "group_ids")
        self.assertEqual(line.new_value, "ditambah: efn_audit.group_auditor_personal")
        self.assertFalse(line.old_value)
        self.assertIn(f",{self.group_personal.id}", line.new_ref)
        self.assertEqual(log.res_name, f"res.users,{self.target.id}", "tanpa nama user")
        self.assertEqual(log.company_id, self.target.company_id, "auditor PT melihat perubahan grup user PT-nya")
        events, output = self._sink_events()
        event = next(e for e in events if e["model"] == "res.users" and e["res_id"] == self.target.id)
        self.assertEqual(event["audit_op"], "access_grant")
        self.assertTrue(event["critical"])
        self.assertEqual(event["groups_added"], ["efn_audit.group_auditor_personal"])
        self.assertEqual(event["users_added"], [self.target.id])
        self.assertNotIn("Budi", output)
        self.assertNotIn("budi.target", output)
        # Pencabutan -> access_revoke, tidak critical.
        self.target.write({"group_ids": [Command.unlink(self.group_personal.id)]})
        revoke = self._line(self._logs(self.target, "write").sorted("id")[-1], "group_ids")
        self.assertEqual(revoke.old_value, "dihapus: efn_audit.group_auditor_personal")
        events, _output = self._sink_events()
        event = next(e for e in events if e["model"] == "res.users")
        self.assertEqual((event["audit_op"], event["critical"]), ("access_revoke", False))
        self.assertEqual(event["groups_removed"], ["efn_audit.group_auditor_personal"])

    def test_grant_system_via_group_side(self):
        """Relasi yang sama dari sisi res.groups (user_ids) juga tercatat."""
        self.group_system.write({"user_ids": [Command.link(self.target.id)]})
        log = self._logs(self.group_system, "write")
        self.assertEqual(len(log), 1)
        line = self._line(log, "user_ids")
        self.assertEqual(line.new_value, f"ditambah: res.users,{self.target.id}", "uid, bukan nama")
        self.assertFalse(log.is_personal)
        events, _output = self._sink_events()
        event = next(e for e in events if e["model"] == "res.groups")
        self.assertEqual(event["audit_op"], "access_grant")
        self.assertTrue(event["critical"])
        self.assertEqual(event["groups_added"], ["base.group_system"])
        self.assertEqual(event["users_added"], [self.target.id])

    def test_implied_group_escalation_critical(self):
        group = self.env["res.groups"].create({"name": "Grup Uji Implied"})
        events, _output = self._sink_events()
        self.assertTrue(any(e["model"] == "res.groups" and e["operation"] == "create" for e in events))
        group.write({"implied_ids": [Command.link(self.group_system.id)]})
        line = self._line(self._logs(group, "write"), "implied_ids")
        self.assertEqual(line.new_value, "ditambah: base.group_system")
        events, _output = self._sink_events()
        event = next(e for e in events if e["model"] == "res.groups" and e["res_id"] == group.id)
        self.assertEqual((event["audit_op"], event["critical"]), ("access_grant", True))
        self.assertEqual(event["groups_added"], ["base.group_system"])
        # Grup biasa -> tidak critical.
        other = self.env["res.groups"].create({"name": "Grup Uji Biasa"})
        self.env.cr.postcommit.clear()
        other.write({"user_ids": [Command.link(self.target.id)]})
        events, _output = self._sink_events()
        event = next(e for e in events if e["res_id"] == other.id)
        self.assertEqual((event["audit_op"], event["critical"]), ("access_grant", False))
        self.assertEqual(event["groups_added"], [f"res.groups,{other.id}"])

    def test_new_user_default_groups_logged(self):
        user = (
            self.env["res.users"].with_context(no_reset_password=True).create({"name": "Baru", "login": "baru.akses"})
        )
        log = self._logs(user, "create")
        self.assertEqual(len(log), 1)
        fnames = set(log.line_ids.mapped("field_name"))
        self.assertIn("group_ids", fnames, "grup default (bukan dari vals) tetap tercatat")
        self.assertIn("login", fnames)
        self.assertTrue(self._line(log, "login").is_masked)
        events, _output = self._sink_events()
        event = next(e for e in events if e["model"] == "res.users" and e["res_id"] == user.id)
        self.assertEqual(event["audit_op"], "access_grant")

    def test_user_active_login_company(self):
        company_b = self.env["res.company"].create({"name": "PT Akses B"})
        self.target.write({"company_ids": [Command.link(company_b.id)]})
        self.target.write({"login": "budi.baru@example.com"})
        self.target.write({"active": False})
        logs = self._logs(self.target, "write").sorted("id")
        # Odoo dapat menulis ulang user (mis. company_id) di dalam write yang sama -> cari per field.
        lines = logs.line_ids
        self.assertIn(
            "ditambah: PT Akses B", lines.filtered(lambda r: r.field_name == "company_ids").mapped("new_value")
        )
        login = lines.filtered(lambda r: r.field_name == "login")
        self.assertEqual(len(login), 1)
        self.assertTrue(login.is_masked)
        self.assertEqual((login.old_value, login.new_value), ("b***@example.com", "b***@example.com"))
        self.assertIn("False", lines.filtered(lambda r: r.field_name == "active").mapped("new_value"))
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT count(*) FROM efn_audit_log_line WHERE concat(old_value, new_value) ILIKE %s",
            ["%budi.%"],
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0, "login lengkap tidak disimpan")
        events, _output = self._sink_events()
        ops = {e["audit_op"] for e in events if e["model"] == "res.users"}
        self.assertIn("user_change", ops)
        # Odoo menambah grup multi-company otomatis saat user punya > 1 company: implisit pun tercatat.
        grants = [e for e in events if e["model"] == "res.users" and e["audit_op"] == "access_grant"]
        self.assertEqual([e["groups_added"] for e in grants], [["base.group_multi_company"]])
        self.assertFalse(grants[0]["critical"])
        # Non-tracked field: tanpa log.
        before = len(self._logs(self.target, "write"))
        self.target.write({"tz": "Asia/Jakarta"})
        self.assertEqual(len(self._logs(self.target, "write")), before)

    # ------------------------------------------------------ ir.rule & ACL
    def test_record_rule_and_acl_changes(self):
        rule = self.env["ir.rule"].create(
            {
                "name": "Rule Uji Akses",
                "model_id": self.env["ir.model"]._get_id("res.partner.category"),
                "domain_force": "[('id', '!=', 0)]",
                "groups": [Command.link(self.group_user.id)],
            }
        )
        rule.write({"domain_force": "[(1, '=', 1)]", "active": False})
        create = self._logs(rule, "create")
        self.assertEqual(self._line(create, "groups").new_value, "base.group_user")
        write = self._logs(rule, "write")
        self.assertEqual(self._line(write, "domain_force").new_value, "[(1, '=', 1)]")
        self.assertEqual(self._line(write, "active").new_value, "False")
        acl = self.env["ir.model.access"].create(
            {
                "name": "ACL Uji Akses",
                "model_id": self.env["ir.model"]._get_id("res.partner.category"),
                "group_id": self.group_user.id,
                "perm_read": True,
                "perm_unlink": True,
            }
        )
        acl_id = acl.id
        acl.unlink()
        self.assertEqual(len(self._logs(acl, "create")), 1)
        gone = self.Log.search(
            [("model_name", "=", "ir.model.access"), ("res_id", "=", acl_id), ("operation", "=", "unlink")]
        )
        self.assertEqual(self._line(gone, "perm_unlink").old_value, "True")
        events, _output = self._sink_events()
        ops = {(e["model"], e["operation"]): e for e in events}
        for key in (
            ("ir.rule", "create"),
            ("ir.rule", "write"),
            ("ir.model.access", "create"),
            ("ir.model.access", "unlink"),
        ):
            self.assertEqual(ops[key]["audit_op"], "access_rule_change", key)
            self.assertTrue(ops[key]["critical"], key)

    # ------------------------------------------------------------ API key
    def test_api_key_create_and_remove(self):
        Keys = self.env["res.users.apikeys"].with_user(self.target).sudo()
        secret = Keys._generate(None, "Key Uji Audit", fields.Datetime.now() + timedelta(days=30))
        key = Keys.search([("name", "=", "Key Uji Audit"), ("user_id", "=", self.target.id)])
        self.assertEqual(len(key), 1)
        log = self._logs(key, "create")
        self.assertEqual(len(log), 1)
        self.assertEqual(log.user_id, self.target)
        self.assertEqual(self._line(log, "name").new_value, "Key Uji Audit")
        self.assertEqual(self._line(log, "user_id").new_value, f"res.users,{self.target.id}")
        self.assertFalse(log.line_ids.filtered(lambda line: line.field_name in ("key", "index")))
        key_id = key.id
        key._remove()
        gone = self.Log.search(
            [("model_name", "=", "res.users.apikeys"), ("res_id", "=", key_id), ("operation", "=", "unlink")]
        )
        self.assertEqual(len(gone), 1)
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT count(*) FROM efn_audit_log_line WHERE concat(old_value, new_value) LIKE %s", [f"%{secret[:8]}%"]
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0, "nilai/indeks key tidak pernah disimpan")
        events, output = self._sink_events()
        ops = [e["audit_op"] for e in events if e["model"] == "res.users.apikeys"]
        self.assertEqual(ops, ["apikey_create", "apikey_revoke"])
        self.assertTrue(all(e["key_owner_uid"] == self.target.id for e in events if e["model"] == "res.users.apikeys"))
        self.assertNotIn(secret[:8], output)

    # --------------------------------------------------------- GC/erasure
    def test_access_logs_protected(self):
        self.target.write({"group_ids": [Command.link(self.group_personal.id)]})
        log = self._logs(self.target, "write")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_audit_log SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=4000), log.id],
        )
        log.invalidate_recordset(["create_date"])
        self.Log._gc_efn_audit_logs()
        self.assertTrue(log.exists(), "log hak akses tidak ikut GC")
        with self.assertRaises(UserError):
            self.Log._efn_audit_purge_records("res.users", self.target.ids)
