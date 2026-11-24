"""Transform murni payload ESB -> baris mart ClickHouse (docs/04 §25.5). Tanpa I/O.

Setiap baris berakhir dengan `_version` (versi slice, DateTime64(3) UTC). Kolom = konstanta `*_COLUMNS`; test
`test_ddl.py` memastikan DDL `infra/clickhouse/init/12-g41-esb.sql` memuat semua kolom ini.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from efn_esb.normalize import (
    flag,
    is_fin,
    is_takeaway,
    resolve_product,
    text,
    to_decimal,
    to_float,
    to_int,
)

DIM_BRANCH_COLUMNS = (
    "brand", "company_code", "branch_id", "branch_code", "branch_name", "is_ck", "is_store", "active", "_version",
)  # fmt: skip
DIM_PRODUCT_COLUMNS = (
    "brand", "company_code", "product_code", "product_name", "category", "uom", "is_fin", "batch_to_ea_ratio",
    "active", "_version",
)  # fmt: skip
DIM_MENU_COLUMNS = (
    "brand", "company_code", "menu_code", "menu_name", "menu_category", "menu_category_detail", "is_takeaway",
    "_version",
)  # fmt: skip
DIM_ALIAS_COLUMNS = ("brand", "company_code", "alias_norm", "product_code", "_version")
# supplier_id: G65 (docs/04 §27.13.2, ALTER di 23-g65-esb-purchase-stock.sql) -> join header PO `supplierID` -> kode.
DIM_SUPPLIER_COLUMNS = ("brand", "company_code", "supplier_code", "supplier_name", "active", "supplier_id", "_version")
FACT_SALES_COLUMNS = (
    "brand", "company_code", "branch_id", "sales_date", "sales_num", "line_no", "menu_code", "menu_name", "qty",
    "price", "total", "discount", "cogs", "row_type", "is_takeaway", "_version",
)  # fmt: skip
FACT_PAYMENT_COLUMNS = (
    "brand", "company_code", "branch_id", "branch_code", "sales_date", "payment_type", "payment_method",
    "payment_count", "amount", "mdr", "net", "_version",
)  # fmt: skip
FACT_FIN_SOLD_COLUMNS = ("brand", "company_code", "branch_id", "sales_date", "product_code", "qty_ea", "_version")
FACT_PRODUCTION_COLUMNS = (
    "brand", "company_code", "branch_id", "prod_date", "source", "doc_num", "product_code", "uom", "qty_ea", "is_ck",
    "is_fin", "_version",
)  # fmt: skip
FACT_DELIVERY_COLUMNS = (
    "brand", "company_code", "delivery_date", "doc_num", "origin_branch_id", "dest_branch_id", "product_code", "uom",
    "qty_ea", "is_fin", "_version",
)  # fmt: skip
FACT_TRANSFER_COLUMNS = (
    "brand", "company_code", "transfer_date", "doc_num", "origin_branch_id", "dest_branch_id", "product_code", "uom",
    "qty_ea", "status", "is_fin", "_version",
)  # fmt: skip
FACT_STOCK_COLUMNS = (
    "brand", "company_code", "branch_id", "snapshot_date", "product_code", "uom", "qty", "_version",
)  # fmt: skip

MART_TABLES: dict[str, tuple[str, ...]] = {
    "dim_esb_branch": DIM_BRANCH_COLUMNS,
    "dim_esb_product": DIM_PRODUCT_COLUMNS,
    "dim_esb_menu": DIM_MENU_COLUMNS,
    "dim_esb_product_alias": DIM_ALIAS_COLUMNS,
    "dim_esb_supplier": DIM_SUPPLIER_COLUMNS,
    "fact_esb_sales_line": FACT_SALES_COLUMNS,
    "fact_esb_payment_daily": FACT_PAYMENT_COLUMNS,
    "fact_esb_fin_sold_daily": FACT_FIN_SOLD_COLUMNS,
    "fact_esb_production": FACT_PRODUCTION_COLUMNS,
    "fact_esb_delivery": FACT_DELIVERY_COLUMNS,
    "fact_esb_transfer": FACT_TRANSFER_COLUMNS,
    "fact_esb_stock_snapshot": FACT_STOCK_COLUMNS,
}

# Nama cabang yang bukan toko (prototipe extract.py). Ditambah `ESB_NON_STORE_BRANCHES` per brand.
NON_STORE_KEYWORDS = (
    "HEAD OFFICE", "CENTRAL KITCHEN", "CKWH", "WHPROD", "WAREHOUSE", "STORAGE", "TEMPORARY", "DAMAGE", "EXPIRED",
)  # fmt: skip
GD_IGNORED_STATUS = 33  # goods delivery pending tanpa nomor (prototipe)
TRANSFER_COUNTED_STATUS = frozenset({3, 6})
AUTHORIZED_STATUS = 3

CompanyFor = Callable[[int], str]


@dataclass(frozen=True)
class BomRatio:
    """Rasio BATCH -> EA satu BOM, dari detail simple-manufacturing contoh."""

    product_code: str
    product_name: str
    ratio: float
    uom: str
    sample_doc: str


# ================================================================== dimensi
def transform_branches(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    ck_branches: frozenset[int],
    non_store: frozenset[int],
    version: datetime,
) -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in rows:
        branch_id = to_int(row.get("branchID"))
        if branch_id <= 0:
            continue
        name = text(row.get("branchName"))
        is_ck = branch_id in ck_branches
        keyword = any(word in name.upper() for word in NON_STORE_KEYWORDS)
        is_store = not is_ck and not keyword and branch_id not in non_store
        active = flag(row.get("flagActive", row.get("active", row.get("isActive"))), 1)
        out.append(
            [
                brand,
                company_for(branch_id),
                branch_id,
                text(row.get("branchCode"), 64),
                name,
                int(is_ck),
                int(is_store),
                active,
                version,
            ]
        )
    return out


def store_ids(branch_rows: Iterable[list[Any]], include_inactive: bool) -> list[int]:
    """ID toko dari baris `dim_esb_branch` (kolom DIM_BRANCH_COLUMNS)."""
    return sorted(row[2] for row in branch_rows if row[6] == 1 and (include_inactive or row[7] == 1))


def transform_products(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_code: str,
    ratios_by_code: Mapping[str, float],
    version: datetime,
) -> list[list[Any]]:
    out: dict[str, list[Any]] = {}
    for row in rows:
        code = text(row.get("productCode"), 64)
        if not code:
            continue
        name = text(row.get("productName"))
        out[code] = [
            brand,
            company_code,
            code,
            name,
            text(row.get("categoryName")),
            text(row.get("uomName") or row.get("baseUomName"), 32),
            int(is_fin(code, name)),
            ratios_by_code.get(code),
            flag(row.get("flagActive"), 1),
            version,
        ]
    return list(out.values())


def alias_rows(aliases: Mapping[str, str], brand: str, company_code: str, version: datetime) -> list[list[Any]]:
    return [[brand, company_code, norm, code, version] for norm, code in sorted(aliases.items()) if norm and code]


def transform_menus(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, version: datetime
) -> list[list[Any]]:
    """`/menu/browse` (categoryDesc/categoryDetailDesc) atau baris penjualan (menuCategory/menuCategoryDetail)."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        code = text(row.get("menuCode"), 64)
        if not code:
            continue
        name = text(row.get("menuName"))
        out[code] = [
            brand,
            company_code,
            code,
            name,
            text(row.get("categoryDesc") or row.get("menuCategory")),
            text(row.get("categoryDetailDesc") or row.get("menuCategoryDetail")),
            int(is_takeaway(name)),
            version,
        ]
    return list(out.values())


def sales_menu_version(day: date) -> datetime:
    """Versi baris dim_esb_menu dari penjualan = awal hari penjualan (00:00 UTC), BUKAN versi slice.

    dim_esb_menu = ReplacingMergeTree(_version): versi terbesar menang. Slice master (`/menu/browse`) memakai waktu run,
    jadi run master mana pun setelah hari D selalu mengalahkan baris penjualan hari D (browse = sumber utama), sedangkan
    antar-baris penjualan hari terbaru menang (backfill mundur tidak menimpa nama/kategori yang lebih baru).
    """
    return datetime.combine(day, time.min, tzinfo=UTC)


def transform_sales_menus(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, day: date
) -> list[list[Any]]:
    """Baris `/report/sales-menu-cogs` -> dim_esb_menu untuk menu yang tidak dimuat `/menu/browse` (mis. kode terlaris
    yang hilang dari browse). Hanya baris salesType Sales (atau kosong) dengan `menuCategory` terisi: baris tanpa
    kategori tidak pernah ditulis, sehingga kategori tidak pernah ditimpa string kosong dari jalur ini.
    `company_code` = PT default brand (sama dengan baris master; PT bukan bagian kunci dim_esb_menu).
    """
    usable = [row for row in rows if text(row.get("salesType"), 32) in ("", "Sales") and text(row.get("menuCategory"))]
    return transform_menus(usable, brand, company_code, sales_menu_version(day))


def transform_suppliers(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, version: datetime
) -> list[list[Any]]:
    """Hanya kode, nama perusahaan, status, ID teknis (L-110). Baris tanpa kode dibuang. `supplierID` tidak ada /
    tidak valid -> 0 (nama field di `/supplier` belum diverifikasi, docs/04 §27.13.1 [B])."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        code = text(row.get("supplierCode"), 64)
        if code:
            name = text(row.get("supplierName"))
            supplier_id = max(0, to_int(row.get("supplierID")))
            out[code] = [brand, company_code, code, name, flag(row.get("flagActive"), 1), supplier_id, version]
    return list(out.values())


# ================================================================== penjualan
def _sales_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    # L-113: (menuCode, rowType, qty, price, total); sisa kolom = pemecah seri agar urutan deterministik.
    return (
        text(row.get("menuCode"), 64),
        text(row.get("rowType"), 32),
        to_decimal(row.get("qty"), 3),
        to_decimal(row.get("price"), 2),
        to_decimal(row.get("total"), 2),
        to_decimal(row.get("discountTotal"), 2),
        to_decimal(row.get("cogsTotal"), 2),
        text(row.get("menuName")),
    )


def transform_sales_lines(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_code: str,
    branch_id: int,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """`/report/sales-menu-cogs` -> fact_esb_sales_line. `line_no` = 1..n per salesNum setelah diurutkan (L-113)."""
    by_sales: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        sales_type = text(row.get("salesType"), 32)
        if sales_type and sales_type != "Sales":
            continue
        sales_num = text(row.get("salesNum"), 64)
        if sales_num:
            by_sales[sales_num].append(row)
    out: list[list[Any]] = []
    for sales_num in sorted(by_sales):
        for line_no, row in enumerate(sorted(by_sales[sales_num], key=_sales_sort_key), start=1):
            name = text(row.get("menuName"))
            out.append(
                [
                    brand,
                    company_code,
                    branch_id,
                    day,
                    sales_num,
                    line_no,
                    text(row.get("menuCode"), 64),
                    name,
                    to_decimal(row.get("qty"), 3),
                    to_decimal(row.get("price"), 2),
                    to_decimal(row.get("total"), 2),
                    to_decimal(row.get("discountTotal"), 2),
                    to_decimal(row.get("cogsTotal"), 2),
                    text(row.get("rowType"), 32),
                    int(is_takeaway(name)),
                    version,
                ]
            )
    return out


def transform_fin_sold(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_code: str,
    branch_id: int,
    day: date,
    aliases: Mapping[str, str],
    version: datetime,
) -> list[list[Any]]:
    """`/report/sales-menu-cogs-detail?mode=recap&productName=FIN` -> qty EA per produk FIN (prototipe aggregate.py)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if text(row.get("salesTypeHead") or "Sales") != "Sales":
            continue
        name = text(row.get("productName"))
        if not name or not is_fin("", name):
            continue
        totals[resolve_product(name, aliases)] += to_decimal(row.get("qtyTotal"), 3)
    return [
        [brand, company_code, branch_id, day, code, qty, version] for code, qty in sorted(totals.items()) if qty != 0
    ]


def transform_payments(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    branch_by_code: Mapping[str, int],
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """`/report/sales-payment-summary` (semua cabang) -> per cabang x tipe x metode (duplikat dijumlah)."""
    agg: dict[tuple[str, str, str], list[Decimal | int]] = {}
    for branch in rows:
        code = text(branch.get("branchCode"), 64)
        if not code:
            continue
        for payment in branch.get("payments") or []:
            if not isinstance(payment, Mapping):
                continue
            key = (code, text(payment.get("paymentMethodTypeName"), 64), text(payment.get("paymentMethodName"), 128))
            current = agg.setdefault(key, [0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00")])
            current[0] = int(current[0]) + to_int(payment.get("paymentCount"))
            current[1] = Decimal(current[1]) + to_decimal(payment.get("paymentAmount"), 2)
            current[2] = Decimal(current[2]) + to_decimal(payment.get("mdr"), 2)
            current[3] = Decimal(current[3]) + to_decimal(payment.get("netAfterMDR"), 2)
    out: list[list[Any]] = []
    for (code, ptype, method), (count, amount, mdr, net) in sorted(agg.items()):
        branch_id = branch_by_code.get(code, 0)
        company = company_for(branch_id)
        out.append([brand, company, branch_id, code, day, ptype, method, count, amount, mdr, net, version])
    return out


# ================================================================== produksi & perpindahan
def compute_bom_ratio(detail: Mapping[str, Any], doc_num: str) -> BomRatio | None:
    """Detail simple-manufacturing -> rasio output utama (FIN bila ada, qty terbesar) per 1 batch."""
    batches = to_float(detail.get("manufacturingQty"))
    results = [r for r in detail.get("simpleManufacturingResults") or [] if isinstance(r, Mapping)]
    parsed = [
        (
            text(r.get("productCode"), 64),
            text(r.get("productName")),
            to_float(r.get("qty")),
            text(r.get("uomName"), 32),
        )
        for r in results
    ]
    main = [p for p in parsed if is_fin(p[0], p[1])] or parsed
    if batches <= 0 or not main:
        return None
    code, name, qty, uom = max(main, key=lambda p: p[2])
    if qty <= 0 or not code:
        return None
    return BomRatio(code, name, qty / batches, uom, doc_num)


def _production_rows(
    agg: Mapping[tuple[int, str, str], list[Any]],
    brand: str,
    company_for: CompanyFor,
    ck_branches: frozenset[int],
    day: date,
    source: str,
    version: datetime,
) -> list[list[Any]]:
    out: list[list[Any]] = []
    for (branch_id, doc_num, code), (qty, uom, fin) in sorted(agg.items()):
        out.append(
            [
                brand,
                company_for(branch_id),
                branch_id,
                day,
                source,
                doc_num,
                code,
                uom,
                Decimal(qty).quantize(Decimal("0.001")),
                int(branch_id in ck_branches),
                int(fin),
                version,
            ]
        )
    return out


def transform_simple_mfg(
    rows: Iterable[Mapping[str, Any]],
    ratios: Mapping[int, BomRatio],
    brand: str,
    company_for: CompanyFor,
    ck_branches: frozenset[int],
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Daftar simple-manufacturing (Authorized) x rasio BOM -> qty EA. Tanpa rasio: qty batch, uom `NO_RATIO`."""
    agg: dict[tuple[int, str, str], list[Any]] = {}
    for row in rows:
        if to_int(row.get("statusID"), AUTHORIZED_STATUS) != AUTHORIZED_STATUS:
            continue
        doc_num = text(row.get("simpleManufacturingNum"), 64)
        if not doc_num:
            continue
        branch_id = to_int(row.get("branchID"))
        ratio = ratios.get(to_int(row.get("bomID")))
        batches = to_decimal(row.get("manufacturingQty"), 6)
        if ratio is not None:
            code, name, uom, factor = ratio.product_code, ratio.product_name, ratio.uom, Decimal(repr(ratio.ratio))
        else:
            code, name, uom, factor = text(row.get("bomCode"), 64), text(row.get("bomName")), "NO_RATIO", Decimal(1)
        if not code:
            continue
        fin = is_fin(code, name) or is_fin(code, row.get("bomName"))
        entry = agg.setdefault((branch_id, doc_num, code), [Decimal(0), uom, fin])
        entry[0] += batches * factor
    return _production_rows(agg, brand, company_for, ck_branches, day, "simple_mfg", version)


def transform_production_result(
    details: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    ck_branches: frozenset[int],
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Detail production-result -> qty x convertionQty (satuan dasar; diasumsikan EA seperti prototipe)."""
    agg: dict[tuple[int, str, str], list[Any]] = {}
    for doc in details:
        doc_num = text(doc.get("productionResultNum"), 64)
        if not doc_num:
            continue
        branch_id = to_int(doc.get("branchID"))
        for line in doc.get("productionResultDetails") or []:
            if not isinstance(line, Mapping):
                continue
            code = text(line.get("productCode"), 64)
            if not code:
                continue
            conversion = line.get("convertionQty")
            factor = to_decimal(conversion, 6) if conversion not in (None, "", 0) else Decimal(1)
            uom = "EA" if conversion not in (None, "", 0) else text(line.get("uomName"), 32)
            fin = is_fin(code, line.get("productName"))
            entry = agg.setdefault((branch_id, doc_num, code), [Decimal(0), uom, fin])
            entry[0] += to_decimal(line.get("qty"), 6) * factor
    return _production_rows(agg, brand, company_for, ck_branches, day, "production_result", version)


def transform_goods_delivery(
    details: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Detail goods-delivery -> qty terima (bila ada) atau qty kirim; status 33 / tanpa nomor diabaikan."""
    agg: dict[tuple[str, int, int, str], list[Any]] = {}
    for doc in details:
        doc_num = text(doc.get("goodsDeliveryNum"), 64)
        if not doc_num or to_int(doc.get("statusID")) == GD_IGNORED_STATUS:
            continue
        origin = to_int(doc.get("originBranchID"))
        dest = to_int(doc.get("destinationBranchID"))
        for line in doc.get("goodsDeliveryDetails") or []:
            if not isinstance(line, Mapping):
                continue
            code = text(line.get("productCode"), 64)
            if not code:
                continue
            received = line.get("receiptQty")
            qty = to_decimal(received if received not in (None, "", 0) else line.get("deliveryQty"), 3)
            entry = agg.setdefault(
                (doc_num, origin, dest, code),
                [Decimal("0.000"), text(line.get("uomName"), 32), is_fin(code, line.get("productName"))],
            )
            entry[0] += qty
    return [
        [brand, company_for(origin), day, doc_num, origin, dest, code, uom, qty, int(fin), version]
        for (doc_num, origin, dest, code), (qty, uom, fin) in sorted(agg.items())
    ]


def transform_simple_transfer(
    details: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    aliases: Mapping[str, str],
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Detail simple-transfer status 3 & 6. Nama produk tanpa kode -> alias -> `NAME:<alias>` bila tak terpetakan."""
    agg: dict[tuple[str, int, int, str], list[Any]] = {}
    for doc in details:
        status = to_int(doc.get("statusID"))
        doc_num = text(doc.get("simpleTransferNum"), 64)
        if status not in TRANSFER_COUNTED_STATUS or not doc_num:
            continue
        origin = to_int(doc.get("originBranchID"))
        dest = to_int(doc.get("destinationBranchID"))
        for line in doc.get("simpleTransferDetails") or []:
            if not isinstance(line, Mapping):
                continue
            name = text(line.get("productName"))
            code = text(line.get("productCode"), 64) or (resolve_product(name, aliases) if name else "")
            if not code:
                continue
            entry = agg.setdefault(
                (doc_num, origin, dest, code),
                [Decimal("0.000"), text(line.get("uomName"), 32), is_fin(code, name), status],
            )
            entry[0] += to_decimal(line.get("qty"), 3)
    return [
        [brand, company_for(origin), day, doc_num, origin, dest, code, uom, qty, status, int(fin), version]
        for (doc_num, origin, dest, code), (qty, uom, fin, status) in sorted(agg.items())
    ]


def transform_stock(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_code: str,
    branch_id: int,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """`/inventory/stock-list` (per produk x lokasi) -> jumlah stockQty per produk di cabang."""
    agg: dict[str, list[Any]] = {}
    for row in rows:
        code = text(row.get("productCode"), 64)
        if not code:
            continue
        entry = agg.setdefault(code, [Decimal("0.000"), text(row.get("uomName"), 32)])
        entry[0] += to_decimal(row.get("stockQty"), 3)
    return [[brand, company_code, branch_id, day, code, uom, qty, version] for code, (qty, uom) in sorted(agg.items())]


def doc_aliases(details: Iterable[Mapping[str, Any]], line_key: str) -> list[tuple[str, str]]:
    """Pasangan (kode, nama) dari baris dokumen untuk memperkaya alias (prototipe `learn`)."""
    pairs: list[tuple[str, str]] = []
    for doc in details:
        for line in doc.get(line_key) or []:
            if isinstance(line, Mapping) and line.get("productCode") and line.get("productName"):
                pairs.append((text(line.get("productCode"), 64), text(line.get("productName"))))
    return pairs
