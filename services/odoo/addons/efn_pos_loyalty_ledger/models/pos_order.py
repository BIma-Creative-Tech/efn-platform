"""Validasi & antrean Poin EFN saat order POS disinkron (docs/08 §6.3 E/H + §9.2 E-30; kontrak docs/04 §9 v6).

G18 (E-30, K-18-1..K-18-5): redeem = **pembayaran** metode "Poin EFN" (`pos.payment.method.efn_is_loyalty_points`),
bukan baris produk diskon. DPP & PPN/PB1 tetap atas harga penuh (P-8). Pembayaran poin dideteksi dari METODE
(SEC-12), bukan flag klien.

Hook (source 19.0): `sync_from_ui` -> `_process_order` -> `_process_saved_order(draft)`; baris & pembayaran sudah
tertulis. Modul ini membungkus `_process_saved_order` milik `efn_pos_otp_gate` (depends), jadi:

- **Draft**: pembayaran Poin EFN tanpa reservasi aktif untuk (config, uuid) dihapus.
- **Paid, jual** (sebelum langkah standar; SEC-62 PREVENTIF, pelanggaran -> `UserError`, sync di-rollback):
  pembayaran Poin EFN tanpa reservasi `reserved`; nominal != `value_idr` reservasi (**toleransi 0**, IDR Rp1
  W-03); pembayaran poin tidak positif; partner != partner reservasi; nilai > `redeem_max_pct` x total order
  incl. pajak (dihitung server); kembalian dari porsi non-tunai (Σ non-tunai > total). Baris produk lama
  "Poin EFN" (desain G13) ditolak.
- **Paid, refund** (jenis ditentukan server, SEC-59): pembayaran Poin EFN hanya boleh negatif, hanya bila order
  asal dibayar poin, dan tidak melebihi sisa poin order asal (preventif). Nilai dibanding alokasi **proporsional**
  kumulatif (Q-F18-4, L-23) -> selisih > Rp1 = reverse `hold` + order `review` (SEC-60, tidak dikirim). Outbox
  `reverse` membawa `points_restored_value` (K-18-5).
- Retur tanpa struk / refund ambigu dengan pembayaran Poin EFN -> `UserError`.
- Reservasi aktif tanpa pembayaran poin -> outbox `redeem_release`. Cocok -> outbox `redeem_commit`
  (`payment_amount` = `value_idr`, K-18-3).
- Earn (K-18-4, default sementara configurable `efn.pos.loyalty.earn_exclude_point_payment`):
  `eligible_amount = Σ subtotal tanpa pajak baris eligible × (1 − bayar_poin ÷ amount_total incl pajak)`.
- Order draft dibatalkan/dihapus dengan reservasi aktif -> outbox `redeem_release`.

Semua panggilan BFF terjadi di cron outbox (bukan di dalam sync), sehingga sync order tidak pernah gagal karena
BFF mati.
"""

import logging
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.models.efn_bff_client import mask_phone, partner_member_ref
from odoo.addons.efn_core.scope import touch_row

_logger = logging.getLogger(__name__)

# Desain lama (G13): produk diskon "Poin EFN". Diarsipkan (migrasi 19.0.2.0.0); baris baru ditolak server.
LEGACY_POINTS_PRODUCT_XMLID = "efn_pos_loyalty_ledger.product_efn_points"
EARN_EXCLUDE_POINTS_PARAM = "efn.pos.loyalty.earn_exclude_point_payment"
_TRUE = {"1", "true", "yes", "y", "on"}
LOYALTY_STATES = [
    ("pending", "Menunggu sinkron poin"),
    ("done", "Poin tersinkron"),
    ("review", "Perlu review"),
]
PAID_STATES = ("paid", "done", "invoiced")


class PosOrder(models.Model):
    _inherit = "pos.order"

    efn_loyalty_state = fields.Selection(
        LOYALTY_STATES, string="Status Poin EFN", copy=False, readonly=True, index="btree_not_null"
    )
    efn_loyalty_points_earned = fields.Integer(string="Poin diperoleh", copy=False, readonly=True)
    efn_loyalty_points_redeemed = fields.Integer(
        string="Poin ditukar",
        copy=False,
        help="Diisi frontend hanya untuk struk; server menimpa dari reservasi saat sync.",
    )
    efn_loyalty_redeem_value = fields.Monetary(
        string="Nilai poin ditukar",
        copy=False,
        help="Diisi frontend hanya untuk struk; server menimpa dari reservasi saat sync.",
    )
    efn_loyalty_note = fields.Char(string="Catatan Poin EFN", copy=False, readonly=True)

    @api.model
    def _load_pos_data_fields(self, config):
        fields_list = super()._load_pos_data_fields(config)
        if fields_list:
            extra = ["efn_loyalty_points_redeemed", "efn_loyalty_redeem_value"]
            fields_list = [*fields_list, *[f for f in extra if f not in fields_list]]
        return fields_list

    # ------------------------------------------------------------- helpers
    @api.model
    def _efn_legacy_points_product(self):
        return self.env.ref(LEGACY_POINTS_PRODUCT_XMLID, raise_if_not_found=False) or self.env["product.product"]

    def _efn_legacy_points_lines(self):
        self.ensure_one()
        product = self._efn_legacy_points_product()
        if not product:
            return self.env["pos.order.line"]
        return self.lines.sudo().filtered(lambda line: line.product_id == product)

    def _efn_points_payments(self):
        """Pembayaran metode Poin EFN (dideteksi dari metode, SEC-12)."""
        self.ensure_one()
        return self.payment_ids.sudo().filtered(lambda pay: pay.payment_method_id.efn_is_loyalty_points)

    def _efn_points_paid(self):
        """Σ nominal pembayaran Poin EFN (bertanda; refund -> negatif)."""
        self.ensure_one()
        return sum(self._efn_points_payments().mapped("amount"))

    def _efn_line_raw_amounts(self, line, qty=None):
        """(excl, incl) baris dihitung ulang server dari harga & pajak (tanpa tanda dokumen refund).

        Tidak memakai `price_subtotal(_incl)` kiriman browser (konvensi tanda refund berbeda dan
        nilainya bisa dipalsukan).
        """
        qty = line.qty if qty is None else qty
        price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
        taxes = self.fiscal_position_id.map_tax(line.tax_ids) if self.fiscal_position_id else line.tax_ids
        if not taxes:
            amount = price * qty
            return amount, amount
        res = taxes.compute_all(price, self.currency_id, qty, product=line.product_id, partner=self.partner_id)
        return res["total_excluded"], res["total_included"]

    def _efn_line_is_eligible(self, line, excluded_categ_ids):
        return line.product_id.categ_id.id not in excluded_categ_ids

    def _efn_loyalty_eligible_raw(self, lines=None, extra_excluded=None):
        """Σ subtotal tanpa pajak baris yang berhak poin (bertanda; refund -> negatif). ``extra_excluded`` (G31b) =
        kategori tambahan yang dikecualikan (pengecualian kebijakan MGM)."""
        self.ensure_one()
        excluded = self.env["product.category"]._efn_loyalty_excluded_ids()
        if extra_excluded:
            excluded = excluded | set(extra_excluded)
        lines = self.lines.sudo() if lines is None else lines
        return sum(self._efn_line_raw_amounts(line)[0] for line in lines if self._efn_line_is_eligible(line, excluded))

    def _efn_mgm_excluded_ids(self):
        """G31b (docs/04 §20.10): kategori pengecualian kebijakan MGM PT order ini (``self`` = order ASAL) pada
        ``date_order``; ``None`` = tidak ada kebijakan/pengecualian (dasar MGM tidak dikirim). Tanpa depends:
        efn_mgm_policy opsional."""
        self.ensure_one()
        if "efn.mgm.policy" not in self.env:
            return None
        return self.env["efn.mgm.policy"]._efn_mgm_excluded_category_ids(self.company_id, self.date_order)

    def _efn_mgm_basis_vals(self, original=None, lines=None, refund=False):
        """G31b: nilai outbox ``efn_mgm_eligible_amount``/``efn_mgm_basis_set`` (rumus dasar earn + pengecualian MGM,
        faktor porsi uang order asal). Kosong bila tidak ada pengecualian."""
        self.ensure_one()
        origin = original or self
        mgm_excluded = origin._efn_mgm_excluded_ids()
        if not mgm_excluded:
            return {}
        raw = self._efn_loyalty_eligible_raw(lines, extra_excluded=mgm_excluded)
        value = (-raw if refund else raw) * origin._efn_loyalty_money_factor()
        return {"efn_mgm_eligible_amount": int(math.floor(value + 1e-6)) if value > 0 else 0, "efn_mgm_basis_set": True}

    def _efn_loyalty_goods_lines(self):
        self.ensure_one()
        return self.lines.sudo()

    def _efn_loyalty_order_kind(self):
        """SEC-59: jenis order menurut SERVER (flag `is_refund` klien hanya menambah kecurigaan).

        :returns: ``(kind, origins)`` dengan kind:
            - ``sale``: tanpa baris refund, tanpa baris barang qty < 0, `is_refund` tidak diset;
            - ``refund``: baris barang refund menunjuk TEPAT SATU order asal
              (`refunded_orderline_id`), tanpa barang qty < 0 tanpa asal dan tanpa barang baru;
            - ``return_unlinked``: barang qty < 0 tanpa order asal (retur tanpa struk / tombol
              `-`) atau `is_refund` tanpa baris refund;
            - ``refund_ambiguous``: baris refund dari > 1 order asal, atau refund bercampur
              barang lain.
        """
        self.ensure_one()
        goods = self._efn_loyalty_goods_lines()
        linked = goods.filtered("refunded_orderline_id")
        origins = linked.refunded_orderline_id.order_id
        unlinked_negative = goods.filtered(lambda line: line.qty < 0 and not line.refunded_orderline_id)
        if not origins:
            if unlinked_negative or self.is_refund:
                return "return_unlinked", origins
            return "sale", origins
        new_items = (goods - linked).filtered(lambda line: line.qty > 0)
        if len(origins) != 1 or unlinked_negative or new_items:
            return "refund_ambiguous", origins
        return "refund", origins

    def _efn_loyalty_exclude_point_payment(self):
        """K-18-4 / L-24: flag `earn_exclude_point_payment` dari `GET /v1/pos/loyalty/params` (cache, tanpa memanggil
        BFF saat sync); tanpa cache -> `ir.config_parameter` `efn.pos.loyalty.earn_exclude_point_payment` (True)."""
        self.ensure_one()
        flag = self.config_id.sudo()._efn_loyalty_cached_params().get("earn_exclude_point_payment")
        if isinstance(flag, bool):
            return flag
        raw = self.env["ir.config_parameter"].sudo().get_param(EARN_EXCLUDE_POINTS_PARAM, "True")
        return str(raw).strip().lower() in _TRUE

    def _efn_loyalty_money_factor(self):
        """K-18-4: porsi order yang dibayar selain Poin EFN = 1 − bayar_poin ÷ amount_total (incl. pajak), 0..1.

        Satu fungsi (keputusan final Finance). `efn.pos.loyalty.earn_exclude_point_payment` = False -> 1.0
        (pembayaran poin ikut menghasilkan poin)."""
        self.ensure_one()
        if not self._efn_loyalty_exclude_point_payment():
            return 1.0
        total = self._efn_goods_incl_raw()
        points = self._efn_points_paid()
        if total <= 0 or points <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - points / total))

    def _efn_loyalty_eligible_amount(self, factor=None, lines=None, refund=False):
        """K-1/K-2/K-18-4 (docs/04 §9.6, §9.9): Σ subtotal TANPA pajak baris produk setelah diskon baris & reward
        promo (baris reward ikut, mengurangi dasar), TIDAK termasuk produk di kategori `efn_loyalty_earn_excluded`,
        dikali faktor porsi non-poin. Dibulatkan ke bawah ke Rupiah. SEC-59: TANPA `abs()` -- order jual hanya
        menghasilkan dasar bila Σ bertanda positif; refund (`refund=True`) memakai -Σ baris refund dengan faktor
        order asal."""
        self.ensure_one()
        raw = self._efn_loyalty_eligible_raw(lines)
        if factor is None:
            factor = self._efn_loyalty_money_factor()
        value = (-raw if refund else raw) * factor
        return int(math.floor(value + 1e-6)) if value > 0 else 0

    def _efn_goods_incl_raw(self, lines=None):
        """Σ total incl. pajak baris order (bertanda), dihitung server."""
        self.ensure_one()
        lines = self._efn_loyalty_goods_lines() if lines is None else lines
        return sum(self._efn_line_raw_amounts(line)[1] for line in lines)

    def _efn_currency_compare(self, value1, value2):
        return self.currency_id.compare_amounts(value1, value2)

    # ------------------------------------------------------- state & review
    def _efn_loyalty_flag_review(self, reason):
        for order in self:
            _logger.warning("EFN loyalty: order %s ditandai review (%s)", order.uuid, reason)
            order.sudo().write({"efn_loyalty_state": "review", "efn_loyalty_note": reason})
            order.sudo().message_post(
                body=_("Poin EFN perlu review supervisor: %(reason)s", reason=reason),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )

    def _efn_loyalty_refresh_state(self):
        Outbox = self.env["efn.pos.loyalty.outbox"].sudo()
        for order in self:
            if order.efn_loyalty_state == "review":
                continue  # review bersifat lengket: hanya supervisor yang menyelesaikan
            records = Outbox.search([("order_id", "=", order.id)])
            if not records:
                continue
            states = set(records.mapped("state"))
            if "hold" in states:
                new_state = "review"
            elif "pending" in states:
                new_state = "pending"
            elif "failed" in states:
                new_state = "review"
            else:
                new_state = "done"
            if order.efn_loyalty_state != new_state:
                order.sudo().write({"efn_loyalty_state": new_state})

    # ----------------------------------------------------------------- sync
    def _efn_loyalty_strip_draft(self):
        """Draft: pembayaran poin tanpa reservasi aktif yang cocok dihapus; baris produk poin lama juga dihapus.

        Re-review SEC-125: "cocok" = reservasi aktif order ini, partner sama, setiap baris positif, dan jumlah baris
        Poin EFN = `value_idr` reservasi (toleransi 0). Validasi lengkap tetap saat order dilunasi."""
        self.ensure_one()
        legacy = self._efn_legacy_points_lines()
        if legacy:
            _logger.warning(
                "EFN loyalty: %d baris produk Poin EFN (desain lama) dihapus dari draft %s", len(legacy), self.uuid
            )
            legacy.unlink()
        payments = self._efn_points_payments()
        if not payments:
            return
        reservation = self.env["efn.pos.loyalty.reservation"]._efn_find_active(self.config_id, self.uuid)
        reason = self._efn_loyalty_draft_mismatch(reservation, payments)
        if not reason:
            return
        _logger.warning(
            "EFN loyalty: %d pembayaran Poin EFN dihapus dari draft %s (%s)", len(payments), self.uuid, reason
        )
        payments.unlink()
        self.write({"efn_loyalty_points_redeemed": 0, "efn_loyalty_redeem_value": 0.0})

    def _efn_loyalty_draft_mismatch(self, reservation, payments):
        if not reservation:
            return "no_reservation"
        if reservation.partner_id != self.partner_id:
            return "partner_mismatch"
        if any(self._efn_currency_compare(amount, 0.0) <= 0 for amount in payments.mapped("amount")):
            return "non_positive"
        if self._efn_currency_compare(sum(payments.mapped("amount")), reservation.value_idr) != 0:
            return "value_mismatch"
        return False

    def _efn_loyalty_reject(self, reason, message=None):
        _logger.warning("EFN loyalty: order %s ditolak saat sync (%s)", self.uuid, reason)
        raise UserError(
            message
            or _(
                "Penukaran Poin EFN pada order ini tidak sesuai reservasi (%(reason)s). "
                "Order tidak disimpan; batalkan penukaran poin lalu ulangi.",
                reason=reason,
            )
        )

    def _efn_loyalty_validate_paid(self):
        """Dipanggil SEBELUM langkah standar & consume token promo; gagal -> rollback sync."""
        self.ensure_one()
        if self._efn_legacy_points_lines():
            self._efn_loyalty_reject(
                "legacy_points_line",
                _("Produk 'Poin EFN' tidak lagi dipakai. Tukar poin lewat tombol 'Tukar Poin EFN' (metode bayar)."),
            )
        payments = self._efn_points_payments()
        if not payments:
            return
        kind, origins = self._efn_loyalty_order_kind()
        if kind == "refund":
            self._efn_loyalty_validate_refund_payments(origins, payments)
            return
        if kind != "sale":
            self._efn_loyalty_reject(
                f"points_payment_on_{kind}",
                _("Retur tanpa struk / refund campuran tidak boleh memakai metode Poin EFN."),
            )
        reservation = self.env["efn.pos.loyalty.reservation"]._efn_find_active(self.config_id, self.uuid)
        if not reservation:
            self._efn_loyalty_reject(
                "no_reservation",
                _(
                    "Order ini membawa pembayaran Poin EFN tanpa reservasi yang sah. "
                    "Hapus pembayaran Poin EFN atau ulangi penukaran (OTP)."
                ),
            )
        reason = self._efn_loyalty_redeem_violation(reservation, payments)
        if reason:
            self._efn_loyalty_reject(reason)

    def _efn_loyalty_redeem_violation(self, reservation, payments):
        """SEC-62/E-30: kode pelanggaran redeem (None bila sah). Semua nilai dihitung server; toleransi 0."""
        self.ensure_one()
        if any(self._efn_currency_compare(amount, 0.0) <= 0 for amount in payments.mapped("amount")):
            return "points_payment_not_positive"
        value = sum(payments.mapped("amount"))
        if self._efn_currency_compare(value, reservation.value_idr) != 0:
            return "value_mismatch"
        if reservation.partner_id != self.partner_id:
            return "partner_mismatch"
        total = self._efn_goods_incl_raw()
        pct = reservation.redeem_max_pct or 100
        if self._efn_currency_compare(value, total * pct / 100.0) > 0:
            return "exceeds_max_pct"
        non_cash = sum(
            self.payment_ids.sudo().filtered(lambda p: not p.payment_method_id.is_cash_count).mapped("amount")
        )
        if self._efn_currency_compare(non_cash, total) > 0:
            # Kembalian hanya boleh dari porsi tunai.
            return "change_from_points"
        return None

    def _efn_loyalty_lock_origin(self, original):
        """SEC-117/SEC-127: serialkan refund paralel atas order asal sebelum menghitung sisa Poin EFN (UPDATE no-op,
        transaksi kedua gagal serialisasi lalu di-retry Odoo dengan snapshot baru)."""
        touch_row(self.env.cr, "pos_order", original.id)

    def _efn_loyalty_validate_refund_payments(self, origins, payments):
        original = origins[:1]
        self._efn_loyalty_lock_origin(original)
        amounts = payments.mapped("amount")
        if any(self._efn_currency_compare(amount, 0.0) >= 0 for amount in amounts):
            self._efn_loyalty_reject(
                "refund_points_payment_positive", _("Pembayaran Poin EFN pada refund harus bernilai negatif.")
            )
        original_points = original._efn_points_paid()
        if self._efn_currency_compare(original_points, 0.0) <= 0:
            self._efn_loyalty_reject(
                "refund_points_without_redeem",
                _("Order asal tidak dibayar dengan Poin EFN; refund tidak boleh memakai metode itu."),
            )
        remaining = original_points - original._efn_loyalty_points_restored(exclude_refund=self)
        if self._efn_currency_compare(-sum(amounts), remaining) > 0:
            self._efn_loyalty_reject(
                "refund_points_exceeds_redeemed", _("Pengembalian Poin EFN melebihi poin yang dipakai di order asal.")
            )

    def _efn_loyalty_process_paid(self):
        self.ensure_one()
        Outbox = self.env["efn.pos.loyalty.outbox"]
        kind, origins = self._efn_loyalty_order_kind()
        if kind == "refund":
            self._efn_loyalty_process_refund(origins)
        elif kind == "sale":
            self._efn_loyalty_process_redeem()
            self._efn_loyalty_enqueue_earn()
        elif self._efn_loyalty_return_relevant(origins):
            # SEC-59: retur tanpa tepat satu order asal -> tidak ada earn, tidak ada reverse
            # otomatis (tidak dilewati diam-diam); supervisor memutuskan clawback manual.
            self._efn_loyalty_flag_review(kind)
        if Outbox.sudo().search_count([("order_id", "=", self.id)], limit=1):
            self._efn_loyalty_refresh_state()
            Outbox._efn_trigger()

    def _efn_loyalty_return_relevant(self, origins):
        """Retur yang menyangkut Poin EFN: pelanggan dipilih di POS ber-earn, atau order asal ikut loyalty."""
        self.ensure_one()
        if self.partner_id and self.config_id.efn_loyalty_earn_enabled:
            return True
        return any(origin._efn_loyalty_original_involved() for origin in origins)

    def _efn_loyalty_process_redeem(self):
        Reservation = self.env["efn.pos.loyalty.reservation"]
        Outbox = self.env["efn.pos.loyalty.outbox"]
        reservation = Reservation._efn_find_active(self.config_id, self.uuid)
        payments = self._efn_points_payments()
        if not payments:
            self.write({"efn_loyalty_points_redeemed": 0, "efn_loyalty_redeem_value": 0.0})
            if reservation:
                # Pembayaran poin dihapus kasir -> lepaskan poin pelanggan.
                reservation.write({"order_id": self.id, "note": "no_points_payment"})
                Outbox._efn_enqueue(
                    {
                        "company_id": reservation.company_id.id,
                        "order_id": self.id,
                        "kind": "redeem_release",
                        "idempotency_key": reservation._efn_release_key(),
                        "reservation_id": reservation.id,
                    }
                )
            return
        # Backstop (validasi preventif SEC-62 sudah menolak sync sebelum titik ini).
        reason = self._efn_loyalty_redeem_violation(reservation, payments)
        self.write(
            {
                "efn_loyalty_points_redeemed": reservation.points,
                "efn_loyalty_redeem_value": reservation.value_idr,
            }
        )
        if reason:
            reservation.write({"order_id": self.id, "state": "review", "note": reason})
            self._efn_loyalty_flag_review(f"redeem:{reason}")
            return
        reservation.write({"order_id": self.id})
        Outbox._efn_enqueue(
            {
                "company_id": self.company_id.id,
                "order_id": self.id,
                "kind": "redeem_commit",
                "idempotency_key": f"pos_redeem:{self.uuid}",
                "reservation_id": reservation.id,
            }
        )

    def _efn_loyalty_enqueue_earn(self):
        if not self.config_id.efn_loyalty_earn_enabled or not self.partner_id:
            return
        if not self.company_id.efn_brand_code:
            _logger.warning("EFN loyalty: company #%s tanpa efn_brand_code, earn dilewati", self.company_id.id)
            return
        vals = {
            "company_id": self.company_id.id,
            "order_id": self.id,
            "kind": "earn",
            "idempotency_key": f"pos_earn:{self.uuid}",
            "eligible_amount": self._efn_loyalty_eligible_amount(),
            **self._efn_mgm_basis_vals(),
        }
        member, member_error = partner_member_ref(self.partner_id.sudo())
        if not member:
            # Partner tanpa HP Indonesia / sudah dianonimkan: dicatat, tidak dikirim.
            vals.update({"state": "skipped", "last_error": f"member_missing:{member_error}"})
        else:
            self.partner_id._efn_mark_member()  # SEC-115: kunci HP member permanen
        # §9.7: earn dikirim untuk SETIAP order ber-member, termasuk eligible 0 (BFF mencatatnya
        # sebagai dasar reverse).
        self.env["efn.pos.loyalty.outbox"]._efn_enqueue(vals)

    # ----------------------------------------------------------------- refund
    def _efn_loyalty_original_involved(self):
        """True bila order (asal) ikut loyalty: earn/commit tercatat dan tidak dilewati."""
        self.ensure_one()
        return bool(
            self.env["efn.pos.loyalty.outbox"]
            .sudo()
            .search_count(
                [
                    ("order_id", "=", self.id),
                    ("kind", "in", ("earn", "redeem_commit")),
                    ("state", "!=", "skipped"),
                ],
                limit=1,
            )
        )

    def _efn_loyalty_refund_orders(self, exclude_refund=None):
        """Order refund lain (tersinkron) atas order asal `self`."""
        self.ensure_one()
        domain = [
            ("lines.refunded_orderline_id.order_id", "=", self.id),
            ("state", "in", PAID_STATES),
        ]
        if exclude_refund:
            domain.append(("id", "!=", exclude_refund.id))
        return self.sudo().search(domain)

    def _efn_loyalty_points_restored(self, exclude_refund=None):
        """Σ Poin EFN (Rupiah, positif) yang sudah dikembalikan lewat refund lain atas order asal `self`."""
        self.ensure_one()
        return -sum(order._efn_points_paid() for order in self._efn_loyalty_refund_orders(exclude_refund))

    def _efn_loyalty_expected_points_restore(self, refund_goods_incl, exclude_refund=None):
        """K-18-5 / Q-F18-4 (proporsional, kumulatif): Poin EFN (Rupiah bulat) yang dikembalikan untuk refund
        bernilai `refund_goods_incl` (incl. pajak) atas order asal `self` =
        round((Σ refund sebelumnya + refund ini) × bayar_poin ÷ total) − poin yang sudah dikembalikan."""
        self.ensure_one()
        points = self._efn_points_paid()
        total = abs(self._efn_goods_incl_raw())
        if points <= 0 or total <= 0:
            return 0
        previous_orders = self._efn_loyalty_refund_orders(exclude_refund)
        previous_goods = sum(abs(order._efn_goods_incl_raw()) for order in previous_orders)
        restored = -sum(order._efn_points_paid() for order in previous_orders)
        cumulative = min(previous_goods + abs(refund_goods_incl), total)
        expected = self.currency_id.round(cumulative * points / total) - restored
        return int(round(max(0.0, min(expected, points - restored))))

    def _efn_loyalty_refund_ratio(self, refunded_goods_eligible, refunded_goods, exclude_refund=None):
        """Rasio refund atas order asal `self` (kumulatif <= 1) untuk clawback earn (K-18-5: `refund_ratio` tetap).

        Rasio = nilai barang eligible yang direfund / nilai barang eligible order asal (tanpa pajak). Bila
        eligible asal 0, dipakai rasio nilai barang incl. pajak. Rasio refund lain yang sudah tercatat untuk
        order asal mengurangi sisa.
        """
        self.ensure_one()
        original_goods_eligible = abs(self._efn_loyalty_eligible_raw())
        if original_goods_eligible > 0.005:
            ratio = abs(refunded_goods_eligible) / original_goods_eligible
        else:
            original_goods = abs(self._efn_goods_incl_raw())
            ratio = (abs(refunded_goods) / original_goods) if original_goods else 0.0
        domain = [
            ("original_order_id", "=", self.id),
            ("kind", "=", "reverse"),
            ("state", "!=", "skipped"),
        ]
        if exclude_refund:
            domain.append(("order_id", "!=", exclude_refund.id))
        previous = sum(self.env["efn.pos.loyalty.outbox"].sudo().search(domain).mapped("refund_ratio"))
        ratio = max(0.0, min(ratio, 1.0 - previous, 1.0))
        return round(ratio, 6)

    def _efn_loyalty_process_refund(self, original):
        Outbox = self.env["efn.pos.loyalty.outbox"]
        goods = self._efn_loyalty_goods_lines()
        actual_restore = int(round(-self._efn_points_paid()))
        original_has_points = self._efn_currency_compare(original._efn_points_paid(), 0.0) > 0
        if not original._efn_loyalty_original_involved() and not original_has_points:
            self._efn_loyalty_qr_refund(original, goods)
            return
        # Dasar clawback memakai faktor uang order ASAL.
        refunded_eligible = self._efn_loyalty_eligible_amount(
            factor=original._efn_loyalty_money_factor(), lines=goods, refund=True
        )
        ratio = original._efn_loyalty_refund_ratio(
            self._efn_loyalty_eligible_raw(goods), self._efn_goods_incl_raw(goods), exclude_refund=self
        )
        hold_reason = None
        if original_has_points:
            expected = original._efn_loyalty_expected_points_restore(
                self._efn_goods_incl_raw(goods), exclude_refund=self
            )
            # Toleransi Rp1 (pembulatan alokasi kumulatif vs frontend).
            if abs(actual_restore - expected) > 1:
                hold_reason = "refund_points_allocation_mismatch"
                self._efn_loyalty_flag_review(hold_reason)
        vals = {
            "company_id": self.company_id.id,
            "order_id": self.id,
            "original_order_id": original.id,
            "kind": "reverse",
            "idempotency_key": f"pos_reverse:{self.uuid}",
            "eligible_amount": refunded_eligible,
            "refund_ratio": ratio,
            "points_restored_value": actual_restore,
            **self._efn_mgm_basis_vals(original=original, lines=goods, refund=True),
        }
        member, member_error = partner_member_ref(original.partner_id.sudo())
        if ratio <= 0 and actual_restore <= 0:
            vals.update({"state": "skipped", "last_error": "zero_ratio"})
        elif not member:
            vals.update({"state": "skipped", "last_error": f"member_missing:{member_error}"})
        elif hold_reason:
            # SEC-60: restore poin redeem TIDAK boleh terjadi otomatis bila alokasi tidak sesuai.
            vals.update({"state": "hold", "last_error": hold_reason})
        record = Outbox._efn_enqueue(vals)
        if record.state == "hold":
            record._efn_schedule_activity(hold_reason or record.last_error)

    # ------------------------------------------------------------- refund QR (G22 L-80, r15 K3)
    def _efn_loyalty_qr_refund(self, original, goods):
        """K3 r15 (SEC-164): refund atas order yang mungkin di-earn lewat klaim QR (``efn_pos_bff_order``, dibaca
        tanpa depends lewat ``pos.order._efn_qr_refund_mode``):

        - ``reverse``: event close yang melaporkan order asal sudah diterima BFF (2xx) -> reverse ``earn_channel:"qr"``;
        - ``deferred``: sesi meja order asal masih open/expired, atau event close masih pending di outbox -> TIDAK ada
          reverse; refund dikurangkan dari ``eligible_amount`` yang dihitung ulang saat outbox mengirim (baris sesi
          dikunci, tanpa jendela);
        - ``netted``: refund sudah dinetokan di event close -> tidak ada reverse (hindari potong dobel);
        - ``awaiting`` (L-83): snapshot close sudah beku tetapi belum 2xx -> refund ditandai; outbox QR memanggil
          metode ini lagi saat 2xx (mode lalu ``reverse``).
        Log hanya uuid order (tanpa PII)."""
        self.ensure_one()
        mode = original._efn_qr_refund_mode(self) if hasattr(original, "_efn_qr_refund_mode") else None
        if mode == "reverse":
            self._efn_loyalty_enqueue_qr_reverse(original, goods)
        elif mode in ("deferred", "netted", "awaiting"):
            _logger.info(
                "EFN loyalty: refund %s atas order QR %s -> %s (tanpa reverse)", self.uuid, original.uuid, mode
            )
        return mode

    def _efn_loyalty_qr_refund_ratio(self, original, goods):
        """Rasio reverse QR terhadap dasar NETO yang dilaporkan di event close (``efn_qr_eligible_amount``), karena
        BFF meng-earn atas neto itu dan clawback = ceil(earn × Σ rasio). Refund yang sudah dinetokan tidak ikut;
        rasio reverse QR sebelumnya untuk order asal mengurangi sisa (kumulatif ≤ 1). Tanpa dasar neto (data lama) ->
        rumus refund POS (``_efn_loyalty_refund_ratio``)."""
        self.ensure_one()
        base = original.efn_qr_eligible_amount if "efn_qr_eligible_amount" in original._fields else 0
        if not base or base <= 0:
            return original._efn_loyalty_refund_ratio(
                self._efn_loyalty_eligible_raw(goods), self._efn_goods_incl_raw(goods), exclude_refund=self
            )
        refunded = -self._efn_loyalty_eligible_raw(goods) * original._efn_loyalty_money_factor()
        previous = sum(
            self.env["efn.pos.loyalty.outbox"]
            .sudo()
            .search(
                [
                    ("original_order_id", "=", original.id),
                    ("kind", "=", "reverse"),
                    ("state", "!=", "skipped"),
                    ("order_id", "!=", self.id),
                ]
            )
            .mapped("refund_ratio")
        )
        ratio = max(0.0, min(refunded / base, 1.0 - previous, 1.0))
        return round(ratio, 6)

    def _efn_loyalty_enqueue_qr_reverse(self, original, goods):
        """Reverse ``earn_channel: "qr"`` tanpa member_ref (docs/04 §14.A.4, L-80). Dasar = rumus refund POS (faktor
        uang order asal, kategori dikecualikan); rasio terhadap dasar neto close (``_efn_loyalty_qr_refund_ratio``);
        tanpa restore poin (order QR tidak dibayar poin di jalur ini — order asal berpoin sudah ``involved`` dan
        memakai jalur POS)."""
        self.ensure_one()
        refunded_eligible = self._efn_loyalty_eligible_amount(
            factor=original._efn_loyalty_money_factor(), lines=goods, refund=True
        )
        ratio = self._efn_loyalty_qr_refund_ratio(original, goods)
        vals = {
            "company_id": self.company_id.id,
            "order_id": self.id,
            "original_order_id": original.id,
            "kind": "reverse",
            "earn_channel": "qr",
            "idempotency_key": f"pos_reverse:{self.uuid}",
            "eligible_amount": refunded_eligible,
            "refund_ratio": ratio,
            "points_restored_value": 0,
            **self._efn_mgm_basis_vals(original=original, lines=goods, refund=True),
        }
        if ratio <= 0:
            vals.update({"state": "skipped", "last_error": "zero_ratio"})
        return self.env["efn.pos.loyalty.outbox"]._efn_enqueue(vals)

    # ------------------------------------------------------------------ hooks
    def _process_saved_order(self, draft):
        self.ensure_one()
        if draft:
            self._efn_loyalty_strip_draft()
            return super()._process_saved_order(draft)
        if self.state != "cancel":
            self._efn_loyalty_validate_paid()
        order_id = super()._process_saved_order(draft)
        if self.state != "cancel":
            self._efn_loyalty_process_paid()
        return order_id

    def _efn_loyalty_release_drafts(self, keep_order_link):
        Reservation = self.env["efn.pos.loyalty.reservation"]
        Outbox = self.env["efn.pos.loyalty.outbox"]
        enqueued = False
        # `cancel` ikut: pos_self_order.remove_from_ui menulis state cancel sebelum super().
        for order in self.filtered(lambda o: o.state in ("draft", "cancel")):
            reservation = Reservation._efn_find_active(order.config_id, order.uuid)
            if not reservation:
                continue
            Outbox._efn_enqueue(
                {
                    "company_id": reservation.company_id.id,
                    "order_id": order.id if keep_order_link else False,
                    "kind": "redeem_release",
                    "idempotency_key": reservation._efn_release_key(),
                    "reservation_id": reservation.id,
                }
            )
            reservation.write({"note": "order_cancelled"})
            enqueued = True
        if enqueued:
            Outbox._efn_trigger()

    def action_pos_order_cancel(self):
        self._efn_loyalty_release_drafts(keep_order_link=True)
        return super().action_pos_order_cancel()

    @api.model
    def remove_from_ui(self, server_ids):
        drafts = self.search([("id", "in", server_ids), ("state", "in", ("draft", "cancel"))])
        # Order bisa dihapus (core: sudo().unlink()) atau hanya dibatalkan (pos_self_order) ->
        # outbox release hanya menunjuk reservasi agar tidak menghalangi penghapusan.
        drafts._efn_loyalty_release_drafts(keep_order_link=False)
        return super().remove_from_ui(server_ids)

    # ----------------------------------------------------------- tampilan
    def _efn_loyalty_member_masked(self):
        self.ensure_one()
        member, _error = partner_member_ref(self.partner_id.sudo())
        return mask_phone(member) if member else False
