"""G60-A: persetujuan 4-eyes pembayaran keluar, voucher, masking rekening, terbilang (tag efn)."""

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.efn_account_localization.models.account_payment import mask_account_number
from odoo.addons.efn_account_localization.models.terbilang import terbilang_id

VOUCHER_REPORT = "efn_account_localization.action_report_efn_payment_voucher"


@tagged("post_install", "-at_install", "efn")
class TestEfnPaymentApproval(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company_2 = cls.setup_other_company(name="G60 PT B")["company"]
        cls.company.efn_payment_approval_threshold = 1000.0
        own = [Command.set(cls.company.ids)]
        cls.maker = new_test_user(
            cls.env,
            login="g60_maker",
            groups="base.group_user,account.group_account_invoice",
            company_id=cls.company.id,
            company_ids=own,
        )
        cls.approver = new_test_user(
            cls.env,
            login="g60_approver",
            groups="base.group_user,account.group_account_manager",
            company_id=cls.company.id,
            company_ids=own,
        )
        cls.approver_b = new_test_user(
            cls.env,
            login="g60_approver_b",
            groups="base.group_user,account.group_account_manager",
            company_id=cls.company_2.id,
            company_ids=[Command.set(cls.company_2.ids)],
        )
        # Vendor berbadan usaha: rekening tetap plaintext di efn_pii_account (rekening orang terenkripsi).
        cls.vendor = cls.env["res.partner"].create({"name": "PT Vendor Uji G60", "is_company": True})
        cls.vendor_bank = cls.env["res.partner.bank"].create({"partner_id": cls.vendor.id, "acc_number": "1234567890"})

    # -------------------------------------------------------------------------------------------------- helper
    def _payment(self, amount, user=None, payment_type="outbound"):
        return (
            self.env["account.payment"]
            .with_user(user or self.maker)
            .with_company(self.company)
            .create(
                {
                    "payment_type": payment_type,
                    "partner_type": "supplier" if payment_type == "outbound" else "customer",
                    "partner_id": self.vendor.id,
                    "amount": amount,
                    "journal_id": self.company_data["default_journal_bank"].id,
                }
            )
        )

    def _bill(self, amount):
        return self.init_invoice("in_invoice", partner=self.vendor, amounts=[amount], taxes=[], post=True)

    def _register(self, bill, user=None):
        Register = self.env["account.payment.register"].with_user(user or self.maker).with_company(self.company)
        Register.with_context(active_model="account.move", active_ids=bill.ids).create({}).action_create_payments()
        return bill.matched_payment_ids

    # --------------------------------------------------------------------------------------------------- gerbang
    def test_below_threshold_posts_directly(self):
        payment = self._payment(500.0)
        self.assertFalse(payment.efn_needs_approval)
        payment.action_post()
        self.assertNotEqual(payment.state, "draft")
        self.assertEqual(payment.efn_approval_state, "not_required")

    def test_threshold_zero_disables_gate(self):
        self.company.efn_payment_approval_threshold = 0.0
        payment = self._payment(50000.0)
        payment.action_post()
        self.assertNotEqual(payment.state, "draft")

    def test_above_threshold_requires_approval(self):
        payment = self._payment(1500.0)
        self.assertTrue(payment.efn_needs_approval)
        with self.assertRaises(UserError):
            payment.action_post()
        payment.action_efn_request_approval()
        self.assertEqual(payment.efn_approval_state, "to_approve")
        self.assertEqual(payment.efn_approval_requested_by_id, self.maker)
        with self.assertRaises(AccessError):
            payment.with_user(self.maker).action_efn_approve()
        payment.with_user(self.approver).action_efn_approve()
        self.assertEqual(payment.efn_approval_state, "approved")
        self.assertEqual(payment.efn_approved_by_id, self.approver)
        self.assertNotEqual(payment.state, "draft")
        self.assertEqual(payment.move_id.state, "posted")

    def test_four_eyes_creator_cannot_approve(self):
        payment = self._payment(2000.0, user=self.approver)
        payment.action_efn_request_approval()
        with self.assertRaises(AccessError):
            payment.with_user(self.approver).action_efn_approve()
        with self.assertRaises(AccessError):
            payment.with_user(self.approver).action_efn_reject()
        self.assertEqual(payment.efn_approval_state, "to_approve")

    def test_reject_stays_draft_and_can_rerequest(self):
        payment = self._payment(1500.0)
        payment.action_efn_request_approval()
        payment.with_user(self.approver).action_efn_reject()
        self.assertEqual(payment.state, "draft")
        self.assertEqual(payment.efn_approval_state, "rejected")
        with self.assertRaises(UserError):
            payment.action_post()
        payment.action_efn_request_approval()
        self.assertEqual(payment.efn_approval_state, "to_approve")

    def test_reset_to_draft_voids_approval(self):
        payment = self._payment(1500.0)
        payment.action_efn_request_approval()
        payment.with_user(self.approver).action_efn_approve()
        payment.with_user(self.approver).action_draft()
        self.assertEqual(payment.efn_approval_state, "not_required")
        with self.assertRaises(UserError):
            payment.action_post()

    def test_inbound_not_affected(self):
        payment = self._payment(50000.0, payment_type="inbound")
        self.assertFalse(payment.efn_needs_approval)
        payment.action_post()
        self.assertNotEqual(payment.state, "draft")

    def test_request_below_threshold_refused(self):
        with self.assertRaises(UserError):
            self._payment(10.0).action_efn_request_approval()

    def test_other_company_manager_cannot_approve(self):
        payment = self._payment(1500.0)
        payment.action_efn_request_approval()
        with self.assertRaises(AccessError):
            payment.with_user(self.approver_b).with_company(self.company_2).action_efn_approve()
        self.assertEqual(payment.efn_approval_state, "to_approve")

    def test_register_payment_holds_then_reconciles_on_approval(self):
        bill = self._bill(2000.0)
        payment = self._register(bill)
        self.assertEqual(len(payment), 1)
        self.assertEqual(payment.state, "draft")
        self.assertEqual(payment.efn_approval_state, "to_approve")
        self.assertEqual(bill.payment_state, "not_paid")
        payment.with_user(self.approver).action_efn_approve()
        self.assertIn(bill.payment_state, ("paid", "in_payment"))

    # ------------------------------------------------------------------------------------ SEC-G60-01 / SEC-G60-02
    def test_rpc_write_approval_fields_refused(self):
        payment = self._payment(1500.0)
        for vals in (
            {"efn_approval_state": "approved", "efn_approved_by_id": self.approver.id},
            {"efn_approval_requested_by_id": self.approver.id},
            {"efn_approved_at": "2026-01-01 00:00:00"},
        ):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                payment.write(vals)
        with self.assertRaises(AccessError):
            payment.sudo().write({"efn_approval_state": "approved"})  # sudo tanpa konteks sistem tetap ditolak
        self.assertEqual(payment.efn_approval_state, "not_required")
        with self.assertRaises(UserError):
            payment.action_post()

    def test_create_cannot_preset_approval(self):
        Payment = self.env["account.payment"].with_user(self.maker).with_company(self.company)
        vals = {
            "payment_type": "outbound",
            "partner_type": "supplier",
            "partner_id": self.vendor.id,
            "amount": 1500.0,
            "journal_id": self.company_data["default_journal_bank"].id,
        }
        preset = Payment.create({**vals, "efn_approval_state": "approved", "efn_approved_by_id": self.approver.id})
        by_context = Payment.with_context(
            default_efn_approval_state="approved", default_efn_approved_by_id=self.approver.id
        ).create(vals)
        for payment in preset | by_context:
            self.assertEqual(payment.efn_approval_state, "not_required")
            self.assertFalse(payment.efn_approved_by_id)
            with self.assertRaises(UserError):
                payment.action_post()

    def test_transfer_account_spoof_still_gated(self):
        transfer = self.company.transfer_account_id
        if not transfer:
            self.skipTest("chart uji tanpa akun transfer")
        payment = self._payment(1500.0)
        payment.destination_account_id = transfer
        self.assertTrue(payment.efn_needs_approval, "vendor + akun transfer bukan transfer internal")
        with self.assertRaises(UserError):
            payment.action_post()

    def test_post_requires_independent_approver(self):
        payment = self._payment(1500.0)
        # Data lama/rusak: approved oleh pembuat sendiri -> action_post tetap menolak (defense in depth).
        payment._efn_system_write({"efn_approval_state": "approved", "efn_approved_by_id": self.maker.id})
        with self.assertRaises(UserError):
            payment.action_post()

    def test_edit_then_approve_blocked(self):
        payment = self._payment(1500.0)
        payment.action_efn_request_approval()
        other_bank = self.env["res.partner.bank"].create({"partner_id": self.vendor.id, "acc_number": "999988887777"})
        for vals in ({"amount": 1600.0}, {"partner_bank_id": other_bank.id}):
            with self.subTest(vals=vals):
                payment.with_user(self.approver).write(vals)
                self.assertEqual(payment.efn_approval_state, "not_required", "edit isi menggugurkan persetujuan")
                with self.assertRaises(UserError):
                    payment.with_user(self.approver).action_efn_approve()
                payment.action_efn_request_approval()
        # Edit tanpa perubahan nilai tidak menggugurkan.
        payment.with_user(self.approver).write({"amount": payment.amount})
        self.assertEqual(payment.efn_approval_state, "to_approve")
        payment.with_user(self.approver).action_efn_approve()
        self.assertEqual(payment.efn_approval_state, "approved")

    # --------------------------------------------------------------------------------------------------- voucher
    def test_voucher_rows_balanced_with_bill_reference(self):
        bill = self._bill(600.0)
        payment = self._register(bill)
        self.assertNotEqual(payment.state, "draft", "di bawah ambang: wizard langsung mem-post")
        rows = payment._efn_voucher_rows()
        self.assertTrue(rows)
        self.assertAlmostEqual(sum(r["debit"] for r in rows), sum(r["credit"] for r in rows))
        self.assertAlmostEqual(sum(r["debit"] for r in rows), 600.0)
        self.assertIn(bill.name, [r["doc_ap"] for r in rows])

    def test_recipient_account_masked_except_manager(self):
        payment = self._payment(100.0)
        payment.partner_bank_id = self.vendor_bank
        self.assertEqual(payment.with_user(self.maker)._efn_voucher_recipient_bank()["acc_number"], "****7890")
        self.assertEqual(payment.with_user(self.approver)._efn_voucher_recipient_bank()["acc_number"], "1234567890")
        html, _fmt = self.env["ir.actions.report"].with_user(self.maker)._render_qweb_html(VOUCHER_REPORT, payment.ids)
        html = html.decode()
        self.assertIn("****7890", html)
        self.assertNotIn("1234567890", html)
        self.assertIn("PAYMENT VOUCHER", html)

    def test_unapproved_voucher_is_marked(self):
        payment = self._payment(1500.0)
        payment.action_efn_request_approval()
        html, _fmt = self.env["ir.actions.report"]._render_qweb_html(VOUCHER_REPORT, payment.ids)
        self.assertIn("BELUM DISETUJUI", html.decode())

    def test_mask_and_terbilang_helpers(self):
        self.assertEqual(mask_account_number("1234 5678 90"), "****7890")
        self.assertEqual(mask_account_number("123"), "****")
        self.assertEqual(mask_account_number(""), "")
        self.assertEqual(terbilang_id(0), "Nol Rupiah")
        self.assertEqual(terbilang_id(1_000_000), "Satu Juta Rupiah")
        self.assertEqual(
            terbilang_id(1_234_567.89),
            "Satu Juta Dua Ratus Tiga Puluh Empat Ribu Lima Ratus Enam Puluh Delapan Rupiah",
        )
        self.assertEqual(terbilang_id(270_045), "Dua Ratus Tujuh Puluh Ribu Empat Puluh Lima Rupiah")
