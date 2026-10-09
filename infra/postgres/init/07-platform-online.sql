-- Migrasi platform v5.0 — Fase 3 penjualan online Super-App (docs/04_SERVICE_CONTRACTS.md §10.7, §10.8; ADR-034 &
-- ADR-019 Proposed). Pemilik: services/api-gateway (efn-bff-engineer). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT).
--   * Volume baru: dijalankan otomatis setelah 01–06 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 sebelum BFF baru start. Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/07-platform-online.sql
--     Aman diulang; BFF lama tetap berjalan (kolom baru punya default, tabel baru belum dipakai).
--
-- Isi:
-- 1. `payment_intent` + `purpose` ('pos'|'online') + `online_order_id` (§10.7). Perilaku POS tidak berubah (default 'pos').
-- 2. Transaksi online (§10.8.1): online_order, online_order_part, payment_intent_portion, online_refund, online_order_event.
-- 3. Katalog salinan BFF (§10.8.2, non-PII): online_catalog_item, online_catalog_category, online_catalog_sync.
-- 4. Audit CLI refund CS (L-3): online_admin_audit.
-- 5. Hak akses: efn_bff DML minimum; efn_readonly/efn_etl/efn_ocr TIDAK diberi akses (analitik online dari Odoo).
--
-- Kolom di luar daftar minimum §10.8 (alasan, diusulkan ke lead):
--   online_catalog_item.image_ready_sha  — hash gambar yang SUDAH di-re-encode & tersimpan di bucket `catalog`
--                                          (endpoint gambar hanya melayani hash yang siap; job tahu gambar mana yang perlu ditarik).
--   online_catalog_sync.channel_data     — blok `channel` (nama, company_code, outlet) dari efn_online_catalog (usulan
--                                          tambahan kontrak; §10.3.2 `GET /v1/catalog/channels` butuh data ini).
--   online_catalog_sync.last_attempt_at  — metrik umur sinkron & backoff.
--   online_admin_audit                   — L-3 "teraudit (aktor, alasan, order, porsi)"; tanpa PII pelanggan.
--
-- PII (UU PDP): satu-satunya tautan ke orang = online_order.member_id (pseudonim UUID, ADR-029), di-NULL-kan setelah
-- ONLINE_ORDER_MEMBER_LINK_DAYS (usulan 730 hari, L-5) atau saat erasure DSAR. `pickup_code_enc` = rahasia (AES-256-GCM,
-- AAD 'efn:online_order_part.pickup_code_enc:v1'), di-NULL-kan saat final / 7 hari setelah ready. Retensi baris keuangan
-- 10 tahun (pasangan dokumen akuntansi Odoo, Q-P2). Risiko: R-T10 (saga), R-O05 (refund ≤ porsi), R-L01 (PII), R-S09.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 2a. Platform order ─────────────
CREATE TABLE IF NOT EXISTS platform.online_order (
  id                  uuid PRIMARY KEY,
  member_id           uuid NULL REFERENCES platform.member (id),
  idempotency_key     text        NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{16,128}$'),
  request_sha256      char(64)    NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  quote_id            uuid        NOT NULL,
  status              text        NOT NULL CHECK (status IN ('created', 'awaiting_payment', 'paid', 'fulfilling', 'completed',
                                                             'cancelled', 'expired', 'failed', 'review')),
  amount_payable      numeric(14,2) NOT NULL CHECK (amount_payable > 0 AND amount_payable = trunc(amount_payable)),
  currency            text        NOT NULL DEFAULT 'IDR' CHECK (currency = 'IDR'),
  payment_intent_id   uuid NULL UNIQUE,
  expires_at          timestamptz NOT NULL,
  paid_at             timestamptz,
  closed_at           timestamptz,
  close_reason        text NULL CHECK (close_reason IN ('payment_expired', 'customer', 'place_failed', 'stock_short', 'ops', 'review')),
  member_unlinked_at  timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_online_order_member_key UNIQUE (member_id, idempotency_key),
  CONSTRAINT ck_online_order_unlinked CHECK (member_unlinked_at IS NULL OR member_id IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_online_order_member_created ON platform.online_order (member_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_online_order_open ON platform.online_order (status, expires_at)
  WHERE status IN ('created', 'awaiting_payment', 'paid', 'fulfilling', 'review');
CREATE INDEX IF NOT EXISTS ix_online_order_closed ON platform.online_order (closed_at) WHERE member_id IS NOT NULL;

-- ───────────── 1. payment_intent (§10.7) ─────────────
ALTER TABLE platform.payment_intent ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'pos';
ALTER TABLE platform.payment_intent ADD COLUMN IF NOT EXISTS online_order_id uuid NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_payment_intent_purpose') THEN
    ALTER TABLE platform.payment_intent ADD CONSTRAINT ck_payment_intent_purpose
      CHECK (purpose IN ('pos', 'online') AND ((purpose = 'online') = (online_order_id IS NOT NULL)));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_payment_intent_online_order') THEN
    ALTER TABLE platform.payment_intent ADD CONSTRAINT uq_payment_intent_online_order UNIQUE (online_order_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_payment_intent_online_order') THEN
    ALTER TABLE platform.payment_intent ADD CONSTRAINT fk_payment_intent_online_order
      FOREIGN KEY (online_order_id) REFERENCES platform.online_order (id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_online_order_payment_intent') THEN
    ALTER TABLE platform.online_order ADD CONSTRAINT fk_online_order_payment_intent
      FOREIGN KEY (payment_intent_id) REFERENCES platform.payment_intent (id);
  END IF;
END $$;

-- ───────────── 2b. Bagian per kanal (satu SO Odoo per bagian) ─────────────
CREATE TABLE IF NOT EXISTS platform.online_order_part (
  id               uuid PRIMARY KEY,
  order_id         uuid        NOT NULL REFERENCES platform.online_order (id),
  channel_code     text        NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  company_code     text        NOT NULL CHECK (company_code IN ('EFN', 'RTL', 'FNB', 'FARM')),
  outlet_code      text        NOT NULL CHECK (outlet_code ~ '^[A-Z0-9][A-Z0-9-]{1,23}$'),
  fulfilment       text        NOT NULL CHECK (fulfilment IN ('pickup', 'delivery')),
  status           text        NOT NULL CHECK (status IN ('placing', 'placed', 'place_failed', 'paying', 'confirmed', 'stock_short',
                                                          'review', 'ready', 'handed_over', 'cancelled')),
  lines            jsonb       NOT NULL,
  tax_groups       jsonb       NOT NULL DEFAULT '[]'::jsonb,
  amount_total     numeric(14,2) NOT NULL CHECK (amount_total >= 0),
  amount_payable   numeric(14,2) NOT NULL CHECK (amount_payable > 0 AND amount_payable = trunc(amount_payable)),
  quote_hash       text        NOT NULL CHECK (length(quote_hash) BETWEEN 1 AND 128),
  sale_order_name  text NULL CHECK (sale_order_name IS NULL OR length(sale_order_name) <= 64),
  odoo_state       text NULL CHECK (odoo_state IS NULL OR length(odoo_state) <= 32),
  last_seq         integer     NOT NULL DEFAULT 0 CHECK (last_seq >= 0),
  pickup_code_enc  bytea NULL CHECK (pickup_code_enc IS NULL OR (octet_length(pickup_code_enc) > 29 AND get_byte(pickup_code_enc, 0) >= 1)),
  refunded_amount  numeric(14,2) NOT NULL DEFAULT 0 CHECK (refunded_amount >= 0 AND refunded_amount <= amount_payable),
  confirmed_at     timestamptz,
  ready_at         timestamptz,
  handed_over_at   timestamptz,
  cancelled_at     timestamptz,
  cancel_reason    text NULL CHECK (cancel_reason IS NULL OR cancel_reason IN
                     ('payment_expired', 'customer', 'place_failed', 'stock_short', 'ops', 'review', 'late_payment')),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_online_order_part_channel UNIQUE (order_id, channel_code)
);
CREATE INDEX IF NOT EXISTS ix_online_order_part_status ON platform.online_order_part (status, updated_at);
CREATE INDEX IF NOT EXISTS ix_online_order_part_pickup ON platform.online_order_part (ready_at) WHERE pickup_code_enc IS NOT NULL;

-- ───────────── 2c. Porsi intent per bagian (§10.7) ─────────────
CREATE TABLE IF NOT EXISTS platform.payment_intent_portion (
  payment_intent_id  uuid NOT NULL REFERENCES platform.payment_intent (id),
  part_id            uuid NOT NULL REFERENCES platform.online_order_part (id),
  channel_code       text NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  company_code       text NOT NULL CHECK (company_code IN ('EFN', 'RTL', 'FNB', 'FARM')),
  amount             numeric(14,2) NOT NULL CHECK (amount > 0 AND amount = trunc(amount)),
  PRIMARY KEY (payment_intent_id, part_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_intent_portion_part ON platform.payment_intent_portion (part_id);

-- ───────────── 2d. Refund (selalu terikat satu bagian; Σ ≤ porsi, R-O05) ─────────────
CREATE TABLE IF NOT EXISTS platform.online_refund (
  id                   uuid PRIMARY KEY,
  part_id              uuid NOT NULL REFERENCES platform.online_order_part (id),
  payment_intent_id    uuid NOT NULL REFERENCES platform.payment_intent (id),
  kind                 text NOT NULL CHECK (kind IN ('full', 'lines', 'late_payment')),
  reason               text NOT NULL CHECK (reason IN ('stock_short', 'customer_cs', 'ops_cancelled', 'review', 'late_payment')),
  lines                jsonb NULL,
  amount               numeric(14,2) NULL CHECK (amount IS NULL OR (amount > 0 AND amount = trunc(amount))),
  odoo_refund_ref      text NULL CHECK (odoo_refund_ref IS NULL OR length(odoo_refund_ref) BETWEEN 1 AND 128),
  provider_refund_ref  text NULL CHECK (provider_refund_ref IS NULL OR length(provider_refund_ref) BETWEEN 1 AND 128),
  status               text NOT NULL CHECK (status IN ('requested', 'odoo_done', 'gateway_pending', 'gateway_done', 'settled',
                                                        'manual_required', 'failed')),
  requested_by         text NOT NULL CHECK (requested_by IN ('system', 'cs')),
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  settled_at           timestamptz
);
CREATE INDEX IF NOT EXISTS ix_online_refund_part ON platform.online_refund (part_id);
CREATE INDEX IF NOT EXISTS ix_online_refund_open ON platform.online_refund (status, updated_at)
  WHERE status IN ('requested', 'odoo_done', 'gateway_pending', 'gateway_done', 'manual_required', 'failed');
-- Idempotensi refund otomatis: satu refund sistem per (bagian, alasan) bila tanpa dokumen Odoo; satu per dokumen Odoo.
CREATE UNIQUE INDEX IF NOT EXISTS uq_online_refund_system ON platform.online_refund (part_id, reason)
  WHERE requested_by = 'system' AND odoo_refund_ref IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_online_refund_odoo_ref ON platform.online_refund (part_id, odoo_refund_ref)
  WHERE odoo_refund_ref IS NOT NULL;

-- ───────────── 2e. Riwayat/inbox event (tanpa payload mentah) ─────────────
CREATE TABLE IF NOT EXISTS platform.online_order_event (
  id               bigserial PRIMARY KEY,
  part_id          uuid NOT NULL REFERENCES platform.online_order_part (id),
  source           text NOT NULL CHECK (source IN ('odoo', 'bff', 'gateway')),
  event            text NOT NULL CHECK (event ~ '^[a-z_]{1,32}$'),
  seq              integer NULL,
  idempotency_key  text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  outcome          text NOT NULL CHECK (outcome IN ('applied', 'duplicate', 'ignored_stale', 'ignored_unknown_event')),
  occurred_at      timestamptz,
  received_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_online_order_event_received ON platform.online_order_event (received_at);
CREATE INDEX IF NOT EXISTS ix_online_order_event_part ON platform.online_order_event (part_id);

-- ───────────── 3. Katalog (salinan non-PII, §10.8.2) ─────────────
CREATE TABLE IF NOT EXISTS platform.online_catalog_item (
  channel_code     text    NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  product_id       integer NOT NULL CHECK (product_id > 0),
  tmpl_id          integer NOT NULL CHECK (tmpl_id > 0),
  data             jsonb   NOT NULL,
  image_sha        text NULL CHECK (image_sha IS NULL OR image_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  image_ready_sha  text NULL CHECK (image_ready_sha IS NULL OR image_ready_sha ~ '^([0-9a-f]{40}|[0-9a-f]{64})$'),
  category_id      integer NULL,
  sequence         integer NOT NULL DEFAULT 0,
  search_text      text    NOT NULL DEFAULT '',
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz,
  PRIMARY KEY (channel_code, product_id)
);
CREATE INDEX IF NOT EXISTS ix_online_catalog_item_list ON platform.online_catalog_item (channel_code, sequence, product_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_online_catalog_item_image ON platform.online_catalog_item (image_sha) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.online_catalog_category (
  channel_code  text    NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  category_id   integer NOT NULL,
  data          jsonb   NOT NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  deleted_at    timestamptz,
  PRIMARY KEY (channel_code, category_id)
);

CREATE TABLE IF NOT EXISTS platform.online_catalog_sync (
  channel_code     text PRIMARY KEY CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  since            text NULL CHECK (since IS NULL OR length(since) <= 64),
  cursor           text NULL CHECK (cursor IS NULL OR length(cursor) <= 512),
  last_ok_at       timestamptz,
  last_attempt_at  timestamptz,
  last_error_code  text NULL CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z_]{1,40}$'),
  channel_data     jsonb NULL
);

-- ───────────── 4. Audit CLI refund/resolve CS (L-3) ─────────────
CREATE TABLE IF NOT EXISTS platform.online_admin_audit (
  id          bigserial PRIMARY KEY,
  action      text NOT NULL CHECK (action IN ('refund', 'refund_settle', 'resolve_cancel', 'resolve_sync')),
  actor       text NOT NULL CHECK (actor ~ '^[A-Za-z0-9._@-]{2,64}$'),
  reason      text NOT NULL CHECK (length(reason) BETWEEN 3 AND 200),
  order_id    uuid NULL,
  part_id     uuid NULL,
  refund_id   uuid NULL,
  amount      numeric(14,2) NULL,
  outcome     text NOT NULL CHECK (outcome ~ '^[a-z_]{1,40}$'),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_online_admin_audit_part ON platform.online_admin_audit (part_id);

RESET ROLE;

-- ───────────── 5. Hak akses ─────────────
REVOKE ALL ON platform.online_order, platform.online_order_part, platform.payment_intent_portion, platform.online_refund,
              platform.online_order_event, platform.online_catalog_item, platform.online_catalog_category,
              platform.online_catalog_sync, platform.online_admin_audit
  FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT, UPDATE ON platform.online_order, platform.online_order_part, platform.online_refund TO efn_bff;
GRANT SELECT, INSERT ON platform.payment_intent_portion TO efn_bff;
-- DELETE hanya untuk job retensi (riwayat event & katalog), §10.8.3
GRANT SELECT, INSERT, DELETE ON platform.online_order_event TO efn_bff;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.online_catalog_item, platform.online_catalog_category, platform.online_catalog_sync TO efn_bff;
GRANT SELECT, INSERT ON platform.online_admin_audit TO efn_bff;
GRANT USAGE, SELECT ON SEQUENCE platform.online_order_event_id_seq, platform.online_admin_audit_id_seq TO efn_bff;

COMMIT;
