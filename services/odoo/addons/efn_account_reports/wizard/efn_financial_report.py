"""Wizard laporan keuangan: pilih jenis, PT (satu = per PT, beberapa = konsolidasi), periode -> layar/PDF/XLSX."""

import base64
import io
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

REPORT_TYPES = [("bs", "Neraca"), ("pl", "Laba Rugi"), ("cf", "Arus Kas"), ("tb", "Neraca Saldo")]
CF_METHODS = [("indirect", "Tidak langsung"), ("direct", "Langsung")]
COMPARE_TYPES = [
    ("none", "Tanpa pembanding"),
    ("previous_period", "Periode sebelumnya"),
    ("previous_year", "Tahun lalu"),
]
# G35: Neraca Saldo tanpa pembanding (kolomnya sudah saldo awal/mutasi/akhir).
COMPARABLE = ("bs", "pl", "cf")


def _is_month_end(day):
    return (day + timedelta(days=1)).day == 1


def compare_dates(kind, report_type, date_from, date_to):
    """(dari, sampai) periode pembanding (G35).

    * Tahun lalu: kedua tanggal mundur 1 tahun (29 Feb -> 28 Feb; akhir bulan tetap akhir bulan).
    * Periode sebelumnya: Neraca = posisi 1 bulan sebelum ``date_to`` (akhir bulan -> akhir bulan sebelumnya).
      Laporan periode: bila rentang = bulan utuh, mundur sejumlah bulan yang sama; selain itu mundur sepanjang
      jumlah hari yang sama dan berakhir sehari sebelum ``date_from``.
    """
    if kind == "previous_year":
        start, end = date_from - relativedelta(years=1), date_to - relativedelta(years=1)
        if _is_month_end(date_to):
            end = end + relativedelta(day=31)
        return start, end
    if report_type == "bs":
        end = date_to - relativedelta(months=1)
        if _is_month_end(date_to):
            end = end + relativedelta(day=31)
        return date_from, end
    if date_from.day == 1 and _is_month_end(date_to):
        months = (date_to.year - date_from.year) * 12 + date_to.month - date_from.month + 1
        return date_from - relativedelta(months=months), date_from - timedelta(days=1)
    length = (date_to - date_from).days + 1
    end = date_from - timedelta(days=1)
    return end - timedelta(days=length - 1), end


def merge_compare(cur, prev):
    """Gabungkan data laporan periode pembanding ke data periode berjalan (per bagian x akun).

    Setiap baris mendapat ``amount_cmp`` dan ``delta``; urutan baris periode berjalan dipertahankan (baris tanpa kode
    seperti laba bersih arus kas tetap di atas), baris yang hanya ada di pembanding ditambahkan di akhir (nilai 0).
    Bagian mendapat ``total_cmp``; totals pembanding di ``totals_cmp``.
    """
    prev_sections = {sec["key"]: sec for sec in prev.get("sections", [])}
    keys = [sec["key"] for sec in cur.get("sections", [])]
    keys += [k for k in prev_sections if k not in keys]
    cur_sections = {sec["key"]: sec for sec in cur.get("sections", [])}
    merged = []
    for key in keys:
        sec = cur_sections.get(key) or {**prev_sections[key], "lines": [], "total": 0.0}
        psec = prev_sections.get(key, {"lines": [], "total": 0.0})
        plines = {(line["code"], line["name"]): line for line in psec["lines"]}
        lines = []
        for line in sec["lines"]:
            p = plines.pop((line["code"], line["name"]), None)
            amount_cmp = p["amount"] if p else 0.0
            ids = sorted(set(line.get("account_ids", [])) | set((p or {}).get("account_ids", [])))
            lines.append(
                {**line, "account_ids": ids, "amount_cmp": amount_cmp, "delta": round(line["amount"] - amount_cmp, 2)}
            )
        for p in plines.values():
            lines.append({**p, "amount": 0.0, "amount_cmp": p["amount"], "delta": round(-p["amount"], 2)})
        merged.append(
            {
                **sec,
                "lines": lines,
                "total_cmp": psec["total"],
                "total_delta": round(sec["total"] - psec["total"], 2),
            }
        )
    return {**cur, "sections": merged, "totals_cmp": prev.get("totals", {})}


CF_TOTAL_LABELS = {
    "operating": "Kas bersih dari aktivitas operasi",
    "investing": "Kas bersih dari aktivitas investasi",
    "financing": "Kas bersih dari aktivitas pendanaan",
    "net_change": "Kenaikan (penurunan) bersih kas",
    "cash_opening": "Kas dan setara kas awal periode",
    "cash_closing": "Kas dan setara kas akhir periode",
    "difference": "Selisih",
}


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
    compare = fields.Selection(COMPARE_TYPES, string="Pembanding", required=True, default="none")
    cf_method = fields.Selection(CF_METHODS, string="Metode arus kas", required=True, default="indirect")
    use_adjustments = fields.Boolean(
        string="Penyesuaian konsolidasi",
        default=True,
        help="Sertakan penyesuaian konsolidasi posted (semua PT-nya ada di laporan, tanggal s.d. tanggal laporan).",
    )
    # Dipakai atribut `invisible` di form: evaluator ekspresi web client Odoo 19 tidak mengenal len().
    is_consolidated = fields.Boolean(compute="_compute_is_consolidated")
    html = fields.Html(string="Hasil", compute="_compute_html", sanitize=False)
    xlsx_file = fields.Binary(readonly=True)
    xlsx_name = fields.Char(readonly=True)

    @api.depends("report_type")
    def _compute_display_name(self):
        # Breadcrumb setelah drill-down: "Laba Rugi", bukan "efn.financial.report,19".
        labels = dict(REPORT_TYPES)
        for rec in self:
            rec.display_name = labels.get(rec.report_type) or _("Laporan Keuangan EFN")

    @api.depends("company_ids")
    def _compute_is_consolidated(self):
        for rec in self:
            rec.is_consolidated = len(rec.company_ids) > 1

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
        # _origin: di onchange (form live) company_ids berupa record NewId; tanpa _origin selisihnya tidak pernah
        # kosong sehingga layar selalu "PT di luar akses" (G35, regresi test_html_in_onchange_uses_real_companies).
        if self.company_ids._origin - self.env.user.company_ids:
            raise UserError(_("efn_account_reports:company — PT di luar akses Anda."))

    def _efn_compute(self, companies, date_from, date_to):
        engine = self.env["efn.financial.report.engine"].sudo()
        adjust = bool(self.use_adjustments and len(companies) > 1)
        if self.report_type == "bs":
            return engine._efn_balance_sheet(companies, date_to, self.eliminate, adjust=adjust)
        if self.report_type == "pl":
            return engine._efn_profit_loss(companies, date_from, date_to, self.eliminate, adjust=adjust)
        if self.report_type == "cf":
            method = engine._efn_cash_flow_direct if self.cf_method == "direct" else engine._efn_cash_flow
            return method(companies, date_from, date_to, self.eliminate, adjust=adjust)
        return engine._efn_trial_balance(companies, date_from, date_to, self.eliminate, adjust=adjust)

    def _efn_compare_range(self):
        """(dari, sampai) pembanding atau None (tanpa pembanding / Neraca Saldo)."""
        self.ensure_one()
        if self.compare == "none" or self.report_type not in COMPARABLE:
            return None
        return compare_dates(self.compare, self.report_type, self.date_from, self.date_to)

    def _efn_data(self):
        self.ensure_one()
        self._efn_check_access()
        companies = self.company_ids._origin.sorted("id")
        data = self._efn_compute(companies, self.date_from, self.date_to)
        cmp_range = self._efn_compare_range()
        if cmp_range:
            data = merge_compare(data, self._efn_compute(companies, *cmp_range))
            data["compare"] = {
                "date_from": cmp_range[0],
                "date_to": cmp_range[1],
                "label": dict(COMPARE_TYPES)[self.compare],
            }
        else:
            data["compare"] = None
        adjustments = self.env["efn.consolidation.adjustment"]
        if self.use_adjustments and len(companies) > 1:
            adjustments = (
                self.env["efn.consolidation.adjustment.line"].sudo()._efn_report_adjustments(companies, self.date_to)
            )
        title = dict(REPORT_TYPES)[self.report_type]
        if self.report_type == "cf":
            title = f"{title} ({dict(CF_METHODS)[self.cf_method].lower()})"
        data.update(
            {
                "title": title,
                "adjustments": [
                    {
                        "date": a.date,
                        "name": a.name,
                        "amount": round(a.total_debit, 2),
                        "approved_by": a.approved_by_id.name,
                    }
                    for a in adjustments
                ],
                "companies": ", ".join(companies.mapped("name")),
                "consolidated": len(companies) > 1,
                "date_from": self.date_from,
                "date_to": self.date_to,
                "report_type": self.report_type,
                "currency": companies[:1].currency_id,
            }
        )
        return data

    @api.depends(
        "report_type", "company_ids", "date_from", "date_to", "eliminate", "compare", "cf_method", "use_adjustments"
    )
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
            rec.html = self.env["ir.qweb"]._render(
                "efn_account_reports.efn_financial_report_body", {"d": data, "drill": True}
            )

    def action_drill(self, account_ids, period="current"):
        """G35: buka Journal Items (posted) di balik satu baris laporan.

        Dipanggil widget layar (baris akun). Akun wajib milik PT laporan; filter PT + periode yang sama dengan baris
        (Neraca = s.d. tanggal; laporan periode = rentang) dan ``period='compare'`` memakai rentang pembanding.
        Daftar dibuka TANPA sudo: ACL/record rule akuntansi pengguna tetap berlaku.
        """
        self.ensure_one()
        self._efn_check_access()
        if period not in ("current", "compare"):
            raise UserError(_("efn_account_reports:drill — periode tidak dikenal."))
        if not isinstance(account_ids, (list, tuple)) or not all(isinstance(i, int) for i in account_ids):
            raise UserError(_("efn_account_reports:drill — akun tidak valid."))
        companies = self.company_ids._origin
        accounts = self.env["account.account"].sudo().browse(account_ids).exists()
        if not accounts or len(accounts) != len(set(account_ids)):
            raise UserError(_("efn_account_reports:drill — akun tidak valid."))
        if any(not (a.company_ids & companies) for a in accounts):
            raise UserError(_("efn_account_reports:company — akun di luar PT laporan."))
        date_from, date_to = self.date_from, self.date_to
        if period == "compare":
            cmp_range = self._efn_compare_range()
            if not cmp_range:
                raise UserError(_("efn_account_reports:drill — laporan tanpa pembanding."))
            date_from, date_to = cmp_range
        domain = [
            ("account_id", "in", accounts.ids),
            ("company_id", "in", companies.ids),
            ("parent_state", "=", "posted"),
            ("date", "<=", fields.Date.to_string(date_to)),
        ]
        if self.report_type != "bs":
            domain.append(("date", ">=", fields.Date.to_string(date_from)))
        label = ", ".join(a.display_name for a in accounts[:3]) + (" …" if len(accounts) > 3 else "")
        return {
            "type": "ir.actions.act_window",
            "name": _("Jurnal: %s", label),
            "res_model": "account.move.line",
            "views": [(False, "list"), (False, "form")],
            "domain": domain,
            "context": {"create": False},
            "target": "current",
        }

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
            cmp_ = data.get("compare")
            if cmp_:
                sheet.write(row, 2, f"{data['date_from']} s.d. {data['date_to']}", bold)
                sheet.write(row, 3, f"{cmp_['date_from']} s.d. {cmp_['date_to']}", bold)
                sheet.write(row, 4, "Selisih", bold)
                row += 1
            for sec in data["sections"]:
                sheet.write(row, 0, sec["label"], bold)
                row += 1
                for line in sec["lines"]:
                    sheet.write(row, 0, line["code"])
                    sheet.write(row, 1, line["name"])
                    sheet.write_number(row, 2, line["amount"], money)
                    if cmp_:
                        sheet.write_number(row, 3, line["amount_cmp"], money)
                        sheet.write_number(row, 4, line["delta"], money)
                    row += 1
                sheet.write(row, 1, f"Total {sec['label']}", bold)
                sheet.write_number(row, 2, sec["total"], money_b)
                if cmp_:
                    sheet.write_number(row, 3, sec["total_cmp"], money_b)
                    sheet.write_number(row, 4, sec["total_delta"], money_b)
                row += 2
            labels = CF_TOTAL_LABELS if data["report_type"] == "cf" else {}
            for key, value in data["totals"].items():
                sheet.write(row, 1, labels.get(key, key), bold)
                sheet.write_number(row, 2, value, money_b)
                if cmp_ and key in data.get("totals_cmp", {}):
                    sheet.write_number(row, 3, data["totals_cmp"][key], money_b)
                    sheet.write_number(row, 4, round(value - data["totals_cmp"][key], 2), money_b)
                row += 1
            if data.get("adjustments"):
                row += 1
                sheet.write(row, 0, "Penyesuaian konsolidasi (posted)", bold)
                for adj in data["adjustments"]:
                    row += 1
                    sheet.write(row, 0, str(adj["date"]))
                    sheet.write(row, 1, adj["name"])
                    sheet.write_number(row, 2, adj["amount"], money)
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
