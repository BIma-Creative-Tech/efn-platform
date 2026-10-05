"""Cermin lokal `platform.payment_intent` BFF untuk pembayaran POS `efn_gateway` (docs/04 §8.3).

Status final hanya berubah dari jawaban BFF (`GET /v1/pos/payments/:id` atau cancel) —
tidak pernah dari browser. Ditulis hanya lewat RPC server (sudo terkontrol) dan cron.

Tidak ada data kartu/PAN (QRIS saja); `qr_string` dikosongkan setelah status final.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from odoo import _, api, fields, models

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError

_logger = logging.getLogger(__name__)

PAYMENTS_PATH = "/v1/pos/payments"
STATUSES = [
    ("pending", "Menunggu pembayaran"),
    ("paid", "Lunas"),
    ("expired", "Kedaluwarsa"),
    ("failed", "Gagal"),
    ("cancelled", "Dibatalkan"),
]
STATUS_KEYS = frozenset(key for key, _label in STATUSES)
FINAL_STATUSES = frozenset({"paid", "expired", "failed", "cancelled"})
BFF_ID_RE = re.compile(r"^[0-9A-Za-z_\-]{1,64}$")
ORPHAN_GRACE_MINUTES = 60
PENDING_GRACE_MINUTES = 10


def parse_bff_datetime(value):
    """ISO-8601 dari BFF (mis. `2026-10-06T03:00:00.000Z`) -> datetime UTC naive (format ORM)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


class EfnPosPaymentIntent(models.Model):
    _name = "efn.pos.payment.intent"
    _description = "EFN POS Payment Intent (QRIS via BFF)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "bff_payment_id"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    pos_config_id = fields.Many2one(
        "pos.config", string="Point of Sale", required=True, index=True, readonly=True,
        ondelete="restrict", check_company=True,
    )
    payment_method_id = fields.Many2one(
        "pos.payment.method", required=True, readonly=True, ondelete="restrict", check_company=True,
    )
    pos_order_uuid = fields.Char(string="Order (uuid)", required=True, index=True, readonly=True)
    payment_uuid = fields.Char(string="Baris pembayaran (uuid)", required=True, readonly=True)
    attempt = fields.Integer(default=1, readonly=True)
    amount = fields.Monetary(required=True, readonly=True, currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    bff_payment_id = fields.Char(string="Payment ID (BFF)", required=True, index=True, readonly=True)
    provider = fields.Char(readonly=True)
    provider_ref = fields.Char(readonly=True)
    status = fields.Selection(STATUSES, required=True, default="pending", index=True, readonly=True, tracking=True)
    qr_string = fields.Char(readonly=True, copy=False)
    expires_at = fields.Datetime(readonly=True)
    paid_at = fields.Datetime(readonly=True)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    pos_payment_id = fields.Many2one(
        "pos.payment", string="Pembayaran POS", readonly=True, ondelete="set null", index="btree_not_null",
        copy=False,
    )
    pos_order_id = fields.Many2one(
        "pos.order", string="Order POS", readonly=True, ondelete="set null", index="btree_not_null",
        copy=False, check_company=True,
    )
    note = fields.Char(string="Catatan", readonly=True)
    orphan_flagged = fields.Boolean(readonly=True, copy=False)

    _bff_payment_id_unique = models.Constraint(
        "UNIQUE(bff_payment_id)", "Payment ID BFF sudah tercatat pada intent lain."
    )
    _idempotency_key_unique = models.Constraint(
        "UNIQUE(idempotency_key)", "Idempotency key intent harus unik."
    )

    # ---------------------------------------------------------------- BFF sync
    def _efn_apply_remote(self, data):
        """Terapkan jawaban BFF (`{payment_id, status, paid_at, provider_ref, amount}`).

        - `paid` bersifat final: tidak pernah diturunkan lagi.
        - `paid` dengan `amount` berbeda dari intent -> `failed` + catatan `amount_mismatch`
          (order tidak bisa disinkronkan; uang diurus lewat SOP/settlement BFF).
        :returns: True bila status dipahami dan diterapkan.
        """
        self.ensure_one()
        status = data.get("status") if isinstance(data, dict) else None
        if status not in STATUS_KEYS:
            _logger.warning("EFN gateway: status BFF tidak dikenal untuk intent %s", self.id)
            return False
        remote_id = data.get("payment_id")
        if remote_id is not None and remote_id != self.bff_payment_id:
            _logger.warning("EFN gateway: payment_id BFF tidak cocok untuk intent %s", self.id)
            return False
        if self.status == "paid":
            if status != "paid":
                _logger.warning(
                    "EFN gateway: intent %s sudah paid; status BFF %s diabaikan", self.id, status
                )
            return status == "paid"
        vals = {"status": status}
        if status == "paid":
            amount = data.get("amount")
            if amount is not None:
                try:
                    remote_amount = float(amount)
                except (TypeError, ValueError):
                    remote_amount = None
                if remote_amount is None or self.currency_id.compare_amounts(remote_amount, self.amount) != 0:
                    _logger.warning("EFN gateway: intent %s paid dengan nominal berbeda", self.id)
                    self.write({"status": "failed", "note": "amount_mismatch", "qr_string": False})
                    return True
            vals["paid_at"] = parse_bff_datetime(data.get("paid_at")) or fields.Datetime.now()
            provider_ref = data.get("provider_ref")
            if isinstance(provider_ref, str):
                vals["provider_ref"] = provider_ref[:128]
        if status in FINAL_STATUSES:
            vals["qr_string"] = False
        self.write(vals)
        return True

    def _efn_refresh_status(self):
        """GET status ke BFF. :raises EfnBffError: BFF tidak dapat dipakai."""
        self.ensure_one()
        if self.status in FINAL_STATUSES:
            return self.status
        response = self.env["efn.bff.client"].get(
            f"{PAYMENTS_PATH}/{self.bff_payment_id}", log_context={"intent": self.id}
        )
        if response.status == 200:
            self._efn_apply_remote(response.data)
        elif response.status == 404:
            self.write({"status": "failed", "note": "not_found", "qr_string": False})
        else:
            _logger.warning("EFN gateway: status intent %s ditolak BFF (HTTP %s)", self.id, response.status)
        return self.status

    def _efn_cancel_remote(self):
        """POST cancel ke BFF. :raises EfnBffError: BFF tidak dapat dipakai."""
        self.ensure_one()
        if self.status in FINAL_STATUSES:
            return self.status
        response = self.env["efn.bff.client"].post(
            f"{PAYMENTS_PATH}/{self.bff_payment_id}/cancel", {}, log_context={"intent": self.id}
        )
        remote_status = response.data.get("status")
        if response.status == 200 and remote_status == "paid":
            # Balapan: pelanggan sudah membayar sebelum cancel. Ambil detail lengkap.
            return self._efn_refresh_status()
        if response.status == 200 and remote_status in STATUS_KEYS:
            self._efn_apply_remote({"status": remote_status})
        else:
            _logger.warning("EFN gateway: cancel intent %s ditolak BFF (HTTP %s)", self.id, response.status)
        return self.status

    # ------------------------------------------------------------ rekonsiliasi
    @api.model
    def _cron_efn_gateway_reconcile(self):
        """Harian: (1) pending lewat `expires_at` -> refresh, lalu cancel bila masih pending;
        (2) intent `paid` tanpa `pos.payment` (yatim) -> aktivitas akuntan + log alert."""
        now = fields.Datetime.now()
        Intent = self.sudo()
        stale = Intent.search(
            [
                ("status", "=", "pending"),
                ("expires_at", "!=", False),
                ("expires_at", "<", now - timedelta(minutes=PENDING_GRACE_MINUTES)),
            ]
        )
        for intent in stale:
            try:
                if intent._efn_refresh_status() == "pending":
                    intent._efn_cancel_remote()
            except EfnBffError as exc:
                _logger.warning("EFN gateway: rekonsiliasi intent %s gagal (%s)", intent.id, exc.code)
                continue
            if intent.status == "pending":
                intent.write({"status": "expired", "note": "expired_local", "qr_string": False})

        orphans = Intent.search(
            [
                ("status", "=", "paid"),
                ("pos_payment_id", "=", False),
                ("orphan_flagged", "=", False),
                ("paid_at", "<", now - timedelta(minutes=ORPHAN_GRACE_MINUTES)),
            ]
        )
        for intent in orphans:
            # Alert untuk Loki/Alertmanager: pelanggan sudah membayar tetapi order tidak tersinkron.
            _logger.warning(
                "EFN gateway: intent paid YATIM %s (company %s, config %s) — perlu rekonsiliasi",
                intent.bff_payment_id,
                intent.company_id.id,
                intent.pos_config_id.id,
            )
            user = intent._efn_reconcile_user()
            if user:
                intent.activity_schedule(
                    "mail.mail_activity_data_todo",
                    user_id=user.id,
                    summary=_("QRIS lunas tanpa order POS"),
                    note=_(
                        "Pembayaran QRIS %(ref)s lunas tetapi tidak terhubung ke order POS. "
                        "Cocokkan dengan antrean sync kasir / refund sesuai SOP.",
                        ref=intent.bff_payment_id,
                    ),
                )
            intent.write({"orphan_flagged": True})
        return {"expired": len(stale), "orphans": len(orphans)}

    def _efn_reconcile_user(self):
        self.ensure_one()
        group = self.env.ref("account.group_account_manager", raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        return (
            self.env["res.users"]
            .sudo()
            .search(
                [
                    ("all_group_ids", "in", group.ids)
                    if "all_group_ids" in self.env["res.users"]._fields
                    else ("groups_id", "in", group.ids),
                    ("company_ids", "in", self.company_id.ids),
                    ("share", "=", False),
                    ("id", "!=", self.env.ref("base.user_root").id),
                ],
                order="id",
                limit=1,
            )
        )
