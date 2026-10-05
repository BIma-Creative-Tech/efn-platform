"""Topologi queue, payload event, dan kebijakan retry sesuai docs/04_SERVICE_CONTRACTS.md §4. Logika murni.

Kontrak v2 (5 Okt 2026): tidak ada nomor HP mentah di payload. `ocr.requested` membawa `user_hash`; event keluar
membawa `member_hash` (= `user_hash` laporan) + `source`/`source_ref` agar BFF bisa mendekripsi `user_enc` dari
baris sumber. Properti AMQP `user_id` disetel publisher (lihat publisher.py, SEC-08).
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from efn_ocr.errors import PoisonMessageError

SCHEMA_VERSION = 2
SOURCE_PRICE_HUNTER = "price_hunter"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
QUEUE_OCR_REQUESTED = "ocr.requested"
QUEUE_LOYALTY_EARN = "loyalty.earn.requested"
QUEUE_NOTIFY_PUSH = "notify.push"
ALL_QUEUES = (QUEUE_OCR_REQUESTED, QUEUE_LOYALTY_EARN, QUEUE_NOTIFY_PUSH)
RETRY_HEADER = "x-retry-count"
MAX_BODY_BYTES = 64 * 1024


def dlq_name(queue: str) -> str:
    return f"{queue}.dlq"


def queue_arguments(queue: str) -> dict[str, str]:
    """Argumen queue utama (acuan kontrak §4). Sejak Gelombang 5b queue dideklarasikan broker (definitions devsecops);
    worker hanya passive declare, jadi nilai ini dipakai sebagai acuan test/kontrak, bukan untuk membuat queue."""
    return {
        "x-queue-type": "quorum",
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": dlq_name(queue),
    }


def dlq_arguments() -> dict[str, str]:
    return {"x-queue-type": "quorum"}


@dataclass(frozen=True)
class OcrRequest:
    report_id: str
    user_hash: str
    image_bucket: str | None
    image_object: str | None


def is_member_hash(value: object) -> bool:
    """`member_hash`/`user_hash` = hex sha256 huruf kecil (64 karakter), docs/04 §1."""
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def parse_ocr_request(body: bytes) -> OcrRequest:
    """Validasi minimal payload `ocr.requested`. Data laporan selebihnya dibaca dari PostgreSQL (source of truth)."""
    if len(body) > MAX_BODY_BYTES:
        raise PoisonMessageError("payload terlalu besar")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PoisonMessageError("payload bukan JSON") from exc
    if not isinstance(data, dict):
        raise PoisonMessageError("payload bukan objek JSON")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise PoisonMessageError("schema_version tidak didukung")
    raw_id = data.get("report_id")
    try:
        report_id = str(uuid.UUID(str(raw_id)))
    except ValueError as exc:
        raise PoisonMessageError("report_id bukan UUID") from exc
    user_hash = data.get("user_hash")
    if not is_member_hash(user_hash):
        raise PoisonMessageError("user_hash wajib (hex sha256)")
    if "user_id" in data or "member_ref" in data:
        # Kontrak v2: nomor HP mentah tidak boleh ada di payload. Tolak agar publisher yang salah segera terlihat.
        raise PoisonMessageError("payload v2 tidak boleh memuat nomor HP (user_id/member_ref)")
    bucket = data.get("image_bucket")
    obj = data.get("image_object")
    return OcrRequest(
        report_id=report_id,
        user_hash=str(user_hash),
        image_bucket=bucket if isinstance(bucket, str) else None,
        image_object=obj if isinstance(obj, str) else None,
    )


def retry_count(headers: dict[str, Any] | None) -> int:
    """Baca header retry dengan toleransi tipe (int/str/bytes); nilai tidak valid dianggap 0."""
    if not headers:
        return 0
    value = headers.get(RETRY_HEADER, 0)
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 0
    return max(count, 0)


class FailureAction(StrEnum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"


def action_for_failure(retries_so_far: int, max_retries: int) -> FailureAction:
    """Error transien: retry sampai `max_retries` kali (dihitung via header), lalu DLQ."""
    if retries_so_far < max_retries:
        return FailureAction.RETRY
    return FailureAction.DEAD_LETTER


@dataclass(frozen=True)
class OutgoingMessage:
    queue: str
    body: bytes
    message_id: str
    headers: dict[str, Any] = field(default_factory=dict)


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def loyalty_idempotency_key(report_id: str) -> str:
    return f"price_hunter:{report_id}"


def notify_idempotency_key(report_id: str) -> str:
    return f"price_hunter_notify:{report_id}"


def _require_hash(member_hash: str) -> str:
    if not is_member_hash(member_hash):
        # Fail closed: jangan pernah mem-publish identitas yang bukan hash (mis. nomor HP dari skema lama).
        raise ValueError("member_hash harus hex sha256 64 karakter")
    return member_hash


def loyalty_earn_message(report_id: str, member_hash: str, points: int) -> OutgoingMessage:
    key = loyalty_idempotency_key(report_id)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "idempotency_key": key,
        "member_hash": _require_hash(member_hash),
        "points": points,
        "reason": "price_hunter_valid",
        "source": SOURCE_PRICE_HUNTER,
        "source_ref": report_id,
    }
    return OutgoingMessage(queue=QUEUE_LOYALTY_EARN, body=encode(payload), message_id=key)


def notify_message(report_id: str, member_hash: str, template: str, params: dict[str, Any]) -> OutgoingMessage:
    key = notify_idempotency_key(report_id)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "idempotency_key": key,
        "member_hash": _require_hash(member_hash),
        "source": SOURCE_PRICE_HUNTER,
        "source_ref": report_id,
        "template": template,
        "params": params,
    }
    return OutgoingMessage(queue=QUEUE_NOTIFY_PUSH, body=encode(payload), message_id=key)


def result_messages(
    report_id: str,
    member_hash: str,
    status: str,
    reason: str,
    points: int,
    product_name: str = "",
) -> list[OutgoingMessage]:
    """Event yang dipublish setelah keputusan. `flagged` tidak memicu notifikasi (menunggu review manual).

    `member_hash` = `price_hunter_report.user_hash`. BFF memverifikasi report `source_ref` berstatus `valid` dengan
    `user_hash = member_hash` dan menyelesaikan penerima notifikasi dari `user_enc` (docs/04 §4).
    `params` wajib memuat kunci yang dipakai template BFF (`src/notification/templates.ts`):
    `price_hunter_valid` -> product_name, points; `price_hunter_rejected` -> product_name, reason.
    """
    name = (product_name or "")[:100]
    if status == "valid":
        messages: list[OutgoingMessage] = []
        if points > 0:
            messages.append(loyalty_earn_message(report_id, member_hash=member_hash, points=points))
        params = {"report_id": report_id, "product_name": name, "points": points}
        messages.append(notify_message(report_id, member_hash, "price_hunter_valid", params))
        return messages
    if status == "rejected":
        params = {"report_id": report_id, "product_name": name, "reason": reason}
        return [notify_message(report_id, member_hash, "price_hunter_rejected", params)]
    return []


def short_hash(value: str | None) -> str:
    """Prefix hash untuk korelasi log (bukan identitas): 8 karakter pertama."""
    text = value or ""
    return f"{text[:8]}..." if text else "-"
