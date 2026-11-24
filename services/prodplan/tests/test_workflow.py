"""Mesin status rencana, SoD, gerbang posting, pratinjau + digest (docs/04 §30.7–§30.8)."""

from datetime import UTC, date, datetime, timedelta

import pytest

from efn_prodplan.actor import Actor
from efn_prodplan.service import build_preview, req_decimal, req_int, req_str, to_json
from efn_prodplan.workflow import (
    ApiError,
    check_date_window,
    check_sod,
    check_stock_age,
    check_version,
    plan_no,
    preview_digest,
    require_brand,
    require_role,
    transition,
)

NOW = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)  # 10:00 WIB


def actor(sub=7, roles=("pp_planner",), brand="pb"):
    return Actor(sub=sub, name="Uji", roles=frozenset(roles), brand=brand, branch_ids=frozenset({4}))


def code_of(fn, *args):
    with pytest.raises(ApiError) as exc:
        fn(*args)
    return exc.value.status, exc.value.code


@pytest.mark.parametrize(
    ("action", "src", "dst"),
    [
        ("submit", "draft", "submitted"),
        ("approve", "submitted", "approved"),
        ("return_to_draft", "submitted", "draft"),
        ("post_production", "approved", "posted"),
        ("cancel", "draft", "cancelled"),
        ("cancel", "posted", "cancelled"),
    ],
)
def test_valid_transitions(action, src, dst):
    assert transition(action, src) == dst


@pytest.mark.parametrize(
    ("action", "src"),
    [
        ("submit", "submitted"),
        ("submit", "approved"),
        ("approve", "draft"),
        ("approve", "approved"),
        ("post_production", "submitted"),
        ("post_production", "posted"),
        ("return_to_draft", "approved"),
        ("cancel", "cancelled"),
    ],
)
def test_invalid_transitions(action, src):
    assert code_of(transition, action, src) == (409, "invalid_state")


def test_sod_creator_or_submitter_cannot_approve():
    assert code_of(check_sod, 7, 7, 8) == (403, "sod_violation")
    assert code_of(check_sod, 8, 7, 8) == (403, "sod_violation")
    check_sod(9, 7, 8)
    check_sod(9, 7, None)


def test_roles_and_brand():
    require_role(actor(), {"pp_planner"})
    assert code_of(require_role, actor(roles=("pp_warehouse",)), {"pp_planner"}) == (403, "forbidden")
    require_brand(actor(), "pb", ["pb"])
    assert code_of(require_brand, actor(brand="wtz"), "pb", ["pb"]) == (403, "forbidden")  # brand sesi lain
    assert code_of(require_brand, actor(brand="wtz"), "wtz", ["pb"]) == (403, "forbidden")  # brand tidak aktif
    require_brand(actor(brand="bacha"), "bacha", ["bacha"])


def test_check_version():
    check_version(3, 3)
    with pytest.raises(ApiError) as exc:
        check_version(3, 2)
    assert exc.value.code == "conflict_version" and exc.value.details == {"row_version": 3}
    for bad in (None, "3", True, 3.0):
        with pytest.raises(ApiError):
            check_version(3, bad)


def test_date_window_uses_wib():
    check_date_window(date(2026, 11, 23), NOW, (-1, 7))
    check_date_window(date(2026, 12, 1), NOW, (-1, 7))
    assert code_of(check_date_window, date(2026, 11, 22), NOW, (-1, 7)) == (409, "date_out_of_window")
    assert code_of(check_date_window, date(2026, 12, 2), NOW, (-1, 7)) == (409, "date_out_of_window")
    late_utc = datetime(2026, 11, 24, 18, 0, tzinfo=UTC)  # 25 Nov 01:00 WIB -> jendela mulai 24 Nov
    assert code_of(check_date_window, date(2026, 11, 23), late_utc, (-1, 7)) == (409, "date_out_of_window")


def test_stock_age():
    check_stock_age(NOW - timedelta(minutes=29), NOW, 30)
    assert code_of(check_stock_age, NOW - timedelta(minutes=31), NOW, 30) == (409, "stock_too_old")
    assert code_of(check_stock_age, None, NOW, 30) == (409, "stock_too_old")


def test_error_body_has_no_internal_detail():
    body = ApiError(500, "internal").body()
    assert body == {"error": {"code": "internal", "message": "Terjadi kesalahan internal"}}
    assert ApiError(422, "bom_cycle", {"path": ["A", "B", "A"]}).body()["error"]["details"] == {"path": ["A", "B", "A"]}


def test_plan_no():
    assert plan_no("PB", date(2026, 11, 25), 3) == "PP-PB-20261125-003"


# ---------------------------------------------------------------- pratinjau posting
PLAN = {"id": "p1", "branch_id": 4, "production_location_id": 7}
TRANSFERS = [
    {
        "id": 2,
        "doc_type": "goods_transfer_request",
        "status": "approved",
        "origin_branch_id": 13,
        "origin_location_id": 21,
        "dest_branch_id": 4,
        "dest_location_id": 7,
    },
    {
        "id": 1,
        "doc_type": "simple_transfer",
        "status": "approved",
        "origin_branch_id": 4,
        "origin_location_id": 8,
        "dest_branch_id": 4,
        "dest_location_id": 7,
    },
    {
        "id": 3,
        "doc_type": "simple_transfer",
        "status": "rejected",
        "origin_branch_id": 4,
        "origin_location_id": 9,
        "dest_branch_id": 4,
        "dest_location_id": 7,
    },
]
TLINES = {
    1: [{"product_detail_id": 21, "product_code": "RMT000021", "uom_name": "ML", "qty": "1000.0000"}],
    2: [{"product_detail_id": 21, "product_code": "RMT000021", "uom_name": "ML", "qty": "1000.0000"}],
    3: [{"product_detail_id": 21, "product_code": "RMT000021", "uom_name": "ML", "qty": "5.0000"}],
}


def _mat(pid, code, per_unit, total):
    return {"product_detail_id": pid, "product_code": code, "qty_per_unit": per_unit, "total_qty": total}


PLINES = [
    {
        "id": 10,
        "level": 1,
        "bom_id": 104,
        "product_detail_id": 1004,
        "product_code": "FIN-C",
        "qty": "10.0000",
        "materials": [_mat(2001, "WIP-D", "0.2", "2.0000")],
    },
    {
        "id": 11,
        "level": 2,
        "bom_id": 201,
        "product_detail_id": 2001,
        "product_code": "WIP-D",
        "qty": "2.0000",
        "materials": [_mat(31, "RMT000031", "0.5", "1.0000")],
    },
]


def test_preview_transfers_only_approved_and_impact():
    preview = build_preview("transfers", PLAN, TRANSFERS, TLINES, PLINES)
    assert [d["transfer_id"] for d in preview["docs"]] == [1, 2]
    assert preview["stock_impact"] == [
        {"location_id": 7, "product_code": "RMT000021", "qty_out": "0.0000", "qty_in": "2000.0000"},
        {"location_id": 8, "product_code": "RMT000021", "qty_out": "1000.0000", "qty_in": "0.0000"},
        {"location_id": 21, "product_code": "RMT000021", "qty_out": "1000.0000", "qty_in": "0.0000"},
    ]
    assert preview["digest"] == preview_digest(preview["docs"])
    assert len(preview["digest"]) == 64


def test_preview_production_order_wip_first_and_digest_changes():
    preview = build_preview("production", PLAN, TRANSFERS, TLINES, PLINES)
    assert [d["product_code"] for d in preview["docs"]] == ["WIP-D", "FIN-C"]
    assert preview["docs"][1]["lines"] == [{"product_detail_id": 2001, "product_code": "WIP-D", "qty": "2.0000"}]
    changed = [dict(PLINES[0], qty="11.0000"), PLINES[1]]
    assert build_preview("production", PLAN, TRANSFERS, TLINES, changed)["digest"] != preview["digest"]
    assert build_preview("production", PLAN, TRANSFERS, TLINES, list(reversed(PLINES)))["digest"] == preview["digest"]
    with pytest.raises(ApiError):
        build_preview("all", PLAN, TRANSFERS, TLINES, PLINES)


def test_body_validators():
    assert req_int({"a": 5}, "a") == 5
    assert req_int({}, "a", optional=True) is None
    for body in ({"a": True}, {"a": "5"}, {"a": 0}, {}):
        with pytest.raises(ApiError):
            req_int(body, "a")
    assert str(req_decimal({"q": "12.34567"}, "q")) == "12.3457"
    for bad in ({"q": 0}, {"q": -1}, {"q": "x"}, {"q": "NaN"}, {"q": True}, {"q": "1e12"}):
        with pytest.raises(ApiError):
            req_decimal(bad, "q")
    assert req_str({"s": " ab "}, "s", 5) == "ab"
    with pytest.raises(ApiError):
        req_str({"s": "abcdef"}, "s", 5)


def test_to_json():
    from decimal import Decimal

    assert to_json({"a": Decimal("1.5"), "d": date(2026, 1, 2), "l": [Decimal("2")]}) == {
        "a": "1.5000",
        "d": "2026-01-02",
        "l": ["2.0000"],
    }
