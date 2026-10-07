import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

import pytest

from efn_ocr.config import Settings
from efn_ocr.delivery import Settle, handle_delivery
from efn_ocr.errors import ImageNotFoundError, VlmUnavailableError
from efn_ocr.messages import QUEUE_LOYALTY_EARN, QUEUE_NOTIFY_PUSH, OcrRequest, OutgoingMessage
from efn_ocr.ocr_engines import OcrResult
from efn_ocr.processor import ReportProcessor
from efn_ocr.repository import ReportRow
from efn_ocr.vlm import VlmResult

REPORT_ID = "3f2c1b8e-6d1a-4c55-9a7e-1f0d2a9b8c7d"
USER_HASH = "8930b421545b77ea6ff5093510c261afe67f1b1e3e18c925cb76d59516926653"
REQUEST = OcrRequest(report_id=REPORT_ID, user_hash=USER_HASH, image_bucket="market-scout", image_object=None)


class FakeRepo:
    def __init__(self, status: str = "queued", duplicate: str | None = None, valid_today: int = 0) -> None:
        self.status = status
        self.duplicate = duplicate
        self.valid_today = valid_today
        self.finalized: dict[str, Any] | None = None
        self.released = False
        self.fail_on_commit = False
        self.counted_for: str | None = None

    def claim(self, report_id: str, allow_reclaim: bool, stale_seconds: int) -> ReportRow | None:
        if self.status != "queued" and not (self.status == "processing" and allow_reclaim):
            return None
        self.status = "processing"
        return ReportRow(
            report_id=report_id,
            user_hash=USER_HASH,
            product_name="Indomie Goreng",
            reported_price=3500,
            competitor_name="Toko A",
            geo_lat=None,
            geo_lng=None,
            image_object="reports/2026/10/x.jpg",
        )

    def current_status(self, report_id: str) -> str | None:
        return self.status

    def find_duplicate(self, report_id: str, phash: str, max_distance: int, lookback_days: int) -> str | None:
        return self.duplicate

    def count_valid_today(self, user_hash: str, report_id: str) -> int:
        self.counted_for = user_hash
        return self.valid_today

    def finalize(
        self,
        report_id: str,
        status: str,
        reason: str,
        extracted: dict[str, Any],
        confidence: float | None,
        phash: str | None,
        before_commit: Callable[[], None],
    ) -> bool:
        if self.status != "processing":
            return False
        before_commit()
        if self.fail_on_commit:
            raise ConnectionError("commit gagal")
        self.status = status
        self.finalized = {"status": status, "reason": reason, "extracted": extracted, "confidence": confidence}
        return True

    def release(self, report_id: str) -> None:
        self.released = True
        self.status = "queued"


class FakeImages:
    def __init__(self, missing: bool = False) -> None:
        self.missing = missing

    def fetch(self, bucket: str, key: str) -> bytes:
        if self.missing:
            raise ImageNotFoundError(key)
        return b"jpeg-bytes"


class FakeOcr:
    name = "tesseract"

    def __init__(self, text: str, confidence: float) -> None:
        self.result = OcrResult(text=text, confidence=confidence, engine=self.name)

    def extract(self, image: Any) -> OcrResult:
        return self.result


class FakeVlm:
    name = "ollama:test"

    def __init__(self, result: VlmResult | None = None) -> None:
        self.result = result
        self.calls = 0

    def extract(self, image_bytes: bytes) -> VlmResult:
        self.calls += 1
        if self.result is None:
            raise VlmUnavailableError("ConnectionError")
        return self.result


class FakePublisher:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[OutgoingMessage] = []
        self.fail = fail

    def publish(self, messages: Sequence[OutgoingMessage]) -> None:
        if self.fail:
            raise ConnectionError("broker down")
        self.sent.extend(messages)


def _processor(
    repo: FakeRepo,
    ocr: FakeOcr,
    vlm: FakeVlm | None = None,
    publisher: FakePublisher | None = None,
    images: FakeImages | None = None,
) -> tuple[ReportProcessor, FakePublisher]:
    pub = publisher or FakePublisher()
    settings = replace(Settings(), points_per_valid_report=10)
    processor = ReportProcessor(
        settings,
        repo,
        images or FakeImages(),
        ocr,
        vlm,
        pub,
        image_loader=lambda data: object(),
        phasher=lambda image: "0123456789abcdef",
        vlm_encoder=lambda image: b"vlm-jpeg",
    )
    return processor, pub


def test_high_confidence_valid_publishes_reward_and_notification() -> None:
    repo = FakeRepo()
    vlm = FakeVlm()
    processor, pub = _processor(repo, FakeOcr("INDOMIE GORENG 85g Rp 3.500", 0.92), vlm)
    result = processor.process(REQUEST, allow_reclaim=False)
    assert (result.outcome, result.status) == ("processed", "valid")
    assert vlm.calls == 0
    assert [m.queue for m in pub.sent] == [QUEUE_LOYALTY_EARN, QUEUE_NOTIFY_PUSH]
    earn = json.loads(pub.sent[0].body)
    assert earn["points"] == 10
    assert earn["member_hash"] == USER_HASH
    assert repo.counted_for == USER_HASH  # cap harian memakai user_hash
    assert repo.finalized is not None and repo.finalized["extracted"]["price"] == 3500


def test_daily_cap_rejects_with_user_hash() -> None:
    repo = FakeRepo(valid_today=20)
    processor, pub = _processor(repo, FakeOcr("INDOMIE GORENG Rp 3.500", 0.95))
    result = processor.process(REQUEST, allow_reclaim=False)
    assert result.status == "rejected"
    assert repo.counted_for == USER_HASH
    assert all("loyalty" not in m.queue for m in pub.sent)


def test_low_confidence_uses_vlm() -> None:
    repo = FakeRepo()
    vlm = FakeVlm(VlmResult(product_name="Indomie Goreng", price=3500, raw={}))
    processor, _ = _processor(repo, FakeOcr("1NDOM1E", 0.41), vlm)
    result = processor.process(REQUEST, allow_reclaim=False)
    assert (result.status, result.vlm) == ("valid", "ok")
    assert vlm.calls == 1


def test_vlm_down_flags_report_without_crash() -> None:
    repo = FakeRepo()
    processor, pub = _processor(repo, FakeOcr("blurry", 0.30), FakeVlm(None))
    result = processor.process(REQUEST, allow_reclaim=False)
    assert (result.status, result.reason, result.vlm) == ("flagged", "vlm_unavailable", "unavailable")
    assert pub.sent == []


def test_vlm_disabled_flags_report() -> None:
    processor, _ = _processor(FakeRepo(), FakeOcr("blurry", 0.30), None)
    result = processor.process(REQUEST, allow_reclaim=False)
    assert (result.status, result.reason) == ("flagged", "vlm_disabled")


def test_duplicate_image_flagged_before_ocr() -> None:
    repo = FakeRepo(duplicate="other-report")
    processor, pub = _processor(repo, FakeOcr("INDOMIE GORENG Rp 3.500", 0.95))
    result = processor.process(REQUEST, allow_reclaim=False)
    assert (result.status, result.reason) == ("flagged", "duplicate_image")
    assert pub.sent == []


def test_missing_image_rejected() -> None:
    processor, pub = _processor(FakeRepo(), FakeOcr("x", 0.9), images=FakeImages(missing=True))
    result = processor.process(REQUEST, allow_reclaim=False)
    assert (result.status, result.reason) == ("rejected", "image_missing")
    assert [m.queue for m in pub.sent] == [QUEUE_NOTIFY_PUSH]


def test_already_processed_is_skipped_idempotently() -> None:
    repo = FakeRepo(status="valid")
    processor, pub = _processor(repo, FakeOcr("INDOMIE GORENG Rp 3.500", 0.95))
    result = processor.process(REQUEST, allow_reclaim=True)
    assert (result.outcome, result.status) == ("skipped", "valid")
    assert pub.sent == []


def _delivery_body() -> bytes:
    return json.dumps({"schema_version": 2, "report_id": REPORT_ID, "user_hash": USER_HASH}).encode()


def test_delivery_publish_failure_triggers_retry_with_header() -> None:
    repo = FakeRepo()
    failing = FakePublisher(fail=True)
    processor, _ = _processor(repo, FakeOcr("INDOMIE GORENG Rp 3.500", 0.95), publisher=failing)
    retry_pub = FakePublisher()
    outcome = handle_delivery(processor, retry_pub, _delivery_body(), None, "mid-1", False, 3, sleep=lambda s: None)
    assert outcome is Settle.ACK
    assert repo.released is True
    assert len(retry_pub.sent) == 1
    assert retry_pub.sent[0].headers["x-retry-count"] == 1
    assert retry_pub.sent[0].message_id == "mid-1"


def test_delivery_dead_letters_after_max_retries() -> None:
    repo = FakeRepo()
    repo.fail_on_commit = True
    processor, _ = _processor(repo, FakeOcr("INDOMIE GORENG Rp 3.500", 0.95))
    headers = {"x-retry-count": 3}
    publisher = FakePublisher()
    outcome = handle_delivery(processor, publisher, _delivery_body(), headers, None, True, 3, sleep=lambda s: None)
    assert outcome is Settle.DEAD_LETTER


def test_delivery_requeues_when_retry_publish_fails() -> None:
    repo = FakeRepo()
    repo.fail_on_commit = True
    processor, _ = _processor(repo, FakeOcr("INDOMIE GORENG Rp 3.500", 0.95))
    outcome = handle_delivery(
        processor,
        FakePublisher(fail=True),
        _delivery_body(),
        None,
        None,
        False,
        3,
        sleep=lambda s: None,
    )
    assert outcome is Settle.REQUEUE


@pytest.mark.parametrize("body", [b"garbage", b'{"schema_version": 2}', b'{"schema_version": 1}'])
def test_delivery_poison_goes_to_dlq(body: bytes) -> None:
    processor, _ = _processor(FakeRepo(), FakeOcr("x", 0.9))
    outcome = handle_delivery(processor, FakePublisher(), body, None, None, False, 3, sleep=lambda s: None)
    assert outcome is Settle.DEAD_LETTER
