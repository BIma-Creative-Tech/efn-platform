"""E-31: refund ke metode asal (docs/08 §9.3, L-23)."""

from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPosRefundOrigin(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.product = cls.create_product("Produk Refund Uji", cls.categ_basic, 50000.0)

    def setUp(self):
        super().setUp()
        self.open_new_session()
        self.env["ir.config_parameter"].sudo().set_param("efn.pos.refund.unlinked_return_mode", "cash")

    def _sync(self, data):
        self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].search([("uuid", "=", data["uuid"])])

    def _sale(self, qty=2, payments=None):
        return self._sync(self.create_ui_order_data([(self.product, qty)], payments=payments))

    def _refund_data(self, original, qty, payments):
        line = original.lines[:1]
        lines = [{"product": self.product, "quantity": -qty, "refunded_orderline_id": line.id}]
        return self.create_ui_order_data(lines, payments=payments, pos_order_ui_args={"is_refund": True})

    def _refund(self, original, qty, payments):
        return self._sync(self._refund_data(original, qty, payments))

    def test_refund_to_origin_method_ok(self):
        original = self._sale(payments=[(self.bank_pm1, 100000)])
        refund = self._refund(original, 1, [(self.bank_pm1, -50000)])
        self.assertEqual(refund.state, "paid")

    def test_refund_other_method_rejected(self):
        original = self._sale(payments=[(self.bank_pm1, 100000)])
        data = self._refund_data(original, 1, [(self.cash_pm1, -50000)])
        with self.assertRaises(UserError):
            self._sync(data)
        self.assertFalse(self.env["pos.order"].search([("uuid", "=", data["uuid"])]))

    def test_split_origin_caps_and_cumulative(self):
        original = self._sale(payments=[(self.cash_pm1, 60000), (self.bank_pm1, 40000)])
        allocation = {m: a for m, a, _r in original._efn_refund_allocation(50000)}
        self.assertEqual(allocation, {self.cash_pm1: 30000, self.bank_pm1: 20000}, "proporsional (Q-F18-4)")
        # Bank melebihi yang dibayar dengan bank -> ditolak.
        with self.assertRaises(UserError):
            self._refund(original, 1, [(self.bank_pm1, -50000)])
        first = self._refund(original, 1, [(self.cash_pm1, -30000), (self.bank_pm1, -20000)])
        self.assertEqual(first.state, "paid")
        allocation = {m: (a, r) for m, a, r in original._efn_refund_allocation(50000)}
        self.assertEqual(allocation[self.cash_pm1], (30000, 30000))
        self.assertEqual(allocation[self.bank_pm1], (20000, 20000))
        # Refund kedua: bank tersisa 20.000 -> 25.000 ditolak.
        with self.assertRaises(UserError):
            self._refund(original, 1, [(self.cash_pm1, -25000), (self.bank_pm1, -25000)])
        second = self._refund(original, 1, [(self.cash_pm1, -30000), (self.bank_pm1, -20000)])
        self.assertEqual(second.state, "paid")

    def test_cash_change_reduces_cash_paid(self):
        # Kembalian = pembayaran tunai negatif (seperti baris `is_change` core).
        original = self._sync(
            self.create_ui_order_data([(self.product, 1)], payments=[(self.cash_pm1, 70000), (self.cash_pm1, -20000)])
        )
        paid = original._efn_paid_by_method()
        self.assertEqual(paid[self.cash_pm1], 50000)
        with self.assertRaises(UserError):
            self._refund(original, 1, [(self.cash_pm1, -60000)])

    def test_unlinked_return_cash_only(self):
        ok = self._sync(self.create_ui_order_data([(self.product, -1)], payments=[(self.cash_pm1, -50000)]))
        self.assertEqual(ok.state, "paid")
        with self.assertRaises(UserError):
            self._sync(self.create_ui_order_data([(self.product, -1)], payments=[(self.bank_pm1, -50000)]))
        self.env["ir.config_parameter"].sudo().set_param("efn.pos.refund.unlinked_return_mode", "deny")
        with self.assertRaises(UserError):
            self._sync(self.create_ui_order_data([(self.product, -1)], payments=[(self.cash_pm1, -50000)]))

    def test_sale_and_exchange_without_outgoing_payment_untouched(self):
        sale = self._sale(payments=[(self.bank_pm1, 100000)])
        self.assertEqual(sale.state, "paid")

    def test_allocation_rpc(self):
        original = self._sale(payments=[(self.cash_pm1, 60000), (self.bank_pm1, 40000)])
        res = self.config.efn_refund_origin_allocation(original.id, 50000)
        self.assertTrue(res["ok"])
        rows = {row["payment_method_id"]: row["amount"] for row in res["allocation"]}
        self.assertEqual(rows, {self.cash_pm1.id: 30000, self.bank_pm1.id: 20000})
        self.assertEqual(self.config.efn_refund_origin_allocation(True, 1)["error_code"], "invalid_order")
        self.assertEqual(self.config.efn_refund_origin_allocation(original.id, "x")["error_code"], "invalid_amount")
        plain = new_test_user(
            self.env, login="efn_refund_plain", groups="base.group_user", company_id=self.env.company.id
        )
        with self.assertRaises(AccessError):
            self.config.with_user(plain).efn_refund_origin_allocation(original.id, 1000)
