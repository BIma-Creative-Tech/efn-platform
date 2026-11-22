"""Konfigurasi forecast-worker dari env non-rahasia + Docker secret (`<NAME>_FILE`). Antarmuka env = overlay
`infra/compose/docker-compose.forecast.yml` (devsecops G44). Pola = services/esb-extract/efn_esb/config.py.

Secret tidak pernah masuk repr/log/pesan exception. API key Odoo per PT: `ODOO_API_KEY_FORECAST_<KODE>_FILE` (default
`/run/secrets/odoo_api_key_forecast_<kode>`); isi placeholder (`disabled`, dsb.) ditolak.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from efn_forecast.calendar import CalendarConfig
from efn_forecast.contract import MAX_ITEMS, MAX_QTY_PER_LINE
from efn_forecast.models import CANDIDATE_MODELS, DEFAULT_ETS_MODEL, DEFAULT_MODELS, ETS_MODEL_RE
from efn_forecast.pipeline import MinMaxConfig, PipelineConfig
from efn_forecast.schedule import parse_cron
from efn_forecast.tls import TlsConfigError, ca_file, is_production, plaintext_allowed, require_readable_file

_TRUE = {"1", "true", "yes", "on"}
_COMPANY_RE = re.compile(r"^[A-Z]{2,8}$")
_PLACEHOLDERS = frozenset({"disabled", "changeme", "change-me", "placeholder", "xxx", "todo", "password"})


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


MAX_COMPANIES = 8


def company_codes(value: str) -> tuple[str, ...]:
    """Daftar PT `SMD,PB` (G46d): satu worker menjalankan batch per PT bergiliran (gateway maxconn 1). Tiap PT punya
    user teknis + API key sendiri (`odoo_api_key_forecast_<pt>`)."""
    codes = tuple(dict.fromkeys(company_code(p) for p in (value or "").split(",") if p.strip()))
    if not 1 <= len(codes) <= MAX_COMPANIES:
        raise RuntimeError(f"daftar PT wajib 1..{MAX_COMPANIES} kode (mis. SMD,PB)")
    return codes


def forecast_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Kill switch §28.10 (default false). Dibaca sebelum konfigurasi lain: mode idle tidak butuh secret."""
    return _bool(os.environ if env is None else env, "FORECAST_ENABLED", False)


@dataclass(frozen=True)
class Params:
    """Parameter model & jadwal (tanpa secret)."""

    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    minmax: MinMaxConfig = field(default_factory=MinMaxConfig)
    history_days: int = 800
    stock_days: int = 400
    ets_model: str = DEFAULT_ETS_MODEL
    chunk_size: int = MAX_ITEMS
    daily_cron: str = "30 23 * * *"
    tz: str = "Asia/Jakarta"
    minmax_weekday: int = 6  # 0=Senin..6=Minggu: batch min/max mingguan setelah batch harian
    dc_codes: tuple[str, ...] = ("SMDDC",)
    warehouse_codes: tuple[str, ...] = ()  # kosong = semua gudang aktif PT
    backtest_enabled: bool = True
    backtest_days: int = 28
    seed_calendar: bool = True
    waste_lookback_days: int = 56
    include_today: bool = True
    # Plafon nilai per baris (= parameter Odoo efn_forecast.max_qty_per_line; baris di atasnya tidak dikirim).
    max_qty_per_line: float = MAX_QTY_PER_LINE
    production: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        candidates = _list(e, "FORECAST_MODELS", ",".join(DEFAULT_MODELS))
        if not candidates or any(c not in CANDIDATE_MODELS for c in candidates):
            raise RuntimeError(f"FORECAST_MODELS: pilihan {', '.join(CANDIDATE_MODELS)}")
        ets_model = _str(e, "FORECAST_ETS_MODEL", DEFAULT_ETS_MODEL).upper()
        if not ETS_MODEL_RE.fullmatch(ets_model):
            raise RuntimeError("FORECAST_ETS_MODEL: 3 huruf galat[AMZ] tren[NAZ] musim[NAMZ], mis. ZNZ atau ZZZ")
        weekend = tuple(sorted({int(x) for x in _list(e, "FORECAST_WEEKEND_DAYS", "5,6")}))
        if not weekend or any(d < 0 or d > 6 for d in weekend):
            raise RuntimeError("FORECAST_WEEKEND_DAYS: daftar 0..6 (0=Senin)")
        try:
            lebaran = tuple(date.fromisoformat(d) for d in _list(e, "FORECAST_LEBARAN_DATES", ""))
        except ValueError:
            raise RuntimeError("FORECAST_LEBARAN_DATES: daftar YYYY-MM-DD") from None
        horizon = _int(e, "FORECAST_HORIZON_DAYS", 28, 7, 60)
        sl = _float(e, "FORECAST_SERVICE_LEVEL", 0.95, 0.5, 0.999)
        sl_min = _float(e, "FORECAST_SERVICE_LEVEL_MIN", 0.85, 0.5, 0.999)
        if sl_min > sl:
            raise RuntimeError("FORECAST_SERVICE_LEVEL_MIN harus <= FORECAST_SERVICE_LEVEL")
        cron = _str(e, "FORECAST_DAILY_CRON", "30 23 * * *")
        parse_cron(cron)  # validasi saat start
        return cls(
            pipeline=PipelineConfig(
                horizon_days=horizon,
                min_history_days=_int(e, "FORECAST_MIN_HISTORY_DAYS", 56, 7, 365),
                intermittent_zero_ratio=_float(e, "FORECAST_INTERMITTENT_ZERO_RATIO", 0.6, 0.1, 0.99),
                candidates=candidates,
                cv_windows=_int(e, "FORECAST_CV_WINDOWS", 4, 0, 12),
                fit_days=_int(e, "FORECAST_FIT_DAYS", 365, 56, 1500),
                shrink_days=_float(e, "FORECAST_FACTOR_SHRINK_DAYS", 5.0, 0.0, 100.0),
                jobs=_int(e, "FORECAST_JOBS", 1, 1, 32),
                inactive_days=_int(e, "FORECAST_INACTIVE_DAYS", 28, 0, 365),
            ),
            calendar=CalendarConfig(
                payday_tail_days=_int(e, "FORECAST_PAYDAY_TAIL_DAYS", 7, 0, 15),
                payday_head_days=_int(e, "FORECAST_PAYDAY_HEAD_DAYS", 7, 0, 15),
                weekend_days=weekend,
                lebaran_overrides=lebaran,
            ),
            minmax=MinMaxConfig(
                service_level=sl,
                service_level_min=sl_min,
                waste_high_ratio=_float(e, "FORECAST_WASTE_HIGH_RATIO", 0.05, 0.0, 1.0),
                lead_days=_int(e, "FORECAST_MINMAX_LEAD_DAYS", 2, 0, 60),
                dc_lead_days=_int(e, "FORECAST_MINMAX_DC_LEAD_DAYS", 7, 0, 120),
                review_days=_int(e, "FORECAST_MINMAX_REVIEW_DAYS", 7, 1, 60),
            ),
            history_days=_int(e, "FORECAST_HISTORY_DAYS", 800, 56, 3000),
            ets_model=ets_model,
            stock_days=_int(e, "FORECAST_STOCK_DAYS", 400, 0, 3000),
            chunk_size=_int(e, "FORECAST_CHUNK_SIZE", MAX_ITEMS, 1, MAX_ITEMS),
            daily_cron=cron,
            tz=_str(e, "FORECAST_TZ", "Asia/Jakarta"),
            minmax_weekday=_int(e, "FORECAST_MINMAX_WEEKDAY", 6, -1, 6),
            # Kode gudang dipakai PERSIS (case-sensitive, sama dengan stock.warehouse.code / ClickHouse).
            dc_codes=_list(e, "FORECAST_DC_CODES", "SMDDC"),
            warehouse_codes=_list(e, "FORECAST_WAREHOUSE_CODES", ""),
            max_qty_per_line=_float(e, "FORECAST_MAX_QTY_PER_LINE", MAX_QTY_PER_LINE, 1.0, 1e9),
            backtest_enabled=_bool(e, "FORECAST_BACKTEST_ENABLED", True),
            backtest_days=_int(e, "FORECAST_BACKTEST_DAYS", 28, 7, 120),
            seed_calendar=_bool(e, "FORECAST_SEED_CALENDAR", True),
            waste_lookback_days=_int(e, "FORECAST_WASTE_LOOKBACK_DAYS", 56, 7, 365),
            include_today=_bool(e, "FORECAST_INCLUDE_TODAY", True),
            production=is_production(e),
        )


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str = "clickhouse"
    port: int = 8443
    user: str = "efn_forecast_reader"
    password: str = field(default="", repr=False)
    database: str = "efn_forecast"
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
            raise RuntimeError("CH_PASSWORD_FILE wajib diisi (secret clickhouse_password_forecast)")
        return cls(
            host=_str(e, "CH_HOST", "clickhouse"),
            port=_int(e, "CH_PORT", 8443 if secure else 8123, 1, 65535),
            user=_str(e, "CH_USER", "efn_forecast_reader"),
            password=password,
            database=_str(e, "CH_DATABASE", "efn_forecast"),
            secure=secure,
            ca_file=ca,
            query_timeout_seconds=_int(e, "CH_QUERY_TIMEOUT_SECONDS", 300, 10, 600),
        )


@dataclass(frozen=True)
class OdooSettings:
    url: str
    company: str
    api_key: str = field(repr=False)
    ca_file: str | None = None
    client_cert_file: str | None = None
    client_key_file: str | None = None
    database: str = ""
    timeout_seconds: int = 25
    max_retries: int = 6

    @classmethod
    def from_env(cls, company: str, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        code = company_code(company)
        url = _str(e, "ODOO_RPC_URL", "https://odoo-rpc-gateway:8443").rstrip("/")
        parts = urlsplit(url)
        if parts.username or parts.password or parts.query or parts.fragment:
            raise RuntimeError("ODOO_RPC_URL tidak boleh memuat kredensial/query/fragment")
        ca = cert = key = None
        if parts.scheme == "https":
            ca = ca_file(e, "ODOO_RPC_CA_FILE")
            # odoo-rpc-gateway hanya menerima klien bersertifikat (allow-cn forecast-worker) -> mTLS wajib.
            cert_path = _str(e, "ODOO_RPC_CLIENT_CERT_FILE", "/run/secrets/tls_forecast_worker_crt")
            key_path = _str(e, "ODOO_RPC_CLIENT_KEY_FILE", "/run/secrets/tls_forecast_worker_key")
            cert = require_readable_file(cert_path, "ODOO_RPC_CLIENT_CERT_FILE")
            key = require_readable_file(key_path, "ODOO_RPC_CLIENT_KEY_FILE")
        elif not (parts.scheme == "http" and plaintext_allowed(e)):
            raise TlsConfigError("ODOO_RPC_URL wajib https:// (http:// hanya dengan EFN_ALLOW_PLAINTEXT di dev)")
        env_name = f"ODOO_API_KEY_FORECAST_{code}"
        secret_env = {**e}
        secret_env.setdefault(f"{env_name}_FILE", f"/run/secrets/odoo_api_key_forecast_{code.lower()}")
        api_key = read_secret(env_name, secret_env).strip()
        if not api_key or api_key.lower() in _PLACEHOLDERS or api_key.lower().endswith(":disabled"):
            raise RuntimeError(
                f"{env_name}_FILE kosong/placeholder: buat user teknis efn_forecast_{code.lower()} + API key di Odoo "
                "(runbook forecast-worker) sebelum FORECAST_ENABLED=true"
            )
        return cls(
            url=url,
            company=code,
            api_key=api_key,
            ca_file=ca,
            client_cert_file=cert,
            client_key_file=key,
            database=_str(e, "ODOO_DB", ""),
            timeout_seconds=_int(e, "ODOO_RPC_TIMEOUT_SECONDS", 25, 5, 120),
            max_retries=_int(e, "ODOO_RPC_MAX_RETRIES", 6, 0, 10),
        )
