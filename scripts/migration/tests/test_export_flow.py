"""Alur export -> validasi -> (hasil impor sintetis) -> reconcile / writeback, dengan sumber ClickHouse palsu."""

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from conftest import FakeSource, flow, sales_daily, sales_line

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
from efn_mig.reconcile import aggregate_bundle, reconcile, reconcile_final
from efn_mig.results import DryRunResult, map_rows
from efn_mig.validate import check_segment_set, validate_bundle

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
    assert expand_steps(["history", "legacy_flow"]) == [
        "legacy_flow", "legacy_sales_daily", "legacy_sales_line", "legacy_purchase_line"
    ]  # fmt: skip
    assert expand_steps(["cutover"])[-2:] == ["stock", "cutover-param"]
    assert expand_steps(["stock"]) == ["stock"]  # G65: stock dibangun
    for bad in (["opening"], ["cutover", "opening"], ["apa"]):
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


def history(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource, only_rows: bool = True, **kw: object
) -> tuple[str, list]:
    """Ekspor histori; `only_rows` = hanya segmen berbaris (segmen bulan kosong tetap dibuat untuk pembersihan)."""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    ids = iter(str(uuid.UUID(int=i + 1)) for i in range(1000))
    parent, segments = export_history(
        source, data(source), params(**kw), expand_steps(["history"]), maps, keys, tmp_path, work, now=NOW,
        parent_run_id=str(uuid.UUID(int=999)), run_id_factory=lambda: next(ids),
    )  # fmt: skip
    return parent, [s for s in segments if sum(s.rows.values())] if only_rows else segments


WH = {n: f"efn_mig_esb.pb_pb_branch_wh_{n}" for n in (4, 11, 12)}


def test_history_segments_split_per_warehouse_and_validate(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    parent, everything = history(tmp_path, keys, maps, source, only_rows=False)
    segments = [s for s in everything if sum(s.rows.values())]
    assert [(s.month, s.part, s.parts) for s in segments] == [
        ("2026-09", 1, 2), ("2026-09", 2, 2), ("2026-10", 1, 2), ("2026-10", 2, 2)
    ]  # fmt: skip
    assert segments[0].path.name == f"{parent}_PB_202609_p1.efnmig"
    assert segments[0].skipped["legacy_flow_daily"] == 3  # NAME:ROTI X tak terpetakan
    opened_all = [b.read_bundle(seg.path, keys, "bundle") for seg in everything]
    manifests = [o.manifest for o in opened_all]
    for opened in opened_all:
        man = opened.manifest
        assert man["parent_run_id"] == parent and man["company_warehouses"] == sorted(WH.values())
        assert all(f["name"].endswith(".jsonl.gz") for f in man["files"])
        report = validate_bundle(opened, manifests)
        assert report.errors == 0, report.findings
        assert {f.code for f in report.findings} <= {"ROWS_UNMAPPED"}
        assert report.segment_set is not None and report.segment_set["complete"]
    sep1 = opened_all[[s.path for s in everything].index(segments[0].path)].manifest
    # Gudang induk 4 (+ lokasi 30) dan gudang tanpa baris tetap terdaftar di tepat satu part.
    assert sep1["target_warehouses"] == sorted([WH[11], WH[4]]) and sep1["branch_ids"] == [4, 11, 30]
    empty = [o.manifest for o in opened_all if not any(f["rows"] for f in o.manifest["files"])]
    assert empty and all(mn["target_warehouses"] == sorted(WH.values()) for mn in empty)  # bulan kosong: bersihkan
    assert not list((tmp_path / "work").iterdir())  # plaintext sementara dihapus


def test_sec197_location_rows_aggregate_into_parent_warehouse(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    """Cabang lokasi 30 -> gudang induk 4: baris 4 dan 30 jatuh di part yang sama dan dijumlahkan per kunci model."""
    day = date(2026, 10, 2)
    source.history = {"legacy_flow_daily": [flow(day, 4, "FIN-001", 5, 0, 0), flow(day, 30, "FIN-001", 7, 0, 1)],
                      "legacy_sales_daily": [sales_daily(day, 4, "M01T"), sales_daily(day, 30, "M01T")],
                      "legacy_sales_line": [sales_line(day, 4, 1, "M01T")]}  # fmt: skip
    source.history["legacy_sales_line"].append({**sales_line(day, 30, 1, "M01T"), "sales_num": "LOC30"})
    for i in range(20):  # gudang 11 besar -> segmen dipecah, gudang 4 tetap utuh
        source.history["legacy_sales_line"].append({**sales_line(day, 11, i + 1, "M01T"), "sales_num": "BIG"})
    _, segments = history(tmp_path, keys, maps, source)
    october = [b.read_bundle(s.path, keys, "bundle") for s in segments if s.month == "2026-10"]
    owners = [o for o in october if WH[4] in o.manifest["target_warehouses"]]
    assert len(owners) == 1 and len(october) == 2
    rows = {f["entity"]: list(owners[0].rows(f["name"])) for f in owners[0].manifest["files"]}
    (flow_row,) = rows["legacy_flow_daily"]
    assert flow_row["branch_key"] == WH[4] and flow_row["sold"] == 12 and flow_row["outflow"] == 13
    (daily,) = rows["legacy_sales_daily"]
    assert daily["qty"] == 6 and daily["net"] == 140000 and daily["trx_count"] == 4
    assert daily["menu_name"] == "Roti Tawar T" and daily["menu_category"] == "Bakery"
    assert flow_row["esb_product_name"] == "FIN Roti Tawar"
    assert {r["sales_num"] for r in rows["legacy_sales_line"]} == {"S41002", "LOC30"}
    check_segment_set([o.manifest for o in october])  # tidak tumpang tindih


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


def odoo_totals_from(bundle: b.Bundle | list[b.Bundle]) -> dict:
    """Simulasi importer: total per irisan dari baris yang dimuat (kunci & kolom = kontrak README)."""
    agg = aggregate_bundle(bundle if isinstance(bundle, list) else [bundle])
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


def totals_result(keys: b.Keys, parent: str, totals: dict, steps: list[str] | None = None) -> b.Bundle:
    """Hasil importer mode `totals` (kontrak README): kind=result, steps=["totals"], parent_run_id = induk ekspor."""
    blob = json.dumps(totals).encode()
    manifest = {"run_id": str(uuid.uuid4()), "brand": "pb", "company_codes": ["PB"], "parent_run_id": parent,
                "steps": steps or ["totals"], "files": [b.file_entry("odoo_totals.json", "odoo_totals", blob, 0)],
                "maps": []}  # fmt: skip
    return b.open_bundle(b.build_bundle(manifest, {"odoo_totals.json": blob}, keys, kind="result"), keys, "result")


def test_reconcile_final_across_all_parts(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> None:
    parent, everything = history(tmp_path, keys, maps, source, only_rows=False)
    sources = [b.read_bundle(s.path, keys, "bundle") for s in everything]
    ok = reconcile_final(parent, sources, [totals_result(keys, parent, odoo_totals_from(sources))])
    assert ok.blocking == 0
    assert any(ln.item == "5_kelengkapan_part" and ln.status == "ok" for ln in ok.lines)
    # Part hilang (mis. part 2 Sep tidak diekspor/diimpor) -> kelengkapan + selisih total = pemblokir.
    missing = [s for s in sources if not (s.manifest["segment"]["month"] == "2026-09"
                                          and s.manifest["segment"]["part"] == 2)]  # fmt: skip
    bad = reconcile_final(parent, missing, [totals_result(keys, parent, odoo_totals_from(sources))])
    assert any(ln.item == "5_kelengkapan_part" and ln.status == "selisih" for ln in bad.lines)
    assert bad.blocking > 1
    # Parameter Odoo sales_line_months = 0 -> baris sumber tidak dihitung, Odoo kosong -> bukan selisih.
    no_lines = {**odoo_totals_from(sources), "legacy_sales_line": [], "sales_line_months_effective": 0}
    assert reconcile_final(parent, sources, [totals_result(keys, parent, no_lines)]).blocking == 0
    with pytest.raises(ValueError, match="totals"):
        reconcile_final(parent, sources, [totals_result(keys, parent, no_lines, steps=["history"])])
    with pytest.raises(ValueError, match="parent_run_id"):
        reconcile_final(str(uuid.uuid4()), sources, [totals_result(keys, parent, no_lines)])
    dup = totals_result(keys, parent, odoo_totals_from(sources))
    with pytest.raises(ValueError, match="tumpang tindih"):
        reconcile_final(parent, sources, [dup, totals_result(keys, parent, odoo_totals_from(sources))])


def _history_pairs(tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource) -> tuple[list, list]:
    _, segments = history(tmp_path, keys, maps, source)
    bundles = [b.read_bundle(s.path, keys, "bundle") for s in segments]
    totals = [odoo_totals_from(bd) for bd in bundles]
    return bundles, totals


def _pair(keys: b.Keys, bundle: b.Bundle, report_json: dict, totals: dict) -> tuple[b.Bundle, b.Bundle]:
    files = {"import_report.json": report_json, "odoo_totals.json": totals}
    return bundle, result_bundle(keys, bundle, files)


def test_reconcile_flags_odoo_only_rows_and_takeaway_flip(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    """Kasus negatif: baris ada di Odoo tapi tidak di sumber, dan flag takeaway terbalik = selisih pemblokir."""
    bundles, totals = _history_pairs(tmp_path, keys, maps, source)
    report_json = {"dry_run": False, "entities": {}}
    extra = dict(totals[0]["legacy_sales_daily"][0], menu_key="efn_mig_esb.pb_pb_menu_zz", is_takeaway=False)
    totals[0]["legacy_sales_daily"].append(extra)
    pairs = [_pair(keys, bd, report_json, t) for bd, t in zip(bundles, totals, strict=True)]
    report = reconcile(pairs)
    assert report.blocking > 0
    assert any(ln.item == "5b_sales_daily" and "menu_zz" in ln.key and ln.status == "selisih" for ln in report.lines)
    totals[0]["legacy_sales_daily"].pop()
    totals[0]["legacy_sales_daily"][0]["is_takeaway"] = not totals[0]["legacy_sales_daily"][0]["is_takeaway"]
    pairs = [_pair(keys, bd, report_json, t) for bd, t in zip(bundles, totals, strict=True)]
    assert reconcile(pairs).blocking > 0  # kunci (bulan|cabang|menu|takeaway) tidak cocok di kedua sisi


def test_reconcile_sales_line_skipped_by_param_is_info_not_blocking(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    """Parameter `sales_line_months` = 0 di Odoo: importer melaporkan skipped_by_param -> bukan selisih (§27.5 (5c))."""
    bundles, totals = _history_pairs(tmp_path, keys, maps, source)
    report_json = {"dry_run": False, "entities": {}, "skipped_by_param": {"legacy_sales_line": 20}}
    pairs = []
    for bd, t in zip(bundles, totals, strict=True):
        t["legacy_sales_line"] = []  # importer tidak memuat apa pun
        pairs.append(_pair(keys, bd, report_json, t))
    report = reconcile(pairs)
    assert report.blocking == 0
    assert not any(ln.item == "5c_sales_line" for ln in report.lines)
    assert sum(1 for ln in report.lines if ln.item == "5c_dilewati_param") == len(bundles)
    # Tanpa laporan skipped_by_param, baris yang sama = selisih pemblokir (importer kehilangan semua baris).
    plain = [_pair(keys, bd, {"dry_run": False, "entities": {}}, t) for bd, t in zip(bundles, totals, strict=True)]
    assert reconcile(plain).blocking > 0


def test_reconcile_sales_line_outside_window_is_warning(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    bundles, totals = _history_pairs(tmp_path, keys, maps, source)
    first = totals[0]["legacy_sales_line"][0]
    first["rows"] -= 2
    slices = [{"model": "efn.legacy.sales.line", "month": first["month"], "branch_key": first["branch_key"],
               "rows_skipped_window": 2}]  # fmt: skip
    pairs = [_pair(keys, bundles[0], {"dry_run": False, "entities": {}, "slices": slices}, totals[0])]
    clean = {"dry_run": False, "entities": {}}
    pairs += [_pair(keys, bd, clean, t) for bd, t in zip(bundles[1:], totals[1:], strict=True)]
    report = reconcile(pairs)
    assert report.blocking == 0
    assert any(ln.item == "5c_sales_line" and ln.status == "peringatan" for ln in report.lines)
    # Selisih di irisan lain yang TIDAK dilaporkan skip tetap pemblokir.
    other = totals[1]["legacy_sales_line"][0]
    other["rows"] -= 1
    pairs[1] = _pair(keys, bundles[1], {"dry_run": False, "entities": {}}, totals[1])
    assert reconcile(pairs).blocking == 1


def test_export_refuses_blocked_companies_imd_cup_by_default(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    """IMD/CUP: histori/saldo ditolak (exit 4) sampai Q-ESB5; default RunParams = EFN_MIG_SALDO_BLOCKED."""
    for code in ("IMD", "CUP"):

        class OtherSource(FakeSource):
            company = code

            def companies(self, brand: str) -> list[str]:
                return [self.company]

        src = OtherSource()
        default_params = RunParams(brand="pb", cutover=CUTOVER, legacy_days=400, sales_line_months=24)
        assert default_params.blocked == frozenset({"IMD", "CUP"})
        with pytest.raises(GateError, match=code):
            export_history(src, data(src), default_params, ["legacy_flow"], maps, keys, tmp_path, tmp_path)


def test_writeback_requires_explicit_dry_run_false(
    tmp_path: Path, keys: b.Keys, maps: Maps, source: FakeSource
) -> None:
    """Hasil tanpa flag / flag bukan boolean False (mis. string) tidak boleh di-writeback (fail closed, exit 4)."""
    path = export_master(data(source), params(), ["product"], maps, keys, tmp_path, now=NOW)
    bundle = b.read_bundle(path, keys, "bundle")
    row = {"entity": "product", "company_code": "PB", "esb_key": "RM001", "odoo_model": "product.template",
           "xmlid": "efn_mig_esb.pb_pb_product_rm001", "odoo_id": 17}  # fmt: skip
    for flag in ({}, {"dry_run": "false"}, {"dry_run": None}, {"dry_run": 0}):
        files = {"import_report.json": flag, "odoo_totals.json": {}}
        with pytest.raises(DryRunResult):
            map_rows(result_bundle(keys, bundle, files, [row]), NOW)


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
