"""Token SSO `eit1` / `eit2` / `eit3` Odoo -> insight-web (G42 + G47 + G52, ADR-041 §3.3-§3.6 Proposed, docs/04 §26 +
§28.13 + §28.15).

`eit3` (G52, docs/04 §28.15.5): format sama dengan `eit2`, prefix `eit3`, `v=3`, tepat 14 kunci (13 kunci `eit2` +
`brs` = cabang ESB `"<esb_brand>:<branch_id>"` untuk kepala toko F&B) dan aturan konsistensi T1-T8
(`validate_claims_v3`). `eit2` tetap ada untuk rollback (`EFN_INSIGHT_TOKEN_VERSION=2`).
G66 (docs/04 §30.7, ADR-051 Proposed): `roles` eit3 boleh memuat `PP_ROLES` (maks 8 peran) + aturan T9; format tetap.

`eit2` (G47, docs/04 §28.13.4): format sama dengan `eit1` di bawah, prefix `eit2`, tepat 13 kunci (tambahan `cats`,
`wids`; `v=2`; `codes` boleh memuat `SM_CODES`; `roles` = satu dari user/all_brands + 0..5 persona) dan aturan
konsistensi (`validate_claims_v2`). `eit1` tetap ada untuk rollback (`EFN_INSIGHT_TOKEN_VERSION=1`).

Format eit1 (harus identik dengan verifikator insight-web):

    eit1.<payload_b64url>.<sig_b64url>

- payload = JSON kanonik (sort_keys, separators "," dan ":", ensure_ascii=True: non-ASCII di
  `name` menjadi escape backslash-u), lalu base64url TANPA padding "=".
- sig = HMAC-SHA256(key, ASCII("eit1." + payload_b64url)), base64url tanpa padding.
- **Kunci HMAC = byte ASCII dari string hex di file secret apa adanya (setelah strip), BUKAN
  hasil hex-decode.** Titik interop paling rawan; ada test khusus (vektor docs/04 §26.3).
- Isi: tepat 11 kunci v, aud, iss, sub, name, codes, cids, roles, iat, exp (= iat + 60), jti.
  Tanpa login/email/telepon (UU PDP: minimisasi).

Fungsi murni (tanpa ORM, bukan metode model) agar bisa diuji dengan vektor tetap dan tidak
menambah permukaan RPC. `verify_token` hanya rujukan algoritma untuk test; Odoo tidak pernah
memverifikasi token ini di produksi.
"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import unicodedata

PREFIX = "eit1"
VERSION = 1
AUDIENCE = "insight-web"
ISSUER = "odoo"
TTL_SECONDS = 60
MAX_NAME = 64
MIN_KEY_HEX = 64
# Kode brand mart ESB (G41, docs/04 §25). IMD/CUP = company Odoo sejak G43 (efn_setup_company 19.0.1.9.0).
ESB_CODES = frozenset({"PB", "WTZ", "BCF", "BCR", "IMD", "CUP"})
ROLES = frozenset({"user", "all_brands"})
PAYLOAD_KEYS = frozenset({"aud", "cids", "codes", "exp", "iat", "iss", "jti", "name", "roles", "sub", "v"})

MAX_CIDS = 32
MAX_SUB = 2**31 - 1

# ------------------------------------------------------------------ eit2 (G47, docs/04 §28.13.3-§28.13.4)
# Konstanta identik dengan insight-web `lib/claims.ts`. Menambah PT supermarket = revisi kontrak, bukan konfigurasi.
PREFIX_V2 = "eit2"
VERSION_V2 = 2
SM_CODES = frozenset({"SMD"})
PERSONAS = frozenset({"category", "dc", "exec", "marketing", "store"})
# Persona tingkat PT: mendapat wids 0 (baris tanpa outlet) + semua warehouse aktif PT SM.
ORG_PERSONAS = frozenset({"category", "dc", "exec", "marketing"})
PAYLOAD_KEYS_V2 = PAYLOAD_KEYS | {"cats", "wids"}
MAX_CODES_V2 = 7
MAX_WIDS = 200
MAX_CATS = 50
MAX_ID = 2**32 - 1  # UInt32 ClickHouse
MAX_PAYLOAD_B64 = 3000
_TOKEN_V2_RE = re.compile(r"eit2\.[A-Za-z0-9_-]{16,3000}\.[A-Za-z0-9_-]{43}")

# ------------------------------------------------------------------ eit3 (G52, docs/04 §28.15.4-§28.15.5)
# Konstanta identik dengan insight-web `lib/claims.ts`.
PREFIX_V3 = "eit3"
VERSION_V3 = 3
FNB_PERSONAS = frozenset({"exec", "marketing", "store"})
# Akun ESB (= KNOWN_BRANDS esb-extract). Bacha melayani dua PT (BCF + BCR).
ESB_BRANDS = frozenset({"bacha", "cup", "imd", "pb", "wtz"})
BRAND_COMPANY_CODES = {
    "bacha": frozenset({"BCF", "BCR"}),
    "cup": frozenset({"CUP"}),
    "imd": frozenset({"IMD"}),
    "pb": frozenset({"PB"}),
    "wtz": frozenset({"WTZ"}),
}
MAX_BRS = 50
# G66 (docs/04 §30.7, ADR-051, L-251): peran production planning di `roles` eit3 (bukan persona -> sig ClickHouse
# tetap). Identik dengan insight-web `lib/claims.ts` `PP_ROLES`. Panjang `roles` maks 8 (basis 1 + persona 5 + PP 2).
PP_ROLES = frozenset({"pp_planner", "pp_warehouse"})
MAX_ROLES_V3 = 8
PAYLOAD_KEYS_V3 = PAYLOAD_KEYS_V2 | {"brs"}
_TOKEN_V3_RE = re.compile(r"eit3\.[A-Za-z0-9_-]{16,3000}\.[A-Za-z0-9_-]{43}")
_BRS_RE = re.compile(r"(bacha|cup|imd|pb|wtz):[1-9][0-9]{0,9}")
# L-245 butir 1: persona yang membuat `store` dibuang bila dipegang tanpa `exec`.
_STORE_CONFLICT_PERSONAS = frozenset({"category", "dc", "marketing"})
# docs/04 §26.3: kategori Unicode yang dihapus dari `name`.
_NAME_DROP_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})

_KEY_RE = re.compile(rf"[0-9a-fA-F]{{{MIN_KEY_HEX},}}")
_JTI_RE = re.compile(r"[0-9a-f]{32}")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
# URL insight (docs/04 §26.2): skema + host (huruf/angka/titik/strip) + port opsional, path hanya "/" opsional.
# Tanpa userinfo, query, fragment, spasi, kutip, `;`/`,` (dipakai di header CSP form-action dan atribut HTML).
_URL_RE = re.compile(
    r"(?P<scheme>https?)://(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)(?P<port>:[0-9]{1,5})?/?"
)


def b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(text):
    if not _B64URL_RE.fullmatch(text or ""):
        raise ValueError("bad_b64")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("bad_b64") from exc


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def is_valid_key(key):
    return isinstance(key, str) and bool(_KEY_RE.fullmatch(key))


def load_key(path):
    """Baca kunci dari file secret; None bila tidak terbaca / bukan hex >= 64. Isi tidak di-log."""
    try:
        with open(path, encoding="ascii") as handle:
            key = handle.read().strip()
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return key if is_valid_key(key) else None


def parse_insight_url(raw, allow_plaintext=False):
    """Validasi EFN_INSIGHT_URL. Mengembalikan origin (`skema://host[:port]`, tanpa `/` akhir) atau None.

    `https://` wajib; `http://` hanya bila `allow_plaintext` (efn_core `plaintext_allowed()`: dev/ci eksplisit).
    Path selain `/` ditolak (docs/04 §26.2). Aksi form = origin + "/sso".
    """
    match = _URL_RE.fullmatch((raw or "").strip())
    if not match:
        return None
    scheme = match.group("scheme")
    if scheme != "https" and not (scheme == "http" and allow_plaintext):
        return None
    port = match.group("port") or ""
    if port and not 0 < int(port[1:]) <= 65535:
        return None
    return f"{scheme}://{match.group('host').lower()}{port}"


def clean_name(raw):
    """docs/04 §26.3: hapus Cc/Cf/Zl/Zp, rapikan spasi, trim, maks 64 code point; kosong -> "-"."""
    text = "".join(ch for ch in str(raw or "") if unicodedata.category(ch) not in _NAME_DROP_CATEGORIES)
    text = " ".join(text.split())[:MAX_NAME].strip()
    return text or "-"


def build_payload(*, sub, name, codes, cids, roles, iat, jti):
    """Payload eit1 tervalidasi. `codes`/`cids` diurutkan dan dideduplikasi; ValueError bila input tidak sah."""
    if not isinstance(sub, int) or isinstance(sub, bool) or not 0 < sub <= MAX_SUB:
        raise ValueError("bad_sub")
    codes = sorted(set(codes or ()))
    if not codes or not set(codes) <= ESB_CODES:
        raise ValueError("bad_codes")
    cids = sorted(set(cids or ()))
    if any(not isinstance(c, int) or isinstance(c, bool) or c <= 0 for c in cids) or len(cids) > MAX_CIDS:
        raise ValueError("bad_cids")
    roles = list(roles or ())
    if len(roles) != 1 or roles[0] not in ROLES:
        raise ValueError("bad_roles")
    if roles == ["user"] and not cids:
        raise ValueError("bad_cids")
    if not isinstance(iat, int) or isinstance(iat, bool) or iat <= 0:
        raise ValueError("bad_iat")
    if not isinstance(jti, str) or not _JTI_RE.fullmatch(jti):
        raise ValueError("bad_jti")
    return {
        "v": VERSION,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "sub": sub,
        "name": clean_name(name),
        "codes": codes,
        "cids": cids,
        "roles": roles,
        "iat": iat,
        "exp": iat + TTL_SECONDS,
        "jti": jti,
    }


def sign(signing_input, key):
    """HMAC-SHA256 dengan kunci = byte ASCII string hex (bukan hex-decode)."""
    if not is_valid_key(key):
        raise ValueError("bad_key")
    return hmac.new(key.encode("ascii"), signing_input.encode("ascii"), hashlib.sha256).digest()


def encode_token(payload, key, prefix=PREFIX):
    """Token `<prefix>.<payload>.<sig>` (default eit1). JSON kanonik ensure_ascii=True (docs/04 §26.3)."""
    payload_b64 = b64url_encode(canonical_json(payload).encode("ascii"))
    signing_input = f"{prefix}.{payload_b64}"
    return f"{signing_input}.{b64url_encode(sign(signing_input, key))}"


def verify_token(token, key, now):
    """Rujukan verifikasi (test). Payload atau ValueError(kode); bukan pengganti insight-web."""
    parts = (token or "").split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise ValueError("bad_format")
    expected = sign(f"{PREFIX}.{parts[1]}", key)
    if not hmac.compare_digest(expected, b64url_decode(parts[2])):
        raise ValueError("bad_sig")
    try:
        payload = json.loads(b64url_decode(parts[1]).decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("bad_payload") from exc
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise ValueError("bad_claims")
    if payload.get("v") != VERSION or payload.get("aud") != AUDIENCE or payload.get("iss") != ISSUER:
        raise ValueError("bad_claims")
    iat, exp = payload.get("iat"), payload.get("exp")
    if not isinstance(iat, int) or not isinstance(exp, int):
        raise ValueError("bad_claims")
    if iat > now + 30 or not now < exp or not 1 <= exp - iat <= TTL_SECONDS:
        raise ValueError("expired")
    if not set(payload.get("codes") or ()) <= ESB_CODES:
        raise ValueError("bad_codes")
    return payload


# ====================================================================== eit2 (G47)
def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _sorted_unique(values):
    """True bila list unik dan urut naik (bentuk kanonik yang wajib di payload)."""
    return isinstance(values, list) and all(values[i] < values[i + 1] for i in range(len(values) - 1))


def _validate_common(payload, extra_roles=frozenset()):
    """Bentuk kunci cakupan bersama eit2/eit3 (`codes`, `cids`, `roles`, `wids`, `cats`). Mengembalikan persona.

    `extra_roles` = peran non-persona tambahan yang sah (eit3: `PP_ROLES`; eit2: tidak ada).
    """
    codes, cids, roles = payload.get("codes"), payload.get("cids"), payload.get("roles")
    wids, cats = payload.get("wids"), payload.get("cats")
    if (
        not isinstance(codes, list)
        or not 1 <= len(codes) <= MAX_CODES_V2
        or not all(isinstance(c, str) for c in codes)
        or not _sorted_unique(codes)
        or not set(codes) <= (ESB_CODES | SM_CODES)
    ):
        raise ValueError("bad_codes")
    if (
        not isinstance(cids, list)
        or len(cids) > MAX_CIDS
        or not all(_is_int(c) and 0 < c <= MAX_ID for c in cids)
        or not _sorted_unique(cids)
    ):
        raise ValueError("bad_cids")
    if (
        not isinstance(roles, list)
        or not all(isinstance(r, str) for r in roles)
        or not _sorted_unique(roles)
        or not set(roles) <= (ROLES | PERSONAS | extra_roles)
        or len(set(roles) & ROLES) != 1
    ):
        raise ValueError("bad_roles")
    if (
        not isinstance(wids, list)
        or len(wids) > MAX_WIDS
        or not all(_is_int(w) and 0 <= w <= MAX_ID for w in wids)
        or not _sorted_unique(wids)
    ):
        raise ValueError("bad_wids")
    if (
        not isinstance(cats, list)
        or len(cats) > MAX_CATS
        or not all(_is_int(c) and 0 < c <= MAX_ID for c in cats)
        or not _sorted_unique(cats)
    ):
        raise ValueError("bad_cats")
    personas = set(roles) & PERSONAS
    # cids >= 1 kecuali roles memuat all_brands dan tanpa persona.
    if not cids and not ("all_brands" in roles and not personas):
        raise ValueError("bad_cids")
    return personas


def validate_claims_v2(payload):
    """Validasi isi klaim eit2 (docs/04 §28.13.4) — dipakai pembuat DAN rujukan verifikasi.

    Hanya memeriksa kunci yang dipengaruhi cakupan (`codes`, `cids`, `roles`, `wids`, `cats`) + aturan konsistensi
    1-3; batas panjang payload (aturan 4) diperiksa `encode_token_v2` / `verify_token_v2`. ValueError(kode).
    """
    personas = _validate_common(payload)
    codes, roles, wids, cats = payload["codes"], payload["roles"], payload["wids"], payload["cats"]
    # Aturan konsistensi 1-3.
    if not (bool(wids) == bool(personas) == bool(set(codes) & SM_CODES)):
        raise ValueError("bad_claims")
    if (0 in wids) != bool(personas & ORG_PERSONAS):
        raise ValueError("bad_claims")
    if cats and "category" not in roles:
        raise ValueError("bad_claims")


def build_payload_v2(*, sub, name, codes, cids, roles, wids, cats, iat, jti):
    """Payload eit2 tervalidasi (13 kunci). Daftar diurutkan & dideduplikasi; ValueError bila tidak sah/konsisten."""
    if not _is_int(sub) or not 0 < sub <= MAX_SUB:
        raise ValueError("bad_sub")
    if not _is_int(iat) or iat <= 0:
        raise ValueError("bad_iat")
    if not isinstance(jti, str) or not _JTI_RE.fullmatch(jti):
        raise ValueError("bad_jti")
    payload = {
        "v": VERSION_V2,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "sub": sub,
        "name": clean_name(name),
        "codes": sorted(set(codes or ())),
        "cids": sorted(set(cids or ())),
        "roles": sorted(set(roles or ())),
        "wids": sorted(set(wids or ())),
        "cats": sorted(set(cats or ())),
        "iat": iat,
        "exp": iat + TTL_SECONDS,
        "jti": jti,
    }
    validate_claims_v2(payload)
    return payload


def encode_token_v2(payload, key):
    """Token eit2. ValueError("scope_too_large") bila payload_b64 > 3000 karakter (aturan konsistensi 4)."""
    payload_b64 = b64url_encode(canonical_json(payload).encode("ascii"))
    if len(payload_b64) > MAX_PAYLOAD_B64:
        raise ValueError("scope_too_large")
    signing_input = f"{PREFIX_V2}.{payload_b64}"
    return f"{signing_input}.{b64url_encode(sign(signing_input, key))}"


def verify_token_v2(token, key, now):
    """Rujukan verifikasi eit2 (test; insight-web adalah verifikator sesungguhnya). Payload atau ValueError(kode)."""
    if not isinstance(token, str) or not _TOKEN_V2_RE.fullmatch(token):
        raise ValueError("bad_format")
    _prefix, payload_b64, sig_b64 = token.split(".")
    expected = sign(f"{PREFIX_V2}.{payload_b64}", key)
    if not hmac.compare_digest(expected, b64url_decode(sig_b64)):
        raise ValueError("bad_sig")
    try:
        payload = json.loads(b64url_decode(payload_b64).decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("bad_payload") from exc
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS_V2:
        raise ValueError("bad_claims")
    if payload.get("v") != VERSION_V2 or payload.get("aud") != AUDIENCE or payload.get("iss") != ISSUER:
        raise ValueError("bad_claims")
    iat, exp, sub = payload.get("iat"), payload.get("exp"), payload.get("sub")
    if not _is_int(iat) or not _is_int(exp) or not _is_int(sub) or not 0 < sub <= MAX_SUB:
        raise ValueError("bad_claims")
    if iat > now + 30 or not now < exp or not 1 <= exp - iat <= TTL_SECONDS:
        raise ValueError("expired")
    # Kode rinci (bad_codes, bad_wids, bad_claims, ...) dipertahankan untuk test; insight-web memetakan ke bad_claims.
    validate_claims_v2(payload)
    return payload


# ====================================================================== eit3 (G52)
def suppress_store(held):
    """docs/04 §28.15.4 butir 1 (L-245, perluasan L-234). `(G', store_suppressed)` dari persona yang dipegang.

    `store` dibuang bila dipegang tanpa `exec` bersama category/dc/marketing (SEC-G47-01: `roles` satu himpunan untuk
    F&B dan SMD).
    """
    held = frozenset(held)
    if "store" in held and "exec" not in held and held & _STORE_CONFLICT_PERSONAS:
        return held - {"store"}, True
    return held, False


def is_store_only_fnb(held, all_brands):
    """docs/04 §28.15.4 butir 2: store ∈ G, G ∩ {exec, marketing} = ∅, tanpa grup all_brands."""
    held = frozenset(held)
    return "store" in held and not held & {"exec", "marketing"} and not all_brands


def _valid_brs(brs):
    if not isinstance(brs, list) or len(brs) > MAX_BRS or not all(isinstance(b, str) for b in brs):
        return False
    for item in brs:
        if not _BRS_RE.fullmatch(item) or int(item.split(":", 1)[1]) > MAX_ID:
            return False
    # Urut naik string ASCII (mis. "pb:12" < "pb:7"), unik.
    return _sorted_unique(brs)


def validate_claims_v3(payload):
    """Validasi isi klaim eit3 (docs/04 §28.15.5 T1-T7 + §30.7 T9) — pembuat DAN rujukan verifikasi. ValueError(kode).

    T8 (`payload_b64 ≤ 3000`) diperiksa `encode_token_v3` / `verify_token_v3`.
    """
    personas = _validate_common(payload, extra_roles=PP_ROLES)
    codes, roles, wids, cats = payload["codes"], payload["roles"], payload["wids"], payload["cats"]
    if len(roles) > MAX_ROLES_V3:
        raise ValueError("bad_roles")
    brs = payload.get("brs")
    if not _valid_brs(brs):
        raise ValueError("bad_brs")
    esb, sm = set(codes) & ESB_CODES, set(codes) & SM_CODES
    # T1: wids ≠ [] ⇔ SM ≠ ∅; SM ≠ ∅ ⇒ P ≠ ∅.
    if bool(wids) != bool(sm) or (sm and not personas):
        raise ValueError("bad_claims")
    # T2: SM ≠ ∅ ⇒ (0 ∈ wids ⇔ P ∩ {category, dc, exec, marketing} ≠ ∅).
    if sm and (0 in wids) != bool(personas & ORG_PERSONAS):
        raise ValueError("bad_claims")
    # T3: P ∩ {category, dc} ≠ ∅ ⇒ SM ≠ ∅.
    if personas & {"category", "dc"} and not sm:
        raise ValueError("bad_claims")
    # T4: P ≠ ∅ ⇒ SM ≠ ∅ ∨ ESB ≠ ∅.
    if personas and not (sm or esb):
        raise ValueError("bad_claims")
    # T5: store ∈ P ⇒ P ⊆ {exec, store} (L-245).
    if "store" in personas and not personas <= {"exec", "store"}:
        raise ValueError("bad_claims")
    # T6: brs ≠ [] ⇔ (store ∈ P ∧ exec ∉ P ∧ all_brands ∉ roles ∧ ESB ≠ ∅).
    branch_scoped = "store" in personas and "exec" not in personas and "all_brands" not in roles and bool(esb)
    if bool(brs) != branch_scoped:
        raise ValueError("bad_claims")
    # T7: cats ≠ [] ⇒ category ∈ P.
    if cats and "category" not in roles:
        raise ValueError("bad_claims")
    # T9 (docs/04 §30.7, G66): PP ∩ roles ≠ ∅ ⇒ ESB ≠ ∅ ∧ brs = [].
    if set(roles) & PP_ROLES and (not esb or brs):
        raise ValueError("bad_claims")


def build_payload_v3(*, sub, name, codes, cids, roles, wids, cats, brs, iat, jti):
    """Payload eit3 tervalidasi (14 kunci). Daftar diurutkan & dideduplikasi; ValueError bila tidak sah/konsisten."""
    if not _is_int(sub) or not 0 < sub <= MAX_SUB:
        raise ValueError("bad_sub")
    if not _is_int(iat) or iat <= 0:
        raise ValueError("bad_iat")
    if not isinstance(jti, str) or not _JTI_RE.fullmatch(jti):
        raise ValueError("bad_jti")
    payload = {
        "v": VERSION_V3,
        "aud": AUDIENCE,
        "iss": ISSUER,
        "sub": sub,
        "name": clean_name(name),
        "codes": sorted(set(codes or ())),
        "cids": sorted(set(cids or ())),
        "roles": sorted(set(roles or ())),
        "wids": sorted(set(wids or ())),
        "cats": sorted(set(cats or ())),
        "brs": sorted(set(brs or ())),
        "iat": iat,
        "exp": iat + TTL_SECONDS,
        "jti": jti,
    }
    validate_claims_v3(payload)
    return payload


def encode_token_v3(payload, key):
    """Token eit3. ValueError("scope_too_large") bila payload_b64 > 3000 karakter (T8)."""
    payload_b64 = b64url_encode(canonical_json(payload).encode("ascii"))
    if len(payload_b64) > MAX_PAYLOAD_B64:
        raise ValueError("scope_too_large")
    signing_input = f"{PREFIX_V3}.{payload_b64}"
    return f"{signing_input}.{b64url_encode(sign(signing_input, key))}"


def verify_token_v3(token, key, now):
    """Rujukan verifikasi eit3 (test; insight-web adalah verifikator sesungguhnya). Payload atau ValueError(kode)."""
    if not isinstance(token, str) or not _TOKEN_V3_RE.fullmatch(token):
        raise ValueError("bad_format")
    _prefix, payload_b64, sig_b64 = token.split(".")
    expected = sign(f"{PREFIX_V3}.{payload_b64}", key)
    if not hmac.compare_digest(expected, b64url_decode(sig_b64)):
        raise ValueError("bad_sig")
    try:
        payload = json.loads(b64url_decode(payload_b64).decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("bad_payload") from exc
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS_V3:
        raise ValueError("bad_claims")
    if payload.get("v") != VERSION_V3 or payload.get("aud") != AUDIENCE or payload.get("iss") != ISSUER:
        raise ValueError("bad_claims")
    iat, exp, sub = payload.get("iat"), payload.get("exp"), payload.get("sub")
    if not _is_int(iat) or not _is_int(exp) or not _is_int(sub) or not 0 < sub <= MAX_SUB:
        raise ValueError("bad_claims")
    if iat > now + 30 or not now < exp or not 1 <= exp - iat <= TTL_SECONDS:
        raise ValueError("expired")
    validate_claims_v3(payload)
    return payload
