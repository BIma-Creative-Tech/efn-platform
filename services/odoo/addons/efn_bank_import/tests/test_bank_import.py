"""Impor mutasi bank CSV (port test_bank_import.py sumber: CSV generik + BCA korporat; kasus XLS/XLSX/H2H dibuang)
+ kasus keamanan G58 §4.5. Fixture CSV sintetis: tanpa nomor rekening, nama, atau MID nyata."""

import base64
from datetime import date
from decimal import Decimal

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.service.model import get_public_method
from odoo.tests import new_test_user, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon

GENERIC_CSV = b"date,ref,amount\n2026-05-01,REF001,1500.00\n2026-05-02,REF002,-250.00\n2026-05-03,REF003,75.50\n"

BCA_CSV = (
    b'"Informasi Rekening - Mutasi Rekening"," "," "," "," ",\n'
    b"\n"
    b'"No. rekening : 0000000000"\n'
    b'"Nama : PT CONTOH UJI"\n'
    b'"Periode : 01/06/2026 - 30/06/2026"\n'
    b'"Kode Mata Uang : Rp"\n'
    b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
    b'"01/06","BIAYA ADM       ","0000","30,000.00 DB","2,313,178.27"\n'
    b'"13/06","KR OTOMATIS SETTLEMENT UJI   ","0000","10,930,941.82 CR","13,244,120.09"\n'
    b'"Saldo Awal : 2,343,178.27"\n'
    b'"Mutasi Debet : 30,000.00","1"\n'
    b'"Mutasi Kredit : 10,930,941.82","1"\n'
    b'"Saldo Akhir : 13,244,120.09"\n'
)


def _b64(raw):
    return base64.b64encode(raw).decode()


@tagged("post_install", "-at_install", "efn")
class TestEfnBankImport(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.journal = cls.company_data["default_journal_bank"]
        cls.Template = cls.env["efn.bank.import.template"]
        cls.Wizard = cls.env["efn.bank.import.wizard"]
        cls.Log = cls.env["efn.bank.import.log"]
        cls.template = cls.Template.create(
            {
                "name": "CSV uji",
                "code": "test_csv",
                "company_id": cls.company.id,
                "delimiter": ",",
                "has_header": True,
                "date_format": "%Y-%m-%d",
                "date_column_index": 1,
                "ref_column_index": 2,
                "signed_amount_column_index": 3,
                "decimal_separator": ".",
                "thousand_separator": "",
            }
        )

    def _import(self, raw, filename="mutasi.csv", journal=None, template=None, user=None):
        Wizard = self.Wizard.with_user(user) if user else self.Wizard
        wiz = Wizard.create(
            {
                "journal_id": (journal or self.journal).id,
                "template_id": (template or self.template).id,
                "file": _b64(raw),
                "filename": filename,
            }
        )
        action = wiz.action_import()
        return self.Log.browse(action["res_id"])

    # ------------------------------------------------------------------ port sumber
    def test_csv_happy_path(self):
        log = self._import(GENERIC_CSV)
        self.assertEqual(log.state, "imported")
        self.assertEqual(log.line_count, 3)
        self.assertEqual(log.error_count, 0)
        self.assertEqual(log.company_id, self.company)
        lines = log.statement_id.line_ids
        self.assertEqual(sorted(lines.mapped("amount")), [-250.0, 75.5, 1500.0])
        self.assertTrue(all(lines.mapped("efn_import_fingerprint")))
        self.assertEqual(lines.efn_import_log_id, log)
        self.assertFalse(any(lines.mapped("account_number")), "parser tidak mengisi rekening lawan")

    def test_csv_bad_rows_graceful_without_cell_content(self):
        raw = (
            b"date,ref,amount\n"
            b"NOT-A-DATE,NAMA ORANG 1234567890,1500.00\n"
            b"2026-05-02,REF002,abc\n"
            b"2026-05-03,REF003,0\n"
            b"2026-05-04\n"
            b"2026-05-05,REF005,75.50\n"
        )
        log = self._import(raw)
        self.assertEqual(log.state, "partial")
        self.assertEqual(log.line_count, 1)
        self.assertEqual(log.error_count, 3)
        self.assertEqual(log.skipped_zero, 1)
        self.assertIn("baris 2: invalid_date", log.error_message)
        self.assertIn("baris 3: invalid_amount", log.error_message)
        self.assertIn("baris 5: missing_column", log.error_message)
        for secret in ("NOT-A-DATE", "NAMA ORANG", "1234567890", "abc"):
            self.assertNotIn(secret, log.error_message, "pesan error tidak boleh memuat isi sel")

    def test_bca_corp_autodetect(self):
        res = self.template._efn_parse(BCA_CSV)
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["lines"]), 2)
        debit, credit = sorted(res["lines"], key=lambda line: line["amount"])
        self.assertEqual(debit["amount"], Decimal("-30000.00"))
        self.assertEqual(credit["amount"], Decimal("10930941.82"))
        self.assertEqual(debit["date"], date(2026, 6, 1))
        self.assertEqual(debit["ref"], "BIAYA ADM")
        self.assertEqual(debit["balance"], Decimal("2313178.27"))

    def test_bca_corp_full_date_variant(self):
        raw = (
            b'"Informasi Rekening - Mutasi Rekening"," "," "," "," ",\n'
            b'"Periode : 01/07/2026 - 20/07/2026"\n'
            b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
            b'"01/07/2026","KR OTOMATIS UJI","0998","4,689,356.40 CR","7,273,422.02"\n'
            b'"20/07/2026","TRSF E-BANKING DB","0000","960,064,168.95 DB","2,500,000.00"\n'
            b'"Saldo Akhir : 2,500,000.00"\n'
        )
        res = self.template._efn_parse(raw)
        self.assertEqual(res["errors"], [])
        credit, debit = res["lines"]
        self.assertEqual(credit["amount"], Decimal("4689356.40"))
        self.assertEqual(debit["date"], date(2026, 7, 20))
        self.assertEqual(debit["amount"], Decimal("-960064168.95"))

    def test_bca_corp_year_rollover(self):
        raw = (
            b'"Periode : 20/12/2026 - 10/01/2027"\n'
            b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
            b'"28/12","A","0000","1.00 CR","1.00"\n'
            b'"05/01","B","0000","2.00 CR","3.00"\n'
        )
        res = self.template._efn_parse(raw)
        self.assertEqual([line["date"] for line in res["lines"]], [date(2026, 12, 28), date(2027, 1, 5)])

    def test_bca_corp_via_wizard_imports(self):
        log = self._import(BCA_CSV, filename="bca.csv")
        self.assertEqual(log.state, "imported")
        self.assertEqual(sorted(log.statement_id.line_ids.mapped("amount")), [-30000.0, 10930941.82])

    # ------------------------------------------------------------------ dedup
    def test_same_file_twice_is_refused(self):
        self._import(GENERIC_CSV)
        with self.assertRaises(UserError):
            self._import(GENERIC_CSV, filename="nama-lain.csv")

    def test_overlapping_file_skips_known_lines(self):
        first = self._import(b"date,ref,amount\n2026-05-01,A,100\n2026-05-02,B,200\n2026-05-02,B,200\n")
        self.assertEqual(first.line_count, 3, "dua baris identik dalam satu file tetap dua mutasi")
        second = self._import(b"date,ref,amount\n2026-05-02,B,200\n2026-05-02,B,200\n2026-05-03,C,300\n")
        self.assertEqual(second.line_count, 1)
        self.assertEqual(second.duplicate_count, 2)
        self.assertEqual(second.statement_id.line_ids.mapped("payment_ref"), ["C"])
        with self.assertRaises(UserError):
            self._import(b"date,ref,amount\n2026-05-01,A,100\n")

    def test_same_file_other_company_is_not_a_duplicate(self):
        other = self.setup_other_company(name="PT lain uji impor")
        self.env.user.company_ids |= other["company"]
        template_b = self.Template.create(dict(self.template.copy_data()[0], company_id=other["company"].id))
        self._import(GENERIC_CSV)
        log_b = self._import(GENERIC_CSV, journal=other["default_journal_bank"], template=template_b)
        self.assertEqual(log_b.line_count, 3)

    # ------------------------------------------------------------------ lock date (fail-closed)
    def test_lock_date_refuses_whole_file(self):
        self.company.sudo().fiscalyear_lock_date = date(2026, 5, 1)
        with self.assertRaisesRegex(UserError, "tanggal kunci"):
            self._import(GENERIC_CSV)
        self.assertFalse(self.env["account.bank.statement.line"].search([("journal_id", "=", self.journal.id)]))

    def test_hard_lock_date_refuses(self):
        self.company.sudo().hard_lock_date = date(2026, 5, 2)
        with self.assertRaises(UserError):
            self._import(GENERIC_CSV)

    # ------------------------------------------------------------------ akses & multi-company
    def test_other_company_cannot_see_log(self):
        log = self._import(GENERIC_CSV)
        other = self.setup_other_company(name="PT B uji log")
        user_b = new_test_user(
            self.env,
            login="efn_bi_user_b",
            groups="base.group_user,account.group_account_user",
            company_id=other["company"].id,
            company_ids=[Command.set(other["company"].ids)],
        )
        self.assertFalse(self.Log.with_user(user_b).search([("id", "=", log.id)]))
        self.assertFalse(self.Template.with_user(user_b).search([("id", "=", self.template.id)]))

    def test_billing_user_cannot_import(self):
        billing = new_test_user(
            self.env,
            login="efn_bi_billing",
            groups="base.group_user,account.group_account_invoice",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
        )
        wiz = self.Wizard.create(
            {"journal_id": self.journal.id, "template_id": self.template.id, "file": _b64(GENERIC_CSV)}
        )
        with self.assertRaises(AccessError):
            wiz.with_user(billing).action_import()

    def test_template_of_other_company_refused(self):
        other = self.setup_other_company(name="PT C uji template")
        self.env.user.company_ids |= other["company"]
        foreign = self.Template.create(dict(self.template.copy_data()[0], company_id=other["company"].id))
        with self.assertRaises(UserError):  # AccessError turunan UserError
            self._import(GENERIC_CSV, template=foreign)

    def test_parser_is_not_rpc_callable(self):
        for name in ("_efn_parse", "_efn_parse_generic", "_efn_parse_bca_corp", "_efn_decode"):
            with self.assertRaises(AccessError):
                get_public_method(self.template, name)
        self.assertFalse(hasattr(self.template, "parse_csv"))

    def test_log_is_immutable(self):
        log = self._import(GENERIC_CSV)
        with self.assertRaises(UserError):
            log.write({"line_count": 99})
        log.write({"active": False})
        self.assertFalse(log.active)

    # ------------------------------------------------------------------ batas & encoding
    def test_failed_import_keeps_a_log(self):
        log = self._import(b"date,ref,amount\nBUKAN,X,1\n")
        self.assertEqual(log.state, "failed")
        self.assertFalse(log.statement_id)
        self.assertIn("invalid_date", log.error_message)

    def test_size_and_row_limits(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("efn_bank_import.max_bytes", "10")
        with self.assertRaises(UserError):
            self._import(GENERIC_CSV)
        params.set_param("efn_bank_import.max_bytes", str(10 * 1024 * 1024))
        params.set_param("efn_bank_import.max_rows", "2")
        with self.assertRaises(UserError):
            self._import(GENERIC_CSV)

    def test_encoding_fallback_and_strict(self):
        raw = "date,ref,amount\n2026-05-01,CAFÉ UJI,10\n".encode("cp1252")
        log = self._import(raw)
        self.assertEqual(log.statement_id.line_ids.payment_ref, "CAFÉ UJI")
        self.template.encoding = "utf-8"
        with self.assertRaises(UserError):
            self._import("date,ref,amount\n2026-05-02,CAFÉ DUA,10\n".encode("cp1252"))
