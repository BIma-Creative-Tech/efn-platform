-- Migrasi platform Gelombang 44 (docs/04 §12.1 revisi 18 Nov 2026, §28, L-144; ADR-045 Proposed): kode PT ke-8 `SMD`
-- (PT Supermarket Demo, placeholder; nama/NPWP/kode final = fit-gap Q-SM1).
-- Pemilik: services/api-gateway (kontrak CHECK) / efn-devsecops-engineer (file migrasi). IDEMPOTEN dan transaksional
-- (satu BEGIN/COMMIT). Hanya menulis ulang CHECK, tanpa DROP kolom/tabel dan tanpa perubahan data.
--   * Nomor 18 DICADANGKAN G43 (`18-platform-g43.sql`, branch migrasi ESB -> Odoo, menambah IMD/CUP). Jangan dipakai.
--   * Volume baru: dijalankan otomatis setelah 01–17 (urutan alfabet docker-entrypoint-initdb.d). File 02..17 memuat
--     daftar 7 kode G24; langkah di bawah menulis ulang constraint dengan daftar 8 kode.
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (19 masuk daftar WAJIB).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/19-platform-g44.sql
--
-- Masalah yang ditutup: BFF G44 menerima `company_code = 'SMD'` (COMPANY_CODES) tetapi CHECK DB menolak tulis ledger
-- poin / kebijakan / MGM untuk PT itu (INSERT gagal 23514 -> 500 di jalur POS).
--
-- Isi:
-- 1. Kode PT EFN/GL/PB/BCR/BCF/WTZ/FARM/SMD pada 11 CHECK `company_code` (nama otomatis `<tabel>_company_code_check`)
--    dan CHECK `ck_loyalty_ledger_issuer` (`issuer_company_code`, 10-platform-g21.sql): DROP IF EXISTS -> ADD ... NOT
--    VALID -> VALIDATE (baris lama memuat salah satu dari 7 kode lama ⊂ daftar baru; bila ada data di luar kontrak,
--    constraint dibiarkan NOT VALID + WARNING, baris baru tetap dipaksa).
-- 2. Pengaman: CHECK lain di kolom yang sama yang memuat daftar kode PT tanpa 'SMD' dibuang (pola 13-platform-g24).
-- 3. PAGAR MERGE (G43): sebelum constraint lama dibuang, setiap kode PT di definisinya harus ada di daftar baru. Bila
--    tidak (mis. G43 `18-platform-g43.sql` sudah menambah IMD/CUP dan file ini belum diperbarui) migrasi GAGAL
--    (RAISE EXCEPTION, transaksi dibatalkan) alih-alih diam-diam MENYEMPITKAN daftar. Saat G43 merge ke main, `codes`
--    di bawah WAJIB menjadi: 'EFN','GL','PB','BCR','BCF','WTZ','IMD','CUP','FARM','SMD' (urutan bebas, isi persis).
-- TIDAK diubah: CHECK `company_code IN ('PB','BCF')` tabel QR meja (11) dan reservasi (12) — SMD tidak memakainya.
-- PII: tidak ada kolom baru. Risiko: R-T01 (kode PT konsisten antar-service: efn_core, BFF, DB, ETL, deploy-validate).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1–3. Kode PT G44 (+ SMD) ─────────────
DO $$
DECLARE
  spec text[];
  t text;
  col text;
  cname text;
  -- Daftar kode PT §12.1 (sama dengan scripts/lib/deploy-validate.sh EFN_COMPANY_CODES). Ubah keduanya bersamaan.
  code_list constant text[] := ARRAY['EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM', 'SMD'];
  codes text;
  cond text;
  stale boolean;
  old record;
  lost text[];
BEGIN
  SELECT string_agg(quote_literal(c), ', ') INTO codes FROM unnest(code_list) AS c;
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
      ARRAY['mgm_policy',             'company_code',        'mgm_policy_company_code_check'],
      ARRAY['mgm_source',             'company_code',        'mgm_source_company_code_check'],
      ARRAY['mgm_reward',             'company_code',        'mgm_reward_company_code_check'],
      ARRAY['loyalty_ledger',         'issuer_company_code', 'ck_loyalty_ledger_issuer']] LOOP
    t := spec[1]; col := spec[2]; cname := spec[3];
    IF to_regclass('platform.' || t) IS NULL THEN
      CONTINUE;
    END IF;
    -- (3) Pagar merge: CHECK daftar kode PT yang ada di kolom ini (nama tetap ATAU constraint lama lain yang memuat
    -- 'FARM') tidak boleh punya kode yang hilang dari daftar baru.
    FOR old IN SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint
                WHERE conrelid = ('platform.' || t)::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%' || col || '%'
                  AND (conname = cname OR pg_get_constraintdef(oid) LIKE '%''FARM''%') LOOP
      SELECT array_agg(DISTINCT m[1]) INTO lost
        FROM regexp_matches(old.def, '''([A-Z]{2,8})''', 'g') AS m
       WHERE m[1] <> ALL (code_list);
      IF lost IS NOT NULL THEN
        RAISE EXCEPTION '19-platform-g44: platform.%.% constraint % memuat kode PT % yang tidak ada di daftar G44 % — perbarui code_list (lihat catatan merge G43 di header), migrasi dibatalkan',
          t, col, old.conname, lost, code_list;
      END IF;
    END LOOP;
    EXECUTE format('ALTER TABLE platform.%I DROP CONSTRAINT IF EXISTS %I', t, cname);
    -- (2) Pengaman: CHECK lain di kolom ini yang masih memuat daftar kode PT lama (ada 'FARM', tanpa 'SMD') dibuang
    FOR old IN SELECT conname FROM pg_constraint
                WHERE conrelid = ('platform.' || t)::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%' || col || '%'
                  AND pg_get_constraintdef(oid) LIKE '%''FARM''%'
                  AND pg_get_constraintdef(oid) NOT LIKE '%''SMD''%' LOOP
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
      RAISE WARNING '19-platform-g44: platform.%.% memuat kode PT di luar kontrak §12.1 — constraint % dibiarkan NOT VALID', t, col, cname;
    ELSE
      EXECUTE format('ALTER TABLE platform.%I VALIDATE CONSTRAINT %I', t, cname);
    END IF;
  END LOOP;
END $$;

COMMIT;
