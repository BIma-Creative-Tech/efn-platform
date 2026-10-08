"""Status stasiun KDS untuk POS (cadangan printer F-53) + pemuatan stasiun ke data POS.

SEC-77: notifikasi KDS untuk kasir (``EFN_KDS_READY``, ``EFN_KDS_STATUS``) TIDAK dikirim di kanal
token ``pos.config`` (token itu menjadi isi QR self-order dan dimuat kiosk) melainkan di **kanal
partner user kasir** (``res.partner`` lewat ``bus.listener.mixin``). Kanal partner hanya
dilanggani sesi websocket ter-autentikasi milik user itu (``ir.websocket._build_bus_channel_list``),
bukan berbasis rahasia string. Penerima = user yang memuat POS untuk sesi terbuka config tersebut
(dicatat di ``pos.session.load_data``) + pembuka sesi; harus masih aktif, grup POS user, dan
company config termasuk company-nya. Payload membawa ``config_id`` (klien menyaring per config).
"""

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError

POS_USER_GROUP = "point_of_sale.group_pos_user"
OPEN_SESSION_STATES = ("opening_control", "opened", "closing_control")


class PosConfig(models.Model):
    _inherit = "pos.config"

    def efn_kds_station_status(self):
        """[{id, mode, is_online}] stasiun config ini — dipanggil POS sebelum mencetak dapur.

        Cek SEC-58: grup user POS + record rule config + company user. Tanpa token/data perangkat.
        """
        self.ensure_one()
        if not self.env.su:
            if not self.env.user.has_group(POS_USER_GROUP):
                raise AccessError(_("Anda tidak berhak membaca status KDS."))
            try:
                self.check_access("read")
            except AccessError:
                raise AccessError(_("Anda tidak berhak membaca status KDS.")) from None
            if self.company_id not in self.env.user.company_ids:
                raise AccessError(_("Anda tidak berhak membaca status KDS."))
        stations = (
            self.env["efn.kds.station"]
            .sudo()
            .search([("config_ids", "in", self.id), ("company_id", "=", self.company_id.id)])
        )
        return stations._efn_kds_status()

    def _efn_kds_cashier_users(self):
        self.ensure_one()
        sessions = self.sudo().session_ids.filtered(lambda s: s.state in OPEN_SESSION_STATES)
        users = sessions.user_id | sessions.efn_kds_listener_user_ids
        return users.filtered(
            lambda u: u.active and not u.share and u._has_group(POS_USER_GROUP) and self.company_id in u.company_ids
        )

    def _efn_kds_cashier_send(self, notification_type, payload):
        """Kirim ke kanal partner kasir config ini (bukan kanal token config, SEC-77)."""
        for config in self.sudo():
            message = {**payload, "config_id": config.id}
            for partner in config._efn_kds_cashier_users().partner_id:
                partner._bus_send(notification_type, message)


class PosSession(models.Model):
    _inherit = "pos.session"

    # Penerima notifikasi kasir KDS (SEC-77). Hanya server; tidak dimuat ke POS.
    efn_kds_listener_user_ids = fields.Many2many(
        "res.users",
        "efn_kds_pos_session_listener_rel",
        "session_id",
        "user_id",
        string="Penerima notifikasi KDS",
        readonly=True,
        copy=False,
        groups="point_of_sale.group_pos_manager",
    )

    @api.model
    def _load_pos_data_models(self, config):
        return [*super()._load_pos_data_models(config), "efn.kds.station"]

    def load_data(self, models_to_load):
        """Override CE: catat user yang membuka POS untuk notifikasi kasir KDS."""
        result = super().load_data(models_to_load)
        user = self.env.user
        if not self.env.su and len(self) == 1 and user._has_group(POS_USER_GROUP):
            session = self.sudo()
            if user not in session.efn_kds_listener_user_ids:
                session.efn_kds_listener_user_ids = [Command.link(user.id)]
        return result
