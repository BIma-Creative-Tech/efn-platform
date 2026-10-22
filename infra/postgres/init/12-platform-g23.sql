-- Migrasi platform Gelombang 23 (docs/04 §15.5, keputusan lead L-85..L-90).
-- Pemilik: services/api-gateway. IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya ADD/CREATE, tanpa DROP.
--   * Volume baru: dijalankan otomatis setelah 01–11 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (12 masuk daftar WAJIB).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/12-platform-g23.sql
--
-- Isi:
-- 1. (§15.5) `table_booking`: reservasi meja PB/BCF. `guest_label`/`note` TIDAK disimpan (hanya diteruskan ke Odoo).
-- 2. (§15.4) `online_order_part.pickup_slot`: jadwal ambil pre-order per bagian (NULL = ambil secepatnya).
-- Kolom di luar daftar minimum kontrak (alasan):
--   table_booking.outlet_name — tampilan "Reservasi saya" tanpa memanggil Odoo (bukan PII).
-- PII (UU PDP): `table_booking.member_id` (pseudonim UUID; dilepas BOOKING_MEMBER_LINK_DAYS=180 hari setelah end_at,
-- NULL saat erasure). Retensi 400 hari (BOOKING_RETENTION_DAYS).
-- Hak akses: efn_bff DML minimum; efn_readonly kolom non-PII untuk laporan; efn_ocr/efn_etl tanpa akses.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Reservasi meja ─────────────
CREATE TABLE IF NOT EXISTS platform.table_booking (
  id                uuid        PRIMARY KEY,
  member_id         uuid        NULL REFERENCES platform.member (id) ON DELETE SET NULL,
  idempotency_key   text        NOT NULL CHECK (char_length(idempotency_key) BETWEEN 16 AND 64),
  request_sha256    text        NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  company_code      text        NOT NULL CHECK (company_code IN ('PB', 'BCF')),
  channel_code      text        NOT NULL CHECK (channel_code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
  outlet_code       text        NOT NULL CHECK (char_length(outlet_code) BETWEEN 1 AND 32),
  outlet_name       text        NULL CHECK (outlet_name IS NULL OR char_length(outlet_name) <= 128),
  start_at          timestamptz NOT NULL,
  end_at            timestamptz NOT NULL,
  party_size        smallint    NOT NULL CHECK (party_size BETWEEN 1 AND 50),
  status            text        NOT NULL CHECK (status IN ('pending', 'requested', 'confirmed', 'seated', 'done', 'cancelled', 'no_show', 'failed')),
  odoo_booking_ref  text        NULL CHECK (odoo_booking_ref IS NULL OR char_length(odoo_booking_ref) <= 64),
  table_label       text        NULL CHECK (table_label IS NULL OR char_length(table_label) <= 64),
  last_seq          integer     NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CHECK (end_at > start_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_table_booking_member_idem ON platform.table_booking (member_id, idempotency_key);
CREATE INDEX IF NOT EXISTS ix_table_booking_member_start ON platform.table_booking (member_id, start_at DESC) WHERE member_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_table_booking_active ON platform.table_booking (status, start_at)
  WHERE status IN ('pending', 'requested', 'confirmed');
CREATE INDEX IF NOT EXISTS ix_table_booking_created ON platform.table_booking (created_at);

-- ───────────── 2. Slot ambil pre-order ─────────────
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS pickup_slot timestamptz NULL;

-- ───────────── 3. Hak akses (least privilege, docs/04 §7.3) ─────────────
REVOKE ALL ON platform.table_booking FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
-- DELETE hanya untuk retensi BOOKING_RETENTION_DAYS (400) & erasure
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.table_booking TO efn_bff;
-- Laporan okupansi (non-PII; tanpa member_id/idempotency_key).
GRANT SELECT (id, company_code, channel_code, outlet_code, start_at, end_at, party_size, status, created_at)
  ON platform.table_booking TO efn_readonly;
-- online_order_part: efn_bff sudah SELECT/INSERT/UPDATE tingkat tabel (07) → kolom baru tercakup.
GRANT SELECT (pickup_slot) ON platform.online_order_part TO efn_readonly;

COMMIT;
