-- Migrasi platform v4.0/v4.1 — Fase 1 loyalty di POS (docs/04_SERVICE_CONTRACTS.md §9, §9.6, gelombang 13). Pemilik:
-- services/api-gateway (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT: gagal = tidak ada perubahan).
--   * Volume baru: dijalankan otomatis setelah 01–04 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: jalankan manual sebagai superuser (setelah 04) SEBELUM men-deploy BFF gelombang 13:
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/05-platform-loyalty-pos.sql
--     Aman diulang; BFF lama tetap berjalan (tabel baru belum dipakai).
--
-- Isi:
-- 1. `platform.loyalty_reservation` — reservasi redeem poin POS (§9.3, §9.5, §9.6): menahan poin sampai `hold_until`
--    (K-3, default 24 jam; TTL 15 menit hanya untuk UI) sampai
--    order disinkron (commit → entri ledger negatif) atau dibatalkan (release) / kedaluwarsa (job BFF).
--    Kolom di luar daftar minimum §9.5 (alasan):
--      company_code    — PT order (klaim token OTP) = PT penanggung entri ledger redeem (ADR-020).
--      token_jti       — `jti` discount_token pos_redeem: UNIQUE = token sekali pakai (K-4); retry reserve identik
--                        mengembalikan reservasi yang sama.
--      hold_until      — K-3 (§9.6): batas penahanan poin & commit (default 24 jam); `expires_at` = TTL UI 15 menit.
--      amount_total    — total order saat reserve (validasi LOYALTY_REDEEM_MAX_PCT_OF_ORDER; audit).
--      commit_idempotency_key, committed_at, updated_at — idempotensi commit & retensi.
-- 1b. `platform.loyalty_pos_earn` — catatan earn per order (K-6 `earn_pending`).
-- 2. Indeks ledger (source, source_ref) untuk reverse (§9.4: cari earn/redeem order asal).
-- 3. Hak akses: efn_bff DML minimum; efn_readonly kolom non-PII (tanpa member_hash, token_jti).
-- Tidak ada nomor HP (plaintext maupun terenkripsi) di tabel ini: commit membawa member_ref (K-3) yang diverifikasi
-- terhadap member_hash lalu dienkripsi langsung ke ledger.member_ref_enc.
--
-- Retensi: baris berstatus akhir (committed/released/expired) dihapus BFF setelah LOYALTY_RESERVATION_RETENTION_DAYS
-- (default 30) — reservasi adalah data transien (pseudonim member_hash); catatan finansial ada di loyalty_ledger. Erasure (DSAR) menghapus
-- seluruh reservasi subjek. ADR terkait: ADR-017 (redeem wajib OTP, nonaktif saat offline), ADR-020 (ledger),
-- ADR-022 (enkripsi, least privilege). Risiko: R-B05/R-B06 (fraud poin), R-L01 (UU PDP).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1. Reservasi redeem ─────────────
CREATE TABLE IF NOT EXISTS platform.loyalty_reservation (
  id                      uuid        PRIMARY KEY,
  member_hash             char(64)    NOT NULL CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  company_code            text        NOT NULL CHECK (company_code IN ('EFN','RTL','FNB','FARM')),
  order_ref               text        NOT NULL CHECK (char_length(order_ref) BETWEEN 1 AND 64),
  token_jti               text        NOT NULL CHECK (char_length(token_jti) BETWEEN 1 AND 64),
  points                  integer     NOT NULL CHECK (points > 0),
  value_idr               bigint      NOT NULL CHECK (value_idr >= 0),
  amount_total            numeric(16,2) NOT NULL CHECK (amount_total >= 0),
  status                  text        NOT NULL DEFAULT 'reserved'
                          CHECK (status IN ('reserved', 'committed', 'released', 'expired')),
  expires_at              timestamptz NOT NULL,          -- TTL UI (15 menit, informasi kasir)
  hold_until              timestamptz NOT NULL,          -- K-3: poin ditahan & boleh di-commit sampai (default +24 jam)
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now(),
  committed_at            timestamptz,
  commit_idempotency_key  text        CHECK (commit_idempotency_key IS NULL OR char_length(commit_idempotency_key) <= 128),
  CONSTRAINT ck_loyalty_reservation_committed
    CHECK ((status = 'committed') = (committed_at IS NOT NULL AND commit_idempotency_key IS NOT NULL)),
  CONSTRAINT ck_loyalty_reservation_hold CHECK (hold_until >= expires_at),
  CONSTRAINT uq_loyalty_reservation_commit_key UNIQUE (commit_idempotency_key),
  -- K-4: discount_token pos_redeem dikonsumsi saat reserve (sekali pakai, termasuk setelah release)
  CONSTRAINT uq_loyalty_reservation_token_jti UNIQUE (token_jti)
);

-- Satu order = satu reservasi aktif (re-reserve hanya setelah release/expired, dengan OTP baru).
CREATE UNIQUE INDEX IF NOT EXISTS uq_loyalty_reservation_order_active
  ON platform.loyalty_reservation (order_ref) WHERE status IN ('reserved', 'committed');
-- Poin tertahan per member (saldo tersedia = saldo ledger − reservasi aktif)
CREATE INDEX IF NOT EXISTS ix_loyalty_reservation_member_active
  ON platform.loyalty_reservation (member_hash, hold_until) WHERE status = 'reserved';
-- Job pelepas reservasi kedaluwarsa & retensi
CREATE INDEX IF NOT EXISTS ix_loyalty_reservation_expiry
  ON platform.loyalty_reservation (hold_until) WHERE status = 'reserved';
CREATE INDEX IF NOT EXISTS ix_loyalty_reservation_terminal
  ON platform.loyalty_reservation (updated_at) WHERE status <> 'reserved';
-- Erasure (DSAR) menghapus per member_hash
CREATE INDEX IF NOT EXISTS ix_loyalty_reservation_member
  ON platform.loyalty_reservation (member_hash);

-- ───────────── 1b. Catatan earn per order POS (K-6) ─────────────
-- Satu baris per order ber-member yang sudah diproses earn (TERMASUK earn 0 poin): reverse sebelum baris ini ada →
-- `409 earn_pending`. Ledger tidak bisa mencatat 0 poin (CHECK delta <> 0), maka tabel terpisah. Pseudonim
-- (member_hash), tanpa nomor HP. Retensi: LOYALTY_POS_EARN_RETENTION_DAYS (default 400 hari ≥ masa retur);
-- erasure menghapus baris subjek.
CREATE TABLE IF NOT EXISTS platform.loyalty_pos_earn (
  order_ref        text        PRIMARY KEY CHECK (char_length(order_ref) BETWEEN 1 AND 64),
  idempotency_key  text        NOT NULL UNIQUE CHECK (char_length(idempotency_key) <= 128),
  member_hash      char(64)    NOT NULL CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  company_code     text        NOT NULL CHECK (company_code IN ('EFN','RTL','FNB','FARM')),
  eligible_amount  numeric(16,2) NOT NULL CHECK (eligible_amount >= 0),
  points_earned    integer     NOT NULL CHECK (points_earned >= 0),
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_loyalty_pos_earn_member  ON platform.loyalty_pos_earn (member_hash);
CREATE INDEX IF NOT EXISTS ix_loyalty_pos_earn_created ON platform.loyalty_pos_earn (created_at);

-- ───────────── 2. Ledger: lookup reverse ─────────────
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_source_ref
  ON platform.loyalty_ledger (source, source_ref) WHERE source_ref IS NOT NULL;

RESET ROLE;

-- ───────────── 3. Hak akses ─────────────
-- efn_bff: reserve (INSERT), commit/release/expire (UPDATE status & kolom commit), retensi & erasure (DELETE),
-- Tidak ada UPDATE points/value_idr/member_hash/order_ref.
REVOKE ALL ON platform.loyalty_reservation FROM efn_bff, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, DELETE ON platform.loyalty_reservation TO efn_bff;
GRANT UPDATE (status, updated_at, committed_at, commit_idempotency_key)
  ON platform.loyalty_reservation TO efn_bff;

REVOKE ALL ON platform.loyalty_pos_earn FROM efn_bff, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, DELETE ON platform.loyalty_pos_earn TO efn_bff;   -- append; retensi & erasure (DELETE)
GRANT SELECT (order_ref, company_code, eligible_amount, points_earned, created_at) ON platform.loyalty_pos_earn TO efn_readonly;

-- efn_readonly (ETL/Text-to-SQL): kolom non-PII saja. order_ref opak (pos.order.uuid) setara ledger.source_ref.
GRANT SELECT (id, company_code, order_ref, points, value_idr, status, expires_at, hold_until, created_at, updated_at,
              committed_at)
  ON platform.loyalty_reservation TO efn_readonly;

COMMIT;
