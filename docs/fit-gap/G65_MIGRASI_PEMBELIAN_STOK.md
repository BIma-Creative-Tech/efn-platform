# G65 — Addendum fit-gap migrasi ESB → Odoo: histori pembelian, harga beli supplier, HPP cutover, stok awal (PB)

Penulis: efn-odoo-functional, 24 Nov 2026. Untuk: efn-odoo-engineer, efn-data-ai-engineer (ekstraksi), efn-lead.
Melengkapi `docs/fit-gap/G43_MIGRASI_ESB_ODOO.md` (§2.2 harga pokok, §4 langkah 8–10, §5.2 stok awal). Bila
bertentangan, dokumen ini menang untuk keempat butir di bawah, **khusus PB**, sampai docs/04 §27 diperbarui.
ADR: ADR-042 (migrasi & histori, *Proposed*), ADR-031 (tanpa modul pihak ketiga), ADR-001 (satu DB multi-company).

**Keputusan user 24 Nov 2026:** brand PB saja; target efn-prod lokal (gladi); **cutover uji 2026-12-01** (saldo awal per
akhir 2026-11-30); hanya **stok awal** — neraca pembukaan & AR/AP tetap NOT_BUILT (Q-ESB21).

Legenda: **[V]** terverifikasi di source `odoo/odoo@19.0` (GitHub, diperiksa 24 Nov 2026) atau di kode repo (file
disebut); **[R]** rekomendasi; **[B]** belum terverifikasi — wajib dibuktikan engineer di dry-run/test.
Tidak ada referensi OCA/Cybrosys yang diperlukan: tiga dari empat butir tertutup CE standar; butir 1 meniru pola
arsip `efn_legacy_history` yang sudah ada.

---

## Ringkasan fit-gap

| # | Kebutuhan | Standar CE 19 | Custom `efn_*` | Sumber ESB | Status |
|---|---|---|---|---|---|
| 1 | Histori pembelian 24 bulan, bisa dilihat di Odoo | Tidak cocok (`purchase.order` = transaksi hidup, lihat §1.1) | **Model arsip baru** `efn.legacy.purchase.line` di `efn_legacy_history` | `/report/purchase/purchase-recapitulation/purchase` + header `/purchase/purchase-order` | Gap → engineer |
| 2 | Harga beli per supplier | `product.supplierinfo` [V] | Tidak ada (hanya langkah importer `supplierinfo`) | Turunan butir 1 (PO terakhir 12 bulan) | Standar |
| 3 | HPP cutover per PT | `standard_price` company-dependent [V] + `property_cost_method` kategori [V] | Tidak ada (langkah `cost` sudah ada, ganti sumber) | `/report/stock-movement` baris `Beginning` | Standar |
| 4 | Stok awal per cabang | `stock.quant` mode inventaris + `accounting_date` [V] | Tidak ada (langkah `stock` NOT_BUILT → dibangun di importer) | `/report/stock-movement` baris `Beginning`, D = 2026-12-01 | Standar |

Urutan wajib (menggantikan G43 §4 langkah 8–10 untuk PB): master (1–7) → **histori pembelian** → **supplierinfo**
→ **cost** → freeze ESB → **stock** → closing valuasi (opsional, §4.5) → rekonsiliasi.

---

## 1. Histori pembelian 24 bulan → `efn.legacy.purchase.line` (gap, custom)

### 1.1 Kenapa bukan `purchase.order`
- **Stok ganda.** Dengan `purchase_stock` terpasang, konfirmasi PO membuat picking + `stock.move` penerimaan. Histori
  yang sudah diterima di ESB akan masuk stok kedua kali di atas stok awal (§4), atau dibiarkan "belum diterima" sehingga
  qty masuk (forecast/orderpoint `efn_replenishment`) salah selamanya.
- **Jurnal & pajak ganda.** Tagihan vendor dari PO membuat `account.move` + PPN masukan yang sudah ada di buku ESB
  (dan nanti di saldo awal/AP, Q-ESB21).
- **Nomor dokumen ganda.** Sequence `purchase.order` (P0xxxx) terpakai ±15 rb dokumen, atau nomor ESB dipaksa ke `name`
  sehingga bercampur dengan PO baru; `purchase.report` dan ETL Insight (`fact_dc_purchase_line`) ikut menelan histori.
- **Volume & risiko:** ±60 rb baris lewat ORM PO (compute pajak, onchange, chatter) jauh lebih berat dan tidak bisa
  dihapus bersih bila perlu diulang. Model arsip = baca saja, `DELETE + INSERT` per irisan, tanpa efek samping.

### 1.2 Model (rekomendasi untuk engineer; pola sama dengan `efn.legacy.sales.line` [V repo])
`_name = "efn.legacy.purchase.line"`, `_check_company_auto = True`, `_log_access = False`, `_order = "date desc, id desc"`,
`_rec_name = "purchase_num"`, semua field `readonly=True`, create/write/unlink lewat `check_import_allowed` [V repo `bulk.py:56`].

| Field | Tipe | Sumber ESB | Catatan |
|---|---|---|---|
| `company_id` | M2o `res.company`, required | cabang → PT (`dim_esb_branch.company_code`) | |
| `date` | Date, required | `purchaseDate` | kunci irisan |
| `date_required` | Date | `requiredDate` | opsional |
| `purchase_num` | Char(32), required | `purchaseNum` | nomor dokumen ESB, bukan PII |
| `warehouse_id` | M2o `stock.warehouse`, **nullable**, `check_company` | `branchID` (dari header PO) → `branch_map` | HO/cabang `skip` → kosong |
| `branch_name` | Char(64) | `branchName` | teks asli, selalu diisi |
| `supplier_id` | M2o `res.partner`, nullable, `check_company`, `ondelete=set null` | `supplierID` (header) → xmlid `supplier` | |
| `supplier_name` | Char(128) | `supplierName` | nama perusahaan saja (L-110) |
| `product_id` | M2o `product.product`, nullable, `check_company`, `ondelete=set null` | `productCode` → xmlid `product` | |
| `esb_product_code` | Char(32), required | `productCode` | |
| `product_name` | Char(128) | `productName` | |
| `category_name` / `sub_category_name` | Char(64) | `categoryName` / `subCategoryName` | kategori ESB asli (bukan pohon G61) |
| `uom_name` | Char(16) | `uomName` | teks ESB; **tidak** dikonversi |
| `qty`, `receipt_qty` | Float (16,3) | `qty`, `receiptQty` | satuan = `uom_name` |
| `pricelist_price` | Float (16,2) | `pricelistPrice` | |
| `price_unit` | Float (16,2), `aggregator="avg"` | `price` | Rupiah |
| `discount` | Float (16,2) | `discount` | **nominal atau %: [B]** (Q-G65-2) |
| `vat_value`, `total` | Float (16,2) | `vatValue`, `total` | |
| `status` | Char(32) | `statusName` | Char, bukan Selection (nilai ESB belum diketahui lengkap) |
| `cost_center` | Char(64) | `costCenterName` | opsional; `projectName` dibuang kecuali Finance minta |

**Dibuang (PDP L-110 / teks bebas):** `notes`, `additionalInfo`, `createdBy`, `emailSupplier` dan semua field orang.
**Tanpa** kolom mata uang per baris (IDR, sama dengan sales line); bila header `currencyID` ≠ IDR → baris ditolak & dilaporkan.

- **Indeks:** `(company_id, date)`, `(company_id, product_id, date)` (lookup harga beli & butir 2),
  `(company_id, supplier_id, date)`; `purchase_num` btree biasa (60 rb baris, murah). Tanpa constraint unik — laporan
  rekap tidak punya ID baris.
- **Idempotensi:** ganti irisan **company × bulan** (`DELETE … WHERE company_id = %s AND date >= %s AND date < %s` lalu
  INSERT). **Gap teknis:** `bulk_replace_slice` sekarang mewajibkan `warehouse_id` [V repo `bulk.py:414-505`] →
  engineer menambah varian irisan tanpa gudang (atau parameter `warehouse_id=None` = seluruh PT), bukan memaksa
  gudang dummy.
- **Record rule** global `[('company_id', 'in', company_ids)]`, `noupdate="1"`, sama dengan tiga model arsip yang ada [V repo].
- **ACL:** `purchase.group_purchase_user` baca saja (`1,0,0,0`); manager mewarisi user. Tidak ada grup tulis.
- **Menu:** `purchase.purchase_report_main` (Purchase › Reporting) hanya untuk `purchase.group_purchase_manager` [V
  `purchase/report/purchase_report_views.xml`] → user pembelian tidak akan melihat anak menu di sana. Rekomendasi:
  menu baru **Purchase › Histori ESB (arsip)** (parent `purchase.menu_purchase_root` [V `purchase/views/purchase_views.xml`],
  sequence 98, grup `purchase.group_purchase_user`) + entri tambahan di Inventory › Reporting › Histori ESB (root yang
  sudah ada [V repo `views/menus.xml`]) untuk `stock.group_stock_user` **hanya bila** Ops gudang perlu (Q-G65-4).
  Tampilan: list + pivot + graph, filter default 3 bulan terakhir, group by supplier/produk/bulan.
- **Depends:** tambahkan **`purchase`** ke `efn_legacy_history` (untuk grup, menu, dan relevansi supplierinfo).
  `purchase` sudah ada di DB efn-prod lewat `purchase_stock` (depends `efn_assortment`, `efn_agri`, `efn_intercompany`
  [V repo manifest]) — satu DB, jadi tersedia juga untuk PT PB [R; status instal runtime [B], cek
  `ir.module.module` di efn-prod]. Alternatif modul jembatan `efn_legacy_history_purchase` hanya bila ada DB tanpa
  `purchase` — tidak ada saat ini, jadi tidak perlu.

### 1.3 Ekstraksi & pemetaan (efn-data-ai-engineer)
- Baris: `/report/purchase/purchase-recapitulation/purchase` per bulan (paginasi `page/limit`), 24 bulan
  `[2024-12-01, 2026-11-30]`. Header `/purchase/purchase-order?dateFrom&dateTo` per bulan → peta `purchaseNum →
  (branchID, supplierID, currencyID, statusID)`. **Join per `purchaseNum`**, bukan pencocokan nama; nama hanya
  fallback dengan laporan "dipetakan via nama" (Q-G65-1).
- PO dengan status batal/draft **ikut disimpan** (arsip lengkap, field `status`); filter dilakukan di butir 2.
- Baris tanpa `product_id` terpetakan tetap disimpan (teks), dihitung di laporan rekonsiliasi.

---

## 2. Harga beli supplier → `product.supplierinfo` (standar)

### 2.1 Fakta CE 19 [V `product/models/product_supplierinfo.py`]
- Model di modul **`product`** (bukan `purchase`); `purchase` hanya memakainya untuk RFQ/orderpoint.
- Field: `partner_id` (required, `check_company`), `product_tmpl_id` (required, compute dari `product_id`, stored),
  `product_id` (varian, opsional), `company_id` (default company aktif), `currency_id` (required, default mata uang
  company), `price`, `discount` (**%**), `price_discounted` (compute), `min_qty` (required, default 0),
  `product_uom_id` (compute dari produk **hanya bila kosong**, stored, editable, required — **bukan** `product_uom`),
  `date_start`, `date_end`, `sequence` (default 1), `delay` (default 1), `product_code`, `product_name`.
- Tidak ada field `company_dependent`; satu record per company (supplier PB milik PT PB).
- `_order = "sequence, min_qty DESC, price, id"` → urutan prioritas supplier ditentukan `sequence`.
- Tidak ada constraint UoM di supplierinfo [V]; Odoo 19 tanpa kategori UoM (G43 §2.2) → konversi harga harus eksplisit.

### 2.2 Aturan pembentukan [R]
- Jendela: PO **12 bulan** terakhir sebelum cutover `[2025-12-01, 2026-11-30]` dari `efn.legacy.purchase.line`
  (atau langsung dari datamart, sumber sama), hanya `status` yang berarti PO sah (nilai persis [B], Q-G65-3),
  `qty > 0`, `price_unit > 0`, `supplier_id` dan `product_id` terpetakan.
- Kunci = (company, supplier, product template) → satu record; xmlid entitas `supplierinfo` sudah dipesan di
  `xmlid.py` [V repo] (`<brand>_<company>_supplierinfo_<kode supplier>_<kode produk>`).
- Isi: `price` = `price_unit` PO **terakhir** (tanggal terbaru, tie → `purchase_num` terbesar); `discount` = %
  (konversi dari nominal bila Q-G65-2 = nominal); `currency_id` = IDR; `min_qty = 0`; `date_start` = tanggal PO terakhir;
  `date_end` kosong; `product_id` kosong (PB tanpa varian); `delay` default.
- **Satuan:** bila `uom_name` ESB → `uom_map.csv` = UoM produk → `product_uom_id` = UoM produk, harga apa adanya. Bila
  beda (mis. beli per KARTON, stok per PCS) → isi `product_uom_id` = UoM beli hasil peta **dan** harga per UoM itu
  (CE mengonversi saat membuat RFQ). Bila UoM beli tidak terpetakan → konversi harga ke UoM produk dengan
  `convert_price` yang sudah ada [V repo `mapping.py:422`] (`ea_factor`), laporkan. Perilaku RFQ dengan
  `product_uom_id` ≠ UoM produk di CE 19 **[B]** (Odoo 19 menghapus `uom_po_id`? [B]) → engineer uji satu produk.
- `sequence`: supplier dengan PO terakhir paling baru = 1, berikutnya 2, … (prioritas RFQ otomatis).
- Upsert: field milik migrasi = `price`, `discount`, `date_start`, `product_uom_id`, `sequence`; record yang dibuat
  user setelah migrasi tidak disentuh. Supplier yang tidak lagi muncul di 12 bulan → **tidak** dihapus, dilaporkan.
- Hapus catatan manifest "supplierinfo: belum ada sumber ESB" di `export.py` [V repo `:48`, `:266-267`].

---

## 3. HPP cutover → `standard_price` (standar)

### 3.1 Fakta CE 19
- `standard_price` = `company_dependent=True`, help "Used to value the product when the purchase cost is not known
  (e.g. inventory adjustment)" [V `product/models/product_product.py`] → nilai per PT, dan **itulah** biaya unit stok
  awal.
- Metode biaya = `property_cost_method` kategori (company-dependent), fallback `res.company.cost_method` (default
  `standard`) [V `stock_account/models/res_company.py`, `stock_quant._compute_cost_method`].
- Valuasi = `property_valuation` kategori, fallback `res.company.inventory_valuation` (default **`periodic`**) [V].
- **Konfigurasi PB di repo:** `scripts/migration/recategorize_pb.py` (G61) mengisi `property_cost_method = average`
  untuk Persediaan / Bahan Baku, Kemasan, WIP, Barang Jadi, Merchandise; `property_valuation` **tidak** diisi →
  `periodic` (tidak ada modul `efn_*` yang menulis `real_time`/`inventory_valuation` [V repo grep]). Placeholder
  sampai Finance (Q-ESB16). Status di efn-prod [B] (cek kategori PB).
- `product.product.write` dengan `standard_price` memanggil `_change_standard_price` (revaluasi) kecuali konteks
  `disable_auto_revaluation` [V `stock_account/models/product.py`]. AVCO: move masuk saat stok 0 → biaya rata-rata
  = nilai unit move itu (`_run_average_batch`) [V].

### 3.2 Aturan [R]
- Sumber utama: baris `transactionType="Beginning"` dari `/report/stock-movement` dengan `startPeriod=endPeriod=2026-12-01`
  (= saldo akhir 30 Nov) — **ekstrak yang sama** dengan stok awal §4 (satu snapshot, dua pemakaian → HPP dan qty konsisten).
- HPP per (PT, produk) = `Σ amountBalance / Σ qtyBalance` atas **semua** cabang & lokasi PT itu, setelah qty dikonversi ke
  UoM produk (AVCO Odoo per company, bukan per gudang). Baris `qtyBalance ≤ 0` dikeluarkan dari pembilang & penyebut.
- Fallback berurutan (dicatat `cost_source` di laporan): (1) `lastHpp` BOM (langkah `cost` sekarang [V repo
  `export.py:211`]); (2) `price_unit` PO terakhir 12 bulan (butir 2, dikonversi ke UoM produk); (3) kosong → produk
  **diblok** dari stok awal bila qty > 0 (bukan nol diam-diam).
- Pembulatan ke presisi `Product Price`; nilai ESB `int` (Rupiah bulat?) [B].
- **Wajib sebelum stok awal.** Importer sudah menolak ubah HPP produk berstok (`conflict_has_stock`) [V repo
  `importer.py:906-907`] — pertahankan. Konsekuensi: HPP salah setelah stok masuk = revaluasi manual Finance. Untuk
  gladi: reset DB/hapus stok uji dulu (runbook `efn-prod-odoo-reset`), atau jalankan `cost` sebelum `stock` di setiap
  ulangan.
- Metode `standard`: HPP ini tetap sampai diubah. Metode `average` (default PB): menjadi rata-rata awal, lalu bergerak
  dengan penerimaan PO baru. FIFO: lapisan awal = satu lapisan harga ini. Metode akhir = Finance (Q-ESB16); ubah
  `property_cost_method` **sebelum** stok awal (saat 0 move aman, G61 §5).

---

## 4. Stok awal → `stock.quant` mode inventaris (standar)

### 4.1 Fakta CE 19 [V]
- `stock.quant.action_apply_inventory(self, date=None)`: bila ada quant `is_outdated` → **mengembalikan wizard**
  `stock.inventory.conflict` (tidak menerapkan); selain itu `_apply_inventory(date)` [V `stock/models/stock_quant.py`].
- `_apply_inventory(date)`: membuat `stock.move` `is_inventory=True` (konteks `inventory_mode=False`), `_action_done()`,
  lalu **`moves.date = date` bila `date` diberikan**; kemudian `action_clear_inventory_quantity()` [V].
- `stock_account` override: mengelompokkan quant per `accounting_date`; kelompok bertanggal dijalankan dengan konteks
  **`force_period_date = accounting_date`**, lalu `accounting_date` dikosongkan; `accounting_date` termasuk field yang
  boleh ditulis di mode inventaris [V `stock_account/models/stock_quant.py`]. Konteks itu **hanya** dipakai untuk
  tanggal `account.move` [V `stock_account/models/stock_move.py`] dan label move "[Accounted on …]" — **tidak** mengubah
  `stock.move.date`.
- Jurnal valuasi per move dibuat hanya bila `product.valuation == 'real_time'`, produk storable, move bernilai, lokasi
  punya `valuation_account_id`, qty ≠ 0 (`_should_create_account_move`) [V]. **PB = `periodic` → tidak ada jurnal saat
  apply.** Nilai persediaan tetap tercatat di move (`value`).
- Periodik: `res.company.action_close_stock_valuation(at_date=None, auto_post=False)` membuat satu jurnal "Stock Closing"
  di `account_stock_journal_id`: reklas antara `stock.location.valuation_account_id` tiap lokasi dan akun valuasi
  kategori untuk move periodik setelah closing terakhir s.d. `at_date`, plus selisih nilai stok vs saldo buku [V
  `stock_account/models/res_company.py`]. Cron `_cron_post_stock_valuation` harian/bulanan [V].
- `stock.location.valuation_account_id` = "akun beban untuk me-reklas produk yang keluar dari stok" (lokasi virtual,
  mis. Inventory adjustment) [V `stock_account/models/stock_location.py`]; lokasi bernilai = `internal`/`transit`
  ber-company [V].
- Quant untuk produk non-storable ditolak `ValidationError` "Quants cannot be created for consumables or services"
  [V `check_product_id`]; lot milik produk lain ditolak [V].

### 4.2 Sumber & pemetaan lokasi [R]
- Ekstrak `/report/stock-movement?startPeriod=2026-12-01&endPeriod=2026-12-01`, ambil hanya `transactionType="Beginning"`
  (`documentCode="Opening"`) → grain (branchID, location, productID). Ditarik **setelah freeze ESB** (tidak ada transaksi
  bertanggal ≤ 30 Nov yang masih bisa masuk); bila ada input mundur → ekstrak ulang + importer ulang (idempoten).
  Format filter `branchCode` (HTTP 400) dan `limit` maks [B] — efn-data-ai-engineer. Opname ESB (`stock_opname`, E6)
  = **validator** (selisih per produk dilaporkan), bukan sumber.
- Cabang: `branchID` → `branch_map.csv` (sudah ada [V repo `scripts/migration/README.md:84-92`]) → gudang (`type`
  store/ck/warehouse) atau lokasi `branch_loc` (`type=location`); `skip` / PT ≠ PB → tidak dimuat, dilaporkan.
- Sub-lokasi ESB (`location`, nama teks; `dim_esb_location` = referensi): **default dijumlahkan ke `lot_stock_id`
  gudang cabang** (gudang Odoo PB tidak dipecah per rak/dapur). Tabel opsional baru `location_map.csv`
  (`brand, branch_id, esb_location_name, action` ∈ `stock` | `skip` | `quarantine`) untuk lokasi Damage/Expired/
  Temporary (G43 §2.1, Q-ESB13). Nama lokasi tak terpetakan → `stock` + peringatan.
- Qty: kolom `UOM` per baris → UoM produk via `uom_map.csv` + `convert_qty` yang sudah ada; `unitToShow` disetel ke satuan
  dasar ESB bila bisa [B arti parameter]. UoM tak terpetakan → baris error (bukan default).

### 4.3 Aturan impor (langkah `stock`, sekarang NOT_BUILT [V repo `export.py:52`, `importer.py:183`]) [R]
1. Prasyarat dicek & fail-closed: `cost` sudah jalan (HPP terisi untuk semua produk qty > 0); tidak ada `stock.move`
   `done` non-migrasi di lokasi PB bertanggal ≥ cutover (bila ada → batal: penimpaan akan menghapus transaksi nyata);
   tidak ada sesi POS PB terbuka; `ir.config_parameter efn_mig_esb.cutover_date.pb = 2026-12-01` (digeser dari 17 Nov).
2. Filter: hanya `is_storable=True` (Persediaan G61); non-storable (Perlengkapan, Aset, Beban, Menu) **dilewati &
   dihitung** di laporan. Qty ≤ 0 → tidak dimuat, dilaporkan (stok negatif ESB = masalah data, bukan saldo).
3. Per (lokasi, produk): agregasi qty → cari/buat quant `(product_id, location_id, lot_id=False, package_id=False,
   owner_id=False)` dengan konteks `inventory_mode=True`, set **`inventory_quantity = target` (absolut)** dan
   `accounting_date = 2026-11-30`. Perilaku `create` mode inventaris pada quant yang sudah ada (tulis vs duplikat) [B].
4. **Ulangan idempoten:** hanya quant dengan `inventory_diff_quantity ≠ 0` yang di-apply (ulang dengan data sama = 0 move).
   Produk yang ada di Odoo tapi hilang dari snapshot: default **dilaporkan saja**; flag `--zero-missing` menyetel ke 0
   (hanya untuk gladi ulang).
5. Apply: `action_apply_inventory(date=<2026-11-30 23:59:59 WIB dalam UTC>)` agar `stock.move.date` = cutover−1
   (`accounting_date` saja tidak mengubah tanggal move, §4.1). Hasil berupa dict aksi (wizard konflik) → **error**,
   bukan diabaikan. Nama move: konteks `inventory_name = "Saldo awal ESB 2026-11-30"` [V konteks dibaca
   `_get_inventory_move_values`]. Efek `date` mundur pada AVCO/`_run_average_batch` dan laporan stok per tanggal [B].
6. Dry-run = satu transaksi + rollback (pola G43 §4). Laporan: qty & nilai (qty × `standard_price`) per gudang × kategori
   vs `Σ amountBalance` ESB (toleransi pembulatan), jumlah dilewati per alasan.

### 4.4 Lot
- Produk `tracking != 'none'` membutuhkan `lot_id` (ESB stock-movement tanpa lot). Default [R]: PB pilot **tanpa tracking**
  (cek jumlah produk PB dengan tracking di efn-prod [B]); bila ada → satu lot per produk `ESB-OPEN-20261130`
  (tanpa tanggal kedaluwarsa) dan dilaporkan, atau Ops mematikan tracking sebelum cutover (Q-G65-5). Serial: blok.

### 4.5 Akun lawan (kliring) [R; Finance]
- PB periodik → **tidak ada jurnal saat apply** [V]. Pengakuan nilai stok awal terjadi saat closing valuasi:
  `action_close_stock_valuation(at_date=2026-11-30)` mereklas nilai move dari lokasi *Inventory adjustment* (akun
  `valuation_account_id`-nya) ke akun persediaan kategori (`l10n_id_11300180`, G61).
- Karena neraca pembukaan **tidak** dikerjakan, set `valuation_account_id` lokasi Inventory adjustment PB = akun
  **"Saldo Awal – Kliring"** (dibuat Finance, placeholder) **selama cutover**, lalu kembalikan ke akun selisih persediaan
  sebelum opname rutin pertama. Saldo kliring = nilai stok awal sampai TB dimuat (Q-ESB21).
- Lokasi Inventory adjustment mana yang dipakai PB (per company atau global; `property_stock_inventory` produk) dan
  apakah `at_date` closing memfilter `stock.move.date` [B]. Bila Finance memilih `real_time` (Q-ESB16): jurnal dibuat
  saat apply dengan tanggal `accounting_date` [V `force_period_date`], lawan = `valuation_account_id` lokasi yang sama.
- Closing valuasi di gladi: jalankan dengan `auto_post=False` (draft) → Finance memeriksa; jangan aktifkan cron closing
  sebelum akun dikonfirmasi.

---

## 5. Pertanyaan klien baru (Q-G65)
1. **Q-G65-1** (IT/ESB) Laporan rekap pembelian tanpa `branchID`/`supplierID`: boleh join ke header PO per `purchaseNum`
   (rekomendasi), dan apakah `purchaseNum` unik lintas cabang? Default: unik per akun brand.
2. **Q-G65-2** (Finance) `discount` di rekap/PO ESB = nominal Rupiah per baris atau persen? Default: nominal → dikonversi ke %.
3. **Q-G65-3** (Finance/Purchasing) Status PO ESB mana yang dihitung sebagai harga beli sah (mis. Approved/Closed/Partial)?
   Default: semua kecuali yang mengandung "Cancel"/"Draft"/"Reject".
4. **Q-G65-4** (Ops) Siapa yang membaca histori pembelian di Odoo: tim Purchasing saja (default) atau juga kepala gudang/CK?
5. **Q-G65-5** (Ops/QC) Produk PB dengan lot/kedaluwarsa saat cutover: lot dummy saldo awal, atau tracking dimatikan dulu? Default: lot dummy.
6. **Q-G65-6** (Finance) Akun "Saldo Awal – Kliring" untuk stok awal (kode akun), valuasi tetap periodik untuk PB, dan HPP
   cutover = rata-rata `amountBalance/qtyBalance` ESB per PT? Default: ya untuk ketiganya (placeholder).
7. **Q-G65-7** (Ops) Lokasi ESB Damage/Expired/Temporary di cabang PB: dimuat ke karantina atau dilewati? Default: dilewati + dilaporkan.

## 6. Daftar [B] untuk engineer (wajib dibuktikan di test/dry-run efn-prod)
1. `purchase` (dan `purchase_stock`) berstatus `installed` di DB efn-prod.
2. Kategori PB di efn-prod: `property_cost_method = average`, `property_valuation` kosong → produk `valuation = periodic`.
3. `stock.quant.with_context(inventory_mode=True).create(...)` pada quant yang sudah ada: menulis `inventory_quantity`
   ke quant lama (bukan duplikat).
4. `action_apply_inventory(date=<datetime lampau>)`: `stock.move.date` = cutover−1, nilai move = qty × `standard_price`,
   `standard_price` AVCO tidak bergeser setelah apply (`_run_average_batch`), laporan stok "per tanggal" 30 Nov benar.
5. `action_close_stock_valuation(at_date=2026-11-30)` hanya mengambil move ≤ at_date (memakai `stock.move.date`),
   akun lawan = `valuation_account_id` lokasi Inventory adjustment PB; lokasi itu per company atau global.
6. `product.supplierinfo` dengan `product_uom_id` ≠ UoM produk → harga & qty RFQ benar di CE 19; apakah `uom_po_id`
   masih ada di `product.template` 19.0.
7. Arti `discount`, nilai `statusName`, tipe angka (`int` Rupiah) di rekap pembelian & stock-movement; format filter
   `branchCode`, `limit` maksimum, arti `unitToShow` (efn-data-ai-engineer).
8. Jumlah produk PB dengan `tracking != 'none'`.
9. Varian `bulk_replace_slice` tanpa `warehouse_id` (irisan company × bulan) tidak melanggar SEC-197/aturan irisan yang ada.
