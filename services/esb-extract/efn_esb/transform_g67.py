"""Transform murni G67 (docs/04 §31.1, §31.5; ADR-040 amandemen G67) -> baris DDL `24-g67-esb-pl-waste-fillrate.sql`.

Tanpa I/O. Input sudah ter-whitelist (`normalize.PL_FIELDS`, `ITEM_JOURNAL_*`, `GTR_*`; detail goods-delivery = payload
G41 ter-redaksi). Setiap baris berakhir dengan `_version`. `tests/test_g67.py` memastikan DDL memuat semua kolom
`G67_TABLES`. Angka lewat `to_decimal` (Decimal(str(x)), bukan float); teks bukan angka -> ValueError (slice error).

Fail closed (docs/04 §31 "[B]"): struktur respons yang tidak sesuai fakta probe (label bulan tak terbaca, prefix
cabang/`area.id` beda, tahun beda) -> ValueError -> slice `error` tanpa checkpoint, bukan tebakan.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from efn_esb.normalize import PL_SECTIONS, PL_SUMMARY_POS, alias_norm, flag, parse_day, text, to_decimal, to_int
from efn_esb.transform import GD_IGNORED_STATUS
from efn_esb.transform_g65 import unique_map

FACT_PROFIT_LOSS_COLUMNS = (
    "brand", "company_code", "period_month", "scope", "branch_id", "line_type", "section", "account_no", "account_key",
    "account_name", "amount", "slice_year", "source_lines", "_version",
)  # fmt: skip
FACT_ITEM_JOURNAL_LINE_COLUMNS = (
    "brand", "company_code", "journal_date", "journal_num", "detail_id", "line_no", "branch_id", "location_id",
    "location_name", "status_id", "status_name", "template_name", "purpose_id", "purpose_name", "product_id",
    "product_detail_id", "product_name", "uom", "qty", "hpp", "value", "_version",
)  # fmt: skip
FACT_TRANSFER_REQUEST_LINE_COLUMNS = (
    "brand", "company_code", "transfer_date", "transfer_num", "requester_branch_id", "supplier_branch_id", "status_id",
    "status_name", "category_type", "bypass_gd", "finished", "purchase_request_num", "product_id",
    "product_detail_id", "product_code", "product_name", "uom", "request_qty", "qty", "pending_qty", "available_qty",
    "stock_qty", "_version",
)  # fmt: skip
FACT_DELIVERY_REF_LINE_COLUMNS = (
    "brand", "company_code", "delivery_date", "doc_num", "reference_num", "trans_type", "ref_date", "origin_branch_id",
    "dest_branch_id", "product_code", "uom", "qty_sent", "qty_received", "_version",
)  # fmt: skip
DIM_PRODUCT_ID_COLUMNS = ("brand", "company_code", "product_id", "product_code", "source", "_version")

G67_TABLES: dict[str, tuple[str, ...]] = {
    "fact_esb_profit_loss": FACT_PROFIT_LOSS_COLUMNS,
    "fact_esb_item_journal_line": FACT_ITEM_JOURNAL_LINE_COLUMNS,
    "fact_esb_transfer_request_line": FACT_TRANSFER_REQUEST_LINE_COLUMNS,
    "fact_esb_delivery_ref_line": FACT_DELIVERY_REF_LINE_COLUMNS,
    "dim_esb_product_id": DIM_PRODUCT_ID_COLUMNS,
}

SCOPE_BRANCH = "branch"
SCOPE_BRAND = "brand"
LINE_ACCOUNT = "account"
LINE_SUMMARY = "summary"
SOURCE_GTR = "gtr"
# [B] arah GTR (docs/04 §31.1.3): origin = cabang PEMASOK (CK/gudang), destination = cabang PEMINTA (outlet). Bila
# backfill membuktikan terbalik, ubah konstanta ini (kontrak kolom requester/supplier tidak berubah) dan catat.
GTR_ORIGIN_IS_SUPPLIER = True
TEMPLATE_MAX = 128

_MONTHS = {
    name: number
    for number, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
         "november", "december"),
        start=1,
    )
}  # fmt: skip
# "<branchID>-<Bulan> <Tahun>" (prefix opsional di tingkat regex; wajib untuk scope cabang, lihat parse_period_label).
_PERIOD_RE = re.compile(r"^\s*(?:(\d+)\s*-\s*)?([A-Za-z]+)\s+(\d{4})\s*$")
_DIGITS = re.compile(r"\d+")
_CENT = Decimal("0.01")
_ZERO3 = Decimal("0.000")

CompanyFor = Callable[[int], str]


# ================================================================== profit_loss
def parse_period_label(label: Any, branch_id: int | None, year: int) -> date:
    """`"12-January 2026"` -> 2026-01-01. `branch_id` terisi (scope cabang) -> prefix WAJIB = branch_id. Nama bulan
    Inggris saja. Label tak terbaca / prefix beda / tahun != `year` -> ValueError (fail closed)."""
    match = _PERIOD_RE.match(text(label, 64))
    if match is None:
        raise ValueError("label bulan P&L tak terbaca")
    prefix, name, year_s = match.groups()
    if branch_id is not None and (prefix is None or int(prefix) != branch_id):
        raise ValueError("prefix label bulan P&L != cabang diminta")
    month = _MONTHS.get(name.lower())
    if month is None:
        raise ValueError("nama bulan P&L tidak dikenal")
    if int(year_s) != year:
        raise ValueError("tahun label P&L != tahun diminta")
    return date(year, month, 1)


def account_key(account_no: str) -> str:
    """`"4 1 10 10"` -> `"4.1.10.10"` (segmen angka digabung titik)."""
    return ".".join(_DIGITS.findall(account_no))


def pl_detail_values(details: Any, branch_id: int | None, year: int, month: int) -> dict[date, Decimal]:
    """`details[]` satu baris akun/summary -> {bulan: nilai}. Bulan < M dari `periods`, bulan M dari `area.value`.

    Scope cabang (`branch_id` terisi): setiap detail wajib `area.id` = cabang dan `area.value` ada. Scope brand: detail
    (bisa lebih dari satu area) dijumlah. Bulan > M, label ganda dalam satu detail, atau `periods` bulan M berbeda
    dengan `area.value` -> ValueError."""
    if details is None:
        return {}
    if not isinstance(details, list):
        raise ValueError("details P&L bukan list")
    out: dict[date, Decimal] = defaultdict(lambda: Decimal("0.00"))
    current = date(year, month, 1)
    for detail in details:
        if not isinstance(detail, Mapping):
            raise ValueError("detail P&L bukan objek")
        area = detail.get("area")
        if branch_id is not None:
            if not isinstance(area, Mapping) or to_int(area.get("id"), -1) != branch_id:
                raise ValueError("area.id P&L != cabang diminta")
            if area.get("value") is None:
                raise ValueError("area.value (bulan M) P&L tidak ada")
        periods = detail.get("periods") or []
        if not isinstance(periods, list):
            raise ValueError("periods P&L bukan list")
        values: dict[date, Decimal] = {}
        for period in periods:
            if not isinstance(period, Mapping):
                raise ValueError("period P&L bukan objek")
            when = parse_period_label(period.get("key"), branch_id, year)
            if when > current:
                raise ValueError("periods P&L memuat bulan setelah M")
            if when in values:
                raise ValueError("label bulan P&L ganda")
            values[when] = to_decimal(period.get("value"), 2)
        if isinstance(area, Mapping) and area.get("value") is not None:
            value_m = to_decimal(area.get("value"), 2)
            if current in values and values[current] != value_m:
                raise ValueError("periods bulan M != area.value")
            values[current] = value_m
        for when, value in values.items():
            out[when] += value
    return dict(out)


def _summary_has_details(details: Any) -> bool:
    return (
        isinstance(details, list)
        and bool(details)
        and all(isinstance(d, Mapping) and ("periods" in d or "area" in d) for d in details)
    )


def transform_profit_loss(
    payload: Mapping[str, Any],
    brand: str,
    company_code: str,
    branch_id: int,
    year: int,
    month: int,
    version: datetime,
) -> list[list[Any]]:
    """Respons month-to-month (ter-whitelist) -> fact_esb_profit_loss. `branch_id` 0 = total brand (scope 'brand').

    - Seksi top-level (`PL_SECTIONS`) = list baris akun -> `line_type='account'`; akun sama dua kali dalam satu seksi x
      bulan dijumlah, `source_lines` = jumlah baris sumber (> 1 = `dup_account` di v_esb_g67_quality).
    - `summaries.<pos>` -> `line_type='summary'`, `account_no=''`: nilai per bulan dari `details` bila strukturnya sama
      dengan baris akun [B], jika tidak `total` untuk bulan M saja.
    - Tanda angka apa adanya (konvensi tanda beban [B], Q-ESB34)."""
    if not 1 <= month <= 12:
        raise ValueError("month P&L di luar 1..12")
    scope = SCOPE_BRANCH if branch_id > 0 else SCOPE_BRAND
    expected = branch_id if branch_id > 0 else None
    agg: dict[tuple[str, str, str], list[Any]] = {}

    def add(key: tuple[str, str, str], acc_key: str, name: str, values: Mapping[date, Decimal]) -> None:
        entry = agg.get(key)
        if entry is None:
            agg[key] = [acc_key, name, dict(values), 1]
            return
        entry[3] += 1
        for when, value in values.items():
            entry[2][when] = entry[2].get(when, Decimal("0.00")) + value

    for section in PL_SECTIONS:
        rows = payload.get(section)
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError(f"seksi P&L {section} bukan list baris akun")
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"baris akun P&L {section} bukan objek")
            account_no = " ".join(text(row.get("account"), 64).split())
            values = pl_detail_values(row.get("details"), expected, year, month)
            add((LINE_ACCOUNT, section, account_no), account_key(account_no), text(row.get("description")), values)
    summaries = payload.get("summaries")
    if summaries is not None and not isinstance(summaries, Mapping):
        raise ValueError("summaries P&L bukan objek")
    for pos in PL_SUMMARY_POS:
        item = summaries.get(pos) if isinstance(summaries, Mapping) else None
        if item is None:
            continue
        if not isinstance(item, Mapping):
            raise ValueError(f"summary P&L {pos} bukan objek")
        details = item.get("details")
        if _summary_has_details(details):
            values = pl_detail_values(details, expected, year, month)
        elif item.get("total") is not None:
            values = {date(year, month, 1): to_decimal(item.get("total"), 2)}
        else:
            values = {}
        add((LINE_SUMMARY, pos, ""), "", text(item.get("description")), values)
    out: list[list[Any]] = []
    for (line_type, section, account_no), (acc_key, name, values, lines) in sorted(agg.items()):
        for when in sorted(values):
            out.append(
                [
                    brand, company_code, when, scope, branch_id, line_type, section, account_no, acc_key, name,
                    values[when], year, lines, version,
                ]
            )  # fmt: skip
    return out


# ================================================================== item_journal / transfer_request: cek
def check_doc_day(rows: Sequence[Mapping[str, Any]], day: date, date_key: str, source: str) -> None:
    """Setiap baris list wajib `<date_key>` = hari slice (filter dateFrom/dateTo [B]); selain itu slice error
    (tanggal fakta = hari slice, baris hari lain akan tercatat salah)."""
    other = sum(1 for row in rows if parse_day(row.get(date_key)) != day)
    if other:
        raise ValueError(f"{source}: {other} baris {date_key} != {day.isoformat()} (filter tanggal tidak berlaku)")


def check_detail_num(detail: Mapping[str, Any], num_key: str, num: str) -> None:
    """Detail yang memuat nomor dokumen lain dari yang diminta -> error (respons salah alamat)."""
    got = text(detail.get(num_key), 64)
    if got and got != num:
        raise ValueError(f"detail {num_key} != nomor diminta")


# ================================================================== item_journal
def _journal_sort_key(line: Mapping[str, Any]) -> tuple[Any, ...]:
    # Pola L-113: (productID, productDetailID, purposeID, qty, hpp).
    return (
        to_int(line.get("productID")),
        to_int(line.get("productDetailID")),
        to_int(line.get("purposeID")),
        to_decimal(line.get("qty"), 3),
        to_decimal(line.get("hpp"), 4),
    )


def journal_line_keys(lines: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, Mapping[str, Any]]]:
    """-> [(detail_id, line_no, baris)]. Semua `ID` > 0 dan unik dalam dokumen -> (ID, 0). Selain itu (ada ID kosong/0/
    ganda) SELURUH dokumen memakai (0, 1..n) setelah diurutkan stabil -> kunci tidak pernah bentrok."""
    ids = [to_int(line.get("ID")) for line in lines]
    if ids and all(i > 0 for i in ids) and len(set(ids)) == len(ids):
        return [(i, 0, line) for i, line in zip(ids, lines, strict=True)]
    ordered = sorted(lines, key=_journal_sort_key)
    return [(0, n, line) for n, line in enumerate(ordered, start=1)]


def line_value(qty: Decimal, hpp: Decimal) -> Decimal:
    """`value = qty x hpp`, Decimal(18,2) ROUND_HALF_UP (qty bertanda: negatif = keluar)."""
    return (qty * hpp).quantize(_CENT, rounding=ROUND_HALF_UP)


def transform_item_journal(
    docs: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Dokumen jurnal item (list + detail digabung, ter-whitelist) -> fact_esb_item_journal_line. Semua status
    disimpan (view menyaring `ITEM_JOURNAL_COUNTED_STATUS`). Dokumen tanpa nomor dibuang; nomor ganda -> yang
    pertama."""
    by_num: dict[str, Mapping[str, Any]] = {}
    for doc in docs:
        num = text(doc.get("itemJournalNum"), 64)
        if num and num not in by_num:
            by_num[num] = doc
    out: list[list[Any]] = []
    for num in sorted(by_num):
        doc = by_num[num]
        branch_id = max(0, to_int(doc.get("branchID")))
        raw_lines = doc.get("itemJournalDetails")
        lines = [line for line in raw_lines if isinstance(line, Mapping)] if isinstance(raw_lines, list) else []
        for detail_id, line_no, line in journal_line_keys(lines):
            qty = to_decimal(line.get("qty"), 3)
            hpp = to_decimal(line.get("hpp"), 4)
            out.append(
                [
                    brand,
                    company_for(branch_id),
                    day,
                    num,
                    detail_id,
                    line_no,
                    branch_id,
                    max(0, to_int(doc.get("locationID"))),
                    text(doc.get("locationName")),
                    to_int(doc.get("statusID")),
                    text(doc.get("statusName"), 64),
                    text(doc.get("requestTemplateName"), TEMPLATE_MAX),
                    max(0, to_int(line.get("purposeID"))),
                    text(line.get("purposeName"), 64),
                    max(0, to_int(line.get("productID"))),
                    max(0, to_int(line.get("productDetailID"))),
                    text(line.get("productName")),
                    text(line.get("uomName"), 32),
                    qty,
                    hpp,
                    line_value(qty, hpp),
                    version,
                ]
            )
    return out


# ================================================================== transfer_request
def gtr_parties(doc: Mapping[str, Any]) -> tuple[int, int]:
    """-> (requester_branch_id, supplier_branch_id) menurut `GTR_ORIGIN_IS_SUPPLIER`."""
    origin = max(0, to_int(doc.get("originBranchID")))
    dest = max(0, to_int(doc.get("destinationBranchID")))
    return (dest, origin) if GTR_ORIGIN_IS_SUPPLIER else (origin, dest)


def resolve_gtr_code(line: Mapping[str, Any], product_ids: Mapping[int, str], aliases: Mapping[str, str]) -> str:
    """Kode produk baris GTR: `productCode` -> `dim_esb_product_id` (productID) -> alias nama -> '' (tetap disimpan,
    dihitung di v_esb_g67_quality)."""
    code = text(line.get("productCode"), 64)
    if code:
        return code
    product_id = to_int(line.get("productID"))
    if product_id > 0 and product_ids.get(product_id):
        return product_ids[product_id]
    norm = alias_norm(line.get("productName"))
    return aliases.get(norm, "") if norm else ""


def transform_transfer_requests(
    docs: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    product_ids: Mapping[int, str],
    aliases: Mapping[str, str],
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Dokumen GTR (list + detail digabung, ter-whitelist) -> fact_esb_transfer_request_line. Satu baris per (dokumen,
    product_code, uom); baris ganda dijumlah (qty) dan atribut produk dari baris pertama. company_code = PT cabang
    PEMINTA (L-266)."""
    by_num: dict[str, Mapping[str, Any]] = {}
    for doc in docs:
        num = text(doc.get("transferNum"), 64)
        if num and num not in by_num:
            by_num[num] = doc
    agg: dict[tuple[str, str, str], list[Any]] = {}
    for num in sorted(by_num):
        doc = by_num[num]
        requester, supplier = gtr_parties(doc)
        header = [
            requester,
            supplier,
            to_int(doc.get("statusID")),
            text(doc.get("statusName"), 64),
            text(doc.get("categoryTypeName"), 64),
            flag(doc.get("bypassGoodsDelivery"), 0),
            flag(doc.get("finishTransferRequest"), 0),
            text(doc.get("purchaseRequestNum"), 64),
        ]
        raw_lines = doc.get("transferDetails")
        for line in raw_lines if isinstance(raw_lines, list) else []:
            if not isinstance(line, Mapping):
                continue
            code = resolve_gtr_code(line, product_ids, aliases)
            uom = text(line.get("uomName"), 32)
            entry = agg.get((num, code, uom))
            if entry is None:
                entry = agg[(num, code, uom)] = [
                    *header,
                    max(0, to_int(line.get("productID"))),
                    max(0, to_int(line.get("productDetailID"))),
                    text(line.get("productName")),
                    _ZERO3, _ZERO3, _ZERO3, _ZERO3, _ZERO3,
                ]  # fmt: skip
            for offset, key in enumerate(("requestQty", "qty", "pendingQty", "availableQty", "stockQty")):
                entry[11 + offset] += to_decimal(line.get(key), 3)
    out: list[list[Any]] = []
    for (num, code, uom), e in sorted(agg.items()):
        out.append(
            [
                brand, company_for(e[0]), day, num, e[0], e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[8], e[9], code,
                e[10], uom, e[11], e[12], e[13], e[14], e[15], version,
            ]
        )  # fmt: skip
    return out


def gtr_product_pairs(docs: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """{productID: productCode} dari baris GTR yang memuat KEDUANYA (bukan hasil resolusi); productID ambigu (dua kode)
    dibuang."""
    pairs: list[tuple[int, str]] = []
    for doc in docs:
        raw_lines = doc.get("transferDetails")
        for line in raw_lines if isinstance(raw_lines, list) else []:
            if isinstance(line, Mapping):
                pairs.append((to_int(line.get("productID")), text(line.get("productCode"), 64)))
    return unique_map((pid, code) for pid, code in pairs if pid > 0)


def product_id_rows(
    pairs: Mapping[int, str], brand: str, company_code: str, source: str, version: datetime
) -> list[list[Any]]:
    return [[brand, company_code, pid, code, source, version] for pid, code in sorted(pairs.items())]


# ================================================================== delivery_ref
def transform_delivery_ref(
    details: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    day: date,
    version: datetime,
) -> list[list[Any]]:
    """Detail goods-delivery (list + detail digabung, payload G41) -> fact_esb_delivery_ref_line. Status 33 / tanpa
    nomor / baris tanpa productCode diabaikan (sama G41). `referenceNumber` per baris bila ada, jika tidak dari header
    [B]. qty kirim (`deliveryQty`) dan terima (`receiptQty`) TERPISAH. company_code = PT cabang asal (L-266)."""
    agg: dict[tuple[str, str, str, str], list[Any]] = {}
    for doc in details:
        doc_num = text(doc.get("goodsDeliveryNum"), 64)
        if not doc_num or to_int(doc.get("statusID")) == GD_IGNORED_STATUS:
            continue
        origin = max(0, to_int(doc.get("originBranchID")))
        dest = max(0, to_int(doc.get("destinationBranchID")))
        header_ref = text(doc.get("referenceNumber"), 64)
        trans_type = text(doc.get("transType"), 64)
        ref_date = parse_day(doc.get("refDate"))
        raw_lines = doc.get("goodsDeliveryDetails")
        for line in raw_lines if isinstance(raw_lines, list) else []:
            if not isinstance(line, Mapping):
                continue
            code = text(line.get("productCode"), 64)
            if not code:
                continue
            reference = text(line.get("referenceNumber"), 64) or header_ref
            key = (doc_num, reference, code, text(line.get("uomName"), 32))
            entry = agg.setdefault(key, [trans_type, ref_date, origin, dest, _ZERO3, _ZERO3])
            entry[4] += to_decimal(line.get("deliveryQty"), 3)
            entry[5] += to_decimal(line.get("receiptQty"), 3)
    return [
        [brand, company_for(origin), day, doc_num, ref, trans, rdate, origin, dest, code, uom, sent, received, version]
        for (doc_num, ref, code, uom), (trans, rdate, origin, dest, sent, received) in sorted(agg.items())
    ]


class RawMissing(ValueError):
    """Retransform: raw goods-delivery hari D tidak lengkap (list tidak ada, detail hilang, atau `doc_date != D` karena
    ESB memakai ulang nomor) -> slice tidak ditulis; hari itu ditarik ulang lewat `backfill --force`."""

    def __init__(self, day: date, missing: Sequence[str]) -> None:
        super().__init__(f"raw_missing day={day.isoformat()} docs={len(missing)}")
        self.day = day
        self.missing = list(missing)


def delivery_details_from_raw(day: date, listed: Any, details: Mapping[str, tuple[date, Any]]) -> list[dict[str, Any]]:
    """Retransform (§31.1.4): list raw `inventory/goods-delivery` hari D + detail raw per nomor ({num: (doc_date,
    payload)}) -> dokumen gabungan (bentuk sama dengan extract_delivery). Kekurangan apa pun -> RawMissing."""
    if not isinstance(listed, list):
        raise RawMissing(day, ["<list>"])
    merged: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in listed:
        if not isinstance(row, Mapping):
            continue
        num = text(row.get("goodsDeliveryNum"), 64)
        if not num or to_int(row.get("statusID")) == GD_IGNORED_STATUS:
            continue
        found = details.get(num)
        if found is None or found[0] != day or not isinstance(found[1], Mapping):
            missing.append(num)
            continue
        merged.append({**row, **found[1]})
    if missing:
        raise RawMissing(day, missing)
    return merged


def raw_delivery_nums(listed: Any) -> list[str]:
    """Nomor dokumen goods-delivery dari list raw (status 33 / tanpa nomor diabaikan)."""
    if not isinstance(listed, list):
        return []
    nums = {
        text(row.get("goodsDeliveryNum"), 64)
        for row in listed
        if isinstance(row, Mapping) and to_int(row.get("statusID")) != GD_IGNORED_STATUS
    }
    return sorted(num for num in nums if num)
