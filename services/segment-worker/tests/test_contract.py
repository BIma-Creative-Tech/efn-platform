"""Payload BFF (§28.7 persis) dan ringkasan Odoo (tanpa data member): chunking, idempotensi, validasi negatif."""

import copy
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from conftest import key

from efn_segment.contract import (
    MAX_SUBJECTS_PER_CHUNK,
    MEMBERS_FIELDS,
    MEMBERS_KEYS,
    PII_RE,
    PROPOSAL_KEYS,
    ContractError,
    chunk_subjects,
    idempotency_key,
    members_payloads,
    proposal_payload,
    top_summary,
    total_members,
    validate_members,
    validate_proposal,
)
from efn_segment.segments import Segment, SegmentParams

RUN = str(uuid.uuid4())
AS_OF = date(2026, 11, 19)


def test_chunking_max_5000_deterministic_and_dedup() -> None:
    keys = [key(i) for i in range(12_001)]
    shuffled = list(reversed(keys)) + keys[:10]  # urutan acak + duplikat
    chunks = chunk_subjects(shuffled)
    assert [len(c) for c in chunks] == [5000, 5000, 2001]
    assert chunks == chunk_subjects(keys)  # retry -> chunk identik
    flat = [k for c in chunks for k in c]
    assert flat == sorted(set(keys))


def test_chunk_size_bounds_and_invalid_keys() -> None:
    with pytest.raises(ContractError):
        chunk_subjects([key(1)], 5001)
    with pytest.raises(ContractError):
        chunk_subjects([key(1)], 0)
    for bad in ("62812345678", "A" * 64, "g" * 64, ""):
        with pytest.raises(ContractError):
            chunk_subjects([key(1), bad])


def test_members_payloads_exact_contract_shape() -> None:
    payloads = members_payloads("SMD", RUN, "oos_viewed", [key(i) for i in range(7)], 3)
    assert [p["chunk_no"] for p in payloads] == [0, 1, 2]
    assert all(set(p) == MEMBERS_KEYS for p in payloads)
    assert all(list(p) == list(MEMBERS_FIELDS) for p in payloads)  # urutan = BFF
    assert payloads[0]["schema_version"] == 1 and payloads[0]["run_id"] == RUN
    assert {p["company_code"] for p in payloads} == {"SMD"}
    assert total_members(payloads) == 7
    assert members_payloads("SMD", RUN, "oos_viewed", [key(i) for i in range(7)], 3) == payloads
    assert members_payloads("SMD", RUN, "oos_viewed", [], 3) == []
    with pytest.raises(ContractError):
        members_payloads("XYZ", RUN, "oos_viewed", [key(1)], 3)
    assert idempotency_key(RUN, "oos_viewed", 2) == f"segment:{RUN}:oos_viewed:2"


def _members(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "company_code": "SMD",
        "run_id": RUN,
        "segment_code": "viewed_not_bought",
        "subject_keys": [key(1), key(2)],
        "chunk_no": 0,
    }
    payload.update(over)
    return payload


def test_validate_members_accepts_valid() -> None:
    validate_members(_members())


@pytest.mark.parametrize(
    "over",
    [
        {"schema_version": 2},
        {"company_code": "XYZ"},
        {"company_code": "smd"},
        {"company_code": ["SMD"]},
        {"company_code": None},
        {"chunk_no": 100_001},
        {"run_id": "not-uuid"},
        {"run_id": str(uuid.uuid1())},  # bukan v4
        {"run_id": RUN.upper()},
        {"segment_code": "vip"},
        {"chunk_no": -1},
        {"chunk_no": True},
        {"subject_keys": []},
        {"subject_keys": [key(1)] * 2},
        {"subject_keys": [key(i) for i in range(MAX_SUBJECTS_PER_CHUNK + 1)]},
        {"subject_keys": ["62812****789"]},
        {"extra": 1},
    ],
)
def test_validate_members_rejects(over: dict[str, Any]) -> None:
    with pytest.raises(ContractError):
        validate_members(_members(**over))


@pytest.mark.parametrize("missing", ["chunk_no", "company_code"])
def test_validate_members_missing_key(missing: str) -> None:
    payload = _members()
    del payload[missing]
    with pytest.raises(ContractError):
        validate_members(payload)


def _segment(code: str = "price_sensitive") -> Segment:
    return Segment(
        code=code,
        members=tuple(sorted(key(i) for i in range(25))),
        product_subjects={10: 20, 11: 3, 12: 15},
        reason_counts={"market": 12, "habit": 15} if code == "price_sensitive" else {},
    )


GEN = datetime(2026, 11, 20, 2, 15, 30, 123456, tzinfo=UTC)


def test_proposal_payload_flat_numeric_metrics() -> None:
    payload = proposal_payload("SMD", RUN, _segment(), SegmentParams(), AS_OF, 10, 10, GEN)
    assert payload == {
        "schema_version": 1,
        "company_code": "SMD",
        "run_id": RUN,
        "segment_code": "price_sensitive",
        "member_count": 25,
        "metrics": {
            "lookback_days": 14,
            "product_count": 3,
            "as_of_yyyymmdd": 20261119,
            "k_min": 10,
            "top_product_count": 2,
            "top_product_subjects": 35,
            "reason_habit": 15,
            "reason_market": 12,
        },
        "summary": "2 produk teratas (>= 10 subjek): id 10 (20), id 12 (15)",
        "generated_at": "2026-11-20T02:15:30Z",
    }
    assert set(payload) == PROPOSAL_KEYS
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in payload["metrics"].values())
    text = repr(payload)
    assert all(key(i) not in text for i in range(25))
    wl = proposal_payload("SMD", RUN, _segment("wishlist_idle"), SegmentParams(), AS_OF, 10, 10, GEN)
    assert wl["metrics"]["lookback_days"] == 60
    assert not any(k.startswith("reason_") for k in wl["metrics"])


def test_generated_at_defaults_to_now_utc() -> None:
    payload = proposal_payload("SMD", RUN, _segment(), SegmentParams(), AS_OF, 10, 10)
    assert payload["generated_at"].endswith("Z") and len(payload["generated_at"]) == 20


def test_top_summary_empty_long_ids_and_length() -> None:
    assert top_summary([], 10) == ""
    # Id >= 6 digit tidak ditulis (pola PII Odoo \d{6,}).
    text = top_summary([{"product_id": 1234567, "subjects": 50}, {"product_id": 42, "subjects": 11}], 10)
    assert text == "2 produk teratas (>= 10 subjek): id 42 (11) (+1 lainnya)"
    assert top_summary([{"product_id": 1234567, "subjects": 50}], 10) == "1 produk teratas (>= 10 subjek) (+1 lainnya)"
    many = [{"product_id": 10_000 + i, "subjects": 99_999} for i in range(50)]
    text = top_summary(many, 10)
    assert len(text) <= 256 and "lainnya" in text
    assert not PII_RE.search(text)


def _proposal() -> dict[str, Any]:
    return proposal_payload("SMD", RUN, _segment(), SegmentParams(), AS_OF, 10, 10, GEN)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(subject_keys=[key(1)]),  # kunci di luar allowlist
        lambda p: p["metrics"].update(note=key(1)),  # metrik bukan angka
        lambda p: p["metrics"].update(top_products=[{"product_id": 1, "subjects": 1}]),  # list
        lambda p: p["metrics"].update(reasons={"market": 1}),  # dict
        lambda p: p["metrics"].update(as_of="2026-11-19"),  # string
        lambda p: p["metrics"].update(flag=True),  # bool bukan angka
        lambda p: p["metrics"].update(huge=10**12),
        lambda p: p["metrics"].update(Bad_Key=1),
        lambda p: p["metrics"].update({f"m{i}": i for i in range(20)}),  # > 20 kunci
        lambda p: p["metrics"].pop("product_count"),
        lambda p: p.update(member_count=0),
        lambda p: p.update(company_code="smd"),
        lambda p: p.update(segment_code="all"),
        lambda p: p.update(schema_version=2),
        lambda p: p.update(summary="x" * 257),
        lambda p: p.update(summary="hubungi 081234567890"),
        lambda p: p.update(summary="kirim ke budi@mail.com"),
        lambda p: p.update(generated_at="2026-11-20 02:15:30"),
        lambda p: p.update(generated_at="2026-11-20T09:15:30+07:00"),
        lambda p: p.pop("generated_at"),
    ],
)
def test_validate_proposal_rejects(mutate: Any) -> None:
    payload = copy.deepcopy(_proposal())
    mutate(payload)
    with pytest.raises(ContractError):
        validate_proposal(payload)


def test_validate_proposal_rejects_hash_anywhere() -> None:
    payload = copy.deepcopy(_proposal())
    payload["summary"] = key(1)
    with pytest.raises(ContractError):
        validate_proposal(payload)
