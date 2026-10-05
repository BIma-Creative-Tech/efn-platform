"""member_hash Odoo == BFF (test vector lintas bahasa) + perilaku compute.

Pepper uji = `pepper_hex` turunan HKDF dari otp_pepper uji `test-otp-pepper-v1`
(services/api-gateway/src/config/app-config.spec.ts). Bukan secret nyata.
"""

import os
import tempfile
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged

from odoo.addons.efn_privacy.models.member_hash import (
    PEPPER_FILE_ENV,
    PepperUnavailable,
    member_hash,
    read_pepper,
)

TEST_PEPPER_HEX = "46df3d2fee0e91a91083827a5f303586e9c6b5c54afe096896bac9feb724061f"
TEST_MEMBER_REF = "6281234567890"
TEST_MEMBER_HASH = "8930b421545b77ea6ff5093510c261afe67f1b1e3e18c925cb76d59516926653"


def write_tmp(testcase, content):
    handle, path = tempfile.mkstemp(prefix="efn-pepper-test-")
    with os.fdopen(handle, "w", encoding="ascii") as fh:
        fh.write(content)
    testcase.addCleanup(lambda: os.path.exists(path) and os.remove(path))
    return path


class PepperMixin:
    def _use_pepper(self, content=TEST_PEPPER_HEX + "\n"):
        path = write_tmp(self, content)
        env_patch = patch.dict(os.environ, {PEPPER_FILE_ENV: path})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        return path

    def _no_pepper(self):
        env_patch = patch.dict(os.environ, {PEPPER_FILE_ENV: "/nonexistent/efn-test-pepper"})
        env_patch.start()
        self.addCleanup(env_patch.stop)


@tagged("post_install", "-at_install", "efn")
class TestEfnMemberHash(PepperMixin, TransactionCase):
    def test_shared_test_vector_pure_function(self):
        self.assertEqual(member_hash(TEST_PEPPER_HEX, TEST_MEMBER_REF), TEST_MEMBER_HASH)

    def test_read_pepper_validates_format(self):
        self._use_pepper(TEST_PEPPER_HEX.upper() + "\n")
        self.assertEqual(read_pepper(), TEST_PEPPER_HEX)
        self._use_pepper("bukan-hex")
        with self.assertRaises(PepperUnavailable) as cm:
            read_pepper()
        self.assertEqual(cm.exception.code, "pepper_invalid")
        self._no_pepper()
        with self.assertRaises(PepperUnavailable) as cm:
            read_pepper()
        self.assertEqual(cm.exception.code, "pepper_missing")

    def test_partner_hash_matches_bff_for_all_phone_formats(self):
        self._use_pepper()
        Partner = self.env["res.partner"]
        for phone in ("0812-3456-7890", "+62 812 3456 7890", "6281234567890", "0062 812 3456 7890"):
            with self.subTest(phone=phone):
                partner = Partner.create({"name": "Uji Hash", "phone": phone})
                self.assertEqual(partner.efn_member_hash, TEST_MEMBER_HASH)

    def test_hash_recomputed_when_phone_changes(self):
        self._use_pepper()
        partner = self.env["res.partner"].create({"name": "Uji Ubah", "phone": "081234567890"})
        first = partner.efn_member_hash
        self.assertTrue(first)
        partner.phone = "0812-3456-7890"
        self.assertEqual(partner.efn_member_hash, TEST_MEMBER_HASH)
        self.assertNotEqual(first, TEST_MEMBER_HASH)
        partner.phone = "+65 9123 4567"
        self.assertFalse(partner.efn_member_hash, "nomor non-Indonesia tidak di-hash")
        partner.phone = False
        self.assertFalse(partner.efn_member_hash)

    def test_missing_pepper_never_blocks_partner_save(self):
        self._no_pepper()
        partner = self.env["res.partner"].create({"name": "Tanpa Pepper", "phone": "0812-3456-7890"})
        self.assertTrue(partner.id)
        self.assertFalse(partner.efn_member_hash)

    def test_field_is_indexed_and_admin_only(self):
        self.env.cr.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'res_partner' AND indexdef ILIKE %s",
            ("%efn_member_hash%",),
        )
        self.assertTrue(self.env.cr.fetchone(), "efn_member_hash harus ber-index")
        field = self.env["res.partner"]._fields["efn_member_hash"]
        self.assertTrue(field.store)
        self.assertEqual(field.groups, "base.group_system")

    def test_not_loaded_in_pos(self):
        config = self.env["pos.config"].search([], limit=1)
        fields_list = self.env["res.partner"]._load_pos_data_fields(config)
        self.assertNotIn("efn_member_hash", fields_list)
        self.assertNotIn("efn_erasure_request_id", fields_list)

    def test_recompute_backfills_and_requires_admin(self):
        self._no_pepper()
        partner = self.env["res.partner"].create({"name": "Backfill", "phone": "0812-3456-7890"})
        self.assertFalse(partner.efn_member_hash)
        self._use_pepper()
        count = self.env["res.partner"]._efn_recompute_member_hash()
        self.assertGreaterEqual(count, 1)
        partner.invalidate_recordset(["efn_member_hash"])
        self.assertEqual(partner.efn_member_hash, TEST_MEMBER_HASH)

        user = new_test_user(self.env, login="efn_privacy_nonadmin", groups="base.group_user")
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(user)._efn_recompute_member_hash()
