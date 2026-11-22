"""Fixture efn_petty_cash: dua PT uji (AccountTestInvoicingCommon), akun uang muka buatan test (placeholder Q-PC1),
dua jenis per PT A (uang muka karyawan + kas kecil outlet), user per peran (pemohon, penyetuju, finance)."""

from odoo.fields import Command
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

USER = "efn_petty_cash.group_petty_cash_user"
APPROVER = "efn_petty_cash.group_petty_cash_approver"
FINANCE = "efn_petty_cash.group_petty_cash_finance"


class EfnPettyCashCommon(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.company_data["company"]
        cls.data_b = cls.setup_other_company(name="PT Uji Kas Kecil B")
        cls.company_b = cls.data_b["company"]

        cls.advance_ca = cls._efn_account(cls.company_a, "EFNPC101", "Uang Muka Karyawan (uji)")
        cls.advance_pc = cls._efn_account(cls.company_a, "EFNPC102", "Uang Muka Kas Kecil (uji)")
        cls.expense = cls.company_data["default_account_expense"]
        cls.bank_journal = cls.company_data["default_journal_bank"]
        cls.misc_journal = cls.company_data["default_journal_misc"]

        Type = cls.env["efn.petty.cash.type"].sudo()
        cls.type_ca = Type.create(
            {
                "name": "Uang Muka Perjalanan",
                "code": "UM",
                "kind": "cash_advance",
                "is_default": True,
                "company_id": cls.company_a.id,
                "advance_account_id": cls.advance_ca.id,
                "bank_out_journal_id": cls.bank_journal.id,
                "payment_journal_id": cls.misc_journal.id,
                "expense_journal_id": cls.misc_journal.id,
            }
        )
        cls.type_pc = Type.create(
            {
                "name": "Kas Kecil Outlet",
                "code": "KK",
                "kind": "petty_cash",
                "company_id": cls.company_a.id,
                "advance_account_id": cls.advance_pc.id,
                "bank_out_journal_id": cls.bank_journal.id,
                "payment_journal_id": cls.misc_journal.id,
                "expense_journal_id": cls.misc_journal.id,
            }
        )

        a = [cls.company_a]
        cls.requester = cls._efn_user("efn_pc_requester", [USER], a)
        cls.custodian = cls._efn_user("efn_pc_custodian", [USER], a)
        cls.approver = cls._efn_user("efn_pc_approver", [APPROVER], a)
        cls.finance = cls._efn_user("efn_pc_finance", [FINANCE], a)
        cls.finance_approver = cls._efn_user("efn_pc_fin_appr", [FINANCE, APPROVER], a)
        cls.custodian_b = cls._efn_user("efn_pc_custodian_b", [USER], [cls.company_b])

        cls.fund = (
            cls.env["efn.petty.cash.fund"]
            .sudo()
            .create(
                {
                    "name": "Kas Kecil Outlet Uji",
                    "company_id": cls.company_a.id,
                    "type_id": cls.type_pc.id,
                    "holder_partner_id": cls.custodian.partner_id.id,
                    "holder_user_id": cls.custodian.id,
                    "float_amount": 5_000_000,
                    "limit_per_transaction": 1_000_000,
                }
            )
        )
        cls.vendor = cls.env["res.partner"].create({"name": "Toko Bahan Uji", "is_company": True})

    @classmethod
    def _efn_account(cls, company, code, name):
        return (
            cls.env["account.account"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "code": code,
                    "name": name,
                    "account_type": "asset_current",
                    "reconcile": True,
                    "company_ids": [Command.set(company.ids)],
                }
            )
        )

    @classmethod
    def _efn_user(cls, login, groups, companies):
        return new_test_user(
            cls.env,
            login=login,
            groups=",".join(["base.group_user", *groups]),
            company_id=companies[0].id,
            company_ids=[Command.set([c.id for c in companies])],
        )

    # ------------------------------------------------------------------ alur bantu
    def _new_request(self, amount=1_000_000, user=None, **extra):
        user = user or self.requester
        vals = {"advance_type_id": self.type_ca.id, "amount_requested": amount, "purpose": "Perjalanan", **extra}
        return self.env["efn.petty.cash.request"].with_user(user).create(vals)

    def _disbursed_request(self, amount=1_000_000):
        request = self._new_request(amount)
        request.action_submit()
        request.with_user(self.approver).action_approve()
        request.with_user(self.finance).action_disburse()
        return request

    def _realization(self, owner_vals, lines, user=None):
        Realization = self.env["efn.petty.cash.realization"].with_user(user or self.requester)
        return Realization.create({**owner_vals, "line_ids": [Command.create(vals) for vals in lines]})

    def _attachment(self, user):
        """Nota milik pengunggah (lampiran tanpa res_model hanya terbaca oleh pembuatnya)."""
        Attachment = self.env["ir.attachment"].with_user(user)
        return Attachment.create({"name": "nota.pdf", "datas": "cGRm", "mimetype": "application/pdf"})

    def _expense_line(self, amount, name="Parkir"):
        return {"line_type": "expense", "name": name, "account_id": self.expense.id, "price_unit": amount}

    def _advance_balance(self, account, partner):
        lines = self.env["account.move.line"].search(
            [("account_id", "=", account.id), ("partner_id", "=", partner.id), ("parent_state", "=", "posted")]
        )
        return sum(lines.mapped("balance"))
