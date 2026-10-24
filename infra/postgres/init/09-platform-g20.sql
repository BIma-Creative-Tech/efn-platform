-- Migrasi platform Gelombang 20 (docs/04 §12.1–§12.2, keputusan lead L-48/L-50/L-52/L-53; ADR-001 revisi 6 entitas).
-- Pemilik: services/api-gateway (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT).
--   * Volume baru: dijalankan otomatis setelah 01–08 (urutan alfabet docker-entrypoint-initdb.d). File 02..08 sudah
--     memuat daftar kode baru; langkah 1 di bawah lalu menulis ulang constraint yang sama (no-op secara isi).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (glob `[0-9][0-9]-*.sql`).
--     BFF G20 WAJIB migrasi ini (kolom `online_order.payment_method`, `online_order_part.counter_code_enc`,
--     status `awaiting_counter_payment`, refund tanpa intent). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/09-platform-g20.sql
--
-- Isi:
-- 1. (§12.1, L-53) Kode PT EFN/GL/PB/BCR/BCF/FARM menggantikan EFN/RTL/FNB/FARM pada 7 CHECK `company_code`
--    (G24 §16.3: daftar memuat WTZ juga, agar deploy berulang tidak mengembalikan CHECK ke 6 kode sebelum 13 jalan)
--    (nama otomatis `<tabel>_company_code_check`): DROP IF EXISTS → ADD ... NOT VALID → VALIDATE bila tidak ada baris lama.
--    Baris dev lama ber-kode RTL/FNB TIDAK dimigrasi (keputusan §12.1: tidak ada migrasi data, DB dev/CI dibuat ulang,
--    `docs/runbooks/dev-reset.md`); selama baris itu ada constraint tetap NOT VALID (baris baru tetap dipaksa).
-- 2. (§12.2) `online_order.payment_method` ('qris' default | 'counter'); order `counter` tidak pernah punya intent.
-- 3. (§12.2 no. 4) status order `awaiting_counter_payment` (+ indeks job kedaluwarsa).
-- 4. (§12.2 no. 3) `online_order_part.counter_code_enc` — kode bayar di kasir, AES-256-GCM AAD
--    `efn:online_order_part.counter_code_enc:v2:<part_id>`; rahasia (bukan PII pelanggan), retensi: di-NULL-kan saat
--    bagian keluar dari `placed` / order tidak lagi menunggu bayar di kasir (job retensi BFF).
-- 5. (§12.2 no. 8) `online_refund.payment_intent_id` boleh NULL — refund order bayar di kasir (uang keluar lewat refund
--    order POS pelunasan, E-31; BFF tidak memanggil gateway).
-- Tidak ada CHECK lain yang memuat kode kanal literal (kanal hanya regex `^[A-Z][A-Z0-9-]{1,15}$`).
-- PII: tidak ada kolom PII pelanggan baru. Hak akses: efn_bff sudah SELECT/INSERT/UPDATE tingkat tabel (04/07) →
-- kolom baru tercakup; efn_readonly/efn_etl/efn_ocr tetap tanpa akses tabel online.
-- Risiko: R-T10 (saga), R-O05 (refund ≤ porsi), R-L01 (rahasia at rest).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Kode PT G20 ─────────────
DO $$
DECLARE
  t text;
  cname text;
  stale boolean;
  old record;
BEGIN
  FOREACH t IN ARRAY ARRAY['loyalty_ledger', 'payment_intent', 'loyalty_reservation', 'loyalty_pos_earn',
                           'online_order_part', 'payment_intent_portion', 'loyalty_pos_reverse'] LOOP
    IF to_regclass('platform.' || t) IS NULL THEN
      CONTINUE;
    END IF;
    cname := t || '_company_code_check';
    EXECUTE format('ALTER TABLE platform.%I DROP CONSTRAINT IF EXISTS %I', t, cname);
    -- Pengaman: CHECK lama bernama lain yang masih memuat daftar kode RTL/FNB juga dibuang
    FOR old IN SELECT conname FROM pg_constraint
                WHERE conrelid = ('platform.' || t)::regclass AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%company_code%' AND pg_get_constraintdef(oid) LIKE '%''RTL''%' LOOP
      EXECUTE format('ALTER TABLE platform.%I DROP CONSTRAINT %I', t, old.conname);
    END LOOP;
    EXECUTE format(
      'ALTER TABLE platform.%I ADD CONSTRAINT %I CHECK (company_code IN (''EFN'', ''GL'', ''PB'', ''BCR'', ''BCF'', ''WTZ'', ''FARM'')) NOT VALID',
      t, cname);
    EXECUTE format(
      'SELECT EXISTS (SELECT 1 FROM platform.%I WHERE company_code NOT IN (''EFN'', ''GL'', ''PB'', ''BCR'', ''BCF'', ''WTZ'', ''FARM''))', t)
      INTO stale;
    IF stale THEN
      RAISE WARNING '09-platform-g20: platform.% masih memuat kode PT lama (RTL/FNB) — constraint % dibiarkan NOT VALID; reset DB dev (docs/runbooks/dev-reset.md)', t, cname;
    ELSE
      EXECUTE format('ALTER TABLE platform.%I VALIDATE CONSTRAINT %I', t, cname);
    END IF;
  END LOOP;
END $$;

-- ───────────── 2–3. Metode bayar & status bayar di kasir ─────────────
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS payment_method text NOT NULL DEFAULT 'qris';
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_order_payment_method'
                   AND conrelid = 'platform.online_order'::regclass) THEN
    ALTER TABLE platform.online_order ADD CONSTRAINT ck_online_order_payment_method
      CHECK (payment_method IN ('qris', 'counter') AND (payment_method = 'qris' OR payment_intent_id IS NULL));
  END IF;
  -- Nama = nama otomatis CHECK inline di 07 (pola 07 bagian 4b)
  ALTER TABLE platform.online_order DROP CONSTRAINT IF EXISTS online_order_status_check;
  ALTER TABLE platform.online_order ADD CONSTRAINT online_order_status_check CHECK (status IN ('created', 'awaiting_payment',
    'awaiting_counter_payment', 'paid', 'fulfilling', 'completed', 'cancelled', 'expired', 'failed', 'review'));
END $$;
CREATE INDEX IF NOT EXISTS ix_online_order_counter_open ON platform.online_order (expires_at)
  WHERE status = 'awaiting_counter_payment';

-- ───────────── 4. Kode bayar di kasir (terenkripsi) ─────────────
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS counter_code_enc bytea NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_order_part_counter_code'
                   AND conrelid = 'platform.online_order_part'::regclass) THEN
    ALTER TABLE platform.online_order_part ADD CONSTRAINT ck_online_order_part_counter_code
      CHECK (counter_code_enc IS NULL OR (octet_length(counter_code_enc) > 29 AND get_byte(counter_code_enc, 0) >= 1));
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_online_order_part_counter ON platform.online_order_part (order_id) WHERE counter_code_enc IS NOT NULL;

-- ───────────── 5. Refund tanpa intent (bayar di kasir) ─────────────
ALTER TABLE platform.online_refund ALTER COLUMN payment_intent_id DROP NOT NULL;

RESET ROLE;

COMMIT;
