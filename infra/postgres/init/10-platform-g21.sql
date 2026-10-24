-- Migrasi platform Gelombang 21 (docs/04 §13.1 no. 3–7, keputusan lead L-61/L-62; ADR-020 revisi, ADR-001 §poin).
-- Pemilik: services/api-gateway (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT).
--   * Volume baru: dijalankan otomatis setelah 01–09 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start. BFF G21 WAJIB migrasi ini
--     (INSERT ledger menulis `issuer_company_code`, INSERT loyalty_pos_earn menulis `policy_version`, tabel
--     `loyalty_policy`). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/10-platform-g21.sql
--
-- Isi:
-- 1. (§13.1 no. 3–4) `platform.loyalty_policy` — kebijakan poin per PT dari Odoo (`PUT /v1/pos/loyalty/policies/*`),
--    APPEND-ONLY (trigger tolak UPDATE/DELETE/TRUNCATE; efn_bff hanya SELECT/INSERT). UNIQUE (company_code, version);
--    `point_value_idr` hanya untuk `EFN` (L-62). `body_sha256` = sidik kanonik isi (409 policy_conflict). Berlaku pada
--    GREATEST(effective_from, received_at). Retensi: = data keuangan (10 th, Q-P2) — tidak dipurge BFF.
-- 2. (§13.1 no. 5) `loyalty_ledger.issuer_company_code` — PT PENERBIT lot per entri (earn = PT order; redeem/clawback/
--    forfeit = penerbit lot yang dikonsumsi FIFO; restore = penerbit lot asal). NULL = baris pra-G21 → dibaca sebagai
--    `company_code` (COALESCE). Migrasi data dev TIDAK diperlukan (§13.1 no. 5: dev reset).
-- 3. (§13.1 no. 6) indeks akrual harian per penerbit (`COALESCE(issuer_company_code, company_code), created_at`) dan
--    kedaluwarsa (`expires_at`).
-- 4. `loyalty_pos_earn.policy_version` — versi kebijakan yang menghitung earn (audit; NULL = env).
-- PII: tidak ada kolom PII baru (`approved_by_uid` = id user Odoo staf, tidak dibuka ke efn_readonly).
-- Risiko: R-B05 (rekonsiliasi poin), R-L01, R-S14 (empat mata kebijakan di Odoo).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Kebijakan poin per PT ─────────────
CREATE TABLE IF NOT EXISTS platform.loyalty_policy (
  id               bigserial   PRIMARY KEY,
  company_code     text        NOT NULL CHECK (company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM')),
  version          integer     NOT NULL CHECK (version >= 1),
  effective_from   timestamptz NOT NULL,
  approved_by_uid  integer     NOT NULL CHECK (approved_by_uid >= 1),
  params           jsonb       NOT NULL CHECK (jsonb_typeof(params) = 'object'),
  point_value_idr  integer     CHECK (point_value_idr IS NULL OR point_value_idr >= 1),
  body_sha256      char(64)    NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
  received_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_loyalty_policy_company_version UNIQUE (company_code, version),
  CONSTRAINT ck_loyalty_policy_point_value_global CHECK (point_value_idr IS NULL OR company_code = 'EFN')
);
CREATE INDEX IF NOT EXISTS ix_loyalty_policy_active ON platform.loyalty_policy (company_code, version DESC);

CREATE OR REPLACE FUNCTION platform.loyalty_policy_append_only() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'platform.loyalty_policy append-only: % ditolak (perubahan = versi baru)', TG_OP;
END $$;
DROP TRIGGER IF EXISTS trg_loyalty_policy_append_only ON platform.loyalty_policy;
CREATE TRIGGER trg_loyalty_policy_append_only BEFORE UPDATE OR DELETE ON platform.loyalty_policy
  FOR EACH ROW EXECUTE FUNCTION platform.loyalty_policy_append_only();
DROP TRIGGER IF EXISTS trg_loyalty_policy_no_truncate ON platform.loyalty_policy;
CREATE TRIGGER trg_loyalty_policy_no_truncate BEFORE TRUNCATE ON platform.loyalty_policy
  FOR EACH STATEMENT EXECUTE FUNCTION platform.loyalty_policy_append_only();

-- ───────────── 2. Penerbit lot di ledger ─────────────
ALTER TABLE platform.loyalty_ledger ADD COLUMN IF NOT EXISTS issuer_company_code text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_ledger_issuer'
                   AND conrelid = 'platform.loyalty_ledger'::regclass) THEN
    ALTER TABLE platform.loyalty_ledger ADD CONSTRAINT ck_loyalty_ledger_issuer
      CHECK (issuer_company_code IS NULL OR issuer_company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM'));
  END IF;
END $$;

-- ───────────── 3. Indeks akrual ─────────────
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_issuer_created
  ON platform.loyalty_ledger ((COALESCE(issuer_company_code, company_code)), created_at);
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_expires ON platform.loyalty_ledger (expires_at) WHERE expires_at IS NOT NULL;

-- ───────────── 4. Versi kebijakan per earn POS ─────────────
ALTER TABLE platform.loyalty_pos_earn ADD COLUMN IF NOT EXISTS policy_version integer;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_pos_earn_policy_version'
                   AND conrelid = 'platform.loyalty_pos_earn'::regclass) THEN
    ALTER TABLE platform.loyalty_pos_earn ADD CONSTRAINT ck_loyalty_pos_earn_policy_version
      CHECK (policy_version IS NULL OR policy_version >= 1);
  END IF;
END $$;

RESET ROLE;

-- Hak akses (least privilege, docs/04 §7.3). loyalty_policy: efn_bff hanya SELECT/INSERT (append-only); efn_readonly
-- (ETL/rekonsiliasi, anggota: efn_etl) kolom tanpa approved_by_uid. ledger/pos_earn: efn_bff sudah SELECT/INSERT tingkat
-- tabel (04) → kolom baru tercakup; efn_readonly diberi kolom baru secara eksplisit (GRANT level kolom).
REVOKE ALL ON platform.loyalty_policy FROM PUBLIC, efn_bff, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT ON platform.loyalty_policy TO efn_bff;
GRANT USAGE, SELECT ON SEQUENCE platform.loyalty_policy_id_seq TO efn_bff;
GRANT SELECT (id, company_code, version, effective_from, params, point_value_idr, received_at)
  ON platform.loyalty_policy TO efn_readonly;
GRANT SELECT (issuer_company_code) ON platform.loyalty_ledger TO efn_readonly;
GRANT SELECT (policy_version) ON platform.loyalty_pos_earn TO efn_readonly;

COMMIT;
