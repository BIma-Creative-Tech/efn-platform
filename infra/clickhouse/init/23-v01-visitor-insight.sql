-- V-01 (keputusan pemilik proyek 23 Nov 2026: log kunjungan visitor anonim tanpa consent, tampil di Insight; demo SMD
-- dan PB). Pemilik: efn-data-ai-engineer. Idempoten (CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setelah
-- 16-g50-behavior.sql (tabel agg_visitor_*).
--
-- View semantik efn_dwh.v_vis_* untuk halaman Insight "Pengunjung" LINTAS PT (kunci = company_code, bukan company_id/
-- warehouse): satu katalog query untuk PT F&B (PB, kode ESB = kode Odoo) dan supermarket (SMD). Dibungkus view DEFINER
-- efn_insight.v_vis_* (access/insight-visitor-access.sql) + row policy efn_insight_scope bertanda tangan sendiri
-- (access/insight-visitor-policy.sh: SQL_efn_vcodes + SQL_efn_vsig).
--
-- Sumber = agregat anonim agg_visitor_* (tanpa subject_key, TTL 2 tahun) yang dihitung dari SEMUA visitor (anonim +
-- consented). Tidak ada akses ke fact_visitor_event (per subjek). Satuan visitor = subjek-hari (Σ per hari; dan untuk
-- funnel/produk juga Σ per outlet) -> label jujur di UI, pola Q-SM41. Istilah pencarian sudah k-anonim di agregat
-- (ETL_BEHAVIOR_K_MIN); produk di-k-anonim di query Insight (HAVING viewers >= K_ANON).
-- Semua item SELECT teratas ber-AS (pola test_insight_sm_access.py).

-- Halaman per hari x kanal. page = '*' = semua visitor hari itu (lihat agg_visitor_page_daily).
CREATE OR REPLACE VIEW efn_dwh.v_vis_page_daily AS
SELECT
    p.day AS day,
    toString(p.cc) AS company_code,
    toString(p.ch) AS channel,
    toString(p.pg) AS page,
    p.visitors_n AS visitors,
    p.views_n AS views
FROM
(
    SELECT day, company_code AS cc, channel AS ch, page AS pg, visitors AS visitors_n, views AS views_n
    FROM efn_dwh.agg_visitor_page_daily FINAL
) AS p;

-- Funnel subjek per hari x kanal (Σ subjek-hari per outlet; event tanpa outlet = satu kelompok).
CREATE OR REPLACE VIEW efn_dwh.v_vis_funnel_daily AS
SELECT
    f.day AS day,
    toString(f.cc) AS company_code,
    toString(f.ch) AS channel,
    sum(f.subjects_n) AS subjects,
    sum(f.viewers_n) AS viewers,
    sum(f.carters_n) AS carters,
    sum(f.checkout_n) AS checkout_starters,
    sum(f.purchasers_n) AS purchasers,
    sum(f.no_result_n) AS searchers_no_result
FROM
(
    SELECT day, company_code AS cc, channel AS ch, subjects AS subjects_n, viewers AS viewers_n, carters AS carters_n,
           checkout_starters AS checkout_n, purchasers AS purchasers_n, searchers_no_result AS no_result_n
    FROM efn_dwh.agg_visitor_funnel_daily FINAL
) AS f
GROUP BY f.day, f.cc, f.ch;

-- Produk dilihat vs dibeli per hari x kanal (+ nama produk Odoo; tanpa harga/stok).
CREATE OR REPLACE VIEW efn_dwh.v_vis_product_daily AS
SELECT
    a.day AS day,
    toString(a.cc) AS company_code,
    toString(a.ch) AS channel,
    a.product_id AS product_id,
    p.pname AS product_name,
    a.views_n AS views,
    a.viewers_n AS viewers,
    a.carts_n AS cart_adds,
    a.buys_n AS purchases
FROM
(
    SELECT day, company_code AS cc, channel AS ch, product_id, sum(views) AS views_n, sum(viewers) AS viewers_n,
           sum(cart_adds) AS carts_n, sum(purchases) AS buys_n
    FROM efn_dwh.agg_visitor_product_daily FINAL
    GROUP BY day, company_code, channel, product_id
) AS a
LEFT JOIN (SELECT product_id, name AS pname FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = a.product_id;

-- Istilah pencarian per hari x kanal (sudah >= k subjek per hari x kanal_code di agregat).
CREATE OR REPLACE VIEW efn_dwh.v_vis_search_daily AS
SELECT
    s.day AS day,
    toString(s.cc) AS company_code,
    toString(s.ch) AS channel,
    s.q AS query,
    sum(s.searches_n) AS searches,
    sum(s.no_result_n) AS no_result,
    sum(s.subjects_n) AS subjects
FROM
(
    SELECT day, company_code AS cc, channel AS ch, query AS q, searches AS searches_n, no_result AS no_result_n,
           subjects AS subjects_n
    FROM efn_dwh.agg_visitor_search_daily FINAL
) AS s
GROUP BY s.day, s.cc, s.ch, s.q;
