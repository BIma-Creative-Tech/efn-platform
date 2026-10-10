"""G17-QA-04: label pajak ke pelanggan berbahasa Indonesia (invoice_label + nama grup), ADR-027."""

from odoo.tests import TransactionCase, tagged

COMPANIES = ("company_efn", "company_rtl", "company_fnb", "company_farm")


@tagged("post_install", "-at_install", "efn")
class TestTaxLabels(TransactionCase):
    def _tmpl(self, company_xmlid, name):
        company = self.env.ref(f"efn_setup_company.{company_xmlid}")
        return self.env.ref(f"account.{company.id}_{name}")

    def test_sale_tax_invoice_labels(self):
        expected = {
            "tax_ST4": "PPN 11%",
            "tax_ST5": "PPN 11%",
            "tax_ST3": "PPN 12%",
            "tax_ST6": "PPN 12%",
            "tax_luxury_sales": "PPnBM 20%",
            "tax_ST0": "PPN 0%",
            "tax_ST7": "PPN 0%",
            "tax_ST2": "Bebas PPN",
        }
        for company in COMPANIES:
            for name, label in expected.items():
                with self.subTest(company=company, tax=name):
                    self.assertEqual(self._tmpl(company, name).invoice_label, label)

    def test_tax_group_names(self):
        expected = {
            "l10n_id_tax_group_non_luxury_goods": "PPN 11%",
            "l10n_id_tax_group_luxury_goods": "PPN 12%",
            "l10n_id_tax_group_stlg": "PPnBM",
            "l10n_id_tax_group_0": "PPN 0%",
            "l10n_id_tax_group_exempt": "Bebas PPN",
            "default_tax_group": "Pajak",
        }
        for company in COMPANIES:
            for name, label in expected.items():
                with self.subTest(company=company, group=name):
                    self.assertEqual(self._tmpl(company, name).name, label)

    def test_default_sale_tax_label_per_pt(self):
        # Pajak penjualan default yang dipakai produk baru: PPN untuk RTL/EFN/FARM, PB1 untuk F&B.
        for company, label, group in (
            ("company_rtl", "PPN 11%", "PPN 11%"),
            ("company_efn", "PPN 11%", "PPN 11%"),
            ("company_farm", "PPN 11%", "PPN 11%"),
            ("company_fnb", "PB1", "PB1 (Pajak Restoran / PBJT)"),
        ):
            with self.subTest(company=company):
                tax = self.env.ref(f"efn_setup_company.{company}").account_sale_tax_id
                self.assertEqual(tax.invoice_label, label)
                self.assertEqual(tax.tax_group_id.name, group)
                self.assertNotIn("Luxury", tax.tax_group_id.name)

    def test_tax_totals_group_name(self):
        # Label subtotal pajak = tax_totals.group_name (sumber tax_groups di quote online E-28).
        company = self.env.ref("efn_setup_company.company_rtl")
        tax = self._tmpl("company_rtl", "tax_ST4")
        base_lines = [
            self.env["account.tax"]._prepare_base_line_for_taxes_computation(
                None, price_unit=10000.0, quantity=1.0, tax_ids=tax, currency_id=company.currency_id
            )
        ]
        self.env["account.tax"]._add_tax_details_in_base_lines(base_lines, company)
        self.env["account.tax"]._round_base_lines_tax_details(base_lines, company)
        totals = self.env["account.tax"]._get_tax_totals_summary(base_lines, company.currency_id, company)
        names = [g["group_name"] for s in totals["subtotals"] for g in s["tax_groups"]]
        self.assertEqual(names, ["PPN 11%"])
