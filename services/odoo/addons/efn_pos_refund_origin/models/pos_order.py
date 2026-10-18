"""E-31: refund POS wajib ke metode bayar asal (docs/08 §9.3, W-24; default L-23).

CE 19 tidak menegakkan ini: layar bayar refund menampilkan semua metode dan server menerima metode apa pun.
Validasi di sini berjalan saat order disinkron (`_process_saved_order`, tidak draft) dan menilai pembayaran
NEGATIF (uang keluar) order:

- **Refund ber-struk** (baris ber-`refunded_orderline_id`, satu order asal — core 19 menolak > 1): setiap metode
  pembayaran negatif harus ada di order asal (subset), dan nominal per metode ≤ dibayar di order asal (kas:
  dikurangi kembalian) − refund sebelumnya (order refund lain yang tersinkron). Alokasi default proporsional
  dihitung server (`_efn_refund_allocation`, dipakai frontend). Metode yang tidak bisa dibalik di kanalnya
  (QRIS statis, gateway tanpa reversal) tetap dicatat atas metode asal; pengembalian dana fisik = transfer manual
  SOP Finance (Q-F18-6, L-23).
- **Order campuran** (baris ber-struk + barang qty < 0 tanpa asal): ditolak di semua mode (SEC-147, 19.0.1.4.0).
- **Retur tanpa struk** (barang qty < 0 tanpa order asal): hanya tunai (Q-F18-5, L-23);
  `efn.pos.refund.unlinked_return_mode` = ``manager_approval`` (default E-41, 18-15: wajib persetujuan Store
  Manager terverifikasi server, `efn.pos.return.approval` sekali pakai) | ``cash`` | ``deny`` (ditolak).
- Pelanggaran -> `UserError` (sync ditolak; kasir memperbaiki pembayaran). Jenis order ditentukan server dari
  baris (bukan flag klien `is_refund`, pola SEC-59).

Integritas order POS (re-review G18, berlaku untuk semua modul efn POS; modul ini `auto_install` dan menjadi
dependency `efn_pos_loyalty_ledger`/`efn_pos_otp_gate`):

- **SEC-125:** order hanya boleh berpindah dari draft/cancel ke paid/done/invoiced di dalam
  `_process_saved_order` (sync POS atau wizard pembayaran backend `pos.make.payment`), tempat seluruh validasi
  E-30 (Poin EFN), E-31 (metode asal), SEC-116 (`is_change`) dan gerbang OTP promo berjalan. Jalur lain
  (`action_pos_order_paid` lewat RPC, `write({'state': 'paid'})`, `create` order ber-state paid di luar sync)
  ditolak untuk non-superuser. `amount_paid` dihitung ulang dari pembayaran nyata sebelum validasi.
- **SEC-126:** penanda sync membawa token order yang sedang disinkron (id order lama / uuid order baru).
  Perintah `payment_ids`/`lines` payload yang menunjuk record milik order lain (`[1|2|3|4, id]`, `[6, 0, ids]`)
  ditolak sebelum diproses core; guard `pos.payment` memeriksa kepemilikan record yang disentuh.
- **SEC-127:** order asal "disentuh" (`UPDATE ... SET write_date = write_date`) sebelum sisa per metode dihitung.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.scope import (
    POS_ORDER_SAVE_SCOPE,
    POS_PAYMENT_SYNC_SCOPE,
    internal_scope,
    scope_tokens,
    touch_row,
)

_logger = logging.getLogger(__name__)

UNLINKED_MODE_PARAM = "efn.pos.refund.unlinked_return_mode"
# E-41 (18-15, docs/04 §12.4): `manager_approval` = default baru; nilai tidak dikenal juga -> manager_approval.
UNLINKED_MODES = ("manager_approval", "cash", "deny")
DEFAULT_UNLINKED_MODE = "manager_approval"
PAID_STATES = ("paid", "done", "invoiced")
OPEN_STATES = ("draft", "cancel")
# Field x2many payload sync yang diperiksa kepemilikannya (SEC-126).
GUARDED_X2M = ("payment_ids", "lines")


class PosOrder(models.Model):
    _inherit = "pos.order"

    # ------------------------------------------------------------- helpers
    def _efn_refund_origins(self):
        self.ensure_one()
        return self.lines.sudo().refunded_orderline_id.order_id

    def _efn_paid_by_method(self):
        """{metode: nominal dibayar bersih} order asal. Kembalian dicatat core 19 sebagai pembayaran tunai negatif
        (`is_change`), jadi jumlah bersih per metode sudah mengurangi kembalian."""
        self.ensure_one()
        paid = {}
        for payment in self.payment_ids.sudo():
            paid[payment.payment_method_id] = paid.get(payment.payment_method_id, 0.0) + payment.amount
        return {method: amount for method, amount in paid.items() if self.currency_id.compare_amounts(amount, 0.0) > 0}

    def _efn_refunded_by_method(self, exclude=None):
        """{metode: nominal sudah dikembalikan (positif)} lewat order refund lain yang tersinkron."""
        self.ensure_one()
        domain = [("lines.refunded_orderline_id.order_id", "=", self.id), ("state", "in", PAID_STATES)]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        refunded = {}
        for order in self.sudo().search(domain):
            for payment in order.payment_ids.filtered(lambda p: p.amount < 0):
                refunded[payment.payment_method_id] = refunded.get(payment.payment_method_id, 0.0) - payment.amount
        return refunded

    def _efn_refund_allocation(self, refund_amount, exclude=None):
        """Alokasi proporsional (kumulatif) `refund_amount` (positif) ke metode order asal `self`.

        :returns: list ``[(metode, nominal_alokasi, sisa_maksimum)]`` urut nominal dibayar terbesar.
            nominal_m = round((refund sebelumnya + refund ini) × dibayar_m ÷ total) − refund_m sebelumnya, dibatasi
            0..sisa; selisih pembulatan masuk metode dengan sisa terbesar (tunai lebih dulu).
        """
        self.ensure_one()
        currency = self.currency_id
        paid = self._efn_paid_by_method()
        refunded = self._efn_refunded_by_method(exclude=exclude)
        total = sum(paid.values())
        if total <= 0 or refund_amount <= 0:
            return [(method, 0.0, max(amount - refunded.get(method, 0.0), 0.0)) for method, amount in paid.items()]
        previous = sum(refunded.get(method, 0.0) for method in paid)
        cumulative = min(previous + refund_amount, total)
        rows = []
        for method, amount in sorted(paid.items(), key=lambda item: -item[1]):
            remaining = max(amount - refunded.get(method, 0.0), 0.0)
            share = currency.round(cumulative * amount / total) - refunded.get(method, 0.0)
            rows.append([method, max(0.0, min(currency.round(share), remaining)), remaining])
        diff = currency.round(min(refund_amount, sum(r[2] for r in rows)) - sum(r[1] for r in rows))
        if not currency.is_zero(diff):
            for row in sorted(rows, key=lambda r: (not r[0].is_cash_count, -(r[2] - r[1]))):
                room = row[2] - row[1] if diff > 0 else row[1]
                step = min(abs(diff), room)
                row[1] = currency.round(row[1] + (step if diff > 0 else -step))
                diff = currency.round(diff - (step if diff > 0 else -step))
                if currency.is_zero(diff):
                    break
        return [tuple(row) for row in rows]

    @api.model
    def _efn_unlinked_return_mode(self):
        raw = self.env["ir.config_parameter"].sudo().get_param(UNLINKED_MODE_PARAM, DEFAULT_UNLINKED_MODE)
        mode = str(raw or DEFAULT_UNLINKED_MODE).strip().lower()
        return mode if mode in UNLINKED_MODES else DEFAULT_UNLINKED_MODE

    def _efn_consume_return_approval(self, amount):
        """E-41: retur tanpa struk wajib persetujuan Store Manager yang sah untuk order ini (sekali pakai)."""
        self.ensure_one()
        Approval = self.env["efn.pos.return.approval"].sudo()
        approval = Approval.search(
            [
                ("config_id", "=", self.config_id.id),
                ("order_ref", "=", self.uuid),
                ("state", "in", ("approved", "consumed")),
            ],
            order="id desc",
            limit=1,
        )
        if approval:
            # Serialkan dua sync paralel yang membawa uuid yang sama.
            touch_row(self.env.cr, "efn_pos_return_approval", approval.id)
            approval.invalidate_recordset()
        error = approval._efn_usable_error(self, amount)
        if error:
            self._efn_refund_reject(
                f"return_approval_{error}",
                _(
                    "Retur tanpa struk memerlukan persetujuan Store Manager yang sah untuk order ini "
                    "(persetujuan tidak ada, kedaluwarsa, sudah dipakai, kasir/sesi berbeda, atau nominal melebihi "
                    "yang disetujui). Minta persetujuan ulang lalu validasi lagi."
                ),
            )
        if approval.state == "approved":
            approval.write(
                {
                    "state": "consumed",
                    "consumed_at": fields.Datetime.now(),
                    "consumed_order_id": self.id,
                    "amount_used": self.currency_id.round(amount),
                }
            )
            _logger.info("EFN refund origin: order %s memakai persetujuan retur %s", self.uuid, approval.id)

    def _efn_refund_reject(self, code, message):
        _logger.warning("EFN refund origin: order %s ditolak saat sync (%s)", self.uuid, code)
        raise UserError(message)

    # ---------------------------------------------------------- validation
    def _efn_recompute_change_flags(self):
        """SEC-116 (L-25d): `is_change` dari payload browser tidak dipercaya. Sah hanya untuk pembayaran TUNAI
        negatif yang tertutup tender: kumulatif |kembalian| ≤ min(Σ tender tunai positif, Σ tender positif − total
        order hitung server). Selebihnya `is_change` = False sehingga dinilai aturan E-31 sebagai uang keluar."""
        self.ensure_one()
        currency = self.currency_id
        payments = self.payment_ids.sudo()
        flagged = payments.filtered("is_change")
        if not flagged:
            return
        total = sum(self.lines.sudo().mapped("price_subtotal_incl"))
        positive = payments.filtered(lambda p: currency.compare_amounts(p.amount, 0.0) > 0)
        cash_tender = sum(positive.filtered(lambda p: p.payment_method_id.is_cash_count).mapped("amount"))
        allowance = min(cash_tender, sum(positive.mapped("amount")) - total)
        config = self.config_id.sudo()
        if config.cash_rounding and config.rounding_method:
            # Pembulatan tunai (total dibulatkan turun) boleh menambah kembalian maksimal satu satuan pembulatan.
            allowance = min(cash_tender, allowance + config.rounding_method.rounding)
        used = 0.0
        invalid = self.env["pos.payment"]
        for payment in flagged.sorted("id"):
            amount = -payment.amount
            legit = (
                payment.payment_method_id.is_cash_count
                and currency.compare_amounts(amount, 0.0) > 0
                and currency.compare_amounts(used + amount, allowance) <= 0
            )
            if legit:
                used += amount
            else:
                invalid |= payment
        if invalid:
            _logger.warning(
                "EFN refund origin: order %s %d is_change tidak sah dihapus (SEC-116)", self.uuid, len(invalid)
            )
            invalid.write({"is_change": False})

    def _efn_lock_origin(self, origin):
        """SEC-117/SEC-127: serialkan refund paralel atas order asal yang sama sebelum menghitung sisa per metode
        (UPDATE no-op -> transaksi kedua gagal serialisasi dan di-retry Odoo dengan snapshot baru)."""
        touch_row(self.env.cr, "pos_order", origin.id)

    def _efn_refund_origin_validate(self):
        self.ensure_one()
        self._efn_recompute_change_flags()
        outgoing = self.payment_ids.sudo().filtered(
            lambda p: not p.is_change and self.currency_id.compare_amounts(p.amount, 0.0) < 0
        )
        lines = self.lines.sudo()
        origins = self._efn_refund_origins()
        unlinked = lines.filtered(lambda line: line.qty < 0 and not line.refunded_orderline_id)
        if unlinked and origins:
            # SEC-147: order campuran (baris ber-struk + barang qty < 0 tanpa asal) dulu dinilai "refund ber-struk"
            # sehingga E-41 (persetujuan) dan aturan tunai-saja lolos untuk porsi tanpa struk. Ditolak di semua mode;
            # kasir memisahkan: refund dari struk, lalu retur tanpa struk sebagai order sendiri.
            self._efn_refund_reject(
                "mixed_return",
                _(
                    "Refund dari struk dan retur tanpa struk tidak boleh digabung dalam satu order. Selesaikan "
                    "refund dari struk dulu, lalu buat retur tanpa struk sebagai order terpisah."
                ),
            )
        if unlinked and not origins:
            mode = self._efn_unlinked_return_mode()
            if mode == "deny":
                self._efn_refund_reject("unlinked_return_denied", _("Retur tanpa struk tidak diizinkan."))
            non_cash = outgoing.filtered(lambda p: not p.payment_method_id.is_cash_count)
            if non_cash:
                self._efn_refund_reject(
                    "unlinked_return_non_cash",
                    _(
                        "Retur tanpa struk hanya boleh dikembalikan tunai (metode %(name)s ditolak).",
                        name=non_cash[:1].payment_method_id.name,
                    ),
                )
            if mode == "manager_approval":
                # Berlaku juga bila tidak ada uang keluar (tukar barang tanpa struk): nominal 0.
                self._efn_consume_return_approval(-sum(outgoing.mapped("amount")))
            return
        if not origins or not outgoing:
            return
        if len(origins) != 1:
            self._efn_refund_reject("multiple_origins", _("Refund hanya boleh atas satu order asal."))
        origin = origins
        self._efn_lock_origin(origin)
        paid = origin._efn_paid_by_method()
        refunded = origin._efn_refunded_by_method(exclude=self)
        per_method = {}
        for payment in outgoing:
            per_method[payment.payment_method_id] = per_method.get(payment.payment_method_id, 0.0) - payment.amount
        for method, amount in per_method.items():
            if method not in paid:
                self._efn_refund_reject(
                    "method_not_in_origin",
                    _(
                        "Refund harus ke metode bayar asal. Metode %(name)s tidak dipakai di order asal.",
                        name=method.name,
                    ),
                )
            remaining = paid[method] - refunded.get(method, 0.0)
            if self.currency_id.compare_amounts(amount, remaining) > 0:
                self._efn_refund_reject(
                    "exceeds_origin_method",
                    _(
                        "Refund ke %(name)s melebihi nominal yang dibayar dengan metode itu (sisa %(rest)s).",
                        name=method.name,
                        rest=self.currency_id.round(max(remaining, 0.0)),
                    ),
                )

    def _process_saved_order(self, draft):
        self.ensure_one()
        # SEC-125: satu-satunya titik transisi ke paid/done (sync & wizard pembayaran backend memanggil ini).
        with internal_scope(self.env, POS_ORDER_SAVE_SCOPE, {("id", self.id)}):
            if not draft and self.state != "cancel":
                # `amount_paid` bisa ditulis klien (RPC) -> selalu dari pembayaran nyata sebelum cek lunas core.
                paid = self._compute_amount_paid()
                if self.currency_id.compare_amounts(paid, self.amount_paid) != 0:
                    self.write({"amount_paid": paid})
                self._efn_refund_origin_validate()
            elif draft:
                self._efn_recompute_change_flags()
            return super()._process_saved_order(draft)

    def _process_order(self, order, existing_order):
        # SEC-113/SEC-126: jalur sync resmi, terbatas pada order yang sedang disinkron.
        self._efn_check_sync_commands(order, existing_order)
        tokens = set()
        if existing_order:
            tokens.add(("id", existing_order.id))
        if order.get("uuid"):
            tokens.add(("uuid", order["uuid"]))
        with internal_scope(self.env, POS_PAYMENT_SYNC_SCOPE, tokens):
            return super()._process_order(order, existing_order)

    @api.model
    def _efn_check_sync_commands(self, order, existing_order):
        """SEC-126: perintah x2many payload hanya boleh menyentuh record milik order yang disinkron.

        CREATE (0) selalu boleh (inverse dipaksa core ke order ini). UPDATE/DELETE/UNLINK/LINK (1/2/3/4) dan SET
        (6) hanya untuk id yang sudah menjadi milik `existing_order`; order baru tidak punya record lama. CLEAR (5)
        hanya menyentuh milik order sendiri.
        """
        for fname in GUARDED_X2M:
            commands = order.get(fname) or []
            if not isinstance(commands, (list, tuple)):
                self._efn_sync_command_reject(fname, "format")
            own = set(existing_order[fname].ids) if existing_order else set()
            for command in commands:
                if not isinstance(command, (list, tuple)) or not command:
                    self._efn_sync_command_reject(fname, "format")
                op = command[0]
                if op in (0, 5):
                    continue
                if op in (1, 2, 3, 4):
                    ids = [command[1]] if len(command) > 1 else [None]
                elif op == 6:
                    ids = list(command[2] or []) if len(command) > 2 else [None]
                else:
                    self._efn_sync_command_reject(fname, f"op_{op}")
                if any(not isinstance(i, int) or i not in own for i in ids):
                    self._efn_sync_command_reject(fname, f"foreign_{op}")

    @api.model
    def _efn_sync_command_reject(self, fname, code):
        _logger.warning("EFN POS: perintah %s payload sync ditolak (%s, SEC-126)", fname, code)
        raise UserError(_("Data order tidak sah: baris/pembayaran milik order lain tidak dapat diubah."))

    # ------------------------------------------------- SEC-125 transisi state
    def _efn_transition_allowed(self):
        """Order yang sedang divalidasi `_process_saved_order` (penanda per cursor, bukan context)."""
        if self.env.su:
            return True
        tokens = scope_tokens(self.env, POS_ORDER_SAVE_SCOPE)
        return all(("id", order.id) in tokens for order in self)

    def _efn_transition_reject(self):
        _logger.warning(
            "EFN POS: user %s mencoba menandai order %s lunas di luar alur resmi (SEC-125)", self.env.uid, self.ids
        )
        raise UserError(
            _(
                "Order POS hanya bisa dilunasi lewat layar kasir atau tombol Payment (validasi Poin EFN, refund, "
                "dan OTP promo wajib berjalan)."
            )
        )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            tokens = scope_tokens(self.env, POS_PAYMENT_SYNC_SCOPE)
            for vals in vals_list:
                if vals.get("state") in PAID_STATES and ("uuid", vals.get("uuid")) not in tokens:
                    self._efn_transition_reject()
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("state") in PAID_STATES:
            pending = self.filtered(lambda o: o.state in OPEN_STATES)
            if pending and not pending._efn_transition_allowed():
                pending._efn_transition_reject()
        return super().write(vals)
