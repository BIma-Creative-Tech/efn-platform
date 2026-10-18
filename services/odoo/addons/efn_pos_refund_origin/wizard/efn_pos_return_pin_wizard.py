"""E-41: wizard atur PIN persetujuan retur tanpa struk (Store Manager).

Pola wizard ganti kata sandi Odoo (``change.password.user``): PIN diketik di field transient lalu langsung
dikosongkan setelah hash ditulis. Transient tidak memakai ``company_id`` (data milik user, bukan per PT) — akses
record transient dibatasi Odoo ke pembuatnya.

``action_apply`` (RPC, di ``RPC_ALLOWLIST``):
- identity check Odoo < 10 menit di sesi HTTP (``identity-check-last``, sama dengan ``@check_identity``) — wizard
  tidak bisa dibuat + dijalankan lewat RPC tanpa konfirmasi ulang kata sandi;
- target = diri sendiri atau ``base.group_system``; target harus user internal aktif dengan grup POS manager;
- PIN 6–8 digit, bukan deret/semua sama, konfirmasi cocok; hash pbkdf2-sha512 ditulis ``sudo``.
"""

import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.return_pin import PIN_RE, hash_pin, pin_is_weak

_logger = logging.getLogger(__name__)

IDENTITY_CHECK_SECONDS = 10 * 60


class EfnPosReturnPinWizard(models.TransientModel):
    _name = "efn.pos.return.pin.wizard"
    _description = "EFN: Atur PIN Persetujuan Retur"

    user_id = fields.Many2one(
        "res.users",
        string="Store Manager",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
        ondelete="cascade",
    )
    pin = fields.Char(string="PIN baru (6–8 digit)")
    pin_confirm = fields.Char(string="Ulangi PIN")

    @api.model
    def _efn_require_recent_identity(self):
        """Konfirmasi kata sandi terbaru di sesi HTTP (identity check Odoo). Superuser/kode server: lewat."""
        if self.env.su:
            return
        last = request.session.get("identity-check-last", 0) if request else 0
        if last <= time.time() - IDENTITY_CHECK_SECONDS:
            raise UserError(_("Konfirmasi ulang kata sandi Anda: buka lagi menu 'Atur PIN persetujuan retur'."))

    def action_apply(self):
        self.ensure_one()
        self._efn_require_recent_identity()
        user = self.user_id
        if not self.env.su and not user._efn_can_manage_return_pin():
            raise AccessError(_("Anda hanya dapat mengatur PIN persetujuan retur milik Anda sendiri."))
        target = user.sudo()
        if not target.active or target.share or not target.has_group("point_of_sale.group_pos_manager"):
            raise UserError(_("PIN persetujuan retur hanya untuk Store Manager (grup Manajer POS)."))
        pin = (self.pin or "").strip()
        confirm = (self.pin_confirm or "").strip()
        if not PIN_RE.match(pin):
            raise UserError(_("PIN harus 6–8 digit angka."))
        if pin_is_weak(pin):
            raise UserError(_("PIN terlalu mudah ditebak (deret atau angka sama). Pilih PIN lain."))
        if pin != confirm:
            raise UserError(_("Konfirmasi PIN tidak cocok."))
        target.write({"efn_return_pin_hash": hash_pin(self.env, pin), "efn_return_pin_set_at": fields.Datetime.now()})
        # Teks PIN di tabel transient dikosongkan di transaksi yang sama (pola wizard kata sandi Odoo). Bila validasi
        # gagal (UserError -> rollback), baris transient dihapus vacuum Odoo (batasan, README).
        self.sudo().write({"pin": False, "pin_confirm": False})
        _logger.info("EFN return approval: PIN user %s diatur oleh user %s", target.id, self.env.uid)
        return {"type": "ir.actions.act_window_close"}
