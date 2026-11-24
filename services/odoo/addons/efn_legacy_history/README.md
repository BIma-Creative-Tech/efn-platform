# efn_legacy_history — Arsip histori ESB Core (read-only)

**Gelombang 43 fase 2 (W2) + G65 (W-C).** ADR-042 (Accepted dengan amendemen, 18 Nov 2026; amandemen §0b G65),
kontrak `docs/04` §27.8, §27.8a, §27.9 (v19.1), §27.13.6–§27.13.8 (G65), fit-gap `docs/fit-gap/G43_MIGRASI_ESB_ODOO.md`
§7 dan `docs/fit-gap/G65_MIGRASI_PEMBELIAN_STOK.md`. Versi `19.0.1.1.0`, LGPL-3, depends `efn_core`, `stock`,
`point_of_sale`, `purchase` (G65), `efn_demand_forecast` (semua CE/efn; tanpa modul pihak ketiga, ADR-031). Upgrade
19.0.1.0.0 → 19.0.1.1.0 tanpa skrip migrasi (hanya tabel/view/menu/ACL/rule baru; `purchase` sudah terpasang di efn-prod).

## Tujuan
Menyimpan **arsip sistem lama ESB** di Odoo **tanpa** membuat transaksi Odoo (tidak ada `pos.order`, `account.move`,
`stock.move`, jurnal, pajak, atau nomor dokumen). Arsip ini dipakai untuk dua hal:

1. **Forecast min/max sejak hari pertama cutover**: `efn.legacy.flow.daily` (aliran produk harian) menjadi input
   `efn_demand_forecast` untuk hari **sebelum** `efn_mig_esb.cutover_date.<KODE PT>` (tanpa cold start 8 minggu).
2. **Histori penjualan di dalam Odoo** (keputusan user Q-ESB20): `efn.legacy.sales.daily` (harian per menu) dan
   `efn.legacy.sales.line` (baris transaksi), dilihat lewat pivot/graph/list.

Angka resmi histori tetap gudang data ClickHouse (Dashboard Insight, retensi 10 tahun, L-109).

## Model
| Model | Grain | Kunci idempoten | `_log_access` | Grup baca |
|---|---|---|---|---|
| `efn.legacy.flow.daily` | hari × company × gudang × produk storable | UNIQUE `(company_id, warehouse_id, product_id, day)` (upsert) | ya | `stock.group_stock_user` (planner = Inventory Admin, implied) |
| `efn.legacy.sales.daily` | hari × company × outlet × kode menu ESB | UNIQUE `(company_id, warehouse_id, day, esb_menu_code)` (upsert) | ya | `group_legacy_sales_reader` |
| `efn.legacy.sales.line` | baris transaksi ESB (`sales_num`, `line_no`) | **tanpa** UNIQUE — ganti irisan company × gudang × bulan | **tidak** | `group_legacy_sales_reader` |
| `efn.legacy.purchase.line` (G65) | baris rekap pembelian ESB (`purchase_num`, `line_no`) | **tanpa** UNIQUE — ganti irisan company × bulan (**tanpa gudang**) | **tidak** | `purchase.group_purchase_user` |

Field per model:
- **flow.daily**: `day`, `company_id`, `warehouse_id`, `product_id` (wajib), `uom_id` (related), `sold`, `ck_prod`,
  `store_prod`, `ck_out`, `in_ck`, `in_other`, `st_in`, `st_out` (Float 16,3), `outflow` (= `sold + st_out + ck_out`,
  **disimpan**, bukan compute), `esb_brand`, `esb_branch_id`, `esb_product_code` (wajib), `esb_product_name`, `run_id`.
- **sales.daily**: `day`, `company_id`, `currency_id` (related), `warehouse_id`, `pos_config_id` (opsional),
  `product_id` (**opsional** — menu tak terpetakan tetap tersimpan), `esb_brand`, `esb_branch_id`, `esb_menu_code`
  (wajib), `menu_name`, `menu_category`, `is_takeaway`, `qty`, `gross`, `discount`, `net`, `cogs` (Monetary),
  `trx_count`, `run_id`. `net` = nilai export; bila kosong diisi `gross − discount`.
- **sales.line**: `date`, `company_id`, `warehouse_id`, `product_id` (opsional), `esb_menu_code` (wajib, Char 32),
  `sales_num` (wajib, Char 32), `line_no`, `qty` (Float 16,3), `price`, `total`, `discount`, `cogs` (Float 16,2 —
  Rupiah tanpa kolom mata uang per baris), `row_type` (`ALACARTE`/`PACKAGE_HEADER`/`PACKAGE_COMPONENT`), `is_takeaway`.
  Tanpa `run_id` per baris (jejak run = `import_report.json` importer).
- **purchase.line** (G65, §27.13.6): `company_id`, `date` (tanggal PO), `date_required`, `purchase_num` (Char 64, indeks; `esb_product_code` juga 64, selaras extractor),
  `line_no`, `warehouse_id` (**opsional**: HO / cabang `skip` / tak terpetakan), `branch_name` (teks ESB, selalu),
  `supplier_id` (opsional, `ondelete set null`), `supplier_name`, `product_id` (opsional), `esb_product_code` (wajib),
  `product_name`, `category_name`, `sub_category_name`, `uom_name` (teks ESB, tidak dikonversi), `qty`, `receipt_qty`
  (Float 16,3), `pricelist_price`, `price_unit` (`aggregator=avg`), `discount` (nominal/%: [B] Q-G65-2), `vat_value`,
  `total` (Float 16,2), `status` (Char, bukan Selection), `map_source` (`header|name|none`), `cost_center` (disediakan,
  **tidak diisi** G65). Indeks `(company_id, date)`, `(company_id, product_id, date)`, `(company_id, supplier_id, date)`.
  BUKAN `purchase.order`: tanpa relasi ke PO/stock.move/account.move; tidak dibaca `purchase.report`/ETL
  `fact_dc_purchase_line`. `supplier_name` supplier perorangan = data pribadi tak terenkripsi → inventaris PDP §2.8.14 (DPO).

Aturan bersama: `company_id` wajib, `_check_company_auto`, record rule **global** `[('company_id', 'in',
company_ids)]`, ACL hanya `perm_read`, tanpa `mail.thread`. `create`/`write`/`unlink` (juga `copy`) di-override →
`AccessError` kecuali `env.su` **dan** konteks `efn_legacy_import=True`. Indeks: `(company_id, day|date)`,
`(company_id, warehouse_id, day|date)`; flow juga `(warehouse_id, product_id, day)` (query forecast).
`group_legacy_sales_reader` ("Histori ESB — baca penjualan") di-imply `point_of_sale.group_pos_manager`; analis PT
tanpa hak POS manager ditambahkan manual.

### Keputusan `_log_access`
- `sales.line` **False**: ±8 juta baris PB; kolom `create/write_uid/date` ±24 byte/baris (≈ 0,2 GB PB) tanpa nilai
  audit (penulis satu-satunya = skrip migrasi; jejak = laporan impor per irisan + log skrip).
- `flow.daily` & `sales.daily` **dipertahankan** (kontrak §27.8; volume kecil/sedang): diisi SQL bulk dengan `env.uid`
  (user teknis `efn_migration` bila importer memakai `env(user=efn_migration, su=True)`) dan `now()`.

## Menu
- Inventaris › Laporan › **Histori ESB (arsip)**: Aliran produk harian; Penjualan harian per menu; Baris penjualan.
- Point of Sale › Laporan › **Histori ESB (arsip)**: Penjualan harian per menu; Baris penjualan.
- Purchase › **Histori ESB (arsip)** (G65, sequence 98, `purchase.group_purchase_user`; `purchase.purchase_report_main`
  hanya untuk manager): list/pivot/graph/form, filter default 3 bulan terakhir, group by supplier/produk/cabang/bulan.
  Entri Inventaris hanya bila Q-G65-4 meminta.
Semua view tanpa tombol buat/ubah/hapus; judul view dan banner form: "Arsip sistem lama ESB — bukan transaksi Odoo".
Filter default: penjualan harian 3 bulan terakhir; baris penjualan bulan terakhir (`limit` 80, tanpa graph default).

## API impor (untuk `scripts/migration`, BUKAN RPC)
Fungsi Python di `efn_legacy_history/bulk.py` (dibungkus metode privat `_efn_*` di model; tidak bisa dipanggil lewat
`call_kw`/`json2`, SEC-58). Wajib `env.su` + konteks `efn_legacy_import=True`:

```python
Flow = env["efn.legacy.flow.daily"].sudo().with_context(efn_legacy_import=True)
Flow._efn_bulk_upsert(rows, run_id, company_ids, batch_size=5000, max_day=None)
# -> {"rows", "inserted", "updated", "unchanged"}
Sales = env["efn.legacy.sales.daily"].sudo().with_context(efn_legacy_import=True)
Sales._efn_bulk_upsert(rows, run_id, company_ids, batch_size=5000, max_day=None)
Line = env["efn.legacy.sales.line"].sudo().with_context(efn_legacy_import=True)
Line._efn_bulk_replace_slice(
    company_id,
    warehouse_id,
    date_from,
    date_to,
    rows,
    run_id=None,
    batch_size=5000,
    max_day=None,
    sales_line_months=None,
)
# -> {"rows_deleted", "rows_inserted"}; date_to EKSKLUSIF; juga tersedia di flow/sales.daily (run_id wajib)
```
- `rows`: iterable `dict` berkunci nama field di atas (Many2one = id int hasil resolve xmlid; tanggal `date` atau
  `'YYYY-MM-DD'`; angka int/str/Decimal). Kolom tak dikenal (termasuk `outflow`) → error (fail closed).
- Validasi sebelum menulis (pengganti `check_company` yang dilewati SQL): company ∈ `company_ids` target, gudang &
  kasir milik company baris, produk milik company itu atau tanpa company, tanggal `<` cutover company (parameter) dan
  `<` `max_day`; untuk `sales.line` irisan wajib di dalam `[awal bulan(cutover) − N bulan, cutover)`,
  `N = min(efn_mig_esb.sales_line_months, sales_line_months manifest)`; `N = 0` → `UserError`.
- SQL: `INSERT … ON CONFLICT … DO UPDATE … WHERE … IS DISTINCT FROM` (upsert; baris sama persis tidak ditulis ulang)
  atau `DELETE` irisan + `INSERT` multi-baris; disusun dengan `odoo.tools.SQL` (identifier ter-quote, nilai = parameter
  terikat). `COPY` tidak dipakai (akses cursor mentah Odoo 19 belum diverifikasi, §27.8a [B]).
- Transaksi milik pemanggil: commit per irisan (run nyata) / rollback (dry-run) = tugas importer.
- Helper parameter: `get_cutover_date(env, company)`, `get_sales_line_months(env)`, `sales_line_window(env, company,
  anchor=None, months=None)`.

## Importer bundel ESB (G43 W2b) — `esb_import/`
Paket Python server-side (bukan model, tidak diimpor `__init__` modul → tanpa RPC, tanpa `cryptography` saat modul
dimuat). Pemanggil: `scripts/migration/odoo_import_esb.py` di `odoo shell` (pembungkus host
`scripts/migration/run-import.sh`, overlay `infra/compose/docker-compose.migration.yml`) dan test modul ini.

| Berkas | Isi |
|---|---|
| `esb_import/mapping.py` | MURNI (tanpa `odoo`): validasi baris (amplop, xmlid dihitung ulang, referensi satu brand × PT), baris → nilai Odoo (`Plan`: field milik migrasi + hanya-saat-create), konversi qty/HPP via `ea_factor`, BoM normal/phantom, nilai bulk histori. Diuji pytest `scripts/migration/tests/test_odoo_import_mapping.py`. |
| `esb_import/importer.py` | `run_import(env, bundle, Options, commit)` + `build_result(...)`: gerbang §27.5, urutan §27.2, upsert xmlid §27.4, histori bulk, `odoo_totals` dari tabel, `table_bytes`. |
| `esb_import/efn_mig/{bundle,xmlid}.py` | **Salinan terkendali** `scripts/migration/efn_mig` (lihat di bawah). |

Keputusan:
- **Salinan `efn_mig`, bukan import dari path repo.** Image Odoo (`services/odoo/Dockerfile`) hanya memuat
  `services/odoo/addons`; test CI Odoo tidak melihat `scripts/`. Dua berkas disalin persis dan dijaga
  `test_vendored_efn_mig_identical` (pytest, abaikan CRLF) + test bahwa hanya `AESGCM`/`HKDF`/`hashes`/`InvalidTag`
  dari `cryptography` yang dipakai (cryptography = bawaan image Odoo, bukan pin 50.0.2 milik `esb-migrate`). Di
  `odoo shell`, `odoo_import_esb.py` memakai `efn_mig` resmi dari `EFN_MIG_CODE_DIR` bila ada (overlay), fallback salinan.
- Upsert berjalan sebagai `EFN_MIG_USER_LOGIN` (`env(user=…, su=True)`): xmlid baru → create + xmlid `noupdate`;
  xmlid ada & `write_uid` = user migrasi → tulis field yang berbeda; diubah pengguna lain + beda → `conflict` (tidak
  ditimpa); hilang di sumber → `missing_in_source`; xmlid ke record terhapus → `dangling_xmlid` (gagal).
- Cabang store → `stock.warehouse` + `pos.config` tanpa sesi & **tanpa metode bayar** (`payment_method_ids` kosong:
  milik admin/efn_setup_pos; juga mencegah default CE membuat jurnal); `location` → `stock.location` internal di bawah
  `lot_stock_id` gudang induk; `efn_is_central_kitchen` hanya bila `efn_ck_production` terpasang (dicatat).
- Kategori produk (G61, `docs/fit-gap/G61_KATEGORI_PRODUK_PB.md`): bundel membawa pohon per peran akuntansi
  (`Persediaan / Bahan Baku / Impor`, `Perlengkapan / …`, `Beban / Jasa / …`, `Aset Tetap / …`) dari
  `scripts/migration/efn_mig/category_roles.csv`. Tingkat atas **tanpa** induk brand. `product.category` tidak ber-company,
  jadi kategori yang sudah ada dengan nama + induk sama (mis. dibuat `scripts/migration/recategorize_pb.py`) ditautkan
  ke xmlid bundel, bukan digandakan. Importer memiliki `categ_id`, `type`, `is_storable`, `sale_ok`, `purchase_ok` produk
  (nilai dari tabel peran). Induk brand `efn_mig_esb.<brand>_category_root` hanya untuk bundel lama (produk tanpa
  `categ_key`).
- Produk & menu dibuat dengan `responsible_id` kosong; user `efn_migration` wajib anggota semua company target.
- Gerbang `--confirm-brand`: `APP_ENV` kosong/tidak dikenal = production (fail closed); non-production hanya
  `dev|development|local|test|ci|staging|stg`.
- Produk & menu: xmlid → `product.template`; histori memakai varian tunggal. Menu takeaway `alias_of` → xmlid kedua ke
  menu dasar (map dua baris, `odoo_id` sama); menu arsip → produk `active=False`; harga hanya bila ada di bundel.
- BoM: `normal` (produksi CK, ADR-018) `product_qty` = rasio BATCH (EA ÷ `ea_factor` produk); `phantom` = satu kit
  per menu (`menu_keys` > 1 → xmlid tambahan `<xmlid>_k<sha256(menu_key)[:8]>`, esb_key `<bom_id>|<menu_key>` — asumsi
  W2b); qty komponen dikonversi ke UoM produk komponen; `yield_percent` tidak dipakai (Q-ESB17). Butuh `mrp`.
- HPP (`cost`): `standard_price` per PT; produk yang sudah berstok di PT itu → `conflict` (tidak direvaluasi).
- Histori: flow/sales.daily = `bulk_upsert` per gudang setelah agregasi (cabang `location` → gudang induk);
  sales.line = `bulk_replace_slice` per gudang × range berkas dipotong `sales_line_window` (di luar → dilewati,
  `rows_skipped_window`; parameter 0 → `skipped_by_param`). Produk/menu tak terpetakan: flow dilewati
  (`rows_skipped_unmapped`), penjualan disimpan dengan `product_id` kosong.
- Dry-run = savepoint di-rollback setelah `odoo_totals`/`table_bytes` dihitung; run nyata commit per entitas/irisan.
- Neraca pembukaan (`opening`) = stub (`import_opening_balances`) — default mati, aktif pun exit 2 (Q-ESB21).

### G65 — supplierinfo, HPP cutover, stok awal, histori pembelian (docs/04 §27.13.6–§27.13.8)
| Langkah / berkas | Perilaku importer |
|---|---|
| `supplierinfo` (master, `supplierinfo.jsonl`, setelah `bom`, sebelum `cost`) | Upsert `product.supplierinfo` per xmlid `<brand>_<pt>_supplierinfo_<supplier>_<produk>`; `esb_key` wajib `<supplier_code>\|<product_code>` (exit 3 bila beda). Field milik migrasi `price`, `discount` (%), `date_start`, `product_uom_id`, `sequence`, `min_qty`, `currency_id` (IDR saja); saat create `partner_id` (`partner_key` → xmlid `supplier`), `product_tmpl_id`, `company_id`. `product_uom_id` = UoM produk (kontrak final W-B: harga selalu per UoM produk); `uom_xmlid` yang diisi tetapi ≠ UoM produk → baris `failed` (tidak dimuat). Diubah pengguna → `conflict` (L-131); tidak muncul lagi → `missing_in_source`, tidak dihapus. |
| `cost` | Seperti G43 + laporan `cost_sources` (`stock_opening`/`bom_last_hpp`/`purchase_last`; `source` lain → exit 3). Run nyata menulis `efn_mig_esb.cost_as_of.<PT>` = `as_of` hanya bila semua baris PT itu tidak gagal dan `as_of` tunggal. `conflict_has_stock` dipertahankan. |
| `stock` (`quant.jsonl`, bundel cutover tunggal; grup `cutover`) | Gerbang exit 4 sebelum tulis: PT tidak di `EFN_MIG_SALDO_BLOCKED`; `cost_as_of.<PT>` = `cutover_date`; `manifest.source.snapshot.fact_esb_stock_opening.opening_date` = `cutover_date`; tidak ada `stock.move` `done` non-inventaris (lokasi asal/tujuan internal PT) bertanggal ≥ cutover 00:00 WIB dan tidak ada sesi POS PT terbuka; produk storable ber-`tracking` → tolak; di production (`APP_ENV` fail-closed) `esb_locations` berpola damage/expired/temporary (pola = `efn_mig.mapping.SUSPECT_LOCATION`, dijaga test) tanpa entri `maps/location_map.csv` brand itu → tolak (SEC-G65-03; laporan validator tidak mengikat environment). Integritas exit 3: kunci `data` persis, `esb_key = <branch_id>\|<product_code>`, `location_key` = xmlid `branch_wh` (→ `lot_stock_id`) / `branch_loc` PT itu, lokasi internal milik PT, qty > 0, (lokasi, produk) unik. Per lokasi (satu savepoint): quant `(produk, lokasi, tanpa lot/paket/pemilik)` dicari; qty sama (presisi `Product Unit`) → `skipped`; selain itu `inventory_quantity` = target **absolut** (UoM produk; `uom_xmlid` berbeda dikonversi `_compute_quantity` hanya bila `_has_common_reference`, selain itu `uom_incompatible`) + `inventory_quantity_set` + `accounting_date = cutover − 1`, lalu `action_apply_inventory(date=cutover − 1 23:59:59 WIB → UTC)`; hasil dict (wizard konflik) = error. Non-storable dilewati (`stock_skipped.non_storable`). Quant > 0 Odoo yang tidak ada di snapshot → `stock_missing_in_snapshot` (dilaporkan); `zero_missing` (non-production saja) → disetel 0. Valuasi PB periodik → apply tanpa jurnal; closing `action_close_stock_valuation(auto_post=False)` = runbook, bukan importer. |
| `legacy_purchase_line` (segmen histori, part 1 saja) | Ganti irisan **company × range berkas** (`bulk_replace_slice(..., warehouse_id=None)`); semua status dimuat; `branch_key`/`supplier_key`/`product_key` tak terpetakan → Many2one kosong + hitungan `rows_unmapped_*` di laporan irisan; `(purchase_num, line_no)` ganda / referensi PT lain → exit 3. Segmen pembelian saja tidak butuh `target_warehouses`. `odoo_totals.legacy_purchase_line` = baris, Σ qty, Σ total per bulan × `branch_name` (rekonsiliasi 5e). |
| `cutover-param` (§27.13.8) | Manifest `cutover_date` > parameter: production → exit 4 kecuali opsi `allow_cutover_shift` = persis `<CODE>:<lama>:<baru>` (keputusan user 24 Nov 2026, gladi efn-prod; dicatat `import_report.cutover_shift_override`, lalu aturan non-production berikut berlaku); non-production → histori `[lama, baru)` boleh dimuat (helper bulk `shift_cutover`, batas = cutover manifest) dan parameter baru ditulis hanya bila setiap model histori yang pernah dimuat PT punya baris di setiap bulan `[lama, baru)` (selain itu exit 4). Manifest < parameter + `cutover-param` → exit 4 (geser mundur). |

Keputusan teknis G65:
- **Tanpa konteks `inventory_mode`** di importer stok: di Odoo 19 `_is_inventory_mode()` juga mensyaratkan user
  teknis anggota `stock.group_stock_user`, dan pada mode itu `create` memakai ulang quant + whitelist field. Importer
  mencari quant sendiri lalu write/create sebagai su, sehingga perilaku deterministik (fit-gap G65 §6 [B] #3 tidak
  relevan). `_apply_inventory` sendiri membuat move dengan `inventory_mode=False` + `is_inventory=True` [V 19.0].
- **Karantina (G65b, keputusan user 24 Nov 2026, fit-gap G65 §4.6):** baris quant ber-`quarantine_of` (xmlid
  `branch_wh` PT sama; `location_key` = xmlid `branch_quarantine` cabang `esb_branch_id`; kontrak dicek → exit 3) dimuat
  ke lokasi internal **"Karantina ESB"** yang dibuat/ditemukan SAAT langkah `stock` (master PB tidak diekspor ulang;
  karena itu `branch_quarantine` sengaja tidak di `XMLID_ENTITIES["branch"]`): induk `view_location_id` gudang (bukan
  di bawah `lot_stock_id` → tidak ikut `_get_available_quantity`/reservasi Stock), `company_id` PT,
  `replenish_location=False`, xmlid didaftarkan (idempoten; dry-run ikut rollback). Gudang `quarantine_of` tidak ada
  → baris gagal (fail-closed, tidak jatuh ke Stock). Quant karantina ikut gerbang, `odoo_totals.quant`, dan
  `_missing_quants` seperti quant lain. Tindak lanjut Ops = transfer internal / Scrap standar CE.
- Replay SEC-198 bundel stok = grup `.stock` (terpisah dari `.master`).
- Varian `bulk_replace_slice` tanpa gudang hanya untuk model `_efn_slice_by_warehouse = False`; model beririsan gudang
  menolak `warehouse_id=None` dan sebaliknya (SEC-197: DELETE tidak melebar diam-diam).

### Perbaikan review keamanan G43 (`docs/reviews/SECURITY_REVIEW_2026-11-18_g43.md`)
| Temuan | Perilaku importer |
|---|---|
| SEC-197 | Segmen histori WAJIB `manifest.target_warehouses` (daftar xmlid `branch_wh` company segmen; semua gudang tujuan company × bulan, termasuk tanpa baris). Untuk SETIAP gudang target: flow/sales.daily = agregasi per kunci grain lalu **ganti irisan** gudang × range berkas (`bulk_replace_slice`, baris basi terhapus); sales.line = ganti irisan (dipotong `sales_line_window`). Gudang target tanpa baris → irisan dikosongkan. Tanpa/rusak → exit 3; baris di luar target → exit 3; gudang target belum diimpor / milik PT lain → exit 4 sebelum histori ditulis. Impor ulang = `rows_deleted == rows_inserted` (bukan lagi "0 perubahan" upsert). |
| SEC-198 | Run nyata sukses penuh mencatat `ir.config_parameter` `efn_mig_esb.last_run.<brand>.<PT>.master` / `.history.<YYYY-MM>` = `{created_at, run_id}` (nilai maks). Bundel dengan `manifest.created_at` lebih tua → exit 4 (juga dry-run), kecuali `allow_older` (laporan `allow_older_used`, wajib dicatat di berita acara). Bundel sama/lebih baru = boleh. |
| SEC-201 | Laporan validator wajib `sig` = HMAC-SHA256(HKDF(`migration_bundle_key`, info `efn:mig-esb:val:v1`), canonical JSON tanpa `sig`); tidak ada/salah → exit 3. Pengecualian: dry-run non-production + `allow_missing_validation`. Fungsi (`derive_validation_key`, `sign_validation_report`, `verify_validation_report`) sementara di `importer.py` karena `efn_mig/bundle.py` belum memuatnya — **kontrak untuk validator W1**; pindahkan ke `efn_mig` bila data engineer menambahkannya. |
| SEC-200 | `run-import.sh` = `docker compose run --rm --no-deps -T` (container sekali jalan, label Traefik mati, tanpa port, tanpa cron); bundel > `EFN_MIG_MAX_BUNDLE_BYTES` (default 1 GiB) ditolak sebelum dibaca (exit 2). Batas memori = batas service `odoo` di compose (opsi `--memory` tidak ada di `compose run`). |
| SEC-202 | Hasil ditulis berkas sementara unik `O_CREAT|O_EXCL|O_NOFOLLOW` 0600 + fsync + `os.replace` atomik; bundel/validasi dibaca `O_NOFOLLOW`. |
| SEC-203 | `std_ref(xmlid, model)` memvalidasi model: UoM wajib `uom.uom`; salah model → baris gagal dengan pesan "xmlid … menunjuk model …, bukan uom.uom". |
| Gerbang | `confirm_brand` wajib untuk run nyata di production (fail closed) **dan** staging/stg (`requires_confirm_brand`). |
| Mode totals | `EFN_MIG_ARGV {"steps":["totals"],"companies":[…],"months":[…]}` → `run_totals` menghitung `odoo_totals` dari tabel (semua gudang PT, bulan penuh, tanpa tulis) → `<run_id>.totals.result.efnmig` (`import_report.mode = "totals"`) untuk `reconcile --final`. |
| Nama tampilan | Kunci opsional baris histori `esb_product_name` (flow) dan `menu_name`, `menu_category` (sales.daily) diisi bila ada (dipotong ke ukuran kolom). |

## Parameter (`ir.config_parameter`)
| Kunci | Default | Catatan |
|---|---|---|
| `efn_mig_esb.cutover_date.<KODE>` | — (tidak di-seed) | diisi langkah `cutover-param`; kosong/format salah → forecast perilaku lama (+ log peringatan) |
| `efn_mig_esb.sales_line_months` | `24` (`noupdate`) | 0..24; 0 = `sales.line` tidak dimuat; tidak valid → 24 + peringatan |
| `efn_mig_esb.legacy_days` | `400` (`noupdate`) | informasi (batas efektif = manifest, ditegakkan importer/validator) |
| `efn_mig_esb.cost_as_of.<KODE>` | — (tidak di-seed) | G65: ditulis langkah `cost` run nyata; gerbang (c) langkah `stock` |

## Hook forecast
`efn_demand_forecast` 19.0.1.1.0 punya `_efn_legacy_outflow(warehouse, product, date_from, date_to)` → `(None, {})`.
Modul ini meng-override: batas = cutover company gudang; dict = Σ `outflow` per hari untuk `[date_from, min(date_to,
batas − 1)]`. `_efn_daily_outflow` mengambil hari `< batas` dari legacy dan hari `≥ batas` dari `stock.move`
(stock.move sebelum cutover diabaikan — itu artefak migrasi). Signature mengikuti `docs/04` §27.8 / ADR-042 §3.2
(per gudang × produk), bukan `(company, products)`, karena forecast v1 menghitung per gudang.

## Volume & pengukuran
Perkiraan (architect §27.8a, belum diukur): `sales.line` PB ±8,2 juta baris ≈ **±1,5–2 GB** (heap ±150 B/baris +
indeks), `sales.daily` PB ±0,35–0,5 GB, `flow.daily` PB ±0,1 GB; lima brand ±4,5–6,5 GB. WAL sekali muat ≈ ukuran
tabel + indeks; replica ikut bertambah; backup harian `pg_dump`/basebackup bertambah sebesar itu (R-T08).
**Angka resmi = dry-run PB**:
```sql
SELECT relname, pg_size_pretty(pg_total_relation_size(oid))
  FROM pg_class WHERE relname IN ('efn_legacy_flow_daily', 'efn_legacy_sales_daily', 'efn_legacy_sales_line');
```
(diukur sebelum rollback dry-run; `ANALYZE` setelah run nyata, `VACUUM (ANALYZE)` setelah dry-run besar.) Profil dev
(K25-06): `sales_line_months = 0` atau 1.

## Retensi & PDP
Isi = data bisnis (kode/nama menu & produk, angka, `sales_num` = nomor dokumen ESB). **Tanpa data pribadi** (sumber
tidak memuat pelanggan/kasir). Retensi mengikuti dokumen penjualan (10 tahun, sama dengan ClickHouse L-109) sampai
keputusan Finance/DPO; hapus = uninstall modul atau ganti irisan oleh skrip migrasi. Ekspor CSV mengikuti
`base.group_allow_export`. Entri inventaris PDP: tugas efn-architect (W4).

## Risiko yang dimitigasi
R-L03 (pajak/valuasi ganda — histori bukan transaksi), R-O02/R-O13 (histori bias: validasi company/gudang/produk,
tanggal < cutover, idempoten, rekonsiliasi butir 5 oleh skrip), R-T08 (volume: indeks minimal, `_log_access=False`
untuk baris, parameter rentang), SEC-58 (tanpa metode RPC publik baru). G65: stok ganda / jurnal ganda dari histori
pembelian (arsip, bukan `purchase.order`, fit-gap G65 §1.1), penimpaan transaksi nyata oleh stok awal (gerbang
stock.move ≥ cutover & sesi POS, Q-ESB29), HPP stok awal salah (gerbang `cost_as_of`), stok ber-lot tanpa lot (gerbang
tracking), replay bundel stok lama (SEC-198 grup `.stock`).

## Uji manual
1. Install modul; Inventaris › Laporan › Histori ESB (arsip) tampil kosong dengan pesan arsip.
2. `odoo shell`: `env["efn.legacy.flow.daily"].sudo().with_context(efn_legacy_import=True)._efn_bulk_upsert([...],
   "<uuid>", [company.id])` dua kali → jumlah baris sama; ubah satu nilai → `updated = 1`.
3. Login user PB → hanya baris PB; user stok biasa tidak melihat menu penjualan.
4. Set `efn_mig_esb.cutover_date.PB` → Forecast EFN › Hitung: `history_qty` memuat outflow legacy untuk hari sebelum
   cutover.
5. (G65) User Purchase PB: Purchase › Histori ESB (arsip) tampil (list/pivot), hanya baris PB; user stok biasa tidak
   melihat menu. Setelah langkah `supplierinfo`: produk › tab Pembelian memuat supplier ESB, urutan `sequence`
   (supplier terbaru = 1); buat RFQ satu produk dengan `product_uom_id` ≠ UoM produk → periksa harga/qty ([B]).
6. (G65) Gladi stok: `cost` run nyata dengan `--cutover-date` → parameter `efn_mig_esb.cost_as_of.PB`; `stock` dry-run
   lalu nyata → Inventaris › Laporan › Riwayat Pergerakan: move "Saldo awal ESB <cutover−1>" bertanggal cutover−1
   23:59:59 WIB; impor ulang bundel sama → 0 move baru. Closing valuasi draft lewat runbook.

## Batasan / belum
- Importer ditulis (W2b) tetapi **belum dijalankan** terhadap Odoo 19 nyata maupun bundel PB nyata; `table_bytes`
  resmi menunggu dry-run PB. Perilaku `pos.config` create di PT tanpa CoA, keamanan xmlid modul `efn_mig_esb` yang
  tidak terpasang saat upgrade modul lain (§27.4 butir 6) = [B].
- Test **belum dijalankan** oleh penulis (tanpa shell); lead menjalankan di container Odoo 19.
- "Wajib ada filter tanggal" pada list `sales.line` hanya berupa filter default (tidak dipaksa server).
- `sales.daily`: kunci memakai `esb_menu_code` (bukan `(product_id, day, is_takeaway)` kontrak) agar menu tak
  terpetakan tetap bisa di-upsert; setara bila semua menu terpetakan. Bila dua cabang ESB dipetakan ke satu gudang
  (cabang `location` → gudang induk), importer mengagregasi dulu sebelum upsert (`mapping.aggregate`).
- Penurunan `sales_line_months` tidak menghapus baris lama (runbook: ganti irisan kosong).
- Referensi desain: tidak ada modul OCA/Cybrosys yang dipakai/dirujuk (pola upsert = PostgreSQL standar).
- G65 [B] (belum terverifikasi; test ditulis, dijalankan lead): perilaku RFQ dengan `product_uom_id` supplierinfo ≠ UoM
  produk; efek `date` mundur pada AVCO (`standard_price` tidak bergeser) dan laporan stok per tanggal; filter `at_date`
  closing valuasi terhadap `stock.move.date`; arti `discount` ESB (nominal/persen, Q-G65-2). Satuan ESB beda teks tetapi
  UoM Odoo sama (mis. PACK=6 EA → Unit) **tidak** dikonversi `ea_factor` di langkah `stock` (bundel stok tanpa
  `uom.jsonl`) — export wajib mengirim qty dalam satuan produk atau `uom_xmlid` yang berbeda; selain itu qty salah skala.
- G65: `prepass_stock` me-resolve semua baris quant ke memori sebelum tulis (PB ±2–5 rb baris; aman).
