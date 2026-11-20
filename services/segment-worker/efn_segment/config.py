"""Konfigurasi segment-worker dari env non-rahasia + Docker secret (`<NAME>_FILE`). Pola = efn_forecast/config.py.

Secret tidak pernah masuk repr/log/pesan exception. API key Odoo per PT: `ODOO_API_KEY_CAMPAIGN_<KODE>_FILE` (default
`/run/secrets/odoo_api_key_campaign_<kode>`); isi placeholder (`disabled`, dsb.) ditolak. Sertifikat klien mTLS (CN
`segment-worker`) dipakai untuk BFF dan odoo-rpc-gateway.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from efn_segment.contract import MAX_SUBJECTS_PER_CHUNK
from efn_segment.schedule import parse_cron
from efn_segment.segments import SEGMENT_CODES, SegmentParams
from efn_segment.tls import TlsConfigError, ca_file, plaintext_allowed, require_readable_file

_TRUE = {"1", "true", "yes", "on"}
_COMPANY_RE = re.compile(r"^[A-Z]{2,8}$")
_PLACEHOLDERS = frozenset({"disabled", "changeme", "change-me", "placeholder", "xxx", "todo", "password"})
DEFAULT_CLIENT_CERT = "/run/secrets/tls_segment_worker_crt"
DEFAULT_CLIENT_KEY = "/run/secrets/tls_segment_worker_key"


def read_secret(name: str, env: Mapping[str, str], default: str = "") -> str:
    path = env.get(f"{name}_FILE")
    if path:
        file = Path(path)
        if not file.is_file():
            raise RuntimeError(f"{name}_FILE di-set tetapi file tidak ditemukan")
        return file.read_text(encoding="utf-8").strip()
    return env.get(name, default)


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return value.strip() if value is not None and value.strip() else default


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    return _str(env, name, "true" if default else "false").lower() in _TRUE


def _int(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(_str(env, name, str(default)))
    except ValueError:
        raise RuntimeError(f"{name} harus bilangan bulat") from None
    if not low <= value <= high:
        raise RuntimeError(f"{name} di luar rentang {low}..{high}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(_str(env, name, str(default)))
    except ValueError:
        raise RuntimeError(f"{name} harus angka") from None
    if not low <= value <= high:
        raise RuntimeError(f"{name} di luar rentang {low}..{high}")
    return value


def _list(env: Mapping[str, str], name: str, default: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(p.strip() for p in _str(env, name, default).split(",") if p.strip()))


def company_code(value: str) -> str:
    code = (value or "").strip().upper()
    if not _COMPANY_RE.fullmatch(code):
        raise RuntimeError("kode PT tidak valid (2..8 huruf, docs/04 §12.1)")
    return code


def segment_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Kill switch (default false). Dibaca sebelum konfigurasi lain: mode idle tidak butuh secret."""
    return _bool(os.environ if env is None else env, "SEGMENT_ENABLED", False)


@dataclass(frozen=True)
class Params:
    """Parameter segmen & jadwal (tanpa secret)."""

    segment: SegmentParams = field(default_factory=SegmentParams)
    companies: tuple[str, ...] = ("SMD",)
    min_segment_size: int = 10
    k_min: int = 10
    top_n: int = 10
    chunk_size: int = MAX_SUBJECTS_PER_CHUNK
    daily_cron: str = "15 2 * * *"
    tz: str = "Asia/Jakarta"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        segments = _list(e, "SEGMENT_CODES", ",".join(SEGMENT_CODES))
        if not segments or any(s not in SEGMENT_CODES for s in segments):
            raise RuntimeError(f"SEGMENT_CODES: pilihan {', '.join(SEGMENT_CODES)}")
        companies = tuple(company_code(c) for c in _list(e, "SEGMENT_COMPANIES", "SMD"))
        if not companies:
            raise RuntimeError("SEGMENT_COMPANIES wajib diisi")
        lookback = _int(e, "SEGMENT_LOOKBACK_DAYS", 14, 1, 80)
        wishlist_lookback = _int(e, "SEGMENT_WISHLIST_LOOKBACK_DAYS", 60, 7, 85)
        idle = _int(e, "SEGMENT_WISHLIST_IDLE_DAYS", 7, 1, 60)
        if idle >= wishlist_lookback:
            raise RuntimeError("SEGMENT_WISHLIST_IDLE_DAYS harus < SEGMENT_WISHLIST_LOOKBACK_DAYS")
        cron = _str(e, "SEGMENT_DAILY_CRON", "15 2 * * *")
        parse_cron(cron)  # validasi saat start
        return cls(
            segment=SegmentParams(
                lookback_days=lookback,
                wishlist_lookback_days=wishlist_lookback,
                wishlist_idle_days=idle,
                min_views=_int(e, "SEGMENT_MIN_VIEWS", 2, 1, 100),
                min_view_seconds=_int(e, "SEGMENT_MIN_VIEW_SECONDS", 30, 0, 3600),
                price_margin=_float(e, "SEGMENT_PRICE_MARGIN", 0.05, 0.0, 1.0),
                min_category_purchases=_int(e, "SEGMENT_MIN_CATEGORY_PURCHASES", 3, 1, 50),
                min_no_result=_int(e, "SEGMENT_MIN_NO_RESULT", 1, 1, 50),
                include_device=_bool(e, "SEGMENT_INCLUDE_DEVICE", False),
                segments=segments,
            ),
            companies=companies,
            min_segment_size=_int(e, "SEGMENT_MIN_SIZE", 10, 1, 1_000_000),
            k_min=_int(e, "SEGMENT_K_MIN", 10, 2, 1000),
            top_n=_int(e, "SEGMENT_TOP_PRODUCTS", 10, 0, 50),
            chunk_size=_int(e, "SEGMENT_CHUNK_SIZE", MAX_SUBJECTS_PER_CHUNK, 1, MAX_SUBJECTS_PER_CHUNK),
            daily_cron=cron,
            tz=_str(e, "SEGMENT_TZ", "Asia/Jakarta"),
        )


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str = "clickhouse"
    port: int = 8443
    user: str = "efn_segment_reader"
    password: str = field(default="", repr=False)
    database: str = "efn_segment"
    secure: bool = True
    ca_file: str | None = None
    query_timeout_seconds: int = 300

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        allow_plain = plaintext_allowed(e)
        secure = _bool(e, "CH_SECURE", True)
        if secure:
            ca: str | None = ca_file(e, "CH_CA_FILE")
        elif allow_plain:
            ca = None
        else:
            raise TlsConfigError("CH_SECURE wajib true (ClickHouse HTTPS 8443)")
        password = read_secret("CH_PASSWORD", e)
        if not password:
            raise RuntimeError("CH_PASSWORD_FILE wajib diisi (secret clickhouse_password_segment)")
        return cls(
            host=_str(e, "CH_HOST", "clickhouse"),
            port=_int(e, "CH_PORT", 8443 if secure else 8123, 1, 65535),
            user=_str(e, "CH_USER", "efn_segment_reader"),
            password=password,
            database=_str(e, "CH_DATABASE", "efn_segment"),
            secure=secure,
            ca_file=ca,
            query_timeout_seconds=_int(e, "CH_QUERY_TIMEOUT_SECONDS", 300, 10, 600),
        )


@dataclass(frozen=True)
class Endpoint:
    """URL + TLS klien (CA + mTLS) untuk BFF atau odoo-rpc-gateway."""

    url: str
    ca_file: str | None = None
    client_cert_file: str | None = None
    client_key_file: str | None = None
    timeout_seconds: int = 25
    max_retries: int = 6


def _endpoint(e: Mapping[str, str], prefix: str, default_url: str) -> Endpoint:
    url = _str(e, f"{prefix}_URL", default_url).rstrip("/")
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise RuntimeError(f"{prefix}_URL tidak boleh memuat kredensial/path/query/fragment")
    ca = cert = key = None
    if parts.scheme == "https":
        ca = ca_file(e, f"{prefix}_CA_FILE")
        # BFF internal & odoo-rpc-gateway hanya menerima klien bersertifikat (allow-cn segment-worker) -> mTLS wajib.
        cert_path = _str(e, "SEGMENT_CLIENT_CERT_FILE", DEFAULT_CLIENT_CERT)
        key_path = _str(e, "SEGMENT_CLIENT_KEY_FILE", DEFAULT_CLIENT_KEY)
        cert = require_readable_file(cert_path, "SEGMENT_CLIENT_CERT_FILE")
        key = require_readable_file(key_path, "SEGMENT_CLIENT_KEY_FILE")
    elif not (parts.scheme == "http" and plaintext_allowed(e)):
        raise TlsConfigError(f"{prefix}_URL wajib https:// (http:// hanya dengan EFN_ALLOW_PLAINTEXT di dev)")
    return Endpoint(
        url=url,
        ca_file=ca,
        client_cert_file=cert,
        client_key_file=key,
        timeout_seconds=_int(e, f"{prefix}_TIMEOUT_SECONDS", 25, 5, 120),
        max_retries=_int(e, f"{prefix}_MAX_RETRIES", 6, 0, 10),
    )


def bff_endpoint(env: Mapping[str, str] | None = None) -> Endpoint:
    return _endpoint(os.environ if env is None else env, "SEGMENT_BFF", "https://api-gateway:3000")


@dataclass(frozen=True)
class OdooSettings:
    endpoint: Endpoint
    company: str
    api_key: str = field(repr=False)
    database: str = ""

    @classmethod
    def from_env(cls, company: str, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        code = company_code(company)
        endpoint = _endpoint(e, "ODOO_RPC", "https://odoo-rpc-gateway:8443")
        env_name = f"ODOO_API_KEY_CAMPAIGN_{code}"
        secret_env = {**e}
        secret_env.setdefault(f"{env_name}_FILE", f"/run/secrets/odoo_api_key_campaign_{code.lower()}")
        api_key = read_secret(env_name, secret_env).strip()
        if not api_key or api_key.lower() in _PLACEHOLDERS or api_key.lower().endswith(":disabled"):
            raise RuntimeError(
                f"{env_name}_FILE kosong/placeholder: buat user teknis efn_campaign_{code.lower()} + API key di Odoo "
                "sebelum SEGMENT_ENABLED=true"
            )
        return cls(endpoint=endpoint, company=code, api_key=api_key, database=_str(e, "ODOO_DB", ""))
