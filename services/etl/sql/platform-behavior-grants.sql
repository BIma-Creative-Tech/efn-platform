-- G50 (ADR-047 Proposed, docs/04 §28.6/§28.7, L-153/L-154): grant read-only ETL pada skema `behavior` DB platform.
-- Pemilik skema & tabel = BFF (`efn_bff`, init Postgres G50 milik efn-bff-engineer/devsecops). File ini TIDAK membuat
-- tabel; dijalankan SETELAH tabel ada, sebagai pemilik tabel (pola odoo-readonly-grants.sql):
--   psql "host=postgres dbname=efn_platform user=efn_bff sslmode=verify-full sslrootcert=/run/secrets/efn_ca_crt" \
--        -v ON_ERROR_STOP=1 -f services/etl/sql/platform-behavior-grants.sql
-- Atau isi file ini disalin ke init Postgres G50 setelah CREATE TABLE (pilihan devsecops). Idempoten.
-- ON_ERROR_STOP + grant level kolom = verifikasi skema: kolom yang diasumsikan ETL (services/etl/efn_etl/behavior.py,
-- SQL_BEHAVIOR_EVENTS / SQL_BEHAVIOR_ERASURES / SQL_DSAR_SUBJECT_KEYS) tidak ada -> deploy gagal, bukan ETL diam.
--
-- Prinsip: grant LANGSUNG ke efn_etl (bukan efn_readonly): Text-to-SQL/Metabase tidak boleh membaca event per subjek.
-- Tidak ada akses ke behavior.device_link (member_id <-> device_key), behavior.device_consent, atau kolom batch_id/
-- client_ts (tidak dibutuhkan mart). Koneksi ETL read-only (default_transaction_read_only=on).

\set ON_ERROR_STOP on

GRANT USAGE ON SCHEMA behavior TO efn_etl;

-- Staging event (partisi harian, retensi 7 hari oleh BFF). Grant pada tabel induk berlaku untuk query lewat induk;
-- partisi baru tidak perlu grant tersendiri selama ETL selalu membaca lewat `behavior.event_raw`.
GRANT SELECT (event_id, subject_key, subject_kind, company_code, channel, channel_code, outlet_code, type, product_id,
              duration_s, qty, query, result_count, stock_status, price_idr, received_at, ingested_at)
    ON behavior.event_raw TO efn_etl;

-- Antrean erasure perilaku (append-only, milik BFF, retensi >= 100 hari > TTL ClickHouse 90 hari).
GRANT SELECT (id, subject_key, request_id, created_at) ON behavior.subject_erasure TO efn_etl;

-- Pertahanan berlapis: tabel penaut identitas tidak pernah terbaca ETL/Text-to-SQL.
DO $$
BEGIN
  IF to_regclass('behavior.device_link') IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON behavior.device_link FROM efn_etl, efn_readonly';
  END IF;
  IF to_regclass('behavior.device_consent') IS NOT NULL THEN
    EXECUTE 'REVOKE ALL ON behavior.device_consent FROM efn_etl, efn_readonly';
  END IF;
END
$$;

-- Index cursor yang DIBUTUHKAN ETL (dibuat BFF di DDL partisi; dicantumkan di sini sebagai kontrak, bukan dibuat):
--   behavior.event_raw        : (ingested_at)              -- cursor job behavior_events
--   behavior.subject_erasure  : (created_at, id), (request_id)
