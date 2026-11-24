"""DDL ClickHouse cocok dengan kolom transform; grant esb_writer cocok dengan tabel; view & retensi sesuai §25."""

import re
from pathlib import Path

from efn_esb.records import CONTROL_TABLES
from efn_esb.transform import MART_TABLES

ROOT = Path(__file__).resolve().parents[3]
DDL = (ROOT / "infra" / "clickhouse" / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")
DDL_G43 = (ROOT / "infra" / "clickhouse" / "init" / "14-g43-migration.sql").read_text(encoding="utf-8")
DDL_G65 = (ROOT / "infra" / "clickhouse" / "init" / "23-g65-esb-purchase-stock.sql").read_text(encoding="utf-8")
DDL_G67 = (ROOT / "infra" / "clickhouse" / "init" / "24-g67-esb-pl-waste-fillrate.sql").read_text(encoding="utf-8")
# Kolom mart G41 yang ditambahkan belakangan lewat ALTER idempoten (G65: dim_esb_supplier.supplier_id).
ALTER_ADDED = set(re.findall(r"ALTER TABLE efn_dwh\.(\w+) ADD COLUMN IF NOT EXISTS (\w+) ", DDL_G65))
ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "esb-access.sql").read_text(encoding="utf-8")
INIT_SH = (ROOT / "infra" / "clickhouse" / "clickhouse-init.sh").read_text(encoding="utf-8")
VIEWS = ("v_esb_sales_daily", "v_esb_payment_daily", "v_esb_flow_daily", "v_esb_menu_mix", "v_esb_data_quality")


def table_body(qualified: str) -> str:
    return DDL.split(f"CREATE TABLE IF NOT EXISTS {qualified}\n", 1)[1].split("ENGINE", 1)[0]


def ddl_tables() -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", DDL))


def test_mart_columns_match_transform() -> None:
    for table, columns in MART_TABLES.items():
        body = table_body(f"efn_dwh.{table}")
        for column in columns:
            if (table, column) in ALTER_ADDED:
                continue
            assert re.search(rf"^\s+{column}\s", body, re.MULTILINE), (table, column)
        assert re.search(r"^\s+_loaded_at\s", body, re.MULTILINE), table


def test_control_columns_match_records() -> None:
    for table, columns in CONTROL_TABLES.items():
        body = table_body(f"efn_esb.{table}")
        for column in columns:
            assert re.search(rf"^\s+{column}\s", body, re.MULTILINE), (table, column)


def test_all_tables_are_esb_scoped_and_idempotent() -> None:
    tables = ddl_tables()
    # map_esb_odoo pindah ke 14-g43-migration.sql (kunci baru, docs/04 §27.10).
    assert tables == {f"efn_dwh.{t}" for t in MART_TABLES} | {f"efn_esb.{t}" for t in CONTROL_TABLES}
    assert all(t.startswith("efn_esb.") or "esb" in t.split(".")[1] for t in tables)
    assert "CREATE DATABASE IF NOT EXISTS efn_esb;" in DDL
    assert not re.search(r"\bDROP\b|\bTRUNCATE\b|\bALTER\b", DDL)


def test_engines_and_retention() -> None:
    raw = DDL.split("CREATE TABLE IF NOT EXISTS efn_esb.raw_doc", 1)[1].split(";", 1)[0]
    assert "ReplacingMergeTree(fetched_at)" in raw and "TTL doc_date + INTERVAL 36 MONTH" in raw
    assert "ORDER BY (brand, endpoint, doc_key)" in raw and "CODEC(ZSTD" in raw
    run = DDL.split("CREATE TABLE IF NOT EXISTS efn_esb.extract_run", 1)[1].split(";", 1)[0]
    assert "ENGINE = MergeTree" in run and "TTL" not in run
    checkpoint = DDL.split("CREATE TABLE IF NOT EXISTS efn_esb.extract_checkpoint", 1)[1].split(";", 1)[0]
    assert "ReplacingMergeTree(finished_at)" in checkpoint and "TTL" not in checkpoint
    for table in MART_TABLES:
        block = DDL.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{table}", 1)[1].split(";", 1)[0]
        assert "ReplacingMergeTree(_version)" in block and "TTL" not in block, table
    sales = DDL.split("CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_sales_line", 1)[1].split(";", 1)[0]
    assert "ORDER BY (company_code, brand, branch_id, sales_date, sales_num, line_no)" in sales


def test_views_present_and_not_forbidden() -> None:
    for view in VIEWS:
        assert f"CREATE OR REPLACE VIEW efn_dwh.{view} AS" in DDL, view
    assert "v_esb_sales_hourly" not in DDL.replace("Tidak ada v_esb_sales_hourly", "")  # L-112
    assert "CREATE OR REPLACE VIEW efn_dwh.v_esb_stock_cover" not in DDL
    # Fakta dibaca hanya dari versi slice terakhir.
    assert DDL.count("FROM efn_esb.extract_checkpoint FINAL") >= 8


def test_flow_view_semantics_documented() -> None:
    flow = DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_esb_flow_daily AS", 1)[1].split(";", 1)[0]
    for column in ("ck_prod", "ck_out", "in_ck", "in_other", "store_prod", "st_in", "st_out", "sold"):
        assert f"AS {column}" in flow, column
    assert "x.source = 'production_result' OR ckp.is_ck = 1" in flow
    assert "x.source = 'simple_mfg' AND ckp.is_ck = 0" in flow


def test_review_ddl_views_only_and_flow_stays_fin() -> None:
    # 19-insight-review.sql (21 Nov 2026): hanya view baru, tanpa tabel/ubah skema; v_esb_flow_daily tetap FIN-only
    # (semantik L-116 tidak berubah) -- pergerakan non-FIN dibaca dari v_esb_movement_daily.
    review = (ROOT / "infra" / "clickhouse" / "init" / "19-insight-review.sql").read_text(encoding="utf-8")
    code = re.sub(r"--[^\n]*", "", review)
    assert set(re.findall(r"CREATE OR REPLACE VIEW (efn_\w+\.\w+) AS", code)) == {
        "efn_dwh.v_esb_movement_daily",
        "efn_dwh.v_esb_coverage",
    }
    assert not re.search(r"\b(CREATE TABLE|DROP|TRUNCATE|ALTER|INSERT)\b", code)
    assert "efn_esb.raw_doc" not in code and "efn_esb.bom_ratio" not in code
    flow = DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_esb_flow_daily AS", 1)[1].split(";", 1)[0]
    assert flow.count("WHERE is_fin = 1 AND") == 5  # produksi, delivery x2, transfer x2


def granted(role: str, privileges: str) -> set[str]:
    return set(re.findall(rf"GRANT {privileges} ON (efn_\w+\.\w+) TO {role};", ACCESS))


def test_esb_writer_grants_match_tables_exactly() -> None:
    # SEC-188 / L-130: map_esb_odoo tidak pernah ditulis esb-extract (penulis tunggal efn_mig).
    g43 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", DDL_G43))
    g65 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", DDL_G65))
    g67 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", DDL_G67))
    assert granted("esb_writer", "SELECT, INSERT") == (ddl_tables() | g43 | g65 | g67) - {"efn_dwh.map_esb_odoo"}
    assert "REVOKE ALL ON efn_dwh.map_esb_odoo FROM esb_writer;" in ACCESS
    writer_lines = [line for line in ACCESS.splitlines() if "TO esb_writer" in line]
    assert all(line.startswith("GRANT SELECT, INSERT ON ") for line in writer_lines)
    assert "allow_ddl = 0 CONST" in ACCESS
    assert not re.search(r"GRANT (ALL|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE)", ACCESS)


def test_metabase_reader_views_without_raw() -> None:
    grants = granted("metabase_reader", "SELECT")
    assert {f"efn_dwh.{v}" for v in VIEWS} <= grants
    assert "efn_esb.raw_doc" not in grants and "efn_esb.bom_ratio" not in grants


def test_init_script_creates_and_binds_user() -> None:
    assert "create_user efn_esb /run/secrets/clickhouse_password_esb" in INIT_SH
    assert "GRANT esb_writer TO efn_esb;" in INIT_SH
    assert "ALTER USER efn_esb SETTINGS PROFILE 'esb_writer_profile';" in INIT_SH
    assert "efn_t2s.v_esb" not in INIT_SH  # row policy ditunda ke G42 (§25.7)
