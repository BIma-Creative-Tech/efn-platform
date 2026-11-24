"""Konfigurasi: kill switch, kredensial per brand (fail closed), allowlist host, override PT, CK, ClickHouse TLS."""

from pathlib import Path

import pytest

from efn_esb.config import (
    ClickHouseSettings,
    EsbSettings,
    esb_enabled,
    parse_branch_sets,
    parse_company_overrides,
    parse_entities,
    parse_window,
    validate_base_url,
)
from efn_esb.tls import TlsConfigError

SECRET = "sintetis-Password-xyz"


def env_with(tmp_path: Path, **extra: str) -> dict[str, str]:
    secret = tmp_path / "esb_core_password_pb"
    secret.write_text(SECRET + "\n", encoding="utf-8")
    base = {
        "ESB_ENABLED": "true",
        "ESB_BRANDS": "pb",
        "ESB_USER_PB": "user-sintetis",
        "ESB_PASSWORD_PB_FILE": str(secret),
    }
    base.update(extra)
    return base


def test_disabled_by_default() -> None:
    assert esb_enabled({}) is False
    assert esb_enabled({"ESB_ENABLED": "TRUE"}) is True


def test_defaults_match_compose_overlay(tmp_path: Path) -> None:
    settings = EsbSettings.from_env(env_with(tmp_path))
    assert settings.base_url == "https://services.esb.co.id/core"
    assert settings.rate_per_sec == 2.0 and settings.workers == 4 and settings.max_calls_per_run == 20_000
    assert settings.interval_seconds == 3600
    assert "stock" not in settings.entities
    brand = settings.brand("pb")
    assert brand.password == SECRET and brand.company_code == "PB"
    assert brand.ck_branches == frozenset({4, 38})
    assert SECRET not in repr(settings) and SECRET not in repr(brand)


@pytest.mark.parametrize("content", ["disabled", "", "changeme"])
def test_placeholder_password_fails_closed(tmp_path: Path, content: str) -> None:
    env = env_with(tmp_path)
    Path(env["ESB_PASSWORD_PB_FILE"]).write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError) as info:
        EsbSettings.from_env(env)
    assert "esb_core_password_pb" in str(info.value)


def test_missing_user_or_secret_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_USER_PB=""))
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_PASSWORD_PB_FILE=str(tmp_path / "tidak-ada")))


def test_enabled_without_brands_and_unknown_brand(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_BRANDS=""))
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_BRANDS="pb,xyz"))


@pytest.mark.parametrize(
    "url",
    [
        "http://services.esb.co.id/core",
        "https://evil.example/core",
        "https://services.esb.co.id.evil.example/core",
        "https://user:pw@services.esb.co.id/core",
        "https://services.esb.co.id:8443/core",
        "https://services.esb.co.id/core?x=1",
    ],
)
def test_base_url_allowlist(url: str) -> None:
    with pytest.raises((RuntimeError, TlsConfigError)):
        validate_base_url(url)


def test_base_url_normalised() -> None:
    assert validate_base_url("https://SERVICES.esb.co.id:443/core/") == "https://services.esb.co.id/core"


def test_overrides_and_branch_sets() -> None:
    assert parse_company_overrides("bacha:12=BCR, bacha:15=bcr") == {"bacha": {12: "BCR", 15: "BCR"}}
    assert parse_branch_sets("pb:4|38;wtz:7", "X") == {"pb": frozenset({4, 38}), "wtz": frozenset({7})}
    for bad in ("bacha:12=XYZ", "bacha=BCR", "bacha:abc=BCR"):
        with pytest.raises(RuntimeError):
            parse_company_overrides(bad)
    for bad in ("pb", "pb:0", "pb:x"):
        with pytest.raises(RuntimeError):
            parse_branch_sets(bad, "X")


def test_company_for_override(tmp_path: Path) -> None:
    secret = tmp_path / "bacha"
    secret.write_text(SECRET, encoding="utf-8")
    env = env_with(
        tmp_path,
        ESB_BRANDS="pb,bacha",
        ESB_USER_BACHA="user-b",
        ESB_PASSWORD_BACHA_FILE=str(secret),
        ESB_BRANCH_COMPANY_OVERRIDES="bacha:12=BCR",
    )
    bacha = EsbSettings.from_env(env).brand("bacha")
    assert bacha.company_for(12) == "BCR" and bacha.company_for(13) == "BCF"


def test_window_entities_ranges(tmp_path: Path) -> None:
    assert parse_window("22:00-05:00")[0].hour == 22
    for bad in ("22:00", "25:00-01:00", "05:00-05:00"):
        with pytest.raises(RuntimeError):
            parse_window(bad)
    assert parse_entities("sales, stock,sales") == ("sales", "stock")
    with pytest.raises(RuntimeError):
        parse_entities("sales,customer")
    # G65: purchase_line opt-in (diterima ESB_ENTITIES), bukan default; stock_opening hanya lewat `snapshot`.
    assert parse_entities("branch,purchase_line") == ("branch", "purchase_line")
    assert "purchase_line" not in EsbSettings.from_env(env_with(tmp_path)).entities
    with pytest.raises(RuntimeError):
        parse_entities("stock_opening")
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_RATE_PER_SEC="50"))
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_MAX_CALLS_PER_RUN="0"))
    with pytest.raises(RuntimeError):
        EsbSettings.from_env(env_with(tmp_path, ESB_MAX_RETRIES="9"))


def test_max_response_bytes_default_and_range(tmp_path: Path) -> None:
    mib = 1024 * 1024
    assert EsbSettings.from_env(env_with(tmp_path)).max_response_bytes == 32 * mib
    assert EsbSettings.from_env(env_with(tmp_path, ESB_MAX_RESPONSE_BYTES=str(mib))).max_response_bytes == mib
    top = EsbSettings.from_env(env_with(tmp_path, ESB_MAX_RESPONSE_BYTES=str(128 * mib)))
    assert top.max_response_bytes == 128 * mib
    for bad in (str(mib - 1), str(128 * mib + 1), "32MiB", "-1"):
        with pytest.raises(RuntimeError):
            EsbSettings.from_env(env_with(tmp_path, ESB_MAX_RESPONSE_BYTES=bad))


def test_ignore_window_flag_and_production_fail_closed(tmp_path: Path) -> None:
    default = EsbSettings.from_env(env_with(tmp_path))
    assert default.allow_ignore_window is False and default.production is True  # APP_ENV kosong = production
    dev = EsbSettings.from_env(env_with(tmp_path, APP_ENV="dev", ESB_ALLOW_IGNORE_WINDOW="true"))
    assert dev.production is False and dev.allow_ignore_window is True
    assert EsbSettings.from_env(env_with(tmp_path, APP_ENV="staging")).production is True
    assert EsbSettings.from_env(env_with(tmp_path, APP_ENV="production")).production is True


def test_clickhouse_requires_tls_and_password(tmp_path: Path) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nsintetis\n", encoding="utf-8")
    pw = tmp_path / "ch"
    pw.write_text("sintetisCHpassword123", encoding="utf-8")
    ok = ClickHouseSettings.from_env({"CH_CA_FILE": str(ca), "CH_PASSWORD_FILE": str(pw)})
    assert ok.user == "efn_esb" and ok.port == 8443 and ok.secure and ok.raw_database == "efn_esb"
    assert "sintetisCHpassword123" not in repr(ok)
    with pytest.raises(TlsConfigError):
        ClickHouseSettings.from_env({"CH_SECURE": "false", "CH_PASSWORD_FILE": str(pw)})
    with pytest.raises(TlsConfigError):  # plaintext ditolak di production walau diminta
        ClickHouseSettings.from_env({"CH_SECURE": "false", "EFN_ALLOW_PLAINTEXT": "true", "CH_PASSWORD_FILE": str(pw)})
    with pytest.raises(RuntimeError):
        ClickHouseSettings.from_env({"CH_CA_FILE": str(ca)})
    dev = ClickHouseSettings.from_env(
        {"CH_SECURE": "false", "EFN_ALLOW_PLAINTEXT": "true", "APP_ENV": "dev", "CH_PASSWORD_FILE": str(pw)}
    )
    assert dev.port == 8123 and dev.ca_file is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CH_DATABASE", "efn_dwh; DROP TABLE x"),
        ("CH_DATABASE", "efn-dwh"),
        ("CH_RAW_DATABASE", "Efn_esb"),
        ("CH_RAW_DATABASE", "1efn"),
        ("CH_RAW_DATABASE", "a" * 65),
        ("CH_DATABASE", "efn_dwh.x"),
    ],
)
def test_clickhouse_database_names_are_identifiers(tmp_path: Path, name: str, value: str) -> None:
    # SEC-AG-04: nama database masuk SQL sebagai identifier -> ditolak sebelum klien dibuat.
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nsintetis\n", encoding="utf-8")
    pw = tmp_path / "ch"
    pw.write_text("sintetisCHpassword123", encoding="utf-8")
    env = {"CH_CA_FILE": str(ca), "CH_PASSWORD_FILE": str(pw)}
    with pytest.raises(RuntimeError, match=name):
        ClickHouseSettings.from_env({**env, name: value})
    ok = ClickHouseSettings.from_env({**env, "CH_DATABASE": "efn_dwh_stg", "CH_RAW_DATABASE": "_efn_esb"})
    assert (ok.database, ok.raw_database) == ("efn_dwh_stg", "_efn_esb")
