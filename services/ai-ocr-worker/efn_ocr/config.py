"""Konfigurasi dari env non-rahasia dan Docker secret (`<NAME>_FILE` -> /run/secrets/*). Lihat README.

Semua koneksi keluar wajib TLS terverifikasi (docs/04 §7.1); validasi dilakukan di `Settings.from_env` sehingga
service gagal start bila TLS tidak terkonfigurasi. Kredensial per service (docs/04 §7.3): PostgreSQL `efn_ocr`,
RabbitMQ `efn-ocr`, MinIO `efn-ocr`.
"""

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from efn_ocr.fraud import GeoCircle, parse_geofence
from efn_ocr.imaging import estimate_vlm_image_tokens
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
# Token teks prompt VLM + template chat (perkiraan longgar; prompt efn_ocr.vlm.PROMPT ~80 token).
VLM_PROMPT_TOKEN_BUDGET = 200


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
    # G12 performa VLM: gambar diperkecil sebelum dikirim (sisi terpanjang, px), kualitas JPEG, batas token.
    vlm_max_side: int = 1024
    vlm_jpeg_quality: int = 85
    # Trim tepi polos: default mati (G12: 1 dari 3 foto smoke kehilangan sufiks nama; manfaat token nihil karena
    # Ollama qwen2.5vl menormalkan ukuran gambar).
    vlm_trim_border: bool = False
    ollama_num_predict: int = 128
    ollama_num_ctx: int = 2048
    ollama_keep_alive: str = "30m"
    # G37 (K25-01, docs/04 §22): gateway LLM. ollama (default, tanpa egress) | ninerouter | ninerouter_ollama
    # (primer 9router, fallback Ollama). 9router = transfer lintas negara (PDP P6): wajib OCR_LLM_CROSS_BORDER_OK=true.
    llm_provider: str = "ollama"
    ninerouter_url: str = "https://9router.bimacreative.tech"
    ninerouter_api_key: str = field(default="", repr=False)
    ninerouter_model: str = "cc/claude-haiku-4-5-20251001"
    ninerouter_timeout_seconds: float = 45.0
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
    # Alamat bind endpoint metrik. Default: semua interface bila HTTPS (Prometheus scrape lintas container),
    # loopback bila plaintext dev (hanya healthcheck lokal). Wajib literal IP (bukan hostname).
    metrics_bind_address: str = "0.0.0.0"  # noqa: S104 - disengaja (HTTPS+mTLS), lihat _bind_address
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

        vlm_max_side, vlm_quality, num_predict, num_ctx = _vlm_limits(e, d)
        llm_provider, ninerouter_url, ninerouter_key = _llm_gateway(e, d, vlm_enabled)

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
            vlm_max_side=vlm_max_side,
            vlm_jpeg_quality=vlm_quality,
            vlm_trim_border=_bool(e, "OCR_VLM_TRIM_BORDER", d.vlm_trim_border),
            ollama_num_predict=num_predict,
            ollama_num_ctx=num_ctx,
            ollama_keep_alive=_str(e, "OLLAMA_KEEP_ALIVE", d.ollama_keep_alive),
            llm_provider=llm_provider,
            ninerouter_url=ninerouter_url,
            ninerouter_api_key=ninerouter_key,
            ninerouter_model=_str(e, "NINEROUTER_VISION_MODEL", d.ninerouter_model),
            ninerouter_timeout_seconds=_float(e, "NINEROUTER_TIMEOUT_SECONDS", d.ninerouter_timeout_seconds),
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
            metrics_bind_address=_bind_address(e, metrics_pair is not None),
            metrics_cert_file=metrics_pair[0] if metrics_pair else None,
            metrics_key_file=metrics_pair[1] if metrics_pair else None,
            metrics_client_ca_file=metrics_client_ca,
        )


LLM_PROVIDERS = ("ollama", "ninerouter", "ninerouter_ollama")


def _llm_gateway(e: Mapping[str, str], d: "Settings", vlm_enabled: bool) -> tuple[str, str, str]:
    """Validasi gateway LLM G37 (fail-closed saat start).

    * ``OCR_LLM_PROVIDER`` di :data:`LLM_PROVIDERS` (default ``ollama``).
    * Penyedia 9router wajib ``OCR_LLM_CROSS_BORDER_OK=true`` (keputusan DPO transfer lintas negara, PDP P6) dan API key
      di ``NINEROUTER_API_KEY_FILE`` (bukan kosong / ``disabled``); URL wajib host allowlist.
    """
    from efn_ocr.llm import validate_ninerouter_url  # noqa: PLC0415 - llm mengimpor vlm, bukan config

    provider = _str(e, "OCR_LLM_PROVIDER", d.llm_provider).lower()
    if provider not in LLM_PROVIDERS:
        raise ValueError(f"OCR_LLM_PROVIDER harus salah satu {LLM_PROVIDERS}")
    url = _str(e, "NINEROUTER_URL", d.ninerouter_url)
    if not vlm_enabled or provider == "ollama":
        return provider, url, ""
    if not _bool(e, "OCR_LLM_CROSS_BORDER_OK", False):
        raise ValueError(
            "OCR_LLM_PROVIDER 9router mengirim foto ke luar negeri: wajib OCR_LLM_CROSS_BORDER_OK=true "
            "(keputusan DPO, docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md P6)"
        )
    key = read_secret("NINEROUTER_API_KEY", e)
    if not key or key == "disabled":
        raise ValueError("NINEROUTER_API_KEY_FILE wajib berisi API key 9router (bukan kosong/disabled)")
    return provider, validate_ninerouter_url(url), key


def _vlm_limits(e: Mapping[str, str], d: "Settings") -> tuple[int, int, int, int]:
    """Validasi batas VLM (G12). Konteks harus muat token gambar terbesar + prompt + keluaran; jika tidak, Ollama
    memotong konteks diam-diam dan hasil tidak dapat diandalkan -> gagal start."""
    max_side = _int(e, "OCR_VLM_MAX_SIDE", d.vlm_max_side)
    quality = _int(e, "OCR_VLM_JPEG_QUALITY", d.vlm_jpeg_quality)
    num_predict = _int(e, "OLLAMA_NUM_PREDICT", d.ollama_num_predict)
    num_ctx = _int(e, "OLLAMA_NUM_CTX", d.ollama_num_ctx)
    if not 256 <= max_side <= 2048:
        raise ValueError("OCR_VLM_MAX_SIDE harus 256..2048")
    if not 50 <= quality <= 95:
        raise ValueError("OCR_VLM_JPEG_QUALITY harus 50..95")
    if not 32 <= num_predict <= 1024:
        raise ValueError("OLLAMA_NUM_PREDICT harus 32..1024")
    needed = estimate_vlm_image_tokens(max_side, max_side) + VLM_PROMPT_TOKEN_BUDGET + num_predict
    if not needed <= num_ctx <= 32768:
        raise ValueError(f"OLLAMA_NUM_CTX harus >= {needed} untuk OCR_VLM_MAX_SIDE={max_side} (maks 32768)")
    return max_side, quality, num_predict, num_ctx


def _bind_address(e: Mapping[str, str], tls: bool) -> str:
    """`METRICS_BIND_ADDRESS`: literal IPv4/IPv6. Tanpa TLS (dev) default loopback agar /metrics plaintext tidak
    terekspos ke network container lain; non-loopback tanpa TLS ditolak."""
    raw = _str(e, "METRICS_BIND_ADDRESS", "0.0.0.0" if tls else "127.0.0.1").strip()  # noqa: S104
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError("METRICS_BIND_ADDRESS harus alamat IP literal") from exc
    if not tls and not address.is_loopback:
        raise TlsConfigError("METRICS_BIND_ADDRESS non-loopback membutuhkan METRICS_TLS_CERT_FILE/KEY_FILE")
    return str(address)
