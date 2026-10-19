"""Jejak lookup kode bayar di kasir (rate limit + klaim yang diwajibkan saat sync). Tanpa kode, tanpa data pelanggan.

- Setiap panggilan ``pos.config.efn_online_counter_lookup`` = satu baris (berhasil/gagal) -> rate limit per sesi POS
  dan per kasir (``efn_online_counter.lookup_max_failures`` gagal dalam ``lookup_window_minutes``, default 5/15 menit).
- Lookup berhasil = klaim: sync order POS pelunasan hanya diterima bila ada klaim ``ok`` untuk config + pesanan itu
  (label pesanan dari browser tidak cukup).
- Append-only untuk user (ACL baca saja); ditulis sudo oleh RPC setelah cek pemanggil. Retensi 180 hari (autovacuum).
"""

from datetime import timedelta

from odoo import api, fields, models

RESULTS = [
    ("ok", "Ditemukan"),
    ("invalid_format", "Format kode salah"),
    ("not_found", "Tidak ditemukan"),
    ("not_payable", "Tidak bisa dibayar (kedaluwarsa/batal/sudah dibayar)"),
    ("rate_limited", "Dibatasi (terlalu banyak gagal)"),
]
FAILURE_RESULTS = ("invalid_format", "not_found", "not_payable")
MAX_FAILURES_PARAM = "efn_online_counter.lookup_max_failures"
WINDOW_PARAM = "efn_online_counter.lookup_window_minutes"
DEFAULT_MAX_FAILURES = 5
DEFAULT_WINDOW_MINUTES = 15
RETENTION_DAYS = 180


class EfnOnlineCounterLookup(models.Model):
    _name = "efn.online.counter.lookup"
    _description = "EFN Online - Lookup kode bayar di kasir (audit & rate limit)"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    config_id = fields.Many2one("pos.config", required=True, index=True, readonly=True, check_company=True)
    session_id = fields.Many2one("pos.session", index=True, readonly=True, check_company=True)
    user_id = fields.Many2one("res.users", string="Kasir", required=True, index=True, readonly=True)
    online_order_id = fields.Many2one(
        "efn.online.order", string="Pesanan online", index="btree_not_null", readonly=True, check_company=True
    )
    result = fields.Selection(RESULTS, required=True, readonly=True, index=True)

    @api.model
    def _efn_int_param(self, key, default):
        raw = self.env["ir.config_parameter"].sudo().get_param(key, default)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return default

    @api.model
    def _efn_rate_limited(self, session, user):
        limit = self._efn_int_param(MAX_FAILURES_PARAM, DEFAULT_MAX_FAILURES)
        since = fields.Datetime.now() - timedelta(minutes=self._efn_int_param(WINDOW_PARAM, DEFAULT_WINDOW_MINUTES))
        base = [("result", "in", FAILURE_RESULTS), ("create_date", ">=", since)]
        Lookup = self.sudo()
        by_session = Lookup.search_count([*base, ("session_id", "=", session.id)], limit=limit) if session else 0
        by_user = Lookup.search_count([*base, ("user_id", "=", user.id)], limit=limit)
        return by_session >= limit or by_user >= limit

    @api.model
    def _efn_has_claim(self, config, online_order):
        return bool(
            self.sudo().search_count(
                [("config_id", "=", config.id), ("online_order_id", "=", online_order.id), ("result", "=", "ok")],
                limit=1,
            )
        )

    @api.autovacuum
    def _gc_efn_online_counter_lookup(self):
        limit_date = fields.Datetime.now() - timedelta(days=RETENTION_DAYS)
        self.sudo().search([("create_date", "<", limit_date)], limit=5000).unlink()
