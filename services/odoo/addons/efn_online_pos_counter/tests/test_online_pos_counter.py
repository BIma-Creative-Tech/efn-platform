"""E-45 bayar di kasir (docs/04 §12.2, L-50/L-52/L-55/L-56; docs/08 §11.8 no. 8).

Lookup kode salah/PT lain/outlet lain ditolak; nominal diubah ditolak; bayar setelah kedaluwarsa/batal -> review;
dobel sync idempoten; jurnal kliring = 0; SO tetap tidak dimuat POS (regresi SEC-105); refund lewat POS hanya setelah
refund online disetujui. BFF tidak dipanggil (event hanya diantrikan di outbox).
"""

from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_online_order.models.efn_online_common import counter_code_digest, counter_secret
from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from .common import PICKUP_CODE, make_counter_channel, online_record, place_counter, set_test_params

CODE = "HJKM2345"
CODE_2 = "NPQR6789"
CODE_3 = "STUV3456"


@tagged("post_install", "-at_install", "efn")
class TestEfnOnlinePosCounter(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        set_test_params(cls.env)
        company = cls.config.company_id
        cls.company = company
        warehouse = cls.config.picking_type_id.warehouse_id
        cls.fx = make_counter_channel(cls.env, company, warehouse, "TCT", "efn_online_tct")
        cls.channel = cls.fx["channel"]
        # Outlet kedua di gudang lain (company sama) -> lookup dari POS gudang pertama ditolak.
        su = cls.env(su=True)
        cls.warehouse_2 = su["stock.warehouse"].create(
            {"name": "Gudang Lain E45", "code": "WE45", "company_id": company.id}
        )
        su["efn.online.outlet"].create(
            {
                "channel_id": cls.channel.id,
                "code": "TOKO-C2",
                "public_name": "Toko Lain",
                "warehouse_id": cls.warehouse_2.id,
            }
        )
        su["stock.quant"]._update_available_quantity(cls.fx["product"], cls.warehouse_2.lot_stock_id, 50.0)
        # PT lain dengan kanal counter sendiri.
        cls.company_data_2 = cls.setup_other_company(name="PT Lain E45")
        cls.company_2 = cls.company_data_2["company"]
        warehouse_other = su["stock.warehouse"].search([("company_id", "=", cls.company_2.id)], limit=1)
        cls.fx_2 = make_counter_channel(cls.env, cls.company_2, warehouse_other, "OTH", "efn_online_oth")

        cls.cashier = new_test_user(
            cls.env,
            login="efn_e45_kasir",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=company.id,
            company_ids=[Command.set(company.ids)],
        )
        cls.plain = new_test_user(cls.env, login="efn_e45_plain", groups="base.group_user", company_id=company.id)
        cls.outsider = new_test_user(
            cls.env,
            login="efn_e45_kasir_lain",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=cls.company_2.id,
            company_ids=[Command.set(cls.company_2.ids)],
        )
        cls.counter_product = company.efn_online_counter_product_id
        cls.clearing = company._efn_online_counter_account()

    def setUp(self):
        super().setUp()
        self.open_new_session()

    # ----------------------------------------------------------------- helpers
    def _place(self, code=CODE, **kwargs):
        part, payload, result = place_counter(self.env, self.fx, code, **kwargs)
        return online_record(self.env, part), payload, result

    def _lookup(self, code=CODE, user=None):
        return self.config.with_user(user or self.cashier).efn_online_counter_lookup(code)

    def _settle_data(self, online, price=None, payments=None, customer=False, extra_product=None):
        amount = online.amount_payable
        price = amount if price is None else price
        products = [(self.counter_product, 1)]
        if extra_product:
            products.append((extra_product, 1))
        data = self.create_ui_order_data(products, payments=payments or [(self.cash_pm1, price)], customer=customer)
        line = data["lines"][0][2]
        line.update(
            price_unit=price, price_subtotal=price, price_subtotal_incl=price, efn_online_order_label=online.name
        )
        total = sum(cmd[2]["price_subtotal_incl"] for cmd in data["lines"])
        data.update(amount_total=total, amount_tax=0.0, amount_paid=sum(p[2]["amount"] for p in data["payment_ids"]))
        return data

    def _sync(self, data, user=None):
        self.env["pos.order"].with_user(user or self.cashier).sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _assert_rejected(self, data):
        with self.assertRaises(UserError), mute_logger("odoo.addons.efn_online_pos_counter"):
            self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))

    def _paid_online(self, code=CODE):
        """Place -> lookup (klaim) -> sync order POS pelunasan. Kembalikan (pesanan online, order POS)."""
        online, _payload, _result = self._place(code)
        self.assertIn("order_label", self._lookup(code))
        return online, self._sync(self._settle_data(online))

    def _clearing_balance(self):
        lines = (
            self.env["account.move.line"]
            .sudo()
            .search([("account_id", "=", self.clearing.id), ("parent_state", "=", "posted")])
        )
        return self.company.currency_id.round(sum(lines.mapped("balance")))

    def _close_session(self):
        session = self.pos_session
        cash = sum(session.order_ids.payment_ids.filtered(lambda p: p.payment_method_id.is_cash_count).mapped("amount"))
        session.post_closing_cash_details(cash)
        session.close_session_from_ui()
        self.assertEqual(session.state, "closed")

    # ------------------------------------------------------------ setup & place
    def test_setup_placeholder_per_company(self):
        product = self.counter_product
        self.assertEqual((product.type, product.company_id, product.available_in_pos), ("service", self.company, False))
        self.assertFalse(product.taxes_id, "tanpa pajak (pajak hanya di faktur SO)")
        self.assertEqual(self.clearing.account_type, "asset_current")
        journal = self.company.efn_online_counter_journal_id
        self.assertEqual(journal.type, "bank")
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        self.assertEqual(set(lines.mapped("payment_account_id").ids), {self.clearing.id})
        self.assertNotEqual(self.company_2.efn_online_counter_product_id, product, "data per PT")
        self.assertIn(product, self.config._get_special_products())
        self.assertTrue(self.config.efn_online_counter_enabled)
        self.assertEqual(self.config.efn_online_counter_product_id, product)

    def test_place_counter_stores_digest_only(self):
        online, payload, result = self._place()
        self.assertNotIn("counter_code", result, "L-56: kode bayar dibuat BFF, tidak dikembalikan")
        self.assertEqual((online.payment_mode, online.state), ("counter", "placed"))
        stored = online.counter_code_hash
        self.assertNotIn(CODE, stored)
        self.assertEqual(stored, counter_code_digest(counter_secret(self.env), self.company.id, CODE, 1000))
        self.assertNotEqual(stored, online.pickup_code_hash)
        # Replay idempoten: kode bayar tidak ikut payload_sha256 (pola kode ambil).
        replay = self.env["efn.online.order"].with_user(self.fx["tech"]).efn_online_place("TCT", payload)
        self.assertEqual(replay["sale_order_name"], online.name)
        block = self.env["efn.online.channel"].with_user(self.fx["tech"]).efn_online_catalog("TCT")["channel"]
        self.assertEqual(block["payment_modes"], ["qris", "counter"])

    def test_place_counter_input_rules(self):
        # Tanpa kode bayar, kode bayar = kode ambil (L-50), alfabet salah (0/1 tidak dipakai).
        for code in (None, PICKUP_CODE, "ABCD1111"):
            with self.subTest(code=code), self.assertRaisesRegex(UserError, "efn_online:invalid_input"):
                place_counter(self.env, self.fx, code)
        self._place(CODE)
        with self.assertRaisesRegex(UserError, "efn_online:invalid_input"):
            self._place(CODE)  # digest bentrok dengan pesanan terbuka lain
        self.channel.write({"payment_modes": "qris"})
        with self.assertRaisesRegex(UserError, "efn_online:invalid_input"):
            self._place(CODE_2)

    def test_bff_mark_paid_and_refund_settled_rejected_for_counter(self):
        online, _payload, _result = self._place()
        api = self.env["efn.online.order"].with_user(self.fx["tech"])
        with self.assertRaisesRegex(UserError, "efn_online:invalid_state"):
            api.efn_online_mark_paid(
                "TCT",
                online.platform_order_ref,
                {
                    "payment_ref": "gw-1",
                    "amount_paid": int(online.amount_payable),
                    "paid_at": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "idempotency_key": f"online:paid:{online.platform_order_ref}:gw-1",
                },
            )
        self.assertEqual(online.state, "placed")

    # ------------------------------------------------------------------ lookup
    def test_lookup_returns_label_and_amount_only(self):
        online, _payload, _result = self._place()
        result = self._lookup("hjkm-2345")
        self.assertEqual(result, {"order_label": online.name, "amount_payable": int(online.amount_payable)})
        log = self.env["efn.online.counter.lookup"].sudo().search([("online_order_id", "=", online.id)])
        self.assertEqual((log.result, log.user_id, log.session_id), ("ok", self.cashier, self.pos_session))

    def test_lookup_wrong_code_other_company_other_outlet(self):
        self._place()
        self.assertEqual(self._lookup(CODE_2)["error_code"], "not_found")
        self.assertEqual(self._lookup("123")["error_code"], "invalid_format")
        place_counter(self.env, self.fx_2, CODE_2)  # PT lain, kode sama dengan yang dicoba
        self.assertEqual(self._lookup(CODE_2)["error_code"], "not_found")
        place_counter(self.env, self.fx, CODE_3, outlet="TOKO-C2")  # outlet lain (gudang lain)
        self.assertEqual(self._lookup(CODE_3)["error_code"], "not_found")
        logs = self.env["efn.online.counter.lookup"].sudo().search([("config_id", "=", self.config.id)])
        self.assertFalse(logs.filtered(lambda r: r.result == "ok"))

    def test_lookup_rate_limited_per_session(self):
        self._place()
        for _attempt in range(5):
            self.assertEqual(self._lookup(CODE_2)["error_code"], "not_found")
        self.assertEqual(self._lookup(CODE)["error_code"], "rate_limited", "kode benar pun ditolak saat dibatasi")
        self.env.cr.execute(
            "UPDATE efn_online_counter_lookup SET create_date = create_date - interval '16 minutes' "
            "WHERE config_id = %s",
            (self.config.id,),
        )
        self.env["efn.online.counter.lookup"].invalidate_model()
        self.assertIn("order_label", self._lookup(CODE))

    def test_lookup_code_never_logged(self):
        self._place()
        with self.assertLogs("odoo.addons.efn_online_pos_counter", level="INFO") as logs:
            self._lookup(CODE_2)
            self._lookup(CODE)
        joined = "\n".join(logs.output)
        self.assertNotIn(CODE, joined)
        self.assertNotIn(CODE_2, joined)

    def test_lookup_expired_or_cancelled_rejected(self):
        online, _payload, _result = self._place()
        online.write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        self.assertEqual(self._lookup()["error_code"], "not_payable")
        online.env["efn.online.order"]._cron_efn_online_expire()
        self.assertEqual(online.state, "cancelled")
        self.assertFalse(online.counter_code_hash, "kode bayar dihapus saat batal")
        self.assertEqual(self._lookup()["error_code"], "not_found")

    def test_lookup_rpc_access(self):
        self._place()
        with self.assertRaises(AccessError):
            self._lookup(user=self.plain)
        with self.assertRaises(AccessError):
            self._lookup(user=self.outsider)

    # -------------------------------------------------------------------- sync
    def test_sync_settles_online_order_and_clearing_nets_zero(self):
        online, pos_order = self._paid_online()
        self.assertEqual(pos_order.state, "paid")
        self.assertEqual(online.state, "confirmed")
        self.assertEqual(online.payment_ref, pos_order.uuid)
        self.assertEqual(online.paid_idempotency_key, f"pos_counter:{pos_order.uuid}")
        self.assertFalse(online.counter_code_hash)
        invoice = online.invoice_id
        self.assertEqual(invoice.state, "posted")
        self.assertIn(invoice.payment_state, ("paid", "in_payment"))
        self.assertEqual(self.company.currency_id.compare_amounts(invoice.amount_total, online.amount_payable), 0)
        journal = self.company.efn_online_counter_journal_id
        payments = self.env["account.payment"].sudo().search([("journal_id", "=", journal.id)])
        self.assertEqual(len(payments), 1)
        self.assertNotEqual(payments.journal_id, self.channel.journal_titipan_id, "L-55: tanpa titipan Holding")
        settlement = self.env["efn.online.counter.settlement"].sudo().search([("online_order_id", "=", online.id)])
        self.assertEqual((settlement.pos_order_id, settlement.online_state), (pos_order, "confirmed"))
        # Event paid membawa metode saja (tanpa identitas kasir).
        paid_event = online.event_ids.filtered(lambda e: e.event == "paid")
        payload = paid_event._efn_payload()
        self.assertEqual(payload["payment"], {"method": "counter"})
        self.assertNotIn(self.cashier.login, str(payload))
        self.assertEqual(online.event_ids.sorted("seq").mapped("event"), ["placed", "paid", "confirmed"])
        # Kode bayar tidak bisa dipakai lagi.
        self.assertEqual(self._lookup()["error_code"], "not_found")
        # Kliring: POS (kredit, akun pendapatan produk pelunasan) + pembayaran faktur (debit) = 0.
        self._close_session()
        self.assertEqual(self._clearing_balance(), 0.0)
        pos_line = pos_order.lines
        self.assertFalse(pos_line.tax_ids)
        if "sale_order_origin_id" in pos_line._fields:
            self.assertFalse(pos_line.sale_order_origin_id, "SEC-105: baris pelunasan tidak menautkan SO")
        with self.assertRaises(UserError):
            self._lookup()  # sesi sudah ditutup

    def test_sync_rejects_modified_amount_and_shape(self):
        online, _payload, _result = self._place()
        self.assertIn("order_label", self._lookup())
        amount = online.amount_payable
        extra = self.create_product("Barang Lain E45", self.categ_basic, 1000.0)
        partner = self.env["res.partner"].create({"name": "Pelanggan E45"})
        cases = {
            "price_lower": self._settle_data(online, price=amount - 1),
            "price_higher": self._settle_data(online, price=amount + 1),
            "extra_line": self._settle_data(online, extra_product=extra, payments=[(self.cash_pm1, amount + 1000)]),
            "partner": self._settle_data(online, customer=partner),
        }
        qty_two = self._settle_data(online)
        qty_two["lines"][0][2]["qty"] = 2
        cases["qty_two"] = qty_two
        wrong_label = self._settle_data(online)
        wrong_label["lines"][0][2]["efn_online_order_label"] = "S99999"
        cases["wrong_label"] = wrong_label
        for name, data in cases.items():
            with self.subTest(case=name):
                self._assert_rejected(data)
        self.assertEqual(online.state, "placed")

    def test_sync_requires_lookup_claim(self):
        online, _payload, _result = self._place()
        self._assert_rejected(self._settle_data(online))
        self.assertEqual(online.state, "placed")

    def test_double_sync_idempotent_and_second_pos_order_rejected(self):
        online, _payload, _result = self._place()
        self.assertIn("order_label", self._lookup())
        data = self._settle_data(online)
        first = self._sync(data)
        again = self._sync(data)
        self.assertEqual(first, again)
        settlements = self.env["efn.online.counter.settlement"].sudo().search([("online_order_id", "=", online.id)])
        self.assertEqual(len(settlements), 1)
        self.assertEqual(len(online.event_ids.filtered(lambda e: e.event == "paid")), 1)
        self._assert_rejected(self._settle_data(online))  # order POS lain (uuid baru) untuk pesanan yang sama

    def test_paid_after_expiry_goes_to_review(self):
        online, _payload, _result = self._place()
        self.assertIn("order_label", self._lookup())
        online.write({"expires_at": fields.Datetime.now() - timedelta(minutes=1)})
        pos_order = self._sync(self._settle_data(online))
        self.assertEqual(pos_order.state, "paid")
        self.assertEqual((online.state, online.review_reason), ("review", "counter_paid_after_expiry"))
        self.assertFalse(online.invoice_id)
        # QA G20 / L-57: event `review` WAJIB membawa alasan, kalau tidak BFF mengabaikannya (ignored_stale).
        review = online.event_ids.filtered(lambda e: e.event == "review")
        self.assertEqual(len(review), 1)
        self.assertEqual(review._efn_payload()["review_reason"], "counter_paid_after_expiry")

    def test_paid_after_cancel_goes_to_review_then_pos_refund(self):
        online, _payload, _result = self._place()
        self.assertIn("order_label", self._lookup())
        self.env["efn.online.order"].with_user(self.fx["tech"]).efn_online_cancel(
            "TCT", online.platform_order_ref, "customer", f"online:cancel:{online.platform_order_ref}"
        )
        self.assertEqual(online.state, "cancelled")
        pos_order = self._sync(self._settle_data(online))
        self.assertEqual((online.state, online.review_reason), ("review", "counter_paid_after_cancel"))
        self.assertTrue(online.paid_idempotency_key)
        review = online.event_ids.filtered(lambda e: e.event == "review")
        self.assertEqual(review.sorted("seq")[-1]._efn_payload()["review_reason"], "counter_paid_after_cancel")
        # Penyelesaian review lewat BFF = batal; dana kembali lewat refund order POS pelunasan.
        self.env["efn.online.order"].with_user(self.fx["tech"]).efn_online_cancel(
            "TCT", online.platform_order_ref, "review", f"online:cancel_review:{online.platform_order_ref}"
        )
        self.assertEqual(online.state, "cancelled")
        refund = self._sync(self._refund_data(pos_order))
        self.assertEqual(refund.state, "paid")
        settlement = self.env["efn.online.counter.settlement"].sudo().search([("online_order_id", "=", online.id)])
        self.assertEqual(settlement.refund_pos_order_id, refund)
        self._assert_rejected(self._refund_data(pos_order))  # refund kedua ditolak

    # ------------------------------------------------------------------ refund
    def _refund_data(self, pos_order, payments=None):
        line = pos_order.lines[:1]
        amount = line.price_subtotal_incl
        data = self.create_ui_order_data(
            [{"product": self.counter_product, "quantity": -1, "refunded_orderline_id": line.id}],
            payments=payments or [(self.cash_pm1, -amount)],
            pos_order_ui_args={"is_refund": True},
        )
        cmd = data["lines"][0][2]
        cmd.update(price_unit=amount, price_subtotal=-amount, price_subtotal_incl=-amount)
        data.update(amount_total=-amount, amount_tax=0.0, amount_paid=-amount)
        return data

    def test_pos_refund_requires_approved_online_refund(self):
        online, pos_order = self._paid_online()
        self._assert_rejected(self._refund_data(pos_order))
        # CS + approver di BFF -> efn_online_refund (nota kredit, requested) -> uang keluar lewat refund POS.
        api = self.env["efn.online.order"].with_user(self.fx["tech"])
        result = api.efn_online_refund(
            "TCT",
            online.platform_order_ref,
            {"idempotency_key": f"online:refund:{online.platform_order_ref}", "full": True, "reason": "customer_cs"},
        )
        record = online.refund_ids
        self.assertEqual(record.state, "requested")
        with self.assertRaisesRegex(UserError, "efn_online:invalid_state"):
            api.efn_online_refund_settled(
                "TCT",
                result["refund_ref"],
                {
                    "provider_refund_ref": "gw-r1",
                    "settled_at": fields.Datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ok": True,
                    "idempotency_key": f"online:refund_settled:{result['refund_ref']}",
                },
            )
        refund = self._sync(self._refund_data(pos_order))
        self.assertEqual(refund.state, "paid")
        self.assertEqual(record.state, "settled")
        self.assertEqual(record.settled_payment_id.journal_id, self.company.efn_online_counter_journal_id)
        # L-58: event `refund_settled` (bukan `refunded`) membawa referensi POS -> BFF menandai lunas.
        outbox = (
            self.env["efn.online.event.outbox"]
            .sudo()
            .search([("order_id", "=", online.id), ("event", "=", "refund_settled")])
        )
        self.assertEqual(len(outbox), 1)
        payload = outbox._efn_payload()["refund"]
        self.assertEqual(payload["refund_ref"], result["refund_ref"])
        self.assertEqual(payload["provider_refund_ref"], f"pos:{refund.uuid}")
        self.assertTrue(payload["settled_at"].endswith("Z"))
        self._close_session()
        self.assertEqual(self._clearing_balance(), 0.0, "jual + bayar faktur + refund POS + refund nota kredit = 0")
