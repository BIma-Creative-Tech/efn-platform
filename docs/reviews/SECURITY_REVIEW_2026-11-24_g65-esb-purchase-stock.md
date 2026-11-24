# Security review G65 — histori pembelian + stok awal ESB (2026-11-24)

Reviewer: efn-security-reviewer (read-only). Branch `feat/esb-migration-purchase-stock` (worktree `efn-platform-esbmig`) vs
`origin/main`. Kontrak acuan: docs/04 §25, §27, §27.13; ADR-040; ADR-042 §0b; review G41/G43 (SEC-18x/SEC-19x/SEC-20x).

## Ringkasan eksekutif

- **0 Critical / 0 High / 0 Medium / 4 Low / 4 Info.** **Verdict: boleh merge.** Kontrol utama dari G41/G43 tetap
  berlaku untuk entitas baru: hanya GET, whitelist PDP sebelum data disimpan, grant ClickHouse per objek, bundel yang
  ditandatangani, importer hanya lewat `su` + konteks impor, dan record rule multi-company.
- Tiga temuan Low menyangkut **integritas data migrasi**, bukan kebocoran data: (01) tanggal PO tidak dicek terhadap
  hari slice, (02) slice stok awal yang kosong dianggap sukses, (03) laporan validator tidak mengikat `APP_ENV`, sehingga
  gerbang lokasi damage/expired untuk production bisa terlewati. 01 dan 02 sebaiknya ditutup sebelum gladi cutover PB
  (2026-12-01); 03 wajib sebelum cutover production.
- Satu Low kepatuhan: inventaris PDP belum mencatat data G65 (nama supplier perorangan di ClickHouse/Odoo).

## Tabel temuan

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-G65-01 | Low (PLAUSIBLE) | `services/esb-extract/efn_esb/transform_g65.py:131-153`, `extract_g65.py:51-66` | `purchaseDate` baris recap/header tidak dicek = hari slice | ADR-042 (integritas), R-S10 |
| SEC-G65-02 | Low (PLAUSIBLE) | `services/esb-extract/efn_esb/extract_g65.py:75-97`, `transform_g65.py:185-190` | Slice `stock_opening` dengan 0 baris tetap `done` + checkpoint | ADR-042 §0b (integritas stok) |
| SEC-G65-03 | Low | `scripts/migration/efn_mig/validate.py:445-448`, `efn_mig/bundle.py:409`, `esb_import/importer.py:262-280` | Gerbang `LOCATION_SUSPECT` production hanya di validator; laporan bertanda tangan tidak mengikat environment | R-S10, Q-ESB25 |
| SEC-G65-04 | Low (kepatuhan) | `docs/compliance/PDP_DATA_INVENTORY.md` §2.8.14; `models/efn_legacy_purchase_line.py:10-11` | Inventaris PDP belum mencakup data G65 | R-L01 |
| SEC-G65-05 | Info | `extract_g65.py:63-66`, `config.py:189-190` | `company_for` memakai PT default brand untuk `branchID` header yang tidak ada di peta | ADR-001 |
| SEC-G65-06 | Info | `__main__.py:201-233` | `snapshot` tidak dibatasi `ESB_BACKFILL_WINDOW` (sesuai desain G43) | ADR-040 (beban API) |
| SEC-G65-07 | Info | `esb_import/importer.py:1569-1592` | `zero_missing` menolkan semua quant internal PT yang tidak ada di snapshot | — |
| SEC-G65-08 | Info | `normalize.py:133-138` | `documentCode`/`referenceNumber` masuk whitelist stock-movement, tetapi baris yang disimpan hanya `Beginning` | R-L01 |

## Detail

### SEC-G65-01 — Low (PLAUSIBLE): tanggal PO tidak diverifikasi terhadap hari slice
**Lokasi.** `transform_purchase_lines` (`transform_g65.py:131-180`) mengisi `purchase_date = day` (hari slice) untuk setiap
baris recap tanpa membandingkan `row["purchaseDate"]`. Di sisi lain, `check_branch` untuk stok (`transform_g65.py:185`)
sudah fail closed.
**Skenario.** Endpoint recap yang sama terbukti mengabaikan filter `branchID[]` (probe 24 Nov). Kalau `dateFrom/dateTo`
juga diabaikan atau ditafsirkan berbeda (inklusif/zona waktu), bisa terjadi dua hal:
- Halaman berisi PO dari hari lain. Setiap slice harian lalu menyalin baris yang sama dengan `purchase_date` berbeda.
  Karena `purchase_date` bagian dari ORDER BY RMT, duplikat ini tidak terdedup.
- `fetch_all` menarik sampai `max_pages` (500 x 1000 baris) per slice, sehingga beban ke ESB membengkak.

Akibatnya `supplierinfo` (harga dan `date_start` PO terakhir) dan `efn.legacy.purchase.line` menjadi salah. Rekonsiliasi 5e
tidak akan mendeteksinya karena membandingkan bundel dengan fakta yang sama-sama salah. Statusnya PLAUSIBLE: probe
mencatat ±80–100 baris per hari, jadi filter tanggal kemungkinan berfungsi.
**Rekomendasi.** Di `extract_purchase_line`, setelah whitelist: `parse_day(row.get("purchaseDate")) != task.day` →
`ValueError` (slice `error`, pola `check_branch`). Header di luar hari slice diabaikan. Tambahkan test dengan satu baris
recap hari lain.

### SEC-G65-02 — Low (PLAUSIBLE): slice stok awal kosong dianggap sukses
**Lokasi.** `extract_stock_opening` (`extract_g65.py:75-97`). Pada `rows == []`, `check_branch` lolos begitu saja dan
slice ditulis bersama checkpoint. Ringkasan `rows_total=0` hanya tersimpan di `raw_doc`.
**Skenario.** `branchCode` dari `/branch/list/user` tidak sama dengan kode yang diterima `/report/stock-movement`
(ditandai [B] di §27.13.1). Pada pola endpoint ini, server menjawab 0 baris, bukan error (bandingkan `branchID=` → 0
baris). Rantai akibatnya:
1. Checkpoint cabang terbentuk.
2. Gerbang export "setiap cabang non-skip ber-checkpoint" lolos.
3. `quant.jsonl` tidak memuat cabang itu.
4. Rekonsiliasi butir 2 (Σ fakta vs Σ quant) tetap 0 = 0.

Hasilnya, stok awal satu outlet hilang tanpa peringatan. Di non-production dengan `--zero-missing`, quant Odoo cabang itu
malah ikut dinolkan.
**Rekomendasi.** Untuk cabang aktif, `rows_total == 0` sebaiknya menjadi slice `error` (atau status khusus tanpa
checkpoint). Alternatifnya, ekspor `rows_total` per cabang ke manifest (`stock_opening_meta`) dan buat validator
memunculkan error `STOCK_BRANCH_EMPTY` untuk cabang non-`skip`.

### SEC-G65-03 — Low: gerbang production `LOCATION_SUSPECT` bisa dilewati lewat laporan validator non-production
**Lokasi.** `validate.py:445-448`: error hanya bila `production=True` (dari `APP_ENV` mesin validator). `bundle.py:409`
`VALIDATION_BINDING` tidak memuat environment. Importer (`importer.py:262-280`) hanya memeriksa tanda tangan,
`manifest_sha256`, `bundle_sha256`, dan `errors == 0`, dan tidak memeriksa ulang pola lokasi.
**Skenario.** Operator yang memegang `migration_bundle_key` menjalankan `validate_import.py` di container dev/stg
(`APP_ENV=dev`). Lokasi "Damage/Expired" tanpa `location_map.csv` hanya menjadi peringatan, sehingga `errors=0`. Laporan
lalu dipakai untuk `run-import.sh --apply --confirm-brand pb` di production. Stok rusak/kedaluwarsa ikut masuk
`lot_stock_id` gudang utama dan persediaan pembukaan menjadi lebih besar dari seharusnya (Q-ESB25). Ini bisa terjadi
karena kelalaian maupun insider (R-S10).
**Rekomendasi.** Pilih salah satu:
- Tambahkan `production: bool` ke laporan validator, masukkan ke `VALIDATION_BINDING`, dan biarkan importer production
  menolak laporan dengan `production=false`.
- Atau importer production memeriksa ulang `data.esb_locations` di `prepass_stock` dengan regex yang sama, lalu
  `GateError`.

### SEC-G65-04 — Low (kepatuhan UU PDP): inventaris data belum diperbarui
**Lokasi.** Docstring model (`efn_legacy_purchase_line.py:10-11`) merujuk "inventaris PDP §2.8.14", tetapi §2.8.14 (v18)
hanya mencantumkan flow/sales.
**Data G65 yang belum tercatat:**
- `fact_esb_purchase_line.supplier_name`
- `raw_doc` `report/purchase-recapitulation` / `purchase/purchase-order` (TTL 36 bulan)
- `efn.legacy.purchase.line.supplier_name` (dibaca `purchase.group_purchase_user`, tidak terenkripsi)
- `product.supplierinfo` hasil migrasi

Supplier perorangan termasuk data pribadi.
**Rekomendasi.** Tambahkan entri v19 di `PDP_DATA_INVENTORY.md` (sumber, dasar pemrosesan, akses, retensi 24 bulan
histori + TTL raw) dan pertanyaan DPO (lanjutan Q-P32). Produksi tetap menunggu DPO, sama seperti status G43.

### SEC-G65-05 — Info: fallback PT default untuk `branchID` header yang tidak dikenal
`company_for` (`config.py:189-190`) mengembalikan `company_code` brand bila `branch_id` tidak ada di `branch_company`.
Untuk PB (satu PT) tidak ada dampak. Untuk Bacha (BCF/BCR), PO cabang BCR yang petanya belum lengkap akan tercatat di BCF.
Polanya sama dengan G41 (SEC-IR-02 diperbaiki fail closed hanya di view movement). Saran sebelum `purchase_line` diaktifkan
untuk brand multi-PT: anggap `branch_id` yang tidak ada di `dim_esb_branch` sebagai `map_source='none'`, ditambah cek
kualitas.

### SEC-G65-06 — Info: `snapshot` di luar jendela backfill
`snapshot --entity stock_opening` (±477 GET untuk PB, budget `ESB_BACKFILL_MAX_CALLS`, token bucket 2 req/dtk) tidak
dicek terhadap `ESB_BACKFILL_WINDOW`. Ini konsisten dengan G43 dan memang dijalankan setelah freeze ESB. Cukup dicatat di
runbook: jalankan di luar jam operasional.

### SEC-G65-07 — Info: cakupan `zero_missing`
`_missing_quants` menolkan semua quant internal milik PT target yang tidak ada di snapshot, termasuk lokasi non-ESB
(mis. DC/gudang demo dalam PT yang sama). Gerbang production sudah fail closed (`importer.py:1891-1892`, `ConfigError`;
`APP_ENV` kosong = production). Untuk gladi di efn-prod yang berisi data demo, operator perlu memahami efeknya. Saran
tambahkan peringatan di runbook.

### SEC-G65-08 — Info: whitelist stock-movement
`STOCK_MOVEMENT_FIELDS` memuat `documentCode`/`referenceNumber` (ID dokumen, bukan PII). `raw_doc` hanya menyimpan baris
`Beginning` qty ≠ 0 beserta ringkasan. Tidak perlu tindakan.

## Kontrol yang sudah benar (diverifikasi di kode)

- **GET-only:** `http.py` hanya mengizinkan POST ke `LOGIN_PATH`. Tiga endpoint baru lewat `fetch_all` (GET).
  `max_pages` (default 500, maks 5000) memicu `PagingError` (fail closed). `limit` stock-movement 100 sesuai probe.
- **PDP L-110:** `whitelist()` diterapkan sebelum transform dan `raw_doc` (`extract_g65.py:54-60, 80-83`). Nilai bersarang
  dibuang. `createdBy/editedBy/emailSupplier/printedBy/notes/additionalInfo/costCenterName/projectName` tidak ada di daftar.
  `redact()` tetap menjadi lapis kedua.
- **Log:** runner hanya mencatat brand/entitas/scope/hari/jumlah dan `type(exc).__name__`. Pesan error bulk tanpa nilai
  baris (`bulk.py:135-137`). Tidak ada username ESB (SEC-180). Tidak ada data klien di fixture/test (nama supplier
  sintetis, mis. "PT Pemasok Satu").
- **ClickHouse least privilege:** `esb_writer` mendapat `SELECT, INSERT` per tabel G65. `mig_rw` hanya `SELECT` (+ view
  `v_esb_g65_quality`). Tidak ada grant `efn_dwh.*`. `metabase_reader`/Insight/forecast/text2sql tidak mendapat tabel G65.
  View kualitas G65 sengaja dipisah dari `v_esb_data_quality` (INVOKER). Semua query `store.py`/`source.py` memakai
  parameter terikat.
- **Odoo multi-company:** `efn.legacy.purchase.line` punya `company_id` required, `_check_company_auto`, rule global
  `company_ids` (noupdate), ACL baca saja untuk `purchase.group_purchase_user`, menu di bawah grup yang sama.
  `create/write/unlink/copy` → `check_import_allowed` (su + konteks). `_efn_bulk_replace_slice` privat (tidak bisa RPC).
  Test membuktikan PT lain tidak terlihat dan tulis ditolak.
- **Bulk tanpa gudang:** `warehouse_id` wajib `None` untuk model ini (SEC-197 tidak melebar). DELETE dibatasi
  `company_id` + rentang. Referensi gudang/supplier/produk dicek milik PT baris. Tanggal < cutover (atau `max_day` saat
  shift non-production).
- **Integritas bundel:** berkas baru (`supplierinfo.jsonl`, `quant.jsonl`, `legacy_purchase_line.jsonl.gz`,
  `maps/location_map.csv`) ikut sha256 + HMAC manifest secara generik (`_verify_files`). Gzip dibatasi rasio/ukuran.
  Laporan validator terikat `run_id` + `bundle_sha256` + `manifest_sha256`. Validator memakai whitelist kunci yang ketat
  (`HISTORY_KEYS`/`DATA_KEYS`, PII fail closed).
- **Lintas PT di importer:** `check_master_row` menghitung ulang xmlid `supplierinfo` dan prefix brand×company untuk
  `partner_key`/`product_key`/`location_key`. `prepass_stock` mewajibkan lokasi internal milik PT baris. Histori pembelian
  menolak `branch_key` gudang PT lain.
- **Gerbang production (fail closed):** `APP_ENV` kosong/asing = production (`importer.py:134-135`, `settings.py:31-33`).
  `zero_missing` ditolak di production. Geser cutover maju di production → `GateError`, geser mundur → `GateError`.
  Langkah `stock` mensyaratkan `cost_as_of` = cutover, tidak ada `stock.move` nyata ≥ cutover, tidak ada sesi POS
  terbuka, dan `opening_date` = cutover.
- **`run-import.sh`:** argumen divalidasi regex, flag boolean dicek tipe di `odoo_import_esb.py:176-178`, tidak ada
  secret di argumen.

## Belum terverifikasi

- Test (pytest esb-extract/migration, Odoo `TransactionCase`) tidak dijalankan reviewer.
- `git diff` tidak dijalankan. File dibaca dari tree worktree.
- Perilaku server ESB untuk `dateFrom/dateTo` header PO dan kecocokan `branchCode` (asal SEC-G65-01/02) belum dibuktikan.
- `clickhouse-init.sh` (pengikatan role/urutan file 23) tidak dibaca ulang.

---

## Addendum G65b (2026-11-24) — delta sejak 4d14116

Cakupan: 5ac0ddc (`--allow-cutover-shift`), 4d27700 (kode produk opsional di arsip pembelian), 81aa4dc
(`--stock-empty-branch`), f5bce79 (`unitToShow=Base Unit`, satuan dari snapshot, lokasi karantina dibuat importer),
d4131d6 + 8ed8426 (`--allow-zero-cost`, `--skip-unconvertible-stock`), 81bca63 (importer melewati `uom_incompatible`).
Review read-only atas tree worktree (git diff tidak dijalankan; reviewer tidak punya shell).

### Ringkasan

- **0 Critical / 0 High / 0 Medium / 1 Low / 3 Info.** **Verdict: boleh merge.** Kelima fokus terpenuhi. Override
  cutover cocok persis dan fail closed. Opsi pelonggar hanya berlaku dari manifest yang terikat laporan validator
  bertanda tangan. Lokasi karantina terkunci ke PT dan cabang baris. Tidak ada PII di test/docs. Beban API ESB tidak
  berubah.
- Satu Low: deklarasi `--stock-empty-branch` tidak diverifikasi terhadap data ESB, dan banner laporan validator tetap
  menulis "ketat". Sebaiknya ditutup sebelum cutover production. Untuk gladi efn-prod cukup ditangani lewat berita acara.

| ID | Severity | Lokasi | Ringkas | Risiko |
|---|---|---|---|---|
| SEC-G65b-01 | Low (PLAUSIBLE) | `scripts/migration/efn_mig/export.py:462-466`, `validate.py:48-53, 448-450`, `esb_to_odoo.py:95-101` | `--stock-empty-branch` = deklarasi operator tanpa bukti; tidak masuk `export_options`/banner pengecualian | ADR-042 §0b, R-S10 (bersama SEC-G65-02) |
| SEC-G65b-02 | Info | `scripts/migration/efn_mig/validate.py:431-434` | `zero_ok` menerima `standard_price < 0` untuk produk terdaftar; importer `_do_cost` tidak cek ulang | integritas HPP |
| SEC-G65b-03 | Info | `esb_import/importer.py:1406-1421` | Lokasi karantina yang sudah ada (xmlid terikat) tidak dicek "bukan child_of `lot_stock_id`"; cek hanya saat create | Q-ESB25 |
| SEC-G65b-04 | Info | `docs/runbooks/migrasi-esb-odoo.md:339-345`, `docs/agent-reports/efn-lead.md:94-95` | Repo publik memuat fakta operasional PB (branch_id 39/40, kota, jumlah baris gladi) | R-L01 (bukan PII) |

### SEC-G65b-01 — Low (PLAUSIBLE): `--stock-empty-branch` tanpa verifikasi, tidak tampil di banner pengecualian
**Lokasi.** `export.py:463-466` mengeluarkan cabang tanpa checkpoint `stock_opening` dari `missing` hanya karena ada
argumen `--stock-empty-branch`. Validator (`validate.py:448-450`) cukup memberi peringatan `STOCK_EMPTY_DECLARED`.
`ExportOptions.to_manifest()` (`mapping.py:454-462`) tidak memuat daftar ini, sehingga `Report.exceptions`/`to_markdown`
(`validate.py:50-53, 88-92`) tetap menulis "export_options: ketat (tanpa pengecualian)".
**Skenario.** Snapshot cabang aktif gagal atau di-skip (mis. `branchCode` tidak cocok, timeout). Operator menambahkan
`--stock-empty-branch <id>` agar export lolos. Bundel ditandatangani, `errors=0`, dan importer memuatnya. Stok awal
outlet itu = 0 di Odoo, dan rekonsiliasi tidak menangkapnya karena Σ fakta dan Σ quant sama-sama 0. Pembaca laporan
validator yang hanya melihat banner mengira export ketat. Ini kesalahan operasional atau insider (R-S10), bukan bypass
kriptografis: manifest tetap terikat HMAC.
**Rekomendasi.**
- Masukkan `stock_empty_branches` ke `export_options` (atau ke `Report.exceptions`) agar banner menampilkan
  "DRY-RUN DENGAN PENGECUALIAN".
- Export menolak deklarasi untuk cabang yang punya baris `fact_esb_stock_opening`/stock-movement di versi mana pun
  (bukti "tanpa kartu stok sama sekali").
- `import_report` ikut menyalin `export_options` + `stock_empty_branches_declared` sebagai lampiran berita acara.

### SEC-G65b-02 — Info: harga negatif lolos jalur `--allow-zero-cost`
Kondisi `(price is None or price <= 0) and not zero_ok` membuat produk di `zero_cost_products` juga lolos dengan harga
negatif. Export hanya menghasilkan 0, dan manifest terikat HMAC, jadi tidak bisa dieksploitasi tanpa kunci bundel.
Saran: `zero_ok` hanya berlaku bila `price == 0`. Sebagai lapis kedua, `_do_cost` menolak `value < 0`.

### SEC-G65b-03 — Info: invariant lokasi karantina yang sudah ada
Saat dibuat (`_ensure_quarantine`, `importer.py:1543-1547`), lokasi ditempatkan di bawah `view_location_id` gudang
dengan `replenish_location=False`. Ini sudah benar. Untuk lokasi yang sudah ada, `prepass_stock` hanya memeriksa
`warehouse_id == quarantine_of`, `company_id`, `usage=internal`, dan `replenish_location`. Lokasi itu tidak diperiksa
terhadap `child_of lot_stock_id`. Xmlid `branch_quarantine` hanya bisa dibuat importer (`_bind`), jadi risikonya kecil.
Saran: tambahkan cek `location.parent_path` tidak berawalan `wh.lot_stock_id.parent_path` agar stok karantina tidak
pernah bisa di-reserve.

### SEC-G65b-04 — Info: fakta operasional di repo publik
Runbook dan laporan lead menyebut branch_id PB 39/40, kotanya, serta jumlah temuan gladi ("17 QUANT_NO_COST", "35 baris
WIP"). Tidak ada nama orang, nilai rupiah, atau data supplier. Ini dapat diterima, tetapi sebaiknya angka hasil gladi
disimpan di catatan gladi privat, bukan di repo.

### Kontrol G65b yang sudah benar (diverifikasi di kode)

- **Override cutover (fokus 1):** `parse_cutover_shift` memakai `fullmatch`, tanggal valid, dan wajib maju; selain itu
  `None` (`mapping.py:72-82`). `check_cutover_shift` di production menuntut tuple **persis** `(CODE, lama di DB, baru di
  manifest)` per company (`importer.py:422-427`). Geser mundur tetap `GateError` dengan izin apa pun. `APP_ENV` kosong
  dianggap production. Nilai ditolak lebih awal di `odoo_import_esb.py:182-184`, dan regex `run-import.sh:51` mencegah
  injeksi JSON ke argv. Override tercatat di `import_report.cutover_shift_override`. Izin yang cocok tetap ditolak bila
  irisan histori [lama, baru) belum dimuat. Test mencakup nilai salah, PT lain, spasi di ujung, garbage, dan mundur
  (`test_esb_import_g65.py:494-520`).
- **Opsi pelonggar (fokus 2):** `allow_zero_cost` dan `skip_unconvertible_stock` hanya ada di argumen **export**
  (`esb_to_odoo.py:67-76`) lalu masuk `manifest.export_options`. Validator membaca opsi dari manifest dengan kunci dan
  tipe whitelist (`validate.py:209-225`), bukan dari flag sendiri. Importer tidak punya argumen untuk opsi ini:
  `_skip_unconvertible_stock` membaca `self.man.export_options` (`importer.py:1517-1519`), dan manifest itu terikat
  `manifest_sha256` di laporan validator bertanda tangan (`importer.py:273-285`). Laporan validator menampilkan
  pengecualian (banner + `export_options` di JSON). Import report mencatat `stock_skipped.uom_incompatible(_keys)`.
  Cost 0 hanya untuk produk di `zero_cost_products`.
- **Lokasi karantina (fokus 3):** `check_quant_data` mewajibkan `location_key` = xmlid `branch_quarantine` hasil hitung
  ulang dari brand×company×`esb_branch_id`, dan `quarantine_of` = xmlid `branch_wh` milik brand×company yang sama
  (`mapping.py:253-267`). Xmlid lain tidak bisa disisipkan. `prepass_stock` menolak gudang induk dari PT lain dan lokasi
  yang ada di bawah gudang lain (`importer.py:1406-1421`). Gudang induk yang tidak ada membuat baris gagal tanpa jatuh ke
  Stock. Create dijalankan per company (`_cenv`) di dalam savepoint, dan dry-run ikut di-rollback. Tidak ada `sudo()`
  baru di luar env `su` importer yang sudah ada. Validator memeriksa invariant yang sama (`validate.py:531-541`).
- **Arsip pembelian tanpa kode produk:** `esb_product_code` menjadi opsional di `efn.legacy.purchase.line` (tanpa kunci
  unik, hanya `bulk_replace_slice` per company×bulan). Tidak ada dampak ke idempotensi maupun multi-company.
- **Data klien di test (fokus 4):** fixture tetap sintetis (`ZFIN`, `ZRM`, `RM001`, `SUP1`, lokasi "Damage/Rusak").
- **Beban API ESB (fokus 5):** `extract_stock_opening` hanya menambah parameter `unitToShow=Base Unit`
  (`extract_g65.py:95`). Endpoint, `limit` 100, `max_pages`, dan jumlah panggilan per cabang tidak berubah. Tetap
  GET-only.

### Belum terverifikasi (addendum)

- Test pytest/Odoo tidak dijalankan reviewer. Isi diff per commit tidak dibandingkan. File dibaca dari tree worktree.
- Perilaku ESB `unitToShow=Base Unit` (satuan benar-benar seragam per produk) belum dibuktikan oleh reviewer.
