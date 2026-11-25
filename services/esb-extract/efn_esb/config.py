"""Konfigurasi esb-extract dari env non-rahasia dan Docker secret (`<NAME>_FILE`). Kontrak: docs/04 §25.2.

Pola sama dengan `services/etl/efn_etl/config.py` (read_secret/_bool/validasi fail-closed). Password ESB per brand hanya
dari `/run/secrets/esb_core_password_<brand>`; tidak pernah masuk repr/log/pesan exception.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from efn_esb.http import DEFAULT_MAX_RESPONSE_BYTES, MAX_MAX_RESPONSE_BYTES, MIN_MAX_RESPONSE_BYTES
from efn_esb.tls import TlsConfigError, ca_file, is_production, plaintext_allowed

_TRUE = {"1", "true", "yes", "on"}
ESB_HOST = "services.esb.co.id"
DEFAULT_BASE_URL = f"https://{ESB_HOST}/core"
KNOWN_BRANDS = ("pb", "wtz", "bacha", "imd", "cup")
DEFAULT_COMPANY = {"pb": "PB", "wtz": "WTZ", "bacha": "BCF", "imd": "IMD", "cup": "CUP"}
# Kode PT docs/04 §12.1 + IMD/CUP (dicadangkan G43, L-108).
ALLOWED_COMPANY_CODES = frozenset({"EFN", "GL", "PB", "BCR", "BCF", "WTZ", "FARM", "IMD", "CUP"})
ENTITIES = (
    "branch", "master", "sales", "fin_sold", "payment", "production", "delivery", "transfer", "stock", "purchase_line",
    "profit_loss", "item_journal", "transfer_request", "delivery_ref",
)  # fmt: skip
# Entitas harian (punya tanggal). `branch` & `master` = snapshot per hari jalan.
# G68 (docs/04 §32.1): `item_journal`, `transfer_request` = harian per brand (opt-in). `profit_loss` BUKAN harian
# generik (slice = cabang x TAHUN, planning.pl_*_tasks); `delivery_ref` = turunan `delivery` (tanpa slice sendiri).
DAILY_ENTITIES = (
    "sales", "fin_sold", "payment", "production", "delivery", "transfer", "stock", "purchase_line", "item_journal",
    "transfer_request",
)  # fmt: skip
PL_ENTITY = "profit_loss"
DELIVERY_REF_ENTITY = "delivery_ref"
# Entitas yang diterima `backfill --entity` (harian + P&L + turunan delivery_ref).
BACKFILL_ENTITIES = (*DAILY_ENTITIES, PL_ENTITY, DELIVERY_REF_ENTITY)
# Mode `retransform` (tanpa API, dari raw_doc): hanya delivery_ref di G68 (§32.3).
RETRANSFORM_ENTITIES = (DELIVERY_REF_ENTITY,)
DEFAULT_PL_REFRESH_MONTHS = 2
# `stock` bersyarat (§25.4): default tidak aktif sampai parameter terbukti di production.
# `purchase_line` (G65, docs/04 §27.13.1): opt-in lewat ESB_ENTITIES / `backfill --entity`, default MATI.
DEFAULT_ENTITIES = ("branch", "master", "sales", "fin_sold", "payment", "production", "delivery", "transfer")
BRANCH_SCOPED = frozenset({"sales", "fin_sold", "stock"})
# Entitas `snapshot` yang satu slice per cabang (semua cabang dim_esb_branch), bukan per brand (G65 §27.13.1).
SNAPSHOT_BRANCH_SCOPED = frozenset({"stock_opening"})
# docs/04 §27.11 (G43): CK per brand hasil prototipe migrasi. Bacha tanpa CK (Q-ESB8).
DEFAULT_CK_BRANCHES = "pb:4|38,wtz:5|6,imd:2,cup:4|6"
_BRAND_RE = re.compile(r"^[a-z0-9]{2,16}$")
_CODE_RE = re.compile(r"^[A-Z0-9]{2,8}$")
_DB_RE = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")
# `disabled` = isi secret `host:disabled` dari scripts/secrets.list (belum diisi operator) -> brand gagal start.
_PLACEHOLDERS = frozenset({"disabled", "changeme", "change-me", "placeholder", "xxx", "todo", "password"})


def _items(text: str) -> list[str]:
    """Pemisah daftar `,` atau `;` (overlay compose memakai keduanya di komentar)."""
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]


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
    return value.strip() if value not in (None, "") and value.strip() else default


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    return _str(env, name, "true" if default else "false").lower() in _TRUE


def _int_range(env: Mapping[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = _str(env, name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} harus bilangan bulat") from None
    if not low <= value <= high:
        raise RuntimeError(f"{name} di luar rentang {low}..{high}")
    return value


def _float_range(env: Mapping[str, str], name: str, default: float, low: float, high: float) -> float:
    raw = _str(env, name, str(default))
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{name} harus angka") from None
    if not low <= value <= high:
        raise RuntimeError(f"{name} di luar rentang {low}..{high}")
    return value


def esb_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Kill switch (L-115). Dibaca sebelum konfigurasi lain agar mode idle tidak butuh secret apa pun."""
    return _bool(os.environ if env is None else env, "ESB_ENABLED", False)


def validate_base_url(url: str) -> str:
    """§25.2: wajib https, host persis services.esb.co.id, port 443, tanpa kredensial/query/fragment."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise TlsConfigError("ESB_BASE_URL wajib https://")
    if parts.username or parts.password:
        raise RuntimeError("ESB_BASE_URL tidak boleh memuat kredensial")
    if (parts.hostname or "").lower() != ESB_HOST:
        raise RuntimeError(f"ESB_BASE_URL wajib host {ESB_HOST}")
    if parts.port not in (None, 443):
        raise RuntimeError("ESB_BASE_URL wajib port 443")
    if parts.query or parts.fragment:
        raise RuntimeError("ESB_BASE_URL tidak boleh memuat query/fragment")
    return f"https://{ESB_HOST}{parts.path.rstrip('/')}"


def parse_branch_sets(text: str, env_name: str) -> dict[str, frozenset[int]]:
    """`pb:4|38,wtz:7` -> {"pb": {4, 38}, "wtz": {7}}."""
    out: dict[str, set[int]] = {}
    for item in _items(text):
        brand, sep, ids = item.partition(":")
        brand = brand.strip().lower()
        if not sep or not _BRAND_RE.fullmatch(brand):
            raise RuntimeError(f"{env_name}: format wajib brand:id|id")
        for raw in ids.split("|"):
            raw = raw.strip()
            if not raw.isdigit() or int(raw) <= 0:
                raise RuntimeError(f"{env_name}: branchID harus bilangan bulat positif")
            out.setdefault(brand, set()).add(int(raw))
    return {brand: frozenset(ids) for brand, ids in out.items()}


def parse_company_overrides(text: str) -> dict[str, dict[int, str]]:
    """`bacha:12=BCR,bacha:15=BCR` -> {"bacha": {12: "BCR", 15: "BCR"}} (L-108)."""
    out: dict[str, dict[int, str]] = {}
    for item in _items(text):
        left, sep, code = item.partition("=")
        brand, sep2, branch = left.partition(":")
        brand = brand.strip().lower()
        branch = branch.strip()
        code = code.strip().upper()
        if not (sep and sep2 and _BRAND_RE.fullmatch(brand) and branch.isdigit() and int(branch) > 0):
            raise RuntimeError("ESB_BRANCH_COMPANY_OVERRIDES: format wajib brand:branchID=KODE")
        if code not in ALLOWED_COMPANY_CODES:
            raise RuntimeError("ESB_BRANCH_COMPANY_OVERRIDES: kode PT tidak dikenal")
        out.setdefault(brand, {})[int(branch)] = code
    return out


def parse_window(text: str) -> tuple[time, time]:
    """`22:00-05:00` -> (22:00, 05:00). Jendela boleh melewati tengah malam."""
    start, sep, end = text.partition("-")
    if not sep:
        raise RuntimeError("ESB_BACKFILL_WINDOW: format wajib HH:MM-HH:MM")
    try:
        parsed = (time.fromisoformat(start.strip()), time.fromisoformat(end.strip()))
    except ValueError:
        raise RuntimeError("ESB_BACKFILL_WINDOW: format wajib HH:MM-HH:MM") from None
    if parsed[0] == parsed[1]:
        raise RuntimeError("ESB_BACKFILL_WINDOW: awal dan akhir tidak boleh sama")
    return parsed


def parse_entities(text: str) -> tuple[str, ...]:
    items = tuple(dict.fromkeys(part.strip().lower() for part in text.split(",") if part.strip()))
    unknown = [item for item in items if item not in ENTITIES]
    if unknown or not items:
        raise RuntimeError(f"ESB_ENTITIES: entitas tidak dikenal; pilihan {', '.join(ENTITIES)}")
    return items


def parse_pl_refresh_months(raw: str | None) -> int:
    """`ESB_PL_REFRESH_MONTHS` (docs/04 §32.2): 1..12; kosong, bukan bilangan bulat, atau di luar rentang -> 2
    (kontrak: di luar -> default, bukan gagal start)."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_PL_REFRESH_MONTHS
    return value if 1 <= value <= 12 else DEFAULT_PL_REFRESH_MONTHS


def _parse_days(text: str) -> tuple[int, ...]:
    try:
        days = tuple(sorted({int(part) for part in text.split(",") if part.strip()}))
    except ValueError:
        raise RuntimeError("ESB_CORRECTION_DAYS harus daftar bilangan bulat") from None
    if not days or any(d < 1 or d > 31 for d in days):
        raise RuntimeError("ESB_CORRECTION_DAYS di luar rentang 1..31")
    return days


@dataclass(frozen=True)
class BrandConfig:
    key: str
    user: str
    password: str = field(repr=False)
    company_code: str = ""
    ck_branches: frozenset[int] = frozenset()
    non_store_branches: frozenset[int] = frozenset()
    branch_company: Mapping[int, str] = field(default_factory=dict)

    def company_for(self, branch_id: int) -> str:
        return self.branch_company.get(branch_id, self.company_code)

    @property
    def multi_company(self) -> bool:
        """Brand dengan cabang lebih dari satu PT (override `ESB_BRANCH_COMPANY_OVERRIDES` ke PT lain)."""
        return any(code != self.company_code for code in self.branch_company.values())


@dataclass(frozen=True)
class EsbSettings:
    enabled: bool
    base_url: str = DEFAULT_BASE_URL
    brands: tuple[BrandConfig, ...] = ()
    rate_per_sec: float = 2.0
    workers: int = 4
    max_retries: int = 5
    max_calls_per_run: int = 20_000
    backfill_max_calls: int = 60_000
    timeout_seconds: int = 60
    max_pages: int = 500
    interval_seconds: int = 3600
    sales_ready_hour: int = 3
    correction_days: tuple[int, ...] = (1, 7)
    gap_fill_days: int = 7
    backfill_window: tuple[time, time] = (time(22, 0), time(5, 0))
    backfill_include_inactive: bool = True
    entities: tuple[str, ...] = DEFAULT_ENTITIES
    # G68: jendela refresh harian P&L (bulan berjalan + N-1 bulan sebelumnya; L-274).
    pl_refresh_months: int = DEFAULT_PL_REFRESH_MONTHS
    run_once: bool = False
    # SEC-187: batas body respons ESB (byte).
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    # SEC-185: `backfill --ignore-window` di production hanya dengan izin eksplisit + rentang 1 hari.
    allow_ignore_window: bool = False
    # Fail closed (SEC-27): APP_ENV kosong/tidak dikenal = production.
    production: bool = True
    # tmpfs container (read_only rootfs).
    heartbeat_file: str = "/tmp/efn-esb.heartbeat"  # noqa: S108

    def brand(self, key: str) -> BrandConfig:
        for cfg in self.brands:
            if cfg.key == key:
                return cfg
        raise RuntimeError(f"brand {key!r} tidak ada di ESB_BRANDS")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        base_url = validate_base_url(_str(e, "ESB_BASE_URL", DEFAULT_BASE_URL))
        ck = parse_branch_sets(_str(e, "ESB_CK_BRANCHES", DEFAULT_CK_BRANCHES), "ESB_CK_BRANCHES")
        non_store = parse_branch_sets(_str(e, "ESB_NON_STORE_BRANCHES", ""), "ESB_NON_STORE_BRANCHES")
        overrides = parse_company_overrides(_str(e, "ESB_BRANCH_COMPANY_OVERRIDES", ""))
        keys = tuple(dict.fromkeys(k.strip().lower() for k in _str(e, "ESB_BRANDS", "").split(",") if k.strip()))
        brands: list[BrandConfig] = []
        for key in keys:
            if key not in KNOWN_BRANDS:
                raise RuntimeError(f"ESB_BRANDS: brand tidak dikenal; pilihan {', '.join(KNOWN_BRANDS)}")
            upper = key.upper()
            user = _str(e, f"ESB_USER_{upper}", "")
            password = read_secret(f"ESB_PASSWORD_{upper}", e).strip()
            if not user:
                raise RuntimeError(f"ESB_USER_{upper} wajib diisi untuk brand aktif")
            if not password or password.lower() in _PLACEHOLDERS:
                raise RuntimeError(
                    f"ESB_PASSWORD_{upper}_FILE kosong/placeholder/'disabled': isi secret esb_core_password_{key} "
                    f"atau keluarkan {key} dari ESB_BRANDS (brand {key} gagal start)"
                )
            company = _str(e, f"ESB_COMPANY_{upper}", DEFAULT_COMPANY[key]).upper()
            if company not in ALLOWED_COMPANY_CODES:
                raise RuntimeError(f"ESB_COMPANY_{upper}: kode PT tidak dikenal")
            brands.append(
                BrandConfig(
                    key=key,
                    user=user,
                    password=password,
                    company_code=company,
                    ck_branches=ck.get(key, frozenset()),
                    non_store_branches=non_store.get(key, frozenset()),
                    branch_company=overrides.get(key, {}),
                )
            )
        enabled = esb_enabled(e)
        if enabled and not brands:
            raise RuntimeError("ESB_ENABLED=true tetapi ESB_BRANDS kosong")
        return cls(
            enabled=enabled,
            base_url=base_url,
            brands=tuple(brands),
            rate_per_sec=_float_range(e, "ESB_RATE_PER_SEC", 2.0, 0.1, 10.0),
            workers=_int_range(e, "ESB_WORKERS", 4, 1, 16),
            max_retries=_int_range(e, "ESB_MAX_RETRIES", 5, 0, 5),
            max_calls_per_run=_int_range(e, "ESB_MAX_CALLS_PER_RUN", 20_000, 1, 1_000_000),
            backfill_max_calls=_int_range(e, "ESB_BACKFILL_MAX_CALLS", 60_000, 1, 1_000_000),
            timeout_seconds=_int_range(e, "ESB_HTTP_TIMEOUT_SECONDS", 60, 5, 300),
            max_pages=_int_range(e, "ESB_MAX_PAGES", 500, 1, 5000),
            interval_seconds=_int_range(e, "ESB_INTERVAL_SECONDS", 3600, 60, 86_400),
            sales_ready_hour=_int_range(e, "ESB_SALES_READY_HOUR_WIB", 3, 0, 23),
            correction_days=_parse_days(_str(e, "ESB_CORRECTION_DAYS", "1,7")),
            gap_fill_days=_int_range(e, "ESB_GAP_FILL_DAYS", 7, 1, 31),
            backfill_window=parse_window(_str(e, "ESB_BACKFILL_WINDOW", "22:00-05:00")),
            backfill_include_inactive=_bool(e, "ESB_BACKFILL_INCLUDE_INACTIVE", True),
            entities=parse_entities(_str(e, "ESB_ENTITIES", ",".join(DEFAULT_ENTITIES))),
            pl_refresh_months=parse_pl_refresh_months(e.get("ESB_PL_REFRESH_MONTHS")),
            run_once=_bool(e, "ESB_RUN_ONCE", False),
            max_response_bytes=_int_range(
                e, "ESB_MAX_RESPONSE_BYTES", DEFAULT_MAX_RESPONSE_BYTES, MIN_MAX_RESPONSE_BYTES, MAX_MAX_RESPONSE_BYTES
            ),
            allow_ignore_window=_bool(e, "ESB_ALLOW_IGNORE_WINDOW", False),
            production=is_production(e),
            heartbeat_file=_str(e, "ESB_HEARTBEAT_FILE", "/tmp/efn-esb.heartbeat"),  # noqa: S108
        )


@dataclass(frozen=True)
class MarginSettings:
    """G67 "Margin bahan" (docs/04 §31.2, L-259/L-261). Tanpa secret: dipakai juga CLI `refresh-margin` yang hanya
    membaca/menulis ClickHouse (jalan dengan ESB_ENABLED=false). `brands` kosong = fitur mati (default)."""

    brands: tuple[str, ...] = ()
    months: int = 13
    weekday: int = 0  # 0 = Senin (WIB)
    hour_wib: int = 2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        e: Mapping[str, str] = os.environ if env is None else env
        brands = tuple(dict.fromkeys(item.lower() for item in _items(_str(e, "ESB_MARGIN_BRANDS", ""))))
        if any(brand not in KNOWN_BRANDS for brand in brands):
            raise RuntimeError(f"ESB_MARGIN_BRANDS: brand tidak dikenal; pilihan {', '.join(KNOWN_BRANDS)}")
        return cls(
            brands=brands,
            months=_int_range(e, "ESB_MARGIN_MONTHS", 13, 2, 24),
            weekday=_int_range(e, "ESB_MARGIN_WEEKDAY", 0, 0, 6),
            hour_wib=_int_range(e, "ESB_MARGIN_HOUR_WIB", 2, 0, 23),
        )


def validate_margin(settings: EsbSettings, margin: MarginSettings) -> None:
    """Loop `run` (fail-fast saat start): brand margin wajib aktif di ESB_BRANDS (snapshot bom_detail butuh login) dan
    entitas `purchase_line` wajib aktif (harga PO harian, §31.1)."""
    if not margin.brands:
        return
    active = {cfg.key for cfg in settings.brands}
    missing = [brand for brand in margin.brands if brand not in active]
    if missing:
        raise RuntimeError(f"ESB_MARGIN_BRANDS: {', '.join(missing)} tidak ada di ESB_BRANDS")
    if "purchase_line" not in settings.entities:
        raise RuntimeError("ESB_MARGIN_BRANDS membutuhkan entitas purchase_line di ESB_ENTITIES")


OMS_HOST = "esbcore.co.id"
OMS_DEFAULT_BASE_URL = f"https://{OMS_HOST}"


def oms_enabled(env: Mapping[str, str] | None = None) -> bool:
    """G69 kill switch OMS (docs/04 §33.3), terpisah dari ESB_ENABLED."""
    return _bool(os.environ if env is None else env, "ESB_OMS_ENABLED", False)


def validate_oms_base_url(url: str) -> str:
    """§33.2: wajib https, host persis esbcore.co.id, port 443, tanpa path/kredensial/query/fragment."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise TlsConfigError("ESB_OMS_BASE_URL wajib https://")
    if parts.username or parts.password:
        raise RuntimeError("ESB_OMS_BASE_URL tidak boleh memuat kredensial")
    if (parts.hostname or "").lower() != OMS_HOST:
        raise RuntimeError(f"ESB_OMS_BASE_URL wajib host {OMS_HOST}")
    try:
        port = parts.port
    except ValueError:
        raise RuntimeError("ESB_OMS_BASE_URL port tidak valid") from None
    if port not in (None, 443):
        raise RuntimeError("ESB_OMS_BASE_URL wajib port 443")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise RuntimeError("ESB_OMS_BASE_URL tidak boleh memuat path/query/fragment")
    return OMS_DEFAULT_BASE_URL


def parse_hours(text: str, env_name: str = "ESB_OMS_INTRADAY_HOURS") -> tuple[int, int]:
    """`07-23` -> (7, 23): jam WIB [awal, akhir), 0 <= awal < akhir <= 24."""
    start, sep, end = text.partition("-")
    if not sep or not start.strip().isdigit() or not end.strip().isdigit():
        raise RuntimeError(f"{env_name}: format wajib HH-HH")
    low, high = int(start), int(end)
    if not 0 <= low < high <= 24:
        raise RuntimeError(f"{env_name}: wajib 0 <= awal < akhir <= 24")
    return low, high


@dataclass(frozen=True)
class OmsSettings:
    """G69 ESB OMS (docs/04 §33.3). Kredensial = `EsbSettings.brands` (secret Core yang sama, tanpa secret baru)."""

    enabled: bool = False
    brands: tuple[str, ...] = ("pb",)
    base_url: str = OMS_DEFAULT_BASE_URL
    rate_per_sec: float = 2.0
    interval_seconds: int = 900
    intraday_hours: tuple[int, int] = (7, 23)
    correction_days: tuple[int, ...] = (1, 7)
    max_calls_per_run: int = 5000
    backfill_max_calls: int = 60_000
    max_pages: int = 500

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self:
        """Validasi selalu (format); syarat lintas-setting hanya bila ESB_OMS_ENABLED=true (fail closed):
        ESB_ENABLED=true dan ESB_OMS_BRANDS ⊆ ESB_BRANDS."""
        e: Mapping[str, str] = os.environ if env is None else env
        enabled = oms_enabled(e)
        brands = tuple(dict.fromkeys(item.lower() for item in _items(_str(e, "ESB_OMS_BRANDS", "pb"))))
        if not brands or any(brand not in KNOWN_BRANDS for brand in brands):
            raise RuntimeError(f"ESB_OMS_BRANDS: brand tidak dikenal/kosong; pilihan {', '.join(KNOWN_BRANDS)}")
        try:
            days = _parse_days(_str(e, "ESB_OMS_CORRECTION_DAYS", "1,7"))
        except RuntimeError:
            raise RuntimeError("ESB_OMS_CORRECTION_DAYS harus daftar bilangan bulat 1..31") from None
        settings = cls(
            enabled=enabled,
            brands=brands,
            base_url=validate_oms_base_url(_str(e, "ESB_OMS_BASE_URL", OMS_DEFAULT_BASE_URL)),
            rate_per_sec=_float_range(e, "ESB_OMS_RATE_PER_SEC", 2.0, 0.1, 10.0),
            interval_seconds=_int_range(e, "ESB_OMS_INTERVAL_SECONDS", 900, 60, 86_400),
            intraday_hours=parse_hours(_str(e, "ESB_OMS_INTRADAY_HOURS", "07-23")),
            correction_days=days,
            max_calls_per_run=_int_range(e, "ESB_OMS_MAX_CALLS_PER_RUN", 5000, 1, 1_000_000),
            backfill_max_calls=_int_range(e, "ESB_OMS_BACKFILL_MAX_CALLS", 60_000, 1, 1_000_000),
            max_pages=_int_range(e, "ESB_OMS_MAX_PAGES", _int_range(e, "ESB_MAX_PAGES", 500, 1, 5000), 1, 5000),
        )
        if enabled:
            if not esb_enabled(e):
                raise RuntimeError("ESB_OMS_ENABLED=true mensyaratkan ESB_ENABLED=true")
            core = {k.strip().lower() for k in _str(e, "ESB_BRANDS", "").split(",") if k.strip()}
            missing = [brand for brand in brands if brand not in core]
            if missing:
                raise RuntimeError(f"ESB_OMS_BRANDS: {', '.join(missing)} tidak ada di ESB_BRANDS")
            check_oms_prod_lock(enabled, is_production(e), _str(e, OMS_ALLOW_PRODUCTION_ENV, "false").lower() == "true")
        return settings


# SEC-OMS-04: di APP_ENV production (`tls.is_production`, fail closed) OMS aktif ditolak KECUALI flag eksplisit
# `ESB_OMS_ALLOW_PRODUCTION=true`. Keputusan pemilik proyek 25 Nov 2026: efn-prod lokal = staging berdata produksi
# (direset saat go-live) -> flag hanya di override lokal efn-prod, tidak pernah di repo (check-encryption: default
# false). Go-live sungguhan: flag dihapus sampai Q-ESB1, Q-OMS2, Q-OMS3 + persetujuan DPO (runbook esb-extract §10).
OMS_ALLOW_PRODUCTION_ENV = "ESB_OMS_ALLOW_PRODUCTION"


def check_oms_prod_lock(enabled: bool, production: bool, allowed: bool) -> None:
    if enabled and production and not allowed:
        raise RuntimeError(
            f"ESB_OMS_ENABLED=true ditolak di production (SEC-OMS-04): butuh {OMS_ALLOW_PRODUCTION_ENV}=true "
            "(hanya efn-prod lokal pra-go-live) atau APP_ENV non-produksi: dev/development/local/test/ci"
        )


@dataclass(frozen=True)
class ClickHouseSettings:
    host: str = "clickhouse"
    port: int = 8443
    user: str = "efn_esb"
    password: str = field(default="", repr=False)
    database: str = "efn_dwh"
    raw_database: str = "efn_esb"
    secure: bool = True
    ca_file: str | None = None

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
            raise RuntimeError("CH_PASSWORD_FILE wajib diisi (secret clickhouse_password_esb)")
        # SEC-AG-04: nama database disisipkan sebagai identifier SQL -> wajib identifier sederhana.
        database = _str(e, "CH_DATABASE", "efn_dwh")
        raw_database = _str(e, "CH_RAW_DATABASE", "efn_esb")
        for name, value in (("CH_DATABASE", database), ("CH_RAW_DATABASE", raw_database)):
            if not _DB_RE.fullmatch(value):
                raise RuntimeError(f"{name} wajib identifier ^[a-z_][a-z0-9_]{{0,63}}$")
        return cls(
            host=_str(e, "CH_HOST", "clickhouse"),
            port=_int_range(e, "CH_PORT", 8443 if secure else 8123, 1, 65535),
            user=_str(e, "CH_USER", "efn_esb"),
            password=password,
            database=database,
            raw_database=raw_database,
            secure=secure,
            ca_file=ca,
        )
