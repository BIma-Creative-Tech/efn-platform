"""Konfigurasi (antarmuka env compose G44, fail-closed TLS/secret) + jadwal cron + mode idle."""

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from efn_forecast.__main__ import _kinds_for, serve
from efn_forecast.config import ClickHouseSettings, OdooSettings, Params, company_code, forecast_enabled
from efn_forecast.schedule import WIB, next_fire, parse_cron, zone
from efn_forecast.tls import TlsConfigError


@pytest.fixture
def files(tmp_path: Path) -> dict[str, str]:
    out = {}
    for name, content in (
        ("ca", "-----BEGIN CERTIFICATE-----\nx\n"),
        ("crt", "cert"),
        ("key", "key"),
        ("api", "abc123-real-key\n"),
        ("chpw", "Zm9yZWNhc3RwYXNzd29yZA=="),
    ):
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        out[name] = str(p)
    return out


def _odoo_env(files: dict[str, str], **kw: str) -> dict[str, str]:
    env = {
        "APP_ENV": "production",
        "ODOO_RPC_URL": "https://odoo-rpc-gateway:8443",
        "ODOO_RPC_CA_FILE": files["ca"],
        "ODOO_RPC_CLIENT_CERT_FILE": files["crt"],
        "ODOO_RPC_CLIENT_KEY_FILE": files["key"],
        "ODOO_RPC_TIMEOUT_SECONDS": "25",
        "ODOO_API_KEY_FORECAST_SMD_FILE": files["api"],
    }
    env.update(kw)
    return env


def test_params_defaults_match_contract() -> None:
    p = Params.from_env({})
    assert p.pipeline.horizon_days == 28 and p.chunk_size == 500
    assert p.calendar.payday_tail_days == p.calendar.payday_head_days == 7 and p.calendar.weekend_days == (5, 6)
    assert p.daily_cron == "30 23 * * *" and p.tz == "Asia/Jakarta" and p.dc_codes == ("SMDDC",)
    assert p.production is True and p.pipeline.candidates == ("AutoETS",)
    assert p.ets_model == "ZNZ" and Params.from_env({"FORECAST_ETS_MODEL": "zzz"}).ets_model == "ZZZ"


@pytest.mark.parametrize(
    "env",
    [
        {"FORECAST_CHUNK_SIZE": "501"},
        {"FORECAST_CHUNK_SIZE": "0"},
        {"FORECAST_HORIZON_DAYS": "abc"},
        {"FORECAST_MODELS": "Prophet"},
        {"FORECAST_ETS_MODEL": "ZMZ"},  # tren multiplikatif tidak diizinkan
        {"FORECAST_ETS_MODEL": "ZZ"},
        {"FORECAST_WEEKEND_DAYS": "7"},
        {"FORECAST_DAILY_CRON": "30 23 * *"},
        {"FORECAST_DAILY_CRON": "61 23 * * *"},
        {"FORECAST_SERVICE_LEVEL": "0.9", "FORECAST_SERVICE_LEVEL_MIN": "0.95"},
        {"FORECAST_LEBARAN_DATES": "2027-13-01"},
        {"FORECAST_SERVICE_LEVEL": "0.49"},  # Odoo hanya menerima [0,5; 1)
        {"FORECAST_SERVICE_LEVEL": "1"},
        {"FORECAST_SERVICE_LEVEL_MIN": "0.3"},
        {"FORECAST_MAX_QTY_PER_LINE": "0"},
        {"FORECAST_MAX_QTY_PER_LINE": "abc"},
    ],
)
def test_params_fail_closed(env: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Params.from_env(env)


def test_value_cap_and_warehouse_codes_exact() -> None:
    assert Params.from_env({}).max_qty_per_line == 100_000
    env = {"FORECAST_MAX_QTY_PER_LINE": "2500", "FORECAST_DC_CODES": "SmdDc", "FORECAST_WAREHOUSE_CODES": "Smd01"}
    p = Params.from_env(env)
    assert p.max_qty_per_line == 2500 and p.dc_codes == ("SmdDc",) and p.warehouse_codes == ("Smd01",)


def test_params_overrides() -> None:
    env = {
        "FORECAST_PAYDAY_TAIL_DAYS": "5",
        "FORECAST_WEEKEND_DAYS": "4,5",
        "FORECAST_CHUNK_SIZE": "200",
        "FORECAST_LEBARAN_DATES": "2027-03-10",
        "FORECAST_HORIZON_DAYS": "14",
    }
    p = Params.from_env(env)
    assert p.calendar.payday_tail_days == 5 and p.calendar.weekend_days == (4, 5) and p.chunk_size == 200
    assert p.calendar.lebaran_overrides[0].isoformat() == "2027-03-10" and p.pipeline.horizon_days == 14


def test_odoo_settings_mtls_and_secret(files: dict[str, str]) -> None:
    s = OdooSettings.from_env("smd", _odoo_env(files))
    assert s.company == "SMD" and s.api_key == "abc123-real-key" and s.timeout_seconds == 25
    assert s.client_cert_file == files["crt"] and s.ca_file == files["ca"]
    assert "abc123" not in repr(s)


def test_odoo_settings_fail_closed(files: dict[str, str], tmp_path: Path) -> None:
    disabled = tmp_path / "disabled"
    disabled.write_text("disabled\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        OdooSettings.from_env("SMD", _odoo_env(files, ODOO_API_KEY_FORECAST_SMD_FILE=str(disabled)))
    with pytest.raises(RuntimeError):
        OdooSettings.from_env("SMD", _odoo_env(files, ODOO_API_KEY_FORECAST_SMD_FILE=str(tmp_path / "missing")))
    with pytest.raises(TlsConfigError):
        OdooSettings.from_env("SMD", _odoo_env(files, ODOO_RPC_URL="http://odoo-rpc-gateway:8443"))
    with pytest.raises(TlsConfigError):
        OdooSettings.from_env("SMD", _odoo_env(files, ODOO_RPC_CLIENT_KEY_FILE=str(tmp_path / "nokey")))
    with pytest.raises(RuntimeError):
        OdooSettings.from_env("SMD", _odoo_env(files, ODOO_RPC_URL="https://u:p@odoo-rpc-gateway:8443"))
    with pytest.raises(RuntimeError):
        company_code("S M D")


def test_clickhouse_settings(files: dict[str, str]) -> None:
    env = {
        "APP_ENV": "production",
        "CH_CA_FILE": files["ca"],
        "CH_PASSWORD_FILE": files["chpw"],
        "CH_DATABASE": "efn_forecast",
        "CH_USER": "efn_forecast_reader",
    }
    s = ClickHouseSettings.from_env(env)
    assert (s.user, s.database, s.port, s.secure) == ("efn_forecast_reader", "efn_forecast", 8443, True)
    assert "Zm9y" not in repr(s)
    with pytest.raises(TlsConfigError):
        ClickHouseSettings.from_env({**env, "CH_SECURE": "false"})
    with pytest.raises(TlsConfigError):  # plaintext ditolak di production walau diminta
        ClickHouseSettings.from_env({**env, "CH_SECURE": "false", "EFN_ALLOW_PLAINTEXT": "true"})
    with pytest.raises(RuntimeError):
        ClickHouseSettings.from_env({k: v for k, v in env.items() if k != "CH_PASSWORD_FILE"})


def test_cron_next_fire_wib() -> None:
    cron = parse_cron("30 23 * * *")
    tz = zone("Asia/Jakarta")
    morning = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 10:00 WIB
    fire = datetime(2026, 11, 18, 16, 30, tzinfo=UTC)  # 23:30 WIB
    assert next_fire(morning, cron, tz) == fire
    assert next_fire(fire, cron, tz) == datetime(2026, 11, 19, 16, 30, tzinfo=UTC)
    sunday = parse_cron("0 1 * * 0")  # Minggu 01:00 WIB
    assert next_fire(morning, sunday, WIB) == datetime(2026, 11, 21, 18, 0, tzinfo=UTC)
    steps = parse_cron("*/15 8-9 1,15 * *")
    assert 45 in steps.minutes and steps.hours == {8, 9} and steps.days == {1, 15}


def test_minmax_runs_after_daily_on_configured_weekday() -> None:
    sunday = datetime(2026, 11, 22, 16, 30, tzinfo=UTC)  # Minggu 23:30 WIB
    assert _kinds_for(sunday, WIB, 6) == ["daily", "weekly_minmax"]
    assert _kinds_for(sunday, WIB, 0) == ["daily"]
    assert _kinds_for(sunday, WIB, -1) == ["daily"]


def test_disabled_worker_idles_without_secrets() -> None:
    assert forecast_enabled({}) is False and forecast_enabled({"FORECAST_ENABLED": "true"}) is True
    stop = threading.Event()
    stop.set()
    assert serve("SMD", {"FORECAST_ENABLED": "false"}, stop) == 0  # tidak butuh secret/ClickHouse/Odoo
