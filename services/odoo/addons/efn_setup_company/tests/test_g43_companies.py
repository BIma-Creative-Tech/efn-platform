"""G43 (keputusan user 18 Nov 2026): company PT Im Donut (IMD) & PT Curry Up (CUP), pola WTZ (G24).

Data PT placeholder (Q-ESB5): nama "(placeholder)", tanpa NPWP/alamat. Migrasi 19.0.1.9.0 untuk DB lama diuji
idempoten dan hanya menyentuh IMD/CUP.
"""

import importlib.util
import os

from odoo.tests import TransactionCase, tagged

G43 = {
    "company_imd": ("PT Im Donut (placeholder)", "IMD", "imd"),
    "company_cup": ("PT Curry Up (placeholder)", "CUP", "cup"),
}
MIGRATION = os.path.join(os.path.dirname(__file__), "..", "migrations", "19.0.1.9.0", "post-migrate.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("efn_setup_company_g43_post_migrate", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@tagged("post_install", "-at_install", "efn")
class TestG43Companies(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.companies = {xmlid: cls.env.ref(f"efn_setup_company.{xmlid}") for xmlid in G43}
        cls.admin = cls.env.ref("base.user_admin")

    def test_companies_exist_as_placeholder_root_fnb(self):
        idr = self.env.ref("base.IDR")
        for xmlid, (name, code, _sfx) in G43.items():
            company = self.companies[xmlid]
            with self.subTest(company=xmlid):
                self.assertEqual(company.name, name)
                self.assertEqual((company.efn_brand_code, company.efn_business_type), (code, "fnb"))
                self.assertFalse(company.parent_id)
                self.assertEqual(company.currency_id, idr)
                self.assertEqual(company.country_id, self.env.ref("base.id"))
                self.assertEqual(company.chart_template, "id")
                self.assertFalse(company.vat_check_vies)
                # Q-ESB5: tanpa NPWP / alamat nyata di repo publik.
                self.assertFalse(company.vat)
                self.assertFalse(company.street)
                self.assertFalse(company.efn_is_merchant_of_record)
                self.assertEqual(company.tax_calculation_rounding_method, "round_globally")
                self.assertEqual(company.account_price_include, "tax_excluded")
                self.assertIn(company, self.admin.company_ids)

    def test_codes_unique_per_database(self):
        Company = self.env["res.company"].sudo().with_context(active_test=False)
        for _name, code, _sfx in G43.values():
            with self.subTest(code=code):
                self.assertEqual(Company.search_count([("efn_brand_code", "=", code)]), 1)
        codes = Company.search([("efn_brand_code", "!=", False)]).mapped("efn_brand_code")
        self.assertEqual(len(codes), len(set(codes)))

    def test_pb1_default_per_company(self):
        seen_taxes = self.env["account.tax"]
        seen_groups = self.env["account.tax.group"]
        for xmlid, (_name, _code, sfx) in G43.items():
            company = self.companies[xmlid]
            pb1 = self.env.ref(f"efn_setup_company.tax_pb1_10_{sfx}")
            group = self.env.ref(f"efn_setup_company.tax_group_pb1_{sfx}")
            with self.subTest(company=xmlid):
                self.assertEqual(company.account_sale_tax_id, pb1)
                self.assertEqual(pb1.company_id, company)
                self.assertEqual(group.company_id, company)
                self.assertEqual(pb1.tax_group_id, group)
                self.assertEqual((pb1.amount_type, pb1.amount, pb1.type_tax_use), ("percent", 10.0, "sale"))
                self.assertEqual(pb1.invoice_label, "PB1")
                self.assertIn("PLACEHOLDER", pb1.name)
            seen_taxes |= pb1
            seen_groups |= group
        self.assertEqual(len(seen_taxes), 2)
        self.assertEqual(len(seen_groups), 2)

    def test_migration_noop_without_version(self):
        imd = self.companies["company_imd"]
        imd.tax_calculation_rounding_method = "round_per_line"
        self.env.flush_all()
        _load_migration().migrate(self.env.cr, None)
        self.env.invalidate_all()
        self.assertEqual(imd.tax_calculation_rounding_method, "round_per_line")

    def test_migration_idempotent_and_scoped(self):
        migration = _load_migration()
        fnb = self.env.ref("efn_setup_company.company_fnb")
        imd, cup = self.companies["company_imd"], self.companies["company_cup"]
        # Kondisi DB lama setelah upgrade: blok noupdate tidak jalan untuk company baru.
        for company in imd | cup:
            company.tax_calculation_rounding_method = "round_per_line"
            self.env.ref(f"account.{company.id}_tax_ST4").invoice_label = "12%"
            self.env.ref(f"account.{company.id}_l10n_id_tax_group_non_luxury_goods").name = "Non-luxury Good Taxes"
        self.admin.company_ids = [(3, imd.id), (3, cup.id)]
        # Company lain TIDAK boleh disentuh migrasi.
        fnb.tax_calculation_rounding_method = "round_per_line"
        self.env.ref(f"account.{fnb.id}_tax_ST4").invoice_label = "label admin PB"
        self.env.flush_all()

        for run in (1, 2):
            with self.subTest(run=run):
                migration.migrate(self.env.cr, "19.0.1.8.0")
                self.env.invalidate_all()
                for company in imd | cup:
                    self.assertEqual(company.tax_calculation_rounding_method, "round_globally")
                    self.assertEqual(self.env.ref(f"account.{company.id}_tax_ST4").invoice_label, "PPN 11%")
                    self.assertEqual(self.env.ref(f"account.{company.id}_tax_ST2").invoice_label, "Bebas PPN")
                    self.assertEqual(
                        self.env.ref(f"account.{company.id}_l10n_id_tax_group_non_luxury_goods").name, "PPN 11%"
                    )
                    self.assertIn(company, self.admin.company_ids)
                    # PB1 default dan data PT placeholder tidak diubah migrasi.
                    self.assertEqual(company.account_sale_tax_id.invoice_label, "PB1")
                    self.assertFalse(company.vat)
                self.assertEqual(fnb.tax_calculation_rounding_method, "round_per_line")
                self.assertEqual(self.env.ref(f"account.{fnb.id}_tax_ST4").invoice_label, "label admin PB")
        # Satu tautan admin per company (tidak terduplikasi oleh run kedua).
        self.assertEqual(len(self.admin.company_ids.filtered(lambda c: c in (imd | cup))), 2)

    def test_migration_helper_returns_only_g43_companies(self):
        processed = _load_migration().apply_g43_company_defaults(self.env)
        self.assertEqual(processed, self.companies["company_imd"] | self.companies["company_cup"])
