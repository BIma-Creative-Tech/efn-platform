-- Akses read-only forecast-worker (G44; ADR-046 Proposed §3.1, docs/04 §28.2, L-145, K25-02, SEC-04).
-- User ClickHouse `efn_forecast_reader` (services/forecast-worker) HANYA membaca database `efn_forecast`: view SQL
-- SECURITY DEFINER dengan kolom eksplisit di atas efn_dwh.v_fc_* (init/15-g44-forecast.sql). Worker TIDAK menulis apa
-- pun ke ClickHouse (hasil forecast ditulis ke Odoo lalu turun lewat ETL).
-- clickhouse-init.sh membuat user dari secret `clickhouse_password_forecast` dan mengikat role/profile ke user (pola
-- insight/text2sql). Dijalankan clickhouse-init.sh setiap deploy (idempoten) SETELAH init/*.sql.
-- Tanpa row policy per PT: satu user dipakai worker untuk PT yang dikonfigurasi (`--company`), view tidak memuat data
-- pribadi; pembatasan PT ditegakkan di sisi tulis (API key Odoo per PT, 403 company_mismatch).

-- readonly=2: tidak bisa menulis/DDL; boleh SETTINGS per query (clickhouse-connect mengirim setting sesi) tetapi batas
-- atas dikunci MAX. Batch harian membaca histori ±2 tahun (2.400–50.000 seri) -> batas baris/memori lebih besar dari
-- insight, konkurensi kecil (satu worker).
CREATE SETTINGS PROFILE IF NOT EXISTS forecast_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 600,
    max_result_rows = 50000000 MAX 50000000,
    result_overflow_mode = 'throw',
    max_rows_to_read = 2000000000 MAX 2000000000,
    max_memory_usage = 4000000000 MAX 4000000000,
    max_concurrent_queries_for_user = 4 MAX 4;
-- Profil dari deploy sebelumnya (CREATE ... IF NOT EXISTS tidak mengubahnya) -- pola insight-access.sql.
ALTER SETTINGS PROFILE forecast_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 600,
    max_result_rows = 50000000 MAX 50000000,
    result_overflow_mode = 'throw',
    max_rows_to_read = 2000000000 MAX 2000000000,
    max_memory_usage = 4000000000 MAX 4000000000,
    max_concurrent_queries_for_user = 4 MAX 4;

CREATE ROLE IF NOT EXISTS forecast_reader;

-- Pertahanan berlapis: role ini tidak pernah punya hak langsung di efn_dwh / efn_esb (hanya efn_forecast).
REVOKE ALL ON efn_dwh.* FROM forecast_reader;
REVOKE ALL ON efn_esb.* FROM forecast_reader;

-- Definer view efn_forecast: tidak bisa login (HOST NONE, tanpa password). Hak MINIMUM = view v_fc_* + tabel dasarnya
-- (view efn_dwh.v_fc_* bertipe INVOKER -> dieksekusi dengan hak definer ini). Tanpa GL/audit/loyalty/Price Hunter.
CREATE USER IF NOT EXISTS efn_forecast_definer NOT IDENTIFIED HOST NONE;
GRANT SELECT ON efn_dwh.v_fc_sales_daily TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.v_fc_stock_daily TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.v_fc_scrap_daily TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.v_fc_calendar TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.v_fc_product TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.v_fc_warehouse TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.fact_sales_line TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.dim_outlet TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.dim_fc_warehouse TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.fact_stock_snapshot TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.stock_snapshot_run TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.fact_scrap TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.dim_product TO efn_forecast_definer;
GRANT SELECT ON efn_dwh.dim_calendar TO efn_forecast_definer;

CREATE DATABASE IF NOT EXISTS efn_forecast;

-- Kolom = yang dibaca services/forecast-worker/efn_forecast/source.py (diuji test_access_sql.py). Tanpa kolom identitas
-- (customer_id, member/user hash, nama produk, partner, lot).
CREATE OR REPLACE VIEW efn_forecast.v_sales_daily DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS
SELECT day, company_id, company_code, warehouse_id, warehouse_code, product_id, qty
FROM efn_dwh.v_fc_sales_daily;

CREATE OR REPLACE VIEW efn_forecast.v_stock_daily DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, product_id, on_hand
FROM efn_dwh.v_fc_stock_daily;

CREATE OR REPLACE VIEW efn_forecast.v_scrap_daily DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS
SELECT day, company_id, warehouse_id, product_id, category, qty
FROM efn_dwh.v_fc_scrap_daily;

CREATE OR REPLACE VIEW efn_forecast.v_calendar DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS
SELECT calendar_id, company_id, warehouse_id, date, kind, name, source, active
FROM efn_dwh.v_fc_calendar;

CREATE OR REPLACE VIEW efn_forecast.v_product DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS
SELECT product_id, template_id, category, company_id, active, sale_ok, is_storable, use_expiration_date
FROM efn_dwh.v_fc_product;

CREATE OR REPLACE VIEW efn_forecast.v_warehouse DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS
SELECT warehouse_id, code, company_id, company_code, active
FROM efn_dwh.v_fc_warehouse;

GRANT SELECT ON efn_forecast.* TO forecast_reader;

-- Kuota per jam (batch harian + mingguan; plafon agar bug loop tidak membebani ClickHouse bersama).
CREATE QUOTA IF NOT EXISTS forecast_quota KEYED BY user_name
    FOR INTERVAL 1 hour MAX queries = 2000, errors = 200, result_rows = 500000000, read_rows = 50000000000,
        execution_time = 7200
    TO forecast_reader;
