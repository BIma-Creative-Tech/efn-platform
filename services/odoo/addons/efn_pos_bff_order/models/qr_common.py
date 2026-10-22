"""Helper bersama E-27 (bukan model): kode error kontrak, validasi input BFF, hash kanonik, seq event.

Kontrak: docs/04_SERVICE_CONTRACTS.md §14.A.5 (galat bisnis = ``UserError("efn_qr:<kode>")`` -> HTTP 422, L-15),
§14.A.3 (validasi catatan), §14.A.7 (batas), §14.7 L-70..L-72. Spesifikasi: docs/08 §13.1b.
"""

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime

from odoo.exceptions import UserError

# Kode galat bisnis yang boleh keluar ke BFF (§14.A.5). Peta ke HTTP ada di BFF.
ERROR_CODES = frozenset(
    {
        "session_closed",
        "pos_session_closed",
        "item_unavailable",
        "invalid_input",
        "idempotency_conflict",
        "qr_disabled",
        "limit_exceeded",
    }
)

# Batas keras Odoo (sama dengan default BFF §14.A.7; BFF memvalidasi lebih dulu). Batas per sesi meja = field config.
MAX_LINES = 30  # FNB_MAX_LINES
MAX_LINE_QTY = 20  # FNB_MAX_LINE_QTY
MAX_ATTRIBUTE_VALUES = 10
MAX_NOTE = 140
MAX_STATUS_REFS = 100
MAX_MENU_LIMIT = 200

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Kunci idempotensi BFF: `fnb:submit:<submission_id>` (§14.A.3); batas umum agar aman disimpan.
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_:.-]{8,128}$")
# order_uuid POS 19 = uuid4 (frontend `uuidv4()`); `pos.order.uuid` Char. Batas 64 sesuai kolom BFF.
ORDER_UUID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# §14.A.3 [D-22-6] + SEC-168 (r15): filter PII catatan, IDENTIK PERSIS dengan BFF ``sanitizeNote``
# (services/api-gateway/src/fnb/fnb.model.ts; aturan lengkap di README "Filter catatan"). Urutan:
# 0 None -> kosong; bukan str / > 1000 mentah -> tolak; 1 NFKC; 2 HAPUS rentang format NOTE_FORMAT_RANGES;
# 3 kontrol C0, DEL+C1, U+2028, U+2029 -> spasi; 4 runtun whitespace -> satu spasi, trim; 5 kosong -> tanpa catatan;
# 6 > 140 code point -> tolak; 7 tolak bila '@', ATAU Σ digit Nd >= 8, ATAU cocok deret ASCII lama.
NOTE_RAW_MAX = 1000
NOTE_FORMAT_RANGES = (
    (0x200B, 0x200F),  # zero-width space/non-joiner/joiner, LRM, RLM
    (0x202A, 0x202E),  # bidi embedding/override
    (0x2060, 0x2064),  # word joiner, invisible operators
    (0x2066, 0x2069),  # bidi isolate
    (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
)
NOTE_CONTROL_RANGES = ((0x0000, 0x001F), (0x007F, 0x009F), (0x2028, 0x2029))
NOTE_PHONE_RE = re.compile(r"[0-9][0-9\s.-]{6,}[0-9]")  # 7b: aturan lama, digit ASCII saja
NOTE_MAX_DIGITS = 7  # 7a: lebih dari 7 digit Nd (yaitu >= 8) -> tolak

ALLOWED_LANGS = ("id_ID", "en_US")

SUBMISSION_STATES = (
    "pending_confirmation",
    "accepted",
    "preparing",
    "ready",
    "served",
    "rejected",
    "cancelled",
)
TERMINAL_STATES = ("rejected", "cancelled")
STATE_RANK = {"pending_confirmation": 0, "accepted": 1, "preparing": 2, "ready": 3, "served": 4}

# seq event berbasis waktu (pola G18-QA-01 efn_online_order): tetap naik setelah DB dipulihkan.
SEQ_EPOCH = datetime(2026, 1, 1)


MAX_ERROR_IDS = 30  # r15: ``efn_qr:item_unavailable:<id>,<id>`` (≤ 30 id desimal, tanpa spasi; BFF QR_BUSINESS_RE)


def qr_error(code, product_ids=None):
    """``UserError`` dengan pesan stabil ``efn_qr:<kode>`` (BFF hanya membaca pesan).

    r15: ``item_unavailable`` boleh membawa id produk -> ``efn_qr:item_unavailable:<id>,<id>`` (urut naik, unik,
    ≤ 30). Pemanggil yang hanya mencocokkan prefiks ``efn_qr:item_unavailable`` tetap bekerja."""
    if code not in ERROR_CODES:
        code = "invalid_input"
    if code == "item_unavailable" and product_ids:
        ids = sorted({i for i in product_ids if isinstance(i, int) and not isinstance(i, bool) and 0 < i < 2**31})
        if ids:
            return UserError(f"efn_qr:{code}:{','.join(str(i) for i in ids[:MAX_ERROR_IDS])}")
    return UserError(f"efn_qr:{code}")


def require(condition, code="invalid_input"):
    if not condition:
        raise qr_error(code)


def check_int(value, minimum=None, maximum=None, code="invalid_input"):
    require(isinstance(value, int) and not isinstance(value, bool), "invalid_input")
    if minimum is not None:
        require(value >= minimum, code)
    if maximum is not None:
        require(value <= maximum, code)
    return value


def check_str(value, pattern=None, max_len=128):
    require(isinstance(value, str) and 0 < len(value) <= max_len)
    if pattern is not None:
        require(bool(pattern.match(value)))
    return value


def _in_ranges(code, ranges):
    return any(low <= code <= high for low, high in ranges)


def normalize_note(value):
    """SEC-168 langkah 1-4: NFKC, HAPUS karakter format (rentang ``NOTE_FORMAT_RANGES``, bukan diganti spasi, agar
    ``0812<ZWSP>3456<ZWSP>789`` menjadi deret digit utuh), kontrol -> spasi, runtun whitespace -> satu spasi, trim."""
    text = unicodedata.normalize("NFKC", value)
    chars = []
    for char in text:
        code = ord(char)
        if _in_ranges(code, NOTE_FORMAT_RANGES):
            continue
        chars.append(" " if _in_ranges(code, NOTE_CONTROL_RANGES) else char)
    return " ".join("".join(chars).split())


def note_has_pii(text):
    """SEC-168 langkah 7: '@', ATAU Σ digit desimal Unicode (Nd, ``str.isdecimal``) >= 8, ATAU deret ASCII lama."""
    if "@" in text or NOTE_PHONE_RE.search(text):
        return True
    return sum(1 for char in text if char.isdecimal()) > NOTE_MAX_DIGITS


def clean_note(value):
    """§14.A.3 + SEC-168: aturan identik BFF ``sanitizeNote`` (lihat komentar konstanta di atas).

    Mengembalikan teks bersih ('' bila kosong). Teks catatan TIDAK pernah di-log."""
    if value is None:
        return ""
    require(isinstance(value, str) and len(value) <= NOTE_RAW_MAX)
    text = normalize_note(value)
    require(len(text) <= MAX_NOTE)
    require(not note_has_pii(text))
    return text


def note_json(text):
    """Format catatan baris POS 19 (``pos.order.line.note`` = JSON list ``[{"text", "colorIndex"}]``,
    ``pos_order_line.js`` default ``"[]"``; dibaca ``efn_pos_kds.note_text``)."""
    if not text:
        return False
    return json.dumps([{"text": text, "colorIndex": 0}], ensure_ascii=False)


def canonical_sha256(data):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def epoch_seconds(now=None):
    """Detik sejak 2026-01-01 UTC (``now`` = datetime naive UTC, default jam sekarang)."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    return int((now - SEQ_EPOCH).total_seconds())


def iso_utc(value):
    if not value:
        return None
    return value.replace(microsecond=0).isoformat() + "Z"


def parse_since(value):
    """``since`` RPC menu: None/'' = sinkron penuh; selain itu ISO-8601 UTC (``Z``/``+00:00``/tanpa zona)."""
    if value in (None, "", False):
        return None
    check_str(value, max_len=40)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    elif text.endswith("+00:00"):
        text = text[:-6]
    try:
        return datetime.fromisoformat(text.replace(" ", "T").split(".")[0])
    except ValueError:
        raise qr_error("invalid_input") from None
