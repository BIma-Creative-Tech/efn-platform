"""Cermin lokal reservasi tukar poin BFF (`platform.loyalty_reservation`, docs/04 §9.3/§9.5).

Dibuat hanya oleh RPC `pos.config.efn_loyalty_redeem_reserve` (sudo terkontrol) setelah OTP
`pos_redeem` terverifikasi. Server memakai reservasi ini untuk memvalidasi baris "Poin EFN"
saat order disinkron (frontend tidak dipercaya, SEC-12).

Data pribadi: hanya nomor HP ter-mask; `bff_reservation_id` hanya untuk admin sistem.
Retensi `efn.pos.loyalty.reservation_retention_days` (default 400, PLACEHOLDER Finance).
"""

import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 400
# K-3: reservasi `reserved` masih bisa di-commit BFF sampai 24 jam; setelah itu kedaluwarsa.
COMMIT_WINDOW_HOURS = 24

RESERVATION_STATES = [
    ("reserved", "Ditahan"),
    ("committed", "Dipakai"),
    ("released", "Dilepas"),
    ("expired", "Kedaluwarsa"),
    ("review", "Perlu review"),
]


class EfnPosLoyaltyReservation(models.Model):
    _name = "efn.pos.loyalty.reservation"
    _description = "EFN POS Loyalty Reservation (tukar Poin EFN)"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    config_id = fields.Many2one(
        "pos.config",
        string="Point of Sale",
        required=True,
        index=True,
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    order_uuid = fields.Char(string="Order (uuid)", required=True, index=True, readonly=True)
    order_id = fields.Many2one(
        "pos.order",
        string="Order",
        index="btree_not_null",
        readonly=True,
        ondelete="set null",
        check_company=True,
    )
    partner_id = fields.Many2one("res.partner", string="Pelanggan", readonly=True, ondelete="set null")
    member_ref_masked = fields.Char(string="Member (mask)", readonly=True)
    otp_challenge_id = fields.Many2one(
        "efn.pos.otp.challenge",
        string="Challenge OTP",
        readonly=True,
        ondelete="set null",
        check_company=True,
    )
    bff_reservation_id = fields.Char(
        string="Reservation ID (BFF)", groups="base.group_system", readonly=True, copy=False, index=True
    )
    points = fields.Integer(required=True, readonly=True)
    value_idr = fields.Integer(string="Nilai (Rp)", required=True, readonly=True)
    # SEC-62: batas % poin terhadap nilai order (parameter BFF §9.1 saat reserve, fallback
    # `efn.pos.loyalty.redeem_max_pct_fallback`). Dicek ulang server saat sync terhadap total
    # barang hasil hitung server (amount_total saat reserve berasal dari klien).
    redeem_max_pct = fields.Integer(string="Batas % order", readonly=True, default=100)
    expires_at = fields.Datetime(readonly=True, help="Batas UI (TTL 15 menit); commit masih diterima BFF s.d. 24 jam.")
    state = fields.Selection(RESERVATION_STATES, required=True, default="reserved", index=True, readonly=True)
    note = fields.Char(readonly=True)

    @api.model
    def _efn_release_key(self):
        """G18-QA-01: kunci idempotensi release tanpa id lokal Odoo (id BFF reservasi = UUID global)."""
        self.ensure_one()
        reservation = self.sudo()
        return f"pos_release:{reservation.bff_reservation_id or reservation.order_uuid}"

    def _efn_find_active(self, config, order_uuid):
        return self.sudo().search(
            [("config_id", "=", config.id), ("order_uuid", "=", order_uuid), ("state", "=", "reserved")],
            order="id desc",
            limit=1,
        )

    @api.model
    def _cron_efn_loyalty_housekeeping(self):
        """Harian: reservasi `reserved` tanpa order paid > 24 jam -> `expired` (BFF melepas sendiri);
        outbox `pending` > 24 jam -> aktivitas ke POS manager (sekali)."""
        now = fields.Datetime.now()
        stale = self.sudo().search(
            [("state", "=", "reserved"), ("create_date", "<", now - timedelta(hours=COMMIT_WINDOW_HOURS))]
        )
        stale.write({"state": "expired", "note": "expired_local"})
        late = (
            self.env["efn.pos.loyalty.outbox"]
            .sudo()
            .search([("state", "=", "pending"), ("create_date", "<", now - timedelta(hours=COMMIT_WINDOW_HOURS))])
        )
        for outbox in late:
            outbox._efn_schedule_activity("pending_over_24h")
        return {"expired": len(stale), "late_outbox": len(late)}

    @api.autovacuum
    def _gc_efn_pos_loyalty_reservations(self):
        params = self.env["ir.config_parameter"].sudo()
        try:
            days = int(params.get_param("efn.pos.loyalty.reservation_retention_days", DEFAULT_RETENTION_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        days = max(days, 30)
        limit_date = fields.Datetime.now() - timedelta(days=days)
        self.sudo().search([("create_date", "<", limit_date), ("state", "!=", "reserved")]).unlink()
