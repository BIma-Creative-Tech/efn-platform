"""Rekonsiliasi harian ledger poin BFF <-> order POS Odoo (Gelombang 14, sisa SEC-61). Logika murni + orkestrasi.

Recon BFF (`loyalty-recon.service.ts`) hanya melihat data platform dan tidak bisa mendeteksi order fiktif; job ini
membandingkan dua sumber kebenaran: `platform.loyalty_pos_earn` + `platform.loyalty_ledger` (`pos_earn`, `pos_redeem`,
`pos_reverse`) + `platform.loyalty_reservation` (status `committed`) dengan `pos.order` Odoo (uuid = `order_ref` BFF,
docs/04 §9), baris produk "Poin EFN" (`default_code` di `ETL_POINTS_PRODUCT_CODES`), outbox `efn.pos.loyalty.outbox`
dan reservasi lokal `efn.pos.loyalty.reservation`.

Kelas temuan (`kind`):

Critical (anomali integritas, alert):
- `earn_without_paid_order`: earn BFF (catatan earn / ledger `pos_earn`) untuk uuid yang tidak ada di Odoo, tidak
  `paid/done`, atau order refund (order fiktif — yang tidak terlihat recon BFF);
- `paid_member_order_without_earn`: order `paid/done` non-refund ber-partner di POS ber-earn, lebih tua dari `settle`
  (default 6 jam), outbox earn tidak `skipped/failed/hold`, bukan `review`, tanpa catatan earn BFF;
- `redeem_without_points_line`: ledger `pos_redeem` untuk order yang tidak ada / tanpa baris Poin EFN;
- `points_line_value_mismatch`: abs(Σ baris Poin EFN incl. pajak) ≠ `value_idr` reservasi committed (toleransi Rp1 ×
  jumlah baris, sama dengan validasi Odoo);
- `points_line_without_redeem`: order paid non-refund berbaris Poin EFN (lebih tua dari `settle`, bukan `review`) tanpa
  reservasi committed di BFF;
- `redeem_points_mismatch`: −Σ ledger `pos_redeem` order ≠ `points` reservasi committed (atau reservasi tidak ada);
- `reverse_without_refund_order`: ledger `pos_reverse` yang order refund-nya (uuid di idempotency key) atau order
  asalnya (`source_ref`) tidak ada / bukan paid / jenisnya salah;
- `points_payment_daily_mismatch` (G18, K-18-6, hanya mode `payment`): per PT x hari WIB yang sudah settle,
  Σ `pos.payment` metode Poin EFN (Odoo, refund negatif) ≠ Σ `value_idr` redeem committed − Σ `points_restored_value`
  (BFF). Hasil per hari juga ditulis ke `loyalty_points_daily` (dashboard) dan `points_daily` di log.

Mode poin (`ETL_LOYALTY_POINTS_MODE`): `product` = redeem berupa baris produk Poin EFN (G13, default sampai E-30
terpasang); `payment` = redeem berupa pembayaran metode `efn_is_loyalty_points` (docs/04 §9.9 K-18-1). Pada mode
`payment` kind 3-5 memakai baris pembayaran (toleransi `ETL_LOYALTY_POINTS_TOLERANCE_IDR`, default 0).

Warning (antrean kerja yang menua; open item):
- `outbox_hold_aged`, `outbox_pending_aged`: outbox `hold`/`pending` > `ETL_LOYALTY_RECON_AGED_HOURS` (default 24);
- `outbox_failed`: outbox `failed` (final; mis. `earn_limit_exceeded`) dalam `ETL_LOYALTY_RECON_OPEN_DAYS` (30);
- `order_review_aged`, `reservation_review_aged`: order `efn_loyalty_state=review` / reservasi lokal `review` yang
  menua (> aged, dalam open days).

`anomalies` = Σ critical (alert), `open_items` = Σ warning (antrean kerja POS manager/Finance; alert ringan).

PDP: tidak ada `member_hash`, nomor HP, partner, atau nama. Kolom yang dibaca: grant `efn_readonly` (platform) dan
`sql/odoo-readonly-grants.sql` Gelombang 14 (Odoo). Sampel = id order Odoo / id ledger (angka), maks 10 per kind+PT.
Hasil: log `EFN_LOYALTY_RECON_ETL {json}` (logger `efn_etl.loyalty_recon`, WARNING bila anomalies > 0) + tabel
ClickHouse `loyalty_recon_run` / `loyalty_recon_finding` (infra/clickhouse/init/05-loyalty-recon.sql).

Batas: earn di BFF yang dibuat > `lookback` setelah order tidak dibandingkan; nilai restore/clawback refund tidak
dicek nominal (pembulatan floor/ceil BFF), hanya keberadaan order refund; saldo negatif per member belum dicek.
"""

import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from efn_etl.watermark import to_utc

log = logging.getLogger(__name__)

RECON_MARKER = "EFN_LOYALTY_RECON_ETL"
RUN_TABLE = "loyalty_recon_run"
FINDING_TABLE = "loyalty_recon_finding"
RUN_COLUMNS = (
    "run_at",
    "status",
    "lookback_hours",
    "settle_hours",
    "orders_checked",
    "ledger_checked",
    "anomalies",
    "open_items",
)
FINDING_COLUMNS = ("run_at", "kind", "severity", "company_code", "findings", "sample_order_ids", "sample_ledger_ids")

PAID_STATES = frozenset({"paid", "done"})
CRITICAL_KINDS = (
    "earn_without_paid_order",
    "paid_member_order_without_earn",
    "redeem_without_points_line",
    "points_line_value_mismatch",
    "points_line_without_redeem",
    "redeem_points_mismatch",
    "reverse_without_refund_order",
    "points_payment_daily_mismatch",
)
WARNING_KINDS = (
    "outbox_hold_aged",
    "outbox_pending_aged",
    "outbox_failed",
    "order_review_aged",
    "reservation_review_aged",
)
KIND_SEVERITY = {**{k: "critical" for k in CRITICAL_KINDS}, **{k: "warning" for k in WARNING_KINDS}}
# Outbox earn berstatus ini = earn sengaja tidak terkirim (dilewati, ditolak final, ditahan) -> bukan "tanpa earn"
# (failed/hold dilaporkan sebagai open item).
EARN_NOT_EXPECTED_STATES = frozenset({"skipped", "failed", "hold"})
_REVERSE_KEY_RE = re.compile(r"^pos_reverse:([0-9A-Za-z-]{1,64}):")
_UNKNOWN_COMPANY = "?"
SAMPLE_LIMIT = 10
_CHUNK = 5000
WIB = timezone(timedelta(hours=7), "WIB")  # tanpa DST; tidak bergantung tzdata di image
POINTS_MODES = ("product", "payment")
DAILY_TABLE = "loyalty_points_daily"
DAILY_COLUMNS = (
    "run_at",
    "day",
    "company_code",
    "odoo_points_payment_idr",
    "bff_redeem_idr",
    "bff_restored_idr",
    "diff_idr",
    "orders",
    "orders_mismatched",
)

# ---------------------------------------------------------------- SQL platform (efn_readonly, kolom non-PII)
SQL_PLATFORM_EARNS = """
SELECT order_ref, company_code, points_earned
FROM platform.loyalty_pos_earn
WHERE created_at >= %(since)s
"""
SQL_PLATFORM_LEDGER = """
SELECT id, company_code, delta_points, source, source_ref, idempotency_key
FROM platform.loyalty_ledger
WHERE source IN ('pos_earn', 'pos_redeem', 'pos_reverse') AND created_at >= %(since)s
ORDER BY id
"""
SQL_PLATFORM_EARNS_BY_REF = "SELECT order_ref FROM platform.loyalty_pos_earn WHERE order_ref = ANY(%(refs)s)"
SQL_PLATFORM_RESERVATIONS = """
SELECT order_ref, points, value_idr
FROM platform.loyalty_reservation
WHERE status = 'committed' AND order_ref = ANY(%(refs)s)
"""

# ---------------------------------------------------------------- SQL Odoo (grant Gelombang 14)
_ORDER_COLUMNS = """
SELECT o.id, o.uuid, o.company_id, o.state, COALESCE(o.is_refund, FALSE) AS is_refund,
       EXISTS (SELECT 1 FROM pos_order_line rl WHERE rl.order_id = o.id AND rl.refunded_orderline_id IS NOT NULL)
           AS has_refund_lines,
       (o.partner_id IS NOT NULL) AS has_partner, o.efn_loyalty_state, o.date_order,
       COALESCE(cfg.efn_loyalty_earn_enabled, FALSE) AS earn_enabled
FROM pos_order o
JOIN pos_config cfg ON cfg.id = o.config_id
"""
SQL_ODOO_ORDERS_WINDOW = (
    _ORDER_COLUMNS + "WHERE o.partner_id IS NOT NULL AND o.date_order >= %(since)s AND o.date_order < %(until)s"
)
SQL_ODOO_ORDERS_BY_UUID = _ORDER_COLUMNS + "WHERE o.uuid = ANY(%(uuids)s)"
SQL_ODOO_POINTS_LINES = """
SELECT l.order_id, count(*) AS n, COALESCE(sum(l.price_subtotal_incl), 0) AS total
FROM pos_order_line l
JOIN product_product p ON p.id = l.product_id
WHERE p.default_code = ANY(%(codes)s) AND l.order_id = ANY(%(ids)s)
GROUP BY l.order_id
"""
# G18 (docs/04 §9.9 K-18-1, E-30): redeem = baris pembayaran metode Poin EFN (`efn_is_loyalty_points`), bukan
# produk. Mode `payment` (ETL_LOYALTY_POINTS_MODE) memakai query ini menggantikan SQL_ODOO_POINTS_LINES, dengan
# struktur hasil sama (order_id, n, total). Refund = pembayaran negatif.
SQL_ODOO_POINTS_PAYMENTS = """
SELECT p.pos_order_id AS order_id, count(*) AS n, COALESCE(sum(p.amount), 0) AS total
FROM pos_payment p
JOIN pos_payment_method m ON m.id = p.payment_method_id
WHERE m.efn_is_loyalty_points AND p.pos_order_id = ANY(%(ids)s)
GROUP BY p.pos_order_id
"""
# K-18-6 harian: pembayaran Poin EFN per order paid/done (tanggal = payment_date, fallback date_order).
_POINTS_PAYMENT_ROWS = """
SELECT p.id, p.pos_order_id AS order_id, o.uuid, o.company_id, COALESCE(p.payment_date, o.date_order) AS paid_at,
       p.amount
FROM pos_payment p
JOIN pos_payment_method m ON m.id = p.payment_method_id
JOIN pos_order o ON o.id = p.pos_order_id
WHERE m.efn_is_loyalty_points AND o.state IN ('paid', 'done')
"""
SQL_ODOO_POINTS_PAYMENTS_WINDOW = (
    _POINTS_PAYMENT_ROWS
    + "  AND COALESCE(p.payment_date, o.date_order) >= %(since)s AND COALESCE(p.payment_date, o.date_order) < %(until)s"
)
SQL_ODOO_POINTS_PAYMENTS_BY_UUID = _POINTS_PAYMENT_ROWS + "  AND o.uuid = ANY(%(uuids)s)"
# Sisi BFF (kolom non-PII efn_readonly). Redeem = value_idr reservasi committed (pos_redeem, K-18-3).
_PLATFORM_REDEEM_ROWS = """
SELECT order_ref, company_code, value_idr, committed_at AS at
FROM platform.loyalty_reservation
WHERE status = 'committed'
"""
SQL_PLATFORM_REDEEMS_WINDOW = _PLATFORM_REDEEM_ROWS + "  AND committed_at >= %(since)s AND committed_at < %(until)s"
SQL_PLATFORM_REDEEMS_BY_REF = _PLATFORM_REDEEM_ROWS + "  AND order_ref = ANY(%(refs)s)"
# Restore = points_restored_value reverse (K-18-5). USULAN KONTRAK (belum ada di BFF, lihat laporan G18): tabel
# `platform.loyalty_pos_reverse`, satu baris per order refund (idempoten), grant SELECT kolom ini ke efn_readonly.
_PLATFORM_RESTORE_ROWS = """
SELECT refund_order_ref AS order_ref, company_code, points_restored_value AS value_idr, created_at AS at
FROM platform.loyalty_pos_reverse
WHERE points_restored_value > 0
"""
SQL_PLATFORM_RESTORES_WINDOW = _PLATFORM_RESTORE_ROWS + "  AND created_at >= %(since)s AND created_at < %(until)s"
SQL_PLATFORM_RESTORES_BY_REF = _PLATFORM_RESTORE_ROWS + "  AND refund_order_ref = ANY(%(refs)s)"
SQL_ODOO_OUTBOX_BY_ORDERS = "SELECT order_id, kind, state FROM efn_pos_loyalty_outbox WHERE order_id = ANY(%(ids)s)"
SQL_ODOO_COMPANIES = "SELECT id, efn_brand_code FROM res_company"
SQL_ODOO_OUTBOX_OPEN = """
SELECT company_id, state, count(*) AS n, (array_agg(COALESCE(order_id, 0) ORDER BY id))[1:10] AS sample
FROM efn_pos_loyalty_outbox
WHERE create_date >= %(open_since)s
  AND ((state IN ('hold', 'pending') AND create_date < %(aged_before)s) OR state = 'failed')
GROUP BY company_id, state
"""
SQL_ODOO_ORDER_REVIEW = """
SELECT company_id, count(*) AS n, (array_agg(id ORDER BY id))[1:10] AS sample
FROM pos_order
WHERE efn_loyalty_state = 'review' AND date_order >= %(open_since)s AND date_order < %(aged_before)s
GROUP BY company_id
"""
SQL_ODOO_RESERVATION_REVIEW = """
SELECT company_id, count(*) AS n, (array_agg(COALESCE(order_id, 0) ORDER BY id))[1:10] AS sample
FROM efn_pos_loyalty_reservation
WHERE state = 'review' AND create_date >= %(open_since)s AND create_date < %(aged_before)s
GROUP BY company_id
"""
_OUTBOX_KIND = {"hold": "outbox_hold_aged", "pending": "outbox_pending_aged", "failed": "outbox_failed"}


# ---------------------------------------------------------------- model data
@dataclass(frozen=True)
class OdooOrder:
    id: int
    uuid: str
    company_code: str
    state: str
    is_refund: bool
    has_partner: bool
    loyalty_state: str
    date_order: datetime
    earn_enabled: bool

    @property
    def paid(self) -> bool:
        return self.state in PAID_STATES


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    company_code: str
    delta_points: int
    source: str
    source_ref: str
    idempotency_key: str


@dataclass(frozen=True)
class Reservation:
    points: int
    value_idr: int


@dataclass(frozen=True)
class PointsLines:
    count: int
    total_incl: Decimal


@dataclass
class ReconInputs:
    earn_refs: dict[str, str]  # order_ref -> company_code (catatan earn BFF)
    ledger: list[LedgerEntry]
    orders_by_uuid: dict[str, OdooOrder]
    window_uuids: set[str]  # order ber-partner di jendela (subset orders_by_uuid)
    points_lines: dict[int, PointsLines]
    outbox_earn_states: dict[int, set[str]]
    reservations: dict[str, Reservation]


@dataclass
class Finding:
    count: int = 0
    order_ids: list[int] = field(default_factory=list)
    ledger_ids: list[int] = field(default_factory=list)

    def add(self, order_id: int = 0, ledger_id: int = 0, count: int = 1) -> None:
        self.count += count
        if order_id and len(self.order_ids) < SAMPLE_LIMIT and order_id not in self.order_ids:
            self.order_ids.append(order_id)
        if ledger_id and len(self.ledger_ids) < SAMPLE_LIMIT:
            self.ledger_ids.append(ledger_id)


@dataclass
class ReconReport:
    findings: dict[tuple[str, str], Finding] = field(default_factory=lambda: defaultdict(Finding))
    orders_checked: int = 0
    ledger_checked: int = 0

    def add(self, kind: str, company: str, order_id: int = 0, ledger_id: int = 0, count: int = 1) -> None:
        if kind not in KIND_SEVERITY:
            raise ValueError(f"kind tidak dikenal: {kind}")
        self.findings[(kind, company or _UNKNOWN_COMPANY)].add(order_id, ledger_id, count)

    def _total(self, severity: str) -> int:
        return sum(f.count for (kind, _c), f in self.findings.items() if KIND_SEVERITY[kind] == severity)

    @property
    def anomalies(self) -> int:
        return self._total("critical")

    @property
    def open_items(self) -> int:
        return self._total("warning")

    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for (kind, _company), finding in sorted(self.findings.items()):
            out[kind] = out.get(kind, 0) + finding.count
        return out

    def samples(self, attr: str) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for (kind, _company), finding in sorted(self.findings.items()):
            ids = out.setdefault(kind, [])
            for value in getattr(finding, attr):
                if len(ids) < SAMPLE_LIMIT:
                    ids.append(value)
        return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------- logika murni
def reverse_refund_uuid(idempotency_key: str) -> str | None:
    """`pos_reverse:<uuid refund>:claw` / `...:restore#n` -> uuid order refund (kontrak BFF §9.4)."""
    match = _REVERSE_KEY_RE.match(idempotency_key or "")
    return match.group(1) if match else None


def reconcile(
    inputs: ReconInputs,
    now: datetime,
    settle: timedelta,
    report: ReconReport | None = None,
    points_tolerance: Decimal | None = None,
) -> ReconReport:
    """`points_tolerance` None = mode produk (toleransi Rp1 x jumlah baris, validasi Odoo G13); mode `payment` (E-30)
    memberi toleransi tetap (default 0, K-18-1) antara Σ pembayaran Poin EFN dan `value_idr`."""
    report = report or ReconReport()
    orders = inputs.orders_by_uuid
    report.ledger_checked = len(inputs.ledger)
    report.orders_checked = len(orders)
    settled_before = to_utc(now) - settle

    # 1. earn tanpa order paid (order fiktif / draft / batal / refund)
    earn_companies: dict[str, tuple[str, int]] = {ref: (company, 0) for ref, company in inputs.earn_refs.items()}
    for entry in inputs.ledger:
        if entry.source == "pos_earn" and entry.source_ref:
            prev = earn_companies.get(entry.source_ref)
            if prev is None or not prev[1]:
                earn_companies[entry.source_ref] = (entry.company_code, entry.id)
    for ref, (company, ledger_id) in sorted(earn_companies.items()):
        order = orders.get(ref)
        if order is None or not order.paid or order.is_refund:
            report.add("earn_without_paid_order", company, order.id if order else 0, ledger_id)

    # 2. order paid ber-member tanpa earn
    earned = set(inputs.earn_refs)
    for uuid in sorted(inputs.window_uuids):
        order = orders[uuid]
        if not (order.paid and not order.is_refund and order.has_partner and order.earn_enabled):
            continue
        if order.loyalty_state == "review" or to_utc(order.date_order) >= settled_before:
            continue
        if inputs.outbox_earn_states.get(order.id, set()) & EARN_NOT_EXPECTED_STATES:
            continue
        if uuid not in earned:
            report.add("paid_member_order_without_earn", order.company_code, order.id)

    # 3 + 6. redeem: baris poin & jumlah poin vs reservasi
    redeem_by_ref: dict[str, list[LedgerEntry]] = defaultdict(list)
    for entry in inputs.ledger:
        if entry.source == "pos_redeem" and entry.source_ref:
            redeem_by_ref[entry.source_ref].append(entry)
    for ref, entries in sorted(redeem_by_ref.items()):
        first = entries[0]
        order = orders.get(ref)
        lines = inputs.points_lines.get(order.id) if order else None
        if order is None or lines is None or lines.count == 0:
            report.add("redeem_without_points_line", first.company_code, order.id if order else 0, first.id)
        reservation = inputs.reservations.get(ref)
        redeemed = -sum(e.delta_points for e in entries)
        if reservation is None or reservation.points != redeemed:
            report.add("redeem_points_mismatch", first.company_code, order.id if order else 0, first.id)

    # 4 + 5. baris poin vs reservasi committed
    for uuid in sorted(set(inputs.window_uuids) | set(redeem_by_ref)):
        order = orders.get(uuid)
        if order is None or not order.paid or order.is_refund:
            continue
        lines = inputs.points_lines.get(order.id)
        if lines is None or lines.count == 0:
            continue
        reservation = inputs.reservations.get(uuid)
        if reservation is None:
            if order.loyalty_state != "review" and to_utc(order.date_order) < settled_before:
                report.add("points_line_without_redeem", order.company_code, order.id)
            continue
        tolerance = Decimal(lines.count) if points_tolerance is None else points_tolerance
        if abs(abs(lines.total_incl) - Decimal(reservation.value_idr)) > tolerance:
            report.add("points_line_value_mismatch", order.company_code, order.id)

    # 7. reverse tanpa order refund / order asal
    for entry in inputs.ledger:
        if entry.source != "pos_reverse":
            continue
        refund_uuid = reverse_refund_uuid(entry.idempotency_key)
        refund = orders.get(refund_uuid) if refund_uuid else None
        original = orders.get(entry.source_ref) if entry.source_ref else None
        refund_ok = refund is not None and refund.paid and refund.is_refund
        original_ok = original is not None and original.paid and not original.is_refund
        if not (refund_ok and original_ok):
            report.add("reverse_without_refund_order", entry.company_code, refund.id if refund else 0, entry.id)
    return report


# ---------------------------------------------------------------- K-18-6: rekonsiliasi harian pembayaran Poin EFN
@dataclass(frozen=True)
class PointsPayment:
    """Satu `pos.payment` metode Poin EFN (Odoo). amount negatif = refund."""

    id: int
    order_id: int
    uuid: str
    company_code: str
    paid_at: datetime
    amount: Decimal


@dataclass(frozen=True)
class BffPointsMove:
    """Sisi BFF per order: redeem (value_idr reservasi committed) atau restore (points_restored_value, order refund)."""

    order_ref: str
    company_code: str
    at: datetime
    value_idr: int
    kind: str  # redeem | restore

    @property
    def signed(self) -> Decimal:
        return Decimal(self.value_idr if self.kind == "redeem" else -self.value_idr)


@dataclass
class DailyPoints:
    day: date
    company_code: str
    odoo_idr: Decimal = Decimal(0)
    redeem_idr: Decimal = Decimal(0)
    restored_idr: Decimal = Decimal(0)
    orders: set[str] = field(default_factory=set)
    mismatched: set[str] = field(default_factory=set)
    sample_order_ids: list[int] = field(default_factory=list)

    @property
    def diff(self) -> Decimal:
        return self.odoo_idr - (self.redeem_idr - self.restored_idr)


def wib_day(value: datetime) -> date:
    return to_utc(value).astimezone(WIB).date()


def wib_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=WIB)


def settled_days(since: datetime, now: datetime, settle: timedelta) -> tuple[date, date]:
    """Rentang hari WIB [awal, akhir) yang utuh di dalam jendela dan sudah lewat `settle` sejak tengah malam akhirnya
    (sinkron offline / commit BFF sempat masuk). Kosong bila awal >= akhir."""
    first = wib_day(since)
    if wib_start(first) < to_utc(since):
        first += timedelta(days=1)
    last = wib_day(to_utc(now) - settle)  # hari ini (belum utuh+settle) tidak termasuk
    return first, last


def daily_points_recon(
    payments: Iterable[PointsPayment],
    order_days: Mapping[str, tuple[date, str, int]],
    moves: Iterable[BffPointsMove],
    day_from: date,
    day_to: date,
    tolerance: Decimal = Decimal(0),
) -> list[DailyPoints]:
    """Σ pembayaran Poin EFN Odoo per PT x hari WIB vs Σ redeem − Σ restore BFF (K-18-6).

    `payments` = pembayaran di jendela hari (dedup id). `order_days` = uuid -> (hari WIB pembayaran poin pertama, PT,
    id order Odoo) untuk SEMUA order yang diketahui (termasuk di luar jendela). Sisi BFF diatribusikan ke hari & PT
    order Odoo-nya (commit/reverse BFF bisa terjadi lewat tengah malam setelah order offline disinkron); hanya yang
    tidak punya order Odoo memakai tanggal BFF (dan PT BFF). Hari di luar [day_from, day_to) diabaikan.
    """
    buckets: dict[tuple[date, str], DailyPoints] = {}
    per_order_odoo: dict[str, Decimal] = defaultdict(Decimal)
    per_order_bff: dict[str, Decimal] = defaultdict(Decimal)
    order_bucket: dict[str, tuple[date, str]] = {}

    def bucket(day: date, company: str) -> DailyPoints:
        key = (day, company or _UNKNOWN_COMPANY)
        if key not in buckets:
            buckets[key] = DailyPoints(day, key[1])
        return buckets[key]

    seen: set[int] = set()
    for p in payments:
        if p.id in seen:
            continue
        seen.add(p.id)
        day, company, _oid = order_days.get(p.uuid, (wib_day(p.paid_at), p.company_code, p.order_id))
        if not day_from <= day < day_to:
            continue
        b = bucket(day, company)
        b.odoo_idr += p.amount
        b.orders.add(p.uuid)
        per_order_odoo[p.uuid] += p.amount
        order_bucket[p.uuid] = (day, b.company_code)
    for m in moves:
        known = order_days.get(m.order_ref)
        day, company = (known[0], known[1]) if known else (wib_day(m.at), m.company_code)
        if not day_from <= day < day_to:
            continue
        b = bucket(day, company)
        if m.kind == "redeem":
            b.redeem_idr += Decimal(m.value_idr)
        else:
            b.restored_idr += Decimal(m.value_idr)
        b.orders.add(m.order_ref)
        per_order_bff[m.order_ref] += m.signed
        order_bucket.setdefault(m.order_ref, (day, b.company_code))
    for ref, (day, company) in sorted(order_bucket.items()):
        if abs(per_order_odoo.get(ref, Decimal(0)) - per_order_bff.get(ref, Decimal(0))) > tolerance:
            b = buckets[(day, company)]
            b.mismatched.add(ref)
            oid = order_days[ref][2] if ref in order_days else 0
            if oid and len(b.sample_order_ids) < SAMPLE_LIMIT:
                b.sample_order_ids.append(oid)
    return [buckets[k] for k in sorted(buckets)]


def add_daily_findings(report: ReconReport, days: Iterable[DailyPoints], tolerance: Decimal = Decimal(0)) -> None:
    """Satu temuan critical per PT x hari yang selisihnya > toleransi; sampel = id order Odoo yang tidak cocok."""
    for d in days:
        if abs(d.diff) <= tolerance:
            continue
        report.add("points_payment_daily_mismatch", d.company_code)
        finding = report.findings[("points_payment_daily_mismatch", d.company_code)]
        for oid in d.sample_order_ids:
            if len(finding.order_ids) < SAMPLE_LIMIT and oid not in finding.order_ids:
                finding.order_ids.append(oid)


def add_open_items(
    report: ReconReport, kind: str, rows: Iterable[Mapping[str, Any]], companies: Mapping[int, str]
) -> None:
    """Baris agregat Odoo (company_id, n, sample) -> temuan warning."""
    for row in rows:
        company = companies.get(int(row["company_id"] or 0), _UNKNOWN_COMPANY)
        sample = [int(x) for x in (row.get("sample") or []) if x]
        finding_kind = _OUTBOX_KIND[str(row["state"])] if kind == "outbox" else kind
        report.add(finding_kind, company, count=0)
        finding = report.findings[(finding_kind, company)]
        finding.count += int(row["n"])
        for order_id in sample:
            if len(finding.order_ids) < SAMPLE_LIMIT and order_id not in finding.order_ids:
                finding.order_ids.append(order_id)


def summary_payload(report: ReconReport, lookback: timedelta, settle: timedelta) -> dict[str, Any]:
    """JSON log `EFN_LOYALTY_RECON_ETL` — kunci pertama `status` (kontrak promtail, laporan Gelombang 14)."""
    return {
        "status": "ok",
        "anomalies": report.anomalies,
        "open_items": report.open_items,
        "lookback_hours": int(lookback.total_seconds() // 3600),
        "settle_hours": int(settle.total_seconds() // 3600),
        "orders_checked": report.orders_checked,
        "ledger_checked": report.ledger_checked,
        "kinds": report.by_kind(),
        "sample_order_ids": report.samples("order_ids"),
        "sample_ledger_ids": report.samples("ledger_ids"),
    }


# ---------------------------------------------------------------- orkestrasi
class Source(Protocol):
    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class ReconStore(Protocol):
    def last_loyalty_recon(self) -> datetime | None: ...

    def insert(self, table: str, columns: Sequence[str], rows: list[list[Any]]) -> None: ...


@dataclass(frozen=True)
class LoyaltyReconSettings:
    interval: timedelta = timedelta(hours=24)
    lookback: timedelta = timedelta(hours=72)
    settle: timedelta = timedelta(hours=6)
    aged: timedelta = timedelta(hours=24)
    open_window: timedelta = timedelta(days=30)
    points_codes: frozenset[str] = frozenset({"EFN-POINTS"})
    # Go-live loyalty POS (`ETL_LOYALTY_RECON_NOT_BEFORE`): order/ledger sebelum ini tidak direkonsiliasi (order lama
    # tidak pernah melewati hook efn_pos_loyalty_ledger -> positif palsu `paid_member_order_without_earn`).
    not_before: datetime | None = None
    # G18 (docs/04 §9.9, E-30): `product` = redeem sebagai baris produk Poin EFN (G13); `payment` = metode bayar
    # Poin EFN (`efn_is_loyalty_points`) + rekonsiliasi harian K-18-6. Ganti ke `payment` SETELAH E-30 terpasang.
    points_mode: str = "product"
    points_tolerance_idr: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.points_mode not in POINTS_MODES:
            raise ValueError(f"points_mode harus salah satu {POINTS_MODES}")
        if self.points_tolerance_idr < 0:
            raise ValueError("points_tolerance_idr negatif")

    @property
    def payment_mode(self) -> bool:
        return self.points_mode == "payment"

    def since(self, now: datetime) -> datetime:
        start = to_utc(now) - self.lookback
        return max(start, to_utc(self.not_before)) if self.not_before else start


def recon_due(store: ReconStore, cfg: LoyaltyReconSettings, now: datetime) -> bool:
    last = store.last_loyalty_recon()
    return last is None or to_utc(now) - to_utc(last) >= cfg.interval


def _chunks(values: Sequence[Any]) -> Iterable[list[Any]]:
    for i in range(0, len(values), _CHUNK):
        yield list(values[i : i + _CHUNK])


def _order(row: Mapping[str, Any], companies: Mapping[int, str]) -> OdooOrder:
    return OdooOrder(
        id=int(row["id"]),
        uuid=str(row["uuid"] or ""),
        company_code=companies.get(int(row["company_id"] or 0), _UNKNOWN_COMPANY),
        state=str(row["state"] or ""),
        is_refund=bool(row["is_refund"]) or bool(row["has_refund_lines"]),
        has_partner=bool(row["has_partner"]),
        loyalty_state=str(row["efn_loyalty_state"] or ""),
        date_order=row["date_order"],
        earn_enabled=bool(row["earn_enabled"]),
    )


def load_inputs(
    platform: Source, odoo: Source, cfg: LoyaltyReconSettings, now: datetime
) -> tuple[ReconInputs, dict[int, str]]:
    since = cfg.since(now)
    companies = {int(r["id"]): str(r["efn_brand_code"] or _UNKNOWN_COMPANY) for r in odoo.query(SQL_ODOO_COMPANIES, {})}
    earn_refs = {
        str(r["order_ref"]): str(r["company_code"]) for r in platform.query(SQL_PLATFORM_EARNS, {"since": since})
    }
    ledger = [
        LedgerEntry(
            int(r["id"]),
            str(r["company_code"]),
            int(r["delta_points"]),
            str(r["source"]),
            str(r["source_ref"] or ""),
            str(r["idempotency_key"]),
        )
        for r in platform.query(SQL_PLATFORM_LEDGER, {"since": since})
    ]
    orders: dict[str, OdooOrder] = {}
    for row in odoo.query(SQL_ODOO_ORDERS_WINDOW, {"since": since, "until": now}):
        order = _order(row, companies)
        orders[order.uuid] = order
    window_uuids = set(orders)
    wanted: set[str] = set(earn_refs)
    for entry in ledger:
        if entry.source_ref:
            wanted.add(entry.source_ref)
        refund_uuid = reverse_refund_uuid(entry.idempotency_key) if entry.source == "pos_reverse" else None
        if refund_uuid:
            wanted.add(refund_uuid)
    missing = sorted(wanted - set(orders))
    for chunk in _chunks(missing):
        for row in odoo.query(SQL_ODOO_ORDERS_BY_UUID, {"uuids": chunk}):
            order = _order(row, companies)
            orders[order.uuid] = order
    # Earn order jendela yang dicatat BFF sebelum `since` (tidak mungkin: earn >= order), tetapi order di awal jendela
    # dengan earn tercatat sebelum `since` karena jam berbeda -> cek per ref agar tidak positif palsu.
    unmatched = sorted(window_uuids - set(earn_refs))
    for chunk in _chunks(unmatched):
        for row in platform.query(SQL_PLATFORM_EARNS_BY_REF, {"refs": chunk}):
            earn_refs.setdefault(str(row["order_ref"]), orders[str(row["order_ref"])].company_code)
    order_ids = sorted({o.id for o in orders.values()})
    points_lines: dict[int, PointsLines] = {}
    outbox: dict[int, set[str]] = defaultdict(set)
    for chunk in _chunks(order_ids):
        points_rows = (
            odoo.query(SQL_ODOO_POINTS_PAYMENTS, {"ids": chunk})
            if cfg.payment_mode
            else odoo.query(SQL_ODOO_POINTS_LINES, {"codes": sorted(cfg.points_codes), "ids": chunk})
        )
        for row in points_rows:
            points_lines[int(row["order_id"])] = PointsLines(int(row["n"]), Decimal(str(row["total"])))
        for row in odoo.query(SQL_ODOO_OUTBOX_BY_ORDERS, {"ids": chunk}):
            if row["kind"] == "earn" and row["order_id"]:
                outbox[int(row["order_id"])].add(str(row["state"]))
    refs_with_points = sorted({o.uuid for o in orders.values() if o.id in points_lines})
    refs_with_points = sorted(set(refs_with_points) | {e.source_ref for e in ledger if e.source == "pos_redeem"})
    reservations: dict[str, Reservation] = {}
    for chunk in _chunks(refs_with_points):
        for row in platform.query(SQL_PLATFORM_RESERVATIONS, {"refs": chunk}):
            reservations[str(row["order_ref"])] = Reservation(int(row["points"]), int(row["value_idr"]))
    inputs = ReconInputs(earn_refs, ledger, orders, window_uuids, points_lines, dict(outbox), reservations)
    return inputs, companies


def _points_payment(row: Mapping[str, Any], companies: Mapping[int, str]) -> PointsPayment:
    return PointsPayment(
        id=int(row["id"]),
        order_id=int(row["order_id"]),
        uuid=str(row["uuid"] or ""),
        company_code=companies.get(int(row["company_id"] or 0), _UNKNOWN_COMPANY),
        paid_at=row["paid_at"],
        amount=Decimal(str(row["amount"] or 0)),
    )


def _moves(rows: Iterable[Mapping[str, Any]], kind: str) -> list[BffPointsMove]:
    return [
        BffPointsMove(str(r["order_ref"]), str(r["company_code"]), r["at"], int(r["value_idr"]), kind)
        for r in rows
        if r["order_ref"] and r["at"] is not None
    ]


def run_daily_points(
    platform: Source, odoo: Source, cfg: LoyaltyReconSettings, now: datetime, companies: Mapping[int, str]
) -> list[DailyPoints]:
    """K-18-6. Muat pembayaran Poin EFN (Odoo) & redeem/restore (BFF) untuk hari WIB yang sudah settle, lalu
    `daily_points_recon`. Order di luar jendela diambil per uuid hanya untuk menentukan harinya (bukan dijumlah)."""
    day_from, day_to = settled_days(cfg.since(now), now, cfg.settle)
    if day_from >= day_to:
        return []
    window = {"since": wib_start(day_from), "until": wib_start(day_to)}
    payments = [_points_payment(r, companies) for r in odoo.query(SQL_ODOO_POINTS_PAYMENTS_WINDOW, window)]
    moves = _moves(platform.query(SQL_PLATFORM_REDEEMS_WINDOW, window), "redeem")
    moves += _moves(platform.query(SQL_PLATFORM_RESTORES_WINDOW, window), "restore")
    # Sisi BFF untuk order Odoo jendela yang dicatat BFF di luar jendela (commit setelah tengah malam).
    have = {(m.kind, m.order_ref) for m in moves}
    refs = sorted({p.uuid for p in payments if p.uuid})
    for chunk in _chunks(refs):
        for kind, sql in (("redeem", SQL_PLATFORM_REDEEMS_BY_REF), ("restore", SQL_PLATFORM_RESTORES_BY_REF)):
            for m in _moves(platform.query(sql, {"refs": chunk}), kind):
                if (m.kind, m.order_ref) not in have:
                    have.add((m.kind, m.order_ref))
                    moves.append(m)
    known: list[PointsPayment] = list(payments)
    unknown = sorted({m.order_ref for m in moves} - set(refs))
    for chunk in _chunks(unknown):
        known += [_points_payment(r, companies) for r in odoo.query(SQL_ODOO_POINTS_PAYMENTS_BY_UUID, {"uuids": chunk})]
    order_days: dict[str, tuple[date, str, int]] = {}
    for p in sorted(known, key=lambda x: (to_utc(x.paid_at), x.id)):
        order_days.setdefault(p.uuid, (wib_day(p.paid_at), p.company_code, p.order_id))
    return daily_points_recon(payments, order_days, moves, day_from, day_to, cfg.points_tolerance_idr)


def daily_payload(days: Sequence[DailyPoints], tolerance: Decimal = Decimal(0)) -> dict[str, Any]:
    bad = [d for d in days if abs(d.diff) > tolerance]
    return {
        "days_checked": len({d.day for d in days}),
        "mismatched": [
            {"day": d.day.isoformat(), "company": d.company_code, "diff_idr": str(d.diff), "orders": len(d.mismatched)}
            for d in bad
        ],
    }


def daily_rows(days: Sequence[DailyPoints], run_at: datetime) -> list[list[Any]]:
    return [
        [
            run_at,
            d.day,
            d.company_code,
            d.odoo_idr,
            d.redeem_idr,
            d.restored_idr,
            d.diff,
            len(d.orders),
            len(d.mismatched),
        ]
        for d in days
    ]


def run_loyalty_recon(
    platform: Source, odoo: Source, store: ReconStore, cfg: LoyaltyReconSettings, now: datetime
) -> ReconReport:
    """Satu siklus rekonsiliasi. Melempar bila sumber gagal (pemanggil mencatat heartbeat error)."""
    inputs, companies = load_inputs(platform, odoo, cfg, now)
    report = reconcile(inputs, now, cfg.settle, points_tolerance=cfg.points_tolerance_idr if cfg.payment_mode else None)
    daily: list[DailyPoints] = []
    if cfg.payment_mode:
        daily = run_daily_points(platform, odoo, cfg, now, companies)
        add_daily_findings(report, daily, cfg.points_tolerance_idr)
    open_since = now - cfg.open_window
    if cfg.not_before:
        open_since = max(to_utc(open_since), to_utc(cfg.not_before))
    params = {"open_since": open_since, "aged_before": now - cfg.aged}
    add_open_items(report, "outbox", odoo.query(SQL_ODOO_OUTBOX_OPEN, params), companies)
    add_open_items(report, "order_review_aged", odoo.query(SQL_ODOO_ORDER_REVIEW, params), companies)
    add_open_items(report, "reservation_review_aged", odoo.query(SQL_ODOO_RESERVATION_REVIEW, params), companies)

    payload = summary_payload(report, cfg.lookback, cfg.settle)
    if cfg.payment_mode:
        payload["points_daily"] = daily_payload(daily, cfg.points_tolerance_idr)
    level = logging.WARNING if report.anomalies else logging.INFO
    log.log(level, "%s %s", RECON_MARKER, json.dumps(payload, separators=(",", ":"), sort_keys=False))
    rows = [
        [now, kind, KIND_SEVERITY[kind], company, f.count, list(f.order_ids), list(f.ledger_ids)]
        for (kind, company), f in sorted(report.findings.items())
        if f.count
    ]
    # Temuan dulu, penanda run terakhir: run tanpa penanda diulang siklus berikutnya (temuan ganda dibedakan run_at;
    # view membaca run_at terakhir yang punya penanda).
    if rows:
        store.insert(FINDING_TABLE, FINDING_COLUMNS, rows)
    if daily:
        store.insert(DAILY_TABLE, DAILY_COLUMNS, daily_rows(daily, now))
    store.insert(
        RUN_TABLE,
        RUN_COLUMNS,
        [
            [
                now,
                "ok",
                payload["lookback_hours"],
                payload["settle_hours"],
                report.orders_checked,
                report.ledger_checked,
                report.anomalies,
                report.open_items,
            ]
        ],
    )
    return report
