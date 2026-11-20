"""Orkestrasi run: urutan BFF -> Odoo, segmen kecil dilewati, dry-run, kegagalan parsial, tanpa subject_key di log."""

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import pytest
from conftest import key

from efn_segment.config import Params
from efn_segment.runner import business_yesterday, run_company, windows
from efn_segment.segments import SegmentParams, SubjectProduct, SubjectSearch
from efn_segment.transport import HttpError

NOW = datetime(2026, 11, 20, 2, 15, tzinfo=UTC)  # 09:15 WIB 20 Nov -> as_of 19 Nov


class FakeSource:
    def __init__(self, n_oos: int = 12, n_search: int = 3, fail: bool = False) -> None:
        self.n_oos = n_oos
        self.n_search = n_search
        self.fail = fail
        self.calls: list[tuple[str, Any]] = []

    def subject_products(self, company: str, start: date, end: date, kinds: Sequence[str]) -> list[SubjectProduct]:
        if self.fail:
            raise ConnectionError("clickhouse down: SELECT ... secret")
        self.calls.append(("products", (company, start, end, tuple(kinds))))
        return [SubjectProduct(key(i), "member", 10, views=1, oos_views=1) for i in range(self.n_oos)]

    def subject_searches(
        self, company: str, start: date, end: date, kinds: Sequence[str], min_no_result: int
    ) -> list[SubjectSearch]:
        self.calls.append(("searches", (company, start, end)))
        return [SubjectSearch(key(f"s{i}"), "member", 2, 1) for i in range(self.n_search)]

    def categories(self, company: str) -> dict[int, str]:
        return {10: "Susu"}

    def market_prices(self, company: str) -> dict[int, float]:
        return {}


class FakeBff:
    def __init__(self, fail_segment: str | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail_segment = fail_segment
        self.log: list[str] = []

    def send_members(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload["segment_code"] == self.fail_segment:
            raise HttpError("rejected", "invalid_payload", 422)
        self.sent.append(payload)
        self.log.append(f"bff:{payload['segment_code']}:{payload['chunk_no']}")
        return {"accepted": len(payload["subject_keys"])}


class FakeOdoo:
    def __init__(self, shared_log: list[str]) -> None:
        self.sent: list[dict[str, Any]] = []
        self.log = shared_log

    def proposal_upsert(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(payload)
        self.log.append(f"odoo:{payload['segment_code']}")
        return {"ok": True}


def _params(**seg: Any) -> Params:
    return Params(segment=SegmentParams(**seg), min_segment_size=10, chunk_size=5)


def test_dates() -> None:
    assert business_yesterday(NOW) == date(2026, 11, 19)
    assert business_yesterday(datetime(2026, 11, 19, 18, 0, tzinfo=UTC)) == date(2026, 11, 19)  # 01:00 WIB 20 Nov
    assert windows(date(2026, 11, 19), 14, 60) == (date(2026, 11, 6), date(2026, 9, 21))


def test_run_sends_members_then_proposal(caplog: pytest.LogCaptureFixture) -> None:
    source, bff = FakeSource(), FakeBff()
    odoo = FakeOdoo(bff.log)
    caplog.set_level(logging.INFO)
    report = run_company("SMD", source, bff, odoo, _params(), NOW)
    assert report.status == "ok"
    statuses = {s.code: s.status for s in report.segments}
    assert statuses["oos_viewed"] == "sent"
    assert statuses["search_no_result"] == "skipped_small"  # 3 < 10
    assert statuses["viewed_not_bought"] == "skipped_small"  # 0 anggota
    # 12 anggota, chunk 5 -> 3 chunk, semua ke BFF SEBELUM ringkasan Odoo.
    assert bff.log == ["bff:oos_viewed:0", "bff:oos_viewed:1", "bff:oos_viewed:2", "odoo:oos_viewed"]
    assert sum(len(p["subject_keys"]) for p in bff.sent) == 12
    assert {p["run_id"] for p in bff.sent} == {report.run_id} == {odoo.sent[0]["run_id"]}
    assert odoo.sent[0]["member_count"] == 12 and odoo.sent[0]["company_code"] == "SMD"
    assert ("products", ("SMD", date(2026, 11, 6), date(2026, 11, 19), ("member",))) in source.calls
    assert all(key(i) not in caplog.text for i in range(12))
    assert "EFN_SEGMENT_RUN" in caplog.text


def test_dry_run_makes_no_calls() -> None:
    report = run_company("SMD", FakeSource(), None, None, _params(), NOW)
    assert report.status == "dry_run"
    assert {s.code: s.status for s in report.segments}["oos_viewed"] == "dry_run"


def test_bff_failure_blocks_odoo_for_that_segment_only() -> None:
    source = FakeSource(n_oos=12, n_search=11)
    bff = FakeBff(fail_segment="oos_viewed")
    odoo = FakeOdoo(bff.log)
    report = run_company("SMD", source, bff, odoo, _params(), NOW)
    assert report.status == "partial"
    statuses = {s.code: (s.status, s.error) for s in report.segments}
    assert statuses["oos_viewed"] == ("failed", "rejected:invalid_payload")
    assert statuses["search_no_result"][0] == "sent"
    assert [p["segment_code"] for p in odoo.sent] == ["search_no_result"]


def test_all_segments_failed_is_failed() -> None:
    bff = FakeBff(fail_segment="oos_viewed")
    report = run_company("SMD", FakeSource(n_search=0), bff, FakeOdoo(bff.log), _params(), NOW)
    assert report.status == "failed"


def test_source_failure_no_calls_no_raw_error(caplog: pytest.LogCaptureFixture) -> None:
    bff = FakeBff()
    odoo = FakeOdoo(bff.log)
    caplog.set_level(logging.INFO)
    report = run_company("SMD", FakeSource(fail=True), bff, odoo, _params(), NOW)
    assert report.status == "failed" and report.error == "ConnectionError"
    assert bff.sent == [] and odoo.sent == []
    assert "secret" not in caplog.text


def test_segment_selection_skips_unneeded_queries() -> None:
    source = FakeSource()
    run_company("SMD", source, None, None, _params(segments=("oos_viewed",)), NOW)
    kinds = [c[0] for c in source.calls]
    assert kinds == ["products"]  # tanpa jendela panjang, tanpa pencarian
