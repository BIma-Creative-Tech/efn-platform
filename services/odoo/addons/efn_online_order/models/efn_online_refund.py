"""Refund per SO (R-O05, ADR-019 §3): nominal = nota kredit Odoo, kumulatif <= dibayar untuk SO ini saja.

Nota kredit dibuat dengan mekanisme standar (``account.move._reverse_moves`` atas faktur online, lalu qty baris
disesuaikan untuk refund parsial) sehingga akun, pajak (fiscal position outlet), pembulatan kas, dan tautan baris SO
(``sale_line_ids``) identik dengan faktur. Tidak ada perhitungan pajak sendiri (ADR-027).
``refund_settled`` mencatat pembayaran keluar lewat jurnal titipan kanal (docs/08 §8.3 D.6).
Retur barang (``restock``) TIDAK diotomasi (docs/08 §8.3 G: picking/retur standar) -> aktivitas ke staf gudang.
"""

import json
import logging

from odoo import _, api, fields, models
from odoo.fields import Command

from .efn_online_common import REFUND_REASONS, online_error

_logger = logging.getLogger(__name__)


class EfnOnlineRefund(models.Model):
    _name = "efn.online.refund"
    _description = "EFN Online - Refund per pesanan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"
    _check_company_auto = True
    _rec_name = "refund_ref"

    order_id = fields.Many2one(
        "efn.online.order", required=True, readonly=True, index=True, ondelete="restrict", check_company=True
    )
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    refund_ref = fields.Char(required=True, readonly=True, copy=False, help="Id opak (UUID) untuk BFF.")
    idempotency_key = fields.Char(required=True, readonly=True, copy=False)
    payload_sha256 = fields.Char(readonly=True, copy=False)
    kind = fields.Selection([("lines", "Per baris"), ("full", "Penuh")], required=True, readonly=True)
    origin = fields.Selection([("refund", "Refund"), ("cancel", "Pembatalan")], required=True, readonly=True)
    initiator = fields.Selection([("bff", "BFF / CS"), ("odoo", "Odoo / ops")], required=True, readonly=True)
    initiator_user_id = fields.Many2one(
        "res.users",
        string="Dipicu oleh (Odoo)",
        readonly=True,
        help="SEC-95/L-16: user Odoo yang memicu refund initiator=odoo (aturan empat mata persetujuan di BFF).",
    )
    reason = fields.Selection([(r, r) for r in REFUND_REASONS], required=True, readonly=True)
    line_summary = fields.Text(readonly=True, help="JSON {product_id: qty} yang direfund (tanpa data pribadi).")
    restock = fields.Boolean(readonly=True)
    amount = fields.Monetary(readonly=True)
    credit_note_id = fields.Many2one("account.move", readonly=True, check_company=True)
    state = fields.Selection(
        [("requested", "Menunggu dana keluar"), ("settled", "Selesai"), ("failed", "Gagal")],
        required=True,
        default="requested",
        readonly=True,
        tracking=True,
    )
    provider_refund_ref = fields.Char(readonly=True, groups="base.group_system", copy=False)
    settle_idempotency_key = fields.Char(readonly=True, groups="base.group_system", copy=False)
    settled_at = fields.Datetime(readonly=True)
    settled_payment_id = fields.Many2one("account.payment", readonly=True, check_company=True)

    _refund_ref_unique = models.Constraint("unique (refund_ref)", "refund_ref harus unik.")
    _idempotency_key_unique = models.Constraint("unique (idempotency_key)", "Kunci idempotensi refund harus unik.")

    # ------------------------------------------------------------------ helpers
    def _efn_result(self, with_state=False):
        self.ensure_one()
        result = {
            "refund_ref": self.refund_ref,
            "amount": self.order_id._efn_amount_out(self.amount),
            "credit_note": self.credit_note_id.name or None,
        }
        if with_state:
            result.update({"state": self.state, "kind": self.kind})
        return result

    @api.model
    def _efn_remaining_qty(self, order, invoice):
        """{product_id: qty yang masih bisa direfund} = qty faktur - refund sebelumnya (selain gagal)."""
        remaining = {}
        for line in invoice.invoice_line_ids.filtered(lambda line: line.display_type == "product" and line.product_id):
            remaining[line.product_id.id] = remaining.get(line.product_id.id, 0.0) + line.quantity
        for refund in order.refund_ids.filtered(lambda r: r.state != "failed"):
            for pid, qty in json.loads(refund.line_summary or "{}").items():
                remaining[int(pid)] = remaining.get(int(pid), 0.0) - qty
        return remaining

    @api.model
    def _efn_create(self, order, idempotency_key, lines, restock, reason, initiator, origin, payload_sha256=None):
        """Buat & post nota kredit lalu catat refund. ``lines`` None = sisa penuh. Dipanggil dalam sudo."""
        invoice = order._efn_posted_invoice()
        if not invoice:
            raise online_error("invalid_state")
        remaining = self._efn_remaining_qty(order, invoice)
        if lines is None:
            requested = {pid: qty for pid, qty in remaining.items() if qty > 0}
            kind = "full"
        else:
            requested = dict(lines)
            kind = "lines"
            for pid, qty in requested.items():
                if pid not in remaining or qty > remaining[pid] + 1e-6:
                    raise online_error("invalid_input")
        if not requested:
            raise online_error("invalid_state")
        today = fields.Date.context_today(self)
        credit = invoice._reverse_moves(
            [{"ref": _("Refund online %(name)s", name=order.name), "invoice_date": today, "date": today}],
            cancel=False,
        )
        commands = []
        for line in credit.invoice_line_ids.filtered(lambda line: line.display_type == "product"):
            qty = requested.get(line.product_id.id, 0.0)
            if qty <= 0:
                commands.append(Command.unlink(line.id))
            elif abs(qty - line.quantity) > 1e-6:
                commands.append(Command.update(line.id, {"quantity": qty}))
        if commands:
            credit.write({"invoice_line_ids": commands})
        credit.action_post()
        amount = credit.amount_total
        paid = order.paid_amount or order.amount_payable
        if order.currency_id.compare_amounts(order._efn_refunded_total() + amount, paid) > 0:
            # Kumulatif melebihi porsi yang dibayar untuk SO ini (ADR-019 §3): batalkan seluruh transaksi.
            raise online_error("amount_mismatch")
        record = self.create(
            {
                "order_id": order.id,
                "company_id": order.company_id.id,
                "refund_ref": order._efn_new_refund_ref(),
                "idempotency_key": idempotency_key,
                "payload_sha256": payload_sha256,
                "kind": kind,
                "origin": origin,
                "initiator": initiator,
                "initiator_user_id": self.env.uid if initiator == "odoo" else False,
                "reason": reason,
                "line_summary": json.dumps({str(pid): qty for pid, qty in sorted(requested.items())}),
                "restock": restock,
                "amount": amount,
                "credit_note_id": credit.id,
            }
        )
        if restock:
            order._efn_schedule_activity(
                _("Retur barang pesanan online"), f"restock:{record.refund_ref}", "stock.group_stock_user"
            )
        _logger.info("EFN online refund %s order=%s kind=%s origin=%s", record.id, order.id, kind, origin)
        return record

    def _efn_settle(self, key, provider_ref, settled_at, ok):
        self.ensure_one()
        if self.state in ("settled", "failed"):
            if self.settle_idempotency_key == key:
                return {"state": self.state}
            raise online_error("idempotency_conflict" if self.state == "settled" else "invalid_state")
        values = {"provider_refund_ref": provider_ref, "settle_idempotency_key": key, "settled_at": settled_at}
        if not ok:
            values["state"] = "failed"
            self.write(values)
            self.order_id._efn_schedule_activity(
                _("Refund online gagal di gateway"), f"refund_failed:{self.refund_ref}", "account.group_account_manager"
            )
            return {"state": "failed"}
        payments = self.order_id._efn_register_payment(self.credit_note_id, self.amount)
        values.update({"state": "settled", "settled_payment_id": payments[:1].id})
        self.write(values)
        return {"state": "settled"}
