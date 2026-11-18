"""Extractor migrasi G43 + `snapshot` dengan ESB & ClickHouse palsu (fixture sintetis, tanpa panggilan ESB nyata)."""

import json
import threading
from datetime import date, datetime
from typing import Any

import pytest
from test_runner import CFG, Client, FakeEsb, FakeStore, V, base_routes

import efn_esb.__main__ as cli
from efn_esb.config import BrandConfig, EsbSettings
from efn_esb.extract import BrandRuntime, run_slice
from efn_esb.extract_mig import MIG_EXTRACTORS, P1_ENTITIES, P2_ENTITIES, SNAPSHOT_ENTITIES
from efn_esb.planning import WIB, SliceTask
from efn_esb.records import raw_rows
from efn_esb.runner import ALL_TABLES, run_snapshot

DAY = date(2026, 11, 18)
BACHA = BrandConfig("bacha", "user-sintetis", "pw-sintetis", "BCF", frozenset(), frozenset(), {6: "BCR"})


def runtime(cfg: BrandConfig = CFG) -> BrandRuntime:
    rt = BrandRuntime(cfg)
    rt.branch_rows = [[cfg.key, cfg.company_for(4), 4, "CK1", "CK", 1, 0, 1, V],
                      [cfg.key, cfg.company_for(6), 6, "T06", "Toko 6", 0, 1, 1, V]]  # fmt: skip
    return rt


def test_registry_covers_all_snapshot_entities_and_tables() -> None:
    assert set(MIG_EXTRACTORS) == set(SNAPSHOT_ENTITIES) == set(P1_ENTITIES) | set(P2_ENTITIES)
    assert {"dim_esb_bom", "dim_esb_customer", "fact_esb_ap_open"} <= set(ALL_TABLES)


def test_bom_detail_fetches_active_boms_only() -> None:
    def detail(path: str, params: dict[str, Any]) -> Any:
        bom_id = int(path.rsplit("/", 1)[1])
        return {"bomID": bom_id, "productCode": f"FIN{bom_id}", "createdBy": "user.x",
                "bomDetails": [{"ID": 1, "productCode": "RM1", "qty": 1, "uomName": "KG"}]}  # fmt: skip

    esb = FakeEsb({
        "/product/bom": {"count": 3, "data": [{"bomID": 1, "flagActive": 1}, {"bomID": 2, "flagActive": 0},
                                              {"bomID": 3}]},
        "/product/bom/*": detail,
    })  # fmt: skip
    result = run_slice(runtime(), SliceTask(DAY, "bom_detail", "*"), V, esb.get)
    assert [p for p, _ in esb.calls] == ["/product/bom", "/product/bom/1", "/product/bom/3"]
    assert [r[2] for r in result.tables["dim_esb_bom"]] == [1, 3]
    assert len(result.tables["dim_esb_bom_line"]) == 2
    assert "user.x" not in json.dumps([r[4] for r in raw_rows("pb", result.raw, V)])


def test_customer_whitelisted_before_raw_and_mart() -> None:
    esb = FakeEsb({"/customer": {"count": 1, "data": [
        {"customerID": 1, "customerCode": "C1", "customerName": "PT Mitra", "address": "Jl. Contoh No. 1",
         "picName": "Nama PIC", "picPhone": "080000000000", "paymentDueDays": 30, "flagActive": 1}]}})  # fmt: skip
    result = run_slice(runtime(), SliceTask(DAY, "customer", "*"), V, esb.get)
    assert result.tables["dim_esb_customer"][0][2:6] == ["C1", "PT Mitra", "", 30]
    payload = json.dumps([r[4] for r in raw_rows("pb", result.raw, V)])
    for secret in ("Jl. Contoh", "Nama PIC", "080000000000"):
        assert secret not in payload
    assert all("Jl. Contoh" not in str(c) for c in result.tables["dim_esb_customer"][0])


def test_location_and_ap_ar_per_branch_with_company_override() -> None:
    esb = FakeEsb({
        "/location": lambda p, q: [{"locationID": q["branchID"] * 10, "locationName": "Gudang"}],
        "/supplier-payable/invoice-aging": lambda p, q: {"count": 1, "data": [
            {"purchaseInvoiceNum": f"PI{q['branchID']}", "supplierCode": "S1", "supplierName": "PT S",
             "payableTotal": 10}]},
        "/customer-receivable": lambda p, q: {"count": 0, "data": []},
    })  # fmt: skip
    rt = runtime(BACHA)
    loc = run_slice(rt, SliceTask(DAY, "location", "*"), V, esb.get)
    assert [(r[1], r[2], r[3]) for r in loc.tables["dim_esb_location"]] == [("BCF", 4, 40), ("BCR", 6, 60)]
    ap = run_slice(rt, SliceTask(DAY, "ap_open", "*"), V, esb.get)
    assert [(r[1], r[3], r[4]) for r in ap.tables["fact_esb_ap_open"]] == [("BCF", 4, "PI4"), ("BCR", 6, "PI6")]
    assert ("/supplier-payable/invoice-aging", {"branchID": 6, "payableDate": "2026-11-18", "page": 1,
                                                "limit": 100}) in esb.calls  # fmt: skip
    run_slice(rt, SliceTask(DAY, "ar_open", "*"), V, esb.get)
    ar_params = [q for p, q in esb.calls if p == "/customer-receivable"]
    assert ar_params[0]["type"] == 2 and ar_params[0]["receivableDate"] == "2026-11-18"
    assert "PT S" not in json.dumps([r[4] for r in raw_rows("bacha", ap.raw, V)])


def test_stock_opname_window_and_detail() -> None:
    esb = FakeEsb({
        "/inventory/stock-opname": {"count": 1, "data": [{"stockOpnameNum": "SP/1", "branchID": 6,
                                                          "createdBy": "petugas"}]},
        "/inventory/stock-opname/*": {"stockOpnameNum": "SP/1", "branchID": 6, "statusID": 3,
                                      "stockOpnameDetails": [{"productCode": "RM1", "qty": 2, "uomName": "KG"}]},
    })  # fmt: skip
    rt = runtime(BACHA)
    rt.opname_window_days = 2
    result = run_slice(rt, SliceTask(DAY, "stock_opname", "*"), V, esb.get)
    assert esb.calls[0] == ("/inventory/stock-opname", {"dateFrom": "2026-11-16", "dateTo": "2026-11-18", "page": 1,
                                                        "limit": 100})  # fmt: skip
    assert esb.calls[1][0] == "/inventory/stock-opname/SP%2F1"
    assert result.tables["fact_esb_stock_opname"][0][1] == "BCR"


def test_snapshot_args_p2_default_off() -> None:
    cli.validate_snapshot_args(["bom_detail", "customer"], False, 3)
    with pytest.raises(ValueError, match="belum diverifikasi"):
        cli.validate_snapshot_args(["ap_open"], False, 3)
    cli.validate_snapshot_args(["ap_open"], True, 3)
    with pytest.raises(ValueError):
        cli.validate_snapshot_args(["coa"], True, 40)


def test_run_snapshot_writes_checkpoint_per_entity() -> None:
    settings = EsbSettings(enabled=True, brands=(CFG,), backfill_max_calls=777)
    routes = {**base_routes(), "/menu/browse-for-product": {"count": 1, "data": [{"menuCode": "M1", "price": 5}]}}
    esb = FakeEsb(routes)
    store = FakeStore({("master", "*", DAY): V})
    client = Client(esb)
    run = run_snapshot(
        settings, BrandRuntime(CFG), client, store, ["menu_price", "location"], DAY, threading.Event(),
        now_fn=lambda: datetime(2026, 11, 18, 9, 0, tzinfo=WIB),
    )  # fmt: skip
    assert run.status == "ok" and run.mode == "snapshot" and client.budget == 777
    checkpoints = [rows[0] for kind, table, rows in store.writes if table == "extract_checkpoint"]
    assert (checkpoints[0][1], checkpoints[0][3]) == ("branch", DAY)
    assert {(c[1], c[2], c[3]) for c in checkpoints[1:]} == {("menu_price", "*", DAY), ("location", "*", DAY)}
    assert [p for p, _ in esb.calls if p == "/product"] == []  # master sudah done hari ini
    assert "dim_esb_menu_price" in store.tables() and store.tables()[-1] == "extract_run"
