"""Unit test pembuat token eit2 (G47, docs/04 §28.13.4) + vektor scope sig2 (§28.13.6).

Vektor A/B/C sintetis (bukan secret), dibagi dengan Vitest insight-web. Nilai `payload_b64`/`sig_b64`/sig2 belum
tercatat di kontrak (architect tanpa runtime): test ini MENGHITUNG lalu mencetaknya ke log dengan prefix
`EFN_G47_VECTOR` (pola G42) agar efn-lead membandingkannya dengan builder Node dan menuliskannya di docs/04 §28.13.4.
Setelah nilai disepakati, tambahkan konstanta di sini (assert byte-exact) seperti test_insight_token.py.
"""

import base64
import hashlib
import hmac
import json
import logging

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_insight.controllers.main import merge_scope
from odoo.addons.efn_insight.lib import insight_token as it

_logger = logging.getLogger(__name__)

VECTOR_KEY = "ab" * 32  # sintetis, sama dengan §26.3
NOW = 1795000010

# docs/04 §28.13.4 — salinan literal kontrak (urutan kunci = sort_keys).
VECTOR_JSON = {
    "A": (
        '{"aud":"insight-web","cats":[],"cids":[3,5,11],"codes":["BCF","PB","SMD"],"exp":1795000060,"iat":1795000000,'
        '"iss":"odoo","jti":"0123456789abcdef0123456789abcdef","name":"Budi Uji","roles":["exec","store","user"],'
        '"sub":7,"v":2,"wids":[0,20,21,22,23,24,25]}'
    ),
    "B": (
        '{"aud":"insight-web","cats":[],"cids":[11],"codes":["SMD"],"exp":1795000060,"iat":1795000000,"iss":"odoo",'
        '"jti":"fedcba9876543210fedcba9876543210","name":"Sari Toko","roles":["store","user"],"sub":9,"v":2,'
        '"wids":[22]}'
    ),
    "C": (
        '{"aud":"insight-web","cats":[4,9],"cids":[11],"codes":["SMD"],"exp":1795000060,"iat":1795000000,'
        '"iss":"odoo","jti":"00112233445566778899aabbccddeeff","name":"Rina Buyer","roles":["category","user"],'
        '"sub":12,"v":2,"wids":[0,20,21,22,23,24,25]}'
    ),
}
# Masukan builder (sengaja tidak terurut / berduplikat: builder wajib menormalkan).
VECTOR_INPUT = {
    "A": {
        "sub": 7,
        "name": "Budi Uji",
        "codes": ["SMD", "PB", "BCF"],
        "cids": [11, 5, 3, 5],
        "roles": ["user", "store", "exec"],
        "wids": [25, 24, 23, 22, 21, 20, 0],
        "cats": [],
        "jti": "0123456789abcdef0123456789abcdef",
    },
    "B": {
        "sub": 9,
        "name": "Sari Toko",
        "codes": ["SMD"],
        "cids": [11],
        "roles": ["store", "user"],
        "wids": [22],
        "cats": [],
        "jti": "fedcba9876543210fedcba9876543210",
    },
    "C": {
        "sub": 12,
        "name": "Rina Buyer",
        "codes": ["SMD"],
        "cids": [11],
        "roles": ["category", "user"],
        "wids": [0, 20, 21, 22, 23, 24, 25],
        "cats": [9, 4],
        "jti": "00112233445566778899aabbccddeeff",
    },
}
# docs/04 §28.13.6 — string masukan sig2 (setelah KEY).
SCOPE_VECTORS = {
    "A": "|3,5,11|SMD|0,20,21,22,23,24,25|exec,store|",
    "C": "|11|SMD|0,20,21,22,23,24,25|category|4,9",
}


def _build(name, **overrides):
    kwargs = dict(VECTOR_INPUT[name], iat=1795000000)
    kwargs.update(overrides)
    return it.build_payload_v2(**kwargs)


def _b64(text):
    return base64.urlsafe_b64encode(text.encode("ascii")).rstrip(b"=").decode("ascii")


def _sign_raw(payload_obj, prefix="eit2", key=VECTOR_KEY):
    """Tanda tangani payload apa adanya (tanpa validasi builder) — untuk vektor negatif."""
    payload_b64 = _b64(it.canonical_json(payload_obj))
    sig = hmac.new(key.encode("ascii"), f"{prefix}.{payload_b64}".encode("ascii"), hashlib.sha256).digest()
    return f"{prefix}.{payload_b64}.{it.b64url_encode(sig)}"


@tagged("efn", "post_install", "-at_install")
class TestInsightTokenV2(TransactionCase):
    # ------------------------------------------------------------------ vektor positif
    def test_vectors_byte_exact_and_printed(self):
        for name, literal in VECTOR_JSON.items():
            with self.subTest(vector=name):
                payload = _build(name)
                self.assertEqual(it.canonical_json(payload), literal)
                token = it.encode_token_v2(payload, VECTOR_KEY)
                prefix, payload_b64, sig_b64 = token.split(".")
                self.assertEqual(prefix, "eit2")
                # Independen dari builder: base64url literal kontrak + HMAC kunci ASCII.
                self.assertEqual(payload_b64, _b64(literal))
                expected = hmac.new(
                    VECTOR_KEY.encode("ascii"), f"eit2.{payload_b64}".encode("ascii"), hashlib.sha256
                ).digest()
                self.assertEqual(sig_b64, it.b64url_encode(expected))
                self.assertEqual(len(sig_b64), 43)
                self.assertNotIn("=", token)
                self.assertEqual(it.verify_token_v2(token, VECTOR_KEY, now=NOW), json.loads(literal))
                wrong = hmac.new(bytes.fromhex(VECTOR_KEY), f"eit2.{payload_b64}".encode("ascii"), hashlib.sha256)
                _logger.info(
                    "EFN_G47_VECTOR eit2 %s payload_b64=%s sig_b64=%s sig_hexdecoded_N1=%s",
                    name,
                    payload_b64,
                    sig_b64,
                    it.b64url_encode(wrong.digest()),
                )

    def test_scope_sig2_vectors_printed(self):
        # Odoo tidak menghitung sig2 di produksi (insight-web yang mengirim setting ClickHouse); nilai dicetak agar
        # Python (hashlib) dapat dibandingkan dengan Node (crypto) — docs/04 §28.13.6.
        for name, tail in SCOPE_VECTORS.items():
            digest = hashlib.sha256((VECTOR_KEY + tail).encode("ascii")).hexdigest()
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(tail.count("|"), 5)
            _logger.info("EFN_G47_VECTOR sig2 %s input=KEY%s sig2=%s", name, tail, digest)

    # ------------------------------------------------------------------ vektor negatif (§28.13.4)
    def test_negative_vectors(self):
        a, b = json.loads(VECTOR_JSON["A"]), json.loads(VECTOR_JSON["B"])
        # N1: kunci hex-decode -> sig berbeda -> bad_sig.
        token = it.encode_token_v2(_build("A"), VECTOR_KEY)
        payload_b64 = token.split(".")[1]
        wrong = hmac.new(bytes.fromhex(VECTOR_KEY), f"eit2.{payload_b64}".encode("ascii"), hashlib.sha256).digest()
        self.assertNotEqual(token.split(".")[2], it.b64url_encode(wrong))
        with self.assertRaisesRegex(ValueError, "bad_sig"):
            it.verify_token_v2(f"eit2.{payload_b64}.{it.b64url_encode(wrong)}", VECTOR_KEY, now=NOW)
        cases = {
            "N2": {**a, "wids": [20, 21, 22, 23, 24, 25]},
            "N3": {**b, "wids": [0, 22]},
            "N4": {**a, "v": 1},
            "N5": {**b, "codes": ["SMD"], "wids": []},
        }
        for label, payload in cases.items():
            with self.subTest(vector=label), self.assertRaisesRegex(ValueError, "bad_claims"):
                it.verify_token_v2(_sign_raw(payload), VECTOR_KEY, now=NOW)
        # Pembuat menolak membuat N2/N3/N5.
        for overrides in (
            {"wids": [20, 21, 22, 23, 24, 25]},
            {"roles": ["store", "user"], "wids": [0, 22]},
            {"wids": []},
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "bad_claims"):
                _build("A", **overrides)

    def test_consistency_rules(self):
        bad = (
            # 1: wids ada tanpa persona / tanpa kode SM
            {"roles": ["user"], "wids": [22], "codes": ["SMD"], "cids": [11]},
            {"codes": ["PB"], "cids": [3], "roles": ["store", "user"], "wids": [22]},
            # 1: kode SM tanpa persona
            {"codes": ["SMD"], "cids": [11], "roles": ["user"], "wids": []},
            # 2: 0 untuk store saja / org tanpa 0
            {"roles": ["store", "user"], "wids": [0, 22]},
            {"roles": ["dc", "user"], "wids": [20]},
            # 3: cats tanpa category
            {"roles": ["exec", "user"], "wids": [0, 20], "cats": [4]},
        )
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, "bad_claims"):
                _build("B", **overrides)
        # Persona tingkat PT saja (tanpa store) sah.
        for persona in ("exec", "category", "dc", "marketing"):
            payload = _build("B", roles=[persona, "user"], wids=[0, 20])
            self.assertEqual(payload["roles"], sorted([persona, "user"]))

    def test_payload_key_set_and_roles(self):
        payload = _build("A")
        self.assertEqual(set(payload), it.PAYLOAD_KEYS_V2)
        self.assertEqual(len(payload), 13)
        for forbidden in ("login", "email", "phone", "mobile"):
            self.assertNotIn(forbidden, payload)
        for overrides, code in (
            ({"roles": ["exec", "store"]}, "bad_roles"),  # tanpa user/all_brands
            ({"roles": ["all_brands", "exec", "store", "user"]}, "bad_roles"),  # dua basis
            ({"roles": ["admin", "exec", "user"]}, "bad_roles"),
            ({"codes": ["GL", "SMD"]}, "bad_codes"),
            ({"codes": []}, "bad_codes"),
            ({"cids": []}, "bad_cids"),
            ({"cids": [0, 11]}, "bad_cids"),
            ({"wids": [-1, 0, 20]}, "bad_wids"),
            ({"wids": [0] + list(range(1, 201))}, "bad_wids"),
            ({"cats": [0]}, "bad_cats"),
            ({"sub": 0}, "bad_sub"),
            ({"jti": "XYZ"}, "bad_jti"),
        ):
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, code):
                _build("A", **overrides)
        cats51 = list(range(1, 52))
        with self.assertRaisesRegex(ValueError, "bad_cats"):
            _build("C", cats=cats51)
        # all_brands tanpa persona: cids boleh kosong, wids kosong.
        payload = it.build_payload_v2(
            sub=1,
            name="x",
            codes=sorted(it.ESB_CODES),
            cids=[],
            roles=["all_brands"],
            wids=[],
            cats=[],
            iat=1,
            jti="a" * 32,
        )
        self.assertEqual(payload["cids"], [])

    def test_size_limits(self):
        # Batas atas sah: 200 wids + 50 cats kecil masih < 3000.
        payload = _build("C", wids=[0, *range(1, 200)], cats=list(range(1, 51)))
        token = it.encode_token_v2(payload, VECTOR_KEY)
        self.assertLessEqual(len(token.split(".")[1]), it.MAX_PAYLOAD_B64)
        self.assertEqual(it.verify_token_v2(token, VECTOR_KEY, now=NOW)["wids"][-1], 199)
        # id besar -> payload_b64 > 3000 -> scope_too_large (pembuat menolak).
        big = _build("C", wids=[0, *range(10**9, 10**9 + 199)], cats=list(range(10**9, 10**9 + 50)))
        with self.assertRaisesRegex(ValueError, "scope_too_large"):
            it.encode_token_v2(big, VECTOR_KEY)
        # Penerima: regex membatasi 3000 karakter.
        with self.assertRaisesRegex(ValueError, "bad_format"):
            it.verify_token_v2(_sign_raw(big), VECTOR_KEY, now=NOW)

    def test_verify_rejects_eit1_and_extra_keys(self):
        token = it.encode_token(
            it.build_payload(sub=7, name="x", codes=["PB"], cids=[3], roles=["user"], iat=1795000000, jti="a" * 32),
            VECTOR_KEY,
        )
        with self.assertRaisesRegex(ValueError, "bad_format"):
            it.verify_token_v2(token, VECTOR_KEY, now=NOW)
        a = json.loads(VECTOR_JSON["A"])
        for payload in ({**a, "extra": 1}, {k: v for k, v in a.items() if k != "cats"}, {**a, "aud": "lain"}):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "bad_claims"):
                it.verify_token_v2(_sign_raw(payload), VECTOR_KEY, now=NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            it.verify_token_v2(_sign_raw(a), VECTOR_KEY, now=1795000060)

    def test_merge_scope(self):
        esb = (["PB", "BCF"], [5, 3], ["user"])
        sm = {"personas": ["store", "exec"], "codes": ["SMD"], "cids": [11], "wids": [25, 0, 20], "cats": [9, 4]}
        merged = merge_scope(esb, sm)
        self.assertEqual(
            merged,
            {
                "codes": ["BCF", "PB", "SMD"],
                "cids": [3, 5, 11],
                "roles": ["exec", "store", "user"],
                "wids": [0, 20, 25],
                "cats": [4, 9],
            },
        )
        self.assertEqual(merge_scope(esb, {})["roles"], ["user"])
