-- Migrasi platform Gelombang 27b (docs/04 §18.3a): purpose consent baru `privacy` (persetujuan Kebijakan Privasi +
-- pemrosesan data akun saat masuk/daftar, UU PDP). Pemilik: services/api-gateway. IDEMPOTEN dan transaksional.
--   * Volume baru: dijalankan otomatis setelah 01–15 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (16 masuk daftar WAJIB).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/16-platform-g27b.sql
--
-- Isi: CHECK `platform.consent.purpose` diperluas dari ('location','marketing') ke ('location','marketing','privacy').
-- Tabel tetap APPEND-ONLY (trigger 03 tidak diubah); tidak ada data yang diubah. Hanya konstrain yang diganti.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

ALTER TABLE platform.consent DROP CONSTRAINT IF EXISTS consent_purpose_check;
ALTER TABLE platform.consent
  ADD CONSTRAINT consent_purpose_check CHECK (purpose IN ('location', 'marketing', 'privacy'));

COMMIT;
