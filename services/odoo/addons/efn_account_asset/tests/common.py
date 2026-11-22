"""Fixture uji efn_account_asset: akun buatan test (bukan l10n_id), jurnal misc, kelompok, user env = Aset / Manager."""

from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from odoo import Command, fields

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class EfnAssetCommon(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.manager_group = cls.env.ref("efn_account_asset.group_asset_manager")
        cls.user_group = cls.env.ref("efn_account_asset.group_asset_user")
        cls.env.user.sudo().write({"group_ids": [Command.link(cls.manager_group.id)]})
        cls.journal = cls.company_data["default_journal_misc"]
        cls.asset_account = cls._efn_account("EFNFA1", "FA - Peralatan", "asset_fixed")
        cls.accum_account = cls._efn_account("EFNFA9", "FA - Akumulasi", "asset_fixed")
        cls.expense_account = cls._efn_account("EFNDE1", "Beban Penyusutan", "expense")
        cls.surplus_account = cls._efn_account("EFNRS1", "Surplus Revaluasi", "equity")
        cls.reval_loss_account = cls._efn_account("EFNRL1", "Rugi Revaluasi", "expense")
        cls.reval_income_account = cls._efn_account("EFNRI1", "Pendapatan Revaluasi", "income_other")
        cls.retained_earnings_account = cls._efn_account("EFNRE1", "Laba Ditahan", "equity")
        cls.gain_account = cls._efn_account("EFNGN1", "Laba Pelepasan", "income_other")
        cls.loss_account = cls._efn_account("EFNLS1", "Rugi Pelepasan", "expense")
        cls.proceeds_account = cls._efn_account("EFNPR1", "Kliring Hasil Pelepasan", "asset_current")
        cls.group = cls.env["efn.account.asset.group"].create(
            {
                "name": "Peralatan uji",
                "code": "EQ-T",
                "company_id": cls.company.id,
                "default_useful_life_months": 12,
                "default_asset_account_id": cls.asset_account.id,
                "default_depreciation_account_id": cls.accum_account.id,
                "default_expense_account_id": cls.expense_account.id,
                "default_journal_id": cls.journal.id,
            }
        )

    @classmethod
    def _efn_account(cls, code, name, account_type, company=None):
        company = company or cls.company
        return (
            cls.env["account.account"]
            .with_company(company)
            .create(
                {
                    "code": code,
                    "name": name,
                    "account_type": account_type,
                    "company_ids": [Command.set(company.ids)],
                }
            )
        )

    def _make_asset(self, **overrides):
        vals = {
            "name": "Laptop uji",
            "company_id": self.company.id,
            "group_id": self.group.id,
            "acquisition_date": date(2025, 1, 1),
            "acquisition_value": 12000.0,
            "salvage_value": 0.0,
            "useful_life_months": 12,
            "depreciation_method": "straight_line",
            "asset_account_id": self.asset_account.id,
            "depreciation_account_id": self.accum_account.id,
            "expense_account_id": self.expense_account.id,
            "journal_id": self.journal.id,
        }
        vals.update(overrides)
        return self.env["efn.account.asset"].create(vals)

    @contextmanager
    def _mock_today(self, today):
        with patch.object(fields.Date, "context_today", lambda *args, **kwargs: today):
            yield
