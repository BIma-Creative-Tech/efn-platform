"""Gelombang 13 (Fase 1, docs/01 §7 "ETL dasar + 3 dashboard Metabase"): job incremental POS tambahan dari Odoo.

- `pos_payments`  : `pos_payment` -> `fact_pos_payment` (metode bayar per order; kolom kartu TIDAK dibaca);
- `pos_outlets`   : `pos_config` -> `dim_outlet` (nama outlet + gudang untuk filter dashboard);
- `products`      : `product_product` + `product_template` -> `dim_product` (nama, kategori, harga jual `list_price`
                    untuk indeks Price Hunter, flag lacak lot/kedaluwarsa).

Kolom diverifikasi di DB dev Odoo CE 19.0 (information_schema, 7 Okt 2026). Grant kolom: sql/odoo-readonly-grants.sql.
PII: `pos_payment.card_no`, `cardholder_name`, `card_brand`, `transaction_id`, `payment_ref_no`,
`payment_method_authcode`, `ticket` tidak di-grant dan tidak dibaca (PCI/UU PDP). `pos_order.email`/`mobile` juga tidak.
"""

from decimal import Decimal
from typing import Any

from efn_etl.jobs import Job
from efn_etl.watermark import to_utc

# ---------------------------------------------------------------- pos_payment -> fact_pos_payment

_SQL_POS_PAYMENTS = """
SELECT p.id AS payment_id, p.pos_order_id AS order_id, o.company_id, c.name AS company_name, o.config_id AS store_id,
       COALESCE(p.payment_date, o.date_order) AS payment_date, p.amount, p.payment_method_id,
       COALESCE(pm.name ->> 'id_ID', pm.name ->> 'en_US', '') AS method_name,
       COALESCE(pm.payment_method_type, '') AS method_type, COALESCE(pm.is_cash_count, FALSE) AS is_cash,
       COALESCE(p.is_change, FALSE) AS is_change, COALESCE(o.is_refund, FALSE) AS is_refund, p.write_date,
       {points_flag} AS is_loyalty_points,
       p.write_date AS _cursor_ts, p.id::text AS _cursor_key
FROM pos_payment p
JOIN pos_order o ON o.id = p.pos_order_id
JOIN res_company c ON c.id = o.company_id
LEFT JOIN pos_payment_method pm ON pm.id = p.payment_method_id
WHERE o.state IN ('paid', 'done')
  AND p.write_date >= %(cursor_ts)s
  AND (p.write_date, p.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY p.write_date, p.id::text
LIMIT %(limit)s
"""
# G18 (docs/04 §9.9, E-30): penanda metode "Poin EFN". Kolom Odoo `pos_payment_method.efn_is_loyalty_points` baru ada
# setelah E-30 terpasang -> dibaca hanya bila ETL_LOYALTY_POINTS_MODE=payment; sebelumnya FALSE (kolom mart 0).
POINTS_FLAG_PAYMENT = "COALESCE(pm.efn_is_loyalty_points, FALSE)"
POINTS_FLAG_PRODUCT = "FALSE"


def pos_payments_sql(points_payment: bool = False) -> str:
    return _SQL_POS_PAYMENTS.format(points_flag=POINTS_FLAG_PAYMENT if points_payment else POINTS_FLAG_PRODUCT)


SQL_POS_PAYMENTS = pos_payments_sql()
POS_PAYMENT_COLUMNS = (
    "payment_id",
    "order_id",
    "company_id",
    "company_name",
    "store_id",
    "payment_date",
    "amount",
    "payment_method_id",
    "method_name",
    "method_type",
    "is_cash",
    "is_change",
    "is_refund",
    "write_date",
    "is_loyalty_points",
)


def transform_pos_payment(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["payment_id"]),
        int(row["order_id"]),
        int(row["company_id"]),
        str(row["company_name"] or ""),
        int(row["store_id"] or 0),
        to_utc(row["payment_date"]),
        Decimal(str(row["amount"] or 0)),
        int(row["payment_method_id"] or 0),
        str(row["method_name"] or ""),
        str(row["method_type"] or ""),
        1 if row["is_cash"] else 0,
        1 if row["is_change"] else 0,
        1 if row["is_refund"] else 0,
        to_utc(row["write_date"]),
        1 if row.get("is_loyalty_points") else 0,
    ]


# ---------------------------------------------------------------- pos_config -> dim_outlet

SQL_POS_OUTLETS = """
SELECT cfg.id AS store_id, COALESCE(cfg.name, '') AS name, cfg.company_id, cfg.warehouse_id,
       COALESCE(cfg.active, FALSE) AS active, cfg.write_date,
       cfg.write_date AS _cursor_ts, cfg.id::text AS _cursor_key
FROM pos_config cfg
WHERE cfg.write_date >= %(cursor_ts)s
  AND (cfg.write_date, cfg.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY cfg.write_date, cfg.id::text
LIMIT %(limit)s
"""
OUTLET_COLUMNS = ("store_id", "name", "company_id", "warehouse_id", "active", "write_date")


def transform_outlet(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["store_id"]),
        str(row["name"] or ""),
        int(row["company_id"]),
        int(row["warehouse_id"]) if row["warehouse_id"] is not None else None,
        1 if row["active"] else 0,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- product_product -> dim_product

# Tabel produk kecil (ribuan baris): cursor GREATEST(variant, template) tanpa index khusus masih wajar; bila volume
# tumbuh, pecah menjadi dua job (seperti gl_lines/gl_lines_by_move).
_PRODUCT_VERSION = "GREATEST(pp.write_date, pt.write_date)"
_SQL_PRODUCTS = """
SELECT pp.id AS product_id, pp.product_tmpl_id AS template_id, COALESCE(pp.default_code, '') AS default_code,
       COALESCE(pt.name ->> 'id_ID', pt.name ->> 'en_US', '') AS name, COALESCE(pc.complete_name, '') AS category,
       pt.company_id, pt.list_price, COALESCE(pt.type, '') AS product_type,
       COALESCE(pt.is_storable, FALSE) AS is_storable,
       COALESCE(pt.tracking, 'none') AS tracking, COALESCE(pt.use_expiration_date, FALSE) AS use_expiration_date,
       COALESCE(pt.sale_ok, FALSE) AS sale_ok, COALESCE(pt.available_in_pos, FALSE) AS available_in_pos,
       (COALESCE(pp.active, FALSE) AND COALESCE(pt.active, FALSE)) AS active,
       {version} AS version_ts,
       {version} AS _cursor_ts, pp.id::text AS _cursor_key
FROM product_product pp
JOIN product_template pt ON pt.id = pp.product_tmpl_id
LEFT JOIN product_category pc ON pc.id = pt.categ_id
WHERE {version} >= %(cursor_ts)s
  AND ({version}, pp.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY {version}, pp.id::text
LIMIT %(limit)s
"""
SQL_PRODUCTS = _SQL_PRODUCTS.format(version=_PRODUCT_VERSION)
PRODUCT_COLUMNS = (
    "product_id",
    "template_id",
    "default_code",
    "name",
    "category",
    "company_id",
    "list_price",
    "product_type",
    "is_storable",
    "tracking",
    "use_expiration_date",
    "sale_ok",
    "available_in_pos",
    "active",
    "version_ts",
)


def transform_product(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["product_id"]),
        int(row["template_id"]),
        str(row["default_code"] or ""),
        str(row["name"] or ""),
        str(row["category"] or ""),
        int(row["company_id"]) if row["company_id"] is not None else None,  # None = produk lintas company
        Decimal(str(row["list_price"] or 0)),
        str(row["product_type"] or ""),
        1 if row["is_storable"] else 0,
        str(row["tracking"] or "none"),
        1 if row["use_expiration_date"] else 0,
        1 if row["sale_ok"] else 0,
        1 if row["available_in_pos"] else 0,
        1 if row["active"] else 0,
        to_utc(row["version_ts"]),
    ]


def pos_payments_job(points_payment: bool = False) -> Job:
    return Job(
        "pos_payments",
        "odoo",
        pos_payments_sql(points_payment),
        "fact_pos_payment",
        POS_PAYMENT_COLUMNS,
        transform_pos_payment,
    )


POS_PAYMENTS = pos_payments_job()
POS_OUTLETS = Job("pos_outlets", "odoo", SQL_POS_OUTLETS, "dim_outlet", OUTLET_COLUMNS, transform_outlet)
PRODUCTS = Job("products", "odoo", SQL_PRODUCTS, "dim_product", PRODUCT_COLUMNS, transform_product)
POS_JOBS = (POS_OUTLETS, PRODUCTS, POS_PAYMENTS)


def pos_jobs(points_payment: bool = False) -> tuple[Job, ...]:
    return (POS_OUTLETS, PRODUCTS, pos_payments_job(points_payment))
