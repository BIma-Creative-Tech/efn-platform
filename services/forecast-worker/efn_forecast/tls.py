"""Kebijakan TLS klien (docs/04 §7.1, ADR-022). Logika murni; salinan kecil `services/esb-extract/efn_esb/tls.py`
(tidak ada paket bersama antar-service Python).

- ClickHouse: HTTPS 8443 + verifikasi CA internal `/run/secrets/efn_ca_crt`.
- odoo-rpc-gateway: HTTPS + verifikasi CA internal + sertifikat klien CN `forecast-worker` (mTLS).
- SEC-27 (fail-closed): `APP_ENV` kosong/tidak dikenal = production. Plaintext hanya dengan `EFN_ALLOW_PLAINTEXT=true`
  DAN `APP_ENV` di `NON_PRODUCTION_ENVS`.
"""

from collections.abc import Mapping
from pathlib import Path

DEFAULT_CA_FILE = "/run/secrets/efn_ca_crt"
_TRUE = {"1", "true", "yes", "on"}
NON_PRODUCTION_ENVS = frozenset({"dev", "development", "local", "test", "ci"})


class TlsConfigError(RuntimeError):
    """Konfigurasi TLS tidak aman/tidak lengkap. Service wajib berhenti (tidak boleh jalan plaintext)."""


def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def is_production(env: Mapping[str, str]) -> bool:
    values = [_get(env, key).lower() for key in ("APP_ENV", "NODE_ENV") if _get(env, key)]
    return not values or any(value not in NON_PRODUCTION_ENVS for value in values)


def plaintext_allowed(env: Mapping[str, str]) -> bool:
    requested = _get(env, "EFN_ALLOW_PLAINTEXT").lower() in _TRUE
    if requested and is_production(env):
        raise TlsConfigError(
            "EFN_ALLOW_PLAINTEXT=true ditolak: APP_ENV harus salah satu "
            f"{sorted(NON_PRODUCTION_ENVS)} (kosong = production)"
        )
    return requested


def require_readable_file(path: str, what: str) -> str:
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as exc:
        raise TlsConfigError(f"{what}: file tidak terbaca") from exc
    if not data.strip():
        raise TlsConfigError(f"{what}: file kosong")
    return str(file)


def ca_file(env: Mapping[str, str], name: str) -> str:
    return require_readable_file(_get(env, name) or DEFAULT_CA_FILE, name)
