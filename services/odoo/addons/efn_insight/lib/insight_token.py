"""Token SSO `eit1` Odoo -> insight-web (G42, ADR-041 Proposed, docs/04 §26).

Format (harus identik dengan verifikator insight-web):

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


def encode_token(payload, key):
    """Token eit1 dari payload (biasanya hasil `build_payload`). JSON kanonik ensure_ascii=True (docs/04 §26.3)."""
    payload_b64 = b64url_encode(canonical_json(payload).encode("ascii"))
    signing_input = f"{PREFIX}.{payload_b64}"
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
