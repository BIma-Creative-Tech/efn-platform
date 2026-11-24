-- Gelombang 67 "Margin bahan" (docs/04 §31.3, L-259..L-264; turunan baca di atas ADR-040 + ADR-041, keduanya Proposed).
-- Idempoten (CREATE ... IF NOT EXISTS / CREATE OR REPLACE VIEW). Dijalankan clickhouse-init.sh setiap deploy setelah
-- 01..23 (glob /init/schema/*.sql, urutan nama) dan SEBELUM access/*.sql (wrapper efn_insight membaca view di bawah).
--
-- Penulis: HANYA `python -m efn_esb refresh-margin` / jadwal mingguan loop `run` (services/esb-extract/efn_esb/margin.py),
-- role esb_writer = SELECT + INSERT (tanpa CREATE/DROP/ALTER/TRUNCATE, pola SEC-AG-01; L-263). Tabel dibuat admin di
-- sini: engine MergeTree + storage_policy 'encrypted' (ADR-022) tidak bisa dipilih esb_writer; clickhouse-init.sh GAGAL
-- (fail-closed, hak esb_writer dicabut) bila salah satu tabel bukan MergeTree/encrypted.
-- Versi-insert (L-263): setiap refresh satu brand meng-INSERT versi baru (`_version` = mulai run, UTC) ke ketiga tabel;
-- view efn_dwh.v_esb_* di bawah hanya menampilkan `_version = max(_version)` per brand. Satu INSERT per tabel per refresh
-- (konsisten per tabel); refresh yang gagal di tengah dapat membuat versi terakhir tiga tabel berbeda sampai refresh ok
-- berikutnya (catatan kesegaran efn_esb.aggregate_refresh name 'margin_<brand>' berstatus error).
-- Kolom (urutan = kontrak view §31.3) diuji sama dengan efn_esb/margin.py (*_COLUMNS) di tests/test_margin.py.
-- company_code = PT brand dari dim_esb_branch (PT dengan cabang aktif terbanyak; satu PT per brand, lihat margin.py).
-- PDP/komersial: tanpa nama supplier (supplier perorangan = PII, §27.13) dan tanpa identitas orang. Harga beli & biaya
-- resep = data komersial sensitif (L-260): hanya esb_writer (tulis) + efn_insight_definer (baca, wrapper DEFINER + row
-- policy kelas PT); TIDAK di-grant ke metabase_reader / text2sql / forecast / mig / segment.
-- Retensi: TTL 60 hari per baris (versi lama terbuang sendiri; versi terbaru diperbarui mingguan). Bila refresh berhenti
-- > 60 hari, view menjadi kosong (fail-visible, bukan data basi diam-diam).

-- Langkah 1-2 §31.2: harga bahan bulanan (satuan beli dominan) + indeks terhadap bulan dasar.
CREATE TABLE IF NOT EXISTS efn_dwh.mart_esb_ing_price_monthly
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    month          Date,                            -- tanggal 1
    product_code   String,
    product_name   String,                          -- nama PO terbaru, kode dalam kurung dibuang
    ing_group      LowCardinality(String),          -- sub_category_name -> category_name -> 'Lainnya'
    uom            LowCardinality(String),          -- satuan beli dominan (nilai terbesar)
    receipt_qty    Decimal(18, 3),                  -- bulan ini (0 bila tidak ada penerimaan)
    value          Decimal(18, 2),                  -- Σ price × receipt_qty bulan ini
    price          Decimal(18, 4),                  -- rata-rata berbobot bulan ini (0 bila tidak ada penerimaan)
    price_4m       Decimal(18, 4),                  -- rata-rata berbobot 4 bulan bergulir (bulan ini + 3 sebelumnya)
    idx            Nullable(Float64),               -- price_4m / price_4m bulan dasar
    usage_value    Float64,                         -- Rp/bulan nilai pakai di resep (keranjang tetap, harga dasar)
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (brand, company_code, _version, month, product_code)
TTL toDateTime(_loaded_at) + INTERVAL 60 DAY
SETTINGS storage_policy = 'encrypted';

-- Langkah 4-5: menu (nama kanonik) per bulan; cost_idx hanya untuk menu ber-BOM (bom_matched = 1).
CREATE TABLE IF NOT EXISTS efn_dwh.mart_esb_menu_cost_monthly
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    month          Date,
    menu_name      String,                          -- kanonik: huruf besar, spasi tunggal, tanpa ' T' / 'KIOSK - '
    menu_category  String,
    qty            Decimal(18, 3),
    net            Decimal(18, 2),
    cogs           Decimal(18, 2),                  -- COGS ESB (agg_esb_sales_daily)
    bom_matched    UInt8,
    cost_idx       Nullable(Float64),
    leaf_coverage  Float64,                         -- porsi nilai daun BOM yang punya harga PO (0 bila tanpa BOM)
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (brand, company_code, _version, month, menu_name)
TTL toDateTime(_loaded_at) + INTERVAL 60 DAY
SETTINGS storage_policy = 'encrypted';

-- Langkah 6: atribusi kelompok bahan (Laspeyres, keranjang tetap), Rupiah per bulan. Kontribusi = basket_value -
-- base_value (dihitung pembaca). Daun tanpa harga PO = kelompok 'Tanpa harga PO' (idx 1, kontribusi 0).
CREATE TABLE IF NOT EXISTS efn_dwh.mart_esb_ing_group_monthly
(
    brand          LowCardinality(String),
    company_code   LowCardinality(String),
    month          Date,
    ing_group      LowCardinality(String),
    basket_value   Float64,
    base_value     Float64,
    _version       DateTime64(3, 'UTC'),
    _loaded_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (brand, company_code, _version, month, ing_group)
TTL toDateTime(_loaded_at) + INTERVAL 60 DAY
SETTINGS storage_policy = 'encrypted';

-- Versi terakhir per brand, kolom = tabel tanpa _version/_loaded_at (sumber wrapper efn_insight.* DEFINER).
CREATE OR REPLACE VIEW efn_dwh.v_esb_ing_price_monthly AS
SELECT t.brand AS brand, t.company_code AS company_code, t.month AS month, t.product_code AS product_code,
       t.product_name AS product_name, t.ing_group AS ing_group, t.uom AS uom, t.receipt_qty AS receipt_qty,
       t.value AS value, t.price AS price, t.price_4m AS price_4m, t.idx AS idx,
       t.usage_value AS usage_value
FROM efn_dwh.mart_esb_ing_price_monthly AS t
WHERE (t.brand, t._version) IN
      (SELECT brand, max(_version) FROM efn_dwh.mart_esb_ing_price_monthly GROUP BY brand);

CREATE OR REPLACE VIEW efn_dwh.v_esb_menu_cost_monthly AS
SELECT t.brand AS brand, t.company_code AS company_code, t.month AS month, t.menu_name AS menu_name,
       t.menu_category AS menu_category, t.qty AS qty, t.net AS net, t.cogs AS cogs, t.bom_matched AS bom_matched,
       t.cost_idx AS cost_idx, t.leaf_coverage AS leaf_coverage
FROM efn_dwh.mart_esb_menu_cost_monthly AS t
WHERE (t.brand, t._version) IN
      (SELECT brand, max(_version) FROM efn_dwh.mart_esb_menu_cost_monthly GROUP BY brand);

CREATE OR REPLACE VIEW efn_dwh.v_esb_ing_group_monthly AS
SELECT t.brand AS brand, t.company_code AS company_code, t.month AS month, t.ing_group AS ing_group,
       t.basket_value AS basket_value, t.base_value AS base_value
FROM efn_dwh.mart_esb_ing_group_monthly AS t
WHERE (t.brand, t._version) IN
      (SELECT brand, max(_version) FROM efn_dwh.mart_esb_ing_group_monthly GROUP BY brand);
