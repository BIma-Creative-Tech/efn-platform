"""R3: konfigurasi tanpa egress internet (cron standar ke layanan Odoo S.A. nonaktif)."""

from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "efn")
class TestNoEgress(TransactionCase):
    def test_external_crons_inactive(self):
        for xmlid in ("base_vat.vies_iap_check_update", "mail.ir_cron_module_update_notification"):
            with self.subTest(cron=xmlid):
                cron = self.env.ref(xmlid, raise_if_not_found=False)
                self.assertTrue(cron, f"{xmlid} tidak ditemukan (cek nama di versi Odoo)")
                self.assertFalse(cron.active)

    def test_efn_companies_without_vies(self):
        for xmlid in ("company_efn", "company_rtl", "company_fnb", "company_bcr", "company_bcf", "company_farm"):
            with self.subTest(company=xmlid):
                company = self.env.ref(f"efn_setup_company.{xmlid}")
                self.assertFalse(company.vat_check_vies)

    def test_vies_not_called_on_partner_vat(self):
        # Tanpa company ber-vat_check_vies, mengisi NPWP partner tidak memanggil VIES (IAP).
        if self.env["res.company"].sudo().search_count([("vat_check_vies", "=", True)]):
            self.skipTest("ada company dengan vat_check_vies aktif di DB uji")
        with patch("odoo.addons.base_vat.models.res_partner.requests.post") as post:
            partner = self.env["res.partner"].create({"name": "PT Uji NPWP"})
            partner._compute_vies_valid()
        self.assertFalse(partner.vies_valid)
        post.assert_not_called()
