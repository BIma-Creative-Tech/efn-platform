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
-- Gelombang 12: mart GL (ReplacingMergeTree, idempoten lewat versi).
GRANT SELECT, INSERT ON efn_dwh.fact_gl_line TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_gl_account TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_company TO etl_writer;
-- Gelombang 13 (Fase 1): POS pembayaran/outlet/produk (ReplacingMergeTree) + snapshot stok + heartbeat job.
GRANT SELECT, INSERT ON efn_dwh.fact_pos_payment TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_outlet TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.dim_product TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_stock_snapshot TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.fact_orderpoint_snapshot TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.stock_snapshot_run TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.etl_job_run TO etl_writer;
-- Gelombang 14 (sisa SEC-61): hasil rekonsiliasi loyalty ledger <-> pos.order (append-only, tanpa PII).
GRANT SELECT, INSERT ON efn_dwh.loyalty_recon_run TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.loyalty_recon_finding TO etl_writer;
-- Gelombang 18 (K-18-6): rekonsiliasi harian pembayaran Poin EFN (append-only, tanpa PII).
GRANT SELECT, INSERT ON efn_dwh.loyalty_points_daily TO etl_writer;
-- Salinan audit tamper-evident: HANYA SELECT + INSERT. Sengaja TANPA ALTER DELETE/UPDATE, TRUNCATE, DROP, OPTIMIZE:
-- ETL (atau kredensial efn_etl yang bocor) tidak bisa menghapus/mengubah salinan; perubahan hanya oleh admin ClickHouse
-- (terdeteksi lewat kepala rantai di Loki). Bila suatu saat ada `GRANT ALL`/wildcard ke etl_writer, cabut dulu.
GRANT SELECT, INSERT ON efn_dwh.audit_log_copy TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.audit_log_line_copy TO etl_writer;
GRANT SELECT, INSERT ON efn_dwh.audit_recon_run TO etl_writer;
-- G18 chain epoch (07-audit-epoch.sql): riwayat epoch, append-only seperti salinan audit.
GRANT SELECT, INSERT ON efn_dwh.audit_chain_epoch TO etl_writer;
-- Defensif (idempoten): pastikan tidak ada hak ubah/hapus tersisa dari deploy lama.
REVOKE ALTER DELETE, ALTER UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE ON efn_dwh.audit_log_copy FROM etl_writer;
REVOKE ALTER DELETE, ALTER UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE ON efn_dwh.audit_log_line_copy FROM etl_writer;
REVOKE ALTER DELETE, ALTER UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE ON efn_dwh.audit_recon_run FROM etl_writer;
REVOKE ALTER DELETE, ALTER UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE ON efn_dwh.audit_chain_epoch FROM etl_writer;
-- Erasure DSAR (docs/04 §8.4): hanya tabel dengan kolom user_hash/member_hash (services/etl/efn_etl/erasure.py
-- ERASURE_TARGETS). `mutations_sync` disetel per query oleh ETL.
GRANT ALTER DELETE ON efn_dwh.competitor_prices TO etl_writer;
GRANT ALTER DELETE ON efn_dwh.loyalty_events TO etl_writer;

GRANT etl_writer TO efn_etl;
SET DEFAULT ROLE etl_writer TO efn_etl;
ALTER USER efn_etl SETTINGS PROFILE 'etl_writer_profile';

-- Pembaca salinan audit (auditor / forensik). TIDAK diikat ke user mana pun secara default: penunjukan = keputusan
-- klien/DPO (sama dengan group_auditor_personal di Odoo). Contoh: GRANT audit_copy_reader TO <user auditor>;
-- Salinan tidak memuat isi data pribadi (lihat 03-gl-audit.sql), tetapi memuat id user pelaku dan model/record.
CREATE ROLE IF NOT EXISTS audit_copy_reader;
GRANT SELECT ON efn_dwh.audit_log_copy TO audit_copy_reader;
GRANT SELECT ON efn_dwh.audit_log_line_copy TO audit_copy_reader;
GRANT SELECT ON efn_dwh.audit_recon_run TO audit_copy_reader;
GRANT SELECT ON efn_dwh.audit_chain_epoch TO audit_copy_reader;
