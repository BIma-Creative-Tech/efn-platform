"""Stasiun dapur KDS (docs/08 §7.3 E-26, F-52/F-53).

Satu stasiun = satu layar dapur (mis. Dapur Panas, Bar, Expo) untuk satu atau beberapa
``pos.config`` milik company yang sama. Routing baris = kategori POS produk (beserta induknya)
∩ ``category_ids``; stasiun tanpa kategori = stasiun "semua" (expo/pass) yang menerima seluruh
baris — maksimal satu per config.

Kanal realtime: ``pos.bus.mixin`` -> kanal ``"<access_token>-EFN_KDS"`` (token uuid4 acak, temuan
G14 #31: bus Odoo 19 tidak membatasi kanal string, jadi kerahasiaan kanal = token). Token hanya
dibaca server (field ``groups`` manager; controller layar KDS membacanya via ``sudo`` setelah cek
akses) dan tidak pernah dikirim lewat RPC/ data POS. Rotasi: :meth:`action_rotate_token`.

Semua metode publik (RPC-callable) memeriksa grup/akses/company secara eksplisit dan terdaftar di
``efn_core/tests/test_rpc_surface.py::RPC_ALLOWLIST`` (SEC-58).
"""

import hashlib
import logging
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

ONLINE_SECONDS = 60  # heartbeat klien tiap 30 dtk; 2x jeda = dianggap mati
DEVICE_GROUP = "efn_pos_kds.group_kds_device"
MANAGER_GROUP = "point_of_sale.group_pos_manager"
ACTIVE_STATES = ("new", "in_progress", "ready")


def token_fingerprint(token):
    """Sidik token (bukan token) untuk mendeteksi rotasi di klien KDS."""
    return hashlib.sha256((token or "").encode()).hexdigest()[:12]


class EfnKdsStation(models.Model):
    _name = "efn.kds.station"
    _description = "EFN KDS Station"
    _inherit = ["pos.bus.mixin", "pos.load.mixin"]
    _order = "sequence, id"
    _check_company_auto = True

    # Token kanal bus: hanya manager POS yang dapat membaca lewat ORM (user POS/perangkat tidak).
    access_token = fields.Char("Security Token", copy=False, groups=MANAGER_GROUP)

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    config_ids = fields.Many2many(
        "pos.config",
        "efn_kds_station_pos_config_rel",
        "station_id",
        "config_id",
        string="Point of Sale",
        check_company=True,
    )
    category_ids = fields.Many2many(
        "pos.category",
        "efn_kds_station_pos_category_rel",
        "station_id",
        "category_id",
        string="Kategori POS",
        help="Kategori (beserta sub-kategori) yang dirutekan ke stasiun ini. Kosong = semua baris "
        "(stasiun expo/pass), maksimal satu per Point of Sale.",
    )
    mode = fields.Selection(
        [
            ("kds", "Layar KDS (printer hanya cadangan)"),
            ("kds_printer", "Layar KDS + printer dapur"),
            ("printer", "Printer dapur saja (tanpa layar)"),
        ],
        required=True,
        default="kds_printer",
        help="kds: printer cadangan hanya mencetak bila POS offline atau layar KDS mati. "
        "kds_printer: layar dan printer standar sama-sama aktif (default pilot). "
        "printer: perilaku printer dapur standar, tidak ada tiket KDS.",
    )
    fallback_printer_id = fields.Many2one(
        "pos.printer",
        string="Printer cadangan",
        check_company=True,
        help="Printer dapur standar (pos.printer) untuk stasiun ini. Routing kategori printer tetap "
        "memakai pos.printer.product_categories_ids standar.",
    )
    # PLACEHOLDER Q-19 (target menit per stasiun belum diputuskan klien).
    warn_minutes = fields.Integer(default=10)
    late_minutes = fields.Integer(default=15)
    show_course = fields.Boolean(default=True)
    device_user_ids = fields.Many2many(
        "res.users",
        "efn_kds_station_device_user_rel",
        "station_id",
        "user_id",
        string="User perangkat",
        domain=lambda self: [("group_ids", "in", self.env.ref(DEVICE_GROUP).ids)],
        help="User internal khusus layar (grup 'KDS / Perangkat dapur'), satu per layar.",
    )
    last_seen = fields.Datetime(readonly=True, copy=False)
    is_online = fields.Boolean(compute="_compute_is_online")

    _minutes_check = models.Constraint(
        "CHECK (late_minutes >= warn_minutes AND warn_minutes > 0)",
        "Batas terlambat harus >= batas peringatan, dan keduanya > 0.",
    )

    @api.depends("last_seen")
    def _compute_is_online(self):
        threshold = fields.Datetime.now() - timedelta(seconds=ONLINE_SECONDS)
        for station in self:
            station.is_online = bool(station.last_seen and station.last_seen >= threshold)

    @api.constrains("category_ids", "config_ids", "active")
    def _check_single_catch_all(self):
        for station in self.filtered(lambda s: s.active and not s.category_ids):
            for config in station.config_ids:
                others = self.search(
                    [
                        ("id", "!=", station.id),
                        ("config_ids", "in", config.id),
                        ("category_ids", "=", False),
                    ]
                )
                if others:
                    raise ValidationError(
                        _("Point of Sale %s sudah punya stasiun 'semua kategori' (%s).", config.name, others[0].name)
                    )

    @api.constrains("mode", "fallback_printer_id", "config_ids")
    def _check_fallback_printer(self):
        for station in self:
            if station.mode in ("kds", "printer") and not station.fallback_printer_id:
                raise ValidationError(_("Stasiun %s: mode ini memerlukan printer cadangan.", station.name))
            printer = station.fallback_printer_id
            if printer:
                missing = station.config_ids.filtered(lambda c, p=printer: p not in c.printer_ids)
                if missing:
                    raise ValidationError(
                        _(
                            "Printer cadangan %(printer)s belum dipasang di Point of Sale %(configs)s "
                            "(Konfigurasi POS -> Printer dapur).",
                            printer=printer.name,
                            configs=", ".join(missing.mapped("name")),
                        )
                    )

    # ------------------------------------------------------------------ POS data
    @api.model
    def _load_pos_data_domain(self, data, config):
        return [("config_ids", "in", config.id), ("company_id", "=", config.company_id.id)]

    @api.model
    def _load_pos_data_fields(self, config):
        # Tanpa access_token, device_user_ids, last_seen (minimisasi data ke browser POS).
        return ["id", "name", "mode", "fallback_printer_id", "category_ids", "sequence"]

    # ------------------------------------------------------------------ akses
    def _efn_kds_check_device(self):
        """User perangkat stasiun ini ATAU manager POS; stasiun milik company user (SEC-58)."""
        self.ensure_one()
        if self.env.su:
            return
        user = self.env.user
        denied = AccessError(_("Anda tidak berhak memakai layar KDS ini."))
        is_manager = user.has_group(MANAGER_GROUP)
        if not is_manager and not user.has_group(DEVICE_GROUP):
            raise denied
        try:
            self.check_access("read")  # record rule: company + (perangkat) stasiun miliknya
        except AccessError:
            raise denied from None
        if self.company_id not in user.company_ids:
            raise denied
        if not is_manager and user not in self.sudo().device_user_ids:
            raise denied

    def _efn_kds_screen_data(self):
        """Data boot layar KDS (dipanggil controller setelah cek akses). Satu-satunya jalan token."""
        self.ensure_one()
        sudo = self.sudo()
        sudo._ensure_access_token()
        return {
            "station_id": self.id,
            "name": self.name,
            "access_token": sudo.access_token,
            "token_fp": token_fingerprint(sudo.access_token),
            "warn_minutes": self.warn_minutes,
            "late_minutes": self.late_minutes,
            "show_course": self.show_course,
            "server_time": fields.Datetime.to_string(fields.Datetime.now()),
        }

    # ------------------------------------------------------------------ RPC layar KDS
    def efn_kds_load(self, since=False):
        """Tiket aktif stasiun (+ tiket selesai/batal dalam jendela recall) untuk layar KDS.

        ``since`` (string datetime server): hanya tiket yang berubah setelahnya (reconnect).
        Tanpa data pelanggan/harga/pembayaran.
        """
        self._efn_kds_check_device()
        Ticket = self.env["efn.kds.ticket"].sudo()
        recall_from = fields.Datetime.now() - timedelta(minutes=Ticket._efn_recall_minutes())
        domain = [
            ("station_id", "=", self.id),
            "|",
            ("state", "in", ACTIVE_STATES),
            ("write_date", ">=", recall_from),
        ]
        if since:
            try:
                since_dt = fields.Datetime.to_datetime(since)
            except (TypeError, ValueError):
                since_dt = False
            if since_dt:
                domain = [("station_id", "=", self.id), ("write_date", ">=", since_dt)]
        tickets = Ticket.search(domain, order="sent_at, id", limit=500)
        return {
            "server_time": fields.Datetime.to_string(fields.Datetime.now()),
            "tickets": [ticket._efn_kds_payload() for ticket in tickets],
        }

    def efn_kds_heartbeat(self):
        """Heartbeat layar (tiap 30 dtk) -> last_seen; status online dikirim ke POS saat berubah."""
        self._efn_kds_check_device()
        sudo = self.sudo()
        was_online = sudo.is_online
        sudo.last_seen = fields.Datetime.now()
        if not was_online:
            sudo._efn_kds_notify_configs_status(True)
        sudo._ensure_access_token()
        return {
            "server_time": fields.Datetime.to_string(fields.Datetime.now()),
            "token_fp": token_fingerprint(sudo.access_token),
        }

    def action_rotate_token(self):
        """Ganti token kanal bus (manager POS). Layar KDS memuat ulang otomatis (sidik token)."""
        if not self.env.su and not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(_("Hanya manager POS yang boleh merotasi token KDS."))
        self.check_access("write")
        for station in self.sudo():
            station.access_token = str(uuid.uuid4())
        _logger.info("EFN KDS: token stasiun %s dirotasi oleh user %s", self.ids, self.env.uid)
        return True

    def action_open_screen(self):
        self.ensure_one()
        self._efn_kds_check_device()  # tombol form manager; URL tanpa token (controller cek ulang)
        return {"type": "ir.actions.act_url", "url": f"/efn_kds/{self.id}", "target": "new"}

    # ------------------------------------------------------------------ notifikasi
    def _efn_kds_notify(self, kind, ticket_ids):
        """Payload minimal ke kanal stasiun: tanpa isi tiket (klien memuat lewat RPC)."""
        for station in self.sudo():
            station._notify(
                "EFN_KDS",
                {
                    "type": kind,
                    "ticket_ids": list(ticket_ids),
                    "ts": fields.Datetime.to_string(fields.Datetime.now()),
                },
            )

    def _efn_kds_notify_configs_status(self, online):
        for station in self.sudo():
            for config in station.config_ids:
                config._notify("EFN_KDS_STATUS", {"station_id": station.id, "online": bool(online)})

    def _efn_kds_status(self):
        return [{"id": station.id, "mode": station.mode, "is_online": station.is_online} for station in self.sudo()]
