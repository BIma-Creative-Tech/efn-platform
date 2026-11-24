import pytest

from efn_prodplan import config
from efn_prodplan.config import (
    ConfigError,
    Settings,
    check_write_lock,
    parse_company_codes,
    parse_date_window,
    parse_location_map,
    preflight,
    prodplan_enabled,
)

STG = "https://stg7.esb.co.id/core-stg"
PROD = "https://services.esb.co.id/core"
HEX_KEY = "ab" * 32


def test_prod_write_unlocked_constant_is_false():
    # L-250: wajib False sampai ADR-051 Accepted + Q-PP1 (perubahan = PR terpisah + review security).
    assert config.PROD_WRITE_UNLOCKED is False


def test_write_to_production_is_locked():
    with pytest.raises(ConfigError) as exc:
        preflight({"PRODPLAN_ESB_BASE_URL": PROD, "PRODPLAN_WRITE_ENABLED": "true"})
    assert exc.value.reason == "prod_write_locked"


def test_write_to_staging_allowed_and_prod_read_only_allowed():
    assert preflight({"PRODPLAN_ESB_BASE_URL": STG, "PRODPLAN_WRITE_ENABLED": "true"}) == (STG, "staging")
    assert preflight({"PRODPLAN_ESB_BASE_URL": PROD}) == (PROD, "production")
    assert preflight({}) == (STG, "staging")


def test_lock_only_lifts_with_code_constant():
    check_write_lock(True, "production", unlocked=True)
    with pytest.raises(ConfigError):
        check_write_lock(True, "production", unlocked=False)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/core",
        "http://services.esb.co.id/core",
        "https://services.esb.co.id/core-stg",
        "https://stg7.esb.co.id/core",
        "https://services.esb.co.id:8443/core",
        "https://user:pw@services.esb.co.id/core",
        "https://services.esb.co.id/core?x=1",
    ],
)
def test_base_url_outside_allowlist_rejected(url):
    with pytest.raises(ConfigError) as exc:
        preflight({"PRODPLAN_ESB_BASE_URL": url})
    assert exc.value.reason == "base_url_not_allowed"


def test_flags_only_exact_true():
    assert prodplan_enabled({"PRODPLAN_ENABLED": "true"}) is True
    for value in ("TRUE", "1", "yes", "on", " true x", ""):
        assert prodplan_enabled({"PRODPLAN_ENABLED": value}) is False
    # WRITE "TRUE" bukan true -> tidak memicu kunci production.
    assert preflight({"PRODPLAN_ESB_BASE_URL": PROD, "PRODPLAN_WRITE_ENABLED": "TRUE"}) == (PROD, "production")


def test_parse_location_map():
    assert parse_location_map("pb:4=8|9;13=21", "X", ("pb",)) == {"pb": {4: (8, 9), 13: (21,)}}
    assert parse_location_map("pb:4=7", "X", ("pb",)) == {"pb": {4: (7,)}}
    assert parse_location_map("wtz:5=1", "X", ("pb",)) == {}
    assert parse_location_map("", "X", ("pb",)) == {}


@pytest.mark.parametrize("text", ["pb:4=x", "zz:4=1", "4=7", "pb:4", "pb:0=7", "pb:4=-1"])
def test_parse_location_map_invalid(text):
    with pytest.raises(ConfigError):
        parse_location_map(text, "X", ("pb",))


def test_parse_date_window():
    assert parse_date_window("-1,7") == (-1, 7)
    for bad in ("7", "1,7", "-1,40", "a,b", "-8,1"):
        with pytest.raises(ConfigError):
            parse_date_window(bad)


def _env(tmp_path, **over):
    secrets = {
        "pg": "pg-secret",
        "ca": "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n",
        "key": HEX_KEY,
    }
    for name, value in secrets.items():
        (tmp_path / name).write_text(value, encoding="utf-8")
    env = {
        "PRODPLAN_ENABLED": "true",
        "PRODPLAN_ESB_BASE_URL": STG,
        "PRODPLAN_BRANDS": "pb",
        "PRODPLAN_ESB_COMPANY_CODES": "pb=CORP-UJI",
        "PRODPLAN_ASSERT_KEY_FILE": str(tmp_path / "key"),
        "PG_PASSWORD_FILE": str(tmp_path / "pg"),
        "PG_SSLROOTCERT": str(tmp_path / "ca"),
    }
    env.update(over)
    return env


def test_settings_from_env(tmp_path):
    s = Settings.from_env(_env(tmp_path))
    assert s.enabled and not s.write_enabled
    assert s.esb_env == "staging" and s.mfg_mode == "actual"
    assert s.assert_key == HEX_KEY.encode("ascii")
    brand = s.brand("pb")
    assert brand is not None and brand.company_code == "PB" and brand.esb_company_code == "CORP-UJI"
    assert brand.is_ck(4, 7) and not brand.is_ck(4, 8)
    assert s.pg.sslmode == "verify-full"
    # Default aturan peran §30.17.6
    assert ("B.4", frozenset({"view", "insert"})) in s.planner_rules
    assert ("D.9", frozenset({"view", "insert"})) in s.warehouse_rules
    assert s.doc_access == {"simple_transfer": "D.9", "goods_transfer_request": "D.4", "simple_manufacturing": "B.4"}
    assert s.odoo.enabled is False  # provisioning default mati, tanpa secret
    text = repr(s)
    assert "pg-secret" not in text and HEX_KEY not in text


def test_service_account_env_is_gone(tmp_path):
    """§30.17.10: akun service ESB dihapus; env lama diabaikan (tidak dibaca, tidak wajib)."""
    s = Settings.from_env(_env(tmp_path, PRODPLAN_ESB_USER_PB="x", PRODPLAN_ESB_PASSWORD_PB_FILE="/nonexistent/x"))
    assert s.brand("pb") is not None
    assert not hasattr(s.brand("pb"), "password") and not hasattr(s.brand("pb"), "user")
    assert not hasattr(s.brand("pb"), "sources")


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"PRODPLAN_ESB_COMPANY_CODES": ""}, "esb_company_code_missing"),
        ({"PRODPLAN_ESB_COMPANY_CODES": "wtz=X"}, "esb_company_code_missing"),
        ({"PRODPLAN_ESB_COMPANY_CODES": "pb"}, "invalid_company_codes"),
        ({"PRODPLAN_ESB_ROLE_PLANNER": "B.4:view+authorize"}, "esb_role_rule"),
        ({"PRODPLAN_ESB_ROLE_WAREHOUSE": "D.9"}, "esb_role_rule"),
        ({"PRODPLAN_ESB_ROLE_WAREHOUSE": ";"}, "esb_role_rule"),
        ({"PRODPLAN_ESB_DOC_ACCESS": "simple_transfer=D.9"}, "esb_role_rule"),
        ({"PRODPLAN_ODOO_RPC_URL": "https://odoo:8069"}, "odoo_rpc_url"),
        ({"PRODPLAN_ODOO_PROVISION_ENABLED": "true"}, "secret_unreadable"),
        ({"PG_SSLMODE": "require"}, "pg_sslmode"),
        ({"PRODPLAN_ESB_MFG_MODE": "mixed"}, "invalid_mfg_mode"),
        ({"PRODPLAN_BRANDS": "pb,xyz"}, "invalid_brands"),
        ({"PRODPLAN_ESB_RATE_PER_SEC": "10"}, "out_of_range"),
        ({"PRODPLAN_STOCK_MAX_AGE_MINUTES": "1"}, "out_of_range"),
        ({"PRODPLAN_RETENTION_DAYS": "100"}, "out_of_range"),
    ],
)
def test_settings_invalid(tmp_path, over, reason):
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(_env(tmp_path, **over))
    assert exc.value.reason == reason


def test_odoo_provision_enabled_reads_files_without_leaking(tmp_path):
    for name in ("api", "crt", "pem"):
        (tmp_path / name).write_text(f"isi-{name}-rekaan", encoding="utf-8")
    env = _env(
        tmp_path,
        PRODPLAN_ODOO_PROVISION_ENABLED="true",
        PRODPLAN_ODOO_API_KEY_FILE=str(tmp_path / "api"),
        PRODPLAN_ODOO_CLIENT_CERT_FILE=str(tmp_path / "crt"),
        PRODPLAN_ODOO_CLIENT_KEY_FILE=str(tmp_path / "pem"),
        PRODPLAN_ODOO_CA_FILE=str(tmp_path / "ca"),
    )
    s = Settings.from_env(env)
    assert s.odoo.enabled and s.odoo.api_key == "isi-api-rekaan"
    assert "isi-api-rekaan" not in repr(s)


def test_parse_company_codes():
    assert parse_company_codes("pb=ABC; wtz=XYZ", ("pb",)) == {"pb": "ABC"}
    assert parse_company_codes("pb=ABC,wtz=XYZ", ("pb", "wtz")) == {"pb": "ABC", "wtz": "XYZ"}
    for bad in ("pb=A;wtz=A", "zz=A", "pb=a b", "pb=A;pb=B"):
        with pytest.raises(ConfigError):
            parse_company_codes(bad, ("pb", "wtz"))


def test_short_key(tmp_path):
    env = _env(tmp_path)
    (tmp_path / "key").write_text("abcd", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(env)
    assert exc.value.reason == "assert_key_invalid"


def test_settings_prod_write_locked(tmp_path):
    with pytest.raises(ConfigError) as exc:
        Settings.from_env(_env(tmp_path, PRODPLAN_ESB_BASE_URL=PROD, PRODPLAN_WRITE_ENABLED="true"))
    assert exc.value.reason == "prod_write_locked"
