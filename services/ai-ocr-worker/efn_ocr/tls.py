"""Kebijakan TLS klien (docs/04_SERVICE_CONTRACTS.md §7.1, ADR-022). Logika murni, bisa diuji tanpa jaringan.

- Semua koneksi keluar memakai TLS terverifikasi terhadap CA internal (default `/run/secrets/efn_ca_crt`),
  hostname = nama service compose.
- SEC-27 (fail-closed, selaras BFF/Odoo): `APP_ENV` kosong/tidak dikenal = production. Konfigurasi tanpa TLS hanya
  diterima bila `EFN_ALLOW_PLAINTEXT=true` DAN `APP_ENV` (atau `NODE_ENV` bila `APP_ENV` kosong) termasuk
  `NON_PRODUCTION_ENVS`; selain itu flag ditolak. Tidak ada fallback diam-diam.
- CA/sertifikat yang tidak terbaca -> `TlsConfigError` saat start (fail closed).
"""

import ssl
from collections.abc import Mapping
from pathlib import Path

DEFAULT_CA_FILE = "/run/secrets/efn_ca_crt"
PG_STRICT_SSLMODE = "verify-full"
_TRUE = {"1", "true", "yes", "on"}
NON_PRODUCTION_ENVS = frozenset({"dev", "development", "local", "test", "ci"})


class TlsConfigError(RuntimeError):
    """Konfigurasi TLS tidak aman/tidak lengkap. Service wajib berhenti (tidak boleh jalan plaintext)."""


def _get(env: Mapping[str, str], name: str) -> str:
    return (env.get(name) or "").strip()


def is_production(env: Mapping[str, str]) -> bool:
    """Fail closed (SEC-27): production kecuali env yang di-set SEMUANYA ada di allowlist non-produksi.

    `APP_ENV` kosong dan `NODE_ENV` kosong -> production. `APP_ENV=development` + `NODE_ENV=production` -> production.
    """
    values = [_get(env, key).lower() for key in ("APP_ENV", "NODE_ENV") if _get(env, key)]
    return not values or any(value not in NON_PRODUCTION_ENVS for value in values)


def plaintext_allowed(env: Mapping[str, str]) -> bool:
    """`EFN_ALLOW_PLAINTEXT=true` hanya untuk APP_ENV non-produksi eksplisit. Selain itu flag menggagalkan start."""
    requested = _get(env, "EFN_ALLOW_PLAINTEXT").lower() in _TRUE
    if requested and is_production(env):
        raise TlsConfigError(
            "EFN_ALLOW_PLAINTEXT=true ditolak: APP_ENV harus salah satu "
            f"{sorted(NON_PRODUCTION_ENVS)} (kosong = production)"
        )
    return requested


def require_readable_file(path: str, what: str) -> str:
    """Pastikan file (CA/sertifikat/kunci) ada, terbaca, dan tidak kosong. Isi tidak pernah di-log."""
    file = Path(path)
    try:
        data = file.read_bytes()
    except OSError as exc:
        raise TlsConfigError(f"{what}: file {path} tidak terbaca") from exc
    if not data.strip():
        raise TlsConfigError(f"{what}: file {path} kosong")
    return str(file)


def ca_file(env: Mapping[str, str], name: str) -> str:
    """Path CA dari env `name` (default CA internal), wajib terbaca."""
    return require_readable_file(_get(env, name) or DEFAULT_CA_FILE, name)


def client_ssl_context(cafile: str) -> ssl.SSLContext:
    """Konteks klien: hanya CA internal (bukan trust store sistem), verifikasi hostname, TLS >= 1.2."""
    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=cafile)
    except (OSError, ssl.SSLError) as exc:
        raise TlsConfigError(f"CA {cafile} tidak valid") from exc
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def pg_ssl_settings(env: Mapping[str, str], allow_plaintext: bool) -> tuple[str, str | None]:
    """(`sslmode`, `sslrootcert`) untuk libpq. Default `verify-full` + CA internal.

    Mode selain `verify-full` (termasuk `disable`/`require`/`verify-ca`) hanya dengan `EFN_ALLOW_PLAINTEXT`.
    """
    mode = _get(env, "PG_SSLMODE") or PG_STRICT_SSLMODE
    if mode != PG_STRICT_SSLMODE and not allow_plaintext:
        raise TlsConfigError(f"PG_SSLMODE={mode} ditolak; wajib {PG_STRICT_SSLMODE}")
    if mode in (PG_STRICT_SSLMODE, "verify-ca"):
        return mode, ca_file(env, "PG_SSLROOTCERT")
    return mode, None


def server_cert_pair(env: Mapping[str, str], cert_name: str, key_name: str) -> tuple[str, str] | None:
    """Pasangan sertifikat+kunci (server metrik, atau klien mTLS ke Ollama). Hanya satu yang di-set -> error."""
    cert = _get(env, cert_name)
    key = _get(env, key_name)
    if not cert and not key:
        return None
    if not cert or not key:
        raise TlsConfigError(f"{cert_name} dan {key_name} wajib di-set bersamaan")
    return require_readable_file(cert, cert_name), require_readable_file(key, key_name)


cert_key_pair = server_cert_pair


def optional_file(env: Mapping[str, str], name: str) -> str | None:
    """File opsional (mis. CA klien metrik): tidak di-set -> None; di-set -> wajib terbaca."""
    path = _get(env, name)
    return require_readable_file(path, name) if path else None
