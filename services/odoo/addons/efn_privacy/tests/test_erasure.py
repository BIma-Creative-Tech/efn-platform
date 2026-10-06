"""Cron erasure UU PDP dengan BFF di-mock (tidak ada HTTP sungguhan).

TestPoSCommon dipakai agar ada `pos.order` nyata (salinan kontak `email`/`mobile` stored).
Nomor & email di bawah fiktif khusus test.
"""

from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse, EfnBffError
from odoo.addons.efn_privacy.models.efn_privacy_erasure import PLACEHOLDER_EMAIL
from odoo.addons.efn_privacy.tests.test_member_hash import (
    TEST_MEMBER_HASH,
    TEST_PEPPER_HEX,
    PepperMixin,
)
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

LIST = "/v1/pos/privacy/erasures"
REQ_1 = "11111111-aaaa-4bbb-8ccc-000000000001"
REQ_2 = "22222222-aaaa-4bbb-8ccc-000000000002"
OTHER_HASH = "0" * 64
TEST_EMAIL = "member.uji@example.com"


@tagged("post_install", "-at_install", "efn")
class TestEfnPrivacyErasure(PepperMixin, TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.company_b = cls.env["res.company"].create({"name": "EFN Privacy Uji B"})

    def setUp(self):
        super().setUp()
        self._use_pepper()
        Partner = self.env["res.partner"]
        self.member = Partner.create(
            {
                "name": "Member Uji Hapus",
                "phone": "0812-3456-7890",
                "email": TEST_EMAIL,
                "street": "Jl. Uji 1",
                "city": "Kota Uji",
            }
        )
        # Duplikat di company lain dan sudah diarsipkan: harus ikut dianonimkan.
        self.member_archived_b = Partner.create(
            {
                "name": "Member Uji Arsip B",
                "phone": "+62 812 3456 7890",
                "company_id": self.company_b.id,
                "active": False,
            }
        )
        self.other = Partner.create({"name": "Pelanggan Lain", "phone": "0812-9999-0000"})
        self.assertEqual(self.member.efn_member_hash, TEST_MEMBER_HASH)
        self.assertEqual(self.member_archived_b.efn_member_hash, TEST_MEMBER_HASH)
        self.member.message_post(body="Catatan: HP 0812-3456-7890")
        self.open_new_session()
        data = self.create_ui_order_data(
            [(self.create_product("Produk Privasi", self.categ_basic, 10.0), 1)], customer=self.member
        )
        self.env["pos.order"].sync_from_ui([data])
        self.order = self.env["pos.order"].search([("uuid", "=", data["uuid"])])
        self.calls = []
        self.list_response = BffResponse(200, [{"request_id": REQ_1, "member_hash": TEST_MEMBER_HASH}])
        self.complete_response = BffResponse(200, {"ok": True})

    # ----------------------------------------------------------------- helpers
    def _fake_get(self, path, params=None, log_context=None):
        self.calls.append(("GET", path, dict(params or {})))
        if isinstance(self.list_response, Exception):
            raise self.list_response
        return self.list_response

    def _fake_post(self, path, payload, log_context=None):
        self.calls.append(("POST", path, dict(payload)))
        result = self.complete_response
        if callable(result):
            result = result(path)
        if isinstance(result, Exception):
            raise result
        return result

    def _run_cron(self):
        client = self.env.registry["efn.bff.client"]
        with (
            patch.object(client, "get", side_effect=self._fake_get),
            patch.object(client, "post", side_effect=self._fake_post),
        ):
            return self.env["efn.privacy.erasure"].with_context(efn_privacy_no_commit=True)._cron_pull_erasures()

    def _completes(self):
        return [c for c in self.calls if c[0] == "POST"]

    def _privacy_logs(self):
        return self.env["privacy.log"].sudo().search([("execution_details", "ilike", "EFN erasure")])

    def _pending(self, request_id=None):
        domain = [("request_id", "=", request_id)] if request_id else []
        return self.env["efn.privacy.erasure.pending"].sudo().search(domain)

    def _tables_containing(self, needle):
        """Tabel (dari daftar) yang masih memuat `needle` di kolom mana pun (SEC-31)."""
        self.env.flush_all()
        found = []
        for table in ("res_partner", "privacy_log", "efn_privacy_erasure_pending", "mail_message", "pos_order"):
            self.env.cr.execute(
                f"SELECT count(*) FROM {table} t WHERE row_to_json(t)::text LIKE %s",  # noqa: S608 - nama tabel konstan
                (f"%{needle}%",),
            )
            if self.env.cr.fetchone()[0]:
                found.append(table)
        return found

    # ------------------------------------------------------------------- tests
    def test_erasure_anonymizes_partner_archived_cross_company_and_copies(self):
        self.assertTrue(self.order.email)
        with self.assertLogs("odoo.addons.efn_privacy", level="INFO") as logs:
            summary = self._run_cron()
        self.assertEqual(summary["done"], 1)
        self.assertEqual(self.calls[0], ("GET", LIST, {"store": "odoo"}))
        self.assertEqual(
            self._completes(),
            [("POST", f"{LIST}/{REQ_1}/complete", {"store": "odoo", "records": 2})],
        )

        partners = (self.member | self.member_archived_b).with_context(active_test=False)
        partners.invalidate_recordset()
        for partner in partners:
            self.assertEqual(partner.name, "Pelanggan dihapus", "SEC-31: tanpa potongan request_id")
            self.assertFalse(partner.phone)
            self.assertFalse(partner.email)
            self.assertFalse(partner.street)
            self.assertFalse(partner.city)
            self.assertFalse(partner.active)
            self.assertFalse(partner.efn_member_hash)
            self.assertTrue(partner.efn_erased)
        self.assertEqual(self.other.name, "Pelanggan Lain")
        self.assertTrue(self.other.phone)

        # pos.order dipertahankan, salinan kontak dikosongkan.
        self.order.invalidate_recordset()
        self.assertTrue(self.order.exists())
        self.assertEqual(self.order.partner_id, self.member)
        self.assertFalse(self.order.email)
        self.assertFalse(self.order.mobile)

        # Chatter partner terhapus.
        messages = (
            self.env["mail.message"].sudo().search([("model", "=", "res.partner"), ("res_id", "in", partners.ids)])
        )
        self.assertFalse(messages)

        # Jejak privacy.log standar, placeholder (tanpa HP/hash).
        log = self._privacy_logs()
        self.assertEqual(len(log), 1)
        self.assertNotIn(TEST_MEMBER_HASH, log.execution_details or "")
        self.assertNotIn(REQ_1, log.execution_details or "", "SEC-31: privacy.log tanpa request_id")
        self.assertNotEqual(log.anonymized_email, TEST_EMAIL)
        self.assertIn("@", PLACEHOLDER_EMAIL)

        # SEC-31: setelah complete diterima, tidak ada request_id / member_hash yang tersisa.
        self.assertFalse(self._pending(), "penanda sementara dihapus setelah complete sukses")
        self.assertEqual(self._tables_containing(REQ_1), [])
        self.assertEqual(self._tables_containing(REQ_1[:8]), [])
        self.assertEqual(self._tables_containing(TEST_MEMBER_HASH), [])

        # Log aplikasi tanpa nomor HP / email / hash.
        output = "\n".join(logs.output)
        for secret in ("81234567890", "3456", TEST_EMAIL, TEST_MEMBER_HASH, TEST_PEPPER_HEX):
            self.assertNotIn(secret, output)

    def test_complete_retry_is_idempotent_without_permanent_link(self):
        # Run 1: anonimisasi sukses, complete gagal -> penanda sementara menyimpan jumlah.
        self.complete_response = EfnBffError("bff_unreachable")
        summary = self._run_cron()
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(self._pending(REQ_1).records, 2)
        self.assertEqual(self._tables_containing(REQ_1), ["efn_privacy_erasure_pending"])
        # Run 2: partner sudah anonim (hash kosong) tetapi jumlah tetap 2; penanda lalu dihapus.
        self.complete_response = BffResponse(200, {"ok": True})
        summary = self._run_cron()
        self.assertEqual(summary["done"], 1)
        completes = self._completes()
        self.assertEqual(len(completes), 2)
        self.assertEqual(completes[0][2], {"store": "odoo", "records": 2})
        self.assertEqual(completes[1][2], {"store": "odoo", "records": 2}, "jumlah sama saat retry")
        self.assertEqual(len(self._privacy_logs()), 1, "tidak ada privacy.log ganda")
        self.assertFalse(self._pending())
        self.assertEqual(self._tables_containing(REQ_1), [])

    def test_rejected_complete_keeps_marker(self):
        self.complete_response = BffResponse(409, {"ok": False})
        self._run_cron()
        self.assertEqual(self._pending(REQ_1).records, 2)

    def test_stale_marker_vacuumed(self):
        Pending = self.env["efn.privacy.erasure.pending"].sudo()
        old = Pending.create({"request_id": REQ_2, "records": 1})
        fresh = Pending.create({"request_id": REQ_1, "records": 1})
        self.env.cr.execute(
            "UPDATE efn_privacy_erasure_pending SET create_date = now() - interval '31 days' WHERE id = %s",
            (old.id,),
        )
        Pending.invalidate_model(["create_date"])
        Pending._gc_efn_stale_markers()
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_pending_marker_admin_read_only(self):
        Pending = self.env["efn.privacy.erasure.pending"]
        self.assertFalse(Pending._fields.get("partner_id"), "penanda tidak boleh menautkan partner")
        user = self.env["res.users"].create(
            {
                "name": "Kasir Uji Privasi",
                "login": "kasir.uji.privasi",
                "group_ids": [(6, 0, [self.env.ref("point_of_sale.group_pos_user").id])],
            }
        )
        with self.assertRaises(AccessError):
            Pending.with_user(user).search([])

    def test_unknown_hash_completes_with_zero(self):
        self.list_response = BffResponse(200, [{"request_id": REQ_2, "member_hash": OTHER_HASH}])
        self._run_cron()
        self.assertEqual(self._completes(), [("POST", f"{LIST}/{REQ_2}/complete", {"store": "odoo", "records": 0})])
        self.assertTrue(self.member.active)

    def test_audit_logs_purged(self):
        """Log efn_audit partner (HP lama ter-mask, nama) dihapus saat erasure; log purge tanpa PII."""
        Rule = self.env["efn.audit.rule"].sudo().with_context(active_test=False)
        fields_ = [self.env["ir.model.fields"]._get("res.partner", f).id for f in ("name", "phone", "email")]
        masked = fields_[1:]
        rule = Rule.search([("model_name", "=", "res.partner")], limit=1)
        vals = {
            "active": True,
            "log_write": True,
            "tracked_field_ids": [(6, 0, fields_)],
            "masked_field_ids": [(6, 0, masked)],
        }
        if rule:
            rule.write(vals)
        else:
            Rule.create(dict(vals, name="Uji privasi", model_id=self.env["ir.model"]._get_id("res.partner")))
        self.addCleanup(self.env.registry.clear_cache)
        self.member.write({"name": "Member Uji Hapus 2"})
        self.other.write({"name": "Pelanggan Lain 2"})
        Log = self.env["efn.audit.log"].sudo()
        ids = [self.member.id, self.member_archived_b.id]
        self.assertTrue(Log.search([("model_name", "=", "res.partner"), ("res_id", "in", ids)]))
        self._run_cron()
        self.assertFalse(Log.search([("model_name", "=", "res.partner"), ("res_id", "in", ids)]))
        self.assertTrue(Log.search([("model_name", "=", "res.partner"), ("res_id", "=", self.other.id)]))
        purge = Log.search([("operation", "=", "purge"), ("model_name", "=", "res.partner")], limit=1)
        self.assertTrue(purge)
        self.assertFalse(purge.res_id or purge.res_name)
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT count(*) FROM efn_audit_log_line l JOIN efn_audit_log g ON g.id = l.log_id"
            " WHERE g.res_id = ANY(%s) AND g.model_name = 'res.partner'",
            [ids],
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0)

    def test_failure_of_one_request_does_not_stop_others(self):
        self.list_response = BffResponse(
            200,
            [
                {"request_id": "bad id!", "member_hash": TEST_MEMBER_HASH},
                {"request_id": REQ_2, "member_hash": OTHER_HASH},
                {"request_id": REQ_1, "member_hash": TEST_MEMBER_HASH},
            ],
        )
        self.complete_response = lambda path: EfnBffError("bff_timeout") if REQ_2 in path else BffResponse(200, {})
        summary = self._run_cron()
        self.assertEqual(summary, {"done": 1, "failed": 1, "skipped": 1})
        self.assertFalse(self.member.with_context(active_test=False).phone)

    def test_items_wrapper_object_is_accepted(self):
        self.list_response = BffResponse(200, {"items": [{"request_id": REQ_1, "member_hash": TEST_MEMBER_HASH}]})
        summary = self._run_cron()
        self.assertEqual(summary["done"], 1)

    def test_missing_pepper_fails_hard_without_calling_bff(self):
        self._no_pepper()
        with self.assertLogs("odoo.addons.efn_privacy", level="ERROR"):
            with self.assertRaises(UserError):
                self._run_cron()
        self.assertFalse(self.calls)
        self.assertTrue(self.member.active)

    def test_bff_unreachable_fails_cron(self):
        self.list_response = EfnBffError("bff_unreachable")
        with self.assertLogs("odoo.addons.efn_privacy", level="ERROR"):
            with self.assertRaises(UserError):
                self._run_cron()
        self.assertFalse(self._completes())
