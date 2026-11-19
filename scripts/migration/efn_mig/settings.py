"""Konfigurasi `esb-migrate` dari env non-rahasia + Docker secret (docs/04 §27.2). Fail closed.

Kebijakan TLS = salinan kecil `services/esb-extract/efn_esb/tls.py` (SEC-27): APP_ENV kosong/tidak dikenal =
production; plaintext ke ClickHouse hanya `EFN_ALLOW_PLAINTEXT=true` di env non-produksi. Secret tidak pernah masuk
repr/log/pesan error.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from efn_mig.bundle import Keys, derive_keys
from efn_mig.segments import MAX_LEGACY_DAYS, MAX_SALES_LINE_MONTHS

NON_PRODUCTION_ENVS = frozenset({"dev", "development", "local", "test", "ci"})
DEFAULT_CA_FILE = "/run/secrets/efn_ca_crt"
_TRUE = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Konfigurasi tidak valid (exit 2)."""


def _get(env: Mapping[str, str], name: str, default: str = "") -> str:
    value = (env.get(name) or "").strip()
    return value or default


def is_production(env: Mapping[str, str]) -> bool:
    values = [_get(env, k).lower() for k in ("APP_ENV", "NODE_ENV") if _get(env, k)]
    return not values or any(v not in NON_PRODUCTION_ENVS for v in values)


def read_secret(env: Mapping[str, str], name: str) -> str:
    """`<name>_FILE` wajib ada bila di-set; tanpa fallback ke env berisi secret di production."""
    path = _get(env, f"{name}_FILE")
    if path:
        file = Path(path)
        if not file.is_file():
            raise ConfigError(f"{name}_FILE di-set tetapi file tidak ditemukan")
        return file.read_text(encoding="utf-8").strip()
    if is_production(env):
        raise ConfigError(f"{name}_FILE wajib di production")
    return _get(env, name)


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = _get(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} harus bilangan bulat") from None
    if not low <= value <= high:
        raise ConfigError(f"{name} di luar rentang {low}..{high}")
    return value


@dataclass(frozen=True)
class MigSettings:
    production: bool
    legacy_days: int = 400
    sales_line_months: int = 24
    segment_max_rows: int = 500_000
    saldo_blocked: frozenset[str] = frozenset({"IMD", "CUP"})
    workdir: Path = Path("/work")
    out_dir: Path = Path("/out")
    git_sha: str = "unknown"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        raw_blocked = _get(e, "EFN_MIG_SALDO_BLOCKED", "IMD,CUP")
        blocked = frozenset(c.strip().upper() for c in raw_blocked.split(",") if c.strip())
        return cls(
            production=is_production(e),
            legacy_days=_int(e, "EFN_MIG_LEGACY_DAYS", 400, 1, MAX_LEGACY_DAYS),
            sales_line_months=_int(e, "EFN_MIG_SALES_LINE_MONTHS", 24, 0, MAX_SALES_LINE_MONTHS),
            segment_max_rows=_int(e, "EFN_MIG_SEGMENT_MAX_ROWS", 500_000, 1_000, 5_000_000),
            saldo_blocked=blocked,
            workdir=Path(_get(e, "MIG_WORKDIR", "/work")),
            out_dir=Path(_get(e, "MIG_OUT_DIR", "/out")),
            git_sha=_get(e, "EFN_GIT_SHA", "unknown")[:40],
        )


def bundle_keys(env: Mapping[str, str] | None = None) -> Keys:
    e: Mapping[str, str] = os.environ if env is None else env
    secret = read_secret(e, "MIG_BUNDLE_KEY")
    if not secret:
        raise ConfigError("MIG_BUNDLE_KEY_FILE wajib (secret migration_bundle_key)")
    try:
        return derive_keys(secret)
    except ValueError as exc:
        raise ConfigError(str(exc)) from None


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    secure: bool = True
    ca_file: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        secure = _get(e, "CH_SECURE", "true").lower() in _TRUE
        allow_plain = _get(e, "EFN_ALLOW_PLAINTEXT").lower() in _TRUE
        if allow_plain and is_production(e):
            raise ConfigError("EFN_ALLOW_PLAINTEXT=true ditolak di production")
        ca: str | None = None
        if secure:
            ca = _get(e, "CH_CA_FILE", DEFAULT_CA_FILE)
            if not Path(ca).is_file() or not Path(ca).read_bytes().strip():
                raise ConfigError("CH_CA_FILE tidak terbaca/kosong")
        elif not allow_plain:
            raise ConfigError("CH_SECURE wajib true (ClickHouse HTTPS 8443)")
        password = read_secret(e, "CH_PASSWORD")
        if not password:
            raise ConfigError("CH_PASSWORD_FILE wajib (secret clickhouse_password_mig)")
        return cls(
            host=_get(e, "CH_HOST", "clickhouse"),
            port=_int(e, "CH_PORT", 8443 if secure else 8123, 1, 65535),
            user=_get(e, "CH_USER", "efn_mig"),
            password=password,
            secure=secure,
            ca_file=ca,
        )
