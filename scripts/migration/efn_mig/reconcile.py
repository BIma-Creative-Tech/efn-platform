"""Laporan rekonsiliasi 7 butir (docs/04 §27.5 v19.1). Murni: bundel sumber + hasil impor (+ data sumber opsional).

Sisi "sumber" = isi bundel (snapshot ClickHouse yang sama dengan yang diimpor) + statistik baris tak terpetakan di
manifest; sisi "Odoo" = `import_report.json` + `odoo_totals.json` dari importer. Butir 2-4 (stok, AR/AP, neraca)
belum berlaku selama langkah `stock`/`opening` default mati -> dilaporkan "tidak dijalankan".
Toleransi: qty 0,001; uang 0,01 (pembulatan float JSON).
"""

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from efn_mig.bundle import Bundle
from efn_mig.mapping import FLOW_COLUMNS
from efn_mig.results import json_member
from efn_mig.segments import add_months, month_label, month_start
from efn_mig.validate import check_segment_set

QTY_TOL = 0.001
MONEY_TOL = 0.01
SALES_DAILY_SUMS = ("qty", "gross", "discount", "net", "cogs", "trx_count")
SALES_LINE_SUMS = ("rows", "qty", "total", "discount", "cogs")
ENTITY_STATUSES = ("created", "updated", "skipped", "conflict", "failed")
# Rasio net penjualan / pembayaran (pembayaran memuat PB1 + SC): PB ±89,6 % (G41). Di luar rentang -> peringatan.
SANITY_RANGE = (0.80, 0.98)


@dataclass
class Line:
    item: str
    key: str
    source: float | str
    odoo: float | str
    diff: float | str
    status: str  # ok | selisih | peringatan | info | tidak_dijalankan


@dataclass
class ReconReport:
    parent_run_id: str
    lines: list[Line] = field(default_factory=list)

    @property
    def blocking(self) -> int:
        return sum(1 for ln in self.lines if ln.status == "selisih")

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(["butir", "kunci", "sumber", "odoo", "selisih", "status"])
        for ln in self.lines:
            writer.writerow([ln.item, ln.key, ln.source, ln.odoo, ln.diff, ln.status])
        return buf.getvalue()

    def to_markdown(self) -> str:
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for ln in self.lines:
            counts[ln.item][ln.status] += 1
        out = [
            f"# Rekonsiliasi migrasi ESB -> Odoo ({self.parent_run_id})",
            "",
            f"Pemblokir (selisih): **{self.blocking}**. Rincian per baris: CSV pendamping. Tanpa data pribadi.",
            "",
            "| Butir | ok | selisih | peringatan | info | tidak dijalankan |",
            "|---|---|---|---|---|---|",
        ]
        for item in sorted(counts):
            c = counts[item]
            out.append(
                f"| {item} | {c['ok']} | {c['selisih']} | {c['peringatan']} | {c['info']} | {c['tidak_dijalankan']} |"
            )
        problems = [ln for ln in self.lines if ln.status in ("selisih", "peringatan")][:200]
        if problems:
            out += ["", "## Selisih & peringatan (maks 200)", "", "| Butir | Kunci | Sumber | Odoo | Selisih |"]
            out.append("|---|---|---|---|---|")
            out += [f"| {ln.item} | `{ln.key}` | {ln.source} | {ln.odoo} | {ln.diff} |" for ln in problems]
        return "\n".join(out) + "\n"


def _cmp(report: ReconReport, item: str, key: str, source: float, odoo: float, tol: float) -> None:
    diff = round(odoo - source, 6)
    status = "ok" if abs(diff) <= tol else "selisih"
    report.lines.append(Line(item, key, round(source, 6), round(odoo, 6), diff, status))


def _month(day: str) -> str:
    return str(day)[:7]


def aggregate_bundle(
    bundles: Iterable[Bundle], skip_sales_line_runs: frozenset[str] = frozenset()
) -> dict[str, dict[tuple[str, ...], dict[str, float]]]:
    """Agregat sisi sumber dari isi bundel histori, kunci sama dengan `odoo_totals.json`.

    `skip_sales_line_runs`: run_id segmen yang `legacy_sales_line`-nya sengaja tidak dimuat importer
    (`skipped_by_param`, parameter `efn_mig_esb.sales_line_months` = 0) -> tidak dihitung di sisi sumber."""
    agg: dict[str, dict[tuple[str, ...], dict[str, float]]] = {
        "legacy_flow_daily": defaultdict(lambda: defaultdict(float)),
        "legacy_sales_daily": defaultdict(lambda: defaultdict(float)),
        "legacy_sales_line": defaultdict(lambda: defaultdict(float)),
    }
    for bundle in bundles:
        for entry in bundle.manifest.get("files", []):
            entity, name = entry.get("entity"), str(entry.get("name"))
            if entity == "legacy_flow_daily":
                for r in bundle.rows(name):
                    bucket = agg[entity][(_month(r["day"]), r["branch_key"], r["product_key"])]
                    for col in (*FLOW_COLUMNS, "outflow"):
                        bucket[col] += float(r[col])
            elif entity == "legacy_sales_daily":
                for r in bundle.rows(name):
                    takeaway = str(bool(r["is_takeaway"])).lower()
                    bucket = agg[entity][(_month(r["day"]), r["branch_key"], str(r["menu_key"]), takeaway)]
                    for col in SALES_DAILY_SUMS:
                        bucket[col] += float(r[col])
            elif entity == "legacy_sales_line":
                if str(bundle.manifest.get("run_id")) in skip_sales_line_runs:
                    continue
                for r in bundle.rows(name):
                    bucket = agg[entity][(_month(r["date"]), r["branch_key"])]
                    bucket["rows"] += 1
                    for col in ("qty", "total", "discount", "cogs"):
                        bucket[col] += float(r[col])
    return agg


def _odoo_totals(totals: Mapping[str, Any]) -> dict[str, dict[tuple[str, ...], dict[str, float]]]:
    out: dict[str, dict[tuple[str, ...], dict[str, float]]] = {}
    keyfn = {
        "legacy_flow_daily": lambda r: (r["month"], r["branch_key"], r["product_key"]),
        "legacy_sales_daily": lambda r: (r["month"], r["branch_key"], str(r["menu_key"]),
                                         str(bool(r["is_takeaway"])).lower()),
        "legacy_sales_line": lambda r: (r["month"], r["branch_key"]),
    }  # fmt: skip
    for entity, fn in keyfn.items():
        bucket: dict[tuple[str, ...], dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for row in totals.get(entity) or []:
            for col, value in row.items():
                if isinstance(value, int | float) and not isinstance(value, bool):
                    bucket[fn(row)][col] += float(value)
        out[entity] = bucket
    return out


def reconcile(
    pairs: list[tuple[Bundle, Bundle]],
    sales_payments: list[Mapping[str, Any]] | None = None,
    data_quality: list[Mapping[str, Any]] | None = None,
) -> ReconReport:
    """`pairs` = (bundel sumber, bundel hasil) dengan run_id sama; semua satu `parent_run_id` (atau satu master)."""
    if not pairs:
        raise ValueError("minimal satu pasangan bundel/hasil")
    parent = str(pairs[0][0].manifest.get("parent_run_id") or pairs[0][0].manifest["run_id"])
    report = ReconReport(parent)
    for bundle, result in pairs:
        if bundle.manifest["run_id"] != result.manifest["run_id"]:
            raise ValueError("run_id bundel dan hasil tidak sama")
    _item1_counts(report, pairs)
    for item in ("2_stok", "3_ar_ap", "4_neraca"):
        report.lines.append(Line(item, "-", "-", "-", "-", "tidak_dijalankan"))
    _item5(report, pairs)
    _item6(report, sales_payments)
    for row in data_quality or []:
        report.lines.append(
            Line("7_kualitas_data", f"{row.get('check_name')}:{row.get('item')}", str(row.get("value")), "-", "-",
                 "peringatan")
        )  # fmt: skip
    return report


def _item1_counts(report: ReconReport, pairs: list[tuple[Bundle, Bundle]]) -> None:
    for bundle, result in pairs:
        if any(e.get("entity", "").startswith("legacy_") for e in bundle.manifest.get("files", [])):
            continue  # histori: butir 5
        entities = json_member(result, "import_report.json").get("entities") or {}
        for entry in bundle.manifest.get("files", []):
            entity = str(entry["entity"])
            got = entities.get(entity) or {}
            handled = sum(int(got.get(s, 0)) for s in ENTITY_STATUSES)
            failed = int(got.get("failed", 0))
            conflict = int(got.get("conflict", 0))
            status = "selisih" if failed or handled != int(entry["rows"]) else ("peringatan" if conflict else "ok")
            detail = ", ".join(f"{s}={int(got.get(s, 0))}" for s in ENTITY_STATUSES)
            report.lines.append(Line("1_jumlah", entity, int(entry["rows"]), handled, detail, status))


def _item5(report: ReconReport, pairs: list[tuple[Bundle, Bundle]]) -> None:
    history = [(b, r) for b, r in pairs if b.manifest.get("segment")]
    if not history:
        return
    # Baris `legacy_sales_line` yang SENGAJA tidak dimuat (docs/04 §27.5 (5): parameter 0 atau di luar jendela
    # `sales_line_months`) dilaporkan importer (`skipped_by_param`, `rows_skipped_window`) -> bukan selisih pemblokir.
    param_skipped: set[str] = set()
    window_skipped: set[tuple[str, str]] = set()
    for bundle, result in history:
        imported = json_member(result, "import_report.json")
        if (imported.get("skipped_by_param") or {}).get("legacy_sales_line"):
            param_skipped.add(str(bundle.manifest["run_id"]))
        for sl in imported.get("slices") or []:
            if sl.get("model") == "efn.legacy.sales.line" and int(sl.get("rows_skipped_window") or 0) > 0:
                window_skipped.add((str(sl.get("month")), str(sl.get("branch_key"))))
    source = aggregate_bundle((b for b, _ in history), frozenset(param_skipped))
    merged: dict[str, list[Any]] = defaultdict(list)
    bulk = {"wrong_company_rows": 0, "foreign_product_rows": 0}
    for bundle, result in history:
        if str(bundle.manifest["run_id"]) in param_skipped:
            seg = bundle.manifest["segment"]
            skipped_rows = sum(
                int(e.get("rows", 0))
                for e in bundle.manifest.get("files", [])
                if e.get("entity") == "legacy_sales_line"
            )
            key = f"{seg['company_code']}|{seg['month']}|p{seg['part']}"
            report.lines.append(Line("5c_dilewati_param", key, skipped_rows, 0, skipped_rows, "info"))
        totals = json_member(result, "odoo_totals.json")
        for entity in source:
            merged[entity] += list(totals.get(entity) or [])
        for key in bulk:
            bulk[key] += int((totals.get("bulk_check") or {}).get(key, 0))
    odoo = _odoo_totals(merged)
    specs = {
        "legacy_flow_daily": ("5a_flow", (*FLOW_COLUMNS, "outflow")),
        "legacy_sales_daily": ("5b_sales_daily", SALES_DAILY_SUMS),
        "legacy_sales_line": ("5c_sales_line", SALES_LINE_SUMS),
    }
    for entity, (item, cols) in specs.items():
        for key in sorted(set(source[entity]) | set(odoo[entity])):
            src, dst = source[entity].get(key, {}), odoo[entity].get(key, {})
            for col in cols:
                tol = QTY_TOL if col in (*FLOW_COLUMNS, "outflow", "qty", "rows", "trx_count") else MONEY_TOL
                _cmp(report, item, "|".join(key) + f"|{col}", src.get(col, 0.0), dst.get(col, 0.0), tol)
                if entity == "legacy_sales_line" and tuple(key[:2]) in window_skipped:
                    last = report.lines[-1]
                    if last.status == "selisih":  # baris di luar jendela sales_line_months: peringatan, bukan pemblokir
                        last.status = "peringatan"
    for key, value in bulk.items():
        report.lines.append(Line("5d_bulk_check", key, 0, value, value, "ok" if value == 0 else "selisih"))
    for bundle, _ in history:
        for entity, stats in (bundle.manifest.get("stats") or {}).items():
            skipped = int(stats.get("rows_skipped_unmapped", 0))
            if skipped:
                seg = bundle.manifest["segment"]
                key = f"{seg['company_code']}|{seg['month']}|p{seg['part']}|{entity}"
                report.lines.append(Line("5_tak_terpetakan", key, skipped, 0, skipped, "info"))


TOTALS_STEP = "totals"
HISTORY_SPECS = {
    "legacy_flow_daily": ("5a_flow", (*FLOW_COLUMNS, "outflow")),
    "legacy_sales_daily": ("5b_sales_daily", SALES_DAILY_SUMS),
    "legacy_sales_line": ("5c_sales_line", SALES_LINE_SUMS),
}


def reconcile_final(parent_run_id: str, sources: list[Bundle], totals_results: list[Bundle]) -> ReconReport:
    """Rekonsiliasi FINAL lintas segmen (SEC-197 rekomendasi 3): agregat SEMUA bundel segmen `parent_run_id` vs total
    yang dihitung importer mode `totals` dari tabel Odoo SETELAH semua part dimuat (bukan penjumlahan odoo_totals
    per part). Bundel hasil totals: kind=result, `steps == ["totals"]`, `parent_run_id` sama, berkas `odoo_totals.json`
    (format sama dengan hasil per segmen; opsional `sales_line_months_effective` = nilai efektif parameter Odoo).
    Part yang hilang (company x bulan tidak lengkap) = selisih pemblokir."""
    if not sources or not totals_results:
        raise ValueError("reconcile --final butuh bundel segmen dan hasil totals")
    for bundle in sources:
        if str(bundle.manifest.get("parent_run_id")) != parent_run_id or not bundle.manifest.get("segment"):
            raise ValueError("semua bundel wajib segmen histori dengan parent_run_id yang diminta")
    for result in totals_results:
        man = result.manifest
        if man.get("kind") != "result" or man.get("steps") != [TOTALS_STEP]:
            raise ValueError('hasil totals wajib kind=result dan steps=["totals"]')
        if str(man.get("parent_run_id")) != parent_run_id:
            raise ValueError("hasil totals untuk parent_run_id lain")
    report = ReconReport(parent_run_id)
    for group in check_segment_set(b.manifest for b in sources):
        status = "ok" if group["complete"] else "selisih"
        key = f"{group['company_code']}|{group['month']}"
        report.lines.append(Line("5_kelengkapan_part", key, group["parts"], len(group["parts_seen"]), "-", status))
    merged: dict[str, list[Any]] = defaultdict(list)
    seen: dict[tuple[str, tuple[str, ...]], int] = {}
    bulk = {"wrong_company_rows": 0, "foreign_product_rows": 0}
    effective: set[int] = set()
    keyfn = {
        "legacy_flow_daily": lambda r: (r["month"], r["branch_key"], r["product_key"]),
        "legacy_sales_daily": lambda r: (r["month"], r["branch_key"], str(r["menu_key"]), str(bool(r["is_takeaway"]))),
        "legacy_sales_line": lambda r: (r["month"], r["branch_key"]),
    }
    for index, result in enumerate(totals_results):
        totals = json_member(result, "odoo_totals.json")
        for entity in HISTORY_SPECS:
            for row in totals.get(entity) or []:
                key = (entity, keyfn[entity](row))
                if seen.setdefault(key, index) != index:
                    raise ValueError("hasil totals tumpang tindih (kunci sama di dua hasil)")
                merged[entity].append(row)
        for key in bulk:
            bulk[key] += int((totals.get("bulk_check") or {}).get(key, 0))
        if "sales_line_months_effective" in totals:
            effective.add(int(totals["sales_line_months_effective"]))
    if len(effective) > 1:
        raise ValueError("sales_line_months_effective berbeda antar-hasil totals")
    skip_runs: set[str] = set()
    if effective:
        months = effective.pop()
        for bundle in sources:
            cutover = bundle.manifest.get("cutover_date")
            start = add_months(month_start(date.fromisoformat(str(cutover)[:10])), -months) if cutover else None
            seg_month = str(bundle.manifest["segment"]["month"])
            if months == 0 or (start is not None and seg_month < month_label(start)):
                skip_runs.add(str(bundle.manifest["run_id"]))
    source = aggregate_bundle(sources, frozenset(skip_runs))
    _compare_history(report, source, _odoo_totals(merged))
    for key, value in bulk.items():
        report.lines.append(Line("5d_bulk_check", key, 0, value, value, "ok" if value == 0 else "selisih"))
    for bundle in sources:
        for entity, stats in (bundle.manifest.get("stats") or {}).items():
            skipped = int(stats.get("rows_skipped_unmapped", 0))
            if skipped:
                seg = bundle.manifest["segment"]
                key = f"{seg['company_code']}|{seg['month']}|p{seg['part']}|{entity}"
                report.lines.append(Line("5_tak_terpetakan", key, skipped, 0, skipped, "info"))
    return report


def _compare_history(
    report: ReconReport,
    source: Mapping[str, Mapping[tuple[str, ...], Mapping[str, float]]],
    odoo: Mapping[str, Mapping[tuple[str, ...], Mapping[str, float]]],
) -> None:
    for entity, (item, cols) in HISTORY_SPECS.items():
        for key in sorted(set(source[entity]) | set(odoo[entity])):
            src, dst = source[entity].get(key, {}), odoo[entity].get(key, {})
            for col in cols:
                tol = QTY_TOL if col in (*FLOW_COLUMNS, "outflow", "qty", "rows", "trx_count") else MONEY_TOL
                _cmp(report, item, "|".join(key) + f"|{col}", src.get(col, 0.0), dst.get(col, 0.0), tol)


def _item6(report: ReconReport, rows: list[Mapping[str, Any]] | None) -> None:
    if rows is None:
        report.lines.append(Line("6_sanity", "-", "-", "-", "-", "tidak_dijalankan"))
        return
    low, high = SANITY_RANGE
    for row in rows:
        net, paid = float(row.get("net") or 0), float(row.get("amount") or 0)
        ratio = net / paid if paid else 0.0
        key = f"{str(row.get('month'))[:7]}|{row.get('branch_id')}"
        status = "ok" if low <= ratio <= high else "peringatan"
        report.lines.append(Line("6_sanity", key, round(net, 2), round(paid, 2), round(ratio, 4), status))
