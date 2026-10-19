"""Outbox event QR Odoo -> BFF ``POST /v1/pos/fnb/events`` (docs/04 §14.A.4, pola §10.5 / efn_online_order).

- Record dibuat DI DALAM transaksi perubahan (status kiriman / tutup sesi meja): tidak ada event untuk perubahan yang
  di-rollback. ``seq`` naik monoton per sesi meja dan berbasis waktu (G18-QA-01), ``idempotency_key`` =
  ``fnb:<table_session_id>:<event>:<seq>``.
- Payload dibangun saat kirim dari snapshot yang disimpan (tanpa PII, tanpa catatan, tanpa token):
  ``submission`` = ``{submission_ref, status, order_uuid}`` hanya untuk ``submission_status`` (K1 r15);
  ``close`` hanya untuk ``session_closed``; L-80 + K2 r15: bila ``reason=paid``, ``close.orders`` =
  ``[{order_uuid, eligible_amount, company_code, pos_member_attached}]`` order sesi yang dibayar dengan dasar poin
  NETO refund > 0 (BFF meng-earn per order dengan kunci ``pos_earn:<order_uuid>`` hanya bila
  ``pos_member_attached=false``). K3 r15 (L-83): pass pertama membekukan ``close.orders`` (neto refund yang sudah
  dibayar, di bawah kunci baris sesi) dan menyimpannya; POST pertama dan semua retry mengirim body beku yang sama.
  Refund setelah pembekuan ditandai ``efn_qr_refund_awaiting`` dan di-enqueue sebagai reverse QR saat 2xx; flag
  ``efn_qr_points_eligible`` / ``efn_qr_refund_netted`` dipasang hanya setelah 2xx; close gagal permanen -> alert
  ``EFN_QR_REFUND_UNRESOLVED`` (refund menunggu tetap bertanda untuk review).
- TLS + CA internal (helper ``efn.bff.client``) + ``X-EFN-Service-Key`` + ``X-EFN-Signature: t=<unix>,v1=<hex
  HMAC-SHA256(secret, "<t>." + rawBody)>``; secret ``odoo_fnb_event_hmac`` dari file env ``EFN_FNB_EVENT_HMAC_FILE``
  (default ``/run/secrets/odoo_fnb_event_hmac``). Tanpa fallback tanpa tanda tangan: secret tidak terbaca -> retry +
  log error.
- 2xx -> ``done``; 400/401/403/404/409/422 -> ``failed`` FINAL + log alert ``EFN_QR_EVENT_FAILED``; 429/5xx/timeout ->
  retry 1 m -> 1 j; umur > ``efn_pos_bff_order.outbox_max_hours`` (48) -> ``failed``.
- Urutan per sesi meja: event tidak dikirim selama event ber-seq lebih kecil sesi itu masih ``pending``.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import timedelta

import requests
from psycopg2.extensions import TransactionRollbackError

from odoo import api, fields, models

from odoo.addons.efn_core.models.efn_bff_client import SERVICE_KEY_HEADER, EfnBffError

from .qr_common import SUBMISSION_STATES, iso_utc

_logger = logging.getLogger(__name__)
_alert_logger = logging.getLogger("odoo.addons.efn_pos_bff_order.alert")

EVENT_PATH = "/v1/pos/fnb/events"
EVENTS = [("submission_status", "submission_status"), ("session_closed", "session_closed")]
HMAC_FILE_ENV = "EFN_FNB_EVENT_HMAC_FILE"
DEFAULT_HMAC_FILE = "/run/secrets/odoo_fnb_event_hmac"
SIGNATURE_HEADER = "X-EFN-Signature"
FINAL_HTTP = frozenset({400, 401, 403, 404, 409, 422})
DEFAULT_MAX_HOURS = 48
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
MAX_BODY_BYTES = 16384  # r15: batas body event BFF 16 KB
MAX_CLOSE_ORDERS = 100  # r15: FNB_CLOSE_MAX_ORDERS BFF


class EfnQrHmacMissing(Exception):
    pass


def read_event_hmac_secret():
    """Secret HMAC event; dibaca per kirim (rotasi tanpa restart). Tidak pernah di-log."""
    path = os.environ.get(HMAC_FILE_ENV) or DEFAULT_HMAC_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            secret = handle.read().strip()
    except OSError:
        raise EfnQrHmacMissing() from None
    if len(secret) < 32:
        raise EfnQrHmacMissing()
    return secret.encode("utf-8")


def sign_body(secret, timestamp, raw_body):
    message = str(timestamp).encode("ascii") + b"." + raw_body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class EfnPosQrEventOutbox(models.Model):
    _name = "efn.pos.qr.event.outbox"
    _description = "EFN POS - Outbox event QR ke BFF"
    _order = "id"
    _check_company_auto = True
    _rec_name = "idempotency_key"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    table_session_id = fields.Many2one(
        "efn.pos.table.session", required=True, index=True, readonly=True, ondelete="cascade", check_company=True
    )
    submission_id = fields.Many2one("efn.pos.qr.order", readonly=True, ondelete="set null", check_company=True)
    event = fields.Selection(EVENTS, required=True, readonly=True)
    seq = fields.Integer(required=True, readonly=True)
    # Snapshot (non-PII) saat event dibuat.
    submission_ref = fields.Char(readonly=True)
    submission_status = fields.Selection([(s, s) for s in SUBMISSION_STATES], readonly=True)
    # K1 r15: uuid order kiriman saat event dibuat (None bila belum ada).
    submission_order_uuid = fields.Char(readonly=True)
    close_reason = fields.Char(readonly=True)
    # L-80 + K2 r15: [{order_uuid, eligible_amount, company_code, pos_member_attached}] (hanya reason=paid).
    orders_snapshot = fields.Json(readonly=True)
    # K3 r15 (L-83): snapshot dibekukan sebelum POST pertama; retry selalu mengirim body yang sama persis.
    snapshot_frozen = fields.Boolean(readonly=True, copy=False)
    # id order refund yang sudah dinetokan di snapshot beku (ditandai efn_qr_refund_netted setelah 2xx).
    netted_refund_ids = fields.Json(readonly=True, copy=False)
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
    _session_seq_unique = models.Constraint("unique (table_session_id, seq)", "seq event harus unik per sesi meja.")

    # ---------------------------------------------------------------- enqueue
    @api.model
    def _efn_enqueue(self, session, event, vals):
        seq = session._efn_next_seq()
        record = self.sudo().create(
            {
                "company_id": session.company_id.id,
                "table_session_id": session.id,
                "event": event,
                "seq": seq,
                "idempotency_key": f"fnb:{session.bff_session_id}:{event}:{seq}",
                **vals,
            }
        )
        self._efn_trigger()
        return record

    @api.model
    def _efn_enqueue_status(self, submission, status):
        return self._efn_enqueue(
            submission.table_session_id,
            "submission_status",
            {
                "submission_id": submission.id,
                "submission_ref": submission.submission_ref,
                "submission_status": status,
                "submission_order_uuid": submission._efn_order_uuid(),
            },
        )

    @api.model
    def _efn_enqueue_close(self, session, reason):
        vals = {"close_reason": reason}
        if reason == "paid":
            vals["orders_snapshot"] = session._efn_paid_orders_payload()
        return self._efn_enqueue(session, "session_closed", vals)

    @api.model
    def _efn_trigger(self):
        cron = self.env.ref("efn_pos_bff_order.ir_cron_efn_qr_outbox", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ---------------------------------------------------------------- payload
    def _efn_payload(self):
        self.ensure_one()
        session = self.table_session_id.sudo()
        payload = {
            "schema_version": 1,
            "idempotency_key": self.idempotency_key,
            "seq": self.seq,
            "table_session_id": session.bff_session_id,
            "company_code": session.config_id._efn_qr_company_code(),
            "config_id": session.config_id.id,
            "event": self.event,
            "occurred_at": iso_utc(self.occurred_at),
        }
        if self.event == "submission_status":
            payload["submission"] = {
                "submission_ref": self.submission_ref,
                "status": self.submission_status,
                "order_uuid": self.submission_order_uuid or None,
            }
        else:
            close = {"reason": self.close_reason}
            if self.close_reason == "paid":
                close["orders"] = list(self.orders_snapshot or [])
            payload["close"] = close
        return payload

    # ----------------------------------------------------------------- runner
    @api.model
    def _cron_efn_qr_outbox_run(self):
        self._efn_process_due(limit=200)

    @api.model
    def _efn_process_due(self, limit=50):
        processed = []
        while len(processed) < limit:
            self.flush_model()
            self.env.cr.execute(
                """
                SELECT o.id FROM efn_pos_qr_event_outbox o
                 WHERE o.state = 'pending'
                   AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= %s)
                   AND NOT (o.id = ANY(%s))
                   AND NOT EXISTS (
                       SELECT 1 FROM efn_pos_qr_event_outbox p
                        WHERE p.table_session_id = o.table_session_id AND p.seq < o.seq AND p.state = 'pending')
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
        except EfnQrHmacMissing:
            # Yang di-log hanya NAMA env var, nilai secret tidak pernah.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            _logger.error("EFN QR outbox: secret HMAC event tidak terbaca (cek env %s); tidak dikirim", HMAC_FILE_ENV)
            self._efn_retry_or_fail("hmac_missing")
            return
        except EfnBffError as exc:
            self._efn_retry_or_fail(exc.code)
            return
        close_paid = self._efn_is_close_paid()
        if close_paid and not self.snapshot_frozen:
            # Pass pertama hanya MEMBEKUKAN snapshot (di-commit cron) lalu POST di pass berikutnya: body yang keluar
            # selalu body beku yang sudah tersimpan, walau proses mati tepat setelah POST.
            self._efn_freeze_close_orders()
            return
        if close_paid and len(self.orders_snapshot or []) > MAX_CLOSE_ORDERS:
            self._efn_finish("failed", "too_many_orders")
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
            _logger.error("EFN QR outbox %s: verifikasi TLS BFF gagal", self.id)
            self._efn_retry_or_fail("bff_tls_error")
            return
        except requests.exceptions.Timeout:
            self._efn_retry_or_fail("bff_timeout")
            return
        except requests.exceptions.RequestException as exc:
            _logger.warning("EFN QR outbox %s: BFF tidak terjangkau (%s)", self.id, type(exc).__name__)
            self._efn_retry_or_fail("bff_unreachable")
            return
        status = response.status_code
        if 200 <= status < 300:
            if close_paid and not self._efn_lock_session():
                # Refund paralel sedang menandai dirinya "menunggu": event tetap pending, retry -> BFF replay 200.
                return
            self.write(
                {
                    "state": "done",
                    "done_at": fields.Datetime.now(),
                    "last_error": False,
                    "attempt_count": self.attempt_count + 1,
                }
            )
            if close_paid:
                # K3 r15: flag poin QR, refund ternetto, dan reverse refund "menunggu" hanya setelah 2xx.
                self.table_session_id.sudo()._efn_mark_close_sent(self)
            _logger.info("EFN QR outbox %s (%s seq %s) -> %s", self.id, self.event, self.seq, status)
            return
        if status in FINAL_HTTP:
            code = self._efn_error_code(response)
            self._efn_finish("failed", f"http_{status}:{code}" if code else f"http_{status}")
            return
        self._efn_retry_or_fail(f"http_{status}")

    def _efn_is_close_paid(self):
        return self.event == "session_closed" and self.close_reason == "paid"

    def _efn_lock_session(self):
        """Kunci baris sesi meja (``touch_row``) di savepoint. Bentrok serialisasi dengan transaksi refund paralel ->
        event ditunda ke percobaan berikut (``session_busy``), tidak pernah diproses dengan snapshot basi. False bila
        ditunda. Kunci TIDAK dipegang selama POST (hanya saat membekukan dan saat memproses 2xx)."""
        self.ensure_one()
        session = self.table_session_id.sudo()
        try:
            with self.env.cr.savepoint():
                session._efn_lock()
        except TransactionRollbackError:
            _logger.info("EFN QR outbox %s: sesi meja %s sedang diubah transaksi lain; ditunda", self.id, session.id)
            self._efn_retry_or_fail("session_busy")
            return False
        return True

    def _efn_freeze_close_orders(self):
        """K3 r15 (L-83): hitung ``close.orders`` (neto refund yang sudah dibayar) di bawah kunci sesi, lalu BEKUKAN.

        Refund yang dibayar sebelum pembekuan -> tercermin di neto (``netted_refund_ids``). Refund sesudahnya ->
        ``pos.order._efn_qr_refund_mode`` = ``awaiting`` (tanpa reverse sekarang); setelah 2xx di-enqueue sebagai
        reverse ``earn_channel:"qr"`` terhadap ``eligible_amount`` snapshot. Snapshot tidak dihitung ulang."""
        self.ensure_one()
        if not self._efn_lock_session():
            return False
        entries, refunded, _eligible = self.table_session_id.sudo()._efn_close_orders()
        self.write(
            {
                "orders_snapshot": entries,
                "netted_refund_ids": refunded.ids,
                "snapshot_frozen": True,
                "next_attempt_at": fields.Datetime.now(),
            }
        )
        self._efn_trigger()
        return True

    @staticmethod
    def _efn_error_code(response):
        try:
            data = response.json()
        except ValueError:
            return None
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else None

    def _efn_max_hours(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn_pos_bff_order.outbox_max_hours", DEFAULT_MAX_HOURS)
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
            "EFN QR outbox %s (%s) gagal %s, percobaan %d; ulang %ds", self.id, self.event, code, attempts, delay
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
            event = {
                "event": "EFN_QR_EVENT_FAILED",
                "outbox_id": self.id,
                "kind": self.event,
                "table_session_id": self.table_session_id.id,
                "company_id": self.company_id.id,
                "error": code,
            }
            _alert_logger.error("EFN_QR_EVENT_FAILED %s", json.dumps(event, sort_keys=True))
            if self._efn_is_close_paid():
                self.table_session_id.sudo()._efn_alert_awaiting_refunds(self)

    @api.autovacuum
    def _gc_efn_qr_outbox_done(self):
        """Outbox ``done`` > 30 hari dihapus (pola efn_online_order)."""
        limit_date = fields.Datetime.now() - timedelta(days=30)
        self.sudo().search([("state", "=", "done"), ("done_at", "<", limit_date)], limit=5000).unlink()
