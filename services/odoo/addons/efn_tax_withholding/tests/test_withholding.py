"""G59-A efn_tax_withholding (docs/fit-gap/G59_G60_ACCOUNTING_PORT.md §G59-A, test 1-6).

Test sumber `custom_tax_id` menguji engine yang di-drop (D-1/D-2) sehingga tidak diport; semua test di sini baru.
Tarif & kode objek = data uji (ADR-027), bukan konfigurasi produksi.
"""

import ast
from datetime import date

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

NPWP = "1234567890123457"


@tagged("post_install", "-at_install", "efn")
class TestEfnWithholding(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.pph23_account = cls._efn_account("EFNPPH23", cls.company)
        cls.pph26_account = cls._efn_account("EFNPPH26", cls.company)
        cls.pph23 = cls._efn_tax("PPh 23 Uji 2%", -2.0, "pph_23", "24-104-01", cls.pph23_account, cls.company_data)
        cls.pph26 = cls._efn_tax("PPh 26 Uji 20%", -20.0, "pph_26", "27-100-99", cls.pph26_account, cls.company_data)
        cls.fp_foreign = cls.env["account.fiscal.position"].create(
            {"name": "Vendor Luar Negeri Uji", "company_id": cls.company.id}
        )
        cls.pph26.write(
            {"fiscal_position_ids": [Command.set(cls.fp_foreign.ids)], "original_tax_ids": [Command.set(cls.pph23.ids)]}
        )
        cls.service = cls.env["product.product"].create(
            {
                "name": "Jasa Konsultan Uji",
                "type": "service",
                "property_account_expense_id": cls.company_data["default_account_expense"].id,
                "supplier_taxes_id": [Command.set(cls.pph23.ids)],
            }
        )
        cls.data_b = cls.setup_other_company(name="PT PPh B")
        cls.pph23_b = cls._efn_tax(
            "PPh 23 Uji B 2%", -2.0, "pph_23", "24-104-01", cls._efn_account("EFNPPH23B", cls.data_b["company"]),
            cls.data_b,
        )
        cls.accountant = new_test_user(
            cls.env,
            login="efn_pph_accountant",
            groups="base.group_user,account.group_account_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        cls.billing = new_test_user(
            cls.env,
            login="efn_pph_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )

    @classmethod
    def _efn_account(cls, code, company):
        return cls.env["account.account"].create(
            {
                "name": f"Hutang PPh Uji {code}",
                "code": code,
                "account_type": "liability_current",
                "company_ids": [Command.set(company.ids)],
            }
        )

    @classmethod
    def _efn_tax(cls, name, amount, kind, code, account, company_data):
        def repartition():
            return [
                Command.create({"repartition_type": "base"}),
                Command.create({"repartition_type": "tax", "account_id": account.id}),
            ]

        return cls.env["account.tax"].create(
            {
                "name": name,
                "amount_type": "percent",
                "amount": amount,
                "type_tax_use": "purchase",
                "company_id": company_data["company"].id,
                "tax_group_id": company_data["default_tax_purchase"].tax_group_id.id,
                "efn_pph_kind": kind,
                "efn_bupot_object_code": code,
                "invoice_repartition_line_ids": repartition(),
                "refund_repartition_line_ids": repartition(),
            }
        )

    def _bill(self, taxes, price=1000000.0, partner=None, company_data=None, post=True, fiscal_position=None):
        company_data = company_data or self.company_data
        vals = {
            "move_type": "in_invoice",
            "partner_id": (partner or self.partner_a).id,
            "invoice_date": date(2025, 5, 10),
            "date": date(2025, 5, 10),
            "invoice_line_ids": [
                Command.create(
                    {
                        "name": "Jasa Konsultan Uji",
                        "account_id": company_data["default_account_expense"].id,
                        "quantity": 1,
                        "price_unit": price,
                        "tax_ids": [Command.set(taxes.ids)],
                    }
                )
            ],
        }
        if fiscal_position:
            vals["fiscal_position_id"] = fiscal_position.id
        bill = self.env["account.move"].with_company(company_data["company"]).create(vals)
        if post:
            bill.action_post()
        return bill

    @staticmethod
    def _pph_line(bill):
        return bill.line_ids.filtered(lambda line: line.tax_line_id.efn_pph_kind)

    def _register_domain(self):
        action = self.env.ref("efn_tax_withholding.action_efn_withholding_register")
        return ast.literal_eval(action.domain)

    # 1
    def test_bill_pph23_net_payable(self):
        bill = self._bill(self.pph23)
        payable = bill.line_ids.filtered(lambda line: line.account_type == "liability_payable")
        self.assertAlmostEqual(sum(payable.mapped("credit")), 980000.0, places=2)
        pph = self._pph_line(bill)
        self.assertEqual(len(pph), 1)
        self.assertEqual(pph.account_id, self.pph23_account)
        self.assertAlmostEqual(pph.credit, 20000.0, places=2)
        self.assertAlmostEqual(abs(pph.tax_base_amount), 1000000.0, places=2)
        self.assertEqual(pph.efn_pph_kind, "pph_23")
        self.assertEqual(pph.efn_pph_object_code, "24-104-01")

    # 2
    def test_fiscal_position_maps_pph23_to_pph26(self):
        self.assertEqual(self.fp_foreign.map_tax(self.pph23), self.pph26)
        bill = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.partner_a.id,
                "invoice_date": date(2025, 5, 10),
                "fiscal_position_id": self.fp_foreign.id,
                "invoice_line_ids": [Command.create({"product_id": self.service.id, "price_unit": 500000.0})],
            }
        )
        self.assertEqual(bill.invoice_line_ids.tax_ids, self.pph26)
        bill.action_post()
        pph = self._pph_line(bill)
        self.assertEqual(pph.account_id, self.pph26_account)
        self.assertAlmostEqual(pph.credit, 100000.0, places=2)

    # 3
    def test_constraints(self):
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.pph23.amount = 2.0
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.pph23.efn_bupot_object_code = "24104-01"
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.pph23.efn_bupot_object_code = False
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.pph23.amount_type = "fixed"
        # bukan PPh: tarif positif tanpa kode objek tetap boleh
        self.pph23.write({"efn_pph_kind": False, "efn_bupot_object_code": False, "amount": 2.0})
        self.assertFalse(self.pph23.efn_pph_kind)

    # 4
    def test_register_posted_only_and_company_scoped(self):
        posted_a = self._bill(self.pph23)
        draft_a = self._bill(self.pph23, post=False)
        posted_b = self._bill(self.pph23_b, company_data=self.data_b)
        no_pph = self._bill(self.tax_purchase_a) if self.tax_purchase_a else self.env["account.move"]
        lines = self.env["account.move.line"].with_user(self.accountant).search(self._register_domain())
        self.assertIn(self._pph_line(posted_a), lines)
        self.assertNotIn(self._pph_line(draft_a), lines)
        self.assertFalse(lines & self._pph_line(posted_b))
        self.assertFalse(lines & no_pph.line_ids)
        self.assertEqual(lines.mapped("company_id"), self.company)

    # 5
    def test_bupot_number_after_posting(self):
        bill = self._bill(self.pph23)
        pph = self._pph_line(bill)
        pph.with_user(self.accountant).write({"efn_bupot_number": "BP-0001", "efn_bupot_date": date(2025, 6, 1)})
        self.assertEqual(pph.efn_bupot_number, "BP-0001")
        self.assertEqual(bill.state, "posted")
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            pph.with_user(self.billing).write({"efn_bupot_number": "BP-0002"})
        self.assertEqual(pph.efn_bupot_number, "BP-0001")
        expense_line = bill.invoice_line_ids
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            expense_line.with_user(self.accountant).write({"efn_bupot_number": "BP-0003"})
        # salinan tagihan tidak membawa bukti potong
        copy = bill.copy({"invoice_date": date(2025, 5, 11)})
        self.assertFalse(copy.line_ids.filtered("efn_bupot_number"))

    # 6
    def test_npwp_decrypted_via_orm(self):
        if type(self.env["res.partner"]._fields["vat"]).__name__ != "EfnPiiChar":
            self.skipTest("efn_pii_base_vat tidak terpasang: NPWP tidak dienkripsi di DB ini")
        from odoo.addons.efn_pii.tests.common import PiiKeyMixin  # noqa: PLC0415 (hanya bila efn_pii terpasang)

        PiiKeyMixin._pii_env(self)
        vendor = self.env["res.partner"].create(
            {"name": "CV Vendor NPWP Uji", "is_company": True, "vat": NPWP, "country_id": self.env.ref("base.id").id}
        )
        raw = PiiKeyMixin._raw_table(self, "res_partner", vendor.id, "vat")["vat"]
        self.assertTrue(raw)
        self.assertNotIn(NPWP, raw)
        pph = self._pph_line(self._bill(self.pph23, partner=vendor))
        pph.invalidate_recordset()
        self.assertEqual(pph.with_user(self.accountant).read(["efn_pph_partner_vat"])[0]["efn_pph_partner_vat"], NPWP)
        with self.assertRaises(AccessError):
            pph.with_user(self.billing).read(["efn_pph_partner_vat"])
