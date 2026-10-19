"""G22 (docs/04 §14.C.5): rekonsiliasi poin online — ledger BFF <-> pembayaran Poin EFN faktur/nota kredit SO."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from efn_etl import loyalty_recon as lr
from efn_etl import loyalty_recon_online as lo
from efn_etl.loyalty_recon import LoyaltyReconSettings, run_loyalty_recon

V = 100  # nilai 1 poin (Rp), LOYALTY_POINT_VALUE_IDR
DAY_FROM, DAY_TO = date(2026, 10, 16), date(2026, 10, 18)
PART_A = "aaaaaaaa-0000-4000-8000-000000000001"
PART_B = "bbbbbbbb-0000-4000-8000-000000000002"


def _at(day: date, hour_wib: int = 12) -> datetime:
    """Datetime UTC untuk jam WIB tertentu pada `day`."""
    return datetime(day.year, day.month, day.day, hour_wib, tzinfo=UTC) - timedelta(hours=7)


def _pay(pid: int, ref: str, amount: str, day: date, company: str = "PB", order_id: int = 70) -> lo.OnlinePointsPayment:
    """Pembayaran Poin EFN Odoo (amount < 0 = nota kredit / restore)."""
    return lo.OnlinePointsPayment(pid, order_id, ref, company, day, Decimal(amount))


def _led(lid: int, ref: str, reason: str, delta: int, at: datetime, company: str = "PB") -> lo.OnlineLedgerEntry:
    return lo.OnlineLedgerEntry(lid, ref, company, reason, delta, at)


def _order(ref: str, state: str = "picked_up", refunds: int = 0, oid: int = 70) -> lo.OnlineOrderRef:
    return lo.OnlineOrderRef(oid, ref, "PB", state, refunds)


def _recon(
    payments: list[lo.OnlinePointsPayment],
    ledger: list[lo.OnlineLedgerEntry],
    orders: dict[str, lo.OnlineOrderRef] | None = None,
    tolerance: Decimal = Decimal(0),
) -> lo.OnlineReconResult:
    return lo.online_points_recon(payments, ledger, orders or {}, DAY_FROM, DAY_TO, V, tolerance)


def _kinds(result: lo.OnlineReconResult) -> dict[str, int]:
    out: dict[str, int] = {}
    for (kind, _company), finding in result.findings.items():
        out[kind] = out.get(kind, 0) + finding.count
    return out


D1 = date(2026, 10, 16)
D2 = date(2026, 10, 17)


# ---------------------------------------------------------------- logika murni
def test_clean_redeem_and_restore_with_floor_rounding() -> None:
    # Redeem 300 poin = Rp30.000; refund parsial Odoo Rp3.750 -> BFF restore floor 37 poin (Rp3.700), sisa Rp50 < V.
    payments = [_pay(1, PART_A, "30000", D1), _pay(2, PART_A, "-3750", D2)]
    ledger = [
        _led(10, PART_A, lo.REDEEM, -200, _at(D1)),  # FIFO dua lot
        _led(11, PART_A, lo.REDEEM, -100, _at(D1)),
        _led(12, PART_A, lo.RESTORE, 37, _at(D2)),
        _led(13, PART_A, lo.EARN, 25, _at(D2)),
    ]
    result = _recon(payments, ledger, {PART_A: _order(PART_A, refunds=1)})
    assert result.findings == {}
    assert result.parts_checked == 1 and result.parts_deferred == 0
    days = {(d.day, d.company_code): d for d in result.daily}
    assert days[(D1, "PB")].odoo_idr == Decimal(30000) and days[(D1, "PB")].redeem_idr == Decimal(30000)
    assert days[(D1, "PB")].diff == 0
    # Hari refund: Odoo −3.750 vs BFF −3.700 -> selisih harian −50 (sisa pembulatan, bukan temuan).
    assert days[(D2, "PB")].diff == Decimal(-50)


def test_redeem_without_odoo_points_payment_is_critical() -> None:
    result = _recon([], [_led(10, PART_A, lo.REDEEM, -300, _at(D1))], {PART_A: _order(PART_A, "confirmed")})
    assert _kinds(result) == {"online_points_payment_mismatch": 1}
    finding = result.findings[("online_points_payment_mismatch", "PB")]
    assert finding.order_ids == [70] and finding.ledger_ids == [10]
    assert result.daily[0].mismatched == {PART_A}


def test_odoo_points_payment_without_bff_redeem_is_critical() -> None:
    result = _recon([_pay(1, PART_B, "5000", D1, order_id=71)], [])
    assert _kinds(result) == {"online_points_payment_mismatch": 1}
    assert result.findings[("online_points_payment_mismatch", "PB")].order_ids == [71]


def test_value_mismatch_respects_tolerance() -> None:
    payments = [_pay(1, PART_A, "30001", D1)]
    ledger = [_led(10, PART_A, lo.REDEEM, -300, _at(D1))]
    assert "online_points_payment_mismatch" in _kinds(_recon(payments, ledger))
    assert _recon(payments, ledger, tolerance=Decimal(1)).findings == {}


@pytest.mark.parametrize(
    ("odoo_restore", "bff_points", "kind_expected"),
    [
        ("-3700", 37, False),  # tepat
        ("-3799", 37, False),  # sisa 99 < V
        ("-3800", 37, True),  # sisa 100 = V -> BFF mengembalikan terlalu sedikit
        ("-3600", 37, True),  # BFF mengembalikan lebih banyak dari nilai Odoo
        (None, 5, True),  # restore BFF tanpa refund Odoo
    ],
)
def test_restore_rounding_window(odoo_restore: str | None, bff_points: int, kind_expected: bool) -> None:
    payments = [_pay(1, PART_A, "30000", D1)]
    if odoo_restore:
        payments.append(_pay(2, PART_A, odoo_restore, D2))
    ledger = [_led(10, PART_A, lo.REDEEM, -300, _at(D1)), _led(11, PART_A, lo.RESTORE, bff_points, _at(D2))]
    kinds = _kinds(_recon(payments, ledger))
    assert ("online_points_restore_mismatch" in kinds) is kind_expected
    assert "online_points_payment_mismatch" not in kinds


def test_restore_helper_edges() -> None:
    assert lo.restore_within_rounding(Decimal(0), 0, 0, V, Decimal(0))
    assert lo.restore_within_rounding(Decimal(150), 0, 2, V, Decimal(0))  # dua refund kecil, masing-masing < V
    assert not lo.restore_within_rounding(Decimal(200), 0, 2, V, Decimal(0))
    with pytest.raises(ValueError):
        lo.online_points_recon([], [], {}, DAY_FROM, DAY_TO, 0)


def test_part_with_activity_after_window_is_deferred() -> None:
    # Refund Odoo sudah settle di hari setelah jendela, restore BFF belum -> jangan dilaporkan sekarang.
    payments = [_pay(1, PART_A, "30000", D1), _pay(2, PART_A, "-3700", DAY_TO)]
    ledger = [_led(10, PART_A, lo.REDEEM, -300, _at(D1))]
    result = _recon(payments, ledger)
    assert result.findings == {} and result.parts_deferred == 1 and result.parts_checked == 0


def test_history_before_window_counts_but_inactive_parts_skipped() -> None:
    old = date(2026, 10, 10)
    # PART_A: redeem lama (di luar jendela) + refund di jendela -> diperiksa dengan seluruh riwayat (bersih).
    # PART_B: hanya riwayat lama (selisih) -> tidak aktif di jendela, tidak diperiksa ulang.
    payments = [_pay(1, PART_A, "30000", old), _pay(2, PART_A, "-3700", D1), _pay(3, PART_B, "999", old)]
    ledger = [_led(10, PART_A, lo.REDEEM, -300, _at(old)), _led(11, PART_A, lo.RESTORE, 37, _at(D1))]
    result = _recon(payments, ledger, {PART_A: _order(PART_A, refunds=1)})
    assert result.findings == {} and result.parts_checked == 1
    assert {d.day for d in result.daily} == {D1}


def test_bff_day_boundary_uses_wib() -> None:
    # 23:30 WIB tanggal 15 = 16:30 UTC -> hari 15 (di luar jendela), bukan 16.
    ledger = [_led(10, PART_A, lo.REDEEM, -1, _at(date(2026, 10, 15), 23) + timedelta(minutes=30))]
    assert _recon([], ledger).daily == []


def test_earn_requires_picked_up_order() -> None:
    ledger = [_led(20, PART_A, lo.EARN, 50, _at(D1)), _led(21, PART_B, lo.EARN, 10, _at(D1))]
    orders = {PART_A: _order(PART_A, "ready")}  # PART_B tidak ada di Odoo
    result = _recon([], ledger, orders)
    assert _kinds(result) == {"online_earn_without_picked_up": 2}
    assert result.findings[("online_earn_without_picked_up", "PB")].ledger_ids == [20, 21]
    clean = _recon([], ledger[:1], {PART_A: _order(PART_A, "picked_up")})
    assert clean.findings == {}


def test_reverse_requires_refund() -> None:
    ledger = [_led(30, PART_A, lo.REVERSE, -5, _at(D2))]
    assert _kinds(_recon([], ledger, {PART_A: _order(PART_A, refunds=0)})) == {"online_reverse_without_refund": 1}
    assert _recon([], ledger, {PART_A: _order(PART_A, refunds=1)}).findings == {}
    # Entri di luar jendela tidak diperiksa.
    late = [_led(31, PART_A, lo.REVERSE, -5, _at(DAY_TO))]
    assert _recon([], late, {}).findings == {}


def test_company_from_odoo_part_wins_over_ledger() -> None:
    # Restore lintas PT (ledger company_code = penerbit lot) diatribusikan ke PT bagian (Odoo).
    payments = [_pay(1, PART_A, "30000", D1, company="BCF")]
    ledger = [_led(10, PART_A, lo.REDEEM, -300, _at(D1), company="GL")]
    result = _recon(payments, ledger)
    assert [(d.company_code, d.diff) for d in result.daily] == [("BCF", Decimal(0))]


def test_dedup_rows_loaded_twice() -> None:
    payments = [_pay(1, PART_A, "30000", D1), _pay(1, PART_A, "30000", D1)]
    ledger = [_led(10, PART_A, lo.REDEEM, -300, _at(D1)), _led(10, PART_A, lo.REDEEM, -300, _at(D1))]
    result = _recon(payments, ledger)
    assert result.findings == {} and result.daily[0].odoo_idr == Decimal(30000)


def test_rows_and_payload_without_pii() -> None:
    result = _recon([_pay(1, PART_A, "30000", D1)], [_led(10, PART_A, lo.REDEEM, -200, _at(D1))])
    now = datetime(2026, 10, 19, 1, tzinfo=UTC)
    rows = lo.online_daily_rows(result.daily, now)
    assert len(rows[0]) == len(lr.DAILY_COLUMNS)
    row = dict(zip(lr.DAILY_COLUMNS, rows[0], strict=True))
    assert row["channel"] == "online" and row["diff_idr"] == Decimal(10000) and row["orders_mismatched"] == 1
    payload = lo.online_daily_payload(result)
    text = json.dumps(payload)
    assert PART_A not in text and payload["mismatched"][0]["parts"] == 1


def test_online_kinds_registered_as_critical() -> None:
    assert set(lo.ONLINE_KINDS) <= set(lr.CRITICAL_KINDS)
    assert all(lr.KIND_SEVERITY[k] == "critical" for k in lo.ONLINE_KINDS)


def test_settings_online_requires_payment_mode() -> None:
    assert LoyaltyReconSettings().online_enabled is False
    with pytest.raises(ValueError):
        LoyaltyReconSettings(points_mode="product", online_enabled=True)


def test_sql_has_filters_and_no_identity() -> None:
    for sql in (
        lo.SQL_PLATFORM_ONLINE_LEDGER_WINDOW,
        lo.SQL_PLATFORM_ONLINE_LEDGER_BY_REF,
        lo.SQL_ODOO_ONLINE_PAYMENTS_WINDOW,
        lo.SQL_ODOO_ONLINE_PAYMENTS_BY_REF,
        lo.SQL_ODOO_ONLINE_ORDERS_BY_REF,
    ):
        assert "member" not in sql and "partner" not in sql and "*" not in sql
        assert "WHERE" in sql and "%(" in sql
    assert "source = 'online'" in lo.SQL_PLATFORM_ONLINE_LEDGER_WINDOW


# ---------------------------------------------------------------- orkestrasi dengan fake I/O
NOW = datetime(2026, 10, 19, 3, tzinfo=UTC)  # 10:00 WIB; lookback 72 jam, settle 6 jam -> hari 16..17 Okt


class FakePlatform:
    """Sisi BFF: POS kosong + entri ledger online."""

    def __init__(self, entries: list[lo.OnlineLedgerEntry]) -> None:
        self.entries = entries
        self.sql: list[str] = []

    def _row(self, e: lo.OnlineLedgerEntry) -> dict[str, Any]:
        return {
            "id": e.id,
            "company_code": e.company_code,
            "delta_points": e.delta_points,
            "reason": e.reason,
            "source_ref": e.part_ref.upper(),  # dinormalisasi huruf kecil oleh ETL
            "created_at": e.created_at,
        }

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert "member_hash" not in sql and "member_ref" not in sql
        self.sql.append(sql)
        if sql == lo.SQL_PLATFORM_ONLINE_LEDGER_WINDOW:
            return [self._row(e) for e in self.entries if params["since"] <= e.created_at < params["until"]]
        if sql == lo.SQL_PLATFORM_ONLINE_LEDGER_BY_REF:
            return [self._row(e) for e in self.entries if e.part_ref in params["refs"]]
        return []  # sisi POS (earn/ledger/reservasi/redeem/restore) kosong


class FakeOdoo:
    def __init__(self, payments: list[lo.OnlinePointsPayment], orders: list[lo.OnlineOrderRef]) -> None:
        self.payments = payments
        self.orders = orders

    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        assert "partner_id," not in sql and "member" not in sql
        if sql == lr.SQL_ODOO_COMPANIES:
            return [{"id": 3, "efn_brand_code": "PB"}]
        if sql == lo.SQL_ODOO_ONLINE_PAYMENTS_WINDOW:
            rows = [p for p in self.payments if params["day_from"] <= p.paid_on < params["day_to"]]
        elif sql == lo.SQL_ODOO_ONLINE_PAYMENTS_BY_REF:
            rows = [p for p in self.payments if p.part_ref in params["refs"]]
        elif sql == lo.SQL_ODOO_ONLINE_ORDERS_BY_REF:
            return [
                {
                    "online_order_id": o.id,
                    "part_ref": o.part_ref,
                    "company_id": 3,
                    "state": o.state,
                    "refunds": o.refunds,
                }
                for o in self.orders
                if o.part_ref in params["refs"]
            ]
        else:
            return []  # query POS: tidak ada order/pembayaran POS
        return [
            {
                "payment_id": p.id,
                "online_order_id": p.online_order_id,
                "part_ref": p.part_ref,
                "company_id": 3,
                "paid_on": p.paid_on,
                "amount": p.amount,
            }
            for p in rows
        ]


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[str, list[list[Any]]] = {}

    def last_loyalty_recon(self) -> datetime | None:
        return None

    def insert(self, table: str, columns: Any, rows: list[list[Any]]) -> None:
        expected = {
            lr.RUN_TABLE: lr.RUN_COLUMNS,
            lr.FINDING_TABLE: lr.FINDING_COLUMNS,
            lr.DAILY_TABLE: lr.DAILY_COLUMNS,
        }
        assert tuple(columns) == expected[table]
        for row in rows:
            assert len(row) == len(columns)
        self.rows.setdefault(table, []).extend(rows)


def test_run_loyalty_recon_with_online(caplog: pytest.LogCaptureFixture) -> None:
    # Jendela run: since = 16 Okt 10:00 WIB -> hari utuh pertama 17 Okt; settle 6 jam -> sampai 18 Okt (eksklusif 19).
    e1, e2 = date(2026, 10, 17), date(2026, 10, 18)
    payments = [_pay(1, PART_A, "30000", e1), _pay(2, PART_B, "5000", e2, order_id=71)]
    entries = [
        _led(10, PART_A, lo.REDEEM, -300, _at(e1)),
        _led(11, PART_A, lo.EARN, 20, _at(e2)),
    ]
    orders = [_order(PART_A, "picked_up"), _order(PART_B, "confirmed", oid=71)]
    platform, odoo, store = FakePlatform(entries), FakeOdoo(payments, orders), FakeStore()
    with caplog.at_level(logging.INFO, logger="efn_etl.loyalty_recon"):
        report = run_loyalty_recon(platform, odoo, store, LoyaltyReconSettings(online_enabled=True), NOW)
    assert report.anomalies == 1  # PART_B: pembayaran Poin EFN tanpa redeem BFF
    line = next(r.getMessage() for r in caplog.records if lr.RECON_MARKER in r.getMessage())
    payload = json.loads(line.split(" ", 1)[1])
    assert list(payload)[:3] == ["status", "anomalies", "open_items"]  # kontrak regex Loki tidak berubah
    assert payload["kinds"]["online_points_payment_mismatch"] == 1
    assert payload["points_daily_online"]["parts_checked"] == 2
    assert PART_A not in line and PART_B not in line
    daily = [dict(zip(lr.DAILY_COLUMNS, r, strict=True)) for r in store.rows[lr.DAILY_TABLE]]
    assert {r["channel"] for r in daily} == {"online"}
    findings = {r[1]: r for r in store.rows[lr.FINDING_TABLE]}
    assert findings["online_points_payment_mismatch"][2:6] == ["critical", "PB", 1, [71]]


def test_run_loyalty_recon_online_disabled_does_not_query_views() -> None:
    platform, odoo = FakePlatform([]), FakeOdoo([], [])
    run_loyalty_recon(platform, odoo, FakeStore(), LoyaltyReconSettings(), NOW)
    assert lo.SQL_PLATFORM_ONLINE_LEDGER_WINDOW not in platform.sql
