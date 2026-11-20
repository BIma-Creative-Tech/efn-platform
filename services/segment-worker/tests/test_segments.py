"""Logika segmen dengan data sintetis (positif + negatif)."""

from datetime import UTC, date, datetime

from conftest import key

from efn_segment.segments import (
    SEGMENT_CODES,
    SegmentParams,
    SubjectProduct,
    SubjectSearch,
    compute_segments,
    habit_medians,
    is_viewed_not_bought,
    is_wishlist_idle,
    opt_ts,
    top_products,
)

AS_OF = date(2026, 11, 19)
P = SegmentParams()
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def sp(i: int, product: int, kind: str = "member", **kw: object) -> SubjectProduct:
    return SubjectProduct(subject_key=key(i), subject_kind=kind, product_id=product, **kw)  # type: ignore[arg-type]


def by_code(segments: list) -> dict:
    return {s.code: s for s in segments}


def test_opt_ts_epoch_is_none() -> None:
    assert opt_ts(EPOCH) is None
    assert opt_ts(None) is None
    assert opt_ts(datetime(2026, 11, 1)) == datetime(2026, 11, 1, tzinfo=UTC)


def test_viewed_not_bought_thresholds() -> None:
    assert is_viewed_not_bought(sp(1, 10, views=2), P)
    assert is_viewed_not_bought(sp(1, 10, views=1, view_seconds=45), P)
    assert not is_viewed_not_bought(sp(1, 10, views=1, view_seconds=5), P)  # sekilas
    assert not is_viewed_not_bought(sp(1, 10, views=5, purchases=1), P)  # sudah beli
    assert not is_viewed_not_bought(sp(1, 10, views=5, checkouts=1), P)
    assert not is_viewed_not_bought(sp(1, 10, views=0, view_seconds=100), P)


def test_wishlist_idle_rules() -> None:
    old = datetime(2026, 11, 1, 10, tzinfo=UTC)
    recent = datetime(2026, 11, 17, 10, tzinfo=UTC)
    assert is_wishlist_idle(sp(1, 10, last_wishlist_add_at=old), AS_OF, P)
    assert not is_wishlist_idle(sp(1, 10, last_wishlist_add_at=recent), AS_OF, P)  # < 7 hari
    assert not is_wishlist_idle(sp(1, 10, last_wishlist_add_at=old, last_wishlist_remove_at=recent), AS_OF, P)
    assert not is_wishlist_idle(sp(1, 10, last_wishlist_add_at=old, last_purchase_at=recent), AS_OF, P)
    # Dibeli SEBELUM ditambahkan ke wishlist -> tetap idle.
    before = datetime(2026, 10, 20, tzinfo=UTC)
    assert is_wishlist_idle(sp(1, 10, last_wishlist_add_at=old, last_purchase_at=before), AS_OF, P)
    assert not is_wishlist_idle(sp(1, 10, last_wishlist_add_at=EPOCH), AS_OF, P)
    # Tepat 7 hari sebelum as_of (12 Nov) masih termasuk.
    assert is_wishlist_idle(sp(1, 10, last_wishlist_add_at=datetime(2026, 11, 12, 23, tzinfo=UTC)), AS_OF, P)


def test_compute_all_segments() -> None:
    short = [
        sp(1, 10, views=3, max_view_price_idr=30000),  # viewed_not_bought + price (habit)
        sp(2, 11, views=1, oos_views=1),  # oos_viewed saja (views 1, durasi 0)
        sp(3, 12, views=4, purchases=1, oos_views=2),  # sudah beli -> tidak masuk mana pun
        sp(4, 13, views=2, max_view_price_idr=11000),  # viewed_not_bought + market
        sp(5, 10, kind="device", views=9, oos_views=3),  # device dikecualikan default
    ]
    long = [
        *short,
        # Subjek 1 biasa membeli kategori "Beras" di harga ~15-16 rb (3 pembelian).
        sp(1, 20, purchases=2, purchase_price_sum=30000, purchase_price_n=2),
        sp(1, 21, purchases=1, purchase_price_sum=16000, purchase_price_n=1),
        sp(6, 30, last_wishlist_add_at=datetime(2026, 10, 30, tzinfo=UTC)),
    ]
    categories = {10: "Beras", 20: "Beras", 21: "Beras", 13: "Minyak", 11: "Susu", 30: "Gula"}
    market = {13: 10000.0, 10: 50000.0}
    searches = [
        SubjectSearch(key(7), "member", 5, 2),
        SubjectSearch(key(8), "member", 3, 0),
        SubjectSearch(key(9), "device", 3, 3),
    ]
    segs = by_code(compute_segments(short, long, searches, categories, market, AS_OF, P))
    assert list(segs) == list(SEGMENT_CODES)
    assert segs["viewed_not_bought"].members == tuple(sorted([key(1), key(4)]))
    assert segs["oos_viewed"].members == (key(2),)
    assert segs["wishlist_idle"].members == (key(6),)
    assert segs["search_no_result"].members == (key(7),)
    ps = segs["price_sensitive"]
    assert ps.members == tuple(sorted([key(1), key(4)]))
    assert ps.reason_counts == {"habit": 1, "market": 1}
    assert segs["viewed_not_bought"].product_subjects == {10: 1, 13: 1}
    assert key(3) not in {m for s in segs.values() for m in s.members}
    assert key(5) not in {m for s in segs.values() for m in s.members}


def test_include_device_and_segment_selection() -> None:
    params = SegmentParams(include_device=True, segments=("oos_viewed",))
    segs = compute_segments([sp(5, 10, kind="device", views=1, oos_views=1)], [], [], {}, {}, AS_OF, params)
    assert [s.code for s in segs] == ["oos_viewed"]
    assert segs[0].members == (key(5),)


def test_price_sensitive_needs_signal() -> None:
    # Harga di bawah pasar dan tanpa histori kategori -> bukan price_sensitive.
    short = [sp(1, 10, views=3, max_view_price_idr=9000)]
    segs = by_code(compute_segments(short, short, [], {10: "Beras"}, {10: 10000.0}, AS_OF, P))
    assert segs["price_sensitive"].members == ()
    # Margin 5 %: 10.400 tidak > 10.500.
    short = [sp(1, 10, views=3, max_view_price_idr=10400)]
    segs = by_code(compute_segments(short, short, [], {}, {10: 10000.0}, AS_OF, P))
    assert segs["price_sensitive"].members == ()
    # Tanpa harga saat dilihat -> tidak dinilai.
    short = [sp(1, 10, views=3, max_view_price_idr=0)]
    segs = by_code(compute_segments(short, short, [], {}, {10: 1.0}, AS_OF, P))
    assert segs["price_sensitive"].members == ()


def test_habit_median_requires_min_purchases() -> None:
    rows = [
        sp(1, 20, purchase_price_sum=10000, purchase_price_n=1),
        sp(1, 21, purchase_price_sum=40000, purchase_price_n=2),
        sp(2, 20, purchase_price_sum=12000, purchase_price_n=1),
        sp(3, 99, purchase_price_sum=5000, purchase_price_n=5),  # tanpa kategori -> diabaikan
    ]
    medians = habit_medians(rows, {20: "Beras", 21: "Beras"}, 3)
    assert medians == {(key(1), "Beras"): 15000.0}


def test_empty_input_gives_empty_segments() -> None:
    segs = compute_segments([], [], [], {}, {}, AS_OF, P)
    assert all(s.member_count == 0 for s in segs)


def test_top_products_k_anonymity() -> None:
    counts = {1: 50, 2: 9, 3: 10, 4: 50, 5: 12}
    assert top_products(counts, 10, 3) == [
        {"product_id": 1, "subjects": 50},
        {"product_id": 4, "subjects": 50},
        {"product_id": 5, "subjects": 12},
    ]
    assert top_products(counts, 100, 10) == []
    assert top_products(counts, 10, 0) == []
