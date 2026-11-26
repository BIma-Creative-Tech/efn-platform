-- Cadangan pembayaran OMS (26 Nov 2026, docs/04 §33.5a; ADR-040 amandemen §6c -- Proposed). Idempoten (CREATE OR
-- REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah 01..26 (butuh tabel G69 26-g69-esb-oms.sql).
--
-- Masalah: /report/sales-payment-summary ESB Core kadang HTTP 500 permanen untuk hari tertentu (PB 17, 18, 22, 25 Nov
-- 2026: server ESB timeout ±33 dtk; branchID diabaikan sehingga tidak bisa dipecah per cabang) -> halaman Pembayaran
-- Insight bolong pada hari itu.
-- Solusi: untuk (brand, hari) TANPA baris Core, pakai ringkasan OMS sales-head (struk final saja). Uji 21 Nov PB:
-- tipe, metode, jumlah transaksi dan nominal OMS = Core persis; selisih hanya baris OTHER COST (FOC/WASTE) yang tidak
-- ada di OMS. MDR tidak ada di OMS -> ESTIMASI = tarif rata-rata Core per (brand, metode) 60 hari terakhir (0 bila
-- metode belum pernah muncul di Core). Kolom `source` = 'core' | 'oms' supaya UI bisa menandai estimasi.
-- Begitu Core berhasil ditarik untuk hari itu, baris OMS hilang otomatis (Core menang). Hari berjalan (WIB) tidak
-- di-fallback (OMS intraday parsial; sebelumnya pun kosong sampai Core H+1).
--
-- Pembaca: HANYA efn_insight_definer (wrapper efn_insight.v_esb_payment_daily + v_esb_store_payment_daily).
-- efn_dwh.v_esb_payment_daily (Metabase, mig_rw rekonsiliasi) TIDAK berubah: sumber Core murni.
-- PDP/PCI: hanya nama tipe/metode bayar + jumlah; nomor kartu/rekening sudah dibuang sebelum mart (SEC-OMS-02).

CREATE OR REPLACE VIEW efn_dwh.v_esb_payment_daily_fb AS
SELECT
    c.sales_date AS sales_date, c.brand AS brand, c.company_code AS company_code, c.branch_id AS branch_id,
    c.branch_code AS branch_code, c.branch_name AS branch_name, c.payment_type AS payment_type,
    c.payment_method AS payment_method, c.payment_count AS payment_count, c.amount AS amount, c.mdr AS mdr,
    c.net AS net, 'core' AS source
FROM efn_dwh.v_esb_payment_daily AS c
UNION ALL
SELECT
    o.sales_date AS sales_date, o.brand AS brand, o.company_code AS company_code, o.branch_id AS branch_id,
    o.branch_code AS branch_code, if(b.branch_name = '', o.branch_code, b.branch_name) AS branch_name,
    o.payment_type AS payment_type, o.payment_method AS payment_method, o.payment_count AS payment_count,
    o.amount AS amount, o.mdr AS mdr, CAST(o.amount - o.mdr AS Decimal(18, 2)) AS net, 'oms' AS source
FROM
(
    SELECT a.sales_date AS sales_date, a.brand AS brand, a.company_code AS company_code, a.branch_id AS branch_id,
           a.branch_code AS branch_code, a.payment_type AS payment_type, a.payment_method AS payment_method,
           a.payment_count AS payment_count, a.amount AS amount,
           CAST(round(toFloat64(a.amount) * r.mdr_rate, 2) AS Decimal(18, 2)) AS mdr
    FROM
    (
        SELECT sales_date, brand, company_code, branch_id, branch_code,
               toString(payment_method_type_name) AS payment_type, payment_method_name AS payment_method,
               toUInt32(count()) AS payment_count, CAST(sum(payment_amount) AS Decimal(18, 2)) AS amount
        FROM efn_dwh.fact_esb_oms_bill_payment FINAL
        WHERE (brand, branch_code, sales_date, _version) IN
              (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'oms_sales')
          AND (brand, branch_code, sales_date, sales_num) IN
              (SELECT brand, branch_code, sales_date, sales_num FROM efn_dwh.fact_esb_oms_bill FINAL
               WHERE is_final = 1 AND (brand, branch_code, sales_date, _version) IN
                     (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                      WHERE entity = 'oms_sales'))
          AND sales_date < toDate(now('Asia/Jakarta'))  -- hari berjalan = OMS intraday parsial; Core baru H+1
          AND (brand, sales_date) NOT IN (SELECT DISTINCT brand, sales_date FROM efn_dwh.v_esb_payment_daily)
        GROUP BY sales_date, brand, company_code, branch_id, branch_code, payment_type, payment_method
    ) AS a
    LEFT JOIN
    (
        SELECT brand, payment_method, toFloat64(sum(mdr)) / toFloat64(sum(amount)) AS mdr_rate
        FROM efn_dwh.v_esb_payment_daily
        WHERE sales_date >= today() - 60
        GROUP BY brand, payment_method
        HAVING sum(amount) > 0
    ) AS r ON r.brand = a.brand AND r.payment_method = a.payment_method
) AS o
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = o.brand AND b.branch_id = o.branch_id;
