from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    efn_payment_approval_threshold = fields.Monetary(
        related="company_id.efn_payment_approval_threshold", readonly=False, currency_field="currency_id"
    )
    efn_mdr_journal_id = fields.Many2one(related="company_id.efn_mdr_journal_id", readonly=False)
    efn_outlet_analytic_plan_id = fields.Many2one(related="company_id.efn_outlet_analytic_plan_id", readonly=False)
