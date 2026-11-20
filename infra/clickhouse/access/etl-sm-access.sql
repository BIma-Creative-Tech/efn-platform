-- G47 (docs/04 §28.13.8): hak tambahan user ClickHouse `efn_etl` (role etl_writer dibuat etl-access.sql, dijalankan
-- lebih dulu karena urutan abjad). Hanya SELECT + INSERT pada mart supermarket (ReplacingMergeTree / MergeTree append-only)
-- -- tanpa ALTER DELETE/UPDATE, TRUNCATE, DROP, OPTIMIZE. INSERT ... SELECT agregat (agg_sm_stock_daily,
-- agg_sm_expiry_daily) membaca fact_stock_snapshot/fact_orderpoint_snapshot/stock_snapshot_run/fact_sales_line/dim_outlet/
-- dim_product (sudah di-grant etl-access.sql) + dim_sm_warehouse/fact_lot_expiry + view v_sm_unit_cost (INVOKER) di bawah.
GRANT SELECT, INSERT ON efn_dwh.dim_sm_warehouse TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_online_outlet TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_sm_kpi_config TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_sm_supplier TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_replenishment TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_replenishment_bias TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_dc_fill_rate TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_dc_purchase_line TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.agg_sm_stock_daily TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_lot_expiry TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_lot_markdown TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.agg_sm_expiry_daily TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_campaign_result TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_sm_segment_run TO etl_writer;
GRANT SELECT ON efn_dwh.v_sm_unit_cost TO etl_writer;
-- Defensif (idempoten): log bias append-only tidak boleh diubah/dihapus lewat kredensial ETL.
REVOKE ALTER DELETE, ALTER UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE ON efn_dwh.fact_replenishment_bias FROM etl_writer;
