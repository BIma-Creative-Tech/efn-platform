-- Tabel tambahan ETL (services/etl, ADR-003, ADR-020). Dijalankan setelah 01-schema.sql.

-- Event ledger poin (append-only di platform.loyalty_ledger). Hanya hash member (dihitung BFF); tidak ada nomor HP.
-- company_code = PT penanggung liabilitas poin (ADR-020, settlement antar-PT): EFN | RTL | FNB | FARM.
CREATE TABLE IF NOT EXISTS efn_dwh.loyalty_events
(
    event_id      String,
    member_hash   String,
    company_code  LowCardinality(String),
    delta_points  Int64,
    reason        LowCardinality(String),
    source        LowCardinality(String),
    source_ref    String,
    created_at    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(created_at)
PARTITION BY toYYYYMM(created_at)
ORDER BY (member_hash, event_id);

-- Volume yang dibuat sebelum kolom company_code ada (idempoten). Baris lama berisi '' sampai ETL dimuat ulang
-- (hapus baris watermark job loyalty_events untuk backfill penuh).
ALTER TABLE efn_dwh.loyalty_events ADD COLUMN IF NOT EXISTS company_code LowCardinality(String) AFTER member_hash;

-- Watermark per job ETL. Dibaca dengan argMax(watermark, updated_at) sehingga tidak bergantung pada merge.
CREATE TABLE IF NOT EXISTS efn_dwh.etl_watermark
(
    job          String,
    watermark    DateTime64(6, 'UTC'),
    rows_loaded  UInt64,
    updated_at   DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY job;

-- View semantic layer (dipakai Metabase & Text-to-SQL; whitelist di services/text2sql/semantic_layer.yaml).
CREATE VIEW IF NOT EXISTS efn_dwh.v_competitor_prices AS
SELECT report_id, reported_at, product_name, product_sku, competitor_name, price, extracted_price,
       ocr_confidence, geo_lat, geo_lng
FROM efn_dwh.competitor_prices FINAL
WHERE status = 'valid';

CREATE VIEW IF NOT EXISTS efn_dwh.v_price_hunter_quality AS
SELECT toDate(reported_at) AS day, status, reason, count() AS reports
FROM efn_dwh.competitor_prices FINAL
GROUP BY day, status, reason;

CREATE OR REPLACE VIEW efn_dwh.v_loyalty_daily AS
SELECT toDate(created_at, 'Asia/Jakarta') AS day, company_code, source, reason,
       sumIf(delta_points, delta_points > 0) AS points_issued,
       -sumIf(delta_points, delta_points < 0) AS points_redeemed_or_reversed,
       count() AS events
FROM efn_dwh.loyalty_events FINAL
GROUP BY day, company_code, source, reason;
