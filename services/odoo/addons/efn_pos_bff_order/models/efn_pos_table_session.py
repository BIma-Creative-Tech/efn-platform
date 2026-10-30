"""Sesi meja QR (E-27, docs/04 §14.A.2 + §14.7 L-70; docs/08 §13.1b no. 2).

Odoo menyimpan **hanya** ``bff_session_id`` (UUID sesi yang diterbitkan BFF), bukan token/URL QR. Satu sesi ``open``
per (config, meja) — indeks unik parsial. Sesi dapat memiliki banyak order (order pelayan + satu order per kiriman
QR, L-70). Siklus:

- buka (pelayan, ``pos.config.efn_qr_session_open``) -> ``open``;
- order sesi dibayar/dibatalkan dan meja tidak lagi punya order draft -> ``closed`` (``paid`` bila ada order sesi
  yang dibayar, selain itu ``cancelled``) + event ``session_closed`` (dengan ``eligible_amount`` NETO refund dan
  ``pos_member_attached`` per order bila ``paid``, K2 r15);
- cetak ulang -> hanya order ``draft`` pindah ke sesi baru (SEC-174); sesi lama ``closed reissued`` (BFF mencabut
  sendiri, tanpa event) atau, bila sesi lama punya order dibayar, ``closed paid`` + event ``session_closed paid``
  berisi order dibayar miliknya (earn ke pengklaim lama);
- staf menutup / sesi POS ditutup -> ``closed_by_staff`` + event;
- cron 5 menit -> ``expired`` setelah ``expires_at`` (BFF juga menolak token kedaluwarsa); order yang SUDAH tertaut
  (pelayan/carried/kiriman QR) dan dibayar setelahnya tetap mengirim ``session_closed paid``; order baru di meja tidak
  pernah dilekatkan ke sesi expired (SEC-166).
Tanpa data pelanggan.
"""

import json
import logging
import math

from odoo import api, fields, models

from odoo.addons.efn_core.scope import touch_row

from .qr_common import epoch_seconds

_logger = logging.getLogger(__name__)
_alert_logger = logging.getLogger("odoo.addons.efn_pos_bff_order.alert")

STATES = [("open", "Terbuka"), ("closed", "Ditutup"), ("expired", "Kedaluwarsa")]
CLOSE_REASONS = [
    ("paid", "Dibayar"),
    ("cancelled", "Dibatalkan"),
    ("closed_by_staff", "Ditutup staf"),
    ("reissued", "Dicetak ulang"),
]
PAID_STATES = ("paid", "done", "invoiced")
RETENTION_DAYS = 400  # L-76


class EfnPosTableSession(models.Model):
    _name = "efn.pos.table.session"
    _description = "EFN POS - Sesi meja QR ponsel"
    _order = "opened_at desc, id desc"
    _check_company_auto = True
    _rec_name = "bff_session_id"

    company_id = fields.Many2one("res.company", required=True, index=True, readonly=True)
    config_id = fields.Many2one("pos.config", required=True, index=True, readonly=True, check_company=True)
    table_id = fields.Many2one("restaurant.table", required=True, index=True, readonly=True)
    pos_session_id = fields.Many2one("pos.session", readonly=True, check_company=True)
    bff_session_id = fields.Char(
        string="ID sesi BFF", required=True, readonly=True, copy=False, help="UUID sesi dari BFF (bukan token)."
    )
    order_uuid = fields.Char(
        string="UUID order meja", required=True, readonly=True, help="Order meja saat QR dicetak (kunci earn BFF)."
    )
    print_no = fields.Integer(string="Cetakan ke", readonly=True, default=1)
    state = fields.Selection(STATES, required=True, default="open", index=True, readonly=True)
    close_reason = fields.Selection(CLOSE_REASONS, readonly=True)
    opened_by_id = fields.Many2one("res.users", string="Dibuka oleh", readonly=True)
    opened_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    expires_at = fields.Datetime(required=True, readonly=True, index=True)
    closed_at = fields.Datetime(readonly=True)
    event_seq = fields.Integer(readonly=True, copy=False, help="seq event terakhir ke BFF (naik monoton, G18-QA-01).")
    order_ids = fields.One2many("pos.order", "efn_qr_table_session_id", string="Order", readonly=True)
    submission_ids = fields.One2many("efn.pos.qr.order", "table_session_id", string="Kiriman QR", readonly=True)
    submission_count = fields.Integer(compute="_compute_totals")
    amount_total = fields.Monetary(compute="_compute_totals", currency_field="currency_id")
    currency_id = fields.Many2one(related="company_id.currency_id")

    _bff_session_unique = models.Constraint("unique (bff_session_id)", "ID sesi BFF harus unik.")
    # Satu sesi terbuka per meja per POS (pola UniqueIndex efn_role).
    _open_per_table = models.UniqueIndex(
        "(config_id, table_id) WHERE state = 'open'",
        "Satu sesi QR terbuka per meja.",
    )

    @api.depends("submission_ids", "order_ids.amount_total", "order_ids.state")
    def _compute_totals(self):
        for session in self:
            session.submission_count = len(session.submission_ids)
            session.amount_total = sum(
                session.order_ids.filtered(lambda o: o.state != "cancel" and o.efn_qr_order_ref).mapped("amount_total")
            )

    # ------------------------------------------------------------------ helper
    @api.model
    def _efn_parse_bff_datetime(self, value):
        """``expires_at`` ISO dari BFF -> datetime naive UTC (None bila tidak valid)."""
        if not isinstance(value, str) or len(value) > 40:
            return None
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1]
        elif text.endswith("+00:00"):
            text = text[:-6]
        try:
            return fields.Datetime.to_datetime(text.replace("T", " ").split(".")[0])
        except ValueError:
            return None

    def _efn_is_live(self):
        """Sesi menerima kiriman: ``open`` dan belum lewat ``expires_at``."""
        self.ensure_one()
        return self.state == "open" and self.expires_at > fields.Datetime.now()

    def _efn_may_attach(self, order):
        """SEC-166 (r15): order dibayar yang belum tertaut boleh dilekatkan ke sesi ini hanya bila sesi ``open`` (bukan
        ``expired``), ``order.date_order >= opened_at``, dan order membawa baris kiriman QR sesi ini (hasil gabung
        order kiriman QR ke order lain). Order pelayan/carried sudah tertaut saat buka/cetak ulang dan order kiriman
        tertaut saat dibuat, jadi tidak lewat sini. Order tamu berikutnya di meja yang sama (tanpa baris QR sesi ini)
        TIDAK pernah diserap walau sesi masih open/expired."""
        self.ensure_one()
        if self.state != "open":
            return False
        if not order.date_order or order.date_order < self.opened_at:
            return False
        refs = set(order.sudo().lines.mapped("efn_qr_submission_ref"))
        refs.discard(False)
        if not refs:
            return False
        return bool(self.sudo().submission_ids.filtered(lambda s: s.submission_ref in refs))

    def _efn_lock(self):
        """Serialkan transaksi yang menghitung batas/seq sesi ini (pola SEC-127 ``touch_row``)."""
        for session in self:
            touch_row(self.env.cr, self._table, session.id)

    def _efn_next_seq(self):
        """seq event = max(seq terakhir + 1, detik sejak 2026-01-01) — tetap naik setelah DB dipulihkan."""
        self.ensure_one()
        self._efn_lock()  # dua transaksi paralel tidak boleh mendapat seq sama (unik per sesi)
        seq = max(self.event_seq + 1, epoch_seconds())
        self.sudo().write({"event_seq": seq})
        return seq

    def _efn_paid_orders(self):
        """Order sesi yang dibayar, TANPA order refund (refund dinetokan ke order asalnya, K2 r15)."""
        self.ensure_one()
        return self.order_ids.sudo().filtered(lambda o: o.state in PAID_STATES and not o._efn_qr_is_refund())

    @api.model
    def _efn_order_mgm_net(self, order, refunds):
        """G31b (docs/04 §20.10): dasar MGM NETO satu order = rumus ``_efn_order_net_eligible`` + kategori pengecualian
        kebijakan MGM PT order (``pos.order._efn_mgm_excluded_ids``, efn_pos_loyalty_ledger + efn_mgm_policy opsional).
        ``None`` = tidak dikirim (tanpa kebijakan/pengecualian, atau modul tidak terpasang)."""
        if not hasattr(order, "_efn_mgm_excluded_ids"):
            return None
        excluded = order._efn_mgm_excluded_ids()
        if not excluded:
            return None
        value = order._efn_loyalty_eligible_raw(order.lines.sudo(), extra_excluded=excluded)
        for refund, lines in refunds:
            value += refund._efn_loyalty_eligible_raw(lines, extra_excluded=excluded)
        value *= order._efn_loyalty_money_factor()
        return int(math.floor(value + 1e-6)) if value > 0 else 0

    @api.model
    def _efn_order_eligible_raw(self, order, lines=None):
        """Dasar poin (float, bertanda; baris refund negatif) — rumus earn POS bila ``efn_pos_loyalty_ledger``
        terpasang (``_efn_loyalty_eligible_raw``: tanpa pajak, tanpa kategori dikecualikan), selain itu subtotal tanpa
        pajak. Faktor porsi Poin EFN TIDAK di sini (lihat ``_efn_order_net_eligible``)."""
        lines = order.lines.sudo() if lines is None else lines
        if hasattr(order, "_efn_loyalty_eligible_raw"):
            return order._efn_loyalty_eligible_raw(lines)
        return sum(lines.mapped("price_subtotal"))

    @api.model
    def _efn_order_refunds(self, order):
        """Order refund (dibayar) yang menunjuk ``order`` beserta baris refund-nya: ``[(refund, lines)]``."""
        refunds = (
            self.env["pos.order"]
            .sudo()
            .search(
                [("lines.refunded_orderline_id.order_id", "=", order.id), ("state", "in", PAID_STATES)],
                order="id",
            )
        )
        return [
            (refund, refund.lines.filtered(lambda line, o=order: line.refunded_orderline_id.order_id == o))
            for refund in refunds
        ]

    @api.model
    def _efn_order_net_eligible(self, order, refunds):
        """K-1/K-18-4 + K2 r15 (SEC-164a): dasar poin NETO = (dasar order − Σ dasar baris refund yang sudah dibayar)
        × faktor porsi non-poin order asal, dibulatkan ke bawah ke Rupiah; ≤ 0 -> 0."""
        value = self._efn_order_eligible_raw(order)
        for refund, lines in refunds:
            value += self._efn_order_eligible_raw(refund, lines)  # baris refund bertanda negatif
        if hasattr(order, "_efn_loyalty_money_factor"):
            value *= order._efn_loyalty_money_factor()
        return int(math.floor(value + 1e-6)) if value > 0 else 0

    @api.model
    def _efn_order_eligible_amount(self, order):
        """Dasar poin satu order (tanpa refund) — dipertahankan untuk pemanggil lama."""
        return self._efn_order_net_eligible(order, [])

    def _efn_close_orders(self):
        """Hitung (tanpa menulis) daftar order event close: ``(entries, refunds, eligible)``.

        - ``entries`` = ``[{order_uuid, eligible_amount, company_code, pos_member_attached}]`` (urut id);
          ``eligible_amount`` = dasar poin NETO semua refund yang sudah dibayar (SEC-164a); neto ≤ 0 -> tidak dikirim.
        - ``pos_member_attached`` = order sudah punya member POS (SEC-165, ``pos.order._efn_qr_pos_member_attached``):
          BFF tidak meng-earn klaim QR untuk order itu (earn POS/OTP kasir yang berlaku).
        - ``refunds`` = order refund yang dinetokan; ``eligible`` = ``[(order, net)]`` yang dapat di-earn QR."""
        self.ensure_one()
        entries, eligible = [], []
        refunded = self.env["pos.order"].sudo()
        for order in self._efn_paid_orders().sorted("id"):
            refunds = self._efn_order_refunds(order)
            for refund, _lines in refunds:
                refunded |= refund
            net = self._efn_order_net_eligible(order, refunds)
            if net <= 0:
                continue
            attached = order._efn_qr_pos_member_attached()
            if not attached:
                eligible.append((order, net))
            entry = {
                "order_uuid": order.uuid,
                "eligible_amount": net,
                "company_code": order.company_id.efn_brand_code or None,
                "pos_member_attached": attached,
            }
            mgm = self._efn_order_mgm_net(order, refunds)
            if mgm is not None:
                # G31b §20.10: hanya bila kebijakan MGM PT punya pengecualian kategori (snapshot beku ikut membawanya)
                entry["mgm_eligible_amount"] = min(mgm, net)
            entries.append(entry)
        return entries, refunded, eligible

    def _efn_paid_orders_payload(self):
        """L-80 + K2 r15: ``close.orders`` TANPA efek samping (snapshot awal saat enqueue, untuk tampilan; yang
        dikirim = snapshot yang dibekukan outbox sebelum POST pertama, K3 r15 / L-83)."""
        return self._efn_close_orders()[0]

    def _efn_pending_close(self):
        """Outbox ``session_closed paid`` sesi ini yang belum diterima BFF (``pending``), atau recordset kosong."""
        self.ensure_one()
        return (
            self.env["efn.pos.qr.event.outbox"]
            .sudo()
            .search(
                [
                    ("table_session_id", "=", self.id),
                    ("event", "=", "session_closed"),
                    ("close_reason", "=", "paid"),
                    ("state", "=", "pending"),
                ],
                limit=1,
            )
        )

    def _efn_awaiting_refunds(self):
        """Order refund bertanda ``efn_qr_refund_awaiting`` yang menunjuk order sesi ini yang dibayar."""
        self.ensure_one()
        originals = self._efn_paid_orders()
        if not originals:
            return self.env["pos.order"].sudo()
        return (
            self.env["pos.order"]
            .sudo()
            .search(
                [("efn_qr_refund_awaiting", "=", True), ("lines.refunded_orderline_id.order_id", "in", originals.ids)],
                order="id",
            )
        )

    def _efn_mark_close_sent(self, outbox):
        """K3 r15 (L-83): dipanggil outbox SETELAH event ``session_closed paid`` diterima BFF (2xx), di bawah kunci
        sesi.
        Semua nilai dari snapshot BEKU yang dikirim (bukan dihitung ulang):

        - refund di ``netted_refund_ids`` -> ``efn_qr_refund_netted`` (tidak pernah memicu reverse QR);
        - order ``pos_member_attached=false`` -> ``efn_qr_points_eligible`` + ``efn_qr_eligible_amount`` =
          ``eligible_amount`` snapshot (penyebut rasio reverse);
        - refund ``efn_qr_refund_awaiting`` (dibayar setelah pembekuan) -> flag dilepas lalu diproses ulang oleh
          ``efn_pos_loyalty_ledger`` (``_efn_loyalty_qr_refund``) yang kini melihat ``reverse`` -> reverse QR
          di-enqueue."""
        self.ensure_one()
        PosOrder = self.env["pos.order"].sudo()
        netted = PosOrder.browse(outbox.netted_refund_ids or []).exists()
        if netted:
            netted.write({"efn_qr_refund_netted": True, "efn_qr_refund_awaiting": False})
        amounts = {
            entry["order_uuid"]: entry["eligible_amount"]
            for entry in (outbox.orders_snapshot or [])
            if not entry.get("pos_member_attached")
        }
        if amounts:
            for order in PosOrder.search([("uuid", "in", sorted(amounts))]):
                order.write({"efn_qr_points_eligible": True, "efn_qr_eligible_amount": amounts[order.uuid]})
        originals = self._efn_paid_orders()
        for refund in self._efn_awaiting_refunds():
            refund.write({"efn_qr_refund_awaiting": False})
            if not hasattr(refund, "_efn_loyalty_qr_refund"):
                continue
            for original in refund.lines.refunded_orderline_id.order_id & originals:
                refund._efn_loyalty_qr_refund(original, refund._efn_loyalty_goods_lines())
            refund._efn_loyalty_refresh_state()
        if "efn.pos.loyalty.outbox" in self.env:
            self.env["efn.pos.loyalty.outbox"]._efn_trigger()

    def _efn_alert_awaiting_refunds(self, outbox):
        """K3 r15 (L-83): event close gagal permanen -> refund ``awaiting`` TETAP bertanda (untuk review) + alert.

        BFF tidak menerima event, jadi tidak ada earn yang perlu dipotong; alert memastikan staf memeriksa bila BFF
        ternyata sempat meng-earn (mis. 4xx setelah pemrosesan). Log hanya id (tanpa PII)."""
        self.ensure_one()
        refunds = self._efn_awaiting_refunds()
        if not refunds:
            return
        event = {
            "event": "EFN_QR_REFUND_UNRESOLVED",
            "outbox_id": outbox.id,
            "table_session_id": self.id,
            "company_id": self.company_id.id,
            "refund_order_ids": refunds.ids,
        }
        _alert_logger.error("EFN_QR_REFUND_UNRESOLVED %s", json.dumps(event, sort_keys=True))

    # ------------------------------------------------------------------ penutupan
    def _efn_close(self, reason, emit=True):
        """Tutup sesi (idempoten untuk sesi yang sudah ``closed``). ``emit`` = antre event ``session_closed``."""
        Outbox = self.env["efn.pos.qr.event.outbox"].sudo()
        for session in self.sudo():
            if session.state == "closed":
                continue
            session._efn_lock()
            session.write({"state": "closed", "close_reason": reason, "closed_at": fields.Datetime.now()})
            _logger.info("EFN_QR_SESSION_CLOSED session=%s reason=%s", session.id, reason)
            if emit:
                Outbox._efn_enqueue_close(session, reason)

    def _efn_check_close_after_orders(self):
        """Dipanggil setelah order sesi/meja final (dibayar/batal/dihapus): tutup bila meja tanpa order draft."""
        PosOrder = self.env["pos.order"].sudo()
        for session in self.sudo().filtered(lambda s: s.state in ("open", "expired")):
            drafts = PosOrder.search_count(
                [
                    ("config_id", "=", session.config_id.id),
                    ("table_id", "=", session.table_id.id),
                    ("state", "=", "draft"),
                ],
                limit=1,
            )
            if drafts:
                continue
            reason = "paid" if session._efn_paid_orders() else "cancelled"
            session._efn_close(reason)

    # ------------------------------------------------------------------ cron
    @api.model
    def _cron_efn_qr_expire(self):
        """Sesi ``open`` lewat ``expires_at`` -> ``expired`` (tanpa event: BFF mengedaluwarsakan sendiri)."""
        now = fields.Datetime.now()
        sessions = self.sudo().search([("state", "=", "open"), ("expires_at", "<=", now)], limit=1000)
        if sessions:
            sessions.write({"state": "expired"})
            _logger.info("EFN QR: %s sesi meja kedaluwarsa", len(sessions))
        # Sesi POS sudah ditutup tetapi sesi meja masih open/expired -> tutup (pengaman bila hook pos.session terlewat).
        orphans = self.sudo().search(
            [("state", "in", ("open", "expired")), ("pos_session_id.state", "=", "closed")], limit=1000
        )
        for session in orphans:
            session._efn_close("paid" if session._efn_paid_orders() else "closed_by_staff")

    @api.model
    def _cron_efn_qr_retention(self):
        """L-76: sesi meja Odoo disimpan 400 hari lalu dihapus (order POS tetap, retensi pos.order; tautan order ->
        sesi menjadi kosong). Kiriman QR ikut terhapus (cascade); outbox ``done`` sudah dibersihkan 30 hari."""
        limit = fields.Datetime.subtract(fields.Datetime.now(), days=RETENTION_DAYS)
        old = self.sudo().search([("state", "!=", "open"), ("opened_at", "<", limit)], limit=5000)
        if old:
            _logger.info("EFN QR: retensi menghapus %s sesi meja > %s hari", len(old), RETENTION_DAYS)
            old.unlink()
