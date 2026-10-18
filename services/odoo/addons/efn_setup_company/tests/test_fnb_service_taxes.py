"""G19 W-14: pajak F&B per layanan (data/account_tax_fnb_service_data.xml), docs/08 §10.4.

Keputusan #15/#37: PB1 + SC 5 % hanya dine-in, SC masuk DPP PB1, harga exclude; non-dine-in tanpa SC.
PB1 non-dine-in = TODO konsultan pajak (R-L09): tarif sengaja sama dengan PB1 default sampai ada opini.
Nominal dipilih bulat agar hasil sama pada IDR Rp1 (DB baru) maupun 0,01 (DB demo).
"""

from odoo.tests import TransactionCase, tagged

PRICE = 100000.0


@tagged("post_install", "-at_install", "efn")
class TestFnbServiceTaxes(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ref = cls.env.ref
        cls.company = ref("efn_setup_company.company_fnb")
        cls.currency = cls.company.currency_id
        cls.pb1 = ref("efn_setup_company.tax_pb1_10_fnb")
        cls.sc = ref("efn_setup_company.tax_service_charge_5_fnb")
        cls.pb1_dine_in = ref("efn_setup_company.tax_pb1_dine_in_fnb")
        cls.pb1_non_dine_in = ref("efn_setup_company.tax_pb1_non_dine_in_fnb")
        cls.fp_dine_in = ref("efn_setup_company.fiscal_position_fnb_dine_in")
        cls.fp_non_dine_in = ref("efn_setup_company.fiscal_position_fnb_non_dine_in")

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

    def test_non_dine_in_without_service_charge(self):
        taxes, res, by_tax = self._compute(self.fp_non_dine_in)
        self.assertEqual(taxes, self.pb1_non_dine_in)
        self.assertNotIn(self.sc.id, by_tax)
        self.assertAmount(res["total_included"], 110000.0)

    def test_non_dine_in_pb1_is_todo_placeholder(self):
        # R-L09: modul tidak memutuskan PB1 bawa pulang -> tarif = PB1 default + tanda TODO di nama.
        self.assertEqual(self.pb1_non_dine_in.amount, self.pb1.amount)
        self.assertIn("TODO", self.pb1_non_dine_in.name)
        self.assertEqual(self.pb1_non_dine_in.tax_group_id, self.pb1.tax_group_id)

    def test_service_charge_shape(self):
        self.assertTrue(self.sc.include_base_amount)
        self.assertLess(self.sc.sequence, self.pb1_dine_in.sequence)
        self.assertTrue(self.pb1_dine_in.is_base_affected)
        self.assertNotEqual(self.sc.tax_group_id, self.pb1.tax_group_id)
