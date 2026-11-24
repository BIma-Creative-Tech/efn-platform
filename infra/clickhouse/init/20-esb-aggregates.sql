-- Tabel agregat harian Insight (keputusan pemilik proyek 22 Nov 2026, docs/design/insight-web.md; ADR-040 Proposed,
-- ADR-041 Proposed, K25-02). Idempoten (CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh
-- setiap deploy setelah 12/13 (view sumber harus sudah ada) dan SEBELUM access/*.sql (wrapper efn_insight membaca ini).
--
-- Alasan: efn_dwh.v_esb_sales_branch_daily / v_esb_sales_daily / v_esb_flow_daily menghitung ulang setiap query (FINAL
-- fakta penuh + filter checkpoint + uniqExact + JOIN dimensi) -> Insight galat 159/241 setelah data 1 tahun. Tabel di
-- bawah = materialisasi view tersebut (view TIDAK diubah; tetap dipakai text2sql/forecast/mig/metabase dan sebagai
-- sumber refresh). Kolom = keluaran view (nama + tipe diambil dari view lewat AS SELECT ... WHERE 0 -> tabel kosong).
--
-- SEC-AG-01 (review 22 Nov 2026): SEMUA tabel (agg + build) dibuat di sini oleh admin -- engine MergeTree dan
-- storage_policy 'encrypted' (ADR-022) ditetapkan admin; esb_writer TIDAK punya CREATE/DROP TABLE. Build permanen
-- `<agg>_build` = struktur identik dengan agg (`AS <agg>`), syarat REPLACE PARTITION.
-- Isi: HANYA `python -m efn_esb refresh-aggregates` (services/esb-extract/efn_esb/aggregates.py): per bulan TRUNCATE
-- build -> INSERT bulan itu dari view -> pagar penyusutan -> ALTER TABLE agg REPLACE PARTITION ID '<yyyymm>' FROM build
-- (atomik per bulan), lalu DROP PARTITION untuk bulan yang tidak lagi ada di sumber. Otomatis di akhir siklus run/backfill
-- yang menulis data. Setelah deploy pertama tabel KOSONG -> refresh pertama wajib (runbook esb-extract §6a).
-- Definisi agg di bawah diuji sama persis dengan aggregates.table_definition() (test_ddl.py); ubah keduanya bersamaan.
-- Perubahan kolom view sumber: clickhouse-init.sh mendeteksi kolom agg/build != view, menghapus agg + build (data
-- turunan), menjalankan ulang file ini; refresh-aggregates wajib sesudahnya. clickhouse-init.sh juga GAGAL bila salah satu
-- dari 6 tabel bukan MergeTree/encrypted (pagar fail-closed).
-- Keamanan: insight_reader tidak punya hak di efn_dwh (REVOKE ALL); tabel ini hanya dibaca lewat view efn_insight.*
-- SQL SECURITY DEFINER + row policy efn_insight_scope (insight-access.sql, clickhouse-init.sh).
-- PDP: agregat tanpa identitas orang (L-110), sama dengan view sumber. Retensi: ikut data sumber (dibangun ulang penuh).

CREATE TABLE IF NOT EXISTS efn_dwh.agg_esb_sales_branch_daily
ENGINE = MergeTree
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, sales_date, branch_id)
SETTINGS storage_policy = 'encrypted'
AS SELECT sales_date, brand, company_code, branch_id, branch_name, trx_count, line_count, qty, gross, discount, net, cogs
FROM efn_dwh.v_esb_sales_branch_daily
WHERE 0;

CREATE TABLE IF NOT EXISTS efn_dwh.agg_esb_sales_daily
ENGINE = MergeTree
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, sales_date, branch_id, menu_code)
SETTINGS storage_policy = 'encrypted'
AS SELECT sales_date, brand, company_code, branch_id, branch_name, menu_code, menu_name, menu_category, menu_category_detail, is_takeaway, qty, gross, discount, net, cogs, trx_count
FROM efn_dwh.v_esb_sales_daily
WHERE 0;

CREATE TABLE IF NOT EXISTS efn_dwh.agg_esb_flow_daily
ENGINE = MergeTree
PARTITION BY toYYYYMM(day)
ORDER BY (company_code, brand, day, branch_id, product_code)
SETTINGS storage_policy = 'encrypted'
AS SELECT day, brand, company_code, branch_id, branch_name, product_code, product_name, ck_prod, ck_out, in_ck, in_other, store_prod, st_in, st_out, sold
FROM efn_dwh.v_esb_flow_daily
WHERE 0;

-- Build permanen (area kerja refresh; kosong di luar refresh). Struktur, engine, partisi, ORDER BY, storage policy =
-- agg (CREATE TABLE ... AS <tabel>).
CREATE TABLE IF NOT EXISTS efn_dwh.agg_esb_sales_branch_daily_build AS efn_dwh.agg_esb_sales_branch_daily;
CREATE TABLE IF NOT EXISTS efn_dwh.agg_esb_sales_daily_build AS efn_dwh.agg_esb_sales_daily;
CREATE TABLE IF NOT EXISTS efn_dwh.agg_esb_flow_daily_build AS efn_dwh.agg_esb_flow_daily;

-- Bauran menu untuk wrapper efn_insight.v_esb_menu_mix: badan SAMA dengan efn_dwh.v_esb_menu_mix (12-g41-esb.sql) tetapi
-- membaca agg_esb_sales_daily (kolom + tipe identik dengan v_esb_sales_daily) -> keluaran sama, tanpa FINAL fakta penuh.
-- v_esb_menu_mix asli tetap untuk Metabase. Diuji sama dengan badan aslinya kecuali sumber (test_insight_access.py).
CREATE OR REPLACE VIEW efn_dwh.v_esb_menu_mix_agg AS
SELECT
    m.month AS month, m.brand AS brand, m.company_code AS company_code, m.branch_id AS branch_id,
    m.b_name AS branch_name, m.m_cat AS menu_category, m.m_takeaway AS is_takeaway,
    m.qty_m AS qty, m.net_m AS net,
    if(sum(m.qty_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) = 0, NULL,
       round(m.qty_m / sum(m.qty_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) * 100, 2)) AS share_qty,
    if(sum(m.net_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) = 0, NULL,
       round(m.net_m / sum(m.net_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) * 100, 2)) AS share_net
FROM
(
    SELECT toStartOfMonth(v.sales_date) AS month, v.brand AS brand, v.company_code AS company_code,
           v.branch_id AS branch_id, any(v.branch_name) AS b_name,
           if(v.menu_category = '', 'Tanpa kategori', v.menu_category) AS m_cat, v.is_takeaway AS m_takeaway,
           toFloat64(sum(v.qty)) AS qty_m, toFloat64(sum(v.net)) AS net_m
    FROM efn_dwh.agg_esb_sales_daily AS v
    GROUP BY month, brand, company_code, branch_id, m_cat, m_takeaway
) AS m;

-- Kesegaran: satu baris per agregat per refresh (status ok | busy | error). error = kelas exception + kode ClickHouse
-- / `shrink` / `lock_lost` saja (tanpa pesan/query). Kapan terakhir dibangun:
--   SELECT name, max(finished_at) FROM efn_esb.aggregate_refresh WHERE status = 'ok' GROUP BY name;
-- Retensi 400 hari (catatan operasional, tanpa data pribadi).
CREATE TABLE IF NOT EXISTS efn_esb.aggregate_refresh
(
    name         LowCardinality(String),
    status       LowCardinality(String),
    started_at   DateTime64(3, 'UTC'),
    finished_at  DateTime64(3, 'UTC'),
    rows         UInt64,
    months       UInt16,
    first_day    Date,                              -- 1970-01-01 bila belum ada data / gagal
    last_day     Date,
    error        String
)
ENGINE = MergeTree
ORDER BY (name, finished_at)
TTL toDateTime(finished_at) + INTERVAL 400 DAY;

-- Kunci refresh (SEC-AG-02): satu refresh-aggregates pada satu waktu. Proses menulis baris (run_id) -> waktu diisi SERVER
-- (DEFAULT now64) agar urutan tidak bergantung jam container. Pemegang = run dengan heartbeat terakhir < 15 menit dan
-- belum `released`, diurutkan (min started_at, run_id). Heartbeat = baris baru tiap bulan selesai; selesai/gagal = baris
-- released = 1. Retensi 1 hari (kunci operasional, tanpa data pribadi).
CREATE TABLE IF NOT EXISTS efn_esb.aggregate_refresh_lock
(
    run_id        UUID,
    started_at    DateTime64(3, 'UTC') DEFAULT now64(3),
    heartbeat_at  DateTime64(3, 'UTC') DEFAULT now64(3),
    released      UInt8 DEFAULT 0
)
ENGINE = MergeTree
ORDER BY (run_id, heartbeat_at)
TTL toDateTime(heartbeat_at) + INTERVAL 1 DAY;
