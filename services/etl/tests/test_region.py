"""G21 (ADR-036 §0.7, docs/04 §13.2): wilayah kab/kota pembeli di mart penjualan + dim_region, tanpa PII."""

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from efn_etl.config import Settings
from efn_etl.jobs import SALES_FACT, SALES_FACT_COLUMNS, enabled_jobs, sales_fact_sql, transform_sales_line
from efn_etl.region import (
    REGION_COLUMNS,
    SQL_REGIONS,
    region_code,
    regions_job,
    strict_region_code,
    transform_region,
)

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
GRANTS = ROOT / "sql" / "odoo-readonly-grants.sql"
CH_G21 = REPO / "infra" / "clickhouse" / "init" / "09-g21-region.sql"
TS = datetime(2026, 10, 19, 3, 0)
# Kolom res_partner yang terenkripsi/terlindungi ADR-036 (+ kontak/identitas): tidak boleh disentuh ETL maupun view.
PARTNER_PII = (
    "p.name",
    "p.display_name",
    "p.complete_name",
    "street",
    "city",
    "zip",
    "email",
    "phone",
    "mobile",
    "vat",
    "efn_name_tokens",
    "l10n_id_nik",
    "bidx",
)


# ---------------------------------------------------------------- logika murni
@pytest.mark.parametrize(("value", "expected"), [("31.71", "31.71"), (" 51.03 ", "51.03"), ("00.00", "00.00")])
def test_region_code_accepts_kab_kota(value: str, expected: str) -> None:
    assert region_code(value) == expected
    assert strict_region_code(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "31",  # provinsi saja
        "31.71.01",  # kecamatan: granularitas lebih halus ditolak (Q-PII10)
        "3171",
        "31.7a",
        "Jl. Sudirman 1",  # teks bebas (alamat) tidak pernah disalin
        "081234567890",
        "31.71x",
        "31.71\n32.01",
    ],
)
def test_region_code_rejects_non_codes(value: object) -> None:
    assert region_code(value) == ""
    with pytest.raises(ValueError):
        strict_region_code(value)


def test_region_code_strips_trailing_newline_only_as_whitespace() -> None:
    # str.strip() membuang newline -> kode valid; tidak ada injeksi baris karena hasil hanya PP.KK.
    assert region_code("31.71\n") == "31.71"


def test_transform_region() -> None:
    row = {
        "region_code": "31.71",
        "name": "Kota Jakarta Selatan",
        "state_code": "JK",
        "state_name": "DKI Jakarta",
        "active": True,
        "write_date": TS,
    }
    out = dict(zip(REGION_COLUMNS, transform_region(row), strict=True))
    assert out["region_code"] == "31.71" and out["name"] == "Kota Jakarta Selatan"
    assert out["state_code"] == "JK" and out["active"] == 1
    assert out["write_date"] == datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


def test_transform_region_negative() -> None:
    base = {
        "region_code": "31.71",
        "name": "x",
        "state_code": "JK",
        "state_name": "",
        "active": None,
        "write_date": TS,
    }
    with pytest.raises(ValueError):
        transform_region({**base, "region_code": "Jakarta"})
    out = dict(
        zip(REGION_COLUMNS, transform_region({**base, "state_code": "J K;DROP", "name": "n" * 500}), strict=True)
    )
    assert out["state_code"] == "" and len(out["name"]) == 128 and out["active"] == 0


# ---------------------------------------------------------------- SQL & job
def test_regions_job_sql_contract() -> None:
    job = regions_job()
    assert (job.name, job.source, job.target_table, job.columns) == ("regions", "odoo", "dim_region", REGION_COLUMNS)
    assert job.identity_field is None
    sql = SQL_REGIONS
    assert "SELECT *" not in sql.upper()
    for param in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s"):
        assert param in sql
    assert "FROM efn_etl.region r" in sql  # berkualifikasi (search_path=public)
    assert "res_partner" not in sql


def test_sales_sql_without_region_does_not_touch_efn_etl_schema() -> None:
    sql = sales_fact_sql(False)
    assert sql == SALES_FACT.sql
    assert "efn_etl." not in sql
    assert "''::text AS customer_region_code" in sql
    assert "{" not in sql


def test_sales_sql_with_region_joins_view_only() -> None:
    sql = sales_fact_sql(True)
    assert "LEFT JOIN efn_etl.partner_region pr ON pr.partner_id = o.partner_id" in sql
    assert "pr.region_code AS customer_region_code" in sql
    assert "res_partner" not in sql and "efn_region " not in sql
    lowered = sql.lower()
    for column in PARTNER_PII + ("o.email", "o.mobile", "general_customer_note"):
        assert column not in lowered, column
    assert "{" not in sql and "SELECT *" not in sql.upper()
    # LEFT JOIN: order tanpa member / member tanpa wilayah tetap dimuat.
    assert "JOIN efn_etl.partner_region" in sql and "LEFT JOIN efn_etl.partner_region" in sql


def test_transform_sales_line_region_column() -> None:
    row = {
        "line_id": 5,
        "company_id": 2,
        "company_name": "PT GL",
        "brand": "GL",
        "store_id": 3,
        "order_ref": "Order 1",
        "order_date": datetime(2026, 10, 18, 3, 0),
        "customer_id": 77,
        "product_id": 9,
        "product_name": "Susu",
        "category": "Minuman",
        "qty": Decimal("1"),
        "price_unit": Decimal("18000"),
        "discount_pct": None,
        "subtotal": Decimal("18000"),
        "tax_amount": Decimal("0"),
        "cost_total": None,
        "write_date": datetime(2026, 10, 18, 3, 1),
    }
    now = datetime(2026, 10, 19, tzinfo=UTC)
    assert SALES_FACT_COLUMNS[-1] == "customer_region_code"
    without = dict(zip(SALES_FACT_COLUMNS, transform_sales_line(row, now=now), strict=True))
    assert without["customer_region_code"] == ""
    with_region = dict(
        zip(SALES_FACT_COLUMNS, transform_sales_line({**row, "customer_region_code": "51.71"}, now=now), strict=True)
    )
    assert with_region["customer_region_code"] == "51.71"
    bad = dict(
        zip(SALES_FACT_COLUMNS, transform_sales_line({**row, "customer_region_code": "Denpasar"}, now=now), strict=True)
    )
    assert bad["customer_region_code"] == ""  # lenient di fakta: baris tetap dimuat, teks bebas tidak disalin


def test_region_kept_when_customer_id_aged_out() -> None:
    row = {
        "line_id": 6,
        "company_id": 2,
        "company_name": "PT GL",
        "brand": "GL",
        "store_id": 3,
        "order_ref": "Order 2",
        "order_date": datetime(2020, 1, 1, 3, 0),
        "customer_id": 77,
        "product_id": 9,
        "product_name": "Susu",
        "category": "Minuman",
        "qty": Decimal("1"),
        "price_unit": Decimal("1"),
        "discount_pct": None,
        "subtotal": Decimal("1"),
        "tax_amount": Decimal("0"),
        "cost_total": None,
        "write_date": datetime(2026, 10, 18, 3, 1),
        "customer_region_code": "31.71",
    }
    out = dict(zip(SALES_FACT_COLUMNS, transform_sales_line(row, now=datetime(2026, 10, 19, tzinfo=UTC)), strict=True))
    assert out["customer_id"] is None and out["customer_region_code"] == "31.71"


def test_enabled_jobs_partner_region_flag() -> None:
    default = [j.name for j in enabled_jobs(True)]
    assert "regions" not in default
    jobs = enabled_jobs(True, partner_region=True)
    names = [j.name for j in jobs]
    assert names[-1] == "regions" and names[:-1] == default
    sales = next(j for j in jobs if j.name == "sales_fact_pos")
    assert "efn_etl.partner_region" in sales.sql
    assert "efn_etl.partner_region" not in next(j for j in enabled_jobs(True) if j.name == "sales_fact_pos").sql
    # Tanpa Odoo tidak ada job wilayah.
    assert [j.name for j in enabled_jobs(False, partner_region=True)] == ["competitor_prices", "loyalty_events"]


# ---------------------------------------------------------------- konfigurasi
@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    return {"APP_ENV": "production", "PG_SSLROOTCERT": str(ca), "CH_CA_FILE": str(ca), "AMQP_TLS_CA_FILE": str(ca)}


def test_partner_region_config(tls_env: dict[str, str]) -> None:
    assert Settings.from_env(tls_env).partner_region_enabled is False
    on = Settings.from_env({**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_PARTNER_REGION_ENABLED": "true"})
    assert on.partner_region_enabled is True
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "ETL_PARTNER_REGION_ENABLED": "true"})


# ---------------------------------------------------------------- grant Odoo & skema ClickHouse
def _g21_block() -> str:
    text = GRANTS.read_text(encoding="utf-8")
    match = re.search(r"DO \$grant_g21\$(.*?)\$grant_g21\$;", text, re.DOTALL)
    assert match, "blok G21 tidak ada di odoo-readonly-grants.sql"
    return match.group(1)


def test_grant_g21_view_reads_only_non_pii_columns() -> None:
    block = _g21_block()
    view = re.search(r"CREATE OR REPLACE VIEW efn_etl\.partner_region AS(.*?)\$v\$", block, re.DOTALL)
    assert view
    body = " ".join(view.group(1).split())
    assert body == (
        "SELECT p.id AS partner_id, p.company_id, r.code::text AS region_code FROM public.res_partner p "
        "JOIN public.efn_region r ON r.id = p.efn_region_id"
    )
    lowered = block.lower()
    for column in PARTNER_PII:
        assert column not in lowered, column
    assert "security_invoker" not in lowered
    assert "GRANT SELECT ON efn_etl.partner_region TO efn_etl" in block
    assert "GRANT SELECT ON efn_etl.region TO efn_etl" in block
    assert "REVOKE ALL ON efn_etl.partner_region FROM PUBLIC" in block
    # Tidak ada grant tabel res_partner / efn_region ke efn_etl di mana pun (K-36-5).
    text = GRANTS.read_text(encoding="utf-8")
    assert not re.search(r"ON public\.res_partner\b[^;]*TO efn_etl", text)
    assert not re.search(r"ON public\.efn_region\b[^;]*TO efn_etl", text)


def test_grant_g21_is_conditional_on_efn_region() -> None:
    block = _g21_block()
    assert "column_name = 'efn_region_id'" in block and "RETURN;" in block


def test_clickhouse_g21_schema_matches_jobs() -> None:
    ddl = CH_G21.read_text(encoding="utf-8")
    assert "ALTER TABLE efn_dwh.fact_sales_line ADD COLUMN IF NOT EXISTS customer_region_code " in ddl
    table = re.search(r"CREATE TABLE IF NOT EXISTS efn_dwh\.dim_region\s*\((.*?)\)\s*ENGINE", ddl, re.DOTALL)
    assert table
    for col in REGION_COLUMNS:
        assert re.search(rf"^\s+{col}\s", table.group(1), re.MULTILINE), col
    assert "ReplacingMergeTree(write_date)" in ddl
    view = ddl.split("CREATE OR REPLACE VIEW efn_dwh.v_pos_sales_region_daily", 1)[1]
    assert "customer_id" not in view  # view INVOKER: tidak butuh grant identitas pseudonim
    etl = (REPO / "infra" / "clickhouse" / "access" / "etl-access.sql").read_text(encoding="utf-8")
    assert "GRANT SELECT, INSERT ON efn_dwh.dim_region TO etl_writer;" in etl
    mb = (REPO / "infra" / "clickhouse" / "access" / "metabase-access.sql").read_text(encoding="utf-8")
    assert "GRANT SELECT(customer_region_code) ON efn_dwh.fact_sales_line TO metabase_reader;" in mb
    assert "GRANT SELECT ON efn_dwh.v_pos_sales_region_daily TO metabase_reader;" in mb
    assert not re.search(r"GRANT SELECT\([^)]*customer_id", mb)
