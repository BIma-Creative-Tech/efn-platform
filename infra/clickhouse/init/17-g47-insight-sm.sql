-- Gelombang 47 (efn-data-ai-engineer): EFN Insight supermarket per persona (docs/04 §28.13 v20.3, ADR-041 §3.3-§3.4
-- Proposed, L-186..L-203). Idempoten (CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE VIEW).
-- Dijalankan clickhouse-init.sh setiap deploy setelah 01..16 (butuh tabel G44 15-g44 dan G50 16-g50: #114/#117 sudah di
-- main, jadi view perilaku/kampanye ada di file ini; tidak perlu 18-g47-insight-sm-g50.sql, L-198).
--
-- Arah data (ADR-003): Odoo replica / PG platform -> ETL 15 menit (services/etl/efn_etl/sm*.py, flag ETL_ENABLE_SM,
-- ETL_ENABLE_SM_EXPIRY, ETL_ENABLE_SM_CAMPAIGN; semua default false) -> tabel di bawah -> view efn_dwh.v_sm_* (INVOKER,
-- FINAL di subquery) -> view efn_insight.v_sm_* DEFINER (access/insight-sm-access.sql) + row policy efn_insight_scope
-- kelas W/C/F (access/insight-sm-policy.sh, disourcing clickhouse-init.sh).
--
-- PDP / minimisasi (§28.13.8): tanpa user pelaku (approved_by, changed_by, user_id, activated_by, maker_id), tanpa partner
-- pelanggan, tanpa member, tanpa teks bebas selain nama master. Supplier perorangan tanpa nama. Visitor hanya agregat.
-- L-196: view SM TIDAK membaca efn_esb / fact_esb_* / fact_legacy_sales_daily / map_esb_odoo.
-- Semua uang Decimal(18,2), qty Decimal(18,3), waktu UTC kecuali disebut. DDL menang untuk detail kolom (§28.13.8);
-- tambahan di luar kontrak ditandai "G47 tambahan" + alasannya.

-- ============================================================== Dimensi
CREATE TABLE IF NOT EXISTS efn_dwh.dim_sm_warehouse
(
    warehouse_id  UInt32,
    code          String,
    name          String,
    company_id    UInt32,
    company_code  LowCardinality(String),
    is_dc         UInt8,
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY warehouse_id;

-- Pemetaan (channel_code, outlet_code) event -> warehouse (L-190, Q-SM37 sementara). Kunci peka huruf besar.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_online_outlet
(
    channel_code  LowCardinality(String),
    outlet_code   String,
    outlet_id     UInt32,
    company_id    UInt32,
    warehouse_id  UInt32,
    active        UInt8,
    write_date    DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY (channel_code, outlet_code);

-- Konfigurasi KPI per PT (L-200). value_str '' untuk kunci numerik; active = 0 -> view memakai default.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_sm_kpi_config
(
    company_id  UInt32,
    key         LowCardinality(String),
    value_num   Nullable(Float64),
    value_str   String,
    active      UInt8,
    write_date  DateTime64(6, 'UTC'),
    _loaded_at  DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY (company_id, key);

-- Supplier DC. name '' bila partner bukan perusahaan (minimisasi PDP; UI menampilkan "Supplier #<id>").
CREATE TABLE IF NOT EXISTS efn_dwh.dim_sm_supplier
(
    supplier_id  UInt32,
    name         String,
    write_date   DateTime64(6, 'UTC'),
    _loaded_at   DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
ORDER BY supplier_id;

-- Kategori produk (job `products` yang ada). Baris lama bernilai default sampai watermark job `products` di-reset
-- (README ETL bagian G47).
ALTER TABLE efn_dwh.dim_product ADD COLUMN IF NOT EXISTS categ_id UInt32 DEFAULT 0;
ALTER TABLE efn_dwh.dim_product ADD COLUMN IF NOT EXISTS categ_parent_path String DEFAULT '';
-- G51 (spesifikasi lead G51 §B2/§B4): umur simpan hari (product_template.expiration_time; 0 = tidak dikelola). Di file
-- ini (bukan 18-g51) karena v_sm_product_list di bawah membacanya.
ALTER TABLE efn_dwh.dim_product ADD COLUMN IF NOT EXISTS shelf_life_days UInt16 DEFAULT 0;

-- ============================================================== Fakta replenishment & DC
-- Satu baris per baris usulan. header_ts (G47 tambahan) = write_date header usulan: Odoo MENGHAPUS baris lama saat usulan
-- dihitung ulang (draft/proposed); view hanya memakai baris dengan header_ts terbaru per usulan (baris yang dihapus tidak
-- pernah dibaca ulang sehingga header_ts-nya tertinggal).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_replenishment
(
    line_id              UInt64,
    proposal_id          UInt64,
    company_id           UInt32,
    warehouse_id         UInt32,
    supply_warehouse_id  UInt32,
    schedule_id          UInt32,
    delivery_date        Date,
    cutoff_at            DateTime64(0, 'UTC'),
    state                LowCardinality(String),
    approved_at          Nullable(DateTime64(0, 'UTC')),
    picking_id           UInt64,
    run_id               UInt64,
    product_id           UInt32,
    forecast_qty         Decimal(18, 3),
    on_hand              Decimal(18, 3),
    in_transit           Decimal(18, 3),
    qty_need             Decimal(18, 3),
    qty_shelf_cap        Decimal(18, 3),
    qty_rack_cap         Decimal(18, 3),
    qty_proposed         Decimal(18, 3),
    qty_approved         Decimal(18, 3),
    excluded             UInt8,
    reason_codes         String,
    change_reason        LowCardinality(String),
    header_ts            DateTime64(6, 'UTC'),
    version_ts           DateTime64(6, 'UTC'),
    _loaded_at           DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version_ts)
PARTITION BY toYYYYMM(delivery_date)
ORDER BY (company_id, warehouse_id, delivery_date, proposal_id, line_id)
TTL delivery_date + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Log bias append-only (Odoo efn.replenishment.bias.log tidak bisa diubah/dihapus). MergeTree: dedup di ETL (log_id yang
-- sudah ada di tabel tidak ditulis ulang) + view memakai LIMIT 1 BY log_id (pertahanan berlapis). Tanpa user_id.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_replenishment_bias
(
    log_id            UInt64,
    company_id        UInt32,
    kind              LowCardinality(String),
    proposal_id       UInt64,
    warehouse_id      UInt32,
    product_id        UInt32,
    run_id            UInt64,
    purchase_line_id  UInt64,
    qty_system        Decimal(18, 3),
    qty_before        Decimal(18, 3),
    qty_after         Decimal(18, 3),
    change_reason     LowCardinality(String),
    created_at        DateTime64(6, 'UTC'),
    _loaded_at        DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (company_id, warehouse_id, log_id)
TTL toDateTime(created_at) + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Fill rate DC per baris usulan disetujui (dihitung ulang ETL untuk jendela ETL_SM_FILL_RECALC_DAYS). warehouse_id =
-- outlet tujuan. Fit-gap G47 C-3: qty_shipped = Σ move done ber-tujuan lokasi transit di picking usulan + backorder
-- (≤ 3; bila picking usulan ternyata picking pick, diikuti satu langkah move_dest ke move ship); qty_received = move
-- lanjutan berantai (stock_move_move_rel, ≤ 2) yang MELINTASI batas outlet (tujuan ber-warehouse outlet, asal bukan
-- outlet) -> penerimaan 2/3 langkah tidak terhitung dua kali. picking_shared = picking dipakai > 1 usulan (alokasi FIFO;
-- diharapkan ≈ 0 di CE 19, fit-gap §6.2).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_dc_fill_rate
(
    line_id           UInt64,
    proposal_id       UInt64,
    company_id        UInt32,
    warehouse_id      UInt32,
    dc_warehouse_id   UInt32,
    product_id        UInt32,
    delivery_date     Date,
    qty_approved      Decimal(18, 3),
    qty_shipped       Decimal(18, 3),
    qty_received      Decimal(18, 3),
    last_done_at      Nullable(DateTime64(0, 'UTC')),
    last_received_at  Nullable(DateTime64(0, 'UTC')),
    on_time           UInt8,
    picking_shared    UInt8,
    computed_at       DateTime64(6, 'UTC'),
    _loaded_at        DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(delivery_date)
ORDER BY (company_id, warehouse_id, delivery_date, line_id)
TTL delivery_date + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Baris PO DC (warehouse_id = DC). Tanpa nama/kontak supplier. product_qty & qty_received dalam UoM baris PO.
-- date_order RFQ draf dapat berubah -> partisi berbeda: view memakai LIMIT 1 BY line_id (versi terbaru).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_dc_purchase_line
(
    line_id          UInt64,
    order_id         UInt64,
    company_id       UInt32,
    warehouse_id     UInt32,
    supplier_id      UInt32,
    product_id       UInt32,
    po_state         LowCardinality(String),
    date_order       DateTime64(0, 'UTC'),
    date_approve     Nullable(DateTime64(0, 'UTC')),
    date_planned     Nullable(DateTime64(0, 'UTC')),
    product_qty      Decimal(18, 3),
    qty_received     Decimal(18, 3),
    price_subtotal   Decimal(18, 2),
    from_orderpoint  UInt8,
    version_ts       DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(version_ts)
PARTITION BY toYYYYMM(date_order)
ORDER BY (company_id, warehouse_id, order_id, line_id)
TTL toDateTime(date_order) + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== Agregat stok harian (nilai mentah, L-200)
-- Snapshot selesai terakhir per hari (WIB). Status OOS/kritis dihitung view dari dim_sm_kpi_config sehingga perubahan
-- definisi berlaku juga untuk hari lampau. Bucket umur (in_date) & sisa umur lot (expiration_date) tetap (Q-INS-S10).
CREATE TABLE IF NOT EXISTS efn_dwh.agg_sm_stock_daily
(
    day                Date,
    snapshot_ts        DateTime64(6, 'UTC'),
    company_id         UInt32,
    warehouse_id       UInt32,
    product_id         UInt32,
    categ_id           UInt32,
    tracked            UInt8,
    sold_28d           UInt8,
    qty_sold_28d       Decimal(18, 3),
    on_hand_qty        Decimal(18, 3),
    free_qty           Decimal(18, 3),
    min_qty            Decimal(18, 3),
    max_qty            Decimal(18, 3),
    stock_value_cost   Decimal(18, 2),
    value_age_0_30     Decimal(18, 2),
    value_age_31_60    Decimal(18, 2),
    value_age_61_90    Decimal(18, 2),
    value_age_91p      Decimal(18, 2),
    value_exp_expired  Decimal(18, 2),
    value_exp_0_7      Decimal(18, 2),
    value_exp_8_30     Decimal(18, 2),
    value_exp_31_90    Decimal(18, 2),
    value_exp_91p      Decimal(18, 2),
    value_no_lot       Decimal(18, 2),
    computed_at        DateTime64(6, 'UTC'),
    _loaded_at         DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (company_id, day, warehouse_id, product_id)
TTL day + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== Near-expiry & markdown (#114, ETL_ENABLE_SM_EXPIRY)
CREATE TABLE IF NOT EXISTS efn_dwh.fact_lot_expiry
(
    alert_id         UInt64,
    company_id       UInt32,
    warehouse_id     UInt32,
    lot_id           UInt64,
    lot_name         String,
    product_id       UInt32,
    categ_id         UInt32,
    stage            LowCardinality(String),
    state            LowCardinality(String),
    detected_date    Date,
    last_run_date    Date,
    days_left        Int32,
    expiration_date  Nullable(DateTime64(0, 'UTC')),
    qty              Decimal(18, 3),
    write_date       DateTime64(6, 'UTC'),
    _loaded_at       DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(detected_date)
ORDER BY (company_id, warehouse_id, alert_id)
TTL detected_date + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- date_from markdown draf dapat diubah -> partisi berbeda: view memakai LIMIT 1 BY markdown_id. Tanpa activated_by.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_lot_markdown
(
    markdown_id     UInt64,
    company_id      UInt32,
    warehouse_id    UInt32,
    lot_id          UInt64,
    product_id      UInt32,
    alert_id        UInt64,
    state           LowCardinality(String),
    price           Nullable(Decimal(18, 2)),
    discount_pct    Nullable(Decimal(6, 2)),
    date_from       Date,
    date_to         Date,
    qty_cap         Decimal(18, 3),
    qty_sold        Decimal(18, 3),
    notify_members  UInt8,
    write_date      DateTime64(6, 'UTC'),
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(date_from)
ORDER BY (company_id, warehouse_id, markdown_id)
TTL date_from + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Riwayat harian alert terbuka (open|actioned), hanya hari ini WIB dihitung ulang tiap siklus (riwayat sejak job aktif).
CREATE TABLE IF NOT EXISTS efn_dwh.agg_sm_expiry_daily
(
    day           Date,
    company_id    UInt32,
    warehouse_id  UInt32,
    categ_id      UInt32,
    stage         LowCardinality(String),
    lots          UInt32,
    qty           Decimal(18, 3),
    value_cost    Decimal(18, 2),
    computed_at   DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (company_id, day, warehouse_id, categ_id, stage)
TTL day + INTERVAL 3 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== Kampanye & segmen (#117, ETL_ENABLE_SM_CAMPAIGN)
-- Agregat per kampanye. Omzet inkremental tidak dihitung (Q-INS-S14). Redemption = POS (pos_order_line.coupon_id,
-- order paid|done) + online (sale_order_line.coupon_id, SO state 'sale', baris reward = reward_id IS NOT NULL; fit-gap
-- G47 C-4). Satu sumber per pesanan: dokumen tempat kupon diterapkan (pelunasan online di kasir = order POS berisi
-- produk pelunasan tanpa kupon, E-45), sehingga satu pesanan tidak pernah terhitung di dua kanal.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_campaign_result
(
    campaign_id               UInt32,
    company_id                UInt32,
    name                      String,
    kind                      LowCardinality(String),
    origin                    LowCardinality(String),
    state                     LowCardinality(String),
    voucher_mode              LowCardinality(String),
    channels                  Array(LowCardinality(String)),
    date_from                 Date,
    date_to                   Date,
    approved_at               Nullable(DateTime64(0, 'UTC')),
    sent_at                   Nullable(DateTime64(0, 'UTC')),
    approval_rev              UInt32,
    budget_idr                Nullable(Int64),
    segment_code              String,
    segment_member_count      UInt32,
    delivered                 UInt64,
    skipped                   UInt64,
    push_sent                 UInt64,
    email_queued              UInt64,
    wa_queued                 UInt64,
    stop_reason               LowCardinality(String),
    vouchers_issued           UInt64,
    vouchers_used             UInt64,
    orders_with_voucher       UInt64,
    discount_idr              Decimal(18, 2),
    revenue_with_voucher_idr  Decimal(18, 2),
    computed_at               DateTime64(6, 'UTC'),
    _loaded_at                DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
ORDER BY (company_id, campaign_id)
TTL date_to + INTERVAL 2 YEAR;

CREATE TABLE IF NOT EXISTS efn_dwh.fact_sm_segment_run
(
    company_id      UInt32,
    run_id          String,
    segment_code    LowCardinality(String),
    member_count    UInt32,
    generated_at    DateTime64(6, 'UTC'),
    campaign_id     UInt32,
    campaign_state  LowCardinality(String),
    write_date      DateTime64(6, 'UTC'),
    _loaded_at      DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(write_date)
PARTITION BY toYYYYMM(generated_at)
ORDER BY (company_id, generated_at, segment_code, run_id)
TTL toDateTime(generated_at) + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== View pembantu (tidak diekspos ke efn_insight)
-- Semua item SELECT teratas memakai AS (diuji tests/test_insight_sm_access.py).

-- Default literal = docs/contracts/insight_sm_kpi_defaults.json (test parity). Tuple: (kunci, angka|nan, teks).
-- is_default (fit-gap G47 C-1) = baris Odoo tidak ada/nonaktif ATAU nilainya sama dengan default (post_init_hook Odoo
-- membuat semua baris default, jadi "baris ada" saja tidak berarti diubah). effective_since = write_date baris aktif.
CREATE OR REPLACE VIEW efn_dwh.v_sm_kpi_config AS
SELECT
    co.company_id AS company_id,
    d.key AS key,
    if(c.active = 1, c.value_num, if(isNaN(d.num), NULL, d.num)) AS value_num,
    if(c.active = 1, c.value_str, d.str) AS value_str,
    toUInt8(c.active != 1
            OR (isNaN(d.num) AND c.value_str = d.str)
            OR (NOT isNaN(d.num) AND ifNull(c.value_num = d.num, 0) = 1)) AS is_default,
    if(c.active = 1, toNullable(c.write_date), NULL) AS effective_since
FROM (SELECT DISTINCT company_id FROM efn_dwh.dim_sm_warehouse FINAL) AS co
CROSS JOIN
(
    SELECT tupleElement(t, 1) AS key, tupleElement(t, 2) AS num, tupleElement(t, 3) AS str
    FROM
    (
        SELECT arrayJoin([
            ('gmroi.period', nan, 'month'),
            ('gmroi.stock_avg', nan, 'daily'),
            ('gmroi.include_dc', 1.0, ''),
            ('gmroi.annualize', 0.0, ''),
            ('oos.universe', nan, 'orderpoint'),
            ('oos.basis', nan, 'on_hand'),
            ('oos.threshold_qty', 0.0, ''),
            ('oos.include_dc', 0.0, ''),
            ('critical.rule', nan, 'min_or_cover'),
            ('critical.cover_days', 2.0, ''),
            ('critical.sales_window_days', 28.0, ''),
            ('fill.basis', nan, 'shipped'),
            ('fill.level', nan, 'qty'),
            ('fill.grace_days', 0.0, ''),
            ('waste.basis', nan, 'sales_excl_tax'),
            ('thr.oos.warn_pct', 2.0, ''),
            ('thr.oos.bad_pct', 5.0, ''),
            ('thr.fill.good_pct', 95.0, ''),
            ('thr.fill.warn_pct', 90.0, ''),
            ('thr.waste.warn_pct', 2.0, ''),
            ('thr.waste.bad_pct', 4.0, ''),
            ('thr.wape.warn_pct', 25.0, ''),
            ('thr.wape.bad_pct', 40.0, ''),
            ('thr.bias.warn_pct', 5.0, ''),
            ('thr.bias.bad_pct', 10.0, ''),
            ('thr.price.warn_pct', 10.0, ''),
            ('thr.price.bad_pct', 20.0, ''),
            ('thr.price.below_pct', 10.0, ''),
            ('thr.price.min_samples', 3.0, ''),
            ('thr.po_late.warn', 1.0, ''),
            ('thr.po_late.bad', 3.0, ''),
            ('thr.cutoff.urgent_min', 120.0, ''),
            ('thr.stock_age.days', 30.0, ''),
            ('fresh.near_live.warn_min', 30.0, ''),
            ('fresh.near_live.stale_min', 60.0, ''),
            ('fresh.stock.warn_min', 75.0, ''),
            ('fresh.stock.stale_min', 135.0, ''),
            ('fresh.daily.warn_min', 1560.0, ''),
            ('fresh.daily.stale_min', 2880.0, '')
        ]) AS t
    )
) AS d
LEFT JOIN
(
    SELECT company_id, toString(key) AS key, value_num, value_str, active, write_date
    FROM efn_dwh.dim_sm_kpi_config FINAL
) AS c ON c.company_id = co.company_id AND c.key = d.key;

-- Definisi efektif yang dipakai view (satu baris per PT SM). Nilai di luar registri sudah dinonaktifkan ETL.
CREATE OR REPLACE VIEW efn_dwh.v_sm_kpi_settings AS
SELECT
    k.company_id AS company_id,
    anyIf(k.value_str, k.key = 'oos.universe') AS oos_universe,
    anyIf(k.value_str, k.key = 'oos.basis') AS oos_basis,
    ifNull(anyIf(k.value_num, k.key = 'oos.threshold_qty'), 0.0) AS oos_threshold_qty,
    toUInt8(ifNull(anyIf(k.value_num, k.key = 'oos.include_dc'), 0.0)) AS oos_include_dc,
    anyIf(k.value_str, k.key = 'critical.rule') AS critical_rule,
    ifNull(anyIf(k.value_num, k.key = 'critical.cover_days'), 2.0) AS critical_cover_days,
    toUInt16(ifNull(anyIf(k.value_num, k.key = 'critical.sales_window_days'), 28.0)) AS critical_sales_window_days,
    anyIf(k.value_str, k.key = 'fill.basis') AS fill_basis,
    toUInt8(ifNull(anyIf(k.value_num, k.key = 'fill.grace_days'), 0.0)) AS fill_grace_days
FROM efn_dwh.v_sm_kpi_config AS k
GROUP BY k.company_id;

-- Nama & jalur kategori (product.category) dari dim_product.
CREATE OR REPLACE VIEW efn_dwh.v_sm_category AS
SELECT
    p.categ_id AS categ_id,
    any(p.category) AS category,
    any(p.categ_parent_path) AS categ_parent_path
FROM (SELECT categ_id, category, categ_parent_path FROM efn_dwh.dim_product FINAL WHERE categ_id != 0) AS p
GROUP BY p.categ_id;

-- HPP satuan per PT x produk dari snapshot stok selesai terakhir (standard_price per company).
CREATE OR REPLACE VIEW efn_dwh.v_sm_unit_cost AS
SELECT
    s.company_id AS company_id,
    s.product_id AS product_id,
    any(s.unit_cost) AS unit_cost
FROM efn_dwh.fact_stock_snapshot AS s
WHERE s.snapshot_ts = (SELECT max(snapshot_ts) FROM efn_dwh.stock_snapshot_run) AND isNotNull(s.unit_cost)
GROUP BY s.company_id, s.product_id;

-- Penjualan POS (termasuk kiosk pos_self_order) per hari x warehouse SM x produk. Basis uang = price_subtotal (tanpa
-- PPN); net_sales_incl_tax = Σ (subtotal + tax_amount) (L-202). cogs = Σ cost_total baris produk;
-- net_sales_with_cost = penjualan produk yang punya HPP (cakupan HPP, R-T22).
CREATE OR REPLACE VIEW efn_dwh.v_sm_sales_product_daily AS
SELECT
    s.day AS day,
    s.company_id AS company_id,
    o.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    s.product_id AS product_id,
    p.categ_id AS categ_id,
    if(p.category != '', p.category, s.category) AS category,
    s.gross_sales AS gross_sales,
    s.discounts AS discounts,
    s.points_redeemed_idr AS points_redeemed_idr,
    s.refunds AS refunds,
    s.net_sales AS net_sales,
    s.tax AS tax,
    s.net_sales_incl_tax AS net_sales_incl_tax,
    s.cogs AS cogs,
    s.net_sales_with_cost AS net_sales_with_cost,
    s.qty_sold AS qty_sold,
    s.lines AS lines
FROM
(
    SELECT
        toDate(order_date) AS day, company_id, store_id, product_id, any(category) AS category,
        sumIf(subtotal + discount_amount, line_kind = 'product' AND is_refund = 0)   AS gross_sales,
        sumIf(discount_amount, line_kind = 'product' AND is_refund = 0)
            - sumIf(subtotal, line_kind IN ('discount', 'reward') AND is_refund = 0) AS discounts,
        -sumIf(subtotal, line_kind = 'points' AND is_refund = 0)                      AS points_redeemed_idr,
        -sumIf(subtotal, is_refund = 1)                                               AS refunds,
        sum(subtotal)                                                                 AS net_sales,
        sum(tax_amount)                                                               AS tax,
        sum(subtotal + tax_amount)                                                    AS net_sales_incl_tax,
        ifNull(sumIf(cost_total, line_kind = 'product'), toDecimal128(0, 2))          AS cogs,
        sumIf(subtotal, line_kind = 'product' AND isNotNull(cost_total))              AS net_sales_with_cost,
        sumIf(qty, line_kind = 'product' AND is_refund = 0)                           AS qty_sold,
        count()                                                                       AS lines
    FROM efn_dwh.fact_sales_line FINAL
    WHERE source = 'pos'
    GROUP BY day, company_id, store_id, product_id
) AS s
INNER JOIN
(
    SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id FROM efn_dwh.dim_outlet FINAL WHERE isNotNull(warehouse_id)
) AS o ON o.store_id = s.store_id
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = o.warehouse_id AND w.company_id = s.company_id
LEFT JOIN (SELECT product_id, categ_id, category FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = s.product_id;

-- Baris usulan TERKINI (header_ts terbaru per usulan, lihat fact_replenishment). Usulan draft/proposed yang sudah lewat
-- cut-off ditampilkan `expired` (Odoo cron menandainya; usulan yang dihapus Odoo tidak pernah mendapat versi baru).
CREATE OR REPLACE VIEW efn_dwh.v_sm_replenishment_current AS
SELECT
    r.line_id AS line_id,
    r.proposal_id AS proposal_id,
    r.company_id AS company_id,
    r.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    r.supply_warehouse_id AS supply_warehouse_id,
    r.delivery_date AS delivery_date,
    r.cutoff_at AS cutoff_at,
    if(r.state IN ('draft', 'proposed') AND r.cutoff_at < now(), 'expired', toString(r.state)) AS state,
    r.approved_at AS approved_at,
    r.picking_id AS picking_id,
    r.product_id AS product_id,
    p.categ_id AS categ_id,
    r.qty_proposed AS qty_proposed,
    r.qty_approved AS qty_approved,
    r.excluded AS excluded,
    r.reason_codes AS reason_codes,
    r.change_reason AS change_reason
FROM
(
    SELECT line_id, proposal_id, company_id, warehouse_id, supply_warehouse_id, delivery_date, cutoff_at, state,
           approved_at, picking_id, product_id, qty_proposed, qty_approved, excluded, reason_codes, change_reason, header_ts
    FROM efn_dwh.fact_replenishment FINAL
) AS r
INNER JOIN
(
    SELECT proposal_id, max(header_ts) AS header_ts FROM efn_dwh.fact_replenishment GROUP BY proposal_id
) AS h ON h.proposal_id = r.proposal_id AND h.header_ts = r.header_ts
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = r.warehouse_id AND w.company_id = r.company_id
LEFT JOIN (SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = r.product_id;

-- ============================================================== View semantik SM (dibungkus efn_insight.v_sm_*)
-- Tabel §28.13.9. Semua view berbasis warehouse hanya memuat company/warehouse di dim_sm_warehouse (inner join).

CREATE OR REPLACE VIEW efn_dwh.v_sm_warehouse_list AS
SELECT
    w.company_id AS company_id,
    w.company_code AS company_code,
    w.warehouse_id AS warehouse_id,
    w.code AS warehouse_code,
    w.name AS warehouse_name,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    w.active AS active
FROM (SELECT warehouse_id, code, name, company_id, company_code, is_dc, active FROM efn_dwh.dim_sm_warehouse FINAL) AS w;

-- Produk ber-company_id PT SM (produk lintas PT tidak terlihat, Q-SM42).
CREATE OR REPLACE VIEW efn_dwh.v_sm_product_list AS
SELECT
    p.company_id AS company_id,
    p.product_id AS product_id,
    p.template_id AS product_tmpl_id,
    p.default_code AS default_code,
    p.name AS product_name,
    p.categ_id AS categ_id,
    p.category AS category,
    p.categ_parent_path AS categ_parent_path,
    p.active AS active,
    p.shelf_life_days AS shelf_life_days
FROM
(
    SELECT product_id, template_id, default_code, name, categ_id, category, categ_parent_path, active, shelf_life_days,
           assumeNotNull(company_id) AS company_id
    FROM efn_dwh.dim_product FINAL
    WHERE isNotNull(company_id)
) AS p
INNER JOIN (SELECT DISTINCT company_id FROM efn_dwh.dim_sm_warehouse FINAL) AS co ON co.company_id = p.company_id;

CREATE OR REPLACE VIEW efn_dwh.v_sm_sales_daily AS
SELECT
    a.day AS day,
    a.company_id AS company_id,
    a.warehouse_id AS warehouse_id,
    a.outlet_type AS outlet_type,
    a.categ_id AS categ_id,
    a.category AS category,
    sum(a.gross_sales) AS gross_sales,
    sum(a.discounts) AS discounts,
    sum(a.points_redeemed_idr) AS points_redeemed_idr,
    sum(a.refunds) AS refunds,
    sum(a.net_sales) AS net_sales,
    sum(a.tax) AS tax,
    sum(a.net_sales_incl_tax) AS net_sales_incl_tax,
    sum(a.qty_sold) AS qty_sold,
    sum(a.lines) AS lines
FROM efn_dwh.v_sm_sales_product_daily AS a
GROUP BY a.day, a.company_id, a.warehouse_id, a.outlet_type, a.categ_id, a.category;

-- Ringkasan outlet per hari (grain tanpa kategori: order unik per POS config). Rata-rata belanja termasuk PPN, tanpa
-- order refund.
CREATE OR REPLACE VIEW efn_dwh.v_sm_outlet_daily AS
SELECT
    t.day AS day,
    t.company_id AS company_id,
    t.warehouse_id AS warehouse_id,
    t.outlet_type AS outlet_type,
    sum(t.orders_n) AS orders,
    sum(t.net) AS net_sales,
    sum(t.net_incl) AS net_sales_incl_tax,
    sum(t.refund_amt) AS refunds,
    if(sum(t.orders_n) = 0, NULL, round(toFloat64(sum(t.sales_incl)) / sum(t.orders_n), 0)) AS avg_basket_incl_tax
FROM
(
    SELECT
        s.day AS day, s.company_id AS company_id, o.warehouse_id AS warehouse_id,
        if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type, s.orders_n AS orders_n, s.net AS net, s.net_incl AS net_incl,
        s.refund_amt AS refund_amt, s.sales_incl AS sales_incl
    FROM
    (
        SELECT
            toDate(order_date) AS day, company_id, store_id,
            uniqExactIf(order_id, is_refund = 0)       AS orders_n,
            sum(subtotal)                              AS net,
            sum(subtotal + tax_amount)                 AS net_incl,
            -sumIf(subtotal, is_refund = 1)            AS refund_amt,
            sumIf(subtotal + tax_amount, is_refund = 0) AS sales_incl
        FROM efn_dwh.fact_sales_line FINAL
        WHERE source = 'pos'
        GROUP BY day, company_id, store_id
    ) AS s
    INNER JOIN
    (
        SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id FROM efn_dwh.dim_outlet FINAL WHERE isNotNull(warehouse_id)
    ) AS o ON o.store_id = s.store_id
    INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
        ON w.warehouse_id = o.warehouse_id AND w.company_id = s.company_id
) AS t
GROUP BY t.day, t.company_id, t.warehouse_id, t.outlet_type;

-- Margin kotor TANPA PPN (L-202): gross_margin = net_sales - cogs. Cakupan HPP = net_sales_with_cost / net_sales.
CREATE OR REPLACE VIEW efn_dwh.v_sm_margin_daily AS
SELECT
    m.day AS day,
    m.company_id AS company_id,
    m.warehouse_id AS warehouse_id,
    m.outlet_type AS outlet_type,
    m.categ_id AS categ_id,
    m.category AS category,
    m.ns AS net_sales,
    m.cg AS cogs,
    m.ns - m.cg AS gross_margin,
    if(m.ns = 0, NULL, round(toFloat64(m.ns - m.cg) / toFloat64(m.ns) * 100, 2)) AS gross_margin_pct,
    m.nwc AS net_sales_with_cost
FROM
(
    SELECT a.day AS day, a.company_id AS company_id, a.warehouse_id AS warehouse_id, a.outlet_type AS outlet_type,
           a.categ_id AS categ_id, a.category AS category, sum(a.net_sales) AS ns, sum(a.cogs) AS cg,
           sum(a.net_sales_with_cost) AS nwc
    FROM efn_dwh.v_sm_sales_product_daily AS a
    GROUP BY a.day, a.company_id, a.warehouse_id, a.outlet_type, a.categ_id, a.category
) AS m;

-- GMROI bulanan: penyebut rata-rata harian (Σ nilai akhir hari / hari ber-snapshot) atau nilai akhir bulan (snapshot
-- hari terakhir bulan itu per warehouse). Periode/DC/anualisasi diterapkan aplikasi dari v_sm_kpi_config (§28.13.8c).
CREATE OR REPLACE VIEW efn_dwh.v_sm_gmroi_monthly AS
SELECT
    u.month AS month,
    u.company_id AS company_id,
    u.warehouse_id AS warehouse_id,
    u.outlet_type AS outlet_type,
    u.categ_id AS categ_id,
    if(c.category = '', 'Tanpa kategori', c.category) AS category,
    sum(u.gm) AS gross_margin,
    sum(u.avg_daily) AS avg_stock_value_cost_daily,
    sum(u.avg_me) AS avg_stock_value_cost_month_end,
    max(u.sd) AS stock_days,
    toUInt8(toDayOfMonth(toLastDayOfMonth(u.month))) AS days_in_month
FROM
(
    SELECT
        toStartOfMonth(m.day) AS month, m.company_id AS company_id, m.warehouse_id AS warehouse_id,
        m.outlet_type AS outlet_type, m.categ_id AS categ_id, toDecimal128(m.gross_margin, 2) AS gm,
        toDecimal128(0, 2) AS avg_daily, toDecimal128(0, 2) AS avg_me, toUInt16(0) AS sd
    FROM efn_dwh.v_sm_margin_daily AS m

    UNION ALL

    SELECT
        v.month AS month, v.company_id AS company_id, v.warehouse_id AS warehouse_id, v.outlet_type AS outlet_type,
        v.categ_id AS categ_id, toDecimal128(0, 2) AS gm, toDecimal128(v.total / v.days, 2) AS avg_daily,
        toDecimal128(v.month_end, 2) AS avg_me, toUInt16(v.days) AS sd
    FROM
    (
        SELECT
            s.month AS month, s.company_id AS company_id, s.warehouse_id AS warehouse_id,
            if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type, s.categ_id AS categ_id,
            sum(s.stock_value_cost) AS total, sumIf(s.stock_value_cost, s.day = d.last_day) AS month_end,
            any(d.days) AS days
        FROM
        (
            SELECT toStartOfMonth(day) AS month, day, company_id, warehouse_id, categ_id, stock_value_cost
            FROM efn_dwh.agg_sm_stock_daily FINAL
        ) AS s
        INNER JOIN
        (
            SELECT toStartOfMonth(day) AS month, warehouse_id, uniqExact(day) AS days, max(day) AS last_day
            FROM efn_dwh.agg_sm_stock_daily FINAL
            GROUP BY month, warehouse_id
        ) AS d ON d.month = s.month AND d.warehouse_id = s.warehouse_id
        INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
            ON w.warehouse_id = s.warehouse_id AND w.company_id = s.company_id
        GROUP BY s.month, s.company_id, s.warehouse_id, outlet_type, s.categ_id
    ) AS v
) AS u
LEFT JOIN efn_dwh.v_sm_category AS c ON c.categ_id = u.categ_id
GROUP BY u.month, u.company_id, u.warehouse_id, u.outlet_type, u.categ_id, category;

-- Waste (scrap selesai, G32) nilai HPP. Kepala toko melihat tokonya (L-203). Pembagi Waste % = net_sales tanpa PPN
-- (atau cogs, waste.basis) di aplikasi.
CREATE OR REPLACE VIEW efn_dwh.v_sm_waste_daily AS
SELECT
    toDate(s.date_done) AS day,
    s.company_id AS company_id,
    s.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    s.categ_id AS categ_id,
    if(c.category = '', 'Tanpa kategori', c.category) AS category,
    s.reason_id AS reason_id,
    if(r.name = '', 'Tanpa alasan', r.name) AS reason_name,
    count() AS scraps,
    sum(s.qty) AS waste_qty,
    sum(s.waste_value) AS waste_value_cost
FROM
(
    SELECT company_id, warehouse_id, categ_id, reason_id, qty, waste_value, date_done
    FROM efn_dwh.fact_scrap FINAL
    WHERE is_done = 1
) AS s
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = s.warehouse_id AND w.company_id = s.company_id
LEFT JOIN efn_dwh.v_sm_category AS c ON c.categ_id = s.categ_id
LEFT JOIN (SELECT reason_id, name FROM efn_dwh.dim_scrap_reason FINAL) AS r ON r.reason_id = s.reason_id
GROUP BY day, s.company_id, s.warehouse_id, outlet_type, s.categ_id, category, s.reason_id, reason_name;

-- Akurasi forecast (G44) per minggu x warehouse x produk; WAPE/bias diagregasi ulang dari jumlah mentah di aplikasi.
-- SMD tidak punya baris legacy (L-196).
CREATE OR REPLACE VIEW efn_dwh.v_sm_forecast_accuracy_weekly AS
SELECT
    a.week_start AS week_start,
    a.company_id AS company_id,
    a.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    a.product_id AS product_id,
    p.categ_id AS categ_id,
    a.days AS days,
    a.days_with_sales AS days_with_sales,
    a.forecast_qty AS forecast_qty,
    a.actual_qty AS actual_qty,
    a.abs_error AS abs_error
FROM
(
    SELECT week_start, company_id, warehouse_id, product_id, days, days_with_sales, forecast_qty, actual_qty, abs_error
    FROM efn_dwh.fact_forecast_accuracy FINAL
) AS a
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = a.warehouse_id AND w.company_id = a.company_id
LEFT JOIN (SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = a.product_id;

-- OOS per produk (definisi dari dim_sm_kpi_config, L-200/L-201): universe = SKU ber-orderpoint (opsi: + terjual 28 hari),
-- DC hanya bila oos.include_dc; habis = stok (on_hand|free) <= oos.threshold_qty pada snapshot akhir hari.
CREATE OR REPLACE VIEW efn_dwh.v_sm_oos_product_daily AS
SELECT
    a.day AS day,
    a.company_id AS company_id,
    a.warehouse_id AS warehouse_id,
    a.outlet_type AS outlet_type,
    a.product_id AS product_id,
    p.template_id AS product_tmpl_id,
    a.categ_id AS categ_id,
    a.in_univ AS in_universe,
    toUInt8(a.in_univ = 1 AND a.basis_qty <= a.threshold) AS oos,
    a.below AS below_min,
    a.on_hand AS on_hand_qty
FROM
(
    SELECT
        s.day AS day, s.company_id AS company_id, s.warehouse_id AS warehouse_id,
        if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type, s.product_id AS product_id, s.categ_id AS categ_id,
        toUInt8(s.tracked = 1 AND (k.oos_universe != 'orderpoint_sold_28d' OR s.sold_28d = 1)
                AND (w.is_dc = 0 OR k.oos_include_dc = 1)) AS in_univ,
        if(k.oos_basis = 'free', toFloat64(s.free_qty), toFloat64(s.on_hand_qty)) AS basis_qty,
        k.oos_threshold_qty AS threshold,
        toUInt8(s.tracked = 1 AND s.on_hand_qty <= s.min_qty) AS below,
        s.on_hand_qty AS on_hand
    FROM
    (
        SELECT day, company_id, warehouse_id, product_id, categ_id, tracked, sold_28d, on_hand_qty, free_qty, min_qty
        FROM efn_dwh.agg_sm_stock_daily FINAL
    ) AS s
    INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
        ON w.warehouse_id = s.warehouse_id AND w.company_id = s.company_id
    INNER JOIN efn_dwh.v_sm_kpi_settings AS k ON k.company_id = s.company_id
) AS a
LEFT JOIN (SELECT product_id, template_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = a.product_id;

CREATE OR REPLACE VIEW efn_dwh.v_sm_stock_daily AS
SELECT
    o.day AS day,
    o.company_id AS company_id,
    o.warehouse_id AS warehouse_id,
    o.outlet_type AS outlet_type,
    o.categ_id AS categ_id,
    sum(o.in_universe) AS sku_universe,
    sum(o.oos) AS sku_oos,
    sum(o.below_min) AS sku_below_min,
    sum(o.on_hand_qty) AS on_hand_qty
FROM efn_dwh.v_sm_oos_product_daily AS o
GROUP BY o.day, o.company_id, o.warehouse_id, o.outlet_type, o.categ_id;

-- Nilai persediaan HPP + bucket umur di gudang (in_date) dan sisa umur lot (expiration_date). Tertutup bagi store (L-203).
CREATE OR REPLACE VIEW efn_dwh.v_sm_stock_value_daily AS
SELECT
    s.day AS day,
    s.company_id AS company_id,
    s.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    s.categ_id AS categ_id,
    sum(s.stock_value_cost) AS stock_value_cost,
    sum(s.value_age_0_30) AS value_age_0_30,
    sum(s.value_age_31_60) AS value_age_31_60,
    sum(s.value_age_61_90) AS value_age_61_90,
    sum(s.value_age_91p) AS value_age_91p,
    sum(s.value_exp_expired) AS value_exp_expired,
    sum(s.value_exp_0_7) AS value_exp_0_7,
    sum(s.value_exp_8_30) AS value_exp_8_30,
    sum(s.value_exp_31_90) AS value_exp_31_90,
    sum(s.value_exp_91p) AS value_exp_91p,
    sum(s.value_no_lot) AS value_no_lot
FROM
(
    SELECT day, company_id, warehouse_id, categ_id, stock_value_cost, value_age_0_30, value_age_31_60, value_age_61_90,
           value_age_91p, value_exp_expired, value_exp_0_7, value_exp_8_30, value_exp_31_90, value_exp_91p, value_no_lot
    FROM efn_dwh.agg_sm_stock_daily FINAL
) AS s
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = s.warehouse_id AND w.company_id = s.company_id
GROUP BY s.day, s.company_id, s.warehouse_id, outlet_type, s.categ_id;

-- Stok kritis (ST-3) pada hari agregat terakhir. Habis = is_out (oos.basis/oos.threshold_qty); kritis menurut
-- critical.rule: min_or_cover (default: <= min ATAU cakupan < N hari), cover_only, min_only. Rata-rata jual harian =
-- Σ qty POS jendela critical.sales_window_days (sebelum hari ini WIB) / jendela.
CREATE OR REPLACE VIEW efn_dwh.v_sm_stock_critical AS
SELECT
    x.snapshot_ts AS snapshot_ts,
    x.company_id AS company_id,
    x.warehouse_id AS warehouse_id,
    x.outlet_type AS outlet_type,
    x.product_id AS product_id,
    p.template_id AS product_tmpl_id,
    p.default_code AS default_code,
    p.name AS product_name,
    x.categ_id AS categ_id,
    x.on_hand AS on_hand_qty,
    x.free AS free_qty,
    x.min_q AS min_qty,
    x.max_q AS max_qty,
    x.below AS below_min,
    x.avg_sales AS avg_daily_sales,
    x.cover AS days_of_cover,
    x.out_flag AS is_out,
    toUInt8(multiIf(x.rule = 'min_only', x.below = 1,
                    x.rule = 'cover_only', x.cover_low = 1,
                    x.below = 1 OR x.cover_low = 1)) AS is_critical,
    r.proposal AS open_proposal_id
FROM
(
    SELECT
        s.snapshot_ts AS snapshot_ts, s.company_id AS company_id, s.warehouse_id AS warehouse_id,
        if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type, s.product_id AS product_id, s.categ_id AS categ_id,
        s.on_hand_qty AS on_hand, s.free_qty AS free, s.min_qty AS min_q, s.max_qty AS max_q,
        toUInt8(s.tracked = 1 AND s.on_hand_qty <= s.min_qty) AS below,
        round(d.qty / k.critical_sales_window_days, 3) AS avg_sales,
        if(d.qty > 0, round(toFloat64(s.on_hand_qty) / (d.qty / k.critical_sales_window_days), 1), NULL) AS cover,
        toUInt8(d.qty > 0 AND toFloat64(s.on_hand_qty) / (d.qty / k.critical_sales_window_days) < k.critical_cover_days)
            AS cover_low,
        toUInt8(if(k.oos_basis = 'free', toFloat64(s.free_qty), toFloat64(s.on_hand_qty)) <= k.oos_threshold_qty)
            AS out_flag,
        k.critical_rule AS rule
    FROM
    (
        SELECT snapshot_ts, company_id, warehouse_id, product_id, categ_id, tracked, on_hand_qty, free_qty, min_qty, max_qty
        FROM efn_dwh.agg_sm_stock_daily FINAL
        WHERE day = (SELECT max(day) FROM efn_dwh.agg_sm_stock_daily)
    ) AS s
    INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
        ON w.warehouse_id = s.warehouse_id AND w.company_id = s.company_id
    INNER JOIN efn_dwh.v_sm_kpi_settings AS k ON k.company_id = s.company_id
    LEFT JOIN
    (
        SELECT f.company_id AS company_id, o.warehouse_id AS warehouse_id, f.product_id AS product_id,
               toFloat64(sumIf(f.qty, f.day >= toDate(now(), 'Asia/Jakarta') - kk.critical_sales_window_days)) AS qty
        FROM
        (
            SELECT company_id, store_id, product_id, toDate(order_date) AS day, qty
            FROM efn_dwh.fact_sales_line FINAL
            WHERE source = 'pos' AND line_kind = 'product' AND is_refund = 0
              AND order_date >= now() - INTERVAL 92 DAY AND toDate(order_date) < toDate(now(), 'Asia/Jakarta')
        ) AS f
        INNER JOIN
        (
            SELECT store_id, assumeNotNull(warehouse_id) AS warehouse_id
            FROM efn_dwh.dim_outlet FINAL WHERE isNotNull(warehouse_id)
        ) AS o ON o.store_id = f.store_id
        INNER JOIN efn_dwh.v_sm_kpi_settings AS kk ON kk.company_id = f.company_id
        GROUP BY f.company_id, o.warehouse_id, f.product_id
    ) AS d ON d.warehouse_id = s.warehouse_id AND d.product_id = s.product_id
) AS x
LEFT JOIN (SELECT product_id, template_id, default_code, name FROM efn_dwh.dim_product FINAL) AS p
    ON p.product_id = x.product_id
LEFT JOIN
(
    SELECT warehouse_id, product_id, max(proposal_id) AS proposal
    FROM efn_dwh.v_sm_replenishment_current
    WHERE state IN ('proposed', 'approved') AND excluded = 0 AND delivery_date >= toDate(now(), 'Asia/Jakarta')
    GROUP BY warehouse_id, product_id
) AS r ON r.warehouse_id = x.warehouse_id AND r.product_id = x.product_id;

CREATE OR REPLACE VIEW efn_dwh.v_sm_replenishment AS
SELECT
    c.proposal_id AS proposal_id,
    c.company_id AS company_id,
    c.warehouse_id AS warehouse_id,
    c.outlet_type AS outlet_type,
    c.supply_warehouse_id AS supply_warehouse_id,
    c.delivery_date AS delivery_date,
    c.cutoff_at AS cutoff_at,
    c.state AS state,
    c.approved_at AS approved_at,
    c.picking_id AS picking_id,
    count() AS line_count,
    sum(c.excluded) AS lines_excluded,
    countIf(c.qty_approved != c.qty_proposed) AS lines_changed,
    sum(c.qty_proposed) AS qty_proposed_total,
    sum(c.qty_approved) AS qty_approved_total
FROM efn_dwh.v_sm_replenishment_current AS c
GROUP BY c.proposal_id, c.company_id, c.warehouse_id, c.outlet_type, c.supply_warehouse_id, c.delivery_date,
         c.cutoff_at, c.state, c.approved_at, c.picking_id;

CREATE OR REPLACE VIEW efn_dwh.v_sm_replenishment_line AS
SELECT
    c.line_id AS line_id,
    c.proposal_id AS proposal_id,
    c.company_id AS company_id,
    c.warehouse_id AS warehouse_id,
    c.outlet_type AS outlet_type,
    c.delivery_date AS delivery_date,
    c.state AS state,
    c.product_id AS product_id,
    c.categ_id AS categ_id,
    c.qty_proposed AS qty_proposed,
    c.qty_approved AS qty_approved,
    c.excluded AS excluded,
    c.reason_codes AS reason_codes,
    c.change_reason AS change_reason
FROM efn_dwh.v_sm_replenishment_current AS c;

-- Bias manusia per minggu (Senin WIB): qty_final = Σ qty_after, bias_qty = qty_final - qty_system.
CREATE OR REPLACE VIEW efn_dwh.v_sm_replenishment_bias_weekly AS
SELECT
    toMonday(toDate(b.created_at, 'Asia/Jakarta')) AS week_start,
    b.company_id AS company_id,
    b.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    b.kind AS kind,
    count() AS entries,
    sum(b.qty_system) AS qty_system,
    sum(b.qty_after) AS qty_final,
    sum(b.qty_after) - sum(b.qty_system) AS bias_qty
FROM
(
    SELECT log_id, company_id, warehouse_id, kind, qty_system, qty_after, created_at
    FROM efn_dwh.fact_replenishment_bias
    ORDER BY log_id
    LIMIT 1 BY log_id
) AS b
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = b.warehouse_id AND w.company_id = b.company_id
GROUP BY week_start, b.company_id, b.warehouse_id, outlet_type, b.kind;

-- Fill rate DC (L-194/L-201): qty_basis = qty_shipped (default) atau qty_received (fill.basis); on_time dengan
-- toleransi fill.grace_days (berbasis tanggal kirim terakhir). Rasio qty/baris dihitung aplikasi (fill.level).
CREATE OR REPLACE VIEW efn_dwh.v_sm_dc_fill_rate AS
SELECT
    f.line_id AS line_id,
    f.proposal_id AS proposal_id,
    f.company_id AS company_id,
    f.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    f.dc_warehouse_id AS dc_warehouse_id,
    f.delivery_date AS delivery_date,
    f.product_id AS product_id,
    p.categ_id AS categ_id,
    f.qty_approved AS qty_approved,
    f.qty_shipped AS qty_shipped,
    f.qty_received AS qty_received,
    if(k.fill_basis = 'received', f.qty_received, f.qty_shipped) AS qty_basis,
    toUInt8(f.on_time_raw = 1
            OR (k.fill_grace_days > 0 AND f.qty_shipped >= f.qty_approved AND isNotNull(f.last_done_at)
                AND toDate(assumeNotNull(f.last_done_at), 'Asia/Jakarta') <= f.delivery_date + k.fill_grace_days))
        AS on_time,
    f.picking_shared AS picking_shared
FROM
(
    SELECT line_id, proposal_id, company_id, warehouse_id, dc_warehouse_id, delivery_date, product_id, qty_approved,
           qty_shipped, qty_received, last_done_at, on_time AS on_time_raw, picking_shared
    FROM efn_dwh.fact_dc_fill_rate FINAL
) AS f
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = f.warehouse_id AND w.company_id = f.company_id
INNER JOIN efn_dwh.v_sm_kpi_settings AS k ON k.company_id = f.company_id
LEFT JOIN (SELECT product_id, categ_id FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = f.product_id;

-- PO supplier DC. is_late = jadwal terima lewat, belum diterima penuh, PO terkonfirmasi.
CREATE OR REPLACE VIEW efn_dwh.v_sm_dc_purchase AS
SELECT
    l.line_id AS line_id,
    l.order_id AS order_id,
    l.company_id AS company_id,
    l.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    l.supplier_id AS supplier_id,
    if(s.name = '', concat('Supplier #', toString(l.supplier_id)), s.name) AS supplier_name,
    l.product_id AS product_id,
    l.po_state AS po_state,
    l.date_order AS date_order,
    l.date_planned AS date_planned,
    l.product_qty AS product_qty,
    l.qty_received AS qty_received,
    l.price_subtotal AS price_subtotal,
    l.from_orderpoint AS from_orderpoint,
    toUInt8(l.po_state = 'purchase'
            AND ifNull(l.date_planned, toDateTime64('2100-01-01 00:00:00', 0, 'UTC')) < now64(0)
            AND l.qty_received < l.product_qty) AS is_late
FROM
(
    SELECT line_id, order_id, company_id, warehouse_id, supplier_id, product_id, po_state, date_order, date_planned,
           product_qty, qty_received, price_subtotal, from_orderpoint
    FROM efn_dwh.fact_dc_purchase_line FINAL
    ORDER BY version_ts DESC
    LIMIT 1 BY line_id
) AS l
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = l.warehouse_id AND w.company_id = l.company_id
LEFT JOIN (SELECT supplier_id, name FROM efn_dwh.dim_sm_supplier FINAL) AS s ON s.supplier_id = l.supplier_id;

-- Markdown per lot (#114). effective_discount_pct = discount_pct bila diisi, selain itu dari harga markdown vs harga jual
-- template (pendekatan lst_price Odoo; atribut varian tidak dihitung).
CREATE OR REPLACE VIEW efn_dwh.v_sm_markdown AS
SELECT
    md.markdown_id AS markdown_id,
    md.company_id AS company_id,
    md.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    md.lot_id AS lot_id,
    md.product_id AS product_id,
    md.alert_id AS alert_id,
    md.state AS state,
    md.price AS price,
    md.discount_pct AS discount_pct,
    multiIf(ifNull(toFloat64(md.discount_pct), 0.0) > 0, toFloat64(md.discount_pct),
            isNotNull(md.price) AND p.list_price > 0,
            round((1 - toFloat64(md.price) / toFloat64(p.list_price)) * 100, 2),
            NULL) AS effective_discount_pct,
    md.date_from AS date_from,
    md.date_to AS date_to,
    md.qty_cap AS qty_cap,
    md.qty_sold AS qty_sold,
    md.notify_members AS notify_members
FROM
(
    SELECT markdown_id, company_id, warehouse_id, lot_id, product_id, alert_id, state, price, discount_pct, date_from,
           date_to, qty_cap, qty_sold, notify_members
    FROM efn_dwh.fact_lot_markdown FINAL
    ORDER BY write_date DESC
    LIMIT 1 BY markdown_id
) AS md
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = md.warehouse_id AND w.company_id = md.company_id
LEFT JOIN (SELECT product_id, list_price FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = md.product_id;

-- Alert near-expiry terbuka (#114). value_cost = qty x HPP satuan snapshot terakhir (L-203: store melihat tokonya);
-- value_retail = qty x harga jual template. display_status: kedaluwarsa | markdown_aktif | markdown_sekarang | pantau.
CREATE OR REPLACE VIEW efn_dwh.v_sm_lot_expiry AS
SELECT
    e.alert_id AS alert_id,
    e.company_id AS company_id,
    e.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    e.lot_id AS lot_id,
    e.lot_name AS lot_name,
    e.product_id AS product_id,
    p.template_id AS product_tmpl_id,
    p.name AS product_name,
    e.categ_id AS categ_id,
    e.stage AS stage,
    e.state AS state,
    e.detected_date AS detected_date,
    e.last_run_date AS last_run_date,
    e.days_left AS days_left,
    e.expiration_date AS expiration_date,
    e.qty AS qty,
    toDecimal128(toDecimal128(e.qty, 3) * toDecimal128(ifNull(c.unit_cost, toDecimal64(0, 4)), 4), 2) AS value_cost,
    toDecimal128(toDecimal128(e.qty, 3) * toDecimal128(p.list_price, 2), 2) AS value_retail,
    m.md_id AS markdown_id,
    m.md_state AS markdown_state,
    m.md_pct AS markdown_effective_pct,
    m.md_price AS markdown_price,
    m.md_date_to AS markdown_date_to,
    multiIf(e.stage = 'expired', 'kedaluwarsa', m.md_id != 0, 'markdown_aktif', e.stage = 'markdown', 'markdown_sekarang',
            'pantau') AS display_status
FROM
(
    SELECT alert_id, company_id, warehouse_id, lot_id, lot_name, product_id, categ_id, stage, state, detected_date,
           last_run_date, days_left, expiration_date, qty
    FROM efn_dwh.fact_lot_expiry FINAL
    WHERE state IN ('open', 'actioned')
) AS e
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = e.warehouse_id AND w.company_id = e.company_id
LEFT JOIN (SELECT product_id, template_id, name, list_price FROM efn_dwh.dim_product FINAL) AS p
    ON p.product_id = e.product_id
LEFT JOIN efn_dwh.v_sm_unit_cost AS c ON c.company_id = e.company_id AND c.product_id = e.product_id
LEFT JOIN
(
    SELECT
        mk.alert_id AS alert_id,
        argMax(mk.markdown_id, mk.markdown_id) AS md_id,
        argMax(toString(mk.state), mk.markdown_id) AS md_state,
        argMax(mk.effective_discount_pct, mk.markdown_id) AS md_pct,
        argMax(mk.price, mk.markdown_id) AS md_price,
        argMax(toNullable(mk.date_to), mk.markdown_id) AS md_date_to
    FROM efn_dwh.v_sm_markdown AS mk
    WHERE mk.state = 'active' AND mk.alert_id != 0
    GROUP BY mk.alert_id
) AS m ON m.alert_id = e.alert_id;

CREATE OR REPLACE VIEW efn_dwh.v_sm_expiry_daily AS
SELECT
    a.day AS day,
    a.company_id AS company_id,
    a.warehouse_id AS warehouse_id,
    if(w.is_dc = 1, 'dc', 'outlet') AS outlet_type,
    a.categ_id AS categ_id,
    a.stage AS stage,
    a.lots AS lots,
    a.qty AS qty,
    a.value_cost AS value_cost
FROM
(
    SELECT day, company_id, warehouse_id, categ_id, stage, lots, qty, value_cost
    FROM efn_dwh.agg_sm_expiry_daily FINAL
) AS a
INNER JOIN (SELECT warehouse_id, company_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w
    ON w.warehouse_id = a.warehouse_id AND w.company_id = a.company_id;

-- Harga rak vs Price Hunter 30 hari (baris tingkat PT, warehouse_id = 0). Pencocokan laporan -> produk = aturan
-- v_price_index (04-fase1-marts.sql: SKU, lalu nama dinormalisasi), ditulis ulang di sini tanpa agregasi POS 30 hari.
-- our_price = list_price template (basis termasuk/tanpa PPN SMD belum terverifikasi, §28.13.11).
CREATE OR REPLACE VIEW efn_dwh.v_sm_price_index AS
SELECT
    p.company_id AS company_id,
    toUInt32(0) AS warehouse_id,
    'none' AS outlet_type,
    p.product_id AS product_id,
    p.template_id AS product_tmpl_id,
    p.default_code AS default_code,
    p.name AS product_name,
    p.categ_id AS categ_id,
    p.list_price AS our_price,
    m.med AS market_median_30d,
    m.mn AS market_min_30d,
    m.mx AS market_max_30d,
    m.n AS sample_n_30d,
    m.competitors AS competitors_n_30d,
    m.last_day AS last_sample_date,
    if(isNull(m.med) OR m.med = 0, NULL, round((toFloat64(p.list_price) - m.med) / m.med * 100, 1)) AS diff_pct
FROM
(
    SELECT product_id, template_id, default_code, name, categ_id, list_price, assumeNotNull(company_id) AS company_id
    FROM efn_dwh.dim_product FINAL
    WHERE isNotNull(company_id) AND active = 1 AND sale_ok = 1
) AS p
INNER JOIN (SELECT DISTINCT company_id FROM efn_dwh.dim_sm_warehouse FINAL) AS co ON co.company_id = p.company_id
LEFT JOIN
(
    SELECT
        pk.product_id AS product_id,
        toNullable(median(toFloat64(c.price))) AS med,
        toNullable(toFloat64(min(c.price))) AS mn,
        toNullable(toFloat64(max(c.price))) AS mx,
        count() AS n,
        uniqExact(c.competitor_name) AS competitors,
        toNullable(max(toDate(c.reported_at))) AS last_day
    FROM
    (
        SELECT competitor_name, price, reported_at,
               if(ifNull(product_sku, '') != '', concat('sku:', product_sku),
                  concat('name:', lower(trimBoth(replaceRegexpAll(product_name, '\\s+', ' '))))) AS match_key
        FROM efn_dwh.competitor_prices FINAL
        WHERE status = 'valid' AND reported_at >= now() - INTERVAL 30 DAY
    ) AS c
    INNER JOIN
    (
        SELECT product_id,
               arrayJoin(arrayFilter(k -> k NOT IN ('sku:', 'name:'),
                   [concat('sku:', default_code), concat('name:', lower(trimBoth(replaceRegexpAll(name, '\\s+', ' '))))]))
                   AS match_key
        FROM efn_dwh.dim_product FINAL
        WHERE active = 1 AND sale_ok = 1 AND isNotNull(company_id)
    ) AS pk ON pk.match_key = c.match_key
    GROUP BY pk.product_id
) AS m ON m.product_id = p.product_id;

-- Cakupan data harga per kategori ("tersedia untuk 38 dari 210 SKU").
CREATE OR REPLACE VIEW efn_dwh.v_sm_price_coverage AS
SELECT
    i.company_id AS company_id,
    toUInt32(0) AS warehouse_id,
    'none' AS outlet_type,
    i.categ_id AS categ_id,
    count() AS sku_active,
    countIf(i.sample_n_30d >= 1) AS sku_with_sample,
    countIf(i.sample_n_30d >= 3) AS sku_with_sample_3
FROM efn_dwh.v_sm_price_index AS i
GROUP BY i.company_id, i.categ_id;

-- Sinyal "dilihat tapi terlalu mahal" 30 hari (#117): Σ perangkat-hari (Q-SM41); hitungan NULL bila viewers_30d < 10
-- (k-anonim Q-SM17, tidak configurable).
CREATE OR REPLACE VIEW efn_dwh.v_sm_price_signal AS
SELECT
    v.company_id AS company_id,
    toUInt32(0) AS warehouse_id,
    'none' AS outlet_type,
    v.product_id AS product_id,
    i.categ_id AS categ_id,
    if(v.viewers_n < 10, NULL, v.viewers_n) AS viewers_30d,
    if(v.viewers_n < 10, NULL, v.views_n) AS views_30d,
    if(v.viewers_n < 10, NULL, v.carts_n) AS cart_adds_30d,
    if(v.viewers_n < 10, NULL, v.purchases_n) AS purchases_30d,
    i.our_price AS our_price,
    i.market_median_30d AS market_median_30d,
    i.diff_pct AS diff_pct
FROM
(
    SELECT co.company_id AS company_id, a.product_id AS product_id, sum(a.viewers) AS viewers_n, sum(a.views) AS views_n,
           sum(a.cart_adds) AS carts_n, sum(a.purchases) AS purchases_n
    FROM
    (
        SELECT company_code, product_id, viewers, views, cart_adds, purchases
        FROM efn_dwh.agg_visitor_product_daily FINAL
        WHERE day >= toDate(now(), 'Asia/Jakarta') - 30
    ) AS a
    INNER JOIN (SELECT DISTINCT company_id, toString(company_code) AS company_code FROM efn_dwh.dim_sm_warehouse FINAL) AS co
        ON co.company_code = a.company_code
    GROUP BY co.company_id, a.product_id
) AS v
INNER JOIN efn_dwh.v_sm_price_index AS i ON i.company_id = v.company_id AND i.product_id = v.product_id;

-- Funnel visitor per hari x warehouse x kanal (#117). Pemetaan (channel_code, outlet_code) -> warehouse lewat
-- dim_online_outlet (L-190); tanpa/tak dikenal/nonaktif/PT lain -> warehouse_id 0 (tingkat PT, tidak terlihat store).
-- Hitungan = Σ subjek-hari per outlet (label jujur, Q-SM41).
CREATE OR REPLACE VIEW efn_dwh.v_sm_visitor_funnel_daily AS
SELECT
    x.day AS day,
    x.company_id AS company_id,
    x.warehouse_id AS warehouse_id,
    if(x.warehouse_id = 0, 'none', if(w.is_dc = 1, 'dc', 'outlet')) AS outlet_type,
    x.channel AS channel,
    x.channel_code AS channel_code,
    sum(x.subjects_n) AS subjects,
    sum(x.viewers_n) AS viewers,
    sum(x.carters_n) AS carters,
    sum(x.checkout_n) AS checkout_starters,
    sum(x.purchasers_n) AS purchasers,
    sum(x.no_result_n) AS searchers_no_result
FROM
(
    SELECT
        f.day AS day, co.company_id AS company_id,
        if(m.warehouse_id != 0 AND m.active = 1 AND m.company_id = co.company_id, m.warehouse_id, toUInt32(0))
            AS warehouse_id,
        f.channel AS channel, f.channel_code AS channel_code, f.subjects AS subjects_n, f.viewers AS viewers_n,
        f.carters AS carters_n, f.checkout_starters AS checkout_n, f.purchasers AS purchasers_n,
        f.searchers_no_result AS no_result_n
    FROM
    (
        SELECT day, toString(company_code) AS company_code, channel, toString(channel_code) AS channel_code,
               toString(outlet_code) AS outlet_code, subjects, viewers, carters, checkout_starters, purchasers,
               searchers_no_result
        FROM efn_dwh.agg_visitor_funnel_daily FINAL
    ) AS f
    INNER JOIN (SELECT DISTINCT company_id, toString(company_code) AS company_code FROM efn_dwh.dim_sm_warehouse FINAL) AS co
        ON co.company_code = f.company_code
    LEFT JOIN
    (
        SELECT toString(channel_code) AS channel_code, outlet_code, company_id, warehouse_id, active
        FROM efn_dwh.dim_online_outlet FINAL
    ) AS m ON m.channel_code = f.channel_code AND m.outlet_code = f.outlet_code
) AS x
LEFT JOIN (SELECT warehouse_id, is_dc FROM efn_dwh.dim_sm_warehouse FINAL) AS w ON w.warehouse_id = x.warehouse_id
GROUP BY x.day, x.company_id, x.warehouse_id, outlet_type, x.channel, x.channel_code;

-- Istilah pencarian (#117, sudah k-anonim di agregat). Tingkat PT.
CREATE OR REPLACE VIEW efn_dwh.v_sm_search_terms AS
SELECT
    s.day AS day,
    co.company_id AS company_id,
    toUInt32(0) AS warehouse_id,
    'none' AS outlet_type,
    s.channel AS channel,
    s.channel_code AS channel_code,
    s.query AS query,
    s.searches AS searches,
    s.no_result AS no_result,
    s.subjects AS subjects
FROM
(
    SELECT day, toString(company_code) AS company_code, channel, channel_code, query, searches, no_result, subjects
    FROM efn_dwh.agg_visitor_search_daily FINAL
) AS s
INNER JOIN (SELECT DISTINCT company_id, toString(company_code) AS company_code FROM efn_dwh.dim_sm_warehouse FINAL) AS co
    ON co.company_code = s.company_code;

-- Segmen (#117): run terakhir per hari WIB x segmen.
CREATE OR REPLACE VIEW efn_dwh.v_sm_segment_daily AS
SELECT
    toDate(r.generated_at, 'Asia/Jakarta') AS day,
    r.company_id AS company_id,
    toUInt32(0) AS warehouse_id,
    'none' AS outlet_type,
    r.segment_code AS segment_code,
    argMax(r.member_count, r.generated_at) AS member_count,
    argMax(r.run_id, r.generated_at) AS run_id,
    argMax(r.campaign_id, r.generated_at) AS campaign_id,
    argMax(toString(r.campaign_state), r.generated_at) AS campaign_state
FROM
(
    SELECT company_id, run_id, segment_code, member_count, generated_at, campaign_id, campaign_state
    FROM efn_dwh.fact_sm_segment_run FINAL
) AS r
INNER JOIN (SELECT DISTINCT company_id FROM efn_dwh.dim_sm_warehouse FINAL) AS co ON co.company_id = r.company_id
GROUP BY day, r.company_id, r.segment_code;

CREATE OR REPLACE VIEW efn_dwh.v_sm_campaign_result AS
SELECT
    f.campaign_id AS campaign_id,
    f.company_id AS company_id,
    toUInt32(0) AS warehouse_id,
    'none' AS outlet_type,
    f.name AS name,
    f.kind AS kind,
    f.origin AS origin,
    f.state AS state,
    f.voucher_mode AS voucher_mode,
    f.channels AS channels,
    f.date_from AS date_from,
    f.date_to AS date_to,
    f.approved_at AS approved_at,
    f.sent_at AS sent_at,
    f.approval_rev AS approval_rev,
    f.budget_idr AS budget_idr,
    f.segment_code AS segment_code,
    f.segment_member_count AS segment_member_count,
    f.delivered AS delivered,
    f.skipped AS skipped,
    f.push_sent AS push_sent,
    f.email_queued AS email_queued,
    f.wa_queued AS wa_queued,
    f.stop_reason AS stop_reason,
    f.vouchers_issued AS vouchers_issued,
    f.vouchers_used AS vouchers_used,
    f.orders_with_voucher AS orders_with_voucher,
    f.discount_idr AS discount_idr,
    f.revenue_with_voucher_idr AS revenue_with_voucher_idr,
    f.computed_at AS computed_at
FROM
(
    SELECT campaign_id, company_id, name, kind, origin, state, voucher_mode, channels, date_from, date_to, approved_at,
           sent_at, approval_rev, budget_idr, segment_code, segment_member_count, delivered, skipped, push_sent,
           email_queued, wa_queued, stop_reason, vouchers_issued, vouchers_used, orders_with_voucher, discount_idr,
           revenue_with_voucher_idr, computed_at
    FROM efn_dwh.fact_campaign_result FINAL
) AS f
INNER JOIN (SELECT DISTINCT company_id FROM efn_dwh.dim_sm_warehouse FINAL) AS co ON co.company_id = f.company_id;

-- Kesegaran per mart (§28.13.10). data_as_of = run_ts siklus ok terakhir job; NULL bila tidak pernah ok ("Tidak
-- diketahui"). Ambang efektif dibaca aplikasi dari v_sm_kpi_config fresh.<kelas>.* (kelas F: tanpa PT).
-- mart `waste` = job sm_scraps (G47, scrap PT SM tanpa efn_ck_production; kontrak menyebut waste_scraps G32).
CREATE OR REPLACE VIEW efn_dwh.v_sm_freshness AS
SELECT
    m.mart AS mart,
    m.freshness_class AS freshness_class,
    if(r.ok_runs = 0, NULL, toNullable(r.last_ok)) AS data_as_of,
    multiIf(m.mart IN ('stock', 'stock_value'), src.stock_ts, m.mart = 'expiry', src.expiry_ts,
            m.mart = 'price_hunter', src.price_ts, NULL) AS source_as_of,
    if(r.runs = 0, NULL, toNullable(r.last_run)) AS last_run_at,
    if(r.runs = 0, NULL, toNullable(toString(r.last_status))) AS last_status
FROM
(
    SELECT tupleElement(t, 1) AS mart, tupleElement(t, 2) AS freshness_class, tupleElement(t, 3) AS job
    FROM
    (
        SELECT arrayJoin([
            ('replenishment', 'near_live', 'sm_replenishment'),
            ('dc_purchase', 'near_live', 'sm_dc_purchase'),
            ('stock', 'stock', 'sm_stock_agg'),
            ('sales', 'daily', 'sales_fact_pos'),
            ('waste', 'daily', 'sm_scraps'),
            ('forecast_accuracy', 'daily', 'forecast_accuracy'),
            ('dc_fill_rate', 'daily', 'sm_dc_fill_rate'),
            ('stock_value', 'daily', 'sm_stock_agg'),
            ('expiry', 'daily', 'sm_expiry'),
            ('price_hunter', 'daily', 'competitor_prices'),
            ('visitor', 'daily', 'behavior_events'),
            ('campaign', 'daily', 'sm_campaign')
        ]) AS t
    )
) AS m
LEFT JOIN
(
    SELECT toString(job) AS job, count() AS runs, countIf(status = 'ok') AS ok_runs, maxIf(run_ts, status = 'ok') AS last_ok,
           max(run_ts) AS last_run, argMax(status, run_ts) AS last_status
    FROM efn_dwh.etl_job_run
    WHERE job IN ('sm_replenishment', 'sm_dc_purchase', 'sm_stock_agg', 'sales_fact_pos', 'sm_scraps',
                  'forecast_accuracy', 'sm_dc_fill_rate', 'sm_expiry', 'competitor_prices', 'behavior_events',
                  'sm_campaign')
    GROUP BY job
) AS r ON r.job = m.job
CROSS JOIN
(
    SELECT
        (SELECT if(count() = 0, NULL, toNullable(max(snapshot_ts))) FROM efn_dwh.agg_sm_stock_daily) AS stock_ts,
        (SELECT if(count() = 0, NULL, toNullable(toDateTime64(max(last_run_date), 6, 'UTC')))
           FROM efn_dwh.fact_lot_expiry) AS expiry_ts,
        (SELECT if(count() = 0, NULL, toNullable(toDateTime64(max(reported_at), 6, 'UTC')))
           FROM efn_dwh.competitor_prices WHERE status = 'valid') AS price_ts
) AS src;
