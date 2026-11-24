-- Insight "Tahun lalu vs sekarang" (24 Nov 2026): perbandingan YoY sebanding + harga jual vs COGS per menu.
-- Sumber kebenaran tetap fakta ESB (ADR-040 Proposed, G41); view di sini hanya turunan baca. Dibaca insight-web lewat
-- wrapper efn_insight.* (access/insight-access.sql) + row policy efn_insight_scope.
-- PDP: tanpa identitas orang (L-110). Retensi: ikut data sumber.
--
-- Alasan (analisis 24 Nov 2026, docs/runbooks/insight-yoy.md):
-- 1. Perbandingan "bulan ini vs bulan yang sama tahun lalu" hanya sah untuk pasangan cabang-hari yang sama-sama sudah
--    ditarik lengkap (checkpoint `sales` status done). Contoh: 17 Nov 2026 hanya 16/32 slice selesai -> 11 toko kosong.
-- 2. Harga jual per menu harus memisahkan baris berbayar, item gratis (price = 0, COGS tetap ada) dan komponen paket
--    (total = 0, omzet ada di baris header paket); tanpa pemisahan, harga rata-rata turun semu dan diskon bundel hilang.

-- Hari x cabang yang slice penjualannya selesai. company_code = PT cabang (dim_esb_branch); cabang tak dikenal -> ''
-- (row policy `company_code != ''` menolak, fail closed).
CREATE OR REPLACE VIEW efn_dwh.v_esb_sales_done_day AS
SELECT
    c.brand AS brand,
    CAST(ifNull(b.company_code, ''), 'String') AS company_code,
    c.branch_id AS branch_id,
    c.day AS day
FROM
(
    SELECT brand, toUInt32OrZero(scope_key) AS branch_id, day
    FROM efn_esb.extract_checkpoint FINAL
    WHERE entity = 'sales' AND status = 'done'
) AS c
LEFT JOIN (SELECT brand, branch_id, company_code FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = c.brand AND b.branch_id = c.branch_id
SETTINGS join_use_nulls = 1;

-- Ekonomi menu per hari x cabang x nama menu (nama, bukan kode: sejak Jun 2026 menu yang sama punya kode SC000xx selain
-- kode DB/TB lama). Hanya versi slice selesai terakhir (pola v_esb_sales_daily).
--   *_paid : baris a la carte berbayar (price > 0)          -> harga list = gross_paid / qty_paid
--   *_free : baris a la carte gratis (price = 0)            -> biaya promo item gratis
--   *_pkg  : komponen paket (total = 0, COGS ada)           -> nilai list komponen dibanding omzet header
--   pkg_*  : header paket (omzet paket; COGS = 0)
CREATE OR REPLACE VIEW efn_dwh.v_esb_menu_econ_daily AS
SELECT
    a.sales_date AS sales_date,
    a.brand AS brand,
    a.company_code AS company_code,
    a.branch_id AS branch_id,
    a.menu_name AS menu_name,
    a.m_cat AS menu_category,
    a.qty_paid AS qty_paid,
    a.gross_paid AS gross_paid,
    a.disc_paid AS disc_paid,
    a.cogs_paid AS cogs_paid,
    a.qty_free AS qty_free,
    a.cogs_free AS cogs_free,
    a.qty_pkg AS qty_pkg,
    a.cogs_pkg AS cogs_pkg,
    a.pkg_qty AS pkg_qty,
    a.pkg_gross AS pkg_gross,
    a.pkg_disc AS pkg_disc
FROM
(
    SELECT
        s.sales_date AS sales_date, s.brand AS brand, s.company_code AS company_code, s.branch_id AS branch_id,
        s.menu_name AS menu_name,
        anyIf(m.menu_category, m.menu_category != '') AS m_cat,
        sumIf(s.qty, s.row_type = 'ALACARTE' AND s.price > 0) AS qty_paid,
        sumIf(s.total, s.row_type = 'ALACARTE' AND s.price > 0) AS gross_paid,
        sumIf(s.discount, s.row_type = 'ALACARTE' AND s.price > 0) AS disc_paid,
        sumIf(s.cogs, s.row_type = 'ALACARTE' AND s.price > 0) AS cogs_paid,
        sumIf(s.qty, s.row_type = 'ALACARTE' AND s.price = 0) AS qty_free,
        sumIf(s.cogs, s.row_type = 'ALACARTE' AND s.price = 0) AS cogs_free,
        sumIf(s.qty, s.row_type = 'PACKAGE_COMPONENT') AS qty_pkg,
        sumIf(s.cogs, s.row_type = 'PACKAGE_COMPONENT') AS cogs_pkg,
        sumIf(s.qty, s.row_type = 'PACKAGE_HEADER') AS pkg_qty,
        sumIf(s.total, s.row_type = 'PACKAGE_HEADER') AS pkg_gross,
        sumIf(s.discount, s.row_type = 'PACKAGE_HEADER') AS pkg_disc
    FROM
    (
        SELECT brand, company_code, branch_id, sales_date, menu_code, menu_name, row_type, price, qty, total, discount, cogs
        FROM efn_dwh.fact_esb_sales_line FINAL
        WHERE (brand, toString(branch_id), sales_date, _version) IN
              (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'sales')
    ) AS s
    LEFT JOIN (SELECT brand, menu_code, menu_category FROM efn_dwh.dim_esb_menu FINAL) AS m
        ON m.brand = s.brand AND m.menu_code = s.menu_code
    GROUP BY sales_date, brand, company_code, branch_id, menu_name
) AS a;
