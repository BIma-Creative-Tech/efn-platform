"""E-38a (ADR-036 §0.2-§0.3) pada res.partner: protect-by-default, SQL mentah = efnpii2 terikat id di semua
jalur tulis, ORM/form/ekspor/QWeb plaintext, `=`/`in`/`=ilike`/`ilike` nilai lengkap lewat blind index,
K-a (user internal / context server), mode terkunci.

Semua nama, HP, email, NPWP, alamat di bawah fiktif khusus test.
"""

import os

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.models.res_partner import PII_FIELDS

from .common import KEYRING_LOGGER, V1, V2, PiiKeyMixin

PHONE = "+62 812-3456-7890"
PHONE_LOCAL = "081234567890"
EMAIL = "Budi.Santoso@Example.COM"
VAT = "01.234.567.8-901.000"
NAME = "Budi Santoso"
STREET = "Jl. Melati No. 5"
CITY = "Kota Uji"
ZIP = "10110"


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiPartner(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"]

    def _create_protected(self, **vals):
        values = {
            "name": NAME,
            "phone": PHONE,
            "email": EMAIL,
            "vat": VAT,
            "street": STREET,
            "street2": "RT 01/RW 02",
            "city": CITY,
            "zip": ZIP,
        }
        values.update(vals)
        return self.Partner.create(values)

    def assertAllBound(self, partner, plaintexts=()):
        raw = self._raw(partner.id, *PII_FIELDS)
        for column, value in raw.items():
            if value is None:
                continue
            self.assertBound(value, "res_partner", column, partner.id)
            for plain in plaintexts:
                self.assertNotIn(plain, value)
        return raw

    def _flag(self, partner):
        return partner.sudo().efn_pii_protected

    # ----------------------------------------------------------------- struktur
    def test_field_classes_keep_standard_attributes(self):
        fields_ = self.Partner._fields
        for fname in PII_FIELDS:
            self.assertTrue(getattr(fields_[fname], "efn_pii_encrypted", False), fname)
            self.assertIsInstance(fields_[fname], fields.Char)
        self.assertFalse(pm.broken_fields(self.Partner))
        # atribut definisi sebelumnya digabung (base: index vat, compute complete_name; mail: tracking, compute)
        self.assertTrue(fields_["vat"].index)
        self.assertTrue(fields_["phone"].tracking)
        self.assertTrue(fields_["email"].tracking)
        self.assertTrue(fields_["name"].tracking)
        self.assertEqual(fields_["complete_name"].compute, "_compute_complete_name")
        self.assertTrue(fields_["complete_name"].store)
        self.assertEqual(fields_["email_normalized"].compute, "_compute_email_normalized")
        self.assertTrue(fields_["email_normalized"].store)
        self.assertTrue(fields_["efn_pii_protected"].precompute)
        # Char lain tidak terpengaruh
        self.assertFalse(getattr(fields_["ref"], "efn_pii_encrypted", False))
        self.assertFalse(getattr(fields_["company_name"], "efn_pii_encrypted", False))
        for fname in ("efn_pii_protected", "efn_phone_bidx", "efn_email_bidx", "efn_vat_bidx", "efn_name_tokens"):
            self.assertEqual(fields_[fname].groups, "base.group_system")

    # ----------------------------------------------------------------- protect-by-default + efnpii2
    def test_individual_protected_by_default_and_bound_to_id(self):
        partner = self._create_protected()
        self.assertTrue(self._flag(partner))
        raw = self.assertAllBound(partner, (NAME, PHONE, EMAIL, EMAIL.lower(), VAT, STREET, CITY, ZIP))
        for column in PII_FIELDS:
            self.assertTrue(raw[column], f"{column} kosong")
        self.assertEqual(
            self.assertBound(raw["email_normalized"], "res_partner", "email_normalized", partner.id),
            EMAIL.lower(),
        )
        extra = self._raw(partner.id, "phone_sanitized", "efn_phone_bidx", "efn_email_bidx", "efn_vat_bidx")
        self.assertIsNone(extra["phone_sanitized"])
        for column in ("efn_phone_bidx", "efn_email_bidx", "efn_vat_bidx"):
            self.assertRegex(extra[column], r"^[0-9a-f]{64}$")
        # SEC-138: tidak ada efnpii1 tersisa (query verifikasi), AAD mengikat kolom DAN id
        self.env.cr.execute(
            SQL(
                "SELECT count(*) FROM res_partner WHERE id = %s AND (%s)",
                partner.id,
                SQL(" OR ").join(SQL("%s LIKE %s", SQL.identifier(c), V1 + "%") for c in PII_FIELDS),
            )
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        ring = kr.get_keyring(self.env.cr.dbname)
        for aad in (
            kr.aad_for("res_partner", "phone"),
            kr.aad_for("res_partner", "email", partner.id),
            kr.aad_for("res_partner", "phone", partner.id + 1),
        ):
            with self.assertRaises(kr.PiiError):
                ring.decrypt_text(raw["phone"], aad)

    def test_class_rules_company_contact_address(self):
        company = self.Partner.create({"name": "PT Sumber Rejeki", "is_company": True, "phone": "021-555-0101"})
        contact = self.Partner.create({"name": "Budi", "parent_id": company.id, "type": "contact"})
        invoice = self.Partner.create({"name": "Tagihan", "parent_id": company.id, "type": "invoice"})
        person = self._create_protected()
        delivery = self.Partner.create({"name": "Rumah", "parent_id": person.id, "type": "delivery"})
        self.assertFalse(self._flag(company))
        self.assertTrue(self._flag(contact))
        self.assertFalse(self._flag(invoice))
        self.assertTrue(self._flag(delivery))
        self.assertEqual(self._raw(company.id, "name", "phone"), {"name": "PT Sumber Rejeki", "phone": "021-555-0101"})
        self.assertEqual(self._raw(invoice.id, "name")["name"], "Tagihan")
        self.assertAllBound(contact, ("Budi",))
        self.assertAllBound(delivery, ("Rumah",))
        # default dari context form (default_parent_id) juga dievaluasi saat create
        contact2 = self.Partner.with_context(default_parent_id=company.id).create({"name": "Siti"})
        self.assertTrue(self._flag(contact2))
        # is_company False -> True tetap terlindungi (satu arah)
        person.write({"is_company": True})
        self.assertTrue(self._flag(person))

    def test_non_admin_and_client_context_cannot_skip(self):
        user = new_test_user(self.env, login="efn_pii_user", groups="base.group_user,base.group_partner_manager")
        as_user = self.Partner.with_user(user)
        partner = as_user.create({"name": "Dari Kasir", "phone": "0812 0000 0001"})
        self.assertTrue(self._flag(partner))
        self.assertAllBound(partner, ("Dari Kasir", "0812 0000 0001"))
        # klien (non-admin) tidak bisa menulis penanda
        with self.assertRaises(AccessError):
            as_user.create({"name": "X", "efn_pii_protected": False})
        # context dari klien RPC (string/bool biasa) tidak berlaku sebagai pengecualian K-a
        for value in ("efn_pii_skip_default", True, 1):
            created = as_user.with_context(efn_pii_skip_default=value).create({"name": f"Coba {value}"})
            self.assertTrue(self._flag(created), value)
        # admin: nilai eksplisit False untuk individu tetap dipaksa True
        forced = self.Partner.create({"name": "Impor", "efn_pii_protected": False})
        self.assertTrue(self._flag(forced))
        # user biasa membaca plaintext dan mencari exact tanpa AccessError ke kolom blind index
        partner.invalidate_recordset()
        self.assertEqual(partner.with_user(user).phone, "0812 0000 0001")
        self.assertIn(partner, as_user.search([("phone", "=", "+6281200000001")]))
        with self.assertRaises(AccessError):
            partner.with_user(user).read(["efn_phone_bidx"])
        # duplikat oleh user non-admin: tetap terlindungi, tanpa AccessError (penanda dari aturan)
        dup = partner.with_user(user).copy()
        self.assertTrue(self._flag(dup))
        self.assertAllBound(dup, ("0812 0000 0001",))

    def test_k_a_internal_user_plain_portal_protected(self):
        internal = new_test_user(self.env, login="efn_pii_staff", name="Staf Internal", groups="base.group_user")
        self.assertFalse(self._flag(internal.partner_id))
        self.assertEqual(self._raw(internal.partner_id.id, "name")["name"], "Staf Internal")
        portal = new_test_user(self.env, login="vnd-7Q3K9M", name="Vendor Portal", groups="base.group_portal")
        self.assertTrue(self._flag(portal.partner_id))
        self.assertAllBound(portal.partner_id, ("Vendor Portal",))
        self.assertEqual(portal.name, "Vendor Portal")
        # context server (SKIP_TOKEN) hanya dari kode server
        server = self.Partner.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN}).create({"name": "Work Contact"})
        self.assertFalse(self._flag(server))
        # user internal diubah jadi portal -> partner menjadi terlindungi
        internal.write({"group_ids": [fields.Command.set([self.env.ref("base.group_portal").id])]})
        self.assertTrue(self._flag(internal.partner_id))
        self.assertAllBound(internal.partner_id, ("Staf Internal",))

    def test_write_and_setattr_encrypt_bound(self):
        partner = self.Partner.create({"name": "Tanpa kontak"})
        self.assertIsNone(self._raw(partner.id, "phone")["phone"])
        partner.write({"phone": "0899 1111 2222", "email": "baru@example.com", "city": "Bandung"})
        partner.vat = "0123456789012345"  # __set__ -> write
        partner.name = "Nama Baru"
        raw = self.assertAllBound(partner, ("0899 1111 2222", "baru@example.com", "0123456789012345", "Nama Baru"))
        self.assertTrue(raw["complete_name"])
        before = raw["phone"]
        partner.phone = "0899 1111 2222"
        after = self._raw(partner.id, "phone")["phone"]
        self.assertBound(after, "res_partner", "phone", partner.id, "0899 1111 2222")
        partner.invalidate_recordset()
        self.assertEqual((partner.phone, partner.name, partner.city), ("0899 1111 2222", "Nama Baru", "Bandung"))
        self.assertTrue(before.startswith(V2))

    def test_import_load_create_and_update(self):
        result = self.Partner.load(
            ["name", "phone", "email", "vat", "street"],
            [["Impor PII", "0812 1111 2222", "impor@example.com", "0198765432109000", "Jl. Impor 1"]],
        )
        self.assertFalse(result["messages"], result["messages"])
        partner = self.Partner.browse(result["ids"][0])
        self.assertTrue(self._flag(partner))
        self.assertAllBound(partner, ("Impor PII", "0812 1111 2222", "impor@example.com", "Jl. Impor 1"))
        result = self.Partner.load([".id", "phone"], [[str(partner.id), "0812 3333 4444"]])
        self.assertFalse(result["messages"], result["messages"])
        self.assertBound(self._raw(partner.id, "phone")["phone"], "res_partner", "phone", partner.id, "0812 3333 4444")

    def test_becoming_protected_encrypts_existing_and_redacts_tracking(self):
        Server = self.Partner.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN})
        partner = Server.create({"name": "Lama", "phone": PHONE, "email": EMAIL, "vat": VAT, "street": STREET})
        self.env.flush_all()
        self.env.cr.precommit.run()
        partner.write({"phone": "0811 0000 0009", "street": "Jl. Lama 9"})
        self.env.flush_all()
        self.env.cr.precommit.run()
        raw = self._raw(partner.id, "name", "phone", "email_normalized", "efn_phone_bidx")
        self.assertEqual((raw["name"], raw["phone"]), ("Lama", "0811 0000 0009"))
        self.assertEqual(raw["email_normalized"], EMAIL.lower())
        self.assertIsNone(raw["efn_phone_bidx"])
        partner.write({"efn_pii_protected": True})
        self.assertTrue(self._flag(partner))
        self.assertAllBound(partner, ("Lama", "0811 0000 0009", EMAIL.lower(), VAT, "Jl. Lama 9"))
        extra = self._raw(partner.id, "phone_sanitized", "efn_phone_bidx", "efn_name_tokens")
        self.assertIsNone(extra["phone_sanitized"])
        self.assertRegex(extra["efn_phone_bidx"], r"^[0-9a-f]{64}$")
        self.assertTrue(extra["efn_name_tokens"])
        partner.invalidate_recordset()
        self.assertEqual((partner.name, partner.phone, partner.email), ("Lama", "0811 0000 0009", EMAIL))
        # riwayat tracking sebelum penandaan diredaksi (SEC-136)
        self.env.cr.execute(
            SQL(
                """SELECT v.old_value_char, v.new_value_char FROM mail_tracking_value v
                   JOIN mail_message m ON m.id = v.mail_message_id
                   WHERE m.model = 'res.partner' AND m.res_id = %s""",
                partner.id,
            )
        )
        values = {v for row in self.env.cr.fetchall() for v in row if v}
        for plain in (PHONE, "0811 0000 0009", STREET, "Jl. Lama 9"):
            self.assertFalse([v for v in values if plain in v], plain)

    def test_flag_one_way_and_marker_rejected(self):
        partner = self._create_protected()
        with self.assertRaises(UserError):
            partner.write({"efn_pii_protected": False})
        for marker in ("efnpii1:AAAA", "efnpii2:AAAA", "efnpii9:AAAA"):
            with self.assertRaises(ValidationError):
                self.Partner.create({"name": "Palsu", "phone": marker})
            with self.assertRaises(ValidationError):
                partner.write({"email": marker})
            with self.assertRaises(ValidationError):
                self.Partner.create({"name": marker})

    def test_tracking_never_stores_protected_plaintext(self):
        protected = self._create_protected(phone="0811 0000 0001")
        control = self.Partner.create({"name": "PT Kontrol", "is_company": True, "phone": "0811 0000 0002"})
        holder = self.Partner.create({"name": "PT Pemegang", "is_company": True})
        self.env.flush_all()
        self.env.cr.precommit.run()
        protected.write({"phone": "0811 0000 0003", "name": "Budi Baru", "street": "Jl. Baru 3"})
        control.write({"phone": "0811 0000 0004"})
        # many2one ke partner terlindungi: label, bukan nama (SEC-136)
        holder.write({"parent_id": protected.id})
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.assertTrue(self._flag(holder), "partner di bawah partner terlindungi ikut terlindungi")
        self.env.cr.execute(
            SQL(
                "SELECT old_value_char, new_value_char FROM mail_tracking_value WHERE create_date >= %s",
                protected.create_date,
            )
        )
        values = {v for row in self.env.cr.fetchall() for v in row if v}
        for plain in ("0811 0000 0001", "0811 0000 0003", "Budi Baru", NAME, "Jl. Baru 3"):
            self.assertNotIn(plain, values)
        self.assertFalse([v for v in values if "Budi" in v])
        self.assertIn(f"{pm.REDACTED_LABEL}{protected.id}", values)
        # kontrol: tracking standar tetap aktif untuk partner tidak terlindungi
        self.assertIn("0811 0000 0004", values)

    # ----------------------------------------------------------------- baca
    def test_orm_read_paths_return_plaintext(self):
        partner = self._create_protected()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual((partner.name, partner.phone, partner.email, partner.vat), (NAME, PHONE, EMAIL, VAT))
        self.assertEqual((partner.street, partner.city, partner.zip), (STREET, CITY, ZIP))
        self.assertEqual(partner.complete_name, NAME)
        self.assertEqual(partner.display_name, NAME)
        self.assertEqual(partner.email_normalized, EMAIL.lower())
        self.assertEqual(partner.read(["email", "name"])[0]["name"], NAME)
        self.assertEqual(partner.web_read({"phone": {}, "vat": {}, "city": {}})[0]["city"], CITY)
        rows = self.Partner.search_read([("id", "=", partner.id)], ["phone", "street", "display_name"])
        self.assertEqual((rows[0]["phone"], rows[0]["street"], rows[0]["display_name"]), (PHONE, STREET, NAME))
        self.assertEqual(partner.export_data(["name", "phone", "zip"])["datas"][0], [NAME, PHONE, ZIP])
        self.assertEqual(partner.mapped("email"), [EMAIL])
        self.assertIn(EMAIL.lower(), (partner.email_formatted or "").lower())
        self.assertIn(CITY, partner.contact_address or "")
        copy = partner.copy()
        self.assertTrue(self._flag(copy))
        self.assertEqual(copy.phone, PHONE)
        self.assertAllBound(copy, (PHONE, EMAIL, VAT))
        copy2 = partner.copy(default={"efn_pii_protected": False})
        self.assertTrue(self._flag(copy2))
        self.assertAllBound(copy2, (PHONE, EMAIL, VAT))

    def test_qweb_render_plaintext(self):
        partner = self._create_protected()
        self.env.flush_all()
        self.env.invalidate_all()
        view = self.env["ir.ui.view"].create(
            {
                "name": "efn_pii_test_qweb",
                "type": "qweb",
                "arch": '<t t-name="efn_pii_test_qweb"><div><span t-out="partner.name"/>|<span t-out="partner.phone"/>|'
                '<span t-field="partner.email"/>|<span t-out="partner.vat"/>|<span t-out="partner.street"/>|'
                '<span t-out="partner.city"/></div></t>',
            }
        )
        html = str(self.env["ir.qweb"]._render(view.id, {"partner": partner}))
        for plain in (NAME, PHONE, EMAIL, VAT, STREET, CITY):
            self.assertIn(plain, html)
        self.assertNotIn("efnpii", html)

    # ----------------------------------------------------------------- cari exact (blind index)
    def test_search_equal_in_and_complete_values_use_blind_index(self):
        partner = self._create_protected()
        other = self._create_protected(
            name="Siti Aminah", phone="0899 9999 0000", email="lain@example.com", vat="99.999.999.9-999.000"
        )
        Partner = self.Partner
        for domain in (
            [("phone", "=", PHONE_LOCAL)],
            [("phone", "=", "6281234567890")],
            [("phone", "in", ["+6281234567890", "0800000000"])],
            [("phone", "ilike", PHONE)],
            [("email", "=", "budi.santoso@example.com")],
            [("email", "in", ["BUDI.SANTOSO@EXAMPLE.COM"])],
            [("email", "=ilike", EMAIL)],
            [("email", "ilike", "budi.santoso@example.com")],
            [("email_normalized", "in", ["budi.santoso@example.com"])],
            [("email_normalized", "=", "budi.santoso@example.com")],
            [("vat", "=", "012345678901000")],
            [("vat", "=", VAT)],
            [("vat", "ilike", "012345678901000")],
        ):
            with self.subTest(domain=domain):
                found = Partner.search(domain)
                self.assertIn(partner, found)
                self.assertNotIn(other, found)
        both = [("id", "in", [partner.id, other.id])]
        self.assertEqual(Partner.search([("phone", "!=", PHONE_LOCAL), *both]), other)
        self.assertEqual(Partner.search([("phone", "not in", [PHONE_LOCAL]), *both]), other)
        blank = self.Partner.create({"name": "Tanpa HP"})
        self.assertIn(blank, Partner.search([("phone", "=", False)]))
        self.assertNotIn(partner, Partner.search([("phone", "=", False)]))
        self.assertEqual(partner.filtered_domain([("phone", "=", PHONE)]), partner)
        # name_search: email/NPWP lengkap ada di _rec_names_search -> ditemukan
        self.assertIn(partner.id, [r[0] for r in Partner.name_search("budi.santoso@example.com")])
        self.assertIn(partner.id, [r[0] for r in Partner.name_search("012345678901000")])

    def test_same_vat_duplicate_detection_via_blind_index(self):
        first = self._create_protected()
        second = self._create_protected(name="Duplikat NPWP", phone=False, email=False)
        self.assertEqual(second.same_vat_partner_id, first)

    def test_partial_contact_search_excludes_protected(self):
        partner = self._create_protected()
        plain = self.Partner.create(
            {"name": "PT Vendor", "is_company": True, "phone": "0812 3456 0000", "email": "budi@vendor.com"}
        )
        for domain in (
            [("phone", "ilike", "3456")],
            [("email", "ilike", "budi")],
            [("email", "ilike", "budi.santoso@")],
            [("vat", "like", "234")],
            [("street", "ilike", "Melati")],
            [("city", "=", CITY)],
            [("zip", "=", ZIP)],
            [("phone_mobile_search", "ilike", "3456")],
        ):
            with self.subTest(domain=domain):
                self.assertNotIn(partner, self.Partner.search(domain))
        self.assertIn(plain, self.Partner.search([("phone", "ilike", "3456")]))
        self.assertIn(plain, self.Partner.search([("phone_mobile_search", "ilike", "3456")]))
        self.assertIn(partner, self.Partner.search([("phone", "not ilike", "3456"), ("id", "=", partner.id)]))
        # HP lengkap lewat phone_mobile_search (pencarian angka POS 19) -> blind index
        self.assertIn(partner, self.Partner.search([("phone_mobile_search", "ilike", PHONE_LOCAL)]))

    # ----------------------------------------------------------------- rotasi & integritas
    def test_rotation_v1_key_readable_after_active_v2(self):
        _folder, keys, bidx_key = self._pii_env()
        name_key = self.pii_name_key
        partner = self._create_protected()
        old = self._raw(partner.id, "phone")["phone"]
        self.assertEqual(kr.version_of(old), 1)
        self._pii_env(keys={1: keys[1], 2: os.urandom(32)}, bidx_key=bidx_key, name_key=name_key, active=2)
        self.env.invalidate_all()
        self.assertEqual(partner.phone, PHONE)
        partner.phone = "0812 5555 6666"
        self.assertEqual(kr.version_of(self._raw(partner.id, "phone")["phone"]), 2)
        self.assertEqual(kr.version_of(self._raw(partner.id, "email")["email"]), 1)
        self.assertIn(partner, self.Partner.search([("email", "=", EMAIL)]))
        self.assertIn(partner, self.Partner.search([("name", "ilike", "budi")]))

    def test_swapped_ciphertext_between_records_fails_auth(self):
        """AC-7 / SEC-138: ciphertext dipindah antar-record lewat SQL -> nilai pengganti + auth_failed."""
        first = self._create_protected()
        second = self._create_protected(name="Siti Aminah", phone="0899 1234 5678")
        raw = self._raw(first.id, "phone", "email")
        self.env.cr.execute(SQL("UPDATE res_partner SET phone = %s WHERE id = %s", raw["phone"], second.id))
        self.env.invalidate_all()
        with self.assertLogs(KEYRING_LOGGER, "ERROR") as logs:
            self.assertEqual(second.phone, kr.PLACEHOLDER)
        output = "\n".join(logs.output)
        self.assertIn("auth_failed", output)
        for secret in (PHONE, raw["phone"], "0899 1234 5678"):
            self.assertNotIn(secret, output)
        # kolom lain di record yang sama (AAD kolom berbeda) juga gagal
        self.env.cr.execute(SQL("UPDATE res_partner SET phone = %s WHERE id = %s", raw["email"], first.id))
        self.env.invalidate_all()
        with self.assertLogs(KEYRING_LOGGER, "ERROR"):
            self.assertEqual(first.phone, kr.PLACEHOLDER)


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiLocked(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        _folder, self.keys, self.bidx_key = self._pii_env()
        self.name_key = self.pii_name_key
        self.Partner = self.env["res.partner"]
        self.partner = self.Partner.create({"name": "Member", "phone": PHONE, "email": EMAIL})
        self.raw_before = self._raw(self.partner.id, "name", "phone", "email", "efn_phone_bidx", "efn_name_tokens")

    def test_locked_without_keys(self):
        (_folder, _keys, _bidx), load_logs = self._pii_locked()
        self.assertIn('"status": "locked"', "\n".join(load_logs.output))
        self.env.invalidate_all()
        self.assertEqual(self.partner.phone, kr.PLACEHOLDER)
        self.assertEqual(self.partner.read(["name"])[0]["name"], kr.PLACEHOLDER)
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
            with self.assertRaises(UserError):
                self.partner.write({"phone": "0899 0000 1111"})
            with self.assertRaises(UserError):
                self.partner.write({"name": "Member diubah"})
            with self.assertRaises(UserError):
                self.Partner.create({"name": "Pelanggan Baru"})
        output = "\n".join(logs.output)
        for secret in (PHONE, EMAIL, "0899", "Member", "efnpii"):
            self.assertNotIn(secret, output)
        self.env.invalidate_all()
        self.assertEqual(
            self._raw(self.partner.id, "name", "phone", "email", "efn_phone_bidx", "efn_name_tokens"), self.raw_before
        )
        # POS/penjualan tetap jalan untuk partner tidak terlindungi
        company = self.Partner.create({"name": "PT Walk-in", "is_company": True, "phone": "0811 2222 3333"})
        self.assertEqual(self._raw(company.id, "phone")["phone"], "0811 2222 3333")
        self.partner.write({"comment": "catatan tanpa PII"})
        self.assertNotIn(self.partner, self.Partner.search([("phone", "=", PHONE_LOCAL)]))
        self.assertNotIn(self.partner, self.Partner.search([("name", "ilike", "member")]))

    def test_locked_when_only_name_key_missing(self):
        self._pii_locked(keys=self.keys, bidx_key=self.bidx_key, with_bidx=True, with_name=False)
        self.env.invalidate_all()
        self.assertEqual(self.partner.phone, PHONE)  # dekripsi tetap bisa
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"):
            with self.assertRaises(UserError):
                self.partner.write({"email": "baru@example.com"})

    def test_locked_when_only_bidx_key_missing(self):
        self._pii_locked(keys=self.keys, with_bidx=False, name_key=self.name_key, with_name=True)
        self.env.invalidate_all()
        self.assertEqual(self.partner.phone, PHONE)
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"):
            with self.assertRaises(UserError):
                self.partner.write({"email": "baru@example.com"})

    def test_keys_restored_unlocks(self):
        self._pii_locked()
        self._pii_env(keys=self.keys, bidx_key=self.bidx_key, name_key=self.name_key)
        self.env.invalidate_all()
        self.assertEqual(self.partner.phone, PHONE)
        self.partner.write({"phone": "0812 7777 8888"})
        self.assertIn(self.partner, self.Partner.search([("phone", "=", "081277778888")]))
        self.assertIn(self.partner, self.Partner.search([("name", "ilike", "memb")]))
