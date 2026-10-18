-- Migrasi platform Gelombang 18 — privasi (W-18 Q-P18, W-19 Q-P3). Pemilik: services/api-gateway (efn-bff-engineer).
-- Keputusan pemilik proyek 10 Okt 2026 (docs/decisions/README.md, PDP_DATA_INVENTORY v7 §0a).
-- IDEMPOTEN dan transaksional (satu BEGIN/COMMIT). Hanya ADD COLUMN dengan default → aman untuk BFF lama.
--   * Volume baru: dijalankan otomatis setelah 01–07 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` menjalankan semua file >= 04 SEBELUM BFF baru start (BFF G18 membutuhkan kolom
--     ini: INSERT dsar_request menulis `open_orders_ack_at`, job erasure membaca `defer_escalation`, login menulis
--     `age_confirmed_at`). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/08-platform-g18-privacy.sql
--
-- Isi:
-- 1. `dsar_request.open_orders_ack_at` — waktu pelanggan MENGONFIRMASI hapus akun walau ada pesanan online terbuka
--    (`DELETE /v1/me?confirm_open_orders=true`); bukti pilihan sadar (L-4 + Q-P18). NULL = tidak ada pesanan terbuka.
-- 2. `dsar_request.defer_escalation` — tingkat eskalasi CS yang sudah dikirim (0 = belum, 1 = hari 3, 2 = hari 10)
--    selama erasure tertahan pesanan online terbuka; mencegah alert berulang tiap siklus job (multi-replika aman).
-- 3. `member.age_confirmed_at` (+ `age_confirmed_source`, `age_confirmed_pos_config_id`, §9.9 K-18-8) — waktu & kanal pernyataan usia minimum (17 th, Q-P3) saat login Super-App atau pendaftaran kasir POS. Tanpa tanggal
--    lahir (minimisasi data). Ikut terhapus bersama baris member saat erasure. Member hasil migrasi: NULL sampai
--    login Super-App atau pendaftaran ulang lewat kasir (`pos_enroll`).
-- 5. (§9.9 K-18-5/K-18-6) `platform.loyalty_pos_reverse` — satu baris per refund POS ber-member: nilai Rupiah baris
--    pembayaran Poin EFN negatif yang dilaporkan Odoo (`points_restored_value`) + poin yang benar-benar dikembalikan.
--    Dibaca ETL rekonsiliasi harian (`services/etl/efn_etl/loyalty_recon.py` `_PLATFORM_RESTORE_ROWS`). Ditulis BFF di
--    transaksi yang sama dengan pengembalian poin (idempoten per `refund_order_ref`). Retensi = data keuangan 10 th
--    (Q-P2): BFF TIDAK mem-purge (sama dengan ledger). Tanpa PII (tanpa member_hash; order_ref opak).
-- PII: tidak ada kolom PII baru (stempel waktu saja). Risiko: R-L01, R-B05 (rekonsiliasi poin).

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

ALTER TABLE platform.dsar_request ADD COLUMN IF NOT EXISTS open_orders_ack_at timestamptz;
ALTER TABLE platform.dsar_request ADD COLUMN IF NOT EXISTS defer_escalation smallint NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_dsar_defer_escalation'
                   AND conrelid = 'platform.dsar_request'::regclass) THEN
    ALTER TABLE platform.dsar_request
      ADD CONSTRAINT ck_dsar_defer_escalation CHECK (defer_escalation BETWEEN 0 AND 2);
  END IF;
END $$;

ALTER TABLE platform.member ADD COLUMN IF NOT EXISTS age_confirmed_at timestamptz;
-- 4. (§9.9 K-18-8) kanal pernyataan usia: 'app' (login Super-App) | 'pos' (kasir, OTP `pos_enroll`) + pos.config kasir
--    yang mencentang (jejak prosedural, bukan PII). NULL bila belum ada pernyataan.
ALTER TABLE platform.member ADD COLUMN IF NOT EXISTS age_confirmed_source text;
ALTER TABLE platform.member ADD COLUMN IF NOT EXISTS age_confirmed_pos_config_id integer;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_member_age_confirmed_source'
                   AND conrelid = 'platform.member'::regclass) THEN
    ALTER TABLE platform.member ADD CONSTRAINT ck_member_age_confirmed_source CHECK (
      (age_confirmed_source IS NULL OR age_confirmed_source IN ('app', 'pos'))
      AND (age_confirmed_pos_config_id IS NULL OR (age_confirmed_source = 'pos' AND age_confirmed_pos_config_id > 0)));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS platform.loyalty_pos_reverse (
  refund_order_ref      text        PRIMARY KEY CHECK (refund_order_ref ~ '^[\x20-\x7e]{1,64}$'),
  original_order_ref    text        NOT NULL CHECK (original_order_ref ~ '^[\x20-\x7e]{1,64}$'),
  company_code          text        NOT NULL CHECK (company_code IN ('EFN', 'GL', 'PB', 'BCR', 'BCF', 'FARM')),  -- G20 §12.1 (volume lama: 09)
  points_restored_value bigint      NOT NULL CHECK (points_restored_value >= 0),
  points_restored       integer     NOT NULL CHECK (points_restored >= 0),
  idempotency_key       text        NOT NULL UNIQUE CHECK (char_length(idempotency_key) <= 128),
  created_at            timestamptz NOT NULL DEFAULT now(),
  CHECK (refund_order_ref <> original_order_ref)
);
CREATE INDEX IF NOT EXISTS ix_loyalty_pos_reverse_created ON platform.loyalty_pos_reverse (created_at);
CREATE INDEX IF NOT EXISTS ix_loyalty_pos_reverse_original ON platform.loyalty_pos_reverse (original_order_ref);
-- 6. (review r11 SEC-118, L-25f) rasio refund (eligible) per reverse: BFF menghitung restore yang diharapkan secara
--    kumulatif (Σ rasio × nilai redeem − Σ nilai restore sebelumnya) dari data miliknya sendiri. Kolom ditambah idempoten
--    agar volume yang sudah menjalankan 08 versi sebelumnya ikut.
ALTER TABLE platform.loyalty_pos_reverse ADD COLUMN IF NOT EXISTS refund_ratio numeric(9,6) NOT NULL DEFAULT 0;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_pos_reverse_ratio'
                   AND conrelid = 'platform.loyalty_pos_reverse'::regclass) THEN
    ALTER TABLE platform.loyalty_pos_reverse
      ADD CONSTRAINT ck_loyalty_pos_reverse_ratio CHECK (refund_ratio >= 0 AND refund_ratio <= 1);
  END IF;
END $$;

-- 8. (re-review G18 SEC-128) cek restore berbasis NILAI incl pajak, tidak bergantung kategori earn:
--    reservasi committed menyimpan total barang order final incl pajak (`order_goods_incl`, dari commit), reverse menyimpan
--    nilai barang refund incl pajak (`refund_goods_incl`) dan persetujuan supervisor hold/review (`restore_approved`).
ALTER TABLE platform.loyalty_reservation ADD COLUMN IF NOT EXISTS order_goods_incl bigint;
ALTER TABLE platform.loyalty_pos_reverse ADD COLUMN IF NOT EXISTS refund_goods_incl bigint;
ALTER TABLE platform.loyalty_pos_reverse ADD COLUMN IF NOT EXISTS restore_approved boolean NOT NULL DEFAULT false;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_reservation_order_goods'
                   AND conrelid = 'platform.loyalty_reservation'::regclass) THEN
    ALTER TABLE platform.loyalty_reservation
      ADD CONSTRAINT ck_loyalty_reservation_order_goods CHECK (order_goods_incl IS NULL OR order_goods_incl > 0);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_loyalty_pos_reverse_goods'
                   AND conrelid = 'platform.loyalty_pos_reverse'::regclass) THEN
    ALTER TABLE platform.loyalty_pos_reverse
      ADD CONSTRAINT ck_loyalty_pos_reverse_goods CHECK (refund_goods_incl IS NULL OR refund_goods_incl >= 0);
  END IF;
END $$;

-- 9. (QA G18-QA-01) event Odoo→BFF: kunci idempotensi baris = kunci STABIL BFF `odoo-ev:<platform_order_ref>:<event>:<seq>[:<event_id>]`
--    (bukan id lokal Odoo yang terpakai ulang setelah restore/rebuild DB). `payload_sha256` = sidik isi (kunci sama isi beda →
--    409 + anomali), `source_key` = kunci asli Odoo (audit, kompatibilitas format lama). NULL untuk baris lama.
ALTER TABLE platform.online_order_event ADD COLUMN IF NOT EXISTS payload_sha256 char(64);
ALTER TABLE platform.online_order_event ADD COLUMN IF NOT EXISTS source_key text;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_online_order_event_payload'
                   AND conrelid = 'platform.online_order_event'::regclass) THEN
    ALTER TABLE platform.online_order_event ADD CONSTRAINT ck_online_order_event_payload CHECK (
      (payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$') AND (source_key IS NULL OR length(source_key) BETWEEN 1 AND 200));
  END IF;
END $$;

RESET ROLE;

-- loyalty_pos_reverse: efn_bff hanya SELECT/INSERT (append-only, tanpa UPDATE/DELETE — catatan keuangan 10 th);
-- efn_readonly (ETL rekonsiliasi, anggota: efn_etl) kolom non-PII.
REVOKE ALL ON platform.loyalty_pos_reverse FROM PUBLIC, efn_bff, efn_readonly, efn_ocr, efn_etl;
GRANT SELECT, INSERT ON platform.loyalty_pos_reverse TO efn_bff;
GRANT SELECT (refund_order_ref, original_order_ref, company_code, points_restored_value, points_restored, created_at)
  ON platform.loyalty_pos_reverse TO efn_readonly;

-- 7. (review r11 SEC-124, L-25l) erasure DSAR TIDAK lagi menghapus reservasi `committed` (nilai keuangan redeem, sisi BFF
--    rekonsiliasi K-18-6, retensi 10 th) — hanya `member_hash` → tombstone acak. Hak UPDATE kolom itu saja.
GRANT UPDATE (member_hash) ON platform.loyalty_reservation TO efn_bff;
-- SEC-128: commit mengisi total order final (sekali, bersama status committed)
GRANT UPDATE (order_goods_incl) ON platform.loyalty_reservation TO efn_bff;
GRANT SELECT (order_goods_incl) ON platform.loyalty_reservation TO efn_readonly;
GRANT SELECT (refund_ratio, refund_goods_incl, restore_approved) ON platform.loyalty_pos_reverse TO efn_readonly;

-- Hak akses: efn_bff sudah SELECT/INSERT/UPDATE tingkat tabel (04-platform-v3-6.sql) → kolom baru tercakup.
-- efn_etl hanya SELECT kolom tertentu dsar_request (03) → kolom baru TIDAK terlihat ETL (sengaja).

COMMIT;
