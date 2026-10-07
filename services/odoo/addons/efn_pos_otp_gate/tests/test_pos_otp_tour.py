"""Test UI POS (OWL) nyata untuk OTP gate — Gelombang 12.

`HttpCase` + tour (`start_pos_tour`, pola `point_of_sale/tests/test_frontend.py` 19.0) dengan
Chrome headless. Tag `efn_tour` (bukan `efn`): hanya dijalankan job CI `odoo-tour` yang memakai
image target `test` (Chrome tidak ada di image produksi). Tanpa Chrome, Odoo men-skip test
HttpCase -> job CI menolak hasil "skipped" (lihat ci.yml).

BFF di-mock server-side: `efn.bff.client._efn_post` di-patch pada kelas registry, jadi berlaku juga
untuk request HTTP dari browser yang dilayani thread server test (registry sama).
"""

from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse
from odoo.addons.point_of_sale.tests.test_frontend import TestPointOfSaleHttpCommon

REQUEST = "/v1/pos/otp/request"
VERIFY = "/v1/pos/otp/verify"
CONSUME = "/v1/pos/discount-token/consume"
GOOD_CODE = "123456"
DUMMY_TOKEN = "dummy.tour.token"  # bukan token nyata


@tagged("post_install", "-at_install", "efn_tour")
class TestEfnPosOtpTour(TestPointOfSaleHttpCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env["loyalty.program"].search([]).write({"active": False})
        ICP = env["ir.config_parameter"].sudo()
        ICP.set_param("efn.pos.otp_required_for_promo", "True")
        ICP.set_param("efn.pos.promo_disabled_when_offline", "True")
        company = cls.main_pos_config.company_id
        env["res.company"].search([("efn_brand_code", "=", "FNB"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        env["res.company"].flush_model(["efn_brand_code"])
        company.write({"efn_brand_code": "FNB"})

        # Nomor fiktif khusus test.
        cls.member = env["res.partner"].create({"name": "Member Tour OTP", "phone": "0812-3456-7890"})
        cls.product = env["product.template"].create(
            {
                "name": "Produk Tour OTP",
                "list_price": 100.0,
                "available_in_pos": True,
                "taxes_id": [Command.clear()],
            }
        )
        cls.program = env["loyalty.program"].create(
            {
                "name": "Promo Tour OTP",
                "program_type": "promotion",
                "trigger": "auto",
                "applies_on": "current",
                "pos_ok": True,
                "pos_config_ids": [Command.set(cls.main_pos_config.ids)],
                "efn_requires_otp": True,
                "rule_ids": [Command.create({"minimum_amount": 0})],
                "reward_ids": [
                    Command.create(
                        {
                            "reward_type": "discount",
                            "discount": 10,
                            "discount_mode": "percent",
                            "discount_applicability": "order",
                        }
                    )
                ],
            }
        )

    def setUp(self):
        super().setUp()
        self.calls = []
        client_cls = self.env.registry["efn.bff.client"]
        patcher = patch.object(client_cls, "_efn_post", autospec=True, side_effect=self._fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ mock
    def _fake_post(self, _client, path, payload, log_context=None):
        self.calls.append((path, dict(payload)))
        if path == REQUEST:
            return BffResponse(202, {"challenge_id": f"chal-{len(self.calls)}", "expires_in": 180})
        if path == VERIFY:
            if payload.get("code") == GOOD_CODE:
                return BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
            return BffResponse(200, {"ok": False, "reason": "invalid", "attempts_left": 2})
        if path == CONSUME:
            return BffResponse(200, {"ok": True})
        raise AssertionError(f"path BFF tak terduga: {path}")

    def _paths(self):
        return [path for path, _payload in self.calls]

    def _last_order(self):
        return self.env["pos.order"].search([("config_id", "=", self.main_pos_config.id)], order="id desc", limit=1)

    # ----------------------------------------------------------------- tours
    def test_otp_gate_flow(self):
        """Pilih reward ber-OTP tanpa pelanggan -> pilih member -> OTP salah -> benar -> bayar."""
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosOtpGateTour", login="pos_user")
        self.assertEqual(self._paths(), [REQUEST, VERIFY, VERIFY, CONSUME])
        request_payload = self.calls[0][1]
        self.assertEqual(request_payload["member_ref"], "6281234567890", "nomor dari res.partner, bukan input kasir")
        self.assertEqual(request_payload["company_code"], "FNB")
        self.assertEqual(self.calls[1][1]["code"], "111111")
        order = self._last_order()
        self.assertEqual(order.partner_id, self.member)
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_otp_gate_state, "consumed")
        self.assertTrue(order.efn_otp_verified)
        self.assertEqual(order.efn_otp_member_masked, "62812****890")
        self.assertTrue(order.lines.filtered(lambda line: line.reward_id.program_id == self.program))
        consume = self.calls[3][1]
        self.assertEqual(consume["order_ref"], order.uuid)
        self.assertEqual(consume["amount_total"], int(round(order.amount_total)))
        self.assertEqual(consume["discount_token"], DUMMY_TOKEN)
        challenge = self.env["efn.pos.otp.challenge"].search([("order_ref", "=", order.uuid)])
        self.assertEqual(challenge.state, "consumed")

    def test_otp_gate_no_customer(self):
        """Tanpa pelanggan dan kasir batal memilih: OTP tidak diminta, reward tidak terpasang."""
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosOtpNoCustomerTour", login="pos_user")
        self.assertEqual(self.calls, [], "tanpa pelanggan tidak ada panggilan BFF")
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertFalse(order.lines.filtered("reward_id"))
        self.assertFalse(order.efn_otp_gate_state)

    def test_otp_gate_offline(self):
        """Offline: reward ber-OTP tidak ditawarkan; validasi order ber-promo diblok sampai online."""
        self.main_pos_config.with_user(self.pos_user).open_ui()
        self.start_pos_tour("EfnPosOtpOfflineTour", login="pos_user")
        self.assertEqual(self._paths(), [REQUEST, VERIFY, CONSUME])
        order = self._last_order()
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_otp_gate_state, "consumed")
