"""Template impor mutasi bank CSV (port ADR-049 dari `custom_bank_import/models/bank_import_template.py`).

Perubahan EFN (G58 §4.1/§4.2):
- hanya CSV: generik (indeks kolom 1-based) + deteksi otomatis BCA korporat (KlikBCA Bisnis); parser XLS/XLSX
  (BRI BRISIM, BNI Transaction Inquiry, Mandiri Acc_Statement) ditunda (Q-G58-1);
- parser PRIVAT (`_efn_parse`): sumber `parse_csv` publik -> siapa pun bisa memanggilnya lewat RPC;
- encoding ketat: `utf-8-sig` lalu `cp1252` (mode otomatis); gagal -> tolak (sumber `errors="replace"` merusak narasi);
- error per baris = kode (`invalid_date`, `invalid_amount`, `missing_column`) tanpa isi sel (narasi memuat nama &
  nomor rekening lawan transaksi); baris nol dihitung `skipped_zero`;
- `company_id` wajib + kode unik per company; `sample_file` dibuang (contoh mutasi asli = data rahasia).
"""

import csv
import io
import re
from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal, InvalidOperation

from odoo import _, api, fields, models
from odoo.exceptions import UserError

MAX_ROWS_PARAM = "efn_bank_import.max_rows"
DEFAULT_MAX_ROWS = 20000

ERR_DATE = "invalid_date"
ERR_AMOUNT = "invalid_amount"
ERR_COLUMN = "missing_column"

_BCA_CORP_HEADER = ["Tanggal Transaksi", "Keterangan", "Cabang", "Jumlah", "Saldo"]
_BCA_CORP_FOOTER = ("Saldo Awal", "Mutasi Debet", "Mutasi Kredit", "Saldo Akhir")
_BCA_PERIOD_RE = re.compile(r"Periode\s*:\s*(\d{2})/(\d{2})/(\d{4})\s*-\s*\d{2}/\d{2}/(\d{4})")
_BCA_DATE_RE = re.compile(r"^(\d{2})/(\d{2})(?:/(\d{4}))?$")
_BCA_AMOUNT_RE = re.compile(r"^(.*?)\s*(CR|DB)$", re.IGNORECASE)


def _cell(row, idx_1based):
    """Isi sel kolom 1-based, atau None bila kolom tidak dipakai (<=0) / tidak ada di baris."""
    if not idx_1based or idx_1based <= 0:
        return None
    i = idx_1based - 1
    return row[i] if 0 <= i < len(row) else None


def _bca_amount(raw):
    """Nominal BCA korporat selalu `1,234,567.89` (koma ribuan, titik desimal). None bila tidak valid."""
    text = str(raw or "").strip().replace(",", "")
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


class EfnBankImportTemplate(models.Model):
    _name = "efn.bank.import.template"
    _description = "EFN Template Impor Mutasi Bank"
    _order = "sequence, name"
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(required=True, index=True, help="Kode stabil, mis. 'generic_signed_csv'.")
    bank_id = fields.Many2one("res.bank", string="Bank")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    active = fields.Boolean(default=True)
    encoding = fields.Selection(
        [("auto", "Otomatis (UTF-8, lalu Windows-1252)"), ("utf-8", "UTF-8"), ("cp1252", "Windows-1252")],
        default="auto",
        required=True,
    )
    delimiter = fields.Char(default=",", size=1, required=True)
    has_header = fields.Boolean(default=True, help="Lewati baris pertama (judul kolom).")
    date_format = fields.Char(default="%d/%m/%Y", required=True, help="Format strptime Python, mis. %d/%m/%Y.")
    # Indeks kolom 1-based; -1 = tidak dipakai.
    date_column_index = fields.Integer(default=1, required=True)
    ref_column_index = fields.Integer(default=-1, help="Kolom narasi/keterangan -> Label baris mutasi.")
    amount_credit_column_index = fields.Integer(default=-1)
    amount_debit_column_index = fields.Integer(default=-1)
    balance_column_index = fields.Integer(default=-1)
    signed_amount_column_index = fields.Integer(
        default=-1, help="Bila diisi: kolom nominal bertanda (mengalahkan kolom kredit/debit)."
    )
    decimal_separator = fields.Char(default=".", size=1, required=True)
    thousand_separator = fields.Char(default=",", size=1)

    _code_company_uniq = models.Constraint("unique(code, company_id)", "Kode template harus unik per company.")

    # ------------------------------------------------------------------ data bawaan
    @api.model
    def _efn_ensure_default_templates(self):
        """Contoh struktur per company (bukan data klien): generik bertanda + BCA korporat. Idempoten."""
        defaults = (
            {
                "code": "generic_signed_csv",
                "name": "CSV generik (tanggal, keterangan, nominal bertanda)",
                "sequence": 10,
                "date_format": "%d/%m/%Y",
                "date_column_index": 1,
                "ref_column_index": 2,
                "signed_amount_column_index": 3,
                "balance_column_index": 4,
            },
            {
                "code": "bca_corp_csv",
                "name": "BCA KlikBCA Bisnis (deteksi otomatis)",
                "sequence": 20,
            },
        )
        Template = self.sudo().with_context(active_test=False)
        for company in self.env["res.company"].sudo().search([]):
            existing = set(Template.search([("company_id", "=", company.id)]).mapped("code"))
            to_create = [dict(vals, company_id=company.id) for vals in defaults if vals["code"] not in existing]
            if to_create:
                self.sudo().create(to_create)

    # ------------------------------------------------------------------ parsing (privat)
    def _efn_decode(self, file_bytes):
        encodings = ("utf-8-sig", "cp1252") if self.encoding == "auto" else (self.encoding,)
        for encoding in encodings:
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise UserError(
            _("Encoding file tidak dikenali (dicoba: %s). Simpan ulang file sebagai CSV UTF-8.", ", ".join(encodings))
        )

    def _efn_parse_amount(self, raw):
        """Decimal, Decimal('0') bila kosong, None bila tidak valid."""
        text = str(raw if raw is not None else "").strip()
        if not text:
            return Decimal("0")
        if self.thousand_separator:
            text = text.replace(self.thousand_separator, "")
        if self.decimal_separator and self.decimal_separator != ".":
            text = text.replace(self.decimal_separator, ".")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        try:
            return Decimal(text)
        except InvalidOperation:
            return None

    def _efn_parse_date(self, raw):
        text = str(raw or "").strip()
        if not text:
            return False
        try:
            return datetime.strptime(text, self.date_format).date()
        except ValueError:
            return False

    def _efn_parse(self, file_bytes):
        """Parse file CSV mutasi. Kembalikan dict:
        ``lines`` [{date, ref, amount (Decimal), balance (Decimal|None)}], ``errors`` [(nomor baris, kode)],
        ``total_rows``, ``skipped_zero``. Tidak pernah memasukkan isi sel ke pesan error."""
        self.ensure_one()
        text = self._efn_decode(file_bytes)
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=self.delimiter or ","))
        max_rows = int(self.env["ir.config_parameter"].sudo().get_param(MAX_ROWS_PARAM, DEFAULT_MAX_ROWS))
        if len(rows) > max_rows:
            raise UserError(
                _(
                    "File memuat %(rows)s baris; maksimum %(max)s. Potong file per periode.",
                    rows=len(rows),
                    max=max_rows,
                )
            )
        if self._efn_is_bca_corp(rows):
            return self._efn_parse_bca_corp(rows)
        return self._efn_parse_generic(rows)

    def _efn_parse_generic(self, rows):
        start = 1 if self.has_header else 0
        data_rows = rows[start:]
        lines, errors = [], []
        skipped_zero = 0
        signed = self.signed_amount_column_index and self.signed_amount_column_index > 0
        for n, row in enumerate(data_rows, start=start + 1):
            if not row or all(str(c).strip() == "" for c in row):
                continue
            needed = [self.date_column_index]
            needed += (
                [self.signed_amount_column_index]
                if signed
                else [i for i in (self.amount_credit_column_index, self.amount_debit_column_index) if i and i > 0]
            )
            if any(idx > len(row) for idx in needed if idx and idx > 0):
                errors.append((n, ERR_COLUMN))
                continue
            line_date = self._efn_parse_date(_cell(row, self.date_column_index))
            if not line_date:
                errors.append((n, ERR_DATE))
                continue
            if signed:
                amount = self._efn_parse_amount(_cell(row, self.signed_amount_column_index))
            else:
                credit = self._efn_parse_amount(_cell(row, self.amount_credit_column_index))
                debit = self._efn_parse_amount(_cell(row, self.amount_debit_column_index))
                amount = None if credit is None or debit is None else credit - debit
            if amount is None:
                errors.append((n, ERR_AMOUNT))
                continue
            if amount == 0:
                skipped_zero += 1
                continue
            balance_raw = _cell(row, self.balance_column_index)
            balance = self._efn_parse_amount(balance_raw) if balance_raw not in (None, "") else None
            lines.append(
                {
                    "date": line_date,
                    "ref": re.sub(r"\s+", " ", str(_cell(row, self.ref_column_index) or "")).strip(),
                    "amount": amount,
                    "balance": balance,
                }
            )
        return {"lines": lines, "errors": errors, "total_rows": len(data_rows), "skipped_zero": skipped_zero}

    # ------------------------------------------------------------------ BCA korporat (KlikBCA Bisnis)
    # Tidak bisa dideskripsikan model indeks kolom: preamble metadata sebelum judul kolom; tanggal `DD/MM` TANPA tahun
    # (tahun dari baris "Periode :") atau `DD/MM/YYYY`; nominal + tanda menyatu di "Jumlah" (`30,000.00 DB` /
    # `1,000.00 CR`; CR = masuk/positif, DB = keluar/negatif); baris penutup Saldo Awal/Mutasi/Saldo Akhir.
    # Dideteksi otomatis, terlepas dari konfigurasi kolom template.
    @api.model
    def _efn_is_bca_corp(self, rows):
        if not rows:
            return False
        if (str(_cell(rows[0], 1) or "")).strip().startswith("Informasi Rekening"):
            return True
        return any([str(c).strip() for c in row[:5]] == _BCA_CORP_HEADER for row in rows)

    @api.model
    def _efn_parse_bca_corp(self, rows):
        lines, errors = [], []
        skipped_zero = 0
        from_month = from_year = to_year = None
        header_idx = None
        for i, row in enumerate(rows):
            match = _BCA_PERIOD_RE.search(" ".join(str(c) for c in row))
            if match:
                from_month, from_year, to_year = int(match.group(2)), int(match.group(3)), int(match.group(4))
            if [str(c).strip() for c in row[:5]] == _BCA_CORP_HEADER:
                header_idx = i
                break
        start = header_idx + 1 if header_idx is not None else 0
        for n, row in enumerate(rows[start:], start=start + 1):
            if not row or all(str(c).strip() == "" for c in row):
                continue
            first = str(_cell(row, 1) or "").strip()
            if first.startswith(_BCA_CORP_FOOTER):
                break
            if len(row) < 4:
                errors.append((n, ERR_COLUMN))
                continue
            date_match = _BCA_DATE_RE.match(first)
            if not date_match:
                errors.append((n, ERR_DATE))
                continue
            day, month = int(date_match.group(1)), int(date_match.group(2))
            if date_match.group(3):
                year = int(date_match.group(3))
            elif from_year is None:
                errors.append((n, ERR_DATE))
                continue
            else:
                # Periode Des->Jan: bulan sebelum bulan awal memakai tahun akhir.
                year = to_year if (to_year != from_year and month < from_month) else from_year
            try:
                line_date = date_cls(year, month, day)
            except ValueError:
                errors.append((n, ERR_DATE))
                continue
            raw_amount = str(_cell(row, 4) or "").strip()
            amount_match = _BCA_AMOUNT_RE.match(raw_amount)
            if amount_match:
                number, sign = amount_match.group(1), (1 if amount_match.group(2).upper() == "CR" else -1)
            else:
                number, sign = raw_amount, 1
            amount = _bca_amount(number)
            if amount is None:
                errors.append((n, ERR_AMOUNT))
                continue
            amount *= sign
            if amount == 0:
                skipped_zero += 1
                continue
            balance_raw = _cell(row, 5)
            balance = _bca_amount(balance_raw) if balance_raw not in (None, "") else None
            lines.append(
                {
                    "date": line_date,
                    "ref": re.sub(r"\s+", " ", str(_cell(row, 2) or "")).strip(),
                    "amount": amount,
                    "balance": balance,
                }
            )
        return {
            "lines": lines,
            "errors": errors,
            "total_rows": max(0, len(rows) - start),
            "skipped_zero": skipped_zero,
        }
