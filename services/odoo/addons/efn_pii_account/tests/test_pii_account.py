"""SEC-153 / AC-10: `account.move.invoice_partner_display_name` terenkripsi; rekening bank tetap EfnPiiChar setelah
`account` mendefinisikan ulang field (R-T18); deteksi duplikat rekening terlindungi. Data fiktif khusus test."""

from odoo.tests import tagged
from odoo.tools import SQL

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.tests.common import V2, PiiKeyMixin


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiAccount(PiiKeyMixin, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        # kunci dipasang SEBELUM AccountTestInvoicingCommon membuat partner perorangan (protect-by-default)
        cls._pii_class_env()
        super().setUpClass()

    def _display_raw(self, move):
        return self._raw_table("account_move", move.id, "invoice_partner_display_name")["invoice_partner_display_name"]

    def test_invoice_display_name_encrypted_and_searchable(self):
        buyer = self.env["res.partner"].create({"name": "Budi Faktur"})
        move = self.init_invoice("out_invoice", partner=buyer, amounts=[100.0])
        self.assertTrue(move.sudo().efn_pii_protected)
        self.assertBound(
            self._display_raw(move), "account_move", "invoice_partner_display_name", move.id, "Budi Faktur"
        )
        move.invalidate_recordset()
        self.assertEqual(move.invoice_partner_display_name, "Budi Faktur")
        Move = self.env["account.move"]
        for domain in (
            [("invoice_partner_display_name", "ilike", "budi fak")],
            [("invoice_partner_display_name", "=", "Budi Faktur")],
            [("partner_id", "ilike", "faktur")],
        ):
            self.assertEqual(Move.search([*domain, ("id", "=", move.id)]), move, domain)
        self.assertFalse(Move.search([("invoice_partner_display_name", "ilike", "aktur"), ("id", "=", move.id)]))
        # partner perusahaan: standar (plaintext)
        company = self.env["res.partner"].create({"name": "PT Pembeli Grosir", "is_company": True})
        plain = self.init_invoice("out_invoice", partner=company, amounts=[50.0])
        self.assertFalse(plain.sudo().efn_pii_protected)
        self.assertEqual(self._display_raw(plain), "PT Pembeli Grosir")
        self.assertIn(plain, Move.search([("invoice_partner_display_name", "ilike", "grosir")]))

    def test_existing_invoices_encrypted_when_partner_becomes_protected(self):
        Server = self.env["res.partner"].with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN})
        person = Server.create({"name": "Siti Lama"})
        move = self.init_invoice("out_invoice", partner=person, amounts=[10.0])
        self.assertEqual(self._display_raw(move), "Siti Lama")
        person.write({"efn_pii_protected": True})
        self.assertTrue(move.sudo().efn_pii_protected)
        self.assertBound(self._display_raw(move), "account_move", "invoice_partner_display_name", move.id, "Siti Lama")

    def test_bank_fields_keep_encryption_after_account_redefinition(self):
        Bank = self.env["res.partner.bank"]
        self.assertFalse(pm.broken_fields(Bank))
        for fname in ("acc_number", "acc_holder_name"):
            self.assertTrue(getattr(Bank._fields[fname], "efn_pii_encrypted", False), fname)
            self.assertTrue(Bank._fields[fname].tracking, fname)
        self.assertFalse(pm.broken_fields(self.env["account.move"]))

    def test_protected_bank_duplicates_and_tracking(self):
        one = self.env["res.partner"].create({"name": "Pemilik Satu"})
        two = self.env["res.partner"].create({"name": "Pemilik Dua"})
        Bank = self.env["res.partner.bank"]
        bank_one = Bank.create({"acc_number": "4400 1122 33", "partner_id": one.id})
        bank_two = Bank.create({"acc_number": "4400112233", "partner_id": two.id})
        self.assertTrue(bank_one.efn_pii_protected and bank_two.efn_pii_protected)
        self.assertIn(two, bank_one.duplicate_bank_partner_ids)
        bank_one.write({"acc_number": "4400 1122 99"})
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                """SELECT count(*) FROM mail_tracking_value v JOIN mail_message m ON m.id = v.mail_message_id
                    WHERE m.model = 'res.partner.bank' AND m.res_id = %s
                      AND (v.old_value_char LIKE %s OR v.new_value_char LIKE %s
                           OR v.old_value_char LIKE %s OR v.new_value_char LIKE %s)""",
                bank_one.id,
                "%4400%",
                "%4400%",
                "%Pemilik%",
                "%Pemilik%",
            )
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        self.assertTrue(self._raw_table("res_partner_bank", bank_one.id, "acc_number")["acc_number"].startswith(V2))
