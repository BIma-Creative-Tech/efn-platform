"""G44 (ADR-046, docs/04 §28.1): job mart forecast + akurasi mingguan (logika murni, tanpa DB)."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from efn_etl.config import Settings
from efn_etl.forecast import (
    ACCURACY_COLUMNS,
    CALENDAR_COLUMNS,
    DAILY_COLUMNS,
    FC_WAREHOUSE_COLUMNS,
    LEGACY_SALES_COLUMNS,
    RUN_COLUMNS,
    ClickHouseForecastStore,
    accuracy_due,
    accuracy_rows,
    accuracy_weeks,
    forecast_jobs,
    legacy_sales_jobs,
    run_forecast_accuracy,
    transform_calendar,
    transform_daily,
    transform_fc_warehouse,
    transform_legacy_sales,
    transform_run,
    week_start,
)
from efn_etl.jobs import enabled_jobs

ROOT = Path(__file__).resolve().parents[3]
DDL = (ROOT / "infra" / "clickhouse" / "init" / "15-g44-forecast.sql").read_text(encoding="utf-8")
ETL_ACCESS = (ROOT / "infra" / "clickhouse" / "access" / "etl-access.sql").read_text(encoding="utf-8")
WD = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)


def _table_columns(table: str) -> list[str]:
    body = DDL.split(f"CREATE TABLE IF NOT EXISTS efn_dwh.{table}", 1)[1].split("ENGINE", 1)[0]
    cols = re.findall(r"^\s{4}(\w+)\s", body, re.MULTILINE)
    return [c for c in cols if c != "_loaded_at"]


# ---------------------------------------------------------------- definisi job
def test_forecast_jobs_flag_off_by_default() -> None:
    fc = {"fc_warehouses", "fc_calendar", "fc_runs", "fc_daily"}
    assert not fc & {j.name for j in enabled_jobs(True)}
    assert fc <= {j.name for j in enabled_jobs(True, forecast=True)}
    # Tanpa Odoo tidak ada job forecast walau flag aktif (Settings juga menolak kombinasi ini).
    assert not fc & {j.name for j in enabled_jobs(False, forecast=True)}


def test_job_sql_contract_keyset_no_select_star_no_pii() -> None:
    for job in forecast_jobs():
        sql = job.sql
        assert "SELECT *" not in sql
        for needle in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s", "_cursor_ts", "_cursor_key", "ORDER BY"):
            assert needle in sql, (job.name, needle)
        for pii in ("partner", "create_uid", "write_uid", "user_id", "lot_id"):
            assert pii not in sql, (job.name, pii)
        assert job.identity_field is None


@pytest.mark.parametrize(
    ("table", "columns"),
    [
        ("dim_fc_warehouse", FC_WAREHOUSE_COLUMNS),
        ("dim_calendar", CALENDAR_COLUMNS),
        ("dim_forecast_run", RUN_COLUMNS),
        ("fact_forecast_daily", DAILY_COLUMNS),
        ("fact_forecast_accuracy", ACCURACY_COLUMNS),
    ],
)
def test_columns_match_ddl_and_etl_grant(table: str, columns: tuple[str, ...]) -> None:
    assert _table_columns(table) == list(columns)
    assert f"GRANT SELECT, INSERT ON efn_dwh.{table} TO etl_writer;" in ETL_ACCESS
    assert "ReplacingMergeTree" in DDL.split(f"efn_dwh.{table}", 1)[1].split(";", 1)[0]


# ---------------------------------------------------------------- transform
def test_transform_warehouse_normalizes_codes() -> None:
    row = {
        "warehouse_id": 7,
        "code": "SMD01",
        "name": "Outlet 1",
        "company_id": 9,
        "company_code": "smd",
        "active": True,
        "write_date": WD,
    }
    assert transform_fc_warehouse(row) == [7, "SMD01", "Outlet 1", 9, "SMD", 1, WD]
    # Kode PT lain yang belum dikenal (mis. G43) tidak membatalkan halaman.
    assert transform_fc_warehouse({**row, "company_code": "IMD"})[4] == "IMD"
    # Kode gudang tidak dinormalisasi (dikirim balik ke Odoo sebagai warehouse_code apa adanya).
    assert transform_fc_warehouse({**row, "code": "Smd02"})[1] == "Smd02"


def test_transform_calendar_and_fail_closed() -> None:
    row = {
        "calendar_id": 3,
        "company_id": 9,
        "warehouse_id": None,
        "date": date(2027, 3, 10),
        "kind": "holiday",
        "name": {"en_US": "Eid", "id_ID": "Idul Fitri"},
        "source": "lib",
        "active": True,
        "write_date": WD,
    }
    assert transform_calendar(row) == [3, 9, 0, date(2027, 3, 10), "holiday", "Idul Fitri", "lib", 1, WD]
    for bad in ({"kind": "promo"}, {"source": "api"}, {"kind": ""}):
        with pytest.raises(ValueError):
            transform_calendar({**row, **bad})


def test_transform_run_defaults_generated_at() -> None:
    row = {
        "run_id": 1,
        "run_uuid": "6f1c2b8e-0d3a-4c8e-9d61-1b5a2f4e7c90",
        "company_id": 9,
        "kind": "daily",
        "model_version": "g44-1.0.0",
        "state": "ok",
        "generated_at": None,
        "write_date": WD,
    }
    out = transform_run(row)
    assert out[6] == WD and out[5] == "ok"


def test_transform_daily_validates_contract() -> None:
    row = {
        "line_id": 1,
        "run_id": 2,
        "company_id": 9,
        "warehouse_id": 7,
        "product_id": 55,
        "forecast_date": date(2026, 11, 19),
        "yhat": 12.4,
        "p10": 8.0,
        "p90": 17.1,
        "sigma": 2.9,
        "model": "AutoETS",
        "write_date": WD,
    }
    out = transform_daily(row)
    assert out[5] == date(2026, 11, 19)
    assert out[6:10] == [Decimal("12.400"), Decimal("8.000"), Decimal("17.100"), Decimal("2.900")]
    for bad in ({"model": "Prophet"}, {"p10": 13.0}, {"p90": 1.0}, {"yhat": -1}, {"sigma": "nan"}):
        with pytest.raises(ValueError):
            transform_daily({**row, **bad})


# ---------------------------------------------------------------- akurasi
def test_week_start_and_completed_weeks() -> None:
    assert week_start(date(2026, 11, 18)) == date(2026, 11, 16)  # Rabu -> Senin
    assert week_start(date(2026, 11, 16)) == date(2026, 11, 16)
    # 18 Nov 2026 03:00 UTC = 10:00 WIB (Rabu) -> 2 minggu selesai sebelum Senin 16 Nov
    assert accuracy_weeks(WD, 2) == (date(2026, 11, 2), date(2026, 11, 16))
    # Minggu 15 Nov 2026 23:30 WIB masih minggu sebelumnya
    late_sunday = datetime(2026, 11, 15, 16, 30, tzinfo=UTC)
    assert accuracy_weeks(late_sunday, 1) == (date(2026, 11, 2), date(2026, 11, 9))
    with pytest.raises(ValueError):
        accuracy_weeks(WD, 0)


def test_accuracy_due_once_per_business_day() -> None:
    day = timedelta(days=1)
    assert accuracy_due(None, WD, day) is True
    assert accuracy_due(WD - timedelta(hours=1), WD, day) is False
    # tanggal WIB berganti (00:30 WIB) -> jatuh tempo walau < interval
    assert accuracy_due(datetime(2026, 11, 17, 16, 0, tzinfo=UTC), datetime(2026, 11, 17, 17, 30, tzinfo=UTC), day)


def test_accuracy_rows_wape_mape_bias() -> None:
    mon = date(2026, 11, 2)
    forecasts = [
        (9, 7, 55, mon, 10.0),
        (9, 7, 55, mon + timedelta(days=1), 10.0),
        (9, 7, 55, mon + timedelta(days=2), 4.0),  # aktual 0 -> masuk WAPE, tidak masuk MAPE
        (9, 7, 55, mon, 99.0),  # duplikat hari yang sama diabaikan
        (9, 7, 56, mon + timedelta(days=7), 5.0),  # minggu berikutnya, tanpa penjualan
    ]
    actuals = {(7, 55, mon): 8.0, (7, 55, mon + timedelta(days=1)): 12.0, (7, 99, mon): 100.0}
    rows = accuracy_rows(forecasts, actuals, WD)
    assert len(rows) == 2
    first = dict(zip(ACCURACY_COLUMNS, rows[0], strict=True))
    assert (first["company_id"], first["warehouse_id"], first["product_id"], first["week_start"]) == (9, 7, 55, mon)
    assert first["days"] == 3 and first["days_with_sales"] == 2
    assert first["forecast_qty"] == Decimal("24.000") and first["actual_qty"] == Decimal("20.000")
    assert first["abs_error"] == Decimal("8.000")  # 2 + 2 + 4
    assert first["wape"] == pytest.approx(0.4)
    assert first["bias"] == pytest.approx(0.2)
    assert first["mape"] == pytest.approx((2 / 8 + 2 / 12) / 2, abs=1e-6)
    second = dict(zip(ACCURACY_COLUMNS, rows[1], strict=True))
    # Tanpa penjualan: rasio tidak terdefinisi -> NULL (bukan 0 atau tak hingga).
    assert second["wape"] is None and second["mape"] is None and second["bias"] is None
    assert second["abs_error"] == Decimal("5.000")


def test_accuracy_rows_clamps_negative_values() -> None:
    rows = accuracy_rows([(1, 2, 3, date(2026, 11, 2), -5.0)], {(2, 3, date(2026, 11, 2)): -1.0}, WD)
    assert rows[0][6] == Decimal("0.000") and rows[0][7] == Decimal("0.000")


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.result_rows = rows


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, sql: str, parameters: dict[str, Any] | None = None) -> _Result:
        self.calls.append((sql, parameters))
        if "fact_forecast_accuracy" in sql:
            return _Result([(None, 0)])
        if "fact_forecast_daily" in sql:
            return _Result([(9, 7, 55, date(2026, 11, 3), 10.0)])
        return _Result([(7, 55, date(2026, 11, 3), 6.0), (7, 55, date(2026, 11, 3), 2.0)])


class _Sink:
    def __init__(self) -> None:
        self.inserted: list[tuple[str, tuple[str, ...], list[list[Any]]]] = []

    def insert(self, table: str, columns: Any, rows: list[list[Any]]) -> None:
        self.inserted.append((table, tuple(columns), rows))


def test_run_forecast_accuracy_parameterized_queries() -> None:
    client = _FakeClient()
    store = ClickHouseForecastStore(client)
    assert store.last_accuracy_at() is None
    sink = _Sink()
    result = run_forecast_accuracy(store, sink, WD, 2)
    assert result.rows == 1 and (result.start, result.end) == (date(2026, 11, 2), date(2026, 11, 16))
    table, columns, rows = sink.inserted[0]
    assert table == "fact_forecast_accuracy" and columns == ACCURACY_COLUMNS
    assert rows[0][7] == Decimal("8.000")  # dua baris aktual hari yang sama dijumlah
    for sql, params in client.calls[1:]:
        assert "{start:Date}" in sql and "{end:Date}" in sql
        assert params == {"start": date(2026, 11, 2), "end": date(2026, 11, 16)}
    forecast_sql = client.calls[1][0]
    assert "state = 'ok'" in forecast_sql
    assert "toDate(r.generated_at, 'Asia/Jakarta') < f.forecast_date" in forecast_sql


# ---------------------------------------------------------------- config & DDL
@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    return {"APP_ENV": "production", "PG_SSLROOTCERT": str(ca), "CH_CA_FILE": str(ca), "AMQP_TLS_CA_FILE": str(ca)}


def test_forecast_flag_default_off_and_fail_closed(tls_env: dict[str, str]) -> None:
    settings = Settings.from_env(tls_env)
    assert settings.forecast_enabled is False
    assert settings.forecast_accuracy_weeks == 2
    on = Settings.from_env({**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_ENABLE_FORECAST": "true"})
    assert on.forecast_enabled is True
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "ETL_ENABLE_FORECAST": "true"})  # tanpa Odoo
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "ETL_FORECAST_ACCURACY_WEEKS": "0"})


def test_ddl_idempotent_and_without_pii() -> None:
    code = "\n".join(line.split("--", 1)[0] for line in DDL.splitlines())
    assert not re.search(r"\b(DROP|TRUNCATE|INSERT|ALTER)\b", code)
    assert "CREATE TABLE efn" not in code and "CREATE VIEW efn" not in code
    for pii in ("customer_id", "member_hash", "user_hash", "partner", "lot_name"):
        assert pii not in code, pii
    assert "CREATE DATABASE IF NOT EXISTS efn_forecast;" in code


# ---------------------------------------------------------------- G46: histori penjualan ESB pra-cutover (§28.2a)
def test_legacy_sales_job_only_with_forecast_flag() -> None:
    assert "fc_legacy_sales" not in {j.name for j in enabled_jobs(True, forecast=True)}
    assert "fc_legacy_sales" in {j.name for j in enabled_jobs(True, forecast=True, forecast_legacy=True)}
    assert "fc_legacy_sales" not in {j.name for j in enabled_jobs(True, forecast_legacy=True)}
    assert "fc_legacy_sales" not in {j.name for j in enabled_jobs(False, forecast=True, forecast_legacy=True)}


def test_legacy_sales_sql_minimum_columns_and_grant() -> None:
    (job,) = legacy_sales_jobs()
    sql = job.sql
    assert "SELECT *" not in sql and job.identity_field is None
    for needle in ("%(cursor_ts)s", "%(cursor_key)s", "%(limit)s", "_cursor_ts", "_cursor_key", "ORDER BY"):
        assert needle in sql, needle
    # Tanpa nama menu/cabang, rupiah, run_id, pelaku (minimisasi; forecast hanya butuh qty).
    for banned in ("menu_name", "branch", "gross", "net", "cogs", "discount", "run_id", "create_uid", "write_uid"):
        assert banned not in sql, banned
    read = set(re.findall(r"\bl\.([a-z_]+)", sql))
    grants = (ROOT / "services" / "etl" / "sql" / "odoo-readonly-grants.sql").read_text(encoding="utf-8")
    block = grants.split("Gelombang 46 (efn-lead)", 1)[1]
    assert r"\if :efn_g46_legacy_sales" in block
    granted = re.search(r"GRANT SELECT \(([^)]*)\)\s+ON public\.efn_legacy_sales_daily TO efn_etl;", block)
    assert granted is not None
    assert read <= {c.strip() for c in granted.group(1).split(",")}


def test_legacy_sales_columns_match_ddl_and_grants() -> None:
    assert _table_columns("fact_legacy_sales_daily") == list(LEGACY_SALES_COLUMNS)
    assert "GRANT SELECT, INSERT ON efn_dwh.fact_legacy_sales_daily TO etl_writer;" in ETL_ACCESS
    fc_access = (ROOT / "infra" / "clickhouse" / "access" / "forecast-access.sql").read_text(encoding="utf-8")
    assert "GRANT SELECT ON efn_dwh.fact_legacy_sales_daily TO efn_forecast_definer;" in fc_access
    view = DDL.split("CREATE OR REPLACE VIEW efn_dwh.v_fc_sales_daily AS", 1)[1].split(";", 1)[0]
    assert "efn_dwh.fact_legacy_sales_daily FINAL" in view
    assert "product_id != 0 AND qty > 0" in view
    assert "NOT IN" in view  # hari x gudang dengan POS tidak memakai histori ESB (anti dobel hitung)


def test_transform_legacy_sales() -> None:
    row = {
        "company_id": 3,
        "warehouse_id": 11,
        "day": date(2026, 10, 1),
        "esb_menu_code": " MN-001 ",
        "product_id": None,
        "qty": Decimal("12.5"),
        "write_date": WD,
    }
    out = transform_legacy_sales(row)
    assert out == [3, 11, date(2026, 10, 1), "MN-001", 0, Decimal("12.500"), WD]
    assert transform_legacy_sales({**row, "product_id": 77, "qty": Decimal("-2")})[4:6] == [77, Decimal("-2.000")]
    for bad in ({"esb_menu_code": ""}, {"esb_menu_code": "X" * 65}, {"qty": "NaN"}):
        with pytest.raises(ValueError):
            transform_legacy_sales({**row, **bad})


def test_legacy_flag_requires_forecast(tls_env: dict[str, str]) -> None:
    on = {**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_ENABLE_FORECAST": "true"}
    assert Settings.from_env(on).forecast_legacy_enabled is False
    assert Settings.from_env({**on, "ETL_ENABLE_FORECAST_LEGACY": "true"}).forecast_legacy_enabled is True
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_ENABLE_FORECAST_LEGACY": "true"})
