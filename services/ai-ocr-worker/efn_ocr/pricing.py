"""Parsing harga Rupiah dari teks OCR/VLM. Logika murni, tanpa I/O.

Format yang didukung: `Rp 12.500`, `Rp12.500,-`, `12,500`, `12.500,00`, `IDR 15000`, `12.5rb`, `12rb`, `10k`,
`1,2jt`. Angka yang diikuti satuan (`85g`, `500 ml`, `2x`, `20%`) dan angka panjang (barcode) diabaikan kecuali
diawali `Rp`/`IDR`.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MIN_PRICE = 100
MAX_PRICE = 100_000_000
MAX_DIGITS = 9

_CANDIDATE_RE = re.compile(
    r"(?<![\w.,])(?P<cur>rp\.?\s*|idr\s*)?(?P<num>\d[\d.,]*\d|\d)(?:\s*(?P<suffix>rb|ribu|k|jt|juta)(?![a-z]))?",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"\s*(?:%|(?:g|gr|gram|kg|mg|ml|l|lt|ltr|liter|pcs|pc|cm|mm|x|sachet|btl|pack|pak)(?![a-z]))",
    re.IGNORECASE,
)
_THOUSANDS_RE = re.compile(r"^\d{1,3}(?:([.,])\d{3})(?:\1\d{3})*$")
_MULTIPLIER = {"rb": 1_000, "ribu": 1_000, "k": 1_000, "jt": 1_000_000, "juta": 1_000_000}


@dataclass(frozen=True)
class PriceCandidate:
    value: int
    has_currency: bool


def _to_decimal(token: str) -> Decimal | None:
    """Tafsirkan pemisah ribuan/desimal gaya Indonesia maupun Inggris."""
    token = token.strip(".,")
    if not token:
        return None
    last_sep = max(token.rfind("."), token.rfind(","))
    try:
        if last_sep < 0:
            return Decimal(token)
        tail = token[last_sep + 1 :]
        if _THOUSANDS_RE.match(token):
            return Decimal(token.replace(".", "").replace(",", ""))
        if len(tail) in (1, 2):
            head = token[:last_sep].replace(".", "").replace(",", "")
            if not head.isdigit():
                return None
            return Decimal(f"{head}.{tail}")
    except InvalidOperation:
        return None
    return None


def _apply_multiplier(value: Decimal, suffix: str | None) -> int:
    multiplier = _MULTIPLIER.get((suffix or "").lower(), 1)
    return int((value * multiplier).to_integral_value(rounding=ROUND_HALF_UP))


def _in_range(value: int) -> bool:
    return MIN_PRICE <= value <= MAX_PRICE


def extract_price_candidates(text: str) -> list[PriceCandidate]:
    """Semua kandidat harga dalam teks, yang berawalan mata uang lebih dulu, tanpa duplikat."""
    found: list[PriceCandidate] = []
    for match in _CANDIDATE_RE.finditer(text or ""):
        num = match.group("num")
        has_currency = match.group("cur") is not None
        if sum(ch.isdigit() for ch in num) > MAX_DIGITS:
            continue
        if not has_currency and _UNIT_RE.match(text, match.end()):
            continue
        value = _to_decimal(num)
        if value is None:
            continue
        amount = _apply_multiplier(value, match.group("suffix"))
        if _in_range(amount):
            found.append(PriceCandidate(value=amount, has_currency=has_currency))
    ordered = [c for c in found if c.has_currency] + [c for c in found if not c.has_currency]
    unique: list[PriceCandidate] = []
    seen: set[int] = set()
    for candidate in ordered:
        if candidate.value not in seen:
            seen.add(candidate.value)
            unique.append(candidate)
    return unique


def parse_rupiah(value: object) -> int | None:
    """Satu harga dari input bebas (str, int, float, Decimal). None bila tidak ada harga yang masuk akal."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if _in_range(value) else None
    if isinstance(value, float | Decimal):
        try:
            amount = int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
        except InvalidOperation:
            return None
        return amount if _in_range(amount) else None
    candidates = extract_price_candidates(str(value))
    return candidates[0].value if candidates else None


def match_price(reported: int, candidates: list[int] | tuple[int, ...], tolerance_pct: float) -> int | None:
    """Kandidat terdekat ke harga yang dilaporkan dan masih dalam toleransi persen; None bila tidak ada."""
    if reported <= 0:
        return None
    limit = Decimal(reported) * Decimal(str(tolerance_pct)) / Decimal(100)
    within = [c for c in candidates if Decimal(abs(c - reported)) <= limit]
    if not within:
        return None
    return min(within, key=lambda c: abs(c - reported))
