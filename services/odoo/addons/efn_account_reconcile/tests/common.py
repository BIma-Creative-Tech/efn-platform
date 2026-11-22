"""Fixture bersama test efn_account_reconcile (tanpa data klien/PII nyata)."""

from datetime import date

from odoo import Command

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class EfnReconcileCommon(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.misc_journal = cls.company_data["default_journal_misc"]
        cls.partner = cls.partner_a

    @classmethod
    def _invoice(cls, amount, inv_date=date(2026, 7, 1), move_type="out_invoice", payment_reference=None):
        vals = {
            "move_type": move_type,
            "partner_id": cls.partner.id,
            "invoice_date": inv_date,
            "date": inv_date,
            "invoice_line_ids": [
                Command.create({"name": "Layanan uji", "quantity": 1, "price_unit": amount, "tax_ids": []})
            ],
        }
        if payment_reference:
            vals["payment_reference"] = payment_reference
        move = cls.env["account.move"].create(vals)
        move.action_post()
        return move

    @classmethod
    def _receivable(cls, move):
        return move.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable")

    @classmethod
    def _st_line(cls, amount, ref="TRF UJI", st_date=date(2026, 7, 5), partner=True, journal=None):
        line = cls.env["account.bank.statement.line"].create(
            {
                "journal_id": (journal or cls.bank_journal).id,
                "date": st_date,
                "payment_ref": ref,
                "partner_id": cls.partner.id if partner else False,
                "amount": amount,
            }
        )
        if line.move_id.state == "draft":
            line.move_id.action_post()
        return line

    @classmethod
    def _entry(cls, account, amount, when=date(2026, 7, 4), ref="POS UJI", analytic=None, company_data=None):
        """Entri umum Dr ``account`` / Cr pendapatan; kembalikan baris pada ``account``."""
        data = company_data or cls.company_data
        line_vals = {"account_id": account.id, "name": "Piutang uji", "debit": amount, "credit": 0.0}
        if analytic:
            line_vals["analytic_distribution"] = {str(analytic.id): 100.0}
        move = cls.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": data["default_journal_misc"].id,
                "company_id": data["company"].id,
                "date": when,
                "ref": ref,
                "line_ids": [
                    Command.create(line_vals),
                    Command.create(
                        {
                            "account_id": data["default_account_revenue"].id,
                            "name": "Penjualan uji",
                            "debit": 0.0,
                            "credit": amount,
                        }
                    ),
                ],
            }
        )
        move.action_post()
        return move.line_ids.filtered(lambda line: line.account_id == account)

    def _wizard(self, st_line):
        action = st_line.action_efn_open_match_wizard()
        return self.env["efn.bank.reconcile.wizard"].browse(action["res_id"])
