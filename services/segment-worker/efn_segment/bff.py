"""Klien BFF `POST /internal/v1/segments/{run_id}/members` (docs/04 §28.7).

- HTTPS + verifikasi CA internal + sertifikat klien CN `segment-worker` (mTLS; satu-satunya autentikasi menurut
  kontrak).
- Body PERSIS kontrak (`additionalProperties: false` di BFF): `schema_version, run_id, segment_code, subject_keys,
  chunk_no`. Header tambahan `Idempotency-Key: segment:<run_id>:<segment_code>:<chunk_no>` (informasi; kunci idempotensi
  tetap tuple body).
- Sukses = 2xx. ASUMSI G50 (respons belum ditetapkan kontrak): chunk yang sudah diterima boleh dijawab 2xx (mis.
  `{ "duplicate": true }`) ATAU `409` dengan kode `duplicate_chunk` -> keduanya sukses. 4xx lain tidak diulang.
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
