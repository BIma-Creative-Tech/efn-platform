"""AC-3 / 18-8 (ADR-036 §0.8): NIK & nomor dokumen pembeli terenkripsi + blind index; XML Coretax tersimpan efnpii2,
isinya identik dengan render langsung (lengkap, plaintext saat diunduh user Accounting), tanpa lampiran chatter.
Data fiktif khusus test. Pola setup mengikuti test CE `l10n_id_efaktur_coretax` 19.0 (`setup_country('id')`)."""

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tools import SQL

from odoo.addons.account.tests.common import AccountTestInvoicingCommon, AccountTestInvoicingHttpCommon
from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.tests.common import KEYRING_LOGGER, PiiKeyMixin
from odoo.addons.efn_pii_l10n_id.models.ir_attachment import CORETAX_MODEL, _internal

NIK = "3171012345670009"
DOC_NUMBER = "A1234567"
BUYER = "Budi Coretax"
STREET = "Jl. Faktur Pajak 12"
NPWP = "1234567890123457"


class EfnCoretaxCommon(PiiKeyMixin):
    @classmethod
    def _efn_setup_company(cls):
        company = cls.company_data["company"]
        company.street = "Jl. Perusahaan 1"
        company.phone = "12345"
        company.vat = "1234567890123456"
        if "efn_tax_rounding_account_id" in company._fields:
            # efn_l10n_id_coretax_rounding terpasang (run gabungan): XML Coretax mensyaratkan akun & jurnal selisih
            # pembulatan (ketat, perilaku benar). Fixture memakai akun beban + jurnal Misc company uji.
            company.write(
                {
                    "efn_tax_rounding_account_id": cls.company_data["default_account_expense"].id,
                    "efn_tax_rounding_journal_id": cls.company_data["default_journal_misc"].id,
                }
            )
        cls.tax_sale_a.amount = 11.0
        cls.buyer = cls.env["res.partner"].create(
            {
                "name": BUYER,
                "street": STREET,
                "email": "budi.coretax@example.com",
                "vat": NPWP,
                "l10n_id_pkp": True,
                "l10n_id_kode_transaksi": "04",
                "l10n_id_nik": NIK,
                "country_id": cls.env.ref("base.id").id,
            }
        )

    def _invoice(self, partner=None):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": (partner or self.buyer).id,
                "invoice_date": "2026-10-01",
                "date": "2026-10-01",
                "invoice_line_ids": [
                    Command.create({"name": "line1", "price_unit": 110.0, "tax_ids": self.tax_sale_a.ids})
                ],
                "l10n_id_kode_transaksi": "01",
            }
        )
        invoice.action_post()
        return invoice


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiCoretax(EfnCoretaxCommon, AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("id")
    def setUpClass(cls):
        cls._pii_class_env()
        super().setUpClass()
        cls._efn_setup_company()

    def test_nik_and_document_number_encrypted_with_blind_index(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Pembeli NIK",
                "l10n_id_buyer_document_type": "Passport",
                "l10n_id_buyer_document_number": DOC_NUMBER,
                "l10n_id_nik": "3171 0123 4567 0010",
            }
        )
        self.assertTrue(partner.sudo().efn_pii_protected)
        raw = self._raw(
            partner.id,
            "l10n_id_nik",
            "l10n_id_buyer_document_number",
            "efn_l10n_id_nik_bidx",
            "efn_l10n_id_buyer_document_number_bidx",
        )
        self.assertBound(raw["l10n_id_nik"], "res_partner", "l10n_id_nik", partner.id, "3171 0123 4567 0010")
        self.assertBound(
            raw["l10n_id_buyer_document_number"], "res_partner", "l10n_id_buyer_document_number", partner.id, DOC_NUMBER
        )
        for column in ("efn_l10n_id_nik_bidx", "efn_l10n_id_buyer_document_number_bidx"):
            self.assertRegex(raw[column], r"^[0-9a-f]{64}$")
        Partner = self.env["res.partner"]
        self.assertIn(partner, Partner.search([("l10n_id_nik", "=", "3171012345670010")]))
        self.assertIn(partner, Partner.search([("l10n_id_buyer_document_number", "=", "a1234567")]))
        self.assertIn(partner, Partner.search([("l10n_id_buyer_document_number", "ilike", DOC_NUMBER)]))
        self.assertNotIn(partner, Partner.search([("l10n_id_nik", "ilike", "0123")]))
        partner.invalidate_recordset()
        self.assertEqual(partner.l10n_id_buyer_document_number, DOC_NUMBER)

    def test_xml_stored_encrypted_and_identical_to_render(self):
        invoice = self._invoice()
        with freeze_time("2026-10-02 08:00:00"):
            invoice.download_efaktur()
        document = invoice.l10n_id_coretax_document
        attachment = document.attachment_id
        self.assertTrue(attachment)
        stored = attachment.sudo().raw
        self.assertTrue(stored.startswith(b"efnpii2:"))
        for plain in (BUYER.encode(), STREET.encode(), NPWP.encode(), b"<TaxInvoice"):
            self.assertNotIn(plain, stored)
        plain = attachment._efn_coretax_plain_raw()
        with freeze_time("2026-10-02 08:00:00"):
            expected = document._generate_efaktur_invoice()
        self.assertEqual(plain, expected)  # AC-3: isi identik dengan render ORM langsung (lengkap)
        for value in (BUYER.encode(), STREET.encode(), NPWP.encode()):
            self.assertIn(value, plain)
        # salinan di chatter tidak dibuat; pesan tetap ada
        copies = (
            self.env["ir.attachment"]
            .sudo()
            .search([("res_model", "=", CORETAX_MODEL), ("res_id", "=", document.id), ("id", "!=", attachment.id)])
        )
        self.assertFalse(copies)
        self.assertFalse(document.message_ids.attachment_ids)
        # regenerasi: tetap terenkripsi, isi tetap benar
        with freeze_time("2026-10-03 08:00:00"):
            document.action_regenerate()
            expected = document._generate_efaktur_invoice()
        self.assertTrue(attachment.sudo().raw.startswith(b"efnpii2:"))
        self.assertEqual(attachment._efn_coretax_plain_raw(), expected)

    def test_ciphertext_bound_to_attachment_and_forgery_rejected(self):
        invoice = self._invoice()
        invoice.download_efaktur()
        attachment = invoice.l10n_id_coretax_document.attachment_id
        other = self.env["ir.attachment"].create({"name": "lain.txt", "raw": b"lain", "res_model": "res.partner"})
        ring = kr.get_keyring(self.env.cr.dbname)
        with self.assertRaises(kr.PiiError):
            ring.decrypt_bytes(attachment.sudo().raw, kr.aad_for("ir_attachment", "raw", other.id))
        with self.assertRaises(ValidationError):
            self.env["ir.attachment"].create(
                {"name": "palsu.xml", "raw": attachment.sudo().raw, "res_model": CORETAX_MODEL, "res_id": 1}
            )

    def test_legacy_plaintext_attachment_encrypted(self):
        invoice = self._invoice()
        invoice.download_efaktur()
        attachment = invoice.l10n_id_coretax_document.attachment_id
        plain = attachment._efn_coretax_plain_raw()
        # simulasi lampiran lama: isi plaintext ditulis melewati modul ini
        with _internal(self.env.cr):
            attachment.sudo().write({"raw": plain})
        self.assertFalse(attachment.sudo().raw.startswith(b"efnpii"))
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"):
            self.assertEqual(attachment._efn_coretax_plain_raw(), plain)  # tetap tersaji + alarm
        self.assertEqual(self.env["ir.attachment"].sudo()._efn_coretax_encrypt_legacy(), 1)
        self.assertTrue(attachment.sudo().raw.startswith(b"efnpii2:"))
        self.assertEqual(attachment._efn_coretax_plain_raw(), plain)
        # index_content (teks yang diindeks Odoo) tidak memuat plaintext
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT index_content FROM ir_attachment WHERE id = %s", attachment.id))
        self.assertNotIn(BUYER, self.env.cr.fetchone()[0] or "")


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiCoretaxDownload(EfnCoretaxCommon, AccountTestInvoicingHttpCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("id")
    def setUpClass(cls):
        cls._pii_class_env()
        super().setUpClass()
        cls._efn_setup_company()

    def test_download_returns_plain_xml_for_accounting_user(self):
        invoice = self._invoice()
        action = invoice.download_efaktur()
        attachment = invoice.l10n_id_coretax_document.attachment_id
        expected = attachment._efn_coretax_plain_raw()
        self.authenticate(self.env.user.login, self.env.user.login)
        response = self.url_open(action["url"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)
        self.assertIn(BUYER.encode(), response.content)
        self.assertRegex(response.headers.get("Content-Disposition", ""), r"efaktur_.*\.xml")
