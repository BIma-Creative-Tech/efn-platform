-- Hak user ClickHouse `efn_etl` (services/etl). Kontrak: docs/04 §7.3 — INSERT/SELECT mart + etl_watermark,
-- ditambah §8.4 (Gelombang 5): ALTER DELETE pada tabel mart yang memuat hash member, untuk erasure DSAR.
-- Dijalankan saat deploy (idempoten) oleh clickhouse-init.sh SETELAH user dibuat dari secret `clickhouse_password_etl`.
-- ETL tidak butuh CREATE/DROP/ALTER kolom, OPTIMIZE, TRUNCATE, maupun akses database lain.
--
-- allow_ddl: ClickHouse memperlakukan ALTER (termasuk ALTER DELETE dan lightweight DELETE) sebagai DDL, sehingga
-- `allow_ddl = 0` akan memblokir erasure. Profil kini `allow_ddl = 1 CONST`; pembatas nyata = GRANT di bawah (role hanya
-- punya ALTER DELETE pada dua tabel, tanpa CREATE/DROP/ALTER TABLE lain). Bila kebijakan menghendaki allow_ddl=0,
-- alternatifnya user terpisah `efn_etl_erasure` (secret baru) — lihat laporan efn-data-ai-engineer Gelombang 5.

CREATE SETTINGS PROFILE IF NOT EXISTS etl_writer_profile SETTINGS
    allow_ddl = 1 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 900,
    max_memory_usage = 4000000000 MAX 8000000000;
-- Profil yang sudah ada dari deploy sebelumnya (CREATE ... IF NOT EXISTS tidak mengubahnya).
ALTER SETTINGS PROFILE etl_writer_profile SETTINGS
    allow_ddl = 1 CONST,
    allow_introspection_functions = 0 CONST,
    max_execution_time = 300 MAX 900,
    max_memory_usage = 4000000000 MAX 8000000000;

CREATE ROLE IF NOT EXISTS etl_writer;
GRANT SELECT, INSERT ON efn_dwh.competitor_prices TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.loyalty_events TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_sales_line TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.etl_watermark TO etl_writer;
-- Erasure DSAR (docs/04 §8.4): hanya tabel dengan kolom user_hash/member_hash (services/etl/efn_etl/erasure.py
-- ERASURE_TARGETS). `mutations_sync` disetel per query oleh ETL.
GRANT ALTER DELETE ON efn_dwh.competitor_prices TO etl_writer;
GRANT ALTER DELETE ON efn_dwh.loyalty_events TO etl_writer;

GRANT etl_writer TO efn_etl;
SET DEFAULT ROLE etl_writer TO efn_etl;
ALTER USER efn_etl SETTINGS PROFILE 'etl_writer_profile';
