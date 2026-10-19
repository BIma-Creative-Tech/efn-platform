"""AC-5 (SEC-136), AC-6 (SEC-137), AC-7 (SEC-138) + migrasi `efnpii1:` (ADR-036 §0.3, §0.11)."""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.models import pii_model as pm
from odoo.addons.efn_pii.models.res_partner import PII_FIELDS

from .common import KEYRING_LOGGER, V1, PiiKeyMixin

VAT = "0123456789012345"
EMAIL = "siti.aminah@example.com"


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiSec136(PiiKeyMixin, TransactionCase):
    """Propagasi ke anak / commercial sync / merge / email masuk / tracking lama."""

    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"]
        self.person = self.Partner.create({"name": "Siti Aminah", "email": EMAIL, "vat": VAT})

    def _flag(self, partner):
        return partner.sudo().efn_pii_protected

    def test_child_of_protected_parent_and_commercial_sync(self):
        child = self.Partner.create({"name": "Alamat Kirim", "parent_id": self.person.id, "type": "delivery"})
        self.assertTrue(self._flag(child))
        self.assertEqual(child.vat, VAT)  # _commercial_sync_from_company
        self.assertBound(self._raw(child.id, "vat")["vat"], "res_partner", "vat", child.id, VAT)
        # anak dibuat lewat context server K-a tetap ikut terlindungi saat menerima NPWP dari induk
        server_child = self.Partner.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN}).create(
            {"name": "Anak Server", "parent_id": self.person.id, "type": "other"}
        )
        self.assertTrue(self._flag(server_child))
        self.assertBound(self._raw(server_child.id, "vat")["vat"], "res_partner", "vat", server_child.id, VAT)
        # NPWP induk diubah -> _commercial_sync_to_descendants -> anak tetap ciphertext
        self.person.write({"vat": "9988776655443322"})
        for partner in (child, server_child):
            self.assertBound(self._raw(partner.id, "vat")["vat"], "res_partner", "vat", partner.id, "9988776655443322")

    def test_existing_unprotected_child_moved_under_protected_parent(self):
        company = self.Partner.create({"name": "PT Lama", "is_company": True})
        address = self.Partner.create(
            {"name": "Gudang", "parent_id": company.id, "type": "delivery", "street": "Jl. A"}
        )
        self.assertFalse(self._flag(address))
        address.write({"parent_id": self.person.id})
        self.assertTrue(self._flag(address))
        raw = self._raw(address.id, "name", "street", "vat")
        self.assertBound(raw["street"], "res_partner", "street", address.id)
        self.assertBound(raw["vat"], "res_partner", "vat", address.id, VAT)

    def test_protecting_parent_protects_descendants(self):
        Server = self.Partner.with_context(**{pm.SKIP_CONTEXT_KEY: pm.SKIP_TOKEN})
        parent = Server.create({"name": "Induk Lama", "phone": "0812 4000 0001"})
        kid = Server.create({"name": "Anak Lama", "parent_id": parent.id, "type": "invoice", "street": "Jl. B"})
        self.assertFalse(self._flag(kid))
        parent.write({"efn_pii_protected": True})
        self.assertTrue(self._flag(kid))
        self.assertBound(self._raw(kid.id, "street")["street"], "res_partner", "street", kid.id, "Jl. B")

    def test_merge_wizard_protects_destination(self):
        dst = self.Partner.create({"name": "PT Tujuan", "is_company": True})
        self.assertFalse(self._flag(dst))
        src = self.Partner.create({"name": "Budi Merge", "phone": "0812 4000 0002", "street": "Jl. Merge 1"})
        self.env["base.partner.merge.automatic.wizard"]._merge([src.id, dst.id], dst_partner=dst, extra_checks=False)
        self.assertFalse(src.exists())
        self.assertTrue(self._flag(dst))
        raw = self._raw(dst.id, *PII_FIELDS)
        for column, value in raw.items():
            if value:
                self.assertBound(value, "res_partner", column, dst.id)
        self.assertEqual(dst.phone, "0812 4000 0002")
        self.assertEqual(dst.street, "Jl. Merge 1")

    def test_incoming_email_finds_protected_owner(self):
        before = self.Partner.search_count([])
        found = self.Partner._find_or_create_from_emails([f"Siti <{EMAIL.upper()}>"])
        self.assertEqual(found[0], self.person)
        self.assertEqual(self.Partner.find_or_create(EMAIL), self.person)
        self.assertEqual(self.Partner.search_count([]), before, "tidak ada partner duplikat dari email masuk")
        # email baru -> partner baru, langsung terlindungi
        new = self.Partner._find_or_create_from_emails(["Tamu Baru <tamu.baru@example.com>"])[0]
        self.assertTrue(self._flag(new))
        self.assertBound(self._raw(new.id, "email")["email"], "res_partner", "email", new.id)


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiSec137(PiiKeyMixin, TransactionCase):
    """Fail-closed bila field cakupan bukan EfnPiiChar + alarm plaintext saat baca."""

    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"]
        self.person = self.Partner.create({"name": "Rina Kartika", "street": "Jl. C"})

    def _break(self, fname):
        field = self.Partner._fields[fname]
        patcher = patch.object(field, "efn_pii_encrypted", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_redefined_field_refuses_protected_writes(self):
        self._break("street")
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
            self.assertEqual(pm.check_registry(self.Partner), ["street"])
            with self.assertRaises(UserError):
                self.Partner.create({"name": "Baru", "street": "Jl. D"})
            with self.assertRaises(UserError):
                self.person.write({"street": "Jl. E"})
        output = "\n".join(logs.output)
        self.assertIn("field_not_encrypted", output)
        self.assertIn("street", output)
        for plain in ("Jl. D", "Jl. E", "Rina"):
            self.assertNotIn(plain, output)
        # field lain / partner tidak terlindungi tetap bisa ditulis (POS & B2B tidak berhenti)
        self.person.write({"comment": "tanpa PII"})
        company = self.Partner.create({"name": "PT Bebas", "is_company": True, "street": "Jl. F"})
        self.assertEqual(self._raw(company.id, "street")["street"], "Jl. F")

    def test_redefined_compute_field_refuses_dependency_write(self):
        self._break("complete_name")
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"):
            with self.assertRaises(UserError):
                self.person.write({"name": "Rina K"})
        self.person.write({"comment": "boleh"})

    def test_plaintext_on_protected_record_raises_alarm(self):
        self.env.flush_all()
        self.env.cr.execute(SQL("UPDATE res_partner SET street = %s WHERE id = %s", "Jl. Plain 1", self.person.id))
        self.env.invalidate_all()
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
            self.assertEqual(self.person.street, "Jl. Plain 1")
        output = "\n".join(logs.output)
        self.assertIn("plaintext_in_protected", output)
        self.assertIn('"model": "res.partner"', output)
        self.assertNotIn("Jl. Plain 1", output)
        # partner tidak terlindungi dengan plaintext: tanpa alarm
        company = self.Partner.create({"name": "PT Biasa", "is_company": True, "street": "Jl. G"})
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertNoLogs(KEYRING_LOGGER, "CRITICAL"):
            self.assertEqual(company.street, "Jl. G")


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiSec138(PiiKeyMixin, TransactionCase):
    """efnpii2 terikat id; efnpii1 hanya transisi create; baca efnpii1 lama = alarm + migrasi."""

    def setUp(self):
        super().setUp()
        self._pii_env()
        self.Partner = self.env["res.partner"]

    def test_no_efnpii1_left_after_any_create_path(self):
        created = self.Partner.create([{"name": f"Batch {i}", "phone": f"0812 5000 {i:04d}"} for i in range(5)])
        created |= self.Partner.create({"name": "Satu", "email": "satu@example.com"}).copy()
        loaded = self.Partner.load(["name", "phone"], [["Impor B", "0812 5000 9999"]])
        created |= self.Partner.browse(loaded["ids"])
        self.env.flush_all()
        self.env.cr.execute(
            SQL(
                "SELECT count(*) FROM res_partner WHERE id IN %s AND (%s)",
                tuple(created.ids),
                SQL(" OR ").join(SQL("%s LIKE %s", SQL.identifier(c), V1 + "%") for c in PII_FIELDS),
            )
        )
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        for partner in created:
            self.assertBound(self._raw(partner.id, "name")["name"], "res_partner", "name", partner.id)

    def test_legacy_efnpii1_read_alarm_and_migration(self):
        partner = self.Partner.create({"name": "Legacy PoC", "phone": "0812 6000 0001"})
        ring = kr.get_keyring(self.env.cr.dbname)
        legacy_phone = ring.encrypt_text("0812 6000 0001", kr.aad_for("res_partner", "phone"), kr.PREFIX)
        self.env.flush_all()
        # data PoC G19: phone efnpii1 (tanpa id), nama & alamat plaintext
        self.env.cr.execute(
            SQL(
                "UPDATE res_partner SET phone = %s, name = %s, complete_name = %s, city = %s WHERE id = %s",
                legacy_phone,
                "Legacy PoC",
                "Legacy PoC",
                "Kota Lama",
                partner.id,
            )
        )
        self.env.invalidate_all()
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
            self.assertEqual(partner.phone, "0812 6000 0001")
        output = "\n".join(logs.output)
        self.assertIn("unbound_ciphertext", output)
        self.assertIn("plaintext_in_protected", output)
        totals = self.Partner.sudo()._efn_pii_migrate(ids=partner.ids)
        self.assertEqual((totals["partners"], totals["failed"]), (1, 0))
        self.assertGreaterEqual(totals["bound"], 1)
        self.assertGreaterEqual(totals["encrypted"], 3)
        raw = self._raw(partner.id, "phone", "name", "complete_name", "city", "efn_name_tokens")
        self.assertBound(raw["phone"], "res_partner", "phone", partner.id, "0812 6000 0001")
        self.assertBound(raw["name"], "res_partner", "name", partner.id, "Legacy PoC")
        self.assertBound(raw["city"], "res_partner", "city", partner.id, "Kota Lama")
        self.assertTrue(raw["efn_name_tokens"])
        self.env.invalidate_all()
        with self.assertNoLogs(KEYRING_LOGGER, "CRITICAL"):
            self.assertEqual(partner.phone, "0812 6000 0001")
        self.assertIn(partner, self.Partner.search([("name", "ilike", "legacy")]))
