"""Fixture bersama `tests/fixtures/segment_members_v1.json` (worker pytest <-> BFF Jest, SEC-G50-16).

Memastikan: (1) body yang dibentuk worker SAMA PERSIS (isi + urutan field) dengan contoh valid; (2) contoh invalid
ditolak validator lokal worker dengan kode yang tercatat; (3) respons BFF di fixture ditangani klien sesuai harapan;
(4) urutan field & daftar PT sama dengan source BFF (`segment-ingest.service.ts`, `loyalty.service.ts`).
"""

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from efn_segment.bff import BffSegmentClient
from efn_segment.contract import (
    COMPANY_CODES,
    MAX_CHUNK_NO,
    MAX_SUBJECTS_PER_CHUNK,
    MEMBERS_FIELDS,
    ContractError,
    members_payloads,
    validate_members,
)
from efn_segment.transport import HttpError, RetryPolicy

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "segment_members_v1.json").read_text(encoding="utf-8"))
ROOT = Path(__file__).resolve().parents[3]
BFF_INGEST = ROOT / "services/api-gateway/src/campaign/segment-ingest.service.ts"
BFF_LOYALTY = ROOT / "services/api-gateway/src/loyalty/loyalty.service.ts"
VALID = {case["name"]: case for case in FIXTURE["valid"]}


class FakeTransport:
    def __init__(self, status: int, body: Mapping[str, Any]) -> None:
        self.response = (status, json.dumps(body).encode("utf-8"))
        self.requests: list[tuple[str, bytes]] = []

    def post(self, path: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.requests.append((path, body))
        return self.response


def _client(transport: FakeTransport) -> BffSegmentClient:
    return BffSegmentClient(transport, RetryPolicy(timeout_seconds=5, max_retries=3, sleep=lambda _s: None))


def _error_body(code: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": "x"}}


def test_field_order_and_limits_match_worker() -> None:
    assert tuple(FIXTURE["field_order"]) == MEMBERS_FIELDS
    assert FIXTURE["limits"] == {"max_subject_keys_per_chunk": MAX_SUBJECTS_PER_CHUNK, "max_chunk_no": MAX_CHUNK_NO}


@pytest.mark.parametrize("name", list(VALID))
def test_worker_builds_exact_fixture_body(name: str) -> None:
    case = VALID[name]
    src = case["generated_from"]
    payloads = members_payloads(
        src["company_code"], src["run_id"], src["segment_code"], src["subject_keys"], src["chunk_size"]
    )
    built = payloads[src["chunk_no"]]
    assert built == case["body"]
    assert list(built) == FIXTURE["field_order"]  # urutan JSON yang dikirim
    if name == "last_chunk":
        assert src["chunk_no"] == len(payloads) - 1
    validate_members(case["body"])
    assert case["path_run_id"] == case["body"]["run_id"]


@pytest.mark.parametrize("name", list(VALID))
def test_client_sends_fixture_body_and_accepts_200(name: str) -> None:
    case = VALID[name]
    transport = FakeTransport(case["expect"]["status"], case["expect"]["body"])
    result = _client(transport).send_members(case["body"])
    assert result == case["expect"]["body"]
    path, body = transport.requests[0]
    assert path == f"/internal/v1/segments/{case['path_run_id']}/members"
    assert json.loads(body) == case["body"]
    assert list(json.loads(body)) == FIXTURE["field_order"]


def test_duplicate_is_success_without_retry() -> None:
    dup = FIXTURE["duplicate"]
    transport = FakeTransport(dup["expect"]["status"], dup["expect"]["body"])
    result = _client(transport).send_members(VALID[dup["body_ref"]]["body"])
    assert result["duplicate_chunk"] is True
    assert len(transport.requests) == 1


@pytest.mark.parametrize("case", FIXTURE["invalid"], ids=lambda c: c["name"])
def test_invalid_bodies_rejected_locally_with_expected_code(case: dict[str, Any]) -> None:
    # expect.error_code = kode galat BFF (kanonik, dicek ada di source BFF di test_fixture_matches_bff_source).
    assert case["expect"]["status"] == 400 and case["expect"]["error_code"]
    if case["worker_error"] is None:
        # Path dibentuk dari body.run_id: kasus ini tidak bisa dihasilkan worker; body sendiri valid.
        validate_members(case["body"])
        assert case["path_run_id"] != case["body"]["run_id"]
        return
    with pytest.raises(ContractError) as err:
        validate_members(case["body"])
    assert err.value.code == case["worker_error"]
    transport = FakeTransport(400, _error_body("invalid_request"))
    with pytest.raises(ContractError):
        _client(transport).send_members(case["body"])
    assert transport.requests == []  # tidak pernah dikirim


@pytest.mark.parametrize("case", FIXTURE["conflict"], ids=lambda c: c["name"])
def test_conflicts_are_rejected_without_retry(case: dict[str, Any]) -> None:
    transport = FakeTransport(case["expect"]["status"], _error_body(case["expect"]["error_code"]))
    with pytest.raises(HttpError) as err:
        _client(transport).send_members(VALID[case["body_ref"]]["body"])
    assert (err.value.kind, err.value.code, err.value.status) == ("rejected", case["expect"]["error_code"], 409)
    assert len(transport.requests) == 1


def test_bff_400_invalid_request_not_retried() -> None:
    transport = FakeTransport(400, _error_body("invalid_request"))
    with pytest.raises(HttpError) as err:
        _client(transport).send_members(VALID["single_chunk"]["body"])
    assert (err.value.kind, err.value.code) == ("rejected", "invalid_request")
    assert len(transport.requests) == 1


def test_fixture_matches_bff_source() -> None:
    ingest = BFF_INGEST.read_text(encoding="utf-8")
    keys = re.search(r"const KEYS = new Set\(\[(.*?)\]\)", ingest, re.S)
    assert keys, "KEYS tidak ditemukan di segment-ingest.service.ts"
    assert re.findall(r"'([a-z_]+)'", keys.group(1)) == list(FIXTURE["field_order"])
    limit = re.search(r"MAX_SUBJECTS_PER_CHUNK = ([0-9_]+)", ingest)
    assert limit and int(limit.group(1).replace("_", "")) == MAX_SUBJECTS_PER_CHUNK
    fixture_codes = {c["expect"]["error_code"] for c in FIXTURE["invalid"] + FIXTURE["conflict"]}
    for code in sorted(fixture_codes):
        assert f"'{code}'" in ingest, f"kode galat fixture {code!r} tidak ada di segment-ingest.service.ts"
    loyalty = BFF_LOYALTY.read_text(encoding="utf-8")
    codes = re.search(r"export const COMPANY_CODES = \[(.*?)\]", loyalty, re.S)
    assert codes, "COMPANY_CODES tidak ditemukan di loyalty.service.ts"
    assert set(re.findall(r"'([A-Z]+)'", codes.group(1))) == COMPANY_CODES
