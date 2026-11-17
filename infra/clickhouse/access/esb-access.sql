-- Hak user ClickHouse `efn_esb` (services/esb-extract, G41, docs/04 §25.7). Dijalankan clickhouse-init.sh (idempoten)
-- SETELAH user dibuat dari secret `clickhouse_password_esb`; pengikatan role -> user dilakukan di clickhouse-init.sh.
-- Role `esb_writer`: SELECT + INSERT pada efn_esb.* dan efn_dwh.*esb* SAJA. Tanpa DDL/ALTER/DROP/TRUNCATE/OPTIMIZE dan
-- tanpa akses tabel non-ESB (mart Odoo/platform, salinan audit). DDL hanya oleh clickhouse-init (admin).

CREATE SETTINGS PROFILE IF NOT EXISTS esb_writer_profile SETTINGS
    readonly = 0 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 900,
    max_memory_usage = 2000000000 MAX 4000000000;
-- Profil dari deploy sebelumnya (CREATE ... IF NOT EXISTS tidak mengubahnya).
ALTER SETTINGS PROFILE esb_writer_profile SETTINGS
    readonly = 0 CONST,
    allow_ddl = 0 CONST,
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
-- map_esb_odoo: TANPA grant di G41 (SEC-188); penulisnya = migrasi G43, grant ditambah di gelombang itu.
GRANT SELECT, INSERT ON efn_dwh.fact_esb_sales_line TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_payment_daily TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_fin_sold_daily TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_production TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_delivery TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_transfer TO esb_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_esb_stock_snapshot TO esb_writer;

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
