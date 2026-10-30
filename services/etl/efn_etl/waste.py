"""Gelombang 32 (docs/04 §21, K25-08): mart waste & yield Central Kitchen dari Odoo `efn_ck_production`.

Sumber kebenaran = Odoo (G28): `stock.scrap` beralasan + `efn_waste_value`, `mrp.production` dengan yield/varians/alert
tersimpan, ambang per PT `res.company.efn_ck_*`. Job incremental (cursor `write_date, id`) -> ReplacingMergeTree
(versi `write_date`); baris yang tidak lagi `done` ditulis ulang dengan `is_done = 0` dan disaring view.

PDP / minimisasi: tanpa `owner_id` (partner konsinyasi), `user_id`/`create_uid`, `origin`, nama dokumen, lot, partner.
Yang disalin: id teknis, PT, gudang, produk, alasan, qty, nilai, cap waktu.

Komponen MO (`fact_mo_component`): rumus = Odoo `mrp.production._efn_variance_rows`:
expected = Σ product_uom_qty (UoM produk) × yield/100, actual = Σ quantity move `done` (UoM produk),
variance_value = (actual − expected) × standard_price PT. Kursor = `mrp_production.write_date` + kunci MO:produk (MO
selesai menulis ulang dirinya -> semua komponennya dibaca ulang).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from efn_etl.jobs import Job
from efn_etl.watermark import to_utc

_MAX_LABEL = 128
Q3 = Decimal("0.001")
Q2 = Decimal("0.01")


def _dec(value: Any) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _label(value: Any) -> str:
    return ("" if value is None else str(value).strip())[:_MAX_LABEL]


def _opt_ts(value: Any) -> datetime | None:
    return to_utc(value) if isinstance(value, datetime) else None


# ---------------------------------------------------------------- stock_scrap -> fact_scrap
SQL_SCRAPS = """
SELECT s.id AS scrap_id, s.company_id, COALESCE(c.efn_brand_code, '') AS brand,
       COALESCE(s.efn_warehouse_id, 0) AS warehouse_id, s.product_id, COALESCE(pt.categ_id, 0) AS categ_id,
       (SELECT MIN(rel.stock_scrap_reason_tag_id) FROM stock_scrap_stock_scrap_reason_tag_rel rel
         WHERE rel.stock_scrap_id = s.id) AS reason_id,
       COALESCE(s.production_id, 0) AS production_id, s.scrap_qty * u.factor / pu.factor AS qty,
       COALESCE(s.efn_waste_value, 0) AS waste_value, s.state, s.date_done, s.write_date,
       s.write_date AS _cursor_ts, s.id::text AS _cursor_key
FROM stock_scrap s
JOIN res_company c ON c.id = s.company_id
JOIN product_product pp ON pp.id = s.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
JOIN uom_uom u ON u.id = s.product_uom_id
JOIN uom_uom pu ON pu.id = pt.uom_id
WHERE s.write_date >= %(cursor_ts)s
  AND (s.write_date, s.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY s.write_date, s.id::text
LIMIT %(limit)s
"""
SCRAP_COLUMNS = (
    "scrap_id",
    "company_id",
    "brand",
    "warehouse_id",
    "product_id",
    "categ_id",
    "reason_id",
    "production_id",
    "qty",
    "waste_value",
    "is_done",
    "date_done",
    "write_date",
)


def transform_scrap(row: dict[str, Any]) -> list[Any]:
    done = str(row.get("state") or "") == "done"
    date_done = _opt_ts(row.get("date_done")) or to_utc(row["write_date"])
    return [
        int(row["scrap_id"]),
        int(row["company_id"]),
        str(row.get("brand") or ""),
        int(row.get("warehouse_id") or 0),
        int(row["product_id"]),
        int(row.get("categ_id") or 0),
        int(row.get("reason_id") or 0),
        int(row.get("production_id") or 0),
        _dec(row.get("qty")).quantize(Q3),
        _dec(row.get("waste_value")).quantize(Q2),
        1 if done else 0,
        date_done,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- stock_scrap_reason_tag -> dim_scrap_reason
SQL_REASONS = """
SELECT r.id AS reason_id, r.name, r.write_date, r.write_date AS _cursor_ts, r.id::text AS _cursor_key
FROM stock_scrap_reason_tag r
WHERE r.write_date >= %(cursor_ts)s
  AND (r.write_date, r.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY r.write_date, r.id::text
LIMIT %(limit)s
"""
REASON_COLUMNS = ("reason_id", "name", "write_date")


def reason_name(value: Any) -> str:
    """Nama tag (jsonb terjemahan di Odoo 19: id_ID lalu en_US; string biasa diterima apa adanya)."""
    if isinstance(value, dict):
        return _label(value.get("id_ID") or value.get("en_US") or next(iter(value.values()), ""))
    return _label(value)


def transform_reason(row: dict[str, Any]) -> list[Any]:
    return [int(row["reason_id"]), reason_name(row.get("name")), to_utc(row["write_date"])]


# ---------------------------------------------------------------- mrp_production -> fact_mo_yield
SQL_MO_YIELD = """
SELECT mo.id AS production_id, mo.company_id, COALESCE(c.efn_brand_code, '') AS brand,
       COALESCE(spt.warehouse_id, 0) AS warehouse_id, mo.product_id, COALESCE(mo.bom_id, 0) AS bom_id,
       mo.product_qty * u.factor / pu.factor AS planned_qty,
       COALESCE(mo.qty_producing, 0) * u.factor / pu.factor AS produced_qty,
       COALESCE(mo.efn_yield_pct, 0) AS yield_pct, COALESCE(mo.efn_byproduct_qty, 0) AS byproduct_qty,
       COALESCE(mo.efn_variance_value, 0) AS variance_value, COALESCE(mo.efn_yield_alert, FALSE) AS alert,
       mo.state, mo.date_finished, mo.write_date,
       mo.write_date AS _cursor_ts, mo.id::text AS _cursor_key
FROM mrp_production mo
JOIN res_company c ON c.id = mo.company_id
JOIN product_product pp ON pp.id = mo.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
JOIN uom_uom u ON u.id = mo.product_uom_id
JOIN uom_uom pu ON pu.id = pt.uom_id
LEFT JOIN stock_picking_type spt ON spt.id = mo.picking_type_id
WHERE mo.write_date >= %(cursor_ts)s
  AND (mo.write_date, mo.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY mo.write_date, mo.id::text
LIMIT %(limit)s
"""
MO_COLUMNS = (
    "production_id",
    "company_id",
    "brand",
    "warehouse_id",
    "product_id",
    "bom_id",
    "planned_qty",
    "produced_qty",
    "yield_pct",
    "byproduct_qty",
    "variance_value",
    "alert",
    "is_done",
    "date_finished",
    "write_date",
)


def transform_mo(row: dict[str, Any]) -> list[Any]:
    done = str(row.get("state") or "") == "done"
    finished = _opt_ts(row.get("date_finished")) or to_utc(row["write_date"])
    return [
        int(row["production_id"]),
        int(row["company_id"]),
        str(row.get("brand") or ""),
        int(row.get("warehouse_id") or 0),
        int(row["product_id"]),
        int(row.get("bom_id") or 0),
        _dec(row.get("planned_qty")).quantize(Q3),
        _dec(row.get("produced_qty")).quantize(Q3) if done else Decimal("0.000"),
        _dec(row.get("yield_pct")).quantize(Q2) if done else Decimal("0.00"),
        _dec(row.get("byproduct_qty")).quantize(Q3) if done else Decimal("0.000"),
        _dec(row.get("variance_value")).quantize(Q2) if done else Decimal("0.00"),
        1 if (done and row.get("alert")) else 0,
        1 if done else 0,
        finished,
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- stock_move (bahan MO) -> fact_mo_component
# Satu baris per (MO, komponen). Kunci kursor = `<mo.id 12 digit>:<product_id 12 digit>` (unik & urut leksikal sama
# dengan urutan numerik) -> halaman yang terpotong di tengah MO dilanjutkan dari komponen berikutnya, tidak dilompati.
SQL_MO_COMPONENTS = """
SELECT mo.id AS production_id, mo.company_id, COALESCE(spt.warehouse_id, 0) AS warehouse_id,
       m.product_id, mo.state,
       SUM(m.product_uom_qty * mu.factor / pu.factor) * COALESCE(mo.efn_yield_pct, 0) / 100.0 AS expected_qty,
       SUM(CASE WHEN m.state = 'done' THEN m.quantity * mu.factor / pu.factor ELSE 0 END) AS actual_qty,
       MAX((pp.standard_price ->> mo.company_id::text)::numeric) AS unit_cost,
       mo.date_finished, mo.write_date,
       mo.write_date AS _cursor_ts,
       lpad(mo.id::text, 12, '0') || ':' || lpad(m.product_id::text, 12, '0') AS _cursor_key
FROM mrp_production mo
JOIN stock_move m ON m.raw_material_production_id = mo.id AND m.state IN ('done', 'cancel')
JOIN uom_uom mu ON mu.id = m.product_uom
JOIN product_product pp ON pp.id = m.product_id
JOIN product_template pt ON pt.id = pp.product_tmpl_id
JOIN uom_uom pu ON pu.id = pt.uom_id
LEFT JOIN stock_picking_type spt ON spt.id = mo.picking_type_id
WHERE mo.write_date >= %(cursor_ts)s
  AND (mo.write_date, lpad(mo.id::text, 12, '0') || ':' || lpad(m.product_id::text, 12, '0'))
      > (%(cursor_ts)s, %(cursor_key)s)
GROUP BY mo.id, mo.company_id, spt.warehouse_id, m.product_id, mo.state, mo.efn_yield_pct, mo.date_finished,
         mo.write_date
ORDER BY mo.write_date, _cursor_key
LIMIT %(limit)s
"""
COMPONENT_COLUMNS = (
    "production_id",
    "company_id",
    "warehouse_id",
    "product_id",
    "expected_qty",
    "actual_qty",
    "variance_qty",
    "variance_pct",
    "variance_value",
    "is_done",
    "date_finished",
    "write_date",
)


def variance_pct(expected: Decimal, diff: Decimal) -> Decimal:
    """Persen selisih terhadap BoM; expected 0 -> 100 bila ada selisih (sama dengan Odoo)."""
    if expected:
        return (Decimal(100) * diff / expected).quantize(Decimal("0.1"))
    return Decimal("100.0") if diff else Decimal("0.0")


def transform_component(row: dict[str, Any]) -> list[Any]:
    done = str(row.get("state") or "") == "done"
    expected = _dec(row.get("expected_qty"))
    actual = _dec(row.get("actual_qty"))
    diff = actual - expected
    cost = _dec(row.get("unit_cost"))
    return [
        int(row["production_id"]),
        int(row["company_id"]),
        int(row.get("warehouse_id") or 0),
        int(row["product_id"]),
        expected.quantize(Q3),
        actual.quantize(Q3),
        diff.quantize(Q3),
        variance_pct(expected, diff),
        (diff * cost).quantize(Q2),
        1 if done else 0,
        _opt_ts(row.get("date_finished")) or to_utc(row["write_date"]),
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- res_company -> dim_ck_threshold
SQL_THRESHOLDS = """
SELECT c.id AS company_id, COALESCE(c.efn_brand_code, '') AS brand,
       COALESCE(c.efn_ck_yield_warn_pct, 0) AS yield_warn_pct,
       COALESCE(c.efn_ck_variance_warn_pct, 0) AS variance_warn_pct,
       COALESCE(c.efn_ck_waste_warn_value, 0) AS waste_warn_value, c.write_date,
       c.write_date AS _cursor_ts, c.id::text AS _cursor_key
FROM res_company c
WHERE c.write_date >= %(cursor_ts)s
  AND (c.write_date, c.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY c.write_date, c.id::text
LIMIT %(limit)s
"""
THRESHOLD_COLUMNS = ("company_id", "brand", "yield_warn_pct", "variance_warn_pct", "waste_warn_value", "write_date")


def transform_threshold(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["company_id"]),
        str(row.get("brand") or ""),
        _dec(row.get("yield_warn_pct")).quantize(Q2),
        _dec(row.get("variance_warn_pct")).quantize(Q2),
        _dec(row.get("waste_warn_value")).quantize(Q2),
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- stock_warehouse -> dim_warehouse
SQL_WAREHOUSES = """
SELECT w.id AS warehouse_id, COALESCE(w.name, '') AS name, w.company_id,
       COALESCE(w.efn_is_central_kitchen, FALSE) AS is_ck, COALESCE(w.active, FALSE) AS active, w.write_date,
       w.write_date AS _cursor_ts, w.id::text AS _cursor_key
FROM stock_warehouse w
WHERE w.write_date >= %(cursor_ts)s
  AND (w.write_date, w.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY w.write_date, w.id::text
LIMIT %(limit)s
"""
WAREHOUSE_COLUMNS = ("warehouse_id", "name", "company_id", "is_ck", "active", "write_date")


def transform_warehouse(row: dict[str, Any]) -> list[Any]:
    return [
        int(row["warehouse_id"]),
        _label(row.get("name")),
        int(row["company_id"]),
        1 if row.get("is_ck") else 0,
        1 if row.get("active") else 0,
        to_utc(row["write_date"]),
    ]


def waste_jobs() -> tuple[Job, ...]:
    """Job incremental G32 (`ETL_ENABLE_WASTE`, butuh `efn_ck_production` + grant G32)."""
    return (
        Job("waste_reasons", "odoo", SQL_REASONS, "dim_scrap_reason", REASON_COLUMNS, transform_reason),
        Job("ck_thresholds", "odoo", SQL_THRESHOLDS, "dim_ck_threshold", THRESHOLD_COLUMNS, transform_threshold),
        Job("ck_warehouses", "odoo", SQL_WAREHOUSES, "dim_warehouse", WAREHOUSE_COLUMNS, transform_warehouse),
        Job("waste_scraps", "odoo", SQL_SCRAPS, "fact_scrap", SCRAP_COLUMNS, transform_scrap),
        Job("mo_yield", "odoo", SQL_MO_YIELD, "fact_mo_yield", MO_COLUMNS, transform_mo),
        Job("mo_components", "odoo", SQL_MO_COMPONENTS, "fact_mo_component", COMPONENT_COLUMNS, transform_component),
    )
