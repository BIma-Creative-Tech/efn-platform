-- Gelombang 42 (Dashboard Insight, kontrak G42 bagian B; ADR-041 Proposed, ADR-040 Proposed, K25-02).
-- View tambahan di efn_dwh untuk halaman insight-web. Dibaca HANYA lewat efn_insight.* (view SQL SECURITY DEFINER +
-- row policy `efn_insight_scope` per company_code, infra/clickhouse/access/insight-access.sql + clickhouse-init.sh).
-- Idempoten (CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah 12-g41-esb.sql.
--
-- Aturan penulisan (diuji services/esb-extract/tests/test_insight_access.py): setiap kolom keluaran SELECT teratas
-- memakai `AS <nama>` dan setiap view memiliki kolom `company_code` yang terisi (row policy memfilter kolom itu; baris
-- tanpa company_code tidak pernah terlihat oleh insight_reader -> fail closed).
-- PDP: tanpa customer/member/karyawan (L-110); hanya agregat, nama cabang/produk/menu.

-- Hari x brand x PT x cabang (tanpa dimensi menu). `trx_count` = jumlah salesNum unik per cabang-hari.
-- Alasan: v_esb_sales_daily.trx_count dihitung per menu, sehingga menjumlahkannya lintas menu menghitung transaksi
-- berulang (satu transaksi dengan 3 menu = 3). KPI "transaksi" dan "rata-rata per transaksi" wajib memakai view ini
-- (Q-INS2 docs/design/insight-web.md). Catatan fakta G41: `total` ESB belum termasuk PB1/service charge
-- (total - discount ~ 89,6% pembayaran), jadi net view ini != total pembayaran (Q-INS4).
CREATE OR REPLACE VIEW efn_dwh.v_esb_sales_branch_daily AS
SELECT
    a.sales_date AS sales_date, a.brand AS brand, a.company_code AS company_code, a.branch_id AS branch_id,
    if(b.branch_name = '', concat('Cabang #', toString(a.branch_id)), b.branch_name) AS branch_name,
    a.trx_s AS trx_count, a.lines_s AS line_count, a.qty_s AS qty, a.gross_s AS gross, a.discount_s AS discount,
    a.gross_s - a.discount_s AS net, a.cogs_s AS cogs
FROM
(
    SELECT s.sales_date AS sales_date, s.brand AS brand, s.company_code AS company_code, s.branch_id AS branch_id,
           uniqExact(s.sales_num) AS trx_s, count() AS lines_s, sum(s.qty) AS qty_s, sum(s.total) AS gross_s,
           sum(s.discount) AS discount_s, sum(s.cogs) AS cogs_s
    FROM
    (
        SELECT brand, company_code, branch_id, sales_date, sales_num, qty, total, discount, cogs
        FROM efn_dwh.fact_esb_sales_line FINAL
        WHERE (brand, toString(branch_id), sales_date, _version) IN
              (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'sales')
    ) AS s
    GROUP BY sales_date, brand, company_code, branch_id
) AS a
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = a.brand AND b.branch_id = a.branch_id;

-- Daftar cabang untuk filter global (PT = PT cabang, termasuk override Bacha BCF/BCR).
CREATE OR REPLACE VIEW efn_dwh.v_esb_branch_list AS
SELECT
    d.brand AS brand, d.company_code AS company_code, d.branch_id AS branch_id, d.branch_code AS branch_code,
    d.branch_name AS branch_name, d.is_ck AS is_ck, d.is_store AS is_store, d.active AS active
FROM (SELECT brand, company_code, branch_id, branch_code, branch_name, is_ck, is_store, active
      FROM efn_dwh.dim_esb_branch FINAL) AS d
WHERE d.company_code != '';

-- Daftar produk untuk filter. Master produk ESB milik BRAND (satu akun ESB per brand), bukan per PT:
-- dim_esb_product.company_code = PT default brand (Bacha -> BCF) sehingga tidak bisa dipakai untuk scope PT.
-- Keputusan G42: satu baris per (PT yang punya cabang di brand itu) x produk, PT diambil dari dim_esb_branch.
-- Akibatnya user BCR melihat katalog Bacha yang sama dengan BCF (katalog brand bersama, bukan data transaksi), user PB
-- tidak pernah melihat katalog brand lain. Brand tanpa cabang terdaftar -> tidak ada baris (fail closed).
CREATE OR REPLACE VIEW efn_dwh.v_esb_product_list AS
SELECT
    p.brand AS brand, c.company_code AS company_code, p.product_code AS product_code,
    p.product_name AS product_name, p.category AS category, p.uom AS uom, p.is_fin AS is_fin, p.active AS active
FROM (SELECT brand, product_code, product_name, category, uom, is_fin, active
      FROM efn_dwh.dim_esb_product FINAL) AS p
INNER JOIN (SELECT DISTINCT brand, company_code FROM efn_dwh.dim_esb_branch FINAL WHERE company_code != '') AS c
    ON c.brand = p.brand;

-- v_esb_data_quality dengan company_code terisi di SETIAP baris. Cek per brand (data_age_hours, last_run_not_ok)
-- tidak punya PT (company_code = '') -> diduplikasi ke setiap PT yang punya cabang di brand itu (Bacha -> BCF dan BCR;
-- isi baris = metadata ekstraksi brand: umur data per entitas / status run, tanpa angka transaksi). Baris yang sudah
-- ber-PT diteruskan apa adanya. Brand tanpa cabang terdaftar -> baris per brand hilang (fail closed).
-- v_esb_data_quality asli tetap untuk Metabase (analis Holding).
CREATE OR REPLACE VIEW efn_dwh.v_esb_data_quality_pt AS
SELECT
    q.check_name AS check_name, q.brand AS brand, q.company_code AS company_code, q.item AS item, q.value AS value,
    q.last_day AS last_day
FROM (SELECT check_name, brand, company_code, item, value, last_day FROM efn_dwh.v_esb_data_quality
      WHERE company_code != '') AS q
UNION ALL
SELECT
    u.check_name AS check_name, u.brand AS brand, c.company_code AS company_code, u.item AS item, u.value AS value,
    u.last_day AS last_day
FROM (SELECT check_name, brand, item, value, last_day FROM efn_dwh.v_esb_data_quality
      WHERE company_code = '') AS u
INNER JOIN (SELECT DISTINCT brand, company_code FROM efn_dwh.dim_esb_branch FINAL WHERE company_code != '') AS c
    ON c.brand = u.brand;
