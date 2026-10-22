"""Hook status kiriman QR dari tiket KDS (E-27; tanpa mengubah kode efn_pos_kds — cukup _inherit).

Tiket dibuat (pelayan menekan *Order* / terminal mode auto) atau berubah status (bump/selesai/batal) -> status
kiriman QR order terkait dihitung ulang -> event ``submission_status`` ke BFF (outbox).
"""

from odoo import api, models


class EfnKdsTicket(models.Model):
    _inherit = "efn.kds.ticket"

    @api.model
    def _efn_kds_create_for_order(self, order, line_deltas, printed_offline=False):
        tickets = super()._efn_kds_create_for_order(order, line_deltas, printed_offline=printed_offline)
        if tickets:
            self.env["efn.pos.qr.order"].sudo()._efn_refresh_for_orders(order)
        return tickets

    def _efn_kds_after_change(self, kind="update"):
        result = super()._efn_kds_after_change(kind=kind)
        orders = self.sudo().order_id
        if orders:
            self.env["efn.pos.qr.order"].sudo()._efn_refresh_for_orders(orders)
        return result
