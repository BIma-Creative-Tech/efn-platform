"""Uang muka karyawan: pengajuan -> setuju (user lain) -> cair -> realisasi (beban + pihak ketiga ber-nota) ->
jurnal -> kembalikan sisa -> settle. Empat mata, fail-closed akun/jurnal, status hanya lewat tombol, laporan."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import tagged

from .common import EfnPettyCashCommon

WIZARD = "efn.petty.cash.report.wizard"


@tagged("post_install", "-at_install", "efn")
class TestEfnPettyCashRequestFlow(EfnPettyCashCommon):
    def test_full_cycle_journal_entries(self):
        request = self._new_request(1_000_000)
        self.assertEqual(request.maker_id, self.requester)
        self.assertEqual(request.holder_partner_id, self.requester.partner_id)
        request.action_submit()
        self.assertEqual(request.state, "to_approve")
        request.with_user(self.approver).action_approve()
        self.assertEqual((request.state, request.approved_by_id), ("approved", self.approver))

        request.with_user(self.finance).action_disburse()
        self.assertEqual(request.state, "disbursed")
        move = request.sudo().disburse_move_id
        self.assertEqual(move.state, "posted")
        advance_line = move.line_ids.filtered(lambda ln: ln.account_id == self.advance_ca)
        bank_line = move.line_ids - advance_line
        self.assertEqual((advance_line.debit, advance_line.partner_id), (1_000_000, self.requester.partner_id))
        self.assertEqual(bank_line.credit, 1_000_000)
        self.assertEqual(bank_line.account_id, self.bank_journal.default_account_id)
        self.assertEqual(request.amount_outstanding, 1_000_000)
        self.assertTrue(request.realization_deadline)

        realization = self._realization(
            {"request_id": request.id},
            [
                self._expense_line(600_000),
                {
                    "line_type": "third_party",
                    "name": "Bahan baku darurat",
                    "partner_id": self.vendor.id,
                    "account_id": self.expense.id,
                    "price_unit": 300_000,
                    "attachment_ids": [Command.set(self._attachment(self.requester).ids)],
                },
            ],
        )
        self.assertEqual(realization.holder_user_id, self.requester)
        realization.action_submit()
        realization.with_user(self.finance).action_post()
        self.assertEqual((realization.state, realization.posted_by_id), ("posted", self.finance))
        self.assertEqual(request.state, "in_realization")

        moves = realization.sudo().bill_ids
        bill = moves.filtered(lambda m: m.move_type == "in_invoice")
        self.assertEqual((bill.state, bill.partner_id, bill.amount_total), ("posted", self.vendor, 300_000))
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        expense_lines = moves.line_ids.filtered(lambda ln: ln.account_id == self.expense and ln.move_type == "entry")
        self.assertEqual(sum(expense_lines.mapped("debit")), 600_000)
        self.assertAlmostEqual(request.amount_outstanding, 100_000, 2)
        self.assertAlmostEqual(self._advance_balance(self.advance_ca, self.requester.partner_id), 100_000, 2)

        request.with_user(self.finance).action_return_balance()
        self.assertAlmostEqual(request.amount_outstanding, 0.0, 2)
        request.with_user(self.finance).action_settle()
        self.assertEqual(request.state, "settled")
        advance_lines = request.sudo().move_ids.line_ids.filtered(lambda ln: ln.account_id == self.advance_ca)
        self.assertTrue(advance_lines)
        self.assertTrue(all(advance_lines.mapped("reconciled")))

    def test_requester_cannot_approve_own_request(self):
        request = self._new_request(500_000, user=self.finance_approver)
        request.with_user(self.finance_approver).action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.finance_approver).action_approve()
        self.assertEqual(request.state, "to_approve")

    def test_editor_cannot_approve(self):
        request = self._new_request(500_000)
        # Penyetuju yang ikut menyunting draft menjadi penyunting -> tidak boleh memutuskan.
        request.with_user(self.finance_approver).write({"purpose": "Diubah penyetuju"})
        request.action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.finance_approver).action_approve()
        request.with_user(self.approver).action_approve()
        self.assertEqual(request.state, "approved")

    def test_disburser_cannot_be_requester(self):
        request = self._new_request(500_000, user=self.finance_approver)
        request.with_user(self.finance_approver).action_submit()
        request.with_user(self.approver).action_approve()
        with self.assertRaises(UserError):
            request.with_user(self.finance_approver).action_disburse()
        self.assertFalse(request.sudo().disburse_move_id)

    def test_poster_cannot_be_realization_maker(self):
        request = self._disbursed_request(500_000)
        lines = [self._expense_line(100_000)]
        realization = self._realization({"request_id": request.id}, lines, user=self.finance)
        realization.with_user(self.finance).action_submit()
        with self.assertRaises(UserError):
            realization.with_user(self.finance).action_post()
        self.assertEqual(realization.state, "submitted")

    def test_plain_user_cannot_approve_or_disburse(self):
        request = self._new_request(500_000)
        request.action_submit()
        with self.assertRaises(AccessError):
            request.with_user(self.requester).action_approve()
        request.with_user(self.approver).action_approve()
        with self.assertRaises(AccessError):
            request.with_user(self.requester).action_disburse()
        with self.assertRaises(AccessError):
            request.with_user(self.approver).action_disburse()

    def test_missing_advance_account_fails_closed(self):
        bare_type = (
            self.env["efn.petty.cash.type"]
            .sudo()
            .create(
                {
                    "name": "Tanpa Akun",
                    "code": "NA",
                    "kind": "cash_advance",
                    "company_id": self.company_a.id,
                    "bank_out_journal_id": self.bank_journal.id,
                }
            )
        )
        request = self._new_request(200_000, advance_type_id=bare_type.id)
        request.action_submit()
        request.with_user(self.approver).action_approve()
        with self.assertRaisesRegex(UserError, "Akun uang muka"):
            request.with_user(self.finance).action_disburse()
        self.assertEqual(request.state, "approved")
        self.assertFalse(request.sudo().move_ids)

    def test_missing_journal_fails_closed(self):
        no_journal = (
            self.env["efn.petty.cash.type"]
            .sudo()
            .create(
                {
                    "name": "Tanpa Jurnal",
                    "code": "NJ",
                    "kind": "cash_advance",
                    "company_id": self.company_a.id,
                    "advance_account_id": self.advance_ca.id,
                }
            )
        )
        request = self._new_request(200_000, advance_type_id=no_journal.id)
        request.action_submit()
        request.with_user(self.approver).action_approve()
        with self.assertRaisesRegex(UserError, "Jurnal bank keluar"):
            request.with_user(self.finance).action_disburse()

    def test_third_party_requires_attachment(self):
        request = self._disbursed_request(500_000)
        line = {
            "line_type": "third_party",
            "name": "Tanpa nota",
            "partner_id": self.vendor.id,
            "account_id": self.expense.id,
            "price_unit": 100_000,
        }
        with self.assertRaises(UserError):
            self._realization({"request_id": request.id}, [line])

    def test_workflow_fields_not_writable_directly(self):
        request = self._new_request(500_000)
        with self.assertRaises(AccessError):
            request.write({"state": "approved"})
        with self.assertRaises(AccessError):
            request.with_user(self.approver).write({"approved_by_id": self.approver.id})
        request.action_submit()
        with self.assertRaises(UserError):
            request.write({"amount_requested": 9_000_000})

    def test_limit_block(self):
        self.type_ca.sudo().write({"limit_enforcement": "block", "limit_per_request": 750_000})
        request = self._new_request(1_000_000)
        with self.assertRaisesRegex(UserError, "plafon"):
            request.action_submit()
        self.assertEqual(request.state, "draft")

    def test_overdue_reminder_cron(self):
        request = self._disbursed_request(500_000)
        request.sudo().write({"realization_deadline": fields.Date.today() - timedelta(days=1)})
        self.assertTrue(request.is_overdue)
        Request = self.env["efn.petty.cash.request"].sudo()
        self.assertIn(request, Request.search([("is_overdue", "=", True)]))
        Request._cron_efn_petty_cash_reminder()
        self.assertTrue(request.sudo().activity_ids.filtered(lambda a: a.user_id == self.requester))

    def test_report_outstanding_and_aging(self):
        request = self._disbursed_request(400_000)
        Wizard = self.env[WIZARD].with_user(self.finance)
        for kind in ("outstanding", "aging", "statement"):
            wizard = Wizard.create({"report_kind": kind})
            self.assertTrue(wizard._efn_data()["rows"], kind)
            action = wizard.action_xlsx()
            self.assertEqual(action["type"], "ir.actions.act_url")
            self.assertTrue(wizard.xlsx_file)
        rows = Wizard.create({"report_kind": "outstanding"})._efn_data()["rows"]
        self.assertIn([self.requester.partner_id.display_name, request.name], [row[:2] for row in rows])
        with self.assertRaises(AccessError):
            self.env[WIZARD].with_user(self.requester).create({"report_kind": "aging"})
