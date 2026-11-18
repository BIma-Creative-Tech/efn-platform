-- Gelombang 43 fase 2 (docs/04 §27.10/§27.11, ADR-042): tabel entitas migrasi ESB -> Odoo + map_esb_odoo kunci baru.
-- Idempoten (CREATE ... IF NOT EXISTS). Dijalankan clickhouse-init.sh setiap deploy setelah 01..13.
-- Penulis: esb-extract (`python -m efn_esb snapshot`, role esb_writer) untuk dim/fact; mesin migrasi
-- (scripts/migration/esb_to_odoo.py writeback, user efn_mig role mig_rw) HANYA untuk map_esb_odoo.
--
-- Snapshot: satu slice per (brand, entitas, hari) -> semua baris slice membawa `_version` yang sama. Baris yang hilang di
-- ESB tetap ada sebagai versi lama; pembaca (export migrasi) memilih versi terakhir per brand (dim) atau versi
-- ber-checkpoint (fact P2), pola 12-g41-esb.sql.
-- PDP (L-110/L-132): customer = kode, nama perusahaan, kategori, termin, status (tanpa alamat/PIC/telepon/email/NPWP);
-- AP/AR = kode supplier/pelanggan saja; stock opname tanpa nama petugas/lampiran. Data keuangan per dokumen ->
-- TIDAK di-grant ke metabase_reader / efn_insight (docs/04 §27.11). Retensi: ikut DWH (#59); tabel P2 boleh dikosongkan
-- setelah berita acara brand ditandatangani (keputusan Finance/DPO, Q-ESB24).
-- Tipe [B]: arti kolom P2 (qty vs stockQty, receivableTotal = sisa atau nilai faktur) belum diverifikasi ESB nyata.

-- ============================================================== P1 (sebelum dry-run master)
-- E1 `/product/bom/{bomID}` header. product_code = produk hasil BOM; bom_type_* = jenis ESB (arti [B], Q-ESB17).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_bom
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    bom_id         UInt64,
    bom_code       String,
    bom_name       String,
    bom_type_id    Int32,
    bom_type_name  LowCardinality(String),
    product_code   String,
    product_name   String,
    uom            LowCardinality(String),
    active         UInt8,
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, bom_id);

-- E1 komponen (`bomDetails`). line_id = ID detail ESB (tanpa ID -> 1e9 + posisi). qty dalam `uom` komponen;
-- uom_qty/convertion_qty = konversi ESB (arti [B]); last_hpp = HPP terakhir per satuan komponen (sumber standard_price).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_bom_line
(
    brand              LowCardinality(String),
    company_code       LowCardinality(String),
    bom_id             UInt64,
    line_id            UInt64,
    product_code       String,
    product_name       String,
    qty                Decimal(18, 6),
    uom                LowCardinality(String),
    uom_qty            Decimal(18, 6),
    convertion_qty     Decimal(18, 6),
    last_hpp           Decimal(18, 4),
    yield_percent      Decimal(9, 3),
    tolerance_percent  Decimal(9, 3),
    _version           DateTime64(3, 'UTC'),
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, bom_id, line_id);

-- E2 `/menu/browse-for-product`. Tabel terpisah dari dim_esb_menu (penulis berbeda: master harian vs snapshot migrasi;
-- satu tabel ReplacingMergeTree dengan dua penulis akan saling menimpa kolom). price = Rupiah, satu harga per menu.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_menu_price
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    menu_code     String,
    menu_id       UInt64,
    menu_name     String,
    price         Decimal(18, 2),
    bom_name      String,
    active        UInt8,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, menu_code);

-- E4 `/customer` (whitelist normalize.CUSTOMER_FIELDS). customer_name = nama pelanggan B2B (bisa berupa nama orang
-- untuk pelanggan perorangan -> data pribadi; grant hanya esb_writer + mig_rw).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_customer
(
    brand              LowCardinality(String),
    company_code       LowCardinality(String),
    customer_code      String,
    customer_name      String,
    customer_category  LowCardinality(String),
    payment_due_days   UInt16,
    lock_vat           UInt8,
    active             UInt8,
    _version           DateTime64(3, 'UTC'),
    _loaded_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, customer_code);

-- E5 `/location?branchID=` (respons: locationID + locationName).
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_location
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    branch_id      UInt32,
    location_id    UInt64,
    location_name  String,
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, branch_id, location_id);

-- ============================================================== P2 (sebelum cutover; extractor default mati)
-- E9 `/accounting/coa`.
CREATE TABLE IF NOT EXISTS efn_dwh.dim_esb_coa
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    coa_no        String,
    coa_name      String,
    coa_level     Int32,
    currency      LowCardinality(String),
    active        UInt8,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, coa_no);

-- E6 stock opname sekitar cutover. as_of_date = hari snapshot (cutover - 1); opname_date = tanggal dokumen.
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_stock_opname
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    as_of_date    Date,
    branch_id     UInt32,
    location_id   UInt64,
    opname_date   Date,
    doc_num       String,
    status_id     Int32,
    product_code  String,
    uom           LowCardinality(String),
    qty_counted   Decimal(18, 3),
    qty_system    Decimal(18, 3),
    hpp           Decimal(18, 4),
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(as_of_date)
ORDER BY (company_code, brand, as_of_date, doc_num, product_code, uom);

-- E7 faktur pembelian terbuka per tanggal saldo (supplier = kode saja).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_ap_open
(
    brand                 LowCardinality(String),
    company_code          LowCardinality(String),
    as_of_date            Date,
    branch_id             UInt32,
    doc_num               String,
    supplier_code         String,
    supplier_invoice_num  String,
    invoice_date          Nullable(Date),
    due_date              Nullable(Date),
    currency              LowCardinality(String),
    payable_total         Decimal(18, 2),
    payment_total         Decimal(18, 2),
    _version              DateTime64(3, 'UTC'),
    _loaded_at            DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(as_of_date)
ORDER BY (company_code, brand, as_of_date, branch_id, doc_num);

-- E8 faktur penjualan B2B terbuka per tanggal saldo (pelanggan = kode saja).
CREATE TABLE IF NOT EXISTS efn_dwh.fact_esb_ar_open
(
    brand             LowCardinality(String),
    company_code      LowCardinality(String),
    as_of_date        Date,
    branch_id         UInt32,
    doc_num           String,
    customer_code     String,
    invoice_date      Nullable(Date),
    due_date          Nullable(Date),
    currency          LowCardinality(String),
    receivable_total  Decimal(18, 2),
    payment_total     Decimal(18, 2),
    advance_total     Decimal(18, 2),
    _version          DateTime64(3, 'UTC'),
    _loaded_at        DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(as_of_date)
ORDER BY (company_code, brand, as_of_date, branch_id, doc_num);

-- ============================================================== Pemetaan ESB -> Odoo (L-128, L-130)
-- Salinan hasil impor (sumber kebenaran = xmlid `efn_mig_esb.*` di Odoo). Kunci dengan company_code: produk kembar
-- BCF/BCR tidak saling menimpa. esb_key = kode ESB ASLI (bukan key ternormalisasi); odoo_xmlid lengkap. Penulis tunggal:
-- user efn_mig (writeback). Tabel G41 (kunci lama, kosong) dihapus clickhouse-init.sh sebelum file ini dijalankan.
CREATE TABLE IF NOT EXISTS efn_dwh.map_esb_odoo
(
    brand         LowCardinality(String),
    company_code  LowCardinality(String),
    entity        LowCardinality(String),
    esb_key       String,
    odoo_model    LowCardinality(String),
    odoo_xmlid    String,
    odoo_id       UInt64,
    run_id        UUID,
    _version      DateTime64(3, 'UTC'),
    _loaded_at    DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY (brand, company_code, entity, esb_key);
