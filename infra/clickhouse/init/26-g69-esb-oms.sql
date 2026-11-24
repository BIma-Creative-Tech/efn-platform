-- Gelombang 69 (docs/04 §33, amandemen ADR-040 §6b -- Proposed): ESB OMS (penjualan level struk) -> ClickHouse.
-- Idempoten (CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah
-- 01..25 (glob /init/schema/*.sql, urutan nama) dan SEBELUM access/*.sql (wrapper efn_insight membaca view di bawah).
-- Penulis: services/esb-extract (efn_esb/oms_runner.py, role esb_writer = SELECT + INSERT saja, tanpa DDL).
--
-- PDP/PCI (§33.6): member (ID/kode/nama), nomor kartu, nama rekening, kode verifikasi/voucher, teks bebas, pelaku
-- (createdBy/editedBy), selfOrderID, dan salesInfo[] DIBUANG sebelum raw_doc & mart (efn_esb/oms_transform.py
-- strip_oms); yang tersisa dari member hanya has_member (0/1). tableName juga dibuang (SEC-OMS-01: label meja bisa
-- berisi nama pelanggan/driver); salesPayments[] di-whitelist + denylist substring (SEC-OMS-02).
-- Retensi (usulan, Q-OMS3): raw_doc 36 bulan (TTL tabel G41), mart tanpa TTL pendek (DWH 10 tahun).
-- Versi slice (pola §25.4): slice = (brand, 'oms_sales', branch_code, sales_date); fakta struk/baris/pembayaran slice
-- membawa _version = finished_at checkpoint; view hanya membaca versi ber-checkpoint (slice gagal/terpotong tidak
-- terlihat). `oms_branch_daily` = slice (brand, 'oms_branch_daily', '*', sales_date).
-- [B] belum diverifikasi sumber primer: arti statusName per status (is_final/is_cancelled = penanda teks, README),
-- semantik `total` vs `discount_value` baris, bentuk packages[]/extras[], branchCode OMS = branch_code ESB Core.
-- Semua tabel: storage_policy 'encrypted' (ADR-022; juga default merge_tree config.d/encrypted-storage.xml).

-- ============================================================== fakta
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_oms_bill
(
    brand                  LowCardinality(String),
    company_code           LowCardinality(String),
    branch_id              UInt32,                  -- cabang ESB Core (dim_esb_branch.branch_code), 0 = tak dikenal
    branch_code            String,
    sales_date             Date,                    -- hari slice yang diminta
    sales_num              String,
    bill_num               String,
    parent_link_sales_num  String,
    sales_ts_in            Nullable(DateTime('Asia/Jakarta')),
    sales_ts_out           Nullable(DateTime('Asia/Jakarta')),
    visit_purpose_id       UInt32,
    visit_purpose_name     String,                  -- kanal (dine in / take away / ojol ...)
    pax                    UInt16,
    subtotal               Decimal(18, 2),
    discount_total         Decimal(18, 2),
    menu_discount_total    Decimal(18, 2),
    promotion_discount     Decimal(18, 2),
    other_tax_total        Decimal(18, 2),
    vat_total              Decimal(18, 2),
    voucher_total          Decimal(18, 2),
    rounding_total         Decimal(18, 2),
    grand_total            Decimal(18, 2),
    payment_total          Decimal(18, 2),
    promotion_id           UInt64,
    promotion_name         String,
    status_id              UInt32,
    status_name            LowCardinality(String),
    is_final               UInt8,                   -- 0 = void/batal/terbuka (penanda statusName, [B])
    has_member             UInt8,
    _version              DateTime64(3, 'UTC'),
    _loaded_at             DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_code, sales_date, sales_num)
SETTINGS storage_policy = 'encrypted';

-- line_no stabil per sales_num (§33.4): baris menu diurutkan (menu_id, menu_code, sales_type, qty, price, total,
-- discount_value, menu_name); packages[]/extras[] diratakan depth-first di bawah induknya (parent_line_no; 0 = menu).
-- menu_key = nama menu kanonik (§31.2 butir 4) untuk pemetaan FIN lewat nama. has_packages = header paket.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_oms_bill_line
(
    brand                      LowCardinality(String),
    company_code               LowCardinality(String),
    branch_id                  UInt32,
    branch_code                String,
    sales_date                 Date,
    sales_num                  String,
    line_no                    UInt32,
    parent_line_no             UInt32,
    line_kind                  LowCardinality(String),  -- menu | package | extra
    menu_id                    UInt64,
    menu_code                  String,                  -- '' bila kosong di sumber
    menu_name                  String,
    menu_key                   String,
    menu_category_name         String,
    menu_category_detail_name  String,
    qty                        Decimal(18, 3),
    original_price             Decimal(18, 2),
    price                      Decimal(18, 2),
    discount_value             Decimal(18, 2),
    total                      Decimal(18, 2),
    vat_value                  Decimal(18, 2),
    status_id                  UInt32,
    status_name                LowCardinality(String),
    sales_type                 LowCardinality(String),
    is_promo                   UInt8,
    is_cancelled               UInt8,
    has_packages               UInt8,
    _version                   DateTime64(3, 'UTC'),
    _loaded_at                 DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_code, sales_date, sales_num, line_no)
SETTINGS storage_policy = 'encrypted';

-- Tanpa nomor kartu / nama rekening / kode verifikasi / kode voucher (§33.6).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_oms_bill_payment
(
    brand                     LowCardinality(String),
    company_code              LowCardinality(String),
    branch_id                 UInt32,
    branch_code               String,
    sales_date                Date,
    sales_num                 String,
    line_no                   UInt32,
    payment_method_id         UInt32,
    payment_method_name       String,
    payment_method_type_id    UInt32,
    payment_method_type_name  LowCardinality(String),
    bank_name                 String,
    payment_amount            Decimal(18, 2),
    full_payment_amount       Decimal(18, 2),
    _version                  DateTime64(3, 'UTC'),
    _loaded_at                DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_code, sales_date, sales_num, line_no)
SETTINGS storage_policy = 'encrypted';

-- sales-branch-summary (angka string di sumber -> Decimal/UInt).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_oms_branch_daily
(
    brand           LowCardinality(String),
    company_code    LowCardinality(String),
    branch_id       UInt32,
    branch_code     String,
    sales_date      Date,
    pax_total       UInt32,
    bill_total      UInt32,
    subtotal        Decimal(18, 2),
    discount_total  Decimal(18, 2),
    sc_total        Decimal(18, 2),
    tax_total       Decimal(18, 2),
    grand_total     Decimal(18, 2),
    _version        DateTime64(3, 'UTC'),
    _loaded_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(sales_date)
ORDER BY (company_code, brand, branch_code, sales_date)
SETTINGS storage_policy = 'encrypted';

-- ============================================================== dimensi (snapshot harian, entitas oms_master)
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_oms_visit_purpose
(
    brand               LowCardinality(String),
    company_code        LowCardinality(String),
    visit_purpose_id    UInt32,
    visit_purpose_name  String,
    flag_dine_in        UInt8,
    _version            DateTime64(3, 'UTC'),
    _loaded_at          DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, visit_purpose_id)
SETTINGS storage_policy = 'encrypted';

CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_oms_payment_method
(
    brand                     LowCardinality(String),
    company_code              LowCardinality(String),
    payment_method_type_id    UInt32,
    payment_method_type_name  String,
    payment_method_id         UInt32,
    payment_method_name       String,
    _version                  DateTime64(3, 'UTC'),
    _loaded_at                DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, payment_method_type_id, payment_method_id)
SETTINGS storage_policy = 'encrypted';

-- Pemetaan menu OMS -> produk FIN, diturunkan harian oleh esb-extract (entitas oms_menu_fin) dari resep mart G43 yang
-- SUDAH ada (BOM aktif bertipe Menu, versi snapshot terakhir; komponen FIN = dim_esb_product.is_fin / aturan FIN §25.5).
-- Tabel turunan terpisah (bukan view di atas resep) karena resep memuat HPP (last_hpp) dan sengaja tidak di-grant ke
-- Insight (§27.11); tabel ini hanya memuat qty FIN per menu. Versi-insert (pola G67): view = versi terakhir per brand;
-- TTL 60 hari (refresh berhenti > 60 hari -> view kosong, fail-visible).
-- key_kind 'code' (method bom_code = menu_code ↔ bom_code; menu_price = menu_code -> bom_name) didahulukan atas 'name'
-- (nama kanonik). product_code '' = BOM menu cocok tanpa komponen FIN (mis. minuman; bukan "tak terpetakan").
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_oms_menu_fin
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    key_kind       LowCardinality(String),          -- code | name
    match_key      String,
    method         LowCardinality(String),          -- bom_code | menu_price | name
    bom_id         UInt64,
    product_code   String,
    product_name   String,
    uom            LowCardinality(String),
    qty_per_menu   Decimal(18, 6),
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (brand, _version, key_kind, match_key, product_code)
TTL toDateTime(_loaded_at) + INTERVAL 60 DAY
SETTINGS storage_policy = 'encrypted';

-- ============================================================== view (semantic layer; nama stabil §33.5)
-- Struk versi slice terbaru. hour = jam WIB sales_ts_out (NULL -> sales_ts_in; keduanya NULL -> NULL). Struk yang
-- ditutup sesudah tanggal struknya (lewat tengah malam) masuk jam 23 hari struk (QA G69 #2), bukan jam 0.
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_bill AS
SELECT
    b.sales_date AS sales_date, b.brand AS brand, b.company_code AS company_code, b.branch_id AS branch_id,
    b.branch_code AS branch_code, if(br.branch_name = '', b.branch_code, br.branch_name) AS branch_name,
    b.sales_num AS sales_num, b.bill_num AS bill_num, b.parent_link_sales_num AS parent_link_sales_num,
    b.sales_ts_in AS sales_ts_in, b.sales_ts_out AS sales_ts_out,
    if(toDate(coalesce(b.sales_ts_out, b.sales_ts_in)) > b.sales_date, 23,
       toHour(coalesce(b.sales_ts_out, b.sales_ts_in))) AS hour,
    b.visit_purpose_id AS visit_purpose_id, b.visit_purpose_name AS visit_purpose_name, b.pax AS pax,
    b.subtotal AS subtotal, b.discount_total AS discount_total, b.menu_discount_total AS menu_discount_total,
    b.promotion_discount AS promotion_discount, b.other_tax_total AS other_tax_total, b.vat_total AS vat_total,
    b.voucher_total AS voucher_total, b.rounding_total AS rounding_total, b.grand_total AS grand_total,
    b.payment_total AS payment_total, b.promotion_id AS promotion_id, b.promotion_name AS promotion_name,
    b.status_id AS status_id, b.status_name AS status_name, b.is_final AS is_final, b.has_member AS has_member
FROM
(
    SELECT brand, company_code, branch_id, branch_code, sales_date, sales_num, bill_num, parent_link_sales_num,
           sales_ts_in, sales_ts_out, visit_purpose_id, visit_purpose_name, pax, subtotal, discount_total,
           menu_discount_total, promotion_discount, other_tax_total, vat_total, voucher_total, rounding_total,
           grand_total, payment_total, promotion_id, promotion_name, status_id, status_name, is_final, has_member
    FROM efn_dwh.fact_esb_oms_bill FINAL
    WHERE (brand, branch_code, sales_date, _version) IN
          (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'oms_sales')
) AS b
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS br
    ON br.brand = b.brand AND br.branch_id = b.branch_id;

-- Baris (menu/paket/extra) versi slice terbaru + jam & status final struk (pembantu; tidak di-wrap ke efn_insight).
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_line AS
SELECT
    l.sales_date AS sales_date, l.brand AS brand, b.company_code AS company_code, b.branch_id AS branch_id,
    l.branch_code AS branch_code, l.sales_num AS sales_num, l.line_no AS line_no, l.parent_line_no AS parent_line_no,
    l.line_kind AS line_kind, l.menu_id AS menu_id, l.menu_code AS menu_code, l.menu_name AS menu_name,
    l.menu_key AS menu_key, l.menu_category_name AS menu_category_name, l.qty AS qty, l.total AS total,
    l.discount_value AS discount_value, l.is_cancelled AS is_cancelled, l.has_packages AS has_packages,
    b.hour AS hour, b.is_final AS bill_final
FROM
(
    SELECT brand, branch_code, sales_date, sales_num, line_no, parent_line_no, line_kind, menu_id, menu_code,
           menu_name, menu_key, menu_category_name, qty, total, discount_value, is_cancelled, has_packages
    FROM efn_dwh.fact_esb_oms_bill_line FINAL
    WHERE (brand, branch_code, sales_date, _version) IN
          (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'oms_sales')
) AS l
INNER JOIN efn_dwh.v_esb_oms_bill AS b
    ON b.brand = l.brand AND b.branch_code = l.branch_code AND b.sales_date = l.sales_date
   AND b.sales_num = l.sales_num;

-- Per jam x kanal, struk final saja. net_sales = Σ (subtotal - discount_total) [B]; items = Σ qty baris menu non-batal.
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_hourly AS
SELECT
    h.s_date AS sales_date, h.s_brand AS brand, h.c_code AS company_code, h.b_id AS branch_id,
    h.b_code AS branch_code, h.b_name AS branch_name, h.hr AS hour, h.vp AS visit_purpose_name,
    h.bills_h AS bills, h.pax_h AS pax, h.gross_h AS gross_sales, h.net_h AS net_sales, h.items_h AS items
FROM
(
    SELECT b.sales_date AS s_date, b.brand AS s_brand, b.company_code AS c_code, b.branch_id AS b_id,
           b.branch_code AS b_code, any(b.branch_name) AS b_name, toUInt8(assumeNotNull(b.hour)) AS hr,
           if(b.visit_purpose_name = '', 'Tanpa kanal', b.visit_purpose_name) AS vp,
           count() AS bills_h, sum(toUInt64(b.pax)) AS pax_h, sum(b.grand_total) AS gross_h,
           sum(b.subtotal - b.discount_total) AS net_h, sum(i.items_b) AS items_h
    FROM efn_dwh.v_esb_oms_bill AS b
    LEFT JOIN
    (
        SELECT brand, branch_code, sales_date, sales_num, sum(qty) AS items_b
        FROM efn_dwh.fact_esb_oms_bill_line FINAL
        WHERE line_kind = 'menu' AND is_cancelled = 0 AND (brand, branch_code, sales_date, _version) IN
              (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL
               WHERE entity = 'oms_sales')
        GROUP BY brand, branch_code, sales_date, sales_num
    ) AS i
        ON i.brand = b.brand AND i.branch_code = b.branch_code AND i.sales_date = b.sales_date
       AND i.sales_num = b.sales_num
    WHERE b.is_final = 1 AND b.hour IS NOT NULL
    GROUP BY s_date, s_brand, c_code, b_id, b_code, hr, vp
) AS h;

-- Per jam x menu (semua line_kind; filter line_kind = 'menu' untuk menu jual). Kunci grup = menu_code, atau nama
-- kanonik bila kode kosong. net_sales = gross_sales - discount (Σ total - Σ discount_value) [B: semantik total].
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_menu_hourly AS
SELECT
    m.sales_date AS sales_date, m.brand AS brand, m.company_code AS company_code, m.branch_id AS branch_id,
    m.branch_code AS branch_code, m.hr AS hour, m.kind AS line_kind, m.m_code AS menu_code, m.m_name AS menu_name,
    m.qty_m AS qty, m.gross_m AS gross_sales, m.disc_m AS discount, m.gross_m - m.disc_m AS net_sales
FROM
(
    SELECT l.sales_date AS sales_date, l.brand AS brand, l.company_code AS company_code, l.branch_id AS branch_id,
           l.branch_code AS branch_code, toUInt8(assumeNotNull(l.hour)) AS hr, l.line_kind AS kind,
           if(l.menu_code = '', concat('NAME:', l.menu_key), l.menu_code) AS m_key, any(l.menu_code) AS m_code,
           any(l.menu_name) AS m_name, sum(l.qty) AS qty_m, sum(l.total) AS gross_m, sum(l.discount_value) AS disc_m
    FROM efn_dwh.v_esb_oms_line AS l
    WHERE l.bill_final = 1 AND l.is_cancelled = 0 AND l.hour IS NOT NULL
    GROUP BY sales_date, brand, company_code, branch_id, branch_code, hr, kind, m_key
) AS m;

-- Pemetaan menu -> FIN versi terakhir per brand (pembantu).
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_menu_fin AS
SELECT t.brand AS brand, t.company_code AS company_code, t.key_kind AS key_kind, t.match_key AS match_key,
       t.method AS method, t.bom_id AS bom_id, t.product_code AS product_code, t.product_name AS product_name,
       t.uom AS uom, t.qty_per_menu AS qty_per_menu
FROM efn_dwh.dim_esb_oms_menu_fin AS t
WHERE (t.brand, t._version) IN
      (SELECT brand, max(_version) FROM efn_dwh.dim_esb_oms_menu_fin GROUP BY brand);

-- Baris yang dihitung untuk FIN (pembantu): struk final, baris non-batal, ber-jam; header paket (menu ber-packages)
-- dikecualikan (komponennya yang dihitung). key_kind 'code' bila menu_code ada di pemetaan kode, selain itu 'name'.
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_fin_line AS
SELECT
    l.sales_date AS sales_date, l.brand AS brand, l.company_code AS company_code, l.branch_id AS branch_id,
    l.branch_code AS branch_code, toUInt8(assumeNotNull(l.hour)) AS hr, l.menu_code AS menu_code, l.qty AS qty,
    if(l.menu_code != '' AND (l.brand, l.menu_code) IN (SELECT brand, match_key FROM efn_dwh.v_esb_oms_menu_fin WHERE key_kind = 'code'), 'code', 'name') AS key_kind,
    if(l.menu_code != '' AND (l.brand, l.menu_code) IN (SELECT brand, match_key FROM efn_dwh.v_esb_oms_menu_fin WHERE key_kind = 'code'), l.menu_code, l.menu_key) AS match_key
FROM efn_dwh.v_esb_oms_line AS l
WHERE l.bill_final = 1 AND l.is_cancelled = 0 AND l.hour IS NOT NULL
  AND NOT (l.line_kind = 'menu' AND l.has_packages = 1);

-- Terjual per jam per produk FIN (EA bila uom resep EA; uom ikut keluaran). product_key = kode produk ESB Core
-- (sama dengan v_esb_flow_daily.product_code).
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_fin_hourly AS
SELECT
    f.s_date AS sales_date, f.s_brand AS brand, f.c_code AS company_code, f.b_id AS branch_id,
    f.b_code AS branch_code, f.h_r AS hour, f.p_code AS product_key, f.p_name AS product_name, f.p_uom AS uom,
    f.qty_f AS qty_fin
FROM
(
    SELECT e.sales_date AS s_date, e.brand AS s_brand, e.company_code AS c_code, e.branch_id AS b_id,
           e.branch_code AS b_code, e.hr AS h_r, mp.product_code AS p_code, any(mp.product_name) AS p_name,
           any(mp.uom) AS p_uom, sum(toFloat64(e.qty) * toFloat64(mp.qty_per_menu)) AS qty_f
    FROM efn_dwh.v_esb_oms_fin_line AS e
    INNER JOIN efn_dwh.v_esb_oms_menu_fin AS mp
        ON mp.brand = e.brand AND mp.key_kind = e.key_kind AND mp.match_key = e.match_key
    WHERE mp.product_code != ''
    GROUP BY s_date, s_brand, c_code, b_id, b_code, h_r, p_code
) AS f;

-- Kualitas pemetaan per cabang-hari: menu_qty = Σ qty baris yang dihitung; fin_mapped_qty = cocok BOM dengan komponen
-- FIN; no_fin_qty = cocok BOM tanpa FIN (mis. minuman); unmapped_qty = tanpa BOM cocok; no_code_qty = menu_code kosong
-- (informasi, tumpang-tindih dengan kategori lain); unmapped_pct = unmapped_qty / menu_qty × 100. qty_fin vs
-- core_fin_sold_qty (fact_esb_fin_sold_daily Core hari itu) = cek silang.
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_fin_mapping_quality AS
SELECT
    q.s_date AS sales_date, q.s_brand AS brand, q.c_code AS company_code, q.b_id AS branch_id,
    q.b_code AS branch_code, q.menu_q AS menu_qty, q.fin_q AS fin_mapped_qty, q.nofin_q AS no_fin_qty,
    q.unm_q AS unmapped_qty, q.nocode_q AS no_code_qty,
    if(q.menu_q = 0, NULL, round(q.unm_q / q.menu_q * 100, 2)) AS unmapped_pct,
    x.qty_x AS qty_fin, c.sold_c AS core_fin_sold_qty
FROM
(
    SELECT e.sales_date AS s_date, e.brand AS s_brand, e.company_code AS c_code, e.branch_id AS b_id,
           e.branch_code AS b_code, sum(toFloat64(e.qty)) AS menu_q,
           sumIf(toFloat64(e.qty), k.has_fin = 1) AS fin_q,
           sumIf(toFloat64(e.qty), k.hit = 1 AND k.has_fin = 0) AS nofin_q,
           sumIf(toFloat64(e.qty), k.hit = 0) AS unm_q,
           sumIf(toFloat64(e.qty), e.menu_code = '') AS nocode_q
    FROM efn_dwh.v_esb_oms_fin_line AS e
    LEFT JOIN
    (
        SELECT brand, key_kind, match_key, toUInt8(1) AS hit, toUInt8(max(product_code != '')) AS has_fin
        FROM efn_dwh.v_esb_oms_menu_fin
        GROUP BY brand, key_kind, match_key
    ) AS k
        ON k.brand = e.brand AND k.key_kind = e.key_kind AND k.match_key = e.match_key
    GROUP BY s_date, s_brand, c_code, b_id, b_code
) AS q
LEFT JOIN
(
    SELECT brand, branch_code, sales_date, sum(qty_fin) AS qty_x
    FROM efn_dwh.v_esb_oms_fin_hourly
    GROUP BY brand, branch_code, sales_date
) AS x
    ON x.brand = q.s_brand AND x.branch_code = q.b_code AND x.sales_date = q.s_date
LEFT JOIN
(
    SELECT brand, branch_id, sales_date, sum(toFloat64(qty_ea)) AS sold_c
    FROM efn_dwh.fact_esb_fin_sold_daily FINAL
    WHERE (brand, toString(branch_id), sales_date, _version) IN
          (SELECT brand, scope_key, day, finished_at FROM efn_esb.extract_checkpoint FINAL WHERE entity = 'fin_sold')
    GROUP BY brand, branch_id, sales_date
) AS c
    ON c.brand = q.s_brand AND c.branch_id = q.b_id AND c.sales_date = q.s_date;

-- Rekonsiliasi per cabang-hari (dasar = baris sales-branch-summary): struk final OMS vs ringkasan OMS vs Σ amount
-- pembayaran ESB Core (fact_esb_payment_daily, branch_code sama [B]). diff_*_pct = (OMS - pembanding) / pembanding × 100.
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_recon_daily AS
SELECT
    s.sales_date AS sales_date, s.brand AS brand, s.company_code AS company_code, s.branch_id AS branch_id,
    s.branch_code AS branch_code, if(br.branch_name = '', s.branch_code, br.branch_name) AS branch_name,
    r.bills_o AS bills_oms, r.gt_o AS grand_total_oms, s.bill_total AS bills_summary,
    s.grand_total AS grand_total_summary, p.amt_p AS core_payment_amount,
    if(s.bill_total = 0, NULL, round((toFloat64(r.bills_o) - s.bill_total) / s.bill_total * 100, 3)) AS diff_bills_pct,
    if(s.grand_total = 0, NULL,
       round((toFloat64(r.gt_o) - toFloat64(s.grand_total)) / toFloat64(s.grand_total) * 100, 3)) AS diff_summary_pct,
    if(p.amt_p = 0, NULL, round((toFloat64(r.gt_o) - toFloat64(p.amt_p)) / toFloat64(p.amt_p) * 100, 3)) AS diff_core_pct
FROM
(
    SELECT brand, company_code, branch_id, branch_code, sales_date, bill_total, grand_total
    FROM efn_dwh.fact_esb_oms_branch_daily FINAL
    WHERE (brand, sales_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'oms_branch_daily' AND scope_key = '*')
) AS s
LEFT JOIN
(
    SELECT brand, branch_code, sales_date, count() AS bills_o, sum(grand_total) AS gt_o
    FROM efn_dwh.v_esb_oms_bill
    WHERE is_final = 1
    GROUP BY brand, branch_code, sales_date
) AS r
    ON r.brand = s.brand AND r.branch_code = s.branch_code AND r.sales_date = s.sales_date
LEFT JOIN
(
    SELECT brand, branch_code, sales_date, sum(amount) AS amt_p
    FROM efn_dwh.fact_esb_payment_daily FINAL
    WHERE (brand, sales_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'payment' AND scope_key = '*')
    GROUP BY brand, branch_code, sales_date
) AS p
    ON p.brand = s.brand AND p.branch_code = s.branch_code AND p.sales_date = s.sales_date
LEFT JOIN (SELECT brand, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL) AS br
    ON br.brand = s.brand AND br.branch_id = s.branch_id;

-- Kesegaran per cabang (hari ini WIB): struk terakhir keluar + checkpoint OMS terakhir. Per cabang (bukan per PT)
-- agar row policy kelas BR (persona Toko F&B) bisa membacanya; ambil max untuk tingkat PT.
CREATE OR REPLACE VIEW efn_dwh.v_esb_oms_freshness AS
SELECT
    f.brand AS brand, f.c_code AS company_code, f.b_id AS branch_id, f.branch_code AS branch_code,
    f.max_out AS max_sales_ts_out_today, c.last_f AS last_finished_at
FROM
(
    SELECT brand, branch_code, any(company_code) AS c_code, any(branch_id) AS b_id, max(sales_ts_out) AS max_out
    FROM efn_dwh.v_esb_oms_bill
    WHERE sales_date = toDate(now(), 'Asia/Jakarta')
    GROUP BY brand, branch_code
) AS f
LEFT JOIN
(
    SELECT brand, scope_key, max(finished_at) AS last_f
    FROM efn_esb.extract_checkpoint FINAL
    WHERE entity = 'oms_sales' AND day = toDate(now(), 'Asia/Jakarta')
    GROUP BY brand, scope_key
) AS c
    ON c.brand = f.brand AND c.scope_key = f.branch_code;
