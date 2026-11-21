-- Review Insight 21 Nov 2026 (docs/04 §26.7a; ADR-041 Proposed, ADR-040 Proposed, K25-02).
-- View tambahan efn_dwh untuk halaman insight-web "Produksi vs Penjualan" dan penanda cakupan data. Dibaca HANYA
-- lewat efn_insight.* (view SQL SECURITY DEFINER + row policy `efn_insight_scope` per company_code;
-- infra/clickhouse/access/insight-access.sql + loop policy di clickhouse-init.sh).
-- Idempoten (CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah 12/13 (urutan nama file).
--
-- Aturan penulisan sama dengan 13-g42-insight.sql (diuji services/esb-extract/tests/test_insight_access.py): setiap
-- kolom keluaran SELECT teratas memakai `AS <nama>` (konversi tipe memakai CAST(x, 'T'), bukan CAST(x AS T)), dan
-- setiap view punya kolom `company_code` yang terisi (baris tanpa PT tidak pernah terlihat insight_reader).
-- PDP: tanpa customer/member/karyawan (L-110); hanya agregat, nama cabang/produk.

-- Alasan v_esb_movement_daily: v_esb_flow_daily hanya memakai baris is_fin = 1, padahal fact_esb_delivery juga memuat
-- kiriman non-FIN dari CK/gudang (bahan baku RMT/IDF, WIP, kemasan PAM, supplies SU*/UNI) dan fact_esb_production
-- memuat WIP. Toko membuat FIN sendiri (simple_mfg di toko) dari WIP/bahan baku, dan kiriman FIN > produksi per hari
-- bisa berasal dari stok CK sebelum periode (stok awal tidak ditarik: fact_esb_stock_snapshot kosong). View ini
-- menampilkan SEMUA pergerakan (produksi + kiriman) per kelompok barang agar pembaca bisa merekonsiliasi.
--
-- Grain: hari x brand x kind x cabang asal x cabang tujuan x produk x satuan. qty TIDAK pernah dijumlah lintas uom
-- (uom bagian dari kunci agregasi; production uom 'NO_RATIO' = qty masih batch).
--   kind        = 'production' (fact_esb_production, asal = cabang produksi, tujuan 0/'' ) | 'delivery' (fact_esb_delivery)
--   *_kind      = 'ck' (dim_esb_branch.is_ck = 1) | 'store' (is_store = 1) | 'non_sales' (lainnya / cabang tak dikenal)
--   item_group  = 'fin' (is_fin baris fakta) | 'wip' | 'raw' | 'packaging' | 'supplies' | 'other' (prefix kode lalu
--                 kategori ESB, urutan multiIf di bawah). 'N' pada catatan kategori Packaging = satuan, bukan prefix.
--   company_code= PT cabang ASAL (dim_esb_branch, fallback PT baris fakta = PT asal). Satu baris per pergerakan, tidak
--                 diduplikasi ke PT tujuan (jumlah tetap benar untuk user multi-PT); kiriman lintas PT tidak terlihat
--                 oleh user yang hanya ber-scope PT tujuan -- sisi terima tetap ada di v_esb_flow_daily (in_ck/in_other).
-- Hanya hari yang slice-nya selesai (filter checkpoint sama dengan v_esb_flow_daily).
CREATE OR REPLACE VIEW efn_dwh.v_esb_movement_daily AS
SELECT
    t.day AS day, t.brand AS brand,
    -- Fail-closed (SEC-IR-02): cabang asal tak dikenal di dim_esb_branch -> '' (row policy menolak), bukan PT default brand.
    CAST(ob.company_code, 'String') AS company_code,
    t.kind AS kind,
    t.origin_id AS origin_branch_id,
    if(ob.branch_name = '', concat('Cabang #', toString(t.origin_id)), ob.branch_name) AS origin_branch_name,
    multiIf(ob.is_ck = 1, 'ck', ob.is_store = 1, 'store', 'non_sales') AS origin_kind,
    t.dest_id AS dest_branch_id,
    if(t.kind = 'production', '',
       if(tb.branch_name = '', concat('Cabang #', toString(t.dest_id)), tb.branch_name)) AS dest_branch_name,
    if(t.kind = 'production', '', multiIf(tb.is_ck = 1, 'ck', tb.is_store = 1, 'store', 'non_sales')) AS dest_kind,
    multiIf(
        t.fin_s = 1, 'fin',
        startsWith(t.p_code, 'WIP') OR pr.category = 'Inventory - WIP', 'wip',
        startsWith(t.p_code, 'RMT') OR startsWith(t.p_code, 'IDF')
            OR pr.category IN ('Inventory - Food & Beverage', 'Import Ingredient'), 'raw',
        startsWith(t.p_code, 'PAM') OR pr.category = 'Packaging Material', 'packaging',
        startsWith(t.p_code, 'SU') OR startsWith(t.p_code, 'UNI') OR pr.category IN ('Supplies', 'Uniform'), 'supplies',
        'other') AS item_group,
    if(pr.category = '', 'Tanpa kategori', pr.category) AS category,
    t.p_code AS product_code,
    if(pr.product_name = '', t.p_code, pr.product_name) AS product_name,
    t.uom_r AS uom, t.qty_s AS qty, t.lines_s AS lines
FROM
(
    SELECT u.day AS day, u.brand AS brand, u.kind AS kind, u.origin_id AS origin_id, u.dest_id AS dest_id,
           u.p_code AS p_code, u.uom_r AS uom_r, any(u.c_code) AS c_code, max(u.is_fin) AS fin_s,
           sum(u.qty_r) AS qty_s, count() AS lines_s
    FROM
    (
        -- produksi: semua produk (FIN, WIP, dll.), simple_mfg + production_result
        SELECT x.prod_date AS day, x.brand AS brand, 'production' AS kind, x.branch_id AS origin_id,
               toUInt32(0) AS dest_id, x.company_code AS c_code, x.product_code AS p_code,
               CAST(x.uom, 'String') AS uom_r, x.is_fin AS is_fin, toFloat64(x.qty_ea) AS qty_r
        FROM
        (
            SELECT brand, company_code, branch_id, prod_date, product_code, uom, qty_ea, is_fin
            FROM efn_dwh.fact_esb_production FINAL
            WHERE (brand, prod_date, _version) IN
                  (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                   WHERE entity = 'production' AND scope_key = '*')
        ) AS x
        UNION ALL
        -- kiriman: semua produk, asal -> tujuan
        SELECT d.delivery_date AS day, d.brand AS brand, 'delivery' AS kind, d.origin_branch_id AS origin_id,
               d.dest_branch_id AS dest_id, d.company_code AS c_code, d.product_code AS p_code,
               CAST(d.uom, 'String') AS uom_r, d.is_fin AS is_fin, toFloat64(d.qty_ea) AS qty_r
        FROM
        (
            SELECT brand, company_code, delivery_date, origin_branch_id, dest_branch_id, product_code, uom, qty_ea,
                   is_fin
            FROM efn_dwh.fact_esb_delivery FINAL
            WHERE (brand, delivery_date, _version) IN
                  (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                   WHERE entity = 'delivery' AND scope_key = '*')
        ) AS d
    ) AS u
    GROUP BY day, brand, kind, origin_id, dest_id, p_code, uom_r
) AS t
LEFT JOIN (SELECT brand, branch_id, branch_name, company_code, is_ck, is_store FROM efn_dwh.dim_esb_branch FINAL) AS ob
    ON ob.brand = t.brand AND ob.branch_id = t.origin_id
LEFT JOIN (SELECT brand, branch_id, branch_name, is_ck, is_store FROM efn_dwh.dim_esb_branch FINAL) AS tb
    ON tb.brand = t.brand AND tb.branch_id = t.dest_id
LEFT JOIN (SELECT brand, product_code, product_name, category FROM efn_dwh.dim_esb_product FINAL) AS pr
    ON pr.brand = t.brand AND pr.product_code = t.p_code;

-- Cakupan data per brand x PT x entitas harian, dari checkpoint slice yang selesai (status 'done'). UI memakai ini
-- untuk menyatakan rentang yang benar-benar tersedia (mis. sales 365 hari, production/delivery hanya beberapa hari).
--   entitas per brand (scope_key '*': payment, production, delivery, transfer) -> diperluas ke setiap PT yang punya
--     cabang di brand itu (pola v_esb_data_quality_pt; metadata ekstraksi, bukan angka transaksi);
--   entitas per toko (scope_key = branch_id: sales, fin_sold) -> PT cabang itu (cabang tak dikenal = tidak dihitung).
--   days = jumlah hari unik dengan minimal satu slice selesai (per toko: cukup satu cabang PT itu); hari bolong di
--   tengah rentang terlihat dari days < dateDiff('day', first_day, last_day) + 1.
-- Brand tanpa cabang terdaftar -> tidak ada baris (fail closed).
CREATE OR REPLACE VIEW efn_dwh.v_esb_coverage AS
SELECT
    a.brand AS brand, CAST(a.c_code, 'String') AS company_code, CAST(a.entity, 'String') AS entity,
    a.first_d AS first_day, a.last_d AS last_day, a.days_d AS days
FROM
(
    SELECT u.brand AS brand, u.c_code AS c_code, u.entity AS entity, min(u.day) AS first_d, max(u.day) AS last_d,
           uniqExact(u.day) AS days_d
    FROM
    (
        SELECT k.brand AS brand, p.company_code AS c_code, k.entity AS entity, k.day AS day
        FROM (SELECT brand, entity, day FROM efn_esb.extract_checkpoint FINAL
              WHERE status = 'done' AND scope_key = '*'
                AND entity IN ('sales', 'payment', 'production', 'delivery', 'transfer', 'fin_sold')) AS k
        INNER JOIN (SELECT DISTINCT brand, company_code FROM efn_dwh.dim_esb_branch FINAL WHERE company_code != '') AS p
            ON p.brand = k.brand
        UNION ALL
        SELECT k.brand AS brand, s.company_code AS c_code, k.entity AS entity, k.day AS day
        FROM (SELECT brand, entity, scope_key, day FROM efn_esb.extract_checkpoint FINAL
              WHERE status = 'done' AND scope_key != '*'
                AND entity IN ('sales', 'payment', 'production', 'delivery', 'transfer', 'fin_sold')) AS k
        INNER JOIN (SELECT brand, toString(branch_id) AS scope_key, company_code FROM efn_dwh.dim_esb_branch FINAL
                    WHERE company_code != '') AS s
            ON s.brand = k.brand AND s.scope_key = k.scope_key
    ) AS u
    GROUP BY brand, c_code, entity
) AS a;
