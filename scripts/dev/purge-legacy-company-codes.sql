-- DEV SAJA: buang baris BFF berkode PT lama (RTL/FNB, sebelum cutover G20 §12.1) lalu VALIDATE constraint
-- `*_company_code_check`. Alternatif terarah untuk docs/runbooks/dev-reset.md Opsi A (reset total).
--
-- Gejala yang diperbaiki: ETL `loyalty_events` gagal tiap siklus ("company_code tidak dikenal", fail-closed
-- entities.company_code) dan constraint company_code tetap NOT VALID (WARNING 09-platform-g20).
--
-- Pemakaian (backup dulu: pg_dump -Fc efn_platform):
--   docker exec -i efn-postgres-1 psql -X -v ON_ERROR_STOP=1 -v confirm=dev-only -U postgres -d efn_platform \
--     -f - < scripts/dev/purge-legacy-company-codes.sql
--
-- Pengaman: wajib `-v confirm=dev-only`; gagal bila ada baris RTL/FNB setelah 2026-10-19 (bukan data pra-G20) atau
-- bila baris lama dirujuk pesanan online/refund/porsi pembayaran. Satu transaksi: gagal = tidak ada yang berubah.
\set ON_ERROR_STOP on
\if :{?confirm}
\else
\echo 'Tolak: jalankan dengan -v confirm=dev-only (skrip ini menghapus data, HANYA untuk dev)'
\quit
\endif
SELECT :'confirm' = 'dev-only' AS efn_confirm_ok \gset
\if :efn_confirm_ok
\else
\echo 'Tolak: confirm harus dev-only'
\quit
\endif

BEGIN;
SET LOCAL lock_timeout = '10s';

DO $guard$
DECLARE
    t text;
    n bigint;
    tables constant text[] := ARRAY['loyalty_ledger', 'loyalty_pos_earn', 'loyalty_pos_reverse', 'loyalty_reservation',
                                    'payment_intent'];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('SELECT count(*) FROM platform.%I WHERE company_code IN (''RTL'', ''FNB'')
                          AND created_at >= ''2026-10-19''', t) INTO n;
        IF n > 0 THEN
            RAISE EXCEPTION 'platform.%: % baris RTL/FNB setelah cutover G20 - bukan data lama, berhenti', t, n;
        END IF;
    END LOOP;
    SELECT count(*) INTO n FROM platform.payment_intent pi
     WHERE pi.company_code IN ('RTL', 'FNB')
       AND (EXISTS (SELECT 1 FROM platform.online_order o WHERE o.payment_intent_id = pi.id)
            OR EXISTS (SELECT 1 FROM platform.online_refund r WHERE r.payment_intent_id = pi.id)
            OR EXISTS (SELECT 1 FROM platform.payment_intent_portion p WHERE p.payment_intent_id = pi.id));
    IF n > 0 THEN
        RAISE EXCEPTION 'payment_intent lama dirujuk pesanan/refund/porsi (%), berhenti - pakai dev-reset Opsi A', n;
    END IF;
END
$guard$;

WITH d AS (DELETE FROM platform.loyalty_pos_reverse WHERE company_code IN ('RTL', 'FNB') RETURNING 1)
SELECT 'loyalty_pos_reverse' AS tabel, count(*) AS dihapus FROM d;
WITH d AS (DELETE FROM platform.loyalty_pos_earn WHERE company_code IN ('RTL', 'FNB') RETURNING 1)
SELECT 'loyalty_pos_earn' AS tabel, count(*) AS dihapus FROM d;
WITH d AS (DELETE FROM platform.loyalty_reservation WHERE company_code IN ('RTL', 'FNB') RETURNING 1)
SELECT 'loyalty_reservation' AS tabel, count(*) AS dihapus FROM d;
WITH d AS (DELETE FROM platform.payment_intent WHERE company_code IN ('RTL', 'FNB') RETURNING 1)
SELECT 'payment_intent' AS tabel, count(*) AS dihapus FROM d;
WITH d AS (DELETE FROM platform.loyalty_ledger WHERE company_code IN ('RTL', 'FNB') RETURNING 1)
SELECT 'loyalty_ledger' AS tabel, count(*) AS dihapus FROM d;

-- Constraint company_code yang tersisa NOT VALID sekarang bisa divalidasi (gagal = masih ada kode asing lain).
DO $validate$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT c.conrelid::regclass AS tbl, c.conname
               FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
              WHERE n.nspname = 'platform' AND NOT c.convalidated AND c.conname LIKE '%company_code_check' LOOP
        EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I', r.tbl, r.conname);
        RAISE NOTICE 'divalidasi: %.%', r.tbl, r.conname;
    END LOOP;
END
$validate$;

SELECT count(*) AS company_code_check_not_valid
  FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
 WHERE n.nspname = 'platform' AND NOT c.convalidated AND c.conname LIKE '%company_code_check';
COMMIT;
