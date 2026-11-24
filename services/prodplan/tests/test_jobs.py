"""Parser respons ESB + katalog BOM + pemetaan PR (murni / ESB palsu)."""

from decimal import Decimal as D

import pytest

from efn_prodplan.calc import CalcError
from efn_prodplan.esb import CallBudgetExceeded, EsbAuthError, EsbError
from efn_prodplan.jobs import (
    BomCatalog,
    BomHeader,
    JobAbort,
    aggregate_pr,
    boms_covering,
    fetch_boms,
    index_by_product,
    job_error_code,
    map_pr_demand,
    parse_bom_detail,
    parse_bom_header,
    parse_bom_list,
    parse_master_uom,
    pr_number,
    validate_search,
)

HDR = BomHeader(101, 1001, "FIN-A", "Roti A", "PCS", "BOM Roti A", "B-101")


class FakeReader:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        value = self.routes[path]
        return value(params) if callable(value) else value

    def get_all(self, path, params=None, *, limit=200, max_pages=100):
        result = self.get(path, params)
        return result["data"] if isinstance(result, dict) else result


def test_parse_bom_list_and_header():
    listed = parse_bom_list(
        [
            {"bomID": 101, "bomName": "BOM Roti A", "bomCode": "B-101"},
            {"bomID": 102, "bomName": "x", "flagActive": 0},
            {"bomID": 103, "bomName": "y", "bomTypeID": 2},
            {"bomID": 0},
        ]
    )
    assert listed == {101: {"bom_name": "BOM Roti A", "bom_code": "B-101"}}
    doc = {
        "bomID": 101,
        "bomTypeID": 1,
        "productDetailID": 1001,
        "productCode": "FIN-A",
        "productName": "Roti A",
        "uomName": "PCS",
        "bomName": "BOM Roti A",
    }
    assert parse_bom_header(doc, listed[101]) == HDR
    assert parse_bom_header({**doc, "bomTypeID": 2}) is None
    assert parse_bom_header({**doc, "productDetailID": None}) is None
    assert parse_bom_header(None) is None


def test_index_by_product_prefers_smallest_bom():
    h2 = BomHeader(90, 1001, "FIN-A", "Roti A", "PCS")
    assert index_by_product({101: HDR, 90: h2}) == {1001: 90}


def test_parse_bom_detail_skips_substitutes_and_reads_stock():
    rows = [
        {
            "productDetailID": 21,
            "productCode": "RMT000021",
            "productName": "Susu",
            "uomName": "ML",
            "qty": 45,
            "yieldPercent": 100,
            "stockQty": "700.5",
        },
        {
            "productDetailID": 22,
            "productCode": "RMT000022",
            "uomName": "ML",
            "qty": 45,
            "subtitution": 1,
            "stockQty": -3,
        },
        {"productDetailID": 0, "qty": 1},
    ]
    detail = parse_bom_detail(HDR, rows)
    assert [ln.product_code for ln in detail.bom.lines] == ["RMT000021"]
    assert detail.bom.lines[0].qty == D(45) and detail.bom.lines[0].yield_percent == D(100)
    assert detail.stock == {21: D("700.5"), 22: D(-3)}
    assert detail.skipped_substitutes == ["RMT000022"]
    # Putaran 2 (SEC-231): qty tak terbaca tidak lagi menggagalkan job; baris dilewati + peringatan.
    bad = parse_bom_detail(HDR, [{"productDetailID": 5, "productCode": "X", "qty": "abc"}])
    assert bad.bom.lines == () and bad.invalid_qty == ["X"]


def test_master_uom_and_pr_number():
    assert parse_master_uom([{"productCode": "A", "uomName": "KG"}, {"productCode": "B"}, {"uomName": "X"}]) == {
        "A": "KG"
    }
    assert pr_number({"purchaseRequestNum": " PR1 "}) == "PR1"
    assert pr_number({}) is None


def test_aggregate_and_map_pr():
    details = [
        (
            "PR1",
            {
                "purchaseRequestDetails": [
                    {"productDetailID": 1001, "productCode": "FIN-A", "uomName": "PCS", "qty": 10},
                    {"productDetailID": 5000, "productCode": "RAW-X", "uomName": "KG", "qty": 2},
                    {"productDetailID": 0, "productCode": "??", "qty": 1},
                ]
            },
        ),
        (
            "PR2",
            {
                "purchaseRequestDetails": [
                    {"productDetailID": 1001, "productCode": "FIN-A", "uomName": "PCS", "qty": "5.5"},
                    {"productDetailID": 1002, "productCode": "FIN-B", "uomName": "BOX", "qty": 1},
                    {"productDetailID": 1003, "productCode": "FIN-C", "uomName": "PCS", "qty": 0},
                ]
            },
        ),
    ]
    agg, unknown = aggregate_pr(details)
    assert agg[1001].qty == D("15.5") and agg[1001].pr_nums == ["PR1", "PR2"]
    assert 1003 not in agg
    assert [u["reason"] for u in unknown] == ["unknown_product"]
    headers = {101: HDR, 102: BomHeader(102, 1002, "FIN-B", "Roti B", "PCS")}
    demand, unmapped = map_pr_demand(agg, {1001: 101, 1002: 102}, headers)
    assert demand == [
        {
            "product_detail_id": 1001,
            "product_code": "FIN-A",
            "product_name": "Roti A",
            "bom_id": 101,
            "uom_name": "PCS",
            "qty": D("15.5000"),
            "pr_nums": ["PR1", "PR2"],
        }
    ]
    assert sorted((u["product_code"], u["reason"]) for u in unmapped) == [
        ("FIN-B", "uom_mismatch"),
        ("RAW-X", "no_assembly_bom"),
    ]


def test_fetch_boms_recursive_and_missing_header():
    headers = {104: BomHeader(104, 1004, "FIN-C", "C", "PCS"), 201: BomHeader(201, 2001, "WIP-D", "D", "KG")}
    reader = FakeReader(
        {
            "/product/bom/detail/104": [
                {"productDetailID": 2001, "productCode": "WIP-D", "uomName": "KG", "qty": 0.2, "stockQty": 1}
            ],
            "/product/bom/detail/201": {
                "data": [{"productDetailID": 31, "productCode": "T", "uomName": "KG", "qty": 0.5, "stockQty": 4}]
            },
        }
    )
    boms, stock, skipped, invalid = fetch_boms(reader, [104], {1004: 104, 2001: 201}, headers, 4, 7)
    assert sorted(boms) == [104, 201] and stock == {2001: D(1), 31: D(4)} and skipped == [] and invalid == []
    assert reader.calls[0] == ("/product/bom/detail/104", {"branchID": 4, "locationID": 7})
    with pytest.raises(CalcError) as exc:
        fetch_boms(reader, [999], {}, headers, 4, 7)
    assert exc.value.code == "bom_not_assembly"
    assert boms_covering([31, 2001], boms) == [104, 201]
    assert boms_covering([31], boms) == [201]


def test_job_error_codes():
    assert job_error_code(CalcError("bom_cycle")) == "bom_cycle"
    assert job_error_code(JobAbort("conflict_version")) == "conflict_version"
    assert job_error_code(CallBudgetExceeded("x")) == "call_budget_exceeded"
    assert job_error_code(EsbAuthError("x")) == "esb_auth_failed"
    assert job_error_code(EsbError("x")) == "esb_unavailable"
    assert job_error_code(ValueError("x")) == "parse_error"
    assert job_error_code(RuntimeError("x")) == "internal"


def _catalog_reader():
    def bom_list(params):
        rows = [{"bomID": 101, "bomName": "BOM Roti A", "bomCode": "B-101"}, {"bomID": 102, "bomName": "BOM Roti B"}]
        if params and params.get("bomName"):
            rows = [r for r in rows if params["bomName"].lower() in r["bomName"].lower()]
        return {"page": 1, "limit": 200, "count": len(rows), "data": rows}

    return FakeReader(
        {
            "/product/bom": bom_list,
            "/product/bom/101": {
                "bomID": 101,
                "productDetailID": 1001,
                "productCode": "FIN-A",
                "productName": "Roti A",
                "uomName": "PCS",
                "bomName": "BOM Roti A",
            },
            "/product/bom/102": {
                "bomID": 102,
                "productDetailID": 1002,
                "productCode": "FIN-B",
                "productName": "Roti B",
                "uomName": "PCS",
            },
        }
    )


def test_catalog_index_and_cache_ttl():
    now = [0.0]
    reader = _catalog_reader()
    catalog = BomCatalog(ttl_seconds=600, clock=lambda: now[0])
    by_product, headers = catalog.index("pb", reader)
    assert by_product == {1001: 101, 1002: 102} and headers[101].bom_code == "B-101"
    calls = len(reader.calls)
    catalog.index("pb", reader)
    assert len(reader.calls) == calls  # semua dari cache
    now[0] = 601
    catalog.index("pb", reader)
    assert len(reader.calls) == calls * 2


def test_catalog_search_uses_bom_name_and_header():
    reader = _catalog_reader()
    items = BomCatalog().search("pb", reader, "roti b", 10)
    assert items == [
        {
            "bom_id": 102,
            "bom_name": "BOM Roti B",
            "bom_code": "",
            "product_detail_id": 1002,
            "product_code": "FIN-B",
            "product_name": "Roti B",
            "uom_name": "PCS",
        }
    ]
    path, params = reader.calls[0]
    assert path == "/product/bom" and params["bomName"] == "roti b" and params["bomTypeID"] == 1
    assert BomCatalog().search("pb", _catalog_reader(), "roti", 1)[0]["bom_id"] == 101


def test_validate_search():
    assert validate_search(" roti ") == "roti"
    assert validate_search("Croissant (beku)") == "Croissant (beku)"
    # Putaran 2: 2..64 karakter bebas tanpa karakter kontrol (selaras insight-web).
    assert validate_search("roti;drop") == "roti;drop"
    for bad in ("a", "", None, "x" * 65, "a\nb", "\x07ab"):
        with pytest.raises(ValueError):
            validate_search(bad)
