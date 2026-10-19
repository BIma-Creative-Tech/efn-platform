"""Helper bersama E-28 (bukan model): kode error kontrak, validasi input, hash kanonik, KDF kode ambil.

Kontrak: docs/04_SERVICE_CONTRACTS.md §10.2 (format id/kode), §10.4.2 (error = ``UserError`` dengan pesan
PERSIS ``efn_online:<kode>``), §10.3.6 (kode ambil = rahasia; Odoo hanya menyimpan hash KDF lambat).
"""

import hashlib
import json
import re
from contextlib import contextmanager

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

# G19 E-37a (docs/04 §11.3 no. 2): kode brand kanal, tidak unik (Bacha dipakai dua kanal), boleh kosong.
BRAND_CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

# G19 E-36 (docs/04 §11.2 no. 1): kode promo dari BFF; maks 3, dinormalisasi huruf besar, duplikat dibuang.
PROMO_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_PROMO_CODES = 3
# docs/04 §11.2 no. 7 (ADR-020): hanya tipe ini yang boleh `sale_ok=True` (kanal online). Poin/dompet/gift card/kupon
# pesanan berikut ditolak: partner generik kanal akan mengumpulkan kartu/poin.
ONLINE_PROGRAM_TYPES = ("promotion", "promo_code", "buy_x_get_y", "coupons")

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

# G20 E-45 (docs/04 §12.2, L-50, L-56): metode bayar pesanan online. Urutan = urutan kanonik keluaran katalog.
PAYMENT_MODES = ("qris", "counter")
DEFAULT_PAYMENT_MODE = "qris"
# Kode bayar di kasir (dibuat BFF, alfabet §10.2 sama dengan kode ambil). Odoo hanya menyimpan digest KDF
# DETERMINISTIK per company (bukan salt acak seperti kode ambil) karena kasir mengetik kode TANPA memilih pesanan:
# pencarian = persamaan digest. Salt = sha256("efn_online_counter:<company_id>:<database.secret>") -> digest tidak
# bisa dihitung tanpa isi DB dan tidak sama antar-company. Putaran: param `efn_online.counter_code_kdf_rounds`
# (default 210 000 = rekomendasi OWASP PBKDF2-SHA512); putaran ikut tersimpan di digest (pencarian mencoba setiap
# nilai putaran yang dipakai pesanan terbuka, jadi parameter boleh diubah kapan saja).
COUNTER_KDF_PARAM = "efn_online.counter_code_kdf_rounds"
DEFAULT_COUNTER_KDF_ROUNDS = 210_000
COUNTER_HASH_PREFIX = "pbkdf2-sha512"


def counter_code_digest(secret, company_id, code, rounds):
    """Digest KDF kode bayar (``pbkdf2-sha512$<rounds>$<hex>``). Kode tidak pernah disimpan/di-log."""
    rounds = max(int(rounds), MIN_PICKUP_KDF_ROUNDS)
    salt = hashlib.sha256(f"efn_online_counter:{int(company_id)}:{secret or ''}".encode()).digest()
    derived = hashlib.pbkdf2_hmac("sha512", code.encode("ascii"), salt, rounds)
    return f"{COUNTER_HASH_PREFIX}${rounds}${derived.hex()}"


def counter_hash_rounds(value):
    """Putaran dari digest tersimpan (None bila format tidak dikenal)."""
    parts = (value or "").split("$")
    if len(parts) != 3 or parts[0] != COUNTER_HASH_PREFIX or not parts[1].isdigit():
        return None
    return int(parts[1])


def parse_payment_modes(value):
    """``payment_modes`` kanal (Char dipisah koma) -> list kanonik; None bila tidak valid (kosong/duplikat/asing)."""
    if not isinstance(value, str):
        return None
    tokens = [token.strip() for token in value.split(",")]
    if not tokens or any(token not in PAYMENT_MODES for token in tokens) or len(set(tokens)) != len(tokens):
        return None
    return [mode for mode in PAYMENT_MODES if mode in tokens]


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


def counter_kdf_rounds(env):
    raw = env["ir.config_parameter"].sudo().get_param(COUNTER_KDF_PARAM, DEFAULT_COUNTER_KDF_ROUNDS)
    try:
        return max(int(raw), MIN_PICKUP_KDF_ROUNDS)
    except (TypeError, ValueError):
        return DEFAULT_COUNTER_KDF_ROUNDS


def counter_secret(env):
    return env["ir.config_parameter"].sudo().get_param("database.secret") or ""


def normalize_pickup_code(value):
    """Normalisasi input staf: huruf besar, buang spasi/strip. Hasil tidak pernah di-log."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\s\-]", "", value).upper()


def pickup_crypt_context(rounds):
    rounds = max(int(rounds), MIN_PICKUP_KDF_ROUNDS)
    return CryptContext(schemes=["pbkdf2_sha512"], pbkdf2_sha512__rounds=rounds)


def promo_codes_payload(value):
    """Validasi ``promo_codes`` (input BFF, §11.2 no. 1). ``None`` = tanpa kode. Hasil: huruf besar, urutan input,
    duplikat dibuang. Kode promo = rahasia ringan (anti-enumerasi kupon): tidak pernah di-log dalam bentuk asli."""
    if value is None:
        return []
    require(isinstance(value, list) and len(value) <= MAX_PROMO_CODES)
    result = []
    for code in value:
        check_str(code, PROMO_CODE_RE, 40)
        code = code.upper()
        if code not in result:
            result.append(code)
    return result


# L-42 / SEC-132: tidak ada tag hash kode promo di log (helper `promo_code_ref` dihapus; hash tanpa kunci atas ruang
# kode ±32 bit bisa dibalik). Log promo hanya jumlah kode / jumlah terpakai.


def like_escape(value):
    """Escape wildcard LIKE/ILIKE (``\\``, ``%``, ``_``) agar ``=ilike`` = sama persis tanpa beda huruf besar/kecil."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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


# SEC-104/L-19: penanda "jalur internal" (melewati guard) TIDAK boleh berbasis context (context RPC dikendalikan
# klien: /web/dataset/call_kw kwargs.context). Penanda disimpan di cache transaksi cursor (`cr.cache`), yang hanya
# bisa diisi kode server di transaksi yang sama, dan dibersihkan di `finally`.
CANCEL_SCOPE = "efn_online_internal_cancel_so_ids"
CONFIRM_SCOPE = "efn_online_internal_confirm_so_ids"
HANDOVER_SCOPE = "efn_online_handover_so_ids"


@contextmanager
def internal_scope(env, key, ids):
    bucket = env.cr.cache.setdefault(key, set())
    added = set(ids) - bucket
    bucket.update(added)
    try:
        yield
    finally:
        current = env.cr.cache.get(key)
        if current is not None:
            current.difference_update(added)


def in_scope(env, key, ids):
    ids = set(ids)
    return bool(ids) and ids <= (env.cr.cache.get(key) or set())


def in_any_scope(env, ids):
    """True bila semua SO berada di salah satu jalur internal E-28 (konfirmasi, batal, serah terima)."""
    ids = set(ids)
    if not ids:
        return True
    allowed = set()
    for key in (CONFIRM_SCOPE, CANCEL_SCOPE, HANDOVER_SCOPE):
        allowed |= env.cr.cache.get(key) or set()
    return ids <= allowed
