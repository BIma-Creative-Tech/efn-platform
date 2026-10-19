"""Pesanan bayar di kasir: faktur/refund dibayar di jurnal kliring PT penjual (docs/04 §12.2 no. 6, L-55), bukan
jurnal titipan Holding.

SEC-145 (L-59): RPC BFF ``efn_online_counter_refund_authorize`` — otorisasi refund POS atas pelunasan counter
(lihat ``efn_online_counter_refund_auth.py``)."""

import logging

from odoo import api, models

from odoo.addons.efn_online_order.models.efn_online_common import (
    IDEMPOTENCY_KEY_RE,
    UUID_RE,
    canonical_sha256,
    check_amount_int,
    check_str,
    online_error,
    require,
)

_logger = logging.getLogger(__name__)

AUTH_KEYS = frozenset({"idempotency_key", "refund_id", "refund_ref", "amount"})


class EfnOnlineOrder(models.Model):
    _inherit = "efn.online.order"

    def _efn_payment_journal(self):
        self.ensure_one()
        if self.payment_mode == "counter":
            return self.company_id.sudo().efn_online_counter_journal_id
        return super()._efn_payment_journal()

    # ================================================================ BFF: SEC-145
    @api.model
    def efn_online_counter_refund_authorize(self, channel_code, platform_order_ref, authorization):
        """BFF: refund counter sudah lolos persetujuan (status BFF ``manual_required`` + ``counter_refund_via_pos``).

        ``authorization`` = ``{idempotency_key, refund_id (UUID refund BFF), refund_ref (UUID refund Odoo | null),
        amount (Rupiah bulat)}``. ``refund_ref`` diisi untuk refund bernota kredit (alur CS / pembatalan berfaktur,
        nominal = refund Odoo ``requested``); ``null`` hanya untuk pesanan ``cancelled`` tanpa faktur (nominal =
        seluruh pelunasan). Hasil ``{"state": "authorized", "amount"}``; replay kunci sama + payload sama = hasil
        sama. Error: ``not_found`` | ``invalid_state`` | ``amount_mismatch`` | ``idempotency_conflict`` |
        ``invalid_input``.
        """
        channel = self._efn_channel_env(channel_code)
        require(isinstance(authorization, dict) and set(authorization) == AUTH_KEYS)
        key = check_str(authorization["idempotency_key"], IDEMPOTENCY_KEY_RE)
        bff_refund_id = check_str(authorization["refund_id"], UUID_RE, 36).lower()
        refund_ref = authorization["refund_ref"]
        if refund_ref is not None:
            refund_ref = check_str(refund_ref, UUID_RE, 36).lower()
        amount = check_amount_int(authorization["amount"])
        require(amount > 0)
        order = self.with_env(channel.env)._efn_find(channel, platform_order_ref)
        if not order:
            raise online_error("not_found")
        Auth = order.env["efn.online.counter.refund.auth"].sudo()
        payload_sha = canonical_sha256(
            {"order": order.id, "refund_id": bff_refund_id, "refund_ref": refund_ref, "amount": amount}
        )
        existing = Auth.search([("idempotency_key", "=", key)], limit=1)
        if existing:
            if existing.payload_sha256 != payload_sha:
                raise online_error("idempotency_conflict")
            return existing._efn_result()
        if order.payment_mode != "counter":
            raise online_error("invalid_state")
        settlement = (
            order.env["efn.online.counter.settlement"].sudo().search([("online_order_id", "=", order.id)], limit=1)
        )
        if not settlement or settlement.refund_pos_order_id:
            raise online_error("invalid_state")
        if Auth.search_count([("bff_refund_id", "=", bff_refund_id)], limit=1):
            raise online_error("idempotency_conflict")
        currency = order.currency_id
        refund = order.env["efn.online.refund"]
        if refund_ref:
            refund = order.refund_ids.filtered(lambda r: r.refund_ref == refund_ref)
            if not refund:
                raise online_error("not_found")
            if refund.state != "requested":
                raise online_error("invalid_state")
            if Auth.search_count([("refund_id", "=", refund.id)], limit=1):
                raise online_error("idempotency_conflict")
            if currency.compare_amounts(refund.amount, amount) != 0:
                raise online_error("amount_mismatch")
        else:
            if order.state != "cancelled" or order._efn_posted_invoice():
                raise online_error("invalid_state")
            if currency.compare_amounts(settlement.amount, amount) != 0:
                raise online_error("amount_mismatch")
            if settlement.refund_auth_ids.filtered(lambda a: not a.refund_id):
                raise online_error("idempotency_conflict")
        if currency.compare_amounts(sum(settlement.refund_auth_ids.mapped("amount")) + amount, settlement.amount) > 0:
            raise online_error("amount_mismatch")
        auth = Auth.create(
            {
                "company_id": order.company_id.id,
                "settlement_id": settlement.id,
                "online_order_id": order.id,
                "refund_id": refund.id or False,
                "bff_refund_id": bff_refund_id,
                "idempotency_key": key,
                "payload_sha256": payload_sha,
                "amount": amount,
            }
        )
        _logger.info(
            "EFN bayar di kasir: refund pelunasan %s diotorisasi BFF (otorisasi %s, nota kredit=%s)",
            settlement.id,
            auth.id,
            bool(refund),
        )
        return auth._efn_result()
