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
from datetime import date
from decimal import Decimal
from typing import Any

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
}  # fmt: skip
# Kunci OPSIONAL histori (nama tampilan, data bisnis -- bukan PII). Export selalu mengisinya (nilai boleh null);
# identik dengan `HISTORY_OPTIONAL_KEYS` importer Odoo (esb_import/mapping.py).
HISTORY_OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "legacy_flow_daily": frozenset({"esb_product_name"}),
    "legacy_sales_daily": frozenset({"menu_name", "menu_category"}),
    "legacy_sales_line": frozenset(),
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
    "cost": frozenset({"product_key", "product_code", "standard_price", "source_uom"}),
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
    "legacy_flow_daily": "legacy_flow_daily.jsonl.gz",
    "legacy_sales_daily": "legacy_sales_daily.jsonl.gz",
    "legacy_sales_line": "legacy_sales_line.jsonl.gz",
}


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


def build_categories(brand: str, company: str, products: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    names = sorted({norm_text(p.get("category")) for p in products} - {""})
    return [
        record("category", company, n, category_key(brand, company, n), {"name": n, "parent_key": None}) for n in names
    ]


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
        cat = norm_text(p.get("category"))
        is_fin = bool(p.get("is_fin"))
        ratio = p.get("batch_to_ea_ratio")
        data = {
            "default_code": code,
            "name": norm_text(p.get("product_name")) or code,
            "categ_key": category_key(brand, company, cat) if cat else None,
            "uom_esb": uom or None,
            "uom_xmlid": mapped.uom_xmlid if mapped else (default_uom if use_default else None),
            "ea_factor": num(mapped.ea_factor) if mapped else None,
            "type": "consu",
            "is_storable": True,
            "purchase_ok": not is_fin,
            "sale_ok": False,
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


def build_menus(
    brand: str,
    company: str,
    current: Iterable[Mapping[str, Any]],
    prices: Iterable[Mapping[str, Any]],
    archive: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], MenuIndex]:
    """Menu aktif (dim_esb_menu terbaru) + harga (dim_esb_menu_price) + menu arsip (hanya di histori, active=False).

    Takeaway (nama berakhiran " T"): digabung ke menu dasar dengan nama sama (tanpa " T") -> baris `alias_of` = xmlid
    menu dasar (importer menambah xmlid kedua ke record yang sama). Tanpa menu dasar -> menu berdiri sendiri.
    """
    price_by_code = {menu_code_of(r): r for r in prices}
    menus: dict[str, dict[str, Any]] = {}
    for row in archive:
        menus[menu_code_of(row)] = {**row, "_archive": True}
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


def build_costs(
    brand: str, company: str, lines: Iterable[Mapping[str, Any]], product_codes: set[str]
) -> list[dict[str, Any]]:
    """standard_price = `lastHpp` komponen BOM terbaru (nilai > 0 terbesar per kode; satuan = satuan komponen [B])."""
    best: dict[str, tuple[Decimal, str]] = {}
    for line in lines:
        code = str(line["product_code"])
        hpp = Decimal(str(line.get("last_hpp") or 0))
        if code in product_codes and hpp > 0 and (code not in best or hpp > best[code][0]):
            best[code] = (hpp, norm_uom(line.get("uom")))
    return [
        record(
            "cost",
            company,
            code,
            None,
            {
                "product_key": xmlid(brand, company, "product", code),
                "product_code": code,
                "standard_price": num(hpp),
                "source_uom": uom or None,
            },
        )
        for code, (hpp, uom) in sorted(best.items())
    ]


# ================================================================== histori (baris datar)
@dataclass
class HistoryContext:
    brand: str
    company: str
    branches: BranchIndex
    product_codes: set[str]
    ea_factor: Mapping[str, Decimal]  # product_code -> EA per satuan produk
    menus: MenuIndex


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


HISTORY_BUILDERS = {
    "legacy_flow_daily": flow_row,
    "legacy_sales_daily": sales_daily_row,
    "legacy_sales_line": sales_line_row,
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
