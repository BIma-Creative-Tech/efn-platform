"""Konfigurasi prodplan dari env non-rahasia + Docker secret (docs/04 §30.3, ADR-051 Proposed). Fail-closed.

- `PRODPLAN_ENABLED` / `PRODPLAN_WRITE_ENABLED`: hanya string `true` persis yang mengaktifkan.
- Base URL ESB hanya salah satu dari dua nilai di `ESB_BASE_URLS`; nilai lain -> `ConfigError` (exit 2).
- L-250: tulis ke ESB production terkunci konstanta kode `PROD_WRITE_UNLOCKED` (BUKAN env). Mengubahnya = PR terpisah
  dengan review security setelah ADR-051 Accepted + Q-PP1.
- Password PG, kunci asersi, dan API key provisioning Odoo hanya dari file `/run/secrets/*`; tidak pernah masuk
  repr/log/pesan exception.
- §30.17 (amandemen A1 ADR-051): TIDAK ada lagi akun service ESB (`PRODPLAN_ESB_USER_<BRAND>`,
  `PRODPLAN_ESB_PASSWORD_<BRAND>_FILE`) maupun `PRODPLAN_SOURCE_LOCATIONS`; semua panggilan ESB memakai token pengguna.
  Baru: `PRODPLAN_ESB_COMPANY_CODES`, aturan peran `PRODPLAN_ESB_ROLE_*`, `PRODPLAN_ESB_DOC_ACCESS`, provisioning Odoo
  (`PRODPLAN_ODOO_*`, default mati).
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

# L-250 (fail-closed). JANGAN diubah tanpa ADR-051 Accepted + Q-PP1 + review security (PR terpisah).
PROD_WRITE_UNLOCKED = False

ESB_BASE_URLS: Mapping[str, str] = {
    "https://services.esb.co.id/core": "production",
    "https://stg7.esb.co.id/core-stg": "staging",
}
DEFAULT_BASE_URL = "https://stg7.esb.co.id/core-stg"
KNOWN_BRANDS = ("pb", "wtz", "bacha", "imd", "cup")
# Kode PT yang disimpan di kolom `company_code` per brand.
BRAND_COMPANY: Mapping[str, str] = {"pb": "PB", "wtz": "WTZ", "bacha": "BCF", "imd": "IMD", "cup": "CUP"}
MFG_MODES = ("actual", "standard")
ODOO_RPC_URL = "https://odoo-rpc-gateway:8443"  # §30.17.13: nilai lain -> exit 2
_PLACEHOLDERS = frozenset({"disabled", "changeme", "change-me", "placeholder", "xxx", "todo", "password"})
_HEX_RE = re.compile(r"^[0-9a-fA-F]{64,}$")
_COMPANY_CODE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


class ConfigError(RuntimeError):
    """Konfigurasi tidak valid -> proses tidak start (exit 2). `reason` aman dilog (tanpa nilai rahasia)."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _flag(env: Mapping[str, str], name: str) -> bool:
    """Hanya `true` persis = aktif (§30.3)."""
    return (env.get(name) or "").strip() == "true"


def _int_range(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = _str(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("invalid_int", f"{name} harus bilangan bulat") from None
    if not low <= value <= high:
        raise ConfigError("out_of_range", f"{name} di luar rentang {low}..{high}")
    return value


def _float_range(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = _str(env, name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError("invalid_number", f"{name} harus angka") from None
    if not low <= value <= high:
        raise ConfigError("out_of_range", f"{name} di luar rentang {low}..{high}")
    return value


def read_secret_file(path: str, what: str) -> str:
    """Isi file secret (di-strip). Pesan galat hanya menyebut nama variabel, bukan isi."""
    try:
        data = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        raise ConfigError("secret_unreadable", f"{what}: file secret tidak terbaca") from None
    if not data:
        raise ConfigError("secret_empty", f"{what}: file secret kosong")
    return data


def prodplan_enabled(env: Mapping[str, str] | None = None) -> bool:
    return _flag(os.environ if env is None else env, "PRODPLAN_ENABLED")


def validate_base_url(url: str) -> tuple[str, str]:
    """Kembalikan (base_url, esb_env). Hanya dua nilai persis (tanpa slash akhir) yang diterima."""
    normalized = url.strip().rstrip("/")
    env_name = ESB_BASE_URLS.get(normalized)
    if env_name is None:
        raise ConfigError("base_url_not_allowed", "PRODPLAN_ESB_BASE_URL bukan salah satu base URL ESB yang diizinkan")
    return normalized, env_name


def check_write_lock(write_enabled: bool, esb_env: str, unlocked: bool | None = None) -> None:
    """L-250: tulis + production + konstanta terkunci -> ConfigError `prod_write_locked`."""
    is_unlocked = PROD_WRITE_UNLOCKED if unlocked is None else unlocked
    if write_enabled and esb_env == "production" and not is_unlocked:
        raise ConfigError("prod_write_locked", "tulis ke ESB production terkunci di kode (L-250)")


def preflight(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Cek murah tanpa secret, dijalankan juga saat PRODPLAN_ENABLED=false: base URL + kunci tulis production."""
    e: Mapping[str, str] = os.environ if env is None else env
    base_url, esb_env = validate_base_url(_str(e, "PRODPLAN_ESB_BASE_URL", DEFAULT_BASE_URL))
    check_write_lock(_flag(e, "PRODPLAN_WRITE_ENABLED"), esb_env)
    return base_url, esb_env


def parse_location_map(text: str, env_name: str, brands: tuple[str, ...]) -> dict[str, dict[int, tuple[int, ...]]]:
    """`pb:4=7|8;13=21` -> {"pb": {4: (7, 8), 13: (21,)}}. Item tanpa `brand:` mewarisi brand sebelumnya."""
    out: dict[str, dict[int, list[int]]] = {}
    brand: str | None = None
    for raw in text.split(";"):
        item = raw.strip()
        if not item:
            continue
        left, sep, locs = item.partition("=")
        if not sep:
            raise ConfigError("invalid_location_map", f"{env_name}: format wajib brand:branch=loc|loc")
        if ":" in left:
            brand_part, _, branch_part = left.partition(":")
            brand = brand_part.strip().lower()
        else:
            branch_part = left
        if brand is None or brand not in KNOWN_BRANDS:
            raise ConfigError("invalid_location_map", f"{env_name}: brand tidak dikenal")
        if brand not in brands:
            continue  # brand tidak aktif -> diabaikan
        branch_text = branch_part.strip()
        if not branch_text.isdigit() or int(branch_text) <= 0:
            raise ConfigError("invalid_location_map", f"{env_name}: branchID harus bilangan bulat positif")
        loc_ids: list[int] = []
        for loc_raw in locs.split("|"):
            loc_text = loc_raw.strip()
            if not loc_text.isdigit() or int(loc_text) <= 0:
                raise ConfigError("invalid_location_map", f"{env_name}: locationID harus bilangan bulat positif")
            loc_ids.append(int(loc_text))
        bucket = out.setdefault(brand, {}).setdefault(int(branch_text), [])
        bucket.extend(loc for loc in loc_ids if loc not in bucket)
    return {b: {br: tuple(sorted(ls)) for br, ls in m.items()} for b, m in out.items()}


def parse_date_window(text: str) -> tuple[int, int]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 2:
        raise ConfigError("invalid_date_window", "PRODPLAN_POST_DATE_WINDOW_DAYS format wajib '<min>,<max>'")
    try:
        low, high = int(parts[0]), int(parts[1])
    except ValueError:
        raise ConfigError("invalid_date_window", "PRODPLAN_POST_DATE_WINDOW_DAYS harus bilangan bulat") from None
    if not (-7 <= low <= 0 <= high <= 31):
        raise ConfigError("invalid_date_window", "PRODPLAN_POST_DATE_WINDOW_DAYS di luar rentang -7..0, 0..31")
    return low, high


def parse_company_codes(text: str, brands: tuple[str, ...]) -> dict[str, str]:
    """`pb=<companyCode ESB>[;wtz=...]` (pemisah `;` atau `,`) -> {brand: companyCode}. Wajib untuk setiap brand aktif;
    kode yang sama untuk dua brand ditolak (login harus memetakan ke tepat satu brand)."""
    out: dict[str, str] = {}
    for raw in text.replace(",", ";").split(";"):
        item = raw.strip()
        if not item:
            continue
        brand, sep, code = (part.strip() for part in item.partition("="))
        brand = brand.lower()
        if not sep or brand not in KNOWN_BRANDS or brand in out or not _COMPANY_CODE_RE.fullmatch(code):
            raise ConfigError("invalid_company_codes", "PRODPLAN_ESB_COMPANY_CODES format wajib brand=companyCode")
        out[brand] = code
    active = {b: c for b, c in out.items() if b in brands}
    if any(b not in active for b in brands):
        raise ConfigError("esb_company_code_missing", "PRODPLAN_ESB_COMPANY_CODES wajib memuat setiap brand aktif")
    if len(set(active.values())) != len(active):
        raise ConfigError("invalid_company_codes", "PRODPLAN_ESB_COMPANY_CODES: companyCode ganda")
    return active


@dataclass(frozen=True)
class BrandConfig:
    key: str
    company_code: str
    esb_company_code: str = ""  # `result.companyCode` login ESB untuk brand ini (§30.17.3 langkah 4)
    ck: Mapping[int, tuple[int, ...]] = field(default_factory=dict)

    def is_ck(self, branch_id: int, location_id: int) -> bool:
        return location_id in self.ck.get(branch_id, ())


@dataclass(frozen=True)
class OdooProvisionSettings:
    """§30.17.11. `enabled=False` -> tidak ada antrean/koneksi; file secret/sertifikat tidak dibaca."""

    enabled: bool = False
    url: str = ODOO_RPC_URL
    api_key: str = field(default="", repr=False)
    ca_file: str = "/run/secrets/efn_ca_crt"
    cert_file: str = "/run/secrets/tls_prodplan_odoo_crt"
    key_file: str = "/run/secrets/tls_prodplan_odoo_key"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        url = _str(env, "PRODPLAN_ODOO_RPC_URL", ODOO_RPC_URL).rstrip("/")
        if url != ODOO_RPC_URL:
            raise ConfigError("odoo_rpc_url", "PRODPLAN_ODOO_RPC_URL wajib https://odoo-rpc-gateway:8443")
        if not _flag(env, "PRODPLAN_ODOO_PROVISION_ENABLED"):
            return cls(enabled=False, url=url)
        key_path = _str(env, "PRODPLAN_ODOO_API_KEY_FILE", "/run/secrets/odoo_api_key_esb_provisioner")
        api_key = read_secret_file(key_path, "PRODPLAN_ODOO_API_KEY")
        if api_key.lower() in _PLACEHOLDERS:
            raise ConfigError("odoo_api_key_placeholder", "PRODPLAN_ODOO_API_KEY: masih placeholder")
        files = {
            "ca_file": _str(env, "PRODPLAN_ODOO_CA_FILE", "/run/secrets/efn_ca_crt"),
            "cert_file": _str(env, "PRODPLAN_ODOO_CLIENT_CERT_FILE", "/run/secrets/tls_prodplan_odoo_crt"),
            "key_file": _str(env, "PRODPLAN_ODOO_CLIENT_KEY_FILE", "/run/secrets/tls_prodplan_odoo_key"),
        }
        for name, path in files.items():
            read_secret_file(path, f"PRODPLAN_ODOO_{name.upper()}")  # hanya cek terbaca; isi tidak disimpan
        return cls(enabled=True, url=url, api_key=api_key, **files)


@dataclass(frozen=True)
class PgSettings:
    host: str = "postgres"
    port: int = 5432
    dbname: str = "efn_prodplan"
    user: str = "efn_prodplan"
    password: str = field(default="", repr=False)
    sslmode: str = "verify-full"
    sslrootcert: str = "/run/secrets/efn_ca_crt"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Self:
        sslmode = _str(env, "PG_SSLMODE", "verify-full")
        if sslmode != "verify-full":
            raise ConfigError("pg_sslmode", "PG_SSLMODE wajib verify-full")
        rootcert = _str(env, "PG_SSLROOTCERT", "/run/secrets/efn_ca_crt")
        read_secret_file(rootcert, "PG_SSLROOTCERT")
        password = read_secret_file(_str(env, "PG_PASSWORD_FILE", "/run/secrets/pg_password_prodplan"), "PG_PASSWORD")
        return cls(
            host=_str(env, "PG_HOST", "postgres"),
            port=_int_range(env, "PG_PORT", 5432, 1, 65535),
            dbname=_str(env, "PG_DB", "efn_prodplan"),
            user=_str(env, "PG_USER", "efn_prodplan"),
            password=password,
            sslmode=sslmode,
            sslrootcert=rootcert,
        )


@dataclass(frozen=True)
class Settings:
    enabled: bool
    write_enabled: bool
    base_url: str
    esb_env: str
    mfg_mode: str
    brands: tuple[BrandConfig, ...]
    assert_key: bytes = field(repr=False)
    pg: PgSettings
    app_env: str = "production"
    apply_yield: bool = False
    stock_max_age_minutes: int = 30
    post_max_docs: int = 60
    date_window: tuple[int, int] = (-1, 7)
    rate_per_sec: float = 2.0
    job_max_calls: int = 2000
    timeout_seconds: int = 30
    max_response_bytes: int = 16_777_216
    outbox_poll_seconds: int = 10
    reconcile_grace_seconds: int = 300
    retention_days: int = 730
    planner_rules: tuple[tuple[str, frozenset[str]], ...] = ()
    warehouse_rules: tuple[tuple[str, frozenset[str]], ...] = ()
    doc_access: Mapping[str, str] = field(default_factory=dict)
    odoo: OdooProvisionSettings = field(default_factory=OdooProvisionSettings)

    def brand(self, key: str) -> BrandConfig | None:
        for cfg in self.brands:
            if cfg.key == key:
                return cfg
        return None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        base_url, esb_env = validate_base_url(_str(e, "PRODPLAN_ESB_BASE_URL", DEFAULT_BASE_URL))
        write_enabled = _flag(e, "PRODPLAN_WRITE_ENABLED")
        check_write_lock(write_enabled, esb_env)
        mfg_mode = _str(e, "PRODPLAN_ESB_MFG_MODE", "actual")
        if mfg_mode not in MFG_MODES:
            raise ConfigError("invalid_mfg_mode", "PRODPLAN_ESB_MFG_MODE wajib actual atau standard")
        keys = tuple(dict.fromkeys(k.strip().lower() for k in _str(e, "PRODPLAN_BRANDS", "pb").split(",") if k.strip()))
        if not keys or any(k not in KNOWN_BRANDS for k in keys):
            raise ConfigError("invalid_brands", f"PRODPLAN_BRANDS wajib subset {', '.join(KNOWN_BRANDS)}")
        ck = parse_location_map(_str(e, "PRODPLAN_CK_BRANCHES", "pb:4=7"), "PRODPLAN_CK_BRANCHES", keys)
        company_codes = parse_company_codes(_str(e, "PRODPLAN_ESB_COMPANY_CODES", ""), keys)
        brands = tuple(
            BrandConfig(key=k, company_code=BRAND_COMPANY[k], esb_company_code=company_codes[k], ck=ck.get(k, {}))
            for k in keys
        )
        # Impor lokal: `access` memakai `ConfigError` dari modul ini.
        from efn_prodplan.access import (
            DEFAULT_DOC_ACCESS,
            DEFAULT_PLANNER_RULES,
            DEFAULT_WAREHOUSE_RULES,
            parse_doc_access,
            parse_rules,
        )

        planner_env, warehouse_env = "PRODPLAN_ESB_ROLE_PLANNER", "PRODPLAN_ESB_ROLE_WAREHOUSE"
        planner_rules = parse_rules(_str(e, planner_env, DEFAULT_PLANNER_RULES), planner_env)
        warehouse_rules = parse_rules(_str(e, warehouse_env, DEFAULT_WAREHOUSE_RULES), warehouse_env)
        doc_access = parse_doc_access(_str(e, "PRODPLAN_ESB_DOC_ACCESS", DEFAULT_DOC_ACCESS))
        odoo = OdooProvisionSettings.from_env(e)
        key_text = read_secret_file(
            _str(e, "PRODPLAN_ASSERT_KEY_FILE", "/run/secrets/prodplan_assert_key"), "PRODPLAN_ASSERT_KEY"
        )
        if not _HEX_RE.fullmatch(key_text):
            raise ConfigError("assert_key_invalid", "PRODPLAN_ASSERT_KEY wajib >= 64 karakter hex")
        return cls(
            enabled=prodplan_enabled(e),
            write_enabled=write_enabled,
            base_url=base_url,
            esb_env=esb_env,
            mfg_mode=mfg_mode,
            brands=brands,
            # §30.7: kunci HMAC = byte ASCII string hex (bukan hasil decode hex).
            assert_key=key_text.encode("ascii"),
            pg=PgSettings.from_env(e),
            app_env=_str(e, "APP_ENV", "production"),
            apply_yield=_flag(e, "PRODPLAN_APPLY_YIELD"),
            stock_max_age_minutes=_int_range(e, "PRODPLAN_STOCK_MAX_AGE_MINUTES", 30, 5, 240),
            post_max_docs=_int_range(e, "PRODPLAN_POST_MAX_DOCS", 60, 1, 200),
            date_window=parse_date_window(_str(e, "PRODPLAN_POST_DATE_WINDOW_DAYS", "-1,7")),
            rate_per_sec=_float_range(e, "PRODPLAN_ESB_RATE_PER_SEC", 2.0, 0.2, 4.0),
            job_max_calls=_int_range(e, "PRODPLAN_JOB_MAX_CALLS", 2000, 10, 20_000),
            timeout_seconds=_int_range(e, "PRODPLAN_ESB_TIMEOUT_SECONDS", 30, 5, 120),
            max_response_bytes=_int_range(e, "PRODPLAN_ESB_MAX_RESPONSE_BYTES", 16_777_216, 1_048_576, 67_108_864),
            outbox_poll_seconds=_int_range(e, "PRODPLAN_OUTBOX_POLL_SECONDS", 10, 2, 300),
            reconcile_grace_seconds=_int_range(e, "PRODPLAN_RECONCILE_GRACE_SECONDS", 300, 60, 3600),
            retention_days=_int_range(e, "PRODPLAN_RETENTION_DAYS", 730, 400, 1825),
            planner_rules=planner_rules,
            warehouse_rules=warehouse_rules,
            doc_access=doc_access,
            odoo=odoo,
        )
