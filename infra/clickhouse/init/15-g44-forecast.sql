-- Gelombang 44 (efn-data-ai-engineer): forecast supermarket (ADR-046 Proposed, docs/04 §28.1/§28.2, K25-02, R-T21).
-- Idempoten (CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW / CREATE DATABASE IF NOT EXISTS). Dijalankan
-- clickhouse-init.sh setiap deploy setelah 01..13 (nomor 14 dipakai G43 di branch lain).
--
-- Arah data (ADR-046 §3.1, L-145): forecast-worker HANYA membaca ClickHouse (user efn_forecast_reader, database
-- efn_forecast = view DEFINER di access/forecast-access.sql di atas view efn_dwh.v_fc_* di bawah) dan HANYA menulis ke
-- Odoo (/json/2 lewat odoo-rpc-gateway). Hasil forecast turun ke ClickHouse lewat ETL (services/etl/efn_etl/forecast.py,
-- ETL_ENABLE_FORECAST=false default): efn.forecast.daily -> fact_forecast_daily, efn.forecast.run -> dim_forecast_run,
-- efn.forecast.calendar -> dim_calendar, stock.warehouse -> dim_fc_warehouse; ETL juga menghitung fact_forecast_accuracy.
--
-- PDP / minimisasi: tanpa partner, member, user pelaku, nama dokumen, lot. Hanya id teknis, kode PT/gudang, produk,
-- kategori, tanggal, qty, nilai statistik. Nama libur nasional bukan data pribadi.

CREATE DATABASE IF NOT EXISTS efn_forecast;

-- ============================================================== Dimensi
-- Gudang + kode (stock.warehouse.code, mis. SMDDC/SMD01..SMD05) + kode PT (res.company.efn_brand_code). Tabel sendiri
-- (bukan dim_warehouse G32): dim_warehouse membaca kolom efn_ck_production yang tidak ada di PT supermarket.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_fc_warehouse
(
    warehouse_id  UInt32,
    code          String,
    name          String,
    company_id    UInt32,
    company_code  LowCardinality(String),
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY warehouse_id;

-- Kalender forecast (Odoo efn.forecast.calendar, docs/04 §28.3): sumber kebenaran = Odoo; entri `lib` (seed library
-- holidays oleh worker) dan `manual` (user). warehouse_id 0 = berlaku semua gudang company. active = 0 -> entri
-- dinonaktifkan user (worker tidak memakainya, termasuk entri lib yang sama).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_calendar
(
    calendar_id   UInt64,
    company_id    UInt32,
    warehouse_id  UInt32,
    date          Date,
    kind          LowCardinality(String),           -- weekend|payday|holiday|cuti_bersama|ramadan|lebaran_window|custom
    name          String,
    source        LowCardinality(String),           -- lib|manual
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY calendar_id;

-- Run forecast (Odoo efn.forecast.run). run_uuid = run_id UUIDv4 dari worker (§28.2); run_id = id Odoo.
-- state: open|ok|failed (nilai Odoo; hanya `ok` dipakai untuk akurasi).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_forecast_run
(
    run_id         UInt64,
    run_uuid       String,
    company_id     UInt32,
    kind           LowCardinality(String),          -- daily|weekly_minmax
    model_version  LowCardinality(String),
    state          LowCardinality(String),
    generated_at   DateTime64(6, 'UTC'),
    write_date     DateTime64(6, 'UTC'),
    _loaded_at     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY run_id;

-- ============================================================== Fakta
-- Proyeksi harian (keluaran 4 ADR-046 §3.4): satu baris per run x gudang x produk x tanggal. Riwayat semua run disimpan
-- (akurasi memakai run `ok` terakhir yang dibuat SEBELUM tanggal itu, lead >= 1 hari). Tanpa PII -> TTL baris 3 tahun
-- (data operasional; cukup untuk evaluasi tahunan Lebaran, ADR-046 §7).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_forecast_daily
(
    line_id        UInt64,
    run_id         UInt64,
    company_id     UInt32,
    warehouse_id   UInt32,
    product_id     UInt32,
    forecast_date  Date,
    yhat           Decimal(18, 3),
    p10            Decimal(18, 3),
    p90            Decimal(18, 3),
    sigma          Decimal(18, 3),
    model          LowCardinality(String),          -- AutoETS|AutoARIMA|CrostonOptimized|ADIDA|category_mean
    write_date     DateTime64(6, 'UTC'),
    _loaded_at     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(forecast_date)
ORDER BY (company_id, warehouse_id, product_id, forecast_date, line_id)
TTL forecast_date + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Akurasi per SKU x gudang x minggu (Senin, WIB) vs penjualan aktual (ADR-046 §3.6). Dihitung ulang ETL untuk minggu
-- yang sudah lewat (versi computed_at -> idempoten). Jumlah mentah disimpan agar WAPE/MAPE/bias bisa diagregasi ulang
-- lintas SKU/gudang dengan benar (Σ|F−A| / ΣA, bukan rata-rata persen):
--   wape = abs_error / actual_qty;  bias = (forecast_qty − actual_qty) / actual_qty;  mape = ape_sum / days_with_sales
CREATE TABLE IF NOT EXISTS efn_dwh.fact_forecast_accuracy
(
    company_id       UInt32,
    warehouse_id     UInt32,
    product_id       UInt32,
    week_start       Date,
    days             UInt8,                          -- hari dengan forecast (lead >= 1) dalam minggu itu
    days_with_sales  UInt8,
    forecast_qty     Decimal(18, 3),
    actual_qty       Decimal(18, 3),
    abs_error        Decimal(18, 3),
    ape_sum          Float64,
    wape             Nullable(Float64),
    mape             Nullable(Float64),
    bias             Nullable(Float64),
    computed_at      DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(week_start)
ORDER BY (company_id, warehouse_id, product_id, week_start)
TTL week_start + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== View sumber forecast-worker (efn_dwh.v_fc_*)
-- Dibungkus view DEFINER efn_forecast.* (access/forecast-access.sql). Semua item SELECT teratas memakai AS.

-- Permintaan harian per SKU x gudang (POS; online menyusul saat fact_sales_line source='online' ada). qty = qty baris
-- produk non-refund (retur bukan permintaan negatif). Gudang = pos.config.warehouse_id (dim_outlet).
CREATE OR REPLACE VIEW efn_dwh.v_fc_sales_daily AS
SELECT
    s.day AS day, s.company_id AS company_id, s.company_code AS company_code, o.warehouse_id AS warehouse_id,
    w.code AS warehouse_code, s.product_id AS product_id, toFloat64(sum(s.qty)) AS qty
FROM
(
    SELECT toDate(order_date) AS day, company_id, brand AS company_code, store_id, product_id,
           sumIf(qty, line_kind = 'product' AND is_refund = 0) AS qty
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos'
    GROUP BY day, company_id, company_code, store_id, product_id
) AS s
INNER JOIN
(
    SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id
    FROM efn_dwh.dim_outlet FINAL
    WHERE isNotNull(warehouse_id)
) AS o ON o.store_id = s.store_id
INNER JOIN (SELECT warehouse_id, code FROM efn_dwh.dim_fc_warehouse FINAL WHERE code != '') AS w
    ON w.warehouse_id = o.warehouse_id
GROUP BY s.day, s.company_id, s.company_code, o.warehouse_id, w.code, s.product_id;

-- Stok akhir hari per SKU x gudang = snapshot selesai TERAKHIR per tanggal (pola v_stock_daily). Dipakai worker untuk
-- censoring OOS (on_hand <= 0 di akhir hari -> penjualan hari itu bukan permintaan nol, ADR-046 §3.3).
CREATE OR REPLACE VIEW efn_dwh.v_fc_stock_daily AS
SELECT
    s.snapshot_date AS day, s.company_id AS company_id, s.warehouse_id AS warehouse_id, s.product_id AS product_id,
    toFloat64(sum(s.quantity)) AS on_hand
FROM efn_dwh.fact_stock_snapshot AS s
WHERE s.warehouse_id != 0
  AND (s.snapshot_date, s.snapshot_ts) IN
    (SELECT snapshot_date, max(snapshot_ts) FROM efn_dwh.stock_snapshot_run GROUP BY snapshot_date)
GROUP BY s.snapshot_date, s.company_id, s.warehouse_id, s.product_id;

-- Waste (scrap selesai) per hari x gudang x produk + kategori (menurunkan service level min/max, ADR-046 §3.4).
-- Sumber fact_scrap = job G32 (ETL_ENABLE_WASTE); kosong bila job itu mati -> worker tidak menurunkan service level.
CREATE OR REPLACE VIEW efn_dwh.v_fc_scrap_daily AS
SELECT
    toDate(s.date_done) AS day, s.company_id AS company_id, s.warehouse_id AS warehouse_id,
    s.product_id AS product_id, ifNull(p.category, '') AS category, toFloat64(sum(s.qty)) AS qty
FROM
(
    SELECT date_done, company_id, warehouse_id, product_id, qty FROM efn_dwh.fact_scrap FINAL WHERE is_done = 1
) AS s
LEFT JOIN (SELECT product_id, category FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = s.product_id
GROUP BY toDate(s.date_done), s.company_id, s.warehouse_id, s.product_id, ifNull(p.category, '');

-- Kalender (entri lib + manual dari Odoo).
CREATE OR REPLACE VIEW efn_dwh.v_fc_calendar AS
SELECT calendar_id, company_id, warehouse_id, date, kind, name, source, active
FROM efn_dwh.dim_calendar FINAL;

-- Master produk TANPA nama/partner (kategori = complete_name Odoo; dipakai untuk category_mean & waste per kategori).
CREATE OR REPLACE VIEW efn_dwh.v_fc_product AS
SELECT product_id, template_id, category, company_id, active, sale_ok, is_storable, use_expiration_date
FROM efn_dwh.dim_product FINAL;

-- Gudang per PT (kode = warehouse_code kontrak §28.2).
CREATE OR REPLACE VIEW efn_dwh.v_fc_warehouse AS
SELECT warehouse_id, code, company_id, company_code, active
FROM efn_dwh.dim_fc_warehouse FINAL;

-- ============================================================== View dashboard (Metabase/Insight G47)
-- Akurasi mingguan + kode gudang + kategori (tanpa nama produk: Insight menggabungkan sendiri bila perlu).
CREATE OR REPLACE VIEW efn_dwh.v_forecast_accuracy_weekly AS
SELECT
    a.week_start AS week_start, a.company_id AS company_id, w.company_code AS company_code,
    a.warehouse_id AS warehouse_id, w.code AS warehouse_code, a.product_id AS product_id,
    ifNull(p.category, '') AS category, a.days AS days, a.days_with_sales AS days_with_sales,
    a.forecast_qty AS forecast_qty, a.actual_qty AS actual_qty, a.abs_error AS abs_error, a.ape_sum AS ape_sum,
    a.wape AS wape, a.mape AS mape, a.bias AS bias
FROM
(
    SELECT week_start, company_id, warehouse_id, product_id, days, days_with_sales, forecast_qty, actual_qty, abs_error,
           ape_sum, wape, mape, bias
    FROM efn_dwh.fact_forecast_accuracy FINAL
) AS a
LEFT JOIN (SELECT warehouse_id, code, company_code FROM efn_dwh.dim_fc_warehouse FINAL) AS w
    ON w.warehouse_id = a.warehouse_id
LEFT JOIN (SELECT product_id, category FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = a.product_id;
