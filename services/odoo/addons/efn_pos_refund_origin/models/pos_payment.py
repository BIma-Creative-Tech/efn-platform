"""Review r11 SEC-113 (L-25a) + re-review G18 SEC-126: pembayaran order POS hanya berubah lewat jalur resmi.

ACL inti CE memberi `point_of_sale.group_pos_user` CRUD penuh `pos.payment` (terverifikasi `call_kw` kasir di
Odoo 19 nyata: write metode/nominal, create, unlink pada order paid semuanya DITERIMA sebelum perbaikan r11).
Akibatnya validasi saat sync (E-30 Poin EFN, E-31 metode asal) bisa dilewati setelah order tersimpan.

Aturan (di atas ACL, untuk non-superuser):
- Di dalam sync resmi (`pos.order._process_order`, penanda per cursor `efn_core.scope`, tidak bisa diisi context
  RPC): hanya pembayaran milik order YANG SEDANG DISINKRON (token id/uuid, SEC-126) yang boleh dibuat/diubah/
  dihapus. Perintah x2many atau relasi payload yang menunjuk pembayaran order lain ditolak.
- Di luar sync: `create`, `unlink`, dan `write` atas `amount`, `payment_method_id`, `pos_order_id`, `is_change`
  untuk pembayaran order yang **bukan draft** ditolak. Order draft mengikuti alur standar (wizard pembayaran
  backend); validasi lengkap berjalan saat order dilunasi (`_process_saved_order`, SEC-125).
- Kode server ber-`sudo`/superuser dikecualikan (penutupan sesi, pembayaran online core, cron).
"""

from odoo import _, api, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.scope import POS_PAYMENT_SYNC_SCOPE, in_scope, scope_tokens

PROTECTED_FIELDS = frozenset({"amount", "payment_method_id", "pos_order_id", "is_change"})


class PosPayment(models.Model):
    _inherit = "pos.payment"

    # ----------------------------------------------------------------- helpers
    def _efn_in_sync(self):
        return in_scope(self.env, POS_PAYMENT_SYNC_SCOPE)

    def _efn_sync_owns(self, orders):
        """True bila semua `orders` adalah order yang sedang disinkron (SEC-126). Dipakai juga guard Poin EFN."""
        tokens = scope_tokens(self.env, POS_PAYMENT_SYNC_SCOPE)
        return bool(tokens) and all(
            ("id", order.id) in tokens or ("uuid", order.uuid) in tokens for order in orders.sudo()
        )

    def _efn_payment_locked_orders(self, orders):
        return orders.sudo().filtered(lambda o: o.state != "draft")

    def _efn_payment_allowed(self, orders):
        """Boleh menyentuh pembayaran milik `orders`? (create: order tujuan; write: lama + baru; unlink: lama)."""
        if self.env.su:
            return True
        if self._efn_in_sync():
            return self._efn_sync_owns(orders)
        return not self._efn_payment_locked_orders(orders)

    def _efn_payment_reject(self):
        raise UserError(
            _(
                "Pembayaran order POS yang sudah dibayar (atau milik order lain) tidak dapat diubah. Gunakan refund "
                "dari layar POS (review keamanan SEC-113/SEC-126)."
            )
        )

    # --------------------------------------------------------------------- ORM
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            order_ids = {vals.get("pos_order_id") for vals in vals_list}
            orders = self.env["pos.order"].browse([i for i in order_ids if i])
            if (None in order_ids or False in order_ids) and self._efn_in_sync():
                # Pembayaran tanpa order di dalam sync: tidak pernah dibuat core.
                self._efn_payment_reject()
            if not self._efn_payment_allowed(orders):
                self._efn_payment_reject()
        return super().create(vals_list)

    def write(self, vals):
        if PROTECTED_FIELDS.intersection(vals) and not self.env.su:
            orders = self.sudo().pos_order_id
            if vals.get("pos_order_id"):
                orders |= self.env["pos.order"].browse(vals["pos_order_id"])
            if not self._efn_payment_allowed(orders):
                self._efn_payment_reject()
        return super().write(vals)

    def unlink(self):
        if not self.env.su and not self._efn_payment_allowed(self.sudo().pos_order_id):
            self._efn_payment_reject()
        return super().unlink()
