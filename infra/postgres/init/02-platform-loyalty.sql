-- Tabel platform milik BFF (docs/04_SERVICE_CONTRACTS.md §5, §7.2, §7.3). Dijalankan setelah 01-databases.sql
-- (urutan alfabet docker-entrypoint-initdb.d) dan HANYA saat volume PostgreSQL masih kosong.
-- Untuk volume yang sudah ada: jalankan manual sebagai superuser `psql -d efn_platform -f 02-platform-loyalty.sql`.
-- Pemilik: services/api-gateway (efn-bff-engineer). ADR terkait: D-05 (satu ledger poin), ADR-020 (Proposed,
-- liabilitas poin per PT), ADR-022 (enkripsi). Retensi: lihat komentar per tabel.
--
-- PERUBAHAN v2 (5 Okt 2026, Fase 0): loyalty_ledger.member_ref_enc WAJIB (NOT NULL); objek dimiliki efn_bff.

\connect efn_platform
\set ON_ERROR_STOP on

SET ROLE efn_bff;   -- objek dimiliki efn_bff (pemilik skema platform), bukan superuser

-- ─────────────────────────────────────────────────────────────
-- Ledger poin tunggal (D-05). Saldo = SUM(delta_points) entri yang belum kedaluwarsa.
-- member_ref_hash = hex(sha256(pepper_hex || member_ref)) dengan
-- pepper_hex = hex(HKDF-SHA256(otp_pepper, salt "", info "efn:member_hash:v1", 32)) — docs/04 §1.
-- member_ref_enc  = member_ref terenkripsi AES-256-GCM, AAD 'efn:loyalty_ledger.member_ref_enc:v1' (WAJIB, §7.2);
--                   untuk earn price_hunter: dekripsi price_hunter_report.user_enc → enkripsi ulang oleh BFF.
-- Retensi: ledger adalah catatan finansial (liabilitas poin) → disimpan selama kewajiban akuntansi berlaku.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.loyalty_ledger (
  id               bigserial PRIMARY KEY,
  member_ref_hash  char(64)    NOT NULL CHECK (member_ref_hash ~ '^[0-9a-f]{64}$'),
  member_ref_enc   bytea       NOT NULL CHECK (octet_length(member_ref_enc) > 29 AND get_byte(member_ref_enc, 0) >= 1),
  company_code     text        NOT NULL DEFAULT 'EFN'
                   CHECK (company_code IN ('EFN','RTL','FNB','FARM')),   -- PT penanggung liabilitas (ADR-020)
  delta_points     integer     NOT NULL CHECK (delta_points <> 0),
  reason           text        NOT NULL DEFAULT '',
  source           text        NOT NULL,                    -- price_hunter | mgm | pos | adjustment ...
  source_ref       text,
  idempotency_key  text        NOT NULL,
  expires_at       timestamptz,                             -- NULL = tidak kedaluwarsa
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_loyalty_ledger_idempotency_key UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_member  ON platform.loyalty_ledger (member_ref_hash, expires_at);
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_company ON platform.loyalty_ledger (company_code, created_at);
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_created ON platform.loyalty_ledger (created_at);   -- watermark ETL

-- ─────────────────────────────────────────────────────────────
-- Idempotensi consumer RabbitMQ (kontrak §4).
-- Retensi: PROCESSED_MESSAGE_RETENTION_DAYS (default 14) — job purge BFF (RetentionService).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.processed_message (
  consumer         text        NOT NULL,
  idempotency_key  text        NOT NULL,
  processed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (consumer, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_processed_message_at ON platform.processed_message (processed_at);

-- ─────────────────────────────────────────────────────────────
-- Transactional outbox (BFF → RabbitMQ). Payload v2 tidak memuat nomor HP (hanya hash), tetapi tetap tidak dibuka
-- ke efn_readonly. Retensi: baris terkirim > OUTBOX_RETENTION_DAYS (default 14) — job purge BFF.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.outbox (
  id            bigserial PRIMARY KEY,
  queue         text        NOT NULL,
  payload       jsonb       NOT NULL,
  message_id    text        NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  published_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_outbox_unpublished ON platform.outbox (id) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_outbox_published ON platform.outbox (published_at) WHERE published_at IS NOT NULL;

-- ─────────────────────────────────────────────────────────────
-- Hak akses efn_readonly (efn_etl / Text-to-SQL, R-S05, R-L01): level kolom, tanpa member_ref_enc & outbox.payload.
-- efn_ocr tidak mendapat akses ke tabel-tabel ini (worker hanya publish loyalty.earn.requested via RabbitMQ).
-- REVOKE dipertahankan agar idempoten pada volume lama.
-- ─────────────────────────────────────────────────────────────
REVOKE ALL ON platform.loyalty_ledger, platform.processed_message, platform.outbox FROM efn_readonly, efn_ocr;

GRANT SELECT (id, member_ref_hash, company_code, delta_points, reason, source, source_ref,
              idempotency_key, expires_at, created_at)
  ON platform.loyalty_ledger TO efn_readonly;
GRANT SELECT (consumer, idempotency_key, processed_at) ON platform.processed_message TO efn_readonly;
GRANT SELECT (id, queue, message_id, created_at, published_at) ON platform.outbox TO efn_readonly;

RESET ROLE;
