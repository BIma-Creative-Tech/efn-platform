"""E-41 (docs/04 §12.4, L-51): PIN persetujuan retur tanpa struk milik Store Manager, disimpan server-side.

- ``efn_return_pin_hash``: hash pbkdf2-sha512, ``groups="base.group_system"`` (tidak terbaca POS manager, kasir,
  maupun data POS di browser). Hanya ditulis lewat wizard ``efn.pos.return.pin.wizard`` (sudo setelah cek).
- ``efn_action_return_pin_wizard`` (RPC, di ``RPC_ALLOWLIST``): ``@check_identity`` Odoo (konfirmasi ulang kata
  sandi, sesi < 10 menit) + target = diri sendiri atau ``base.group_system``; hanya membuka wizard.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

from odoo.addons.base.models.res_users import check_identity


class ResUsers(models.Model):
    _inherit = "res.users"

    efn_return_pin_hash = fields.Char(
        string="Hash PIN Persetujuan Retur",
        groups="base.group_system",
        copy=False,
        help="pbkdf2-sha512 (passlib). PIN asli tidak disimpan. Diisi lewat wizard 'Atur PIN persetujuan retur'.",
    )
    efn_return_pin_set_at = fields.Datetime(string="PIN Persetujuan Retur Diatur", copy=False, readonly=True)
    efn_return_pin_is_set = fields.Boolean(
        string="PIN Persetujuan Retur Aktif",
        compute="_compute_efn_return_pin_is_set",
        compute_sudo=True,
    )

    @api.depends("efn_return_pin_hash")
    def _compute_efn_return_pin_is_set(self):
        for user in self:
            user.efn_return_pin_is_set = bool(user.efn_return_pin_hash)

    def _efn_can_manage_return_pin(self):
        """User aktif boleh mengatur PIN `self`: diri sendiri atau admin sistem."""
        self.ensure_one()
        return self == self.env.user or self.env.user.has_group("base.group_system")

    @check_identity
    def efn_action_return_pin_wizard(self):
        self.ensure_one()
        if not self.env.su and not self._efn_can_manage_return_pin():
            raise AccessError(_("Anda hanya dapat mengatur PIN persetujuan retur milik Anda sendiri."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Atur PIN persetujuan retur"),
            "res_model": "efn.pos.return.pin.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_user_id": self.id},
        }
