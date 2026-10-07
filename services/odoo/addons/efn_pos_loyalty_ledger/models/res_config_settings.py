from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Prefix `pos_` -> ditulis ke pos_config_id oleh point_of_sale (lihat catatan modul induk).
    pos_efn_loyalty_earn_enabled = fields.Boolean(related="pos_config_id.efn_loyalty_earn_enabled", readonly=False)
    pos_efn_loyalty_redeem_enabled = fields.Boolean(related="pos_config_id.efn_loyalty_redeem_enabled", readonly=False)
