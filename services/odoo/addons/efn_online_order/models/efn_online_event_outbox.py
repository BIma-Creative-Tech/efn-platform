"""Outbox event pesanan online Odoo -> BFF (docs/04 §10.5, K-ON1; pola outbox ``efn_pos_loyalty_ledger``).

- Record dibuat DI DALAM transaksi perubahan state order (``efn.online.order._efn_emit``) -> event tidak pernah
  hilang/terkirim untuk perubahan yang di-rollback. ``seq`` naik monoton per order; ``idempotency_key`` unik
  ``online:<efn.online.order id>:<event>:<seq>``.
- **Payload tidak disimpan**; dibangun saat kirim dari order + snapshot state (tanpa PII, tanpa kode ambil).
- Kirim ``POST <efn.bff_url>/v1/pos/online/order-events`` lewat TLS dengan verifikasi CA internal (helper
  ``efn.bff.client``: URL https wajib, CA ``EFN_CA_FILE``, tanpa redirect) + header ``X-EFN-Service-Key`` +
  ``X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(secret, "<t>." + rawBody)>``. Secret ``odoo_online_event_hmac``
  dari file ``EFN_ONLINE_EVENT_HMAC_FILE`` (default ``/run/secrets/odoo_online_event_hmac``). **Tanpa fallback
  tanpa tanda tangan** (L-2): secret tidak ada -> tidak dikirim, retry + log error.
- Respons: 200 -> ``done``; 400/401/404/409 -> ``failed`` FINAL + aktivitas; 429/5xx/timeout/jaringan -> retry
  backoff 1 m -> 1 j; umur > ``efn_online.outbox_max_hours`` (48) -> ``failed`` + aktivitas.
- Urutan: event suatu order tidak dikirim selama event ber-seq lebih kecil masih ``pending`` (BFF mengabaikan
  ``seq`` <= ``last_seq``). Baris dikunci ``FOR UPDATE SKIP LOCKED`` (dua worker tidak mengirim record sama).
"""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import timedelta

import requests

from odoo import _, api, fields, models

from odoo.addons.efn_core.models.efn_bff_client import SERVICE_KEY_HEADER, EfnBffError

_logger = logging.getLogger(__name__)

EVENT_PATH = "/v1/pos/online/order-events"
EVENTS = [
    ("placed", "placed"),
    ("paid", "paid"),
    ("confirmed", "confirmed"),
    ("stock_short", "stock_short"),
    ("ready", "ready"),
    ("picked_up", "picked_up"),
    ("cancelled", "cancelled"),
    ("refunded", "refunded"),
    ("review", "review"),
]
HMAC_FILE_ENV = "EFN_ONLINE_EVENT_HMAC_FILE"
DEFAULT_HMAC_FILE = "/run/secrets/odoo_online_event_hmac"
SIGNATURE_HEADER = "X-EFN-Signature"
FINAL_HTTP = frozenset({400, 401, 403, 404, 409, 422})
DEFAULT_MAX_HOURS = 48
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
MAX_BODY_BYTES = 8192


class EfnOnlineHmacMissing(Exception):
    pass


def read_event_hmac_secret():
    """Secret HMAC event; dibaca per kirim (rotasi tanpa restart). Tidak pernah di-log."""
    path = os.environ.get(HMAC_FILE_ENV) or DEFAULT_HMAC_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            secret = handle.read().strip()
    except OSError:
        raise EfnOnlineHmacMissing() from None
    if len(secret) < 32:
        raise EfnOnlineHmacMissing()
    return secret.encode("utf-8")


def sign_body(secret, timestamp, raw_body):
    message = str(timestamp).encode("ascii") + b"." + raw_body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class EfnOnlineEventOutbox(models.Model):
    _name = "efn.online.event.outbox"
    _description = "EFN Online - Outbox event ke BFF"
    _order = "id"
    _check_company_auto = True
    _rec_name = "idempotency_key"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    order_id = fields.Many2one(
        "efn.online.order", required=True, index=True, readonly=True, ondelete="cascade", check_company=True
    )
    refund_id = fields.Many2one("efn.online.refund", readonly=True, ondelete="set null", check_company=True)
    event = fields.Selection(EVENTS, required=True, readonly=True)
    seq = fields.Integer(required=True, readonly=True)
    state_snapshot = fields.Char(readonly=True)
    initiator = fields.Selection([("bff", "bff"), ("odoo", "odoo")], readonly=True)
    occurred_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    state = fields.Selection(
        [("pending", "Menunggu"), ("done", "Terkirim"), ("failed", "Gagal")],
        required=True,
        default="pending",
        index=True,
        readonly=True,
    )
    attempt_count = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(index=True, readonly=True, default=fields.Datetime.now)
    last_error = fields.Char(readonly=True, help="Kode error saja (tanpa payload).")
    done_at = fields.Datetime(readonly=True)

    _idempotency_key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi event harus unik.")
    _order_seq_unique = models.Constraint("unique (order_id, seq)", "seq event harus unik per pesanan.")

    # ---------------------------------------------------------------- enqueue
    @api.model
    def _efn_enqueue(self, order, event, seq, refund=None):
        refund = refund or self.env["efn.online.refund"]
        record = self.sudo().create(
            {
                "company_id": order.company_id.id,
                "order_id": order.id,
                "refund_id": refund.id or False,
                "event": event,
                "seq": seq,
                "state_snapshot": order.state,
                "initiator": refund.initiator or False,
                "idempotency_key": f"online:{order.id}:{event}:{seq}",
            }
        )
        self._efn_trigger()
        return record

    @api.model
    def _efn_trigger(self):
        cron = self.env.ref("efn_online_order.ir_cron_efn_online_outbox", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ---------------------------------------------------------------- payload
    def _efn_payload(self):
        self.ensure_one()
        order = self.order_id.sudo()
        refund = self.refund_id.sudo()
        refund_payload = None
        if refund:
            refund_payload = {
                "refund_ref": refund.refund_ref,
                "amount": order._efn_amount_out(refund.amount),
                "kind": refund.kind,
                "initiator": refund.initiator,
            }
        return {
            "schema_version": 1,
            "idempotency_key": self.idempotency_key,
            "seq": self.seq,
            "platform_order_ref": order.platform_order_ref,
            "checkout_ref": order.checkout_ref,
            "channel_code": order.channel_id.code,
            "company_code": order.company_id.efn_brand_code or None,
            "event": self.event,
            "state": self.state_snapshot,
            "occurred_at": self.occurred_at.replace(microsecond=0).isoformat() + "Z",
            "refund": refund_payload,
        }

    # ----------------------------------------------------------------- runner
    @api.model
    def _cron_efn_online_outbox_run(self):
        self._efn_process_due(limit=200)

    @api.model
    def _efn_process_due(self, limit=50):
        processed = []
        while len(processed) < limit:
            self.flush_model()
            self.env.cr.execute(
                """
                SELECT o.id FROM efn_online_event_outbox o
                 WHERE o.state = 'pending'
                   AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= %s)
                   AND NOT (o.id = ANY(%s))
                   AND NOT EXISTS (
                       SELECT 1 FROM efn_online_event_outbox p
                        WHERE p.order_id = o.order_id AND p.seq < o.seq AND p.state = 'pending')
                 ORDER BY o.id
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
                """,
                (fields.Datetime.now(), processed or [0]),
            )
            row = self.env.cr.fetchone()
            if not row:
                break
            processed.append(row[0])
            self.sudo().browse(row[0])._efn_send_one()
            if self.env.context.get("ir_cron_progress_id"):
                self.env["ir.cron"]._commit_progress(1)
        return processed

    def _efn_send_one(self):
        self.ensure_one()
        if self.state != "pending":
            return
        client = self.env["efn.bff.client"]
        try:
            secret = read_event_hmac_secret()
            base_url = client._get_base_url()
            verify = client._get_verify(base_url)
            service_key = client._read_api_key()
        except EfnOnlineHmacMissing:
            _logger.error(
                "EFN online outbox: secret HMAC event tidak terbaca (cek env %s); tidak dikirim", HMAC_FILE_ENV
            )
            self._efn_retry_or_fail("hmac_missing")
            return
        except EfnBffError as exc:
            self._efn_retry_or_fail(exc.code)
            return
        raw = json.dumps(self._efn_payload(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(raw) > MAX_BODY_BYTES:
            self._efn_finish("failed", "payload_too_large")
            return
        timestamp = int(time.time())
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            SERVICE_KEY_HEADER: service_key,
            SIGNATURE_HEADER: f"t={timestamp},v1={sign_body(secret, timestamp, raw)}",
        }
        try:
            response = requests.post(  # noqa: S113 - timeout di bawah
                base_url + EVENT_PATH,
                data=raw,
                headers=headers,
                timeout=client._get_timeout(),
                allow_redirects=False,
                verify=verify,
            )
        except requests.exceptions.SSLError:
            _logger.error("EFN online outbox %s: verifikasi TLS BFF gagal", self.id)
            self._efn_retry_or_fail("bff_tls_error")
            return
        except requests.exceptions.Timeout:
            self._efn_retry_or_fail("bff_timeout")
            return
        except requests.exceptions.RequestException as exc:
            _logger.warning("EFN online outbox %s: BFF tidak terjangkau (%s)", self.id, type(exc).__name__)
            self._efn_retry_or_fail("bff_unreachable")
            return
        status = response.status_code
        if 200 <= status < 300:
            self.write(
                {
                    "state": "done",
                    "done_at": fields.Datetime.now(),
                    "last_error": False,
                    "attempt_count": self.attempt_count + 1,
                }
            )
            _logger.info("EFN online outbox %s (%s seq %s) -> %s", self.id, self.event, self.seq, status)
            return
        if status in FINAL_HTTP:
            self._efn_finish("failed", f"http_{status}")
            return
        self._efn_retry_or_fail(f"http_{status}")

    def _efn_max_hours(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn_online.outbox_max_hours", DEFAULT_MAX_HOURS)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MAX_HOURS

    def _efn_retry_or_fail(self, code):
        self.ensure_one()
        if self.create_date and self.create_date < fields.Datetime.now() - timedelta(hours=self._efn_max_hours()):
            self._efn_finish("failed", f"max_age:{code}")
            return
        attempts = self.attempt_count + 1
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_MAX_SECONDS)
        _logger.warning(
            "EFN online outbox %s (%s) gagal %s, percobaan %d; ulang %ds", self.id, self.event, code, attempts, delay
        )
        self.write(
            {
                "attempt_count": attempts,
                "last_error": code,
                "next_attempt_at": fields.Datetime.now() + timedelta(seconds=delay),
            }
        )

    def _efn_finish(self, state, code):
        self.ensure_one()
        self.write(
            {
                "state": state,
                "last_error": code,
                "done_at": fields.Datetime.now(),
                "attempt_count": self.attempt_count + 1,
            }
        )
        if state == "failed":
            _logger.warning("EFN online outbox %s (%s) GAGAL final: %s", self.id, self.event, code)
            self.order_id.sudo()._efn_schedule_activity(
                _("Event pesanan online gagal terkirim ke BFF"),
                f"event_failed:{self.event}:{code}",
                "sales_team.group_sale_manager",
            )

    @api.autovacuum
    def _gc_efn_online_outbox_done(self):
        """Retensi: outbox ``done`` > 30 hari dihapus (docs/08 §8.3 E)."""
        limit_date = fields.Datetime.now() - timedelta(days=30)
        self.sudo().search([("state", "=", "done"), ("done_at", "<", limit_date)], limit=5000).unlink()
