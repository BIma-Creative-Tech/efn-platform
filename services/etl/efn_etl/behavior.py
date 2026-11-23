"""Gelombang 50 (ADR-047 Proposed, docs/04 §28.6/§28.7, L-152..L-155, R-L13): perilaku visitor supermarket.

Arah data (ADR-003 tetap, L-153): BFF -> RabbitMQ `behavior.event` -> PG `behavior.event_raw` (staging 7 hari, milik
BFF) -> job incremental `behavior_events` (cursor `ingested_at, subject_key||event_id`) -> ClickHouse
`fact_visitor_event` (ReplacingMergeTree(ingested_at), TTL 90 hari) -> agregat harian `agg_visitor_*` (INSERT ... SELECT
di server, TTL 2 tahun, tanpa subject_key).

Erasure (L-154): BFF menulis `subject_key` yang harus dihapus ke PG `behavior.subject_erasure` (ASUMSI G50: tabel milik
BFF, lihat `SQL_BEHAVIOR_ERASURES` dan laporan) -> job `behavior_erasure` (setiap siklus, SEBELUM job incremental):
ALTER DELETE per batch subject_key di `fact_visitor_event`, verifikasi count = 0, baru watermark maju. Kunci dalam
jendela recheck (default 24 jam) dihitung ulang setiap siklus (menutup race baris staging yang terbaca sebelum BFF
menghapusnya) dan dikecualikan dari job incremental pada siklus yang sama. DSAR (`platform.dsar_request` store
`clickhouse`): `BehaviorDsarHook` menahan `privacy.erasure.completed` sampai semua subject_key permintaan itu
(kolom `request_id`)
hilang dari ClickHouse.

PDP: ETL tidak memegang pepper (SEC-16) — `subject_key` sudah HMAC dari BFF dan divalidasi hex sha256 (fail closed).
Tidak ada device ID mentah, member_id, IP, user agent, GPS. `query` disanitasi ulang (defense in depth).

V-01 (keputusan pemilik proyek 23 Nov 2026): kunjungan anonim tanpa consent ikut dicatat. Kolom staging `consented`
(false = anonim, subject_key domain `anon:` dari BFF) dan `page` (event `page_view`). Anonim tidak pernah `member`
(fail closed). Agregat dihitung dari semua visitor; segmen hanya `consented = 1` (view v_seg_*).
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from efn_etl.entities import company_code
from efn_etl.jobs import Job, pseudonym
from efn_etl.stock import JAKARTA
from efn_etl.watermark import Cursor, cursor_after, initial_cursor, next_watermark, to_utc

log = logging.getLogger(__name__)

JOB_EVENTS = "behavior_events"
JOB_ERASURE = "behavior_erasure"
JOB_AGG = "behavior_agg"
BEHAVIOR_ERASURE_TABLE = "fact_visitor_event"

# docs/04 §28.6 (klien) + turunan server. `cart_abandon` = §28.6 ("diturunkan server"); `purchase` = ASUMSI G50
# (event server per baris pesanan online `paid`, dipakai segmen viewed-not-bought). Tipe lain -> halaman batal.
CLIENT_EVENT_TYPES = (
    "product_view",
    "wishlist_add",
    "wishlist_remove",
    "cart_add",
    "cart_remove",
    "checkout_start",
    "search",
    "notify_me_request",
    "price_alert_set",
    "page_view",  # V-01
)
SERVER_EVENT_TYPES = ("cart_abandon", "purchase")
# V-01: tipe yang tidak boleh datang dari subjek anonim = fitur member + turunan server (cart_abandon/purchase hanya
# subjek consented). Cermin CHECK `event_raw_anon_check` staging PG; ETL mengulang (fail closed).
ANON_FORBIDDEN_TYPES = frozenset(
    {"wishlist_add", "wishlist_remove", "notify_me_request", "price_alert_set", *SERVER_EVENT_TYPES}
)
# V-01: enum `page` event page_view (kontrak V-01 = CHECK staging PG).
PAGES = frozenset(
    {"home", "category", "product", "search", "cart", "checkout", "promo", "store", "account", "order", "other"}
)
EVENT_TYPES = frozenset(CLIENT_EVENT_TYPES + SERVER_EVENT_TYPES)
CHANNELS = frozenset({"web", "app"})
SUBJECT_KINDS = frozenset({"device", "member"})
STOCK_STATUSES = frozenset({"in_stock", "low", "out"})
MAX_DURATION_S = 600
MAX_QTY = 999
MAX_QUERY = 64
REDACTED = "[redacted]"
_CHANNEL_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{0,15}$")
_OUTLET_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,15}$")
_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+")
# Rangkaian angka (boleh dipisah spasi/titik/strip/kurung, mis. nomor HP "0812-3456-7890"); disensor bila >= 6 digit.
_NUMBER_RUN_RE = re.compile(r"\+?\d[\d\s().-]*\d|\d")
_SPACES_RE = re.compile(r"\s+")


# ---------------------------------------------------------------- behavior.event_raw -> fact_visitor_event
# Kolom staging = ASUMSI G50 (tabel dibuat BFF; kontrak §28.6/§28.7 hanya menyebut "insert batch"): lihat laporan
# efn-data-ai-engineer bagian G50 dan services/etl/sql/platform-behavior-grants.sql (grant kolom = verifikasi skema).
# Cursor `ingested_at` (waktu baris masuk staging, bukan received_at): antrean RabbitMQ yang tertunda tidak membuat
# event terlewat. Butuh index BFF `(ingested_at)` per partisi.
SQL_BEHAVIOR_EVENTS = """
SELECT e.event_id::text AS event_id, e.subject_key, e.subject_kind, e.company_code, e.channel, e.channel_code,
       e.outlet_code, e.type, e.product_id, e.duration_s, e.qty, e.query, e.result_count, e.stock_status, e.price_idr,
       e.consented, e.page, e.received_at, e.ingested_at,
       e.ingested_at AS _cursor_ts, (e.subject_key || ':' || e.event_id::text) AS _cursor_key
FROM behavior.event_raw e
WHERE e.ingested_at >= %(cursor_ts)s
  AND (e.ingested_at, e.subject_key || ':' || e.event_id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY e.ingested_at, (e.subject_key || ':' || e.event_id::text)
LIMIT %(limit)s
"""

EVENT_COLUMNS = (
    "event_id",
    "subject_key",
    "subject_kind",
    "company_code",
    "channel",
    "channel_code",
    "outlet_code",
    "event_type",
    "product_id",
    "duration_s",
    "qty",
    "query",
    "result_count",
    "stock_status",
    "price_idr",
    "consented",
    "page",
    "received_at",
    "event_date",
    "ingested_at",
)


def sanitize_query(value: Any) -> str:
    """Istilah pencarian (§28.6): NFKC, lowercase, spasi dirapatkan, email & rangkaian >= 6 digit -> `[redacted]`,
    maks 64 karakter. BFF sudah melakukannya; ETL mengulang agar data pribadi yang lolos tidak masuk mart."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).lower()
    text = "".join(ch for ch in text if ch.isprintable())
    text = _SPACES_RE.sub(" ", text).strip()
    text = _EMAIL_RE.sub(REDACTED, text)

    def _digits(match: re.Match[str]) -> str:
        chunk = match.group(0)
        return REDACTED if sum(c.isdigit() for c in chunk) >= 6 else chunk

    text = _NUMBER_RUN_RE.sub(_digits, text)
    return text[:MAX_QUERY].strip()


def _clamp_int(value: Any, high: int) -> int:
    if value is None or value == "":
        return 0
    number = int(value)
    return max(0, min(number, high))


def _opt_nonneg_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    return number if number >= 0 else None


def consented_flag(value: Any) -> int:
    """V-01: `event_raw.consented` (boolean NOT NULL DEFAULT true) -> 1/0. NULL -> 0 (fail safe: tidak pernah masuk
    segmen). Nilai lain -> ValueError (halaman batal)."""
    if value is None or value is False or (type(value) is int and value == 0):
        return 0
    if value is True or (type(value) is int and value == 1):
        return 1
    raise ValueError("event_raw.consented bukan boolean")


def business_date(value: datetime) -> date:
    return to_utc(value).astimezone(JAKARTA).date()


def transform_behavior_event(row: dict[str, Any]) -> list[Any]:
    """Validasi ketat (fail closed): subject_key bukan hash / PT tak dikenal / tipe di luar kontrak -> halaman batal
    (heartbeat error) agar data yang tidak sesuai kontrak PDP tidak pernah masuk ClickHouse."""
    try:
        event_id = uuid.UUID(str(row.get("event_id")))
    except ValueError as exc:
        raise ValueError("event_raw.event_id bukan UUID") from exc
    subject_key = pseudonym(row.get("subject_key"), "event_raw.subject_key")
    kind = str(row.get("subject_kind") or "").strip()
    if kind not in SUBJECT_KINDS:
        raise ValueError("event_raw.subject_kind di luar kontrak")
    channel = str(row.get("channel") or "").strip()
    if channel not in CHANNELS:
        raise ValueError("event_raw.channel di luar kontrak")
    channel_code = str(row.get("channel_code") or "").strip().upper()
    if not _CHANNEL_CODE_RE.fullmatch(channel_code):
        raise ValueError("event_raw.channel_code tidak valid")
    outlet_code = str(row.get("outlet_code") or "").strip()
    if outlet_code and not _OUTLET_CODE_RE.fullmatch(outlet_code):
        raise ValueError("event_raw.outlet_code tidak valid")
    event_type = str(row.get("type") or "").strip()
    if event_type not in EVENT_TYPES:
        raise ValueError("event_raw.type di luar kontrak")
    consented = consented_flag(row.get("consented"))
    if not consented and (kind == "member" or event_type in ANON_FORBIDDEN_TYPES):
        # V-01: kunjungan anonim tidak pernah tertaut member dan tidak memakai fitur member.
        raise ValueError("event_raw anonim melanggar kontrak V-01")
    page = ""
    if event_type == "page_view":
        page = str(row.get("page") or "").strip()
        if page not in PAGES:
            raise ValueError("event_raw.page di luar kontrak")
    is_search = event_type == "search"
    # product_id pada page_view hanya bermakna untuk page=product (kontrak V-01); selain itu diabaikan.
    product_id = _clamp_int(row.get("product_id"), 2**32 - 1)
    if event_type == "page_view" and page != "product":
        product_id = 0
    stock_status = str(row.get("stock_status") or "").strip()
    received_at = to_utc(row["received_at"])
    return [
        event_id,
        subject_key,
        kind,
        company_code(row.get("company_code")),
        channel,
        channel_code,
        outlet_code,
        event_type,
        product_id,
        _clamp_int(row.get("duration_s"), MAX_DURATION_S),
        _clamp_int(row.get("qty"), MAX_QTY),
        sanitize_query(row.get("query")) if is_search else "",
        _opt_nonneg_int(row.get("result_count")) if is_search else None,
        stock_status if stock_status in STOCK_STATUSES else "",
        _opt_nonneg_int(row.get("price_idr")),
        consented,
        page,
        received_at,
        business_date(received_at),
        to_utc(row["ingested_at"]),
    ]


def behavior_job() -> Job:
    """Job incremental G50 (`ETL_ENABLE_BEHAVIOR`, sumber DB platform; butuh grant platform-behavior-grants.sql)."""
    return Job(
        name=JOB_EVENTS,
        source="platform",
        sql=SQL_BEHAVIOR_EVENTS,
        target_table="fact_visitor_event",
        columns=EVENT_COLUMNS,
        transform=transform_behavior_event,
        identity_field="subject_key",
    )


# ---------------------------------------------------------------- agregat harian (INSERT ... SELECT di ClickHouse)
# Parameter server-side {start:Date}/{end:Date}/{k:UInt32}; computed_at = now64(6) server. Rentang [start, end) tanggal
# WIB (event_date). Hitung ulang = versi computed_at baru -> ReplacingMergeTree menimpa kunci yang sama.
SQL_AGG_PRODUCT = """
INSERT INTO efn_dwh.agg_visitor_product_daily
    (day, company_code, channel, channel_code, outlet_code, product_id, views, view_seconds, viewers, oos_views,
     wishlist_adds, cart_adds, cart_removes, cart_abandons, purchases, notify_me, price_alerts, computed_at)
SELECT
    event_date, company_code, channel, channel_code, outlet_code, product_id,
    countIf(event_type = 'product_view'),
    sumIf(toUInt64(duration_s), event_type = 'product_view'),
    uniqExactIf(subject_key, event_type = 'product_view'),
    countIf(event_type = 'product_view' AND stock_status = 'out'),
    countIf(event_type = 'wishlist_add'),
    countIf(event_type = 'cart_add'),
    countIf(event_type = 'cart_remove'),
    countIf(event_type = 'cart_abandon'),
    countIf(event_type = 'purchase'),
    countIf(event_type = 'notify_me_request'),
    countIf(event_type = 'price_alert_set'),
    now64(6)
FROM efn_dwh.fact_visitor_event FINAL
WHERE event_date >= {start:Date} AND event_date < {end:Date} AND product_id != 0
GROUP BY event_date, company_code, channel, channel_code, outlet_code, product_id
"""

SQL_AGG_FUNNEL = """
INSERT INTO efn_dwh.agg_visitor_funnel_daily
    (day, company_code, channel, channel_code, outlet_code, subjects, viewers, carters, checkout_starters, purchasers,
     searchers_no_result, computed_at)
SELECT
    event_date, company_code, channel, channel_code, outlet_code,
    uniqExact(subject_key),
    uniqExactIf(subject_key, event_type = 'product_view'),
    uniqExactIf(subject_key, event_type = 'cart_add'),
    uniqExactIf(subject_key, event_type = 'checkout_start'),
    uniqExactIf(subject_key, event_type = 'purchase'),
    uniqExactIf(subject_key, event_type = 'search' AND ifNull(result_count, 1) = 0),
    now64(6)
FROM efn_dwh.fact_visitor_event FINAL
WHERE event_date >= {start:Date} AND event_date < {end:Date}
GROUP BY event_date, company_code, channel, channel_code, outlet_code
"""

# k-anonim (Q-SM17): istilah dengan < k subjek berbeda TIDAK disimpan di agregat 2 tahun.
SQL_AGG_SEARCH = """
INSERT INTO efn_dwh.agg_visitor_search_daily
    (day, company_code, channel, channel_code, query, searches, no_result, subjects, computed_at)
SELECT
    event_date, company_code, channel, channel_code, query,
    count(),
    countIf(ifNull(result_count, 1) = 0),
    uniqExact(subject_key),
    now64(6)
FROM efn_dwh.fact_visitor_event FINAL
WHERE event_date >= {start:Date} AND event_date < {end:Date} AND event_type = 'search' AND query != ''
GROUP BY event_date, company_code, channel, channel_code, query
HAVING uniqExact(subject_key) >= {k:UInt32}
"""

# V-01: halaman per kanal x hari (page_view) + baris page='*' = semua visitor hari itu (event apa pun).
SQL_AGG_PAGE = """
INSERT INTO efn_dwh.agg_visitor_page_daily
    (day, company_code, channel, page, visitors, views, computed_at)
SELECT event_date, company_code, channel, page, uniqExact(subject_key), count(), now64(6)
FROM efn_dwh.fact_visitor_event FINAL
WHERE event_date >= {start:Date} AND event_date < {end:Date} AND event_type = 'page_view' AND page != ''
GROUP BY event_date, company_code, channel, page
UNION ALL
SELECT event_date, company_code, channel, '*', uniqExact(subject_key), countIf(event_type = 'page_view'), now64(6)
FROM efn_dwh.fact_visitor_event FINAL
WHERE event_date >= {start:Date} AND event_date < {end:Date}
GROUP BY event_date, company_code, channel
"""

# Semua agregat dihitung dari SEMUA visitor (anonim + consented, kontrak V-01); tanpa filter `consented`.
AGG_STATEMENTS = (
    ("agg_visitor_product_daily", SQL_AGG_PRODUCT),
    ("agg_visitor_funnel_daily", SQL_AGG_FUNNEL),
    ("agg_visitor_search_daily", SQL_AGG_SEARCH),
    ("agg_visitor_page_daily", SQL_AGG_PAGE),
)
SQL_LAST_AGG = "SELECT max(computed_at), count() FROM efn_dwh.agg_visitor_funnel_daily"


def agg_window(now: datetime, days: int) -> tuple[date, date]:
    """[awal, akhir) tanggal WIB: `days` hari terakhir TERMASUK hari ini (event terlambat ditangkap hitung ulang)."""
    if days < 1:
        raise ValueError("days >= 1")
    end = business_date(now) + timedelta(days=1)
    return end - timedelta(days=days), end


def agg_due(last_computed: datetime | None, now: datetime, interval: timedelta) -> bool:
    if last_computed is None:
        return True
    return to_utc(now) - to_utc(last_computed) >= interval


class AggStore(Protocol):
    def last_agg_at(self) -> datetime | None: ...

    def execute(self, sql: str, parameters: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class AggResult:
    start: date
    end: date
    tables: tuple[str, ...]


def run_behavior_agg(store: AggStore, now: datetime, days: int, k_min: int) -> AggResult:
    if k_min < 2:
        raise ValueError("k_min >= 2")
    start, end = agg_window(now, days)
    done: list[str] = []
    for table, sql in AGG_STATEMENTS:
        params: dict[str, Any] = {"start": start, "end": end}
        if "{k:UInt32}" in sql:
            params["k"] = k_min
        store.execute(sql, params)
        done.append(table)
    return AggResult(start, end, tuple(done))


# ---------------------------------------------------------------- erasure per subject_key
# G50 (tabel milik BFF, init 20-platform-g50a.sql; belum di kontrak docs/04): `behavior.subject_erasure(id bigserial,
# subject_key char(64), request_id uuid NOT NULL, reason text, created_at timestamptz DEFAULT now())`, append-only,
# disimpan BFF >= 100 hari (> TTL 90 hari ClickHouse). BFF mengisi SEMUA subject_key subjek (HMAC member + setiap
# device_key tertaut) dalam transaksi yang sama dengan transisi dsar_request ke processing (commit atomik).
SQL_BEHAVIOR_ERASURES = """
SELECT s.id::text AS erasure_id, s.subject_key, s.created_at,
       s.created_at AS _cursor_ts, s.id::text AS _cursor_key
FROM behavior.subject_erasure s
WHERE s.created_at >= %(cursor_ts)s
  AND (s.created_at, s.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY s.created_at, s.id::text
LIMIT %(limit)s
"""
SQL_DSAR_SUBJECT_KEYS = """
SELECT s.subject_key
FROM behavior.subject_erasure s
WHERE s.request_id = %(request_id)s
ORDER BY s.id
LIMIT %(limit)s
"""
MAX_DSAR_KEYS = 10_000
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def valid_subject_keys(values: Iterable[Any]) -> tuple[list[str], int]:
    """(kunci valid unik terurut, jumlah tidak valid). Kunci tidak valid tidak bisa dicocokkan -> dilewati + dicatat."""
    keys: set[str] = set()
    invalid = 0
    for value in values:
        try:
            keys.add(pseudonym(value, "subject_erasure.subject_key"))
        except ValueError:
            invalid += 1
    return sorted(keys), invalid


def _fq(database: str) -> str:
    if not _IDENT_RE.fullmatch(database):
        raise ValueError("nama database tidak valid")
    return f"{database}.{BEHAVIOR_ERASURE_TABLE}"


def _key_list(keys: Sequence[str]) -> str:
    if not keys:
        raise ValueError("daftar subject_key kosong")
    # Literal aman: setiap kunci divalidasi ketat ^[0-9a-f]{64}$ (pola efn_etl/erasure.py; parameter server-side pada
    # mutation ALTER belum terverifikasi di 24.8).
    return ", ".join(f"'{pseudonym(k, 'subject_key')}'" for k in keys)


def erasure_count_sql(database: str, keys: Sequence[str]) -> str:
    return f"SELECT count() FROM {_fq(database)} WHERE subject_key IN ({_key_list(keys)})"  # noqa: S608 - tervalidasi


def erasure_delete_sql(database: str, keys: Sequence[str]) -> str:
    return f"ALTER TABLE {_fq(database)} DELETE WHERE subject_key IN ({_key_list(keys)})"  # noqa: S608 - tervalidasi


class ErasureSource(Protocol):
    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]: ...

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class SubjectStore(Protocol):
    def count(self, keys: Sequence[str]) -> int: ...

    def delete(self, keys: Sequence[str]) -> None: ...


class WatermarkStore(Protocol):
    def get(self, job: str) -> datetime | None: ...

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None: ...


class ErasurePending(RuntimeError):
    """Mutation belum menghapus semua baris (timeout/gagal): watermark tidak maju, diulang siklus berikutnya."""


def erase_keys(keys: Sequence[str], store: SubjectStore) -> int:
    """Hapus satu batch; kembalikan jumlah baris yang ditemukan. Sisa > 0 setelah DELETE -> ErasurePending."""
    if not keys:
        return 0
    found = store.count(keys)
    if found > 0:
        store.delete(keys)
        remaining = store.count(keys)
        if remaining > 0:
            raise ErasurePending(f"sisa {remaining} baris")
    return found


@dataclass(frozen=True)
class BehaviorErasureResult:
    subjects: int
    rows_deleted: int
    invalid: int
    pages: int
    watermark: datetime | None
    excluded: frozenset[str] = field(default_factory=frozenset)


def run_behavior_erasure(
    source: ErasureSource,
    store: SubjectStore,
    watermarks: WatermarkStore,
    recheck: timedelta,
    batch_size: int,
    max_pages: int,
) -> BehaviorErasureResult:
    """Proses `behavior.subject_erasure` sejak `watermark - recheck`. Kunci di jendela recheck dihitung ulang setiap
    siklus (DELETE hanya bila count > 0) dan dikembalikan sebagai `excluded` untuk job incremental."""
    watermark = watermarks.get(JOB_ERASURE)
    cursor = initial_cursor(watermark, recheck)
    subjects = deleted = invalid = pages = 0
    excluded: set[str] = set()
    while pages < max_pages:
        rows = source.fetch(SQL_BEHAVIOR_ERASURES, cursor, batch_size)
        if not rows:
            break
        keys, bad = valid_subject_keys(r.get("subject_key") for r in rows)
        invalid += bad
        excluded.update(keys)
        deleted += erase_keys(keys, store)
        subjects += len(keys)
        new_watermark = next_watermark(watermark, rows)
        if new_watermark is not None:
            watermarks.set(JOB_ERASURE, new_watermark, len(rows))
            watermark = new_watermark
        cursor = cursor_after(rows)
        pages += 1
        if len(rows) < batch_size:
            break
    if invalid:
        log.error("job=%s %d subject_key tidak valid dilewati (bukan hex sha256)", JOB_ERASURE, invalid)
    return BehaviorErasureResult(subjects, deleted, invalid, pages, watermark, frozenset(excluded))


class BehaviorDsarHook:
    """Dipanggil efn_etl.erasure untuk setiap DSAR store `clickhouse`: hapus subject_key milik permintaan itu dan
    kembalikan sisa baris (0 = boleh lapor selesai)."""

    def __init__(self, source: ErasureSource, store: SubjectStore, batch_size: int = 500) -> None:
        self._source = source
        self._store = store
        self._batch = max(1, min(batch_size, 1000))

    def erase(self, request_id: str) -> int:
        rows = self._source.query(SQL_DSAR_SUBJECT_KEYS, {"request_id": request_id, "limit": MAX_DSAR_KEYS})
        keys, _bad = valid_subject_keys(r.get("subject_key") for r in rows)
        remaining = 0
        for i in range(0, len(keys), self._batch):
            part = keys[i : i + self._batch]
            try:
                erase_keys(part, self._store)
            except ErasurePending:
                remaining += self._store.count(part)
        return remaining
