"""Validasi & antrean Poin EFN saat order POS disinkron (docs/08 §6.3 E/H, docs/04 §9 v4.1).

Hook (source 19.0): `sync_from_ui` -> `_process_order` -> `_process_saved_order(draft)`; pada
titik ini baris order sudah tertulis. Modul ini membungkus `_process_saved_order` milik
`efn_pos_otp_gate` (depends), jadi:

- **Draft**: baris "Poin EFN" tanpa reservasi aktif untuk (config, uuid) dihapus.
- **Paid** (sebelum langkah standar & consume token promo): baris Poin EFN tanpa reservasi
  `reserved` -> `UserError` (klien dimodifikasi; seluruh sync di-rollback). Baris poin
  dideteksi dari `product_id` (SEC-12), bukan flag klien.
- **Paid** (setelah langkah standar): nilai |Σ baris poin incl. pajak| dibandingkan dengan
  `value_idr` reservasi (toleransi Rp1 x jumlah baris) dan partner order dengan partner
  reservasi -> beda: reservasi & order `review` (tanpa commit); cocok -> outbox `redeem_commit`.
  Reservasi aktif tanpa baris poin -> outbox `redeem_release`.
- Earn: order paid non-refund, partner ada, `efn_loyalty_earn_enabled`, company ber-
  `efn_brand_code` -> outbox `earn` (`eligible_amount` K-1).
- Refund atas order asal yang ikut loyalty -> outbox `reverse` dengan `refund_ratio`; bila order
  asal memakai poin, refund wajib membawa baris penyesuaian Poin EFN = round(ratio x nilai poin
  asal) (selisih -> `review`).
- Order draft dibatalkan/dihapus (`action_pos_order_cancel`, `remove_from_ui`) dengan reservasi
  aktif -> outbox `redeem_release`.

Semua panggilan BFF terjadi di cron outbox (bukan di dalam sync), sehingga sync order tidak
pernah gagal karena BFF mati.
"""

import logging
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.models.efn_bff_client import mask_phone, partner_member_ref

_logger = logging.getLogger(__name__)

POINTS_PRODUCT_XMLID = "efn_pos_loyalty_ledger.product_efn_points"
LOYALTY_STATES = [
    ("pending", "Menunggu sinkron poin"),
    ("done", "Poin tersinkron"),
    ("review", "Perlu review"),
]


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
    def _efn_points_product(self):
        return self.env.ref(POINTS_PRODUCT_XMLID, raise_if_not_found=False) or self.env["product.product"]

    def _efn_points_lines(self):
        self.ensure_one()
        product = self._efn_points_product()
        if not product:
            return self.env["pos.order.line"]
        return self.lines.sudo().filtered(lambda line: line.product_id == product)

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

    def _efn_points_value(self, lines=None):
        """Nilai Rupiah (incl. pajak, bertanda) total baris poin."""
        self.ensure_one()
        lines = self._efn_points_lines() if lines is None else lines
        return sum(self._efn_line_raw_amounts(line)[1] for line in lines)

    def _efn_line_is_eligible(self, line, points_product, excluded_categ_ids):
        if line.product_id == points_product:
            return False
        return line.product_id.categ_id.id not in excluded_categ_ids

    def _efn_loyalty_eligible_raw(self):
        """Σ subtotal tanpa pajak baris BARANG yang berhak poin (bertanda; refund -> negatif).
        Baris Poin EFN tidak termasuk (porsinya diterapkan lewat `_efn_loyalty_money_factor`)."""
        self.ensure_one()
        points_product = self._efn_points_product()
        excluded = self.env["product.category"]._efn_loyalty_excluded_ids()
        return sum(
            self._efn_line_raw_amounts(line)[0]
            for line in self.lines.sudo()
            if self._efn_line_is_eligible(line, points_product, excluded)
        )

    def _efn_loyalty_money_factor(self):
        """Porsi nilai barang yang dibayar uang (1 - porsi Poin EFN), tanpa pajak, 0..1.

        K-1: "bagian yang dibayar poin tidak menghasilkan poin". Nilai poin tersebar proporsional
        ke seluruh barang (frontend membaginya per kelompok pajak), jadi dasar earn barang
        eligible dikalikan faktor yang sama.
        """
        self.ensure_one()
        points_product = self._efn_points_product()
        goods = 0.0
        points = 0.0
        for line in self.lines.sudo():
            excl = self._efn_line_raw_amounts(line)[0]
            if line.product_id == points_product:
                points += excl
            else:
                goods += excl
        if not goods or self.is_refund:
            return 1.0
        return max(0.0, min(1.0, 1.0 + points / goods))

    def _efn_loyalty_eligible_amount(self, factor=None):
        """K-1 (docs/04 §9.6): Σ subtotal TANPA pajak baris produk setelah diskon baris & reward
        promo (baris reward ikut, mengurangi dasar), TIDAK termasuk baris "Poin EFN" dan produk di
        kategori `efn_loyalty_earn_excluded` (K-2), dikurangi porsi yang dibayar poin (faktor
        uang). Dibulatkan ke bawah ke Rupiah; refund -> nilai absolut dengan faktor order asal.
        Satu fungsi agar mudah diubah bila Finance menetapkan lain (Q-13)."""
        self.ensure_one()
        raw = self._efn_loyalty_eligible_raw()
        if factor is None:
            factor = self._efn_loyalty_money_factor()
        value = abs(raw) * factor
        return int(math.floor(value + 1e-6)) if value > 0 else 0

    def _efn_goods_incl_raw(self):
        """Σ total incl. pajak semua baris kecuali baris poin (bertanda)."""
        self.ensure_one()
        points_product = self._efn_points_product()
        return sum(
            self._efn_line_raw_amounts(line)[1] for line in self.lines.sudo() if line.product_id != points_product
        )

    @api.model
    def _efn_tolerance(self, line_count):
        return max(1, line_count) * 1.0

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
            if "pending" in states:
                new_state = "pending"
            elif "failed" in states:
                new_state = "review"
            else:
                new_state = "done"
            if order.efn_loyalty_state != new_state:
                order.sudo().write({"efn_loyalty_state": new_state})

    # ----------------------------------------------------------------- sync
    def _efn_loyalty_strip_draft(self):
        """Draft: baris poin tanpa reservasi aktif dihapus (pola _efn_strip_unverified_rewards)."""
        self.ensure_one()
        lines = self._efn_points_lines()
        if not lines:
            return
        reservation = self.env["efn.pos.loyalty.reservation"]._efn_find_active(self.config_id, self.uuid)
        if reservation:
            return
        _logger.warning("EFN loyalty: %d baris Poin EFN tanpa reservasi dihapus dari draft %s", len(lines), self.uuid)
        lines.unlink()
        self.write({"efn_loyalty_points_redeemed": 0, "efn_loyalty_redeem_value": 0.0})

    def _efn_loyalty_validate_paid(self):
        """Dipanggil SEBELUM langkah standar & consume token promo; gagal -> rollback sync."""
        self.ensure_one()
        lines = self._efn_points_lines()
        if not lines:
            return
        if self.is_refund:
            # Refund hanya boleh MENGURANGI uang kembali lewat baris penyesuaian (nilai positif).
            if any(self._efn_line_raw_amounts(line)[1] < 0 for line in lines):
                _logger.warning("EFN loyalty: refund %s membawa baris Poin EFN negatif", self.uuid)
                raise UserError(_("Baris Poin EFN pada refund tidak valid. Hapus baris tersebut."))
            return
        reservation = self.env["efn.pos.loyalty.reservation"]._efn_find_active(self.config_id, self.uuid)
        if not reservation:
            _logger.warning("EFN loyalty: order %s membawa baris Poin EFN tanpa reservasi", self.uuid)
            raise UserError(
                _(
                    "Order ini membawa penukaran Poin EFN tanpa reservasi yang sah. "
                    "Hapus baris Poin EFN atau ulangi penukaran (OTP)."
                )
            )

    def _efn_loyalty_process_paid(self):
        self.ensure_one()
        Outbox = self.env["efn.pos.loyalty.outbox"]
        if self.is_refund:
            originals = self.refunded_order_id
            if len(originals) == 1:
                self._efn_loyalty_process_refund(originals)
        else:
            self._efn_loyalty_process_redeem()
            self._efn_loyalty_enqueue_earn()
        if Outbox.sudo().search_count([("order_id", "=", self.id)], limit=1):
            self._efn_loyalty_refresh_state()
            Outbox._efn_trigger()

    def _efn_loyalty_process_redeem(self):
        Reservation = self.env["efn.pos.loyalty.reservation"]
        Outbox = self.env["efn.pos.loyalty.outbox"]
        reservation = Reservation._efn_find_active(self.config_id, self.uuid)
        lines = self._efn_points_lines()
        if not lines:
            self.write({"efn_loyalty_points_redeemed": 0, "efn_loyalty_redeem_value": 0.0})
            if reservation:
                # Baris poin dihapus kasir (mis. saat offline) -> lepaskan poin pelanggan.
                reservation.write({"order_id": self.id, "note": "no_points_line"})
                Outbox._efn_enqueue(
                    {
                        "company_id": reservation.company_id.id,
                        "order_id": self.id,
                        "kind": "redeem_release",
                        "idempotency_key": f"pos_release:{reservation.id}",
                        "reservation_id": reservation.id,
                    }
                )
            return
        value = abs(self._efn_points_value(lines))
        stored = abs(sum(lines.mapped("price_subtotal_incl")))
        tolerance = self._efn_tolerance(len(lines))
        reason = None
        if abs(value - reservation.value_idr) > tolerance or abs(stored - reservation.value_idr) > tolerance:
            reason = "value_mismatch"
        elif reservation.partner_id != self.partner_id:
            reason = "partner_mismatch"
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
        }
        member, member_error = partner_member_ref(self.partner_id.sudo())
        if not member:
            # Partner tanpa HP Indonesia / sudah dianonimkan: dicatat, tidak dikirim.
            vals.update({"state": "skipped", "last_error": f"member_missing:{member_error}"})
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

    def _efn_loyalty_refund_ratio(self, refunded_goods_eligible, refunded_goods, exclude_refund=None):
        """Rasio refund atas order asal `self` (kumulatif <= 1).

        Rasio = nilai barang eligible yang direfund / nilai barang eligible order asal (tanpa
        pajak, sebelum porsi poin — faktor poin sama di kedua sisi sehingga hasilnya sama dengan
        rasio eligible_amount §9.4). Bila eligible asal 0 (mis. semua produk kategori tanpa poin),
        dipakai rasio nilai barang incl. pajak agar penyesuaian poin tetap proporsional. Rasio
        refund lain yang sudah tercatat untuk order asal mengurangi sisa.
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

    def _efn_loyalty_expected_adjustment(self, ratio):
        """Nilai baris penyesuaian Poin EFN untuk refund dengan rasio `ratio` atas order asal `self`."""
        self.ensure_one()
        original_value = abs(self._efn_points_value())
        return int(round(ratio * original_value))

    def _efn_loyalty_process_refund(self, original):
        Outbox = self.env["efn.pos.loyalty.outbox"]
        refund_lines = self._efn_points_lines()
        original_has_points = bool(original._efn_points_lines())
        if not original._efn_loyalty_original_involved() and not original_has_points:
            if refund_lines:
                self._efn_loyalty_flag_review("refund_points_without_redeem")
            return
        # Dasar clawback memakai faktor uang order ASAL (baris penyesuaian poin di refund
        # mengembalikan porsi poin secara proporsional).
        refunded_eligible = self._efn_loyalty_eligible_amount(factor=original._efn_loyalty_money_factor())
        ratio = original._efn_loyalty_refund_ratio(
            self._efn_loyalty_eligible_raw(), self._efn_goods_incl_raw(), exclude_refund=self
        )
        if original_has_points:
            expected = original._efn_loyalty_expected_adjustment(ratio)
            actual = self._efn_points_value(refund_lines)
            if abs(actual - expected) > self._efn_tolerance(len(refund_lines)):
                self._efn_loyalty_flag_review("refund_points_adjustment_mismatch")
        vals = {
            "company_id": self.company_id.id,
            "order_id": self.id,
            "original_order_id": original.id,
            "kind": "reverse",
            "idempotency_key": f"pos_reverse:{self.uuid}",
            "eligible_amount": refunded_eligible,
            "refund_ratio": ratio,
        }
        member, member_error = partner_member_ref(original.partner_id.sudo())
        if ratio <= 0:
            vals.update({"state": "skipped", "last_error": "zero_ratio"})
        elif not member:
            vals.update({"state": "skipped", "last_error": f"member_missing:{member_error}"})
        Outbox._efn_enqueue(vals)

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
                    "idempotency_key": f"pos_release:{reservation.id}",
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
