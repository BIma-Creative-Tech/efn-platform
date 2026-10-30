"""G22 (docs/04 §14.B W-04 + §14.C W-12; L-72 D-22-8/D-22-9/D-22-11): atribut no_variant di katalog/quote/place,
blok channel, menu habis, porsi Poin EFN (place, bayar, refund, earn), ready_source/preparing.

Kebijakan poin (``efn_loyalty_accounting``) dan metode "Poin EFN" (``efn_pos_loyalty_ledger``) TIDAK di ``depends``
modul ini -> test mem-patch ``efn_online_loyalty.redeem_limits`` / ``points_method`` / ``earn_on_money_only``
(pembacaan kebijakan nyata diuji terpisah dengan stub). Angka = data uji (ADR-027).
"""

import math
import uuid
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_online_order.models import efn_online_loyalty as loyalty_module
from odoo.addons.efn_online_order.models.efn_online_common import cart_lines_canonical, cart_lines_payload

from .common import PICKUP_CODE, EfnOnlineCommon

LOYALTY = "odoo.addons.efn_online_order.models.efn_online_loyalty"
ORDER_MODULE = "odoo.addons.efn_online_order.models.efn_online_order"
LIMITS = {"min_points": 50, "max_pct": 50, "point_value_idr": 100}


def _code(exc):
    return str(exc.exception.args[0])


class _PointsMethodStub:
    """Pengganti ``pos.payment.method`` Poin EFN (efn_pos_loyalty_ledger tidak di depends)."""

    def __init__(self, journal, account):
        self.journal_id = journal
        self.outstanding_account_id = account

    def __bool__(self):
        return True


class EfnOnlineG22Common(EfnOnlineCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        income = cls.company_data["default_account_revenue"]
        Attribute = env["product.attribute"]
        cls.sugar = Attribute.create(
            {
                "name": "Gula G22 uji",
                "create_variant": "no_variant",
                "display_type": "radio",
                "value_ids": [
                    Command.create({"name": "Normal"}),
                    Command.create({"name": "Tanpa gula"}),
                    Command.create({"name": "Kustom", "is_custom": True}),
                ],
            }
        )
        cls.extra = Attribute.create(
            {
                "name": "Ekstra G22 uji",
                "create_variant": "no_variant",
                "display_type": "multi",
                "value_ids": [
                    Command.create({"name": "Shot", "default_extra_price": 6000.0}),
                    Command.create({"name": "Krim", "default_extra_price": 3000.0}),
                ],
            }
        )
        Template = env["product.template"].with_company(cls.company)

        def menu(name, price, attributes):
            return Template.create(
                {
                    "name": name,
                    "type": "consu",
                    "list_price": price,
                    "taxes_id": [Command.set(cls.tax.ids)],
                    "company_id": cls.company.id,
                    "categ_id": cls.product_category.id,
                    "property_account_income_id": income.id,
                    "efn_online_channel_ids": [Command.set(cls.channel.ids)],
                    "attribute_line_ids": [
                        Command.create({"attribute_id": attr.id, "value_ids": [Command.set(attr.value_ids.ids)]})
                        for attr in attributes
                    ],
                }
            )

        cls.latte_tmpl = menu("Latte G22", 30000.0, cls.sugar | cls.extra)
        cls.latte = cls.latte_tmpl.product_variant_id
        cls.tea_tmpl = menu("Teh G22", 10000.0, cls.sugar)

        def ptav(tmpl, name):
            return tmpl.attribute_line_ids.product_template_value_ids.filtered(lambda v: v.name == name)

        cls.v_normal = ptav(cls.latte_tmpl, "Normal")
        cls.v_less = ptav(cls.latte_tmpl, "Tanpa gula")
        cls.v_custom = ptav(cls.latte_tmpl, "Kustom")
        cls.v_shot = ptav(cls.latte_tmpl, "Shot")
        cls.v_cream = ptav(cls.latte_tmpl, "Krim")
        cls.tea_normal = ptav(cls.tea_tmpl, "Normal")
        cls.stock_user = new_test_user(
            env,
            login="efn_online_g22_stock",
            groups="base.group_user,stock.group_stock_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        # Jurnal Poin EFN uji: baris metode masuk/keluar memakai akun liabilitas Poin EFN (L-79, pola efn_setup_pos).
        cls.points_account = env["account.account"].create(
            {
                "name": "Liabilitas Poin EFN uji",
                "code": "219900G22",
                "account_type": "liability_current",
                "reconcile": False,
                "company_ids": [Command.set(cls.company.ids)],
            }
        )
        cls.points_journal = env["account.journal"].create(
            {"name": "Poin EFN uji", "type": "bank", "code": "PEFNT", "company_id": cls.company.id}
        )
        cls.points_lines = (
            cls.points_journal.inbound_payment_method_line_ids | cls.points_journal.outbound_payment_method_line_ids
        )
        cls.points_lines.write({"payment_account_id": cls.points_account.id})
        cls.finance_user = new_test_user(
            env,
            login="efn_online_g22_finance",
            groups="base.group_user,account.group_account_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    def setUp(self):
        super().setUp()
        stub = _PointsMethodStub(self.points_journal, self.points_account)
        for target, value in (
            ("redeem_limits", LIMITS),
            ("points_method", stub),
            ("earn_on_money_only", True),
        ):
            patcher = patch(f"{LOYALTY}.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ helpers
    def place_points(self, points=100, value=None, lines=None, part=None, key=None):
        lines = lines if lines is not None else [{"product_id": self.p_rice.id, "qty": 3}]
        quote = self.quote(lines)
        part = part or str(uuid.uuid4())
        payload = {
            "idempotency_key": key or f"online:place:{part}",
            "platform_order_ref": part,
            "checkout_ref": str(uuid.uuid4()),
            "outlet_code": "TOKO-T1",
            "fulfilment": "pickup",
            "lines": lines,
            "quote_hash": quote["quote_hash"],
            "pickup_code": PICKUP_CODE,
        }
        if points is not None:
            payload["points_payment"] = {"points": points, "value_idr": points * 100 if value is None else value}
        return part, payload, self.order_api().efn_online_place("TST", payload)

    def pay_points(self, part, amount=None, points_value=None):
        order = self.record(part)
        ref = str(uuid.uuid4())
        payment = {
            "payment_ref": ref,
            "amount_paid": int(order.amount_due) if amount is None else amount,
            "paid_at": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "idempotency_key": f"online:paid:{part}:{ref}",
        }
        payment["points_value"] = int(order.points_value) if points_value is None else points_value
        return self.order_api().efn_online_mark_paid("TST", part, payment)

    def refund(self, part, **kwargs):
        payload = {"idempotency_key": f"online:refund:{uuid.uuid4()}", "reason": "customer_cs", **kwargs}
        return self.order_api().efn_online_refund("TST", part, payload)


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineG22Attributes(EfnOnlineG22Common):
    def test_cart_lines_payload_identity_and_canonical(self):
        rows = cart_lines_payload(
            [
                {"product_id": 5, "qty": 1, "attribute_value_ids": [9, 3]},
                {"product_id": 5, "qty": 2, "attribute_value_ids": []},
                {"product_id": 4, "qty": 1},
            ],
            50,
        )
        self.assertEqual(rows, [(4, (), 1), (5, (), 2), (5, (3, 9), 1)])
        self.assertEqual(cart_lines_canonical(rows), [[4, 1], [5, 2], [5, 1, [3, 9]]])
        bad = [
            [{"product_id": 5, "qty": 1, "attribute_value_ids": [3, 3]}],
            [{"product_id": 5, "qty": 1, "attribute_value_ids": list(range(1, 12))}],
            [{"product_id": 5, "qty": 1, "attribute_value_ids": ["3"]}],
            [{"product_id": 5, "qty": 1, "attribute_value_ids": 3}],
            [
                {"product_id": 5, "qty": 1, "attribute_value_ids": [3]},
                {"product_id": 5, "qty": 2, "attribute_value_ids": [3]},
            ],
        ]
        for lines in bad:
            with self.subTest(lines=lines), self.assertRaises(UserError):
                cart_lines_payload(lines, 50)

    def test_catalog_attributes_block(self):
        catalog = self.channel_api().efn_online_catalog("TST")
        item = next(i for i in catalog["items"] if i["product_id"] == self.latte.id)
        attrs = {a["attribute_id"]: a for a in item["attributes"]}
        sugar = attrs[self.sugar.id]
        self.assertEqual((sugar["display_type"], sugar["required"]), ("radio", True))
        self.assertEqual({v["id"] for v in sugar["values"]}, {self.v_normal.id, self.v_less.id})  # tanpa nilai kustom
        extra = attrs[self.extra.id]
        self.assertEqual((extra["display_type"], extra["required"]), ("multi", False))
        shot = next(v for v in extra["values"] if v["id"] == self.v_shot.id)
        self.assertEqual(shot["price_extra_excl"], 6000.0)
        self.assertEqual(set(shot), {"id", "name", "price_extra_excl"})
        rice = next(i for i in catalog["items"] if i["product_id"] == self.p_rice.id)
        self.assertEqual(rice["attributes"], [])

    def test_quote_lines_with_modifiers_price_extra_and_hash(self):
        lines = [
            {"product_id": self.latte.id, "qty": 1, "attribute_value_ids": [self.v_less.id]},
            {"product_id": self.latte.id, "qty": 2, "attribute_value_ids": [self.v_normal.id, self.v_shot.id]},
        ]
        quote = self.quote(lines)
        self.assertEqual(quote["issues"], [])
        # L-78: setiap baris membawa attribute_value_ids (int[] urut naik) + attributes_text.
        for line in quote["lines"]:
            self.assertEqual(line["attribute_value_ids"], sorted(line["attribute_value_ids"]))
            self.assertIsInstance(line["attributes_text"], str)
        by_attrs = {tuple(line["attribute_value_ids"]): line for line in quote["lines"]}
        plain = by_attrs[(self.v_less.id,)]
        shot = by_attrs[tuple(sorted((self.v_normal.id, self.v_shot.id)))]
        self.assertAlmostEqual(plain["price_unit"], 30000.0)
        self.assertAlmostEqual(shot["price_unit"], 36000.0, msg="price_extra no_variant dihitung sale.order.line")
        self.assertEqual(shot["qty"], 2)
        self.assertIn("Shot", shot["attributes_text"])
        self.assertIn("Gula G22 uji: Normal", shot["attributes_text"])
        self.assertAlmostEqual(quote["amount_untaxed"], 30000.0 + 72000.0)
        # Modifier berbeda -> hash berbeda (PTAV masuk quote_hash).
        other = self.quote(
            [
                {"product_id": self.latte.id, "qty": 1, "attribute_value_ids": [self.v_less.id]},
                {"product_id": self.latte.id, "qty": 2, "attribute_value_ids": [self.v_normal.id, self.v_cream.id]},
            ]
        )
        self.assertNotEqual(other["quote_hash"], quote["quote_hash"])
        # Tanpa atribut: `attribute_value_ids: []` = tanpa kunci (hash sama, baris tanpa atribut).
        rice = self.quote([{"product_id": self.p_rice.id, "qty": 1}])
        rice_empty = self.quote([{"product_id": self.p_rice.id, "qty": 1, "attribute_value_ids": []}])
        self.assertEqual(rice["quote_hash"], rice_empty["quote_hash"])
        self.assertEqual((rice["lines"][0]["attribute_value_ids"], rice["lines"][0]["attributes_text"]), ([], ""))

    def test_quote_attribute_invalid_issues(self):
        cases = {
            "required_missing": [],
            "two_radio_values": [self.v_normal.id, self.v_less.id],
            "custom_value": [self.v_custom.id],
            "other_template": [self.tea_normal.id],
            "unknown_id": [self.v_normal.id, 999999999],
        }
        for name, ptavs in cases.items():
            with self.subTest(case=name):
                quote = self.quote([{"product_id": self.latte.id, "qty": 1, "attribute_value_ids": ptavs}])
                self.assertIn({"product_id": self.latte.id, "code": "attribute_invalid"}, quote["issues"])
        # Atribut pada produk tanpa atribut -> tidak sah.
        quote = self.quote([{"product_id": self.p_rice.id, "qty": 1, "attribute_value_ids": [self.v_normal.id]}])
        self.assertIn({"product_id": self.p_rice.id, "code": "attribute_invalid"}, quote["issues"])

    def test_place_with_modifiers_and_payload_hash(self):
        lines = [
            {"product_id": self.latte.id, "qty": 1, "attribute_value_ids": [self.v_less.id, self.v_shot.id]},
            {"product_id": self.latte.id, "qty": 1, "attribute_value_ids": [self.v_normal.id]},
        ]
        part, payload, result = self.place(lines=lines)
        so = self.record(part).sale_order_id
        self.assertEqual(len(so.order_line.filtered("product_id")), 2)
        shot_line = so.order_line.filtered(lambda line: self.v_shot in line.product_no_variant_attribute_value_ids)
        self.assertAlmostEqual(shot_line.price_unit, 36000.0)
        # Replay identik -> hasil sama; atribut beda dengan kunci sama -> konflik.
        self.assertEqual(self.order_api().efn_online_place("TST", payload), result)
        changed = dict(payload, lines=[dict(lines[0], attribute_value_ids=[self.v_less.id]), lines[1]])
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_place("TST", changed)
        self.assertEqual(_code(exc), "efn_online:idempotency_conflict")

    def test_place_attribute_invalid_rejected_without_so(self):
        count = self.env["sale.order"].sudo().search_count([])
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.latte.id, "qty": 1}], quote_hash="0" * 64)
        self.assertEqual(_code(exc), "efn_online:invalid_input")
        self.assertEqual(self.env["sale.order"].sudo().search_count([]), count)

    def test_combo_not_published_online(self):
        combo = self.env["product.combo"].create(
            {
                "name": "Paket G22 uji",
                "company_id": self.company.id,  # item combo ikut company combo (check_company produk PT)
                "combo_item_ids": [Command.create({"product_id": self.p_bag.id})],
            }
        )
        product = (
            self.env["product.product"]
            .with_company(self.company)
            .create(
                {
                    "name": "Paket Online G22",
                    "type": "combo",
                    "combo_ids": [Command.set(combo.ids)],
                    "taxes_id": [Command.set(self.tax.ids)],
                    "company_id": self.company.id,
                    "efn_online_channel_ids": [Command.set(self.channel.ids)],
                }
            )
        )
        ids = {item["product_id"] for item in self.channel_api().efn_online_catalog("TST")["items"]}
        self.assertNotIn(product.id, ids)
        quote = self.quote([{"product_id": product.id, "qty": 1}])
        self.assertIn({"product_id": product.id, "code": "not_found"}, quote["issues"])


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineG22ChannelStock(EfnOnlineG22Common):
    def test_channel_block_loyalty_flags(self):
        self.channel.write({"loyalty_earn": True, "loyalty_redeem": True})
        block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertEqual(block["loyalty"], {"earn": True, "redeem": True})
        self.assertEqual(block["business_type"], "retail")
        with patch.object(type(self.channel), "_efn_online_is_fnb", return_value=True):
            block = self.channel_api().efn_online_catalog("TST")["channel"]
        self.assertEqual(block["business_type"], "fnb")

    def test_sold_out_and_fnb_stock(self):
        self.channel.write({"stock_expose_qty": True})
        stock = self.channel_api().efn_online_stock("TST", "TOKO-T1", [self.p_rice.id])
        self.assertIn("qty", stock[0])
        with patch.object(type(self.channel), "_efn_online_is_fnb", return_value=True):
            fnb = self.channel_api().efn_online_stock("TST", "TOKO-T1", [self.p_rice.id])
        self.assertEqual(fnb, [{"product_id": self.p_rice.id, "status": "in_stock"}], "F&B: tanpa qty")
        self.p_rice.product_tmpl_id.efn_online_sold_out = True
        stock = self.channel_api().efn_online_stock("TST", "TOKO-T1", [self.p_rice.id])
        self.assertEqual(stock, [{"product_id": self.p_rice.id, "status": "out"}])
        quote = self.quote([{"product_id": self.p_rice.id, "qty": 1}])
        self.assertIn({"product_id": self.p_rice.id, "code": "out_of_stock"}, quote["issues"])
        self.assertFalse(quote["lines"][0]["available"])
        with self.assertRaises(UserError) as exc:
            self.place(lines=[{"product_id": self.p_rice.id, "qty": 1}], quote_hash=quote["quote_hash"])
        self.assertEqual(_code(exc), "efn_online:out_of_stock")


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineG22Points(EfnOnlineG22Common):
    def setUp(self):
        super().setUp()
        self.channel.write({"loyalty_redeem": True, "loyalty_earn": True})

    def test_place_points_rules(self):
        cases = {
            "below_min": (40, None),
            "value_not_points_x_rate": (100, 9000),
            "exceeds_max_pct": (200, None),  # 20.000 > floor(33.297 × 50 %) = 16.648
        }
        for name, (points, value) in cases.items():
            with self.subTest(case=name), self.assertRaises(UserError) as exc:
                self.place_points(points=points, value=value)
            self.assertEqual(_code(exc), "efn_online:points_invalid")
        for bad in ({"points": 0, "value_idr": 0}, {"points": 100}, {"points": "100", "value_idr": 10000}):
            part = str(uuid.uuid4())
            with self.subTest(bad=bad), self.assertRaises(UserError) as exc:
                lines = [{"product_id": self.p_rice.id, "qty": 3}]
                self.order_api().efn_online_place(
                    "TST",
                    {
                        "idempotency_key": f"online:place:{part}",
                        "platform_order_ref": part,
                        "checkout_ref": str(uuid.uuid4()),
                        "outlet_code": "TOKO-T1",
                        "fulfilment": "pickup",
                        "lines": lines,
                        "quote_hash": self.quote(lines)["quote_hash"],
                        "pickup_code": PICKUP_CODE,
                        "points_payment": bad,
                    },
                )
            self.assertEqual(_code(exc), "efn_online:invalid_input")
        self.assertFalse(self.env["efn.online.order"].sudo().search([("points_value", ">", 0)]))

    def test_place_points_requires_channel_flag_and_policy(self):
        self.channel.write({"loyalty_redeem": False})
        with self.assertRaises(UserError) as exc:
            self.place_points()
        self.assertEqual(_code(exc), "efn_online:points_invalid")
        self.channel.write({"loyalty_redeem": True})
        with patch(f"{LOYALTY}.redeem_limits", return_value=None), self.assertRaises(UserError) as exc:
            self.place_points()
        self.assertEqual(_code(exc), "efn_online:points_invalid")

    def test_place_points_amount_due_and_idempotency(self):
        part, payload, result = self.place_points()
        order = self.record(part)
        self.assertEqual(result["amount_payable"], 33297)
        self.assertEqual(result["amount_due"], 23297)
        self.assertEqual((order.points_redeemed, order.points_value, order.amount_due), (100, 10000.0, 23297.0))
        self.assertEqual(self.order_api().efn_online_place("TST", payload), result)
        changed = dict(payload, points_payment={"points": 120, "value_idr": 12000})
        with self.assertRaises(UserError) as exc:
            self.order_api().efn_online_place("TST", changed)
        self.assertEqual(_code(exc), "efn_online:idempotency_conflict")
        # Tanpa poin: amount_due = amount_payable.
        _part, _payload, plain = self.place()
        self.assertEqual(plain["amount_due"], plain["amount_payable"])

    def test_mark_paid_two_invoice_payments(self):
        part, _payload, _result = self.place_points()
        self.assertEqual(self.pay_points(part), {"order_state": "confirmed"})
        order = self.record(part)
        invoice = order.invoice_id
        self.assertEqual(invoice.amount_total, 33297.0)
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        Payment = self.env["account.payment"].sudo()
        cash = Payment.search([("journal_id", "=", self.channel.journal_titipan_id.id)])
        points = Payment.search([("journal_id", "=", self.points_journal.id)])
        self.assertEqual(cash.mapped("amount"), [23297.0])
        self.assertEqual(points.mapped("amount"), [10000.0])
        self.assertEqual(points.outstanding_account_id, self.points_account, "akun liabilitas Poin EFN")
        self.assertTrue(points.move_id, "pembayaran Poin EFN berjurnal (Dr Liabilitas / Cr Piutang)")
        journal = self.points_journal
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        self.assertEqual(set(lines.mapped("payment_account_id").ids), {self.points_account.id})
        # Event paid QRIS tidak membawa kunci payment (bentuk G17-G21 tetap).
        paid = order.event_ids.filtered(lambda e: e.event == "paid")
        self.assertNotIn("payment", paid._efn_payload())

    def test_mark_paid_mismatch_goes_review(self):
        part, _payload, _result = self.place_points()
        self.assertEqual(self.pay_points(part, points_value=9000), {"order_state": "review"})
        self.assertEqual(self.record(part).review_reason, "points_mismatch")
        part2, _payload, _result = self.place_points()
        self.assertEqual(self.pay_points(part2, amount=33297), {"order_state": "review"})
        self.assertEqual(self.record(part2).review_reason, "amount_mismatch")

    def test_place_rejected_when_points_accounts_missing(self):
        """L-79: akun pembayaran jurnal / akun liabilitas metode kosong -> place ditolak + ERROR + alert Finance
        terjadwal setelah rollback. Odoo TIDAK mengisi akun saat runtime."""
        self.points_lines[:1].write({"payment_account_id": False})
        self._assert_points_rejected_with_alert()
        self.assertFalse(self.points_lines[:1].payment_account_id, "tidak ada penulisan akun runtime (L-79)")

    def test_place_rejected_when_method_without_liability_account(self):
        stub = _PointsMethodStub(self.points_journal, self.env["account.account"])
        with patch(f"{LOYALTY}.points_method", return_value=stub):
            self._assert_points_rejected_with_alert()

    def _assert_points_rejected_with_alert(self):
        with (
            patch(f"{ORDER_MODULE}._efn_points_account_alert_job") as job,
            self.assertLogs("odoo.addons.efn_online_order", level="ERROR") as logs,
            self.assertRaises(UserError) as exc,
        ):
            self.place_points()
        self.assertEqual(_code(exc), "efn_online:points_invalid")
        self.assertIn(f"company #{self.company.id}", "\n".join(logs.output))
        funcs = self.env.cr.postrollback._funcs
        callbacks = [f for f in funcs if getattr(f, "func", None) is job]
        self.assertEqual(len(callbacks), 1, "alert Finance dijadwalkan setelah rollback request")
        self.assertEqual(callbacks[0].args, (self.env.cr.dbname, self.company.id))
        for callback in callbacks:
            funcs.remove(callback)  # jangan sampai berjalan (membuka cursor baru) saat test di-rollback
        self.assertFalse(self.env["efn.online.order"].sudo().search([("points_value", ">", 0)]))

    def test_points_account_activity_once_per_day(self):
        Order = self.env["efn.online.order"]
        self.points_lines.write({"payment_account_id": False})
        self.assertTrue(Order._efn_points_account_activity(self.company))
        self.assertFalse(Order._efn_points_account_activity(self.company), "sekali per company per hari")
        activities = (
            self.env["mail.activity"]
            .sudo()
            .search([("res_model", "=", "account.journal"), ("res_id", "=", self.points_journal.id)])
        )
        self.assertEqual(len(activities), 1)
        user = activities.user_id
        self.assertTrue(user.has_group("account.group_account_manager"))
        self.assertIn(self.company, user.company_ids)
        self.assertFalse(self.points_lines.filtered("payment_account_id"), "tidak ada penulisan akun runtime (L-79)")

    def test_points_journal_missing_goes_review(self):
        part, _payload, _result = self.place_points()
        with patch(f"{LOYALTY}.points_method", return_value=None):
            self.assertEqual(self.pay_points(part), {"order_state": "review"})
        self.assertEqual(self.record(part).review_reason, "points_journal_missing")

    def test_refund_proportional_points_and_settle(self):
        part, _payload, _result = self.place_points()
        self.pay_points(part)
        first = self.refund(part, lines=[{"product_id": self.p_rice.id, "qty": 1}])
        self.assertEqual(first["amount"], 11099)
        # round(11.099 × 10.000 ÷ 33.297) = 3.333
        self.assertEqual((first["points_value"], first["cash_amount"]), (3333, 7766))
        factor = 1 - 10000 / 33297
        self.assertEqual(first["eligible_amount_refunded"], math.floor(9999 * factor + 1e-6))
        rest = self.refund(part, full=True)
        self.assertEqual(rest["amount"], 22198)
        self.assertEqual((rest["points_value"], rest["cash_amount"]), (6667, 15531))
        order = self.record(part)
        self.assertEqual(sum(order.refund_ids.mapped("points_value")), 10000.0)
        self.assertEqual(sum(order.refund_ids.mapped("cash_amount")), 23297.0)
        ref = first["refund_ref"]
        event = order.event_ids.filtered(lambda e: e.event == "refunded" and e.refund_id.refund_ref == ref)
        block = event._efn_payload()["refund"]
        self.assertEqual(
            {k: block[k] for k in ("points_value", "cash_amount", "eligible_amount_refunded")},
            {k: first[k] for k in ("points_value", "cash_amount", "eligible_amount_refunded")},
        )
        settle = {
            "provider_refund_ref": "mock-refund-g22",
            "settled_at": "2026-10-19T11:00:00Z",
            "ok": True,
            "idempotency_key": f"online:refund_settled:{first['refund_ref']}",
        }
        api = self.order_api()
        self.assertEqual(api.efn_online_refund_settled("TST", first["refund_ref"], settle), {"state": "settled"})
        refund = order.refund_ids.filtered(lambda r: r.refund_ref == first["refund_ref"])
        Payment = self.env["account.payment"].sudo()
        outbound = Payment.search([("payment_type", "=", "outbound"), ("journal_id", "=", self.points_journal.id)])
        self.assertEqual(outbound.mapped("amount"), [3333.0])
        self.assertEqual(refund.settled_payment_id.journal_id, self.channel.journal_titipan_id)
        self.assertEqual(refund.settled_payment_id.amount, 7766.0)
        self.assertIn(refund.credit_note_id.payment_state, ("paid", "in_payment"))

    def test_cancel_after_invoice_refund_block(self):
        part, _payload, _result = self.place_points()
        self.pay_points(part)
        result = self.order_api().efn_online_cancel("TST", part, "customer", f"online:cancel:{part}")
        self.assertEqual(result["refund"]["amount"], 33297)
        self.assertEqual((result["refund"]["points_value"], result["refund"]["cash_amount"]), (10000, 23297))

    def _pickup(self, part):
        order = self.record(part)
        order.with_user(self.stock_user).action_efn_ready()
        wizard = (
            self.env["efn.online.handover.wizard"]
            .with_user(self.stock_user)
            .create({"order_id": order.id, "code": PICKUP_CODE})
        )
        wizard.action_confirm()
        self.assertEqual(order.state, "picked_up")
        return order

    def test_earn_eligible_amount_on_picked_up(self):
        part, _payload, _result = self.place_points()
        self.pay_points(part)
        status = self.order_api().efn_online_status("TST", [part])[0]
        self.assertIsNone(status["eligible_amount"])
        order = self._pickup(part)
        expected = math.floor(29997 * (1 - 10000 / 33297) + 1e-6)
        self.assertEqual(order.eligible_amount, expected)
        payload = order.event_ids.filtered(lambda e: e.event == "picked_up")._efn_payload()
        self.assertEqual(payload["loyalty"], {"eligible_amount": expected})
        self.assertEqual(self.order_api().efn_online_status("TST", [part])[0]["eligible_amount"], expected)
        ready = order.event_ids.filtered(lambda e: e.event == "ready")._efn_payload()
        self.assertEqual(ready["ready_source"], "manual")
        self.assertEqual(order.ready_source, "manual")

    def test_mgm_basis_on_picked_up_and_refund_event(self):
        """G31b (docs/04 §20.10): kebijakan MGM PT mengecualikan kategori beras -> dasar MGM tanpa beras; kantong tetap.
        efn_mgm_policy tidak di depends -> ``mgm_excluded_category_ids`` di-patch (kebijakan nyata: modul MGM)."""
        lines = [{"product_id": self.p_rice.id, "qty": 2}, {"product_id": self.p_bag.id, "qty": 2}]
        part, _payload, _result = self.place_points(points=None, lines=lines)
        self.pay(part)
        rice_categ = self.env["product.category"].create({"name": "Beras MGM (uji)"})
        self.p_rice.categ_id = rice_categ
        with patch(f"{LOYALTY}.mgm_excluded_category_ids", return_value={rice_categ.id}) as mocked:
            order = self._pickup(part)
            self.assertEqual(mocked.call_args.args[1], order.company_id)
            so_lines = order.sale_order_id.order_line.filtered("product_id")
            bag = math.floor(
                sum(so_lines.filtered(lambda line: line.product_id == self.p_bag).mapped("price_subtotal"))
            )
            self.assertTrue(0 < bag < order.eligible_amount)
            self.assertEqual(order.efn_mgm_eligible_amount, bag, "dasar MGM = kantong saja (beras dikecualikan MGM)")
            payload = order.event_ids.filtered(lambda e: e.event == "picked_up")._efn_payload()
            self.assertEqual(payload["loyalty"], {"eligible_amount": order.eligible_amount, "mgm_eligible_amount": bag})
            refund = self.refund(part, lines=[{"product_id": self.p_bag.id, "qty": 1}])
            self.assertNotIn("mgm_eligible_amount_refunded", refund, "hasil RPC refund tidak berubah")
            self.assertEqual(mocked.call_args.args[2], order.picked_up_at, "kebijakan saat diambil")
        event = order.event_ids.filtered(
            lambda e: e.event == "refunded" and e.refund_id.refund_ref == refund["refund_ref"]
        )
        block = event._efn_payload()["refund"]
        self.assertGreater(block["eligible_amount_refunded"], 0)
        self.assertEqual(
            block["mgm_eligible_amount_refunded"], block["eligible_amount_refunded"], "kantong = dasar MGM"
        )

    def test_mgm_basis_absent_without_policy(self):
        part, _payload, _result = self.place_points(points=None)
        self.pay(part)
        order = self._pickup(part)
        self.assertFalse(order.efn_mgm_basis_set)
        payload = order.event_ids.filtered(lambda e: e.event == "picked_up")._efn_payload()
        self.assertEqual(set(payload["loyalty"]), {"eligible_amount"})
        refund = self.refund(part, lines=[{"product_id": self.p_rice.id, "qty": 1}])
        event = order.event_ids.filtered(
            lambda e: e.event == "refunded" and e.refund_id.refund_ref == refund["refund_ref"]
        )
        self.assertNotIn("mgm_eligible_amount_refunded", event._efn_payload()["refund"])

    def test_earn_money_only_off_and_refund_before_pickup_and_channel_flag(self):
        part, _payload, _result = self.place_points()
        self.pay_points(part)
        self.refund(part, lines=[{"product_id": self.p_rice.id, "qty": 1}])
        self.channel.write({"loyalty_earn": False})
        with patch(f"{LOYALTY}.earn_on_money_only", return_value=False):
            order = self._pickup(part)
        self.assertEqual(order.eligible_amount, 29997 - 9999, "neto refund sebelum diambil, tanpa faktor poin")
        payload = order.event_ids.filtered(lambda e: e.event == "picked_up")._efn_payload()
        self.assertNotIn("loyalty", payload, "kanal earn mati -> tanpa blok loyalty")

    def test_preparing_event_once(self):
        part, _payload, _result = self.place()
        order = self.record(part)
        self.assertFalse(order._efn_set_kitchen_started(), "belum dibayar")
        self.pay(part)
        self.assertTrue(order._efn_set_kitchen_started())
        self.assertFalse(order._efn_set_kitchen_started())
        events = order.event_ids.filtered(lambda e: e.event == "preparing")
        self.assertEqual(len(events), 1)
        payload = events._efn_payload()
        self.assertTrue(payload["kitchen_started_at"].endswith("Z"))
        self.assertEqual(payload["state"], "confirmed")
        self.assertRegex(payload["idempotency_key"], r"^online:[0-9a-f-]{36}:preparing:\d{1,9}$")


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlineG22LoyaltyHelpers(EfnOnlineCommon):
    """Pembacaan kebijakan nyata (tanpa patch) dengan stub kebijakan."""

    def test_redeem_limits_fail_closed_and_values(self):
        class Policy:
            redeem_max_pct = 40
            redeem_min_points = 20
            point_value_idr = 0
            earn_on_money_only = False

        class GlobalPolicy(Policy):
            point_value_idr = 125

        company = self.company
        with patch(f"{LOYALTY}.active_policy", return_value=None):
            self.assertIsNone(loyalty_module.redeem_limits(self.env, company))
            self.assertTrue(loyalty_module.earn_on_money_only(self.env, company), "fallback parameter = True")

        def fake(env, comp):
            return GlobalPolicy() if comp and comp.efn_brand_code == "EFN" else Policy()

        efn = self.env["res.company"].sudo().search([("efn_brand_code", "=", "EFN")], limit=1)
        with patch(f"{LOYALTY}.active_policy", side_effect=fake):
            limits = loyalty_module.redeem_limits(self.env, company)
            self.assertFalse(loyalty_module.earn_on_money_only(self.env, company))
        if efn:
            self.assertEqual(limits, {"min_points": 20, "max_pct": 40, "point_value_idr": 125})
        else:
            self.assertIsNone(limits, "tanpa company EFN -> nilai tukar global tidak ada -> fail-closed")

    def test_active_policy_without_module_returns_none(self):
        if "efn.loyalty.policy" in self.env:
            self.skipTest("efn_loyalty_accounting terpasang; cabang modul-absen tidak bisa diuji di DB ini")
        self.assertIsNone(loyalty_module.active_policy(self.env, self.company))
