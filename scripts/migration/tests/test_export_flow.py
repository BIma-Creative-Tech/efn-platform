"""Alur export -> validasi -> (hasil impor sintetis) -> reconcile / writeback, dengan sumber ClickHouse palsu."""

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import FakeSource

from efn_mig import bundle as b
from efn_mig.export import (
    GateError,
    Maps,
    MasterData,
    RunParams,
    StepError,
    expand_steps,
    export_history,
    export_master,
)
from efn_mig.reconcile import aggregate_bundle, reconcile
from efn_mig.results import DryRunResult, map_rows
from efn_mig.validate import validate_bundle

CUTOVER = date(2026, 10, 8)
NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)


def params(**kw: object) -> RunParams:
    base: dict = {"brand": "pb", "cutover": CUTOVER, "legacy_days": 400, "sales_line_months": 24,
                  "segment_max_rows": 15, "blocked": frozenset({"IMD", "CUP"})}  # fmt: skip
    base.update(kw)
    return RunParams(**base)


def data(source: FakeSource) -> MasterData:
    return MasterData.load(source, "pb", date(2024, 10, 1), CUTOVER)


def test_expand_steps() -> None:
    assert expand_steps(["master"])[:2] == ["branch", "uom"] and "cost" in expand_steps(["master"])
    assert expand_steps(["history", "legacy_flow"]) == ["legacy_flow", "legacy_sales_daily", "legacy_sales_line"]
    assert expand_steps(["cutover"])[-1] == "cutover-param"
    for bad in (["stock"], ["opening"], ["apa"]):
        with pytest.raises(StepError):
            expand_steps(bad)


def test_master_bundle_valid(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    path = export_master(data(source), params(), expand_steps(["master"]), maps, keys, tmp_path, now=NOW)
    opened = b.read_bundle(path, keys, "bundle")
    man = opened.manifest
    assert man["company_codes"] == ["PB"] and man["sales_line_months"] == 24 and man["segment"] is None
    assert {f["name"] for f in man["files"]} >= {"branch.jsonl", "product.jsonl", "menu.jsonl", "bom.jsonl"}
    assert {m["name"] for m in man["maps"]} == {"maps/branch_map.csv", "maps/uom_map.csv"}
    report = validate_bundle(opened)
    assert report.errors == 0, report.findings
    customer = next(opened.rows("customer.jsonl"))
    assert set(customer["data"]) == {"ref", "name", "is_company", "customer_rank", "category_name",
                                     "payment_term_key", "active"}  # fmt: skip


def history(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> tuple[str, list]:
    work = tmp_path / "work"
    work.mkdir()
    ids = iter(str(uuid.UUID(int=i + 1)) for i in range(100))
    return export_history(
        source, data(source), params(), expand_steps(["history"]), maps, keys, tmp_path, work, now=NOW,
        parent_run_id=str(uuid.UUID(int=999)), run_id_factory=lambda: next(ids),
    )  # fmt: skip


def test_history_segments_split_per_branch_and_validate(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    parent, segments = history(tmp_path, keys, maps, source)
    assert [(s.month, s.part, s.parts) for s in segments] == [
        ("2026-09", 1, 2), ("2026-09", 2, 2), ("2026-10", 1, 2), ("2026-10", 2, 2)
    ]  # fmt: skip
    assert segments[0].path.name == f"{parent}_PB_202609_p1.efnmig"
    assert segments[0].skipped["legacy_flow_daily"] == 3  # NAME:ROTI X tak terpetakan
    for seg in segments:
        opened = b.read_bundle(seg.path, keys, "bundle")
        assert opened.manifest["parent_run_id"] == parent and len(opened.manifest["branch_ids"]) == 1
        assert all(f["name"].endswith(".jsonl.gz") for f in opened.manifest["files"])
        report = validate_bundle(opened)
        assert report.errors == 0, report.findings
        assert {f.code for f in report.findings} == {"ROWS_UNMAPPED"}
    assert not list((tmp_path / "work").iterdir())  # plaintext sementara dihapus


def test_history_requires_cutover_and_blocks_companies(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    with pytest.raises(StepError):
        export_history(source, data(source), params(cutover=None), ["legacy_flow"], maps, keys, tmp_path, tmp_path)
    with pytest.raises(GateError):
        export_history(
            source, data(source), params(blocked=frozenset({"PB"})), ["legacy_flow"], maps, keys, tmp_path, tmp_path
        )


def result_bundle(
    keys: b.Keys, source_bundle: b.Bundle, files: dict[str, object], map_rows_: list | None = None
) -> b.Bundle:
    man = source_bundle.manifest
    members = {name: json.dumps(value).encode() for name, value in files.items()}
    entries = [b.file_entry(name, name.split(".")[0], data_, 0) for name, data_ in members.items()]
    if map_rows_ is not None:
        blob, rows = b.jsonl_bytes(map_rows_)
        members["map.jsonl"] = blob
        entries.append(b.file_entry("map.jsonl", "map", blob, rows))
    manifest = {"run_id": man["run_id"], "brand": man["brand"], "company_codes": man["company_codes"],
                "parent_run_id": man.get("parent_run_id"), "files": entries, "maps": []}  # fmt: skip
    return b.open_bundle(b.build_bundle(manifest, members, keys, kind="result"), keys, "result")


def odoo_totals_from(bundle: b.Bundle) -> dict:
    """Simulasi importer: total per irisan dari baris yang dimuat (kunci & kolom = kontrak README)."""
    agg = aggregate_bundle([bundle])
    names = {"legacy_flow_daily": ("month", "branch_key", "product_key"),
             "legacy_sales_daily": ("month", "branch_key", "menu_key", "is_takeaway"),
             "legacy_sales_line": ("month", "branch_key")}  # fmt: skip
    out: dict = {"bulk_check": {"wrong_company_rows": 0, "foreign_product_rows": 0}}
    for entity, cols in names.items():
        rows = []
        for key, sums in agg[entity].items():
            row = dict(zip(cols, key, strict=True))
            if "is_takeaway" in row:
                row["is_takeaway"] = row["is_takeaway"] == "true"
            rows.append({**row, **sums})
        out[entity] = rows
    return out


def test_reconcile_history_ok_then_mismatch(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    _, segments = history(tmp_path, keys, maps, source)
    bundles = [b.read_bundle(s.path, keys, "bundle") for s in segments]
    report_json = {"dry_run": True, "entities": {}}
    pairs = []
    for bd in bundles:
        files = {"import_report.json": report_json, "odoo_totals.json": odoo_totals_from(bd)}
        pairs.append((bd, result_bundle(keys, bd, files)))
    ok = reconcile(pairs)
    assert ok.blocking == 0
    assert {ln.item for ln in ok.lines} >= {"5a_flow", "5b_sales_daily", "5c_sales_line", "5d_bulk_check"}
    assert any(ln.item == "5_tak_terpetakan" for ln in ok.lines)
    assert "Pemblokir (selisih): **0**" in ok.to_markdown() and ok.to_csv().startswith("butir,")
    totals = odoo_totals_from(bundles[0])
    totals["legacy_sales_line"][0]["rows"] -= 1  # importer kehilangan satu baris
    totals["bulk_check"]["wrong_company_rows"] = 2
    pairs[0] = (bundles[0], result_bundle(keys, bundles[0], {"import_report.json": report_json,
                                                              "odoo_totals.json": totals}))  # fmt: skip
    bad = reconcile(pairs)
    assert bad.blocking == 2


def test_reconcile_master_counts(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    path = export_master(data(source), params(), ["branch", "product"], maps, keys, tmp_path, now=NOW)
    bundle = b.read_bundle(path, keys, "bundle")
    entities = {"branch": {"created": 4}, "product": {"created": 1, "conflict": 1}}
    result = result_bundle(keys, bundle, {"import_report.json": {"dry_run": False, "entities": entities},
                                          "odoo_totals.json": {}})  # fmt: skip
    report = reconcile([(bundle, result)])
    status = {ln.key: ln.status for ln in report.lines if ln.item == "1_jumlah"}
    assert status == {"branch": "ok", "product": "peringatan"}
    entities["branch"] = {"created": 3, "failed": 1}
    result = result_bundle(keys, bundle, {"import_report.json": {"dry_run": False, "entities": entities},
                                          "odoo_totals.json": {}})  # fmt: skip
    assert reconcile([(bundle, result)]).blocking == 1


def test_writeback_rows_and_rejections(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    path = export_master(data(source), params(), ["product"], maps, keys, tmp_path, now=NOW)
    bundle = b.read_bundle(path, keys, "bundle")
    row = {"entity": "product", "company_code": "PB", "esb_key": "RM001", "odoo_model": "product.template",
           "xmlid": "efn_mig_esb.pb_pb_product_rm001", "odoo_id": 17}  # fmt: skip
    files = {"import_report.json": {"dry_run": False}, "odoo_totals.json": {}}
    rows = map_rows(result_bundle(keys, bundle, files, [row]), NOW)
    assert rows[0][:7] == ["pb", "PB", "product", "RM001", "product.template", "efn_mig_esb.pb_pb_product_rm001", 17]
    assert str(rows[0][7]) == bundle.manifest["run_id"]
    with pytest.raises(DryRunResult):
        map_rows(result_bundle(keys, bundle, {**files, "import_report.json": {"dry_run": True}}, [row]), NOW)
    for bad in ({**row, "odoo_id": 0}, {**row, "company_code": "WTZ"}, {**row, "xmlid": "base.x"}, {**row, "x": 1}):
        with pytest.raises(b.IntegrityError):
            map_rows(result_bundle(keys, bundle, files, [bad]), NOW)
