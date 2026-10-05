-- Dijalankan sekali saat volume PostgreSQL kosong (docker-entrypoint-initdb.d, sebagai superuser, urutan alfabet).
-- 00-roles.sh (DevSecOps) SUDAH membuat role login per service dengan password dari secret (docs/04 §7.3, SEC-02):
--   efn_odoo, efn_bff, efn_ocr, efn_etl (anggota efn_readonly), efn_metabase, efn_exporter.
-- File ini: database, skema `platform` milik efn_bff, tabel inti, dan GRANT level-kolom.
-- Pemilik isi skema platform: services/api-gateway (efn-bff-engineer). Kontrak: docs/04 §5, §7.2, §7.3.
--
-- PERUBAHAN v2 (5 Okt 2026, Fase 0 — belum ada data produksi, file diubah langsung):
--   * price_hunter_report.user_id (nomor HP plaintext) DIHAPUS → user_hash char(64) + user_enc bytea (AES-256-GCM).
--   * otp_audit.ip inet → ip_enc bytea (AES-256-GCM).
--   Volume dev lama: hapus volume postgres lalu init ulang (init script tidak berjalan pada volume yang sudah ada).

\set ON_ERROR_STOP on

-- Prasyarat: role dari 00-roles.sh. Gagal dengan pesan jelas bila belum ada (tidak membuat role tanpa password).
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['efn_bff', 'efn_ocr', 'efn_etl', 'efn_metabase'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      RAISE EXCEPTION 'Role % belum ada: jalankan infra/postgres/init/00-roles.sh (DevSecOps) lebih dulu', r;
    END IF;
  END LOOP;
END $$;

-- Role grup read-only (ETL, Text-to-SQL). Idempoten: 00-roles.sh mungkin sudah membuatnya.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'efn_readonly') THEN
    CREATE ROLE efn_readonly NOLOGIN;
  END IF;
END $$;
-- Volume baru: 00-roles.sh berjalan SEBELUM role efn_readonly ada sehingga melewati keanggotaan ini; tanpa baris
-- ini ETL (dev `docker compose up` tanpa deploy.sh) tidak punya grant kolom apa pun. Idempoten (QA gelombang 4).
GRANT efn_readonly TO efn_etl;

-- DB Metabase (metadata aplikasi), dimiliki efn_metabase saja (§7.3). Idempoten via \gexec.
SELECT 'CREATE DATABASE metabase OWNER efn_metabase'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'metabase')\gexec

-- Least privilege pada DB platform: PUBLIC tidak otomatis boleh CONNECT/TEMP.
REVOKE ALL ON DATABASE efn_platform FROM PUBLIC;
GRANT CONNECT ON DATABASE efn_platform TO efn_bff, efn_ocr, efn_readonly;
GRANT TEMPORARY ON DATABASE efn_platform TO efn_bff;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'efn_exporter') THEN
    GRANT CONNECT ON DATABASE efn_platform TO efn_exporter;   -- postgres-exporter (pg_monitor)
  END IF;
END $$;

\connect efn_platform
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Skema platform dimiliki efn_bff (§7.3). Objek dibuat SEBAGAI efn_bff agar pemiliknya efn_bff (bukan superuser).
CREATE SCHEMA IF NOT EXISTS platform AUTHORIZATION efn_bff;
ALTER SCHEMA platform OWNER TO efn_bff;
REVOKE ALL ON SCHEMA platform FROM PUBLIC;

SET ROLE efn_bff;

-- ─────────────────────────────────────────────────────────────
-- Price Hunter (docs/04 §3.3, §7.2). Identitas pelapor:
--   user_hash = member_hash (hex sha256(pepper_hex || member_ref), docs/04 §1) — cap harian, dedup, join analitik;
--   user_enc  = 0x01 ‖ IV12 ‖ ciphertext ‖ tag16, AES-256-GCM, AAD 'efn:price_hunter_report.user_enc:v1'
--               (hanya BFF yang memegang kunci; dipakai untuk notifikasi & DSAR).
-- Koordinat sudah dibulatkan 3 desimal oleh BFF (SEC-17).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.price_hunter_report (
  id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_hash       char(64) NOT NULL CHECK (user_hash ~ '^[0-9a-f]{64}$'),
  user_enc        bytea NOT NULL CHECK (octet_length(user_enc) > 29 AND get_byte(user_enc, 0) >= 1),
  product_name    text NOT NULL,
  reported_price  numeric(14,2) NOT NULL,
  competitor_name text,
  geo_lat         double precision,
  geo_lng         double precision,
  image_object    text NOT NULL,              -- key di MinIO
  image_phash     char(16),                   -- perceptual hash untuk dedup (R-B05), diisi ai-ocr-worker
  status          text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','valid','rejected','flagged')),
  extracted       jsonb,
  confidence      numeric(4,3),
  reason          text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_phr_user_created ON platform.price_hunter_report (user_hash, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_phr_phash ON platform.price_hunter_report (image_phash);
CREATE INDEX IF NOT EXISTS ix_phr_updated ON platform.price_hunter_report (updated_at);   -- watermark ETL

-- ─────────────────────────────────────────────────────────────
-- Audit OTP. subject = member_hash; ip_enc = IP klien terenkripsi (AAD 'efn:otp_audit.ip_enc:v1'),
-- forensik hanya lewat dekripsi oleh admin. Retensi: OTP_AUDIT_RETENTION_DAYS (default 90) — job purge BFF.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.otp_audit (
  id          bigserial PRIMARY KEY,
  purpose     text NOT NULL,                  -- pos_promo | login | redeem
  subject     text NOT NULL,                  -- member_hash
  order_ref   text,
  outcome     text NOT NULL CHECK (outcome IN ('sent','verified','failed','expired','locked')),
  ip_enc      bytea,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_otp_audit_created ON platform.otp_audit (created_at);

-- ─────────────────────────────────────────────────────────────
-- GRANT (pemilik = efn_bff). Tidak ada kolom *_enc untuk role selain efn_bff.
-- Tidak ada `GRANT SELECT ON ALL TABLES` / `ALTER DEFAULT PRIVILEGES`: tabel baru tidak otomatis terbaca.
-- ─────────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA platform TO efn_ocr, efn_readonly;

-- efn_ocr (ai-ocr-worker, §7.3): baca kolom non-PII yang dibutuhkan klaim/anti-fraud, update hasil saja.
GRANT SELECT (id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object,
              image_phash, status, extracted, confidence, reason, created_at, updated_at)
  ON platform.price_hunter_report TO efn_ocr;
GRANT UPDATE (status, reason, extracted, confidence, image_phash, updated_at)
  ON platform.price_hunter_report TO efn_ocr;

-- efn_readonly (efn_etl / Text-to-SQL, R-S05, R-L01, SEC-16): kolom yang dibaca ETL (services/etl/efn_etl/jobs.py).
-- user_hash sudah ter-hash di Postgres → ETL tidak butuh otp_pepper. TIDAK di-grant: user_enc, image_object,
-- image_phash, otp_audit.subject/ip_enc/order_ref.
GRANT SELECT (id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, status, extracted,
              confidence, reason, created_at, updated_at)
  ON platform.price_hunter_report TO efn_readonly;
GRANT SELECT (id, purpose, outcome, created_at) ON platform.otp_audit TO efn_readonly;

RESET ROLE;
