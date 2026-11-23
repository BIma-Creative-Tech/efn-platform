"""G59-B efn_account_deferred. Tiga test pertama diport dari odoo-platform `custom_account_deferred/tests/
test_deferred.py` (ADR-049); sisanya baru per docs/fit-gap/G59_G60_ACCOUNTING_PORT.md §G59-B (analitik terbawa,
PPh 4(2) tidak ditangguhkan, multi-company, lock date). Angka = data uji, bukan konfigurasi produksi."""

from datetime import date

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnDeferred(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.expense = cls.company_data["default_account_expense"]
        cls.revenue = cls.company_data["default_account_revenue"]
        cls.prepaid = cls._efn_account("EFNPREP1", "asset_prepayments", cls.company)
        cls.unearned = cls._efn_account("EFNDREV1", "liability_current", cls.company)
        cls.company.write(
            {
                "efn_deferred_expense_account_id": cls.prepaid.id,
                "efn_deferred_revenue_account_id": cls.unearned.id,
                "efn_deferred_journal_id": cls.company_data["default_journal_misc"].id,
            }
        )
        cls.data_b = cls.setup_other_company(name="PT Tangguhan B")

    @classmethod
    def _efn_account(cls, code, account_type, company):
        return cls.env["account.account"].create(
            {
                "name": f"EFN Uji {code}",
                "code": code,
                "account_type": account_type,
                "company_ids": [Command.set(company.ids)],
            }
        )

    def _bill(self, price, start, end, bill_date=None, account=None, taxes=None, analytic=None, company=None,
              move_type="in_invoice"):
        company = company or self.company
        line = {
            "name": "Sewa Outlet Uji",
            "account_id": (account or self.expense).id,
            "quantity": 1,
            "price_unit": price,
            "tax_ids": [Command.set(taxes.ids if taxes else [])],
            "efn_deferred_start_date": start,
            "efn_deferred_end_date": end,
        }
        if analytic:
            line["analytic_distribution"] = analytic
        bill_date = bill_date or start or date(2025, 3, 1)
        return (
            self.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": move_type,
                    "partner_id": self.partner_a.id,
                    "invoice_date": bill_date,
                    "date": bill_date,
                    "invoice_line_ids": [Command.create(line)],
                }
            )
        )

    @staticmethod
    def _of_type(moves, kind):
        return moves.filtered(lambda m: m.efn_deferred_entry_type == kind)

    # ---------------------------------------------------------------- port dari sumber
    def test_full_year_spread(self):
        bill = self._bill(1200.0, date(2026, 1, 1), date(2026, 12, 31))
        bill.action_post()
        generated = bill.efn_deferred_generated_ids
        deferral = self._of_type(generated, "deferral")
        recogs = self._of_type(generated, "recognition")
        self.assertEqual(len(deferral), 1)
        self.assertEqual(deferral.state, "posted")
        self.assertEqual(len(recogs), 12)
        recognized = sum(line.credit - line.debit for line in recogs.line_ids if line.account_id == self.prepaid)
        self.assertAlmostEqual(recognized, 1200.0, places=2)
        today = fields.Date.context_today(bill)
        for move in recogs:
            if move.date <= today:
                self.assertEqual(move.state, "posted")
            else:
                self.assertEqual(move.state, "draft")
                self.assertEqual(move.auto_post, "at_date")

    def test_reset_to_draft_cleans_up(self):
        bill = self._bill(600.0, date(2025, 1, 1), date(2025, 6, 30))
        bill.action_post()
        self.assertTrue(bill.efn_deferred_generated_ids)
        bill.button_draft()
        self.assertFalse(bill.efn_deferred_generated_ids)
        # posting ulang membuat jadwal baru (idempoten per siklus)
        bill.action_post()
        self.assertEqual(len(self._of_type(bill.efn_deferred_generated_ids, "recognition")), 6)

    def test_no_dates_no_entries(self):
        bill = self._bill(100.0, False, False, bill_date=date(2025, 3, 1))
        bill.action_post()
        self.assertFalse(bill.efn_deferred_generated_ids)

    # ---------------------------------------------------------------- baru (G59-B)
    def test_prorata_and_last_month_remainder(self):
        bill = self._bill(1000.0, date(2025, 1, 15), date(2025, 3, 10))
        bill.action_post()
        recogs = self._of_type(bill.efn_deferred_generated_ids, "recognition").sorted("date")
        self.assertEqual(recogs.mapped("date"), [date(2025, 1, 31), date(2025, 2, 28), date(2025, 3, 31)])
        amounts = [sum(line.debit for line in m.line_ids if line.account_id == self.expense) for m in recogs]
        # 55 hari: Jan 17, Feb 28, Mar sisa (10 hari).
        self.assertAlmostEqual(amounts[0], round(1000.0 * 17 / 55, 2), places=2)
        self.assertAlmostEqual(amounts[1], round(1000.0 * 28 / 55, 2), places=2)
        self.assertAlmostEqual(sum(amounts), 1000.0, places=2)

    def test_analytic_carried_over(self):
        plan = self.env["account.analytic.plan"].create({"name": "Outlet Uji Tangguhan"})
        outlet = self.env["account.analytic.account"].create(
            {"name": "Outlet Uji A", "plan_id": plan.id, "company_id": self.company.id}
        )
        distribution = {str(outlet.id): 100.0}
        bill = self._bill(600.0, date(2025, 1, 1), date(2025, 6, 30), analytic=distribution)
        bill.action_post()
        generated = bill.efn_deferred_generated_ids
        self.assertEqual(len(generated), 7)
        for line in generated.line_ids:
            self.assertEqual(
                {k: float(v) for k, v in (line.analytic_distribution or {}).items()}, distribution, line.name
            )
        # P&L outlet: total beban sewa di analitik = nilai tagihan (tagihan - penangguhan + 6 pengakuan).
        analytic_lines = (bill | generated).line_ids.analytic_line_ids.filtered(
            lambda al: al.general_account_id == self.expense
        )
        self.assertAlmostEqual(sum(analytic_lines.mapped("amount")), -600.0, places=2)

    def test_withholding_tax_line_not_deferred(self):
        pph_account = self._efn_account("EFNPPH42", "liability_current", self.company)
        pph42 = self.env["account.tax"].create(
            {
                "name": "PPh 4(2) Sewa Uji -10%",
                "amount_type": "percent",
                "amount": -10.0,
                "type_tax_use": "purchase",
                "company_id": self.company.id,
                "tax_group_id": self.tax_purchase_a.tax_group_id.id,
                "invoice_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create({"repartition_type": "tax", "account_id": pph_account.id}),
                ],
                "refund_repartition_line_ids": [
                    Command.create({"repartition_type": "base"}),
                    Command.create({"repartition_type": "tax", "account_id": pph_account.id}),
                ],
            }
        )
        bill = self._bill(1000000.0, date(2025, 1, 1), date(2025, 12, 31), taxes=pph42)
        bill.action_post()
        payable = bill.line_ids.filtered(lambda line: line.account_type == "liability_payable")
        self.assertAlmostEqual(sum(payable.mapped("credit")), 900000.0, places=2)
        generated = bill.efn_deferred_generated_ids
        self.assertNotIn(pph_account, generated.line_ids.account_id)
        deferral = self._of_type(generated, "deferral")
        moved = sum(line.credit for line in deferral.line_ids if line.account_id == self.expense)
        self.assertAlmostEqual(moved, 1000000.0, places=2)
        self.assertEqual(len(self._of_type(generated, "recognition")), 12)

    def test_revenue_invoice_uses_unearned_account(self):
        invoice = self._bill(
            1200.0, date(2025, 1, 1), date(2025, 3, 31), account=self.revenue, move_type="out_invoice"
        )
        invoice.action_post()
        generated = invoice.efn_deferred_generated_ids
        self.assertIn(self.unearned, generated.line_ids.account_id)
        self.assertNotIn(self.prepaid, generated.line_ids.account_id)
        recognized = sum(
            line.credit for line in self._of_type(generated, "recognition").line_ids if line.account_id == self.revenue
        )
        self.assertAlmostEqual(recognized, 1200.0, places=2)

    def test_multi_company_uses_own_accounts(self):
        data_b = self.data_b
        company_b = data_b["company"]
        bill_b = self._bill(
            300.0, date(2025, 1, 1), date(2025, 3, 31), account=data_b["default_account_expense"], company=company_b
        )
        # PT B belum dikonfigurasi: konfigurasi PT A tidak boleh dipakai.
        with self.assertRaisesRegex(UserError, "belum diatur"), self.env.cr.savepoint():
            bill_b.action_post()
        self.assertEqual(bill_b.state, "draft")
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            company_b.efn_deferred_expense_account_id = self.prepaid
        prepaid_b = self._efn_account("EFNPREPB", "asset_prepayments", company_b)
        company_b.write(
            {
                "efn_deferred_expense_account_id": prepaid_b.id,
                "efn_deferred_journal_id": data_b["default_journal_misc"].id,
            }
        )
        bill_b.action_post()
        generated = bill_b.efn_deferred_generated_ids
        self.assertEqual(len(generated), 4)
        self.assertEqual(generated.company_id, company_b)
        self.assertEqual(generated.journal_id, data_b["default_journal_misc"])
        self.assertIn(prepaid_b, generated.line_ids.account_id)
        self.assertNotIn(self.prepaid, generated.line_ids.account_id)

    def test_lock_date_blocks_reset_with_clear_message(self):
        # Tagihan Juni untuk masa Jan-Des: pengakuan Jan-Mei bertanggal sebelum tagihan.
        bill = self._bill(1200.0, date(2025, 1, 1), date(2025, 12, 31), bill_date=date(2025, 6, 15))
        bill.action_post()
        self.company.fiscalyear_lock_date = date(2025, 3, 31)
        with self.assertRaisesRegex(UserError, "periode terkunci"), self.env.cr.savepoint():
            bill.button_draft()
        self.assertEqual(bill.state, "posted")
        self.assertEqual(len(bill.efn_deferred_generated_ids), 13)

    def test_action_open_deferred_entries(self):
        bill = self._bill(600.0, date(2025, 1, 1), date(2025, 6, 30))
        bill.action_post()
        action = bill.action_efn_open_deferred_entries()
        self.assertEqual(action["res_model"], "account.move")
        self.assertEqual(action["domain"], [("efn_deferred_origin_move_id", "=", bill.id)])
        self.assertEqual(bill.efn_deferred_generated_count, 7)

    def test_dates_constraint(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._bill(100.0, date(2025, 1, 1), False)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self._bill(100.0, date(2025, 3, 1), date(2025, 1, 1))

    def test_missing_journal_blocks_posting(self):
        self.company.efn_deferred_journal_id = False
        bill = self._bill(100.0, date(2025, 1, 1), date(2025, 2, 28))
        with self.assertRaisesRegex(UserError, "Jurnal Tangguhan"), self.env.cr.savepoint():
            bill.action_post()
        self.assertEqual(bill.state, "draft")
