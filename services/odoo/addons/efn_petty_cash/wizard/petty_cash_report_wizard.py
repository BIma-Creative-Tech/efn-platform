"""Laporan kas kecil: Kartu Uang Muka, Outstanding, Aging (XLSX / PDF) — K-PC5.

Pengganti `custom_accounting_reports` + tiga wizard sumber (tidak diport). Semua angka dari `account.move.line`
posted pada akun uang muka jenis-jenis company (per `date_to`), dibaca dengan hak user (tanpa sudo).
"""

import base64
import io

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models.common import GROUP_FINANCE

READONLY_GROUP = "account.group_account_readonly"
AGING_BUCKETS = ((0, 30, "0-30"), (31, 60, "31-60"), (61, 90, "61-90"), (91, None, ">90"))
TITLES = {"statement": "Kartu Uang Muka", "outstanding": "Outstanding Uang Muka", "aging": "Aging Uang Muka"}


class EfnPettyCashReportWizard(models.TransientModel):
    _name = "efn.petty.cash.report.wizard"
    _description = "EFN Laporan Kas Kecil & Uang Muka"
    _check_company_auto = True

    report_kind = fields.Selection(
        [("statement", "Kartu Uang Muka"), ("outstanding", "Outstanding"), ("aging", "Aging")],
        string="Laporan",
        required=True,
        default="outstanding",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        domain=lambda self: [("id", "in", self.env.companies.ids)],
    )
    type_ids = fields.Many2many(
        "efn.petty.cash.type",
        string="Jenis",
        check_company=True,
        help="Kosong = semua jenis.",
    )
    partner_id = fields.Many2one("res.partner", string="Pemegang")
    date_from = fields.Date(string="Dari", help="Hanya Kartu Uang Muka; kosong = sejak awal.")
    date_to = fields.Date(string="Per Tanggal", required=True, default=fields.Date.context_today)
    xlsx_file = fields.Binary(readonly=True, attachment=False)
    xlsx_name = fields.Char(readonly=True)

    def _efn_check_access(self):
        user = self.env.user
        if not (user.has_group(GROUP_FINANCE) or user.has_group(READONLY_GROUP)):
            raise AccessError(_("Laporan kas kecil hanya untuk Kas Kecil / Finance atau auditor akuntansi."))
        for wizard in self:
            if wizard.company_id not in self.env.companies:
                raise AccessError(_("Company laporan tidak termasuk company yang Anda pilih."))

    def _efn_lines(self, date_to, date_from=None):
        self.ensure_one()
        Type = self.env["efn.petty.cash.type"].with_context(active_test=False)
        types = self.type_ids or Type.search([("company_id", "=", self.company_id.id)])
        accounts = types.mapped("advance_account_id")
        if not accounts:
            raise UserError(_("Belum ada akun uang muka yang dikonfigurasi pada jenis kas kecil company ini."))
        domain = [
            ("company_id", "=", self.company_id.id),
            ("account_id", "in", accounts.ids),
            ("parent_state", "=", "posted"),
            ("date", "<=", date_to),
        ]
        if date_from:
            domain.append(("date", ">=", date_from))
        if self.partner_id:
            domain.append(("partner_id", "=", self.partner_id.id))
        return self.env["account.move.line"].search(domain, order="partner_id, date, id")

    def _efn_statement(self):
        headers = ["Pemegang", "Tanggal", "Entri", "Referensi", "Keterangan", "Debit", "Kredit", "Saldo"]
        opening = {}
        if self.date_from:
            for line in self._efn_lines(fields.Date.subtract(self.date_from, days=1)):
                opening[line.partner_id] = opening.get(line.partner_id, 0.0) + line.balance
        rows, running, current = [], 0.0, None
        for line in self._efn_lines(self.date_to, self.date_from):
            if line.partner_id != current:
                current = line.partner_id
                running = opening.get(current, 0.0)
                rows.append([current.display_name or "-", "", "", "", _("Saldo awal"), 0.0, 0.0, running])
            running += line.balance
            move = line.move_id
            row = [current.display_name or "-", str(line.date), move.name or "", move.ref or "", line.name or ""]
            rows.append([*row, line.debit, line.credit, running])
        return headers, rows, [5, 6, 7], None

    def _efn_open_items(self):
        """Saldo terbuka per (pemegang, fund, pengajuan) per ``date_to``; fund menggantikan pengajuan isiannya."""
        groups = {}
        for line in self._efn_lines(self.date_to):
            move = line.move_id
            fund = move.efn_petty_cash_fund_id
            request = move.efn_petty_cash_request_id if not fund else None
            entry = groups.setdefault((line.partner_id, fund, request), {"balance": 0.0, "first": line.date})
            entry["balance"] += line.balance
            entry["first"] = min(entry["first"], line.date)
        currency = self.company_id.currency_id
        return [(key, entry) for key, entry in groups.items() if not currency.is_zero(entry["balance"])]

    def _efn_outstanding(self):
        headers = ["Pemegang", "Dana / Pengajuan", "Sejak", "Saldo"]
        rows = []
        for (partner, fund, request), entry in self._efn_open_items():
            document = fund.name if fund else (request.name if request else _("(tanpa dokumen)"))
            rows.append([partner.display_name or "-", document, str(entry["first"]), entry["balance"]])
        return headers, rows, [3], ["Total", "", "", sum(row[3] for row in rows)]

    def _efn_aging(self):
        headers = ["Pemegang", "Pengajuan", "Sejak", *[bucket[2] for bucket in AGING_BUCKETS], "Total"]
        rows = []
        for (partner, fund, request), entry in self._efn_open_items():
            if fund:
                continue  # dana imprest permanen; bukan uang muka yang menua
            age = (self.date_to - entry["first"]).days
            amounts = [
                entry["balance"] if low <= age and (high is None or age <= high) else 0.0
                for low, high, _label in AGING_BUCKETS
            ]
            document = request.name if request else "-"
            rows.append([partner.display_name or "-", document, str(entry["first"]), *amounts, entry["balance"]])
        money_cols = list(range(3, 4 + len(AGING_BUCKETS)))
        return headers, rows, money_cols, ["Total", "", ""] + [sum(row[c] for row in rows) for c in money_cols]

    def _efn_data(self):
        """Tabel generik {title, subtitle, headers, rows, money_cols, totals} untuk XLSX dan PDF."""
        self.ensure_one()
        self._efn_check_access()
        builders = {"statement": self._efn_statement, "outstanding": self._efn_outstanding, "aging": self._efn_aging}
        headers, rows, money_cols, totals = builders[self.report_kind]()
        return {
            "title": TITLES[self.report_kind],
            "subtitle": f"{self.company_id.name} — per {self.date_to}",
            "headers": headers,
            "rows": rows,
            "money_cols": money_cols,
            "totals": totals,
        }

    def action_xlsx(self):
        self.ensure_one()
        import xlsxwriter  # noqa: PLC0415 - dependensi web CE (lazy, sama dengan /web/export/xlsx)

        data = self._efn_data()
        buf = io.BytesIO()
        # SEC-G57-03: teks pengguna (keterangan, nama) tidak pernah menjadi formula/hyperlink.
        options = {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False}
        book = xlsxwriter.Workbook(buf, options)
        sheet = book.add_worksheet(data["title"][:31])
        bold = book.add_format({"bold": True})
        money = book.add_format({"num_format": "#,##0.00"})
        money_bold = book.add_format({"num_format": "#,##0.00", "bold": True})
        sheet.write(0, 0, data["title"], bold)
        sheet.write(1, 0, data["subtitle"])
        row = 3
        for col, head in enumerate(data["headers"]):
            sheet.write(row, col, head, bold)
        for values in data["rows"]:
            row += 1
            for col, value in enumerate(values):
                if col in data["money_cols"]:
                    sheet.write_number(row, col, value, money)
                else:
                    sheet.write(row, col, value)
        if data["totals"]:
            row += 1
            for col, value in enumerate(data["totals"]):
                if col in data["money_cols"]:
                    sheet.write_number(row, col, value, money_bold)
                else:
                    sheet.write(row, col, value, bold)
        sheet.set_column(0, len(data["headers"]) - 1, 18)
        book.close()
        name = f"{data['title']} {self.date_to}.xlsx"
        self.write({"xlsx_file": base64.b64encode(buf.getvalue()), "xlsx_name": name})
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self._name}/{self.id}/xlsx_file/{name}?download=true",
            "target": "self",
        }

    def action_pdf(self):
        self.ensure_one()
        self._efn_check_access()
        return self.env.ref("efn_petty_cash.action_efn_petty_cash_report_pdf").report_action(self)

    @api.model
    def _efn_fmt(self, value):
        return f"{value:,.2f}"
