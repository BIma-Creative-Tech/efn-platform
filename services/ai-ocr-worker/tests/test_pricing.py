from decimal import Decimal

import pytest

from efn_ocr.pricing import extract_price_candidates, match_price, parse_rupiah


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Rp 12.500", 12500),
        ("Rp12.500,-", 12500),
        ("Rp. 12.500", 12500),
        ("12,500", 12500),
        ("12.500,00", 12500),
        ("12,500.00", 12500),
        ("Rp 1.250.000", 1250000),
        ("IDR 15000", 15000),
        ("12.5rb", 12500),
        ("12,5 rb", 12500),
        ("12rb", 12000),
        ("12 ribu", 12000),
        ("10k", 10000),
        ("1,2jt", 1200000),
        ("harga 9.900", 9900),
    ],
)
def test_parse_rupiah_formats(text: str, expected: int) -> None:
    assert parse_rupiah(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "Rp", "8991234567890", "500 gr", "Diskon 20%", "Rp 50"])
def test_parse_rupiah_returns_none_for_non_prices(text: str) -> None:
    assert parse_rupiah(text) is None


def test_parse_rupiah_numeric_inputs() -> None:
    assert parse_rupiah(12500) == 12500
    assert parse_rupiah(Decimal("12500.00")) == 12500
    assert parse_rupiah(12499.6) == 12500
    assert parse_rupiah(None) is None
    assert parse_rupiah(True) is None
    assert parse_rupiah(10) is None
    assert parse_rupiah(10**12) is None


def test_candidates_skip_units_and_barcodes() -> None:
    text = "INDOMIE GORENG 85g 8991002101 Rp 3.500"
    assert [c.value for c in extract_price_candidates(text)] == [3500]


def test_candidates_currency_first_and_unique() -> None:
    text = "Harga normal 15.000 promo Rp 12.500 hemat 2.500 Rp 12.500"
    values = [c.value for c in extract_price_candidates(text)]
    assert values == [12500, 15000, 2500]


def test_candidates_currency_overrides_unit_filter() -> None:
    assert [c.value for c in extract_price_candidates("Rp 15.000 / pcs")] == [15000]


def test_match_price_within_tolerance() -> None:
    assert match_price(12500, [12700], 2.0) == 12700
    assert match_price(12500, [12750], 2.0) == 12750
    assert match_price(12500, [12751], 2.0) is None


def test_match_price_picks_closest() -> None:
    assert match_price(10000, [10150, 9950, 15000], 2.0) == 9950


def test_match_price_rejects_invalid_reported() -> None:
    assert match_price(0, [0, 100], 2.0) is None
    assert match_price(-5, [100], 2.0) is None


def test_match_price_empty_candidates() -> None:
    assert match_price(12500, [], 2.0) is None
