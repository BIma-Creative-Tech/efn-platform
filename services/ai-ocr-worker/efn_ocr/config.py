"""Konfigurasi dari env non-rahasia dan Docker secret (`<NAME>_FILE` -> /run/secrets/*). Lihat README.

Semua koneksi keluar wajib TLS terverifikasi (docs/04 §7.1); validasi dilakukan di `Settings.from_env` sehingga
service gagal start bila TLS tidak terkonfigurasi. Kredensial per service (docs/04 §7.3): PostgreSQL `efn_ocr`,
RabbitMQ `efn-ocr`, MinIO `efn-ocr`.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from efn_ocr.fraud import GeoCircle, parse_geofence
from efn_ocr.tls import (
    TlsConfigError,
    ca_file,
    cert_key_pair,
    is_production,
    optional_file,
    pg_ssl_settings,
    plaintext_allowed,
    server_cert_pair,
)

_TRUE = {"1", "true", "yes", "on"}


def read_secret(name: str, env: Mapping[str, str] | None = None, default: str = "") -> str:
    """Baca secret dari file `<name>_FILE` bila di-set (wajib ada), jika tidak dari env `<name>`."""
    source = os.environ if env is None else env
    path = source.get(f"{name}_FILE")
    if path:
        file = Path(path)
        if not file.is_file():
            raise RuntimeError(f"{name}_FILE di-set tetapi file tidak ditemukan")
        return file.read_text(encoding="utf-8").strip()
    return source.get(name, default)


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return value if value not in (None, "") else default


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    return int(_str(env, name, str(default)))


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    return float(_str(env, name, str(default)))


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    return _str(env, name, "true" if default else "false").strip().lower() in _TRUE


def _percent_score(value: float) -> float:
    """Ambang nama boleh ditulis 0.75 (pecahan, seperti compose lama) atau 75 (skala rapidfuzz)."""
    return value * 100.0 if value <= 1.0 else value


def _minio_endpoint(env: Mapping[str, str]) -> str:
    """`MINIO_ENDPOINT` (host) + `MINIO_PORT`; nilai lama `host:port` tetap diterima."""
    host = _str(env, "MINIO_ENDPOINT", "minio")
    if ":" in host:
        return host
    return f"{host}:{_int(env, 'MINIO_PORT', 9000)}"


# docs/04 §8.12/§8.13 (ADR-030): access key object storage = secret acak (`minio_ocr_access_key`), dibaca dari
# `MINIO_ACCESS_KEY_FILE`. Literal `MINIO_ACCESS_KEY` (atau default `efn-ocr`) hanya untuk APP_ENV=dev, karena
# CVE-2026-41145/40344 di build MinIO lama cukup membutuhkan access key ID yang mudah ditebak.
_DEV_APP_ENVS = frozenset({"dev", "development", "local", "test"})
_ACCESS_KEY_RE = re.compile(r"^[A-Za-z0-9._~+=-]{3,128}$")
_ACCESS_KEY_MIN_LEN = 16


def _minio_access_key(env: Mapping[str, str], default: str) -> str:
    """Access key MinIO: `MINIO_ACCESS_KEY_FILE` (wajib di luar dev) > literal `MINIO_ACCESS_KEY` (dev saja)."""
    dev = _str(env, "APP_ENV", "").strip().lower() in _DEV_APP_ENVS and not is_production(env)
    path = env.get("MINIO_ACCESS_KEY_FILE")
    if path:
        file = Path(path)
        if not file.is_file():
            raise RuntimeError("MINIO_ACCESS_KEY_FILE di-set tetapi file tidak ditemukan")
        value = file.read_text(encoding="utf-8").strip()
    elif dev:
        value = _str(env, "MINIO_ACCESS_KEY", default)
    else:
        raise RuntimeError("MINIO_ACCESS_KEY_FILE wajib di luar APP_ENV=dev (docs/04 §8.13)")
    if not _ACCESS_KEY_RE.fullmatch(value):
        raise RuntimeError("Access key MinIO memuat karakter/panjang tidak valid")
    if not dev and len(value) < _ACCESS_KEY_MIN_LEN:
        raise RuntimeError("Access key MinIO terlalu pendek (< 16) di luar APP_ENV=dev (docs/04 §8.12)")
    return value


@dataclass(frozen=True)
class Settings:
    # RabbitMQ (AMQP_URL tanpa password; password dari secret)
    amqp_url: str = "amqps://efn-ocr@rabbitmq:5671"
    amqp_password: str = field(default="", repr=False)
    amqp_ca_file: str | None = None  # None = plaintext (hanya dev dengan EFN_ALLOW_PLAINTEXT)
    prefetch: int = 2
    max_retries: int = 3
    # PostgreSQL platform (user efn_ocr: SELECT + UPDATE kolom hasil pada platform.price_hunter_report)
    pg_host: str = "postgres"
    pg_port: int = 5432
    pg_user: str = "efn_ocr"
    pg_password: str = field(default="", repr=False)
    pg_database: str = "efn_platform"
    pg_sslmode: str = "verify-full"
    pg_sslrootcert: str | None = None
    # MinIO (service account efn-ocr: GetObject market-scout/reports/*)
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "efn-ocr"
    minio_secret_key: str = field(default="", repr=False)
    minio_secure: bool = True
    minio_ca_file: str | None = None
    minio_bucket: str = "market-scout"
    minio_key_prefix: str = "reports/"
    max_image_bytes: int = 10 * 1024 * 1024
    # OCR tahap 1
    ocr_engine: str = "tesseract"
    tesseract_lang: str = "ind+eng"
    ocr_confidence_threshold: float = 0.80
    # VLM tahap 2 (Ollama di balik sidecar TLS)
    vlm_enabled: bool = True
    ollama_url: str = "https://ollama:11434"
    ollama_ca_file: str | None = None
    # mTLS ke sidecar ghostunnel Ollama (docs/04 §8.5): secret tls_ocr_worker_crt/key; wajib di production.
    ollama_client_cert_file: str | None = None
    ollama_client_key_file: str | None = None
    ollama_model: str = "qwen2.5vl:7b"
    ollama_timeout_seconds: float = 90.0
    # Validasi & anti-fraud
    name_similarity_threshold: float = 75.0
    price_tolerance_pct: float = 2.0
    daily_valid_cap: int = 20
    phash_max_distance: int = 6
    phash_lookback_days: int = 30
    geofence: tuple[GeoCircle, ...] = ()
    points_per_valid_report: int = 10
    stale_processing_seconds: int = 900
    # Observability (HTTPS bila sertifikat tersedia)
    metrics_port: int = 9100
    metrics_cert_file: str | None = None
    metrics_key_file: str | None = None
    # mTLS endpoint metrik (docs/04 §8.5): CA untuk memverifikasi sertifikat klien Prometheus; wajib di production.
    metrics_client_ca_file: str | None = None

    @property
    def amqp_user(self) -> str:
        """Nama user RabbitMQ = nilai properti AMQP `user_id` saat publish (SEC-08)."""
        return urlsplit(self.amqp_url).username or ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        d = cls()
        allow_plain = plaintext_allowed(e)
        production = is_production(e)

        amqp_url = _str(e, "AMQP_URL", _str(e, "RABBITMQ_URL", d.amqp_url))
        scheme = urlsplit(amqp_url).scheme
        if scheme == "amqps":
            amqp_ca: str | None = ca_file(e, "AMQP_TLS_CA_FILE")
        elif scheme == "amqp" and allow_plain:
            amqp_ca = None
        else:
            raise TlsConfigError("AMQP_URL wajib amqps:// (amqp:// hanya dengan EFN_ALLOW_PLAINTEXT)")
        if urlsplit(amqp_url).password:
            raise TlsConfigError("AMQP_URL tidak boleh memuat password; gunakan RABBITMQ_PASSWORD_FILE")

        pg_sslmode, pg_sslrootcert = pg_ssl_settings(e, allow_plain)

        minio_secure = _bool(e, "MINIO_USE_SSL", _bool(e, "MINIO_SECURE", d.minio_secure))
        if minio_secure:
            minio_ca: str | None = ca_file(e, "MINIO_CA_FILE")
        elif allow_plain:
            minio_ca = None
        else:
            raise TlsConfigError("MINIO_USE_SSL wajib true")

        vlm_enabled = _bool(e, "OCR_VLM_ENABLED", d.vlm_enabled)
        ollama_url = _str(e, "OLLAMA_URL", d.ollama_url).rstrip("/")
        ollama_ca: str | None = None
        ollama_client: tuple[str, str] | None = None
        if vlm_enabled:
            ollama_scheme = urlsplit(ollama_url).scheme
            if ollama_scheme == "https":
                ollama_ca = ca_file(e, "OLLAMA_CA_FILE")
                ollama_client = cert_key_pair(e, "OLLAMA_CLIENT_CERT_FILE", "OLLAMA_CLIENT_KEY_FILE")
                if ollama_client is None and production:
                    raise TlsConfigError(
                        "OLLAMA_CLIENT_CERT_FILE/OLLAMA_CLIENT_KEY_FILE wajib di production (mTLS ke sidecar Ollama)"
                    )
            elif not (ollama_scheme == "http" and allow_plain):
                raise TlsConfigError("OLLAMA_URL wajib https:// (sidecar TLS)")

        metrics_pair = server_cert_pair(e, "METRICS_TLS_CERT_FILE", "METRICS_TLS_KEY_FILE")
        if metrics_pair is None and not allow_plain:
            raise TlsConfigError("METRICS_TLS_CERT_FILE/METRICS_TLS_KEY_FILE wajib (endpoint metrik HTTPS)")
        metrics_client_ca = optional_file(e, "METRICS_TLS_CLIENT_CA_FILE")
        if metrics_client_ca and metrics_pair is None:
            raise TlsConfigError("METRICS_TLS_CLIENT_CA_FILE butuh METRICS_TLS_CERT_FILE/METRICS_TLS_KEY_FILE")
        if metrics_client_ca is None and production:
            raise TlsConfigError("METRICS_TLS_CLIENT_CA_FILE wajib di production (mTLS scrape Prometheus)")

        return cls(
            amqp_url=amqp_url,
            amqp_password=read_secret("RABBITMQ_PASSWORD", e),
            amqp_ca_file=amqp_ca,
            prefetch=_int(e, "OCR_PREFETCH", d.prefetch),
            max_retries=_int(e, "OCR_MAX_RETRIES", d.max_retries),
            pg_host=_str(e, "PG_HOST", d.pg_host),
            pg_port=_int(e, "PG_PORT", d.pg_port),
            pg_user=_str(e, "PG_USER", d.pg_user),
            pg_password=read_secret("PG_PASSWORD", e),
            pg_database=_str(e, "PG_DB", _str(e, "PLATFORM_DB", d.pg_database)),
            pg_sslmode=pg_sslmode,
            pg_sslrootcert=pg_sslrootcert,
            minio_endpoint=_minio_endpoint(e),
            minio_access_key=_minio_access_key(e, d.minio_access_key),
            minio_secret_key=read_secret("MINIO_SECRET_KEY", e),
            minio_secure=minio_secure,
            minio_ca_file=minio_ca,
            minio_bucket=_str(e, "MINIO_BUCKET_MARKET_SCOUT", d.minio_bucket),
            max_image_bytes=_int(e, "OCR_MAX_IMAGE_BYTES", d.max_image_bytes),
            ocr_engine=_str(e, "OCR_ENGINE", d.ocr_engine).lower(),
            tesseract_lang=_str(e, "OCR_TESSERACT_LANG", d.tesseract_lang),
            ocr_confidence_threshold=_float(e, "OCR_CONFIDENCE_THRESHOLD", d.ocr_confidence_threshold),
            vlm_enabled=vlm_enabled,
            ollama_url=ollama_url,
            ollama_ca_file=ollama_ca,
            ollama_client_cert_file=ollama_client[0] if ollama_client else None,
            ollama_client_key_file=ollama_client[1] if ollama_client else None,
            ollama_model=_str(e, "OLLAMA_MODEL", d.ollama_model),
            ollama_timeout_seconds=_float(e, "OLLAMA_TIMEOUT_SECONDS", d.ollama_timeout_seconds),
            name_similarity_threshold=_percent_score(
                _float(e, "NAME_SIMILARITY_THRESHOLD", d.name_similarity_threshold),
            ),
            price_tolerance_pct=_float(e, "PRICE_TOLERANCE_PCT", d.price_tolerance_pct),
            daily_valid_cap=_int(e, "PRICE_HUNTER_DAILY_CAP", d.daily_valid_cap),
            phash_max_distance=_int(e, "PHASH_MAX_DISTANCE", d.phash_max_distance),
            phash_lookback_days=_int(e, "PHASH_LOOKBACK_DAYS", d.phash_lookback_days),
            geofence=parse_geofence(_str(e, "GEOFENCE_CIRCLES", "")),
            points_per_valid_report=_int(e, "PRICE_HUNTER_POINTS", d.points_per_valid_report),
            stale_processing_seconds=_int(e, "OCR_STALE_PROCESSING_SECONDS", d.stale_processing_seconds),
            metrics_port=_int(e, "METRICS_PORT", d.metrics_port),
            metrics_cert_file=metrics_pair[0] if metrics_pair else None,
            metrics_key_file=metrics_pair[1] if metrics_pair else None,
            metrics_client_ca_file=metrics_client_ca,
        )
