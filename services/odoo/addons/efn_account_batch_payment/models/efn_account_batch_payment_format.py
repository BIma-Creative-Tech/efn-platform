"""Format file transfer bank (port ADR-049 dari `custom_account_batch_payment/models/account_batch_export_format.py`).

Perubahan EFN (G58 §5.2):
- renderer PRIVAT `_efn_render(batch)` (sumber `render` publik = bisa dipanggil RPC);
- CSV benar dengan modul `csv` (sumber `delim.join` -> koma di memo memecah kolom); memo/nama disanitasi (buang
  delimiter, CR/LF, karakter di luar `[A-Za-z0-9 .,/-]`, dipotong per format) dan nilai berawalan `= + - @` diberi
  spasi depan (injeksi formula spreadsheet);
- nomor rekening penerima fail-closed: kosong / bukan digit / placeholder keyring `efn_pii` terkunci -> tolak
  seluruh ekspor, tidak pernah menulis placeholder ke file; pesan memakai nomor payment, bukan nomor rekening;
- `corporate_id` / `debit_account` pindah ke konfigurasi per company × jurnal
  (`efn.account.batch.payment.format.company`); format global tidak memuat data rahasia;
- tata letak bank (BCA MCM, Mandiri MCM, BNI, BRI) = "unverified baselines" sumber: diblokir sampai `verified`.
"""

import csv
import io
import re

from odoo import _, fields, models
from odoo.exceptions import UserError

GENERIC_CODE = "generic"
#: Penanda placeholder keyring efn_pii (`efn_pii/models/keyring.py` PLACEHOLDER diawali empat bullet).
_PII_PLACEHOLDER_MARK = "••••"
_DISALLOWED_RE = re.compile(r"[^A-Za-z0-9 .,/\-]")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _efn_sanitize(value, delimiter, max_len):
    """Teks aman untuk sel file transfer (satu baris, tanpa delimiter, tanpa awalan formula)."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    if delimiter:
        text = text.replace(delimiter, " ")
    text = re.sub(r"\s+", " ", _DISALLOWED_RE.sub("", text)).strip()
    if max_len and max_len > 0:
        text = text[:max_len].rstrip()
    if text.startswith(_FORMULA_PREFIXES):
        text = " " + text
    return text


class EfnAccountBatchPaymentFormat(models.Model):
    _name = "efn.account.batch.payment.format"
    _description = "EFN Format File Transfer Bank"
    _order = "name"
    # Pengecualian company_id (G58 §5.1): tata letak global tanpa data rahasia; data per PT ada di
    # efn.account.batch.payment.format.company (company_id + rule).

    name = fields.Char(required=True)
    code = fields.Char(required=True, index=True, help="Kode stabil, mis. 'generic', 'bca_mcm'.")
    bank_label = fields.Char(help="Bank/portal tujuan tata letak ini.")
    active = fields.Boolean(default=True)
    verified = fields.Boolean(
        string="Terverifikasi",
        help="Centang hanya setelah uji unggah file ke portal bank berhasil (Q-G58-2). Format selain 'generic' "
        "tidak bisa diekspor sebelum dicentang.",
    )
    encoding = fields.Char(default="utf-8")
    delimiter = fields.Char(default=",", size=1)
    date_format = fields.Char(default="%d/%m/%Y")
    file_extension = fields.Char(default="csv")
    include_header = fields.Boolean(default=True)
    name_max_length = fields.Integer(string="Maks. Panjang Nama", default=35)
    memo_max_length = fields.Integer(string="Maks. Panjang Memo", default=35)
    note = fields.Text()
    company_config_ids = fields.One2many("efn.account.batch.payment.format.company", "format_id")

    _code_uniq = models.Constraint("unique(code)", "Kode format harus unik.")

    # ------------------------------------------------------------------ rendering (privat)
    def _efn_company_config(self, batch):
        self.ensure_one()
        configs = self.env["efn.account.batch.payment.format.company"].search(
            [("format_id", "=", self.id), ("company_id", "=", batch.company_id.id)]
        )
        return (
            configs.filtered(lambda c: c.journal_id == batch.journal_id)[:1]
            or configs.filtered(lambda c: not c.journal_id)[:1]
        )

    def _efn_render(self, batch):
        """``(bytes, filename)`` file transfer batch. Menolak (UserError) sebelum menulis apa pun bila data tidak
        lengkap atau rekening tidak valid."""
        self.ensure_one()
        rows = self._efn_rows(batch)
        renderer = getattr(self, f"_efn_render_{self.code}", None)
        if renderer is None:
            raise UserError(_("Format %s belum punya tata letak.", self.code))
        header, data = renderer(batch, rows)
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=self.delimiter or ",", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
        if self.include_header and header:
            writer.writerow(header)
        writer.writerows(data)
        content = buf.getvalue().encode(self.encoding or "utf-8")
        base = re.sub(r"[^A-Za-z0-9_-]", "-", f"{self.code}_{batch.name or 'batch'}")
        return content, f"{base}.{self.file_extension or 'csv'}"

    def _efn_rows(self, batch):
        """Data baris bersama semua tata letak. Fail-closed atas rekening & nama (keyring efn_pii terkunci)."""
        delim = self.delimiter or ","
        missing, locked, invalid = [], [], []
        rows = []
        for pay in batch.payment_ids.sorted("id"):
            bank = pay.partner_bank_id
            raw_account = bank.acc_number or ""
            holder = bank.acc_holder_name or pay.partner_id.name or ""
            if not raw_account:
                missing.append(pay.name)
                continue
            if _PII_PLACEHOLDER_MARK in raw_account or _PII_PLACEHOLDER_MARK in holder:
                locked.append(pay.name)
                continue
            account = re.sub(r"[\s.\-]", "", raw_account)
            if not account.isdigit():
                invalid.append(pay.name)
                continue
            rows.append(
                {
                    "account": account,
                    "holder": _efn_sanitize(holder, delim, self.name_max_length),
                    "amount": f"{pay.amount:.2f}",
                    "amount_int": f"{pay.amount:.0f}",
                    "date": (pay.date or batch.date).strftime(self.date_format or "%d/%m/%Y"),
                    "memo": _efn_sanitize(pay.memo or pay.name, delim, self.memo_max_length),
                    "bic": _efn_sanitize(bank.bank_id.bic, delim, 11),
                }
            )
        if locked:
            raise UserError(
                _(
                    "Kunci PII tidak tersedia: nomor/nama rekening tidak dapat dibaca untuk %s. Ekspor dibatalkan; "
                    "hubungi admin sistem (keyring efn_pii).",
                    ", ".join(locked),
                )
            )
        if missing:
            raise UserError(
                _("Pembayaran tanpa rekening penerima: %s. Isi rekening bank partner dulu.", ", ".join(missing))
            )
        if invalid:
            raise UserError(_("Nomor rekening penerima bukan angka untuk: %s.", ", ".join(invalid)))
        return rows

    # ------------------------------------------------------------------ tata letak
    def _efn_render_generic(self, batch, rows):
        return (
            ["Account Number", "Account Holder", "Amount", "Date", "Memo", "Bank BIC"],
            [[r["account"], r["holder"], r["amount"], r["date"], r["memo"], r["bic"]] for r in rows],
        )

    def _efn_render_bca_mcm(self, batch, rows):
        # Baseline BCA MCM sumber (BELUM DIVERIFIKASI): tanpa judul, nominal tanpa desimal (IDR).
        config = self._efn_company_config(batch)
        if not (config.corporate_id and config.debit_account):
            raise UserError(_("Isi Corporate ID dan rekening sumber format ini untuk company/jurnal batch."))
        corporate = _efn_sanitize(config.corporate_id, self.delimiter, 20)
        debit = re.sub(r"\D", "", config.debit_account)
        return (
            None,
            [[corporate, debit, r["account"], r["amount_int"], r["holder"], r["memo"], r["date"]] for r in rows],
        )

    def _efn_render_mandiri_mcm(self, batch, rows):
        return (
            ["No", "Beneficiary Account", "Beneficiary Name", "Amount", "Remark", "Value Date"],
            [[i + 1, r["account"], r["holder"], r["amount"], r["memo"], r["date"]] for i, r in enumerate(rows)],
        )

    def _efn_render_bni(self, batch, rows):
        return (
            ["Account No", "Name", "Amount", "Description", "Date"],
            [[r["account"], r["holder"], r["amount"], r["memo"], r["date"]] for r in rows],
        )

    def _efn_render_bri(self, batch, rows):
        return (
            ["Account Number", "Recipient Name", "Amount", "Remark"],
            [[r["account"], r["holder"], r["amount"], r["memo"]] for r in rows],
        )


class EfnAccountBatchPaymentFormatCompany(models.Model):
    _name = "efn.account.batch.payment.format.company"
    _description = "EFN Konfigurasi Format Transfer per Company"
    _check_company_auto = True

    format_id = fields.Many2one("efn.account.batch.payment.format", required=True, ondelete="cascade")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    journal_id = fields.Many2one(
        "account.journal",
        check_company=True,
        domain="[('type', '=', 'bank')]",
        help="Kosong = berlaku untuk semua jurnal bank company ini.",
    )
    corporate_id = fields.Char(string="Corporate ID", help="Kode korporat yang diminta portal bank.")
    debit_account = fields.Char(string="Rekening Sumber", help="Rekening perusahaan yang didebit (badan usaha).")

    _format_company_journal_uniq = models.Constraint(
        "unique(format_id, company_id, journal_id)", "Satu konfigurasi per format, company, dan jurnal."
    )
