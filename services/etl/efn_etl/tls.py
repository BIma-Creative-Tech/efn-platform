"""Kebijakan TLS klien ETL (docs/04_SERVICE_CONTRACTS.md §7.1, ADR-022). Logika murni.

Salinan kecil dari `services/ai-ocr-worker/efn_ocr/tls.py` (tidak ada paket bersama antar-service Python).
- PostgreSQL: `sslmode=verify-full` + CA internal. ClickHouse: HTTPS (8443) + verifikasi CA internal.
  RabbitMQ: `amqps://efn-etl@rabbitmq:5671` + CA internal (publish `privacy.erasure.completed`).
- SEC-27 (fail-closed, selaras BFF/Odoo): `APP_ENV` kosong/tidak dikenal = production. Plaintext hanya dengan
  `EFN_ALLOW_PLAINTEXT=true` DAN `APP_ENV` (atau `NODE_ENV` bila `APP_ENV` kosong) di `NON_PRODUCTION_ENVS`.
"""

import ssl
from collections.abc import Mapping
from pathlib import Path

DEFAULT_CA_FILE = "/run/secrets/efn_ca_crt"
PG_STRICT_SSLMODE = "verify-full"
_TRUE = {"1", "true", "yes", "on"}
NON_PRODUCTION_ENVS = frozenset({"dev", "development", "local", "test", "ci"})


class TlsConfigError(RuntimeError):
    """Konfigurasi TLS tidak aman/tidak lengkap. ETL wajib berhenti (tidak boleh jalan plaintext)."""


def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def is_production(env: Mapping[str, str]) -> bool:
    """Fail closed (SEC-27): production kecuali env yang di-set SEMUANYA ada di allowlist non-produksi."""
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
        raise TlsConfigError(f"{what}: file {path} tidak terbaca") from exc
    if not data.strip():
        raise TlsConfigError(f"{what}: file {path} kosong")
    return str(file)


def ca_file(env: Mapping[str, str], name: str) -> str:
    return require_readable_file(_get(env, name) or DEFAULT_CA_FILE, name)


def client_ssl_context(cafile: str) -> ssl.SSLContext:
    """Konteks klien AMQP: hanya CA internal (bukan trust store sistem), verifikasi hostname, TLS >= 1.2."""
    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    except (OSError, ssl.SSLError) as exc:
        raise TlsConfigError(f"CA {cafile} tidak valid") from exc
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def pg_ssl_settings(
    env: Mapping[str, str],
    allow_plaintext: bool,
    prefix: str = "PG",
    fallback: tuple[str, str | None] | None = None,
) -> tuple[str, str | None]:
    """(`sslmode`, `sslrootcert`) dari `<prefix>_SSLMODE`/`<prefix>_SSLROOTCERT`.

    `fallback` dipakai bila env prefix tidak di-set (mis. ODOO_PG_* ikut PG_*).
    """
    mode = _get(env, f"{prefix}_SSLMODE")
    if not mode and fallback is not None:
        return fallback
    mode = mode or PG_STRICT_SSLMODE
    if mode != PG_STRICT_SSLMODE and not allow_plaintext:
        raise TlsConfigError(f"{prefix}_SSLMODE={mode} ditolak; wajib {PG_STRICT_SSLMODE}")
    if mode in (PG_STRICT_SSLMODE, "verify-ca"):
        root = _get(env, f"{prefix}_SSLROOTCERT") or (fallback[1] if fallback and fallback[1] else "")
        return mode, require_readable_file(root or DEFAULT_CA_FILE, f"{prefix}_SSLROOTCERT")
    return mode, None
