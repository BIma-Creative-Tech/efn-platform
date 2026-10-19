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
# Mode produk (redeem = baris produk, data historis pra-E-30); default sejak SEC-114 = payment.
PRODUCT = LoyaltyReconSettings(points_mode="product")


def _order(oid: int, uuid: str, **kw: Any) -> OdooOrder:
    base: dict[str, Any] = {
        "id": oid,
        "uuid": uuid,
        "company_code": "PB",
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
        LedgerEntry(10, "PB", 100, "pos_earn", "u-earn", "pos_earn:u-earn"),
        LedgerEntry(11, "PB", -60, "pos_redeem", "u-redeem", "pos_redeem:u-redeem"),
        LedgerEntry(12, "PB", 19, "pos_earn", "u-redeem", "pos_earn:u-redeem"),
        LedgerEntry(13, "PB", 30, "pos_reverse", "u-redeem", "pos_reverse:u-refund:restore#1"),
        LedgerEntry(14, "PB", -10, "pos_reverse", "u-redeem", "pos_reverse:u-refund:claw"),
    ]
    return ReconInputs(
        earn_refs={"u-earn": "PB", "u-redeem": "PB"},
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
    inputs.earn_refs["u-ghost"] = "GL"
    inputs.ledger.append(LedgerEntry(20, "GL", 50, "pos_earn", "u-ghost", "pos_earn:u-ghost"))
    inputs.orders_by_uuid["u-draft"] = _order(5, "u-draft", state="draft")
    inputs.earn_refs["u-draft"] = "PB"
    inputs.earn_refs["u-refund"] = "PB"
    report = reconcile(inputs, NOW, SETTLE)
    assert _kinds(report) == {"earn_without_paid_order": 3}
    ghost = report.findings[("earn_without_paid_order", "GL")]
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
    finding = report.findings[("paid_member_order_without_earn", "PB")]
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
    inputs.earn_refs["u-free"] = "PB"
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
    assert report.findings[("reverse_without_refund_order", "PB")].ledger_ids == [13, 14]

    inputs = _clean_inputs()
    inputs.orders_by_uuid["u-refund"] = _order(3, "u-refund", is_refund=False)  # bukan refund
    assert "reverse_without_refund_order" in _kinds(reconcile(inputs, NOW, SETTLE))


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        ReconReport().add("bogus", "PB")


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
            return [{"id": 3, "efn_brand_code": "PB"}]
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
        tables = {lr.RUN_TABLE: RUN_COLUMNS, lr.FINDING_TABLE: FINDING_COLUMNS, lr.DAILY_TABLE: lr.DAILY_COLUMNS}
        expected = tables[table]
        assert tuple(columns) == expected
        self.rows.setdefault(table, []).extend(rows)


def test_run_loyalty_recon_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    inputs = _clean_inputs()
    # Order asal redeem di luar jendela (diambil per uuid dari ledger)
    platform, odoo, store = FakePlatform(inputs), FakeOdoo(inputs, {"u-earn", "u-nomember"}), FakeStore()
    cfg = PRODUCT
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
    assert kinds["order_review_aged"][2:5] == ["warning", "PB", 2]
    assert not recon_due(store, cfg, NOW + timedelta(hours=1))
    assert recon_due(store, cfg, NOW + timedelta(hours=24))


def test_run_logs_warning_on_anomaly(caplog: pytest.LogCaptureFixture) -> None:
    inputs = _clean_inputs()
    inputs.earn_refs["u-ghost"] = "PB"
    with caplog.at_level(logging.INFO, logger="efn_etl.loyalty_recon"):
        report = run_loyalty_recon(FakePlatform(inputs), FakeOdoo(inputs, set()), FakeStore(), PRODUCT, NOW)
    assert report.anomalies == 1
    record = next(r for r in caplog.records if lr.RECON_MARKER in r.getMessage())
    assert record.levelno == logging.WARNING
    assert json.loads(record.getMessage().split(" ", 1)[1])["kinds"]["earn_without_paid_order"] == 1


def test_not_before_bounds_window() -> None:
    cfg = LoyaltyReconSettings(not_before=NOW - timedelta(hours=5))
    assert cfg.since(NOW) == NOW - timedelta(hours=5)
    assert LoyaltyReconSettings().since(NOW) == NOW - timedelta(hours=72)
    assert LoyaltyReconSettings(not_before=NOW - timedelta(days=30)).since(NOW) == NOW - timedelta(hours=72)


# ---------------------------------------------------------------- G18: redeem = pembayaran Poin EFN (E-30, K-18-6)

WIB = lr.WIB
PB = "PB"
D6, D7, D8 = (datetime(2026, 10, d).date() for d in (6, 7, 8))


def _wib(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=WIB)


def _pay(pid: int, oid: int, uuid: str, at: datetime, amount: str, company: str = PB) -> lr.PointsPayment:
    return lr.PointsPayment(pid, oid, uuid, company, at, Decimal(amount))


def _days_of(payments: list[lr.PointsPayment]) -> dict[str, tuple[Any, str, int]]:
    out: dict[str, tuple[Any, str, int]] = {}
    for p in sorted(payments, key=lambda x: x.paid_at):
        out.setdefault(p.uuid, (lr.wib_day(p.paid_at), p.company_code, p.order_id))
    return out


def test_settled_days_window() -> None:
    # NOW 12:00 UTC = 19:00 WIB 8 Okt; lookback 72 j -> mulai 19:00 WIB 5 Okt -> hari utuh pertama 6 Okt;
    # settle 6 j -> 13:00 WIB 8 Okt -> 8 Okt belum utuh.
    assert lr.settled_days(NOW - timedelta(hours=72), NOW, SETTLE) == (D6, D8)
    assert lr.settled_days(_wib(6, 0), NOW, SETTLE) == (D6, D8)  # tepat tengah malam = hari utuh
    first, last = lr.settled_days(NOW - timedelta(hours=10), NOW, SETTLE)
    assert first >= last  # jendela pendek -> kosong


def test_daily_points_clean_redeem_and_refund_with_cross_midnight_commit() -> None:
    payments = [
        _pay(1, 2, "u-redeem", _wib(6, 23, 50), "6000.00"),  # dibayar 23:50 WIB, commit BFF 00:10 hari berikutnya
        _pay(2, 3, "u-refund", _wib(7, 10), "-3000.00"),
    ]
    moves = [
        lr.BffPointsMove("u-redeem", PB, _wib(7, 0, 10), 6000, "redeem"),
        lr.BffPointsMove("u-refund", PB, _wib(7, 10, 5), 3000, "restore"),
    ]
    days = lr.daily_points_recon(payments, _days_of(payments), moves, D6, D8)
    assert [(d.day, d.odoo_idr, d.redeem_idr, d.restored_idr, d.diff) for d in days] == [
        (D6, Decimal("6000.00"), Decimal(6000), Decimal(0), Decimal(0)),
        (D7, Decimal("-3000.00"), Decimal(0), Decimal(3000), Decimal(0)),
    ]
    report = ReconReport()
    lr.add_daily_findings(report, days)
    assert report.anomalies == 0
    rows = lr.daily_rows(days, NOW)
    assert rows[0] == [
        NOW,
        D6,
        PB,
        Decimal("6000.00"),
        Decimal(6000),
        Decimal(0),
        Decimal(0),
        1,
        0,
        Decimal(0),
        0,
        "pos",  # G22: kolom channel
    ]
    assert len(rows[0]) == len(lr.DAILY_COLUMNS)


def test_daily_points_mismatches() -> None:
    payments = [
        _pay(1, 2, "u-short", _wib(6, 10), "5000"),  # BFF 6000 -> selisih order
        _pay(2, 4, "u-nobff", _wib(6, 11), "2000"),  # pembayaran poin tanpa redeem BFF
        _pay(3, 5, "u-ok", _wib(7, 9), "1000"),
    ]
    moves = [
        lr.BffPointsMove("u-short", PB, _wib(6, 10, 1), 6000, "redeem"),
        lr.BffPointsMove("u-ghost", "GL", _wib(7, 12), 4000, "redeem"),  # redeem BFF tanpa order Odoo
        lr.BffPointsMove("u-ok", PB, _wib(7, 9, 1), 1000, "redeem"),
    ]
    days = lr.daily_points_recon(payments, _days_of(payments), moves, D6, D8)
    by_key = {(d.day, d.company_code): d for d in days}
    assert by_key[(D6, PB)].diff == Decimal(1000)  # 7000 - 6000
    assert sorted(by_key[(D6, PB)].mismatched) == ["u-nobff", "u-short"]
    assert sorted(by_key[(D6, PB)].sample_order_ids) == [2, 4]
    assert by_key[(D7, "GL")].diff == Decimal(-4000) and by_key[(D7, "GL")].sample_order_ids == []
    assert by_key[(D7, PB)].diff == 0
    report = ReconReport()
    lr.add_daily_findings(report, days)
    assert report.by_kind() == {"points_payment_daily_mismatch": 2}
    assert sorted(report.findings[("points_payment_daily_mismatch", PB)].order_ids) == [2, 4]
    payload = lr.daily_payload(days)
    assert payload["days_checked"] == 2 and len(payload["mismatched"]) == 2
    assert "member" not in json.dumps(payload)


def test_daily_points_tolerance_and_out_of_window() -> None:
    payments = [_pay(1, 2, "u-a", _wib(6, 10), "5999")]
    order_days = _days_of(payments)
    order_days["u-old"] = (datetime(2026, 10, 5).date(), PB, 9)  # order Odoo di luar jendela
    moves = [
        lr.BffPointsMove("u-a", PB, _wib(6, 10), 6000, "redeem"),
        lr.BffPointsMove("u-old", PB, _wib(6, 0, 30), 500, "restore"),  # dicatat BFF di jendela, order hari 5
    ]
    strict = lr.daily_points_recon(payments, order_days, moves, D6, D8)
    assert len(strict) == 1 and strict[0].diff == Decimal(-1) and strict[0].restored_idr == 0
    tolerant = lr.daily_points_recon(payments, order_days, moves, D6, D8, Decimal(1))
    report = ReconReport()
    lr.add_daily_findings(report, tolerant, Decimal(1))
    assert report.anomalies == 0 and tolerant[0].mismatched == set()


def test_payment_mode_value_check_has_zero_tolerance() -> None:
    inputs = _clean_inputs()
    inputs.points_lines[2] = PointsLines(1, Decimal("5999.00"))
    assert _kinds(reconcile(inputs, NOW, SETTLE)) == {}  # mode produk: toleransi Rp1 x baris
    kinds = _kinds(reconcile(inputs, NOW, SETTLE, points_tolerance=Decimal(0)))
    assert kinds == {"points_line_value_mismatch": 1}


def test_points_mode_validation() -> None:
    assert LoyaltyReconSettings().payment_mode is True  # SEC-114: default payment
    assert LoyaltyReconSettings(points_mode="product").payment_mode is False
    with pytest.raises(ValueError):
        LoyaltyReconSettings(points_mode="diskon")
    with pytest.raises(ValueError):
        LoyaltyReconSettings(points_tolerance_idr=Decimal(-1))


def test_payment_sql_reads_marker_not_product_or_identity() -> None:
    for sql in (lr.SQL_ODOO_POINTS_PAYMENTS, lr.SQL_ODOO_POINTS_PAYMENTS_WINDOW, lr.SQL_ODOO_POINTS_PAYMENTS_BY_UUID):
        assert "efn_is_loyalty_points" in sql and "default_code" not in sql
        assert "partner" not in sql and "card" not in sql
    for sql in (
        lr.SQL_PLATFORM_REDEEMS_WINDOW,
        lr.SQL_PLATFORM_REDEEMS_BY_REF,
        lr.SQL_PLATFORM_RESTORES_WINDOW,
        lr.SQL_PLATFORM_RESTORES_BY_REF,
    ):
        assert "member" not in sql and "token" not in sql


class FakePlatformPayment(FakePlatform):
    def __init__(self, inputs: ReconInputs, moves: list[lr.BffPointsMove]) -> None:
        super().__init__(inputs)
        self.moves = moves

    def _rows(self, kind: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for m in self.moves:
            if m.kind != kind:
                continue
            if "refs" in params and m.order_ref not in params["refs"]:
                continue
            if "since" in params and not params["since"] <= m.at < params["until"]:
                continue
            row = {"order_ref": m.order_ref, "company_code": m.company_code, "value_idr": m.value_idr, "at": m.at}
            if kind == "restore":
                row["points"] = m.points
            out.append(row)
        return out

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if sql in (lr.SQL_PLATFORM_REDEEMS_WINDOW, lr.SQL_PLATFORM_REDEEMS_BY_REF):
            return self._rows("redeem", params)
        if sql in (lr.SQL_PLATFORM_RESTORES_WINDOW, lr.SQL_PLATFORM_RESTORES_BY_REF):
            return self._rows("restore", params)
        return super().query(sql, params)


class FakeOdooPayment(FakeOdoo):
    def __init__(
        self,
        inputs: ReconInputs,
        window_only: set[str],
        payments: list[lr.PointsPayment],
        legacy_lines: dict[int, PointsLines] | None = None,
    ) -> None:
        super().__init__(inputs, window_only)
        self.payments = payments
        self.legacy_lines = legacy_lines or {}
        self.seen_sql: list[str] = []

    def _prow(self, p: lr.PointsPayment) -> dict[str, Any]:
        return {
            "id": p.id,
            "order_id": p.order_id,
            "uuid": p.uuid,
            "company_id": 3,
            "paid_at": p.paid_at,
            "amount": p.amount,
        }

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        self.seen_sql.append(sql)
        if sql == lr.SQL_ODOO_POINTS_LINES:
            # Cadangan order pra-E-30: hanya ditanya untuk order TANPA pembayaran Poin EFN.
            assert not set(params["ids"]) & {i for i in self.inputs.points_lines}
            return [
                {"order_id": i, "n": p.count, "total": p.total_incl}
                for i, p in self.legacy_lines.items()
                if i in params["ids"]
            ]
        if sql == lr.SQL_ODOO_POINTS_PAYMENTS:
            return [
                {"order_id": i, "n": p.count, "total": abs(p.total_incl)}
                for i, p in self.inputs.points_lines.items()
                if i in params["ids"]
            ]
        if sql == lr.SQL_ODOO_POINTS_PAYMENTS_WINDOW:
            return [self._prow(p) for p in self.payments if params["since"] <= p.paid_at < params["until"]]
        if sql == lr.SQL_ODOO_POINTS_PAYMENTS_BY_UUID:
            return [self._prow(p) for p in self.payments if p.uuid in params["uuids"]]
        return super().query(sql, params)


def test_run_payment_mode_end_to_end(caplog: pytest.LogCaptureFixture) -> None:
    inputs = _clean_inputs()
    payments = [
        _pay(1, 2, "u-redeem", _wib(6, 23, 50), "6000"),
        _pay(2, 3, "u-refund", _wib(7, 10), "-3000"),
        _pay(3, 7, "u-early", _wib(5, 9), "800"),  # di luar jendela hari
    ]
    moves = [
        lr.BffPointsMove("u-redeem", PB, _wib(7, 0, 10), 6000, "redeem"),
        lr.BffPointsMove("u-refund", PB, _wib(7, 10, 5), 2500, "restore"),  # BFF restore kurang Rp500
        lr.BffPointsMove("u-early", PB, _wib(6, 0, 5), 800, "redeem"),  # commit di jendela, order hari 5
    ]
    platform = FakePlatformPayment(inputs, moves)
    odoo = FakeOdooPayment(inputs, {"u-earn", "u-nomember"}, payments)
    store = FakeStore()
    cfg = LoyaltyReconSettings(points_mode="payment")
    with caplog.at_level(logging.INFO, logger="efn_etl.loyalty_recon"):
        report = run_loyalty_recon(platform, odoo, store, cfg, NOW)
    assert report.by_kind()["points_payment_daily_mismatch"] == 1
    assert report.findings[("points_payment_daily_mismatch", PB)].order_ids == [3]
    assert lr.SQL_ODOO_POINTS_PAYMENTS_BY_UUID in odoo.seen_sql  # u-early: hari ditentukan dari order Odoo
    daily = store.rows[lr.DAILY_TABLE]
    assert [(r[1], r[3], r[4], r[5], r[6]) for r in daily] == [
        (D6, Decimal("6000"), Decimal(6000), Decimal(0), Decimal(0)),
        (D7, Decimal("-3000"), Decimal(0), Decimal(2500), Decimal(-500)),
    ]
    record = next(r for r in caplog.records if lr.RECON_MARKER in r.getMessage())
    assert record.levelno == logging.WARNING
    payload = json.loads(record.getMessage().split(" ", 1)[1])
    assert payload["points_daily"]["mismatched"] == [
        {"day": "2026-10-07", "company": PB, "diff_idr": "-500", "orders": 1}
    ]


def test_product_mode_skips_daily() -> None:
    inputs = _clean_inputs()
    store = FakeStore()
    run_loyalty_recon(FakePlatform(inputs), FakeOdoo(inputs, set()), store, PRODUCT, NOW)
    assert lr.DAILY_TABLE not in store.rows


# ---------------------------------------------------------------- SEC-114: default payment tanpa alert palsu


def test_default_payment_mode_legacy_order_no_false_redeem_alert() -> None:
    """Order pra-E-30 di jendela lookback (redeem = baris produk) tidak memicu `redeem_without_points_line` palsu;
    order E-30 (pembayaran Poin EFN) tetap dicek dengan toleransi 0."""
    inputs = _clean_inputs()
    legacy = {2: inputs.points_lines.pop(2)}  # u-redeem: hanya baris produk -6000 (Rp1 x baris)
    inputs.points_lines.pop(3)  # order refund pra-E-30: baris produk +3000 juga lewat cadangan
    legacy[3] = PointsLines(1, Decimal("3000.00"))
    platform, store = FakePlatformPayment(inputs, []), FakeStore()
    odoo = FakeOdooPayment(inputs, {"u-earn", "u-nomember"}, [], legacy_lines=legacy)
    report = run_loyalty_recon(platform, odoo, store, LoyaltyReconSettings(), NOW)
    assert report.anomalies == 0
    assert lr.SQL_ODOO_POINTS_PAYMENTS in odoo.seen_sql and lr.SQL_ODOO_POINTS_LINES in odoo.seen_sql

    # Baris produk legacy: toleransi lama Rp1 x baris, bukan 0.
    inputs = _clean_inputs()
    inputs.points_lines[2] = PointsLines(1, Decimal("-5999.00"), legacy=True)
    assert _kinds(reconcile(inputs, NOW, SETTLE, points_tolerance=Decimal(0))) == {}
    inputs.points_lines[2] = PointsLines(1, Decimal("5999.00"))  # pembayaran E-30: toleransi 0
    assert _kinds(reconcile(inputs, NOW, SETTLE, points_tolerance=Decimal(0))) == {"points_line_value_mismatch": 1}


def test_payment_mode_still_flags_redeem_without_any_points_line() -> None:
    inputs = _clean_inputs()
    inputs.points_lines.pop(2)  # tidak ada pembayaran Poin EFN DAN tidak ada baris produk
    odoo = FakeOdooPayment(inputs, {"u-earn", "u-nomember"}, [])
    report = run_loyalty_recon(FakePlatformPayment(inputs, []), odoo, FakeStore(), LoyaltyReconSettings(), NOW)
    assert report.by_kind().get("redeem_without_points_line") == 1


def test_daily_excludes_legacy_orders() -> None:
    inputs = _clean_inputs()
    legacy = {2: inputs.points_lines.pop(2)}
    moves = [lr.BffPointsMove("u-redeem", PB, _wib(7, 9), 6000, "redeem")]  # redeem pra-E-30, tanpa pos.payment
    platform, store = FakePlatformPayment(inputs, moves), FakeStore()
    odoo = FakeOdooPayment(inputs, {"u-earn", "u-nomember"}, [], legacy_lines=legacy)
    report = run_loyalty_recon(platform, odoo, store, LoyaltyReconSettings(), NOW)
    assert "points_payment_daily_mismatch" not in report.by_kind()
    assert lr.DAILY_TABLE not in store.rows  # tidak ada hari dengan nilai -> tidak ada baris harian


# ---------------------------------------------------------------- SEC-118: poin dikembalikan vs points_restored_value


def _restore(ref: str, value: int, points: int | None, at: datetime | None = None) -> lr.BffPointsMove:
    return lr.BffPointsMove(ref, PB, at or _wib(7, 10, 5), value, "restore", points)


def test_restore_points_vs_value_rounding_window() -> None:
    """Nilai poin Rp100: sisa pembulatan floor BFF 0..99 -> cocok; poin berlebih atau kurang >= 1 poin -> temuan
    `points_restore_value_mismatch`, terpisah dari selisih pembayaran (pembayaran tetap cocok)."""
    payments = [
        _pay(1, 3, "u-r-ok", _wib(7, 10), "-3050"),
        _pay(2, 4, "u-r-over", _wib(7, 11), "-3000"),
        _pay(3, 5, "u-r-under", _wib(7, 12), "-3200"),
        _pay(4, 6, "u-r-exact", _wib(7, 13), "-1000"),
    ]
    moves = [
        _restore("u-r-ok", 3050, 30),  # sisa Rp50 < 1 poin
        _restore("u-r-over", 3000, 31),  # BFF mengembalikan Rp3.100 > nilai Odoo
        _restore("u-r-under", 3200, 30),  # kurang 2 poin
        _restore("u-r-exact", 1000, 10),
    ]
    days = lr.daily_points_recon(payments, _days_of(payments), moves, D6, D8, point_value_idr=100)
    (d7,) = days
    assert d7.diff == 0  # pembayaran vs points_restored_value cocok
    assert d7.restored_points_idr == Decimal(3000 + 3100 + 3000 + 1000)
    assert d7.restore_mismatched == {"u-r-over", "u-r-under"}
    assert sorted(d7.restore_sample_order_ids) == [4, 5]
    report = ReconReport()
    lr.add_daily_findings(report, days)
    assert report.by_kind() == {"points_restore_value_mismatch": 2}
    assert sorted(report.findings[("points_restore_value_mismatch", PB)].order_ids) == [4, 5]
    payload = lr.daily_payload(days)
    assert payload["mismatched"] == []
    assert payload["restore_mismatched"] == [{"day": "2026-10-07", "company": PB, "orders": 2}]
    row = dict(zip(lr.DAILY_COLUMNS, lr.daily_rows(days, NOW)[0], strict=True))
    assert row["bff_restored_points_idr"] == Decimal(10100) and row["restore_value_mismatched"] == 2


def test_restore_check_skipped_without_points_or_point_value() -> None:
    payments = [_pay(1, 3, "u-r", _wib(7, 10), "-3000")]
    moves = [_restore("u-r", 3000, 99)]
    assert lr.daily_points_recon(payments, _days_of(payments), moves, D6, D8)[0].restore_mismatched == set()
    no_points = [_restore("u-r", 3000, None)]
    days = lr.daily_points_recon(payments, _days_of(payments), no_points, D6, D8, point_value_idr=100)
    assert days[0].restore_mismatched == set() and days[0].restored_points_idr == 0


def test_restore_check_custom_point_value() -> None:
    payments = [_pay(1, 3, "u-r", _wib(7, 10), "-2499")]
    ok = lr.daily_points_recon(payments, _days_of(payments), [_restore("u-r", 2499, 9)], D6, D8, point_value_idr=250)
    bad = lr.daily_points_recon(payments, _days_of(payments), [_restore("u-r", 2499, 8)], D6, D8, point_value_idr=250)
    assert ok[0].restore_mismatched == set() and bad[0].restore_mismatched == {"u-r"}


def test_run_payment_mode_reports_restore_mismatch_separately() -> None:
    inputs = _clean_inputs()
    payments = [_pay(2, 3, "u-refund", _wib(7, 10), "-3000")]
    moves = [lr.BffPointsMove("u-refund", PB, _wib(7, 10, 5), 3000, "restore", 35)]  # 35 poin = Rp3.500 > Rp3.000
    store = FakeStore()
    odoo = FakeOdooPayment(inputs, {"u-earn", "u-nomember"}, payments)
    report = run_loyalty_recon(FakePlatformPayment(inputs, moves), odoo, store, LoyaltyReconSettings(), NOW)
    kinds = report.by_kind()
    assert kinds.get("points_restore_value_mismatch") == 1
    assert "points_payment_daily_mismatch" not in kinds
    assert report.findings[("points_restore_value_mismatch", PB)].order_ids == [3]
    (row,) = store.rows[lr.DAILY_TABLE]
    assert dict(zip(lr.DAILY_COLUMNS, row, strict=True))["restore_value_mismatched"] == 1
    assert LoyaltyReconSettings().point_value_idr == 100
    with pytest.raises(ValueError):
        LoyaltyReconSettings(point_value_idr=0)


def test_restore_sql_reads_points_restored() -> None:
    for sql in (lr.SQL_PLATFORM_RESTORES_WINDOW, lr.SQL_PLATFORM_RESTORES_BY_REF):
        assert "points_restored AS points" in sql and "member" not in sql
