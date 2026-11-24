"""DDL ClickHouse cocok dengan kolom transform; grant esb_writer cocok dengan tabel; view & retensi sesuai §25."""

import re
from pathlib import Path

from efn_esb.aggregates import AGGREGATES, LOCK_TABLE, REFRESH_COLUMNS, REFRESH_TABLE, table_definition
from efn_esb.records import CONTROL_TABLES
from efn_esb.transform import MART_TABLES

ROOT = Path(__file__).resolve().parents[3]
DDL = (ROOT / "infra" / "clickhouse" / "init" / "12-g41-esb.sql").read_text(encoding="utf-8")
DDL_G43 = (ROOT / "infra" / "clickhouse" / "init" / "14-g43-migration.sql").read_text(encoding="utf-8")
DDL_G65 = (ROOT / "infra" / "clickhouse" / "init" / "23-g65-esb-purchase-stock.sql").read_text(encoding="utf-8")
# Kolom mart G41 yang ditambahkan belakangan lewat ALTER idempoten (G65: dim_esb_supplier.supplier_id).
ALTER_ADDED = set(re.findall(r"ALTER TABLE efn_dwh\.(\w+) ADD COLUMN IF NOT EXISTS (\w+) ", DDL_G65))
DDL_AGG = (ROOT / "infra" / "clickhouse" / "init" / "20-esb-aggregates.sql").read_text(encoding="utf-8")
DDL_MARGIN = (ROOT / "infra" / "clickhouse" / "init" / "24-margin-bahan.sql").read_text(encoding="utf-8")
ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "esb-access.sql").read_text(encoding="utf-8")
ACCESS_CODE = re.sub(r"--[^\n]*", "", ACCESS)
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
    control20 = {f"efn_esb.{REFRESH_TABLE}", f"efn_esb.{LOCK_TABLE}"}
    margin24 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", DDL_MARGIN))  # G67, versi-insert
    ddl26 = (ROOT / "infra" / "clickhouse" / "init" / "26-g69-esb-oms.sql").read_text(encoding="utf-8")
    oms26 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (efn_\w+\.\w+)", ddl26))  # G69 ESB OMS
    expected = (ddl_tables() | g43 | g65 | control20 | margin24 | oms26) - {"efn_dwh.map_esb_odoo"}
    assert granted("esb_writer", "SELECT, INSERT") == expected
    assert "REVOKE ALL ON efn_dwh.map_esb_odoo FROM esb_writer;" in ACCESS
    writer_lines = [line for line in ACCESS.splitlines() if "TO esb_writer" in line]
    # Selain SELECT+INSERT: hanya grant tabel agregat Insight (diuji persis di test_esb_writer_aggregate_grants_exact).
    plain = [line for line in writer_lines if "agg_esb_" not in line and not line.startswith("GRANT SELECT ON ")]
    assert all(line.startswith("GRANT SELECT, INSERT ON ") for line in plain)
    assert not re.search(r"GRANT (ALL|ALTER|DROP|TRUNCATE|CREATE|OPTIMIZE)", ACCESS)
    # ALTER/TRUNCATE hanya pada tabel agregat Insight; OPTIMIZE tidak pernah.
    for line in ACCESS_CODE.splitlines():
        if re.search(r"\b(ALTER|TRUNCATE)\b", line) and "TO esb_writer" in line:
            assert "efn_dwh.agg_esb_" in line, line
    assert not re.search(r"\bOPTIMIZE\b[^;]*TO esb_writer", ACCESS_CODE)


# ------------------------------------------------------------------ tabel agregat Insight (init/20-esb-aggregates.sql)
def test_aggregate_ddl_idempotent_and_matches_definition() -> None:
    code = re.sub(r"--[^\n]*", "", DDL_AGG)
    # Hanya CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW (dijalankan ulang setiap deploy); tidak pernah
    # menghapus/mengubah/mengisi.
    assert not re.search(r"\b(DROP|TRUNCATE|ALTER|INSERT|EXCHANGE|RENAME|OPTIMIZE)\b", code)
    created = re.findall(r"CREATE TABLE (IF NOT EXISTS )?(efn_\w+\.\w+)", code)
    assert all(ine for ine, _ in created)
    assert {t for _, t in created} == (
        {f"efn_dwh.{a.name}" for a in AGGREGATES}
        | {f"efn_dwh.{a.build}" for a in AGGREGATES}
        | {f"efn_esb.{REFRESH_TABLE}", f"efn_esb.{LOCK_TABLE}"}
    )
    assert set(re.findall(r"CREATE OR REPLACE VIEW (efn_\w+\.\w+) AS", code)) == {"efn_dwh.v_esb_menu_mix_agg"}
    for agg in AGGREGATES:
        # Teks init = table_definition() (kolom/tipe dari view sumber, MergeTree, partisi bulan, policy terenkripsi).
        definition = table_definition(agg, "efn_dwh")
        assert f"CREATE TABLE IF NOT EXISTS efn_dwh.{agg.name}\n{definition};" in DDL_AGG
        assert "ENGINE = MergeTree\n" in definition and "SETTINGS storage_policy = 'encrypted'\n" in definition
        assert f"PARTITION BY toYYYYMM({agg.date_column})\n" in definition
        assert agg.date_column == agg.columns[0] and agg.date_column in agg.order_by
        assert f"FROM efn_dwh.{agg.source}\nWHERE 0" in definition
        # Build permanen dibuat admin dengan struktur identik (syarat REPLACE PARTITION), SETELAH agg-nya.
        build = f"CREATE TABLE IF NOT EXISTS efn_dwh.{agg.build} AS efn_dwh.{agg.name};"
        assert build in DDL_AGG
        assert DDL_AGG.index(build) > DDL_AGG.index(f"CREATE TABLE IF NOT EXISTS efn_dwh.{agg.name}\n")


def test_aggregate_lock_table() -> None:
    block = DDL_AGG.split(f"CREATE TABLE IF NOT EXISTS efn_esb.{LOCK_TABLE}", 1)[1].split(";", 1)[0]
    # Waktu diisi server (urutan pemegang tidak bergantung jam container); retensi pendek.
    assert "started_at    DateTime64(3, 'UTC') DEFAULT now64(3)" in block
    assert "heartbeat_at  DateTime64(3, 'UTC') DEFAULT now64(3)" in block
    assert "released      UInt8 DEFAULT 0" in block and "run_id        UUID" in block
    assert "TTL toDateTime(heartbeat_at) + INTERVAL 1 DAY" in block


def test_aggregate_refresh_table_columns() -> None:
    body = DDL_AGG.split(f"CREATE TABLE IF NOT EXISTS efn_esb.{REFRESH_TABLE}\n", 1)[1].split("ENGINE", 1)[0]
    assert re.findall(r"^\s{4}(\w+)\s", body, re.MULTILINE) == list(REFRESH_COLUMNS)
    block = DDL_AGG.split(f"CREATE TABLE IF NOT EXISTS efn_esb.{REFRESH_TABLE}", 1)[1].split(";", 1)[0]
    assert "ENGINE = MergeTree" in block and "TTL toDateTime(finished_at) + INTERVAL 400 DAY" in block


def test_esb_writer_aggregate_grants_exact() -> None:
    # SEC-AG-01: tanpa CREATE/DROP TABLE (engine & storage policy hanya ditetapkan admin).
    aggs = {f"efn_dwh.{a.name}" for a in AGGREGATES}
    builds = {f"efn_dwh.{a.build}" for a in AGGREGATES}
    assert set(re.findall(r"GRANT SELECT, INSERT, ALTER DELETE ON (efn_\w+\.\w+) TO esb_writer;", ACCESS_CODE)) == aggs
    assert set(re.findall(r"GRANT SELECT, INSERT, TRUNCATE ON (efn_\w+\.\w+) TO esb_writer;", ACCESS_CODE)) == builds
    grant_lines = [line for line in ACCESS_CODE.splitlines() if line.startswith("GRANT ") and "TO esb_writer" in line]
    assert not [line for line in grant_lines if re.search(r"\b(CREATE|DROP)\b", line)]
    # Grant CREATE/DROP TABLE dari deploy sebelumnya dicabut eksplisit pada keenam tabel.
    revoked = set(re.findall(r"REVOKE CREATE TABLE, DROP TABLE ON (efn_\w+\.\w+) FROM esb_writer;", ACCESS_CODE))
    assert revoked == aggs | builds
    assert not re.search(r"ON efn_\w+\.\*[^;]*TO esb_writer", ACCESS_CODE)
    assert not re.search(r"\b(CREATE|DROP) (DATABASE|VIEW|DICTIONARY)\b", ACCESS_CODE)
    # Sumber refresh: SELECT 3 view (INVOKER -> tabel dasarnya sudah SELECT, diuji di bawah).
    assert set(re.findall(r"GRANT SELECT ON (efn_dwh\.v_\w+) TO esb_writer;", ACCESS_CODE)) == {
        f"efn_dwh.{a.source}" for a in AGGREGATES
    }
    readable = granted("esb_writer", "SELECT, INSERT")
    view = DDL + (ROOT / "infra" / "clickhouse" / "init" / "13-g42-insight.sql").read_text(encoding="utf-8")
    for agg in AGGREGATES:
        body = view.split(f"CREATE OR REPLACE VIEW efn_dwh.{agg.source} AS", 1)[1].split(";", 1)[0]
        base = set(re.findall(r"\b(efn_(?:dwh|esb)\.\w+)", re.sub(r"--[^\n]*", "", body)))
        assert base <= readable, (agg.source, base - readable)


def test_esb_writer_profile_allows_ddl_only_via_grants() -> None:
    # allow_ddl=0 menolak SEMUA DDL termasuk TRUNCATE/ALTER yang di-grant -> 1 CONST; batas = grant per tabel.
    for stmt in (
        "CREATE SETTINGS PROFILE IF NOT EXISTS esb_writer_profile",
        "ALTER SETTINGS PROFILE esb_writer_profile",
    ):
        body = ACCESS.split(stmt, 1)[1].split(";", 1)[0]
        assert "allow_ddl = 1 CONST" in body and "readonly = 0 CONST" in body, stmt
        assert "allow_introspection_functions = 0 CONST" in body, stmt
    assert "allow_ddl = 0" not in ACCESS


def test_init_script_aggregate_guards() -> None:
    # SEC-AG-01: 6 tabel wajib MergeTree + encrypted (fail-closed, cabut hak esb_writer); kolom != view -> dibuat ulang
    # admin SEBELUM access/*.sql (wrapper efn_insight membaca agg).
    for agg in AGGREGATES:
        assert f"'{agg.name}'" in INIT_SH and f"'{agg.build}'" in INIT_SH and f"'{agg.source}'" in INIT_SH
    guard = INIT_SH.index('AGG_BAD="$($CH --query')
    assert "engine = 'MergeTree' AND storage_policy = 'encrypted'" in INIT_SH[guard:]
    assert 'REVOKE ALL ON efn_dwh.${t} FROM esb_writer" < /dev/null' in INIT_SH[guard:]
    assert INIT_SH.index("system.row_policies WHERE database = 'efn_insight'") < guard < INIT_SH.index('rm -f "$CFG"')
    rebuild = INIT_SH.index('AGG_STALE="$($CH --query')
    assert INIT_SH.index("for f in /init/schema/*.sql") < rebuild < INIT_SH.index("for f in /init/access/*.sql")
    assert "$CH --multiquery < /init/schema/20-esb-aggregates.sql" in INIT_SH
    assert "arraySort(groupArray((position, name, type)))" in INIT_SH  # nama + tipe + urutan kolom


def test_metabase_reader_views_without_raw() -> None:
    grants = granted("metabase_reader", "SELECT")
    assert {f"efn_dwh.{v}" for v in VIEWS} <= grants
    assert "efn_esb.raw_doc" not in grants and "efn_esb.bom_ratio" not in grants


def test_init_script_creates_and_binds_user() -> None:
    assert "create_user efn_esb /run/secrets/clickhouse_password_esb" in INIT_SH
    assert "GRANT esb_writer TO efn_esb;" in INIT_SH
    assert "ALTER USER efn_esb SETTINGS PROFILE 'esb_writer_profile';" in INIT_SH
    assert "efn_t2s.v_esb" not in INIT_SH  # row policy ditunda ke G42 (§25.7)
