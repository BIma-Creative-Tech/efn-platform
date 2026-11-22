"""G59-B: akun & jurnal tangguhan per PT (port `custom_account_deferred/models/res_company.py`, ADR-049).

Nama field sengaja `efn_*`: template CoA `l10n_id` CE mengisi `deferred_expense_account_id` milik Enterprise; nama
berbeda mencegah tabrakan bila suatu saat modul EE dipasang dan menjaga nilai diisi Finance, bukan template.
"""

import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    efn_deferred_expense_account_id = fields.Many2one(
        "account.account",
        string="Akun Beban Dibayar di Muka",
        check_company=True,
        domain="[('account_type', 'in', ('asset_current', 'asset_prepayments'))]",
    )
    efn_deferred_revenue_account_id = fields.Many2one(
        "account.account",
        string="Akun Pendapatan Diterima di Muka",
        check_company=True,
        domain="[('account_type', 'in', ('liability_current', 'liability_non_current'))]",
    )
    efn_deferred_journal_id = fields.Many2one(
        "account.journal",
        string="Jurnal Tangguhan",
        check_company=True,
        domain="[('type', '=', 'general')]",
    )

    @api.constrains("efn_deferred_expense_account_id", "efn_deferred_revenue_account_id", "efn_deferred_journal_id")
    def _check_efn_deferred_company(self):
        """Akun/jurnal PT lain tidak boleh dipakai (multi-company), apa pun jalurnya (UI, RPC, import)."""
        for company in self:
            for model, record in (
                ("account.account", company.efn_deferred_expense_account_id),
                ("account.account", company.efn_deferred_revenue_account_id),
                ("account.journal", company.efn_deferred_journal_id),
            ):
                if not record:
                    continue
                domain = [("id", "=", record.id), *self.env[model]._check_company_domain(company)]
                if not self.env[model].sudo().search_count(domain):
                    raise ValidationError(
                        self.env._(
                            "%(record)s bukan milik PT %(company)s.",
                            record=record.sudo().display_name,
                            company=company.name,
                        )
                    )
            journal = company.efn_deferred_journal_id
            if journal and journal.type != "general":
                raise ValidationError(self.env._("Jurnal Tangguhan harus jurnal tipe Miscellaneous (general)."))

    def _efn_deferred_load_demo(self):
        """Data demo: PT ber-CoA l10n_id diisi akun 11210040 / 28110030 + jurnal Miscellaneous pertama."""
        for company in self.search([("chart_template", "=", "id")]):
            chart = self.env["account.chart.template"].with_company(company)
            vals = {}
            expense = chart.ref("l10n_id_11210040", raise_if_not_found=False)
            revenue = chart.ref("l10n_id_28110030", raise_if_not_found=False)
            journal = self.env["account.journal"].search(
                [*self.env["account.journal"]._check_company_domain(company), ("type", "=", "general")], limit=1
            )
            if expense and not company.efn_deferred_expense_account_id:
                vals["efn_deferred_expense_account_id"] = expense.id
            if revenue and not company.efn_deferred_revenue_account_id:
                vals["efn_deferred_revenue_account_id"] = revenue.id
            if journal and not company.efn_deferred_journal_id:
                vals["efn_deferred_journal_id"] = journal.id
            if vals:
                company.write(vals)
                _logger.info("EFN_DEFERRED_DEMO company=%s fields=%s", company.id, sorted(vals))
