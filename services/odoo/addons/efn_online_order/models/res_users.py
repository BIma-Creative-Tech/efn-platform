"""SEC-101: hak user teknis kanal online ditegakkan juga saat grup/company user atau grup berubah belakangan."""

from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.constrains("group_ids", "company_ids", "company_id")
    def _check_efn_online_technical_user(self):
        self.env["efn.online.channel"]._efn_online_recheck_users(self)


class ResGroups(models.Model):
    _inherit = "res.groups"

    @api.constrains("implied_ids", "user_ids")
    def _check_efn_online_technical_users(self):
        channels = self.env["efn.online.channel"].sudo().with_context(active_test=False).search([])
        users = channels.user_ids
        if users:
            self.env["efn.online.channel"]._efn_online_recheck_users(users)
