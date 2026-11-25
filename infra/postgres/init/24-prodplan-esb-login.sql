-- Migrasi G66 amandemen A1 (docs/04 §30.17.8, ADR-051 §8 Proposed): login per pengguna ESB.
--   * prodplan.actor        : identitas aktor = akun ESB (esb_env, esb_brand, username_norm); id = semua kolom *_sub.
--   * outbox.run_as_sub     : aktor yang tokennya dipakai worker (NULL = created_by_sub).
--   * outbox.status         : + 'needs_login' (TIDAK masuk ix_outbox_work; dilanjutkan lewat resume-posting).
-- Tidak ada token/password/role_id/cabang yang disimpan di PG (hanya di memori proses prodplan).
--
-- IDEMPOTEN. Penerapan PERTAMA membatalkan diri (RAISE EXCEPTION) bila masih ada rencana draft/submitted/approved atau
-- outbox non-terminal: identitas lama (uid Odoo) tidak bisa melanjutkan alur dengan aktor ESB baru -> operator
-- membatalkan rencana staging lama dulu (runbook prodplan). Setelah prodplan.actor ada, pemeriksaan itu dilewati agar
-- deploy berikutnya tidak gagal karena rencana baru.
--   * Volume baru: docker-entrypoint-initdb.d (setelah 23-prodplan-g66.sql).
--   * Volume lama: loop migrasi scripts/deploy.sh. Dev manual:
--       docker compose ... exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres \
--         -f /docker-entrypoint-initdb.d/24-prodplan-esb-login.sql

\set ON_ERROR_STOP on

\connect efn_prodplan
\set ON_ERROR_STOP on

BEGIN;

DO $$
BEGIN
  IF to_regclass('prodplan.actor') IS NULL AND (
       EXISTS (SELECT 1 FROM prodplan.plan WHERE status IN ('draft','submitted','approved'))
    OR EXISTS (SELECT 1 FROM prodplan.outbox
                WHERE status NOT IN ('confirmed','failed','deleted','delete_failed'))) THEN
    RAISE EXCEPTION 'prodplan 24: masih ada rencana draft/submitted/approved atau outbox non-terminal; batalkan dulu (runbook prodplan)';
  END IF;
END $$;

SET LOCAL ROLE efn_prodplan_owner;

CREATE TABLE IF NOT EXISTS prodplan.actor (
  id integer GENERATED ALWAYS AS IDENTITY (START WITH 1000000) PRIMARY KEY,  -- > uid Odoo lama, tidak bentrok
  esb_env text NOT NULL CHECK (esb_env IN ('staging','production')),
  esb_brand text NOT NULL CHECK (esb_brand IN ('pb','wtz','bacha','imd','cup')),
  username_norm varchar(64) NOT NULL CHECK (username_norm ~ '^[a-z0-9._@-]{1,64}$'),
  display_name varchar(64) NOT NULL,
  first_login_at timestamptz NOT NULL DEFAULT now(), last_login_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (esb_env, esb_brand, username_norm));

ALTER TABLE prodplan.outbox ADD COLUMN IF NOT EXISTS run_as_sub integer;  -- NULL = created_by_sub

-- CHECK status outbox + 'needs_login'. Nama constraint inline lama dibuat otomatis oleh PostgreSQL
-- (outbox_status_check); dicari lewat definisinya agar tidak bergantung pada nama, lalu diganti outbox_status_check_v2.
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'prodplan.outbox'::regclass AND contype = 'c'
              AND conname <> 'outbox_status_check_v2'
              AND pg_get_constraintdef(oid) LIKE '%delete_failed%' LOOP
    EXECUTE format('ALTER TABLE prodplan.outbox DROP CONSTRAINT %I', c.conname);
  END LOOP;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'prodplan.outbox'::regclass AND conname = 'outbox_status_check_v2') THEN
    ALTER TABLE prodplan.outbox ADD CONSTRAINT outbox_status_check_v2 CHECK (status IN (
      'pending','sending','sent','confirmed','unknown','failed',
      'delete_pending','deleting','deleted','delete_failed','needs_login'));
  END IF;
END $$;
-- ix_outbox_work (23-prodplan-g66.sql) sengaja TIDAK diubah: 'needs_login' bukan kerja worker.

-- GRANT (§30.6 pola sama): DML untuk aplikasi; tanpa TRUNCATE/REFERENCES/TRIGGER.
GRANT SELECT, INSERT, UPDATE, DELETE ON prodplan.actor TO efn_prodplan;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA prodplan TO efn_prodplan;

RESET ROLE;

COMMIT;
