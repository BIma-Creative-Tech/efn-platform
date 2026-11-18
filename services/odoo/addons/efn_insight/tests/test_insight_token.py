"""Unit test pembuat token eit1 (docs/04 §26.3). Vektor bersama dengan Vitest insight-web (sintetis, bukan secret)."""

import base64
import hashlib
import hmac
import json
import os
import tempfile

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_insight.lib import insight_token as it

# docs/04 §26.3 — vektor uji bersama (diverifikasi efn-lead: Python 3.14 dan Node 22 identik).
VECTOR_KEY = "ab" * 32
VECTOR_JSON = (
    '{"aud":"insight-web","cids":[3,5],"codes":["BCF","PB"],"exp":1795000060,"iat":1795000000,"iss":"odoo",'
    '"jti":"0123456789abcdef0123456789abcdef","name":"Budi Uji","roles":["user"],"sub":7,"v":1}'
)
VECTOR_PAYLOAD_B64 = (
    "eyJhdWQiOiJpbnNpZ2h0LXdlYiIsImNpZHMiOlszLDVdLCJjb2RlcyI6WyJCQ0YiLCJQQiJdLCJleHAiOjE3OTUwMDAwNjAsImlhdCI6MTc5NTAw"
    "MDAwMCwiaXNzIjoib2RvbyIsImp0aSI6IjAxMjM0NTY3ODlhYmNkZWYwMTIzNDU2Nzg5YWJjZGVmIiwibmFtZSI6IkJ1ZGkgVWppIiwicm9sZXMi"
    "OlsidXNlciJdLCJzdWIiOjcsInYiOjF9"
)
VECTOR_SIG_B64 = "WSDaF4H2YlaMeKTB73SwsefWHIQi8ywZb8DB2HnGBZc"
# Sig bila kunci di-hex-decode (SALAH) — tidak boleh pernah dihasilkan.
VECTOR_SIG_HEXDECODED_B64 = "i5CARcXxPFdTmGd6vttMWHrYS0VkI0UHJj07imyQJkM"
VECTOR_JTI = "0123456789abcdef0123456789abcdef"


def _vector_payload(**overrides):
    kwargs = {
        "sub": 7,
        "name": "Budi Uji",
        "codes": ["PB", "BCF"],
        "cids": [5, 3],
        "roles": ["user"],
        "iat": 1795000000,
        "jti": VECTOR_JTI,
    }
    kwargs.update(overrides)
    return it.build_payload(**kwargs)


@tagged("efn", "post_install", "-at_install")
class TestInsightToken(TransactionCase):
    def test_vector_byte_exact(self):
        payload = _vector_payload()
        self.assertEqual(it.canonical_json(payload), VECTOR_JSON)
        token = it.encode_token(payload, VECTOR_KEY)
        self.assertEqual(token, f"eit1.{VECTOR_PAYLOAD_B64}.{VECTOR_SIG_B64}")
        prefix, payload_b64, sig_b64 = token.split(".")
        self.assertEqual(prefix, "eit1")
        self.assertEqual(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)).decode(), VECTOR_JSON)
        self.assertNotIn("=", token)
        self.assertEqual(len(sig_b64), 43)

    def test_key_is_ascii_not_hex_decoded(self):
        token = it.encode_token(_vector_payload(), VECTOR_KEY)
        sig_b64 = token.rsplit(".", 1)[1]
        self.assertNotEqual(sig_b64, VECTOR_SIG_HEXDECODED_B64)
        wrong = hmac.new(bytes.fromhex(VECTOR_KEY), f"eit1.{VECTOR_PAYLOAD_B64}".encode(), hashlib.sha256).digest()
        self.assertEqual(it.b64url_encode(wrong), VECTOR_SIG_HEXDECODED_B64)

    def test_round_trip_and_tamper(self):
        token = it.encode_token(_vector_payload(), VECTOR_KEY)
        payload = it.verify_token(token, VECTOR_KEY, now=1795000010)
        self.assertEqual(payload["codes"], ["BCF", "PB"])
        self.assertEqual(payload["exp"] - payload["iat"], 60)
        with self.assertRaisesRegex(ValueError, "bad_sig"):
            it.verify_token(token, "cd" * 32, now=1795000010)
        prefix, payload_b64, sig_b64 = token.split(".")
        forged = json.loads(VECTOR_JSON)
        forged["codes"] = ["BCF", "PB", "WTZ"]
        forged_b64 = it.b64url_encode(it.canonical_json(forged).encode())
        with self.assertRaisesRegex(ValueError, "bad_sig"):
            it.verify_token(f"{prefix}.{forged_b64}.{sig_b64}", VECTOR_KEY, now=1795000010)
        with self.assertRaisesRegex(ValueError, "expired"):
            it.verify_token(token, VECTOR_KEY, now=1795000060)
        with self.assertRaisesRegex(ValueError, "bad_format"):
            it.verify_token("eit2." + token[5:], VECTOR_KEY, now=1795000010)

    def test_verify_rejects_malformed_claims(self):
        # Rujukan verifikasi harus sama ketatnya dengan insight-web (tepat 11 kunci, aud/iss/v, 1 <= exp-iat <= 60).
        base = json.loads(VECTOR_JSON)

        def signed(payload):
            return self._sign_b64(it.b64url_encode(it.canonical_json(payload).encode("ascii")))

        cases = {
            "bad_claims": (
                {**base, "extra": 1},
                {k: v for k, v in base.items() if k != "name"},
                {**base, "aud": "lain"},
                {**base, "iss": "bukan-odoo"},
                {**base, "v": 2},
            ),
            "expired": (
                {**base, "exp": base["iat"]},  # umur 0 (< 1)
                {**base, "exp": base["iat"] + 61},  # umur > 60
                {**base, "iat": 1795000040, "exp": 1795000070},  # iat > now + 30
            ),
            "bad_codes": ({**base, "codes": ["PB", "GL"]},),
        }
        for expected, payloads in cases.items():
            for payload in payloads:
                with self.subTest(expected=expected, payload=payload), self.assertRaisesRegex(ValueError, expected):
                    it.verify_token(signed(payload), VECTOR_KEY, now=1795000005)

    @staticmethod
    def _sign_b64(payload_b64):
        return f"eit1.{payload_b64}.{it.b64url_encode(it.sign(f'eit1.{payload_b64}', VECTOR_KEY))}"

    def test_verify_rejects_padded_or_non_base64url(self):
        token = it.encode_token(_vector_payload(), VECTOR_KEY)
        prefix, payload_b64, sig_b64 = token.split(".")
        for bad_sig in (sig_b64 + "=", sig_b64[:-1] + "+", sig_b64[:-1] + "/", ""):
            with self.subTest(sig=bad_sig), self.assertRaises(ValueError):
                it.verify_token(f"{prefix}.{payload_b64}.{bad_sig}", VECTOR_KEY, now=1795000010)

    def test_payload_keys_and_no_pii(self):
        payload = _vector_payload()
        self.assertEqual(set(payload), it.PAYLOAD_KEYS)
        for forbidden in ("login", "email", "phone", "mobile"):
            self.assertNotIn(forbidden, payload)

    def test_name_rules(self):
        self.assertEqual(it.clean_name("  Budi \t\n  Uji  "), "Budi Uji")
        self.assertEqual(it.clean_name("Bu​di\x00 Uji"), "BudiUji")  # Cf, Cc, Zl dihapus
        self.assertEqual(it.clean_name(""), "-")
        self.assertEqual(it.clean_name(None), "-")
        self.assertEqual(it.clean_name("​ "), "-")
        self.assertEqual(len(it.clean_name("é" * 100)), 64)
        payload = _vector_payload(name="Sánchez")
        self.assertIn('"name":"S\\u00e1nchez"', it.canonical_json(payload))

    def test_build_payload_rejects_invalid(self):
        for overrides in (
            {"codes": []},
            {"codes": ["PB", "GL"]},
            {"codes": ["EFN"]},
            {"roles": ["admin"]},
            {"roles": ["user", "all_brands"]},
            {"roles": ["user"], "cids": []},
            {"cids": [0]},
            {"cids": list(range(1, 34))},
            {"sub": 0},
            {"sub": 2**31},
            {"sub": True},
            {"jti": "ABC"},
            {"iat": 0},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                _vector_payload(**overrides)
        payload = _vector_payload(roles=["all_brands"], cids=[], codes=sorted(it.ESB_CODES))
        self.assertEqual(payload["codes"], ["BCF", "BCR", "CUP", "IMD", "PB", "WTZ"])
        self.assertEqual(payload["cids"], [])

    def test_load_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "k")
            for content, expected in (
                (VECTOR_KEY + "\n", VECTOR_KEY),
                ("  " + VECTOR_KEY.upper() + "  ", VECTOR_KEY.upper()),
                ("ab" * 31, None),  # 62 < 64
                ("zz" * 32, None),
                ("", None),
            ):
                with open(path, "w", encoding="ascii") as handle:
                    handle.write(content)
                with self.subTest(content_len=len(content)):
                    self.assertEqual(it.load_key(path), expected)
            self.assertIsNone(it.load_key(os.path.join(tmp, "tidak-ada")))
        with self.assertRaises(ValueError):
            it.sign("eit1.x", "ab" * 10)

    def test_parse_insight_url(self):
        self.assertEqual(it.parse_insight_url("https://insight.test"), "https://insight.test")
        self.assertEqual(it.parse_insight_url(" https://Insight.Test:8443/ "), "https://insight.test:8443")
        for bad in (
            "",
            None,
            "http://insight.test",
            "https://insight.test/app",
            "https://insight.test?x=1",
            "https://insight.test#x",
            "https://user:pw@insight.test",
            'https://insight.test/"><script>',
            "https://insight.test; script-src *",
            "https://insight.test:99999",
            "javascript:alert(1)",
        ):
            with self.subTest(url=bad):
                self.assertIsNone(it.parse_insight_url(bad))
        self.assertEqual(it.parse_insight_url("http://insight:3002", allow_plaintext=True), "http://insight:3002")
