from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.efn_core.models.res_company import EFN_BRAND_BUSINESS_TYPE


@tagged("post_install", "-at_install", "efn")
class TestEfnResCompany(TransactionCase):
    def test_brand_code_unique(self):
        Company = self.env["res.company"]
        # Bersihkan kode yang mungkin sudah dipakai data demo di DB uji.
        Company.search([("efn_brand_code", "=", "FARM")]).write({"efn_brand_code": False})
        # UPDATE harus sampai DB sebelum INSERT berikut (constraint UNIQUE dicek PostgreSQL).
        Company.flush_model(["efn_brand_code"])
        Company.create({"name": "EFN Farm Uji A", "efn_brand_code": "FARM"})
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            # IntegrityError dari constraint UNIQUE (dibungkus savepoint oleh assertRaises).
            Company.create({"name": "EFN Farm Uji B", "efn_brand_code": "FARM"})

    def test_business_type_derived_from_brand_code(self):
        # G20 (docs/04 §12.1) + G24 (§16.1) + G43 + G44: GL,BCR,SMD -> retail; PB,BCF,WTZ,IMD,CUP -> fnb;
        # EFN -> holding; FARM -> farm.
        Company = self.env["res.company"]
        expected = {
            "EFN": "holding",
            "GL": "retail",
            "PB": "fnb",
            "BCR": "retail",
            "BCF": "fnb",
            "WTZ": "fnb",
            "IMD": "fnb",
            "CUP": "fnb",
            "SMD": "retail",
            "FARM": "farm",
        }
        Company.search([("efn_brand_code", "in", list(expected))]).write({"efn_brand_code": False})
        Company.flush_model(["efn_brand_code"])
        company = Company.create({"name": "EFN Uji Kode", "efn_brand_code": "GL"})
        for code, business_type in expected.items():
            with self.subTest(code=code):
                company.efn_brand_code = code
                self.assertEqual(company.efn_business_type, business_type)
        company.efn_brand_code = False
        self.assertFalse(company.efn_business_type)

    def test_selection_matches_contract_codes(self):
        """W-32/E-43 + G24 + G43 + G44: Selection = 10 kode (§12.1/§16.1 + IMD/CUP + SMD).

        Kode lama RTL/FNB tidak lagi diterima.
        """
        field = self.env["res.company"]._fields["efn_brand_code"]
        codes = [value for value, _label in field.selection]
        self.assertEqual(codes, ["EFN", "GL", "PB", "BCR", "BCF", "WTZ", "IMD", "CUP", "SMD", "FARM"])
        self.assertEqual(len(codes), len(set(codes)))
        # Setiap kode punya jenis usaha (compute tidak boleh jatuh ke False untuk kode yang sah).
        self.assertEqual(set(codes), set(EFN_BRAND_BUSINESS_TYPE))

    def test_g43_codes_unique(self):
        """G43: IMD/CUP tunduk pada UNIQUE(efn_brand_code) seperti kode lain."""
        Company = self.env["res.company"]
        for code in ("IMD", "CUP"):
            with self.subTest(code=code):
                Company.with_context(active_test=False).search([("efn_brand_code", "=", code)]).write(
                    {"efn_brand_code": False}
                )
                Company.flush_model(["efn_brand_code"])
                first = Company.create({"name": f"EFN Uji {code} A", "efn_brand_code": code})
                self.assertEqual(first.efn_business_type, "fnb")
                with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
                    Company.create({"name": f"EFN Uji {code} B", "efn_brand_code": code})
        for old in ("RTL", "FNB"):
            with self.subTest(old=old), self.assertRaises(ValueError):
                self.env["res.company"].create({"name": f"EFN Kode Lama {old}", "efn_brand_code": old})

    def test_single_merchant_of_record(self):
        Company = self.env["res.company"]
        Company.with_context(active_test=False).search([("efn_is_merchant_of_record", "=", True)]).write(
            {"efn_is_merchant_of_record": False}
        )
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
