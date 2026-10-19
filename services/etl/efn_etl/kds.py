"""Gelombang 22 (docs/04 §14.B B-6, docs/08 §7.3 F-54 / §13.2 E-29a): mart KPI dapur dari tiket KDS Odoo.

Sumber (DB Odoo, grant `sql/odoo-readonly-grants.sql` bagian G22):
- `efn_kds_ticket` -> `fact_kds_ticket` (satu baris per tiket = order x stasiun x pengiriman). Cursor `write_date`
  (setiap bump/recall menulis ulang tiket -> versi terbaru menang di ReplacingMergeTree(write_date)).
- `efn_kds_station` -> `dim_kds_station` (nama stasiun + target menit warn/late, PLACEHOLDER Q-19).

Kanal: `pos` (order POS/QR, `efn_kds_ticket.order_id`) | `online` (pesanan F&B online lewat jembatan SO E-29a,
`efn_kds_ticket.efn_online_order_id`). Kolom `efn_online_order_id` baru ada setelah `efn_online_kds_bridge` terpasang
(docs/08 §10.6 "order_id opsional + efn_online_order_id") -> dibaca hanya bila `ETL_KDS_ONLINE_ENABLED=true`; mati =
semua tiket `pos`, tanpa menyentuh kolom itu (job tidak gagal sebelum E-29a dipasang).

PDP / minimisasi (PDP_DATA_INVENTORY §2.2b, laporan architect G15 #3): TIDAK dibaca `table_label`, `tracking_number`,
`internal_note`, catatan/nama produk/atribut baris, `session_id`, maupun data pelanggan (tiket memang tidak
menyimpannya). Yang disalin hanya id teknis (tiket, stasiun, config, order), status, cap waktu, dan hitungan baris.
`efn_kds_station.access_token`/`device_user_ids`/`last_seen` tidak di-grant.

Durasi (detik, NULL bila cap waktu belum ada / negatif karena jam perangkat):
- `queue_seconds` = started_at - sent_at (antre sebelum mulai dimasak);
- `cook_seconds`  = ready_at - started_at (waktu masak);
- `wait_seconds`  = ready_at - sent_at (KPI F-54 "waktu tunggu", sama dengan field Odoo `wait_seconds`);
- `serve_seconds` = served_at - ready_at (siap sampai disajikan / diambil).
Tiket cetak cadangan offline (`printed_offline`) dibuat langsung `in_progress` -> `queue_seconds` = 0.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from efn_etl.jobs import Job
from efn_etl.watermark import to_utc

KDS_STATES = ("new", "in_progress", "ready", "served", "cancelled")
KDS_CHANNELS = ("pos", "online")
STATION_MODES = ("kds", "kds_printer", "printer")
_MAX_LABEL = 64

# ---------------------------------------------------------------- efn_kds_ticket -> fact_kds_ticket
CHANNEL_ONLINE_SELECT = (
    "CASE WHEN t.efn_online_order_id IS NOT NULL THEN 'online' ELSE 'pos' END AS channel, "
    "t.efn_online_order_id AS online_order_id"
)
CHANNEL_POS_SELECT = "'pos'::text AS channel, NULL::integer AS online_order_id"

_SQL_KDS_TICKETS = """
SELECT t.id AS ticket_id, t.company_id, COALESCE(c.efn_brand_code, '') AS brand, {channel_select},
       t.station_id, t.config_id AS store_id, t.order_id, COALESCE(t.preset_name, '') AS preset_name,
       COALESCE(t.course_index, 0) AS course_index, COALESCE(t.sequence_no, 0) AS sequence_no, t.state,
       COALESCE(t.is_rush, FALSE) AS is_rush, COALESCE(t.printed_offline, FALSE) AS printed_offline,
       t.sent_at, t.started_at, t.ready_at, t.served_at, t.cancelled_at,
       (SELECT count(*) FROM efn_kds_ticket_line l WHERE l.ticket_id = t.id) AS lines,
       (SELECT COALESCE(sum(l.qty_delta), 0) FROM efn_kds_ticket_line l WHERE l.ticket_id = t.id AND l.qty_delta > 0)
           AS items,
       (SELECT COALESCE(-sum(l.qty_delta), 0) FROM efn_kds_ticket_line l WHERE l.ticket_id = t.id AND l.qty_delta < 0)
           AS items_cancelled,
       t.write_date,
       t.write_date AS _cursor_ts, t.id::text AS _cursor_key
FROM efn_kds_ticket t
JOIN res_company c ON c.id = t.company_id
WHERE t.write_date >= %(cursor_ts)s
  AND (t.write_date, t.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY t.write_date, t.id::text
LIMIT %(limit)s
"""


def kds_tickets_sql(online: bool = False) -> str:
    return _SQL_KDS_TICKETS.format(channel_select=CHANNEL_ONLINE_SELECT if online else CHANNEL_POS_SELECT)


KDS_TICKET_COLUMNS = (
    "ticket_id",
    "company_id",
    "brand",
    "channel",
    "station_id",
    "store_id",
    "order_id",
    "online_order_id",
    "preset_name",
    "course_index",
    "sequence_no",
    "state",
    "is_rush",
    "printed_offline",
    "sent_at",
    "started_at",
    "ready_at",
    "served_at",
    "cancelled_at",
    "queue_seconds",
    "cook_seconds",
    "wait_seconds",
    "serve_seconds",
    "lines",
    "items",
    "items_cancelled",
    "write_date",
)


def _opt_ts(value: Any) -> datetime | None:
    return to_utc(value) if isinstance(value, datetime) else None


def duration_seconds(start: datetime | None, end: datetime | None) -> int | None:
    """Detik dari `start` ke `end`; None bila salah satu kosong atau negatif (jam/recall tidak konsisten)."""
    if start is None or end is None:
        return None
    seconds = int((to_utc(end) - to_utc(start)).total_seconds())
    return seconds if seconds >= 0 else None


def _label(value: Any) -> str:
    return ("" if value is None else str(value).strip())[:_MAX_LABEL]


def transform_kds_ticket(row: dict[str, Any]) -> list[Any]:
    state = str(row["state"] or "")
    if state not in KDS_STATES:
        raise ValueError("efn_kds_ticket.state tidak dikenal")
    channel = str(row.get("channel") or "pos")
    if channel not in KDS_CHANNELS:
        raise ValueError("kanal tiket KDS tidak dikenal")
    online_order_id = row.get("online_order_id")
    sent_at = to_utc(row["sent_at"])
    started_at = _opt_ts(row.get("started_at"))
    ready_at = _opt_ts(row.get("ready_at"))
    served_at = _opt_ts(row.get("served_at"))
    return [
        int(row["ticket_id"]),
        int(row["company_id"]),
        str(row.get("brand") or ""),
        channel,
        int(row["station_id"]),
        int(row.get("store_id") or 0),
        int(row.get("order_id") or 0),
        int(online_order_id) if online_order_id is not None else 0,
        _label(row.get("preset_name")),
        int(row.get("course_index") or 0),
        int(row.get("sequence_no") or 0),
        state,
        1 if row.get("is_rush") else 0,
        1 if row.get("printed_offline") else 0,
        sent_at,
        started_at,
        ready_at,
        served_at,
        _opt_ts(row.get("cancelled_at")),
        duration_seconds(sent_at, started_at),
        duration_seconds(started_at, ready_at),
        duration_seconds(sent_at, ready_at),
        duration_seconds(ready_at, served_at),
        int(row.get("lines") or 0),
        Decimal(str(row.get("items") or 0)),
        Decimal(str(row.get("items_cancelled") or 0)),
        to_utc(row["write_date"]),
    ]


# ---------------------------------------------------------------- efn_kds_station -> dim_kds_station
SQL_KDS_STATIONS = """
SELECT s.id AS station_id, COALESCE(s.name, '') AS name, s.company_id, s.mode, s.warn_minutes, s.late_minutes,
       COALESCE(s.active, FALSE) AS active, s.write_date,
       s.write_date AS _cursor_ts, s.id::text AS _cursor_key
FROM efn_kds_station s
WHERE s.write_date >= %(cursor_ts)s
  AND (s.write_date, s.id::text) > (%(cursor_ts)s, %(cursor_key)s)
ORDER BY s.write_date, s.id::text
LIMIT %(limit)s
"""
KDS_STATION_COLUMNS = (
    "station_id",
    "name",
    "company_id",
    "mode",
    "warn_minutes",
    "late_minutes",
    "active",
    "write_date",
)


def transform_kds_station(row: dict[str, Any]) -> list[Any]:
    mode = str(row["mode"] or "")
    if mode not in STATION_MODES:
        raise ValueError("efn_kds_station.mode tidak dikenal")
    return [
        int(row["station_id"]),
        _label(row["name"]),
        int(row["company_id"]),
        mode,
        int(row.get("warn_minutes") or 0),
        int(row.get("late_minutes") or 0),
        1 if row.get("active") else 0,
        to_utc(row["write_date"]),
    ]


def kds_jobs(online: bool = False) -> tuple[Job, Job]:
    """Job incremental KDS. `online` = baca `efn_online_order_id` (E-29a terpasang, ETL_KDS_ONLINE_ENABLED)."""
    return (
        Job("kds_stations", "odoo", SQL_KDS_STATIONS, "dim_kds_station", KDS_STATION_COLUMNS, transform_kds_station),
        Job(
            "kds_tickets",
            "odoo",
            kds_tickets_sql(online),
            "fact_kds_ticket",
            KDS_TICKET_COLUMNS,
            transform_kds_ticket,
        ),
    )
