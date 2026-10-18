"""G19: E-35 reservasi stok saat place, E-36 promo online, E-37a brand_code kanal.

Kontrak: docs/04_SERVICE_CONTRACTS.md §11.1-§11.3 (menang atas §10 dan docs/08). Daftar uji: docs/08 §10.1 no. 7 dan
§10.2 no. 7. Semua angka = data uji (bukan konfigurasi produksi; ADR-027 tanpa tarif di kode).
"""

import hashlib
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_online_order.hooks import efn_online_disable_invalid_sale_programs

from .common import PICKUP_CODE, EfnOnlineCommon

# L-41 placeholder pilot (data uji, bukan konfigurasi produksi).
CONFIRM_MAX_ORDER_AMOUNT = 5_000_000


def _code(exc):
    return str(exc.exception.args[0])


class EfnOnlineG19Common(EfnOnlineCommon):
    FIXED_1000 = {
        "reward_type": "discount",
        "discount": 1000,
        "discount_mode": "per_order",
        "discount_applicability": "order",
        "required_points": 1,
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # L-41: mode confirm wajib batas nominal (> 0); max_line_qty default 20.
        cls.channel.write({"stock_hold_mode": "confirm", "max_order_amount": CONFIRM_MAX_ORDER_AMOUNT})
        cls.stock_user = new_test_user(
            cls.env,
            login="efn_g19_stock",
            groups="base.group_user,stock.group_stock_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    # ------------------------------------------------------------------ helpers
    def free(self, product=None):
        product = (product or self.p_rice).with_context(warehouse_id=self.warehouse.id)
        product.invalidate_recordset(["free_qty", "qty_available"])
        return product.free_qty

    def counts(self):
        return (
            self.env["sale.order"].sudo().search_count([]),
            self.env["efn.online.order"].sudo().search_count([]),
            self.env["loyalty.card"].sudo().search_count([]),
        )

    def consume_outside(self, remaining):
        """Stok keluar di luar SO online (setara penjualan kasir POS yang memaksa qty): penyesuaian inventaris standar
        -> ``stock.move.line._action_done`` -> ``_free_reservation`` atas reservasi yang belum ``picked``."""
        quant = (
            self.env["stock.quant"]
            .with_context(inventory_mode=True)
            .search(
                [("product_id", "=", self.p_rice.id), ("location_id", "=", self.warehouse.lot_stock_id.id)], limit=1
            )
        )
        quant.inventory_quantity = remaining
        quant.action_apply_inventory()
        self.env.invalidate_all()

    def program(self, name, program_type="promotion", code=None, pricelist=None, rule=None, reward=None, **extra):
        """Program promo online sah (sale_ok + pricelist kanal TST)."""
        pricelist = pricelist or self.pricelist
        with_code = bool(code) or program_type in ("promo_code", "coupons")
        rule_vals = dict(rule or {"minimum_amount": 0, "reward_point_amount": 1, "reward_point_mode": "order"})
        if code:
            rule_vals.update(mode="with_code", code=code)
        reward_vals = reward or {
            "reward_type": "discount",
            "discount": 10,
            "discount_mode": "percent",
            "discount_applicability": "order",
            "required_points": 1,
        }
        vals = {
            "name": name,
            "program_type": program_type,
            "trigger": "with_code" if with_code else "auto",
            "applies_on": "current",
            "company_id": self.company.id,
            "sale_ok": True,
            "pricelist_ids": [Command.set(pricelist.ids)],
            "rule_ids": [Command.clear()] if program_type == "coupons" else [Command.create(rule_vals)],
            "reward_ids": [Command.create(reward_vals)],
            **extra,
        }
        return self.env["loyalty.program"].create(vals)

    @staticmethod
    def reward_lines(quote):
        return [line for line in quote["lines"] if line.get("reward")]


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineReservationE35(EfnOnlineG19Common):
    """docs/08 §10.1 no. 7 / docs/04 §11.1."""

    def test_channel_defaults_and_hold_mode_allowed(self):
        channel = self.env["efn.online.channel"].create(
            {
                "name": "Kanal default",
                "code": "TSD",
                "company_id": self.company.id,
                "pricelist_id": self.pricelist.id,
                "partner_id": self.channel.partner_id.id,
                "journal_titipan_id": self.channel.journal_titipan_id.id,
            }
        )
        self.assertEqual(channel.payment_timeout_minutes, 70)
        self.assertEqual(channel.stock_hold_mode, "none")  # default tetap none (kanal lain tidak berubah diam-diam)
        self.assertEqual(channel.max_line_qty, 20)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            channel.stock_hold_mode = "confirm"  # L-41: tanpa max_order_amount
        channel.write({"stock_hold_mode": "confirm", "max_order_amount": CONFIRM_MAX_ORDER_AMOUNT})
        self.assertEqual(channel.stock_hold_mode, "confirm")
        with self.assertRaises(ValidationError), self.cr.savepoint():
            channel.fulfilment_delivery = True

    def test_place_confirm_reserves_stock(self):
        self.assertEqual(self.free(), 10.0)
        part, _payload, result = self.place()
        order = self.record(part)
        so = order.sale_order_id
        self.assertEqual(result["order_state"], "placed")
        self.assertEqual(order.state, "placed")
        self.assertEqual(so.state, "sale")
        moves = order._efn_reservation_moves()
        self.assertTrue(moves)
        self.assertEqual(set(moves.mapped("state")), {"assigned"})
        self.assertEqual(self.free(), 7.0)
        self.assertFalse(so.invoice_ids)
        delta = order.expires_at - fields.Datetime.now()
        self.assertTrue(timedelta(minutes=69) < delta <= timedelta(minutes=70), delta)
        self.assertEqual(order.event_ids.mapped("event"), ["placed"])

    def test_place_beyond_remaining_rolls_back_without_so(self):
        self.place()
        self.assertEqual(self.free(), 7.0)
        before = self.counts()
        # (a) cek free_qty kanal menolak lebih dulu.
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.p_rice.id, "qty": 8}], quote_hash="0" * 64)
        self.assertEqual(_code(exc), "efn_online:out_of_stock")
        # (b) reservasi tidak penuh setelah konfirmasi (cek free_qty dimatikan): savepoint place di-rollback.
        self.channel.stock_check = "none"
        quote = self.quote([{"product_id": self.p_rice.id, "qty": 8}])
        self.assertEqual(quote["issues"], [])
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.p_rice.id, "qty": 8}], quote_hash=quote["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:out_of_stock")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.free(), 7.0)

    def test_expiry_releases_reservation_and_late_payment_refund(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        moves = order._efn_reservation_moves()
        self.assertEqual(self.free(), 7.0)
        order.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env["efn.online.order"]._cron_efn_online_expire()
        self.assertEqual(order.state, "cancelled")
        self.assertEqual(order.cancel_reason, "payment_expired")
        self.assertEqual(order.sale_order_id.state, "cancel")
        self.assertEqual(set(moves.mapped("state")), {"cancel"})
        self.assertEqual(self.free(), 10.0)
        self.assertEqual(self.pay(part), {"order_state": "cancelled", "refund_required": "full"})

    def test_customer_cancel_before_payment_releases(self):
        part, _payload, _result = self.place()
        result = self.order_api().efn_online_cancel("TST", part, "customer", f"online:cancel:{part}")
        self.assertEqual(result, {"order_state": "cancelled"})
        self.assertEqual(self.record(part).sale_order_id.state, "cancel")
        self.assertEqual(self.free(), 10.0)

    def test_mark_paid_confirm_mode_invoices_without_reconfirm(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        so = order.sale_order_id
        pickings = so.picking_ids
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        self.assertEqual(so.state, "sale")
        self.assertEqual(so.picking_ids, pickings)
        self.assertEqual(order.invoice_id.state, "posted")
        self.assertEqual(order.invoice_id.amount_total, 33297.0)
        self.assertIn(order.invoice_id.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.free(), 7.0)

    def test_pos_sale_revokes_reservation_then_stock_short(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        self.consume_outside(1.0)
        moves = order._efn_reservation_moves()
        self.assertNotEqual(set(moves.mapped("state")), {"assigned"})
        result = self.pay(part)
        self.assertEqual(result["order_state"], "stock_short")
        self.assertEqual(result["short_lines"], [{"product_id": self.p_rice.id, "requested": 3.0, "available": 1.0}])
        self.assertFalse(order.sale_order_id.invoice_ids)
        cancel = self.order_api().efn_online_cancel("TST", part, "stock_short", f"online:cancel:{part}")
        self.assertEqual(cancel, {"order_state": "cancelled"})
        self.assertEqual(order.sale_order_id.state, "cancel")

    def test_ready_marks_picked_and_survives_pos_sale(self):
        part, _payload, _result = self.place()
        self.pay(part)
        order = self.record(part)
        order.with_user(self.stock_user).action_efn_ready()
        self.assertEqual(order.state, "ready")
        moves = order._efn_reservation_moves()
        self.assertTrue(moves)
        self.assertTrue(all(moves.mapped("picked")))
        self.consume_outside(1.0)
        moves = order._efn_reservation_moves()
        self.assertEqual(set(moves.mapped("state")), {"assigned"})
        self.assertEqual(sum(moves.mapped("quantity")), 3.0)

    def test_confirm_mode_mixed_lines_ready_then_handover(self):
        lines = [{"product_id": self.p_rice.id, "qty": 2}, {"product_id": self.p_bag.id, "qty": 1}]
        part, _payload, _result = self.place(lines=lines)
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        order = self.record(part)
        order.with_user(self.stock_user).action_efn_ready()
        ok, _message = order._efn_handover(PICKUP_CODE)
        self.assertTrue(ok)
        self.assertEqual(order.state, "picked_up")
        customer_moves = order.sale_order_id.order_line.move_ids
        self.assertEqual(set(customer_moves.mapped("state")), {"done"})
        self.assertEqual(self.free(), 8.0)

    def test_mode_none_regression(self):
        self.channel.stock_hold_mode = "none"
        part, _payload, _result = self.place()
        order = self.record(part)
        self.assertEqual(order.sale_order_id.state, "draft")
        self.assertEqual(self.free(), 10.0)
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        self.assertEqual(self.free(), 7.0)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlinePromotionsE36(EfnOnlineG19Common):
    """docs/08 §10.2 no. 7 / docs/04 §11.2."""

    def test_auto_promo_only_on_brand_pricelist_and_total_kept_on_confirm(self):
        self.program("Diskon brand A 10%")
        quote = self.quote()
        rewards = self.reward_lines(quote)
        self.assertEqual(len(rewards), 1)
        self.assertEqual(rewards[0]["name"], "Diskon brand A 10%")
        self.assertLess(rewards[0]["subtotal_incl"], 0)
        self.assertEqual(rewards[0]["tax_labels"], ["PPN 11%"])
        self.assertTrue(rewards[0]["available"])
        normal = [line for line in quote["lines"] if not line.get("reward")]
        self.assertNotIn("reward", normal[0])
        self.assertAlmostEqual(quote["amount_total"], 29967.0, delta=1.0)
        self.assertEqual((quote["promo_applied"], quote["promo_rejected"]), ([], []))
        # Brand B (kanal lain, company sama, pricelist lain) tidak mendapat promo brand A.
        tsc, tsc_user, _tax, _wh, _inc = self._make_channel(self.company, "TSC", "efn_online_tsc", self.company_data)
        self.p_rice.product_tmpl_id.efn_online_channel_ids = [Command.link(tsc.id)]
        other = self.channel_api(tsc_user).efn_online_quote(
            "TSC", "TOKO-T1", "pickup", [{"product_id": self.p_rice.id, "qty": 3}]
        )
        self.assertFalse(self.reward_lines(other))
        self.assertAlmostEqual(other["amount_total"], 33296.67)
        # Place + bayar: total tidak berubah saat konfirmasi (regresi #37).
        part, _payload, result = self.place(quote_hash=quote["quote_hash"])
        self.assertEqual(result["amount_payable"], quote["amount_payable"])
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        order = self.record(part)
        self.assertEqual(order.sale_order_id.amount_total, order.amount_total)
        self.assertEqual(order.invoice_id.amount_total, quote["amount_payable"])

    def test_quote_with_promo_leaves_no_db_trace(self):
        self.program("Diskon otomatis")
        # Reward kode = potongan nominal (bukan diskon global %) agar tidak bersaing dengan diskon global otomatis.
        self.program("Kode hemat", program_type="promo_code", code="HEMAT10", reward=self.FIXED_1000)
        before = self.counts()
        quote = self.quote(promo_codes=["HEMAT10"])
        self.assertEqual(quote["promo_applied"], ["HEMAT10"])
        self.assertEqual(self.counts(), before)

    def test_promo_code_valid_unknown_duplicate(self):
        self.program("Kode hemat", program_type="promo_code", code="HEMAT10")
        quote = self.quote(promo_codes=["hemat10", "TIDAKADA", "HEMAT10"])
        self.assertEqual(quote["promo_applied"], ["HEMAT10"])
        self.assertEqual(quote["promo_rejected"], ["TIDAKADA"])
        self.assertEqual(len(self.reward_lines(quote)), 1)
        self.assertNotIn("error", json.dumps(quote).lower())
        self.assertNotEqual(quote["quote_hash"], self.quote()["quote_hash"])
        part, _payload, result = self.place(quote_hash=quote["quote_hash"], promo_codes=["HEMAT10"])
        self.assertEqual(result["amount_payable"], quote["amount_payable"])
        order = self.record(part)
        self.assertTrue(order.sale_order_id.order_line.filtered("reward_id"))
        self.assertEqual(order.sale_order_id.state, "sale")

    def test_promo_code_expired_and_quota_generic_rejection(self):
        today = fields.Date.context_today(self.env.user)
        self.program(
            "Kode lama",
            program_type="promo_code",
            code="LAMA10",
            date_from=today - timedelta(days=10),
            date_to=today - timedelta(days=1),
        )
        self.program("Kode kuota", program_type="promo_code", code="KUOTA1", limit_usage=True, max_usage=1)
        expired = self.quote(promo_codes=["LAMA10"])
        self.assertEqual((expired["promo_applied"], expired["promo_rejected"]), ([], ["LAMA10"]))
        first = self.quote(promo_codes=["KUOTA1"])
        self.assertEqual(first["promo_applied"], ["KUOTA1"])
        self.place(quote_hash=first["quote_hash"], promo_codes=["KUOTA1"])
        exhausted = self.quote(promo_codes=["KUOTA1"])
        self.assertEqual((exhausted["promo_applied"], exhausted["promo_rejected"]), ([], ["KUOTA1"]))
        unknown = self.quote(promo_codes=["ZZZ999"])
        # Anti-enumerasi: bentuk respons identik untuk kedaluwarsa / kuota habis / tidak dikenal.
        for quote in (expired, exhausted, unknown):
            self.assertEqual(set(quote), set(unknown))
            self.assertFalse(self.reward_lines(quote))
            self.assertEqual(quote["quote_hash"], unknown["quote_hash"])

    def test_promo_codes_input_validation(self):
        for bad in (["A1", "B2", "C3", "D4"], ["bad code"], ["x" * 41], "HEMAT10", [123], [""], ["KODE!"]):
            with self.subTest(bad=bad), self.assertRaises(UserError) as exc:
                self.quote(promo_codes=bad)
            self.assertEqual(_code(exc), "efn_online:invalid_input")

    def test_mode_none_rejects_all_codes(self):
        self.channel.stock_hold_mode = "none"
        self.program("Kode hemat", program_type="promo_code", code="HEMAT10")
        quote = self.quote(promo_codes=["HEMAT10"])
        self.assertEqual((quote["promo_applied"], quote["promo_rejected"]), ([], ["HEMAT10"]))
        self.assertFalse(self.reward_lines(quote))
        part, _payload, _result = self.place(quote_hash=quote["quote_hash"], promo_codes=["HEMAT10"])
        so = self.record(part).sale_order_id
        self.assertEqual(so.state, "draft")
        self.assertFalse(so.order_line.filtered("reward_id"))

    def test_auto_promo_still_applies_in_mode_none_only_codes_rejected(self):
        """L-38: promo OTOMATIS tetap berlaku di kanal mode ``none``; hanya KODE yang ditolak (§11.2 no. 8)."""
        self.channel.stock_hold_mode = "none"
        self.program("Diskon otomatis mode none")
        self.program("Kode hemat", program_type="promo_code", code="HEMAT10", reward=self.FIXED_1000)
        plain_total = self.quote(promo_codes=[])["amount_total"]
        quote = self.quote(promo_codes=["HEMAT10"])
        self.assertEqual((quote["promo_applied"], quote["promo_rejected"]), ([], ["HEMAT10"]))
        rewards = self.reward_lines(quote)
        self.assertEqual([line["name"] for line in rewards], ["Diskon otomatis mode none"])
        self.assertEqual(quote["amount_total"], plain_total)  # kode tidak menambah potongan apa pun
        # BFF mengirim promo_applied grup ([]): SO draf tetap memuat reward otomatis dan hash cocok dengan quote.
        part, _payload, result = self.place(quote_hash=quote["quote_hash"], promo_codes=[])
        order = self.record(part)
        self.assertEqual(order.sale_order_id.state, "draft")
        self.assertTrue(order.sale_order_id.order_line.filtered("reward_id"))
        self.assertEqual(result["amount_payable"], quote["amount_payable"])
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        self.assertEqual(order.invoice_id.amount_total, quote["amount_payable"])

    def test_hash_changes_when_promo_ends_between_quote_and_place(self):
        program = self.program("Kode singkat", program_type="promo_code", code="SINGKAT")
        quote = self.quote(promo_codes=["SINGKAT"])
        self.assertEqual(quote["promo_applied"], ["SINGKAT"])
        program.active = False
        before = self.counts()
        with self.assertRaises(UserError) as exc:
            self.place(quote_hash=quote["quote_hash"], promo_codes=["SINGKAT"])
        self.assertEqual(_code(exc), "efn_online:price_changed")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.free(), 10.0)

    def test_single_use_coupon_one_place_wins_and_cancel_restores(self):
        program = self.program("Kupon sekali pakai", program_type="coupons")
        card = self.env["loyalty.card"].create({"program_id": program.id, "points": 1, "code": "KUPON-UJI1"})
        quote_a = self.quote(promo_codes=["kupon-uji1"])
        quote_b = self.quote(promo_codes=["KUPON-UJI1"])
        self.assertEqual(quote_a["promo_applied"], ["KUPON-UJI1"])
        self.assertEqual(quote_a["quote_hash"], quote_b["quote_hash"])
        part_a, _pa, _ra = self.place(quote_hash=quote_a["quote_hash"], promo_codes=["KUPON-UJI1"])
        card.invalidate_recordset()
        self.assertEqual(card.points, 0)
        # Checkout kedua dengan quote yang sama -> kupon sudah terpakai -> hash beda -> price_changed, tanpa SO.
        before = self.counts()
        with self.assertRaises(UserError) as exc:
            self.place(quote_hash=quote_b["quote_hash"], promo_codes=["KUPON-UJI1"])
        self.assertEqual(_code(exc), "efn_online:price_changed")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.quote(promo_codes=["KUPON-UJI1"])["promo_rejected"], ["KUPON-UJI1"])
        # Batal sebelum bayar -> kupon dikembalikan sale_loyalty._action_cancel.
        self.order_api().efn_online_cancel("TST", part_a, "customer", f"online:cancel:{part_a}")
        card.invalidate_recordset()
        self.assertEqual(card.points, 1)
        self.assertEqual(self.quote(promo_codes=["KUPON-UJI1"])["promo_applied"], ["KUPON-UJI1"])

    def test_generated_lowercase_coupon_code_resolves(self):
        program = self.program("Kupon buatan Odoo", program_type="coupons")
        card = self.env["loyalty.card"].create({"program_id": program.id, "points": 1})
        code = card.code.upper()
        quote = self.quote(promo_codes=[code])
        self.assertEqual(quote["promo_applied"], [code])

    def test_code_making_payable_below_one_rupiah_rejected(self):
        """L-35: reward kode yang membuat tagihan grup < Rp1 dibatalkan; kode masuk promo_rejected."""
        self.program(
            "Gratis total",
            program_type="promo_code",
            code="GRATIS100",
            reward={
                "reward_type": "discount",
                "discount": 100,
                "discount_mode": "percent",
                "discount_applicability": "order",
                "required_points": 1,
            },
        )
        plain = self.quote()
        quote = self.quote(promo_codes=["GRATIS100"])
        self.assertEqual((quote["promo_applied"], quote["promo_rejected"]), ([], ["GRATIS100"]))
        self.assertFalse(self.reward_lines(quote))
        self.assertEqual(quote["amount_payable"], plain["amount_payable"])
        self.assertEqual(quote["quote_hash"], plain["quote_hash"])
        part, _payload, _result = self.place(quote_hash=quote["quote_hash"], promo_codes=["GRATIS100"])
        self.assertFalse(self.record(part).sale_order_id.order_line.filtered("reward_id"))

    def test_reward_lines_shape_for_bff(self):
        """Batas parser BFF: baris reward `reward: true`, subtotal_incl <= 0, maks 20 per grup."""
        self.program("Diskon otomatis")
        self.program("Kode hemat", program_type="promo_code", code="HEMAT5", reward=self.FIXED_1000)
        quote = self.quote(promo_codes=["HEMAT5"])
        rewards = self.reward_lines(quote)
        self.assertTrue(rewards)
        self.assertLessEqual(len(rewards), 20)
        for line in rewards:
            self.assertIs(line["reward"], True)
            self.assertLessEqual(line["subtotal_incl"], 0)
        self.assertFalse(set(quote["promo_applied"]) & set(quote["promo_rejected"]))
        self.assertLessEqual(set(quote["promo_applied"]) | set(quote["promo_rejected"]), {"HEMAT5"})

    def test_code_of_other_brand_or_pos_program_rejected(self):
        tsc, _user, _tax, _wh, _inc = self._make_channel(self.company, "TSC", "efn_online_tsc", self.company_data)
        self.program("Kode brand B", program_type="promo_code", code="BRANDB", pricelist=tsc.pricelist_id)
        quote = self.quote(promo_codes=["BRANDB"])
        self.assertEqual(quote["promo_rejected"], ["BRANDB"])
        self.assertFalse(self.reward_lines(quote))

    def test_buy_x_get_y_reward_line(self):
        self.program(
            "Beli 2 beras gratis kantong",
            program_type="buy_x_get_y",
            rule={
                "product_ids": [Command.set(self.p_rice.ids)],
                "reward_point_mode": "unit",
                "reward_point_amount": 1,
                "minimum_qty": 2,
            },
            reward={
                "reward_type": "product",
                "reward_product_id": self.p_bag.id,
                "reward_product_qty": 1,
                "required_points": 2,
            },
        )
        lines = [{"product_id": self.p_rice.id, "qty": 2}]
        quote = self.quote(lines)
        rewards = self.reward_lines(quote)
        self.assertEqual(len(rewards), 1)
        self.assertEqual(rewards[0]["product_id"], self.p_bag.id)
        self.assertEqual(rewards[0]["qty"], 1)
        self.assertEqual(rewards[0]["subtotal_incl"], 0.0)
        self.assertEqual(rewards[0]["tax_labels"], ["PPN 11%"])
        self.assertAlmostEqual(quote["amount_total"], 22197.78)
        part, _payload, _result = self.place(lines=lines, quote_hash=quote["quote_hash"])
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        self.assertEqual(self.record(part).invoice_id.amount_total, quote["amount_payable"])

    def test_promo_codes_in_payload_sha(self):
        self.program("Kode hemat", program_type="promo_code", code="HEMAT10")
        quote = self.quote(promo_codes=["HEMAT10"])
        part, payload, result = self.place(quote_hash=quote["quote_hash"], promo_codes=["HEMAT10"])
        self.assertEqual(self.order_api().efn_online_place("TST", payload), result)
        without = {k: v for k, v in payload.items() if k != "promo_codes"}
        for changed in (dict(payload, promo_codes=[]), without):
            with self.subTest(changed=sorted(changed)), self.assertRaises(UserError) as exc:
                self.order_api().efn_online_place("TST", changed)
            self.assertEqual(_code(exc), "efn_online:idempotency_conflict")
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_place("TST", dict(payload, promo_codes="HEMAT10"))
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        self.assertEqual(self.record(part).state, "placed")

    def test_promo_code_never_logged_raw(self):
        """L-42 / SEC-132: log promo hanya jumlah kode/terpakai; tanpa kode asli maupun tag hash."""
        self.program("Kode rahasia", program_type="promo_code", code="RAHASIA77")
        with self.assertLogs("odoo.addons.efn_online_order", level="DEBUG") as logs:
            self.quote(promo_codes=["RAHASIA77", "SALAH12345"])
            self.place(quote_hash=self.quote(promo_codes=["RAHASIA77"])["quote_hash"], promo_codes=["RAHASIA77"])
        text = "\n".join(logs.output)
        self.assertIn("codes=2 applied=1", text)
        self.assertNotIn("refs=", text)
        for code in ("RAHASIA77", "SALAH12345"):
            self.assertNotIn(code, text)
            self.assertNotIn(code.lower(), text)
            self.assertNotIn(hashlib.sha256(code.encode("utf-8")).hexdigest()[:8], text)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineLoyaltyProgramRules(EfnOnlineCommon):
    """docs/04 §11.2 no. 7: constraint program loyalty yang boleh berlaku di SO online."""

    def _vals(self, **extra):
        vals = {
            "name": f"Program uji {uuid.uuid4().hex[:6]}",
            "program_type": "promotion",
            "company_id": self.company.id,
            "sale_ok": True,
            "pricelist_ids": [Command.set(self.pricelist.ids)],
        }
        if "pos_ok" in self.env["loyalty.program"]._fields:
            vals["pos_ok"] = False
        vals.update(extra)
        return vals

    def test_default_sale_ok_false(self):
        program = self.env["loyalty.program"].create(
            {"name": "Promo POS baru", "program_type": "promotion", "company_id": self.company.id}
        )
        self.assertFalse(program.sale_ok)

    def test_forbidden_types_and_missing_pricelist_rejected(self):
        Program = self.env["loyalty.program"]
        for program_type in ("loyalty", "ewallet", "gift_card", "next_order_coupons"):
            with self.subTest(program_type=program_type), self.assertRaises(ValidationError), self.cr.savepoint():
                Program.create(self._vals(program_type=program_type))
        with self.assertRaises(ValidationError), self.cr.savepoint():
            Program.create(self._vals(pricelist_ids=[Command.clear()]))
        not_online = self.env["product.pricelist"].create(
            {"name": "Bukan kanal online", "currency_id": self.currency.id, "company_id": self.company.id}
        )
        with self.assertRaises(ValidationError), self.cr.savepoint():
            Program.create(self._vals(pricelist_ids=[Command.set(not_online.ids)]))
        if "efn_requires_otp" in Program._fields:
            with self.assertRaises(ValidationError), self.cr.savepoint():
                Program.create(self._vals(efn_requires_otp=True))
        valid = Program.create(self._vals())
        self.assertTrue(valid.sale_ok)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            valid.pricelist_ids = [Command.clear()]
        # Program POS biasa (sale_ok=False) bebas dari aturan online.
        pos_only = Program.create(self._vals(sale_ok=False, pricelist_ids=[Command.clear()], program_type="loyalty"))
        self.assertFalse(pos_only.sale_ok)

    def test_legacy_invalid_programs_disabled_by_hook(self):
        Program = self.env["loyalty.program"]
        legacy = Program.create(self._vals(sale_ok=False, program_type="loyalty", pricelist_ids=[Command.clear()]))
        valid = Program.create(self._vals())
        self.env.flush_all()
        self.env.cr.execute("UPDATE loyalty_program SET sale_ok = true WHERE id = %s", (legacy.id,))
        self.env.invalidate_all()
        disabled = efn_online_disable_invalid_sale_programs(self.env)
        self.assertIn(legacy, disabled)
        self.assertNotIn(valid, disabled)
        self.assertFalse(legacy.sale_ok)
        self.assertTrue(valid.sale_ok)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineBrandCodeE37a(EfnOnlineCommon):
    """docs/04 §11.3 no. 2."""

    def test_brand_code_validation_not_unique_and_catalog(self):
        self.channel.brand_code = "GL"
        self.channel_2.brand_code = "GL"  # tidak unik: brand sama di PT lain (mis. Bacha butik & kafe)
        for bad in ("gl", "G", "1GL", "GL-1", "ABCDEFGHIJK"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError), self.cr.savepoint():
                self.channel.brand_code = bad
        self.assertEqual(self.channel.brand_code, "GL")
        block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertEqual(block["brand_code"], "GL")
        self.channel.brand_code = False
        self.assertIsNone(self.channel_api().efn_online_catalog("TST")["channel"]["brand_code"])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineOneUserPerChannelL37(EfnOnlineCommon):
    """L-37: satu user teknis per kanal (API key per kanal, rotasi mencabut key per user)."""

    def test_technical_user_cannot_serve_two_channels(self):
        tsc, tsc_user, _tax, _wh, _inc = self._make_channel(self.company, "TSC", "efn_online_tsc", self.company_data)
        with self.assertRaises(ValidationError), self.cr.savepoint():
            tsc.user_ids = [Command.link(self.tech_user.id)]
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.channel.user_ids = [Command.link(tsc_user.id)]
        self.assertEqual(tsc.user_ids, tsc_user)
        # Mengganti user kanal (lepas lama, pasang baru) tetap boleh.
        tsc.user_ids = [Command.clear()]
        self.channel.user_ids = [Command.link(tsc_user.id)]
        self.assertIn(tsc_user, self.channel.user_ids)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineSecurityR12(EfnOnlineG19Common):
    """Putaran perbaikan r12 (docs/04 §11.7 L-40..L-44): SEC-130, SEC-131, SEC-139, QA G19 #1/#3."""

    # ------------------------------------------------------------------ helpers
    def _xmin(self, program):
        """Versi baris ``loyalty_program`` (xmin berubah hanya bila baris di-UPDATE, bukan oleh FOR UPDATE)."""
        self.env.flush_all()
        self.env.cr.execute("SELECT xmin::text FROM loyalty_program WHERE id = %s", (program.id,))
        return self.env.cr.fetchone()[0]

    def _spy_promo(self):
        """Rekam urutan kunci kuota (L-40) vs penerapan promo; kembalikan (patchers, events)."""
        Channel = type(self.env["efn.online.channel"])
        lock_original = Channel._efn_online_lock_quota_programs
        apply_original = Channel._efn_online_apply_promotions
        events = []

        def lock(channel, codes, programs):
            locked = lock_original(channel, codes, programs)
            events.append(("lock", sorted(locked.ids)))
            return locked

        def apply(channel, order, codes, programs, outlet):
            events.append(("apply",))
            return apply_original(channel, order, codes, programs, outlet)

        patchers = (
            patch.object(Channel, "_efn_online_lock_quota_programs", lock),
            patch.object(Channel, "_efn_online_apply_promotions", apply),
        )
        return patchers, events

    # ------------------------------------------------------------- SEC-130 / L-40
    def test_place_locks_quota_code_program_before_promotions(self):
        quota = self.program("Kode kuota", program_type="promo_code", code="KUOTA5", limit_usage=True, max_usage=5)
        self.program("Kode bebas", program_type="promo_code", code="BEBAS5", reward=self.FIXED_1000)
        before = self._xmin(quota)
        (lock_patch, apply_patch), events = self._spy_promo()
        with lock_patch, apply_patch:
            quote = self.quote(promo_codes=["KUOTA5"])
            self.assertEqual(quote["promo_applied"], ["KUOTA5"])
            # Quote tidak mengunci kuota (hanya FOR UPDATE NOWAIT sale_loyalty di savepoint yang di-rollback).
            self.assertNotIn("lock", [event[0] for event in events])
            self.assertEqual(self._xmin(quota), before)
            del events[:]
            self.place(quote_hash=quote["quote_hash"], promo_codes=["KUOTA5"])
            self.assertEqual(events[0], ("lock", [quota.id]))  # UPDATE terjadi SEBELUM promo dihitung
            self.assertIn(("apply",), events[1:])
            self.assertNotEqual(self._xmin(quota), before)  # baris benar-benar di-UPDATE (bukan sekadar dikunci)
            # Kode tanpa limit_usage: tidak ada program yang dikunci.
            other = self.quote(promo_codes=["BEBAS5"])
            self.assertEqual(other["promo_applied"], ["BEBAS5"])
            del events[:]
            self.place(quote_hash=other["quote_hash"], promo_codes=["BEBAS5"])
            self.assertEqual(events[0], ("lock", []))

    def test_place_locks_automatic_quota_promo_and_quota_still_enforced(self):
        auto = self.program("Otomatis kuota", limit_usage=True, max_usage=1)
        quote = self.quote()
        self.assertEqual(len(self.reward_lines(quote)), 1)
        before = self._xmin(auto)
        (lock_patch, apply_patch), events = self._spy_promo()
        with lock_patch, apply_patch:
            self.place(quote_hash=quote["quote_hash"])
        self.assertEqual(events[0], ("lock", [auto.id]))
        self.assertNotEqual(self._xmin(auto), before)
        # Berurutan: kuota 1 sudah terpakai -> promo otomatis tidak berlaku lagi -> quote lama = price_changed.
        self.assertFalse(self.reward_lines(self.quote()))
        with self.assertRaises(UserError) as exc:
            self.place(quote_hash=quote["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:price_changed")

    # ------------------------------------------------------------- SEC-131 / L-41
    def test_confirm_mode_requires_order_amount_and_line_qty(self):
        Channel = self.env["efn.online.channel"]
        vals = {
            "name": "Kanal batas",
            "code": "TSE",
            "company_id": self.company.id,
            "pricelist_id": self.pricelist.id,
            "partner_id": self.channel.partner_id.id,
            "journal_titipan_id": self.channel.journal_titipan_id.id,
            "stock_hold_mode": "confirm",
        }
        for extra in ({}, {"max_order_amount": 0}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError), self.cr.savepoint():
                Channel.create({**vals, **extra})
        channel = Channel.create({**vals, "max_order_amount": CONFIRM_MAX_ORDER_AMOUNT})
        self.assertEqual(channel.max_line_qty, 20)
        for bad in ({"max_line_qty": 0}, {"max_line_qty": 10000}, {"max_order_amount": 0}, {"max_order_amount": -1}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError), self.cr.savepoint():
                channel.write(bad)
        # Mode tanpa reservasi: 0 = tanpa batas nominal tetap boleh.
        channel.write({"stock_hold_mode": "none", "max_order_amount": 0})
        self.assertEqual(channel.max_order_amount, 0)

    def test_qty_limit_issue_quote_and_place(self):
        self.channel.max_line_qty = 2
        lines = [{"product_id": self.p_rice.id, "qty": 3}, {"product_id": self.p_bag.id, "qty": 2}]
        quote = self.quote(lines)
        self.assertEqual(quote["issues"], [{"product_id": self.p_rice.id, "code": "qty_limit"}])
        self.assertTrue(all(line["available"] for line in quote["lines"]))  # bukan masalah stok
        before = self.counts()
        with self.assertRaises(UserError) as exc:
            self.place(lines=lines, quote_hash=quote["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.free(), 10.0)
        catalog = self.channel_api().efn_online_catalog("TST")
        self.assertEqual({item["max_qty"] for item in catalog["items"]}, {2})
        # Batas nominal mode confirm tetap issue order_limit di quote dan invalid_input di place.
        self.channel.write({"max_line_qty": 20, "max_order_amount": 1000})
        limited = self.quote()
        self.assertEqual(limited["issues"], [{"product_id": None, "code": "order_limit"}])
        with self.assertRaises(UserError) as exc:
            self.place(quote_hash=limited["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        # Mode tanpa reservasi: max_line_qty tidak berlaku.
        self.channel.write({"stock_hold_mode": "none", "max_order_amount": 0, "max_line_qty": 2})
        self.assertEqual(self.quote(lines)["issues"], [])
        catalog = self.channel_api().efn_online_catalog("TST")
        self.assertEqual({item["max_qty"] for item in catalog["items"]}, {99})

    # ------------------------------------------------------------- SEC-139 / L-43
    def test_unpaid_confirm_order_cannot_be_invoiced(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        so = order.sale_order_id
        self.assertEqual(so.state, "sale")
        with self.assertRaises(UserError) as exc, self.cr.savepoint():
            so._create_invoices()
        self.assertIn("belum dibayar", str(exc.exception))
        # Titik guard bersama faktur biasa + uang muka (wizard uang muka memanggil _prepare_invoice langsung).
        with self.assertRaises(UserError), self.cr.savepoint():
            so._prepare_invoice()
        wizard = (
            self.env["sale.advance.payment.inv"]
            .with_context(active_model="sale.order", active_ids=so.ids)
            .create({"advance_payment_method": "delivered"})
        )
        with self.assertRaises(UserError) as exc, self.cr.savepoint():
            wizard.create_invoices()
        self.assertIn("belum dibayar", str(exc.exception))
        self.assertFalse(so.invoice_ids)
        # Jalur saga setelah bayar tidak terhalang.
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        self.assertEqual(order.invoice_id.state, "posted")

    def _post_invoice_without_payment(self, order):
        """Simulasi faktur ter-posting yang dibuat staf SEBELUM guard ada (data lama): penanda bayar dipasang
        sementara hanya untuk melewati guard ``_prepare_invoice``, lalu dihapus lagi."""
        order.sudo().write({"paid_idempotency_key": "online:paid:simulasi-data-lama"})
        invoice = order.sale_order_id._create_invoices()
        invoice.action_post()
        order.sudo().write({"paid_idempotency_key": False})
        return invoice

    def test_cancel_or_expiry_of_unpaid_order_never_refunds(self):
        part_a, _pa, _ra = self.place()
        part_b, _pb, _rb = self.place(lines=[{"product_id": self.p_rice.id, "qty": 2}])
        order_a, order_b = self.record(part_a), self.record(part_b)
        invoices = self._post_invoice_without_payment(order_a) | self._post_invoice_without_payment(order_b)
        # (a) batal pelanggan lewat BFF.
        result = self.order_api().efn_online_cancel("TST", part_a, "customer", f"online:cancel:{part_a}")
        self.assertEqual(result, {"order_state": "cancelled"})
        # (b) kedaluwarsa (cron).
        order_b.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env["efn.online.order"]._cron_efn_online_expire()
        self.assertEqual(order_b.state, "cancelled")
        for order in (order_a, order_b):
            with self.subTest(order=order.platform_order_ref):
                self.assertFalse(order.refund_ids)
                cancelled = order.event_ids.filtered(lambda e: e.event == "cancelled")
                self.assertEqual(len(cancelled), 1)
                self.assertFalse(cancelled.refund_id)
        credit_notes = self.env["account.move"].search([("reversed_entry_id", "in", invoices.ids)])
        self.assertFalse(credit_notes)
        self.assertEqual(self.free(), 10.0)

    # ------------------------------------------------------------- QA G19 #3 / L-44
    def test_reward_product_without_stock_is_dropped_not_an_issue(self):
        """Kode: reward produk hadiah (beras) melebihi stok -> kode ditolak, tanpa issues."""
        self.program(
            "Kode beras gratis",
            program_type="promo_code",
            code="BERAS1",
            reward={
                "reward_type": "product",
                "reward_product_id": self.p_rice.id,
                "reward_product_qty": 1,
                "required_points": 1,
            },
        )
        ok = self.quote([{"product_id": self.p_rice.id, "qty": 3}], promo_codes=["BERAS1"])
        self.assertEqual(ok["promo_applied"], ["BERAS1"])
        self.assertEqual([line["product_id"] for line in self.reward_lines(ok)], [self.p_rice.id])
        lines = [{"product_id": self.p_rice.id, "qty": 10}]  # seluruh stok -> hadiah tidak tersedia
        quote = self.quote(lines, promo_codes=["BERAS1"])
        self.assertEqual(quote["issues"], [])
        self.assertEqual((quote["promo_applied"], quote["promo_rejected"]), ([], ["BERAS1"]))
        self.assertFalse(self.reward_lines(quote))
        self.assertEqual(quote["quote_hash"], self.quote(lines)["quote_hash"])
        # Place menghitung sama (hash cocok) dan mencadangkan barang yang dibeli saja.
        part, _payload, _result = self.place(lines=lines, quote_hash=quote["quote_hash"], promo_codes=[])
        self.assertFalse(self.record(part).sale_order_id.order_line.filtered("reward_id"))
        self.assertEqual(self.free(), 0.0)

    def test_auto_reward_product_without_stock_is_skipped(self):
        """Promo otomatis beli 2 kantong gratis 1 beras: beras habis -> reward dilewati, tanpa issues."""
        self.program(
            "Beli 2 kantong gratis beras",
            program_type="buy_x_get_y",
            rule={
                "product_ids": [Command.set(self.p_bag.ids)],
                "reward_point_mode": "unit",
                "reward_point_amount": 1,
                "minimum_qty": 2,
            },
            reward={
                "reward_type": "product",
                "reward_product_id": self.p_rice.id,
                "reward_product_qty": 1,
                "required_points": 2,
            },
        )
        ok = self.quote([{"product_id": self.p_rice.id, "qty": 3}, {"product_id": self.p_bag.id, "qty": 2}])
        self.assertEqual([line["product_id"] for line in self.reward_lines(ok)], [self.p_rice.id])
        lines = [{"product_id": self.p_rice.id, "qty": 10}, {"product_id": self.p_bag.id, "qty": 2}]
        quote = self.quote(lines)
        self.assertEqual(quote["issues"], [])
        self.assertFalse(self.reward_lines(quote))
        self.assertTrue(all(line["available"] for line in quote["lines"]))
        part, _payload, _result = self.place(lines=lines, quote_hash=quote["quote_hash"])
        self.assertEqual(self.record(part).state, "placed")
        self.assertFalse(self.record(part).sale_order_id.order_line.filtered("reward_id"))

    # ------------------------------------------------------------- QA G19 #1
    def test_buy_x_get_y_with_pb1_exclusive_fnb_fixture(self):
        """QA G19 #1. FIXTURE LOKAL (bukan data efn_setup_company): pajak 'PB1 uji' 10 % exclude, menu F&B
        non-storable. Angka = data uji (ADR-027: tanpa tarif di kode modul)."""
        pb1 = self.env["account.tax"].create(
            {
                "name": "PB1 uji (fixture)",
                "invoice_label": "PB1",
                "amount": 10.0,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "price_include_override": "tax_excluded",
                "company_id": self.company.id,
            }
        )
        Product = self.env["product.product"].with_company(self.company)
        common = {
            "type": "consu",
            "taxes_id": [Command.set(pb1.ids)],
            "company_id": self.company.id,
            "categ_id": self.product_category.id,
            "property_account_income_id": self.company_data["default_account_revenue"].id,
            "efn_online_channel_ids": [Command.set(self.channel.ids)],
        }
        coffee = Product.create({**common, "name": "Kopi susu uji", "list_price": 25000.0})
        cookie = Product.create({**common, "name": "Kue uji", "list_price": 12000.0})
        self.program(
            "Beli 2 kopi gratis kue",
            program_type="buy_x_get_y",
            rule={
                "product_ids": [Command.set(coffee.ids)],
                "reward_point_mode": "unit",
                "reward_point_amount": 1,
                "minimum_qty": 2,
            },
            reward={
                "reward_type": "product",
                "reward_product_id": cookie.id,
                "reward_product_qty": 1,
                "required_points": 2,
            },
        )
        lines = [{"product_id": coffee.id, "qty": 2}]
        quote = self.quote(lines)
        rewards = self.reward_lines(quote)
        self.assertEqual(len(rewards), 1)
        self.assertEqual(rewards[0]["product_id"], cookie.id)
        self.assertEqual(rewards[0]["subtotal_incl"], 0.0)
        self.assertEqual(rewards[0]["tax_labels"], ["PB1"])
        self.assertAlmostEqual(quote["amount_untaxed"], 50000.0)
        self.assertAlmostEqual(quote["amount_tax"], 5000.0)  # PB1 hanya atas menu berbayar (hadiah Rp0)
        self.assertAlmostEqual(quote["amount_total"], 55000.0)
        self.assertEqual(quote["amount_payable"], 55000)
        self.assertAlmostEqual(sum(group["amount"] for group in quote["tax_groups"]), 5000.0)
        self.assertEqual(quote["issues"], [])
        part, _payload, result = self.place(lines=lines, quote_hash=quote["quote_hash"])
        self.assertEqual(result["amount_payable"], 55000)
        self.assertEqual(self.pay(part), {"order_state": "confirmed"})
        invoice = self.record(part).invoice_id
        self.assertEqual(invoice.amount_total, 55000.0)
        self.assertEqual(invoice.amount_tax, 5000.0)
