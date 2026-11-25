"""Transform murni ESB OMS -> baris mart (G69, docs/04 §33.4-§33.6). Tanpa I/O.

PDP/PCI (§33.6): `strip_oms` membuang kunci denylist OMS + denylist L-110 (`normalize.redact`) di SEMUA tingkat sarang,
termasuk larik `salesInfo[]` utuh, SEBELUM payload masuk `efn_esb.raw_doc` maupun transform. `has_member` dihitung
dari payload mentah sebelum pembuangan (satu-satunya turunan data member yang disimpan).

Angka (§33.2): respons OMS mengirim angka sebagai string -> parse KETAT (`to_decimal`/`strict_int`); gagal parse =
`ValueError` -> slice gagal, tanpa checkpoint. Bentuk `packages[]`/`extras[]` belum terdokumentasi -> parser toleran:
elemen bukan objek / tanpa nama-kode-id / larik bukan list diabaikan dan dihitung di `OmsTables.errors` (asumsi di
README "G69 asumsi bentuk").
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from efn_esb.margin import MENU_BOM_TYPE, canonical_menu, menu_bom_index, normalize_name
from efn_esb.normalize import is_fin, is_pii_key, text, to_decimal, whitelist
from efn_esb.planning import WIB

# §33.6: dibuang di semua tingkat (lowercase). Ditambah denylist L-110 (normalize.is_pii_key).
OMS_DROP_KEYS = frozenset(
    {
        "memberid",
        "membercode",
        "membername",
        "cardnumber",
        "accountname",
        "verificationcode",
        "vouchercode",
        "notes",
        "cancelnotes",
        "additionalinfo",
        "createdby",
        "editedby",
        "selforderid",
        "salesinfo",
        "tablename",  # SEC-OMS-01: take away/ojol bisa memakai field meja untuk nama pelanggan/driver
    }
)
# SEC-OMS-02: substring (lowercase) yang dibuang rekursif di seluruh payload struk -- menangkap kunci baru vendor
# (maskedCardNumber, approvalCode, customerPhone, ...).
OMS_DROP_SUBSTRINGS = ("card", "verification", "approval", "account", "member", "phone", "email")
# SEC-OMS-02: elemen salesPayments[] = WHITELIST (kunci lain dibuang, nilai bersarang dibuang).
PAYMENT_FIELDS = (
    "paymentMethodID", "paymentMethodName", "paymentMethodTypeID", "paymentMethodTypeName", "bankName",
    "paymentAmount", "fullPaymentAmount", "salesPaymentBackendID", "salesPaymentPosID",
)  # fmt: skip
MEMBER_KEYS = ("memberID", "memberCode", "memberName")
PAGE_SIZE = 20  # sales-head: 20 struk per halaman (probe 25 Nov 2026)
LINE_KINDS = ("menu", "package", "extra")
CHILD_KEYS = (("packages", "package"), ("extras", "extra"))
MAX_DEPTH = 3  # menu -> paket -> extra; lebih dalam = bentuk tak dikenal (errors)
# Status struk/baris NON-final (substring, tanpa beda huruf besar/kecil). Nilai statusID per status belum terverifikasi
# dari sumber primer (§33.5) -> dipakai statusName; status kosong dianggap final. README "G69 asumsi status".
NON_FINAL_MARKERS = ("void", "cancel", "batal", "open", "terbuka", "draft", "pending", "hold", "refund")

BILL_COLUMNS = (
    "brand", "company_code", "branch_id", "branch_code", "sales_date", "sales_num", "bill_num",
    "parent_link_sales_num", "sales_ts_in", "sales_ts_out", "visit_purpose_id", "visit_purpose_name", "pax",
    "subtotal", "discount_total", "menu_discount_total", "promotion_discount", "other_tax_total", "vat_total",
    "voucher_total", "rounding_total", "grand_total", "payment_total", "promotion_id", "promotion_name", "status_id",
    "status_name", "is_final", "has_member", "_version",
)  # fmt: skip
LINE_COLUMNS = (
    "brand", "company_code", "branch_id", "branch_code", "sales_date", "sales_num", "line_no", "parent_line_no",
    "line_kind", "menu_id", "menu_code", "menu_name", "menu_key", "menu_category_name", "menu_category_detail_name",
    "qty", "original_price", "price", "discount_value", "total", "vat_value", "status_id", "status_name",
    "sales_type", "is_promo", "is_cancelled", "has_packages", "_version",
)  # fmt: skip
PAYMENT_COLUMNS = (
    "brand", "company_code", "branch_id", "branch_code", "sales_date", "sales_num", "line_no", "payment_method_id",
    "payment_method_name", "payment_method_type_id", "payment_method_type_name", "bank_name", "payment_amount",
    "full_payment_amount", "_version",
)  # fmt: skip
BRANCH_DAILY_COLUMNS = (
    "brand", "company_code", "branch_id", "branch_code", "sales_date", "pax_total", "bill_total", "subtotal",
    "discount_total", "sc_total", "tax_total", "grand_total", "_version",
)  # fmt: skip
VISIT_PURPOSE_COLUMNS = (
    "brand", "company_code", "visit_purpose_id", "visit_purpose_name", "flag_dine_in", "_version",
)  # fmt: skip
PAYMENT_METHOD_COLUMNS = (
    "brand", "company_code", "payment_method_type_id", "payment_method_type_name", "payment_method_id",
    "payment_method_name", "_version",
)  # fmt: skip
MENU_FIN_COLUMNS = (
    "brand", "company_code", "key_kind", "match_key", "method", "bom_id", "product_code", "product_name", "uom",
    "qty_per_menu", "_version",
)  # fmt: skip
OMS_TABLES: dict[str, tuple[str, ...]] = {
    "fact_esb_oms_bill": BILL_COLUMNS,
    "fact_esb_oms_bill_line": LINE_COLUMNS,
    "fact_esb_oms_bill_payment": PAYMENT_COLUMNS,
    "fact_esb_oms_branch_daily": BRANCH_DAILY_COLUMNS,
    "dim_esb_oms_visit_purpose": VISIT_PURPOSE_COLUMNS,
    "dim_esb_oms_payment_method": PAYMENT_METHOD_COLUMNS,
    "dim_esb_oms_menu_fin": MENU_FIN_COLUMNS,
}

CompanyFor = Callable[[int], str]


# ================================================================== PDP
def is_oms_drop_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in OMS_DROP_KEYS or any(part in lowered for part in OMS_DROP_SUBSTRINGS) or is_pii_key(key)


def strip_oms(value: Any) -> Any:
    """Salinan tanpa kunci §33.6 + substring SEC-OMS-02 + L-110, rekursif ke dict/list; elemen `salesPayments[]`
    di-whitelist (`PAYMENT_FIELDS`, nilai skalar saja; elemen bukan objek dibuang)."""
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            name = str(k)
            if is_oms_drop_key(name):
                continue
            if name.lower() == "salespayments" and isinstance(v, list):
                out[name] = [whitelist(p, PAYMENT_FIELDS) for p in v if isinstance(p, Mapping)]
            else:
                out[name] = strip_oms(v)
        return out
    if isinstance(value, list):
        return [strip_oms(item) for item in value]
    return value


def has_member(bill: Mapping[str, Any]) -> int:
    """1 bila memberID/memberCode/memberName terisi (ID 0/'0' = tanpa member)."""
    for key in MEMBER_KEYS:
        value = bill.get(key)
        if value is None or isinstance(value, bool):
            continue
        raw = str(value).strip()
        if raw and raw != "0":
            return 1
    return 0


@dataclass(frozen=True)
class OmsBill:
    """Struk yang SUDAH di-strip + flag member dari payload mentah."""

    payload: dict[str, Any]
    has_member: int


def prepare_bill(raw: Mapping[str, Any]) -> OmsBill:
    stripped = strip_oms(raw)
    return OmsBill(stripped if isinstance(stripped, dict) else {}, has_member(raw))


# ================================================================== parsing
def oms_rows(result: Any) -> list[dict[str, Any]]:
    """Baris dari respons OMS (list, atau objek ber-`data`/`result` list/objek-ber-data). Elemen bukan dict dibuang."""
    data: Any = result
    for _ in range(3):
        if isinstance(data, Mapping):
            data = data.get("data", data.get("result"))
        else:
            break
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict)]


def strict_int(value: Any) -> int:
    """Bilangan bulat dari int/string ('12', '12.00'). None/'' -> 0; pecahan/teks -> ValueError."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise ValueError("nilai boolean bukan angka")
    number = to_decimal(value, 6)
    if number != number.to_integral_value():
        raise ValueError("bilangan bulat diharapkan")
    return int(number)


def opt_int(value: Any) -> int:
    """ID opsional (0 bila kosong/tidak valid) -- bukan angka uang, tidak menggagalkan slice."""
    try:
        return max(0, strict_int(value))
    except ValueError:
        return 0


def parse_ts(value: Any) -> datetime | None:
    """`YYYY-MM-DD HH:MM:SS` (WIB) -> datetime aware WIB; kosong/tidak valid -> None."""
    raw = text(value, 32).replace("T", " ")
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=WIB)


def is_final_status(status_name: Any) -> int:
    lowered = text(status_name).lower()
    return 0 if any(marker in lowered for marker in NON_FINAL_MARKERS) else 1


def _filled(value: Any) -> bool:
    raw = text(value)
    return bool(raw) and raw != "0"


def _first(row: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if row.get(key) not in (None, ""):
            return row.get(key)
    return None


ID_KEYS = ("menuID", "menuId", "packageMenuID", "extraMenuID", "menuExtraID", "id")
CODE_KEYS = ("menuCode", "code")
NAME_KEYS = ("menuName", "packageName", "extraName", "name")


@dataclass
class OmsTables:
    tables: dict[str, list[list[Any]]] = field(default_factory=dict)
    errors: int = 0


@dataclass(frozen=True)
class SliceKey:
    brand: str
    company_code: str
    branch_id: int
    branch_code: str
    day: date
    version: datetime


def _line_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    # §33.4: (menu_id, menu_code, sales_type, qty, price, total) + pemecah seri (discount_value, menu_name).
    return (
        opt_int(_first(row, ID_KEYS)),
        text(_first(row, CODE_KEYS), 64),
        text(row.get("salesType"), 32),
        to_decimal(row.get("qty"), 3),
        to_decimal(row.get("price"), 2),
        to_decimal(row.get("total"), 2),
        to_decimal(row.get("discountValue"), 2),
        text(_first(row, NAME_KEYS)),
    )


def _children(item: Mapping[str, Any], out: OmsTables) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for key, kind in CHILD_KEYS:
        value = item.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            out.errors += 1
            continue
        for child in value:
            if isinstance(child, dict) and (
                _first(child, NAME_KEYS) is not None
                or _first(child, CODE_KEYS) is not None
                or _first(child, ID_KEYS) is not None
            ):
                found.append((kind, child))
            else:
                out.errors += 1
    return found


def flatten_lines(key: SliceKey, sales_num: str, menus: Any, out: OmsTables, rows: list[list[Any]]) -> None:
    """salesMenus[] -> baris; packages[]/extras[] depth-first di bawah induknya (parent_line_no)."""
    if not isinstance(menus, list):
        if menus is not None:
            out.errors += 1
        return
    items = [m for m in menus if isinstance(m, dict)]
    out.errors += len(menus) - len(items)
    counter = [0]

    def emit(item: dict[str, Any], kind: str, parent: int, depth: int) -> None:
        counter[0] += 1
        line_no = counter[0]
        children = _children(item, out) if depth < MAX_DEPTH else []
        if depth >= MAX_DEPTH and any(item.get(k) for k, _ in CHILD_KEYS):
            out.errors += 1
        name = text(_first(item, NAME_KEYS))
        status_name = text(item.get("statusName"), 64)
        rows.append(
            [
                key.brand,
                key.company_code,
                key.branch_id,
                key.branch_code,
                key.day,
                sales_num,
                line_no,
                parent,
                kind,
                opt_int(_first(item, ID_KEYS)),
                text(_first(item, CODE_KEYS), 64),
                name,
                canonical_menu(name),
                text(item.get("menuCategoryName"), 128),
                text(item.get("menuCategoryDetailName"), 128),
                to_decimal(item.get("qty"), 3),
                to_decimal(item.get("originalPrice"), 2),
                to_decimal(item.get("price"), 2),
                to_decimal(item.get("discountValue"), 2),
                to_decimal(item.get("total"), 2),
                to_decimal(item.get("vatValue"), 2),
                opt_int(item.get("statusID")),
                status_name,
                text(item.get("salesType"), 32),
                int(_filled(item.get("menuPromotionID")) or _filled(item.get("promotionDetailID"))),
                1 - is_final_status(status_name),
                int(any(child_kind == "package" for child_kind, _ in children)),
                key.version,
            ]
        )
        for child_kind, child in sorted(children, key=lambda pair: (pair[0], _line_sort_key(pair[1]))):
            emit(child, child_kind, line_no, depth + 1)

    for item in sorted(items, key=_line_sort_key):
        emit(item, "menu", 0, 1)


def _payment_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    # §33.4: (payment_method_type_id, payment_method_id, payment_amount) + pemecah seri.
    return (
        opt_int(row.get("paymentMethodTypeID")),
        opt_int(row.get("paymentMethodID")),
        to_decimal(row.get("paymentAmount"), 2),
        to_decimal(row.get("fullPaymentAmount"), 2),
        text(row.get("bankName"), 64),
        opt_int(row.get("salesPaymentBackendID")),
        opt_int(row.get("salesPaymentPosID")),
    )


def transform_bills(bills: Iterable[OmsBill], key: SliceKey) -> OmsTables:
    """Struk satu slice (cabang x hari) -> fact_esb_oms_bill / _bill_line / _bill_payment."""
    out = OmsTables()
    bill_rows: list[list[Any]] = []
    line_rows: list[list[Any]] = []
    pay_rows: list[list[Any]] = []
    seen: set[str] = set()
    for bill in bills:
        row = bill.payload
        sales_num = text(row.get("salesNum"), 64)
        if not sales_num:
            out.errors += 1
            continue
        if sales_num in seen:  # dedup dalam slice: kemunculan pertama dipakai
            continue
        seen.add(sales_num)
        status_name = text(row.get("statusName"), 64)
        bill_rows.append(
            [
                key.brand,
                key.company_code,
                key.branch_id,
                key.branch_code,
                key.day,
                sales_num,
                text(row.get("billNum"), 64),
                text(row.get("parentLinkSalesNum"), 64),
                parse_ts(row.get("salesDateIn")),
                parse_ts(row.get("salesDateOut")),
                opt_int(row.get("visitPurposeID")),
                text(row.get("visitPurposeName"), 128),
                min(max(strict_int(row.get("paxTotal")), 0), 65_535),
                to_decimal(row.get("subtotal"), 2),
                to_decimal(row.get("discountTotal"), 2),
                to_decimal(row.get("menuDiscountTotal"), 2),
                to_decimal(row.get("promotionDiscount"), 2),
                to_decimal(row.get("otherTaxTotal"), 2),
                to_decimal(row.get("vatTotal"), 2),
                to_decimal(row.get("voucherTotal"), 2),
                to_decimal(row.get("roundingTotal"), 2),
                to_decimal(row.get("grandTotal"), 2),
                to_decimal(row.get("paymentTotal"), 2),
                opt_int(row.get("promotionID")),
                text(row.get("promotionName"), 128),
                opt_int(row.get("statusID")),
                status_name,
                is_final_status(status_name),
                bill.has_member,
                key.version,
            ]
        )
        flatten_lines(key, sales_num, row.get("salesMenus"), out, line_rows)
        payments = row.get("salesPayments")
        if isinstance(payments, list):
            valid = [p for p in payments if isinstance(p, dict)]
            out.errors += len(payments) - len(valid)
            for line_no, pay in enumerate(sorted(valid, key=_payment_sort_key), start=1):
                pay_rows.append(
                    [
                        key.brand,
                        key.company_code,
                        key.branch_id,
                        key.branch_code,
                        key.day,
                        sales_num,
                        line_no,
                        opt_int(pay.get("paymentMethodID")),
                        text(pay.get("paymentMethodName"), 128),
                        opt_int(pay.get("paymentMethodTypeID")),
                        text(pay.get("paymentMethodTypeName"), 128),
                        text(pay.get("bankName"), 64),
                        to_decimal(pay.get("paymentAmount"), 2),
                        to_decimal(pay.get("fullPaymentAmount"), 2),
                        key.version,
                    ]
                )
        elif payments is not None:
            out.errors += 1
    out.tables = {
        "fact_esb_oms_bill": bill_rows,
        "fact_esb_oms_bill_line": line_rows,
        "fact_esb_oms_bill_payment": pay_rows,
    }
    return out


def parse_day(value: Any) -> date | None:
    """Tanggal OMS: `sales-branch-summary` memakai `DD-MM-YYYY` (terverifikasi data nyata 25 Nov 2026), `sales-head`
    memakai ISO `YYYY-MM-DD`. Bentuk lain -> None (baris dibuang, dihitung pemanggil)."""
    raw = text(value, 32)[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def transform_branch_daily(
    rows: Iterable[Mapping[str, Any]],
    brand: str,
    company_for: CompanyFor,
    branch_ids: Mapping[str, int],
    version: datetime,
) -> dict[date, list[list[Any]]]:
    """`sales-branch-summary` (angka string) -> baris per hari (kunci = salesDate). Tanggal/kode kosong dibuang."""
    out: dict[date, list[list[Any]]] = {}
    for row in rows:
        code = text(row.get("branchCode"), 64)
        day = parse_day(row.get("salesDate"))
        if day is None or not code:
            continue
        branch_id = branch_ids.get(code, 0)
        out.setdefault(day, []).append(
            [
                brand,
                company_for(branch_id),
                branch_id,
                code,
                day,
                strict_int(row.get("paxTotal")),
                strict_int(row.get("billTotal")),
                to_decimal(row.get("subtotal"), 2),
                to_decimal(row.get("discountTotal"), 2),
                to_decimal(row.get("scTotal"), 2),
                to_decimal(row.get("taxTotal"), 2),
                to_decimal(row.get("grandTotal"), 2),
                version,
            ]
        )
    return out


def transform_visit_purposes(
    rows: Iterable[Mapping[str, Any]], brand: str, company_code: str, version: datetime
) -> list[list[Any]]:
    out: list[list[Any]] = []
    for row in rows:
        vp_id = opt_int(row.get("visitPurposeID"))
        if vp_id <= 0:
            continue
        dine = row.get("flagDineIn")
        flag = 1 if str(dine).strip().lower() in {"1", "true", "yes", "y"} else 0
        out.append([brand, company_code, vp_id, text(row.get("visitPurposeName"), 128), flag, version])
    return out


def transform_payment_methods(result: Any, brand: str, company_code: str, version: datetime) -> list[list[Any]]:
    """Objek per paymentMethodType -> paymentMethods[]. Menerima list objek tipe atau objek {nama: objek tipe}."""
    data: Any = result
    for _ in range(3):
        if isinstance(data, Mapping) and ("data" in data or "result" in data):
            data = data.get("data", data.get("result"))
    if isinstance(data, list):
        types: list[Any] = [v for v in data if isinstance(v, Mapping)]
    elif isinstance(data, Mapping):
        types = [v for v in data.values() if isinstance(v, Mapping)]
    else:
        types = []
    out: dict[tuple[int, int], list[Any]] = {}
    for item in types:
        type_id = opt_int(item.get("paymentMethodTypeID"))
        type_name = text(item.get("paymentMethodTypeName"), 128)
        methods = item.get("paymentMethods")
        for method in methods if isinstance(methods, list) else []:
            if not isinstance(method, Mapping):
                continue
            method_id = opt_int(method.get("paymentMethodID"))
            if method_id <= 0:
                continue
            out[(type_id, method_id)] = [
                brand,
                company_code,
                type_id,
                type_name,
                method_id,
                text(method.get("paymentMethodName"), 128),
                version,
            ]
    return [out[k] for k in sorted(out)]


# ================================================================== menu -> produk FIN (dim_esb_oms_menu_fin)
@dataclass(frozen=True)
class BomHeaderRow:
    """Satu header dim_esb_bom (atribut yang dibaca margin.menu_bom_index + bom_code)."""

    bom_id: int
    bom_code: str
    bom_name: str
    bom_type: str
    product_code: str
    product_name: str
    active: bool


@dataclass(frozen=True)
class BomLine:
    bom_id: int
    product_code: str
    product_name: str
    qty: Decimal
    uom: str


def build_menu_fin(
    headers: Iterable[BomHeaderRow],
    lines: Iterable[BomLine],
    menu_prices: Iterable[tuple[str, str]],
    fin_codes: frozenset[str],
    brand: str,
    company_code: str,
    version: datetime,
) -> list[list[Any]]:
    """Pemetaan menu OMS -> produk FIN dari resep mart yang sudah ada (G43 dim_esb_bom + dim_esb_bom_line,
    BOM aktif bertipe Menu, versi snapshot terakhir; pemanggil memuatnya).

    Kunci (prioritas di view: `code` dulu, lalu `name`):
    - key_kind `code`, method `bom_code`   : menu_code = bom_code BOM Menu;
    - key_kind `code`, method `menu_price` : menu_code -> dim_esb_menu_price.bom_name -> BOM Menu (nama kanonik);
    - key_kind `name`, method `name`       : nama menu kanonik (§31.2 butir 4: margin.canonical_menu) = nama BOM / nama
      produk hasil BOM Menu.
    Komponen FIN = baris BOM langsung dengan kode di `fin_codes` (dim_esb_product.is_fin) atau aturan FIN §25.5
    (normalize.is_fin). BOM cocok tanpa komponen FIN -> satu baris product_code '' (mis. minuman; bukan 'tak
    terpetakan'). Sub-BOM tidak diurai (rasio batch BOM perantara tidak diketahui)."""
    header_list = [h for h in headers if h.active and normalize_name(h.bom_type) == MENU_BOM_TYPE]
    by_name = menu_bom_index(header_list)  # type: ignore[arg-type]  # duck-typed: atribut sama dengan BomHeader
    by_code: dict[str, int] = {}
    for header in header_list:
        code = text(header.bom_code, 64)
        if code and header.bom_id > by_code.get(code, -1):
            by_code[code] = header.bom_id
    fin: dict[int, dict[str, list[Any]]] = {}
    for line in lines:
        if line.qty <= 0 or not line.product_code:
            continue
        if line.product_code not in fin_codes and not is_fin(line.product_code, line.product_name):
            continue
        acc = fin.setdefault(line.bom_id, {}).setdefault(line.product_code, [line.product_name, line.uom, Decimal(0)])
        acc[2] += line.qty
    keys: dict[tuple[str, str], tuple[str, int]] = {}
    for code, bom_id in by_code.items():
        keys[("code", code)] = ("bom_code", bom_id)
    for menu_code, bom_name in menu_prices:
        bom_id = by_name.get(canonical_menu(bom_name))
        code = text(menu_code, 64)
        if code and bom_id is not None and ("code", code) not in keys:
            keys[("code", code)] = ("menu_price", bom_id)
    for name, bom_id in by_name.items():
        keys[("name", name)] = ("name", bom_id)
    out: list[list[Any]] = []
    for (kind, match_key), (method, bom_id) in sorted(keys.items()):
        products = fin.get(bom_id) or {}
        if not products:
            out.append([brand, company_code, kind, match_key, method, bom_id, "", "", "", Decimal(0), version])
            continue
        for code in sorted(products):
            name, uom, qty = products[code]
            out.append([brand, company_code, kind, match_key, method, bom_id, code, name, uom, qty, version])
    return out
