"""efn_account_reports (G28, W-16): neraca seimbang, laba rugi, neraca saldo, konsolidasi + eliminasi antar-PT."""

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install", "efn")
class TestEfnFinancialReports(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.c1 = cls.company_data["company"]
        cls.c2_data = cls.setup_other_company(name="PT dua uji")
        cls.c2 = cls.c2_data["company"]
        cls.env.user.company_ids |= cls.c2
        cls.engine = cls.env["efn.financial.report.engine"]
        cls.customer = cls.env["res.partner"].create({"name": "Pelanggan luar"})

    def _invoice(self, company, partner, amount, move_type="out_invoice"):
        move = self.init_invoice(
            move_type,
            partner=partner,
            invoice_date="2026-10-10",
            amounts=[amount],
            taxes=[],
            company=company,
            post=True,
        )
        return move

    def test_single_company_bs_balances_and_pl(self):
        self._invoice(self.c1, self.customer, 1000)
        self._invoice(self.c1, self.customer, 300, "in_invoice")
        pl = self.engine._efn_profit_loss(self.c1, self.c1.compute_fiscalyear_dates(self._d())["date_from"], self._d())
        self.assertEqual(pl["totals"]["revenue"], 1000)
        self.assertEqual(pl["totals"]["net_profit"], 700)
        bs = self.engine._efn_balance_sheet(self.c1, self._d())
        self.assertEqual(bs["totals"]["difference"], 0)
        self.assertEqual(bs["totals"]["current_profit"], 700)
        self.assertEqual(bs["totals"]["asset"], 1000)
        self.assertEqual(bs["totals"]["liability"], 300)

    def test_trial_balance_sums_to_zero(self):
        self._invoice(self.c1, self.customer, 500)
        tb = self.engine._efn_trial_balance(self.c1, self._d().replace(month=1, day=1), self._d())
        self.assertEqual(tb["totals"]["debit"], tb["totals"]["credit"])
        self.assertEqual(tb["totals"]["closing"], 0)

    def test_consolidation_eliminates_intercompany(self):
        self._invoice(self.c1, self.customer, 1000)
        # c1 menjual ke c2 (antar-PT) dan c2 mencatat tagihannya.
        self._invoice(self.c1, self.c2.partner_id, 400)
        self._invoice(self.c2, self.c1.partner_id, 400, "in_invoice")
        both = self.c1 | self.c2
        start = self._d().replace(month=1, day=1)
        pl = self.engine._efn_profit_loss(both, start, self._d())
        self.assertEqual(pl["totals"]["revenue"], 1000)
        self.assertEqual(pl["totals"]["net_profit"], 1000)
        self.assertTrue(pl["eliminations"])
        bs = self.engine._efn_balance_sheet(both, self._d())
        self.assertEqual(bs["totals"]["difference"], 0)
        self.assertEqual(bs["totals"]["asset"], 1000)
        self.assertEqual(bs["totals"].get("liability", 0), 0)
        # Tanpa eliminasi: penjualan antar-PT ikut terhitung.
        raw = self.engine._efn_profit_loss(both, start, self._d(), eliminate=False)
        self.assertEqual(raw["totals"]["revenue"], 1400)

    def test_wizard_renders_html_and_xlsx(self):
        self._invoice(self.c1, self.customer, 250)
        wiz = self.env["efn.financial.report"].create(
            {
                "report_type": "pl",
                "company_ids": [(6, 0, self.c1.ids)],
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            }
        )
        self.assertIn("Laba Rugi", wiz.html)
        action = wiz.action_xlsx()
        self.assertTrue(wiz.xlsx_file)
        self.assertIn("/web/content/", action["url"])

    def test_form_view_has_no_python_only_builtins(self):
        """Regresi: web client Odoo 19 tidak mengenal len() di atribut invisible -> OwlError saat menu dibuka."""
        from lxml import etree

        arch = self.env.ref("efn_account_reports.efn_financial_report_view_form").arch
        for node in etree.fromstring(arch.encode()).iter():
            for attr in ("invisible", "readonly", "required", "column_invisible"):
                self.assertNotIn("len(", node.get(attr) or "", f"{node.get('name')}.{attr}")
        Wiz = self.env["efn.financial.report"]
        one = Wiz.create({"company_ids": [(6, 0, self.c1.ids)]})
        two = Wiz.create({"company_ids": [(6, 0, (self.c1 | self.c2).ids)]})
        self.assertFalse(one.is_consolidated)
        self.assertTrue(two.is_consolidated)

    def _d(self):
        from datetime import date

        return date(2026, 10, 31)
