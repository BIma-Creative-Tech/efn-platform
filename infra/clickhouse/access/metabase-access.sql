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
-- Gelombang 12: konsolidasi GL (F-19). View INVOKER -> kolom tabel dasar juga di-grant (tanpa PII: partner tidak
-- disimpan di mart). Rekonsiliasi audit: hanya hitungan (audit_recon_run), BUKAN salinan log.
GRANT SELECT ON efn_dwh.v_gl_consolidated TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_gl_line TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_gl_account TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_company TO metabase_reader;
GRANT SELECT ON efn_dwh.audit_recon_run TO metabase_reader;

-- Mart: semua kolom KECUALI identitas pseudonim (user_hash, member_hash, customer_id). Analisis per member
-- (mis. kohort) butuh keputusan DPO dan grant terpisah, bukan default dashboard.
GRANT SELECT(report_id, reported_at, product_name, product_sku, competitor_name, price, extracted_price, status,
             reason, ocr_confidence, geo_lat, geo_lng, updated_at) ON efn_dwh.competitor_prices TO metabase_reader;
GRANT SELECT(event_id, company_code, delta_points, reason, source, source_ref, created_at)
    ON efn_dwh.loyalty_events TO metabase_reader;
GRANT SELECT(line_id, source, company_id, company_name, brand, store_id, order_ref, order_date, product_id,
             product_name, category, qty, price_unit, discount_pct, subtotal, tax_amount, cost_total, write_date)
    ON efn_dwh.fact_sales_line TO metabase_reader;

-- Gelombang 13 (Fase 1): view dashboard "EFN Fase 1" (infra/metabase/dashboards/*.json). View INVOKER -> tabel dasar
-- juga di-grant. Tabel baru tidak memuat identitas (tanpa partner/kartu/hash), jadi grant tabel penuh.
GRANT SELECT ON efn_dwh.v_pos_sales_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_pos_outlet_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_pos_sales_hourly TO metabase_reader;
GRANT SELECT ON efn_dwh.v_pos_payment_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_stock_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_stock_current TO metabase_reader;
GRANT SELECT ON efn_dwh.v_stock_expiring TO metabase_reader;
GRANT SELECT ON efn_dwh.v_stock_below_reorder TO metabase_reader;
GRANT SELECT ON efn_dwh.v_price_index TO metabase_reader;
GRANT SELECT ON efn_dwh.v_price_hunter_match_rate TO metabase_reader;
GRANT SELECT ON efn_dwh.v_etl_freshness TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_pos_payment TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_outlet TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_product TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_stock_snapshot TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_orderpoint_snapshot TO metabase_reader;
GRANT SELECT ON efn_dwh.stock_snapshot_run TO metabase_reader;
GRANT SELECT ON efn_dwh.etl_job_run TO metabase_reader;
-- Gelombang 14: rekonsiliasi loyalty (hitungan + id order Odoo sampel; tanpa member/partner) dan ringkasan
-- rekonsiliasi audit (hitungan saja).
GRANT SELECT ON efn_dwh.v_loyalty_recon_latest TO metabase_reader;
GRANT SELECT ON efn_dwh.v_loyalty_recon_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_audit_recon_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.loyalty_recon_run TO metabase_reader;
GRANT SELECT ON efn_dwh.loyalty_recon_finding TO metabase_reader;
-- Kolom baru fact_sales_line (tetap TANPA customer_id).
GRANT SELECT(order_id, is_refund, line_kind, discount_amount) ON efn_dwh.fact_sales_line TO metabase_reader;
-- Gelombang 18 (K-18-6): rekonsiliasi harian pembayaran Poin EFN per PT (nominal agregat, tanpa member/partner).
GRANT SELECT ON efn_dwh.v_loyalty_points_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.loyalty_points_daily TO metabase_reader;
-- Gelombang 20 (W-32): rekonsiliasi eliminasi antar-entitas per pasangan (agregat GL, tanpa partner). Kolom baru
-- dim_company (business_type, brand_group) sudah tercakup grant tabel penuh di atas.
GRANT SELECT ON efn_dwh.v_gl_ic_recon TO metabase_reader;
-- Gelombang 21 (ADR-036 §0.7): penjualan per wilayah domisili. Kode kab/kota tanpa customer_id (tetap tidak di-grant).
GRANT SELECT ON efn_dwh.v_pos_sales_region_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_region TO metabase_reader;
GRANT SELECT(customer_region_code) ON efn_dwh.fact_sales_line TO metabase_reader;
-- Gelombang 22: KPI dapur KDS (tanpa label meja/catatan/pelanggan -> grant tabel penuh) dan rekonsiliasi poin per
-- kanal + total POS+online (nominal agregat per PT, tanpa member/partner/part_id).
GRANT SELECT ON efn_dwh.v_kds_ticket_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_kds_ticket_hourly TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_kds_ticket TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_kds_station TO metabase_reader;
GRANT SELECT ON efn_dwh.v_loyalty_points_daily_channel TO metabase_reader;
GRANT SELECT ON efn_dwh.v_loyalty_points_daily_total TO metabase_reader;
-- Gelombang 32 (K25-08): waste & yield CK. Tanpa partner/user/dokumen -> view + tabel dasar penuh (view INVOKER).
GRANT SELECT ON efn_dwh.v_waste_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_waste_warehouse_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_mo_yield TO metabase_reader;
GRANT SELECT ON efn_dwh.v_mo_yield_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_mo_component_variance TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_scrap TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_scrap_reason TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_mo_yield TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_mo_component TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_ck_threshold TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_warehouse TO metabase_reader;

GRANT metabase_reader TO efn_metabase;
SET DEFAULT ROLE metabase_reader TO efn_metabase;
ALTER USER efn_metabase SETTINGS PROFILE 'metabase_readonly';
