"""Normalisasi & masukan token nama (ADR-036 §0.5). Murni Python (tanpa import Odoo/kunci).

Normalisasi `N(s)` (sama saat tulis dan cari):
1. Unicode NFKC -> casefold() -> NFKD lalu buang tanda diakritik (é -> e).
2. Karakter selain [a-z0-9] -> spasi ("Hj.", "Al-Fatih" -> "hj", "al fatih").
3. Rapatkan spasi.
Tidak ada kamus gelar/sinonim/fonetik ("Muhammad" != "Muhamad"). Huruf non-Latin (mis. aksara Han)
terbuang di langkah 2, sehingga nama yang seluruhnya non-Latin tidak punya token (tidak bisa dicari).

Jenis token (HMAC dihitung di keyring.KeyRing.name_token):
- `f`: N(name) dan N(complete_name) (bila berbeda)          -> `=`, `in`, `=ilike` tanpa wildcard
- `w`: setiap kata panjang >= 2                               -> kata utuh (cadangan; tidak dipakai kueri tahap 1)
- `p`: awalan kata w[:L], 3 <= L <= min(len(w), 15)           -> `ilike`/`like` (AND semua kata, tanpa infiks)
"""

import re
import unicodedata

MIN_WORD = 2
MIN_PREFIX = 3
MAX_PREFIX = 15
PAD_MULTIPLE = 16

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_LIKE_WILDCARD_RE = re.compile(r"[%_]")


def normalize(text):
    if not isinstance(text, str) or not text:
        return ""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = _NON_ALNUM_RE.sub(" ", value)
    return " ".join(value.split())


def record_inputs(name, complete_name):
    """Masukan plaintext ternormalisasi per jenis token untuk satu record: {"f": set, "w": set, "p": set}."""
    n_name = normalize(name)
    n_full = normalize(complete_name) or n_name
    full = {value for value in (n_name, n_full) if value}
    words = set(n_full.split()) | set(n_name.split())
    whole = {word for word in words if len(word) >= MIN_WORD}
    prefixes = {word[:size] for word in words for size in range(MIN_PREFIX, min(len(word), MAX_PREFIX) + 1)}
    return {"f": full, "w": whole, "p": prefixes}


def exact_input(value):
    """Masukan token `f` untuk pencarian nama lengkap persis; "" bila kosong."""
    return normalize(value)


def has_like_wildcard(value):
    return isinstance(value, str) and bool(_LIKE_WILDCARD_RE.search(value))


def prefix_query_inputs(value):
    """Masukan token `p` untuk `ilike`/`like` (semua wajib cocok, AND).

    Wildcard `%`, `_`, `*` dan tanda baca jadi pemisah kata (langkah 2 normalisasi). Kata < 3 huruf
    diabaikan bila ada kata lain. Kata > 15 huruf dipotong ke 15 (awalan maksimum yang disimpan).

    :returns: ``[]`` bila kueri tidak berisi huruf/angka sama sekali (pola "semua"), ``None`` bila
        tidak ada kata >= 3 huruf (tidak cocok untuk partner terlindungi), selain itu daftar terurut.
    """
    words = normalize(value if isinstance(value, str) else str(value or "")).split()
    if not words:
        return []
    long_words = sorted({word[:MAX_PREFIX] for word in words if len(word) >= MIN_PREFIX})
    return long_words or None


MAX_PAD_ATTEMPTS = 10_000


def pad_tokens(tokens, pad_token, multiple=PAD_MULTIPLE, extra_blocks=0):
    """Urutkan + padatkan ke kelipatan ``multiple`` (+ ``extra_blocks`` blok penuh) dengan token padding unik.

    ``pad_token(i)`` mengembalikan kandidat ke-i (SEC-149: deterministik per record dan per himpunan token asli,
    sehingga menghitung ulang token untuk nama yang sama menghasilkan kolom identik; dua snapshot tidak lagi
    memisahkan token asli dari padding). Token padding diurutkan bersama token asli (posisi tidak membedakan).
    """
    real = set(tokens)
    if not real:
        return []
    need = (-len(real)) % multiple + multiple * max(0, int(extra_blocks))
    pads = set()
    index = 0
    while len(pads) < need:
        if index >= MAX_PAD_ATTEMPTS:
            raise ValueError("padding token nama tidak konvergen")
        candidate = pad_token(index)
        index += 1
        if candidate not in real:
            pads.add(candidate)
    return sorted(real | pads)
