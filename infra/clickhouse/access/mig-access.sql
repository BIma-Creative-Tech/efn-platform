-- Hak user ClickHouse `efn_mig` (mesin migrasi ESB -> Odoo, scripts/migration/esb_to_odoo.py; G43, docs/04 §27.10,
-- ADR-042). Dijalankan clickhouse-init.sh (idempoten) setelah init/*.sql. User dibuat clickhouse-init.sh HANYA bila
-- secret `clickhouse_password_mig` ada dan bukan placeholder (selain itu DROP USER IF EXISTS efn_mig); pengikatan
-- role/profil ke user juga di clickhouse-init.sh. Role & profil di file ini dibuat selalu (tanpa user = tanpa efek).
--
-- Role `mig_rw`: SELECT per objek ESB yang dibaca export/reconcile + SELECT, INSERT HANYA efn_dwh.map_esb_odoo.
-- Tanpa efn_esb.raw_doc (payload mentah), fact_esb_stock_snapshot (tidak dipakai migrasi, §27.6), mart Odoo/platform,
-- GL, salinan audit. Tanpa DDL/ALTER/DROP/TRUNCATE/OPTIMIZE (allow_ddl=0 CONST + tidak ada grant).
-- View efn_dwh.v_esb_* bertipe INVOKER -> tabel dasarnya ikut di-grant (daftar = tabel yang dirujuk view; diuji
-- services/esb-extract/tests/test_mig_access.py).

CREATE SETTINGS PROFILE IF NOT EXISTS mig_profile SETTINGS
    readonly = 0 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 600 MAX 1800,
    max_memory_usage = 4000000000 MAX 4000000000;
-- Profil dari deploy sebelumnya (CREATE ... IF NOT EXISTS tidak mengubahnya) -- pola esb-access.sql.
ALTER SETTINGS PROFILE mig_profile SETTINGS
    readonly = 0 CONST,
    allow_ddl = 0 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 600 MAX 1800,
    max_memory_usage = 4000000000 MAX 4000000000;

CREATE ROLE IF NOT EXISTS mig_rw;
-- Pertahanan berlapis: cabut sisa hak luas (mis. eksperimen manual) sebelum grant per objek.
REVOKE ALL ON efn_dwh.* FROM mig_rw;
REVOKE ALL ON efn_esb.* FROM mig_rw;

-- Dimensi & fakta G41.
GRANT SELECT ON efn_dwh.dim_esb_branch TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_product TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_menu TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_product_alias TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_supplier TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_sales_line TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_payment_daily TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_fin_sold_daily TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_production TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_delivery TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_transfer TO mig_rw;
-- View semantic G41 (INVOKER).
GRANT SELECT ON efn_dwh.v_esb_sales_daily TO mig_rw;
GRANT SELECT ON efn_dwh.v_esb_payment_daily TO mig_rw;
GRANT SELECT ON efn_dwh.v_esb_flow_daily TO mig_rw;
GRANT SELECT ON efn_dwh.v_esb_data_quality TO mig_rw;
-- Kendali (dirujuk view + snapshot versi). Tanpa raw_doc.
GRANT SELECT ON efn_esb.extract_checkpoint TO mig_rw;
GRANT SELECT ON efn_esb.extract_run TO mig_rw;
GRANT SELECT ON efn_esb.bom_ratio TO mig_rw;
-- Tabel G43 (14-g43-migration.sql).
GRANT SELECT ON efn_dwh.dim_esb_bom TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_bom_line TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_menu_price TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_customer TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_location TO mig_rw;
GRANT SELECT ON efn_dwh.dim_esb_coa TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_stock_opname TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_ap_open TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_ar_open TO mig_rw;
-- Tabel G65 (23-g65-esb-purchase-stock.sql, docs/04 §27.13.3) + view cek kualitas G65 (INVOKER; tabel dasarnya
-- fact_esb_purchase_line, fact_esb_stock_opening, dim_esb_branch, extract_checkpoint sudah di-grant).
GRANT SELECT ON efn_dwh.fact_esb_purchase_line TO mig_rw;
GRANT SELECT ON efn_dwh.fact_esb_stock_opening TO mig_rw;
GRANT SELECT ON efn_dwh.v_esb_g65_quality TO mig_rw;
-- Satu-satunya tabel yang boleh ditulis (writeback hasil impor; ReplacingMergeTree -> ulang = idempoten).
GRANT SELECT, INSERT ON efn_dwh.map_esb_odoo TO mig_rw;
