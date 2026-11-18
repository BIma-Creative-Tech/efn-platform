"""Aturan validator §27.7 -- satu fixture sintetis per aturan (kasus negatif)."""

from typing import Any

import pytest

from efn_mig import bundle as b
from efn_mig.validate import validate_bundle
from efn_mig.xmlid import xmlid

RUN = "22222222-3333-4444-8555-666666666666"
PARENT = "99999999-3333-4444-8555-666666666666"


def rec(entity: str, key: str, data: dict[str, Any], kind: str | None = None, company: str = "PB") -> dict[str, Any]:
    return {"entity": entity, "company_code": company, "esb_key": key,
            "xmlid": xmlid("pb", company, kind or entity, key) if (kind or entity) != "cost" else None,
            "data": data}  # fmt: skip


def master(keys: b.Keys, records: dict[str, list[dict[str, Any]]], **extra: Any) -> b.Bundle:
    members, files = {}, []
    for entity, rows in records.items():
        blob, n = b.jsonl_bytes(rows)
        members[f"{entity}.jsonl"] = blob
        files.append(b.file_entry(f"{entity}.jsonl", entity, blob, n))
    man = {"run_id": RUN, "brand": "pb", "company_codes": ["PB"], "legacy_days": 400, "sales_line_months": 24,
           "files": files, "maps": [], **extra}  # fmt: skip
    return b.open_bundle(b.build_bundle(man, members, keys), keys)


def segment_bundle(keys: b.Keys, entity: str, rows: list[dict[str, Any]], **extra: Any) -> b.Bundle:
    blob = b.gzip_bytes(b.jsonl_bytes(rows)[0])
    name = f"{entity}.jsonl.gz"
    man = {"run_id": RUN, "brand": "pb", "company_codes": ["PB"], "legacy_days": 400, "sales_line_months": 24,
           "cutover_date": "2026-10-08", "parent_run_id": PARENT,
           "segment": {"company_code": "PB", "month": "2026-10", "part": 1, "parts": 1},
           "files": [b.file_entry(name, entity, blob, len(rows))], "maps": []}  # fmt: skip
    man.update(extra)
    return b.open_bundle(b.build_bundle(man, {name: blob}, keys), keys)


def codes(report: Any) -> set[str]:
    return {f.code for f in report.findings if f.level == "error"}


def line(sales_num: str = "S1", line_no: int = 1, day: str = "2026-10-01", row_type: str = "ALACARTE") -> dict:
    return {"sales_num": sales_num, "line_no": line_no, "date": day, "branch_key": "efn_mig_esb.pb_pb_branch_wh_11",
            "menu_key": None, "esb_menu_code": "M1", "qty": 1, "price": 1, "total": 1, "discount": 0, "cogs": 0,
            "row_type": row_type, "is_takeaway": False}  # fmt: skip


def product(code: str, uom: str | None = "uom.product_uom_unit", is_fin: bool = False) -> dict[str, Any]:
    return rec("product", code, {"default_code": code, "name": code, "uom_xmlid": uom, "is_fin": is_fin})


def test_pii_key_rejected(keys: b.Keys) -> None:
    bad = rec("customer", "C1", {"ref": "C1", "name": "PT A", "phone": "0812"})
    nested = rec("bom", "1", {"type": "normal", "lines": [{"product_code": "X", "creatorName": "a"}]})
    assert "PII_WHITELIST" in codes(validate_bundle(master(keys, {"customer": [bad], "bom": [nested]})))


def test_envelope_and_xmlid_recomputed(keys: b.Keys) -> None:
    row = product("RM1")
    assert "ENVELOPE" in codes(validate_bundle(master(keys, {"product": [{**row, "extra": 1}]})))
    with pytest.raises(b.IntegrityError, match="xmlid"):
        validate_bundle(master(keys, {"product": [{**row, "xmlid": "efn_mig_esb.pb_pb_product_lain"}]}))


def test_company_rules(keys: b.Keys) -> None:
    report = validate_bundle(master(keys, {"product": [product("A")]}, company_codes=["PB", "XYZ"]))
    assert "COMPANY_UNKNOWN" in codes(report)
    other = rec("product", "B", {"default_code": "B", "uom_xmlid": "uom.x"}, company="WTZ")
    assert "COMPANY_SCOPE" in codes(validate_bundle(master(keys, {"product": [other]})))


def test_branch_rules(keys: b.Keys) -> None:
    long = rec("branch", "11", {"type": "store", "warehouse_code": "TOOLONG"}, kind="branch_wh")
    dup1 = rec("branch", "12", {"type": "store", "warehouse_code": "AB"}, kind="branch_wh")
    dup2 = rec("branch", "13", {"type": "store", "warehouse_code": "AB"}, kind="branch_wh")
    loc = rec("branch", "30", {"type": "location", "parent_key": None}, kind="branch_loc")
    unmapped = [{"branch_id": 50, "active": 1, "is_store": 1}, {"branch_id": 51, "active": 0, "is_store": 1}]
    report = validate_bundle(master(keys, {"branch": [long, dup1, dup2, loc]}, unmapped_branches=unmapped))
    assert {"WAREHOUSE_CODE", "WAREHOUSE_CODE_DUP", "BRANCH_PARENT", "BRANCH_UNMAPPED"} <= codes(report)
    assert sum(1 for f in report.findings if f.code == "BRANCH_UNMAPPED") == 1  # cabang nonaktif tidak


def test_product_uom_and_menu_warnings(keys: b.Keys) -> None:
    menu = rec("menu", "M1", {"active": True, "list_price": 0, "alias_of": None, "is_takeaway": False})
    take = rec("menu", "M1T", {"active": True, "list_price": 5, "alias_of": None, "is_takeaway": True})
    report = validate_bundle(master(keys, {"product": [product("A", uom=None)], "menu": [menu, take]}))
    assert "PRODUCT_UOM" in codes(report)
    assert {"MENU_PRICE", "MENU_TAKEAWAY_BASE"} <= {f.code for f in report.findings if f.level == "warning"}


def test_bom_rules(keys: b.Keys) -> None:
    key_a, key_b = xmlid("pb", "PB", "product", "A"), xmlid("pb", "PB", "product", "B")
    comp = {"product_key": None, "product_code": "Z", "qty": 0}
    boms = [
        rec("bom", "1", {"type": "normal", "product_code": "A", "product_key": key_a, "ratio_source": "default_1",
                         "lines": [{"product_key": key_b, "product_code": "B", "qty": 1}]}),
        rec("bom", "2", {"type": "normal", "product_code": "B", "product_key": key_b, "ratio_source": "bom_ratio",
                         "lines": [{"product_key": key_a, "product_code": "A", "qty": 1}, comp]}),
        rec("bom", "3", {"type": "phantom", "menu_keys": [], "lines": []}),
    ]  # fmt: skip
    report = validate_bundle(master(keys, {"product": [product("A", is_fin=True), product("B")], "bom": boms}))
    expected = {"BOM_NO_RATIO", "BOM_COMPONENT_MISSING", "BOM_QTY", "BOM_EMPTY", "BOM_PHANTOM_MENU", "BOM_CYCLE"}
    assert expected <= codes(report)


def test_history_segment_integrity(keys: b.Keys) -> None:
    with pytest.raises(b.IntegrityError, match="segment"):
        validate_bundle(segment_bundle(keys, "legacy_sales_line", [line()], segment=None))
    with pytest.raises(b.IntegrityError, match="di luar segment.month"):
        validate_bundle(segment_bundle(keys, "legacy_sales_line", [line(day="2026-09-30")]))
    with pytest.raises(b.IntegrityError, match="company_codes"):
        validate_bundle(segment_bundle(keys, "legacy_sales_line", [line()], company_codes=["WTZ"]))
    with pytest.raises(b.IntegrityError, match="sales_line_months"):
        validate_bundle(segment_bundle(keys, "legacy_sales_line", [line()], sales_line_months=25))


def test_history_row_rules(keys: b.Keys) -> None:
    rows = [line(), line(), line("S2", row_type="COMBO"), line("S3", day="2026-10-09"), {**line("S4"), "phone": "1"}]
    report = validate_bundle(segment_bundle(keys, "legacy_sales_line", rows))
    assert {"SALES_LINE_DUP", "ROW_TYPE", "HISTORY_AFTER_CUTOVER", "HISTORY_KEYS"} <= codes(report)
    assert "MENU_UNMAPPED" in {f.code for f in report.findings if f.level == "warning"}


def test_legacy_days_bounds(keys: b.Keys) -> None:
    assert "LEGACY_DAYS" in codes(validate_bundle(master(keys, {"product": [product("A")]}, legacy_days=731)))
    clean = validate_bundle(master(keys, {"product": [product("A")]}))
    assert clean.errors == 0 and clean.to_json()["manifest_sha256"] == clean.manifest_sha256
