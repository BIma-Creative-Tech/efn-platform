"""Penanganan satu delivery `ocr.requested` -> keputusan ack/nack. Tanpa pika agar bisa diuji."""

import logging
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Protocol

from efn_ocr import metrics
from efn_ocr.errors import PoisonMessageError
from efn_ocr.messages import (
    QUEUE_OCR_REQUESTED,
    RETRY_HEADER,
    FailureAction,
    OcrRequest,
    OutgoingMessage,
    action_for_failure,
    parse_ocr_request,
    retry_count,
)
from efn_ocr.processor import ProcessResult, Publisher

log = logging.getLogger(__name__)


class Settle(StrEnum):
    ACK = "ack"
    REQUEUE = "requeue"  # nack requeue=True (broker bermasalah saat republish retry)
    DEAD_LETTER = "dead_letter"  # nack requeue=False -> ocr.requested.dlq


class Processor(Protocol):
    def process(self, request: OcrRequest, allow_reclaim: bool) -> ProcessResult: ...

    def release(self, report_id: str) -> None: ...

    def fail(self, report_id: str) -> None: ...


def handle_delivery(
    processor: Processor,
    publisher: Publisher,
    body: bytes,
    headers: dict[str, Any] | None,
    message_id: str | None,
    redelivered: bool,
    max_retries: int,
    sleep: Callable[[float], None] = time.sleep,
) -> Settle:
    try:
        request = parse_ocr_request(body)
    except PoisonMessageError as exc:
        log.error("pesan ocr.requested tidak valid (%s) -> DLQ", exc)
        metrics.DELIVERIES.labels(action="dead_letter").inc()
        return Settle.DEAD_LETTER

    retries = retry_count(headers)
    try:
        result = processor.process(request, allow_reclaim=redelivered or retries > 0)
    except Exception:
        log.exception("report=%s gagal diproses (retry ke-%d)", request.report_id, retries)
        return _on_failure(processor, publisher, request, body, headers, message_id, retries, max_retries, sleep)

    if result.outcome == "processed" and result.status:
        metrics.REPORTS.labels(status=result.status).inc()
        metrics.PROCESSED.inc()
        metrics.LATENCY.observe(result.duration_seconds)
        metrics.VLM_CALLS.labels(outcome=result.vlm).inc()
        metrics.DELIVERIES.labels(action="ack").inc()
    else:
        metrics.DELIVERIES.labels(action="skipped").inc()
    return Settle.ACK


def backoff_seconds(retries_so_far: int) -> float:
    """Backoff eksponensial sebelum republish retry: 2, 4, 8, ... maks 30 detik."""
    return float(min(2 ** (retries_so_far + 1), 30))


def _on_failure(
    processor: Processor,
    publisher: Publisher,
    request: OcrRequest,
    body: bytes,
    headers: dict[str, Any] | None,
    message_id: str | None,
    retries: int,
    max_retries: int,
    sleep: Callable[[float], None],
) -> Settle:
    if action_for_failure(retries, max_retries) is FailureAction.DEAD_LETTER:
        try:
            processor.fail(request.report_id)
        except Exception:
            log.exception("report=%s gagal ditandai processing_failed", request.report_id)
        metrics.DELIVERIES.labels(action="dead_letter").inc()
        return Settle.DEAD_LETTER

    try:
        processor.release(request.report_id)
    except Exception:
        log.exception("report=%s gagal dikembalikan ke queued", request.report_id)
    sleep(backoff_seconds(retries))  # di thread worker, tidak memblokir heartbeat koneksi consumer
    retry_headers = dict(headers or {})
    retry_headers[RETRY_HEADER] = retries + 1
    retry = OutgoingMessage(
        queue=QUEUE_OCR_REQUESTED,
        body=body,
        message_id=message_id or f"ocr.requested:{request.report_id}",
        headers=retry_headers,
    )
    try:
        publisher.publish([retry])
    except Exception:
        log.exception("report=%s republish retry gagal; requeue", request.report_id)
        metrics.DELIVERIES.labels(action="requeue").inc()
        return Settle.REQUEUE
    metrics.DELIVERIES.labels(action="retry").inc()
    return Settle.ACK
