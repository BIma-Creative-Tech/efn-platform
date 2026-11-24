-- Gelombang 65 (docs/04 §27.13.2, ADR-042 amandemen §0b, L-250..L-258): histori pembelian + saldo stok awal ESB (PB).
-- Idempoten (ALTER ... ADD COLUMN IF NOT EXISTS / CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan
-- clickhouse-init.sh setiap deploy setelah 01..22 (glob /init/schema/*.sql, urutan nama).
-- Penulis: esb-extract (role esb_writer). Pembaca: mesin migrasi (role mig_rw). TIDAK di-grant ke metabase_reader,
-- efn_insight / efn_insight_definer, forecast_reader (L-258: harga beli per dokumen = data komersial sensitif; nama
-- supplier perorangan = data pribadi).
-- PDP (L-110): extractor me-whitelist kunci SEBELUM disimpan (notes/additionalInfo/costCenterName/projectName/
-- createdBy/editedBy/authorizer*/creator*/emailSupplier/printedBy dibuang). Retensi: ikut DWH (#59); raw_doc TTL 36 bln.
-- Pembaca wajib pola §25.4: FINAL + hanya versi slice ber-checkpoint (purchase_line scope '*', stock_opening scope
-- '<branch_id>').
-- [B] belum diverifikasi: arti `discount` (nominal/persen), PPN di `total`, `amountBalance` bulat, arti default
-- `unitToShow` (satuan `uom` = UOM baris apa adanya).

-- supplier_id = `supplierID` /supplier (nama field [B]); diisi entitas `master`. 0 = belum diisi/tidak ada.
ALTER TABLE efn_dwh.dim_esb_supplier ADD COLUMN IF NOT EXISTS supplier_id UInt64 DEFAULT 0;

-- Satu slice = brand x hari PO (purchase_date = hari slice). line_no = 1..n per purchase_num setelah diurutkan
-- (productCode, uomName, qty, price, total; pemecah seri discount, vatValue, receiptQty, productName).
-- branch_id/supplier_id dari header PO (map_source 'header'); fallback nama tepat-satu ('name'); gagal ('none':
-- branch_id 0 / supplier_code ''). company_code = PT cabang (branch_id 0 -> PT default brand).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_purchase_line
(
    brand              LowCardinality(String),
    company_code       LowCardinality(String),
    purchase_date      Date,
    purchase_num       String,
    line_no            UInt32,
    required_date      Nullable(Date),
    branch_id          UInt32,
    branch_name        String,
    supplier_id        UInt64,
    supplier_code      String,
    supplier_name      String,                      -- nama perusahaan (supplier perorangan = PII -> grant terbatas)
    status_id          Int32,                       -- dari header PO (0 bila header tidak ditemukan)
    status_name        LowCardinality(String),
    currency           LowCardinality(String),      -- header `currencySign` (IDR/USD, '' tanpa header)
    product_code       String,
    product_name       String,
    category_name      String,
    sub_category_name  String,
    uom                LowCardinality(String),
    qty                Decimal(18, 3),
    receipt_qty        Decimal(18, 3),
    pricelist_price    Decimal(18, 4),
    price              Decimal(18, 4),
    discount           Decimal(18, 2),              -- nominal vs persen [B], apa adanya
    vat_value          Decimal(18, 2),
    total              Decimal(18, 2),              -- termasuk PPN? [B], apa adanya
    map_source         LowCardinality(String),      -- header | name | none
    _version           DateTime64(3, 'UTC'),
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(purchase_date)
ORDER BY (company_code, brand, purchase_date, purchase_num, line_no);

-- Saldo AWAL hari opening_date (= tanggal cutover) = saldo akhir hari sebelumnya; baris `Beginning` qty != 0 saja.
-- Satu slice = cabang x hari (snapshot --entity stock_opening --date <cutover>).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_stock_opening
(
    brand              LowCardinality(String),
    company_code       LowCardinality(String),
    opening_date       Date,
    branch_id          UInt32,
    branch_code        String,
    location_name      String,
    product_id         UInt64,
    product_detail_id  UInt64,
    product_code       String,
    product_name       String,
    uom                LowCardinality(String),
    value_per_unit     Decimal(18, 4),
    qty_balance        Decimal(18, 3),
    amount_balance     Decimal(18, 2),
    _version           DateTime64(3, 'UTC'),
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(opening_date)
ORDER BY (company_code, brand, opening_date, branch_id, location_name, product_code, product_detail_id, uom);

-- Cek kualitas G65 (bentuk kolom = v_esb_data_quality). View TERPISAH, bukan tambahan v_esb_data_quality: view itu
-- INVOKER dan dibaca metabase_reader + efn_insight_definer, yang sengaja TIDAK punya hak atas tabel G65 (L-258) ->
-- menambah UNION ke sana akan membuat view itu gagal untuk mereka. Hanya mig_rw yang membaca view ini.
CREATE OR REPLACE VIEW efn_dwh.v_esb_g65_quality AS
-- 1. purchase_unmapped (30 hari): item = 'map_source:<name|none>', value = jumlah baris non-header.
SELECT 'purchase_unmapped' AS check_name, u.brand AS brand, u.company_code AS company_code, u.item AS item,
       toFloat64(u.lines_u) AS value, u.last_u AS last_day
FROM
(
    SELECT brand, company_code, concat('map_source:', map_source) AS item, count() AS lines_u,
           max(purchase_date) AS last_u
    FROM efn_dwh.fact_esb_purchase_line FINAL
    WHERE map_source != 'header' AND purchase_date >= today() - 30
      AND (brand, purchase_date, _version) IN
          (SELECT brand, day, finished_at FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'purchase_line' AND scope_key = '*')
    GROUP BY brand, company_code, item
) AS u
UNION ALL
-- 2. stock_opening_missing_branch: cabang aktif tanpa checkpoint stock_opening pada opening_date terakhir brand itu.
SELECT 'stock_opening_missing_branch', x.brand, x.company_code, concat(toString(x.branch_id), ' ', x.branch_name),
       toFloat64(1), x.last_d
FROM
(
    SELECT b.brand AS brand, b.company_code AS company_code, b.branch_id AS branch_id, b.branch_name AS branch_name,
           l.last_d AS last_d
    FROM (SELECT brand, company_code, branch_id, branch_name FROM efn_dwh.dim_esb_branch FINAL WHERE active = 1) AS b
    INNER JOIN (SELECT brand, max(day) AS last_d FROM efn_esb.extract_checkpoint FINAL
                WHERE entity = 'stock_opening' GROUP BY brand) AS l
        ON l.brand = b.brand
) AS x
LEFT JOIN (SELECT brand, scope_key, day, toUInt8(1) AS hit FROM efn_esb.extract_checkpoint FINAL
           WHERE entity = 'stock_opening') AS c
    ON c.brand = x.brand AND c.scope_key = toString(x.branch_id) AND c.day = x.last_d
WHERE c.hit = 0;
