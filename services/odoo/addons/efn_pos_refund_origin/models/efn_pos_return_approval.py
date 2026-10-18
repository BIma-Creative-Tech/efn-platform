"""E-41 (docs/08 §11.6, docs/04 §12.4, L-51): persetujuan Store Manager untuk retur tanpa struk.

Satu record = satu percobaan persetujuan (berhasil atau gagal) — sekaligus jejak audit dan "token sekali pakai".
Token tidak pernah keluar dari server (pola ``efn.pos.otp.challenge``): browser hanya menerima ``ok`` +
``expires_in`` + ``max_amount``; saat sync order, server mencari persetujuan ``approved`` yang terikat ke
(config, sesi, ``pos.order.uuid``, kasir = user sync, company), belum kedaluwarsa (TTL 5 menit), nominal uang keluar
≤ ``amount_max``, lalu menandainya ``consumed`` (sekali pakai).

Teraudit: record append-only — ``create``/``write``/``unlink`` hanya lewat kode server ber-sudo (setelah cek);
POS manager & admin hanya membaca. Percobaan PIN salah juga dicatat (dasar kunci 15 menit setelah 5 salah per
approver). Retensi ``efn.pos.return_approval_retention_days`` (default 730, minimum 365) lewat autovacuum.
Data pribadi: hanya id user staf (nama staf internal plaintext, keputusan 18-40) + alasan teks pendek (UI meminta
tanpa data pelanggan); tanpa PIN, tanpa data pelanggan.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

APPROVAL_TTL_SECONDS = 300
LOCK_THRESHOLD = 5
LOCK_MINUTES = 15
DEFAULT_RETENTION_DAYS = 730
MIN_RETENTION_DAYS = 365
RETENTION_PARAM = "efn.pos.return_approval_retention_days"


class EfnPosReturnApproval(models.Model):
    _name = "efn.pos.return.approval"
    _description = "EFN: Persetujuan Retur Tanpa Struk"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    config_id = fields.Many2one(
        "pos.config", string="Point of Sale", required=True, index=True, readonly=True, check_company=True
    )
    session_id = fields.Many2one("pos.session", string="Sesi", required=True, readonly=True, check_company=True)
    order_ref = fields.Char(string="Order (uuid)", required=True, index=True, readonly=True)
    cashier_user_id = fields.Many2one("res.users", string="Kasir", required=True, readonly=True, ondelete="restrict")
    approver_user_id = fields.Many2one(
        "res.users", string="Store Manager", required=True, index=True, readonly=True, ondelete="restrict"
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount_max = fields.Monetary(string="Nominal maks. disetujui", readonly=True)
    reason = fields.Char(string="Alasan", readonly=True)
    state = fields.Selection(
        [
            ("approved", "Disetujui (belum dipakai)"),
            ("consumed", "Dipakai"),
            ("superseded", "Diganti persetujuan baru"),
            ("failed", "Ditolak"),
        ],
        required=True,
        index=True,
        readonly=True,
    )
    failure_reason = fields.Selection([("wrong_pin", "PIN salah"), ("locked", "Approver terkunci")], readonly=True)
    expires_at = fields.Datetime(string="Berlaku sampai", readonly=True)
    consumed_at = fields.Datetime(string="Dipakai pada", readonly=True)
    consumed_order_id = fields.Many2one(
        "pos.order",
        string="Order retur",
        readonly=True,
        index="btree_not_null",
        ondelete="set null",
        check_company=True,
    )
    amount_used = fields.Monetary(string="Uang keluar", readonly=True)

    # ------------------------------------------------------------- append-only
    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            self._efn_immutable()
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            self._efn_immutable()
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            self._efn_immutable()
        return super().unlink()

    @api.model
    def _efn_immutable(self):
        raise UserError(_("Catatan persetujuan retur adalah jejak audit dan tidak dapat diubah atau dihapus."))

    # ----------------------------------------------------------------- lockout
    @api.model
    def _efn_recent_failures(self, approver):
        """PIN salah approver dalam 15 menit terakhir, setelah persetujuan suksesnya yang terakhir."""
        since = fields.Datetime.now() - timedelta(minutes=LOCK_MINUTES)
        domain = [
            ("approver_user_id", "=", approver.id),
            ("state", "=", "failed"),
            ("failure_reason", "=", "wrong_pin"),
            ("create_date", ">=", since),
        ]
        last_ok = self.sudo().search(
            [("approver_user_id", "=", approver.id), ("state", "in", ("approved", "consumed", "superseded"))],
            order="id desc",
            limit=1,
        )
        if last_ok:
            domain.append(("id", ">", last_ok.id))
        return self.sudo().search_count(domain)

    @api.model
    def _efn_is_locked(self, approver):
        return self._efn_recent_failures(approver) >= LOCK_THRESHOLD

    # ------------------------------------------------------------------- usage
    def _efn_usable_error(self, order, amount):
        """Kode alasan persetujuan `self` tidak sah untuk `order` dengan uang keluar `amount`, atau None."""
        if not self:
            return "missing"
        self.ensure_one()
        if self.state == "consumed":
            # Sync ulang order yang sama (idempoten); order lain = pemakaian ulang.
            return None if self.consumed_order_id == order else "used"
        if self.state != "approved":
            return "missing"
        if self.order_ref != order.uuid or self.config_id != order.config_id:
            return "order_mismatch"
        if self.session_id != order.session_id:
            return "session_mismatch"
        if self.company_id != order.company_id:
            return "company_mismatch"
        if self.cashier_user_id.id != self.env.uid:
            return "cashier_mismatch"
        if not self.expires_at or self.expires_at < fields.Datetime.now():
            return "expired"
        if order.currency_id.compare_amounts(amount, self.amount_max) > 0:
            return "amount_exceeded"
        return None

    # --------------------------------------------------------------- retention
    @api.autovacuum
    def _gc_efn_pos_return_approvals(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM, DEFAULT_RETENTION_DAYS)
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        days = max(days, MIN_RETENTION_DAYS)
        limit_date = fields.Datetime.now() - timedelta(days=days)
        self.sudo().search([("create_date", "<", limit_date)], limit=10000).unlink()
