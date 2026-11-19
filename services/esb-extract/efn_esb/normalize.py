"""Normalisasi murni data ESB (docs/04 §25.5): alias nama produk, FIN, takeaway, angka, redaksi PII (L-110)."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# Aturan ejaan alias (urutan berlaku). Tambah di sini bila data kualitas (`v_esb_data_quality`) menunjukkan varian baru.
ALIAS_RULES: tuple[tuple[re.Pattern[str], str], ...] = ((re.compile(r"\bYOGHURT\b"), "YOGURT"),)
NAME_PREFIX = "NAME:"
_SPACES = re.compile(r"\s+")
_FIN_NAME = re.compile(r"(^|\s)FINB?\s")
TEXT_MAX = 256

# L-110: kunci field identitas orang yang DIBUANG dari payload sebelum ditulis ke raw_doc (rekursif, case-insensitive).
# Exact match (lowercase):
PII_KEYS = frozenset(
    {
        "createdby",
        "createdbyname",
        "createdbyusername",
        "createdusername",
        "updatedby",
        "updatedbyname",
        "modifiedby",
        "authorizedby",
        "authorizedbyname",
        "approvedby",
        "approvedbyname",
        "rejectedby",
        "voidby",
        "voidbyname",
        "username",
        "userfullname",
        "fullname",
        "firstname",
        "lastname",
        "contactperson",
        "address",
        "npwp",
        "nik",
        "ktp",
        "birthdate",
        "dateofbirth",
        "gender",
        # SEC-181: pelaku dokumen ESB (nama kunci nyata: creator/editor/authorizer di production-result detail)
        "creator",
        "editor",
        "authorizer",
        "approver",
        "pic",
        # Teks bebas (bisa memuat nama/telepon/catatan pribadi); tidak dipakai mart.
        "additionalinfo",
        "notes",
        "note",
        "remark",
        "remarks",
        "description",
        "memo",
        "comment",
        "comments",
    }
)
# Substring (lowercase): menangkap varian seperti picPhone, cellPhone, customerName, memberCode, employeeName.
PII_SUBSTRINGS = (
    "email",
    "phone",
    "mobile",
    "whatsapp",
    "customer",
    "member",
    "employee",
    "cashier",
    "waiter",
    "picname",
    "pic_name",
    "visitor",
    "guest",
    "npwp",
    "nationalid",
    "bankaccount",
    "accountnumber",
    # SEC-181: creatorFullName/authorizerFullName/editorFullName, driver/kurir/penerima, lampiran gambar.
    "fullname",
    "username",
    "driver",
    "sender",
    "receiver",
    "recipient",
    "courier",
    "attachment",
    "image",
    "photo",
    "signature",
)
# SEC-181: sufiks pelaku (createdBy, editedBy, receivedBy, postedBy, checkedByName, createdUserID, ...).
PII_SUFFIXES = ("by", "byname", "byid", "userid")
# Supplier (L-110): hanya kode, nama perusahaan, status (+ ID teknis).
SUPPLIER_FIELDS = ("supplierID", "supplierCode", "supplierName", "flagActive")
# G43 (docs/04 §27.11) -- whitelist per entitas migrasi, diterapkan SEBELUM apa pun disimpan. Respons ESB memuat alamat,
# PIC, telepon, rekening -> semua dibuang. NPWP tidak ada di daftar `/customer` dan sengaja tidak ditarik (§27.4: NPWP
# diisi Finance di Odoo, bukan milik migrasi).
CUSTOMER_FIELDS = (
    "customerID", "customerCode", "customerName", "customerCategoryName", "paymentDueDays", "flagActive", "lockVat",
)  # fmt: skip
# COA: `description` = nama akun (bukan teks bebas) -> whitelist, bukan redact() (redact membuang `description`).
COA_FIELDS = ("coaNo", "description", "coaLevel", "currency", "flagActive")
# Piutang per faktur (`/customer-receivable?type=2`): kode pelanggan saja (tanpa nama/alamat).
AR_FIELDS = (
    "customerCode", "productSalesInvoiceNum", "invoiceDate", "dueDate", "currencyName", "receivableTotal",
    "paymentTotal", "advanceTotal",
)  # fmt: skip
# Utang per faktur (`/supplier-payable/invoice-aging`): kode supplier saja (§27.11 "supplier = kode saja").
AP_FIELDS = (
    "supplierCode", "purchaseInvoiceNum", "supplierInvoiceNum", "invoiceDate", "dueDate", "currencyName",
    "payableTotal", "paymentTotal",
)  # fmt: skip


def is_pii_key(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in PII_KEYS
        or any(part in lowered for part in PII_SUBSTRINGS)
        or any(lowered.endswith(suffix) for suffix in PII_SUFFIXES)
    )


def redact(value: Any) -> Any:
    """Salinan `value` tanpa kunci identitas orang (rekursif ke dict/list). Nilai lain apa adanya."""
    if isinstance(value, Mapping):
        return {str(k): redact(v) for k, v in value.items() if not is_pii_key(str(k))}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def whitelist(row: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Salinan `row` hanya dengan kunci di `fields` (nilai skalar; dict/list dibuang -> tidak ada kontak bersarang)."""
    return {key: row.get(key) for key in fields if key in row and not isinstance(row.get(key), Mapping | list)}


def redact_supplier(row: Mapping[str, Any]) -> dict[str, Any]:
    return whitelist(row, SUPPLIER_FIELDS)


def redact_customer(row: Mapping[str, Any]) -> dict[str, Any]:
    return whitelist(row, CUSTOMER_FIELDS)


def text(value: Any, limit: int = TEXT_MAX) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def alias_norm(name: Any) -> str:
    """Upper, NFKC, trim, spasi ganda dirapikan, aturan ejaan `ALIAS_RULES`."""
    value = unicodedata.normalize("NFKC", text(name)).upper()
    value = _SPACES.sub(" ", value).strip()
    for pattern, replacement in ALIAS_RULES:
        value = pattern.sub(replacement, value)
    return value


def is_fin(code: Any, name: Any) -> bool:
    """Produk jadi FIN: kode berawalan FIN atau nama memuat token FIN/FINB (prototipe aggregate.py)."""
    return text(code).upper().startswith("FIN") or _FIN_NAME.search(text(name).upper() + " ") is not None


def is_takeaway(menu_name: Any) -> bool:
    return text(menu_name).upper().endswith(" T")


def resolve_product(name: Any, aliases: Mapping[str, str]) -> str:
    """Kode produk dari nama lewat alias; tak terpetakan -> `NAME:<alias_norm>`."""
    norm = alias_norm(name)
    return aliases.get(norm) or f"{NAME_PREFIX}{norm}"


def to_decimal(value: Any, places: int) -> Decimal:
    """Angka JSON ESB -> Decimal terkuantisasi. None/'' -> 0; teks bukan angka -> ValueError (data rusak terlihat)."""
    if value is None or value == "":
        return Decimal(0).quantize(Decimal(1).scaleb(-places))
    if isinstance(value, bool):
        raise ValueError("nilai boolean bukan angka")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        raise ValueError("nilai bukan angka") from None
    if not number.is_finite():
        raise ValueError("nilai tidak hingga")
    return number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        try:
            return int(float(str(value).strip()))
        except ValueError:
            return default


def to_float(value: Any) -> float:
    try:
        return float(value) if value not in (None, "") and not isinstance(value, bool) else 0.0
    except (TypeError, ValueError):
        return 0.0


def flag(value: Any, default: int = 1) -> int:
    """Flag aktif ESB (bool/int/str) -> 0/1; tidak ada -> default."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return 1 if value else 0
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "y", "active", "aktif"} else 0


def parse_day(value: Any) -> date | None:
    raw = text(value)[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def learn_aliases(pairs: Iterable[tuple[Any, Any]]) -> dict[str, str]:
    """(kode, nama) -> {alias_norm: kode}. Bentrok satu alias dua kode -> kode terkecil (deterministik)."""
    out: dict[str, str] = {}
    for code, name in pairs:
        code_s = text(code, 64)
        norm = alias_norm(name)
        if not code_s or not norm:
            continue
        current = out.get(norm)
        if current is None or code_s < current:
            out[norm] = code_s
    return out
