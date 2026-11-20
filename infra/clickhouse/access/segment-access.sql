-- Akses read-only segment-worker (G50; ADR-047 Proposed §3.4, docs/04 §28.7, L-153/L-155, K25-02, SEC-04).
-- User ClickHouse `efn_segment_reader` (services/segment-worker) HANYA membaca database `efn_segment`: view SQL
-- SECURITY DEFINER dengan kolom eksplisit di atas efn_dwh.v_seg_* (init/16-g50-behavior.sql). Worker TIDAK menulis apa pun
-- ke ClickHouse (anggota segmen -> BFF, ringkasan -> Odoo).
-- clickhouse-init.sh (devsecops) WAJIB menambah, pola forecast G44:
--   create_user efn_segment_reader /run/secrets/clickhouse_password_segment
--   GRANT segment_reader TO efn_segment_reader; SET DEFAULT ROLE segment_reader TO efn_segment_reader;
--   ALTER USER efn_segment_reader SETTINGS PROFILE 'segment_readonly';
-- Dijalankan clickhouse-init.sh setiap deploy (idempoten) SETELAH init/*.sql.
-- Tanpa row policy per PT: satu user dipakai worker untuk PT yang dikonfigurasi (SEGMENT_COMPANIES); semua query worker
-- difilter company_code; pembatasan tulis = API key Odoo per PT (403 company_mismatch) + allowlist BFF.
-- SEC-215 (G44): grant PER VIEW (bukan efn_segment.*) agar objek baru di database ini tidak otomatis terbaca.

CREATE SETTINGS PROFILE IF NOT EXISTS segment_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 600,
    max_result_rows = 20000000 MAX 20000000,
    result_overflow_mode = 'throw',
    max_rows_to_read = 2000000000 MAX 2000000000,
    max_memory_usage = 4000000000 MAX 4000000000,
    max_concurrent_queries_for_user = 2 MAX 2;
ALTER SETTINGS PROFILE segment_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 600,
    max_result_rows = 20000000 MAX 20000000,
    result_overflow_mode = 'throw',
    max_rows_to_read = 2000000000 MAX 2000000000,
    max_memory_usage = 4000000000 MAX 4000000000,
    max_concurrent_queries_for_user = 2 MAX 2;

CREATE ROLE IF NOT EXISTS segment_reader;

-- Pertahanan berlapis: role ini tidak pernah punya hak langsung di efn_dwh / efn_esb / efn_forecast.
REVOKE ALL ON efn_dwh.* FROM segment_reader;
REVOKE ALL ON efn_esb.* FROM segment_reader;
REVOKE ALL ON efn_forecast.* FROM segment_reader;

-- Definer view efn_segment: tidak bisa login (HOST NONE, tanpa password). Hak MINIMUM = view v_seg_* + tabel dasarnya.
-- competitor_prices dibaca HANYA lewat v_seg_market_price (median per produk, tanpa user_hash/lokasi).
CREATE USER IF NOT EXISTS efn_segment_definer NOT IDENTIFIED HOST NONE;
GRANT SELECT ON efn_dwh.v_seg_subject_product_daily TO efn_segment_definer;
GRANT SELECT ON efn_dwh.v_seg_subject_search_daily TO efn_segment_definer;
GRANT SELECT ON efn_dwh.v_seg_product TO efn_segment_definer;
GRANT SELECT ON efn_dwh.v_seg_market_price TO efn_segment_definer;
GRANT SELECT ON efn_dwh.fact_visitor_event TO efn_segment_definer;
GRANT SELECT ON efn_dwh.dim_product TO efn_segment_definer;
GRANT SELECT ON efn_dwh.competitor_prices TO efn_segment_definer;

CREATE DATABASE IF NOT EXISTS efn_segment;

-- Kolom = yang dibaca services/segment-worker/efn_segment/source.py (diuji test_access_sql.py).
CREATE OR REPLACE VIEW efn_segment.v_subject_product_daily DEFINER = efn_segment_definer SQL SECURITY DEFINER AS
SELECT day, company_code, subject_key, subject_kind, product_id, views, view_seconds, oos_views, cart_adds, checkouts,
       purchases, last_view_at, last_wishlist_add_at, last_wishlist_remove_at, last_purchase_at, max_view_price_idr,
       purchase_price_sum, purchase_price_n
FROM efn_dwh.v_seg_subject_product_daily;

CREATE OR REPLACE VIEW efn_segment.v_subject_search_daily DEFINER = efn_segment_definer SQL SECURITY DEFINER AS
SELECT day, company_code, subject_key, subject_kind, searches, no_result_searches
FROM efn_dwh.v_seg_subject_search_daily;

CREATE OR REPLACE VIEW efn_segment.v_product DEFINER = efn_segment_definer SQL SECURITY DEFINER AS
SELECT company_code, product_id, category
FROM efn_dwh.v_seg_product;

CREATE OR REPLACE VIEW efn_segment.v_market_price DEFINER = efn_segment_definer SQL SECURITY DEFINER AS
SELECT product_id, median_price_idr, reports
FROM efn_dwh.v_seg_market_price;

REVOKE ALL ON efn_segment.* FROM segment_reader;
GRANT SELECT ON efn_segment.v_subject_product_daily TO segment_reader;
GRANT SELECT ON efn_segment.v_subject_search_daily TO segment_reader;
GRANT SELECT ON efn_segment.v_product TO segment_reader;
GRANT SELECT ON efn_segment.v_market_price TO segment_reader;

-- Kuota per jam (batch harian per PT; plafon agar bug loop tidak membebani ClickHouse bersama).
CREATE QUOTA IF NOT EXISTS segment_quota KEYED BY user_name
    FOR INTERVAL 1 hour MAX queries = 500, errors = 100, result_rows = 200000000, read_rows = 20000000000,
        execution_time = 3600
    TO segment_reader;
