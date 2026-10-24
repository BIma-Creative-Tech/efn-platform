-- Migrasi platform Gelombang 24 (docs/04 §16.3, keputusan lead L-91/L-94; ADR-001 revisi: entitas ke-7 WTZ).
-- Pemilik: services/api-gateway. IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya menulis ulang CHECK, tanpa
-- DROP kolom/tabel dan tanpa perubahan data.
--   * Volume baru: dijalankan otomatis setelah 01–12 (urutan alfabet docker-entrypoint-initdb.d). File 02..10 sudah
--     memuat daftar 7 kode; langkah di bawah lalu menulis ulang constraint yang sama (no-op secara isi).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (13 masuk daftar WAJIB).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/13-platform-g24.sql
--
-- Isi:
-- 1. (§16.3) Kode PT EFN/GL/PB/BCR/BCF/WTZ/FARM pada 8 CHECK `company_code` (nama otomatis `<tabel>_company_code_check`)
--    dan CHECK `ck_loyalty_ledger_issuer` (`issuer_company_code`, 10-platform-g21.sql): DROP IF EXISTS → ADD ... NOT
--    VALID → VALIDATE (semua baris lama memuat salah satu dari 6 kode lama ⊂ daftar baru, jadi VALIDATE selalu lolos
--    kecuali ada data di luar kontrak; bila ada, constraint dibiarkan NOT VALID + WARNING, baris baru tetap dipaksa).
-- 2. Pengaman: CHECK lain di tabel yang sama yang masih memuat daftar 6 entitas tanpa 'WTZ' dibuang (sama dengan pola
--    pengaman RTL/FNB di 09).
-- TIDAK diubah (L-92, §16.2): CHECK `company_code IN ('PB','BCF')` tabel `fnb_table_session`, `fnb_menu_item`,
-- `fnb_menu_sync` (QR meja, 11) dan `table_booking` (reservasi, 12) — WTZ tidak memakai QR meja/reservasi.
-- PII: tidak ada kolom baru. Risiko: R-T01 (kode PT konsisten antar-service).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1–2. Kode PT G24 (+ WTZ) ─────────────
DO $$
DECLARE
  spec text[];
  t text;
  col text;
  cname text;
  codes constant text := '''EFN'', ''GL'', ''PB'', ''BCR'', ''BCF'', ''WTZ'', ''FARM''';
  cond text;
  stale boolean;
  old record;
BEGIN
  -- {tabel, kolom, nama constraint}
  FOREACH spec SLICE 1 IN ARRAY ARRAY[
      ARRAY['loyalty_ledger',         'company_code',        'loyalty_ledger_company_code_check'],
      ARRAY['payment_intent',         'company_code',        'payment_intent_company_code_check'],
      ARRAY['loyalty_reservation',    'company_code',        'loyalty_reservation_company_code_check'],
      ARRAY['loyalty_pos_earn',       'company_code',        'loyalty_pos_earn_company_code_check'],
      ARRAY['online_order_part',      'company_code',        'online_order_part_company_code_check'],
      ARRAY['payment_intent_portion', 'company_code',        'payment_intent_portion_company_code_check'],
      ARRAY['loyalty_pos_reverse',    'company_code',        'loyalty_pos_reverse_company_code_check'],
      ARRAY['loyalty_policy',         'company_code',        'loyalty_policy_company_code_check'],
      ARRAY['loyalty_ledger',         'issuer_company_code', 'ck_loyalty_ledger_issuer']] LOOP
    t := spec[1]; col := spec[2]; cname := spec[3];
    IF to_regclass('platform.' || t) IS NULL THEN
      CONTINUE;
    END IF;
    EXECUTE format('ALTER TABLE platform.%I DROP CONSTRAINT IF EXISTS %I', t, cname);
    -- Pengaman: CHECK lain di kolom ini yang masih memuat daftar 6 entitas (ada 'FARM', tanpa 'WTZ') juga dibuang
    FOR old IN SELECT conname FROM pg_constraint
                WHERE conrelid = ('platform.' || t)::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%' || col || '%'
                  AND pg_get_constraintdef(oid) LIKE '%''FARM''%'
                  AND pg_get_constraintdef(oid) NOT LIKE '%''WTZ''%' LOOP
      EXECUTE format('ALTER TABLE platform.%I DROP CONSTRAINT %I', t, old.conname);
    END LOOP;
    IF col = 'issuer_company_code' THEN
      cond := format('%I IS NULL OR %I IN (%s)', col, col, codes);
    ELSE
      cond := format('%I IN (%s)', col, codes);
    END IF;
    EXECUTE format('ALTER TABLE platform.%I ADD CONSTRAINT %I CHECK (%s) NOT VALID', t, cname, cond);
    EXECUTE format('SELECT EXISTS (SELECT 1 FROM platform.%I WHERE NOT (%s))', t, cond) INTO stale;
    IF stale THEN
      RAISE WARNING '13-platform-g24: platform.%.% memuat kode PT di luar kontrak §16.1 — constraint % dibiarkan NOT VALID', t, col, cname;
    ELSE
      EXECUTE format('ALTER TABLE platform.%I VALIDATE CONSTRAINT %I', t, cname);
    END IF;
  END LOOP;
END $$;

COMMIT;
