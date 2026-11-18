# G43 — Fit-gap migrasi ESB Core → Odoo CE 19 (master, saldo awal, histori)

- **Penyusun:** efn-odoo-functional · **Tanggal:** 18 Nov 2026 · **Gelombang:** 43 fase 1 (dokumen, tanpa kode modul)
- **Keputusan user 18 Nov 2026:** migrasi = master + saldo awal + histori 24 bulan; IMD/CUP = company baru
  (placeholder); Bacha default BCF, override per cabang memakai pemetaan G41; tanggal cutover = parameter; histori
  **tidak** boleh merusak valuasi, pajak, dan penomoran → keputusan final di **ADR-042 (belum ada, akan Proposed)**.
- **Terkait:** ADR-040 (ESB → datamart, Proposed), ADR-018 (paket CK = BoM manufaktur, combo POS = kit), ADR-031
  (tanpa modul pihak ketiga), ADR-036 (enkripsi kolom PII partner), ADR-020 (poin di ledger BFF), docs/04 §25 (G41),
  `docs/08_ODOO_FIT_GAP.md` §6.4 (validator), `docs/templates/migrasi/`, `services/api-gateway/src/migration`.
- **Risiko:** R-O02 (migrasi data), R-L01 (UU PDP), R-L03 (pajak), R-T01 (salah konsep phantom), R-B06.
- **Repo publik:** dokumen ini tidak memuat nama cabang, angka penjualan, atau username ESB. Jumlah cabang = angka pola saja.

Legenda verifikasi: **[V]** = diverifikasi di source `odoo/odoo` 19.0 atau spec ESB `openapi.json` (WebFetch 18 Nov
2026); **[R]** = diverifikasi di repo ini; **[B]** = belum terverifikasi.

> **Ringkasan untuk manajemen.** Data master (cabang, produk, menu, resep, supplier, pelanggan B2B) dan saldo awal
> (stok fisik, piutang/utang, neraca pembukaan) dipindah ke Odoo dengan fitur standar dan sebuah skrip impor yang
> bisa diulang tanpa menggandakan data. **Histori 24 bulan tidak dimasukkan sebagai transaksi Odoo** (tidak sebagai
> pesanan POS, jurnal, atau mutasi stok), karena itu akan menggandakan nilai persediaan, pajak, dan nomor dokumen.
> Histori tetap lengkap di gudang data (ClickHouse, sudah terisi G41) dan dibaca dashboard Insight; Odoo hanya
> menyimpan **ringkasan harian aliran produk** secukupnya agar forecast stok langsung jalan sejak hari pertama.
> Sejumlah data (resep rinci, harga menu, stok opname, utang/piutang, pelanggan B2B) **belum** ditarik dari ESB dan
> perlu ditambahkan ke ekstraktor sebelum migrasi.

---

## 1. Apa yang sebenarnya tersedia di datamart G41 (hasil pemeriksaan kode)

Sumber: `services/esb-extract/efn_esb/extract.py`, `transform.py`, `normalize.py`, DDL
`infra/clickhouse/init/12-g41-esb.sql` [R].

| Data ESB | Ditarik G41? | Di mana | Cukup untuk migrasi? |
|---|---|---|---|
| Cabang `/branch/list/user` | Ya | `dim_esb_branch` (branch_id, code, name, `company_code`, `is_ck`, `is_store`, active) | Ya untuk kerangka; tipe cabang (gudang/storage/HO/konsinyasi) perlu tabel pemetaan manual |
| Produk `/product` | Ya | `dim_esb_product` (code, name, category, `uom` — **sering kosong**, `is_fin`, `batch_to_ea_ratio`, active) | Sebagian: **tanpa harga pokok, tanpa konversi satuan**, `company_code` = PT default brand |
| BOM `/product/bom` (daftar) | **Hanya raw** | `efn_esb.raw_doc` endpoint `product/bom` (header saja) | **Tidak**: komponen resep tidak ditarik |
| BOM detail `/product/bom/{bomID}` | Tidak | — | Perlu ditambah (§9) |
| Rasio BATCH→EA | Ya | `efn_esb.bom_ratio`, `dim_esb_product.batch_to_ea_ratio` | Ya (hanya BOM yang pernah diproduksi via simple-manufacturing) |
| Menu `/menu/browse` | Ya | `dim_esb_menu` (code, name, kategori, detail, `is_takeaway`) | Sebagian: **tanpa harga, tanpa tautan BOM, tanpa isi paket** |
| Alias nama produk | Ya | `dim_esb_product_alias` | Ya |
| Supplier `/supplier` | Ya (whitelist L-110) | `dim_esb_supplier` (code, nama perusahaan, active) | Ya untuk partner; NPWP/alamat/kontak diisi Finance manual |
| Customer B2B, member, karyawan | **Tidak** (L-110) | — | Customer B2B perlu ditambah dengan whitelist (§9); member/karyawan tidak dari ESB |
| Penjualan baris | Ya | `fact_esb_sales_line` (per `salesNum`, `row_type` ALACARTE/PACKAGE_HEADER/PACKAGE_COMPONENT, `total` **sebelum PB1/SC**) | Ya untuk histori; tanpa jam (L-112) |
| Pembayaran harian | Ya | `fact_esb_payment_daily` | Ya untuk rekonsiliasi histori |
| Produk FIN terjual | Ya | `fact_esb_fin_sold_daily` (EA) | Ya (sumber `sold` forecast) |
| Produksi, delivery, transfer | Ya | `fact_esb_production/delivery/transfer` | Ya untuk histori aliran |
| Stok `/inventory/stock-list` | Kode ada, **default mati**, 0 baris pada uji | `fact_esb_stock_snapshot` | **Tidak** (belum terbukti); stok awal harus dari stock opname cutover |
| Saldo AR/AP, neraca (TB) | Tidak | — | Perlu ditambah (§9) |

Fakta penting dari README G41 [R]: komponen paket punya `total = 0` dan HPP ada di komponen; `total − discount` ≈
89,6 % pembayaran (artinya `total` belum termasuk PB1 10 % dan service charge); nama produk tanpa kode dipetakan
lewat alias `NAME:<alias>`; produk FIN = kode berawalan `FIN` atau nama memuat token `FIN`/`FINB`.

**Fakta cabang (uji GET nyata 18 Nov 2026, disampaikan efn-lead; kelima akun login OK):** PB 41 cabang, WTZ 9,
Bacha 13, IMD 9, CUP 7. Setiap brand punya cabang HEAD OFFICE dan Warehouse/Storage. CK: PB branchID 4 & 38, WTZ 5 &
6, IMD 2, CUP 4 & 6. Bacha: satu cabang "Retail" (branchID 6) kandidat BCR, satu "B2B Consignment" (branchID 10)
perlu konfirmasi, sisanya kafe/storage/gudang → BCF. Satu lokasi CK muncul di **dua brand** (WTZ dan CUP, nama
lokasi sama). **Q-ESB2 terjawab:** "WP" = Wetzel's Pretzels → `WTZ`.
Tindak lanjut G41 (efn-lead/data): `ESB_CK_BRANCHES` default perlu menjadi `pb:4|38,wtz:5|6,imd:2,cup:4|6` dan
`ESB_BRANCH_COMPANY_OVERRIDES` `bacha:6=BCR` (+ `bacha:10=…` setelah Q-ESB10) — migrasi wajib membaca hasil yang
sama dari `dim_esb_branch.company_code`, bukan konfigurasi sendiri.

---

## 2. Pemetaan entitas → model/field Odoo CE 19

Prinsip: **standar CE dulu**; data per PT memakai `company_id`; semua record migrasi punya external ID (§3).

### 2.1 Cabang → company / gudang / POS / analitik

| Tipe cabang ESB (penentu) | Odoo CE 19 | Field kunci | Catatan |
|---|---|---|---|
| Semua cabang | `res.company` dari `dim_esb_branch.company_code` (PB, WTZ, BCF/BCR, IMD, CUP) | `efn_brand_code` [R] | Satu-satunya sumber pemetaan PT = kolom ini (override Bacha ikut G41) |
| Toko/outlet (`is_store=1`) | `stock.warehouse` + `pos.config` (satu per outlet; kiosk menyusul) | warehouse `code` **maks 5 karakter, unik per company**, `name` unik per company [V]; `pos.config.picking_type_id` dari gudang | Kode ESB bisa > 5 karakter → **tabel pemetaan** `branch_map.csv` (brand, branch_id, tipe, warehouse_code, nama tampilan). `pos.config` dibuat **tanpa** sesi |
| Central Kitchen (`is_ck=1`) | `stock.warehouse` dengan `efn_is_central_kitchen` (efn_ck_production) [R], *Manufacture to Resupply* | `manufacture_to_resupply`, outlet `efn_ck_supplier_wh_id` [R] | Mode produksi per produk (`efn_ck_production_mode`) diisi planner, bukan migrasi |
| Warehouse / Storage | **Default:** `stock.location` internal di bawah gudang CK/brand terdekat (satu situs fisik); **bila situs terpisah** → `stock.warehouse` sendiri | `location_id`, `usage='internal'` | Ditentukan per cabang di `branch_map.csv` (Q-ESB13) |
| HEAD OFFICE | **Bukan outlet, tanpa gudang** (kecuali memegang stok) | — | Hanya akun analitik "HO" bila Finance memakai analitik per cabang |
| Damage / Expired / Temporary (nama) | Bukan gudang: stok di cabang ini **tidak** dimigrasi sebagai stok jual; saldo → lokasi scrap/karantina bila Finance minta | `stock.location` `usage` internal "Karantina" | Q-ESB13 |
| B2B Consignment (Bacha #10) | `stock.location` internal "Konsinyasi <mitra>" di gudang PT pemilik barang (stok tetap milik PT; penjualan saat laporan mitra) | — | PT = BCR atau BCF → **Q-ESB10** |
| Analitik per outlet (opsional) | `account.analytic.plan` "Outlet" + `account.analytic.account` per cabang | — | Hubungan analitik ke `pos.config`/jurnal sesi POS di CE 19 **[B]** → bila Finance butuh P&L per outlet, cek dulu (Q-ESB18) |

**CK bersama antar-brand (WTZ & CUP).** ESB mencatat CK itu terpisah di setiap akun brand. Rekomendasi default:
**dua `stock.warehouse` di alamat yang sama** (satu di WTZ, satu di CUP), masing-masing memproduksi dan memegang stok
PT-nya sendiri — sama dengan perilaku ESB hari ini, tanpa inter-company. Hanya bila satu PT memiliki dapur dan
"menjual" produksi ke PT lain, pakai `efn_intercompany` [R] + transfer pricing (Q-3) → **Q-ESB11**.

### 2.2 Kategori, satuan, produk

| ESB | Odoo CE 19 | Aturan |
|---|---|---|
| `dim_esb_product.category` | `product.category` (**global, tanpa `company_id`** [V]) path `<Brand> / <kategori ESB>` (**catatan QA 18 Nov:** Odoo 19 tidak memasang kategori "All" → `<Brand>` = tingkat atas) | Akun valuasi/biaya = properti company-dependent pada kategori; metode biaya per kategori (Finance, Q-ESB16) |
| `dim_esb_menu.menu_category` / `_detail` | `pos.category` induk = kategori, anak = detail | Kategori POS = juga rute KDS (`efn_pos_kds`) → stasiun diisi Ops |
| `uom` (teks ESB) | `uom.uom` (Odoo 19 **tanpa kategori UoM**; relasi `relative_uom_id`) [R efn_kitting] | Tabel `uom_map.csv` (teks ESB → xmlid `uom.product_uom_unit`/`kgm`/`litre`/…); teks kosong → error validator, bukan default diam-diam |
| `BATCH` | **Tidak dibuat sebagai UoM.** Satu BoM = satu batch: `mrp.bom.product_qty` = rasio EA (mis. 24 Unit) [V field] | Rasio berbeda per produk → UoM "Batch" bersama akan salah. Sumber rasio: `bom_ratio`, fallback detail BOM (§9) |
| Produk bahan baku | `product.template` `type='consu'`, `is_storable=True`, `purchase_ok`, `company_id` = PT | `default_code` = kode ESB (tidak unik secara DB; unik per brand) |
| Produk FIN (dibuat di CK) | idem + BoM `normal` di CK; **paket CK** → `efn_is_kitting_package=True` [R] | Sesuai ADR-018; tidak boleh punya BoM kit (constraint efn_kitting) |
| Produk FIN dibuat di toko (store_prod) | idem, BoM `normal` di gudang toko (rute Manufacture outlet) | Bila klien ingin toko tidak memproduksi di Odoo → Q-ESB14 |
| Menu jual (ALACARTE) | `product.template` `available_in_pos=True`, `sale_ok`, **tidak storable**, BoM `phantom` (Kit) ke FIN/bahan [V type] | Stok terpotong lewat `pos_mrp` [R docs/08 §13.3] |
| Menu takeaway (nama berakhiran `" T"`) | **Digabung** ke menu dasarnya (satu produk); dine-in/takeaway = **preset POS** + fiscal position (`efn_setup_company`) [R] | Dua `menu_code` ESB → satu produk Odoo (map_esb_odoo dua baris, `odoo_id` sama). Bila harga takeaway beda → pricelist per preset [B] / Q-ESB15 |
| Paket (PACKAGE_HEADER + COMPONENT) | Isi tetap → produk menu + BoM `phantom`; ada pilihan → `type='combo'` + `product.combo` [R docs/08 §13.3] | Komposisi paket **tidak ada di master yang ditarik**; kandidat diturunkan dari pola baris penjualan (header diikuti komponen dalam satu `salesNum`) lalu dikonfirmasi Ops (Q-ESB12) |
| Harga menu | `list_price` (Rupiah bulat, **exclude pajak** — sesuai `account_price_include=tax_excluded` PT F&B) [R] | Sumber: `/menu/browse-for-product` field `price` [V spec] — belum ditarik. Harga per cabang (bila ada) → pricelist per `pos.config` (Q-ESB15) |
| Harga pokok | `standard_price` per company (company-dependent) | Sumber: `lastHpp` komponen detail BOM [V spec] / HPP per produk (endpoint [B]); wajib terisi **sebelum** stok awal |
| Pajak | **Tidak dimigrasi**; produk menerima pajak penjualan default PT (PB1 F&B / PPN BCR) [R] | `lockVat` ESB (customer) / `/product/vat` [V path] hanya pembanding validator |
| Barcode | `barcode` hanya bila ESB punya barcode valid; aturan validator §6.4 | Menu F&B umumnya tanpa barcode |

Bacha dua PT: produk yang dipakai cabang BCR **dibuat kembar** di BCR (Q-AD1-4 default, [R] efn_setup_company);
penentu = produk yang muncul di penjualan/stok cabang BCR. Kunci external ID memuat kode PT (§3).

### 2.3 Resep / BoM (ADR-018)

| ESB (`/product/bom/{bomID}` [V spec]) | Odoo | Aturan |
|---|---|---|
| BOM yang dipakai produksi (simple-manufacturing / production-result) | `mrp.bom` `type='normal'`, `company_id` PT CK, `product_tmpl_id` = produk hasil, `product_qty` = hasil per batch (EA), `product_uom_id` | `bomCode` → `code` [V] |
| BOM yang ditautkan ke menu (`/menu/browse-for-product.bomName` [V spec]) | `mrp.bom` `type='phantom'` pada produk menu | Membedakan dua jenis: `bomTypeID/bomTypeName` [V nama field, arti [B]] + tautan menu |
| `bomDetails.qty/uomName/convertionQty` | `mrp.bom.line` `product_id`, `product_qty`, `product_uom_id` [V] | Qty dikonversi ke UoM produk komponen bila `convertionQty` ≠ 1 |
| `yieldPercent`, `tolerancePercent` | **Tidak ada field yield di `mrp.bom.line` CE** [V] | Default: qty ESB dipakai apa adanya (diasumsikan sudah bruto); ambang yield/varians = `efn_ck_production` per PT [R]. Bila ESB qty = neto → qty/yield (Q-ESB17) |
| `subtitution` (bahan pengganti) | Tidak ada padanan CE | Tidak dimigrasi; dicatat di laporan |
| `bomCosts` (biaya lain + COA) | Tidak dimigrasi (CE: biaya operasi via work center, tidak dipakai EFN) | Laporan ke Finance |
| `bomReplacements` (pengganti per menu) | Tidak dimigrasi | Laporan |
| Disassembly (bila ada) | `mrp.bom.byproduct` + `cost_share` [V] | Sesuai efn_ck_production |

### 2.4 Partner

| ESB | Odoo | Aturan PDP |
|---|---|---|
| Supplier (`dim_esb_supplier`) | `res.partner` `is_company=True`, `ref` = kode, `company_id` = PT, `supplier_rank` | Hanya nama perusahaan; NPWP/alamat/kontak diisi Finance di Odoo (field PII terenkripsi ADR-036) |
| Customer B2B (`/customer` [V spec]: `customerCode`, `customerName`, `customerCategoryName`, `paymentDueDays`, `flagActive`, `lockVat`, **`address`, `picName`, `picPhone`**) | `res.partner` `is_company=True`, `ref`, `company_id`, `property_payment_term_id`, kategori → `category_id` (tag) | **Whitelist** di ekstraktor: buang `address`, `picName`, `picPhone` (data orang). Kontak PIC diisi manual bila ada dasar hukum (DPO) |
| `paymentDueDays` | `account.payment.term` per nilai unik ("Net N hari") | Dibuat sekali per company |
| Member retail & poin | **Bukan Odoo**: BFF `POST /internal/v1/migration/members` dan `/opening-balance` [R] | `source_ref` wajib opak (HMAC); sumber member bukan ESB G41 (Q-ESB19) |
| Karyawan/kasir | Tidak dari ESB (L-110); template `07_karyawan_kasir.csv` [R] | PIN tidak diimpor |

### 2.5 Metode bayar, pajak, CoA (konfigurasi, bukan data migrasi)
- `fact_esb_payment_daily.payment_type/payment_method` → daftar unik per PT untuk menyiapkan `pos.payment.method`
  (tunai, EDC per bank, QRIS, voucher, 3PL). Dibuat admin/`efn_setup_pos`, bukan skrip migrasi; skrip hanya
  menghasilkan `payment_map.csv` untuk rekonsiliasi.
- CoA ESB (`/accounting/coa` [V spec]) → `coa_map.csv` (coaNo ESB → kode akun l10n_id per PT), disusun Finance;
  dipakai untuk neraca pembukaan (§5.3).

---

## 3. Kunci idempoten & `map_esb_odoo`

1. **External ID** (sumber kebenaran pemetaan di Odoo): `ir.model.data` module `efn_mig_esb`, `noupdate=True`,
   name = `<brand>_<company>_<entity>_<key>`:
   - `company` = kode PT huruf kecil (`pb`, `wtz`, `bcf`, `bcr`, `imd`, `cup`) — **ditambahkan** pada usulan
     kontrak (`<brand>_<entity>_<esbid>`) karena Bacha punya produk kembar BCF/BCR dengan kode ESB sama.
   - `key` = kode ESB dinormalkan: huruf kecil, karakter selain `[a-z0-9_]` → `_`, dan bila normalisasi mengubah
     kode atau panjang > 64 → tambahkan `_` + 8 hex `sha1(kode asli)` (mencegah dua kode berbeda bertabrakan).
   - Contoh: `efn_mig_esb.pb_pb_product_fin_0123`, `efn_mig_esb.bacha_bcr_product_bc_001_a1b2c3d4`,
     `efn_mig_esb.wtz_wtz_branch_5`, `efn_mig_esb.pb_pb_bom_812`, `efn_mig_esb.pb_pb_menu_m1001`.
   - Entitas: `branch_wh`, `branch_loc`, `branch_pos`, `category`, `pos_category`, `uom`, `product`, `menu`
     (menu takeaway → `menu` dengan key kode `" T"` menunjuk record yang sama: **xmlid kedua dibuat** agar lookup
     idempoten), `bom`, `supplier`, `customer`, `payment_term`, `quant` (tidak perlu xmlid; idempoten lewat
     `inventory_quantity` absolut), `opening_move`, `opening_ar_line`/`opening_ap_line` (via `ref` dokumen).
   - Mode impor: upsert — xmlid ada → `write` field yang dikelola migrasi saja (bukan field yang sudah diubah
     pengguna setelah migrasi; daftar field "milik migrasi" per entitas di kontrak §27); tidak ada → `create`.
     Record yang hilang di ESB **tidak dihapus**, hanya dilaporkan (arsip manual).
2. **`efn_dwh.map_esb_odoo`** (salinan untuk dashboard/forecast/rekonsiliasi; bukan sumber kebenaran): satu baris per
   (brand, entity, esb_key) dengan `company_code`, `odoo_xmlid`, `odoo_id`, `_version` = waktu run. Ditulis
   **setelah** transaksi Odoo commit. ReplacingMergeTree → aman diulang.
3. **Pemisahan jaringan (rekomendasi untuk ADR-042/§27):** Odoo tidak menjangkau ClickHouse dan sebaliknya (K25-02,
   network `esb` internal). Usul alur tiga langkah: (a) `export` di stack data membaca `dim_/fact_esb_*` dengan user
   ClickHouse baru **`efn_mig`** (SELECT dim/fact/view ESB + INSERT **hanya** `map_esb_odoo`, TLS CA internal) →
   bundel JSONL tanpa PII; (b) bundel di-`docker cp` ke container Odoo → `odoo shell` (pola
   `scripts/brand-catalog/import_odoo.py` [R]) → hasil `map.jsonl`; (c) `map-writeback` menulis ke `map_esb_odoo`.
   Bundel = data bisnis klien → disimpan terenkripsi, dihapus setelah berita acara (retensi sama template migrasi).

---

## 4. Urutan migrasi & dependensi

| # | Langkah | Prasyarat | Dry-run? |
|---|---|---|---|
| 0 | Company IMD/CUP (G43-A), CoA, pajak, `efn_setup_*`, gudang CK flag; **parameter cutover per brand**; tabel pemetaan disetujui (`branch_map`, `uom_map`, `coa_map`, `payment_map`) | Q-ESB5/6/10/13 | — |
| 1 | Cabang → gudang/lokasi/`pos.config` (tanpa sesi) / analitik | 0 | Ya |
| 2 | UoM tambahan | 0 | Ya |
| 3 | `product.category`, `pos.category` | 0 | Ya |
| 4 | Produk bahan & FIN per PT (+ kembar BCR) | 2, 3 | Ya |
| 5 | Produk menu (gabung `" T"`), harga | 3, 4 | Ya |
| 6 | Supplier, payment term, customer B2B | 0 | Ya |
| 7 | BoM produksi (`normal`) → BoM menu (`phantom`) → paket (`combo`/kit) | 4, 5 | Ya |
| 8 | `product.supplierinfo` (opsional, dari harga beli terakhir) | 4, 6 | Ya |
| 9 | Harga pokok (`standard_price`) per PT per tanggal cutover | 4 | Ya |
| 10 | **Freeze ESB** (tidak ada transaksi stok setelah jam cutover) → stock opname fisik → stok awal | 1, 4, 9 | Ya (rollback) |
| 11 | Neraca pembukaan + AR/AP terbuka | 6, `coa_map`, TB ESB final | Ya (draft, tidak di-post) |
| 12 | Member & saldo poin → BFF (jalur terpisah, DPO) | Q-ESB19, Q-12/13 | API idempoten |
| 13 | Histori (sesuai ADR-042 — §7) | 1, 4, 5 | Ya |
| 14 | Rekonsiliasi akhir + berita acara; brand dikeluarkan dari `ESB_BRANDS` (L-105) | semua | — |

Langkah 1–9 boleh diulang kapan saja sebelum cutover (master bergerak); 10–11 sekali per brand pada tanggal cutover
(boleh diulang sebelum *post*). **Dry-run** = jalankan seluruh langkah dalam satu transaksi lalu `env.cr.rollback()`,
keluarkan laporan yang sama dengan run nyata.

---

## 5. Cutover

### 5.1 Parameter
- `--cutover-date YYYY-MM-DD` per brand (Q-ESB6), disimpan juga di Odoo `ir.config_parameter`
  `efn_mig_esb.cutover_date.<company_code>` (dipakai forecast/histori, §7). Tidak ada konstanta di kode.
- Arti: transaksi **sebelum** tanggal ini = ESB (histori), **sejak** tanggal ini = Odoo. Saldo awal per akhir hari
  H-1 (`cutover − 1`), diakui pada tanggal akuntansi `cutover − 1`.
- Bacha BCF dan BCR satu akun ESB → satu tanggal cutover untuk brand `bacha` (dua PT sekaligus).

### 5.2 Stok awal (`stock.quant` mode inventaris)
- Per lokasi (§2.1) × produk storable: `inventory_quantity` (UoM produk) lalu `action_apply_inventory()` →
  `_apply_inventory(date)` membuat `stock.move` `is_inventory=True` [V]; `stock_account` menambah
  **`accounting_date`** pada quant (konteks `force_period_date`) [V] → isi `cutover − 1`.
- Sumber qty: **stock opname cutover di ESB** (`/inventory/stock-opname` + detail [V path], belum ditarik) atau
  hitung fisik langsung di template `03_stok_awal.csv` [R]. `fact_esb_stock_snapshot` **tidak** dipakai sampai
  terbukti (0 baris pada uji).
- Harga pokok (`standard_price`) wajib terisi sebelum apply, karena nilai persediaan awal = qty × biaya.
- **Hindari nilai persediaan ganda:** pembalik jurnal penyesuaian persediaan masuk ke akun lokasi *Inventory
  adjustment* (`property_stock_inventory` [V]). Rekomendasi: selama cutover akun valuasi lokasi itu = akun
  **"Saldo Awal – Kliring"**, dan neraca pembukaan (§5.3) **tidak** memuat saldo akun persediaan ESB (diganti
  hasil quant), atau memuat dengan lawan kliring yang sama sehingga kliring = 0 setelah keduanya diposting. Perilaku
  valuasi stok otomatis CE 19 (perpetual) pada adjustment bertanggal mundur **[B]** → uji di dry-run (Q-ESB16).
- Lot & kedaluwarsa: hanya produk `tracking=lot` (Q-34); sumber lot ESB (`/goods-delivery-expiration` [V path])
  tidak ditarik → default stok awal tanpa lot kecuali Ops meminta.

### 5.3 Neraca pembukaan & AR/AP
- **Neraca (TB):** satu `account.move` jurnal Umum tanggal `cutover − 1` per PT, baris per akun (`coa_map`), selisih
  ke *unaffected earnings* — sama dengan mekanisme `res.company._update_opening_move` /
  `account_opening_move_id`/`account_opening_date` [V]. Endpoint TB/GL ESB **[B]** (spec memuat modul COA
  `/accounting/coa*` [V]; laporan neraca belum ditemukan) → alternatif: TB dari ekspor Finance.
- **AR/AP terbuka (per dokumen, bukan per saldo partner):** baris jurnal pembukaan per faktur terbuka:
  `partner_id`, akun piutang/utang partner, `date_maturity` = jatuh tempo ESB, `name`/`ref` = nomor dokumen ESB,
  debit/kredit sisa; lawan = akun Saldo Awal – Kliring. Hasil: aging & pelunasan (rekonsiliasi dengan pembayaran
  baru) jalan standar. **Jangan** membuat ulang faktur/tagihan (pajak & nomor dokumen akan terbentuk dua kali).
  Saldo akun piutang/utang di TB diganti total baris per dokumen (kontrol: harus sama).
- Sumber ESB: AP → `/purchase/purchase-invoices`, `/payable-settlement`, `/finance/disbursement` (`invoiceDueDate`,
  `grandTotal`, `overDue`) [V path]; AR → `/customer-advance-settlement` [V path] untuk uang muka, **faktur piutang
  B2B [B]** (endpoint belum ditemukan). Uang muka pelanggan/supplier → baris terpisah di akun uang muka.
- Rekening bank/kas: saldo dari TB; rekonsiliasi bank dimulai dari tanggal cutover.

---

## 6. Validasi & laporan rekonsiliasi

Validator = perluasan `scripts/migration/validate_import.py` (spes §6.4 docs/08 [R]) dengan sumber bundel ESB.
**Error memblokir** langkah; **peringatan** dicatat.

| Pemeriksaan | Error / Peringatan |
|---|---|
| Cabang aktif tanpa baris `branch_map`; warehouse code > 5 / bentrok per company | Error |
| `dim_esb_branch.company_code` ∉ kode Odoo aktif (mis. IMD/CUP belum ada) | Error |
| Produk tanpa UoM / UoM tak ada di `uom_map` | Error |
| Produk FIN diproduksi tanpa rasio BATCH (`uom='NO_RATIO'`) | Error untuk BoM; peringatan untuk histori |
| Alias `NAME:` tak terpetakan yang masih punya qty | Peringatan (histori), error bila produk itu punya stok awal |
| Menu tanpa harga / harga ≤ 0; menu `" T"` tanpa menu dasar | Peringatan / peringatan |
| BOM: komponen tidak ada, qty ≤ 0, siklus (A memakai B memakai A), menu kit ke produk paket CK yang punya BoM kit | Error (aturan efn_kitting) |
| Paket tanpa komposisi terkonfirmasi | Peringatan (menu paket tidak dibuat) |
| Produk BCR tanpa pajak PPN BCR / produk F&B membawa pajak PT lain | Error (aturan pajak §6.4) |
| Stok: qty < 0, lokasi bukan milik PT produk, produk lot tanpa lot | Error |
| Partner B2B: payload memuat kunci PII yang tidak di-whitelist | Error (fail closed) |
| Standard price kosong untuk produk dengan stok awal | Error |

**Laporan rekonsiliasi** (Markdown/CSV per run, tanpa data pribadi, disimpan di berita acara):
1. Jumlah per entitas: sumber ClickHouse vs dibuat / diperbarui / dilewati / gagal di Odoo.
2. Stok: Σ qty per lokasi × produk (opname vs quant) = 0 selisih; Σ nilai persediaan vs saldo akun persediaan TB ESB.
3. AR/AP: Σ per partner dan total vs TB ESB; jumlah dokumen terbuka.
4. Neraca pembukaan seimbang; saldo kliring = 0 setelah stok + TB + AR/AP diposting.
5. Histori (bila model Odoo dibangun): Σ qty & Σ net per bulan × cabang Odoo vs `v_esb_sales_daily`; Σ aliran FIN vs
   `v_esb_flow_daily`; selisih harus 0.
6. Sanity bisnis: Σ (`total − discount`) vs Σ pembayaran per cabang-bulan dalam rentang PB1+SC yang wajar (G41: ≈ 89,6 %
   untuk PB) — di luar rentang → peringatan (mungkin cabang PPN/BCR atau SC berbeda).
7. Kualitas data: tarik `v_esb_data_quality` terbaru (alias, non-EA, umur data) sebagai lampiran.

---

## 7. Histori 24 bulan — perbandingan opsi

Ukuran (perkiraan dari README G41, bukan data klien): ±450 baris penjualan per toko-hari; ±50 toko lima brand (PB
±25 dari 41 cabang) → **±10–16 juta baris** tingkat baris 24 bulan; agregat harian outlet × menu (±100 menu aktif
per toko-hari) **±3–4 juta**; aliran FIN harian outlet × produk (jarang terisi) **±1–2 juta**; bulanan outlet × menu
**±0,3–0,5 juta**.

| Kriteria | (a) Model read-only `efn_legacy_*` di Odoo | (b) `pos.order` arsip / `sale.order` | (c) Tetap di ClickHouse + laporan via Insight |
|---|---|---|---|
| Valuasi stok | Aman (tidak membuat `stock.move`) | **Rusak**: pesanan POS yang di-*post*/sesi ditutup membuat picking & `stock.move` → stok dan nilai persediaan dikurangi dua kali (sebelum dan sesudah opname). Tanpa posting → data "menggantung" di status `paid` | Aman |
| Pajak | Aman (tidak ada `account.tax` / jurnal) | **Rusak**: penutupan sesi membuat jurnal penjualan & PB1 ganda atas periode yang sudah dilaporkan dari ESB | Aman |
| Penomoran | Aman | **Rusak**: `pos.order` menghabiskan `order_seq_id`/`pos_reference` config [V]; `name` dihitung saat `paid` [V]; `sale.order` memakai sequence SO | Aman |
| Prasyarat teknis | Model + ACL + record rule baru | `pos.order` butuh sesi; `_process_order` menolak sesi tertutup [V] → harus membuka/menutup sesi buatan per hari × outlet (±35 rb sesi) | User ClickHouse/Insight sudah ada (G42) |
| Laporan Odoo | Hanya laporan khusus (pivot/graph model baru); tidak muncul di laporan POS/akuntansi standar (justru diinginkan) | Muncul di laporan POS/penjualan standar — tetapi bercampur dan menggandakan angka | Tidak di Odoo; Insight/Metabase sudah membaca `v_esb_*` |
| Forecast v1 (`efn_demand_forecast`) | **Bisa** bila `_efn_daily_outflow` membaca model legacy untuk hari < cutover | Otomatis (via `stock.move`) tetapi dengan valuasi rusak | **Tidak bisa**: v1 membaca `stock_move` done keluar dari gudang, 56 hari [R] → 8 minggu pertama setelah cutover usulan min/max kosong/terlalu rendah |
| Ukuran PostgreSQL | Kecil bila agregat ramping (≤ 2 juta baris, ±0,5–1 GB dengan indeks); besar bila tingkat baris (10–16 juta) | Sangat besar (order + line + payment + sesi + jurnal) | 0 (sudah ada di ClickHouse, ZSTD) |
| Multi-company / record rule | `company_id` + rule standar per model (pola efn_*) | Standar | Row policy ClickHouse per PT (G42, `efn_insight` cids) |
| Dampak profil dev (K25-06 ≤ 8 GB) | Kecil (data demo kecil, model kosong) | Berat | Tidak ada |
| Pembalikan | Mudah (drop model/data) | Sulit (jurnal & stok sudah terposting) | Mudah |

**Rekomendasi: (c) sebagai arsip & sumber laporan histori, ditambah (a) versi ramping khusus kebutuhan operasional
di Odoo. Opsi (b) ditolak.**

1. **(c) tetap sumber kebenaran histori**: semua grain (baris penjualan, pembayaran, produksi, delivery, transfer)
   tetap di `efn_dwh.*esb*` (retensi 10 tahun, L-109) dan dibaca Insight/Metabase/Tanya Data. Laporan "histori
   penjualan per outlet/menu" = halaman Insight, bukan Odoo.
2. **(a) ramping, satu model wajib**: `efn.legacy.flow.daily` — grain **hari × company × gudang × produk storable**
   (FIN/paket CK, dan bahan bila kelak ada), kolom qty EA/UoM produk: `sold`, `ck_prod`, `store_prod`, `ck_out`,
   `in_ck`, `in_other`, `st_in`, `st_out`, plus `outflow` turunan (definisi = definisi keluar stok forecast v1:
   `sold + st_out + ck_out`). Sumber = `v_esb_flow_daily` setelah `product_code`→`product.product` dan
   `branch_id`→`stock.warehouse` lewat `map_esb_odoo`. Rentang default **400 hari** sebelum cutover (cukup untuk 56
   hari + faktor musim tahunan), parameter hingga 730. Read-only untuk semua user (ACL hanya read; tulis hanya oleh
   skrip migrasi lewat `sudo` + konteks khusus), `company_id` + record rule multi-company, tanpa `mail.thread`.
3. **Forecast v1**: engineer menambah satu hook di `_efn_daily_outflow`: untuk tanggal < `efn_mig_esb.cutover_date.
   <company_code>` jumlahkan `efn.legacy.flow.daily.outflow`; sejak cutover tetap `stock.move`. Tanpa cutover
   parameter → perilaku lama.
4. **Opsional (hanya bila klien meminta laporan histori di dalam Odoo, Q-ESB20)**: `efn.legacy.sales.monthly`
   (bulan × company × `pos.config` × produk menu: qty, gross, discount, net, cogs, trx). Bulanan, bukan harian, agar
   tetap ±0,5 juta baris.
5. Usulan efn-lead (model harian per outlet × menu + produksi + transfer) disederhanakan: menu harian tidak dibutuhkan
   proses Odoo mana pun (forecast bekerja di level produk stok), dan produksi/transfer sudah tercakup kolom aliran.

Batasan yang harus ditulis di ADR-042: (i) histori menu non-FIN (minuman/olahan di outlet) tidak memberi outflow
bahan baku karena resep historis tidak di-explode — forecast bahan baku butuh G44 (ClickHouse); (ii) waste/scrap ESB
tidak ada di mart → outflow legacy sedikit lebih rendah dari outflow Odoo (scrap = keluar ke lokasi scrap, bukan
`customer/production/internal/transit` → **tidak** dihitung forecast v1 juga, jadi konsisten); (iii) pemetaan produk
legacy yang gagal (alias `NAME:`) tidak masuk model, dilaporkan.

---

## 8. Multi-company

| PT | Sumber cabang | Catatan migrasi |
|---|---|---|
| PB | brand `pb`, CK 4 & 38 | Sudah ada di Odoo (`company_fnb`) [R] |
| WTZ | brand `wtz`, CK 5 & 6 | Ada (`company_wtz`) [R]; Q-ESB2 terjawab |
| BCF | brand `bacha` default | Kafe, storage, gudang |
| BCR | brand `bacha` override (cabang Retail #6; #10 menunggu Q-ESB10) | Produk kembar BCR; pajak PPN (`tax_ST4`) [R]; `account_price_include` BCR (Q-9) menentukan arti `list_price` — harga ESB exclude pajak harus dikonversi bila BCR tax-included |
| IMD | brand `imd`, CK 2 | Company baru placeholder (G43-A). Master boleh dimuat; **saldo awal & histori menunggu Q-ESB5** (NPWP, PB1/PPN) |
| CUP | brand `cup`, CK 4 & 6 | Idem; CK bersama dengan WTZ? (Q-ESB11) |

- Semua record migrasi `company_id` = PT dari `dim_esb_branch.company_code` (cabang) atau PT pemilik produk; tidak ada
  produk/partner tanpa company (hindari kebocoran katalog antar-PT; Q-8 default).
- Transfer ESB antar cabang **beda PT** (mis. gudang BCF → retail BCR): histori dicatat apa adanya di model legacy
  (sisi asal/tujuan per company); transaksi sejak cutover memakai `efn_intercompany` [R].
- Insight (G42) otomatis memberi cids IMD/CUP setelah company ada [kontrak G43-A].

---

## 9. Entitas yang perlu ditambah ke `esb-extract` (pemilik: efn-data-ai-engineer, perlu revisi docs/04 §25)

Semua GET, redaksi L-110 sebelum `raw_doc`, master snapshot harian kecuali disebut lain.

| # | Endpoint [V path, spec 18 Nov] | Tujuan | Redaksi |
|---|---|---|---|
| E1 | `/product/bom/{bomID}` (per BOM aktif) | Komponen resep: `bomDetails` (qty, uom, `convertionQty`, `lastHpp`, `yieldPercent`), header `bomTypeID/Name`, `productCode`, `uomName` → tabel baru `dim_esb_bom`, `dim_esb_bom_line` | Buang `createdBy/editedBy`, `map-user`/`user-access` tidak ditarik |
| E2 | `/menu/browse-for-product` | Harga menu + `bomName` (tautan menu → resep) → kolom `price`, `bom_name` di `dim_esb_menu` | — |
| E3 | Komposisi paket menu | **Endpoint belum ditemukan [B]**; sementara diturunkan dari `fact_esb_sales_line` | — |
| E4 | `/customer` | Customer B2B → `dim_esb_customer` | **Whitelist**: `customerID`, `customerCode`, `customerName`, `customerCategoryName`, `paymentDueDays`, `flagActive`, `lockVat`; buang `address`, `picName`, `picPhone` |
| E5 | `/location`, `/location/branch/{branchID}` | Sub-lokasi stok per cabang → `dim_esb_location` | — |
| E6 | `/inventory/stock-opname` + `/{stockOpnameNum}` (rentang tanggal cutover) | Qty opname cutover per lokasi × produk | Buang nama petugas |
| E7 | `/purchase/purchase-invoices`, `/payable-settlement/{num}`, `/finance/disbursement` | AP terbuka per dokumen (sisa, jatuh tempo) | Supplier = kode saja |
| E8 | AR faktur B2B & `/customer-advance-settlement` | AR terbuka & uang muka | **Endpoint faktur AR [B]**; buang `creator/editor/authorizer` |
| E9 | `/accounting/coa` | Daftar akun untuk `coa_map` | — |
| E10 | TB/GL per tanggal | Neraca pembukaan | **Endpoint [B]**; fallback ekspor Finance |
| E11 | Biaya per produk (HPP) | `standard_price` cutover | `lastHpp` dari E1 [V]; endpoint biaya produk [B] |
| E12 | `/product/{productID}` (bila `uomName` kosong di daftar) | Satuan & konversi produk | **Isi respons [B]** |
| E13 | `/customer-category-pricelist`, harga per cabang (bila ada) | Pricelist | **[B]** |

E1, E2, E4, E5 = **wajib sebelum dry-run master**; E6–E10 = wajib sebelum cutover (hanya rentang sekitar cutover,
beban kecil). Stok harian (`stock`) tetap opsional.

---

## 10. Audit modul custom terkait (duplikasi standar?)

| Modul | Temuan | Rekomendasi |
|---|---|---|
| `efn_kitting`, `efn_ck_production` | Memakai BoM/rute/scrap CE; tidak menduplikasi [R] | Tetap. Migrasi memakai flag `efn_is_kitting_package` & `efn_is_central_kitchen` |
| `efn_demand_forecast` | Membaca `stock_move` 56 hari [R]; tanpa histori legacy → usulan kosong 8 minggu | Tambah hook legacy (§7 butir 3), bukan model forecast baru |
| `docs/templates/migrasi/` | Template pilot ritel (kode PT lama `RTL/FNB` di aturan poin §6.4) | Perbarui kode PT ke §12.1 (GL/PB/…/IMD/CUP) saat validator dibangun |
| `api-gateway/src/migration` | Endpoint member/poin idempoten, `source_ref` opak [R] | Dipakai apa adanya; laporan rekonsiliasi total per PT masih "Belum" di README |

Tidak ada modul yang perlu dihapus.

---

## 11. Rekomendasi untuk efn-architect (ADR-042, docs/04 §27) dan efn-odoo-engineer (fase 2)

1. ADR-042: putuskan **(c) + (a) ramping** (§7); tolak (b) dengan alasan valuasi/pajak/penomoran; cantumkan batasan
   (i)–(iii).
2. §27: skema external ID `efn_mig_esb.<brand>_<company>_<entity>_<key>` + normalisasi + hash; daftar field milik
   migrasi per entitas; tabel pemetaan CSV (branch/uom/coa/payment) sebagai input yang disetujui klien.
3. §27: alur export (stack data) → import (`odoo shell`) → writeback `map_esb_odoo`; user ClickHouse `efn_mig`
   (SELECT ESB, INSERT `map_esb_odoo`), TLS; bundel terenkripsi & retensi.
4. §27: CLI `scripts/migration/esb_to_odoo.py --brand --step --cutover-date --dry-run`; langkah §4; laporan §6.
5. Engineer: modul `efn_legacy_history` (model `efn.legacy.flow.daily`, read-only, multi-company rule, pivot/graph,
   tanpa menu tulis) + hook `_efn_daily_outflow` di `efn_demand_forecast` (dependency terbalik dihindari: hook lewat
   method yang bisa di-override modul legacy, mis. `_efn_legacy_outflow()` mengembalikan `{}` di forecast dan
   di-override `efn_legacy_history`).
6. Stok awal & neraca pembukaan **tidak** butuh kode Odoo baru — fitur standar `stock.quant` + `account.move`;
   skrip migrasi yang mengisi.
7. Data-AI: perluasan ekstraktor E1–E13 (§9) + DDL tabel baru + redaksi; ubah default `ESB_CK_BRANCHES`.

---

## 12. Pertanyaan klien baru (melanjutkan Q-ESB1..9)

- **Q-ESB10** (Finance/Ops) Cabang Bacha "B2B Consignment" (branchID 10): masuk **BCR** (ritel PPN) atau **BCF**? Barang
  konsinyasi dicatat milik Bacha sampai laporan penjualan mitra?
- **Q-ESB11** (Ops/Finance) CK yang muncul di akun WTZ **dan** CUP: satu dapur fisik bersama? Siapa PT pemiliknya?
  Default kami: dua gudang (satu per PT) di alamat yang sama, tanpa inter-company; bila satu PT memproduksi untuk PT
  lain → inter-company + harga transfer (Q-3).
- **Q-ESB12** (Ops per brand) Komposisi paket menu (isi tetap vs pilihan); kami akan mengirim daftar kandidat dari pola
  penjualan untuk dikonfirmasi.
- **Q-ESB13** (Ops) Per cabang non-toko: Warehouse/Storage = situs terpisah (gudang sendiri) atau ruang di CK/kantor?
  Stok di cabang Damage/Expired/Temporary dimigrasi (karantina) atau dihapusbukukan sebelum cutover?
- **Q-ESB14** (Ops) Produk FIN yang dibuat di toko (store production): tetap diproduksi di Odoo toko (MO di outlet)
  atau dipindah ke CK?
- **Q-ESB15** (Ops/Finance) Harga menu sama di semua cabang brand atau berbeda per cabang/kota? Harga menu takeaway
  (`" T"`) sama dengan dine-in?
- **Q-ESB16** (Finance) Metode biaya persediaan per kategori (Standard/AVCO/FIFO), valuasi otomatis atau manual, dan
  akun "Saldo Awal – Kliring" untuk stok/AR/AP pembukaan.
- **Q-ESB17** (CK/QC) Qty komponen BOM ESB = bruto (sudah memperhitungkan yield) atau neto? Bahan pengganti
  (substitution) perlu dipertahankan?
- **Q-ESB18** (Finance) Laporan laba-rugi per outlet dibutuhkan (analitik per cabang) sejak cutover?
- **Q-ESB19** (Marketing/DPO) Data member & poin saat ini ada di mana (ESB, Eraspace, sistem lain)? Ada bukti consent?
- **Q-ESB20** (Manajemen) Apakah laporan histori penjualan cukup di Insight, atau wajib terlihat di dalam Odoo
  (menentukan model bulanan opsional)?
- **Q-ESB21** (Finance/IT) Apakah ESB menyediakan laporan neraca (TB) dan daftar faktur piutang B2B terbuka lewat API,
  atau Finance menyerahkan ekspor resmi per tanggal cutover?
- **Q-ESB22** (Ops) Pesanan pembelian, transfer, dan delivery yang masih berjalan saat cutover: diselesaikan di ESB
  sebelum freeze (rekomendasi) atau dibuat ulang di Odoo?
- **Q-ESB23** (PO) Urutan cutover per brand (rekomendasi: satu brand pilot — PB atau brand kecil — lalu brand lain),
  dan apakah IMD/CUP boleh migrasi master sebelum Q-ESB5 terjawab.

---

## 13. Belum terverifikasi

- Endpoint ESB: komposisi paket menu, faktur AR B2B, TB/GL, biaya produk, isi `/product/{productID}`, arti
  `bomTypeID`; spec dibaca sebagian (file 9,6 MB, potongan yang relevan saja).
- CE 19: analitik di `pos.config`/sesi POS; pricelist per `pos.preset`; perilaku valuasi otomatis untuk penyesuaian
  stok bertanggal mundur (`accounting_date`); keamanan `ir.model.data` dengan module `efn_mig_esb` yang tidak
  terpasang sebagai addon (pola `__import__` impor CSV CE) — uji dry-run.
- Perkiraan ukuran histori = turunan README G41, bukan hitungan dari ClickHouse.
- Tidak ada kode yang ditulis; tidak ada test yang dijalankan.
