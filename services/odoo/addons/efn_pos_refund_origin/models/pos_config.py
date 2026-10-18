"""E-31: RPC alokasi refund ke metode asal untuk layar bayar refund POS.

Metode publik `efn_refund_origin_allocation` terdaftar di `RPC_ALLOWLIST` (`efn_core/tests/test_rpc_surface.py`):
cek = grup POS user + `check_access('read')` config + company user + sesi POS terbuka; order asal dibaca dengan
hak user (record rule) lalu dicek company-nya. Hanya mengembalikan id metode & nominal (tanpa data pelanggan).
"""

from odoo import _, models
from odoo.exceptions import AccessError, UserError


class PosConfig(models.Model):
    _inherit = "pos.config"

    def _efn_refund_check_caller(self):
        self.ensure_one()
        if not self.env.user.has_group("point_of_sale.group_pos_user"):
            raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        if not self.env.su:
            try:
                self.check_access("read")
            except AccessError:
                raise AccessError(_("Anda tidak berhak memakai fitur ini.")) from None
            if self.company_id not in self.env.user.company_ids:
                raise AccessError(_("Anda tidak berhak memakai fitur ini."))
        session = self.current_session_id
        if not session or session.state != "opened":
            raise UserError(_("Sesi POS belum dibuka."))

    def efn_refund_origin_allocation(self, original_order_id, refund_amount):
        """Metode & nominal default refund atas order asal (E-31).

        :param int original_order_id: order asal.
        :param float refund_amount: nilai refund (positif, total order refund termasuk pajak).
        :returns: ``{"ok": True, "allocation": [{"payment_method_id", "amount", "max_amount"}]}`` atau
            ``{"ok": False, "error_code"}``.
        """
        self._efn_refund_check_caller()
        if isinstance(original_order_id, bool) or not isinstance(original_order_id, int):
            return {"ok": False, "error_code": "invalid_order"}
        if isinstance(refund_amount, bool) or not isinstance(refund_amount, (int, float)) or refund_amount < 0:
            return {"ok": False, "error_code": "invalid_amount"}
        original = self.env["pos.order"].browse(original_order_id).exists()
        if not original:
            return {"ok": False, "error_code": "invalid_order"}
        try:
            original.check_access("read")
        except AccessError:
            return {"ok": False, "error_code": "invalid_order"}
        original = original.sudo()
        if original.company_id != self.company_id:
            return {"ok": False, "error_code": "invalid_order"}
        rows = original._efn_refund_allocation(float(refund_amount))
        return {
            "ok": True,
            "allocation": [
                {"payment_method_id": method.id, "amount": amount, "max_amount": remaining}
                for method, amount, remaining in rows
            ],
        }
