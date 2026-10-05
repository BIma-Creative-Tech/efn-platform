"""E-15 efn_pos_payment_gateway — BFF di-mock (tidak ada HTTP sungguhan).

TestPoSCommon (sesi POS nyata + sync_from_ui) agar validasi `_process_payment_lines` teruji
seperti sync dari frontend. Nilai payment_id/QR di bawah fiktif khusus test.
"""

import uuid as uuidlib
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse, EfnBffError
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

PAYMENTS = "/v1/pos/payments"
QR = "00020101021226TESTONLYQRIS5802ID6304ABCD"


@tagged("post_install", "-at_install", "efn")
class TestEfnPosPaymentGateway(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        company = cls.config.company_id
        cls.env["res.company"].search([("efn_brand_code", "=", "FNB"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        company.write({"efn_brand_code": "FNB"})
        # DB uji memakai mata uang company TestPoSCommon (bukan IDR); kontrak produksi = IDR.
        cls.env["ir.config_parameter"].sudo().set_param("efn.pos.gateway_currency", company.currency_id.name)
        cls.gw_method = cls.env["pos.payment.method"].create(
            {
                "name": "QRIS EFN (uji)",
                "journal_id": cls.company_data["default_journal_bank"].id,
                "receivable_account_id": cls.pos_receivable_bank.id,
                "outstanding_account_id": cls.outstanding_bank.id,
                "payment_method_type": "terminal",
                "use_payment_terminal": "efn_gateway",
                "company_id": company.id,
            }
        )
        cls.config.write({"payment_method_ids": [Command.link(cls.gw_method.id)]})
        cls.product = cls.create_product("Produk Uji QRIS", cls.categ_basic, 100.0)

    def setUp(self):
        super().setUp()
        self.open_new_session()
        self.calls = []
        self.post_responses = {}
        self.get_responses = {}

    # ----------------------------------------------------------------- helpers
    def _fake_post(self, path, payload, log_context=None):
        self.calls.append(("POST", path, dict(payload)))
        result = self.post_responses[path]
        if isinstance(result, Exception):
            raise result
        return result

    def _fake_get(self, path, params=None, log_context=None):
        self.calls.append(("GET", path, dict(params or {})))
        result = self.get_responses[path]
        if isinstance(result, Exception):
            raise result
        return result

    def _patch_bff(self):
        client = self.env.registry["efn.bff.client"]
        post = patch.object(client, "post", side_effect=self._fake_post)
        get = patch.object(client, "get", side_effect=self._fake_get)
        post.start()
        get.start()
        self.addCleanup(post.stop)
        self.addCleanup(get.stop)

    def _created(self, payment_id, status="pending"):
        return BffResponse(
            201,
            {
                "payment_id": payment_id,
                "provider": "mock",
                "status": status,
                "qr_string": QR,
                "expires_at": "2099-01-01T00:00:00.000Z",
            },
        )

    def _remote(self, payment_id, status, amount=100):
        return BffResponse(
            200,
            {
                "payment_id": payment_id,
                "status": status,
                "paid_at": "2026-10-06T03:00:00Z" if status == "paid" else None,
                "provider_ref": "prov-ref-test",
                "amount": amount,
            },
        )

    def _create_intent(self, payment_id="pay-test-1", amount=100.0, order_uuid=None, pay_uuid=None):
        order_uuid = order_uuid or self.create_random_uid()
        pay_uuid = pay_uuid or str(uuidlib.uuid4())
        self.post_responses[PAYMENTS] = self._created(payment_id)
        res = self.gw_method.efn_gateway_create(order_uuid, pay_uuid, amount, self.config.id)
        self.assertTrue(res["ok"], res)
        return res, order_uuid, pay_uuid

    def _mark_paid(self, payment_id, amount=100):
        self.get_responses[f"{PAYMENTS}/{payment_id}"] = self._remote(payment_id, "paid", amount)

    def _order_data(self, order_uuid, tx=None, pay_uuid=None, amount=100.0):
        data = self.create_ui_order_data([(self.product, 1)], payments=[(self.gw_method, amount)], uuid=order_uuid)
        for command in data["payment_ids"]:
            vals = command[2]
            if vals["payment_method_id"] == self.gw_method.id:
                if tx:
                    vals["transaction_id"] = tx
                if pay_uuid and "uuid" in self.env["pos.payment"]._fields:
                    vals["uuid"] = pay_uuid
        return data

    def _sync(self, data):
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _intent(self, payment_id):
        return self.env["efn.pos.payment.intent"].sudo().search([("bff_payment_id", "=", payment_id)])

    # ---------------------------------------------------------------- create
    def test_create_sends_contract_payload_and_is_idempotent(self):
        self._patch_bff()
        res, order_uuid, pay_uuid = self._create_intent("pay-idem")
        again = self.gw_method.efn_gateway_create(order_uuid, pay_uuid, 100.0, self.config.id)
        self.assertEqual(again["intent_id"], res["intent_id"], "key sama -> satu intent")
        posts = [c for c in self.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1, "intent pending yang sama tidak memanggil BFF lagi")
        payload = posts[0][2]
        self.assertEqual(
            payload,
            {
                "order_ref": order_uuid,
                "amount": 100,
                "currency": self.config.company_id.currency_id.name,
                "company_code": "FNB",
                "pos_config_id": self.config.id,
                "method": "qris",
                "idempotency_key": f"pos:{order_uuid}:{pay_uuid}",
            },
        )
        self.assertEqual(res["status"], "pending")
        self.assertEqual(res["qr_string"], QR)
        self.assertNotIn("transaction_id", res)
        intent = self._intent("pay-idem")
        self.assertEqual(len(intent), 1)
        self.assertEqual(intent.company_id, self.config.company_id)
        self.assertEqual(intent.idempotency_key, f"pos:{order_uuid}:{pay_uuid}")

    def test_create_rejects_invalid_amount_and_bff_down(self):
        self._patch_bff()
        uid, puid = self.create_random_uid(), str(uuidlib.uuid4())
        res = self.gw_method.efn_gateway_create(uid, puid, 0, self.config.id)
        self.assertEqual(res["error_code"], "invalid_amount")
        res = self.gw_method.efn_gateway_create(uid, puid, 10**12, self.config.id)
        self.assertEqual(res["error_code"], "invalid_amount")
        self.post_responses[PAYMENTS] = EfnBffError("bff_tls_error")
        res = self.gw_method.efn_gateway_create(uid, puid, 100.0, self.config.id)
        self.assertEqual(res["error_code"], "bff_unavailable")
        self.assertFalse(self.env["efn.pos.payment.intent"].sudo().search([("pos_order_uuid", "=", uid)]))

    def test_non_pos_user_is_denied(self):
        self._patch_bff()
        user = new_test_user(self.env, login="efn_gw_nopos", groups="base.group_user")
        with self.assertRaises(AccessDenied):
            self.gw_method.with_user(user).efn_gateway_create(
                self.create_random_uid(), str(uuidlib.uuid4()), 100.0, self.config.id
            )
        self.assertFalse(self.calls)

    def test_method_not_in_config_is_denied(self):
        self._patch_bff()
        other = self.gw_method.copy({"name": "QRIS lain"})
        with self.assertRaises(AccessDenied):
            other.efn_gateway_create(self.create_random_uid(), str(uuidlib.uuid4()), 100.0, self.config.id)

    # ---------------------------------------------------------- status/cancel
    def test_status_paid_returns_transaction_and_clears_qr(self):
        self._patch_bff()
        res, _o, _p = self._create_intent("pay-status")
        self._mark_paid("pay-status")
        status = self.gw_method.efn_gateway_status(res["intent_id"])
        self.assertEqual(status["status"], "paid")
        self.assertEqual(status["transaction_id"], "pay-status")
        intent = self._intent("pay-status")
        self.assertFalse(intent.qr_string)
        self.assertEqual(intent.provider_ref, "prov-ref-test")
        self.assertTrue(intent.paid_at)

    def test_paid_with_different_amount_from_bff_is_failed(self):
        self._patch_bff()
        res, _o, _p = self._create_intent("pay-amt")
        self._mark_paid("pay-amt", amount=1)
        status = self.gw_method.efn_gateway_status(res["intent_id"])
        self.assertEqual(status["status"], "failed")
        self.assertEqual(self._intent("pay-amt").note, "amount_mismatch")

    def test_cancel_then_retry_uses_new_idempotency_key(self):
        self._patch_bff()
        res, order_uuid, pay_uuid = self._create_intent("pay-cancel")
        self.post_responses[f"{PAYMENTS}/pay-cancel/cancel"] = BffResponse(200, {"status": "cancelled"})
        cancel = self.gw_method.efn_gateway_cancel(res["intent_id"])
        self.assertTrue(cancel["ok"])
        self.assertEqual(self._intent("pay-cancel").status, "cancelled")
        self.post_responses[PAYMENTS] = self._created("pay-cancel-2")
        again = self.gw_method.efn_gateway_create(order_uuid, pay_uuid, 100.0, self.config.id)
        self.assertNotEqual(again["intent_id"], res["intent_id"])
        self.assertEqual(self.calls[-1][2]["idempotency_key"], f"pos:{order_uuid}:{pay_uuid}:2")

    def test_cancel_race_with_paid_keeps_paid(self):
        self._patch_bff()
        res, _o, _p = self._create_intent("pay-race")
        self.post_responses[f"{PAYMENTS}/pay-race/cancel"] = BffResponse(200, {"status": "paid"})
        self._mark_paid("pay-race")
        cancel = self.gw_method.efn_gateway_cancel(res["intent_id"])
        self.assertFalse(cancel["ok"])
        self.assertEqual(cancel["status"], "paid")

    # --------------------------------------------------------- sync validation
    def test_sync_with_paid_intent_succeeds_and_links_intent(self):
        self._patch_bff()
        _res, order_uuid, pay_uuid = self._create_intent("pay-sync")
        self._mark_paid("pay-sync")
        order = self._sync(self._order_data(order_uuid, tx="pay-sync", pay_uuid=pay_uuid))
        self.assertEqual(order.state, "paid")
        intent = self._intent("pay-sync")
        self.assertEqual(intent.status, "paid", "intent pending di-refresh saat sync")
        self.assertEqual(intent.pos_order_id, order)
        self.assertEqual(intent.pos_payment_id.pos_order_id, order)
        payment = order.payment_ids.filtered(lambda p: p.payment_method_id == self.gw_method)
        self.assertFalse(payment.card_no if "card_no" in payment._fields else False)

    def test_sync_with_pending_intent_is_rejected(self):
        self._patch_bff()
        _res, order_uuid, pay_uuid = self._create_intent("pay-pending")
        self.get_responses[f"{PAYMENTS}/pay-pending"] = self._remote("pay-pending", "pending")
        data = self._order_data(order_uuid, tx="pay-pending", pay_uuid=pay_uuid)
        with self.assertRaises(UserError):
            self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", order_uuid)]))

    def test_sync_with_pending_intent_and_bff_down_is_rejected(self):
        self._patch_bff()
        _res, order_uuid, pay_uuid = self._create_intent("pay-down")
        self.get_responses[f"{PAYMENTS}/pay-down"] = EfnBffError("bff_timeout")
        with self.assertRaises(UserError):
            self._sync(self._order_data(order_uuid, tx="pay-down", pay_uuid=pay_uuid))

    def test_sync_with_amount_mismatch_is_rejected(self):
        self._patch_bff()
        _res, order_uuid, pay_uuid = self._create_intent("pay-small", amount=60.0)
        self._mark_paid("pay-small", amount=60)
        data = self._order_data(order_uuid, tx="pay-small", pay_uuid=pay_uuid, amount=100.0)
        with self.assertRaises(UserError):
            self._sync(data)

    def test_sync_reusing_intent_on_other_order_is_rejected(self):
        self._patch_bff()
        _res, order_uuid, pay_uuid = self._create_intent("pay-reuse")
        self._mark_paid("pay-reuse")
        self._sync(self._order_data(order_uuid, tx="pay-reuse", pay_uuid=pay_uuid))
        other_uuid = self.create_random_uid()
        with self.assertRaises(UserError):
            self._sync(self._order_data(other_uuid, tx="pay-reuse", pay_uuid=pay_uuid))
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", other_uuid)]))

    def test_offline_payment_without_intent_is_rejected(self):
        """POS offline tidak bisa membuat intent (frontend menolak); payload yang tetap
        membawa metode efn_gateway tanpa intent/transaction ditolak server."""
        self._patch_bff()
        order_uuid = self.create_random_uid()
        with self.assertRaises(UserError):
            self._sync(self._order_data(order_uuid))
        with self.assertRaises(UserError):
            self._sync(self._order_data(order_uuid, tx="pay-forged"))
        self.assertFalse([c for c in self.calls if c[0] == "POST"])

    # ------------------------------------------------------- rekonsiliasi/ACL
    def test_reconcile_cron_expires_pending_and_flags_orphan(self):
        self._patch_bff()
        _res, _o, _p = self._create_intent("pay-stale")
        stale = self._intent("pay-stale")
        stale.write({"expires_at": fields.Datetime.now() - timedelta(hours=1)})
        self.get_responses[f"{PAYMENTS}/pay-stale"] = self._remote("pay-stale", "pending")
        self.post_responses[f"{PAYMENTS}/pay-stale/cancel"] = BffResponse(200, {"status": "cancelled"})

        _res, _o, _p = self._create_intent("pay-orphan")
        self._mark_paid("pay-orphan")
        orphan = self._intent("pay-orphan")
        orphan._efn_refresh_status()
        orphan.write({"paid_at": fields.Datetime.now() - timedelta(hours=2)})

        result = self.env["efn.pos.payment.intent"]._cron_efn_gateway_reconcile()
        self.assertEqual(result, {"expired": 1, "orphans": 1})
        self.assertEqual(stale.status, "cancelled")
        self.assertFalse(stale.qr_string)
        self.assertTrue(orphan.orphan_flagged)
        again = self.env["efn.pos.payment.intent"]._cron_efn_gateway_reconcile()
        self.assertEqual(again["orphans"], 0, "intent yatim hanya ditandai sekali")

    def test_multi_company_rule(self):
        self._patch_bff()
        self._create_intent("pay-mc")
        other_company = self.env["res.company"].create({"name": "EFN Gateway Uji Lain"})
        user = new_test_user(
            self.env,
            login="efn_gw_other_company",
            groups="point_of_sale.group_pos_user",
            company_id=other_company.id,
            company_ids=[Command.set(other_company.ids)],
        )
        visible = self.env["efn.pos.payment.intent"].with_user(user).search([("bff_payment_id", "=", "pay-mc")])
        self.assertFalse(visible)
        own = self.env["efn.pos.payment.intent"].search([("bff_payment_id", "=", "pay-mc")])
        self.assertTrue(own)

    def test_pos_user_cannot_write_intent(self):
        self._patch_bff()
        self._create_intent("pay-acl")
        user = new_test_user(
            self.env,
            login="efn_gw_cashier",
            groups="point_of_sale.group_pos_user",
            company_id=self.config.company_id.id,
            company_ids=[Command.set(self.config.company_id.ids)],
        )
        intent = self.env["efn.pos.payment.intent"].with_user(user).search([("bff_payment_id", "=", "pay-acl")])
        self.assertTrue(intent)
        with self.assertRaises(AccessError):
            intent.write({"status": "paid"})

    def test_constraint_requires_bank_journal(self):
        cash_journal = self.cash_pm1.journal_id
        with self.assertRaises(ValidationError):
            self.env["pos.payment.method"].create(
                {
                    "name": "QRIS salah jurnal",
                    "journal_id": cash_journal.id,
                    "payment_method_type": "terminal",
                    "use_payment_terminal": "efn_gateway",
                    "company_id": self.config.company_id.id,
                }
            )
