"""G19 W-14: pajak F&B per layanan (data/account_tax_fnb_service_data.xml), docs/08 §10.4.

Keputusan #15/#37: PB1 + SC 5 % hanya dine-in, SC masuk DPP PB1, harga exclude; non-dine-in tanpa SC.
Keputusan pemilik proyek 18 Okt 2026 (18-1): PB1 hanya dine-in -> PB1 Non-Dine-in 0 % (bisa diubah tanpa kode;
risiko R-L09 diterima pemilik proyek). docs/08 §11.1.
Nominal dipilih bulat agar hasil sama pada IDR Rp1 (DB baru) maupun 0,01 (DB demo).
"""

from odoo.tests import TransactionCase, tagged

PRICE = 100000.0


@tagged("post_install", "-at_install", "efn")
class TestFnbServiceTaxes(TransactionCase):
    """PT Paris Baguette (PB, xmlid lama sufiks _fnb). Subkelas di bawah menjalankan uji yang sama untuk BCF."""

    COMPANY = "company_fnb"
    SUFFIX = "fnb"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        s = cls.SUFFIX
        cls.company = ref(f"efn_setup_company.{cls.COMPANY}")
        cls.currency = cls.company.currency_id
        cls.pb1 = ref(f"efn_setup_company.tax_pb1_10_{s}")
        cls.sc = ref(f"efn_setup_company.tax_service_charge_5_{s}")
        cls.pb1_dine_in = ref(f"efn_setup_company.tax_pb1_dine_in_{s}")
        cls.pb1_non_dine_in = ref(f"efn_setup_company.tax_pb1_non_dine_in_{s}")
        cls.fp_dine_in = ref(f"efn_setup_company.fiscal_position_{s}_dine_in")
        cls.fp_non_dine_in = ref(f"efn_setup_company.fiscal_position_{s}_non_dine_in")

    def _compute(self, fiscal_position):
        taxes = fiscal_position.map_tax(self.pb1) if fiscal_position else self.pb1
        res = taxes.with_company(self.company).compute_all(PRICE, currency=self.currency, quantity=1.0)
        by_tax = {t["id"]: t for t in res["taxes"]}
        return taxes, res, by_tax

    def assertAmount(self, value, expected):
        self.assertEqual(self.currency.compare_amounts(value, expected), 0, f"{value} != {expected}")

    def test_menu_price_excluded_on_fnb(self):
        self.assertEqual(self.company.account_price_include, "tax_excluded")
        for tax in self.pb1 | self.sc | self.pb1_dine_in | self.pb1_non_dine_in:
            with self.subTest(tax=tax.name):
                self.assertFalse(tax.price_include_override)
                self.assertEqual(tax.company_id, self.company)
                self.assertEqual(tax.type_tax_use, "sale")

    def test_fiscal_positions_are_manual(self):
        for fp in self.fp_dine_in | self.fp_non_dine_in:
            with self.subTest(fp=fp.name):
                self.assertFalse(fp.auto_apply)
                self.assertEqual(fp.company_id, self.company)

    def test_source_tax_stays_domestic(self):
        # original_tax_ids hanya menerima pajak domestik; PB1 default (pajak produk) tidak boleh masuk fiscal position.
        self.assertTrue(self.pb1.is_domestic)
        self.assertFalse(self.pb1.fiscal_position_ids)
        self.assertEqual(self.company.account_sale_tax_id, self.pb1)

    def test_without_fiscal_position_no_service_charge(self):
        taxes, res, _by_tax = self._compute(self.env["account.fiscal.position"])
        self.assertEqual(taxes, self.pb1)
        self.assertAmount(res["total_included"], 110000.0)

    def test_dine_in_service_charge_in_pb1_base(self):
        taxes, res, by_tax = self._compute(self.fp_dine_in)
        self.assertEqual(set(taxes.ids), {self.sc.id, self.pb1_dine_in.id})
        self.assertAmount(by_tax[self.sc.id]["amount"], 5000.0)
        self.assertAmount(by_tax[self.pb1_dine_in.id]["base"], 105000.0)
        self.assertAmount(by_tax[self.pb1_dine_in.id]["amount"], 10500.0)
        self.assertAmount(res["total_excluded"], PRICE)
        self.assertAmount(res["total_included"], 115500.0)

    def test_non_dine_in_without_service_charge_nor_pb1(self):
        # 18-1: bawa pulang / 3PL / pickup / online = tanpa SC dan tanpa PB1 -> total = harga menu.
        taxes, res, by_tax = self._compute(self.fp_non_dine_in)
        self.assertEqual(taxes, self.pb1_non_dine_in)
        self.assertNotIn(self.sc.id, by_tax)
        self.assertAmount(sum(t["amount"] for t in res["taxes"]), 0.0)
        self.assertAmount(res["total_included"], PRICE)

    def test_non_dine_in_pb1_zero_and_configurable(self):
        # 18-1: default 0 %, label TODO R-L09 dihapus; pajak & FP tetap ada (ubah Amount = tanpa kode, grup PB1 sama).
        self.assertEqual(self.pb1_non_dine_in.amount, 0.0)
        self.assertEqual(self.pb1_non_dine_in.amount_type, "percent")
        self.assertTrue(self.pb1_non_dine_in.active)
        self.assertNotIn("TODO", self.pb1_non_dine_in.name)
        self.assertNotIn("TODO", self.fp_non_dine_in.name)
        self.assertEqual(self.pb1_non_dine_in.tax_group_id, self.pb1.tax_group_id)
        self.assertIn(self.pb1_non_dine_in, self.fp_non_dine_in.tax_ids)
        self.assertIn(self.pb1, self.pb1_non_dine_in.original_tax_ids)
        # Dine-in tidak terpengaruh: PB1 dine-in tetap = PB1 default.
        self.assertEqual(self.pb1_dine_in.amount, self.pb1.amount)

    def test_non_dine_in_rate_change_needs_no_code(self):
        # Bila perda kota memungut PBJT bawa pulang: cukup ubah Amount (savepoint test, tidak permanen).
        self.pb1_non_dine_in.amount = 10.0
        _taxes, res, _by_tax = self._compute(self.fp_non_dine_in)
        self.assertAmount(res["total_included"], 110000.0)

    def test_service_charge_shape(self):
        self.assertTrue(self.sc.include_base_amount)
        self.assertLess(self.sc.sequence, self.pb1_dine_in.sequence)
        self.assertTrue(self.pb1_dine_in.is_base_affected)
        self.assertNotEqual(self.sc.tax_group_id, self.pb1.tax_group_id)


@tagged("post_install", "-at_install", "efn")
class TestBcfServiceTaxes(TestFnbServiceTaxes):
    """G20 W-32 (docs/04 §12.1): Bacha Coffee F&B = PB1 + SC dengan record sendiri (sufiks _bcf)."""

    COMPANY = "company_bcf"
    SUFFIX = "bcf"

    def test_records_do_not_leak_across_fnb_companies(self):
        pb = self.env.ref("efn_setup_company.company_fnb")
        for record in (self.pb1, self.sc, self.pb1_dine_in, self.pb1_non_dine_in, self.fp_dine_in, self.fp_non_dine_in):
            with self.subTest(record=record.display_name):
                self.assertEqual(record.company_id, self.company)
                self.assertNotEqual(record.company_id, pb)
        self.assertNotEqual(self.pb1.tax_group_id, self.env.ref("efn_setup_company.tax_group_pb1_fnb"))


@tagged("post_install", "-at_install", "efn")
class TestWtzServiceTaxes(TestFnbServiceTaxes):
    """G24 W-40 (docs/04 §16.1, L-91): PT Wetzel's Pretzels = PB1 + SC dengan record sendiri (sufiks _wtz)."""

    COMPANY = "company_wtz"
    SUFFIX = "wtz"

    def test_records_do_not_leak_across_fnb_companies(self):
        others = self.env.ref("efn_setup_company.company_fnb") | self.env.ref("efn_setup_company.company_bcf")
        for record in (self.pb1, self.sc, self.pb1_dine_in, self.pb1_non_dine_in, self.fp_dine_in, self.fp_non_dine_in):
            with self.subTest(record=record.display_name):
                self.assertEqual(record.company_id, self.company)
                self.assertNotIn(record.company_id, others)
        for suffix in ("fnb", "bcf"):
            self.assertNotEqual(self.pb1.tax_group_id, self.env.ref(f"efn_setup_company.tax_group_pb1_{suffix}"))

    def test_wtz_is_root_fnb_company_with_chart(self):
        self.assertFalse(self.company.parent_id)
        self.assertEqual((self.company.efn_brand_code, self.company.efn_business_type), ("WTZ", "fnb"))
        self.assertEqual(self.company.chart_template, "id")
        self.assertIn(self.company, self.env.ref("base.user_admin").company_ids)
