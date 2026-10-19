from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Akun akrual Poin EFN per PT (company aktif Settings). Settings CE hanya dapat disimpan admin sistem
    (Superadmin, keputusan klien 6 Okt) -- perubahan ``res.company`` ikut tercatat chatter/efn_audit bila ada aturan."""

    _inherit = "res.config.settings"

    efn_loyalty_liability_account_id = fields.Many2one(
        related="company_id.efn_loyalty_liability_account_id", readonly=False
    )
    efn_loyalty_expense_account_id = fields.Many2one(
        related="company_id.efn_loyalty_expense_account_id", readonly=False
    )
    efn_loyalty_clearing_account_id = fields.Many2one(
        related="company_id.efn_loyalty_clearing_account_id", readonly=False
    )
    efn_loyalty_breakage_account_id = fields.Many2one(
        related="company_id.efn_loyalty_breakage_account_id", readonly=False
    )
    efn_loyalty_interco_payable_account_id = fields.Many2one(
        related="company_id.efn_loyalty_interco_payable_account_id", readonly=False
    )
    efn_loyalty_journal_id = fields.Many2one(related="company_id.efn_loyalty_journal_id", readonly=False)
