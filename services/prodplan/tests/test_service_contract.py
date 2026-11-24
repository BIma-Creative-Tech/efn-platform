"""Kontrak antar-lapisan di dalam prodplan: pratinjau -> body ESB, matriks transisi status, kode galat bermakna.

Menangkap beda bentuk dokumen pratinjau (`build_preview`) vs pembangun body (`Service._esb_body`) tanpa PG/ESB.
"""

import re
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from efn_prodplan.service import Service, build_preview
from efn_prodplan.workflow import MESSAGES, PLAN_STATUSES, TRANSITIONS, ApiError, transition

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)
PLAN = {"id": "p1", "branch_id": 4, "production_location_id": 7, "production_date": date(2026, 11, 25)}
MARKER = "pp:0123456789ab"


def _service(mode="actual"):
    return Service(SimpleNamespace(mfg_mode=mode), None, {}, lambda: NOW)  # type: ignore[arg-type]


def _transfer(tid, doc_type, origin):
    return {
        "id": tid,
        "doc_type": doc_type,
        "status": "approved",
        "origin_branch_id": origin[0],
        "origin_location_id": origin[1],
        "dest_branch_id": 4,
        "dest_location_id": 7,
    }


TRANSFERS = [_transfer(1, "simple_transfer", (4, 8)), _transfer(2, "goods_transfer_request", (13, 21))]
TLINES = {
    1: [{"product_detail_id": 21, "product_code": "RMT000021", "uom_name": "ML", "qty": "1000.0000"}],
    2: [{"product_detail_id": 21, "product_code": "RMT000021", "uom_name": "ML", "qty": "2.5000"}],
}
PLINES = [
    {
        "id": 11,
        "level": 2,
        "bom_id": 201,
        "product_detail_id": 2001,
        "product_code": "WIP-D",
        "qty": "2.0000",
        "materials": [
            {"product_detail_id": 31, "product_code": "RMT000031", "qty_per_unit": "0.5", "total_qty": "1.0000"},
            {"product_detail_id": 32, "product_code": "RMT000032", "qty_per_unit": "0.25", "total_qty": "0.5000"},
        ],
    }
]


def test_transfer_previews_build_exact_esb_bodies():
    svc = _service()
    docs = build_preview("transfers", PLAN, TRANSFERS, TLINES, PLINES)["docs"]
    simple = svc._esb_body(PLAN, docs[0], MARKER)
    assert simple == {
        "originLocationID": 8,
        "destinationLocationID": 7,
        "simpleTransferDate": "2026-11-25",
        "additionalInfo": MARKER,
        "simpleTransferDetails": [{"productDetailID": 21, "qty": 1000}],
    }
    gtr = svc._esb_body(PLAN, docs[1], MARKER)
    assert gtr["originBranchID"] == 13 and gtr["destinationBranchID"] == 4 and gtr["originLocationID"] == 21
    assert gtr["transferDetails"] == [{"productDetailID": 21, "qty": 2.5, "requestQty": 2.5}]
    assert "purchaseRequestNum" not in gtr and "transferDetailAssets" not in gtr


def test_production_preview_builds_actual_and_standard_bodies():
    docs = build_preview("production", PLAN, TRANSFERS, TLINES, PLINES)["docs"]
    actual = _service("actual")._esb_body(PLAN, docs[0], MARKER)
    detail = actual["simpleManufacturingDetails"][0]
    assert actual["branchID"] == 4 and actual["originLocationID"] == 7 and actual["destinationLocationID"] == 7
    assert detail["bomID"] == 201 and detail["productDetailID"] == 2001
    assert detail["manufacturingQty"] == 2 and detail["resultQty"] == 2
    assert detail["notes"] == MARKER
    assert detail["simpleManufacturingMaterials"] == [
        {"productDetailID": 31, "totalQty": 1},
        {"productDetailID": 32, "totalQty": 0.5},
    ]
    standard = _service("standard")._esb_body(PLAN, docs[0], MARKER)
    assert standard["simpleManufacturingDetails"] == [
        {"bomID": 201, "productDetailID": 2001, "qty": 2, "additionalInfo": MARKER}
    ]


def test_preview_digest_is_independent_of_input_row_order_for_transfers():
    a = build_preview("transfers", PLAN, TRANSFERS, TLINES, PLINES)["digest"]
    b = build_preview("transfers", PLAN, list(reversed(TRANSFERS)), TLINES, PLINES)["digest"]
    assert a == b


def test_preview_without_approved_transfers_has_no_docs_and_empty_impact():
    rejected = [dict(t, status="rejected") for t in TRANSFERS]
    preview = build_preview("transfers", PLAN, rejected, TLINES, PLINES)
    assert preview["docs"] == [] and preview["stock_impact"] == []


# ---------------------------------------------------------------- mesin status rencana (matriks penuh)
@pytest.mark.parametrize("action", sorted(TRANSITIONS))
@pytest.mark.parametrize("status", PLAN_STATUSES)
def test_transition_matrix(action, status):
    sources, target = TRANSITIONS[action]
    if status in sources:
        assert transition(action, status) == target
    else:
        with pytest.raises(ApiError) as exc:
            transition(action, status)
        assert (exc.value.status, exc.value.code) == (409, "invalid_state")


def test_cancelled_is_terminal_and_posted_only_leaves_via_cancel():
    for action in TRANSITIONS:
        sources, _ = TRANSITIONS[action]
        assert "cancelled" not in sources
        if "posted" in sources:
            assert action == "cancel"


# ---------------------------------------------------------------- kode galat
def test_every_error_code_raised_in_service_and_api_has_a_message():
    base = Path(__file__).resolve().parent.parent / "efn_prodplan"
    codes: set[str] = set()
    for name in ("service.py", "api.py", "workflow.py"):
        codes |= set(re.findall(r'ApiError\(\s*\d{3},\s*"([a-z_]+)"', (base / name).read_text(encoding="utf-8")))
    assert codes, "pola ApiError tidak ditemukan; perbarui regex tes"
    assert codes <= set(MESSAGES), sorted(codes - set(MESSAGES))
