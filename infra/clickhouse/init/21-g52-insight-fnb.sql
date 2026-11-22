-- G52 (docs/04 §28.15.8 v21.1, L-244; ADR-040 Proposed, ADR-041 §3.6 Proposed, K25-02): persona F&B Toko.
-- View tambahan efn_dwh untuk halaman insight-web `/fnb/store` (kiriman CK yang diterima toko). Dibaca HANYA lewat
-- efn_insight.v_esb_store_receipt_daily (SQL SECURITY DEFINER efn_insight_definer, infra/clickhouse/access/
-- insight-access.sql) + row policy `efn_insight_scope` kelas BR (cabang; access/insight-esb-policy.sh).
-- Tiga view toko lain (sales/menu/payment) TIDAK punya view efn_dwh baru: wrapper efn_insight membaca sumber yang sama
-- dengan pasangannya (v_esb_sales_branch_daily / v_esb_sales_daily / v_esb_payment_daily), sehingga saat
-- 20-esb-aggregates.sql (branch feat/esb-daily-aggregates) mengalihkan wrapper lama ke tabel agregat, wrapper toko
-- dialihkan bersama (test_insight_fnb_access.py menegaskan pasangan sumber sama). File ini tidak bergantung pada 20.
-- Idempoten (CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah 19 (urutan nama file).
--
-- Aturan penulisan sama dengan 13/19 (diuji services/esb-extract/tests/test_insight_fnb_access.py): setiap kolom
-- keluaran SELECT teratas memakai `AS <nama>` (CAST(x, 'T')), kolom company_code selalu terisi.
-- PDP: tanpa customer/member/karyawan (L-110, L-249); hanya agregat, nama cabang/produk.

-- Sisi TERIMA kiriman (kind = 'delivery') per cabang tujuan. Grain = v_esb_movement_daily (hari x brand x cabang asal x
-- cabang tujuan x produk x satuan); qty tidak pernah dijumlah lintas uom.
--   company_code = PT cabang TUJUAN (bukan PT asal seperti v_esb_movement_daily, SEC-IR-02/03), branch_id = cabang tujuan.
--   INNER JOIN dim_esb_branch (company_code != '', is_store = 1): kiriman ke cabang tujuan yang tidak dikenal/tanpa PT
--   atau bukan toko (CK/gudang) TIDAK muncul (fail closed, negatif E8 §28.15.7).
--   origin_* = cabang asal (CK) apa adanya dari v_esb_movement_daily: metadata kiriman yang diterima toko itu sendiri
--   (boleh milik PT lain dalam brand yang sama, §28.15.8).
-- SQL SECURITY default (INVOKER) seperti view efn_dwh lain -> dieksekusi dengan hak efn_insight_definer.
CREATE OR REPLACE VIEW efn_dwh.v_esb_store_receipt_daily AS
SELECT
    m.day AS day, m.brand AS brand,
    CAST(b.company_code, 'String') AS company_code,
    m.dest_branch_id AS branch_id,
    if(b.branch_name = '', concat('Cabang #', toString(m.dest_branch_id)), b.branch_name) AS branch_name,
    m.origin_branch_id AS origin_branch_id, m.origin_branch_name AS origin_branch_name, m.origin_kind AS origin_kind,
    m.item_group AS item_group, m.category AS category, m.product_code AS product_code, m.product_name AS product_name,
    m.uom AS uom, m.qty AS qty, m.lines AS lines
FROM
(
    SELECT day, brand, dest_branch_id, origin_branch_id, origin_branch_name, origin_kind, item_group, category,
           product_code, product_name, uom, qty, lines
    FROM efn_dwh.v_esb_movement_daily
    WHERE kind = 'delivery'
) AS m
INNER JOIN (SELECT brand, branch_id, branch_name, company_code FROM efn_dwh.dim_esb_branch FINAL
            WHERE company_code != '' AND is_store = 1) AS b
    ON b.brand = m.brand AND b.branch_id = m.dest_branch_id;
