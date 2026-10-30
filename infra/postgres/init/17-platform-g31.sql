-- Migrasi platform Gelombang 31 (docs/04 §20): engine MGM (member-get-member, K25-10, ADR-009 opsi A).
-- Pemilik: services/api-gateway (konteks loyalty, src/loyalty/mgm). IDEMPOTEN dan transaksional.
--   * Volume baru: dijalankan otomatis setelah 01–16 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (17 masuk daftar WAJIB:
--     BFF G31 menulis `mgm_source`/`mgm_reversal` di transaksi earn/reverse POS & online bila MGM aktif). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/17-platform-g31.sql
--
-- Isi:
-- 1. `mgm_policy`   — kebijakan MGM per PT dari Odoo (§20.1), APPEND-ONLY (pola `loyalty_policy` G21).
-- 2. `mgm_member`   — kode referral milik member + tautan ke referrer (§20.2). Pseudonim (member_hash), tanpa PII.
-- 3. `mgm_source`   — transaksi pembeli ber-referrer yang memicu akrual (§20.3); basis Rupiah, tanpa baris produk.
-- 4. `mgm_reward`   — reward per (sumber, level): pending → settled | cancelled | capped (§20.3–§20.5).
-- 5. `mgm_reversal` — fakta retur sumber (§20.4) + `mgm_reversal_applied` (diterapkan sekali per reward).
-- Hak: efn_bff DML seperlunya; efn_readonly/efn_ocr/efn_etl TIDAK diberi akses (pseudonim + graf relasi).
-- Retensi: mgm_policy/mgm_reward/mgm_source/mgm_reversal = data keuangan (10 th, Q-P2), tidak dipurge BFF.
-- Risiko: R-B03 (margin: cap), R-B06 (fraud: satu referrer, tanpa siklus, jendela klaim), R-L02, R-O05 (clawback).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Kebijakan MGM per PT ─────────────
CREATE TABLE IF NOT EXISTS platform.mgm_policy (
  id               bigserial   PRIMARY KEY,
  company_code     text        NOT NULL CHECK (company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM')),
  version          integer     NOT NULL CHECK (version >= 1),
  effective_from   timestamptz NOT NULL,
  approved_by_uid  integer     NOT NULL CHECK (approved_by_uid >= 1),
  params           jsonb       NOT NULL CHECK (jsonb_typeof(params) = 'object'),
  body_sha256      char(64)    NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
  received_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_mgm_policy_company_version UNIQUE (company_code, version)
);
CREATE INDEX IF NOT EXISTS ix_mgm_policy_active ON platform.mgm_policy (company_code, version DESC);

CREATE OR REPLACE FUNCTION platform.mgm_policy_append_only() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'platform.mgm_policy append-only: % ditolak (perubahan = versi baru)', TG_OP;
END $$;
DROP TRIGGER IF EXISTS trg_mgm_policy_append_only ON platform.mgm_policy;
CREATE TRIGGER trg_mgm_policy_append_only BEFORE UPDATE OR DELETE ON platform.mgm_policy
  FOR EACH ROW EXECUTE FUNCTION platform.mgm_policy_append_only();
DROP TRIGGER IF EXISTS trg_mgm_policy_no_truncate ON platform.mgm_policy;
CREATE TRIGGER trg_mgm_policy_no_truncate BEFORE TRUNCATE ON platform.mgm_policy
  FOR EACH STATEMENT EXECUTE FUNCTION platform.mgm_policy_append_only();

-- ───────────── 2. Kode & relasi referral ─────────────
-- Satu baris per member yang pernah membuka /v1/me/referral atau mengklaim kode. `referrer_hash` NULL + `linked_at`
-- terisi = upline dihapus (erasure §20.7): tautan tidak bisa diganti dan rantai berhenti di sini.
CREATE TABLE IF NOT EXISTS platform.mgm_member (
  member_hash    char(64)    PRIMARY KEY CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  code           char(8)     NOT NULL CHECK (code ~ '^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$'),
  referrer_hash  char(64)    CHECK (referrer_hash IS NULL OR referrer_hash ~ '^[0-9a-f]{64}$'),
  linked_at      timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_mgm_member_code UNIQUE (code),
  CONSTRAINT ck_mgm_member_not_self CHECK (referrer_hash IS NULL OR referrer_hash <> member_hash),
  CONSTRAINT ck_mgm_member_link CHECK (referrer_hash IS NULL OR linked_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_mgm_member_referrer ON platform.mgm_member (referrer_hash) WHERE referrer_hash IS NOT NULL;

-- ───────────── 3. Sumber akrual ─────────────
CREATE TABLE IF NOT EXISTS platform.mgm_source (
  source_kind    text        NOT NULL CHECK (source_kind IN ('pos', 'online')),
  source_ref     text        NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 64),
  company_code   text        NOT NULL CHECK (company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM')),
  buyer_hash     char(64)    NOT NULL CHECK (buyer_hash ~ '^[0-9a-f]{64}$'),
  basis_idr      bigint      NOT NULL CHECK (basis_idr >= 0),
  occurred_at    timestamptz NOT NULL,
  processed_at   timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_kind, source_ref)
);
CREATE INDEX IF NOT EXISTS ix_mgm_source_buyer ON platform.mgm_source (buyer_hash);

-- ───────────── 4. Reward per level ─────────────
CREATE TABLE IF NOT EXISTS platform.mgm_reward (
  id               uuid        PRIMARY KEY,
  source_kind      text        NOT NULL,
  source_ref       text        NOT NULL,
  level            smallint    NOT NULL CHECK (level BETWEEN 1 AND 5),
  referrer_hash    char(64)    NOT NULL CHECK (referrer_hash ~ '^[0-9a-f]{64}$'),
  company_code     text        NOT NULL CHECK (company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM')),
  points           integer     NOT NULL CHECK (points > 0),
  reversed_points  integer     NOT NULL DEFAULT 0 CHECK (reversed_points >= 0),
  settled_points   integer     NOT NULL DEFAULT 0 CHECK (settled_points >= 0),
  clawed_points    integer     NOT NULL DEFAULT 0 CHECK (clawed_points >= 0),
  status           text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'settled', 'cancelled', 'capped')),
  status_reason    text        CHECK (status_reason IS NULL OR status_reason ~ '^[a-z_]{1,40}$'),
  policy_version   integer     NOT NULL CHECK (policy_version >= 1),
  point_value_idr  integer     NOT NULL CHECK (point_value_idr >= 1),
  cap_points       integer     NOT NULL CHECK (cap_points >= 1),
  occurred_at      timestamptz NOT NULL,
  settle_after     timestamptz NOT NULL,
  settled_at       timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_mgm_reward_source_level UNIQUE (source_kind, source_ref, level),
  CONSTRAINT fk_mgm_reward_source FOREIGN KEY (source_kind, source_ref)
    REFERENCES platform.mgm_source (source_kind, source_ref) ON DELETE RESTRICT,
  CONSTRAINT ck_mgm_reward_amounts CHECK (reversed_points <= points AND settled_points <= points
                                          AND clawed_points <= settled_points)
);
CREATE INDEX IF NOT EXISTS ix_mgm_reward_due ON platform.mgm_reward (settle_after) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS ix_mgm_reward_referrer_month ON platform.mgm_reward (referrer_hash, company_code, occurred_at);

-- ───────────── 5. Retur sumber ─────────────
CREATE TABLE IF NOT EXISTS platform.mgm_reversal (
  source_kind    text          NOT NULL,
  source_ref     text          NOT NULL,
  reverse_key    text          NOT NULL CHECK (length(reverse_key) BETWEEN 1 AND 200),
  ratio          numeric(9, 6) NOT NULL CHECK (ratio > 0 AND ratio <= 1),
  created_at     timestamptz   NOT NULL DEFAULT now(),
  PRIMARY KEY (source_kind, source_ref, reverse_key),
  CONSTRAINT fk_mgm_reversal_source FOREIGN KEY (source_kind, source_ref)
    REFERENCES platform.mgm_source (source_kind, source_ref) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS platform.mgm_reversal_applied (
  reward_id      uuid        NOT NULL REFERENCES platform.mgm_reward (id) ON DELETE RESTRICT,
  reverse_key    text        NOT NULL,
  applied_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (reward_id, reverse_key)
);

-- ───────────── Hak akses ─────────────
REVOKE ALL ON platform.mgm_policy, platform.mgm_member, platform.mgm_source, platform.mgm_reward,
              platform.mgm_reversal, platform.mgm_reversal_applied
  FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT ON platform.mgm_policy TO efn_bff;
GRANT USAGE ON SEQUENCE platform.mgm_policy_id_seq TO efn_bff;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.mgm_member TO efn_bff;
GRANT SELECT, INSERT, UPDATE ON platform.mgm_source, platform.mgm_reward, platform.mgm_reversal,
                                platform.mgm_reversal_applied TO efn_bff;

RESET ROLE;

COMMIT;
