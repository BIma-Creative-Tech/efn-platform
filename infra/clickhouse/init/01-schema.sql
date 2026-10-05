-- Dijalankan sekali saat volume ClickHouse kosong. Data mart awal (Cetak Biru §7).
-- Semua tabel yang dimuat ETL memakai ReplacingMergeTree dengan kolom versi (updated_at / write_date) agar
-- re-run & overlap window ETL idempotent (ADR-003). Query analitik membaca lewat view ber-FINAL (02-*.sql).
-- Catatan: perubahan skrip init TIDAK diterapkan ke volume yang sudah ada; untuk dev lakukan
-- `docker compose down -v` pada volume clickhouse-data, atau jalankan DDL setara secara manual.
CREATE DATABASE IF NOT EXISTS efn_dwh;

-- Hasil Price Hunter (status akhir valid|rejected|flagged) dari platform.price_hunter_report.
-- Pelapor dipseudonimkan: user_hash disalin apa adanya dari platform.price_hunter_report.user_hash (dihitung BFF,
-- rumus docs/04 §1; ETL tidak memegang pepper, SEC-16), identik dengan loyalty_events.member_hash.
-- Koordinat dibulatkan 3 desimal (~100 m). Data at rest: disk terenkripsi (config.d/encrypted-storage.xml).
CREATE TABLE IF NOT EXISTS efn_dwh.competitor_prices
(
    report_id        UUID,
    reported_at      DateTime('Asia/Jakarta'),
    product_name     String,
    product_sku      Nullable(String),
    competitor_name  LowCardinality(String),
    price            Decimal(14, 2),
    extracted_price  Nullable(Decimal(14, 2)),
    status           LowCardinality(String),
    reason           LowCardinality(String),
    ocr_confidence   Nullable(Float32),
    geo_lat          Nullable(Float64),
    geo_lng          Nullable(Float64),
    user_hash        String,
    updated_at       DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(reported_at)
ORDER BY (report_id);

-- Sales mart (dari Odoo pos.order.line; sale.order.line menyusul), dimuat incremental.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_sales_line
(
    line_id          UInt64,
    source           LowCardinality(String),      -- pos | online
    company_id       UInt32,
    company_name     LowCardinality(String),
    brand            LowCardinality(String),
    store_id         UInt32,
    order_ref        String,
    order_date       DateTime('Asia/Jakarta'),
    customer_id      Nullable(UInt32),             -- id internal res.partner (pseudonim), bukan nomor HP
    product_id       UInt32,
    product_name     String,
    category         LowCardinality(String),
    qty              Decimal(14, 3),
    price_unit       Decimal(14, 2),
    discount_pct     Decimal(6, 2),
    subtotal         Decimal(14, 2),
    tax_amount       Decimal(14, 2),
    cost_total       Nullable(Decimal(14, 2)),
    write_date       DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(order_date)
ORDER BY (company_id, store_id, order_date, line_id);

-- Agregat harian untuk Metabase.
-- Sengaja VIEW (bukan MATERIALIZED VIEW SummingMergeTree seperti scaffold awal): MV dipicu per INSERT,
-- sehingga baris yang dimuat ulang oleh overlap window ETL akan terhitung ganda. Bila volume menuntut
-- pre-agregasi, ganti dengan tabel agregat yang dibangun ulang per partisi dari FINAL.
CREATE VIEW IF NOT EXISTS efn_dwh.v_sales_daily AS
SELECT
    toDate(order_date)       AS day,
    company_id, brand, store_id, category,
    sum(qty)                 AS qty,
    sum(subtotal)            AS revenue,
    sum(tax_amount)          AS tax,
    sum(cost_total)          AS cogs,
    count()                  AS lines
FROM efn_dwh.fact_sales_line FINAL
GROUP BY day, company_id, brand, store_id, category;
