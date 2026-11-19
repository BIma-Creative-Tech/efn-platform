"""Transform murni entitas migrasi G43 (docs/04 §27.11, ADR-042) -> baris tabel DDL `14-g43-migration.sql`.

Tanpa I/O. Input sudah melewati whitelist/redaksi (`efn_esb.normalize`). Setiap baris berakhir dengan `_version`.
Test `tests/test_ddl_g43.py` memastikan DDL memuat semua kolom `MIG_TABLES`.

P1 (sebelum dry-run master): E1 BOM detail, E2 harga menu, E4 customer B2B, E5 lokasi.
P2 (sebelum cutover, BELUM DIVERIFIKASI terhadap data nyata): E6 stock opname, E7 utang terbuka, E8 piutang terbuka,
E9 COA. E10 (neraca saldo) TIDAK dibangun: endpoint GL/TB di spec ESB berupa POST (esb-extract GET-only) -> ekspor
Finance (Q-ESB21).
"""

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from efn_esb.normalize import flag, parse_day, text, to_decimal, to_int

DIM_BOM_COLUMNS = (
    "brand", "company_code", "bom_id", "bom_code", "bom_name", "bom_type_id", "bom_type_name", "product_code",
    "product_name", "uom", "active", "_version",
)  # fmt: skip
DIM_BOM_LINE_COLUMNS = (
    "brand", "company_code", "bom_id", "line_id", "product_code", "product_name", "qty", "uom", "uom_qty",
    "convertion_qty", "last_hpp", "yield_percent", "tolerance_percent", "_version",
)  # fmt: skip
DIM_MENU_PRICE_COLUMNS = (
    "brand", "company_code", "menu_code", "menu_id", "menu_name", "price", "bom_name", "active", "_version",
)  # fmt: skip
DIM_CUSTOMER_COLUMNS = (
    "brand", "company_code", "customer_code", "customer_name", "customer_category", "payment_due_days", "lock_vat",
    "active", "_version",
)  # fmt: skip
DIM_LOCATION_COLUMNS = ("brand", "company_code", "branch_id", "location_id", "location_name", "_version")
DIM_COA_COLUMNS = ("brand", "company_code", "coa_no", "coa_name", "coa_level", "currency", "active", "_version")
FACT_OPNAME_COLUMNS = (
    "brand", "company_code", "as_of_date", "branch_id", "location_id", "opname_date", "doc_num", "status_id",
    "product_code", "uom", "qty_counted", "qty_system", "hpp", "_version",
)  # fmt: skip
FACT_AP_COLUMNS = (
    "brand", "company_code", "as_of_date", "branch_id", "doc_num", "supplier_code", "supplier_invoice_num",
    "invoice_date", "due_date", "currency", "payable_total", "payment_total", "_version",
)  # fmt: skip
FACT_AR_COLUMNS = (
    "brand", "company_code", "as_of_date", "branch_id", "doc_num", "customer_code", "invoice_date", "due_date",
    "currency", "receivable_total", "payment_total", "advance_total", "_version",
)  # fmt: skip

MIG_TABLES: dict[str, tuple[str, ...]] = {
    "dim_esb_bom": DIM_BOM_COLUMNS,
    "dim_esb_bom_line": DIM_BOM_LINE_COLUMNS,
    "dim_esb_menu_price": DIM_MENU_PRICE_COLUMNS,
    "dim_esb_customer": DIM_CUSTOMER_COLUMNS,
    "dim_esb_location": DIM_LOCATION_COLUMNS,
    "dim_esb_coa": DIM_COA_COLUMNS,
    "fact_esb_stock_opname": FACT_OPNAME_COLUMNS,
    "fact_esb_ap_open": FACT_AP_COLUMNS,
    "fact_esb_ar_open": FACT_AR_COLUMNS,
}

CompanyFor = Callable[[int], str]


# ================================================================== P1
def transform_bom(
    detail: Mapping[str, Any], brand: str, company_code: str, version: datetime
) -> tuple[list[Any] | None, list[list[Any]]]:
    """`/product/bom/{bomID}` -> (baris header, baris komponen). `line_id` = `ID` detail ESB; tanpa ID -> posisi.

    Komponen dengan kode kosong dibuang; header tanpa bomID -> (None, []).
    """
    bom_id = to_int(detail.get("bomID"))
    if bom_id <= 0:
        return None, []
    header = [
        brand,
        company_code,
        bom_id,
        text(detail.get("bomCode"), 64),
        text(detail.get("bomName")),
        to_int(detail.get("bomTypeID")),
        text(detail.get("bomTypeName"), 64),
        text(detail.get("productCode"), 64),
        text(detail.get("productName")),
        text(detail.get("uomName"), 32),
        flag(detail.get("flagActive"), 1),
        version,
    ]
    lines: list[list[Any]] = []
    seen: set[int] = set()
    for position, line in enumerate(detail.get("bomDetails") or [], start=1):
        if not isinstance(line, Mapping):
            continue
        code = text(line.get("productCode"), 64)
        if not code:
            continue
        line_id = to_int(line.get("ID"))
        if line_id <= 0 or line_id in seen:
            line_id = 1_000_000_000 + position  # deterministik per urutan respons
        seen.add(line_id)
        lines.append(
            [
                brand,
                company_code,
                bom_id,
                line_id,
                code,
                text(line.get("productName")),
                to_decimal(line.get("qty"), 6),
                text(line.get("uomName"), 32),
                to_decimal(line.get("uomQty"), 6),
                to_decimal(line.get("convertionQty"), 6),
                to_decimal(line.get("lastHpp"), 4),
                to_decimal(line.get("yieldPercent"), 3),
                to_decimal(line.get("tolerancePercent"), 3),
                version,
            ]
        )
    return header, lines


def transform_menu_prices(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, version: datetime
) -> list[list[Any]]:
    """`/menu/browse-for-product` -> harga (Rupiah, exclude pajak menurut fit-gap; [B]) + tautan BOM per menu."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        code = text(row.get("menuCode"), 64)
        if not code:
            continue
        out[code] = [
            brand,
            company_code,
            code,
            to_int(row.get("menuID")),
            text(row.get("menuName")),
            to_decimal(row.get("price"), 2),
            text(row.get("bomName")),
            flag(row.get("flagActive"), 1),
            version,
        ]
    return list(out.values())


def transform_customers(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, version: datetime
) -> list[list[Any]]:
    """Customer B2B ter-whitelist (`normalize.CUSTOMER_FIELDS`). Baris tanpa kode dibuang."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        code = text(row.get("customerCode"), 64)
        if not code:
            continue
        due = to_int(row.get("paymentDueDays"))
        out[code] = [
            brand,
            company_code,
            code,
            text(row.get("customerName")),
            text(row.get("customerCategoryName"), 128),
            max(0, min(due, 3650)),
            flag(row.get("lockVat"), 0),
            flag(row.get("flagActive"), 1),
            version,
        ]
    return list(out.values())


def transform_locations(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, branch_id: int, version: datetime
) -> list[list[Any]]:
    """`/location?branchID=` -> lokasi per cabang (respons hanya locationID + locationName)."""
    out: dict[int, list[Any]] = {}
    for row in rows:
        location_id = to_int(row.get("locationID"))
        if location_id > 0:
            out[location_id] = [brand, company_code, branch_id, location_id, text(row.get("locationName")), version]
    return [out[key] for key in sorted(out)]


# ================================================================== P2 (belum diverifikasi)
def transform_coa(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, version: datetime
) -> list[list[Any]]:
    """`/accounting/coa` (ter-whitelist COA_FIELDS) -> daftar akun untuk menyusun `coa_map.csv` (Finance)."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        coa_no = text(row.get("coaNo"), 64)
        if not coa_no:
            continue
        out[coa_no] = [
            brand,
            company_code,
            coa_no,
            text(row.get("description")),
            to_int(row.get("coaLevel")),
            text(row.get("currency"), 16),
            flag(row.get("flagActive"), 1),
            version,
        ]
    return [out[key] for key in sorted(out)]


def transform_stock_opname(
    details: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    as_of: date,
    version: datetime,
) -> list[list[Any]]:
    """Detail stock opname -> per dokumen x produk x satuan. `qty` = hitung fisik, `stockQty` = sistem (arti [B])."""
    agg: dict[tuple[str, str, str], list[Any]] = {}
    for doc in details:
        doc_num = text(doc.get("stockOpnameNum"), 64)
        if not doc_num:
            continue
        branch_id = to_int(doc.get("branchID"))
        location_id = to_int(doc.get("locationID"))
        opname_day = parse_day(doc.get("stockOpnameDate")) or as_of
        status = to_int(doc.get("statusID"))
        for line in doc.get("stockOpnameDetails") or []:
            if not isinstance(line, Mapping):
                continue
            code = text(line.get("productCode"), 64)
            if not code:
                continue
            uom = text(line.get("uomName"), 32)
            entry = agg.setdefault(
                (doc_num, code, uom),
                [branch_id, location_id, opname_day, status, Decimal("0.000"), Decimal("0.000"), Decimal("0.0000")],
            )
            entry[4] += to_decimal(line.get("qty"), 3)
            entry[5] += to_decimal(line.get("stockQty"), 3)
            entry[6] = max(entry[6], to_decimal(line.get("hpp"), 4))
    return [
        [brand, company_for(b), as_of, b, loc, day, doc, status, code, uom, counted, system, hpp, version]
        for (doc, code, uom), (b, loc, day, status, counted, system, hpp) in sorted(agg.items())
    ]


def transform_ap_open(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, branch_id: int, as_of: date, version: datetime
) -> list[list[Any]]:
    """`/supplier-payable/invoice-aging?payableDate=` (ter-whitelist AP_FIELDS) -> faktur pembelian terbuka."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        num = text(row.get("purchaseInvoiceNum"), 64)
        if not num:
            continue
        out[num] = [
            brand,
            company_code,
            as_of,
            branch_id,
            num,
            text(row.get("supplierCode"), 64),
            text(row.get("supplierInvoiceNum"), 64),
            parse_day(row.get("invoiceDate")),
            parse_day(row.get("dueDate")),
            text(row.get("currencyName"), 16),
            to_decimal(row.get("payableTotal"), 2),
            to_decimal(row.get("paymentTotal"), 2),
            version,
        ]
    return [out[key] for key in sorted(out)]


def transform_ar_open(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, branch_id: int, as_of: date, version: datetime
) -> list[list[Any]]:
    """`/customer-receivable?type=2&receivableDate=` (ter-whitelist AR_FIELDS) -> faktur penjualan B2B terbuka."""
    out: dict[str, list[Any]] = {}
    for row in rows:
        num = text(row.get("productSalesInvoiceNum"), 64)
        if not num:
            continue
        out[num] = [
            brand,
            company_code,
            as_of,
            branch_id,
            num,
            text(row.get("customerCode"), 64),
            parse_day(row.get("invoiceDate")),
            parse_day(row.get("dueDate")),
            text(row.get("currencyName"), 16),
            to_decimal(row.get("receivableTotal"), 2),
            to_decimal(row.get("paymentTotal"), 2),
            to_decimal(row.get("advanceTotal"), 2),
            version,
        ]
    return [out[key] for key in sorted(out)]
