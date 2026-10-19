"""G21 (docs/04 §13.2): SEC-149 padding deterministik, SEC-148 jendela create, SEC-153 residu mail (merge log,
`mail.mail` penerima), E-38b rekening orang (Q-PII9) dan login portal opak (Q-PII8). Data fiktif khusus test."""

import re

from psycopg2 import IntegrityError

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import SQL, mute_logger

from odoo.addons.efn_pii.models import fields_pii as fp
from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.tools import name_tokens as nt

from .common import V2, PiiKeyMixin

OPAQUE_RE = re.compile(r"^vnd-[2-9A-HJ-NP-Z]{6}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiG21Core(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"]

    def _tokens(self, partner):
        raw = self._raw(partner.id, "efn_name_tokens")["efn_name_tokens"]
        return raw.split(" ") if raw else []

    def _real_tokens(self, name, complete_name=None):
        ring = kr.get_keyring(self.env.cr.dbname)
        inputs = nt.record_inputs(name, complete_name or name)
        return {ring.name_token(kind, value) for kind, values in inputs.items() for value in values}

    # ----------------------------------------------------------------- SEC-149
    def test_token_padding_stable_across_recompute(self):
        partner = self.Partner.create({"name": "Budi Santoso"})
        before = self._tokens(partner)
        self.assertEqual(len(before) % 16, 0)
        partner.write({"name": "Budi Santoso"})  # sinkron member / impor: nilai sama
        self.env.add_to_compute(self.Partner._fields["efn_name_tokens"], partner)
        partner.flush_recordset()
        self.assertEqual(self._tokens(partner), before)
        self.Partner.sudo()._efn_pii_migrate(ids=partner.ids)
        self.assertEqual(self._tokens(partner), before)
        # ganti nama: padding berganti total (irisan = token asli bersama saja)
        partner.write({"name": "Budi Hartono"})
        after = self._tokens(partner)
        shared_real = self._real_tokens("Budi Santoso") & self._real_tokens("Budi Hartono")
        self.assertEqual(set(before) & set(after), shared_real)
        # dua record dengan nama sama: padding berbeda (seed id)
        twin = self.Partner.create({"name": "Budi Hartono"})
        real = self._real_tokens("Budi Hartono")
        self.assertEqual(set(after) & set(self._tokens(twin)), real)

    # ----------------------------------------------------------------- SEC-148
    def test_created_window_released_after_bind(self):
        partner = self.Partner.create({"name": "Jendela Create", "phone": "0812 6300 0001"})
        self.assertFalse(fp.is_created(self.env, "res_partner", partner.id))
        self.assertTrue(self._raw(partner.id, "phone")["phone"].startswith(V2))

    # ----------------------------------------------------------------- SEC-153 merge log
    def test_merge_log_without_names_or_emails(self):
        dst = self.Partner.create({"name": "Rina Tujuan", "email": "rina.tujuan@example.com"})
        src = self.Partner.create({"name": "Rina Sumber", "email": "rina.sumber@example.com"})
        self.env["base.partner.merge.automatic.wizard"]._merge([src.id, dst.id], dst_partner=dst, extra_checks=False)
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT body FROM mail_message WHERE model = 'res.partner' AND res_id = %s", dst.id))
        bodies = " ".join(str(row[0] or "") for row in self.env.cr.fetchall())
        self.assertIn(f"ID {src.id}", bodies)
        for plain in ("Rina Sumber", "rina.sumber@example.com", "Rina Tujuan"):
            self.assertNotIn(plain, bodies)

    def test_merge_log_unchanged_for_companies(self):
        dst = self.Partner.create({"name": "PT Tujuan Biasa", "is_company": True})
        src = self.Partner.create({"name": "PT Sumber Biasa", "is_company": True, "email": "pt.sumber@example.com"})
        self.env["base.partner.merge.automatic.wizard"]._merge([src.id, dst.id], dst_partner=dst, extra_checks=False)
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT body FROM mail_message WHERE model = 'res.partner' AND res_id = %s", dst.id))
        bodies = " ".join(str(row[0] or "") for row in self.env.cr.fetchall())
        self.assertIn("PT Sumber Biasa", bodies)  # perilaku standar mail untuk partner tidak terlindungi

    # ----------------------------------------------------------------- SEC-153 mail.mail
    def _mail(self, **vals):
        values = {"subject": "Uji", "body_html": "<p>uji</p>"}
        values.update(vals)
        return self.env["mail.mail"].sudo().create(values)

    def test_mail_mail_recipients_encrypted_for_protected(self):
        member = self.Partner.create({"name": "Member Mail", "email": "member.mail@example.com"})
        by_partner = self._mail(email_to="member.mail@example.com", recipient_ids=[(4, member.id)])
        by_email = self._mail(email_to="Member <MEMBER.MAIL@example.com>", email_cc="member.mail@example.com")
        by_record = self._mail(email_to="lain@example.com", model="res.partner", res_id=member.id)
        for mail in (by_partner, by_email, by_record):
            self.assertTrue(mail.efn_pii_protected)
            raw = self._raw_table("mail_mail", mail.id, "email_to", "email_cc")
            self.assertBound(raw["email_to"], "mail_mail", "email_to", mail.id)
            if raw["email_cc"]:
                self.assertBound(raw["email_cc"], "mail_mail", "email_cc", mail.id)
            self.assertNotIn("member.mail", raw["email_to"].lower())
        by_email.invalidate_recordset()
        self.assertEqual(by_email.email_to, "Member <MEMBER.MAIL@example.com>")
        # penerima tidak terlindungi: standar (plaintext)
        plain = self._mail(email_to="pt.biasa@example.com")
        self.assertFalse(plain.efn_pii_protected)
        self.assertEqual(self._raw_table("mail_mail", plain.id, "email_to")["email_to"], "pt.biasa@example.com")
        with self.assertRaises(UserError):
            by_partner.write({"efn_pii_protected": False})

    def test_mail_mail_queued_before_partner_protected(self):
        Server = self.Partner.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN})
        person = Server.create({"name": "Belum Terlindungi", "email": "belum@example.com"})
        mail = self._mail(email_to="belum@example.com", recipient_ids=[(4, person.id)])
        self.assertFalse(mail.efn_pii_protected)
        person.write({"efn_pii_protected": True})
        self.assertTrue(mail.efn_pii_protected)
        raw = self._raw_table("mail_mail", mail.id, "email_to")["email_to"]
        self.assertBound(raw, "mail_mail", "email_to", mail.id, "belum@example.com")

    # ----------------------------------------------------------------- E-38b rekening (Q-PII9)
    def test_person_bank_account_encrypted_with_blind_index(self):
        person = self.Partner.create({"name": "Vendor Perorangan"})  # vendor perorangan = individu (kelas V)
        bank = self.env["res.partner.bank"].create({"acc_number": "1234 5678 90", "partner_id": person.id})
        self.assertTrue(bank.efn_pii_protected)
        raw = self._raw_table("res_partner_bank", bank.id, "acc_number", "acc_holder_name", "sanitized_acc_number")
        self.assertBound(raw["acc_number"], "res_partner_bank", "acc_number", bank.id, "1234 5678 90")
        if raw["acc_holder_name"]:
            self.assertBound(raw["acc_holder_name"], "res_partner_bank", "acc_holder_name", bank.id)
        self.assertRegex(raw["sanitized_acc_number"], HEX64)
        self.assertNotIn("1234567890", raw["sanitized_acc_number"])
        bank.invalidate_recordset()
        self.assertEqual(bank.acc_number, "1234 5678 90")
        Bank = self.env["res.partner.bank"]
        for term in ("1234567890", "1234 5678 90", "12-3456-7890"):
            self.assertIn(bank, Bank.search([("acc_number", "=", term)]), term)
        self.assertNotIn(bank, Bank.search([("acc_number", "ilike", "5678")]))
        # constraint unik (nomor, partner) tetap berlaku untuk blind index
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                Bank.create({"acc_number": "1234567890", "partner_id": person.id})
                Bank.flush_model()
        # ganti nomor: blind index ikut berganti
        bank.write({"acc_number": "999 888 777"})
        self.assertIn(bank, Bank.search([("acc_number", "=", "999888777")]))
        self.assertNotIn(bank, Bank.search([("acc_number", "=", "1234567890")]))

    def test_company_bank_account_stays_plain(self):
        company = self.Partner.create({"name": "PT Rekening", "is_company": True})
        bank = self.env["res.partner.bank"].create({"acc_number": "5555 6666", "partner_id": company.id})
        self.assertFalse(bank.efn_pii_protected)
        raw = self._raw_table("res_partner_bank", bank.id, "acc_number", "sanitized_acc_number")
        self.assertEqual(raw, {"acc_number": "5555 6666", "sanitized_acc_number": "55556666"})
        self.assertIn(bank, self.env["res.partner.bank"].search([("acc_number", "=", "55556666")]))

    def test_internal_user_bank_protected_name_plain(self):
        """K-a: nama user internal plaintext, rekeningnya data pribadi (K) -> terenkripsi."""
        user = new_test_user(self.env, login="efn_pii_bank_staff", name="Staf Bank", groups="base.group_user")
        self.assertFalse(user.partner_id.sudo().efn_pii_protected)
        bank = self.env["res.partner.bank"].create({"acc_number": "7777 0001", "partner_id": user.partner_id.id})
        self.assertTrue(bank.efn_pii_protected)
        self.assertTrue(self._raw_table("res_partner_bank", bank.id, "acc_number")["acc_number"].startswith(V2))
        self.assertEqual(self._raw(user.partner_id.id, "name")["name"], "Staf Bank")

    # ----------------------------------------------------------------- E-38b login portal opak (Q-PII8)
    def test_portal_login_email_replaced_by_opaque_id(self):
        portal = new_test_user(
            self.env, login="vendor.satu@example.com", name="Vendor Satu", groups="base.group_portal"
        )
        self.assertRegex(portal.login, OPAQUE_RE)
        self.assertTrue(portal.partner_id.sudo().efn_pii_protected)
        self.env.flush_all()
        self.env.cr.execute(SQL("SELECT count(*) FROM res_users WHERE login ILIKE %s", "%vendor.satu%"))
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        # login opak yang sudah ada tidak diubah; user internal ber-login email tidak disentuh
        fixed = new_test_user(self.env, login="vnd-7Q3K9N", name="Vendor Dua", groups="base.group_portal")
        self.assertEqual(fixed.login, "vnd-7Q3K9N")
        staff = new_test_user(self.env, login="staf.internal@example.com", groups="base.group_user")
        self.assertEqual(staff.login, "staf.internal@example.com")
        # menulis email sebagai login portal -> diganti lagi
        fixed.write({"login": "vendor.dua@example.com"})
        self.assertRegex(fixed.login, OPAQUE_RE)
