"""Seed baris default KPI saat company menjadi PT supermarket (G47, docs/04 §28.13.8c).

`post_init_hook` hanya mencakup company yang sudah ada saat install. Di DB baru urutan install umumnya
`efn_insight_sm` lebih dulu, lalu `efn_setup_supermarket` membuat PT SMD — tanpa override ini PT itu tidak punya baris
(tetap aman: baris tidak ada = default, tetapi admin tidak melihat daftar di UI). Idempoten.
"""

from odoo import api, models

from odoo.addons.efn_insight.lib.insight_token import SM_CODES


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model_create_multi
    def create(self, vals_list):
        companies = super().create(vals_list)
        self._efn_insight_sm_seed(companies)
        return companies

    def write(self, vals):
        res = super().write(vals)
        if vals.get("efn_brand_code") in SM_CODES:
            self._efn_insight_sm_seed(self)
        return res

    @api.model
    def _efn_insight_sm_seed(self, companies):
        sm = companies.filtered(lambda c: c.efn_brand_code in SM_CODES)
        if sm:
            self.env["efn.insight.sm.kpi"].sudo()._efn_seed_defaults(sm)
