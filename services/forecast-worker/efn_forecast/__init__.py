"""forecast-worker G44 (ADR-046 Proposed, docs/04 §28.2, R-T21): forecast permintaan SKU x gudang supermarket.

Baca ClickHouse `efn_forecast.*` (read-only) -> model statsforecast + penyesuaian kalender -> tulis Odoo lewat /json/2
(odoo-rpc-gateway, mTLS CN `forecast-worker`, API key per PT). Tidak pernah menulis ClickHouse/DB Odoo (L-145).
"""

MODEL_VERSION = "g44-1.0.0"
