"""Klien BFF `POST /internal/v1/segments/{run_id}/members` (docs/04 §28.7).

- HTTPS + verifikasi CA internal + sertifikat klien CN `segment-worker` (mTLS; satu-satunya autentikasi menurut
  kontrak).
- Body = BFF `src/campaign/segment-ingest.service.ts` (kunci tak dikenal ditolak): `schema_version, company_code,
  run_id, segment_code, subject_keys, chunk_no` (SEC-G50-08/16: `company_code` wajib, ikut hash idempotensi, satu run =
  satu PT). Header tambahan `Idempotency-Key: segment:<run_id>:<segment_code>:<chunk_no>` (informasi; kunci
  idempotensi tetap tuple body). Contoh bersama: `tests/fixtures/segment_members_v1.json`.
- Respons BFF: `200 {ok, run_id, segment_code, chunk_no, accepted, duplicate_chunk}` (chunk ulang dengan isi sama =
  `duplicate_chunk: true`, sukses). `400 invalid_request` (body tidak valid), `409 chunk_conflict` (isi berbeda untuk
  kunci sama), `409 run_company_mismatch` (run sudah milik PT lain) -> tidak diulang. `409 duplicate_chunk` tetap
  diterima sebagai sukses (toleransi bila BFF berubah).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from efn_segment.contract import idempotency_key, validate_members
from efn_segment.transport import RetryPolicy, Transport, error_code, parse_object, post_json

DUPLICATE_CODES = frozenset({"duplicate_chunk", "duplicate"})


def _duplicate(status: int, body: bytes) -> bool:
    return status == 409 and error_code(body) in DUPLICATE_CODES


@dataclass
class BffSegmentClient:
    transport: Transport
    policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __repr__(self) -> str:
        return "BffSegmentClient()"

    def send_members(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        validate_members(payload)  # juga memvalidasi run_id (UUIDv4) sebelum masuk path
        run_id = payload["run_id"]
        headers = {"Idempotency-Key": idempotency_key(run_id, payload["segment_code"], payload["chunk_no"])}
        status, content = post_json(
            self.transport,
            f"/internal/v1/segments/{run_id}/members",
            payload,
            headers,
            self.policy,
            "bff segments/members",
            accept=_duplicate,
        )
        if status == 409:
            return {"duplicate": True}
        return parse_object(content)
