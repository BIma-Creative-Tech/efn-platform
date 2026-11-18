"""Pemetaan baris bundel migrasi ESB -> nilai Odoo (G43 W2b, docs/04 §27.3/§27.4, `scripts/migration/README.md`).

MURNI: tanpa `import odoo` (diuji pytest di `scripts/migration/tests/test_odoo_import_mapping.py`). Referensi ke record
Odoo di-resolve lewat objek `resolver` yang disuntikkan (sisi Odoo: `importer._Resolver`; test: tiruan).

Konvensi `Plan.owned` (field milik migrasi, §27.4) dan `Plan.create_only` (hanya saat create):
- Many2one = id int atau `False`; Many2many = `tuple` id terurut; angka = `float`; teks = `str`.
- Field khusus: `bom_line_ids` = tuple `(product_id, qty, uom_id)` terurut; `_nb_days` = hari jatuh tempo payment term
  (satu baris 100 %); `payment_method_ids` (create_only pos.config) = tuple kosong -> tanpa metode bayar (milik admin).
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from .efn_mig.bundle import IntegrityError
from .efn_mig.xmlid import MODULE, xmlid

# ------------------------------------------------------------------------------------------------ kontrak bundel
ENVELOPE = frozenset({"entity", "company_code", "esb_key", "xmlid", "data"})
# Urutan entitas master = urutan impor §27.2 (langkah 1-7). Partner: payment_term dulu (direferensikan customer).
MASTER_ORDER = (
    "branch", "uom", "category", "pos_category", "product", "menu", "payment_term", "supplier", "customer", "bom",
    "cost",
)  # fmt: skip
STEP_ENTITIES = {
    "branch": ("branch",),
    "uom": ("uom",),
    "category": ("category", "pos_category"),
    "product": ("product",),
    "menu": ("menu",),
    "partner": ("payment_term", "supplier", "customer"),
    "bom": ("bom",),
    "supplierinfo": (),
    "cost": ("cost",),
}
MASTER_STEPS = tuple(STEP_ENTITIES)
HISTORY_STEPS = {
    "legacy_flow": "legacy_flow_daily",
    "legacy_sales_daily": "legacy_sales_daily",
    "legacy_sales_line": "legacy_sales_line",
}
OPENING_STEPS = ("stock", "opening")
FLOW_COLUMNS = ("sold", "ck_prod", "store_prod", "ck_out", "in_ck", "in_other", "st_in", "st_out")
SALES_DAILY_SUMS = ("qty", "gross", "discount", "net", "cogs", "trx_count")
# Salinan `efn_mig.mapping.HISTORY_KEYS` (dijaga test kesamaan di scripts/migration/tests).
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
HISTORY_DATE_FIELD = {"legacy_flow_daily": "day", "legacy_sales_daily": "day", "legacy_sales_line": "date"}
HISTORY_MODEL = {
    "legacy_flow_daily": "efn.legacy.flow.daily",
    "legacy_sales_daily": "efn.legacy.sales.daily",
    "legacy_sales_line": "efn.legacy.sales.line",
}
_XMLID_ENTITY = {
    "uom": "uom", "category": "category", "pos_category": "pos_category", "product": "product", "menu": "menu",
    "payment_term": "payment_term", "supplier": "supplier", "customer": "customer", "bom": "bom",
}  # fmt: skip
# Referensi antar-entitas di `data`: kunci -> entitas xmlid tujuan (per entitas sumber bila beda).
_REFS = {
    "categ_key": "category",
    "pos_categ_key": "pos_category",
    "product_key": "product",
    "payment_term_key": "payment_term",
    "alias_of": "menu",
    "pos_xmlid": "branch_pos",
}
_PARENT_REF = {"category": "category", "pos_category": "pos_category", "branch": "branch_wh"}


# ------------------------------------------------------------------------------------------------ validasi baris
def row_xmlid_entity(entity: str, data: Mapping[str, Any]) -> str | None:
    """Entitas xmlid (§27.4) untuk baris master; `cost` tanpa xmlid."""
    if entity == "branch":
        return "branch_loc" if data.get("type") == "location" else "branch_wh"
    if entity == "cost":
        return None
    try:
        return _XMLID_ENTITY[entity]
    except KeyError:
        raise IntegrityError(f"entitas master tidak dikenal: {entity}") from None


def company_prefix(brand: str, company: str) -> str:
    """Awalan xmlid lengkap milik brand × company (`efn_mig_esb.<brand>_<company>_`)."""
    return f"{MODULE}.{brand}_{company.lower()}_"


def _check_ref(brand: str, company: str, target_entity: str, value: Any, what: str) -> None:
    if value is None:
        return
    prefix = f"{company_prefix(brand, company)}{target_entity}_"
    if not isinstance(value, str) or not value.startswith(prefix) or len(value) <= len(prefix):
        # Referensi ke company/brand/entitas lain = bundel tidak konsisten (fail closed, cegah lintas PT).
        raise IntegrityError(f"referensi {what} bukan xmlid {target_entity} milik {brand}/{company}")


def check_master_row(brand: str, entity: str, row: Mapping[str, Any], companies: Iterable[str]) -> None:
    """Amplop, company, xmlid dihitung ulang (§27.3: beda -> exit 3), dan referensi `*_key` satu brand × company."""
    if not isinstance(row, Mapping) or set(row) != ENVELOPE:
        raise IntegrityError(f"{entity}: kunci baris master tidak sesuai kontrak")
    if row["entity"] != entity:
        raise IntegrityError(f"{entity}: entitas baris tidak sama dengan berkas")
    company = row["company_code"]
    if company not in set(companies):
        raise IntegrityError(f"{entity}: company_code baris di luar manifest")
    data = row["data"]
    if not isinstance(data, Mapping):
        raise IntegrityError(f"{entity}: data bukan objek")
    esb_key = row["esb_key"]
    if not isinstance(esb_key, str) or not esb_key:
        raise IntegrityError(f"{entity}: esb_key kosong")
    target = row_xmlid_entity(entity, data)
    expected = xmlid(brand, company, target, esb_key) if target else None
    if row["xmlid"] != expected:
        raise IntegrityError(f"{entity}: xmlid tidak sama dengan hasil hitung ulang (§27.4)")
    for key, target_entity in _REFS.items():
        if key in data:
            _check_ref(brand, company, target_entity, data[key], key)
    if "parent_key" in data:
        _check_ref(brand, company, _PARENT_REF.get(entity, entity), data["parent_key"], "parent_key")
    for key in data.get("menu_keys") or []:
        _check_ref(brand, company, "menu", key, "menu_keys")
    for line in data.get("lines") or []:
        if not isinstance(line, Mapping):
            raise IntegrityError("bom: baris komponen bukan objek")
        _check_ref(brand, company, "product", line.get("product_key"), "lines.product_key")
    if entity == "branch" and target == "branch_wh":
        expected_pos = xmlid(brand, company, "branch_pos", esb_key) if data.get("type") == "store" else None
        if data.get("pos_xmlid") != expected_pos:
            raise IntegrityError("branch: pos_xmlid tidak sama dengan hasil hitung ulang")
    if entity == "cost" and data.get("product_key") != xmlid(brand, company, "product", data.get("product_code")):
        raise IntegrityError("cost: product_key tidak sama dengan hasil hitung ulang")


# Kunci OPSIONAL per berkas histori (nama tampilan, data bisnis — bukan PII; diisi bila export menyertakannya).
HISTORY_OPTIONAL_KEYS: dict[str, frozenset[str]] = {
    "legacy_flow_daily": frozenset({"esb_product_name"}),
    "legacy_sales_daily": frozenset({"menu_name", "menu_category"}),
    "legacy_sales_line": frozenset(),
}
# Panjang kolom model histori (Char size) -> dipotong agar satu nama panjang tidak menolak satu irisan.
_NAME_SIZE = {"esb_product_name": 256, "menu_name": 256, "menu_category": 128}


def check_history_row(file: str, row: Mapping[str, Any]) -> None:
    """Kunci wajib persis + kunci opsional yang dikenal saja (§27.7: kunci lain = PII fail closed -> exit 3)."""
    if not isinstance(row, Mapping):
        raise IntegrityError(f"{file}: baris histori bukan objek")
    keys, required = set(row), set(HISTORY_KEYS[file])
    if not required <= keys or keys - required - HISTORY_OPTIONAL_KEYS[file]:
        raise IntegrityError(f"{file}: kunci baris histori tidak sesuai kontrak")


def _name(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return str(value)[: _NAME_SIZE[key]]


# ------------------------------------------------------------------------------------------------ resolver & plan
UOM_MODEL = "uom.uom"


class Resolver(Protocol):
    def res_id(self, xmlid: str | None) -> int | None: ...  # xmlid efn_mig_esb -> id record
    # xmlid standar (mis. uom.product_uom_unit); `model` diisi -> xmlid model lain = None (SEC-203)
    def std_ref(self, xmlid: str | None, model: str | None = None) -> int | None: ...
    def variant_id(self, xmlid: str | None) -> int | None: ...  # xmlid produk/menu (template) -> product.product
    def uom_of(self, xmlid: str | None) -> int | None: ...  # xmlid produk/menu -> uom_id template
    def tag_id(self, name: str) -> int | None: ...  # nama kategori pelanggan -> res.partner.category


@dataclass
class Plan:
    model: str
    owned: dict[str, Any]
    create_only: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def _f(value: Any) -> float:
    return float(Decimal(str(value)))


def _round6(value: Decimal) -> float:
    return float(round(value, 6))


def warehouse_plan(data: Mapping[str, Any], company_id: int) -> Plan:
    code = str(data.get("warehouse_code") or "").strip().upper()
    owned = {
        "name": str(data["name"]),
        "code": code,
        # Field efn_ck_production; dibuang importer bila modul itu tidak terpasang (dicatat di laporan).
        "efn_is_central_kitchen": bool(data.get("is_central_kitchen")),
    }
    missing = [] if 1 <= len(code) <= 5 else ["warehouse_code (1..5 karakter)"]
    return Plan("stock.warehouse", owned, {"company_id": company_id}, missing)


def pos_config_plan(data: Mapping[str, Any], company_id: int, picking_type_id: int | None) -> Plan:
    create_only = {
        "company_id": company_id,
        "picking_type_id": picking_type_id or False,
        # Tanpa metode bayar: milik admin/efn_setup_pos (§27.4), juga mencegah default CE membuat jurnal/metode.
        "payment_method_ids": (),
        "active": bool(data.get("active", True)),
    }
    return Plan("pos.config", {"name": str(data["name"])}, create_only, [] if picking_type_id else ["picking_type_id"])


def location_plan(data: Mapping[str, Any], company_id: int, parent_location_id: int | None) -> Plan:
    owned = {"name": str(data["name"]), "location_id": parent_location_id or False}
    missing = [] if parent_location_id else ["parent_key (gudang induk)"]
    return Plan("stock.location", owned, {"usage": "internal", "company_id": company_id}, missing)


def category_plan(data: Mapping[str, Any], resolver: Resolver, root_id: int | None) -> Plan:
    key = data.get("parent_key")
    parent = resolver.res_id(key) if key else root_id
    missing = ["parent_key"] if key and not parent else []
    return Plan("product.category", {"name": str(data["name"]), "parent_id": parent or False}, {}, missing)


def pos_category_plan(data: Mapping[str, Any], resolver: Resolver) -> Plan:
    key = data.get("parent_key")
    parent = resolver.res_id(key) if key else None
    missing = ["parent_key"] if key and not parent else []
    return Plan("pos.category", {"name": str(data["name"]), "parent_id": parent or False}, {}, missing)


def product_plan(data: Mapping[str, Any], company_id: int, resolver: Resolver, root_categ_id: int | None) -> Plan:
    """Bahan / FIN (storable). `uom_id` hanya saat create (§27.4)."""
    key = data.get("categ_key")
    categ = resolver.res_id(key) if key else root_categ_id
    uom = resolver.std_ref(data.get("uom_xmlid"), UOM_MODEL)
    missing = []
    if key and not categ:
        missing.append("categ_key")
    if not uom:
        missing.append("uom_xmlid")
    owned = {
        "name": str(data["name"]),
        "default_code": str(data["default_code"]),
        "categ_id": categ or False,
        "type": str(data.get("type") or "consu"),
        "is_storable": bool(data.get("is_storable", True)),
        "purchase_ok": bool(data.get("purchase_ok")),
        "sale_ok": bool(data.get("sale_ok")),
        "active": bool(data.get("active", True)),
    }
    # responsible_id: default stock = user pelaku (akun teknis migrasi) -> kosongkan (bukan PIC bisnis; menghindari
    # inkonsistensi company bila akun migrasi bukan anggota PT target).
    create_only = {"company_id": company_id, "uom_id": uom or False, "responsible_id": False}
    if not categ:
        owned.pop("categ_id")  # default CE (All) bila tidak ada kategori & root
    return Plan("product.template", owned, create_only, missing)


def menu_alias_target(data: Mapping[str, Any]) -> str | None:
    """Menu takeaway `" T"` dengan menu dasar -> xmlid menu dasar (importer menambah xmlid kedua, §27.4)."""
    return data.get("alias_of") or None


def menu_plan(data: Mapping[str, Any], company_id: int, resolver: Resolver) -> Plan:
    """Menu POS (tidak storable). Menu arsip (`active=false`) -> produk arsip (ADR-042 §3.3)."""
    key = data.get("pos_categ_key")
    pos_categ = resolver.res_id(key) if key else None
    owned: dict[str, Any] = {
        "name": str(data["name"]),
        "pos_categ_ids": (pos_categ,) if pos_categ else (),
        "available_in_pos": bool(data.get("available_in_pos")),
        "sale_ok": bool(data.get("sale_ok", True)),
        "type": str(data.get("type") or "consu"),
        "active": bool(data.get("active", True)),
    }
    if data.get("list_price") is not None:
        owned["list_price"] = _f(data["list_price"])  # exclude pajak (kontrak); validator memperingatkan bila kosong
    create_only = {
        "company_id": company_id,
        "is_storable": False,
        "default_code": str(data["menu_code"]),
        "responsible_id": False,
    }
    return Plan("product.template", owned, create_only, ["pos_categ_key"] if key and not pos_categ else [])


def payment_term_plan(data: Mapping[str, Any], company_id: int) -> Plan:
    days = data.get("days")
    ok = isinstance(days, int) and not isinstance(days, bool) and days > 0
    owned = {"name": str(data["name"]), "_nb_days": days if ok else 0}
    return Plan("account.payment.term", owned, {"company_id": company_id}, [] if ok else ["days"])


def supplier_plan(data: Mapping[str, Any], company_id: int) -> Plan:
    owned = {
        "ref": str(data["ref"]),
        "name": str(data["name"]),
        "is_company": bool(data.get("is_company", True)),
        "supplier_rank": int(data.get("supplier_rank") or 1),
        "active": bool(data.get("active", True)),
    }
    return Plan("res.partner", owned, {"company_id": company_id})


def customer_plan(data: Mapping[str, Any], company_id: int, resolver: Resolver) -> Plan:
    """Customer B2B (whitelist E4: tanpa alamat/PIC/telepon/NPWP). Nama perorangan = data pribadi -> laporan tanpa
    nama; field PII terenkripsi ADR-036 tidak disentuh (bundel tidak membawanya)."""
    key = data.get("payment_term_key")
    term = resolver.res_id(key) if key else None
    tag = resolver.tag_id(str(data["category_name"])) if data.get("category_name") else None
    owned = {
        "ref": str(data["ref"]),
        "name": str(data["name"]),
        "is_company": bool(data.get("is_company", True)),
        "customer_rank": int(data.get("customer_rank") or 1),
        "active": bool(data.get("active", True)),
        "property_payment_term_id": term or False,
        "category_id": (tag,) if tag else (),
    }
    return Plan("res.partner", owned, {"company_id": company_id}, ["payment_term_key"] if key and not term else [])


# ------------------------------------------------------------------------------------------------ satuan & BoM
def uom_index(uom_rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Baris `uom.jsonl` -> {teks ESB: {"uom_xmlid", "ea_factor"}} (satu company; teks sama lintas company)."""
    out: dict[str, dict[str, Any]] = {}
    for row in uom_rows:
        data = row["data"]
        out[str(data["esb_uom"])] = {"uom_xmlid": data.get("uom_xmlid"), "ea_factor": data.get("ea_factor")}
    return out


def product_info(product_rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Baris `product.jsonl` -> {xmlid produk: {"uom_esb", "uom_xmlid", "ea_factor"}} (konversi qty BoM/HPP)."""
    return {
        str(row["xmlid"]): {
            "uom_esb": row["data"].get("uom_esb"),
            "uom_xmlid": row["data"].get("uom_xmlid"),
            "ea_factor": row["data"].get("ea_factor"),
        }
        for row in product_rows
    }


def _ea(uoms: Mapping[str, Mapping[str, Any]], esb_uom: str | None, fallback: Any = None) -> Decimal:
    raw = (uoms.get(esb_uom) or {}).get("ea_factor") if esb_uom else None
    raw = raw if raw not in (None, "") else fallback
    value = Decimal(str(raw)) if raw not in (None, "") else Decimal(1)
    return value if value > 0 else Decimal(1)


def convert_qty(
    qty: Any,
    from_esb: str | None,
    from_xmlid: str | None,
    product: Mapping[str, Any] | None,
    uoms: Mapping[str, Mapping[str, Any]],
) -> tuple[Decimal, str | None]:
    """Qty dalam satuan ESB `from_esb` -> (qty, xmlid UoM Odoo tujuan | None = UoM produk).

    - satuan sama dengan satuan produk (atau tidak diketahui) -> qty apa adanya, UoM produk;
    - beda teks ESB tetapi xmlid UoM Odoo sama (mis. PACK=6 EA dan EA keduanya `Unit`) -> konversi lewat `ea_factor`;
    - xmlid UoM Odoo lain (mis. G vs KG) -> qty apa adanya dengan UoM itu (Odoo yang mengonversi).
    """
    value = Decimal(str(qty))
    if not from_esb or product is None:
        return value, (from_xmlid if product is None else None)
    if from_esb == product.get("uom_esb"):
        return value, None
    if from_xmlid and from_xmlid == product.get("uom_xmlid"):
        ea_from = _ea(uoms, from_esb)
        ea_product = _ea(uoms, product.get("uom_esb"), product.get("ea_factor"))
        return value * ea_from / ea_product, None
    return value, (from_xmlid or None)


def convert_price(
    price: Any,
    source_uom: str | None,
    product: Mapping[str, Any] | None,
    uoms: Mapping[str, Mapping[str, Any]],
) -> tuple[Decimal, str | None]:
    """HPP per satuan `source_uom` -> (harga, xmlid UoM sumber | None = sudah per UoM produk); kebalikan
    `convert_qty`."""
    value = Decimal(str(price))
    if not source_uom or product is None or source_uom == product.get("uom_esb"):
        return value, None
    source_xmlid = (uoms.get(source_uom) or {}).get("uom_xmlid")
    if source_xmlid and source_xmlid == product.get("uom_xmlid"):
        ea_source = _ea(uoms, source_uom)
        ea_product = _ea(uoms, product.get("uom_esb"), product.get("ea_factor"))
        return value / ea_source * ea_product, None
    return value, (source_xmlid or None)


@dataclass
class BomTarget:
    xmlid: str
    esb_key: str
    plan: Plan


def extra_bom_xmlid(base_xmlid: str, menu_key: str) -> str:
    """xmlid BoM kit tambahan bila satu BoM ESB ditautkan > 1 menu (kit per menu; asumsi W2b, README)."""
    digest = hashlib.sha1(menu_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{base_xmlid}_k{digest}"


def bom_targets(
    row: Mapping[str, Any],
    company_id: int,
    resolver: Resolver,
    products: Mapping[str, Mapping[str, Any]],
    uoms: Mapping[str, Mapping[str, Any]],
) -> list[BomTarget]:
    """`bom.jsonl` -> mrp.bom. `normal` (produksi CK, ADR-018) satu BoM; `phantom` (resep menu) satu kit per menu.

    `product_qty`: `ratio_source == "bom_ratio"` -> rasio BATCH dalam EA dikonversi ke UoM produk (EA / ea_factor);
    selain itu nilai bundel (1). Baris komponen: qty dikonversi ke UoM produk komponen (`convert_qty`); `yield_percent`
    tidak dipakai (CE tanpa field yield; Q-ESB17)."""
    data = row["data"]
    missing: list[str] = []
    lines: list[tuple[int, float, int]] = []
    for line in data.get("lines") or []:
        key = line.get("product_key")
        product_id = resolver.variant_id(key) if key else None
        if not product_id:
            missing.append(f"komponen {line.get('product_code')}")
            continue
        qty, uom_xmlid = convert_qty(
            line.get("qty"), line.get("uom_esb"), line.get("uom_xmlid"), products.get(key), uoms
        )
        uom_id = resolver.std_ref(uom_xmlid, UOM_MODEL) if uom_xmlid else resolver.uom_of(key)
        if qty <= 0 or not uom_id:
            missing.append(f"qty/uom komponen {line.get('product_code')}")
            continue
        lines.append((product_id, _round6(qty), uom_id))
    if not data.get("lines"):
        missing.append("lines (BoM tanpa komponen)")
    bom_type = data.get("type")
    if bom_type == "phantom":
        keys = sorted(data.get("menu_keys") or [])
    elif bom_type == "normal":
        keys = [data["product_key"]] if data.get("product_key") else []
    else:
        keys = []
        missing.append("type")
    if not keys:
        missing.append("menu_keys/product_key")
        keys = [""]
    out = []
    for index, key in enumerate(keys):
        target_missing = list(missing)
        tmpl_id = resolver.res_id(key) if key else None
        uom_id = resolver.uom_of(key) if key else None
        if key and not tmpl_id:
            target_missing.append("produk/menu BoM belum ada di Odoo")
        qty = Decimal(str(data.get("product_qty") or 1))
        if bom_type == "normal" and data.get("ratio_source") == "bom_ratio":
            info = products.get(key) or {}
            qty = qty / _ea(uoms, info.get("uom_esb"), info.get("ea_factor"))
        owned = {
            "code": str(data.get("code") or row["esb_key"]),
            "type": bom_type if bom_type in ("normal", "phantom") else "normal",
            "product_tmpl_id": tmpl_id or False,
            "product_qty": _round6(qty),
            "product_uom_id": uom_id or False,
            "bom_line_ids": tuple(sorted(lines)),
        }
        plan = Plan("mrp.bom", owned, {"company_id": company_id}, target_missing)
        if index == 0:
            out.append(BomTarget(str(row["xmlid"]), str(row["esb_key"]), plan))
        else:
            out.append(BomTarget(extra_bom_xmlid(str(row["xmlid"]), key), f"{row['esb_key']}|{key}", plan))
    return out


# ------------------------------------------------------------------------------------------------ histori
def flow_vals(
    row: Mapping[str, Any], *, company_id: int, warehouse_id: int, product_id: int, brand: str
) -> dict[str, Any]:
    """Baris `legacy_flow_daily` -> spesifikasi bulk `efn.legacy.flow.daily` (`outflow` dihitung ulang helper)."""
    vals = {
        "day": row["day"],
        "company_id": company_id,
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "esb_brand": brand,
        "esb_branch_id": row.get("esb_branch_id"),
        "esb_product_code": row.get("esb_product_code"),
        "esb_product_name": _name(row, "esb_product_name"),
    }
    vals.update({c: row.get(c) for c in FLOW_COLUMNS})
    return vals


def sales_daily_vals(
    row: Mapping[str, Any],
    *,
    company_id: int,
    warehouse_id: int,
    pos_config_id: int | None,
    product_id: int | None,
    brand: str,
) -> dict[str, Any]:
    return {
        "day": row["day"],
        "company_id": company_id,
        "warehouse_id": warehouse_id,
        "pos_config_id": pos_config_id,
        "product_id": product_id,
        "esb_brand": brand,
        "esb_branch_id": row.get("esb_branch_id"),
        "esb_menu_code": row.get("esb_menu_code"),
        "menu_name": _name(row, "menu_name"),
        "menu_category": _name(row, "menu_category"),
        "is_takeaway": bool(row.get("is_takeaway")),
        "qty": row.get("qty"),
        "gross": row.get("gross"),
        "discount": row.get("discount"),
        "net": row.get("net"),
        "cogs": row.get("cogs"),
        "trx_count": row.get("trx_count") or 0,
    }


def sales_line_vals(row: Mapping[str, Any], *, product_id: int | None) -> dict[str, Any]:
    """Baris `legacy_sales_line` (company/gudang diisi irisan `bulk_replace_slice`)."""
    return {
        "date": row["date"],
        "product_id": product_id,
        "esb_menu_code": row.get("esb_menu_code"),
        "sales_num": row.get("sales_num"),
        "line_no": row.get("line_no"),
        "qty": row.get("qty"),
        "price": row.get("price"),
        "total": row.get("total"),
        "discount": row.get("discount"),
        "cogs": row.get("cogs"),
        "row_type": row.get("row_type") or None,
        "is_takeaway": bool(row.get("is_takeaway")),
    }


def aggregate(rows: Iterable[Mapping[str, Any]], key: tuple[str, ...], sums: tuple[str, ...]) -> list[dict[str, Any]]:
    """Gabungkan baris berkunci sama (cabang `location` dipetakan ke gudang induk -> kunci grain bisa ganda).

    Kolom `sums` dijumlah (Decimal; `trx_count` int), kolom lain dari baris pertama. Urutan = urutan kemunculan."""
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        k = tuple(row[c] for c in key)
        current = merged.get(k)
        if current is None:
            merged[k] = dict(row)
            for col in sums:
                value = merged[k].get(col)
                merged[k][col] = int(value or 0) if col == "trx_count" else Decimal(str(value or 0))
            continue
        for col in sums:
            value = row.get(col)
            current[col] += int(value or 0) if col == "trx_count" else Decimal(str(value or 0))
    return list(merged.values())


def month_label(value: str | date) -> str:
    return (value.isoformat() if isinstance(value, date) else str(value))[:7]


def parse_range(entry: Mapping[str, Any]) -> tuple[date, date]:
    """`files[].range` manifest histori `[mulai, akhir)` -> tanggal; tidak ada/tidak valid -> IntegrityError."""
    rng = entry.get("range")
    try:
        start, end = date.fromisoformat(str(rng[0])), date.fromisoformat(str(rng[1]))
    except (TypeError, ValueError, IndexError, KeyError):
        raise IntegrityError(f"{entry.get('name')}: range histori tidak valid") from None
    if start >= end:
        raise IntegrityError(f"{entry.get('name')}: range histori kosong")
    return start, end


def clip_range(start: date, end: date, window: tuple[date, date] | None) -> tuple[date, date] | None:
    """Irisan `[start, end)` dengan jendela `[w0, w1)`; None bila kosong / jendela nonaktif."""
    if window is None:
        return None
    lo, hi = max(start, window[0]), min(end, window[1])
    return (lo, hi) if lo < hi else None


def segment_month_ok(segment_month: str, cutover: date) -> bool:
    """Gerbang §27.5 (5): bulan segmen < bulan cutover, atau = bulan cutover (hari < cutover ditegakkan range)."""
    try:
        year, month = (int(p) for p in str(segment_month).split("-"))
        first = date(year, month, 1)
    except ValueError:
        return False
    return first <= cutover.replace(day=1) and first < cutover
