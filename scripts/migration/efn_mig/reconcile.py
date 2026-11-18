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
from typing import Any

from efn_mig.bundle import Bundle
from efn_mig.mapping import FLOW_COLUMNS
from efn_mig.results import json_member

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


def aggregate_bundle(bundles: Iterable[Bundle]) -> dict[str, dict[tuple[str, ...], dict[str, float]]]:
    """Agregat sisi sumber dari isi bundel histori, kunci sama dengan `odoo_totals.json`."""
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
    source = aggregate_bundle(b for b, _ in history)
    merged: dict[str, list[Any]] = defaultdict(list)
    bulk = {"wrong_company_rows": 0, "foreign_product_rows": 0}
    for _, result in history:
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
    for key, value in bulk.items():
        report.lines.append(Line("5d_bulk_check", key, 0, value, value, "ok" if value == 0 else "selisih"))
    for bundle, _ in history:
        for entity, stats in (bundle.manifest.get("stats") or {}).items():
            skipped = int(stats.get("rows_skipped_unmapped", 0))
            if skipped:
                seg = bundle.manifest["segment"]
                key = f"{seg['company_code']}|{seg['month']}|p{seg['part']}|{entity}"
                report.lines.append(Line("5_tak_terpetakan", key, skipped, 0, skipped, "info"))


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
