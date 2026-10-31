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

    # ------------------------------------------------------------------ G34 arus kas
    def _pay(self, move):
        """Lunasi faktur/tagihan lewat jurnal bank (register payment CE)."""
        return (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create({"payment_date": "2026-10-15"})
            ._create_payments()
        )

    def _entry(self, company, lines, ref="uji arus kas"):
        """Jurnal umum posted: lines = [(account, debit, credit)]."""
        journal = self.env["account.journal"].search(
            [("company_id", "=", company.id), ("type", "=", "general")], limit=1
        )
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "date": "2026-10-20",
                "journal_id": journal.id,
                "ref": ref,
                "company_id": company.id,
                "line_ids": [
                    (0, 0, {"account_id": acc.id, "debit": d, "credit": c, "name": ref}) for acc, d, c in lines
                ],
            }
        )
        move.action_post()
        return move

    def _cf(self, companies, **kw):
        start = self._d().replace(month=1, day=1)
        return self.engine._efn_cash_flow(companies, start, self._d(), **kw)

    def _section(self, cf, key):
        return next((s for s in cf["sections"] if s["key"] == key), {"lines": [], "total": 0})

    def test_cash_flow_operating_reconciles_to_cash(self):
        inv = self._invoice(self.c1, self.customer, 1000)
        bill = self._invoice(self.c1, self.customer, 300, "in_invoice")
        self._pay(inv)
        self._invoice(self.c1, self.customer, 200)  # belum dibayar -> kenaikan piutang
        cf = self._cf(self.c1)
        t = cf["totals"]
        self.assertEqual(t["difference"], 0)
        # Laba 900 (1000 + 200 - 300); piutang naik 200 (-200); utang naik 300 (+300) -> kas operasi 1000.
        op = self._section(cf, "operating")
        self.assertEqual(op["lines"][0]["amount"], 900)
        self.assertEqual(t["operating"], 1000)
        self.assertEqual(t["net_change"], 1000)
        self.assertEqual(t["cash_closing"] - t["cash_opening"], 1000)
        self.assertTrue(bill)

    def test_cash_flow_fixed_asset_depreciation_and_capital(self):
        c = self.c1
        Account = self.env["account.account"].with_company(c)
        bank = self.company_data["default_journal_bank"].default_account_id
        fixed = Account.create({"name": "Mesin oven uji", "code": "EFNFA1", "account_type": "asset_fixed"})
        dep_exp = Account.create(
            {"name": "Beban penyusutan uji", "code": "EFNDEP1", "account_type": "expense_depreciation"}
        )
        capital = Account.create({"name": "Modal disetor uji", "code": "EFNCAP1", "account_type": "equity"})
        loan = Account.create(
            {"name": "Utang bank jangka panjang uji", "code": "EFNLOAN1", "account_type": "liability_non_current"}
        )
        self._entry(c, [(bank, 5000, 0), (capital, 0, 5000)], "setor modal")
        self._entry(c, [(bank, 2000, 0), (loan, 0, 2000)], "pinjaman bank")
        self._entry(c, [(fixed, 3000, 0), (bank, 0, 3000)], "beli mesin")
        self._entry(c, [(dep_exp, 500, 0), (fixed, 0, 500)], "penyusutan")
        cf = self._cf(c)
        t = cf["totals"]
        self.assertEqual(t["difference"], 0)
        # Operasi: laba -500 + penyusutan 500 = 0; investasi: -2500 (aset tetap neto) + -500 (penyusutan) ... = -3000
        self.assertEqual(t["operating"], 0)
        self.assertEqual(t["investing"], -3000)
        self.assertEqual(t["financing"], 7000)
        self.assertEqual(t["net_change"], 4000)
        names = [line["name"] for line in self._section(cf, "operating")["lines"]]
        self.assertIn("Penyusutan (beban non-kas)", names)

    def test_cash_flow_tag_overrides_default_category(self):
        c = self.c1
        Account = self.env["account.account"].with_company(c)
        bank = self.company_data["default_journal_bank"].default_account_id
        deposit = Account.create(
            {
                "name": "Deposito jaminan sewa uji",
                "code": "EFNDEPO1",
                "account_type": "asset_current",
                "tag_ids": [(4, self.env.ref("account.account_tag_investing").id)],
            }
        )
        self._entry(c, [(deposit, 800, 0), (bank, 0, 800)], "deposito sewa")
        cf = self._cf(c)
        self.assertEqual(cf["totals"]["difference"], 0)
        self.assertEqual(cf["totals"]["investing"], -800)
        self.assertNotIn("EFNDEPO1", [line["code"] for line in self._section(cf, "operating")["lines"]])

    def test_cash_flow_consolidated_eliminates_intercompany(self):
        # c1 menjual ke c2 400 dan c2 membayar lunas; plus penjualan luar c1 1000 lunas.
        self._pay(self._invoice(self.c1, self.customer, 1000))
        ic_sale = self._invoice(self.c1, self.c2.partner_id, 400)
        ic_bill = self._invoice(self.c2, self.c1.partner_id, 400, "in_invoice")
        self._pay(ic_sale)
        self._pay(ic_bill)
        both = self.c1 | self.c2
        cf = self._cf(both)
        self.assertEqual(cf["totals"]["difference"], 0)
        # Arus kas grup: hanya kas dari pelanggan luar; kas antar-PT saling hapus (400 masuk c1, 400 keluar c2).
        self.assertEqual(cf["totals"]["net_change"], 1000)
        self.assertEqual(
            self._section(cf, "operating")["lines"][0]["amount"], 1000
        )  # laba grup tanpa penjualan antar-PT

    def test_wizard_cash_flow_html_and_xlsx(self):
        self._pay(self._invoice(self.c1, self.customer, 250))
        wiz = self.env["efn.financial.report"].create(
            {
                "report_type": "cf",
                "company_ids": [(6, 0, self.c1.ids)],
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            }
        )
        self.assertIn("Arus Kas", wiz.html)
        self.assertIn("aktivitas operasi", wiz.html)
        self.assertNotIn("text-danger", wiz.html)
        wiz.action_xlsx()
        self.assertTrue(wiz.xlsx_file)

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
