"""Pencocokan mutasi bank (port test_bank_reconcile.py sumber + bagian generik Levi's: batas tanggal, isi otomatis,
tanpa auto-match tunai, No. Transaksi)."""

from datetime import date

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import EfnReconcileCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnBankReconcile(EfnReconcileCommon):
    def test_exact_match_reconciles(self):
        inv = self._invoice(500000.0)
        st = self._st_line(500000.0, ref=inv.name)
        aml = st._efn_get_auto_match_candidate()
        self.assertEqual(aml.move_id, inv)
        st._efn_reconcile_with_amls(aml)
        self.assertTrue(st.is_reconciled)
        self.assertIn(inv.payment_state, ("paid", "in_payment"))
        self.assertFalse(st._seek_for_lines()[1], "tidak boleh ada kaki suspense tersisa")

    def test_writeoff_remainder(self):
        inv = self._invoice(500000.0)
        st = self._st_line(490000.0, ref=inv.name)
        fee_account = self.env["account.account"].create(
            {"name": "Biaya bank uji", "code": "EFNBF1", "account_type": "expense"}
        )
        st._efn_reconcile_with_amls(
            self._receivable(inv), writeoff_vals_list=[{"account_id": fee_account.id, "name": "Biaya bank"}]
        )
        self.assertTrue(st.is_reconciled)
        self.assertIn(inv.payment_state, ("paid", "in_payment"))
        fee_line = st.move_id.line_ids.filtered(lambda line: line.account_id == fee_account)
        self.assertAlmostEqual(fee_line.debit, 10000.0, places=2)

    def test_partial_match_leaves_remainder_on_suspense(self):
        inv = self._invoice(300000.0)
        st = self._st_line(500000.0, ref=inv.name)
        st._efn_reconcile_with_amls(self._receivable(inv))
        suspense = st._seek_for_lines()[1]
        self.assertAlmostEqual(sum(suspense.mapped("balance")), -200000.0, places=2)
        self.assertTrue(self._receivable(inv).reconciled)

    def test_undo_restores_suspense(self):
        inv = self._invoice(250000.0)
        st = self._st_line(250000.0, ref=inv.name)
        st._efn_reconcile_with_amls(st._efn_get_auto_match_candidate())
        self.assertTrue(st.is_reconciled)
        st.action_undo_reconciliation()
        self.assertFalse(st.is_reconciled)
        self.assertTrue(st._seek_for_lines()[1])

    def test_auto_match_ambiguous_skips(self):
        self._invoice(100000.0)
        self._invoice(100000.0)
        st = self._st_line(100000.0)
        self.assertFalse(st._efn_get_auto_match_candidate())
        st.action_efn_auto_match()
        self.assertFalse(st.is_reconciled)

    def test_auto_match_action_reconciles_unique(self):
        inv = self._invoice(123000.0)
        st = self._st_line(123000.0, ref=inv.name)
        result = st.action_efn_auto_match()
        self.assertEqual(result["params"]["type"], "success")
        self.assertTrue(st.is_reconciled)

    def test_wizard_page_preselects_exact(self):
        inv = self._invoice(750000.0)
        st = self._st_line(750000.0, ref=inv.name)
        action = st.action_efn_open_match_wizard()
        self.assertEqual(action["target"], "current", "halaman penuh, bukan dialog")
        wiz = self.env["efn.bank.reconcile.wizard"].browse(action["res_id"])
        self.assertEqual(wiz.st_line_id, st)
        self.assertEqual(wiz.candidate_ids.filtered("selected").aml_id.move_id, inv)
        self.assertAlmostEqual(wiz.remainder, 0.0, places=2)
        wiz.action_reconcile()
        self.assertTrue(st.is_reconciled)

    def test_wizard_writeoff_checkbox(self):
        inv = self._invoice(500000.0)
        st = self._st_line(495000.0, ref=inv.name)
        fee_account = self.env["account.account"].create(
            {"name": "Biaya bank uji 2", "code": "EFNBF2", "account_type": "expense"}
        )
        wiz = self._wizard(st)
        wiz.candidate_ids.selected = False
        wiz.candidate_ids.filtered(lambda c: c.aml_id.move_id == inv).selected = True
        wiz.write({"writeoff": True, "writeoff_account_id": fee_account.id})
        wiz.action_reconcile()
        self.assertTrue(st.is_reconciled)
        fee_line = st.move_id.line_ids.filtered(lambda line: line.account_id == fee_account)
        self.assertAlmostEqual(fee_line.balance, 5000.0, places=2)

    def test_reconciled_line_cannot_open_wizard(self):
        inv = self._invoice(80000.0)
        st = self._st_line(80000.0, ref=inv.name)
        st._efn_reconcile_with_amls(self._receivable(inv))
        with self.assertRaises(UserError):
            st.action_efn_open_match_wizard()

    # ------------------------------------------------------------------ batas tanggal mutasi (Levi's, generik)
    def test_item_after_statement_date_is_not_offered(self):
        before = self._invoice(400000.0, inv_date=date(2026, 7, 3))
        after = self._invoice(400000.0, inv_date=date(2026, 7, 20))
        st = self._st_line(400000.0, st_date=date(2026, 7, 5))
        candidates = st._efn_get_match_candidates()
        self.assertIn(self._receivable(before), candidates)
        self.assertNotIn(self._receivable(after), candidates, "bank tidak mungkin membayar transaksi yang belum ada")
        self.assertNotIn(self._receivable(after), st._efn_get_match_candidates(relax=True))
        wiz = self._wizard(st)
        wiz.action_search_more()
        self.assertNotIn(self._receivable(after), wiz.candidate_ids.aml_id)

    def test_date_ceiling_parameter_can_be_disabled(self):
        after = self._invoice(410000.0, inv_date=date(2026, 7, 20))
        st = self._st_line(410000.0, st_date=date(2026, 7, 5))
        self.env["ir.config_parameter"].sudo().set_param("efn_account_reconcile.date_ceiling", "0")
        self.assertIn(self._receivable(after), st._efn_get_match_candidates())

    # ------------------------------------------------------------------ setoran tunai (Levi's, generik)
    def test_suggest_stops_at_the_deposit(self):
        cash = self.env["account.account"].create(
            {"name": "Outstanding kas POS uji", "code": "EFNCS1", "account_type": "asset_current", "reconcile": True}
        )
        for day, amount in ((1, 4_000_000.0), (2, 3_000_000.0), (3, 2_500_000.0)):
            self._entry(cash, amount, when=date(2026, 7, day))
        st = self._st_line(7_000_000.0, ref="SETORAN TUNAI", st_date=date(2026, 7, 4), partner=False)
        wiz = self._wizard(st)
        self.assertIsNone(wiz.action_efn_suggest())
        picked = wiz.candidate_ids.filtered("selected")
        total = sum(picked.mapped("amount_residual"))
        self.assertLessEqual(total, 7_000_000.0, "tidak pernah melebihi nominal setoran")
        self.assertAlmostEqual(total, 7_000_000.0, places=2)
        self.assertEqual(len(picked), 2)

    def test_suggest_leaves_the_shortfall_on_suspense(self):
        cash = self.env["account.account"].create(
            {"name": "Outstanding kas POS uji 2", "code": "EFNCS2", "account_type": "asset_current", "reconcile": True}
        )
        self._entry(cash, 4_000_000.0, when=date(2026, 7, 1))
        st = self._st_line(6_000_000.0, ref="SETORAN TUNAI", st_date=date(2026, 7, 4), partner=False)
        wiz = self._wizard(st)
        wiz.action_efn_suggest()
        self.assertAlmostEqual(wiz.remainder, -2_000_000.0, places=2)
        wiz.action_reconcile()
        left = st._seek_for_lines()[1]
        self.assertAlmostEqual(sum(left.mapped("balance")), -2_000_000.0, places=2)

    def test_suggest_without_fitting_item_raises(self):
        st = self._st_line(1.0, ref="KECIL", partner=False)
        wiz = self._wizard(st)
        with self.assertRaises(UserError):
            wiz.action_efn_suggest()

    def test_cash_account_is_never_auto_matched(self):
        cash = self.env["account.account"].create(
            {"name": "Outstanding kas POS uji 3", "code": "EFNCS3", "account_type": "asset_current", "reconcile": True}
        )
        self._entry(cash, 500_000.0, when=date(2026, 7, 3))
        st = self._st_line(500_000.0, ref="SETORAN TUNAI", st_date=date(2026, 7, 4), partner=False)
        self.assertTrue(st._efn_get_auto_match_candidate(), "kontrol: tanpa penanda, kandidat tunggal ditemukan")
        cash.efn_no_auto_match = True
        self.assertFalse(st._efn_get_auto_match_candidate(), "setoran tunai tidak dicocokkan otomatis")

    # ------------------------------------------------------------------ No. Transaksi
    def test_transaction_ref_prefers_payment_reference_then_entry(self):
        inv = self._invoice(500000.0, payment_reference="INV-TRX-0099")
        self.assertEqual(self._receivable(inv).efn_transaction_ref, "INV-TRX-0099")
        cash = self.env["account.account"].create(
            {"name": "Kliring uji", "code": "EFNCL1", "account_type": "asset_current", "reconcile": True}
        )
        aml = self._entry(cash, 1000.0, ref="SETTLE-01")
        self.assertEqual(aml.efn_transaction_ref, "SETTLE-01")
        aml.move_id.write({"ref": False})
        self.assertEqual(aml.efn_transaction_ref, aml.move_id.name)
