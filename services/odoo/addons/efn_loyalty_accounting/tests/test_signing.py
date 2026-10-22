"""Tanda tangan publikasi kebijakan (docs/04 §13.1 no. 3): format, HMAC atas byte persis, replay window, kunci."""

import hashlib
import hmac
import os
import secrets
import tempfile
from unittest.mock import patch

from odoo.tests import BaseCase, tagged

from odoo.addons.efn_loyalty_accounting.models import signing


@tagged("post_install", "-at_install", "efn")
class TestEfnLoyaltySigning(BaseCase):
    def setUp(self):
        super().setUp()
        self.key = secrets.token_hex(32).encode("ascii")

    def test_header_format_and_hmac_over_exact_body(self):
        body = signing.canonical_json({"b": 1, "a": "x"})
        self.assertEqual(body, b'{"a":"x","b":1}')
        header = signing.sign(self.key, 1_760_000_000, body)
        expected = hmac.new(self.key, b"1760000000." + body, hashlib.sha256).hexdigest()
        self.assertEqual(header, f"t=1760000000,v1={expected}")

    def test_w17_params_canonical_and_tamper_detected(self):
        """G22 W-17: parameter global di ``params`` ikut ditandatangani; mengubah bulan/epoch -> tanda tangan gagal."""
        payload = {
            "params": {"inactivity_expiry_months": 12, "expiry_notice_days": 30, "activity_epoch": "2026-11-01"},
            "company_code": "EFN",
        }
        body = signing.canonical_json(payload)
        self.assertEqual(
            body,
            b'{"company_code":"EFN","params":{"activity_epoch":"2026-11-01","expiry_notice_days":30,'
            b'"inactivity_expiry_months":12}}',
        )
        header = signing.sign(self.key, 1_760_000_000, body)
        for old, new in ((b":12}", b":36}"), (b"2026-11-01", b"2027-11-01")):
            with self.subTest(old=old):
                tampered = body.replace(old, new)
                self.assertNotEqual(tampered, body)
                result = signing.verify_signature(self.key, header, tampered, now=1_760_000_000)
                self.assertEqual(result, (False, "bad_signature"))

    def test_verify_accepts_valid_and_rejects_tamper_replay_and_wrong_key(self):
        body = signing.canonical_json({"version": 3})
        header = signing.sign(self.key, 1_760_000_000, body)
        self.assertEqual(signing.verify_signature(self.key, header, body, now=1_760_000_100), (True, None))
        # Body diubah satu byte -> ditolak.
        tampered = body.replace(b"3", b"4")
        self.assertEqual(signing.verify_signature(self.key, header, tampered, now=1_760_000_100)[1], "bad_signature")
        # Replay di luar jendela 300 s (maju maupun mundur) -> ditolak walau HMAC benar.
        self.assertEqual(signing.verify_signature(self.key, header, body, now=1_760_000_301)[1], "replay_window")
        self.assertEqual(signing.verify_signature(self.key, header, body, now=1_759_999_699)[1], "replay_window")
        self.assertTrue(signing.verify_signature(self.key, header, body, now=1_760_000_300)[0])
        # Kunci lain -> ditolak; header rusak -> malformed.
        other = secrets.token_hex(32).encode("ascii")
        self.assertEqual(signing.verify_signature(other, header, body, now=1_760_000_000)[1], "bad_signature")
        self.assertEqual(signing.verify_signature(self.key, "v1=abc", body, now=1_760_000_000)[1], "malformed")
        # Timestamp dalam header tidak bisa diganti tanpa menghitung ulang HMAC (replay dengan t baru).
        forged = header.replace("t=1760000000", "t=1760000200")
        self.assertEqual(signing.verify_signature(self.key, forged, body, now=1_760_000_200)[1], "bad_signature")

    def test_read_key_missing_short_and_ok(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, directory)
        path = os.path.join(directory, "k")
        with patch.dict(os.environ, {signing.SIGNING_KEY_FILE_ENV: path}):
            with self.assertRaises(signing.SigningKeyMissing) as missing:
                signing.read_signing_key()
            self.assertEqual(missing.exception.code, "signing_key_missing")
            with open(path, "wb") as handle:
                handle.write(b"short\n")
            self.addCleanup(os.unlink, path)
            with self.assertRaises(signing.SigningKeyMissing) as short:
                signing.read_signing_key()
            self.assertEqual(short.exception.code, "signing_key_too_short")
            with open(path, "wb") as handle:
                handle.write(b"  " + self.key + b"\n")
            self.assertEqual(signing.read_signing_key(), self.key)
            # Pesan exception tidak memuat isi kunci.
            self.assertNotIn(self.key.decode(), str(missing.exception) + str(short.exception))
