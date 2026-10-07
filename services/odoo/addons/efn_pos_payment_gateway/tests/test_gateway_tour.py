"""Test UI POS (OWL) nyata untuk metode terminal `efn_gateway` (QRIS) — Gelombang 12.

`HttpCase` + tour dengan Chrome headless (tag `efn_tour`, image target `test`; lihat
efn_pos_otp_gate/tests/test_pos_otp_tour.py). BFF di-mock server-side (`efn.bff.client`
post/get di-patch pada kelas registry -> berlaku untuk request browser).
"""

from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

PAYMENTS = "/v1/pos/payments"
PAYMENT_ID = "pay_tour_001"  # fiktif
QR = "00020101021226TESTONLYQRIS5802ID6304ABCD"  # fiktif, bukan QRIS nyata


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnPosGatewayTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        config = cls.main_pos_config
        company = config.company_id
        env["res.company"].search([("efn_brand_code", "=", "FNB"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        env["res.company"].flush_model(["efn_brand_code"])
        company.write({"efn_brand_code": "FNB"})
        # DB uji memakai mata uang company test (bukan IDR); kontrak produksi = IDR.
        env["ir.config_parameter"].sudo().set_param("efn.pos.gateway_currency", company.currency_id.name)
        journal = env["account.journal"].create(
            {"name": "QRIS Tour", "type": "bank", "code": "QRT", "company_id": company.id}
        )
        cls.gw_method = env["pos.payment.method"].create(
            {
                "name": "QRIS EFN",
                "journal_id": journal.id,
                "outstanding_account_id": cls.inbound_payment_method_line.payment_account_id.id,
                "payment_method_type": "terminal",
                "use_payment_terminal": "efn_gateway",
                "company_id": company.id,
            }
        )
        config.write(
            {
                "payment_method_ids": [Command.link(cls.gw_method.id)],
                # Validasi manual oleh kasir agar langkah tour deterministik.
                "auto_validate_terminal_payment": False,
            }
        )
        env["product.template"].create(
            {
                "name": "Produk Tour QRIS",
                "list_price": 100.0,
                "available_in_pos": True,
                "taxes_id": [Command.clear()],
            }
        )

    def setUp(self):
        super().setUp()
        self.calls = []
        self.status_after_polls = None  # None = tetap pending
        client_cls = self.env.registry["efn.bff.client"]
        for name, fake in (("_efn_post", self._fake_post), ("_efn_get", self._fake_get)):
            patcher = patch.object(client_cls, name, autospec=True, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ mock
    def _fake_post(self, _client, path, payload, log_context=None):
        self.calls.append(("POST", path, dict(payload)))
        if path == PAYMENTS:
            self.amount = payload["amount"]
            return BffResponse(
                201,
                {"payment_id": PAYMENT_ID, "status": "pending", "qr_string": QR, "provider": "mock"},
            )
        if path == f"{PAYMENTS}/{PAYMENT_ID}/cancel":
            return BffResponse(200, {"payment_id": PAYMENT_ID, "status": "cancelled"})
        raise AssertionError(f"path BFF tak terduga: {path}")

    def _fake_get(self, _client, path, params=None, log_context=None):
        self.calls.append(("GET", path, dict(params or {})))
        if path != f"{PAYMENTS}/{PAYMENT_ID}":
            raise AssertionError(f"path BFF tak terduga: {path}")
        polls = sum(1 for method, p, _payload in self.calls if method == "GET" and p == path)
        if self.status_after_polls is not None and polls >= self.status_after_polls:
            return BffResponse(
                200,
                {
                    "payment_id": PAYMENT_ID,
                    "status": "paid",
                    "amount": self.amount,
                    "paid_at": "2026-10-07T03:00:00.000Z",
                    "provider_ref": "MOCK-REF-1",
                },
            )
        return BffResponse(200, {"payment_id": PAYMENT_ID, "status": "pending"})

    def _intent(self):
        return self.env["efn.pos.payment.intent"].sudo().search([("bff_payment_id", "=", PAYMENT_ID)])

    def _last_order(self):
        return self.env["pos.order"].search([("config_id", "=", self.main_pos_config.id)], order="id desc", limit=1)

    # ----------------------------------------------------------------- tours
    def test_qris_paid(self):
        self.status_after_polls = 2  # pending sekali, lalu paid
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosQrisPaidTour", login="pos_user")
        intent = self._intent()
        self.assertEqual(intent.status, "paid")
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertEqual(intent.pos_order_uuid, order.uuid)
        payment = order.payment_ids
        self.assertEqual(payment.payment_method_id, self.gw_method)
        self.assertEqual(payment.transaction_id, PAYMENT_ID)
        create = next(p for m, path, p in self.calls if m == "POST" and path == PAYMENTS)
        self.assertEqual(create["order_ref"], order.uuid)
        self.assertEqual(create["method"], "qris")

    def test_qris_cancel(self):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosQrisCancelTour", login="pos_user")
        self.assertEqual(self._intent().status, "cancelled")
        self.assertIn(("POST", f"{PAYMENTS}/{PAYMENT_ID}/cancel", {}), self.calls)
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.payment_ids.payment_method_id, self.bank_payment_method)
