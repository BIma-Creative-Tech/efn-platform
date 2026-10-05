"""Logika watermark & cursor keyset (murni, tanpa I/O).

Model:
- Watermark per job = `max(_cursor_ts)` baris yang SUDAH berhasil ditulis ke ClickHouse.
- Run berikutnya mulai dari `watermark - overlap` (default 5 menit) untuk menangkap late commit: `now()` /
  `write_date` diisi saat transaksi MULAI, sehingga transaksi panjang bisa commit dengan timestamp lebih tua
  dari watermark. Baris yang terbaca ulang tidak menggandakan data karena target `ReplacingMergeTree`.
- Dalam satu run, halaman berikutnya memakai cursor keyset `(_cursor_ts, _cursor_key)` sehingga tidak ada
  loop tak berujung walau ribuan baris punya timestamp sama.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def to_utc(value: datetime) -> datetime:
    """Naive dianggap UTC (Odoo `write_date` = timestamp tanpa zona, UTC); aware dikonversi ke UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def normalize_watermark(value: datetime | None) -> datetime | None:
    """ClickHouse mengembalikan 1970-01-01 untuk argMax pada set kosong; perlakukan sebagai belum ada."""
    if value is None:
        return None
    utc = to_utc(value)
    return None if utc <= EPOCH else utc


def window_start(watermark: datetime | None, overlap: timedelta) -> datetime:
    if watermark is None:
        return EPOCH
    return max(EPOCH, to_utc(watermark) - overlap)


@dataclass(frozen=True)
class Cursor:
    ts: datetime
    key: str


def initial_cursor(watermark: datetime | None, overlap: timedelta) -> Cursor:
    return Cursor(ts=window_start(watermark, overlap), key="")


def cursor_after(rows: list[dict[str, object]]) -> Cursor:
    """Cursor dari baris terakhir halaman (SQL job wajib ORDER BY _cursor_ts, _cursor_key)."""
    last = rows[-1]
    ts = last["_cursor_ts"]
    if not isinstance(ts, datetime):
        raise TypeError("_cursor_ts harus datetime")
    return Cursor(ts=to_utc(ts), key=str(last["_cursor_key"]))


def next_watermark(current: datetime | None, rows: list[dict[str, object]]) -> datetime | None:
    """Watermark tidak pernah mundur."""
    candidates = [to_utc(ts) for ts in (r.get("_cursor_ts") for r in rows) if isinstance(ts, datetime)]
    if current is not None:
        candidates.append(to_utc(current))
    return max(candidates) if candidates else None
