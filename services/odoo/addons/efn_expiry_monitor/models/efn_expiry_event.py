"""Jejak audit append-only efn_expiry_monitor + persetujuan override supervisor POS.

``efn.expiry.event``: satu baris per kejadian yang perlu diaudit — override penerimaan sisa umur pendek, penjualan
lot kedaluwarsa di POS (dengan/tanpa override sah), kuota markdown terlampaui, markdown tidak sah/harga tidak cocok
saat sync. Order POS yang sudah dibayar **tidak** ditolak (fit-gap §3 opsi D "Server"); selisih dicatat di sini.

``efn.expiry.pos.approval``: persetujuan supervisor (grup ``group_expiry_override`` + PIN E-41 yang diverifikasi
server) untuk menjual lot kedaluwarsa di order POS tertentu. Satu record = satu percobaan (berhasil/gagal); token
tidak pernah keluar dari server — saat sync, server mencari persetujuan yang terikat ke (config, sesi, uuid order,
produk, nama lot, kasir) lalu menandainya ``consumed``.

Keduanya: create/write/unlink hanya lewat kode server ber-sudo (setelah cek); diaudit ``efn_audit``. Tanpa data
pelanggan; hanya id staf + alasan pendek.
"""

from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

from odoo.addons.efn_pos_refund_origin.models.efn_pos_return_approval import (
    CASHIER_WINDOW_MINUTES,
    LOCK_THRESHOLD,
)
from odoo.addons.efn_pos_refund_origin.models.return_pin import pin_failure_count

EVENT_KINDS = [
    ("receipt_override", "Override penerimaan sisa umur pendek"),
    ("pos_expired_override", "POS: lot kedaluwarsa dijual dengan override"),
    ("pos_expired_no_override", "POS: lot kedaluwarsa dijual TANPA override sah"),
    ("markdown_cap_exceeded", "POS: kuota markdown terlampaui"),
    ("markdown_invalid", "POS: markdown tidak sah saat sync"),
    ("markdown_price_mismatch", "POS: harga di bawah markdown"),
    ("clock_skew", "POS: jam order di luar jendela sesi (dinilai dengan waktu server)"),
]
LOCK_MINUTES = 15
# SEC-224: retensi persetujuan override (pola E-41; id staf + alasan pendek, inventaris PDP).
RETENTION_PARAM = "efn_expiry_monitor.pos_approval_retention_days"
DEFAULT_RETENTION_DAYS = 730
MIN_RETENTION_DAYS = 365


def _immutable(env):
    # Bukan AbstractModel mixin: AbstractModel efn.* tidak boleh punya metode publik (SEC-58, test_rpc_surface).
    if not env.su:
        # Teks tetap (fungsi modul, bukan metode model: tanpa `self` untuk konteks bahasa `_`).
        raise UserError("efn_expiry:append_only — catatan ini adalah jejak audit dan tidak dapat diubah.")


class EfnExpiryEvent(models.Model):
    _name = "efn.expiry.event"
    _description = "EFN kejadian kedaluwarsa/markdown (jejak audit)"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    kind = fields.Selection(EVENT_KINDS, required=True, index=True, readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", readonly=True, index=True, check_company=True)
    lot_id = fields.Many2one("stock.lot", readonly=True, index=True)
    # SEC-220: himpunan lot yang dievaluasi saat override penerimaan; lot lain tetap diblokir.
    lot_ids = fields.Many2many(
        "stock.lot", "efn_expiry_event_lot_rel", "event_id", "lot_id", string="Lot di-override", readonly=True
    )
    product_id = fields.Many2one("product.product", readonly=True)
    qty = fields.Float(digits="Product Unit", readonly=True)
    picking_id = fields.Many2one("stock.picking", readonly=True, index="btree_not_null", check_company=True)
    pos_order_id = fields.Many2one("pos.order", readonly=True, index="btree_not_null", check_company=True)
    config_id = fields.Many2one("pos.config", readonly=True, check_company=True)
    markdown_id = fields.Many2one("efn.lot.markdown", readonly=True, index="btree_not_null")
    user_id = fields.Many2one("res.users", string="Pelaku", readonly=True, ondelete="restrict")
    approver_id = fields.Many2one("res.users", string="Penyetuju", readonly=True, ondelete="restrict")
    reason = fields.Char(readonly=True)
    note = fields.Text(readonly=True, help="Ringkasan teknis (daftar lot/sisa hari), tanpa data pelanggan.")

    @api.model_create_multi
    def create(self, vals_list):
        _immutable(self.env)
        return super().create(vals_list)

    def write(self, vals):
        _immutable(self.env)
        return super().write(vals)

    def unlink(self):
        _immutable(self.env)
        return super().unlink()

    @api.model
    def _efn_log(self, **vals):
        vals.setdefault("user_id", self.env.uid)
        return self.sudo().create(vals)


class EfnExpiryPosApproval(models.Model):
    _name = "efn.expiry.pos.approval"
    _description = "EFN persetujuan supervisor jual lot kedaluwarsa di POS"
    _order = "id desc"
    _check_company_auto = True

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    config_id = fields.Many2one("pos.config", required=True, index=True, readonly=True, check_company=True)
    session_id = fields.Many2one("pos.session", required=True, readonly=True, check_company=True)
    order_ref = fields.Char(string="Order (uuid)", required=True, index=True, readonly=True)
    product_id = fields.Many2one("product.product", required=True, readonly=True)
    lot_name = fields.Char(required=True, readonly=True)
    cashier_user_id = fields.Many2one("res.users", string="Kasir", required=True, readonly=True, ondelete="restrict")
    approver_user_id = fields.Many2one(
        "res.users", string="Supervisor", required=True, index=True, readonly=True, ondelete="restrict"
    )
    reason = fields.Char(readonly=True)
    state = fields.Selection(
        [("approved", "Disetujui (belum dipakai)"), ("consumed", "Dipakai"), ("failed", "Ditolak")],
        required=True,
        index=True,
        readonly=True,
    )
    failure_reason = fields.Selection([("wrong_pin", "PIN salah"), ("locked", "Supervisor terkunci")], readonly=True)
    expires_at = fields.Datetime(readonly=True)
    consumed_order_id = fields.Many2one("pos.order", readonly=True, index="btree_not_null", check_company=True)

    @api.model_create_multi
    def create(self, vals_list):
        _immutable(self.env)
        return super().create(vals_list)

    def write(self, vals):
        _immutable(self.env)
        return super().write(vals)

    def unlink(self):
        _immutable(self.env)
        return super().unlink()

    @api.model
    def _efn_recent_failures(self, approver):
        since = fields.Datetime.now() - timedelta(minutes=LOCK_MINUTES)
        domain = [
            ("approver_user_id", "=", approver.id),
            ("state", "=", "failed"),
            ("failure_reason", "=", "wrong_pin"),
            ("create_date", ">=", since),
        ]
        last_ok = self.sudo().search(
            [("approver_user_id", "=", approver.id), ("state", "in", ("approved", "consumed"))],
            order="id desc",
            limit=1,
        )
        if last_ok:
            domain.append(("id", ">", last_ok.id))
        return self.sudo().search_count(domain)

    @api.model
    def _efn_cashier_pin_failures(self, cashier):
        since = fields.Datetime.now() - timedelta(minutes=CASHIER_WINDOW_MINUTES)
        return self.sudo().search_count(
            [
                ("cashier_user_id", "=", cashier.id),
                ("state", "=", "failed"),
                ("failure_reason", "=", "wrong_pin"),
                ("create_date", ">=", since),
            ]
        )

    @api.model
    def _efn_is_locked(self, approver):
        """SEC-216: penghitung BERSAMA dengan retur tanpa struk E-41 (PIN yang sama)."""
        return pin_failure_count(self.env, approver) >= LOCK_THRESHOLD

    @api.model
    def _efn_find_valid(self, order, product, lot_name, cashier):
        """Persetujuan sah untuk baris order POS (sudah dipakai order yang sama = idempoten).

        SEC-217: ``cashier`` = user yang menyinkronkan order (``env.uid`` saat sync), bukan ``order.user_id`` payload."""
        candidates = self.sudo().search(
            [
                ("config_id", "=", order.config_id.id),
                ("order_ref", "=", order.uuid),
                ("product_id", "=", product.id),
                ("lot_name", "=", lot_name),
                ("state", "in", ("approved", "consumed")),
            ],
            order="id desc",
        )
        now = fields.Datetime.now()
        for approval in candidates:
            if approval.state == "consumed":
                if approval.consumed_order_id == order:
                    return approval
                continue
            if approval.session_id != order.session_id or approval.company_id != order.company_id:
                continue
            if approval.cashier_user_id != cashier:
                continue
            if not approval.expires_at or approval.expires_at < now:
                continue
            return approval
        return self.browse()

    @api.autovacuum
    def _gc_efn_expiry_pos_approvals(self):
        """SEC-224: retensi pola E-41 — ``efn_expiry_monitor.pos_approval_retention_days`` (default 730, min 365)."""
        raw = self.env["ir.config_parameter"].sudo().get_param(RETENTION_PARAM, DEFAULT_RETENTION_DAYS)
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        days = max(days, MIN_RETENTION_DAYS)
        limit_date = fields.Datetime.now() - timedelta(days=days)
        self.sudo().search([("create_date", "<", limit_date)], limit=10000).unlink()


class EfnPosReturnApproval(models.Model):
    """SEC-216: perluas penghitung PIN bersama E-41 dengan kegagalan override kedaluwarsa."""

    _inherit = "efn.pos.return.approval"

    @api.model
    def _efn_pin_failures_total(self, approver):
        return super()._efn_pin_failures_total(approver) + self.env[
            "efn.expiry.pos.approval"
        ]._efn_recent_failures(approver)

    @api.model
    def _efn_cashier_pin_failures_total(self, cashier):
        return super()._efn_cashier_pin_failures_total(cashier) + self.env[
            "efn.expiry.pos.approval"
        ]._efn_cashier_pin_failures(cashier)
