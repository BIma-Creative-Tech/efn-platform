"""K-a (ADR-036 §0.2): work contact karyawan tidak terlindungi; context dari klien tidak bisa meniru.
E-38b kelas K: data pribadi `hr.employee`/`hr.version` terenkripsi, NIK lewat blind index, tracking tanpa nilai,
nama karyawan plaintext (18-40). Data fiktif khusus test."""

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL

from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.tests.common import KEYRING_LOGGER, V2, PiiKeyMixin
from odoo.addons.efn_pii_hr import post_init_hook

NIK = "3171 0123 4567 0001"
PRIVATE = {
    "private_phone": "0813 9000 0001",
    "private_email": "pribadi.karyawan@example.com",
    "emergency_contact": "Ibu Karyawan",
    "emergency_phone": "0813 9000 0002",
    "place_of_birth": "Kota Lahir Uji",
}
VERSION = {
    "private_street": "Jl. Rumah Karyawan 7",
    "private_city": "Kota Rumah Uji",
    "private_zip": "40111",
    "identification_id": NIK,
    "passport_id": "X1234567",
}


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


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiHrPrivateData(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Employee = self.env["hr.employee"]

    def _employee(self, name="Karyawan Pribadi"):
        return self.Employee.create({"name": name, **PRIVATE, **VERSION})

    def test_private_fields_encrypted_bound_and_readable(self):
        employee = self._employee()
        version = employee.version_id
        self.assertTrue(version)
        raw_emp = self._raw_table("hr_employee", employee.id, "name", *PRIVATE)
        self.assertEqual(raw_emp.pop("name"), "Karyawan Pribadi")  # identitas kerja plaintext (18-40)
        for column, plain in PRIVATE.items():
            self.assertBound(raw_emp[column], "hr_employee", column, employee.id, plain)
        raw_ver = self._raw_table("hr_version", version.id, *VERSION, "efn_identification_id_bidx")
        bidx = raw_ver.pop("efn_identification_id_bidx")
        self.assertRegex(bidx, r"^[0-9a-f]{64}$")
        for column, plain in VERSION.items():
            self.assertBound(raw_ver[column], "hr_version", column, version.id, plain)
        employee.invalidate_recordset()
        version.invalidate_recordset()
        self.assertEqual(employee.private_phone, PRIVATE["private_phone"])
        self.assertEqual(employee.private_street, VERSION["private_street"])
        self.assertEqual(employee.identification_id, NIK)
        # tidak ada efnpii1 tersisa
        self.assertEqual(pm.unbound_ids(self.env["hr.version"], version.ids), [])
        self.assertEqual(pm.unbound_ids(self.Employee, employee.ids), [])

    def test_search_full_nik_blind_index(self):
        employee = self._employee()
        Version = self.env["hr.version"]
        for term in (NIK, "3171012345670001", "3171-0123-4567-0001"):
            self.assertIn(employee.version_id, Version.search([("identification_id", "=", term)]), term)
            self.assertIn(employee, self.Employee.search([("identification_id", "=", term)]), term)
        self.assertNotIn(employee.version_id, Version.search([("identification_id", "ilike", "4567")]))
        self.assertNotIn(employee.version_id, Version.search([("private_city", "ilike", "Rumah")]))
        self.assertIn(employee, self.Employee.search([("name", "ilike", "pribadi")]))  # nama tetap dicari biasa

    def test_tracking_never_stores_private_values(self):
        employee = self._employee()
        employee.write(
            {
                "private_street": "Jl. Pindah Rumah 9",
                "emergency_contact": "Ayah Karyawan",
                "identification_id": "3171 0123 4567 0002",
                "job_title": "Kasir Senior",
            }
        )
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                """SELECT f.model, f.name, v.old_value_char, v.new_value_char, v.old_value_text, v.new_value_text
                     FROM mail_tracking_value v JOIN ir_model_fields f ON f.id = v.field_id
                     JOIN mail_message m ON m.id = v.mail_message_id
                    WHERE m.model IN ('hr.employee', 'hr.version')
                      AND m.res_id IN %s""",
                (employee.id, employee.version_id.id),
            )
        )
        rows = self.env.cr.fetchall()
        secrets = ("Rumah", "Pindah", "Ibu Karyawan", "Ayah", "3171", "pribadi.karyawan", "0813")
        for model, fname, *values in rows:
            for value in values:
                for secret in secrets:
                    self.assertNotIn(secret, value or "", f"{model}.{fname}")

    def test_legacy_rows_encrypted_by_hook(self):
        employee = self._employee()
        self.env.flush_all()
        self.env.cr.execute(
            SQL("UPDATE hr_employee SET private_phone = %s WHERE id = %s", "0813 9000 0999", employee.id)
        )
        self.env.cr.execute(
            SQL("UPDATE hr_version SET private_city = %s WHERE id = %s", "Kota Lama Uji", employee.version_id.id)
        )
        self.env.invalidate_all()
        post_init_hook(self.env)
        self.assertBound(
            self._raw_table("hr_employee", employee.id, "private_phone")["private_phone"],
            "hr_employee",
            "private_phone",
            employee.id,
            "0813 9000 0999",
        )
        self.assertBound(
            self._raw_table("hr_version", employee.version_id.id, "private_city")["private_city"],
            "hr_version",
            "private_city",
            employee.version_id.id,
            "Kota Lama Uji",
        )

    def test_locked_mode_refuses_private_data_but_not_employee(self):
        self.env.flush_all()
        self._pii_locked()
        employee = self.Employee.create({"name": "Tanpa Data Pribadi"})
        self.assertTrue(employee)
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"), self.assertRaises(UserError):
            employee.write({"private_phone": "0813 9000 0003"})

    def test_raw_columns_never_plaintext(self):
        employee = self._employee("Karyawan Kedua")
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT private_phone, private_email FROM hr_employee WHERE id = %s", employee.id))
        for value in self.env.cr.fetchone():
            self.assertTrue(value.startswith(V2))
