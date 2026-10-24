-- Skema platform v3 (docs/04 §8.2–§8.4 + klarifikasi v3.1 §8.7). Pemilik: services/api-gateway (efn-bff-engineer).
-- Dijalankan setelah 01/02 (urutan alfabet docker-entrypoint-initdb.d) saat volume kosong. IDEMPOTEN: untuk volume yang
-- sudah ada jalankan manual sebagai superuser `psql -d efn_platform -f 03-platform-v3.sql` (aman diulang SETELAH
-- 04-platform-v3-6.sql pernah dijalankan — SEC-39).
-- ADR terkait: ADR-016 (payment, Proposed), ADR-019/020 (merchant of record, ledger), ADR-022 (enkripsi). UU PDP: SEC-11.

\connect efn_platform
\set ON_ERROR_STOP on

SET ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN (bukan efn_bff, bukan superuser); hak efn_bff di 04-platform-v3-6.sql

-- ─────────────────────────────────────────────────────────────
-- §8.2 Identitas member opak. JWT `sub` = member.id. member_ref_enc = AES-256-GCM, AAD 'efn:member.member_ref_enc:v1'.
-- deleted_at diisi saat DSAR delete masuk `processing`; baris DIHAPUS setelah erasure platform (tidak ada PII tersisa).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.member (
  id              uuid PRIMARY KEY,
  member_hash     char(64) NOT NULL UNIQUE CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  member_ref_enc  bytea    NOT NULL CHECK (octet_length(member_ref_enc) > 29 AND get_byte(member_ref_enc, 0) >= 1),
  created_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz
);

-- ─────────────────────────────────────────────────────────────
-- §8.4 Consent append-only (+ §8.7 text_sha256). ip_enc AAD 'efn:consent.ip_enc:v1'.
-- member_id sengaja TANPA FK: baris consent dipertahankan sebagai bukti setelah member dihapus (ip_enc di-null-kan).
-- Append-only ditegakkan dua lapis: (1) hak efn_bff hanya SELECT, INSERT, UPDATE(ip_enc) — UPDATE kolom ip_enc
-- dibutuhkan erasure (NULL) dan re-encrypt rotasi kunci (U2); (2) trigger menolak DELETE/TRUNCATE dan UPDATE kolom
-- lain. SEC-39: pemilik tabel = efn_platform_owner (NOLOGIN), sehingga efn_bff yang terkompromi TIDAK bisa
-- DISABLE TRIGGER / GRANT ulang; tetap bukan pagar terhadap superuser/DBA (lihat README privacy). Retensi "selama akun + 5 th" butuh job DBA terpisah (BFF tidak punya DELETE).
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.consent (
  id           bigserial PRIMARY KEY,
  member_id    uuid     NOT NULL,
  purpose      text     NOT NULL CHECK (purpose IN ('location', 'marketing')),
  granted      boolean  NOT NULL,
  version      text     NOT NULL CHECK (length(version) BETWEEN 1 AND 32),
  text_sha256  char(64) NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
  source       text     NOT NULL CHECK (length(source) BETWEEN 1 AND 32),
  ip_enc       bytea    CHECK (ip_enc IS NULL OR (octet_length(ip_enc) > 29 AND get_byte(ip_enc, 0) >= 1)),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_consent_member_purpose ON platform.consent (member_id, purpose, created_at DESC, id DESC);
-- (SEC-39) Pemilik = efn_platform_owner sehingga DROP/CREATE TRIGGER di run ulang tidak butuh hak efn_bff.

CREATE OR REPLACE FUNCTION platform.consent_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF (NEW.id, NEW.member_id, NEW.purpose, NEW.granted, NEW.version, NEW.text_sha256, NEW.source, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.member_id, OLD.purpose, OLD.granted, OLD.version, OLD.text_sha256, OLD.source, OLD.created_at) THEN
      RAISE EXCEPTION 'platform.consent append-only: hanya ip_enc yang boleh diubah';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'platform.consent append-only: % ditolak', TG_OP;
END $$;

DROP TRIGGER IF EXISTS trg_consent_append_only ON platform.consent;
CREATE TRIGGER trg_consent_append_only BEFORE UPDATE OR DELETE ON platform.consent
  FOR EACH ROW EXECUTE FUNCTION platform.consent_append_only();
DROP TRIGGER IF EXISTS trg_consent_no_truncate ON platform.consent;
CREATE TRIGGER trg_consent_no_truncate BEFORE TRUNCATE ON platform.consent
  FOR EACH STATEMENT EXECUTE FUNCTION platform.consent_append_only();

REVOKE ALL ON platform.consent FROM efn_bff;
GRANT SELECT, INSERT ON platform.consent TO efn_bff;
GRANT UPDATE (ip_enc) ON platform.consent TO efn_bff;
GRANT USAGE, SELECT ON SEQUENCE platform.consent_id_seq TO efn_bff;

-- ─────────────────────────────────────────────────────────────
-- §8.4 + §8.7 DSAR. reason = alasan penolakan/kegagalan tanpa PII. v3.6 (SEC-31, 04-platform-v3-6.sql): member_hash
-- NULLABLE — hanya ada selama request aktif (scheduled/processing); di-NULL-kan saat `completed`.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.dsar_request (
  id              uuid PRIMARY KEY,
  member_id       uuid     NOT NULL,
  member_hash     char(64) NOT NULL CHECK (member_hash ~ '^[0-9a-f]{64}$'),
  type            text     NOT NULL CHECK (type IN ('export', 'delete', 'correction', 'phone_change')),
  status          text     NOT NULL CHECK (status IN ('pending_reauth', 'scheduled', 'cancelled', 'processing',
                                                     'completed', 'rejected', 'failed')),
  stores_pending  text[]   NOT NULL DEFAULT '{}'
                  CHECK (stores_pending <@ ARRAY['platform', 'odoo', 'clickhouse']::text[]),
  requested_at    timestamptz NOT NULL DEFAULT now(),
  scheduled_at    timestamptz,
  completed_at    timestamptz,
  reason          text
);
CREATE INDEX IF NOT EXISTS ix_dsar_member ON platform.dsar_request (member_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS ix_dsar_due ON platform.dsar_request (scheduled_at) WHERE status = 'scheduled';
CREATE INDEX IF NOT EXISTS ix_dsar_processing ON platform.dsar_request (requested_at) WHERE status = 'processing';
-- Satu permintaan hapus aktif per member (DELETE /v1/me idempotent)
CREATE UNIQUE INDEX IF NOT EXISTS uq_dsar_active_delete ON platform.dsar_request (member_id)
  WHERE type = 'delete' AND status IN ('scheduled', 'processing');

-- ─────────────────────────────────────────────────────────────
-- §8.3 Payment intent (gateway-agnostic). Tidak ada data kartu (PCI SAQ A). Settlement per PT (ADR-019) belum.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform.payment_intent (
  id               uuid PRIMARY KEY,
  provider         text NOT NULL CHECK (provider IN ('mock', 'xendit', 'midtrans')),
  provider_ref     text,
  order_ref        text NOT NULL CHECK (length(order_ref) BETWEEN 1 AND 64),
  company_code     text NOT NULL CHECK (company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'WTZ', 'FARM')),  -- G20 §12.1 + G24 §16.3 WTZ (volume lama: 09, 13)
  amount           numeric(14,2) NOT NULL CHECK (amount > 0),
  currency         text NOT NULL CHECK (currency = 'IDR'),
  method           text NOT NULL CHECK (method = 'qris'),
  status           text NOT NULL CHECK (status IN ('pending', 'paid', 'expired', 'failed', 'cancelled')),
  qr_string        text,
  expires_at       timestamptz,
  paid_at          timestamptz,
  idempotency_key  text NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 8 AND 128),
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_provider_ref ON platform.payment_intent (provider, provider_ref)
  WHERE provider_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_payment_order ON platform.payment_intent (order_ref);

-- ─────────────────────────────────────────────────────────────
-- §8.4: price_hunter_report.user_enc di-null-kan setelah 90 hari (retensi) dan saat erasure.
-- §8.4/§8.7: loyalty_ledger.member_ref_enc NULLABLE hanya untuk baris member terhapus, ditandai erased_at.
-- ─────────────────────────────────────────────────────────────
ALTER TABLE platform.price_hunter_report ALTER COLUMN user_enc DROP NOT NULL;

ALTER TABLE platform.loyalty_ledger ADD COLUMN IF NOT EXISTS erased_at timestamptz;
ALTER TABLE platform.loyalty_ledger ALTER COLUMN member_ref_enc DROP NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_ledger_enc_or_erased') THEN
    ALTER TABLE platform.loyalty_ledger
      ADD CONSTRAINT ck_loyalty_ledger_enc_or_erased CHECK (member_ref_enc IS NOT NULL OR erased_at IS NOT NULL);
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────
-- GRANT. Tidak ada kolom *_enc untuk role selain efn_bff.
-- ETL (efn_etl, langsung — bukan efn_readonly/Text-to-SQL): baca antrean erasure (docs/04 §8.4).
-- ─────────────────────────────────────────────────────────────
REVOKE ALL ON platform.member, platform.dsar_request, platform.payment_intent FROM efn_readonly, efn_ocr;
REVOKE ALL ON platform.consent FROM efn_readonly, efn_ocr;
GRANT SELECT (id, member_hash, status, stores_pending) ON platform.dsar_request TO efn_etl;
-- ETL juga boleh membaca penanda erased_at ledger (opsional, untuk mengecualikan baris terhapus)
GRANT SELECT (erased_at) ON platform.loyalty_ledger TO efn_readonly;

RESET ROLE;
