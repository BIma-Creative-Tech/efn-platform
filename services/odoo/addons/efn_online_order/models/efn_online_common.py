"""Helper bersama E-28 (bukan model): kode error kontrak, validasi input, hash kanonik, KDF kode ambil.

Kontrak: docs/04_SERVICE_CONTRACTS.md §10.2 (format id/kode), §10.4.2 (error = ``UserError`` dengan pesan
PERSIS ``efn_online:<kode>``), §10.3.6 (kode ambil = rahasia; Odoo hanya menyimpan hash KDF lambat).
"""

import hashlib
import json
import math
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
        # G21 E-39 (docs/04 §13.5, L-63): PT bukan PPN, atau efn_pii tidak terpasang/aktif -> BFF 422.
        "invoice_not_available",
        # G22 W-12 (docs/04 §14.C.3 "Place"): porsi Poin EFN di luar kebijakan PT / kanal tanpa redeem -> BFF 409
        # exceeds_max_pct.
        "points_invalid",
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


# G21 E-39 (docs/04 §13.5): profil pembeli untuk faktur atas nama. Validasi server (BFF juga memvalidasi; Odoo tidak
# memercayai input). Nilai profil = data pribadi: tidak pernah di-log, tidak masuk hash/outbox/hasil RPC.
INVOICE_PROFILE_KEYS = frozenset({"buyer_type", "tax_id", "name", "address", "email"})
INVOICE_PROFILE_REQUIRED = frozenset({"buyer_type", "tax_id", "name", "address"})
BUYER_TYPES = ("npwp", "nik", "passport")
# buyer_type -> nilai `l10n_id_buyer_document_type` (l10n_id_efaktur_coretax 19.0: TIN/NIK/Passport/Other).
BUYER_DOCUMENT_TYPES = {"npwp": "TIN", "nik": "NIK", "passport": "Passport"}
DIGITS16_RE = re.compile(r"^\d{16}$")
# Format paspor tidak ditetapkan kontrak; 5-20 alfanumerik = aturan form Super-App (laporan frontend G21).
PASSPORT_RE = re.compile(r"^[A-Z0-9]{5,20}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _profile_text(value, min_len, max_len):
    require(isinstance(value, str))
    text = " ".join(value.split())  # spasi/baris baru beruntun -> satu spasi
    require(min_len <= len(text) <= max_len and not CONTROL_CHARS_RE.search(text))
    return text


def invoice_profile_payload(value):
    """Validasi + normalisasi ``profile`` (docs/04 §13.5): NPWP/NIK 16 digit (titik/strip/spasi dibuang), paspor
    5-20 alfanumerik huruf besar, nama 2-100, alamat 5-255, email opsional (RFC dasar, huruf kecil, <= 254).
    Gagal -> ``efn_online:invalid_input`` tanpa menyebut field (pesan tidak memuat nilai)."""
    require(isinstance(value, dict) and INVOICE_PROFILE_REQUIRED <= set(value) <= INVOICE_PROFILE_KEYS)
    buyer_type = value["buyer_type"]
    require(buyer_type in BUYER_TYPES)
    tax_id = value["tax_id"]
    require(isinstance(tax_id, str) and len(tax_id) <= 40)
    tax_id = re.sub(r"[\s.\-]", "", tax_id).upper()
    require(bool((PASSPORT_RE if buyer_type == "passport" else DIGITS16_RE).match(tax_id)))
    email = value.get("email")
    if email is None or email == "":
        email = ""
    else:
        require(isinstance(email, str))
        email = email.strip().lower()
        require(len(email) <= 254 and bool(EMAIL_RE.match(email)))
    return {
        "buyer_type": buyer_type,
        "tax_id": tax_id,
        "name": _profile_text(value["name"], 2, 100),
        "address": _profile_text(value["address"], 5, 255),
        "email": email,
    }


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


# G22 W-04 B-4 (docs/04 §14.B, D-22-9): baris cart F&B membawa atribut `no_variant` (PTAV). Batas 10 nilai per baris.
MAX_ATTRIBUTE_VALUES = 10
CART_LINE_KEYS = frozenset({"product_id", "qty", "attribute_value_ids"})


def cart_lines_payload(lines, max_lines):
    """Validasi baris cart/quote/place (input BFF, docs/04 §14.B B-4).

    Baris = ``{product_id, qty, attribute_value_ids?: int[] <= 10}``. Identitas baris = ``(product_id,
    sorted(attribute_value_ids))`` -> produk sama dengan modifier berbeda boleh dua baris; identitas ganda / PTAV ganda
    dalam satu baris ditolak. ``attribute_value_ids: []`` sama dengan tanpa kunci. Kepemilikan PTAV divalidasi kanal
    (``_efn_online_line_ptavs``), bukan di sini.

    :returns: list terurut ``(product_id, tuple(ptav_ids terurut), qty)``.
    """
    require(isinstance(lines, list) and 0 < len(lines) <= min(max_lines, MAX_LINES_HARD))
    result = {}
    for line in lines:
        require(isinstance(line, dict) and set(line) <= CART_LINE_KEYS)
        product_id = check_int(line.get("product_id"), minimum=1)
        qty = check_int(line.get("qty"), minimum=1)
        raw = line.get("attribute_value_ids", [])
        require(isinstance(raw, list) and len(raw) <= MAX_ATTRIBUTE_VALUES)
        ptav_ids = tuple(sorted({check_int(value, minimum=1) for value in raw}))
        require(len(ptav_ids) == len(raw))
        key = (product_id, ptav_ids)
        require(key not in result)
        result[key] = qty
    return [(pid, ptav_ids, qty) for (pid, ptav_ids), qty in sorted(result.items())]


def cart_lines_canonical(normalized):
    """Bentuk kanonik baris untuk ``payload_sha256`` place: ``[pid, qty]`` tanpa atribut (hash G17..G21 tidak berubah)
    atau ``[pid, qty, [ptav...]]``."""
    return [[pid, qty, list(ptav_ids)] if ptav_ids else [pid, qty] for pid, ptav_ids, qty in normalized]


# G22 W-12 (docs/04 §14.C.3 "Place"): porsi Poin EFN per bagian. Klien BFF hanya mengirim jumlah poin; nilai Rupiah
# dihitung BFF (`points × point_value_idr`) dan DIVALIDASI ulang Odoo terhadap kebijakan PT (pertahanan berlapis).
POINTS_PAYMENT_KEYS = frozenset({"points", "value_idr"})
MAX_POINTS = 10**9


def points_payment_payload(value):
    """``points_payment`` place (opsional): ``None`` = tanpa poin; selain itu ``{points, value_idr}`` int > 0."""
    if value is None:
        return None
    require(isinstance(value, dict) and set(value) == POINTS_PAYMENT_KEYS)
    points = check_int(value["points"], minimum=1, maximum=MAX_POINTS)
    value_idr = check_amount_int(value["value_idr"])
    require(value_idr >= 1)
    return {"points": points, "value_idr": value_idr}


def round_half_up(value):
    """Pembulatan Rupiah setengah ke atas untuk nilai >= 0 (bukan banker's rounding ``round()``)."""
    return int(math.floor(value + 0.5 + 1e-9)) if value > 0 else 0


def floor_idr(value):
    """Rupiah dibulatkan ke bawah (dasar earn K-1/K-18-4, pola ``efn_pos_loyalty_ledger``); <= 0 -> 0."""
    return int(math.floor(value + 1e-6)) if value > 0 else 0


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
