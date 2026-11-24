# Laporan agent: efn-data-ai-engineer

Tanggal: 2026-10-05. Lingkup: OCR worker Price Hunter, ETL PostgreSQL -> ClickHouse, guard Text-to-SQL, skema ClickHouse.
ADR terkait (semua `Proposed`): ADR-003, ADR-006, ADR-007, ADR-008, ADR-010, ADR-014, ADR-020. Kontrak: `docs/04_SERVICE_CONTRACTS.md` §3.3, §4, §5.

**Tidak ada yang dijalankan.** Mesin penulis tidak punya Python; semua kode, test, dan SQL belum pernah dieksekusi. Validasi pertama terjadi di CI / oleh QA.

## Rekonsiliasi merge #165 + penomoran ulang G67 → G68 (25 Nov 2026)

Worktree `efn-platform-g67`, branch `feat/esb-insight-pl-waste-fillrate`, saat lead menjalankan `git merge origin/main`
(5f16eb3 = PR #165 "Margin bahan", yang lebih dulu memakai label G67, docs/04 §31, `24-margin-bahan.sql`, L-259..L-264).
**Tidak ada yang dijalankan** (tanpa shell/git): pytest, ruff, dan `git rm` file lama dikerjakan lead.

- Konflik diselesaikan dengan **mempertahankan kedua fitur**: docs/04, laporan ini, `insight-access.sql`,
  `insight-esb-policy.sh`, `clickhouse-init.sh`, `efn_esb/__main__.py`, `efn_esb/runner.py`, `tests/test_ddl.py`,
  `tests/test_insight_access.py`, `tests/test_insight_fnb_access.py`.
- **Jumlah policy ESB = 25** (20 kelas PT + 5 kelas BR): 12 PT dasar (G52/G53/YoY) + 3 margin bahan + 5 G68. Dikunci di
  `clickhouse-init.sh` (`ESB_POLICY_EXPECTED=25`), `test_insight_access.py` (`ESB_POLICY_COUNT`),
  `test_insight_fnb_access.py`, `test_margin.py` (semula 20), `test_g68_ddl.py` (PT = 20).
- Gelombang paket W-A ini **dinomori ulang G68**: docs/04 §31 → **§32** (v21.6 → v21.7; baris v21.6 untuk margin bahan
  ditambahkan karena #165 tidak menambah baris versi), L-260..L-268 → **L-270..L-278** (bentrok dengan L-259..L-264
  margin bahan; L-27x belum dipakai di repo), `24-g67-…sql` → `25-g68-esb-pl-waste-fillrate.sql`, `extract_g67.py` /
  `transform_g67.py` → `*_g68.py` (`G67_TABLES`/`G67_EXTRACTORS` → `G68_*`), test `test_g67*.py` → `test_g68*.py`,
  harness → `g68_views_harness.sh`, view `v_esb_g67_quality` → `v_esb_g68_quality`, flag `INSIGHT_G67_ENABLED` →
  `INSIGHT_G68_ENABLED` (compose, `deploy.sh`, `deploy-validate.sh`), SEC-G67-01 → SEC-G68-01 di kode/test. ADR-040 §6b
  menjadi "Amandemen G68" (#165 tidak menambah amandemen). Isi bagian di bawah tidak diubah (rekaman historis; baca
  G67/§31/L-26x sebagai G68/§32/L-27x).
- `test_g68_ddl.py::test_init_file_renumbered_after_margin_bahan` sengaja gagal selama file lama
  `infra/clickhouse/init/24-g67-esb-pl-waste-fillrate.sql` masih ada (DDL ganda di glob `init/*.sql`).

## Gelombang 67 → 68 paket W-A — esb-extract + ClickHouse: laba rugi outlet, waste, fill rate (25 Nov 2026)

> Dinomori ulang G68 saat merge #165 (lihat bagian rekonsiliasi di atas).

Branch `feat/esb-insight-pl-waste-fillrate` (worktree `efn-platform-g67`). Kontrak `docs/04` §31 + ADR-040 §6b. **Tidak
ada yang dijalankan** (tanpa shell): pytest, ruff, DDL di ClickHouse 24.8, dan harness view dijalankan lead.

### File
- Baru: `services/esb-extract/efn_esb/extract_g67.py`, `efn_esb/transform_g67.py` (murni),
  `infra/clickhouse/init/24-g67-esb-pl-waste-fillrate.sql`, `tests/test_g67.py`, `tests/test_g67_ddl.py`,
  `tests/clickhouse/g67_views_harness.sh`.
- Diubah: `efn_esb/config.py` (entitas opt-in, `BACKFILL_ENTITIES`, `RETRANSFORM_ENTITIES`, `ESB_PL_REFRESH_MONTHS`),
  `normalize.py` (whitelist G67, `whitelist_tree`, `whitelist_doc`), `planning.py` (`pl_*`), `extract.py`
  (`SliceResult.derived`, `BrandRuntime.delivery_ref/product_ids/pl_branches`, `delivery_ref` di `extract_delivery`),
  `runner.py` (P&L di run/backfill, checkpoint turunan, `run_retransform`, `_finish(calls)`), `store.py`
  (`load_product_ids`, `load_raw`), `__main__.py` (`retransform`, validasi `delivery_ref`), `init/19-insight-review.sql`
  (`v_esb_coverage` + 3 entitas), `access/esb-access.sql`, `access/insight-access.sql`, `access/insight-esb-policy.sh`,
  `clickhouse-init.sh` (`ESB_POLICY_EXPECTED=22`), `infra/compose/docker-compose.esb.yml` (`ESB_PL_REFRESH_MONTHS`),
  test `test_insight_access.py`, `test_insight_fnb_access.py`, `test_ddl.py`, `test_runner.py` (FakeStore), README,
  `docs/runbooks/esb-extract.md` §5b.

### Nama final (antarmuka W-B)
Tabel: `fact_esb_profit_loss`, `fact_esb_item_journal_line`, `fact_esb_transfer_request_line`,
`fact_esb_delivery_ref_line`, `dim_esb_product_id`. View dibungkus `efn_insight.*` (kolom = §31.6 persis, kelas PT):
`v_esb_pl_monthly`, `v_esb_item_usage_daily`, `v_esb_item_usage_product_monthly`, `v_esb_fill_rate_daily`,
`v_esb_fill_rate_product_monthly`. Internal: `v_esb_fill_rate_line` (kolom §31.6), `v_esb_item_journal_line` (baru),
`v_esb_g67_quality` (admin saja). Nilai `section` baris summary = kunci pos ESB camelCase (`netSales`, `grossProfit`,
`operationalExpenses`, `earningBeforeInterestTaxDepreciationAmortization`, `netProfitLoss`, …); `scope` = `branch|brand`.

### Penyimpangan dari teks kontrak (DDL menang untuk detail)
1. `fact_esb_profit_loss.source_lines UInt16` (tambahan): jumlah baris akun sumber yang dijumlah; dasar cek
   `dup_account` (penjumlahan di transform menghapus jejak duplikat).
2. View internal `v_esb_item_journal_line` (tambahan): satu tempat untuk filter versi slice, `ITEM_JOURNAL_COUNTED_STATUS`,
   dan `multiIf` kelas purpose (dipakai dua view usage + quality). Di-grant ke definer (dirujuk), tidak dibungkus.
3. Jumlah policy ESB **17 → 22** (kontrak menulis 15 → 20; basis di main sudah 17 karena YoY). `20-esb-aggregates.sql`
   tidak ada di worktree ini (#164 belum termasuk) → rekonsiliasi angka saat merge.
4. `lines_open` = baris `is_mature = 0` dengan `request_qty > 0` (semua status): kolom `v_esb_fill_rate_line` dikunci
   kontrak tanpa status/bypass, jadi baris batal yang masih muda ikut "masih berjalan" sampai matang.
5. `uom_mismatch` = ada GD untuk (permintaan, produk) tetapi TIDAK ada satu pun bersatuan sama. GD yang punya satuan sama
   **dan** satuan lain → baris dihitung dengan qty satuan sama saja (yang beda satuan diabaikan, tidak dikonversi).
6. `v_esb_g67_quality` menambah `gtr_bypass_gd` dan `gtr_product_unmapped`; `pl_sign_check` = porsi baris akun negatif
   per seksi beban + `formula_abs_diff` (|netProfitLoss − rumus fallback §31.1.1|).
7. `dim_esb_product_id` hanya diisi `transfer_request` (source `gtr`); `master` belum (opsional, `productID` di `/product`
   [B]).

### Asumsi skema / perilaku
- Respons `get()` = objek `result` envelope (sama G41). P&L: seksi top-level = list baris akun; summary `details` dipakai
  bila setiap elemen punya `periods`/`area`, jika tidak `total` untuk bulan M. Scope brand: semua `details` dijumlah,
  prefix label tidak dicek, `area` opsional; scope cabang: prefix label dan `area.id` wajib = cabang, `area.value` wajib.
- Konstanta sementara: `ITEM_JOURNAL_COUNTED_STATUS` = nama `authorized/finished/closed` (lower/trim);
  `GTR_COUNTED_STATUS` = `status_id IN (3, 6)` (pola `TRANSFER_COUNTED_STATUS` G41); `GTR_ORIGIN_IS_SUPPLIER = True`.
- `delivery_ref` ditulis hanya bila `delivery` DAN `delivery_ref` aktif; checkpoint `delivery_ref` (calls 0) setelah
  checkpoint `delivery`, versi sama. `backfill --entity delivery_ref` tanpa `delivery` → exit 2 (arahkan ke retransform).
- Retransform: list raw `inventory/goods-delivery` (`doc_key=D`) + detail `inventory/goods-delivery/{num}` dengan
  `doc_date = D`; tanpa raw baru; `extract_run.mode='retransform'`, `calls=0`; exit 1 bila ada `raw_missing`.
- P&L `run`: cabang aktif semua tipe + `*`, sekali/hari (checkpoint slice tahun selesai setelah 03:00 WIB hari ini);
  `backfill`: semua cabang (nonaktif bila `ESB_BACKFILL_INCLUDE_INACTIVE`). `month` dihitung dari versi slice (WIB).
- `ESB_PL_REFRESH_MONTHS` tidak valid → 2 (kontrak: tidak gagal start).

### Uji (sintetis, belum dijalankan)
`test_g67.py`: label bulan + fail closed (8 kasus detail), akun ganda, summary total-saja, scope brand, parameter GET
(`branchIDs` tunggal, tanpa `branchIDs[]`, month 11/12), whitelist PII (raw + mart), `line_no`/`detail_id` fallback stabil,
`value` ROUND_HALF_UP, tolak tanggal/nomor detail beda (tanpa tulis), GTR arah/PT peminta/merge/resolusi kode,
pasangan productID ambigu, `delivery_ref` kirim/terima terpisah + referensi per baris, checkpoint turunan terakhir,
**retransform = hasil extract & idempoten (2x)**, `raw_missing` (detail hilang, nomor dipakai ulang, list hilang),
planning P&L run/backfill, run_cycle/run_backfill P&L, konfigurasi, CLI (exit 2, kill switch).
`test_g67_ddl.py`: kolom/tipe/ORDER BY/PARTITION, kontrak kolom 7 view, filter versi slice, rumus fill rate, kelas
purpose, grant positif/negatif, policy PT, `insight_reader` tanpa efn_dwh. Harness `g67_views_harness.sh`: 7 kasus wajib
§31.6.1 (+ trans_type lain, status batal, bypass), angka daily/product, P&L versi slice + selisih brand, kelas/status
jurnal, resolusi `ID:`, negatif row policy di kelima view (WTZ, `company_code ''`, `brs` terisi).

### Putaran perbaikan (security + QA, 25 Nov 2026)
- SEC-G67-01: `BrandConfig.multi_company` (override ke PT lain); total brand P&L brand multi-PT → `company_code=''`
  (row policy membuang), PB tetap PT default. Test positif PB + negatif Bacha; harness: scope BCF / BCF+BCR tidak melihat
  total brand Bacha. docs/04 §31.1.1 diselaraskan.
- QA P2#4: `is_counted` + `r.product_code != ''` (quality `gtr_product_unmapped` sudah ada); harness TFE.
- QA P3: `first_delivery_date` = `minIf(delivery_date, qty_sent > 0)`, NULL bila sebelum `transfer_date`; `lead_days_*`
  hanya baris dengan `first_delivery_date` terisi; harness TF10 (GD qty 0 diabaikan → +4 hari), TF11 (GD sebelum
  permintaan → NULL). Angka harness daily D30 `7|4|7|4|2|4.6|1|0|8|4`, produk P1 EA `6|47|34|39`.

### Belum terverifikasi
Semua **[B]** §31.13 (struktur summaries, bentuk respons total brand, tanda beban, filter tanggal list, stabilitas `ID`,
arti `hpp`/`qty` GTR/`bypassGoodsDelivery`, arah GTR, daftar status, `referenceNumber` header vs baris, performa view fill
rate). Belum ada uji extractor terhadap ESB nyata; backfill PB + distribusi status menunggu lead (malam). Sintaks SQL
(ClickHouse 24.8, analyzer baru) belum dieksekusi: `makeDate`, tuple-IN dengan `if()`, `if(..., NULL)` → Nullable(Date).

## Gelombang 67 — "Margin bahan", sisi data (25 Nov 2026)

Branch `feat/insight-margin-bahan` (tanpa commit). Kontrak `docs/04` §31 (+ §25.7, pola PR #164). Tanpa ADR baru
(turunan baca ADR-040/041, Proposed). Frontend `/margin-bahan` bukan bagian paket ini.

**Status verifikasi: BELUM ADA YANG DIJALANKAN.** Sesi ini tidak punya akses shell (hanya baca/tulis file), jadi
perintah wajib `ruff check` + `ruff format --check` + `pytest` di container `python:3.12-slim` dan uji DDL di
ClickHouse 24.8 **belum** dijalankan. Kode ditulis mengikuti gaya ruff/black secara manual (≤ 120 kolom, trailing
comma), tetapi lead wajib menjalankan perintah verifikasi sebelum merge.

### File
| File | Isi |
|---|---|
| `infra/clickhouse/init/24-margin-bahan.sql` (baru) | 3 tabel `efn_dwh.mart_esb_ing_price_monthly` / `mart_esb_menu_cost_monthly` / `mart_esb_ing_group_monthly` (MergeTree, `ORDER BY (brand, company_code, _version, month, …)`, `TTL toDateTime(_loaded_at) + INTERVAL 60 DAY`, `storage_policy = 'encrypted'`) + 3 view `efn_dwh.v_esb_*` versi terakhir per brand |
| `infra/clickhouse/access/esb-access.sql` | esb_writer `SELECT, INSERT` pada 3 tabel (sumber sudah ter-grant: dim_esb_bom(_line), fact_esb_purchase_line, dim_esb_branch, agg_esb_sales_daily, extract_checkpoint, aggregate_refresh(_lock)) |
| `infra/clickhouse/access/insight-access.sql` | definer `SELECT` 3 view + 3 tabel; 3 wrapper `efn_insight.*` DEFINER (kolom = tabel tanpa `_version`). Grant YoY/G52/G53 tidak disentuh |
| `infra/clickhouse/access/insight-esb-policy.sh` | +3 view kelas PT |
| `infra/clickhouse/clickhouse-init.sh` | `ESB_POLICY_EXPECTED=20`; pagar fail-closed MergeTree+encrypted mencakup 3 tabel margin (`MARGIN_TABLES`) |
| `infra/compose/docker-compose.esb.yml` | env `ESB_MARGIN_*` diteruskan (default kosong = mati); dibutuhkan `test_contract.py` |
| `services/esb-extract/efn_esb/margin.py` (baru) | algoritme §31.2 langkah 1–6 (logika murni) + I/O (`refresh_margin`, kunci `RefreshLock` agregat, catatan `aggregate_refresh` `margin_<brand>`) |
| `services/esb-extract/efn_esb/config.py` | `MarginSettings` (`ESB_MARGIN_BRANDS/MONTHS/WEEKDAY/HOUR_WIB`, validasi rentang + brand dikenal) + `validate_margin` (brand wajib di `ESB_BRANDS`, `purchase_line` wajib di `ESB_ENTITIES`) |
| `services/esb-extract/efn_esb/runner.py` | `MarginSchedule` (jadwal mingguan: snapshot `bom_detail` → hitung; satu percobaan per brand per hari WIB; tidak pernah melempar) |
| `services/esb-extract/efn_esb/__main__.py` | subcommand `refresh-margin [--brand B]` (tanpa ESB); loop `run` memanggil jadwal setelah `refresh-aggregates`; fail-fast konfigurasi margin saat start |
| `services/esb-extract/tests/test_margin.py` (baru) | algoritme (sintetis: harga berbobot, bergulir 4 bulan, indeks dasar, satuan dominan, kelompok fallback, BOM rekursif + siklus + batas kedalaman, kanonisasi ` T`/`KIOSK - `, Laspeyres + skala COGS), refresh palsu (versi, parameter, busy, error per prasyarat, galat CH tanpa pesan, lock_lost), jadwal, CLI, config, DDL/grant/policy/pagar init |
| `tests/test_insight_access.py`, `tests/test_insight_fnb_access.py`, `tests/test_ddl.py` | +3 view, jumlah policy 20, DDL 24 ikut sumber badan view, grant esb_writer termasuk tabel 24 |
| `services/esb-extract/README.md`, `docs/runbooks/esb-extract.md` §6b | env, jadwal, refresh manual, arti error, verifikasi, pemulihan |

### Asumsi skema / data
- `dim_esb_bom(_line)` = snapshot: versi terakhir per brand = `_version = max(_version)` (pola `efn_mig.source.dim`).
- `fact_esb_purchase_line` dibaca `FINAL` + `(brand, purchase_date, _version) IN checkpoint purchase_line '*'` (pola §25.4).
  Batal = `status_name` mengandung `cancel`/`batal`/`void` (tanpa beda huruf) — **nama status ESB untuk batal belum
  diverifikasi**. IDR = `currency` ∈ {'', IDR, Rp} (pola `efn_mig.mapping.IDR_CODES`). Baris tanpa `product_code` dibuang.
- Nama PO: kurung berisi `product_code` dibuang untuk tampilan (`Nama (KODE)` → `Nama`).
- Menu: BOM `bom_type_name` = `Menu` (tanpa beda huruf) dicocokkan lewat **nama BOM dan nama produk hasil** setelah
  kanonisasi; nama ganda → `bom_id` terbesar. Kecocokan `bom_name` ↔ `menu_name` penjualan belum dicek di data nyata
  (runbook §6b punya query cakupan COGS + menu terlaris tanpa BOM).
- Sub-BOM: `product_code` komponen = `product_code` BOM non-Menu **aktif** (ganda → `bom_id` terbesar).

### Keputusan desain yang menyimpang / menafsirkan kontrak (perlu konfirmasi lead)
1. **`company_code` = satu PT per brand** (PT dengan cabang aktif terbanyak di `dim_esb_branch`, seri kode terkecil).
   Kontrak hanya menulis "PT brand (dim_esb_branch)". Menduplikasi baris per PT (pola `v_esb_product_list`) akan
   menggandakan angka untuk pengguna yang melihat beberapa PT. Konsekuensi: brand multi-PT (Bacha BCF/BCR) hanya
   terlihat oleh PT mayoritas. PB (L-259) satu PT → tidak berpengaruh.
2. **Jendela = N bulan kalender penuh terakhir** (bulan berjalan tidak ikut); bulan pertama = bulan dasar. PO dimuat
   mulai 3 bulan sebelum jendela agar `price_4m` bulan dasar penuh 4 bulan.
3. Baris `ing_price_monthly` hanya untuk bulan yang punya `price_4m`; bulan tanpa penerimaan → `receipt_qty`/`value`/
   `price` = 0 (kolom kontrak tidak Nullable), `price_4m`/`idx` tetap terisi. Bahan tanpa harga 4 bulan di jendela tidak
   punya baris (= daun tanpa harga PO).
4. Daun tanpa harga PO masuk kelompok **`Tanpa harga PO`** di `ing_group_monthly` (idx 1 → kontribusi 0) agar Σ
   kelompok = nilai keranjang total; frontend boleh menyembunyikannya.
5. Baris BOM bernilai ≤ 0 (`qty × last_hpp`) diabaikan; menu dengan total bobot 0 = tanpa BOM (`bom_matched 0`).
   "Kedalaman maks 8" = maksimum 8 BOM dalam satu rantai (menu + 7 sub-BOM).
6. Skala Laspeyres = rata-rata COGS bulanan menu cocok / nilai keranjang bulan dasar; bila salah satunya ≤ 0 → skala 1
   (Rupiah HPP ESB). COGS hanya dari menu cocok dengan total qty jendela > 0.
7. Jadwal: hari WIB = weekday **dan jam ≥ `ESB_MARGIN_HOUR_WIB` (sepanjang sisa hari itu)**, bukan jam tepat (interval
   loop bisa > 1 jam); maksimum satu percobaan per brand per hari. Snapshot `bom_detail` tidak `ok` → **tidak dihitung**
   (`error=snapshot_<status>`), bukan hitung dengan resep lama. Refresh pertama setelah deploy = manual (runbook).
8. CLI `refresh-margin` **tidak** melakukan snapshot (hanya ClickHouse, jalan dengan `ESB_ENABLED=false`, pola
   `refresh-aggregates`); snapshot manual = `snapshot --entity bom_detail`.
9. `ESB_MARGIN_MONTHS` minimum 2 (kontrak hanya menyebut maks 24).
10. Kunci = kunci heartbeat agregat yang sama: refresh margin dan `refresh-aggregates` saling menunggu (`busy`).
11. Pagar fail-closed `clickhouse-init.sh` diperluas ke 3 tabel margin (tidak diminta eksplisit; pola SEC-AG-01).

### Belum terverifikasi / tindak lanjut
- **Semua perintah verifikasi** (ruff, format, pytest) dan uji DDL 24 di ClickHouse 24.8 — tidak bisa dijalankan sesi ini.
- SQL ClickHouse yang belum pernah dieksekusi: `argMaxIf`, `multiSearchAnyCaseInsensitive` dengan parameter
  `Array(String)`, `has(param, upper(toString(currency)))`, `toDecimal128(...) * toDecimal128(...)`, TTL
  `toDateTime(_loaded_at)` pada MergeTree encrypted, view `(t.brand, t._version) IN (SELECT brand, max(_version) …)`.
- Data nyata efn-prod: cakupan BOM↔menu, rasio biaya/unit vs COGS ESB (median 1,00 di analisis lokal), volume baris.
- **Tanggal refresh terakhir untuk halaman** (§31.5 "aggregate_refresh"): `efn_esb.aggregate_refresh` tidak terbaca
  insight_reader dan kontrak view tidak memuat `_version`/`_loaded_at`. Perlu keputusan lead: (a) tambah kolom
  `refreshed_at` di view/wrapper (ubah kontrak §31.3), atau (b) wrapper kecil baru `efn_insight.v_esb_margin_refresh`
  (brand, company_code, refreshed_at) kelas PT (policy 21). Belum dibangun.
- Harness perilaku row policy (`tests/clickhouse/g52_row_policy_harness.sh`) belum diperluas ke 3 view baru (predikat
  PT sama persis dengan view PT lain; diuji statis).

## Gelombang 65 paket W-A — esb-extract: `purchase_line` + `stock_opening` (24 Nov 2026)

Branch `feat/esb-migration-purchase-stock` (worktree `efn-platform-esbmig`). Kontrak `docs/04` §27.13.1–27.13.3,
§27.13.9 (L-250..L-258); ADR-042 §0b (Proposed). **Tidak ada yang dijalankan** (tanpa shell/Python/ClickHouse); lead
menjalankan pytest + ruff di container.

### File
- Baru: `services/esb-extract/efn_esb/extract_g65.py`, `efn_esb/transform_g65.py`, `tests/test_g65.py`,
  `infra/clickhouse/init/23-g65-esb-purchase-stock.sql`.
- Diubah: `efn_esb/config.py` (`purchase_line` di ENTITIES/DAILY, bukan default; `SNAPSHOT_BRANCH_SCOPED`),
  `normalize.py` (whitelist `PURCHASE_RECAP_FIELDS`, `PURCHASE_HEADER_FIELDS`, `STOCK_MOVEMENT_FIELDS`; `name_norm`),
  `transform.py` (`DIM_SUPPLIER_COLUMNS` + `supplier_id`), `extract.py` (`BrandRuntime.suppliers`,
  `master_forced_for_ids`, `extractor_for` G65), `planning.py` (`snapshot_tasks`), `runner.py` (`ALL_TABLES` + G65,
  `prepare_brand(need_supplier_ids)`, `_absorb` master → supplier, snapshot per cabang), `store.py` (`load_suppliers`),
  `__main__.py` (`snapshot --entity stock_opening`, `--date` wajib), `infra/clickhouse/access/esb-access.sql`
  (esb_writer), `infra/clickhouse/access/mig-access.sql` (mig_rw: role ini didefinisikan di file itu, bukan di
  esb-access.sql), README, runbook §5a, test lama (`test_ddl.py`, `test_ddl_g43.py`, `test_transform.py`,
  `test_config.py`, `test_runner.py` FakeStore.load_suppliers). `clickhouse-init.sh` tidak diubah (glob `init/*.sql`).

### Antarmuka ke W-B (tabel final)
- `efn_dwh.fact_esb_purchase_line`: `brand, company_code, purchase_date, purchase_num, line_no, required_date,
  branch_id, branch_name, supplier_id, supplier_code, supplier_name, status_id, status_name, currency, product_code,
  product_name, category_name, sub_category_name, uom, qty, receipt_qty, pricelist_price, price, discount, vat_value,
  total, map_source, _version, _loaded_at` — persis §27.13.2. Checkpoint: entitas `purchase_line`, scope `*`, day =
  purchase_date.
- `efn_dwh.fact_esb_stock_opening`: `brand, company_code, opening_date, branch_id, branch_code, location_name,
  product_id, product_detail_id, product_code, product_name, uom, value_per_unit, qty_balance, amount_balance,
  _version, _loaded_at`. Checkpoint: entitas `stock_opening`, scope `<branch_id>`, day = opening_date.
- `efn_dwh.dim_esb_supplier.supplier_id UInt64 DEFAULT 0` (ALTER).
- `efn_dwh.v_esb_g65_quality` (kolom = `v_esb_data_quality`): `purchase_unmapped`, `stock_opening_missing_branch`.

### Asumsi / keputusan
- **Penyimpangan disengaja dari §27.13.2**: dua cek kualitas TIDAK ditambahkan ke `v_esb_data_quality` tetapi ke view
  baru `v_esb_g65_quality` (hanya mig_rw). `v_esb_data_quality` adalah INVOKER dan dibaca `metabase_reader` +
  `efn_insight_definer`; UNION ke tabel G65 akan membuat view itu gagal untuk mereka (L-258 melarang grant). Perlu
  diserap architect ke §27.13.2.
- `map_source` baris = pemetaan terlemah (cabang, supplier): header < name < none. Header ada tetapi `supplierID` tak
  ada di dim → kode lewat nama (`'name'`), `supplier_id` tetap dari header.
- `purchase_date` = hari slice (bukan `purchaseDate` respons). `status_id`/`currency` dari header (0/'' tanpa header);
  `currency` = `currencyID` apa adanya (kode atau angka [B]). Semua status disimpan; filter izin = W-B.
- Baris recap tanpa `purchaseNum` dibuang; `productCode` kosong disimpan apa adanya.
- `stock_opening`: kunci ganda (lokasi, produk, detail, UOM) dijumlah, `value_per_unit` baris pertama; saldo negatif
  disimpan (L-257: W-B yang memutuskan/melaporkan). Satu GET per halaman, snapshot berjalan 1 worker (token bucket 2/dtk).
- `supplier_id` kosong di store saat `purchase_line` aktif → `master` dipaksa sekali per proses (bukan per siklus).

### Belum terverifikasi
Semua test/ruff (belum dijalankan); DDL & view di ClickHouse 24.8 (ALTER ADD COLUMN, LEFT JOIN `c.hit = 0` tanpa
`join_use_nulls`); GET nyata via extractor; nama field `supplierID` di `/supplier`; batas `limit` header PO (100);
kesamaan `branchCode` stock-movement dengan `dim_esb_branch.branch_code`; apakah `dateFrom/dateTo` recap & header
sama-sama memfilter `purchaseDate`; arti `discount`/`total`/`amountBalance`/`unitToShow`; tipe `currencyID`.

## Gelombang 52 — sisi ClickHouse persona F&B: 4 view Toko, sig3 + kelas PT/BR, 14 policy (22 Nov 2026)

Branch `feat/insight-nav-dual-menu` (worktree `efn-platform-nav`). Kontrak `docs/04` §28.15.6–§28.15.8 (v21.1,
L-243/L-244); ADR-040, ADR-041 §3.6 (Proposed). **Tidak ada yang dijalankan** (tanpa shell/Python/ClickHouse).

### File
| File | Isi |
|---|---|
| `infra/clickhouse/init/21-g52-insight-fnb.sql` (baru) | `efn_dwh.v_esb_store_receipt_daily` (INVOKER): `v_esb_movement_daily WHERE kind = 'delivery'` INNER JOIN `dim_esb_branch FINAL` (`company_code != ''`, `is_store = 1`) pada `(brand, dest_branch_id)`; `company_code` = PT tujuan, `branch_id` = cabang tujuan |
| `infra/clickhouse/access/insight-access.sql` | 4 wrapper `efn_insight.v_esb_store_{sales,menu,payment,receipt}_daily` (DEFINER, kolom persis §28.15.8; payment `WHERE branch_id != 0`); grant definer `efn_dwh.v_esb_store_receipt_daily` |
| `infra/clickhouse/access/insight-esb-policy.sh` (baru) | manifest 14 view (9 PT + 5 BR) + `esb_policy_predicate KEY PT\|BR` (predikat persis §28.15.7) + pemasangan (`--log_queries=0`, cek jumlah = `ESB_POLICY_EXPECTED`); `ESB_POLICY_DEFINE_ONLY=1` untuk harness |
| `infra/clickhouse/clickhouse-init.sh` | loop policy v1 + `INSIGHT_SIG` diganti `ESB_POLICY_EXPECTED=14` + source file di atas; cek fail-closed "setiap view efn_insight punya policy" tetap |
| `infra/clickhouse/access/insight-sm-policy.sh` | komentar saja (urutan source) |
| `services/esb-extract/tests/test_insight_access.py` | 14 view, regex wrapper menerima satu `WHERE`, `DDL21`, test policy membaca manifest |
| `services/esb-extract/tests/test_insight_fnb_access.py` (baru) | kolom/sumber pasangan/tanpa cogs-mdr, receipt (E8 statis), klausa predikat persis, kelas, cermin Python predikat + E1–E7, E9 |
| `services/esb-extract/tests/clickhouse/g52_row_policy_harness.sh` (baru) | uji perilaku ClickHouse 24.8 E1–E9 + kontrol positif, predikat & badan view diambil dari file produksi |

### Keputusan / asumsi
- Hanya `v_esb_store_receipt_daily` punya view `efn_dwh` baru (sesuai §28.15.8). Tiga view toko lain = wrapper atas
  sumber pasangan (`v_esb_sales_branch_daily`, `v_esb_sales_daily`, `v_esb_payment_daily`); test menegaskan sumber sama
  agar ikut dialihkan saat `20-esb-aggregates.sql` merge (konflik merge `insight-access.sql` diselesaikan dengan
  mengalihkan wrapper toko ke tabel agregat yang sama). Tidak bergantung pada file 20.
- Helper `fexec_*`/`fmkt_*` = named query insight-web (§28.15.9), bukan view; tidak ada view ClickHouse tambahan.
- Tidak ada fallback `SQL_efn_sig` v1 untuk view ESB (kontrak: insight berhenti mengirimnya). Kompatibilitas rollout
  eit2 = sesi `eis1/eis2` dikirim insight-web dengan `SQL_efn_brs=''` → kelas PT tetap jalan. insight-web lama gagal
  tertutup → `clickhouse-init` + insight-web satu rilis (§28.15.10).
- Konstanta 14 ada di `clickhouse-init.sh` (`ESB_POLICY_EXPECTED`) dan `test_insight_access.py`; manifest di file `.sh`
  terpisah (pola `insight-sm-policy.sh`) supaya harness memakai predikat produksi.

### Belum terverifikasi
- Semua pytest + `ruff check`/`ruff format --check` (format ditulis manual gaya ruff) belum dijalankan.
- Harness `g52_row_policy_harness.sh` belum pernah dijalankan (perintah contoh di header; butuh `clickhouse-init`).
- DDL `21` + wrapper belum dieksekusi di ClickHouse 24.8 (resolusi alias `branch_id` vs `b.branch_id` di JOIN, row policy
  di atas view dengan `WHERE`).
- Performa: `v_esb_store_menu_daily` di atas `v_esb_sales_daily` yang lambat (sebelum agregat) dan
  `v_esb_store_receipt_daily` di atas `v_esb_movement_daily` (pushdown `kind = 'delivery'` belum diukur).
- Path filter CI job Python tidak mencakup `infra/clickhouse/**`: perubahan hanya di infra tidak memicu test ini.

## Tabel agregat harian Insight — revisi keamanan SEC-AG-01..05 (22 Nov 2026)

Review `docs/reviews/SECURITY_REVIEW_2026-11-22_esb-aggregates.md` + keputusan desain lead (SEC-AG-01 dibuktikan lead di
24.8.14: esb_writer bisa `CREATE ... SETTINGS storage_policy='default'`, `ENGINE=Memory/Log`). Desain EXCHANGE di bawah
**diganti**. Tanpa shell: tidak ada yang dijalankan.

- **SEC-AG-01:** esb_writer tanpa CREATE/DROP TABLE (REVOKE eksplisit pada 6 nama, karena grant lama tidak hilang
  sendiri). Agg (CTAS `WHERE 0` dari view, `SETTINGS storage_policy = 'encrypted'`) dan build permanen
  (`CREATE TABLE IF NOT EXISTS <agg>_build AS <agg>`, struktur identik = syarat REPLACE PARTITION) dibuat admin di
  `20-esb-aggregates.sql`. Grant final: build `SELECT, INSERT, TRUNCATE`; agg `SELECT, INSERT, ALTER DELETE`
  (REPLACE PARTITION = SELECT sumber + ALTER DELETE|INSERT tujuan; DROP PARTITION = ALTER DELETE —
  `InterpreterAlterQuery::getRequiredAccessForCommand`, dari ingatan kode sumber, belum dicek live). `allow_ddl = 1`
  tetap (TRUNCATE/ALTER termasuk kelompok DDL di `ContextAccess`). `clickhouse-init.sh`: (a) setelah init/*.sql, agg/build
  yang kolomnya (posisi, nama, tipe) != view, atau build bukan MergeTree/encrypted → DROP + jalankan ulang 20; (b) di akhir,
  6 tabel wajib ada + MergeTree + encrypted, jika tidak `REVOKE ALL` esb_writer pada tabel itu dan exit 1.
- **SEC-AG-02:** kunci `efn_esb.aggregate_refresh_lock` (run_id, started_at/heartbeat_at DEFAULT now64 server, released).
  Acquire = tulis baris → jeda 2 dtk → pemegang = run belum dilepas dengan heartbeat < 15 menit, urut (min started_at,
  run_id). Cek ulang sebelum setiap TRUNCATE/REPLACE/DROP PARTITION; heartbeat tiap bulan; release di `finally`. Satu
  kunci untuk ketiga agregat; bukan pemegang → semua `busy` tanpa query ke tabel; kunci hilang → `lock_lost`, sisanya
  `busy`. Sisa risiko: baris proses lain yang lebih awal (jam server) tetapi baru terlihat setelah jeda 2 dtk.
- **SEC-AG-03:** per bulan: build 0 padahal agg bulan itu > 0, atau < 50% → bulan dilewati; partisi agg yang tidak ada
  di rentang sumber juga dilewati (bukan DROP); agregat `error` `shrink <yyyymm,...>`. `--allow-shrink` hanya CLI manual.
  Rentang sumber kosong tidak lagi mengosongkan agg.
- **SEC-AG-04:** `CH_DATABASE`/`CH_RAW_DATABASE` divalidasi `^[a-z_][a-z0-9_]{0,63}$` (config.py, test negatif).
- **SEC-AG-05:** runbook §6a "Pemulihan bila tabel agg hilang / kosong / rusak".
- **menu_mix:** `efn_dwh.v_esb_menu_mix_agg` (badan identik dengan `v_esb_menu_mix`, sumber `agg_esb_sales_daily`; diuji
  kesamaan badan) → wrapper `efn_insight.v_esb_menu_mix` membacanya; keluaran sama. Definer kehilangan grant yang tidak
  lagi dirujuk: `v_esb_sales_daily`, `v_esb_menu_mix`, `dim_esb_menu`, `fact_esb_sales_line` (+ `v_esb_sales_branch_daily`).

File revisi: `efn_esb/{aggregates,runner,__main__,store,config}.py`, `tests/{test_aggregates,test_ddl,
test_insight_access,test_config}.py`, `infra/clickhouse/init/20-esb-aggregates.sql`,
`infra/clickhouse/access/{esb-access,insight-access}.sql`, `infra/clickhouse/clickhouse-init.sh`, README, runbook §6a,
docs/04 §25.7/§26.7.

Belum diverifikasi live: hak tepat REPLACE/DROP PARTITION dan TRUNCATE dengan grant tabel; esb_writer melihat
`system.parts`; `CREATE TABLE ... SETTINGS storage_policy ... AS SELECT ... WHERE 0` dan `CREATE TABLE b AS a` lolos di
24.8; query perbandingan kolom `system.columns` di init (tipe view vs tabel identik untuk agg yang sudah ada di prod —
bila tidak, init akan membuat ulang agg kosong sekali, lalu refresh); tabel build sisa uji lead (policy default/Memory/Log)
dihapus otomatis oleh langkah (a).

## Tabel agregat harian Insight (22 Nov 2026) — desain awal (EXCHANGE, diganti revisi di atas)

Branch `feat/esb-daily-aggregates`. Keputusan pemilik proyek 22 Nov (`docs/design/insight-web.md`): perbaikan akar galat
159/241 Insight setelah data ESB 1 tahun = tabel agregat harian. ADR-040/041 (Proposed), K25-02. Tanpa shell:
**tidak ada yang dijalankan** (pytest/ruff/ClickHouse dijalankan lead di container).

**Desain (rekomendasi lead diterima, dengan dua penyesuaian):**
- `efn_dwh.agg_esb_sales_branch_daily`, `agg_esb_sales_daily`, `agg_esb_flow_daily`: MergeTree, PARTITION BY bulan,
  ORDER BY `(company_code, brand, <tanggal>, branch_id[, menu_code|product_code])` (company_code dulu = kunci row policy
  + filter PT, sama dengan urutan tabel fakta). Kolom nama + tipe **diambil dari view** lewat
  `CREATE TABLE ... AS SELECT <kolom> FROM <view> WHERE 0` (bukan tipe ditulis tangan, jadi tidak bisa menyimpang).
- Penyesuaian 1: build dibuat dari **definisi view** (`table_definition()`), bukan `CREATE TABLE build AS <agg>`. Alasan:
  perubahan kolom view ikut otomatis lewat EXCHANGE (tanpa ALTER/DROP manual), dan tidak perlu hak `SHOW COLUMNS` pada
  tabel agg untuk `CREATE ... AS <tabel>`. Teks definisi di init diuji sama persis dengan renderer Python.
- Penyesuaian 2: nama build TETAP (`<agg>_build`) dipakai juga sebagai **lock**: build ada + umur < 2 jam = refresh lain
  berjalan (`busy`, tidak menyentuh apa pun); > 2 jam = sisa proses mati, dihapus. `CREATE` tanpa IF NOT EXISTS sehingga
  balapan dimenangkan satu proses.
- Urutan refresh: rentang hari dari `extract_checkpoint` (status done, entitas view) → cek umur build → CREATE build →
  INSERT per bulan kalender (parameter `{start:Date}`/`{end:Date}`, `max_execution_time` 280 < timeout klien 300) →
  `count()` → `EXCHANGE TABLES` → `DROP ... SYNC`. Gagal sebelum/saat EXCHANGE: build milik proses ini dihapus, tabel
  lama tetap dibaca Insight. Hasil per agregat → `efn_esb.aggregate_refresh` (ok/busy/error, error = kelas exception +
  kode ClickHouse saja).
- Pemicu otomatis: akhir siklus `run` (satu refresh setelah semua brand) dan akhir `backfill` bila ada slice
  `sales/fin_sold/production/delivery/transfer/master` ditulis; gagal = log, ditandai ulang untuk siklus berikutnya;
  exit code ekstraksi tidak berubah. `refresh-aggregates` jalan tanpa ESB (juga `ESB_ENABLED=false`, untuk data DR).
- Row policy: policy saat ini **berbasis view `efn_insight.*`** (loop `clickhouse-init.sh` + pagar fail-closed
  `system.row_policies`), bukan tabel. Wrapper tetap view `efn_insight` dengan nama sama → policy dan pagar berlaku tanpa
  perubahan. Tidak memasang policy di tabel agg: `insight_reader` tidak punya hak `efn_dwh` (REVOKE ALL), dan policy di
  tabel akan memengaruhi user lain (esb_writer membaca untuk count/EXCHANGE). Definer: `SELECT` 3 tabel agg; `SELECT`
  `v_esb_sales_branch_daily` dicabut (tidak dirujuk lagi).
- Least privilege `esb_writer`: `SELECT, INSERT, CREATE TABLE, DROP TABLE` hanya pada 3 agg + 3 build (nama eksplisit),
  `SELECT` 3 view sumber, `SELECT, INSERT` `aggregate_refresh`. Konsekuensi: `allow_ddl = 1 CONST` di
  `esb_writer_profile` (allow_ddl=0 menolak semua DDL termasuk yang di-grant). Tanpa ALTER/TRUNCATE/OPTIMIZE/wildcard.

**File:**
- baru `services/esb-extract/efn_esb/aggregates.py`, `services/esb-extract/tests/test_aggregates.py`,
  `infra/clickhouse/init/20-esb-aggregates.sql`
- ubah `services/esb-extract/efn_esb/{runner.py,__main__.py,store.py}`, `infra/clickhouse/access/esb-access.sql`,
  `infra/clickhouse/access/insight-access.sql`, `services/esb-extract/tests/{test_ddl.py,test_insight_access.py}`,
  `services/esb-extract/README.md`, `docs/runbooks/esb-extract.md` (§6a), `docs/04_SERVICE_CONTRACTS.md` (§25.7, §26.7)

**Asumsi skema:** view sumber tidak menghasilkan kolom Nullable pada kunci ORDER BY (LEFT JOIN dengan
`join_use_nulls=0` default); database `efn_dwh` Atomic (EXCHANGE sudah dipakai `clickhouse-init.sh` untuk
loyalty_events); view `efn_dwh.v_esb_*` SQL SECURITY INVOKER (default 24.8), jadi `esb_writer` butuh SELECT tabel
dasarnya (sudah ada).

**Belum diverifikasi (ClickHouse 24.8 nyata):** grant tingkat tabel cukup untuk `CREATE ... AS SELECT` dan
`EXCHANGE TABLES` (InterpreterRename: SELECT+DROP TABLE dan CREATE TABLE+INSERT pada kedua tabel), grant pada nama
tabel yang belum ada diterima, `table_engines_require_grant` = false (default), `esb_writer` melihat build di
`system.tables`; filter tanggal per bulan terdorong ke partisi fakta di balik FINAL (EXPLAIN di runbook §6a); durasi &
memori per bulan untuk PB 1 tahun; `efn_insight.v_esb_menu_mix` masih di atas view lambat (tidak dalam lingkup).

## Review Insight — rekonsiliasi alur CK & cakupan data (21 Nov 2026)

Branch `fix/insight-review`. ADR-040 & ADR-041 (Proposed), K25-02. Tidak ada Bash: **tidak ada yang dijalankan**
(pytest, ruff, ClickHouse) — lead menerapkan dan menguji.

**File**
- baru `infra/clickhouse/init/19-insight-review.sql`: `efn_dwh.v_esb_movement_daily`, `efn_dwh.v_esb_coverage`.
- `infra/clickhouse/access/insight-access.sql`: grant definer 2 view + `efn_insight.v_esb_movement_daily`,
  `efn_insight.v_esb_coverage` (DEFINER `efn_insight_definer`, SQL SECURITY DEFINER, kolom eksplisit).
- `infra/clickhouse/clickhouse-init.sh`: 2 view masuk loop row policy `efn_insight_scope` (tanpa ini pagar fail-closed
  mencabut grant `efn_insight.*` dan init gagal).
- test: `services/esb-extract/tests/test_insight_access.py` (kontrak kolom persis, aturan item_group + negatif prefix
  'N', filter checkpoint, uom di kunci agregasi, coverage fail-closed, grant = penutupan rekursif, policy loop),
  `services/esb-extract/tests/test_ddl.py` (19 hanya view; flow tetap FIN-only).
- docs: `docs/design/insight-web-queries.md` §2 (+§3.5 catatan), `docs/04_SERVICE_CONTRACTS.md` §26.7.

**Keputusan/asumsi**
- `company_code` movement = PT cabang **asal** (tanpa fallback sejak SEC-IR-02: cabang tak dikenal -> `''`, ditolak row policy), satu baris per pergerakan. Tidak diduplikasi
  ke PT tujuan (jumlah benar untuk user multi-PT). Akibat: user yang hanya ber-scope PT tujuan (mis. toko BCR menerima
  dari CK BCF, bila ada) tidak melihat kiriman itu di view ini; sisi terima tetap ada di `v_esb_flow_daily`.
- `item_group`: is_fin baris fakta dulu, lalu prefix kode ATAU kategori ESB (WIP → wip; RMT/IDF/'Inventory - Food &
  Beverage'/'Import Ingredient' → raw; PAM/'Packaging Material' → packaging; SU*/UNI/'Supplies'/'Uniform' → supplies).
  "N" pada catatan Packaging ditafsirkan sebagai **satuan**, bukan prefix (prefix 'N' akan menangkap kode lain).
- Production tidak memakai alias `NAME:` (kode selalu kode ESB di production/delivery), jadi tanpa join alias.
- Coverage: entitas `'*'` diperluas ke semua PT brand (pola `v_esb_data_quality_pt`), entitas per toko (sales,
  fin_sold) ke PT cabang; `status = 'done'` (records.py menulis 'done'); `days` = hari unik dengan ≥1 slice selesai.

**Belum terverifikasi**
- DDL belum pernah dijalankan di ClickHouse (sintaks `CAST(x, 'String')`, tipe keluaran LowCardinality vs String,
  performa join dim × fakta production ±ratusan ribu baris).
- pytest/ruff belum dijalankan.
- Apakah ada kiriman lintas PT nyata (Bacha BCF ↔ BCR) — menentukan apakah keputusan PT asal cukup.
- Apakah semua cabang `scope_key` sales/fin_sold ada di `dim_esb_branch` (yang tidak ada tidak dihitung di coverage).

### Lanjutan: kategori menu kosong 94% (21 Nov 2026)

Akar (diverifikasi lead di efn-prod): 664 dari 740 `menu_code` terjual tidak ada di `dim_esb_menu` karena
`/menu/browse` tidak memuatnya; baris `report/sales-menu-cogs` membawa `menuCategory`/`menuCategoryDetail`.

**File**
- `services/esb-extract/efn_esb/transform.py`: `sales_menu_version(day)`, `transform_sales_menus(rows, brand,
  company_code, day)` (murni; filter salesType Sales/kosong + `menuCategory` terisi, lalu `transform_menus`).
- `services/esb-extract/efn_esb/extract.py`: `extract_sales` menambah tabel `dim_esb_menu` ke `SliceResult` (PT default
  brand = sama dengan baris master).
- Test: `tests/test_transform.py` (filter kategori kosong/spasi/tanpa kunci, salesType non-Sales, tanpa kode; urutan
  versi), `tests/test_runner.py` (slice sales Bacha: dim ber-PT BCF, versi awal hari, urutan tulis sebelum checkpoint).
- `services/esb-extract/README.md` (bagian Alur & idempotensi).

**Pilihan versi.** `dim_esb_menu` = `ReplacingMergeTree(_version)`, tidak bisa "isi hanya bila belum ada". Baris sales
memakai `_version` = 00:00 UTC hari penjualan: run master setelah hari itu selalu menang (browse sumber utama), hari
penjualan lebih baru menang atas yang lama, backfill mundur tidak menimpa. Jendela sementara: sales hari D yang ditarik
sebelum run master pertama setelah D 00:00 UTC bisa menang atas browse untuk kode yang ada di keduanya sampai run master
berikutnya (isi sales = kategori terisi, jadi tidak pernah mengosongkan). Kode yang ada di browse dengan kategori kosong
tetap kosong (browse menang) — tidak diubah.
Backfill sekali jalan (bukan file init) = SQL di laporan ke lead; `_version` = `toDateTime64(hari terakhir, 3, 'UTC')`
sehingga konsisten dengan jalur kode.

**Belum terverifikasi:** pytest/ruff belum dijalankan; SQL backfill belum dijalankan (memori ARRAY JOIN atas ±11,8 rb
payload — jalankan per brand bila perlu); asumsi `menuCode` di payload bertipe string JSON (angka -> '' di
`JSONExtractString`, baris itu terlewat).

## Gelombang 51 — SMD end-to-end: view Insight B2/B4, histori stok B6, histori forecast A6 (21 Nov 2026)

Kontrak: `docs/design/G51_SMD_E2E_LEAD_SPEC.md` + keputusan lead G51 (is_reconstructed & versi aman, jalur A6
`--as-of/--out` + loader odoo-shell, view ke-4 `v_sm_lot_stock`, policy 33). ADR-003/ADR-041/ADR-046 (Proposed).
**Tidak ada yang dijalankan** (tanpa shell): pytest, ruff, ClickHouse 24.8, harness, worker, loader belum pernah
dieksekusi.

### File
- CH: `infra/clickhouse/init/17-g47-insight-sm.sql` (ALTER `dim_product.shelf_life_days UInt16 DEFAULT 0`,
  `v_sm_product_list` + kolom akhir), **baru** `infra/clickhouse/init/18-g51-insight-sm.sql` (ALTER
  `agg_sm_stock_daily.is_reconstructed UInt8 DEFAULT 0`; view `efn_dwh.v_sm_payment_daily`, `v_sm_product_sales_daily`,
  `v_sm_forecast_daily`, `v_sm_lot_stock`), `infra/clickhouse/access/insight-sm-access.sql` (grant definer + 4 view
  DEFINER), `infra/clickhouse/access/insight-sm-policy.sh` (4 baris, kunci 33).
- ETL: `services/etl/efn_etl/pos.py` (`shelf_life_days`), **baru** `services/etl/efn_etl/sm_stock_hist.py`,
  `sm_cycle.py` (job `sm_stock_reconstruct`), `config.py` (`ETL_SM_STOCK_RECONSTRUCT_DAYS` 0..400, butuh SM),
  `__main__.py`, `sql/odoo-readonly-grants.sql` (blok G51: `product_template.expiration_time`), `README.md`.
- Compose: `infra/compose/docker-compose.yml` (etl: `ETL_SM_STOCK_RECONSTRUCT_DAYS:-0`, `ETL_FORECAST_ACCURACY_WEEKS:-2`,
  `ETL_FORECAST_ACCURACY_INTERVAL_SECONDS:-86400` — dua terakhir sebelumnya tidak diteruskan), `infra/compose/.env.example`.
- Worker: **baru** `services/forecast-worker/efn_forecast/asof.py`, `__main__.py` (`--as-of`, `--out`), `runner.py`
  (`run_batch(run_id=None)`), `README.md`. Loader **baru** `scripts/dev/load_forecast_asof.py`. Runbook
  `docs/runbooks/demo-supermarket-seed.md` §8 "A6 forecast history" (hanya bagian itu).
- Test: `services/etl/tests/test_insight_sm_access.py`, `test_g47_cross_service.py` (29→33), `test_sm_ddl.py`,
  **baru** `test_sm_stock_hist.py`, harness `tests/clickhouse/g47_row_policy_harness.sh` (tabel `payment`, persona dari
  manifest produksi), **baru** `services/forecast-worker/tests/test_asof.py`.

### Kolom view efn_insight (urutan = implementasi)
- `v_sm_payment_daily` (W; exec, store): day Date, company_id UInt32, warehouse_id UInt32, outlet_type String,
  method_name String, method_type String, is_refund UInt8, amount Float64 (Σ termasuk baris kembalian negatif = bersih),
  payments UInt64 (baris non-kembalian). Sumber fact_pos_payment FINAL ⋈ dim_outlet (pos.config.warehouse_id).
- `v_sm_product_sales_daily` (W; category, exec, store): day, company_id, warehouse_id, outlet_type, product_id UInt32,
  categ_id UInt32, qty_sold Float64, net_sales Float64 (tanpa PPN), net_sales_incl_tax Float64. Sumber
  v_sm_sales_product_daily (fact_sales_line `sales_fact_pos`), dijumlah ulang per warehouse.
- `v_sm_forecast_daily` (W; category, dc, exec, store): company_id, warehouse_id, outlet_type, product_id, categ_id,
  forecast_date Date, yhat/p10/p90 Float64, run_id UInt32, generated_at DateTime('UTC'). Run `ok` kind daily terbaru per
  PT **menurut generated_at** (run as-of tidak pernah menjadi terbaru). Seluruh horizon (28 hari); aplikasi memfilter 14.
- `v_sm_lot_stock` (W; category, exec, store): company_id, warehouse_id, outlet_type, product_id, categ_id, lot_id UInt32,
  lot_name String, expiration_date Date (WIB), days_left Int32 (vs hari ini WIB), quantity Float64, value_cost Float64.
  Snapshot selesai terakhir, lot_id ≠ 0, ber-expiration_date, qty > 0, warehouse SM.
- `v_sm_product_list` + `shelf_life_days UInt16` di akhir (0 bila `use_expiration_date` mati).

### Temuan / keputusan
- `fact_pos_payment` 0 baris SMD **bukan** karena filter PT: job `pos_payments` tidak memfilter company (hanya order
  `paid|done`); penyebabnya order histori seed tanpa `pos.payment` (diatasi A4). store_id→warehouse lewat `dim_outlet`.
- B6 versi: baris rekonstruksi `computed_at = 2000-01-01 UTC`, baris nyata `now64(6)` → nyata selalu menang di RMT apa pun
  urutannya; hari dengan `stock_snapshot_run` tidak ditulis; hari ini tidak pernah direkonstruksi. Diuji (simulasi RMT).
- A6: Odoo `_efn_validate_line` menolak tanggal < hari ini server, jadi worker tidak bisa mengirim run lampau lewat API;
  jalur = `--as-of` (hitung, RecordingOdoo, payload tervalidasi kontrak) → file → loader ORM (validator Odoo dipakai ulang
  relatif tanggal batch, `is_active=False`, tanpa `_efn_on_run_closed_ok`). run_id deterministik → idempoten.

### Perintah untuk lead
- Test: `docker run ... pytest services/etl/tests services/forecast-worker/tests` + `ruff check` (pola CI).
- Harness: `services/etl/tests/clickhouse/g47_row_policy_harness.sh` (cara di header file; uji G51 baru = payment).
- A6: runbook §8 (loop 8 Senin → `docker cp` → loader → ETL dengan `ETL_FORECAST_ACCURACY_WEEKS=8`,
  `ETL_FORECAST_ACCURACY_INTERVAL_SECONDS=900` sementara).
- B6 demo: `.env` `ETL_ENABLE_SM=true ETL_SM_STOCK_RECONSTRUCT_DAYS=90` (setelah seed A1/A2/A3/A7), recreate etl.
- Shelf life: seed A5 menaikkan write_date template → job `products` menyalin otomatis; DB lama: reset watermark
  `products` (README G47).

### Belum diverifikasi / asumsi
- Semua SQL ClickHouse belum dijalankan di 24.8 (alias kualifikasi `toFloat64(f.yhat) AS yhat` mengikuti pola yang sudah
  jalan di view G47/G44; agregat memakai alias berbeda). `toDateTime(DateTime64, 'UTC')` untuk generated_at.
- `stock_move.date` diasumsikan ber-index (Odoo `index=True`); tidak menambah index. Hari move = `date + 7 jam` (WIB tetap).
- Rekonstruksi: konversi UoM `quantity * mu.factor / pu.factor` (pola SQL_FILL_MOVES, belum diverifikasi arah faktor
  UoM 19.0); quant & move dibaca dalam satu statement per warehouse; volume (±6 gudang × 400 SKU × 90 hari ≈ 216 rb baris)
  ditulis sekali per hari WIB.
- Loader A6: field `opened_at/closed_at/state/is_active` ditulis lewat ORM sudo (readonly hanya UI); `_efn_scope`
  dipanggil dengan env shell; `metric_*` lain default. Ukuran file ±5–15 MB; izin tulis bind mount user 65534.
- `generated_at` as-of = Minggu 23:00 WIB (lead), cron default 23:30; akurasi memakai `toDate(generated_at WIB) < tanggal`.
- Freshness: mart pembayaran/forecast_daily/lot_stock tidak ditambah ke `v_sm_freshness` (di luar lingkup; bisa memakai
  mart `sales`/`forecast_accuracy`/`stock`).

## Gelombang 47 — EFN Insight supermarket per persona, sisi data (21 Nov 2026)

Kontrak: `docs/04` §28.13 v20.3 (L-186..L-203) + fit-gap `docs/fit-gap/G47_INSIGHT_SM.md` C-1..C-7 (menang untuk detail
Odoo). ADR-041 §3.3–§3.4 (Proposed), ADR-003. **Tanpa shell: pytest/ruff, DDL ClickHouse 24.8, psql grant, dan harness
row policy belum pernah dijalankan.**

### File
| File | Isi |
|---|---|
| `infra/clickhouse/init/17-g47-insight-sm.sql` | 14 tabel mart (engine/kunci/partisi/TTL = §28.13.8), ALTER `dim_product` (+`categ_id`, `categ_parent_path`), 6 view pembantu + 29 view semantik `efn_dwh.v_sm_*` |
| `infra/clickhouse/access/insight-sm-access.sql` | 29 view `efn_insight.v_sm_*` DEFINER kolom eksplisit + grant `efn_insight_definer` = penutupan rekursif |
| `infra/clickhouse/access/insight-sm-policy.sh` | fragmen row policy `efn_insight_scope` kelas W/C/F + allowlist persona per view (disourcing `clickhouse-init.sh`) |
| `infra/clickhouse/access/etl-sm-access.sql` | SELECT/INSERT `etl_writer` untuk tabel G47 (tanpa ALTER/DROP) |
| `infra/postgres/init/22-platform-g47.sql` | view `platform.v_campaign_stats` (agregat, tanpa member/payload/kode) + GRANT ke `efn_etl` |
| `docs/contracts/insight_sm_kpi_defaults.json` | file normatif 39 kunci KPI + 10 pasangan ambang |
| `services/etl/efn_etl/sm.py`, `sm_cycle.py`, `sm_kpi.py` | job, transform, logika murni (fill rate, kampanye, KPI, hari agregat), orkestrasi |
| `services/etl/efn_etl/{config,jobs,adapters,__main__,pos,waste}.py` | flag, wiring, `ClickHouseSmStore`, kolom kategori produk, fallback scrap (C-6) |
| `services/etl/sql/odoo-readonly-grants.sql` | blok "Gelombang 47" (bersyarat per modul) + `location_id`/`scrap_id`/`value` di blok G32 + view `efn_etl.purchase_partner` |
| `services/etl/tests/test_sm.py`, `test_sm_ddl.py`, `test_insight_sm_access.py`, `test_sm_kpi_parity.py` | test murni, DDL vs kontrak, akses + 8 negatif (cermin Python), parity KPI |
| `services/etl/tests/clickhouse/g47_row_policy_harness.sh` | uji perilaku row policy di ClickHouse 24.8 (manual) |
| `services/etl/README.md` | bagian G47 |

### Butuh tindakan lead / devsecops (file di luar milik saya)
1. **`infra/clickhouse/clickhouse-init.sh`**: tambahkan satu baris `. /init/access/insight-sm-policy.sh` tepat setelah
   loop policy `v_esb_*` dan SEBELUM `unset INSIGHT_KEY INSIGHT_SIG`. Tanpa baris ini init **gagal** (pemeriksaan
   fail-closed "setiap view efn_insight punya efn_insight_scope" mencabut grant). Fragmen memakai `$INSIGHT_KEY` dan `$CH`.
2. **`scripts/deploy.sh`**: masukkan `22-platform-g47.sql` ke daftar migrasi PG volume lama.
3. **Compose `etl`**: env baru `ETL_ENABLE_SM`, `ETL_SM_COMPANIES`, `ETL_ENABLE_SM_EXPIRY`, `ETL_ENABLE_SM_CAMPAIGN`,
   `ETL_SM_FILL_RECALC_DAYS`, `ETL_SM_CAMPAIGN_ATTR_DAYS`, `ETL_SM_STOCK_BACKFILL_DAYS` (+ `PROMO_CAMPAIGN_ENABLED` bila
   kampanye dinyalakan di production; tanpa itu ETL menolak start, pola L-180). Tidak ada secret baru.
4. Setelah deploy: reset watermark job `products` sekali (README) agar `dim_product.categ_id` terisi.
5. Jalankan harness row policy dan catat vektor sig2 §28.13.6 (`test_scope_vectors_are_computable`).

### Penerapan fit-gap C-1..C-7
- **C-1** `v_sm_kpi_config.is_default` = baris tidak ada/nonaktif ATAU nilai sama dengan default (enum: teks; numerik:
  angka).
- **C-3** `qty_shipped` = move `done` bertujuan lokasi `transit` di picking usulan + backorder (≤ 3); bila picking usulan
  ternyata picking pick, diikuti satu langkah `move_dest` ke move ship. `qty_received` = move lanjutan (≤ 2 langkah dari
  ship) yang **melintasi** batas outlet (tujuan = warehouse outlet, asal ≠ outlet) → receipt 2/3 langkah dihitung sekali.
  Qty dikonversi ke UoM produk. `picking_shared` tetap ada sebagai penjaga (alokasi FIFO).
- **C-4** redemption online = `sale_order_line.coupon_id` dengan baris reward `reward_id IS NOT NULL`, hanya SO
  `state='sale'`; grant `sale_order(id, company_id, state)`, `sale_order_line(id, order_id, coupon_id, reward_id,
  price_subtotal)` bersyarat. ETL mendeteksi grant lewat `information_schema.column_privileges` (tanpa grant = hanya POS).
  **Aturan satu sumber:** kupon dihitung di dokumen tempat ia diterapkan (POS `pos_order_line.coupon_id` atau SO
  `sale_order_line.coupon_id`); pesanan online yang dilunasi di kasir (E-45) menjadi order POS berisi produk pelunasan
  tanpa kupon, jadi tidak ada pesanan yang masuk dua kanal. Pesanan dihitung per (kanal, order_id); voucher dipakai =
  kartu berbeda lintas kanal; omzet = Σ subtotal (tanpa PPN) semua baris pesanan bervoucher.
- **C-5** `cats` diproduksi Odoo/insight-web; sisi data tidak memfilter `product.category.active` (kolom tidak ada).
  `cats` hanya ikut tanda tangan, belum predikat (L-189).
- **C-6** scrap: gudang `COALESCE(efn_warehouse_id, stock_location.warehouse_id, 0)`, nilai `COALESCE(efn_waste_value,
  NULLIF(Σ|stock_move.value| move scrap done, 0), qty × standard_price PT, 0)`. Job G32 `waste_scraps` dan job baru
  `sm_scraps` (PT SM, deteksi kolom `efn_*` tiap siklus) memakai **ekspresi yang sama** sehingga baris identik bila
  keduanya aktif. `v_sm_freshness` mart `waste` memakai job `sm_scraps` (kontrak menyebut `waste_scraps`).
- **C-7** `from_orderpoint = orderpoint_id IS NOT NULL AND stock_warehouse_orderpoint.efn_replenishment_managed`; grant
  `purchase_order_line.orderpoint_id`, `stock_warehouse_orderpoint(id, warehouse_id, efn_replenishment_managed)`.
  `is_late` memakai `po_state='purchase'` (PO 19.0 tanpa `done`).

### Asumsi & penyimpangan dari kontrak (beralasan)
- `fact_replenishment.header_ts` (tambahan): Odoo menghapus baris usulan saat hitung ulang; view memakai baris dengan
  `header_ts` terbaru per usulan. Usulan draft/proposed lewat cut-off ditampilkan `expired` (usulan yang dihapus Odoo
  tidak pernah mendapat versi baru).
- Supplier: **tanpa grant `res_partner`**. View `efn_etl.purchase_partner` (milik efn_odoo) memberi `id,
  commercial_partner_id, is_company, write_date` dan nama **hanya** untuk partner perusahaan. Lebih sempit dari grant
  kolom kontrak (yang membuka nama pelanggan perorangan ke efn_etl).
- `dim_sm_kpi_config`: salin penuh tiap siklus (bukan cursor) agar baris yang **dihapus** di Odoo terdeteksi → `active=0`.
  Nilai tidak sah menurut registri → `active=0` (view memakai default). Kunci tak dikenal tidak disalin.
- `fact_replenishment_bias`: MergeTree + dedup `log_id` di ETL (cek ClickHouse per halaman) + `LIMIT 1 BY log_id` di view.
- `fact_dc_purchase_line`/`fact_lot_markdown`: kolom partisi bisa berubah (RFQ/markdown draf) → view `LIMIT 1 BY` id
  versi terbaru (RMT hanya dedup per partisi).
- `agg_sm_stock_daily` & `agg_sm_expiry_daily`: hitung ulang hari menyertakan kunci lama dengan nilai 0, agar produk/alert
  yang hilang tidak meninggalkan angka basi. Backfill stok pertama `ETL_SM_STOCK_BACKFILL_DAYS` (90), maks 14 hari/siklus.
- `fact_dc_fill_rate`: hanya baris berubah yang ditulis tiap siklus. `on_time` tersimpan = toleransi 0 (Σ qty terkirim ≤
  `delivery_date` WIB ≥ disetujui); toleransi `fill.grace_days` di view berbasis tanggal kirim terakhir.
- `v_sm_price_index` menulis ulang aturan pencocokan `v_price_index` (SKU lalu nama) tanpa agregasi POS 30 hari;
  `our_price` = `list_price` template (basis PPN SMD belum terverifikasi).
- Funnel: `channel_code` dicocokkan persis dengan `efn_online_channel.code` (L-190), sedangkan ETL G50 meng-uppercase
  `channel_code` event → kode kanal Odoo SMD wajib huruf besar (data setup, mirip Q-SM43).
- JSON normatif saya buat dari tabel kontrak (format `keys[]` + `pairs[]`); builder Odoo/insight-web perlu memakai format
  ini (kontrak menyebut builder Odoo yang membuat).

### Belum terverifikasi
- Semua SQL ClickHouse (tipe Decimal/Nullable di `multiIf`/`ifNull`/UNION, `ORDER BY` kolom tak terpilih + `LIMIT 1 BY`,
  JOIN view `v_sm_kpi_settings`), CREATE VIEW DEFINER atas view INVOKER, dan perilaku row policy (harness belum dijalankan).
- Kolom Odoo: `stock_move_move_rel(move_orig_id, move_dest_id)`, `stock_move.scrap_id`/`value`, `uom_uom.factor` di 19.0,
  `standard_price ->> company_id` (jsonb), `purchase_order_line.qty_received` (fit-gap: [V] tersimpan), `efn_insight_sm_kpi`
  (modul dibangun paralel; nama tabel diasumsikan dari model `efn.insight.sm.kpi`), `information_schema.column_privileges`
  untuk deteksi grant online.
- Kupon: `pos_order_line.coupon_id` pada baris reward ([V] fit-gap); asumsi baris reward POS selalu membawa `coupon_id`.
- `proposal.picking_id` = picking ship (fit-gap §6.1 [B runtime]); usulan dengan beberapa picking DC hanya picking
  pertama yang dihitung.
- Insight-web `lib/sm-defaults.ts` dibandingkan bila ada (test parity); registri Odoo belum ada di branch ini.

## 1. File

### services/ai-ocr-worker (refactor; `worker.py` dihapus)
| File | Isi |
|---|---|
| `efn_ocr/config.py` | `Settings.from_env()`, `read_secret()` (`<NAME>_FILE` wajib ada bila di-set) |
| `efn_ocr/messages.py` | nama queue, argumen queue/DLQ, parse `ocr.requested`, payload `loyalty.earn.requested` & `notify.push`, header retry, mask nomor |
| `efn_ocr/pricing.py` | parser Rupiah (`Rp 12.500`, `12,500`, `12.5rb`, `1,2jt`, satuan & barcode diabaikan), toleransi harga |
| `efn_ocr/matching.py` | fuzzy nama rapidfuzz (`max(token_set_ratio, partial_ratio)`) |
| `efn_ocr/fraud.py` | geofence (haversine), pHash helper, cap harian |
| `efn_ocr/validation.py` | keputusan murni `valid/rejected/flagged` + reason |
| `efn_ocr/imaging.py`, `ocr_engines.py`, `vlm.py`, `storage.py` | Pillow+imagehash, Tesseract (default)/PaddleOCR (opsional), Ollama `/api/generate`, MinIO |
| `efn_ocr/repository.py` | psycopg 3: klaim, dedup pHash (SQL `bit_count`), cap harian, finalize+publish dalam satu transaksi, release |
| `efn_ocr/publisher.py`, `consumer.py`, `delivery.py` | pika: deklarasi topologi, publish dengan confirm, consumer ack manual + thread worker, retry/DLQ |
| `efn_ocr/processor.py` | orkestrasi per laporan (dependensi diinjeksi) |
| `efn_ocr/metrics.py`, `__main__.py` | Prometheus `:9100`, entry point `python -m efn_ocr` |
| `tests/` (8 file) | pricing, matching, fraud, validation, messages, config, engines (agregasi Tesseract, parse VLM), processor+delivery dengan fake |
| `requirements.txt`, `requirements-dev.txt`, `Dockerfile`, `.dockerignore`, `ruff.toml`, `pytest.ini`, `README.md` | |

### services/etl (refactor; `etl.py` dihapus)
`efn_etl/{config,watermark,hashing,jobs,adapters,runner,__main__}.py`, `tests/{test_watermark,test_jobs,test_runner}.py`, `requirements*.txt`, `Dockerfile`, `.dockerignore`, `ruff.toml`, `pytest.ini`, `README.md`.

### services/text2sql (baru)
`efn_text2sql/{guard,semantic,prompt,__init__}.py`, `semantic_layer.yaml`, `tests/{test_guard,test_prompt}.py` (±50 kasus negatif), `requirements*.txt`, `ruff.toml`, `pytest.ini`, `README.md`.

### infra/clickhouse
- `init/01-schema.sql` (diubah): `competitor_prices` -> `ReplacingMergeTree(updated_at)`, `ORDER BY report_id`, kolom baru `extracted_price, status, reason, ocr_confidence, user_hash, updated_at`, **`user_id` dihapus** (diganti hash); `fact_sales_line.write_date` -> `DateTime64(6,'UTC')`; `mv_sales_daily` (MV SummingMergeTree) **diganti** VIEW `v_sales_daily` ber-FINAL karena MV menghitung ganda baris overlap ETL; `_etl_watermark` dihapus (diganti `etl_watermark`).
- `init/02-loyalty-etl.sql` (baru): `loyalty_events`, `etl_watermark`, view `v_competitor_prices`, `v_price_hunter_quality`, `v_loyalty_daily`.
- `access/text2sql-access.sql` (baru, **sengaja di luar `init/`**): settings profile `text2sql_readonly`, role `text2sql_reader`, grant SELECT view + kolom tabel dasar. Dijalankan saat deploy bersama `CREATE USER` (butuh secret), agar kesalahan sintaks tidak menggagalkan init.
- Init script hanya berjalan pada volume kosong: volume dev yang sudah ada perlu `docker compose down -v` (clickhouse-data) atau DDL manual.

## 2. Env & secret yang dibaca (untuk dicocokkan devsecops/QA dengan compose)

### ocr-worker
| Env | Wajib/Default | Catatan compose |
|---|---|---|
| `RABBITMQ_URL` | `amqp://efn@rabbitmq:5672` | sudah ada |
| `RABBITMQ_PASSWORD_FILE` | secret `rabbitmq_password` | sudah ada |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_DB` (fallback `PLATFORM_DB`) | `postgres`, `5432`, `efn`, `efn_platform` | sesuai env yang disiapkan compose |
| `PG_PASSWORD_FILE` | secret `pg_password` | sesuai compose |
| `MINIO_ENDPOINT`, `MINIO_SECURE` | `minio:9000`, `false` | ada / opsional |
| `MINIO_ACCESS_KEY` -> fallback `MINIO_ROOT_USER` | | sekarang memakai root (disarankan service account read-only) |
| `MINIO_SECRET_KEY_FILE` -> fallback `MINIO_ROOT_PASSWORD_FILE` | secret | ada (root) |
| `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` | opsional |
| `OCR_ENGINE`, `OCR_TESSERACT_LANG`, `OCR_CONFIDENCE_THRESHOLD`, `OCR_VLM_ENABLED` | `tesseract`, `ind+eng`, `0.80`, `true` | |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS` | `http://ollama:11434`, `qwen2.5vl:7b`, `90` | ada (kecuali timeout) |
| `NAME_SIMILARITY_THRESHOLD` | `75` (nilai `0.75` diterima) | ada |
| `PRICE_TOLERANCE_PCT`, `PRICE_HUNTER_DAILY_CAP`, `PRICE_HUNTER_POINTS` | `2.0`, `20`, `10` | |
| `PHASH_MAX_DISTANCE`, `PHASH_LOOKBACK_DAYS`, `GEOFENCE_CIRCLES` | `6`, `30`, kosong | |
| `OCR_PREFETCH`, `OCR_MAX_RETRIES`, `OCR_STALE_PROCESSING_SECONDS`, `OCR_MAX_IMAGE_BYTES` | `2`, `3`, `900`, 10 MB | |
| `METRICS_PORT`, `LOG_LEVEL` | `9100`, `INFO` | tambahkan target scrape Prometheus `ocr-worker:9100` |
| `BFF_INTERNAL_URL` | **tidak lagi dipakai** | hapus dari compose (worker tidak memanggil HTTP BFF, sesuai kontrak) |

Jaringan: worker perlu `data` (postgres, rabbitmq) dan `internal` (minio, ollama) — sudah `[internal, data]`. Tesseract menulis file sementara ke `/tmp`: bila service diberi `read_only: true`, tambahkan `tmpfs: [/tmp]`. Image berjalan sebagai UID 10001, kode milik root.

### etl
| Env | Default | Catatan |
|---|---|---|
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD_FILE`, `PG_DB` (fallback `PLATFORM_DB`) | `postgres`, `5432`, `efn`, secret `pg_password`, `efn_platform` | ada |
| `ODOO_DB` | `efn_odoo` | ada |
| `ODOO_PG_HOST`, `ODOO_PG_PORT`, `ODOO_PG_USER`, `ODOO_PG_PASSWORD_FILE` | ikut `PG_*` | baru, opsional (read replica produksi) |
| `ETL_ENABLE_ODOO` | `false` | job Odoo masih stub |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD_FILE`, `CH_DATABASE`, `CH_SECURE` | `clickhouse`, `8123`, `efn`, secret `clickhouse_password`, `efn_dwh`, `false` | ada sebagian |
| `OTP_PEPPER_FILE` | **wajib** | **compose etl perlu secret `otp_pepper`** (`OTP_PEPPER_FILE=/run/secrets/otp_pepper`). `user_hash = hex(HMAC-SHA256(key, user_id))`, `key = HKDF-SHA256(ikm=otp_pepper, salt=b"", info=b"efn:member_hash:v1", length=32)` — identik dengan `member_ref_hash` BFF (kontrak §1). Tanpa secret, job `competitor_prices` dilewati (fail closed). Dependency baru `cryptography==44.0.1`. Test vector di `tests/test_jobs.py`. |
| `ETL_INTERVAL_SECONDS`, `ETL_OVERLAP_SECONDS`, `ETL_BATCH_SIZE`, `ETL_MAX_PAGES_PER_RUN`, `ETL_STATEMENT_TIMEOUT_MS`, `ETL_RUN_ONCE` | `900`, `300`, `5000`, `200`, `300000`, `false` | |

Entry point berubah: `python -m efn_etl` / `python -m efn_ocr` (CMD Dockerfile sudah disesuaikan; compose tidak override `command`).

### text2sql
Belum service; contoh pemakaian membaca `semantic_layer.yaml` dan memanggil Ollama `qwen2.5-coder:7b`. User ClickHouse `efn_text2sql` + secret password dibuat saat deploy (lihat `infra/clickhouse/access/text2sql-access.sql`).

## 3. Kontrak RabbitMQ yang diimplementasikan (cek silang dengan BFF)

- Tidak ada `"dlx"` lagi (worker.py lama dihapus). Worker mendeklarasikan **ketiga** queue + DLQ dengan argumen persis: queue `{"x-queue-type":"quorum","x-dead-letter-exchange":"","x-dead-letter-routing-key":"<queue>.dlq"}`, durable; DLQ `{"x-queue-type":"quorum"}`, durable. BFF wajib memakai argumen identik (tanpa `x-delivery-limit`, TTL, dll.), jika tidak broker menolak dengan 406 dan worker reconnect berulang.
- Publish: default exchange, `message_id = idempotency_key`, `content_type=application/json`, `delivery_mode=2`, publisher confirm + `mandatory`.
- `loyalty.earn.requested`: `{schema_version:1, idempotency_key:"price_hunter:<report_id>", member_ref:<user_id>, points, reason:"price_hunter_valid", source:"price_hunter", source_ref:<report_id>}`.
- `notify.push`: `{schema_version:1, idempotency_key:"price_hunter_notify:<report_id>", user_id, template:"price_hunter_valid"|"price_hunter_rejected", params:{report_id, points}|{report_id, reason}}`. Status `flagged` tidak mengirim notifikasi.
- Retry `ocr.requested`: republish pesan yang sama dengan header `x-retry-count` (int) dan `message_id` asli.
- Worker hanya memakai `report_id` dan `schema_version` (wajib 1) dari pesan; data laporan dibaca dari PostgreSQL. `image_bucket` di pesan diabaikan (bucket dari konfigurasi), key harus berawalan `reports/`.

## 4. Asumsi skema

1. `platform.price_hunter_report` sesuai `infra/postgres/init/01-databases.sql`. Worker menulis `status, reason, extracted (jsonb), confidence (numeric 4,3), image_phash (16 hex huruf kecil, imagehash pHash), updated_at`. **Worker pemilik `image_phash`** (menimpa nilai BFF); BFF sebaiknya tidak mengisinya atau memakai algoritma identik.
2. `user_id` laporan = `member_ref` (E.164 tanpa `+`). Jika BFF memakai UUID user, kontrak `ocr.requested` perlu menambah `member_ref` atau BFF memetakan saat consume `loyalty.earn.requested`.
3. `platform.loyalty_ledger` (milik BFF, `02-platform-loyalty.sql` belum ada saat saya bekerja) punya kolom `id, member_ref_hash, delta_points, reason, source, source_ref, created_at`; tipe `id` bebas (ETL membandingkan `id::text`). Disarankan index `(created_at)`.
4. User PostgreSQL worker butuh SELECT/UPDATE `platform.price_hunter_report`; ETL cukup read-only.
5. PostgreSQL >= 14 (compose 16.6) untuk `bit_count`.
6. Job Odoo: kolom Odoo 17/18 (`pos_order_line.full_product_name`, `total_cost`, `price_subtotal_incl`, `pos_order.pos_reference/state/session_id`, `res_company.efn_brand_code`); belum dicek di CE 19.
7. Nilai poin per laporan (10) dan cap 20/hari adalah placeholder sampai Q-13/Q-43 dijawab.

## 5. Keputusan desain yang perlu diketahui

- **OCR tahap 1 = Tesseract**, bukan PaddleOCR (image kecil, tanpa unduh model runtime, aman Python 3.12). PaddleOCR opsional via `OCR_ENGINE=paddle` di image turunan. Menyimpang dari diagram docs/02 §3.2 yang menyebut PaddleOCR; ADR-008 mengizinkan keduanya.
- **Publish sebelum COMMIT** di transaksi finalize: publish gagal -> rollback -> retry; commit gagal setelah publish -> retry publish ulang dengan idempotency key sama (BFF dedup via `processed_message`). Outbox (`platform.outbox`) tidak dipakai worker karena relay-nya milik BFF dan belum ada.
- Klaim boleh mengambil alih status `processing` bila pesan redelivered/retry atau klaim > 15 menit (worker crash), agar laporan tidak macet. Finalize kondisional `WHERE status='processing'` mencegah tulis ganda.
- Pemrosesan di thread; ack via `add_callback_threadsafe` supaya heartbeat RabbitMQ tidak putus saat VLM 60–120 s.
- Sinyal fraud diperiksa **sebelum** OCR (hemat CPU/GPU): duplikat pHash -> `flagged`, cap harian -> `rejected`, di luar geofence -> `flagged`.
- Nilai laporan pengguna tidak dikirim ke VLM (hindari bias), prompt menyuruh abaikan instruksi di gambar.
- Guard Text-to-SQL mengeksekusi SQL hasil regenerasi sqlglot tanpa komentar, dibungkus `SELECT * FROM (...) AS _guarded LIMIT n SETTINGS max_execution_time = N`.

## 6. Belum terverifikasi / risiko sisa

- Test vector member hash (`otp_pepper="test-otp-pepper"`, member `6281234567890`) dihitung independen dengan HMAC-SHA256 .NET mengikuti RFC 5869; belum dicocokkan dengan implementasi BFF — sebaiknya BFF memakai vector yang sama.
- Seluruh test belum pernah dijalankan. Paling berisiko: kasus guard yang bergantung perilaku parser **sqlglot 26.0.0** (versi pin juga belum dicek ketersediaannya di PyPI; ganti ke rilis 26.x/27.x terbaru bila perlu), mis. `INTO OUTFILE`, `SETTINGS`, `remoteSecure(...)`; dan ambang skor rapidfuzz di `test_matching.py`.
- Versi pin lain (pika 1.3.2, psycopg 3.2.3, minio 7.2.12, requests 2.32.4, Pillow 11.3.0, ImageHash 4.3.1, rapidfuzz 3.10.1, pytesseract 0.3.13, prometheus-client 0.21.1, clickhouse-connect 0.8.9, PyYAML 6.0.2, pytest 8.3.4) belum diuji `pip-audit` — requests dinaikkan ke 2.32.4 dan Pillow ke 11.3.0 untuk CVE yang saya ketahui.
- `ruff format --check`: saya menulis mengikuti gaya ruff/black, tetapi belum dijalankan. Setiap service punya `ruff.toml` yang `extend` root + `known-first-party` agar urutan import deterministik.
- **CI**: job `python` di `.github/workflows/ci.yml` belum menjalankan pytest dan belum mencakup `services/text2sql` (ruff, compileall, pip-audit). Saya tidak punya izin menulis `.github/`; mohon devsecops menambahkan: `pip install -r requirements-dev.txt && pytest` per service dan path filter `services/text2sql/**`.
- ClickHouse: grant level-kolom + view `FINAL` dengan hak INVOKER belum diuji di 24.8 (mungkin perlu hak kolom sorting key, mis. `member_hash`); alternatif `SQL SECURITY DEFINER`.
- `clickhouse-connect` dan kolom `DateTime64(6,'UTC')`: konversi datetime aware dan nilai `argMax` pada set kosong (ditangani `normalize_watermark`) belum diuji.
- PaddleOCR path (`PaddleEngine`) memakai API 2.x dan tidak diuji sama sekali.
- Cap harian bisa terlewati 1 laporan bila dua laporan user yang sama diproses paralel.
- Deteksi foto editan/AI/dari internet belum ada (R-B05 sisa).
- ETL belum mengekspor metrik (lag watermark); belum menangani hard delete.

---

## Gelombang 3 — Enkripsi di setiap transaksi data (5 Okt 2026)

Kontrak: docs/04 v2 §4 (payload `schema_version: 2`, tanpa HP mentah, properti AMQP `user_id`) dan §7 (TLS, at rest, kredensial per service). **Tidak ada yang dijalankan** (tanpa Python/Docker); semua test belum dieksekusi. Bagian §2–§3 di atas sudah usang untuk env/payload; tabel di bawah yang berlaku.

### G3.1 File

| File | Perubahan |
|---|---|
| `services/ai-ocr-worker/efn_ocr/tls.py` (baru) | kebijakan TLS: `EFN_ALLOW_PLAINTEXT` (ditolak bila `APP_ENV`/`NODE_ENV=production`), CA wajib terbaca, `client_ssl_context` (hanya CA internal, TLS ≥ 1.2, verifikasi hostname), `pg_ssl_settings` (wajib `verify-full`), pasangan cert/key server |
| `efn_ocr/config.py` | default TLS: `amqps://efn-ocr@rabbitmq:5671`, PG `efn_ocr` + verify-full, MinIO `minio:9000` SSL + `efn-ocr`, Ollama `https://`, metrik HTTPS; fallback `MINIO_ROOT_*` dihapus; validasi fail-closed di `from_env` |
| `efn_ocr/publisher.py` | pika `SSLOptions(context, server_hostname)`, port 5671; menolak password di URL; properti AMQP `user_id` = user URL (SEC-08) |
| `efn_ocr/storage.py` | `Minio(secure=True, http_client=urllib3.PoolManager(cert_reqs=CERT_REQUIRED, ca_certs=CA))` |
| `efn_ocr/vlm.py` | `requests.Session.verify=CA`, `trust_env=False` |
| `efn_ocr/metrics.py`, `healthcheck.py` (baru), `Dockerfile` | `start_http_server(certfile, keyfile)` (prometheus-client ≥ 0.17, pin 0.21.1); healthcheck HTTPS loopback |
| `efn_ocr/messages.py`, `processor.py`, `repository.py`, `consumer.py`, `__main__.py` | event v2 (`user_hash` masuk, `member_hash`+`source`+`source_ref` keluar), cap harian & dedup per `user_hash`, PG `sslmode/sslrootcert`; `mask_ref` → `short_hash` |
| `requirements.txt` | + `urllib3==2.5.0` (dipakai langsung) |
| `tests/test_config.py`, `test_messages.py`, `test_processor.py` (ubah), `test_tls.py`, `test_repository_sql.py` (baru) | fail-closed per koneksi, payload v2 persis, tolak HP mentah, `user_id` AMQP, kolom UPDATE/SELECT yang diizinkan |
| `services/etl/efn_etl/tls.py` (baru), `config.py`, `adapters.py`, `jobs.py`, `runner.py`, `__main__.py` | PG verify-full (platform + Odoo), ClickHouse HTTPS 8443 `verify=True, ca_cert=CH_CA_FILE`, user default `efn_etl`; `user_hash`/`member_ref_hash` disalin (validasi hex sha256, fail closed); `company_code` di `loyalty_events` |
| `services/etl/efn_etl/hashing.py` | **dihapus** (SEC-16) + `cryptography` dihapus dari requirements; `TransformContext` dihapus |
| `services/etl/tests/test_jobs.py`, `test_runner.py` (ubah), `test_config.py` (baru) | test vector HKDF dihapus; test pass-through hash, tolak HP, company_code, TLS fail-closed |
| `services/text2sql/efn_text2sql/guard.py`, `semantic.py`, `semantic_layer.yaml`, `tests/test_guard.py` | SEC-04 (lihat G3.4) |
| `infra/clickhouse/init/02-loyalty-etl.sql` | `loyalty_events.company_code` + `ALTER ... ADD COLUMN IF NOT EXISTS` (volume lama), `v_loyalty_daily` per `company_code` (`CREATE OR REPLACE VIEW`) |
| `infra/clickhouse/access/text2sql-access.sql` (ubah), `etl-access.sql`, `metabase-access.sql` (baru) | role + profil + grant per user `efn_text2sql` / `efn_etl` / `efn_metabase` (lihat G3.3) |
| `infra/clickhouse/config.d/encrypted-storage.xml` (baru) | disk `encrypted` AES-256-CTR, kunci `from_env="CLICKHOUSE_DISK_KEY_HEX"`, policy default MergeTree |
| `infra/clickhouse/config.d/tls.xml` | saya tulis, lalu **ditimpa devsecops** (pemilik); versi devsecops yang berlaku |

### G3.2 Env & secret yang dibaca per service (untuk compose devsecops)

**ocr-worker** — secret: `pg_password_ocr`, `rabbitmq_password_ocr`, `minio_ocr_secret`, `efn_ca_crt`, `tls_ocr_worker_crt`, `tls_ocr_worker_key`.

| Env | Nilai produksi |
|---|---|
| `APP_ENV` | `production` (jangan set `EFN_ALLOW_PLAINTEXT`) |
| `AMQP_URL` (fallback lama `RABBITMQ_URL`) | `amqps://efn-ocr@rabbitmq:5671` |
| `RABBITMQ_PASSWORD_FILE`, `AMQP_TLS_CA_FILE` | `/run/secrets/rabbitmq_password_ocr`, `/run/secrets/efn_ca_crt` |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_DB`, `PG_PASSWORD_FILE` | `postgres`, `5432`, `efn_ocr`, `efn_platform`, `/run/secrets/pg_password_ocr` |
| `PG_SSLMODE`, `PG_SSLROOTCERT` | `verify-full`, `/run/secrets/efn_ca_crt` |
| `MINIO_ENDPOINT`, `MINIO_PORT`, `MINIO_USE_SSL`, `MINIO_CA_FILE` | `minio`, `9000`, `true`, `/run/secrets/efn_ca_crt` |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY_FILE` | `efn-ocr`, `/run/secrets/minio_ocr_secret` |
| `OLLAMA_URL`, `OLLAMA_CA_FILE` | `https://ollama:11434`, `/run/secrets/efn_ca_crt` |
| `METRICS_TLS_CERT_FILE`, `METRICS_TLS_KEY_FILE`, `METRICS_TLS_CA_FILE` | `/run/secrets/tls_ocr_worker_crt`, `…_key`, `/run/secrets/efn_ca_crt` |
| lainnya tidak berubah | `OCR_*`, `OLLAMA_MODEL`, `PRICE_HUNTER_*`, `PHASH_*`, `GEOFENCE_CIRCLES`, `METRICS_PORT=9100` |

Dihapus: `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD_FILE`, `MINIO_SECURE` (masih dibaca sebagai fallback `MINIO_USE_SSL`), `BFF_INTERNAL_URL`. Prometheus scrape `https://ocr-worker:9100` dengan CA internal.

**etl** — secret: `pg_password_etl`, `clickhouse_password_etl`, `efn_ca_crt`. **`otp_pepper` dicabut dari service etl.**

| Env | Nilai produksi |
|---|---|
| `APP_ENV` | `production` |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_DB`, `PG_PASSWORD_FILE` | `postgres`, `5432`, `efn_etl`, `efn_platform`, `/run/secrets/pg_password_etl` |
| `PG_SSLMODE`, `PG_SSLROOTCERT` | `verify-full`, `/run/secrets/efn_ca_crt` |
| `ODOO_PG_*` (`HOST/PORT/USER/PASSWORD_FILE/SSLMODE/SSLROOTCERT`), `ODOO_DB` | opsional; default ikut `PG_*` |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD_FILE`, `CH_DATABASE` | `clickhouse`, `8443`, `efn_etl`, `/run/secrets/clickhouse_password_etl`, `efn_dwh` |
| `CH_SECURE`, `CH_CA_FILE` | `true`, `/run/secrets/efn_ca_crt` |

**clickhouse (server)** — secret baru **`clickhouse_disk_key`** (64 karakter hex, `openssl rand -hex 32`); entrypoint compose perlu `export CLICKHOUSE_DISK_KEY_HEX="$(cat /run/secrets/clickhouse_disk_key)"` (pola yang sama dengan `CLICKHOUSE_PASSWORD`), dan mount `../clickhouse/config.d/encrypted-storage.xml:/etc/clickhouse-server/config.d/encrypted-storage.xml:ro`. Secret ini **wajib** di-backup terpisah dari volume (hilang kunci = data mart hilang; mart bisa dibangun ulang dari Postgres dengan reset watermark).

**text2sql** — belum service; saat dibuat: user `efn_text2sql`, ClickHouse HTTPS 8443 + `efn_ca_crt`, Ollama `https://ollama:11434` + CA (README).

### G3.3 Hak database yang dibutuhkan (untuk BFF / devsecops)

- **PostgreSQL `efn_ocr`** pada `platform.price_hunter_report`: `SELECT (id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object, image_phash, status, created_at, updated_at)` + `UPDATE (status, reason, extracted, confidence, image_phash, updated_at)`. `UPDATE … RETURNING` dan `WHERE` butuh SELECT atas kolom-kolom itu. Tanpa `user_enc`. Index `(user_hash, created_at)` disarankan untuk cap harian (pengganti `ix_phr_user_created`).
- **PostgreSQL `efn_etl`** (anggota `efn_readonly`): grant kolom `price_hunter_report` **harus mengganti `user_id` dengan `user_hash`** (`id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, status, extracted, confidence, reason, created_at, updated_at`); `loyalty_ledger (id, member_ref_hash, company_code, delta_points, reason, source, source_ref, created_at)` sudah sesuai.
- **RabbitMQ `efn-ocr`**: read `ocr.requested`; write via default exchange (`amq.default`); **configure** pada 3 queue + 3 DLQ (worker mendeklarasikan topologi). Catatan: dengan default exchange, izin write RabbitMQ diperiksa pada exchange `amq.default`, **bukan** per queue — pembatasan "write hanya ke loyalty/notify" di §7.3 tidak bisa ditegakkan broker; kontrol nyata = validasi `user_id` + semantik di BFF (SEC-08). Retry worker mem-publish ulang ke `ocr.requested` (butuh write `amq.default`, sudah tercakup).
- **MinIO `efn-ocr`**: `s3:GetObject` `market-scout/reports/*` (SSE-S3 transparan untuk GET).
- **ClickHouse** (`infra/clickhouse/access/*.sql`, dijalankan admin saat deploy **setelah** user dibuat devsecops; idempoten): `efn_etl` → role `etl_writer` (SELECT+INSERT 3 tabel mart + `etl_watermark`, profil `allow_ddl=0`); `efn_text2sql` → role `text2sql_reader` (SELECT 4 view + kolom non-identitas, profil `readonly=2 CONST`, `max_execution_time 15 MAX 30`, `max_result_rows 1000`, `allow_introspection_functions=0`, kuota per jam); `efn_metabase` → role `metabase_reader` (SELECT view + kolom mart tanpa `user_hash`/`member_hash`/`customer_id`, `readonly=2 CONST`). Urutan file bebas; masing-masing butuh user target sudah ada.

### G3.4 Status SEC

| ID | Status | Catatan |
|---|---|---|
| SEC-04 | **Diperbaiki (kode), belum dieksekusi** | Resolusi CTE per scope (`_ScopeChecker`), tolak shadowing (`protected_tables` + view whitelist), tolak CTE skalar sebagai sumber tabel, tolak `WITH RECURSIVE`, `IN` hanya subquery/konstanta, minimal satu view whitelist. Test: payload laporan + 20 variasi (subquery IN/FROM/scalar, UNION, self-reference, urutan CTE, `IN tabel`, `GLOBAL IN`, `IN (kolom)`, `JOIN USING`) + 7 kasus positif. Lapisan kedua lengkap (rekomendasi 4): access SQL per user; menjadikannya langkah wajib deploy = devsecops. |
| SEC-08 | **Sisi worker selesai** | Properti AMQP `user_id=efn-ocr`, payload `member_hash` + `source_ref`; validasi semantik di BFF. |
| SEC-16 | **Selesai** | ETL tidak lagi membaca `otp_pepper`; `hashing.py` + `cryptography` dihapus; hash disalin dari Postgres dan divalidasi format. devsecops: cabut secret `otp_pepper` dari service etl. |
| SEC-19 | Info, tanpa kode | Berlaku di BFF (decode gambar). Catatan data-AI: worker OCR sudah memproses gambar di proses terpisah dengan prefetch 2 dan `OCR_MAX_IMAGE_BYTES`; bila intake dipindah ke worker (rekomendasi jangka menengah), pola ini bisa dipakai ulang. Disarankan `Image.MAX_IMAGE_PIXELS` disamakan dengan batas BFF. |

### G3.5 Enkripsi at rest ClickHouse

Disk `encrypted` dipakai (praktis di 24.8, fitur stabil sejak 21.x): `config.d/encrypted-storage.xml`, AES-256-CTR, kunci dari secret `clickhouse_disk_key` lewat env, policy `encrypted` jadi default via `<merge_tree><storage_policy>`. Batasan: metadata/DDL/nama part tidak terenkripsi, tabel lama tidak otomatis pindah (dev: `docker compose down -v` volume `clickhouse-data`), log sistem (`system.query_log`) ikut MergeTree → ikut terenkripsi. LUKS host tetap wajib. **Belum diverifikasi**: bahwa `<merge_tree><storage_policy>` diterapkan ke tabel yang dibuat oleh skrip init (cek `system.tables.storage_policy`); fallback bila tidak: tambahkan `SETTINGS storage_policy = 'encrypted'` di DDL `init/*.sql`.

### G3.6 Asumsi & risiko yang perlu diketahui lead

1. **`tls.xml` devsecops menghapus port native 9000.** Entrypoint image resmi menjalankan skrip `/docker-entrypoint-initdb.d` dengan `clickhouse-client` ke `127.0.0.1` port default 9000 (readiness memakai HTTP/HTTPS ping). Tanpa 9000, inisialisasi skema pada volume kosong kemungkinan gagal. Opsi: `/etc/clickhouse-client/config.xml` dengan `<secure>true</secure><port>9440</port>` + CA dan SAN sertifikat `localhost/127.0.0.1`, atau pertahankan 9000 hanya saat init. Perlu diverifikasi devsecops.
2. Endpoint Prometheus ClickHouse `:9363` (`config.d/prometheus.xml`) masih HTTP — tidak memuat PII; perlu sidecar ghostunnel atau pengecualian ADR-022.
3. Skema BFF: `price_hunter_report.user_hash char(64)` berisi hex huruf kecil (rumus §1). Worker menolak `ocr.requested` v1 dan pesan yang memuat `user_id`/`member_ref` (→ DLQ); pesan v1 yang masih ada di queue saat deploy akan masuk DLQ.
4. ETL fail-closed: satu baris dengan `user_hash`/`member_ref_hash` bukan hex sha256, atau `company_code` di luar `EFN|RTL|FNB|FARM`, menghentikan job tersebut setiap run sampai baris diperbaiki (error di log, watermark tidak maju).
5. `loyalty_events` pada volume lama: kolom `company_code` ditambah dengan nilai `''` untuk baris lama; backfill = hapus baris `etl_watermark` job `loyalty_events` (ReplacingMergeTree men-dedup).
6. Belum diverifikasi: versi pin `urllib3==2.5.0` kompatibel dengan `minio 7.2.12`/`requests 2.32.4` (seharusnya); `clickhouse-connect 0.8.9` argumen `verify`/`ca_cert`; perilaku sqlglot 26.0.0 untuk `x IN tabel` dan CTE skalar ClickHouse (`scalar=True`); GRANT kolom + `FINAL` dengan hak INVOKER; `CREATE QUOTA … KEYED BY user_name` dan `readonly = 2 CONST` di 24.8.
7. Mutual TLS tidak dipakai; autentikasi klien tetap password per service. *(Usang — lihat Gelombang 5: mTLS Ollama & metrik.)*

---

## Gelombang 5 — SEC-27, erasure ClickHouse, TTL, mTLS, grant Odoo (5 Okt 2026)

Kontrak: docs/04 v3 §8.4, §8.5, §4, §7; review r2 SEC-11/20/27; QA G4 U1. **Tidak ada yang dijalankan** (tanpa Python/Docker/ClickHouse).

### G5.1 File
| File | Perubahan |
|---|---|
| `services/ai-ocr-worker/efn_ocr/tls.py`, `services/etl/efn_etl/tls.py` | SEC-27: `is_production` fail-closed — production kecuali `APP_ENV` (dan `NODE_ENV` bila di-set) ∈ {dev, development, local, test, ci}; kosong/`staging`/tak dikenal = production → `EFN_ALLOW_PLAINTEXT` ditolak. ETL + `client_ssl_context` |
| `efn_ocr/config.py`, `vlm.py`, `consumer.py` | mTLS ke Ollama: `OLLAMA_CLIENT_CERT_FILE/KEY_FILE` → `requests.Session.cert`; wajib di production bila VLM aktif + https |
| `efn_ocr/metrics.py`, `__main__.py`, `healthcheck.py` | `METRICS_TLS_CLIENT_CA_FILE` → `start_http_server(client_cafile, client_auth_required=True)`; wajib di production; healthcheck membawa sertifikat worker sebagai klien |
| `services/etl/efn_etl/erasure.py` (baru) | job `privacy_erasure` (logika murni): baca request, count → `ALTER TABLE ... DELETE` → verifikasi 0 → publish; builder SQL whitelist + validasi hash |
| `efn_etl/amqp.py` (baru) | publisher pika amqps, `user_id=efn-etl`, confirms + `mandatory`, tanpa deklarasi queue |
| `efn_etl/config.py`, `adapters.py`, `runner.py`, `jobs.py`, `__main__.py` | config AMQP/erasure; `PgSource.query`, `ClickHouseErasureStore` (`mutations_sync=2`); `Job.identity_field` + pengecualian hash yang sedang dihapus; SQL `sales_fact_pos` ke kolom Odoo 19.0 |
| `services/etl/requirements.txt` | + `pika==1.3.2` |
| `services/etl/sql/odoo-readonly-grants.sql` (baru) | grant kolom minimum DB `efn_odoo` + index `(write_date, id)` (U1) |
| `infra/clickhouse/access/etl-access.sql` | + `GRANT ALTER DELETE` pada `competitor_prices`, `loyalty_events`; profil `allow_ddl = 1 CONST` (lihat risiko 1), `max_execution_time MAX 900`, `ALTER SETTINGS PROFILE` untuk profil lama |
| `infra/clickhouse/clickhouse-init.sh` (milik devsecops, ditambah satu blok) | TTL mart `CH_MART_RETENTION_YEARS` (default 5, 1..30) + `ttl_only_drop_parts=1`, `materialize_ttl_after_modify=0` |
| tests | worker `test_tls.py`, `test_config.py` (SEC-27, mTLS); ETL `test_config.py` (SEC-27, AMQP), `test_erasure.py` (baru: kontrak payload, idempotensi, verifikasi, isolasi error, SQL whitelist/injeksi, runner exclusion) |
| README worker & ETL | env baru, erasure, retensi, grant Odoo |

### G5.2 Env & secret baru (devsecops: compose + gen-secrets)
**etl** — secret baru `rabbitmq_password_etl`; network yang menjangkau `rabbitmq` (`data`).
| Env | Nilai produksi |
|---|---|
| `AMQP_URL` | `amqps://efn-etl@rabbitmq:5671` (default) |
| `RABBITMQ_PASSWORD_FILE` | `/run/secrets/rabbitmq_password_etl` |
| `AMQP_TLS_CA_FILE` | `/run/secrets/efn_ca_crt` (default) |
| `ETL_ERASURE_ENABLED` / `ETL_ERASURE_BATCH_SIZE` / `ETL_ERASURE_MUTATION_TIMEOUT_SECONDS` | `true` / `100` / `600` |
| `APP_ENV` | `production` (kosong pun kini production) |

**ocr-worker** — secret `tls_ocr_worker_crt/key` (sudah ada) dipakai juga sebagai sertifikat klien (EKU client sudah ada di gen-pki.sh).
| Env | Nilai produksi |
|---|---|
| `OLLAMA_CLIENT_CERT_FILE`, `OLLAMA_CLIENT_KEY_FILE` | `/run/secrets/tls_ocr_worker_crt`, `/run/secrets/tls_ocr_worker_key` |
| `METRICS_TLS_CLIENT_CA_FILE` | `/run/secrets/efn_ca_crt` |
Tanpa ketiganya worker **gagal start** di production. Sidecar `ollama-tls`: ganti `--disable-authentication` dengan `--allow-cn=ocr-worker` (+ text2sql nanti). Prometheus job `ocr-worker`: `tls_config.cert_file/key_file` (sertifikat prometheus).

**clickhouse-init** — env opsional `CH_MART_RETENTION_YEARS` (default 5; tunggu DPO).

### G5.3 Kebutuhan grant/kontrak untuk pihak lain
- **BFF / pemilik `platform.dsar_request` (PostgreSQL):** `GRANT SELECT (id, member_hash, status, stores_pending) ON platform.dsar_request TO efn_etl;` (kolom di WHERE juga butuh SELECT). Nilai status persis `processing`; store persis `clickhouse` di `stores_pending text[]`.
- **BFF (urutan):** tombstone/rewrite hash di `price_hunter_report.user_hash` dan `loyalty_ledger.member_ref_hash` (PDP_DSAR_DESIGN §5) **sebelum atau bersamaan** dengan set `processing`, dan hapus `clickhouse` dari `stores_pending` hanya setelah menerima `privacy.erasure.completed`. ETL hanya mengecualikan hash selama request `processing`; setelah itu baris sumber yang masih ber-hash asli akan dimuat ulang pada overlap/backfill.
- **BFF:** mendeklarasikan queue `privacy.erasure.completed` (+ `.dlq`, argumen §4) dan menerima hanya `user_id = efn-etl` untuk `store=clickhouse`. ETL bisa mem-publish ulang pesan dengan idempotency key sama tiap siklus sampai BFF memperbarui `stores_pending` → dedup `processed_message` wajib.
- **devsecops (RabbitMQ):** user `efn-etl`, vhost `/`: configure `^$`, write `^amq\.default$` (izin write dicek pada exchange default), read `^$`.
- **devsecops (Odoo DB):** jalankan `services/etl/sql/odoo-readonly-grants.sql` sebagai `efn_odoo` setiap deploy setelah install/upgrade Odoo; baru kemudian `ETL_ENABLE_ODOO=true`.
- **devsecops (ClickHouse):** tidak ada user baru; `etl-access.sql` sudah dijalankan otomatis oleh `clickhouse-init.sh`.

### G5.4 Kolom Odoo 19.0 (diverifikasi terhadap sumber GitHub 19.0, ringkasan WebFetch)
`pos_order`: `config_id` kini **stored** (join `pos_session` dihapus), `state` = `draft|cancel|paid|done` (`invoiced` dihapus dari filter), `pos_reference` bisa NULL sebelum sync → `COALESCE(pos_reference, name)`. `pos_order_line`: `full_product_name, qty, price_unit, discount, price_subtotal, price_subtotal_incl, total_cost` ada. `product_category.complete_name` stored. Verifikasi berasal dari ringkasan model kecil atas file sumber — skrip grant gagal keras (`ON_ERROR_STOP`) bila kolom tidak ada, jadi deploy pertama adalah verifikasi definitifnya.

### G5.5 Risiko / belum terverifikasi
1. **`allow_ddl`:** saya menyimpulkan ClickHouse 24.8 menganggap `ALTER DELETE` sebagai DDL (diblok `allow_ddl=0`) — belum dicek. Profil dilonggarkan ke `allow_ddl=1`; batas nyata = GRANT. Alternatif bila ditolak reviewer: user `efn_etl_erasure` terpisah.
2. `mutations_sync=2` + `max_execution_time` pada `client.command(settings=...)` di clickhouse-connect 0.8.9; mutation yang timeout akan diterbitkan ulang di siklus berikutnya (mutation duplikat tidak berbahaya, hanya boros).
3. Hash disisipkan sebagai literal (regex `^[0-9a-f]{64}$`), bukan parameter server-side, karena dukungan parameter pada mutation belum terverifikasi.
4. `fact_sales_line.customer_id` (id `res.partner`) tidak dihapus: setelah Odoo menganonimkan partner, id tidak lagi terhubung ke orang. Perlu konfirmasi DPO; bila harus dihapus, ETL butuh SELECT `(id, efn_member_hash)` pada `res_partner`.
5. Request > `ETL_ERASURE_BATCH_SIZE` yang `processing` bersamaan tidak ikut dikecualikan pada siklus itu (urut `id`).
6. TTL: `MODIFY TTL` dengan `materialize_ttl_after_modify=0` dan `toDateTime(created_at)` untuk `DateTime64` belum diuji di 24.8; baris lama yang sudah kedaluwarsa baru terbuang saat merge berikutnya (`merge_with_ttl_timeout`, default 4 jam).
7. Metrik worker: prometheus-client tidak bisa membatasi CN/SAN klien — setiap sertifikat dari CA internal diterima (CA satu tingkat, SEC-23). Kontrol CN penuh butuh sidecar ghostunnel `--allow-cn=prometheus`.
8. `CREATE INDEX CONCURRENTLY IF NOT EXISTS`: bila pembuatan pertama gagal, index INVALID tertinggal dan tidak dibuat ulang otomatis (cek `pg_index.indisvalid`).
9. Semua test belum dijalankan.

---

## Gelombang 5b — topologi milik broker, verifikasi mTLS Ollama (5 Okt 2026)

| File | Perubahan |
|---|---|
| `efn_ocr/publisher.py` | `declare_topology` dihapus → `verify_topology` (`queue_declare(passive=True)`) hanya untuk `ocr.requested.dlq` + `ocr.requested`; 404 → `TopologyMissingError` dengan pesan jelas; error broker lain diteruskan |
| `efn_ocr/consumer.py` | memakai `verify_topology`; `TopologyMissingError` → log error + `SystemExit(2)` (bukan loop reconnect) |
| `efn_ocr/messages.py` | `queue_arguments` kini hanya acuan kontrak (docstring) |
| `tests/test_config.py` | passive-only & hanya queue konsumsi, gagal jelas per queue hilang (parametrize), error 403 diteruskan |
| `tests/test_tls.py` | `test_ollama_request_actually_carries_client_cert`: adapter transport palsu memastikan setiap `POST /api/generate` membawa `cert=(crt,key)` dan `verify=<CA internal>` (bukan hanya atribut session) |
| `README.md` worker | bagian topologi |

Hasil cek `vlm.py`: `OllamaVlm` menyetel `Session.cert = (crt, key)`; `requests` menggabungkan `Session.cert` ke setiap request (`merge_environment_settings`, tidak bergantung `trust_env`). `consumer.build_processor` meneruskan `OLLAMA_CLIENT_CERT_FILE/KEY_FILE` dari Settings; production gagal start tanpa keduanya.

Untuk devsecops: user `efn-ocr` cukup configure `^$`, read `^ocr\.requested$`, write `^amq\.default$` (retry publish ke `ocr.requested` + publish loyalty/notify lewat default exchange). Belum diverifikasi: bahwa passive `queue.declare` di RabbitMQ 3.13/4.x tidak memerlukan izin `configure` (perkiraan saya tidak; bila ternyata perlu, berikan configure `^ocr\.requested(\.dlq)?$`). ETL sejak Gelombang 5 tidak mendeklarasikan queue apa pun.

## Gelombang 12 — VLM performa, salinan audit tamper-evident, mart konsolidasi GL (7 Okt 2026)

### 1. VLM (services/ai-ocr-worker)
- `imaging.encode_for_vlm` (sisi maks `OCR_VLM_MAX_SIDE`=1024, JPEG q85, tidak pernah upscale; trim tepi polos opsional `OCR_VLM_TRIM_BORDER`=false), dipanggil `processor` sebelum `vlm.extract` (gagal encode -> `flagged/vlm_unavailable`, bukan crash).
- `OllamaVlm`: `options.num_predict`=128, `num_ctx`=2048, `keep_alive`=30m (env `OLLAMA_NUM_PREDICT/NUM_CTX/KEEP_ALIVE`); `Settings` gagal start bila `num_ctx` tidak muat token gambar sisi maks + 200 + num_predict.
- Pengukuran dev (Ollama 0.35.1 CPU, qwen2.5vl:3b, 3 foto smoke baru per varian): **sebelum 66,7 s/foto, 1333 token prompt, 3/3 benar -> sesudah (default G12) 59,1 s/foto, 1166 token, 3/3 benar (-11 %)**. 768/512 px tidak lebih cepat (token tetap 1166: Ollama menormalkan ukuran gambar) dan 768 kehilangan sufiks nama (1/3). Kesimpulan: resize bukan solusi throughput; butuh GPU (README §Performa VLM: L4/RTX ≥ 8 GB, perkiraan 1–3 s/foto, target p95 < 10 s). Angka GPU belum diukur.
- `METRICS_BIND_ADDRESS` (permintaan lead): literal IP, default `0.0.0.0` bila HTTPS metrik, `127.0.0.1` bila plaintext dev; non-loopback tanpa TLS ditolak (`TlsConfigError`); diteruskan ke `start_http_server(addr=)`.
- Skenario `run-e2e-smoke.sh v` **belum dijalankan dengan kode baru** (butuh rebuild/recreate container ocr-worker; tidak dilakukan agar stack yang sedang dipakai tidak terganggu). Pengukuran dilakukan dengan skrip benchmark lewat `docker exec` ke Ollama yang sama (mTLS, payload identik).

### 2. Salinan audit tamper-evident (services/etl/efn_etl/audit.py, audit_job.py)
- Tabel ClickHouse (`infra/clickhouse/init/03-gl-audit.sql`): `audit_log_copy`, `audit_log_line_copy` (MergeTree biasa, tanpa partisi -> INSERT atomik), `audit_recon_run`.
- `payload_hash = sha256(canonical_row)` (JSON kanonik kolom log + `lines_digest`), `row_hash = sha256(prev_hash || payload_hash)` per `chain_company` (company log; 0 = tanpa company), `chain_seq`. Kolom yang berubah sah (write_*, FK set-null `model_id/rule_id/field_id`, kolom related baris) tidak di-hash. Kepala rantai dicatat `EFN_AUDIT_CHAIN_HEAD` (Loki = jangkar eksternal).
- Late commit: id ≤ max tersalin yang muncul dalam `ETL_AUDIT_LATE_WINDOW_SECONDS` disalin `ingest_kind=late`; log baru setelah `ETL_AUDIT_SETTLE_SECONDS`. Crash antara insert baris dan log tidak menggandakan baris.
- PII: log orang/is_personal -> `res_id`/`res_name` NULL; baris ter-mask atau ref ke model orang -> nilai/ref NULL (`redacted=1`), hanya hash. Kebijakan erasure: gap karena `_efn_audit_purge_records` sah bila ada log purge `erasure: N log` untuk model itu setelah log dibuat (dalam anggaran N); retensi sah bila umur ≥ 365 hari (dalam anggaran `retensi: N log`); log purge/model kontrol hilang = selalu `unexplained`. Gap sequence PostgreSQL (rollback) bukan indikasi. Kelas & alert: lihat `services/etl/README.md`.
- Rekonsiliasi harian -> `audit_recon_run` + log `EFN_AUDIT_RECON {json}` (WARNING bila `unexplained|modified|unseen|chain_problems` > 0). **devsecops**: rule Loki `{service="etl"} |= "EFN_AUDIT_RECON" |= "WARNING"` dan `|= "job=audit_copy gagal"`; opsional bandingkan `EFN_AUDIT_CHAIN_HEAD` antar hari.
- Retensi salinan: TTL `CH_AUDIT_COPY_RETENTION_YEARS` (default 10, clickhouse-init.sh) — usulan menunggu DPO/Q-49. Role `audit_copy_reader` dibuat, tidak diikat ke user (keputusan klien).

### 3. Mart konsolidasi GL (services/etl/efn_etl/gl.py)
- Kolom Odoo 19 diverifikasi dari DB dev (information_schema): `account_account.code_store` jsonb per company root, `name` jsonb, relasi `account_account_res_company_rel`; `account_move_line.parent_state`, `balance`, dll.
- Job `companies` -> `dim_company`, `gl_accounts` -> `dim_gl_account` (kode per company), `gl_lines` (cursor `account_move_line.write_date`) + `gl_lines_by_move` (cursor `account_move.write_date`) -> `fact_gl_line` (ReplacingMergeTree(version_ts), **partisi per company** karena tanggal draft bisa berubah). Semua state dimuat; view hanya `posted`. `partner_id` tidak disalin; `ic_company_id` = company grup lawan (partner = partner company lain).
- View `v_gl_consolidated` (Metabase): period, fiscal_year, company, akun, `is_intercompany`, `counterparty_company_id`, debit/credit/balance, `balance_after_elimination`. Tidak ditambahkan ke semantic layer Text-to-SQL (bebas PII, tetapi laporan keuangan lintas PT tanpa pembatasan per company -> Q-6); tabel baru masuk `protected_tables`.

### Grant yang dibutuhkan
- PostgreSQL (Odoo, `services/etl/sql/odoo-readonly-grants.sql`, dijalankan deploy sebagai `efn_odoo` **setelah `-u efn_audit` ke 19.0.1.2.0** karena `origin_user_id`): SELECT kolom `account_move_line`, `account_move`, `account_account`, `account_account_res_company_rel`, `res_company` (+partner_id, parent_id, parent_path, currency_id, fiscalyear_*, write_date), `res_currency(id,name)`, `efn_audit_log`, `efn_audit_log_line`; index `CONCURRENTLY` `(write_date, id)` pada `account_move_line` dan `account_move`.
- ClickHouse (`etl-access.sql`): `efn_etl` SELECT+INSERT `fact_gl_line`, `dim_gl_account`, `dim_company`, `audit_log_copy`, `audit_log_line_copy`, `audit_recon_run`; REVOKE defensif ALTER DELETE/UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE pada tabel audit. `metabase-access.sql`: `v_gl_consolidated` + tabel GL + `audit_recon_run`. Role `audit_copy_reader`.
- Env baru ETL: `ETL_ENABLE_GL`, `ETL_AUDIT_COPY_ENABLED` (default false), `ETL_AUDIT_*`. Compose belum diubah (milik devsecops): aktifkan dua flag di service `etl` setelah grant diterapkan.

### Validasi
- pytest (python:3.12-slim digest Dockerfile): ai-ocr-worker 184 lulus, etl 94 lulus, text2sql 93 lulus; ruff 0.16.10 check + format bersih (ketiga service).
- Stack dev: `clickhouse-init` dijalankan ulang (`compose run --rm --no-deps`, idempoten) -> 03-gl-audit.sql, TTL, grant diterapkan; grant PG diterapkan ke DB dev (tanpa kolom `origin_user_id` karena efn_audit dev masih 19.0.1.1.0). ETL baru dijalankan sekali di dalam container `efn-etl-1` (kode lewat /tmp, SQL `origin_user_id` diganti `NULL` untuk dev): `dim_company` 5, `dim_gl_account` 523, `fact_gl_line` 0 (DB dev belum punya jurnal), `audit_log_copy` 20 log (2 rantai: company 0 seq 14, company 4 seq 6), baris 21 (8 diredaksi, 0 nilai bocor), rekonsiliasi 0 anomali; run kedua: 0 baris baru, kepala rantai identik. `efn_etl` mencoba ALTER DELETE / DELETE / TRUNCATE / ALTER UPDATE / DROP / OPTIMIZE pada tabel audit -> semua ditolak (497 ACCESS_DENIED). SQL view `v_gl_consolidated` dieksekusi (0 baris).
- Catatan: Docker Desktop berhenti di tengah sesi dan dinyalakan ulang oleh agent ini (container kembali otomatis); tidak ada `down`/recreate service.

### Belum terverifikasi
- GL dengan jurnal nyata (posted/cancel, inter-company, tahun fiskal non-Desember); grant Metabase pada view (diuji hanya sebagai `efn_etl` untuk SQL body).
- Kolom `origin_user_id` di DB nyata (menunggu upgrade efn_audit 19.0.1.2.0).
- Skenario tamper nyata di PostgreSQL (hanya unit test) dan alert Loki (rule belum dibuat devsecops).
- `run-e2e-smoke.sh v` dengan image ocr-worker baru; angka GPU.

---

## Gelombang 13 — Mart Fase 1, 3 dashboard Metabase sebagai kode, heartbeat ETL (7 Okt 2026)

Konteks: docs/01 §7 Fase 1 "ETL dasar + 3 dashboard Metabase" (daftar final dashboard C-level = Q-47, masih terbuka), SEC-52 butir 1 (dead-man), tambahan lead: verifikasi sink audit v2 (HMAC).

### 1. Mart (infra/clickhouse/init/04-fase1-marts.sql, services/etl)
| Bagian | Tabel / job | View (semantic layer Metabase) |
|---|---|---|
| (a) Penjualan POS | `fact_sales_line` + `order_id`, `is_refund`, `line_kind` (product/discount/reward/points/tip), `discount_amount`; job baru `pos_payments` → `fact_pos_payment`, `pos_outlets` → `dim_outlet`, `products` → `dim_product` (`efn_etl/pos.py`) | `v_pos_sales_daily` (hari×PT×outlet×kategori: kotor, diskon baris, promo, poin EFN, refund, bersih, pajak, HPP, qty), `v_pos_outlet_daily` (order, basket), `v_pos_sales_hourly`, `v_pos_payment_daily` |
| (b) Stok & kedaluwarsa | job `stock_snapshot` (`efn_etl/stock.py`): snapshot penuh quant internal + lot + orderpoint dalam satu transaksi REPEATABLE READ, penanda `stock_snapshot_run` ditulis terakhir; `fact_stock_snapshot`, `fact_orderpoint_snapshot` | `v_stock_daily`, `v_stock_current`, `v_stock_expiring` (≤ 7 hari, termasuk yang lewat), `v_stock_below_reorder` |
| (c) Price Hunter | `dim_product.list_price` + `competitor_prices` | `v_price_index` (match SKU atau nama dinormalisasi; indeks vs harga jual & vs harga POS 30 hari), `v_price_hunter_match_rate` |
| Heartbeat | `etl_job_run` (TTL 90 hari) | `v_etl_freshness` |

Mengapa snapshot stok (bukan incremental): Odoo menghapus quant ber-qty nol, jadi incremental `write_date` tidak pernah melihat stok habis. Kolom Odoo 19 diverifikasi lewat `information_schema` DB dev (product_expiry + pos_loyalty terpasang). Backfill `fact_sales_line` di dev: watermark epoch → 84 baris dimuat ulang (prosedur di README ETL). TTL mart 5 tahun juga untuk tabel baru (`clickhouse-init.sh`). **Kontrak baru untuk Odoo engineer:** produk "Poin EFN" (docs/04 §9.3) memakai `default_code = EFN-POINTS` (atau set `ETL_POINTS_PRODUCT_CODES`).

### 2. Grant baru
- **Odoo** (`sql/odoo-readonly-grants.sql`, kolom per kolom; diterapkan ke DB dev sebagai `efn_odoo` → OK): `pos_order_line(refunded_orderline_id, is_reward_line)`, `pos_order(is_refund)`, `pos_payment(id, pos_order_id, payment_method_id, amount, is_change, payment_date, write_date)` — TANPA card_no, cardholder_name, card_brand/type, transaction_id, payment_ref_no, authcode, issuer_bank, ticket; `pos_payment_method(id, name, payment_method_type, is_cash_count)`; `pos_config(id, name, company_id, warehouse_id, discount_product_id, tip_product_id, active, write_date)`; `product_product(+default_code, active, standard_price, write_date)`; `product_template(+name, company_id, list_price, type, is_storable, tracking, use_expiration_date, sale_ok, available_in_pos, active, write_date)`; `stock_quant(id, company_id, location_id, product_id, lot_id, quantity, reserved_quantity, in_date)` — TANPA owner_id/user_id; `stock_lot(id, name, expiration_date)`; `stock_location(id, complete_name, usage, warehouse_id)`; `stock_warehouse(id, name)`; `stock_warehouse_orderpoint(id, company_id, warehouse_id, location_id, product_id, product_min_qty, product_max_qty, trigger, active)`; index `efn_etl_pos_payment_write_date_id_idx`.
- **ClickHouse:** `etl_writer` SELECT+INSERT pada 7 tabel baru; `metabase_reader` SELECT 11 view baru + tabel dasarnya (tanpa identitas) + 4 kolom baru `fact_sales_line` (tetap tanpa `customer_id` — diverifikasi ditolak 497; INSERT juga 497). Semantic layer Text-to-SQL TIDAK diubah (di luar batas file).

### 3. Dashboard (infra/metabase) — diuji di stack dev
`provision.py` (stdlib, idempoten; dijalankan 2× tanpa duplikat; 28/28 kartu `completed`; filter outlet/tanggal/PT diuji via API). Koneksi ClickHouse `efn_metabase`: HTTPS 8443 + `sslmode=strict` + CA internal (CA salah → "Failed to create connection", diverifikasi). Skrip → Metabase lewat Traefik `bi.efn.local` (HTTPS terverifikasi; CA server salah → gagal). Driver ClickHouse resmi 1.52.0 (Apache-2.0) dipin sha256 di `infra/metabase/Dockerfile`.

Kartu (semua SQL native ke `efn_dwh.v_*`; validator menolak relasi lain/DDL/fungsi tabel):
- **Penjualan Harian Outlet** (filter: dari/sampai tanggal, PT nama/kode brand, outlet): Penjualan bersih, Jumlah order, Rata-rata nilai belanja, Retur/refund (scalar, `v_pos_outlet_daily`); Tren penjualan bersih harian per outlet (line); Metode pembayaran (pie, `v_pos_payment_daily`); Penjualan per kategori (row, `v_pos_sales_daily`); Pola jam WIB (bar, `v_pos_sales_hourly`); Diskon/promo/poin EFN/refund per hari (bar stacked, `v_pos_sales_daily`); Ringkasan outlet harian (table); Kesegaran data ETL (`v_etl_freshness`).
- **Stok & Kedaluwarsa** (filter: tanggal, PT, gudang/outlet): Nilai stok saat ini, Lot kedaluwarsa ≤ 7 hari, Produk di bawah titik pesan ulang (scalar); Produk mendekati kedaluwarsa (table, `v_stock_expiring`); Stok di bawah titik pesan ulang (table, `v_stock_below_reorder`); Nilai stok harian per gudang (line, `v_stock_daily`); Stok saat ini per produk & gudang (table, `v_stock_current`); Kesegaran data ETL.
- **Price Hunter** (filter: tanggal, pesaing, produk): Laporan valid (`v_price_hunter_quality`), Indeks harga rata-rata (`v_price_index`), Laporan valid termatch % (`v_price_hunter_match_rate`) (scalar); Indeks harga per produk & pesaing (table); Indeks rata-rata per pesaing (row); Tren indeks harian (line); Kualitas laporan per status (bar stacked); Produk dilaporkan belum termatch (table, `v_competitor_prices` minus `v_price_index`); Kesegaran data ETL.

Data dev tambahan (Odoo company 4, lewat `odoo shell`): produk `ETL-DEV-UHT` (lot ETLDEV-L1 kedaluwarsa +3 hari, L2 +60 hari, orderpoint min 50/max 120) dan `ETL-DEV-INDOMIE` "Indomie Goreng" (match laporan Price Hunter smoke → indeks 94,6).

### 4. Heartbeat (SEC-52 butir 1) — nama metrik untuk devsecops
Log Loki `service="etl"`, satu baris logfmt per job per siklus (INFO bila ok, WARNING selain itu), plus baris `efn_dwh.etl_job_run`:
`EFN_ETL_HEARTBEAT job=<job> status=<ok|error|partial> rows=<n> pages=<n> duration_ms=<n> watermark=<iso|-> ts=<iso>`.
Job: `cycle` (loop hidup), `competitor_prices`, `loyalty_events`, `sales_fact_pos`, `pos_outlets`, `products`, `pos_payments`, `companies`, `gl_accounts`, `gl_lines`, `gl_lines_by_move`, `stock_snapshot`, `audit_copy`, `audit_recon`, `privacy_erasure`. Nama job divalidasi `^[a-z0-9_]{1,64}$`. Usulan alert: `absent_over_time({service="etl"} |= "EFN_ETL_HEARTBEAT" | logfmt | job="<job>" | status="ok" [<ambang>])` — cycle 45m, sales_fact_pos 45m, audit_copy 1h (prod), stock_snapshot 3h, audit_recon 26h; plus `sum by (job) (count_over_time({service="etl"} |= "EFN_ETL_HEARTBEAT" | logfmt | status!="ok" [1h])) >= 2`.

### 5. Sink audit v2 (tambahan lead)
`efn_etl/sink.py` + 16 test: verifikasi `mac` (kanonik identik `efn_audit.models.base.sink_canonical`) dan rekonsiliasi → `sink_mac_invalid`, `sink_missing_log`, `log_missing_sink`. **Belum di-wire ke `EFN_AUDIT_RECON`.** Spesifikasi wiring: (1) devsecops mount secret `efn_audit_sink_key` ke `etl` + env `EFN_AUDIT_SINK_KEY_FILE`, sertifikat klien `etl` yang di-allow `loki-tls`, akses network observability; (2) ETL membaca `GET /loki/api/v1/query_range?query={service="odoo"} |= "EFN_AUDIT_SINK"` per jendela recon (paging `limit`/`start`, ≤ retensi stream sink); (3) PG: `efn_audit_log` model kontrol + `purge` di jendela yang sama (grant sudah ada); (4) hitungan digabung ke payload `EFN_AUDIT_RECON` + kolom baru `audit_recon_run`; WARNING bila > 0. Kunci belum ada di Odoo → `mac` null → semua baris `sink_mac_invalid` (sengaja tidak diam).

### 6. Validasi
- `services/etl`: ruff check + format bersih; pytest 154 lulus (container python:3.12-slim). `infra/metabase`: ruff bersih; pytest 23 lulus.
- `clickhouse-init` dijalankan ulang di dev (semua skema/akses OK); image ETL dibangun ulang; satu siklus penuh: semua heartbeat `status=ok`.

### 7. Belum / perlu pihak lain
- **devsecops:** compose `metabase` → `build: ../metabase` (driver ClickHouse; di dev jar disalin manual dengan `docker cp` dan hilang saat recreate); secret `metabase_admin_password` di `scripts/secrets.list` (dev: dibuat lokal `secrets/metabase_admin_password.txt`); langkah `provision.py` di deploy segera setelah Metabase pertama hidup (instance yang belum di-setup bisa diklaim siapa pun di subnet staf); alert heartbeat; job CI untuk `infra/metabase/tests`. Catatan: `docker restart efn-metabase-1` membuat sidecar `metabase-tls` (network_mode service) unhealthy, jadi sidecar juga harus di-restart.
- Q-47 (dashboard C-level), Q-54 (SSO/password Metabase), izin per grup pada collection.
- WITA/WIT per outlet belum dibedakan; reorder memakai on-hand (bukan forecast Odoo); filter berupa teks (bukan dropdown).
- View baru belum masuk semantic layer Text-to-SQL.

## Gelombang 14 — Rekonsiliasi loyalty ↔ pos.order (sisa SEC-61), wiring verifikasi sink + jangkar (SEC-51/52), dashboard refund poin (8 Okt 2026)

Tanpa commit. Tidak ada volume yang dihapus.

### File
- Baru: `services/etl/efn_etl/loki.py` (klien `query_range` stdlib, mTLS CN `etl`, paginasi aman batas timestamp,
  `truncated` dilaporkan), `services/etl/efn_etl/loyalty_recon.py`, `infra/clickhouse/init/05-loyalty-recon.sql`,
  `tests/test_loki.py`, `tests/test_loyalty_recon.py`.
- Diubah: `sink.py` (jangkar `EFN_AUDIT_CHAIN_HEAD` + orkestrasi `run_sink_check`), `audit_job.py` (hasil sink digabung ke
  `EFN_AUDIT_RECON` + kolom `audit_recon_run`; Loki gagal = `sink_error=1`, WARNING), `config.py`, `adapters.py`,
  `__main__.py`, `sql/odoo-readonly-grants.sql` (bagian G14), `infra/clickhouse/init/04-fase1-marts.sql`
  (`refund_goods`, `points_returned_idr` di `v_pos_sales_daily`/`v_pos_outlet_daily`), `access/etl-access.sql`,
  `access/metabase-access.sql`, `infra/metabase/dashboards/01-penjualan-harian-outlet.json`, README ETL, tes.

### Kontrak untuk devsecops (nama baris / alert yang diusulkan)
- Baris loyalty: `<YYYY-MM-DD HH:MM:SS,mmm> (INFO|WARNING|ERROR) efn_etl.loyalty_recon EFN_LOYALTY_RECON_ETL {"status":"ok"|"error",...}`
  dari `{service="etl"}`. Usul promtail: stream `efn_loyalty`, label `loyalty_event="etl_recon"` (regex berjangkar,
  kunci pertama `"status"`). Field: `anomalies` (critical), `open_items` (warning), `kinds{kind:n}`, `sample_order_ids`,
  `sample_ledger_ids` (angka saja). Alert: `| json | anomalies > 0` → critical (Finance); `open_items > 0` → warning
  harian; dead-man `job="loyalty_recon" status="ok"` absen 26 jam (heartbeat `EFN_ETL_HEARTBEAT`).
- `EFN_AUDIT_RECON` tetap diawali `{"max_log_id":` (regex promtail lama cocok); field baru `sink_error`, `sink_*`,
  `anchor_*`. Alert lama (WARNING) sudah menangkap anomali sink/jangkar.
- Compose dev: tambahkan `ETL_LOYALTY_RECON_NOT_BEFORE` (go-live loyalty POS; dev `2026-10-07T07:09:00Z`) — tanpa itu
  42 order smoke lama (sebelum `efn_pos_loyalty_ledger` dipasang) muncul sebagai `paid_member_order_without_earn`.
- `deploy.sh`: grant G14 harus dijalankan setelah `efn_pos_loyalty_ledger` terpasang (ON_ERROR_STOP).
- Temuan promtail: tahap redaksi nomor HP berjalan SEBELUM tahap sink `efn_audit_sink` → sink yang memuat pola
  `08xxxxxxxx` (mis. di `note`) akan rusak dan terhitung `sink_mac_invalid`. Usul: cocokkan sink sebelum redaksi
  (seperti jangkar; sink tidak memuat nilai).

### Uji di stack dev (8 Okt)
- pytest ETL **192 passed**, metabase **23 passed**; `ruff check` + `ruff format --check` bersih (container python:3.12).
- Grant G14 diterapkan ke DB dev (sebagai `efn_odoo`); `clickhouse-init` ulang sukses (05 + access).
- Siklus ETL (`ETL_RUN_ONCE`, recon dipaksa): Loki mTLS berhasil. Tanpa cutoff: `earn_without_paid_order` 30.326 =
  earn load test BFF tanpa order Odoo (terdeteksi benar; 22 earn asal Odoo cocok dengan 22 outbox `done`),
  `paid_member_order_without_earn` 42 = order sebelum modul loyalty dipasang. Dengan `NOT_BEFORE`: **anomalies 0**,
  open_items 3 (2 order `review` — earn_limit & retur tanpa struk dari smoke G13c, 1 outbox `failed` earn_limit).
- Anomali sengaja: baris Poin EFN order paid #145 diubah −6.000→−9.000 → `points_line_value_mismatch` 1 (WARNING),
  lalu dikembalikan (−6.000, diverifikasi); run bersih terakhir anomalies 0.
- Recon audit: `sink_mac_invalid` 3 = baris sink injeksi QA (log_id 990001/990101/990102, mac null/tanpa mac);
  `log_missing_sink` 5 = log `purge` erasure res.partner id 39/46/55/62/71 yang dibuat lewat `odoo shell`
  (`docker exec`, stdout tidak tertangkap promtail) — deteksi benar; `anchor_missing` 1 = jangkar palsu injeksi QA
  (company 990). Setelah jendela 25 jam lewat, sink 0 anomali; 20 jangkar cocok.
- ClickHouse: `v_pos_outlet_daily` 7 Okt refund uang 341.000 = barang 350.000 − poin dikembalikan 9.000.
  `metabase-provision` exit 0, semua kartu termasuk 5 kartu baru `completed`.
- `efn-etl-1` di-recreate dengan image baru (healthy/starting).

### Belum / asumsi
- Saldo negatif per member, nominal restore/clawback refund (pembulatan BFF), dan earn yang dicatat > lookback setelah
  order belum dicek. Admin `base.group_system` memanggil `_efn_post` (SEC-61 butir 3) tidak tertutup job ini, hanya
  terdeteksi bila tidak ada order Odoo yang cocok.
- Promtail stream/alert untuk `EFN_LOYALTY_RECON_ETL` belum ada (devsecops). Jangkar tidak ber-HMAC (alarm palsu
  mungkin, penyembunyian tidak). CI GitHub belum dijalankan.

## Gelombang 16 — pip-audit CI merah: Pillow 12, requests, urllib3, pytest (9 Okt 2026)

**Pemicu:** job CI `Python (ruff, compile, pip-audit)` gagal di `services/ai-ocr-worker/requirements.txt`
(47 temuan di 3 paket). Audit ulang semua requirements di container menemukan juga pytest (dev) dan `services/odoo`.

### Versi
| File | Paket | Lama | Baru | Advisory |
|---|---|---|---|---|
| ai-ocr-worker/requirements.txt | Pillow | 11.3.0 | **12.3.0** | PYSEC-2026-165, -2249..-2257, -2874, -3451/3453/3454, -3493..-3496 |
| ai-ocr-worker/requirements.txt | requests | 2.32.4 | **2.34.2** | PYSEC-2026-2275 (fix 2.33.0) |
| ai-ocr-worker/requirements.txt | urllib3 | 2.5.0 | **2.8.0** | PYSEC-2026-141, -1994, -1996, -1998, -4175, -4177 |
| {ai-ocr-worker,etl,text2sql}/requirements-dev.txt | pytest | 8.3.4 | **9.0.3** | PYSEC-2026-1845 |

etl & text2sql runtime: tidak ada temuan. Dependensi transitif terpasang: ImageHash 4.3.1, numpy 2.5.3, scipy 1.18.1, PyWavelets 1.10.0, minio 7.2.12.

### Perubahan kode
Tidak ada. `efn_ocr/imaging.py` sudah memakai `Image.Resampling.LANCZOS` (konstanta lama `Image.ANTIALIAS`/`LANCZOS`
top-level tidak dipakai); `Image.open`, `ImageOps`, `ImageChops`, `DecompressionBombError`, `MAX_IMAGE_PIXELS` kompatibel
Pillow 12. Klien urllib3 MinIO (`PoolManager`, `Timeout`, `Retry`, `cert_reqs`/`ca_certs`) dan `requests.Session` kompatibel.

### Validasi (container `python:3.12-slim@sha256:05cda977…`, digest Dockerfile terbaru, Python 3.12.15)
`ruff==0.16.10 check` + `format --check` bersih · `compileall` OK · pytest: ai-ocr-worker **184 passed**, etl **192 passed**,
text2sql **93 passed** · `pip-audit -r requirements.txt --strict` dan `-r requirements-dev.txt --strict`: **No known vulnerabilities** untuk ketiga service.

### Sisa (di luar batas file saya)
- `services/odoo/requirements.txt`: `requests==2.32.3` rentan (PYSEC-2026-1872 fix 2.32.4, PYSEC-2026-2275 fix 2.33.0) →
  job pip-audit CI akan tetap merah setelah ai-ocr-worker lolos. Perlu dinaikkan ke `requests==2.34.2` oleh pemilik Odoo
  (cek kompatibilitas dengan urllib3 bawaan image odoo:19.0).
- Test OCR end-to-end dengan Tesseract/PaddleOCR + Pillow 12 di image runtime belum dijalankan (hanya unit test).

## Gelombang 16b — Upgrade Metabase 0.52.4 → v0.58.35 LTS di stack dev (9 Okt 2026)

**Pemicu:** user menyetujui upgrade (gate Trivy G16). Dockerfile sudah di v0.58.35 (driver ClickHouse bawaan) sejak 7c048b4.

### Langkah & hasil nyata
1. **Backup** DB aplikasi `metabase`: `pg_dump -Fc` di container postgres → scratchpad sesi (di luar repo)
   `metabase-backup/metabase-pre-v0.58.dump` (514 081 byte, sha256 `1b23087d…fc5e48`, sama di container & host);
   `pg_restore --list` terbaca (1057 baris). Isi sebelum migrasi: 440 changeset Liquibase, 70 kartu, 4 dashboard.
   Image lama di-tag `efn/metabase:rollback-v0.52.4` (v0.52.4 + jar driver 1.52.0).
2. **Build + recreate** `metabase` + `metabase-tls` (`--no-deps --force-recreate`): Liquibase "Migration complete in
   14.1 s", "Initialization COMPLETE in 24.2 s", healthy; health check koneksi lama `EFN DWH (ClickHouse)` sukses.
   Satu-satunya ERROR = `/api/health 503` sesaat sebelum init selesai; WARN = penghapusan job Quartz lama (normal).
3. **Driver bawaan** = plugin `Metabase ClickHouse Driver 1.53.3` (`metabase/clickhouse/metabase-plugin.yaml` di
   metabase.jar). Perbedaan kunci `details`: `scan-all-databases` **dihapus**, diganti `enable-multiple-db` (default
   TRUE = sinkron semua database selain system). `ssl`, `dbname`, `additional-options` (ditempel ke URL JDBC) tetap.
   → `provision.clickhouse_details` kini mengirim `enable-multiple-db: False`. TLS tidak berubah: `ssl=true`,
   `sslmode=strict&sslrootcert=/run/secrets/efn_ca_crt`.
4. **Provision** (`$DC --profile core --profile data run --rm --no-deps metabase-provision`, python:3.12-bookworm digest
   compose, mTLS ke sidecar): exit 0, database diperbarui (id=2), 3 dashboard (16/8/9 kartu), **33/33 kartu
   `completed`**. Run kedua (idempoten) exit 0, tetap 33 kartu bertag + 4 dashboard aktif.
5. **Uji TLS** (skrip sekali pakai via `/api/database/validate`): CA internal → valid; CA sistem (salah) →
   "Failed to create connection". Detail tersimpan: `enable-multiple-db=false`, `ssl=true`; metadata hanya skema `efn_dwh`.
6. `tests/test_provision.py`: assert `enable-multiple-db is False` + `scan-all-databases` tidak dikirim. CI-equivalent
   (python:3.12-bookworm, ruff 0.16.10, pytest 8.3.4): ruff check/format bersih, **23 passed**.

### File diubah
`infra/metabase/provision.py` (details driver 1.53), `infra/metabase/tests/test_provision.py`,
`infra/metabase/README.md` (status terverifikasi, prosedur upgrade/backup/rollback, item "Belum" yang basi dihapus).

### Belum diverifikasi / risiko sisa
- Rollback (restore dump + image lama) **tidak diuji coba** — migrasi sukses sehingga tidak dijalankan.
- `MB_ENCRYPTION_SECRET_KEY` tidak diset → detail koneksi (password ClickHouse read-only) plaintext di DB aplikasi dan
  di dump backup; bertentangan dengan prinsip enkripsi at rest → devsecops (secret baru + entrypoint wrapper).
- Dump backup di scratchpad sesi tidak terenkripsi; hapus setelah upgrade dinyatakan stabil.
- Rendering UI dashboard di browser (filter, visualisasi) tidak dicek — hanya eksekusi kartu via API.
- Staging/prod belum di-upgrade; ikuti urutan README (backup dulu).

## Gelombang 18 — W-19 retensi data (ClickHouse TTL 10 th / pseudonim 5 th, lokasi Price Hunter 90 h, Metabase) (10 Okt 2026)

Sumber: `docs/10_RENCANA_GELOMBANG_18_PLUS.md` W-19, `docs/compliance/PDP_DATA_INVENTORY.md` v7 §0a/§3, keputusan
Q-6/Q-49 (DWH 10 th), Q-P7 (pseudonim), Q-P8 (lokasi 90 h), Q-P14 (riwayat Metabase 10 th).
**Penafsiran Q-P7 vs Q-6** = sama dengan usulan lead: *baris fakta 10 th, kolom pseudonim dikosongkan setelah 5 th*.
Tidak ada penafsiran berbeda.

### File
| File | Perubahan |
|---|---|
| `infra/clickhouse/clickhouse-init.sh` | Blok retensi (milik data-AI) ditulis ulang: `CH_MART_RETENTION_YEARS` default **10**; baru `CH_PSEUDONYM_RETENTION_YEARS` (5, 1..mart) dan `CH_GEO_RETENTION_DAYS` (90, 1..3650); TTL kolom `competitor_prices.user_hash/geo_lat/geo_lng`, `loyalty_events.member_hash`, `fact_sales_line.customer_id`; `MATERIALIZE TTL` sekali per tabel hanya bila definisi berubah (hash `create_table_query`); migrasi sekali `loyalty_events`. Blok salinan audit 10 th **tidak disentuh**. |
| `infra/clickhouse/init/02-loyalty-etl.sql` | `loyalty_events` (volume baru) `ORDER BY (event_id)` + skip index `bloom_filter` `member_hash` + `ttl_only_drop_parts=1`. |
| `services/etl/efn_etl/jobs.py` | `AgeOut` (lapisan kedua): saat transformasi, `geo_*` -> None setelah 90 h; `user_hash`/`member_hash` -> '' dan `customer_id` -> None setelah 5 th (validasi hash tetap fail-closed). Job dibangun lewat factory (`competitor_prices_job`, `loyalty_events_job`, `sales_fact_job`). |
| `services/etl/efn_etl/config.py`, `__main__.py` | Membaca `CH_PSEUDONYM_RETENTION_YEARS` (1..30), `CH_GEO_RETENTION_DAYS` (1..3650); di luar rentang = gagal start. |
| `services/etl/tests/test_jobs.py`, `test_config.py` | +13 test (batas 90 h / 5 th, 29 Feb, fail-closed pada baris lama, kebijakan custom, rentang env). Test lama kini memakai `now` tetap (tidak membusuk setelah Jan 2027). |
| `infra/metabase/provision.py` (+ `tests/test_provision.py`, +5 test) | `--deactivate-inactive-days` (default 90, 0 = mati) dan `--housekeeping-only`; `inactive_users()` murni; log hanya id (tanpa email). |
| `services/etl/README.md` §Retensi, `infra/metabase/README.md` §Retensi riwayat & akun tidak aktif | Dokumentasi. |
| `services/ai-ocr-worker/**` | **Tidak diubah**: worker hanya memakai `geo_*` saat memproses laporan (geofence, toleran NULL — `tests/test_fraud.py:38`) dan tidak menyimpannya. |

### Temuan teknis (diverifikasi di ClickHouse dev 24.8.14.39)
1. TTL kolom didukung pada tabel yang ada (`ALTER TABLE ... MODIFY COLUMN <kolom> <tipe SAMA> TTL ...`; tanpa tipe = syntax error karena `TTL` dibaca sebagai nama tipe). Tipe sama = perubahan metadata saja.
2. **TTL kolom ditolak untuk kolom kunci**: `Trying to set TTL for key column member_hash (ILLEGAL_COLUMN)` -> `loyalty_events` (dulu `ORDER BY (member_hash, event_id)`) dimigrasi ke `ORDER BY (event_id)`.
3. TTL kolom tetap berlaku dengan `ttl_only_drop_parts=1` (setting itu hanya untuk TTL baris). Insert baris yang TTL kolomnya sudah lewat **tidak** langsung dikosongkan (baru saat merge) -> alasan lapisan ETL `AgeOut`. Baris yang TTL barisnya sudah lewat dibuang saat insert.
4. Part lama tanpa info TTL tidak otomatis diproses -> `MATERIALIZE TTL` (mutation async) sekali saat definisi berubah.

### Uji nyata (stack dev)
- `clickhouse-init` (env `CH_MART_RETENTION_YEARS=10 CH_PSEUDONYM_RETENTION_YEARS=5 CH_GEO_RETENTION_DAYS=90` di-override karena `.env` dev masih 5): run 1 exit 0 — migrasi `loyalty_events` (30 440 event unik, `sum(delta_points)` 455 891 sama sebelum/sesudah; tabel lama di-DROP; ETL tetap jalan selama migrasi tanpa kehilangan baris), 7 tabel `MATERIALIZE TTL` (semua `is_done=1`). Run 2 exit 0: tanpa migrasi, tanpa mutation (idempoten).
- Grant setelah `EXCHANGE TABLES` utuh: `etl_writer` SELECT/INSERT/ALTER DELETE, grant kolom `text2sql_reader`/`metabase_reader` pada `loyalty_events`. `audit_log_copy` tetap `TTL ... + 10 YEAR`, tanpa TTL kolom.
- Baris uji sintetis: lokasi 100 h -> `geo_*` NULL & hash tetap; 6 th -> `user_hash`/`member_hash` ''; 11 th -> dibuang saat insert; 5 903 baris lokasi baru tetap. Baris uji dihapus.
- Image ETL dibangun ulang dan container di-recreate: heartbeat `competitor_prices`, `loyalty_events`, `sales_fact_pos`, `cycle` = ok.
- `metabase-provision` 2x exit 0 (33 kartu completed); akun uji `w19-probe@efn.local` dengan `date_joined` 120 h -> `--housekeeping-only` menonaktifkan (exit 0). Akun uji dibiarkan nonaktif di DB dev.
- pytest: ETL 205 passed / 1 skipped; Metabase 28 passed. ruff check + format bersih (ETL, Metabase).
- Data dev: event ledger `4`,`5` sempat ganda di mart (hash lama + hash sumber, sisa data uji lama); kunci baru menyatukannya ke hash lama -> dikoreksi manual ke nilai sumber. Dengan `ORDER BY event_id`, perubahan hash di sumber kini menimpa baris lama (sebelumnya menghasilkan dua baris).

### Metabase 0.58 (Q-P14 & akun tidak aktif)
- Riwayat query = `query_execution`, dipangkas task OSS `TruncateAuditTables` menurut `audit-max-retention-days`: setting **internal tanpa setter** (API: "Pengaturan :audit-max-retention-days internal") -> **tidak bisa lewat `provision.py`**, hanya env `MB_AUDIT_MAX_RETENTION_DAYS`. OSS hanya memangkas `query_execution`; `audit_log`/`view_log` (Pro/EE) kosong di dev.
- Akun tidak aktif 90 h: **tidak ada fitur bawaan OSS** -> diemulasikan di `provision.py` (deactivate, bukan hapus). Butuh jadwal harian agar efektif (provision hanya saat deploy).

### Env baru / berubah untuk devsecops
| Env | Service | Nilai |
|---|---|---|
| `CH_MART_RETENTION_YEARS` | `clickhouse-init` | **10** — compose (`:-5`), `.env`, `.env.example`, `deploy.yml` (default '5'), `scripts/deploy.sh:280` masih 5 -> ubah ke 10 (default skrip kini 10) |
| `CH_PSEUDONYM_RETENTION_YEARS` | `clickhouse-init` **dan** `etl` | 5 (baru) |
| `CH_GEO_RETENTION_DAYS` | `clickhouse-init` **dan** `etl` | 90 (baru) |
| `MB_AUDIT_MAX_RETENTION_DAYS` | `metabase` | **3650** (baru) |
| timer harian | host | `metabase-provision <argumen sama> --housekeeping-only` (akun tidak aktif 90 h) |

Rekomendasi deploy pertama: `stop etl` sebelum `clickhouse-init` (migrasi `loyalty_events`), lalu `up etl`.

### Untuk lead / BFF
- Pemilik data lokasi di Postgres = BFF (`platform.price_hunter_report`; ETL/OCR hanya baca, tanpa UPDATE — least privilege). Perubahan BFF yang sedang berjalan di branch ini (`retention.service.ts`: `geo_lat/geo_lng = NULL, updated_at = now()` setelah `PRICE_HUNTER_GEO_RETENTION_DAYS`) **cocok** dengan sisi ClickHouse: `updated_at` dinaikkan -> ETL memuat versi tanpa lokasi; TTL kolom + `AgeOut` menutup sisanya.
- PDP inventory §2/§3 perlu revisi (architect): `competitor_prices`/`loyalty_events`/`fact_sales_line` baris 10 th, kolom pseudonim 5 th, geo 90 h; status Q-P8 sisi ClickHouse = jalan.

### Belum / risiko sisa
- Env compose/CI/deploy belum diubah (milik devsecops) -> sampai itu, `.env` dev/prod tetap memberi TTL baris **5 th** dan ETL memakai default 5 th/90 h.
- `MB_AUDIT_MAX_RETENTION_DAYS` belum diset dan efeknya belum diuji (task truncation terjadwal, tidak dipicu manual); default 720 h bersumber dokumentasi upstream, tidak terverifikasi dari jar.
- Jendela race migrasi: erasure DSAR yang jalan tepat di antara salin dan `EXCHANGE` diulang otomatis (verifikasi `count()=0`), tetapi penyalinan "jendela migrasi" (1 jam, `_loaded_at`) secara teori bisa mengembalikan baris subjek yang dihapus pada menit yang sama -> mitigasi: stop ETL saat deploy itu.
- Versi sama (`created_at`) di `loyalty_events`: bila hash sumber berubah tanpa mengubah `created_at`, pemenang merge = insert terakhir (reload ETL memperbaikinya).
- Tidak ada view/semantic layer yang mengekspos `member_hash`/`user_hash`; tidak diubah.

## Gelombang 18b — Rekonsiliasi pembayaran Poin EFN K-18-6 (E-30) (12 Okt 2026)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §9.9 K-18-1, K-18-5, K-18-6; desain Odoo `docs/08_ODOO_FIT_GAP.md` §9.2 (E-30).
E-30 **belum** ada di DB dev -> logika diuji dengan fixture; uji nyata mode `payment` menunggu lead.

### File
| File | Perubahan |
|---|---|
| `services/etl/efn_etl/loyalty_recon.py` | `LoyaltyReconSettings.points_mode` (`product`/`payment`) + `points_tolerance_idr`. Mode `payment`: kind 3-5 memakai `SQL_ODOO_POINTS_PAYMENTS` (metode `efn_is_loyalty_points`) dengan toleransi tetap (default 0). Baru: `settled_days`, `daily_points_recon`, `add_daily_findings`, `run_daily_points`, kind critical `points_payment_daily_mismatch`, kunci log `points_daily`, tabel `loyalty_points_daily`. |
| `services/etl/efn_etl/pos.py`, `jobs.py` | `fact_pos_payment.is_loyalty_points` (SQL membaca penanda hanya di mode `payment`; mode `product` = konstanta FALSE). |
| `services/etl/efn_etl/config.py`, `__main__.py` | Env `ETL_LOYALTY_POINTS_MODE` (default `product`), `ETL_LOYALTY_POINTS_TOLERANCE_IDR` (default 0, 0..1000); log start memuat `points_mode`. |
| `services/etl/sql/odoo-readonly-grants.sql` | Grant bersyarat (DO block) `pos_payment_method.efn_is_loyalty_points` — lolos sebelum E-30 (NOTICE), aktif saat dijalankan ulang setelah E-30. Diuji di dev: exit 0. |
| `infra/clickhouse/init/06-loyalty-points-payment.sql` | Kolom `fact_pos_payment.is_loyalty_points`, tabel `loyalty_points_daily` (TTL 400 h, tanpa PII), view `v_loyalty_points_daily`. |
| `infra/clickhouse/access/{etl,metabase}-access.sql` | `etl_writer` SELECT/INSERT tabel harian; `metabase_reader` view + tabel. |
| Test | `test_loyalty_recon.py` +11 (hari settle, atribusi lewat tengah malam, selisih per order/BFF-only/Odoo-only, toleransi, order di luar jendela, end-to-end mode payment, mode product tanpa harian), `test_pos.py` +1, `test_config.py` +1. |
| `services/etl/README.md` | Bagian "Redeem poin = pembayaran Poin EFN". |

### Desain
- Rumus K-18-6 per PT x hari WIB: Σ `pos.payment` Poin EFN (order `paid/done`, refund negatif) − (Σ `value_idr` reservasi `committed` − Σ `points_restored_value`) = 0 (toleransi 0).
- Hari = tanggal WIB pembayaran Poin EFN di Odoo; nilai BFF **diatribusikan ke order Odoo-nya** (`order_ref` = `pos.order.uuid`; restore memakai uuid order refund). Order offline yang di-commit BFF lewat tengah malam tidak memicu alarm palsu. Nilai BFF tanpa order Odoo memakai tanggal/PT BFF (= anomali). Hari yang dicek = hari utuh dalam lookback (72 j) yang sudah lewat settle (6 j).
- PDP: tidak membaca member/partner/kartu; sampel = id order Odoo.

### Butuh keputusan / pekerjaan pihak lain
1. **BFF (usulan kontrak, belum ada):** `points_restored_value` (K-18-5) belum disimpan BFF; ETL mengasumsikan tabel `platform.loyalty_pos_reverse` (`refund_order_ref` PK/unik, `original_order_ref`, `company_code`, `points_restored_value` bigint, `created_at`) + `GRANT SELECT` kolom tersebut ke `efn_readonly`. Bila lead memilih bentuk lain, cukup ubah `_PLATFORM_RESTORE_ROWS`.
2. **Odoo E-30:** `pos_payment_method.efn_is_loyalty_points` (boolean). Setelah terpasang: jalankan ulang grant Odoo, set `ETL_LOYALTY_POINTS_MODE=payment` di service `etl` (devsecops), lalu uji nyata.
3. Redeem BFF diambil dari `loyalty_reservation` (retensi 30 h) — cukup untuk jendela 72 j.

### Verifikasi
- pytest ETL 216 passed / 1 skipped; ruff check + format bersih.
- `clickhouse-init` dev exit 0 (06 diterapkan, idempoten pada run ke-2); ETL di-rebuild (mode `product`): `pos_payments` ok, `is_loyalty_points` = 0.
- Query BFF redeem & join Odoo pembayaran diuji sebagai role `efn_etl` di dev (grant cukup, tanpa kolom penanda).
- Catatan dev: `infra/compose/.env` lokal (gitignored) masih `CH_MART_RETENTION_YEARS=5` sehingga run init tanpa override sempat mengembalikan TTL baris ke 5 th; diubah ke 10 dan init dijalankan ulang (tidak ada data > 5 th di dev).

### Belum terverifikasi
- Mode `payment` terhadap Odoo/BFF nyata (E-30 + tabel reverse BFF belum ada).
- Perbandingan `payment_date` (timestamp tanpa zona, UTC) dengan parameter timestamptz mengikuti pola query recon yang ada (session TZ UTC).
- Kartu Metabase untuk `v_loyalty_points_daily` belum dibuat (view + grant sudah siap).

## Gelombang 18c — Perbaikan review r11: SEC-114 (L-25b), koordinasi SEC-124 (14 Okt 2026)

### Perubahan
| File | Isi |
|---|---|
| `services/etl/efn_etl/config.py` | `ETL_LOYALTY_POINTS_MODE` default **`payment`**; `product` ditolak bila `is_production` (APP_ENV production/kosong) — lapisan kedua `deploy-validate`. |
| `services/etl/efn_etl/loyalty_recon.py` | `LoyaltyReconSettings.points_mode` default `payment`. Mode payment: order **tanpa** pembayaran Poin EFN jatuh ke baris produk lama (`PointsLines.legacy=True`, toleransi Rp1 x baris) -> order pra-E-30 di jendela lookback tidak memicu `redeem_without_points_line` palsu; order legacy (termasuk order refund-nya) dikeluarkan dari perbandingan harian K-18-6. Order tanpa pembayaran poin DAN tanpa baris produk tetap ditandai. |
| `services/etl/efn_etl/pos.py`, `jobs.py` | Default `points_payment=True` (`fact_pos_payment.is_loyalty_points` dari penanda E-30). |
| Test | `test_loyalty_recon.py` (+3: legacy tanpa alarm palsu, redeem tanpa pembayaran/baris tetap critical, legacy keluar dari harian; test mode produk lama memakai `PRODUCT` eksplisit), `test_config.py` (default payment, product ditolak di production/APP_ENV kosong, boleh di development), `test_pos.py`. |
| `services/etl/README.md` | Default & aturan production. |

### Verifikasi
- pytest ETL **219 passed / 1 skipped**; ruff check + format bersih.
- Dev: E-30 kini terpasang (`pos_payment_method.efn_is_loyalty_points` ada) dan `platform.loyalty_pos_reverse` (BFF, `08-platform-g18-privacy.sql`) cocok dengan kolom yang dibaca ETL. Grant Odoo dijalankan ulang (exit 0, tanpa NOTICE = grant penanda diterapkan). Image ETL di-rebuild + recreate: log start `points_mode=payment`, job `pos_payments` ok dengan SQL penanda.
- Uji kering (dry-run, tanpa menulis ClickHouse) rekonsiliasi mode payment terhadap DB dev: berjalan tanpa error, tetapi **DB Odoo dev kosong (0 `pos_order`)** sementara ledger BFF masih berisi data uji lama (termasuk ±30 rb event bench) -> semua temuan (`earn_without_paid_order`, 7 `redeem_without_points_line`, 3 hari selisih) adalah ketidakkonsistenan data dev (order Odoo sudah tidak ada), bukan positif palsu logika. Uji nyata bermakna butuh order POS E-30 baru di Odoo dev + `ETL_LOYALTY_RECON_NOT_BEFORE` setelah reset data.

### SEC-124 (BFF)
Rekonsiliasi harian membaca `value_idr` reservasi `committed` (sisi redeem). Saat ini `erasure.service.ts` masih `DELETE FROM platform.loyalty_reservation WHERE member_hash = $1` dan sweeper retensi menghapus reservasi terminal setelah 30 h. Setelah SEC-124 (lepas tautan member, nilai keuangan committed tetap), erasure tidak lagi menghasilkan `points_payment_daily_mismatch`/`redeem_points_mismatch` palsu. Retensi 30 h tetap aman untuk jendela recon 72 j. Tidak ada perubahan ETL yang dibutuhkan untuk SEC-124 (kolom yang dibaca: `order_ref, company_code, value_idr, committed_at, status, points`).

## Gelombang 18d — SEC-118 sisi ETL (re-review G18) (14 Okt 2026)

Rekonsiliasi harian kini juga membandingkan **poin yang benar-benar dikembalikan BFF** dengan **nilai Rupiah dari Odoo**
per order refund: `points_restored_value − points_restored × LOYALTY_POINT_VALUE_IDR` harus di `[0, nilai 1 poin)`
(sisa pembulatan floor BFF). Di luar itu → kind critical **`points_restore_value_mismatch`**, terpisah dari
`points_payment_daily_mismatch` (selisih pembayaran).

| File | Perubahan |
|---|---|
| `services/etl/efn_etl/loyalty_recon.py` | `SQL_PLATFORM_RESTORES_*` membaca `loyalty_pos_reverse.points_restored`; `BffPointsMove.points`; `daily_points_recon(..., point_value_idr)` mengisi `restored_points_idr`, `restore_mismatched`, sampel id order refund Odoo; `add_daily_findings` menambah kind baru (hitungan = order); payload `points_daily.restore_mismatched`; `LoyaltyReconSettings.point_value_idr` (default 100, ≥ 1). |
| `services/etl/efn_etl/config.py`, `__main__.py` | Env `LOYALTY_POINT_VALUE_IDR` (default 100, 1..10^9) — **nama & nilai sama dengan BFF**. |
| `infra/clickhouse/init/06-loyalty-points-payment.sql` | `loyalty_points_daily.bff_restored_points_idr`, `restore_value_mismatched` (ADD COLUMN IF NOT EXISTS) + view `v_loyalty_points_daily` diperbarui. |
| Test | `test_loyalty_recon.py` +5 (jendela pembulatan: sisa Rp50 cocok, poin berlebih/kurang ≥ 1 poin ditandai; temuan terpisah dari selisih pembayaran; dilewati bila poin/nilai tidak ada; nilai poin custom; end-to-end mode payment; SQL membaca `points_restored`), `test_config.py` +1. |

Verifikasi: pytest ETL **225 passed / 1 skipped**; ruff check + format bersih; `clickhouse-init` dev 2x exit 0 (kolom baru ada,
view terbaca); ETL di-rebuild + recreate (`cycle` ok); dry-run recon terhadap DB dev: query restore ke tabel nyata
`loyalty_pos_reverse` berjalan (dev belum punya baris refund berpoin; Odoo dev masih 0 order -> temuan lain = data dev lama).

Untuk devsecops: set `LOYALTY_POINT_VALUE_IDR` di service `etl` dari nilai yang sama dengan `api-gateway` (bila BFF
memakai nilai non-default, ETL dengan default 100 akan menghasilkan positif palsu `points_restore_value_mismatch`).

## Gelombang 18e — Chain epoch salinan audit (restore DB Odoo) (15 Okt 2026)

Celah (devsecops, runbook restore): setelah restore DB Odoo, id `efn.audit.log` mundur; `audit_copy` melanjutkan dari
`max(log_id)` tersalin sehingga log baru ber-id ≤ watermark tidak pernah tersalin, dan rekonsiliasi beranomali.

### Desain
- **Epoch** = garis keturunan sumber. Kolom `chain_epoch` (default 0 = salinan lama) di `audit_log_copy`,
  `audit_log_line_copy`, `audit_recon_run`; riwayat di tabel append-only `audit_chain_epoch`
  (`infra/clickhouse/init/07-audit-epoch.sql`). Rantai hash (GENESIS per company), watermark, late pass, rekonsiliasi
  isi = **per epoch**. Epoch baru menyalin ulang seluruh log sumber; salinan epoch lama **tidak disentuh** (bukti) dan
  rantainya tetap diverifikasi setiap rekonsiliasi (tamper epoch lama tetap `chain_problems`).
- **Pemicu**: penanda eksplisit `ir.config_parameter` `efn_audit.chain_epoch` (diubah runbook restore) berbeda dari
  penanda epoch berjalan -> epoch baru (WARNING `EFN_AUDIT_EPOCH` `new_epoch`). Dibaca lewat view
  `efn_etl_audit_epoch` (milik efn_odoo, `security_barrier`, satu kunci) — efn_etl tetap tanpa akses
  `ir_config_parameter` (memuat rahasia). **Tanpa perubahan kode/dependency Odoo.**
- **Deteksi otomatis + fail-closed**: `id_regressed` (`max(id)` sumber < watermark) atau `head_reused` (log tersalin
  terbaru non-orang ada di sumber dengan create_date/model/operasi lain = id dipakai ulang). Tanpa perubahan penanda
  -> **diblokir** (ERROR `EFN_AUDIT_EPOCH` `divergence_blocked`, `AuditEpochBlocked`, heartbeat `audit_copy` error,
  tidak ada penyalinan). Penghapusan log terbaru tanpa id mundur (tamper/erasure) TIDAK dianggap restore: salinan
  lanjut, rekonsiliasi menandai. Log model orang disaring dari anchor (erasure UU PDP sah). `ETL_AUDIT_EPOCH_AUTO=true`
  hanya non-production (config menolak di production).
- **Jangkar Loki**: epoch ≥ 1 menambah `"epoch":N` di `EFN_AUDIT_CHAIN_HEAD`; `sink.verify_anchors` mencocokkan per
  (epoch, company, seq). Epoch 0 = format lama.

### File
`services/etl/efn_etl/audit.py` (`chain_epoch` di baris salinan, `EpochRecord`, `DivergenceCheck`, `anchor_matches`,
`decide_epoch`, `validate_marker`), `audit_job.py` (`resolve_epoch`, `check_divergence`, `AuditEpochBlocked`, semua
langkah per epoch, `chain_epoch` di recon), `adapters.py` (query ClickHouse ber-`chain_epoch`, `epochs`,
`recent_copies`), `sink.py` (jangkar per epoch), `config.py` / `__main__.py` (`ETL_AUDIT_EPOCH_AUTO`,
`ETL_AUDIT_EPOCH_ANCHORS`), `sql/odoo-readonly-grants.sql` (view), `infra/clickhouse/init/07-audit-epoch.sql`,
`infra/clickhouse/access/etl-access.sql` (SELECT+INSERT `audit_chain_epoch`, REVOKE ubah/hapus, `audit_copy_reader`),
README ETL. Test: `test_audit.py` +7 (aturan keputusan, penanda, registrasi epoch 0, restore tanpa penanda diblokir
untuk id mundur & id dipakai ulang, restore dengan penanda = epoch baru + bukti lama utuh + rantai GENESIS + recon
bersih, tamper epoch lama terdeteksi, auto dev + erasure/penghapusan bukan divergensi), `test_sink.py` +1,
`test_config.py` +1.

### Verifikasi
- pytest ETL **234 passed / 1 skipped**; ruff check + format bersih.
- ClickHouse dev: `clickhouse-init` 2x exit 0 (kolom + tabel + grant; baris lama `chain_epoch=0`).
- Grant Odoo dijalankan ulang: `efn_etl` membaca view (0 baris), `ir_config_parameter` tetap `permission denied`.
- **Uji nyata kasus sebenarnya** (DB Odoo dev memang sudah di-reset: salinan max log_id 158, sumber max 87):
  1. ETL baru -> ERROR `divergence_blocked` (`id_regressed`, anchors 20/0 cocok), tidak ada penyalinan;
  2. runbook: `odoo shell` `set_param('efn_audit.chain_epoch','dev-reset-20261015')` -> siklus berikut WARNING
     `new_epoch` epoch 1, 78 log disalin ulang (rantai baru dari seq 1), salinan epoch 0 (119 log) utuh;
  3. siklus berikutnya `epoch=1 new=0` (stabil); rekonsiliasi (one-off) epoch 1: `checked=78`, 0 anomali,
     `chain_problems=0` (rantai epoch 0 + 1 terverifikasi).

### Butuh pihak lain
- **devsecops**: (1) regex promtail stream `efn_audit_anchor` (`infra/observability/promtail.yml:50`) harus menerima
  kunci opsional `,"epoch":\d+` sebelum `}` — tanpa ini jangkar epoch ≥ 1 tidak masuk Loki (penulisan ulang salinan
  epoch baru tidak terdeteksi lewat jangkar); (2) alert Loki `|= "EFN_AUDIT_EPOCH"` (WARNING new_epoch = info
  terjadwal restore, ERROR divergence_blocked = page); (3) runbook restore: setelah DB pulih jalankan ulang
  `odoo-readonly-grants.sql`, set penanda via `odoo shell` (ORM -> tercatat di log audit), lalu ETL; (4) dev reset
  boleh `ETL_AUDIT_EPOCH_AUTO=true` di `docker-compose.dev.yml`.
- **Odoo engineer**: tidak wajib. Opsional: tombol/wizard admin teraudit untuk mengubah penanda (saat ini `odoo shell`).

### Batas / risiko sisa
- Pemegang hak tulis DB Odoo bisa mengubah penanda untuk "memulai ulang" rantai; mitigasi: WARNING `new_epoch` selalu
  muncul, epoch lama + jangkar Loki lama tetap ada sebagai bukti, dan perubahan via ORM tercatat di log audit.
- Restore yang tidak menghilangkan log apa pun (tidak ada aktivitas sejak backup) tidak terdeteksi otomatis — tidak
  perlu (tidak ada id yang dipakai ulang).
- Epoch baru menyalin ulang seluruh sumber (volume log audit kecil; dibatasi `max_pages` per siklus).

## Gelombang 20 — W-32 enam entitas: `COMPANY_CODES`, `dim_company`, eliminasi antar-entitas (18 Okt 2026)

Kontrak: docs/04 §12.1 + §12.6 (L-48), ADR-001 revisi 18 Okt §2 dan §6. Kode PT `EFN, GL, PB, BCR, BCF, FARM`; `RTL`/`FNB`
dihapus; tidak ada migrasi data (DB dev dibuat ulang).

### Desain
- **`efn_etl/entities.py` (baru, logika murni)**: `COMPANY_CODES`, `BUSINESS_TYPES` (`EFN`→holding, `GL`/`BCR`→retail,
  `PB`/`BCF`→fnb, `FARM`→farm), `brand_group` (`BCR`+`BCF` = `BACHA`, laporan per brand Bacha), `company_code()`
  (fail closed), `ic_relation()`.
- **`loyalty_events.company_code`**: `jobs._company_code` memakai `entities.company_code` → `RTL`/`FNB`/`BACHA` ditolak
  (halaman tidak dimuat). Ledger BFF dev lama harus dibuang (§12.1).
- **`dim_company`**: kolom baru `business_type`, `brand_group` (diisi ETL; `ALTER ... ADD COLUMN IF NOT EXISTS` di
  `08-g20-entities.sql`). `brand` dinormalisasi huruf besar. Kode kosong → ''; kode tidak dikenal → `ValueError`
  (fail closed, agar konsolidasi tidak salah klasifikasi).
- **Eliminasi**: tetap untuk **semua** baris ber-`ic_company_id` (partner = company grup lain), jadi otomatis mencakup
  6×5 arah. View `v_gl_consolidated` (dipindah dari 03 ke 08 karena membaca kolom baru) bertambah `business_type`,
  `brand_group`, `counterparty_brand`, `ic_relation`: `tp_holding` (Holding↔PT), `tp_bacha` (BCR↔BCF), `non_tp`
  (pasangan lain, mis. FARM→PT, PB↔GL; Q-AD1-1), `unknown` (kode company tidak dikenal/ganda). Label hanya untuk
  rekonsiliasi dasar harga; tidak mengubah angka eliminasi.
- **`v_gl_ic_recon` (baru)**: per bulan × pasangan tak berurut (`company_a_id < company_b_id`): `balance_a`,
  `balance_b`, `net_balance` (≠ 0 = selisih eliminasi: kurs, timing, sisi belum diposting, partner salah), grant
  Metabase. Tidak masuk semantic layer Text-to-SQL (sama dengan `v_gl_consolidated`).

### File
- Baru: `services/etl/efn_etl/entities.py`, `services/etl/tests/test_entities.py`,
  `infra/clickhouse/init/08-g20-entities.sql`.
- Ubah: `services/etl/efn_etl/jobs.py`, `services/etl/efn_etl/gl.py`, `services/etl/README.md`,
  `infra/clickhouse/init/03-gl-audit.sql` (view dipindah), `infra/clickhouse/init/02-loyalty-etl.sql` (komentar),
  `infra/clickhouse/access/metabase-access.sql` (`v_gl_ic_recon`), `services/text2sql/semantic_layer.yaml`
  (deskripsi kode), `services/text2sql/tests/test_guard.py` (fixture kode).
- Test fixture kode lama diganti: `services/etl/tests/test_jobs.py`, `test_gl.py`, `test_pos.py`, `test_stock.py`,
  `test_loyalty_recon.py` (`FNB`→`PB`, `RTL`→`GL`).

### Test baru
- `test_entities.py`: 6 kode & kode lama hilang; business_type per kode (+huruf kecil); brand_group Bacha; `company_code`
  menolak `RTL`/`FNB`/`BACHA`/salah ketik/None/''; `ic_relation` 30 pasangan berarah (simetris, tepat 6 pasangan TP),
  kasus tepi (None, '', kode lama, kode ganda); keselarasan SQL view ↔ Python (daftar kode di `NOT IN`, label,
  pasangan TP, urutan cabang `multiIf`), eliminasi semua IC, tanpa `partner_id`, view tidak lagi di 03; grant Metabase.
- `test_gl.py`: `transform_company` 6 entitas (business_type/brand_group, normalisasi, kode kosong) + menolak
  `RTL`/`FNB`/`BACHA`/`XYZ`; cek kolom DDL menerima `ADD COLUMN` di 08.
- `test_jobs.py`: `loyalty_events` menerima 6 kode, menolak `RTL`/`FNB`/`BACHA`.

### Asumsi skema
- `res_company.efn_brand_code` berisi kode baru persis (`GL`, `PB`, `BCR`, `BCF`, …) setelah efn-odoo-engineer (A)
  mengubah Selection `efn_core`; xmlid lama tidak relevan bagi ETL.
- Keenam company adalah company root dengan `partner_id` masing-masing (dasar `ic_company_id`).

### Belum terverifikasi / butuh pihak lain
- **Belum dijalankan**: pytest + ruff (lead), `clickhouse-init` (DDL `ALTER` + dua view; terutama JOIN kunci Nullable
  `cp.company_id = l.ic_company_id` dan alias `GROUP BY` di `v_gl_ic_recon` — pola standar ClickHouse 24.8 tetapi belum
  diuji di container).
- **Postgres CHECK masih kode lama** (di luar cakupan saya, milik BFF/devsecops): `infra/postgres/init/02-platform-loyalty.sql:29`,
  `03-platform-v3.sql:104`, `05-platform-loyalty-pos.sql:41,87`, `07-platform-online.sql:89,122`,
  `08-platform-g18-privacy.sql:61` → `CHECK (company_code IN ('EFN','RTL','FNB','FARM'))`. Tanpa diganti, BFF tidak bisa
  menulis `GL/PB/BCR/BCF`; ETL sudah menolak kode lama.
- Dev ClickHouse yang tidak dibuat ulang: `dim_company` lama tidak punya nilai `business_type` sampai `res_company`
  berubah → `TRUNCATE efn_dwh.dim_company` + hapus watermark job `companies` (pola `docs/runbooks/dev-stack-boot.md`).
  `loyalty_events` ber-`RTL`/`FNB` di ClickHouse dev ikut dibuang.
- Penanda transaksi `efn_intercompany` (W-15) belum ada; eliminasi masih berbasis partner company lawan (kontak anak
  company tidak terdeteksi, sama seperti G12). `tax_regime` (ADR-001 §6) belum dibuat: FARM belum ditetapkan
  (Q-AD1-2), lebih tepat diturunkan dari fiscal position/pajak Odoo daripada di-hardcode.
- Dashboard Metabase belum memakai `brand_group`/`v_gl_ic_recon` (belum ada kartu baru).

## Gelombang 21 — Alert `EFN_LOYALTY_RECON` liabilitas poin per PT + wilayah pembeli `efn_etl.partner_region` (19 Okt 2026)

Kontrak: docs/04 §13.1 no. 7 (rekonsiliasi liabilitas poin setelah akrual), §13.2 (E-38b `efn_region` + view ETL),
§13.6 (batas file); ADR-036 §0.7 + K-36-5 (ralat), ADR-022 revisi 18 Okt. Branch `chore/wave-21`, tanpa commit.

> **Ringkasan manajemen.** Bila saldo liabilitas poin suatu PT di Odoo berbeda dari catatan Super-App setelah akrual
> harian, tim Finance kini menerima peringatan (selisih berapa pun) dan peringatan kritis (selisih besar; ambang
> sementara Rp 100.000 menunggu keputusan Finance), plus dasbor Grafana per PT. Laporan penjualan per kabupaten/kota
> domisili pembeli tersedia di Metabase tanpa membuka alamat pelanggan: yang diambil hanya kode wilayah.

### 1. `EFN_LOYALTY_RECON` (Odoo) → Loki → Alertmanager + Grafana
- **Promtail tahap 2c** (`infra/observability/promtail.yml`): hanya `service=odoo`, logger
  `odoo.addons.efn_loyalty_accounting[.<sub>]`, level `INFO|WARNING`, regex berjangkar penuh dengan 5 kunci kontrak
  (`bff_closing_liability_idr`, `company_code` `[A-Z][A-Z0-9]{1,7}`, `date` `YYYY-MM-DD`, `diff_idr`,
  `odoo_liability_idr` — bilangan bulat, boleh negatif). Diterima dua urutan kunci: `json.dumps(sort_keys=True)`
  dan urutan kontrak; separator `", "/": "` atau ringkas. → `{stream="efn_loyalty", loyalty_event="liability_recon",
  company_code}` (retensi 400 hari). Baris lain yang memuat teks `EFN_LOYALTY_RECON` (logger lain, kunci tambahan, teks
  ekor, newline) tidak masuk stream dan tetap diredaksi.
- **Pengecualian redaksi**: selector redaksi kini `loyalty_event!="liability_recon"`. Alasannya, nominal ≥ Rp6,2 M
  berawalan `628…` berpola nomor HP, sehingga redaksi akan merusak nilai dan alert jadi buta. Pengecualian ini aman
  karena skemanya hanya menerima angka, kode PT, dan tanggal. Fixture membuktikan nominal `62812345678` lolos utuh,
  sedangkan baris palsu dengan nomor HP tetap diredaksi.
- **Loki rules** (`loki-rules/efn-loyalty.yml`, grup `efn-loyalty-liability-recon`): nilai terakhir dalam 26 jam per
  `company_code`, `|diff|` = unwrap digit tanpa tanda.
  - `EfnLoyaltyLiabilityReconMismatch` (`> 0`, warning, team finance).
  - `EfnLoyaltyLiabilityReconCritical` (`> 100000`, critical).
  - `EfnLoyaltyLiabilityReconMissing` (dead-man 26 jam; aktif hanya bila ada baris dalam 7 hari).
  - Inhibit critical → warning per `company_code` (`alertmanager.yml`).
- **Ambang "configurable"**: Loki ruler tidak mengekspansi env di file aturan. Karena itu ambang critical berada di
  satu tempat (angka `> 100000` di rule). Ambang itu dijaga sama dengan threshold merah panel Grafana oleh pytest
  `test_threshold_consistent_with_grafana_and_inhibit`. Perubahan ambang = PR.
- **Grafana** `efn-loyalty-liability-recon.json` (folder EFN), berisi enam panel:
  - |selisih| terakhir per PT;
  - jumlah PT yang tercatat dalam 26 jam;
  - jumlah PT dengan selisih;
  - selisih bertanda per PT;
  - liabilitas Odoo vs BFF;
  - log mentah.

### 2. Wilayah pembeli tanpa PII (`efn_etl.partner_region`)
- **View Odoo** (`services/etl/sql/odoo-readonly-grants.sql` blok `DO $grant_g21$`): dibuat oleh pemilik `efn_odoo`,
  bersyarat (NOTICE + lewati sampai `res_partner.efn_region_id` dan `efn_region.code` ada). Rinciannya:
  - Skema `efn_etl` dibuat; `efn_etl` (role) hanya mendapat `USAGE`, `PUBLIC` dicabut.
  - `efn_etl.partner_region(partner_id, company_id, region_code)` = `res_partner p JOIN efn_region r` (INNER JOIN:
    partner tanpa wilayah atau sudah di-erasure tidak muncul).
  - `efn_etl.region(region_code, name, state_code, state_name, active, write_date)` = `efn_region LEFT JOIN
    res_country_state`. Ekspresi nama (jsonb `translate=True` atau varchar) dan keberadaan `active`/`state_id` dipilih
    dari `information_schema` saat deploy.
  - Tanpa `security_invoker` (view berjalan dengan hak pemilik). Tanpa `security_barrier`, karena view tidak menyaring
    baris; pembatasnya adalah daftar kolom, dan dengan begitu join ETL bisa memakai PK.
- **Kolom yang dibaca (lengkap)**: `res_partner.id, company_id, efn_region_id`; `efn_region.id, code, name, state_id,
  active` (bila ada), `write_date, create_date`; `res_country_state.id, code, name`. Kolom yang tidak dibaca: nama,
  alamat, kota, kode pos, email, HP, NPWP/NIK, blind index, `efn_name_tokens`. Tidak ada grant tabel ke `res_partner`
  maupun `efn_region` (diuji pytest).
- **ETL** (`efn_etl/region.py`, `ETL_PARTNER_REGION_ENABLED`, default `false`, butuh `ETL_ENABLE_ODOO`):
  - `sales_fact_pos` melakukan LEFT JOIN view → `fact_sales_line.customer_region_code`. Nilainya adalah wilayah saat
    baris dimuat. Kode tidak valid → `''` (lenient: baris tetap dimuat, teks bebas tidak pernah disalin). Granularitas
    kecamatan `PP.KK.CC` ditolak.
  - Job `regions` → `dim_region` (ReplacingMergeTree(write_date)). Kode master tidak valid → `ValueError`
    (heartbeat error).
  - **Tidak ada tabel partner → wilayah di ClickHouse** (minimisasi). Partner ID pseudonim tetap hanya
    `fact_sales_line.customer_id` lama (TTL 5 th, tidak di-grant ke Metabase). Wilayah tetap ada setelah
    `customer_id` dikosongkan; yang tersisa hanya agregat kab/kota.
- **ClickHouse** `infra/clickhouse/init/09-g21-region.sql`:
  - kolom `customer_region_code`;
  - tabel `dim_region`;
  - view `v_pos_sales_region_daily` (per hari × PT × outlet × wilayah; tanpa `customer_id` agar view INVOKER tidak butuh
    grant identitas).
  - Grant: `etl_writer` INSERT `dim_region`; `metabase_reader` view + `dim_region` + kolom `customer_region_code`.
  - Kartu Metabase "Penjualan per wilayah domisili pembeli (kab/kota)" di dashboard 01 (baris 41).
- **Hardening**: `PgSource` kini `-c search_path=public`. Nama skema `efn_etl` = nama role, sehingga `"$user"` di
  search_path bawaan akan me-resolve nama tak berkualifikasi ke skema view. Semua query ETL sudah berkualifikasi
  (`platform.` atau tabel Odoo di `public`).

### File
- Baru:
  - `services/etl/efn_etl/region.py`
  - `services/etl/tests/test_region.py`
  - `services/etl/tests/test_observability_liability_recon.py`
  - `infra/clickhouse/init/09-g21-region.sql`
  - `infra/observability/grafana/provisioning/dashboards/json/efn-loyalty-liability-recon.json`
- Ubah:
  - `services/etl/efn_etl/jobs.py` (SQL penjualan bertemplate, kolom `customer_region_code`, `enabled_jobs(partner_region)`)
  - `services/etl/efn_etl/config.py`
  - `services/etl/efn_etl/__main__.py`
  - `services/etl/efn_etl/adapters.py` (search_path)
  - `services/etl/sql/odoo-readonly-grants.sql` (blok G21)
  - `services/etl/README.md`
  - `infra/clickhouse/access/etl-access.sql`
  - `infra/clickhouse/access/metabase-access.sql`
  - `infra/metabase/dashboards/01-penjualan-harian-outlet.json`
  - `infra/observability/promtail.yml` (tahap 2c + selector redaksi)
  - `infra/observability/loki-rules/efn-loyalty.yml`
  - `infra/observability/alertmanager.yml`
  - `infra/observability/tests/promtail/fixtures/odoo.txt` (+4 baris: 2 valid, 2 palsu)
  - `infra/observability/tests/promtail/expected.txt` (+4 baris, posisi urutan `LC_ALL=C sort` disusun manual)

### Test
- `test_region.py`:
  - kode kab/kota valid/tidak valid (provinsi saja, kecamatan, alamat, nomor HP, newline);
  - `strict_region_code`, `transform_region` (+ negatif: kode rusak, `state_code` aneh, nama 500 karakter);
  - kontrak SQL `regions`;
  - SQL penjualan tanpa/dengan wilayah (tanpa `efn_etl.` saat mati; saat aktif hanya LEFT JOIN view, tanpa
    `res_partner`/kolom PII);
  - kolom fakta (kosong, valid, teks bebas → `''`); wilayah tetap ada saat `customer_id` di-age-out;
  - flag `enabled_jobs`; konfigurasi (default mati, butuh Odoo);
  - isi view `partner_region` persis 3 kolom kontrak, tanpa kolom PII/`security_invoker`, tanpa grant `res_partner`/
    `efn_region`, bersyarat;
  - DDL ClickHouse cocok dengan `REGION_COLUMNS`; grant ETL/Metabase tanpa `customer_id`.
- `test_observability_liability_recon.py`: regex selector promtail diekstrak dari `promtail.yml` lalu diuji dengan
  `re` Python (subset sintaks RE2) atas fixture yang sama.
  - Fixture: 2 cocok, 2 palsu ditolak.
  - 13 baris negatif: level ERROR/DEBUG, logger `efn_loyalty_accounting_evil`/modul lain, teks ekor berisi nomor HP,
    kode huruf kecil/nama orang, format tanggal, desimal, string, kunci tambahan, injeksi newline, prefiks.
  - 4 varian valid (dua urutan kunci, separator ringkas, negatif, 12 digit).
  - Ekstraksi `|diff|` rule; ambang = threshold Grafana + inhibit Alertmanager; urutan tahap 2c sebelum redaksi.
- Uji perilaku promtail sungguhan: `scripts/test-promtail-redaction.sh` (CI `promtail-redaction`) dengan `expected.txt`
  yang diperbarui.

### Asumsi skema / kontrak (perlu dikonfirmasi efn-odoo-engineer A dan B)
- **(A) `efn_loyalty_accounting`**:
  - logger di bawah `odoo.addons.efn_loyalty_accounting` dengan format log Odoo standar;
  - level INFO (selisih 0) / WARNING (≠ 0);
  - **satu baris per PT per tanggal setiap run, termasuk selisih 0** (dibutuhkan dead-man dan panel "PT tercatat");
  - JSON lewat `json.dumps(payload, sort_keys=True)` dengan nilai IDR `int`;
  - `diff_idr = odoo_liability_idr − bff_closing_liability_idr` (dashboard menafsirkan tanda begitu).
  - Bila hanya mencatat saat ada selisih, dead-man harus dihapus/diubah. Bila format berbeda, tahap 2c tidak cocok;
    dead-man menangkapnya.
- **(B) `efn_region`**:
  - tabel `efn_region` dengan `code` (`^\d{2}\.\d{2}$`), `name`, `state_id` → `res_country_state`, opsional `active`;
  - `res_partner.efn_region_id` Many2one plaintext;
  - erasure mengosongkan `efn_region_id` (K-36-4).
  - **View dibuat satu pemilik saja**: skrip grant ETL ini. Bila modul `efn_region` juga membuat
    `efn_etl.partner_region`, kolomnya wajib persis `(partner_id integer, company_id integer, region_code text)`. Kalau
    tidak, `CREATE OR REPLACE VIEW` gagal saat deploy. Lead perlu memutuskan satu pemilik.

### Belum terverifikasi / risiko sisa
- **Belum dijalankan**:
  - pytest + ruff (lead);
  - `scripts/test-promtail-redaction.sh` (urutan `expected.txt` disusun manual);
  - Loki ruler memuat grup baru: sintaks LogQL `absent_over_time(...) and sum by (...) (count_over_time(...[7d])) > 0`
    dan `| regexp ... | unwrap` belum dievaluasi di Loki 3.2.1;
  - `amtool check-config`;
  - render panel Grafana 11.3;
  - blok `DO $grant_g21$` di PostgreSQL (modul `efn_region` belum ada di repo saat ditulis);
  - DDL/view `09-g21-region.sql` di ClickHouse 24.8;
  - validasi kartu Metabase oleh `provision.py`.
- **Uninstall `efn_region` / perubahan tipe kolom**: view bergantung pada `res_partner.efn_region_id`.
  `DROP COLUMN` tanpa `CASCADE` atau `ALTER COLUMN TYPE` oleh Odoo akan gagal selama view ada. Hal yang sama berlaku
  untuk `efn_etl_audit_epoch` (G18). Belum diverifikasi apakah `odoo.tools.sql.drop_column` memakai CASCADE. Runbook
  uninstall: `DROP VIEW efn_etl.partner_region, efn_etl.region` dulu.
- **Wilayah = saat baris dimuat**: wilayah yang diisi/diubah setelah transaksi tidak memperbarui fakta lama, kecuali
  backfill (reset watermark `sales_fact_pos`, yang memakai wilayah saat ini). Ini perilaku yang dipilih untuk laporan
  domisili saat transaksi; perlu dikonfirmasi Finance/PO.
- **Dead-man**: padam setelah 7 hari tanpa baris (agar lingkungan tanpa modul tidak alarm terus). Berhenti lebih dari
  7 hari hanya terlihat di panel "PT tercatat". Belum ada dead-man per PT (PT baru tanpa akun dikonfigurasi tidak
  memicu alert selama PT lain tercatat).
- **Ambang critical Rp 100.000 = PLACEHOLDER** → usulan **Q-F21-1** (Finance): nilai ambang, apakah per PT, dan
  apakah selisih bertanda tertentu (mis. Odoo < BFF) lebih kritis.
- **Runbook belum ditulis** (di luar cakupan file saya): `docs/runbooks/loyalty-anomaly.md` anchor
  `#efnloyaltyliabilityreconmismatch`, `#…critical`, `#…missing` direferensikan rule. Usulan ke lead/devsecops.
- `v_pos_sales_region_daily` hanya POS (`source='pos'`); penjualan online (`sale_order_line`) belum ada di mart.
  Text-to-SQL semantic layer belum memuat view wilayah (perlu keputusan: kab/kota + PT + hari bisa kecil per sel).

## Gelombang 22 — Rekonsiliasi poin online, mart KPI dapur KDS, dashboard poin hangus (19 Okt 2026)

Kontrak: docs/04 §14.C.5 (rekonsiliasi poin online), §14.B B-6 (tiket KDS online), §14.D.9 (observability hangus),
§14.E.6 (batas file). ADR-020, ADR-032, ADR-036. Branch `chore/wave-22`, tanpa commit, tanpa shell (pytest/ruff/
clickhouse-local dijalankan lead).

> **Ringkasan manajemen.** (1) Pembayaran sebagian belanja online dengan poin kini dicocokkan setiap hari: nilai poin
> yang dicatat Odoo di faktur dan nota kredit dibandingkan dengan pemakaian/pengembalian poin di Super-App, per PT dan
> per pesanan. Selisih memicu alert yang sudah ada untuk tim Finance. (2) Waktu masak dan waktu siap per stasiun dapur
> (POS dan pesanan online) tersedia untuk Metabase tanpa nama meja, catatan, atau data pelanggan. (3) Dasbor Grafana
> poin hangus menampilkan poin dan member per hari, run yang gagal atau dibatalkan, dan notifikasi WA yang gagal.
> Semua fitur baru default mati sampai modul Odoo/BFF terkait terpasang.

### 1. RECON poin online (§14.C.5) — `efn_etl/loyalty_recon_online.py`
- **Dipanggil** di job `loyalty_recon` yang ada (mode `payment`) bila `ETL_LOYALTY_RECON_ONLINE_ENABLED=true` (default
  `false`; gagal start bila recon mati atau mode `product`). Hari WIB utuh yang sudah lewat `settle`, sama dengan K-18-6.
- **Sisi BFF**: `platform.loyalty_ledger` `source='online'`, `reason ∈ {online_redeem, online_restore, online_earn,
  online_reverse}`, `source_ref = part_id`. Kolom yang dibaca hanya kolom `efn_readonly` lama (id, company_code,
  delta_points, reason, source_ref, created_at), **tanpa** `member_ref_hash`. **Tidak perlu grant BFF baru** (sesuai
  §14.E.1 "efn_etl/efn_readonly tanpa akses ke tabel baru"). Nilai Rupiah = poin × `LOYALTY_POINT_VALUE_IDR` (C.7 API3).
- **Sisi Odoo**: dua view milik `efn_odoo` di skema `efn_etl` (pola G21, `sql/odoo-readonly-grants.sql` blok
  `DO $grant_g22_online$`, bersyarat):
  - `efn_etl.online_points_payment(payment_id, online_order_id, part_ref, company_id, paid_on, amount)`:
    `account_payment` di jurnal Poin EFN yang tertaut (M2M `account_move__account_payment`) ke faktur
    `efn_online_order.invoice_id` (inbound, +) atau nota kredit `efn_online_refund.credit_note_id` (outbound, −). Status
    `in_process|paid`. Jurnal Poin EFN dipilih dari `account_journal.efn_is_loyalty_points` bila E-28 menambahkannya,
    selain itu `pos_payment_method.efn_is_loyalty_points → journal_id` (jurnal PEFN per PT).
  - `efn_etl.online_order_ref(online_order_id, part_ref, company_id, state, refunds)`.
  - `efn_etl` **tidak** diberi akses ke `account_payment`, `efn_online_order` (hash kode ambil/bayar, payment_ref),
    `efn_online_refund` (provider_refund_ref).
- **Temuan critical** (masuk `anomalies` → alert `EfnLoyaltyEtlReconAnomaly` yang sudah ada; format log tidak berubah,
  kunci `points_daily_online` ditambahkan di akhir):
  - `online_points_payment_mismatch`: per bagian, Σ pembayaran Poin EFN faktur ≠ −Σ online_redeem × nilai poin
    (termasuk redeem tanpa pembayaran Odoo dan sebaliknya), toleransi `ETL_LOYALTY_POINTS_TOLERANCE_IDR`;
  - `online_points_restore_mismatch`: nilai restore Odoo − poin restore × nilai poin di luar [0, nilai poin × jumlah
    refund Odoo) (sisa pembulatan floor, pola SEC-118); restore BFF tanpa refund Odoo = temuan;
  - `online_earn_without_picked_up`: `online_earn` untuk bagian yang tidak ada / belum `picked_up`;
  - `online_reverse_without_refund`: `online_reverse` tanpa refund Odoo yang tidak `failed`.
  - Bagian yang punya aktivitas sesudah batas jendela (belum settle) ditunda ke run berikutnya (`parts_deferred`).
  - Sampel = id `efn.online.order` + id ledger (angka). `part_id` tidak ditulis ke log maupun ClickHouse.
- **Harian**: `loyalty_points_daily` kolom baru `channel` (`pos` | `online`). View `v_loyalty_points_daily` kini
  difilter `channel='pos'` (dashboard lama tetap benar), view baru `v_loyalty_points_daily_channel` dan
  `v_loyalty_points_daily_total` (= persamaan kontrak: Σ jurnal Poin EFN POS + faktur SO vs Σ redeem − Σ restore per PT ×
  hari). Untuk online `bff_restored_idr = bff_restored_points_idr` (BFF tidak membuka nilai Rupiah restore online),
  jadi selisih harian kecil (< nilai poin per refund) di hari refund itu wajar. Temuan hanya dari cek per bagian.

### 2. Mart KPI dapur (POS + online) — `efn_etl/kds.py`
- Catatan: **mart KDS belum ada sebelumnya** (laporan architect G15 #3, PDP inventory §2.2b, README `efn_pos_kds`), jadi
  dibuat sekarang untuk POS dan online sekaligus.
- Job `kds_tickets` (`efn_kds_ticket` → `fact_kds_ticket`, cursor `write_date`, ReplacingMergeTree(write_date)) dan
  `kds_stations` (`efn_kds_station` → `dim_kds_station`). Aktif dengan `ETL_ENABLE_KDS=true` (butuh `ETL_ENABLE_ODOO`).
  Kanal online (`efn_online_order_id`, E-29a) hanya dibaca bila `ETL_KDS_ONLINE_ENABLED=true`; tanpa flag itu, semua
  tiket berkanal `pos` dan kolom E-29a tidak disentuh.
- Kolom: id tiket/stasiun/config/order POS/order online, preset, course, sequence, state, rush, cetak offline, cap waktu,
  durasi `queue/cook/wait/serve_seconds` (NULL bila belum terjadi atau negatif karena recall/jam), jumlah baris, item
  tambah/batal. **Tidak dibaca**: `table_label`, `tracking_number`, `internal_note`, `session_id`, isi baris (nama
  produk, atribut, catatan, combo), token/perangkat stasiun. Grant kolom G22 diuji agar tidak memuat kolom itu.
- View Metabase `v_kds_ticket_daily` (tiket, batal, siap, rush, offline, item, rata-rata antre/masak/tunggu/saji, p50/p90
  tunggu, tiket terlambat vs `late_minutes` stasiun saat ini) dan `v_kds_ticket_hourly` (beban per jam WIB).
- Index cursor `efn_etl_efn_kds_ticket_write_date_id_idx` (CONCURRENTLY, di dalam `\if` psql agar bersyarat tetapi di
  luar transaksi).

### 3. Observability poin hangus (§14.D.9)
- **Koordinasi**: devsecops sudah menulis tahap promtail `EFN_LOYALTY_EXPIRY_RUN` dan alert run
  (`EfnLoyaltyExpiryAborted` critical, `EfnLoyaltyExpiryFailed`, `EfnLoyaltyExpiryHeartbeatMissing`) di
  `loki-rules/efn-loyalty.yml`. Saya **tidak** menduplikasi alert itu (diuji pytest) dan tidak menyentuh `promtail.yml`.
- **Grafana** `efn-loyalty-expiry.json`, delapan panel:
  - run dibatalkan (kandidat > batas);
  - run gagal;
  - kandidat run expire terakhir (threshold merah > 20 000 = default `LOYALTY_EXPIRY_MAX_PER_RUN`);
  - notifikasi WA gagal 24 jam;
  - poin hangus per hari per PT penerbit (`efn_loyalty_expired_points_total{issuer}`);
  - member per hari per fase (`efn_loyalty_expiry_total{phase,outcome="ok"}`);
  - semua outcome per fase;
  - log heartbeat.
- **Prometheus** `alert-rules.yml` grup `efn-loyalty-expiry`: `EfnLoyaltyExpiryNoticeFailed` (warning, team finance,
  `metric_status: proposed`). Alasannya: §14.D.4 menetapkan hangus tetap berjalan walau notifikasi WA gagal.
- `EfnLoyaltyEtlReconAnomaly` (efn-loyalty.yml): hanya teks deskripsi diperluas ke online; ekspresi tidak berubah.

### File
- Baru:
  - `services/etl/efn_etl/loyalty_recon_online.py`
  - `services/etl/efn_etl/kds.py`
  - `services/etl/tests/test_loyalty_recon_online.py`
  - `services/etl/tests/test_kds.py`
  - `services/etl/tests/test_observability_g22.py`
  - `infra/clickhouse/init/10-g22-kds-online-points.sql`
  - `infra/observability/grafana/provisioning/dashboards/json/efn-loyalty-expiry.json`
- Ubah:
  - `services/etl/efn_etl/loyalty_recon.py` (kind online, `channel` di `DAILY_COLUMNS`, `merge_findings`,
    `online_enabled`, pemanggilan online)
  - `services/etl/efn_etl/jobs.py` (`enabled_jobs(kds, kds_online)`)
  - `services/etl/efn_etl/config.py` (3 flag + validasi fail-closed)
  - `services/etl/efn_etl/__main__.py`
  - `services/etl/sql/odoo-readonly-grants.sql` (blok G22)
  - `services/etl/tests/test_config.py`
  - `services/etl/README.md`
  - `infra/clickhouse/access/etl-access.sql`
  - `infra/clickhouse/access/metabase-access.sql`
  - `infra/observability/alert-rules.yml` (grup `efn-loyalty-expiry`)
  - `infra/observability/loki-rules/efn-loyalty.yml` (komentar/deskripsi saja)

### Test (pytest, logika murni + fake I/O)
- `test_loyalty_recon_online.py`:
  - cocok bersih (FIFO dua lot, restore floor);
  - redeem tanpa pembayaran Odoo dan sebaliknya; toleransi;
  - 5 kasus jendela pembulatan restore (tepat, sisa 99, sisa = V, BFF lebih besar, restore tanpa refund);
  - bagian ditunda; riwayat sebelum jendela; batas hari WIB;
  - earn tanpa `picked_up`, clawback tanpa refund, entri di luar jendela;
  - PT dari Odoo menang atas ledger; dedup;
  - baris/payload tanpa `part_id`; kind terdaftar critical; settings fail-closed; SQL tanpa identitas/`*`;
  - end-to-end `run_loyalty_recon` (kunci awal payload tetap untuk regex Loki, `channel='online'`, sampel order);
  - online mati = view tidak di-query.
- `test_kds.py`:
  - durasi, NULL negatif/kosong, tiket online tanpa order POS;
  - state/kanal/mode tidak dikenal → `ValueError`;
  - SQL tanpa kolom PII + keyset; kolom E-29a hanya bila flag;
  - `enabled_jobs`; kolom yang dibaca ⊆ grant G22; DDL ClickHouse memuat semua kolom.
- `test_config.py`: default mati + 5 kombinasi fail-closed.
- `test_observability_g22.py`: metrik dashboard = kontrak; label Loki ⊆ nilai tahap promtail; alert run tidak
  diduplikasi di Prometheus.

### Asumsi skema / kontrak (perlu dikonfirmasi)
- **BFF (efn-bff-engineer)**:
  - entri ledger online memakai `source='online'` dan `source_ref = part_id` untuk **keempat** reason (kontrak
    menyebut `source_ref=part_id` eksplisit untuk redeem; restore/earn/reverse diasumsikan sama);
  - restore memakai **floor** (`points = floor(points_value / V)`); bila ceil/round, kind restore memberi positif palsu;
  - `company_code` redeem = PT bagian (restore lintas PT boleh berbeda; ETL memakai PT bagian Odoo);
  - metrik `efn_loyalty_expiry_total` diinkremen **per member** per fase dengan `outcome` minimal `ok` (panel member per
    hari), dan seri counter diinisialisasi 0 (agar `increase()` tidak melewatkan kejadian pertama).
- **Odoo E-28 / E-45 (efn-odoo-engineer B)**:
  - pembayaran Poin EFN faktur online dan nota kredit dicatat sebagai `account.payment` lewat
    `account.payment.register`, sehingga M2M `invoice_ids` (`account_move__account_payment`) terisi;
  - jurnal = jurnal Poin EFN PT (tanda `account_journal.efn_is_loyalty_points` atau jurnal metode POS PEFN);
  - `account_payment.date` = tanggal WIB (user teknis ber-tz Asia/Jakarta; bila UTC, atribusi harian bergeser
    00:00–07:00 WIB, temuan per bagian tidak terpengaruh);
  - `platform_order_ref` huruf kecil.
- **E-29a `efn_online_kds_bridge`**: `efn_kds_ticket.order_id` opsional + kolom `efn_online_order_id` (docs/08 §10.6);
  `state`/cap waktu tiket online sama dengan POS; `config_id` boleh kosong (mart → `store_id=0`, label "Online").

### Belum terverifikasi / risiko sisa
- **Belum dijalankan**:
  - pytest + `ruff check` + `ruff format --check` (lead). Beberapa assert panjang diformat manual; jalankan
    `ruff format` bila berbeda;
  - DDL/view `10-g22-kds-online-points.sql` di ClickHouse 24.8 (`quantileExactIf`, LEFT JOIN subquery FINAL, `ALTER ADD
    COLUMN channel` lalu `CREATE OR REPLACE VIEW` di file yang sama);
  - blok G22 `odoo-readonly-grants.sql` (`\gset`/`\if`, `DO`, `format()` dengan `%1$s`) di PostgreSQL. Modul
    `efn_online_kds_bridge` dan perubahan poin E-28 **belum ada di repo** saat ditulis;
  - nama tabel M2M `account_move__account_payment` dan kolom `account_payment.date/state/payment_type` Odoo 19
    (dicek dari pengetahuan 18.0, bukan sumber 19.0);
  - `promtool check rules` untuk grup baru; render dashboard Grafana 11.3; LogQL panel `| regexp | unwrap`.
- **Perbaikan setelah run lead (19 Okt)**: (1) `test_loyalty_recon.py::test_daily_points_clean_redeem_and_refund_with_cross_midnight_commit`
  kini mengharapkan kolom `channel='pos'` di akhir baris harian; test lain sudah memakai indeks/`zip(DAILY_COLUMNS)`.
  (2) `v_loyalty_points_daily_total` ditulis ulang dengan subquery berkolom prefiks `c_` (ClickHouse 24.8 Code 184
  ILLEGAL_AGGREGATION: alias = nama kolom sumber tersulih ke argumen `sum`). Belum dijalankan ulang oleh saya.
- **Runbook belum ada**: `docs/runbooks/loyalty-inactivity-expiry.md` (dirujuk alert devsecops dan alert saya,
  anchor `#efnloyaltyexpirynoticefailed`). Usulan ke lead/architect.
- **Compose**: env baru (`ETL_LOYALTY_RECON_ONLINE_ENABLED`, `ETL_ENABLE_KDS`, `ETL_KDS_ONLINE_ENABLED`) belum
  ditambahkan ke service `etl` (milik devsecops); default `false` aman.
- **Persamaan harian online tidak eksak** selama BFF tidak membuka nilai Rupiah restore online ke `efn_readonly`.
  Opsi: BFF menambah grant kolom `online_order_part.points_restored_value`, atau menulis nilai restore di tabel
  non-PII. Butuh keputusan lead.
- **SLA dapur** memakai `late_minutes` stasiun saat ini (target historis tidak disimpan; PLACEHOLDER Q-19).
- `fact_kds_ticket` belum masuk TTL mart `clickhouse-init.sh` (milik devsecops; tanpa PII, jadi tidak mendesak) dan
  belum ada kartu Metabase (view sudah siap).
- `earn` online yang **hilang** (picked_up + member tertaut tanpa `online_earn`) tidak bisa dideteksi ETL: Odoo tidak
  tahu member dan `online_order_part.earn_status` tidak dibuka ke `efn_readonly` (sengaja). Pengecekan itu tetap di
  recon BFF.

---

## Gelombang 41 — ESB Core → ClickHouse datamart, service `esb-extract` (17 Nov 2026)

Kontrak: `docs/04` §25 (ADR-040 Proposed, K25-02). Dibangun dari prototipe `extract.py`/`aggregate.py` (tidak membaca
data klien; semua fixture test sintetis).

### File
- `services/esb-extract/efn_esb/`: `config.py` (env/secret, allowlist host, override PT, CK), `tls.py` (salinan ETL),
  `http.py` (klien GET-only, login/401, retry+jitter, token bucket, budget, tanpa proxy/redirect), `paging.py`
  (`count` vs `dataCount`), `normalize.py` (alias, FIN, takeaway, angka, **redaksi L-110**), `planning.py` (slice,
  jendela WIB, H-1/H-7/gap fill, backfill resumable), `transform.py` (kolom + transform murni), `extract.py`
  (extractor per entitas), `records.py` (raw/checkpoint/run/bom_ratio), `store.py` (ClickHouse TLS), `runner.py`
  (orkestrasi), `heartbeat.py`, `__main__.py` (CLI `run|backfill|probe|health`).
- `services/esb-extract/tests/`: `test_http.py`, `test_paging.py`, `test_normalize.py`, `test_planning.py`,
  `test_config.py`, `test_transform.py`, `test_runner.py`, `test_ddl.py`, `test_heartbeat.py`.
- `services/esb-extract/{Dockerfile,requirements.txt,requirements-dev.txt,ruff.toml,pytest.ini,.dockerignore,README.md}`.
- `infra/clickhouse/init/12-g41-esb.sql`, `infra/clickhouse/access/esb-access.sql`, `infra/clickhouse/clickhouse-init.sh`
  (user `efn_esb` + ikat role `esb_writer`).

### Asumsi skema / API
- Bentuk respons dari spec publik (`openapi.json`): `/product` tanpa satuan, `/menu/browse` (`categoryDesc`,
  `categoryDetailDesc`), `/supplier` (memuat `address`, `cellPhone`, `contactPerson` → whitelist), `/inventory/stock-list`
  (`stockDate`, `branchID`, per produk × lokasi, 503 = filter terlalu luas), `/branch/list/user` (`page/pageSize`,
  `dataCount` = baris halaman ini, BUKAN total → paging berhenti saat halaman tidak penuh, limit 20 seperti prototipe).
- `production-result` qty × `convertionQty` = satuan dasar EA (prototipe); simple-transfer list memuat `statusID`
  (kalau tidak, detail diambil lalu disaring).
- Tanggal fakta = hari slice yang diminta, bukan tanggal yang diparse dari dokumen (agar cocok dengan versi slice).

### Keputusan desain
- **Versi slice**: semua baris fakta slice `_version` = `extract_checkpoint.finished_at`; checkpoint ditulis terakhir;
  view hanya membaca versi slice terakhir (`IN` ke checkpoint FINAL). Tarik ulang menghapus baris void/terhapus dari view.
- `line_no` L-113 sesuai §25.5 + pemecah seri (discount, cogs, menuName).
- `brand` ditambahkan ke ORDER BY fakta setelah `company_code` (branchID hanya unik per akun ESB).
- Kolom tambahan: `menu_name` (sales), `branch_id` (payment), `uom` + `is_fin` (production/transfer), `is_fin`
  (delivery), `active` (product). Tabel tambahan `efn_esb.bom_ratio`. `payment_count` (bukan `count`).
- `v_esb_flow_daily`: `simple_mfg` → `ck_prod`/`store_prod` menurut `dim_esb_branch.is_ck`; `production_result` selalu
  `ck_prod` (prototipe; asumsi hanya di CK). `NAME:` di-resolve ulang saat query.
- Row policy `efn_t2s` untuk `v_esb_*` ditunda ke G42 (perlu view `efn_t2s` + whitelist text2sql di luar ownership).

### Belum terverifikasi / risiko sisa
- **pytest dan ruff BELUM dijalankan** (sesi tanpa akses shell). Jalankan:
  `docker run --rm -v "$(pwd -W):/w" -w /w/services/esb-extract python:3.12-slim sh -c "pip install -q -r requirements-dev.txt ruff && ruff check . && ruff format --check . && pytest"`
  (dari root repo; `test_ddl.py` membaca `infra/` → mount root repo, bukan hanya folder service).
- DDL/view `12-g41-esb.sql` belum dijalankan di ClickHouse 24.8 (tuple `IN` dengan `DateTime64(3)`, window function di
  `v_esb_menu_mix`, `UNION ALL` LowCardinality vs String di `v_esb_data_quality`).
- Tidak ada GET nyata ke ESB (sengaja; efn-lead). Baris komponen paket `sales-menu-cogs` mungkin dobel di `gross/cogs`.
- `ruff format --check` mungkin berbeda pada blok `# fmt: skip` di test; jalankan `ruff format` bila perlu.

### Perbaikan review security G41: SEC-185/186/187 (18 Nov 2026)
Hanya `services/esb-extract` (+ komentar kolom status `extract_run` di `12-g41-esb.sql`). SEC-181 (`normalize.py`) tidak
diubah.
- **SEC-186**: `runner.AuthBreaker` (murni) + `guarded_cycle`. `auth_failed` 2 siklus berturut-turut → brand ditangguhkan
  di memori; siklus berikutnya tanpa login/GET, hanya baris `extract_run.status='auth_suspended'` + heartbeat INFO;
  WARNING sekali (kunci brand + nama secret, tanpa user/password/token). Reset hanya oleh `ok|partial|limit`; `error`
  netral. Kolom `status` = `LowCardinality(String)` → tidak perlu ubah DDL (hanya komentar); `RUN_STATUSES` ditambah.
- **SEC-187**: `http.read_limited` + `UrllibTransport(max_response_bytes)`. `Content-Length` > batas ditolak sebelum
  baca; baca maks batas+1 byte; `EsbResponseTooLarge` (subkelas `EsbProtocolError`) tanpa retry, pesan tanpa URL/query.
  Body respons error (non-2xx, tidak di-parse) dipotong 64 KiB tanpa exception (retry 429/5xx tetap jalan). Env
  `ESB_MAX_RESPONSE_BYTES` default 33554432 (32 MiB), rentang 1048576..134217728.
- **SEC-185**: `planning.validate_ignore_window` dipanggil `_cmd_backfill` sebelum I/O; production (fail closed via
  `tls.is_production`: `APP_ENV` kosong/tidak dikenal/`staging` = production) wajib `ESB_ALLOW_IGNORE_WINDOW=true`
  (default `false`) **dan** `--from` = `--to`; selain itu exit 2 + pesan di stderr. `validate_backfill_range` yang gagal
  kini juga exit 2 dengan pesan (sebelumnya traceback).
- Test baru: `test_http.py` (batas ukuran, Content-Length bohong/tidak valid, body error dipotong, tanpa retry),
  `test_config.py` (default/rentang, flag, production fail closed), `test_planning.py`, `test_runner.py` (breaker,
  WARNING sekali, tanpa panggilan setelah ditangguhkan, gerbang CLI sebelum I/O).
- `test_contract.py` akan merah sampai devsecops menambah `ESB_MAX_RESPONSE_BYTES` + `ESB_ALLOW_IGNORE_WINDOW` ke overlay
  `docker-compose.esb.yml` dan lead ke `docs/04` §25.2.
- Belum terverifikasi: pytest/ruff tidak dijalankan di sesi ini (tanpa shell); `Retry-After` dari body error tidak
  terpengaruh (header). Lockout nyata akun ESB tetap belum diketahui (N=2 = konservatif).

## Gelombang 42 — Akses ClickHouse `efn_insight` + row policy per PT + katalog query Insight (18 Nov 2026)

Kontrak: kontrak kerja G42 bagian B, docs/04 §26.7–§26.9, §26.13 (C4); ADR-041 (Proposed), ADR-040 (Proposed), K25-02.
Tidak ada commit (efn-lead).

### File
- **Baru** `infra/clickhouse/init/13-g42-insight.sql` — view `efn_dwh.v_esb_sales_branch_daily`, `v_esb_branch_list`,
  `v_esb_product_list`, `v_esb_data_quality_pt` (idempoten, `CREATE OR REPLACE VIEW` saja).
- **Baru** `infra/clickhouse/access/insight-access.sql` — profil `insight_readonly` (CREATE + ALTER), role
  `insight_reader` (REVOKE efn_dwh.* dan efn_esb.*), definer `efn_insight_definer NOT IDENTIFIED HOST NONE` dengan
  grant per objek, DB `efn_insight`, 8 view DEFINER berkolom eksplisit, `GRANT SELECT ON efn_insight.*`, kuota per jam.
- **Ubah** `infra/clickhouse/clickhouse-init.sh` — `create_user efn_insight` (secret `clickhouse_password_insight`),
  ikat role/profil, baca `/run/secrets/insight_scope_key` (hex `[0-9a-fA-F]` ≥ 64, tolak bila sama dengan
  `text2sql_scope_key`), row policy `efn_insight_scope` per view (`company_code != '' AND CODES AND SIG`), lalu
  verifikasi ke `system.row_policies`: ada view `efn_insight` tanpa policy → `REVOKE SELECT ON efn_insight.*` + exit 1.
- **Baru** `services/esb-extract/tests/test_insight_access.py` — test statis (lihat di bawah).
- **Baru** `docs/design/insight-web-queries.md` — daftar view + kolom, aturan parameter, contoh HTTP, katalog query
  bernama sesuai §26.8 (+ tambahan bertanda), keputusan scope, tabel perbedaan dengan §26.

### View `efn_insight` (kolom)
| View | Kolom |
|---|---|
| `v_esb_sales_daily` | sales_date, brand, company_code, branch_id, branch_name, menu_code, menu_name, menu_category, menu_category_detail, is_takeaway, qty, gross, discount, net, cogs, trx_count (per menu) |
| `v_esb_sales_branch_daily` | sales_date, brand, company_code, branch_id, branch_name, trx_count (struk unik cabang-hari), line_count, qty, gross, discount, net, cogs |
| `v_esb_payment_daily` | sales_date, brand, company_code, branch_id, branch_code, branch_name, payment_type, payment_method, payment_count, amount, mdr, net |
| `v_esb_flow_daily` | day, brand, company_code, branch_id, branch_name, product_code, product_name, ck_prod, ck_out, in_ck, in_other, store_prod, st_in, st_out, sold |
| `v_esb_menu_mix` | month, brand, company_code, branch_id, branch_name, menu_category, is_takeaway, qty, net, share_qty, share_net |
| `v_esb_data_quality` | check_name, brand, company_code (selalu terisi), item, value, last_day |
| `v_esb_branch_list` | brand, company_code, branch_id, branch_code, branch_name, is_ck, is_store, active |
| `v_esb_product_list` | brand, company_code, product_code, product_name, category, uom, is_fin, active |

### Keputusan
- **Scope data_quality** (permintaan lead #1, L-123): `efn_dwh.v_esb_data_quality_pt` meneruskan baris ber-PT dan
  memperluas baris per brand (`data_age_hours`, `last_run_not_ok`) ke setiap `company_code` DISTINCT di
  `dim_esb_branch` brand itu (Bacha → BCF dan BCR). Tidak ada baris `company_code=''`; selain itu policy menambah
  `company_code != ''` (C4). Brand tanpa cabang terdaftar → baris brand tidak muncul (fail closed). Metabase tetap
  memakai `v_esb_data_quality` asli. Beda dengan §26.7 (pemetaan statis) — lihat tabel §5 dokumen katalog.
- **Scope produk**: `dim_esb_product.company_code` = PT default brand (Bacha → BCF), jadi tidak dipakai. Produk
  di-JOIN ke DISTINCT (brand, company_code) dari `dim_esb_branch`: user PT melihat katalog brand yang punya cabang
  PT itu (BCR melihat katalog Bacha), tidak pernah katalog brand lain.
- **KPI transaksi** (permintaan lead #2, Q-INS2): `v_esb_sales_branch_daily` (`uniqExact(sales_num)` per cabang-hari,
  filter versi slice sama). Nama mengikuti usulan lead (bukan `v_esb_sales_trx_daily` di spesifikasi UI). Catatan
  fakta G41 (total ESB belum termasuk PB1/service, ≈ 89,6% pembayaran) dicantumkan di view dan katalog.
- **Secret wajib** (bukan dilewati): perilaku `text2sql_scope_key` dan `create_user` = `cat` gagal di bawah `set -eu`
  → init berhenti. Dipilih konsisten: secret insight hilang = clickhouse-init gagal (fail closed; tidak ada user tanpa
  password atau view tanpa policy). Konsekuensi: **devsecops wajib** menambah `clickhouse_password_insight` dan
  `insight_scope_key` ke `secrets:` service `clickhouse-init` (base compose) + `scripts/secrets.list` (`hex32` = 64 hex
  memenuhi ≥ 64) dalam PR yang sama, kalau tidak `clickhouse-init` gagal di semua environment.
- **Definer least privilege**: grant per objek (9 view + 10 tabel `efn_dwh.*esb*` + `efn_esb.extract_checkpoint`,
  `extract_run`), bukan `efn_dwh.*` seperti §26.7. `efn_esb.*` wajib karena view v_esb_* membaca checkpoint/run;
  tanpa `raw_doc`/`bom_ratio`. Test memastikan daftar grant = penutupan rekursif objek yang dirujuk view.
- Profil: `result_overflow_mode='throw'` (ekspor CSV melebihi 200 000 baris gagal jelas, bukan terpotong);
  `max_rows_to_read` 500 jt, memori 2 GB (MAX 4 GB), 8 query paralel. Kuota `KEYED BY user_name` = plafon service
  bersama (20 000 query/jam); per staf = insight-web (atau `client_key` gelombang berikut).
- Katalog query: nama kanonik §26.8, parameter = filter URL §26.9 (`from,to,pt,branch,cat,product`), cabang/produk
  dikirim `'<brand>:<id>'` (branch_id/product_code hanya unik per brand), alias keluaran sengaja berbeda dari nama kolom
  (`net_sales`, `sold_qty`, ...) karena alias = nama kolom yang dipakai ulang dalam agregat lain memicu error
  "aggregate inside aggregate" di analyzer lama ClickHouse. `LIMIT` literal (parameter di LIMIT belum diverifikasi).
- Temuan semantik untuk frontend: tabel "selisih kirim vs terima per cabang × produk" (spesifikasi UI §4.5 butir 5)
  tidak bermakna (`ck_out` tercatat di CK, `in_ck` di toko) → diganti `dist_ck_gap_by_product` (per produk × hari).

### Test (`services/esb-extract/tests/test_insight_access.py`)
Set view efn_insight = 8 yang diharapkan; kolom eksplisit (tanpa `*`) ⊆ kolom keluaran view sumber (di-parse dari
12/13) dan memuat `company_code`; data_quality memakai `_pt` dan pemetaan via `dim_esb_branch`; product_list tidak
memakai `p.company_code`; sales_branch_daily `uniqExact` + checkpoint; 13 hanya `CREATE OR REPLACE VIEW` dan setiap
kolom teratas ber-`AS`; `insight_reader` hanya `efn_insight.*` (+ REVOKE efn_dwh/efn_esb); grant definer = penutupan
rekursif objek yang dirujuk (tanpa raw_doc/bom_ratio, tanpa wildcard); profil + kuota; init: user, ikatan, validasi
kunci (hex, ≥ 64, ≠ text2sql); setiap view punya row policy dengan `company_code != ''`, format SIG identik text2sql
kecuali variabel kunci; verifikasi `system.row_policies` + REVOKE.

### Belum terverifikasi
- **Tidak ada yang dijalankan** (sesi tanpa shell): pytest, ruff, `sh -n`/shellcheck, DDL di ClickHouse 24.8, query
  katalog. Jalankan dari root repo:
  `docker run --rm -v "$(pwd -W):/w" -w /w/services/esb-extract python:3.12-slim sh -c "pip install -q -r requirements-dev.txt ruff && ruff check . && ruff format --check . && pytest"`.
- ClickHouse 24.8 (lead): row policy pada view DEFINER berlapis (efn_insight → efn_dwh view INVOKER → tabel) dengan
  definer ber-grant per objek (bukan wildcard) — pola text2sql diverifikasi dengan `efn_dwh.*`; `ALTER SETTINGS PROFILE`
  dengan `result_overflow_mode`; `system.row_policies.short_name`/`table`; window function atas agregat
  (`menu_mix_by_branch`); `UNION ALL` LowCardinality vs String di `v_esb_data_quality_pt`; `NULLS LAST`.
- Test negatif wajib (§26.7) untuk lead: scope {PB} tidak melihat WTZ; sig salah → 0 baris; tanpa setting → error;
  `efn_insight` `SELECT efn_dwh.v_esb_sales_daily` → ditolak; baris `company_code=''` tidak terlihat (mis. codes `''`
  dengan sig sah atas codes kosong → 0 baris); user `BCR` melihat baris `data_age_hours` brand `bacha`; admin `efn`
  membaca `efn_insight.*` → 0 baris (efek row policy, bukan bug).
- Katalog query ditulis tanpa eksekusi; type JSON (UInt64 dikutip) bergantung pada setting
  `output_format_json_quote_64bit_integers=0` yang dikirim insight-web.
- Belum dikerjakan (di luar ownership): compose/secrets.list/check-encryption (devsecops), docs/04 §26.7 perlu
  direvisi untuk 8 view, grant definer per objek, dan pemetaan data_quality dinamis (architect/lead).

---

## Gelombang 44 — forecast-worker statsforecast + kalender budaya, mart forecast, SMD di ETL (18 Nov 2026)

Kontrak: ADR-046 (Proposed), docs/04 §28.1/§28.2 (byte-exact)/§28.3/§28.10, §12.1 revisi 18 Nov (SMD), L-144..L-148,
K25-02, ADR-003. Worktree `efn-platform-g44`, branch `feat/wave-44-supermarket-forecast`. Tidak ada commit.

### File
- **Baru** `services/forecast-worker/` — `efn_forecast/{__init__,__main__,calendar,factors,models,pipeline,contract,
  odoo,source,runner,schedule,config,tls}.py`, `tests/{conftest,test_calendar,test_factors_models,test_contract,
  test_odoo,test_runner,test_pipeline_minmax,test_backtest,test_config_schedule,test_access_sql}.py`, `Dockerfile`
  (pola esb-extract: digest dipin, user 65534, `NUMBA_CACHE_DIR=/tmp/numba`), `requirements.txt`,
  `requirements-dev.txt`, `ruff.toml`, `pytest.ini` (marker `slow`), `.dockerignore`, `README.md`.
- **Baru** `infra/clickhouse/init/15-g44-forecast.sql` — DB `efn_forecast`; tabel `dim_fc_warehouse`, `dim_calendar`,
  `dim_forecast_run`, `fact_forecast_daily` (TTL 3 th), `fact_forecast_accuracy` (TTL 3 th) — semua
  ReplacingMergeTree; view `efn_dwh.v_fc_{sales_daily,stock_daily,scrap_daily,calendar,product,warehouse}` +
  `v_forecast_accuracy_weekly` (dashboard).
- **Baru** `infra/clickhouse/access/forecast-access.sql` — profil `forecast_readonly` (readonly=2, CREATE = ALTER), role
  `forecast_reader` (REVOKE efn_dwh/efn_esb), definer `efn_forecast_definer` (grant per objek = penutupan view),
  6 view DEFINER `efn_forecast.v_*` berkolom eksplisit, `GRANT SELECT ON efn_forecast.*`, kuota per jam.
- **Ubah** `infra/clickhouse/access/etl-access.sql` (grant SELECT/INSERT 5 tabel G44 + SELECT `v_fc_sales_daily`),
  `infra/clickhouse/init/08-g20-entities.sql` (daftar kode view konsolidasi + `SMD`), `clickhouse-init.sh` (awalnya
  saya tambahkan blok bersyarat; devsecops lalu menjadikannya wajib — saya hanya merapikan teks `echo` penutup).
- **Ubah** `services/etl`: `efn_etl/entities.py` (+`SMD` retail), `efn_etl/forecast.py` (baru), `config.py`
  (`ETL_ENABLE_FORECAST`, `ETL_FORECAST_ACCURACY_WEEKS`, `ETL_FORECAST_ACCURACY_INTERVAL_SECONDS`), `jobs.py`
  (`enabled_jobs(forecast=)`), `__main__.py` (siklus akurasi harian), `sql/odoo-readonly-grants.sql` (bagian G44
  bersyarat + `stock_warehouse.code`), `README.md`, `tests/test_entities.py` (8 entitas, 56 pasangan), `tests/test_forecast.py`.

### Env / secret baru (untuk devsecops — sudah selaras dengan `docker-compose.forecast.yml`)
- forecast-worker membaca persis env compose: `FORECAST_ENABLED`, `FORECAST_DAILY_CRON`, `FORECAST_TZ`,
  `FORECAST_HORIZON_DAYS`, `FORECAST_CHUNK_SIZE`, `ODOO_RPC_URL`, `ODOO_RPC_CA_FILE`, `ODOO_RPC_CLIENT_CERT_FILE`,
  `ODOO_RPC_CLIENT_KEY_FILE`, `ODOO_RPC_TIMEOUT_SECONDS`, `ODOO_API_KEY_FORECAST_SMD_FILE` (`disabled` ditolak),
  `CH_HOST/PORT/SECURE/CA_FILE/DATABASE/USER/PASSWORD_FILE`, `NUMBA_CACHE_DIR`. Opsional (default aman, tidak wajib di
  compose): `FORECAST_MINMAX_WEEKDAY`, `FORECAST_DC_CODES`, `FORECAST_WAREHOUSE_CODES`, `FORECAST_PAYDAY_*`,
  `FORECAST_WEEKEND_DAYS`, `FORECAST_LEBARAN_DATES`, `FORECAST_SERVICE_LEVEL*`, `FORECAST_WASTE_*`, `FORECAST_MINMAX_*`,
  `FORECAST_BACKTEST_*`, `FORECAST_MODELS`, `FORECAST_CV_WINDOWS`, `FORECAST_HISTORY_DAYS`, `FORECAST_STOCK_DAYS`,
  `FORECAST_FIT_DAYS`, `ODOO_RPC_MAX_RETRIES`, `ODOO_DB`, `CH_QUERY_TIMEOUT_SECONDS`, `LOG_LEVEL`.
- `FORECAST_ENABLED=false` → proses idle (tidak exit; sesuai komentar compose). Healthcheck compose `os.kill(1,0)` cukup.
- ETL: `ETL_ENABLE_FORECAST` (default `false`, butuh `ETL_ENABLE_ODOO`), `ETL_FORECAST_ACCURACY_WEEKS` (2),
  `ETL_FORECAST_ACCURACY_INTERVAL_SECONDS` (86400). Tidak ada secret baru untuk ETL.

### Skema Odoo & rekonsiliasi dengan efn-odoo-engineer (dicocokkan dari source `efn_demand_forecast/models/*`)
- `efn_forecast_run.run_id` = Char UUID worker (bukan `run_uuid`; mart `dim_forecast_run.run_uuid` diisi dari kolom
  ini), `state` open|ok|failed, `generated_at`; `efn_forecast_daily` (`run_id` many2one, `date`, `yhat/p10/p90/sigma`,
  `model`); `efn_forecast_calendar` (`name` Char ≤ 64). SQL ETL + grant G44 disesuaikan. Catatan: wizard v1 TransientModel
  `efn.forecast.run` di `wizard/` masih ada di tree — konflik nama dengan model v2 harus diselesaikan sisi Odoo.
- Body json2 = payload §28.2 sebagai argumen bernama (`def efn_forecast_ingest(self, **payload)`) ✓.
- Galat: 422 `efn_forecast:<kode>` (UserError) / 403 `efn_forecast:company_mismatch|access_denied` → worker tidak retry,
  run ditandai failed; 429/5xx/timeout → retry backoff. Parser kode error membaca `message` json2 Odoo 19.
- Odoo menolak `chunk_total < 1` → run gagal sebelum data siap dibuka dengan `chunk_total=1` lalu `run_close failed`
  (failed tidak menuntut chunk lengkap). `run_close ok` setelah SEMUA chunk terkirim (`incomplete_run` bila tidak).
- Odoo menolak `wape_backtest: null` (metrik boleh tidak lengkap) → kunci dihilangkan bila backtest tidak ada
  (Odoo menyimpan 0). Respons open/close/upsert divalidasi longgar (field tambahan diabaikan).
- text2sql: `COMPANY_CODES` + `SMD` (`efn_text2sql/scope.py`, test `test_scope.py`, deskripsi `semantic_layer.yaml`).
- Worker menghitung minggu gajian dari env (default 7+7), bukan dari parameter Odoo per company (belum ada jalur baca).

### Keputusan desain
- "Regressor kalender" = penyesuaian multiplikatif (backfitting X-11, diestimasi di agregat gudang×kategori → gudang →
  PT, disusutkan n/(n+5)), lalu model statsforecast pada seri yang sudah disesuaikan; berlaku seragam untuk semua model
  (AutoARIMA exog tidak dipakai — lebih lambat dan tidak membantu Croston/ADIDA).
- Klasifikasi: histori < 56 hari / SKU baru → `category_mean` (perintah lead), bukan Croston seperti ADR §3.3; hari nol
  > 60 % → Croston (CV² < 0,49) / ADIDA. DC = Σ outlet + permintaan langsung (hanya bila tepat satu DC).
- Akurasi (ETL): forecast run `ok` kind `daily` TERAKHIR yang dibuat sebelum tanggalnya (lead ≥ 1 hari); minggu Senin
  WIB; minggu selesai dihitung ulang setiap hari (versi `computed_at`); jumlah mentah disimpan agar WAPE/bias bisa
  diagregasi ulang.
- `dim_fc_warehouse` terpisah dari `dim_warehouse` G32 (yang membaca kolom `efn_ck_production`).
- ClickHouse tanpa row policy per PT untuk `efn_forecast` (satu worker, tanpa PII); pembatasan PT di sisi tulis Odoo.

### Belum terverifikasi
- **Tidak ada yang dijalankan** (sesi tanpa shell): pytest, ruff, build image, DDL di ClickHouse 24.8. Perintah:
  `docker run --rm -v "$(pwd -W):/w" -w /w/services/forecast-worker python:3.12-slim sh -c "pip install -q -r requirements-dev.txt ruff && ruff check . && pytest"`
  (dan sama untuk `services/etl`). Uji backtest memakai statsforecast nyata (JIT numba; marker `slow`).
- Versi pin `statsforecast==2.0.1`, `numba==0.61.2`/`llvmlite==0.44.0`, `numpy==2.1.3`, `pandas==2.2.3`,
  `scipy==1.15.2`, `statsmodels==0.14.4`, `holidays==0.86` belum diuji resolusi pip-nya bersama; lisensi dari metadata,
  belum dicek ke repo primer. API `model.forecast(y=, h=, level=)` statsforecast dan `holidays.country_holidays(...,
  categories=("public",)/("government",))` ditulis dari dokumentasi, belum dieksekusi.
- Ambang test backtest (WAPE < 0,8 × baseline 56 hari, < seasonal naive; lonjakan H−3..H−1 > 1,3 × H−10..H−8) dipilih
  dari hitungan kasar, belum dijalankan.
- Durasi batch/RAM untuk 2.400 seri (AutoETS+AutoARIMA, CV 4 jendela, + backtest ≈ 2× kerja) belum diukur terhadap
  batas compose 1536M/2 CPU; kemungkinan perlu `FORECAST_CV_WINDOWS` lebih kecil atau `FORECAST_MODELS=AutoETS`.
- View ClickHouse (`assumeNotNull`, GROUP BY alias pada join, predicate pushdown filter `company_code`/`day` ke
  subquery `FINAL`) belum dieksekusi; `fact_scrap` hanya terisi bila `ETL_ENABLE_WASTE` (butuh `efn_ck_production`).
- Penambahan `SMD` di sisi lain (BFF `COMPANY_CODES`, Selection `efn_core`, CHECK platform `loyalty_ledger.company_code`,
  `deploy-validate`) bukan bagian saya; merge dengan G43 (IMD/CUP) perlu rekonsiliasi `entities.py` + view 08.

### Perbaikan QA G44 (setelah pytest/ruff hijau di container)
- Service level min/max dalam [0,5; 1) seperti Odoo: `contract.SERVICE_LEVEL_MIN/MAX`, `validate_minmax`,
  `pipeline.service_level`; env `FORECAST_SERVICE_LEVEL(_MIN)` dibatasi 0.5..0.999.
- `warehouse_code` dikirim persis (tanpa upper/strip): worker `source.py`, `config.py` (DC/warehouse codes), ETL
  `transform_fc_warehouse`.
- Plafon nilai per baris `FORECAST_MAX_QTY_PER_LINE` (default 100000 = `efn_forecast.max_qty_per_line`): baris di
  atasnya dibuang sebelum chunking (`RunReport.rejected_local`, log `EFN_FORECAST_RUN` + `rejected_local`); validator
  menolak `value_too_large`.
- Test: `test_contract.py`, `test_runner.py`, `test_config_schedule.py`, `test_odoo_contract_parity.py` (service level;
  plafon di-skip sampai parameter ada di source Odoo), ETL `test_forecast.py`. Belum dijalankan ulang.
## Gelombang 43 fase 2 (W1) — entitas migrasi esb-extract, DDL/akses `efn_mig`, mesin migrasi + validator (18 Nov 2026)

Kontrak: kontrak kerja G43 fase 2 (paket W1), ADR-042 (Accepted 18 Nov 2026), docs/04 §27 **v19.1** (§27.3a segmentasi,
§27.8/§27.8a histori penjualan, §27.10, §27.11), fit-gap G43. Tidak ada commit (efn-lead). Tidak ada panggilan ESB nyata.

### File
- **esb-extract** — baru `efn_esb/transform_mig.py` (kolom + transform E1/E2/E4/E5/E6–E9), `efn_esb/extract_mig.py`
  (extractor, `P1_ENTITIES`/`P2_ENTITIES`); ubah `normalize.py` (whitelist `CUSTOMER_FIELDS`, `COA_FIELDS`, `AR_FIELDS`,
  `AP_FIELDS`, fungsi `whitelist`/`redact_customer`; `redact_supplier` kini juga membuang nilai dict/list), `extract.py`
  (`BrandRuntime.opname_window_days`, `extractor_for` dengan import tertunda), `runner.py` (`ALL_TABLES`, `run_snapshot`),
  `__main__.py` (subcommand `snapshot`, `validate_snapshot_args`), `config.py` (default `ESB_CK_BRANCHES` =
  `pb:4|38,wtz:5|6,imd:2,cup:4|6`), `README.md`. Test baru `test_transform_mig.py`, `test_extract_mig.py`,
  `test_ddl_g43.py`, `test_mig_access.py`; ubah `test_ddl.py`.
- **ClickHouse** — baru `infra/clickhouse/init/14-g43-migration.sql` (9 tabel + `map_esb_odoo` kunci baru),
  `infra/clickhouse/access/mig-access.sql` (profil `mig_profile`, role `mig_rw`); ubah `12-g41-esb.sql` (definisi
  `map_esb_odoo` dipindah ke 14), `esb-access.sql` (grant esb_writer tabel baru + `REVOKE ALL ON map_esb_odoo`),
  `clickhouse-init.sh` (migrasi kunci map sebelum skema, user `efn_mig` bersyarat, ikat role/profil).
- **scripts/migration** (baru) — `esb_to_odoo.py`, `validate_import.py`, `efn_mig/{bundle,xmlid,mapping,segments,export,
  source,settings,validate,reconcile,results}.py`, `README.md` (**kontrak format bundel bersama importer W2**),
  `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `ruff.toml`, `tests/{conftest,test_xmlid,test_bundle,
  test_segments,test_mapping,test_export_flow,test_validate}.py`.

### Entitas esb-extract (`python -m efn_esb snapshot --brand B --entity E ...`, bukan loop `run`)
| Entitas | Endpoint GET (spec publik) | Tabel | Redaksi |
|---|---|---|---|
| `bom_detail` E1 | `/product/bom` → `/product/bom/{bomID}` per BOM aktif | `dim_esb_bom`, `dim_esb_bom_line` | redact |
| `menu_price` E2 | `/menu/browse-for-product` | `dim_esb_menu_price` | redact |
| `customer` E4 | `/customer` | `dim_esb_customer` | whitelist §27.11 |
| `location` E5 | `/location?branchID=` per cabang | `dim_esb_location` | redact |
| `stock_opname` E6 | `/inventory/stock-opname` + `/{num}` | `fact_esb_stock_opname` | redact |
| `ap_open` E7 | `/supplier-payable/invoice-aging?payableDate=` per cabang | `fact_esb_ap_open` | whitelist (kode) |
| `ar_open` E8 | `/customer-receivable?type=2&receivableDate=` per cabang | `fact_esb_ar_open` | whitelist (kode) |
| `coa` E9 | `/accounting/coa` | `dim_esb_coa` | whitelist |
P2 (E6–E9) ditolak CLI tanpa `--allow-unverified` (default mati). E10 TB/GL tidak dibangun: di spec ESB
`/accounting/general-ledger` dan `/summary` = **POST** (melanggar GET-only) → ekspor Finance (Q-ESB21).

### Keputusan / penyimpangan dari usulan §27 (untuk architect/lead)
1. **E2 tabel terpisah `dim_esb_menu_price`**, bukan kolom di `dim_esb_menu`: dua penulis (master harian vs snapshot)
   pada satu ReplacingMergeTree saling menimpa kolom (versi terbaru tanpa harga).
2. **E7/E8 endpoint**: §27.11 menyebut `/purchase/purchase-invoices` + settlement; dipilih
   `/supplier-payable/invoice-aging` (per faktur, `payableDate`) dan `/customer-receivable?type=2` (per faktur,
   `receivableDate`) karena langsung memberi saldo per tanggal; respons tanpa cabang → satu panggilan per cabang
   (PT benar untuk Bacha BCF/BCR).
3. **NPWP pelanggan tidak ditarik**: tidak ada di daftar `/customer` dan §27.4 menyatakan NPWP diisi Finance (bukan milik
   migrasi) → minimisasi data. Whitelist = §27.11 persis.
4. `map_esb_odoo`: definisi dipindah dari 12 ke 14 (§27.10 masih menyebut nama lama di satu tempat — sudah 14 di v19.1);
   `clickhouse-init.sh` memeriksa `system.tables.sorting_key` SEBELUM `init/*.sql`: kunci lama + kosong → `DROP … SYNC`
   lalu 14 membuat ulang; berisi → exit 1 dengan pesan (manual). Role bernama `mig_rw` (sesuai §27.10).
5. User `efn_mig`: secret tidak di-mount/kosong/placeholder (`disabled` dsb.) → `DROP USER IF EXISTS efn_mig` (bukan
   gagal); secret ada tetapi tidak valid → `create_user` gagal (fail closed).
6. **Format bundel = §27.3/§27.3a v19.1** (satu AES-GCM per bundel; histori per company × bulan, dipecah per gudang;
   gzip dalam tar; batas 512 MB / 50×). Penegasan teknis yang belum tertulis di §27 (tercantum di README):
   kunci hex di-`bytes.fromhex` sebelum HKDF; rasio 50× baru diperiksa setelah > 1 MiB terdekompresi; header wajib
   kanonik; nama anggota tar di-allowlist; baris histori = objek datar (bukan amplop master) dengan kunci persis;
   `result` manifest `run_id` = run_id bundel sumber; bentuk `import_report.json`/`odoo_totals.json`/`map.jsonl`.
7. Master kembar per company brand (Bacha → BCF dan BCR) untuk produk/menu/partner/BoM/biaya; cabang ikut PT
   `dim_esb_branch`. Dimensi = snapshot terakhir per brand (`_version = max`), sehingga baris yang hilang di ESB tidak
   diekspor. Menu arsip = menu di `v_esb_sales_daily` rentang 24 bulan yang tidak ada di master → `active=false`.
8. BoM: `phantom` bila `bomName` dirujuk `/menu/browse-for-product`, selain itu `normal`; `product_qty` = rasio
   `bom_ratio` (BATCH→EA) bila ada. Satuan produk: `/product` tidak memberi satuan → fallback satuan dari BOM.
   `standard_price` = `lastHpp` komponen terbesar > 0. Semua asumsi [B].
9. `stock`/`opening` export → exit 2 "belum dibangun" (selaras default mati importer); `supplierinfo` tanpa sumber.
10. Validator `--source templates` (aturan barcode/HP docs/08 §6.4) **tidak** dibangun; hanya `esb-bundle`.

### Env / secret baru
- Secret: `clickhouse_password_mig` (mount ke `clickhouse-init` + `esb-migrate`), `migration_bundle_key` (hex ≥ 64;
  `esb-migrate` + `odoo` selama jendela migrasi).
- Env `esb-migrate`: `CH_HOST/CH_PORT/CH_USER=efn_mig/CH_PASSWORD_FILE/CH_SECURE/CH_CA_FILE`, `MIG_BUNDLE_KEY_FILE`,
  `MIG_WORKDIR`, `MIG_OUT_DIR`, `APP_ENV`, `EFN_MIG_SALDO_BLOCKED`, `EFN_MIG_LEGACY_DAYS`, `EFN_MIG_SALES_LINE_MONTHS`,
  `EFN_MIG_SEGMENT_MAX_ROWS`, `EFN_ALLOW_PLAINTEXT`, `EFN_GIT_SHA` (opsional). esb-extract: tidak ada env baru (P2 lewat
  flag CLI agar test kontrak overlay tetap hijau).

### Untuk W2/W3/W4 (diteruskan lead)
- **W2 (odoo-engineer)**: kontrak = `scripts/migration/README.md`; pakai `efn_mig.bundle.open_bundle/build_bundle` dan
  `efn_mig.xmlid.xmlid` (stdlib + cryptography) agar format & xmlid identik; takeaway = `alias_of`; `menu_key` sales.line
  boleh null; `odoo_totals.json` dihitung dari tabel Odoo.
- **W3 (devsecops)**: image `esb-migrate` butuh `cryptography==46.0.3` (tidak ada di image esb-extract); overlay
  `docker-compose.esb.yml` + `.env.example` masih default `ESB_CK_BRANCHES=pb:4|38`; mount 2 secret di atas; CI job
  `scripts/migration` (pytest + ruff + pip-audit).
- **W4 (architect)**: §27.11 diperbarui dengan butir 1–3 + E10 POST; §27.3 dengan penegasan butir 6.

### Belum terverifikasi
- **Tidak ada yang dijalankan** (sesi tanpa shell): pytest, `ruff check`, **`ruff format --check`** (gaya multi-baris
  ditulis tangan; kemungkinan perlu `ruff format .`), `sh -n`/shellcheck `clickhouse-init.sh`, DDL 14 + access di
  ClickHouse 24.8. Perintah:
  `docker run --rm -v "$(pwd -W):/w" -w /w/services/esb-extract python:3.12-slim sh -c "pip install -q -r requirements-dev.txt ruff && ruff check . && ruff format --check . && pytest"`
  dan sama untuk `-w /w/scripts/migration`.
- ClickHouse 24.8 (lead): `REVOKE ALL ON <tabel belum ada>` saat 14 belum jalan (urutan sudah init → access, jadi tabel
  ada); `REVOKE ALL ON efn_dwh.* FROM mig_rw` lalu grant per objek; `query_row_block_stream` + `has({ids:Array(UInt32)},
  branch_id)`; subquery `_version = (SELECT max(_version) …)` pada FINAL. Test negatif `efn_mig`: INSERT tabel lain,
  SELECT `raw_doc`, CREATE → ditolak.
- ESB: semua extractor G43 dari spec publik tanpa GET nyata; arti kolom P2 (qty/stockQty, total faktur vs sisa,
  invoice-aging hanya faktur terbuka?) [B]; paging `/location` (list tanpa count) diasumsikan satu halaman.
- `cryptography==46.0.3`: versi dipilih tanpa akses PyPI; pastikan tersedia & lolos pip-audit.
- PDP: `dim_esb_customer.customer_name` bisa nama orang (pelanggan perorangan) → perlu entri inventaris PDP; definer
  `efn_t2s_definer` punya `SELECT efn_dwh.*` (wildcard G38) sehingga secara teknis bisa membaca tabel G43 — tidak ada
  view `efn_t2s` yang mengeksposnya, tetapi lebih aman dipersempit (security review).

### G43 perbaikan security (SECURITY_REVIEW_2026-11-18_g43) — sisi data
- **SEC-197**: part histori per GUDANG TUJUAN (`export.plan_warehouse_parts`); manifest `target_warehouses` (gudang part
  ini, xmlid lengkap `efn_mig_esb.<brand>_<co>_branch_wh_<id>`) + `company_warehouses` (semua gudang PT; gudang tanpa
  baris di part 1; bulan kosong tetap satu bundel); baris flow/sales.daily dijumlahkan per kunci model
  (`mapping.aggregate_rows`, sumber diurut hari); lokasi dengan induk PT lain tidak dipetakan; validator
  `check_segment_set` (tumpang tindih = exit 3, part tidak lengkap = `PARTS_INCOMPLETE`), `HISTORY_KEY_DUP`,
  `branch_key` di luar target = exit 3; `reconcile --final --parent-run-id` (`reconcile.reconcile_final`) vs hasil
  importer `steps=["totals"]`.
- **SEC-201**: `Keys.val` (HKDF `efn:mig-esb:val:v1`), `bundle.sign_validation`/`verify_validation` (mengikat run_id +
  `bundle_sha256` + `manifest_sha256` + errors); `validate_import.py` menulis laporan bertanda tangan, menerima banyak
  bundel. Uji lintas-sisi `tests/test_cross_side_validation.py` menjalankan fungsi importer (dari source, tanpa Odoo).
- **SEC-200**: `EFN_MIG_MAX_BUNDLE_BYTES` (default 1 GiB) dicek via `fstat` sebelum baca (`read_limited`) + di
  `open_bundle`. **SEC-202**: `write_secure`/`open_secure_write` (O_CREAT|O_EXCL|O_NOFOLLOW 0600 + rename atomik).
- **SEC-206**: `text2sql-access.sql` grant per objek (8 view + 10 tabel dasar) + `REVOKE ALL ON efn_dwh.*`; test
  `scripts/migration/tests/test_t2s_access.py` (penutupan rekursif dari 7 view efn_t2s).
- QA residu: `esb_product_name` (flow), `menu_name`/`menu_category` (sales.daily) sebagai kunci OPSIONAL
  (`mapping.HISTORY_OPTIONAL_KEYS`, identik importer). README: lokasi dari `branch_map.csv`, kontrak 1/2/5.
- Salinan `efn_mig/bundle.py` disalin ulang ke `services/odoo/addons/efn_legacy_history/esb_import/efn_mig/`.
- Belum terverifikasi: tidak ada yang dijalankan (pytest/ruff/ClickHouse); grant per objek efn_t2s perlu uji text2sql
  nyata di ClickHouse 24.8 (view DEFINER berlapis tanpa wildcard).

## G46 — histori penjualan ESB sebagai input forecast (19 Nov 2026, dikerjakan efn-lead)
Kontrak: docs/04 §28.2a, L-165; ADR-046 §3.8.
- **Keputusan jalur:** ESB tidak dibaca worker langsung. Sumber = Odoo `efn_legacy_sales_daily` (G43: menu ESB sudah
  dipetakan ke `product.product`, cabang ke `stock.warehouse`, hari < cutover ditegakkan Odoo) → ETL `fc_legacy_sales`
  → `efn_dwh.fact_legacy_sales_daily` → `v_fc_sales_daily` (UNION dengan POS). Alasan: pemetaan & cutover satu
  sumber, ADR-003 tetap (PG → CH), `map_esb_odoo` menyimpan id `product.template` (bukan varian) dan tidak tahu cutover.
- Worker tidak berubah (kolom view sama). Hari × gudang yang sudah punya POS Odoo memakai POS saja.
- Flag `ETL_ENABLE_FORECAST_LEGACY=false` (butuh `ETL_ENABLE_FORECAST`); sekalian `ETL_ENABLE_FORECAST` (G44) kini
  diteruskan compose/deploy.sh/deploy-validate/check-encryption (sebelumnya tidak diteruskan sama sekali → job forecast
  G44 tidak bisa dinyalakan lewat compose).
- Grant: PG `odoo-readonly-grants.sql` blok G46 (kolom minimum, index cursor, bersyarat `efn_legacy_history`);
  CH `etl_writer` SELECT/INSERT, `efn_forecast_definer` SELECT.

### Terverifikasi
- pytest ETL (semua lolos, +5 test G46), ruff 0.16.10 bersih; test `forecast-worker/tests/test_access_sql.py` lolos.
- ClickHouse 24.8 sementara: init 01..15 + `forecast-access.sql` jalan; data sintetis → histori ESB dipakai di hari
  tanpa POS, hari ber-POS memakai POS, menu tak terpetakan & qty negatif dibuang, versi baru menimpa; user
  `forecast_reader` membaca lewat view DEFINER dan ditolak membaca tabel dasar.
- `test-efn-deploy.sh` 370/370 (kasus baru 274a–d); render compose: flag default `false`.

### Belum
- Belum diuji dengan data Odoo nyata (histori G43 belum diimpor ke DB dev) dan belum end-to-end worker → Odoo.
- Penghapusan baris di Odoo (impor ulang irisan) tidak terbawa cursor → langkah manual (README ETL).
- Censoring OOS tidak berlaku untuk hari ESB; forecast bahan/komponen CK tetap lewat BoM Odoo.

## G46 uji end-to-end dengan data nyata (19 Nov 2026, efn-lead) — lingkungan dry-run terisolasi
Rantai diuji: ESB (backfill G41, 7 hari PB) → bundel migrasi → Odoo `efn_dryrun` (`efn.legacy.sales.daily`) → ETL
(kode `efn_etl` asli, job sumber Odoo) → ClickHouse (`fact_legacy_sales_daily`, `v_fc_sales_daily`, view DEFINER
`efn_forecast`) → forecast-worker (`--dry-run`, lalu `--once` ke Odoo `/json/2` dengan user teknis `efn_forecast_pb` +
API key) → Odoo `efn_forecast_daily` → ETL → `fact_forecast_daily`. Total qty Odoo = total view ClickHouse; jumlah baris
proyeksi Odoo = ClickHouse; run `ok`, 0 baris ditolak. Angka bisnis klien tidak dicatat di repo.

### Temuan & perbaikan (PR fix/g46-e2e-findings)
1. **L-166 menu POS tidak lengkap di ESB Core:** sebagian besar kode menu yang terjual tidak ada di `/menu/browse`
   (termasuk `flagActive=0`; spec tidak punya endpoint menu lain) → G43 mengimpornya sebagai produk **arsip** → POS
   dan forecast tidak melihatnya. Perbaikan: export `--recent-menu-days` (default 30) → menu histori yang terjual
   dalam N hari sebelum cutover = aktif; importer mengaktifkan varian tunggal saat template diaktifkan kembali
   (CE tidak melakukannya).
2. **L-167 konflik palsu setelah upgrade modul:** importer menganggap `write_uid != efn_migration` = edit pengguna;
   upgrade modul/recompute menulis sebagai `__system__` → impor ulang menolak semua record. Perbaikan: `__system__`
   diperlakukan seperti user migrasi.
3. **Worker cold start:** `category_mean` tanpa seri pembanding di gudang menyusut ke level 0 → ramalan = m/56 dari
   level (7 hari histori → 1/8). Perbaikan: tanpa pembanding, level seri sendiri.

### Catatan (bukan bug)
- Hari antara data terakhir dan hari ini tanpa penjualan dihitung nol (dry-run tidak punya POS setelah cutover).
- Overlap watermark ETL (300 dtk) membaca ulang baris impor bulk yang `write_date`-nya berdekatan setiap siklus sampai
  ada tulisan baru — benar (ReplacingMergeTree), tetapi impor histori besar perlu dipantau durasi ETL-nya.
- Histori 7 hari → semua seri `category_mean`, WAPE backtest belum bermakna; butuh backfill ≥ 112 hari.
- Lingkungan dry-run memakai PostgreSQL tanpa TLS (`EFN_ALLOW_PLAINTEXT`, jaringan Docker lokal terisolasi) dan Odoo
  HTTP tanpa gateway mTLS; ClickHouse tetap HTTPS + CA internal.

## G46b — backtest 112 hari data nyata + perbaikan performa/akurasi worker (20 Nov 2026, efn-lead)
- Backfill penjualan PB 105 hari (jendela malam, GET saja, 0 error) → impor histori Odoo → ETL → worker. Run pertama
  (`AutoETS,AutoARIMA` + CV 4 + backtest) dihentikan setelah 3,5 jam tanpa progres terlihat (satu core, tanpa log).
- Perbaikan: `FORECAST_JOBS` (proses `spawn` per gudang, hasil identik dengan 1; test), log `forecast gudang i/N`.
  Run `FORECAST_MODELS=AutoETS`, 4 jobs: ±5 menit untuk ±5.100 seri.
- Diagnosa backtest (as-of = H-1 cutover; as-of hari ini memasukkan hari tanpa POS di dry-run sebagai nol):
  AutoETS (mayoritas volume) sudah mengalahkan baseline; error didominasi (a) Croston pada seri yang berhenti terjual,
  (b) `category_mean` yang ditarik menu terlaris. Faktor kalender tidak berpengaruh berarti pada 112 hari histori.
- Perbaikan akurasi (L-168): seri tidak aktif (`FORECAST_INACTIVE_DAYS`, default 28; hari OOS dihitung aktif) →
  ramalan 0, tidak dikirim ke Odoo (forecast & min/max), tetap dihitung di backtest; level `category_mean` = median.
  Hasil: WAPE total turun ke bawah baseline v1, setara seasonal naive (angka rinci di laporan ke user, bukan di repo).
- ClickHouse: `config.d/system-logs.xml` (OOM dry-run akibat merge `system.metric_log`): log yang redundan dengan
  Prometheus dimatikan, `query_log` 90 hari (audit), `part_log` 14 hari; diverifikasi di 24.8 (container uji sekali pakai).
- Belum: AutoARIMA vs AutoETS pada histori panjang (24 bulan) belum dibandingkan; `category_mean` masih di atas baseline
  untuk menu baru (sebagian artefak backtest: seri yang baru muncul di periode uji); penghapusan baris Odoo legacy tidak
  dipropagasi ETL (selisih 2 baris pada uji).

## Gelombang 50 — perilaku visitor ke ClickHouse + `segment-worker` (20 Nov 2026)

ADR-047 (Proposed), docs/04 §28.6/§28.7/§28.10, L-152..L-155, R-L13/R-B17. Semua default mati; **production OFF**
sampai DPO (Q-SM16/17/26). Ditulis tanpa shell: pytest/ruff belum dijalankan oleh agent ini.

### File
- ETL: `services/etl/efn_etl/behavior.py` (baru), `config.py`, `jobs.py` (`enabled_jobs(behavior=)`), `erasure.py`
  (hook `SubjectHook` per `request_id`, default kosong = perilaku lama), `adapters.py` (`ClickHouseBehaviorStore`),
  `__main__.py` (siklus erasure perilaku → job incremental → agregat), `sql/platform-behavior-grants.sql` (baru),
  `tests/test_behavior.py` (baru), `README.md` (bagian G50).
- ClickHouse: `infra/clickhouse/init/16-g50-behavior.sql` (baru), `infra/clickhouse/access/segment-access.sql` (baru),
  `infra/clickhouse/access/etl-access.sql` (grant G50).
- `services/segment-worker/` (baru): `efn_segment/{__init__,__main__,config,tls,schedule,source,segments,contract,http,
  bff,odoo,runner}.py`, `tests/{conftest,test_segments,test_contract,test_runner,test_clients,test_config,
  test_access_sql}.py`, `Dockerfile`, `.dockerignore`, `requirements.txt`, `requirements-dev.txt`, `ruff.toml`,
  `pytest.ini`, `README.md`. **`requirements.lock` belum ada** (lihat Dependency).

### Skema ClickHouse
- `efn_dwh.fact_visitor_event`: `event_id` UUID, `subject_key`, `subject_kind`, `company_code`, `channel`,
  `channel_code`, `outlet_code`, `event_type`, `product_id`, `duration_s`, `qty`, `query` (search saja, disanitasi ulang),
  `result_count`, `stock_status`, `price_idr`, `received_at`, `event_date` (WIB), `ingested_at` (versi).
  ReplacingMergeTree(ingested_at), PARTITION BY event_date, ORDER BY (company_code, event_date, subject_key, event_id),
  TTL 90 hari (`ttl_only_drop_parts`), bloom filter `subject_key`.
- `agg_visitor_product_daily` (funnel per produk×outlet×kanal×hari), `agg_visitor_funnel_daily` (subjek berbeda per tahap
  per outlet×kanal×hari), `agg_visitor_search_daily` (istilah dengan ≥ k subjek). Tanpa subject_key, TTL 2 tahun,
  ReplacingMergeTree(computed_at), dihitung ETL lewat INSERT ... SELECT server-side untuk 3 hari terakhir tiap jam.
- View `efn_dwh.v_seg_subject_product_daily`, `v_seg_subject_search_daily`, `v_seg_product`, `v_seg_market_price` →
  dibungkus DEFINER `efn_segment.v_subject_product_daily`, `v_subject_search_daily`, `v_product`, `v_market_price`.
- Retensi ditulis literal di DDL (90 h / 2 th) karena `clickhouse-init.sh` milik devsecops; ubah = `ALTER ... MODIFY TTL`.

### User/grant baru
- ClickHouse `efn_segment_reader` + role `segment_reader` + profil `segment_readonly` (readonly=2 CONST, allow_ddl=0,
  max_execution_time 300/600, konkurensi 2) + kuota `segment_quota`; definer `efn_segment_definer` (HOST NONE). Grant
  **per view** (SEC-215), REVOKE di `efn_dwh/efn_esb/efn_forecast`.
- `etl_writer`: SELECT, INSERT pada `fact_visitor_event` + 3 tabel agregat; `ALTER DELETE` hanya `fact_visitor_event`.
- PostgreSQL `efn_etl` (langsung, bukan `efn_readonly`): USAGE skema `behavior`; SELECT kolom `behavior.event_raw`
  dan `behavior.subject_erasure(id, subject_key, request_id, created_at)`; REVOKE `device_link`/`device_consent`.

### Untuk devsecops (compose/secret/flag) — usulan, tidak saya ubah
1. `clickhouse-init.sh`: `create_user efn_segment_reader /run/secrets/clickhouse_password_segment` + blok
   `GRANT segment_reader TO efn_segment_reader; SET DEFAULT ROLE segment_reader TO efn_segment_reader;
   ALTER USER efn_segment_reader SETTINGS PROFILE 'segment_readonly';` (pola forecast G44). Tanpa ini access SQL tetap
   jalan (role/view saja) tetapi worker tidak bisa login. Pertimbangkan `HOST IP` subnet efn-data (SEC-215).
2. Secret baru: `clickhouse_password_segment` (clickhouse-init + segment-worker), `tls_segment_worker_crt/key`
   (CN `segment-worker`, CA internal), `odoo_api_key_segment_<kode>` per PT (mis. `_smd`, user teknis
   `efn_segment_<kode>`, keputusan devsecops; placeholder `disabled` ditolak worker). Tidak ada secret baru untuk ETL (tidak memegang `behavior_subject_pepper`; §28.10 menyebut "job
   erasure ETL untuk subject" — desain ini membuatnya tidak perlu, lihat Keputusan).
3. Service `segment-worker` di compose `efn-data` (container & kredensial terpisah dari forecast-worker): read_only,
   tmpfs /tmp, user 65534, network ke ClickHouse + jalur WireGuard ke `api-gateway:3000` dan `odoo-rpc-gateway:8443`.
   `command: ["python","-m","efn_segment","run"]`.
4. `odoo-rpc-gateway`: tambah CN `segment-worker` ke allow-cn **hanya** untuk path
   `/json/2/efn.campaign/efn_campaign_proposal_upsert`. BFF: allowlist CN `segment-worker` hanya untuk
   `POST /internal/v1/segments/*/members`.
5. Env ETL baru: `ETL_ENABLE_BEHAVIOR` (false), `ETL_BEHAVIOR_AGG_DAYS` (3), `ETL_BEHAVIOR_AGG_INTERVAL_SECONDS` (3600),
   `ETL_BEHAVIOR_K_MIN` (10), `ETL_BEHAVIOR_ERASURE_RECHECK_HOURS` (24), `ETL_BEHAVIOR_ERASURE_BATCH_SIZE` (500).
   Env segment-worker: lihat `services/segment-worker/README.md` (flag `SEGMENT_ENABLED=false`).
6. `deploy-validate`: `SEGMENT_ENABLED`/`ETL_ENABLE_BEHAVIOR` boolean; usul tolak `true` di production selama
   `BEHAVIOR_TRACKING_ENABLED`/`PROMO_CAMPAIGN_ENABLED` OFF.
7. Jalankan `services/etl/sql/platform-behavior-grants.sql` sebagai pemilik tabel `behavior.*` setelah DDL BFF
   (atau salin ke init Postgres G50). CI: job pytest/ruff + pip-audit + Trivy untuk `services/segment-worker`.

### Dependency + lock
`segment-worker/requirements.txt`: `clickhouse-connect==0.8.9`, `requests==2.34.2`, `urllib3==2.8.0`,
`certifi==2026.10.19` (sama dengan forecast-worker; tanpa numpy/pandas). Dev: `pytest==9.0.3`. **Lock ber-hash belum
dibuat** (tanpa shell saya tidak bisa menghitung hash; `scripts/check-py-lock.sh` dan
`docs/runbooks/python-dependency-lock.md` belum ada di worktree saat saya bekerja). Lead: jalankan di container
`pip-compile --generate-hashes --allow-unsafe --output-file=requirements.lock requirements.txt` lalu sesuaikan dengan
script lock devsecops. Dockerfile sudah memakai `pip install --require-hashes -r requirements.lock` → build gagal sampai
lock ada (sengaja, fail closed).

### Keputusan desain
- **Cursor ETL = `ingested_at`** (waktu masuk staging), bukan `received_at`: antrean RabbitMQ yang tertunda tidak membuat
  event terlewat. `event_date` tetap dari `received_at` (waktu server).
- **Erasure tanpa pepper di ETL**: BFF menulis semua `subject_key` subjek (HMAC member + setiap `device_key` tertaut) ke
  `behavior.subject_erasure`; ETL hanya mencocokkan. Job `behavior_erasure` jalan tiap siklus sebelum job incremental,
  recheck 24 jam (menutup race staging), kunci di jendela recheck dikecualikan dari muat ulang; gagal → job
  `behavior_events` dilewati siklus itu. DSAR: `privacy.erasure.completed` (store clickhouse) ditahan sampai
  subject_key milik `request_id` hilang. Mutation `ALTER DELETE` (fisik), bukan lightweight DELETE (pola §8.4).
- **Agregat**: hitungan tanpa pengenal disimpan apa adanya; istilah pencarian hanya ≥ k (10) subjek. Agregat tidak dihapus
  saat erasure (anonim, ADR-047 §3.3); hitung ulang setelah erasure bisa menyisakan baris lama (diterima).
- **Segmen**: Python murni atas agregat per subjek×produk dari ClickHouse; hanya subjek `member` (device = opsi);
  segmen < 10 anggota tidak dikirim; anggota dikirim lengkap ke BFF sebelum ringkasan Odoo; segmen gagal di BFF tidak
  diringkas ke Odoo; run baru = `run_id` baru. Ringkasan divalidasi menolak string hex-64 di mana pun (L-155).
- Produk teratas di `summary` hanya produk dengan ≥ `SEGMENT_K_MIN` subjek.
- **Perbaikan QA G50 #1**: `metrics` diratakan menjadi angka saja (`as_of` → `as_of_yyyymmdd`, `reason_counts` →
  `reason_market`/`reason_habit`, `top_products` → teks `summary`), `generated_at` di top-level. Sebelumnya setiap
  `proposal_upsert` akan ditolak 422. User Odoo worker diganti ke `efn_segment_<kode>` / secret
  `odoo_api_key_segment_<kode>` (env `ODOO_API_KEY_SEGMENT_<KODE>_FILE`).

### Asumsi skema/kontrak yang belum ditetapkan (perlu rekonsiliasi lead dengan BFF/Odoo)
1. **PG `behavior.event_raw`** kolom: `event_id uuid, subject_key char(64), subject_kind ('device'|'member'),
   company_code (diturunkan BFF dari channel_code, §8), channel, channel_code, outlet_code, type, product_id, duration_s,
   qty, query, result_count, stock_status ('in_stock'|'low'|'out'), price_idr bigint, received_at, ingested_at
   timestamptz DEFAULT now()` + index `(ingested_at)`. Kontrak §28.7 tidak menyebut `company_code` maupun `ingested_at`.
2. **PG `behavior.subject_erasure(id bigserial, subject_key char(64), request_id uuid NULL, reason text, created_at)`**
   append-only, retensi ≥ 100 hari, diisi BFF dalam transaksi erasure **sebelum** `dsar_request` berstatus processing.
   Belum ada di docs/04 (usulan revisi §28.6/§8.4).
3. **Event server `purchase`** (per baris pesanan online `paid`, `price_idr` = harga satuan) — belum ada di §28.6. Tanpa
   itu "sudah beli" hanya dari `checkout_start` (product_id keranjang bisa kosong) → viewed-not-bought kelebihan anggota.
   Tipe di luar daftar membatalkan halaman ETL (fail closed).
4. **Kode segmen**: `viewed_not_bought|wishlist_idle|oos_viewed|price_sensitive|search_no_result`.
5. **Ringkasan Odoo** (diperbaiki QA G50 #1, sesuai validator `efn_campaign`): `{schema_version:1, company_code,
   run_id, segment_code, member_count, metrics:{lookback_days, product_count, as_of_yyyymmdd, k_min,
   top_product_count, top_product_subjects, reason_market?, reason_habit?} (angka saja), summary (<= 256, tanpa angka
   >= 6 digit), generated_at (RFC3339 UTC)}`; idempoten per `(company, run_id, segment_code)`; galat
   `efn_campaign:<kode>`; konstanta dicek terhadap source Odoo di `tests/test_odoo_contract_parity.py`.
6. **BFF members** (diperbaiki SEC-G50-16, sesuai `src/campaign/segment-ingest.service.ts`): body
   `{schema_version:1, company_code, run_id, segment_code, subject_keys[<= 5000], chunk_no (0..100000)}` dalam urutan
   itu; `company_code` = PT run (∈ COMPANY_CODES), ikut hash idempotensi BFF, satu run = satu PT. Respons
   `200 {ok, run_id, segment_code, chunk_no, accepted, duplicate_chunk}`; `400 invalid_request`,
   `409 chunk_conflict`, `409 run_company_mismatch` tidak diulang. Contoh bersama worker/BFF:
   `services/segment-worker/tests/fixtures/segment_members_v1.json` (valid 1 chunk, chunk terakhir, duplikat, invalid
   dengan kode galat, konflik), diuji `tests/test_members_fixture.py` (termasuk paritas KEYS & COMPANY_CODES dengan
   source BFF). Usul kontrak: field `chunk_total`; docs/04 §28.7 perlu menambah `company_code` (architect).
7. `outlet_code` di mart belum dipetakan ke `warehouse_id` (row policy Insight `v_sm_*` G47 butuh `warehouse_id`).

### Belum terverifikasi
- pytest + ruff kedua paket (belum dijalankan); DDL/akses ClickHouse 24.8 belum diterapkan ke server (sintaks view
  subquery + `FINAL`, `uniqExactIf`, `maxIf` DateTime64 default 1970).
- `clickhouse_connect` `command(sql, parameters=...)` dengan parameter server-side `{x:Type}` pada INSERT ... SELECT
  (dipakai agregat) belum diuji; bila tidak didukung, ganti ke `query()` atau literal tanggal tervalidasi.
- Volume: FINAL atas 90 hari event + agregasi per subjek×produk 60 hari belum diukur (asumsi ADR ±0,5 juta event/hari).
- Pembanding Price Hunter kosong sampai `competitor_prices.product_sku` dipetakan (`default_code` juga belum unik antar
  PT).
- File `services/segment-worker/efn_segment/http.py` bernama sama dengan modul stdlib `http`; aman karena hanya diimpor
  absolut sebagai `efn_segment.http`, tetapi lead boleh mengganti nama menjadi `transport.py` (agent tidak bisa
  menghapus file).

**Verifikasi lead (21 Nov 2026):** pytest esb-extract lulus, ruff 0.16.10 bersih (satu reformat `transform.py`). Backfill
`dim_esb_menu` dari `raw_doc` dijalankan di efn-prod dengan dua koreksi atas SQL draf: tanpa `FINAL` pada `raw_doc` (gagal dengan
`ARRAY JOIN`; duplikat diredam `argMax(..., doc_date)`), dan alias kolom dalam dibedakan dari alias agregat (galat
`ILLEGAL_AGGREGATION`). Hasil: +1.004 kode, kategori kosong di `v_esb_sales_daily` (22 Okt–20 Nov) 94% -> 0%.

## Gelombang 65 paket W-B — `scripts/migration`: supplierinfo, cost berurutan sumber, stock, histori pembelian (24 Nov 2026)

Branch `feat/esb-migration-purchase-stock` (worktree `efn-platform-esbmig`). Kontrak `docs/04` §27.13.2 (antarmuka
masukan), §27.13.4–27.13.5, §27.13.8–27.13.9; ADR-042 §0b; fit-gap `G65_MIGRASI_PEMBELIAN_STOK.md`. **Tidak ada yang
dijalankan** (pytest/ruff oleh lead). `odoo_import_esb.py` dan `services/odoo` tidak disentuh (W-C).

### File
- Diubah: `scripts/migration/efn_mig/mapping.py` (DATA_KEYS `supplierinfo`/`quant`, `cost` + `source, as_of`;
  HISTORY_KEYS/OPTIONAL `legacy_purchase_line`; ENTITY_FILES; `parse_location_map`; `latest_purchases`,
  `build_supplierinfos`, `supplier_discount`, `opening_costs`, `bom_last_hpp`, `build_costs` berurutan sumber,
  `build_quants` + `StockSkips`, `purchase_line_row`), `export.py` (`legacy_purchase_line` di HISTORY_STEPS, `stock` keluar
  NOT_BUILT, `SINGLE_STEPS`, `G65Inputs`/`G65Result`, gerbang `stock`, `StockGateError`, pembelian hanya part 1),
  `source.py` (`purchase_lines`, `purchase_history_rows`, `purchase_snapshot`, `stock_opening`, `stock_opening_meta`),
  `segments.py` (rentang pembelian = sales.daily), `validate.py` (§27.13.5 + `production`), `reconcile.py` (butir 1a, 2,
  5e), `esb_to_odoo.py` (`--purchase-statuses`, `--purchase-discount-mode`), `validate_import.py` (APP_ENV production),
  `README.md`, `tests/conftest.py`, `tests/test_export_flow.py` (expand_steps), `docs/runbooks/migrasi-esb-odoo.md` (§3,
  §4.6 urutan gladi PB 2026-12-01).
- Baru: `scripts/migration/tests/test_g65.py` (sintetis; kasus negatif validator supplierinfo/cost/quant/pembelian).

### Format bundel final (W-B ↔ W-C)
- Master: `supplierinfo.jsonl` (xmlid `supplierinfo` dari `<sup>_<prod>`, esb_key `<sup>|<prod>`, data = DATA_KEYS §27.13.4);
  `cost.jsonl` + `source`, `as_of`. Cutover: `quant.jsonl` (xmlid null, esb_key `<branch_id>|<prod>`, data = DATA_KEYS
  §27.13.4); `location_key` = xmlid `branch_wh` (store/ck/warehouse → `lot_stock_id`) **atau** `branch_loc` (cabang
  `location`) — W-C wajib menerima keduanya.
- Histori: `legacy_purchase_line.jsonl.gz` hanya di part 1 segmen; `files[].range` ada; kunci = §27.13.4.
- `odoo_totals.json` baru (didefinisikan W-B, belum ada di kontrak): `legacy_purchase_line: [{month, company_code,
  branch_name, rows, qty, total}]`, `quant: [{company_code, location_key, product_key, quantity (satuan uom_esb), value}]`.

### Asumsi / keputusan
- `currency` IDR = `""` atau `"IDR"` (W-A menyimpan `currencyID` apa adanya; bila numerik, semua baris PO dilewati →
  tambah nilainya ke `mapping.IDR_CODES`).
- Satuan campur dalam satu cabang × produk (stok) → tidak diekspor, error validator `STOCK_UOM_MIXED`; untuk HPP
  `stock_opening` → sumber berikutnya (`manifest.cost_mixed_uom`). Tidak menebak konversi.
- Gerbang checkpoint: bundel tetap ditulis (`stock_opening_missing_branches`) lalu exit 1; validator error.
- Supplierinfo dengan satuan beli tanpa `uom_map` → dilewati (konversi `ea_factor` tidak mungkin tanpa entri peta).
- Neraca qty export per cabang (`stock_by_branch`) = butir `2_stok_sumber`; perbandingan ulang ke ClickHouse
  (`--with-source`) dan butir 2b (opname) belum dibangun.

### Belum terverifikasi
pytest/ruff; SQL G65 di ClickHouse 24.8 (IN tuple `(brand, toString(branch_id), opening_date, _version)` vs
`extract_checkpoint`); importer W-C menerima `supplierinfo.jsonl` di bundel master lama-baru, `data.source/as_of`, dan
`odoo_totals` baru; arti `discount`/PPN (Q-G65-2, Q-ESB26); status PO PB lain (Q-ESB28); `purchase_ok` produk belum
diturunkan dari supplierinfo.

**Perbaikan temuan W-C (24 Nov):** `build_quants` kini mengonversi qty ke UoM produk (`mapping.to_product_qty`: satuan
sama → apa adanya; beda → hanya bila keduanya di `uom_map` dengan `uom_xmlid` sama, qty × ea_factor baris / ea_factor
produk; selain itu baris ditolak `skipped.uom_unconvertible` + error validator `STOCK_UOM_UNCONVERTIBLE`, menggantikan
`uom_mixed`/`STOCK_UOM_MIXED`). `quant.uom_esb/uom_xmlid` = UoM produk; `stock_by_branch` tetap satuan ESB. HPP
`stock_opening` juga dihitung per satuan produk. `odoo_totals.quant.quantity` = UoM produk.
## Gelombang 66 — service `prodplan` (production planning CK di atas ESB, 24 Nov 2026)

docs/04 §30, ADR-051 (Proposed). Worktree `efn-platform-prodplan`, branch `feat/production-planning`. Ditulis tanpa shell:
ruff/pytest belum dijalankan oleh agent ini.

### File (semua baru, `services/prodplan/`)
`efn_prodplan/{__init__,__main__,config,esb,calc,actor,workflow,payloads,store,outbox,jobs,service,api,logs,metrics}.py`,
`tests/{test_config,test_esb,test_calc,test_actor,test_workflow,test_payloads,test_outbox,test_jobs,test_api,
test_store_integration}.py`, `Dockerfile`, `.dockerignore`, `requirements.txt` (`psycopg[binary]==3.2.9`,
`certifi==2026.10.23`), `requirements-dev.txt` (`pytest==9.2.0`), `ruff.toml`, `pytest.ini`, `README.md`.

### Asumsi skema
Kolom/tabel = `infra/postgres/init/23-prodplan-g66.sql` (devsecops) persis §30.6; aplikasi tidak memegang DDL. Purge
audit lewat `prodplan.purge_expired_audit_events()`.

### Keputusan implementasi (tafsir kontrak)
1. API stdlib `ThreadingHTTPServer` (tanpa framework); job + outbox + purge = thread di `serve`. `check-config` tambahan.
2. `PRODPLAN_ENABLED=false`: `/readyz` 200 `{"status":"disabled"}` tanpa PG.
3. Allowlist GET ditambah `/product/bom/{bomID}` (header; daftar BOM tanpa productDetailID — info lead), `/product`,
   `/product/list`, `/product/{productID}`. Nomor dokumen wajib memuat digit (menolak `assembly-actual`/`authorize`
   sebagai `{num}`).
4. Indeks BOM Assembly = daftar + header per BOM (cache 15 menit, bersama API+job). Stok lokasi sumber dibaca lewat
   `GET /product/bom/detail/{bomID}?branchID&locationID` (bukan `branch-stock`, parameternya belum terbukti).
5. Pembatalan: 409 `invalid_state` bila outbox `sending/sent/unknown/delete_*`; `pending` → `failed` kode `cancelled`.
   DELETE berhenti bila baris seq lebih tinggi `delete_failed`.
6. `POST /v1/plans/{id}/calculate` menaikkan `row_version`; hasil job dibuang (`conflict_version`) bila rencana berubah
   selama job. Job yang terputus restart → `failed` kode `interrupted`. Demand bom tidak aktif → job `bom_not_assembly`.
7. `GET /v1/plans` tanpa `brand` = semua brand yang boleh (asumsi frontend). `POST /v1/plans` mengembalikan seluruh plan.
8. `import-pr` default `status_ids=[3,30]`; baris BOM `subtitution` truthy dilewati (belum terverifikasi).
9. Endpoint baru `GET /v1/boms?brand&q&limit` (keputusan lead) — kontrak §30.8 perlu diperbarui lead.

### Belum terverifikasi
ruff/pytest; E2E staging ESB; PG nyata (`test_store_integration.py` skip tanpa `PRODPLAN_TEST_DSN`); field list PR
(`purchaseRequestNum`), `uomName` di `/product`, mode `standard`; jumlah panggilan header BOM awal (R-O10).

### G66 putaran 2 (perbaikan security/QA, 24 Nov 2026)
- SEC-225: `outbox.find_by_marker` paginasi penuh (limit 100, maks 50 halaman) → `ReconcileResult(number, status_id,
  complete)`; tanpa kepastian (batas halaman, galat ESB, detail tak terbaca) baris tetap `unknown`
  (`last_error_code=reconcile_incomplete` setelah grace), tidak pernah `failed`.
- SEC-226: baris ber-nomor tidak pernah turun ke `unknown/failed` (tetap `sent` + `doc_not_found`/`marker_mismatch`);
  `store.outbox_update_sql` menegakkan di SQL (`COALESCE` nomor, status lama dipertahankan). `resend` hanya untuk
  `failed` tanpa nomor dengan kode ∈ {esb_rejected, not_sent, client_error, not_found_after_timeout}; selalu
  rekonsiliasi dulu → nomor diadopsi (`sent`) atau 409 `reconcile_incomplete`.
- QA-B: `get_plan.posting_state` ∈ none|in_progress|needs_attention|complete.
- SEC-227: SoD memakai aktor `audit_event` aksi isi (`workflow.CONTENT_ACTIONS`) — tanpa DDL.
- QA-A: `calculate` pada `approved` (P/W) = refresh; `job.result.plan_status` + `changed`.
- QA-D/E; SEC-231 (qty BOM ≤ 0 dilewati + `warnings.invalid_bom_qty`; `payloads.num` menolak qty ≤ 0 → 422
  `invalid_quantity`); SEC-230 (cancel mengunci outbox `FOR UPDATE` + claim `sending` mensyaratkan rencana belum batal).
- Nama ESB (`names.py`, `/branch/list/user` ditambah ke allowlist): `branch_name`, `production_location_name`,
  `location_name`, `origin_branch_name`, `origin_location_name`, `dest_branch_name`, `dest_location_name`.
- `/v1/boms`: 422 `bad_input`, `q` 2..64 tanpa karakter kontrol. Kode galat job terdaftar di `workflow.JOB_ERROR_CODES`.
- Test QA yang perilakunya berubah: `test_outbox_edges.test_confirm_*` (kini tetap `sent`), `test_outbox` marker
  mismatch, `test_jobs` (qty tak terbaca dilewati; `q` lebih longgar). Test baru: `tests/test_round2.py`.
