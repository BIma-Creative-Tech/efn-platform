-- Gelombang 50 (efn-data-ai-engineer): perilaku visitor supermarket (ADR-047 Proposed, docs/04 §28.6/§28.7/§28.10,
-- L-152..L-155, R-L13). Idempoten (CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh
-- setiap deploy setelah 01..15.
--
-- Arah data (ADR-003 tetap, L-153): BFF -> RabbitMQ `behavior.event` -> PG `behavior.event_raw` (staging 7 hari) -> ETL
-- 15 menit (services/etl/efn_etl/behavior.py, ETL_ENABLE_BEHAVIOR=false default) -> fact_visitor_event (TTL 90 hari)
-- -> agg_visitor_* harian (TTL 2 tahun, tanpa subject_key). TIDAK ada tulis BFF -> ClickHouse.
--
-- PDP / minimisasi (ADR-047 §3.2/§3.3):
-- - fact_visitor_event hanya memuat `subject_key` (HMAC-SHA256 dihitung BFF; ETL tidak memegang pepper), tanpa device ID
--   mentah, member_id, nama, HP, email, IP, user agent, GPS. `query` = istilah pencarian yang SUDAH disanitasi BFF
--   (NFKC, lowercase, <= 64, angka >= 6 digit & pola email -> [redacted]) dan disanitasi ulang ETL (defense in depth).
-- - agg_visitor_* tanpa subject_key; istilah pencarian hanya bila >= k subjek berbeda (ETL_BEHAVIOR_K_MIN, usulan 10,
--   Q-SM17). Agregat produk/funnel = hitungan tanpa pengenal.
-- - Erasure (L-154): ALTER DELETE per subject_key oleh job ETL (sumber: PG behavior.subject_erasure milik BFF). Agregat
--   tidak dihapus (anonim, ADR-047 §3.3).
-- - Retensi 90 hari / 2 tahun = ADR-047 (menunggu DPO Q-SM17). Ditulis literal di DDL (bukan env clickhouse-init) karena
--   pemilik clickhouse-init.sh = devsecops; ubah = ALTER TABLE ... MODIFY TTL (lihat README ETL bagian G50).

-- ============================================================== Fakta event (TTL 90 hari)
-- Satu baris per event (kunci dedup staging = (subject_key, event_id), docs/04 §28.6). Versi = ingested_at (waktu baris
-- masuk staging) -> re-run / overlap window idempoten. event_date = tanggal WIB dari received_at (waktu server, bukan
-- client_ts). Partisi harian -> TTL membuang partisi utuh tepat di hari ke-90 (ttl_only_drop_parts).
-- event_type: product_view|wishlist_add|wishlist_remove|cart_add|cart_remove|checkout_start|search|notify_me_request|
--             price_alert_set|page_view (V-01) + turunan server cart_abandon|purchase (ASUMSI G50, lihat laporan).
-- V-01 (keputusan pemilik proyek 23 Nov 2026, log kunjungan anonim tanpa consent):
--   consented = 1 event subjek ber-consent `tracking` (perilaku lama), 0 = kunjungan anonim (subject_key HMAC domain
--               `anon:` dari BFF, tidak pernah tertaut member). Baris lama = 1 (DEFAULT). Segmen/kampanye HANYA
--               consented = 1 (view v_seg_* di bawah); agregat agg_visitor_* dihitung dari SEMUA visitor.
--   page      = halaman event page_view (home|category|product|search|cart|checkout|promo|store|account|order|other),
--               '' untuk tipe lain.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_visitor_event
(
    event_id      UUID,
    subject_key   String,
    subject_kind  LowCardinality(String),           -- device|member
    company_code  LowCardinality(String),
    channel       LowCardinality(String),           -- web|app
    channel_code  LowCardinality(String),
    outlet_code   LowCardinality(String),           -- '' = tanpa outlet
    event_type    LowCardinality(String),
    product_id    UInt32,                           -- 0 = tanpa produk (search, checkout_start keranjang)
    duration_s    UInt16,
    qty           UInt16,
    query         String,                           -- hanya event search, '' selain itu
    result_count  Nullable(UInt32),
    stock_status  LowCardinality(String),           -- in_stock|low|out|'' (pengayaan server saat event)
    price_idr     Nullable(Int64),                  -- harga saat event (pengayaan server)
    consented     UInt8 DEFAULT 1,                  -- V-01: 1 = ber-consent, 0 = kunjungan anonim
    page          LowCardinality(String) DEFAULT '', -- V-01: halaman page_view, '' selain itu
    received_at   DateTime64(3, 'UTC'),
    event_date    Date,
    ingested_at   DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now(),
    INDEX idx_subject_key subject_key TYPE bloom_filter(0.01) GRANULARITY 4
)
ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY event_date
ORDER BY (company_code, event_date, subject_key, event_id)
TTL event_date + INTERVAL 90 DAY
SETTINGS ttl_only_drop_parts = 1;

-- V-01: tabel yang dibuat sebelum 23 Nov 2026 (idempoten; metadata saja, baris lama membaca DEFAULT).
ALTER TABLE efn_dwh.fact_visitor_event ADD COLUMN IF NOT EXISTS consented UInt8 DEFAULT 1 AFTER price_idr;
ALTER TABLE efn_dwh.fact_visitor_event ADD COLUMN IF NOT EXISTS page LowCardinality(String) DEFAULT '' AFTER consented;

-- ============================================================== Agregat harian anonim (TTL 2 tahun)
-- Dihitung ETL dengan INSERT ... SELECT di server (data tidak lewat proses ETL) untuk N hari terakhir
-- (ETL_BEHAVIOR_AGG_DAYS, event terlambat), versi computed_at -> hitung ulang menimpa kunci yang sama.

-- Funnel per produk x outlet x kanal x hari.
CREATE TABLE IF NOT EXISTS efn_dwh.agg_visitor_product_daily
(
    day            Date,
    company_code   LowCardinality(String),
    channel        LowCardinality(String),
    channel_code   LowCardinality(String),
    outlet_code    LowCardinality(String),
    product_id     UInt32,
    views          UInt64,
    view_seconds   UInt64,
    viewers        UInt64,                          -- subjek berbeda yang melihat
    oos_views      UInt64,                          -- dilihat saat stock_status = out
    wishlist_adds  UInt64,
    cart_adds      UInt64,
    cart_removes   UInt64,
    cart_abandons  UInt64,
    purchases      UInt64,
    notify_me      UInt64,
    price_alerts   UInt64,
    computed_at    DateTime64(6, 'UTC'),
    _loaded_at     DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (company_code, day, channel, channel_code, outlet_code, product_id)
TTL day + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Funnel subjek per outlet x kanal x hari (jumlah subjek berbeda per tahap).
CREATE TABLE IF NOT EXISTS efn_dwh.agg_visitor_funnel_daily
(
    day                 Date,
    company_code        LowCardinality(String),
    channel             LowCardinality(String),
    channel_code        LowCardinality(String),
    outlet_code         LowCardinality(String),
    subjects            UInt64,
    viewers             UInt64,
    carters             UInt64,
    checkout_starters   UInt64,
    purchasers          UInt64,
    searchers_no_result UInt64,
    computed_at         DateTime64(6, 'UTC'),
    _loaded_at          DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (company_code, day, channel, channel_code, outlet_code)
TTL day + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- Istilah pencarian per kanal x hari, HANYA istilah dengan >= k subjek berbeda (k-anonim, Q-SM17).
CREATE TABLE IF NOT EXISTS efn_dwh.agg_visitor_search_daily
(
    day           Date,
    company_code  LowCardinality(String),
    channel       LowCardinality(String),
    channel_code  LowCardinality(String),
    query         String,
    searches      UInt64,
    no_result     UInt64,
    subjects      UInt64,
    computed_at   DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (company_code, day, channel, channel_code, query)
TTL day + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- V-01: halaman per kanal x hari dari event page_view (anonim + consented). Baris page = '*' = SEMUA visitor hari itu
-- (subjek berbeda dengan event apa pun; views = jumlah page_view) -> "pengunjung unik per hari" tanpa menjumlah halaman.
-- '*' bukan nilai enum halaman, jadi tidak bertabrakan. Unik = per subject_key: perangkat yang memberi consent di tengah
-- hari terhitung dua (kunci anonim `anon:` != kunci consent) — batas yang diterima V-01.
CREATE TABLE IF NOT EXISTS efn_dwh.agg_visitor_page_daily
(
    day           Date,
    company_code  LowCardinality(String),
    channel       LowCardinality(String),
    page          LowCardinality(String),
    visitors      UInt64,
    views         UInt64,
    computed_at   DateTime64(6, 'UTC'),
    _loaded_at    DateTime DEFAULT now()
)
ENGINE = ReplacingMergeTree(computed_at)
PARTITION BY toYYYYMM(day)
ORDER BY (company_code, day, channel, page)
TTL day + INTERVAL 2 YEAR
SETTINGS ttl_only_drop_parts = 1;

-- ============================================================== View sumber segment-worker (efn_dwh.v_seg_*)
-- Dibungkus view DEFINER efn_segment.* (access/segment-access.sql). Semua item SELECT teratas memakai AS. Tanpa nama
-- produk, tanpa query pencarian (segmen hanya butuh jumlah), tanpa id event.
-- V-01: WAJIB `consented = 1` — kunjungan anonim tidak pernah dipakai segmen/kampanye (kontrak V-01).

-- Per subjek x produk x hari. last_* = 1970-01-01 bila tidak ada event jenis itu (worker memperlakukan sebagai None).
CREATE OR REPLACE VIEW efn_dwh.v_seg_subject_product_daily AS
SELECT
    e.event_date AS day,
    e.company_code AS company_code,
    e.subject_key AS subject_key,
    any(e.subject_kind) AS subject_kind,
    e.product_id AS product_id,
    countIf(e.event_type = 'product_view') AS views,
    sumIf(toUInt64(e.duration_s), e.event_type = 'product_view') AS view_seconds,
    countIf(e.event_type = 'product_view' AND e.stock_status = 'out') AS oos_views,
    countIf(e.event_type = 'cart_add') AS cart_adds,
    countIf(e.event_type = 'checkout_start') AS checkouts,
    countIf(e.event_type = 'purchase') AS purchases,
    maxIf(e.received_at, e.event_type = 'product_view') AS last_view_at,
    maxIf(e.received_at, e.event_type = 'wishlist_add') AS last_wishlist_add_at,
    maxIf(e.received_at, e.event_type = 'wishlist_remove') AS last_wishlist_remove_at,
    maxIf(e.received_at, e.event_type IN ('purchase', 'checkout_start')) AS last_purchase_at,
    maxIf(ifNull(e.price_idr, 0), e.event_type = 'product_view') AS max_view_price_idr,
    sumIf(ifNull(e.price_idr, 0), e.event_type = 'purchase' AND ifNull(e.price_idr, 0) > 0) AS purchase_price_sum,
    countIf(e.event_type = 'purchase' AND ifNull(e.price_idr, 0) > 0) AS purchase_price_n
FROM
(
    SELECT event_date, company_code, subject_key, subject_kind, product_id, event_type, duration_s, stock_status,
           price_idr, received_at
    FROM efn_dwh.fact_visitor_event FINAL
    WHERE product_id != 0 AND consented = 1
) AS e
GROUP BY e.event_date, e.company_code, e.subject_key, e.product_id;

-- Per subjek x hari: pencarian & pencarian tanpa hasil (tanpa istilahnya).
CREATE OR REPLACE VIEW efn_dwh.v_seg_subject_search_daily AS
SELECT
    e.event_date AS day,
    e.company_code AS company_code,
    e.subject_key AS subject_key,
    any(e.subject_kind) AS subject_kind,
    count() AS searches,
    countIf(ifNull(e.result_count, 1) = 0) AS no_result_searches
FROM
(
    SELECT event_date, company_code, subject_key, subject_kind, result_count
    FROM efn_dwh.fact_visitor_event FINAL
    WHERE event_type = 'search' AND consented = 1
) AS e
GROUP BY e.event_date, e.company_code, e.subject_key;

-- Kategori produk yang pernah muncul di event company itu (tanpa nama produk).
CREATE OR REPLACE VIEW efn_dwh.v_seg_product AS
SELECT
    v.company_code AS company_code,
    v.product_id AS product_id,
    p.category AS category
FROM (SELECT DISTINCT company_code, product_id FROM efn_dwh.fact_visitor_event WHERE product_id != 0 AND consented = 1) AS v
INNER JOIN (SELECT product_id, category FROM efn_dwh.dim_product FINAL) AS p ON p.product_id = v.product_id;

-- Harga pasar Price Hunter tervalidasi 30 hari per produk (ADR-047 §3.2). Pemetaan laporan -> produk lewat
-- competitor_prices.product_sku = dim_product.default_code; selama pemetaan SKU Price Hunter belum ada (product_sku
-- NULL, efn_etl/jobs.py) view ini KOSONG -> worker hanya memakai pembanding "median harga beli member". Tanpa user_hash
-- / lokasi pelapor. Minimal 3 laporan per produk.
CREATE OR REPLACE VIEW efn_dwh.v_seg_market_price AS
SELECT
    p.product_id AS product_id,
    toFloat64(median(toFloat64(c.price))) AS median_price_idr,
    count() AS reports
FROM
(
    SELECT assumeNotNull(product_sku) AS sku, price
    FROM efn_dwh.competitor_prices FINAL
    WHERE status = 'valid' AND isNotNull(product_sku) AND product_sku != ''
      AND reported_at >= now() - INTERVAL 30 DAY
) AS c
INNER JOIN (SELECT product_id, default_code FROM efn_dwh.dim_product FINAL WHERE default_code != '') AS p
    ON p.default_code = c.sku
GROUP BY p.product_id
HAVING count() >= 3;
