"""Kas kecil outlet (imprest): dana awal -> realisasi -> belum diisi ulang -> pengisian ulang -> saldo kembali ke
plafon; plafon imprest, batas per transaksi, saldo, tutup dana."""

from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tests import tagged

from .common import EfnPettyCashCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPettyCashFund(EfnPettyCashCommon):
    @property
    def fresh_fund(self):
        """Saldo/belum-diisi-ulang = compute non-stored: baca ulang dari DB di setiap assert."""
        self.env.invalidate_all()
        return self.fund

    def _fund_request(self, amount, user=None):
        Request = self.env["efn.petty.cash.request"].with_user(user or self.custodian)
        return Request.create({"fund_id": self.fund.id, "amount_requested": amount, "purpose": "Dana awal"})

    def _approve_disburse(self, request):
        request.with_user(request.maker_id).action_submit()
        request.with_user(self.approver).action_approve()
        request.with_user(self.finance).action_disburse()

    def _funded(self):
        request = self._fund_request(5_000_000)
        self._approve_disburse(request)
        return request

    def _fund_realization(self, amounts, submit=True, post=True):
        lines = [self._expense_line(amount, name=f"Belanja {i}") for i, amount in enumerate(amounts)]
        realization = self._realization({"fund_id": self.fund.id}, lines, user=self.custodian)
        if submit:
            realization.action_submit()
        if submit and post:
            realization.with_user(self.finance).action_post()
        return realization

    def _replenish(self):
        action = self.fund.with_user(self.custodian).action_request_replenishment()
        return self.env["efn.petty.cash.request"].sudo().browse(action["res_id"])

    def test_imprest_cycle(self):
        initial = self._funded()
        self.assertEqual(initial.holder_partner_id, self.custodian.partner_id)
        self.assertEqual(initial.advance_type_id, self.type_pc)
        self.assertEqual(initial.sudo().disburse_move_id.efn_petty_cash_fund_id, self.fund)
        self.assertEqual(initial.state, "disbursed")
        self.assertFalse(initial.realization_deadline)
        self.assertEqual(self.fresh_fund.balance, 5_000_000)

        realization = self._fund_realization([400_000, 300_000])
        self.assertEqual(realization.state, "posted")
        self.assertEqual(realization.holder_partner_id, self.custodian.partner_id)
        self.assertEqual(self.fresh_fund.balance, 4_300_000)
        self.assertEqual(self.fresh_fund.unreplenished_amount, 700_000)
        self.assertAlmostEqual(self._advance_balance(self.advance_pc, self.custodian.partner_id), 4_300_000, 2)

        replenish = self._replenish()
        self.assertTrue(replenish.is_replenishment)
        self.assertEqual(replenish.amount_requested, 700_000)
        self.assertEqual(replenish.maker_id, self.custodian)
        self.assertEqual(realization.replenish_request_id, replenish)
        self.assertEqual(self.fresh_fund.unreplenished_amount, 0)

        self._approve_disburse(replenish)
        self.assertEqual(self.fresh_fund.balance, 5_000_000)
        self.assertTrue(self.fresh_fund.last_replenish_date)

    def test_third_party_realization_on_fund(self):
        self._funded()
        line = {
            "line_type": "third_party",
            "name": "Gas elpiji",
            "partner_id": self.vendor.id,
            "account_id": self.expense.id,
            "price_unit": 250_000,
            "attachment_ids": [Command.set(self._attachment(self.custodian).ids)],
        }
        realization = self._realization({"fund_id": self.fund.id}, [line], user=self.custodian)
        realization.action_submit()
        realization.with_user(self.finance).action_post()
        bill = realization.sudo().bill_ids.filtered(lambda m: m.move_type == "in_invoice")
        self.assertEqual(bill.efn_petty_cash_fund_id, self.fund)
        self.assertIn(bill.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.fresh_fund.balance, 4_750_000)

    def test_replenishment_cancel_releases_realizations(self):
        self._funded()
        realization = self._fund_realization([250_000])
        replenish = self._replenish()
        replenish.with_user(self.custodian).action_cancel()
        self.assertFalse(realization.replenish_request_id)
        self.assertEqual(self.fresh_fund.unreplenished_amount, 250_000)

    def test_float_cannot_be_exceeded(self):
        self._funded()
        extra = self._fund_request(100_000)
        with self.assertRaisesRegex(UserError, "plafon imprest"):
            extra.action_submit()

    def test_limit_per_transaction(self):
        self._funded()
        realization = self._fund_realization([1_500_000], submit=False)
        with self.assertRaisesRegex(UserError, "batas per transaksi"):
            realization.action_submit()

    def test_realization_cannot_exceed_balance(self):
        self._approve_disburse(self._fund_request(500_000))
        realization = self._fund_realization([800_000], post=False)
        with self.assertRaisesRegex(UserError, "saldo kas kecil"):
            realization.with_user(self.finance).action_post()

    def test_custodian_cannot_approve_own_replenishment(self):
        approver_group = self.env.ref("efn_petty_cash.group_petty_cash_approver")
        self.custodian.sudo().write({"group_ids": [Command.link(approver_group.id)]})
        request = self._fund_request(1_000_000)
        request.action_submit()
        with self.assertRaises(UserError):
            request.with_user(self.custodian).action_approve()

    def test_close_fund(self):
        self._funded()
        self._fund_realization([1_000_000])
        with self.assertRaises(AccessError):
            self.fund.with_user(self.custodian).action_close_fund()
        self.fund.with_user(self.finance).action_close_fund()
        self.assertEqual(self.fresh_fund.state, "closed")
        self.assertEqual(self.fresh_fund.balance, 0)
        lines = self.fund.move_ids.line_ids.filtered(lambda ln: ln.account_id == self.advance_pc)
        self.assertTrue(all(lines.mapped("reconciled")))
        with self.assertRaises(UserError):
            self._fund_request(100_000).action_submit()

    def test_fund_request_needs_fund_type(self):
        with self.assertRaises(UserError):
            self._new_request(100_000, advance_type_id=self.type_pc.id)
