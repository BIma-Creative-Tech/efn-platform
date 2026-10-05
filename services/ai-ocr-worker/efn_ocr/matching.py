"""Fuzzy match nama produk (rapidfuzz). Skor 0..100."""

import re
import unicodedata

from rapidfuzz import fuzz

MIN_NAME_CHARS = 3
_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_name(value: str | None) -> str:
    """Huruf kecil ASCII, non-alfanumerik menjadi spasi."""
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()
    return _NON_ALNUM.sub(" ", text).strip()


def name_similarity(reported: str | None, extracted: str | None) -> float:
    """Skor kemiripan nama yang dilaporkan terhadap teks hasil ekstraksi.

    Memakai maksimum dari `token_set_ratio` (urutan kata bebas) dan `partial_ratio` (nama sebagai potongan teks
    label yang panjang/berisik). Nama laporan yang terlalu pendek (< 3 huruf/angka) selalu 0 agar tidak lolos
    dengan nama generik.
    """
    a = normalize_name(reported)
    b = normalize_name(extracted)
    if len(a.replace(" ", "")) < MIN_NAME_CHARS or not b:
        return 0.0
    return float(max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b)))
