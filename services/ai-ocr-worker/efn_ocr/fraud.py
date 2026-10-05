"""Sinyal anti-fraud murni (R-B05): pHash, cap harian, geofence. Tanpa I/O."""

import math
import re
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_000.0
_PHASH_RE = re.compile(r"^[0-9a-f]{16}$")


# Lingkaran geofence: area operasi yang sah untuk laporan (mis. radius kota/area toko kompetitor).
@dataclass(frozen=True)
class GeoCircle:
    lat: float
    lng: float
    radius_m: float


def parse_geofence(raw: str) -> tuple[GeoCircle, ...]:
    """Format env `GEOFENCE_CIRCLES`: `lat,lng,radius_m;lat,lng,radius_m`. Kosong = geofence nonaktif."""
    circles: list[GeoCircle] = []
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(",")]
        if len(parts) != 3:
            raise ValueError(f"entri geofence tidak valid: {chunk!r}")
        lat, lng, radius = (float(part) for part in parts)
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0 and radius > 0):
            raise ValueError(f"entri geofence di luar rentang: {chunk!r}")
        circles.append(GeoCircle(lat=lat, lng=lng, radius_m=radius))
    return tuple(circles)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Jarak great-circle dalam meter."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def outside_geofence(lat: float | None, lng: float | None, circles: tuple[GeoCircle, ...]) -> bool | None:
    """True bila koordinat di luar semua lingkaran. None = tidak dievaluasi (nonaktif / tanpa koordinat)."""
    if not circles or lat is None or lng is None:
        return None
    return not any(haversine_m(lat, lng, c.lat, c.lng) <= c.radius_m for c in circles)


def is_valid_phash(value: str | None) -> bool:
    """pHash 64-bit dalam hex huruf kecil 16 karakter (format `str(imagehash.phash(img))`)."""
    return bool(value) and _PHASH_RE.match(value or "") is not None


def hamming_hex(a: str, b: str) -> int:
    """Jarak Hamming dua pHash hex. Referensi untuk query SQL `bit_count(... # ...)` di repository."""
    if not (is_valid_phash(a) and is_valid_phash(b)):
        raise ValueError("pHash harus 16 karakter hex huruf kecil")
    return (int(a, 16) ^ int(b, 16)).bit_count()


def daily_cap_exceeded(valid_today: int, cap: int) -> bool:
    """Cap <= 0 berarti nonaktif. Dihitung dari laporan berstatus valid hari ini (WIB), di luar laporan ini."""
    return cap > 0 and valid_today >= cap
