-- Migrasi platform Gelombang 50 bagian B (docs/04 §28.7/§28.9; ADR-048 email, ADR-044 WA unofficial — keduanya
-- Proposed): penyimpanan kanal email & WhatsApp tidak resmi di sisi BFF.
-- Pemilik: services/api-gateway (src/notification/email, src/notification/wa-unofficial). IDEMPOTEN dan transaksional
-- (satu BEGIN/COMMIT). Hanya CREATE; tanpa DROP/ubah data.
--   * Nomor 20 dicadangkan untuk migrasi G50 bagian A (behavior/wishlist/campaign). File ini tidak bergantung padanya.
--   * Volume baru: dijalankan otomatis setelah 01–20 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (21 perlu masuk daftar
--     WAJIB — tugas devsecops). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/21-platform-g50b.sql
--
-- Isi:
-- 1. `member_email`: alamat email member TERENKRIPSI (AES-256-GCM key ring PII, AAD `efn:member_email.email_enc:v1`),
--    `verified_at` (NULL = belum terverifikasi → tidak pernah dikirimi), `status` (`invalid` setelah SMTP 5xx RCPT).
--    Data pribadi. Retensi = selama akun; ikut terhapus saat erasure (FK CASCADE). Alur isi/verifikasi alamat BELUM
--    dibangun (G50-B hanya membaca) — lihat README `src/notification/email`.
-- 2. `email_outbound`: jejak antre/kirim per (sumber, member) TANPA alamat & isi. Retensi 90 hari (job BFF).
-- 3. `email_unsubscribe_used`: jti token berhenti berlangganan yang sudah dipakai (sekali pakai). Bukan PII.
--    Dihapus setelah token kedaluwarsa.
-- 4. `wa_unofficial_recipient`: `to_hash` (HMAC nomor, kunci `wa_unofficial_hash_key` — hanya BFF + wa-unofficial)
--    → member, untuk memproses "STOP". Pseudonim (data pribadi). Retensi 180 hari sejak antre terakhir.
-- 5. `wa_unofficial_outbound`: status kirim WA unofficial per member TANPA nomor & isi. Retensi 90 hari.
-- Consent memakai tabel `platform.consent` yang ada (source baru `email_unsubscribe`, `wa_stop`; kolom source sudah
-- bebas 1..32 karakter — tidak perlu ubah CHECK).
-- Hak akses: efn_bff DML minimum; efn_readonly/efn_etl/efn_ocr tanpa akses. Pemilik efn_platform_owner (SEC-39).
-- Risiko: R-L01 (PII email/nomor), R-B16/R-S21 (WA unofficial), R-B17 (spam promo).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Email member (data pribadi, terenkripsi) ─────────────
CREATE TABLE IF NOT EXISTS platform.member_email (
  member_id     uuid        PRIMARY KEY REFERENCES platform.member (id) ON DELETE CASCADE,
  email_enc     bytea       NOT NULL CHECK (octet_length(email_enc) > 29 AND get_byte(email_enc, 0) >= 1),
  verified_at   timestamptz NULL,
  status        text        NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invalid')),
  status_reason text        NULL CHECK (status_reason IS NULL OR status_reason ~ '^[a-z0-9_]{1,40}$'),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ───────────── 2. Jejak kirim email (tanpa alamat) ─────────────
CREATE TABLE IF NOT EXISTS platform.email_outbound (
  idempotency_key text        PRIMARY KEY CHECK (length(idempotency_key) BETWEEN 1 AND 300),
  member_id       uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  source          text        NOT NULL CHECK (source IN ('campaign', 'restock', 'price_alert', 'transactional')),
  source_ref      text        NOT NULL CHECK (source_ref ~ '^[A-Za-z0-9:_.-]{1,128}$'),
  template        text        NOT NULL CHECK (template ~ '^[a-z0-9_]{1,64}$'),
  status          text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'sent', 'skipped', 'invalid_address')),
  queued_at       timestamptz NOT NULL DEFAULT now(),
  done_at         timestamptz NULL
);
CREATE INDEX IF NOT EXISTS ix_email_outbound_member ON platform.email_outbound (member_id);
CREATE INDEX IF NOT EXISTS ix_email_outbound_queued_at ON platform.email_outbound (queued_at);

-- ───────────── 3. Token berhenti berlangganan terpakai ─────────────
CREATE TABLE IF NOT EXISTS platform.email_unsubscribe_used (
  jti        uuid        PRIMARY KEY,
  used_at    timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_email_unsubscribe_used_exp ON platform.email_unsubscribe_used (expires_at);

-- ───────────── 4. Pemetaan to_hash → member (STOP) ─────────────
CREATE TABLE IF NOT EXISTS platform.wa_unofficial_recipient (
  to_hash        char(64)    PRIMARY KEY CHECK (to_hash ~ '^[0-9a-f]{64}$'),
  member_id      uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  last_queued_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_wa_unofficial_recipient_member ON platform.wa_unofficial_recipient (member_id);
CREATE INDEX IF NOT EXISTS ix_wa_unofficial_recipient_last ON platform.wa_unofficial_recipient (last_queued_at);

-- ───────────── 5. Status kirim WA unofficial (tanpa nomor/isi) ─────────────
CREATE TABLE IF NOT EXISTS platform.wa_unofficial_outbound (
  idempotency_key text        PRIMARY KEY CHECK (length(idempotency_key) BETWEEN 1 AND 300),
  member_id       uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  to_hash         char(64)    NOT NULL CHECK (to_hash ~ '^[0-9a-f]{64}$'),
  category        text        NOT NULL CHECK (category IN ('promo', 'notify_restock', 'price_alert')),
  source          text        NOT NULL CHECK (source ~ '^[a-z_]{1,32}$'),
  source_ref      text        NOT NULL CHECK (source_ref ~ '^[A-Za-z0-9:_.-]{1,128}$'),
  template        text        NOT NULL CHECK (template ~ '^[a-z0-9_]{1,64}$'),
  status          text        NOT NULL DEFAULT 'queued'
                              CHECK (status IN ('queued', 'sent', 'failed', 'rate_limited', 'banned', 'disconnected')),
  reason          text        NULL CHECK (reason IS NULL OR reason ~ '^[a-z0-9_]{1,40}$'),
  queued_at       timestamptz NOT NULL DEFAULT now(),
  expires_at      timestamptz NOT NULL,
  result_at       timestamptz NULL
);
CREATE INDEX IF NOT EXISTS ix_wa_unofficial_outbound_member ON platform.wa_unofficial_outbound (member_id);
CREATE INDEX IF NOT EXISTS ix_wa_unofficial_outbound_queued_at ON platform.wa_unofficial_outbound (queued_at);

REVOKE ALL ON platform.member_email, platform.email_outbound, platform.email_unsubscribe_used,
              platform.wa_unofficial_recipient, platform.wa_unofficial_outbound
  FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.member_email, platform.email_outbound, platform.email_unsubscribe_used,
                                       platform.wa_unofficial_recipient, platform.wa_unofficial_outbound
  TO efn_bff;

RESET ROLE;

COMMIT;
