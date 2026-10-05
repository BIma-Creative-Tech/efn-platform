from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install", "efn")
class TestEfnResCompany(TransactionCase):
    def test_brand_code_unique(self):
        Company = self.env["res.company"]
        # Bersihkan kode yang mungkin sudah dipakai data demo di DB uji.
        Company.search([("efn_brand_code", "=", "FARM")]).write({"efn_brand_code": False})
        Company.create({"name": "EFN Farm Uji A", "efn_brand_code": "FARM"})
        with mute_logger("odoo.sql_db"), self.assertRaises(Exception):
            # IntegrityError dari constraint UNIQUE (dibungkus savepoint oleh assertRaises).
            Company.create({"name": "EFN Farm Uji B", "efn_brand_code": "FARM"})

    def test_business_type_derived_from_brand_code(self):
        Company = self.env["res.company"]
        Company.search([("efn_brand_code", "in", ("RTL", "FNB"))]).write({"efn_brand_code": False})
        company = Company.create({"name": "EFN Retail Uji", "efn_brand_code": "RTL"})
        self.assertEqual(company.efn_business_type, "retail")
        company.efn_brand_code = "FNB"
        self.assertEqual(company.efn_business_type, "fnb")
        company.efn_brand_code = False
        self.assertFalse(company.efn_business_type)

    def test_single_merchant_of_record(self):
        Company = self.env["res.company"]
        Company.with_context(active_test=False).search(
            [("efn_is_merchant_of_record", "=", True)]
        ).write({"efn_is_merchant_of_record": False})
        first = Company.create({"name": "EFN Holding Uji", "efn_is_merchant_of_record": True})
        self.assertTrue(first.efn_is_merchant_of_record)
        with self.assertRaises(ValidationError):
            Company.create({"name": "EFN Holding Uji 2", "efn_is_merchant_of_record": True})
        other = Company.create({"name": "EFN Lain Uji"})
        with self.assertRaises(ValidationError):
            other.efn_is_merchant_of_record = True
        # Memindahkan peran: matikan yang lama dulu, lalu aktifkan yang baru.
        first.efn_is_merchant_of_record = False
        other.efn_is_merchant_of_record = True
        self.assertTrue(other.efn_is_merchant_of_record)
