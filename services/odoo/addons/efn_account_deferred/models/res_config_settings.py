from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    efn_deferred_expense_account_id = fields.Many2one(
        related="company_id.efn_deferred_expense_account_id", readonly=False
    )
    efn_deferred_revenue_account_id = fields.Many2one(
        related="company_id.efn_deferred_revenue_account_id", readonly=False
    )
    efn_deferred_journal_id = fields.Many2one(related="company_id.efn_deferred_journal_id", readonly=False)
