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
- **Retur tanpa struk** (barang qty < 0 tanpa order asal): default hanya tunai (Q-F18-5, L-23);
  `efn.pos.refund.unlinked_return_mode` = ``cash`` | ``deny`` (``deny``: retur tanpa struk ditolak).
- Pelanggaran -> `UserError` (sync ditolak; kasir memperbaiki pembayaran). Jenis order ditentukan server dari
  baris (bukan flag klien `is_refund`, pola SEC-59).
"""

import logging

from odoo import _, api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

UNLINKED_MODE_PARAM = "efn.pos.refund.unlinked_return_mode"
UNLINKED_MODES = ("cash", "deny")
PAID_STATES = ("paid", "done", "invoiced")


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
        raw = self.env["ir.config_parameter"].sudo().get_param(UNLINKED_MODE_PARAM, "cash")
        mode = str(raw or "cash").strip().lower()
        return mode if mode in UNLINKED_MODES else "cash"

    def _efn_refund_reject(self, code, message):
        _logger.warning("EFN refund origin: order %s ditolak saat sync (%s)", self.uuid, code)
        raise UserError(message)

    # ---------------------------------------------------------- validation
    def _efn_refund_origin_validate(self):
        self.ensure_one()
        outgoing = self.payment_ids.sudo().filtered(
            lambda p: not p.is_change and self.currency_id.compare_amounts(p.amount, 0.0) < 0
        )
        lines = self.lines.sudo()
        origins = self._efn_refund_origins()
        unlinked = lines.filtered(lambda line: line.qty < 0 and not line.refunded_orderline_id)
        if unlinked and not origins:
            if self._efn_unlinked_return_mode() == "deny":
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
            return
        if not origins or not outgoing:
            return
        if len(origins) != 1:
            self._efn_refund_reject("multiple_origins", _("Refund hanya boleh atas satu order asal."))
        origin = origins
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
        if not draft and self.state != "cancel":
            self._efn_refund_origin_validate()
        return super()._process_saved_order(draft)
