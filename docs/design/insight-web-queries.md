# Katalog query Insight Web (G42) — `efn_insight` ClickHouse

Pemilik: efn-data-ai-engineer. Konsumen: efn-frontend-engineer (`services/insight-web/lib/queries.ts`).
Kontrak: G42 bagian B/C, ADR-041 (Proposed), ADR-040 (Proposed), spesifikasi UI `docs/design/insight-web.md`.
Sumber: `infra/clickhouse/access/insight-access.sql` (view `efn_insight.*`), `infra/clickhouse/init/12-g41-esb.sql`
dan `13-g42-insight.sql` (view `efn_dwh.v_esb_*`), row policy `efn_insight_scope` di `infra/clickhouse/clickhouse-init.sh`.

> Status verifikasi: SQL di dokumen ini ditulis tanpa shell; lead menjalankan **36 query katalog** (`lib/queries.ts`)
> dan uji row policy terhadap ClickHouse 24.8 (18 Nov 2026) — lulus. Pengecualian: `sales_menu_daily` (§3.3) dan
> `dq_status_board` (§3.6) **tidak ada di katalog kode** (belum dibuat / tidak dipakai), jadi tidak termasuk yang dijalankan.
> Konkurensi: profil `insight_readonly` `max_concurrent_queries_for_user = 24`; insight-web membatasi 12 query in-flight
> (`INSIGHT_CH_MAX_CONCURRENCY`, 1..24).

## 1. Aturan pemakaian (wajib)

1. Semua query **terdefinisi di kode** (katalog bernama di bawah). Tidak ada SQL atau potongan SQL dari user. Nilai
   filter hanya lewat parameter ClickHouse `{nama:Tipe}` + parameter HTTP `param_nama` (ClickHouse mem-parse nilai
   sesuai tipe; bukan interpolasi string).
2. Database `efn_insight` (parameter HTTP `database=efn_insight`); tulis nama view lengkap `efn_insight.v_esb_*`.
3. Setiap request membawa setting cakupan dari **sesi server** (bukan dari client):
   `SQL_efn_cids` (company_id Odoo terurut, dipisah koma), `SQL_efn_codes` (kode PT terurut, dipisah koma),
   `SQL_efn_sig = hex(SHA256(insight_scope_key + "|" + cids + "|" + codes))` (huruf kecil). Format **sama** dengan
   `services/text2sql/efn_text2sql/scope.py` (lihat test vector `services/text2sql/tests/test_scope.py`: kunci
   `"ab"*16`, cids `1,3`, codes `BCR,PB`), kuncinya berbeda (`/run/secrets/insight_scope_key`).
   Untuk user `all_brands`: codes = `BCF,BCR,CUP,IMD,PB,WTZ`, cids = company_id yang ada di Odoo saja (IMD/CUP belum
   punya company sampai G43). `SQL_efn_cids` boleh string kosong untuk `all_brands` tanpa company (docs/04 §26.7);
   row policy insight hanya memeriksa `company_code`, tetapi cids ikut ditandatangani.
   Kunci = string hex apa adanya setelah strip (byte ASCII, bukan hex-decode), ≥ 64 karakter.
   Tanpa setting -> error ClickHouse (getSetting gagal); tanda tangan salah -> 0 baris; baris `company_code=''` tidak
   pernah terlihat (`company_code != ''` di policy, C4 §26.13). Semuanya fail closed.
4. Row policy sudah membatasi PT (lapis 2). Setiap query katalog tetap memfilter `has({pt:Array(String)}, company_code)`
   (lapis 1, docs/04 §26.8); `pt` = filter URL `pt` ⊆ codes sesi, kosong = semua codes sesi (§26.9).
   Nama parameter = nama filter URL §26.9: `from`, `to`, `pt`, `branch`, `cat`, `product` (+ `prev_from`, `prev_to`
   dihitung server; `product_key`, `menus`, `sort`, `weekly`, `by_branch`, `check` khusus query tertentu).
5. **Kunci cabang dan produk = brand + id**: `branch_id` dan `product_code` hanya unik dalam satu akun ESB (brand).
   Parameter filter cabang/produk dikirim sebagai `Array(String)` berformat `'<brand>:<branch_id>'` /
   `'<brand>:<product_code>'` (mis. `'pb:4'`, `'pb:FG-001'`). Array kosong = semua.
6. Setting HTTP yang disarankan per request: `default_format=JSON`, `output_format_json_quote_64bit_integers=0`
   (UInt64 dari `count`/`uniqExact`/`sum(UInt*)` tidak dikutip sebagai string), `max_execution_time=30`.
   Semua uang/qty di katalog di-cast `toFloat64` (JSON number). Profil `insight_readonly` membatasi
   `max_execution_time` ≤ 60, `max_result_rows` 200 000 (`result_overflow_mode=throw`: melebihi = error, bukan
   terpotong), kuota per jam bersama untuk seluruh service (rate-limit per staf = tanggung jawab insight-web).
7. `LIMIT` di katalog **ditulis literal** (10/500). Paginasi tabel = di aplikasi atas hasil ≤ 500 baris. (Parameter
   di `LIMIT` belum diverifikasi di 24.8, jadi tidak dipakai.)
8. Pilihan urutan (`sort`) dikirim sebagai `{sort:String}` yang divalidasi server terhadap enum (`net|qty|margin`);
   query memakai `multiIf`, bukan menyusun `ORDER BY` dari string.

### Contoh request HTTP

```
POST https://clickhouse:8443/?database=efn_insight&default_format=JSON
     &output_format_json_quote_64bit_integers=0&max_execution_time=30
     &SQL_efn_cids=1&SQL_efn_codes=PB&SQL_efn_sig=<64 hex>
     &param_from=2026-10-18&param_to=2026-11-16
     &param_prev_from=2026-09-18&param_prev_to=2026-10-17
     &param_pt=%5B%27PB%27%5D               (= ['PB'])
     &param_branch=%5B%5D                    (= [])
Header: X-ClickHouse-User: efn_insight
        X-ClickHouse-Key: <isi /run/secrets/clickhouse_password_insight>
        Content-Type: text/plain; charset=utf-8
Body:   <SQL dari katalog>
```

Format nilai parameter: `Date` = `YYYY-MM-DD`; `Array(String)` = literal ClickHouse `['PB','WTZ']` yang disusun
server **hanya dari nilai tervalidasi** (regex `^[a-z0-9]{2,16}:[A-Za-z0-9._:\- ]{1,64}$` untuk kunci cabang/produk;
tolak `'` dan `\`); `UInt8` = `0|1`. Semua di-URL-encode. Body/parameter tidak boleh dicatat di log bersama setting
`SQL_efn_sig`.

### Fragmen filter standar

Tidak bisa dipakai sebagai "include" di ClickHouse; disalin ke tiap query (dibangun di TypeScript dari konstanta,
bukan dari input):

```sql
-- F_SALES (view harian ber-sales_date)
sales_date BETWEEN {from:Date} AND {to:Date}
AND has({pt:Array(String)}, company_code)
AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
```

Untuk view alur (`day`) ganti `sales_date` -> `day`, dan filter produk:
`AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))`.
Filter kategori (hanya view menu): `AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))`.

## 2. Daftar view `efn_insight` dan kolom

Semua view: SQL SECURITY DEFINER (`efn_insight_definer`), kolom eksplisit, row policy `efn_insight_scope`
(`company_code ∈ SQL_efn_codes` AND tanda tangan). Tidak ada kolom identitas orang (L-110).

| View | Butir (grain) | Kolom (tipe ringkas) |
|---|---|---|
| `v_esb_sales_daily` | hari × brand × PT × cabang × menu | `sales_date` Date, `brand` String, `company_code` String, `branch_id` UInt32, `branch_name` String, `menu_code` String, `menu_name` String, `menu_category` String, `menu_category_detail` String, `is_takeaway` UInt8, `qty` Decimal(38,3), `gross` Decimal(38,2), `discount` Decimal(38,2), `net` Decimal(38,2), `cogs` Decimal(38,2), `trx_count` UInt64 (**per menu — jangan dijumlah lintas menu**) |
| `v_esb_sales_branch_daily` (G42) | hari × brand × PT × cabang | `sales_date`, `brand`, `company_code`, `branch_id`, `branch_name`, `trx_count` UInt64 (struk unik per cabang-hari), `line_count` UInt64, `qty`, `gross`, `discount`, `net`, `cogs` |
| `v_esb_payment_daily` | hari × brand × PT × cabang × tipe × metode | `sales_date`, `brand`, `company_code`, `branch_id` (0 = branchCode tak dikenal), `branch_code` String, `branch_name`, `payment_type` String, `payment_method` String, `payment_count` UInt32, `amount` Decimal(18,2), `mdr` Decimal(18,2), `net` Decimal(18,2) |
| `v_esb_flow_daily` | hari × brand × PT × cabang × produk FIN (EA) | `day` Date, `brand`, `company_code`, `branch_id`, `branch_name`, `product_code` String (`NAME:<alias>` = belum dipetakan), `product_name`, `ck_prod`, `ck_out`, `in_ck`, `in_other`, `store_prod`, `st_in`, `st_out`, `sold` (semua Float64) |
| `v_esb_menu_mix` | bulan × brand × PT × cabang × kategori × takeaway | `month` Date (awal bulan), `brand`, `company_code`, `branch_id`, `branch_name`, `menu_category` String (`'Tanpa kategori'` bila kosong), `is_takeaway` UInt8, `qty` Float64, `net` Float64, `share_qty` Nullable(Float64) %, `share_net` Nullable(Float64) % |
| `v_esb_data_quality` | temuan | `check_name` String (`alias_unmapped`, `non_ea_lines`, `store_no_sales_h1`, `data_age_hours`, `last_run_not_ok`), `brand`, `company_code` (**selalu terisi**, lihat §4), `item` String, `value` Float64, `last_day` Date |
| `v_esb_branch_list` (G42) | cabang | `brand`, `company_code`, `branch_id`, `branch_code`, `branch_name`, `is_ck` UInt8, `is_store` UInt8, `active` UInt8 |
| `v_esb_product_list` (G42) | PT × produk | `brand`, `company_code`, `product_code`, `product_name`, `category`, `uom`, `is_fin` UInt8, `active` UInt8 |

Semantik penting:
- `net` = `total − discount` dari ESB `sales-menu-cogs`. **Fakta G41: `total` ESB belum termasuk PB1/service charge**
  (total − discount ≈ 89,6% nilai pembayaran pada sampel prototipe). Jadi `sum(net)` penjualan ≠ `sum(amount)`
  pembayaran; jangan dibandingkan langsung (Q-INS4). Label UI: "Penjualan bersih (sebelum PB1/service)".
- KPI Transaksi & rata-rata per transaksi **wajib** dari `v_esb_sales_branch_daily.trx_count` (Q-INS2 terjawab).
  Dengan filter kategori/menu aktif, jumlah struk tidak tersedia (struk bisa memuat banyak kategori) -> tampilkan
  "n/a" atau "struk yang memuat kategori ini" tidak didukung di G42.
- Alur (`v_esb_flow_daily`): `ck_out` dicatat di **cabang CK asal**, `in_ck` di **toko tujuan** (lihat docs/04 §25.6).
  Selisih kirim vs terima hanya bermakna **per produk/hari lintas cabang**, bukan per baris cabang × produk.
- Tidak ada data per jam (L-112) dan tidak ada stok (stok akhir/awal) -> sell-through berbasis arus barang.

## 3. Katalog query per halaman

Nama query = **nama kanonik docs/04 §26.8** (`exec_kpi`, `flow_summary`, ...). Query tambahan di luar §26.8 ditandai
"(tambahan)" dan wajib dicatat di README insight-web (§26.8). Parameter selalu dari sesi + filter tervalidasi.

### 3.0 Filter global (opsi dropdown)

```sql
-- list_branches  (param: pt)
SELECT brand, company_code, branch_id, branch_name, is_ck, is_store, active
FROM efn_insight.v_esb_branch_list
WHERE has({pt:Array(String)}, company_code)
ORDER BY brand, branch_name
LIMIT 500;

-- list_categories  (param: from, to, pt)
SELECT DISTINCT menu_category
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date} AND has({pt:Array(String)}, company_code)
ORDER BY menu_category
LIMIT 500;

-- list_products  (param: pt; produk FIN untuk halaman alur; dedup brand x produk bila sesi punya BCF+BCR)
SELECT brand, product_code, any(product_name) AS product_label, any(category) AS category_label
FROM efn_insight.v_esb_product_list
WHERE has({pt:Array(String)}, company_code) AND is_fin = 1 AND active = 1
GROUP BY brand, product_code
ORDER BY brand, product_label
LIMIT 500;
```
Catatan: daftar produk > 500 -> ganti dropdown dengan pencarian (`AND positionCaseInsensitive(product_name, {q:String}) > 0`).

### 3.1 Ringkasan (`/`)

```sql
-- exec_kpi  (param: from, to, prev_from, prev_to, pt, branch) -> 1 baris
SELECT
    toFloat64(sumIf(net, sales_date >= {from:Date}))                   AS net_cur,
    toFloat64(sumIf(net, sales_date <= {prev_to:Date}))                AS net_prev,
    sumIf(trx_count, sales_date >= {from:Date})                        AS trx_cur,
    sumIf(trx_count, sales_date <= {prev_to:Date})                     AS trx_prev,
    toFloat64(sumIf(cogs, sales_date >= {from:Date}))                  AS cogs_cur,
    toFloat64(sumIf(cogs, sales_date <= {prev_to:Date}))               AS cogs_prev
FROM efn_insight.v_esb_sales_branch_daily
WHERE sales_date BETWEEN {prev_from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))));
-- Turunan di app: avg = net/trx (null bila trx=0), hpp_pct = cogs/net*100 (null bila net<=0).
-- Syarat: prev_to = from - 1 hari, prev_from = from - (to - from + 1) hari (dihitung server).

-- exec_trend_daily  (param: from, to, prev_from, prev_to, pt, branch) -> harian per brand + periode lalu
SELECT sales_date, brand,
       toFloat64(sum(net)) AS net, sum(trx_count) AS trx
FROM efn_insight.v_esb_sales_branch_daily
WHERE sales_date BETWEEN {prev_from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
GROUP BY sales_date, brand
ORDER BY sales_date, brand
LIMIT 5000;
-- App memisah baris < from sebagai "periode lalu" (digeser sejumlah hari periode).

-- exec_branch_rank  (param: from, to, pt, branch) -> semua cabang; app mengambil top/bottom 10
SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(net)) AS net_sales, sum(trx_count) AS trx,
       if(sum(trx_count) = 0, NULL, toFloat64(sum(net)) / sum(trx_count)) AS avg_trx,
       if(sum(net) <= 0, NULL, toFloat64(sum(cogs)) / toFloat64(sum(net)) * 100) AS hpp_pct
FROM efn_insight.v_esb_sales_branch_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
GROUP BY brand, company_code, branch_id
ORDER BY net_sales DESC
LIMIT 500;

-- exec_pt_share (tambahan)  (param: from, to, pt, branch) -> tampil bila > 1 PT di sesi
SELECT company_code, toFloat64(sum(net)) AS net
FROM efn_insight.v_esb_sales_branch_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
GROUP BY company_code
ORDER BY net DESC
LIMIT 50;

-- exec_sell_through (tambahan)  (param: from, to, pt, branch, product) -> KPI tile ke-5 (definisi Q-INS1)
SELECT toFloat64(sum(sold)) AS sold,
       toFloat64(sum(in_ck + in_other + store_prod + st_in - st_out)) AS supply
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)));
-- App: sell_through = supply > 0 ? sold/supply : null.

-- dq_freshness  (param: pt; dipakai juga di Ringkasan) -> FreshnessBadge (umur data per brand x entitas; dedup BCF/BCR)
SELECT brand, item AS entity, min(value) AS age_hours, max(last_day) AS last_day
FROM efn_insight.v_esb_data_quality
WHERE check_name = 'data_age_hours' AND has({pt:Array(String)}, company_code)
GROUP BY brand, entity
ORDER BY brand, entity
LIMIT 500;
-- ageHours badge = max(age_hours) untuk entity IN ('sales','payment') (atau semua entitas aktif). Tidak ada baris
-- -> null -> status "Tidak diketahui" (bukan "baik").

-- exec_attention (tambahan)  (param: pt) -> cabang tanpa penjualan H-1 + run tidak ok (sell-through merah: flow_by_branch)
SELECT check_name, brand, item, any(value) AS value, max(last_day) AS last_day
FROM efn_insight.v_esb_data_quality
WHERE check_name IN ('store_no_sales_h1', 'last_run_not_ok') AND has({pt:Array(String)}, company_code)
GROUP BY check_name, brand, item
ORDER BY check_name, brand, item
LIMIT 50;
```

### 3.2 Produksi vs Penjualan (`/flow`) — `v_esb_flow_daily`

```sql
-- flow_summary  (param: from, to, prev_from, prev_to, pt, branch, product) -> 1 baris
SELECT
    toFloat64(sumIf(ck_prod, day >= {from:Date}))  AS ck_prod_cur,  toFloat64(sumIf(ck_prod, day <= {prev_to:Date}))  AS ck_prod_prev,
    toFloat64(sumIf(ck_out, day >= {from:Date}))   AS ck_out_cur,   toFloat64(sumIf(ck_out, day <= {prev_to:Date}))   AS ck_out_prev,
    toFloat64(sumIf(in_ck + in_other, day >= {from:Date}))  AS received_cur,
    toFloat64(sumIf(in_ck + in_other, day <= {prev_to:Date})) AS received_prev,
    toFloat64(sumIf(store_prod, day >= {from:Date})) AS store_prod_cur,
    toFloat64(sumIf(st_in, day >= {from:Date}))      AS st_in_cur,
    toFloat64(sumIf(st_out, day >= {from:Date}))     AS st_out_cur,
    toFloat64(sumIf(sold, day >= {from:Date}))     AS sold_cur,     toFloat64(sumIf(sold, day <= {prev_to:Date}))     AS sold_prev,
    toFloat64(sumIf(in_ck + in_other + store_prod + st_in - st_out, day >= {from:Date}))  AS supply_cur,
    toFloat64(sumIf(in_ck + in_other + store_prod + st_in - st_out, day <= {prev_to:Date})) AS supply_prev
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {prev_from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)));
-- Sankey/funnel memakai nilai *_cur di atas (tanpa query tambahan).

-- flow_by_branch  (param: from, to, pt, branch, product)
SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(in_ck + in_other + store_prod + st_in - st_out)) AS supply,
       toFloat64(sum(sold)) AS sold_qty,
       if(sum(in_ck + in_other + store_prod + st_in - st_out) <= 0, NULL,
          sum(sold) / sum(in_ck + in_other + store_prod + st_in - st_out) * 100) AS sell_through_pct
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY brand, company_code, branch_id
HAVING supply != 0 OR sold_qty != 0
ORDER BY sell_through_pct ASC NULLS LAST
LIMIT 500;
-- Cabang CK (ck_prod/ck_out tanpa sold) biasanya supply 0 -> NULL ("n/a").

-- flow_daily_trend (tambahan)  (param: from, to, pt, branch, product, weekly UInt8)
SELECT if({weekly:UInt8} = 1, toMonday(day), day) AS period,
       toFloat64(sum(ck_prod)) AS ck_prod, toFloat64(sum(store_prod)) AS store_prod,
       toFloat64(sum(ck_out)) AS ck_out, toFloat64(sum(sold)) AS sold
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY period
ORDER BY period
LIMIT 1000;

-- flow_by_product  (param: from, to, pt, branch, product) -> total per produk lintas cabang
SELECT brand, product_code, any(product_name) AS product_label, startsWith(product_code, 'NAME:') AS unmapped,
       toFloat64(sum(ck_prod)) AS ck_prod_qty, toFloat64(sum(ck_out)) AS ck_out_qty,
       toFloat64(sum(in_ck + in_other)) AS received, toFloat64(sum(store_prod)) AS store_prod_qty,
       toFloat64(sum(sold)) AS sold_qty,
       if(sum(in_ck + in_other + store_prod + st_in - st_out) <= 0, NULL,
          sum(sold) / sum(in_ck + in_other + store_prod + st_in - st_out) * 100) AS sell_through_pct
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY brand, product_code
ORDER BY sold_qty DESC
LIMIT 500;

-- flow_outliers  (param: from, to, pt, branch, product) -> cabang x produk, sell-through terendah dulu
SELECT brand, company_code, branch_id, any(branch_name) AS branch_label, product_code,
       any(product_name) AS product_label, startsWith(product_code, 'NAME:') AS unmapped,
       toFloat64(sum(in_ck + in_other + st_in - st_out)) AS received,
       toFloat64(sum(store_prod)) AS store_prod_qty,
       toFloat64(sum(sold)) AS sold_qty,
       if(sum(in_ck + in_other + store_prod + st_in - st_out) <= 0, NULL,
          sum(sold) / sum(in_ck + in_other + store_prod + st_in - st_out) * 100) AS sell_through_pct
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY brand, company_code, branch_id, product_code
HAVING received != 0 OR sold_qty != 0 OR store_prod_qty != 0
ORDER BY sell_through_pct ASC NULLS LAST, sold_qty DESC
LIMIT 500;

-- flow_product_daily (tambahan)  (param: from, to, pt, branch, product_key String = '<brand>:<code>')
SELECT day, toFloat64(sum(ck_prod)) AS ck_prod, toFloat64(sum(ck_out)) AS ck_out,
       toFloat64(sum(in_ck + in_other)) AS received, toFloat64(sum(sold)) AS sold
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND concat(brand, ':', product_code) = {product_key:String}
GROUP BY day
ORDER BY day
LIMIT 1000;
```

### 3.3 Penjualan & Menu (`/sales`) — `v_esb_sales_daily`, `v_esb_sales_branch_daily`, `v_esb_menu_mix`

```sql
-- sales_kpi (tambahan)  (param: from, to, prev_from, prev_to, pt, branch, cat) -> 1 baris
-- Tanpa filter kategori boleh memakai v_esb_sales_branch_daily (lebih ringan); dengan filter kategori wajib view ini.
SELECT
    toFloat64(sumIf(net, sales_date >= {from:Date}))       AS net_cur,
    toFloat64(sumIf(net, sales_date <= {prev_to:Date}))    AS net_prev,
    toFloat64(sumIf(gross, sales_date >= {from:Date}))     AS gross_cur,
    toFloat64(sumIf(discount, sales_date >= {from:Date}))  AS discount_cur,
    toFloat64(sumIf(discount, sales_date <= {prev_to:Date})) AS discount_prev,
    toFloat64(sumIf(gross, sales_date <= {prev_to:Date}))  AS gross_prev,
    toFloat64(sumIf(qty, sales_date >= {from:Date}))       AS qty_cur,
    toFloat64(sumIf(qty, sales_date <= {prev_to:Date}))    AS qty_prev,
    toFloat64(sumIf(cogs, sales_date >= {from:Date}))      AS cogs_cur,
    toFloat64(sumIf(cogs, sales_date <= {prev_to:Date}))   AS cogs_prev
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {prev_from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category));
-- App: discount_pct = discount/gross*100; margin_pct = (net-cogs)/net*100.

-- sales_by_weekday  (param: from, to, pt, branch, cat) -> rata-rata net per hari-dalam-minggu (1=Senin..7=Minggu)
SELECT toDayOfWeek(sales_date) AS dow,
       toFloat64(sum(net)) / uniqExact(sales_date) AS avg_net,
       uniqExact(sales_date) AS days
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))
GROUP BY dow
ORDER BY dow
LIMIT 7;
-- Catatan: hari tanpa penjualan sama sekali tidak dihitung di pembagi (uniqExact atas baris yang ada).

-- sales_weekday_heatmap (tambahan)  (param sama) -> minggu (Senin) x hari
SELECT toMonday(sales_date) AS week, toDayOfWeek(sales_date) AS dow, toFloat64(sum(net)) AS net
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))
GROUP BY week, dow
ORDER BY week, dow
LIMIT 1000;

-- sales_by_category  (param: from, to, pt, branch, cat)
SELECT if(menu_category = '', 'Tanpa kategori', menu_category) AS category,
       toFloat64(sum(net)) AS net_sales, toFloat64(sum(qty)) AS qty_sold,
       if(sum(net) = 0, NULL, toFloat64(sum(net) - sum(cogs)) / toFloat64(sum(net)) * 100) AS margin_pct
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))
GROUP BY category
ORDER BY net_sales DESC
LIMIT 500;
-- App: maks 8 + "Lainnya".

-- sales_channel_split  (param: from, to, pt, branch, cat, by_branch UInt8) -> per brand atau per cabang
-- Alias sengaja beda dari nama kolom (branch_key/branch_label): alias = nama kolom memicu alias siklik di ClickHouse
-- dan alias terlihat di WHERE.
SELECT brand,
       if({by_branch:UInt8} = 1, branch_id, toUInt32(0)) AS branch_key,
       if({by_branch:UInt8} = 1, any(branch_name), '') AS branch_label,
       toFloat64(sumIf(net, is_takeaway = 1)) AS net_takeaway,
       toFloat64(sumIf(net, is_takeaway = 0)) AS net_dine_in
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))
GROUP BY brand, branch_key
ORDER BY brand, branch_key
LIMIT 500;
-- is_takeaway = heuristik nama menu dari ESB (prototipe G41), bukan tipe pesanan; sebut di info-ikon.

-- sales_top_menu  (param: from, to, pt, branch, cat, sort String in net|qty|margin)
SELECT brand, menu_code, any(menu_name) AS menu_label, any(menu_category) AS category,
       toFloat64(sum(net)) AS net_sales, toFloat64(sum(qty)) AS qty_sold,
       if(sum(net) = 0, NULL, toFloat64(sum(net) - sum(cogs)) / toFloat64(sum(net)) * 100) AS margin_pct
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({cat:Array(String)}) OR has({cat:Array(String)}, menu_category))
GROUP BY brand, menu_code
ORDER BY multiIf({sort:String} = 'qty', qty_sold, {sort:String} = 'margin', ifNull(margin_pct, -1e18), net_sales) DESC
LIMIT 500;
-- App mengambil 10 teratas / 10 terbawah dari hasil (urutan terbalik untuk "terbawah").

-- sales_menu_daily (tambahan)  (param: from, to, pt, branch, menus Array(String) '<brand>:<menu_code>' maks 10) -> sparkline/drawer
SELECT brand, menu_code, sales_date, toFloat64(sum(net)) AS net, toFloat64(sum(qty)) AS qty
FROM efn_insight.v_esb_sales_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND has({menus:Array(String)}, concat(brand, ':', menu_code))
GROUP BY brand, menu_code, sales_date
ORDER BY brand, menu_code, sales_date
LIMIT 5000;

-- menu_mix_by_branch  (param: from, to, pt, branch) -> cabang x kategori, share_net atas rentang bulan
SELECT brand, branch_id, any(branch_name) AS branch_label, menu_category,
       sum(net) AS net_sales,
       if(sum(sum(net)) OVER (PARTITION BY brand, branch_id) = 0, NULL,
          round(sum(net) / sum(sum(net)) OVER (PARTITION BY brand, branch_id) * 100, 2)) AS share_pct
FROM efn_insight.v_esb_menu_mix
WHERE month BETWEEN toStartOfMonth({from:Date}) AND toStartOfMonth({to:Date})
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
GROUP BY brand, branch_id, menu_category
ORDER BY brand, branch_label, share_pct DESC
LIMIT 5000;
-- Catatan UI: v_esb_menu_mix bulanan -> rentang dibulatkan ke bulan penuh (sebutkan di subjudul). Alternatif harian
-- persis: hitung dari v_esb_sales_daily dengan GROUP BY brand, branch_id, menu_category (query sama, sumber beda).
```

### 3.4 Pembayaran (`/payments`) — `v_esb_payment_daily`

```sql
-- pay_kpi (tambahan)  (param: from, to, prev_from, prev_to, pt, branch) -> 1 baris
SELECT
    toFloat64(sumIf(amount, sales_date >= {from:Date}))     AS amount_cur,
    toFloat64(sumIf(amount, sales_date <= {prev_to:Date}))  AS amount_prev,
    sumIf(payment_count, sales_date >= {from:Date})         AS count_cur,
    sumIf(payment_count, sales_date <= {prev_to:Date})      AS count_prev,
    toFloat64(sumIf(mdr, sales_date >= {from:Date}))        AS mdr_cur,
    toFloat64(sumIf(mdr, sales_date <= {prev_to:Date}))     AS mdr_prev
FROM efn_insight.v_esb_payment_daily
WHERE sales_date BETWEEN {prev_from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))));
-- App: mdr_pct = mdr/amount*100; delta dalam pp.

-- pay_by_method  (param: from, to, pt, branch) -> bauran metode
-- pay_mdr        = SQL yang SAMA (tabel "MDR per metode": kolom mdr_total, mdr_pct, avg_amount); boleh satu
--                  konstanta dengan dua nama, atau pay_mdr ORDER BY mdr_pct DESC.
SELECT payment_type, if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
       sum(payment_count) AS cnt, toFloat64(sum(amount)) AS amount_total, toFloat64(sum(mdr)) AS mdr_total,
       if(sum(amount) = 0, NULL, toFloat64(sum(mdr)) / toFloat64(sum(amount)) * 100) AS mdr_pct,
       if(sum(payment_count) = 0, NULL, toFloat64(sum(amount)) / sum(payment_count)) AS avg_amount
FROM efn_insight.v_esb_payment_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
GROUP BY payment_type, method
ORDER BY amount_total DESC
LIMIT 500;

-- pay_trend  (param: from, to, pt, branch)
SELECT sales_date, payment_type, toFloat64(sum(amount)) AS amount, sum(payment_count) AS payment_count
FROM efn_insight.v_esb_payment_daily
WHERE sales_date BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
GROUP BY sales_date, payment_type
ORDER BY sales_date, payment_type
LIMIT 5000;

-- pay_by_branch (tambahan)  (param: from, to, pt, branch) -> total, MDR, metode dominan
SELECT brand, branch_id, any(b_name) AS branch_name, sum(amt) AS amount, sum(m) AS mdr,
       argMax(method, amt) AS top_method
FROM
(
    SELECT brand, branch_id, any(branch_name) AS b_name,
           if(payment_method = '', 'Tidak diketahui', payment_method) AS method,
           toFloat64(sum(amount)) AS amt, toFloat64(sum(mdr)) AS m
    FROM efn_insight.v_esb_payment_daily
    WHERE sales_date BETWEEN {from:Date} AND {to:Date}
      AND has({pt:Array(String)}, company_code)
      AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
    GROUP BY brand, branch_id, method
)
GROUP BY brand, branch_id
ORDER BY amount DESC
LIMIT 500;
-- branch_id 0 = branchCode pembayaran tidak dikenal di master cabang (branch_name = branch_code).
```

### 3.5 Distribusi (`/distribution`) — `v_esb_flow_daily`

```sql
-- dist_kpi (tambahan)  (param: from, to, prev_from, prev_to, pt, branch, product) -> 1 baris
SELECT
    toFloat64(sumIf(ck_out, day >= {from:Date}))    AS ck_out_cur,   toFloat64(sumIf(ck_out, day <= {prev_to:Date}))   AS ck_out_prev,
    toFloat64(sumIf(in_ck, day >= {from:Date}))     AS in_ck_cur,    toFloat64(sumIf(in_ck, day <= {prev_to:Date}))    AS in_ck_prev,
    toFloat64(sumIf(st_out, day >= {from:Date}))    AS st_out_cur,   toFloat64(sumIf(st_out, day <= {prev_to:Date}))   AS st_out_prev,
    toFloat64(sumIf(in_other, day >= {from:Date}))  AS in_other_cur, toFloat64(sumIf(in_other, day <= {prev_to:Date})) AS in_other_prev
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {prev_from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)));
-- Selisih = ck_out - in_ck. PERHATIAN: dengan filter cabang, ck_out (di cabang CK) dan in_ck (di toko) berasal dari
-- cabang berbeda -> selisih hanya bermakna tanpa filter cabang atau bila CK + tokonya sama-sama dipilih. Bila CK dan
-- toko beda PT, user salah satu PT hanya melihat satu sisi (row policy) -> selisih tampak besar; beri catatan.

-- dist_ck_delivery  (param: from, to, pt, branch, product) -> harian ck_out / in_ck / in_other
SELECT day, toFloat64(sum(ck_out)) AS ck_out_qty, toFloat64(sum(in_ck)) AS in_ck_qty,
       toFloat64(sum(in_other)) AS in_other_qty
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY day
ORDER BY day
LIMIT 1000;

-- dist_transfer  (matriks cabang: diterima CK, transfer masuk/keluar, neto) (param: from, to, pt, branch, product)
SELECT brand, company_code, branch_id, any(branch_name) AS branch_label,
       toFloat64(sum(ck_out)) AS ck_out_qty, toFloat64(sum(in_ck)) AS in_ck_qty, toFloat64(sum(in_other)) AS in_other_qty,
       toFloat64(sum(st_in)) AS st_in_qty, toFloat64(sum(st_out)) AS st_out_qty,
       toFloat64(sum(st_in) - sum(st_out)) AS st_net
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY brand, company_code, branch_id
HAVING ck_out_qty != 0 OR in_ck_qty != 0 OR in_other_qty != 0 OR st_in_qty != 0 OR st_out_qty != 0
ORDER BY brand, branch_label
LIMIT 500;

-- dist_transfer_top_products (tambahan)  (param: from, to, pt, branch, product)
SELECT brand, product_code, any(product_name) AS product_label,
       toFloat64(sum(st_in)) AS st_in_qty, toFloat64(sum(st_out)) AS st_out_qty, toFloat64(sum(st_in + st_out)) AS moved
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({branch:Array(String)}) OR has({branch:Array(String)}, concat(brand, ':', toString(branch_id))))
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY brand, product_code
HAVING moved > 0
ORDER BY moved DESC
LIMIT 10;

-- dist_ck_gap_by_product (tambahan)  (param: from, to, pt, product) -> PENGGANTI "tabel selisih cabang x produk"
-- ck_out dicatat di cabang CK dan in_ck di toko, sehingga selisih per baris cabang x produk selalu = nilai penuh.
-- Selisih yang bermakna = per produk (lintas cabang) per hari. Tanpa filter cabang.
SELECT day, brand, product_code, any(product_name) AS product_label,
       toFloat64(sum(ck_out)) AS ck_out_qty, toFloat64(sum(in_ck)) AS in_ck_qty, toFloat64(sum(ck_out) - sum(in_ck)) AS gap
FROM efn_insight.v_esb_flow_daily
WHERE day BETWEEN {from:Date} AND {to:Date}
  AND has({pt:Array(String)}, company_code)
  AND (empty({product:Array(String)}) OR has({product:Array(String)}, concat(brand, ':', product_code)))
GROUP BY day, brand, product_code
HAVING abs(gap) > 0.0005
ORDER BY abs(gap) DESC
LIMIT 500;
```
Peta asal → tujuan (Q-INS3) tidak tersedia: `v_esb_flow_daily` hanya sisi asal/tujuan terpisah. Bila dibutuhkan,
tambah view baru dari `fact_esb_delivery`/`fact_esb_transfer` (asal, tujuan, produk, qty) dengan keputusan scope:
baris terlihat oleh PT asal **dan** PT tujuan (dua baris) — belum dibuat di G42.

### 3.6 Kualitas Data (`/data-quality`) — `v_esb_data_quality`

Jendela waktu cek sudah tetap di view (30 hari / H-1 / terakhir); **filter tanggal tidak berlaku** di halaman ini
(filter cabang juga tidak: kolom `item` berisi teks). Cek per brand (`data_age_hours`, `last_run_not_ok`) diduplikasi
ke setiap PT brand itu (Bacha → BCF dan BCR), maka query mengelompokkan per `brand, item` agar user yang punya
dua PT tidak melihat baris ganda.

```sql
-- dq_status_board (tambahan)  (param: pt) -> jumlah temuan per cek
SELECT check_name, uniqExact(brand, item) AS findings
FROM efn_insight.v_esb_data_quality
WHERE has({pt:Array(String)}, company_code)
GROUP BY check_name
ORDER BY check_name
LIMIT 20;
-- data_age_hours selalu punya baris (bukan temuan) -> status dari nilai maks (dq_freshness), bukan jumlah.

-- dq_freshness  -> §3.1

-- dq_last_run, dq_alias_unmapped, dq_non_ea, dq_store_no_sales  (param: pt)
-- Satu bentuk SQL; check_name ditulis LITERAL per konstanta (bukan parameter dari URL):
--   dq_last_run       -> 'last_run_not_ok'
--   dq_alias_unmapped -> 'alias_unmapped'
--   dq_non_ea         -> 'non_ea_lines'
--   dq_store_no_sales -> 'store_no_sales_h1'
SELECT check_name, brand, groupUniqArray(company_code) AS company_codes, item,
       max(value) AS value_max, max(last_day) AS last_seen
FROM efn_insight.v_esb_data_quality
WHERE has({pt:Array(String)}, company_code) AND check_name = 'alias_unmapped'
GROUP BY check_name, brand, item
ORDER BY value_max DESC
LIMIT 500;
-- alias_unmapped: item = 'NAME:<nama>' (tampilkan tanpa prefix), value = qty 30 hari.
-- non_ea_lines:   item = '<tabel>:<uom>', value = jumlah baris 30 hari.
-- store_no_sales_h1: item = '<branch_id> <nama cabang>', value = 0, last_day = H-1.
-- last_run_not_ok: item = '<mode>:<status>', value = jumlah error.
```

## 4. Keputusan scope (G42)

- **`v_esb_data_quality`**: view efn_insight membaca `efn_dwh.v_esb_data_quality_pt` (baru, 13-g42-insight.sql).
  Baris yang sudah ber-PT (`alias_unmapped`, `non_ea_lines`, `store_no_sales_h1`) diteruskan apa adanya; baris per
  brand (`data_age_hours`, `last_run_not_ok`, `company_code=''`) di-INNER JOIN ke `SELECT DISTINCT brand,
  company_code FROM dim_esb_branch FINAL` → satu baris per PT yang punya cabang di brand itu (Bacha: BCF dan BCR).
  Tidak ada baris tanpa kode. Isi baris per brand = metadata ekstraksi (umur jam, status run), tanpa angka transaksi;
  terlihatnya oleh BCF maupun BCR dapat diterima (satu akun ESB). Brand tanpa cabang terdaftar → baris per brand tidak
  muncul (fail closed; UI: "Tidak diketahui").
- **`v_esb_product_list`**: master produk ESB milik brand; `dim_esb_product.company_code` = PT default brand (Bacha →
  BCF) sehingga tidak dipakai. PT diambil dari `dim_esb_branch` (DISTINCT brand → company_code): user BCR melihat
  katalog Bacha (sama dengan BCF), user PB tidak pernah melihat katalog brand lain. Produk tetap terlihat di halaman
  alur hanya lewat fakta ber-PT.
- **`v_esb_sales_branch_daily`**: tingkat cabang-hari, `trx_count = uniqExact(sales_num)`, filter versi slice sama
  dengan `v_esb_sales_daily` (checkpoint `entity='sales'`).
- Row policy `efn_insight_scope` pada setiap view: `company_code != '' AND has(codes, company_code) AND sig` (C4).

## 5. Perbedaan dengan docs/04 §26.7/§26.8 (untuk direkonsiliasi lead/architect)

| Butir | docs/04 §26 | Implementasi G42 | Alasan |
|---|---|---|---|
| View tambahan | 7 view | + `v_esb_sales_branch_daily` (8 view) | KPI transaksi (permintaan lead, Q-INS2) |
| Hak definer | `GRANT SELECT ON efn_dwh.*` | `GRANT SELECT` per objek (9 view + 10 tabel `efn_dwh.*esb*` + `efn_esb.extract_checkpoint`, `extract_run`) | least privilege; `efn_esb.*` memang perlu (view v_esb_* membaca checkpoint/run) dan tidak tercakup `efn_dwh.*` |
| Pemetaan brand → PT di `v_esb_data_quality` | statis (`pb→PB`, `bacha→BCF,BCR`, ...) | dinamis dari `dim_esb_branch` (DISTINCT brand, company_code) lewat `efn_dwh.v_esb_data_quality_pt` | ikut override cabang Bacha (Q-ESB3) tanpa kode keras; hasil sama bila setiap PT punya ≥ 1 cabang |
| `v_esb_product_list` sumber | `dim_esb_product` / `dim_esb_menu` | `dim_esb_product` × PT cabang brand; kategori menu dari `list_categories` (sales_daily) | dim produk tidak punya PT per cabang |
| Query tambahan | — | ditandai "(tambahan)" di §3 | kebutuhan komponen `docs/design/insight-web.md` |
| `dist_*` | `dist_ck_delivery`, `dist_transfer` | + `dist_kpi`, `dist_transfer_top_products`, `dist_ck_gap_by_product` (pengganti tabel selisih cabang × produk, yang tidak bermakna karena ck_out di CK dan in_ck di toko) | semantik §25.6 |
