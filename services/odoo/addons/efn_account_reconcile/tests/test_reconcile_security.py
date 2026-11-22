"""Kasus keamanan G58 §3.4/§3.5: ringkasan per company, RPC user Billing ditolak, injeksi kandidat wizard ditolak."""

from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from .common import EfnReconcileCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnReconcileSecurity(EfnReconcileCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_data = cls.setup_other_company(name="PT lain uji rekonsiliasi")
        cls.other = cls.other_data["company"]
        cls.env.user.company_ids |= cls.other
        cls.billing = new_test_user(
            cls.env,
            login="efn_rec_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.accountant_a = new_test_user(
            cls.env,
            login="efn_rec_acc_a",
            groups="base.group_user,account.group_account_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        # Satu akun dipakai dua PT -> sumber menjumlahnya dalam satu baris.
        cls.shared = cls.env["account.account"].create(
            {
                "name": "Kliring bersama uji",
                "code": "EFNSH1",
                "account_type": "asset_current",
                "reconcile": True,
                # CE 19: kode wajib per company; code_mapping_ids ditulis sebelum company_ids (write_sequence).
                "code_mapping_ids": [
                    Command.create({"company_id": cls.company.id, "code": "EFNSH1"}),
                    Command.create({"company_id": cls.other.id, "code": "EFNSH1"}),
                ],
                "company_ids": [Command.set((cls.company | cls.other).ids)],
            }
        )

    def test_overview_is_split_per_company_and_filtered_by_rule(self):
        self._entry(self.shared, 1000.0)
        self._entry(self.shared, 7777.0, company_data=self.other_data)
        self.env.flush_all()
        Overview = self.env["efn.reconcile.account"].with_context(allowed_company_ids=(self.company | self.other).ids)
        rows = Overview.search([("account_id", "=", self.shared.id)])
        self.assertEqual(len(rows), 2, "satu baris per (company, akun)")
        self.assertEqual(
            sorted(rows.mapped("residual")), [1000.0, 7777.0], "nilai PT lain tidak boleh tercampur dalam satu baris"
        )
        own = (
            self.env["efn.reconcile.account"].with_user(self.accountant_a).search([("account_id", "=", self.shared.id)])
        )
        self.assertEqual(own.company_id, self.company)
        self.assertEqual(own.residual, 1000.0, "PT lain tidak terlihat")
        action = own.action_open_lines()
        self.assertIn(("company_id", "=", self.company.id), action["domain"])

    def test_billing_user_cannot_auto_match_via_rpc(self):
        inv = self._invoice(50000.0)
        st = self._st_line(50000.0, ref=inv.name)
        with self.assertRaises(AccessError):
            st.with_user(self.billing).action_efn_auto_match()
        with self.assertRaises(AccessError):
            st.with_user(self.billing).action_efn_open_match_wizard()
        self.assertFalse(st.is_reconciled)

    def test_billing_user_cannot_use_journal_item_wizard(self):
        inv = self._invoice(10.0)
        with self.assertRaises(AccessError):
            self.env["efn.account.reconcile.wizard"].with_user(self.billing).with_context(
                active_model="account.move.line", active_ids=self._receivable(inv).ids
            ).default_get(["line_ids"])

    def test_wizard_rejects_injected_candidate(self):
        """Baris wizard bisa ditulis lewat RPC: item setelah tanggal mutasi tidak boleh ikut direkonsiliasi."""
        st = self._st_line(60000.0, st_date=date(2026, 7, 5))
        later = self._receivable(self._invoice(60000.0, inv_date=date(2026, 7, 25)))
        wiz = self._wizard(st)
        wiz.candidate_ids = [Command.create({"aml_id": later.id, "selected": True})]
        with self.assertRaisesRegex(UserError, "bukan kandidat"):
            wiz.action_reconcile()
        self.assertFalse(later.reconciled)

    def test_journal_item_wizard_reconciles_and_revalidates(self):
        inv = self._invoice(400.0)
        other_inv = self._invoice(400.0, move_type="out_refund")
        lines = self._receivable(inv) | self._receivable(other_inv)
        wiz = (
            self.env["efn.account.reconcile.wizard"]
            .with_context(active_model="account.move.line", active_ids=lines.ids)
            .create({})
        )
        self.assertTrue(wiz.is_balanced)
        wiz.action_reconcile()
        self.assertTrue(all(lines.mapped("reconciled")))
        with self.assertRaises(UserError):
            self.env["efn.account.reconcile.wizard"].with_context(
                active_model="account.move.line", active_ids=self._receivable(inv).ids
            ).default_get(["line_ids"])

    def test_other_company_statement_line_refused(self):
        other_bank = self.other_data["default_journal_bank"]
        st = self.env["account.bank.statement.line"].create(
            {"journal_id": other_bank.id, "date": date(2026, 7, 5), "payment_ref": "PT LAIN", "amount": 10.0}
        )
        with self.assertRaises(AccessError):
            st.with_user(self.accountant_a).action_efn_open_match_wizard()
