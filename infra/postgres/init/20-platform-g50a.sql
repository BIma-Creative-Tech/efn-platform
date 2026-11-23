-- Migrasi platform Gelombang 50-A (docs/04 §28.6–§28.8, §28.10; ADR-047 Proposed): event perilaku visitor, wishlist /
-- notify-me / price alert, kampanye tertarget (segmen, voucher, inbox). Pemilik: services/api-gateway (konteks
-- `behavior`, `wishlist`, `campaign`). IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya CREATE/ADD + ganti CHECK
-- consent; tanpa DROP kolom/tabel dan tanpa perubahan data.
--   * Volume baru: dijalankan otomatis setelah 01–19 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (20 HARUS masuk daftar WAJIB:
--     BFF G50 menulis `platform.consent` purpose `tracking` dan hook erasure/DSAR membaca tabel di bawah bila ada).
--     Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/20-platform-g50a.sql
--
-- Isi:
-- 0. `platform.consent.purpose` + `tracking` (ADR-047 §3.1 no. 1). Tabel tetap APPEND-ONLY.
-- 1. Skema `behavior` (milik efn_platform_owner, SEC-39):
--    - `device_consent`  bukti consent device (append-only: UPDATE ditolak; DELETE hanya oleh job retensi BFF)
--    - `device_link`     member ↔ device_key (hanya bila consent `tracking` akun aktif)
--    - `subject_member`  subject_key (HMAC member) → member_id, agar segmen (subject_key) dapat dipetakan ke member
--    - `event_raw`       staging event, PARTISI HARIAN (RANGE received_at, UTC), retensi 7 hari (drop partisi)
--    - `event_dedup`     unik (subject_key, event_id) lintas partisi (kontrak §28.6 "Dedup")
--    - `segment_chunk` / `segment_member`  anggota segmen dari segment-worker (§28.7), retensi 30 hari
--    - `erased_subject`  subject_key subjek terhapus (BFF: tolak di ingest segmen), retensi 100 hari
--    - `subject_erasure` antrean erasure ClickHouse untuk ETL (per request_id DSAR, append-only), retensi 100 hari
--    - kolom `event_raw.company_code` + `ingested_at` (cursor ETL) — revisi G50-A, idempoten di DB versi awal
--    - kolom `event_raw.consented` + `page`, tipe `page_view` — V-01 log kunjungan anonim (23 Nov 2026), idempoten
--    - fungsi SECURITY DEFINER `ensure_event_partitions` / `drop_old_event_partitions` (efn_bff tidak punya hak DDL)
-- 2. `platform.member_wishlist`, `restock_alert`, `price_alert` (§28.8, L-161) + state pengamat stok/harga.
-- 3. `platform.campaign`, `member_voucher`, `campaign_delivery`, `member_inbox` (§28.7, L-155, L-164).
-- PII: tidak ada kolom nama/HP/email/IP. `device_key`/`subject_key` = pseudonim (data pribadi, ADR-047 §1.2) → ikut
-- DSAR export & erasure. Kode voucher terenkripsi (`code_enc`, AES-256-GCM BFF, AAD per kolom) + `code_hash` HMAC.
-- Hak: efn_bff DML seperlunya; efn_etl hanya SELECT per kolom `behavior.event_raw` + `behavior.subject_erasure`
-- (= services/etl/sql/platform-behavior-grants.sql; ETL → ClickHouse, ADR-003); efn_readonly/efn_ocr TIDAK diberi akses.
-- Risiko: R-L13 (tracking/profiling), R-B17 (spam/anggaran promo), R-L01, R-S04.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

-- Skema baru dibuat sebagai superuser migrasi lalu diserahkan ke role NOLOGIN (efn_platform_owner tidak punya CREATE DB)
CREATE SCHEMA IF NOT EXISTS behavior AUTHORIZATION efn_platform_owner;
ALTER SCHEMA behavior OWNER TO efn_platform_owner;
REVOKE ALL ON SCHEMA behavior FROM PUBLIC;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

-- ───────────── 0. Consent purpose `tracking` ─────────────
ALTER TABLE platform.consent DROP CONSTRAINT IF EXISTS consent_purpose_check;
ALTER TABLE platform.consent
  ADD CONSTRAINT consent_purpose_check CHECK (purpose IN ('location', 'marketing', 'privacy', 'tracking'));

-- ───────────── 1. Skema behavior ─────────────
CREATE TABLE IF NOT EXISTS behavior.device_consent (
  id           bigserial   PRIMARY KEY,
  device_key   char(64)    NOT NULL CHECK (device_key ~ '^[0-9a-f]{64}$'),
  granted      boolean     NOT NULL,
  version      text        NOT NULL CHECK (version ~ '^[A-Za-z0-9._@-]{1,32}$'),
  text_sha256  char(64)    NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_device_consent_key ON behavior.device_consent (device_key, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS ix_device_consent_created ON behavior.device_consent (created_at);

CREATE OR REPLACE FUNCTION behavior.device_consent_no_update() RETURNS trigger
  LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'behavior.device_consent append-only: UPDATE ditolak (perubahan = baris baru)';
END $$;
DROP TRIGGER IF EXISTS trg_device_consent_no_update ON behavior.device_consent;
CREATE TRIGGER trg_device_consent_no_update BEFORE UPDATE ON behavior.device_consent
  FOR EACH ROW EXECUTE FUNCTION behavior.device_consent_no_update();

CREATE TABLE IF NOT EXISTS behavior.device_link (
  member_id     uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  device_key    char(64)    NOT NULL CHECK (device_key ~ '^[0-9a-f]{64}$'),
  linked_at     timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (member_id, device_key)
);
CREATE INDEX IF NOT EXISTS ix_device_link_device ON behavior.device_link (device_key);
CREATE INDEX IF NOT EXISTS ix_device_link_seen ON behavior.device_link (last_seen_at);

CREATE TABLE IF NOT EXISTS behavior.subject_member (
  subject_key  char(64)    PRIMARY KEY CHECK (subject_key ~ '^[0-9a-f]{64}$'),
  member_id    uuid        NOT NULL UNIQUE REFERENCES platform.member (id) ON DELETE CASCADE,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS behavior.event_raw (
  received_at    timestamptz NOT NULL,
  batch_id       uuid        NOT NULL,
  subject_key    char(64)    NOT NULL CHECK (subject_key ~ '^[0-9a-f]{64}$'),
  subject_kind   text        NOT NULL CHECK (subject_kind IN ('device', 'member')),
  channel        text        NOT NULL CHECK (channel IN ('web', 'app', 'server')),
  event_id       uuid        NOT NULL,
  type           text        NOT NULL CHECK (type IN ('product_view', 'wishlist_add', 'wishlist_remove', 'cart_add', 'cart_remove',
                                                      'checkout_start', 'search', 'notify_me_request', 'price_alert_set',
                                                      'page_view', 'cart_abandon', 'purchase')),
  client_ts      timestamptz NOT NULL,
  channel_code   text        CHECK (channel_code IS NULL OR channel_code ~ '^[A-Z0-9-]{1,8}$'),
  outlet_code    text        CHECK (outlet_code IS NULL OR outlet_code ~ '^[A-Za-z0-9_-]{1,16}$'),
  product_id     integer     CHECK (product_id IS NULL OR product_id > 0),
  duration_s     smallint    CHECK (duration_s IS NULL OR duration_s BETWEEN 0 AND 600),
  qty            integer     CHECK (qty IS NULL OR qty BETWEEN 0 AND 9999),
  query          text        CHECK (query IS NULL OR length(query) <= 64),
  result_count   integer     CHECK (result_count IS NULL OR result_count >= 0),
  stock_status   text        CHECK (stock_status IS NULL OR stock_status IN ('in_stock', 'low', 'out')),
  price_idr      bigint      CHECK (price_idr IS NULL OR price_idr >= 0),
  -- Revisi G50-A (selaras ETL `services/etl/efn_etl/behavior.py`): PT kanal (dari pemetaan kanal → PT BFF) dan waktu
  -- baris masuk staging (cursor ETL `(ingested_at, subject_key||':'||event_id)`)
  company_code   text        NOT NULL CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$'),
  ingested_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
  -- V-01 (keputusan pemilik proyek 23 Nov 2026): false = log kunjungan anonim tanpa consent (tidak pernah segmen/DSAR);
  -- `page` hanya untuk page_view. CHECK lintas kolom dipasang idempoten di bawah (berlaku juga untuk DB lama).
  consented      boolean     NOT NULL DEFAULT true,
  page           text        CHECK (page IS NULL OR page IN ('home', 'category', 'product', 'search', 'cart', 'checkout',
                                                             'promo', 'store', 'account', 'order', 'other'))
) PARTITION BY RANGE (received_at);
CREATE INDEX IF NOT EXISTS ix_event_raw_subject ON behavior.event_raw (subject_key, received_at);
CREATE INDEX IF NOT EXISTS ix_event_raw_type ON behavior.event_raw (type, received_at);
-- Pengaman: baris tanpa partisi harian (job terlambat) tidak membuat insert gagal; dibersihkan job retensi
CREATE TABLE IF NOT EXISTS behavior.event_raw_default PARTITION OF behavior.event_raw DEFAULT;

-- DB yang sudah menjalankan versi awal 20 (tanpa company_code/ingested_at): tambah kolom tanpa default volatil, isi
-- baris lama, baru pasang NOT NULL/DEFAULT/CHECK. Baris staging lama yang PT-nya tidak dapat diturunkan dibuang
-- (staging 7 hari, bukan sumber kebenaran; lebih baik daripada menyimpan event tanpa PT).
ALTER TABLE behavior.event_raw ADD COLUMN IF NOT EXISTS company_code text;
ALTER TABLE behavior.event_raw ADD COLUMN IF NOT EXISTS ingested_at timestamptz;
UPDATE behavior.event_raw e
   SET company_code = s.channel_data ->> 'company_code'
  FROM platform.online_catalog_sync s
 WHERE e.company_code IS NULL AND s.channel_code = e.channel_code
   AND (s.channel_data ->> 'company_code') ~ '^[A-Z][A-Z0-9]{1,9}$';
DELETE FROM behavior.event_raw WHERE company_code IS NULL;
UPDATE behavior.event_raw SET ingested_at = received_at WHERE ingested_at IS NULL;
ALTER TABLE behavior.event_raw ALTER COLUMN company_code SET NOT NULL;
ALTER TABLE behavior.event_raw ALTER COLUMN ingested_at SET DEFAULT clock_timestamp();
ALTER TABLE behavior.event_raw ALTER COLUMN ingested_at SET NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE conrelid = 'behavior.event_raw'::regclass AND contype = 'c'
                    AND pg_get_constraintdef(oid) LIKE '%company_code%') THEN
    ALTER TABLE behavior.event_raw ADD CONSTRAINT event_raw_company_code_check
      CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$');
  END IF;
END $$;
-- QA G50 #7: event server `purchase` (order online lunas) — CHECK `type` ditulis ulang idempoten (nama otomatis
-- `event_raw_type_check` dari CREATE TABLE; constraint di induk partisi berlaku ke semua partisi).
ALTER TABLE behavior.event_raw DROP CONSTRAINT IF EXISTS event_raw_type_check;
ALTER TABLE behavior.event_raw ADD CONSTRAINT event_raw_type_check
  CHECK (type IN ('product_view', 'wishlist_add', 'wishlist_remove', 'cart_add', 'cart_remove', 'checkout_start', 'search',
                  'notify_me_request', 'price_alert_set', 'page_view', 'cart_abandon', 'purchase'));
-- V-01 (keputusan pemilik proyek 23 Nov 2026, log kunjungan anonim): kolom `consented` (DEFAULT true = semua baris lama
-- ber-consent; default konstan → tanpa rewrite tabel) + `page`. CHECK ditulis ulang idempoten (DROP IF EXISTS + ADD):
--   * `page` enum tertutup (bukan URL), wajib tepat untuk page_view dan dilarang untuk tipe lain;
--   * baris anonim selalu subject device dan tidak pernah tipe fitur member / turunan kampanye (cart_abandon, purchase).
ALTER TABLE behavior.event_raw ADD COLUMN IF NOT EXISTS consented boolean NOT NULL DEFAULT true;
ALTER TABLE behavior.event_raw ADD COLUMN IF NOT EXISTS page text;
ALTER TABLE behavior.event_raw DROP CONSTRAINT IF EXISTS event_raw_page_check;
ALTER TABLE behavior.event_raw ADD CONSTRAINT event_raw_page_check
  CHECK (page IS NULL OR page IN ('home', 'category', 'product', 'search', 'cart', 'checkout', 'promo', 'store', 'account',
                                  'order', 'other'));
ALTER TABLE behavior.event_raw DROP CONSTRAINT IF EXISTS event_raw_page_type_check;
ALTER TABLE behavior.event_raw ADD CONSTRAINT event_raw_page_type_check CHECK ((type = 'page_view') = (page IS NOT NULL));
ALTER TABLE behavior.event_raw DROP CONSTRAINT IF EXISTS event_raw_anon_check;
ALTER TABLE behavior.event_raw ADD CONSTRAINT event_raw_anon_check
  CHECK (consented OR (subject_kind = 'device'
                       AND type NOT IN ('wishlist_add', 'wishlist_remove', 'notify_me_request', 'price_alert_set',
                                        'cart_abandon', 'purchase')));
-- Cursor ETL job `behavior_events` (index di induk → terbentuk di setiap partisi, termasuk partisi baru)
CREATE INDEX IF NOT EXISTS ix_event_raw_ingested ON behavior.event_raw (ingested_at);

CREATE TABLE IF NOT EXISTS behavior.event_dedup (
  subject_key  char(64)    NOT NULL CHECK (subject_key ~ '^[0-9a-f]{64}$'),
  event_id     uuid        NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (subject_key, event_id)
);
CREATE INDEX IF NOT EXISTS ix_event_dedup_received ON behavior.event_dedup (received_at);

CREATE TABLE IF NOT EXISTS behavior.segment_chunk (
  run_id        uuid        NOT NULL,
  segment_code  text        NOT NULL CHECK (segment_code ~ '^[a-z][a-z0-9_]{1,39}$'),
  chunk_no      integer     NOT NULL CHECK (chunk_no BETWEEN 0 AND 100000),
  body_sha256   char(64)    NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
  member_count  integer     NOT NULL CHECK (member_count BETWEEN 0 AND 5000),
  received_at   timestamptz NOT NULL DEFAULT now(),
  company_code  text        NOT NULL CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$'),
  PRIMARY KEY (run_id, segment_code, chunk_no)
);
CREATE INDEX IF NOT EXISTS ix_segment_chunk_received ON behavior.segment_chunk (received_at);

CREATE TABLE IF NOT EXISTS behavior.segment_member (
  run_id        uuid        NOT NULL,
  segment_code  text        NOT NULL,
  subject_key   char(64)    NOT NULL CHECK (subject_key ~ '^[0-9a-f]{64}$'),
  received_at   timestamptz NOT NULL DEFAULT now(),
  company_code  text        NOT NULL CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$'),
  PRIMARY KEY (run_id, segment_code, subject_key)
);
CREATE INDEX IF NOT EXISTS ix_segment_member_subject ON behavior.segment_member (subject_key);
CREATE INDEX IF NOT EXISTS ix_segment_member_received ON behavior.segment_member (received_at);

-- SEC-G50-08: segmen terikat PT (body ingest `company_code`, satu PT per run_id); dispatcher memfilter PT kampanye.
-- DB versi awal: snapshot segmen lama tanpa PT dibuang (turunan harian, dibangun ulang worker), lalu NOT NULL + CHECK.
ALTER TABLE behavior.segment_chunk ADD COLUMN IF NOT EXISTS company_code text;
ALTER TABLE behavior.segment_member ADD COLUMN IF NOT EXISTS company_code text;
DELETE FROM behavior.segment_member WHERE company_code IS NULL;
DELETE FROM behavior.segment_chunk WHERE company_code IS NULL;
ALTER TABLE behavior.segment_chunk ALTER COLUMN company_code SET NOT NULL;
ALTER TABLE behavior.segment_member ALTER COLUMN company_code SET NOT NULL;
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['segment_chunk', 'segment_member'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conrelid = ('behavior.' || t)::regclass AND contype = 'c'
                      AND pg_get_constraintdef(oid) LIKE '%company_code%') THEN
      EXECUTE format('ALTER TABLE behavior.%I ADD CONSTRAINT %I CHECK (company_code ~ ''^[A-Z][A-Z0-9]{1,9}$'')',
                     t, t || '_company_code_check');
    END IF;
  END LOOP;
END $$;
CREATE INDEX IF NOT EXISTS ix_segment_member_run_company ON behavior.segment_member (run_id, segment_code, company_code);
CREATE INDEX IF NOT EXISTS ix_segment_chunk_run ON behavior.segment_chunk (run_id, company_code);

CREATE TABLE IF NOT EXISTS behavior.erased_subject (
  subject_key  char(64)    PRIMARY KEY CHECK (subject_key ~ '^[0-9a-f]{64}$'),
  erased_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_erased_subject_at ON behavior.erased_subject (erased_at);

-- Antrean erasure untuk ETL (kontrak `SQL_BEHAVIOR_ERASURES` / `SQL_DSAR_SUBJECT_KEYS` di services/etl/efn_etl/behavior.py):
-- append-only, satu baris per subject_key per permintaan DSAR (HMAC member + setiap device_key tertaut), ditulis dalam
-- transaksi erasure platform yang sama dengan transisi `dsar_request` → processing; retensi ≥ 100 hari (> TTL CH 90 h).
-- `erased_subject` tetap dipakai BFF (menolak subjek terhapus di ingest segmen).
CREATE TABLE IF NOT EXISTS behavior.subject_erasure (
  id           bigserial   PRIMARY KEY,
  subject_key  char(64)    NOT NULL CHECK (subject_key ~ '^[0-9a-f]{64}$'),
  request_id   uuid        NOT NULL,
  reason       text        CHECK (reason IS NULL OR reason ~ '^[a-z_]{1,40}$'),
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_subject_erasure_created ON behavior.subject_erasure (created_at, id);
CREATE INDEX IF NOT EXISTS ix_subject_erasure_request ON behavior.subject_erasure (request_id);

-- QA G50 #7: penanda bagian order online yang event `purchase`-nya sudah diterbitkan (idempotensi job BFF;
-- tanpa data member, hanya id bagian). Retensi 30 hari (jendela job 48 jam).
CREATE TABLE IF NOT EXISTS behavior.purchase_emitted (
  part_id     uuid        PRIMARY KEY,
  emitted_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_purchase_emitted_at ON behavior.purchase_emitted (emitted_at);

-- Partisi harian (UTC) hari ini-1 .. hari ini+p_days_ahead. Kegagalan satu partisi (mis. baris sudah jatuh ke DEFAULT
-- untuk rentang itu) → WARNING, partisi lain tetap dibuat; baris DEFAULT tetap terbaca & dipurge job retensi.
CREATE OR REPLACE FUNCTION behavior.ensure_event_partitions(p_days_ahead integer DEFAULT 2) RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, behavior AS $$
DECLARE
  d date;
  tname text;
  n integer := 0;
BEGIN
  IF p_days_ahead IS NULL OR p_days_ahead < 0 OR p_days_ahead > 14 THEN
    RAISE EXCEPTION 'p_days_ahead harus 0..14';
  END IF;
  FOR i IN -1..p_days_ahead LOOP
    d := (now() AT TIME ZONE 'UTC')::date + i;
    tname := 'event_raw_p' || to_char(d, 'YYYYMMDD');
    IF to_regclass('behavior.' || tname) IS NULL THEN
      BEGIN
        EXECUTE format('CREATE TABLE behavior.%I PARTITION OF behavior.event_raw FOR VALUES FROM (%L) TO (%L)',
                       tname, to_char(d, 'YYYY-MM-DD') || ' 00:00:00+00', to_char(d + 1, 'YYYY-MM-DD') || ' 00:00:00+00');
        n := n + 1;
      EXCEPTION WHEN others THEN
        RAISE WARNING 'behavior.ensure_event_partitions: partisi % gagal dibuat (%)', tname, SQLSTATE;
      END;
    END IF;
  END LOOP;
  RETURN n;
END $$;

-- Retensi staging (L-153): drop partisi harian yang seluruh isinya lebih tua dari p_keep_days + purge DEFAULT.
CREATE OR REPLACE FUNCTION behavior.drop_old_event_partitions(p_keep_days integer DEFAULT 7) RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, behavior AS $$
DECLARE
  r record;
  n integer := 0;
  cutoff date;
BEGIN
  IF p_keep_days IS NULL OR p_keep_days < 1 OR p_keep_days > 7 THEN
    RAISE EXCEPTION 'p_keep_days harus 1..7';
  END IF;
  cutoff := (now() AT TIME ZONE 'UTC')::date - p_keep_days;
  FOR r IN SELECT c.relname
             FROM pg_inherits i
             JOIN pg_class c ON c.oid = i.inhrelid
             JOIN pg_class p ON p.oid = i.inhparent
             JOIN pg_namespace ns ON ns.oid = p.relnamespace
            WHERE ns.nspname = 'behavior' AND p.relname = 'event_raw' AND c.relname ~ '^event_raw_p[0-9]{8}$' LOOP
    IF to_date(substr(r.relname, 12, 8), 'YYYYMMDD') < cutoff THEN
      EXECUTE format('DROP TABLE behavior.%I', r.relname);
      n := n + 1;
    END IF;
  END LOOP;
  DELETE FROM behavior.event_raw_default WHERE received_at < now() - make_interval(days => p_keep_days);
  RETURN n;
END $$;

REVOKE ALL ON FUNCTION behavior.ensure_event_partitions(integer), behavior.drop_old_event_partitions(integer) FROM PUBLIC;
SELECT behavior.ensure_event_partitions(2);

-- ───────────── 2. Wishlist, notify-me, price alert ─────────────
CREATE TABLE IF NOT EXISTS platform.member_wishlist (
  member_id     uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  channel_code  text        NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  product_id    integer     NOT NULL CHECK (product_id > 0),
  added_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (member_id, channel_code, product_id)
);
CREATE INDEX IF NOT EXISTS ix_member_wishlist_product ON platform.member_wishlist (product_id);

CREATE TABLE IF NOT EXISTS platform.restock_alert (
  id            uuid        PRIMARY KEY,
  member_id     uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  channel_code  text        NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  outlet_code   text        NOT NULL CHECK (outlet_code ~ '^[A-Za-z0-9_-]{1,32}$'),
  product_id    integer     NOT NULL CHECK (product_id > 0),
  channels      text[]      NOT NULL CHECK (cardinality(channels) BETWEEN 1 AND 3
                                            AND channels <@ ARRAY['push', 'email', 'wa_unofficial']::text[]),
  created_at    timestamptz NOT NULL DEFAULT now(),
  expires_at    timestamptz NOT NULL,
  sent_at       timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_restock_alert_open ON platform.restock_alert (member_id, channel_code, outlet_code, product_id)
  WHERE sent_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_restock_alert_watch ON platform.restock_alert (channel_code, outlet_code, product_id) WHERE sent_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.price_alert (
  id                uuid        PRIMARY KEY,
  member_id         uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  channel_code      text        NOT NULL CHECK (channel_code ~ '^[A-Z][A-Z0-9-]{1,15}$'),
  product_id        integer     NOT NULL CHECK (product_id > 0),
  target_price_idr  bigint      NOT NULL CHECK (target_price_idr > 0),
  -- Usulan kontrak G50-A: kanal opsional (default push saja) agar email/WA hanya bila dipilih member
  channels          text[]      NOT NULL DEFAULT ARRAY['push']::text[]
                                CHECK (cardinality(channels) BETWEEN 1 AND 3
                                       AND channels <@ ARRAY['push', 'email', 'wa_unofficial']::text[]),
  created_at        timestamptz NOT NULL DEFAULT now(),
  expires_at        timestamptz NOT NULL,
  triggered_at      timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_price_alert_open ON platform.price_alert (member_id, channel_code, product_id) WHERE triggered_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_price_alert_watch ON platform.price_alert (channel_code, product_id) WHERE triggered_at IS NULL;

-- State pengamat (tanpa data member): status stok / harga terakhir yang diamati per produk.
CREATE TABLE IF NOT EXISTS platform.alert_stock_state (
  channel_code  text        NOT NULL,
  outlet_code   text        NOT NULL,
  product_id    integer     NOT NULL,
  status        text        NOT NULL CHECK (status IN ('in_stock', 'low', 'out')),
  restock_seq   integer     NOT NULL DEFAULT 0 CHECK (restock_seq >= 0),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (channel_code, outlet_code, product_id)
);
CREATE TABLE IF NOT EXISTS platform.alert_price_state (
  channel_code   text        NOT NULL,
  product_id     integer     NOT NULL,
  price_idr      bigint      NOT NULL CHECK (price_idr >= 0),
  price_version  integer     NOT NULL DEFAULT 0 CHECK (price_version >= 0),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (channel_code, product_id)
);

-- ───────────── 3. Kampanye ─────────────
CREATE TABLE IF NOT EXISTS platform.campaign (
  campaign_key      text        PRIMARY KEY CHECK (campaign_key ~ '^campaign:[A-Z][A-Z0-9]{1,9}:[0-9]{1,10}:[0-9]{1,6}$'),
  company_code      text        NOT NULL CHECK (company_code ~ '^[A-Z][A-Z0-9]{1,9}$'),
  odoo_campaign_id  integer     NOT NULL CHECK (odoo_campaign_id > 0),
  approval_rev      integer     NOT NULL CHECK (approval_rev >= 0),
  kind              text        NOT NULL CHECK (kind IN ('segment', 'markdown')),
  voucher_mode      text        CHECK (voucher_mode IS NULL OR voucher_mode IN ('anonymous_code', 'public_code', 'member_bound')),
  payload           jsonb       NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  status            text        NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'dispatching', 'done', 'expired', 'disabled')),
  stats             jsonb,
  valid_to          timestamptz NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_campaign_company ON platform.campaign (company_code, odoo_campaign_id);

-- Kode voucher kampanye (L-155/L-164): `anonymous_code` = pool kode tanpa partner (member_id diisi saat penugasan);
-- `public_code` = satu kode bersama (tidak pernah ditugaskan). `member_bound` tidak menyimpan kode di BFF.
CREATE TABLE IF NOT EXISTS platform.member_voucher (
  id            uuid        PRIMARY KEY,
  campaign_key  text        NOT NULL REFERENCES platform.campaign (campaign_key) ON DELETE CASCADE,
  mode          text        NOT NULL CHECK (mode IN ('anonymous_code', 'public_code')),
  code_enc      bytea       NOT NULL,
  code_hash     char(64)    NOT NULL UNIQUE CHECK (code_hash ~ '^[0-9a-f]{64}$'),
  member_id     uuid        REFERENCES platform.member (id) ON DELETE SET NULL,
  assigned_at   timestamptz,
  valid_to      timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_member_voucher_public CHECK (mode = 'anonymous_code' OR (member_id IS NULL AND assigned_at IS NULL))
);
CREATE INDEX IF NOT EXISTS ix_member_voucher_free ON platform.member_voucher (campaign_key) WHERE assigned_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_member_voucher_member ON platform.member_voucher (member_id) WHERE member_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS platform.campaign_delivery (
  campaign_key  text        NOT NULL REFERENCES platform.campaign (campaign_key) ON DELETE CASCADE,
  member_id     uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  status        text        NOT NULL CHECK (status IN ('delivered', 'skipped')),
  reason        text        CHECK (reason IS NULL OR reason ~ '^[a-z_]{1,40}$'),
  channels      jsonb       NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(channels) = 'object'),
  voucher_id    uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (campaign_key, member_id)
);
CREATE INDEX IF NOT EXISTS ix_campaign_delivery_member ON platform.campaign_delivery (member_id, created_at) WHERE status = 'delivered';

CREATE TABLE IF NOT EXISTS platform.member_inbox (
  id            uuid        PRIMARY KEY,
  member_id     uuid        NOT NULL REFERENCES platform.member (id) ON DELETE CASCADE,
  campaign_key  text        NOT NULL,
  title         text        NOT NULL CHECK (length(title) BETWEEN 1 AND 48),
  body          text        NOT NULL CHECK (length(body) BETWEEN 1 AND 160),
  voucher_id    uuid,
  valid_to      timestamptz NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_member_inbox_campaign UNIQUE (member_id, campaign_key)
);
CREATE INDEX IF NOT EXISTS ix_member_inbox_member ON platform.member_inbox (member_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_member_inbox_valid ON platform.member_inbox (valid_to);

-- ───────────── Hak akses ─────────────
REVOKE ALL ON ALL TABLES IN SCHEMA behavior FROM PUBLIC;
REVOKE ALL ON platform.member_wishlist, platform.restock_alert, platform.price_alert, platform.alert_stock_state,
              platform.alert_price_state, platform.campaign, platform.member_voucher, platform.campaign_delivery,
              platform.member_inbox
  FROM PUBLIC, efn_readonly, efn_ocr, efn_etl;

GRANT USAGE ON SCHEMA behavior TO efn_bff, efn_etl;
GRANT SELECT, INSERT, DELETE ON behavior.device_consent, behavior.event_raw, behavior.event_dedup, behavior.segment_chunk,
                                behavior.segment_member, behavior.erased_subject, behavior.subject_erasure,
                                behavior.purchase_emitted TO efn_bff;
GRANT SELECT, INSERT, UPDATE, DELETE ON behavior.device_link, behavior.subject_member TO efn_bff;
GRANT USAGE ON SEQUENCE behavior.device_consent_id_seq, behavior.subject_erasure_id_seq TO efn_bff;
GRANT EXECUTE ON FUNCTION behavior.ensure_event_partitions(integer), behavior.drop_old_event_partitions(integer) TO efn_bff;
-- ETL (ADR-003): grant level kolom = isi services/etl/sql/platform-behavior-grants.sql (dipasang di sini agar deploy.sh
-- tidak butuh file terpisah). Grant tabel penuh dari versi awal file ini dicabut dulu (idempoten).
REVOKE ALL ON behavior.event_raw, behavior.erased_subject, behavior.subject_erasure FROM efn_etl;
GRANT SELECT (event_id, subject_key, subject_kind, company_code, channel, channel_code, outlet_code, type, product_id,
              duration_s, qty, query, result_count, stock_status, price_idr, received_at, ingested_at, consented, page)
    ON behavior.event_raw TO efn_etl;
GRANT SELECT (id, subject_key, request_id, created_at) ON behavior.subject_erasure TO efn_etl;
-- Pertahanan berlapis: tabel penaut identitas tidak pernah terbaca ETL/Text-to-SQL
REVOKE ALL ON behavior.device_link, behavior.device_consent, behavior.subject_member FROM efn_etl, efn_readonly;

GRANT SELECT, INSERT, DELETE ON platform.member_wishlist TO efn_bff;
GRANT SELECT, INSERT, UPDATE, DELETE ON platform.restock_alert, platform.price_alert, platform.alert_stock_state,
                                        platform.alert_price_state, platform.member_inbox TO efn_bff;
GRANT SELECT, INSERT, UPDATE ON platform.campaign, platform.member_voucher, platform.campaign_delivery TO efn_bff;
GRANT DELETE ON platform.member_voucher, platform.campaign_delivery TO efn_bff;

RESET ROLE;

COMMIT;
