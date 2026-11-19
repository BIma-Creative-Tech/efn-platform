-- Gelombang 41 (docs/04 §25, ADR-040 Proposed, K25-02): ESB Core -> ClickHouse (services/esb-extract, paket efn_esb).
-- Idempoten (CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah 01..11.
--
-- PDP (L-110): tanpa customer/member/karyawan; supplier hanya kode/nama perusahaan/status; field identitas orang dibuang
-- dari payload SEBELUM ditulis ke efn_esb.raw_doc (efn_esb/normalize.py redact). salesNum = ID transaksi (bukan PII).
-- Retensi (L-109): raw 36 bulan (TTL); mart tanpa TTL pendek (ikut DWH 10 tahun, #59); checkpoint/run tanpa TTL.
--
-- Versi slice: satu slice = (brand, entitas, scope_key, hari). Semua baris fakta slice membawa `_version` = versi slice;
-- slice selesai -> baris extract_checkpoint dengan finished_at = versi itu (ditulis TERAKHIR). View fakta hanya membaca
-- baris dari versi slice terakhir (filter IN ke extract_checkpoint FINAL), sehingga baris yang hilang di ESB (void,
-- koreksi H-7, dokumen batal) ikut hilang setelah hari itu ditarik ulang, dan slice yang gagal di tengah tidak terlihat.
-- Tanggal fakta = hari slice yang diminta ke ESB (bukan tanggal yang diparse dari dokumen).

CREATE DATABASE IF NOT EXISTS efn_esb;

-- ============================================================== efn_esb (raw + kendali)
CREATE TABLE IF NOT EXISTS efn_esb.raw_doc
(
    brand           LowCardinality(String),
    endpoint        LowCardinality(String),
    doc_key         String,                         -- `<hari>|<branchID>` (laporan per toko), `<hari>` (per brand), nomor dokumen (detail)
    doc_date        Date,
    payload         String CODEC(ZSTD(3)),          -- JSON kanonik ter-redaksi (L-110), laporan berhalaman digabung per slice
    payload_sha256  String,
    fetched_at      DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(fetched_at)
PARTITION BY toYYYYMM(doc_date)
ORDER BY (brand, endpoint, doc_key)
TTL doc_date + INTERVAL 36 MONTH
SETTINGS ttl_only_drop_parts = 1;

-- finished_at = versi slice (= `_version` baris fakta slice). Hanya slice sukses yang ditulis (status 'done').
CREATE TABLE IF NOT EXISTS efn_esb.extract_checkpoint
(
    brand        LowCardinality(String),
    entity       LowCardinality(String),
    scope_key    String,                            -- branchID (entitas per toko) atau '*' (per brand)
    day          Date,
    status       LowCardinality(String),
    rows         UInt64,
    calls        UInt32,
    finished_at  DateTime64(3, 'UTC'),
    written_at   DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(finished_at)
ORDER BY (brand, entity, scope_key, day);

CREATE TABLE IF NOT EXISTS efn_esb.extract_run
(
    run_id       UUID,
    brand        LowCardinality(String),
    mode         LowCardinality(String),            -- run | backfill
    started_at   DateTime64(3, 'UTC'),
    finished_at  DateTime64(3, 'UTC'),
    calls        UInt64,
    rows         UInt64,
    errors       UInt32,
    status       LowCardinality(String)             -- ok|partial|error|limit|auth_failed|auth_suspended|window_closed
)
ENGINE = MergeTree
ORDER BY (brand, started_at, run_id);

-- Rasio BATCH -> EA per BOM (detail simple-manufacturing contoh; satu GET per BOM baru, bukan per dokumen).
CREATE TABLE IF NOT EXISTS efn_esb.bom_ratio
(
    brand         LowCardinality(String),
    bom_id        UInt64,
    product_code  String,
    product_name  String,
    uom           LowCardinality(String),
    ratio         Float64,
    sample_doc    String,
    _version      DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, bom_id);

-- ============================================================== efn_dwh dimensi
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_branch
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    branch_id     UInt32,
    branch_code   String,
    branch_name   String,
    is_ck         UInt8,                            -- ESB_CK_BRANCHES
    is_store      UInt8,                            -- bukan CK / gudang / head office (nama) / ESB_NON_STORE_BRANCHES
    active        UInt8,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, branch_id);

CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_product
(
    brand              LowCardinality(String),
    company_code       LowCardinality(String),
    product_code       String,
    product_name       String,
    category           String,
    uom                LowCardinality(String),      -- '' bila /product tidak memuat satuan (spec: tidak ada)
    is_fin             UInt8,
    batch_to_ea_ratio  Nullable(Float64),
    active             UInt8,
    _version           DateTime64(3, 'UTC'),
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, product_code);

CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_menu
(
    brand                 LowCardinality(String),
    company_code          LowCardinality(String),
    menu_code             String,
    menu_name             String,
    menu_category         String,
    menu_category_detail  String,
    is_takeaway           UInt8,
    _version              DateTime64(3, 'UTC'),
    _loaded_at            DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, menu_code);

CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_product_alias
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    alias_norm    String,
    product_code  String,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, alias_norm);

CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_supplier
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    supplier_code  String,
    supplier_name  String,                          -- nama perusahaan, tanpa kontak/telepon/email/alamat/NPWP
    active         UInt8,
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, supplier_code);

-- efn_dwh.map_esb_odoo: dipindah ke 14-g43-migration.sql (kunci baru dengan company_code, docs/04 §27.10). Tabel
-- versi G41 (kosong) dihapus clickhouse-init.sh sebelum init/*.sql; berisi -> init gagal (tindakan manual).

-- ============================================================== efn_dwh fakta
-- `brand` ikut kunci setelah company_code: branch_id hanya unik dalam satu akun ESB (brand).
-- line_no (L-113) = 1..n dalam satu salesNum setelah baris diurutkan (menuCode, rowType, qty, price, total, lalu
-- discount, cogs, menuName sebagai pemecah seri) -> tarik ulang hari yang sama = kunci sama.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_sales_line
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    branch_id     UInt32,
    sales_date    Date,
    sales_num     String,
    line_no       UInt32,
    menu_code     String,
    menu_name     String,
    qty           Decimal(18, 3),
    price         Decimal(18, 2),
    total         Decimal(18, 2),
    discount      Decimal(18, 2),
    cogs          Decimal(18, 2),
    row_type      LowCardinality(String),           -- ALACARTE / PACKAGE_HEADER / PACKAGE_COMPONENT (komponen total=0)
    is_takeaway   UInt8,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_id, sales_date, sales_num, line_no);

-- `payment_count` (bukan `count`: nama fungsi agregat ClickHouse).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_payment_daily
(
    brand           LowCardinality(String),
    company_code    LowCardinality(String),
    branch_id       UInt32,                         -- 0 bila branchCode tidak dikenal di dim_esb_branch
    branch_code     String,
    sales_date      Date,
    payment_type    LowCardinality(String),
    payment_method  String,
    payment_count   UInt32,
    amount          Decimal(18, 2),
    mdr             Decimal(18, 2),
    net             Decimal(18, 2),
    _version        DateTime64(3, 'UTC'),
    _loaded_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_code, sales_date, payment_type, payment_method);

-- product_code = kode ESB atau 'NAME:<alias_norm>' bila nama resep tidak terpetakan (view me-resolve ulang lewat alias).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_fin_sold_daily
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    branch_id     UInt32,
    sales_date    Date,
    product_code  String,
    qty_ea        Decimal(18, 3),
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_id, sales_date, product_code);

-- uom: satuan setelah konversi ('EA' untuk production_result ber-convertionQty; 'NO_RATIO' = simple_mfg tanpa rasio BOM,
-- qty masih batch). Semua produk dimuat; is_fin menandai produk jadi FIN.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_production
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    branch_id     UInt32,
    prod_date     Date,
    source        LowCardinality(String),           -- simple_mfg | production_result
    doc_num       String,
    product_code  String,
    uom           LowCardinality(String),
    qty_ea        Decimal(18, 3),
    is_ck         UInt8,
    is_fin        UInt8,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(prod_date)
ORDER BY (company_code, brand, branch_id, prod_date, source, doc_num, product_code);

-- company_code = PT cabang asal. qty = qty terima bila ada, selain itu qty kirim (prototipe).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_delivery
(
    brand             LowCardinality(String),
    company_code      LowCardinality(String),
    delivery_date     Date,
    doc_num           String,
    origin_branch_id  UInt32,
    dest_branch_id    UInt32,
    product_code      String,
    uom               LowCardinality(String),
    qty_ea            Decimal(18, 3),
    is_fin            UInt8,
    _version          DateTime64(3, 'UTC'),
    _loaded_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(delivery_date)
ORDER BY (company_code, brand, delivery_date, doc_num, product_code);

-- Hanya status 3 & 6. product_code bisa 'NAME:<alias_norm>' (detail transfer tanpa kode).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_transfer
(
    brand             LowCardinality(String),
    company_code      LowCardinality(String),
    transfer_date     Date,
    doc_num           String,
    origin_branch_id  UInt32,
    dest_branch_id    UInt32,
    product_code      String,
    uom               LowCardinality(String),
    qty_ea            Decimal(18, 3),
    status            UInt16,
    is_fin            UInt8,
    _version          DateTime64(3, 'UTC'),
    _loaded_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(transfer_date)
ORDER BY (company_code, brand, transfer_date, doc_num, product_code);

-- Bersyarat (§25.4): entitas `stock` default TIDAK aktif (ESB_ENTITIES) sampai parameter terbukti di production.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_stock_snapshot
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    branch_id      UInt32,
    snapshot_date  Date,
    product_code   String,
    uom            LowCardinality(String),
    qty            Decimal(18, 3),
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(snapshot_date)
ORDER BY (company_code, brand, branch_id, snapshot_date, product_code);

-- ============================================================== View (semantic layer; nama final docs/04 §25.6)
-- Tidak ada v_esb_sales_hourly (L-112: sales-menu-cogs tanpa jam) dan v_esb_stock_cover (ditunda). Row policy efn_t2s
-- untuk v_esb_* ditunda ke G42 (§25.7).

-- Hari x brand x PT x cabang x menu. gross = Σ total, net = gross - discount (semantik `total` ESB: README).
CREATE OR REPLACE VIEW efn_dwh.v_esb_sales_daily AS
SELECT
    a.sales_date AS sales_date, a.brand AS brand, a.company_code AS company_code, a.branch_id AS branch_id,
    if(b.branch_name = '', concat('Cabang #', toString(a.branch_id)), b.branch_name) AS branch_name,
    a.menu_code AS menu_code, a.m_name AS menu_name, m.menu_category AS menu_category,
    m.menu_category_detail AS menu_category_detail, a.m_takeaway AS is_takeaway,
    a.qty_s AS qty, a.gross_s AS gross, a.discount_s AS discount, a.gross_s - a.discount_s AS net, a.cogs_s AS cogs,
    a.trx_s AS trx_count
FROM
(
    SELECT s.sales_date AS sales_date, s.brand AS brand, s.company_code AS company_code, s.branch_id AS branch_id,
           s.menu_code AS menu_code, any(s.menu_name) AS m_name, max(s.is_takeaway) AS m_takeaway,
           sum(s.qty) AS qty_s, sum(s.total) AS gross_s, sum(s.discount) AS discount_s, sum(s.cogs) AS cogs_s,
           uniqExact(s.sales_num) AS trx_s
    FROM
    (
        SELECT * FROM efn_dwh.fact_esb_sales_line FINAL
        WHERE (brand, toString(branch_id), sales_date, _version) IN
              (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'sales')
    ) AS s
    GROUP BY sales_date, brand, company_code, branch_id, menu_code
) AS a
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = a.brand AND b.branch_id = a.branch_id
LEFT JOIN (SELECT brand, menu_code, menu_category, menu_category_detail FROM efn_dwh.dim_esb_menu FINAL) AS m
    ON m.brand = a.brand AND m.menu_code = a.menu_code;

CREATE OR REPLACE VIEW efn_dwh.v_esb_payment_daily AS
SELECT
    p.sales_date AS sales_date, p.brand AS brand, p.company_code AS company_code, p.branch_id AS branch_id,
    p.branch_code AS branch_code,
    if(b.branch_name = '', p.branch_code, b.branch_name) AS branch_name,
    p.payment_type AS payment_type, p.payment_method AS payment_method, p.payment_count AS payment_count,
    p.amount AS amount, p.mdr AS mdr, p.net AS net
FROM
(
    SELECT * FROM efn_dwh.fact_esb_payment_daily FINAL
    WHERE (brand, sales_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'payment' AND scope_key = '*')
) AS p
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS b
    ON b.brand = p.brand AND b.branch_id = p.branch_id;

-- Alur produk jadi FIN (L-116 = prototipe aggregate.py), satuan EA, hari x brand x PT x cabang x produk.
--   ck_prod   = simple_mfg di cabang dim_esb_branch.is_ck = 1, DITAMBAH semua production_result (prototipe: production-
--               result selalu ck_prod; diasumsikan hanya terjadi di CK/CKWH -- belum diverifikasi untuk semua brand).
--   store_prod= simple_mfg di cabang is_ck = 0.
--   ck_out    = delivery dari cabang CK (di cabang asal); in_ck = baris yang sama di cabang tujuan;
--   in_other  = delivery dari cabang non-CK (di tujuan; asal tidak dikurangi).
--   st_out / st_in = simple-transfer status 3 & 6 (asal / tujuan). sold = fin_sold (salesType Sales).
-- product_code 'NAME:<alias>' di-resolve ulang lewat dim_esb_product_alias saat query (alias yang dipelajari belakangan
-- ikut berlaku untuk data lama). PT = PT cabang di dim_esb_branch (fallback PT baris fakta).
CREATE OR REPLACE VIEW efn_dwh.v_esb_flow_daily AS
SELECT
    t.day AS day, t.brand AS brand,
    if(br.company_code = '', t.c_code, br.company_code) AS company_code,
    t.branch_id AS branch_id,
    if(br.branch_name = '', concat('Cabang #', toString(t.branch_id)), br.branch_name) AS branch_name,
    t.p_code AS product_code,
    if(pr.product_name = '', replaceOne(t.p_code, 'NAME:', ''), pr.product_name) AS product_name,
    t.ck_prod_q AS ck_prod, t.ck_out_q AS ck_out, t.in_ck_q AS in_ck, t.in_other_q AS in_other,
    t.store_prod_q AS store_prod, t.st_in_q AS st_in, t.st_out_q AS st_out, t.sold_q AS sold
FROM
(
    SELECT g.day AS day, g.brand AS brand, g.branch_id AS branch_id, g.p_code AS p_code, any(g.c_code) AS c_code,
           sum(g.ck_prod) AS ck_prod_q, sum(g.ck_out) AS ck_out_q, sum(g.in_ck) AS in_ck_q,
           sum(g.in_other) AS in_other_q, sum(g.store_prod) AS store_prod_q, sum(g.st_in) AS st_in_q,
           sum(g.st_out) AS st_out_q, sum(g.sold) AS sold_q
    FROM
    (
        SELECT r.day AS day, r.brand AS brand, r.branch_id AS branch_id, r.c_code AS c_code,
               if(al.product_code = '', r.code_raw, al.product_code) AS p_code,
               r.ck_prod AS ck_prod, r.ck_out AS ck_out, r.in_ck AS in_ck, r.in_other AS in_other,
               r.store_prod AS store_prod, r.st_in AS st_in, r.st_out AS st_out, r.sold AS sold
        FROM
        (
            -- produksi
            SELECT x.prod_date AS day, x.brand AS brand, x.company_code AS c_code, x.branch_id AS branch_id,
                   x.product_code AS code_raw, '' AS alias_key,
                   if(x.source = 'production_result' OR ckp.is_ck = 1, toFloat64(x.qty_ea), 0.) AS ck_prod,
                   0. AS ck_out, 0. AS in_ck, 0. AS in_other,
                   if(x.source = 'simple_mfg' AND ckp.is_ck = 0, toFloat64(x.qty_ea), 0.) AS store_prod,
                   0. AS st_in, 0. AS st_out, 0. AS sold
            FROM
            (
                SELECT * FROM efn_dwh.fact_esb_production FINAL
                WHERE is_fin = 1 AND (brand, prod_date, _version) IN
                      (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                       WHERE entity = 'production' AND scope_key = '*')
            ) AS x
            LEFT JOIN (SELECT brand, branch_id, is_ck FROM efn_dwh.dim_esb_branch FINAL) AS ckp
                ON ckp.brand = x.brand AND ckp.branch_id = x.branch_id
            UNION ALL
            -- delivery, sisi asal CK
            SELECT d.delivery_date AS day, d.brand AS brand, d.company_code AS c_code, d.origin_branch_id AS branch_id,
                   d.product_code AS code_raw, '' AS alias_key,
                   0. AS ck_prod, toFloat64(d.qty_ea) AS ck_out, 0. AS in_ck, 0. AS in_other, 0. AS store_prod,
                   0. AS st_in, 0. AS st_out, 0. AS sold
            FROM
            (
                SELECT * FROM efn_dwh.fact_esb_delivery FINAL
                WHERE is_fin = 1 AND (brand, delivery_date, _version) IN
                      (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                       WHERE entity = 'delivery' AND scope_key = '*')
            ) AS d
            INNER JOIN (SELECT brand, branch_id FROM efn_dwh.dim_esb_branch FINAL WHERE is_ck = 1) AS cko
                ON cko.brand = d.brand AND cko.branch_id = d.origin_branch_id
            UNION ALL
            -- delivery, sisi tujuan (in_ck bila asal CK, selain itu in_other)
            SELECT d.delivery_date AS day, d.brand AS brand, d.company_code AS c_code, d.dest_branch_id AS branch_id,
                   d.product_code AS code_raw, '' AS alias_key,
                   0. AS ck_prod, 0. AS ck_out,
                   if(ckd.is_ck = 1, toFloat64(d.qty_ea), 0.) AS in_ck,
                   if(ckd.is_ck = 1, 0., toFloat64(d.qty_ea)) AS in_other,
                   0. AS store_prod, 0. AS st_in, 0. AS st_out, 0. AS sold
            FROM
            (
                SELECT * FROM efn_dwh.fact_esb_delivery FINAL
                WHERE is_fin = 1 AND (brand, delivery_date, _version) IN
                      (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                       WHERE entity = 'delivery' AND scope_key = '*')
            ) AS d
            LEFT JOIN (SELECT brand, branch_id, is_ck FROM efn_dwh.dim_esb_branch FINAL) AS ckd
                ON ckd.brand = d.brand AND ckd.branch_id = d.origin_branch_id
            UNION ALL
            -- transfer, sisi asal
            SELECT tr.transfer_date AS day, tr.brand AS brand, tr.company_code AS c_code,
                   tr.origin_branch_id AS branch_id, tr.product_code AS code_raw,
                   if(startsWith(tr.product_code, 'NAME:'), substring(tr.product_code, 6), '') AS alias_key,
                   0. AS ck_prod, 0. AS ck_out, 0. AS in_ck, 0. AS in_other, 0. AS store_prod,
                   0. AS st_in, toFloat64(tr.qty_ea) AS st_out, 0. AS sold
            FROM
            (
                SELECT * FROM efn_dwh.fact_esb_transfer FINAL
                WHERE is_fin = 1 AND (brand, transfer_date, _version) IN
                      (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                       WHERE entity = 'transfer' AND scope_key = '*')
            ) AS tr
            UNION ALL
            -- transfer, sisi tujuan
            SELECT tr.transfer_date AS day, tr.brand AS brand, tr.company_code AS c_code,
                   tr.dest_branch_id AS branch_id, tr.product_code AS code_raw,
                   if(startsWith(tr.product_code, 'NAME:'), substring(tr.product_code, 6), '') AS alias_key,
                   0. AS ck_prod, 0. AS ck_out, 0. AS in_ck, 0. AS in_other, 0. AS store_prod,
                   toFloat64(tr.qty_ea) AS st_in, 0. AS st_out, 0. AS sold
            FROM
            (
                SELECT * FROM efn_dwh.fact_esb_transfer FINAL
                WHERE is_fin = 1 AND (brand, transfer_date, _version) IN
                      (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                       WHERE entity = 'transfer' AND scope_key = '*')
            ) AS tr
            UNION ALL
            -- terjual (resep menu -> produk FIN)
            SELECT f.sales_date AS day, f.brand AS brand, f.company_code AS c_code, f.branch_id AS branch_id,
                   f.product_code AS code_raw,
                   if(startsWith(f.product_code, 'NAME:'), substring(f.product_code, 6), '') AS alias_key,
                   0. AS ck_prod, 0. AS ck_out, 0. AS in_ck, 0. AS in_other, 0. AS store_prod,
                   0. AS st_in, 0. AS st_out, toFloat64(f.qty_ea) AS sold
            FROM
            (
                SELECT * FROM efn_dwh.fact_esb_fin_sold_daily FINAL
                WHERE (brand, toString(branch_id), sales_date, _version) IN
                      (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL
                       WHERE entity = 'fin_sold')
            ) AS f
        ) AS r
        LEFT JOIN (SELECT brand, alias_norm, product_code FROM efn_dwh.dim_esb_product_alias FINAL) AS al
            ON al.brand = r.brand AND al.alias_norm = r.alias_key
    ) AS g
    GROUP BY day, brand, branch_id, p_code
) AS t
LEFT JOIN (SELECT brand, branch_id, branch_name, company_code FROM efn_dwh.dim_esb_branch FINAL) AS br
    ON br.brand = t.brand AND br.branch_id = t.branch_id
LEFT JOIN (SELECT brand, product_code, product_name FROM efn_dwh.dim_esb_product FINAL) AS pr
    ON pr.brand = t.brand AND pr.product_code = t.p_code;

-- Bulan x brand x PT x cabang x kategori menu x takeaway; share dalam cabang-bulan (%).
CREATE OR REPLACE VIEW efn_dwh.v_esb_menu_mix AS
SELECT
    m.month AS month, m.brand AS brand, m.company_code AS company_code, m.branch_id AS branch_id,
    m.b_name AS branch_name, m.m_cat AS menu_category, m.m_takeaway AS is_takeaway,
    m.qty_m AS qty, m.net_m AS net,
    if(sum(m.qty_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) = 0, NULL,
       round(m.qty_m / sum(m.qty_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) * 100, 2)) AS share_qty,
    if(sum(m.net_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) = 0, NULL,
       round(m.net_m / sum(m.net_m) OVER (PARTITION BY m.month, m.brand, m.branch_id) * 100, 2)) AS share_net
FROM
(
    SELECT toStartOfMonth(v.sales_date) AS month, v.brand AS brand, v.company_code AS company_code,
           v.branch_id AS branch_id, any(v.branch_name) AS b_name,
           if(v.menu_category = '', 'Tanpa kategori', v.menu_category) AS m_cat, v.is_takeaway AS m_takeaway,
           toFloat64(sum(v.qty)) AS qty_m, toFloat64(sum(v.net)) AS net_m
    FROM efn_dwh.v_esb_sales_daily AS v
    GROUP BY month, brand, company_code, branch_id, m_cat, m_takeaway
) AS m;

-- Temuan kualitas data: check_name, brand, company_code, item, value, last_day.
CREATE OR REPLACE VIEW efn_dwh.v_esb_data_quality AS
-- 1. Nama produk tak terpetakan (30 hari): value = qty terjual + transfer.
SELECT 'alias_unmapped' AS check_name, q.brand AS brand, q.company_code AS company_code, q.product_code AS item,
       q.qty_q AS value, q.last_q AS last_day
FROM
(
    SELECT f.brand AS brand, f.company_code AS company_code, f.product_code AS product_code,
           sum(f.sold + f.st_in + f.st_out) AS qty_q, max(f.day) AS last_q
    FROM efn_dwh.v_esb_flow_daily AS f
    WHERE startsWith(f.product_code, 'NAME:') AND f.day >= today() - 30
    GROUP BY brand, company_code, product_code
) AS q
UNION ALL
-- 2. Baris FIN non-EA (30 hari): item = '<tabel>:<uom>', value = jumlah baris.
SELECT 'non_ea_lines', n.brand, n.company_code, n.item, toFloat64(n.lines_n), n.last_n
FROM
(
    SELECT brand, company_code, concat('production:', uom) AS item, count() AS lines_n, max(prod_date) AS last_n
    FROM efn_dwh.fact_esb_production FINAL
    WHERE is_fin = 1 AND uom != 'EA' AND prod_date >= today() - 30
    GROUP BY brand, company_code, item
    UNION ALL
    SELECT brand, company_code, concat('delivery:', uom) AS item, count() AS lines_n, max(delivery_date) AS last_n
    FROM efn_dwh.fact_esb_delivery FINAL
    WHERE is_fin = 1 AND uom != 'EA' AND delivery_date >= today() - 30
    GROUP BY brand, company_code, item
    UNION ALL
    SELECT brand, company_code, concat('transfer:', uom) AS item, count() AS lines_n, max(transfer_date) AS last_n
    FROM efn_dwh.fact_esb_transfer FINAL
    WHERE is_fin = 1 AND uom != 'EA' AND transfer_date >= today() - 30
    GROUP BY brand, company_code, item
) AS n
UNION ALL
-- 3. Toko aktif tanpa baris penjualan H-1 (slice ada tetapi 0 baris, atau belum ditarik).
SELECT 'store_no_sales_h1', b.brand, b.company_code, concat(toString(b.branch_id), ' ', b.branch_name),
       toFloat64(c.rows_c), yesterday()
FROM (SELECT brand, company_code, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL
      WHERE is_store = 1 AND active = 1) AS b
LEFT JOIN (SELECT brand, scope_key, sum(rows) AS rows_c FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'sales' AND day = yesterday() GROUP BY brand, scope_key) AS c
    ON c.brand = b.brand AND c.scope_key = toString(b.branch_id)
WHERE c.rows_c = 0
UNION ALL
-- 4. Umur data terakhir per brand x entitas (jam sejak slice terakhir ditulis).
SELECT 'data_age_hours', a.brand, '', a.entity, toFloat64(dateDiff('hour', a.last_w, now64(3))), a.last_d
FROM (SELECT brand, entity, max(written_at) AS last_w, max(day) AS last_d FROM efn_esb.extract_checkpoint FINAL
      GROUP BY brand, entity) AS a
UNION ALL
-- 5. Run terakhir per brand x mode yang tidak ok (status = item, value = errors).
SELECT 'last_run_not_ok', r.brand, '', concat(r.mode, ':', r.status_r), toFloat64(r.errors_r), toDate(r.started_r)
FROM
(
    SELECT brand, mode, argMax(status, started_at) AS status_r, argMax(errors, started_at) AS errors_r,
           max(started_at) AS started_r
    FROM efn_esb.extract_run
    GROUP BY brand, mode
) AS r
WHERE r.status_r != 'ok';
