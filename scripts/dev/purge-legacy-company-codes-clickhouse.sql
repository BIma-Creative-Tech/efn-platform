-- DEV SAJA: pasangan ClickHouse untuk scripts/dev/purge-legacy-company-codes.sql. Membuang baris mart berkode PT lama
-- (RTL/FNB, sebelum cutover G20 §12.1) yang masih tersisa setelah DB BFF/Odoo dev dibersihkan.
-- Jalankan sebagai admin ClickHouse (bukan etl_writer/metabase: keduanya sengaja tanpa hak DELETE), contoh:
--   clickhouse-client --config-file <admin.xml> --multiquery < scripts/dev/purge-legacy-company-codes-clickhouse.sql
-- Mutasi sinkron (mutations_sync=2). JANGAN untuk staging/produksi (tidak ada kode lama di sana).
SELECT 'sebelum', 'loyalty_events', count() FROM efn_dwh.loyalty_events WHERE company_code IN ('RTL', 'FNB');
ALTER TABLE efn_dwh.loyalty_events DELETE WHERE company_code IN ('RTL', 'FNB') SETTINGS mutations_sync = 2;
ALTER TABLE efn_dwh.loyalty_points_daily DELETE WHERE company_code IN ('RTL', 'FNB') SETTINGS mutations_sync = 2;
ALTER TABLE efn_dwh.loyalty_recon_finding DELETE WHERE company_code IN ('RTL', 'FNB') SETTINGS mutations_sync = 2;
ALTER TABLE efn_dwh.fact_sales_line DELETE WHERE brand IN ('RTL', 'FNB') SETTINGS mutations_sync = 2;
SELECT 'sisa', 'loyalty_events', count() FROM efn_dwh.loyalty_events WHERE company_code IN ('RTL', 'FNB');
SELECT 'sisa', 'loyalty_points_daily', count() FROM efn_dwh.loyalty_points_daily WHERE company_code IN ('RTL', 'FNB');
SELECT 'sisa', 'loyalty_recon_finding', count() FROM efn_dwh.loyalty_recon_finding WHERE company_code IN ('RTL', 'FNB');
SELECT 'sisa', 'fact_sales_line', count() FROM efn_dwh.fact_sales_line WHERE brand IN ('RTL', 'FNB');
