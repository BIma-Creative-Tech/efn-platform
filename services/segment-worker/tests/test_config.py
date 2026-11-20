"""Konfigurasi: default aman, TLS/mTLS wajib, API key per PT, validasi parameter (negatif)."""

from pathlib import Path

import pytest

from efn_segment.config import (
    ClickHouseSettings,
    OdooSettings,
    Params,
    bff_endpoint,
    segment_enabled,
)
from efn_segment.contract import MAX_SUBJECTS_PER_CHUNK
from efn_segment.segments import SEGMENT_CODES
from efn_segment.tls import TlsConfigError


@pytest.fixture
def files(tmp_path: Path) -> dict[str, str]:
    out = {}
    for name in ("ca", "crt", "key", "chpw", "apikey"):
        f = tmp_path / name
        f.write_text({"chpw": "chpassword-test", "apikey": "odoo-key-test"}.get(name, "dummy\n"), encoding="utf-8")
        out[name] = str(f)
    return out


@pytest.fixture
def env(files: dict[str, str]) -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "CH_CA_FILE": files["ca"],
        "CH_PASSWORD_FILE": files["chpw"],
        "SEGMENT_BFF_CA_FILE": files["ca"],
        "ODOO_RPC_CA_FILE": files["ca"],
        "SEGMENT_CLIENT_CERT_FILE": files["crt"],
        "SEGMENT_CLIENT_KEY_FILE": files["key"],
        "ODOO_API_KEY_SEGMENT_SMD_FILE": files["apikey"],
    }


def test_disabled_by_default() -> None:
    assert segment_enabled({}) is False
    assert segment_enabled({"SEGMENT_ENABLED": "true"}) is True


def test_params_defaults() -> None:
    p = Params.from_env({})
    assert p.companies == ("SMD",)
    assert p.chunk_size == MAX_SUBJECTS_PER_CHUNK == 5000
    assert p.segment.segments == SEGMENT_CODES
    assert p.segment.include_device is False
    assert (p.min_segment_size, p.k_min) == (10, 10)


@pytest.mark.parametrize(
    "override",
    [
        {"SEGMENT_CHUNK_SIZE": "5001"},
        {"SEGMENT_CODES": "viewed_not_bought,vip"},
        {"SEGMENT_COMPANIES": "smd-1"},
        {"SEGMENT_WISHLIST_IDLE_DAYS": "30", "SEGMENT_WISHLIST_LOOKBACK_DAYS": "30"},
        {"SEGMENT_LOOKBACK_DAYS": "91"},
        {"SEGMENT_K_MIN": "1"},
        {"SEGMENT_DAILY_CRON": "every day"},
        {"SEGMENT_PRICE_MARGIN": "abc"},
    ],
)
def test_params_rejects(override: dict[str, str]) -> None:
    with pytest.raises(RuntimeError):
        Params.from_env(override)


def test_clickhouse_tls_and_secret(env: dict[str, str]) -> None:
    ch = ClickHouseSettings.from_env(env)
    assert (ch.user, ch.database, ch.port, ch.secure) == ("efn_segment_reader", "efn_segment", 8443, True)
    assert "chpassword-test" not in repr(ch)
    with pytest.raises(TlsConfigError):
        ClickHouseSettings.from_env({**env, "CH_SECURE": "false"})
    with pytest.raises(RuntimeError):
        ClickHouseSettings.from_env({k: v for k, v in env.items() if k != "CH_PASSWORD_FILE"})


def test_bff_endpoint_requires_https_and_mtls(env: dict[str, str]) -> None:
    ep = bff_endpoint(env)
    assert ep.url == "https://api-gateway:3000"
    assert ep.client_cert_file == env["SEGMENT_CLIENT_CERT_FILE"]
    with pytest.raises(TlsConfigError):
        bff_endpoint({**env, "SEGMENT_BFF_URL": "http://api-gateway:3000"})
    with pytest.raises(TlsConfigError):
        bff_endpoint({**env, "SEGMENT_CLIENT_KEY_FILE": "/nonexistent/key"})
    with pytest.raises(RuntimeError):
        bff_endpoint({**env, "SEGMENT_BFF_URL": "https://user:pw@api-gateway:3000"})
    with pytest.raises(RuntimeError):
        bff_endpoint({**env, "SEGMENT_BFF_URL": "https://api-gateway:3000/v1"})


def test_plaintext_only_in_dev(env: dict[str, str]) -> None:
    dev = {**env, "APP_ENV": "dev", "EFN_ALLOW_PLAINTEXT": "true", "SEGMENT_BFF_URL": "http://api-gateway:3000"}
    assert bff_endpoint(dev).client_cert_file is None
    with pytest.raises(TlsConfigError):
        bff_endpoint({**dev, "APP_ENV": ""})  # kosong = production


def test_odoo_settings_api_key_per_company(env: dict[str, str], files: dict[str, str], tmp_path: Path) -> None:
    cfg = OdooSettings.from_env("smd", env)
    assert cfg.company == "SMD" and cfg.api_key == "odoo-key-test"
    assert "odoo-key-test" not in repr(cfg)
    assert cfg.endpoint.url == "https://odoo-rpc-gateway:8443"
    placeholder = tmp_path / "ph"
    placeholder.write_text("disabled", encoding="utf-8")
    with pytest.raises(RuntimeError):
        OdooSettings.from_env("SMD", {**env, "ODOO_API_KEY_SEGMENT_SMD_FILE": str(placeholder)})
    # PT lain tanpa secret -> default /run/secrets/odoo_api_key_segment_gl tidak ada -> gagal (bukan key PT lain).
    with pytest.raises(RuntimeError):
        OdooSettings.from_env("GL", env)
    # Key kampanye milik BFF (user efn_campaign_<kode>) TIDAK dipakai worker.
    only_campaign = {k: v for k, v in env.items() if k != "ODOO_API_KEY_SEGMENT_SMD_FILE"}
    only_campaign["ODOO_API_KEY_CAMPAIGN_SMD_FILE"] = files["apikey"]
    with pytest.raises(RuntimeError):
        OdooSettings.from_env("SMD", only_campaign)
