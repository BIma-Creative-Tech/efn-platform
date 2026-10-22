"""Gelombang 22 (docs/04 §14.C.5, W-12): rekonsiliasi poin ONLINE — ledger BFF <-> pembayaran Poin EFN di faktur SO.

Perluasan K-18-6 (efn_etl/loyalty_recon.py, POS) ke penjualan online: per PT x tanggal, Σ pembayaran jurnal Poin EFN
(POS + faktur/nota kredit SO online) = Σ (pos_redeem + online_redeem) − Σ restore. Bagian POS tetap di
loyalty_recon.py; modul ini menghitung bagian online (baris `loyalty_points_daily` kanal `online`) dan temuan per bagian
(`part_id` BFF = `efn.online.order.platform_order_ref`). View ClickHouse `v_loyalty_points_daily_total` menjumlah
kedua kanal (persamaan kontrak §14.C.5).

Sumber (tanpa PII):
- BFF `platform.loyalty_ledger` (kolom efn_readonly yang SUDAH di-grant; tanpa member_ref_hash): entri
  `source='online'`, `reason` online_redeem | online_restore | online_earn | online_reverse, `source_ref = part_id`
  (§14.C.3). Nilai Rupiah redeem = −Σ delta × nilai poin (C.7 API3: nilai = poin × `point_value_idr`, L-62 global);
  restore = Σ delta × nilai poin.
- Odoo view `efn_etl.online_points_payment` (dibuat `sql/odoo-readonly-grants.sql` G22, pemilik efn_odoo):
  `account.payment` di jurnal Poin EFN yang tertaut ke faktur (`efn.online.order.invoice_id`, inbound, +) atau nota
  kredit refund (`efn.online.refund.credit_note_id`, outbound, −). `paid_on` = tanggal pembayaran Odoo.
- Odoo view `efn_etl.online_order_ref`: state bagian + jumlah refund non-gagal (cek earn/clawback).

Kelas temuan (critical, masuk `anomalies` -> alert EfnLoyaltyEtlReconAnomaly yang sudah ada):
- `online_points_payment_mismatch`: per bagian, Σ pembayaran Poin EFN faktur (Odoo) ≠ −Σ online_redeem × nilai poin
  (BFF) di luar toleransi (`ETL_LOYALTY_POINTS_TOLERANCE_IDR`) — termasuk redeem BFF tanpa pembayaran Odoo dan
  sebaliknya;
- `online_points_restore_mismatch`: per bagian, nilai restore Odoo (Σ pembayaran nota kredit) − poin restore BFF ×
  nilai poin di luar [0, nilai poin × jumlah refund Odoo) — sisa pembulatan floor BFF (pola SEC-118);
- `online_earn_without_picked_up`: entri online_earn untuk bagian yang tidak ada di Odoo / belum `picked_up`;
- `online_reverse_without_refund`: entri online_reverse (clawback) untuk bagian tanpa refund Odoo yang tidak gagal.

Jendela: hari WIB utuh yang sudah lewat `settle` (sama dengan K-18-6). Bagian yang punya aktivitas (salah satu sisi)
di jendela diperiksa dengan SELURUH riwayatnya (diambil per ref); bagian dengan aktivitas setelah batas jendela
(belum settle) dilewati dan diperiksa run berikutnya. Baris harian mengatribusikan pembayaran Odoo ke `paid_on`-nya
dan entri BFF ke tanggal WIB entri itu (PT = PT bagian di Odoo bila diketahui) — selisih harian kecil di sekitar
tengah malam / sisa pembulatan restore bisa muncul di dashboard; temuan hanya dari pemeriksaan per bagian.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from efn_etl.loyalty_recon import wib_day as _wib_day
from efn_etl.watermark import to_utc

ONLINE_SOURCE = "online"
REDEEM = "online_redeem"
RESTORE = "online_restore"
EARN = "online_earn"
REVERSE = "online_reverse"
ONLINE_REASONS = (REDEEM, RESTORE, EARN, REVERSE)
PICKED_UP = "picked_up"
ONLINE_KINDS = (
    "online_points_payment_mismatch",
    "online_points_restore_mismatch",
    "online_earn_without_picked_up",
    "online_reverse_without_refund",
)
SAMPLE_LIMIT = 10
_UNKNOWN_COMPANY = "?"
_CHUNK = 5000

# ---------------------------------------------------------------- SQL BFF (kolom efn_readonly yang sudah ada)
_PLATFORM_ONLINE_LEDGER = """
SELECT id, company_code, delta_points, reason, source_ref, created_at
FROM platform.loyalty_ledger
WHERE source = 'online' AND reason IN ('online_redeem', 'online_restore', 'online_earn', 'online_reverse')
"""
SQL_PLATFORM_ONLINE_LEDGER_WINDOW = _PLATFORM_ONLINE_LEDGER + " AND created_at >= %(since)s AND created_at < %(until)s"
SQL_PLATFORM_ONLINE_LEDGER_BY_REF = _PLATFORM_ONLINE_LEDGER + " AND source_ref = ANY(%(refs)s)"

# ---------------------------------------------------------------- SQL Odoo (view G22, sql/odoo-readonly-grants.sql)
_ODOO_ONLINE_PAYMENTS = """
SELECT payment_id, online_order_id, part_ref, company_id, paid_on, amount
FROM efn_etl.online_points_payment
"""
SQL_ODOO_ONLINE_PAYMENTS_WINDOW = _ODOO_ONLINE_PAYMENTS + "WHERE paid_on >= %(day_from)s AND paid_on < %(day_to)s"
SQL_ODOO_ONLINE_PAYMENTS_BY_REF = _ODOO_ONLINE_PAYMENTS + "WHERE part_ref = ANY(%(refs)s)"
SQL_ODOO_ONLINE_ORDERS_BY_REF = """
SELECT online_order_id, part_ref, company_id, state, refunds
FROM efn_etl.online_order_ref
WHERE part_ref = ANY(%(refs)s)
"""


# ---------------------------------------------------------------- model data
@dataclass(frozen=True)
class OnlinePointsPayment:
    """`account.payment` jurnal Poin EFN pada faktur (+) / nota kredit (−) bagian online."""

    id: int
    online_order_id: int
    part_ref: str
    company_code: str
    paid_on: date
    amount: Decimal


@dataclass(frozen=True)
class OnlineLedgerEntry:
    id: int
    part_ref: str
    company_code: str
    reason: str
    delta_points: int
    created_at: datetime


@dataclass(frozen=True)
class OnlineOrderRef:
    id: int
    part_ref: str
    company_code: str
    state: str
    refunds: int


@dataclass
class OnlineDaily:
    """Satu baris `loyalty_points_daily` kanal `online` (PT x tanggal)."""

    day: date
    company_code: str
    odoo_idr: Decimal = Decimal(0)  # Σ pembayaran Poin EFN faktur − nota kredit (Odoo)
    redeem_idr: Decimal = Decimal(0)  # Σ online_redeem × nilai poin (BFF)
    restored_points_idr: Decimal = Decimal(0)  # Σ online_restore × nilai poin (BFF)
    parts: set[str] = field(default_factory=set)
    mismatched: set[str] = field(default_factory=set)
    restore_mismatched: set[str] = field(default_factory=set)

    @property
    def diff(self) -> Decimal:
        return self.odoo_idr - (self.redeem_idr - self.restored_points_idr)


@dataclass
class OnlineFinding:
    count: int = 0
    order_ids: list[int] = field(default_factory=list)
    ledger_ids: list[int] = field(default_factory=list)

    def add(self, order_id: int = 0, ledger_ids: Iterable[int] = ()) -> None:
        self.count += 1
        if order_id and len(self.order_ids) < SAMPLE_LIMIT and order_id not in self.order_ids:
            self.order_ids.append(order_id)
        for ledger_id in ledger_ids:
            if ledger_id and len(self.ledger_ids) < SAMPLE_LIMIT and ledger_id not in self.ledger_ids:
                self.ledger_ids.append(ledger_id)


@dataclass
class OnlineReconResult:
    daily: list[OnlineDaily]
    findings: dict[tuple[str, str], OnlineFinding]
    parts_checked: int = 0
    parts_deferred: int = 0  # aktivitas setelah batas jendela -> diperiksa run berikutnya


# ---------------------------------------------------------------- logika murni
def norm_ref(value: Any) -> str:
    """part_id = UUID; Odoo menyimpan huruf kecil (`_efn_find`), BFF uuid Postgres juga kecil -> samakan."""
    return ("" if value is None else str(value)).strip().lower()


def restore_within_rounding(
    odoo_restore_idr: Decimal, bff_restore_points: int, refunds: int, point_value_idr: int, tolerance: Decimal
) -> bool:
    """Sisa pembulatan floor BFF per refund: 0 <= nilai Odoo − poin × nilai < nilai poin (× jumlah refund Odoo).

    Tanpa refund di Odoo, BFF tidak boleh me-restore apa pun (sisa harus 0 ± toleransi)."""
    residual = odoo_restore_idr - Decimal(bff_restore_points * point_value_idr)
    if refunds > 0:
        return -tolerance <= residual < Decimal(point_value_idr * refunds) + tolerance
    return abs(residual) <= tolerance


def online_points_recon(
    payments: Iterable[OnlinePointsPayment],
    ledger: Iterable[OnlineLedgerEntry],
    orders: Mapping[str, OnlineOrderRef],
    day_from: date,
    day_to: date,
    point_value_idr: int,
    tolerance: Decimal = Decimal(0),
) -> OnlineReconResult:
    """`payments`/`ledger` = baris jendela + seluruh riwayat bagian yang aktif di jendela (dedup id di sini).
    `orders` = part_ref -> bagian Odoo (untuk PT, cek earn/clawback, sampel id)."""
    if point_value_idr < 1:
        raise ValueError("point_value_idr harus >= 1")
    pays = list({p.id: p for p in payments if p.part_ref}.values())
    entries = list({e.id: e for e in ledger if e.part_ref}.values())

    pays_by_part: dict[str, list[OnlinePointsPayment]] = defaultdict(list)
    for p in pays:
        pays_by_part[p.part_ref].append(p)
    entries_by_part: dict[str, list[OnlineLedgerEntry]] = defaultdict(list)
    for e in entries:
        entries_by_part[e.part_ref].append(e)

    def part_company(ref: str, fallback: str) -> str:
        order = orders.get(ref)
        if order is not None and order.company_code:
            return order.company_code
        part_pays = pays_by_part.get(ref)
        if part_pays:
            return part_pays[0].company_code
        return fallback or _UNKNOWN_COMPANY

    def part_order_id(ref: str) -> int:
        order = orders.get(ref)
        if order is not None:
            return order.id
        part_pays = pays_by_part.get(ref)
        return part_pays[0].online_order_id if part_pays else 0

    def in_window(day: date) -> bool:
        return day_from <= day < day_to

    # --- baris harian (atribusi per tanggal masing-masing sisi)
    buckets: dict[tuple[date, str], OnlineDaily] = {}

    def bucket(day: date, company: str) -> OnlineDaily:
        key = (day, company or _UNKNOWN_COMPANY)
        if key not in buckets:
            buckets[key] = OnlineDaily(day, key[1])
        return buckets[key]

    for p in pays:
        if in_window(p.paid_on):
            b = bucket(p.paid_on, part_company(p.part_ref, p.company_code))
            b.odoo_idr += p.amount
            b.parts.add(p.part_ref)
    for e in entries:
        if e.reason not in (REDEEM, RESTORE):
            continue
        day = _wib_day(e.created_at)
        if not in_window(day):
            continue
        b = bucket(day, part_company(e.part_ref, e.company_code))
        value = Decimal(e.delta_points * point_value_idr)
        if e.reason == REDEEM:
            b.redeem_idr += -value
        else:
            b.restored_points_idr += value
        b.parts.add(e.part_ref)

    # --- pemeriksaan per bagian
    findings: dict[tuple[str, str], OnlineFinding] = defaultdict(OnlineFinding)
    checked = deferred = 0
    money_refs = {r for r, es in entries_by_part.items() if any(e.reason in (REDEEM, RESTORE) for e in es)}
    for ref in sorted(set(pays_by_part) | money_refs):
        part_pays = pays_by_part.get(ref, [])
        part_entries = [e for e in entries_by_part.get(ref, []) if e.reason in (REDEEM, RESTORE)]
        days = [p.paid_on for p in part_pays] + [_wib_day(e.created_at) for e in part_entries]
        if not any(in_window(d) for d in days):
            continue  # hanya terbawa riwayat; bukan bagian aktif jendela ini
        if any(d >= day_to for d in days):
            deferred += 1
            continue
        checked += 1
        home_day = min(d for d in days if in_window(d))
        company = part_company(ref, part_entries[0].company_code if part_entries else "")
        home = bucket(home_day, company)
        odoo_redeem = sum((p.amount for p in part_pays if p.amount > 0), Decimal(0))
        odoo_restore = -sum((p.amount for p in part_pays if p.amount < 0), Decimal(0))
        odoo_refunds = sum(1 for p in part_pays if p.amount < 0)
        bff_redeem = Decimal(-sum(e.delta_points for e in part_entries if e.reason == REDEEM) * point_value_idr)
        bff_restore_points = sum(e.delta_points for e in part_entries if e.reason == RESTORE)
        ledger_ids = sorted(e.id for e in part_entries)
        order_id = part_order_id(ref)
        if abs(odoo_redeem - bff_redeem) > tolerance:
            findings[("online_points_payment_mismatch", company)].add(order_id, ledger_ids)
            home.mismatched.add(ref)
        if not restore_within_rounding(odoo_restore, bff_restore_points, odoo_refunds, point_value_idr, tolerance):
            findings[("online_points_restore_mismatch", company)].add(order_id, ledger_ids)
            home.restore_mismatched.add(ref)

    # --- earn / clawback vs status bagian Odoo (entri jendela saja)
    for e in sorted(entries, key=lambda x: x.id):
        if e.reason not in (EARN, REVERSE) or not in_window(_wib_day(e.created_at)):
            continue
        order = orders.get(e.part_ref)
        company = part_company(e.part_ref, e.company_code)
        if e.reason == EARN and (order is None or order.state != PICKED_UP):
            findings[("online_earn_without_picked_up", company)].add(order.id if order else 0, [e.id])
        elif e.reason == REVERSE and (order is None or order.refunds < 1):
            findings[("online_reverse_without_refund", company)].add(order.id if order else 0, [e.id])

    daily = [buckets[k] for k in sorted(buckets)]
    return OnlineReconResult(daily, dict(findings), checked, deferred)


def online_daily_payload(result: OnlineReconResult, tolerance: Decimal = Decimal(0)) -> dict[str, Any]:
    """Ringkasan untuk log EFN_LOYALTY_RECON_ETL (kunci `points_daily_online`); tanpa PII, tanpa part_id."""
    return {
        "days_checked": len({d.day for d in result.daily}),
        "parts_checked": result.parts_checked,
        "parts_deferred": result.parts_deferred,
        "mismatched": [
            {
                "day": d.day.isoformat(),
                "company": d.company_code,
                "diff_idr": str(d.diff),
                "parts": len(d.mismatched),
                "restore_parts": len(d.restore_mismatched),
            }
            for d in result.daily
            if d.mismatched or d.restore_mismatched or abs(d.diff) > tolerance
        ],
    }


def online_daily_rows(days: Sequence[OnlineDaily], run_at: datetime) -> list[list[Any]]:
    """Baris `loyalty_points_daily` (urutan = loyalty_recon.DAILY_COLUMNS) kanal `online`.
    `bff_restored_idr` = `bff_restored_points_idr` (BFF tidak membuka nilai Rupiah restore online ke efn_readonly)."""
    return [
        [
            run_at,
            d.day,
            d.company_code,
            d.odoo_idr,
            d.redeem_idr,
            d.restored_points_idr,
            d.diff,
            len(d.parts),
            len(d.mismatched),
            d.restored_points_idr,
            len(d.restore_mismatched),
            "online",
        ]
        for d in days
    ]


# ---------------------------------------------------------------- orkestrasi (I/O lewat Protocol)
class Source(Protocol):
    def query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


def _chunks(values: Sequence[str]) -> Iterable[list[str]]:
    for i in range(0, len(values), _CHUNK):
        yield list(values[i : i + _CHUNK])


def _payment(row: Mapping[str, Any], companies: Mapping[int, str]) -> OnlinePointsPayment:
    paid_on = row["paid_on"]
    if isinstance(paid_on, datetime):
        paid_on = paid_on.date()
    return OnlinePointsPayment(
        id=int(row["payment_id"]),
        online_order_id=int(row["online_order_id"] or 0),
        part_ref=norm_ref(row["part_ref"]),
        company_code=companies.get(int(row["company_id"] or 0), _UNKNOWN_COMPANY),
        paid_on=paid_on,
        amount=Decimal(str(row["amount"] or 0)),
    )


def _entry(row: Mapping[str, Any]) -> OnlineLedgerEntry:
    return OnlineLedgerEntry(
        id=int(row["id"]),
        part_ref=norm_ref(row["source_ref"]),
        company_code=str(row["company_code"] or ""),
        reason=str(row["reason"] or ""),
        delta_points=int(row["delta_points"]),
        created_at=to_utc(row["created_at"]),
    )


def _order(row: Mapping[str, Any], companies: Mapping[int, str]) -> OnlineOrderRef:
    return OnlineOrderRef(
        id=int(row["online_order_id"]),
        part_ref=norm_ref(row["part_ref"]),
        company_code=companies.get(int(row["company_id"] or 0), _UNKNOWN_COMPANY),
        state=str(row["state"] or ""),
        refunds=int(row["refunds"] or 0),
    )


def load_and_reconcile(
    platform: Source,
    odoo: Source,
    companies: Mapping[int, str],
    day_from: date,
    day_to: date,
    since: datetime,
    until: datetime,
    point_value_idr: int,
    tolerance: Decimal = Decimal(0),
) -> OnlineReconResult:
    """Muat jendela [day_from, day_to) (BFF: [since, until) = batas WIB yang sama), lengkapi riwayat per bagian, lalu
    `online_points_recon`. Tanpa SELECT * dan tanpa query tanpa filter (R-O03)."""
    if day_from >= day_to:
        return OnlineReconResult([], {})
    payments = [
        _payment(r, companies)
        for r in odoo.query(SQL_ODOO_ONLINE_PAYMENTS_WINDOW, {"day_from": day_from, "day_to": day_to})
    ]
    entries = [_entry(r) for r in platform.query(SQL_PLATFORM_ONLINE_LEDGER_WINDOW, {"since": since, "until": until})]
    refs = sorted({p.part_ref for p in payments if p.part_ref} | {e.part_ref for e in entries if e.part_ref})
    orders: dict[str, OnlineOrderRef] = {}
    for chunk in _chunks(refs):
        payments += [_payment(r, companies) for r in odoo.query(SQL_ODOO_ONLINE_PAYMENTS_BY_REF, {"refs": chunk})]
        entries += [_entry(r) for r in platform.query(SQL_PLATFORM_ONLINE_LEDGER_BY_REF, {"refs": chunk})]
        for row in odoo.query(SQL_ODOO_ONLINE_ORDERS_BY_REF, {"refs": chunk}):
            order = _order(row, companies)
            orders[order.part_ref] = order
    return online_points_recon(payments, entries, orders, day_from, day_to, point_value_idr, tolerance)
