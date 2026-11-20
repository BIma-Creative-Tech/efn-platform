"""Query worker hanya membaca view `efn_segment.*` yang didefinisikan + di-grant per view (SEC-04, SEC-215)."""

import re
from pathlib import Path

from efn_segment.source import (
    SQL_MARKET,
    SQL_PRODUCTS,
    SQL_SUBJECT_PRODUCTS,
    SQL_SUBJECT_SEARCHES,
    VIEW_COLUMNS,
)

ROOT = Path(__file__).resolve().parents[3]
ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "segment-access.sql").read_text(encoding="utf-8")
DDL = (ROOT / "infra" / "clickhouse" / "init" / "16-g50-behavior.sql").read_text(encoding="utf-8")
ALL_SQL = (SQL_SUBJECT_PRODUCTS, SQL_SUBJECT_SEARCHES, SQL_PRODUCTS, SQL_MARKET)


def _view_columns(view: str) -> list[str]:
    body = ACCESS.split(f"CREATE OR REPLACE VIEW efn_segment.{view} ", 1)[1].split(";", 1)[0]
    select = body.split("SELECT", 1)[1].split("FROM", 1)[0]
    return [c.strip() for c in select.replace("\n", " ").split(",")]


def test_view_columns_match_source() -> None:
    for view, columns in VIEW_COLUMNS.items():
        assert _view_columns(view) == list(columns), view
        assert f"GRANT SELECT ON efn_segment.{view} TO segment_reader;" in ACCESS
        assert f"efn_dwh.v_seg_{view.removeprefix('v_')}" in ACCESS
        assert f"CREATE OR REPLACE VIEW efn_dwh.v_seg_{view.removeprefix('v_')} AS" in DDL


def test_sql_only_reads_segment_views() -> None:
    for sql in ALL_SQL:
        assert "SELECT *" not in sql.upper()
        tables = set(re.findall(r"FROM\s+([\w.]+)", sql))
        assert tables and all(t.startswith("efn_segment.v_") for t in tables), tables
        for t in tables:
            assert t.removeprefix("efn_segment.") in VIEW_COLUMNS
        assert "{code:String}" in sql  # selalu difilter PT


def test_event_queries_filter_dates_and_kind() -> None:
    for sql in (SQL_SUBJECT_PRODUCTS, SQL_SUBJECT_SEARCHES):
        assert "{start:Date}" in sql and "{end:Date}" in sql and "{kinds:Array(String)}" in sql


def test_role_is_readonly_and_no_wildcard_grant() -> None:
    assert "readonly = 2 CONST" in ACCESS
    assert "allow_ddl = 0 CONST" in ACCESS
    assert not re.search(r"GRANT\s+SELECT\s+ON\s+efn_segment\.\*\s+TO", ACCESS)
    assert not re.search(r"GRANT\s+(INSERT|ALTER|ALL)", ACCESS)
    assert "REVOKE ALL ON efn_dwh.* FROM segment_reader;" in ACCESS
    assert "NOT IDENTIFIED HOST NONE" in ACCESS


def test_views_have_no_raw_identity_or_query_text() -> None:
    for view in ("v_seg_subject_product_daily", "v_seg_subject_search_daily", "v_seg_product", "v_seg_market_price"):
        body = DDL.split(f"CREATE OR REPLACE VIEW efn_dwh.{view} AS", 1)[1].split(";", 1)[0]
        for forbidden in ("user_hash", "geo_lat", "e.query", "event_id", "d.name", "p.name"):
            assert forbidden not in body, (view, forbidden)
