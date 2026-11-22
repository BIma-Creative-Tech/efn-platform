"""Penjaga agar rekonsiliasi tidak hilang diam-diam (port test_reconciliation_guards.py sumber, tanpa data tenant)."""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import EfnReconcileCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnReconciliationGuards(EfnReconcileCommon):
    def _bill(self, amount):
        return self._invoice(amount, move_type="in_invoice")

    def _register(self, bill):
        wizard = (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=bill.ids)
            .create({"journal_id": self.bank_journal.id})
        )
        wizard.action_create_payments()
        return self.env["account.payment"].search([("partner_id", "=", self.partner.id)], order="id desc", limit=1)

    def _payment_vals(self, amount=250.0):
        return {
            "partner_id": self.partner.id,
            "partner_type": "supplier",
            "payment_type": "outbound",
            "amount": amount,
            "date": date(2026, 7, 14),
            "journal_id": self.bank_journal.id,
        }

    # ------------------------------------------------------------------ reset ke draft
    def test_reset_to_draft_releases_the_bill(self):
        bill = self._bill(1000.0)
        payment = self._register(bill)
        self.assertEqual(bill.payment_state, "paid")
        payment.move_id.button_draft()
        self.assertEqual(payment.move_id.state, "draft")
        self.assertFalse(bill.line_ids.filtered(lambda line: line.matched_debit_ids or line.matched_credit_ids))
        self.assertNotEqual(bill.payment_state, "paid")
        payable = bill.line_ids.filtered(lambda line: line.account_id.account_type == "liability_payable")
        self.assertAlmostEqual(sum(payable.mapped("amount_residual")), -1000.0, places=2)

    def test_reset_to_draft_names_what_it_released(self):
        bill = self._bill(500.0)
        payment = self._register(bill)
        before = len(payment.move_id.message_ids)
        payment.move_id.button_draft()
        new_messages = payment.move_id.message_ids[: len(payment.move_id.message_ids) - before]
        self.assertTrue(any(bill.name in (m.body or "") for m in new_messages))

    def test_reset_to_draft_without_reconciliation_is_untouched(self):
        bill = self._bill(300.0)
        bill.button_draft()
        self.assertEqual(bill.state, "draft")

    # ------------------------------------------------------------------ perubahan struktural
    def _other_payable(self):
        return self.env["account.account"].create(
            {"code": "EFNAP2", "name": "Hutang lain uji", "account_type": "liability_payable"}
        )

    def test_cannot_move_a_matched_line_to_another_account(self):
        bill = self._bill(700.0)
        payment = self._register(bill)
        line = payment.move_id.line_ids.filtered(lambda aml: aml.account_id.account_type == "liability_payable")
        with self.assertRaisesRegex(UserError, "masih direkonsiliasi"):
            line.write({"account_id": self._other_payable().id})

    def test_guard_escape_context_ignored_without_superuser(self):
        bill = self._bill(710.0)
        payment = self._register(bill)
        line = payment.move_id.line_ids.filtered(lambda aml: aml.account_id.account_type == "liability_payable")
        with self.assertRaisesRegex(UserError, "masih direkonsiliasi"):
            line.with_context(skip_reconciliation_guard=True).write({"account_id": self._other_payable().id})

    def test_no_op_write_on_a_matched_line_is_allowed(self):
        bill = self._bill(720.0)
        payment = self._register(bill)
        line = payment.move_id.line_ids.filtered(lambda aml: aml.account_id.account_type == "liability_payable")
        line.write({"account_id": line.account_id.id, "name": "relabel"})
        self.assertEqual(line.name, "relabel")

    def test_unmatched_line_can_still_be_moved(self):
        exp_a = self.company_data["default_account_expense"]
        exp_b = self.env["account.account"].create(
            {"code": "EFNEX2", "name": "Beban lain uji", "account_type": "expense"}
        )
        move = self.env["account.move"].create(
            {
                "journal_id": self.misc_journal.id,
                "date": date(2026, 7, 1),
                "line_ids": [
                    (0, 0, {"account_id": exp_a.id, "name": "x", "debit": 700.0, "credit": 0.0}),
                    (0, 0, {"account_id": exp_b.id, "name": "y", "debit": 0.0, "credit": 700.0}),
                ],
            }
        )
        line = move.line_ids.filtered(lambda aml: aml.account_id == exp_a)
        line.write({"account_id": exp_b.id})
        self.assertEqual(line.account_id, exp_b)

    # ------------------------------------------------------------------ pembayaran ganda
    def test_duplicate_payment_is_refused(self):
        self.env["account.payment"].create(self._payment_vals()).action_post()
        second = self.env["account.payment"].create(self._payment_vals())
        with self.assertRaises(UserError):
            second.action_post()

    def test_duplicate_payment_posts_once_confirmed(self):
        self.env["account.payment"].create(self._payment_vals()).action_post()
        second = self.env["account.payment"].create(dict(self._payment_vals(), efn_duplicate_checked=True))
        second.action_post()
        self.assertIn(second.state, ("in_process", "paid"))

    def test_different_amount_is_not_a_duplicate(self):
        self.env["account.payment"].create(self._payment_vals(250.0)).action_post()
        other = self.env["account.payment"].create(self._payment_vals(251.0))
        other.action_post()
        self.assertIn(other.state, ("in_process", "paid"))

    # ------------------------------------------------------------------ belum teralokasi
    def test_unapplied_flag_tracks_application(self):
        bill = self._bill(1000.0)
        applied = self._register(bill)
        self.assertFalse(applied.efn_is_unapplied)
        standalone = self.env["account.payment"].create(self._payment_vals(999.0))
        standalone.action_post()
        self.assertTrue(standalone.efn_is_unapplied)
