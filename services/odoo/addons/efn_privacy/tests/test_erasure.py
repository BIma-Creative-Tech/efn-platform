"""Cron erasure UU PDP dengan BFF di-mock (tidak ada HTTP sungguhan).

TestPoSCommon dipakai agar ada `pos.order` nyata (salinan kontak `email`/`mobile` stored).
Nomor & email di bawah fiktif khusus test.
"""

from unittest.mock import patch

from odoo.exceptions import UserError
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

    def _privacy_logs(self, request_id):
        return self.env["privacy.log"].sudo().search([("execution_details", "ilike", request_id)])

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
            self.assertTrue(partner.name.startswith("Pelanggan dihapus"))
            self.assertFalse(partner.phone)
            self.assertFalse(partner.email)
            self.assertFalse(partner.street)
            self.assertFalse(partner.city)
            self.assertFalse(partner.active)
            self.assertFalse(partner.efn_member_hash)
            self.assertEqual(partner.efn_erasure_request_id, REQ_1)
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
        log = self._privacy_logs(REQ_1)
        self.assertEqual(len(log), 1)
        self.assertNotIn(TEST_MEMBER_HASH, log.execution_details or "")
        self.assertNotEqual(log.anonymized_email, TEST_EMAIL)
        self.assertIn("@", PLACEHOLDER_EMAIL)

        # Log aplikasi tanpa nomor HP / email / hash.
        output = "\n".join(logs.output)
        for secret in ("81234567890", "3456", TEST_EMAIL, TEST_MEMBER_HASH, TEST_PEPPER_HEX):
            self.assertNotIn(secret, output)

    def test_rerun_is_idempotent(self):
        self._run_cron()
        self._run_cron()
        completes = self._completes()
        self.assertEqual(len(completes), 2)
        self.assertEqual(completes[1][2], {"store": "odoo", "records": 2}, "jumlah sama saat rerun")
        self.assertEqual(len(self._privacy_logs(REQ_1)), 1, "tidak ada privacy.log ganda")

    def test_unknown_hash_completes_with_zero(self):
        self.list_response = BffResponse(200, [{"request_id": REQ_2, "member_hash": OTHER_HASH}])
        self._run_cron()
        self.assertEqual(self._completes(), [("POST", f"{LIST}/{REQ_2}/complete", {"store": "odoo", "records": 0})])
        self.assertTrue(self.member.active)

    def test_auditlog_entries_removed_when_installed(self):
        if "auditlog.log" not in self.env:
            self.skipTest("OCA auditlog tidak terpasang")
        Log = self.env["auditlog.log"].sudo()
        vals = {
            "name": "uji",
            "model_id": self.env["ir.model"]._get_id("res.partner"),
            "res_id": self.member.id,
            "method": "write",
        }
        Log.create({k: v for k, v in vals.items() if k in Log._fields})
        self._run_cron()
        model_domain = (
            [("model_model", "=", "res.partner")]
            if "model_model" in Log._fields
            else [("model_id.model", "=", "res.partner")]
        )
        self.assertFalse(Log.search(model_domain + [("res_id", "in", [self.member.id, self.member_archived_b.id])]))

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
