-- Migrasi platform Gelombang 47 (docs/04 §28.13.8b v20.3; ADR-041 Proposed): view agregat hasil kampanye untuk ETL.
-- Pemilik isi: efn-data-ai-engineer (konsumen: services/etl/efn_etl/sm_cycle.py job `sm_campaign`). IDEMPOTEN dan
-- transaksional. Hanya CREATE OR REPLACE VIEW + GRANT; tanpa DROP/ubah data. Butuh 20-platform-g50a.sql
-- (platform.campaign, #117 sudah di main).
--   * Volume baru: dijalankan otomatis setelah 01–21 (urutan alfabet docker-entrypoint-initdb.d).
--   * Volume lama: `scripts/deploy.sh` wajib memasukkan 22 ke daftar migrasi >= 04 (tugas devsecops). Manual (dev):
--       docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/22-platform-g47.sql
--
-- PDP: view TANPA member_id, payload, kode voucher, atau baris per member. `stats` = agregat jumlah kirim BFF.
-- efn_etl hanya SELECT pada view; tabel platform.campaign* tetap REVOKE dari efn_etl (20-platform-g50a.sql). View
-- dieksekusi dengan hak pemiliknya (efn_platform_owner, SEC-39), jadi efn_etl tidak butuh grant tabel.

\connect efn_platform
\set ON_ERROR_STOP on

BEGIN;

SET LOCAL ROLE efn_platform_owner;   -- SEC-39: objek dimiliki role NOLOGIN

CREATE OR REPLACE VIEW platform.v_campaign_stats AS
SELECT campaign_key, company_code, odoo_campaign_id, approval_rev, kind, voucher_mode, status,
       (stats->>'delivered')::bigint AS delivered,
       (stats->>'skipped')::bigint AS skipped,
       (stats->>'push_sent')::bigint AS push_sent,
       (stats->>'email_queued')::bigint AS email_queued,
       (stats->>'wa_queued')::bigint AS wa_queued,
       stats->>'stop_reason' AS stop_reason,
       valid_to, updated_at
FROM platform.campaign;

REVOKE ALL ON platform.v_campaign_stats FROM PUBLIC;
GRANT SELECT ON platform.v_campaign_stats TO efn_etl;

RESET ROLE;

COMMIT;
