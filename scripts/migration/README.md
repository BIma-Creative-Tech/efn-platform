# scripts/migration — mesin migrasi ESB → Odoo (G43, sisi data)

Tujuan: memindahkan data induk + histori ESB (via ClickHouse `efn_dwh`, hasil `esb-extract`) ke Odoo CE 19 dengan
bundel terenkripsi yang bisa divalidasi, diulang tanpa duplikasi, dan direkonsiliasi. Acuan: **ADR-042 (Accepted
18 Nov 2026)**, `docs/04` §27 (v19.1), fit-gap `docs/fit-gap/G43_MIGRASI_ESB_ODOO.md`. Risiko: R-O02, R-O12, R-O13,
R-L01 (PDP), R-L12. **Dokumen ini = kontrak bersama export (efn-data-ai-engineer) ↔ importer `odoo_import_esb.py`
(efn-odoo-engineer).** Bila bertentangan dengan `docs/04` §27, §27 menang — laporkan ke efn-lead.

| Berkas | Isi |
|---|---|
| `esb_to_odoo.py` | CLI `export` / `writeback` / `reconcile` (container `esb-migrate`) |
| `validate_import.py` | validator `--source esb-bundle` (§27.7); mode `templates` (docs/08 §6.4) **belum dibangun** |
| `efn_mig/bundle.py`, `efn_mig/xmlid.py` | format bundel & xmlid — **stdlib + `cryptography` saja**, boleh di-import importer Odoo |
| `efn_mig/mapping.py`, `segments.py`, `export.py` | pemetaan baris ClickHouse → JSONL, rentang & segmen histori |
| `efn_mig/validate.py`, `reconcile.py`, `results.py` | aturan validator, laporan rekonsiliasi, writeback |
| `efn_mig/source.py`, `settings.py` | I/O ClickHouse (user `efn_mig`, HTTPS 8443 + CA internal), env/secret |
| `tests/` | pytest, fixture sintetis (tanpa data klien) |
| `odoo_import_esb.py`, `run-import.sh` | importer sisi Odoo (efn-odoo-engineer, W2b): payload `odoo shell` + pembungkus host; inti di `services/odoo/addons/efn_legacy_history/esb_import/` (README modul). Argumen `EFN_MIG_ARGV` = header `odoo_import_esb.py` |

## Alur
```
esb-extract snapshot (E1/E2/E4/E5) ─► ClickHouse efn_dwh ─► export (bundel .efnmig) ─► validate_import (validation_<run>.json)
   ─► odoo_import_esb.py (odoo shell; dry-run = rollback) ─► <run>.result.efnmig ─► writeback (map_esb_odoo) + reconcile
```
Tidak ada jalur jaringan Odoo ↔ ClickHouse (L-129): pertukaran lewat berkas di `${MIG_EXCHANGE_DIR}`.

## Env / secret (`esb-migrate`)
`CH_HOST`=`clickhouse`, `CH_PORT`=`8443`, `CH_USER`=`efn_mig`, `CH_PASSWORD_FILE`=`/run/secrets/clickhouse_password_mig`,
`CH_SECURE`=`true`, `CH_CA_FILE`=`/run/secrets/efn_ca_crt`, `MIG_BUNDLE_KEY_FILE`=`/run/secrets/migration_bundle_key`,
`MIG_WORKDIR`=`/work` (tmpfs ≥ 768 MB), `MIG_OUT_DIR`=`/out`, `APP_ENV`, `EFN_MIG_SALDO_BLOCKED`=`IMD,CUP`,
`EFN_MIG_LEGACY_DAYS`=`400` (1..730), `EFN_MIG_SALES_LINE_MONTHS`=`24` (0..24), `EFN_MIG_SEGMENT_MAX_ROWS`=`500000`
(1000..5000000), `EFN_ALLOW_PLAINTEXT`=`false`, `EFN_GIT_SHA` (opsional, masuk manifest). Production (APP_ENV kosong =
production) wajib `*_FILE` untuk secret. Dependency: `requirements.txt` (`cryptography` di-pin; **image esb-extract
belum memuatnya** → devsecops menambah ke image `esb-migrate`).

## CLI
```
python esb_to_odoo.py export --brand pb --step master --maps /maps --out /out [--cutover-date D]
python esb_to_odoo.py export --brand pb --step history --cutover-date D --maps /maps --out /out \
       [--months 2025-01,2025-02] [--legacy-days N] [--sales-line-months N]
python esb_to_odoo.py export --brand pb --step cutover --cutover-date D --maps /maps --out /out
python validate_import.py --source esb-bundle /out/<bundel>.efnmig --out /out
python esb_to_odoo.py writeback --result /out/<run_id>.result.efnmig
python esb_to_odoo.py reconcile --bundle <b1> --bundle <b2> --result <r1> --result <r2> --out /out [--with-source]
```
Langkah: `branch uom category product menu partner bom supplierinfo cost` (grup `master`), `legacy_flow
legacy_sales_daily legacy_sales_line` (grup `history`), `cutover` = histori + `cutover-param`. `stock`/`opening` →
exit 2 "belum dibangun" (menunggu E6–E10 terverifikasi). `supplierinfo` tidak punya sumber ESB (dicatat di manifest).
Exit: 0 OK · 1 data/selisih rekonsiliasi · 2 argumen/konfigurasi · 3 integritas · 4 gerbang (company diblokir, hasil dry-run).

## Tabel pemetaan (`--maps`, data klien — di luar repo; ikut bundel `maps/*.csv`, hash di manifest)
- `branch_map.csv`: `brand,branch_id,type,warehouse_code,display_name,parent_branch_id`; `type` ∈ `store` (gudang +
  `pos.config`), `ck`, `warehouse`, `location` (lokasi internal di bawah gudang `parent_branch_id`), `skip`.
  `warehouse_code` 1..5 karakter unik per company. Cabang aktif tanpa baris → `manifest.unmapped_branches` → error.
- `uom_map.csv`: `esb_uom,uom_xmlid,ea_factor` — teks satuan ESB (dinormalisasi huruf besar) → xmlid UoM Odoo;
  `ea_factor` = jumlah EA per 1 satuan itu (kosong = 1), dipakai mengonversi histori aliran (EA) ke UoM produk.
- `coa_map.csv`, `payment_map.csv`: opsional, ikut bundel apa adanya (dipakai langkah `opening`, belum dibangun).

## Format bundel `efn-mig-esb/1` (§27.3, §27.3a)
```
<header JSON kanonik>\n  ‖  nonce 12 byte  ‖  AES-256-GCM(tar, AAD = byte header termasuk \n)  (tag 16 byte di akhir)
header = {"brand":"pb","format":"efn-mig-esb/1","kind":"bundle"|"result","run_id":"<uuid>"}   (sort_keys, tanpa spasi)
```
- Kunci: `migration_bundle_key` = hex ≥ 64 karakter → `bytes.fromhex` → HKDF-SHA256 (salt kosong, panjang 32)
  `info=b"efn:mig-esb:enc:v1"` (AES) dan `info=b"efn:mig-esb:mac:v1"` (HMAC). Lihat `bundle.derive_keys`.
- Tar tanpa kompresi, deterministik (urut nama, mtime 0, mode 0600, uid/gid 0). Anggota hanya berkas reguler bernama
  `^(maps/)?[a-z0-9_]+(\.jsonl|\.jsonl\.gz|\.json|\.csv)$` + `manifest.json`; lainnya → integritas.
- `manifest.json` = §27.3 (+ kunci tambahan di bawah); `sig` = HMAC-SHA256(mac_key, canonical JSON manifest tanpa
  `sig`); canonical = `sort_keys`, separator `(",", ":")`, UTF-8, `allow_nan=False`.
- Per berkas: `sha256` atas byte yang disimpan di tar (terkompresi untuk `.gz`); `rows` = jumlah baris JSONL
  terdekompresi (CSV: tanpa header). gzip deterministik (mtime 0). Batas dekompresi: ≤ 512 MB dan rasio ≤ 50× (rasio
  diperiksa setelah > 1 MiB terdekompresi, agar berkas kecil tidak salah tolak).
- Urutan verifikasi pembaca (`bundle.open_bundle`): header kanonik → dekripsi/tag → struktur tar → HMAC → `format/run_id/
  brand/kind` manifest = header → sha256 + rows setiap berkas & daftar berkas persis. Semua sebelum DB disentuh.
- Satu bundel dienkripsi sekaligus di memori. **Bundel histori dipecah per company × bulan** (dan per gudang bila baris
  > `EFN_MIG_SEGMENT_MAX_ROWS`; satu gudang tidak terbelah) → nama `<parent_run_id>_<CODE>_<YYYYMM>[_pN].efnmig`.
  Bundel master: `<run_id>.efnmig`.

Kunci manifest tambahan (export): `unmapped_branches` (master), `notes`, `branch_ids` (gudang dalam segmen),
`stats.<entitas>.{rows_exported, rows_skipped_unmapped}` (histori), `files[].range` = `[mulai, akhir)` tanggal (histori).

## Baris JSONL master — `{"entity","company_code","esb_key","xmlid","data"}` (§27.3)
`xmlid` = `efn_mig_esb.<brand>_<company huruf kecil>_<entitas xmlid>_<key>`, `key` = `xmlid.xmlid_key(esb_key)`
(§27.4: normalisasi `[^a-z0-9_]`→`_`, potong 48, `+ "_" + sha1[:8]` bila kode tidak cocok `^[A-Z0-9_]{1,48}$`).
Importer **wajib menghitung ulang** (beda → exit 3). Produk/menu/partner/BoM **kembar per company** brand (Bacha:
BCF + BCR). Referensi antar-entitas = `*_key` (xmlid lengkap). Angka = angka JSON (int bila bulat), Rupiah exclude
pajak, tanggal ISO `YYYY-MM-DD`.

| Berkas | entitas xmlid | `esb_key` | `data` (whitelist; validator menolak kunci lain) |
|---|---|---|---|
| `branch.jsonl` | `branch_wh` (store/ck/warehouse) atau `branch_loc` (location) | `branch_id` | `branch_id, name, warehouse_code, type, is_central_kitchen, active, pos_xmlid` (xmlid `branch_pos`, hanya store) / lokasi: `branch_id, name, type="location", parent_key, active` |
| `uom.jsonl` | `uom` | teks satuan ESB | `esb_uom, uom_xmlid` (null = tak terpetakan → error), `ea_factor` |
| `category.jsonl` | `category` | nama kategori ESB | `name, parent_key` (null; ESB satu tingkat) |
| `pos_category.jsonl` | `pos_category` | `kategori` atau `kategori/detail` | `name, parent_key` |
| `product.jsonl` | `product` | `product_code` | `default_code, name, categ_key, uom_esb, uom_xmlid, ea_factor, type="consu", is_storable=true, purchase_ok, sale_ok=false, is_fin, active, batch_to_ea_ratio` |
| `menu.jsonl` | `menu` | `menu_code` | `menu_code, name, pos_categ_key, list_price, available_in_pos, sale_ok, type="consu", active, bom_name, alias_of, is_takeaway` — takeaway `" T"` dengan menu dasar: `alias_of` = xmlid menu dasar → importer **tidak** membuat produk baru, hanya menambah xmlid kedua ke record dasar. Menu arsip (hanya di histori 24 bulan / nonaktif): `active=false` → importer membuat produk arsip |
| `supplier.jsonl` | `supplier` | kode | `ref, name, is_company, supplier_rank, active` |
| `customer.jsonl` | `customer` | kode | `ref, name, is_company, customer_rank, category_name, payment_term_key, active` (tanpa alamat/PIC/telepon/NPWP) |
| `payment_term.jsonl` | `payment_term` | `net_<hari>` | `days, name` |
| `bom.jsonl` | `bom` | `bom_id` | `code, name, type` (`phantom` bila ditautkan menu lewat `bomName`, selain itu `normal`), `bom_type_id, bom_type_name, product_code, product_key` (normal), `menu_keys` (phantom: BoM kit per menu), `product_qty` (rasio BATCH→EA bila `ratio_source="bom_ratio"`, selain itu 1), `uom_esb, uom_xmlid, ratio_source, lines:[{product_key, product_code, qty, uom_esb, uom_xmlid, yield_percent}]` |
| `cost.jsonl` | — (`xmlid` null) | `product_code` | `product_key, product_code, standard_price` (lastHpp komponen BOM terbesar > 0), `source_uom` (satuan HPP; konversi bila ≠ UoM produk [B]) |

## Baris JSONL histori (objek datar, company = `segment.company_code`, berkas `.jsonl.gz`)
| Berkas → model | Kunci (persis; lain = error PII) | Satuan |
|---|---|---|
| `legacy_flow_daily` → `efn.legacy.flow.daily` | `day, branch_key, product_key, esb_branch_id, esb_product_code, sold, ck_prod, store_prod, ck_out, in_ck, in_other, st_in, st_out, outflow` | qty dalam **UoM produk** (= EA / `ea_factor`), 3 desimal; `outflow = sold + st_out + ck_out` (sudah dihitung) |
| `legacy_sales_daily` → `efn.legacy.sales.daily` | `day, branch_key, pos_key, menu_key, esb_branch_id, esb_menu_code, is_takeaway, qty, gross, discount, net, cogs, trx_count` | Rupiah; `net = gross − discount` (sudah dihitung); `menu_key` takeaway = menu dasar |
| `legacy_sales_line` → `efn.legacy.sales.line` | `sales_num, line_no, date, branch_key, menu_key, esb_menu_code, qty, price, total, discount, cogs, row_type, is_takeaway` | Rupiah; `menu_key` boleh null (baris tetap disimpan, §27.8); tanpa `run_id` per baris |
`branch_key` = xmlid `branch_wh` (cabang `location` → gudang induk). Baris dengan cabang/produk tak terpetakan **tidak
diekspor** (dihitung di `stats`). Idempotensi impor = ganti irisan company × gudang × bulan (`DELETE`+`INSERT`, §27.8a);
satu bundel segmen = irisan bulan penuh untuk gudang di `branch_ids` (bulan cutover: hari < cutover).

## Bundel hasil impor (`kind="result"`, dibuat importer dengan `bundle.build_bundle(..., kind="result")`)
Manifest: `run_id` = run_id bundel sumber, `brand`, `company_codes`, `parent_run_id`, `files` (`rows` hanya dicek untuk
`.jsonl`). Berkas:
- `import_report.json`: `{"dry_run": bool, "entities": {"<entitas>": {"created","updated","skipped","conflict","failed",
  "missing_in_source", "dangling_xmlid"}}, "slices": [{"model","company_code","branch_key","month","rows_deleted",
  "rows_inserted","rows_skipped_unmapped","seconds"}], "table_bytes": {"<tabel>": n}}`. `writeback` menolak
  `dry_run` ≠ `false` (exit 4).
- `odoo_totals.json` (histori): `{"legacy_flow_daily": [{"month":"YYYY-MM","branch_key","product_key", <8 kolom>,
  "outflow"}], "legacy_sales_daily": [{"month","branch_key","menu_key","is_takeaway", "qty","gross","discount","net",
  "cogs","trx_count"}], "legacy_sales_line": [{"month","branch_key","rows","qty","total","discount","cogs"}],
  "bulk_check": {"wrong_company_rows": 0, "foreign_product_rows": 0}}` — agregat **dari tabel Odoo setelah tulis**
  (sebelum rollback pada dry-run), bukan dari bundel.
- `map.jsonl` (master): `{"entity","company_code","esb_key","odoo_model","xmlid","odoo_id"}` per xmlid → `writeback`
  ke `efn_dwh.map_esb_odoo` (ReplacingMergeTree, ulang = idempoten).

## Rekonsiliasi (§27.5) — `reconcile_<parent_run_id>.md/.csv`
1 jumlah per entitas (selisih bila gagal > 0 atau total status ≠ baris bundel; konflik = peringatan) · 2–4 "tidak
dijalankan" (langkah stok/saldo default mati) · 5a/5b/5c Σ per bulan × gudang × produk/menu vs `odoo_totals` (toleransi
qty 0,001, uang 0,01) · 5d `bulk_check` = 0 · baris tak terpetakan (info) · 6 rasio net/pembayaran per cabang-bulan
(`--with-source`, peringatan di luar 0,80–0,98) · 7 snapshot `v_esb_data_quality` (`--with-source`).

## PDP & retensi
Bundel tanpa kunci identitas orang (whitelist per entitas + pola terlarang `address|pic|phone|email|creator|editor|
authorizer|npwp|…`). `customer.name` bisa nama orang untuk pelanggan perorangan → data pribadi, ikut inventaris PDP.
Bundel/hasil dihapus ≤ 90 hari setelah berita acara (Q-ESB24); tidak boleh di repo/tiket/chat. Plaintext hanya di tmpfs
(`MIG_WORKDIR`); berkas sementara dihapus setelah dimasukkan ke bundel.

## Belum / belum diverifikasi
- Tidak dijalankan terhadap ClickHouse/Odoo nyata (penulis tanpa shell). SQL `source.py` (`query_row_block_stream`,
  `has({ids:Array(UInt32)}, …)`) perlu uji di ClickHouse 24.8.
- Langkah `stock`, `opening`, `supplierinfo`, `combo` (Q-ESB12) belum diekspor. `validate_import.py --source templates`
  belum dibangun. Pemeriksaan "company ada di Odoo target" & pajak lintas PT = tugas importer.
- Penentuan `phantom` vs `normal` dari tautan `bomName` menu dan arti `bomTypeID` = asumsi [B] (Q-ESB17);
  `standard_price` dari `lastHpp` dalam satuan komponen [B]; satuan produk ESB tidak ada di `/product` (fallback satuan
  BOM).
- Ukuran segmen: perkiraan 500 rb baris ≈ 125 MB JSONL ≈ 15–25 MB gzip; ukur di dry-run PB.
