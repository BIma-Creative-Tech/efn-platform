"""G47 (docs/04 §28.13.8): DDL 17-g47-insight-sm.sql = engine/kunci/partisi/TTL kontrak; kolom job ⊆ DDL. Statis."""

import re
from pathlib import Path

import pytest

from efn_etl import sm, waste
from efn_etl.pos import PRODUCT_COLUMNS

ROOT = Path(__file__).resolve().parents[3]
DDL = (ROOT / "infra" / "clickhouse" / "init" / "17-g47-insight-sm.sql").read_text(encoding="utf-8")
ETL_SM_ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "etl-sm-access.sql").read_text(encoding="utf-8")


def strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def table_block(table: str) -> str:
    head = f"CREATE TABLE IF NOT EXISTS efn_dwh.{table}"
    assert head in DDL, table
    return DDL.split(head, 1)[1].split(";", 1)[0]


def table_columns(table: str) -> list[str]:
    body = table_block(table).split("ENGINE", 1)[0]
    return [c for c in re.findall(r"^\s{4}(\w+)\s", body, re.MULTILINE) if c != "_loaded_at"]


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# Tabel §28.13.8: (engine, ORDER BY, PARTITION BY | None, TTL | None).
CONTRACT = {
    "dim_sm_warehouse": ("ReplacingMergeTree(write_date)", "warehouse_id", None, None),
    "dim_online_outlet": ("ReplacingMergeTree(write_date)", "(channel_code, outlet_code)", None, None),
    "dim_sm_kpi_config": ("ReplacingMergeTree(write_date)", "(company_id, key)", None, None),
    "fact_replenishment": (
        "ReplacingMergeTree(version_ts)",
        "(company_id, warehouse_id, delivery_date, proposal_id, line_id)",
        "toYYYYMM(delivery_date)",
        "delivery_date + INTERVAL 3 YEAR",
    ),
    "fact_replenishment_bias": (
        "MergeTree",
        "(company_id, warehouse_id, log_id)",
        "toYYYYMM(created_at)",
        "toDateTime(created_at) + INTERVAL 3 YEAR",
    ),
    "fact_dc_fill_rate": (
        "ReplacingMergeTree(computed_at)",
        "(company_id, warehouse_id, delivery_date, line_id)",
        "toYYYYMM(delivery_date)",
        "delivery_date + INTERVAL 3 YEAR",
    ),
    "fact_dc_purchase_line": (
        "ReplacingMergeTree(version_ts)",
        "(company_id, warehouse_id, order_id, line_id)",
        "toYYYYMM(date_order)",
        "toDateTime(date_order) + INTERVAL 3 YEAR",
    ),
    "dim_sm_supplier": ("ReplacingMergeTree(write_date)", "supplier_id", None, None),
    "agg_sm_stock_daily": (
        "ReplacingMergeTree(computed_at)",
        "(company_id, day, warehouse_id, product_id)",
        "toYYYYMM(day)",
        "day + INTERVAL 3 YEAR",
    ),
    "fact_lot_expiry": (
        "ReplacingMergeTree(write_date)",
        "(company_id, warehouse_id, alert_id)",
        "toYYYYMM(detected_date)",
        "detected_date + INTERVAL 2 YEAR",
    ),
    "fact_lot_markdown": (
        "ReplacingMergeTree(write_date)",
        "(company_id, warehouse_id, markdown_id)",
        "toYYYYMM(date_from)",
        "date_from + INTERVAL 2 YEAR",
    ),
    "agg_sm_expiry_daily": (
        "ReplacingMergeTree(computed_at)",
        "(company_id, day, warehouse_id, categ_id, stage)",
        "toYYYYMM(day)",
        "day + INTERVAL 3 YEAR",
    ),
    "fact_campaign_result": (
        "ReplacingMergeTree(computed_at)",
        "(company_id, campaign_id)",
        None,
        "date_to + INTERVAL 2 YEAR",
    ),
    "fact_sm_segment_run": (
        "ReplacingMergeTree(write_date)",
        "(company_id, generated_at, segment_code, run_id)",
        "toYYYYMM(generated_at)",
        "toDateTime(generated_at) + INTERVAL 2 YEAR",
    ),
}


@pytest.mark.parametrize("table", sorted(CONTRACT))
def test_engine_key_partition_ttl_match_contract(table: str) -> None:
    engine, order_by, partition, ttl = CONTRACT[table]
    block = norm(table_block(table).split("ENGINE", 1)[1])
    assert block.startswith(f"= {engine} "), (table, block[:60])
    assert f"ORDER BY {order_by}" in block, table
    if partition:
        assert f"PARTITION BY {partition}" in block, table
    else:
        assert "PARTITION BY" not in block, table
    if ttl:
        assert f"TTL {ttl}" in block, table
    else:
        assert "TTL" not in block, table


def test_contract_columns_present() -> None:
    expected = {
        "dim_sm_warehouse": "warehouse_id code name company_id company_code is_dc active write_date",
        "dim_online_outlet": "channel_code outlet_code outlet_id company_id warehouse_id active write_date",
        "dim_sm_kpi_config": "company_id key value_num value_str active write_date",
        "fact_dc_fill_rate": "line_id proposal_id company_id warehouse_id dc_warehouse_id product_id delivery_date "
        "qty_approved qty_shipped qty_received last_done_at last_received_at on_time picking_shared computed_at",
        "dim_sm_supplier": "supplier_id name write_date",
        "agg_sm_expiry_daily": "day company_id warehouse_id categ_id stage lots qty value_cost computed_at",
    }
    for table, cols in expected.items():
        assert table_columns(table) == cols.split(), table
    # Tidak ada kolom pelaku/partner/member di tabel G47.
    for table in CONTRACT:
        assert not set(table_columns(table)) & {
            "user_id",
            "approved_by",
            "changed_by",
            "activated_by",
            "maker_id",
            "partner_id",
            "member_id",
            "member_hash",
        }, table


JOB_TABLES = [
    ("dim_sm_warehouse", sm.WAREHOUSE_COLUMNS),
    ("dim_online_outlet", sm.ONLINE_OUTLET_COLUMNS),
    ("dim_sm_kpi_config", sm.KPI_COLUMNS),
    ("fact_replenishment", sm.REPLENISHMENT_COLUMNS),
    ("fact_replenishment_bias", sm.BIAS_COLUMNS),
    ("fact_dc_fill_rate", sm.FILL_COLUMNS),
    ("fact_dc_purchase_line", sm.PURCHASE_COLUMNS),
    ("dim_sm_supplier", sm.SUPPLIER_COLUMNS),
    ("fact_lot_expiry", sm.EXPIRY_COLUMNS),
    ("fact_lot_markdown", sm.MARKDOWN_COLUMNS),
    ("fact_campaign_result", sm.CAMPAIGN_COLUMNS),
    ("fact_sm_segment_run", sm.SEGMENT_COLUMNS),
]


@pytest.mark.parametrize(("table", "columns"), JOB_TABLES, ids=[t for t, _ in JOB_TABLES])
def test_job_columns_equal_ddl(table: str, columns: tuple[str, ...]) -> None:
    assert list(columns) == table_columns(table), table


def test_insert_select_column_lists_equal_ddl() -> None:
    for sql, table in ((sm.SQL_STOCK_AGG, "agg_sm_stock_daily"), (sm.SQL_EXPIRY_AGG, "agg_sm_expiry_daily")):
        cols = re.search(rf"INSERT INTO efn_dwh\.{table}\s*\(([^)]*)\)", sql)
        assert cols, table
        assert [c.strip() for c in cols.group(1).split(",")] == table_columns(table)
        # Hari dihitung ulang: kunci lama hari itu ikut dengan nilai 0 (tidak tertinggal angka basi).
        assert f"FROM efn_dwh.{table} AS a" in sql or f"FROM efn_dwh.{table}\n" in sql
        assert "WHERE day = {day:Date}" in sql or "WHERE a.day = {day:Date}" in sql
    assert "dim_sm_warehouse" in sm.SQL_STOCK_AGG  # hanya warehouse PT SM


def test_dim_product_category_columns_and_job() -> None:
    assert "ALTER TABLE efn_dwh.dim_product ADD COLUMN IF NOT EXISTS categ_id UInt32 DEFAULT 0;" in DDL
    assert "ALTER TABLE efn_dwh.dim_product ADD COLUMN IF NOT EXISTS categ_parent_path String DEFAULT '';" in DDL
    assert PRODUCT_COLUMNS[-3:] == ("categ_id", "categ_parent_path", "shelf_life_days")  # G51 menambah di akhir


def test_scrap_job_columns_equal_g32_ddl() -> None:
    assert sm.sm_scrap_job(("SMD",), False, False).columns == waste.SCRAP_COLUMNS
    assert sm.sm_scrap_job(("SMD",), True, True).target_table == "fact_scrap"


def test_ddl_idempotent_and_no_legacy_sources() -> None:
    body = strip_comments(DDL)
    assert not re.search(r"\b(DROP|TRUNCATE|INSERT|RENAME|DELETE)\b", body)
    assert "SELECT *" not in body
    # L-196: tanpa data/modul migrasi ESB.
    for forbidden in ("efn_esb", "fact_esb_", "fact_legacy_sales_daily", "map_esb_odoo", "v_fc_sales_daily"):
        assert forbidden not in body, forbidden
    assert body.count("CREATE TABLE IF NOT EXISTS efn_dwh.") == len(CONTRACT)


def test_etl_grants_cover_g47_tables_without_destructive_rights() -> None:
    for table in CONTRACT:
        assert f"GRANT SELECT, INSERT ON efn_dwh.{table} TO etl_writer;" in ETL_SM_ACCESS, table
    assert not re.search(r"GRANT [^;]*(ALTER|DROP|TRUNCATE|OPTIMIZE|ALL)", strip_comments(ETL_SM_ACCESS))
