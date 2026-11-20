"""Jadwal cron 5 kolom (menit jam hari-bulan bulan hari-minggu) di zona SEGMENT_TZ. Logika murni; salinan
`services/forecast-worker/efn_forecast/schedule.py` (tanpa paket bersama antar-service).

Subset cron standar: `*`, angka, daftar `a,b`, rentang `a-b`, langkah `*/n` / `a-b/n`. Hari-minggu 0/7 = Minggu.
Bila hari-bulan DAN hari-minggu dibatasi -> cocok bila salah satu cocok (perilaku Vixie cron).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
WIB = timezone(timedelta(hours=7), "WIB")


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0=Minggu..6=Sabtu (cron)
    day_any: bool
    weekday_any: bool


def _field(text: str, lo: int, hi: int) -> frozenset[int]:
    out: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if step < 1:
            raise ValueError("langkah cron harus >= 1")
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            a_text, b_text = base.split("-", 1)
            a, b = int(a_text), int(b_text)
        else:
            a = b = int(base)
            if step_text:
                b = hi
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError("nilai cron di luar rentang")
        out.update(range(a, b + 1, step))
    return frozenset(out)


def parse_cron(expr: str) -> Cron:
    parts = expr.split()
    if len(parts) != 5:
        raise RuntimeError("SEGMENT_DAILY_CRON wajib 5 kolom (menit jam hari bulan hari-minggu)")
    try:
        fields = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES, strict=True)]
    except ValueError:
        raise RuntimeError("SEGMENT_DAILY_CRON tidak valid") from None
    weekdays = frozenset(0 if d == 7 else d for d in fields[4])
    return Cron(fields[0], fields[1], fields[2], fields[3], weekdays, parts[2] == "*", parts[4] == "*")


def zone(name: str) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name in ("Asia/Jakarta", "WIB"):
            return WIB  # Indonesia tanpa DST; image tanpa tzdata tetap benar
        raise RuntimeError("SEGMENT_TZ tidak dikenal") from None


def _matches_day(cron: Cron, local: datetime) -> bool:
    if local.month not in cron.months:
        return False
    dom = local.day in cron.days
    dow = ((local.weekday() + 1) % 7) in cron.weekdays
    if cron.day_any and cron.weekday_any:
        return True
    if cron.day_any:
        return dow
    if cron.weekday_any:
        return dom
    return dom or dow


def next_fire(after: datetime, cron: Cron, tz: tzinfo) -> datetime:
    """Waktu UTC pertama > `after` yang cocok dengan cron di zona `tz`."""
    local = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if _matches_day(cron, local):
            if local.hour in cron.hours and local.minute in cron.minutes:
                return local.astimezone(UTC)
            local += timedelta(minutes=1)
        else:
            local = (local + timedelta(days=1)).replace(hour=0, minute=0)
    raise RuntimeError("cron tidak pernah cocok dalam setahun")
