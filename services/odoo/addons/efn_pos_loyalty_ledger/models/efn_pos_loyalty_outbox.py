"""Transactional outbox panggilan loyalty POS -> BFF (docs/04 §9.2-§9.4, docs/08 §6.3 D).

Record dibuat DI DALAM transaksi sync order (`pos.order._process_saved_order`), sehingga order
yang tersimpan selalu punya antrean poinnya dan order yang di-rollback tidak meninggalkan apa pun.
POS tetap jalan walau BFF mati: pengiriman terjadi di cron (`_trigger` segera setelah sync,
cadangan tiap 1 menit) dengan backoff eksponensial 1 menit -> maks 1 jam.

Aturan:
- `idempotency_key` unik (DB constraint) dan sama dengan kunci kontrak BFF
  (`pos_earn:<uuid>`, `pos_redeem:<uuid>`, `pos_release:<reservation id>`,
  `pos_reverse:<uuid refund>`) -> sync ganda tidak membuat antrean ganda; BFF juga idempoten.
- **Payload tidak disimpan.** Dibangun saat kirim dari order & partner: nomor HP tidak pernah
  tersimpan di outbox. Partner sudah dianonimkan/tidak ber-HP Indonesia -> `skipped`.
  Yang disimpan hanya angka non-pribadi (eligible_amount, refund_ratio) agar audit & batas
  kumulatif refund deterministik.
- `reverse` menunggu `earn`/`redeem_commit` order asal selesai (`done`/`failed`/`skipped`);
  BFF `409 earn_pending` (K-6) juga dicoba ulang.
- Retry hanya untuk `earn_pending`, 429, dan 5xx/jaringan (docs/04 §9.7); 409 lain final, termasuk
  `earn_limit_exceeded` (§9.8 v4.3: batas per order/harian member/PT & kasir per jam) -> `failed`
  + order `review` + aktivitas. Reverse atas order yang earn-nya ditolak menerima `earn_pending`
  dari BFF (dicoba ulang) sampai `outbox_max_hours` (48) -> `failed` (`max_age:earn_pending`) +
  `review`; tidak ada restore/clawback otomatis.
  404 pada release = selesai.
- 4xx definitif -> `failed` + order `review` + aktivitas ke POS manager. Umur >
  `efn.pos.loyalty.outbox_max_hours` (48) -> `failed`.
- SEC-60: `hold` = tidak pernah dikirim cron. Hanya POS manager company terkait yang dapat
  meneruskan (`pending`) atau membatalkan (`skipped`) lewat tombol form; keduanya memeriksa grup
  & company secara eksplisit (metode publik masuk allowlist RPC `efn_core/tests/test_rpc_surface.py`).
- Dua worker tidak mengirim record yang sama: baris dikunci `FOR UPDATE SKIP LOCKED` sampai
  commit (idempotency BFF tetap jaring pengaman).
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError, mask_phone, partner_member_ref

_logger = logging.getLogger(__name__)

EARN_PATH = "/v1/pos/loyalty/earn"
COMMIT_PATH = "/v1/pos/loyalty/redeem/commit"
RELEASE_PATH = "/v1/pos/loyalty/redeem/release"
REVERSE_PATH = "/v1/pos/loyalty/reverse"

KINDS = [
    ("earn", "Earn"),
    ("redeem_commit", "Commit tukar poin"),
    ("redeem_release", "Lepas reservasi"),
    ("reverse", "Clawback refund"),
]
STATES = [
    ("pending", "Menunggu"),
    ("done", "Terkirim"),
    ("skipped", "Dilewati"),
    ("failed", "Gagal"),
    # SEC-60: reverse refund berpoin dengan baris penyesuaian salah/hilang TIDAK dikirim
    # (restore poin ditahan) sampai POS manager memutuskan (`action_efn_hold_approve/cancel`).
    ("hold", "Ditahan (review supervisor)"),
]
FINAL_STATES = ("done", "failed", "skipped")
# Alasan 4xx yang boleh dicoba ulang (bukan penolakan bisnis final).
RETRYABLE_REASONS = frozenset({"earn_pending", "rate_limited"})
DEFAULT_MAX_HOURS = 48
BACKOFF_BASE_SECONDS = 60
BACKOFF_MAX_SECONDS = 3600
DEFER_SECONDS = 60
_CODE_SAFE = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_:")


def _safe_code(value, fallback):
    """Kode error dari BFF disimpan hanya bila berbentuk snake_case pendek (tanpa data bebas)."""
    if isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= _CODE_SAFE:
        return value
    return fallback


def _int_or_zero(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class _SkipSend(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class EfnPosLoyaltyOutbox(models.Model):
    _name = "efn.pos.loyalty.outbox"
    _description = "EFN POS Loyalty Outbox"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id"
    _check_company_auto = True
    _rec_name = "idempotency_key"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    # Wajib untuk semua jenis kecuali `redeem_release` (order draft yang dibatalkan dihapus
    # oleh pos.order.remove_from_ui, sehingga release hanya menunjuk reservasi).
    order_id = fields.Many2one(
        "pos.order", string="Order", index=True, readonly=True, ondelete="restrict", check_company=True
    )
    original_order_id = fields.Many2one(
        "pos.order",
        string="Order asal (refund)",
        index="btree_not_null",
        readonly=True,
        ondelete="restrict",
        check_company=True,
    )
    kind = fields.Selection(KINDS, required=True, readonly=True, index=True)
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    reservation_id = fields.Many2one(
        "efn.pos.loyalty.reservation", readonly=True, ondelete="set null", check_company=True
    )
    eligible_amount = fields.Integer(string="Dasar poin (Rp)", readonly=True)
    refund_ratio = fields.Float(digits=(16, 6), readonly=True)
    state = fields.Selection(STATES, required=True, default="pending", index=True, readonly=True, tracking=True)
    attempt_count = fields.Integer(readonly=True)
    next_attempt_at = fields.Datetime(index=True, readonly=True, default=fields.Datetime.now)
    last_error = fields.Char(readonly=True, help="Kode error saja (tanpa payload/data pribadi).")
    points_delta = fields.Integer(string="Delta poin (BFF)", readonly=True)
    done_at = fields.Datetime(readonly=True)
    activity_flagged = fields.Boolean(readonly=True, copy=False)

    _idempotency_key_unique = models.Constraint(
        "unique (idempotency_key)", "Kunci idempotensi outbox loyalty harus unik."
    )
    _order_required = models.Constraint(
        "CHECK (kind = 'redeem_release' OR order_id IS NOT NULL)",
        "Outbox loyalty selain release wajib menunjuk order.",
    )

    # ------------------------------------------------- keputusan supervisor (SEC-60)
    def _efn_check_hold_decider(self):
        """POS manager (bukan kasir) pada company record; record harus berstatus `hold`."""
        if not self.env.su and not self.env.user.has_group("point_of_sale.group_pos_manager"):
            raise AccessError(_("Hanya POS manager yang dapat memutuskan outbox Poin EFN yang ditahan."))
        self.check_access("read")  # record rule multi-company
        for record in self:
            if record.company_id not in self.env.user.company_ids and not self.env.su:
                raise AccessError(_("Outbox Poin EFN milik company lain."))
            if record.state != "hold":
                raise UserError(_("Hanya outbox berstatus 'Ditahan' yang dapat diputuskan."))

    def action_efn_hold_approve(self):
        """Supervisor menyetujui reverse yang ditahan: dikirim apa adanya (termasuk restore poin)."""
        self._efn_check_hold_decider()
        for record in self.sudo():
            record.write({"state": "pending", "next_attempt_at": fields.Datetime.now(), "last_error": False})
            record.message_post(
                body=_("Reverse ditahan disetujui oleh %(user)s.", user=self.env.user.name),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            _logger.info("EFN loyalty outbox %s: hold disetujui uid=%s", record.id, self.env.uid)
        self._efn_trigger()
        return True

    def action_efn_hold_cancel(self):
        """Supervisor membatalkan reverse yang ditahan: tidak ada clawback/restore otomatis
        (penyesuaian poin dilakukan manual lewat BFF/Finance)."""
        self._efn_check_hold_decider()
        for record in self.sudo():
            record.write({"state": "skipped", "last_error": "supervisor_cancelled", "done_at": fields.Datetime.now()})
            record.message_post(
                body=_("Reverse ditahan dibatalkan oleh %(user)s.", user=self.env.user.name),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
            _logger.info("EFN loyalty outbox %s: hold dibatalkan uid=%s", record.id, self.env.uid)
        return True

    # ------------------------------------------------------------- enqueue
    @api.model
    def _efn_enqueue(self, vals):
        """Buat record bila kunci idempotensi belum ada (sync ganda -> tidak ada antrean ganda)."""
        existing = self.sudo().search([("idempotency_key", "=", vals["idempotency_key"])], limit=1)
        if existing:
            return existing
        return self.sudo().create(vals)

    @api.model
    def _efn_trigger(self):
        cron = self.env.ref("efn_pos_loyalty_ledger.ir_cron_efn_loyalty_outbox", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # -------------------------------------------------------------- runner
    @api.model
    def _cron_efn_loyalty_outbox_run(self):
        self._efn_process_due(limit=200, commit=True)

    @api.model
    def _efn_process_due(self, limit=50, commit=False):
        """Kirim record `pending` yang jatuh tempo, satu per satu, dengan kunci baris.

        :param bool commit: True hanya dari cron (commit per record via `ir.cron._commit_progress`);
            test memanggil dengan False.
        """
        processed = []
        while len(processed) < limit:
            self.flush_model()
            self.env.cr.execute(
                """
                SELECT id FROM efn_pos_loyalty_outbox
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
            record = self.sudo().browse(row[0])
            record._efn_send_one()
            if commit:
                self.env["ir.cron"]._commit_progress(1)
        return processed

    # ---------------------------------------------------------------- send
    def _efn_send_one(self):
        self.ensure_one()
        if self.state != "pending":
            return
        if self.kind == "reverse" and self._efn_original_pending():
            # Reverse menunggu earn/commit order asal (tidak dihitung sebagai percobaan).
            self.write({"next_attempt_at": fields.Datetime.now() + timedelta(seconds=DEFER_SECONDS)})
            return
        try:
            path, payload, log_context = self._efn_build_request()
        except _SkipSend as skip:
            self._efn_finish("skipped", skip.code)
            return
        try:
            response = self.env["efn.bff.client"]._efn_post(path, payload, log_context=log_context)
        except EfnBffError as exc:
            self._efn_retry_or_fail(exc.code)
            return
        if response.status == 200 or (self.kind == "redeem_release" and response.status == 404):
            # §9.7: 404 pada release = reservasi tidak ada lagi -> selesai.
            self._efn_apply_success(response.data)
            return
        error = response.data.get("error")
        reason = error.get("code") if isinstance(error, dict) else response.data.get("reason")
        if response.status == 429:
            reason = "rate_limited"
        reason = _safe_code(reason, f"http_{response.status}")
        if reason in RETRYABLE_REASONS:
            self._efn_retry_or_fail(reason)
            return
        self._efn_finish("failed", reason)

    def _efn_original_pending(self):
        original = self.original_order_id
        if not original:
            return False
        return bool(
            self.sudo().search_count(
                [
                    ("order_id", "=", original.id),
                    ("kind", "in", ("earn", "redeem_commit")),
                    ("state", "=", "pending"),
                ],
                limit=1,
            )
        )

    def _efn_member(self, partner):
        member, error = partner_member_ref(partner.sudo())
        if not member:
            raise _SkipSend(f"member_missing:{error}")
        return member

    def _efn_build_request(self):
        """(path, payload, log_context). Payload dibangun saat kirim (tidak disimpan)."""
        self.ensure_one()
        order = self.order_id.sudo()
        if self.kind == "redeem_release":
            reservation = self.reservation_id.sudo()
            if not reservation.bff_reservation_id:
                raise _SkipSend("reservation_missing")
            return (
                RELEASE_PATH,
                {"reservation_id": reservation.bff_reservation_id},
                {"order_ref": reservation.order_uuid},
            )
        if self.kind == "earn":
            member = self._efn_member(order.partner_id)
            company_code = order.company_id.efn_brand_code
            if not company_code:
                raise _SkipSend("company_not_configured")
            return (
                EARN_PATH,
                {
                    "idempotency_key": self.idempotency_key,
                    "order_ref": order.uuid,
                    "member_ref": member,
                    "company_code": company_code,
                    "eligible_amount": self.eligible_amount,
                    # §9.8 (v4.3, SEC-61): mengaktifkan batas earn per kasir/jam di BFF.
                    "pos_config_id": order.config_id.id,
                },
                {"member": mask_phone(member), "order_ref": order.uuid},
            )
        if self.kind == "redeem_commit":
            reservation = self.reservation_id.sudo()
            if not reservation.bff_reservation_id:
                raise _SkipSend("reservation_missing")
            # K-3: member_ref dari partner FINAL order; BFF memverifikasi kecocokan.
            member = self._efn_member(order.partner_id)
            return (
                COMMIT_PATH,
                {
                    "reservation_id": reservation.bff_reservation_id,
                    "order_ref": order.uuid,
                    "member_ref": member,
                    "value_idr": reservation.value_idr,
                    "idempotency_key": self.idempotency_key,
                },
                {"member": mask_phone(member), "order_ref": order.uuid},
            )
        if self.kind == "reverse":
            original = self.original_order_id.sudo()
            member = self._efn_member(original.partner_id)
            return (
                REVERSE_PATH,
                {
                    "idempotency_key": self.idempotency_key,
                    "original_order_ref": original.uuid,
                    "refund_order_ref": order.uuid,
                    "member_ref": member,
                    "refunded_eligible_amount": self.eligible_amount,
                    "refund_ratio": round(self.refund_ratio, 6),
                },
                {"member": mask_phone(member), "order_ref": order.uuid},
            )
        raise _SkipSend("unknown_kind")

    # ------------------------------------------------------------ results
    def _efn_apply_success(self, data):
        self.ensure_one()
        order = self.order_id.sudo()
        delta = 0
        if self.kind == "earn":
            delta = _int_or_zero(data.get("points_earned"))
            order.write({"efn_loyalty_points_earned": delta})
        elif self.kind == "redeem_commit":
            delta = -_int_or_zero(data.get("points_redeemed"))
            self.reservation_id.sudo().write({"state": "committed", "order_id": order.id})
        elif self.kind == "redeem_release":
            reservation = self.reservation_id.sudo()
            if reservation.state in ("reserved", "released"):
                reservation.write({"state": "released"})
        elif self.kind == "reverse":
            delta = _int_or_zero(data.get("points_restored")) - _int_or_zero(data.get("points_clawed_back"))
        self.write(
            {
                "state": "done",
                "done_at": fields.Datetime.now(),
                "points_delta": delta,
                "last_error": False,
                "attempt_count": self.attempt_count + 1,
            }
        )
        if order:
            order._efn_loyalty_refresh_state()

    def _efn_retry_or_fail(self, code):
        self.ensure_one()
        max_hours = self._efn_max_hours()
        if self.create_date and self.create_date < fields.Datetime.now() - timedelta(hours=max_hours):
            self._efn_finish("failed", f"max_age:{code}")
            return
        attempts = self.attempt_count + 1
        delay = min(BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)), BACKOFF_MAX_SECONDS)
        _logger.warning(
            "EFN loyalty outbox %s (%s) gagal %s, percobaan %d; ulang dalam %ds",
            self.id,
            self.kind,
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
                "attempt_count": self.attempt_count + (1 if state == "failed" else 0),
            }
        )
        order = self.order_id.sudo()
        if state == "failed":
            _logger.warning("EFN loyalty outbox %s (%s) GAGAL final: %s", self.id, self.kind, code)
            if self.kind == "redeem_commit":
                self.reservation_id.sudo().write({"state": "review", "note": code})
            if order:
                order._efn_loyalty_flag_review(f"{self.kind}:{code}")
            self._efn_schedule_activity(code)
        elif order:
            order._efn_loyalty_refresh_state()

    def _efn_max_hours(self):
        raw = self.env["ir.config_parameter"].sudo().get_param("efn.pos.loyalty.outbox_max_hours", DEFAULT_MAX_HOURS)
        try:
            return max(int(raw), 1)
        except (TypeError, ValueError):
            return DEFAULT_MAX_HOURS

    def _efn_schedule_activity(self, reason):
        """Aktivitas To-Do sekali per record ke POS manager pertama di company."""
        self.ensure_one()
        if self.activity_flagged:
            return
        user = self._efn_review_user()
        if user:
            order_name = self.order_id.name or self.reservation_id.order_uuid or ""
            self.sudo().activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=_("Review Poin EFN"),
                note=_(
                    "Sinkron poin %(kind)s untuk order %(order)s perlu ditinjau (%(reason)s). "
                    "Cek menu Point of Sale → Orders → Review Poin.",
                    kind=self.kind,
                    order=order_name,
                    reason=reason,
                ),
            )
        self.sudo().write({"activity_flagged": True})

    def _efn_review_user(self):
        group = self.env.ref("point_of_sale.group_pos_manager", raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        Users = self.env["res.users"].sudo()
        group_field = "all_group_ids" if "all_group_ids" in Users._fields else "groups_id"
        return Users.search(
            [
                (group_field, "in", group.ids),
                ("company_ids", "in", self.company_id.ids),
                ("share", "=", False),
                ("id", "!=", self.env.ref("base.user_root").id),
            ],
            order="id",
            limit=1,
        )
