"""Payload & validasi lokal (docs/04 §28.7). Semua payload divalidasi SEBELUM panggilan pertama ke BFF/Odoo.

1. BFF `POST /internal/v1/segments/{run_id}/members` — PERSIS kontrak §28.7:
   `{ schema_version: 1, run_id, segment_code, subject_keys: [<= 5000], chunk_no }`; idempoten per
   `(run_id, segment_code, chunk_no)`. Anggota diurutkan + dedup sebelum dipotong -> retry menghasilkan chunk identik.
2. Odoo `efn.campaign/efn_campaign_proposal_upsert` (/json/2) — kontrak §28.7 hanya menyebut "ringkasan segmen,
   tanpa data member"; field di bawah = ASUMSI G50 (laporan efn-data-ai-engineer):
   `{ schema_version: 1, company_code, run_id, segment_code, member_count, metrics: { as_of, lookback_days,
   product_count, top_products: [{product_id, subjects}], reason_counts: {market, habit} } }`.
   Validasi menolak string berbentuk subject_key (hex 64) di mana pun dan kunci di luar allowlist (fail closed: data
   member tidak pernah sampai Odoo, L-155).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from efn_segment.segments import SEGMENT_CODES, Segment, SegmentParams, top_products

SCHEMA_VERSION = 1
MAX_SUBJECTS_PER_CHUNK = 5000
MAX_PROPOSAL_BYTES = 64 * 1024
MAX_TOP_PRODUCTS = 50
REASONS = frozenset({"market", "habit"})
MEMBERS_KEYS = frozenset({"schema_version", "run_id", "segment_code", "subject_keys", "chunk_no"})
PROPOSAL_KEYS = frozenset({"schema_version", "company_code", "run_id", "segment_code", "member_count", "metrics"})
METRIC_KEYS = frozenset({"as_of", "lookback_days", "product_count", "top_products", "reason_counts"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX64_ANY_RE = re.compile(r"[0-9a-fA-F]{64}")
_COMPANY_RE = re.compile(r"^[A-Z]{2,8}$")


class ContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _uuid4(value: Any) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except ValueError:
        raise ContractError("invalid_run_id") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ContractError("invalid_run_id")
    return value


def _nonneg_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(code)
    return value


def chunk_subjects(keys: Iterable[str], size: int = MAX_SUBJECTS_PER_CHUNK) -> list[list[str]]:
    """Dedup + urut + potong deterministik (retry = chunk identik, idempoten di BFF)."""
    if not 1 <= size <= MAX_SUBJECTS_PER_CHUNK:
        raise ContractError("invalid_chunk_size")
    ordered = sorted(set(keys))
    for key in ordered:
        if not _HASH_RE.fullmatch(key):
            raise ContractError("invalid_subject_key")
    return [ordered[i : i + size] for i in range(0, len(ordered), size)]


def members_payloads(run_id: str, segment_code: str, keys: Iterable[str], size: int) -> list[dict[str, Any]]:
    payloads = [
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "segment_code": segment_code,
            "subject_keys": part,
            "chunk_no": i,
        }
        for i, part in enumerate(chunk_subjects(keys, size))
    ]
    for payload in payloads:
        validate_members(payload)
    return payloads


def validate_members(payload: Mapping[str, Any]) -> None:
    if set(payload) != MEMBERS_KEYS:
        raise ContractError("members_keys")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ContractError("schema_version")
    _uuid4(payload["run_id"])
    if payload["segment_code"] not in SEGMENT_CODES:
        raise ContractError("invalid_segment_code")
    _nonneg_int(payload["chunk_no"], "invalid_chunk_no")
    keys = payload["subject_keys"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_SUBJECTS_PER_CHUNK:
        raise ContractError("subject_keys_size")
    if len(set(keys)) != len(keys):
        raise ContractError("subject_keys_duplicate")
    for key in keys:
        if not isinstance(key, str) or not _HASH_RE.fullmatch(key):
            raise ContractError("invalid_subject_key")


def idempotency_key(run_id: str, segment_code: str, chunk_no: int) -> str:
    return f"segment:{run_id}:{segment_code}:{chunk_no}"


def proposal_payload(
    company: str,
    run_id: str,
    segment: Segment,
    params: SegmentParams,
    as_of: date,
    k_min: int,
    top_n: int,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "lookback_days": params.wishlist_lookback_days if segment.code == "wishlist_idle" else params.lookback_days,
        "product_count": len(segment.product_subjects),
        "top_products": top_products(segment.product_subjects, k_min, top_n),
    }
    if segment.reason_counts:
        metrics["reason_counts"] = {k: int(v) for k, v in segment.reason_counts.items()}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "company_code": company,
        "run_id": run_id,
        "segment_code": segment.code,
        "member_count": segment.member_count,
        "metrics": metrics,
    }
    validate_proposal(payload)
    return payload


def _scan_no_identity(value: Any) -> None:
    """Tidak boleh ada string berbentuk hash (subject_key/member hash) di ringkasan Odoo."""
    if isinstance(value, str):
        if _HEX64_ANY_RE.search(value):
            raise ContractError("member_data_in_proposal")
    elif isinstance(value, Mapping):
        for k, v in value.items():
            _scan_no_identity(str(k))
            _scan_no_identity(v)
    elif isinstance(value, list | tuple):
        for v in value:
            _scan_no_identity(v)


def validate_proposal(payload: Mapping[str, Any]) -> None:
    if set(payload) != PROPOSAL_KEYS:
        raise ContractError("proposal_keys")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ContractError("schema_version")
    if not isinstance(payload["company_code"], str) or not _COMPANY_RE.fullmatch(payload["company_code"]):
        raise ContractError("invalid_company_code")
    _uuid4(payload["run_id"])
    if payload["segment_code"] not in SEGMENT_CODES:
        raise ContractError("invalid_segment_code")
    if _nonneg_int(payload["member_count"], "invalid_member_count") < 1:
        raise ContractError("invalid_member_count")
    metrics = payload["metrics"]
    if not isinstance(metrics, Mapping) or not set(metrics) <= METRIC_KEYS:
        raise ContractError("metrics_keys")
    for required in ("as_of", "lookback_days", "product_count", "top_products"):
        if required not in metrics:
            raise ContractError("metrics_keys")
    try:
        date.fromisoformat(str(metrics["as_of"]))
    except ValueError:
        raise ContractError("invalid_as_of") from None
    _nonneg_int(metrics["lookback_days"], "invalid_lookback_days")
    _nonneg_int(metrics["product_count"], "invalid_product_count")
    tops = metrics["top_products"]
    if not isinstance(tops, list) or len(tops) > MAX_TOP_PRODUCTS:
        raise ContractError("invalid_top_products")
    for item in tops:
        if not isinstance(item, Mapping) or set(item) != {"product_id", "subjects"}:
            raise ContractError("invalid_top_products")
        if _nonneg_int(item["product_id"], "invalid_top_products") < 1:
            raise ContractError("invalid_top_products")
        _nonneg_int(item["subjects"], "invalid_top_products")
    reasons = metrics.get("reason_counts", {})
    if not isinstance(reasons, Mapping) or not set(reasons) <= REASONS:
        raise ContractError("invalid_reason_counts")
    for value in reasons.values():
        _nonneg_int(value, "invalid_reason_counts")
    _scan_no_identity(payload)
    if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_PROPOSAL_BYTES:
        raise ContractError("proposal_too_large")


def total_members(payloads: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(p["subject_keys"]) for p in payloads)
