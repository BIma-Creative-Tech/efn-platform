-- Hak user ClickHouse `efn_metabase` (dashboard). Kontrak: docs/04 §7.3 — SELECT view/mart, read-only.
-- Dijalankan saat deploy (idempoten) SETELAH devsecops membuat user dari secretnya, mis.:
--   CREATE USER IF NOT EXISTS efn_metabase IDENTIFIED WITH sha256_password BY '<secret>' HOST ANY;
-- Jalankan: clickhouse-client --secure --multiquery < infra/clickhouse/access/metabase-access.sql
-- Koneksi Metabase -> ClickHouse wajib HTTPS 8443 dengan CA internal (docs/04 §7.1).

-- readonly=2: driver Metabase mengirim SETTINGS per query; readonly sendiri tidak bisa diubah.
CREATE SETTINGS PROFILE IF NOT EXISTS metabase_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 120 MAX 300,
    max_memory_usage = 4000000000 MAX 4000000000;

CREATE ROLE IF NOT EXISTS metabase_reader;

-- View semantic layer.
GRANT SELECT ON efn_dwh.v_competitor_prices TO metabase_reader;
GRANT SELECT ON efn_dwh.v_price_hunter_quality TO metabase_reader;
GRANT SELECT ON efn_dwh.v_loyalty_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_sales_daily TO metabase_reader;

-- Mart: semua kolom KECUALI identitas pseudonim (user_hash, member_hash, customer_id). Analisis per member
-- (mis. kohort) butuh keputusan DPO dan grant terpisah, bukan default dashboard.
GRANT SELECT(report_id, reported_at, product_name, product_sku, competitor_name, price, extracted_price, status,
             reason, ocr_confidence, geo_lat, geo_lng, updated_at) ON efn_dwh.competitor_prices TO metabase_reader;
GRANT SELECT(event_id, company_code, delta_points, reason, source, source_ref, created_at)
    ON efn_dwh.loyalty_events TO metabase_reader;
GRANT SELECT(line_id, source, company_id, company_name, brand, store_id, order_ref, order_date, product_id,
             product_name, category, qty, price_unit, discount_pct, subtotal, tax_amount, cost_total, write_date)
    ON efn_dwh.fact_sales_line TO metabase_reader;

GRANT metabase_reader TO efn_metabase;
SET DEFAULT ROLE metabase_reader TO efn_metabase;
ALTER USER efn_metabase SETTINGS PROFILE 'metabase_readonly';
