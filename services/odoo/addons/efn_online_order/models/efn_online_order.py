"""Pesanan online per bagian (satu ``platform_order_ref`` = satu SO) + 6 metode tulis/baca BFF + 2 metode staf.

Kontrak: docs/04_SERVICE_CONTRACTS.md §10.4.3 no. 5-10, delta §10.4.4, saga §10.6; docs/08 §8.3 B-D.

Aturan inti:
- **Idempotensi**: setiap metode tulis membawa ``idempotency_key`` (``online:place:<part_id>``, dst.). Place: kunci +
  ``payload_sha256`` sama -> hasil lama; payload beda -> ``idempotency_conflict``. Retry paralel: advisory lock
  transaksi + constraint unik; pelanggaran unik -> ``ConcurrencyError`` (Odoo mengulang request, lalu mendapat hasil
  lama).
- **Harga**: Odoo menghitung ulang saat place (``quote_hash`` dari pricelist + mesin pajak standar), dan saat bayar
  memastikan total SO tidak berubah oleh konfirmasi (mitigasi ``sale_loyalty`` temuan #37: beda -> ``review``).
- **Nominal**: ``amount_payable`` = total SO setelah ``account.cash.rounding`` kanal (Rupiah bulat).
  ``amount_paid`` != ``amount_payable`` -> ``review``.
- **Kode ambil**: hanya hash pbkdf2-sha512 (``pickup_code_hash``); teks asli tidak pernah disimpan/di-log; hash
  dihapus saat order final. 5x salah di wizard serah terima -> ``review``.
- **Tanpa PII**: tidak ada partner/HP/nama/alamat pelanggan; SO memakai partner generik kanal.
- Metode BFF memanggil ``efn.online.channel._efn_online_check_caller`` lebih dulu (grup + user kanal + company +
  context dibersihkan), lalu menulis lewat ``sudo``. Metode staf memeriksa grup + record rule + company.
"""

import json
import logging
import uuid
from datetime import timedelta

import psycopg2

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ConcurrencyError, UserError, ValidationError
from odoo.tools import float_compare

from .efn_online_channel import iso_utc, parse_iso_utc
from .efn_online_common import (
    CANCEL_REASONS,
    CANCEL_SCOPE,
    CONFIRM_SCOPE,
    DEFAULT_PICKUP_KDF_ROUNDS,
    FULFILMENTS,
    HANDOVER_SCOPE,
    IDEMPOTENCY_KEY_RE,
    MAX_STATUS_REFS,
    OPAQUE_REF_RE,
    OUTLET_CODE_RE,
    PICKUP_CODE_RE,
    REFUND_REASONS,
    UUID_RE,
    canonical_sha256,
    check_amount_int,
    check_str,
    internal_scope,
    lines_payload,
    online_error,
    pickup_crypt_context,
    require,
)

_logger = logging.getLogger(__name__)

STATES = [
    ("placed", "Menunggu pembayaran"),
    ("paid", "Dibayar (diproses)"),
    ("confirmed", "Dikonfirmasi"),
    ("stock_short", "Stok kurang"),
    ("ready", "Siap diambil"),
    ("picked_up", "Sudah diambil"),
    ("cancelled", "Dibatalkan"),
    ("review", "Perlu ditinjau"),
]
FINAL_STATES = ("picked_up", "cancelled")
PLACE_KEYS = frozenset(
    {
        "idempotency_key",
        "platform_order_ref",
        "checkout_ref",
        "outlet_code",
        "fulfilment",
        "lines",
        "quote_hash",
        "pickup_code",
    }
)
PAYMENT_KEYS = frozenset({"payment_ref", "amount_paid", "paid_at", "idempotency_key"})
REFUND_KEYS = frozenset({"idempotency_key", "lines", "full", "restock", "reason"})
SETTLE_KEYS = frozenset({"provider_refund_ref", "settled_at", "ok", "idempotency_key"})
CANCEL_TO_REFUND_REASON = {
    "stock_short": "stock_short",
    "customer": "customer_cs",
    "review": "review",
}
PICKUP_KDF_PARAM = "efn_online.pickup_code_kdf_rounds"
PICKUP_MAX_DAYS_PARAM = "efn_online.pickup_code_max_days"
DEFAULT_PICKUP_MAX_DAYS = 7
PAID_STATES = ("paid", "confirmed", "stock_short", "ready", "review")
OPS_CANCEL_MIN_REASON = 10


def _efn_is_customer_move(move):
    """Move yang mengeluarkan barang ke pelanggan (atau transit) — dipakai guard SEC-105 dan serah terima."""
    usages = ("customer", "transit")
    final = move.location_final_id if "location_final_id" in move._fields else move.location_dest_id
    return move.location_dest_id.usage in usages or final.usage in usages


class _ReviewNeeded(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class EfnOnlineOrder(models.Model):
    _name = "efn.online.order"
    _description = "EFN Online - Pesanan per kanal (satu SO)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "name"

    name = fields.Char(related="sale_order_id.name", store=True, string="Nomor SO")
    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    channel_id = fields.Many2one("efn.online.channel", required=True, readonly=True, index=True, check_company=True)
    outlet_id = fields.Many2one("efn.online.outlet", required=True, readonly=True, check_company=True)
    fulfilment = fields.Selection([(f, f) for f in FULFILMENTS], required=True, readonly=True, default="pickup")
    platform_order_ref = fields.Char(required=True, readonly=True, index=True, help="part_id BFF (UUID, opak).")
    checkout_ref = fields.Char(required=True, readonly=True, index=True, help="order_id BFF (UUID, opak).")
    idempotency_key = fields.Char(required=True, readonly=True)
    payload_sha256 = fields.Char(readonly=True)
    quote_hash = fields.Char(readonly=True)
    sale_order_id = fields.Many2one("sale.order", readonly=True, ondelete="restrict", index=True, check_company=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection(STATES, required=True, default="placed", readonly=True, index=True, tracking=True)
    amount_total = fields.Monetary(readonly=True)
    amount_payable = fields.Monetary(readonly=True)
    expires_at = fields.Datetime(readonly=True, index=True)
    payment_ref = fields.Char(readonly=True, groups="base.group_system", copy=False)
    paid_idempotency_key = fields.Char(readonly=True, groups="base.group_system", copy=False)
    paid_amount = fields.Monetary(readonly=True)
    paid_at = fields.Datetime(readonly=True)
    invoice_id = fields.Many2one("account.move", readonly=True, check_company=True, copy=False)
    pickup_code_hash = fields.Char(readonly=True, groups="base.group_system", copy=False)
    pickup_attempts = fields.Integer(readonly=True, copy=False)
    ready_at = fields.Datetime(readonly=True)
    picked_up_at = fields.Datetime(readonly=True)
    cancelled_at = fields.Datetime(readonly=True)
    cancel_reason = fields.Selection([(r, r) for r in CANCEL_REASONS], readonly=True)
    cancel_idempotency_key = fields.Char(readonly=True, groups="base.group_system", copy=False)
    review_reason = fields.Char(readonly=True, help="Kode saja (tanpa data pribadi).")
    short_lines = fields.Text(readonly=True, help="JSON [{product_id, requested, available}] saat stok kurang.")
    event_seq = fields.Integer(readonly=True, copy=False)
    reminder_flagged = fields.Boolean(readonly=True, copy=False)
    refund_ids = fields.One2many("efn.online.refund", "order_id", readonly=True)
    event_ids = fields.One2many("efn.online.event.outbox", "order_id", readonly=True)

    _idempotency_key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi place harus unik.")
    _platform_order_ref_unique = models.Constraint("unique (platform_order_ref)", "platform_order_ref harus unik.")
    _sale_order_unique = models.Constraint("unique (sale_order_id)", "Satu SO hanya untuk satu pesanan online.")

    # ======================================================================== helpers
    @api.model
    def _efn_channel_env(self, channel_code):
        return self.env["efn.online.channel"]._efn_online_check_caller(channel_code)

    def _efn_lock(self):
        """Kunci baris (FOR UPDATE) agar langkah saga per order berurutan."""
        self.ensure_one()
        self.flush_recordset()
        self.env.cr.execute("SELECT id FROM efn_online_order WHERE id = %s FOR UPDATE", (self.id,))
        self.invalidate_recordset()
        return self

    @api.model
    def _efn_find(self, channel, platform_order_ref, lock=True):
        check_str(platform_order_ref, UUID_RE, 36)
        order = self.search(
            [("platform_order_ref", "=", platform_order_ref.lower()), ("channel_id", "=", channel.id)], limit=1
        )
        if order and lock:
            order._efn_lock()
        return order

    def _efn_kdf_rounds(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(PICKUP_KDF_PARAM, DEFAULT_PICKUP_KDF_ROUNDS)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return DEFAULT_PICKUP_KDF_ROUNDS

    def _efn_amount_out(self, amount):
        """Nominal ke BFF: int bila bulat (Rupiah), selain itu float apa adanya (BFF menolak + alert)."""
        value = self.currency_id.round(amount)
        return int(round(value)) if float(value).is_integer() else value

    def _efn_emit(self, event, refund=None):
        """Antrikan event outbox (seq naik monoton per order). Payload dibangun saat kirim (tanpa PII)."""
        self.ensure_one()
        seq = self.event_seq + 1
        self.write({"event_seq": seq})
        return self.env["efn.online.event.outbox"].sudo()._efn_enqueue(self, event, seq, refund=refund)

    def _efn_clear_secret(self):
        """Hapus hash kode ambil saat final (delta §10.4.4 no. 1)."""
        self.sudo().write({"pickup_code_hash": False})

    def _efn_set_review(self, code):
        self.ensure_one()
        self.write({"state": "review", "review_reason": code})
        # SEC-99: bagian review tidak bisa diserahkan lagi di Odoo (penyelesaian = batal lewat BFF) -> hash dihapus.
        self._efn_clear_secret()
        _logger.warning("EFN online order %s -> review (%s)", self.id, code)
        self._efn_schedule_activity(_("Pesanan online perlu ditinjau"), code, "sales_team.group_sale_manager")
        self._efn_emit("review")

    def _efn_schedule_activity(self, summary, code, group_xmlid):
        user = self._efn_company_user(group_xmlid)
        if user:
            self.sudo().activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=summary,
                note=_("Pesanan online %(name)s: %(code)s", name=self.name or self.platform_order_ref, code=code),
            )

    def _efn_company_user(self, group_xmlid):
        group = self.env.ref(group_xmlid, raise_if_not_found=False)
        if not group:
            return self.env["res.users"]
        return (
            self.env["res.users"]
            .sudo()
            .search(
                [
                    ("all_group_ids", "in", group.ids),
                    ("company_ids", "in", self.company_id.ids),
                    ("share", "=", False),
                    ("id", "!=", self.env.ref("base.user_root").id),
                ],
                order="id",
                limit=1,
            )
        )

    def _efn_place_result(self):
        self.ensure_one()
        return {
            "order_state": self.state,
            "sale_order_name": self.sale_order_id.name,
            "amount_total": self.currency_id.round(self.amount_total),
            "amount_payable": self._efn_amount_out(self.amount_payable),
            "expires_at": iso_utc(self.expires_at),
        }

    def _efn_posted_invoice(self):
        self.ensure_one()
        invoices = self.sale_order_id.invoice_ids.filtered(
            lambda m: m.move_type == "out_invoice" and m.state == "posted"
        )
        return invoices[:1]

    def _efn_refunded_total(self):
        return sum(r.amount for r in self.refund_ids if r.state != "failed")

    # ==================================================================== BFF: place
    @api.model
    def efn_online_place(self, channel_code, order):
        """Buat SO draft idempoten (docs/04 §10.4.3 no. 5)."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(order, dict) and set(order) <= PLACE_KEYS and set(order) >= PLACE_KEYS)
        key = check_str(order["idempotency_key"], IDEMPOTENCY_KEY_RE)
        ref = check_str(order["platform_order_ref"], UUID_RE, 36).lower()
        checkout_ref = check_str(order["checkout_ref"], UUID_RE, 36).lower()
        outlet_code = check_str(order["outlet_code"], OUTLET_CODE_RE, 24)
        fulfilment = order["fulfilment"]
        require(fulfilment in FULFILMENTS)
        quote_hash = check_str(order["quote_hash"], max_len=64)
        require(len(quote_hash) == 64 and all(c in "0123456789abcdef" for c in quote_hash))
        pickup_code = order["pickup_code"]
        require(isinstance(pickup_code, str) and bool(PICKUP_CODE_RE.match(pickup_code)))
        normalized_lines = lines_payload(order["lines"], channel.max_lines)
        # Kode ambil TIDAK ikut hash payload (sha256 cepat atas 39,6 bit dapat di-brute force).
        payload_sha = canonical_sha256(
            {
                "channel": channel.code,
                "platform_order_ref": ref,
                "checkout_ref": checkout_ref,
                "outlet_code": outlet_code,
                "fulfilment": fulfilment,
                "lines": normalized_lines,
                "quote_hash": quote_hash,
            }
        )
        Orders = self.with_env(channel.env)
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"efn_online_place:{key}",))
        existing = Orders.search([("idempotency_key", "=", key)], limit=1)
        if existing:
            if existing.channel_id != channel or existing.payload_sha256 != payload_sha:
                raise online_error("idempotency_conflict")
            return existing._efn_place_result()
        if Orders.search_count([("platform_order_ref", "=", ref)], limit=1):
            raise online_error("idempotency_conflict")

        outlet, product_qty, issues, summary = channel._efn_online_evaluate(outlet_code, fulfilment, order["lines"])
        codes = {issue["code"] for issue in issues}
        if "not_found" in codes:
            raise online_error("not_found")
        if "out_of_stock" in codes:
            raise online_error("out_of_stock")
        if codes or not product_qty:
            raise online_error("invalid_input")
        if summary["quote_hash"] != quote_hash:
            raise online_error("price_changed")

        now = fields.Datetime.now()
        try:
            with self.env.cr.savepoint():
                record = Orders.create(
                    {
                        "company_id": channel.company_id.id,
                        "channel_id": channel.id,
                        "outlet_id": outlet.id,
                        "fulfilment": fulfilment,
                        "platform_order_ref": ref,
                        "checkout_ref": checkout_ref,
                        "idempotency_key": key,
                        "payload_sha256": payload_sha,
                        "quote_hash": quote_hash,
                        "expires_at": now + timedelta(minutes=channel.payment_timeout_minutes),
                        "pickup_code_hash": pickup_crypt_context(self._efn_kdf_rounds()).hash(pickup_code),
                    }
                )
                record.flush_recordset()
        except psycopg2.errors.UniqueViolation as exc:
            # Place paralel dengan kunci yang sama sudah commit: ulangi request -> cabang `existing`.
            raise ConcurrencyError("EFN online: place paralel, diulang") from exc

        vals = channel._efn_online_so_vals(outlet, product_qty)
        vals.update(
            {
                "client_order_ref": ref,
                "origin": f"Super-App {checkout_ref}",
                "efn_online_order_id": record.id,
            }
        )
        sale_order = channel.env["sale.order"].with_context(mail_create_nosubscribe=True).create(vals)
        stored = channel._efn_online_summarize(sale_order, outlet, fulfilment)
        if stored["quote_hash"] != quote_hash:
            # Hitungan SO tersimpan beda dengan quote (mis. aturan harga berubah di tengah): batalkan transaksi.
            raise online_error("price_changed")
        record.write(
            {
                "sale_order_id": sale_order.id,
                "amount_total": stored["amount_total"],
                "amount_payable": stored["amount_payable"],
            }
        )
        record._efn_emit("placed")
        _logger.info("EFN online place order=%s part=%s so=%s", record.id, ref, sale_order.id)
        return record._efn_place_result()

    # ================================================================ BFF: mark paid
    @api.model
    def efn_online_mark_paid(self, channel_code, platform_order_ref, payment):
        """Catat pembayaran -> cek nominal & stok -> konfirmasi -> faktur + pembayaran titipan (§10.4.3 no. 6)."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(payment, dict) and set(payment) == PAYMENT_KEYS)
        payment_ref = check_str(payment["payment_ref"], OPAQUE_REF_RE)
        key = check_str(payment["idempotency_key"], IDEMPOTENCY_KEY_RE)
        amount_paid = check_amount_int(payment["amount_paid"])
        paid_at = parse_iso_utc(payment["paid_at"])
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            raise online_error("not_found")
        if order.paid_idempotency_key:
            if order.paid_idempotency_key == key and order.payment_ref == payment_ref:
                return order._efn_paid_result()
            if order.payment_ref != payment_ref and order.state not in FINAL_STATES + ("review",):
                # Pembayaran kedua dengan intent lain: jangan proses, tandai review (docs/08 §8.3 D.3 a).
                order._efn_set_review("duplicate_payment")
                return order._efn_paid_result()
            raise online_error("idempotency_conflict")
        if order.state == "cancelled":
            # Bayar terlambat: SO sudah dibatalkan (cron/pelanggan). BFF mengembalikan dana penuh porsi ini.
            order.write(
                {
                    "payment_ref": payment_ref,
                    "paid_idempotency_key": key,
                    "paid_amount": amount_paid,
                    "paid_at": paid_at,
                }
            )
            return {"order_state": "cancelled", "refund_required": "full"}
        if order.state != "placed":
            raise online_error("invalid_state")
        order.write(
            {
                "state": "paid",
                "payment_ref": payment_ref,
                "paid_idempotency_key": key,
                "paid_amount": amount_paid,
                "paid_at": paid_at,
            }
        )
        order._efn_emit("paid")
        if float_compare(amount_paid, order.amount_payable, precision_rounding=order.currency_id.rounding) != 0:
            order._efn_set_review("amount_mismatch")
            return order._efn_paid_result()
        short = order._efn_short_lines()
        if short:
            order.write({"state": "stock_short", "short_lines": json.dumps(short)})
            order._efn_emit("stock_short")
            return order._efn_paid_result()
        try:
            order._efn_confirm_sale_order()
        except _ReviewNeeded as review:
            order._efn_set_review(review.code)
            return order._efn_paid_result()
        if channel.invoice_on_paid:
            try:
                order._efn_invoice_and_register_payment()
            except _ReviewNeeded as review:
                order._efn_set_review(review.code)
                return order._efn_paid_result()
        order.write({"state": "confirmed"})
        order._efn_emit("confirmed")
        return order._efn_paid_result()

    def _efn_paid_result(self):
        self.ensure_one()
        result = {"order_state": self.state}
        if self.state == "stock_short" and self.short_lines:
            result["short_lines"] = json.loads(self.short_lines)
        if self.state == "cancelled" and self.paid_idempotency_key:
            result["refund_required"] = "full"
        return result

    def _efn_short_lines(self):
        self.ensure_one()
        channel = self.channel_id
        if channel.stock_check != "free_qty":
            return []
        lines = self.sale_order_id.order_line.filtered(lambda line: not line.display_type and line.product_id)
        free = channel._efn_online_free_qty(self.outlet_id, lines.product_id)
        short = []
        for line in lines:
            available = free.get(line.product_id.id)
            if available is not None and available < line.product_uom_qty:
                short.append(
                    {
                        "product_id": line.product_id.id,
                        "requested": line.product_uom_qty,
                        "available": max(available, 0),
                    }
                )
        return short

    def _efn_confirm_sale_order(self):
        """Konfirmasi SO dalam savepoint; total berubah (sale_loyalty dsb., temuan #37) -> rollback + review."""
        self.ensure_one()
        sale_order = self.sale_order_id
        currency = self.currency_id
        before = sale_order.amount_total
        try:
            with self.env.cr.savepoint():
                with internal_scope(self.env, CONFIRM_SCOPE, sale_order.ids):
                    sale_order.with_context(send_email=False).action_confirm()
                sale_order.flush_recordset()
                sale_order.invalidate_recordset(["amount_total"])
                if sale_order.state != "sale" or currency.compare_amounts(sale_order.amount_total, before) != 0:
                    raise _ReviewNeeded("total_changed_on_confirm")
        except _ReviewNeeded:
            self.env.invalidate_all()
            raise
        except (UserError, ValidationError) as exc:
            self.env.invalidate_all()
            _logger.warning("EFN online order %s: konfirmasi SO gagal (%s)", self.id, type(exc).__name__)
            raise _ReviewNeeded("confirm_failed") from None

    def _efn_invoice_and_register_payment(self):
        """Faktur + pembayaran pada jurnal titipan (ADR-019 A1). Gagal -> savepoint di-rollback + review."""
        self.ensure_one()
        channel = self.channel_id
        try:
            with self.env.cr.savepoint():
                invoices = self.sale_order_id.with_context(raise_if_nothing_to_invoice=False)._create_invoices()
                if len(invoices) != 1:
                    raise _ReviewNeeded("nothing_to_invoice" if not invoices else "invoice_split")
                if channel.cash_rounding_id:
                    invoices.invoice_cash_rounding_id = channel.cash_rounding_id
                invoices.action_post()
                if self.currency_id.compare_amounts(invoices.amount_total, self.amount_payable) != 0:
                    raise _ReviewNeeded("invoice_amount_mismatch")
                self._efn_register_payment(invoices, self.paid_amount)
                self.write({"invoice_id": invoices.id})
        except _ReviewNeeded:
            self.env.invalidate_all()
            raise
        except (UserError, ValidationError) as exc:
            self.env.invalidate_all()
            _logger.warning("EFN online order %s: faktur/pembayaran gagal (%s)", self.id, type(exc).__name__)
            raise _ReviewNeeded("invoice_failed") from None

    def _efn_register_payment(self, moves, amount):
        """Satu-satunya titik pencatatan pembayaran (ADR-019: A2/opsi C cukup mengubah metode ini)."""
        self.ensure_one()
        journal = self.channel_id.journal_titipan_id
        if not journal:
            raise _ReviewNeeded("journal_missing")
        wizard = (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=moves.ids)
            .create(
                {
                    "journal_id": journal.id,
                    "amount": amount,
                    "payment_date": fields.Date.context_today(self),
                    "communication": f"Super-App {self.checkout_ref}",
                }
            )
        )
        return wizard._create_payments()

    # =================================================================== BFF: cancel
    @api.model
    def efn_online_cancel(self, channel_code, platform_order_ref, reason, idempotency_key):
        """Batalkan bagian (§10.4.3 no. 7). Ref tidak dikenal -> ``{"order_state": "absent"}`` (delta no. 3)."""
        channel = self._efn_channel_env(channel_code)
        require(reason in CANCEL_REASONS)
        key = check_str(idempotency_key, IDEMPOTENCY_KEY_RE)
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            return {"order_state": "absent"}
        if order.state == "cancelled":
            return order._efn_cancel_result()
        if order.state == "picked_up":
            raise online_error("invalid_state")
        refund = order._efn_do_cancel(reason, initiator="bff", idempotency_key=key)
        return order._efn_cancel_result(refund)

    def _efn_cancel_result(self, refund=None):
        self.ensure_one()
        refund = refund or self.refund_ids.filtered(lambda r: r.origin == "cancel")[:1]
        result = {"order_state": self.state}
        if refund:
            result["refund"] = {"refund_ref": refund.refund_ref, "amount": self._efn_amount_out(refund.amount)}
        return result

    def _efn_do_cancel(self, reason, initiator, idempotency_key=None, cancel_sale_order=True):
        """Batalkan SO (+ nota kredit penuh bila faktur sudah di-post). Kembalikan refund (atau kosong)."""
        self.ensure_one()
        refund = self.env["efn.online.refund"]
        invoice = self._efn_posted_invoice()
        if invoice:
            refund = (
                self.env["efn.online.refund"]
                .sudo()
                ._efn_create(
                    self,
                    idempotency_key=f"online:cancel_refund:{self.platform_order_ref}",
                    lines=None,
                    restock=False,
                    reason=CANCEL_TO_REFUND_REASON.get(reason, "ops_cancelled"),
                    initiator=initiator,
                    origin="cancel",
                )
            )
        sale_order = self.sale_order_id
        if cancel_sale_order and sale_order and sale_order.state != "cancel":
            if sale_order.locked:
                sale_order.sudo().action_unlock()
            with internal_scope(self.env, CANCEL_SCOPE, sale_order.ids):
                sale_order._action_cancel()
        self.write(
            {
                "state": "cancelled",
                "cancel_reason": reason,
                "cancelled_at": fields.Datetime.now(),
                "cancel_idempotency_key": idempotency_key or False,
            }
        )
        self._efn_clear_secret()
        self._efn_emit("cancelled", refund=refund or None)
        _logger.info("EFN online order %s cancelled (%s, %s)", self.id, reason, initiator)
        return refund

    # =================================================================== BFF: refund
    @api.model
    def efn_online_refund(self, channel_code, platform_order_ref, refund):
        """Refund parsial/penuh per SO (§10.4.3 no. 8, R-O05). Nominal = nota kredit Odoo, kumulatif <= dibayar."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(refund, dict) and set(refund) <= REFUND_KEYS)
        key = check_str(refund.get("idempotency_key"), IDEMPOTENCY_KEY_RE)
        require(refund.get("reason") in REFUND_REASONS)
        restock = refund.get("restock", False)
        require(isinstance(restock, bool))
        full = refund.get("full", False)
        require(isinstance(full, bool))
        lines = None
        if full:
            require("lines" not in refund)
        else:
            lines = lines_payload(refund.get("lines"), 200)
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            raise online_error("not_found")
        payload_sha = canonical_sha256(
            {"order": order.id, "full": full, "lines": lines, "restock": restock, "reason": refund["reason"]}
        )
        # QA G17: pakai env kanal (context klien sudah dibuang, §10.4.4 no. 4) — `self.env` masih membawa `lang` klien;
        # bahasa yang tidak terpasang (id_ID di DB tanpa bahasa Indonesia) -> env.lang -> UserError 422.
        Refunds = order.env["efn.online.refund"].sudo()
        existing = Refunds.search([("idempotency_key", "=", key)], limit=1)
        if existing:
            if existing.order_id != order or existing.payload_sha256 != payload_sha:
                raise online_error("idempotency_conflict")
            return existing._efn_result()
        if order.state not in ("confirmed", "ready", "picked_up", "review") or not order._efn_posted_invoice():
            raise online_error("invalid_state")
        record = Refunds._efn_create(
            order,
            idempotency_key=key,
            lines=lines,
            restock=restock,
            reason=refund["reason"],
            initiator="bff",
            origin="refund",
            payload_sha256=payload_sha,
        )
        order._efn_emit("refunded", refund=record)
        return record._efn_result()

    @api.model
    def efn_online_refund_settled(self, channel_code, refund_ref, settlement):
        """Dana refund sudah keluar dari gateway -> pembayaran keluar via jurnal titipan (§10.4.3 no. 9)."""
        channel = self._efn_channel_env(channel_code)
        check_str(refund_ref, UUID_RE, 36)
        require(isinstance(settlement, dict) and set(settlement) == SETTLE_KEYS)
        key = check_str(settlement["idempotency_key"], IDEMPOTENCY_KEY_RE)
        provider_ref = check_str(settlement["provider_refund_ref"], OPAQUE_REF_RE)
        settled_at = parse_iso_utc(settlement["settled_at"])
        ok = settlement["ok"]
        require(isinstance(ok, bool))
        record = (
            channel.env["efn.online.refund"]  # env kanal: tanpa context klien (QA G17, lihat efn_online_refund)
            .sudo()
            .search([("refund_ref", "=", refund_ref.lower()), ("order_id.channel_id", "=", channel.id)], limit=1)
        )
        if not record:
            raise online_error("not_found")
        record.order_id._efn_lock()
        return record._efn_settle(key, provider_ref, settled_at, ok)

    # =================================================================== BFF: status
    @api.model
    def efn_online_status(self, channel_code, platform_order_refs):
        """Rekonsiliasi pull (§10.4.3 no. 10). Ref tidak dikenal -> ``state: "absent"``."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(platform_order_refs, list) and 0 < len(platform_order_refs) <= MAX_STATUS_REFS)
        refs = []
        for ref in platform_order_refs:
            refs.append(check_str(ref, UUID_RE, 36).lower())
        orders = self.with_env(channel.env).search(
            [("platform_order_ref", "in", refs), ("channel_id", "=", channel.id)]
        )
        by_ref = {o.platform_order_ref: o for o in orders}
        result = []
        for ref in refs:
            order = by_ref.get(ref)
            if not order:
                result.append({"platform_order_ref": ref, "state": "absent"})
                continue
            result.append(
                {
                    "platform_order_ref": ref,
                    "state": order.state,
                    "seq": order.event_seq,
                    "ready_at": iso_utc(order.ready_at),
                    "picked_up_at": iso_utc(order.picked_up_at),
                    "refunds": [r._efn_result(with_state=True) for r in order.refund_ids.sorted("id")],
                }
            )
        return result

    # ===================================================================== staf (UI)
    def _efn_check_staff(self, group_xmlid):
        if not self.env.user.has_group(group_xmlid):
            raise AccessError(_("Anda tidak berhak melakukan aksi ini pada pesanan online."))
        self.check_access("read")  # ACL + record rule multi-company
        for order in self:
            if order.company_id not in self.env.user.company_ids:
                raise AccessError(_("Pesanan online milik company lain."))

    def action_efn_ready(self):
        """Staf outlet: pesanan siap diambil -> event ``ready`` (BFF memberi notifikasi pelanggan)."""
        self._efn_check_staff("stock.group_stock_user")
        for order in self.sudo():
            order._efn_lock()
            if order.state != "confirmed":
                raise UserError(_("Hanya pesanan berstatus 'Dikonfirmasi' yang dapat ditandai siap."))
            order.write({"state": "ready", "ready_at": fields.Datetime.now()})
            order._efn_emit("ready")
        return True

    def action_efn_retry_events(self):
        """Manajer penjualan: kirim ulang event outbox yang gagal untuk pesanan ini."""
        self._efn_check_staff("sales_team.group_sale_manager")
        outbox = self.sudo().event_ids.filtered(lambda e: e.state == "failed")
        outbox.write({"state": "pending", "next_attempt_at": fields.Datetime.now(), "last_error": False})
        self.env["efn.online.event.outbox"]._efn_trigger()
        return True

    def _efn_handover(self, code):
        """Dipanggil wizard serah terima (sudo, setelah cek staf). Kembalikan (ok, pesan)."""
        self.ensure_one()
        self._efn_lock()
        if self.state not in ("confirmed", "ready"):
            return False, _("Pesanan tidak dalam status siap diserahkan.")
        if not self.pickup_code_hash:
            # SEC-99: kode kedaluwarsa (retensi) -> serah terima lewat verifikasi manual manajer (di luar Odoo).
            return False, _("Kode ambil pesanan ini sudah kedaluwarsa. Hubungi manajer untuk verifikasi manual.")
        max_attempts = max(self.channel_id.pickup_max_attempts, 1)
        valid = False
        if self.pickup_code_hash and PICKUP_CODE_RE.match(code or ""):
            try:
                valid = pickup_crypt_context(self._efn_kdf_rounds()).verify(code, self.pickup_code_hash)
            except (ValueError, TypeError):
                valid = False
        if not valid:
            attempts = self.pickup_attempts + 1
            self.write({"pickup_attempts": attempts})
            _logger.warning("EFN online order %s: kode ambil salah (percobaan %d)", self.id, attempts)
            if attempts >= max_attempts:
                self._efn_set_review("pickup_code_attempts")
                return False, _("Kode salah %(n)d kali. Pesanan dikunci untuk ditinjau.", n=attempts)
            return False, _("Kode ambil salah. Sisa percobaan: %(n)d.", n=max_attempts - attempts)
        # SEC-104: serah terima WAJIB mengeluarkan barang lewat move SO ke lokasi pelanggan di sini. SO batal /
        # tanpa move pelanggan yang masih terbuka (mis. picking dibatalkan/dikeluarkan lewat jalur lain) -> review.
        sale_order = self.sale_order_id
        customer_moves = sale_order.order_line.move_ids.filtered(
            lambda m: m.state != "cancel" and _efn_is_customer_move(m)
        )
        if sale_order.state != "sale" or not customer_moves or any(m.state == "done" for m in customer_moves):
            self._efn_set_review("handover_no_picking")
            return False, _("Pengiriman pesanan ini tidak valid untuk diserahkan. Pesanan dikunci untuk ditinjau.")
        with internal_scope(self.env, HANDOVER_SCOPE, sale_order.ids):
            for picking in customer_moves.picking_id:
                picking.action_assign()
                for move in picking.move_ids.filtered(lambda m: m.state not in ("done", "cancel")):
                    if move.product_uom.compare(move.quantity, move.product_uom_qty) < 0:  # pickup: barang di outlet
                        move.quantity = move.product_uom_qty
                result = picking.with_context(
                    skip_backorder=True, picking_ids_not_to_backorder=picking.ids
                ).button_validate()
                if result is not True and picking.state != "done":
                    raise UserError(_("Pengiriman %(name)s tidak dapat divalidasi otomatis.", name=picking.name))
        if any(m.state != "done" for m in customer_moves):
            raise UserError(_("Sebagian barang pesanan ini belum dapat dikeluarkan."))
        self.write({"state": "picked_up", "picked_up_at": fields.Datetime.now(), "pickup_attempts": 0})
        self._efn_clear_secret()
        self._efn_emit("picked_up")
        _logger.info("EFN online order %s picked_up", self.id)
        return True, _("Pesanan %(name)s diserahkan.", name=self.name)

    # ======================================================================== cron
    @api.model
    def _cron_efn_online_expire(self, limit=200):
        """Draf lewat ``expires_at`` (batas bayar Odoo) -> batal + event ``cancelled`` (docs/08 §8.3 D.7)."""
        orders = self.sudo().search(
            [("state", "=", "placed"), ("expires_at", "<", fields.Datetime.now())], order="id", limit=limit
        )
        for order in orders:
            order._efn_lock()
            if order.state != "placed":
                continue
            order._efn_do_cancel("payment_expired", initiator="odoo")
            if self.env.context.get("ir_cron_progress_id"):
                self.env["ir.cron"]._commit_progress(1)
        return orders.ids

    @api.model
    def _cron_efn_online_reminders(self):
        """Pesanan siap tapi tidak diambil > N jam -> aktivitas (Q-ON13), TANPA batal otomatis."""
        now = fields.Datetime.now()
        orders = self.sudo().search([("state", "in", ("confirmed", "ready")), ("reminder_flagged", "=", False)])
        for order in orders:
            since = order.ready_at or order.paid_at
            if since and since + timedelta(hours=max(order.channel_id.pickup_reminder_hours, 1)) < now:
                order._efn_schedule_activity(
                    _("Pesanan online belum diambil"), "not_picked_up", "stock.group_stock_user"
                )
                order.write({"reminder_flagged": True})
        self._efn_expire_pickup_codes(now)

    @api.model
    def _efn_expire_pickup_codes(self, now=None):
        """SEC-99: hash kode ambil bagian yang tidak diambil dihapus setelah N hari (default 7) sejak siap (atau
        dibayar bila tidak pernah ditandai siap) + aktivitas staf. Parameter ``efn_online.pickup_code_max_days``."""
        now = now or fields.Datetime.now()
        raw = self.env["ir.config_parameter"].sudo().get_param(PICKUP_MAX_DAYS_PARAM, DEFAULT_PICKUP_MAX_DAYS)
        try:
            days = max(int(raw), 1)
        except (TypeError, ValueError):
            days = DEFAULT_PICKUP_MAX_DAYS
        limit = now - timedelta(days=days)
        orders = self.sudo().search([("state", "in", ("confirmed", "ready")), ("pickup_code_hash", "!=", False)])
        expired = orders.filtered(lambda o: (o.ready_at or o.paid_at or o.create_date) < limit)
        for order in expired:
            order._efn_clear_secret()
            order._efn_schedule_activity(
                _("Kode ambil pesanan online kedaluwarsa"), "pickup_code_expired", "stock.group_stock_user"
            )
        return expired.ids

    # ============================================================ hook pembatalan SO
    def _efn_ops_cancel_check(self, reason_note=None):
        """SEC-95: pembatalan dari UI Odoo. Bagian yang sudah dibayar hanya oleh Sales manager + alasan wajib;
        barang yang sudah keluar (picking done) tidak boleh dibatalkan (gunakan refund/retur)."""
        for order in self:
            pickings = order.sudo().sale_order_id.picking_ids
            if order.state == "picked_up" or any(
                p.state == "done" and p.picking_type_code == "outgoing" for p in pickings
            ):
                raise UserError(_("Pesanan online yang sudah diambil tidak dapat dibatalkan; gunakan refund (CS)."))
            if order._efn_is_paid():
                if not self.env.user.has_group("sales_team.group_sale_manager"):
                    raise AccessError(_("Pesanan online yang sudah dibayar hanya dapat dibatalkan oleh Sales Manager."))
                if len((reason_note or "").strip()) < OPS_CANCEL_MIN_REASON:
                    raise UserError(
                        _(
                            "Isi 'Alasan batal (online)' minimal %(n)d karakter sebelum membatalkan pesanan online "
                            "yang sudah dibayar.",
                            n=OPS_CANCEL_MIN_REASON,
                        )
                    )

    def _efn_is_paid(self):
        self.ensure_one()
        return bool(self.sudo().paid_idempotency_key) or self.state in PAID_STATES

    def _efn_ops_cancelled(self, reason_note=None, actor=None):
        """SO dibatalkan staf dari UI Odoo -> order ``cancelled`` (ops) + nota kredit bila sudah difaktur.
        Teks alasan hanya di chatter Odoo; ke BFF hanya kode ``ops`` (L-16: refund ``initiator=odoo`` tidak
        otomatis di BFF, butuh persetujuan CS)."""
        actor = actor or self.env.user
        for order in self:
            order._efn_lock()
            if order.state == "cancelled":
                continue
            paid = order._efn_is_paid()
            order._efn_do_cancel("ops", initiator="odoo", cancel_sale_order=False)
            if paid:
                order.message_post(
                    body=_(
                        "Dibatalkan dari Odoo oleh %(user)s. Alasan: %(reason)s",
                        user=actor.name,
                        reason=(reason_note or "").strip(),
                    ),
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )
                _logger.warning("EFN online order %s (berbayar) dibatalkan ops oleh uid=%s", order.id, actor.id)

    @api.model
    def _efn_new_refund_ref(self):
        return str(uuid.uuid4())
