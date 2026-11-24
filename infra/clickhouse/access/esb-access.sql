-- Hak user ClickHouse `efn_esb` (services/esb-extract, G41, docs/04 §25.7). Dijalankan clickhouse-init.sh (idempoten)
-- SETELAH user dibuat dari secret `clickhouse_password_esb`; pengikatan role -> user dilakukan di clickhouse-init.sh.
-- Role `esb_writer`: SELECT + INSERT pada efn_esb.* dan efn_dwh.*esb* SAJA. Tanpa CREATE/DROP/OPTIMIZE dan tanpa akses
-- tabel non-ESB (mart Odoo/platform, salinan audit). DDL skema (CREATE/DROP, engine, storage policy) hanya oleh
-- clickhouse-init (admin).
-- Pengecualian (tabel agregat Insight, 22 Nov 2026, init/20-esb-aggregates.sql, efn_esb/aggregates.py; SEC-AG-01):
--   build `efn_dwh.agg_esb_*_build` : SELECT, INSERT, TRUNCATE        (TRUNCATE TABLE build; INSERT bulan dari view)
--   agg   `efn_dwh.agg_esb_*`       : SELECT, INSERT, ALTER DELETE    (ALTER TABLE agg REPLACE PARTITION ID .. FROM build
--                                     = SELECT pada sumber + ALTER DELETE|INSERT pada tujuan; DROP PARTITION = ALTER
--                                     DELETE -- InterpreterAlterQuery::getRequiredAccessForCommand, ClickHouse 24.8)
-- TANPA CREATE TABLE / DROP TABLE: engine & storage policy tidak bisa dipilih esb_writer (temuan SEC-AG-01: CREATE tingkat
-- tabel mengizinkan ENGINE = Memory/Log dan SETTINGS storage_policy = 'default' tanpa enkripsi). allow_ddl tetap 1:
-- TRUNCATE dan ALTER termasuk kelompok DDL yang ditolak allow_ddl=0 walau di-grant; batas efektif = grant per tabel.
-- Data agregat = turunan view (dibangun ulang), jadi ALTER DELETE/TRUNCATE tidak menghilangkan data sumber.

CREATE SETTINGS PROFILE IF NOT EXISTS esb_writer_profile SETTINGS
    readonly = 0 CONST,
    allow_ddl = 1 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 900,
    max_memory_usage = 2000000000 MAX 4000000000;
-- Profil dari deploy sebelumnya (CREATE ... IF NOT EXISTS tidak mengubahnya).
ALTER SETTINGS PROFILE esb_writer_profile SETTINGS
    readonly = 0 CONST,
    allow_ddl = 1 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 900,
    max_memory_usage = 2000000000 MAX 4000000000;

CREATE ROLE IF NOT EXISTS esb_writer;
-- efn_esb: raw + kendali.
GRANT SELECT, INSERT ON efn_esb.raw_doc TO esb_writer;
GRANT SELECT, INSERT ON efn_esb.extract_checkpoint TO esb_writer;
GRANT SELECT, INSERT ON efn_esb.extract_run TO esb_writer;
GRANT SELECT, INSERT ON efn_esb.bom_ratio TO esb_writer;
-- efn_dwh: mart ESB (ReplacingMergeTree, idempoten lewat _version).
GRANT SELECT, INSERT ON efn_dwh.dim_esb_branch TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_product TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_menu TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_product_alias TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_supplier TO esb_writer;
-- map_esb_odoo: TIDAK PERNAH di-grant ke esb_writer (SEC-188, docs/04 §27.10 L-130); penulis tunggal = user efn_mig
-- (mig-access.sql). REVOKE = pertahanan berlapis bila grant lama pernah ada.
REVOKE ALL ON efn_dwh.map_esb_odoo FROM esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_sales_line TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_payment_daily TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_fin_sold_daily TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_production TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_delivery TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_transfer TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_stock_snapshot TO esb_writer;
-- G43 (14-g43-migration.sql, `python -m efn_esb snapshot`). Tidak di-grant ke metabase_reader (data keuangan/partner).
GRANT SELECT, INSERT ON efn_dwh.dim_esb_bom TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_bom_line TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_menu_price TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_customer TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_location TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_esb_coa TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_stock_opname TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_ap_open TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_ar_open TO esb_writer;
-- G65 (23-g65-esb-purchase-stock.sql, docs/04 §27.13.3, L-258). Hanya esb_writer + mig_rw (mig-access.sql); tidak ke
-- metabase_reader / Insight / forecast. Kolom baru dim_esb_supplier.supplier_id tidak butuh grant baru.
GRANT SELECT, INSERT ON efn_dwh.fact_esb_purchase_line TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_stock_opening TO esb_writer;

-- Tabel agregat Insight (init/20-esb-aggregates.sql). Sumber refresh = 3 view (INVOKER: tabel dasarnya sudah di-grant
-- SELECT di atas). aggregate_refresh = catatan kesegaran; aggregate_refresh_lock = kunci refresh (SEC-AG-02).
GRANT SELECT, INSERT ON efn_esb.aggregate_refresh TO esb_writer;
GRANT SELECT, INSERT ON efn_esb.aggregate_refresh_lock TO esb_writer;
GRANT SELECT ON efn_dwh.v_esb_sales_branch_daily TO esb_writer;
GRANT SELECT ON efn_dwh.v_esb_sales_daily TO esb_writer;
GRANT SELECT ON efn_dwh.v_esb_flow_daily TO esb_writer;
-- Grant deploy sebelumnya (desain EXCHANGE): GRANT yang dihapus dari file ini TIDAK dicabut otomatis -> REVOKE eksplisit.
REVOKE CREATE TABLE, DROP TABLE ON efn_dwh.agg_esb_sales_branch_daily FROM esb_writer;
REVOKE CREATE TABLE, DROP TABLE ON efn_dwh.agg_esb_sales_branch_daily_build FROM esb_writer;
REVOKE CREATE TABLE, DROP TABLE ON efn_dwh.agg_esb_sales_daily FROM esb_writer;
REVOKE CREATE TABLE, DROP TABLE ON efn_dwh.agg_esb_sales_daily_build FROM esb_writer;
REVOKE CREATE TABLE, DROP TABLE ON efn_dwh.agg_esb_flow_daily FROM esb_writer;
REVOKE CREATE TABLE, DROP TABLE ON efn_dwh.agg_esb_flow_daily_build FROM esb_writer;
GRANT SELECT, INSERT, ALTER DELETE ON efn_dwh.agg_esb_sales_branch_daily TO esb_writer;
GRANT SELECT, INSERT, TRUNCATE ON efn_dwh.agg_esb_sales_branch_daily_build TO esb_writer;
GRANT SELECT, INSERT, ALTER DELETE ON efn_dwh.agg_esb_sales_daily TO esb_writer;
GRANT SELECT, INSERT, TRUNCATE ON efn_dwh.agg_esb_sales_daily_build TO esb_writer;
GRANT SELECT, INSERT, ALTER DELETE ON efn_dwh.agg_esb_flow_daily TO esb_writer;
GRANT SELECT, INSERT, TRUNCATE ON efn_dwh.agg_esb_flow_daily_build TO esb_writer;

-- Analis (opsional, §25.7): view v_esb_* + tabel dasarnya (view INVOKER; v_esb_data_quality membaca extract_run).
-- Tanpa raw_doc (payload mentah) dan bom_ratio.
-- metabase-access.sql berjalan SETELAH file ini (urutan alfabet) -> role dibuat di sini juga (idempoten).
CREATE ROLE IF NOT EXISTS metabase_reader;
GRANT SELECT ON efn_dwh.v_esb_sales_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_esb_payment_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_esb_flow_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.v_esb_menu_mix TO metabase_reader;
GRANT SELECT ON efn_dwh.v_esb_data_quality TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_esb_branch TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_esb_product TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_esb_menu TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_esb_product_alias TO metabase_reader;
GRANT SELECT ON efn_dwh.dim_esb_supplier TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_esb_sales_line TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_esb_payment_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_esb_fin_sold_daily TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_esb_production TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_esb_delivery TO metabase_reader;
GRANT SELECT ON efn_dwh.fact_esb_transfer TO metabase_reader;
GRANT SELECT ON efn_esb.extract_checkpoint TO metabase_reader;
GRANT SELECT ON efn_esb.extract_run TO metabase_reader;
