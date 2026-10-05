import pytest

from efn_ocr.errors import VlmUnavailableError
from efn_ocr.ocr_engines import aggregate_tesseract
from efn_ocr.vlm import parse_vlm_response


def test_aggregate_tesseract_skips_empty_and_negative() -> None:
    texts = ["", "INDOMIE", "GORENG", " ", "Rp", "3.500"]
    confs = ["-1", "90", 80, "-1", "70.5", "bad"]
    text, confidence = aggregate_tesseract(texts, confs)
    assert text == "INDOMIE GORENG Rp"
    assert confidence == pytest.approx((90 + 80 + 70.5) / 3 / 100, abs=1e-4)


def test_aggregate_tesseract_empty() -> None:
    assert aggregate_tesseract([], []) == ("", 0.0)


def test_parse_vlm_response_ok() -> None:
    result = parse_vlm_response('{"product_name": " Indomie Goreng ", "price": "Rp 3.500"}')
    assert result.product_name == "Indomie Goreng"
    assert result.price == 3500


def test_parse_vlm_response_numeric_and_missing_price() -> None:
    assert parse_vlm_response('{"product_name": "Gula", "price": 15000}').price == 15000
    missing = parse_vlm_response('{"product_name": "Gula", "price": ""}')
    assert missing.price is None


@pytest.mark.parametrize("raw", ["", "not json", "[1,2]", '"text"'])
def test_parse_vlm_response_invalid(raw: str) -> None:
    with pytest.raises(VlmUnavailableError):
        parse_vlm_response(raw)
