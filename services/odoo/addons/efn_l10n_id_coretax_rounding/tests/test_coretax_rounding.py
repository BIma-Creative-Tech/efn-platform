"""E-40 (docs/08 §11.2 butir 6): selisih PPN Coretax vs jurnal Odoo per faktur.

Angka uji = data uji (bukan konfigurasi produksi, ADR-027). Contoh PoC G18 (docs/08 §9.1): faktur PPN include
12.990×3, 7.500, 1.499×7, 25.000 -> jurnal Odoo 8.122, Coretax Σ VAT 8.123 -> penyesuaian Rp1.
Prasyarat: IDR dibulatkan Rp1 (efn_setup_company G18). Bila DB uji tidak bisa menaikkan rounding IDR (sudah ada jurnal
IDR 0,01), kelas di-skip dengan alasan (dilaporkan, bukan lulus diam-diam).
"""

import datetime
from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

LOGGER = "odoo.addons.efn_l10n_id_coretax_rounding.models.account_move"
INCLUDE_LINES = [(12990.0, 3.0), (7500.0, 1.0), (1499.0, 7.0), (25000.0, 1.0)]
INVOICE_DATE = "2026-10-01"


@tagged("post_install", "-at_install", "efn")
class TestEfnCoretaxRounding(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("id")
    def setUpClass(cls):
        super().setUpClass()
        cls.skip_reason = None
        cls.idr = cls.env.ref("base.IDR")
        cls.company = cls.company_data["company"]
        if cls.company.currency_id != cls.idr:
            cls.skip_reason = "company uji bukan IDR"
            return
        if cls.idr.rounding != 1.0:
            try:
                with cls.env.cr.savepoint():
                    cls.idr.write({"rounding": 1.0})
            except UserError:
                cls.skip_reason = "rounding IDR tidak dapat dinaikkan ke Rp1 di DB uji ini"
                return
        cls.country_id = cls.env.ref("base.id")
        cls.company.write({"vat": "1234567890123456", "street": "Jl. Penjual Uji 1"})
        cls.buyer = cls.env["res.partner"].create(
            {
                "name": "PT Pembeli Coretax Uji",
                "is_company": True,
                "vat": "1234567890123457",
                "country_id": cls.country_id.id,
                "street": "Jl. Pembeli Uji 2",
                "l10n_id_kode_transaksi": "04",
            }
        )
        cls.taxes = cls._make_taxes(cls.company)
        if not cls.taxes:
            cls.skip_reason = "pajak tax_ST4 l10n_id tidak ditemukan"
            return
        cls.rounding_account, cls.gain_account, cls.journal = cls._configure(cls.company_data, "99990001")

    @classmethod
    def _make_taxes(cls, company):
        chart = cls.env["account.chart.template"].with_company(company)
        base = chart.ref("tax_ST4", raise_if_not_found=False)
        if not base:
            return None
        group = base.tax_group_id
        non_luxury = chart.ref("l10n_id_tax_group_non_luxury_goods", raise_if_not_found=False)
        regular = (chart.ref("default_tax_group", raise_if_not_found=False) or cls.env["account.tax.group"]) | (
            non_luxury or cls.env["account.tax.group"]
        )
        if group not in regular and non_luxury:
            group = non_luxury

        def make(name, amount, include):
            return base.copy(
                {
                    "name": name,
                    "amount": amount,
                    "price_include_override": "tax_included" if include else "tax_excluded",
                    "tax_group_id": group.id,
                }
            )

        return {
            "incl": make(f"PPN 11% Uji Incl {company.id}", 11.0, True),
            "excl": make(f"PPN 11% Uji Excl {company.id}", 11.0, False),
            "excl12": make(f"PPN 12% Uji Excl {company.id}", 12.0, False),
        }

    @classmethod
    def _configure(cls, company_data, code):
        company = company_data["company"]
        Account = cls.env["account.account"].with_company(company)
        expense = Account.create(
            {
                "name": f"Selisih Pembulatan Pajak Uji {code}",
                "code": code,
                "account_type": "expense",
                "company_ids": [Command.set(company.ids)],
            }
        )
        gain = Account.create(
            {
                "name": f"Selisih Pembulatan Pajak Lebih Uji {code}",
                "code": str(int(code) + 1),
                "account_type": "income_other",
                "company_ids": [Command.set(company.ids)],
            }
        )
        journal = company_data["default_journal_misc"]
        company.write(
            {
                "efn_tax_rounding_account_id": expense.id,
                "efn_tax_rounding_gain_account_id": gain.id,
                "efn_tax_rounding_journal_id": journal.id,
            }
        )
        return expense, gain, journal

    def setUp(self):
        super().setUp()
        if self.skip_reason:
            self.skipTest(self.skip_reason)

    # ------------------------------------------------------------------ helper
    def _invoice(self, lines, tax, company=None, trx="04", post=True, check=True):
        company = company or self.company
        move = (
            self.env["account.move"]
            .with_company(company)
            .create(
                {
                    "move_type": "out_invoice",
                    "company_id": company.id,
                    "partner_id": self.buyer.id,
                    "invoice_date": INVOICE_DATE,
                    "l10n_id_kode_transaksi": trx,
                    "invoice_line_ids": [
                        Command.create(
                            {
                                "product_id": self.product_a.id,
                                "quantity": qty,
                                "price_unit": price,
                                "tax_ids": [Command.set(tax.ids)],
                            }
                        )
                        for price, qty in lines
                    ],
                }
            )
        )
        if check:
            self.assertTrue(move.l10n_id_coretax_efaktur_available)
        if post:
            move.action_post()
        return move

    def _coretax_lines(self, move):
        vals = {}
        for line in move.invoice_line_ids.filtered("tax_ids"):
            line._l10n_id_coretax_build_invoice_line_vals(vals)
        return vals["lines"]

    def _coretax_vat(self, move):
        return sum(float(line["VAT"]) for line in self._coretax_lines(move))

    def _tax_lines(self, move):
        return move.line_ids.filtered(lambda aml: aml.display_type == "tax")

    def _odoo_vat(self, move):
        return -sum(self._tax_lines(move).mapped("balance"))

    def _adjustments(self, move):
        return self.env["account.move"].search([("efn_coretax_source_move_id", "=", move.id)])

    # ------------------------------------------------------------------ test
    def test_include_invoice_rp1_adjustment(self):
        inv = self._invoice(INCLUDE_LINES, self.taxes["incl"])
        self.assertEqual(inv.amount_total, 81963)
        self.assertEqual(self._odoo_vat(inv), 8122)
        self.assertEqual(self._coretax_vat(inv), 8123)
        adj = inv.efn_coretax_adjustment_move_id
        self.assertTrue(adj)
        self.assertEqual(adj.state, "posted")
        self.assertEqual(adj.move_type, "entry")
        self.assertEqual(adj.journal_id, self.journal)
        self.assertEqual(adj.date, inv.date)
        self.assertEqual(adj.company_id, self.company)
        self.assertEqual(adj.efn_coretax_source_move_id, inv)
        self.assertEqual(inv.efn_coretax_rounding_diff, 1)
        tax_line = self._tax_lines(inv)
        self.assertEqual(len(tax_line), 1)
        debit = adj.line_ids.filtered(lambda aml: aml.debit)
        credit = adj.line_ids.filtered(lambda aml: aml.credit)
        self.assertEqual((debit.account_id, debit.debit), (self.rounding_account, 1))
        self.assertEqual((credit.account_id, credit.credit), (tax_line.account_id, 1))
        self.assertEqual(credit.tax_tag_ids, tax_line.tax_tag_ids)
        # Piutang & total faktur tidak berubah; jurnal penyesuaian tanpa piutang.
        receivable = inv.line_ids.filtered(lambda aml: aml.account_id.account_type == "asset_receivable")
        self.assertEqual(receivable.balance, 81963)
        self.assertEqual(inv.amount_residual, 81963)
        self.assertFalse(adj.line_ids.filtered(lambda aml: aml.account_id.account_type == "asset_receivable"))
        # "Laporan pajak" = Σ VAT XML: saldo akun PPN keluaran (dan tag repartisi) faktur + penyesuaian.
        lines = inv.line_ids | adj.line_ids
        ppn_lines = lines.filtered(lambda aml: aml.account_id == tax_line.account_id)
        self.assertEqual(-sum(ppn_lines.mapped("balance")), 8123)
        if tax_line.tax_tag_ids:
            tagged_lines = lines.filtered(
                lambda aml: aml.display_type != "product" and aml.tax_tag_ids & tax_line.tax_tag_ids
            )
            self.assertEqual(-sum(tagged_lines.mapped("balance")), 8123)

    def test_exclude_invoice_without_difference(self):
        inv = self._invoice([(100000.0, 1.0)], self.taxes["excl"])
        self.assertEqual(self._odoo_vat(inv), 11000)
        self.assertEqual(self._coretax_vat(inv), 11000)
        self.assertFalse(inv.efn_coretax_adjustment_move_id)
        self.assertFalse(self._adjustments(inv))
        self.assertEqual(inv.efn_coretax_rounding_diff, 0)

    def test_trx_01_other_base_equals_base(self):
        inv = self._invoice([(100000.0, 1.0)], self.taxes["excl12"], trx="01")
        (line,) = self._coretax_lines(inv)
        self.assertEqual(line["OtherTaxBase"], line["TaxBase"])
        self.assertEqual(float(line["VAT"]), 12000)
        self.assertEqual(self._odoo_vat(inv), 12000)
        self.assertFalse(inv.efn_coretax_adjustment_move_id)

    def test_guard_rejects_non_rounding_difference(self):
        """Kode 01 + PPN 11 %: Coretax 12 % × DPP -> selisih Rp1.000 > 1 baris × Rp1 = data salah."""
        with self.assertLogs(LOGGER, "WARNING"):
            inv = self._invoice([(100000.0, 1.0)], self.taxes["excl"], trx="01")
        self.assertEqual(inv.state, "posted")  # posting faktur tidak diblokir
        self.assertFalse(inv.efn_coretax_adjustment_move_id)
        with self.assertRaises(UserError):
            inv._efn_coretax_sync_rounding(strict=True)
        self.assertFalse(self._adjustments(inv))

    def test_idempotent_on_export_and_trx_change(self):
        inv = self._invoice(INCLUDE_LINES, self.taxes["incl"])
        adj = inv.efn_coretax_adjustment_move_id
        inv.download_efaktur()
        self.assertTrue(inv.l10n_id_coretax_document)
        self.assertEqual(inv.efn_coretax_adjustment_move_id, adj)
        inv.l10n_id_coretax_document.action_regenerate()
        self.assertEqual(inv.efn_coretax_adjustment_move_id, adj)
        self.assertEqual(self._adjustments(inv), adj)
        # Kode transaksi diubah ke 08 (dibebaskan, di luar cakupan) sebelum ekspor ulang -> penyesuaian di-reverse.
        inv.l10n_id_kode_transaksi = "08"
        inv._efn_coretax_sync_rounding(strict=True)
        self.assertFalse(inv.efn_coretax_adjustment_move_id)
        reversal = self.env["account.move"].search([("reversed_entry_id", "=", adj.id)])
        self.assertEqual(reversal.state, "posted")
        self.assertEqual(sum(reversal.line_ids.mapped("debit")), 1)

    def test_negative_difference_uses_gain_account(self):
        def fake_compute(move):
            return {"diff": -1.0, "lines": 1, "coretax": 10999.0, "odoo": 11000.0, "tax_lines": self._tax_lines(move)}

        with patch.object(
            type(self.env["account.move"]), "_efn_coretax_compute", autospec=True, side_effect=fake_compute
        ):
            inv = self._invoice([(100000.0, 1.0)], self.taxes["excl"])
        adj = inv.efn_coretax_adjustment_move_id
        self.assertTrue(adj)
        tax_line = self._tax_lines(inv)
        debit = adj.line_ids.filtered(lambda aml: aml.debit)
        credit = adj.line_ids.filtered(lambda aml: aml.credit)
        self.assertEqual((debit.account_id, debit.debit), (tax_line.account_id, 1))
        self.assertEqual(debit.tax_tag_ids, tax_line.tax_tag_ids)
        self.assertEqual((credit.account_id, credit.credit), (self.gain_account, 1))
        self.assertEqual(inv.efn_coretax_rounding_diff, -1)

    def test_multi_company_uses_own_accounts(self):
        data_2 = self.setup_other_company(name="EFN Coretax PT 2")
        company_2 = data_2["company"]
        if company_2.currency_id != self.idr:
            self.skipTest("company uji ke-2 bukan IDR")
        taxes_2 = self._make_taxes(company_2)
        if not taxes_2:
            self.skipTest("pajak tax_ST4 company ke-2 tidak ditemukan")
        account_2, _gain_2, journal_2 = self._configure(data_2, "99990011")
        draft = self._invoice(INCLUDE_LINES, taxes_2["incl"], company=company_2, post=False, check=False)
        if not draft.l10n_id_coretax_efaktur_available:
            self.skipTest("company uji ke-2 bukan negara ID (setup_other_company)")
        draft.action_post()
        inv = draft
        adj = inv.efn_coretax_adjustment_move_id
        self.assertTrue(adj)
        self.assertEqual(adj.company_id, company_2)
        self.assertEqual(adj.journal_id, journal_2)
        self.assertEqual(adj.line_ids.filtered(lambda aml: aml.debit).account_id, account_2)
        self.assertNotEqual(account_2, self.rounding_account)
        # PT 1 tetap memakai akunnya sendiri.
        inv_1 = self._invoice(INCLUDE_LINES, self.taxes["incl"])
        self.assertEqual(inv_1.efn_coretax_adjustment_move_id.journal_id, self.journal)

    def test_missing_config_and_locked_period(self):
        self.company.write({"efn_tax_rounding_account_id": False, "efn_tax_rounding_journal_id": False})
        with self.assertLogs(LOGGER, "WARNING"):
            inv = self._invoice(INCLUDE_LINES, self.taxes["incl"])
        self.assertFalse(inv.efn_coretax_adjustment_move_id)
        with self.assertRaises(UserError):
            inv._efn_coretax_sync_rounding(strict=True)  # ekspor tanpa akun -> ditolak
        self.company.write(
            {
                "efn_tax_rounding_account_id": self.rounding_account.id,
                "efn_tax_rounding_journal_id": self.journal.id,
                "tax_lock_date": datetime.date(2026, 10, 1),  # = tanggal faktur (lampau)
            }
        )
        with self.assertRaises(UserError):
            inv._efn_coretax_sync_rounding(strict=True)  # periode pajak terkunci
        self.assertFalse(self._adjustments(inv))

    def test_reset_to_draft_reverses_adjustment(self):
        inv = self._invoice(INCLUDE_LINES, self.taxes["incl"])
        adj = inv.efn_coretax_adjustment_move_id
        inv.button_draft()
        self.assertFalse(inv.efn_coretax_adjustment_move_id)
        self.assertEqual(self.env["account.move"].search([("reversed_entry_id", "=", adj.id)]).state, "posted")
        inv.action_post()
        new_adj = inv.efn_coretax_adjustment_move_id
        self.assertTrue(new_adj)
        self.assertNotEqual(new_adj, adj)

    def test_settings_constraints(self):
        income = self.company_data["default_account_revenue"]
        with self.assertRaises(ValidationError):
            self.company.write({"efn_tax_rounding_account_id": income.id})
        sale_journal = self.company_data["default_journal_sale"]
        with self.assertRaises(ValidationError):
            self.company.write({"efn_tax_rounding_journal_id": sale_journal.id})
