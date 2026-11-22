# G61 — Kategori produk PB per peran akuntansi

Status: dibangun 23 Nov 2026 (efn-odoo-engineer), **belum dijalankan di efn-prod**. Spesifikasi teknis lengkap:
[G54_G55_G61_ODOO_SETUP.md §G61](G54_G55_G61_ODOO_SETUP.md#g61--restrukturisasi-kategori-produk-per-peran-akuntansi)
(efn-odoo-functional). ADR terkait: ADR-001 (satu DB multi-company), ADR-031 (tanpa modul pihak ketiga), ADR-042
(migrasi ESB). Semuanya *Proposed*.

## Ringkasan untuk manajer

- **Masalah.** Produk PB hasil migrasi ESB punya 52 kategori datar yang mengikuti nama ESB (`PB / Local - Dry`,
  `PB / FA LVA`, …). Ada juga 1.680 menu tanpa kategori. Semua produk diperlakukan sama: barang stok, bisa dibeli, dan
  dijurnal ke akun default PT. Aset, jasa, dan perlengkapan habis pakai tidak terpisah dari persediaan.
- **Perubahan.** Produk dikelompokkan menurut **peran akuntansinya**: Persediaan, Perlengkapan, Beban/Jasa, Aset Tetap,
  dan Menu. Setiap peran menentukan tiga hal:
  - apakah stok produk dihitung;
  - apakah produk bisa dibeli atau dijual;
  - ke akun mana produk dijurnal.
- **Pohon kategori dipakai bersama.** Pohonnya sama untuk PB, IMD, dan CUP (tanpa awalan merek), karena akun di
  kategori Odoo disimpan per PT. Supermarket (SMD) **tidak disentuh**.
- **Akun = placeholder.** Akun per peran sementara memakai akun standar CoA Indonesia (l10n_id). Finance perlu
  menetapkan akun final dan metode valuasi (Q-ESB16). Sampai dijawab, metode biaya = rata-rata (AVCO) dan valuasi =
  periodik.
- **Aman dijalankan sekarang.** PB belum punya pergerakan stok (0 move, 0 quant), jadi mengubah jenis produk tidak
  mengubah nilai persediaan. Skrip menolak jalan bila prasyarat ini tidak terpenuhi.
- **Keputusan yang masih terbuka** (default sudah dipakai, mudah diubah di tabel): A1–A11 di bawah dan Q-G61-1 (pohon
  tanpa awalan merek; default ya).

## Aturan per peran

| Peran | Akar kategori | Jenis produk | Stok dihitung | Bisa dijual | Bisa dibeli | Tampil di POS | Akun (placeholder) |
|---|---|---|---|---|---|---|---|
| Persediaan | `Persediaan / …` | barang | ya | tidak (Merchandise: ya) | ya; WIP & Barang Jadi: hanya bila ada pemasok (A2), FIN tidak pernah | tidak (Merchandise: tetap) | AVCO, valuasi `11300180`, beban `51000020` (Barang Jadi/Merchandise: HPP `51000010`, pendapatan `41000010`) |
| Perlengkapan | `Perlengkapan / …` | barang | **tidak** (dibebankan saat tagihan) | tidak | ya | tidak | beban `63110140` (Kantor & ATK: `63110120`) |
| Beban / Jasa | `Beban / Jasa / …` | **jasa** | tidak | tidak | ya | tidak | `65110080` pemeliharaan, `65110090` pengiriman/biaya impor, `65110010` perizinan, `65110040` sewa, lainnya `63110140` |
| Aset Tetap | `Aset Tetap / …` | barang | tidak | tidak | ya | tidak | `12210010` bangunan, `12210030` peralatan/mesin/furnitur, LVA `63110120` (dibebankan, A6), Perangkat Lunak **belum ada akun** (A10) |
| Menu | `Menu / <kategori POS teratas>` atau `Menu / Lainnya` | tetap (combo tetap combo) | tidak | ya | **tidak** | tidak diubah | HPP `51000010`, pendapatan `41000010` |

Akun ditulis sebagai `l10n_id_<kode>` per PT. Kategori baru langsung diisi akunnya. Kategori yang sudah ada hanya
diisi bila akunnya masih kosong, sehingga pilihan Finance tidak tertimpa.

## Pemetaan 52 kategori ESB PB

Sumber tunggal: [`scripts/migration/efn_mig/category_roles.csv`](../../scripts/migration/efn_mig/category_roles.csv),
dipakai export bundel dan skrip efn-prod. Untuk mengubah keputusan, ubah baris CSV (bukan kode), lalu jalankan export
ulang atau skrip lagi. Kategori ESB yang tidak ada di CSV menggagalkan proses (fail closed).

| Kategori ESB | Jml | Target | Ambiguitas |
|---|---|---|---|
| Inventory - Food & Beverage | 737 | Persediaan / Bahan Baku / Food & Beverage | A1 |
| Inventory - WIP | 661 | Persediaan / WIP | A2 |
| FA LVA | 423 | Aset Tetap / LVA | A6 |
| Packaging Material | 241 | Persediaan / Kemasan | A3 |
| FA Office & Outlet Equipment | 209 | Aset Tetap / Peralatan Kantor & Outlet | |
| FA Furniture & Fixture | 183 | Aset Tetap / Furnitur & Perlengkapan Tetap | |
| Local - Dry | 158 | Persediaan / Bahan Baku / Lokal - Dry | |
| Supplies | 105 | Perlengkapan / Operasional Toko | A4 |
| Import Ingredient | 102 | Persediaan / Bahan Baku / Impor | |
| WIP | 83 | Persediaan / WIP | A2 |
| Indirect Material | 79 | Persediaan / Bahan Baku / Bahan Penolong | A5 |
| Expense - Store Supplies | 69 | Perlengkapan / Operasional Toko | |
| Local - Fresh | 58 | Persediaan / Bahan Baku / Lokal - Fresh | |
| Uniform | 56 | Perlengkapan / Seragam | |
| Tableware & Kitchenware | 53 | Perlengkapan / Peralatan Makan & Dapur | A4 |
| FIN | 52 | Persediaan / Barang Jadi | A2 |
| Sparepart Machine | 52 | Perlengkapan / Suku Cadang | A4 |
| Packaging | 47 | Persediaan / Kemasan | A3 |
| Local - Frozen | 44 | Persediaan / Bahan Baku / Lokal - Frozen | |
| Marketing | 39 | Perlengkapan / Pemasaran | A7 |
| Local - Chill | 22 | Persediaan / Bahan Baku / Lokal - Chill | |
| Supplies - Cleaning | 19 | Perlengkapan / Kebersihan & Keselamatan | |
| Supplies - Production | 16 | Perlengkapan / Produksi | |
| FA Building & Improvements | 12 | Aset Tetap / Bangunan & Renovasi | |
| FA Machinery | 12 | Aset Tetap / Mesin | |
| Supplies - Selling | 12 | Perlengkapan / Operasional Toko | |
| Expense - Equipment Maintenance | 6 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Supplies - Packaging | 6 | Perlengkapan / Kemasan Habis Pakai | A3 |
| Freight - Import | 5 | Beban / Jasa / Biaya Impor | A8 |
| FA Office & Outlet Equipment - Metal | 5 | Aset Tetap / Peralatan Kantor & Outlet | |
| Supplies - Safety | 4 | Perlengkapan / Kebersihan & Keselamatan | |
| Supplies - Marketing | 3 | Perlengkapan / Pemasaran | |
| Expense - Water & Gas | 3 | Beban / Jasa / Utilitas | A9 |
| Expense - Other | 3 | Beban / Jasa / Lain-lain | |
| Expense - Building Maintenance | 2 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Inventory - Merchandise | 2 | Persediaan / Merchandise | |
| Licensing / Sertification | 2 | Beban / Jasa / Perizinan & Sertifikasi | |
| Expense - Maintenance Fee | 2 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Expense - Stationaries | 2 | Perlengkapan / Kantor & ATK | |
| Expense - Office Supplies | 1 | Perlengkapan / Kantor & ATK | |
| EXP - DOMESTIC DISTRIBUTION | 1 | Beban / Jasa / Pengiriman | |
| Expense - Household | 1 | Perlengkapan / Operasional Toko | |
| Expense - Furniture Maintenance | 1 | Beban / Jasa / Pemeliharaan & Instalasi | |
| Expense - Computer supplies | 1 | Perlengkapan / Kantor & ATK | |
| Expense - Printing | 1 | Beban / Jasa / Pemasaran & Cetak | |
| FA - Software | 1 | Aset Tetap / Perangkat Lunak | A10 |
| ONGKOS KIRIM | 1 | Beban / Jasa / Pengiriman | |
| Expense - Reklame | 1 | Beban / Jasa / Pemasaran & Cetak | |
| Expense - Production Supplies | 1 | Perlengkapan / Produksi | |
| Sewa Peralatan | 1 | Beban / Jasa / Sewa | |
| Expense - Printing Marketing | 1 | Beban / Jasa / Pemasaran & Cetak | |
| JASA INSTALASI EQUIPMENT | 1 | Beban / Jasa / Pemeliharaan & Instalasi | A11 |

Ditambah 1.680 menu: `Menu / <kategori POS teratas>`. Menu tanpa kategori POS masuk `Menu / Lainnya`. Menu dengan lebih
dari satu kategori POS teratas memakai yang pertama dan ditandai `menu_multi_root` di laporan.

## Ambiguitas (default dipakai; jawaban Finance mengubah baris CSV)

| ID | Pertanyaan | Default |
|---|---|---|
| A1 | "Inventory - Food & Beverage": bahan baku atau barang dagang jadi? | bahan baku. Produk yang tampil di POS ditandai `A1_available_in_pos` di laporan |
| A2 | WIP/FIN: diproduksi CK atau dibeli setengah jadi? | bisa dibeli hanya bila ada pemasok (`product.supplierinfo`); FIN tidak pernah. "Inventory - WIP" dan "WIP" digabung |
| A3 | Packaging vs Packaging Material vs Supplies - Packaging | dua yang pertama = Persediaan / Kemasan (stok); Supplies - Packaging = habis pakai |
| A4 | Tableware, Sparepart, Supplies: stok atau beban? | beban saat tagihan (non-stok) |
| A5 | Indirect Material (gas, minyak goreng): stok? | stok, sebagai bahan penolong |
| A6 | LVA: dikapitalisasi atau dibebankan? | dibebankan (`63110120`) sampai ada kebijakan aset |
| A7 | Marketing: barang POSM atau jasa agensi? | barang (Perlengkapan) |
| A8 | Freight - Import: masuk nilai persediaan (landed cost)? | beban jasa. Landed cost butuh modul CE `stock_landed_costs` (belum diverifikasi) |
| A9 | Water & Gas: utilitas atau LPG dapur? | utilitas (jasa) |
| A10 | Software: akun aset tak berwujud? | belum ada akun. Skrip memberi peringatan dan Finance membuat akunnya |
| A11 | Jasa instalasi: beban atau dikapitalisasi? | beban |

## Cara kerja (teknis singkat)

1. **Export bundel ESB** (`efn_mig.mapping.build_categories`/`build_products`): kategori dikirim sebagai pohon
   (`Persediaan` → `Persediaan / Bahan Baku` → `… / Impor`) beserta flag produk per peran. Importer Odoo
   (`efn_legacy_history`) memiliki field `categ_id`, `type`, `is_storable`, `sale_ok`, `purchase_ok`, sehingga impor ulang
   menghasilkan struktur baru. Kategori yang sudah ada dengan nama + induk sama ditautkan, bukan digandakan. Sumber
   pemasok untuk aturan A2 belum diekspor (E11–E13), jadi WIP/FIN di bundel = tidak bisa dibeli.
2. **DB yang sudah diimpor (efn-prod)**: `scripts/migration/recategorize_pb.py` lewat `odoo shell`. Default DRY-RUN
   dengan laporan CSV per produk. Runbook: [migrasi-esb-odoo.md §4.5](../runbooks/migrasi-esb-odoo.md).
   - Skrip memindahkan produk dan menulis flag.
   - Skrip membuat kategori yang belum ada (akun placeholder) dan menautkan xmlid importer
     `efn_mig_esb.pb_pb_category_<path>`.
   - `standard_price` dijaga tetap.
   - Kategori lama `PB / …` yang kosong dihapus.
   - Skrip idempoten: jalan kedua tidak mengubah apa pun.
3. Urutan di efn-prod: backup → `-u efn_setup_pos` / `-i efn_setup_menu` → **G61 dry-run → review Finance → apply** →
   wizard POS G54.

## Belum dibangun / belum diverifikasi

- Skrip belum dijalankan di DB Odoo nyata. Pengujian otomatis baru mencakup pemetaan (pytest), belum TransactionCase.
- `_run_average_batch` tanpa pergerakan stok diasumsikan tidak mengubah `standard_price`. Skrip tetap memulihkan
  harga bila berubah.
- Produk menu baru dari impor berikutnya tetap tanpa kategori (bundel belum membawa kategori menu). Jalankan skrip
  lagi setelah impor; skrip idempoten.
- Akun final, valuasi perpetual, dan akun Perangkat Lunak menunggu Finance (Q-ESB16, A10).
