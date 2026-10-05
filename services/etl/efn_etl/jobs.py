"""Definisi job incremental: SQL sumber (keyset, tanpa SELECT *), tabel target, transformasi baris.

Kontrak SQL setiap job:
- parameter `%(cursor_ts)s`, `%(cursor_key)s`, `%(limit)s`;
- kolom `_cursor_ts` (timestamp perubahan) dan `_cursor_key` (id sebagai text);
- `ORDER BY _cursor_ts, _cursor_key` dan filter rentang pada kolom ber-index agar tidak full scan (R-O03).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from efn_etl.watermark import to_utc

GEO_DECIMALS = 3  # ~100 m: cukup untuk analisis area kompetitor, tidak melacak posisi presisi pelapor
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
COMPANY_CODES = frozenset({"EFN", "RTL", "FNB", "FARM"})


@dataclass(frozen=True)
class Job:
    name: str
    source: str  # "platform" | "odoo"
    sql: str
    target_table: str
    columns: tuple[str, ...]
    transform: Callable[[dict[str, Any]], list[Any]]
    # Kolom baris SUMBER yang berisi hash member; baris dengan hash yang sedang di-erasure tidak dimuat (§8.4).
    identity_field: str | None = None


def pseudonym(value: Any, column: str) -> str:
    """Hash identitas yang SUDAH dihitung BFF (docs/04 §1, §7.2). ETL tidak memegang pepper (SEC-16).

    Fail closed: nilai yang bukan hex sha256 (mis. nomor HP dari skema lama) membatalkan halaman agar data
    pribadi tidak pernah masuk ClickHouse.
    """
    text = "" if value is None else str(value).strip()
    if not HASH_RE.fullmatch(text):
        raise ValueError(f"{column} bukan hash sha256 hex; baris tidak dimuat")
    return text


def _opt_float(value: Any, digits: int | None = None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return round(number, digits) if digits is not None else number


def _opt_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _dt(value: datetime) -> datetime:
    return to_utc(value)


# ---------------------------------------------------------------- platform.price_hunter_report -> competitor_prices

SQL_COMPETITOR_PRICES = """
SELECT id, created_at, product_name, competitor_name, reported_price,
       extracted->>'price' AS extracted_price, status, reason, confidence,
       geo_lat, geo_lng, user_hash, updated_at,
       updated_at AS _cursor_ts, id::text AS _cursor_key
FROM platform.price_hunter_report
WHERE status IN ('valid', 'rejected', 'flagged')
  AND updated_at >= %(cursor_ts)s
  AND (updated_at, id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY updated_at, id::text
LIMIT %(limit)s
"""

COMPETITOR_PRICES_COLUMNS = (
    "report_id",
    "reported_at",
    "product_name",
    "product_sku",
    "competitor_name",
    "price",
    "extracted_price",
    "status",
    "reason",
    "ocr_confidence",
    "geo_lat",
    "geo_lng",
    "user_hash",
    "updated_at",
)


def transform_competitor_price(row: dict[str, Any]) -> list[Any]:
    return [
        row["id"],
        _dt(row["created_at"]),
        row["product_name"],
        None,  # product_sku: pemetaan ke SKU internal belum ada (Fase berikutnya)
        row["competitor_name"] or "unknown",
        Decimal(str(row["reported_price"])),
        _opt_decimal(row["extracted_price"]),
        row["status"],
        row["reason"] or "",
        _opt_float(row["confidence"]),
        _opt_float(row["geo_lat"], GEO_DECIMALS),
        _opt_float(row["geo_lng"], GEO_DECIMALS),
        pseudonym(row["user_hash"], "price_hunter_report.user_hash"),
        _dt(row["updated_at"]),
    ]


# ---------------------------------------------------------------- platform.loyalty_ledger -> loyalty_events

# Ledger append-only (ADR-020): created_at sebagai cursor. Hanya hash member yang dibaca (tidak member_ref_enc).
SQL_LOYALTY_EVENTS = """
SELECT id::text AS event_id, member_ref_hash, company_code, delta_points, reason, source, source_ref, created_at,
       created_at AS _cursor_ts, id::text AS _cursor_key
FROM platform.loyalty_ledger
WHERE created_at >= %(cursor_ts)s
  AND (created_at, id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY created_at, id::text
LIMIT %(limit)s
"""

LOYALTY_EVENTS_COLUMNS = (
    "event_id",
    "member_hash",
    "company_code",
    "delta_points",
    "reason",
    "source",
    "source_ref",
    "created_at",
)


def _company_code(value: Any) -> str:
    """PT penanggung liabilitas poin (ADR-020). Nilai di luar daftar ditolak (CHECK di Postgres sama)."""
    code = str(value or "").strip().upper()
    if code not in COMPANY_CODES:
        raise ValueError("company_code tidak dikenal")
    return code


def transform_loyalty_event(row: dict[str, Any]) -> list[Any]:
    return [
        row["event_id"],
        pseudonym(row["member_ref_hash"], "loyalty_ledger.member_ref_hash"),
        _company_code(row["company_code"]),
        int(row["delta_points"]),
        row["reason"] or "",
        row["source"] or "",
        row["source_ref"] or "",
        _dt(row["created_at"]),
    ]


# ---------------------------------------------------------------- Odoo pos_order_line -> fact_sales_line (STUB)

# Kolom diselaraskan ke Odoo CE 19.0 (addons/point_of_sale/models/pos_order.py, product_category.py, dicek
# 5 Okt 2026): `pos.order.config_id` kini stored (tidak perlu join pos_session), `state` 19.0 = draft|cancel|paid|done
# (tidak ada lagi `invoiced`), `product_category.complete_name` stored. Grant minimum: sql/odoo-readonly-grants.sql
# (QA U1). Aktifkan dengan ETL_ENABLE_ODOO=true setelah grant diterapkan pasca-install Odoo.
# Cursor memakai write_date baris (POS menulis order + line dalam satu sinkronisasi); perubahan yang hanya menyentuh
# pos_order.write_date tidak tertangkap (lihat README). `res_partner` TIDAK dibaca (PII); customer_id = id partner.
SQL_SALES_FACT = """
SELECT l.id AS line_id, o.company_id, c.name AS company_name, COALESCE(c.efn_brand_code, '') AS brand,
       o.config_id AS store_id, COALESCE(o.pos_reference, o.name) AS order_ref, o.date_order AS order_date,
       o.partner_id AS customer_id, l.product_id, COALESCE(l.full_product_name, '') AS product_name,
       COALESCE(pc.complete_name, '') AS category, l.qty, l.price_unit, l.discount AS discount_pct,
       l.price_subtotal AS subtotal, (l.price_subtotal_incl - l.price_subtotal) AS tax_amount,
       l.total_cost AS cost_total, l.write_date,
       l.write_date AS _cursor_ts, l.id::text AS _cursor_key
FROM pos_order_line l
JOIN pos_order o ON o.id = l.order_id
JOIN res_company c ON c.id = o.company_id
JOIN product_product pp ON pp.id = l.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
LEFT JOIN product_category pc ON pc.id = pt.categ_id
WHERE o.state IN ('paid', 'done')
  AND l.write_date >= %(cursor_ts)s
  AND (l.write_date, l.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY l.write_date, l.id::text
LIMIT %(limit)s
"""

SALES_FACT_COLUMNS = (
    "line_id",
    "source",
    "company_id",
    "company_name",
    "brand",
    "store_id",
    "order_ref",
    "order_date",
    "customer_id",
    "product_id",
    "product_name",
    "category",
    "qty",
    "price_unit",
    "discount_pct",
    "subtotal",
    "tax_amount",
    "cost_total",
    "write_date",
)


def transform_sales_line(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["line_id"]),
        "pos",
        int(row["company_id"]),
        str(row["company_name"] or ""),
        str(row["brand"] or ""),
        int(row["store_id"] or 0),
        str(row["order_ref"] or ""),
        _dt(row["order_date"]),
        int(row["customer_id"]) if row["customer_id"] is not None else None,
        int(row["product_id"]),
        str(row["product_name"]),
        str(row["category"]),
        Decimal(str(row["qty"])),
        Decimal(str(row["price_unit"])),
        Decimal(str(row["discount_pct"] or 0)),
        Decimal(str(row["subtotal"])),
        Decimal(str(row["tax_amount"] or 0)),
        _opt_decimal(row["cost_total"]),
        _dt(row["write_date"]),
    ]


COMPETITOR_PRICES = Job(
    name="competitor_prices",
    source="platform",
    sql=SQL_COMPETITOR_PRICES,
    target_table="competitor_prices",
    columns=COMPETITOR_PRICES_COLUMNS,
    transform=transform_competitor_price,
    identity_field="user_hash",
)
LOYALTY_EVENTS = Job(
    name="loyalty_events",
    source="platform",
    sql=SQL_LOYALTY_EVENTS,
    target_table="loyalty_events",
    columns=LOYALTY_EVENTS_COLUMNS,
    transform=transform_loyalty_event,
    identity_field="member_ref_hash",
)
SALES_FACT = Job(
    name="sales_fact_pos",
    source="odoo",
    sql=SQL_SALES_FACT,
    target_table="fact_sales_line",
    columns=SALES_FACT_COLUMNS,
    transform=transform_sales_line,
)


def enabled_jobs(odoo_enabled: bool) -> list[Job]:
    jobs = [COMPETITOR_PRICES, LOYALTY_EVENTS]
    if odoo_enabled:
        jobs.append(SALES_FACT)
    return jobs
