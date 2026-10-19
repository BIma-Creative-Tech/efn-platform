"""Outbox publikasi kebijakan poin Odoo -> BFF (docs/04 §13.1 no. 3, L-61; pola ``efn.pos.loyalty.outbox``).

- Satu record per kebijakan yang disetujui (``idempotency_key = loyalty_policy:<company_code>:<version>``, UNIQUE).
  Dibuat di transaksi yang sama dengan persetujuan; dikirim cron (``_trigger`` segera + cadangan tiap 5 menit),
  baris dikunci ``FOR UPDATE SKIP LOCKED``.
- ``PUT /v1/pos/loyalty/policies/{company_code}/{version}`` lewat klien Odoo->BFF yang ada (https + CA internal +
  ``X-EFN-Service-Key``) + ``X-EFN-Signature: t=<unix>,v1=<hex>`` (kunci ``loyalty_policy_signing_key``). Body
  dibangun saat kirim dari kebijakan (read-only setelah approved, jadi deterministik) dan ditandatangani ulang dengan
  ``t`` baru di setiap percobaan (replay window BFF 300 s). Hanya hash body (SHA-256) yang disimpan.
- **Tanpa kunci** -> publikasi tertahan (tetap ``pending``, ``last_error=signing_key_missing``), log ERROR tanpa
  rahasia, aktivitas sekali ke Loyalty Manager; tidak pernah dikirim tanpa tanda tangan dan tidak jatuh ke ``failed``
  karena umur.
- 2xx -> ``done`` + kebijakan ``published``. 409 ``policy_conflict`` / 422 ``policy_out_of_bounds`` / 400 -> ``failed``
  final + aktivitas (kebijakan tetap ``approved``; perbaikan = versi baru). 401 (tanda tangan/replay, mis. jam
  bergeser atau rotasi kunci), 404 (endpoint belum di-deploy), 429, 5xx, jaringan -> coba ulang dengan backoff
  eksponensial 1 menit .. 1 jam sampai ``efn.loyalty.policy_outbox_max_hours`` (72) -> ``failed``.
"""

import hashlib
import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError

from . import signing

_logger = logging.getLogger(__name__)

POLICY_PATH = "/v1/pos/loyalty/policies/{company_code}/{version}"
STATES = [("pending", "Menunggu"), ("done", "Terkirim"), ("failed", "Gagal")]
RETRY_STATUSES = frozenset({401, 404, 408, 429})
DEFAULT_MAX_HOURS = 72
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
_CODE_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_:")


def _now_unix():
    """Detik Unix untuk ``t=`` tanda tangan (titik mock test; jangan mem-patch ``time.time`` global)."""
    return int(time.time())


def _safe_code(value, fallback):
    if isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= _CODE_SAFE:
        return value
    return fallback


class EfnLoyaltyPolicyOutbox(models.Model):
    _name = "efn.loyalty.policy.outbox"
    _description = "EFN Loyalty Policy Publication Outbox"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "idempotency_key"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    policy_id = fields.Many2one(
        "efn.loyalty.policy", required=True, readonly=True, ondelete="restrict", index=True, check_company=True
    )
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    state = fields.Selection(STATES, default="pending", required=True, readonly=True, index=True, tracking=True)
    attempt_count = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(readonly=True, index=True, default=fields.Datetime.now)
    last_error = fields.Char(readonly=True, help="Kode error saja (tanpa body/rahasia).")
    last_status = fields.Integer(string="HTTP terakhir", readonly=True)
    body_sha256 = fields.Char(string="SHA-256 body terakhir", readonly=True)
    done_at = fields.Datetime(readonly=True)
    activity_flagged = fields.Char(readonly=True, copy=False, help="Kode alasan aktivitas terakhir (sekali per kode).")

    _idempotency_key_unique = models.Constraint(
        "UNIQUE (idempotency_key)", "Kunci idempotensi outbox kebijakan poin harus unik."
    )

    # ---------------------------------------------------------------- enqueue
    @api.model
    def _efn_enqueue_policy(self, policy):
        key = f"loyalty_policy:{policy.company_code}:{policy.version}"
        existing = self.sudo().search([("idempotency_key", "=", key)], limit=1)
        if existing:
            return existing
        vals = {"company_id": policy.company_id.id, "policy_id": policy.id, "idempotency_key": key}
        return self.sudo().create(vals)

    @api.model
    def _efn_trigger(self):
        cron = self.env.ref("efn_loyalty_accounting.ir_cron_efn_loyalty_policy_outbox", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------- tombol UI
    def action_efn_retry(self):
        """Kirim ulang outbox ``failed`` (mis. setelah BFF diperbaiki): Loyalty Manager / Accounting Manager company
        record. Konflik isi (409) tetap akan gagal -- perbaikan isi = versi kebijakan baru."""
        user = self.env.user
        allowed = ("efn_loyalty_accounting.group_loyalty_manager", "account.group_account_manager")
        if not any(user.has_group(xmlid) for xmlid in allowed):
            raise AccessError(_("Hanya Loyalty Manager / Accounting Manager yang dapat mengirim ulang publikasi."))
        self.check_access("read")
        for record in self:
            if record.company_id not in user.company_ids:
                raise AccessError(_("Outbox kebijakan poin milik company lain."))
            if record.state != "failed":
                raise UserError(_("Hanya outbox berstatus Gagal yang dapat dikirim ulang."))
        for record in self.sudo():
            record.write(
                {"state": "pending", "next_attempt_at": fields.Datetime.now(), "attempt_count": 0, "done_at": False}
            )
            _logger.info("EFN loyalty policy outbox %s dikirim ulang oleh uid=%s", record.id, self.env.uid)
        self._efn_trigger()
        return True

    # ----------------------------------------------------------------- runner
    @api.model
    def _cron_efn_loyalty_policy_outbox_run(self):
        self._efn_process_due(limit=50, commit=True)

    @api.model
    def _efn_process_due(self, limit=20, commit=False):
        processed = []
        while len(processed) < limit:
            self.flush_model()
            self.env.cr.execute(
                """
                SELECT id FROM efn_loyalty_policy_outbox
                 WHERE state = 'pending'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= %s)
                   AND NOT (id = ANY(%s))
                 ORDER BY id
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
            if commit:
                self.env["ir.cron"]._commit_progress(1)
        return processed

    def _efn_send_one(self):
        self.ensure_one()
        if self.state != "pending":
            return
        policy = self.policy_id.sudo()
        if policy.state not in ("approved", "published"):
            self._efn_finish("failed", "policy_not_approved")
            return
        try:
            key = signing.read_signing_key()
        except signing.SigningKeyMissing as exc:
            self._efn_hold_without_key(exc.code)
            return
        body = signing.canonical_json(policy._efn_publish_body())
        header = signing.sign(key, _now_unix(), body)
        del key
        path = POLICY_PATH.format(company_code=policy.company_code, version=policy.version)
        self.write({"body_sha256": hashlib.sha256(body).hexdigest()})
        try:
            response = self.env["efn.bff.client"]._efn_loyalty_request(
                "PUT",
                path,
                body=body,
                extra_headers={signing.SIGNATURE_HEADER: header},
                log_context={"company": policy.company_code, "version": policy.version},
            )
        except EfnBffError as exc:
            self._efn_retry_or_fail(exc.code, status=0)
            return
        status = response.status
        if 200 <= status < 300:
            self.write({"last_status": status})
            self._efn_finish("done", False)
            policy._efn_mark_published()
            return
        error = response.data.get("error")
        reason = error.get("code") if isinstance(error, dict) else response.data.get("reason")
        reason = _safe_code(reason, f"http_{status}")
        if status in RETRY_STATUSES:
            self._efn_retry_or_fail(reason, status=status)
            return
        self.write({"last_status": status})
        self._efn_finish("failed", reason)

    # --------------------------------------------------------------- results
    def _efn_hold_without_key(self, code):
        """Tanpa kunci: publikasi tertahan (tidak dikirim tanpa tanda tangan), ERROR tanpa rahasia."""
        self.ensure_one()
        _logger.error(
            "EFN loyalty policy outbox %s tertahan: kunci tanda tangan tidak tersedia (%s; cek env %s)",
            self.id,
            code,
            signing.SIGNING_KEY_FILE_ENV,
        )
        attempts = self.attempt_count + 1
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_MAX_SECONDS)
        self.write(
            {
                "attempt_count": attempts,
                "last_error": code,
                "next_attempt_at": fields.Datetime.now() + timedelta(seconds=delay),
            }
        )
        self._efn_schedule_activity(code)

    def _efn_retry_or_fail(self, code, status):
        self.ensure_one()
        max_hours = self._efn_max_hours()
        if self.create_date and self.create_date < fields.Datetime.now() - timedelta(hours=max_hours):
            self.write({"last_status": status})
            self._efn_finish("failed", f"max_age:{code}")
            return
        attempts = self.attempt_count + 1
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_MAX_SECONDS)
        _logger.warning(
            "EFN loyalty policy outbox %s gagal %s (HTTP %s), percobaan %d; ulang dalam %ds",
            self.id,
            code,
            status,
            attempts,
            delay,
        )
        self.write(
            {
                "attempt_count": attempts,
                "last_error": code,
                "last_status": status,
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
            _logger.warning("EFN loyalty policy outbox %s GAGAL final: %s", self.id, code)
            self._efn_schedule_activity(code)

    def _efn_max_hours(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn.loyalty.policy_outbox_max_hours", DEFAULT_MAX_HOURS)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MAX_HOURS

    def _efn_schedule_activity(self, reason):
        """Aktivitas To-Do sekali per kode alasan ke Loyalty Manager company (fallback Accounting Manager)."""
        self.ensure_one()
        if self.activity_flagged == reason:
            return
        user = self._efn_review_user()
        if user:
            self.sudo().activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=_("Publikasi kebijakan Poin EFN"),
                note=_(
                    "Publikasi kebijakan %(policy)s ke BFF perlu ditinjau (%(reason)s).",
                    policy=self.policy_id.display_label,
                    reason=reason,
                ),
            )
        self.sudo().write({"activity_flagged": reason})

    def _efn_review_user(self):
        Users = self.env["res.users"].sudo()
        group_field = "all_group_ids" if "all_group_ids" in Users._fields else "groups_id"
        for xmlid in ("efn_loyalty_accounting.group_loyalty_manager", "account.group_account_manager"):
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if not group:
                continue
            user = Users.search(
                [
                    (group_field, "in", group.ids),
                    ("company_ids", "in", self.company_id.ids),
                    ("share", "=", False),
                    ("id", "!=", self.env.ref("base.user_root").id),
                ],
                order="id",
                limit=1,
            )
            if user:
                return user
        return Users
