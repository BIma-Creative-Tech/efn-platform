"""E-28: metode BFF (docs/04 §10.4.3 + delta §10.4.4, docs/08 §8.3 F)."""

import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.fields import Command
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from .common import PICKUP_CODE, EfnOnlineCommon


def _code(exc):
    return str(exc.exception.args[0]) if hasattr(exc, "exception") else str(exc)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineCaller(EfnOnlineCommon):
    def test_caller_without_group_denied(self):
        plain = new_test_user(
            self.env,
            login="efn_online_plain",
            groups="base.group_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(AccessError):
            self.channel_api(plain).efn_online_quote("TST", "TOKO-T1", "pickup", [{"product_id": 1, "qty": 1}])

    def test_caller_not_in_channel_users_denied(self):
        # User teknis PT lain (grup benar) tidak boleh memakai kanal TST, dan sebaliknya.
        with self.assertRaises(AccessError):
            self.channel_api(self.tech_user_2).efn_online_stock("TST", "TOKO-T1", [self.p_rice.id])
        with self.assertRaises(AccessError):
            self.channel_api(self.tech_user).efn_online_stock("TSB", "TOKO-T1", [self.p_rice.id])
        with self.assertRaises(AccessError):
            self.order_api(self.tech_user_2).efn_online_status("TST", [str(uuid.uuid4())])

    def test_caller_unknown_channel_same_error(self):
        with self.assertRaises(AccessError):
            self.channel_api().efn_online_stock("NOPE", "TOKO-T1", [self.p_rice.id])
        with self.assertRaises(AccessError):
            self.channel_api().efn_online_stock("bad code", "TOKO-T1", [self.p_rice.id])

    def test_channel_inactive(self):
        self.channel.active = False
        with self.assertRaises(UserError) as exc:
            self.quote()
        self.assertEqual(_code(exc), "efn_online:channel_inactive")

    def test_technical_user_has_no_document_access(self):
        user_env = self.env(user=self.tech_user)
        for model in ("sale.order", "efn.online.order", "efn.online.channel", "account.move", "stock.picking"):
            with self.subTest(model=model), self.assertRaises(AccessError):
                user_env[model].check_access("read")
        with self.assertRaises(AccessError):
            user_env["sale.order"].create({"partner_id": self.channel.partner_id.id})

    def test_channel_user_must_have_group_and_single_company(self):
        plain = new_test_user(
            self.env,
            login="efn_online_plain2",
            groups="base.group_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(ValidationError):
            self.channel.user_ids = [Command.link(plain.id)]
        multi = new_test_user(
            self.env,
            login="efn_online_multi",
            groups="base.group_user,efn_online_order.group_online_channel",
            company_id=self.company.id,
            company_ids=[Command.set((self.company | self.company_2).ids)],
        )
        with self.assertRaises(ValidationError):
            self.channel.user_ids = [Command.link(multi.id)]

    def test_generic_partner_without_pii(self):
        with self.assertRaises(ValidationError):
            self.channel.partner_id.phone = "081234567890"

    def test_client_context_is_discarded(self):
        """active_test/allowed_company_ids dari klien /json/2 tidak berlaku (delta §10.4.4 no. 4)."""
        archived = self.env["product.product"].create(
            {
                "name": "Arsip Online",
                "type": "consu",
                "list_price": 100.0,
                "company_id": self.company.id,
                "taxes_id": [Command.set(self.tax.ids)],
                "efn_online_channel_ids": [Command.set(self.channel.ids)],
            }
        )
        archived.active = False
        api = self.channel_api().with_context(
            active_test=False, allowed_company_ids=[self.company_2.id], lang="fr_FR", efn_online_internal=True
        )
        catalog = api.efn_online_catalog("TST")
        ids = {item["product_id"] for item in catalog["items"]}
        self.assertNotIn(archived.id, ids)
        self.assertIn(self.p_rice.id, ids)
        quote = api.efn_online_quote("TST", "TOKO-T1", "pickup", [{"product_id": archived.id, "qty": 1}])
        self.assertEqual(quote["issues"], [{"product_id": archived.id, "code": "not_found"}])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineCatalog(EfnOnlineCommon):
    def test_catalog_only_channel_products_whitelisted_fields(self):
        catalog = self.channel_api().efn_online_catalog("TST")
        ids = {item["product_id"] for item in catalog["items"]}
        self.assertEqual(ids, {self.p_rice.id, self.p_bag.id})
        item = next(i for i in catalog["items"] if i["product_id"] == self.p_rice.id)
        self.assertEqual(
            set(item),
            {
                "product_id",
                "tmpl_id",
                "name",
                "description",
                "categ",
                "uom",
                "image_sha",
                "sequence",
                "prices",
                "max_qty",
                "weight_kg",  # G20 W-33 (docs/04 §12.3), nullable
            },
        )
        self.assertIsNone(item["weight_kg"])  # produk fixture tanpa berat
        self.assertNotIn("standard_price", json.dumps(catalog))
        price = item["prices"][0]
        self.assertIsNone(price["outlet_code"])
        self.assertAlmostEqual(price["price_excl"], 9999.0)
        self.assertAlmostEqual(price["price_incl"], 11098.89)
        self.assertEqual(price["tax_labels"], ["PPN 11%"])
        self.assertIsNone(catalog["next_cursor"])
        self.assertTrue(catalog["server_time"].endswith("Z"))

    def test_catalog_channel_block_lists_all_outlets(self):
        """L-11 (docs/04 §10.17): blok `channel` + outlet dari efn.online.outlet, bukan dari prices[]."""
        self.env["efn.online.outlet"].create(
            {
                "channel_id": self.channel.id,
                "code": "TOKO-T2",
                "public_name": "Toko Dua",
                "sequence": 20,
                "warehouse_id": self.warehouse.id,
                "pickup_enabled": False,
                "prep_minutes": 30,
                "active": False,
            }
        )
        catalog = self.channel_api().efn_online_catalog("TST")
        block = catalog["channel"]
        self.assertEqual(
            set(block), {"code", "name", "brand_code", "company_code", "currency", "payment_modes", "outlets"}
        )
        self.assertEqual(block["payment_modes"], ["qris"])  # G20 E-45: default kanal
        self.assertEqual(block["code"], "TST")
        self.assertIsNone(block["brand_code"])  # G19 E-37a: kosong -> null
        self.assertEqual(block["name"], "Kanal TST")
        self.assertEqual(block["currency"], self.currency.name)
        self.assertEqual(
            block["outlets"],
            [
                {
                    "code": "TOKO-T1",
                    "public_name": "Toko Uji",
                    "pickup_enabled": True,
                    "prep_minutes": 15,
                    "active": True,
                },
                {
                    "code": "TOKO-T2",
                    "public_name": "Toko Dua",
                    "pickup_enabled": False,
                    "prep_minutes": 30,
                    "active": False,
                },
            ],
        )
        # Tanpa fiscal position outlet, prices[] hanya berisi harga dasar -> outlet tetap ada di blok channel.
        self.assertEqual({p["outlet_code"] for i in catalog["items"] for p in i["prices"]}, {None})
        # Halaman berikutnya membawa blok yang sama.
        page = self.channel_api().efn_online_catalog("TST", limit=1)
        self.assertEqual(page["channel"], block)

    def test_catalog_pagination_and_delta(self):
        page = self.channel_api().efn_online_catalog("TST", limit=1)
        self.assertEqual(len(page["items"]), 1)
        self.assertTrue(page["next_cursor"])
        page2 = self.channel_api().efn_online_catalog("TST", cursor=page["next_cursor"], limit=1)
        self.assertEqual(len(page2["items"]), 1)
        self.assertNotEqual(page["items"][0]["product_id"], page2["items"][0]["product_id"])
        since = (self.env.cr.now() - timedelta(seconds=1)).replace(microsecond=0).isoformat() + "Z"
        self.p_bag.product_tmpl_id.write({"efn_online_channel_ids": [Command.clear()]})
        self.env.flush_all()
        with patch.object(type(self.env["efn.online.channel"]), "_efn_online_changed_since", return_value=False):
            delta = self.channel_api().efn_online_catalog("TST", since=since)
        self.assertIn(self.p_bag.id, delta["deleted"])
        self.assertNotIn(self.p_bag.id, {i["product_id"] for i in delta["items"]})

    def test_catalog_input_validation(self):
        for kwargs in ({"limit": 0}, {"limit": 201}, {"cursor": "x"}, {"since": "kemarin"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(UserError) as exc:
                self.channel_api().efn_online_catalog("TST", **kwargs)
            self.assertEqual(_code(exc), "efn_online:invalid_input")

    def test_other_company_product_cannot_be_published(self):
        with self.assertRaises(ValidationError):
            self.env["product.product"].create(
                {
                    "name": "Produk PT lain",
                    "company_id": self.company_2.id,
                    "efn_online_channel_ids": [Command.set(self.channel.ids)],
                }
            )

    def test_description_plain_text_only(self):
        with self.assertRaises(ValidationError):
            self.p_rice.efn_online_description = "<script>alert(1)</script>"

    def test_stock_status(self):
        result = self.channel_api().efn_online_stock(
            "TST", "TOKO-T1", [self.p_rice.id, self.p_bag.id, self.p_hidden.id]
        )
        self.assertEqual(
            result,
            [{"product_id": self.p_rice.id, "status": "in_stock"}, {"product_id": self.p_bag.id, "status": "in_stock"}],
        )
        self.channel.stock_buffer = 6.0
        low = self.channel_api().efn_online_stock("TST", "TOKO-T1", [self.p_rice.id])
        self.assertEqual(low[0]["status"], "low")
        with self.assertRaises(UserError) as exc:
            self.channel_api().efn_online_stock("TST", "NOPE", [self.p_rice.id])
        self.assertEqual(_code(exc), "efn_online:not_found")

    def test_image_only_published(self):
        with self.assertRaises(UserError) as exc:
            self.channel_api().efn_online_image("TST", self.p_hidden.product_tmpl_id.id, 512)
        self.assertEqual(_code(exc), "efn_online:not_found")
        with self.assertRaises(UserError):
            self.channel_api().efn_online_image("TST", self.p_rice.product_tmpl_id.id, 300)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineQuotePlace(EfnOnlineCommon):
    def test_quote_amounts_and_no_db_write(self):
        count = self.env["sale.order"].sudo().search_count([])
        quote = self.quote()
        self.assertEqual(self.env["sale.order"].sudo().search_count([]), count)
        self.assertEqual(quote["channel_code"], "TST")
        line = quote["lines"][0]
        self.assertEqual(line["qty"], 3)
        self.assertAlmostEqual(line["subtotal_excl"], 29997.0)
        self.assertAlmostEqual(line["subtotal_incl"], 33296.67)
        self.assertTrue(line["available"])
        self.assertAlmostEqual(quote["amount_total"], 33296.67)
        self.assertEqual(quote["amount_payable"], 33297)
        self.assertIsInstance(quote["amount_payable"], int)
        self.assertEqual(quote["tax_groups"][0]["amount"], 3299.67)
        self.assertEqual(len(quote["quote_hash"]), 64)
        self.assertEqual(quote["issues"], [])

    def test_quote_issues(self):
        quote = self.quote([{"product_id": self.p_rice.id, "qty": 11}, {"product_id": self.p_hidden.id, "qty": 1}])
        self.assertIn({"product_id": self.p_hidden.id, "code": "not_found"}, quote["issues"])
        self.assertIn({"product_id": self.p_rice.id, "code": "out_of_stock"}, quote["issues"])
        self.assertFalse(quote["lines"][0]["available"])

    def test_quote_input_validation(self):
        bad = [
            [],
            [{"product_id": self.p_rice.id, "qty": 0}],
            [{"product_id": self.p_rice.id, "qty": 1.5}],
            [{"product_id": self.p_rice.id, "qty": 100}],
            [{"product_id": self.p_rice.id, "qty": 1}, {"product_id": self.p_rice.id, "qty": 1}],
            [{"product_id": self.p_rice.id, "qty": 1, "price_unit": 1}],
        ]
        for lines in bad:
            with self.subTest(lines=lines), self.assertRaises(UserError) as exc:
                self.quote(lines)
            self.assertEqual(_code(exc), "efn_online:invalid_input")
        with self.assertRaises(UserError):
            self.channel_api().efn_online_quote(
                "TST", "TOKO-T1", "delivery", [{"product_id": self.p_rice.id, "qty": 1}]
            )

    def test_place_matches_quote_and_is_idempotent(self):
        quote = self.quote()
        part, payload, result = self.place(quote_hash=quote["quote_hash"])
        order = self.record(part)
        self.assertEqual(result["order_state"], "placed")
        self.assertEqual(result["amount_payable"], quote["amount_payable"])
        self.assertAlmostEqual(result["amount_total"], quote["amount_total"])
        self.assertEqual(result["sale_order_name"], order.sale_order_id.name)
        self.assertTrue(result["expires_at"].endswith("Z"))
        so = order.sale_order_id
        self.assertEqual(so.state, "draft")
        self.assertEqual(so.client_order_ref, part)
        self.assertEqual(so.partner_id, self.channel.partner_id)
        self.assertEqual(so.warehouse_id, self.warehouse)
        self.assertEqual(so.efn_online_order_id, order)
        delta = order.expires_at - fields.Datetime.now()
        # G19 E-35 (docs/04 §11.1): default batas bayar Odoo 70 menit (BFF 3600 s + 10), semula 25.
        self.assertTrue(timedelta(minutes=69) < delta <= timedelta(minutes=70), delta)
        # Retry identik -> hasil sama, tidak ada SO baru.
        again = self.order_api().efn_online_place("TST", payload)
        self.assertEqual(again, result)
        self.assertEqual(self.env["sale.order"].sudo().search_count([("client_order_ref", "=", part)]), 1)
        # Kunci sama, payload beda -> konflik.
        changed = dict(payload, lines=[{"product_id": self.p_rice.id, "qty": 2}])
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_place("TST", changed)
        self.assertEqual(_code(exc), "efn_online:idempotency_conflict")
        # Ref sama dengan kunci lain -> konflik.
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_place("TST", dict(payload, idempotency_key="online:place:other-key-1"))
        self.assertEqual(_code(exc), "efn_online:idempotency_conflict")

    def test_pickup_code_never_stored_in_plaintext(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        self.assertTrue(order.pickup_code_hash.startswith("$pbkdf2-sha512$"))
        self.env.flush_all()
        self.env.cr.execute("SELECT row_to_json(o)::text FROM efn_online_order o WHERE id = %s", (order.id,))
        self.assertNotIn(PICKUP_CODE, self.env.cr.fetchone()[0])
        self.env.cr.execute(
            "SELECT count(*) FROM sale_order s WHERE s.id = %s AND row_to_json(s)::text LIKE %s",
            (order.sale_order_id.id, f"%{PICKUP_CODE}%"),
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0)

    def test_place_rejects_bad_pickup_code_and_input(self):
        for code in ("ABCD-2345", "abcd2345", "ABCD234", "ABCD1234", 12345678):
            with self.subTest(code=code), self.assertRaises(UserError):
                self.place(code=code)

    def test_price_change_between_quote_and_place(self):
        quote = self.quote()
        self.env["product.pricelist.item"].create(
            {
                "pricelist_id": self.pricelist.id,
                "applied_on": "1_product",
                "product_tmpl_id": self.p_rice.product_tmpl_id.id,
                "compute_price": "fixed",
                "fixed_price": 9000.0,
            }
        )
        self.assertNotEqual(self.quote()["quote_hash"], quote["quote_hash"])
        count = self.env["sale.order"].sudo().search_count([])
        with self.assertRaises(UserError) as exc:
            self.place(quote_hash=quote["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:price_changed")
        self.assertEqual(self.env["sale.order"].sudo().search_count([]), count)

    def test_place_out_of_stock_and_not_found(self):
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.p_rice.id, "qty": 11}], quote_hash="0" * 64)
        self.assertEqual(_code(exc), "efn_online:out_of_stock")
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.p_hidden.id, "qty": 1}], quote_hash="0" * 64)
        self.assertEqual(_code(exc), "efn_online:not_found")

    def test_place_unique_violation_becomes_concurrency_error(self):
        """Place paralel (kunci sama, transaksi lain sudah commit) -> ConcurrencyError -> Odoo mengulang request."""
        from odoo.exceptions import ConcurrencyError

        part, payload, _result = self.place()
        Order = type(self.env["efn.online.order"])
        original_search = Order.search

        def blind_search(records, domain, *args, **kwargs):
            if any(isinstance(t, (list, tuple)) and t[0] in ("idempotency_key", "platform_order_ref") for t in domain):
                return records.browse()
            return original_search(records, domain, *args, **kwargs)

        with patch.object(Order, "search", blind_search), patch.object(Order, "search_count", return_value=0):
            with self.assertRaises(ConcurrencyError), mute_logger("odoo.sql_db"):
                self.order_api().efn_online_place("TST", payload)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlinePaidCancelRefund(EfnOnlineCommon):
    def test_mark_paid_confirms_invoices_and_pays_titipan(self):
        part, _payload, _result = self.place()
        payment_ref = str(uuid.uuid4())
        result = self.pay(part, payment_ref=payment_ref)
        self.assertEqual(result, {"order_state": "confirmed"})
        order = self.record(part)
        so = order.sale_order_id
        self.assertEqual(so.state, "sale")
        invoice = order.invoice_id
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.amount_total, 33297.0)
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual(invoice.matched_payment_ids.journal_id, self.channel.journal_titipan_id)
        self.assertTrue(so.picking_ids)
        # Idempoten: kunci & intent sama -> hasil sama, tidak ada faktur/pembayaran kedua.
        again = self.pay(part, payment_ref=payment_ref)
        self.assertEqual(again, {"order_state": "confirmed"})
        self.assertEqual(len(so.invoice_ids), 1)

    def test_mark_paid_amount_mismatch_review(self):
        part, _payload, _result = self.place()
        result = self.pay(part, amount=1000)
        self.assertEqual(result, {"order_state": "review"})
        order = self.record(part)
        self.assertEqual(order.review_reason, "amount_mismatch")
        self.assertEqual(order.sale_order_id.state, "draft")

    def test_mark_paid_second_intent_review(self):
        part, _payload, _result = self.place()
        self.pay(part)
        result = self.pay(part, payment_ref=str(uuid.uuid4()))
        self.assertEqual(result["order_state"], "review")
        self.assertEqual(self.record(part).review_reason, "duplicate_payment")

    def test_mark_paid_after_expiry_requires_refund(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        order.expires_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env["efn.online.order"]._cron_efn_online_expire()
        self.assertEqual(order.state, "cancelled")
        self.assertEqual(order.cancel_reason, "payment_expired")
        self.assertEqual(order.sale_order_id.state, "cancel")
        self.assertFalse(order.pickup_code_hash)
        result = self.pay(part)
        self.assertEqual(result, {"order_state": "cancelled", "refund_required": "full"})

    def test_stock_short_after_payment(self):
        part, _payload, _result = self.place()
        self.env["stock.quant"]._update_available_quantity(self.p_rice, self.warehouse.lot_stock_id, -9.0)
        result = self.pay(part)
        self.assertEqual(result["order_state"], "stock_short")
        self.assertEqual(result["short_lines"], [{"product_id": self.p_rice.id, "requested": 3.0, "available": 1.0}])
        order = self.record(part)
        self.assertEqual(order.sale_order_id.state, "draft")
        cancel = self.order_api().efn_online_cancel("TST", part, "stock_short", f"online:cancel:{part}")
        self.assertEqual(cancel, {"order_state": "cancelled"})
        self.assertEqual(order.sale_order_id.state, "cancel")

    def test_sale_loyalty_total_change_goes_to_review(self):
        """Regresi temuan #37: total SO berubah saat konfirmasi (mis. reward sale_loyalty) -> rollback + review.

        Catatan verifikasi 19.0: perubahan total disimulasikan dengan override `_action_confirm` yang mengubah diskon
        baris (perilaku `_update_programs_and_rewards` bila reward berubah). G19 E-36: promo otomatis pricelist kanal
        kini DIKLAIM saat quote & place (pesanan kedua di bawah) dan `action_confirm` tidak mengubah totalnya.
        """
        part, _payload, _result = self.place()
        if "sale_ok" in self.env["loyalty.program"]._fields:
            self.env["loyalty.program"].create(
                {
                    "name": "Promo uji 10%",
                    "program_type": "promotion",
                    "trigger": "auto",
                    "applies_on": "current",
                    "company_id": self.company.id,
                    "sale_ok": True,
                    "pricelist_ids": [Command.set(self.pricelist.ids)],  # G19: wajib (constraint program online)
                    "rule_ids": [
                        Command.create({"minimum_amount": 0, "reward_point_amount": 1, "reward_point_mode": "order"})
                    ],
                    "reward_ids": [
                        Command.create(
                            {
                                "reward_type": "discount",
                                "discount": 10,
                                "discount_mode": "percent",
                                "discount_applicability": "order",
                                "required_points": 1,
                            }
                        )
                    ],
                }
            )
        SaleOrder = type(self.env["sale.order"])
        original = SaleOrder._action_confirm

        def discounting_confirm(orders):
            orders.order_line.write({"discount": 10.0})
            return original(orders)

        with (
            patch.object(SaleOrder, "_action_confirm", discounting_confirm),
            mute_logger("odoo.addons.efn_online_order.models.efn_online_order"),
        ):
            result = self.pay(part)
        self.assertEqual(result, {"order_state": "review"})
        order = self.record(part)
        self.assertEqual(order.review_reason, "total_changed_on_confirm")
        self.assertEqual(order.sale_order_id.state, "draft")
        self.assertFalse(order.sale_order_id.invoice_ids)
        self.assertEqual(order.sale_order_id.amount_total, order.amount_total)
        self.assertFalse(order.sale_order_id.order_line.filtered("discount"))
        # Promosi otomatis (diklaim saat quote & place, G19) tidak mengubah total saat konfirmasi -> confirmed.
        part2, _p2, result2 = self.place(lines=[{"product_id": self.p_bag.id, "qty": 1}])
        order2 = self.record(part2)
        self.assertTrue(order2.sale_order_id.order_line.filtered("reward_id"))
        self.assertEqual(self.pay(part2), {"order_state": "confirmed"})
        self.assertEqual(order2.sale_order_id.amount_total, order2.amount_total)
        self.assertEqual(order2.invoice_id.amount_total, result2["amount_payable"])

    def test_cancel_absent_and_before_payment(self):
        absent = self.order_api().efn_online_cancel("TST", str(uuid.uuid4()), "place_failed", "online:cancel:absent-1")
        self.assertEqual(absent, {"order_state": "absent"})
        part, _payload, _result = self.place()
        result = self.order_api().efn_online_cancel("TST", part, "customer", f"online:cancel:{part}")
        self.assertEqual(result, {"order_state": "cancelled"})
        self.assertEqual(self.record(part).sale_order_id.state, "cancel")
        again = self.order_api().efn_online_cancel("TST", part, "customer", f"online:cancel:{part}")
        self.assertEqual(again, result)
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_cancel("TST", part, "bogus", f"online:cancel:{part}")
        self.assertEqual(_code(exc), "efn_online:invalid_input")

    def test_cancel_after_invoice_full_credit_note(self):
        part, _payload, _result = self.place()
        self.pay(part)
        result = self.order_api().efn_online_cancel("TST", part, "review", f"online:cancel:{part}")
        self.assertEqual(result["order_state"], "cancelled")
        self.assertEqual(result["refund"]["amount"], 33297)
        refund = self.record(part).refund_ids
        self.assertEqual(refund.credit_note_id.move_type, "out_refund")
        self.assertEqual(refund.credit_note_id.state, "posted")
        self.assertEqual(refund.credit_note_id.amount_total, 33297.0)
        self.assertEqual(result["refund"]["refund_ref"], refund.refund_ref)
        again = self.order_api().efn_online_cancel("TST", part, "review", f"online:cancel:{part}")
        self.assertEqual(again, result)

    def test_partial_refund_cumulative_and_settle(self):
        part, _payload, _result = self.place()
        other_part, _p2, _r2 = self.place(lines=[{"product_id": self.p_bag.id, "qty": 2}])
        self.pay(part)
        self.pay(other_part)
        other_before = self.record(other_part).sale_order_id.amount_total
        api = self.order_api()
        refund_payload = {
            "idempotency_key": f"online:refund:{uuid.uuid4()}",
            "lines": [{"product_id": self.p_rice.id, "qty": 1}],
            "restock": False,
            "reason": "customer_cs",
        }
        first = api.efn_online_refund("TST", part, refund_payload)
        self.assertEqual(first["amount"], 11099)
        self.assertTrue(first["credit_note"])
        self.assertEqual(api.efn_online_refund("TST", part, refund_payload), first)
        with self.assertRaises(UserError) as exc:
            api.efn_online_refund("TST", part, dict(refund_payload, reason="review"))
        self.assertEqual(_code(exc), "efn_online:idempotency_conflict")
        # Qty melebihi sisa -> ditolak.
        with self.assertRaises(UserError) as exc:
            api.efn_online_refund(
                "TST",
                part,
                {
                    "idempotency_key": f"online:refund:{uuid.uuid4()}",
                    "lines": [{"product_id": self.p_rice.id, "qty": 3}],
                    "reason": "customer_cs",
                },
            )
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        rest = api.efn_online_refund(
            "TST", part, {"idempotency_key": f"online:refund:{uuid.uuid4()}", "full": True, "reason": "customer_cs"}
        )
        self.assertEqual(first["amount"] + rest["amount"], 33297)
        with self.assertRaises(UserError) as exc:
            api.efn_online_refund(
                "TST", part, {"idempotency_key": f"online:refund:{uuid.uuid4()}", "full": True, "reason": "customer_cs"}
            )
        self.assertEqual(_code(exc), "efn_online:invalid_state")
        # SO PT/bagian lain tidak tersentuh.
        self.assertEqual(self.record(other_part).sale_order_id.amount_total, other_before)
        self.assertFalse(self.record(other_part).refund_ids)
        # Settle: pembayaran keluar via jurnal titipan, idempoten.
        settle = {
            "provider_refund_ref": "mock-refund-1",
            "settled_at": "2026-10-09T11:00:00Z",
            "ok": True,
            "idempotency_key": f"online:refund_settled:{first['refund_ref']}",
        }
        self.assertEqual(api.efn_online_refund_settled("TST", first["refund_ref"], settle), {"state": "settled"})
        self.assertEqual(api.efn_online_refund_settled("TST", first["refund_ref"], settle), {"state": "settled"})
        refund = self.env["efn.online.refund"].sudo().search([("refund_ref", "=", first["refund_ref"])])
        self.assertIn(refund.credit_note_id.payment_state, ("paid", "in_payment"))
        self.assertEqual(refund.settled_payment_id.journal_id, self.channel.journal_titipan_id)
        failed = api.efn_online_refund_settled(
            "TST",
            rest["refund_ref"],
            dict(settle, ok=False, idempotency_key=f"online:refund_settled:{rest['refund_ref']}"),
        )
        self.assertEqual(failed, {"state": "failed"})
        with self.assertRaises(UserError):
            api.efn_online_refund_settled("TST", str(uuid.uuid4()), settle)

    def test_refund_with_bff_lang_context_not_installed(self):
        """QA G17 (smoke nyata): BFF selalu mengirim context `lang: id_ID`. Di DB tanpa bahasa Indonesia, refund &
        refund_settled dulu memakai `self.env` (context klien) -> env.lang -> UserError 'Invalid language code'."""
        if self.env["res.lang"]._get_data(code="id_ID"):
            self.skipTest("id_ID terpasang di DB test; kasus bahasa tidak terpasang tidak bisa direproduksi")
        part, _payload, _result = self.place()
        self.pay(part)
        api = self.order_api().with_context(lang="id_ID")
        payload = {
            "idempotency_key": f"online:refund:{uuid.uuid4()}",
            "lines": [{"product_id": self.p_rice.id, "qty": 1}],
            "restock": False,
            "reason": "customer_cs",
        }
        first = api.efn_online_refund("TST", part, payload)
        self.assertEqual(api.efn_online_refund("TST", part, payload), first)
        settle = {
            "provider_refund_ref": "mock-refund-lang",
            "settled_at": "2026-10-09T11:00:00Z",
            "ok": True,
            "idempotency_key": f"online:refund_settled:{first['refund_ref']}",
        }
        self.assertEqual(api.efn_online_refund_settled("TST", first["refund_ref"], settle), {"state": "settled"})

    def test_refund_requires_invoice(self):
        part, _payload, _result = self.place()
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_refund(
                "TST", part, {"idempotency_key": f"online:refund:{uuid.uuid4()}", "full": True, "reason": "customer_cs"}
            )
        self.assertEqual(_code(exc), "efn_online:invalid_state")

    def test_status(self):
        part, _payload, _result = self.place()
        unknown = str(uuid.uuid4())
        result = self.order_api().efn_online_status("TST", [part, unknown])
        self.assertEqual(result[0]["platform_order_ref"], part)
        self.assertEqual(result[0]["state"], "placed")
        self.assertEqual(result[0]["seq"], self.record(part).event_seq)
        self.assertEqual(result[0]["refunds"], [])
        self.assertEqual(result[1], {"platform_order_ref": unknown, "state": "absent"})
        with self.assertRaises(UserError):
            self.order_api().efn_online_status("TST", [str(uuid.uuid4()) for _i in range(101)])
        # Bagian kanal lain tidak terlihat lewat kanal TSB walau ref-nya diketahui.
        other = self.order_api(self.tech_user_2).efn_online_status("TSB", [part])
        self.assertEqual(other, [{"platform_order_ref": part, "state": "absent"}])

    def test_ops_cancel_from_sale_order_ui(self):
        part, _payload, _result = self.place()
        self.pay(part)
        order = self.record(part)
        manager = new_test_user(
            self.env,
            login="efn_online_ops_mgr",
            groups="base.group_user,sales_team.group_sale_manager",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        so = order.sale_order_id
        so.efn_online_cancel_note = "Pelanggan minta batal via CS #123"
        so.with_user(manager).action_cancel()
        self.assertEqual(order.state, "cancelled")
        self.assertFalse(order.pickup_code_hash)
        self.assertIn("Pelanggan minta batal", order.message_ids[:1].body)
        self.assertEqual(order.cancel_reason, "ops")
        event = order.event_ids.sorted("seq")[-1]
        self.assertEqual(event.event, "cancelled")
        self.assertEqual(event.refund_id.initiator, "odoo")
        self.assertEqual(event.refund_id.amount, 33297.0)
        self.assertEqual(event._efn_payload()["cancel_reason"], "ops")
        self.assertNotIn("Pelanggan", str(event._efn_payload()))

    def test_record_rules_multi_company(self):
        part, _payload, _result = self.place()
        manager_2 = new_test_user(
            self.env,
            login="efn_online_mgr2",
            groups="sales_team.group_sale_manager,stock.group_stock_user",
            company_id=self.company_2.id,
            company_ids=[Command.set(self.company_2.ids)],
        )
        orders = self.env["efn.online.order"].with_user(manager_2).search([])
        self.assertNotIn(self.record(part), orders)
        self.assertFalse(self.env["efn.online.channel"].with_user(manager_2).search([("code", "=", "TST")]))
        self.assertFalse(
            self.env["efn.online.event.outbox"]
            .with_user(manager_2)
            .search([("order_id.platform_order_ref", "=", part)])
        )


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineSecurityR10(EfnOnlineCommon):
    """Review keamanan r10 (docs/reviews/SECURITY_REVIEW_2026-10-09_r10.md, L-16)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        common = {"company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)]}
        cls.salesman = new_test_user(
            cls.env,
            login="efn_r10_sales",
            groups="base.group_user,sales_team.group_sale_salesman_all_leads",
            **common,
        )
        cls.manager = new_test_user(
            cls.env, login="efn_r10_mgr", groups="base.group_user,sales_team.group_sale_manager", **common
        )
        cls.stock_user = new_test_user(
            cls.env, login="efn_r10_stock", groups="base.group_user,stock.group_stock_user", **common
        )

    def _paid(self):
        part, _payload, _result = self.place()
        self.pay(part)
        return self.record(part)

    # ---------------------------------------------------------------- SEC-95
    def test_sec95_salesman_cannot_cancel_paid_online_so(self):
        order = self._paid()
        so = order.sale_order_id
        so.efn_online_cancel_note = "alasan cukup panjang sekali"
        with self.assertRaises(AccessError):
            so.with_user(self.salesman).action_cancel()
        self.assertEqual(order.state, "confirmed")
        self.assertEqual(so.state, "sale")
        self.assertFalse(order.refund_ids)

    def test_sec95_manager_needs_reason(self):
        order = self._paid()
        so = order.sale_order_id
        with self.assertRaises(UserError):
            so.with_user(self.manager).action_cancel()
        so.efn_online_cancel_note = "terlalu"
        with self.assertRaises(UserError):
            so.with_user(self.manager).action_cancel()
        self.assertEqual(order.state, "confirmed")
        so.efn_online_cancel_note = "Stok rusak saat disiapkan, sudah konfirmasi pelanggan"
        so.with_user(self.manager).action_cancel()
        self.assertEqual(order.state, "cancelled")
        event = order.event_ids.sorted("seq")[-1]
        payload = event._efn_payload()
        self.assertEqual((payload["event"], payload["cancel_reason"]), ("cancelled", "ops"))
        self.assertEqual(payload["refund"]["initiator"], "odoo")

    def test_sec95_unpaid_cancel_by_salesman_allowed(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        order.sale_order_id.with_user(self.salesman).action_cancel()
        self.assertEqual(order.state, "cancelled")
        self.assertEqual(self.record(part).event_ids.sorted("seq")[-1]._efn_payload()["cancel_reason"], "ops")

    def test_sec95_picking_cannot_be_validated_outside_handover(self):
        order = self._paid()
        picking = order.sale_order_id.picking_ids
        self.assertTrue(picking)
        picking.move_ids.quantity = 3.0
        for ctx in ({}, {"efn_online_handover_so_ids": order.sale_order_id.ids, "efn_online_internal": True}):
            with self.subTest(ctx=ctx), self.assertRaises(UserError):
                picking.with_user(self.stock_user).with_context(**ctx).button_validate()
        self.assertNotEqual(picking.state, "done")
        # Dan pembatalan setelah barang keluar tetap ditolak (jalur normal lewat wizard -> picked_up).
        self.assertEqual(order.state, "confirmed")

    # ---------------------------------------------------------------- SEC-99
    def test_sec99_pickup_hash_cleared_on_final_and_review_and_expiry(self):
        order = self._paid()
        self.assertTrue(order.pickup_code_hash)
        order._efn_set_review("amount_mismatch")
        self.assertFalse(order.pickup_code_hash)
        cancelled = self._paid()
        self.order_api().efn_online_cancel(
            "TST", cancelled.platform_order_ref, "review", f"online:cancel:{cancelled.platform_order_ref}"
        )
        self.assertFalse(cancelled.pickup_code_hash)
        # Deterministik: jam disuntikkan lewat parameter `now` (cron meneruskan Datetime.now() UTC naif).
        now = datetime(2026, 10, 17, 21, 13, 0)  # waktu run CI yang gagal (04:13 WIB keesokan hari)
        stale, fresh = self._paid(), self._paid()
        stale.paid_at = now - timedelta(days=8)
        fresh.paid_at = now - timedelta(days=1)
        expired = self.env["efn.online.order"]._efn_expire_pickup_codes(now=now)
        self.assertIn(stale.id, expired)
        self.assertNotIn(fresh.id, expired)
        self.assertFalse(stale.pickup_code_hash)
        self.assertTrue(fresh.pickup_code_hash)
        ok, message = stale._efn_handover(PICKUP_CODE)
        self.assertFalse(ok)
        self.assertIn("kedaluwarsa", message)

    def test_sec99_pickup_expiry_boundary_around_midnight_utc_and_wib(self):
        """Batas 7 x 24 jam dihitung dalam datetime UTC naif (tanpa tanggal kalender/zona): hasil sama di sekitar
        tengah malam UTC (07:00 WIB) dan tengah malam WIB (17:00 UTC)."""
        Order = self.env["efn.online.order"]
        # Dua pesanan saja (stok fixture terbatas); hash dipulihkan tiap putaran.
        just_expired, just_fresh = self._paid(), self._paid()
        hashes = {order: order.sudo().pickup_code_hash for order in (just_expired, just_fresh)}
        for now in (
            datetime(2026, 10, 17, 16, 59, 59),  # 23:59:59 WIB
            datetime(2026, 10, 17, 17, 0, 0),  # 00:00 WIB
            datetime(2026, 10, 17, 23, 59, 59),  # 23:59:59 UTC
            datetime(2026, 10, 18, 0, 0, 0),  # 00:00 UTC
            datetime(2026, 10, 17, 21, 13, 0),  # run CI
        ):
            with self.subTest(now=now):
                limit = now - timedelta(days=7)
                for order, value in hashes.items():
                    order.sudo().write({"pickup_code_hash": value})
                just_expired.paid_at = limit - timedelta(seconds=1)
                just_fresh.paid_at = limit + timedelta(seconds=1)
                expired = Order._efn_expire_pickup_codes(now=now)
                self.assertIn(just_expired.id, expired)
                self.assertNotIn(just_fresh.id, expired)
                self.assertTrue(just_fresh.pickup_code_hash)
        # ready_at (bila ada) menjadi acuan, bukan paid_at.
        now = datetime(2026, 10, 18, 0, 0, 0)
        ready = just_fresh
        ready.sudo().write({"pickup_code_hash": hashes[ready]})
        ready.write({"paid_at": now - timedelta(days=30), "ready_at": now - timedelta(days=2)})
        self.assertNotIn(ready.id, Order._efn_expire_pickup_codes(now=now))

    # --------------------------------------------------------------- SEC-101
    def test_sec101_technical_user_only_channel_groups(self):
        extra = new_test_user(
            self.env,
            login="efn_r10_tech_extra",
            groups="base.group_user,efn_online_order.group_online_channel,sales_team.group_sale_salesman",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(ValidationError):
            self.channel.user_ids = [Command.link(extra.id)]
        # Grup ditambahkan belakangan ke user teknis yang sudah terpasang di kanal -> ditolak.
        with self.assertRaises(ValidationError):
            self.tech_user.group_ids = [Command.link(self.env.ref("stock.group_stock_user").id)]
        with self.assertRaises(ValidationError):
            self.env.ref("sales_team.group_sale_salesman").user_ids = [Command.link(self.tech_user.id)]
        # User teknis fixture (Internal User + grup kanal) tetap sah.
        self.channel._check_efn_users()


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineInitiatorPayload(EfnOnlineCommon):
    """SEC-95/L-16: event cancelled/refunded initiator=odoo membawa initiator_uid (+ login non-email)."""

    def _cancel_as(self, login):
        part, _payload, _result = self.place()
        self.pay(part)
        order = self.record(part)
        user = new_test_user(
            self.env,
            login=login,
            groups="base.group_user,sales_team.group_sale_manager",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        order.sale_order_id.efn_online_cancel_note = "Batal ops: barang rusak di rak"
        order.sale_order_id.with_user(user).action_cancel()
        return order, user, order.event_ids.sorted("seq")[-1]._efn_payload()

    def test_initiator_uid_and_safe_login(self):
        order, user, payload = self._cancel_as("ops.manager-01")
        self.assertEqual(payload["event"], "cancelled")
        refund = payload["refund"]
        self.assertEqual(refund["initiator"], "odoo")
        self.assertEqual(refund["initiator_uid"], user.id)
        self.assertEqual(refund["initiator_login"], "ops.manager-01")
        self.assertEqual(order.refund_ids.initiator_user_id, user)

    def test_email_login_sends_uid_only(self):
        _order, user, payload = self._cancel_as("manajer.ops@example.com")
        refund = payload["refund"]
        self.assertEqual(refund["initiator_uid"], user.id)
        self.assertNotIn("initiator_login", refund)
        self.assertNotIn("@", json.dumps(payload))

    def test_bff_initiated_refund_has_no_initiator_identity(self):
        part, _payload, _result = self.place()
        self.pay(part)
        self.order_api().efn_online_refund(
            "TST", part, {"idempotency_key": f"online:refund:{uuid.uuid4()}", "full": True, "reason": "customer_cs"}
        )
        payload = self.record(part).event_ids.sorted("seq")[-1]._efn_payload()
        self.assertEqual(payload["event"], "refunded")
        self.assertEqual(payload["refund"]["initiator"], "bff")
        self.assertNotIn("initiator_uid", payload["refund"])
        self.assertNotIn("initiator_login", payload["refund"])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineReReviewG17(EfnOnlineCommon):
    """Re-review G17 (SEC-104/SEC-105, L-19): bypass lewat context RPC dan jalur pengeluaran barang lain."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        common = {"company_id": cls.company.id, "company_ids": [Command.set(cls.company.ids)]}
        cls.salesman = new_test_user(
            cls.env,
            login="efn_rr_sales",
            groups="base.group_user,sales_team.group_sale_salesman_all_leads",
            **common,
        )
        cls.stock_user = new_test_user(
            cls.env, login="efn_rr_stock", groups="base.group_user,stock.group_stock_manager", **common
        )

    def _paid(self):
        part, _payload, _result = self.place()
        self.pay(part)
        return self.record(part)

    def _assert_not_shipped(self, order):
        moves = order.sale_order_id.order_line.move_ids
        self.assertFalse(moves.filtered(lambda m: m.state == "done"))
        self.assertEqual(order.state, "confirmed")

    # ---------------------------------------------------------------- SEC-104
    def test_sec104_context_flag_does_not_bypass_cancel_guard(self):
        order = self._paid()
        so = order.sale_order_id
        for ctx in (
            {"efn_online_internal": True, "disable_cancel_warning": True},
            {"efn_online_internal_cancel_so_ids": so.ids},
        ):
            with self.subTest(ctx=ctx), self.assertRaises(AccessError):
                so.with_user(self.salesman).with_context(**ctx).action_cancel()
        self.assertEqual(so.state, "sale")
        self.assertEqual(order.state, "confirmed")

    def test_sec104_direct_state_write_rejected(self):
        order = self._paid()
        so = order.sale_order_id
        for state in ("cancel", "draft", "sent"):
            with self.subTest(state=state), self.assertRaises(UserError):
                so.with_context(efn_online_internal=True).write({"state": state})
        self.assertEqual(so.state, "sale")

    def test_sec104_handover_refused_when_pickings_cancelled(self):
        order = self._paid()
        order.sale_order_id.picking_ids.with_user(self.stock_user).action_cancel()
        ok, _message = order._efn_handover(PICKUP_CODE)
        self.assertFalse(ok)
        self.assertEqual(order.state, "review")
        self.assertEqual(order.review_reason, "handover_no_picking")
        self.assertFalse(order.picked_up_at)

    # ---------------------------------------------------------------- SEC-105
    def test_sec105_change_picking_type_then_validate_rejected(self):
        order = self._paid()
        picking = order.sale_order_id.picking_ids
        internal = self.warehouse.with_context(active_test=False).int_type_id
        self.assertTrue(internal)
        # SEC-110: tulis lewat ORM kini ditolak; jaring stock.move._action_done diuji dengan perubahan langsung DB.
        with self.assertRaises(UserError):
            picking.with_user(self.stock_user).write({"picking_type_id": internal.id})
        self.env.flush_all()
        self.env.cr.execute("UPDATE stock_picking SET picking_type_id = %s WHERE id = %s", (internal.id, picking.id))
        self.env.cr.execute(
            "UPDATE stock_move SET picking_type_id = %s WHERE picking_id = %s", (internal.id, picking.id)
        )
        self.env.invalidate_all()
        picking.move_ids.quantity = 3.0
        with self.assertRaises(UserError):
            picking.with_user(self.stock_user).with_context(efn_online_internal=True).button_validate()
        self._assert_not_shipped(order)

    def test_sec105_move_to_manual_picking_rejected(self):
        order = self._paid()
        picking = order.sale_order_id.picking_ids
        manual = self.env["stock.picking"].create(
            {
                "picking_type_id": picking.picking_type_id.id,
                "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id,
                "company_id": self.company.id,
            }
        )
        with self.assertRaises(UserError):
            picking.move_ids.with_user(self.stock_user).write({"picking_id": manual.id})
        self.env.flush_all()
        self.env.cr.execute("UPDATE stock_move SET picking_id = %s WHERE picking_id = %s", (manual.id, picking.id))
        self.env.cr.execute("UPDATE stock_move_line SET picking_id = %s WHERE picking_id = %s", (manual.id, picking.id))
        self.env.invalidate_all()
        self.assertNotEqual(manual, picking)
        manual.move_ids.quantity = 3.0
        with self.assertRaises(UserError):
            manual.with_user(self.stock_user).button_validate()
        self._assert_not_shipped(order)

    def test_sec105_partial_backorder_rejected(self):
        order = self._paid()
        picking = order.sale_order_id.picking_ids
        picking.move_ids.quantity = 1.0
        with self.assertRaises(UserError):
            picking.with_user(self.stock_user).with_context(
                skip_backorder=True, picking_ids_not_to_backorder=picking.ids
            ).button_validate()
        self._assert_not_shipped(order)

    def test_sec105_pos_line_cannot_link_online_so(self):
        """Jaring terakhir di efn_online_order (domain/loader POS diuji di efn_online_pos_sale)."""
        order = self._paid()
        PosLine = self.env["pos.order.line"]
        if "sale_order_origin_id" not in PosLine._fields:
            self.skipTest("pos_sale tidak terpasang")
        with self.assertRaises(UserError):
            PosLine.create([{"sale_order_origin_id": order.sale_order_id.id, "product_id": self.p_rice.id, "qty": 3}])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineGuardedFieldsSec110(EfnOnlineCommon):
    """SEC-110 (re-review akhir r10): field kunci guard serah terima tidak bisa ditulis di luar jalur internal."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stock_user = new_test_user(
            cls.env,
            login="efn_s110_stock",
            groups="base.group_user,stock.group_stock_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    def _paid(self):
        part, _payload, _result = self.place()
        self.pay(part)
        order = self.record(part)
        picking = order.sale_order_id.picking_ids
        picking.action_assign()
        return order, picking, picking.move_ids

    def test_move_guarded_fields_rejected(self):
        order, picking, move = self._paid()
        internal_loc = self.warehouse.lot_stock_id
        internal_type = self.warehouse.with_context(active_test=False).int_type_id
        manual = self.env["stock.picking"].create(
            {
                "picking_type_id": internal_type.id,
                "location_id": internal_loc.id,
                "location_dest_id": internal_loc.id,
                "company_id": self.company.id,
            }
        )
        cases = {
            "sale_line_id": False,
            "location_dest_id": internal_loc.id,
            "location_final_id": internal_loc.id,
            "picking_type_id": internal_type.id,
            "picking_id": manual.id,
        }
        for fname, value in cases.items():
            for ctx in ({}, {"efn_online_internal": True, "efn_online_handover_so_ids": order.sale_order_id.ids}):
                with self.subTest(field=fname, ctx=ctx), self.assertRaises(UserError):
                    move.with_user(self.stock_user).with_context(**ctx).write({fname: value})
        self.assertEqual(move.sale_line_id.order_id, order.sale_order_id)
        self.assertEqual(move.location_dest_id.usage, "customer")

    def test_move_line_guarded_fields_rejected(self):
        _order, _picking, move = self._paid()
        line = move.move_line_ids[:1]
        self.assertTrue(line, "reservasi harus membuat rincian operasi")
        other_move = self.env["stock.move"].create(
            {
                "product_id": self.p_rice.id,
                "product_uom_qty": 1,
                "location_id": self.warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "company_id": self.company.id,
            }
        )
        for fname, value in (("location_dest_id", self.warehouse.lot_stock_id.id), ("move_id", other_move.id)):
            with self.subTest(field=fname), self.assertRaises(UserError):
                line.with_user(self.stock_user).write({fname: value})

    def test_picking_guarded_fields_rejected(self):
        _order, picking, _move = self._paid()
        for fname, value in (
            ("location_dest_id", self.warehouse.lot_stock_id.id),
            ("picking_type_id", self.warehouse.with_context(active_test=False).int_type_id.id),
        ):
            with self.subTest(field=fname), self.assertRaises(UserError):
                picking.with_user(self.stock_user).write({fname: value})

    def test_attach_foreign_move_to_online_line_rejected(self):
        order, _picking, move = self._paid()
        foreign = self.env["stock.move"].create(
            {
                "product_id": self.p_rice.id,
                "product_uom_qty": 1,
                "location_id": self.warehouse.lot_stock_id.id,
                "location_dest_id": self.warehouse.lot_stock_id.id,
                "company_id": self.company.id,
            }
        )
        with self.assertRaises(UserError):
            foreign.with_user(self.stock_user).write({"sale_line_id": move.sale_line_id.id})
        self.assertFalse(foreign.sale_line_id)

    def test_harmless_writes_and_standard_flows_allowed(self):
        order, picking, move = self._paid()
        # Nilai sama (no-op) dan field lain tetap boleh.
        move.with_user(self.stock_user).write({"location_dest_id": move.location_dest_id.id, "priority": "1"})
        picking.with_user(self.stock_user).write({"picking_type_id": picking.picking_type_id.id, "note": "rak B"})
        # Serah terima lewat wizard (jalur internal) tetap bisa mengeluarkan barang.
        ok, _message = order._efn_handover(PICKUP_CODE)
        self.assertTrue(ok)
        self.assertEqual(picking.state, "done")
        # Pembatalan oleh manager tetap bisa (picking dibatalkan di dalam jalur internal).
        other, other_picking, _m = self._paid()
        manager = new_test_user(
            self.env,
            login="efn_s110_mgr",
            groups="base.group_user,sales_team.group_sale_manager",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        other.sale_order_id.efn_online_cancel_note = "Barang rusak di rak, sudah konfirmasi pelanggan"
        other.sale_order_id.with_user(manager).action_cancel()
        self.assertEqual(other.state, "cancelled")
        self.assertEqual(other_picking.state, "cancel")
