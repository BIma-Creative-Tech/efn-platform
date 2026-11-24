"""G65 (docs/04 §27.13.4-§27.13.5): supplierinfo, cost berurutan sumber, stok awal (quant), histori pembelian.

Fixture sintetis saja (bukan data klien).
"""

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from conftest import BRANCH_MAP, UOM_MAP, FakeSource, opening, product, purchase

from efn_mig import bundle as b
from efn_mig import mapping as m
from efn_mig.export import (
    G65Inputs,
    G65Result,
    GateError,
    Maps,
    MasterData,
    RunParams,
    StepError,
    StockGateError,
    expand_steps,
    export_history,
    export_master,
    master_records,
)
from efn_mig.reconcile import aggregate_bundle, reconcile, reconcile_final
from efn_mig.validate import validate_bundle
from efn_mig.xmlid import xmlid

CUTOVER = date(2026, 12, 1)
NOW = datetime(2026, 12, 1, 3, 0, tzinfo=UTC)
WH = {n: xmlid("pb", "PB", "branch_wh", n) for n in (4, 11, 12)}
LOC30 = xmlid("pb", "PB", "branch_loc", 30)
LOCATION_MAP = "brand,branch_id,esb_location_name,action\npb,11,Damage,skip\nwtz,11,Damage,quarantine\n"


def params(**kw: Any) -> RunParams:
    base: dict[str, Any] = {"brand": "pb", "cutover": CUTOVER, "legacy_days": 400, "sales_line_months": 24,
                            "segment_max_rows": 15}  # fmt: skip
    base.update(kw)
    return RunParams(**base)


def maps_with(location: str | None = LOCATION_MAP) -> Maps:
    raw = {"branch_map.csv": BRANCH_MAP.encode(), "uom_map.csv": UOM_MAP.encode()}
    if location is not None:
        raw["location_map.csv"] = location.encode()
    return Maps.from_bytes(raw, "pb")


def master_data(source: FakeSource) -> MasterData:
    return MasterData.load(source, "pb", date(2024, 12, 1), CUTOVER)


def codes(report: Any, level: str = "error") -> set[str]:
    return {f.code for f in report.findings if f.level == level}


# ================================================================== supplierinfo
def purchases_fixture(source: FakeSource) -> None:
    source.dims["dim_esb_supplier"].append({"supplier_code": "SUP2", "supplier_name": "PT Dua", "active": 1})
    source.purchases = [
        purchase(date(2026, 10, 1), "PO1", price="12000"),
        purchase(date(2026, 11, 15), "PO2", line_no=1, price="99999"),
        purchase(date(2026, 11, 15), "PO2", line_no=2, price="13000", discount="13000", status="Receiving"),
        purchase(date(2026, 11, 20), "PO3", supplier="SUP2", price="12500", status="Draft"),  # status ditolak
        purchase(date(2026, 11, 10), "PO4", supplier="SUP2", price="12800", status="Closed"),
        purchase(date(2025, 11, 30), "PO0", supplier="SUP2", price="1"),  # di luar jendela 12 bulan
        purchase(date(2026, 11, 21), "PO6", currency="USD"),  # mata uang -> skipped.currency
        purchase(date(2026, 11, 22), "PO7", supplier="SUPX"),  # supplier tanpa master
        purchase(date(2026, 11, 23), "PO8", code="FIN-001", uom="BOX"),  # satuan beli tanpa uom_map
        purchase(date(2026, 11, 25), "PO9", map_source="none"),  # tidak terpetakan -> bukan harga
        purchase(date(2026, 11, 26), "PO10", price="0"),  # harga 0 -> bukan harga
    ]


def test_supplierinfo_latest_sequence_discount_and_skips(source: FakeSource) -> None:
    purchases_fixture(source)
    skips = m.PurchaseSkips()
    latest = m.latest_purchases(source.purchases, m.PURCHASE_PRICE_STATUSES, date(2025, 12, 1), CUTOVER, skips)
    rows = m.build_supplierinfos("pb", "PB", latest, {"SUP1", "SUP2"}, {"RM001", "FIN-001"},
                                 maps_with().uom, "nominal", skips)  # fmt: skip
    by = {r["esb_key"]: r for r in rows}
    assert set(by) == {"SUP1|RM001", "SUP2|RM001"}
    sup1 = by["SUP1|RM001"]
    # QA P3: xmlid dari esb_key `SUP1|RM001` (hash kode utuh) -- `A_B`+`C` tidak lagi bertabrakan dengan `A`+`B_C`.
    assert sup1["xmlid"] == m.supplierinfo_xmlid("pb", "PB", "SUP1", "RM001") == xmlid("pb", "PB", "supplierinfo",
                                                                                        "SUP1|RM001")  # fmt: skip
    assert m.supplierinfo_xmlid("pb", "PB", "A_B", "C") != m.supplierinfo_xmlid("pb", "PB", "A", "B_C")
    assert sup1["data"]["price"] == 13000 and sup1["data"]["discount"] == 10  # 13000 / (13000 × 10) × 100
    assert sup1["data"]["sequence"] == 1 and sup1["data"]["date_start"] == "2026-11-15"
    assert sup1["data"]["source_purchase_num"] == "PO2" and sup1["data"]["uom_xmlid"] == "uom.product_uom_kgm"
    assert sup1["data"]["partner_key"] == xmlid("pb", "PB", "supplier", "SUP1")
    assert set(sup1["data"]) == m.DATA_KEYS["supplierinfo"]
    assert by["SUP2|RM001"]["data"]["price"] == 12800 and by["SUP2|RM001"]["data"]["sequence"] == 2
    assert skips.currency == 1
    assert skips.unmapped == {("PB", "SUPX", "RM001"): "supplier", ("PB", "SUP1", "FIN-001"): "uom"}
    manifest = skips.to_manifest()
    assert manifest["supplierinfo_unmapped"]["count"] == 2 and manifest["currency"] == {"count": 1}


def test_supplier_discount_modes() -> None:
    row = {"price": D("1000"), "qty": D("4"), "discount": D("400")}
    assert m.supplier_discount(row, "nominal") == D("10.00")
    assert m.supplier_discount({**row, "discount": D("9999")}, "nominal") == 100  # dibatasi 0..100
    assert m.supplier_discount({**row, "discount": D("-5")}, "nominal") == 0
    assert m.supplier_discount({**row, "discount": D("12.5")}, "percent") == D("12.5")
    with pytest.raises(m.MapError):
        m.supplier_discount(row, "lain")
    with pytest.raises(StepError):
        params(purchase_discount_mode="lain")
    with pytest.raises(StepError):
        params(purchase_statuses=())


def test_supplierinfo_price_converted_to_product_uom_same_xmlid() -> None:
    """QA G65: PACK (=6 EA) dan EA sama-sama Unit -> harga per PACK harus dikonversi ke per EA (bukan apa adanya)."""
    umap = maps_with().uom
    rows = [
        purchase(date(2026, 11, 1), "PO1", code="PCK1", price="600", qty="10", uom="PACK"),
        purchase(date(2026, 11, 2), "PO2", code="KGP", price="9000", qty="1", uom="KG"),
    ]
    latest = m.latest_purchases(rows, m.PURCHASE_PRICE_STATUSES, date(2025, 12, 1), CUTOVER)
    out = m.build_supplierinfos("pb", "PB", latest, {"SUP1"}, {"PCK1", "KGP"}, umap, "nominal", None,
                                {"PCK1": "EA", "KGP": "KG"})  # fmt: skip
    by = {r["esb_key"]: r["data"] for r in out}
    assert by["SUP1|PCK1"]["price"] == 100 and by["SUP1|PCK1"]["uom_esb"] == "EA"
    assert by["SUP1|PCK1"]["uom_xmlid"] == "uom.product_uom_unit"
    assert by["SUP1|KGP"]["price"] == 9000 and by["SUP1|KGP"]["uom_esb"] == "KG"  # satuan sama: apa adanya
    # Tanpa product_uoms (perilaku lama): apa adanya per satuan beli.
    raw = m.build_supplierinfos("pb", "PB", latest, {"SUP1"}, {"PCK1"}, umap, "nominal")
    assert raw[0]["data"]["price"] == 600 and raw[0]["data"]["uom_esb"] == "PACK"


def test_year_before_leap_day() -> None:
    assert m.year_before(date(2028, 2, 29)) == date(2027, 2, 28)
    assert m.year_before(CUTOVER) == date(2025, 12, 1)


# ================================================================== cost
def test_cost_source_order_and_as_of(source: FakeSource) -> None:
    purchases_fixture(source)
    source.dims["dim_esb_product"].append(product("PKG1", "Kemasan", "Packaging", "PACK", 0, None))
    source.purchases.append(purchase(date(2026, 11, 1), "PO20", code="PKG1", price="600", uom="PACK"))
    source.opening = [
        opening(11, "RM001", "10", "125000"),
        opening(12, "RM001", "5", "60000"),
        opening(12, "RM001", "-2", "-20000"),  # qty <= 0 tidak dihitung
    ]
    inputs = G65Inputs.load(source, "pb", ["cost"], CUTOVER)
    rows, _ = _master(source, ["cost"], inputs)
    by = {r["esb_key"]: r["data"] for r in rows["cost"]}
    assert by["RM001"]["source"] == "stock_opening" and by["RM001"]["standard_price"] == 12333.3333
    assert by["RM001"]["as_of"] == "2026-12-01" and by["RM001"]["source_uom"] == "KG"
    assert by["FIN-001"]["source"] == "bom_last_hpp" and by["FIN-001"]["standard_price"] == 3500
    assert by["PKG1"] == {**by["PKG1"], "source": "purchase_last", "standard_price": 600, "source_uom": "PACK"}
    # Tanpa cutover: tidak ada sumber stock_opening; as_of = tanggal export (WIB).
    plain = G65Inputs.load(source, "pb", ["cost"], None, NOW)
    rows, _ = _master(source, ["cost"], plain, cutover=None)
    by = {r["esb_key"]: r["data"] for r in rows["cost"]}
    assert by["RM001"]["source"] == "bom_last_hpp" and by["RM001"]["as_of"] == "2026-12-01"


def test_opening_cost_mixed_uom_falls_through() -> None:
    mixed: set[str] = set()
    rows = [opening(11, "RM001", "1", "100", uom="KG"), opening(12, "RM001", "1000", "100", uom="G")]
    assert m.opening_costs(rows, {"RM001"}, mixed) == {} and mixed == {"RM001"}
    out = m.build_costs("pb", "PB", [], {"RM001"}, rows, {"RM001": (D(90), "KG")}, CUTOVER, mixed)
    assert out[0]["data"]["source"] == "purchase_last"


def test_cost_purchase_last_and_bom_converted_to_product_uom_same_xmlid() -> None:
    """QA G65: bundel `cost` tanpa uom.jsonl tidak bisa dikonversi importer -> export mengonversi PACK -> EA."""
    umap = maps_with().uom
    out = m.build_costs("pb", "PB", [], {"PCK1", "KGP"}, (), {"PCK1": (D(600), "PACK"), "KGP": (D(9000), "KG")},
                        CUTOVER, None, {"PCK1": "EA", "KGP": "KG"}, umap)  # fmt: skip
    by = {r["esb_key"]: r["data"] for r in out}
    assert by["PCK1"]["standard_price"] == 100 and by["PCK1"]["source_uom"] == "EA"
    assert by["KGP"]["standard_price"] == 9000 and by["KGP"]["source_uom"] == "KG"
    # Satuan tanpa faktor ke UoM produk (G tidak ada di uom_map uji): sumber dilewati + dicatat, BUKAN apa adanya.
    bad: set[tuple[str, str, str, str]] = set()
    lost = m.build_costs("pb", "PB", [], {"KGP"}, (), {"KGP": (D(9), "G")}, CUTOVER, None, {"KGP": "KG"}, umap,
                         bad)  # fmt: skip
    assert lost == [] and bad == {("PB", "KGP", "purchase_last", "G")}


UMAP_G = m.parse_uom_map(UOM_MAP + "G,uom.product_uom_gram,\nBOX,uom.product_uom_box_x,\n")


def test_weight_family_g_to_kg_converted_for_cost_supplierinfo_and_stock() -> None:
    """QA G65 P2: beli/stok G, produk KG -> faktor berat Odoo (1 G = 0,001 KG); xmlid tak dikenal beda -> ditolak."""
    assert m.uom_ratio("G", "KG", UMAP_G) == D("0.001") and m.uom_ratio("KG", "G", UMAP_G) == 1000
    assert m.uom_ratio("BOX", "KG", UMAP_G) is None and m.uom_ratio("EA", "KG", UMAP_G) is None
    assert m.to_product_qty(D(2500), "G", "KG", UMAP_G) == D("2.5")
    out = m.build_costs("pb", "PB", [], {"KGP"}, (), {"KGP": (D(9), "G")}, CUTOVER, None, {"KGP": "KG"}, UMAP_G)
    assert out[0]["data"]["standard_price"] == 9000 and out[0]["data"]["source_uom"] == "KG"  # Rp 9/G = Rp 9.000/KG
    rows = [purchase(date(2026, 11, 2), "PO2", code="KGP", price="12", uom="G"),
            purchase(date(2026, 11, 3), "PO3", supplier="SUP2", code="KGP", price="5", uom="BOX")]  # fmt: skip
    latest = m.latest_purchases(rows, m.PURCHASE_PRICE_STATUSES, date(2025, 12, 1), CUTOVER)
    skips = m.PurchaseSkips()
    infos = m.build_supplierinfos("pb", "PB", latest, {"SUP1", "SUP2"}, {"KGP"}, UMAP_G, "nominal", skips,
                                  {"KGP": "KG"})  # fmt: skip
    assert [(r["esb_key"], r["data"]["price"], r["data"]["uom_xmlid"]) for r in infos] == [
        ("SUP1|KGP", 12000, "uom.product_uom_kgm")
    ]
    assert skips.unmapped == {("PB", "SUP2", "KGP"): "uom_unconvertible"}


def test_opening_cost_only_from_loaded_branches_and_locations(source: FakeSource) -> None:
    """QA G65 P3: HPP stock_opening hanya dari baris yang dimuat langkah stock (bukan cabang skip / lokasi skip)."""
    source.opening = [
        opening(11, "RM001", "10", "100000"),  # 10.000/KG -> dipakai
        opening(11, "RM001", "10", "0", location="Damage"),  # location_map skip -> tidak ikut rata-rata
        opening(99, "RM001", "10", "500000"),  # cabang skip -> tidak ikut rata-rata
    ]
    inputs = G65Inputs.load(source, "pb", ["cost"], CUTOVER)
    rows, _ = _master(source, ["cost"], inputs)
    by = {r["esb_key"]: r["data"] for r in rows["cost"]}
    assert by["RM001"]["source"] == "stock_opening" and by["RM001"]["standard_price"] == 10000


def test_idr_codes_accept_rp() -> None:
    assert all(m.is_idr(v) for v in ("", None, "IDR", "idr", "Rp", "RP", " rp "))
    assert not m.is_idr("USD") and not m.is_idr("1")


def _master(
    source: FakeSource, steps: list[str], inputs: G65Inputs, cutover: date | None = CUTOVER, maps: Maps | None = None
) -> tuple[dict[str, list[dict[str, Any]]], Any]:
    result = G65Result()
    run = params(cutover=cutover)
    records, _ = master_records(master_data(source), "pb", maps or maps_with(), steps, None, None, inputs, run, result)
    return records, result


# ================================================================== stok awal
def stock_fixture(source: FakeSource) -> None:
    source.dims["dim_esb_product"].append(product("LVA1", "Kursi", "FA LVA", "EA", 0, None))
    source.opening = [
        opening(11, "RM001", "10", "125000", location="Gudang Utama"),
        opening(11, "RM001", "2", "25000", location="Dapur"),
        opening(11, "RM001", "1", "12000", location="Damage"),  # location_map: skip
        opening(12, "RM001", "5", "60000", location="Expired Area"),  # berpola, tanpa peta -> peringatan
        opening(12, "FIN-001", "-3", "-10500", uom="EA"),  # negatif -> tidak dimigrasi
        opening(30, "RM001", "4", "48000"),  # cabang lokasi -> branch_loc
        opening(99, "RM001", "7", "84000"),  # cabang skip
        opening(11, "LVA1", "2", "500000", uom="EA"),  # non-storable
        opening(11, "UNKNOWN", "1", "1000"),  # produk tanpa master
        opening(4, "FIN-001", "6", "21000", uom="EA"),
    ]


def test_build_quants_aggregates_and_skips(source: FakeSource) -> None:
    stock_fixture(source)
    inputs = G65Inputs.load(source, "pb", ["stock"], CUTOVER)
    rows, result = _master(source, ["stock"], inputs)
    by = {(r["data"]["location_key"], r["data"]["product_code"]): r for r in rows["quant"]}
    assert {k: r["data"]["quantity"] for k, r in by.items()} == {
        (WH[11], "RM001"): 12, (WH[12], "RM001"): 5, (LOC30, "RM001"): 4, (WH[4], "FIN-001"): 6,
    }  # fmt: skip
    main = by[(WH[11], "RM001")]
    assert main["esb_key"] == "11|RM001" and main["xmlid"] is None
    assert main["data"]["esb_locations"] == ["Dapur", "Gudang Utama"] and main["data"]["amount"] == 150000
    assert main["data"]["accounting_date"] == "2026-11-30" and main["data"]["uom_xmlid"] == "uom.product_uom_kgm"
    assert set(main["data"]) == m.DATA_KEYS["quant"] - {"quarantine_of"}  # quarantine_of hanya baris karantina
    stock = result.stock
    assert stock.negative_stock == [{"branch_id": 12, "product_code": "FIN-001", "qty": -3}]
    assert (stock.non_storable, stock.product_unmapped, stock.branch_unmapped, stock.location_skip) == (1, 1, 1, 1)
    assert (12, "Expired Area") in stock.location_suspect and (11, "Dapur") in stock.location_unmapped
    assert (11, "Damage") not in stock.location_unmapped  # dipetakan skip
    for stat in stock.by_branch.values():  # neraca qty: sumber = diekspor + dilewati
        assert stat["source_qty"] == stat["exported_qty"] + stat["skipped_qty"]
    assert stock.by_branch[11]["source_qty"] == 16 and stock.by_branch[11]["exported_qty"] == 12


def test_location_map_parse_rules() -> None:
    assert m.parse_location_map(LOCATION_MAP, "pb") == {(11, "DAMAGE"): "skip"}
    head = "brand,branch_id,esb_location_name,action\n"
    bad_maps = ("brand,branch_id,esb_location_name\npb,1,X\n", head + "pb,x,A,stock\n", head + "pb,1,A,buang\n",
                head + "pb,1,A,stock\npb,1, a ,skip\n")  # fmt: skip
    for bad in bad_maps:
        with pytest.raises(m.MapError):
            m.parse_location_map(bad, "pb")


def _stock_bundle(tmp_path: Path, keys: b.Keys, source: FakeSource, **kw: Any) -> b.Bundle:
    steps = expand_steps(["stock", "cutover-param"])
    inputs = G65Inputs.load(source, "pb", steps, CUTOVER)
    path = export_master(master_data(source), params(**kw), steps, maps_with(), keys, tmp_path, now=NOW, g65=inputs)
    return b.read_bundle(path, keys, "bundle")


def test_stock_bundle_validates_and_manifest(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    stock_fixture(source)
    bundle = _stock_bundle(tmp_path, keys, source)
    man = bundle.manifest
    assert man["steps"] == ["stock", "cutover-param"] and {f["name"] for f in man["files"]} == {"quant.jsonl"}
    assert {x["name"] for x in man["maps"]} >= {"maps/location_map.csv"}
    assert man["source"]["snapshot"]["fact_esb_stock_opening"]["opening_date"] == "2026-12-01"
    assert man["stock_opening_missing_branches"] == [] and man["stock_without_cost"] == []
    assert man["skipped"]["negative_stock"]["count"] == 1 and man["stock_by_branch"]["11"]["source_qty"] == 16
    report = validate_bundle(bundle)
    assert report.errors == 0, report.findings
    assert {"STOCK_NEGATIVE", "LOCATION_UNMAPPED", "LOCATION_SUSPECT"} <= codes(report, "warning")
    assert "LOCATION_SUSPECT" in codes(validate_bundle(bundle, production=True))  # Q-ESB25: error di production


def test_stock_gates(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    stock_fixture(source)
    with pytest.raises(StepError):
        expand_steps(["opening"])
    with pytest.raises(StepError):
        G65Inputs.load(source, "pb", ["stock"], None)
    with pytest.raises(StepError):
        export_master(master_data(source), params(cutover=None), ["stock"], maps_with(), keys, tmp_path)
    with pytest.raises(GateError, match="PB"):
        _stock_bundle(tmp_path / "blocked", keys, source, blocked=frozenset({"PB"}))
    source.checkpointed = [4, 11]  # cabang 12 & 30 belum di-snapshot
    (tmp_path / "missing").mkdir()
    with pytest.raises(StockGateError, match="2 cabang"):
        _stock_bundle(tmp_path / "missing", keys, source)
    (written,) = list((tmp_path / "missing").glob("*.efnmig"))  # bundel tetap ditulis untuk diperiksa
    report = validate_bundle(b.read_bundle(written, keys, "bundle"))
    assert "STOCK_CHECKPOINT_MISSING" in codes(report)
    # Deklarasi operator (--stock-empty-branch): cabang tanpa kartu stok ESB -> peringatan, bukan gerbang.
    (tmp_path / "declared").mkdir()
    declared = _stock_bundle(tmp_path / "declared", keys, source, stock_empty_branches=frozenset({12, 30, 999}))
    assert declared.manifest["stock_opening_missing_branches"] == []
    assert declared.manifest["stock_empty_branches_declared"] == [12, 30]
    report = validate_bundle(declared)
    assert "STOCK_CHECKPOINT_MISSING" not in codes(report)
    assert "STOCK_EMPTY_DECLARED" in codes(report, "warning")
    assert "stock_empty_branches=12,30" in report.to_markdown()  # SEC-G65b-01: tampil di banner pengecualian
    (tmp_path / "partial").mkdir()
    with pytest.raises(StockGateError, match="1 cabang"):
        _stock_bundle(tmp_path / "partial", keys, source, stock_empty_branches=frozenset({12}))
    with pytest.raises(StepError, match="punya snapshot"):  # cabang 11 punya checkpoint -> deklarasi ditolak
        _stock_bundle(tmp_path / "partial", keys, source, stock_empty_branches=frozenset({11}))


def test_stock_product_without_cost_and_mixed_uom(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    source.dims["dim_esb_product"].append(product("RM002", "Gula", "Local - Dry", "KG", 0, None))
    source.opening = [opening(11, "RM002", "3", "0"), opening(11, "RM001", "1", "100"),
                      opening(11, "RM001", "500", "50", location="Dapur", uom="G")]  # fmt: skip
    bundle = _stock_bundle(tmp_path, keys, source)
    report = validate_bundle(bundle)
    assert {"QUANT_NO_COST", "STOCK_UOM_UNCONVERTIBLE"} <= codes(report)  # G tanpa uom_map -> ditolak, bukan apa adanya
    assert bundle.manifest["skipped"]["uom_unconvertible"]["items"] == [
        {"branch_id": 11, "product_code": "RM001", "uom": "G"}
    ]
    (rm001,) = [r for r in bundle.rows("quant.jsonl") if r["data"]["product_code"] == "RM001"]
    assert rm001["data"]["quantity"] == 1


def test_stock_converts_to_product_uom_with_ea_factor(source: FakeSource) -> None:
    """Temuan W-C: PACK (=6 EA) dan EA sama-sama `uom.product_uom_unit` -> qty dikonversi export, bukan apa adanya."""
    source.dims["dim_esb_product"].append(product("PCK1", "Cup", "Packaging", "EA", 0, None))
    source.opening = [opening(11, "PCK1", "2", "1200", uom="PACK"), opening(11, "PCK1", "3", "300", uom="EA")]
    inputs = G65Inputs.load(source, "pb", ["stock", "cost"], CUTOVER)
    rows, result = _master(source, ["stock", "cost"], inputs)
    (q,) = rows["quant"]
    assert q["data"]["quantity"] == 15 and q["data"]["uom_esb"] == "EA"  # 2 PACK × 6 + 3 EA
    assert q["data"]["uom_xmlid"] == "uom.product_uom_unit" and q["data"]["amount"] == 1500
    stat = result.stock.by_branch[11]  # neraca tetap dalam satuan ESB baris
    assert stat["source_qty"] == stat["exported_qty"] == 5 and stat["skipped_qty"] == 0
    cost = next(r["data"] for r in rows["cost"] if r["esb_key"] == "PCK1")
    assert cost["source"] == "stock_opening" and cost["standard_price"] == 100 and cost["source_uom"] == "EA"
    umap = maps_with().uom
    assert m.to_product_qty(D(2), "PACK", "EA", umap) == 12
    assert m.to_product_qty(D(12), "EA", "PACK", umap) == 2
    assert m.to_product_qty(D(1), "KG", "EA", umap) is None  # uom_xmlid beda -> tidak ditebak
    assert m.to_product_qty(D(1), "BOX", "EA", umap) is None  # tidak ada di uom_map


# ------------------------------------------------------------------ G65b: satuan produk kosong -> satuan dasar snapshot
def test_opening_uoms_fills_only_empty_and_flags_conflicts() -> None:
    rows = [opening(11, "A", "1", "1", uom="GR"), opening(12, "A", "2", "2", uom=" gr "),
            opening(11, "B", "1", "1", uom="PCS"), opening(11, "B", "1", "1", uom="BOX"),
            opening(11, "C", "1", "1", uom="EA"), opening(11, "D", "1", "1", uom="")]  # fmt: skip
    uoms, conflicts = m.opening_uoms({"A": "", "C": "KG"}, rows)
    assert uoms["A"] == "GR" and uoms["C"] == "KG"  # terisi -> tidak diubah walau snapshot EA
    assert "B" not in uoms and conflicts == {"B": ["BOX", "PCS"]}
    assert "D" not in uoms  # baris tanpa satuan tidak mengisi apa pun
    assert m.opening_uoms(uoms, rows) == (uoms, conflicts)  # idempoten


def test_stock_and_cost_use_opening_base_uom_when_product_uom_empty(
    tmp_path: Path, keys: b.Keys, source: FakeSource
) -> None:
    """Gladi 24 Nov: dim_esb_product.uom kosong (semua PB) -> STOCK_UOM_UNCONVERTIBLE massal. Kini satuan dasar baris
    saldo awal (extract unitToShow=Base Unit) dipakai untuk qty, HPP stock_opening dan uom_xmlid quant."""
    source.dims["dim_esb_product"] += [product("PAM1", "Air", "Local - Dry", "", 0, None),
                                       product("PAM2", "Gula", "Local - Dry", "", 0, None)]  # fmt: skip
    source.opening = [
        opening(11, "PAM1", "24", "48000", uom="EA"),
        opening(12, "PAM1", "6", "12000", uom="EA"),
        opening(11, "PAM2", "1", "100", uom="EA"),
        opening(11, "PAM2", "2", "300", uom="PACK"),  # >1 satuan di snapshot -> produk ditolak
        opening(4, "FIN-001", "6", "21000", uom="EA"),  # satuan dari BOM (EA) -> perilaku lama
    ]
    inputs = G65Inputs.load(source, "pb", ["stock", "cost"], CUTOVER)
    rows, result = _master(source, ["stock", "cost"], inputs)
    by = {(r["data"]["location_key"], r["data"]["product_code"]): r["data"] for r in rows["quant"]}
    pam1 = by[(WH[11], "PAM1")]
    assert pam1["quantity"] == 24 and pam1["uom_esb"] == "EA" and pam1["uom_xmlid"] == "uom.product_uom_unit"
    assert by[(WH[12], "PAM1")]["quantity"] == 6 and by[(WH[4], "FIN-001")]["uom_esb"] == "EA"
    assert all(code != "PAM2" for _, code in by)
    assert result.stock.uom_conflict == {"PAM2": ["EA", "PACK"]} and result.stock.uom_unconvertible == set()
    assert result.stock.by_branch[11]["skipped_qty"] == 3  # neraca: baris konflik dihitung dilewati
    costs = {r["esb_key"]: r["data"] for r in rows["cost"]}
    assert costs["PAM1"] == {**costs["PAM1"], "source": "stock_opening", "standard_price": 2000, "source_uom": "EA"}
    assert "PAM2" not in costs
    assert ("PB", "PAM2", "stock_opening", "EA,PACK") in result.cost_unconvertible
    assert all(uom for *_, uom in result.cost_unconvertible)  # item skip tidak lagi ber-uom ''
    bundle = _stock_bundle(tmp_path, keys, source)
    assert bundle.manifest["skipped"]["uom_conflict"] == {
        "count": 1, "items": [{"product_code": "PAM2", "uoms": ["EA", "PACK"]}]
    }  # fmt: skip
    assert bundle.manifest["skipped"]["uom_unconvertible"]["count"] == 0
    found = codes(validate_bundle(bundle))
    assert "STOCK_UOM_CONFLICT" in found and "STOCK_UOM_UNCONVERTIBLE" not in found


# ------------------------------------------------------------------ G65b: location_map quarantine didukung
QUARANTINE_MAP = LOCATION_MAP + "pb,11,Rusak,quarantine\npb,30,Karantina,quarantine\n"


def test_quarantine_rows_become_quarantine_quants(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    source.opening = [
        opening(11, "RM001", "10", "100000"),
        opening(11, "RM001", "2", "30000", location="Rusak"),
        opening(30, "RM001", "1", "20000", location="Karantina"),  # cabang lokasi -> induk gudang 4
        opening(11, "RM001", "1", "12000", location="Damage"),  # skip tetap dilewati
    ]
    inputs = G65Inputs.load(source, "pb", ["stock", "cost"], CUTOVER)
    rows, result = _master(source, ["stock", "cost"], inputs, maps=maps_with(QUARANTINE_MAP))
    by = {r["data"]["location_key"]: r for r in rows["quant"]}
    q11 = by[xmlid("pb", "PB", "branch_quarantine", 11)]
    assert q11["esb_key"] == "11|RM001" and q11["xmlid"] is None
    assert q11["data"]["quantity"] == 2 and q11["data"]["quarantine_of"] == WH[11]
    assert q11["data"]["esb_locations"] == ["Rusak"] and q11["data"]["uom_xmlid"] == "uom.product_uom_kgm"
    assert set(q11["data"]) == m.DATA_KEYS["quant"]
    assert by[xmlid("pb", "PB", "branch_quarantine", 30)]["data"]["quarantine_of"] == WH[4]
    assert by[WH[11]]["data"]["quantity"] == 10 and "quarantine_of" not in by[WH[11]]["data"]
    assert result.stock.quarantine_rows == 2 and result.stock.location_skip == 1
    cost = next(r["data"] for r in rows["cost"] if r["esb_key"] == "RM001")
    assert cost["source"] == "stock_opening" and cost["standard_price"] == 11538.4615  # 150.000 / 13 (karantina ikut)
    steps = expand_steps(["stock", "cutover-param"])
    path = export_master(master_data(source), params(), steps, maps_with(QUARANTINE_MAP), keys, tmp_path, now=NOW,
                         g65=G65Inputs.load(source, "pb", steps, CUTOVER))  # fmt: skip
    bundle = b.read_bundle(path, keys, "bundle")
    assert bundle.manifest["quarantine_rows"] == 2 and "quarantine" not in bundle.manifest["skipped"]
    report = validate_bundle(bundle)
    assert report.errors == 0, report.findings


# ================================================================== validator master G65 (kasus negatif)
def _bundle(keys: b.Keys, records: dict[str, list[dict[str, Any]]], files: dict[str, bytes] | None = None,
            **extra: Any) -> b.Bundle:  # fmt: skip
    members, entries = {}, []
    for entity, rows in records.items():
        blob, n = b.jsonl_bytes(rows)
        members[f"{entity}.jsonl"] = blob
        entries.append(b.file_entry(f"{entity}.jsonl", entity, blob, n))
    maps = []
    for name, data in (files or {}).items():
        members[name] = data
        maps.append({"name": name, "sha256": b.sha256_hex(data), "rows": data.count(b"\n") - 1})
    man = {"run_id": str(uuid.uuid4()), "brand": "pb", "company_codes": ["PB"], "legacy_days": 400,
           "sales_line_months": 24, "cutover_date": "2026-12-01", "files": entries, "maps": maps, **extra}  # fmt: skip
    return b.open_bundle(b.build_bundle(man, members, keys), keys)


def sinfo(supplier: str = "SUP1", code: str = "RM001", **data: Any) -> dict[str, Any]:
    base = {"partner_key": xmlid("pb", "PB", "supplier", supplier), "product_key": xmlid("pb", "PB", "product", code),
            "supplier_code": supplier, "product_code": code, "price": 100, "discount": 0, "currency": "IDR",
            "min_qty": 0, "sequence": 1, "date_start": "2026-11-01", "uom_esb": "KG",
            "uom_xmlid": "uom.product_uom_kgm", "source_purchase_num": "PO1"}  # fmt: skip
    base.update(data)
    return {"entity": "supplierinfo", "company_code": "PB", "esb_key": f"{supplier}|{code}",
            "xmlid": m.supplierinfo_xmlid("pb", "PB", supplier, code), "data": base}  # fmt: skip


def test_validator_supplierinfo_rules(keys: b.Keys) -> None:
    assert validate_bundle(_bundle(keys, {"supplierinfo": [sinfo(), sinfo("SUP2", sequence=2)]})).errors == 0
    bad = [sinfo(price=0), sinfo(currency="USD"), sinfo(discount=120), sinfo("SUP2", sequence=3),
           sinfo("SUP3", code="X1", discount="10")]  # fmt: skip
    report = validate_bundle(_bundle(keys, {"supplierinfo": bad}))
    expected = {"SUPPLIERINFO_PRICE", "SUPPLIERINFO_CURRENCY", "SUPPLIERINFO_DISCOUNT", "SUPPLIERINFO_DUP",
                "SUPPLIERINFO_SEQUENCE", "DUPLICATE_XMLID"}  # fmt: skip
    assert expected <= codes(report)
    data = {"default_code": "RM001", "name": "x", "uom_xmlid": "uom.product_uom_kgm"}
    product = {"entity": "product", "company_code": "PB", "esb_key": "RM001",
               "xmlid": xmlid("pb", "PB", "product", "RM001"), "data": data}  # fmt: skip
    assert "DUPLICATE_XMLID" in codes(validate_bundle(_bundle(keys, {"product": [product, dict(product)]})))
    extra = sinfo()
    extra["data"]["supplier_phone"] = "0812"
    assert "PII_WHITELIST" in codes(validate_bundle(_bundle(keys, {"supplierinfo": [extra]})))
    with pytest.raises(b.IntegrityError, match="partner_key"):
        validate_bundle(_bundle(keys, {"supplierinfo": [sinfo(partner_key=xmlid("pb", "PB", "supplier", "LAIN"))]}))
    with pytest.raises(b.IntegrityError, match="esb_key"):
        validate_bundle(_bundle(keys, {"supplierinfo": [{**sinfo(), "esb_key": "SUP1_RM001"}]}))


def cost(code: str, **data: Any) -> dict[str, Any]:
    base = {"product_key": xmlid("pb", "PB", "product", code), "product_code": code, "standard_price": 10,
            "source_uom": "KG", "source": "stock_opening", "as_of": "2026-12-01"}  # fmt: skip
    base.update(data)
    return {"entity": "cost", "company_code": "PB", "esb_key": code, "xmlid": None, "data": base}


def test_validator_cost_rules(keys: b.Keys) -> None:
    good = [cost("A"), cost("B", source="purchase_last", as_of="x")]  # as_of hanya diikat untuk stock_opening
    assert validate_bundle(_bundle(keys, {"cost": good})).errors == 0
    report = validate_bundle(_bundle(keys, {"cost": [cost("A", source="tebakan"), cost("B", as_of="2026-11-30")]}))
    assert {"COST_SOURCE", "COST_AS_OF"} <= codes(report)


def quant(loc: str = WH[11], qty: float = 5, **data: Any) -> dict[str, Any]:
    base = {"location_key": loc, "product_key": xmlid("pb", "PB", "product", "RM001"), "product_code": "RM001",
            "quantity": qty, "uom_esb": "KG", "uom_xmlid": "uom.product_uom_kgm", "esb_branch_id": 11,
            "esb_locations": ["Gudang"], "amount": 50, "accounting_date": "2026-11-30"}  # fmt: skip
    base.update(data)
    return {"entity": "quant", "company_code": "PB", "esb_key": "11|RM001", "xmlid": None, "data": base}


OPEN_META = {"source": {"snapshot": {"fact_esb_stock_opening": {"opening_date": "2026-12-01"}}}}


def test_validator_quant_rules(keys: b.Keys) -> None:
    assert validate_bundle(_bundle(keys, {"quant": [quant()]}, **OPEN_META)).errors == 0
    bad = [quant(qty=0), quant(loc=xmlid("pb", "WTZ", "branch_wh", 11)), quant(loc=WH[12], uom_xmlid=None),
           quant(loc=LOC30, accounting_date="2026-12-01"), quant()]  # fmt: skip
    report = validate_bundle(_bundle(keys, {"quant": bad}, **OPEN_META, stock_without_cost=["PB|RM009"],
                                     stock_opening_missing_branches=[12]))  # fmt: skip
    expected = {"QUANT_QTY", "QUANT_LOCATION", "QUANT_UOM", "QUANT_ACCOUNTING_DATE", "QUANT_DUP", "QUANT_NO_COST",
                "STOCK_CHECKPOINT_MISSING"}  # fmt: skip
    assert expected <= codes(report)
    wrong_day = {"source": {"snapshot": {"fact_esb_stock_opening": {"opening_date": "2026-11-25"}}}}
    assert "STOCK_OPENING_DATE" in codes(validate_bundle(_bundle(keys, {"quant": [quant()]}, **wrong_day)))
    with pytest.raises(b.IntegrityError, match="product_key"):
        validate_bundle(_bundle(keys, {"quant": [quant(product_key=xmlid("pb", "PB", "product", "X"))]}, **OPEN_META))
    # G65b: action quarantine didukung -> location_map dengan quarantine tidak lagi error.
    quarantine = b"brand,branch_id,esb_location_name,action\npb,11,Rusak,quarantine\n"
    report = validate_bundle(_bundle(keys, {"quant": [quant()]}, {"maps/location_map.csv": quarantine}, **OPEN_META))
    assert report.errors == 0, report.findings
    qloc = xmlid("pb", "PB", "branch_quarantine", 11)
    assert validate_bundle(_bundle(keys, {"quant": [quant(loc=qloc, quarantine_of=WH[11])]}, **OPEN_META)).errors == 0
    bad_q = [
        quant(loc=qloc),  # tanpa quarantine_of
        quant(loc=xmlid("pb", "PB", "branch_quarantine", 12), quarantine_of=WH[12]),  # karantina cabang lain
        quant(loc=qloc, quarantine_of=xmlid("pb", "WTZ", "branch_wh", 11)),  # induk PT lain
        quant(loc=WH[12], quarantine_of=WH[12]),  # quarantine_of di baris non-karantina
    ]
    report = validate_bundle(_bundle(keys, {"quant": bad_q}, **OPEN_META))
    assert {"QUANT_QUARANTINE_OF", "QUANT_LOCATION"} <= codes(report)
    conflict = {"uom_conflict": {"count": 1, "items": [{"product_code": "RM001", "uoms": ["GR", "PCS"]}]}}
    report = validate_bundle(_bundle(keys, {"quant": [quant()]}, **OPEN_META, skipped=conflict))
    assert "STOCK_UOM_CONFLICT" in codes(report)


# ================================================================== histori pembelian
def purchase_history_fixture(source: FakeSource) -> None:
    source.purchases = [
        purchase(date(2026, 10, 2), "PO30"),
        purchase(date(2026, 11, 5), "PO10", line_no=1),
        purchase(date(2026, 11, 5), "PO10", line_no=2, supplier="SUPX", code="ZZZ", map_source="name"),
        purchase(date(2026, 11, 6), "PO11", branch_id=0, map_source="none"),
        purchase(date(2026, 11, 7), "PO12", currency="USD"),
        purchase(date(2026, 11, 8), "PO13", branch_id=30, status="Draft"),
    ]


def _history(tmp_path: Path, keys: b.Keys, source: FakeSource, steps: list[str], months: list[str]) -> list[Any]:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    ids = iter(str(uuid.UUID(int=i + 1)) for i in range(1000))
    _, segments = export_history(
        source, master_data(source), params(), steps, maps_with(None), keys, tmp_path, work, months, now=NOW,
        parent_run_id=str(uuid.UUID(int=999)), run_id_factory=lambda: next(ids),
    )  # fmt: skip
    return segments


def test_purchase_history_part1_only_and_rows(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    purchase_history_fixture(source)
    segments = _history(tmp_path, keys, source, ["legacy_sales_line", "legacy_purchase_line"], ["2026-10", "2026-11"])
    bundles = [b.read_bundle(s.path, keys, "bundle") for s in segments]
    october = [x for x in bundles if x.manifest["segment"]["month"] == "2026-10"]
    assert len(october) == 2  # sales.line memecah per gudang; pembelian hanya di part 1
    for bd in october:
        has = bd.entity_file("legacy_purchase_line") is not None
        assert has == (bd.manifest["segment"]["part"] == 1)
    (november,) = [x for x in bundles if x.manifest["segment"]["month"] == "2026-11"]
    rows = {(r["purchase_num"], r["line_no"]): r for r in november.rows("legacy_purchase_line.jsonl.gz")}
    assert set(rows) == {("PO10", 1), ("PO10", 2), ("PO11", 1), ("PO13", 1)}  # USD tidak diekspor
    assert november.manifest["stats"]["legacy_purchase_line"]["rows_skipped_currency"] == 1
    first = rows[("PO10", 1)]
    assert set(first) == set(m.HISTORY_KEYS["legacy_purchase_line"]) | m.HISTORY_OPTIONAL_KEYS["legacy_purchase_line"]
    assert first["branch_key"] == WH[11] and first["supplier_key"] == xmlid("pb", "PB", "supplier", "SUP1")
    assert first["product_key"] == xmlid("pb", "PB", "product", "RM001") and first["price_unit"] == 12000
    assert rows[("PO10", 2)]["supplier_key"] is None and rows[("PO10", 2)]["product_key"] is None
    assert rows[("PO11", 1)]["branch_key"] is None and rows[("PO13", 1)]["branch_key"] == WH[4]
    assert rows[("PO13", 1)]["status"] == "Draft"  # semua status dimuat (arsip)
    manifests = [x.manifest for x in bundles]
    for bd in bundles:
        report = validate_bundle(bd, manifests)
        assert report.errors == 0, report.findings
    warnings = codes(validate_bundle(november, manifests), "warning")
    assert {"PURCHASE_MAPPED_BY_NAME", "PURCHASE_UNMAPPED", "PURCHASE_CURRENCY"} <= warnings


def _purchase_segment(keys: b.Keys, rows: list[dict[str, Any]], part: int = 1, parts: int = 1) -> b.Bundle:
    blob = b.gzip_bytes(b.jsonl_bytes(rows)[0])
    name = "legacy_purchase_line.jsonl.gz"
    entry = {**b.file_entry(name, "legacy_purchase_line", blob, len(rows)), "range": ["2026-11-01", "2026-12-01"]}
    man = {"run_id": str(uuid.uuid4()), "brand": "pb", "company_codes": ["PB"], "legacy_days": 400,
           "sales_line_months": 24, "cutover_date": "2026-12-01", "parent_run_id": str(uuid.UUID(int=5)),
           "segment": {"company_code": "PB", "month": "2026-11", "part": part, "parts": parts},
           "target_warehouses": [WH[11], WH[12]] if parts == 1 else [WH[10 + part]],
           "company_warehouses": [WH[11], WH[12]],
           "files": [entry], "maps": []}  # fmt: skip
    return b.open_bundle(b.build_bundle(man, {name: blob}, keys), keys)


def pline(num: str = "PO1", line_no: int = 1, day: str = "2026-11-05", **kw: Any) -> dict[str, Any]:
    row = {k: None for k in m.HISTORY_KEYS["legacy_purchase_line"]}
    row.update(purchase_num=num, line_no=line_no, date=day, branch_key=WH[11], esb_branch_id=11,
               esb_supplier_code="SUP1", esb_product_code="RM001", qty=1, receipt_qty=1, pricelist_price=1,
               price_unit=1, discount=0, vat_value=0, total=1, status="Finished", map_source="header")  # fmt: skip
    row.update(kw)
    return row


def test_validator_purchase_history_rules(keys: b.Keys) -> None:
    assert validate_bundle(_purchase_segment(keys, [pline(), pline(line_no=2, branch_key=None)])).errors == 0
    report = validate_bundle(_purchase_segment(keys, [pline(), pline(), pline("PO2", map_source="tebak"),
                                                       {**pline("PO3"), "creatorName": "x"}]))  # fmt: skip
    assert {"PURCHASE_LINE_DUP", "PURCHASE_MAP_SOURCE", "HISTORY_KEYS"} <= codes(report)
    with pytest.raises(b.IntegrityError, match="part 1"):
        validate_bundle(_purchase_segment(keys, [pline()], part=2, parts=2))
    with pytest.raises(b.IntegrityError, match="branch_key"):
        validate_bundle(_purchase_segment(keys, [pline(branch_key=xmlid("pb", "PB", "branch_wh", 77))]))
    with pytest.raises(b.IntegrityError, match="supplier_key"):
        validate_bundle(_purchase_segment(keys, [pline(supplier_key=xmlid("pb", "WTZ", "supplier", "SUP1"))]))
    with pytest.raises(b.IntegrityError, match="segment.month"):
        validate_bundle(_purchase_segment(keys, [pline(day="2026-10-31")]))


# ================================================================== rekonsiliasi
def _result(keys: b.Keys, source_bundle: b.Bundle, report_json: dict, totals: dict) -> b.Bundle:
    files = {"import_report.json": json.dumps(report_json).encode(), "odoo_totals.json": json.dumps(totals).encode()}
    entries = [b.file_entry(name, name.split(".")[0], data, 0) for name, data in files.items()]
    man = source_bundle.manifest
    manifest = {"run_id": man["run_id"], "brand": "pb", "company_codes": man["company_codes"],
                "parent_run_id": man.get("parent_run_id"), "files": entries, "maps": []}  # fmt: skip
    return b.open_bundle(b.build_bundle(manifest, files, keys, kind="result"), keys, "result")


def purchase_totals(bundle: b.Bundle) -> list[dict[str, Any]]:
    agg = aggregate_bundle([bundle])["legacy_purchase_line"]
    return [{"month": k[0], "company_code": k[1], "branch_name": k[2], **v} for k, v in agg.items()]


def test_reconcile_purchase_history_5e(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    purchase_history_fixture(source)
    (seg,) = _history(tmp_path, keys, source, ["legacy_purchase_line"], ["2026-11"])
    bundle = b.read_bundle(seg.path, keys, "bundle")
    report_json = {"dry_run": False, "entities": {}}
    ok = reconcile([(bundle, _result(keys, bundle, report_json, {"legacy_purchase_line": purchase_totals(bundle)}))])
    assert ok.blocking == 0 and any(ln.item == "5e_purchase_line" and ln.status == "ok" for ln in ok.lines)
    info = {ln.item for ln in ok.lines if ln.status == "info"}
    assert {"5e_map_source", "5e_mata_uang_dilewati"} <= info
    short = purchase_totals(bundle)
    short[0]["rows"] -= 1
    bad = reconcile([(bundle, _result(keys, bundle, report_json, {"legacy_purchase_line": short}))])
    assert bad.blocking == 1


def _totals(keys: b.Keys, parent: str, totals: dict) -> b.Bundle:
    blob = json.dumps(totals).encode()
    entry = b.file_entry("odoo_totals.json", "odoo_totals", blob, 0)
    man = {"run_id": str(uuid.uuid4()), "brand": "pb", "company_codes": ["PB"], "parent_run_id": parent,
           "steps": ["totals"], "files": [entry], "maps": []}  # fmt: skip
    return b.open_bundle(b.build_bundle(man, {"odoo_totals.json": blob}, keys, kind="result"), keys, "result")


def test_reconcile_final_month_outside_parent_is_info(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    """QA G65 P3: ekspor ulang November saja; Oktober (dimuat ekspor sebelumnya) ada di totals Odoo -> info, bukan
    selisih. November yang hilang di Odoo tetap pemblokir."""
    purchase_history_fixture(source)
    (seg,) = _history(tmp_path, keys, source, ["legacy_purchase_line"], ["2026-11"])
    bundle = b.read_bundle(seg.path, keys, "bundle")
    parent = str(bundle.manifest["parent_run_id"])
    older = {"month": "2026-10", "company_code": "PB", "branch_name": "Cabang 11", "rows": 3, "qty": 30, "total": 9}
    totals = {"legacy_purchase_line": [*purchase_totals(bundle), older]}
    report = reconcile_final(parent, [bundle], [_totals(keys, parent, totals)])
    assert report.blocking == 0
    assert any(ln.item == "5e_purchase_line_di_luar_parent" and ln.status == "info" for ln in report.lines)
    missing = reconcile_final(parent, [bundle], [_totals(keys, parent, {"legacy_purchase_line": [older]})])
    assert missing.blocking > 0


def test_reconcile_stock_item2(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    stock_fixture(source)
    bundle = _stock_bundle(tmp_path, keys, source)
    quants = list(bundle.rows("quant.jsonl"))
    totals = [{"company_code": q["company_code"], "location_key": q["data"]["location_key"],
               "product_key": q["data"]["product_key"], "quantity": q["data"]["quantity"],
               "value": q["data"]["amount"]} for q in quants]  # fmt: skip
    report_json = {"dry_run": False, "entities": {"quant": {"created": len(quants)}}}
    ok = reconcile([(bundle, _result(keys, bundle, report_json, {"quant": totals}))])
    assert ok.blocking == 0
    items = {ln.item for ln in ok.lines}
    assert {"2_stok_qty", "2_stok_sumber", "2_stok_nilai", "2_stok_nilai_pt"} <= items and "2_stok" not in items
    assert any(ln.item == "4_neraca" and ln.status == "tidak_dijalankan" for ln in ok.lines)
    totals[0]["value"] = totals[0]["value"] * 3  # HPP rata-rata PT jauh dari nilai buku -> peringatan, bukan pemblokir
    warn = reconcile([(bundle, _result(keys, bundle, report_json, {"quant": totals}))])
    assert warn.blocking == 0 and any(ln.item == "2_stok_nilai_pt" and ln.status == "peringatan" for ln in warn.lines)
    totals[0]["quantity"] -= 1
    assert reconcile([(bundle, _result(keys, bundle, report_json, {"quant": totals}))]).blocking == 1


def test_reconcile_stock_importer_skipped_is_info(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    """QA G65b: baris yang dilewati importer (--skip-unconvertible-stock; qty Odoo 0) = info, bukan selisih
    pemblokir; baris lain tetap dibandingkan, dan 0 untuk kunci yang TIDAK dilaporkan dilewati tetap selisih."""
    stock_fixture(source)
    bundle = _stock_bundle(tmp_path, keys, source)
    quants = list(bundle.rows("quant.jsonl"))
    totals = [{"company_code": q["company_code"], "location_key": q["data"]["location_key"],
               "product_key": q["data"]["product_key"], "quantity": q["data"]["quantity"],
               "value": q["data"]["amount"]} for q in quants]  # fmt: skip
    totals[0]["quantity"], totals[0]["value"] = 0.0, 0.0  # importer melewati baris ini
    report_json = {"dry_run": False, "entities": {"quant": {"created": len(quants) - 1, "skipped": 1}},
                   "stock_skipped": {"non_storable": 0, "uom_incompatible": 1,
                                     "uom_incompatible_keys": [quants[0]["esb_key"]]}}  # fmt: skip
    ok = reconcile([(bundle, _result(keys, bundle, report_json, {"quant": totals}))])
    assert not any(ln.item == "2_stok_qty" and ln.status == "selisih" for ln in ok.lines)
    assert any(ln.item == "2_stok_dilewati_importer" and ln.status == "info" for ln in ok.lines)
    undeclared = {"dry_run": False, "entities": {"quant": {"created": len(quants) - 1, "skipped": 1}}}
    bad = reconcile([(bundle, _result(keys, bundle, undeclared, {"quant": totals}))])
    assert any(ln.item == "2_stok_qty" and ln.status == "selisih" for ln in bad.lines)


# ================================================================== G65b --allow-zero-cost, --skip-unconvertible-stock
def zero_cost_fixture(source: FakeSource) -> None:
    source.dims["dim_esb_product"] += [product("ZC1", "Nol", "Local - Dry", "KG", 0, None),
                                       product("NV1", "Bernilai", "Local - Dry", "KG", 0, None)]  # fmt: skip
    source.opening = [
        opening(11, "ZC1", "4", "0"),
        opening(12, "ZC1", "1", "0"),  # semua amount_balance 0 -> kandidat cost 0
        opening(11, "NV1", "2", "0"),
        opening(12, "NV1", "-1", "-500"),  # nilai ≠ 0 hanya di baris negatif (tidak dimuat) -> tetap kandidat cost 0
        opening(11, "RM001", "1", "100"),
    ]


def test_zero_value_codes_rules() -> None:
    rows = [opening(11, "A", "2", "0"), opening(11, "B", "2", "0"), opening(12, "B", "1", "10"),
            opening(11, "C", "-1", "0"), opening(11, "D", "3", "0")]  # fmt: skip
    assert m.zero_value_codes(rows, {"A", "B", "C", "D"}, {"D"}) == ["A"]  # B bernilai, C tanpa qty > 0, D ber-HPP
    assert m.zero_value_codes(rows, {"B"}, set()) == []
    # Baris negatif (tidak dimuat) tidak membatalkan nilai 0 baris positif (WIP0022 gladi efn-prod).
    mixed = [opening(11, "E", "0.03", "0"), opening(12, "E", "-0.11", "-2.01")]
    assert m.zero_value_codes(mixed, {"E"}, set()) == ["E"]


def test_allow_zero_cost_off_is_error(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    zero_cost_fixture(source)
    bundle = _stock_bundle(tmp_path, keys, source)
    assert bundle.manifest["stock_without_cost"] == ["PB|NV1", "PB|ZC1"]
    assert bundle.manifest["zero_cost_products"] == []
    assert bundle.manifest["export_options"]["allow_zero_cost"] is False
    report = validate_bundle(bundle)
    assert "QUANT_NO_COST" in codes(report) and "QUANT_ZERO_COST" not in codes(report, "warning")
    assert "ketat (tanpa pengecualian)" in report.to_markdown()


def test_allow_zero_cost_on_cost_zero_and_warning(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    zero_cost_fixture(source)
    opts = m.ExportOptions(allow_zero_cost=True)
    inputs = G65Inputs.load(source, "pb", ["cost"], CUTOVER)
    result = G65Result()
    rows, _ = master_records(master_data(source), "pb", maps_with(), ["cost"], opts, None, inputs,
                             params(options=opts), result)  # fmt: skip
    by = {r["esb_key"]: r["data"] for r in rows["cost"]}
    assert by["ZC1"] == {**by["ZC1"], "standard_price": 0, "source": "stock_opening", "as_of": "2026-12-01",
                         "source_uom": "KG"}  # fmt: skip
    assert by["NV1"]["standard_price"] == 0 and result.zero_cost_products == {"PB|NV1", "PB|ZC1"}
    # Tanpa opsi: tidak ada baris cost 0.
    plain, _ = master_records(master_data(source), "pb", maps_with(), ["cost"], None, None, inputs, params(),
                              G65Result())  # fmt: skip
    assert "ZC1" not in {r["esb_key"] for r in plain["cost"]}
    bundle = _stock_bundle(tmp_path, keys, source, options=opts)
    man = bundle.manifest
    assert man["stock_without_cost"] == [] and man["zero_cost_products"] == ["PB|NV1", "PB|ZC1"]
    assert man["export_options"]["allow_zero_cost"] is True
    report = validate_bundle(bundle)
    assert "QUANT_ZERO_COST" in codes(report, "warning")
    assert "QUANT_NO_COST" not in codes(report)
    assert "allow_zero_cost=True" in report.to_markdown()
    assert report.to_json()["export_options"]["allow_zero_cost"] is True


def test_validator_zero_cost_requires_option(keys: b.Keys) -> None:
    zero = [cost("A", standard_price=0)]
    assert "COST_ZERO_WITHOUT_OPTION" in codes(validate_bundle(_bundle(keys, {"cost": zero})))
    listed_only = _bundle(keys, {"cost": zero}, zero_cost_products=["PB|A"])  # tercatat tapi opsi mati
    assert "COST_ZERO_WITHOUT_OPTION" in codes(validate_bundle(listed_only))
    ok = validate_bundle(_bundle(keys, {"cost": zero}, zero_cost_products=["PB|A"],
                                 export_options={"allow_zero_cost": True}))  # fmt: skip
    assert ok.errors == 0, ok.findings
    assert "QUANT_ZERO_COST" in codes(ok, "warning")
    unlisted = _bundle(
        keys, {"cost": zero}, export_options={"allow_zero_cost": True}
    )  # opsi aktif, produk tak tercatat
    assert "COST_ZERO_WITHOUT_OPTION" in codes(validate_bundle(unlisted))
    # SEC-G65b-02: harga negatif selalu error, walau tercatat + opsi aktif.
    negative = _bundle(keys, {"cost": [cost("A", standard_price=-5)]}, zero_cost_products=["PB|A"],
                       export_options={"allow_zero_cost": True})  # fmt: skip
    assert "COST_NEGATIVE" in codes(validate_bundle(negative))


def unconvertible_fixture(source: FakeSource) -> None:
    # Satuan G tidak ada di uom_map uji -> baris tak terkonversi.
    source.opening = [opening(11, "RM001", "1", "100"),
                      opening(11, "RM001", "500", "50", location="Dapur", uom="G")]  # fmt: skip


def test_skip_unconvertible_stock_off_is_error(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    unconvertible_fixture(source)
    bundle = _stock_bundle(tmp_path, keys, source)
    assert bundle.manifest["export_options"]["skip_unconvertible_stock"] is False
    report = validate_bundle(bundle)
    assert "STOCK_UOM_UNCONVERTIBLE" in codes(report) and "STOCK_UOM_SKIPPED" not in codes(report, "warning")


def test_skip_unconvertible_stock_on_is_warning(tmp_path: Path, keys: b.Keys, source: FakeSource) -> None:
    unconvertible_fixture(source)
    opts = m.ExportOptions(skip_unconvertible_stock=True)
    bundle = _stock_bundle(tmp_path, keys, source, options=opts)
    man = bundle.manifest
    assert man["export_options"]["skip_unconvertible_stock"] is True
    assert man["skipped"]["uom_unconvertible"]["items"] == [{"branch_id": 11, "product_code": "RM001", "uom": "G"}]
    (rm001,) = list(bundle.rows("quant.jsonl"))
    assert rm001["data"]["quantity"] == 1  # baris G tetap TIDAK dimuat
    report = validate_bundle(bundle)
    assert report.errors == 0, report.findings
    assert "STOCK_UOM_SKIPPED" in codes(report, "warning")
    assert "skip_unconvertible_stock=True" in report.to_markdown()
