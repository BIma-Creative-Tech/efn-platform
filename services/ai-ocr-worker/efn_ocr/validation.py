"""Keputusan validasi laporan Price Hunter (logika murni, ADR-008 + R-B05).

Urutan keputusan:
1. Sinyal fraud (pHash duplikat -> flagged, cap harian -> rejected, di luar geofence -> flagged).
2. Ekstraksi tidak tersedia (VLM mati/nonaktif saat OCR klasik ragu) -> flagged untuk review manual.
3. Harga tidak ditemukan / tidak cocok dalam toleransi -> rejected.
4. Nama di bawah ambang -> rejected.
5. Selain itu -> valid.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from efn_ocr.fraud import daily_cap_exceeded
from efn_ocr.matching import name_similarity
from efn_ocr.pricing import match_price


class Status(StrEnum):
    VALID = "valid"
    REJECTED = "rejected"
    FLAGGED = "flagged"


class Reason(StrEnum):
    MATCH = "price_and_name_match"
    DUPLICATE_IMAGE = "duplicate_image"
    DAILY_CAP = "daily_cap_exceeded"
    OUTSIDE_GEOFENCE = "outside_geofence"
    VLM_UNAVAILABLE = "vlm_unavailable"
    VLM_DISABLED = "vlm_disabled"
    INVALID_REPORTED_PRICE = "invalid_reported_price"
    PRICE_NOT_FOUND = "price_not_found"
    PRICE_MISMATCH = "price_mismatch"
    NAME_MISMATCH = "name_mismatch"
    IMAGE_MISSING = "image_missing"
    IMAGE_UNREADABLE = "image_unreadable"
    PROCESSING_FAILED = "processing_failed"


@dataclass(frozen=True)
class Thresholds:
    name_similarity: float = 75.0
    price_tolerance_pct: float = 2.0
    daily_cap: int = 20


@dataclass(frozen=True)
class FraudSignals:
    duplicate_of: str | None = None
    valid_today: int = 0
    outside_geofence: bool | None = None


@dataclass(frozen=True)
class Extraction:
    engine: str
    name_text: str
    price_candidates: tuple[int, ...] = field(default_factory=tuple)
    ocr_confidence: float | None = None


@dataclass(frozen=True)
class Decision:
    status: Status
    reason: Reason
    matched_price: int | None = None
    name_score: float = 0.0


def screen_fraud(signals: FraudSignals, thresholds: Thresholds) -> Decision | None:
    """Keputusan dini dari sinyal fraud, sebelum OCR dijalankan (hemat CPU/GPU)."""
    if signals.duplicate_of:
        return Decision(Status.FLAGGED, Reason.DUPLICATE_IMAGE)
    if daily_cap_exceeded(signals.valid_today, thresholds.daily_cap):
        return Decision(Status.REJECTED, Reason.DAILY_CAP)
    if signals.outside_geofence:
        return Decision(Status.FLAGGED, Reason.OUTSIDE_GEOFENCE)
    return None


def decide(
    reported_name: str,
    reported_price: int,
    extraction: Extraction | None,
    signals: FraudSignals,
    thresholds: Thresholds,
    unavailable_reason: Reason = Reason.VLM_UNAVAILABLE,
) -> Decision:
    fraud = screen_fraud(signals, thresholds)
    if fraud is not None:
        return fraud
    if extraction is None:
        return Decision(Status.FLAGGED, unavailable_reason)
    if reported_price <= 0:
        return Decision(Status.REJECTED, Reason.INVALID_REPORTED_PRICE)
    score = name_similarity(reported_name, extraction.name_text)
    if not extraction.price_candidates:
        return Decision(Status.REJECTED, Reason.PRICE_NOT_FOUND, name_score=score)
    matched = match_price(reported_price, extraction.price_candidates, thresholds.price_tolerance_pct)
    if matched is None:
        return Decision(Status.REJECTED, Reason.PRICE_MISMATCH, name_score=score)
    if score < thresholds.name_similarity:
        return Decision(Status.REJECTED, Reason.NAME_MISMATCH, matched_price=matched, name_score=score)
    return Decision(Status.VALID, Reason.MATCH, matched_price=matched, name_score=score)
