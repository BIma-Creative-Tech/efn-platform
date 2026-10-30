-- Gelombang 32 (docs/04 §21, K25-08): mart waste & yield Central Kitchen dari Odoo efn_ck_production (efn_etl/waste.py).
-- Idempoten (CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy
-- setelah 01..10.
--
-- PDP: tanpa partner (owner_id), user pelaku, origin/nama dokumen, lot. Hanya id teknis, PT, gudang, produk, alasan,
-- qty, nilai, cap waktu. Versi = write_date Odoo; baris yang tidak lagi `done` (unbuild/batal) -> is_done = 0
-- (view hanya membaca is_done = 1).

-- ============================================================== Tabel
-- Satu baris per scrap. qty dalam UoM produk; waste_value = nilai valuasi Odoo (efn_waste_value).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_scrap
(
    scrap_id       UInt64,
    company_id     UInt32,
    brand          LowCardinality(String),
    warehouse_id   UInt32,
    product_id     UInt32,
    categ_id       UInt32,
    reason_id      UInt32,                          -- 0 = tanpa alasan (data lama sebelum alasan wajib)
    production_id  UInt64,                          -- 0 = bukan scrap MO
    qty            Decimal(18, 3),
    waste_value    Decimal(18, 2),
    is_done        UInt8,
    date_done      DateTime('Asia/Jakarta'),
    write_date     DateTime64(6, 'UTC'),
    _loaded_at     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(date_done)
ORDER BY (company_id, warehouse_id, scrap_id);

CREATE TABLE IF NOT EXISTS efn_dwh.dim_scrap_reason
(
    reason_id   UInt32,
    name        String,
    write_date  DateTime64(6, 'UTC'),
    _loaded_at  DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY reason_id;

-- Satu baris per MO. yield_pct/variance_value/alert = nilai tersimpan Odoo (_compute_efn_yield).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_mo_yield
(
    production_id   UInt64,
    company_id      UInt32,
    brand           LowCardinality(String),
    warehouse_id    UInt32,
    product_id      UInt32,
    bom_id          UInt32,
    planned_qty     Decimal(18, 3),
    produced_qty    Decimal(18, 3),
    yield_pct       Decimal(9, 2),
    byproduct_qty   Decimal(18, 3),
    variance_value  Decimal(18, 2),
    alert           UInt8,
    is_done         UInt8,
    date_finished   DateTime('Asia/Jakarta'),
    write_date      DateTime64(6, 'UTC'),
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(date_finished)
ORDER BY (company_id, warehouse_id, production_id);

-- Satu baris per MO x komponen (varians konsumsi aktual vs BoM, rumus Odoo _efn_variance_rows).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_mo_component
(
    production_id   UInt64,
    company_id      UInt32,
    warehouse_id    UInt32,
    product_id      UInt32,
    expected_qty    Decimal(18, 3),
    actual_qty      Decimal(18, 3),
    variance_qty    Decimal(18, 3),
    variance_pct    Decimal(9, 1),
    variance_value  Decimal(18, 2),
    is_done         UInt8,
    date_finished   DateTime('Asia/Jakarta'),
    write_date      DateTime64(6, 'UTC'),
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(date_finished)
ORDER BY (company_id, production_id, product_id);

-- Ambang peringatan per PT (nilai SAAT INI; ambang historis tidak disimpan).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_ck_threshold
(
    company_id         UInt32,
    brand              LowCardinality(String),
    yield_warn_pct     Decimal(9, 2),
    variance_warn_pct  Decimal(9, 2),
    waste_warn_value   Decimal(18, 2),               -- 0 = nonaktif
    write_date         DateTime64(6, 'UTC'),
    _loaded_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY company_id;

CREATE TABLE IF NOT EXISTS efn_dwh.dim_warehouse
(
    warehouse_id  UInt32,
    name          String,
    company_id    UInt32,
    is_ck         UInt8,
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY warehouse_id;

-- ============================================================== View (semantic layer Metabase)
-- Waste per hari x PT x gudang x alasan x kategori produk.
CREATE OR REPLACE VIEW efn_dwh.v_waste_daily AS
SELECT
    w.day AS day, w.company_id AS company_id, w.brand AS brand,
    w.warehouse_id AS warehouse_id, if(wh.name = '', concat('Gudang #', toString(w.warehouse_id)), wh.name) AS warehouse_name,
    w.reason_id AS reason_id, if(r.name = '', 'Tanpa alasan', r.name) AS reason_name,
    if(w.category = '', 'Tanpa kategori', w.category) AS category,
    w.scraps AS scraps, w.qty AS qty, w.waste_value AS waste_value
FROM
(
    SELECT toDate(s.date_done) AS day, s.company_id AS company_id, s.brand AS brand, s.warehouse_id AS warehouse_id,
           s.reason_id AS reason_id, p.category AS category,
           count() AS scraps, sum(s.qty) AS qty, sum(s.waste_value) AS waste_value
    FROM (SELECT * FROM efn_dwh.fact_scrap FINAL WHERE is_done = 1) AS s
    LEFT JOIN (SELECT product_id, category FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = s.product_id
    GROUP BY day, company_id, brand, warehouse_id, reason_id, category
) AS w
LEFT JOIN (SELECT reason_id, name FROM efn_dwh.dim_scrap_reason FINAL) AS r ON r.reason_id = w.reason_id
LEFT JOIN (SELECT warehouse_id, name FROM efn_dwh.dim_warehouse FINAL) AS wh ON wh.warehouse_id = w.warehouse_id;

-- Waste per hari x PT x gudang + ambang nilai waste PT (saat ini).
CREATE OR REPLACE VIEW efn_dwh.v_waste_warehouse_daily AS
SELECT
    w.day AS day, w.company_id AS company_id, w.brand AS brand,
    w.warehouse_id AS warehouse_id, if(wh.name = '', concat('Gudang #', toString(w.warehouse_id)), wh.name) AS warehouse_name,
    wh.is_ck AS is_ck, w.scraps AS scraps, w.waste_value AS waste_value,
    t.waste_warn_value AS waste_warn_value,
    if(t.waste_warn_value > 0 AND w.waste_value > t.waste_warn_value, 1, 0) AS over_threshold
FROM
(
    SELECT toDate(date_done) AS day, company_id, brand, warehouse_id, count() AS scraps, sum(waste_value) AS waste_value
    FROM efn_dwh.fact_scrap FINAL
    WHERE is_done = 1
    GROUP BY day, company_id, brand, warehouse_id
) AS w
LEFT JOIN (SELECT company_id, waste_warn_value FROM efn_dwh.dim_ck_threshold FINAL) AS t ON t.company_id = w.company_id
LEFT JOIN (SELECT warehouse_id, name, is_ck FROM efn_dwh.dim_warehouse FINAL) AS wh ON wh.warehouse_id = w.warehouse_id;

-- Satu baris per MO selesai (alert = penanda Odoo saat MO selesai).
CREATE OR REPLACE VIEW efn_dwh.v_mo_yield AS
SELECT
    toDate(m.date_finished) AS day, m.production_id AS production_id, m.company_id AS company_id, m.brand AS brand,
    m.warehouse_id AS warehouse_id, if(wh.name = '', concat('Gudang #', toString(m.warehouse_id)), wh.name) AS warehouse_name,
    m.product_id AS product_id, if(p.name = '', concat('Produk #', toString(m.product_id)), p.name) AS product_name,
    p.default_code AS default_code, m.planned_qty AS planned_qty, m.produced_qty AS produced_qty,
    m.yield_pct AS yield_pct, m.byproduct_qty AS byproduct_qty, m.variance_value AS variance_value,
    m.alert AS alert, t.yield_warn_pct AS yield_warn_pct
FROM (SELECT * FROM efn_dwh.fact_mo_yield FINAL WHERE is_done = 1) AS m
LEFT JOIN (SELECT product_id, name, default_code FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = m.product_id
LEFT JOIN (SELECT warehouse_id, name FROM efn_dwh.dim_warehouse FINAL) AS wh ON wh.warehouse_id = m.warehouse_id
LEFT JOIN (SELECT company_id, yield_warn_pct FROM efn_dwh.dim_ck_threshold FINAL) AS t ON t.company_id = m.company_id;

-- Yield per hari x PT x gudang x produk: rata-rata TERTIMBANG (Σ aktual / Σ rencana), bukan rata-rata persen.
CREATE OR REPLACE VIEW efn_dwh.v_mo_yield_daily AS
SELECT
    a.day AS day, a.company_id AS company_id, a.brand AS brand, a.warehouse_id AS warehouse_id,
    a.warehouse_name AS warehouse_name, a.product_id AS product_id, a.product_name AS product_name,
    a.mos AS mos, a.planned AS planned_qty, a.produced AS produced_qty,
    if(a.planned = 0, NULL, round(a.produced / a.planned * 100, 2)) AS yield_pct,
    a.byproduct AS byproduct_qty, a.variance AS variance_value, a.alerts AS mos_alert
FROM
(
    SELECT day, company_id, brand, warehouse_id, warehouse_name, product_id, product_name,
           count() AS mos, sum(planned_qty) AS planned, sum(produced_qty) AS produced,
           sum(byproduct_qty) AS byproduct, sum(variance_value) AS variance, sum(alert) AS alerts
    FROM efn_dwh.v_mo_yield
    GROUP BY day, company_id, brand, warehouse_id, warehouse_name, product_id, product_name
) AS a;

-- Varians konsumsi per komponen MO + penanda di luar ambang PT (saat ini).
CREATE OR REPLACE VIEW efn_dwh.v_mo_component_variance AS
SELECT
    toDate(c.date_finished) AS day, c.production_id AS production_id, c.company_id AS company_id, m.brand AS brand,
    c.warehouse_id AS warehouse_id, if(wh.name = '', concat('Gudang #', toString(c.warehouse_id)), wh.name) AS warehouse_name,
    m.product_id AS finished_product_id,
    if(fp.name = '', concat('Produk #', toString(m.product_id)), fp.name) AS finished_product_name,
    c.product_id AS component_id, if(p.name = '', concat('Produk #', toString(c.product_id)), p.name) AS component_name,
    c.expected_qty AS expected_qty, c.actual_qty AS actual_qty, c.variance_qty AS variance_qty,
    c.variance_pct AS variance_pct, c.variance_value AS variance_value, t.variance_warn_pct AS variance_warn_pct,
    if(abs(c.variance_pct) > t.variance_warn_pct, 1, 0) AS over_threshold
FROM (SELECT * FROM efn_dwh.fact_mo_component FINAL WHERE is_done = 1) AS c
LEFT JOIN (SELECT production_id, product_id, brand FROM efn_dwh.fact_mo_yield FINAL) AS m ON m.production_id = c.production_id
LEFT JOIN (SELECT product_id, name FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = c.product_id
LEFT JOIN (SELECT product_id, name FROM efn_dwh.dim_product FINAL) AS fp ON fp.product_id = m.product_id
LEFT JOIN (SELECT warehouse_id, name FROM efn_dwh.dim_warehouse FINAL) AS wh ON wh.warehouse_id = c.warehouse_id
LEFT JOIN (SELECT company_id, variance_warn_pct FROM efn_dwh.dim_ck_threshold FINAL) AS t ON t.company_id = c.company_id;
