"""Tautan SO <-> pesanan online + hook pembatalan dari UI Odoo (event ``cancelled`` initiator ``odoo``).

Tidak ada metode publik baru (gerbang SEC-58): hanya override privat ``_action_cancel``.
BFF tidak pernah memakai ``get_portal_url``/``access_token`` SO (route portal ``/my/orders``, temuan #38).
"""

from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    efn_online_order_id = fields.Many2one(
        "efn.online.order", string="Pesanan online", readonly=True, index="btree_not_null", copy=False
    )

    def _action_cancel(self):
        online = self.env["efn.online.order"]
        if not self.env.context.get("efn_online_internal"):
            online = self.sudo().efn_online_order_id
            online._efn_ops_cancel_check()
        result = super()._action_cancel()
        if online:
            online._efn_ops_cancelled()
        return result
