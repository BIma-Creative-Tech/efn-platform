"""Review r11 SEC-113 (L-25a): pembayaran order POS hanya berubah lewat jalur sync/refund resmi.

ACL inti CE memberi `point_of_sale.group_pos_user` CRUD penuh `pos.payment` (terverifikasi `call_kw` kasir di
Odoo 19 nyata: write metode/nominal, create, unlink pada order paid semuanya DITERIMA sebelum perbaikan ini).
Akibatnya validasi saat sync (E-30 Poin EFN, E-31 metode asal) bisa dilewati setelah order tersimpan.

Aturan (di atas ACL):
- `create`, `unlink`, dan `write` atas `amount`, `payment_method_id`, `pos_order_id`, `is_change` untuk pembayaran
  order yang **bukan draft** ditolak, kecuali berjalan di jalur sync resmi (`pos.order._process_order`, penanda
  per cursor `efn_core.scope`, tidak bisa diisi context RPC) atau oleh kode server ber-`sudo`/superuser
  (penutupan sesi, pembayaran online/kiosk core, cron).
- Order draft tetap mengikuti alur standar (mis. wizard pembayaran backend); validasi E-30/E-31 berjalan saat
  order dibayar/disinkron.
"""

from odoo import _, api, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.scope import POS_PAYMENT_SYNC_SCOPE, in_scope

PROTECTED_FIELDS = frozenset({"amount", "payment_method_id", "pos_order_id", "is_change"})


class PosPayment(models.Model):
    _inherit = "pos.payment"

    def _efn_payment_guard_active(self):
        return not self.env.su and not in_scope(self.env, POS_PAYMENT_SYNC_SCOPE)

    def _efn_payment_locked_orders(self, orders):
        return orders.sudo().filtered(lambda o: o.state != "draft")

    def _efn_payment_reject(self):
        raise UserError(
            _(
                "Pembayaran order POS yang sudah dibayar tidak dapat diubah. Gunakan refund dari layar POS "
                "(review keamanan SEC-113)."
            )
        )

    @api.model_create_multi
    def create(self, vals_list):
        if self._efn_payment_guard_active():
            order_ids = {vals.get("pos_order_id") for vals in vals_list if vals.get("pos_order_id")}
            if self._efn_payment_locked_orders(self.env["pos.order"].browse(list(order_ids))):
                self._efn_payment_reject()
        return super().create(vals_list)

    def write(self, vals):
        if PROTECTED_FIELDS.intersection(vals) and self._efn_payment_guard_active():
            orders = self.sudo().pos_order_id
            if vals.get("pos_order_id"):
                orders |= self.env["pos.order"].browse(vals["pos_order_id"])
            if self._efn_payment_locked_orders(orders):
                self._efn_payment_reject()
        return super().write(vals)

    def unlink(self):
        if self._efn_payment_guard_active() and self._efn_payment_locked_orders(self.sudo().pos_order_id):
            self._efn_payment_reject()
        return super().unlink()
