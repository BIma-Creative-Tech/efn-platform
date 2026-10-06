-- Migrasi platform v3.6 (gelombang 9; docs/04 §8.13; review keamanan r3 SEC-31 & SEC-39). Pemilik: services/api-gateway
-- (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT: gagal = tidak ada perubahan).
--   * Volume baru: dijalankan otomatis setelah 01–03 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: jalankan manual sebagai superuser SEBELUM menjalankan ulang 02/03:
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/04-platform-v3-6.sql
--     Aman diulang; BFF boleh tetap berjalan (perubahan hak terlihat atomik saat COMMIT).
--
-- 1. SEC-39 — kepemilikan: semua objek skema `platform` dipindah dari efn_bff ke role NOLOGIN `efn_platform_owner`
--    (dipakai hanya oleh migrasi lewat `SET ROLE` superuser). efn_bff tidak lagi pemilik → tidak bisa
--    `ALTER TABLE ... DISABLE TRIGGER`, `GRANT` ulang ke dirinya, atau DDL. Hak efn_bff = DML minimum per tabel di bawah
--    (least privilege, docs/04 §7.3). Batas: superuser/DBA tetap bisa — dicatat di README privacy.
-- 2. SEC-31 — anonimisasi erasure:
--    a. `dsar_request.member_hash` NULLABLE; wajib ada hanya selama request aktif (pending_reauth/scheduled/processing)
--       → CHECK `ck_dsar_member_hash_active`. Backfill: request delete `completed` dan request terminal milik member yang
--       sudah terhapus di-NULL-kan. ETL hanya membaca status `processing` (hash masih ada).
--    b. Tombstone lama deterministik `sha256("erased:"||request_id)` diganti nilai ACAK 32 byte per request (tidak disimpan
--       di mana pun selain baris yang di-tombstone). Entri forfeit lama: `source_ref` (= request_id) di-NULL-kan dan
--       `idempotency_key` (memuat request_id) diganti UUID acak. Laporan Market Scout ikut `updated_at = now()` agar ETL
--       mengirim tombstone baru ke ClickHouse.
-- ADR terkait: ADR-020 (ledger), ADR-022 (enkripsi/least privilege). Risiko: R-L01 (UU PDP), R-S05.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

-- ───────────── 1. SEC-39: pemilik NOLOGIN ─────────────
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'efn_platform_owner') THEN
    CREATE ROLE efn_platform_owner NOLOGIN;
  END IF;
  -- efn_bff tidak boleh mewarisi / SET ROLE ke pemilik
  IF pg_has_role('efn_bff', 'efn_platform_owner', 'MEMBER') THEN
    REVOKE efn_platform_owner FROM efn_bff;
  END IF;
END $$;
ALTER ROLE efn_platform_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

ALTER SCHEMA platform OWNER TO efn_platform_owner;
-- Semua objek milik efn_bff di DB ini (tabel, sequence, fungsi trigger) → efn_platform_owner. ACL yang dulu diberikan
-- efn_bff (ke efn_ocr/efn_readonly/efn_etl) ikut berpindah grantor. No-op bila sudah dipindah.
REASSIGN OWNED BY efn_bff TO efn_platform_owner;
-- Volume lama: 03 dulu mencabut hak efn_bff-sebagai-pemilik pada consent; REASSIGN memindahkan ACL itu apa adanya sehingga
-- pemilik baru kehilangan hak TRIGGER (CREATE TRIGGER di run ulang 03 gagal). Pulihkan hak penuh pemilik (idempoten).
GRANT ALL ON ALL TABLES IN SCHEMA platform TO efn_platform_owner;
GRANT ALL ON ALL SEQUENCES IN SCHEMA platform TO efn_platform_owner;

-- Hak efn_bff: reset lalu beri DML minimum (kode: services/api-gateway/src/**; perubahan query = perbarui di sini).
REVOKE CREATE ON SCHEMA platform FROM efn_bff;
GRANT USAGE ON SCHEMA platform TO efn_bff;
REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM efn_bff;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA platform FROM efn_bff;

-- Ledger: append + erasure/re-encrypt (tombstone, member_ref_enc, erased_at). Tidak ada DELETE.
GRANT SELECT, INSERT ON platform.loyalty_ledger TO efn_bff;
GRANT UPDATE (member_ref_hash, member_ref_enc, erased_at) ON platform.loyalty_ledger TO efn_bff;
-- Idempotensi consumer & outbox: retensi (DELETE) + relay (published_at, FOR UPDATE SKIP LOCKED)
GRANT SELECT, INSERT, DELETE ON platform.processed_message TO efn_bff;
GRANT SELECT, INSERT, DELETE ON platform.outbox TO efn_bff;
GRANT UPDATE (published_at) ON platform.outbox TO efn_bff;
-- Market Scout: intake, erasure, retensi
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.price_hunter_report TO efn_bff;
-- Audit OTP: insert, erasure (subject/ip_enc), retensi
GRANT SELECT, INSERT, DELETE ON platform.otp_audit TO efn_bff;
GRANT UPDATE (subject, ip_enc) ON platform.otp_audit TO efn_bff;
-- Member: daftar, deleted_at, re-encrypt, hapus saat erasure
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.member TO efn_bff;
-- Consent append-only (+ trigger): INSERT, UPDATE(ip_enc) untuk erasure/re-encrypt
GRANT SELECT, INSERT ON platform.consent TO efn_bff;
GRANT UPDATE (ip_enc) ON platform.consent TO efn_bff;
-- DSAR & payment: tanpa DELETE
GRANT SELECT, INSERT, UPDATE ON platform.dsar_request TO efn_bff;
GRANT SELECT, INSERT, UPDATE ON platform.payment_intent TO efn_bff;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA platform TO efn_bff;

-- ───────────── 2a. SEC-31: member_hash dsar_request hanya selama aktif ─────────────
ALTER TABLE platform.dsar_request ALTER COLUMN member_hash DROP NOT NULL;

UPDATE platform.dsar_request
   SET member_hash = NULL
 WHERE type = 'delete' AND status = 'completed' AND member_hash IS NOT NULL;

UPDATE platform.dsar_request d
   SET member_hash = NULL
 WHERE d.member_hash IS NOT NULL
   AND d.status IN ('completed', 'cancelled', 'rejected', 'failed')
   AND NOT EXISTS (SELECT 1 FROM platform.member m WHERE m.id = d.member_id);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_dsar_member_hash_active') THEN
    ALTER TABLE platform.dsar_request
      ADD CONSTRAINT ck_dsar_member_hash_active
      CHECK (member_hash IS NOT NULL OR status IN ('completed', 'cancelled', 'rejected', 'failed'));
  END IF;
END $$;

-- ───────────── 2b. SEC-31: ganti tombstone deterministik lama ─────────────
DO $$
DECLARE
  r     record;
  old_t text;
  new_t text;
BEGIN
  FOR r IN SELECT id::text AS id FROM platform.dsar_request
            WHERE type = 'delete' AND status IN ('processing', 'completed') LOOP
    old_t := encode(sha256(convert_to('erased:' || r.id, 'UTF8')), 'hex');
    new_t := encode(gen_random_bytes(32), 'hex');   -- pgcrypto (01-databases.sql)
    UPDATE platform.loyalty_ledger SET member_ref_hash = new_t WHERE member_ref_hash = old_t;
    UPDATE platform.price_hunter_report SET user_hash = new_t, updated_at = now() WHERE user_hash = old_t;
    UPDATE platform.otp_audit SET subject = new_t WHERE subject = old_t;
    UPDATE platform.loyalty_ledger
       SET source_ref = NULL, idempotency_key = 'dsar:forfeit:' || gen_random_uuid()::text
     WHERE source = 'dsar'
       AND (source_ref = r.id OR idempotency_key LIKE 'dsar:forfeit:' || r.id || ':%');
  END LOOP;
END $$;

COMMIT;
