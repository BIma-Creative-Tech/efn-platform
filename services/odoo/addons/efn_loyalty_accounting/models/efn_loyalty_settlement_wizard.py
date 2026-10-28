"""Wizard menyiapkan settlement poin antar-PT (W-15)."""

from odoo import fields, models


class EfnLoyaltySettlementWizard(models.TransientModel):
    _name = "efn.loyalty.settlement.wizard"
    _description = "EFN siapkan settlement Poin antar-PT"

    period = fields.Char(string="Periode (YYYY-MM)", required=True)
    issuer_company_id = fields.Many2one(
        "res.company", string="PT penerbit", domain=lambda self: [("id", "in", self.env.user.company_ids.ids)]
    )
    redeemer_company_id = fields.Many2one(
        "res.company", string="PT penebus", domain=lambda self: [("id", "in", self.env.user.company_ids.ids)]
    )

    def action_prepare(self):
        self.ensure_one()
        records = self.env["efn.loyalty.settlement"].efn_prepare(
            self.period.strip(), self.issuer_company_id.id or None, self.redeemer_company_id.id or None
        )
        action = self.env["ir.actions.act_window"]._for_xml_id("efn_loyalty_accounting.action_efn_loyalty_settlement")
        action["domain"] = [("id", "in", records.ids)]
        action["context"] = {}
        return action
