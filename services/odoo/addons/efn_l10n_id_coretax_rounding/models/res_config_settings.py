from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    efn_tax_rounding_account_id = fields.Many2one(
        related="company_id.efn_tax_rounding_account_id",
        readonly=False,
        check_company=True,
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost'))]",
    )
    efn_tax_rounding_gain_account_id = fields.Many2one(
        related="company_id.efn_tax_rounding_gain_account_id",
        readonly=False,
        check_company=True,
        domain="[('account_type', 'in', ('income', 'income_other', 'expense', 'expense_direct_cost'))]",
    )
    efn_tax_rounding_journal_id = fields.Many2one(
        related="company_id.efn_tax_rounding_journal_id",
        readonly=False,
        check_company=True,
        domain="[('type', '=', 'general')]",
    )
