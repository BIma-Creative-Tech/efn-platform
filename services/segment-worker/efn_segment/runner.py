"""Orkestrasi satu run segmen per PT (ADR-047 §3.4, docs/04 §28.7). Dependensi diinjeksi agar bisa diuji tanpa
ClickHouse/BFF/Odoo.

Urutan: baca ClickHouse (hari selesai: as_of = kemarin WIB) -> hitung segmen -> segmen < SEGMENT_MIN_SIZE dilewati ->
validasi SEMUA payload lokal -> per segmen: kirim semua chunk anggota ke BFF berurutan (retry di klien, idempoten
`(run_id, segment_code, chunk_no)`) -> BARU kirim ringkasan ke Odoo (marketing tidak bisa menyetujui kampanye yang
anggotanya belum lengkap di BFF). Kegagalan satu segmen tidak menghentikan segmen lain; ringkasan segmen gagal tidak
dikirim. Run baru = `run_id` baru (UUIDv4); run setengah jalan tidak pernah punya ringkasan di Odoo.
Log: hanya kode segmen, jumlah, status, kode error — tidak pernah subject_key atau isi payload.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from efn_segment.config import Params
from efn_segment.contract import ContractError, members_payloads, proposal_payload, total_members
from efn_segment.schedule import WIB
from efn_segment.segments import Segment, SubjectProduct, SubjectSearch, compute_segments
from efn_segment.transport import HttpError

log = logging.getLogger(__name__)


class DataSource(Protocol):
    def subject_products(self, company: str, start: date, end: date, kinds: Sequence[str]) -> list[SubjectProduct]: ...
    def subject_searches(
        self, company: str, start: date, end: date, kinds: Sequence[str], min_no_result: int
    ) -> list[SubjectSearch]: ...
    def categories(self, company: str) -> dict[int, str]: ...
    def market_prices(self, company: str) -> dict[int, float]: ...


class BffApi(Protocol):
    def send_members(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class OdooApi(Protocol):
    def proposal_upsert(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SegmentReport:
    code: str
    status: str  # sent | dry_run | skipped_small | failed
    member_count: int = 0
    chunks: int = 0
    error: str = ""


@dataclass(frozen=True)
class RunReport:
    run_id: str
    company: str
    status: str  # ok | partial | failed | dry_run
    as_of: date | None = None
    segments: tuple[SegmentReport, ...] = field(default_factory=tuple)
    duration_s: float = 0.0
    error: str = ""


def business_yesterday(now: datetime) -> date:
    return now.astimezone(WIB).date() - timedelta(days=1)


def windows(as_of: date, lookback: int, wishlist_lookback: int) -> tuple[date, date]:
    """(awal jendela pendek, awal jendela panjang); akhir keduanya = as_of (inklusif)."""
    return as_of - timedelta(days=lookback - 1), as_of - timedelta(days=wishlist_lookback - 1)


def _code(exc: BaseException) -> str:
    if isinstance(exc, ContractError):
        return exc.code
    if isinstance(exc, HttpError):
        return f"{exc.kind}" + (f":{exc.code}" if exc.code else "")
    return type(exc).__name__  # tanpa pesan exception mentah (bisa memuat potongan SQL/nilai)


@dataclass(frozen=True)
class _Prepared:
    segment: Segment
    members: list[dict[str, Any]]
    proposal: dict[str, Any]


def run_company(
    company: str,
    source: DataSource,
    bff: BffApi | None,
    odoo: OdooApi | None,
    params: Params,
    now: datetime | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RunReport:
    """Satu run untuk satu PT. `bff=None` dan `odoo=None` = dry-run (hanya baca ClickHouse + validasi + log)."""
    now = now or datetime.now(UTC)
    dry_run = bff is None or odoo is None
    run_id = str(uuid.uuid4())
    started = clock()
    sp = params.segment
    as_of = business_yesterday(now)
    try:
        short_start, long_start = windows(as_of, sp.lookback_days, sp.wishlist_lookback_days)
        kinds = sp.kinds()
        short = source.subject_products(company, short_start, as_of, kinds)
        need_long = "wishlist_idle" in sp.segments or "price_sensitive" in sp.segments
        long = source.subject_products(company, long_start, as_of, kinds) if need_long else []
        searches = (
            source.subject_searches(company, short_start, as_of, kinds, sp.min_no_result)
            if "search_no_result" in sp.segments
            else []
        )
        need_prices = "price_sensitive" in sp.segments
        categories = source.categories(company) if need_prices else {}
        market = source.market_prices(company) if need_prices else {}
        segments = compute_segments(short, long, searches, categories, market, as_of, sp)
        prepared: list[_Prepared] = []
        reports: list[SegmentReport] = []
        for seg in segments:
            if seg.member_count < params.min_segment_size:
                reports.append(SegmentReport(seg.code, "skipped_small", seg.member_count))
                continue
            members = members_payloads(company, run_id, seg.code, seg.members, params.chunk_size)
            if total_members(members) != seg.member_count:  # defensif: chunking tidak boleh kehilangan anggota
                raise ContractError("member_count_mismatch")
            proposal = proposal_payload(company, run_id, seg, sp, as_of, params.k_min, params.top_n, now)
            prepared.append(_Prepared(seg, members, proposal))
    except Exception as exc:
        code = _code(exc)
        log.error("segment run=%s company=%s gagal sebelum kirim: %s", run_id, company, code)
        report = RunReport(run_id, company, "failed", as_of, duration_s=round(clock() - started, 1), error=code)
        _log_report(report)
        return report

    if dry_run:
        for p in prepared:
            reports.append(SegmentReport(p.segment.code, "dry_run", p.segment.member_count, len(p.members)))
        report = RunReport(run_id, company, "dry_run", as_of, tuple(reports), round(clock() - started, 1))
        _log_report(report)
        return report

    assert bff is not None and odoo is not None
    for p in prepared:
        reports.append(_send_segment(p, bff, odoo))
    failed = [r for r in reports if r.status == "failed"]
    sent = [r for r in reports if r.status == "sent"]
    status = "ok" if not failed else ("partial" if sent else "failed")
    report = RunReport(run_id, company, status, as_of, tuple(reports), round(clock() - started, 1))
    _log_report(report)
    return report


def _send_segment(p: _Prepared, bff: BffApi, odoo: OdooApi) -> SegmentReport:
    code = p.segment.code
    try:
        for payload in p.members:  # berurutan; retry transport di klien
            bff.send_members(payload)
        odoo.proposal_upsert(p.proposal)
    except Exception as exc:
        err = _code(exc)
        log.error("segmen %s gagal dikirim: %s", code, err)
        return SegmentReport(code, "failed", p.segment.member_count, len(p.members), err)
    return SegmentReport(code, "sent", p.segment.member_count, len(p.members))


def _log_report(r: RunReport) -> None:
    segs = ",".join(f'{{"code":"{s.code}","status":"{s.status}","members":{s.member_count}}}' for s in r.segments)
    log.info(
        'EFN_SEGMENT_RUN {"run_id":"%s","company":"%s","status":"%s","as_of":"%s","segments":[%s],'
        '"duration_s":%.1f,"error":"%s"}',
        r.run_id,
        r.company,
        r.status,
        r.as_of.isoformat() if r.as_of else "",
        segs,
        r.duration_s,
        r.error,
    )
