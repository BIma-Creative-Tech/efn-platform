"""Format blob, test vector bersama BFF, HKDF/blind index, pemuatan key ring (ADR-036 §4.2-§4.4)."""

import base64
import hashlib
import hmac
import json
import math
import os
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

from odoo.addons.efn_pii.models import keyring as kr

from .common import KEYRING_LOGGER, PiiKeyMixin

VECTORS_FILE = "efn_pii/tests/data/efn_pii_test_vectors.json"


def load_vectors():
    with open(file_path(VECTORS_FILE), encoding="utf-8") as handle:
        return json.load(handle)


def manual_hkdf_sha256(ikm, info, length):
    """RFC 5869 ditulis ulang dengan hmac stdlib (implementasi independen dari `cryptography`)."""
    prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


@tagged("post_install", "-at_install", "efn", "efn_pii_poc")
class TestEfnPiiCrypto(PiiKeyMixin, TransactionCase):
    def test_shared_aead_vectors_seal_and_open(self):
        """Kriteria 4: blob Odoo == blob kontrak docs/04 §7.2 untuk vektor publik (dipakai juga BFF)."""
        data = load_vectors()
        self.assertGreaterEqual(len(data["aead_vectors"]), 2)
        for vec in data["aead_vectors"]:
            with self.subTest(vec["name"]):
                key = bytes.fromhex(vec["k_hex"])
                iv = bytes.fromhex(vec["iv_hex"])
                plaintext = bytes.fromhex(vec["plaintext_hex"])
                aad = bytes.fromhex(vec["aad_hex"])
                expected = bytes.fromhex(vec["expected_blob_hex"])
                blob = kr.seal(key, vec["key_version"], iv, plaintext, aad)
                self.assertEqual(blob.hex(), expected.hex())
                self.assertEqual(blob[0], vec["key_version"])
                self.assertEqual(blob[1:13], iv)
                self.assertEqual(len(blob), 1 + 12 + len(plaintext) + 16)
                # dekripsi dengan key ring berisi versi lain juga (pemilihan kunci dari byte versi)
                ring = {vec["key_version"]: key, 200: os.urandom(32)}
                self.assertEqual(kr.open_blob(ring, expected, aad), plaintext)
                text = kr.wrap(expected)
                self.assertTrue(text.startswith("efnpii1:"))
                self.assertEqual(text, "efnpii1:" + base64.b64encode(expected).decode("ascii"))
                self.assertEqual(kr.unwrap(text), expected)
                self.assertEqual(kr.version_of(text), vec["key_version"])

    def test_shared_vector_tc14_via_public_api(self):
        """TC14 (plaintext 16 NUL, AAD kosong) lewat API teks: setara PiiCipher.decrypt(blob, '') BFF."""
        vec = load_vectors()["aead_vectors"][0]
        ring = kr.KeyRing({1: bytes.fromhex(vec["k_hex"])}, 1, os.urandom(32))
        text = kr.wrap(bytes.fromhex(vec["expected_blob_hex"]))
        self.assertEqual(ring.decrypt_text(text, ""), "\x00" * 16)

    def test_hkdf_rfc5869_vector_and_independent_blind_index(self):
        for vec in load_vectors()["hkdf_vectors"]:
            ikm, info = bytes.fromhex(vec["ikm_hex"]), bytes.fromhex(vec["info_hex"])
            self.assertEqual(kr.hkdf_sha256(ikm, info, vec["length"]).hex(), vec["expected_okm_hex"])
            self.assertEqual(manual_hkdf_sha256(ikm, info, vec["length"]).hex(), vec["expected_okm_hex"])
        bidx_key = os.urandom(32)
        ring = kr.KeyRing({1: os.urandom(32)}, 1, bidx_key)
        subkey = manual_hkdf_sha256(bidx_key, b"efn:odoo:bidx:phone:v1", 32)
        expected = hmac.new(subkey, b"6281234567890", hashlib.sha256).hexdigest()
        self.assertEqual(ring.blind_index("phone", "6281234567890"), expected)
        # subkunci per field: nilai sama di field lain -> blind index berbeda
        self.assertNotEqual(ring.blind_index("vat", "6281234567890"), expected)

    def test_text_format_random_iv_length_and_aad(self):
        ring = kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32))
        aad = kr.aad_for("res_partner", "phone")
        self.assertEqual(aad, "efn:odoo:res_partner.phone:v1")
        with self.assertRaises(ValueError):
            kr.aad_for("Res.Partner", "phone")
        plain = "6281234567890"
        first, second = ring.encrypt_text(plain, aad), ring.encrypt_text(plain, aad)
        self.assertNotEqual(first, second)  # IV acak
        self.assertNotIn(plain, first)
        # ADR-036 §4.2: panjang = 8 + ceil((29 + n) / 3) * 4
        self.assertEqual(len(first), 8 + math.ceil((29 + len(plain)) / 3) * 4)
        self.assertEqual(ring.decrypt_text(first, aad), plain)
        # AAD kolom lain (ciphertext dipindah ke kolom email) -> gagal autentikasi
        with self.assertRaises(kr.PiiError) as cm:
            ring.decrypt_text(first, kr.aad_for("res_partner", "email"))
        self.assertEqual(cm.exception.code, "auth_failed")
        # AAD BFF (tanpa prefix odoo:) juga ditolak
        with self.assertRaises(kr.PiiError):
            ring.decrypt_text(first, "efn:res_partner.phone:v1")
        # kunci lain
        other = kr.KeyRing({1: os.urandom(32)}, 1, os.urandom(32))
        with self.assertRaises(kr.PiiError) as cm:
            other.decrypt_text(first, aad)
        self.assertNotIn(plain, str(cm.exception))
        # versi tidak dikenal
        blob = bytearray(kr.unwrap(first))
        blob[0] = 9
        with self.assertRaises(kr.PiiError) as cm:
            ring.decrypt_text(kr.wrap(bytes(blob)), aad)
        self.assertEqual(cm.exception.code, "version_unknown")

    def test_keyring_loading_codes(self):
        self._pii_env()
        ring = kr._build(*kr._candidate_paths())
        self.assertTrue(ring.can_encrypt)
        self.assertEqual(ring.loaded_versions, [1])
        self.assertEqual(ring.active_version, 1)
        # kunci bukan 32 byte
        self._pii_env(keys={}, raw_files={"odoo_pii_key_v1": base64.b64encode(os.urandom(16)).decode()})
        ring = kr._build(*kr._candidate_paths())
        self.assertFalse(ring.can_encrypt)
        self.assertEqual(ring.error_code, "key_invalid")
        # tidak ada kunci aktif
        self._pii_env(keys={})
        ring = kr._build(*kr._candidate_paths())
        self.assertEqual(ring.error_code, "key_missing")
        # kunci blind index hilang -> tidak boleh menulis (mode terkunci), tetapi bisa baca
        self._pii_env(with_bidx=False)
        ring = kr._build(*kr._candidate_paths())
        self.assertFalse(ring.can_encrypt)
        self.assertEqual(ring.loaded_versions, [1])
        # versi aktif 2 dengan kunci v1+v2
        self._pii_env(keys={1: os.urandom(32), 2: os.urandom(32)}, active=2)
        ring = kr._build(*kr._candidate_paths())
        self.assertEqual(ring.active_version, 2)
        self.assertEqual(kr.version_of(ring.encrypt_text("x", "a")), 2)

    def test_lost_key_detected_without_restart_is_critical(self):
        folder, _keys, _bidx = self._pii_env()
        self.assertTrue(kr.get_keyring(self.env.cr.dbname).can_encrypt)
        os.remove(os.path.join(folder, "odoo_pii_key_v1"))
        with patch.object(kr, "RECHECK_SECONDS", 0.0):
            with self.assertLogs(KEYRING_LOGGER, "CRITICAL") as logs:
                ring = kr.get_keyring(self.env.cr.dbname)
        self.assertFalse(ring.can_encrypt)
        payload = json.loads(logs.output[0].split("EFN_PII_KEYRING ", 1)[1])
        self.assertEqual(
            (payload["event"], payload["status"], payload["error_code"]), ("locked", "locked", "key_missing")
        )

    def test_required_without_keys_is_critical(self):
        self._pii_env(keys={}, with_bidx=False, extra_env={kr.ENV_REQUIRED: "true"})
        with self.assertLogs(KEYRING_LOGGER, "CRITICAL"):
            self.assertFalse(kr.get_keyring(self.env.cr.dbname).can_encrypt)

    def test_keyring_log_has_no_secret(self):
        _folder, keys, bidx_key = self._pii_env()
        with self.assertLogs("odoo.addons.efn_pii.models.keyring", "INFO") as logs:
            kr.get_keyring(self.env.cr.dbname)
        output = "\n".join(logs.output)
        self.assertIn("EFN_PII_KEYRING", output)
        payload = json.loads(output.split("EFN_PII_KEYRING ", 1)[1])
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["loaded_versions"], [1])
        for secret in (keys[1], bidx_key):
            self.assertNotIn(base64.b64encode(secret).decode(), output)
            self.assertNotIn(secret.hex(), output)
