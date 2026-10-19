"""Definisi job incremental: SQL sumber (keyset, tanpa SELECT *), tabel target, transformasi baris.

Kontrak SQL setiap job:
- parameter `%(cursor_ts)s`, `%(cursor_key)s`, `%(limit)s`;
- kolom `_cursor_ts` (timestamp perubahan) dan `_cursor_key` (id sebagai text);
- `ORDER BY _cursor_ts, _cursor_key` dan filter rentang pada kolom ber-index agar tidak full scan (R-O03).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

# Kode PT (G20: EFN, GL, PB, BCR, BCF, FARM) = efn_etl.entities.COMPANY_CODES.
from efn_etl.entities import company_code
from efn_etl.region import (
    REGION_JOIN_OFF,
    REGION_JOIN_ON,
    REGION_SELECT_OFF,
    REGION_SELECT_ON,
    region_code,
    regions_job,
)
from efn_etl.watermark import to_utc

GEO_DECIMALS = 3  # ~100 m: cukup untuk analisis area kompetitor, tidak melacak posisi presisi pelapor
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


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


# ---------------------------------------------------------------- retensi kolom (G18/W-19, PDP v7 Q-P7/Q-P8)


def years_before(value: datetime, years: int) -> datetime:
    """Tanggal kalender `years` tahun sebelumnya (29 Feb -> 28 Feb), selaras `INTERVAL n YEAR` ClickHouse."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


@dataclass(frozen=True)
class AgeOut:
    """Batas umur kolom pribadi saat MEMUAT ke mart (lapisan kedua TTL kolom ClickHouse, clickhouse-init.sh).

    TTL kolom ClickHouse baru diterapkan saat merge; baris lama yang dimuat ulang (backfill watermark, update sumber)
    membawa nilai asli sampai merge berikutnya. Karena itu ETL sudah mengosongkan saat transformasi:
    - koordinat Price Hunter (`geo_lat/geo_lng` -> NULL) bila lebih tua dari `geo_days` (Q-P8, 90 h);
    - pseudonim (`user_hash`/`member_hash` -> '', `customer_id` -> NULL) bila lebih tua dari `pseudonym_years`
      (Q-P7 vs Q-6: baris mart 10 th, kolom pseudonim dianonimkan setelah 5 th).
    Nilai = env clickhouse-init yang sama (`CH_GEO_RETENTION_DAYS`, `CH_PSEUDONYM_RETENTION_YEARS`).
    """

    geo_days: int = 90
    pseudonym_years: int = 5

    def geo_expired(self, event_at: datetime, now: datetime) -> bool:
        return to_utc(event_at) < to_utc(now) - timedelta(days=self.geo_days)

    def pseudonym_expired(self, event_at: datetime, now: datetime) -> bool:
        return to_utc(event_at) < years_before(to_utc(now), self.pseudonym_years)


DEFAULT_AGE_OUT = AgeOut()


def _now(now: datetime | None) -> datetime:
    return datetime.now(UTC) if now is None else to_utc(now)


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


def transform_competitor_price(
    row: dict[str, Any], age_out: AgeOut = DEFAULT_AGE_OUT, now: datetime | None = None
) -> list[Any]:
    reported_at = _dt(row["created_at"])
    current = _now(now)
    # Validasi hash tetap dijalankan (fail closed) walau nilainya kemudian dikosongkan.
    user_hash = pseudonym(row["user_hash"], "price_hunter_report.user_hash")
    if age_out.pseudonym_expired(reported_at, current):
        user_hash = ""
    geo_gone = age_out.geo_expired(reported_at, current)
    return [
        row["id"],
        reported_at,
        row["product_name"],
        None,  # product_sku: pemetaan ke SKU internal belum ada (Fase berikutnya)
        row["competitor_name"] or "unknown",
        Decimal(str(row["reported_price"])),
        _opt_decimal(row["extracted_price"]),
        row["status"],
        row["reason"] or "",
        _opt_float(row["confidence"]),
        None if geo_gone else _opt_float(row["geo_lat"], GEO_DECIMALS),
        None if geo_gone else _opt_float(row["geo_lng"], GEO_DECIMALS),
        user_hash,
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
    return company_code(value)


def transform_loyalty_event(
    row: dict[str, Any], age_out: AgeOut = DEFAULT_AGE_OUT, now: datetime | None = None
) -> list[Any]:
    created_at = _dt(row["created_at"])
    member_hash = pseudonym(row["member_ref_hash"], "loyalty_ledger.member_ref_hash")
    if age_out.pseudonym_expired(created_at, _now(now)):
        member_hash = ""
    return [
        row["event_id"],
        member_hash,
        _company_code(row["company_code"]),
        int(row["delta_points"]),
        row["reason"] or "",
        row["source"] or "",
        row["source_ref"] or "",
        created_at,
    ]


# ---------------------------------------------------------------- Odoo pos_order_line -> fact_sales_line

# Kolom diselaraskan ke Odoo CE 19.0 (diverifikasi information_schema DB dev, 7 Okt 2026): `pos.order.config_id`
# stored, `state` 19.0 = draft|cancel|paid|done, `pos_order.is_refund`, `pos_order_line.refunded_orderline_id`,
# `pos_order_line.is_reward_line` (pos_loyalty), `pos_config.discount_product_id`/`tip_product_id`.
# Grant minimum: sql/odoo-readonly-grants.sql. Cursor = write_date baris (POS menulis order + line dalam satu
# sinkronisasi); perubahan yang hanya menyentuh pos_order.write_date tidak tertangkap (lihat README).
# `res_partner` TIDAK dibaca (PII); customer_id = id partner.
# Gelombang 13 (Fase 1): order_id, is_refund, line_kind (product|discount|reward|points|tip), discount_amount.
# Gelombang 21 (ADR-036 §0.7): customer_region_code dari view `efn_etl.partner_region` (efn_etl/region.py) bila
# ETL_PARTNER_REGION_ENABLED; mati = konstanta '' tanpa join.
_SQL_SALES_FACT = """
SELECT l.id AS line_id, o.company_id, c.name AS company_name, COALESCE(c.efn_brand_code, '') AS brand,
       o.config_id AS store_id, COALESCE(o.pos_reference, o.name) AS order_ref, o.date_order AS order_date,
       o.partner_id AS customer_id, l.product_id, COALESCE(l.full_product_name, '') AS product_name,
       COALESCE(pc.complete_name, '') AS category, l.qty, l.price_unit, l.discount AS discount_pct,
       l.price_subtotal AS subtotal, (l.price_subtotal_incl - l.price_subtotal) AS tax_amount,
       l.total_cost AS cost_total, l.write_date,
       l.order_id, (COALESCE(o.is_refund, FALSE) OR l.refunded_orderline_id IS NOT NULL) AS is_refund,
       COALESCE(l.is_reward_line, FALSE) AS is_reward_line, COALESCE(pp.default_code, '') AS default_code,
       (cfg.discount_product_id IS NOT NULL AND cfg.discount_product_id = l.product_id) AS is_discount_product,
       (cfg.tip_product_id IS NOT NULL AND cfg.tip_product_id = l.product_id) AS is_tip_product,
       {region_select},
       l.write_date AS _cursor_ts, l.id::text AS _cursor_key
FROM pos_order_line l
JOIN pos_order o ON o.id = l.order_id
JOIN res_company c ON c.id = o.company_id
JOIN product_product pp ON pp.id = l.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
LEFT JOIN product_category pc ON pc.id = pt.categ_id
LEFT JOIN pos_config cfg ON cfg.id = o.config_id
{region_join}
WHERE o.state IN ('paid', 'done')
  AND l.write_date >= %(cursor_ts)s
  AND (l.write_date, l.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY l.write_date, l.id::text
LIMIT %(limit)s
"""


def sales_fact_sql(partner_region: bool = False) -> str:
    if partner_region:
        return _SQL_SALES_FACT.format(region_select=REGION_SELECT_ON, region_join=REGION_JOIN_ON)
    return _SQL_SALES_FACT.format(region_select=REGION_SELECT_OFF, region_join=REGION_JOIN_OFF)


SQL_SALES_FACT = sales_fact_sql()

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
    "order_id",
    "is_refund",
    "line_kind",
    "discount_amount",
    "customer_region_code",
)

# Produk "Poin EFN" (docs/04 §9.3): baris diskon senilai poin yang di-redeem. Kode internal (default_code) dapat
# diubah lewat env ETL_POINTS_PRODUCT_CODES (dipisah koma) — kontrak dengan modul POS loyalty Odoo.
DEFAULT_POINTS_PRODUCT_CODES = frozenset({"EFN-POINTS"})
LINE_KINDS = ("product", "discount", "reward", "points", "tip")
_HUNDRED = Decimal(100)
_CENT = Decimal("0.01")


def line_kind(row: dict[str, Any], points_codes: frozenset[str] = DEFAULT_POINTS_PRODUCT_CODES) -> str:
    """Jenis baris POS. Urutan prioritas: poin EFN > reward loyalty Odoo > produk diskon global > tip > produk."""
    if str(row.get("default_code") or "").strip() in points_codes:
        return "points"
    if row.get("is_reward_line"):
        return "reward"
    if row.get("is_discount_product"):
        return "discount"
    if row.get("is_tip_product"):
        return "tip"
    return "product"


def line_discount_amount(subtotal: Decimal, discount_pct: Decimal, qty: Decimal, price_unit: Decimal) -> Decimal:
    """Nilai diskon persen baris dalam basis yang sama dengan `price_subtotal` (setelah diskon, tanpa pajak).

    subtotal = dasar * (1 - d/100) -> diskon = subtotal * d / (100 - d). Untuk d >= 100 subtotal = 0, sehingga dipakai
    perkiraan qty * price_unit (basis harga POS; bisa termasuk pajak bila harga POS tax-included).
    """
    if discount_pct <= 0:
        return Decimal(0)
    if discount_pct >= _HUNDRED:
        return (qty * price_unit).quantize(_CENT)
    return (subtotal * discount_pct / (_HUNDRED - discount_pct)).quantize(_CENT)


def transform_sales_line(
    row: dict[str, Any],
    points_codes: frozenset[str] = DEFAULT_POINTS_PRODUCT_CODES,
    age_out: AgeOut = DEFAULT_AGE_OUT,
    now: datetime | None = None,
) -> list[Any]:
    order_date = _dt(row["order_date"])
    customer_id = int(row["customer_id"]) if row["customer_id"] is not None else None
    if age_out.pseudonym_expired(order_date, _now(now)):
        customer_id = None
    qty = Decimal(str(row["qty"]))
    price_unit = Decimal(str(row["price_unit"]))
    discount_pct = Decimal(str(row["discount_pct"] or 0))
    subtotal = Decimal(str(row["subtotal"]))
    return [
        int(row["line_id"]),
        "pos",
        int(row["company_id"]),
        str(row["company_name"] or ""),
        str(row["brand"] or ""),
        int(row["store_id"] or 0),
        str(row["order_ref"] or ""),
        order_date,
        customer_id,
        int(row["product_id"]),
        str(row["product_name"]),
        str(row["category"]),
        qty,
        price_unit,
        discount_pct,
        subtotal,
        Decimal(str(row["tax_amount"] or 0)),
        _opt_decimal(row["cost_total"]),
        _dt(row["write_date"]),
        int(row.get("order_id") or 0),
        1 if row.get("is_refund") else 0,
        line_kind(row, points_codes),
        line_discount_amount(subtotal, discount_pct, qty, price_unit),
        # Wilayah tidak terikat pada partner yang bisa dikenali bila customer_id sudah dikosongkan (AgeOut): kab/kota
        # tanpa id = agregat kasar. Baris tanpa member / member tanpa wilayah = ''.
        region_code(row.get("customer_region_code")),
    ]


def competitor_prices_job(age_out: AgeOut = DEFAULT_AGE_OUT) -> Job:
    return Job(
        name="competitor_prices",
        source="platform",
        sql=SQL_COMPETITOR_PRICES,
        target_table="competitor_prices",
        columns=COMPETITOR_PRICES_COLUMNS,
        transform=lambda row: transform_competitor_price(row, age_out),
        identity_field="user_hash",
    )


def loyalty_events_job(age_out: AgeOut = DEFAULT_AGE_OUT) -> Job:
    return Job(
        name="loyalty_events",
        source="platform",
        sql=SQL_LOYALTY_EVENTS,
        target_table="loyalty_events",
        columns=LOYALTY_EVENTS_COLUMNS,
        transform=lambda row: transform_loyalty_event(row, age_out),
        identity_field="member_ref_hash",
    )


def sales_fact_job(
    points_codes: frozenset[str] = DEFAULT_POINTS_PRODUCT_CODES,
    age_out: AgeOut = DEFAULT_AGE_OUT,
    partner_region: bool = False,
) -> Job:
    return Job(
        name="sales_fact_pos",
        source="odoo",
        sql=sales_fact_sql(partner_region),
        target_table="fact_sales_line",
        columns=SALES_FACT_COLUMNS,
        transform=lambda row: transform_sales_line(row, points_codes, age_out),
    )


COMPETITOR_PRICES = competitor_prices_job()
LOYALTY_EVENTS = loyalty_events_job()
SALES_FACT = sales_fact_job()


def enabled_jobs(
    odoo_enabled: bool,
    points_codes: frozenset[str] = DEFAULT_POINTS_PRODUCT_CODES,
    age_out: AgeOut = DEFAULT_AGE_OUT,
    points_payment: bool = True,
    partner_region: bool = False,
    kds: bool = False,
    kds_online: bool = False,
) -> list[Job]:
    """Job incremental. Odoo: penjualan POS + (Gelombang 13) pembayaran POS, outlet, produk (efn_etl/pos.py) +
    (Gelombang 21, `partner_region`) wilayah pembeli di penjualan dan master `dim_region` (efn_etl/region.py) +
    (Gelombang 22, `kds`) tiket & stasiun KDS, `kds_online` = kanal online E-29a (efn_etl/kds.py)."""
    jobs = [competitor_prices_job(age_out), loyalty_events_job(age_out)]
    if odoo_enabled:
        # Import lokal: pos.py / kds.py mengimpor helper dari modul ini.
        from efn_etl.kds import kds_jobs
        from efn_etl.pos import pos_jobs

        jobs.append(sales_fact_job(points_codes, age_out, partner_region))
        jobs.extend(pos_jobs(points_payment))
        if partner_region:
            jobs.append(regions_job())
        if kds:
            jobs.extend(kds_jobs(kds_online))
    return jobs
