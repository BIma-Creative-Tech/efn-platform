"""Outbox event pesanan online Odoo -> BFF (docs/04 §10.5, K-ON1; pola outbox ``efn_pos_loyalty_ledger``).

- Record dibuat DI DALAM transaksi perubahan state order (``efn.online.order._efn_emit``) -> event tidak pernah
  hilang/terkirim untuk perubahan yang di-rollback. ``seq`` naik monoton per order.
- **G18-QA-01** (kontrak BFF bf5c5f9): ``idempotency_key`` = ``online:<platform_order_ref>:<event>:<seq>`` (tanpa id
  lokal Odoo, yang terpakai ulang setelah DB dibuat ulang / dipulihkan) dan ``event_id`` = UUID v4 acak per baris
  outbox (dibuat sekali saat enqueue, disimpan, unik). Retry mengirim ``event_id`` & kunci yang sama (dibaca dari
  record). ``seq`` berbasis waktu (lihat ``efn.online.order._efn_next_seq``) sehingga tetap naik setelah restore.
  ``409 event_conflict`` (kunci sama, isi beda) = final + pesanan ``review``.
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
import re
import time
import uuid
from datetime import timedelta

import requests

from odoo import _, api, fields, models

from odoo.addons.efn_core.models.efn_bff_client import SERVICE_KEY_HEADER, EfnBffError

from .efn_online_channel import iso_utc

_logger = logging.getLogger(__name__)

EVENT_PATH = "/v1/pos/online/order-events"
EVENTS = [
    ("placed", "placed"),
    ("paid", "paid"),
    ("confirmed", "confirmed"),
    ("stock_short", "stock_short"),
    # G22 B-7b (docs/04 §14.B): opsional, tiket KDS pertama dikerjakan (tanpa transisi state di BFF).
    ("preparing", "preparing"),
    ("ready", "ready"),
    ("picked_up", "picked_up"),
    ("cancelled", "cancelled"),
    ("refunded", "refunded"),
    # L-58 (G20): refund pesanan bayar di kasir benar-benar dikembalikan di POS (bukan sekadar diminta).
    ("refund_settled", "refund_settled"),
    # G26 L-100 (docs/04 §17.5): Verifikator menolak bukti transfer; order tetap `placed` (pelanggan unggah ulang).
    ("transfer_rejected", "transfer_rejected"),
    ("review", "review"),
]
HMAC_FILE_ENV = "EFN_ONLINE_EVENT_HMAC_FILE"
DEFAULT_HMAC_FILE = "/run/secrets/odoo_online_event_hmac"
SIGNATURE_HEADER = "X-EFN-Signature"
# Sama dengan validasi BFF (order-events.service.ts): tanpa '@' -> email tidak pernah dikirim.
LOGIN_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
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


def event_key(platform_order_ref, event, seq):
    """Kunci idempotensi event (G18-QA-01): tanpa id lokal Odoo (format divalidasi BFF ``KEY_RE``)."""
    return f"online:{platform_order_ref}:{event}:{seq}"


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
        "efn.online.order",
        required=True,
        index=True,
        readonly=True,
        ondelete="cascade",
        check_company=True,
    )
    refund_id = fields.Many2one("efn.online.refund", readonly=True, ondelete="set null", check_company=True)
    event = fields.Selection(EVENTS, required=True, readonly=True)
    seq = fields.Integer(required=True, readonly=True)
    state_snapshot = fields.Char(readonly=True)
    initiator = fields.Selection([("bff", "bff"), ("odoo", "odoo")], readonly=True)
    occurred_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    # G18-QA-01: identitas event global (bukan id lokal); dibuat sekali saat enqueue, dipakai ulang saat retry.
    event_id = fields.Char(required=True, readonly=True, copy=False, index=True)
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
    # G26 L-100: kode tambahan event (mis. alasan `transfer_rejected`), dibekukan saat enqueue. Kode allowlist saja.
    detail_code = fields.Char(readonly=True, help="Kode tambahan event (tanpa teks bebas / PII).")

    _idempotency_key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi event harus unik.")
    _event_id_unique = models.Constraint("unique (event_id)", "event_id event harus unik.")
    _order_seq_unique = models.Constraint("unique (order_id, seq)", "seq event harus unik per pesanan.")

    # ---------------------------------------------------------------- enqueue
    @api.model
    def _efn_enqueue(self, order, event, seq, refund=None, detail_code=None):
        refund = refund or self.env["efn.online.refund"]
        event_id = str(uuid.uuid4())
        record = self.sudo().create(
            {
                "company_id": order.company_id.id,
                "order_id": order.id,
                "refund_id": refund.id or False,
                "event": event,
                "seq": seq,
                "state_snapshot": order.state,
                "initiator": refund.initiator or False,
                "event_id": event_id,
                "idempotency_key": event_key(order.platform_order_ref, event, seq),
                "detail_code": detail_code or False,
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
                # G22 W-12 (docs/04 §14.C.4): points_value, cash_amount, eligible_amount_refunded (Rupiah bulat).
                **refund._efn_loyalty_block(),
            }
            if refund.initiator == "odoo" and refund.initiator_user_id:
                # SEC-95/L-16: identitas pemicu untuk aturan empat mata di BFF. Hanya id + login non-email
                # (login berbentuk email/karakter lain = PII -> tidak dikirim, cukup uid).
                refund_payload["initiator_uid"] = refund.initiator_user_id.id
                login = refund.initiator_user_id.login or ""
                if LOGIN_SAFE_RE.match(login):
                    refund_payload["initiator_login"] = login
            if self.event == "refund_settled":
                # L-58: uang refund keluar di kasir; BFF menandai settled HANYA dari event ini.
                refund_payload["provider_refund_ref"] = f"pos:{refund.provider_refund_ref}"
                refund_payload["settled_at"] = (
                    refund.settled_at.replace(microsecond=0).isoformat() + "Z" if refund.settled_at else None
                )
        payload = {
            "schema_version": 1,
            "idempotency_key": self.idempotency_key,
            "event_id": self.event_id,
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
        if self.event == "paid" and order.payment_mode in ("counter", "bank_transfer"):
            # G20 E-45 (docs/04 §12.2 no. 7): metode saja, tanpa identitas kasir/sesi/POS. QRIS tidak membawa kunci
            # ini (payload & sidik event pra-G20 tidak berubah). G22 §14.C.4: + nilai Poin EFN (BFF commit redeem).
            # G26 L-100: transfer bank memakai bentuk yang sama, tanpa identitas Verifikator.
            points_value = order._efn_amount_out(order.points_value or 0.0)
            payload["payment"] = {
                "method": order.payment_mode,
                "points_value": points_value,
            }
        if self.event == "transfer_rejected":
            # G26 §17.5: kode alasan dari allowlist saja; catatan internal staf TIDAK pernah dikirim.
            payload["transfer"] = {"reason": self.detail_code or None}
        if self.event == "ready":
            # G22 B-7a: siap dari layar dapur (semua tiket KDS) atau tombol staf.
            payload["ready_source"] = order.ready_source or "manual"
        if self.event == "preparing":
            # G22 B-7b: BFF mengisi online_order_part.kitchen_started_at tanpa transisi state.
            payload["kitchen_started_at"] = iso_utc(order.kitchen_started_at) or payload["occurred_at"]
        if self.event == "picked_up" and order.channel_id.loyalty_earn:
            # G22 W-12 (§14.C.3 "Earn"): dasar earn PT bagian; hanya bila kanal mengaktifkan earn (kill switch Odoo).
            payload["loyalty"] = {"eligible_amount": order.eligible_amount or 0}
        if self.event == "review":
            # L-57 (QA G20): BFF hanya menerima `cancelled -> review` untuk pelunasan kasir terlambat bila event membawa
            # alasannya (`counter_paid_after_cancel|expiry`); BFF mengabaikan kode lain (-> null). Kode saja, tanpa PII.
            payload["review_reason"] = order.review_reason or None
        if self.event == "cancelled":
            # SEC-95/L-16: kode alasan (tanpa teks bebas) agar BFF bisa membedakan batal ops (refund_attention).
            payload["cancel_reason"] = order.cancel_reason or None
        return payload

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
            # FP semgrep (G17): yang di-log hanya NAMA env var (konstanta HMAC_FILE_ENV), nilai secret tidak pernah.
            # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
            _logger.error(
                "EFN online outbox: secret HMAC event tidak terbaca (cek env %s); tidak dikirim",
                HMAC_FILE_ENV,
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
            response = requests.post(
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
            _logger.warning(
                "EFN online outbox %s: BFF tidak terjangkau (%s)",
                self.id,
                type(exc).__name__,
            )
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
            _logger.info(
                "EFN online outbox %s (%s seq %s) -> %s",
                self.id,
                self.event,
                self.seq,
                status,
            )
            return
        if status == 409 and self._efn_error_code(response) == "event_conflict":
            # G18-QA-01: BFF punya event berkunci sama dengan isi berbeda (mis. Odoo dipulihkan) -> final + review.
            self._efn_finish("failed", "event_conflict")
            order = self.order_id.sudo()
            if order.state not in ("review", "picked_up", "cancelled"):
                order._efn_set_review("event_conflict")
            return
        if status in FINAL_HTTP:
            self._efn_finish("failed", f"http_{status}")
            return
        self._efn_retry_or_fail(f"http_{status}")

    @staticmethod
    def _efn_error_code(response):
        """Kode error snake_case dari body BFF (``{"error": {"code": ...}}``); selain itu None."""
        try:
            data = response.json()
        except ValueError:
            return None
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else None

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
            "EFN online outbox %s (%s) gagal %s, percobaan %d; ulang %ds",
            self.id,
            self.event,
            code,
            attempts,
            delay,
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
