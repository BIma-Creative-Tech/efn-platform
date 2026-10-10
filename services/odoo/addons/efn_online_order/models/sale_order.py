"""Tautan SO <-> pesanan online + hook pembatalan dari UI Odoo (event ``cancelled`` initiator ``odoo``).

Tidak ada metode publik baru (gerbang SEC-58): hanya override privat ``_action_cancel`` / ``_action_done``.
SEC-95 (r10): batal bagian berbayar hanya Sales manager + alasan; picking keluar SO online hanya divalidasi lewat
wizard serah terima (kode ambil). UI CE 19: tombol *Cancel* SO hanya dialog konfirmasi JS generik (tanpa cek
faktur/grup); faktur ter-posting tidak dibatalkan CE (hanya draf) -> kontrol ada di hook ini.
BFF tidak pernah memakai ``get_portal_url``/``access_token`` SO (route portal ``/my/orders``, temuan #38).
"""

from odoo import _, fields, models
from odoo.exceptions import UserError

from .efn_online_order import HANDOVER_CACHE_KEY


class SaleOrder(models.Model):
    _inherit = "sale.order"

    efn_online_order_id = fields.Many2one(
        "efn.online.order", string="Pesanan online", readonly=True, index="btree_not_null", copy=False
    )

    efn_online_cancel_note = fields.Char(
        string="Alasan batal (online)",
        copy=False,
        groups="sales_team.group_sale_manager",
        help="Wajib (min. 10 karakter) untuk membatalkan pesanan online yang sudah dibayar (SEC-95). "
        "Hanya tercatat di Odoo; tidak dikirim ke BFF.",
    )

    def _action_cancel(self):
        online_by_so = {}
        if not self.env.context.get("efn_online_internal"):
            # SEC-95: cek grup memakai user ASLI (bukan sudo) sebelum hook berjalan dengan sudo.
            for order in self.filtered(lambda so: so.sudo().efn_online_order_id):
                note = order.sudo().efn_online_cancel_note
                online = order.sudo().efn_online_order_id
                online.with_user(self.env.user)._efn_ops_cancel_check(note)
                online_by_so[order.id] = (online, note)
        result = super()._action_cancel()
        for online, note in online_by_so.values():
            online._efn_ops_cancelled(reason_note=note, actor=self.env.user)
        return result


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _action_done(self):
        """SEC-95: pengiriman SO online tidak boleh divalidasi di luar wizard serah terima (melewati kode ambil).
        Penanda ada di cache transaksi cursor (diisi ``_efn_handover`` setelah kode valid), bukan context klien."""
        allowed = self.env.cr.cache.get(HANDOVER_CACHE_KEY) or set()
        for picking in self:
            if (
                picking.picking_type_code == "outgoing"
                and picking.sudo().sale_id.efn_online_order_id
                and picking.id not in allowed
            ):
                raise UserError(
                    _(
                        "Pengiriman %(name)s milik pesanan online hanya dapat divalidasi lewat "
                        "'Serahkan (kode ambil)'.",
                        name=picking.name,
                    )
                )
        return super()._action_done()
