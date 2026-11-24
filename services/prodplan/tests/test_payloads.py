"""Payload ESB kedua mode + marker (docs/04 §30.9)."""

from datetime import date
from decimal import Decimal

import pytest

from efn_prodplan.payloads import (
    MARKER_RE,
    InvalidQuantity,
    doc_marker,
    doc_status_id,
    goods_transfer_request_body,
    list_doc_numbers,
    manufacturing_body,
    new_marker,
    num,
    simple_transfer_body,
)

DAY = date(2026, 11, 25)
M = "pp:0123456789ab"
LINES = [{"product_detail_id": 21, "qty": Decimal("1000.0000")}, {"product_detail_id": 31, "qty": Decimal("2.5")}]
PROD_LINE = {
    "bom_id": 104,
    "product_detail_id": 1004,
    "qty": Decimal("10.0000"),
    "materials": [
        {"product_detail_id": 2001, "total_qty": Decimal("2.0000")},
        {"product_detail_id": 31, "total_qty": "0.33335"},
    ],
}


def test_marker_format_and_uniqueness():
    markers = {new_marker() for _ in range(200)}
    assert len(markers) == 200
    assert all(MARKER_RE.fullmatch(m) and len(m) == 15 for m in markers)


def test_num():
    assert num(Decimal("1000.0000")) == 1000 and isinstance(num(Decimal("1000.0000")), int)
    assert num(Decimal("2.5")) == 2.5
    assert num("0.33335") == 0.3334


def test_non_positive_quantities_rejected_before_send():
    # SEC-231: qty <= 0 / bukan angka hingga ditolak sebelum body disimpan/dikirim
    for bad in (Decimal(0), Decimal("-1"), "0.00004", "NaN", "Infinity", "abc"):
        with pytest.raises(InvalidQuantity):
            num(bad)
    negative = {**PROD_LINE, "materials": [{"product_detail_id": 31, "total_qty": "-2"}]}
    with pytest.raises(InvalidQuantity):
        manufacturing_body("actual", 4, 7, DAY, M, negative)
    with pytest.raises(InvalidQuantity):
        simple_transfer_body(8, 7, DAY, M, [{"product_detail_id": 21, "qty": 0}])


def test_simple_transfer_body():
    assert simple_transfer_body(8, 7, DAY, M, LINES) == {
        "originLocationID": 8,
        "destinationLocationID": 7,
        "simpleTransferDate": "2026-11-25",
        "additionalInfo": M,
        "simpleTransferDetails": [{"productDetailID": 21, "qty": 1000}, {"productDetailID": 31, "qty": 2.5}],
    }


def test_goods_transfer_request_body_without_assets_or_pr():
    body = goods_transfer_request_body(13, 4, 21, DAY, M, LINES)
    assert body == {
        "originBranchID": 13,
        "destinationBranchID": 4,
        "originLocationID": 21,
        "categoryTypeID": 1,
        "transferDate": "2026-11-25",
        "additionalInfo": M,
        "transferDetails": [
            {"productDetailID": 21, "qty": 1000, "requestQty": 1000},
            {"productDetailID": 31, "qty": 2.5, "requestQty": 2.5},
        ],
    }
    assert "transferDetailAssets" not in body and "purchaseRequestNum" not in body


def test_manufacturing_body_actual():
    assert manufacturing_body("actual", 4, 7, DAY, M, PROD_LINE) == {
        "branchID": 4,
        "originLocationID": 7,
        "destinationLocationID": 7,
        "simpleManufacturingDate": "2026-11-25",
        "simpleManufacturingDetails": [
            {
                "bomID": 104,
                "productDetailID": 1004,
                "manufacturingQty": 10,
                "resultQty": 10,
                "notes": M,
                "simpleManufacturingMaterials": [
                    {"productDetailID": 2001, "totalQty": 2},
                    {"productDetailID": 31, "totalQty": 0.3334},
                ],
            }
        ],
    }


def test_manufacturing_body_standard():
    assert manufacturing_body("standard", 4, 7, DAY, M, PROD_LINE) == {
        "branchID": 4,
        "originLocationID": 7,
        "destinationLocationID": 7,
        "simpleManufacturingDate": "2026-11-25",
        "simpleManufacturingDetails": [{"bomID": 104, "productDetailID": 1004, "qty": 10, "additionalInfo": M}],
    }
    with pytest.raises(ValueError):
        manufacturing_body("other", 4, 7, DAY, M, PROD_LINE)


def test_doc_marker_and_status():
    assert doc_marker({"additionalInfo": M, "statusID": 1}) == M
    assert doc_marker({"additionalInfo": "", "simpleManufacturingDetails": [{"additionalInfo": M}]}) == M
    assert doc_marker({"additionalInfo": "manual"}) is None
    assert doc_marker(None) is None
    assert doc_status_id({"statusID": 3}) == 3
    assert doc_status_id({"statusID": "3"}) == 3
    assert doc_status_id({"statusID": True}) is None
    assert doc_status_id({}) is None


def test_list_doc_numbers():
    rows = [{"simpleManufacturingNum": "SM202611240001 - 1"}, {"simpleManufacturingNum": "SM202611240001 - 1"}, {}]
    assert list_doc_numbers("simple_manufacturing", rows) == ["SM202611240001 - 1"]
    assert list_doc_numbers("goods_transfer_request", [{"transferNum": "TR1"}]) == ["TR1"]
