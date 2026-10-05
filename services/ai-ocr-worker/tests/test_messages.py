import json

import pytest

from efn_ocr.errors import PoisonMessageError
from efn_ocr.messages import (
    QUEUE_LOYALTY_EARN,
    QUEUE_NOTIFY_PUSH,
    FailureAction,
    action_for_failure,
    dlq_arguments,
    dlq_name,
    is_member_hash,
    loyalty_earn_message,
    parse_ocr_request,
    queue_arguments,
    result_messages,
    retry_count,
    short_hash,
)

REPORT_ID = "3f2c1b8e-6d1a-4c55-9a7e-1f0d2a9b8c7d"
USER_HASH = "8930b421545b77ea6ff5093510c261afe67f1b1e3e18c925cb76d59516926653"
PHONE = "6281234567890"


def _body(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "schema_version": 2,
        "report_id": REPORT_ID,
        "user_hash": USER_HASH,
        "image_bucket": "market-scout",
        "image_object": "reports/2026/10/x.jpg",
        "product_name": "Indomie Goreng",
        "reported_price": 3500,
        "competitor_name": None,
        "geo": None,
        "submitted_at": "2026-10-05T10:00:00Z",
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def test_queue_arguments_match_contract() -> None:
    assert queue_arguments("ocr.requested") == {
        "x-queue-type": "quorum",
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": "ocr.requested.dlq",
    }
    assert dlq_name("notify.push") == "notify.push.dlq"
    assert dlq_arguments() == {"x-queue-type": "quorum"}


def test_parse_valid_request_v2() -> None:
    request = parse_ocr_request(_body())
    assert request.report_id == REPORT_ID
    assert request.user_hash == USER_HASH
    assert request.image_object == "reports/2026/10/x.jpg"


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b"\xff\xfe",
        _body(schema_version=1),
        _body(report_id="not-a-uuid"),
        _body(report_id=None),
        _body(user_hash=""),
        _body(user_hash=None),
        _body(user_hash=USER_HASH.upper()),
        _body(user_hash=USER_HASH[:-1]),
        _body(user_hash=PHONE),
        # v2: nomor HP mentah tidak boleh ada di payload, walaupun user_hash valid.
        _body(user_id=PHONE),
        _body(member_ref=PHONE),
        b"{" + b" " * (70 * 1024) + b"}",
    ],
)
def test_parse_rejects_poison_messages(body: bytes) -> None:
    with pytest.raises(PoisonMessageError):
        parse_ocr_request(body)


def test_is_member_hash() -> None:
    assert is_member_hash(USER_HASH)
    assert not is_member_hash(PHONE)
    assert not is_member_hash(None)
    assert not is_member_hash("g" * 64)


def test_retry_count_header_variants() -> None:
    assert retry_count(None) == 0
    assert retry_count({}) == 0
    assert retry_count({"x-retry-count": 2}) == 2
    assert retry_count({"x-retry-count": "3"}) == 3
    assert retry_count({"x-retry-count": b"1"}) == 1
    assert retry_count({"x-retry-count": "abc"}) == 0
    assert retry_count({"x-retry-count": -4}) == 0


def test_failure_action() -> None:
    assert action_for_failure(0, 3) is FailureAction.RETRY
    assert action_for_failure(2, 3) is FailureAction.RETRY
    assert action_for_failure(3, 3) is FailureAction.DEAD_LETTER
    assert action_for_failure(0, 0) is FailureAction.DEAD_LETTER


def test_valid_result_publishes_loyalty_and_notify_v2() -> None:
    messages = result_messages(
        REPORT_ID, USER_HASH, "valid", "price_and_name_match", 10, product_name="Indomie Goreng"
    )
    assert [m.queue for m in messages] == [QUEUE_LOYALTY_EARN, QUEUE_NOTIFY_PUSH]
    earn = json.loads(messages[0].body)
    assert earn == {
        "schema_version": 2,
        "idempotency_key": f"price_hunter:{REPORT_ID}",
        "member_hash": USER_HASH,
        "points": 10,
        "reason": "price_hunter_valid",
        "source": "price_hunter",
        "source_ref": REPORT_ID,
    }
    assert messages[0].message_id == f"price_hunter:{REPORT_ID}"
    notify = json.loads(messages[1].body)
    assert notify == {
        "schema_version": 2,
        "idempotency_key": f"price_hunter_notify:{REPORT_ID}",
        "member_hash": USER_HASH,
        "source": "price_hunter",
        "source_ref": REPORT_ID,
        "template": "price_hunter_valid",
        # Kunci yang dibaca template BFF (src/notification/templates.ts): product_name, points.
        "params": {"report_id": REPORT_ID, "product_name": "Indomie Goreng", "points": 10},
    }
    assert messages[1].message_id == notify["idempotency_key"]


def test_rejected_result_only_notifies() -> None:
    messages = result_messages(REPORT_ID, USER_HASH, "rejected", "price_mismatch", 0, product_name="Gula 1kg")
    assert [m.queue for m in messages] == [QUEUE_NOTIFY_PUSH]
    payload = json.loads(messages[0].body)
    assert payload["template"] == "price_hunter_rejected"
    assert payload["member_hash"] == USER_HASH
    assert (payload["source"], payload["source_ref"]) == ("price_hunter", REPORT_ID)
    assert payload["params"] == {"report_id": REPORT_ID, "product_name": "Gula 1kg", "reason": "price_mismatch"}


def test_flagged_result_publishes_nothing() -> None:
    assert result_messages(REPORT_ID, USER_HASH, "flagged", "vlm_unavailable", 0) == []


def test_no_phone_number_in_any_outgoing_payload() -> None:
    for status in ("valid", "rejected"):
        for message in result_messages(REPORT_ID, USER_HASH, status, "x", 10, product_name="Gula"):
            body = json.loads(message.body)
            assert "user_id" not in body and "member_ref" not in body
            assert PHONE not in message.body.decode()


def test_outgoing_refuses_non_hash_identity() -> None:
    with pytest.raises(ValueError):
        loyalty_earn_message(REPORT_ID, PHONE, 10)
    with pytest.raises(ValueError):
        result_messages(REPORT_ID, PHONE, "rejected", "price_mismatch", 0)


def test_short_hash() -> None:
    assert short_hash(USER_HASH) == "8930b421..."
    assert short_hash(None) == "-"
