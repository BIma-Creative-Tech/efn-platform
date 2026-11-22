"""Wizard impor mutasi bank CSV (port ADR-049 dari `custom_bank_import/wizard/bank_import_csv_wizard.py`).

Perubahan wajib (G58 §4.2):
1. **Lock date fail-closed**: tolak seluruh impor bila ada tanggal <= `fiscalyear_lock_date` atau `hard_lock_date`
   company jurnal. Sumber menulis ulang tanggal ke periode soft-lock dengan `bypass_lock_check` (melewati tutup buku).
2. **Dedup per company dan per baris**: hash file dicari hanya di company jurnal (sumber: sudo lintas company);
   sidik baris `efn_import_fingerprint` -> mutasi tumpang tindih antar file tidak tergandakan.
3. Ukuran maks 10 MB (`efn_bank_import.max_bytes`) & baris maks 20.000 (`efn_bank_import.max_rows`).
4. Akses dicek SEBELUM `sudo()` apa pun: grup `account.group_account_user`, hak baca jurnal + hak buat baris mutasi,
   company jurnal aktif di sesi, template milik company jurnal. Log ditulis sudo setelah cek.
   (`check_access('write')` pada `account.journal` tidak dipakai: ACL CE memberi write jurnal hanya ke Administrator,
   sehingga Bookkeeper tidak akan pernah lolos; yang dilindungi adalah pembuatan baris mutasi di jurnal itu.)
Log & pesan tanpa isi sel/narasi; file asli tidak disimpan (hanya hash).
"""

import base64
import hashlib
import logging

from odoo import Command, _, fields, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

IMPORT_GROUP = "account.group_account_user"
MAX_BYTES_PARAM = "efn_bank_import.max_bytes"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_ERROR_LINES = 200


def _fingerprint(journal_id, line, occurrence):
    balance = "" if line["balance"] is None else str(line["balance"])
    raw = "|".join(
        (str(journal_id), line["date"].isoformat(), str(line["amount"]), line["ref"] or "", balance, str(occurrence))
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EfnBankImportWizard(models.TransientModel):
    _name = "efn.bank.import.wizard"
    _description = "EFN Wizard Impor Mutasi Bank"
    _check_company_auto = True

    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        domain="[('type', '=', 'bank')]",
    )
    company_id = fields.Many2one(related="journal_id.company_id")
    template_id = fields.Many2one(
        "efn.bank.import.template",
        required=True,
        check_company=True,
        domain="[('company_id', '=', company_id)]",
    )
    file = fields.Binary(string="File Mutasi (CSV)", required=True)
    filename = fields.Char()
    statement_name = fields.Char(string="Nama Rekening Koran")

    def _efn_check_access(self):
        journal = self.journal_id
        if not self.env.user.has_group(IMPORT_GROUP):
            raise AccessError(_("Hanya Accounting / Bookkeeper yang dapat mengimpor mutasi bank."))
        journal.check_access("read")
        self.env["account.bank.statement.line"].check_access("create")
        if journal.company_id not in self.env.companies:
            raise AccessError(_("Jurnal milik company yang tidak aktif di sesi Anda."))
        if journal.type != "bank":
            raise UserError(_("Pilih jurnal bertipe Bank."))
        self.template_id.check_access("read")
        if self.template_id.company_id != journal.company_id:
            raise UserError(_("Template mutasi harus milik company yang sama dengan jurnal."))

    def _efn_check_lock_dates(self, lines):
        company = self.journal_id.company_id
        locks = [d for d in (company.fiscalyear_lock_date, company.hard_lock_date) if d]
        if not locks:
            return
        lock = max(locks)
        violating = sorted({line["date"] for line in lines if line["date"] <= lock})
        if violating:
            raise UserError(
                _(
                    "File memuat %(count)s tanggal transaksi hingga %(worst)s, pada atau sebelum tanggal kunci "
                    "(%(lock)s). Periode itu sudah ditutup: buka tanggal kunci (Finance) atau potong file, lalu impor "
                    "ulang.",
                    count=len(violating),
                    worst=violating[-1],
                    lock=lock,
                )
            )

    def _efn_action_open_log(self, log):
        return {
            "type": "ir.actions.act_window",
            "res_model": "efn.bank.import.log",
            "res_id": log.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_import(self):
        self.ensure_one()
        self._efn_check_access()
        journal = self.journal_id
        company = journal.company_id
        raw = base64.b64decode(self.file or b"")
        if not raw:
            raise UserError(_("Unggah file mutasi terlebih dahulu."))
        max_bytes = int(self.env["ir.config_parameter"].sudo().get_param(MAX_BYTES_PARAM, DEFAULT_MAX_BYTES))
        if len(raw) > max_bytes:
            raise UserError(_("Ukuran file melebihi batas %s MB. Potong file per periode.", max_bytes // (1024 * 1024)))
        file_hash = hashlib.sha256(raw).hexdigest()
        Log = self.env["efn.bank.import.log"].sudo()
        existing = Log.search(
            [("file_hash", "=", file_hash), ("company_id", "=", company.id), ("state", "in", ("imported", "partial"))],
            limit=1,
        )
        if existing:
            raise UserError(
                _(
                    "File yang sama persis sudah pernah diimpor (log %s). Arsipkan log itu bila memang ingin "
                    "mengulang.",
                    existing.name,
                )
            )

        parsed = self.template_id._efn_parse(raw)
        lines, errors = parsed["lines"], parsed["errors"]
        error_message = "\n".join(f"baris {n}: {code}" for n, code in errors[:MAX_ERROR_LINES]) or False
        base_log = {
            "journal_id": journal.id,
            "template_id": self.template_id.id,
            "filename": self.filename,
            "file_hash": file_hash,
            "imported_by_id": self.env.uid,
            "skipped_zero": parsed["skipped_zero"],
            "error_count": len(errors),
            "error_message": error_message,
        }
        if not lines:
            log = Log.create(dict(base_log, state="failed", error_message=error_message or _("Tidak ada baris valid.")))
            _logger.info("EFN bank import: log %s gagal, %s baris error", log.id, len(errors))
            return self._efn_action_open_log(log)

        self._efn_check_lock_dates(lines)

        occurrences = {}
        fingerprints = []
        for line in lines:
            key = (line["date"], line["amount"], line["ref"], line["balance"])
            occurrences[key] = occurrences.get(key, 0) + 1
            fingerprints.append(_fingerprint(journal.id, line, occurrences[key]))
        known = set(
            self.env["account.bank.statement.line"]
            .sudo()
            .search([("journal_id", "=", journal.id), ("efn_import_fingerprint", "in", fingerprints)])
            .mapped("efn_import_fingerprint")
        )
        new = [(line, fp) for line, fp in zip(lines, fingerprints, strict=True) if fp not in known]
        if not new:
            raise UserError(_("Semua %s baris di file ini sudah pernah diimpor ke jurnal ini.", len(lines)))

        log = Log.create(
            dict(
                base_log,
                state="partial" if errors else "imported",
                line_count=len(new),
                duplicate_count=len(lines) - len(new),
            )
        )
        statement = self.env["account.bank.statement"].create(
            {
                "name": self.statement_name or self.filename or _("Impor mutasi"),
                "line_ids": [
                    Command.create(
                        {
                            "journal_id": journal.id,
                            "date": line["date"],
                            "payment_ref": (line["ref"] or "/")[:255],
                            "amount": float(line["amount"]),
                            "efn_import_fingerprint": fp,
                            "efn_import_log_id": log.id,
                        }
                    )
                    for line, fp in new
                ],
            }
        )
        log.with_context(efn_bank_import_internal=True).write({"statement_id": statement.id})
        _logger.info(
            "EFN bank import: log %s, %s baris, %s duplikat, %s error",
            log.id,
            len(new),
            len(lines) - len(new),
            len(errors),
        )
        return self._efn_action_open_log(log)
