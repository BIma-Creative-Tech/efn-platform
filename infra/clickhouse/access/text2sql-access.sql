-- Akses read-only Text-to-SQL (R-S05, R-T06, SEC-04). Lapisan pertahanan KEDUA setelah guard sqlglot.
-- Kontrak: docs/04 §7.3 + §23.3 (G38) — user ClickHouse `efn_text2sql` (readonly=2) HANYA membaca database `efn_t2s`
-- (view SQL SECURITY DEFINER). Dijalankan clickhouse-init.sh setiap deploy (idempoten) SETELAH user dibuat dengan
-- password dari secret. Row policy cakupan PT (`efn_t2s_scope`) memuat kunci rahasia `text2sql_scope_key` -> dibuat
-- clickhouse-init.sh, BUKAN di file ini. Butuh user admin dengan access management (bukan user aplikasi).

-- readonly=2: tidak bisa menulis/DDL/mengubah readonly, tetapi masih boleh SETTINGS per query (service mengirim
-- max_execution_time + setting cakupan SQL_efn_*); batas atas dikunci constraint MAX sehingga tidak bisa dinaikkan.
CREATE SETTINGS PROFILE IF NOT EXISTS text2sql_readonly SETTINGS
    readonly = 2 CONST,
    max_execution_time = 15 MAX 30,
    max_result_rows = 1000 MAX 1000,
    result_overflow_mode = 'break',
    max_rows_to_read = 200000000 MAX 200000000,
    max_memory_usage = 4000000000 MAX 4000000000,
    max_concurrent_queries_for_user = 4 MAX 4,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST;

CREATE ROLE IF NOT EXISTS text2sql_reader;

-- G38: semua grant lama di efn_dwh (view + kolom tabel dasar, pra-G38) DICABUT. Text-to-SQL hanya melihat efn_t2s.
REVOKE ALL ON efn_dwh.* FROM text2sql_reader;

-- Definer view efn_t2s: tidak bisa login (HOST NONE, tanpa password). View SQL SECURITY DEFINER dieksekusi dengan
-- hak user ini (diverifikasi ClickHouse 24.8: role text2sql_reader tanpa grant efn_dwh tetap bisa membaca view).
CREATE USER IF NOT EXISTS efn_t2s_definer NOT IDENTIFIED HOST NONE;
GRANT SELECT ON efn_dwh.* TO efn_t2s_definer;

CREATE DATABASE IF NOT EXISTS efn_t2s;

-- Daftar view = services/text2sql/semantic_layer.yaml (sumber prompt + whitelist guard). Tanpa GL/audit (Q-48), tanpa
-- kolom identitas (member/user hash, customer, report_id, koordinat Price Hunter).
CREATE OR REPLACE VIEW efn_t2s.v_sales_daily DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT day, company_id, company_name, brand, store_id, store_name, category, gross_sales, line_discount,
       promo_discount, points_redeemed_idr, refunds, net_sales, tax, cogs, qty_sold, qty_returned, lines,
       refund_goods, points_returned_idr
FROM efn_dwh.v_pos_sales_daily;

CREATE OR REPLACE VIEW efn_t2s.v_outlet_daily DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT day, company_id, company_name, brand, store_id, store_name, orders, refund_orders, net_sales, gross_sales,
       discounts, points_redeemed_idr, refunds, avg_basket, refund_goods, points_returned_idr
FROM efn_dwh.v_pos_outlet_daily;

CREATE OR REPLACE VIEW efn_t2s.v_loyalty_daily DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT day, company_code, source, reason, points_issued, points_redeemed_or_reversed, events
FROM efn_dwh.v_loyalty_daily;

CREATE OR REPLACE VIEW efn_t2s.v_waste_daily DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT day, company_id, brand, warehouse_id, warehouse_name, reason_id, reason_name, category, scraps, qty, waste_value
FROM efn_dwh.v_waste_daily;

CREATE OR REPLACE VIEW efn_t2s.v_mo_yield_daily DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT day, company_id, brand, warehouse_id, warehouse_name, product_id, product_name, mos, planned_qty, produced_qty,
       yield_pct, byproduct_qty, variance_value, mos_alert
FROM efn_dwh.v_mo_yield_daily;

-- Price Hunter = harga kompetitor (bukan data per PT) -> tanpa row policy. report_id & koordinat sengaja tidak ada.
CREATE OR REPLACE VIEW efn_t2s.v_competitor_prices DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT reported_at, product_name, product_sku, competitor_name, price, extracted_price, ocr_confidence
FROM efn_dwh.v_competitor_prices;

CREATE OR REPLACE VIEW efn_t2s.v_price_hunter_quality DEFINER = efn_t2s_definer SQL SECURITY DEFINER AS
SELECT day, status, reason, reports
FROM efn_dwh.v_price_hunter_quality;

GRANT SELECT ON efn_t2s.* TO text2sql_reader;

-- Kuota per user (DoS / biaya): dihitung per jam.
CREATE QUOTA IF NOT EXISTS text2sql_quota KEYED BY user_name
    FOR INTERVAL 1 hour MAX queries = 300, errors = 100, result_rows = 300000, read_rows = 5000000000,
        execution_time = 1800
    TO text2sql_reader;

GRANT text2sql_reader TO efn_text2sql;
SET DEFAULT ROLE text2sql_reader TO efn_text2sql;
ALTER USER efn_text2sql SETTINGS PROFILE 'text2sql_readonly';
