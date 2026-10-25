"""G26 transfer bank + bukti (docs/04 §17, L-95..L-100). Tag efn. Angka/nomor rekening = data uji."""

import base64
import hashlib
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_online_order.tests.common import PICKUP_CODE, EfnOnlineCommon

JPEG = b"\xff\xd8\xff\xe0" + b"bukti-uji" * 32


def proof_payload(image=JPEG, **extra):
    proof_id = str(uuid.uuid4())
    data = {
        "idempotency_key": f"online:transfer:{proof_id}",
        "proof_id": proof_id,
        "transfer_ref": "QRST2345",
        "image_b64": base64.b64encode(image).decode("ascii"),
        "sha256": hashlib.sha256(image).hexdigest(),
        "duplicate_suspect": False,
        "uploaded_at": fields.Datetime.now().replace(microsecond=0).isoformat() + "Z",
    }
    data.update(extra)
    return data


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineTransfer(EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        bank = cls.env["res.bank"].create({"name": "Bank Uji"})
        account = cls.env["res.partner.bank"].create(
            {
                "acc_number": "1234567890",
                "partner_id": cls.company.partner_id.id,
                "bank_id": bank.id,
                "acc_holder_name": "PT Uji Online",
                "company_id": cls.company.id,
            }
        )
        cls.transfer_journal = cls.env["account.journal"].create(
            {
                "name": "Bank Transfer Online",
                "type": "bank",
                "code": "BTRF",
                "company_id": cls.company.id,
                "bank_account_id": account.id,
            }
        )
        cls.channel.write(
            {
                "payment_modes": "qris,bank_transfer",
                "efn_transfer_journal_id": cls.transfer_journal.id,
            }
        )
        cls.verifier = new_test_user(
            cls.env,
            login="efn_verifier_uji",
            groups="base.group_user,efn_online_transfer.group_payment_verifier",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.outsider = new_test_user(
            cls.env,
            login="efn_bukan_verifier",
            groups="base.group_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    # ------------------------------------------------------------------ helpers
    def place_transfer(self, qty=1):
        lines = [{"product_id": self.p_rice.id, "qty": qty}]
        part = str(uuid.uuid4())
        payload = {
            "idempotency_key": f"online:place:{part}",
            "platform_order_ref": part,
            "checkout_ref": str(uuid.uuid4()),
            "outlet_code": "TOKO-T1",
            "fulfilment": "pickup",
            "lines": lines,
            "quote_hash": self.quote(lines)["quote_hash"],
            "pickup_code": PICKUP_CODE,
            "payment_mode": "bank_transfer",
        }
        return part, payload, self.order_api().efn_online_place("TST", payload)

    def submit(self, part, **extra):
        payload = proof_payload(**extra)
        return payload, self.order_api().efn_online_transfer_submit("TST", part, payload)

    def proof(self, part):
        return self.env["efn.online.transfer.proof"].sudo().search([("order_id", "=", self.record(part).id)])

    def decide(self, proof, user=None, **kwargs):
        return proof.with_user(user or self.verifier)._efn_decide(**kwargs)

    def approve(self, proof, amount=None, user=None):
        return self.decide(
            proof,
            user=user,
            decision="approve",
            bank_date=fields.Date.today(),
            bank_amount=proof.amount_due if amount is None else amount,
            bank_ref="MUT-0001",
        )

    # -------------------------------------------------------------- konfigurasi
    def test_channel_requires_complete_transfer_journal(self):
        with self.assertRaises(ValidationError):
            self.channel.write({"efn_transfer_journal_id": False})
        with self.assertRaises(ValidationError):
            self.channel_2.write({"payment_modes": "qris,bank_transfer"})

    def test_counter_frozen_by_default(self):
        """L-95: kanal tidak bisa memilih 'counter' selama kill switch mati."""
        with self.assertRaises(ValidationError):
            self.channel.write({"payment_modes": "qris,counter"})

    def test_new_modes_parse_and_catalog(self):
        self.channel.write({"payment_modes": "ewallet,va,qris,bank_transfer"})
        block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertEqual(block["payment_modes"], ["qris", "va", "ewallet", "bank_transfer"])

    # -------------------------------------------------------------------- place
    def test_place_returns_company_account(self):
        part, payload, result = self.place_transfer()
        self.assertEqual(
            result["transfer_account"],
            {
                "bank_name": "Bank Uji",
                "account_number": "1234567890",
                "account_holder": "PT Uji Online",
            },
        )
        order = self.record(part)
        self.assertEqual(order.payment_mode, "bank_transfer")
        self.assertEqual(order.state, "placed")
        replay = self.order_api().efn_online_place("TST", payload)
        self.assertEqual(replay, result)
        with self.assertRaisesRegex(Exception, "efn_online:idempotency_conflict"):
            self.order_api().efn_online_place("TST", dict(payload, payment_mode="qris"))

    def test_qris_place_unchanged(self):
        lines = [{"product_id": self.p_rice.id, "qty": 1}]
        part = str(uuid.uuid4())
        result = self.order_api().efn_online_place(
            "TST",
            {
                "idempotency_key": f"online:place:{part}",
                "platform_order_ref": part,
                "checkout_ref": str(uuid.uuid4()),
                "outlet_code": "TOKO-T1",
                "fulfilment": "pickup",
                "lines": lines,
                "quote_hash": self.quote(lines)["quote_hash"],
                "pickup_code": PICKUP_CODE,
            },
        )
        self.assertNotIn("transfer_account", result)

    def test_bff_mark_paid_rejected_for_transfer(self):
        part, _payload, _result = self.place_transfer()
        with self.assertRaisesRegex(Exception, "efn_online:invalid_state"):
            self.pay(part)

    # ------------------------------------------------------------------- submit
    def test_submit_stores_private_attachment_and_is_idempotent(self):
        part, _payload, _result = self.place_transfer()
        payload, result = self.submit(part)
        self.assertEqual(result, {"state": "awaiting_verification"})
        proof = self.proof(part)
        self.assertEqual(len(proof), 1)
        self.assertEqual(proof.transfer_ref, "QRST2345")
        self.assertEqual(proof.amount_due, self.record(part).amount_due)
        self.assertEqual(proof.account_label, "Bank Uji 1234567890")
        self.assertFalse(proof.attachment_id.public)
        self.assertEqual(proof.attachment_id.res_model, "efn.online.transfer.proof")
        self.assertEqual(proof.attachment_id.raw, JPEG)
        again = self.order_api().efn_online_transfer_submit("TST", part, payload)
        self.assertEqual(again, result)
        with self.assertRaisesRegex(Exception, "efn_online:idempotency_conflict"):
            self.order_api().efn_online_transfer_submit("TST", part, dict(payload, duplicate_suspect=True))
        with self.assertRaisesRegex(Exception, "efn_online:invalid_state"):
            self.submit(part)  # bukti lain saat masih ada bukti aktif

    def test_submit_validation(self):
        part, _payload, _result = self.place_transfer()
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
        cases = (
            {"image": png},
            {"sha256": "0" * 64},
            {"image_b64": "@@bukan-base64@@"},
            {"transfer_ref": "abc"},
            {"proof_id": "bukan-uuid"},
        )
        for extra in cases:
            with (
                self.subTest(extra=list(extra)),
                self.assertRaisesRegex(Exception, "efn_online:invalid_input"),
            ):
                self.submit(part, **extra)
        with self.assertRaisesRegex(Exception, "efn_online:invalid_input"):
            payload = proof_payload()
            payload["extra"] = 1
            self.order_api().efn_online_transfer_submit("TST", part, payload)
        self.assertFalse(self.proof(part))

    def test_submit_rejected_for_qris_order_and_foreign_channel(self):
        part, _payload, _result = self.place()
        with self.assertRaisesRegex(Exception, "efn_online:invalid_state"):
            self.submit(part)
        transfer_part, _p, _r = self.place_transfer()
        with self.assertRaises(AccessError):
            self.env["efn.online.order"].with_user(self.tech_user_2).efn_online_transfer_submit(
                "TST", transfer_part, proof_payload()
            )

    def test_submit_after_expiry_rejected(self):
        part, _payload, _result = self.place_transfer()
        self.record(part).write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        with self.assertRaisesRegex(Exception, "efn_online:invalid_state"):
            self.submit(part)

    # ---------------------------------------------------------------- keputusan
    def test_approve_confirms_order_and_pays_in_transfer_journal(self):
        part, _payload, _result = self.place_transfer()
        self.submit(part)
        proof = self.proof(part)
        with self.assertRaises(UserError):
            self.approve(proof, amount=proof.amount_due - 1)
        result = self.approve(proof)
        order = self.record(part)
        self.assertEqual(result["order_state"], "confirmed")
        self.assertEqual(order.state, "confirmed")
        self.assertEqual(proof.state, "approved")
        self.assertEqual(proof.decided_by, self.verifier)
        self.assertEqual(order.payment_ref, proof.proof_id)
        invoice = order._efn_posted_invoice()
        self.assertTrue(invoice)
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        payments = self.env["account.payment"].sudo().search([("journal_id", "=", self.transfer_journal.id)])
        self.assertTrue(payments)
        paid = order.event_ids.filtered(lambda e: e.event == "paid")._efn_payload()
        self.assertEqual(paid["payment"]["method"], "bank_transfer")
        self.assertNotIn("decided_by", str(paid))
        with self.assertRaises(UserError):
            self.approve(proof)  # keputusan kedua ditolak

    def test_reject_emits_event_and_allows_new_proof(self):
        part, _payload, _result = self.place_transfer()
        self.submit(part)
        proof = self.proof(part)
        with self.assertRaises(UserError):
            self.decide(proof, decision="reject", reason="lainnya")
        self.decide(
            proof,
            decision="reject",
            reason="unreadable",
            note="buram, nomor tidak terbaca",
        )
        order = self.record(part)
        self.assertEqual(order.state, "placed")
        self.assertEqual(proof.state, "rejected")
        event = order.event_ids.filtered(lambda e: e.event == "transfer_rejected")
        self.assertEqual(len(event), 1)
        payload = event._efn_payload()
        self.assertEqual(payload["transfer"], {"reason": "unreadable"})
        self.assertNotIn("buram", str(payload))
        self.submit(part)
        self.assertEqual(proof.state, "superseded")
        self.assertEqual(
            len(self.proof(part).filtered(lambda p: p.state == "awaiting_verification")),
            1,
        )

    def test_only_verifier_decides(self):
        part, _payload, _result = self.place_transfer()
        self.submit(part)
        proof = self.proof(part)
        with self.assertRaises(AccessError):
            self.approve(proof, user=self.outsider)
        with self.assertRaises(AccessError):
            proof.with_user(self.outsider).read(["amount_due"])
        self.assertEqual(proof.state, "awaiting_verification")
        verifier_view = proof.with_user(self.verifier)
        self.assertEqual(verifier_view.read(["transfer_ref"])[0]["transfer_ref"], "QRST2345")
        self.assertTrue(verifier_view.image)

    def test_cancel_expires_proof_and_late_approval_goes_to_review(self):
        part, _payload, _result = self.place_transfer()
        self.submit(part)
        proof = self.proof(part)
        self.order_api().efn_online_cancel("TST", part, "payment_expired", f"online:cancel:{part}")
        self.assertEqual(proof.state, "expired")
        with self.assertRaises(UserError):
            self.decide(proof, decision="reject", reason="not_received")
        result = self.approve(proof)
        order = self.record(part)
        self.assertEqual(result["order_state"], "review")
        self.assertEqual(order.review_reason, "transfer_paid_after_cancel")

    def test_sod_rules_and_audit(self):
        rules = (
            self.env["efn.sod.rule"]
            .sudo()
            .with_context(active_test=False)
            .search([("code", "in", ("SOD-PAY-01", "SOD-PAY-02"))])
        )
        self.assertEqual(sorted(rules.mapped("code")), ["SOD-PAY-01", "SOD-PAY-02"])
        rule = self.env.ref("efn_online_transfer.audit_rule_efn_online_transfer_proof")
        self.assertIn("note", rule.excluded_field_ids.mapped("name"))
