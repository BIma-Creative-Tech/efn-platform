"""SEC-75 (r8): kunci teknis mode self-order ``pos_self_order`` ke ``nothing``/``kiosk``.

``pos_self_order`` (dipasang modul ini dan auto-install bersama ``pos_restaurant``) membuka route
``/pos-self*`` ``auth='public'`` tanpa rate limit untuk mode ``mobile`` (QR menu + pesan) dan
``consultation`` (QR menu). Sampai ADR Q-23 diputuskan, kedua mode itu DITOLAK kecuali
``ir.config_parameter`` ``efn.pos.self_order_mobile_allowed`` bernilai benar. Parameter hanya dapat
diubah ``base.group_system`` (ACL CE) dan selalu diaudit ``efn_audit`` sebagai parameter kontrol
(``CONTROL_SPECS``). Kiosk tetap boleh (perangkat outlet, ADR-013).

Terverifikasi di image 19.0 (2026-10-09): default field ``self_ordering_mode`` = ``nothing`` dan
``create`` tidak mengisi ``mobile`` (anggapan PLAUSIBLE r8 tidak berlaku di 19.0). Tetap dipasang
``ir.default`` + constraint (pertahanan berlapis bila perilaku CE berubah di rilis berikutnya).
"""

import logging

from odoo import _, api, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

SELF_ORDER_PARAM = "efn.pos.self_order_mobile_allowed"
SELF_ORDER_ALLOWED_MODES = ("nothing", "kiosk")
TRUTHY = ("1", "true", "yes", "on")


def param_truthy(value):
    return str(value or "").strip().lower() in TRUTHY


class PosConfig(models.Model):
    _inherit = "pos.config"

    @api.model
    def _efn_self_order_mobile_allowed(self):
        return param_truthy(self.env["ir.config_parameter"].sudo().get_param(SELF_ORDER_PARAM))

    @api.constrains("self_ordering_mode")
    def _check_efn_self_order_locked(self):
        if self._efn_self_order_mobile_allowed():
            return
        locked = self.filtered(lambda c: c.self_ordering_mode not in SELF_ORDER_ALLOWED_MODES)
        if locked:
            raise ValidationError(
                _(
                    "Mode self-order QR ponsel/QR menu belum diizinkan (SEC-75, menunggu ADR Q-23). "
                    "Gunakan 'Disable' atau 'Kiosk'. Point of Sale: %s",
                    ", ".join(locked.mapped("name")),
                )
            )

    @api.model
    def _efn_normalize_self_order_mode(self):
        """Install/upgrade (data noupdate=0) & saat sakelar dimatikan: config mobile/QR menu -> nothing."""
        if self._efn_self_order_mobile_allowed():
            return self.browse()
        configs = (
            self.sudo()
            .with_context(active_test=False)
            .search([("self_ordering_mode", "not in", SELF_ORDER_ALLOWED_MODES)])
        )
        if configs:
            _logger.info("EFN SEC-75: self-order config %s dinormalkan ke 'nothing'", configs.ids)
            configs.write({"self_ordering_mode": "nothing"})
        return configs
