-- Gelombang 13 (efn-data-ai-engineer): mart Fase 1 (docs/01 §7 "ETL dasar + 3 dashboard Metabase").
-- Idempoten (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap
-- deploy SETELAH 01..03. Semua view = semantic layer Metabase (dashboard tidak membaca tabel mentah).
-- Zona waktu bisnis: Asia/Jakarta (WIB) seperti mart lain; outlet WITA/WIT belum dibedakan (lihat README ETL).

-- ============================================================== (a) Penjualan POS
-- fact_sales_line (01-schema.sql) dilengkapi. Baris lama berisi default sampai dimuat ulang (README: backfill).
ALTER TABLE efn_dwh.fact_sales_line ADD COLUMN IF NOT EXISTS order_id UInt64 DEFAULT 0;
ALTER TABLE efn_dwh.fact_sales_line ADD COLUMN IF NOT EXISTS is_refund UInt8 DEFAULT 0;
-- product | discount (produk diskon global POS) | reward (loyalty Odoo) | points (produk "Poin EFN", docs/04 §9.3) | tip
ALTER TABLE efn_dwh.fact_sales_line ADD COLUMN IF NOT EXISTS line_kind LowCardinality(String) DEFAULT 'product';
-- Nilai diskon persen baris, basis sama dengan subtotal (tanpa pajak). Diskon/poin berbentuk baris = subtotal negatif.
ALTER TABLE efn_dwh.fact_sales_line ADD COLUMN IF NOT EXISTS discount_amount Decimal(14, 2) DEFAULT 0;

-- Pembayaran POS (pos.payment). Tanpa data kartu/nomor referensi. Baris kembalian (is_change) bernilai negatif.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_pos_payment
(
    payment_id         UInt64,
    order_id           UInt64,
    company_id         UInt32,
    company_name       LowCardinality(String),
    store_id           UInt32,
    payment_date       DateTime('Asia/Jakarta'),
    amount             Decimal(14, 2),
    payment_method_id  UInt32,
    method_name        LowCardinality(String),
    method_type        LowCardinality(String),
    is_cash            UInt8,
    is_change          UInt8,
    is_refund          UInt8,
    write_date         DateTime64(6, 'UTC'),
    _loaded_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(payment_date)
ORDER BY (company_id, store_id, payment_date, payment_id);

-- Outlet = pos.config (nama untuk filter dashboard; warehouse untuk menghubungkan stok outlet).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_outlet
(
    store_id      UInt32,
    name          String,
    company_id    UInt32,
    warehouse_id  Nullable(UInt32),
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY store_id;

-- Produk (varian). list_price = harga jual template (dasar indeks Price Hunter). company_id NULL = lintas company.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_product
(
    product_id           UInt32,
    template_id          UInt32,
    default_code         String,
    name                 String,
    category             LowCardinality(String),
    company_id           Nullable(UInt32),
    list_price           Decimal(14, 2),
    product_type         LowCardinality(String),
    is_storable          UInt8,
    tracking             LowCardinality(String),
    use_expiration_date  UInt8,
    sale_ok              UInt8,
    available_in_pos     UInt8,
    active               UInt8,
    version_ts           DateTime64(6, 'UTC'),
    _loaded_at           DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version_ts)
ORDER BY product_id;

-- ============================================================== (b) Stok & kedaluwarsa (snapshot, efn_etl/stock.py)
CREATE TABLE IF NOT EXISTS efn_dwh.fact_stock_snapshot
(
    snapshot_date      Date,
    snapshot_ts        DateTime64(6, 'UTC'),
    quant_id           UInt64,
    company_id         UInt32,
    company_name       LowCardinality(String),
    warehouse_id       UInt32,
    warehouse_name     LowCardinality(String),
    location_id        UInt32,
    location_name      LowCardinality(String),
    product_id         UInt32,
    lot_id             UInt64,
    lot_name           String,
    expiration_date    Nullable(DateTime('Asia/Jakarta')),
    quantity           Decimal(18, 4),
    reserved_quantity  Decimal(18, 4),
    unit_cost          Nullable(Decimal(18, 4)),
    in_date            Nullable(DateTime('Asia/Jakarta')),
    _loaded_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(snapshot_ts)
PARTITION BY toYYYYMM(snapshot_date)
ORDER BY (snapshot_date, company_id, warehouse_id, product_id, quant_id);

CREATE TABLE IF NOT EXISTS efn_dwh.fact_orderpoint_snapshot
(
    snapshot_date   Date,
    snapshot_ts     DateTime64(6, 'UTC'),
    orderpoint_id   UInt64,
    company_id      UInt32,
    company_name    LowCardinality(String),
    warehouse_id    UInt32,
    warehouse_name  LowCardinality(String),
    location_id     UInt32,
    product_id      UInt32,
    min_qty         Decimal(18, 4),
    max_qty         Decimal(18, 4),
    trigger         LowCardinality(String),
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(snapshot_ts)
PARTITION BY toYYYYMM(snapshot_date)
ORDER BY (snapshot_date, company_id, warehouse_id, product_id, orderpoint_id);

-- Penanda snapshot selesai (ditulis TERAKHIR). View hanya membaca snapshot_ts yang ada di sini.
CREATE TABLE IF NOT EXISTS efn_dwh.stock_snapshot_run
(
    snapshot_date  Date,
    snapshot_ts    DateTime64(6, 'UTC'),
    quants         UInt64,
    orderpoints    UInt64,
    _loaded_at     DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(snapshot_date)
ORDER BY snapshot_ts;

-- ============================================================== Heartbeat ETL (SEC-52 butir 1)
-- Satu baris per job per siklus (status ok|error|partial). Alert dead-man memakai log EFN_ETL_HEARTBEAT (Loki);
-- tabel ini untuk kartu kesegaran data di Metabase. Retensi 90 hari (operasional, tanpa data pribadi).
CREATE TABLE IF NOT EXISTS efn_dwh.etl_job_run
(
    job          LowCardinality(String),
    run_ts       DateTime64(6, 'UTC'),
    status       LowCardinality(String),
    rows         UInt64,
    pages        UInt32,
    duration_ms  UInt64,
    watermark    Nullable(DateTime64(6, 'UTC')),
    _loaded_at   DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(run_ts)
ORDER BY (job, run_ts)
TTL toDateTime(run_ts) + INTERVAL 90 DAY
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== View semantic layer (Metabase)
-- View biasa = SQL SECURITY INVOKER: kolom tabel dasar juga di-grant ke metabase_reader (access/metabase-access.sql).

-- Penjualan POS per hari x PT x outlet x kategori. Semua nilai uang tanpa pajak (basis price_subtotal Odoo).
--   gross_sales   : penjualan produk sebelum diskon persen baris (bukan retur)
--   line_discount : diskon persen di baris produk
--   promo_discount: baris produk diskon global POS + reward loyalty Odoo (nilai positif)
--   points_redeemed_idr: baris "Poin EFN" (nilai positif)
--   refunds       : uang retur/refund (positif), dihitung dari order is_refund / baris refunded_orderline_id
--                   = refund_goods - points_returned_idr
--   refund_goods  : nilai barang yang diretur (positif), tanpa baris penyesuaian poin (G14, QA G13.5)
--   points_returned_idr: nilai baris "Poin EFN" pada order refund (positif) = bagian refund yang dikembalikan sebagai
--                   poin, bukan uang (restore poin BFF §9.4)
--   net_sales     : SUM(subtotal) semua baris = gross - diskon - promo - poin - refund
CREATE OR REPLACE VIEW efn_dwh.v_pos_sales_daily AS
SELECT
    s.day AS day, s.company_id AS company_id, s.company_name AS company_name, s.brand AS brand,
    s.store_id AS store_id, if(o.name = '', concat('POS #', toString(s.store_id)), o.name) AS store_name,
    s.category AS category,
    s.gross_sales AS gross_sales, s.line_discount AS line_discount, s.promo_discount AS promo_discount,
    s.points_redeemed_idr AS points_redeemed_idr, s.refunds AS refunds, s.net_sales AS net_sales,
    s.tax AS tax, s.cogs AS cogs, s.qty_sold AS qty_sold, s.qty_returned AS qty_returned, s.lines AS lines,
    s.refund_goods AS refund_goods, s.points_returned_idr AS points_returned_idr
FROM
(
    SELECT
        toDate(order_date) AS day, company_id, company_name, brand, store_id, category,
        sumIf(subtotal + discount_amount, line_kind = 'product' AND is_refund = 0)   AS gross_sales,
        sumIf(discount_amount, line_kind = 'product' AND is_refund = 0)               AS line_discount,
        -sumIf(subtotal, line_kind IN ('discount', 'reward') AND is_refund = 0)       AS promo_discount,
        -sumIf(subtotal, line_kind = 'points' AND is_refund = 0)                      AS points_redeemed_idr,
        -sumIf(subtotal, is_refund = 1)                                               AS refunds,
        -sumIf(subtotal, is_refund = 1 AND line_kind != 'points')                     AS refund_goods,
        sumIf(subtotal, is_refund = 1 AND line_kind = 'points')                       AS points_returned_idr,
        sum(subtotal)                                                                 AS net_sales,
        sum(tax_amount)                                                               AS tax,
        sumIf(cost_total, line_kind = 'product')                                      AS cogs,
        sumIf(qty, line_kind = 'product' AND is_refund = 0)                           AS qty_sold,
        -sumIf(qty, line_kind = 'product' AND is_refund = 1)                          AS qty_returned,
        count()                                                                       AS lines
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos'
    GROUP BY day, company_id, company_name, brand, store_id, category
) AS s
LEFT JOIN (SELECT store_id, name FROM efn_dwh.dim_outlet FINAL) AS o ON o.store_id = s.store_id;

-- Ringkasan outlet per hari (grain tanpa kategori agar jumlah order tidak terhitung ganda).
CREATE OR REPLACE VIEW efn_dwh.v_pos_outlet_daily AS
SELECT
    s.day AS day, s.company_id AS company_id, s.company_name AS company_name, s.brand AS brand,
    s.store_id AS store_id, if(o.name = '', concat('POS #', toString(s.store_id)), o.name) AS store_name,
    s.orders AS orders, s.refund_orders AS refund_orders, s.net_sales AS net_sales, s.gross_sales AS gross_sales,
    s.discounts AS discounts, s.points_redeemed_idr AS points_redeemed_idr, s.refunds AS refunds,
    if(s.orders = 0, NULL, round(s.sales_excl_refund / s.orders, 0)) AS avg_basket,
    s.refund_goods AS refund_goods, s.points_returned_idr AS points_returned_idr
FROM
(
    SELECT
        toDate(order_date) AS day, company_id, company_name, brand, store_id,
        uniqExactIf(order_id, is_refund = 0)                                         AS orders,
        uniqExactIf(order_id, is_refund = 1)                                         AS refund_orders,
        sum(subtotal)                                                                AS net_sales,
        sumIf(subtotal, is_refund = 0)                                               AS sales_excl_refund,
        sumIf(subtotal + discount_amount, line_kind = 'product' AND is_refund = 0)  AS gross_sales,
        sumIf(discount_amount, line_kind = 'product' AND is_refund = 0)
            - sumIf(subtotal, line_kind IN ('discount', 'reward') AND is_refund = 0) AS discounts,
        -sumIf(subtotal, line_kind = 'points' AND is_refund = 0)                     AS points_redeemed_idr,
        -sumIf(subtotal, is_refund = 1)                                              AS refunds,
        -sumIf(subtotal, is_refund = 1 AND line_kind != 'points')                    AS refund_goods,
        sumIf(subtotal, is_refund = 1 AND line_kind = 'points')                      AS points_returned_idr
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos'
    GROUP BY day, company_id, company_name, brand, store_id
) AS s
LEFT JOIN (SELECT store_id, name FROM efn_dwh.dim_outlet FINAL) AS o ON o.store_id = s.store_id;

-- Pola jam (WIB) per outlet: order & penjualan bersih.
CREATE OR REPLACE VIEW efn_dwh.v_pos_sales_hourly AS
SELECT
    s.day AS day, s.hour AS hour, s.weekday AS weekday, s.company_id AS company_id, s.company_name AS company_name,
    s.store_id AS store_id, if(o.name = '', concat('POS #', toString(s.store_id)), o.name) AS store_name,
    s.orders AS orders, s.net_sales AS net_sales
FROM
(
    SELECT
        toDate(order_date) AS day, toHour(order_date) AS hour, toDayOfWeek(order_date) AS weekday,
        company_id, company_name, store_id,
        uniqExactIf(order_id, is_refund = 0) AS orders,
        sum(subtotal)                        AS net_sales
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos'
    GROUP BY day, hour, weekday, company_id, company_name, store_id
) AS s
LEFT JOIN (SELECT store_id, name FROM efn_dwh.dim_outlet FINAL) AS o ON o.store_id = s.store_id;

-- Metode bayar per hari x outlet. amount = nilai bersih (pembayaran - kembalian); refund = pembayaran keluar.
CREATE OR REPLACE VIEW efn_dwh.v_pos_payment_daily AS
SELECT
    s.day AS day, s.company_id AS company_id, s.company_name AS company_name,
    s.store_id AS store_id, if(o.name = '', concat('POS #', toString(s.store_id)), o.name) AS store_name,
    s.method_name AS method_name, s.method_type AS method_type, s.is_cash AS is_cash,
    s.net_amount AS amount, s.payments AS payments, s.refund_amount AS refund_amount
FROM
(
    SELECT
        toDate(payment_date) AS day, company_id, company_name, store_id, method_name, method_type, is_cash,
        sum(amount)                                  AS net_amount,
        countIf(is_change = 0 AND is_refund = 0)     AS payments,
        -sumIf(amount, is_refund = 1)                AS refund_amount
    FROM efn_dwh.fact_pos_payment FINAL
    GROUP BY day, company_id, company_name, store_id, method_name, method_type, is_cash
) AS s
LEFT JOIN (SELECT store_id, name FROM efn_dwh.dim_outlet FINAL) AS o ON o.store_id = s.store_id;

-- Stok harian = snapshot selesai TERAKHIR per tanggal. stock_value = qty x standard_price company (bila ada).
CREATE OR REPLACE VIEW efn_dwh.v_stock_daily AS
SELECT
    s.snapshot_date AS day, s.snapshot_ts AS snapshot_ts, s.company_id AS company_id, s.company_name AS company_name,
    s.warehouse_id AS warehouse_id, s.warehouse_name AS warehouse_name, s.location_id AS location_id,
    s.location_name AS location_name, s.product_id AS product_id, p.name AS product_name,
    p.default_code AS default_code, p.category AS category, s.lot_id AS lot_id, s.lot_name AS lot_name,
    s.expiration_date AS expiration_date, s.quantity AS quantity, s.reserved_quantity AS reserved_quantity,
    s.quantity - s.reserved_quantity AS free_quantity,
    toDecimal128(toDecimal128(s.quantity, 4) * toDecimal128(ifNull(s.unit_cost, toDecimal64(0, 4)), 4), 2) AS stock_value
FROM efn_dwh.fact_stock_snapshot AS s
LEFT JOIN (SELECT product_id, name, default_code, category FROM efn_dwh.dim_product FINAL) AS p
    ON p.product_id = s.product_id
WHERE (s.snapshot_date, s.snapshot_ts) IN
    (SELECT snapshot_date, max(snapshot_ts) FROM efn_dwh.stock_snapshot_run GROUP BY snapshot_date);

-- Stok saat ini = snapshot selesai terakhir.
CREATE OR REPLACE VIEW efn_dwh.v_stock_current AS
SELECT *
FROM efn_dwh.v_stock_daily
WHERE snapshot_ts = (SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run);

-- Lot yang kedaluwarsa dalam <= 7 hari (termasuk yang sudah lewat: days_to_expiry < 0), stok > 0.
CREATE OR REPLACE VIEW efn_dwh.v_stock_expiring AS
SELECT
    day, company_id, company_name, warehouse_id, warehouse_name, location_name, product_id, product_name,
    default_code, category, lot_name, expiration_date,
    dateDiff('day', toDate(now(), 'Asia/Jakarta'), toDate(expiration_date)) AS days_to_expiry,
    if(dateDiff('day', toDate(now(), 'Asia/Jakarta'), toDate(expiration_date)) < 0, 'kedaluwarsa', 'segera') AS expiry_status,
    quantity, stock_value
FROM efn_dwh.v_stock_current
WHERE expiration_date IS NOT NULL
  AND quantity > 0
  AND dateDiff('day', toDate(now(), 'Asia/Jakarta'), toDate(expiration_date)) <= 7;

-- Stok di bawah titik pesan ulang. Odoo membandingkan stok PERKIRAAN (forecast) dengan min di lokasi orderpoint
-- (child_of); di sini pendekatan: stok on-hand per gudang x produk (snapshot terakhir) <= product_min_qty.
CREATE OR REPLACE VIEW efn_dwh.v_stock_below_reorder AS
SELECT
    op.snapshot_date AS day, op.company_id AS company_id, op.company_name AS company_name,
    op.warehouse_id AS warehouse_id, op.warehouse_name AS warehouse_name, op.product_id AS product_id,
    p.name AS product_name, p.default_code AS default_code, p.category AS category,
    op.min_qty AS min_qty, op.max_qty AS max_qty, op.trigger AS trigger,
    ifNull(q.on_hand, toDecimal128(0, 4)) AS on_hand, ifNull(q.free_qty, toDecimal128(0, 4)) AS free_qty,
    greatest(op.max_qty - ifNull(q.on_hand, toDecimal128(0, 4)), toDecimal128(0, 4)) AS suggested_qty
FROM
(
    SELECT * FROM efn_dwh.fact_orderpoint_snapshot
    WHERE snapshot_ts = (SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run)
) AS op
LEFT JOIN
(
    SELECT warehouse_id, product_id, sum(quantity) AS on_hand, sum(quantity - reserved_quantity) AS free_qty
    FROM efn_dwh.fact_stock_snapshot
    WHERE snapshot_ts = (SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run)
    GROUP BY warehouse_id, product_id
) AS q ON q.warehouse_id = op.warehouse_id AND q.product_id = op.product_id
LEFT JOIN (SELECT product_id, name, default_code, category FROM efn_dwh.dim_product FINAL) AS p
    ON p.product_id = op.product_id
WHERE ifNull(q.on_hand, toDecimal128(0, 4)) <= op.min_qty;

-- ============================================================== (c) Price Hunter: indeks harga
-- Laporan valid dicocokkan ke produk EFN: (1) product_sku = default_code (pemetaan SKU, belum diisi OCR), atau
-- (2) nama dinormalisasi (lowercase, spasi dirapatkan) sama persis dengan nama produk. Tidak termatch = tidak tampil
-- (lihat v_price_hunter_match_rate). price_index = harga pesaing / harga EFN x 100 (< 100 = pesaing lebih murah).
CREATE OR REPLACE VIEW efn_dwh.v_price_index AS
SELECT
    c.report_id AS report_id, c.reported_at AS reported_at, toDate(c.reported_at) AS day,
    c.competitor_name AS competitor_name, c.product_name AS reported_product_name,
    p.product_id AS product_id, p.name AS efn_product_name, p.default_code AS default_code, p.category AS category,
    p.company_id AS company_id, p.match_method AS match_method,
    c.price AS competitor_price, p.list_price AS efn_list_price, pos.avg_net_price_30d AS efn_avg_pos_price_30d,
    if(p.list_price = 0, NULL, round(toFloat64(c.price) / toFloat64(p.list_price) * 100, 1)) AS price_index,
    if(pos.avg_net_price_30d IS NULL OR pos.avg_net_price_30d = 0, NULL,
       round(toFloat64(c.price) / pos.avg_net_price_30d * 100, 1)) AS price_index_pos
FROM
(
    SELECT report_id, reported_at, competitor_name, product_name, price,
           if(ifNull(product_sku, '') != '', concat('sku:', product_sku),
              concat('name:', lower(trimBoth(replaceRegexpAll(product_name, '\\s+', ' '))))) AS match_key
    FROM efn_dwh.competitor_prices FINAL
    WHERE status = 'valid'
) AS c
INNER JOIN
(
    SELECT product_id, name, default_code, category, company_id, list_price,
           arrayJoin(arrayFilter(k -> k NOT IN ('sku:', 'name:'),
               [concat('sku:', default_code), concat('name:', lower(trimBoth(replaceRegexpAll(name, '\\s+', ' '))))]))
               AS match_key,
           if(startsWith(match_key, 'sku:'), 'sku', 'name') AS match_method
    FROM efn_dwh.dim_product FINAL
    WHERE active = 1 AND sale_ok = 1
) AS p ON p.match_key = c.match_key
LEFT JOIN
(
    SELECT product_id, toFloat64(sum(subtotal)) / nullIf(toFloat64(sum(qty)), 0) AS avg_net_price_30d
    FROM efn_dwh.fact_sales_line FINAL
    WHERE line_kind = 'product' AND is_refund = 0 AND order_date >= now() - INTERVAL 30 DAY
    GROUP BY product_id
) AS pos ON pos.product_id = p.product_id;

-- Tingkat pencocokan laporan valid per hari (berapa yang bisa dipakai untuk indeks).
CREATE OR REPLACE VIEW efn_dwh.v_price_hunter_match_rate AS
SELECT
    v.day AS day, v.valid_reports AS valid_reports, ifNull(m.matched_reports, 0) AS matched_reports,
    if(v.valid_reports = 0, NULL, round(ifNull(m.matched_reports, 0) / v.valid_reports * 100, 1)) AS match_pct
FROM
(
    SELECT toDate(reported_at) AS day, count() AS valid_reports
    FROM efn_dwh.competitor_prices FINAL WHERE status = 'valid' GROUP BY day
) AS v
LEFT JOIN
(
    SELECT day, uniqExact(report_id) AS matched_reports FROM efn_dwh.v_price_index GROUP BY day
) AS m ON m.day = v.day;

-- ============================================================== Kesegaran data (heartbeat)
CREATE OR REPLACE VIEW efn_dwh.v_etl_freshness AS
SELECT
    job,
    maxIf(run_ts, status = 'ok')                                            AS last_ok_at,
    argMax(status, run_ts)                                                  AS last_status,
    max(run_ts)                                                             AS last_run_at,
    dateDiff('minute', maxIf(run_ts, status = 'ok'), now64(6, 'UTC'))      AS minutes_since_ok,
    countIf(status != 'ok' AND run_ts >= now64(6, 'UTC') - INTERVAL 1 DAY)  AS failures_24h
FROM efn_dwh.etl_job_run
GROUP BY job;
