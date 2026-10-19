"""efn_region (ADR-036 §0.7, Q-PII10): data contoh, format kode, unik, importer CSV, field partner plaintext,
record rule multi-company, kolom yang dibaca ETL."""

from psycopg2 import IntegrityError

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import SQL, mute_logger


@tagged("post_install", "-at_install", "efn")
class TestEfnRegion(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Region = self.env["efn.region"]

    def test_sample_data_and_format(self):
        jaksel = self.env.ref("efn_region.efn_region_3171")
        self.assertEqual((jaksel.code, jaksel.kind, jaksel.province_code), ("31.71", "kota", "31"))
        self.assertEqual(jaksel.display_name, "31.71 Kota Administrasi Jakarta Selatan")
        self.assertIn(jaksel, self.Region.search([("code", "=", "31.71")]))
        ids = [row[0] for row in self.Region.name_search("31.71")]
        self.assertIn(jaksel.id, ids)
        for bad in ("3171", "31.7", "31-71", "ab.cd", "31.71.01"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.Region.create({"code": bad, "name": "Salah"})
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.Region.create({"code": "31.71", "name": "Duplikat"})
                self.Region.flush_model()

    def test_etl_columns_exist(self):
        """Kontrak view ETL (skrip grant data-ai): efn_region(id, code, name, state_id, active, create_date,
        write_date) dan res_partner.efn_region_id."""
        self.env.cr.execute(
            SQL(
                """SELECT table_name, column_name FROM information_schema.columns
                    WHERE (table_name = 'efn_region'
                           AND column_name IN ('id', 'code', 'name', 'state_id', 'active', 'create_date', 'write_date'))
                       OR (table_name = 'res_partner' AND column_name = 'efn_region_id')"""
            )
        )
        found = set(self.env.cr.fetchall())
        self.assertEqual(len(found), 8, found)

    def test_import_csv_upsert(self):
        content = (
            "code,name,kind\n"
            "12.71,Kota Medan,kota\n"
            "31.71,Kota Adm. Jakarta Selatan,kota\n"
            "99,Salah,kota\n"
            "12.72,,kota\n"
            "12.73,Kota X,provinsi\n"
        )
        counts = self.Region._efn_region_import_csv(content.encode("utf-8"), source="uji")
        self.assertEqual(counts, {"created": 1, "updated": 1, "skipped": 3})
        medan = self.Region.search([("code", "=", "12.71")])
        self.assertEqual((medan.name, medan.source, medan.province_code), ("Kota Medan", "uji", "12"))
        self.assertEqual(self.env.ref("efn_region.efn_region_3171").name, "Kota Adm. Jakarta Selatan")
        with self.assertRaises(ValidationError):
            self.Region._efn_region_import_csv("kode,nama\n31.71,X\n")
        user = new_test_user(self.env, login="efn_region_user", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.Region.with_user(user)._efn_region_import_csv("code,name\n12.74,Kota Y\n")

    def test_partner_region_plaintext_and_rules(self):
        region = self.env.ref("efn_region.efn_region_3273")
        partner = self.env["res.partner"].create({"name": "PT Wilayah Uji", "is_company": True})
        partner.write({"efn_region_id": region.id})
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT efn_region_id FROM res_partner WHERE id = %s", partner.id))
        self.assertEqual(self.env.cr.fetchone()[0], region.id)
        self.assertIn(partner, self.env["res.partner"].search([("efn_region_id.code", "=", "32.73")]))
        # user internal biasa: baca ya, ubah tidak
        user = new_test_user(self.env, login="efn_region_reader", groups="base.group_user")
        self.assertTrue(self.Region.with_user(user).search_count([]))
        with self.assertRaises(AccessError):
            region.with_user(user).write({"name": "Ubah"})
        # wilayah khusus PT lain tidak terlihat
        other = self.env["res.company"].create({"name": "PT Wilayah Lain"})
        private = self.Region.create({"code": "98.01", "name": "Khusus PT Lain", "company_id": other.id})
        self.assertNotIn(private, self.Region.with_user(user).search([]))
        self.assertIn(region, self.Region.with_user(user).search([]))
