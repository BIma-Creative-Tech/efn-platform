"""G20 18-6 (Q-F18-1): pembulatan pajak per dokumen (round_globally) di semua PT; docs/08 §11.1.
G20 W-32/E-43 (docs/04 §12.1, L-48): 6 entitas root dengan kode GL/PB/BCR/BCF."""

from odoo.tests import TransactionCase, tagged

COMPANIES = ("company_efn", "company_rtl", "company_fnb", "company_bcr", "company_bcf", "company_farm")


@tagged("post_install", "-at_install", "efn")
class TestTaxRounding(TransactionCase):
    def test_all_efn_companies_round_globally(self):
        for xmlid in COMPANIES:
            with self.subTest(company=xmlid):
                company = self.env.ref(f"efn_setup_company.{xmlid}")
                self.assertEqual(company.tax_calculation_rounding_method, "round_globally")

    def test_company_display_names_follow_entity_structure(self):
        # 18-27 + §12.1: nama tampilan (PLACEHOLDER nama legal) dan kode kontrak BFF/ETL baru; xmlid lama tetap (L-48).
        expected = {
            "company_efn": ("Holding EFN", "EFN", "holding"),
            "company_rtl": ("PT Grand Lucky", "GL", "retail"),
            "company_fnb": ("PT Paris Baguette", "PB", "fnb"),
            "company_bcr": ("Bacha Coffee Retail", "BCR", "retail"),
            "company_bcf": ("Bacha Coffee F&B", "BCF", "fnb"),
            "company_farm": ("FARM", "FARM", "farm"),
        }
        for xmlid, (name_prefix, code, business_type) in expected.items():
            with self.subTest(company=xmlid):
                company = self.env.ref(f"efn_setup_company.{xmlid}")
                self.assertTrue(company.name.startswith(name_prefix), company.name)
                self.assertEqual(company.efn_brand_code, code)
                self.assertEqual(company.efn_business_type, business_type)

    def test_all_entities_are_root_companies(self):
        # §12.1: semua company root (bukan cabang); hierarki Odoo tidak bisa diubah setelah dibuat.
        for xmlid in COMPANIES:
            with self.subTest(company=xmlid):
                company = self.env.ref(f"efn_setup_company.{xmlid}")
                self.assertFalse(company.parent_id)
                self.assertEqual(company.country_id, self.env.ref("base.id"))

    def test_admin_can_switch_to_all_entities(self):
        admin = self.env.ref("base.user_admin")
        for xmlid in COMPANIES:
            with self.subTest(company=xmlid):
                self.assertIn(self.env.ref(f"efn_setup_company.{xmlid}"), admin.company_ids)
