"""Tour UI POS (OWL) Poin EFN — Gelombang 13b; G18 E-30: redeem = pembayaran metode "Poin EFN".

HttpCase + tour Chrome headless, tag `efn_tour` (job CI `odoo-tour`, image target `test`). BFF
di-mock server-side dengan mem-patch `efn.bff.client._efn_post/_efn_get` pada kelas registry (berlaku juga
untuk request browser yang dilayani thread server test).
"""

from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse
from odoo.addons.efn_pos_loyalty_ledger.models import pos_config as loyalty_pos_config
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

REQUEST = "/v1/pos/otp/request"


def create_points_method(env, company):
    """Metode bayar "Poin EFN" (E-30) seperti yang akan dipasang efn_setup_pos: jurnal bank tanpa terminal,
    akun outstanding = akun liabilitas poin (data uji)."""
    journal = env["account.journal"].create(
        {"name": "Poin EFN (uji)", "type": "bank", "code": "PEFN", "company_id": company.id}
    )
    liability = env["account.account"].create(
        {
            "name": "Liabilitas Poin EFN (uji)",
            "code": "219901",
            "account_type": "liability_current",
            "company_ids": [Command.set(company.ids)],
        }
    )
    return env["pos.payment.method"].create(
        {
            "name": "Poin EFN",
            "journal_id": journal.id,
            "outstanding_account_id": liability.id,
            "efn_is_loyalty_points": True,
            "company_id": company.id,
        }
    )


VERIFY = "/v1/pos/otp/verify"
BALANCE = "/v1/pos/loyalty/balance"
PARAMS = "/v1/pos/loyalty/params"
RESERVE = "/v1/pos/loyalty/redeem/reserve"
RELEASE = "/v1/pos/loyalty/redeem/release"
COMMIT = "/v1/pos/loyalty/redeem/commit"
EARN = "/v1/pos/loyalty/earn"
GOOD_CODE = "123456"
DUMMY_TOKEN = "dummy.tour.loyalty.token"  # bukan token nyata
BFF_RES_ID = "11111111-2222-4333-8444-555555555555"


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnPosLoyaltyTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env["loyalty.program"].search([]).write({"active": False})
        company = cls.main_pos_config.company_id
        env["res.company"].search([("efn_brand_code", "=", "RTL"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        env["res.company"].flush_model(["efn_brand_code"])
        company.write({"efn_brand_code": "RTL"})
        cls.points_method = create_points_method(env, company)
        cls.main_pos_config.write(
            {
                "efn_loyalty_earn_enabled": True,
                "efn_loyalty_redeem_enabled": True,
                "payment_method_ids": [Command.link(cls.points_method.id)],
            }
        )
        cls.member = env["res.partner"].create({"name": "Member Tour Poin", "phone": "0812-3456-7890"})
        cls.product = env["product.template"].create(
            {
                "name": "Produk Tour Poin",
                "list_price": 50000.0,
                "available_in_pos": True,
                "taxes_id": [Command.clear()],
            }
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
        self.calls.append((path, dict(params or {})))
        if path == PARAMS:
            return BffResponse(200, {"point_value_idr": 100, "redeem_min_points": 50, "redeem_max_pct_of_order": 50})
        raise AssertionError(f"path BFF tak terduga: {path}")

    def _fake_post(self, _client, path, payload, log_context=None):
        self.calls.append((path, dict(payload)))
        if path == BALANCE:
            return BffResponse(200, {"points_balance": 500})
        if path == REQUEST:
            return BffResponse(202, {"challenge_id": f"chal-{len(self.calls)}", "expires_in": 180})
        if path == VERIFY:
            if payload.get("code") == GOOD_CODE:
                return BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
            return BffResponse(200, {"ok": False, "reason": "invalid", "attempts_left": 2})
        if path == RESERVE:
            value = payload["points"] * 100
            return BffResponse(
                200,
                {
                    "reservation_id": BFF_RES_ID,
                    "points": payload["points"],
                    "value_idr": value,
                    "expires_at": "2026-10-07T03:15:00.000Z",
                    "hold_until": "2026-10-08T03:00:00.000Z",
                },
            )
        if path == RELEASE:
            return BffResponse(200, {"ok": True})
        if path == COMMIT:
            return BffResponse(200, {"points_redeemed": 100, "points_balance": 400})
        if path == EARN:
            return BffResponse(200, {"points_earned": 4, "points_balance": 404})
        raise AssertionError(f"path BFF tak terduga: {path}")

    def _paths(self):
        return [path for path, _payload in self.calls]

    def _payloads(self, path):
        return [payload for p, payload in self.calls if p == path]

    def _last_order(self):
        return self.env["pos.order"].search([("config_id", "=", self.main_pos_config.id)], order="id desc", limit=1)

    def _points_payments(self, order):
        return order.payment_ids.filtered(lambda pay: pay.payment_method_id == self.points_method)

    def _run_tour(self, name):
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour(name, login="pos_user")

    def test_redeem_success(self):
        self._run_tour("EfnPosLoyaltyRedeemTour")
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.partner_id, self.member)
        payments = self._points_payments(order)
        self.assertEqual(sum(payments.mapped("amount")), 10000.0)
        self.assertEqual(order.amount_total, 50000.0, "P-8: harga & pajak tetap penuh")
        self.assertFalse(order.lines.filtered(lambda line: line.price_subtotal_incl < 0))
        self.assertEqual(order.efn_loyalty_points_redeemed, 100)
        self.assertEqual(self._payloads(REQUEST)[0]["purpose"], "pos_redeem")
        self.assertEqual(self._payloads(REQUEST)[0]["amount"], 50000)
        self.assertEqual(self._payloads(RESERVE)[0]["amount_total"], 50000)
        self.assertEqual(self._payloads(RESERVE)[0]["points"], 100)
        self.assertEqual(self._payloads(BALANCE)[0]["member_ref"], "6281234567890")
        outbox = self.env["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id)])
        self.assertEqual(set(outbox.mapped("kind")), {"earn", "redeem_commit"})
        self.env["efn.pos.loyalty.outbox"]._efn_process_due()
        self.assertEqual(self._payloads(COMMIT)[0]["value_idr"], 10000)
        self.assertEqual(self._payloads(COMMIT)[0]["payment_amount"], 10000, "K-18-3")
        self.assertEqual(self._payloads(COMMIT)[0]["order_goods_incl"], 50000, "L-26")
        self.assertEqual(
            self._payloads(EARN)[0]["eligible_amount"], 40000, "K-18-4: porsi poin tidak menghasilkan poin"
        )
        self.assertEqual(order.efn_loyalty_state, "done")

    def test_redeem_wrong_otp(self):
        self._run_tour("EfnPosLoyaltyWrongOtpTour")
        self.assertFalse(self._payloads(RESERVE), "OTP salah: tidak ada reservasi")
        self.assertEqual(len(self._payloads(VERIFY)), 3)
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertFalse(self._points_payments(order))
        self.assertFalse(self.env["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", order.uuid)]))

    def test_redeem_cancel_releases(self):
        self._run_tour("EfnPosLoyaltyCancelTour")
        # Dua kali tukar lalu batal (hapus baris bayar, kembali ke produk) -> dua release ke BFF.
        self.assertEqual(self._payloads(RELEASE), [{"reservation_id": BFF_RES_ID}] * 2)
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertFalse(self._points_payments(order))
        reservations = self.env["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", order.uuid)])
        self.assertEqual(set(reservations.mapped("state")), {"released"})
        self.assertFalse(
            self.env["efn.pos.loyalty.outbox"]
            .sudo()
            .search([("order_id", "=", order.id), ("kind", "=", "redeem_commit")])
        )

    def test_redeem_offline_blocked(self):
        self._run_tour("EfnPosLoyaltyOfflineTour")
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertTrue(self._points_payments(order))
        outbox = self.env["efn.pos.loyalty.outbox"].sudo().search([("order_id", "=", order.id)])
        self.assertIn("redeem_commit", outbox.mapped("kind"))
