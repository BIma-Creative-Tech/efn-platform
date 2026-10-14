"""Review r11 SEC-113 (L-25a) khusus Poin EFN: pembayaran metode `efn_is_loyalty_points` hanya dibuat/diubah/dihapus
di jalur sync resmi (`pos.order._process_order`, penanda per cursor `efn_core.scope`), apa pun status order -
termasuk draft (wizard pembayaran backend tidak boleh "membayar dengan poin" tanpa OTP + reservasi BFF).
Kode server ber-`sudo`/superuser dikecualikan. Guard umum order non-draft ada di `efn_pos_refund_origin`.
"""

from odoo import _, api, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.scope import POS_PAYMENT_SYNC_SCOPE, in_scope

PROTECTED_FIELDS = frozenset({"amount", "payment_method_id", "pos_order_id", "is_change"})


class PosPayment(models.Model):
    _inherit = "pos.payment"

    def _efn_points_guard_active(self):
        return not self.env.su and not in_scope(self.env, POS_PAYMENT_SYNC_SCOPE)

    def _efn_points_reject(self):
        raise UserError(_("Pembayaran Poin EFN hanya dibuat lewat tombol 'Tukar Poin EFN' (OTP + reservasi)."))

    @api.model_create_multi
    def create(self, vals_list):
        if self._efn_points_guard_active():
            method_ids = [vals.get("payment_method_id") for vals in vals_list if vals.get("payment_method_id")]
            if self.env["pos.payment.method"].sudo().browse(method_ids).filtered("efn_is_loyalty_points"):
                self._efn_points_reject()
        return super().create(vals_list)

    def write(self, vals):
        if PROTECTED_FIELDS.intersection(vals) and self._efn_points_guard_active():
            methods = self.sudo().payment_method_id
            if vals.get("payment_method_id"):
                methods |= self.env["pos.payment.method"].sudo().browse(vals["payment_method_id"])
            if methods.filtered("efn_is_loyalty_points"):
                self._efn_points_reject()
        return super().write(vals)

    def unlink(self):
        if self._efn_points_guard_active() and self.sudo().payment_method_id.filtered("efn_is_loyalty_points"):
            self._efn_points_reject()
        return super().unlink()
