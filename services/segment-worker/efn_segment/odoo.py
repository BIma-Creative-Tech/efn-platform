"""Klien /json/2 ke odoo-rpc-gateway: `efn.campaign/efn_campaign_proposal_upsert` (docs/04 §28.7, L-155/L-156).

- `POST <ODOO_RPC_URL>/json/2/efn.campaign/efn_campaign_proposal_upsert`, TLS verify-full CA internal + sertifikat
  klien CN `segment-worker`, `Authorization: bearer <API key PT>` dari `/run/secrets/odoo_api_key_campaign_<kode>`
  (user teknis `efn_campaign_<kode>` = tepat satu company). Body = argumen bernama = payload ringkasan apa
  adanya (tanpa `context`).
- Galat Odoo diasumsikan mengikuti pola efn_demand_forecast: `UserError("efn_campaign:<kode>")` (422),
  `AccessError("efn_campaign:company_mismatch|access_denied")` (403). 422/403 tidak diulang; 429/5xx/timeout diulang.
- Idempotensi (ASUMSI G50): Odoo meng-upsert per `(run_id, segment_code)`; kirim ulang = tanpa duplikat.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from efn_segment.contract import validate_proposal
from efn_segment.transport import HttpError, RetryPolicy, Transport, parse_object, post_json

MODEL = "efn.campaign"
METHOD = "efn_campaign_proposal_upsert"
ERROR_PREFIX = "efn_campaign:"


@dataclass
class OdooCampaignClient:
    transport: Transport
    api_key: str = field(repr=False)
    database: str = ""
    policy: RetryPolicy = field(default_factory=RetryPolicy)

    def __repr__(self) -> str:  # tanpa API key
        return "OdooCampaignClient()"

    def proposal_upsert(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        validate_proposal(payload)
        headers = {"Authorization": f"bearer {self.api_key}"}
        if self.database:
            headers["X-Odoo-Database"] = self.database
        _status, content = post_json(
            self.transport,
            f"/json/2/{MODEL}/{METHOD}",
            payload,
            headers,
            self.policy,
            f"odoo {METHOD}",
            prefix=ERROR_PREFIX,
        )
        # Respons belum ditetapkan kontrak: objek (field tambahan diabaikan) atau `true`/`null` json2. Hanya `ok: false`
        # eksplisit / `false` yang dianggap gagal.
        stripped = content.strip()
        if stripped in (b"", b"null", b"true"):
            return {}
        if stripped == b"false":
            raise HttpError("invalid_response", "not_ok", 200)
        result = parse_object(content)
        if result.get("ok") is False:
            raise HttpError("invalid_response", "not_ok", 200)
        return result
