"""Test OTP gate POS dengan BFF di-mock (tidak ada HTTP sungguhan).

Memakai TestPoSCommon dari point_of_sale (sesi POS nyata + sync_from_ui) agar hook
`_process_saved_order` teruji sama seperti saat frontend melakukan sync.

Gelombang 3: SEC-07 (member dari partner order, bukan input kasir; consume membawa
member_ref partner final; 409 member_mismatch -> review) dan SEC-12 (gate ditentukan dari
program reward di server, bukan flag klien).
"""

from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_core.models.efn_bff_client import BffResponse, EfnBffError
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

REQUEST = "/v1/pos/otp/request"
VERIFY = "/v1/pos/otp/verify"
CONSUME = "/v1/pos/discount-token/consume"
DUMMY_TOKEN = "dummy.test.token"  # bukan token nyata
# Nomor fiktif khusus test (bukan data pribadi nyata).
MEMBER_A = "6281234567890"
MEMBER_B = "6281298765432"


@tagged("post_install", "-at_install", "efn")
class TestEfnPosOtpGate(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.env["ir.config_parameter"].sudo().set_param("efn.pos.otp_required_for_promo", "True")
        company = cls.config.company_id
        # Pastikan kode FNB unik di DB uji (data demo efn_kitting bisa memakainya).
        cls.env["res.company"].search([("efn_brand_code", "=", "FNB"), ("id", "!=", company.id)]).write(
            {"efn_brand_code": False}
        )
        cls.env["res.company"].flush_model(["efn_brand_code"])
        company.write({"efn_brand_code": "FNB"})  # efn_business_type kini turunan (3b)

        Partner = cls.env["res.partner"]
        cls.member_a = Partner.create({"name": "Member Uji A", "phone": "0812-3456-7890"})
        cls.member_b = Partner.create({"name": "Member Uji B", "phone": "+62 812 9876 5432"})
        cls.partner_no_phone = Partner.create({"name": "Pelanggan Tanpa HP"})
        cls.partner_foreign = Partner.create({"name": "Pelanggan Asing", "phone": "+65 9123 4567"})

        cls.product = cls.create_product("Produk Uji OTP", cls.categ_basic, 100.0)
        cls.program = cls._create_program(requires_otp=True)
        cls.reward = cls.program.reward_ids[:1]
        cls.free_program = cls._create_program(requires_otp=False)
        cls.free_reward = cls.free_program.reward_ids[:1]

    @classmethod
    def _create_program(cls, requires_otp, program_type="promotion", pos_configs=None):
        if pos_configs is None:
            # Program ber-OTP wajib dibatasi ke POS kasir (Gelombang 3b).
            pos_configs = cls.config if requires_otp else cls.env["pos.config"]
        return cls.env["loyalty.program"].create(
            {
                "name": f"Promo Uji {'OTP' if requires_otp else 'Bebas'}",
                "program_type": program_type,
                "trigger": "auto",
                "applies_on": "current",
                "pos_ok": True,
                "pos_config_ids": [Command.set(pos_configs.ids)],
                "efn_requires_otp": requires_otp,
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
        self.open_new_session()
        self.calls = []
        self.responses = {}

    # ----------------------------------------------------------------- helpers
    def _fake_post(self, path, payload, log_context=None):
        self.calls.append((path, dict(payload)))
        result = self.responses[path]
        if isinstance(result, Exception):
            raise result
        return result

    def _patch_bff(self):
        return patch.object(self.env.registry["efn.bff.client"], "_efn_post", side_effect=self._fake_post)

    def _order_data(self, reward=None, draft=False, uuid=None, customer=None, spoof_flags=False):
        """`spoof_flags=True`: klien mengosongkan is_reward_line/reward_id (SEC-12)."""
        lines = [(self.product, 1)]
        if reward:
            line = {
                "product": reward.discount_line_product_id,
                "quantity": 1,
                "price_unit": -10.0,
                "price_subtotal": -10.0,
                "price_subtotal_incl": -10.0,
                "tax_ids": [(6, 0, [])],
                "is_reward_line": True,
                "reward_id": reward.id,
            }
            if spoof_flags:
                line["is_reward_line"] = False
                line.pop("reward_id")
            lines.append(line)
        kwargs = {"uuid": uuid, "customer": customer if customer is not None else self.member_a}
        if draft:
            kwargs["payments"] = []
        data = self.create_ui_order_data(lines, **kwargs)
        if draft:
            data["state"] = "draft"
        if customer is False:
            data["partner_id"] = False
        return data

    def _sync(self, data):
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _verify_otp_for(self, uuid, partner=None):
        partner = partner or self.member_a
        self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-1", "expires_in": 180})
        self.responses[VERIFY] = BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
        res = self.config.efn_otp_request(uuid, 90.0, partner.id)
        self.assertTrue(res["ok"], res)
        res = self.config.efn_otp_verify(uuid, "123456")
        self.assertTrue(res["ok"], res)

    def _challenge(self, uuid):
        return self.env["efn.pos.otp.challenge"].sudo().search([("order_ref", "=", uuid)], limit=1)

    def _consume_calls(self):
        return [c for c in self.calls if c[0] == CONSUME]

    # ------------------------------------------------------------------- tests
    def test_request_verify_and_consume_success(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(200, {"ok": True})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid))

        request_payload = self.calls[0][1]
        self.assertEqual(self.calls[0][0], REQUEST)
        self.assertEqual(request_payload["member_ref"], MEMBER_A, "nomor dari partner, dinormalisasi")
        self.assertEqual(request_payload["order_ref"], uuid)
        self.assertEqual(request_payload["amount"], 90)
        self.assertEqual(request_payload["company_code"], "FNB")
        self.assertEqual(request_payload["pos_config_id"], self.config.id)
        self.assertEqual(self.calls[1], (VERIFY, {"challenge_id": "chal-1", "order_ref": uuid, "code": "123456"}))
        self.assertEqual(
            self.calls[2],
            (
                CONSUME,
                {
                    "discount_token": DUMMY_TOKEN,
                    "order_ref": uuid,
                    "member_ref": MEMBER_A,
                    # §8.8: total final dari server, Rupiah bulat.
                    "amount_total": int(round(order.amount_total)),
                },
            ),
        )
        self.assertIsInstance(self.calls[2][1]["amount_total"], int)

        self.assertEqual(order.state, "paid")
        self.assertTrue(order.lines.filtered("is_reward_line"))
        self.assertTrue(order.efn_otp_verified)
        self.assertEqual(order.efn_otp_gate_state, "consumed")
        self.assertEqual(order.efn_otp_member_masked, "62812****890")
        challenge = self._challenge(uuid)
        self.assertEqual(challenge.state, "consumed")
        self.assertEqual(challenge.partner_id, self.member_a)
        self.assertEqual(challenge.consumed_order_id, order)
        self.assertFalse(challenge.discount_token, "token harus dihapus setelah consume")

    # ---------------------------------------------------------- SEC-07 request
    def test_request_without_partner_is_rejected(self):
        with self._patch_bff():
            res = self.config.efn_otp_request(self.create_random_uid(), 100.0, False)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "partner_missing")
        self.assertIn("pelanggan", res["message"])
        self.assertFalse(self.calls)

    def test_request_with_non_integer_partner_is_rejected(self):
        with self._patch_bff():
            res = self.config.efn_otp_request(self.create_random_uid(), 100.0, "6281234567890")
        self.assertEqual(res["error_code"], "partner_missing")
        self.assertFalse(self.calls)

    def test_request_partner_without_phone_is_rejected(self):
        with self._patch_bff():
            res = self.config.efn_otp_request(self.create_random_uid(), 100.0, self.partner_no_phone.id)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "no_phone")
        self.assertIn("nomor HP", res["message"])
        self.assertFalse(self.calls)

    def test_request_foreign_number_is_rejected(self):
        with self._patch_bff():
            res = self.config.efn_otp_request(self.create_random_uid(), 100.0, self.partner_foreign.id)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "foreign_phone")
        self.assertIn("+62", res["message"])
        self.assertFalse(self.calls)

    def test_request_bff_invalid_phone_is_mapped(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(
                400, {"error": {"code": "invalid_phone", "message": "Nomor tidak valid"}}
            )
            res = self.config.efn_otp_request(uuid, 100.0, self.member_a.id)
        self.assertEqual(res["error_code"], "invalid_phone")
        self.assertFalse(self._challenge(uuid))

    # ---------------------------------------------------------- SEC-07 consume
    def test_consume_member_mismatch_flags_review(self):
        """Pelanggan diganti setelah OTP: consume membawa member_ref partner FINAL;
        BFF 409 member_mismatch -> order tersimpan, ditandai review."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid, partner=self.member_a)
            self.responses[CONSUME] = BffResponse(409, {"ok": False, "reason": "member_mismatch"})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid, customer=self.member_b))
        consume = self._consume_calls()
        self.assertEqual(len(consume), 1)
        self.assertEqual(consume[0][1]["member_ref"], MEMBER_B, "member_ref dari partner final order")
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_otp_gate_state, "review")
        self.assertEqual(order.efn_otp_gate_note, "member_mismatch")
        self.assertFalse(order.efn_otp_verified)
        challenge = self._challenge(uuid)
        self.assertEqual(challenge.state, "failed")
        self.assertFalse(challenge.discount_token)

    def test_consume_amount_mismatch_flags_review(self):
        """§8.8: total final di luar toleransi nominal saat OTP -> BFF 409 amount_mismatch
        -> order tersimpan (tidak dibatalkan), ditandai review, token dibuang."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(409, {"ok": False, "reason": "amount_mismatch"})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid))
        consume = self._consume_calls()
        self.assertEqual(len(consume), 1)
        self.assertEqual(consume[0][1]["amount_total"], int(round(order.amount_total)))
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_otp_gate_state, "review")
        self.assertEqual(order.efn_otp_gate_note, "amount_mismatch")
        self.assertFalse(order.efn_otp_verified)
        challenge = self._challenge(uuid)
        self.assertEqual(challenge.state, "failed")
        self.assertFalse(challenge.discount_token)

    def test_consume_amount_mismatch_as_error_object(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(409, {"error": {"code": "amount_mismatch", "message": "x"}})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid))
        self.assertEqual(order.efn_otp_gate_note, "amount_mismatch")

    def test_consume_member_mismatch_as_error_object(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(409, {"error": {"code": "member_mismatch", "message": "x"}})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid, customer=self.member_b))
        self.assertEqual(order.efn_otp_gate_note, "member_mismatch")

    def test_final_order_without_partner_flags_review_without_consume(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(200, {"ok": True})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid, customer=False))
        self.assertFalse(self._consume_calls())
        self.assertEqual(order.efn_otp_gate_state, "review")
        self.assertEqual(order.efn_otp_gate_note, "member_missing:partner_missing")

    # ----------------------------------------------------------------- SEC-12
    def test_spoofed_reward_flags_without_otp_is_rejected(self):
        """Klien mengirim baris diskon program ber-OTP dengan is_reward_line=False dan
        tanpa reward_id: server tetap mendeteksi dari produk diskon program."""
        data = self._order_data(reward=self.reward, spoof_flags=True)
        with self._patch_bff():
            with self.assertRaises(UserError):
                self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))
        self.assertFalse(self._consume_calls())

    def test_spoofed_reward_flags_with_otp_is_consumed(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(200, {"ok": True})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid, spoof_flags=True))
        self.assertEqual(len(self._consume_calls()), 1)
        self.assertEqual(order.efn_otp_gate_state, "consumed")

    def test_spoofed_reward_on_draft_is_stripped(self):
        data = self._order_data(reward=self.reward, draft=True, spoof_flags=True)
        with self._patch_bff():
            order = self._sync(data)
        self.assertFalse(order.lines.filtered(lambda line: line.product_id == self.reward.discount_line_product_id))
        self.assertTrue(order.lines.filtered(lambda line: line.product_id == self.product))

    def test_archived_otp_program_still_gated(self):
        self.program.action_archive()
        data = self._order_data(reward=self.reward, spoof_flags=True)
        with self._patch_bff():
            with self.assertRaises(UserError):
                self._sync(data)

    # ------------------------------------------------- konfigurasi program (3b)
    def test_requires_otp_rejected_for_irrelevant_program_type(self):
        # Program dibuat valid dulu agar error yang diuji pasti dari constraint EFN.
        program = self._create_program(requires_otp=False, program_type="loyalty", pos_configs=self.config)
        with self.assertRaises(ValidationError):
            program.efn_requires_otp = True

    def test_requires_otp_allowed_for_promo_types(self):
        for program_type in ("promotion", "buy_x_get_y", "promo_code", "coupons"):
            with self.subTest(program_type=program_type):
                program = self._create_program(requires_otp=True, program_type=program_type)
                self.assertTrue(program.efn_requires_otp)

    def test_requires_otp_needs_explicit_pos_configs(self):
        with self.assertRaises(ValidationError):
            self._create_program(requires_otp=True, pos_configs=self.env["pos.config"])

    def test_requires_otp_rejected_on_kiosk_config(self):
        if "self_ordering_mode" not in self.env["pos.config"]._fields:
            self.skipTest("pos_self_order tidak terpasang")
        kiosk = self.config.copy({"name": "Kiosk Uji"})
        kiosk.self_ordering_mode = "kiosk"
        with self.assertRaises(ValidationError):
            self._create_program(requires_otp=True, pos_configs=self.config | kiosk)

    # ------------------------------------------------------------ alur lainnya
    def test_wrong_otp_returns_attempts_left(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-2", "expires_in": 180})
            self.responses[VERIFY] = BffResponse(400, {"ok": False, "reason": "invalid", "attempts_left": 2})
            self.config.efn_otp_request(uuid, 100.0, self.member_a.id)
            res = self.config.efn_otp_verify(uuid, "000000")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "invalid")
        self.assertEqual(res["attempts_left"], 2)
        self.assertNotIn("discount_token", res)
        self.assertEqual(self._challenge(uuid).state, "requested")

    def test_locked_otp_marks_challenge_failed(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-3", "expires_in": 180})
            self.responses[VERIFY] = BffResponse(400, {"ok": False, "reason": "locked", "attempts_left": 0})
            self.config.efn_otp_request(uuid, 100.0, self.member_a.id)
            res = self.config.efn_otp_verify(uuid, "111111")
        self.assertEqual(res["reason"], "locked")
        self.assertEqual(self._challenge(uuid).state, "failed")

    def test_bff_timeout_on_request_rejects_promo(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = EfnBffError("bff_timeout")
            res = self.config.efn_otp_request(uuid, 100.0, self.member_a.id)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "bff_unavailable")
        self.assertFalse(self._challenge(uuid))

    def test_bff_insecure_url_on_request_rejects_promo(self):
        """Client menolak http:// di produksi (EfnBffError) -> promo ditolak, tanpa fallback."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = EfnBffError("bff_insecure_url")
            res = self.config.efn_otp_request(uuid, 100.0, self.member_a.id)
        self.assertEqual(res["error_code"], "bff_unavailable")

    def test_bff_timeout_on_verify_rejects_promo(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-4", "expires_in": 180})
            self.responses[VERIFY] = EfnBffError("bff_timeout")
            self.config.efn_otp_request(uuid, 100.0, self.member_a.id)
            res = self.config.efn_otp_verify(uuid, "123456")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_code"], "bff_unavailable")

    def test_paid_order_with_gated_reward_without_otp_is_rejected(self):
        data = self._order_data(reward=self.reward)
        with self._patch_bff():
            with self.assertRaises(UserError):
                self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))
        self.assertFalse(self._consume_calls())

    def test_sec125_gated_reward_cannot_skip_gate_outside_sync(self):
        """Skenario C re-review G18: reward ber-OTP yang ditambahkan kasir ke draft lewat RPC tidak bisa dilunasi
        tanpa OTP, baik lewat action_pos_order_paid/write state (ditolak) maupun wizard Payment (gerbang berjalan)."""
        cashier = new_test_user(
            self.env,
            login="efn_rr_otp_kasir",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=self.env.company.id,
        )
        manager = new_test_user(
            self.env,
            login="efn_rr_otp_mgr",
            groups="base.group_user,point_of_sale.group_pos_manager",
            company_id=self.env.company.id,
        )
        with self._patch_bff():
            order = self._sync(self._order_data(draft=True))
            self.env["pos.order.line"].with_user(cashier).create(
                {
                    "order_id": order.id,
                    "product_id": self.reward.discount_line_product_id.id,
                    "qty": 1,
                    "price_unit": -10.0,
                    "price_subtotal": -10.0,
                    "price_subtotal_incl": -10.0,
                    "is_reward_line": True,
                    "reward_id": self.reward.id,
                }
            )
            order.add_payment(
                {"pos_order_id": order.id, "payment_method_id": self.cash_pm1.id, "amount": order.amount_total}
            )
            for attempt in (
                lambda: order.with_user(cashier).action_pos_order_paid(),
                lambda: order.with_user(cashier).write({"state": "paid"}),
            ):
                with self.assertRaises(UserError) as caught:
                    attempt()
                self.assertNotIsInstance(caught.exception, AccessError)
            wizard = (
                self.env["pos.make.payment"]
                .with_user(manager)
                .with_context(active_id=order.id)
                .create({"payment_method_id": self.cash_pm1.id, "amount": 0})
            )
            with self.assertRaises(UserError) as caught:
                wizard.check()
            self.assertNotIsInstance(caught.exception, AccessError)
            self.assertIn("OTP", str(caught.exception))
        self.assertEqual(order.state, "draft")
        self.assertFalse(self._consume_calls())

    def test_browser_flag_alone_is_not_trusted(self):
        data = self._order_data(reward=self.reward)
        data["efn_otp_verified"] = True  # dipalsukan dari browser
        with self._patch_bff():
            with self.assertRaises(UserError):
                self._sync(data)

    def test_token_replay_flags_order_for_review(self):
        """ADR-017 #2: consume gagal (already_used) -> order tidak dibatalkan, ditandai review."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(409, {"ok": False, "reason": "already_used"})
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid))
        self.assertEqual(order.state, "paid")
        self.assertEqual(order.efn_otp_gate_state, "review")
        self.assertEqual(order.efn_otp_gate_note, "already_used")
        self.assertFalse(order.efn_otp_verified)
        challenge = self._challenge(uuid)
        self.assertEqual(challenge.state, "failed")
        self.assertFalse(challenge.discount_token)

    def test_consume_timeout_flags_order_for_review(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = EfnBffError("bff_timeout")
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid))
        self.assertEqual(order.efn_otp_gate_state, "review")
        self.assertEqual(order.efn_otp_gate_note, "bff_timeout")

    def test_consume_tls_error_flags_order_for_review(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = EfnBffError("bff_tls_error")
            order = self._sync(self._order_data(reward=self.reward, uuid=uuid))
        self.assertEqual(order.efn_otp_gate_state, "review")
        self.assertEqual(order.efn_otp_gate_note, "bff_tls_error")

    def test_consumed_challenge_cannot_be_reused(self):
        """Setelah consume, challenge berstatus consumed sehingga sync berikutnya dengan
        uuid sama (mis. replay payload) tidak menemukan OTP terverifikasi -> ditolak."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            self.responses[CONSUME] = BffResponse(200, {"ok": True})
            self._sync(self._order_data(reward=self.reward, uuid=uuid))
            self.assertEqual(self._challenge(uuid).state, "consumed")
            found = self.env["efn.pos.otp.challenge"]._efn_find_latest(self.config, uuid, ["verified"])
        self.assertFalse(found)

    def test_token_of_other_order_cannot_be_used(self):
        verified_uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(verified_uuid)
            self.responses[CONSUME] = BffResponse(200, {"ok": True})
            with self.assertRaises(UserError):
                self._sync(self._order_data(reward=self.reward))  # uuid lain
        self.assertFalse(self._consume_calls())

    def test_draft_order_strips_unverified_reward(self):
        data = self._order_data(reward=self.reward, draft=True)
        with self._patch_bff():
            order = self._sync(data)
        self.assertEqual(order.state, "draft")
        self.assertFalse(order.lines.filtered("is_reward_line"), "reward ber-OTP harus dibuang")
        self.assertTrue(order.lines.filtered(lambda line: line.product_id == self.product))

    def test_draft_order_keeps_verified_reward_without_consuming(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
            order = self._sync(self._order_data(reward=self.reward, draft=True, uuid=uuid))
        self.assertTrue(order.lines.filtered("is_reward_line"))
        self.assertFalse(self._consume_calls())
        self.assertEqual(self._challenge(uuid).state, "verified")

    def test_non_gated_program_not_affected(self):
        with self._patch_bff():
            order = self._sync(self._order_data(reward=self.free_reward))
        self.assertEqual(order.state, "paid")
        self.assertFalse(self.calls)

    def test_gate_disabled_by_parameter(self):
        self.env["ir.config_parameter"].sudo().set_param("efn.pos.otp_required_for_promo", "False")
        with self._patch_bff():
            order = self._sync(self._order_data(reward=self.reward))
        self.assertEqual(order.state, "paid")
        self.assertFalse(self.calls)

    # ------------------------------------------------------- E-23 (purpose)
    def test_request_sends_purpose_default_promo(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
        self.assertEqual(self.calls[0][1]["purpose"], "pos_promo")
        self.assertEqual(self._challenge(uuid).purpose, "pos_promo")

    def test_invalid_purpose_is_rejected(self):
        with self._patch_bff():
            res = self.config.efn_otp_request(self.create_random_uid(), 100.0, self.member_a.id, "login")
            res2 = self.config.efn_otp_verify(self.create_random_uid(), "123456", "dsar")
        self.assertEqual(res["error_code"], "invalid_purpose")
        self.assertEqual(res2["error_code"], "invalid_purpose")
        self.assertFalse(self.calls)

    def _verify_redeem_otp_for(self, uuid):
        self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-r", "expires_in": 180})
        self.responses[VERIFY] = BffResponse(200, {"ok": True, "discount_token": DUMMY_TOKEN, "expires_in": 600})
        res = self.config.efn_otp_request(uuid, 90.0, self.member_a.id, "pos_redeem")
        self.assertTrue(res["ok"], res)
        res = self.config.efn_otp_verify(uuid, "123456", "pos_redeem")
        self.assertTrue(res["ok"], res)

    def test_redeem_challenge_does_not_open_promo_gate(self):
        """Challenge `pos_redeem` terverifikasi tidak boleh membuka reward ber-OTP."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_redeem_otp_for(uuid)
            self.assertEqual(self.calls[0][1]["purpose"], "pos_redeem")
            self.responses[CONSUME] = BffResponse(200, {"ok": True})
            with self.assertRaises(UserError):
                self._sync(self._order_data(reward=self.reward, uuid=uuid))
        self.assertFalse(self._consume_calls())
        challenge = self._challenge(uuid)
        self.assertEqual(challenge.purpose, "pos_redeem")
        self.assertEqual(challenge.state, "verified")

    def test_redeem_challenge_not_stripping_exempt_on_draft(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_redeem_otp_for(uuid)
            order = self._sync(self._order_data(reward=self.reward, draft=True, uuid=uuid))
        self.assertFalse(order.lines.filtered("is_reward_line"), "challenge tukar poin tidak membuka promo")

    def test_promo_challenge_not_found_for_redeem(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
        Challenge = self.env["efn.pos.otp.challenge"]
        self.assertTrue(Challenge._efn_find_valid_verified(self.config, uuid))
        self.assertFalse(Challenge._efn_find_valid_verified(self.config, uuid, purpose="pos_redeem"))

    def test_verify_uses_challenge_of_same_purpose(self):
        """Request promo lalu verify dengan purpose redeem -> tidak ada challenge."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-x", "expires_in": 180})
            self.config.efn_otp_request(uuid, 90.0, self.member_a.id)
            res = self.config.efn_otp_verify(uuid, "123456", "pos_redeem")
        self.assertEqual(res["error_code"], "no_challenge")

    # ------------------------------------------------------- E-34 pos_enroll
    def test_enroll_requires_age_confirmed_true(self):
        """K-18-8: tanpa `age_confirmed` True persis -> ditolak server tanpa memanggil BFF."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            for value in (False, None, "true", 1):
                with self.subTest(value=value):
                    res = self.config.efn_otp_request(uuid, 0, self.member_a.id, "pos_enroll", value)
                    self.assertFalse(res["ok"])
                    self.assertEqual(res["error_code"], "age_confirmation_required")
                    res = self.config.efn_otp_verify(uuid, "123456", "pos_enroll", value)
                    self.assertEqual(res["error_code"], "age_confirmation_required")
        self.assertEqual(self.calls, [])

    def test_enroll_flow_forwards_age_and_stores_no_age(self):
        uuid = self.create_random_uid()
        self.assertFalse(self.member_a.efn_member_locked_at)
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-e", "expires_in": 180})
            self.responses[VERIFY] = BffResponse(200, {"ok": True})
            res = self.config.efn_otp_request(uuid, 0, self.member_a.id, "pos_enroll", True)
            self.assertTrue(res["ok"], res)
            res = self.config.efn_otp_verify(uuid, "123456", "pos_enroll", True)
            self.assertTrue(res["ok"], res)
        self.assertEqual(self.calls[0][1]["purpose"], "pos_enroll")
        self.assertIs(self.calls[0][1]["age_confirmed"], True)
        self.assertEqual(
            self.calls[1][1], {"challenge_id": "chal-e", "order_ref": uuid, "code": "123456", "age_confirmed": True}
        )
        challenge = self._challenge(uuid)
        self.assertEqual(challenge.purpose, "pos_enroll")
        self.assertEqual(challenge.state, "consumed")
        self.assertFalse(challenge.sudo().discount_token)
        self.assertFalse([name for name in challenge._fields if "age" in name or "birth" in name])
        # SEC-115: pendaftaran (pos_enroll) menandai member secara permanen, tanpa PII.
        self.assertTrue(self.member_a.efn_member_locked_at)
        # Challenge pos_enroll tidak membuka promo ber-OTP.
        with self._patch_bff():
            with self.assertRaises(UserError):
                self._sync(self._order_data(reward=self.reward, uuid=uuid))

    def test_sec115_failed_request_does_not_mark(self):
        """Penanda hanya diisi bila BFF menerima permintaan OTP (nomor benar-benar dipakai sebagai member)."""
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(400, {"error": {"code": "invalid_phone"}})
            self.config.efn_otp_request(uuid, 1000, self.member_a.id)
        self.assertFalse(self.member_a.efn_member_locked_at)
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(202, {"challenge_id": "chal-p", "expires_in": 180})
            self.assertTrue(self.config.efn_otp_request(uuid, 1000, self.member_a.id)["ok"])
        self.assertTrue(self.member_a.efn_member_locked_at)

    def test_enroll_bff_age_rejection_mapped(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self.responses[REQUEST] = BffResponse(400, {"error": {"code": "age_confirmation_required"}})
            res = self.config.efn_otp_request(uuid, 0, self.member_a.id, "pos_enroll", True)
        self.assertEqual(res["error_code"], "age_confirmation_required")

    def test_promo_payload_has_no_age_field(self):
        uuid = self.create_random_uid()
        with self._patch_bff():
            self._verify_otp_for(uuid)
        self.assertNotIn("age_confirmed", self.calls[0][1])
        self.assertNotIn("age_confirmed", self.calls[1][1])
