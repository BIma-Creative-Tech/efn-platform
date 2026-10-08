"""Rekonsiliasi loyalty ledger BFF <-> pos.order Odoo (G14, sisa SEC-61)."""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from efn_etl import loyalty_recon as lr
from efn_etl.loyalty_recon import (
    FINDING_COLUMNS,
    RUN_COLUMNS,
    LedgerEntry,
    LoyaltyReconSettings,
    OdooOrder,
    PointsLines,
    ReconInputs,
    ReconReport,
    Reservation,
    recon_due,
    reconcile,
    reverse_refund_uuid,
    run_loyalty_recon,
)

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(hours=10)
SETTLE = timedelta(hours=6)


def _order(oid: int, uuid: str, **kw: Any) -> OdooOrder:
    base: dict[str, Any] = {
        "id": oid,
        "uuid": uuid,
        "company_code": "FNB",
        "state": "paid",
        "is_refund": False,
        "has_partner": True,
        "loyalty_state": "done",
        "date_order": OLD,
        "earn_enabled": True,
    }
    base.update(kw)
    return OdooOrder(**base)


def _clean_inputs() -> ReconInputs:
    """Skenario smoke `l`: earn, redeem 60 poin (Rp6.000), refund 50 % dengan restore + clawback."""
    orders = {
        "u-earn": _order(1, "u-earn"),
        "u-redeem": _order(2, "u-redeem"),
        "u-refund": _order(3, "u-refund", is_refund=True),
        "u-nomember": _order(4, "u-nomember", has_partner=False),
    }
    ledger = [
        LedgerEntry(10, "FNB", 100, "pos_earn", "u-earn", "pos_earn:u-earn"),
        LedgerEntry(11, "FNB", -60, "pos_redeem", "u-redeem", "pos_redeem:u-redeem"),
        LedgerEntry(12, "FNB", 19, "pos_earn", "u-redeem", "pos_earn:u-redeem"),
        LedgerEntry(13, "FNB", 30, "pos_reverse", "u-redeem", "pos_reverse:u-refund:restore#1"),
        LedgerEntry(14, "FNB", -10, "pos_reverse", "u-redeem", "pos_reverse:u-refund:claw"),
    ]
    return ReconInputs(
        earn_refs={"u-earn": "FNB", "u-redeem": "FNB"},
        ledger=ledger,
        orders_by_uuid=orders,
        window_uuids=set(orders),
        points_lines={2: PointsLines(1, Decimal("-6000.00")), 3: PointsLines(1, Decimal("3000.00"))},
        outbox_earn_states={1: {"done"}, 2: {"done"}},
        reservations={"u-redeem": Reservation(60, 6000)},
    )


def _kinds(report: ReconReport) -> dict[str, int]:
    return report.by_kind()


def test_reverse_key_parsing() -> None:
    assert reverse_refund_uuid("pos_reverse:abc-1:claw") == "abc-1"
    assert reverse_refund_uuid("pos_reverse:abc-1:restore#2") == "abc-1"
    assert reverse_refund_uuid("pos_earn:abc") is None
    assert reverse_refund_uuid("") is None


def test_clean_smoke_flow_has_no_findings() -> None:
    report = reconcile(_clean_inputs(), NOW, SETTLE)
    assert report.anomalies == 0 and _kinds(report) == {}
    assert report.ledger_checked == 5 and report.orders_checked == 4


def test_earn_without_order_fictitious_draft_or_refund() -> None:
    inputs = _clean_inputs()
    inputs.earn_refs["u-ghost"] = "RTL"
    inputs.ledger.append(LedgerEntry(20, "RTL", 50, "pos_earn", "u-ghost", "pos_earn:u-ghost"))
    inputs.orders_by_uuid["u-draft"] = _order(5, "u-draft", state="draft")
    inputs.earn_refs["u-draft"] = "FNB"
    inputs.earn_refs["u-refund"] = "FNB"
    report = reconcile(inputs, NOW, SETTLE)
    assert _kinds(report) == {"earn_without_paid_order": 3}
    ghost = report.findings[("earn_without_paid_order", "RTL")]
    assert ghost.ledger_ids == [20] and ghost.order_ids == []
    assert report.anomalies == 3


def test_paid_member_order_without_earn_respects_settle_review_and_outbox() -> None:
    inputs = _clean_inputs()
    for oid, uuid, kw, states in [
        (6, "u-missing", {}, set()),  # temuan
        (7, "u-young", {"date_order": NOW - timedelta(hours=1)}, set()),  # belum settle
        (8, "u-review", {"loyalty_state": "review"}, set()),  # dilaporkan sebagai open item
        (9, "u-limit", {}, {"failed"}),  # earn_limit_exceeded
        (10, "u-skip", {}, {"skipped"}),  # member tanpa HP Indonesia
        (11, "u-off", {"earn_enabled": False}, set()),
        (12, "u-pending", {}, {"pending"}),  # outbox macet -> tetap temuan
    ]:
        inputs.orders_by_uuid[uuid] = _order(oid, uuid, **kw)
        inputs.window_uuids.add(uuid)
        if states:
            inputs.outbox_earn_states[oid] = states
    report = reconcile(inputs, NOW, SETTLE)
    finding = report.findings[("paid_member_order_without_earn", "FNB")]
    assert finding.count == 2 and finding.order_ids == [6, 12]


def test_redeem_without_points_line_and_points_mismatch() -> None:
    inputs = _clean_inputs()
    inputs.points_lines.pop(2)
    report = reconcile(inputs, NOW, SETTLE)
    assert _kinds(report) == {"redeem_without_points_line": 1}

    inputs = _clean_inputs()
    inputs.reservations["u-redeem"] = Reservation(50, 6000)
    assert _kinds(reconcile(inputs, NOW, SETTLE)) == {"redeem_points_mismatch": 1}

    inputs = _clean_inputs()
    inputs.ledger.append(LedgerEntry(30, "EFN", -40, "pos_redeem", "u-nowhere", "pos_redeem:u-nowhere"))
    kinds = _kinds(reconcile(inputs, NOW, SETTLE))
    assert kinds == {"redeem_without_points_line": 1, "redeem_points_mismatch": 1}


@pytest.mark.parametrize(
    ("total", "count", "expected"),
    [
        (Decimal("-6000.00"), 1, {}),
        (Decimal("-6001.00"), 1, {}),  # toleransi Rp1 per baris
        (Decimal("-6002.00"), 1, {"points_line_value_mismatch": 1}),
        (Decimal("-9000.00"), 2, {"points_line_value_mismatch": 1}),
    ],
)
def test_points_line_value_vs_reservation(total: Decimal, count: int, expected: dict[str, int]) -> None:
    inputs = _clean_inputs()
    inputs.points_lines[2] = PointsLines(count, total)
    assert _kinds(reconcile(inputs, NOW, SETTLE)) == expected


def test_points_line_without_committed_reservation() -> None:
    inputs = _clean_inputs()
    inputs.orders_by_uuid["u-free"] = _order(15, "u-free")
    inputs.window_uuids.add("u-free")
    inputs.earn_refs["u-free"] = "FNB"
    inputs.points_lines[15] = PointsLines(1, Decimal("-5000"))
    inputs.orders_by_uuid["u-free-review"] = _order(16, "u-free-review", loyalty_state="review")
    inputs.window_uuids.add("u-free-review")
    inputs.points_lines[16] = PointsLines(1, Decimal("-5000"))
    assert _kinds(reconcile(inputs, NOW, SETTLE)) == {"points_line_without_redeem": 1}


def test_reverse_without_refund_order() -> None:
    inputs = _clean_inputs()
    inputs.orders_by_uuid.pop("u-refund")
    inputs.window_uuids.discard("u-refund")
    report = reconcile(inputs, NOW, SETTLE)
    assert _kinds(report) == {"reverse_without_refund_order": 2}  # restore + claw
    assert report.findings[("reverse_without_refund_order", "FNB")].ledger_ids == [13, 14]

    inputs = _clean_inputs()
    inputs.orders_by_uuid["u-refund"] = _order(3, "u-refund", is_refund=False)  # bukan refund
    assert "reverse_without_refund_order" in _kinds(reconcile(inputs, NOW, SETTLE))


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        ReconReport().add("bogus", "FNB")


# ---------------------------------------------------------------- orkestrasi dengan fake I/O


class FakePlatform:
    def __init__(self, inputs: ReconInputs) -> None:
        self.inputs = inputs

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert "member_hash" not in sql and "member_ref" not in sql  # PDP: tidak membaca identitas
        if sql == lr.SQL_PLATFORM_EARNS:
            return [{"order_ref": r, "company_code": c, "points_earned": 1} for r, c in self.inputs.earn_refs.items()]
        if sql == lr.SQL_PLATFORM_LEDGER:
            return [
                {
                    "id": e.id,
                    "company_code": e.company_code,
                    "delta_points": e.delta_points,
                    "source": e.source,
                    "source_ref": e.source_ref,
                    "idempotency_key": e.idempotency_key,
                }
                for e in self.inputs.ledger
            ]
        if sql == lr.SQL_PLATFORM_EARNS_BY_REF:
            return [{"order_ref": r} for r in params["refs"] if r in self.inputs.earn_refs]
        if sql == lr.SQL_PLATFORM_RESERVATIONS:
            return [
                {"order_ref": r, "points": v.points, "value_idr": v.value_idr}
                for r, v in self.inputs.reservations.items()
                if r in params["refs"]
            ]
        raise AssertionError(sql)


class FakeOdoo:
    def __init__(self, inputs: ReconInputs, window_only: set[str]) -> None:
        self.inputs = inputs
        self.window_only = window_only
        self.by_uuid_calls = 0

    def _row(self, o: OdooOrder) -> dict[str, Any]:
        return {
            "id": o.id,
            "uuid": o.uuid,
            "company_id": 3,
            "state": o.state,
            "is_refund": o.is_refund,
            "has_refund_lines": False,
            "has_partner": o.has_partner,
            "efn_loyalty_state": o.loyalty_state,
            "date_order": o.date_order,
            "earn_enabled": o.earn_enabled,
        }

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert "partner_id," not in sql and "member" not in sql
        orders = self.inputs.orders_by_uuid
        if sql == lr.SQL_ODOO_COMPANIES:
            return [{"id": 3, "efn_brand_code": "FNB"}]
        if sql == lr.SQL_ODOO_ORDERS_WINDOW:
            return [self._row(o) for u, o in orders.items() if u in self.window_only and o.has_partner]
        if sql == lr.SQL_ODOO_ORDERS_BY_UUID:
            self.by_uuid_calls += 1
            return [self._row(orders[u]) for u in params["uuids"] if u in orders]
        if sql == lr.SQL_ODOO_POINTS_LINES:
            return [
                {"order_id": i, "n": p.count, "total": p.total_incl}
                for i, p in self.inputs.points_lines.items()
                if i in params["ids"]
            ]
        if sql == lr.SQL_ODOO_OUTBOX_BY_ORDERS:
            return [
                {"order_id": i, "kind": "earn", "state": s}
                for i, states in self.inputs.outbox_earn_states.items()
                for s in states
                if i in params["ids"]
            ]
        if sql == lr.SQL_ODOO_OUTBOX_OPEN:
            return [{"company_id": 3, "state": "failed", "n": 1, "sample": [99]}]
        if sql == lr.SQL_ODOO_ORDER_REVIEW:
            return [{"company_id": 3, "n": 2, "sample": [98, 99]}]
        if sql == lr.SQL_ODOO_RESERVATION_REVIEW:
            return []
        raise AssertionError(sql)


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, list[list[Any]]] = {}

    def last_loyalty_recon(self) -> datetime | None:
        runs = self.rows.get(lr.RUN_TABLE)
        return runs[-1][0] if runs else None

    def insert(self, table: str, columns: Any, rows: list[list[Any]]) -> None:
        expected = RUN_COLUMNS if table == lr.RUN_TABLE else FINDING_COLUMNS
        assert tuple(columns) == expected
        self.rows.setdefault(table, []).extend(rows)


def test_run_loyalty_recon_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    inputs = _clean_inputs()
    # Order asal redeem di luar jendela (diambil per uuid dari ledger)
    platform, odoo, store = FakePlatform(inputs), FakeOdoo(inputs, {"u-earn", "u-nomember"}), FakeStore()
    cfg = LoyaltyReconSettings()
    assert recon_due(store, cfg, NOW)
    with caplog.at_level(logging.INFO, logger="efn_etl.loyalty_recon"):
        report = run_loyalty_recon(platform, odoo, store, cfg, NOW)
    assert report.anomalies == 0 and report.open_items == 3
    assert odoo.by_uuid_calls == 1
    line = next(r.getMessage() for r in caplog.records if lr.RECON_MARKER in r.getMessage())
    payload = json.loads(line.split(" ", 1)[1])
    assert list(payload)[0] == "status" and payload["kinds"] == {"order_review_aged": 2, "outbox_failed": 1}
    assert caplog.records[-1].levelno == logging.INFO
    assert store.rows[lr.RUN_TABLE][0][1:] == ["ok", 72, 6, 3, 5, 0, 3]
    kinds = {r[1]: r for r in store.rows[lr.FINDING_TABLE]}
    assert kinds["order_review_aged"][2:5] == ["warning", "FNB", 2]
    assert not recon_due(store, cfg, NOW + timedelta(hours=1))
    assert recon_due(store, cfg, NOW + timedelta(hours=24))


def test_run_logs_warning_on_anomaly(caplog: pytest.LogCaptureFixture) -> None:
    inputs = _clean_inputs()
    inputs.earn_refs["u-ghost"] = "FNB"
    with caplog.at_level(logging.INFO, logger="efn_etl.loyalty_recon"):
        report = run_loyalty_recon(
            FakePlatform(inputs), FakeOdoo(inputs, set()), FakeStore(), LoyaltyReconSettings(), NOW
        )
    assert report.anomalies == 1
    record = next(r for r in caplog.records if lr.RECON_MARKER in r.getMessage())
    assert record.levelno == logging.WARNING
    assert json.loads(record.getMessage().split(" ", 1)[1])["kinds"]["earn_without_paid_order"] == 1


def test_not_before_bounds_window() -> None:
    cfg = LoyaltyReconSettings(not_before=NOW - timedelta(hours=5))
    assert cfg.since(NOW) == NOW - timedelta(hours=5)
    assert LoyaltyReconSettings().since(NOW) == NOW - timedelta(hours=72)
    assert LoyaltyReconSettings(not_before=NOW - timedelta(days=30)).since(NOW) == NOW - timedelta(hours=72)
