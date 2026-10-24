"""W-03 / Q-ON6: presisi IDR global Rp1 (docs/08 §9.1). Nilai uang di semua PT bulat Rupiah."""

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestCurrencyIdr(TransactionCase):
    def test_idr_rounding_one_rupiah(self):
        idr = self.env.ref("base.IDR")
        self.assertTrue(idr.active)
        self.assertEqual(idr.rounding, 1.0)
        self.assertEqual(idr.decimal_places, 0)
        self.assertEqual(idr.round(1234.5), 1235.0)

    def test_all_efn_companies_use_idr(self):
        idr = self.env.ref("base.IDR")
        for xmlid in (
            "company_efn",
            "company_rtl",
            "company_fnb",
            "company_bcr",
            "company_bcf",
            "company_wtz",
            "company_farm",
        ):
            with self.subTest(company=xmlid):
                self.assertEqual(self.env.ref(f"efn_setup_company.{xmlid}").currency_id, idr)

    def test_invoice_totals_are_whole_rupiah(self):
        """PPN 11% (tax_ST4) per baris -> pajak & total faktur bulat (PoC W-03: 88.638 + 9.750)."""
        company = self.env.ref("efn_setup_company.company_rtl")
        env = self.env(context=dict(self.env.context, allowed_company_ids=company.ids))
        # Eksplisit exclude: DB demo memakai harga termasuk pajak di PT Retail (account_price_include).
        tax = env.ref(f"account.{company.id}_tax_ST4").copy({"price_include_override": "tax_excluded"})
        partner = env["res.partner"].create({"name": "Pelanggan uji W-03"})
        move = env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": company.id,
                "partner_id": partner.id,
                "invoice_line_ids": [
                    Command.create({"name": n, "price_unit": p, "quantity": q, "tax_ids": [Command.set(tax.ids)]})
                    for n, p, q in (("A", 12345, 3), ("B", 7777, 1), ("C", 1499, 7), ("D", 33333, 1))
                ],
            }
        )
        self.assertEqual(move.currency_id.decimal_places, 0)
        self.assertEqual(move.amount_untaxed, 88638)
        self.assertEqual(move.amount_tax, 9750)
        self.assertEqual(move.amount_total, 98388)
        for line in move.line_ids:
            self.assertEqual(line.balance, round(line.balance), line.name)
