-- Migrasi platform Gelombang 26 (docs/04 §17.9, keputusan lead L-95..L-100; CLAUDE.md K25-07).
-- Pemilik: services/api-gateway. IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya ADD/CREATE + menulis ulang
-- CHECK `payment_method`; tanpa DROP kolom/tabel dan tanpa perubahan data.
--   * Volume baru: dijalankan otomatis setelah 01–13 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (14 masuk daftar WAJIB).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/14-platform-g26.sql
--
-- Isi:
-- 1. (§17.2) `online_order.payment_method` + 'bank_transfer' (tanpa payment intent, seperti 'counter').
-- 2. (§17.3) Kolom transfer di `online_order`: sub-status, kode berita transfer, rekening tujuan (milik PT penjual —
--    BUKAN data pribadi pelanggan), jumlah unggahan, alasan tolak terakhir.
-- 3. (§17.4, §17.9) `online_transfer_proof`: riwayat unggahan bukti — HANYA hash (sha256 + dHash), tanpa gambar.
--    Foto bukti (data pribadi pengirim) tidak pernah disimpan BFF; satu-satunya penyimpan = Odoo.
-- Hak akses: efn_bff DML minimum; efn_readonly/efn_etl/efn_ocr tanpa akses (pola tabel online lain).
-- Retensi: `online_transfer_proof` 730 hari (job retensi online BFF). PII: tidak ada kolom PII baru.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Metode bayar 'bank_transfer' ─────────────
ALTER TABLE platform.online_order DROP CONSTRAINT IF EXISTS ck_online_order_payment_method;
ALTER TABLE platform.online_order ADD CONSTRAINT ck_online_order_payment_method
  CHECK (payment_method IN ('qris', 'counter', 'bank_transfer') AND (payment_method = 'qris' OR payment_intent_id IS NULL));

-- ───────────── 2. Kolom transfer bank di order ─────────────
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS transfer_status text NULL;
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS transfer_ref text NULL;
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS transfer_account jsonb NULL;
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS transfer_uploads integer NOT NULL DEFAULT 0;
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS transfer_reject_reason text NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_order_transfer'
                   AND conrelid = 'platform.online_order'::regclass) THEN
    ALTER TABLE platform.online_order ADD CONSTRAINT ck_online_order_transfer CHECK (
      (transfer_status IS NULL OR transfer_status IN ('awaiting_proof', 'awaiting_verification', 'rejected'))
      AND (transfer_ref IS NULL OR transfer_ref ~ '^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$')
      AND (transfer_account IS NULL OR jsonb_typeof(transfer_account) = 'object')
      AND transfer_uploads BETWEEN 0 AND 10
      AND (transfer_reject_reason IS NULL OR transfer_reject_reason IN
            ('amount_mismatch', 'unreadable', 'wrong_account', 'not_received', 'duplicate_proof'))
      AND (payment_method = 'bank_transfer' OR (transfer_status IS NULL AND transfer_ref IS NULL AND transfer_account IS NULL)));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_online_order_transfer_open ON platform.online_order (expires_at)
  WHERE payment_method = 'bank_transfer' AND status = 'awaiting_payment';

-- ───────────── 3. Riwayat bukti transfer (hash saja) ─────────────
CREATE TABLE IF NOT EXISTS platform.online_transfer_proof (
  id                uuid PRIMARY KEY,
  order_id          uuid NOT NULL REFERENCES platform.online_order (id) ON DELETE CASCADE,
  part_id           uuid NOT NULL REFERENCES platform.online_order_part (id) ON DELETE CASCADE,
  status            text NOT NULL CHECK (status IN ('submitted', 'approved', 'rejected', 'superseded', 'expired')),
  sha256            char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  dhash             char(16) NOT NULL CHECK (dhash ~ '^[0-9a-f]{16}$'),
  duplicate_suspect boolean NOT NULL DEFAULT false,
  reject_reason     text NULL CHECK (reject_reason IS NULL OR reject_reason IN
                      ('amount_mismatch', 'unreadable', 'wrong_account', 'not_received', 'duplicate_proof')),
  uploaded_at       timestamptz NOT NULL DEFAULT now(),
  decided_at        timestamptz NULL
);
CREATE INDEX IF NOT EXISTS ix_online_transfer_proof_order ON platform.online_transfer_proof (order_id);
CREATE INDEX IF NOT EXISTS ix_online_transfer_proof_sha ON platform.online_transfer_proof (sha256);
CREATE INDEX IF NOT EXISTS ix_online_transfer_proof_uploaded ON platform.online_transfer_proof (uploaded_at);

REVOKE ALL ON platform.online_transfer_proof FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.online_transfer_proof TO efn_bff;

RESET ROLE;

COMMIT;
