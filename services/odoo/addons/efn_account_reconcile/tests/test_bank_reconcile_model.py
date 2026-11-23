"""True-up MDR lewat `account.reconcile.model` CE di wizard pencocokan (G58 §3.3, G60 D-5).

Versi generik test_bank_reconcile_tender.py Levi's: settlement kartu/QRIS masuk neto, piutang POS dicocokkan bruto,
selisih dibukukan baris model (persentase/tetap/regex) ke akun MDR dengan analitik outlet."""

from datetime import date

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import EfnReconcileCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnBankReconcileModel(EfnReconcileCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Account = cls.env["account.account"]
        cls.mdr = Account.create({"name": "Beban MDR uji", "code": "EFNMDR", "account_type": "expense"})
        cls.pos_receivable = Account.create(
            {"name": "Outstanding EDC uji", "code": "EFNEDC", "account_type": "asset_current", "reconcile": True}
        )
        plan = cls.env["account.analytic.plan"].create({"name": "Outlet uji"})
        cls.outlet = cls.env["account.analytic.account"].create({"name": "OUTLET UJI 1", "plan_id": plan.id})

    def _model(self, line_vals, journals=None):
        return self.env["account.reconcile.model"].create(
            {
                "name": "True-up MDR uji",
                "trigger": "manual",
                "match_journal_ids": [Command.set((journals or self.bank_journal).ids)],
                "line_ids": [Command.create(vals) for vals in line_vals],
            }
        )

    def _settlement(self, gross=1_000_000.0, net=990_000.0, ref="KR OTOMATIS EDC TGH: 1000000.00 DDR: 10000.00"):
        aml = self._entry(self.pos_receivable, gross, when=date(2026, 7, 8), analytic=self.outlet)
        st = self._st_line(net, ref=ref, st_date=date(2026, 7, 9), partner=False)
        return aml, st

    def test_percentage_line_books_the_fee_with_outlet_analytic(self):
        aml, st = self._settlement()
        model = self._model(
            [
                {
                    "account_id": self.mdr.id,
                    "amount_type": "percentage",
                    "amount_string": "100",
                    "label": "MDR true-up",
                    "analytic_distribution": {str(self.outlet.id): 100.0},
                }
            ]
        )
        wiz = self._wizard(st)
        self.assertEqual(wiz.candidate_ids.filtered("selected").aml_id, aml.browse(), "neto tidak sama dengan bruto")
        wiz.candidate_ids.filtered(lambda c: c.aml_id == aml).selected = True
        wiz.reconcile_model_id = model
        wiz.action_reconcile()
        self.assertTrue(aml.reconciled, "piutang harus lunas pada bruto")
        fee = st.move_id.line_ids.filtered(lambda line: line.account_id == self.mdr)
        self.assertEqual(len(fee), 1)
        self.assertAlmostEqual(fee.balance, 10_000.0, places=2)
        self.assertEqual(fee.analytic_distribution, {str(self.outlet.id): 100.0})
        self.assertEqual(fee.reconcile_model_id, model)
        self.assertFalse(st._seek_for_lines()[1], "tidak ada sisa di suspense")

    def test_fixed_line_never_exceeds_and_leaves_rest_on_suspense(self):
        aml, st = self._settlement()
        model = self._model([{"account_id": self.mdr.id, "amount_type": "fixed", "amount_string": "7500"}])
        writeoffs = st._efn_reconcile_model_writeoffs(model, 10_000.0)
        self.assertEqual(len(writeoffs), 1)
        self.assertAlmostEqual(writeoffs[0]["balance"], 7_500.0, places=2)
        st._efn_reconcile_with_amls(aml, writeoff_vals_list=writeoffs)
        self.assertTrue(aml.reconciled)
        self.assertAlmostEqual(sum(st._seek_for_lines()[1].mapped("balance")), 2_500.0, places=2)
        # Nominal tetap lebih besar dari sisa -> dibatasi sisa.
        capped = st._efn_reconcile_model_writeoffs(
            self._model([{"account_id": self.mdr.id, "amount_type": "fixed", "amount_string": "50000"}]), 10_000.0
        )
        self.assertAlmostEqual(capped[0]["balance"], 10_000.0, places=2)

    def test_regex_line_reads_the_fee_from_the_narrative(self):
        _aml, st = self._settlement()
        model = self._model(
            [{"account_id": self.mdr.id, "amount_type": "regex", "amount_string": r"DDR: ([\d.,]+)"}]
        )
        writeoffs = st._efn_reconcile_model_writeoffs(model, 10_000.0)
        self.assertAlmostEqual(writeoffs[0]["balance"], 10_000.0, places=2)

    def test_regex_without_match_is_skipped(self):
        _aml, st = self._settlement(ref="KR OTOMATIS TANPA BIAYA")
        model = self._model([{"account_id": self.mdr.id, "amount_type": "regex", "amount_string": r"DDR: ([\d.,]+)"}])
        self.assertEqual(st._efn_reconcile_model_writeoffs(model, 10_000.0), [])

    def test_percentage_of_statement_line(self):
        _aml, st = self._settlement(gross=1_000_000.0, net=1_000_000.0)
        model = self._model(
            [{"account_id": self.mdr.id, "amount_type": "percentage_st_line", "amount_string": "0.5"}]
        )
        writeoffs = st._efn_reconcile_model_writeoffs(model, 20_000.0)
        self.assertAlmostEqual(writeoffs[0]["balance"], 5_000.0, places=2)

    def test_model_with_tax_is_refused(self):
        _aml, st = self._settlement()
        tax = self.env["account.tax"].create({"name": "PPN uji MDR", "amount": 11.0, "type_tax_use": "purchase"})
        model = self._model(
            [
                {
                    "account_id": self.mdr.id,
                    "amount_type": "percentage",
                    "amount_string": "100",
                    "tax_ids": [Command.set(tax.ids)],
                }
            ]
        )
        with self.assertRaises(UserError):
            st._efn_reconcile_model_writeoffs(model, 10_000.0)

    def test_model_of_another_journal_is_refused(self):
        _aml, st = self._settlement()
        other_bank = self.env["account.journal"].create({"name": "Bank lain uji", "code": "EFNBL", "type": "bank"})
        model = self._model(
            [{"account_id": self.mdr.id, "amount_type": "percentage", "amount_string": "100"}], journals=other_bank
        )
        with self.assertRaises(UserError):
            st._efn_reconcile_model_writeoffs(model, 10_000.0)

    def test_amount_parsing(self):
        from odoo.addons.efn_account_reconcile.models.account_bank_statement_line import _efn_amount_from_label

        self.assertEqual(_efn_amount_from_label(r"BRT: ([\d,]+)", "X BRT: 3358,07 C:"), 3358.07)
        self.assertEqual(_efn_amount_from_label(r"FEE ([\d.,]+)", "FEE 1.234.567,89"), 1234567.89)
        self.assertEqual(_efn_amount_from_label(r"FEE ([\d.,]+)", "FEE 10.000"), 10000.0)
        self.assertEqual(_efn_amount_from_label(r"\s+0*(\d+?)(\d{2})(?=\s)", "01870912 0009065 00115"), 90.65)
        self.assertIsNone(_efn_amount_from_label(r"(", "x"))
