-- Migrasi platform Gelombang 18 — privasi (W-18 Q-P18, W-19 Q-P3). Pemilik: services/api-gateway (efn-bff-engineer).
-- Keputusan pemilik proyek 10 Okt 2026 (docs/decisions/README.md, PDP_DATA_INVENTORY v7 §0a).
-- IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya ADD COLUMN dengan default → aman untuk BFF lama.
--   * Volume baru: dijalankan otomatis setelah 01–07 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (BFF G18 membutuhkan kolom
--     ini: INSERT dsar_request menulis `open_orders_ack_at`, job erasure membaca `defer_escalation`, login menulis
--     `age_confirmed_at`). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/08-platform-g18-privacy.sql
--
-- Isi:
-- 1. `dsar_request.open_orders_ack_at` — waktu pelanggan MENGONFIRMASI hapus akun walau ada pesanan online terbuka
--    (`DELETE /v1/me?confirm_open_orders=true`); bukti pilihan sadar (L-4 + Q-P18). NULL = tidak ada pesanan terbuka.
-- 2. `dsar_request.defer_escalation` — tingkat eskalasi CS yang sudah dikirim (0 = belum, 1 = hari 3, 2 = hari 10)
--    selama erasure tertahan pesanan online terbuka; mencegah alert berulang tiap siklus job (multi-replika aman).
-- 3. `member.age_confirmed_at` — waktu pernyataan usia minimum (17 th, Q-P3) saat login/daftar Super-App. Tanpa tanggal
--    lahir (minimisasi data). Ikut terhapus bersama baris member saat erasure. Member hasil migrasi/POS: NULL sampai
--    login pertama di Super-App.
-- PII: tidak ada kolom PII baru (stempel waktu saja). Risiko: R-L01.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

ALTER TABLE platform.dsar_request ADD COLUMN IF NOT EXISTS open_orders_ack_at timestamptz;
ALTER TABLE platform.dsar_request ADD COLUMN IF NOT EXISTS defer_escalation smallint NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_dsar_defer_escalation'
                   AND conrelid = 'platform.dsar_request'::regclass) THEN
    ALTER TABLE platform.dsar_request
      ADD CONSTRAINT ck_dsar_defer_escalation CHECK (defer_escalation BETWEEN 0 AND 2);
  END IF;
END $$;

ALTER TABLE platform.member ADD COLUMN IF NOT EXISTS age_confirmed_at timestamptz;

RESET ROLE;

-- Hak akses: efn_bff sudah SELECT/INSERT/UPDATE tingkat tabel (04-platform-v3-6.sql) → kolom baru tercakup.
-- efn_etl hanya SELECT kolom tertentu dsar_request (03) → kolom baru TIDAK terlihat ETL (sengaja).

COMMIT;
