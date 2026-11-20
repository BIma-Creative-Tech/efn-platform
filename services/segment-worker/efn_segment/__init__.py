"""segment-worker (G50, ADR-047 Proposed, docs/04 §28.7): segmentasi harian perilaku visitor supermarket.

Baca ClickHouse `efn_segment.*` (user `efn_segment_reader`, read-only) -> hitung segmen -> anggota (subject_key) ke BFF
`POST /internal/v1/segments/{run_id}/members` (mTLS CN `segment-worker`) -> ringkasan TANPA data member ke Odoo
`efn.campaign/efn_campaign_proposal_upsert` (/json/2, user teknis `efn_segment_<kode>`). Tidak menulis ClickHouse.
"""

MODEL_VERSION = "g50-1.0.0"
