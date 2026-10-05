import pytest

from efn_ocr.fraud import (
    GeoCircle,
    daily_cap_exceeded,
    hamming_hex,
    haversine_m,
    is_valid_phash,
    outside_geofence,
    parse_geofence,
)

DENPASAR = GeoCircle(lat=-8.6705, lng=115.2126, radius_m=30_000)


def test_parse_geofence() -> None:
    circles = parse_geofence("-8.6705,115.2126,30000; -6.2,106.8,5000;")
    assert circles == (DENPASAR, GeoCircle(lat=-6.2, lng=106.8, radius_m=5000))
    assert parse_geofence("") == ()


@pytest.mark.parametrize("raw", ["1,2", "a,b,c", "91,0,100", "0,0,0", "0,181,10"])
def test_parse_geofence_rejects_invalid(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_geofence(raw)


def test_haversine_known_distance() -> None:
    # Denpasar -> Ubud kira-kira 19 km (garis lurus)
    distance = haversine_m(-8.6705, 115.2126, -8.5069, 115.2625)
    assert 17_000 < distance < 26_000
    assert haversine_m(1.0, 1.0, 1.0, 1.0) == 0.0


def test_outside_geofence() -> None:
    assert outside_geofence(-8.65, 115.22, (DENPASAR,)) is False
    assert outside_geofence(-6.2, 106.8, (DENPASAR,)) is True
    assert outside_geofence(None, 115.22, (DENPASAR,)) is None
    assert outside_geofence(-8.65, 115.22, ()) is None


def test_phash_helpers() -> None:
    assert is_valid_phash("0123456789abcdef")
    assert not is_valid_phash("0123456789ABCDEF")
    assert not is_valid_phash("xyz")
    assert not is_valid_phash(None)
    assert hamming_hex("0000000000000000", "0000000000000000") == 0
    assert hamming_hex("0000000000000000", "000000000000000f") == 4
    assert hamming_hex("ffffffffffffffff", "0000000000000000") == 64
    with pytest.raises(ValueError):
        hamming_hex("00", "0000000000000000")


def test_daily_cap() -> None:
    assert daily_cap_exceeded(20, 20)
    assert not daily_cap_exceeded(19, 20)
    assert not daily_cap_exceeded(500, 0)
