"""Unit test pembuat token eit3 (G52, docs/04 §28.15.5) + vektor scope sig3 (§28.15.6).

Vektor D-G, N6-N14, dan input sig3 dibaca dari `vectors_g52.json` (sumber tunggal, dipakai juga Vitest insight-web).
`payload_b64`/`sig_b64`/`sig3_hex` belum tercatat di kontrak (architect tanpa runtime): test MENGHITUNG, mencetak ke
log (prefix `EFN_G52_VECTOR`) untuk dibandingkan efn-lead dengan Node, dan meng-assert byte-exact bila nilai di JSON
sudah diisi (bukan null).
"""

import base64
import hashlib
import hmac
import json
import logging
import os

from odoo.tests import TransactionCase, tagged

from odoo.addons.efn_insight.lib import insight_token as it

_logger = logging.getLogger(__name__)

with open(os.path.join(os.path.dirname(__file__), "vectors_g52.json"), encoding="utf-8") as _handle:
    VECTORS = json.load(_handle)

VECTOR_KEY = VECTORS["key_spec"]["unit"] * VECTORS["key_spec"]["repeat"]
NOW = VECTORS["now"]
TOKENS = VECTORS["token_vectors"]

# Masukan builder (sengaja tidak terurut / berduplikat: builder wajib menormalkan).
VECTOR_INPUT = {
    "D": {
        "sub": 21,
        "name": "Andi Toko",
        "codes": ["PB"],
        "cids": [5, 5],
        "roles": ["user", "store"],
        "wids": [],
        "cats": [],
        "brs": ["pb:7", "pb:12", "pb:7"],
        "jti": "0123456789abcdef0123456789abcd01",
    },
    "E": {
        "sub": 7,
        "name": "Budi Uji",
        "codes": ["SMD", "PB", "BCF"],
        "cids": [11, 5, 3],
        "roles": ["user", "exec"],
        "wids": [25, 24, 23, 22, 21, 20, 0],
        "cats": [],
        "brs": [],
        "jti": "0123456789abcdef0123456789abcd02",
    },
    "F": {
        "sub": 30,
        "name": "Maya Promo",
        "codes": ["WTZ", "PB"],
        "cids": [6, 5],
        "roles": ["marketing", "user"],
        "wids": [],
        "cats": [],
        "brs": [],
        "jti": "0123456789abcdef0123456789abcd03",
    },
    "G": {
        "sub": 31,
        "name": "Sinta Toko",
        "codes": ["BCR"],
        "cids": [4],
        "roles": ["store", "user"],
        "wids": [],
        "cats": [],
        "brs": ["bacha:15"],
        "jti": "0123456789abcdef0123456789abcd04",
    },
}


def _build(name, **overrides):
    kwargs = dict(VECTOR_INPUT[name], iat=1795000000)
    kwargs.update(overrides)
    return it.build_payload_v3(**kwargs)


def _b64(text):
    return base64.urlsafe_b64encode(text.encode("ascii")).rstrip(b"=").decode("ascii")


def _sign_raw(payload_obj, prefix="eit3", key=VECTOR_KEY):
    """Tanda tangani payload apa adanya (tanpa validasi builder) — untuk vektor negatif."""
    payload_b64 = _b64(it.canonical_json(payload_obj))
    sig = hmac.new(key.encode("ascii"), f"{prefix}.{payload_b64}".encode("ascii"), hashlib.sha256).digest()
    return f"{prefix}.{payload_b64}.{it.b64url_encode(sig)}"


@tagged("efn", "post_install", "-at_install")
class TestInsightTokenV3(TransactionCase):
    def test_vector_file_shape(self):
        self.assertEqual(VECTOR_KEY, "ab" * 32)
        self.assertEqual(sorted(TOKENS), ["D", "E", "F", "G"])
        self.assertEqual(sorted(VECTORS["negative_vectors"]), sorted(f"N{i}" for i in range(6, 15)))

    # ------------------------------------------------------------------ vektor positif D-G
    def test_vectors_byte_exact_and_printed(self):
        for name, vector in TOKENS.items():
            literal = vector["payload_json"]
            with self.subTest(vector=name):
                payload = _build(name)
                self.assertEqual(it.canonical_json(payload), literal)
                self.assertEqual(len(payload), 14)
                self.assertEqual(set(payload), it.PAYLOAD_KEYS_V3)
                token = it.encode_token_v3(payload, VECTOR_KEY)
                prefix, payload_b64, sig_b64 = token.split(".")
                self.assertEqual(prefix, "eit3")
                # Independen dari builder: base64url literal kontrak + HMAC kunci ASCII (bukan hex-decode).
                self.assertEqual(payload_b64, _b64(literal))
                expected = hmac.new(
                    VECTOR_KEY.encode("ascii"), f"eit3.{payload_b64}".encode("ascii"), hashlib.sha256
                ).digest()
                self.assertEqual(sig_b64, it.b64url_encode(expected))
                self.assertEqual(len(sig_b64), 43)
                self.assertNotIn("=", token)
                self.assertEqual(it.verify_token_v3(token, VECTOR_KEY, now=NOW), json.loads(literal))
                if vector["payload_b64"] is not None:
                    self.assertEqual(payload_b64, vector["payload_b64"])
                if vector["sig_b64"] is not None:
                    self.assertEqual(sig_b64, vector["sig_b64"])
                _logger.info("EFN_G52_VECTOR eit3 %s payload_b64=%s sig_b64=%s", name, payload_b64, sig_b64)

    def test_scope_sig3_vectors(self):
        # Odoo tidak menghitung sig3 di produksi (insight-web mengirim setting ClickHouse). Formula docs/04 §28.15.6
        # dihitung di sini agar Python (hashlib) dapat dibandingkan dengan Node (crypto).
        for name, vector in VECTORS["scope_vectors"].items():
            with self.subTest(vector=name):
                tail = "|" + vector["cids"] + "|" + vector["codes"] + "|" + vector["brs"]
                self.assertEqual(tail, vector["input_after_key"])
                self.assertEqual(tail.count("|"), 3, "pemisahan domain: sig3 tepat 3 pemisah")
                digest = hashlib.sha256((VECTOR_KEY + tail).encode("ascii")).hexdigest()
                self.assertRegex(digest, r"^[0-9a-f]{64}$")
                if vector["sig3_hex"] is not None:
                    self.assertEqual(digest, vector["sig3_hex"])
                _logger.info("EFN_G52_VECTOR sig3 %s input=KEY%s sig3=%s", name, tail, digest)
        # Konsistensi dengan vektor token: D/E -> cids, codes ∩ ESB_CODES, brs.
        for name in ("D", "E"):
            payload = json.loads(TOKENS[name]["payload_json"])
            vector = VECTORS["scope_vectors"][name]
            self.assertEqual(vector["cids"], ",".join(str(c) for c in payload["cids"]))
            self.assertEqual(vector["codes"], ",".join(c for c in payload["codes"] if c in it.ESB_CODES))
            self.assertEqual(vector["brs"], ",".join(payload["brs"]))

    # ------------------------------------------------------------------ vektor negatif N6-N14
    def test_negative_vectors(self):
        for label, spec in VECTORS["negative_vectors"].items():
            payload = {**json.loads(TOKENS[spec["base"]]["payload_json"]), **spec["override"]}
            token = _sign_raw(payload, prefix=spec["prefix"])
            with self.subTest(vector=label):
                if label == "N13":
                    # Prefix eit2 dengan v=3: verifikator eit2 menolak (14 kunci / v), verifikator eit3 menolak format.
                    with self.assertRaisesRegex(ValueError, "bad_claims"):
                        it.verify_token_v2(token, VECTOR_KEY, now=NOW)
                    with self.assertRaisesRegex(ValueError, "bad_format"):
                        it.verify_token_v3(token, VECTOR_KEY, now=NOW)
                    continue
                with self.assertRaisesRegex(ValueError, spec["odoo_error"]):
                    it.verify_token_v3(token, VECTOR_KEY, now=NOW)

    def test_builder_refuses_negative_scopes(self):
        cases = (
            ("D", {"roles": ["marketing", "store", "user"]}, "bad_claims"),  # N6 / T5
            ("D", {"brs": []}, "bad_claims"),  # N7 / T6
            ("F", {"brs": ["pb:7"]}, "bad_claims"),  # N8 / T6
            ("D", {"brs": ["pb:07"]}, "bad_brs"),  # N9
            ("D", {"brs": ["PB:7"]}, "bad_brs"),  # N10
            ("D", {"roles": ["dc", "user"], "brs": []}, "bad_claims"),  # N12 / T3
            ("E", {"brs": ["pb:7"]}, "bad_claims"),  # N14 / T6
        )
        for name, overrides, code in cases:
            with self.subTest(vector=name, overrides=overrides), self.assertRaisesRegex(ValueError, code):
                _build(name, **overrides)
        # N11: builder menormalkan urutan (ASCII), jadi masukan ["pb:7","pb:12"] menghasilkan urutan kontrak.
        self.assertEqual(_build("D", brs=["pb:7", "pb:12"])["brs"], ["pb:12", "pb:7"])

    # ------------------------------------------------------------------ aturan T1-T8
    def test_consistency_rules(self):
        bad = (
            # T1: wids tanpa SM / SM tanpa wids / SM tanpa persona
            ("F", {"wids": [0, 20]}),
            ("E", {"wids": []}),
            ("F", {"codes": ["SMD"], "cids": [11], "roles": ["user"], "wids": [20]}),
            # T2: SM ∧ exec tanpa 0 / store saja dengan 0
            ("E", {"wids": [20, 21]}),
            ("D", {"codes": ["PB", "SMD"], "cids": [5, 11], "wids": [0, 22]}),
            # T3: category tanpa SM
            ("F", {"roles": ["category", "user"]}),
            # T4: persona tanpa SM dan tanpa ESB — tidak mungkin (codes ⊆ ESB ∪ SM, ≥ 1) -> diwakili T3/T1
            # T5: store + marketing/dc/category
            ("D", {"roles": ["dc", "store", "user"], "codes": ["PB", "SMD"], "cids": [5, 11], "wids": [0, 22]}),
            # T6: brs untuk all_brands / tanpa ESB
            ("D", {"roles": ["all_brands", "store"]}),
            ("D", {"codes": ["SMD"], "cids": [11], "wids": [22]}),
            # T7: cats tanpa category
            ("E", {"cats": [4]}),
        )
        for name, overrides in bad:
            with self.subTest(vector=name, overrides=overrides), self.assertRaisesRegex(ValueError, "bad_claims"):
                _build(name, **overrides)
        # Kombinasi sah §28.15.11.
        ok = (
            ("D", {"codes": ["PB", "SMD"], "cids": [5, 11], "wids": [22]}),  # store F&B (brs) + store SMD
            ("E", {"roles": ["exec", "store", "user"]}),  # exec + store: PT penuh, brs kosong
            ("F", {"roles": ["all_brands", "marketing"]}),
            ("F", {"roles": ["all_brands"], "cids": []}),
            ("E", {"roles": ["category", "user"], "cats": [4, 9]}),
        )
        for name, overrides in ok:
            with self.subTest(vector=name, overrides=overrides):
                self.assertEqual(_build(name, **overrides)["v"], 3)

    def test_brs_shape(self):
        for brs, code in (
            (["pb:0"], "bad_brs"),
            (["pb:4294967296"], "bad_brs"),
            (["xx:1"], "bad_brs"),
            (["pb:1", "pb:1"], "bad_brs"),
            ([7], "bad_brs"),
        ):
            payload = {**json.loads(TOKENS["D"]["payload_json"]), "brs": brs}
            with self.subTest(brs=brs), self.assertRaisesRegex(ValueError, code):
                it.validate_claims_v3(payload)
        self.assertEqual(_build("D", brs=["pb:4294967295"])["brs"], ["pb:4294967295"])
        many = [f"pb:{i}" for i in range(1, 52)]
        with self.assertRaisesRegex(ValueError, "bad_brs"):
            _build("D", brs=many)
        self.assertEqual(len(_build("D", brs=many[:50])["brs"]), 50)

    def test_size_limit_t8(self):
        payload = _build("D", brs=[f"bacha:{4294967295 - i}" for i in range(50)], codes=["BCF", "BCR"], cids=[4, 5])
        token = it.encode_token_v3(payload, VECTOR_KEY)
        self.assertLessEqual(len(token.split(".")[1]), it.MAX_PAYLOAD_B64)
        big = _build(
            "E",
            roles=["category", "user"],
            wids=[0, *range(10**9, 10**9 + 199)],
            cats=list(range(10**9, 10**9 + 50)),
        )
        with self.assertRaisesRegex(ValueError, "scope_too_large"):
            it.encode_token_v3(big, VECTOR_KEY)
        with self.assertRaisesRegex(ValueError, "bad_format"):
            it.verify_token_v3(_sign_raw(big), VECTOR_KEY, now=NOW)

    def test_verify_rejects_other_versions_and_keys(self):
        d = json.loads(TOKENS["D"]["payload_json"])
        for payload in (
            {**d, "extra": 1},
            {k: v for k, v in d.items() if k != "brs"},
            {**d, "v": 2},
            {**d, "aud": "lain"},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "bad_claims"):
                it.verify_token_v3(_sign_raw(payload), VECTOR_KEY, now=NOW)
        with self.assertRaisesRegex(ValueError, "expired"):
            it.verify_token_v3(_sign_raw(d), VECTOR_KEY, now=1795000060)
        # Kunci hex-decode -> sig berbeda.
        payload_b64 = _b64(TOKENS["D"]["payload_json"])
        wrong = hmac.new(bytes.fromhex(VECTOR_KEY), f"eit3.{payload_b64}".encode("ascii"), hashlib.sha256).digest()
        with self.assertRaisesRegex(ValueError, "bad_sig"):
            it.verify_token_v3(f"eit3.{payload_b64}.{it.b64url_encode(wrong)}", VECTOR_KEY, now=NOW)
        # eit2 sah tidak diterima verifikator eit3.
        eit2 = it.encode_token_v2(
            it.build_payload_v2(
                sub=7, name="x", codes=["PB"], cids=[5], roles=["user"], wids=[], cats=[], iat=1795000000, jti="a" * 32
            ),
            VECTOR_KEY,
        )
        with self.assertRaisesRegex(ValueError, "bad_format"):
            it.verify_token_v3(eit2, VECTOR_KEY, now=NOW)

    # ------------------------------------------------------------------ aturan persona (§28.15.4 butir 1-2)
    def test_suppress_store_and_store_only(self):
        cases = {
            frozenset({"store"}): ({"store"}, False),
            frozenset({"store", "exec"}): ({"store", "exec"}, False),
            frozenset({"store", "marketing"}): ({"marketing"}, True),
            frozenset({"store", "dc"}): ({"dc"}, True),
            frozenset({"store", "category"}): ({"category"}, True),
            frozenset({"store", "exec", "marketing"}): ({"store", "exec", "marketing"}, False),
            frozenset({"marketing"}): ({"marketing"}, False),
        }
        for held, (expected, suppressed) in cases.items():
            with self.subTest(held=sorted(held)):
                self.assertEqual(it.suppress_store(held), (frozenset(expected), suppressed))
        self.assertTrue(it.is_store_only_fnb({"store"}, False))
        self.assertTrue(it.is_store_only_fnb({"store", "dc"}, False))
        self.assertFalse(it.is_store_only_fnb({"store"}, True))
        self.assertFalse(it.is_store_only_fnb({"store", "exec"}, False))
        self.assertFalse(it.is_store_only_fnb({"store", "marketing"}, False))
        self.assertFalse(it.is_store_only_fnb({"exec"}, False))
