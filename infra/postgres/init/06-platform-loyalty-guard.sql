-- Migrasi platform Gelombang 13c — SEC-61 (pagar earn POS) & SEC-65 (migrasi tidak menghidupkan subjek terhapus).
-- Pemilik: services/api-gateway (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT).
--   * Volume baru: dijalankan otomatis setelah 01–05 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: jalankan manual sebagai superuser SEBELUM men-deploy BFF Gelombang 13c — job erasure BFF menulis ke
--     tabel baru di dalam transaksi erasure; tanpa tabel ini erasure gagal dan dicoba ulang tiap siklus (tidak ada data
--     hilang, tetapi DSAR tertunda):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/06-platform-loyalty-guard.sql
--     Aman diulang; BFF lama tetap berjalan (tabel/indeks baru belum dipakai).
--
-- Isi:
-- 1. `platform.erasure_suppression` (SEC-65) — daftar tolak impor migrasi K-7 untuk subjek yang sudah dihapus (DSAR).
--    Satu kolom identitas: `tag` = hex(HMAC-SHA256(k, "efn:erasure_suppression:v1\n" || member_hash)),
--    k = HKDF-SHA256(otp_pepper, info "efn:erasure_suppression:v1"). Domain terpisah: tag TIDAK sama dengan member_hash
--    mana pun sehingga tidak dapat di-JOIN ke tabel lain; tidak ada request_id, waktu permintaan, atau PT.
--    Retensi: `expires_at` = waktu erasure + ERASURE_SUPPRESSION_DAYS (default 90, keputusan DPO); baris kedaluwarsa
--    dihapus job erasure BFF. Dasar pemrosesan: kewajiban memastikan penghapusan tetap berlaku (UU PDP Pasal 8/16)
--    — sisa risiko (pemegang otp_pepper + nomor HP dapat menguji "pernah dihapus?") dicatat di README privacy.
-- 2. Indeks `loyalty_pos_earn (member_hash, created_at)` (SEC-61) — batas earn harian per member dihitung dari tabel
--    ini di setiap earn.
-- 3. Hak akses: efn_bff INSERT/SELECT/DELETE + UPDATE(expires_at); tidak dibuka ke efn_readonly/efn_etl/efn_ocr.
-- ADR terkait: ADR-020 (ledger), ADR-022 (least privilege). Risiko: R-B05/R-B06 (fraud poin), R-L01 (UU PDP).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Daftar tolak migrasi pasca-erasure (SEC-65) ─────────────
CREATE TABLE IF NOT EXISTS platform.erasure_suppression (
  tag         char(64)    PRIMARY KEY CHECK (tag ~ '^[0-9a-f]{64}$'),
  expires_at  timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_erasure_suppression_expires ON platform.erasure_suppression (expires_at);

-- ───────────── 2. Batas earn harian per member (SEC-61) ─────────────
CREATE INDEX IF NOT EXISTS ix_loyalty_pos_earn_member_created
  ON platform.loyalty_pos_earn (member_hash, created_at);

RESET ROLE;

-- ───────────── 3. Hak akses ─────────────
REVOKE ALL ON platform.erasure_suppression FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, DELETE ON platform.erasure_suppression TO efn_bff;
GRANT UPDATE (expires_at) ON platform.erasure_suppression TO efn_bff;   -- ON CONFLICT: perpanjang, tidak memendekkan

COMMIT;
