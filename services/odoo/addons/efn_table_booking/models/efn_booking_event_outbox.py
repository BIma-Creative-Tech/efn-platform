"""Outbox event reservasi Odoo -> BFF ``POST /v1/pos/booking/events`` (docs/04 §15.5, pola §10.5 / E-27 outbox).

- Record dibuat DI DALAM transaksi perubahan status (tidak ada event untuk perubahan yang di-rollback).
  ``seq`` = ``efn.table.booking.seq`` saat event dibuat (naik monoton per reservasi), ``idempotency_key`` =
  ``booking:<platform_booking_ref>:<event>:<seq>``.
- Payload tanpa PII: ``{schema_version, idempotency_key, seq, platform_booking_ref, company_code, outlet_code, event,
  state, occurred_at}`` (``guest_label``/``note`` tidak pernah dikirim).
- TLS + CA internal (helper ``efn.bff.client``) + ``X-EFN-Service-Key`` + ``X-EFN-Signature: t=<unix>,v1=<hex
  HMAC-SHA256(secret, "<t>." + rawBody)>``; secret ``odoo_booking_event_hmac`` dari file env
  ``EFN_BOOKING_EVENT_HMAC_FILE`` (default ``/run/secrets/odoo_booking_event_hmac``). Tanpa secret = tidak dikirim.
- 2xx -> ``done``; 400/401/403/404/409/422 -> ``failed`` FINAL + log alert ``EFN_BOOKING_EVENT_FAILED``; 429/5xx/
  timeout -> retry 1 m -> 1 j; umur > 48 jam -> ``failed``. Urutan per reservasi dijaga (seq kecil dulu).
"""

import json
import logging
import os
import time
from datetime import timedelta

import requests

from odoo import api, fields, models

from odoo.addons.efn_core.models.efn_bff_client import SERVICE_KEY_HEADER, EfnBffError
from odoo.addons.efn_pos_bff_order.models.efn_pos_qr_event_outbox import sign_body
from odoo.addons.efn_pos_bff_order.models.qr_common import iso_utc

_logger = logging.getLogger(__name__)
_alert_logger = logging.getLogger("odoo.addons.efn_table_booking.alert")

EVENT_PATH = "/v1/pos/booking/events"
EVENTS = [(e, e) for e in ("confirmed", "seated", "done", "cancelled", "no_show")]
HMAC_FILE_ENV = "EFN_BOOKING_EVENT_HMAC_FILE"
DEFAULT_HMAC_FILE = "/run/secrets/odoo_booking_event_hmac"
SIGNATURE_HEADER = "X-EFN-Signature"
FINAL_HTTP = frozenset({400, 401, 403, 404, 409, 422})
MAX_HOURS = 48
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600


class EfnBookingHmacMissing(Exception):
    pass


def read_booking_hmac_secret():
    """Secret HMAC event reservasi; dibaca per kirim (rotasi tanpa restart). Tidak pernah di-log."""
    path = os.environ.get(HMAC_FILE_ENV) or DEFAULT_HMAC_FILE
    try:
        with open(path, encoding="utf-8") as handle:
            secret = handle.read().strip()
    except OSError:
        raise EfnBookingHmacMissing() from None
    if len(secret) < 32:
        raise EfnBookingHmacMissing()
    return secret.encode("utf-8")


class EfnBookingEventOutbox(models.Model):
    _name = "efn.booking.event.outbox"
    _description = "EFN - Outbox event reservasi ke BFF"
    _order = "id"
    _check_company_auto = True
    _rec_name = "idempotency_key"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    booking_id = fields.Many2one(
        "efn.table.booking", required=True, index=True, readonly=True, ondelete="cascade", check_company=True
    )
    event = fields.Selection(EVENTS, required=True, readonly=True)
    state_snapshot = fields.Char(required=True, readonly=True)
    seq = fields.Integer(required=True, readonly=True)
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

    @api.model
    def _efn_enqueue(self, booking, event):
        record = self.sudo().create(
            {
                "company_id": booking.company_id.id,
                "booking_id": booking.id,
                "event": event,
                "state_snapshot": booking.state,
                "seq": booking.seq,
                "idempotency_key": f"booking:{booking.platform_booking_ref}:{event}:{booking.seq}",
            }
        )
        cron = self.env.ref("efn_table_booking.ir_cron_efn_booking_outbox", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return record

    def _efn_payload(self):
        self.ensure_one()
        booking = self.booking_id.sudo()
        return {
            "schema_version": 1,
            "idempotency_key": self.idempotency_key,
            "seq": self.seq,
            "platform_booking_ref": booking.platform_booking_ref,
            "company_code": booking.company_id.efn_brand_code or None,
            "outlet_code": booking.outlet_id.code,
            "event": self.event,
            "state": self.state_snapshot,
            "occurred_at": iso_utc(self.occurred_at),
        }

    @api.model
    def _cron_efn_booking_outbox_run(self):
        self._efn_process_due(limit=200)

    @api.model
    def _efn_process_due(self, limit=50):
        processed = []
        while len(processed) < limit:
            self.flush_model()
            self.env.cr.execute(
                """
                SELECT o.id FROM efn_booking_event_outbox o
                 WHERE o.state = 'pending'
                   AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= %s)
                   AND NOT (o.id = ANY(%s))
                   AND NOT EXISTS (
                       SELECT 1 FROM efn_booking_event_outbox p
                        WHERE p.booking_id = o.booking_id AND p.seq < o.seq AND p.state = 'pending')
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
        return processed

    def _efn_send_one(self):
        self.ensure_one()
        if self.state != "pending":
            return
        client = self.env["efn.bff.client"]
        try:
            secret = read_booking_hmac_secret()
            base_url = client._get_base_url()
            verify = client._get_verify(base_url)
            service_key = client._read_api_key()
        except EfnBookingHmacMissing:
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            _logger.error("EFN booking outbox: secret HMAC event tidak terbaca (cek env %s)", HMAC_FILE_ENV)
            self._efn_retry_or_fail("hmac_missing")
            return
        except EfnBffError as exc:
            self._efn_retry_or_fail(exc.code)
            return
        raw = json.dumps(self._efn_payload(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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
            _logger.error("EFN booking outbox %s: verifikasi TLS BFF gagal", self.id)
            self._efn_retry_or_fail("bff_tls_error")
            return
        except requests.exceptions.Timeout:
            self._efn_retry_or_fail("bff_timeout")
            return
        except requests.exceptions.RequestException as exc:
            _logger.warning("EFN booking outbox %s: BFF tidak terjangkau (%s)", self.id, type(exc).__name__)
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
            return
        if status in FINAL_HTTP:
            self._efn_finish("failed", f"http_{status}")
            return
        self._efn_retry_or_fail(f"http_{status}")

    def _efn_retry_or_fail(self, code):
        self.ensure_one()
        if self.create_date and self.create_date < fields.Datetime.now() - timedelta(hours=MAX_HOURS):
            self._efn_finish("failed", f"max_age:{code}")
            return
        attempts = self.attempt_count + 1
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_MAX_SECONDS)
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
                "event": "EFN_BOOKING_EVENT_FAILED",
                "outbox_id": self.id,
                "kind": self.event,
                "booking_id": self.booking_id.id,
                "company_id": self.company_id.id,
                "error": code,
            }
            _alert_logger.error("EFN_BOOKING_EVENT_FAILED %s", json.dumps(event, sort_keys=True))
