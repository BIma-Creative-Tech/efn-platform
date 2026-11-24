"""Pemetaan baris ClickHouse (dict nama kolom -> nilai) ke baris JSONL bundel (docs/04 §27.3/§27.4). Murni.

Format baris master: `{"entity", "company_code", "esb_key", "xmlid", "data"}` -- `data` hanya field milik migrasi
(§27.4). Format baris histori: objek datar (kunci tetap per berkas, `HISTORY_KEYS`); company = `segment.company_code`.
Kontrak lengkap (kolom, satuan) = `scripts/migration/README.md`.
"""

import csv
import hashlib
import io
import re
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from typing import Any

from efn_mig.category_roles import SEP, CategoryRole, CategoryRoleError, load_roles, path_chain, role_for
from efn_mig.xmlid import xmlid

COMPANY_CODES = frozenset({"EFN", "GL", "PB", "BCR", "BCF", "WTZ", "FARM", "IMD", "CUP"})
BRANCH_TYPES = frozenset({"store", "ck", "warehouse", "location", "skip"})
WAREHOUSE_TYPES = frozenset({"store", "ck", "warehouse"})
ROW_TYPES = frozenset({"ALACARTE", "PACKAGE_HEADER", "PACKAGE_COMPONENT"})
FLOW_COLUMNS = ("sold", "ck_prod", "store_prod", "ck_out", "in_ck", "in_other", "st_in", "st_out")
TAKEAWAY_SUFFIX = " T"

# Kunci baris histori (validator menolak kunci lain -- PII fail closed, §27.7).
HISTORY_KEYS: dict[str, tuple[str, ...]] = {
    "legacy_flow_daily": (
        "day", "branch_key", "product_key", "esb_branch_id", "esb_product_code", *FLOW_COLUMNS, "outflow",
    ),
    "legacy_sales_daily": (
        "day", "branch_key", "pos_key", "menu_key", "esb_branch_id", "esb_menu_code", "is_takeaway", "qty", "gross",
        "discount", "net", "cogs", "trx_count",
    ),
    "legacy_sales_line": (
        "sales_num", "line_no", "date", "branch_key", "menu_key", "esb_menu_code", "qty", "price", "total",
        "discount", "cogs", "row_type", "is_takeaway",
    ),
    # G65 (docs/04 §27.13.4): arsip pembelian 24 bulan, semua status; *_key boleh null.
    "legacy_purchase_line": (
        "purchase_num", "line_no", "date", "date_required", "branch_key", "branch_name", "supplier_key",
        "supplier_name", "product_key", "esb_branch_id", "esb_supplier_code", "esb_product_code", "uom_name", "qty",
        "receipt_qty", "pricelist_price", "price_unit", "discount", "vat_value", "total", "status", "map_source",
    ),
}  # fmt: skip
# Kunci OPSIONAL histori (nama tampilan, data bisnis -- bukan PII). Export selalu mengisinya (nilai boleh null);
# identik dengan `HISTORY_OPTIONAL_KEYS` importer Odoo (esb_import/mapping.py).
HISTORY_OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "legacy_flow_daily": frozenset({"esb_product_name"}),
    "legacy_sales_daily": frozenset({"menu_name", "menu_category"}),
    "legacy_sales_line": frozenset(),
    "legacy_purchase_line": frozenset({"product_name", "category_name", "sub_category_name"}),
}
# Kunci `data` per entitas master (validator: di luar whitelist = error).
DATA_KEYS: dict[str, frozenset[str]] = {
    "branch": frozenset(
        {"branch_id", "name", "warehouse_code", "type", "is_central_kitchen", "active", "pos_xmlid", "parent_key"}
    ),
    "uom": frozenset({"esb_uom", "uom_xmlid", "ea_factor"}),
    "category": frozenset({"name", "parent_key"}),
    "pos_category": frozenset({"name", "parent_key"}),
    "product": frozenset(
        {
            "default_code", "name", "categ_key", "uom_esb", "uom_xmlid", "ea_factor", "type", "is_storable",
            "purchase_ok", "sale_ok", "is_fin", "active", "batch_to_ea_ratio", "uom_source",
        }
    ),
    "menu": frozenset(
        {
            "menu_code", "name", "pos_categ_key", "list_price", "available_in_pos", "sale_ok", "type", "active",
            "bom_name", "alias_of", "is_takeaway",
        }
    ),
    "payment_term": frozenset({"days", "name"}),
    "supplier": frozenset({"ref", "name", "is_company", "supplier_rank", "active"}),
    "customer": frozenset(
        {"ref", "name", "is_company", "customer_rank", "category_name", "payment_term_key", "active"}
    ),
    "bom": frozenset(
        {
            "code", "name", "type", "bom_type_id", "bom_type_name", "product_code", "product_key", "menu_keys",
            "product_qty", "uom_esb", "uom_xmlid", "ratio_source", "lines",
        }
    ),
    "cost": frozenset({"product_key", "product_code", "standard_price", "source_uom", "source", "as_of"}),
    # G65 (§27.13.4)
    "supplierinfo": frozenset(
        {
            "partner_key", "product_key", "supplier_code", "product_code", "price", "discount", "currency", "min_qty",
            "sequence", "date_start", "uom_esb", "uom_xmlid", "source_purchase_num",
        }
    ),
    "quant": frozenset(
        {
            "location_key", "product_key", "product_code", "quantity", "uom_esb", "uom_xmlid", "esb_branch_id",
            "esb_locations", "amount", "accounting_date", "quarantine_of",  # quarantine_of: hanya baris karantina
        }
    ),
}  # fmt: skip
BOM_LINE_KEYS = frozenset({"product_key", "product_code", "qty", "uom_esb", "uom_xmlid", "yield_percent"})
# Berkas per entitas master (§27.3).
ENTITY_FILES: dict[str, str] = {
    "branch": "branch.jsonl",
    "uom": "uom.jsonl",
    "category": "category.jsonl",
    "pos_category": "pos_category.jsonl",
    "product": "product.jsonl",
    "menu": "menu.jsonl",
    "payment_term": "payment_term.jsonl",
    "supplier": "supplier.jsonl",
    "customer": "customer.jsonl",
    "bom": "bom.jsonl",
    "cost": "cost.jsonl",
    "supplierinfo": "supplierinfo.jsonl",
    "quant": "quant.jsonl",
    "legacy_flow_daily": "legacy_flow_daily.jsonl.gz",
    "legacy_sales_daily": "legacy_sales_daily.jsonl.gz",
    "legacy_sales_line": "legacy_sales_line.jsonl.gz",
    "legacy_purchase_line": "legacy_purchase_line.jsonl.gz",
}
# G65 (§27.13.4, Q-ESB28): status PO yang mewakili harga beli sah (daftar izin; status lain tidak menjadi harga).
PURCHASE_PRICE_STATUSES = ("Authorized", "Receiving", "Finished", "Closed")
DISCOUNT_MODES = ("nominal", "percent")  # arti `discount` ESB (Q-G65-2)
COST_SOURCES = ("stock_opening", "bom_last_hpp", "purchase_last")  # urutan sumber HPP (L-252)
LOCATION_ACTIONS = frozenset({"stock", "skip", "quarantine"})
# Q-ESB25: lokasi ESB berpola ini tanpa entri location_map -> peringatan (non-production) / error (production).
SUSPECT_LOCATION = re.compile(r"damage|rusak|expired|kadaluar?sa|temporary|sementara|reject", re.I)
# `currency` fact_esb_purchase_line (header `currencyID`): kosong = baris tanpa header (fallback nama) -> mata uang
# brand. Nilai persis currencyID IDR di ESB [B] (§27.13.9).
IDR_CODES = frozenset({"", "IDR", "RP"})  # dibandingkan setelah upper(): "Rp" -> "RP" (currencySign, QA G65)


class MapError(ValueError):
    """Tabel pemetaan (branch_map/uom_map) tidak valid (exit 2)."""


def num(value: Any) -> int | float:
    """Decimal/float/int -> angka JSON (int bila bulat). None -> 0."""
    if value is None:
        return 0
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    if not dec.is_finite():
        raise ValueError("angka tidak hingga")
    return int(dec) if dec == dec.to_integral_value() else float(dec)


def norm_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def norm_uom(value: Any) -> str:
    return norm_text(value).upper()


# ================================================================== tabel pemetaan (CSV klien, di luar repo)
@dataclass(frozen=True)
class BranchMap:
    branch_id: int
    type: str
    warehouse_code: str
    display_name: str
    parent_branch_id: int | None


@dataclass(frozen=True)
class UomMap:
    esb_uom: str
    uom_xmlid: str
    ea_factor: Decimal


def _csv_rows(text: str, required: tuple[str, ...], name: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise MapError(f"{name}: kolom wajib tidak ada: {', '.join(missing)}")
    return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def parse_branch_map(text: str, brand: str) -> dict[int, BranchMap]:
    """`brand,branch_id,type,warehouse_code,display_name,parent_branch_id` (baris brand lain diabaikan)."""
    out: dict[int, BranchMap] = {}
    cols = ("brand", "branch_id", "type", "warehouse_code", "display_name", "parent_branch_id")
    for line_no, row in enumerate(_csv_rows(text, cols, "branch_map.csv"), start=2):
        if row["brand"].lower() != brand:
            continue
        if not row["branch_id"].isdigit() or int(row["branch_id"]) <= 0:
            raise MapError(f"branch_map.csv baris {line_no}: branch_id tidak valid")
        kind = row["type"].lower()
        if kind not in BRANCH_TYPES:
            raise MapError(f"branch_map.csv baris {line_no}: type wajib salah satu {sorted(BRANCH_TYPES)}")
        parent = row["parent_branch_id"]
        if kind == "location" and not parent.isdigit():
            raise MapError(f"branch_map.csv baris {line_no}: type location wajib parent_branch_id")
        branch_id = int(row["branch_id"])
        if branch_id in out:
            raise MapError(f"branch_map.csv baris {line_no}: branch_id ganda")
        parent_id = int(parent) if parent.isdigit() else None
        out[branch_id] = BranchMap(branch_id, kind, row["warehouse_code"].upper(), row["display_name"], parent_id)
    return out


def parse_uom_map(text: str) -> dict[str, UomMap]:
    """`esb_uom,uom_xmlid,ea_factor` -- ea_factor = jumlah EA per 1 satuan (kosong = 1)."""
    out: dict[str, UomMap] = {}
    for line_no, row in enumerate(_csv_rows(text, ("esb_uom", "uom_xmlid", "ea_factor"), "uom_map.csv"), start=2):
        key = norm_uom(row["esb_uom"])
        if not key or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", row["uom_xmlid"]):
            raise MapError(f"uom_map.csv baris {line_no}: esb_uom kosong atau uom_xmlid bukan xmlid")
        try:
            factor = Decimal(row["ea_factor"] or "1")
        except ArithmeticError:
            raise MapError(f"uom_map.csv baris {line_no}: ea_factor bukan angka") from None
        if not factor.is_finite() or factor <= 0:
            raise MapError(f"uom_map.csv baris {line_no}: ea_factor harus > 0")
        if key in out:
            raise MapError(f"uom_map.csv baris {line_no}: esb_uom ganda")
        out[key] = UomMap(key, row["uom_xmlid"], factor)
    return out


def location_name_key(value: Any) -> str:
    return norm_text(value).upper()


def parse_location_map(text: str, brand: str) -> dict[tuple[int, str], str]:
    """G65 opsional `brand,branch_id,esb_location_name,action` (action stock|skip|quarantine; brand lain diabaikan)
    -> {(branch_id, NAMA LOKASI ternormalisasi): action}."""
    out: dict[tuple[int, str], str] = {}
    cols = ("brand", "branch_id", "esb_location_name", "action")
    for line_no, row in enumerate(_csv_rows(text, cols, "location_map.csv"), start=2):
        if row["brand"].lower() != brand:
            continue
        if not row["branch_id"].isdigit() or int(row["branch_id"]) <= 0:
            raise MapError(f"location_map.csv baris {line_no}: branch_id tidak valid")
        name = location_name_key(row["esb_location_name"])
        action = row["action"].lower()
        if not name or action not in LOCATION_ACTIONS:
            raise MapError(f"location_map.csv baris {line_no}: nama kosong / action bukan stock|skip|quarantine")
        key = (int(row["branch_id"]), name)
        if key in out:
            raise MapError(f"location_map.csv baris {line_no}: lokasi ganda")
        out[key] = action
    return out


# ================================================================== master
def record(entity: str, company: str, esb_key: Any, xml: str | None, data: Mapping[str, Any]) -> dict[str, Any]:
    return {"entity": entity, "company_code": company, "esb_key": str(esb_key), "xmlid": xml, "data": dict(data)}


@dataclass
class BranchIndex:
    """Hasil pemetaan cabang: branch_id -> xmlid gudang (lokasi -> gudang induk), PT, xmlid pos.config."""

    wh_key: dict[int, str] = field(default_factory=dict)
    pos_key: dict[int, str] = field(default_factory=dict)
    company: dict[int, str] = field(default_factory=dict)
    unmapped: list[dict[str, Any]] = field(default_factory=list)
    # SEC-197: gudang tujuan (xmlid branch_wh) -> PT pemilik. Segmen histori dipecah per gudang tujuan.
    wh_company: dict[str, str] = field(default_factory=dict)

    def warehouses_of(self, company: str) -> list[str]:
        """Semua gudang tujuan milik `company` (termasuk yang tanpa baris histori), urut xmlid."""
        return sorted(wh for wh, code in self.wh_company.items() if code == company)

    def branches_by_warehouse(self, branch_ids: Iterable[int]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = defaultdict(list)
        for branch_id in sorted(set(branch_ids)):
            wh = self.wh_key.get(branch_id)
            if wh:
                out[wh].append(branch_id)
        return dict(out)


def build_branches(
    brand: str, branches: Iterable[Mapping[str, Any]], bmap: Mapping[int, BranchMap]
) -> tuple[list[dict[str, Any]], BranchIndex]:
    rows = {int(b["branch_id"]): b for b in branches}
    index = BranchIndex()
    out: list[dict[str, Any]] = []
    for branch_id in sorted(rows):
        b = rows[branch_id]
        company = str(b["company_code"])
        index.company[branch_id] = company
        entry = bmap.get(branch_id)
        if entry is None:
            index.unmapped.append(
                {"branch_id": branch_id, "active": int(b.get("active", 1)), "is_store": int(b.get("is_store", 0))}
            )
            continue
        if entry.type == "skip":
            continue
        name = entry.display_name or str(b.get("branch_name") or f"Cabang #{branch_id}")
        if entry.type in WAREHOUSE_TYPES:
            wh = xmlid(brand, company, "branch_wh", branch_id)
            pos = xmlid(brand, company, "branch_pos", branch_id) if entry.type == "store" else None
            index.wh_key[branch_id] = wh
            index.wh_company[wh] = company
            if pos:
                index.pos_key[branch_id] = pos
            data = {
                "branch_id": branch_id,
                "name": name,
                "warehouse_code": entry.warehouse_code,
                "type": entry.type,
                "is_central_kitchen": entry.type == "ck" or bool(b.get("is_ck")),
                "active": bool(b.get("active", 1)),
                "pos_xmlid": pos,
            }
            out.append(record("branch", company, branch_id, wh, data))
    # Lokasi (situs bukan gudang) -> di bawah gudang induk; histori cabang lokasi = gudang induk (SEC-197: gudang
    # tujuan sama -> part segmen sama). Induk milik PT lain -> histori lokasi TIDAK dipetakan (dihitung tak terpetakan).
    for branch_id in sorted(rows):
        entry = bmap.get(branch_id)
        if entry is None or entry.type != "location":
            continue
        company = index.company[branch_id]
        parent = index.wh_key.get(entry.parent_branch_id or 0)
        loc = xmlid(brand, company, "branch_loc", branch_id)
        if parent and index.wh_company.get(parent) == company:
            index.wh_key[branch_id] = parent
            parent_pos = index.pos_key.get(entry.parent_branch_id or 0)
            if parent_pos:
                index.pos_key[branch_id] = parent_pos
        data = {
            "branch_id": branch_id,
            "name": entry.display_name or str(rows[branch_id].get("branch_name") or f"Lokasi #{branch_id}"),
            "type": "location",
            "parent_key": parent,
            "active": bool(rows[branch_id].get("active", 1)),
        }
        out.append(record("branch", company, branch_id, loc, data))
    return out, index


def build_uoms(brand: str, company: str, uoms: Iterable[str], umap: Mapping[str, UomMap]) -> list[dict[str, Any]]:
    out = []
    for uom in sorted({norm_uom(u) for u in uoms if norm_uom(u)}):
        mapped = umap.get(uom)
        data = {
            "esb_uom": uom,
            "uom_xmlid": mapped.uom_xmlid if mapped else None,
            "ea_factor": num(mapped.ea_factor) if mapped else None,
        }
        out.append(record("uom", company, uom, xmlid(brand, company, "uom", uom), data))
    return out


def category_key(brand: str, company: str, path: str) -> str:
    return xmlid(brand, company, "category", path)


@cache
def category_roles() -> dict[str, CategoryRole]:
    """Tabel peran G61 (`efn_mig/category_roles.csv`, ikut image). Tidak valid -> MapError (exit 2)."""
    try:
        return load_roles()
    except CategoryRoleError as exc:
        raise MapError(str(exc)) from None


def product_role(product: Mapping[str, Any]) -> CategoryRole:
    """Peran kategori ESB produk; kosong / tidak ada di tabel -> MapError (fail closed, G61)."""
    try:
        return role_for(category_roles(), product.get("category"))
    except CategoryRoleError as exc:
        raise MapError(f"produk {str(product.get('product_code'))[:40]}: {exc}") from None


def build_categories(brand: str, company: str, products: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """G61: pohon kategori per peran (`Persediaan / Bahan Baku / Impor`, ...), bukan nama ESB datar. Setiap simpul =
    satu baris (esb_key = path lengkap), urut kedalaman agar induk diimpor lebih dulu; tingkat atas parent_key null."""
    paths = {node for p in products for node in path_chain(product_role(p).target_path)}
    out = []
    for path in sorted(paths, key=lambda x: (x.count(SEP), x)):
        parent = path.rsplit(SEP, 1)[0] if SEP in path else None
        data = {"name": path.rsplit(SEP, 1)[-1], "parent_key": category_key(brand, company, parent) if parent else None}
        out.append(record("category", company, path, category_key(brand, company, path), data))
    return out


def pos_category_path(menu: Mapping[str, Any]) -> tuple[str, str]:
    cat = norm_text(menu.get("menu_category"))
    detail = norm_text(menu.get("menu_category_detail"))
    return cat, (f"{cat}/{detail}" if cat and detail else "")


def build_pos_categories(brand: str, company: str, menus: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    parents: set[str] = set()
    children: set[tuple[str, str]] = set()
    for menu in menus:
        cat, path = pos_category_path(menu)
        if cat:
            parents.add(cat)
        if path:
            children.add((cat, path))
    out = [
        record("pos_category", company, c, xmlid(brand, company, "pos_category", c), {"name": c, "parent_key": None})
        for c in sorted(parents)
    ]
    for cat, path in sorted(children):
        data = {"name": path.split("/", 1)[1], "parent_key": xmlid(brand, company, "pos_category", cat)}
        out.append(record("pos_category", company, path, xmlid(brand, company, "pos_category", path), data))
    return out


def product_uoms(
    products: Iterable[Mapping[str, Any]],
    bom_headers: Iterable[Mapping[str, Any]],
    bom_lines: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    """Satuan per kode produk: dim_esb_product.uom, fallback satuan di BOM (header hasil / komponen; terkecil abjad)."""
    found: dict[str, set[str]] = defaultdict(set)
    for row in [*bom_headers, *bom_lines]:
        if row.get("product_code") and norm_uom(row.get("uom")):
            found[str(row["product_code"])].add(norm_uom(row["uom"]))
    out = {code: min(values) for code, values in found.items()}
    for p in products:
        if norm_uom(p.get("uom")):
            out[str(p["product_code"])] = norm_uom(p["uom"])
    return out


_XMLID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


@dataclass(frozen=True)
class ExportOptions:
    """Pengecualian EKSPLISIT export master (dry-run data nyata PB, 18 Nov 2026). Default = ketat (fail closed).

    Dicatat di manifest `export_options`; validator membaca manifest (bukan flag sendiri) dan menurunkan error terkait
    menjadi peringatan HANYA bila opsi tercatat. Laporan validator menampilkannya (berita acara: dry-run dengan
    pengecualian)."""

    default_uom: str | None = None  # --default-uom: produk tanpa UoM (setelah fallback BoM) -> xmlid ini
    skip_unlinked_menu_boms: bool = False  # --skip-unlinked-menu-boms: BoM tipe Menu tanpa tautan tidak diekspor
    allow_default_ratio: bool = False  # --allow-default-ratio: BoM normal FIN tanpa bom_ratio -> product_qty 1
    drop_zero_qty_components: bool = False  # --drop-zero-qty-components: komponen qty <= 0 dibuang

    def __post_init__(self) -> None:
        if self.default_uom is not None and not _XMLID_RE.fullmatch(self.default_uom):
            raise MapError("--default-uom wajib xmlid UoM (modul.nama)")

    def to_manifest(self) -> dict[str, Any]:
        return {
            "default_uom": self.default_uom,
            "skip_unlinked_menu_boms": self.skip_unlinked_menu_boms,
            "allow_default_ratio": self.allow_default_ratio,
            "drop_zero_qty_components": self.drop_zero_qty_components,
        }

    @property
    def strict(self) -> bool:
        return self == ExportOptions()


@dataclass
class BomSkips:
    """Yang dibuang build_boms karena opsi (dicatat di manifest `skipped`)."""

    unlinked_menu_boms: set[int] = field(default_factory=set)
    zero_qty_components: set[tuple[int, str]] = field(default_factory=set)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "unlinked_menu_boms": {"count": len(self.unlinked_menu_boms), "bom_ids": sorted(self.unlinked_menu_boms)},
            "zero_qty_components": {
                "count": len(self.zero_qty_components),
                "items": [{"bom_id": b, "product_code": c} for b, c in sorted(self.zero_qty_components)],
            },
        }


def build_products(
    brand: str,
    company: str,
    products: Iterable[Mapping[str, Any]],
    uom_by_code: Mapping[str, str],
    umap: Mapping[str, UomMap],
    default_uom: str | None = None,
) -> list[dict[str, Any]]:
    """`default_uom` (ExportOptions) dipakai HANYA bila produk tetap tanpa UoM terpetakan setelah fallback BoM;
    baris itu mendapat `uom_source="default"` (kunci hanya ada bila default dipakai)."""
    out = []
    for p in sorted(products, key=lambda r: str(r["product_code"])):
        code = str(p["product_code"])
        uom = uom_by_code.get(code, "")
        mapped = umap.get(uom)
        use_default = mapped is None and default_uom is not None
        role = product_role(p)
        is_fin = bool(p.get("is_fin"))
        ratio = p.get("batch_to_ea_ratio")
        data = {
            "default_code": code,
            "name": norm_text(p.get("product_name")) or code,
            "categ_key": category_key(brand, company, role.target_path),
            "uom_esb": uom or None,
            "uom_xmlid": mapped.uom_xmlid if mapped else (default_uom if use_default else None),
            "ea_factor": num(mapped.ea_factor) if mapped else None,
            # G61: flag per peran (category_roles.csv). Aturan `supplierinfo` (A2): sumber supplierinfo/PO ESB belum
            # diekspor (E11-E13) -> False; recategorize_pb.py memeriksa product.supplierinfo di Odoo.
            "type": role.type,
            "is_storable": role.is_storable,
            "purchase_ok": role.purchase(has_supplier=False, is_fin=is_fin),
            "sale_ok": role.sale_ok,
            "is_fin": is_fin,
            "active": bool(p.get("active", 1)),
            "batch_to_ea_ratio": num(ratio) if ratio is not None else None,
        }
        if use_default:
            data["uom_source"] = "default"
        out.append(record("product", company, code, xmlid(brand, company, "product", code), data))
    return out


@dataclass
class MenuIndex:
    """menu_code -> xmlid record Odoo (takeaway -> menu dasar), menu_code -> takeaway?"""

    key: dict[str, str] = field(default_factory=dict)
    takeaway: dict[str, bool] = field(default_factory=dict)
    by_bom_name: dict[str, list[str]] = field(default_factory=dict)


def menu_code_of(row: Mapping[str, Any]) -> str:
    """Kode menu ESB; kosong (data nyata: header paket/baris nol tanpa menuCode) -> kunci sintetis deterministik
    `NAME:<12 hex sha256 nama ternormalisasi>` (<= 32 karakter, muat esb_menu_code). Dipakai SAMA untuk menu
    master/arsip, sales.daily, dan sales.line agar kunci konsisten (dry-run PB 18 Nov 2026: 8/62.118 baris)."""
    code = str(row.get("menu_code") or "").strip()
    if code:
        return code
    name = norm_text(row.get("menu_name")).upper()
    return "NAME:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]


def _sold_since(last_sold: Any, since: date | None) -> bool:
    if since is None or last_sold in (None, ""):
        return False
    day = last_sold if isinstance(last_sold, date) else date.fromisoformat(str(last_sold)[:10])
    return day >= since


def build_menus(
    brand: str,
    company: str,
    current: Iterable[Mapping[str, Any]],
    prices: Iterable[Mapping[str, Any]],
    archive: Iterable[Mapping[str, Any]],
    recent_since: date | None = None,
) -> tuple[list[dict[str, Any]], MenuIndex]:
    """Menu aktif (dim_esb_menu terbaru) + harga (dim_esb_menu_price) + menu dari histori penjualan.

    Menu yang hanya ada di histori: aktif bila terjual pada/ setelah `recent_since` (L-166: menu POS ESB tidak lengkap
    di `/menu/browse` ESB Core, mis. minuman/takeaway; uji data nyata 19 Nov 2026: sebagian besar kode terjual
    tidak ada di master), selain itu arsip (active=False). `recent_since=None` = perilaku lama (semua menu histori
    = arsip).

    Takeaway (nama berakhiran " T"): digabung ke menu dasar dengan nama sama (tanpa " T") -> baris `alias_of` = xmlid
    menu dasar (importer menambah xmlid kedua ke record yang sama). Tanpa menu dasar -> menu berdiri sendiri.
    """
    price_by_code = {menu_code_of(r): r for r in prices}
    menus: dict[str, dict[str, Any]] = {}
    for row in archive:
        menus[menu_code_of(row)] = {**row, "_archive": not _sold_since(row.get("last_sold"), recent_since)}
    for row in current:
        menus[menu_code_of(row)] = {**row, "_archive": False}
    base_by_name: dict[str, str] = {}
    for code in sorted(menus):
        name = norm_text(menus[code].get("menu_name")).upper()
        if not name.endswith(TAKEAWAY_SUFFIX):
            base_by_name.setdefault(name, code)
    index = MenuIndex()
    out: list[dict[str, Any]] = []
    for code in sorted(menus):
        m = menus[code]
        name = norm_text(m.get("menu_name")) or code
        price_row = price_by_code.get(code)
        takeaway = name.upper().endswith(TAKEAWAY_SUFFIX)
        base_code = base_by_name.get(name.upper()[: -len(TAKEAWAY_SUFFIX)]) if takeaway else None
        own = xmlid(brand, company, "menu", code)
        base = xmlid(brand, company, "menu", base_code) if base_code else None
        index.key[code] = base or own
        index.takeaway[code] = takeaway
        active = not m["_archive"] and (price_row is None or bool(price_row.get("active", 1)))
        cat, path = pos_category_path(m)
        pos_key = xmlid(brand, company, "pos_category", path or cat) if (path or cat) else None
        bom_name = norm_text(price_row.get("bom_name")) if price_row else ""
        if bom_name:
            index.by_bom_name.setdefault(bom_name.upper(), []).append(base or own)
        data = {
            "menu_code": code,
            "name": name,
            "pos_categ_key": pos_key,
            "list_price": num(price_row.get("price")) if price_row else None,
            "available_in_pos": active,
            "sale_ok": True,
            "type": "consu",
            "active": active,
            "bom_name": bom_name or None,
            "alias_of": base,
            "is_takeaway": takeaway,
        }
        out.append(record("menu", company, code, own, data))
    return out, index


def build_partners(
    brand: str, company: str, suppliers: Iterable[Mapping[str, Any]], customers: Iterable[Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    terms: set[int] = set()
    sup = [
        record(
            "supplier",
            company,
            s["supplier_code"],
            xmlid(brand, company, "supplier", s["supplier_code"]),
            {
                "ref": str(s["supplier_code"]),
                "name": norm_text(s.get("supplier_name")) or str(s["supplier_code"]),
                "is_company": True,
                "supplier_rank": 1,
                "active": bool(s.get("active", 1)),
            },
        )
        for s in sorted(suppliers, key=lambda r: str(r["supplier_code"]))
    ]
    cus = []
    for c in sorted(customers, key=lambda r: str(r["customer_code"])):
        days = int(c.get("payment_due_days") or 0)
        if days > 0:
            terms.add(days)
        data = {
            "ref": str(c["customer_code"]),
            "name": norm_text(c.get("customer_name")) or str(c["customer_code"]),
            "is_company": True,
            "customer_rank": 1,
            "category_name": norm_text(c.get("customer_category")) or None,
            "payment_term_key": xmlid(brand, company, "payment_term", f"net_{days}") if days > 0 else None,
            "active": bool(c.get("active", 1)),
        }
        key = xmlid(brand, company, "customer", c["customer_code"])
        cus.append(record("customer", company, c["customer_code"], key, data))
    pt = [
        record(
            "payment_term",
            company,
            f"net_{d}",
            xmlid(brand, company, "payment_term", f"net_{d}"),
            {"days": d, "name": f"{d} Hari"},
        )
        for d in sorted(terms)
    ]
    return {"supplier": sup, "customer": cus, "payment_term": pt}


def build_boms(
    brand: str,
    company: str,
    headers: Iterable[Mapping[str, Any]],
    lines: Iterable[Mapping[str, Any]],
    product_codes: set[str],
    menus: MenuIndex,
    ratios: Mapping[str, float],
    umap: Mapping[str, UomMap],
    options: ExportOptions | None = None,
    skips: BomSkips | None = None,
) -> list[dict[str, Any]]:
    """BOM ESB -> `mrp.bom`. Ditautkan menu (bomName di `/menu/browse-for-product`) -> `phantom` (kit menu), selain itu
    `normal` (produksi). product_qty = rasio BATCH->EA (`bom_ratio`) bila ada, selain itu 1 (`ratio_source`).

    Opsi (default ketat = tidak ada yang dibuang): `skip_unlinked_menu_boms` -> BoM tipe Menu / tanpa produk hasil yang
    tidak tertaut menu tidak diekspor; `drop_zero_qty_components` -> komponen qty <= 0 dibuang. Yang dibuang dicatat di
    `skips` (manifest `skipped`)."""
    opts = options or ExportOptions()
    skips = skips if skips is not None else BomSkips()
    by_bom: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for line in lines:
        by_bom[int(line["bom_id"])].append(line)
    out = []
    for h in sorted(headers, key=lambda r: int(r["bom_id"])):
        if not h.get("active", 1):
            continue
        bom_id = int(h["bom_id"])
        code = str(h.get("product_code") or "")
        menu_keys = sorted(set(menus.by_bom_name.get(norm_text(h.get("bom_name")).upper(), [])))
        menu_type = norm_text(h.get("bom_type_name")).upper() == "MENU"
        unlinked = not menu_keys and (menu_type or code not in product_codes)
        if unlinked and opts.skip_unlinked_menu_boms:
            skips.unlinked_menu_boms.add(bom_id)
            continue
        ratio = ratios.get(code)
        uom = norm_uom(h.get("uom"))
        comp = []
        for line in sorted(by_bom.get(bom_id, []), key=lambda r: int(r["line_id"])):
            lcode = str(line["product_code"])
            if opts.drop_zero_qty_components and Decimal(str(line.get("qty") or 0)) <= 0:
                skips.zero_qty_components.add((bom_id, lcode))
                continue
            luom = norm_uom(line.get("uom"))
            comp.append(
                {
                    "product_key": xmlid(brand, company, "product", lcode) if lcode in product_codes else None,
                    "product_code": lcode,
                    "qty": num(line.get("qty")),
                    "uom_esb": luom or None,
                    "uom_xmlid": umap[luom].uom_xmlid if luom in umap else None,
                    "yield_percent": num(line.get("yield_percent")),
                }
            )
        data = {
            "code": str(h.get("bom_code") or bom_id),
            "name": norm_text(h.get("bom_name")),
            "type": "phantom" if menu_keys else "normal",
            "bom_type_id": int(h.get("bom_type_id") or 0),
            "bom_type_name": norm_text(h.get("bom_type_name")) or None,
            "product_code": code or None,
            "product_key": xmlid(brand, company, "product", code) if code in product_codes else None,
            "menu_keys": menu_keys,
            "product_qty": num(ratio) if ratio and not menu_keys else 1,
            "uom_esb": uom or None,
            "uom_xmlid": umap[uom].uom_xmlid if uom in umap else None,
            "ratio_source": "bom_ratio" if ratio and not menu_keys else "default_1",
            "lines": comp,
        }
        out.append(record("bom", company, bom_id, xmlid(brand, company, "bom", bom_id), data))
    return out


def dec(value: Any) -> Decimal:
    """Angka sumber (int/float/Decimal/None campur, §27.13.1) -> Decimal lewat str (bukan float)."""
    out = value if isinstance(value, Decimal) else Decimal(str(value if value not in (None, "") else 0))
    if not out.is_finite():
        raise ValueError("angka tidak hingga")
    return out


def bom_last_hpp(lines: Iterable[Mapping[str, Any]], product_codes: set[str]) -> dict[str, tuple[Decimal, str]]:
    """`lastHpp` komponen BOM (nilai > 0 terbesar per kode; satuan = satuan komponen [B])."""
    best: dict[str, tuple[Decimal, str]] = {}
    for line in lines:
        code = str(line["product_code"])
        hpp = dec(line.get("last_hpp"))
        if code in product_codes and hpp > 0 and (code not in best or hpp > best[code][0]):
            best[code] = (hpp, norm_uom(line.get("uom")))
    return best


# Satuan referensi Odoo standar per keluarga (G65 QA P2: beli G, produk KG). Satuan dengan `uom_xmlid` di luar tabel
# hanya dapat dikonversi ke satuan ber-`uom_xmlid` sama (keluarga = xmlid itu sendiri, ukuran = ea_factor). Lusin
# sengaja tidak dimasukkan: klien bisa memetakan LUSIN ke Unit dengan ea_factor 12 (dihitung ganda).
STANDARD_UOM_SIZE: dict[str, tuple[str, Decimal]] = {
    "uom.product_uom_gram": ("weight", Decimal("0.001")),
    "uom.product_uom_kgm": ("weight", Decimal(1)),
    "uom.product_uom_ton": ("weight", Decimal(1000)),
}


def uom_size(entry: UomMap) -> tuple[str, Decimal]:
    family, size = STANDARD_UOM_SIZE.get(entry.uom_xmlid, (entry.uom_xmlid, Decimal(1)))
    return family, size * entry.ea_factor


def uom_ratio(src_uom: str, dst_uom: str, umap: Mapping[str, UomMap]) -> Decimal | None:
    """Berapa satuan `dst_uom` per 1 `src_uom` (qty_dst = qty_src × rasio; harga_dst = harga_src / rasio). Teks sama ->
    1; keduanya di uom_map dan sekeluarga (xmlid sama -> ea_factor; berat standar G/KG/TON -> faktor Odoo) -> rasio;
    selain itu None (tidak ditebak)."""
    if src_uom and src_uom == dst_uom:
        return Decimal(1)
    src, dst = umap.get(src_uom), umap.get(dst_uom)
    if src is None or dst is None:
        return None
    (src_family, src_size), (dst_family, dst_size) = uom_size(src), uom_size(dst)
    return src_size / dst_size if src_family == dst_family else None


def opening_costs(
    rows: Iterable[Mapping[str, Any]],
    product_codes: set[str],
    mixed_uom: set[str] | None = None,
    product_uoms: Mapping[str, str] | None = None,
    umap: Mapping[str, UomMap] | None = None,
) -> dict[str, tuple[Decimal, str]]:
    """HPP cutover per produk = Σ amount_balance / Σ qty_balance atas baris qty_balance > 0 (semua cabang company,
    L-252). Dengan `product_uoms` + `umap`: qty dikonversi ke satuan produk (`to_product_qty`) -> harga per satuan
    produk; tanpa keduanya: per satuan ESB baris. Satuan tak terkonversi / campur -> tidak dihitung (dicatat di
    `mixed_uom`; sumber berikutnya dipakai) -- tidak menebak konversi."""
    sums: dict[str, dict[str, list[Decimal]]] = defaultdict(lambda: defaultdict(lambda: [Decimal(0), Decimal(0)]))
    for row in rows:
        code, qty = str(row["product_code"]), dec(row.get("qty_balance"))
        if code in product_codes and qty > 0:
            uom = norm_uom(row.get("uom"))
            if product_uoms is not None and umap is not None:
                target = product_uoms.get(code, "")
                converted = to_product_qty(qty, uom, target, umap)
                uom, qty = (target, converted) if converted is not None else (f"?{uom}", qty)
            acc = sums[code][uom]
            acc[0] += dec(row.get("amount_balance"))
            acc[1] += qty
    out: dict[str, tuple[Decimal, str]] = {}
    for code, by_uom in sums.items():
        if len(by_uom) > 1 or any(u.startswith("?") for u in by_uom):  # "?" = satuan tak terkonversi
            if mixed_uom is not None:
                mixed_uom.add(code)
            continue
        ((uom, (amount, qty)),) = by_uom.items()
        if amount > 0:
            out[code] = (amount / qty, uom)
    return out


def _price_in_product_uom(
    price: Decimal,
    uom: str,
    code: str,
    product_uoms: Mapping[str, str] | None,
    umap: Mapping[str, UomMap] | None,
) -> tuple[Decimal, str] | None:
    """Harga per `uom` -> per satuan produk: `price / uom_ratio(uom, produk)` (PACK = 6 EA -> /6; G -> KG ×1000).
    QA G65: bundel `cost` tanpa master tidak membawa `uom.jsonl`, jadi importer tidak bisa mengonversi -> export
    tidak pernah mengirim satuan ≠ UoM produk. Tanpa `product_uoms`/`umap` (perilaku lama, test murni) -> apa
    adanya. Tidak terkonversi -> None (sumber dilewati)."""
    if product_uoms is None or umap is None:
        return price, uom
    target = product_uoms.get(code, "")
    ratio = uom_ratio(uom, target, umap)
    return None if ratio is None else (price / ratio, target)


def build_costs(
    brand: str,
    company: str,
    lines: Iterable[Mapping[str, Any]],
    product_codes: set[str],
    opening: Iterable[Mapping[str, Any]] = (),
    purchase_last: Mapping[str, tuple[Decimal, str]] | None = None,
    as_of: date | None = None,
    mixed_uom: set[str] | None = None,
    product_uoms: Mapping[str, str] | None = None,
    umap: Mapping[str, UomMap] | None = None,
    unconvertible: set[tuple[str, str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    """`standard_price` per produk, sumber pertama yang > 0 (§27.13.4, L-252): `stock_opening` (baris
    `fact_esb_stock_opening` company ini pada cutover; kosong bila tanpa --cutover-date) -> `bom_last_hpp` ->
    `purchase_last` (harga baris PO sah terbaru). Dengan `product_uoms` + `umap` (export) semua nilai per satuan
    produk (`source_uom` = UoM produk); sumber yang satuannya tidak terkonversi dilewati dan dicatat di
    `unconvertible` (company, produk, sumber, satuan) -> sumber berikutnya. Tanpa sumber -> tidak ada baris."""
    found: dict[str, tuple[Decimal, str, str]] = {}
    mixed: set[str] = set()
    opening = list(opening)
    for code, (price, uom) in opening_costs(opening, product_codes, mixed, product_uoms, umap).items():
        found[code] = (price, uom, "stock_opening")
    if mixed_uom is not None:
        mixed_uom |= mixed
    if unconvertible is not None and product_uoms is not None and umap is not None:
        # G65b: satuan baris saldo awal yang sebenarnya (bukan '') agar item manifest bisa ditindaklanjuti.
        row_uoms: dict[str, set[str]] = defaultdict(set)
        for row in opening:
            if str(row["product_code"]) in mixed and dec(row.get("qty_balance")) > 0:
                row_uoms[str(row["product_code"])].add(norm_uom(row.get("uom")))
        unconvertible |= {(company, code, "stock_opening", ",".join(sorted(row_uoms[code]))) for code in mixed}

    def offer(code: str, price: Decimal, uom: str, source: str) -> None:
        if code in found:
            return
        converted = _price_in_product_uom(price, uom, code, product_uoms, umap)
        if converted is None:
            if unconvertible is not None:
                unconvertible.add((company, code, source, uom))
            return
        found[code] = (*converted, source)

    for code, (price, uom) in bom_last_hpp(lines, product_codes).items():
        offer(code, price, uom, "bom_last_hpp")
    for code, (price, uom) in (purchase_last or {}).items():
        if code in product_codes and price > 0:
            offer(code, price, uom, "purchase_last")
    return [
        record(
            "cost",
            company,
            code,
            None,
            {
                "product_key": xmlid(brand, company, "product", code),
                "product_code": code,
                "standard_price": num(round(price, 4)),
                "source_uom": uom or None,
                "source": source,
                "as_of": as_of.isoformat() if as_of else None,
            },
        )
        for code, (price, uom, source) in sorted(found.items())
    ]


# ================================================================== G65 harga beli supplier (§27.13.4)
def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def is_idr(value: Any) -> bool:
    return norm_text(value).upper() in IDR_CODES


def year_before(day: date) -> date:
    """`day` − 12 bulan (29 Feb -> 28 Feb)."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


@dataclass
class PurchaseSkips:
    """Yang tidak menjadi harga beli supplier (manifest `skipped.currency`, `skipped.supplierinfo_unmapped`)."""

    currency: int = 0
    unmapped: dict[tuple[str, str, str], str] = field(default_factory=dict)  # (company, supplier, produk) -> alasan

    def to_manifest(self) -> dict[str, Any]:
        items = [
            {"company_code": c, "supplier_code": s, "product_code": p, "reason": r}
            for (c, s, p), r in sorted(self.unmapped.items())
        ]
        return {
            "currency": {"count": self.currency},
            "supplierinfo_unmapped": {"count": len(items), "items": items[:1000]},
        }


PurchaseKey = tuple[str, str, str]  # (company_code, supplier_code, product_code)
PurchaseRank = tuple[date, str, int]  # (purchase_date, purchase_num, line_no) -- terbesar = terbaru


def latest_purchases(
    rows: Iterable[Mapping[str, Any]],
    statuses: Iterable[str],
    start: date,
    end: date,
    skips: PurchaseSkips | None = None,
) -> dict[PurchaseKey, tuple[PurchaseRank, Mapping[str, Any]]]:
    """Baris PO sah terbaru per (company, supplier, produk) dalam `[start, end)`: status di daftar izin,
    `map_source != none`, `supplier_code` ada, qty > 0, price > 0, IDR (lain -> `skips.currency`). Seri tanggal ->
    `purchase_num` terbesar -> `line_no` terbesar."""
    allowed = set(statuses)
    best: dict[PurchaseKey, tuple[PurchaseRank, Mapping[str, Any]]] = {}
    for row in rows:
        day = _date(row["purchase_date"])
        supplier = str(row.get("supplier_code") or "")
        if not start <= day < end or norm_text(row.get("status_name")) not in allowed:
            continue
        if str(row.get("map_source") or "none") == "none" or not supplier:
            continue
        if dec(row.get("qty")) <= 0 or dec(row.get("price")) <= 0:
            continue
        if not is_idr(row.get("currency")):
            if skips is not None:
                skips.currency += 1
            continue
        key = (str(row["company_code"]), supplier, str(row["product_code"]))
        rank = (day, str(row["purchase_num"]), int(row["line_no"]))
        if key not in best or rank > best[key][0]:
            best[key] = (rank, row)
    return best


def purchase_last_prices(
    latest: Mapping[PurchaseKey, tuple[PurchaseRank, Mapping[str, Any]]], company: str
) -> dict[str, tuple[Decimal, str]]:
    """Sumber HPP ke-3: harga baris PO sah terbaru per produk (semua supplier) -> (price, satuan beli)."""
    best: dict[str, tuple[PurchaseRank, Decimal, str]] = {}
    for (comp, _, code), (rank, row) in latest.items():
        if comp == company and (code not in best or rank > best[code][0]):
            best[code] = (rank, dec(row.get("price")), norm_uom(row.get("uom")))
    return {code: (price, uom) for code, (_, price, uom) in best.items()}


def supplier_discount(row: Mapping[str, Any], mode: str) -> Decimal:
    """`discount` ESB -> % Odoo. `nominal`: discount / (price × qty) × 100, 2 desimal, dibatasi 0..100;
    `percent`: apa adanya (validator menolak di luar 0..100)."""
    raw = dec(row.get("discount"))
    if mode == "percent":
        return raw
    if mode != "nominal":
        raise MapError(f"--purchase-discount-mode tidak dikenal: {mode}")
    base = dec(row.get("price")) * dec(row.get("qty"))
    pct = raw / base * 100 if base > 0 else Decimal(0)
    return min(max(round(pct, 2), Decimal(0)), Decimal(100))


def build_supplierinfos(
    brand: str,
    company: str,
    latest: Mapping[PurchaseKey, tuple[PurchaseRank, Mapping[str, Any]]],
    supplier_codes: set[str],
    product_codes: set[str],
    umap: Mapping[str, UomMap],
    discount_mode: str,
    skips: PurchaseSkips | None = None,
    product_uoms: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """`product.supplierinfo` per company × supplier × produk (L-253). Supplier/produk tidak ada di master atau satuan
    beli tidak ada di uom_map -> dilewati (`skips.unmapped`). `sequence` per produk: date_start terbaru = 1.
    `product_uoms` (kode -> satuan ESB produk; selalu diisi export): harga dikonversi ke satuan produk
    (`price / uom_ratio`: PACK = 6 EA -> /6, G -> KG ×1000) dan `uom_*` = satuan produk; tidak terkonversi -> dilewati
    (`skips.unmapped` alasan `uom_unconvertible`). Tanpa `product_uoms` (test murni) -> per satuan beli."""
    picked: dict[str, list[tuple[date, str, Mapping[str, Any], Decimal, UomMap]]] = defaultdict(list)
    for (comp, supplier, code), ((day, _, _), row) in latest.items():
        if comp != company:
            continue
        uom = norm_uom(row.get("uom"))
        converted = _price_in_product_uom(dec(row.get("price")), uom, code, product_uoms, umap)
        reason = (
            "supplier" if supplier not in supplier_codes
            else "product" if code not in product_codes
            else "uom" if uom not in umap
            else "uom_unconvertible" if converted is None or converted[1] not in umap
            else ""
        )  # fmt: skip
        if reason or converted is None:
            if skips is not None:
                skips.unmapped[(comp, supplier, code)] = reason
            continue
        picked[code].append((day, supplier, row, converted[0], umap[converted[1]]))
    out: list[dict[str, Any]] = []
    for code in sorted(picked):
        ordered = sorted(picked[code], key=lambda x: (-x[0].toordinal(), x[1]))
        for seq, (day, supplier, row, unit_price, mapped) in enumerate(ordered, start=1):
            price = round(unit_price, 4)
            data = {
                "partner_key": xmlid(brand, company, "supplier", supplier),
                "product_key": xmlid(brand, company, "product", code),
                "supplier_code": supplier,
                "product_code": code,
                "price": num(price),
                "discount": num(supplier_discount(row, discount_mode)),
                "currency": "IDR",
                "min_qty": 0,
                "sequence": seq,
                "date_start": day.isoformat(),
                "uom_esb": mapped.esb_uom,
                "uom_xmlid": mapped.uom_xmlid,
                "source_purchase_num": str(row["purchase_num"]),
            }
            key = supplierinfo_xmlid(brand, company, supplier, code)
            out.append(record("supplierinfo", company, f"{supplier}|{code}", key, data))
    return out


def supplierinfo_xmlid(brand: str, company: str, supplier: str, code: str) -> str:
    """xmlid supplierinfo dari esb_key `<supplier>|<produk>` (QA G65 P3): `|` di luar `[A-Z0-9_]` sehingga `xmlid_key`
    selalu menambah sha256 kode utuh -> `A_B`+`C` dan `A`+`B_C` tidak bertabrakan (dulu `<sup>_<prod>`). Importer
    wajib menghitung sama."""
    return xmlid(brand, company, "supplierinfo", f"{supplier}|{code}")


# ================================================================== G65 stok awal (§27.13.4 `stock`)
@dataclass
class StockSkips:
    """Baris/kelompok stok yang tidak diekspor (manifest `skipped`) + neraca qty per cabang (rekonsiliasi 2)."""

    negative_stock: list[dict[str, Any]] = field(default_factory=list)
    uom_unconvertible: set[tuple[int, str, str]] = field(default_factory=set)  # (cabang, produk, satuan ESB)
    non_storable: int = 0
    product_unmapped: int = 0
    branch_unmapped: int = 0
    location_skip: int = 0
    # G65b: produk bersatuan kosong dengan >1 satuan di snapshot saldo awal -> ditolak (produk -> satuan terurut).
    uom_conflict: dict[str, list[str]] = field(default_factory=dict)
    # Bukan skip: baris lokasi ESB `quarantine` (location_map) yang dimuat ke lokasi karantina (manifest top-level).
    quarantine_rows: int = 0
    location_unmapped: set[tuple[int, str]] = field(default_factory=set)
    location_suspect: set[tuple[int, str]] = field(default_factory=set)
    by_branch: dict[int, dict[str, Decimal]] = field(default_factory=dict)

    def branch(self, branch_id: int) -> dict[str, Decimal]:
        zero = Decimal(0)
        return self.by_branch.setdefault(branch_id, {"source_qty": zero, "exported_qty": zero, "skipped_qty": zero})

    def to_manifest(self) -> dict[str, Any]:
        def locs(items: set[tuple[int, str]]) -> dict[str, Any]:
            rows = [{"branch_id": b, "location_name": n} for b, n in sorted(items)]
            return {"count": len(rows), "items": rows[:1000]}

        return {
            "negative_stock": {"count": len(self.negative_stock), "items": self.negative_stock[:1000]},
            "uom_unconvertible": {
                "count": len(self.uom_unconvertible),
                "items": [{"branch_id": b, "product_code": c, "uom": u} for b, c, u in sorted(self.uom_unconvertible)][
                    :1000
                ],
            },
            "non_storable": {"count": self.non_storable},
            "product_unmapped": {"count": self.product_unmapped},
            "branch_unmapped": {"count": self.branch_unmapped},
            "location_skip": {"count": self.location_skip},
            "uom_conflict": {
                "count": len(self.uom_conflict),
                "items": [{"product_code": c, "uoms": u} for c, u in sorted(self.uom_conflict.items())][:1000],
            },
            "location_unmapped": locs(self.location_unmapped),
            "location_suspect": locs(self.location_suspect),
        }

    def branch_stats(self) -> dict[str, dict[str, int | float]]:
        return {
            str(b): {k: num(round(v, 3)) for k, v in sorted(stat.items())} for b, stat in sorted(self.by_branch.items())
        }


def location_key(brand: str, branch_id: int, entry: BranchMap | None, index: BranchIndex) -> str | None:
    """Lokasi Odoo stok awal: gudang (store/ck/warehouse) -> xmlid `branch_wh` (importer: `lot_stock_id`);
    `location` -> xmlid `branch_loc` (lokasi internal di bawah gudang induk) bila induk PT sama; skip/tak terpetakan
    -> None."""
    company = index.company.get(branch_id)
    if entry is None or company is None or entry.type == "skip":
        return None
    if entry.type in WAREHOUSE_TYPES:
        return xmlid(brand, company, "branch_wh", branch_id)
    return xmlid(brand, company, "branch_loc", branch_id) if branch_id in index.wh_key else None


def opening_uoms(
    product_uoms: Mapping[str, str], rows: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """G65b (gladi 24 Nov 2026: dim_esb_product.uom kosong untuk semua produk PB): satuan efektif stok/HPP. Produk
    tanpa satuan (`product_uoms` kosong = dim_esb_product + fallback BOM) memakai satuan dasar dari baris
    `fact_esb_stock_opening` (extract `unitToShow=Base Unit` -> satu satuan per produk). >1 satuan di snapshot ->
    produk tetap tanpa satuan dan masuk `conflicts` (stok ditolak, validator error). Produk yang satuannya sudah
    terisi TIDAK diubah. Idempoten (aman dipanggil ulang dengan hasilnya)."""
    seen: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        code, uom = str(row["product_code"]), norm_uom(row.get("uom"))
        if uom and not product_uoms.get(code):
            seen[code].add(uom)
    out = dict(product_uoms)
    conflicts: dict[str, list[str]] = {}
    for code, uoms in sorted(seen.items()):
        if len(uoms) == 1:
            out[code] = next(iter(uoms))
        else:
            conflicts[code] = sorted(uoms)
    return out, conflicts


def build_quants(
    brand: str,
    rows: Iterable[Mapping[str, Any]],
    index: BranchIndex,
    bmap: Mapping[int, BranchMap],
    products: Iterable[Mapping[str, Any]],
    uom_by_code: Mapping[str, str],
    umap: Mapping[str, UomMap],
    lmap: Mapping[tuple[int, str], str],
    cutover: date,
    skips: StockSkips,
) -> list[dict[str, Any]]:
    """`fact_esb_stock_opening` (opening_date = cutover) -> `quant.jsonl`: Σ qty per company × lokasi Odoo × produk
    (lokasi ESB `stock` digabung), dikonversi ke **satuan produk** (`to_product_qty`; `uom_esb`/`uom_xmlid` = UoM
    produk). Satuan tanpa faktor -> `uom_unconvertible` (validator error). Hanya produk storable; hasil < 0 ->
    `negative_stock`, = 0 -> tidak diekspor. `accounting_date` = cutover − 1.
    G65b: satuan produk kosong -> satuan dasar baris snapshot (`opening_uoms`; konflik -> `uom_conflict`). Lokasi ESB
    `quarantine` -> quant terpisah di xmlid `branch_quarantine` cabang (esb_key tetap `<cabang>|<produk>`) +
    `quarantine_of` = xmlid `branch_wh` induk (importer membuat/menemukan lokasi karantina di bawah gudang itu)."""
    rows = list(rows)
    uom_by_code, conflicts = opening_uoms(uom_by_code, rows)
    storable = {str(p["product_code"]): product_role(p).is_storable for p in products}
    groups: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for row in rows:
        branch_id, qty = int(row["branch_id"]), dec(row.get("qty_balance"))
        stat = skips.branch(branch_id)
        stat["source_qty"] += qty
        loc = location_key(brand, branch_id, bmap.get(branch_id), index)
        name = norm_text(row.get("location_name"))
        code = str(row["product_code"])
        converted: Decimal | None = None
        reason = ""
        quarantine_of: str | None = None
        if loc is None:
            skips.branch_unmapped += 1
            reason = "branch"
        else:
            action = lmap.get((branch_id, name.upper()))
            if action is None:
                skips.location_unmapped.add((branch_id, name))
                if SUSPECT_LOCATION.search(name):
                    skips.location_suspect.add((branch_id, name))
                action = "stock"
            if action == "skip":
                skips.location_skip += 1
                reason = "skip"
            elif code not in storable:
                skips.product_unmapped += 1
                reason = "product"
            elif not storable[code]:
                skips.non_storable += 1
                reason = "non_storable"
            elif code in conflicts:
                skips.uom_conflict[code] = conflicts[code]
                reason = "uom_conflict"
            elif action == "quarantine":
                quarantine_of = index.wh_key[branch_id]  # location_key != None -> gudang (induk) PT sama ada
                loc = xmlid(brand, index.company[branch_id], "branch_quarantine", branch_id)
        if not reason:
            row_uom = norm_uom(row.get("uom"))
            converted = to_product_qty(qty, row_uom, uom_by_code.get(code, ""), umap)
            if converted is None:
                skips.uom_unconvertible.add((branch_id, code, row_uom))
                reason = "uom"
        if reason:
            stat["skipped_qty"] += qty
            continue
        company = index.company[branch_id]
        group = groups.setdefault(
            (company, str(loc), branch_id, code),
            {"qty": Decimal(0), "source_qty": Decimal(0), "amount": Decimal(0), "locations": set(),
             "quarantine_of": quarantine_of},
        )  # fmt: skip
        if quarantine_of:
            skips.quarantine_rows += 1
        group["qty"] += converted
        group["source_qty"] += qty
        group["amount"] += dec(row.get("amount_balance"))
        group["locations"].add(name)
    accounting = (cutover - timedelta(days=1)).isoformat()
    out: list[dict[str, Any]] = []
    for (company, loc, branch_id, code), group in sorted(groups.items()):
        # Neraca per cabang dalam satuan ESB baris (sumber); quantity diekspor dalam satuan produk.
        stat, qty, source_qty = skips.by_branch[branch_id], group["qty"], group["source_qty"]
        if qty < 0:
            skips.negative_stock.append({"branch_id": branch_id, "product_code": code, "qty": num(round(qty, 3))})
            stat["skipped_qty"] += source_qty
            continue
        if qty == 0:
            stat["skipped_qty"] += source_qty
            continue
        uom = uom_by_code.get(code, "")
        mapped = umap.get(uom)
        data = {
            "location_key": loc,
            "product_key": xmlid(brand, company, "product", code),
            "product_code": code,
            "quantity": num(round(qty, 3)),
            "uom_esb": uom or None,
            "uom_xmlid": mapped.uom_xmlid if mapped else None,
            "esb_branch_id": branch_id,
            "esb_locations": sorted(group["locations"]),
            "amount": num(round(group["amount"], 2)),
            "accounting_date": accounting,
        }
        if group["quarantine_of"]:
            data["quarantine_of"] = group["quarantine_of"]
        # esb_key tetap `<cabang>|<produk>` juga untuk karantina (kontrak importer); identitas = company×lokasi×produk.
        out.append(record("quant", company, f"{branch_id}|{code}", None, data))
        stat["exported_qty"] += source_qty
    return out


def opening_row_loaded(
    brand: str,
    row: Mapping[str, Any],
    index: BranchIndex,
    bmap: Mapping[int, BranchMap],
    lmap: Mapping[tuple[int, str], str],
) -> bool:
    """Baris saldo awal dari cabang/lokasi yang dimuat langkah `stock` (cabang non-skip terpetakan, lokasi ESB
    `stock`/`quarantine` di location_map atau tanpa entri) -- dipakai juga HPP `stock_opening` (QA G65 P3; G65b:
    karantina diperlakukan sama dengan stok biasa)."""
    branch_id = int(row["branch_id"])
    if location_key(brand, branch_id, bmap.get(branch_id), index) is None:
        return False
    return lmap.get((branch_id, location_name_key(row.get("location_name"))), "stock") in ("stock", "quarantine")


def to_product_qty(qty: Decimal, row_uom: str, product_uom: str, umap: Mapping[str, UomMap]) -> Decimal | None:
    """Qty satuan ESB baris -> satuan produk (temuan W-C G65). Sama -> apa adanya; beda -> hanya bila keduanya di
    uom_map dan sekeluarga (`uom_ratio`: xmlid sama -> ea_factor; G/KG/TON -> faktor berat); selain itu None
    (faktor tidak diketahui -> baris ditolak, tidak dimuat apa adanya)."""
    ratio = uom_ratio(row_uom, product_uom, umap)
    return None if ratio is None else qty * ratio


# ================================================================== histori (baris datar)
@dataclass
class HistoryContext:
    brand: str
    company: str
    branches: BranchIndex
    product_codes: set[str]
    ea_factor: Mapping[str, Decimal]  # product_code -> EA per satuan produk
    menus: MenuIndex
    supplier_codes: frozenset[str] = frozenset()  # G65: kode supplier master (supplier_key)


def _day(value: Any) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)[:10]


def flow_row(ctx: HistoryContext, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """v_esb_flow_daily (EA) -> efn.legacy.flow.daily (satuan produk = EA / ea_factor). Tak terpetakan -> None."""
    branch_id, code = int(row["branch_id"]), str(row["product_code"])
    wh = ctx.branches.wh_key.get(branch_id)
    if not wh or code not in ctx.product_codes:
        return None
    factor = ctx.ea_factor.get(code, Decimal(1))
    values = {c: Decimal(str(row.get(c) or 0)) / factor for c in FLOW_COLUMNS}
    out: dict[str, Any] = {
        "day": _day(row["day"]),
        "branch_key": wh,
        "product_key": xmlid(ctx.brand, ctx.company, "product", code),
        "esb_branch_id": branch_id,
        "esb_product_code": code,
        "esb_product_name": norm_text(row.get("product_name"))[:128] or None,
    }
    out.update({c: num(round(v, 3)) for c, v in values.items()})
    out["outflow"] = num(round(values["sold"] + values["st_out"] + values["ck_out"], 3))
    return out


def sales_daily_row(ctx: HistoryContext, row: Mapping[str, Any]) -> dict[str, Any] | None:
    branch_id, code = int(row["branch_id"]), menu_code_of(row)
    wh = ctx.branches.wh_key.get(branch_id)
    menu = ctx.menus.key.get(code)
    if not wh or not menu:
        return None
    gross, discount = Decimal(str(row.get("gross") or 0)), Decimal(str(row.get("discount") or 0))
    return {
        "day": _day(row["sales_date"]),
        "branch_key": wh,
        "pos_key": ctx.branches.pos_key.get(branch_id),
        "menu_key": menu,
        "esb_branch_id": branch_id,
        "esb_menu_code": code,
        "menu_name": norm_text(row.get("menu_name"))[:128] or None,
        "menu_category": norm_text(row.get("menu_category"))[:128] or None,
        "is_takeaway": bool(row.get("is_takeaway")),
        "qty": num(row.get("qty")),
        "gross": num(gross),
        "discount": num(discount),
        "net": num(gross - discount),
        "cogs": num(row.get("cogs")),
        "trx_count": int(row.get("trx_count") or 0),
    }


def sales_line_row(ctx: HistoryContext, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """fact_esb_sales_line -> efn.legacy.sales.line. Menu tak terpetakan tetap diekspor (menu_key null, §27.8)."""
    wh = ctx.branches.wh_key.get(int(row["branch_id"]))
    if not wh:
        return None
    code = menu_code_of(row)
    return {
        "sales_num": str(row["sales_num"]),
        "line_no": int(row["line_no"]),
        "date": _day(row["sales_date"]),
        "branch_key": wh,
        "menu_key": ctx.menus.key.get(code),
        "esb_menu_code": code,
        "qty": num(row.get("qty")),
        "price": num(row.get("price")),
        "total": num(row.get("total")),
        "discount": num(row.get("discount")),
        "cogs": num(row.get("cogs")),
        "row_type": str(row.get("row_type") or ""),
        "is_takeaway": bool(row.get("is_takeaway")),
    }


def _text(value: Any, size: int) -> str | None:
    return norm_text(value)[:size] or None


def purchase_line_row(ctx: HistoryContext, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """fact_esb_purchase_line -> efn.legacy.purchase.line (§27.13.4/§27.13.6). SEMUA baris/status dimuat; cabang
    skip/HO/tak terpetakan atau gudang PT lain -> `branch_key` null, supplier/produk tanpa master -> `*_key` null.
    Hanya `currency` ≠ IDR -> None (tidak diekspor, dihitung `skipped.currency`)."""
    if not is_idr(row.get("currency")):
        return None
    branch_id = int(row.get("branch_id") or 0)
    wh = ctx.branches.wh_key.get(branch_id)
    supplier, code = str(row.get("supplier_code") or ""), str(row.get("product_code") or "")
    required = row.get("required_date")
    supplier_key = xmlid(ctx.brand, ctx.company, "supplier", supplier) if supplier in ctx.supplier_codes else None
    return {
        "purchase_num": str(row["purchase_num"]),
        "line_no": int(row["line_no"]),
        "date": _day(row["purchase_date"]),
        "date_required": _day(required) if required not in (None, "") else None,
        "branch_key": wh if wh and ctx.branches.wh_company.get(wh) == ctx.company else None,
        "branch_name": _text(row.get("branch_name"), 64),
        "supplier_key": supplier_key,
        "supplier_name": _text(row.get("supplier_name"), 128),
        "product_key": xmlid(ctx.brand, ctx.company, "product", code) if code in ctx.product_codes else None,
        "esb_branch_id": branch_id,
        "esb_supplier_code": supplier,
        "esb_product_code": code,
        "product_name": _text(row.get("product_name"), 128),
        "category_name": _text(row.get("category_name"), 64),
        "sub_category_name": _text(row.get("sub_category_name"), 64),
        "uom_name": _text(row.get("uom"), 16),
        "qty": num(row.get("qty")),
        "receipt_qty": num(row.get("receipt_qty")),
        "pricelist_price": num(row.get("pricelist_price")),
        "price_unit": num(row.get("price")),
        "discount": num(row.get("discount")),
        "vat_value": num(row.get("vat_value")),
        "total": num(row.get("total")),
        "status": _text(row.get("status_name"), 32),
        "map_source": str(row.get("map_source") or "none"),
    }


HISTORY_BUILDERS = {
    "legacy_flow_daily": flow_row,
    "legacy_sales_daily": sales_daily_row,
    "legacy_sales_line": sales_line_row,
    "legacy_purchase_line": purchase_line_row,
}

# SEC-197: kunci unik model Odoo per berkas (beberapa cabang ESB -> satu gudang tujuan wajib dijumlahkan dulu).
# sales.line tidak diagregasi (setiap baris transaksi unik; duplikat = error validator).
AGGREGATE_KEYS: dict[str, tuple[str, ...]] = {
    "legacy_flow_daily": ("day", "branch_key", "product_key"),
    "legacy_sales_daily": ("day", "branch_key", "menu_key", "is_takeaway"),
}
AGGREGATE_SUMS: dict[str, tuple[str, ...]] = {
    "legacy_flow_daily": (*FLOW_COLUMNS, "outflow"),
    "legacy_sales_daily": ("qty", "gross", "discount", "net", "cogs", "trx_count"),
}


def aggregate_rows(file: str, rows: Iterable[dict[str, Any] | None]) -> Iterator[dict[str, Any] | None]:
    """Jumlahkan baris dengan kunci model yang sama (SEC-197), streaming per hari: sumber WAJIB urut hari
    (`source.HISTORY_ORDER`). None (tak terpetakan) diteruskan agar tetap dihitung. Kolom non-jumlah (esb_branch_id,
    esb_*_code, nama, pos_key) = nilai baris pertama, pos_key = yang pertama tidak null.
    Berkas tanpa agregasi (sales.line) diteruskan apa adanya."""
    keys = AGGREGATE_KEYS.get(file)
    if keys is None:
        yield from rows
        return
    sums = AGGREGATE_SUMS[file]
    day_field = keys[0]
    bucket: dict[tuple[Any, ...], dict[str, Any]] = {}
    totals: dict[tuple[Any, ...], dict[str, Decimal]] = {}
    current_day: Any = None

    def flush() -> Iterator[dict[str, Any]]:
        for key in sorted(bucket, key=lambda k: tuple(str(x) for x in k)):
            out = bucket[key]
            for col in sums:
                value = totals[key][col]
                if col == "trx_count":
                    out[col] = int(value)
                else:
                    places = 3 if file == "legacy_flow_daily" or col == "qty" else 2
                    out[col] = num(round(value, places))
            yield out
        bucket.clear()
        totals.clear()

    for row in rows:
        if row is None:
            yield None
            continue
        if current_day is not None and row[day_field] != current_day:
            yield from flush()
        current_day = row[day_field]
        key = tuple(row[k] for k in keys)
        if key not in bucket:
            bucket[key] = dict(row)
            totals[key] = {c: Decimal(str(row[c])) for c in sums}
            continue
        if bucket[key].get("pos_key") is None and row.get("pos_key"):
            bucket[key]["pos_key"] = row["pos_key"]
        for col in sums:
            totals[key][col] += Decimal(str(row[col]))
    yield from flush()
