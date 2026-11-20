"""Klien BFF & Odoo: path, header, retry hanya transport, duplikat = sukses, galat aman, tanpa secret di repr/log."""

import json
import logging
import uuid
from collections.abc import Mapping
from typing import Any

import pytest
from conftest import key

from efn_segment.bff import BffSegmentClient
from efn_segment.contract import ContractError
from efn_segment.odoo import OdooCampaignClient
from efn_segment.transport import HttpError, RetryPolicy

RUN = str(uuid.uuid4())
API_KEY = "test-api-key-not-real"


class FakeTransport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, path: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
        self.requests.append((path, body, dict(headers)))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _policy() -> RetryPolicy:
    return RetryPolicy(timeout_seconds=5, max_retries=3, sleep=lambda _s: None)


def _members() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "company_code": "SMD",
        "run_id": RUN,
        "segment_code": "wishlist_idle",
        "subject_keys": [key(1), key(2)],
        "chunk_no": 4,
    }


def _proposal() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "company_code": "SMD",
        "run_id": RUN,
        "segment_code": "wishlist_idle",
        "member_count": 12,
        "metrics": {"lookback_days": 60, "product_count": 3, "as_of_yyyymmdd": 20261119},
        "summary": "",
        "generated_at": "2026-11-20T02:15:00Z",
    }


# ---------------------------------------------------------------- BFF
def test_bff_path_body_headers_and_retry() -> None:
    transport = FakeTransport([(503, b""), TimeoutError("t"), (200, b'{"accepted": 2}')])
    result = BffSegmentClient(transport, _policy()).send_members(_members())
    assert result == {"accepted": 2}
    assert len(transport.requests) == 3
    path, body, headers = transport.requests[0]
    assert path == f"/internal/v1/segments/{RUN}/members"
    assert json.loads(body) == _members()
    assert headers["Idempotency-Key"] == f"segment:{RUN}:wishlist_idle:4"
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers  # autentikasi = mTLS


@pytest.mark.parametrize("response", [(409, b'{"error":"duplicate_chunk"}'), (200, b'{"duplicate": true}'), (204, b"")])
def test_bff_duplicate_chunk_is_success(response: tuple[int, bytes]) -> None:
    transport = FakeTransport([response])
    BffSegmentClient(transport, _policy()).send_members(_members())
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        ((422, b'{"error":"invalid_payload"}'), "rejected"),
        ((409, b'{"error":"run_closed"}'), "rejected"),
        ((403, b""), "auth"),
        ((404, b""), "not_found"),
    ],
)
def test_bff_client_errors_not_retried(response: tuple[int, bytes], kind: str) -> None:
    transport = FakeTransport([response])
    with pytest.raises(HttpError) as err:
        BffSegmentClient(transport, _policy()).send_members(_members())
    assert err.value.kind == kind
    assert len(transport.requests) == 1


def test_bff_gives_up_after_max_retries() -> None:
    transport = FakeTransport([(503, b"")] * 4)
    with pytest.raises(HttpError) as err:
        BffSegmentClient(transport, _policy()).send_members(_members())
    assert err.value.kind == "transport" and len(transport.requests) == 4


def test_bff_validates_before_sending() -> None:
    transport = FakeTransport([])
    bad = {**_members(), "run_id": "../../admin"}
    with pytest.raises(ContractError):
        BffSegmentClient(transport, _policy()).send_members(bad)
    assert transport.requests == []


# ---------------------------------------------------------------- Odoo
def test_odoo_path_auth_and_ok() -> None:
    transport = FakeTransport([(200, b'{"ok": true, "proposal_id": 7, "created": true}')])
    client = OdooCampaignClient(transport, API_KEY, "efn_odoo", _policy())
    assert client.proposal_upsert(_proposal())["proposal_id"] == 7
    path, body, headers = transport.requests[0]
    assert path == "/json/2/efn.campaign/efn_campaign_proposal_upsert"
    assert headers["Authorization"] == f"bearer {API_KEY}"
    assert headers["X-Odoo-Database"] == "efn_odoo"
    assert json.loads(body) == _proposal()


@pytest.mark.parametrize("content", [b"true", b"null", b""])
def test_odoo_scalar_success(content: bytes) -> None:
    client = OdooCampaignClient(FakeTransport([(200, content)]), API_KEY, policy=_policy())
    assert client.proposal_upsert(_proposal()) == {}


@pytest.mark.parametrize("content", [b"false", b'{"ok": false}', b"[1]", b"<html>"])
def test_odoo_invalid_response(content: bytes) -> None:
    with pytest.raises(HttpError) as err:
        OdooCampaignClient(FakeTransport([(200, content)]), API_KEY, policy=_policy()).proposal_upsert(_proposal())
    assert err.value.kind == "invalid_response"


MISMATCH = b'{"name":"odoo.exceptions.AccessError","message":"efn_campaign:company_mismatch"}'


@pytest.mark.parametrize(
    ("response", "kind", "code"),
    [
        ((403, MISMATCH), "company_mismatch", "company_mismatch"),
        ((403, b'{"message":"efn_campaign:access_denied"}'), "auth", "access_denied"),
        ((422, b'{"message":"efn_campaign:invalid_segment_code"}'), "rejected", "invalid_segment_code"),
        ((422, b'{"message":"Traceback (most recent call last): ..."}'), "rejected", ""),
    ],
)
def test_odoo_errors_safe_codes(response: tuple[int, bytes], kind: str, code: str) -> None:
    transport = FakeTransport([response])
    with pytest.raises(HttpError) as err:
        OdooCampaignClient(transport, API_KEY, policy=_policy()).proposal_upsert(_proposal())
    assert (err.value.kind, err.value.code) == (kind, code)
    assert len(transport.requests) == 1


def test_odoo_rejects_member_data_before_sending() -> None:
    transport = FakeTransport([])
    bad = _proposal()
    bad["summary"] = f"anggota {key(9)}"
    with pytest.raises(ContractError):
        OdooCampaignClient(transport, API_KEY, policy=_policy()).proposal_upsert(bad)
    bad = _proposal()
    bad["metrics"]["top_products"] = [{"product_id": 1, "subjects": 1}]  # bukan angka -> 422 di Odoo
    with pytest.raises(ContractError):
        OdooCampaignClient(transport, API_KEY, policy=_policy()).proposal_upsert(bad)
    assert transport.requests == []


def test_no_secret_in_repr_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = OdooCampaignClient(FakeTransport([(503, b"")] * 4), API_KEY, policy=_policy())
    assert API_KEY not in repr(client)
    with pytest.raises(HttpError):
        client.proposal_upsert(_proposal())
    assert API_KEY not in caplog.text
