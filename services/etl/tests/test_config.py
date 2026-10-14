from pathlib import Path

import pytest

from efn_etl.adapters import clickhouse_client_kwargs
from efn_etl.config import Settings
from efn_etl.tls import TlsConfigError, is_production, plaintext_allowed


@pytest.fixture
def tls_env(tmp_path: Path) -> dict[str, str]:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\ndummy\n-----END CERTIFICATE-----\n", encoding="utf-8")
    return {
        "APP_ENV": "production",
        "PG_SSLROOTCERT": str(ca),
        "CH_CA_FILE": str(ca),
        "AMQP_TLS_CA_FILE": str(ca),
    }


def test_defaults_are_tls_and_least_privilege(tls_env: dict[str, str]) -> None:
    settings = Settings.from_env(tls_env)
    assert settings.platform_pg.sslmode == "verify-full"
    assert settings.platform_pg.sslrootcert == tls_env["PG_SSLROOTCERT"]
    assert settings.platform_pg.user == "efn_etl"
    # Odoo ikut TLS PG_* bila ODOO_PG_SSLMODE tidak di-set.
    assert (settings.odoo_pg.sslmode, settings.odoo_pg.sslrootcert) == ("verify-full", tls_env["PG_SSLROOTCERT"])
    assert settings.ch_secure is True
    assert settings.ch_port == 8443
    assert settings.ch_user == "efn_etl"
    assert settings.ch_ca_file == tls_env["CH_CA_FILE"]
    assert not hasattr(settings, "otp_pepper")


def test_clickhouse_client_verifies_with_internal_ca(tls_env: dict[str, str]) -> None:
    kwargs = clickhouse_client_kwargs(Settings.from_env(tls_env))
    assert kwargs["secure"] is True
    assert kwargs["verify"] is True
    assert kwargs["ca_cert"] == tls_env["CH_CA_FILE"]
    assert kwargs["port"] == 8443


@pytest.mark.parametrize(
    "override",
    [
        {"PG_SSLMODE": "require"},
        {"PG_SSLMODE": "disable"},
        {"ODOO_PG_SSLMODE": "prefer"},
        {"PG_SSLROOTCERT": "/nonexistent/ca.crt"},
        {"CH_SECURE": "false"},
        {"CH_CA_FILE": "/nonexistent/ca.crt"},
        {"EFN_ALLOW_PLAINTEXT": "true"},
        # SEC-27: APP_ENV kosong / tidak dikenal = production.
        {"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": ""},
        {"EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "staging"},
        # AMQP erasure (docs/04 §8.4): wajib amqps + CA, tanpa password di URL.
        {"AMQP_URL": "amqp://efn-etl@rabbitmq:5672"},
        {"AMQP_URL": "amqps://efn-etl:inline@rabbitmq:5671"},
        {"AMQP_URL": "amqps://rabbitmq:5671"},
        {"AMQP_TLS_CA_FILE": "/nonexistent/ca.crt"},
    ],
)
def test_fail_closed_without_tls(tls_env: dict[str, str], override: dict[str, str]) -> None:
    with pytest.raises(TlsConfigError):
        Settings.from_env({**tls_env, **override})


def test_amqp_defaults_for_erasure(tls_env: dict[str, str]) -> None:
    settings = Settings.from_env(tls_env)
    assert settings.erasure_enabled is True
    assert settings.amqp_url == "amqps://efn-etl@rabbitmq:5671"
    assert settings.amqp_user == "efn-etl"
    assert settings.amqp_ca_file == tls_env["AMQP_TLS_CA_FILE"]


def test_erasure_disabled_skips_amqp_validation(tls_env: dict[str, str]) -> None:
    env = {**tls_env, "ETL_ERASURE_ENABLED": "false", "AMQP_URL": "amqp://x@rabbitmq"}
    settings = Settings.from_env(env)
    assert settings.erasure_enabled is False
    assert settings.amqp_ca_file is None


def test_plaintext_dev_only_with_flag() -> None:
    dev = {
        "APP_ENV": "development",
        "EFN_ALLOW_PLAINTEXT": "true",
        "PG_SSLMODE": "disable",
        "CH_SECURE": "false",
        "AMQP_URL": "amqp://efn-etl@rabbitmq:5672",
    }
    settings = Settings.from_env(dev)
    assert settings.platform_pg.sslrootcert is None
    assert settings.ch_port == 8123
    assert settings.amqp_ca_file is None
    assert "ca_cert" not in clickhouse_client_kwargs(settings)
    with pytest.raises(TlsConfigError):
        # SEC-27: tanpa APP_ENV = production -> flag ditolak.
        Settings.from_env({k: v for k, v in dev.items() if k != "APP_ENV"})
    with pytest.raises(TlsConfigError):
        plaintext_allowed({"EFN_ALLOW_PLAINTEXT": "true", "NODE_ENV": "production"})


def test_is_production_fail_closed() -> None:
    assert is_production({})
    assert is_production({"APP_ENV": "prod"})
    assert is_production({"APP_ENV": "staging"})
    assert is_production({"APP_ENV": "test", "NODE_ENV": "production"})
    for value in ("dev", "development", "local", "test", "ci"):
        assert not is_production({"APP_ENV": value})


def test_password_not_in_repr(tls_env: dict[str, str], tmp_path: Path) -> None:
    secret = tmp_path / "pw"
    secret.write_text("very-secret-value", encoding="utf-8")
    settings = Settings.from_env({**tls_env, "PG_PASSWORD_FILE": str(secret), "CH_PASSWORD_FILE": str(secret)})
    assert "very-secret-value" not in repr(settings)


# ---------------------------------------------------------------- G14: verifikasi sink Loki + rekonsiliasi loyalty


@pytest.fixture
def sink_env(tls_env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    for name in ("crt", "key"):
        (tmp_path / f"etl.{name}").write_text("dummy\n", encoding="utf-8")
    (tmp_path / "sink.key").write_text("k" * 40 + "\n", encoding="utf-8")
    return {
        **tls_env,
        "ETL_AUDIT_COPY_ENABLED": "true",
        "LOKI_URL": "https://loki:3100",
        "LOKI_CA_FILE": tls_env["CH_CA_FILE"],
        "LOKI_CLIENT_CERT_FILE": str(tmp_path / "etl.crt"),
        "LOKI_CLIENT_KEY_FILE": str(tmp_path / "etl.key"),
        "EFN_AUDIT_SINK_KEY_FILE": str(tmp_path / "sink.key"),
    }


def test_sink_verification_enabled_with_mtls(sink_env: dict[str, str]) -> None:
    settings = Settings.from_env(sink_env)
    assert settings.audit_sink_enabled and settings.audit_sink_key == b"k" * 40
    assert settings.loki_client_cert_file and settings.loki_client_key_file
    assert "kkkk" not in repr(settings)
    # Default: mati bila LOKI_URL kosong atau salinan audit mati
    assert not Settings.from_env({**sink_env, "LOKI_URL": ""}).audit_sink_enabled
    assert not Settings.from_env({**sink_env, "ETL_AUDIT_COPY_ENABLED": "false"}).audit_sink_enabled


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"LOKI_URL": "http://loki:3100"}, TlsConfigError),
        ({"LOKI_CLIENT_CERT_FILE": ""}, TlsConfigError),
        ({"LOKI_CLIENT_KEY_FILE": "/nope"}, TlsConfigError),
        ({"EFN_AUDIT_SINK_KEY_FILE": "/nope"}, RuntimeError),
        ({"ETL_AUDIT_SINK_ENABLED": "true", "ETL_AUDIT_COPY_ENABLED": "false"}, RuntimeError),
    ],
)
def test_sink_verification_fails_closed(sink_env: dict[str, str], override: dict[str, str], error: type) -> None:
    with pytest.raises(error):
        Settings.from_env({**sink_env, **override})


def test_short_sink_key_rejected(sink_env: dict[str, str], tmp_path: Path) -> None:
    short = tmp_path / "short.key"
    short.write_text("pendek", encoding="utf-8")
    with pytest.raises(ValueError):
        Settings.from_env({**sink_env, "EFN_AUDIT_SINK_KEY_FILE": str(short)})


def test_loyalty_recon_defaults_follow_odoo(tls_env: dict[str, str]) -> None:
    assert not Settings.from_env(tls_env).loyalty_recon_enabled
    settings = Settings.from_env({**tls_env, "ETL_ENABLE_ODOO": "true", "ETL_LOYALTY_RECON_SETTLE_HOURS": "4"})
    assert settings.loyalty_recon_enabled and settings.loyalty_recon_settle_hours == 4
    assert settings.loyalty_recon_lookback_hours == 72 and settings.loyalty_recon_interval_seconds == 86_400
    assert settings.loyalty_recon_not_before is None
    cutoff = Settings.from_env({**tls_env, "ETL_LOYALTY_RECON_NOT_BEFORE": "2026-10-07T07:09:00Z"})
    assert cutoff.loyalty_recon_not_before is not None and cutoff.loyalty_recon_not_before.hour == 7


def test_column_retention_defaults_w19(tls_env: dict[str, str]) -> None:
    # G18/W-19 (PDP v7): pseudonim 5 th, lokasi Price Hunter 90 h — env sama dengan clickhouse-init.
    settings = Settings.from_env(tls_env)
    assert (settings.pseudonym_retention_years, settings.geo_retention_days) == (5, 90)
    custom = Settings.from_env({**tls_env, "CH_PSEUDONYM_RETENTION_YEARS": "3", "CH_GEO_RETENTION_DAYS": "30"})
    assert (custom.pseudonym_retention_years, custom.geo_retention_days) == (3, 30)


@pytest.mark.parametrize(
    "override",
    [
        {"CH_PSEUDONYM_RETENTION_YEARS": "0"},
        {"CH_PSEUDONYM_RETENTION_YEARS": "31"},
        {"CH_GEO_RETENTION_DAYS": "0"},
        {"CH_GEO_RETENTION_DAYS": "3651"},
    ],
)
def test_column_retention_out_of_range_fails(tls_env: dict[str, str], override: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, **override})


def test_loyalty_points_mode_w18(tls_env: dict[str, str]) -> None:
    # SEC-114 (L-25b): default `payment`; toleransi default 0 (K-18-1).
    settings = Settings.from_env(tls_env)
    assert (settings.loyalty_points_mode, settings.loyalty_points_tolerance_idr) == ("payment", 0)
    paid = Settings.from_env({**tls_env, "ETL_LOYALTY_POINTS_MODE": "Payment"})
    assert paid.loyalty_points_mode == "payment"
    # `product` (data historis) ditolak di production (APP_ENV production / kosong), boleh di non-production.
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "ETL_LOYALTY_POINTS_MODE": "product"})
    with pytest.raises(RuntimeError):
        Settings.from_env({**tls_env, "APP_ENV": "", "ETL_LOYALTY_POINTS_MODE": "product"})
    dev = Settings.from_env({**tls_env, "APP_ENV": "development", "ETL_LOYALTY_POINTS_MODE": "product"})
    assert dev.loyalty_points_mode == "product"
    for bad in ({"ETL_LOYALTY_POINTS_MODE": "diskon"}, {"ETL_LOYALTY_POINTS_TOLERANCE_IDR": "-1"}):
        with pytest.raises(RuntimeError):
            Settings.from_env({**tls_env, **bad})
