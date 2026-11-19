"""Opsi pengecualian export (dry-run data nyata PB): default ketat = error; opsi eksplisit = peringatan + tercatat."""

from datetime import date
from pathlib import Path

import pytest
from conftest import FakeSource, bom, bom_line, product

from efn_mig import bundle as b
from efn_mig.export import Maps, MasterData, RunParams, expand_steps, export_master
from efn_mig.mapping import ExportOptions, MapError
from efn_mig.validate import read_export_options, validate_bundle

CUTOVER = date(2026, 10, 8)


def export(tmp_path: Path, keys: b.Keys, maps: Maps, src: FakeSource, options: ExportOptions) -> b.Bundle:
    tmp_path.mkdir(parents=True, exist_ok=True)
    params = RunParams(brand="pb", cutover=CUTOVER, legacy_days=400, sales_line_months=24, options=options)
    data = MasterData.load(src, "pb", date(2024, 10, 1), CUTOVER)
    path = export_master(data, params, expand_steps(["master"]), maps, keys, tmp_path)
    return b.read_bundle(path, keys, "bundle")


def levels(bundle: b.Bundle) -> tuple[set[str], set[str]]:
    report = validate_bundle(bundle)
    return ({f.code for f in report.findings if f.level == "error"},
            {f.code for f in report.findings if f.level == "warning"})  # fmt: skip


def test_strict_default_recorded_and_clean(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    bundle = export(tmp_path, keys, maps, source, ExportOptions())
    assert bundle.manifest["export_options"] == {
        "default_uom": None,
        "skip_unlinked_menu_boms": False,
        "allow_default_ratio": False,
        "drop_zero_qty_components": False,
    }
    assert bundle.manifest["skipped"]["unlinked_menu_boms"] == {"count": 0, "bom_ids": []}
    report = validate_bundle(bundle)
    assert report.errors == 0 and "ketat (tanpa pengecualian)" in report.to_markdown()


def test_default_uom(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    source.dims["dim_esb_product"].append(product("PKG1", "Kemasan", "Bahan", "", 0, None))
    errors, _ = levels(export(tmp_path / "a", keys, maps, source, ExportOptions()))
    assert "PRODUCT_UOM" in errors  # default ketat tetap error
    bundle = export(tmp_path / "b", keys, maps, source, ExportOptions(default_uom="uom.product_uom_unit"))
    row = next(r for r in bundle.rows("product.jsonl") if r["esb_key"] == "PKG1")
    assert row["data"]["uom_xmlid"] == "uom.product_uom_unit" and row["data"]["uom_source"] == "default"
    other = next(r for r in bundle.rows("product.jsonl") if r["esb_key"] == "RM001")
    assert "uom_source" not in other["data"]  # hanya baris yang memakai default
    errors, warnings = levels(bundle)
    assert not errors and "PRODUCT_UOM_DEFAULT" in warnings
    report = validate_bundle(bundle)
    assert "DRY-RUN DENGAN PENGECUALIAN" in report.to_markdown() and "default_uom=uom.product_uom_unit" in (
        report.to_markdown())  # fmt: skip
    assert report.to_json()["export_options"]["default_uom"] == "uom.product_uom_unit"
    with pytest.raises(MapError):
        ExportOptions(default_uom="bukan xmlid")


def test_skip_unlinked_menu_boms(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    source.dims["dim_esb_bom"].append(bom(9, "MENU TANPA TAUTAN", "Menu", ""))
    source.dims["dim_esb_bom_line"].append(bom_line(9, "FIN-001", "1", "EA", "0"))
    errors, _ = levels(export(tmp_path / "a", keys, maps, source, ExportOptions()))
    assert "BOM_PRODUCT_MISSING" in errors
    bundle = export(tmp_path / "b", keys, maps, source, ExportOptions(skip_unlinked_menu_boms=True))
    assert "9" not in {r["esb_key"] for r in bundle.rows("bom.jsonl")}
    assert "8" in {r["esb_key"] for r in bundle.rows("bom.jsonl")}  # BoM menu yang TERTAUT tetap diekspor
    assert bundle.manifest["skipped"]["unlinked_menu_boms"] == {"count": 1, "bom_ids": [9]}
    errors, warnings = levels(bundle)
    assert not errors and "BOM_MENU_UNLINKED_SKIPPED" in warnings


class NoRatioSource(FakeSource):
    def bom_ratios(self, brand: str) -> dict[str, float]:
        return {}


def test_allow_default_ratio(tmp_path: Path, keys: b.Keys, maps: Maps) -> None:
    src = NoRatioSource()
    errors, _ = levels(export(tmp_path / "a", keys, maps, src, ExportOptions()))
    assert "BOM_NO_RATIO" in errors
    bundle = export(tmp_path / "b", keys, maps, src, ExportOptions(allow_default_ratio=True))
    row = next(r for r in bundle.rows("bom.jsonl") if r["esb_key"] == "7")
    assert row["data"]["product_qty"] == 1 and row["data"]["ratio_source"] == "default_1"
    errors, warnings = levels(bundle)
    assert not errors and "BOM_DEFAULT_RATIO" in warnings


def test_drop_zero_qty_components(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    source.dims["dim_esb_bom_line"].append({**bom_line(7, "RM001", "0", "KG", "0"), "line_id": 2})
    errors, _ = levels(export(tmp_path / "a", keys, maps, source, ExportOptions()))
    assert "BOM_QTY" in errors
    bundle = export(tmp_path / "b", keys, maps, source, ExportOptions(drop_zero_qty_components=True))
    row = next(r for r in bundle.rows("bom.jsonl") if r["esb_key"] == "7")
    assert [ln["qty"] for ln in row["data"]["lines"]] == [2.5]
    assert bundle.manifest["skipped"]["zero_qty_components"]["items"] == [{"bom_id": 7, "product_code": "RM001"}]
    errors, warnings = levels(bundle)
    assert not errors and "BOM_QTY_DROPPED" in warnings


def test_validator_reads_manifest_options_fail_closed() -> None:
    assert read_export_options({}) == ExportOptions()
    assert read_export_options({"export_options": {"allow_default_ratio": True}}).allow_default_ratio
    for bad in ({"x": True}, {"allow_default_ratio": "ya"}, {"default_uom": 5}, {"default_uom": "bukan xmlid"}):
        with pytest.raises(b.IntegrityError):
            read_export_options({"export_options": bad})
