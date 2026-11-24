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
```

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
| `ESB_ENTITIES` | `branch,master,sales,fin_sold,payment,production,delivery,transfer` | `stock` opsional |
| `ESB_HTTP_TIMEOUT_SECONDS` / `ESB_MAX_PAGES` | `60` / `500` | |
| `ESB_MAX_RESPONSE_BYTES` | `33554432` (32 MiB) | rentang `1048576`..`134217728` (1–128 MiB); body lebih besar → slice error, tanpa retry (SEC-187) |
| `ESB_ALLOW_IGNORE_WINDOW` | `false` | production: izinkan `backfill --ignore-window` hanya untuk rentang 1 hari, dengan persetujuan (SEC-185) |
| `ESB_HEARTBEAT_FILE` / `ESB_HEALTH_MAX_AGE_SECONDS` | `/tmp/efn-esb.heartbeat` / `7800` | |
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
