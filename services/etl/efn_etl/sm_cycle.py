"""G47 (docs/04 §28.13.8): job khusus mart supermarket yang tidak cocok dengan runner incremental umum.

I/O diinjeksi lewat protokol (diuji dengan fake di tests/test_sm.py). Setiap job menghasilkan satu heartbeat
(`etl_job_run`, dibaca `v_sm_freshness`); kegagalan satu job tidak menghentikan job lain (pola __main__).

- `sm_dims_kpi`           : salin penuh efn_insight_sm_kpi (tabel kecil) + nonaktifkan baris yang dihapus di Odoo.
- `sm_replenishment_bias` : log bias append-only -> MergeTree; cursor (create_date, id) + overlap; log_id yang sudah ada
                            di ClickHouse dilewati (dedup) -> re-run idempoten.
- `sm_dc_fill_rate`       : hitung ulang jendela delivery_date >= hari ini - ETL_SM_FILL_RECALC_DAYS; hanya baris
                            berubah yang ditulis.
- `sm_stock_agg`          : INSERT ... SELECT per hari snapshot baru (butuh ETL_ENABLE_STOCK); watermark = snapshot_ts.
- `sm_expiry_agg`         : INSERT ... SELECT hari ini WIB (ETL_ENABLE_SM_EXPIRY).
- `sm_campaign`           : kampanye berubah + kampanye dalam jendela atribusi -> fact_campaign_result +
                            fact_sm_segment_run (ETL_ENABLE_SM_CAMPAIGN). Sumber: Odoo + PG platform v_campaign_stats.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from efn_etl import sm
from efn_etl.heartbeat import Heartbeat, JobRun
from efn_etl.runner import run_job
from efn_etl.watermark import Cursor, cursor_after, initial_cursor, next_watermark, to_utc

log = logging.getLogger(__name__)

STOCK_AGG_MAX_DAYS_PER_CYCLE = 14
MAX_CAMPAIGN_IDS = 5000


class PgQuery(Protocol):
    def fetch(self, sql: str, cursor: Cursor, limit: int) -> list[dict[str, Any]]: ...

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class ChStore(Protocol):
    def rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[tuple[Any, ...]]: ...

    def execute(self, sql: str, parameters: dict[str, Any]) -> None: ...


class Sink(Protocol):
    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


class WatermarkStore(Protocol):
    def get(self, job: str) -> datetime | None: ...

    def set(self, job: str, watermark: datetime, rows_loaded: int) -> None: ...


# ---------------------------------------------------------------- sm_dims_kpi
def run_sm_kpi_sync(odoo: PgQuery, ch: ChStore, sink: Sink, codes: Sequence[str], now: datetime) -> sm.KpiSync:
    odoo_rows = odoo.query(sm.sql_kpi_all(codes), {"limit": 10_000})
    existing = {(int(c), str(k)): (int(a), to_utc(w)) for c, k, a, w in ch.rows(sm.SQL_KPI_EXISTING)}
    result = sm.kpi_sync_rows(odoo_rows, existing, now)
    sink.insert("dim_sm_kpi_config", sm.KPI_COLUMNS, result.rows)
    if result.unknown_keys or result.invalid_values:
        log.warning(
            "job=%s kunci tak dikenal=%d nilai tidak sah=%d (nilai tidak sah -> default)",
            sm.JOB_DIMS_KPI,
            result.unknown_keys,
            result.invalid_values,
        )
    return result


# ---------------------------------------------------------------- sm_replenishment_bias
@dataclass(frozen=True)
class PagedResult:
    rows: int
    pages: int
    watermark: datetime | None


def run_sm_bias(
    odoo: PgQuery,
    ch: ChStore,
    sink: Sink,
    store: WatermarkStore,
    codes: Sequence[str],
    overlap: timedelta,
    batch_size: int,
    max_pages: int,
) -> PagedResult:
    sql = sm.sql_bias(codes)
    watermark = store.get(sm.JOB_BIAS)
    cursor = initial_cursor(watermark, overlap)
    written = pages = 0
    while pages < max_pages:
        rows = odoo.fetch(sql, cursor, batch_size)
        if not rows:
            break
        ids = [int(r["log_id"]) for r in rows]
        existing = [int(r[0]) for r in ch.rows(sm.SQL_BIAS_EXISTING, {"ids": ids})]
        new_rows = sm.new_bias_rows(rows, existing)
        sink.insert("fact_replenishment_bias", sm.BIAS_COLUMNS, new_rows)
        new_wm = next_watermark(watermark, rows)
        if new_wm is not None:
            store.set(sm.JOB_BIAS, new_wm, len(rows))
            watermark = new_wm
        cursor = cursor_after(rows)
        written += len(new_rows)
        pages += 1
        if len(rows) < batch_size:
            break
    return PagedResult(written, pages, watermark)


# ---------------------------------------------------------------- sm_scraps (fit-gap C-6)
def run_sm_scraps(
    odoo: PgQuery,
    sink: Sink,
    store: WatermarkStore,
    codes: Sequence[str],
    overlap: timedelta,
    batch_size: int,
    max_pages: int,
) -> PagedResult:
    """Deteksi kolom efn_* tiap siklus (murah) lalu jalankan job incremental umum (cursor write_date, RMT)."""
    cols = {str(r["column_name"]) for r in odoo.query(sm.SQL_SCRAP_EFN_COLUMNS, {})}
    job = sm.sm_scrap_job(codes, "efn_warehouse_id" in cols, "efn_waste_value" in cols)
    res = run_job(job, odoo, sink, store, overlap, batch_size, max_pages)
    return PagedResult(res.rows, res.pages, res.watermark)


# ---------------------------------------------------------------- sm_dc_fill_rate
def run_sm_fill_rate(
    odoo: PgQuery,
    ch: ChStore,
    sink: Sink,
    codes: Sequence[str],
    now: datetime,
    recalc_days: int,
    batch_size: int,
    max_pages: int,
) -> PagedResult:
    since = sm.wib_date(now) - timedelta(days=recalc_days)
    lines: list[sm.FillLine] = []
    after = pages = 0
    sql_lines = sm.sql_fill_lines(codes)
    while True:
        if pages >= max_pages:
            # Jendela tidak lengkap = angka fill rate salah; gagal jelas (heartbeat error), bukan sebagian.
            raise RuntimeError("sm_dc_fill_rate: batas halaman tercapai")
        rows = odoo.query(sql_lines, {"since": since, "after_id": after, "limit": batch_size})
        if not rows:
            break
        lines.extend(sm.fill_line(r) for r in rows)
        after = int(rows[-1]["line_id"])
        pages += 1
        if len(rows) < batch_size:
            break
    pickings = sorted({ln.picking_id for ln in lines if ln.picking_id})
    moves: list[dict[str, Any]] = []
    for i in range(0, len(pickings), 500):
        moves.extend(odoo.query(sm.SQL_FILL_MOVES, {"pickings": pickings[i : i + 500]}))
    rows_out = sm.fill_rate_rows(lines, moves, now)
    existing = {int(r[0]): tuple(r[1:]) for r in ch.rows(sm.SQL_FILL_EXISTING, {"since": since})}
    changed = sm.changed_fill_rows(rows_out, existing)
    sink.insert("fact_dc_fill_rate", sm.FILL_COLUMNS, changed)
    return PagedResult(len(changed), pages, None)


# ---------------------------------------------------------------- sm_stock_agg / sm_expiry_agg
def run_sm_stock_agg(
    ch: ChStore,
    store: WatermarkStore,
    now: datetime,
    backfill_days: int,
    max_days: int = STOCK_AGG_MAX_DAYS_PER_CYCLE,
) -> list[date]:
    watermark = store.get(sm.JOB_STOCK_AGG)
    since = sm.stock_since(watermark, now, backfill_days)
    runs = [
        (d if isinstance(d, date) else date.fromisoformat(str(d)), ts)
        for d, ts in ch.rows(sm.SQL_STOCK_RUNS, {"since": since})
    ]
    done: list[date] = []
    for day, ts in sm.stock_days_pending(runs, watermark, max_days):
        ch.execute(sm.SQL_STOCK_AGG, {"day": day})
        store.set(sm.JOB_STOCK_AGG, ts, 1)
        done.append(day)
    return done


def run_sm_expiry_agg(ch: ChStore, now: datetime) -> date:
    day = sm.wib_date(now)
    ch.execute(sm.SQL_EXPIRY_AGG, {"day": day})
    return day


# ---------------------------------------------------------------- sm_campaign
@dataclass(frozen=True)
class CampaignResult:
    campaigns: int
    segments: int
    online: bool
    watermark: datetime | None


def run_sm_campaign(
    odoo: PgQuery,
    platform: PgQuery,
    sink: Sink,
    store: WatermarkStore,
    codes: Sequence[str],
    now: datetime,
    overlap: timedelta,
    attr_days: int,
    batch_size: int,
) -> CampaignResult:
    watermark = store.get(sm.JOB_CAMPAIGN)
    since_ts, attr_since = sm.campaign_window(watermark, now, overlap, attr_days)
    campaigns: list[dict[str, Any]] = []
    after = 0
    sql = sm.sql_campaigns(codes)
    while len(campaigns) < MAX_CAMPAIGN_IDS:
        params = {"since_ts": since_ts, "attr_since": attr_since, "after_id": after, "limit": batch_size}
        page = odoo.query(sql, params)
        if not page:
            break
        campaigns.extend(page)
        after = int(page[-1]["campaign_id"])
        if len(page) < batch_size:
            break
    if not campaigns:
        return CampaignResult(0, 0, False, watermark)
    ids = [int(c["campaign_id"]) for c in campaigns]
    online = bool(odoo.query(sm.SQL_ONLINE_COUPON_VISIBLE, {})[0]["n"] >= 2)
    cards = odoo.query(sm.SQL_CAMPAIGN_CARDS, {"ids": ids})
    issued = {int(r["campaign_id"]): int(r["vouchers_issued"]) for r in cards}
    usage = {int(r["campaign_id"]): r for r in odoo.query(sm.sql_campaign_usage(online), {"ids": ids})}
    stats = sm.merge_campaign_stats(platform.query(sm.SQL_CAMPAIGN_STATS, {"codes": list(codes), "ids": ids}))
    sink.insert("fact_campaign_result", sm.CAMPAIGN_COLUMNS, sm.campaign_rows(campaigns, issued, usage, stats, now))
    segments = sm.segment_rows(campaigns)
    sink.insert("fact_sm_segment_run", sm.SEGMENT_COLUMNS, segments)
    versions = [to_utc(c["write_date"]) for c in campaigns if isinstance(c.get("write_date"), datetime)]
    new_wm = max([*versions, *([to_utc(watermark)] if watermark else [])], default=None)
    if new_wm is not None:
        store.set(sm.JOB_CAMPAIGN, new_wm, len(campaigns))
    return CampaignResult(len(campaigns), len(segments), online, new_wm)


# ---------------------------------------------------------------- orkestrasi siklus
@dataclass(frozen=True)
class SmCycleConfig:
    codes: tuple[str, ...]
    stock: bool
    expiry: bool
    campaign: bool
    fill_recalc_days: int
    campaign_attr_days: int
    stock_backfill_days: int
    overlap: timedelta
    batch_size: int
    max_pages: int


def _guard(job: str, heartbeat: Heartbeat, action: Callable[[], tuple[int, int, datetime | None]]) -> bool:
    started = time.monotonic()
    try:
        rows, pages, watermark = action()
    except Exception as exc:
        # Tanpa pesan exception mentah (bisa memuat potongan SQL/nilai): hanya nama tipe.
        log.error("job=%s gagal: %s", job, type(exc).__name__)
        elapsed = int((time.monotonic() - started) * 1000)
        heartbeat(JobRun(job, datetime.now(UTC), "error", duration_ms=elapsed))
        return False
    elapsed = int((time.monotonic() - started) * 1000)
    heartbeat(JobRun(job, datetime.now(UTC), "ok", rows=rows, pages=pages, duration_ms=elapsed, watermark=watermark))
    return True


def run_sm_cycle(
    cfg: SmCycleConfig,
    odoo: PgQuery,
    platform: PgQuery,
    ch: ChStore,
    sink: Sink,
    store: WatermarkStore,
    heartbeat: Heartbeat,
    now: datetime | None = None,
) -> dict[str, bool]:
    """Satu putaran job khusus G47 (dipanggil __main__ setelah job incremental + snapshot stok)."""
    current = now or datetime.now(UTC)
    status: dict[str, bool] = {}

    def kpi() -> tuple[int, int, datetime | None]:
        res = run_sm_kpi_sync(odoo, ch, sink, cfg.codes, current)
        return len(res.rows), 1, None

    def bias() -> tuple[int, int, datetime | None]:
        res = run_sm_bias(odoo, ch, sink, store, cfg.codes, cfg.overlap, cfg.batch_size, cfg.max_pages)
        return res.rows, res.pages, res.watermark

    def fill() -> tuple[int, int, datetime | None]:
        res = run_sm_fill_rate(odoo, ch, sink, cfg.codes, current, cfg.fill_recalc_days, cfg.batch_size, cfg.max_pages)
        return res.rows, res.pages, None

    def scraps() -> tuple[int, int, datetime | None]:
        res = run_sm_scraps(odoo, sink, store, cfg.codes, cfg.overlap, cfg.batch_size, cfg.max_pages)
        return res.rows, res.pages, res.watermark

    status[sm.JOB_DIMS_KPI] = _guard(sm.JOB_DIMS_KPI, heartbeat, kpi)
    status[sm.JOB_SCRAPS] = _guard(sm.JOB_SCRAPS, heartbeat, scraps)
    status[sm.JOB_BIAS] = _guard(sm.JOB_BIAS, heartbeat, bias)
    status[sm.JOB_FILL] = _guard(sm.JOB_FILL, heartbeat, fill)
    if cfg.stock:

        def stock() -> tuple[int, int, datetime | None]:
            days = run_sm_stock_agg(ch, store, current, cfg.stock_backfill_days)
            return len(days), len(days), store.get(sm.JOB_STOCK_AGG)

        status[sm.JOB_STOCK_AGG] = _guard(sm.JOB_STOCK_AGG, heartbeat, stock)
    if cfg.expiry:

        def expiry() -> tuple[int, int, datetime | None]:
            run_sm_expiry_agg(ch, current)
            return 1, 1, None

        status[sm.JOB_EXPIRY_AGG] = _guard(sm.JOB_EXPIRY_AGG, heartbeat, expiry)
    if cfg.campaign:

        def campaign() -> tuple[int, int, datetime | None]:
            res = run_sm_campaign(
                odoo, platform, sink, store, cfg.codes, current, cfg.overlap, cfg.campaign_attr_days, cfg.batch_size
            )
            return res.campaigns + res.segments, 1, res.watermark

        status[sm.JOB_CAMPAIGN] = _guard(sm.JOB_CAMPAIGN, heartbeat, campaign)
    return status
