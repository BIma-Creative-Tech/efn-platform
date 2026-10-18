"""E-45: validasi server saat sync order POS pelunasan pesanan online (pola E-30/E-31, docs/04 §12.2 no. 6/8).

Hook ``_process_saved_order`` (order tidak draft & tidak batal): validasi SEBELUM langkah standar (pelanggaran ->
``UserError``, sync di-rollback), lalu setelah order POS lunas -> jalur internal ``mark_paid`` pesanan online.

**Pelunasan** (order berisi produk pelunasan / baris berlabel pesanan, tanpa ``refunded_orderline_id``):
tepat satu baris = produk pelunasan PT order POS, qty 1, diskon 0, tanpa pajak; harga = subtotal = total order =
``amount_payable`` pesanan (toleransi 0); tanpa pelanggan & tanpa faktur POS; pesanan ``counter`` company sama,
outlet = gudang config, ada klaim lookup ``ok`` untuk config ini; belum dilunasi order POS lain. Setelah lunas:
``_efn_counter_mark_paid`` (``payment_ref`` = ``pos.order.uuid``, idempotensi ``pos_counter:<uuid>``) ->
``confirmed`` | ``stock_short`` | ``review``. Pesanan sudah batal/kedaluwarsa saat sync -> dibayar + ``review`` (L-56).

**Refund pelunasan** (baris menunjuk baris pelunasan): satu baris qty -1 senilai pelunasan, sekali per pelunasan,
dari config di **outlet (gudang) yang sama** dengan pelunasan asal (SEC-145). Diizinkan hanya dengan **otorisasi BFF**
(``efn.online.counter.refund.auth``, RPC ``efn_online_counter_refund_authorize`` setelah approver CS; SEC-145/L-59):
(a) Σ otorisasi bertaut refund online ``requested`` = nominal -> refund itu di-settle (pembayaran keluar jurnal
kliring) + event ``refund_settled``, atau (b) otorisasi tanpa nota kredit = nominal dan pesanan ``cancelled`` tanpa
faktur. Refund ``requested`` tanpa otorisasi (initiator ``bff`` sebelum approver, atau ``odoo`` dari pembatalan Sales
Manager) dan pesanan batal tanpa otorisasi ditolak. Metode uang keluar tetap dijaga E-31 (``efn_pos_refund_origin``).

SEC-105 tidak berubah: POS tidak memuat SO; baris pelunasan = produk layanan tanpa ``sale_order_origin_id``.
"""

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    # ------------------------------------------------------------- helpers
    def _efn_counter_products(self):
        return self.env["res.company"].sudo().search([]).mapped("efn_online_counter_product_id")

    def _efn_counter_reject(self, code, message):
        _logger.warning("EFN bayar di kasir: order POS %s ditolak saat sync (%s)", self.uuid, code)
        raise UserError(message)

    def _efn_counter_same(self, value1, value2):
        return self.currency_id.compare_amounts(value1, value2) == 0

    # ------------------------------------------------------------ validasi
    def _efn_counter_plan(self):
        """Rencana pelunasan/refund untuk order ini atau None (bukan order bayar di kasir)."""
        self.ensure_one()
        lines = self.lines.sudo()
        products = self._efn_counter_products()
        flagged = lines.filtered(lambda line: line.product_id in products or line.efn_online_order_label)
        origins = lines.refunded_orderline_id.order_id
        Settlement = self.env["efn.online.counter.settlement"].sudo()
        settled_origins = Settlement.search([("pos_order_id", "in", origins.ids)]) if origins else Settlement
        if settled_origins:
            return self._efn_counter_plan_refund(lines, settled_origins)
        if not flagged:
            return None
        if flagged.filtered("refunded_orderline_id"):
            self._efn_counter_reject(
                "refund_without_settlement", _("Baris pelunasan pesanan online ini tidak dapat direfund.")
            )
        return self._efn_counter_plan_sale(lines, flagged)

    def _efn_counter_plan_sale(self, lines, flagged):
        generic = _(
            "Pelunasan pesanan online harus berupa satu baris 'Pelunasan Pesanan Online' (qty 1, nominal dari kode "
            "bayar, tanpa diskon/pajak/pelanggan/faktur) dalam order tersendiri. Hapus baris lalu ulangi lewat "
            "tombol 'Bayar pesanan online'."
        )
        line = flagged[:1]
        if len(lines) != 1 or len(flagged) != 1:
            self._efn_counter_reject("not_single_line", generic)
        if line.product_id != self.company_id.sudo().efn_online_counter_product_id or not line.efn_online_order_label:
            self._efn_counter_reject("wrong_product", generic)
        if line.qty != 1 or line.discount or line.tax_ids:
            self._efn_counter_reject("line_modified", generic)
        if self.partner_id or self.to_invoice:
            self._efn_counter_reject("partner_or_invoice", generic)
        Orders = self.env["efn.online.order"].sudo().with_context(active_test=False)
        online = Orders.search(
            [
                ("name", "=", line.efn_online_order_label),
                ("company_id", "=", self.company_id.id),
                ("payment_mode", "=", "counter"),
            ],
            limit=2,
        )
        if len(online) != 1 or online.outlet_id.warehouse_id != self.config_id.sudo().picking_type_id.warehouse_id:
            self._efn_counter_reject("order_not_found", _("Pesanan online untuk pelunasan ini tidak ditemukan."))
        if not self.env["efn.online.counter.lookup"]._efn_has_claim(self.config_id, online):
            self._efn_counter_reject(
                "no_lookup_claim", _("Pesanan online ini belum dicari dengan kode bayar di POS ini. Ulangi lookup.")
            )
        online._efn_lock()
        amount = online.amount_payable
        if not all(
            self._efn_counter_same(value, amount)
            for value in (line.price_unit, line.price_subtotal, line.price_subtotal_incl, self.amount_total)
        ):
            self._efn_counter_reject(
                "amount_mismatch", _("Nominal pelunasan tidak sama dengan tagihan pesanan online (tidak boleh diubah).")
            )
        existing = self.env["efn.online.counter.settlement"].sudo().search([("online_order_id", "=", online.id)])
        if existing and existing.pos_order_uuid != self.uuid:
            self._efn_counter_reject("already_settled", _("Pesanan online ini sudah dilunasi di kasir."))
        payable = online._efn_counter_payable()
        late = online.state == "cancelled" or (online.state == "placed" and not payable)
        if not payable and not late:
            self._efn_counter_reject(
                "not_payable", _("Pesanan online ini tidak dalam status menunggu pembayaran (sudah dibayar/ditinjau).")
            )
        return {"kind": "sale", "online": online, "amount": amount, "existing": existing}

    def _efn_counter_plan_refund(self, lines, settlements):
        generic = _(
            "Refund pelunasan pesanan online hanya untuk seluruh nominal pelunasan, satu kali, di outlet tempat "
            "pesanan dilunasi, dan setelah refund disetujui CS di Super-App (approver)."
        )
        if len(settlements) != 1 or len(lines) != 1:
            self._efn_counter_reject("refund_shape", generic)
        settlement = settlements
        line = lines
        if line.refunded_orderline_id.order_id != settlement.pos_order_id or line.qty != -1:
            self._efn_counter_reject("refund_shape", generic)
        amount = -self.amount_total
        if not self._efn_counter_same(amount, settlement.amount):
            self._efn_counter_reject("refund_amount", generic)
        if settlement.refund_pos_order_id and settlement.refund_pos_order_id != self:
            self._efn_counter_reject("refund_twice", generic)
        # SEC-145: outlet = gudang config pelunasan asal (kasir mana pun di outlet itu; outlet lain ditolak).
        warehouse = self.config_id.sudo().picking_type_id.warehouse_id
        if not warehouse or warehouse != settlement.config_id.sudo().picking_type_id.warehouse_id:
            self._efn_counter_reject("refund_other_outlet", generic)
        online = settlement.online_order_id
        online._efn_lock()
        settlement.invalidate_recordset(["refund_auth_ids"])
        # SEC-145: refund `requested` saja TIDAK cukup (dibuat sebelum approver BFF, atau initiator=odoo dari
        # pembatalan Sales Manager). Wajib otorisasi BFF (`efn_online_counter_refund_authorize`) yang belum dipakai.
        auths = settlement.refund_auth_ids.filtered(lambda a: not a.consumed_pos_order_id)
        linked = auths.filtered(lambda a: a.refund_id and a.refund_id.state == "requested")
        if linked and self._efn_counter_same(sum(linked.mapped("amount")), amount):
            if all(self._efn_counter_same(a.amount, a.refund_id.amount) for a in linked):
                return {"kind": "refund", "settlement": settlement, "refunds": linked.refund_id, "auths": linked}
        unlinked = auths.filtered(lambda a: not a.refund_id)
        if (
            unlinked
            and self._efn_counter_same(sum(unlinked.mapped("amount")), amount)
            and online.state == "cancelled"
            and not online._efn_posted_invoice()
        ):
            no_refund = online.refund_ids.browse()
            return {"kind": "refund", "settlement": settlement, "refunds": no_refund, "auths": unlinked}
        self._efn_counter_reject("refund_not_approved", generic)

    # ------------------------------------------------------------- terapkan
    def _efn_counter_apply(self, plan):
        self.ensure_one()
        Settlement = self.env["efn.online.counter.settlement"].sudo()
        if plan["kind"] == "sale":
            online = plan["online"]
            result = online._efn_counter_mark_paid(
                payment_ref=self.uuid, idempotency_key=f"pos_counter:{self.uuid}", amount_paid=plan["amount"]
            )
            if not plan["existing"]:
                Settlement.create(
                    {
                        "company_id": self.company_id.id,
                        "online_order_id": online.id,
                        "pos_order_id": self.id,
                        "pos_order_uuid": self.uuid,
                        "config_id": self.config_id.id,
                        "session_id": self.session_id.id,
                        "amount": plan["amount"],
                        "online_state": result.get("order_state"),
                    }
                )
            _logger.info(
                "EFN bayar di kasir: order POS %s melunasi pesanan online %s -> %s",
                self.uuid,
                online.id,
                result.get("order_state"),
            )
            return
        settlement = plan["settlement"]
        now = fields.Datetime.now()
        for refund in plan["refunds"]:
            refund._efn_settle(f"pos_refund:{self.uuid}:{refund.refund_ref}", self.uuid, now, True)
            # L-58: event `refund_settled` -> BFF menandai refund counter lunas (bukan dari `refunded`).
            settlement.online_order_id._efn_emit("refund_settled", refund=refund)
        plan["auths"].write({"consumed_pos_order_id": self.id})
        settlement.write({"refund_pos_order_id": self.id, "refunded_at": now})
        _logger.info(
            "EFN bayar di kasir: refund POS %s atas pelunasan %s (%d refund online di-settle)",
            self.uuid,
            settlement.id,
            len(plan["refunds"]),
        )

    def _process_saved_order(self, draft):
        self.ensure_one()
        if draft or self.state == "cancel":
            return super()._process_saved_order(draft)
        plan = self._efn_counter_plan()
        result = super()._process_saved_order(draft)
        if plan:
            self._efn_counter_apply(plan)
        return result
