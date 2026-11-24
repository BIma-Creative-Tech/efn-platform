"""Pemetaan izin ESB -> peran + akses dokumen + cabang (docs/04 §30.17.6). Fixture = bentuk respons staging yang
disamarkan (tanpa username/nama/perusahaan nyata)."""

import pytest

from efn_prodplan.access import (
    DEFAULT_DOC_ACCESS,
    DEFAULT_PLANNER_RULES,
    DEFAULT_WAREHOUSE_RULES,
    access_items,
    compute_roles,
    doc_access,
    parse_branch_page,
    parse_doc_access,
    parse_rules,
)
from efn_prodplan.config import ConfigError


def detail(code, name, *, view=True, insert=False, update=False, delete=False, authorize=False):
    return {
        "accessID": code,
        "accessName": name,
        "indexAccess": True,
        "viewAccess": view,
        "insertAccess": insert,
        "updateAccess": update,
        "deleteAccess": delete,
        "authorizeAccess": authorize,
        "releaseAccess": False,
        "orderID": 1,
        "node": "x",
    }


# Bentuk `GET /user/access` (uji staging lead 25 Nov 2026): list grup, kode di accessDetails[].accessID.
ADMIN_ACCESS = [
    {
        "accessID": "B",
        "accessName": "Production",
        "accessDetails": [
            detail("B.1", "Assembly", insert=True, delete=True, authorize=True),
            detail("B.4", "Simple Manufacturing", insert=True, update=True, delete=True, authorize=True),
            detail("B.5", "Production Result"),
        ],
    },
    {"accessID": "C", "accessName": "Purchase", "accessDetails": [detail("C.1", "Purchase Request", insert=True)]},
    {
        "accessID": "D",
        "accessName": "Inventory",
        "accessDetails": [
            detail("D.4", "Goods Transfer Request", insert=True, delete=True),
            detail("D.9", "Simple Transfer", insert=True, delete=True),
            detail("D.16", "Stock List"),
        ],
    },
    {"accessID": "K", "accessName": "Master", "accessDetails": [detail("K.5", "User BOM")]},
]

PLANNER = parse_rules(DEFAULT_PLANNER_RULES, "P")
WAREHOUSE = parse_rules(DEFAULT_WAREHOUSE_RULES, "W")
DOCS = parse_doc_access(DEFAULT_DOC_ACCESS)


def test_admin_fixture_gets_both_roles_and_doc_access():
    items = access_items(ADMIN_ACCESS)
    assert len(items) == 8
    assert compute_roles(items, PLANNER, WAREHOUSE) == frozenset({"pp_planner", "pp_warehouse"})
    insert, delete = doc_access(items, DOCS)
    assert insert == frozenset({"simple_transfer", "goods_transfer_request", "simple_manufacturing"})
    assert delete == insert


def test_warehouse_only_and_no_access():
    wh = [
        {
            "accessID": "D",
            "accessDetails": [
                detail("D.9", "Simple Transfer", insert=True),
                detail("D.4", "Goods Transfer Request", insert=True),
                detail("D.16", "Stock List"),
            ],
        }
    ]
    items = access_items(wh)
    assert compute_roles(items, PLANNER, WAREHOUSE) == frozenset({"pp_warehouse"})
    assert doc_access(items, DOCS) == (frozenset({"simple_transfer", "goods_transfer_request"}), frozenset())
    assert compute_roles(access_items([]), PLANNER, WAREHOUSE) == frozenset()
    assert compute_roles(access_items(None), PLANNER, WAREHOUSE) == frozenset()


def test_rights_must_be_boolean_true_and_code_exact():
    loose = [{"accessDetails": [{**detail("B.4", "x"), "insertAccess": 1}, detail(" K.5 ", "y")]}]
    items = access_items(loose)
    assert compute_roles(items, parse_rules("B.4:insert", "P"), WAREHOUSE) == frozenset()
    assert compute_roles(items, parse_rules("K.5:view", "P"), WAREHOUSE) == frozenset({"pp_planner"})  # trim
    assert compute_roles(items, parse_rules("B.40:view", "P"), WAREHOUSE) == frozenset()


def test_authorize_never_counts():
    only_authorize = [{"accessDetails": [detail("B.4", "x", view=False, authorize=True)]}]
    assert compute_roles(access_items(only_authorize), parse_rules("B.4:view", "P"), WAREHOUSE) == frozenset()


def test_flat_detail_list_is_tolerated():
    flat = [detail("B.4", "x", insert=True), detail("K.5", "y"), detail("D.16", "z"), detail("C.1", "w")]
    assert compute_roles(access_items(flat), PLANNER, WAREHOUSE) == frozenset({"pp_planner"})


@pytest.mark.parametrize(
    "text",
    ["", ";", "B.4", "B.4:", "B.4:authorize", "B.4:view+authorize", "B.4:read", "B 4:view", "B.4:view++insert"],
)
def test_invalid_rules_raise_config_error(text):
    with pytest.raises(ConfigError) as exc:
        parse_rules(text, "PRODPLAN_ESB_ROLE_PLANNER")
    assert exc.value.reason == "esb_role_rule"


def test_parse_rules_and_doc_access_valid():
    assert parse_rules(" B.4 : view+insert ; K.5:view ", "P") == (
        ("B.4", frozenset({"view", "insert"})),
        ("K.5", frozenset({"view"})),
    )
    assert DOCS == {"simple_transfer": "D.9", "goods_transfer_request": "D.4", "simple_manufacturing": "B.4"}
    for bad in ("simple_transfer=D.9", "x=D.9;" + DEFAULT_DOC_ACCESS, DEFAULT_DOC_ACCESS + ";simple_transfer=D.1"):
        with pytest.raises(ConfigError):
            parse_doc_access(bad)


def test_parse_branch_page_staging_shape():
    page = {
        "page": 1,
        "pageSize": 100,
        "dataCount": 3,
        "data": [
            {"branchID": 4, "branchCode": "CK01", "branchName": "CK Rekaan"},
            {"branchID": "13", "branchCode": "WH01", "branchName": ""},
            {"branchID": 0, "branchName": "invalid"},
            {"branchID": True, "branchName": "invalid"},
        ],
    }
    assert parse_branch_page(page) == ({4: "CK Rekaan", 13: "WH01"}, 3)
    assert parse_branch_page({"data": None}) == ({}, None)
    assert parse_branch_page("x") == ({}, None)
