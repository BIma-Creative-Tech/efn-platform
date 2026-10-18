"""AC-2 / L-39 (b): vektor bersama BFF <-> Odoo dengan AAD EFN (efnpii1 & efnpii2), blind index, token nama.

Dihitung ulang secara deterministik dari label publik (tools/shared_vectors.py) dan diverifikasi dengan
implementasi independen (AESGCM langsung, HKDF/HMAC stdlib). Bila `expected_*` di JSON masih null, JSON
lengkap dicetak ke log dengan penanda `EFN_PII_SHARED_VECTORS` untuk disalin ke
`tests/data/efn_pii_shared_vectors.json` (lihat README).
"""

import base64
import hashlib
import hmac
import json
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from odoo.tests import TransactionCase, tagged
from odoo.tools import file_path

from odoo.addons.efn_pii.models import keyring as kr
from odoo.addons.efn_pii.tools import name_tokens as nt
from odoo.addons.efn_pii.tools import shared_vectors as sv

from .test_pii_crypto import manual_hkdf_sha256

_logger = logging.getLogger(__name__)

VECTORS_FILE = "efn_pii/tests/data/efn_pii_shared_vectors.json"


def load_shared():
    with open(file_path(VECTORS_FILE), encoding="utf-8") as handle:
        return json.load(handle)


@tagged("post_install", "-at_install", "efn", "efn_pii")
class TestEfnPiiSharedVectors(TransactionCase):
    def setUp(self):
        super().setUp()
        self.generated = sv.build(kr, nt)
        self.stored = load_shared()

    def test_inputs_match_file(self):
        self.assertEqual(self.stored["labels"], self.generated["labels"])
        self.assertEqual(self.stored["format"], self.generated["format"])
        for key, fields_ in (
            ("aead", ("name", "table", "column", "record_id", "aad", "plaintext", "iv_label")),
            ("bidx", ("field", "info", "normalized")),
            ("name_tokens", ("kind", "info", "normalized")),
        ):
            self.assertEqual(len(self.stored[key]), len(self.generated[key]), key)
            for stored, generated in zip(self.stored[key], self.generated[key], strict=True):
                for field in fields_:
                    self.assertEqual(stored[field], generated[field], f"{key}.{field}")

    def test_expected_values_or_emit(self):
        pending = [
            item
            for key, field in (("aead", "expected_text"), ("bidx", "expected_hex"), ("name_tokens", "expected"))
            for item in self.stored[key]
            if item.get(field) is None
        ]
        if pending:
            output = dict(self.generated)
            output.pop("normalize_actual", None)
            _logger.info("EFN_PII_SHARED_VECTORS %s", json.dumps(output, ensure_ascii=False, sort_keys=False))
            return
        for key, field in (("aead", "expected_text"), ("bidx", "expected_hex"), ("name_tokens", "expected")):
            for stored, generated in zip(self.stored[key], self.generated[key], strict=True):
                self.assertEqual(stored[field], generated[field], f"{key}.{field}")

    def test_independent_aead_and_record_binding(self):
        key = hashlib.sha256(sv.AES_LABEL.encode()).digest()
        ring = kr.KeyRing({1: key}, 1, b"\x00" * 32, name_key=b"\x00" * 32)
        for vec in self.generated["aead"]:
            with self.subTest(vec["name"]):
                text = vec["expected_text"]
                prefix = "efnpii1:" if vec["record_id"] is None else "efnpii2:"
                self.assertTrue(text.startswith(prefix))
                blob = base64.b64decode(text[8:])
                iv = hashlib.sha256(vec["iv_label"].encode()).digest()[:12]
                self.assertEqual(blob[0], 1)
                self.assertEqual(blob[1:13], iv)
                plain = AESGCM(key).decrypt(iv, blob[13:], vec["aad"].encode())
                self.assertEqual(plain.decode(), vec["plaintext"])
                self.assertEqual(ring.decrypt_text(text, vec["aad"]), vec["plaintext"])
                if vec["record_id"] is not None:
                    wrong = kr.aad_for(vec["table"], vec["column"], vec["record_id"] + 1)
                    with self.assertRaises(kr.PiiError):
                        ring.decrypt_text(text, wrong)

    def test_independent_bidx_and_tokens(self):
        bidx_ikm = hashlib.sha256(sv.BIDX_LABEL.encode()).digest()
        for vec in self.generated["bidx"]:
            subkey = manual_hkdf_sha256(bidx_ikm, vec["info"].encode(), 32)
            expected = hmac.new(subkey, vec["normalized"].encode(), hashlib.sha256).hexdigest()
            self.assertEqual(vec["expected_hex"], expected)
        name_ikm = hashlib.sha256(sv.NAME_LABEL.encode()).digest()
        for vec in self.generated["name_tokens"]:
            subkey = manual_hkdf_sha256(name_ikm, vec["info"].encode(), 32)
            digest = hmac.new(subkey, vec["normalized"].encode(), hashlib.sha256).digest()[:16]
            expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            self.assertEqual(vec["expected"], expected)
            self.assertEqual(len(expected), 22)

    def test_normalize_vectors(self):
        for item in self.stored["normalize"]:
            with self.subTest(item["input"]):
                self.assertEqual(nt.normalize(item["input"]), item["expected"])
