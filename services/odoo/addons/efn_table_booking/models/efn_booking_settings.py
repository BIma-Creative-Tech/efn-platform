"""Pengaturan reservasi meja per outlet online (docs/04 §15.2, L-85).

Satu record per ``efn.online.outlet``. Kapasitas berasal dari meja nyata ``pos_restaurant`` (lantai yang memuat
``pos_config_id``), jam buka dari ``calendar_id`` (``resource.calendar``). Semua angka configurable (default = jawaban
sementara Q-BK2..Q-BK4: durasi 90 menit, maks 8 tamu, konfirmasi staf).
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.efn_core.scope import touch_row


class EfnBookingSettings(models.Model):
    _name = "efn.booking.settings"
    _description = "EFN - Pengaturan reservasi meja per outlet"
    _order = "outlet_id"
    _check_company_auto = True
    _rec_name = "outlet_id"

    outlet_id = fields.Many2one(
        "efn.online.outlet", required=True, ondelete="cascade", index=True, string="Outlet online"
    )
    company_id = fields.Many2one(related="outlet_id.company_id", store=True, index=True, readonly=True)
    active = fields.Boolean(default=True, help="Nonaktif = reservasi online outlet ini ditolak (booking_disabled).")
    pos_config_id = fields.Many2one(
        "pos.config",
        string="POS restoran",
        required=True,
        check_company=True,
        domain="[('module_pos_restaurant', '=', True)]",
        help="Meja yang dapat dipesan = meja aktif di lantai POS ini.",
    )
    calendar_id = fields.Many2one(
        "resource.calendar", string="Jam buka", required=True, check_company=True, help="Zona waktu = zona kalender."
    )
    slot_minutes = fields.Selection([("15", "15 menit"), ("30", "30 menit"), ("60", "60 menit")], default="30", required=True)
    duration_minutes = fields.Integer(string="Durasi (menit)", default=90, required=True)
    lead_minutes = fields.Integer(string="Batas pesan minimal (menit sebelum)", default=60, required=True)
    horizon_days = fields.Integer(string="Maks hari ke depan", default=14, required=True)
    min_party = fields.Integer(string="Tamu minimal", default=1, required=True)
    max_party = fields.Integer(string="Tamu maksimal", default=8, required=True)
    confirm_mode = fields.Selection(
        [("staff", "Dikonfirmasi staf"), ("auto", "Otomatis terkonfirmasi")], default="staff", required=True
    )
    hold_minutes = fields.Integer(
        string="Meja ditahan (menit)", default=15, required=True, help="Toleransi telat sebelum staf boleh 'Tidak datang'."
    )

    _outlet_unique = models.Constraint("unique (outlet_id)", "Satu pengaturan reservasi per outlet.")

    @api.constrains("duration_minutes", "lead_minutes", "horizon_days", "min_party", "max_party", "hold_minutes")
    def _check_efn_limits(self):
        for rec in self:
            if not 30 <= rec.duration_minutes <= 360:
                raise ValidationError(_("Durasi reservasi harus 30-360 menit."))
            if rec.lead_minutes < 0 or rec.hold_minutes < 0:
                raise ValidationError(_("Batas pesan dan waktu tahan tidak boleh negatif."))
            if not 1 <= rec.horizon_days <= 60:
                raise ValidationError(_("Maks hari ke depan harus 1-60."))
            if not 1 <= rec.min_party <= rec.max_party <= 50:
                raise ValidationError(_("Jumlah tamu: 1 <= minimal <= maksimal <= 50."))

    @api.constrains("pos_config_id", "outlet_id")
    def _check_efn_company(self):
        for rec in self:
            if rec.pos_config_id.company_id != rec.outlet_id.company_id:
                raise ValidationError(_("POS restoran harus milik PT yang sama dengan outlet."))
            if not rec.outlet_id.channel_id._efn_online_is_fnb():
                raise ValidationError(_("Reservasi meja hanya untuk kanal F&B."))

    def _efn_lock(self):
        """Serialisasi pembuatan reservasi per outlet (cek bentrok di bawah kunci, docs/04 §15.2).

        ``FOR UPDATE`` saja tidak cukup di REPEATABLE READ (SEC-127): ``touch_row`` membuat versi baris baru sehingga
        transaksi paralel gagal ``SerializationFailure`` dan Odoo mengulang RPC dengan snapshot baru."""
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute("SELECT id FROM efn_booking_settings WHERE id = %s FOR UPDATE", (self.id,))
        touch_row(self.env.cr, "efn_booking_settings", self.id)
        self.invalidate_recordset()
        return self

    def _efn_tables(self):
        """Meja aktif di lantai POS restoran (pos_restaurant standar)."""
        self.ensure_one()
        floors = self.env["restaurant.floor"].sudo().search([("pos_config_ids", "in", self.pos_config_id.ids)])
        return self.env["restaurant.table"].sudo().search(
            [("floor_id", "in", floors.ids), ("active", "=", True), ("seats", ">", 0)], order="seats, id"
        )
