"""G22: mart KPI dapur dari tiket KDS (efn_etl/kds.py) — transformasi murni, SQL tanpa PII, pemilihan kanal."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from efn_etl import kds
from efn_etl.jobs import enabled_jobs

SENT = datetime(2026, 10, 19, 5, 0, 0)  # naive = UTC (Odoo)
SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
CH_INIT = Path(__file__).resolve().parents[3] / "infra" / "clickhouse" / "init" / "10-g22-kds-online-points.sql"


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticket_id": 11,
        "company_id": 3,
        "brand": "PB",
        "channel": "pos",
        "station_id": 2,
        "store_id": 7,
        "order_id": 501,
        "online_order_id": None,
        "preset_name": "Makan di tempat",
        "course_index": 1,
        "sequence_no": 2,
        "state": "ready",
        "is_rush": True,
        "printed_offline": False,
        "sent_at": SENT,
        "started_at": SENT + timedelta(minutes=2),
        "ready_at": SENT + timedelta(minutes=9),
        "served_at": None,
        "cancelled_at": None,
        "lines": 3,
        "items": Decimal("4"),
        "items_cancelled": Decimal("1"),
        "write_date": SENT + timedelta(minutes=9),
    }
    row.update(over)
    return row


def _as_dict(values: list[Any]) -> dict[str, Any]:
    return dict(zip(kds.KDS_TICKET_COLUMNS, values, strict=True))


def test_transform_ticket_durations_and_ids() -> None:
    out = _as_dict(kds.transform_kds_ticket(_row()))
    assert out["sent_at"] == SENT.replace(tzinfo=UTC)
    assert (out["queue_seconds"], out["cook_seconds"], out["wait_seconds"], out["serve_seconds"]) == (
        120,
        420,
        540,
        None,
    )
    assert (out["channel"], out["order_id"], out["online_order_id"], out["store_id"]) == ("pos", 501, 0, 7)
    assert (out["is_rush"], out["printed_offline"], out["lines"]) == (1, 0, 3)
    assert out["items"] == Decimal("4") and out["items_cancelled"] == Decimal("1")


def test_transform_online_ticket_without_pos_order() -> None:
    row = _row(
        channel="online",
        order_id=None,
        online_order_id=88,
        store_id=None,
        state="served",
        served_at=SENT + timedelta(minutes=12),
    )
    out = _as_dict(kds.transform_kds_ticket(row))
    assert (out["channel"], out["order_id"], out["online_order_id"], out["store_id"]) == ("online", 0, 88, 0)
    assert out["serve_seconds"] == 180


def test_negative_or_missing_durations_are_null() -> None:
    # Recall / jam perangkat: ready sebelum started -> NULL, bukan angka negatif (kolom UInt32).
    out = _as_dict(kds.transform_kds_ticket(_row(started_at=SENT + timedelta(minutes=10))))
    assert out["cook_seconds"] is None and out["queue_seconds"] == 600 and out["wait_seconds"] == 540
    new = _as_dict(kds.transform_kds_ticket(_row(state="new", started_at=None, ready_at=None)))
    assert (new["queue_seconds"], new["cook_seconds"], new["wait_seconds"]) == (None, None, None)
    assert kds.duration_seconds(None, SENT) is None


@pytest.mark.parametrize("over", [{"state": "done"}, {"state": None}, {"channel": "qr"}])
def test_unknown_state_or_channel_fails_page(over: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        kds.transform_kds_ticket(_row(**over))


def test_preset_label_truncated() -> None:
    out = _as_dict(kds.transform_kds_ticket(_row(preset_name="x" * 200)))
    assert len(out["preset_name"]) == 64


def test_station_transform_and_mode_check() -> None:
    row = {
        "station_id": 2,
        "name": " Dapur Panas ",
        "company_id": 3,
        "mode": "kds",
        "warn_minutes": 10,
        "late_minutes": 15,
        "active": True,
        "write_date": SENT,
    }
    assert kds.transform_kds_station(row) == [2, "Dapur Panas", 3, "kds", 10, 15, 1, SENT.replace(tzinfo=UTC)]
    with pytest.raises(ValueError):
        kds.transform_kds_station({**row, "mode": "screen"})


# Kolom yang TIDAK boleh dibaca ETL (PDP_DATA_INVENTORY §2.2b, laporan architect G15 #3).
FORBIDDEN = (
    "table_label",
    "tracking_number",
    "internal_note",
    "note",
    "product_name",
    "attribute_text",
    "combo_parent_name",
    "access_token",
    "device_user_ids",
    "partner",
    "session_id",
)


@pytest.mark.parametrize("sql", [kds.kds_tickets_sql(False), kds.kds_tickets_sql(True), kds.SQL_KDS_STATIONS])
def test_sql_minimised_and_keyset(sql: str) -> None:
    words = set(re.findall(r"[a-z_]+", sql))
    assert not words & set(FORBIDDEN)
    assert "select *" not in sql.lower()
    assert "ORDER BY" in sql and "%(cursor_ts)s" in sql and "%(cursor_key)s" in sql and "LIMIT %(limit)s" in sql
    assert "_cursor_ts" in sql and "_cursor_key" in sql


def test_online_column_only_when_enabled() -> None:
    assert "efn_online_order_id" not in kds.kds_tickets_sql(False)
    assert "efn_online_order_id" in kds.kds_tickets_sql(True)


def test_enabled_jobs_kds_flag() -> None:
    names = [j.name for j in enabled_jobs(True)]
    assert "kds_tickets" not in names
    jobs = {j.name: j for j in enabled_jobs(True, kds=True, kds_online=True)}
    assert jobs["kds_tickets"].target_table == "fact_kds_ticket"
    assert "efn_online_order_id" in jobs["kds_tickets"].sql
    assert jobs["kds_stations"].target_table == "dim_kds_station"
    assert "kds_tickets" not in [j.name for j in enabled_jobs(False, kds=True)]  # tanpa Odoo -> tidak ada


def test_grants_and_clickhouse_columns_match_sql() -> None:
    grants = (SQL_DIR / "odoo-readonly-grants.sql").read_text(encoding="utf-8")
    block = grants.split("Gelombang 22 (efn-data-ai-engineer)", 1)[1]
    ticket_grant = re.search(r"GRANT SELECT \(([^)]*)\)\s+ON public\.efn_kds_ticket TO efn_etl", block)
    assert ticket_grant
    granted = {c.strip() for c in ticket_grant.group(1).split(",")}
    read = set(re.findall(r"\bt\.([a-z_]+)", kds.kds_tickets_sql(False)))
    assert read <= granted, read - granted
    assert not granted & set(FORBIDDEN)
    station_grant = re.search(r"GRANT SELECT \(([^)]*)\)\s+ON public\.efn_kds_station TO efn_etl", block)
    assert station_grant and "access_token" not in station_grant.group(1)
    ddl = CH_INIT.read_text(encoding="utf-8")
    table = ddl.split("CREATE TABLE IF NOT EXISTS efn_dwh.fact_kds_ticket", 1)[1].split("ENGINE", 1)[0]
    for column in kds.KDS_TICKET_COLUMNS:
        assert re.search(rf"^\s+{column}\s", table, re.MULTILINE), column
