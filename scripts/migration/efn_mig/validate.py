"""Aturan validator bundel ESB (docs/04 §27.7 v19.1). Murni: input = `Bundle` terverifikasi (open_bundle).

Error = memblokir impor; warning = dilaporkan. Pelanggaran integritas segmen (tanpa `segment`/`parent_run_id`, baris di
luar company/bulan segmen, `sales_line_months` di luar 0..24, xmlid tidak sama dengan hasil hitung ulang) ->
`IntegrityError` (exit 3). Pemeriksaan yang butuh Odoo target (company sudah ada, pajak produk lintas PT) dilakukan
importer (efn-odoo-engineer); bundel ESB tidak membawa pajak.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from efn_mig import mapping as m
from efn_mig.bundle import Bundle, IntegrityError, Keys, sign_validation
from efn_mig.segments import MAX_LEGACY_DAYS, MAX_SALES_LINE_MONTHS, add_months, parse_month
from efn_mig.xmlid import xmlid

ENVELOPE = frozenset({"entity", "company_code", "esb_key", "xmlid", "data"})
# Pola kunci identitas orang -- ditolak di mana pun (data, baris bom, histori), fail closed (§27.3 PDP).
PII_KEY = re.compile(r"address|^pic|_pic|phone|email|creator|editor|authorizer|npwp|nik$|ktp|bank|birth|gender", re.I)
XMLID_ENTITY = {"category": "category", "pos_category": "pos_category", "uom": "uom", "product": "product",
                "menu": "menu", "payment_term": "payment_term", "supplier": "supplier", "customer": "customer",
                "bom": "bom"}  # fmt: skip


@dataclass(frozen=True)
class Finding:
    level: str  # error | warning
    code: str
    entity: str
    key: str
    message: str


@dataclass
class Report:
    run_id: str
    manifest_sha256: str
    findings: list[Finding]
    bundle_sha256: str = ""  # sha256 berkas .efnmig (diikat tanda tangan, SEC-201)
    segment_set: dict[str, Any] | None = None  # ringkasan pemeriksaan part sesama parent (SEC-197)

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.level == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.level == "warning")

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "run_id": self.run_id,
            "bundle_sha256": self.bundle_sha256,
            "manifest_sha256": self.manifest_sha256,
            "errors": self.errors,
            "warnings": self.warnings,
            "findings": [asdict(f) for f in self.findings],
        }
        if self.segment_set is not None:
            out["segment_set"] = self.segment_set
        return out

    def to_signed_json(self, keys: Keys) -> dict[str, Any]:
        """Laporan bertanda tangan (kunci HKDF `efn:mig-esb:val:v1`); importer memakai `bundle.verify_validation`."""
        return sign_validation(self.to_json(), keys)

    def to_markdown(self) -> str:
        lines = [
            f"# Validasi bundel {self.run_id}",
            "",
            f"- manifest_sha256: `{self.manifest_sha256}`",
            f"- error: **{self.errors}**, peringatan: {self.warnings}",
            "",
            "| Tingkat | Kode | Entitas | Kunci | Pesan |",
            "|---|---|---|---|---|",
        ]
        for f in self.findings[:2000]:
            lines.append(f"| {f.level} | {f.code} | {f.entity} | `{f.key}` | {f.message} |")
        if len(self.findings) > 2000:
            lines.append(f"| … | … | … | … | {len(self.findings) - 2000} temuan lain di JSON |")
        return "\n".join(lines) + "\n"


class _Collector:
    def __init__(self) -> None:
        self.items: list[Finding] = []

    def error(self, code: str, entity: str, key: Any, message: str) -> None:
        self.items.append(Finding("error", code, entity, str(key)[:128], message))

    def warn(self, code: str, entity: str, key: Any, message: str) -> None:
        self.items.append(Finding("warning", code, entity, str(key)[:128], message))


def _pii_keys(keys: Iterator[str] | list[str]) -> list[str]:
    return [k for k in keys if PII_KEY.search(k)]


def check_segment_set(manifests: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """SEC-197: part sesama (parent_run_id, company, bulan) wajib lengkap 1..parts, `target_warehouses` saling lepas,
    dan gabungannya = `company_warehouses`. Gudang di > 1 part -> IntegrityError (exit 3). Kembalikan ringkasan grup
    (`complete` False bila ada part yang tidak disertakan)."""
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for man in manifests:
        seg = man.get("segment")
        if isinstance(seg, dict):
            groups[(str(man.get("parent_run_id")), str(seg.get("company_code")), str(seg.get("month")))].append(man)
    summary = []
    for (parent, company, month), mans in sorted(groups.items()):
        owner: dict[str, int] = {}
        parts_declared = {int(mn["segment"].get("parts") or 0) for mn in mans}
        if len(parts_declared) != 1:
            raise IntegrityError(f"segmen {company} {month}: jumlah part berbeda antar-bundel")
        parts = parts_declared.pop()
        seen_parts = sorted(int(mn["segment"].get("part") or 0) for mn in mans)
        if len(seen_parts) != len(set(seen_parts)):
            raise IntegrityError(f"segmen {company} {month}: part ganda")
        company_whs = {tuple(mn.get("company_warehouses") or []) for mn in mans}
        if len(company_whs) != 1:
            raise IntegrityError(f"segmen {company} {month}: company_warehouses berbeda antar-part")
        for mn in mans:
            for wh in mn.get("target_warehouses") or []:
                if wh in owner:
                    raise IntegrityError(
                        f"gudang {wh} muncul di part {owner[wh]} dan {mn['segment'].get('part')} (parent {parent})"
                    )
                owner[wh] = int(mn["segment"].get("part") or 0)
        complete = seen_parts == list(range(1, parts + 1))
        if complete and set(owner) != set(next(iter(company_whs))):
            raise IntegrityError(f"segmen {company} {month}: gabungan target_warehouses != company_warehouses")
        summary.append(
            {"parent_run_id": parent, "company_code": company, "month": month, "parts": parts,
             "parts_seen": seen_parts, "complete": complete, "warehouses_disjoint": True}
        )  # fmt: skip
    return summary


def validate_bundle(bundle: Bundle, siblings: Iterable[Mapping[str, Any]] | None = None) -> Report:
    """`siblings` = manifest SEMUA bundel segmen yang divalidasi bersama (termasuk bundel ini). Segmen dengan
    parts > 1 tanpa semua part -> error PARTS_INCOMPLETE (validasi wajib per set part, SEC-197)."""
    man = bundle.manifest
    out = _Collector()
    segment_set: dict[str, Any] | None = None
    if isinstance(man.get("segment"), dict):
        group = [mn for mn in (siblings or [man]) if mn.get("parent_run_id") == man.get("parent_run_id")]
        if not any(mn.get("run_id") == man.get("run_id") for mn in group):
            group.append(man)
        seg = man["segment"]
        same = [mn for mn in group if isinstance(mn.get("segment"), dict)
                and mn["segment"].get("company_code") == seg.get("company_code")
                and mn["segment"].get("month") == seg.get("month")]  # fmt: skip
        summary = check_segment_set(same)
        segment_set = summary[0] if summary else None
        if segment_set is not None and not segment_set["complete"]:
            out.error("PARTS_INCOMPLETE", "manifest", seg.get("month"),
                      "validasi wajib menyertakan semua part company x bulan (SEC-197)")  # fmt: skip
    brand = str(man["brand"])
    companies = [str(c) for c in man.get("company_codes") or []]
    for code in companies:
        if code not in m.COMPANY_CODES:
            out.error("COMPANY_UNKNOWN", "manifest", code, "kode PT bukan §12.1/§27.1")
    legacy_days = man.get("legacy_days")
    if not isinstance(legacy_days, int) or not 1 <= legacy_days <= MAX_LEGACY_DAYS:
        out.error("LEGACY_DAYS", "manifest", legacy_days, f"legacy_days wajib 1..{MAX_LEGACY_DAYS}")
    slm = man.get("sales_line_months")
    if not isinstance(slm, int) or not 0 <= slm <= MAX_SALES_LINE_MONTHS:
        raise IntegrityError("sales_line_months di luar 0..24")
    history_files = [e for e in man.get("files", []) if e.get("entity") in m.HISTORY_KEYS]
    if history_files:
        _validate_history(bundle, history_files, out)
    master = _validate_master(bundle, brand, companies, out)
    for branch in man.get("unmapped_branches") or []:
        if branch.get("active"):
            out.error("BRANCH_UNMAPPED", "branch", branch.get("branch_id"), "cabang aktif tanpa baris branch_map.csv")
    _check_branches(master.get("branch", []), out)
    _check_products_menus(master, out)
    _check_boms(master, out)
    return Report(str(man["run_id"]), bundle.manifest_sha256, out.items, bundle.blob_sha256, segment_set)


def _validate_master(
    bundle: Bundle, brand: str, companies: list[str], out: _Collector
) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    for entry in bundle.manifest.get("files", []):
        entity = str(entry.get("entity"))
        if entity in m.HISTORY_KEYS:
            continue
        if entity not in m.DATA_KEYS:
            out.error("ENTITY_UNKNOWN", entity, entry.get("name"), "entitas tidak dikenal")
            continue
        rows = records.setdefault(entity, [])
        for row in bundle.rows(str(entry["name"])):
            rows.append(row)
            key = row.get("esb_key")
            if set(row) != ENVELOPE or row.get("entity") != entity:
                out.error("ENVELOPE", entity, key, "kunci baris wajib entity/company_code/esb_key/xmlid/data")
                continue
            if row["company_code"] not in companies:
                out.error("COMPANY_SCOPE", entity, key, "company_code di luar manifest.company_codes")
            data = row["data"] if isinstance(row["data"], dict) else {}
            extra = sorted(set(data) - m.DATA_KEYS[entity])
            pii = _pii_keys(list(data)) + [
                k for line in data.get("lines") or [] if isinstance(line, dict) for k in _pii_keys(list(line))
            ]
            if extra or pii:
                bad = ", ".join(sorted(set(extra + pii)))
                out.error("PII_WHITELIST", entity, key, f"kunci di luar whitelist: {bad}")
            expected = _expected_xmlid(brand, entity, row)
            if expected is not None and row["xmlid"] != expected:
                raise IntegrityError(f"xmlid {entity} {key!s:.40} tidak sama dengan hasil hitung ulang (§27.4)")
    return records


def _expected_xmlid(brand: str, entity: str, row: Mapping[str, Any]) -> str | None:
    company = str(row["company_code"])
    if entity == "branch":
        kind = "branch_loc" if (row.get("data") or {}).get("type") == "location" else "branch_wh"
        return xmlid(brand, company, kind, row["esb_key"])
    if entity in XMLID_ENTITY:
        return xmlid(brand, company, XMLID_ENTITY[entity], row["esb_key"])
    return None  # cost: tanpa xmlid


def _check_branches(rows: list[dict[str, Any]], out: _Collector) -> None:
    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        data = row["data"]
        if data.get("type") == "location":
            if not data.get("parent_key"):
                out.error("BRANCH_PARENT", "branch", row["esb_key"], "lokasi tanpa gudang induk di branch_map")
            continue
        code = str(data.get("warehouse_code") or "")
        if not code or len(code) > 5:
            out.error("WAREHOUSE_CODE", "branch", row["esb_key"], "warehouse_code wajib 1..5 karakter")
        key = (row["company_code"], code)
        if code and key in seen:
            out.error("WAREHOUSE_CODE_DUP", "branch", row["esb_key"], f"warehouse_code bentrok dengan {seen[key]}")
        seen.setdefault(key, row["esb_key"])


def _check_products_menus(records: Mapping[str, list[dict[str, Any]]], out: _Collector) -> None:
    for row in records.get("product", []):
        if not row["data"].get("uom_xmlid"):
            out.error("PRODUCT_UOM", "product", row["esb_key"], "produk tanpa UoM / UoM tidak ada di uom_map.csv")
    for row in records.get("uom", []):
        if not row["data"].get("uom_xmlid"):
            out.error("UOM_UNMAPPED", "uom", row["esb_key"], "satuan ESB tidak ada di uom_map.csv")
    for row in records.get("menu", []):
        data = row["data"]
        if data.get("active") and not data.get("alias_of") and (data.get("list_price") or 0) <= 0:
            out.warn("MENU_PRICE", "menu", row["esb_key"], "menu aktif tanpa harga / harga <= 0")
        if data.get("is_takeaway") and not data.get("alias_of"):
            out.warn("MENU_TAKEAWAY_BASE", "menu", row["esb_key"], "menu takeaway tanpa menu dasar")


def _check_boms(records: Mapping[str, list[dict[str, Any]]], out: _Collector) -> None:
    fin = {
        (r["company_code"], r["data"].get("default_code")): bool(r["data"].get("is_fin"))
        for r in records.get("product", [])
    }
    graph: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in records.get("bom", []):
        data, key, company = row["data"], row["esb_key"], row["company_code"]
        lines = data.get("lines") or []
        if not lines:
            out.error("BOM_EMPTY", "bom", key, "BoM tanpa komponen")
        for line in lines:
            if not line.get("product_key"):
                out.error("BOM_COMPONENT_MISSING", "bom", key, f"komponen {line.get('product_code')} tidak ada")
            if (line.get("qty") or 0) <= 0:
                out.error("BOM_QTY", "bom", key, f"qty komponen {line.get('product_code')} <= 0")
        if data.get("type") == "normal":
            if not data.get("product_key"):
                out.error("BOM_PRODUCT_MISSING", "bom", key, "produk hasil BoM tidak ada di bundel")
            if fin.get((company, data.get("product_code"))) and data.get("ratio_source") != "bom_ratio":
                out.error("BOM_NO_RATIO", "bom", key, "FIN diproduksi tanpa rasio BATCH (bom_ratio)")
            if data.get("product_code"):
                graph[(company, str(data["product_code"]))] |= {str(ln.get("product_code")) for ln in lines}
        elif data.get("type") == "phantom" and not data.get("menu_keys"):
            out.error("BOM_PHANTOM_MENU", "bom", key, "BoM kit tanpa menu")
    for company, start in sorted(_cycles(graph)):
        out.error("BOM_CYCLE", "bom", start, f"siklus BoM (company {company})")


def _cycles(graph: Mapping[tuple[str, str], set[str]]) -> set[tuple[str, str]]:
    """Node (company, produk) yang berada di siklus BoM normal."""
    found: set[tuple[str, str]] = set()
    for node in graph:
        company = node[0]
        stack, seen = [node[1]], set()
        while stack:
            current = stack.pop()
            for nxt in graph.get((company, current), ()):
                if nxt == node[1]:
                    found.add(node)
                elif nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
    return found


def _validate_history(bundle: Bundle, entries: list[dict[str, Any]], out: _Collector) -> None:
    man = bundle.manifest
    segment = man.get("segment")
    if not isinstance(segment, dict) or not man.get("parent_run_id"):
        raise IntegrityError("bundel histori tanpa segment/parent_run_id (§27.3a)")
    company = segment.get("company_code")
    if man.get("company_codes") != [company]:
        raise IntegrityError("company_codes bundel histori wajib = segment.company_code")
    month = parse_month(str(segment.get("month")))
    month_end = add_months(month, 1)
    cutover = date.fromisoformat(str(man.get("cutover_date"))) if man.get("cutover_date") else None
    if cutover is None:
        out.error("CUTOVER_MISSING", "manifest", man.get("run_id"), "histori wajib cutover_date")
    # SEC-197: gudang tujuan part ini (importer mengganti irisan HANYA gudang ini, termasuk yang tanpa baris).
    targets = man.get("target_warehouses")
    company_whs = man.get("company_warehouses")
    if not isinstance(targets, list) or not isinstance(company_whs, list) or not set(targets) <= set(company_whs):
        raise IntegrityError("bundel histori tanpa target_warehouses/company_warehouses yang konsisten (SEC-197)")
    if len(targets) != len(set(targets)):
        raise IntegrityError("target_warehouses ganda")
    target_set = set(targets)
    for entry in entries:
        entity = str(entry["entity"])
        required = set(m.HISTORY_KEYS[entity])
        allowed = required | m.HISTORY_OPTIONAL_KEYS[entity]
        day_key = "date" if entity == "legacy_sales_line" else "day"
        dup: set[tuple[Any, ...]] = set()
        unmapped_menu = 0
        for row in bundle.rows(str(entry["name"])):
            if not required <= set(row) <= allowed:
                extra = sorted(set(row) - allowed) + [f"-{k}" for k in sorted(required - set(row))]
                out.error("HISTORY_KEYS", entity, row.get(day_key), f"kunci di luar whitelist / hilang: {extra}")
                continue
            try:
                day = date.fromisoformat(str(row[day_key]))
            except ValueError:
                raise IntegrityError(f"{entity}: tanggal tidak valid") from None
            if not month <= day < month_end:
                raise IntegrityError(f"{entity}: baris di luar segment.month {segment.get('month')}")
            if cutover is not None and day >= cutover:
                out.error("HISTORY_AFTER_CUTOVER", entity, row[day_key], "hari histori >= cutover_date")
            if row["branch_key"] not in target_set:
                raise IntegrityError(f"{entity}: baris untuk gudang di luar target_warehouses part ini (SEC-197)")
            agg_keys = m.AGGREGATE_KEYS.get(entity)
            if agg_keys is not None:
                key = tuple(row[k] for k in agg_keys)
                if key in dup:
                    out.error("HISTORY_KEY_DUP", entity, row[day_key], f"kunci model ganda {agg_keys}")
                dup.add(key)
            if entity == "legacy_sales_line":
                if row["row_type"] not in m.ROW_TYPES:
                    out.error("ROW_TYPE", entity, row["sales_num"], f"row_type tidak dikenal: {row['row_type']}")
                key = (row["branch_key"], row["date"], row["sales_num"], row["line_no"])
                if key in dup:
                    out.error("SALES_LINE_DUP", entity, row["sales_num"], "duplikat (sales_num, line_no) cabang-hari")
                dup.add(key)
            if entity != "legacy_flow_daily" and not row.get("menu_key"):
                unmapped_menu += 1
        if unmapped_menu:
            out.warn("MENU_UNMAPPED", entity, unmapped_menu, "baris dengan menu tanpa xmlid (dihitung di rekon 5b/5c)")
        skipped = (man.get("stats") or {}).get(entity, {}).get("rows_skipped_unmapped", 0)
        if skipped:
            out.warn("ROWS_UNMAPPED", entity, skipped, "baris sumber tak terpetakan (cabang/produk), tidak diekspor")
