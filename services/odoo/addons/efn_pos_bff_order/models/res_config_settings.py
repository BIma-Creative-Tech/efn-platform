from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_efn_qr_enabled = fields.Boolean(related="pos_config_id.efn_qr_enabled", readonly=False)
    pos_efn_qr_preset_id = fields.Many2one(related="pos_config_id.efn_qr_preset_id", readonly=False)
    pos_efn_qr_confirm_mode = fields.Selection(related="pos_config_id.efn_qr_confirm_mode", readonly=False)
    pos_efn_qr_session_hours = fields.Integer(related="pos_config_id.efn_qr_session_hours", readonly=False)
    pos_efn_qr_max_submissions = fields.Integer(related="pos_config_id.efn_qr_max_submissions", readonly=False)
    pos_efn_qr_max_items = fields.Integer(related="pos_config_id.efn_qr_max_items", readonly=False)
    pos_efn_qr_max_amount = fields.Monetary(related="pos_config_id.efn_qr_max_amount", readonly=False)
    pos_efn_qr_stale_minutes = fields.Integer(related="pos_config_id.efn_qr_stale_minutes", readonly=False)
