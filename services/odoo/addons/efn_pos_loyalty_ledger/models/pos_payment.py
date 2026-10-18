"""Review r11 SEC-113 (L-25a) khusus Poin EFN: pembayaran metode `efn_is_loyalty_points` hanya dibuat/diubah/dihapus
di jalur sync resmi (`pos.order._process_order`, penanda per cursor `efn_core.scope`), apa pun status order -
termasuk draft (wizard pembayaran backend tidak boleh "membayar dengan poin" tanpa OTP + reservasi BFF), dan
(re-review SEC-126) hanya untuk pembayaran milik order yang sedang disinkron (`_efn_sync_owns`,
`efn_pos_refund_origin`). Kode server ber-`sudo`/superuser dikecualikan. Guard umum ada di `efn_pos_refund_origin`.
"""

from odoo import _, api, models
from odoo.exceptions import UserError

PROTECTED_FIELDS = frozenset({"amount", "payment_method_id", "pos_order_id", "is_change"})


class PosPayment(models.Model):
    _inherit = "pos.payment"

    def _efn_points_guard_active(self, orders):
        """True bila perubahan pembayaran Poin EFN atas `orders` harus ditolak."""
        return not self.env.su and not (self._efn_in_sync() and self._efn_sync_owns(orders))

    def _efn_points_reject(self):
        raise UserError(_("Pembayaran Poin EFN hanya dibuat lewat tombol 'Tukar Poin EFN' (OTP + reservasi)."))

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env["pos.order"].browse(
            [vals.get("pos_order_id") for vals in vals_list if vals.get("pos_order_id")]
        )
        if self._efn_points_guard_active(orders):
            method_ids = [vals.get("payment_method_id") for vals in vals_list if vals.get("payment_method_id")]
            if self.env["pos.payment.method"].sudo().browse(method_ids).filtered("efn_is_loyalty_points"):
                self._efn_points_reject()
        return super().create(vals_list)

    def write(self, vals):
        orders = self.sudo().pos_order_id
        if vals.get("pos_order_id"):
            orders |= self.env["pos.order"].browse(vals["pos_order_id"])
        if PROTECTED_FIELDS.intersection(vals) and self._efn_points_guard_active(orders):
            methods = self.sudo().payment_method_id
            if vals.get("payment_method_id"):
                methods |= self.env["pos.payment.method"].sudo().browse(vals["payment_method_id"])
            if methods.filtered("efn_is_loyalty_points"):
                self._efn_points_reject()
        return super().write(vals)

    def unlink(self):
        if self._efn_points_guard_active(self.sudo().pos_order_id) and self.sudo().payment_method_id.filtered(
            "efn_is_loyalty_points"
        ):
            self._efn_points_reject()
        return super().unlink()
