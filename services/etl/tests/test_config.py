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
