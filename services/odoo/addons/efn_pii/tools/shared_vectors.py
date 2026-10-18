"""Vektor uji bersama BFF <-> Odoo dengan AAD EFN (ADR-036 §0.3, AC-2 / L-39 b). Murni Python.

Semua masukan DETERMINISTIK dan PUBLIK (bukan rahasia, bukan kunci produksi): kunci uji =
SHA-256 dari label teks yang tertulis di bawah, IV = 12 byte pertama SHA-256 label per vektor.
Jadi file JSON tidak menyimpan kunci (gitleaks), hanya label + hasil yang diharapkan.

Pemakaian:
- Test Odoo `tests/test_pii_vectors.py` menghitung ulang semua vektor dan membandingkan dengan
  `tests/data/efn_pii_shared_vectors.json` (bagian `expected`), juga dengan implementasi independen
  (AESGCM/HKDF/HMAC stdlib).
- Menghasilkan ulang JSON (di container Odoo, tanpa DB)::

      python3 /mnt/extra-addons/efn_pii/tools/shared_vectors.py > efn_pii_shared_vectors.json

- BFF (Jest): baca file JSON yang sama; turunkan kunci dengan `createHash('sha256').update(label)`,
  IV dengan `createHash('sha256').update(iv_label).digest().subarray(0, 12)`, lalu
  `Buffer.from(text.slice(8), 'base64')` = blob (versi || iv || ct || tag) dan dekripsi dengan AAD `aad`.
  Blind index: `HKDF(sha256, ikm, salt kosong, info)` lalu `HMAC-SHA256(subkey, normalized)` hex.
  Token nama: HMAC-SHA256(subkey, normalized) 16 byte pertama, base64url tanpa padding.
"""

import hashlib
import json
import sys

AES_LABEL = "efn-pii-shared-vector:aes-key:v1"
BIDX_LABEL = "efn-pii-shared-vector:bidx-key:v1"
NAME_LABEL = "efn-pii-shared-vector:name-key:v1"
KEY_VERSION = 1

AEAD_CASES = (
    # name, table, column, record_id (None = format 1), plaintext
    ("partner-phone-v1-unbound", "res_partner", "phone", None, "6281234567890"),
    ("partner-phone-v2-id42", "res_partner", "phone", 42, "6281234567890"),
    ("partner-name-v2-id42", "res_partner", "name", 42, "Budi Santoso"),
    ("partner-street-v2-id42", "res_partner", "street", 42, "Jl. Melati No. 5, RT 01/RW 02"),
    ("pos-order-email-v2-id7", "pos_order", "email", 7, "budi.santoso@example.com"),
)
BIDX_CASES = (
    ("phone", "6281234567890"),
    ("email", "budi.santoso@example.com"),
    ("vat", "0123456789012345"),
)
TOKEN_CASES = (
    ("f", "budi santoso"),
    ("w", "budi"),
    ("p", "sant"),
)
NORMALIZE_CASES = (
    ("Budi Santoso", "budi santoso"),
    ("  Hj. Siti   Nurhaliza ", "hj siti nurhaliza"),
    ("Al-Fatih", "al fatih"),
    ("José Müller", "jose muller"),
    ("PT Sumber Rejeki, Budi", "pt sumber rejeki budi"),
    ("ＢＵＤＩ", "budi"),
    ("Straße", "strasse"),
    ("O'Neil", "o neil"),
    ("李小龙", ""),
)


def sha256(label):
    return hashlib.sha256(label.encode("utf-8")).digest()


def iv_label(name):
    return f"efn-pii-shared-vector:iv:{name}"


def build(kr, nt):
    """Bangun dict vektor lengkap (dengan `expected`) memakai modul keyring (`kr`) dan name_tokens (`nt`)."""
    aes_key, bidx_key, name_key = sha256(AES_LABEL), sha256(BIDX_LABEL), sha256(NAME_LABEL)
    aead = []
    for name, table, column, record_id, plaintext in AEAD_CASES:
        aad = kr.aad_for(table, column, record_id)
        iv = sha256(iv_label(name))[:12]
        blob = kr.seal(aes_key, KEY_VERSION, iv, plaintext.encode("utf-8"), aad.encode("utf-8"))
        prefix = kr.PREFIX if record_id is None else kr.PREFIX_V2
        aead.append(
            {
                "name": name,
                "table": table,
                "column": column,
                "record_id": record_id,
                "aad": aad,
                "plaintext": plaintext,
                "iv_label": iv_label(name),
                "expected_text": kr.wrap(blob, prefix),
            }
        )
    bidx = []
    for field, normalized in BIDX_CASES:
        info = f"efn:odoo:bidx:{field}:v1"
        subkey = kr.hkdf_sha256(bidx_key, info.encode("ascii"))
        bidx.append(
            {
                "field": field,
                "info": info,
                "normalized": normalized,
                "expected_hex": kr.blind_index_hex(subkey, normalized),
            }
        )
    tokens = []
    for kind, normalized in TOKEN_CASES:
        subkey = kr.derive_name_subkey(name_key, kind)
        tokens.append(
            {
                "kind": kind,
                "info": f"efn:odoo:nametok:{kind}:v1",
                "normalized": normalized,
                "expected": kr.name_token_b64(subkey, normalized),
            }
        )
    return {
        "description": "Vektor bersama BFF <-> Odoo, AAD EFN (ADR-036 §0.3). Kunci uji = SHA-256(label) publik, "
        "BUKAN rahasia. Dibangkitkan oleh efn_pii/tools/shared_vectors.py.",
        "labels": {"aes_key": AES_LABEL, "bidx_ikm": BIDX_LABEL, "name_ikm": NAME_LABEL},
        "key_version": KEY_VERSION,
        "format": {
            "text": "efnpii1:|efnpii2: + base64 standar(versi(1) || iv(12) || ciphertext || tag(16))",
            "aad_v1": "efn:odoo:<tabel>.<kolom>:v1",
            "aad_v2": "efn:odoo:<tabel>.<kolom>:<id>:v2",
            "name_token": "base64url_nopad(HMAC-SHA256(HKDF(name_ikm, info))[:16])",
        },
        "aead": aead,
        "bidx": bidx,
        "name_tokens": tokens,
        "normalize": [{"input": raw, "expected": out} for raw, out in NORMALIZE_CASES],
        "normalize_note": "NFKC -> casefold -> NFKD tanpa diakritik -> [^a-z0-9] jadi spasi -> rapatkan spasi",
        "normalize_actual": [nt.normalize(raw) for raw, _out in NORMALIZE_CASES],
    }


def _load_standalone():
    import importlib.util
    import pathlib

    here = pathlib.Path(__file__).resolve().parent
    modules = []
    sources = (
        ("efn_pii_keyring", here.parent / "models" / "keyring.py"),
        ("efn_pii_nt", here / "name_tokens.py"),
    )
    for name, path in sources:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


if __name__ == "__main__":
    keyring_mod, nt_mod = _load_standalone()
    data = build(keyring_mod, nt_mod)
    data.pop("normalize_actual")
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
