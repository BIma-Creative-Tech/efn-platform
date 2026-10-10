"""SEC-105 (L-19 b): SO online dikeluarkan dari POS (pos_sale). Barang online hanya keluar lewat kode ambil.

- ``_load_pos_data_domain``: SO ber-``efn_online_order_id`` tidak pernah ikut data POS.
- ``load_sale_order_from_pos`` (metode publik CE ``pos_sale``, dipanggil POS saat memilih SO): ditolak untuk SO
  online -> daftar/ settle/ uang muka di POS tidak bisa memuatnya walau domain klien diubah.
- Jaring terakhir di ``efn_online_order``: ``pos.order.line.create`` menolak ``sale_order_origin_id`` SO online dan
  ``stock.move._action_done`` menolak move SO online ke pelanggan di luar wizard serah terima.
"""

from odoo import _, api, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.model
    def _load_pos_data_domain(self, data, config):
        return [*super()._load_pos_data_domain(data, config), ("efn_online_order_id", "=", False)]

    def load_sale_order_from_pos(self, config_id):
        if self.sudo().filtered("efn_online_order_id"):
            raise UserError(_("Pesanan online tidak dapat diproses di POS; serahkan lewat 'Serahkan (kode ambil)'."))
        return super().load_sale_order_from_pos(config_id)
