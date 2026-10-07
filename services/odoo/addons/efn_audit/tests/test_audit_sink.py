"""Gelombang 13: SEC-51 (integritas baris sink + purge manual) dan SEC-56 (celah masking)."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.efn_audit.models import base as audit_base
from odoo.addons.efn_audit.models.audit_policy import is_pii_field, mask_value
from odoo.addons.efn_audit.models.base import sink_canonical, sink_line, sink_mac

from .common import EfnAuditCase

SINK_LOGGER = "odoo.addons.efn_audit.sink"
TEST_KEY = b"k" * 32  # kunci uji (bukan rahasia)


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditSink(EfnAuditCase):
    def setUp(self):
        super().setUp()
        self.env.cr.postcommit.clear()

    def _run_sink(self):
        with self.assertLogs(SINK_LOGGER, "INFO") as captured:
            self.env.cr.postcommit.run()
        return [record.getMessage() for record in captured.records]

    # ------------------------------------------------------------- SEC-51
    def test_sink_line_signed_and_single_line(self):
        with patch.object(audit_base, "_sink_key", return_value=TEST_KEY):
            self.Log._efn_audit_write_purge_log(
                "efn.audit.log",
                'x\n2026-10-07 00:00:00,000 1 INFO db odoo.addons.efn_audit.sink: EFN_AUDIT_SINK {"operation":"purge"}',
            )
        messages = self._run_sink()
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertNotIn("\n", message, "newline di nilai di-escape, tidak membentuk baris sink kedua")
        self.assertTrue(message.startswith("EFN_AUDIT_SINK {"))
        # Teks palsu tetap terkurung di dalam string JSON `note` (kutip di-escape), bukan event kedua.
        self.assertIn('{\\"operation\\":\\"purge\\"}', message)
        event = json.loads(message.split(" ", 1)[1])
        self.assertTrue(event["note"].startswith("manual: x\n"))
        self.assertEqual(event["v"], 2)
        self.assertEqual(event["mac"], sink_mac(TEST_KEY, event))
        # Manipulasi isi -> mac tidak cocok.
        forged = dict(event, note="retensi: 1 log")
        self.assertNotEqual(sink_mac(TEST_KEY, forged), event["mac"])
        # Bentuk kanonik tidak bergantung urutan kunci / spasi.
        self.assertEqual(sink_canonical(event), sink_canonical(json.loads(json.dumps(event, indent=2))))

    def test_sink_without_key_is_explicit(self):
        line = sink_line({"v": 2, "log_id": 1}, key=None)
        self.assertIsNone(json.loads(line)["mac"])

    def test_manual_purge_flagged(self):
        """Log purge di luar jalur GC/erasure -> `manual:` + audit_op purge_manual (critical)."""
        log = self.Log._efn_audit_write_purge_log("res.partner", "erasure: 500 log")
        self.assertEqual(log.note, "manual: erasure: 500 log")
        events = [json.loads(m.split(" ", 1)[1]) for m in self._run_sink()]
        self.assertEqual((events[0]["audit_op"], events[0]["critical"]), ("purge_manual", True))

    def test_purge_logs_refuses_young_logs(self):
        self._set_rule("res.partner.category", tracked=("name",))
        category = self.env["res.partner.category"].create({"name": "Muda"})
        log = self._logs(category, "create")
        self.assertEqual(log._efn_audit_purge_logs("retensi: {count} log"), 0, "log 0 hari tidak boleh dihapus")
        self.assertTrue(log.exists())
        self.env["ir.config_parameter"].sudo().set_param("efn.audit.retention_days", "365")
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE efn_audit_log SET create_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(days=400), log.id],
        )
        log.invalidate_recordset(["create_date"])
        self.env.cr.postcommit.clear()
        self.Log._gc_efn_audit_logs()
        self.assertFalse(log.exists())
        purge = self.Log.search([("operation", "=", "purge"), ("model_name", "=", "efn.audit.log")], limit=1)
        self.assertTrue(purge.note.startswith("retensi:"), "jalur GC resmi tanpa awalan manual")
        events = [json.loads(m.split(" ", 1)[1]) for m in self._run_sink()]
        self.assertIn("purge", {e["audit_op"] for e in events})

    def test_erasure_purge_note_official(self):
        self._set_rule("res.partner", tracked=("name",), masked=())
        partner = self.env["res.partner"].create({"name": "Erasure Resmi"})
        partner.write({"name": "Erasure Resmi 2"})
        self.Log._efn_audit_purge_records("res.partner", partner.ids)
        purge = self.Log.search([("operation", "=", "purge"), ("model_name", "=", "res.partner")], limit=1)
        self.assertTrue(purge.note.startswith("erasure:"))

    # ------------------------------------------------------------- SEC-56
    def test_policy_address_birth_login(self):
        char = SimpleNamespace(type="char")
        date = SimpleNamespace(type="date")
        for fname in ("street", "street2", "zip", "private_street", "private_zip", "login", "x_address_line"):
            self.assertTrue(is_pii_field(fname, char), fname)
        for fname in ("birthday", "date_of_birth", "x_birthdate"):
            self.assertTrue(is_pii_field(fname, date), fname)
        self.assertFalse(is_pii_field("date_order", date))
        self.assertFalse(is_pii_field("city", char), "kota sengaja tidak di-mask (analitik wilayah)")
        self.assertEqual(mask_value("birthday", "1990-01-31"), "****-**-**")
        self.assertEqual(mask_value("street", "Jl. Melati 5"), "****(12)")
        self.assertEqual(mask_value("login", "budi@example.com"), "b***@example.com")
        self.assertEqual(mask_value("login", "081234567890"), "62812****890")
        self.assertEqual(mask_value("login", "kasir01"), "****(7)")

    def test_address_masked_and_person_relation_without_name(self):
        self._set_rule("res.partner", tracked=("name", "street", "street2", "zip"), masked=())
        company = self.env["res.partner"].create({"name": "PT Alamat", "is_company": True})
        company.write({"street": "Jl. Mawar Rahasia 9", "street2": "Blok Z", "zip": "40123"})
        log = self._logs(company, "write")
        for fname in ("street", "street2", "zip"):
            self.assertTrue(self._line(log, fname).is_masked, fname)
        # M2o ke model orang pada model non-orang (cron.user_id; model kontrol) -> `res.users,id`.
        user = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Sari Operator Cron",
                    "login": "sari.cron",
                    "group_ids": [Command.set([self.env.ref("base.group_user").id])],
                }
            )
        )
        cron = self.env["ir.cron"].create(
            {
                "name": "Cron Uji Relasi",
                "model_id": self.env["ir.model"]._get_id("res.partner"),
                "state": "code",
                "code": "pass",
                "interval_number": 1,
                "interval_type": "days",
            }
        )
        cron.write({"user_id": user.id})
        line = self._line(self._logs(cron, "write"), "user_id")
        self.assertEqual(line.new_value, f"res.users,{user.id}")
        self.assertEqual(line.new_ref, f"res.users,{user.id}")
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT count(*) FROM efn_audit_log_line WHERE concat(old_value, new_value) ~ %s",
            ["Mawar|Blok Z|40123|Sari"],
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0, "alamat/nama orang relasi tidak disimpan")
