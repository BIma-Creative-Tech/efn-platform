-- Gelombang 21 (efn-data-ai-engineer, ADR-036 §0.7, docs/04 §13.2 E-38b): penjualan per wilayah domisili pembeli.
-- Idempoten (ADD COLUMN IF NOT EXISTS / CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan
-- clickhouse-init.sh setelah 01..08. Sumber: view Odoo efn_etl.partner_region & efn_etl.region
-- (services/etl/sql/odoo-readonly-grants.sql bagian G21), dimuat ETL bila ETL_PARTNER_REGION_ENABLED=true.
--
-- PDP: TIDAK ada tabel partner -> wilayah di ClickHouse (minimisasi). Kode kab/kota disalin ke baris penjualan saat
-- baris dimuat (wilayah domisili pada saat itu). Kebocoran yang diterima ADR-036 §0.7 / Q-PII10: kab/kota (±500
-- nilai). customer_id di baris yang sama tetap ber-TTL pseudonim (clickhouse-init.sh) dan tidak di-grant ke Metabase.

-- ============================================================== fact_sales_line: wilayah pembeli
-- '' = order tanpa member, member tanpa wilayah, atau baris dimuat sebelum G21 (backfill: README ETL).
ALTER TABLE efn_dwh.fact_sales_line ADD COLUMN IF NOT EXISTS customer_region_code LowCardinality(String) DEFAULT '';

-- ============================================================== dim_region: master kab/kota Kemendagri (efn.region)
CREATE TABLE IF NOT EXISTS efn_dwh.dim_region
(
    region_code  String,                    -- PP.KK, mis. 31.71
    name         String,
    state_code   LowCardinality(String),
    state_name   LowCardinality(String),
    active       UInt8,
    write_date   DateTime64(6, 'UTC'),
    _loaded_at   DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY region_code;

-- ============================================================== v_pos_sales_region_daily (Metabase)
-- Per hari x PT x outlet x wilayah domisili pembeli. Nilai uang tanpa pajak (sama dengan v_pos_outlet_daily).
-- Tanpa hitungan member unik: customer_id sengaja tidak dibaca view (INVOKER -> butuh grant identitas pseudonim).
CREATE OR REPLACE VIEW efn_dwh.v_pos_sales_region_daily AS
SELECT
    s.day AS day, s.company_id AS company_id, s.company_name AS company_name, s.brand AS brand,
    s.store_id AS store_id, if(o.name = '', concat('POS #', toString(s.store_id)), o.name) AS store_name,
    s.region_code AS region_code,
    multiIf(s.region_code = '', 'Tanpa wilayah', r.name = '', concat('Wilayah ', s.region_code), r.name) AS region_name,
    if(s.region_code = '', '', r.state_name) AS state_name,
    s.orders AS orders, s.refund_orders AS refund_orders, s.gross_sales AS gross_sales, s.discounts AS discounts,
    s.refunds AS refunds, s.net_sales AS net_sales, s.qty_sold AS qty_sold, s.lines AS lines
FROM
(
    SELECT
        toDate(order_date) AS day, company_id, company_name, brand, store_id, customer_region_code AS region_code,
        uniqExactIf(order_id, is_refund = 0)                                         AS orders,
        uniqExactIf(order_id, is_refund = 1)                                         AS refund_orders,
        sumIf(subtotal + discount_amount, line_kind = 'product' AND is_refund = 0)  AS gross_sales,
        sumIf(discount_amount, line_kind = 'product' AND is_refund = 0)
            - sumIf(subtotal, line_kind IN ('discount', 'reward') AND is_refund = 0) AS discounts,
        -sumIf(subtotal, is_refund = 1)                                              AS refunds,
        sum(subtotal)                                                                AS net_sales,
        sumIf(qty, line_kind = 'product' AND is_refund = 0)                          AS qty_sold,
        count()                                                                      AS lines
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos'
    GROUP BY day, company_id, company_name, brand, store_id, region_code
) AS s
LEFT JOIN (SELECT store_id, name FROM efn_dwh.dim_outlet FINAL) AS o ON o.store_id = s.store_id
LEFT JOIN (SELECT region_code, name, state_name FROM efn_dwh.dim_region FINAL) AS r ON r.region_code = s.region_code;
