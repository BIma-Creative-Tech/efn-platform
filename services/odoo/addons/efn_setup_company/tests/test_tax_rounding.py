"""G20 18-6 (Q-F18-1): pembulatan pajak per dokumen (round_globally) di semua PT; docs/08 §11.1."""

from odoo.tests import TransactionCase, tagged

COMPANIES = ("company_efn", "company_rtl", "company_fnb", "company_farm")


@tagged("post_install", "-at_install", "efn")
class TestTaxRounding(TransactionCase):
    def test_all_efn_companies_round_globally(self):
        for xmlid in COMPANIES:
            with self.subTest(company=xmlid):
                company = self.env.ref(f"efn_setup_company.{xmlid}")
                self.assertEqual(company.tax_calculation_rounding_method, "round_globally")

    def test_company_display_names_follow_entity_structure(self):
        # 18-27: nama tampilan baru (PLACEHOLDER nama legal); kode kontrak BFF/ETL tidak berubah.
        expected = {
            "company_efn": ("Holding EFN", "EFN"),
            "company_rtl": ("PT Grand Lucky", "RTL"),
            "company_fnb": ("PT Paris Baguette", "FNB"),
            "company_farm": ("FARM", "FARM"),
        }
        for xmlid, (name_prefix, code) in expected.items():
            with self.subTest(company=xmlid):
                company = self.env.ref(f"efn_setup_company.{xmlid}")
                self.assertTrue(company.name.startswith(name_prefix), company.name)
                self.assertEqual(company.efn_brand_code, code)
