"""Wizard laporan keuangan: pilih jenis, PT (satu = per PT, beberapa = konsolidasi), periode -> layar/PDF/XLSX."""

import base64
import io

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

REPORT_TYPES = [("bs", "Neraca"), ("pl", "Laba Rugi"), ("tb", "Neraca Saldo")]


class EfnFinancialReport(models.TransientModel):
    _name = "efn.financial.report"
    _description = "EFN laporan keuangan"

    report_type = fields.Selection(REPORT_TYPES, required=True, default="bs", string="Laporan")
    company_ids = fields.Many2many(
        "res.company",
        string="PT",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.user.company_ids.ids)],
        help="Satu PT = laporan per PT. Beberapa PT = konsolidasi (dengan eliminasi antar-PT).",
    )
    date_from = fields.Date(
        string="Dari", required=True, default=lambda self: fields.Date.context_today(self).replace(month=1, day=1)
    )
    date_to = fields.Date(string="Sampai", required=True, default=fields.Date.context_today)
    eliminate = fields.Boolean(string="Eliminasi antar-PT", default=True)
    html = fields.Html(string="Hasil", compute="_compute_html", sanitize=False)
    xlsx_file = fields.Binary(readonly=True)
    xlsx_name = fields.Char(readonly=True)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from > rec.date_to:
                raise UserError(_("efn_account_reports:dates — tanggal 'Dari' harus sebelum 'Sampai'."))

    def _efn_check_access(self):
        if not self.env.user.has_group("account.group_account_readonly") and not self.env.user.has_group(
            "account.group_account_invoice"
        ):
            raise UserError(_("efn_account_reports:access — butuh hak baca Akuntansi."))
        if self.company_ids - self.env.user.company_ids:
            raise UserError(_("efn_account_reports:company — PT di luar akses Anda."))

    def _efn_data(self):
        self.ensure_one()
        self._efn_check_access()
        engine = self.env["efn.financial.report.engine"].sudo()
        companies = self.company_ids.sorted("id")
        if self.report_type == "bs":
            data = engine._efn_balance_sheet(companies, self.date_to, self.eliminate)
        elif self.report_type == "pl":
            data = engine._efn_profit_loss(companies, self.date_from, self.date_to, self.eliminate)
        else:
            data = engine._efn_trial_balance(companies, self.date_from, self.date_to, self.eliminate)
        data.update(
            {
                "title": dict(REPORT_TYPES)[self.report_type],
                "companies": ", ".join(companies.mapped("name")),
                "consolidated": len(companies) > 1,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "report_type": self.report_type,
                "currency": companies[:1].currency_id,
            }
        )
        return data

    @api.depends("report_type", "company_ids", "date_from", "date_to", "eliminate")
    def _compute_html(self):
        for rec in self:
            if not rec.company_ids or not rec.date_from or not rec.date_to:
                rec.html = False
                continue
            try:
                data = rec._efn_data()
            except UserError as exc:
                rec.html = Markup("<div class='alert alert-danger'>%s</div>") % exc.args[0]
                continue
            rec.html = self.env["ir.qweb"]._render("efn_account_reports.efn_financial_report_body", {"d": data})

    def action_pdf(self):
        self.ensure_one()
        self._efn_check_access()
        return self.env.ref("efn_account_reports.action_efn_financial_report_pdf").report_action(self)

    def action_xlsx(self):
        self.ensure_one()
        import xlsxwriter  # noqa: PLC0415 - dependensi web CE (lazy, sama dengan /web/export/xlsx)

        data = self._efn_data()
        buf = io.BytesIO()
        book = xlsxwriter.Workbook(buf, {"in_memory": True})
        sheet = book.add_worksheet(data["title"][:31])
        bold = book.add_format({"bold": True})
        money = book.add_format({"num_format": "#,##0"})
        money_b = book.add_format({"num_format": "#,##0", "bold": True})
        sheet.write(0, 0, data["title"], bold)
        sheet.write(1, 0, data["companies"])
        sheet.write(2, 0, f"{data['date_from']} s.d. {data['date_to']}")
        row = 4
        if data["report_type"] == "tb":
            for col, head in enumerate(("Kode", "Akun", "Saldo awal", "Debit", "Kredit", "Saldo akhir")):
                sheet.write(row, col, head, bold)
            for line in data["lines"]:
                row += 1
                sheet.write(row, 0, line["code"])
                sheet.write(row, 1, line["name"])
                for col, key in enumerate(("opening", "debit", "credit", "closing"), start=2):
                    sheet.write_number(row, col, line[key], money)
            row += 1
            sheet.write(row, 1, "Total", bold)
            for col, key in enumerate(("opening", "debit", "credit", "closing"), start=2):
                sheet.write_number(row, col, data["totals"][key], money_b)
        else:
            for sec in data["sections"]:
                sheet.write(row, 0, sec["label"], bold)
                row += 1
                for line in sec["lines"]:
                    sheet.write(row, 0, line["code"])
                    sheet.write(row, 1, line["name"])
                    sheet.write_number(row, 2, line["amount"], money)
                    row += 1
                sheet.write(row, 1, f"Total {sec['label']}", bold)
                sheet.write_number(row, 2, sec["total"], money_b)
                row += 2
            for key, value in data["totals"].items():
                sheet.write(row, 1, key, bold)
                sheet.write_number(row, 2, value, money_b)
                row += 1
            if data.get("eliminations"):
                row += 1
                sheet.write(row, 0, "Eliminasi antar-PT", bold)
                for line in data["eliminations"]:
                    row += 1
                    sheet.write(row, 0, line["code"])
                    sheet.write(row, 1, line["name"])
                    sheet.write_number(row, 2, line["amount"], money)
        sheet.set_column(0, 0, 12)
        sheet.set_column(1, 1, 45)
        sheet.set_column(2, 5, 18)
        book.close()
        name = f"{data['title']} {self.date_to}.xlsx"
        self.write({"xlsx_file": base64.b64encode(buf.getvalue()), "xlsx_name": name})
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self._name}/{self.id}/xlsx_file/{name}?download=true",
            "target": "self",
        }
