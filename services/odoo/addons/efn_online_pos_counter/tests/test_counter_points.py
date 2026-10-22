"""G22 W-12 (docs/04 §14.C.4): bayar di kasir untuk pesanan dengan porsi Poin EFN.

Lookup = porsi tunai (`amount_due`); pelunasan POS = porsi tunai; faktur SO dibayar dua kali (kliring + Poin EFN);
event `paid` membawa `points_value`; otorisasi & refund POS = `cash_amount` refund; kliring tetap bersih 0.
Kebijakan & metode Poin EFN di-patch (modulnya tidak di depends efn_online_order).
"""

import uuid
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from .common import make_counter_channel, online_record, set_test_params

LOYALTY = "odoo.addons.efn_online_order.models.efn_online_loyalty"
CODE = "HJKM2345"


class _PointsMethodStub:
    def __init__(self, journal, account):
        self.journal_id = journal
        self.outstanding_account_id = account

    def __bool__(self):
        return True


@tagged("post_install", "-at_install", "efn")
class TestEfnCounterPoints(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        set_test_params(cls.env)
        cls.company = cls.config.company_id
        cls.fx = make_counter_channel(
            cls.env, cls.company, cls.config.picking_type_id.warehouse_id, "TCP", "efn_online_tcp"
        )
        cls.channel = cls.fx["channel"]
        su = cls.env(su=True)
        cls.channel.sudo().write({"loyalty_redeem": True})
        cls.points_account = su["account.account"].create(
            {
                "name": "Liabilitas Poin EFN uji kasir",
                "code": "219900KSR",
                "account_type": "liability_current",
                "company_ids": [Command.set(cls.company.ids)],
            }
        )
        cls.points_journal = su["account.journal"].create(
            {"name": "Poin EFN uji kasir", "type": "bank", "code": "PEFNK", "company_id": cls.company.id}
        )
        # L-79: akun pembayaran jurnal Poin EFN disiapkan data (efn_setup_pos), bukan diisi runtime.
        journal = cls.points_journal
        lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
        lines.write({"payment_account_id": cls.points_account.id})
        cls.cashier = new_test_user(
            cls.env,
            login="efn_g22_kasir",
            groups="base.group_user,point_of_sale.group_pos_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.counter_product = cls.company.efn_online_counter_product_id
        cls.clearing = cls.company._efn_online_counter_account()

    def setUp(self):
        super().setUp()
        self.open_new_session()
        stub = _PointsMethodStub(self.points_journal, self.points_account)
        for target, value in (
            ("redeem_limits", {"min_points": 50, "max_pct": 50, "point_value_idr": 100}),
            ("points_method", stub),
        ):
            patcher = patch(f"{LOYALTY}.{target}", return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _place(self):
        tech = self.fx["tech"]
        lines = [{"product_id": self.fx["product"].id, "qty": 3}]
        quote = self.env["efn.online.channel"].with_user(tech).efn_online_quote("TCP", "TOKO-C1", "pickup", lines)
        part = str(uuid.uuid4())
        self.env["efn.online.order"].with_user(tech).efn_online_place(
            "TCP",
            {
                "idempotency_key": f"online:place:{part}",
                "platform_order_ref": part,
                "checkout_ref": str(uuid.uuid4()),
                "outlet_code": "TOKO-C1",
                "fulfilment": "pickup",
                "lines": lines,
                "quote_hash": quote["quote_hash"],
                "pickup_code": "ABCD2345",
                "payment_mode": "counter",
                "counter_code": CODE,
                "points_payment": {"points": 100, "value_idr": 10000},
            },
        )
        return online_record(self.env, part)

    def _sync(self, data):
        self.env["pos.order"].with_user(self.cashier).sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _settle_data(self, online, price):
        data = self.create_ui_order_data([(self.counter_product, 1)], payments=[(self.cash_pm1, price)])
        data["lines"][0][2].update(
            price_unit=price, price_subtotal=price, price_subtotal_incl=price, efn_online_order_label=online.name
        )
        data.update(amount_total=price, amount_tax=0.0, amount_paid=price)
        return data

    def _refund_data(self, pos_order):
        line = pos_order.lines[:1]
        amount = line.price_subtotal_incl
        data = self.create_ui_order_data(
            [{"product": self.counter_product, "quantity": -1, "refunded_orderline_id": line.id}],
            payments=[(self.cash_pm1, -amount)],
            pos_order_ui_args={"is_refund": True},
        )
        data["lines"][0][2].update(price_unit=amount, price_subtotal=-amount, price_subtotal_incl=-amount)
        data.update(amount_total=-amount, amount_tax=0.0, amount_paid=-amount)
        return data

    def _clearing_balance(self):
        lines = (
            self.env["account.move.line"]
            .sudo()
            .search([("account_id", "=", self.clearing.id), ("parent_state", "=", "posted")])
        )
        return self.company.currency_id.round(sum(lines.mapped("balance")))

    def test_counter_with_points_lookup_settle_refund(self):
        online = self._place()
        self.assertEqual((online.amount_payable, online.points_value, online.amount_due), (33297.0, 10000.0, 23297.0))
        found = self.config.with_user(self.cashier).efn_online_counter_lookup(CODE)
        self.assertEqual(found, {"order_label": online.name, "amount_due": 23297})
        # Menagih amount_payable penuh (bukan porsi tunai) ditolak server.
        with self.assertRaises(UserError), mute_logger("odoo.addons.efn_online_pos_counter"):
            self._sync(self._settle_data(online, 33297.0))
        pos_order = self._sync(self._settle_data(online, 23297.0))
        self.assertEqual(online.state, "confirmed")
        invoice = online.invoice_id
        self.assertEqual(invoice.amount_total, 33297.0)
        Payment = self.env["account.payment"].sudo()
        kliring = Payment.search([("journal_id", "=", self.company.efn_online_counter_journal_id.id)])
        points = Payment.search([("journal_id", "=", self.points_journal.id)])
        self.assertEqual((kliring.mapped("amount"), points.mapped("amount")), ([23297.0], [10000.0]))
        paid = online.event_ids.filtered(lambda e: e.event == "paid")
        self.assertEqual(paid._efn_payload()["payment"], {"method": "counter", "points_value": 10000})
        # Refund penuh lewat CS -> cash_amount = porsi tunai; otorisasi nominal penuh ditolak, porsi tunai diterima.
        api = self.env["efn.online.order"].with_user(self.fx["tech"])
        ref = online.platform_order_ref
        refund_payload = {"idempotency_key": f"online:refund:{ref}", "full": True, "reason": "customer_cs"}
        result = api.efn_online_refund("TCP", ref, refund_payload)
        self.assertEqual((result["amount"], result["points_value"], result["cash_amount"]), (33297, 10000, 23297))

        def authorize(amount):
            bff_id = str(uuid.uuid4())
            return api.efn_online_counter_refund_authorize(
                "TCP",
                online.platform_order_ref,
                {
                    "idempotency_key": f"online:counter_refund_auth:{bff_id}",
                    "refund_id": bff_id,
                    "refund_ref": result["refund_ref"],
                    "amount": amount,
                },
            )

        with self.assertRaisesRegex(Exception, "efn_online:amount_mismatch"):
            authorize(33297)
        self.assertEqual(authorize(23297)["state"], "authorized")
        refund_pos = self._sync(self._refund_data(pos_order))
        self.assertEqual(refund_pos.state, "paid")
        refund = online.refund_ids
        self.assertEqual(refund.state, "settled")
        outbound = Payment.search([("payment_type", "=", "outbound"), ("journal_id", "=", self.points_journal.id)])
        self.assertEqual(outbound.mapped("amount"), [10000.0], "porsi Poin EFN dikembalikan lewat jurnal Poin EFN")
        settled = online.event_ids.filtered(lambda e: e.event == "refund_settled")._efn_payload()["refund"]
        self.assertEqual((settled["points_value"], settled["cash_amount"]), (10000, 23297))
        session = self.pos_session
        cash = sum(session.order_ids.payment_ids.filtered(lambda p: p.payment_method_id.is_cash_count).mapped("amount"))
        session.post_closing_cash_details(cash)
        session.close_session_from_ui()
        self.assertEqual(self._clearing_balance(), 0.0, "kliring bersih 0 dengan porsi Poin EFN")
