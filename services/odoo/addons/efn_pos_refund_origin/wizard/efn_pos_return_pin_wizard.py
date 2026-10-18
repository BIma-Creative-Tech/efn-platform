"""E-41: wizard atur PIN persetujuan retur tanpa struk (Store Manager).

SEC-151 (19.0.1.4.0): teks PIN **tidak pernah** tersimpan di tabel transient. Klik tombol didahului ``web_save``
(transaksi sendiri yang di-commit) sehingga mengosongkan PIN di ``action_apply`` tidak cukup: bila ``action_apply``
gagal, rollback-nya tidak menyentuh baris yang sudah di-commit. Karena itu ``create``/``write`` wizard membuang
``pin``/``pin_confirm`` dari ``vals`` dan langsung menggantinya dengan hasil validasi:

- ``pin_hash`` = hash pbkdf2-sha512 PIN (hanya bila format benar, tidak lemah, dan konfirmasi cocok);
- ``pin_error`` = ``format`` | ``weak`` | ``mismatch`` (tanpa hash).

``pin_hash``/``pin_error`` yang dikirim klien selalu diabaikan (dihitung ulang dari ``pin``/``pin_confirm`` di vals
yang sama), sehingga validasi tidak bisa dilewati lewat RPC. Kolom ``pin``/``pin_confirm`` tetap ada (form view)
tetapi selalu NULL. Transient tidak memakai ``company_id`` (data milik user, bukan per PT) — akses record transient
dibatasi Odoo ke pembuatnya.

``action_apply`` (RPC, di ``RPC_ALLOWLIST``):
- identity check Odoo < 10 menit di sesi HTTP (``identity-check-last``, sama dengan ``@check_identity``) — wizard
  tidak bisa dibuat + dijalankan lewat RPC tanpa konfirmasi ulang kata sandi;
- target = diri sendiri atau ``base.group_system``; target harus user internal aktif dengan grup POS manager;
- PIN 6–8 digit, bukan deret/semua sama, konfirmasi cocok (dinilai saat create/write); hash ditulis ``sudo`` lalu
  ``pin_hash`` wizard dikosongkan.
"""

import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.return_pin import PIN_MAX_INPUT, PIN_RE, hash_pin, pin_is_weak

_logger = logging.getLogger(__name__)

IDENTITY_CHECK_SECONDS = 10 * 60
PIN_FIELDS = ("pin", "pin_confirm")
DERIVED_FIELDS = ("pin_hash", "pin_error")


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
    # SEC-151: selalu NULL di DB (dibuang create/write); hanya wadah input form.
    pin = fields.Char(string="PIN baru (6–8 digit)")
    pin_confirm = fields.Char(string="Ulangi PIN")
    pin_hash = fields.Char(readonly=True, copy=False)
    pin_error = fields.Selection(
        [("format", "Format"), ("weak", "Lemah"), ("mismatch", "Tidak cocok")], readonly=True, copy=False
    )

    # ------------------------------------------------------------ SEC-151
    @api.model
    def _efn_pin_vals(self, vals):
        """Salinan ``vals`` tanpa teks PIN; ``pin_hash``/``pin_error`` dihitung server dari PIN di vals ini."""
        if not any(name in vals for name in PIN_FIELDS + DERIVED_FIELDS):
            return vals
        vals = dict(vals)
        raw_pin = vals.pop("pin", None)
        raw_confirm = vals.pop("pin_confirm", None)
        for name in DERIVED_FIELDS:
            vals.pop(name, None)
        pin = raw_pin.strip() if isinstance(raw_pin, str) and len(raw_pin) <= PIN_MAX_INPUT else ""
        confirm = raw_confirm.strip() if isinstance(raw_confirm, str) and len(raw_confirm) <= PIN_MAX_INPUT else ""
        if not PIN_RE.match(pin):
            error = "format"
        elif pin_is_weak(pin):
            error = "weak"
        elif pin != confirm:
            error = "mismatch"
        else:
            error = False
        vals["pin_error"] = error
        vals["pin_hash"] = False if error else hash_pin(self.env, pin)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._efn_pin_vals(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._efn_pin_vals(vals))

    # ------------------------------------------------------------- apply
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
        wizard = self.sudo()
        if wizard.pin_error == "weak":
            raise UserError(_("PIN terlalu mudah ditebak (deret atau angka sama). Pilih PIN lain."))
        if wizard.pin_error == "mismatch":
            raise UserError(_("Konfirmasi PIN tidak cocok."))
        pin_hash = wizard.pin_hash
        if wizard.pin_error or not pin_hash:
            raise UserError(_("PIN harus 6–8 digit angka."))
        target.write({"efn_return_pin_hash": pin_hash, "efn_return_pin_set_at": fields.Datetime.now()})
        # Hash kandidat tidak dibiarkan di transient (dipakai sekali). Lewat write() -> dihitung ulang -> kosong.
        wizard.write({"pin_hash": False})
        _logger.info("EFN return approval: PIN user %s diatur oleh user %s", target.id, self.env.uid)
        return {"type": "ir.actions.act_window_close"}
