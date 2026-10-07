"""Test Poin EFN POS (E-22/E-23) dengan BFF di-mock (tanpa HTTP sungguhan).

Memakai TestPoSCommon (sesi POS nyata + sync_from_ui) agar hook `_process_saved_order` teruji
sama seperti sync dari frontend. Nomor HP fiktif khusus test.
"""

import logging
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse, EfnBffError
from odoo.addons.efn_pos_loyalty_ledger import hooks
from odoo.addons.efn_pos_loyalty_ledger.models import pos_config as loyalty_pos_config
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

REQUEST = "/v1/pos/otp/request"
VERIFY = "/v1/pos/otp/verify"
CONSUME = "/v1/pos/discount-token/consume"
BALANCE = "/v1/pos/loyalty/balance"
PARAMS = "/v1/pos/loyalty/params"
EARN = "/v1/pos/loyalty/earn"
RESERVE = "/v1/pos/loyalty/redeem/reserve"
COMMIT = "/v1/pos/loyalty/redeem/commit"
RELEASE = "/v1/pos/loyalty/redeem/release"
REVERSE = "/v1/pos/loyalty/reverse"
DUMMY_TOKEN = "dummy.loyalty.token"  # bukan token nyata
BFF_RES_ID = "11111111-2222-4333-8444-555555555555"
MEMBER_A = "6281234567890"
MEMBER_B = "6281298765432"


@tagged("post_install", "-at_install", "efn")
class TestEfnPosLoyaltyLedger(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.config.write({"efn_loyalty_earn_enabled": True, "efn_loyalty_redeem_enabled": True})
        company = cls.config.company_id
        cls.env["res.company"].search([("efn_brand_code", "=", "RTL"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        cls.env["res.company"].flush_model(["efn_brand_code"])
        company.write({"efn_brand_code": "RTL"})
        cls.env["ir.config_parameter"].sudo().set_param("efn.pos.otp_required_for_promo", "True")

        Partner = cls.env["res.partner"]
        cls.member_a = Partner.create({"name": "Member Poin A", "phone": "0812-3456-7890"})
        cls.member_b = Partner.create({"name": "Member Poin B", "phone": "+62 812 9876 5432"})
        cls.partner_foreign = Partner.create({"name": "Member Asing", "phone": "+65 9123 4567"})

        Category = cls.env["product.category"]
        cls.categ_excluded = Category.create({"name": "Rokok (uji)", "efn_loyalty_earn_excluded": True})
        cls.categ_excluded_child = Category.create({"name": "Rokok kretek (uji)", "parent_id": cls.categ_excluded.id})
        cls.product = cls.create_product("Produk Poin Uji", cls.categ_basic, 50000.0)
        cls.product_taxed = cls.create_product(
            "Produk Pajak Uji", cls.categ_basic, 10000.0, tax_ids=cls.taxes["tax7"].ids
        )
        cls.product_excluded = cls.create_product("Produk Tanpa Poin", cls.categ_excluded_child, 20000.0)
        cls.points_product = cls.env.ref("efn_pos_loyalty_ledger.product_efn_points")
        cls.promo = cls.env["loyalty.program"].create(
            {
                "name": "Promo Bebas Uji Poin",
                "program_type": "promotion",
                "trigger": "auto",
                "applies_on": "current",
                "pos_ok": True,
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
        cls.promo_reward = cls.promo.reward_ids[:1]

    def setUp(self):
        super().setUp()
        self.open_new_session()
        self.calls = []
        self.responses = {}
        loyalty_pos_config._BALANCE_CALLS.clear()
        loyalty_pos_config._PARAMS_CACHE.clear()
        client_cls = self.env.registry["efn.bff.client"]
        for name, fake in (("post", self._fake_post), ("get", self._fake_get)):
            patcher = patch.object(client_cls, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ----------------------------------------------------------------- mocks
    def _respond(self, path):
        result = self.responses.get(path)
        if isinstance(result, list):
            result = result.pop(0) if len(result) > 1 else result[0]
        if result is None:
            raise AssertionError(f"path BFF tak terduga: {path}")
        if isinstance(result, Exception):
            raise result
        return result

    def _fake_post(self, path, payload, log_context=None):
        self.calls.append((path, dict(payload)))
        return self._respond(path)

    def _fake_get(self, path, params=None, log_context=None):
        self.calls.append((path, dict(params or {})))
        return self._respond(path)

    def _paths(self):
        return [path for path, _payload in self.calls]

    def _calls_to(self, path):
        return [payload for p, payload in self.calls if p == path]

    # --------------------------------------------------------------- helpers
    def _points_line(self, value, taxes=None):
        """Baris Poin EFN seperti dikirim frontend (total incl = -value)."""
        taxes = taxes or self.env["account.tax"]
        price_unit = -value
        if taxes:
            rate = sum(taxes.mapped("amount")) / 100.0
            price_unit = -value / (1 + rate)
        res = (
            taxes.compute_all(price_unit, self.currency, 1)
            if taxes
            else {"total_excluded": price_unit, "total_included": price_unit}
        )
        return {
            "product": self.points_product,
            "quantity": 1,
            "price_unit": price_unit,
            "price_subtotal": res["total_excluded"],
            "price_subtotal_incl": res["total_included"],
            "tax_ids": [(6, 0, taxes.ids)],
            "price_type": "automatic",
        }

    def _order_data(self, lines, uuid=None, customer=None, draft=False, extra=None):
        kwargs = {"uuid": uuid, "customer": customer if customer is not None else self.member_a}
        if draft:
            kwargs["payments"] = []
        if extra:
            kwargs["pos_order_ui_args"] = extra
        data = self.create_ui_order_data(lines, **kwargs)
        if draft:
            data["state"] = "draft"
        if customer is False:
            data["partner_id"] = False
        return data

    def _sync(self, data):
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _outbox(self, order=None, kind=None):
        domain = []
        if order is not None:
            domain.append(("order_id", "=", order.id))
        if kind:
            domain.append(("kind", "=", kind))
        return self.env["efn.pos.loyalty.outbox"].sudo().search(domain)

    def _run_outbox(self):
        return self.env["efn.pos.loyalty.outbox"]._efn_process_due()

    def _ok_earn(self, points=5):
        return BffResponse(200, {"points_earned": points, "points_balance": 100 + points})

    def _reserve(self, uuid, points=100, value=10000, amount=50000, partner=None):
        partner = partner or self.member_a
        self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-r", "expires_in": 180})
        self.responses[VERIFY] = BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
        self.responses[RESERVE] = BffResponse(
            200,
            {
                "reservation_id": BFF_RES_ID,
                "points": points,
                "value_idr": value,
                "expires_at": "2026-10-07T03:15:00.000Z",
                "hold_until": "2026-10-08T03:00:00.000Z",
            },
        )
        res = self.config.efn_otp_request(uuid, amount, partner.id, "pos_redeem")
        self.assertTrue(res["ok"], res)
        res = self.config.efn_otp_verify(uuid, "123456", "pos_redeem")
        self.assertTrue(res["ok"], res)
        res = self.config.efn_loyalty_redeem_reserve(uuid, partner.id, points, amount)
        self.assertTrue(res["ok"], res)
        return res

    def _reservation(self, uuid):
        return self.env["efn.pos.loyalty.reservation"].sudo().search([("order_uuid", "=", uuid)], limit=1)

    # ================================================================== EARN
    def test_earn_payload_and_eligible_amount(self):
        """K-1: tanpa pajak, tanpa kategori dikecualikan (termasuk subkategori)."""
        data = self._order_data([(self.product, 2), (self.product_taxed, 1), (self.product_excluded, 1)])
        order = self._sync(data)
        earn = self._outbox(order, "earn")
        self.assertEqual(earn.state, "pending")
        self.assertEqual(earn.idempotency_key, f"pos_earn:{order.uuid}")
        self.assertEqual(earn.eligible_amount, 110000, "2x50000 + 10000 (tanpa pajak), rokok dikecualikan")
        self.assertEqual(order.efn_loyalty_state, "pending")
        self.responses[EARN] = self._ok_earn(11)
        self._run_outbox()
        self.assertEqual(
            self._calls_to(EARN),
            [
                {
                    "idempotency_key": f"pos_earn:{order.uuid}",
                    "order_ref": order.uuid,
                    "member_ref": MEMBER_A,
                    "company_code": "RTL",
                    "eligible_amount": 110000,
                }
            ],
        )
        self.assertEqual(earn.state, "done")
        self.assertEqual(earn.points_delta, 11)
        self.assertEqual(order.efn_loyalty_points_earned, 11)
        self.assertEqual(order.efn_loyalty_state, "done")

    def test_earn_promo_reward_reduces_base(self):
        reward_line = {
            "product": self.promo_reward.discount_line_product_id,
            "quantity": 1,
            "price_unit": -5000.0,
            "price_subtotal": -5000.0,
            "price_subtotal_incl": -5000.0,
            "tax_ids": [(6, 0, [])],
            "is_reward_line": True,
            "reward_id": self.promo_reward.id,
        }
        order = self._sync(self._order_data([(self.product, 1), reward_line]))
        self.assertEqual(self._outbox(order, "earn").eligible_amount, 45000)

    def test_earn_zero_eligible_still_queued(self):
        """§9.7: earn untuk setiap order ber-member, termasuk eligible 0."""
        order = self._sync(self._order_data([(self.product_excluded, 1)]))
        earn = self._outbox(order, "earn")
        self.assertEqual(earn.state, "pending")
        self.assertEqual(earn.eligible_amount, 0)

    def test_no_partner_no_outbox(self):
        order = self._sync(self._order_data([(self.product, 1)], customer=False))
        self.assertFalse(self._outbox(order))
        self.assertFalse(order.efn_loyalty_state)

    def test_earn_disabled_on_config(self):
        self.config.efn_loyalty_earn_enabled = False
        order = self._sync(self._order_data([(self.product, 1)]))
        self.assertFalse(self._outbox(order))

    def test_foreign_phone_is_skipped(self):
        order = self._sync(self._order_data([(self.product, 1)], customer=self.partner_foreign))
        earn = self._outbox(order, "earn")
        self.assertEqual(earn.state, "skipped")
        self.assertEqual(earn.last_error, "member_missing:foreign_phone")
        self._run_outbox()
        self.assertFalse(self._calls_to(EARN))
        self.assertEqual(order.efn_loyalty_state, "done")

    def test_anonymized_partner_skipped_at_send(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        self.member_a.phone = False
        self._run_outbox()
        earn = self._outbox(order, "earn")
        self.assertEqual(earn.state, "skipped")
        self.assertEqual(earn.last_error, "member_missing:no_phone")
        self.assertFalse(self._calls_to(EARN))

    def test_duplicate_sync_single_outbox(self):
        data = self._order_data([(self.product, 1)])
        order = self._sync(data)
        self.env["pos.order"].sync_from_ui([dict(data)])
        self.assertEqual(len(self._outbox(order, "earn")), 1)
        again = self.env["efn.pos.loyalty.outbox"]._efn_enqueue(
            {
                "company_id": order.company_id.id,
                "order_id": order.id,
                "kind": "earn",
                "idempotency_key": f"pos_earn:{order.uuid}",
            }
        )
        self.assertEqual(again, self._outbox(order, "earn"))

    def test_bff_timeout_backoff_then_done(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        earn = self._outbox(order, "earn")
        self.responses[EARN] = EfnBffError("bff_timeout")
        self._run_outbox()
        self.assertEqual(earn.state, "pending")
        self.assertEqual(earn.attempt_count, 1)
        self.assertEqual(earn.last_error, "bff_timeout")
        self.assertGreater(earn.next_attempt_at, fields.Datetime.now())
        # Belum jatuh tempo -> tidak dikirim ulang.
        self._run_outbox()
        self.assertEqual(len(self._calls_to(EARN)), 1)
        # Backoff naik eksponensial.
        earn.next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)
        self._run_outbox()
        self.assertEqual(earn.attempt_count, 2)
        delay = (earn.next_attempt_at - fields.Datetime.now()).total_seconds()
        self.assertGreater(delay, 90)
        earn.next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)
        self.responses[EARN] = self._ok_earn()
        self._run_outbox()
        self.assertEqual(earn.state, "done")
        self.assertEqual(order.efn_loyalty_state, "done")

    def test_5xx_and_429_are_retried(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        earn = self._outbox(order, "earn")
        self.responses[EARN] = BffResponse(429, {"error": {"code": "rate_limited", "message": "x"}})
        self._run_outbox()
        self.assertEqual(earn.state, "pending")
        self.assertEqual(earn.last_error, "rate_limited")

    def test_409_is_final_with_review_and_activity(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        earn = self._outbox(order, "earn")
        self.responses[EARN] = BffResponse(409, {"error": {"code": "earn_limit_exceeded", "message": "x"}})
        self._run_outbox()
        self.assertEqual(earn.state, "failed")
        self.assertEqual(earn.last_error, "earn_limit_exceeded")
        self.assertEqual(order.efn_loyalty_state, "review")
        self.assertEqual(order.efn_loyalty_note, "earn:earn_limit_exceeded")
        self.assertTrue(earn.activity_flagged)
        manager_exists = earn._efn_review_user()
        if manager_exists:
            self.assertTrue(earn.activity_ids)

    def test_max_age_marks_failed(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        earn = self._outbox(order, "earn")
        self.env.cr.execute(
            "UPDATE efn_pos_loyalty_outbox SET create_date = now() - interval '72 hours' WHERE id = %s",
            (earn.id,),
        )
        earn.invalidate_recordset(["create_date"])
        self.responses[EARN] = EfnBffError("bff_unreachable")
        self._run_outbox()
        self.assertEqual(earn.state, "failed")
        self.assertEqual(earn.last_error, "max_age:bff_unreachable")

    def test_no_full_phone_in_outbox_or_logs(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        earn = self._outbox(order, "earn")
        stored = " ".join(str(earn[name]) for name in earn._fields if name not in ("message_ids", "activity_ids"))
        self.assertNotIn(MEMBER_A, stored)
        self.responses[EARN] = BffResponse(409, {"error": {"code": "earn_limit_exceeded", "message": "x"}})
        with self.assertLogs("odoo.addons.efn_pos_loyalty_ledger", level="WARNING") as logs:
            self._run_outbox()
        self.assertNotIn(MEMBER_A, "\n".join(logs.output))

    # ================================================================ REDEEM
    def test_reserve_without_otp_is_rejected(self):
        res = self.config.efn_loyalty_redeem_reserve(self.create_random_uid(), self.member_a.id, 100, 50000)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "otp_required")
        self.assertFalse(self._calls_to(RESERVE))

    def test_reserve_with_promo_challenge_is_rejected(self):
        uuid = self.create_random_uid()
        self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-p", "expires_in": 180})
        self.responses[VERIFY] = BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
        self.config.efn_otp_request(uuid, 50000, self.member_a.id)
        self.config.efn_otp_verify(uuid, "123456")
        res = self.config.efn_loyalty_redeem_reserve(uuid, self.member_a.id, 100, 50000)
        self.assertEqual(res["error_code"], "otp_required")
        self.assertFalse(self._calls_to(RESERVE))

    def test_reserve_for_other_partner_is_rejected(self):
        uuid = self.create_random_uid()
        self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-q", "expires_in": 180})
        self.responses[VERIFY] = BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
        self.config.efn_otp_request(uuid, 50000, self.member_a.id, "pos_redeem")
        self.config.efn_otp_verify(uuid, "123456", "pos_redeem")
        res = self.config.efn_loyalty_redeem_reserve(uuid, self.member_b.id, 100, 50000)
        self.assertEqual(res["error_code"], "otp_required")

    def test_reserve_rejections(self):
        uuid = self.create_random_uid()
        self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-s", "expires_in": 180})
        self.responses[VERIFY] = BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
        self.config.efn_otp_request(uuid, 50000, self.member_a.id, "pos_redeem")
        self.config.efn_otp_verify(uuid, "123456", "pos_redeem")
        challenge = self.env["efn.pos.otp.challenge"].sudo().search([("order_ref", "=", uuid)])
        self.responses[RESERVE] = BffResponse(409, {"error": {"code": "insufficient_points", "message": "x"}})
        res = self.config.efn_loyalty_redeem_reserve(uuid, self.member_a.id, 100, 50000)
        self.assertEqual(res["error_code"], "insufficient_points")
        self.assertEqual(challenge.state, "verified", "penolakan bisnis: kasir boleh coba poin lain")
        self.responses[RESERVE] = BffResponse(409, {"error": {"code": "token_invalid", "message": "x"}})
        res = self.config.efn_loyalty_redeem_reserve(uuid, self.member_a.id, 50, 50000)
        self.assertEqual(res["error_code"], "token_invalid")
        self.assertEqual(challenge.state, "failed")
        self.assertFalse(challenge.discount_token)
        self.assertFalse(self._reservation(uuid))

    def test_reserve_payload_and_challenge_consumed(self):
        uuid = self.create_random_uid()
        res = self._reserve(uuid)
        self.assertEqual(
            self._calls_to(RESERVE),
            [
                {
                    "discount_token": DUMMY_TOKEN,
                    "order_ref": uuid,
                    "member_ref": MEMBER_A,
                    "points": 100,
                    "amount_total": 50000,
                }
            ],
        )
        self.assertEqual(self._calls_to(REQUEST)[0]["purpose"], "pos_redeem")
        reservation = self._reservation(uuid)
        self.assertEqual(res["reservation_id"], reservation.id, "browser hanya menerima id lokal")
        self.assertNotIn(BFF_RES_ID, str(res))
        self.assertEqual(reservation.state, "reserved")
        self.assertEqual(reservation.value_idr, 10000)
        self.assertEqual(reservation.member_ref_masked, "62812****890")
        challenge = self.env["efn.pos.otp.challenge"].sudo().search([("order_ref", "=", uuid)])
        self.assertEqual(challenge.state, "consumed")
        self.assertFalse(challenge.discount_token)
        # Reservasi kedua untuk order yang sama ditolak lokal.
        res2 = self.config.efn_loyalty_redeem_reserve(uuid, self.member_a.id, 100, 50000)
        self.assertEqual(res2["error_code"], "reservation_exists")

    def test_redeem_challenge_does_not_open_promo(self):
        """Challenge pos_redeem tidak membuka gate promo (E-23)."""
        otp_program = self.env["loyalty.program"].create(
            {
                "name": "Promo OTP Uji Poin",
                "program_type": "promotion",
                "trigger": "auto",
                "applies_on": "current",
                "pos_ok": True,
                "pos_config_ids": [Command.set(self.config.ids)],
                "efn_requires_otp": True,
                "rule_ids": [Command.create({"minimum_amount": 0})],
                "reward_ids": [Command.create({"reward_type": "discount", "discount": 10})],
            }
        )
        reward = otp_program.reward_ids[:1]
        uuid = self.create_random_uid()
        self._reserve(uuid)
        reward_line = {
            "product": reward.discount_line_product_id,
            "quantity": 1,
            "price_unit": -1000.0,
            "price_subtotal": -1000.0,
            "price_subtotal_incl": -1000.0,
            "tax_ids": [(6, 0, [])],
            "is_reward_line": True,
            "reward_id": reward.id,
        }
        with self.assertRaises(UserError):
            self._sync(self._order_data([(self.product, 1), reward_line, self._points_line(10000)], uuid=uuid))
        self.assertFalse(self._calls_to(CONSUME))

    def test_redeem_full_flow_commit(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(self._order_data([(self.product, 1), self._points_line(10000)], uuid=uuid))
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_loyalty_points_redeemed, 100)
        self.assertEqual(order.efn_loyalty_redeem_value, 10000)
        commit = self._outbox(order, "redeem_commit")
        self.assertEqual(commit.idempotency_key, f"pos_redeem:{uuid}")
        self.assertEqual(
            self._outbox(order, "earn").eligible_amount, 40000, "K-1: porsi yang dibayar poin tidak menghasilkan poin"
        )
        self.responses[COMMIT] = BffResponse(200, {"points_redeemed": 100, "points_balance": 50})
        self.responses[EARN] = self._ok_earn(4)
        self._run_outbox()
        self.assertEqual(
            self._calls_to(COMMIT),
            [
                {
                    "reservation_id": BFF_RES_ID,
                    "order_ref": uuid,
                    "member_ref": MEMBER_A,
                    "value_idr": 10000,
                    "idempotency_key": f"pos_redeem:{uuid}",
                }
            ],
        )
        self.assertEqual(commit.state, "done")
        self.assertEqual(commit.points_delta, -100)
        reservation = self._reservation(uuid)
        self.assertEqual(reservation.state, "committed")
        self.assertEqual(reservation.order_id, order)
        self.assertEqual(order.efn_loyalty_state, "done")
        # Idempoten: menjalankan lagi tidak mengirim ulang.
        self._run_outbox()
        self.assertEqual(len(self._calls_to(COMMIT)), 1)

    def test_redeem_tax_groups_split(self):
        """Baris poin per kelompok pajak (incl. pajak = value_idr) diterima server."""
        uuid = self.create_random_uid()
        self._reserve(uuid, value=10000, amount=60700)
        # Produk 50000 (tanpa pajak) + 10000 (+7%) = 60700; porsi poin proporsional.
        share_taxed = round(10000 * 10700 / 60700, 2)
        share_free = 10000 - share_taxed
        lines = [
            (self.product, 1),
            (self.product_taxed, 1),
            self._points_line(share_free),
            self._points_line(share_taxed, taxes=self.taxes["tax7"]),
        ]
        order = self._sync(self._order_data(lines, uuid=uuid))
        self.assertTrue(self._outbox(order, "redeem_commit"))
        self.assertNotEqual(order.efn_loyalty_state, "review")
        points_excl = sum(
            order._efn_line_raw_amounts(line)[0] for line in order.lines if line.product_id == self.points_product
        )
        self.assertAlmostEqual(self._outbox(order, "earn").eligible_amount, 60000 + points_excl, delta=1.5)

    def test_points_line_without_reservation_is_rejected(self):
        data = self._order_data([(self.product, 1), self._points_line(10000)])
        data["efn_loyalty_points_redeemed"] = 100  # dipalsukan browser
        with self.assertRaises(UserError):
            self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))

    def test_points_line_with_spoofed_flags_is_rejected(self):
        line = self._points_line(10000)
        line.update({"is_reward_line": True, "reward_id": self.promo_reward.id, "price_type": "original"})
        with self.assertRaises(UserError):
            self._sync(self._order_data([(self.product, 1), line]))

    def test_value_mismatch_flags_review(self):
        uuid = self.create_random_uid()
        self._reserve(uuid, value=10000)
        order = self._sync(self._order_data([(self.product, 1), self._points_line(15000)], uuid=uuid))
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_loyalty_state, "review")
        self.assertEqual(order.efn_loyalty_note, "redeem:value_mismatch")
        self.assertFalse(self._outbox(order, "redeem_commit"))
        self.assertEqual(self._reservation(uuid).state, "review")

    def test_partner_changed_flags_review(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(
            self._order_data([(self.product, 1), self._points_line(10000)], uuid=uuid, customer=self.member_b)
        )
        self.assertEqual(order.efn_loyalty_note, "redeem:partner_mismatch")
        self.assertFalse(self._outbox(order, "redeem_commit"))

    def test_commit_reservation_expired_is_final_review(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(self._order_data([(self.product, 1), self._points_line(10000)], uuid=uuid))
        self.responses[COMMIT] = BffResponse(409, {"error": {"code": "reservation_expired", "message": "x"}})
        self.responses[EARN] = self._ok_earn()
        self._run_outbox()
        commit = self._outbox(order, "redeem_commit")
        self.assertEqual(commit.state, "failed")
        self.assertEqual(self._reservation(uuid).state, "review")
        self.assertEqual(order.efn_loyalty_state, "review")

    def test_reservation_without_points_line_is_released(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(self._order_data([(self.product, 1)], uuid=uuid))
        release = self._outbox(order, "redeem_release")
        self.assertTrue(release)
        self.responses[RELEASE] = BffResponse(200, {"ok": True})
        self.responses[EARN] = self._ok_earn()
        self._run_outbox()
        self.assertEqual(self._calls_to(RELEASE), [{"reservation_id": BFF_RES_ID}])
        self.assertEqual(self._reservation(uuid).state, "released")
        self.assertEqual(order.efn_loyalty_points_redeemed, 0)

    def test_draft_points_line_stripped_without_reservation(self):
        order = self._sync(self._order_data([(self.product, 1), self._points_line(10000)], draft=True))
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.lines.filtered(lambda line: line.product_id == self.points_product))

    def test_release_on_remove_from_ui(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(self._order_data([(self.product, 1), self._points_line(10000)], uuid=uuid, draft=True))
        self.assertTrue(order.lines.filtered(lambda line: line.product_id == self.points_product))
        reservation = self._reservation(uuid)
        self.env["pos.order"].remove_from_ui([order.id])
        # Core menghapus order draft; pos_self_order (terpasang lewat efn_setup_pos) hanya membatalkan.
        self.assertTrue(not order.exists() or order.state == "cancel")
        release = self.env["efn.pos.loyalty.outbox"].sudo().search([("reservation_id", "=", reservation.id)])
        self.assertEqual(release.kind, "redeem_release")
        self.assertFalse(release.order_id)
        self.responses[RELEASE] = BffResponse(404, {"error": {"code": "reservation_not_found", "message": "x"}})
        self._run_outbox()
        self.assertEqual(release.state, "done", "§9.7: 404 pada release = selesai")
        self.assertEqual(reservation.state, "released")

    def test_release_on_cancel_draft(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(self._order_data([(self.product, 1), self._points_line(10000)], uuid=uuid, draft=True))
        order.action_pos_order_cancel()
        self.assertEqual(self._outbox(order, "redeem_release").reservation_id, self._reservation(uuid))

    def test_release_rpc_idempotent(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        self.responses[RELEASE] = BffResponse(200, {"ok": True})
        self.assertTrue(self.config.efn_loyalty_redeem_release(uuid)["ok"])
        self.assertEqual(self._reservation(uuid).state, "released")
        self.assertTrue(self.config.efn_loyalty_redeem_release(uuid)["ok"])
        self.assertEqual(len(self._calls_to(RELEASE)), 1)

    def test_release_rpc_bff_down_is_queued(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        self.responses[RELEASE] = EfnBffError("bff_unreachable")
        self.assertTrue(self.config.efn_loyalty_redeem_release(uuid)["ok"])
        reservation = self._reservation(uuid)
        self.assertEqual(reservation.state, "released")
        release = self.env["efn.pos.loyalty.outbox"].sudo().search([("reservation_id", "=", reservation.id)])
        self.assertEqual(release.state, "pending")
        self.responses[RELEASE] = BffResponse(200, {"ok": True})
        self._run_outbox()
        self.assertEqual(release.state, "done")

    def test_reservation_expiry_housekeeping(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        reservation = self._reservation(uuid)
        self.env.cr.execute(
            "UPDATE efn_pos_loyalty_reservation SET create_date = now() - interval '30 hours' WHERE id = %s",
            (reservation.id,),
        )
        reservation.invalidate_recordset(["create_date"])
        result = self.env["efn.pos.loyalty.reservation"]._cron_efn_loyalty_housekeeping()
        self.assertEqual(reservation.state, "expired")
        self.assertGreaterEqual(result["expired"], 1)

    # =============================================================== REVERSE
    def _paid_original(self, points_value=0):
        uuid = self.create_random_uid()
        lines = [(self.product, 2)]
        if points_value:
            self._reserve(uuid, value=points_value, amount=100000)
            lines.append(self._points_line(points_value))
        order = self._sync(self._order_data(lines, uuid=uuid))
        self.responses[EARN] = self._ok_earn(10)
        self.responses[COMMIT] = BffResponse(200, {"points_redeemed": 100, "points_balance": 0})
        self._run_outbox()
        self.assertEqual(self._outbox(order, "earn").state, "done")
        return order

    def _refund_data(self, original, qty=1, adjustment=None):
        product_line = original.lines.filtered(lambda line: line.product_id == self.product)
        lines = [{"product": self.product, "quantity": -qty, "refunded_orderline_id": product_line.id}]
        if adjustment:
            line = self._points_line(-adjustment)
            lines.append(line)
        return self._order_data(lines, extra={"is_refund": True})

    def test_refund_half_ratio(self):
        original = self._paid_original()
        refund = self._sync(self._refund_data(original, qty=1))
        reverse = self._outbox(refund, "reverse")
        self.assertEqual(reverse.refund_ratio, 0.5)
        self.assertEqual(reverse.eligible_amount, 50000)
        self.assertFalse(self._outbox(refund, "earn"), "refund tidak menghasilkan earn")
        self.responses[REVERSE] = BffResponse(200, {"points_clawed_back": 5, "points_restored": 0, "points_balance": 5})
        self._run_outbox()
        self.assertEqual(
            self._calls_to(REVERSE),
            [
                {
                    "idempotency_key": f"pos_reverse:{refund.uuid}",
                    "original_order_ref": original.uuid,
                    "refund_order_ref": refund.uuid,
                    "member_ref": MEMBER_A,
                    "refunded_eligible_amount": 50000,
                    "refund_ratio": 0.5,
                }
            ],
        )
        self.assertEqual(reverse.points_delta, -5)

    def test_two_refunds_cumulative_ratio(self):
        original = self._paid_original()
        first = self._sync(self._refund_data(original, qty=1))
        second = self._sync(self._refund_data(original, qty=1))
        ratios = self._outbox(first, "reverse").refund_ratio + self._outbox(second, "reverse").refund_ratio
        self.assertAlmostEqual(ratios, 1.0)
        self.assertLessEqual(ratios, 1.0)

    def test_reverse_waits_for_pending_earn(self):
        original = self._sync(self._order_data([(self.product, 2)]))
        self.responses[EARN] = EfnBffError("bff_timeout")
        self._run_outbox()
        refund = self._sync(self._refund_data(original, qty=1))
        reverse = self._outbox(refund, "reverse")
        self.responses[REVERSE] = BffResponse(200, {"points_clawed_back": 5, "points_restored": 0, "points_balance": 0})
        self._run_outbox()
        self.assertFalse(self._calls_to(REVERSE), "reverse menunggu earn order asal")
        self.assertEqual(reverse.attempt_count, 0)
        earn = self._outbox(original, "earn")
        earn.next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)
        reverse.next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)
        self.responses[EARN] = self._ok_earn()
        # BFF masih memproses earn -> 409 earn_pending (K-6) dicoba ulang, bukan final.
        self.responses[REVERSE] = BffResponse(409, {"error": {"code": "earn_pending", "message": "x"}})
        self._run_outbox()
        self.assertEqual(earn.state, "done")
        self.assertEqual(reverse.state, "pending")
        self.assertEqual(reverse.last_error, "earn_pending")
        reverse.next_attempt_at = fields.Datetime.now() - timedelta(seconds=1)
        self.responses[REVERSE] = BffResponse(200, {"points_clawed_back": 5, "points_restored": 0, "points_balance": 0})
        self._run_outbox()
        self.assertEqual(reverse.state, "done")

    def test_refund_of_points_order_without_adjustment_flags_review(self):
        original = self._paid_original(points_value=10000)
        refund = self._sync(self._refund_data(original, qty=1))
        self.assertEqual(refund.efn_loyalty_state, "review")
        self.assertEqual(refund.efn_loyalty_note, "refund_points_adjustment_mismatch")
        self.assertTrue(self._outbox(refund, "reverse"), "clawback tetap diantre")

    def test_refund_of_points_order_with_adjustment_ok(self):
        original = self._paid_original(points_value=10000)
        preview = self.config.efn_loyalty_refund_preview(
            original.id, [[original.lines.filtered(lambda line: line.product_id == self.product).id, 1]]
        )
        self.assertEqual(preview, {"ok": True, "ratio": 0.5, "value_idr": 5000})
        refund = self._sync(self._refund_data(original, qty=1, adjustment=preview["value_idr"]))
        self.assertNotEqual(refund.efn_loyalty_state, "review")
        reverse = self._outbox(refund, "reverse")
        self.assertEqual(reverse.refund_ratio, 0.5)
        self.assertEqual(reverse.eligible_amount, 45000, "faktor uang order asal (90%)")
        self.assertEqual(self._outbox(original, "earn").eligible_amount, 90000)

    def test_refund_with_negative_points_line_is_rejected(self):
        original = self._paid_original(points_value=10000)
        data = self._refund_data(original, qty=1, adjustment=-5000)  # menambah uang kembali
        with self.assertRaises(UserError):
            self._sync(data)

    def test_refund_preview_rejects_points_line_and_foreign_order(self):
        original = self._paid_original(points_value=10000)
        points_line = original.lines.filtered(lambda line: line.product_id == self.points_product)
        res = self.config.efn_loyalty_refund_preview(original.id, [[points_line.id, 1]])
        self.assertEqual(res["error_code"], "invalid_lines")
        res = self.config.efn_loyalty_refund_preview(original.id, "bukan list")
        self.assertEqual(res["error_code"], "invalid_lines")

    # ======================================================= BALANCE / PARAMS
    def test_balance_payload_and_params(self):
        self.responses[BALANCE] = BffResponse(200, {"points_balance": 250})
        self.responses[PARAMS] = BffResponse(
            200, {"point_value_idr": 100, "redeem_min_points": 50, "redeem_max_pct_of_order": 50}
        )
        res = self.config.efn_loyalty_balance(self.member_a.id)
        self.assertEqual(
            res,
            {
                "ok": True,
                "points_balance": 250,
                "member_masked": "62812****890",
                "point_value_idr": 100,
                "redeem_min_points": 50,
                "redeem_max_pct_of_order": 50,
            },
        )
        self.assertEqual(
            self._calls_to(BALANCE),
            [{"member_ref": MEMBER_A, "pos_config_id": self.config.id, "company_code": "RTL"}],
        )

    def test_balance_rate_limited_per_session(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.pos.loyalty.balance_rate_per_minute", "2")
        self.responses[BALANCE] = BffResponse(200, {"points_balance": 1})
        self.responses[PARAMS] = BffResponse(200, {})
        self.assertTrue(self.config.efn_loyalty_balance(self.member_a.id)["ok"])
        self.assertTrue(self.config.efn_loyalty_balance(self.member_a.id)["ok"])
        res = self.config.efn_loyalty_balance(self.member_a.id)
        self.assertEqual(res["error_code"], "rate_limited")
        self.assertEqual(len(self._calls_to(BALANCE)), 2)

    def test_balance_bff_down(self):
        self.responses[BALANCE] = EfnBffError("bff_timeout")
        res = self.config.efn_loyalty_balance(self.member_a.id)
        self.assertEqual(res["error_code"], "bff_unavailable")

    # ================================================= KONFIGURASI & SECURITY
    def test_points_product_is_special_and_coded(self):
        self.assertEqual(self.points_product.default_code, "EFN-POINTS")
        self.assertEqual(self.points_product.type, "service")
        self.assertFalse(self.points_product.taxes_id)
        self.assertIn(self.points_product, self.config._get_special_products())
        self.assertEqual(self.config.efn_loyalty_points_product, self.points_product.id)

    def test_category_flag_includes_children(self):
        excluded = self.env["product.category"]._efn_loyalty_excluded_ids()
        self.assertIn(self.categ_excluded.id, excluded)
        self.assertIn(self.categ_excluded_child.id, excluded)
        self.assertNotIn(self.categ_basic.id, excluded)

    def test_kiosk_cannot_enable_redeem(self):
        if "self_ordering_mode" not in self.env["pos.config"]._fields:
            self.skipTest("pos_self_order tidak terpasang")
        kiosk = self.config.copy({"name": "Kiosk Poin Uji", "efn_loyalty_redeem_enabled": False})
        kiosk.self_ordering_mode = "kiosk"
        with self.assertRaises(ValidationError):
            kiosk.efn_loyalty_redeem_enabled = True

    def test_redeem_disabled_config_rejects_reserve(self):
        self.config.efn_loyalty_redeem_enabled = False
        with self.assertRaises(AccessError):
            self.config.efn_loyalty_redeem_reserve(self.create_random_uid(), self.member_a.id, 100, 50000)

    def test_acl_pos_user(self):
        uuid = self.create_random_uid()
        self._reserve(uuid)
        order = self._sync(self._order_data([(self.product, 1), self._points_line(10000)], uuid=uuid))
        cashier = new_test_user(
            self.env,
            login="efn_loyalty_cashier",
            groups="point_of_sale.group_pos_user",
            company_id=self.env.company.id,
        )
        reservation = self._reservation(uuid).with_user(cashier)
        with self.assertRaises(AccessError):
            reservation.read(["points"])
        outbox = self._outbox(order).with_user(cashier)
        with self.assertRaises(AccessError):
            outbox.write({"state": "done"})
        manager = new_test_user(
            self.env,
            login="efn_loyalty_manager",
            groups="point_of_sale.group_pos_manager",
            company_id=self.env.company.id,
        )
        data = self._reservation(uuid).with_user(manager).read(["points", "state"])[0]
        self.assertEqual(data["points"], 100)
        with self.assertRaises(AccessError):
            self._reservation(uuid).with_user(manager).read(["bff_reservation_id"])

    def test_multi_company_rule(self):
        order = self._sync(self._order_data([(self.product, 1)]))
        other_company = self.env["res.company"].create({"name": "PT Lain Uji Poin"})
        outsider = new_test_user(
            self.env,
            login="efn_loyalty_other_co",
            groups="point_of_sale.group_pos_manager",
            company_id=other_company.id,
            company_ids=[Command.set(other_company.ids)],
        )
        Outbox = self.env["efn.pos.loyalty.outbox"].with_user(outsider)
        self.assertFalse(Outbox.search([("order_id", "=", order.id)]))
        self.assertTrue(self._outbox(order))

    def test_audit_filter_e24(self):
        new_filter = self.env.ref("efn_pos_loyalty_ledger.filter_pos_line_negative_non_reward_no_points")
        self.assertIn(str(self.points_product.id), new_filter.domain)
        old_filter = self.env.ref(hooks.OLD_FILTER_XMLID, raise_if_not_found=False)
        if not old_filter:
            self.skipTest("efn_setup_pos tidak terpasang")
        hooks.post_init_hook(self.env)
        self.assertFalse(old_filter.active)
        hooks.uninstall_hook(self.env)
        self.assertTrue(old_filter.active)

    def test_payment_logging_masks_phone(self):
        """Log RPC hanya memuat nomor ter-mask."""
        self.responses[BALANCE] = BffResponse(500, {})
        with self.assertLogs(level=logging.DEBUG) as logs:
            logging.getLogger("odoo.addons.efn_pos_loyalty_ledger").debug("cek log")
            self.config.efn_loyalty_balance(self.member_a.id)
        self.assertNotIn(MEMBER_A, "\n".join(logs.output))
