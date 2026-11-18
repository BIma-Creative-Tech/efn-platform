"""Pemetaan baris ClickHouse sintetis -> JSONL (§27.3/§27.4/§27.8)."""

from datetime import date
from decimal import Decimal

import pytest
from conftest import BRANCH_MAP, FakeSource

from efn_mig import mapping as m
from efn_mig.export import Maps
from efn_mig.xmlid import xmlid


def test_branch_map_parse_and_errors() -> None:
    parsed = m.parse_branch_map(BRANCH_MAP, "pb")
    assert set(parsed) == {4, 11, 12, 30, 99} and parsed[30].parent_branch_id == 4
    header = "brand,branch_id,type,warehouse_code,display_name,parent_branch_id\n"
    for bad in ("pb,x,store,A,,\n", "pb,1,toko,A,,\n", "pb,1,location,,,\n", "pb,1,store,A,,\npb,1,store,B,,\n"):
        with pytest.raises(m.MapError):
            m.parse_branch_map(header + bad, "pb")
    with pytest.raises(m.MapError):
        m.parse_branch_map("brand,branch_id\npb,1\n", "pb")


def test_uom_map_parse_and_errors() -> None:
    header = "esb_uom,uom_xmlid,ea_factor\n"
    parsed = m.parse_uom_map(header + " pack ,uom.product_uom_unit,6\n")
    assert parsed["PACK"].ea_factor == Decimal(6)
    for bad in ("EA,unit,1\n", "EA,uom.product_uom_unit,0\n", "EA,uom.product_uom_unit,x\n", ",uom.a_b,1\n"):
        with pytest.raises(m.MapError):
            m.parse_uom_map(header + bad)


def test_build_branches_types_and_index(maps: Maps, source: FakeSource) -> None:
    records, index = m.build_branches("pb", source.dims["dim_esb_branch"], maps.branch)
    by_key = {r["esb_key"]: r for r in records}
    assert set(by_key) == {"4", "11", "12", "30"}  # 99 = skip
    assert by_key["4"]["data"]["is_central_kitchen"] is True and by_key["4"]["data"]["pos_xmlid"] is None
    assert by_key["11"]["data"]["pos_xmlid"] == xmlid("pb", "PB", "branch_pos", 11)
    assert by_key["30"]["xmlid"] == xmlid("pb", "PB", "branch_loc", 30)
    assert by_key["30"]["data"]["parent_key"] == xmlid("pb", "PB", "branch_wh", 4)
    assert index.wh_key[30] == index.wh_key[4]  # histori lokasi -> gudang induk
    assert index.unmapped == []
    extra = {"branch_id": 50, "company_code": "PB", "active": 1, "is_store": 1}
    _, unmapped = m.build_branches("pb", [*source.dims["dim_esb_branch"], extra], maps.branch)
    assert unmapped.unmapped == [{"branch_id": 50, "active": 1, "is_store": 1}]


def test_products_uom_fallback_from_bom(maps: Maps, source: FakeSource) -> None:
    d = source.dims
    uoms = m.product_uoms(d["dim_esb_product"], d["dim_esb_bom"], d["dim_esb_bom_line"])
    assert uoms == {"FIN-001": "EA", "RM001": "KG"}
    rows = {r["esb_key"]: r for r in m.build_products("pb", "PB", d["dim_esb_product"], uoms, maps.uom)}
    fin = rows["FIN-001"]["data"]
    assert fin["uom_xmlid"] == "uom.product_uom_unit" and fin["is_fin"] and not fin["purchase_ok"]
    assert rows["FIN-001"]["xmlid"].startswith("efn_mig_esb.pb_pb_product_fin_001_")
    assert rows["RM001"]["data"]["categ_key"] == xmlid("pb", "PB", "category", "Bahan")


def test_menus_takeaway_merge_and_archive(source: FakeSource) -> None:
    d = source.dims
    archive = source.archive_menus("pb", date(2024, 1, 1), date(2026, 1, 1))
    rows, index = m.build_menus("pb", "PB", d["dim_esb_menu"], d["dim_esb_menu_price"], archive)
    by = {r["esb_key"]: r["data"] for r in rows}
    base = xmlid("pb", "PB", "menu", "M01")
    assert by["M01T"]["alias_of"] == base and by["M01T"]["is_takeaway"]
    assert index.key["M01T"] == base and index.key["M01"] == base
    assert by["M01"]["list_price"] == 25000 and by["M01"]["active"]
    assert by["OLD9"]["active"] is False and by["OLD9"]["available_in_pos"] is False  # menu arsip
    assert index.by_bom_name["BOM MENU ROTI"] == [base]


def test_partners_and_payment_terms(source: FakeSource) -> None:
    d = source.dims
    out = m.build_partners("pb", "PB", d["dim_esb_supplier"], d["dim_esb_customer"])
    customer = out["customer"][0]["data"]
    assert customer["payment_term_key"] == xmlid("pb", "PB", "payment_term", "net_30")
    assert out["payment_term"][0]["data"] == {"days": 30, "name": "30 Hari"}
    assert set(customer) <= m.DATA_KEYS["customer"]


def test_boms_phantom_normal_and_ratio(maps: Maps, source: FakeSource) -> None:
    d = source.dims
    _, index = m.build_menus("pb", "PB", d["dim_esb_menu"], d["dim_esb_menu_price"], [])
    codes = {"FIN-001", "RM001"}
    rows = m.build_boms("pb", "PB", d["dim_esb_bom"], d["dim_esb_bom_line"], codes, index, {"FIN-001": 12.0}, maps.uom)
    by = {r["esb_key"]: r["data"] for r in rows}
    assert by["7"]["type"] == "normal" and by["7"]["product_qty"] == 12 and by["7"]["ratio_source"] == "bom_ratio"
    assert by["7"]["lines"][0]["uom_xmlid"] == "uom.product_uom_kgm" and by["7"]["lines"][0]["qty"] == 2.5
    assert by["8"]["type"] == "phantom" and by["8"]["menu_keys"] == [xmlid("pb", "PB", "menu", "M01")]
    costs = m.build_costs("pb", "PB", d["dim_esb_bom_line"], codes)
    assert {c["esb_key"]: c["data"]["standard_price"] for c in costs} == {"FIN-001": 3500, "RM001": 12000}


def ctx(maps: Maps, source: FakeSource) -> m.HistoryContext:
    d = source.dims
    _, index = m.build_branches("pb", d["dim_esb_branch"], maps.branch)
    _, menus = m.build_menus("pb", "PB", d["dim_esb_menu"], d["dim_esb_menu_price"], [])
    return m.HistoryContext("pb", "PB", index, {"FIN-001", "PACK1"}, {"PACK1": Decimal(6)}, menus)


def test_flow_row_converts_ea_and_derives_outflow(maps: Maps, source: FakeSource) -> None:
    c = ctx(maps, source)
    row = {"day": date(2026, 10, 1), "branch_id": 11, "product_code": "PACK1", "sold": Decimal(12),
           "ck_prod": 0, "store_prod": 0, "ck_out": Decimal(6), "in_ck": 0, "in_other": 0, "st_in": 0,
           "st_out": Decimal(3)}  # fmt: skip
    out = m.flow_row(c, row)
    assert out is not None and out["sold"] == 2 and out["ck_out"] == 1 and out["st_out"] == 0.5
    assert out["outflow"] == 3.5 and set(out) == set(m.HISTORY_KEYS["legacy_flow_daily"])
    assert m.flow_row(c, {**row, "product_code": "NAME:X"}) is None
    assert m.flow_row(c, {**row, "branch_id": 77}) is None


def test_sales_rows(maps: Maps, source: FakeSource) -> None:
    c = ctx(maps, source)
    daily = m.sales_daily_row(c, source.history["legacy_sales_daily"][0])
    assert daily is not None and daily["net"] == 70000 and daily["menu_key"] == xmlid("pb", "PB", "menu", "M01")
    assert daily["pos_key"] == xmlid("pb", "PB", "branch_pos", 11)
    assert set(daily) == set(m.HISTORY_KEYS["legacy_sales_daily"])
    line = m.sales_line_row(c, source.history["legacy_sales_line"][1])  # menu OLD9 tak terpetakan (tanpa arsip)
    assert line is not None and line["menu_key"] is None and line["esb_menu_code"] == "OLD9"
    assert set(line) == set(m.HISTORY_KEYS["legacy_sales_line"])
    assert m.sales_line_row(c, {**source.history["legacy_sales_line"][0], "branch_id": 99}) is None


def test_num_rejects_non_finite() -> None:
    assert m.num(Decimal("2.000")) == 2 and m.num(Decimal("2.5")) == 2.5 and m.num(None) == 0
    with pytest.raises(ValueError):
        m.num(Decimal("NaN"))
