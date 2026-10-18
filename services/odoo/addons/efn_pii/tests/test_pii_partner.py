"""Kriteria PoC ADR-036 §8.2 pada res.partner: SQL mentah = efnpii1 di semua jalur tulis,
ORM/form/ekspor/QWeb plaintext, `=`/`in` lewat blind index, `ilike` tanpa error, mode terkunci."""

import os

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr

from .common import KEYRING_LOGGER, PiiKeyMixin

PHONE = "+62 812-3456-7890"
PHONE_LOCAL = "081234567890"
EMAIL = "Budi.Santoso@Example.COM"
VAT = "01.234.567.8-901.000"
PII_COLUMNS = ("phone", "email", "vat")


@tagged("post_install", "-at_install", "efn", "efn_pii_poc")
class TestEfnPiiPartner(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"]

    def _create_protected(self, **vals):
        values = {"name": "Pelanggan PII", "efn_pii_protected": True, "phone": PHONE, "email": EMAIL, "vat": VAT}
        values.update(vals)
        return self.Partner.create(values)

    def assertEncrypted(self, raw, plaintexts=()):
        for column in PII_COLUMNS:
            value = raw.get(column)
            if value is None:
                continue
            self.assertTrue(value.startswith("efnpii1:"), f"{column} masih plaintext di SQL")
            for plain in plaintexts:
                self.assertNotIn(plain, value)

    # ----------------------------------------------------------------- struktur
    def test_field_classes_keep_standard_attributes(self):
        fields_ = self.Partner._fields
        for fname in PII_COLUMNS:
            self.assertTrue(getattr(fields_[fname], "efn_pii_encrypted", False), fname)
            self.assertIsInstance(fields_[fname], fields.Char)
        # atribut definisi sebelumnya digabung (base: index vat; mail: tracking)
        self.assertTrue(fields_["vat"].index)
        self.assertTrue(fields_["phone"].tracking)
        self.assertTrue(fields_["email"].tracking)
        # Char lain tidak terpengaruh
        self.assertFalse(getattr(fields_["name"], "efn_pii_encrypted", False))
        self.assertFalse(getattr(fields_["street"], "efn_pii_encrypted", False))
        for fname in ("efn_pii_protected", "efn_phone_bidx", "efn_email_bidx", "efn_vat_bidx"):
            self.assertEqual(fields_[fname].groups, "base.group_system")

    # ----------------------------------------------------------------- kriteria 1: tulis
    def test_create_encrypts_in_sql_and_clears_derived_copies(self):
        partner = self._create_protected()
        raw = self._raw(
            partner.id,
            *PII_COLUMNS,
            "email_normalized",
            "phone_sanitized",
            "efn_phone_bidx",
            "efn_email_bidx",
            "efn_vat_bidx",
        )
        self.assertEncrypted(raw, (PHONE, EMAIL, EMAIL.lower(), VAT))
        self.assertTrue(all(raw[c] for c in PII_COLUMNS))
        self.assertIsNone(raw["email_normalized"])
        self.assertIsNone(raw["phone_sanitized"])
        for column in ("efn_phone_bidx", "efn_email_bidx", "efn_vat_bidx"):
            self.assertRegex(raw[column], r"^[0-9a-f]{64}$")
        # AAD mengikat kolom: ciphertext phone tidak bisa dibaca sebagai email
        ring = kr.get_keyring(self.env.cr.dbname)
        self.assertEqual(ring.decrypt_text(raw["phone"], "efn:odoo:res_partner.phone:v1"), PHONE)
        with self.assertRaises(kr.PiiError):
            ring.decrypt_text(raw["phone"], "efn:odoo:res_partner.email:v1")

    def test_write_and_setattr_encrypt(self):
        partner = self.Partner.create({"name": "Tanpa kontak", "efn_pii_protected": True})
        self.assertEqual(self._raw(partner.id, "phone")["phone"], None)
        partner.write({"phone": "0899 1111 2222", "email": "baru@example.com"})
        partner.vat = "0123456789012345"  # __set__ -> write
        raw = self._raw(partner.id, *PII_COLUMNS)
        self.assertEncrypted(raw, ("0899 1111 2222", "baru@example.com", "0123456789012345"))
        self.assertTrue(all(raw[c] for c in PII_COLUMNS))
        # tulis nilai sama tetap ciphertext (IV baru)
        before = raw["phone"]
        partner.phone = "0899 1111 2222"
        after = self._raw(partner.id, "phone")["phone"]
        self.assertTrue(after.startswith("efnpii1:"))
        partner.invalidate_recordset()
        self.assertEqual(partner.phone, "0899 1111 2222")
        self.assertTrue(before.startswith("efnpii1:"))

    def test_sudo_and_non_admin_paths(self):
        user = new_test_user(self.env, login="efn_pii_user", groups="base.group_user,base.group_partner_manager")
        as_user = self.Partner.with_user(user)
        # klien (non-admin) tidak bisa menulis penanda
        with self.assertRaises(AccessError):
            as_user.create({"name": "X", "efn_pii_protected": True})
        # kode server (sudo) di jalur user biasa
        partner = as_user.sudo().create({"name": "Via sudo", "efn_pii_protected": True, "phone": PHONE_LOCAL})
        self.assertEncrypted(self._raw(partner.id, "phone"), (PHONE_LOCAL,))
        # user biasa mengubah HP partner terlindungi (tanpa sudo): flush tetap mengenkripsi
        partner.with_user(user).write({"phone": "0877 0000 1111", "email": "u@example.com"})
        raw = self._raw(partner.id, "phone", "email")
        self.assertEncrypted(raw, ("0877 0000 1111", "u@example.com"))
        # user biasa membaca plaintext dan bisa mencari exact (kolom blind index tanpa AccessError)
        partner.invalidate_recordset()
        self.assertEqual(partner.with_user(user).phone, "0877 0000 1111")
        found = as_user.search([("phone", "=", "+6287700001111")])
        self.assertIn(partner.id, found.ids)
        with self.assertRaises(AccessError):
            partner.with_user(user).read(["efn_phone_bidx"])

    def test_import_load_create_and_update(self):
        result = self.Partner.load(
            ["name", "phone", "email", "vat", "efn_pii_protected"],
            [["Impor PII", "0812 1111 2222", "impor@example.com", "0198765432109000", "1"]],
        )
        self.assertFalse(result["messages"], result["messages"])
        partner = self.Partner.browse(result["ids"][0])
        self.assertTrue(partner.efn_pii_protected)
        raw = self._raw(partner.id, *PII_COLUMNS)
        self.assertTrue(all(raw[c] for c in PII_COLUMNS))
        self.assertEncrypted(raw, ("0812 1111 2222", "impor@example.com", "0198765432109000"))
        # load update (kolom .id) -> _load_records_write -> write
        result = self.Partner.load([".id", "phone"], [[str(partner.id), "0812 3333 4444"]])
        self.assertFalse(result["messages"], result["messages"])
        self.assertEncrypted(self._raw(partner.id, "phone"), ("0812 3333 4444",))
        partner.invalidate_recordset()
        self.assertEqual(partner.phone, "0812 3333 4444")

    def test_becoming_protected_encrypts_existing_values(self):
        partner = self.Partner.create({"name": "Lama", "phone": PHONE, "email": EMAIL, "vat": VAT})
        raw = self._raw(partner.id, *PII_COLUMNS, "email_normalized", "efn_phone_bidx")
        self.assertEqual(raw["phone"], PHONE)
        self.assertEqual(raw["email_normalized"], EMAIL.lower())
        self.assertIsNone(raw["efn_phone_bidx"])
        partner.write({"efn_pii_protected": True})
        raw = self._raw(partner.id, *PII_COLUMNS, "email_normalized", "phone_sanitized", "efn_phone_bidx")
        self.assertEncrypted(raw, (PHONE, EMAIL.lower(), VAT))
        self.assertTrue(all(raw[c] for c in PII_COLUMNS))
        self.assertIsNone(raw["email_normalized"])
        self.assertIsNone(raw["phone_sanitized"])
        self.assertRegex(raw["efn_phone_bidx"], r"^[0-9a-f]{64}$")
        partner.invalidate_recordset()
        self.assertEqual((partner.phone, partner.email, partner.vat), (PHONE, EMAIL, VAT))

    def test_flag_is_one_way_and_marker_rejected(self):
        partner = self._create_protected()
        with self.assertRaises(UserError):
            partner.write({"efn_pii_protected": False})
        with self.assertRaises(ValidationError):
            self.Partner.create({"name": "Palsu", "phone": "efnpii1:AAAA"})
        with self.assertRaises(ValidationError):
            partner.write({"email": "efnpii1:AAAA"})

    def test_unprotected_partner_stays_plaintext(self):
        partner = self.Partner.create(
            {"name": "Vendor Biasa", "phone": PHONE_LOCAL, "email": "vendor@example.com", "vat": VAT}
        )
        raw = self._raw(partner.id, *PII_COLUMNS, "email_normalized", "efn_phone_bidx", "efn_email_bidx")
        self.assertEqual((raw["phone"], raw["email"], raw["vat"]), (PHONE_LOCAL, "vendor@example.com", VAT))
        self.assertEqual(raw["email_normalized"], "vendor@example.com")
        self.assertIsNone(raw["efn_phone_bidx"])
        self.assertIsNone(raw["efn_email_bidx"])
        self.assertIn(partner, self.Partner.search([("phone", "ilike", "3456")]))
        self.assertIn(partner, self.Partner.search([("email", "ilike", "vendor@")]))

    def test_tracking_never_stores_protected_plaintext(self):
        protected = self._create_protected(phone="0811 0000 0001")
        plain = self.Partner.create({"name": "Biasa", "phone": "0811 0000 0002"})
        self.env.flush_all()
        self.env.cr.precommit.run()
        protected.write({"phone": "0811 0000 0003"})
        plain.write({"phone": "0811 0000 0004"})
        self.env.flush_all()
        self.env.cr.precommit.run()
        self.env.cr.execute(
            SQL(
                "SELECT old_value_char, new_value_char FROM mail_tracking_value "
                "WHERE old_value_char LIKE %s OR new_value_char LIKE %s",
                "0811 0000 %",
                "0811 0000 %",
            )
        )
        values = {v for row in self.env.cr.fetchall() for v in row if v}
        self.assertNotIn("0811 0000 0001", values)
        self.assertNotIn("0811 0000 0003", values)
        # kontrol: tracking standar tetap aktif untuk partner biasa
        self.assertIn("0811 0000 0004", values)

    # ----------------------------------------------------------------- kriteria 2: baca
    def test_orm_read_paths_return_plaintext(self):
        partner = self._create_protected()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual((partner.phone, partner.email, partner.vat), (PHONE, EMAIL, VAT))
        self.assertEqual(partner.read(["phone", "email", "vat"])[0]["email"], EMAIL)
        self.assertEqual(partner.web_read({"phone": {}, "email": {}, "vat": {}})[0]["vat"], VAT)
        rows = self.Partner.search_read([("id", "=", partner.id)], ["phone", "email", "vat"])
        self.assertEqual(rows[0]["phone"], PHONE)
        self.assertEqual(partner.export_data(["phone", "email", "vat"])["datas"][0], [PHONE, EMAIL, VAT])
        self.assertEqual(partner.mapped("phone"), [PHONE])
        self.assertIn(EMAIL.lower(), (partner.email_formatted or "").lower())
        # duplikat partner terlindungi tetap terlindungi (copy_data), bukan salinan plaintext
        copy = partner.copy()
        self.assertTrue(copy.sudo().efn_pii_protected)
        self.assertEqual(copy.phone, PHONE)
        self.assertEncrypted(self._raw(copy.id, *PII_COLUMNS), (PHONE, EMAIL, VAT))
        # pemanggil tidak bisa mematikan penanda lewat `default` (bypass satu arah)
        copy2 = partner.copy(default={"efn_pii_protected": False})
        self.assertTrue(copy2.sudo().efn_pii_protected)
        self.assertEncrypted(self._raw(copy2.id, *PII_COLUMNS), (PHONE, EMAIL, VAT))

    def test_qweb_render_plaintext(self):
        partner = self._create_protected()
        self.env.flush_all()
        self.env.invalidate_all()
        view = self.env["ir.ui.view"].create(
            {
                "name": "efn_pii_test_qweb",
                "type": "qweb",
                "arch": '<t t-name="efn_pii_test_qweb"><div><span t-out="partner.phone"/>|'
                '<span t-field="partner.email"/>|<span t-out="partner.vat"/></div></t>',
            }
        )
        html = str(self.env["ir.qweb"]._render(view.id, {"partner": partner}))
        self.assertIn(PHONE, html)
        self.assertIn(EMAIL, html)
        self.assertIn(VAT, html)
        self.assertNotIn("efnpii1:", html)

    # ----------------------------------------------------------------- kriteria 3: cari
    def test_search_equal_and_in_use_blind_index(self):
        partner = self._create_protected()
        other = self._create_protected(phone="0899 9999 0000", email="lain@example.com", vat="99.999.999.9-999.000")
        Partner = self.Partner
        for domain in (
            [("phone", "=", PHONE_LOCAL)],
            [("phone", "=", "6281234567890")],
            [("phone", "in", ["+6281234567890", "0800000000"])],
            [("email", "=", "budi.santoso@example.com")],
            [("email", "in", ["BUDI.SANTOSO@EXAMPLE.COM"])],
            [("vat", "=", "012345678901000")],
            [("vat", "=", VAT)],
        ):
            with self.subTest(domain=domain):
                found = Partner.search(domain)
                self.assertIn(partner, found)
                self.assertNotIn(other, found)
        # negasi
        both = [("id", "in", [partner.id, other.id])]
        self.assertEqual(Partner.search([("phone", "!=", PHONE_LOCAL), *both]), other)
        self.assertEqual(Partner.search([("phone", "not in", [PHONE_LOCAL]), *both]), other)
        # kosong / NULL tetap bekerja
        blank = self.Partner.create({"name": "Tanpa HP", "efn_pii_protected": True})
        self.assertIn(blank, Partner.search([("phone", "=", False)]))
        self.assertNotIn(partner, Partner.search([("phone", "=", False)]))
        # filtered_domain (evaluasi di memori) memakai nilai plaintext
        self.assertEqual(partner.filtered_domain([("phone", "=", PHONE)]), partner)

    def test_same_vat_duplicate_detection_via_blind_index(self):
        first = self._create_protected()
        second = self._create_protected(name="Duplikat NPWP", phone=False, email=False)
        self.assertEqual(second.same_vat_partner_id, first)

    def test_ilike_does_not_error_and_excludes_protected(self):
        partner = self._create_protected(name="Budi Santoso")
        plain = self.Partner.create(
            {"name": "Tidak Terlindungi", "phone": "0812 3456 0000", "email": "budi@vendor.com"}
        )
        for domain in (
            [("phone", "ilike", "3456")],
            [("phone", "ilike", PHONE)],
            [("email", "ilike", "budi")],
            [("vat", "like", "234")],
            [("email", "=ilike", EMAIL)],
        ):
            with self.subTest(domain=domain):
                self.assertNotIn(partner, self.Partner.search(domain))
        self.assertIn(plain, self.Partner.search([("phone", "ilike", "3456")]))
        self.assertIn(partner, self.Partner.search([("phone", "not ilike", "3456"), ("id", "=", partner.id)]))
        # name_search: email/vat ada di _rec_names_search; tidak error, nama tetap cocok
        self.assertIn(partner.id, [r[0] for r in self.Partner.name_search("Budi Santoso")])
        self.assertNotIn(partner.id, [r[0] for r in self.Partner.name_search("budi.santoso@")])

    # ----------------------------------------------------------------- rotasi minimal & integritas
    def test_rotation_v1_readable_after_active_v2(self):
        _folder, keys, bidx_key = self._pii_env()
        partner = self._create_protected()
        old = self._raw(partner.id, "phone")["phone"]
        self.assertEqual(kr.version_of(old), 1)
        self._pii_env(keys={1: keys[1], 2: os.urandom(32)}, bidx_key=bidx_key, active=2)
        self.env.invalidate_all()
        self.assertEqual(partner.phone, PHONE)
        partner.phone = "0812 5555 6666"
        self.assertEqual(kr.version_of(self._raw(partner.id, "phone")["phone"]), 2)
        self.assertEqual(kr.version_of(self._raw(partner.id, "email")["email"]), 1)
        self.assertIn(partner, self.Partner.search([("email", "=", EMAIL)]))

    def test_tampered_or_swapped_ciphertext_reads_placeholder(self):
        partner = self._create_protected()
        raw = self._raw(partner.id, "phone", "email")
        # tukar ciphertext email ke kolom phone (AAD berbeda) -> gagal autentikasi
        self.env.cr.execute(SQL("UPDATE res_partner SET phone = %s WHERE id = %s", raw["email"], partner.id))
        self.env.invalidate_all()
        with self.assertLogs(KEYRING_LOGGER, "ERROR") as logs:
            self.assertEqual(partner.phone, kr.PLACEHOLDER)
        output = "\n".join(logs.output)
        self.assertIn("auth_failed", output)
        for secret in (PHONE, EMAIL, raw["email"]):
            self.assertNotIn(secret, output)


@tagged("post_install", "-at_install", "efn", "efn_pii_poc")
class TestEfnPiiLocked(PiiKeyMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        _folder, self.keys, self.bidx_key = self._pii_env()
        self.Partner = self.env["res.partner"]
        self.partner = self.Partner.create(
            {"name": "Member", "efn_pii_protected": True, "phone": PHONE, "email": EMAIL}
        )
        self.raw_before = self._raw(self.partner.id, "phone", "email", "efn_phone_bidx", "efn_email_bidx")

    def test_locked_without_keys(self):
        (_folder, _keys, _bidx), load_logs = self._pii_locked()
        self.assertIn('"status": "locked"', "\n".join(load_logs.output))
        self.env.invalidate_all()
        # baca: nilai pengganti, bukan False dan bukan ciphertext
        self.assertEqual(self.partner.phone, kr.PLACEHOLDER)
        self.assertEqual(self.partner.read(["email"])[0]["email"], kr.PLACEHOLDER)
        # tulis field terenkripsi ditolak, ciphertext & blind index tidak berubah
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
            with self.assertRaises(UserError):
                self.partner.write({"phone": "0899 0000 1111"})
            with self.assertRaises(UserError):
                self.Partner.create({"name": "Baru", "efn_pii_protected": True, "phone": "0899 0000 2222"})
            with self.assertRaises(UserError):
                self.Partner.create({"name": "Lama", "phone": "0899 0000 3333"}).write({"efn_pii_protected": True})
        output = "\n".join(logs.output)
        for secret in (PHONE, EMAIL, "0899", "efnpii1:"):
            self.assertNotIn(secret, output)
        self.env.invalidate_all()
        self.assertEqual(
            self._raw(self.partner.id, "phone", "email", "efn_phone_bidx", "efn_email_bidx"), self.raw_before
        )
        # POS/penjualan tetap jalan: partner tidak terlindungi & partner terlindungi tanpa PII bisa dibuat
        plain = self.Partner.create({"name": "Walk-in", "phone": "0811 2222 3333"})
        self.assertEqual(self._raw(plain.id, "phone")["phone"], "0811 2222 3333")
        self.Partner.create({"name": "Member tanpa kontak", "efn_pii_protected": True})
        self.partner.write({"name": "Member diubah"})
        # pencarian exact tidak error (tanpa kunci blind index: tidak ditemukan)
        self.assertNotIn(self.partner, self.Partner.search([("phone", "=", PHONE_LOCAL)]))

    def test_locked_when_only_bidx_key_missing(self):
        self._pii_locked(keys=self.keys, with_bidx=False)
        self.env.invalidate_all()
        self.assertEqual(self.partner.phone, PHONE)  # dekripsi tetap bisa
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"):
            with self.assertRaises(UserError):
                self.partner.write({"email": "baru@example.com"})

    def test_keys_restored_unlocks(self):
        self._pii_locked()
        self._pii_env(keys=self.keys, bidx_key=self.bidx_key)
        self.env.invalidate_all()
        self.assertEqual(self.partner.phone, PHONE)
        self.partner.write({"phone": "0812 7777 8888"})
        self.assertIn(self.partner, self.Partner.search([("phone", "=", "081277778888")]))
