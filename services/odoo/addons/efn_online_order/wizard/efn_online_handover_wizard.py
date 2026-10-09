"""Serah terima pesanan online di outlet: staf memasukkan kode ambil pelanggan (docs/08 §8.3 C, delta §10.4.4).

- Kode dibandingkan dengan hash pbkdf2-sha512 (``passlib.verify``, waktu konstan); teks kode TIDAK disimpan di
  wizard setelah dicek dan tidak pernah di-log.
- Kode salah TIDAK melempar exception (agar penghitung percobaan ter-commit): wizard dibuka ulang dengan pesan.
  Salah ``pickup_max_attempts`` (default 5) kali -> order ``review`` + aktivitas manajer penjualan.
- Sukses -> picking keluar standar divalidasi (``button_validate``) -> ``picked_up`` -> event ke BFF.
"""

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.efn_online_common import normalize_pickup_code


class EfnOnlineHandoverWizard(models.TransientModel):
    _name = "efn.online.handover.wizard"
    _description = "EFN Online - Serah terima pesanan (kode ambil)"

    order_id = fields.Many2one("efn.online.order", required=True, readonly=True)
    code = fields.Char(string="Kode ambil", help="8 karakter, boleh dengan strip (ABCD-EFGH).")
    message = fields.Char(readonly=True)
    done = fields.Boolean(readonly=True)

    def action_confirm(self):
        """Verifikasi kode ambil lalu serahkan pesanan (grup Inventory user + record rule + company)."""
        self.ensure_one()
        if not self.env.user.has_group("stock.group_stock_user"):
            raise AccessError(_("Hanya staf gudang/outlet yang dapat menyerahkan pesanan online."))
        order = self.order_id
        order.check_access("read")
        if order.company_id not in self.env.user.company_ids:
            raise AccessError(_("Pesanan online milik company lain."))
        if self.done:
            raise UserError(_("Serah terima sudah diproses."))
        code = normalize_pickup_code(self.code)
        ok, message = order.sudo()._efn_handover(code)
        self.sudo().write({"code": False, "message": message, "done": ok})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "name": _("Serah terima pesanan online"),
        }
