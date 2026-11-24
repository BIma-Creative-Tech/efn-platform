"""Tabel agregat harian untuk Insight (keputusan pemilik proyek 22 Nov 2026, docs/design/insight-web.md; ADR-040/041
Proposed). DDL: infra/clickhouse/init/20-esb-aggregates.sql (admin); hak: infra/clickhouse/access/esb-access.sql.

Masalah: view `efn_dwh.v_esb_sales_branch_daily`, `v_esb_sales_daily`, `v_esb_flow_daily` menghitung ulang setiap query
(FINAL atas fakta penuh + filter checkpoint + uniqExact + JOIN dimensi) -> Insight kena timeout (159) / memori (241)
setelah data 1 tahun. Solusi: materialisasi ke `efn_dwh.agg_esb_*` (MergeTree, kolom = keluaran view).

Refresh (review keamanan 22 Nov 2026, SEC-AG-01..03): agg DAN build permanen `<agg>_build` dibuat admin di init
(engine + storage policy terenkripsi ditetapkan admin; esb_writer tanpa CREATE/DROP TABLE). Per bulan kalender:

    cek pemegang kunci -> TRUNCATE build -> INSERT bulan itu dari view ke build -> pagar penyusutan
    -> cek pemegang kunci -> ALTER TABLE agg REPLACE PARTITION ID '<yyyymm>' FROM build -> heartbeat kunci

lalu DROP PARTITION untuk bulan di agg yang tidak lagi ada di sumber (juga lewat pagar penyusutan).
Atomik PER BULAN (Insight tidak pernah melihat bulan setengah jadi), tidak atomik lintas bulan: selama refresh,
sebagian bulan sudah baru dan sebagian masih lama.
Pagar penyusutan (SEC-AG-03): bulan yang hasilnya 0 baris padahal agg berisi, atau turun > 50%, dilewati
(agregat berstatus `error`) kecuali `allow_shrink`.
Kunci (SEC-AG-02): tabel `efn_esb.aggregate_refresh_lock` (heartbeat, bukan umur CREATE).
Bukan refreshable MV (eksperimental di 24.8) dan bukan MV-on-insert (fakta di-dedup lewat `_version` + checkpoint).
Logika SQL murni (diuji tanpa ClickHouse); I/O lewat `RefreshStore`.
"""

import logging
import re
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

log = logging.getLogger("efn_esb.aggregates")

# Di bawah send_receive_timeout klien (store.py, 300 dtk): server menghentikan query lebih dulu (galat 159 jelas),
# bukan klien yang memutus koneksi sementara INSERT masih berjalan di server.
INSERT_SETTINGS: dict[str, Any] = {"max_execution_time": 280}
REFRESH_TABLE = "aggregate_refresh"
REFRESH_COLUMNS = ("name", "status", "started_at", "finished_at", "rows", "months", "first_day", "last_day", "error")
LOCK_TABLE = "aggregate_refresh_lock"
# Heartbeat ditulis setiap bulan selesai (satu INSERT <= 280 dtk) -> 15 menit tanpa heartbeat = proses mati.
LOCK_STALE_SECONDS = 15 * 60
# Jeda setelah menulis baris kunci sebelum membaca pemegang: baris proses lain yang ditulis bersamaan sempat terlihat.
LOCK_SETTLE_SECONDS = 2.0
# Pagar penyusutan: hasil < 50% isi agg saat ini (atau 0 padahal agg berisi) -> bulan dilewati.
SHRINK_RATIO = 0.5
NO_DAY = date(1970, 1, 1)
BUSY_ERROR = "refresh lain memegang kunci"
_PARTITION_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class Aggregate:
    name: str  # tabel efn_dwh
    source: str  # view efn_dwh sumber (tidak diubah)
    date_column: str
    entities: tuple[str, ...]  # entitas checkpoint yang mengisi view (rentang bulan)
    columns: tuple[str, ...]  # = kolom keluaran view, urutan sama
    order_by: tuple[str, ...]

    @property
    def build(self) -> str:
        return f"{self.name}_build"


SALES_BRANCH_COLUMNS = (
    "sales_date",
    "brand",
    "company_code",
    "branch_id",
    "branch_name",
    "trx_count",
    "line_count",
    "qty",
    "gross",
    "discount",
    "net",
    "cogs",
)
SALES_COLUMNS = (
    "sales_date",
    "brand",
    "company_code",
    "branch_id",
    "branch_name",
    "menu_code",
    "menu_name",
    "menu_category",
    "menu_category_detail",
    "is_takeaway",
    "qty",
    "gross",
    "discount",
    "net",
    "cogs",
    "trx_count",
)
FLOW_COLUMNS = (
    "day",
    "brand",
    "company_code",
    "branch_id",
    "branch_name",
    "product_code",
    "product_name",
    "ck_prod",
    "ck_out",
    "in_ck",
    "in_other",
    "store_prod",
    "st_in",
    "st_out",
    "sold",
)

# ORDER BY diawali company_code (row policy + filter PT Insight), lalu brand, tanggal, cabang (filter global Insight).
AGGREGATES: tuple[Aggregate, ...] = (
    Aggregate(
        "agg_esb_sales_branch_daily",
        "v_esb_sales_branch_daily",
        "sales_date",
        ("sales",),
        SALES_BRANCH_COLUMNS,
        ("company_code", "brand", "sales_date", "branch_id"),
    ),
    Aggregate(
        "agg_esb_sales_daily",
        "v_esb_sales_daily",
        "sales_date",
        ("sales",),
        SALES_COLUMNS,
        ("company_code", "brand", "sales_date", "branch_id", "menu_code"),
    ),
    Aggregate(
        "agg_esb_flow_daily",
        "v_esb_flow_daily",
        "day",
        ("production", "delivery", "transfer", "fin_sold"),
        FLOW_COLUMNS,
        ("company_code", "brand", "day", "branch_id", "product_code"),
    ),
)
# Slice entitas ini yang ditulis -> agregat perlu dibangun ulang. `master` mengubah nama/kategori menu & produk
# dan alias (sekali/hari). `branch` sengaja tidak: ditarik setiap siklus; nama cabang baru ikut refresh berikutnya.
TRIGGER_ENTITIES = frozenset({e for agg in AGGREGATES for e in agg.entities} | {"master"})


# ------------------------------------------------------------------------------------------------ SQL (murni)
def table_definition(agg: Aggregate, mart: str) -> str:
    """Engine + kolom tabel agregat = teks di init 20-esb-aggregates.sql (diuji sama persis). Tipe kolom diambil dari
    view (CREATE ... AS SELECT ... WHERE 0), jadi nama + tipe = view sumber. Hanya admin (init) yang menjalankannya."""
    return (
        "ENGINE = MergeTree\n"  # noqa: S608 -- identifier konstanta modul
        f"PARTITION BY toYYYYMM({agg.date_column})\n"
        f"ORDER BY ({', '.join(agg.order_by)})\n"
        "SETTINGS storage_policy = 'encrypted'\n"
        f"AS SELECT {', '.join(agg.columns)}\n"
        f"FROM {mart}.{agg.source}\n"
        "WHERE 0"
    )


def truncate_build_sql(agg: Aggregate, mart: str) -> str:
    return f"TRUNCATE TABLE {mart}.{agg.build}"


def insert_month_sql(agg: Aggregate, mart: str) -> str:
    """Satu bulan per INSERT (memori kecil). Filter tanggal pada kolom yang langsung = kunci GROUP BY view, sehingga
    terdorong ke subquery fakta (partisi toYYYYMM) -- diverifikasi lewat EXPLAIN indexes=1 (runbook)."""
    cols = ", ".join(agg.columns)
    return (
        f"INSERT INTO {mart}.{agg.build} ({cols})\n"  # noqa: S608 -- identifier konstanta; nilai = parameter
        f"SELECT {cols}\n"
        f"FROM {mart}.{agg.source}\n"
        f"WHERE {agg.date_column} >= {{start:Date}} AND {agg.date_column} <= {{end:Date}}"
    )


def count_build_sql(agg: Aggregate, mart: str) -> str:
    return f"SELECT count() FROM {mart}.{agg.build}"  # noqa: S608


def count_month_sql(agg: Aggregate, mart: str) -> str:
    """Baris agg saat ini untuk satu bulan (pagar penyusutan)."""
    return (
        f"SELECT count() FROM {mart}.{agg.name} "  # noqa: S608
        f"WHERE {agg.date_column} >= {{start:Date}} AND {agg.date_column} <= {{end:Date}}"
    )


def count_agg_sql(agg: Aggregate, mart: str) -> str:
    return f"SELECT count() FROM {mart}.{agg.name}"  # noqa: S608


def _partition_id(value: str) -> str:
    # ID partisi disisipkan sebagai literal (ALTER ... PARTITION ID tidak menerima parameter) -> wajib 6 digit.
    if not _PARTITION_RE.fullmatch(value):
        raise ValueError("partition id tidak valid")
    return value


def replace_partition_sql(agg: Aggregate, mart: str, partition: str) -> str:
    return f"ALTER TABLE {mart}.{agg.name} REPLACE PARTITION ID '{_partition_id(partition)}' FROM {mart}.{agg.build}"


def drop_partition_sql(agg: Aggregate, mart: str, partition: str) -> str:
    return f"ALTER TABLE {mart}.{agg.name} DROP PARTITION ID '{_partition_id(partition)}'"


def partitions_sql(agg: Aggregate, mart: str) -> str:
    """Partisi agg lewat kolom virtual: cukup SELECT pada agg (esb_writer tanpa hak system.parts, diuji 24.8.14)."""
    return f"SELECT DISTINCT _partition_id FROM {mart}.{agg.name} ORDER BY _partition_id"  # noqa: S608


def day_range_sql(raw: str) -> str:
    """Rentang hari yang terlihat di view = hari ber-checkpoint selesai (view memfilter versi slice ber-checkpoint)."""
    return (
        f"SELECT min(day), max(day), count() FROM {raw}.extract_checkpoint "  # noqa: S608
        "WHERE status = 'done' AND entity IN {entities:Array(String)}"
    )


def holder_sql(raw: str) -> str:
    """Pemegang kunci aktif: belum dilepas, heartbeat terakhir < stale detik (jam SERVER), urut (mulai, run_id)."""
    return (
        "SELECT run_id FROM (SELECT run_id, min(started_at) AS s, max(heartbeat_at) AS hb, max(released) AS rel "  # noqa: S608
        f"FROM {raw}.{LOCK_TABLE} GROUP BY run_id) "
        "WHERE rel = 0 AND hb > now64(3) - toIntervalSecond({stale:UInt32}) ORDER BY s, run_id LIMIT 1"
    )


def month_ranges(first: date, last: date) -> list[tuple[date, date]]:
    """Bulan kalender penuh yang mencakup first..last (batas partisi toYYYYMM; hari baru di bulan terakhir ikut)."""
    if last < first:
        raise ValueError("last < first")
    out: list[tuple[date, date]] = []
    cur = first.replace(day=1)
    while cur <= last:
        nxt = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
        out.append((cur, nxt - timedelta(days=1)))
        cur = nxt
    return out


def is_shrink(built: int, current: int) -> bool:
    """Hasil baru 0 padahal agg berisi, atau < SHRINK_RATIO isi saat ini."""
    return current > 0 and (built == 0 or built < current * SHRINK_RATIO)


def error_label(exc: BaseException) -> str:
    """Kelas exception + kode ClickHouse saja: pesan galat bisa memuat potongan query/nilai -> tidak dicatat."""
    match = re.search(r"Code: (\d+)", str(exc))
    return f"{type(exc).__name__} code={match.group(1)}" if match else type(exc).__name__


# ------------------------------------------------------------------------------------------------ eksekusi (I/O)
class RefreshStore(Protocol):
    @property
    def mart_db(self) -> str: ...

    @property
    def raw_db(self) -> str: ...

    def command(
        self, sql: str, parameters: dict[str, Any] | None = None, settings: dict[str, Any] | None = None
    ) -> None: ...

    def query_rows(self, sql: str, parameters: dict[str, Any] | None = None) -> list[Sequence[Any]]: ...

    def insert_control(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


class LockLost(RuntimeError):
    """Proses ini bukan lagi pemegang kunci (heartbeat basi diambil alih proses lain): berhenti tanpa menulis."""


class RefreshLock:
    """Kunci berbasis heartbeat di `efn_esb.aggregate_refresh_lock` (SEC-AG-02). Waktu baris diisi server."""

    def __init__(
        self,
        store: RefreshStore,
        run_id: uuid.UUID | None = None,
        sleep: Callable[[float], None] = time.sleep,
        settle: float | None = None,
        stale: int = LOCK_STALE_SECONDS,
    ) -> None:
        self._store = store
        self.run_id = run_id or uuid.uuid4()
        self._sleep = sleep
        self._settle = LOCK_SETTLE_SECONDS if settle is None else settle
        self._stale = stale

    def _holder(self) -> Any:
        rows = self._store.query_rows(holder_sql(self._store.raw_db), {"stale": self._stale})
        return rows[0][0] if rows else None

    def heartbeat(self) -> None:
        self._store.insert_control(LOCK_TABLE, ("run_id",), [[self.run_id]])

    def acquire(self) -> bool:
        """Tulis baris, tunggu sebentar, lalu True hanya bila proses ini pemegang (urutan sama di semua proses)."""
        self.heartbeat()
        self._sleep(self._settle)
        return self._holder() == self.run_id

    def ensure(self) -> None:
        if self._holder() != self.run_id:
            raise LockLost("lock_lost")

    def release(self) -> None:
        try:
            self._store.insert_control(LOCK_TABLE, ("run_id", "released"), [[self.run_id, 1]])
        except Exception as exc:  # kunci tanpa release basi sendiri setelah LOCK_STALE_SECONDS
            log.error("aggregate kunci tidak dilepas error=%s", error_label(exc))


@dataclass
class RefreshResult:
    name: str
    status: str  # ok | busy | error
    started_at: datetime
    finished_at: datetime
    rows: int = 0
    months: int = 0
    first_day: date | None = None
    last_day: date | None = None
    error: str = ""

    def row(self) -> list[Any]:
        return [
            self.name,
            self.status,
            self.started_at,
            self.finished_at,
            self.rows,
            self.months,
            self.first_day or NO_DAY,
            self.last_day or NO_DAY,
            self.error,
        ]


def _now() -> datetime:
    value = datetime.now(UTC)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _count(store: RefreshStore, sql: str, parameters: dict[str, Any] | None = None) -> int:
    return int(store.query_rows(sql, parameters)[0][0])


def refresh_one(
    store: RefreshStore,
    agg: Aggregate,
    lock: RefreshLock,
    clock: Callable[[], datetime] = _now,
    allow_shrink: bool = False,
) -> RefreshResult:
    """Bangun ulang satu agregat per bulan. Melempar LockLost / galat ClickHouse (bulan yang sudah diganti tetap)."""
    mart, raw = store.mart_db, store.raw_db
    started = clock()
    first, last, count = store.query_rows(day_range_sql(raw), {"entities": list(agg.entities)})[0]
    months = month_ranges(first, last) if int(count) else []
    insert = insert_month_sql(agg, mart)
    replaced = 0
    skipped: list[str] = []
    for start, end in months:
        partition = start.strftime("%Y%m")
        bounds = {"start": start, "end": end}
        lock.ensure()
        store.command(truncate_build_sql(agg, mart))
        store.command(insert, parameters=bounds, settings=INSERT_SETTINGS)
        built = _count(store, count_build_sql(agg, mart))
        if not allow_shrink and is_shrink(built, _count(store, count_month_sql(agg, mart), bounds)):
            skipped.append(partition)
        else:
            lock.ensure()
            store.command(replace_partition_sql(agg, mart, partition))
            replaced += 1
        lock.heartbeat()
    # Bulan di agg yang tidak lagi ada di sumber (data sumber dihapus / rentang menyempit) = penyusutan juga.
    wanted = {start.strftime("%Y%m") for start, _ in months}
    for row in store.query_rows(partitions_sql(agg, mart)):
        partition = str(row[0])
        if partition in wanted:
            continue
        if not allow_shrink:
            skipped.append(partition)
            continue
        lock.ensure()
        store.command(drop_partition_sql(agg, mart, partition))
    lock.ensure()
    store.command(truncate_build_sql(agg, mart))  # build kosong di luar refresh
    return RefreshResult(
        agg.name,
        "error" if skipped else "ok",
        started,
        clock(),
        rows=_count(store, count_agg_sql(agg, mart)),
        months=replaced,
        first_day=first if months else None,
        last_day=last if months else None,
        error=f"shrink {','.join(sorted(skipped))}" if skipped else "",
    )


def _log_and_record(store: RefreshStore, result: RefreshResult) -> None:
    level = logging.INFO if result.status == "ok" else logging.ERROR
    log.log(
        level,
        "aggregate=%s status=%s rows=%d months=%d seconds=%.1f%s",
        result.name,
        result.status,
        result.rows,
        result.months,
        (result.finished_at - result.started_at).total_seconds(),
        f" error={result.error}" if result.error else "",
    )
    try:
        store.insert_control(REFRESH_TABLE, REFRESH_COLUMNS, [result.row()])
    except Exception as exc:  # catatan kesegaran best effort; tabel agregat sudah benar
        log.error("aggregate=%s aggregate_refresh tidak tersimpan error=%s", result.name, error_label(exc))


def refresh_all(
    store: RefreshStore,
    aggregates: Sequence[Aggregate] = AGGREGATES,
    clock: Callable[[], datetime] = _now,
    allow_shrink: bool = False,
    lock: RefreshLock | None = None,
) -> list[RefreshResult]:
    """Satu kunci untuk semua agregat. Bukan pemegang -> semua `busy` tanpa menyentuh tabel. Satu agregat gagal tidak
    menghentikan yang lain; kunci hilang -> agregat sisanya `busy`. Hasil dicatat ke aggregate_refresh."""
    lock = lock or RefreshLock(store)
    results: list[RefreshResult] = []
    try:
        held = lock.acquire()
        for agg in aggregates:
            started = clock()
            if not held:
                result = RefreshResult(agg.name, "busy", started, clock(), error=BUSY_ERROR)
            else:
                try:
                    result = refresh_one(store, agg, lock, clock, allow_shrink)
                except LockLost:
                    held = False
                    result = RefreshResult(agg.name, "error", started, clock(), error="lock_lost")
                except Exception as exc:
                    result = RefreshResult(agg.name, "error", started, clock(), error=error_label(exc))
            results.append(result)
            _log_and_record(store, result)
    finally:
        lock.release()
    return results
