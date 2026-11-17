"""Perencanaan slice (entitas x scope x hari) dan jendela waktu WIB. Logika murni (docs/04 §25.4).

Satu *slice* = satu (entitas, scope_key, hari). `scope_key` = branchID untuk entitas per toko, `*` untuk per brand.
Slice selesai -> baris `efn_esb.extract_checkpoint` dengan `finished_at` = versi slice (= `_version` semua baris fakta
slice itu). View hanya membaca baris fakta dari versi slice terakhir, jadi baris yang hilang di ESB (void/koreksi) ikut
hilang dari view setelah hari itu ditarik ulang.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone

from efn_esb.config import BRANCH_SCOPED

# Indonesia tanpa DST: offset tetap (tidak bergantung paket tzdata di image slim).
WIB = timezone(timedelta(hours=7), "WIB")
BRAND_SCOPE = "*"
MAX_BACKFILL_DAYS = 1100
# SEC-185: rentang maksimum `backfill --ignore-window` di production (runbook: "<= 1 hari, dengan persetujuan").
IGNORE_WINDOW_MAX_DAYS_PROD = 1

Checkpoints = Mapping[tuple[str, str, date], datetime]


@dataclass(frozen=True, order=True)
class SliceTask:
    day: date
    entity: str
    scope: str

    @property
    def branch_id(self) -> int:
        return 0 if self.scope == BRAND_SCOPE else int(self.scope)

    @property
    def key(self) -> tuple[str, str, date]:
        return (self.entity, self.scope, self.day)


def wib_now(now: datetime) -> datetime:
    return (now if now.tzinfo else now.replace(tzinfo=UTC)).astimezone(WIB)


def wib_today(now: datetime) -> date:
    return wib_now(now).date()


def in_window(now: datetime, window: tuple[time, time]) -> bool:
    """Jendela [awal, akhir) WIB; boleh melewati tengah malam (22:00-05:00)."""
    current = wib_now(now).time().replace(tzinfo=None)
    start, end = window
    if start < end:
        return start <= current < end
    return current >= start or current < end


def date_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def validate_backfill_range(start: date, end: date, today: date) -> None:
    if start > end:
        raise ValueError("--from harus <= --to")
    if end >= today:
        raise ValueError("--to harus sebelum hari ini (WIB); hari berjalan ditarik mode run")
    if (end - start).days + 1 > MAX_BACKFILL_DAYS:
        raise ValueError(f"rentang backfill maks {MAX_BACKFILL_DAYS} hari")


def validate_ignore_window(start: date, end: date, production: bool, allowed: bool) -> None:
    """SEC-185: `--ignore-window` di production (fail closed) hanya bila `ESB_ALLOW_IGNORE_WINDOW=true` DAN rentang
    <= `IGNORE_WINDOW_MAX_DAYS_PROD` hari (inklusif). Di luar production tidak dibatasi (dev/uji)."""
    if not production:
        return
    if not allowed:
        raise ValueError(
            "--ignore-window ditolak di production (APP_ENV kosong/tidak dikenal = production): set "
            "ESB_ALLOW_IGNORE_WINDOW=true dengan persetujuan (runbook esb-extract) dan batasi rentang ke "
            f"{IGNORE_WINDOW_MAX_DAYS_PROD} hari (--from = --to), atau jalankan tanpa --ignore-window di "
            "ESB_BACKFILL_WINDOW"
        )
    if (end - start).days + 1 > IGNORE_WINDOW_MAX_DAYS_PROD:
        raise ValueError(
            f"--ignore-window di production maks {IGNORE_WINDOW_MAX_DAYS_PROD} hari (--from = --to); "
            "rentang lebih panjang wajib lewat ESB_BACKFILL_WINDOW"
        )


def scopes_for(entity: str, store_ids: Sequence[int]) -> list[str]:
    if entity in BRANCH_SCOPED:
        return [str(branch) for branch in store_ids]
    return [BRAND_SCOPE]


def backfill_tasks(
    entities: Iterable[str],
    days: Sequence[date],
    store_ids: Sequence[int],
    done: Checkpoints,
    force: bool = False,
) -> list[SliceTask]:
    """Slice backfill, hari terbaru dulu; slice yang sudah `done` dilewati kecuali `force` (resumable)."""
    tasks: list[SliceTask] = []
    entity_list = list(entities)
    for day in sorted(days, reverse=True):
        for entity in entity_list:
            for scope in scopes_for(entity, store_ids):
                task = SliceTask(day, entity, scope)
                if force or task.key not in done:
                    tasks.append(task)
    return tasks


def incremental_days(
    now: datetime,
    finished: Mapping[date, datetime],
    correction_days: Sequence[int],
    gap_days: int,
    ready_hour: int,
) -> list[date]:
    """Hari yang perlu ditarik untuk satu (entitas, scope).

    - H-n untuk n di `correction_days` (default 1 dan 7): wajib punya checkpoint yang selesai SETELAH hari ini pukul
      `ready_hour` WIB (sekali per hari, setelah penjualan kemarin final ~03:00 WIB).
    - Gap fill: hari H-1..H-`gap_days` tanpa checkpoint sama sekali (mis. service sempat mati).
    - Sebelum `ready_hour` hari ini tidak ada yang dijadwalkan untuk H-1 (data kemarin belum final).
    """
    local = wib_now(now)
    today = local.date()
    ready_at = datetime.combine(today, time(ready_hour), WIB)
    ready = local >= ready_at
    out: set[date] = set()
    if ready:
        for lag in correction_days:
            day = today - timedelta(days=lag)
            last = finished.get(day)
            if last is None or wib_now(last) < ready_at:
                out.add(day)
    for lag in range(1, gap_days + 1):
        if lag == 1 and not ready:
            continue
        day = today - timedelta(days=lag)
        if day not in finished:
            out.add(day)
    return sorted(out, reverse=True)


def incremental_tasks(
    entities: Iterable[str],
    store_ids: Sequence[int],
    now: datetime,
    checkpoints: Checkpoints,
    correction_days: Sequence[int],
    gap_days: int,
    ready_hour: int,
) -> list[SliceTask]:
    tasks: list[SliceTask] = []
    for entity in entities:
        for scope in scopes_for(entity, store_ids):
            finished = {day: ts for (ent, sc, day), ts in checkpoints.items() if ent == entity and sc == scope}
            for day in incremental_days(now, finished, correction_days, gap_days, ready_hour):
                tasks.append(SliceTask(day, entity, scope))
    return sorted(tasks, key=lambda t: (t.day, t.entity, t.scope), reverse=True)


def snapshot_due(entity: str, checkpoints: Checkpoints, today: date) -> bool:
    """`branch`/`master` sekali per hari WIB."""
    return (entity, BRAND_SCOPE, today) not in checkpoints
