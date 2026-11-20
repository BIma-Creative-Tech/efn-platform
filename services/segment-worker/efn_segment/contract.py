"""Payload & validasi lokal (docs/04 §28.7). Semua payload divalidasi SEBELUM panggilan pertama ke BFF/Odoo.

1. BFF `POST /internal/v1/segments/{run_id}/members` — PERSIS kontrak §28.7:
   `{ schema_version: 1, run_id, segment_code, subject_keys: [<= 5000], chunk_no }`; idempoten per
   `(run_id, segment_code, chunk_no)`. Anggota diurutkan + dedup sebelum dipotong -> retry menghasilkan chunk identik.
2. Odoo `efn.campaign/efn_campaign_proposal_upsert` (/json/2) — bentuk = validator Odoo
   (`efn_campaign/models/efn_campaign.py` + `campaign_api.py`, QA G50 #1):
   `{ schema_version: 1, company_code, run_id, segment_code, member_count, metrics: {<kunci>: angka},
   summary: "<= 256 karakter", generated_at: "YYYY-MM-DDTHH:MM:SSZ" }`.
   `metrics` HANYA angka (kunci `^[a-z][a-z0-9_]{0,39}$`, <= 20 kunci, |nilai| < 1e12): `lookback_days`,
   `product_count`, `as_of_yyyymmdd`, `k_min`, `top_product_count`, `top_product_subjects`, dan untuk price_sensitive
   `reason_market`, `reason_habit`. Produk teratas (>= k subjek) masuk `summary` teks; id produk >= 6 digit tidak
   ditulis (pola PII Odoo `\\d{6,}` menolak). Validasi lokal = validator Odoo + tolak string hex-64 (fail closed:
   data member tidak pernah sampai Odoo, L-155).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from efn_segment.segments import SEGMENT_CODES, Segment, SegmentParams, top_products

SCHEMA_VERSION = 1
MAX_SUBJECTS_PER_CHUNK = 5000
MAX_PROPOSAL_BYTES = 64 * 1024
# = efn_campaign.py MAX_METRICS / MAX_SUMMARY / MAX_MEMBER_COUNT (diuji test_odoo_contract_parity.py).
MAX_METRICS = 20
MAX_SUMMARY = 256
MAX_MEMBER_COUNT = 10_000_000
MAX_METRIC_ABS = 1e12
MEMBERS_KEYS = frozenset({"schema_version", "run_id", "segment_code", "subject_keys", "chunk_no"})
PROPOSAL_KEYS = frozenset(
    {"schema_version", "company_code", "run_id", "segment_code", "member_count", "metrics", "summary", "generated_at"}
)
REQUIRED_METRICS = ("lookback_days", "product_count")
# = campaign_api.py METRIC_KEY_RE / PII_RE; efn_campaign.py RFC3339_UTC_RE.
METRIC_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PII_RE = re.compile(r"\d{6,}|[^@\s]+@[^@\s]+\.[^@\s]+")
RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
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
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    tops = top_products(segment.product_subjects, k_min, top_n)
    metrics: dict[str, int] = {
        "lookback_days": params.wishlist_lookback_days if segment.code == "wishlist_idle" else params.lookback_days,
        "product_count": len(segment.product_subjects),
        "as_of_yyyymmdd": as_of.year * 10_000 + as_of.month * 100 + as_of.day,
        "k_min": int(k_min),
        "top_product_count": len(tops),
        "top_product_subjects": sum(t["subjects"] for t in tops),
    }
    for reason in sorted(segment.reason_counts):
        metrics[f"reason_{reason}"] = int(segment.reason_counts[reason])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "company_code": company,
        "run_id": run_id,
        "segment_code": segment.code,
        "member_count": segment.member_count,
        "metrics": metrics,
        "summary": top_summary(tops, k_min),
        "generated_at": rfc3339_utc(generated_at or datetime.now(UTC)),
    }
    validate_proposal(payload)
    return payload


def rfc3339_utc(value: datetime) -> str:
    ts = value if value.tzinfo else value.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def top_summary(tops: Sequence[Mapping[str, int]], k_min: int) -> str:
    """Teks <= 256 karakter tanpa PII: `N produk teratas (>= k subjek): id 123 (40), ...`. Id produk >= 6 digit dan
    jumlah subjek >= 6 digit tidak ditulis (pola PII Odoo `\\d{6,}`); dihitung sebagai `+n lainnya`."""
    if not tops:
        return ""
    head = f"{len(tops)} produk teratas (>= {k_min} subjek): "
    parts: list[str] = []
    skipped = 0
    for item in tops:
        part = f"id {item['product_id']} ({item['subjects']})"
        candidate = head + ", ".join([*parts, part])
        if PII_RE.search(part) or len(candidate) > MAX_SUMMARY - 16:
            skipped += 1
            continue
        parts.append(part)
    text = head + ", ".join(parts) if parts else head.rstrip(": ")
    if skipped:
        text += f" (+{skipped} lainnya)"
    return text[:MAX_SUMMARY]


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
    count = _nonneg_int(payload["member_count"], "invalid_member_count")
    if not 1 <= count <= MAX_MEMBER_COUNT:
        raise ContractError("invalid_member_count")
    metrics = payload["metrics"]
    if not isinstance(metrics, Mapping) or len(metrics) > MAX_METRICS:
        raise ContractError("metrics_keys")
    for name, value in metrics.items():
        if not isinstance(name, str) or not METRIC_KEY_RE.fullmatch(name) or PII_RE.search(name):
            raise ContractError("metrics_keys")
        if isinstance(value, bool) or not isinstance(value, int | float) or not abs(value) < MAX_METRIC_ABS:
            raise ContractError("metrics_not_number")
    for required in REQUIRED_METRICS:
        if required not in metrics:
            raise ContractError("metrics_keys")
    summary = payload["summary"]
    if not isinstance(summary, str) or len(summary) > MAX_SUMMARY or PII_RE.search(summary):
        raise ContractError("invalid_summary")
    generated = payload["generated_at"]
    if not isinstance(generated, str) or not RFC3339_UTC_RE.fullmatch(generated):
        raise ContractError("invalid_generated_at")
    _scan_no_identity(payload)
    if len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_PROPOSAL_BYTES:
        raise ContractError("proposal_too_large")


def total_members(payloads: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(p["subject_keys"]) for p in payloads)
