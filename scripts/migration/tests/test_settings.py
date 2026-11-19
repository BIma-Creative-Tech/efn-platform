"""Konfigurasi `esb-migrate` fail closed (docs/04 §27.2): rentang parameter, secret, TLS. Tanpa jaringan."""

from pathlib import Path

import pytest

from efn_mig.settings import (
    ClickHouseSettings,
    ConfigError,
    MigSettings,
    bundle_keys,
    is_production,
    read_secret,
)

KEY = "cd" * 32


def test_defaults_and_blocked_list() -> None:
    s = MigSettings.from_env({"APP_ENV": "dev"})
    assert (s.legacy_days, s.sales_line_months, s.segment_max_rows) == (400, 24, 500_000)
    assert s.saldo_blocked == frozenset({"IMD", "CUP"}) and not s.production
    custom = MigSettings.from_env({"APP_ENV": "dev", "EFN_MIG_SALDO_BLOCKED": " imd , cup,wtz "})
    assert custom.saldo_blocked == frozenset({"IMD", "CUP", "WTZ"})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("EFN_MIG_LEGACY_DAYS", "0"),
        ("EFN_MIG_LEGACY_DAYS", "731"),
        ("EFN_MIG_LEGACY_DAYS", "abc"),
        ("EFN_MIG_SALES_LINE_MONTHS", "-1"),
        ("EFN_MIG_SALES_LINE_MONTHS", "25"),
        ("EFN_MIG_SEGMENT_MAX_ROWS", "999"),
        ("EFN_MIG_SEGMENT_MAX_ROWS", "5000001"),
    ],
)
def test_out_of_range_parameters_rejected(name: str, value: str) -> None:
    with pytest.raises(ConfigError):
        MigSettings.from_env({"APP_ENV": "dev", name: value})


def test_sales_line_months_zero_is_allowed() -> None:
    assert MigSettings.from_env({"APP_ENV": "dev", "EFN_MIG_SALES_LINE_MONTHS": "0"}).sales_line_months == 0


@pytest.mark.parametrize("env", [{}, {"APP_ENV": ""}, {"APP_ENV": "prodution"}, {"APP_ENV": "staging"},
                                 {"APP_ENV": "dev", "NODE_ENV": "production"}])  # fmt: skip
def test_unknown_or_missing_env_is_production(env: dict[str, str]) -> None:
    assert is_production(env)


def test_secret_requires_file_in_production(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="wajib di production"):
        read_secret({"APP_ENV": "production"}, "MIG_BUNDLE_KEY")
    with pytest.raises(ConfigError, match="tidak ditemukan"):
        read_secret({"MIG_BUNDLE_KEY_FILE": str(tmp_path / "tidak-ada")}, "MIG_BUNDLE_KEY")
    # Non-production boleh env langsung (dev/CI), production tidak pernah.
    assert read_secret({"APP_ENV": "dev", "MIG_BUNDLE_KEY": "x"}, "MIG_BUNDLE_KEY") == "x"


def test_bundle_key_must_be_long_hex(tmp_path: Path) -> None:
    for bad in ("", "zz" * 32, "ab" * 10):
        f = tmp_path / "k.txt"
        f.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigError):
            bundle_keys({"APP_ENV": "production", "MIG_BUNDLE_KEY_FILE": str(f)})
    f = tmp_path / "ok.txt"
    f.write_text(KEY + "\n", encoding="utf-8")
    keys = bundle_keys({"APP_ENV": "production", "MIG_BUNDLE_KEY_FILE": str(f)})
    assert keys.enc != keys.mac  # HKDF info terpisah (enkripsi vs HMAC manifest)


def test_clickhouse_tls_is_mandatory(tmp_path: Path) -> None:
    ca = tmp_path / "ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n", encoding="utf-8")
    pw = tmp_path / "pw.txt"
    pw.write_text("rahasia-uji", encoding="utf-8")
    base = {"CH_PASSWORD_FILE": str(pw), "CH_CA_FILE": str(ca)}
    ok = ClickHouseSettings.from_env({"APP_ENV": "production", **base})
    assert ok.secure and ok.port == 8443 and ok.user == "efn_mig" and ok.ca_file == str(ca)
    assert "rahasia-uji" not in repr(ok), "password tidak boleh masuk repr/log"
    with pytest.raises(ConfigError, match="CH_SECURE"):  # tanpa fallback diam-diam ke plaintext
        ClickHouseSettings.from_env({"APP_ENV": "dev", "CH_SECURE": "false", **base})
    with pytest.raises(ConfigError, match="production"):
        ClickHouseSettings.from_env(
            {"APP_ENV": "production", "CH_SECURE": "false", "EFN_ALLOW_PLAINTEXT": "true", **base}
        )
    with pytest.raises(ConfigError, match="CH_CA_FILE"):
        ClickHouseSettings.from_env({"APP_ENV": "production", "CH_PASSWORD_FILE": str(pw),
                                     "CH_CA_FILE": str(tmp_path / "hilang.crt")})  # fmt: skip
    with pytest.raises(ConfigError, match="CH_PASSWORD_FILE"):
        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")
        ClickHouseSettings.from_env({"APP_ENV": "production", "CH_CA_FILE": str(ca), "CH_PASSWORD_FILE": str(empty)})
