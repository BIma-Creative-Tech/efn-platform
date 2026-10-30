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

G19 (docs/04 §11.1-§11.2, docs/08 §10.1 E-35 / §10.2 E-36; keputusan L-27/L-28):
- Kanal ``stock_hold_mode='confirm'``: place membuat SO, menerapkan promo, mencocokkan ``quote_hash``, lalu
  mengonfirmasi SO (``CONFIRM_SCOPE``) dan mewajibkan SEMUA move storable ``assigned``; kurang -> seluruh savepoint
  place di-rollback + ``efn_online:out_of_stock`` (tidak ada SO setengah tercadang). Kupon sekali pakai terkonsumsi di
  sini; batal/kedaluwarsa -> ``_action_cancel`` mengembalikan kupon + melepas reservasi.
- mark_paid untuk SO yang sudah ``sale``: cek move masih ``assigned`` (bukan ``free_qty``: reservasi sendiri
  mengurangi ``free_qty``); dicabut POS (``_free_reservation``) -> ``_action_assign``; tetap kurang -> ``stock_short``.
- ``action_efn_ready``: move tercadang ditandai ``picked`` agar penjualan kasir berikutnya tidak mencabutnya.

Putaran r12 (docs/04 §11.7): place mengunci program berkuota sebelum promo dihitung (L-40 / SEC-130); batal/kedaluwarsa
order yang tidak pernah dibayar tidak membuat refund/nota kredit (L-43 / SEC-139).

G20 E-45 bayar di kasir (docs/04 §12.2, L-50/L-52/L-55/L-56): place ``payment_mode='counter'`` + ``counter_code``
(dibuat BFF) -> digest KDF deterministik per company (``counter_code_hash``, bukan teks asli, tidak di-log, tidak
dikembalikan). BFF ``mark_paid``/``refund_settled`` untuk order counter -> ``invalid_state``; pelunasan hanya lewat
``_efn_counter_mark_paid`` (dipanggil sync POS ``efn_online_pos_counter``), pembayaran faktur di jurnal kliring PT
(``_efn_payment_journal``). Event ``paid`` membawa ``payment: {method: "counter"}``.

G21 E-39 faktur atas nama pembeli (docs/04 §13.5, L-63; docs/08 §10.7): ``efn_online_set_invoice_party`` memasang
partner pembeli **terlindungi** (``efn_pii``) sebagai ``partner_invoice_id`` SO sebelum bayar. Tanpa ``efn_pii``
terpasang dengan field partner benar-benar terenkripsi, atau PT bukan PPN -> ``efn_online:invoice_not_available``
(profil tidak pernah masuk Odoo tanpa enkripsi). Profil tidak di-log, tidak masuk hasil/hash/outbox.

G26 (docs/04 §17.15, L-95..L-98): ``payment_mode`` bertambah ``va``/``ewallet`` (gateway, sama dengan ``qris``) dan
``bank_transfer`` (hasil place membawa ``transfer_account`` rekening PT penjual; dilunasi hanya lewat jalur internal
Verifikator di ``efn_online_transfer``, BFF ``mark_paid`` -> ``invalid_state``). ``counter`` dibekukan untuk pesanan
baru (kill switch ``efn_online.counter_enabled``).

G22 (docs/04 §14.B W-04, §14.C W-12; L-72 D-22-8/D-22-9/D-22-11):
- B-4: place menerima baris ``{product_id, qty, attribute_value_ids?}`` (PTAV ikut ``payload_sha256`` hanya bila ada).
- B-6/B-7: hook ``_efn_after_confirmed`` / ``_efn_after_cancelled`` / ``_efn_after_refund`` (no-op; diisi jembatan
  ``efn_online_kds_bridge``), ``_efn_set_ready(source)`` (event ``ready`` + ``ready_source``), event opsional
  ``preparing`` (``kitchen_started_at``) lewat ``_efn_set_kitchen_started``.
- W-12: place ``points_payment {points, value_idr}`` divalidasi terhadap kebijakan PT (``points_invalid``) -> hasil
  ``amount_due``; ``mark_paid`` ``payment.points_value``; faktur dibayar dua kali (tunai di jurnal titipan/kliring +
  Poin EFN di jurnal metode "Poin EFN" PT); ``eligible_amount`` (K-1/K-18-4) dibekukan saat ``picked_up``; refund
  membawa ``points_value`` / ``cash_amount`` / ``eligible_amount_refunded`` (alokasi proporsional Q-F18-4).
"""

import functools
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import psycopg2

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import AccessError, ConcurrencyError, UserError, ValidationError
from odoo.modules.registry import Registry
from odoo.tools import float_compare

from . import efn_online_loyalty as loyalty
from .efn_online_channel import iso_utc, parse_iso_utc
from .efn_online_common import (
    BUYER_DOCUMENT_TYPES,
    CANCEL_REASONS,
    CANCEL_SCOPE,
    CONFIRM_SCOPE,
    DEFAULT_PAYMENT_MODE,
    DEFAULT_PICKUP_KDF_ROUNDS,
    FULFILMENTS,
    HANDOVER_SCOPE,
    IDEMPOTENCY_KEY_RE,
    INTERNAL_PAYMENT_MODES,
    MAX_STATUS_REFS,
    OPAQUE_REF_RE,
    OUTLET_CODE_RE,
    PAYMENT_MODES,
    PICKUP_CODE_RE,
    REFUND_REASONS,
    UUID_RE,
    canonical_sha256,
    cart_lines_canonical,
    cart_lines_payload,
    check_amount_int,
    check_str,
    counter_code_digest,
    counter_enabled,
    counter_kdf_rounds,
    counter_secret,
    floor_idr,
    internal_scope,
    invoice_profile_payload,
    lines_payload,
    online_error,
    pickup_crypt_context,
    points_payment_payload,
    promo_codes_payload,
    require,
)

_logger = logging.getLogger(__name__)

# G18-QA-01: dasar seq berbasis waktu (detik UTC sejak tanggal ini).
SEQ_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def _efn_epoch_seconds():
    return int((datetime.now(UTC) - SEQ_EPOCH).total_seconds())


# L-79: penanda "aktivitas Finance akun Poin EFN sudah dibuat hari ini" per company.
POINTS_ALERT_PARAM = "efn_online.points_account_alert"


def _efn_points_account_alert_job(dbname, company_id):
    """Callback ``cr.postrollback``: cursor baru (transaksi request sudah di-rollback) -> aktivitas Finance."""
    try:
        with Registry(dbname).cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            company = env["res.company"].browse(company_id).exists()
            if company:
                env["efn.online.order"]._efn_points_account_activity(company)
    except Exception:
        _logger.exception(
            "EFN online: aktivitas Finance akun Poin EFN gagal (company #%s)",
            company_id,
        )


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
# G19 E-36: opsional (BFF lama tanpa promo tetap sah); bila ada & tidak kosong ikut payload_sha256.
# G20 E-45 (docs/04 §12.2 no. 3, L-56): `payment_mode` ("qris" default | "counter") + `counter_code` (dibuat BFF,
# wajib bila counter, dilarang bila qris). `payment_mode` counter ikut payload_sha256; `counter_code` TIDAK (pola
# pickup_code: sha256 cepat atas 39,6 bit dapat di-brute force).
# G22 W-12 (docs/04 §14.C.3 "Place"): `points_payment` opsional (ikut payload_sha256 hanya bila ada).
PLACE_OPTIONAL_KEYS = frozenset({"promo_codes", "payment_mode", "counter_code", "points_payment"})
PAYMENT_MODE_LABELS = {
    "qris": "QRIS / gateway (BFF)",
    "va": "Virtual Account (BFF)",
    "ewallet": "E-wallet (BFF)",
    "bank_transfer": "Transfer bank + bukti",
    "counter": "Bayar di kasir",
}
# Order bayar di kasir yang tidak lagi bisa dibayar normal (batal/kedaluwarsa) tetapi uangnya sudah diterima kasir.
COUNTER_LATE_REVIEW = {
    "cancelled": "counter_paid_after_cancel",
    "placed": "counter_paid_after_expiry",
}
# G21 E-39: field partner yang menerima profil pembeli; semuanya WAJIB kelas field terenkripsi efn_pii (EfnPiiChar)
# sebelum profil ditulis (L-63). NIK/paspor ditulis ke dokumen pembeli Coretax -> butuh enkripsi field itu juga
# (efn_pii_l10n_id, E-38a sisa).
INVOICE_PARTY_PII_FIELDS = ("name", "street", "email", "vat")
INVOICE_PARTY_DOC_FIELDS = ("l10n_id_buyer_document_number",)
INVOICE_PARTY_PROTECTED_FIELD = "efn_pii_protected"
INVOICE_PARTY_MAX_CANDIDATES = 50
PAYMENT_KEYS = frozenset({"payment_ref", "amount_paid", "paid_at", "idempotency_key"})
# G22 W-12 (docs/04 §14.C.4): `payment.points_value` opsional (= nilai Poin EFN yang di-commit BFF; harus sama dengan
# nilai saat place, selain itu `review`).
PAYMENT_OPTIONAL_KEYS = frozenset({"points_value"})
READY_SOURCES = [("manual", "Manual (staf)"), ("kds", "Layar dapur (KDS)")]
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
    channel_id = fields.Many2one(
        "efn.online.channel",
        required=True,
        readonly=True,
        index=True,
        check_company=True,
    )
    outlet_id = fields.Many2one("efn.online.outlet", required=True, readonly=True, check_company=True)
    fulfilment = fields.Selection([(f, f) for f in FULFILMENTS], required=True, readonly=True, default="pickup")
    platform_order_ref = fields.Char(required=True, readonly=True, index=True, help="part_id BFF (UUID, opak).")
    checkout_ref = fields.Char(required=True, readonly=True, index=True, help="order_id BFF (UUID, opak).")
    idempotency_key = fields.Char(required=True, readonly=True)
    payload_sha256 = fields.Char(readonly=True)
    quote_hash = fields.Char(readonly=True)
    sale_order_id = fields.Many2one("sale.order", readonly=True, ondelete="restrict", index=True, check_company=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection(
        STATES,
        required=True,
        default="placed",
        readonly=True,
        index=True,
        tracking=True,
    )
    amount_total = fields.Monetary(readonly=True)
    amount_payable = fields.Monetary(readonly=True)
    expires_at = fields.Datetime(readonly=True, index=True)
    payment_ref = fields.Char(readonly=True, groups="base.group_system", copy=False)
    paid_idempotency_key = fields.Char(readonly=True, groups="base.group_system", copy=False)
    paid_amount = fields.Monetary(readonly=True)
    paid_at = fields.Datetime(readonly=True)
    invoice_id = fields.Many2one("account.move", readonly=True, check_company=True, copy=False)
    pickup_code_hash = fields.Char(readonly=True, groups="base.group_system", copy=False)
    payment_mode = fields.Selection(
        [(mode, PAYMENT_MODE_LABELS[mode]) for mode in PAYMENT_MODES],
        string="Metode bayar",
        required=True,
        default=DEFAULT_PAYMENT_MODE,
        readonly=True,
        help="G20 E-45: 'Bayar di kasir' = dilunasi lewat POS (kode bayar) -> jalur internal mark_paid, bukan BFF.",
    )
    counter_code_hash = fields.Char(
        readonly=True,
        groups="base.group_system",
        copy=False,
        index="btree_not_null",
        help="Digest KDF kode bayar di kasir (bukan kode ambil). Dihapus saat dibayar/batal/review.",
    )
    invoice_party_idempotency_key = fields.Char(
        readonly=True,
        groups="base.group_system",
        copy=False,
        help="G21 E-39: kunci idempotensi efn_online_set_invoice_party (tanpa profil pembeli).",
    )
    # G22 W-12 (docs/04 §14.C): porsi Poin EFN bagian ini (nilai dari BFF, divalidasi kebijakan PT saat place).
    points_redeemed = fields.Integer(string="Poin ditukar", readonly=True, copy=False)
    points_value = fields.Monetary(
        string="Nilai Poin EFN",
        readonly=True,
        copy=False,
        help="Porsi pembayaran dengan Poin EFN (bukan diskon; DPP/pajak tetap atas harga penuh, K-18-1).",
    )
    amount_due = fields.Monetary(
        string="Tagihan tunai",
        compute="_compute_amount_due",
        store=True,
        help="amount_payable - nilai Poin EFN = yang dibayar lewat QRIS/kasir (docs/04 §14.C.2).",
    )
    eligible_amount = fields.Integer(
        string="Dasar earn Poin EFN",
        readonly=True,
        copy=False,
        help="K-1/K-18-4: Σ subtotal tanpa pajak baris berhak poin (neto refund) × porsi uang; dibekukan saat diambil.",
    )
    # G31b (docs/04 §20.10): dasar MGM beku saat diambil (rumus eligible + kategori pengecualian kebijakan MGM PT).
    efn_mgm_eligible_amount = fields.Integer(string="Dasar MGM (Rp)", readonly=True, copy=False)
    efn_mgm_basis_set = fields.Boolean(readonly=True, copy=False)
    kitchen_started_at = fields.Datetime(
        string="Mulai dimasak",
        readonly=True,
        copy=False,
        help="G22 B-7: tiket KDS pertama mulai dikerjakan.",
    )
    ready_source = fields.Selection(READY_SOURCES, string="Sumber 'siap'", readonly=True, copy=False)
    pickup_attempts = fields.Integer(readonly=True, copy=False)
    ready_at = fields.Datetime(readonly=True)
    picked_up_at = fields.Datetime(readonly=True)
    cancelled_at = fields.Datetime(readonly=True)
    cancel_reason = fields.Selection([(r, r) for r in CANCEL_REASONS], readonly=True)
    cancel_idempotency_key = fields.Char(readonly=True, groups="base.group_system", copy=False)
    review_reason = fields.Char(readonly=True, help="Kode saja (tanpa data pribadi).")
    short_lines = fields.Text(
        readonly=True,
        help="JSON [{product_id, requested, available}] saat stok kurang.",
    )
    event_seq = fields.Integer(readonly=True, copy=False)
    reminder_flagged = fields.Boolean(readonly=True, copy=False)
    refund_ids = fields.One2many("efn.online.refund", "order_id", readonly=True)
    event_ids = fields.One2many("efn.online.event.outbox", "order_id", readonly=True)

    _idempotency_key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi place harus unik.")
    _platform_order_ref_unique = models.Constraint("unique (platform_order_ref)", "platform_order_ref harus unik.")
    _sale_order_unique = models.Constraint("unique (sale_order_id)", "Satu SO hanya untuk satu pesanan online.")
    _points_value_check = models.Constraint(
        "CHECK (points_value IS NULL OR points_value >= 0)",
        "Nilai Poin EFN tidak boleh negatif.",
    )

    @api.depends("amount_payable", "points_value")
    def _compute_amount_due(self):
        for order in self:
            order.amount_due = (order.amount_payable or 0.0) - (order.points_value or 0.0)

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
            [
                ("platform_order_ref", "=", platform_order_ref.lower()),
                ("channel_id", "=", channel.id),
            ],
            limit=1,
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

    def _efn_next_seq(self):
        r"""G18-QA-01: ``seq`` = max(seq terakhir + 1, detik sejak 2026-01-01 UTC).

        Dipilih karena paling aman tanpa ketergantungan jaringan di transaksi perubahan state: setelah DB Odoo
        dibuat ulang / dipulihkan, ``event_seq`` mundur, tetapi detik dinding terus naik sehingga event baru tetap
        > ``last_seq`` BFF (BFF mengabaikan ``seq <= last_seq``). Alternatif ditolak: tarik watermark dari BFF
        (panggilan jaringan di dalam transaksi; BFF mati = state tidak bisa berubah), watermark lokal (ikut hilang
        saat restore). Asumsi: jam server disinkron NTP; event beruntun dalam detik yang sama memakai +1, jadi seq
        bisa mendahului jam beberapa detik (aman selama restore memakan lebih lama dari itu). Muat dalam 9 digit
        (kontrak BFF ``\d{1,9}``) sampai ~2057.
        """
        self.ensure_one()
        return max(self.event_seq + 1, _efn_epoch_seconds())

    def _efn_emit(self, event, refund=None, detail_code=None):
        """Antrikan event outbox (seq naik monoton per order, berbasis waktu). Payload dibangun saat kirim.
        ``detail_code`` (G26): kode tambahan yang dibekukan di baris outbox (mis. alasan ``transfer_rejected``)."""
        self.ensure_one()
        seq = self._efn_next_seq()
        self.write({"event_seq": seq})
        return (
            self.env["efn.online.event.outbox"]
            .sudo()
            ._efn_enqueue(self, event, seq, refund=refund, detail_code=detail_code)
        )

    def _efn_clear_secret(self):
        """Hapus hash kode ambil saat final (delta §10.4.4 no. 1) + digest kode bayar di kasir (E-45)."""
        self.sudo().write({"pickup_code_hash": False, "counter_code_hash": False})

    # --------------------------------------------------------- bayar di kasir (E-45)
    @api.model
    def _efn_counter_digests(self, company, code):
        """Digest kandidat kode bayar untuk ``company``: satu per nilai putaran KDF yang dipakai pesanan terbuka
        (biasanya satu) + putaran saat ini. ``code`` sudah dinormalisasi & divalidasi pemanggil."""
        self.flush_model(["counter_code_hash", "company_id"])
        self.env.cr.execute(
            "SELECT DISTINCT split_part(counter_code_hash, '$', 2) FROM efn_online_order "
            "WHERE company_id = %s AND counter_code_hash IS NOT NULL",
            (company.id,),
        )
        rounds = {int(row[0]) for row in self.env.cr.fetchall() if (row[0] or "").isdigit()}
        rounds.add(counter_kdf_rounds(self.env))
        secret = counter_secret(self.env)
        return [counter_code_digest(secret, company.id, code, r) for r in sorted(rounds)]

    @api.model
    def _efn_counter_find(self, company, code):
        """Pesanan bayar di kasir milik ``company`` dengan kode ``code`` (digest masih tersimpan) atau kosong.
        Dipanggil hanya dari jalur server ber-cek (lookup POS); hasil sudo."""
        if not isinstance(code, str) or not PICKUP_CODE_RE.match(code):
            return self.browse()
        digests = self._efn_counter_digests(company, code)
        return (
            self.sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("company_id", "=", company.id),
                    ("payment_mode", "=", "counter"),
                    ("counter_code_hash", "in", digests),
                ],
                limit=1,
            )
        )

    def _efn_counter_payable(self):
        """True bila order bayar di kasir masih bisa dilunasi normal (placed, belum kedaluwarsa, belum dibayar)."""
        self.ensure_one()
        return (
            self.payment_mode == "counter"
            and self.state == "placed"
            and not self.sudo().paid_idempotency_key
            and bool(self.expires_at)
            and self.expires_at > fields.Datetime.now()
        )

    def _efn_counter_mark_paid(self, payment_ref, idempotency_key, amount_paid):
        """Jalur INTERNAL mark_paid untuk pelunasan POS (docs/04 §12.2 no. 6; tidak ada metode RPC).

        Dipanggil hanya oleh sync order POS (modul ``efn_online_pos_counter``) setelah validasi server. Idempoten
        per ``idempotency_key`` (= ``pos_counter:<pos.order.uuid>``). Alur sama dengan BFF ``mark_paid`` (cek stok/
        reservasi -> konfirmasi -> faktur -> pembayaran di jurnal kliring PT) -> ``confirmed`` | ``stock_short`` |
        ``review``. L-56: order yang sudah batal / kedaluwarsa saat uang diterima kasir -> pembayaran dicatat +
        ``review`` (bukan sukses; dana dikembalikan lewat refund POS). Sudah dibayar dengan kunci lain ->
        ``UserError`` (sync POS ditolak, cegah bayar dobel)."""
        self.ensure_one()
        order = self.with_env(self.env(su=True)).with_company(self.company_id)
        order._efn_lock()
        if order.payment_mode != "counter":
            raise online_error("invalid_state")
        payment_ref = check_str(payment_ref, OPAQUE_REF_RE)
        key = check_str(idempotency_key, IDEMPOTENCY_KEY_RE)
        amount_paid = check_amount_int(amount_paid)
        now = fields.Datetime.now()
        if order.paid_idempotency_key:
            if order.paid_idempotency_key == key and order.payment_ref == payment_ref:
                return order._efn_paid_result()
            raise online_error("invalid_state")
        if order.state == "placed" and order.expires_at and order.expires_at > now:
            return order._efn_apply_payment(payment_ref, key, amount_paid, now)
        reason = COUNTER_LATE_REVIEW.get(order.state)
        if not reason:
            raise online_error("invalid_state")
        order.write(
            {
                "payment_ref": payment_ref,
                "paid_idempotency_key": key,
                "paid_amount": amount_paid,
                "paid_at": now,
            }
        )
        order._efn_set_review(reason)
        return order._efn_paid_result()

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
                note=_(
                    "Pesanan online %(name)s: %(code)s",
                    name=self.name or self.platform_order_ref,
                    code=code,
                ),
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
            # G22 W-12 (docs/04 §14.C.3 "Place"): porsi tunai (= amount_payable bila tanpa Poin EFN).
            "amount_due": self._efn_amount_out(self.amount_due),
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
        """Buat SO idempoten (docs/04 §10.4.3 no. 5): draft (mode ``none``) atau terkonfirmasi + stok tercadang (mode
        ``confirm``, §11.1). ``promo_codes`` opsional (§11.2 no. 2) = ``promo_applied`` grup ini dari quote."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(order, dict) and PLACE_KEYS <= set(order) <= PLACE_KEYS | PLACE_OPTIONAL_KEYS)
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
        normalized_lines = cart_lines_canonical(cart_lines_payload(order["lines"], channel.max_lines))
        promo_codes = promo_codes_payload(order.get("promo_codes"))
        # G22 W-12: porsi Poin EFN (opsional). Batas kebijakan dicek setelah amount_payable dihitung (savepoint place).
        points_payment = points_payment_payload(order.get("points_payment"))
        # G20 E-45 (L-56): metode bayar + kode bayar di kasir (dibuat BFF). Kanal tidak mengizinkan -> invalid_input.
        payment_mode = order.get("payment_mode", DEFAULT_PAYMENT_MODE)
        require(payment_mode in PAYMENT_MODES and payment_mode in channel._efn_online_payment_modes())
        # L-95: kanal lama yang masih memuat 'counter' tidak menerima pesanan counter baru selama kill switch mati.
        require(payment_mode != "counter" or counter_enabled(self.env))
        transfer_account = None
        if payment_mode == "bank_transfer":
            # G26 §17.15: rekening tujuan dari jurnal bank kanal (modul efn_online_transfer). Tidak lengkap -> tolak
            # sebelum SO dibuat (pelanggan tidak boleh diberi rekening kosong).
            transfer_account = channel._efn_online_transfer_account()
            require(transfer_account is not None)
        counter_code = order.get("counter_code")
        if payment_mode == "counter":
            require(isinstance(counter_code, str) and bool(PICKUP_CODE_RE.match(counter_code)))
            require(counter_code != pickup_code)  # L-50: kode bayar != kode ambil
        else:
            require("counter_code" not in order)
        # Kode ambil TIDAK ikut hash payload (sha256 cepat atas 39,6 bit dapat di-brute force). Kode promo ikut
        # (hanya bila ada, agar hash payload tanpa promo sama dengan G17/G18).
        payload = {
            "channel": channel.code,
            "platform_order_ref": ref,
            "checkout_ref": checkout_ref,
            "outlet_code": outlet_code,
            "fulfilment": fulfilment,
            "lines": normalized_lines,
            "quote_hash": quote_hash,
        }
        if promo_codes:
            payload["promo_codes"] = promo_codes
        if payment_mode != DEFAULT_PAYMENT_MODE:
            # Hanya bila bukan default -> hash payload QRIS sama dengan G19/G20 awal. Kode bayar TIDAK ikut.
            payload["payment_mode"] = payment_mode
        if points_payment:
            # G22 §14.C.3: key sama + poin beda -> idempotency_conflict. Tanpa poin -> hash lama tidak berubah.
            payload["points_payment"] = points_payment
        payload_sha = canonical_sha256(payload)
        Orders = self.with_env(channel.env)
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"efn_online_place:{key}",))
        existing = Orders.search([("idempotency_key", "=", key)], limit=1)
        if existing:
            if existing.channel_id != channel or existing.payload_sha256 != payload_sha:
                raise online_error("idempotency_conflict")
            replay = existing._efn_place_result()
            if transfer_account:
                replay["transfer_account"] = transfer_account
            return replay
        if Orders.search_count([("platform_order_ref", "=", ref)], limit=1):
            raise online_error("idempotency_conflict")

        outlet, product_qty, issues, summary = channel._efn_online_evaluate(
            outlet_code,
            fulfilment,
            order["lines"],
            promo_codes=promo_codes,
            for_place=True,
        )
        codes = {issue["code"] for issue in issues}
        if "not_found" in codes:
            raise online_error("not_found")
        if "out_of_stock" in codes:
            raise online_error("out_of_stock")
        if codes or not product_qty:
            raise online_error("invalid_input")
        if summary is not None and summary["quote_hash"] != quote_hash:
            raise online_error("price_changed")

        counter_hash = False
        if payment_mode == "counter":
            # Digest deterministik per company (lihat efn_online_common): kode bentrok dengan pesanan lain yang masih
            # menyimpan digest -> tolak (peluang ~n/8,5e11; BFF membuat kode baru dan place ulang dengan part baru).
            counter_hash = counter_code_digest(
                counter_secret(self.env),
                channel.company_id.id,
                counter_code,
                counter_kdf_rounds(self.env),
            )
            if Orders._efn_counter_find(channel.company_id, counter_code):
                raise online_error("invalid_input")

        now = fields.Datetime.now()
        # Seluruh langkah tulis dalam satu savepoint: galat apa pun (harga berubah, reservasi tidak penuh) tidak
        # meninggalkan efn.online.order / SO / reservasi / kupon terpakai, juga bila pemanggil menangkap error-nya.
        with self.env.cr.savepoint():
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
                            "payment_mode": payment_mode,
                            "counter_code_hash": counter_hash,
                        }
                    )
                    record.flush_recordset()
            except psycopg2.errors.UniqueViolation as exc:
                # Place paralel dengan kunci yang sama sudah commit: ulangi request -> cabang `existing`.
                raise ConcurrencyError("EFN online: place paralel, diulang") from exc
            record._efn_place_sale_order(
                channel,
                outlet,
                fulfilment,
                product_qty,
                promo_codes,
                quote_hash,
                points_payment=points_payment,
            )
            record._efn_emit("placed")
        result = record._efn_place_result()
        if transfer_account:
            result["transfer_account"] = transfer_account
        _logger.info(
            "EFN online place order=%s part=%s so=%s hold=%s pay=%s points=%s",
            record.id,
            ref,
            record.sale_order_id.id,
            channel.stock_hold_mode,
            payment_mode,
            bool(points_payment),
        )
        return result

    def _efn_check_points_payment(self, channel, points_payment, amount_payable):
        """W-12 (docs/04 §14.C.1 no. 2, §14.C.3 "Place"): pertahanan berlapis atas porsi Poin EFN dari BFF.

        Kanal ``loyalty_redeem`` aktif; kebijakan PT + global dipublikasikan (fail-closed); ``points >=
        redeem_min_points``; ``value_idr == points × point_value_idr``; ``value_idr <= floor(amount_payable ×
        redeem_max_pct / 100)`` (incl. pajak, K-18-2); porsi tunai >= Rp1. Gagal -> ``efn_online:points_invalid``."""
        self.ensure_one()
        require(channel.loyalty_redeem, "points_invalid")
        limits = loyalty.redeem_limits(self.env, channel.company_id)
        require(limits is not None, "points_invalid")
        if not loyalty.points_journal(self.env, channel.company_id):
            # L-79: akun Poin EFN PT belum lengkap -> tolak SEBELUM menerima poin (bukan review setelah bayar).
            self._efn_points_account_missing(channel.company_id)
            raise online_error("points_invalid")
        points = points_payment["points"]
        value = points_payment["value_idr"]
        require(points >= limits["min_points"], "points_invalid")
        require(value == points * limits["point_value_idr"], "points_invalid")
        payable = int(round(amount_payable))
        require(value <= (payable * limits["max_pct"]) // 100, "points_invalid")
        require(value <= payable - 1, "points_invalid")

    @api.model
    def _efn_points_account_missing(self, company):
        """L-79: log ERROR (id company saja, tanpa PII) + jadwalkan aktivitas Finance SETELAH rollback request.

        Place yang ditolak me-rollback seluruh transaksi RPC, jadi aktivitas dibuat lewat ``cr.postrollback`` di cursor
        baru (``_efn_points_account_alert_job``); sekali per company per hari (``_efn_points_account_activity``).
        """
        _logger.error(
            "EFN online: metode/jurnal Poin EFN company #%s tanpa akun pembayaran; penukaran poin online ditolak "
            "(L-79, isi akun di efn_setup_pos / Finance)",
            company.id,
        )
        job = functools.partial(_efn_points_account_alert_job, self.env.cr.dbname, company.id)
        self.env.cr.postrollback.add(job)

    @api.model
    def _efn_points_account_activity(self, company):
        """Aktivitas To-Do untuk Accounting Manager company di jurnal Poin EFN (sekali per company per hari, kunci
        ``ir.config_parameter`` ``efn_online.points_account_alert.<company_id>`` = tanggal). True bila dibuat."""
        params = self.env["ir.config_parameter"].sudo()
        key = f"{POINTS_ALERT_PARAM}.{company.id}"
        today = fields.Date.to_string(fields.Date.context_today(self))
        if params.get_param(key) == today:
            return False
        params.set_param(key, today)
        journal = loyalty.points_journal_raw(self.env, company)
        group = self.env.ref("account.group_account_manager", raise_if_not_found=False)
        user = self.env["res.users"]
        if group:
            user = (
                self.env["res.users"]
                .sudo()
                .search(
                    [
                        ("all_group_ids", "in", group.ids),
                        ("company_ids", "in", company.ids),
                        ("share", "=", False),
                        ("id", "!=", self.env.ref("base.user_root").id),
                    ],
                    order="id",
                    limit=1,
                )
            )
        if not journal or not user:
            _logger.error(
                "EFN online: aktivitas Finance Poin EFN company #%s tidak dapat dijadwalkan",
                company.id,
            )
            return False
        journal.sudo().activity_schedule(
            "mail.mail_activity_data_todo",
            user_id=user.id,
            summary=_("Akun Poin EFN belum lengkap"),
            note=_(
                "Penukaran Poin EFN pesanan online ditolak: isi akun pembayaran (liabilitas/kliring Poin EFN) di baris "
                "metode masuk/keluar jurnal ini dan akun liabilitas metode POS Poin EFN."
            ),
        )
        return True

    def _efn_place_sale_order(
        self,
        channel,
        outlet,
        fulfilment,
        product_qty,
        promo_codes,
        quote_hash,
        points_payment=None,
    ):
        """SO dari cart + promo (E-36) -> cek ``quote_hash`` -> (mode confirm) konfirmasi + reservasi penuh (E-35)."""
        self.ensure_one()
        usable, programs, engine = channel._efn_online_promo_plan(promo_codes, True)
        if engine:
            # L-40 / SEC-130: kunci baris program berkuota SEBELUM SO dibuat & kuota dihitung (place paralel ->
            # SerializationFailure -> Odoo mengulang request). Tidak ditangkap.
            channel._efn_online_lock_quota_programs(usable, programs)
        sale_order = channel._efn_online_create_sale_order(
            outlet,
            product_qty,
            {
                "client_order_ref": self.platform_order_ref,
                "origin": f"Super-App {self.checkout_ref}",
                "efn_online_order_id": self.id,
            },
        )
        self.write({"sale_order_id": sale_order.id})
        applied = channel._efn_online_apply_promotions(sale_order, usable, programs, outlet) if engine else []
        stored = channel._efn_online_summarize(sale_order, outlet, fulfilment, promo_applied=applied)
        if stored["quote_hash"] != quote_hash:
            # Hitungan SO tersimpan beda dengan quote (aturan harga berubah, promo berakhir/kuota habis, kupon sudah
            # terpakai checkout lain): savepoint place di-rollback, BFF meminta quote ulang.
            raise online_error("price_changed")
        if channel.max_order_amount and stored["amount_total"] > channel.max_order_amount:
            raise online_error("invalid_input")
        if stored.pop("_weight_issues", None):
            # W-33 / E-35b (docs/04 §12.3): quote yang sama sudah membawa issue berat -> BFF tidak boleh place.
            raise online_error("invalid_input")
        if channel.stock_hold_mode == "confirm":
            try:
                self._efn_confirm_sale_order()
            except _ReviewNeeded as review:
                # Belum ada pembayaran -> bukan `review`: total berubah / kupon ditolak saat konfirmasi = harga berubah.
                _logger.warning(
                    "EFN online order %s: konfirmasi saat place gagal (%s)",
                    self.id,
                    review.code,
                )
                raise online_error("price_changed") from None
            if self._efn_reservation_short(assign=True):
                raise online_error("out_of_stock")
        values = {
            "amount_total": stored["amount_total"],
            "amount_payable": stored["amount_payable"],
        }
        if points_payment:
            self._efn_check_points_payment(channel, points_payment, stored["amount_payable"])
            values.update(
                points_redeemed=points_payment["points"],
                points_value=points_payment["value_idr"],
            )
        self.write(values)

    # ================================================================ BFF: mark paid
    @api.model
    def efn_online_mark_paid(self, channel_code, platform_order_ref, payment):
        """Catat pembayaran -> cek nominal & stok -> konfirmasi -> faktur + pembayaran titipan (§10.4.3 no. 6)."""
        channel = self._efn_channel_env(channel_code)
        require(isinstance(payment, dict) and PAYMENT_KEYS <= set(payment) <= PAYMENT_KEYS | PAYMENT_OPTIONAL_KEYS)
        payment_ref = check_str(payment["payment_ref"], OPAQUE_REF_RE)
        key = check_str(payment["idempotency_key"], IDEMPOTENCY_KEY_RE)
        amount_paid = check_amount_int(payment["amount_paid"])
        paid_at = parse_iso_utc(payment["paid_at"])
        points_value = check_amount_int(payment.get("points_value", 0))
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            raise online_error("not_found")
        if order.payment_mode in INTERNAL_PAYMENT_MODES:
            # E-45 (docs/04 §12.2 no. 2/6): bayar di kasir hanya dilunasi lewat sync POS. G26 (§17.15): transfer bank
            # hanya lewat persetujuan Verifikator (efn_online_transfer). Keduanya jalur internal, bukan BFF.
            raise online_error("invalid_state")
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
        return order._efn_apply_payment(payment_ref, key, amount_paid, paid_at, points_value=points_value)

    def _efn_apply_payment(self, payment_ref, key, amount_paid, paid_at, points_value=None):
        """Langkah bersama BFF ``mark_paid`` dan jalur internal bayar di kasir (E-45): order ``placed`` terkunci
        -> ``paid`` + event -> nominal -> stok/reservasi -> konfirmasi -> faktur + pembayaran -> ``confirmed``.

        G22 W-12: ``amount_paid`` dibandingkan dengan porsi TUNAI ``amount_due``; ``points_value`` (BFF) harus sama
        dengan nilai saat place (``None`` = jalur kasir, memakai nilai tersimpan). Beda -> ``review``."""
        order = self
        order.ensure_one()
        channel = order.channel_id
        order.write(
            {
                "state": "paid",
                "payment_ref": payment_ref,
                "paid_idempotency_key": key,
                "paid_amount": amount_paid,
                "paid_at": paid_at,
                "counter_code_hash": False,
            }
        )
        order._efn_emit("paid")
        rounding = order.currency_id.rounding
        if points_value is not None and float_compare(points_value, order.points_value, precision_rounding=rounding):
            order._efn_set_review("points_mismatch")
            return order._efn_paid_result()
        if float_compare(amount_paid, order.amount_due, precision_rounding=rounding) != 0:
            order._efn_set_review("amount_mismatch")
            return order._efn_paid_result()
        # E-35: SO yang sudah dikonfirmasi saat place (mode confirm) -> cek reservasinya, bukan free_qty (reservasi
        # sendiri mengurangi free_qty). Berbasis state SO, bukan mode kanal saat ini (mode bisa diubah di tengah).
        reserved_flow = order.sale_order_id.state == "sale"
        short = order._efn_reservation_short(assign=True) if reserved_flow else order._efn_short_lines()
        if short:
            order.write({"state": "stock_short", "short_lines": json.dumps(short)})
            order._efn_emit("stock_short")
            return order._efn_paid_result()
        if reserved_flow:
            # Konfirmasi ulang dilewati; jaring #37 tetap: total SO harus sama dengan saat place.
            if order.currency_id.compare_amounts(order.sale_order_id.amount_total, order.amount_total) != 0:
                order._efn_set_review("total_changed_on_confirm")
                return order._efn_paid_result()
        else:
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
        order._efn_after_confirmed()
        return order._efn_paid_result()

    # ---------------------------------------------------- hook jembatan (G22 E-29a)
    def _efn_after_confirmed(self):
        """Dipanggil setelah order ``confirmed`` (sudah dibayar). No-op; ``efn_online_kds_bridge`` membuat tiket KDS.
        Kegagalan hook tidak boleh membatalkan pembayaran (jembatan menangkap & memberi alert)."""
        return

    def _efn_after_cancelled(self):
        """Dipanggil setelah order ``cancelled``. No-op; jembatan KDS membatalkan tiket aktif."""
        return

    def _efn_after_refund(self, refund):
        """Dipanggil setelah refund (nota kredit) dibuat. No-op; jembatan KDS membatalkan/mengurangi tiket."""
        return

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

    def _efn_reservation_moves(self):
        """Move storable SO yang menarik stok gudang (kepala rantai pick/pack/ship; 1 langkah = move pelanggan),
        belum selesai/batal. Reservasi online = move ini ``assigned``."""
        self.ensure_one()
        Move = self.env["stock.move"]
        moves = self.sale_order_id.order_line.move_ids.filtered(lambda m: m.state != "cancel")
        chain = moves
        todo = moves
        while todo:
            todo = todo.move_orig_ids.filtered(lambda m: m.state != "cancel") - chain
            chain |= todo
        heads = Move.browse()
        for move in chain:
            if move.state == "done" or not move.product_id.is_storable:
                continue
            if move.move_orig_ids.filtered(lambda m: m.state not in ("cancel", "done")):
                continue
            heads |= move
        return heads

    def _efn_reservation_short(self, assign=False):
        """E-35: [{product_id, requested, available}] untuk move yang reservasinya tidak penuh (dicabut penjualan POS
        lewat ``_free_reservation``, atau tipe operasi tidak ``at_confirm``). ``assign`` -> coba ``_action_assign``
        dulu (jalur internal ``CONFIRM_SCOPE``)."""
        self.ensure_one()
        moves = self._efn_reservation_moves()
        pending = moves.filtered(lambda m: m.state != "assigned")
        if pending and assign:
            with internal_scope(self.env, CONFIRM_SCOPE, self.sale_order_id.ids):
                pending._action_assign()
            pending = moves.filtered(lambda m: m.state != "assigned")
        short = {}
        for move in pending:
            entry = short.setdefault(
                move.product_id.id,
                {"product_id": move.product_id.id, "requested": 0.0, "available": 0.0},
            )
            entry["requested"] += move.product_uom_qty
            entry["available"] += max(move.quantity, 0.0)
        return [short[pid] for pid in sorted(short)]

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
            _logger.warning(
                "EFN online order %s: konfirmasi SO gagal (%s)",
                self.id,
                type(exc).__name__,
            )
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
                if self.currency_id.compare_amounts(self.points_value, 0.0) > 0:
                    # G22 W-12 (docs/04 §14.C.3 "Mark paid"): porsi Poin EFN = pembayaran kedua di jurnal Poin EFN PT.
                    journal = loyalty.points_journal(self.env, self.company_id)
                    if not journal:
                        raise _ReviewNeeded("points_journal_missing")
                    self._efn_register_payment(invoices, self.points_value, journal=journal)
                self.write({"invoice_id": invoices.id})
        except _ReviewNeeded:
            self.env.invalidate_all()
            raise
        except (UserError, ValidationError) as exc:
            self.env.invalidate_all()
            _logger.warning(
                "EFN online order %s: faktur/pembayaran gagal (%s)",
                self.id,
                type(exc).__name__,
            )
            raise _ReviewNeeded("invoice_failed") from None

    def _efn_payment_journal(self):
        """Jurnal pembayaran faktur/refund. QRIS = jurnal titipan kanal (ADR-019 A1). Bayar di kasir (E-45, L-55):
        jurnal kliring PT penjual — di-override ``efn_online_pos_counter``; tanpa modul itu -> kosong -> ``review``
        (tidak pernah jatuh diam-diam ke titipan)."""
        self.ensure_one()
        if self.payment_mode in INTERNAL_PAYMENT_MODES:
            # counter -> efn_online_pos_counter; bank_transfer -> efn_online_transfer (jurnal bank kanal, G26 §17.6).
            return self.env["account.journal"]
        return self.channel_id.journal_titipan_id

    def _efn_register_payment(self, moves, amount, journal=None):
        """Satu-satunya titik pencatatan pembayaran (ADR-019: A2/opsi C cukup mengubah metode ini).

        ``journal`` eksplisit hanya untuk porsi Poin EFN (G22 W-12); default = jurnal tunai pesanan."""
        self.ensure_one()
        journal = journal or self._efn_payment_journal()
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
            result["refund"] = {
                "refund_ref": refund.refund_ref,
                "amount": self._efn_amount_out(refund.amount),
                # G22 W-12 (docs/04 §14.C.4): alokasi Poin EFN / tunai / dasar clawback.
                **refund._efn_loyalty_block(),
            }
        return result

    def _efn_do_cancel(self, reason, initiator, idempotency_key=None, cancel_sale_order=True):
        """Batalkan SO (+ nota kredit penuh bila faktur sudah di-post DAN pernah dibayar). Kembalikan refund (atau
        kosong).

        L-43 / SEC-139: order yang tidak pernah dibayar (tanpa ``paid_idempotency_key``) tidak pernah menghasilkan
        refund/nota kredit, walau ada faktur ter-posting (mis. dibuat manual sebelum guard ``_prepare_invoice``):
        tidak ada dana untuk dikembalikan. Faktur seperti itu = anomali -> aktivitas Accounting (reversal manual)."""
        self.ensure_one()
        refund = self.env["efn.online.refund"]
        invoice = self._efn_posted_invoice()
        if invoice and not self.sudo().paid_idempotency_key:
            _logger.warning(
                "EFN online order %s: faktur ter-posting tanpa pembayaran saat batal (tanpa refund)",
                self.id,
            )
            self._efn_schedule_activity(
                _("Faktur pesanan online tanpa pembayaran"),
                "unpaid_invoice_on_cancel",
                "account.group_account_invoice",
            )
            invoice = self.env["account.move"]
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
        self._efn_after_cancelled()
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
            {
                "order": order.id,
                "full": full,
                "lines": lines,
                "restock": restock,
                "reason": refund["reason"],
            }
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
            .search(
                [
                    ("refund_ref", "=", refund_ref.lower()),
                    ("order_id.channel_id", "=", channel.id),
                ],
                limit=1,
            )
        )
        if not record:
            raise online_error("not_found")
        if record.order_id.payment_mode == "counter":
            # E-45 (§12.2 no. 8): dana refund pesanan bayar di kasir keluar lewat refund order POS pelunasan; settle
            # dicatat sync POS, bukan BFF (BFF tidak memanggil gateway untuk order counter).
            raise online_error("invalid_state")
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
                    # G22 W-12 (docs/04 §14.C.4): dasar earn untuk rekonsiliasi (null sebelum diambil).
                    "eligible_amount": order.eligible_amount if order.state == "picked_up" else None,
                }
            )
        return result

    # ============================================== BFF: faktur atas nama pembeli (E-39)
    @api.model
    def efn_online_set_invoice_party(self, channel_code, platform_order_ref, profile, idempotency_key):
        """Pasang partner pembeli terlindungi sebagai ``partner_invoice_id`` SO (docs/04 §13.5, L-63).

        Urutan: cek pemanggil -> validasi input (``invalid_input``) -> order kanal (``not_found``) -> ketersediaan
        (PT PPN + ``efn_pii`` aktif; selain itu ``invoice_not_available``) -> idempotensi -> state (hanya ``placed``,
        sebelum faktur; selain itu ``invalid_state``). Hasil ``{"state": "set"}`` tanpa data pribadi. Profil tidak
        pernah di-log / di-hash / masuk outbox. ``partner_id`` SO tetap partner generik kanal (minimisasi)."""
        channel = self._efn_channel_env(channel_code)
        key = check_str(idempotency_key, IDEMPOTENCY_KEY_RE)
        buyer = invoice_profile_payload(profile)
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            raise online_error("not_found")
        if not order._efn_invoice_party_available(buyer["buyer_type"]):
            raise online_error("invoice_not_available")
        sale_order = order.sale_order_id
        if order.invoice_party_idempotency_key:
            if order.invoice_party_idempotency_key == key and order._efn_invoice_party_matches(
                sale_order.partner_invoice_id, buyer
            ):
                return {"state": "set"}
            raise online_error("idempotency_conflict")
        if order.state != "placed" or not sale_order or order.invoice_id or sale_order.invoice_ids:
            raise online_error("invalid_state")
        partner, reused = order._efn_invoice_party_partner(buyer)
        sale_order.write({"partner_invoice_id": partner.id})
        order.write({"invoice_party_idempotency_key": key})
        _logger.info(
            "EFN online order %s: invoice party set (type=%s, partner=%s, reused=%s)",
            order.id,
            buyer["buyer_type"],
            partner.id,
            reused,
        )
        return {"state": "set"}

    @api.model
    def _efn_pii_encrypted_field(self, model, fname):
        """True bila ``model._fields[fname]`` memakai kelas field terenkripsi efn_pii (``EfnPiiChar``). Dicek lewat
        nama kelas di MRO agar modul ini tidak bergantung (depends) pada efn_pii (opsional, L-59)."""
        field = model._fields.get(fname)
        return bool(field) and any(klass.__name__ == "EfnPiiChar" for klass in type(field).__mro__)

    def _efn_invoice_party_available(self, buyer_type):
        """L-63 + §13.5: hanya PT PPN (jenis usaha ritel: GL, BCR) dan hanya bila efn_pii terpasang dengan SEMUA
        field yang akan menerima profil benar-benar terenkripsi (fail-closed: modul lain yang mendefinisikan ulang
        field, mis. ``base_vat`` tanpa ``efn_pii_base_vat`` -> tidak tersedia)."""
        self.ensure_one()
        if self.company_id.efn_business_type != "retail":
            return False
        Partner = self.env["res.partner"]
        if INVOICE_PARTY_PROTECTED_FIELD not in Partner._fields:
            return False
        fnames = INVOICE_PARTY_PII_FIELDS + (INVOICE_PARTY_DOC_FIELDS if buyer_type != "npwp" else ())
        return all(self._efn_pii_encrypted_field(Partner, fname) for fname in fnames)

    @api.model
    def _efn_invoice_party_matches(self, partner, buyer):
        """Partner pembeli (hasil efn_online_set_invoice_party) cocok persis dengan profil ternormalisasi."""
        if not partner or len(partner) != 1:
            return False
        partner = partner.sudo()
        if not partner.efn_online_invoice_buyer:
            return False
        same = (
            (partner.name or "") == buyer["name"]
            and (partner.street or "") == buyer["address"]
            and (partner.email or "") == buyer["email"]
        )
        if not same:
            return False
        if buyer["buyer_type"] == "npwp":
            # base_vat dapat memformat ulang NPWP (prefiks/pemisah) -> bandingkan digit saja.
            return "".join(ch for ch in (partner.vat or "") if ch.isdigit()) == buyer["tax_id"]
        return (
            "l10n_id_buyer_document_number" in partner._fields
            and (partner.l10n_id_buyer_document_number or "") == buyer["tax_id"]
            and partner.l10n_id_buyer_document_type == BUYER_DOCUMENT_TYPES[buyer["buyer_type"]]
        )

    def _efn_invoice_party_partner(self, buyer):
        """Cari-atau-buat partner pembeli perorangan per company. Pencarian ``vat =`` / nomor dokumen ``=`` ditulis
        ulang efn_pii menjadi blind index (partner terlindungi); kandidat dicocokkan persis di Python (nama, alamat,
        email terdekripsi). Hanya partner bertanda ``efn_online_invoice_buyer`` milik company ini yang dipakai ulang
        (tidak pernah menempelkan faktur ke vendor/kontak lain dengan NPWP sama). Returns (partner, reused)."""
        self.ensure_one()
        company = self.company_id
        # Pembuatan pembeli diserialkan per company (kunci tanpa PII) -> tidak ada duplikat dari panggilan paralel.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"efn_online_invoice_buyer:{company.id}",),
        )
        Partner = self.env["res.partner"].sudo().with_company(company)
        domain = [
            ("efn_online_invoice_buyer", "=", True),
            ("company_id", "=", company.id),
            ("parent_id", "=", False),
            ("is_company", "=", False),
        ]
        # Operator `in` = bentuk yang ditulis ulang efn_pii ke blind index (`=` dinormalisasi Domain ke `in`).
        if buyer["buyer_type"] == "npwp":
            domain.append(("vat", "in", [buyer["tax_id"]]))
        else:
            domain.append(("l10n_id_buyer_document_number", "in", [buyer["tax_id"]]))
        for candidate in Partner.search(domain, order="id", limit=INVOICE_PARTY_MAX_CANDIDATES):
            if self._efn_invoice_party_matches(candidate, buyer):
                return candidate, True
        vals = {
            "name": buyer["name"],
            "street": buyer["address"],
            "email": buyer["email"] or False,
            "is_company": False,
            "type": "contact",
            "company_id": company.id,
            "efn_online_invoice_buyer": True,
        }
        country = self.env.ref("base.id", raise_if_not_found=False)
        if country and buyer["buyer_type"] != "passport":
            vals["country_id"] = country.id
        has_doc_fields = "l10n_id_buyer_document_type" in Partner._fields
        if buyer["buyer_type"] == "npwp":
            vals["vat"] = buyer["tax_id"]
            if has_doc_fields:
                vals["l10n_id_buyer_document_type"] = BUYER_DOCUMENT_TYPES["npwp"]
        else:
            require(has_doc_fields, "invoice_not_available")
            vals["l10n_id_buyer_document_type"] = BUYER_DOCUMENT_TYPES[buyer["buyer_type"]]
            vals["l10n_id_buyer_document_number"] = buyer["tax_id"]
        try:
            with self.env.cr.savepoint():
                partner = Partner.create(vals)
                partner.flush_recordset()
                if not partner[INVOICE_PARTY_PROTECTED_FIELD]:
                    # Aturan protect-by-default efn_pii tidak berlaku (mis. dikustom) -> jangan simpan profil.
                    raise online_error("invoice_not_available")
        except ValidationError:
            # mis. base_vat menolak format NPWP. Pesan Odoo memuat nilai -> tidak di-log, tidak diteruskan.
            _logger.warning(
                "EFN online order %s: invoice party rejected by partner validation",
                self.id,
            )
            raise online_error("invalid_input") from None
        except UserError as exc:
            if str(exc) == "efn_online:invoice_not_available":
                raise
            # efn_pii mode terkunci (kunci PII tidak tersedia) / SEC-137 fail-closed -> tidak tersedia.
            _logger.warning(
                "EFN online order %s: invoice party unavailable (%s)",
                self.id,
                type(exc).__name__,
            )
            raise online_error("invoice_not_available") from None
        return partner, False

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
            order._efn_set_ready("manual")
        return True

    def _efn_set_ready(self, source):
        """``confirmed`` -> ``ready`` + event ``ready`` (``ready_source``: ``manual`` staf | ``kds`` semua tiket dapur
        siap, docs/04 §14.B B-6/B-7a). Pemanggil sudah mengunci & memeriksa state (jalur internal, sudo)."""
        self.ensure_one()
        self._efn_mark_reserved_picked()
        self.write(
            {
                "state": "ready",
                "ready_at": fields.Datetime.now(),
                "ready_source": source,
            }
        )
        self._efn_emit("ready")

    def _efn_set_kitchen_started(self, started_at=None):
        """G22 B-7b: tiket dapur pertama mulai dikerjakan -> ``kitchen_started_at`` + event opsional ``preparing``
        (sekali per order, hanya saat ``confirmed``; tanpa transisi state). True bila event diantrikan."""
        self.ensure_one()
        if self.state != "confirmed" or self.kitchen_started_at:
            return False
        self.write({"kitchen_started_at": started_at or fields.Datetime.now()})
        self._efn_emit("preparing")
        return True

    # ---------------------------------------------------------- Poin EFN (G22 W-12)
    def _efn_loyalty_factor(self):
        """K-18-4: porsi uang = 1 − nilai Poin EFN ÷ amount_payable (incl. pajak), 0..1; 1 bila ``earn_on_money_only``
        kebijakan PT mati atau tanpa poin."""
        self.ensure_one()
        payable = self.amount_payable or 0.0
        points = self.points_value or 0.0
        if points <= 0 or payable <= 0 or not loyalty.earn_on_money_only(self.env, self.company_id):
            return 1.0
        return max(0.0, min(1.0, 1.0 - points / payable))

    def _efn_compute_mgm_basis(self, at):
        """G31b: ``{efn_mgm_eligible_amount, efn_mgm_basis_set}`` bila kebijakan MGM PT pada ``at`` punya pengecualian
        kategori; selain itu ``{}`` (field tidak dikirim)."""
        self.ensure_one()
        mgm_excluded = loyalty.mgm_excluded_category_ids(self.env, self.company_id, at)
        if not mgm_excluded:
            return {}
        excluded = loyalty.excluded_category_ids(self.env) | mgm_excluded
        so_lines = self.sale_order_id.order_line.filtered(lambda line: not line.display_type and line.product_id)
        gross = loyalty.eligible_subtotal(so_lines, excluded)
        refunded = 0.0
        for refund in self.refund_ids.filtered(lambda r: r.state != "failed" and r.credit_note_id):
            refunded += refund._efn_credit_eligible_subtotal(excluded)
        return {
            "efn_mgm_eligible_amount": floor_idr(max(gross - refunded, 0.0) * self._efn_loyalty_factor()),
            "efn_mgm_basis_set": True,
        }

    def _efn_compute_eligible_amount(self):
        """K-1/K-2/K-18-4: Σ subtotal tanpa pajak baris SO berhak poin (kategori ``efn_loyalty_earn_excluded``
        dikecualikan; baris reward promo ikut mengurangi) − baris nota kredit refund yang tidak gagal, × porsi uang,
        dibulatkan ke bawah. Refund sebelum diambil sudah neto di sini (tanpa clawback, §14.C.3 "Clawback")."""
        self.ensure_one()
        excluded = loyalty.excluded_category_ids(self.env)
        so_lines = self.sale_order_id.order_line.filtered(lambda line: not line.display_type and line.product_id)
        gross = loyalty.eligible_subtotal(so_lines, excluded)
        refunded = 0.0
        for refund in self.refund_ids.filtered(lambda r: r.state != "failed" and r.credit_note_id):
            refunded += refund._efn_credit_eligible_subtotal(excluded)
        return floor_idr(max(gross - refunded, 0.0) * self._efn_loyalty_factor())

    def _efn_paid_total(self):
        """Total nilai yang diterima bagian ini (tunai + Poin EFN); belum dibayar -> ``amount_payable``."""
        self.ensure_one()
        cash = self.paid_amount if self.sudo().paid_idempotency_key else self.amount_due
        return (cash or 0.0) + (self.points_value or 0.0)

    def _efn_mark_reserved_picked(self):
        """E-35 no. 4: barang sudah disisihkan -> move tercadang ``picked=True``; ``_free_reservation`` (penjualan
        POS/stok keluar lain) tidak mencabut baris picked. Barang belum keluar: tetap lewat serah terima (SEC-105)."""
        self.ensure_one()
        if self.sale_order_id.state != "sale":
            return
        moves = self._efn_reservation_moves().filtered(lambda m: m.state == "assigned" and not m.picked)
        if moves:
            with internal_scope(self.env, CONFIRM_SCOPE, self.sale_order_id.ids):
                moves.write({"picked": True})

    def action_efn_retry_events(self):
        """Manajer penjualan: kirim ulang event outbox yang gagal untuk pesanan ini."""
        self._efn_check_staff("sales_team.group_sale_manager")
        outbox = self.sudo().event_ids.filtered(lambda e: e.state == "failed")
        outbox.write(
            {
                "state": "pending",
                "next_attempt_at": fields.Datetime.now(),
                "last_error": False,
            }
        )
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
            _logger.warning(
                "EFN online order %s: kode ambil salah (percobaan %d)",
                self.id,
                attempts,
            )
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
                    # E-35: sebagian move sudah `picked` sejak "siap" -> tandai semua agar validasi tidak memisahkan
                    # move lain ke backorder (yang dibatalkan karena tanpa backorder).
                    if not move.picked:
                        move.picked = True
                result = picking.with_context(
                    skip_backorder=True, picking_ids_not_to_backorder=picking.ids
                ).button_validate()
                if result is not True and picking.state != "done":
                    raise UserError(
                        _(
                            "Pengiriman %(name)s tidak dapat divalidasi otomatis.",
                            name=picking.name,
                        )
                    )
        if any(m.state != "done" for m in customer_moves):
            raise UserError(_("Sebagian barang pesanan ini belum dapat dikeluarkan."))
        now = fields.Datetime.now()
        self.write(
            {
                "state": "picked_up",
                "picked_up_at": now,
                "pickup_attempts": 0,
                # G22 W-12 (§14.C.3 "Earn"): dasar earn dibekukan saat diambil (event picked_up + status).
                "eligible_amount": self._efn_compute_eligible_amount(),
                # G31b §20.10: dasar MGM ikut dibekukan (hanya bila kebijakan MGM PT punya pengecualian kategori)
                **self._efn_compute_mgm_basis(now),
            }
        )
        self._efn_clear_secret()
        self._efn_emit("picked_up")
        _logger.info("EFN online order %s picked_up", self.id)
        return True, _("Pesanan %(name)s diserahkan.", name=self.name)

    # ======================================================================== cron
    @api.model
    def _cron_efn_online_expire(self, limit=200):
        """Draf lewat ``expires_at`` (batas bayar Odoo) -> batal + event ``cancelled`` (docs/08 §8.3 D.7)."""
        orders = self.sudo().search(
            [("state", "=", "placed"), ("expires_at", "<", fields.Datetime.now())],
            order="id",
            limit=limit,
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
                    _("Pesanan online belum diambil"),
                    "not_picked_up",
                    "stock.group_stock_user",
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
                _("Kode ambil pesanan online kedaluwarsa"),
                "pickup_code_expired",
                "stock.group_stock_user",
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
                _logger.warning(
                    "EFN online order %s (berbayar) dibatalkan ops oleh uid=%s",
                    order.id,
                    actor.id,
                )

    @api.model
    def _efn_new_refund_ref(self):
        return str(uuid.uuid4())
