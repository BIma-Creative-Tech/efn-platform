"""Penyimpanan server-side challenge OTP & discount_token per order POS.

Desain: `challenge_id` dan `discount_token` dari BFF TIDAK pernah dikirim ke browser POS.
Browser hanya menerima `ok` + nomor ter-mask. Saat order di-sync, server mencari token
milik order ini (berdasarkan `pos.order.uuid` = `order_ref` kontrak) dan meng-consume-nya
ke BFF. Ini mencegah kasir/browser memalsukan atau memakai ulang token (replay).

Data pribadi: nomor HP mentah tidak disimpan (hanya versi ter-mask). Retensi default
30 hari (`efn.pos.otp_retention_days`), dibersihkan oleh autovacuum.
"""

from datetime import timedelta

from odoo import api, fields, models

DEFAULT_RETENTION_DAYS = 30
PURPOSE_PROMO = "pos_promo"
PURPOSE_REDEEM = "pos_redeem"
OTP_PURPOSES = [(PURPOSE_PROMO, "Promo"), (PURPOSE_REDEEM, "Tukar poin")]
OTP_PURPOSE_KEYS = frozenset(key for key, _label in OTP_PURPOSES)


class EfnPosOtpChallenge(models.Model):
    _name = "efn.pos.otp.challenge"
    _description = "EFN POS OTP Challenge"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    config_id = fields.Many2one(
        "pos.config",
        string="Point of Sale",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    order_ref = fields.Char(
        string="Order Ref (uuid)",
        required=True,
        index=True,
        help="pos.order.uuid; dipakai sebagai `order_ref` di kontrak BFF.",
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Pelanggan",
        index="btree_not_null",
        ondelete="set null",
        help="Pelanggan order saat OTP diminta (SEC-07). Nomor HP diambil dari partner ini, "
        "bukan diketik kasir. Saat consume, member_ref diambil dari partner FINAL order.",
    )
    # E-23 (docs/08 §6.3 A): challenge tukar poin (pos_redeem) tidak boleh membuka promo dan sebaliknya.
    purpose = fields.Selection(
        OTP_PURPOSES,
        string="Tujuan OTP",
        required=True,
        default=PURPOSE_PROMO,
        index=True,
    )
    member_ref_masked = fields.Char(string="Member (mask)")
    amount = fields.Integer(
        string="Nominal saat OTP (Rp)",
        help="Total order saat OTP diminta; untuk audit/anomali (belum dibandingkan otomatis).",
    )
    bff_challenge_id = fields.Char(groups="base.group_system", copy=False)
    discount_token = fields.Char(groups="base.group_system", copy=False)
    token_expires_at = fields.Datetime(copy=False)
    state = fields.Selection(
        [
            ("requested", "OTP dikirim"),
            ("verified", "Terverifikasi"),
            ("consumed", "Token dipakai"),
            ("failed", "Gagal / terkunci"),
        ],
        default="requested",
        required=True,
        index=True,
    )
    failure_reason = fields.Char(copy=False)
    consumed_order_id = fields.Many2one(
        "pos.order",
        ondelete="set null",
        index="btree_not_null",
        check_company=True,
        copy=False,
    )

    @api.model
    def _efn_find_latest(self, config, order_ref, states, purpose=PURPOSE_PROMO):
        """Challenge terbaru untuk (config, order, purpose). Default `pos_promo`: gate promo
        tidak pernah melihat challenge tukar poin (E-23)."""
        return self.sudo().search(
            [
                ("config_id", "=", config.id),
                ("order_ref", "=", order_ref),
                ("state", "in", list(states)),
                ("purpose", "=", purpose),
            ],
            order="id desc",
            limit=1,
        )

    @api.model
    def _efn_find_valid_verified(self, config, order_ref, purpose=PURPOSE_PROMO):
        """Challenge `verified` yang tokennya belum kedaluwarsa menurut catatan lokal."""
        challenge = self._efn_find_latest(config, order_ref, ["verified"], purpose=purpose)
        if not challenge:
            return challenge
        if challenge.token_expires_at and challenge.token_expires_at < fields.Datetime.now():
            return self.browse()
        return challenge

    @api.autovacuum
    def _gc_efn_pos_otp_challenges(self):
        params = self.env["ir.config_parameter"].sudo()
        try:
            days = int(params.get_param("efn.pos.otp_retention_days", DEFAULT_RETENTION_DAYS))
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        days = max(days, 1)
        limit_date = fields.Datetime.now() - timedelta(days=days)
        self.sudo().search([("create_date", "<", limit_date)]).unlink()
        # Token kedaluwarsa tidak berguna lagi: hapus nilainya lebih awal (minimisasi data).
        self.sudo().search([("discount_token", "!=", False), ("token_expires_at", "<", fields.Datetime.now())]).write(
            {"discount_token": False}
        )
