-- Migrasi Gelombang 66 (docs/04 §30.6, ADR-051 Proposed): DB `efn_prodplan`, skema `prodplan` untuk service prodplan
-- (production planning CK di atas ESB). Pemilik file: efn-devsecops-engineer (DB/role/grant) mengikuti DDL kontrak
-- §30.6 apa adanya; perubahan kolom = revisi kontrak dulu.
--
-- IDEMPOTEN. `CREATE DATABASE` via \gexec di luar transaksi; isi skema dalam BEGIN ... COMMIT.
--   * Volume baru: dijalankan otomatis oleh docker-entrypoint-initdb.d (setelah 00-roles.sh).
--   * Volume lama: loop migrasi scripts/deploy.sh (semua [0-9][0-9]-*.sql >= 04; TIDAK masuk daftar wajib). Dev manual:
--       docker compose ... exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres \
--         -f /docker-entrypoint-initdb.d/23-prodplan-g66.sql
--
-- Role (pola SEC-39):
--   efn_prodplan_owner : NOLOGIN, pemilik skema + tabel; hanya dipakai migrasi lewat SET ROLE oleh superuser.
--   efn_prodplan       : login aplikasi (password dari 00-roles.sh, role opsional overlay docker-compose.prodplan.yml).
--                        Dibuat di sini sebagai NOLOGIN bila belum ada, agar GRANT selalu terpasang apa pun urutannya
--                        (deploy tanpa overlay -> role tetap NOLOGIN tanpa password = tidak bisa login).
--                        Hak: DML pada semua tabel kecuali audit_event (SELECT, INSERT), USAGE sequence. Bukan pemilik,
--                        bukan anggota owner -> tanpa DDL/ALTER/DISABLE TRIGGER/GRANT.
-- Tidak ada role lain (ETL/Metabase tidak membaca DB ini di G66, §30.6).

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'efn_prodplan_owner') THEN
    CREATE ROLE efn_prodplan_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'efn_prodplan') THEN
    CREATE ROLE efn_prodplan NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
  END IF;
END $$;
ALTER ROLE efn_prodplan_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

SELECT 'CREATE DATABASE efn_prodplan'
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'efn_prodplan')\gexec

REVOKE ALL ON DATABASE efn_prodplan FROM PUBLIC;
GRANT CONNECT ON DATABASE efn_prodplan TO efn_prodplan;

\connect efn_prodplan
\set ON_ERROR_STOP on

BEGIN;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS prodplan AUTHORIZATION efn_prodplan_owner;
ALTER SCHEMA prodplan OWNER TO efn_prodplan_owner;
REVOKE ALL ON SCHEMA prodplan FROM PUBLIC;
GRANT USAGE ON SCHEMA prodplan TO efn_prodplan;

SET LOCAL ROLE efn_prodplan_owner;

-- ── DDL docs/04 §30.6 (+ CHECK kolom umum company_code / esb_brand / esb_env di tabel induk) ─────────────────────────
CREATE TABLE IF NOT EXISTS prodplan.pack_rule (
  id bigserial PRIMARY KEY,
  company_code text NOT NULL CHECK (company_code IN ('PB','WTZ','BCF','BCR','IMD','CUP')),
  esb_brand text NOT NULL CHECK (esb_brand IN ('pb','wtz','bacha','imd','cup')),
  product_code varchar(40) NOT NULL, product_detail_id bigint, base_uom varchar(20) NOT NULL,
  pack_qty numeric(18,4) NOT NULL CHECK (pack_qty > 0),
  opened_shelf_life_hours integer CHECK (opened_shelf_life_hours BETWEEN 1 AND 720),
  notes varchar(500), active boolean NOT NULL DEFAULT true,
  created_by_sub integer NOT NULL, created_by_name varchar(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  updated_by_sub integer NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), row_version integer NOT NULL DEFAULT 1,
  UNIQUE (esb_brand, product_code));

CREATE TABLE IF NOT EXISTS prodplan.plan (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), plan_no varchar(32) NOT NULL,  -- PP-<CODE>-<YYYYMMDD>-<NNN>
  company_code text NOT NULL CHECK (company_code IN ('PB','WTZ','BCF','BCR','IMD','CUP')),
  esb_brand text NOT NULL CHECK (esb_brand IN ('pb','wtz','bacha','imd','cup')),
  esb_env text NOT NULL CHECK (esb_env IN ('staging','production')),
  branch_id integer NOT NULL, production_location_id integer NOT NULL, production_date date NOT NULL,
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','approved','posted','cancelled')),
  calc_status text NOT NULL DEFAULT 'none' CHECK (calc_status IN ('none','stale','fresh','failed')),
  calculated_at timestamptz, stock_snapshot_id uuid, notes varchar(1000),
  created_by_sub integer NOT NULL, created_by_name varchar(64) NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  submitted_by_sub integer, submitted_by_name varchar(64), submitted_at timestamptz,
  approved_by_sub integer, approved_by_name varchar(64), approved_at timestamptz,
  posted_by_sub integer, posted_by_name varchar(64), posted_at timestamptz,
  cancelled_by_sub integer, cancelled_at timestamptz, cancel_reason varchar(500),
  row_version integer NOT NULL DEFAULT 1, updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (esb_env, plan_no),
  CHECK (approved_by_sub IS NULL OR (approved_by_sub <> created_by_sub AND approved_by_sub <> submitted_by_sub)));
CREATE INDEX IF NOT EXISTS ix_plan_brand_date ON prodplan.plan (esb_env, esb_brand, production_date DESC);
CREATE INDEX IF NOT EXISTS ix_plan_status ON prodplan.plan (status) WHERE status IN ('draft','submitted','approved');

CREATE TABLE IF NOT EXISTS prodplan.plan_demand (
  id bigserial PRIMARY KEY, plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  product_detail_id bigint NOT NULL, product_code varchar(40) NOT NULL, product_name varchar(200) NOT NULL,
  bom_id bigint NOT NULL, uom_name varchar(20) NOT NULL, qty numeric(18,4) NOT NULL CHECK (qty > 0),
  source text NOT NULL CHECK (source IN ('pr','manual','suggestion')), pr_nums text[] NOT NULL DEFAULT '{}',
  suggestion_id bigint, created_by_sub integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (plan_id, product_detail_id, source));

CREATE TABLE IF NOT EXISTS prodplan.plan_unmapped (          -- baris PR tanpa BOM Assembly (tampil, bukan permintaan)
  id bigserial PRIMARY KEY, plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  pr_num varchar(40) NOT NULL, product_code varchar(40) NOT NULL, product_name varchar(200), qty numeric(18,4) NOT NULL,
  reason text NOT NULL CHECK (reason IN ('no_assembly_bom','unknown_product','uom_mismatch')));

CREATE TABLE IF NOT EXISTS prodplan.plan_production_line (
  id bigserial PRIMARY KEY, plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  level smallint NOT NULL CHECK (level BETWEEN 1 AND 10), bom_id bigint NOT NULL, product_detail_id bigint NOT NULL,
  product_code varchar(40) NOT NULL, product_name varchar(200) NOT NULL, uom_name varchar(20) NOT NULL,
  qty numeric(18,4) NOT NULL CHECK (qty > 0), is_demand boolean NOT NULL,
  materials jsonb NOT NULL,                     -- [{product_detail_id, product_code, qty_per_unit, total_qty}] (snapshot BOM)
  UNIQUE (plan_id, bom_id));

CREATE TABLE IF NOT EXISTS prodplan.plan_requirement (
  id bigserial PRIMARY KEY, plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  product_detail_id bigint NOT NULL, product_code varchar(40) NOT NULL, product_name varchar(200) NOT NULL,
  uom_name varchar(20) NOT NULL, need_qty numeric(18,4) NOT NULL, pack_qty numeric(18,4), packs integer,
  leftover_qty numeric(18,4), need_eff_qty numeric(18,4) NOT NULL, stock_prod_qty numeric(18,4) NOT NULL,
  transfer_qty numeric(18,4) NOT NULL DEFAULT 0, shortage_qty numeric(18,4) NOT NULL DEFAULT 0,
  flags text[] NOT NULL DEFAULT '{}',          -- uom_mismatch | no_stock_data | negative_stock | pack_rule_on_wip
  UNIQUE (plan_id, product_detail_id));

CREATE TABLE IF NOT EXISTS prodplan.plan_suggestion (
  id bigserial PRIMARY KEY, plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  material_detail_id bigint NOT NULL, bom_id bigint NOT NULL, product_detail_id bigint NOT NULL,
  extra_units integer NOT NULL CHECK (extra_units >= 1), uses_qty numeric(18,4) NOT NULL, leftover_qty numeric(18,4) NOT NULL,
  status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','accepted','rejected','superseded')),
  decided_by_sub integer, decided_at timestamptz);

CREATE TABLE IF NOT EXISTS prodplan.plan_transfer (
  id bigserial PRIMARY KEY, plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  doc_type text NOT NULL CHECK (doc_type IN ('simple_transfer','goods_transfer_request')),
  origin_branch_id integer NOT NULL, origin_location_id integer NOT NULL,
  dest_branch_id integer NOT NULL, dest_location_id integer NOT NULL,
  status text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','rejected','superseded')),
  decided_by_sub integer, decided_by_name varchar(64), decided_at timestamptz, reject_reason varchar(500),
  UNIQUE (plan_id, origin_location_id, dest_location_id),
  CHECK ((doc_type = 'simple_transfer') = (origin_branch_id = dest_branch_id)));
CREATE TABLE IF NOT EXISTS prodplan.plan_transfer_line (
  transfer_id bigint NOT NULL REFERENCES prodplan.plan_transfer(id) ON DELETE CASCADE,
  product_detail_id bigint NOT NULL, product_code varchar(40) NOT NULL, uom_name varchar(20) NOT NULL,
  qty numeric(18,4) NOT NULL CHECK (qty > 0), PRIMARY KEY (transfer_id, product_detail_id));

CREATE TABLE IF NOT EXISTS prodplan.stock_snapshot (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_code text NOT NULL CHECK (company_code IN ('PB','WTZ','BCF','BCR','IMD','CUP')),
  esb_brand text NOT NULL CHECK (esb_brand IN ('pb','wtz','bacha','imd','cup')),
  esb_env text NOT NULL CHECK (esb_env IN ('staging','production')),
  fetched_at timestamptz NOT NULL DEFAULT now(), esb_calls integer NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS prodplan.stock_snapshot_line (
  snapshot_id uuid NOT NULL REFERENCES prodplan.stock_snapshot(id) ON DELETE CASCADE,
  branch_id integer NOT NULL, location_id integer NOT NULL, product_detail_id bigint NOT NULL,
  stock_qty numeric(18,4) NOT NULL, source text NOT NULL CHECK (source IN ('bom_detail','branch_stock')),
  PRIMARY KEY (snapshot_id, branch_id, location_id, product_detail_id));
-- plan.stock_snapshot_id REFERENCES stock_snapshot(id) ON DELETE SET NULL (ditambahkan sekali; idempoten)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conname = 'plan_stock_snapshot_id_fkey' AND conrelid = 'prodplan.plan'::regclass) THEN
    ALTER TABLE prodplan.plan ADD CONSTRAINT plan_stock_snapshot_id_fkey
      FOREIGN KEY (stock_snapshot_id) REFERENCES prodplan.stock_snapshot(id) ON DELETE SET NULL;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS prodplan.job (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_code text NOT NULL CHECK (company_code IN ('PB','WTZ','BCF','BCR','IMD','CUP')),
  plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE CASCADE,
  kind text NOT NULL CHECK (kind IN ('pr_import','calculate')),
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
  params jsonb NOT NULL DEFAULT '{}', result jsonb, error_code varchar(40), esb_calls integer NOT NULL DEFAULT 0,
  requested_by_sub integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz);
CREATE UNIQUE INDEX IF NOT EXISTS ux_job_active ON prodplan.job (plan_id) WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS prodplan.outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_code text NOT NULL CHECK (company_code IN ('PB','WTZ','BCF','BCR','IMD','CUP')),
  esb_brand text NOT NULL CHECK (esb_brand IN ('pb','wtz','bacha','imd','cup')),
  esb_env text NOT NULL CHECK (esb_env IN ('staging','production')),
  plan_id uuid NOT NULL REFERENCES prodplan.plan(id) ON DELETE RESTRICT,
  seq smallint NOT NULL, phase text NOT NULL CHECK (phase IN ('transfers','production')),
  doc_kind text NOT NULL CHECK (doc_kind IN ('simple_transfer','goods_transfer_request','simple_manufacturing')),
  source_transfer_id bigint, source_line_id bigint,
  marker varchar(16) NOT NULL CHECK (marker ~ '^pp:[0-9a-f]{12}$'),
  request_body jsonb NOT NULL,                 -- body persis yang dikirim (tanpa kredensial)
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sending','sent','confirmed','unknown','failed',
                                                             'delete_pending','deleting','deleted','delete_failed')),
  attempts smallint NOT NULL DEFAULT 0, esb_doc_number varchar(40), esb_status_id integer,
  last_http_status integer, last_error_code varchar(40), next_attempt_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz, confirmed_at timestamptz, deleted_at timestamptz,
  created_by_sub integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (plan_id, seq), UNIQUE (marker));
-- ESB memakai ulang nomor dokumen setelah dokumennya dihapus (uji staging 24 Nov 2026: SM202611240001 - 1 terbit dua
-- kali). Keunikan hanya berlaku untuk dokumen yang masih hidup; baris `deleted` tetap menyimpan nomor lama untuk audit.
DROP INDEX IF EXISTS prodplan.ux_outbox_docnum;
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_docnum_live ON prodplan.outbox (esb_env, doc_kind, esb_doc_number)
  WHERE esb_doc_number IS NOT NULL AND status <> 'deleted';
CREATE INDEX IF NOT EXISTS ix_outbox_work ON prodplan.outbox (next_attempt_at)
  WHERE status IN ('pending','sending','sent','unknown','delete_pending','deleting');

CREATE TABLE IF NOT EXISTS prodplan.audit_event (
  id bigserial PRIMARY KEY,
  company_code text NOT NULL CHECK (company_code IN ('PB','WTZ','BCF','BCR','IMD','CUP')),
  plan_id uuid, actor_sub integer, actor_name varchar(64),
  action varchar(40) NOT NULL, detail jsonb NOT NULL DEFAULT '{}', request_id varchar(36), created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ix_audit_plan ON prodplan.audit_event (plan_id, created_at);

-- §30.10 vs §30.6: audit_event append-only untuk aplikasi (SELECT, INSERT), tetapi purge retensi 1825 hari tetap harus
-- jalan. Satu-satunya jalur hapus = fungsi SECURITY DEFINER milik owner ini, yang hanya menghapus baris lebih tua dari
-- 1825 hari (batas tetap di SQL, bukan parameter) -> aplikasi tidak bisa menghapus/menyunting jejak audit yang masih
-- dalam masa retensi. Dipanggil job purge harian prodplan: SELECT prodplan.purge_expired_audit_events();
CREATE OR REPLACE FUNCTION prodplan.purge_expired_audit_events() RETURNS bigint
  LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, prodplan AS $$
  WITH d AS (DELETE FROM prodplan.audit_event WHERE created_at < now() - interval '1825 days' RETURNING 1)
  SELECT count(*) FROM d;
$$;
REVOKE ALL ON FUNCTION prodplan.purge_expired_audit_events() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION prodplan.purge_expired_audit_events() TO efn_prodplan;

-- ── GRANT (§30.6): DML semua tabel kecuali audit_event (SELECT, INSERT); USAGE sequence. Tanpa TRUNCATE/REFERENCES/
--    TRIGGER, tanpa ALTER DEFAULT PRIVILEGES (tabel baru = migrasi baru dengan GRANT eksplisit). ──────────────────────
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA prodplan TO efn_prodplan;
REVOKE UPDATE, DELETE, TRUNCATE ON prodplan.audit_event FROM efn_prodplan;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA prodplan TO efn_prodplan;

RESET ROLE;

COMMIT;
