"""Extractor per entitas (docs/04 §25.4): GET ESB -> payload ter-redaksi (L-110) -> baris mart. I/O hanya lewat `get`.

Setiap extractor menerima `get(path, params)` (biasanya `EsbClient.get`, diinjeksi di test) dan mengembalikan
`SliceResult` berisi raw doc (sudah diredaksi) + baris per tabel mart.
Penulisan ke ClickHouse dilakukan runner di thread utama, checkpoint terakhir.

Bentuk respons diambil dari spec publik (openapi.json) dan sudah diuji GET nyata untuk PB pada 18 Nov 2026, kecuali
`/inventory/stock-list` (lihat README "Belum / belum diverifikasi").
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from efn_esb.config import BrandConfig
from efn_esb.normalize import learn_aliases, redact, redact_supplier, text, to_int
from efn_esb.paging import Getter, fetch_all, page_rows
from efn_esb.planning import SliceTask
from efn_esb.transform import (
    AUTHORIZED_STATUS,
    GD_IGNORED_STATUS,
    TRANSFER_COUNTED_STATUS,
    BomRatio,
    alias_rows,
    compute_bom_ratio,
    doc_aliases,
    transform_branches,
    transform_fin_sold,
    transform_goods_delivery,
    transform_menus,
    transform_payments,
    transform_production_result,
    transform_products,
    transform_sales_lines,
    transform_sales_menus,
    transform_simple_mfg,
    transform_simple_transfer,
    transform_stock,
    transform_suppliers,
)

MASTER_LIMIT = 100
REPORT_LIMIT = 20  # sales-menu-cogs(-detail): limit tetap 20 (> 20 -> HTTP 400 di -detail)
BRANCH_LIMIT = 20  # terverifikasi prototipe; dataCount = baris di halaman ini


@dataclass(frozen=True)
class RawDoc:
    endpoint: str
    doc_key: str
    doc_date: date
    payload: Any  # sudah diredaksi (L-110)


@dataclass
class SliceResult:
    task: SliceTask
    version: datetime
    raw: list[RawDoc] = field(default_factory=list)
    tables: dict[str, list[list[Any]]] = field(default_factory=dict)
    bom_ratios: dict[int, BomRatio] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    calls: int = 0

    @property
    def rows(self) -> int:
        return sum(len(rows) for rows in self.tables.values())


@dataclass
class BrandRuntime:
    """Status per brand selama satu proses: cabang, alias, rasio BOM (dibagi antar worker, dilindungi lock)."""

    cfg: BrandConfig
    max_pages: int = 500
    include_inactive: bool = True
    # G43 E6: stock opname ditarik untuk [hari - opname_window_days, hari] (CLI `snapshot --opname-window`).
    opname_window_days: int = 3
    branch_rows: list[list[Any]] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    ratios: dict[int, BomRatio] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def branch_by_code(self) -> dict[str, int]:
        return {row[3]: row[2] for row in self.branch_rows if row[3]}

    def stores(self, include_inactive: bool | None = None) -> list[int]:
        inactive = self.include_inactive if include_inactive is None else include_inactive
        return sorted(row[2] for row in self.branch_rows if row[6] == 1 and (inactive or row[7] == 1))

    def ratio_snapshot(self) -> dict[int, BomRatio]:
        with self.lock:
            return dict(self.ratios)

    def alias_snapshot(self) -> dict[str, str]:
        with self.lock:
            return dict(self.aliases)


class CountingGetter:
    """Membungkus `get` untuk menghitung panggilan logis per slice."""

    def __init__(self, get: Getter) -> None:
        self._get = get
        self.calls = 0

    def __call__(self, path: str, params: dict[str, Any]) -> Any:
        self.calls += 1
        return self._get(path, params)


def _detail(get: Getter, base: str, num: str) -> dict[str, Any]:
    result = get(f"{base}/{quote(num, safe='')}", {})
    return redact(result) if isinstance(result, dict) else {}


def _list(get: Getter, path: str, params: Mapping[str, Any], rt: BrandRuntime, **kwargs: Any) -> list[dict[str, Any]]:
    rows = fetch_all(get, path, params, max_pages=rt.max_pages, **kwargs)
    return [redact(row) for row in rows]


# ================================================================== per brand (snapshot harian)
def extract_branches(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    rows = _list(get, "/branch/list/user", {}, rt, limit=BRANCH_LIMIT, limit_params=("limit", "pageSize"))
    cfg = rt.cfg
    dims = transform_branches(rows, cfg.key, cfg.company_for, cfg.ck_branches, cfg.non_store_branches, version)
    result = SliceResult(task, version, tables={"dim_esb_branch": dims})
    result.raw.append(RawDoc("branch/list/user", task.day.isoformat(), task.day, rows))
    return result


def extract_master(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg = rt.cfg
    products = _list(get, "/product", {}, rt, limit=MASTER_LIMIT)
    boms = _list(get, "/product/bom", {}, rt, limit=MASTER_LIMIT)
    menus = _list(get, "/menu/browse", {}, rt, limit=MASTER_LIMIT)
    # Supplier: whitelist kode/nama/status SEBELUM apa pun disimpan (L-110; respons memuat kontak & alamat).
    supplier_rows = fetch_all(get, "/supplier", {}, limit=MASTER_LIMIT, max_pages=rt.max_pages)
    suppliers = [redact_supplier(row) for row in supplier_rows]
    aliases = learn_aliases((p.get("productCode"), p.get("productName")) for p in products)
    ratio_by_code = {r.product_code: r.ratio for r in rt.ratio_snapshot().values()}
    result = SliceResult(task, version, aliases=aliases)
    result.tables = {
        "dim_esb_product": transform_products(products, cfg.key, cfg.company_code, ratio_by_code, version),
        "dim_esb_menu": transform_menus(menus, cfg.key, cfg.company_code, version),
        "dim_esb_supplier": transform_suppliers(suppliers, cfg.key, cfg.company_code, version),
        "dim_esb_product_alias": alias_rows(aliases, cfg.key, cfg.company_code, version),
    }
    key = task.day.isoformat()
    result.raw = [
        RawDoc("product", key, task.day, products),
        RawDoc("product/bom", key, task.day, boms),
        RawDoc("menu/browse", key, task.day, menus),
        RawDoc("supplier", key, task.day, suppliers),
    ]
    return result


# ================================================================== per toko x hari
def extract_sales(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    day, branch = task.day.isoformat(), task.branch_id
    params = {"salesDate": day, "branchID": branch, "salesType": "Sales"}
    rows = _list(get, "/report/sales-menu-cogs", params, rt, limit=REPORT_LIMIT, send_limit=False)
    company = rt.cfg.company_for(branch)
    lines = transform_sales_lines(rows, rt.cfg.key, company, branch, task.day, version)
    # Pelengkap master menu: kode yang tidak ada di /menu/browse (versi = awal hari penjualan).
    menus = transform_sales_menus(rows, rt.cfg.key, rt.cfg.company_code, task.day)
    result = SliceResult(task, version, tables={"fact_esb_sales_line": lines, "dim_esb_menu": menus})
    result.raw.append(RawDoc("report/sales-menu-cogs", f"{day}|{branch}", task.day, rows))
    return result


def extract_fin_sold(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    day, branch = task.day.isoformat(), task.branch_id
    params = {"salesDate": day, "branchID": branch, "mode": "recap", "productName": "FIN"}
    rows = _list(get, "/report/sales-menu-cogs-detail", params, rt, limit=REPORT_LIMIT)
    company = rt.cfg.company_for(branch)
    facts = transform_fin_sold(rows, rt.cfg.key, company, branch, task.day, rt.alias_snapshot(), version)
    result = SliceResult(task, version, tables={"fact_esb_fin_sold_daily": facts})
    result.raw.append(RawDoc("report/sales-menu-cogs-detail", f"{day}|{branch}", task.day, rows))
    return result


def extract_stock(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    day, branch = task.day.isoformat(), task.branch_id
    rows = _list(get, "/inventory/stock-list", {"stockDate": day, "branchID": branch}, rt, limit=MASTER_LIMIT)
    facts = transform_stock(rows, rt.cfg.key, rt.cfg.company_for(branch), branch, task.day, version)
    result = SliceResult(task, version, tables={"fact_esb_stock_snapshot": facts})
    result.raw.append(RawDoc("inventory/stock-list", f"{day}|{branch}", task.day, rows))
    return result


# ================================================================== per brand x hari
def extract_payment(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    day = task.day.isoformat()
    path = "/report/sales-payment-summary"
    first = get(path, {"salesDate": day})
    count = first.get("count") if isinstance(first, Mapping) else None
    if isinstance(count, int) and not isinstance(count, bool) and count > len(page_rows(first)):
        # Defensif (belum pernah terlihat): ringkasan ternyata berhalaman -> ambil ulang semua halaman.
        rows = fetch_all(get, path, {"salesDate": day}, limit=REPORT_LIMIT, send_limit=False, max_pages=rt.max_pages)
    else:  # satu panggilan memuat semua cabang (terverifikasi 17 Nov 2026)
        rows = page_rows(first)
    rows = [redact(row) for row in rows]
    cfg = rt.cfg
    facts = transform_payments(rows, cfg.key, cfg.company_for, rt.branch_by_code, task.day, version)
    result = SliceResult(task, version, tables={"fact_esb_payment_daily": facts})
    result.raw.append(RawDoc("report/sales-payment-summary", day, task.day, rows))
    return result


def extract_production(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg, day = rt.cfg, task.day.isoformat()
    window = {"dateFrom": day, "dateTo": day, "statusID": AUTHORIZED_STATUS}
    result = SliceResult(task, version)
    sm = _list(get, "/production/simple-manufacturing", window, rt, limit=MASTER_LIMIT)
    result.raw.append(RawDoc("production/simple-manufacturing", day, task.day, sm))
    known = rt.ratio_snapshot()
    samples: dict[int, str] = {}
    for row in sm:
        bom_id, num = to_int(row.get("bomID")), text(row.get("simpleManufacturingNum"), 64)
        if bom_id and num and bom_id not in known:
            samples.setdefault(bom_id, num)
    for bom_id, num in sorted(samples.items()):
        detail = _detail(get, "/production/simple-manufacturing", num)
        result.raw.append(RawDoc("production/simple-manufacturing/{num}", num, task.day, detail))
        ratio = compute_bom_ratio(detail, num)
        if ratio is not None:
            result.bom_ratios[bom_id] = ratio
    if result.bom_ratios:
        with rt.lock:
            rt.ratios.update(result.bom_ratios)
    ratios = {**known, **result.bom_ratios}
    pr_list = _list(get, "/production/production-result", window, rt, limit=MASTER_LIMIT)
    result.raw.append(RawDoc("production/production-result", day, task.day, pr_list))
    pr_details: list[dict[str, Any]] = []
    for row in pr_list:
        num = text(row.get("productionResultNum"), 64)
        if not num:
            continue
        detail = _detail(get, "/production/production-result", num)
        result.raw.append(RawDoc("production/production-result/{num}", num, task.day, detail))
        pr_details.append({**row, **detail})
    facts = transform_simple_mfg(sm, ratios, cfg.key, cfg.company_for, cfg.ck_branches, task.day, version)
    facts += transform_production_result(pr_details, cfg.key, cfg.company_for, cfg.ck_branches, task.day, version)
    pairs = doc_aliases(pr_details, "productionResultDetails")
    pairs += [(r.product_code, r.product_name) for r in result.bom_ratios.values()]
    result.aliases = learn_aliases(pairs)
    result.tables = {
        "fact_esb_production": facts,
        "dim_esb_product_alias": alias_rows(result.aliases, cfg.key, cfg.company_code, version),
    }
    return result


def extract_delivery(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg, day = rt.cfg, task.day.isoformat()
    result = SliceResult(task, version)
    listed = _list(get, "/inventory/goods-delivery", {"dateFrom": day, "dateTo": day}, rt, limit=MASTER_LIMIT)
    result.raw.append(RawDoc("inventory/goods-delivery", day, task.day, listed))
    details: list[dict[str, Any]] = []
    for row in listed:
        num = text(row.get("goodsDeliveryNum"), 64)
        if not num or to_int(row.get("statusID")) == GD_IGNORED_STATUS:
            continue
        detail = _detail(get, "/inventory/goods-delivery", num)
        result.raw.append(RawDoc("inventory/goods-delivery/{num}", num, task.day, detail))
        details.append({**row, **detail})
    result.aliases = learn_aliases(doc_aliases(details, "goodsDeliveryDetails"))
    result.tables = {
        "fact_esb_delivery": transform_goods_delivery(details, cfg.key, cfg.company_for, task.day, version),
        "dim_esb_product_alias": alias_rows(result.aliases, cfg.key, cfg.company_code, version),
    }
    return result


def extract_transfer(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    cfg, day = rt.cfg, task.day.isoformat()
    result = SliceResult(task, version)
    listed = _list(get, "/simple-transfer", {"dateFrom": day, "dateTo": day}, rt, limit=MASTER_LIMIT)
    result.raw.append(RawDoc("simple-transfer", day, task.day, listed))
    details: list[dict[str, Any]] = []
    for row in listed:
        num = text(row.get("simpleTransferNum"), 64)
        status = row.get("statusID")
        if not num or (status is not None and to_int(status) not in TRANSFER_COUNTED_STATUS):
            continue
        detail = _detail(get, "/simple-transfer", num)
        result.raw.append(RawDoc("simple-transfer/{num}", num, task.day, detail))
        details.append({**row, **detail})
    facts = transform_simple_transfer(details, cfg.key, cfg.company_for, rt.alias_snapshot(), task.day, version)
    result.tables = {"fact_esb_transfer": facts}
    return result


Extractor = Callable[[BrandRuntime, SliceTask, datetime, Getter], SliceResult]
EXTRACTORS: dict[str, Extractor] = {
    "branch": extract_branches,
    "master": extract_master,
    "sales": extract_sales,
    "fin_sold": extract_fin_sold,
    "payment": extract_payment,
    "production": extract_production,
    "delivery": extract_delivery,
    "transfer": extract_transfer,
    "stock": extract_stock,
}


def extractor_for(entity: str) -> Extractor:
    """Extractor G41 atau entitas migrasi G43 (`efn_esb.extract_mig`, import tertunda: hindari import melingkar)."""
    if entity in EXTRACTORS:
        return EXTRACTORS[entity]
    from efn_esb.extract_mig import MIG_EXTRACTORS

    return MIG_EXTRACTORS[entity]


def run_slice(rt: BrandRuntime, task: SliceTask, version: datetime, get: Getter) -> SliceResult:
    counter = CountingGetter(get)
    result = extractor_for(task.entity)(rt, task, version, counter)
    result.calls = counter.calls
    return result
