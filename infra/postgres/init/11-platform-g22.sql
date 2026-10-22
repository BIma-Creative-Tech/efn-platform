-- Migrasi platform Gelombang 22 (docs/04 §14.A–§14.E, keputusan lead L-70..L-74; ADR-032 Accepted opsi B, ADR-020).
-- Pemilik: services/api-gateway (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya ADD/CREATE +
-- ganti CHECK (§14.E.1) — tidak ada DROP kolom/tabel.
--   * Volume baru: dijalankan otomatis setelah 01–10 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (11 masuk daftar WAJIB).
--     BFF G22 WAJIB migrasi ini (tabel fnb_*, kolom poin online, loyalty_member_activity/expiry_notice, dsar retention).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/11-platform-g22.sql
--
-- Isi:
-- 1. (§14.A.8 + L-70) QR meja: `fnb_table_session`, `fnb_submission` (satu kiriman = satu pos.order draft baru →
--    `order_uuid`/`order_state`/earn per kiriman), `fnb_menu_item`, `fnb_menu_sync`. Token sesi meja TIDAK disimpan (fts1
--    dihitung ulang dari id + exp). Catatan pesanan TIDAK disimpan (hanya `has_note` di `lines`).
-- 2. (§14.C.6) Poin online: `loyalty_reservation.channel/part_id` (token_jti NULL hanya untuk online),
--    `online_order.amount_points_value/amount_cash`, kolom poin per bagian, `online_refund.points_value/cash_amount`.
--    `payment_intent_portion.amount` = porsi TUNAI (tanpa perubahan skema).
-- 3. (§14.D.2–D.3) `loyalty_member_activity` (+ backfill dari ledger & member) dan `loyalty_expiry_notice`.
-- 4. (§14.D.7) `dsar_request.type` + 'retention'; `member_id` boleh NULL hanya untuk 'retention'; indeks unik "satu hapus
--    aktif" mencakup retention (per member_id dan per member_hash).
-- Kolom di luar daftar minimum kontrak (alasan):
--   fnb_table_session.open_request_sha256 — idempotensi `POST /v1/pos/fnb/table-sessions` (body beda → 409).
--   fnb_table_session.updated_at          — retensi/observability.
--   fnb_submission.display                — nama produk + qty dari Odoo (non-PII) untuk replay respons & GET orders.
--   fnb_submission.order_uuid/order_state/eligible_amount/earn_status/points_earned/last_seq — L-70 (status & bayar per order).
--   fnb_menu_item.company_code/name/category_id — irisan menu ↔ katalog kanal, overlay nama (A.6).
--   loyalty_expiry_notice.updated_at     — audit transisi status.
-- PII (UU PDP): `fnb_table_session.member_id` (pseudonim UUID; dilepas FNB_SESSION_MEMBER_LINK_DAYS=30 hari setelah sesi
-- ditutup, NULL saat erasure); `loyalty_member_activity.member_hash`/`loyalty_expiry_notice.member_hash` (pseudonim; dihapus
-- saat erasure). Retensi (§14.E.5, usulan paket DPO): fnb_* 180 hari, notice 400 hari, activity selama subjek ada.
-- Hak akses: efn_bff DML minimum; TIDAK ada DELETE ledger; efn_readonly/efn_etl/efn_ocr tanpa akses tabel baru.
-- Risiko: R-S03 (token meja), R-B01/R-B05 (fraud poin), R-L01 (PDP), R-T10 (saga).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 1a. Sesi meja QR ─────────────
CREATE TABLE IF NOT EXISTS platform.fnb_table_session (
  id                    uuid        PRIMARY KEY,
  company_code          text        NOT NULL CHECK (company_code IN ('PB', 'BCF')),
  config_id             integer     NOT NULL CHECK (config_id > 0),
  table_id              integer     NOT NULL CHECK (table_id > 0),
  table_label           text        NOT NULL CHECK (char_length(table_label) BETWEEN 1 AND 32),
  -- Odoo E-27 tidak mengirim order_uuid saat membuka sesi (key fnb_open:<config>:<table>:<n>) → NULL; earn memakai
  -- order_uuid dari event `session_closed.close.orders[]` (L-80)
  order_uuid            text        NULL CHECK (order_uuid IS NULL OR char_length(order_uuid) BETWEEN 1 AND 64),
  open_idempotency_key  text        NOT NULL CHECK (char_length(open_idempotency_key) BETWEEN 1 AND 128),
  open_request_sha256   char(64)    NOT NULL CHECK (open_request_sha256 ~ '^[0-9a-f]{64}$'),
  print_seq             integer     NOT NULL DEFAULT 1 CHECK (print_seq >= 1),
  status                text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'expired')),
  close_reason          text        NULL CHECK (close_reason IS NULL OR close_reason IN
                          ('paid', 'cancelled', 'closed_by_staff', 'reissued', 'expired', 'ops')),
  opened_at             timestamptz NOT NULL DEFAULT now(),
  expires_at            timestamptz NOT NULL,
  closed_at             timestamptz,
  member_id             uuid        NULL REFERENCES platform.member (id),
  member_claimed_at     timestamptz,
  member_unlinked_at    timestamptz,
  earn_status           text        NOT NULL DEFAULT 'none' CHECK (earn_status IN ('none', 'earned', 'skipped', 'limited', 'conflict', 'pos_member')),
  -- r15 K3: event session_closed sudah diproses BFF (earn di transaksi yang sama); NULL → reverse QR = earn_pending
  close_event_at        timestamptz NULL,
  submissions_count     integer     NOT NULL DEFAULT 0 CHECK (submissions_count >= 0),
  items_count           integer     NOT NULL DEFAULT 0 CHECK (items_count >= 0),
  last_seq              integer     NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_fnb_table_session_open_key UNIQUE (open_idempotency_key),
  CONSTRAINT ck_fnb_table_session_closed CHECK ((status = 'open') = (closed_at IS NULL)),
  CONSTRAINT ck_fnb_table_session_unlinked CHECK (member_unlinked_at IS NULL OR member_id IS NULL),
  -- A.2: TTL maksimum 4 jam (FNB_TABLE_SESSION_TTL_SECONDS ≤ 14 400)
  CONSTRAINT ck_fnb_table_session_ttl CHECK (expires_at > opened_at AND expires_at <= opened_at + interval '4 hours')
);
-- A.4: satu sesi `open` per meja (config, table)
CREATE UNIQUE INDEX IF NOT EXISTS uq_fnb_table_session_table_open
  ON platform.fnb_table_session (config_id, table_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS ix_fnb_table_session_expiry ON platform.fnb_table_session (expires_at) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS ix_fnb_table_session_closed ON platform.fnb_table_session (closed_at) WHERE closed_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fnb_table_session_member ON platform.fnb_table_session (member_id) WHERE member_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fnb_table_session_order ON platform.fnb_table_session (order_uuid);

-- ───────────── 1b. Kiriman pesanan (L-70: satu kiriman = satu pos.order draft baru) ─────────────
CREATE TABLE IF NOT EXISTS platform.fnb_submission (
  id                uuid        PRIMARY KEY,
  session_id        uuid        NOT NULL REFERENCES platform.fnb_table_session (id) ON DELETE CASCADE,
  idempotency_key   text        NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{16,128}$'),
  request_sha256    char(64)    NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  -- [{product_id, qty, attribute_value_ids, has_note}] — TANPA teks catatan (D-22-6)
  lines             jsonb       NOT NULL CHECK (jsonb_typeof(lines) = 'array'),
  items_count       integer     NOT NULL CHECK (items_count >= 1),
  -- [{product_id, name, qty}] dari Odoo (non-PII) + subtotal indikatif
  display           jsonb       NULL CHECK (display IS NULL OR jsonb_typeof(display) = 'object'),
  status            text        NOT NULL CHECK (status IN ('submitting', 'accepted', 'pending_confirmation', 'preparing', 'ready',
                                                           'served', 'rejected', 'cancelled', 'failed')),
  odoo_state        text        NULL CHECK (odoo_state IS NULL OR char_length(odoo_state) <= 32),
  order_uuid        text        NULL CHECK (order_uuid IS NULL OR char_length(order_uuid) BETWEEN 1 AND 64),
  order_state       text        NULL CHECK (order_state IS NULL OR order_state IN ('draft', 'paid', 'cancelled')),
  eligible_amount   numeric(14,2) NULL CHECK (eligible_amount IS NULL OR eligible_amount >= 0),
  earn_status       text        NOT NULL DEFAULT 'none' CHECK (earn_status IN ('none', 'earned', 'skipped', 'limited', 'conflict', 'pos_member')),
  points_earned     integer     NOT NULL DEFAULT 0 CHECK (points_earned >= 0),
  last_seq          integer     NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_fnb_submission_key UNIQUE (session_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_fnb_submission_session ON platform.fnb_submission (session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_fnb_submission_order ON platform.fnb_submission (order_uuid) WHERE order_uuid IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fnb_submission_submitting ON platform.fnb_submission (updated_at) WHERE status = 'submitting';

-- r15 (volume dev yang sudah menjalankan versi awal file ini; idempoten): kolom close_event_at + earn_status 'pos_member'
ALTER TABLE platform.fnb_table_session ADD COLUMN IF NOT EXISTS close_event_at timestamptz NULL;
DO $$
BEGIN
  ALTER TABLE platform.fnb_table_session DROP CONSTRAINT IF EXISTS fnb_table_session_earn_status_check;
  ALTER TABLE platform.fnb_table_session ADD CONSTRAINT fnb_table_session_earn_status_check
    CHECK (earn_status IN ('none', 'earned', 'skipped', 'limited', 'conflict', 'pos_member'));
  ALTER TABLE platform.fnb_submission DROP CONSTRAINT IF EXISTS fnb_submission_earn_status_check;
  ALTER TABLE platform.fnb_submission ADD CONSTRAINT fnb_submission_earn_status_check
    CHECK (earn_status IN ('none', 'earned', 'skipped', 'limited', 'conflict', 'pos_member'));
END $$;
-- r15 K3: sesi pendahulu yang di-reissue di meja yang sama (markCloseProcessed)
CREATE INDEX IF NOT EXISTS ix_fnb_table_session_close_pending ON platform.fnb_table_session (config_id, table_id)
  WHERE close_event_at IS NULL;

-- ───────────── 1c. Overlay menu per config (A.6, D-22-2) ─────────────
CREATE TABLE IF NOT EXISTS platform.fnb_menu_item (
  config_id     integer     NOT NULL CHECK (config_id > 0),
  product_id    integer     NOT NULL CHECK (product_id > 0),
  company_code  text        NOT NULL CHECK (company_code IN ('PB', 'BCF')),
  name          text        NULL CHECK (name IS NULL OR char_length(name) <= 256),
  category_id   integer     NULL,
  price_excl    numeric(14,2) NOT NULL CHECK (price_excl >= 0),
  tax_labels    jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(tax_labels) = 'array'),
  available     boolean     NOT NULL DEFAULT true,
  attributes    jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(attributes) = 'array'),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  PRIMARY KEY (config_id, product_id)
);
CREATE INDEX IF NOT EXISTS ix_fnb_menu_item_active ON platform.fnb_menu_item (config_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.fnb_menu_sync (
  config_id        integer     PRIMARY KEY CHECK (config_id > 0),
  company_code     text        NOT NULL CHECK (company_code IN ('PB', 'BCF')),
  outlet_name      text        NULL CHECK (outlet_name IS NULL OR char_length(outlet_name) <= 128),
  qr_enabled       boolean     NOT NULL DEFAULT false,
  confirm_mode     text        NOT NULL DEFAULT 'waiter' CHECK (confirm_mode IN ('auto', 'waiter')),
  since            text        NULL CHECK (since IS NULL OR char_length(since) <= 64),
  cursor           text        NULL CHECK (cursor IS NULL OR char_length(cursor) <= 512),
  last_ok_at       timestamptz,
  last_attempt_at  timestamptz,
  last_error_code  text        NULL CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z_]{1,40}$')
);

-- ───────────── 2a. Reservasi poin online (§14.C.6) ─────────────
ALTER TABLE platform.loyalty_reservation ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'pos';
ALTER TABLE platform.loyalty_reservation ADD COLUMN IF NOT EXISTS part_id uuid NULL;
ALTER TABLE platform.loyalty_reservation ALTER COLUMN token_jti DROP NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_reservation_channel'
                   AND conrelid = 'platform.loyalty_reservation'::regclass) THEN
    ALTER TABLE platform.loyalty_reservation ADD CONSTRAINT ck_loyalty_reservation_channel
      CHECK (channel IN ('pos', 'online') AND ((channel = 'online') = (part_id IS NOT NULL)));
  END IF;
  -- token_jti NULL HANYA untuk online (POS tetap wajib token OTP sekali pakai, K-4)
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_reservation_jti'
                   AND conrelid = 'platform.loyalty_reservation'::regclass) THEN
    ALTER TABLE platform.loyalty_reservation ADD CONSTRAINT ck_loyalty_reservation_jti
      CHECK (token_jti IS NOT NULL OR channel = 'online');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_loyalty_reservation_part'
                   AND conrelid = 'platform.loyalty_reservation'::regclass) THEN
    ALTER TABLE platform.loyalty_reservation ADD CONSTRAINT uq_loyalty_reservation_part UNIQUE (part_id);
  END IF;
END $$;

-- ───────────── 2b. Kolom poin order/bagian/refund online ─────────────
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS amount_points_value bigint NOT NULL DEFAULT 0;
ALTER TABLE platform.online_order ADD COLUMN IF NOT EXISTS amount_cash numeric(14,2) NULL;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS points_redeemed integer NOT NULL DEFAULT 0;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS points_value_idr bigint NOT NULL DEFAULT 0;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS amount_cash numeric(14,2) NULL;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS loyalty_reservation_id uuid NULL;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS eligible_amount numeric(14,2) NULL;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS earn_status text NOT NULL DEFAULT 'none';
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS points_earned integer NOT NULL DEFAULT 0;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS points_restored_value bigint NOT NULL DEFAULT 0;
ALTER TABLE platform.online_order_part ADD COLUMN IF NOT EXISTS kitchen_started_at timestamptz;
ALTER TABLE platform.online_refund ADD COLUMN IF NOT EXISTS points_value bigint NOT NULL DEFAULT 0;
ALTER TABLE platform.online_refund ADD COLUMN IF NOT EXISTS cash_amount numeric(14,2) NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_order_points'
                   AND conrelid = 'platform.online_order'::regclass) THEN
    ALTER TABLE platform.online_order ADD CONSTRAINT ck_online_order_points CHECK (
      amount_points_value >= 0
      AND (amount_cash IS NULL OR (amount_cash >= 1 AND amount_cash = trunc(amount_cash))));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_order_part_points'
                   AND conrelid = 'platform.online_order_part'::regclass) THEN
    -- C.6: porsi poin < amount_payable (porsi tunai ≥ Rp1); semua nominal integer Rupiah
    ALTER TABLE platform.online_order_part ADD CONSTRAINT ck_online_order_part_points CHECK (
      points_redeemed >= 0 AND points_value_idr >= 0 AND points_value_idr < amount_payable
      AND (amount_cash IS NULL OR (amount_cash >= 1 AND amount_cash = trunc(amount_cash)))
      AND earn_status IN ('none', 'pending', 'earned', 'skipped', 'limited')
      AND points_earned >= 0 AND points_restored_value >= 0 AND points_restored_value <= points_value_idr
      AND (eligible_amount IS NULL OR eligible_amount >= 0));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_refund_points'
                   AND conrelid = 'platform.online_refund'::regclass) THEN
    ALTER TABLE platform.online_refund ADD CONSTRAINT ck_online_refund_points CHECK (
      points_value >= 0 AND (cash_amount IS NULL OR (cash_amount >= 0 AND cash_amount = trunc(cash_amount))));
  END IF;
END $$;

-- ───────────── 3a. Aktivitas member (§14.D.2) ─────────────
CREATE TABLE IF NOT EXISTS platform.loyalty_member_activity (
  member_hash       char(64)    PRIMARY KEY CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  last_activity_at  timestamptz NOT NULL,
  last_kind         text        NULL CHECK (last_kind IS NULL OR last_kind ~ '^[a-z_]{1,40}$'),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_loyalty_member_activity_last ON platform.loyalty_member_activity (last_activity_at);

-- Backfill (idempoten): MAX(created_at) entri ledger AKTIVITAS (earn/redeem/migrasi/Price Hunter/online) per member_hash
-- ∪ member.created_at. Entri ter-erasure (tombstone) dikecualikan. Clawback/restore/forfeit/expire bukan aktivitas.
INSERT INTO platform.loyalty_member_activity (member_hash, last_activity_at, last_kind, updated_at)
SELECT h, MAX(at), 'backfill', now()
  FROM (
    SELECT l.member_ref_hash AS h, l.created_at AS at
      FROM platform.loyalty_ledger l
     WHERE l.erased_at IS NULL
       AND (l.source IN ('pos_earn', 'pos_redeem', 'migration_opening', 'price_hunter')
            OR (l.source = 'online' AND l.reason IN ('online_earn', 'online_redeem')))
    UNION ALL
    SELECT m.member_hash AS h, m.created_at AS at FROM platform.member m WHERE m.deleted_at IS NULL
  ) x
 GROUP BY h
ON CONFLICT (member_hash) DO UPDATE
  SET last_activity_at = GREATEST(platform.loyalty_member_activity.last_activity_at, EXCLUDED.last_activity_at);

-- ───────────── 3b. Pemberitahuan hangus (§14.D.3 no. 2) ─────────────
CREATE TABLE IF NOT EXISTS platform.loyalty_expiry_notice (
  id           uuid        PRIMARY KEY,
  member_hash  char(64)    NOT NULL CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  anchor_at    timestamptz NOT NULL,
  due_at       timestamptz NOT NULL,
  notified_at  timestamptz NOT NULL,
  status       text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'superseded', 'expired', 'cancelled')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_loyalty_expiry_notice_anchor UNIQUE (member_hash, anchor_at),
  CONSTRAINT ck_loyalty_expiry_notice_due CHECK (due_at >= anchor_at)
);
CREATE INDEX IF NOT EXISTS ix_loyalty_expiry_notice_due ON platform.loyalty_expiry_notice (due_at) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS ix_loyalty_expiry_notice_member ON platform.loyalty_expiry_notice (member_hash);
CREATE INDEX IF NOT EXISTS ix_loyalty_expiry_notice_created ON platform.loyalty_expiry_notice (created_at);

-- ───────────── 3c. Bukti pemberitahuan hangus tanpa identitas (L-76) ─────────────
-- Satu baris per pesan WA yang BERHASIL dikirim consumer notify.push (source loyalty_expiry). TANPA member_hash/nomor:
-- `notice_id` = id acak notice (setelah erasure baris notice dihapus → id ini tidak tertaut ke subjek mana pun).
-- TIDAK ikut dihapus anonimisasi/erasure (bukti program bahwa pemberitahuan dikirim). Retensi usulan 5 tahun
-- (LOYALTY_EXPIRY_PROOF_RETENTION_DAYS=1825, PLACEHOLDER DPO).
CREATE TABLE IF NOT EXISTS platform.loyalty_expiry_notice_proof (
  id           uuid        PRIMARY KEY,
  notice_id    uuid        NOT NULL,
  category     text        NOT NULL CHECK (category ~ '^[a-z_]{1,40}$'),
  -- r15 SEC-171: 'in_app' = WA TIDAK dikirim (consent marketing tidak ada); info hangus hanya di aplikasi (D.6)
  channel      text        NOT NULL DEFAULT 'whatsapp' CHECK (channel IN ('whatsapp', 'in_app')),
  expire_date  date        NOT NULL,
  sent_at      timestamptz NOT NULL,
  CONSTRAINT uq_loyalty_expiry_notice_proof_notice UNIQUE (notice_id)
);
CREATE INDEX IF NOT EXISTS ix_loyalty_expiry_notice_proof_sent ON platform.loyalty_expiry_notice_proof (sent_at);
DO $$
BEGIN
  ALTER TABLE platform.loyalty_expiry_notice_proof DROP CONSTRAINT IF EXISTS loyalty_expiry_notice_proof_channel_check;
  ALTER TABLE platform.loyalty_expiry_notice_proof ADD CONSTRAINT loyalty_expiry_notice_proof_channel_check
    CHECK (channel IN ('whatsapp', 'in_app'));
END $$;

-- Akrual inactivity_expire per tanggal (§14.D.5): indeks (issuer, created_at) G21 sudah ada; filter reason murah.
CREATE INDEX IF NOT EXISTS ix_loyalty_ledger_inactivity ON platform.loyalty_ledger (created_at) WHERE reason = 'inactivity_expire';

-- ───────────── 4. DSAR retention (§14.D.7) ─────────────
ALTER TABLE platform.dsar_request ALTER COLUMN member_id DROP NOT NULL;
DO $$
BEGIN
  ALTER TABLE platform.dsar_request DROP CONSTRAINT IF EXISTS dsar_request_type_check;
  ALTER TABLE platform.dsar_request ADD CONSTRAINT dsar_request_type_check
    CHECK (type IN ('export', 'delete', 'correction', 'phone_change', 'retention'));
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_dsar_member_id_retention'
                   AND conrelid = 'platform.dsar_request'::regclass) THEN
    ALTER TABLE platform.dsar_request ADD CONSTRAINT ck_dsar_member_id_retention
      CHECK (member_id IS NOT NULL OR type = 'retention');
  END IF;
END $$;
-- "Satu hapus aktif" mencakup retention (D.7): per member_id (subjek ber-akun) dan per member_hash (subjek ledger POS)
DROP INDEX IF EXISTS platform.uq_dsar_active_delete;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dsar_active_delete2 ON platform.dsar_request (member_id)
  WHERE type IN ('delete', 'retention') AND status IN ('scheduled', 'processing');
CREATE UNIQUE INDEX IF NOT EXISTS uq_dsar_active_retention_hash ON platform.dsar_request (member_hash)
  WHERE type = 'retention' AND status IN ('scheduled', 'processing');
CREATE INDEX IF NOT EXISTS ix_dsar_due_retention ON platform.dsar_request (scheduled_at)
  WHERE type = 'retention' AND status = 'scheduled';

RESET ROLE;

-- ───────────── 5. Hak akses (least privilege, docs/04 §7.3) ─────────────
REVOKE ALL ON platform.fnb_table_session, platform.fnb_submission, platform.fnb_menu_item, platform.fnb_menu_sync,
              platform.loyalty_member_activity, platform.loyalty_expiry_notice, platform.loyalty_expiry_notice_proof
  FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
-- bukti pemberitahuan (L-76): append; DELETE hanya retensi
GRANT SELECT, INSERT, DELETE ON platform.loyalty_expiry_notice_proof TO efn_bff;
-- fnb_*: DELETE hanya untuk retensi FNB_SESSION_RETENTION_DAYS (180) & sinkron menu
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.fnb_table_session, platform.fnb_submission, platform.fnb_menu_item,
                                        platform.fnb_menu_sync TO efn_bff;
-- aktivitas: DELETE hanya saat erasure subjek; notice: DELETE retensi > 400 hari & erasure
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.loyalty_member_activity, platform.loyalty_expiry_notice TO efn_bff;
-- loyalty_reservation: efn_bff sudah SELECT/INSERT/DELETE tingkat tabel (05) → kolom baru tercakup untuk INSERT;
-- tidak ada UPDATE channel/part_id. efn_readonly (rekonsiliasi K-18-6 diperluas, C.5) kolom non-PII baru:
GRANT SELECT (channel, part_id) ON platform.loyalty_reservation TO efn_readonly;
-- online_*: efn_bff SELECT/INSERT/UPDATE tingkat tabel (07) → kolom baru tercakup.
-- L-77: rekonsiliasi ETL Poin EFN online (C.5) — efn_readonly (anggota: efn_etl) kolom NON-PII bagian online saja; join ke
-- ledger lewat `loyalty_ledger.source_ref = part id` (source 'online'). TANPA online_order.member_id / hash / kode rahasia.
GRANT SELECT (id, order_id, channel_code, company_code, status, amount_payable, amount_cash, points_redeemed, points_value_idr,
              points_restored_value, earn_status, points_earned, created_at, updated_at)
  ON platform.online_order_part TO efn_readonly;

COMMIT;
