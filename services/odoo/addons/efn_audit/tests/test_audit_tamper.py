"""SEC-43 (review r4): ketahanan log audit terhadap pemusnahan lewat retensi/aturan/server action.

- retensi minimum hard-coded (parameter & aturan), nilai kecil ditolak dengan pesan jelas;
- log `purge` dan log model kontrol tidak ikut GC;
- model kontrol (parameter efn.audit.*, server action, cron, modul) selalu diaudit walau
  aturannya diarsipkan/dihapus; aturan admin tidak bisa mengurangi spesifikasi bawaan;
- aturan bawaan (ber-xmlid) tidak bisa dihapus, hanya diarsipkan (tercatat);
- jalur erasure tidak bisa dipakai untuk log kontrol;
- ringkasan log kontrol dikirim ke logger sink setelah commit (tanpa nilai).
"""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.efn_audit.models.audit_policy import MIN_RETENTION_DAYS, PROTECTED_LOG_MODELS

from .common import EfnAuditCase

SINK_LOGGER = "odoo.addons.efn_audit.sink"


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditTamper(EfnAuditCase):
    def setUp(self):
        super().setUp()
        self.ICP = self.env["ir.config_parameter"].sudo()
        # Matikan aturan admin untuk parameter (bila efn_setup_audit terpasang): bawaan tetap jalan.
        self.Rule.search([("model_name", "=", "ir.config_parameter")]).write({"active": False})

    def _age(self, logs, days):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_audit_log SET create_date = %s WHERE id = ANY(%s)",
            [fields.Datetime.now() - timedelta(days=days), logs.ids],
        )
        logs.invalidate_recordset(["create_date"])

    def _param_logs(self, key):
        param = self.ICP.search([("key", "=", key)])
        return self.Log.search([("model_name", "=", "ir.config_parameter"), ("res_id", "=", param.id)])

    # --------------------------------------------------------- retensi min
    def test_retention_param_minimum_enforced(self):
        self.assertEqual(MIN_RETENTION_DAYS, 365)
        for bad in ("1", "30", "364", "-1", "abc", "1.5"):
            with self.assertRaises(ValidationError, msg=bad) as ctx:
                self.ICP.set_param("efn.audit.retention_days", bad)
            self.assertIn("Retensi log audit", str(ctx.exception))
        for good in ("0", "365", "730", "3650"):
            self.ICP.set_param("efn.audit.retention_days", good)
            self.assertEqual(self.ICP.get_param("efn.audit.retention_days"), good)
        # Parameter lain tidak terdampak.
        self.ICP.set_param("efn.audit.other_flag", "1")

    def test_rule_retention_minimum_enforced(self):
        rule = self._set_rule("res.partner.category", tracked=("name",))
        with self.assertRaises(ValidationError) as ctx:
            rule.write({"retention_days": 30})
        self.assertIn(str(MIN_RETENTION_DAYS), str(ctx.exception))
        rule.write({"retention_days": MIN_RETENTION_DAYS})
        rule.write({"retention_days": 0})

    def test_gc_never_below_minimum(self):
        """Nilai kecil yang lolos lewat SQL tetap diperlakukan sebagai minimum oleh GC."""
        rule = self._set_rule("res.partner.category", tracked=("name",))
        category = self.env["res.partner.category"].create({"name": "GC Min"})
        category.write({"name": "GC Min 2"})
        log = self._logs(category, "write")
        self.env.flush_all()
        self.env.cr.execute("UPDATE efn_audit_rule SET retention_days = 1 WHERE id = %s", [rule.id])
        rule.invalidate_recordset(["retention_days"])
        self._age(log, 30)
        self.Log._gc_efn_audit_logs()
        self.assertTrue(log.exists(), "log 30 hari tidak boleh terhapus walau retensi aturan = 1 (SQL)")
        self._age(log, MIN_RETENTION_DAYS + 5)
        self.Log._gc_efn_audit_logs()
        self.assertFalse(log.exists())

    # ------------------------------------------------------ GC terlindungi
    def test_purge_and_control_logs_survive_gc(self):
        self.ICP.set_param("efn.audit.retention_days", "365")
        param_logs = self._param_logs("efn.audit.retention_days")
        self.assertTrue(param_logs, "perubahan parameter retensi selalu tercatat")
        rule = self._set_rule("res.partner.category", tracked=("name",))
        rule_logs = self.Log.search([("model_name", "=", "efn.audit.rule"), ("res_id", "=", rule.id)])
        self.assertTrue(rule_logs)
        category = self.env["res.partner.category"].create({"name": "GC Biasa"})
        plain = self._logs(category, "create")
        old_purge = self.Log._efn_audit_write_purge_log("efn.audit.log", "retensi: 1 log")
        self._age(param_logs | rule_logs | plain | old_purge, 4000)
        self.Log._gc_efn_audit_logs()
        self.assertFalse(plain.exists(), "log biasa melewati retensi -> terhapus")
        self.assertTrue(param_logs.exists(), "log parameter audit tidak ikut GC")
        self.assertTrue(rule_logs.exists(), "log aturan audit tidak ikut GC")
        self.assertTrue(old_purge.exists(), "log purge tidak ikut GC")
        # Purge langsung atas log terlindungi juga diabaikan (jalur server action).
        self.assertEqual((param_logs | rule_logs | old_purge)._efn_audit_purge_logs("x {count}"), 0)
        self.assertTrue((param_logs | rule_logs | old_purge).exists())

    def test_purge_records_refuses_control_models(self):
        for model_name in sorted(PROTECTED_LOG_MODELS):
            with self.assertRaises(UserError):
                self.Log._efn_audit_purge_records(model_name, [1])

    # ------------------------------------------------- model kontrol diaudit
    def test_control_models_audited_without_rule(self):
        self.assertFalse(self.Rule.search([("model_name", "in", ["ir.actions.server", "ir.cron"])]))
        self.ICP.set_param("efn.audit.retention_days", "400")
        self.ICP.set_param("efn.audit.retention_days", "500")
        write = self._param_logs("efn.audit.retention_days").filtered(lambda log: log.operation == "write")[:1]
        self.assertEqual((self._line(write, "value").old_value, self._line(write, "value").new_value), ("400", "500"))
        # Parameter non-audit tanpa aturan admin: tidak dicatat (filter bawaan hanya efn.audit.*).
        self.ICP.set_param("efn.pos.audit_test_flag", "1")
        self.assertFalse(self._param_logs("efn.pos.audit_test_flag"))

        action = self.env["ir.actions.server"].create(
            {
                "name": "Aksi Uji Audit",
                "model_id": self.env["ir.model"]._get_id("res.partner"),
                "state": "code",
                "code": "env['efn.audit.log'].search([])._efn_audit_purge_logs('x')",
            }
        )
        log = self._logs(action, "create")
        self.assertEqual(len(log), 1)
        self.assertIn("_efn_audit_purge_logs", self._line(log, "code").new_value)
        action.write({"code": "pass"})
        self.assertEqual(self._line(self._logs(action, "write"), "code").new_value, "pass")

        cron = self.env["ir.cron"].create(
            {
                "name": "Cron Uji Audit",
                "model_id": self.env["ir.model"]._get_id("res.partner"),
                "state": "code",
                "code": "pass",
                "interval_number": 1,
                "interval_type": "days",
            }
        )
        cron.write({"interval_number": 2})
        cron_log = self._logs(cron, "write")
        self.assertEqual(self._line(cron_log, "interval_number").new_value, "2")
        # Field yang berubah setiap jalan cron tidak dilacak (tanpa log, tanpa read).
        cron.write({"lastcall": fields.Datetime.now()})
        self.assertEqual(len(self._logs(cron, "write")), 1)

    def test_admin_rule_cannot_reduce_builtin(self):
        """Aturan admin pada model kontrol hanya menambah: filter OR, field gabungan, flag paksa."""
        self._set_rule(
            "ir.config_parameter",
            tracked=("key",),
            log_write=False,
            filter_domain="[('key', '=', 'efn.pos.audit_admin_key')]",
        )
        self.ICP.set_param("efn.audit.retention_days", "400")
        self.ICP.set_param("efn.audit.retention_days", "450")
        write = self._param_logs("efn.audit.retention_days").filtered(lambda log: log.operation == "write")
        self.assertTrue(write, "log_write=False di aturan admin tidak mematikan audit bawaan")
        self.assertEqual(self._line(write[:1], "value").new_value, "450")
        self.ICP.set_param("efn.pos.audit_admin_key", "1")
        self.assertTrue(self._param_logs("efn.pos.audit_admin_key"), "filter admin tetap berlaku (OR)")

    # ---------------------------------------------------- aturan bawaan
    def test_builtin_rule_archive_only(self):
        rule = self._set_rule("res.partner.category", tracked=("name",))
        self.env["ir.model.data"].create(
            {"module": "efn_audit_test", "name": "rule_builtin_uji", "model": "efn.audit.rule", "res_id": rule.id}
        )
        with self.assertRaises(UserError):
            rule.unlink()
        rule.write({"active": False})
        archived = self.Log.search(
            [("model_name", "=", "efn.audit.rule"), ("res_id", "=", rule.id), ("operation", "=", "write")]
        ).filtered(lambda log: self._line(log, "active"))
        self.assertTrue(archived, "penonaktifan aturan tercatat")
        self.assertEqual(self._line(archived[:1], "active").new_value, "False")
        # Aturan tanpa xmlid (buatan admin) boleh dihapus — tercatat dengan snapshot.
        custom = self._set_rule("res.country", tracked=("name",))
        custom_id = custom.id
        custom.unlink()
        gone = self.Log.search(
            [("model_name", "=", "efn.audit.rule"), ("res_id", "=", custom_id), ("operation", "=", "unlink")]
        )
        self.assertEqual(len(gone), 1)
        self.assertEqual(self._line(gone, "name").old_value, "Uji res.country")

    # ------------------------------------------------------------- sink
    def test_sink_emitted_after_commit_without_values(self):
        self.env.cr.postcommit.clear()
        self.ICP.set_param("efn.audit.retention_days", "400")
        with self.assertLogs(SINK_LOGGER, "INFO") as captured:
            self.env.cr.postcommit.run()
        output = "\n".join(captured.output)
        self.assertIn("EFN_AUDIT_SINK", output)
        self.assertIn('"model": "ir.config_parameter"', output)
        self.assertIn('"value"', output, "nama field dikirim")
        self.assertNotIn('"400"', output, "nilai tidak dikirim ke sink")
        # Log biasa (bukan kontrol) tidak dikirim ke sink.
        self._set_rule("res.partner.category", tracked=("name",))
        self.env.cr.postcommit.clear()
        self.env["res.partner.category"].create({"name": "Bukan Kontrol"})
        with self.assertNoLogs(SINK_LOGGER, "INFO"):
            self.env.cr.postcommit.run()
