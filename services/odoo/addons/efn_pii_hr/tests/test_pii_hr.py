"""K-a (ADR-036 §0.2): work contact karyawan tidak terlindungi; context dari klien tidak bisa meniru."""

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_pii.tests.common import PiiKeyMixin


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiHrWorkContact(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._pii_env()

    def test_work_contact_is_unprotected_identity(self):
        employee = self.env["hr.employee"].create({"name": "Kasir Uji HR", "work_email": "kasir.hr@example.com"})
        contact = employee.work_contact_id
        self.assertTrue(contact)
        self.assertFalse(contact.sudo().efn_pii_protected)
        self.assertEqual(self._raw(contact.id, "name")["name"], "Kasir Uji HR")
        # partner perorangan lain di transaksi yang sama tetap terlindungi (context tidak bocor)
        other = self.env["res.partner"].create({"name": "Pelanggan HR"})
        self.assertTrue(other.sudo().efn_pii_protected)

    def test_client_context_string_does_not_skip(self):
        partner = (
            self.env["res.partner"]
            .with_context(efn_pii_skip_default="efn_pii_skip_default")
            .create({"name": "Tiruan Klien"})
        )
        self.assertTrue(partner.sudo().efn_pii_protected)
