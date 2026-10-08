"""SEC-75: mematikan sakelar ``efn.pos.self_order_mobile_allowed`` langsung menormalkan config."""

from odoo import api, models

from .pos_config import SELF_ORDER_PARAM


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.model_create_multi
    def create(self, vals_list):
        params = super().create(vals_list)
        params._efn_self_order_param_changed()
        return params

    def write(self, vals):
        res = super().write(vals)
        if "value" in vals or "key" in vals:
            self._efn_self_order_param_changed()
        return res

    def unlink(self):
        touched = any(param.key == SELF_ORDER_PARAM for param in self)
        res = super().unlink()
        if touched:
            self.env["pos.config"]._efn_normalize_self_order_mode()
        return res

    def _efn_self_order_param_changed(self):
        if any(param.key == SELF_ORDER_PARAM for param in self):
            self.env["pos.config"]._efn_normalize_self_order_mode()
