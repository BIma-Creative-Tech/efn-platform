-- Gelombang 51 (efn-data-ai-engineer): view semantik tambahan EFN Insight supermarket untuk SMD di halaman F&B
-- (docs/design/G51_SMD_E2E_LEAD_SPEC.md §B1/§B2/§B3, docs/04 §28.14, addendum ADR-041 Proposed). Idempoten (CREATE OR
-- REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah 01..17 (butuh tabel 04-fase1, 15-g44, 17-g47).
--
-- Arah data tetap ADR-003: Odoo replica -> ETL (pos_payments, sales_fact_pos, products, fc_runs, fc_daily) -> tabel
-- efn_dwh -> view efn_dwh.v_sm_* di bawah (INVOKER, FINAL di subquery) -> view efn_insight.v_sm_* DEFINER
-- (access/insight-sm-access.sql) + row policy efn_insight_scope kelas W (access/insight-sm-policy.sh).
-- Hanya company/warehouse yang ada di dim_sm_warehouse (inner join) -> data brand F&B/ESB tidak pernah masuk (L-196).
-- PDP: tanpa partner, member, kasir, data kartu (fact_pos_payment memang tidak memuatnya). Uang = Float64 (kontrak §B2).
-- Semua item SELECT teratas memakai AS (diuji tests/test_insight_sm_access.py). Agregat memakai alias berbeda dari kolom
-- yang dirujuk (pelajaran ILLEGAL_AGGREGATION 21 Nov 2026), lalu diganti nama di SELECT luar.

-- B6 (keputusan lead G51): penanda baris rekonstruksi stok historis (efn_etl/sm_stock_hist.py). Baris rekonstruksi
-- ditulis dengan computed_at = 2000-01-01 UTC (lebih tua dari baris nyata now64) -> baris snapshot nyata SELALU menang
-- di ReplacingMergeTree(computed_at). Baris nyata (SQL_STOCK_AGG) = default 0.
ALTER TABLE efn_dwh.agg_sm_stock_daily ADD COLUMN IF NOT EXISTS is_reconstructed UInt8 DEFAULT 0;

-- Pembayaran POS per hari (WIB) x warehouse SM x metode. amount = Σ amount termasuk baris kembalian (negatif) = uang
-- bersih diterima per metode; payments = jumlah baris pembayaran non-kembalian. is_refund = baris dari order refund.
-- Gudang = pos.config.warehouse_id (dim_outlet), sama dengan v_sm_outlet_daily.
CREATE OR REPLACE VIEW efn_dwh.v_sm_payment_daily AS
SELECT
    t.day AS day,
    t.company_id AS company_id,
    t.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    t.method_name AS method_name,
    t.method_type AS method_type,
    t.is_refund AS is_refund,
    t.amt AS amount,
    t.n AS payments
FROM
(
    SELECT
        toDate(p.payment_date) AS day, p.company_id AS company_id, o.warehouse_id AS warehouse_id,
        toString(p.method_name) AS method_name, toString(p.method_type) AS method_type, p.is_refund AS is_refund,
        toFloat64(sum(p.amount)) AS amt, toUInt64(countIf(p.is_change = 0)) AS n
    FROM
    (
        SELECT company_id, store_id, payment_date, amount, method_name, method_type, is_change, is_refund
        FROM efn_dwh.fact_pos_payment FINAL
        WHERE company_id IN (SELECT company_id FROM efn_dwh.dim_sm_warehouse FINAL)
    ) AS p
    INNER JOIN
    (
        SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id FROM efn_dwh.dim_outlet FINAL WHERE isNotNull(warehouse_id)
    ) AS o ON o.store_id = p.store_id
    GROUP BY day, company_id, warehouse_id, method_name, method_type, is_refund
) AS t
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = t.warehouse_id AND w.company_id = t.company_id;

-- Penjualan POS per hari x warehouse SM x produk (sumber = v_sm_sales_product_daily, fact_sales_line job
-- sales_fact_pos). net_sales tanpa PPN, net_sales_incl_tax termasuk PPN (L-202; halaman F&B memakai incl. PPN, §B1).
-- Dijumlahkan ulang per warehouse (satu outlet bisa punya > 1 pos.config).
CREATE OR REPLACE VIEW efn_dwh.v_sm_product_sales_daily AS
SELECT
    s.day AS day,
    s.company_id AS company_id,
    s.warehouse_id AS warehouse_id,
    s.outlet_type AS outlet_type,
    toUInt32(s.product_id) AS product_id,
    toUInt32(ifNull(s.categ_id, 0)) AS categ_id,
    toFloat64(s.q) AS qty_sold,
    toFloat64(s.ns) AS net_sales,
    toFloat64(s.nsi) AS net_sales_incl_tax
FROM
(
    SELECT a.day AS day, a.company_id AS company_id, a.warehouse_id AS warehouse_id, a.outlet_type AS outlet_type,
           a.product_id AS product_id, a.categ_id AS categ_id, sum(a.qty_sold) AS q, sum(a.net_sales) AS ns,
           sum(a.net_sales_incl_tax) AS nsi
    FROM efn_dwh.v_sm_sales_product_daily AS a
    GROUP BY a.day, a.company_id, a.warehouse_id, a.outlet_type, a.product_id, a.categ_id
) AS s;

-- Proyeksi harian run forecast `ok` (kind daily) TERBARU per PT menurut generated_at (bukan run_id: run as-of A6 yang
-- diimpor belakangan punya id lebih besar tetapi generated_at lampau -> tidak pernah menjadi "terbaru"). Termasuk
-- baris DC (agregasi outlet di worker). Horizon penuh run (default 28 hari); aplikasi memfilter 14 hari ke depan.
CREATE OR REPLACE VIEW efn_dwh.v_sm_forecast_daily AS
SELECT
    f.company_id AS company_id,
    f.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    f.product_id AS product_id,
    toUInt32(ifNull(p.categ_id, 0)) AS categ_id,
    f.forecast_date AS forecast_date,
    toFloat64(f.yhat) AS yhat,
    toFloat64(f.p10) AS p10,
    toFloat64(f.p90) AS p90,
    toUInt32(f.run_id) AS run_id,
    toDateTime(r.latest_at, 'UTC') AS generated_at
FROM
(
    SELECT run_id, company_id, warehouse_id, product_id, forecast_date, yhat, p10, p90
    FROM efn_dwh.fact_forecast_daily FINAL
    WHERE run_id IN
    (
        SELECT argMax(run_id, generated_at) FROM efn_dwh.dim_forecast_run FINAL
        WHERE state = 'ok' AND kind = 'daily'
        GROUP BY company_id
    )
) AS f
INNER JOIN
(
    SELECT company_id, argMax(run_id, generated_at) AS latest_run, max(generated_at) AS latest_at
    FROM efn_dwh.dim_forecast_run FINAL
    WHERE state = 'ok' AND kind = 'daily'
    GROUP BY company_id
) AS r ON r.company_id = f.company_id AND r.latest_run = f.run_id
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = f.warehouse_id AND w.company_id = f.company_id
LEFT JOIN (SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = f.product_id;

-- Semua lot berstok (bukan hanya yang ber-alert near-expiry) dari snapshot stok selesai TERAKHIR (stock_snapshot_run):
-- quant lokasi internal (snapshot hanya memuat usage='internal') warehouse SM, quantity > 0, lot ber-expiration_date.
-- expiration_date = tanggal WIB; days_left dihitung terhadap hari ini WIB (negatif = lewat). value_cost = Σ qty x HPP
-- satuan quant (standard_price PT saat snapshot; NULL -> 0). Tabel "Umur simpan & sisa umur lot" (§B4).
CREATE OR REPLACE VIEW efn_dwh.v_sm_lot_stock AS
SELECT
    l.company_id AS company_id,
    l.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    l.product_id AS product_id,
    toUInt32(ifNull(p.categ_id, 0)) AS categ_id,
    toUInt32(l.lot_id) AS lot_id,
    l.lname AS lot_name,
    l.exp_day AS expiration_date,
    toInt32(dateDiff('day', toDate(now(), 'Asia/Jakarta'), l.exp_day)) AS days_left,
    l.qty AS quantity,
    l.val AS value_cost
FROM
(
    SELECT
        s.company_id AS company_id, s.warehouse_id AS warehouse_id, s.product_id AS product_id, s.lot_id AS lot_id,
        any(s.lot_name) AS lname, toDate(assumeNotNull(s.expiration_date)) AS exp_day,
        toFloat64(sum(s.quantity)) AS qty,
        sum(toFloat64(s.quantity) * toFloat64(ifNull(s.unit_cost, toDecimal64(0, 4)))) AS val
    FROM efn_dwh.fact_stock_snapshot AS s
    WHERE s.snapshot_date = (SELECT max(snapshot_date) FROM efn_dwh.stock_snapshot_run)
      AND s.snapshot_ts = (SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run)
      AND s.warehouse_id != 0 AND s.lot_id != 0 AND isNotNull(s.expiration_date) AND s.quantity > 0
      AND s.warehouse_id IN (SELECT warehouse_id FROM efn_dwh.dim_sm_warehouse FINAL)
    GROUP BY s.company_id, s.warehouse_id, s.product_id, s.lot_id, exp_day
) AS l
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = l.warehouse_id AND w.company_id = l.company_id
LEFT JOIN (SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = l.product_id;
