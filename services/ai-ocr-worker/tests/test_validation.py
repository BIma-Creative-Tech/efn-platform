from efn_ocr.validation import Extraction, FraudSignals, Reason, Status, Thresholds, decide, screen_fraud

THRESHOLDS = Thresholds(name_similarity=75.0, price_tolerance_pct=2.0, daily_cap=20)
CLEAN = FraudSignals()


def _extraction(text: str, prices: tuple[int, ...]) -> Extraction:
    return Extraction(engine="tesseract", name_text=text, price_candidates=prices, ocr_confidence=0.9)


def test_valid_report() -> None:
    decision = decide("Indomie Goreng", 3500, _extraction("INDOMIE GORENG 85g", (3500,)), CLEAN, THRESHOLDS)
    assert decision.status is Status.VALID
    assert decision.reason is Reason.MATCH
    assert decision.matched_price == 3500


def test_price_within_tolerance_is_valid() -> None:
    decision = decide("Indomie Goreng", 3500, _extraction("INDOMIE GORENG", (3560,)), CLEAN, THRESHOLDS)
    assert decision.status is Status.VALID
    assert decision.matched_price == 3560


def test_price_mismatch_rejected() -> None:
    decision = decide("Indomie Goreng", 3500, _extraction("INDOMIE GORENG", (3900,)), CLEAN, THRESHOLDS)
    assert decision.status is Status.REJECTED
    assert decision.reason is Reason.PRICE_MISMATCH


def test_price_not_found_rejected() -> None:
    decision = decide("Indomie Goreng", 3500, _extraction("INDOMIE GORENG", ()), CLEAN, THRESHOLDS)
    assert decision.reason is Reason.PRICE_NOT_FOUND


def test_name_mismatch_rejected() -> None:
    decision = decide("Beras Ramos 5kg", 3500, _extraction("MINYAK GORENG BIMOLI", (3500,)), CLEAN, THRESHOLDS)
    assert decision.status is Status.REJECTED
    assert decision.reason is Reason.NAME_MISMATCH


def test_invalid_reported_price_rejected() -> None:
    decision = decide("Indomie Goreng", 0, _extraction("INDOMIE GORENG", (3500,)), CLEAN, THRESHOLDS)
    assert decision.reason is Reason.INVALID_REPORTED_PRICE


def test_missing_extraction_is_flagged_not_crash() -> None:
    decision = decide("Indomie Goreng", 3500, None, CLEAN, THRESHOLDS)
    assert decision.status is Status.FLAGGED
    assert decision.reason is Reason.VLM_UNAVAILABLE
    disabled = decide("Indomie Goreng", 3500, None, CLEAN, THRESHOLDS, Reason.VLM_DISABLED)
    assert disabled.reason is Reason.VLM_DISABLED


def test_fraud_signals_take_precedence() -> None:
    good = _extraction("INDOMIE GORENG", (3500,))
    dup = decide("Indomie Goreng", 3500, good, FraudSignals(duplicate_of="abc"), THRESHOLDS)
    assert (dup.status, dup.reason) == (Status.FLAGGED, Reason.DUPLICATE_IMAGE)
    cap = decide("Indomie Goreng", 3500, good, FraudSignals(valid_today=20), THRESHOLDS)
    assert (cap.status, cap.reason) == (Status.REJECTED, Reason.DAILY_CAP)
    geo = decide("Indomie Goreng", 3500, good, FraudSignals(outside_geofence=True), THRESHOLDS)
    assert (geo.status, geo.reason) == (Status.FLAGGED, Reason.OUTSIDE_GEOFENCE)


def test_screen_fraud_clean() -> None:
    assert screen_fraud(CLEAN, THRESHOLDS) is None
    assert screen_fraud(FraudSignals(valid_today=19, outside_geofence=False), THRESHOLDS) is None
