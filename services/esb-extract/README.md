# esb-extract (`efn_esb`) — ESB Core → ClickHouse (G41)

**Tujuan.** Menarik data operasional lima brand dari ESB Core (`https://services.esb.co.id/core`, POS/ERP SaaS yang
dipakai brand sampai cutover ke Odoo) ke ClickHouse: raw JSON ter-redaksi (`efn_esb.raw_doc`) → dimensi & fakta
(`efn_dwh.*esb*`) → view `v_esb_*` untuk dashboard G42, migrasi G43, dan forecast G44.

- Kontrak: `docs/04_SERVICE_CONTRACTS.md` §25 (L-105..L-116, Q-ESB1..9). Keputusan: **ADR-040 (Proposed)** —
  belum disetujui klien; production `ESB_ENABLED=false` sampai Q-ESB1 (user read-only) + Q-ESB7 (izin beban).
- **K25-02**: container sendiri (overlay `infra/compose/docker-compose.esb.yml`, profil `data`), siap dipindah ke compose
  `efn-data`. Hanya butuh ClickHouse (network `esb`) + egress ke `services.esb.co.id:443`; tidak menyentuh PostgreSQL,
  RabbitMQ, atau Odoo. Stack inti tetap jalan bila service ini mati.
- Pengecualian "tanpa tulis langsung ke ClickHouse" (L-107): data milik sistem eksternal, bukan transaksi EFN.

## Aturan keras yang ditegakkan kode
| Aturan | Di mana | Test |
|---|---|---|
| GET saja (kecuali `POST /auth/login`), endpoint tulis (`push/import/authorize/upload/restore/delete`) ditolak sebelum koneksi | `http.check_request`, `EsbClient.request/_send` | `test_http.py` |
| Host tetap `https://services.esb.co.id` port 443, tanpa kredensial/query di URL | `config.validate_base_url` | `test_config.py` |
| TLS CA publik (certifi) ≥ 1.2, verifikasi hostname, **tanpa proxy env**, **redirect tidak diikuti** | `http.UrllibTransport` | `test_http.py` |
| 401 → login ulang sekali; 429/5xx/jaringan → backoff eksponensial + jitter (maks 5, `Retry-After`); 4xx lain tidak di-retry | `EsbClient.get/_send_retry` | `test_http.py` |
| Token bucket per brand, `ESB_WORKERS`, batas `ESB_MAX_CALLS_PER_RUN` (run berhenti rapi, `status=limit`) | `TokenBucket`, `CallBudgetExceeded` | `test_http.py`, `test_runner.py` |
| Password/token/Authorization/body login/query tidak pernah di log/repr/pesan exception | `EsbClient`, `BrandConfig(repr=False)` | `test_http.py`, `test_config.py` |
| PDP L-110: field identitas orang dibuang sebelum `raw_doc`; supplier whitelist kode/nama/status; tanpa customer/member | `normalize.redact/redact_supplier` | `test_normalize.py`, `test_runner.py` |
| `ESB_ENABLED=false` → `run` tidur (tidak exit), CLI lain exit 0 "ESB dinonaktifkan" | `__main__` | `test_heartbeat.py` |
| Password `disabled`/kosong/placeholder untuk brand aktif → gagal start dengan pesan jelas | `EsbSettings.from_env` | `test_config.py` |
| SEC-186: brand `auth_failed` 2 siklus `run` berturut-turut → ditangguhkan di memori (`extract_run.status=auth_suspended`, WARNING sekali, tanpa login lagi) sampai proses di-restart; sukses login mereset hitungan | `runner.AuthBreaker/guarded_cycle` | `test_runner.py` |
| SEC-187: body respons ESB maks `ESB_MAX_RESPONSE_BYTES` (`Content-Length` ditolak sebelum baca; baca maks batas+1), tanpa retry, pesan tanpa URL/query | `http.read_limited`, `UrllibTransport` | `test_http.py` |
| SEC-185: `backfill --ignore-window` di production (fail closed: `APP_ENV` kosong/tidak dikenal/`staging` = production) hanya dengan `ESB_ALLOW_IGNORE_WINDOW=true` **dan** rentang 1 hari (`--from` = `--to`); selain itu exit 2 sebelum koneksi apa pun | `planning.validate_ignore_window`, `__main__._cmd_backfill` | `test_planning.py`, `test_runner.py` |

## Alur & idempotensi
Satu **slice** = (brand, entitas, `scope_key`, hari); `scope_key` = branchID (sales, fin_sold, stock) atau `*`.
Worker: GET (paging) → redaksi → transform. Thread utama menulis: `raw_doc` → tabel mart → `bom_ratio` →
`extract_checkpoint` (**terakhir**). Semua baris fakta slice membawa `_version` = `finished_at` checkpoint; view hanya
membaca versi slice terakhir, sehingga tarik ulang (koreksi H-7, backfill `--force`) mengganti isi hari itu termasuk
baris yang dihapus/void di ESB, dan slice yang gagal di tengah tidak terlihat. Tabel = ReplacingMergeTree.

> **Batasan teruji (ClickHouse 24.8):** baris dari slice tanpa checkpoint yang kuncinya sama dengan baris versi lama
> menimpanya lewat `FINAL`/merge. Bila tulis checkpoint gagal setelah fakta tertulis, hari itu tampak kurang (bukan dobel)
> sampai ditarik ulang. Jalur harian pulih sendiri dalam satu siklus; setelah `backfill --force` yang gagal, jalankan ulang
> perintah yang sama. Cek: `extract_run.status != 'ok'`.

- `line_no` (L-113): baris satu `salesNum` diurutkan `(menuCode, rowType, qty, price, total)` + pemecah seri
  `(discount, cogs, menuName)`, lalu diberi nomor 1..n.
- Nama produk tanpa kode (resep, transfer) → alias (`upper`, NFKC, spasi, `YOGHURT→YOGURT`) → `NAME:<alias>` bila tak
  terpetakan; `v_esb_flow_daily` me-resolve ulang lewat `dim_esb_product_alias` saat query.
- Rasio BATCH→EA: satu GET detail simple-manufacturing per BOM baru, disimpan `efn_esb.bom_ratio`.
- `dim_esb_menu` punya dua penulis (21 Nov 2026): slice `master` (`/menu/browse`, `_version` = waktu run) dan slice
  `sales` (`transform_sales_menus`: baris `sales-menu-cogs` dengan `menuCategory` terisi, PT default brand). Alasan:
  `/menu/browse` tidak memuat sebagian kode yang terjual (mis. kode terlaris), sehingga kategori menu Insight kosong.
  Baris dari sales memakai `_version` = **awal hari penjualan 00:00 UTC** (`sales_menu_version`), bukan versi slice:
  run master mana pun setelah hari itu menang atas baris sales (browse = sumber utama), dan antar-hari penjualan hari
  terbaru menang (backfill mundur tidak menimpa). Baris tanpa kategori tidak pernah ditulis dari jalur sales. Sisa:
  kode yang ADA di browse dengan kategori kosong tetap kosong (browse menang); `rows` checkpoint sales kini termasuk
  baris menu.
- Semantik `v_esb_flow_daily` = prototipe `aggregate.py` (L-116); `production_result` selalu `ck_prod`, `simple_mfg`
  `ck_prod`/`store_prod` menurut `dim_esb_branch.is_ck` (asumsi: production-result hanya di CK/CKWH).

## Mode CLI
```
python -m efn_esb run                                    # loop (compose command)
python -m efn_esb backfill --brand pb --from 2024-11-17 --to 2026-11-16 [--entity sales ...] [--force] [--ignore-window]
python -m efn_esb snapshot --brand pb --entity bom_detail [...]   # G43, lihat di bawah
python -m efn_esb probe --brand pb                       # login + jumlah cabang saja
python -m efn_esb health                                 # file heartbeat segar? (opsional)
python -m efn_esb refresh-aggregates                     # bangun ulang tabel agregat Insight (tanpa ESB)
python -m efn_esb refresh-margin [--brand pb]            # G67: hitung ulang mart margin bahan (tanpa ESB)
```

**Margin bahan (G67, `efn_esb/margin.py`, docs/04 §31, L-259..L-264; ADR-040/041 Proposed).** Indeks harga bahan
dan atribusi margin dari resep ESB (`dim_esb_bom(_line)`, versi snapshot terakhir) × harga terima PO
(`fact_esb_purchase_line`, versi slice ber-checkpoint) × penjualan/COGS ESB (`agg_esb_sales_daily`). Hasil:
`efn_dwh.mart_esb_ing_price_monthly`, `mart_esb_menu_cost_monthly`, `mart_esb_ing_group_monthly`
(`infra/clickhouse/init/24-margin-bahan.sql`, MergeTree + `storage_policy = 'encrypted'`, TTL 60 hari, dibuat admin);
Insight membaca `efn_insight.v_esb_*` (DEFINER, row policy kelas PT, persona `exec`).
- Algoritme §31.2 langkah 1–6 (lihat docstring modul): harga bulanan berbobot `receipt_qty` dalam satuan beli dominan,
  `price_4m` bergulir 4 bulan, indeks terhadap bulan dasar, kelompok = sub-kategori PO (tanpa aturan nama di kode),
  uraian BOM rekursif proporsional nilai (siklus/kedalaman > 8 → daun), `cost_idx` per menu kanonik (` T` / `KIOSK - `
  digabung), atribusi Laspeyres diskalakan ke COGS ESB. Jendela = `ESB_MARGIN_MONTHS` bulan kalender **penuh** terakhir
  (bulan berjalan tidak ikut).
- **Versi-insert** (L-263): setiap refresh INSERT versi baru (`_version` = mulai run); view `efn_dwh.v_esb_*` = versi
  terakhir per brand. esb_writer hanya `SELECT, INSERT` (tanpa CREATE/DROP/ALTER/TRUNCATE). Tidak ada tulisan sebelum
  semua sumber terbaca dan dihitung; prasyarat kosong → `error` `no_company|no_bom|no_purchase|no_sales` tanpa tulisan.
- Kunci = kunci heartbeat agregat (`aggregate_refresh_lock`): tidak pernah bersamaan dengan refresh margin lain atau
  `refresh-aggregates` (bukan pemegang → `busy`). Kesegaran: `efn_esb.aggregate_refresh` `name = 'margin_<brand>'`.
- Jadwal di loop `run`: brand di `ESB_MARGIN_BRANDS`, hari WIB = `ESB_MARGIN_WEEKDAY`, jam WIB ≥ `ESB_MARGIN_HOUR_WIB`,
  refresh `ok` terakhir > 6 hari → snapshot `bom_detail` brand itu → hitung → tulis. Paling banyak satu percobaan per
  brand per hari WIB; snapshot gagal → `error` `snapshot_<status>` tanpa hitung; gagal apa pun tidak menghentikan
  ekstraksi harian. Start `run` gagal (fail-fast) bila brand margin tidak ada di `ESB_BRANDS` atau `purchase_line`
  tidak ada di `ESB_ENTITIES`.
- `company_code` = **satu** PT per brand (PT dengan cabang aktif terbanyak di `dim_esb_branch`); menduplikasi baris per
  PT akan menggandakan angka bagi pengguna yang melihat beberapa PT. Brand multi-PT (Bacha BCF/BCR) → lihat laporan agent.
- Runbook: `docs/runbooks/esb-extract.md` §6b.

**Tabel agregat Insight (`efn_esb/aggregates.py`, keputusan pemilik proyek 22 Nov 2026; ADR-040/041 Proposed).**
Wrapper `efn_insight.v_esb_sales_branch_daily`, `v_esb_sales_daily`, `v_esb_flow_daily` membaca `efn_dwh.agg_esb_*`
(MergeTree, kolom = view `efn_dwh.v_esb_*` dengan nama sama, `infra/clickhouse/init/20-esb-aggregates.sql`), bukan view
yang menghitung FINAL + checkpoint + uniqExact setiap query (galat 159/241 Insight setelah data 1 tahun).
`efn_insight.v_esb_menu_mix` membaca `efn_dwh.v_esb_menu_mix_agg` (badan = `v_esb_menu_mix`, di atas agg).
Agg **dan** build permanen `<agg>_build` dibuat admin (`clickhouse-init`, MergeTree, `storage_policy = 'encrypted'`);
esb-extract tidak pernah membuat/menghapus tabel (review keamanan SEC-AG-01). Refresh per bulan kalender (rentang dari
`extract_checkpoint`): cek kunci → `TRUNCATE` build → `INSERT` bulan itu dari view → pagar penyusutan → cek kunci →
`ALTER TABLE agg REPLACE PARTITION ID '<yyyymm>' FROM build` → heartbeat; lalu `DROP PARTITION` untuk bulan agg yang
hilang dari sumber. **Atomik per bulan, tidak atomik lintas bulan** (selama refresh sebagian bulan sudah baru, sebagian
masih lama; refresh gagal di tengah = bulan yang sudah diganti tetap baru).
- Pagar penyusutan (SEC-AG-03): bulan dengan hasil 0 baris padahal agg berisi, turun > 50%, atau bulan agg yang tidak
  ada lagi di sumber → dilewati, agregat `error` (`shrink <yyyymm,...>`); reset disengaja: `refresh-aggregates
  --allow-shrink` (manual saja, tidak pernah dari loop).
- Kunci (SEC-AG-02): `efn_esb.aggregate_refresh_lock`; waktu baris diisi server; pemegang = run belum dilepas dengan
  heartbeat < 15 menit, urut (mulai, run_id); dicek ulang sebelum setiap TRUNCATE/REPLACE/DROP PARTITION; heartbeat tiap
  bulan; selesai/gagal → baris `released`. Bukan pemegang → semua agregat `busy` tanpa menyentuh tabel.
- Dipanggil otomatis di akhir siklus `run`/`backfill` yang menulis slice `sales/fin_sold/production/delivery/transfer/
  master`; gagal hanya dicatat (log tanpa pesan galat/isi data), tidak menggagalkan ekstraksi. Subcommand: exit 1 bila ada
  agregat `error`/`busy`. Kesegaran: `efn_esb.aggregate_refresh`.
- Hak `esb_writer`: build `SELECT, INSERT, TRUNCATE`; agg `SELECT, INSERT, ALTER DELETE` (REPLACE/DROP PARTITION); tanpa
  CREATE/DROP TABLE (dicabut eksplisit). `allow_ddl = 1` tetap (TRUNCATE/ALTER termasuk DDL); batas = grant per tabel.
- Kolom view sumber berubah → `clickhouse-init` mendeteksi kolom agg/build != view, menghapus dan membuat ulang keduanya
  kosong; lalu `refresh-aggregates`. `clickhouse-init` GAGAL bila salah satu dari 6 tabel bukan MergeTree/encrypted.
- Bukan refreshable MV (eksperimental di 24.8) dan bukan MV-on-insert (fakta di-dedup lewat `_version` + checkpoint).
Prosedur (refresh pertama setelah deploy, setelah salin data DR, pemulihan, verifikasi): runbook
`docs/runbooks/esb-extract.md` §6a.
`run`: tiap `ESB_INTERVAL_SECONDS`, brand satu per satu: cabang (setiap siklus) → master (sekali/hari WIB) → H-1 dan
koreksi H-7 (sekali/hari setelah `ESB_SALES_READY_HOUR_WIB`=03:00) + gap fill `ESB_GAP_FILL_DAYS` hari tanpa checkpoint.
`backfill`: satu brand per proses (L-111), hari terbaru dulu, slice `done` dilewati (resumable); di luar
`ESB_BACKFILL_WINDOW` (WIB) tidak memanggil apa pun / berhenti menjadwalkan (`status=window_closed`) → jalankan ulang
perintah yang sama malam berikutnya. Contoh: `docker compose ... run --rm esb-extract python -m efn_esb backfill ...`.
`--ignore-window` (dev/uji) di production ditolak kecuali `ESB_ALLOW_IGNORE_WINDOW=true` dan `--from` = `--to`.

**`snapshot` (G43, docs/04 §27.11, ADR-042).** Entitas migrasi ESB → Odoo, dijalankan per kebutuhan (bukan loop `run`):
```
python -m efn_esb snapshot --brand pb --entity bom_detail --entity menu_price --entity customer --entity location
python -m efn_esb snapshot --brand pb --entity stock_opname --entity ap_open --entity ar_open --entity coa \
  --date <cutover-1> --allow-unverified [--opname-window 3]
```
| Entitas | Endpoint (GET) | Tabel (14-g43-migration.sql) | Redaksi | Status |
|---|---|---|---|---|
| `bom_detail` (E1) | `/product/bom` → `/product/bom/{bomID}` per BOM aktif | `dim_esb_bom`, `dim_esb_bom_line` | `redact()` (createdBy/editedBy/notes) | P1, belum uji GET nyata |
| `menu_price` (E2) | `/menu/browse-for-product` | `dim_esb_menu_price` (tabel terpisah dari `dim_esb_menu`: penulis beda) | `redact()` | P1, belum uji GET nyata |
| `customer` (E4) | `/customer` | `dim_esb_customer` | whitelist `CUSTOMER_FIELDS` (tanpa alamat/PIC/telepon/NPWP) | P1, belum uji GET nyata |
| `location` (E5) | `/location?branchID=` per cabang | `dim_esb_location` | `redact()` | P1, belum uji GET nyata |
| `stock_opname` (E6) | `/inventory/stock-opname` (+ `/{num}`) | `fact_esb_stock_opname` | `redact()` (petugas, lampiran) | P2, **default mati** |
| `ap_open` (E7) | `/supplier-payable/invoice-aging?payableDate=` per cabang | `fact_esb_ap_open` | whitelist `AP_FIELDS` (kode supplier) | P2, **default mati** |
| `ar_open` (E8) | `/customer-receivable?type=2&receivableDate=` per cabang | `fact_esb_ar_open` | whitelist `AR_FIELDS` (kode pelanggan) | P2, **default mati** |
| `coa` (E9) | `/accounting/coa` | `dim_esb_coa` | whitelist `COA_FIELDS` | P2, **default mati** |

E10 (neraca saldo/GL) tidak dibangun: di spec ESB `/accounting/general-ledger(/summary)` adalah **POST** (melanggar
GET-only) → ekspor resmi Finance (Q-ESB21). Snapshot selalu menarik ulang (bukan resumable), anggaran =
`ESB_BACKFILL_MAX_CALLS`, satu slice/checkpoint per (entitas, `*`, hari), `extract_run.mode='snapshot'`. Grant tabel baru
hanya `esb_writer` + `mig_rw` (tidak ke Metabase/Insight).

**G65 (docs/04 §27.13, ADR-042 §0b, L-250..L-258).** Histori pembelian + saldo stok awal (pilot PB), modul
`extract_g65.py` / `transform_g65.py`, DDL `23-g65-esb-purchase-stock.sql`:
```
python -m efn_esb backfill --brand pb --from 2024-12-01 --to <kemarin> --entity purchase_line [--force]
python -m efn_esb snapshot --brand pb --entity stock_opening --date <cutover>      # --date WAJIB
```
| Entitas | Mode | Endpoint (GET) | Slice | Tabel | Redaksi | Status |
|---|---|---|---|---|---|---|
| `purchase_line` | harian + backfill, opt-in `ESB_ENTITIES`/`--entity` (default mati) | `/report/purchase/purchase-recapitulation/purchase?dateFrom=D&dateTo=D` (limit 1000) + `/purchase/purchase-order?dateFrom=D&dateTo=D` (limit 100), join `purchaseNum` | `('*', D)` | `fact_esb_purchase_line` | whitelist `PURCHASE_RECAP_FIELDS`/`PURCHASE_HEADER_FIELDS` (notes, additionalInfo, costCenterName, projectName, createdBy, emailSupplier, printedBy dibuang) | dibangun, belum uji GET nyata via extractor |
| `stock_opening` | `snapshot`, `--date` = tanggal cutover (wajib) | `/report/stock-movement?startPeriod=D&endPeriod=D&branchCode=<kode>&unitToShow=Base Unit` (limit 100) | `(<branch_id>, D)` per cabang dim_esb_branch | `fact_esb_stock_opening` | whitelist `STOCK_MOVEMENT_FIELDS` | dibangun, belum uji GET nyata via extractor |

- `purchase_line`: cabang/supplier dari header PO (`map_source='header'`, kode supplier lewat kolom baru
  `dim_esb_supplier.supplier_id`); tanpa header/ID tak dikenal → nama tepat-satu (NFKC, upper, spasi dirapikan;
  `'name'`); gagal/ambigu → `branch_id=0`, `supplier_code=''` (`'none'`, baris tetap disimpan). `map_source` = pemetaan
  terlemah di antara cabang dan supplier. `line_no` stabil per `purchaseNum` (pola L-113). Semua status disimpan;
  daftar izin status = tugas export (W-B). `supplier_id` kosong di store → `master` dipaksa sekali per proses.
- SEC-G65-01: baris recap/header dengan `purchaseDate` ≠ hari slice (atau kosong) → slice error tanpa checkpoint.
- `stock_opening`: hanya `transactionType="Beginning"` dan `qtyBalance != 0`; setiap baris wajib `branchID` = cabang
  diminta, atau tanpa `branchID` → `branchCode` = kode diminta (selain itu slice error); cabang AKTIF tanpa satu pun
  baris `Beginning` (respons kosong) → slice error tanpa checkpoint (SEC-G65-02); kunci ganda dijumlah; angka `Decimal(str(x))`. raw = baris tersimpan + ringkasan
  `{pages, rows_total, rows_beginning, rows_kept}`.
- `stock_opening` G65b (gladi 24 Nov 2026): parameter tetap `unitToShow=Base Unit` (konstanta
  `extract_g65.STOCK_MOVEMENT_UNIT`; nilai persis dengan spasi — `Stock Unit`/`baseUnit` → HTTP 400). Tanpa itu ESB
  mengembalikan satuan campur per baris (BOX/PACK/EA…); dengan itu semua baris dalam satuan dasar produk ESB (BOX→GR,
  PACK→PCS), total `amountBalance` identik. Export memakai satuan baris ini bila `dim_esb_product.uom` kosong (semua
  produk PB). Snapshot `stock_opening` yang diambil sebelum perubahan ini wajib diambil ulang.
- Cek kualitas: view terpisah `efn_dwh.v_esb_g65_quality` (`purchase_unmapped`, `stock_opening_missing_branch`), bukan
  tambahan `v_esb_data_quality` — view itu INVOKER dan dibaca Metabase/Insight yang sengaja tidak punya hak tabel G65.

**Brand `auth_suspended` (SEC-186).** Login ESB ditolak 2 siklus berturut-turut → brand tidak login lagi (melindungi akun
ESB yang juga dipakai kasir dari lockout). Siklus berikutnya hanya menulis `extract_run.status='auth_suspended'`
(muncul di `v_esb_data_quality` `last_run_not_ok`). Pemulihan: perbaiki secret `esb_core_password_<brand>` (atau keluarkan
brand dari `ESB_BRANDS`) lalu **restart** `esb-extract`; penangguhan tidak disimpan di disk/ClickHouse. Siklus `error`
(jaringan/ClickHouse) tidak menambah maupun mereset hitungan.

## Env (selaras overlay `docker-compose.esb.yml`)
| Env | Default | Catatan |
|---|---|---|
| `ESB_ENABLED` | `false` | kill switch |
| `ESB_BASE_URL` | `https://services.esb.co.id/core` | allowlist host |
| `ESB_BRANDS` | (kosong) | subset `pb,wtz,bacha,imd,cup` |
| `ESB_USER_<BRAND>`, `ESB_PASSWORD_<BRAND>_FILE` | — | secret `esb_core_password_<brand>` |
| `ESB_COMPANY_<BRAND>` | PB/WTZ/BCF/IMD/CUP | jarang perlu diubah |
| `ESB_BRANCH_COMPANY_OVERRIDES` | (kosong) | `bacha:12=BCR,bacha:15=BCR` (pemisah `,` atau `;`) |
| `ESB_CK_BRANCHES` | `pb:4\|38,wtz:5\|6,imd:2,cup:4\|6` (G43 §27.11) | overlay compose masih `pb:4\|38` → diselaraskan devsecops |
| `ESB_NON_STORE_BRANCHES` | (kosong) | cabang non-toko tambahan, format sama |
| `ESB_RATE_PER_SEC` / `ESB_WORKERS` / `ESB_MAX_RETRIES` | `2.0` / `4` / `5` | per brand |
| `ESB_MAX_CALLS_PER_RUN` / `ESB_BACKFILL_MAX_CALLS` | `20000` / `60000` | budget run / satu proses backfill |
| `ESB_INTERVAL_SECONDS` | `3600` | |
| `ESB_SALES_READY_HOUR_WIB` / `ESB_CORRECTION_DAYS` / `ESB_GAP_FILL_DAYS` | `3` / `1,7` / `7` | |
| `ESB_BACKFILL_WINDOW` / `ESB_BACKFILL_INCLUDE_INACTIVE` | `22:00-05:00` / `true` | toko nonaktif ikut backfill (riwayat toko tutup) |
| `ESB_ENTITIES` | `branch,master,sales,fin_sold,payment,production,delivery,transfer` | `stock`, `purchase_line` (G65) opsional |
| `ESB_HTTP_TIMEOUT_SECONDS` / `ESB_MAX_PAGES` | `60` / `500` | |
| `ESB_MAX_RESPONSE_BYTES` | `33554432` (32 MiB) | rentang `1048576`..`134217728` (1–128 MiB); body lebih besar → slice error, tanpa retry (SEC-187) |
| `ESB_ALLOW_IGNORE_WINDOW` | `false` | production: izinkan `backfill --ignore-window` hanya untuk rentang 1 hari, dengan persetujuan (SEC-185) |
| `ESB_HEARTBEAT_FILE` / `ESB_HEALTH_MAX_AGE_SECONDS` | `/tmp/efn-esb.heartbeat` / `7800` | |
| `ESB_MARGIN_BRANDS` | (kosong = fitur mati) | G67; subset `ESB_BRANDS`, wajib `purchase_line` di `ESB_ENTITIES` |
| `ESB_MARGIN_MONTHS` / `ESB_MARGIN_WEEKDAY` / `ESB_MARGIN_HOUR_WIB` | `13` / `0` (Senin) / `2` | rentang 2..24 / 0..6 / 0..23 |
| `CH_HOST/CH_PORT/CH_USER/CH_PASSWORD_FILE/CH_SECURE/CH_CA_FILE/CH_DATABASE/CH_RAW_DATABASE` | `clickhouse/8443/efn_esb/–/true/efn_ca_crt/efn_dwh/efn_esb` | pola ETL |

## Estimasi beban backfill
`sales-menu-cogs` limit tetap 20 baris → ±23 halaman/toko/hari (±450 baris), ±0,4 dtk/halaman; `fin_sold` ±3
halaman/toko/hari; payment 1/hari; produksi/delivery/transfer 1 list/hari + 1 GET per dokumen. Total ±26–30 GET per
toko-hari. **24 bulan PB ±25 toko ≈ 420 rb GET** sales saja. Pada 2 req/dtk dibatasi token bucket, jendela 7 jam ≈
50 rb GET/malam → **±9–10 malam untuk PB**; brand lain berurutan (satu brand per waktu). Default
`ESB_BACKFILL_MAX_CALLS=60000` ≈ satu malam per proses. Jangan jalankan `run` dan `backfill` brand yang sama bersamaan
tanpa menurunkan `ESB_RATE_PER_SEC` (bucket per proses, bukan global).

## Risiko yang dimitigasi
R-S (kredensial pihak ketiga & egress): GET-only, host allowlist di aplikasi, tanpa proxy/redirect, secret per brand.
PDP (UU 27/2022): redaksi L-110 + supplier whitelist + tanpa customer. Beban ke API vendor: token bucket, jendela malam,
budget. Integritas: checkpoint terakhir + versi slice (idempoten, re-run aman).

## Salinan kecil dari services/etl
`tls.py` (subset), `read_secret`/`_bool` di `config.py`, `clickhouse_client_kwargs` di `store.py`, pola heartbeat —
disalin karena tidak ada paket bersama antar-service Python.

## Belum / belum diverifikasi
- **Uji GET nyata 18 Nov 2026 (PB, 1 hari, extractor + transform asli, tanpa menyimpan data):** semua entitas
  (branch, master, payment, production, delivery, transfer, sales, fin_sold) berhasil di-parse; 389 panggilan.
  Master memberi produk, menu, supplier, dan alias; alias FIN untuk penjualan 100% terpetakan pada hari uji. Tidak ada
  kunci `(sales_num, line_no)` dobel.
  Paket: `PACKAGE_COMPONENT` punya `total` = 0 dan HPP ada di komponen (header ≈ 0), sehingga Σ semua baris tidak dobel.
  `total − discount` ≈ 89,6% dari pembayaran cabang, artinya `total` belum termasuk pajak (PB1 10%) dan service.
- Masih belum diverifikasi: `/inventory/stock-list` (uji 1 toko = 0 baris; entitas `stock` default mati);
  `production-result` dihitung sebagai `ck_prod` (asumsi hanya terjadi di CK) untuk brand selain PB; brand selain PB
  (password belum tersedia).
- Beban nyata per hari (PB): delivery ±98 panggilan (±2 menit, detail per dokumen), production ±132 pada hari pertama
  (detail rasio BOM; sesudahnya di-cache di `bom_ratio`), sales ±23 per toko.
- `payload_sha256` disimpan tetapi belum dipakai untuk melewati transform (transform selalu jalan; idempoten).
- Row policy `efn_t2s` untuk `v_esb_*` dan user `efn_insight` → G42. `v_esb_sales_hourly` tidak dibuat (L-112),
  `v_esb_stock_cover` ditunda.
- Retensi mart: tabel ESB belum masuk loop TTL 10 tahun `clickhouse-init.sh` (tanpa PII, tidak mendesak).
- G67 margin bahan: belum dijalankan terhadap ClickHouse/data nyata (hanya test sintetis); kecocokan nama BOM `Menu` ↔
  `menu_name` penjualan, arti status PO batal, dan rasio biaya/unit BOM×PO vs COGS ESB (median 1,00 di analisis lokal)
  wajib dicek di gladi efn-prod (runbook §6b).
- Tidak ada lock global "satu backfill per brand"; disiplin operasional (runbook).
- Agregat Insight (22 Nov 2026): desain awal (EXCHANGE) terverifikasi di efn-prod (total per bulan view = agg, 15
  bulan). Desain REPLACE PARTITION + kunci heartbeat (SEC-AG-01/02/03): grant diuji 25 Nov 2026 di ClickHouse
  24.8.14 sekali pakai — `SELECT, INSERT, TRUNCATE` build + `SELECT, INSERT, ALTER DELETE` agg cukup untuk TRUNCATE
  build, REPLACE PARTITION, DROP PARTITION; CREATE TABLE, DROP TABLE, TRUNCATE agg ditolak (ACCESS_DENIED). User
  tanpa hak `system.parts` → daftar partisi lewat kolom virtual `_partition_id`. Belum: `clickhouse-init.sh` penuh
  (pagar skema/engine/policy) dan refresh nyata di efn-stg/efn-prod.
  Kunci: celah kecil bila dua proses menulis baris kunci hampir bersamaan dan baris yang lebih awal terlihat setelah jeda
  2 dtk; cek ulang sebelum setiap REPLACE membatasi dampaknya.
- G43 `snapshot`: semua entitas (P1 dan P2) dibangun dari spec publik tanpa uji GET nyata. Arti kolom P2 **[B]**:
  `qty` vs `stockQty` opname, `receivableTotal`/`payableTotal` = sisa atau nilai faktur, apakah invoice-aging hanya faktur
  terbuka, status opname yang final. `customer_name` bisa nama orang (pelanggan perorangan) → data pribadi (inventaris
  PDP). `bomTypeID/Name` arti [B] (Q-ESB17); header BOM ESB tanpa qty hasil (rasio dari `bom_ratio`).

## G69 — ESB OMS (penjualan level struk)
**Tujuan.** Melengkapi ESB Core dengan data per struk dari ESB OMS (`https://esbcore.co.id/external/general/*`, brand
PB dulu): jam transaksi, kanal (visit purpose), pax, promo, pembayaran, baris menu ber-`menuCode`. Konsumen: Insight
`/fnb/store` (penjualan per jam & kanal) dan `/flow` (sell-through per jam). Kontrak: docs/04 §33; **ADR-040 §6b
(amandemen, Proposed)**. Risiko: **R-S17** (akun brand saat ini admin → production menunggu Q-ESB1), **R-O09/R-O10**
(beban API vendor intraday & ketergantungan SaaS: kill switch, budget, token bucket terpisah, Q-OMS2), **R-L01**
(PDP/PCI: denylist §33.6 sebelum raw & mart).

**Kode.** `efn_esb/oms_http.py` (klien POST allowlist 5 path persis, Basic auth, User-Agent eksplisit, redirect tidak
diikuti; klien Core tidak berubah), `oms_transform.py` (strip PII/PCI, transform murni, pemetaan menu→FIN),
`oms_runner.py` (paginasi, slice, siklus, backfill, loop thread), `config.OmsSettings`. DDL
`infra/clickhouse/init/26-g69-esb-oms.sql`; grant `access/esb-access.sql` (writer) + `access/insight-access.sql` /
`insight-esb-policy.sh` (wrapper + row policy, `ESB_POLICY_EXPECTED=27`).

**Runner.** Loop OMS = **thread terpisah** di proses `run` (klien HTTP, bucket, budget, circuit breaker auth, dan klien
ClickHouse sendiri) → gagal OMS tidak menghentikan Core dan sebaliknya. Dipilih karena loop Core sekuensial per jam;
menyisipkan interval 15 menit ke loop itu akan mengikat jadwal keduanya. Per siklus: master harian (visit purpose,
payment method) + pemetaan menu→FIN harian; hari berjalan (di `ESB_OMS_INTRADAY_HOURS`) = summary 1 panggilan lalu
semua cabang ber-struk ditarik ulang utuh; H-1/H-7 + gap fill final setelah `ESB_SALES_READY_HOUR_WIB` (penanda hari
`oms_day`, ditulis hanya bila semua cabang sukses). Slice yang mencapai `ESB_OMS_MAX_PAGES` = `partial`, tanpa
checkpoint. CLI: `oms-probe --brand pb` (jumlah saja), `oms-backfill --brand pb --from D --to D [--force]` (jendela
`ESB_BACKFILL_WINDOW`, budget `ESB_OMS_BACKFILL_MAX_CALLS`, summary 1 panggilan per ≤ 31 hari).

**Env.** `ESB_OMS_ENABLED=false` (kill switch; **`true` hanya berlaku bila `ESB_ENABLED=true`** — kalau tidak, proses
tidak start, exit 2), `ESB_OMS_BRANDS=pb` (subset `ESB_BRANDS`, kredensial Core yang sama), `ESB_OMS_BASE_URL`
(`https://esbcore.co.id` persis), `ESB_OMS_RATE_PER_SEC=2.0`, `ESB_OMS_INTERVAL_SECONDS=900`,
`ESB_OMS_INTRADAY_HOURS=07-23`, `ESB_OMS_CORRECTION_DAYS=1,7`, `ESB_OMS_MAX_CALLS_PER_RUN=5000`,
`ESB_OMS_BACKFILL_MAX_CALLS=60000`, `ESB_OMS_MAX_PAGES` (default = `ESB_MAX_PAGES`).

**Pengunci production (SEC-OMS-04).** Di production — `tls.is_production` (fail closed: `APP_ENV`/`NODE_ENV` kosong atau
di luar `dev/development/local/test/ci` = production; `staging` juga terhitung production) — `ESB_OMS_ENABLED=true`
membuat proses tidak start, **kecuali** `ESB_OMS_ALLOW_PRODUCTION=true` (persis `true`). Keputusan pemilik proyek
25 Nov 2026: efn-prod lokal = staging berdata produksi yang direset saat go-live, jadi flag ini boleh **hanya** di
override lokal efn-prod (`prod-local.overrides`, tidak di-commit). Repo/CI: default `false`, ditegakkan
`check-encryption.sh`. Go-live sungguhan: hapus flag sampai Q-ESB1, Q-OMS2, Q-OMS3, dan persetujuan DPO.

**PDP tambahan (SEC-OMS-01/02).** `tableName` dibuang (label meja bisa berisi nama pelanggan/driver); kunci yang memuat
`card`/`verification`/`approval`/`account`/`member`/`phone`/`email` dibuang rekursif; elemen `salesPayments[]` di-whitelist
9 kunci (`oms_transform.PAYMENT_FIELDS`).

**Sisa risiko yang diterima (SEC-OMS-03).** Circuit breaker auth OMS (`oms_loop`) terpisah dari breaker Core walau akun
ESB-nya sama, sehingga saat password berubah percobaan gagal ke akun bisa ±2× batas SEC-186 (2 siklus Core + 2 siklus
OMS). CLI `oms-probe`/`oms-backfill` tidak memakai breaker (sekali jalan, manual). Mitigasi operasional: setelah rotasi
password ESB, perbarui secret lalu restart `esb-extract`; jangan menjalankan `oms-*` CLI berulang saat auth gagal.

**Pemetaan menu → FIN.** Resep G43 (`dim_esb_bom` + `_line`) memuat HPP dan tidak boleh dibaca Insight, jadi esb-extract
menurunkan tabel `dim_esb_oms_menu_fin` (qty FIN per menu saja) sekali sehari. Kunci, urutan prioritas: `menu_code` =
`bom_code` BOM `Menu` → `menu_code` → `dim_esb_menu_price.bom_name` → nama menu kanonik (§31.2 butir 4). Komponen FIN =
baris BOM langsung yang `is_fin` (dim_esb_product) / aturan FIN §25.5; sub-BOM tidak diurai. Butuh snapshot
`bom_detail` (G43/G67) — tanpa resep, pemetaan kosong dan `v_esb_oms_fin_mapping_quality` menunjukkan 100% tak
terpetakan.

**G69 asumsi bentuk & status [B].** (1) Respons = list atau objek ber-`data`/`result`. (2) `packages[]`/`extras[]` berisi
objek dengan `menuID`/`menuCode`/`menuName`/`qty`/`price`/`total` seperti `salesMenus[]`; bentuk lain diabaikan dan
dihitung di `extract_run.errors`; qty anak disimpan apa adanya (per induk atau total — belum diketahui). (3) Struk
**tidak final** bila `statusName` memuat void/cancel/batal/open/terbuka/draft/pending/hold/refund; baris batal sama
(nilai `statusID` per status belum diverifikasi — perlu fixture probe). (4) `net_sales` struk = Σ(`subtotal` −
`discountTotal`); `net_sales` menu = Σ(`total` − `discountValue`). (5) `branchCode` OMS = `branch_code` ESB Core
(`dim_esb_branch`); cabang tak dikenal → `branch_id` 0, PT default brand. (6) Angka rusak = slice gagal (tanpa
checkpoint).

**Belum / belum diverifikasi.** Belum dijalankan terhadap ESB OMS atau ClickHouse nyata (hanya test sintetis); view
belum dieksekusi di ClickHouse 24.8. Rekonsiliasi `v_esb_oms_recon_daily` (struk final vs summary vs pembayaran Core)
belum dibuktikan pada data nyata. Production sungguhan: terkunci (SEC-OMS-04, `ESB_OMS_ALLOW_PRODUCTION` hanya untuk efn-prod lokal pra-go-live) sampai Q-ESB1 (akun read-only), Q-OMS2
(izin beban intraday), Q-OMS3 (retensi) dan DPO. Bila `fact_esb_oms_bill` sudah dibuat dengan kolom `table_name` (DDL
sebelum SEC-OMS-01), `CREATE IF NOT EXISTS` tidak menghapusnya: hapus tabel kosong itu manual (admin) lalu jalankan
ulang `clickhouse-init`.
