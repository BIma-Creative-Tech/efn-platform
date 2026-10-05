"""Validasi server pembayaran `efn_gateway` saat order POS di-sync (anti-fraud, docs/08 §4.1).

Hook (source 19.0 terverifikasi): `_process_order` -> `_process_payment_lines(pos_order,
order, pos_session, draft)` -> `_process_saved_order(draft)`. Pada `_process_payment_lines`
baris `pos.payment` sudah tertulis (create/write order), sehingga kita memeriksa record
nyata, bukan payload browser.

Untuk setiap `pos.payment` dengan metode `efn_gateway` pada order NON-draft:
- `transaction_id` wajib = `bff_payment_id` sebuah intent;
- intent `paid` (refresh `GET` ke BFF bila masih pending);
- intent milik order ini (`pos_order_uuid`), metode & company sama, baris pembayaran sama
  (bila `pos.payment.uuid` ada), nominal sama (pembulatan mata uang);
- intent belum dipakai order/baris lain, dan satu intent tidak dipakai dua baris;
- nominal negatif (refund) ditolak — refund QRIS fase 1 lewat metode lain (docs/04 §8.8).
Gagal -> `UserError` -> seluruh sync di-rollback (order tetap di antrean POS, kasir melihat
pesan). Tidak pernah diam-diam.

Order draft (meja restoran) tidak divalidasi; validasi terjadi saat order dibayar.
"""

import logging

from odoo import _, models
from odoo.exceptions import UserError

from odoo.addons.efn_core.models.efn_bff_client import EfnBffError

from .pos_payment_method import TERMINAL_KEY

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _process_payment_lines(self, pos_order, order, pos_session, draft):
        result = super()._process_payment_lines(pos_order, order, pos_session, draft)
        if not draft:
            order._efn_gateway_validate_payments()
        return result

    def _efn_gateway_reject(self, reason):
        _logger.warning("EFN gateway: sync order %s DITOLAK (%s)", self.uuid, reason)
        raise UserError(
            _(
                "Pembayaran QRIS pada order ini tidak dapat diverifikasi (%(reason)s). "
                "Order tidak disimpan; periksa status QRIS atau gunakan metode lain.",
                reason=reason,
            )
        )

    def _efn_gateway_validate_payments(self):
        self.ensure_one()
        payments = self.payment_ids.sudo().filtered(lambda p: p.payment_method_id.use_payment_terminal == TERMINAL_KEY)
        if not payments:
            return
        Intent = self.env["efn.pos.payment.intent"].sudo()
        currency = self.currency_id
        used = Intent.browse()
        has_payment_uuid = "uuid" in payments._fields
        for payment in payments:
            if payment.is_change or currency.compare_amounts(payment.amount, 0.0) <= 0:
                self._efn_gateway_reject("refund_not_supported")
            tx = payment.transaction_id
            if not tx:
                self._efn_gateway_reject("transaction_missing")
            intent = Intent.search([("bff_payment_id", "=", tx)], limit=1)
            if not intent:
                self._efn_gateway_reject("intent_unknown")
            if intent in used:
                self._efn_gateway_reject("intent_reused")
            if intent.pos_order_uuid != self.uuid:
                self._efn_gateway_reject("order_mismatch")
            if intent.payment_method_id != payment.payment_method_id or intent.company_id != self.company_id:
                self._efn_gateway_reject("method_mismatch")
            if has_payment_uuid and payment.uuid and intent.payment_uuid != payment.uuid:
                self._efn_gateway_reject("payment_mismatch")
            if (intent.pos_order_id and intent.pos_order_id != self) or (
                intent.pos_payment_id and intent.pos_payment_id.pos_order_id != self
            ):
                self._efn_gateway_reject("intent_reused")
            if intent.status == "pending":
                try:
                    intent._efn_refresh_status()
                except EfnBffError as exc:
                    _logger.warning("EFN gateway: refresh intent %s gagal (%s)", intent.id, exc.code)
            if intent.status != "paid":
                self._efn_gateway_reject(f"intent_{intent.status}")
            if intent.currency_id != currency or currency.compare_amounts(payment.amount, intent.amount) != 0:
                self._efn_gateway_reject("amount_mismatch")
            used |= intent
            intent.write({"pos_payment_id": payment.id, "pos_order_id": self.id})
