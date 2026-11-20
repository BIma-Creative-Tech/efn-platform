"""Payload BFF (§28.7 persis) dan ringkasan Odoo (tanpa data member): chunking, idempotensi, validasi negatif."""

import copy
import uuid
from datetime import date
from typing import Any

import pytest
from conftest import key

from efn_segment.contract import (
    MAX_SUBJECTS_PER_CHUNK,
    MEMBERS_KEYS,
    ContractError,
    chunk_subjects,
    idempotency_key,
    members_payloads,
    proposal_payload,
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
    payloads = members_payloads(RUN, "oos_viewed", [key(i) for i in range(7)], 3)
    assert [p["chunk_no"] for p in payloads] == [0, 1, 2]
    assert all(set(p) == MEMBERS_KEYS for p in payloads)
    assert payloads[0]["schema_version"] == 1 and payloads[0]["run_id"] == RUN
    assert total_members(payloads) == 7
    assert members_payloads(RUN, "oos_viewed", [key(i) for i in range(7)], 3) == payloads
    assert members_payloads(RUN, "oos_viewed", [], 3) == []
    assert idempotency_key(RUN, "oos_viewed", 2) == f"segment:{RUN}:oos_viewed:2"


def _members(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": RUN,
        "segment_code": "viewed_not_bought",
        "subject_keys": [key(1), key(2)],
        "chunk_no": 0,
    }
    payload.update(over)
    return payload


@pytest.mark.parametrize(
    "over",
    [
        {"schema_version": 2},
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


def test_validate_members_missing_key() -> None:
    payload = _members()
    del payload["chunk_no"]
    with pytest.raises(ContractError):
        validate_members(payload)


def _segment(code: str = "price_sensitive") -> Segment:
    return Segment(
        code=code,
        members=tuple(sorted(key(i) for i in range(25))),
        product_subjects={10: 20, 11: 3, 12: 15},
        reason_counts={"market": 12, "habit": 15} if code == "price_sensitive" else {},
    )


def test_proposal_payload_aggregate_only() -> None:
    payload = proposal_payload("SMD", RUN, _segment(), SegmentParams(), AS_OF, 10, 10)
    assert payload == {
        "schema_version": 1,
        "company_code": "SMD",
        "run_id": RUN,
        "segment_code": "price_sensitive",
        "member_count": 25,
        "metrics": {
            "as_of": "2026-11-19",
            "lookback_days": 14,
            "product_count": 3,
            "top_products": [{"product_id": 10, "subjects": 20}, {"product_id": 12, "subjects": 15}],
            "reason_counts": {"market": 12, "habit": 15},
        },
    }
    text = repr(payload)
    assert all(key(i) not in text for i in range(25))
    wl = proposal_payload("SMD", RUN, _segment("wishlist_idle"), SegmentParams(), AS_OF, 10, 10)
    assert wl["metrics"]["lookback_days"] == 60 and "reason_counts" not in wl["metrics"]


def _proposal() -> dict[str, Any]:
    return proposal_payload("SMD", RUN, _segment(), SegmentParams(), AS_OF, 10, 10)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(subject_keys=[key(1)]),  # kunci di luar allowlist
        lambda p: p["metrics"].update(note=key(1)),  # metrik di luar allowlist
        lambda p: p["metrics"].update(top_products=[{"product_id": 1, "subjects": 1, "who": key(1)}]),
        lambda p: p["metrics"]["reason_counts"].update(market=-1),
        lambda p: p["metrics"]["reason_counts"].update(vip=1),
        lambda p: p.update(member_count=0),
        lambda p: p.update(company_code="smd"),
        lambda p: p.update(segment_code="all"),
        lambda p: p.update(schema_version=2),
        lambda p: p["metrics"].update(as_of="kemarin"),
        lambda p: p["metrics"].pop("top_products"),
        lambda p: p["metrics"].update(top_products=[{"product_id": 0, "subjects": 1}]),
    ],
)
def test_validate_proposal_rejects(mutate: Any) -> None:
    payload = copy.deepcopy(_proposal())
    mutate(payload)
    with pytest.raises(ContractError):
        validate_proposal(payload)


def test_validate_proposal_rejects_hash_anywhere() -> None:
    payload = copy.deepcopy(_proposal())
    payload["metrics"]["as_of"] = key(1)
    with pytest.raises(ContractError):
        validate_proposal(payload)
