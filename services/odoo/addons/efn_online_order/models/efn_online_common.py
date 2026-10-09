"""Helper bersama E-28 (bukan model): kode error kontrak, validasi input, hash kanonik, KDF kode ambil.

Kontrak: docs/04_SERVICE_CONTRACTS.md §10.2 (format id/kode), §10.4.2 (error = ``UserError`` dengan pesan
PERSIS ``efn_online:<kode>``), §10.3.6 (kode ambil = rahasia; Odoo hanya menyimpan hash KDF lambat).
"""

import hashlib
import json
import re

from passlib.context import CryptContext

from odoo.exceptions import UserError

# Kode error bisnis yang boleh keluar ke BFF (docs/04 §10.4.2 + delta §10.4.4 no. 2).
ERROR_CODES = frozenset(
    {
        "price_changed",
        "out_of_stock",
        "amount_mismatch",
        "invalid_state",
        "idempotency_conflict",
        "not_found",
        "invalid_input",
        "channel_inactive",
    }
)

CHANNEL_CODE_RE = re.compile(r"^[A-Z][A-Z0-9-]{1,15}$")
OUTLET_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,23}$")
# Kunci idempotensi kontrak: `online:<langkah>:<uuid>[:<uuid>]` (§10.6.4); batas umum agar aman disimpan.
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{8,128}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Referensi pembayaran/refund dari BFF/provider: opak, tanpa spasi.
OPAQUE_REF_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,128}$")
# Alfabet kode ambil (§10.2): tanpa I, L, O, 0, 1.
PICKUP_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PICKUP_CODE_RE = re.compile(rf"^[{PICKUP_ALPHABET}]{{8}}$")

CANCEL_REASONS = ("payment_expired", "customer", "place_failed", "stock_short", "ops", "review")
REFUND_REASONS = ("stock_short", "customer_cs", "ops_cancelled", "review")
FULFILMENTS = ("pickup", "delivery")
ALLOWED_LANGS = ("id_ID", "en_US")

MAX_LINES_HARD = 200  # batas keras di atas konfigurasi kanal (input tidak tepercaya)
MAX_BATCH = 200
MAX_STATUS_REFS = 100

# KDF kode ambil (§10.3.6, delta §10.4.4 no. 1): pbkdf2-sha512 lewat passlib, pola hash password res.users.
DEFAULT_PICKUP_KDF_ROUNDS = 600_000
MIN_PICKUP_KDF_ROUNDS = 1_000  # hanya untuk test; produksi memakai default (param sistem, base.group_system)


def online_error(code):
    """``UserError`` dengan pesan stabil ``efn_online:<kode>`` (BFF hanya membaca ``name`` + ``message``)."""
    if code not in ERROR_CODES:
        code = "invalid_input"
    return UserError(f"efn_online:{code}")


def require(condition, code="invalid_input"):
    if not condition:
        raise online_error(code)


def check_str(value, pattern=None, max_len=128):
    require(isinstance(value, str) and 0 < len(value) <= max_len)
    if pattern is not None:
        require(bool(pattern.match(value)))
    return value


def check_int(value, minimum=None, maximum=None):
    require(isinstance(value, int) and not isinstance(value, bool))
    if minimum is not None:
        require(value >= minimum)
    if maximum is not None:
        require(value <= maximum)
    return value


def check_amount_int(value):
    """Nominal Rupiah bulat (§10.2): int, atau float tanpa pecahan (JSON number)."""
    if isinstance(value, bool):
        raise online_error("invalid_input")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return check_int(value, minimum=0, maximum=10**13)


def canonical_sha256(data):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def normalize_pickup_code(value):
    """Normalisasi input staf: huruf besar, buang spasi/strip. Hasil tidak pernah di-log."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s\-]", "", value).upper()


def pickup_crypt_context(rounds):
    rounds = max(int(rounds), MIN_PICKUP_KDF_ROUNDS)
    return CryptContext(schemes=["pbkdf2_sha512"], pbkdf2_sha512__rounds=rounds)


def lines_payload(lines, max_lines):
    """Validasi ``lines:[{product_id, qty}]`` (input BFF). Produk duplikat ditolak; urutan dinormalisasi."""
    require(isinstance(lines, list) and 0 < len(lines) <= min(max_lines, MAX_LINES_HARD))
    result = {}
    for line in lines:
        require(isinstance(line, dict) and set(line) <= {"product_id", "qty"})
        product_id = check_int(line.get("product_id"), minimum=1)
        qty = check_int(line.get("qty"), minimum=1)
        require(product_id not in result)
        result[product_id] = qty
    return sorted(result.items())
