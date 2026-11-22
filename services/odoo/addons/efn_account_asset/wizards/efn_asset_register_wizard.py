"""Laporan Daftar Aset & Penyusutan (XLSX + PDF). Ditulis ulang (engine `custom.report.engine` sumber dibuang,
ADR-049 / G57 §1.2).

Perbaikan bug sumber: nilai buku sumber = perolehan - akumulasi jadwal (mengabaikan revaluasi, akumulasi awal,
pelepasan sebagian, baris dibalik). Di sini per tanggal akhir laporan:
- nilai bruto = perolehan + revaluasi saat ini, dikoreksi mundur untuk revaluasi / pelepasan sebagian SESUDAH
  tanggal laporan;
- akumulasi = baris jadwal tidak dibalik s.d. tanggal + akumulasi awal - akumulasi dilepas (pelepasan sebagian
  s.d. tanggal);
- aset yang sudah digabung (`merged_into_id`) tidak ditampilkan; aset dilepas s.d. tanggal laporan bernilai buku 0.
Kolom bulanan memakai jadwal (baris belum diposting = proyeksi), sama dengan sumber.
"""

import base64
import io
from datetime import date

from odoo import _, fields, models
from odoo.exceptions import AccessError

from ..models.efn_account_asset_group import MANAGER_GROUP, USER_GROUP

READONLY_GROUP = "account.group_account_readonly"
MONTH_LABELS = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
TOTAL_KEYS = ("acq_value", "opening", "ytd", "accum_end", "book")


class EfnAccountAssetRegisterWizard(models.TransientModel):
    _name = "efn.account.asset.register.wizard"
    _description = "EFN Asset Register Report"

    year = fields.Integer(string="Tahun", required=True, default=lambda self: fields.Date.context_today(self).year)
    company_ids = fields.Many2many(
        "res.company",
        "efn_asset_reg_wiz_company_rel",
        "wizard_id",
        "company_id",
        string="PT",
        default=lambda self: self.env.companies,
    )
    group_ids = fields.Many2many(
        "efn.account.asset.group", "efn_asset_reg_wiz_group_rel", "wizard_id", "group_id", string="Kelompok"
    )
    location_ids = fields.Many2many(
        "efn.account.asset.location", "efn_asset_reg_wiz_location_rel", "wizard_id", "location_id", string="Lokasi"
    )
    asset_states = fields.Selection(
        [("running", "Hanya berjalan"), ("all", "Semua (termasuk draft/dilepas)")],
        string="Aset",
        default="running",
        required=True,
    )
    xlsx_file = fields.Binary(readonly=True, attachment=True)
    xlsx_name = fields.Char(readonly=True)

    def _efn_check_access(self):
        user = self.env.user
        if not self.env.su and not (
            user.has_group(USER_GROUP) or user.has_group(MANAGER_GROUP) or user.has_group(READONLY_GROUP)
        ):
            raise AccessError(_("Laporan Daftar Aset hanya untuk pengguna Aset atau Akuntansi (baca)."))
        if self.company_ids - self.env.companies:
            raise AccessError(_("Laporan hanya untuk PT yang aktif untuk Anda."))

    def _efn_register_data(self):
        """Baris laporan (dict) + total. Membaca aset dengan hak user (record rule company berlaku)."""
        self.ensure_one()
        self._efn_check_access()
        date_from, date_to = date(self.year, 1, 1), date(self.year, 12, 31)
        companies = self.company_ids or self.env.companies
        domain = [("company_id", "in", companies.ids), ("merged_into_id", "=", False)]
        if self.group_ids:
            domain.append(("group_id", "in", self.group_ids.ids))
        if self.location_ids:
            domain.append(("location_id", "in", self.location_ids.ids))
        if self.asset_states == "running":
            domain.append(("state", "=", "running"))
        assets = self.env["efn.account.asset"].search(domain, order="code, id")
        rows = []
        totals = dict.fromkeys(TOTAL_KEYS, 0.0) | {f"m{i}": 0.0 for i in range(12)}
        for index, asset in enumerate(assets, start=1):
            row = self._efn_register_row(asset, date_from, date_to)
            row["no"] = index
            rows.append(row)
            for key in totals:
                totals[key] += row[key]
        return {
            "title": _("Daftar Aset Tetap & Penyusutan %(year)s", year=self.year),
            "companies": ", ".join(companies.mapped("name")),
            "rows": rows,
            "totals": totals,
            "currency": self.env.company.currency_id,
            "months": MONTH_LABELS,
        }

    def _efn_register_row(self, asset, date_from, date_to):
        months = [0.0] * 12
        opening = 0.0
        for line in asset.depreciation_line_ids.filtered(lambda ln: not ln.reversed and ln.date):
            if line.date < date_from:
                opening += line.amount
            elif line.date <= date_to:
                months[line.date.month - 1] += line.amount
        partials = asset.partial_disposal_ids.filtered(lambda p: p.quantity_after > 0)
        retired_before = sum(partials.filtered(lambda p: p.disposal_date < date_from).mapped("accumulated_removed"))
        retired_in_year = sum(
            partials.filtered(lambda p: date_from <= p.disposal_date <= date_to).mapped("accumulated_removed")
        )
        later_cost = sum(partials.filtered(lambda p: p.disposal_date > date_to).mapped("cost_removed"))
        later_reval = sum(
            asset.revaluation_ids.filtered(lambda r: r.revaluation_date > date_to).mapped("revaluation_amount")
        )
        opening = opening + (asset.opening_accumulated_depreciation or 0.0) - retired_before
        ytd = sum(months)
        gross = (asset.acquisition_value or 0.0) + (asset.revaluation_value or 0.0) - later_reval + later_cost
        accum_end = opening + ytd - retired_in_year
        book = gross - accum_end
        disposed = asset.state == "disposed" and asset.disposal_date and asset.disposal_date <= date_to
        if disposed:
            accum_end = book = 0.0
        row = {
            "code": asset.code or "",
            "name": asset.name or "",
            "group": asset.group_id.name or "",
            "acq_date": asset.acquisition_date,
            "life": asset.useful_life_months or 0,
            "acq_value": gross,
            "opening": opening,
            "ytd": ytd,
            "accum_end": accum_end,
            "book": book,
            "state": dict(asset._fields["state"].selection).get(asset.state, asset.state),
        }
        row.update({f"m{i}": months[i] for i in range(12)})
        return row

    def action_xlsx(self):
        self.ensure_one()
        import xlsxwriter  # noqa: PLC0415 - dependensi web CE (lazy, pola efn_account_reports)

        data = self._efn_register_data()
        buf = io.BytesIO()
        # SEC-G57-03: teks (nama aset dari tagihan vendor, dsb.) tidak pernah menjadi formula/URL.
        book = xlsxwriter.Workbook(buf, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
        sheet = book.add_worksheet(_("Daftar Aset")[:31])
        bold = book.add_format({"bold": True})
        money = book.add_format({"num_format": "#,##0"})
        money_b = book.add_format({"num_format": "#,##0", "bold": True})
        sheet.write_string(0, 0, data["title"], bold)
        sheet.write_string(1, 0, data["companies"])
        heads = [
            "No",
            "Kode",
            "Nama Aset",
            "Kelompok",
            "Tgl Perolehan",
            "Umur (bln)",
            "Nilai Perolehan",
            "Akum. Awal Tahun",
            *data["months"],
            "YTD",
            "Akum. Akhir",
            "Nilai Buku",
            "Status",
        ]
        for col, head in enumerate(heads):
            sheet.write(3, col, head, bold)
        money_keys = ["acq_value", "opening", *[f"m{i}" for i in range(12)], "ytd", "accum_end", "book"]
        row_idx = 3
        for row in data["rows"]:
            row_idx += 1
            sheet.write_number(row_idx, 0, row["no"])
            sheet.write_string(row_idx, 1, row["code"])
            sheet.write_string(row_idx, 2, row["name"])
            sheet.write_string(row_idx, 3, row["group"])
            sheet.write_string(row_idx, 4, fields.Date.to_string(row["acq_date"]) or "")
            sheet.write_number(row_idx, 5, row["life"])
            for offset, key in enumerate(money_keys):
                sheet.write_number(row_idx, 6 + offset, row[key], money)
            sheet.write_string(row_idx, 6 + len(money_keys), row["state"])
        row_idx += 1
        sheet.write(row_idx, 2, "TOTAL", bold)
        for offset, key in enumerate(money_keys):
            sheet.write_number(row_idx, 6 + offset, data["totals"][key], money_b)
        sheet.set_column(1, 1, 16)
        sheet.set_column(2, 2, 34)
        sheet.set_column(3, 3, 20)
        sheet.set_column(6, 6 + len(money_keys), 15)
        book.close()
        name = f"Daftar_Aset_{self.year}.xlsx"
        self.write({"xlsx_file": base64.b64encode(buf.getvalue()), "xlsx_name": name})
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self._name}/{self.id}/xlsx_file/{name}?download=true",
            "target": "self",
        }

    def action_pdf(self):
        self.ensure_one()
        self._efn_check_access()
        return self.env.ref("efn_account_asset.action_report_efn_asset_register").report_action(self)
