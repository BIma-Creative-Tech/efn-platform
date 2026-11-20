"""Logika segmen murni (ADR-047 §3.2/§3.4; rencana G46: viewed-not-bought, wishlist-idle, OOS-viewed, price-sensitive,
search-no-result). Tanpa I/O: input = baris agregat per subjek x produk dari `efn_segment.*`, output = anggota segmen
(subject_key terurut) + hitungan agregat untuk ringkasan Odoo.

Definisi (parameter = `SegmentParams`, env SEGMENT_*):
- `viewed_not_bought`  : dalam jendela pendek (lookback_days) subjek melihat produk >= min_views kali ATAU total durasi
                         >= min_view_seconds, dan TIDAK ada `purchase`/`checkout_start` untuk produk itu di jendela.
- `wishlist_idle`      : wishlist aktif (wishlist_add terakhir > wishlist_remove terakhir) yang ditambahkan
                         >= wishlist_idle_days sebelum as_of, tanpa pembelian sesudah ditambahkan (jendela panjang).
- `oos_viewed`         : produk dilihat saat stok `out` (pengayaan server) di jendela pendek dan belum dibeli.
- `price_sensitive`    : pasangan viewed_not_bought dengan harga saat dilihat > (1 + price_margin) x
                         (a) median harga pasar Price Hunter tervalidasi 30 hari produk itu (`market`), ATAU
                         (b) median harga beli subjek di kategori yang sama, minimal min_category_purchases pembelian
                             di jendela panjang (`habit`).
- `search_no_result`   : >= min_no_result pencarian tanpa hasil di jendela pendek.
Subjek `device` (belum tertaut akun) dikecualikan kecuali include_device (default false): kampanye dikirim ke member.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

SEGMENT_CODES = ("viewed_not_bought", "wishlist_idle", "oos_viewed", "price_sensitive", "search_no_result")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def opt_ts(value: datetime | None) -> datetime | None:
    """ClickHouse maxIf tanpa baris cocok = 1970-01-01 -> None."""
    if value is None:
        return None
    ts = value if value.tzinfo else value.replace(tzinfo=UTC)
    return None if ts <= EPOCH else ts.astimezone(UTC)


@dataclass(frozen=True)
class SubjectProduct:
    subject_key: str
    subject_kind: str
    product_id: int
    views: int = 0
    view_seconds: int = 0
    oos_views: int = 0
    cart_adds: int = 0
    checkouts: int = 0
    purchases: int = 0
    last_view_at: datetime | None = None
    last_wishlist_add_at: datetime | None = None
    last_wishlist_remove_at: datetime | None = None
    last_purchase_at: datetime | None = None
    max_view_price_idr: int = 0
    purchase_price_sum: int = 0
    purchase_price_n: int = 0

    @property
    def bought(self) -> bool:
        return self.purchases > 0 or self.checkouts > 0


@dataclass(frozen=True)
class SubjectSearch:
    subject_key: str
    subject_kind: str
    searches: int
    no_result_searches: int


@dataclass(frozen=True)
class SegmentParams:
    lookback_days: int = 14
    wishlist_lookback_days: int = 60
    wishlist_idle_days: int = 7
    min_views: int = 2
    min_view_seconds: int = 30
    price_margin: float = 0.05
    min_category_purchases: int = 3
    min_no_result: int = 1
    include_device: bool = False
    segments: tuple[str, ...] = SEGMENT_CODES

    def kinds(self) -> tuple[str, ...]:
        return ("member", "device") if self.include_device else ("member",)


@dataclass
class Segment:
    code: str
    members: tuple[str, ...] = ()
    # produk -> jumlah subjek berbeda (untuk top_products ringkasan; tanpa subject_key).
    product_subjects: dict[int, int] = field(default_factory=dict)
    # price_sensitive: alasan -> jumlah subjek berbeda.
    reason_counts: dict[str, int] = field(default_factory=dict)

    @property
    def member_count(self) -> int:
        return len(self.members)


class _Builder:
    def __init__(self, code: str) -> None:
        self.code = code
        self.members: set[str] = set()
        self.by_product: dict[int, set[str]] = defaultdict(set)
        self.by_reason: dict[str, set[str]] = defaultdict(set)

    def add(self, subject: str, product_id: int | None = None, reasons: Iterable[str] = ()) -> None:
        self.members.add(subject)
        if product_id:
            self.by_product[product_id].add(subject)
        for reason in reasons:
            self.by_reason[reason].add(subject)

    def build(self) -> Segment:
        return Segment(
            code=self.code,
            members=tuple(sorted(self.members)),
            product_subjects={p: len(s) for p, s in self.by_product.items()},
            reason_counts={r: len(s) for r, s in sorted(self.by_reason.items())},
        )


def _eligible(kind: str, params: SegmentParams) -> bool:
    return kind in params.kinds()


def is_viewed_not_bought(row: SubjectProduct, params: SegmentParams) -> bool:
    if row.views <= 0 or row.bought:
        return False
    return row.views >= params.min_views or row.view_seconds >= params.min_view_seconds


def is_wishlist_idle(row: SubjectProduct, as_of: date, params: SegmentParams) -> bool:
    added = opt_ts(row.last_wishlist_add_at)
    if added is None:
        return False
    removed = opt_ts(row.last_wishlist_remove_at)
    if removed is not None and removed >= added:
        return False
    purchased = opt_ts(row.last_purchase_at)
    if purchased is not None and purchased >= added:
        return False
    cutoff = datetime.combine(as_of - timedelta(days=params.wishlist_idle_days), time.max, tzinfo=UTC)
    return added <= cutoff


def is_oos_viewed(row: SubjectProduct) -> bool:
    return row.oos_views > 0 and not row.bought


def habit_medians(
    rows: Iterable[SubjectProduct], categories: Mapping[int, str], min_purchases: int
) -> dict[tuple[str, str], float]:
    """(subject, kategori) -> median harga beli rata-rata per produk; hanya bila jumlah pembelian >= minimum."""
    prices: dict[tuple[str, str], list[float]] = defaultdict(list)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        category = categories.get(row.product_id, "")
        if not category or row.purchase_price_n <= 0 or row.purchase_price_sum <= 0:
            continue
        key = (row.subject_key, category)
        prices[key].append(row.purchase_price_sum / row.purchase_price_n)
        counts[key] += row.purchase_price_n
    return {key: statistics.median(values) for key, values in prices.items() if counts[key] >= min_purchases}


def price_reasons(
    row: SubjectProduct,
    params: SegmentParams,
    market: Mapping[int, float],
    habits: Mapping[tuple[str, str], float],
    categories: Mapping[int, str],
) -> list[str]:
    price = row.max_view_price_idr
    if price <= 0:
        return []
    factor = 1.0 + params.price_margin
    reasons: list[str] = []
    median_market = market.get(row.product_id)
    if median_market is not None and median_market > 0 and price > median_market * factor:
        reasons.append("market")
    category = categories.get(row.product_id, "")
    habit = habits.get((row.subject_key, category)) if category else None
    if habit is not None and habit > 0 and price > habit * factor:
        reasons.append("habit")
    return reasons


def compute_segments(
    short_rows: Iterable[SubjectProduct],
    long_rows: Iterable[SubjectProduct],
    searches: Iterable[SubjectSearch],
    categories: Mapping[int, str],
    market: Mapping[int, float],
    as_of: date,
    params: SegmentParams,
) -> list[Segment]:
    """Semua segmen yang diaktifkan (`params.segments`, urutan SEGMENT_CODES). Subjek boleh masuk lebih dari satu."""
    wanted = [c for c in SEGMENT_CODES if c in params.segments]
    builders = {code: _Builder(code) for code in wanted}
    short = [r for r in short_rows if _eligible(r.subject_kind, params)]
    long = [r for r in long_rows if _eligible(r.subject_kind, params)]
    habits = habit_medians(long, categories, params.min_category_purchases) if "price_sensitive" in builders else {}
    for row in short:
        vnb = is_viewed_not_bought(row, params)
        if vnb and "viewed_not_bought" in builders:
            builders["viewed_not_bought"].add(row.subject_key, row.product_id)
        if "oos_viewed" in builders and is_oos_viewed(row):
            builders["oos_viewed"].add(row.subject_key, row.product_id)
        if vnb and "price_sensitive" in builders:
            reasons = price_reasons(row, params, market, habits, categories)
            if reasons:
                builders["price_sensitive"].add(row.subject_key, row.product_id, reasons)
    if "wishlist_idle" in builders:
        for row in long:
            if is_wishlist_idle(row, as_of, params):
                builders["wishlist_idle"].add(row.subject_key, row.product_id)
    if "search_no_result" in builders:
        for search in searches:
            if _eligible(search.subject_kind, params) and search.no_result_searches >= params.min_no_result:
                builders["search_no_result"].add(search.subject_key)
    return [builders[code].build() for code in wanted]


def top_products(product_subjects: Mapping[int, int], k_min: int, top_n: int) -> list[dict[str, int]]:
    """Produk teratas per jumlah subjek, HANYA produk dengan >= k_min subjek (k-anonim ringkasan Odoo)."""
    eligible = [(p, n) for p, n in product_subjects.items() if n >= k_min]
    eligible.sort(key=lambda item: (-item[1], item[0]))
    return [{"product_id": int(p), "subjects": int(n)} for p, n in eligible[:top_n]]
