-- Akses read-only Dashboard Insight (G42 bagian B; ADR-041 Proposed, ADR-040 Proposed, K25-02, SEC-04).
-- User ClickHouse `efn_insight` (service insight-web) HANYA membaca database `efn_insight`: view SQL SECURITY DEFINER
-- di atas efn_dwh.v_esb_* dengan kolom eksplisit. Row policy `efn_insight_scope` (G52 §28.15.7: company_code + tanda
-- tangan sig3 = SHA256(kunci|cids|codes|brs), kelas PT atau BR (cabang), kunci = secret `insight_scope_key`) memuat
-- kunci rahasia -> dibuat clickhouse-init.sh lewat access/insight-esb-policy.sh, BUKAN di file ini.
-- clickhouse-init.sh juga membuat user dari secret `clickhouse_password_insight` dan mengikat role/profile ke user.
-- Dijalankan clickhouse-init.sh setiap deploy (idempoten) SETELAH init/*.sql (view 12-g41-esb.sql + 13-g42-insight.sql).
-- Butuh user admin dengan access management (bukan user aplikasi).

-- readonly=2: tidak bisa menulis/DDL/mengubah readonly, tetapi boleh SETTINGS per query (insight-web mengirim
-- SQL_efn_cids/SQL_efn_codes/SQL_efn_brs/SQL_efn_sig3 (G52) + setting SM + param_*). Batas atas dikunci MAX sehingga tidak bisa dinaikkan klien.
-- result_overflow_mode = 'throw': ekspor CSV yang melebihi batas gagal jelas, bukan terpotong diam-diam.
CREATE SETTINGS PROFILE IF NOT EXISTS insight_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 30 MAX 60,
    max_result_rows = 200000 MAX 200000,
    result_overflow_mode = 'throw',
    max_rows_to_read = 500000000 MAX 500000000,
    max_memory_usage = 2000000000 MAX 4000000000,
    -- 24: SATU user dipakai bersama semua staf; insight-web membatasi 12 query in-flight per proses (semaphore,
    -- INSIGHT_CH_MAX_CONCURRENCY) sehingga ekspor CSV tetap muat. 8 (awal) gagal di uji E2E G42 (galat 202).
    max_concurrent_queries_for_user = 24 MAX 24;
-- Profil dari deploy sebelumnya (CREATE ... IF NOT EXISTS tidak mengubahnya) -- pola esb-access.sql.
ALTER SETTINGS PROFILE insight_readonly SETTINGS
    readonly = 2 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 30 MAX 60,
    max_result_rows = 200000 MAX 200000,
    result_overflow_mode = 'throw',
    max_rows_to_read = 500000000 MAX 500000000,
    max_memory_usage = 2000000000 MAX 4000000000,
    -- 24: SATU user dipakai bersama semua staf; insight-web membatasi 12 query in-flight per proses (semaphore,
    -- INSIGHT_CH_MAX_CONCURRENCY) sehingga ekspor CSV tetap muat. 8 (awal) gagal di uji E2E G42 (galat 202).
    max_concurrent_queries_for_user = 24 MAX 24;

CREATE ROLE IF NOT EXISTS insight_reader;

-- Pertahanan berlapis: role ini tidak pernah punya hak di efn_dwh / efn_esb (hanya efn_insight).
REVOKE ALL ON efn_dwh.* FROM insight_reader;
REVOKE ALL ON efn_esb.* FROM insight_reader;

-- Definer view efn_insight: tidak bisa login (HOST NONE, tanpa password). Hak MINIMUM: view v_esb_* yang dibaca +
-- tabel dasarnya (view efn_dwh.v_esb_* bertipe INVOKER -> dieksekusi dengan hak definer ini). Lebih sempit dari pola
-- text2sql (efn_dwh.*): tanpa mart Odoo/GL/audit, tanpa efn_esb.raw_doc / bom_ratio.
-- Daftar ini diuji test_insight_access.py terhadap semua tabel yang dirujuk view (rekursif).
CREATE USER IF NOT EXISTS efn_insight_definer NOT IDENTIFIED HOST NONE;
GRANT SELECT ON efn_dwh.v_esb_payment_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_flow_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_data_quality TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_data_quality_pt TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_branch_list TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_product_list TO efn_insight_definer;
-- Review Insight 21 Nov 2026 (init/19-insight-review.sql): tabel dasarnya sudah ada di daftar ini.
GRANT SELECT ON efn_dwh.v_esb_movement_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_coverage TO efn_insight_definer;
-- G52 (init/21-g52-insight-fnb.sql, §28.15.8): tabel/view dasarnya (v_esb_movement_daily, dim_esb_branch) sudah di sini.
GRANT SELECT ON efn_dwh.v_esb_store_receipt_daily TO efn_insight_definer;
-- Insight YoY 24 Nov 2026 (init/23-insight-yoy.sql): tabel dasarnya sudah ada di daftar ini.
GRANT SELECT ON efn_dwh.v_esb_sales_done_day TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_menu_econ_daily TO efn_insight_definer;
-- G68 (init/25-g68-esb-pl-waste-fillrate.sql, docs/04 §32.7): lima view kelas PT + view internal yang dirujuk
-- (v_esb_item_journal_line, v_esb_fill_rate_line; TIDAK dibungkus efn_insight) + tabel dasarnya. v_esb_g68_quality
-- tidak di-grant (admin/lead saja).
GRANT SELECT ON efn_dwh.v_esb_pl_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_item_usage_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_item_usage_product_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_fill_rate_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_fill_rate_product_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_item_journal_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_fill_rate_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_profit_loss TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_item_journal_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_transfer_request_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_delivery_ref_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_esb_product_id TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_esb_branch TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_esb_product TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_esb_menu TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_esb_product_alias TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_sales_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_payment_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_fin_sold_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_production TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_delivery TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_transfer TO efn_insight_definer;
GRANT SELECT ON efn_esb.extract_checkpoint TO efn_insight_definer;
GRANT SELECT ON efn_esb.extract_run TO efn_insight_definer;
-- G53 (init/22-g53-insight-fnb-forecast.sql): forecast F&B; fact_forecast_daily/dim_forecast_run/dim_product juga di
-- insight-sm-access.sql (grant ganda aman, file ini berdiri sendiri).
GRANT SELECT ON efn_dwh.v_esb_forecast_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_forecast_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_forecast_run TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_fc_warehouse TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_product TO efn_insight_definer;
-- Tabel agregat harian (22 Nov 2026, init/20-esb-aggregates.sql): sumber 3 wrapper di bawah + v_esb_menu_mix_agg
-- (bauran menu di atas agg_esb_sales_daily). v_esb_flow_daily tetap di-grant (dibaca v_esb_data_quality). Objek yang
-- tidak lagi dirujuk wrapper mana pun dicabut (grant per objek = penutupan rujukan, diuji test_insight_access.py):
-- v_esb_sales_branch_daily, v_esb_sales_daily, v_esb_menu_mix. dim_esb_menu + fact_esb_sales_line tetap (dibaca
-- v_esb_menu_econ_daily, init/23-insight-yoy.sql).
GRANT SELECT ON efn_dwh.agg_esb_sales_branch_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_esb_sales_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.agg_esb_flow_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_menu_mix_agg TO efn_insight_definer;
REVOKE SELECT ON efn_dwh.v_esb_sales_branch_daily FROM efn_insight_definer;
REVOKE SELECT ON efn_dwh.v_esb_sales_daily FROM efn_insight_definer;
REVOKE SELECT ON efn_dwh.v_esb_menu_mix FROM efn_insight_definer;
-- G67 "Margin bahan" (init/24-margin-bahan.sql, docs/04 §31.4): 3 view versi terakhir + tabel dasarnya. Data komersial
-- sensitif (L-260): wrapper kelas PT, insight-web membatasi ke persona exec; tidak di-grant ke role lain.
GRANT SELECT ON efn_dwh.v_esb_ing_price_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_menu_cost_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_ing_group_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.mart_esb_ing_price_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.mart_esb_menu_cost_monthly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.mart_esb_ing_group_monthly TO efn_insight_definer;
-- G69 ESB OMS (init/26-g69-esb-oms.sql, docs/04 §33.5): view + tabel dasarnya (penutupan rujukan, diuji
-- test_insight_access.py). Pemetaan FIN dibaca dari tabel turunan dim_esb_oms_menu_fin (qty FIN per menu saja), BUKAN
-- dari tabel resep G43 (memuat HPP; tidak pernah di-grant ke Insight). raw_doc tidak di-grant.
GRANT SELECT ON efn_dwh.v_esb_oms_bill TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_hourly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_menu_hourly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_menu_fin TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_fin_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_fin_hourly TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_fin_mapping_quality TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_recon_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.v_esb_oms_freshness TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_oms_bill TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_oms_bill_line TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_oms_branch_daily TO efn_insight_definer;
GRANT SELECT ON efn_dwh.dim_esb_oms_menu_fin TO efn_insight_definer;
-- Cadangan pembayaran OMS (init/27-esb-payment-oms-fallback.sql, docs/04 §33.5a): view gabungan Core+OMS + tabel
-- pembayaran OMS (hanya tipe/metode/jumlah; nomor kartu/rekening dibuang sebelum mart, SEC-OMS-02). Hanya definer;
-- wrapper efn_insight.v_esb_payment_daily / v_esb_store_payment_daily membaca view gabungan, bukan tabel ini.
GRANT SELECT ON efn_dwh.v_esb_payment_daily_fb TO efn_insight_definer;
GRANT SELECT ON efn_dwh.fact_esb_oms_bill_payment TO efn_insight_definer;

CREATE DATABASE IF NOT EXISTS efn_insight;

-- Semua view punya kolom company_code (target row policy). Kolom = katalog docs/design/insight-web-queries.md.
-- Tanpa kolom identitas (tidak ada di mart ESB: L-110).
-- v_esb_sales_daily, v_esb_sales_branch_daily, v_esb_flow_daily membaca TABEL AGREGAT efn_dwh.agg_esb_* (materialisasi
-- view efn_dwh.v_esb_* dengan nama yang sama, kolom identik; dibangun ulang `python -m efn_esb refresh-aggregates`).
-- Row policy efn_insight_scope tetap dipasang pada view efn_insight ini (loop clickhouse-init.sh), bukan pada tabel.
CREATE OR REPLACE VIEW efn_insight.v_esb_sales_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_name, menu_code, menu_name, menu_category,
       menu_category_detail, is_takeaway, qty, gross, discount, net, cogs, trx_count
FROM efn_dwh.agg_esb_sales_daily;

-- KPI Transaksi / rata-rata per transaksi (trx_count unik per cabang-hari; Q-INS2).
CREATE OR REPLACE VIEW efn_insight.v_esb_sales_branch_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_name, trx_count, line_count, qty, gross, discount, net, cogs
FROM efn_dwh.agg_esb_sales_branch_daily;

-- `source` = core | oms (cadangan OMS bila Core gagal untuk hari itu; mdr/net baris oms = ESTIMASI, init/27).
CREATE OR REPLACE VIEW efn_insight.v_esb_payment_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, branch_name, payment_type, payment_method,
       payment_count, amount, mdr, net, source
FROM efn_dwh.v_esb_payment_daily_fb;

CREATE OR REPLACE VIEW efn_insight.v_esb_flow_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, brand, company_code, branch_id, branch_name, product_code, product_name, ck_prod, ck_out, in_ck,
       in_other, store_prod, st_in, st_out, sold
FROM efn_dwh.agg_esb_flow_daily;

-- Sumber = v_esb_menu_mix_agg (badan sama dengan efn_dwh.v_esb_menu_mix, di atas agg_esb_sales_daily; keluaran sama).
CREATE OR REPLACE VIEW efn_insight.v_esb_menu_mix DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT month, brand, company_code, branch_id, branch_name, menu_category, is_takeaway, qty, net, share_qty, share_net
FROM efn_dwh.v_esb_menu_mix_agg;

-- Sumber = v_esb_data_quality_pt (company_code terisi di setiap baris; cek per brand dipetakan ke PT brand itu).
CREATE OR REPLACE VIEW efn_insight.v_esb_data_quality DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT check_name, brand, company_code, item, value, last_day
FROM efn_dwh.v_esb_data_quality_pt;

CREATE OR REPLACE VIEW efn_insight.v_esb_branch_list DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, branch_id, branch_code, branch_name, is_ck, is_store, active
FROM efn_dwh.v_esb_branch_list;

CREATE OR REPLACE VIEW efn_insight.v_esb_product_list DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, product_code, product_name, category, uom, is_fin, active
FROM efn_dwh.v_esb_product_list;

-- Review Insight 21 Nov 2026: semua pergerakan produksi + kiriman (FIN dan non-FIN) per kelompok barang; company_code =
-- PT cabang asal. Rekonsiliasi alur CK (v_esb_flow_daily hanya FIN).
CREATE OR REPLACE VIEW efn_insight.v_esb_movement_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, brand, company_code, kind, origin_branch_id, origin_branch_name, origin_kind, dest_branch_id,
       dest_branch_name, dest_kind, item_group, category, product_code, product_name, uom, qty, lines
FROM efn_dwh.v_esb_movement_daily;

-- Review Insight 21 Nov 2026: rentang hari yang benar-benar tersedia per entitas (checkpoint selesai).
CREATE OR REPLACE VIEW efn_insight.v_esb_coverage DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, entity, first_day, last_day, days
FROM efn_dwh.v_esb_coverage;

-- G52 (docs/04 §28.15.8, L-244): empat view persona Toko F&B, row policy kelas BR (cabang `brs`). Tanpa cogs/mdr/net
-- pembayaran (Q-INS-F3). Sumber = sumber pasangan view PT (sales_branch / sales / payment): bila pasangan dialihkan ke
-- tabel agregat (20-esb-aggregates.sql), view toko WAJIB dialihkan bersama (test_insight_fnb_access.py).
CREATE OR REPLACE VIEW efn_insight.v_esb_store_sales_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_name, trx_count, qty, gross, discount, net
FROM efn_dwh.agg_esb_sales_branch_daily;

-- Tanpa trx_count: tidak aditif di grain menu (satu transaksi tiga menu = 3).
CREATE OR REPLACE VIEW efn_insight.v_esb_store_menu_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, menu_code, menu_name, menu_category, qty, gross, discount, net
FROM efn_dwh.agg_esb_sales_daily;

-- Baris branch_id = 0 (branchCode tak dikenal) dibuang: tidak pernah milik cabang mana pun (E7).
CREATE OR REPLACE VIEW efn_insight.v_esb_store_payment_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_name, payment_type, payment_method, payment_count, amount
FROM efn_dwh.v_esb_payment_daily_fb
WHERE branch_id != 0;

-- company_code = PT cabang TUJUAN, branch_id = cabang tujuan (init/21-g52-insight-fnb.sql).
CREATE OR REPLACE VIEW efn_insight.v_esb_store_receipt_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT day, brand, company_code, branch_id, branch_name, origin_branch_id, origin_branch_name, origin_kind, item_group,
       category, product_code, product_name, uom, qty, lines
FROM efn_dwh.v_esb_store_receipt_daily;
-- Insight YoY 24 Nov 2026 (init/23-insight-yoy.sql): pasangan cabang-hari yang slice penjualannya selesai (pembanding
-- sebanding) dan ekonomi menu per nama (berbayar / gratis / komponen paket / header paket).
CREATE OR REPLACE VIEW efn_insight.v_esb_sales_done_day DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, branch_id, day
FROM efn_dwh.v_esb_sales_done_day;

CREATE OR REPLACE VIEW efn_insight.v_esb_menu_econ_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, menu_name, menu_category, qty_paid, gross_paid, disc_paid, cogs_paid,
       qty_free, cogs_free, qty_pkg, cogs_pkg, pkg_qty, pkg_gross, pkg_disc
FROM efn_dwh.v_esb_menu_econ_daily;

-- Grant wildcard: view baru di efn_insight otomatis terbaca -> clickhouse-init.sh MEMVERIFIKASI setiap view efn_insight
-- punya row policy efn_insight_scope dan mencabut grant ini (fail closed) bila ada yang tidak.
GRANT SELECT ON efn_insight.* TO insight_reader;

-- Kuota per jam (DoS/biaya). KEYED BY user_name: semua staf berbagi satu user ClickHouse `efn_insight`, jadi kuota ini
-- adalah plafon service; rate-limit per staf = tanggung jawab insight-web (kuota per client_key = gelombang berikutnya).
CREATE QUOTA IF NOT EXISTS insight_quota KEYED BY user_name
    FOR INTERVAL 1 hour MAX queries = 20000, errors = 2000, result_rows = 50000000, read_rows = 100000000000,
        execution_time = 36000
    TO insight_reader;

-- G53 (init/22-g53-insight-fnb-forecast.sql): forecast F&B (run ok terbaru per PT), row policy kelas PT. Satuan qty.
CREATE OR REPLACE VIEW efn_insight.v_esb_forecast_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT company_code, brand, warehouse_id, warehouse_code, warehouse_name, product_id, product_code, product_name,
       category, forecast_date, yhat, p10, p90, model, generated_at
FROM efn_dwh.v_esb_forecast_daily;

-- G67 "Margin bahan" (docs/04 §31.4, init/24-margin-bahan.sql): versi refresh terakhir per brand, kolom = tabel mart
-- tanpa _version. Row policy kelas PT (insight-esb-policy.sh). company_code = PT brand (satu PT per brand).
CREATE OR REPLACE VIEW efn_insight.v_esb_ing_price_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, month, product_code, product_name, ing_group, uom, idx, usage_value
FROM efn_dwh.v_esb_ing_price_monthly;

CREATE OR REPLACE VIEW efn_insight.v_esb_menu_cost_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, month, menu_name, menu_category, qty, net, cogs, bom_matched, cost_idx, leaf_coverage
FROM efn_dwh.v_esb_menu_cost_monthly;

CREATE OR REPLACE VIEW efn_insight.v_esb_ing_group_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, month, ing_group, basket_value, base_value
FROM efn_dwh.v_esb_ing_group_monthly;

-- G68 (docs/04 §32.6-§32.7, L-277): laba rugi outlet, waste & pemakaian internal, fill rate outlet -> CK. Kelas PT
-- (sesi ber-cabang -> 0 baris). Kolom eksplisit = antarmuka insight-web (urutan & nama tetap).
CREATE OR REPLACE VIEW efn_insight.v_esb_pl_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT period_month, brand, company_code, scope, branch_id, branch_name, line_type, section, account_no, account_key,
       account_name, amount, is_partial
FROM efn_dwh.v_esb_pl_monthly;

CREATE OR REPLACE VIEW efn_insight.v_esb_item_usage_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT journal_date, brand, company_code, branch_id, branch_name, usage_class, purpose_name, docs, lines, value_out,
       value_in, value_net
FROM efn_dwh.v_esb_item_usage_daily;

CREATE OR REPLACE VIEW efn_insight.v_esb_item_usage_product_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT month, brand, company_code, branch_id, usage_class, purpose_name, product_code, product_name, uom, qty_out,
       value_net
FROM efn_dwh.v_esb_item_usage_product_monthly;

CREATE OR REPLACE VIEW efn_insight.v_esb_fill_rate_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT transfer_date, brand, company_code, branch_id, branch_name, supplier_branch_id, supplier_name, requests,
       requests_full, lines, lines_full, lines_zero, line_ratio_sum, lines_uom_mismatch, lines_open, lead_days_sum,
       lead_days_n
FROM efn_dwh.v_esb_fill_rate_daily;

CREATE OR REPLACE VIEW efn_insight.v_esb_fill_rate_product_monthly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT month, brand, company_code, branch_id, product_code, product_name, uom, lines, request_qty, sent_capped,
       qty_sent, qty_received
FROM efn_dwh.v_esb_fill_rate_product_monthly;

-- G69 ESB OMS (docs/04 §33.5/§33.7). Kelas BR (cabang `brs`, persona Toko F&B) untuk view ber-cabang tanpa data
-- pembanding keuangan Core; v_esb_oms_recon_daily kelas PT (memuat Σ pembayaran ESB Core). Tanpa identitas member
-- (hanya has_member 0/1), tanpa data kartu (§33.6). Kolom = keluaran view efn_dwh (urutan sama).
CREATE OR REPLACE VIEW efn_insight.v_esb_oms_bill DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, branch_name, sales_num, bill_num,
       parent_link_sales_num, sales_ts_in, sales_ts_out, hour, visit_purpose_id, visit_purpose_name, pax, subtotal,
       discount_total, menu_discount_total, promotion_discount, other_tax_total, vat_total, voucher_total,
       rounding_total, grand_total, payment_total, promotion_id, promotion_name, status_id, status_name, is_final,
       has_member
FROM efn_dwh.v_esb_oms_bill;

CREATE OR REPLACE VIEW efn_insight.v_esb_oms_hourly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, branch_name, hour, visit_purpose_name, bills, pax,
       gross_sales, net_sales, items
FROM efn_dwh.v_esb_oms_hourly;

CREATE OR REPLACE VIEW efn_insight.v_esb_oms_menu_hourly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, hour, line_kind, menu_code, menu_name, qty,
       gross_sales, discount, net_sales
FROM efn_dwh.v_esb_oms_menu_hourly;

CREATE OR REPLACE VIEW efn_insight.v_esb_oms_fin_hourly DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, hour, product_key, product_name, uom, qty_fin
FROM efn_dwh.v_esb_oms_fin_hourly;

CREATE OR REPLACE VIEW efn_insight.v_esb_oms_fin_mapping_quality DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, menu_qty, fin_mapped_qty, no_fin_qty, unmapped_qty,
       no_code_qty, unmapped_pct, qty_fin, core_fin_sold_qty
FROM efn_dwh.v_esb_oms_fin_mapping_quality;

CREATE OR REPLACE VIEW efn_insight.v_esb_oms_recon_daily DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT sales_date, brand, company_code, branch_id, branch_code, branch_name, bills_oms, grand_total_oms,
       bills_summary, grand_total_summary, core_payment_amount, diff_bills_pct, diff_summary_pct, diff_core_pct
FROM efn_dwh.v_esb_oms_recon_daily;

CREATE OR REPLACE VIEW efn_insight.v_esb_oms_freshness DEFINER = efn_insight_definer SQL SECURITY DEFINER AS
SELECT brand, company_code, branch_id, branch_code, max_sales_ts_out_today, last_finished_at
FROM efn_dwh.v_esb_oms_freshness;
