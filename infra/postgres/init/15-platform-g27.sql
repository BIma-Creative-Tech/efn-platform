-- Migrasi platform Gelombang 27 (docs/04 §18.2): promo Super-App (banner slider, pop-up) + token push.
-- Pemilik: services/api-gateway. IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya CREATE; tanpa DROP/ubah data.
--   * Volume baru: dijalankan otomatis setelah 01–14 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (15 masuk daftar WAJIB).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/15-platform-g27.sql
--
-- Isi:
-- 1. `promo_item`: salinan feed promo Odoo (#22) per PT — materi visual publik (judul, teks, tautan, sha gambar).
--    Bukan data pribadi. Gambar di bucket `catalog` key `promo/<sha>/<w>.jpg`.
-- 2. `promo_sync`: status tarikan feed per PT (umur data, galat terakhir).
-- 3. `push_token`: token push Expo per member — token TERENKRIPSI (AES-256-GCM, AAD `efn:push_token.token_enc:v1`),
--    `token_hash` HMAC untuk dedupe. Data pribadi (pengenal perangkat): dihapus saat logout/erasure (FK CASCADE),
--    retensi = selama token aktif; token tak dipakai 180 hari dibersihkan job retensi.
-- Hak akses: efn_bff DML minimum; efn_readonly/efn_etl/efn_ocr tanpa akses.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Salinan promo ─────────────
CREATE TABLE IF NOT EXISTS platform.promo_item (
  kind          text        NOT NULL CHECK (kind IN ('banner', 'popup')),
  company_code  text        NOT NULL CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$'),
  odoo_id       integer     NOT NULL CHECK (odoo_id > 0),
  payload       jsonb       NOT NULL CHECK (jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 4096),
  image_sha     char(64)    NULL CHECK (image_sha IS NULL OR image_sha ~ '^[0-9a-f]{64}$'),
  image_ready   boolean     NOT NULL DEFAULT false,
  web           boolean     NOT NULL DEFAULT true,
  app           boolean     NOT NULL DEFAULT true,
  sequence      integer     NOT NULL DEFAULT 10,
  date_start    timestamptz NOT NULL,
  date_end      timestamptz NOT NULL CHECK (date_end > date_start),
  synced_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (kind, company_code, odoo_id)
);
CREATE INDEX IF NOT EXISTS ix_promo_item_live ON platform.promo_item (kind, date_end, sequence);
CREATE INDEX IF NOT EXISTS ix_promo_item_sha ON platform.promo_item (image_sha) WHERE image_sha IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform.promo_sync (
  company_code    text        PRIMARY KEY CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$'),
  last_ok_at      timestamptz NULL,
  last_attempt_at timestamptz NULL,
  last_error_code text        NULL CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z_]{1,40}$')
);

-- ───────────── 2. Token push (data pribadi: pengenal perangkat) ─────────────
CREATE TABLE IF NOT EXISTS platform.push_token (
  id           uuid        PRIMARY KEY,
  member_id    uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  platform     text        NOT NULL CHECK (platform IN ('android', 'ios')),
  token_enc    bytea       NOT NULL CHECK (octet_length(token_enc) > 29 AND get_byte(token_enc, 0) >= 1),
  token_hash   char(64)    NOT NULL CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  disabled_at  timestamptz NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_push_token_hash ON platform.push_token (token_hash);
CREATE INDEX IF NOT EXISTS ix_push_token_member ON platform.push_token (member_id) WHERE disabled_at IS NULL;

REVOKE ALL ON platform.promo_item, platform.promo_sync, platform.push_token FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.promo_item, platform.promo_sync, platform.push_token TO efn_bff;

RESET ROLE;

COMMIT;
