"""G60-B (estimasi MDR saat tutup sesi) + G60-C (analitik Outlet) dengan sesi POS nyata (TestPoSCommon, tag efn)."""

from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.efn_account_localization.models.outlet_analytic import merge_outlet_distribution
from odoo.addons.point_of_sale.tests.common import TestPoSCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnPosMdrOutlet(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.warehouse = cls.config.warehouse_id
        cls.product_a = cls.create_product("G60 Produk A", cls.categ_basic, 300000.0)
        cls.product_b = cls.create_product("G60 Produk B", cls.categ_basic, 400000.0)
        cls.plan = cls.env.ref("efn_account_localization.analytic_plan_outlet")
        cls.outlet = cls.env["account.analytic.account"].create(
            {"name": "Outlet Uji G60", "code": "G60O", "plan_id": cls.plan.id, "company_id": cls.company.id}
        )
        cls.warehouse.efn_analytic_account_id = cls.outlet
        cls.mdr_account = cls.env["account.account"].create(
            {"code": "G60MDR", "name": "Beban MDR uji", "account_type": "expense"}
        )
        cls.company.efn_mdr_journal_id = cls.company_data["default_journal_misc"]
        # Ambang persetujuan aktif: payment POS (pos_session_id) tetap tidak tersentuh gerbang G60-A.
        cls.company.efn_payment_approval_threshold = 1.0
        cls.rate = cls._rate(cls.bank_pm1)

    @classmethod
    def _rate(cls, method, **vals):
        return cls.env["efn.mdr.rate"].create(
            {
                "company_id": cls.company.id,
                "payment_method_id": method.id,
                "mdr_percent": 1.5,
                "mdr_fixed": 500.0,
                "account_id": cls.mdr_account.id,
                "date_start": "2000-01-01",
                **vals,
            }
        )

    # ------------------------------------------------------------------------------------------------ helper
    def _sell(self, method, customer=False):
        """Tiga transaksi = 1.000.000 lewat ``method``."""
        orders = [
            self.create_ui_order_data([(product, 1)], customer=customer, payments=[(method, product.lst_price)])
            for product in (self.product_a, self.product_a, self.product_b)
        ]
        self.env["pos.order"].sync_from_ui(orders)

    def _close(self):
        self.pos_session.action_pos_session_closing_control()
        self.assertEqual(self.pos_session.state, "closed")
        return self.pos_session

    def _balance(self, moves, account):
        return sum(moves.line_ids.filtered(lambda line: line.account_id == account).mapped("balance"))

    def _session_payment_moves(self, session):
        return self.env["account.payment"].search([("pos_session_id", "=", session.id)]).move_id

    # --------------------------------------------------------------------------------------------------- MDR
    def test_mdr_estimate_booked_on_close(self):
        self.open_new_session()
        self._sell(self.bank_pm1)
        session = self._close()
        mdr_moves = session.efn_mdr_move_ids
        self.assertEqual(len(mdr_moves), 1)
        self.assertEqual(mdr_moves.state, "posted")
        self.assertEqual(mdr_moves.journal_id, self.company_data["default_journal_misc"])
        self.assertAlmostEqual(self._balance(mdr_moves, self.mdr_account), 16500.0)
        outstanding = self.bank_pm1.outstanding_account_id
        self.assertAlmostEqual(self._balance(mdr_moves, outstanding), -16500.0)
        net = self._balance(self._session_payment_moves(session) | mdr_moves, outstanding)
        self.assertAlmostEqual(net, 983500.0, msg="outstanding neto = bruto - MDR")
        expense_line = mdr_moves.line_ids.filtered(lambda line: line.account_id == self.mdr_account)
        self.assertIn(str(self.outlet.id), ",".join(expense_line.analytic_distribution or {}))
        credit_line = mdr_moves.line_ids.filtered(lambda line: line.account_id == outstanding)
        self.assertFalse(credit_line.analytic_distribution, "baris neraca tidak dicap (Q-G60-8)")

    def test_split_transactions_same_result(self):
        self.rate.payment_method_id = self.bank_split_pm1
        self.open_new_session()
        self._sell(self.bank_split_pm1, customer=self.customer)
        session = self._close()
        self.assertAlmostEqual(self._balance(session.efn_mdr_move_ids, self.mdr_account), 16500.0)

    def test_rate_not_in_effect_no_move(self):
        self.rate.write({"date_start": "2999-01-01"})
        self.open_new_session()
        self._sell(self.bank_pm1)
        session = self._close()
        self.assertFalse(session.efn_mdr_move_ids)

    def test_incomplete_config_does_not_block_close(self):
        self.open_new_session()
        self._sell(self.bank_pm1)
        Session = type(self.env["pos.session"])
        with patch.object(Session, "_efn_mdr_journal", lambda self: self.env["account.journal"]):
            session = self._close()
        self.assertFalse(session.efn_mdr_move_ids)
        self.assertTrue(
            any("Estimasi MDR" in (message.body or "") for message in session.message_ids),
            "konfigurasi kurang dilaporkan di chatter sesi",
        )

    def test_rate_overlap_and_bank_only(self):
        with self.assertRaises(ValidationError):
            self._rate(self.bank_pm1, date_start="2020-01-01")
        with self.assertRaises(ValidationError):
            self._rate(self.cash_pm1)
        with self.assertRaises(ValidationError):
            self._rate(self.bank_split_pm1, mdr_percent=101)

    def test_spoofed_pos_session_still_gated(self):
        """SEC-G60-01: payment vendor dengan pos_session_id tulisan user (bukan alur tutup sesi) tetap tertahan."""
        session = self.open_new_session()
        payment = self.env["account.payment"].create(
            {
                "payment_type": "outbound",
                "partner_type": "supplier",
                "partner_id": self.customer.id,
                "amount": 5000.0,
                "journal_id": self.company_data["default_journal_bank"].id,
                "pos_session_id": session.id,
            }
        )
        self.assertFalse(self.env.su)
        self.assertTrue(payment._efn_needs_approval())
        with self.assertRaises(UserError):
            payment.action_post()

    def test_rate_company_isolation(self):
        other = self.setup_other_company(name="G60 PT POS B")["company"]
        with self.assertRaises(UserError):
            self.env["efn.mdr.rate"].create(
                {
                    "company_id": other.id,
                    "payment_method_id": self.bank_split_pm1.id,
                    "mdr_percent": 1.0,
                    "account_id": self.mdr_account.id,
                    "date_start": "2000-01-01",
                }
            )
        user_b = new_test_user(
            self.env,
            login="g60_readonly_b",
            groups="base.group_user,account.group_account_readonly",
            company_id=other.id,
            company_ids=[Command.set(other.ids)],
        )
        Rate = self.env["efn.mdr.rate"].with_user(user_b).with_company(other)
        self.assertFalse(Rate.search([("id", "=", self.rate.id)]), "rate PT lain tidak terlihat")

    # ------------------------------------------------------------------------------------------------ outlet
    def _sales_lines(self, session):
        return session.move_id.line_ids.filtered(lambda line: line.account_id.internal_group == "income")

    def test_outlet_stamped_on_pnl_lines_only(self):
        self.open_new_session()
        self._sell(self.bank_pm1)
        session = self._close()
        sales = self._sales_lines(session)
        self.assertTrue(sales)
        for line in sales:
            keys = [key.split(",") for key in line.analytic_distribution or {}]
            self.assertTrue(keys and all(str(self.outlet.id) in key for key in keys), line.analytic_distribution)
        balance_sheet = session.move_id.line_ids.filtered(
            lambda line: line.account_id.internal_group not in ("income", "expense")
        )
        self.assertTrue(balance_sheet)
        self.assertFalse(any(line.analytic_distribution for line in balance_sheet), "piutang/pajak tidak dicap")
        column = self.plan._column_name()
        analytic = self.env["account.analytic.line"].search([(column, "=", self.outlet.id)])
        sales_analytic = analytic.filtered(lambda a: a.general_account_id in sales.account_id)
        self.assertAlmostEqual(sum(sales_analytic.mapped("amount")), 1000000.0)

    def test_existing_distribution_kept_and_outlet_added(self):
        other_plan = self.env["account.analytic.plan"].create({"name": "G60 Brand"})
        brand = self.env["account.analytic.account"].create(
            {"name": "Brand uji", "plan_id": other_plan.id, "company_id": self.company.id}
        )
        sales_account = self.product_a.product_tmpl_id._get_product_accounts()["income"]
        self.env["account.analytic.distribution.model"].create(
            {
                "account_prefix": sales_account.code,
                "analytic_distribution": {str(brand.id): 100},
                "company_id": self.company.id,
            }
        )
        self.open_new_session()
        self._sell(self.bank_pm1)
        session = self._close()
        line = self._sales_lines(session).filtered(lambda line: line.account_id == sales_account)
        self.assertTrue(line)
        keys = [set(key.split(",")) for key in line.analytic_distribution]
        self.assertEqual(keys, [{str(brand.id), str(self.outlet.id)}], "outlet ditambahkan, bukan menimpa")

    def test_unmapped_warehouse_not_stamped(self):
        self.warehouse.efn_analytic_account_id = False
        self.company.efn_outlet_analytic_plan_id = self.plan
        self.open_new_session()
        self._sell(self.bank_pm1)
        session = self._close()
        self.assertFalse(any(line.analytic_distribution for line in self._sales_lines(session)))
        self.assertTrue(any("Analitik Outlet" in (message.body or "") for message in session.message_ids))

    def test_merge_helper(self):
        self.assertEqual(merge_outlet_distribution({}, 7, set()), {"7": 100.0})
        self.assertEqual(merge_outlet_distribution({"3": 60.0, "4": 40.0}, 7, {7}), {"3,7": 60.0, "4,7": 40.0})
        self.assertEqual(merge_outlet_distribution({"3,7": 100.0}, 7, {7}), {"3,7": 100.0}, "idempoten")
        self.assertEqual(merge_outlet_distribution({"8": 100.0}, 7, {7, 8}), {"8": 100.0}, "outlet lain dipertahankan")

    # ------------------------------------------------------------------------------------------------ wizard
    def _wizard(self, user=None):
        return (
            self.env["efn.outlet.analytic.wizard"]
            .with_user(user or self.env.user)
            .with_company(self.company)
            .create({"company_id": self.company.id})
        )

    def test_wizard_idempotent_and_shared_warehouse(self):
        self.warehouse.efn_analytic_account_id = False
        second = self.env["pos.config"].create(
            {
                "name": "G60 Kasir 2",
                "picking_type_id": self.config.picking_type_id.id,
                "payment_method_ids": [Command.set([])],
            }
        )
        self.assertEqual(second.warehouse_id, self.warehouse)
        check = self._wizard()
        check.action_check()
        self.assertFalse(self.warehouse.efn_analytic_account_id, "cek tidak menulis")
        self.assertIn(
            "create", check.line_ids.filtered(lambda line: line.warehouse_id == self.warehouse).mapped("status")
        )
        Account = self.env["account.analytic.account"]
        before = Account.search_count([("plan_id", "=", self.plan.id)])
        self._wizard().action_apply()
        account = self.warehouse.efn_analytic_account_id
        self.assertTrue(account)
        self.assertEqual(account.plan_id, self.plan)
        self.assertEqual(account.company_id, self.company)
        self.assertEqual(self.company.efn_outlet_analytic_plan_id, self.plan)
        after_first = Account.search_count([("plan_id", "=", self.plan.id)])
        self.assertGreater(after_first, before)
        self._wizard().action_apply()
        self.assertEqual(Account.search_count([("plan_id", "=", self.plan.id)]), after_first, "apply kedua: 0 duplikat")
        self.assertEqual(second.warehouse_id.efn_analytic_account_id, account, "dua kasir satu gudang = satu akun")

    def test_wizard_requires_manager_and_company(self):
        cashier = new_test_user(
            self.env,
            login="g60_wizard_user",
            groups="base.group_user,account.group_account_invoice",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        with self.assertRaises(AccessError):
            self._wizard(cashier).action_apply()

    def test_purchase_line_gets_outlet(self):
        po = self.env["purchase.order"].create(
            {
                "partner_id": self.customer.id,
                "picking_type_id": self.warehouse.in_type_id.id,
                "order_line": [Command.create({"product_id": self.product_a.id, "product_qty": 1, "price_unit": 10})],
            }
        )
        self.assertIn(str(self.outlet.id), ",".join(po.order_line.analytic_distribution or {}))
