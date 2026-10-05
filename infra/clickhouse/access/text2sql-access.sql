-- Akses read-only Text-to-SQL (R-S05, R-T06, SEC-04). Lapisan pertahanan KEDUA setelah guard sqlglot.
-- Kontrak: docs/04 §7.3 — user ClickHouse `efn_text2sql` (readonly=2, SELECT view whitelist).
-- SENGAJA tidak di init/: dijalankan saat deploy (idempoten) SETELAH devsecops membuat user dengan password dari
-- secret, mis.:  CREATE USER IF NOT EXISTS efn_text2sql IDENTIFIED WITH sha256_password BY '<secret>'
--                  HOST ANY;   (password tidak pernah ada di repo)
-- Jalankan: clickhouse-client --secure --multiquery < infra/clickhouse/access/text2sql-access.sql
-- Butuh user admin dengan access management (bukan user aplikasi). Service Text-to-SQL WAJIB login sebagai
-- efn_text2sql, tidak pernah sebagai admin/efn_etl.

-- readonly=2: tidak bisa menulis/DDL/mengubah readonly, tetapi masih boleh SETTINGS per query (guard menambahkan
-- max_execution_time); batas atas dikunci dengan constraint MAX sehingga tidak bisa dinaikkan dari query.
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

-- Hanya view semantic layer (services/text2sql/semantic_layer.yaml).
GRANT SELECT ON efn_dwh.v_competitor_prices TO text2sql_reader;
GRANT SELECT ON efn_dwh.v_price_hunter_quality TO text2sql_reader;
GRANT SELECT ON efn_dwh.v_loyalty_daily TO text2sql_reader;
GRANT SELECT ON efn_dwh.v_sales_daily TO text2sql_reader;

-- View biasa ClickHouse dieksekusi dengan hak INVOKER, sehingga kolom tabel dasar yang dipakai view juga perlu
-- di-GRANT. Hanya kolom NON-identitas: user_hash, member_hash, customer_id, order_ref, product_id TIDAK di-grant,
-- sehingga bypass guard (SEC-04) pun tidak bisa membaca identitas. Alternatif setelah diverifikasi di 24.8:
-- `CREATE VIEW ... SQL SECURITY DEFINER` lalu cabut grant kolom tabel dasar.
GRANT SELECT(report_id, reported_at, product_name, product_sku, competitor_name, price, extracted_price,
             ocr_confidence, geo_lat, geo_lng, status, reason, updated_at) ON efn_dwh.competitor_prices TO text2sql_reader;
-- BELUM DIVERIFIKASI: FINAL mungkin juga memerlukan hak atas kolom sorting key (member_hash). Bila query view
-- gagal ACCESS_DENIED, pilih SQL SECURITY DEFINER, BUKAN grant member_hash.
GRANT SELECT(created_at, company_code, source, reason, delta_points, event_id) ON efn_dwh.loyalty_events TO text2sql_reader;
GRANT SELECT(order_date, company_id, brand, store_id, category, qty, subtotal, tax_amount, cost_total,
             line_id, write_date) ON efn_dwh.fact_sales_line TO text2sql_reader;

-- Kuota per user (DoS / biaya): dihitung per jam.
CREATE QUOTA IF NOT EXISTS text2sql_quota KEYED BY user_name
    FOR INTERVAL 1 hour MAX queries = 300, errors = 100, result_rows = 300000, read_rows = 5000000000,
        execution_time = 1800
    TO text2sql_reader;

GRANT text2sql_reader TO efn_text2sql;
SET DEFAULT ROLE text2sql_reader TO efn_text2sql;
ALTER USER efn_text2sql SETTINGS PROFILE 'text2sql_readonly';
