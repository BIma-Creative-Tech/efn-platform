from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from efn_etl.stock import (
    ORDERPOINT_COLUMNS,
    SQL_ORDERPOINTS,
    SQL_STOCK_QUANTS,
    STOCK_COLUMNS,
    SnapshotIncomplete,
    run_stock_snapshot,
    snapshot_date,
    snapshot_due,
    transform_orderpoint,
    transform_quant,
)

NOW = datetime(2026, 10, 7, 16, 30, tzinfo=UTC)  # 23:30 WIB


def _quant(i: int, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "quant_id": i,
        "company_id": 4,
        "company_name": "PT Paris Baguette",
        "warehouse_id": 2,
        "warehouse_name": "WH PB",
        "location_id": 32,
        "location_name": "PB/Stock",
        "product_id": 9,
        "lot_id": 0,
        "lot_name": "",
        "expiration_date": None,
        "quantity": Decimal("12"),
        "reserved_quantity": Decimal("0"),
        "unit_cost": "15000.0",
        "in_date": datetime(2026, 10, 1, 2, 0),
    }
    row.update(overrides)
    return row


def _op(i: int) -> dict[str, Any]:
    return {
        "orderpoint_id": i,
        "company_id": 4,
        "company_name": "PT Paris Baguette",
        "warehouse_id": 2,
        "warehouse_name": "WH",
        "location_id": 32,
        "product_id": 9,
        "product_min_qty": Decimal("50"),
        "product_max_qty": Decimal("120"),
        "trigger": "manual",
    }


class FakeTx:
    def __init__(self, quants: list[dict[str, Any]], ops: list[dict[str, Any]]) -> None:
        self.data = {SQL_STOCK_QUANTS: (quants, "quant_id"), SQL_ORDERPOINTS: (ops, "orderpoint_id")}
        self.calls: list[dict[str, Any]] = []

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append(params)
        rows, key = self.data[sql]
        return [r for r in rows if r[key] > params["after_id"]][: params["limit"]]


class FakeSink:
    def __init__(self, fail_table: str | None = None) -> None:
        self.tables: list[str] = []
        self.rows: dict[str, list[list[Any]]] = {}
        self.fail_table = fail_table

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None:
        if table == self.fail_table:
            raise ConnectionError("clickhouse down")
        self.tables.append(table)
        self.rows.setdefault(table, []).extend(rows)


def test_snapshot_date_uses_wib() -> None:
    assert snapshot_date(datetime(2026, 10, 7, 17, 0, tzinfo=UTC)) == date(2026, 10, 8)
    assert snapshot_date(datetime(2026, 10, 7, 16, 59, tzinfo=UTC)) == date(2026, 10, 7)
    assert snapshot_date(datetime(2026, 10, 7, 17, 0)) == date(2026, 10, 8)  # naive = UTC


def test_snapshot_due() -> None:
    hour = timedelta(hours=1)
    assert snapshot_due(None, NOW, hour)
    assert not snapshot_due(NOW - timedelta(minutes=10), NOW, hour)
    assert snapshot_due(NOW - hour, NOW, hour)
    # Ganti tanggal WIB (23:30 -> 00:05) memicu snapshot walau < interval.
    assert snapshot_due(NOW, NOW + timedelta(minutes=35), hour)


def test_run_snapshot_pages_and_writes_marker_last() -> None:
    tx = FakeTx([_quant(i) for i in range(1, 6)], [_op(1)])
    sink = FakeSink()
    result = run_stock_snapshot(tx, sink, NOW, batch_size=2, max_pages=10)
    assert result.quants == 5 and result.orderpoints == 1
    assert sink.tables[-1] == "stock_snapshot_run"
    assert sink.rows["stock_snapshot_run"] == [[date(2026, 10, 7), NOW, 5, 1]]
    assert len(sink.rows["fact_stock_snapshot"]) == 5
    assert [c["after_id"] for c in tx.calls[:3]] == [0, 2, 4]
    # Semua baris satu snapshot memakai snapshot_ts yang sama (dipakai view untuk memilih snapshot).
    assert {r[1] for r in sink.rows["fact_stock_snapshot"]} == {NOW}


def test_empty_snapshot_still_marked() -> None:
    sink = FakeSink()
    result = run_stock_snapshot(FakeTx([], []), sink, NOW, batch_size=2, max_pages=10)
    assert result.quants == 0
    assert sink.rows["stock_snapshot_run"] == [[date(2026, 10, 7), NOW, 0, 0]]


def test_incomplete_snapshot_has_no_marker() -> None:
    tx = FakeTx([_quant(i) for i in range(1, 10)], [])
    sink = FakeSink()
    with pytest.raises(SnapshotIncomplete):
        run_stock_snapshot(tx, sink, NOW, batch_size=2, max_pages=2)
    assert "stock_snapshot_run" not in sink.rows


def test_sink_failure_has_no_marker() -> None:
    sink = FakeSink(fail_table="fact_orderpoint_snapshot")
    with pytest.raises(ConnectionError):
        run_stock_snapshot(FakeTx([_quant(1)], [_op(1)]), sink, NOW, batch_size=10, max_pages=10)
    assert "stock_snapshot_run" not in sink.rows


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("15000.0", Decimal("15000.0")), (None, None), ("", None), ("abc", None), ("NaN", None), ("Infinity", None)],
)
def test_unit_cost_parsing(raw: Any, expected: Decimal | None) -> None:
    out = dict(zip(STOCK_COLUMNS, transform_quant(_quant(1, unit_cost=raw), NOW), strict=True))
    assert out["unit_cost"] == expected


def test_transform_quant_expiration_and_lot() -> None:
    exp = datetime(2026, 10, 10, 5, 0)
    out = dict(zip(STOCK_COLUMNS, transform_quant(_quant(1, lot_id=None, expiration_date=exp), NOW), strict=True))
    assert out["lot_id"] == 0
    assert out["expiration_date"] == exp.replace(tzinfo=UTC)
    assert out["snapshot_ts"] == NOW and out["snapshot_date"] == date(2026, 10, 7)


def test_transform_orderpoint() -> None:
    out = dict(zip(ORDERPOINT_COLUMNS, transform_orderpoint(_op(3), NOW), strict=True))
    assert out["min_qty"] == Decimal("50") and out["trigger"] == "manual"


def test_stock_sql_privacy_and_scope() -> None:
    assert "owner_id" not in SQL_STOCK_QUANTS and "user_id" not in SQL_STOCK_QUANTS
    assert "loc.usage = 'internal'" in SQL_STOCK_QUANTS
    assert "SELECT *" not in SQL_STOCK_QUANTS.upper() and "SELECT *" not in SQL_ORDERPOINTS.upper()
    assert "op.active" in SQL_ORDERPOINTS
