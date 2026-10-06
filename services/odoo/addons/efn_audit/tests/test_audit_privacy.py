"""SEC-45 (minimisasi PII di log audit) dan SEC-46 (company lama) — review r4.

NPWP/NIK/HP/email di bawah fiktif khusus test (bukan data pribadi nyata).
"""

from odoo import Command
from odoo.tests import tagged

from odoo.addons.efn_audit.models.audit_policy import is_pii_field, mask_identifier, mask_person_name

from .common import EfnAuditCase

NIK_OLD = "3171012345678901"
NIK_NEW = "3171019876543210"


@tagged("post_install", "-at_install", "efn")
class TestEfnAuditPrivacy(EfnAuditCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "PT Privasi B"})
        Users = cls.env["res.users"].with_context(no_reset_password=True)
        base_user = cls.env.ref("base.group_user").id
        cls.auditor_a = Users.create(
            {
                "name": "Auditor Privasi A",
                "login": "auditor.privasi.a",
                "company_id": cls.company_a.id,
                "company_ids": [Command.set(cls.company_a.ids)],
                "group_ids": [Command.set([base_user, cls.env.ref("efn_audit.group_auditor").id])],
            }
        )
        cls.auditor_personal = Users.create(
            {
                "name": "Auditor Holding",
                "login": "auditor.privasi.holding",
                "company_id": cls.company_a.id,
                "company_ids": [Command.set(cls.company_a.ids)],
                "group_ids": [Command.set([base_user, cls.env.ref("efn_audit.group_auditor_personal").id])],
            }
        )

    # ----------------------------------------------------------- masking
    def test_policy_helpers(self):
        for fname in ("phone", "mobile", "email", "vat", "l10n_id_nik", "npwp", "x_ktp", "acc_number", "passport_id"):
            self.assertTrue(is_pii_field(fname), fname)
        for fname in ("name", "list_price", "unique_code", "nickname_x"):
            self.assertFalse(is_pii_field(fname), fname)
        self.assertEqual(mask_identifier(NIK_OLD), "****901")
        self.assertEqual(mask_identifier("12345"), "****(5)")
        self.assertEqual(mask_person_name("Budi  Santoso"), "B*** S***")

    def test_pii_masked_even_if_rule_does_not_select_it(self):
        # Aturan admin melacak vat/phone/email/name TANPA masked_field_ids.
        self._set_rule("res.partner", tracked=("name", "vat", "phone", "email"), masked=())
        # no_vat_validation: base_vat (l10n_id) memvalidasi format; yang diuji di sini masking.
        person = (
            self.env["res.partner"]
            .with_context(no_vat_validation=True)
            .create({"name": "Siti Aminah", "vat": NIK_OLD, "phone": "0812-3456-7890", "email": "siti.uji@example.com"})
        )
        person.write(
            {"name": "Siti Rahma", "vat": NIK_NEW, "phone": "0813-1111-2222", "email": "siti.baru@example.com"}
        )
        log = self._logs(person, "write")
        self.assertEqual(log.res_name, f"res.partner,{person.id}", "nama tidak disimpan di header")
        self.assertTrue(log.is_personal)
        vat = self._line(log, "vat")
        self.assertTrue(vat.is_masked)
        self.assertEqual((vat.old_value, vat.new_value), ("****901", "****210"))
        self.assertEqual(self._line(log, "phone").new_value, "62813****222")
        self.assertEqual(self._line(log, "email").new_value, "s***@example.com")
        name = self._line(log, "name")
        self.assertTrue(name.is_masked)
        self.assertEqual((name.old_value, name.new_value), ("S*** A***", "S*** R***"))
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT count(*) FROM efn_audit_log_line l JOIN efn_audit_log g ON g.id = l.log_id"
            " WHERE g.id = %s AND (concat(l.old_value, l.new_value, g.res_name) ~ %s)",
            [log.id, "3171|Aminah|Rahma|siti.baru|34567|11112222"],
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0, "nilai lengkap NIK/nama/HP/email tidak boleh ada di DB")

    def test_company_partner_name_kept(self):
        self._set_rule("res.partner", tracked=("name",), masked=())
        supplier = self.env["res.partner"].create({"name": "PT Pemasok Uji", "is_company": True})
        supplier.write({"name": "PT Pemasok Uji Baru"})
        line = self._line(self._logs(supplier, "write"), "name")
        self.assertFalse(line.is_masked)
        self.assertEqual(line.new_value, "PT Pemasok Uji Baru")

    # ------------------------------------------------------- visibilitas
    def test_personal_log_without_company_only_for_cross_company_auditor(self):
        self._set_rule("res.partner", tracked=("name",), masked=())
        customer = self.env["res.partner"].create({"name": "Pelanggan Tanpa PT"})
        customer.write({"name": "Pelanggan Tanpa PT 2"})
        log = self._logs(customer, "write")
        self.assertFalse(log.company_id)
        self.assertTrue(log.is_personal)
        Log = self.env["efn.audit.log"]
        Line = self.env["efn.audit.log.line"]
        self.assertFalse(Log.with_user(self.auditor_a).search([("id", "=", log.id)]))
        self.assertFalse(Line.with_user(self.auditor_a).search([("log_id", "=", log.id)]))
        self.assertEqual(Log.with_user(self.auditor_personal).search([("id", "=", log.id)]), log)
        self.assertTrue(Line.with_user(self.auditor_personal).search([("log_id", "=", log.id)]))
        # Log kontrol tanpa company (bukan data pribadi) tetap terlihat oleh auditor biasa.
        rule_log = self.Log.search([("model_name", "=", "efn.audit.rule")], limit=1)
        self.assertTrue(rule_log)
        self.assertFalse(rule_log.is_personal)
        self.assertEqual(Log.with_user(self.auditor_a).search([("id", "=", rule_log.id)]), rule_log)
        # Admin (Manager) tidak otomatis menjadi auditor data pribadi lintas PT.
        self.assertFalse(self.env.ref("base.user_admin").has_group("efn_audit.group_auditor_personal"))

    def test_moved_record_visible_to_previous_company(self):
        """SEC-46: record dipindah PT A -> PT B tetap terlihat oleh auditor PT A."""
        self._set_rule("res.partner.category", tracked=("name",))
        self._set_rule("res.partner", tracked=("name", "company_id"), masked=())
        partner = self.env["res.partner"].create(
            {"name": "PT Pindah", "is_company": True, "company_id": self.company_a.id}
        )
        partner.write({"company_id": self.company_b.id})
        log = self._logs(partner, "write")
        self.assertEqual((log.company_id, log.prev_company_id), (self.company_b, self.company_a))
        self.assertEqual(self.env["efn.audit.log"].with_user(self.auditor_a).search([("id", "=", log.id)]), log)
        self.assertTrue(self.env["efn.audit.log.line"].with_user(self.auditor_a).search([("log_id", "=", log.id)]))
        # Write tanpa company_id: prev_company kosong (tanpa read tambahan).
        partner.write({"name": "PT Pindah 2"})
        latest = self._logs(partner, "write").sorted("id")[-1]
        self.assertFalse(latest.prev_company_id)
