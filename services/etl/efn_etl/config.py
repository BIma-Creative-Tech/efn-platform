"""Konfigurasi ETL dari env non-rahasia dan Docker secret (`<NAME>_FILE`). Lihat README.

docs/04 §7: semua koneksi TLS terverifikasi (PostgreSQL verify-full, ClickHouse HTTPS 8443 + CA internal);
validasi di `Settings.from_env` sehingga ETL gagal start bila TLS tidak terkonfigurasi. User PostgreSQL `efn_etl`
(read-only, grant level kolom). ETL TIDAK lagi membaca `otp_pepper` (SEC-16): hash sudah dihitung BFF.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from efn_etl.sink import load_key
from efn_etl.tls import TlsConfigError, ca_file, pg_ssl_settings, plaintext_allowed, require_readable_file

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


def _int_range(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    value = _int(env, name, default)
    if not low <= value <= high:
        raise RuntimeError(f"{name} di luar rentang {low}..{high}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    return _str(env, name, "true" if default else "false").strip().lower() in _TRUE


def _datetime(env: Mapping[str, str], name: str) -> datetime | None:
    """ISO 8601; tanpa zona = UTC. Kosong = None."""
    raw = _str(env, name, "")
    if not raw:
        return None
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class PgTarget:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    dbname: str
    sslmode: str = "verify-full"
    sslrootcert: str | None = None


@dataclass(frozen=True)
class Settings:
    platform_pg: PgTarget
    odoo_pg: PgTarget
    odoo_enabled: bool = False
    ch_host: str = "clickhouse"
    ch_port: int = 8443
    ch_user: str = "efn_etl"
    ch_password: str = field(default="", repr=False)
    ch_database: str = "efn_dwh"
    ch_secure: bool = True
    ch_ca_file: str | None = None
    interval_seconds: int = 900
    overlap_seconds: int = 300
    batch_size: int = 5000
    max_pages_per_run: int = 200
    statement_timeout_ms: int = 300_000
    run_once: bool = False
    # Erasure ClickHouse (docs/04 §8.4): baca platform.dsar_request, hapus baris mart, publish
    # `privacy.erasure.completed` sebagai user RabbitMQ `efn-etl` (secret rabbitmq_password_etl).
    erasure_enabled: bool = True
    erasure_batch_size: int = 100
    erasure_mutation_timeout_seconds: int = 600
    amqp_url: str = "amqps://efn-etl@rabbitmq:5671"
    amqp_password: str = field(default="", repr=False)
    amqp_ca_file: str | None = None  # None = plaintext (hanya dev dengan EFN_ALLOW_PLAINTEXT)
    # Gelombang 12: mart GL (F-19) dan salinan audit tamper-evident. Default mati sampai grant Odoo diterapkan
    # (sql/odoo-readonly-grants.sql); keduanya membaca DB Odoo (ODOO_PG_*).
    gl_enabled: bool = False
    audit_copy_enabled: bool = False
    audit_settle_seconds: int = 600
    audit_late_window_seconds: int = 86_400
    audit_batch_size: int = 2000
    audit_recon_interval_seconds: int = 86_400
    audit_recon_content: bool = True
    # Gelombang 13 (Fase 1): snapshot stok & kedaluwarsa (efn_etl/stock.py). Default = ETL_ENABLE_ODOO (grant sama).
    stock_enabled: bool = False
    stock_snapshot_interval_seconds: int = 3600
    # default_code produk "Poin EFN" (docs/04 §9.3) -> line_kind=points di fact_sales_line.
    points_product_codes: frozenset[str] = frozenset({"EFN-POINTS"})
    # Gelombang 14 (SEC-51/52): verifikasi sink audit Odoo + jangkar rantai lewat Loki (mTLS CN etl -> loki-tls).
    # Default aktif bila salinan audit aktif DAN LOKI_URL di-set; kunci/sertifikat wajib ada (gagal start bila tidak).
    audit_sink_enabled: bool = False
    loki_url: str = ""
    loki_ca_file: str | None = None
    loki_client_cert_file: str | None = None
    loki_client_key_file: str | None = None
    audit_sink_key: bytes = field(default=b"", repr=False)
    audit_sink_window_seconds: int = 90_000
    audit_anchor_lookback_seconds: int = 691_200
    # Gelombang 14 (sisa SEC-61): rekonsiliasi ledger poin BFF <-> pos.order Odoo (efn_etl/loyalty_recon.py).
    loyalty_recon_enabled: bool = False
    loyalty_recon_interval_seconds: int = 86_400
    loyalty_recon_lookback_hours: int = 72
    loyalty_recon_settle_hours: int = 6
    loyalty_recon_aged_hours: int = 24
    loyalty_recon_open_days: int = 30
    loyalty_recon_not_before: datetime | None = None
    # G18/W-19 (PDP v7 Q-P7/Q-P8): umur kolom pribadi saat dimuat (lapisan kedua TTL kolom ClickHouse). Env SAMA
    # dengan service clickhouse-init agar kedua lapisan konsisten.
    pseudonym_retention_years: int = 5
    geo_retention_days: int = 90

    @property
    def amqp_user(self) -> str:
        """Nama user RabbitMQ = properti AMQP `user_id` saat publish (SEC-08)."""
        return urlsplit(self.amqp_url).username or ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        allow_plain = plaintext_allowed(e)
        pg_host = _str(e, "PG_HOST", "postgres")
        pg_port = _int(e, "PG_PORT", 5432)
        pg_user = _str(e, "PG_USER", "efn_etl")
        pg_password = read_secret("PG_PASSWORD", e)
        pg_ssl = pg_ssl_settings(e, allow_plain)
        platform = PgTarget(
            host=pg_host,
            port=pg_port,
            user=pg_user,
            password=pg_password,
            dbname=_str(e, "PG_DB", _str(e, "PLATFORM_DB", "efn_platform")),
            sslmode=pg_ssl[0],
            sslrootcert=pg_ssl[1],
        )
        # Odoo dibaca dari read replica bila tersedia (R-O03); default ke host & TLS yang sama.
        odoo_ssl = pg_ssl_settings(e, allow_plain, prefix="ODOO_PG", fallback=pg_ssl)
        odoo = PgTarget(
            host=_str(e, "ODOO_PG_HOST", pg_host),
            port=_int(e, "ODOO_PG_PORT", pg_port),
            user=_str(e, "ODOO_PG_USER", pg_user),
            password=read_secret("ODOO_PG_PASSWORD", e) or pg_password,
            dbname=_str(e, "ODOO_DB", "efn_odoo"),
            sslmode=odoo_ssl[0],
            sslrootcert=odoo_ssl[1],
        )
        ch_secure = _bool(e, "CH_SECURE", True)
        if ch_secure:
            ch_ca: str | None = ca_file(e, "CH_CA_FILE")
        elif allow_plain:
            ch_ca = None
        else:
            raise TlsConfigError("CH_SECURE wajib true (ClickHouse HTTPS 8443)")

        erasure_enabled = _bool(e, "ETL_ERASURE_ENABLED", True)
        amqp_url = _str(e, "AMQP_URL", cls.amqp_url)
        amqp_ca: str | None = None
        if erasure_enabled:
            parts = urlsplit(amqp_url)
            if parts.scheme == "amqps":
                amqp_ca = ca_file(e, "AMQP_TLS_CA_FILE")
            elif not (parts.scheme == "amqp" and allow_plain):
                raise TlsConfigError("AMQP_URL wajib amqps:// (amqp:// hanya dengan EFN_ALLOW_PLAINTEXT)")
            if parts.password:
                raise TlsConfigError("AMQP_URL tidak boleh memuat password; gunakan RABBITMQ_PASSWORD_FILE")
            if not parts.username:
                raise TlsConfigError("AMQP_URL wajib memuat user (efn-etl)")
        odoo_enabled = _bool(e, "ETL_ENABLE_ODOO", False)
        audit_copy_enabled = _bool(e, "ETL_AUDIT_COPY_ENABLED", False)
        loki_url = _str(e, "LOKI_URL", "")
        sink_enabled = _bool(e, "ETL_AUDIT_SINK_ENABLED", audit_copy_enabled and bool(loki_url))
        loki_ca: str | None = None
        loki_cert: str | None = None
        loki_key: str | None = None
        sink_key = b""
        if sink_enabled:
            if not audit_copy_enabled:
                raise RuntimeError("ETL_AUDIT_SINK_ENABLED membutuhkan ETL_AUDIT_COPY_ENABLED=true")
            scheme = urlsplit(loki_url).scheme
            if scheme == "https":
                loki_ca = ca_file(e, "LOKI_CA_FILE")
                # loki-tls hanya menerima klien bersertifikat (allowlist CN) -> mTLS wajib.
                loki_cert = require_readable_file(_str(e, "LOKI_CLIENT_CERT_FILE", ""), "LOKI_CLIENT_CERT_FILE")
                loki_key = require_readable_file(_str(e, "LOKI_CLIENT_KEY_FILE", ""), "LOKI_CLIENT_KEY_FILE")
            elif not (scheme == "http" and allow_plain):
                raise TlsConfigError("LOKI_URL wajib https:// (http:// hanya dengan EFN_ALLOW_PLAINTEXT)")
            sink_key = load_key(read_secret("EFN_AUDIT_SINK_KEY", e).encode("utf-8"))
        points_codes = frozenset(
            code.strip() for code in _str(e, "ETL_POINTS_PRODUCT_CODES", "EFN-POINTS").split(",") if code.strip()
        )
        return cls(
            platform_pg=platform,
            odoo_pg=odoo,
            odoo_enabled=odoo_enabled,
            ch_host=_str(e, "CH_HOST", "clickhouse"),
            ch_port=_int(e, "CH_PORT", 8443 if ch_secure else 8123),
            ch_user=_str(e, "CH_USER", "efn_etl"),
            ch_password=read_secret("CH_PASSWORD", e),
            ch_database=_str(e, "CH_DATABASE", "efn_dwh"),
            ch_secure=ch_secure,
            ch_ca_file=ch_ca,
            interval_seconds=_int(e, "ETL_INTERVAL_SECONDS", 900),
            overlap_seconds=_int(e, "ETL_OVERLAP_SECONDS", 300),
            batch_size=_int(e, "ETL_BATCH_SIZE", 5000),
            max_pages_per_run=_int(e, "ETL_MAX_PAGES_PER_RUN", 200),
            statement_timeout_ms=_int(e, "ETL_STATEMENT_TIMEOUT_MS", 300_000),
            run_once=_bool(e, "ETL_RUN_ONCE", False),
            erasure_enabled=erasure_enabled,
            erasure_batch_size=_int(e, "ETL_ERASURE_BATCH_SIZE", 100),
            erasure_mutation_timeout_seconds=_int(e, "ETL_ERASURE_MUTATION_TIMEOUT_SECONDS", 600),
            amqp_url=amqp_url,
            amqp_password=read_secret("RABBITMQ_PASSWORD", e) if erasure_enabled else "",
            amqp_ca_file=amqp_ca,
            gl_enabled=_bool(e, "ETL_ENABLE_GL", False),
            audit_copy_enabled=audit_copy_enabled,
            audit_settle_seconds=_int(e, "ETL_AUDIT_SETTLE_SECONDS", 600),
            audit_late_window_seconds=_int(e, "ETL_AUDIT_LATE_WINDOW_SECONDS", 86_400),
            audit_batch_size=_int(e, "ETL_AUDIT_BATCH_SIZE", 2000),
            audit_recon_interval_seconds=_int(e, "ETL_AUDIT_RECON_INTERVAL_SECONDS", 86_400),
            audit_recon_content=_bool(e, "ETL_AUDIT_RECON_CONTENT", True),
            stock_enabled=_bool(e, "ETL_ENABLE_STOCK", odoo_enabled),
            stock_snapshot_interval_seconds=_int(e, "ETL_STOCK_SNAPSHOT_INTERVAL_SECONDS", 3600),
            points_product_codes=points_codes,
            audit_sink_enabled=sink_enabled,
            loki_url=loki_url,
            loki_ca_file=loki_ca,
            loki_client_cert_file=loki_cert,
            loki_client_key_file=loki_key,
            audit_sink_key=sink_key,
            audit_sink_window_seconds=_int(e, "ETL_AUDIT_SINK_WINDOW_SECONDS", 90_000),
            audit_anchor_lookback_seconds=_int(e, "ETL_AUDIT_ANCHOR_LOOKBACK_SECONDS", 691_200),
            loyalty_recon_enabled=_bool(e, "ETL_LOYALTY_RECON_ENABLED", odoo_enabled),
            loyalty_recon_interval_seconds=_int(e, "ETL_LOYALTY_RECON_INTERVAL_SECONDS", 86_400),
            loyalty_recon_lookback_hours=_int(e, "ETL_LOYALTY_RECON_LOOKBACK_HOURS", 72),
            loyalty_recon_settle_hours=_int(e, "ETL_LOYALTY_RECON_SETTLE_HOURS", 6),
            loyalty_recon_aged_hours=_int(e, "ETL_LOYALTY_RECON_AGED_HOURS", 24),
            loyalty_recon_open_days=_int(e, "ETL_LOYALTY_RECON_OPEN_DAYS", 30),
            loyalty_recon_not_before=_datetime(e, "ETL_LOYALTY_RECON_NOT_BEFORE"),
            pseudonym_retention_years=_int_range(e, "CH_PSEUDONYM_RETENTION_YEARS", 5, 1, 30),
            geo_retention_days=_int_range(e, "CH_GEO_RETENTION_DAYS", 90, 1, 3650),
        )
