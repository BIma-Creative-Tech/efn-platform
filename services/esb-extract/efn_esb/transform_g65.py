"""Transform murni G65 (docs/04 §27.13.1-27.13.2, ADR-042 §0b) -> baris DDL `23-g65-esb-purchase-stock.sql`.

Tanpa I/O. Input sudah ter-whitelist (`normalize.PURCHASE_*_FIELDS`, `normalize.STOCK_MOVEMENT_FIELDS`). Setiap baris
berakhir dengan `_version`. `tests/test_g65.py` memastikan DDL memuat semua kolom `G65_TABLES`.

- `purchase_line`: baris recap pembelian (`/report/purchase/purchase-recapitulation/purchase`) + header PO hari yang
  sama (`/purchase/purchase-order`), join `purchaseNum`. Cabang/supplier dari header (`map_source='header'`); tanpa
  header / ID tak terpetakan -> nama tepat-satu (`'name'`); gagal/ambigu -> `branch_id=0`, `supplier_code=''`
  (`'none'`). `map_source` baris = pemetaan TERLEMAH di antara cabang dan supplier (header < name < none).
- `stock_opening`: baris `/report/stock-movement` dengan `transactionType="Beginning"` dan `qtyBalance != 0` = saldo
  AWAL hari D (= saldo akhir D-1) per (cabang, lokasi, produk, satuan).
Angka lewat `to_decimal` (Decimal(str(x)), bukan float); teks bukan angka -> ValueError (slice error, data rusak
terlihat). Arti `discount` (nominal/persen), PPN dalam `total`, dan `amountBalance` bulat = [B], disimpan apa adanya.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from efn_esb.normalize import name_norm, parse_day, text, to_decimal, to_int

FACT_PURCHASE_LINE_COLUMNS = (
    "brand", "company_code", "purchase_date", "purchase_num", "line_no", "required_date", "branch_id", "branch_name",
    "supplier_id", "supplier_code", "supplier_name", "status_id", "status_name", "currency", "product_code",
    "product_name", "category_name", "sub_category_name", "uom", "qty", "receipt_qty", "pricelist_price", "price",
    "discount", "vat_value", "total", "map_source", "_version",
)  # fmt: skip
FACT_STOCK_OPENING_COLUMNS = (
    "brand", "company_code", "opening_date", "branch_id", "branch_code", "location_name", "product_id",
    "product_detail_id", "product_code", "product_name", "uom", "value_per_unit", "qty_balance", "amount_balance",
    "_version",
)  # fmt: skip

G65_TABLES: dict[str, tuple[str, ...]] = {
    "fact_esb_purchase_line": FACT_PURCHASE_LINE_COLUMNS,
    "fact_esb_stock_opening": FACT_STOCK_OPENING_COLUMNS,
}

MAP_HEADER = "header"
MAP_NAME = "name"
MAP_NONE = "none"
_MAP_RANK = {MAP_HEADER: 0, MAP_NAME: 1, MAP_NONE: 2}
OPENING_TYPE = "beginning"  # transactionType baris saldo awal (dibandingkan casefold)

CompanyFor = Callable[[int], str]


def unique_map(pairs: Iterable[tuple[Any, Any]]) -> dict[Any, Any]:
    """(kunci, nilai) -> {kunci: nilai} hanya untuk kunci yang menunjuk TEPAT SATU nilai (ambigu dibuang).
    Kunci/nilai kosong (`''`, 0, None) diabaikan."""
    seen: dict[Any, set[Any]] = defaultdict(set)
    for key, value in pairs:
        if key in ("", 0, None) or value in ("", 0, None):
            continue
        seen[key].add(value)
    return {key: next(iter(values)) for key, values in seen.items() if len(values) == 1}


@dataclass(frozen=True)
class PurchaseLookups:
    """Peta pemetaan cabang/supplier untuk `purchase_line` (dari dim_esb_branch + dim_esb_supplier brand ini)."""

    branch_by_name: Mapping[str, int] = field(default_factory=dict)
    supplier_code_by_id: Mapping[int, str] = field(default_factory=dict)
    supplier_code_by_name: Mapping[str, str] = field(default_factory=dict)
    supplier_id_by_code: Mapping[str, int] = field(default_factory=dict)


def purchase_lookups(branches: Iterable[tuple[int, str]], suppliers: Iterable[tuple[int, str, str]]) -> PurchaseLookups:
    """`branches` = (branch_id, branch_name); `suppliers` = (supplier_id, supplier_code, supplier_name)."""
    supplier_list = [(max(0, to_int(i)), text(c, 64), text(n)) for i, c, n in suppliers]
    return PurchaseLookups(
        branch_by_name=unique_map((name_norm(n), max(0, to_int(b))) for b, n in branches),
        supplier_code_by_id=unique_map((i, c) for i, c, _ in supplier_list),
        supplier_code_by_name=unique_map((name_norm(n), c) for _, c, n in supplier_list),
        supplier_id_by_code=unique_map((c, i) for i, c, _ in supplier_list),
    )


def resolve_purchase_parties(
    row: Mapping[str, Any], header: Mapping[str, Any] | None, lookups: PurchaseLookups
) -> tuple[int, int, str, str]:
    """-> (branch_id, supplier_id, supplier_code, map_source) untuk satu baris recap (docs/04 §27.13.1)."""
    branch_id, supplier_id, supplier_code = 0, 0, ""
    branch_src = supplier_src = MAP_NONE
    if header is not None:
        branch_id = max(0, to_int(header.get("branchID")))
        branch_src = MAP_HEADER if branch_id > 0 else MAP_NONE
        supplier_id = max(0, to_int(header.get("supplierID")))
        supplier_code = lookups.supplier_code_by_id.get(supplier_id, "") if supplier_id else ""
        supplier_src = MAP_HEADER if supplier_code else MAP_NONE
    if branch_src == MAP_NONE:
        branch_id = lookups.branch_by_name.get(name_norm(row.get("branchName")), 0)
        branch_src = MAP_NAME if branch_id else MAP_NONE
    if supplier_src == MAP_NONE:
        supplier_code = lookups.supplier_code_by_name.get(name_norm(row.get("supplierName")), "")
        supplier_src = MAP_NAME if supplier_code else MAP_NONE
        if supplier_code and not supplier_id:
            supplier_id = lookups.supplier_id_by_code.get(supplier_code, 0)
    source = max((branch_src, supplier_src), key=_MAP_RANK.__getitem__)
    return branch_id, supplier_id, supplier_code, source


def _purchase_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    # Pola L-113: (productCode, uomName, qty, price, total); pemecah seri (discount, vatValue, receiptQty, productName).
    return (
        text(row.get("productCode"), 64),
        text(row.get("uomName"), 32),
        to_decimal(row.get("qty"), 3),
        to_decimal(row.get("price"), 4),
        to_decimal(row.get("total"), 2),
        to_decimal(row.get("discount"), 2),
        to_decimal(row.get("vatValue"), 2),
        to_decimal(row.get("receiptQty"), 3),
        text(row.get("productName")),
    )


def transform_purchase_lines(
    recap_rows: Iterable[Mapping[str, Any]],
    header_rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    lookups: PurchaseLookups,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Recap + header PO satu hari -> fact_esb_purchase_line. `purchase_date` = hari slice (§25.4); baris tanpa
    `purchaseNum` dibuang (tidak punya kunci). `line_no` = 1..n per purchaseNum setelah diurutkan (stabil)."""
    headers: dict[str, Mapping[str, Any]] = {}
    for header in header_rows:
        num = text(header.get("purchaseNum"), 64)
        if num and num not in headers:
            headers[num] = header
    by_num: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in recap_rows:
        num = text(row.get("purchaseNum"), 64)
        if num:
            by_num[num].append(row)
    out: list[list[Any]] = []
    for num in sorted(by_num):
        header = headers.get(num)
        for line_no, row in enumerate(sorted(by_num[num], key=_purchase_sort_key), start=1):
            branch_id, supplier_id, supplier_code, source = resolve_purchase_parties(row, header, lookups)
            status_name = text(row.get("statusName"), 64) or (text(header.get("statusName"), 64) if header else "")
            out.append(
                [
                    brand,
                    company_for(branch_id),
                    day,
                    num,
                    line_no,
                    parse_day(row.get("requiredDate")),
                    branch_id,
                    text(row.get("branchName")) or (text(header.get("branchName")) if header else ""),
                    supplier_id,
                    supplier_code,
                    text(row.get("supplierName")) or (text(header.get("supplierName")) if header else ""),
                    to_int(header.get("statusID")) if header else 0,
                    status_name,
                    text(header.get("currencySign"), 16) if header else "",
                    text(row.get("productCode"), 64),
                    text(row.get("productName")),
                    text(row.get("categoryName")),
                    text(row.get("subCategoryName")),
                    text(row.get("uomName"), 32),
                    to_decimal(row.get("qty"), 3),
                    to_decimal(row.get("receiptQty"), 3),
                    to_decimal(row.get("pricelistPrice"), 4),
                    to_decimal(row.get("price"), 4),
                    to_decimal(row.get("discount"), 2),
                    to_decimal(row.get("vatValue"), 2),
                    to_decimal(row.get("total"), 2),
                    source,
                    version,
                ]
            )
    return out


# ================================================================== stock_opening
def check_purchase_day(rows: Sequence[Mapping[str, Any]], day: date, source: str) -> None:
    """SEC-G65-01: setiap baris recap/header wajib `purchaseDate` = hari slice (filter `dateFrom/dateTo` [B]). Selain
    itu (tanggal lain, kosong, tak terbaca) slice error tanpa checkpoint: `purchase_date` = hari slice, jadi baris hari
    lain akan tercatat di tanggal yang salah."""
    other = sum(1 for row in rows if parse_day(row.get("purchaseDate")) != day)
    if other:
        raise ValueError(f"{source}: {other} baris purchaseDate != {day.isoformat()} (filter tanggal tidak berlaku)")


def _row_in_branch(row: Mapping[str, Any], branch_id: int, branch_code: str) -> bool:
    raw_id = row.get("branchID")
    if raw_id not in (None, ""):
        return to_int(raw_id) == branch_id
    code = text(row.get("branchCode"), 64)  # QA P3: tanpa branchID -> cocokkan kode cabang yang diminta
    return bool(code) and bool(branch_code) and code == branch_code


def check_branch(rows: Sequence[Mapping[str, Any]], branch_id: int, branch_code: str) -> None:
    """Setiap baris stock-movement wajib milik cabang yang diminta (filter `branchCode` [B]): `branchID` = cabang, atau
    bila `branchID` tidak ada, `branchCode` = kode diminta; keduanya kosong / cabang lain -> slice error (lebih baik
    gagal daripada saldo cabang lain tercatat di cabang ini)."""
    foreign = sum(1 for row in rows if not _row_in_branch(row, branch_id, branch_code))
    if foreign:
        raise ValueError(f"stock-movement: {foreign} baris bukan cabang {branch_id} (filter branchCode tidak berlaku)")


def check_opening_present(rows: Sequence[Mapping[str, Any]], branch_id: int, active: bool) -> None:
    """SEC-G65-02: ESB mengembalikan baris `Beginning` (qty 0 pun) untuk setiap produk cabang aktif; respons tanpa satu
    pun baris `Beginning` untuk cabang AKTIF = sinyal filter salah -> slice error tanpa checkpoint (bukan sukses 0
    baris). Cabang nonaktif boleh kosong."""
    if active and not any(is_opening_row(row) for row in rows):
        raise ValueError(f"stock-movement: cabang aktif {branch_id} tanpa baris Beginning (respons kosong)")


def is_opening_row(row: Mapping[str, Any]) -> bool:
    return text(row.get("transactionType"), 32).casefold() == OPENING_TYPE


def opening_rows(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Hanya baris `Beginning` dengan `qtyBalance != 0` (baris mutasi hari D dan saldo nol dibuang)."""
    return [row for row in rows if is_opening_row(row) and to_decimal(row.get("qtyBalance"), 3) != 0]


def transform_stock_opening(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_code: str,
    branch_id: int,
    branch_code: str,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Baris `Beginning` -> fact_esb_stock_opening (`opening_date` = D = tanggal cutover). Kunci ganda (tidak
    diharapkan) dijumlah (qty, nilai) agar ReplacingMergeTree tidak diam-diam membuang baris; `value_per_unit` = baris
    pertama. Hasil penjumlahan qty 0 dibuang."""
    agg: dict[tuple[str, str, int, str], list[Any]] = {}
    for row in rows:
        if not is_opening_row(row):
            continue
        key = (
            text(row.get("location")),
            text(row.get("productCode"), 64),
            max(0, to_int(row.get("productDetailID"))),
            text(row.get("UOM"), 32),
        )
        entry = agg.setdefault(
            key,
            [
                max(0, to_int(row.get("productID"))),
                text(row.get("productName")),
                to_decimal(row.get("valuePerUnit"), 4),
                Decimal("0.000"),
                Decimal("0.00"),
            ],
        )
        entry[3] += to_decimal(row.get("qtyBalance"), 3)
        entry[4] += to_decimal(row.get("amountBalance"), 2)
    return [
        [brand, company_code, day, branch_id, branch_code, loc, pid, did, code, name, uom, vpu, qty, amount, version]
        for (loc, code, did, uom), (pid, name, vpu, qty, amount) in sorted(agg.items())
        if qty != 0
    ]
