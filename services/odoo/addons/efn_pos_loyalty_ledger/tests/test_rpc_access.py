"""SEC-58 (r6, High): RPC nyata lewat ``/web/dataset/call_kw`` sebagai user biasa & kasir.

- ``efn.bff.client`` (proxy service key BFF) tidak dapat dipanggil siapa pun lewat RPC: tidak
  ada request ke BFF (mock ``_efn_post``/``_efn_get`` tidak terpanggil).
- Metode publik POS (``pos.config.efn_*``, ``pos.payment.method.efn_gateway_*``,
  ``efn.pos.loyalty.outbox.action_efn_hold_*``) menolak user tanpa grup/sesi/company yang benar.
- Jalur sah tetap jalan: kasir dengan sesi terbuka memanggil ``efn_loyalty_balance`` via RPC.
- Review r11: kasir (dengan Contact Creation) tidak bisa mengubah HP member / penanda member (SEC-115) dan tidak
  bisa membuat pembayaran Poin EFN di luar sync (SEC-113) lewat ``call_kw``.

HttpCase tanpa browser (``url_open``). Tag ``efn_tour`` (job CI ``odoo-tour``, DB tanpa
``efn_setup_*``): fixture ``TestPointOfSaleHttpCommon`` gagal di DB job unit karena record rule
multi-company 4 PT ``efn_setup_company`` (accountman tidak bisa membaca pajak), sama dengan tour.
Server test berjalan di proses yang sama sehingga patch pada kelas registry berlaku untuk request.
"""

import json
import uuid as uuid_lib
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_core.models.efn_bff_client import BffResponse
from odoo.addons.efn_pos_loyalty_ledger.models import pos_config as loyalty_pos_config
from odoo.addons.efn_pos_loyalty_ledger.tests.test_loyalty_tour import create_points_method
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

BALANCE = "/v1/pos/loyalty/balance"
PARAMS = "/v1/pos/loyalty/params"
EARN = "/v1/pos/loyalty/earn"


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnRpcAccess(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        company = cls.main_pos_config.company_id
        env["res.company"].search([("efn_brand_code", "=", "RTL"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        env["res.company"].flush_model(["efn_brand_code"])
        company.write({"efn_brand_code": "RTL"})
        cls.main_pos_config.write({"efn_loyalty_earn_enabled": True, "efn_loyalty_redeem_enabled": True})
        cls.member = env["res.partner"].create({"name": "Member RPC Uji", "phone": "0812-3456-7890"})
        cls.points_pm = create_points_method(env, company)
        cls.main_pos_config.write({"payment_method_ids": [Command.link(cls.points_pm.id)]})
        cls.plain = new_test_user(
            env, login="efn_rpc_plain_pos", password="efn_rpc_plain_pos", groups="base.group_user"
        )
        cls.other_company = env["res.company"].create({"name": "PT Lain Uji RPC"})
        cls.other_cashier = new_test_user(
            env,
            login="efn_rpc_other_cashier",
            password="efn_rpc_other_cashier",
            groups="point_of_sale.group_pos_user",
            company_id=cls.other_company.id,
            company_ids=[Command.set(cls.other_company.ids)],
        )
        cls.held = (
            env["efn.pos.loyalty.outbox"]
            .sudo()
            .create(
                {
                    "company_id": company.id,
                    "kind": "redeem_release",
                    "idempotency_key": "pos_release:rpc-test",
                    "state": "hold",
                }
            )
        )

    def setUp(self):
        super().setUp()
        self.calls = []
        loyalty_pos_config._BALANCE_CALLS.clear()
        loyalty_pos_config._PARAMS_CACHE.clear()
        client_cls = self.env.registry["efn.bff.client"]
        for name, fake in (("_efn_post", self._fake_post), ("_efn_get", self._fake_get)):
            patcher = patch.object(client_cls, name, autospec=True, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _fake_get(self, _client, path, params=None, log_context=None):
        self.calls.append(path)
        if path == PARAMS:
            return BffResponse(200, {})
        raise AssertionError(f"path BFF tak terduga: {path}")

    def _fake_post(self, _client, path, payload, log_context=None):
        self.calls.append(path)
        if path == BALANCE:
            return BffResponse(200, {"points_balance": 42})
        raise AssertionError(f"path BFF tak terduga: {path}")

    def _call_kw(self, model, method, args):
        with mute_logger("odoo.http"):
            response = self.url_open(
                "/web/dataset/call_kw",
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "call",
                        "id": 1,
                        "params": {"model": model, "method": method, "args": args, "kwargs": {}},
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def assertDenied(self, payload, msg=None):
        self.assertIn("error", payload, msg)
        self.assertNotIn("result", payload, msg)

    def _open_session(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        session = self.main_pos_config.current_session_id
        if session.state == "opening_control":
            session.set_opening_control(0, "")
        self.assertEqual(self.main_pos_config.current_session_id.state, "opened")

    def test_bff_client_denied_for_cashier_and_plain_user(self):
        self._open_session()
        earn = {"idempotency_key": "pos_earn:x", "order_ref": "x", "member_ref": "6281200000000", "eligible_amount": 1}
        for login in ("pos_user", "efn_rpc_plain_pos"):
            self.authenticate(login, login)
            for method, args in (
                ("post", [EARN, earn]),
                ("get", [PARAMS]),
                ("_efn_post", [EARN, earn]),
                ("_efn_get", [PARAMS]),
                ("_efn_request", ["POST", EARN, earn]),
            ):
                with self.subTest(login=login, method=method):
                    self.assertDenied(self._call_kw("efn.bff.client", method, args), method)
        self.assertFalse(self.calls, "tidak boleh ada panggilan BFF")

    def test_pos_rpc_denied_for_plain_user(self):
        self._open_session()
        self.authenticate("efn_rpc_plain_pos", "efn_rpc_plain_pos")
        config_id = self.main_pos_config.id
        method_id = self.main_pos_config.payment_method_ids[:1].id
        for model, method, args in (
            ("pos.config", "efn_loyalty_balance", [[config_id], self.member.id]),
            ("pos.config", "efn_otp_request", [[config_id], "a" * 16, 1000, self.member.id]),
            ("pos.config", "efn_loyalty_redeem_release", [[config_id], "a" * 16]),
            ("pos.payment.method", "efn_gateway_status", [[method_id], 1]),
            ("efn.pos.loyalty.outbox", "action_efn_hold_approve", [[self.held.id]]),
        ):
            with self.subTest(method=method):
                self.assertDenied(self._call_kw(model, method, args), method)
        self.assertFalse(self.calls)
        self.assertEqual(self.held.state, "hold")

    def test_cashier_other_company_denied(self):
        self._open_session()
        self.authenticate("efn_rpc_other_cashier", "efn_rpc_other_cashier")
        payload = self._call_kw("pos.config", "efn_loyalty_balance", [[self.main_pos_config.id], self.member.id])
        self.assertDenied(payload)
        self.assertFalse(self.calls)

    def test_cashier_cannot_decide_held_outbox(self):
        self.authenticate("pos_user", "pos_user")
        for method in ("action_efn_hold_approve", "action_efn_hold_cancel"):
            self.assertDenied(self._call_kw("efn.pos.loyalty.outbox", method, [[self.held.id]]), method)
        self.assertEqual(self.held.state, "hold")

    def test_cashier_without_open_session_denied(self):
        self.authenticate("pos_user", "pos_user")
        payload = self._call_kw("pos.config", "efn_loyalty_balance", [[self.main_pos_config.id], self.member.id])
        self.assertDenied(payload)
        self.assertFalse(self.calls)

    def test_cashier_legit_balance_still_works(self):
        self._open_session()
        self.authenticate("pos_user", "pos_user")
        payload = self._call_kw("pos.config", "efn_loyalty_balance", [[self.main_pos_config.id], self.member.id])
        self.assertIn("result", payload, payload.get("error"))
        self.assertTrue(payload["result"]["ok"], payload["result"])
        self.assertEqual(payload["result"]["points_balance"], 42)
        self.assertIn(BALANCE, self.calls)

    # ------------------------------------------------------------ review r11
    def test_r11_cashier_rpc_cannot_change_member_phone_or_flag(self):
        """SEC-115: kasir pemegang Contact Creation (SEC-121) tetap tidak bisa mengubah HP member via RPC."""
        self.pos_user.write({"group_ids": [Command.link(self.env.ref("base.group_partner_manager").id)]})
        self.member._efn_mark_member()
        stranger = self.env["res.partner"].create({"name": "Bukan Member RPC", "phone": "0812-1212-3434"})
        self.authenticate("pos_user", "pos_user")
        self.assertDenied(self._call_kw("res.partner", "write", [[self.member.id], {"phone": "0813-0000-1111"}]))
        self.assertDenied(self._call_kw("res.partner", "write", [[self.member.id], {"efn_member_locked_at": False}]))
        self.assertDenied(
            self._call_kw("res.partner", "write", [[stranger.id], {"efn_member_locked_at": "2026-01-01 00:00:00"}])
        )
        ok = self._call_kw("res.partner", "write", [[stranger.id], {"phone": "0812-5656-7878"}])
        self.assertIn("result", ok, ok.get("error"))
        self.member.invalidate_recordset()
        self.assertEqual(self.member.phone, "0812-3456-7890")
        self.assertTrue(self.member.efn_member_locked_at)
        self.assertFalse(stranger.efn_member_locked_at)

    def test_r11_cashier_rpc_cannot_create_points_payment(self):
        """SEC-113: kasir tidak bisa "membayar dengan poin" order draft lewat call_kw (tanpa OTP + reservasi)."""
        self._open_session()
        session = self.main_pos_config.current_session_id
        product = self.env["product.product"].create(
            {"name": "Produk RPC Poin", "list_price": 100.0, "available_in_pos": True, "taxes_id": [Command.clear()]}
        )
        order_uuid = str(uuid_lib.uuid4())
        self.env["pos.order"].with_user(self.pos_user).sync_from_ui(
            [
                {
                    "uuid": order_uuid,
                    "name": "Order draft RPC",
                    "session_id": session.id,
                    "company_id": self.main_pos_config.company_id.id,
                    "pricelist_id": self.main_pos_config.pricelist_id.id,
                    "partner_id": self.member.id,
                    "user_id": self.pos_user.id,
                    "date_order": fields.Datetime.to_string(fields.Datetime.now()),
                    "amount_total": 100.0,
                    "amount_tax": 0.0,
                    "amount_paid": 0.0,
                    "amount_return": 0.0,
                    "state": "draft",
                    "lines": [
                        (
                            0,
                            0,
                            {
                                "product_id": product.id,
                                "qty": 1.0,
                                "price_unit": 100.0,
                                "price_subtotal": 100.0,
                                "price_subtotal_incl": 100.0,
                                "tax_ids": [(6, 0, [])],
                                "discount": 0.0,
                            },
                        )
                    ],
                    "payment_ids": [],
                }
            ]
        )
        order = self.env["pos.order"].search([("uuid", "=", order_uuid)])
        self.assertEqual(order.state, "draft")
        self.authenticate("pos_user", "pos_user")
        vals = {"pos_order_id": order.id, "payment_method_id": self.points_pm.id, "amount": 100.0}
        self.assertDenied(self._call_kw("pos.payment", "create", [vals]))
        self.assertFalse(order.payment_ids)
        self.assertFalse(self.calls)
