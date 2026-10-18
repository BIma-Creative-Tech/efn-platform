from pathlib import Path

import pytest

from efn_text2sql.guard import GuardConfig, SqlGuardError, guard_sql
from efn_text2sql.semantic import load_semantic_layer

LAYER = load_semantic_layer(Path(__file__).resolve().parents[1] / "semantic_layer.yaml")
CONFIG = LAYER.guard_config()


# ------------------------------------------------------------------ positif


def test_config_from_semantic_layer() -> None:
    assert "efn_dwh.v_competitor_prices" in CONFIG.allowed_tables
    assert "efn_dwh.competitor_prices" not in CONFIG.allowed_tables
    assert CONFIG.max_limit == 1000


def test_simple_select_is_wrapped_with_limit_and_settings() -> None:
    result = guard_sql(
        "SELECT competitor_name, avg(price) AS avg_price FROM v_competitor_prices GROUP BY competitor_name",
        CONFIG,
    )
    assert result.sql.startswith("SELECT * FROM (")
    assert result.sql.endswith("LIMIT 1000 SETTINGS max_execution_time = 15")
    assert result.tables == ("efn_dwh.v_competitor_prices",)


def test_requested_limit_is_capped() -> None:
    assert guard_sql("SELECT * FROM v_sales_daily", CONFIG, limit=50).limit == 50
    assert guard_sql("SELECT * FROM v_sales_daily", CONFIG, limit=50_000).limit == 1000
    assert guard_sql("SELECT * FROM v_sales_daily", CONFIG, limit=0).limit == 1


def test_qualified_view_and_cte_and_union_allowed() -> None:
    guard_sql("SELECT day, revenue FROM efn_dwh.v_sales_daily WHERE brand = 'PB'", CONFIG)
    guard_sql("WITH t AS (SELECT * FROM v_competitor_prices) SELECT count() FROM t", CONFIG)
    result = guard_sql(
        "SELECT day FROM v_sales_daily UNION ALL SELECT day FROM v_loyalty_daily",
        CONFIG,
    )
    assert set(result.tables) == {"efn_dwh.v_sales_daily", "efn_dwh.v_loyalty_daily"}


def test_comments_are_stripped_from_executed_sql() -> None:
    result = guard_sql("SELECT price FROM v_competitor_prices /* ; DROP TABLE x */ -- ; DROP\n", CONFIG)
    assert "DROP" not in result.sql.upper()
    assert "/*" not in result.sql
    assert "--" not in result.sql


@pytest.mark.parametrize(("question", "sql"), LAYER.examples)
def test_semantic_layer_examples_pass_guard(question: str, sql: str) -> None:
    assert guard_sql(sql, CONFIG).sql


# ------------------------------------------------------------------ negatif

NEGATIVE = [
    # DDL / DML / perintah
    "DROP TABLE efn_dwh.v_competitor_prices",
    "DROP DATABASE efn_dwh",
    "TRUNCATE TABLE efn_dwh.competitor_prices",
    "ALTER TABLE efn_dwh.competitor_prices DELETE WHERE 1 = 1",
    "INSERT INTO efn_dwh.v_competitor_prices SELECT * FROM efn_dwh.v_competitor_prices",
    "DELETE FROM efn_dwh.competitor_prices WHERE 1 = 1",
    "CREATE TABLE x (a Int32) ENGINE = Memory",
    "SYSTEM SHUTDOWN",
    "KILL QUERY WHERE 1 = 1",
    "SET readonly = 0",
    "GRANT ALL ON *.* TO efn_text2sql",
    "OPTIMIZE TABLE efn_dwh.competitor_prices FINAL",
    # multi statement (termasuk yang disembunyikan di balik komentar)
    "SELECT 1 FROM v_sales_daily; DROP TABLE v_sales_daily",
    "SELECT * FROM v_sales_daily; SELECT * FROM v_loyalty_daily",
    "SELECT * FROM v_sales_daily -- komentar\n; DROP TABLE v_sales_daily",
    "SELECT * FROM v_sales_daily /* x */; DELETE FROM efn_dwh.competitor_prices WHERE 1 = 1",
    # database sistem / tabel di luar whitelist
    "SELECT * FROM system.tables",
    "SELECT name FROM system.users",
    "SELECT * FROM `system`.`query_log`",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM efn_dwh.competitor_prices",
    "SELECT * FROM competitor_prices",
    "SELECT * FROM efn_dwh.loyalty_events",
    "SELECT * FROM otherdb.v_sales_daily",
    # union / join / subquery ke tabel terlarang
    "SELECT product_name FROM v_competitor_prices UNION ALL SELECT name FROM system.tables",
    "SELECT * FROM v_competitor_prices AS a JOIN efn_dwh.competitor_prices AS b ON a.report_id = b.report_id",
    "SELECT * FROM v_competitor_prices WHERE product_name IN (SELECT user_hash FROM efn_dwh.competitor_prices)",
    "SELECT * FROM (SELECT * FROM system.processes) AS p",
    "WITH x AS (SELECT * FROM system.users) SELECT * FROM x",
    "SELECT (SELECT max(member_hash) FROM efn_dwh.loyalty_events) AS m FROM v_loyalty_daily",
    # table function / fungsi berbahaya
    "SELECT * FROM url('http://evil.example/x.csv', CSV, 'a String')",
    "SELECT * FROM file('/etc/passwd', 'LineAsString')",
    "SELECT * FROM remote('127.0.0.1', system.users)",
    "SELECT * FROM remoteSecure('db:9440', efn_dwh.competitor_prices)",
    "SELECT * FROM s3('https://bucket.example/x.csv', 'CSV')",
    "SELECT * FROM numbers(1000000000000)",
    "SELECT * FROM mysql('h:3306', 'db', 't', 'u', 'p')",
    "SELECT * FROM v_sales_daily WHERE sleep(3) = 0",
    "SELECT dictGet('d', 'a', toUInt64(1)) FROM v_sales_daily",
    "SELECT version() FROM v_sales_daily",
    "SELECT currentUser() FROM v_sales_daily",
    # klausa yang harus dikendalikan guard
    "SELECT * FROM v_sales_daily SETTINGS max_execution_time = 100000",
    "SELECT * FROM v_sales_daily INTO OUTFILE '/var/lib/x.csv'",
    # tanpa tabel / kosong / sampah
    "SELECT 1",
    "",
    "   ",
    "this is not sql",
]


@pytest.mark.parametrize("sql", NEGATIVE)
def test_negative_cases_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        guard_sql(sql, CONFIG)


def test_too_long_sql_rejected() -> None:
    with pytest.raises(SqlGuardError) as info:
        guard_sql("SELECT * FROM v_sales_daily WHERE " + " OR ".join(["1 = 1"] * 3000), CONFIG)
    assert info.value.code == "too_long"


def test_error_codes_are_specific() -> None:
    cases = {
        "SELECT * FROM system.tables": "table_not_allowed",
        "SELECT 1 FROM v_sales_daily; SELECT 2 FROM v_sales_daily": "multiple_statements",
        "DROP TABLE v_sales_daily": "not_select",
        "SELECT * FROM v_sales_daily WHERE sleep(3) = 0": "function_not_allowed",
    }
    for sql, code in cases.items():
        with pytest.raises(SqlGuardError) as info:
            guard_sql(sql, CONFIG)
        assert info.value.code == code, sql


# ------------------------------------------------------------------ SEC-04: CTE per scope & IN <tabel>

SEC04_EXPLOITS = [
    # Payload persis dari laporan security: CTE di subquery IN menutupi tabel dasar di query luar.
    "SELECT company_id, customer_id, order_ref, price_unit FROM fact_sales_line "
    "WHERE company_id IN (WITH fact_sales_line AS (SELECT 1 AS company_id) SELECT company_id FROM fact_sales_line)",
    # Variasi tabel dasar lain.
    "SELECT user_hash FROM competitor_prices "
    "WHERE 1 IN (WITH competitor_prices AS (SELECT 1 AS x) SELECT x FROM competitor_prices)",
    "SELECT member_hash FROM loyalty_events "
    "WHERE 1 IN (WITH loyalty_events AS (SELECT * FROM v_loyalty_daily) SELECT 1 FROM loyalty_events)",
    # CTE di subquery FROM, referensi di query luar / JOIN luar.
    "SELECT * FROM (WITH competitor_prices AS (SELECT * FROM v_competitor_prices) SELECT * FROM competitor_prices) a "
    "JOIN competitor_prices b ON a.report_id = b.report_id",
    # CTE di scalar subquery, referensi di query luar (nama x di luar = tabel dasar efn_dwh.x).
    "SELECT (WITH x AS (SELECT * FROM v_sales_daily) SELECT count() FROM x) AS s FROM x",
    # CTE di subquery IN dengan nama tabel dasar yang tidak terdaftar sebagai protected.
    "SELECT * FROM secret_base WHERE 1 IN "
    "(WITH secret_base AS (SELECT 1 FROM v_sales_daily) SELECT 1 FROM secret_base)",
    # CTE di cabang kanan UNION tidak terlihat di cabang kiri.
    "SELECT user_hash FROM competitor_prices "
    "UNION ALL WITH competitor_prices AS (SELECT * FROM v_competitor_prices) SELECT * FROM competitor_prices",
    # Shadowing langsung: nama CTE = tabel dasar terlindung / view whitelist (ditolak walau scope benar).
    "WITH competitor_prices AS (SELECT * FROM v_competitor_prices) SELECT * FROM competitor_prices",
    "WITH v_sales_daily AS (SELECT * FROM v_competitor_prices) SELECT * FROM v_sales_daily",
    "WITH etl_watermark AS (SELECT * FROM v_sales_daily) SELECT * FROM etl_watermark",
    # Badan CTE merujuk namanya sendiri = tabel dasar (non-recursive).
    "WITH t AS (SELECT * FROM t) SELECT * FROM t, v_sales_daily",
    # CTE didefinisikan setelah dipakai oleh CTE sebelumnya.
    "WITH a AS (SELECT * FROM b), b AS (SELECT * FROM v_sales_daily) SELECT * FROM a",
    # Alias skalar ClickHouse (`WITH <expr> AS x`) bukan sumber tabel: FROM x = tabel dasar.
    "WITH 'x' AS competitor_prices SELECT * FROM competitor_prices",
    "WITH 1 AS secret_table SELECT * FROM secret_table, v_sales_daily",
    # Tanpa tabel whitelist sama sekali (hanya CTE konstanta).
    "WITH t AS (SELECT 1 AS a) SELECT a FROM t",
    # Vektor kedua: sisi kanan IN berupa nama tabel / kolom (tidak pernah jadi node Table).
    "SELECT * FROM v_competitor_prices WHERE report_id IN efn_dwh.competitor_prices",
    "SELECT * FROM v_competitor_prices WHERE report_id IN competitor_prices",
    "SELECT * FROM v_competitor_prices WHERE report_id GLOBAL IN competitor_prices",
    "SELECT * FROM v_competitor_prices WHERE report_id NOT IN system.users",
    "SELECT * FROM v_competitor_prices WHERE product_name IN (competitor_name)",
    # JOIN USING ke tabel dasar.
    "SELECT * FROM v_competitor_prices JOIN competitor_prices USING (report_id)",
    "SELECT * FROM v_competitor_prices AS a JOIN efn_dwh.loyalty_events USING (source_ref)",
]


@pytest.mark.parametrize("sql", SEC04_EXPLOITS)
def test_sec04_cte_scope_and_in_table_exploits_rejected(sql: str) -> None:
    with pytest.raises(SqlGuardError):
        guard_sql(sql, CONFIG)


def test_sec04_report_payload_error_code() -> None:
    with pytest.raises(SqlGuardError) as info:
        guard_sql(SEC04_EXPLOITS[0], CONFIG)
    # Ditolak oleh FROM luar (table_not_allowed) atau oleh nama CTE (cte_shadowing), tergantung urutan traversal.
    assert info.value.code in {"table_not_allowed", "cte_shadowing"}
    with pytest.raises(SqlGuardError) as info:
        guard_sql("WITH competitor_prices AS (SELECT * FROM v_competitor_prices) SELECT 1 FROM v_sales_daily", CONFIG)
    assert info.value.code == "cte_shadowing"


SCOPED_CTE_OK = [
    # CTE luar terlihat di subquery turunan (IN, FROM, scalar).
    "WITH t AS (SELECT report_id FROM v_competitor_prices) "
    "SELECT * FROM v_competitor_prices WHERE report_id IN (SELECT report_id FROM t)",
    "WITH t AS (SELECT * FROM v_sales_daily) SELECT * FROM (SELECT day FROM t) AS s",
    "WITH a AS (SELECT * FROM v_sales_daily), b AS (SELECT day FROM a) SELECT * FROM b",
    "SELECT * FROM v_sales_daily WHERE day IN (WITH t AS (SELECT day FROM v_loyalty_daily) SELECT day FROM t)",
    "SELECT * FROM v_sales_daily WHERE brand IN ('PB', 'GL', 'BCR', 'BCF') AND store_id NOT IN (1, 2)",
    "WITH toDate('2026-10-01') AS d0 SELECT * FROM v_sales_daily WHERE day >= d0",
    "SELECT a.day FROM v_sales_daily AS a JOIN v_loyalty_daily AS b USING (day)",
]


@pytest.mark.parametrize("sql", SCOPED_CTE_OK)
def test_scoped_cte_and_safe_in_allowed(sql: str) -> None:
    assert guard_sql(sql, CONFIG).tables


def test_protected_tables_loaded_from_semantic_layer() -> None:
    assert {"competitor_prices", "loyalty_events", "fact_sales_line", "etl_watermark"} <= CONFIG.protected_tables


def test_custom_whitelist() -> None:
    config = GuardConfig(allowed_tables=frozenset({"efn_dwh.only_this"}))
    guard_sql("SELECT * FROM only_this", config)
    with pytest.raises(SqlGuardError):
        guard_sql("SELECT * FROM v_sales_daily", config)


def test_g12_tables_protected_and_not_whitelisted() -> None:
    new_tables = {"fact_gl_line", "dim_gl_account", "dim_company", "audit_log_copy", "audit_log_line_copy"}
    assert new_tables | {"audit_recon_run"} <= CONFIG.protected_tables
    for table in sorted(new_tables):
        with pytest.raises(SqlGuardError):
            guard_sql(f"SELECT count() FROM {table}", CONFIG)
