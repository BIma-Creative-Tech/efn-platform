"""efn_account_reports (G28/G34/G35, W-16): neraca, L/R, arus kas, neraca saldo, konsolidasi, pembanding, drill."""

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
    def _pay(self, move, payment_date="2026-10-15"):
        """Lunasi faktur/tagihan lewat jurnal bank (register payment CE)."""
        return (
            self.env["account.payment.register"]
            .with_context(active_model="account.move", active_ids=move.ids)
            .create({"payment_date": payment_date})
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

    # ------------------------------------------------------------------ G35 pembanding + drill-down
    def test_compare_dates(self):
        from datetime import date

        from odoo.addons.efn_account_reports.wizard.efn_financial_report import compare_dates

        d = date
        # Bulan utuh -> bulan sebelumnya; kuartal -> kuartal sebelumnya.
        self.assertEqual(
            compare_dates("previous_period", "pl", d(2026, 10, 1), d(2026, 10, 31)), (d(2026, 9, 1), d(2026, 9, 30))
        )
        self.assertEqual(
            compare_dates("previous_period", "pl", d(2026, 4, 1), d(2026, 6, 30)), (d(2026, 1, 1), d(2026, 3, 31))
        )
        self.assertEqual(
            compare_dates("previous_period", "cf", d(2026, 3, 1), d(2026, 3, 31)), (d(2026, 2, 1), d(2026, 2, 28))
        )
        # Rentang bebas -> panjang hari sama, berakhir sehari sebelum date_from.
        self.assertEqual(
            compare_dates("previous_period", "pl", d(2026, 10, 11), d(2026, 10, 20)), (d(2026, 10, 1), d(2026, 10, 10))
        )
        # Tahun lalu; akhir Februari kabisat tetap akhir bulan.
        self.assertEqual(
            compare_dates("previous_year", "pl", d(2026, 1, 1), d(2026, 10, 31)), (d(2025, 1, 1), d(2025, 10, 31))
        )
        self.assertEqual(compare_dates("previous_year", "bs", d(2025, 2, 1), d(2025, 2, 28))[1], d(2024, 2, 29))
        # Neraca periode sebelumnya = posisi akhir bulan lalu.
        self.assertEqual(compare_dates("previous_period", "bs", d(2026, 1, 1), d(2026, 10, 31))[1], d(2026, 9, 30))

    def _wiz(
        self, report_type, compare="previous_period", companies=None, date_from="2026-10-01", date_to="2026-10-31"
    ):
        return self.env["efn.financial.report"].create(
            {
                "report_type": report_type,
                "company_ids": [(6, 0, (companies or self.c1).ids)],
                "date_from": date_from,
                "date_to": date_to,
                "compare": compare,
            }
        )

    def _sep_invoice(self, amount):
        move = self.init_invoice(
            "out_invoice",
            partner=self.customer,
            invoice_date="2026-09-15",
            amounts=[amount],
            taxes=[],
            company=self.c1,
            post=True,
        )
        return move

    def test_pl_compare_previous_month(self):
        self._sep_invoice(600)
        self._invoice(self.c1, self.customer, 1000)  # Oktober
        wiz = self._wiz("pl")
        data = wiz._efn_data()
        self.assertEqual(data["compare"]["date_from"].isoformat(), "2026-09-01")
        self.assertEqual(data["totals"]["revenue"], 1000)
        self.assertEqual(data["totals_cmp"]["revenue"], 600)
        income = next(s for s in data["sections"] if s["key"] == "income")
        self.assertEqual(income["total_cmp"], 600)
        self.assertEqual(income["total_delta"], 400)
        self.assertEqual(sum(line["delta"] for line in income["lines"]), 400)
        self.assertTrue(all(line["account_ids"] for line in income["lines"]))
        self.assertIn("Pembanding", wiz.html)
        self.assertIn("efn-drill", wiz.html)
        wiz.action_xlsx()
        self.assertTrue(wiz.xlsx_file)

    def test_line_only_in_compare_period_is_kept(self):
        account = (
            self.env["account.account"]
            .with_company(self.c1)
            .create({"name": "Pendapatan lain uji", "code": "EFNOI1", "account_type": "income_other"})
        )
        move = self._sep_invoice(250)
        move.button_draft()
        move.invoice_line_ids.account_id = account
        move.action_post()
        data = self._wiz("pl")._efn_data()
        other = next(s for s in data["sections"] if s["key"] == "income_other")
        line = next(line for line in other["lines"] if line["code"] == "EFNOI1")
        self.assertEqual((line["amount"], line["amount_cmp"], line["delta"]), (0.0, 250, -250))

    def test_bs_and_cf_compare_keep_difference_zero(self):
        self._pay(self._sep_invoice(700), "2026-09-20")
        self._invoice(self.c1, self.customer, 300)
        bs = self._wiz("bs")._efn_data()
        self.assertEqual(bs["totals"]["difference"], 0)
        self.assertEqual(bs["totals_cmp"]["difference"], 0)
        self.assertEqual(bs["totals"]["asset"] - bs["totals_cmp"]["asset"], 300)
        cf = self._wiz("cf")._efn_data()
        self.assertEqual(cf["totals"]["difference"], 0)
        self.assertEqual(cf["totals_cmp"]["difference"], 0)
        self.assertEqual(cf["totals_cmp"]["net_change"], 700)
        self.assertEqual(cf["totals"]["cash_opening"], cf["totals_cmp"]["cash_closing"])

    def test_trial_balance_ignores_compare(self):
        self._invoice(self.c1, self.customer, 100)
        wiz = self._wiz("tb")
        self.assertIsNone(wiz._efn_data()["compare"])
        self.assertIn("efn-drill", wiz.html)

    def test_drill_returns_posted_move_lines_of_line(self):
        self._sep_invoice(600)
        inv = self._invoice(self.c1, self.customer, 1000)
        draft = self.init_invoice(
            "out_invoice", partner=self.customer, invoice_date="2026-10-12", amounts=[50], taxes=[], company=self.c1
        )
        wiz = self._wiz("pl")
        income = next(s for s in wiz._efn_data()["sections"] if s["key"] == "income")
        ids = income["lines"][0]["account_ids"]
        action = wiz.action_drill(ids)
        self.assertEqual(action["res_model"], "account.move.line")
        self.assertEqual(wiz.display_name, "Laba Rugi")
        lines = self.env["account.move.line"].search(action["domain"])
        self.assertTrue(lines)
        self.assertEqual(lines.move_id, inv)
        self.assertNotIn(draft, lines.move_id)
        self.assertEqual(
            round(sum(lines.mapped("credit")) - sum(lines.mapped("debit")), 2), income["lines"][0]["amount"]
        )
        prev = self.env["account.move.line"].search(wiz.action_drill(ids, "compare")["domain"])
        self.assertEqual(round(sum(prev.mapped("credit")) - sum(prev.mapped("debit")), 2), 600)
        # Neraca: s.d. tanggal (tanpa batas awal).
        bs = self._wiz("bs", compare="none")
        recv = next(s for s in bs._efn_data()["sections"] if s["key"] == "asset_receivable")["lines"][0]
        bs_lines = self.env["account.move.line"].search(bs.action_drill(recv["account_ids"])["domain"])
        self.assertEqual(round(sum(bs_lines.mapped("balance")), 2), recv["amount"])

    def test_drill_rejects_foreign_or_bad_input(self):
        from odoo.exceptions import UserError

        wiz = self._wiz("pl", compare="none")
        foreign = self.env["account.account"].search(
            [("company_ids", "in", self.c2.ids), ("company_ids", "not in", self.c1.ids)], limit=1
        )
        self.assertTrue(foreign)
        with self.assertRaisesRegex(UserError, "efn_account_reports:company"):
            wiz.action_drill(foreign.ids)
        for bad in ([], ["1"], [10**9]):
            with self.assertRaisesRegex(UserError, "efn_account_reports:drill"):
                wiz.action_drill(bad)
        own = self.company_data["default_account_revenue"]
        with self.assertRaisesRegex(UserError, "efn_account_reports:drill"):
            wiz.action_drill(own.ids, "compare")  # laporan tanpa pembanding
        with self.assertRaisesRegex(UserError, "efn_account_reports:drill"):
            wiz.action_drill(own.ids, "x")

    def test_drill_requires_report_access(self):
        from odoo.exceptions import AccessError, UserError

        from odoo.addons.base.tests.common import new_test_user

        wiz = self._wiz("pl", compare="none")
        acc = self.company_data["default_account_revenue"]
        outsider = new_test_user(self.env, login="efn_g35_outsider", groups="base.group_user", company_id=self.c1.id)
        with self.assertRaises(Exception) as cm:
            wiz.with_user(outsider).action_drill(acc.ids)
        self.assertIsInstance(cm.exception, (AccessError, UserError))

    def test_html_in_onchange_uses_real_companies(self):
        """Regresi: form live (onchange, record NewId) dulu selalu menampilkan "PT di luar akses Anda"."""
        from odoo.tests import Form

        self._invoice(self.c1, self.customer, 100)
        with Form(self.env["efn.financial.report"]) as form:
            form.report_type = "pl"
            form.compare = "previous_period"
            self.assertNotIn("efn_account_reports:", form.html or "")
            self.assertIn("Laba Rugi", form.html)
            self.assertIn("Pembanding", form.html)

    def test_form_view_compare_and_widget(self):
        from lxml import etree

        arch = etree.fromstring(self.env.ref("efn_account_reports.efn_financial_report_view_form").arch.encode())
        self.assertEqual(arch.xpath("//field[@name='html']")[0].get("widget"), "efn_report_html")
        self.assertTrue(arch.xpath("//field[@name='compare']"))

    def _d(self):
        from datetime import date

        return date(2026, 10, 31)
