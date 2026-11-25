# Runbook: ekstraksi ESB Core → ClickHouse (`esb-extract`, G41)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §25 · Keputusan: `docs/decisions/ADR-040-integrasi-esb-core-datamart.md` (**Proposed**) · Kode: `services/esb-extract` (paket `efn_esb`) · Compose: `infra/compose/docker-compose.esb.yml` (overlay, profil `data`) · Skema: `infra/clickhouse/init/12-g41-esb.sql`, akses `infra/clickhouse/access/esb-access.sql`.

## 0. Ringkas

| Hal | Nilai |
|---|---|
| Fungsi | Menarik data POS ESB (penjualan per baris menu, pembayaran harian, produksi CK, pengiriman, transfer, master) dari **ESB Core API produksi** `https://services.esb.co.id/core` ke ClickHouse (`efn_esb.*` raw + checkpoint, `efn_dwh.*esb*` mart). |
| Arah | **Hanya baca** dari ESB: `GET` saja, kecuali `POST /auth/login`. Klien menolak method lain di level kode. |
| Kredensial | Satu user API per brand. Username = env `ESB_USER_<BRAND>` yang diisi di `infra/compose/.env` lokal (gitignored) / config rilis, **tidak pernah di repo** (SEC-180); password = Docker secret `esb_core_password_<brand>`. ClickHouse: user `efn_esb` (secret `clickhouse_password_esb`, role `esb_writer`, SELECT/INSERT tabel ESB saja; tanpa CREATE/DROP TABLE; pengecualian tabel agregat Insight: TRUNCATE pada `efn_dwh.agg_esb_*_build`, ALTER DELETE (REPLACE/DROP PARTITION) pada `efn_dwh.agg_esb_*`, §6a). |
| Jaringan | `esb` (internal: hanya `clickhouse` ↔ `esb-extract`) + `egress-esb` (internet, **khusus** `esb-extract`, `enable_icc=false`, subnet `172.30.8.0/24`; SEC-183). **Tidak** di network `data` (PostgreSQL/RabbitMQ) maupun `egress` bersama (BFF dll.). Allowlist host ESB ditegakkan aplikasi (§9). |
| Kill switch | `ESB_ENABLED=false` (default) → proses idle, tidak memanggil ESB maupun ClickHouse. |
| Data pribadi | Tidak menarik customer/member. Supplier hanya kode, nama perusahaan, status. `salesNum` = ID transaksi (bukan PII). Raw JSON di `efn_esb.raw_doc` TTL 36 bulan. |
| Stack | K25-02: siap dipindah ke compose `efn-data` (mesin lain). Stack inti tidak bergantung pada service ini. |

| Brand | Env username (nilai di `.env` lokal) | Secret password | PT (`company_code`) |
|---|---|---|---|
| `pb` | `ESB_USER_PB=<user API brand PB>` | `esb_core_password_pb` | PB |
| `wtz` | `ESB_USER_WTZ=<user API brand WTZ>` | `esb_core_password_wtz` | WTZ |
| `bacha` | `ESB_USER_BACHA=<user API brand Bacha>` | `esb_core_password_bacha` | BCF (override cabang → BCR lewat `ESB_BRANCH_COMPANY_OVERRIDES`) |
| `imd` | `ESB_USER_IMD=<user API brand IMD>` | `esb_core_password_imd` | IMD (company Odoo menyusul G43) |
| `cup` | `ESB_USER_CUP=<user API brand CUP>` | `esb_core_password_cup` | CUP (company Odoo menyusul G43) |

Nilai username nyata diperoleh dari pemilik akun ESB brand / log kustodi (lihat [key-custody.md](key-custody.md)), bukan dari repo. Akun yang tersedia saat ini berhak **admin** di ESB; user teknis read-only per brand masih diminta (Q-ESB1). Username bukan secret, tetapi repo publik: username akun admin + host login memudahkan password spraying dan penguncian akun (SEC-180).

Perintah di bawah memakai variabel ini (Git Bash di laptop dev, dari root repo):

```bash
cd infra/compose
DC="docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.esb.yml --profile core --profile data"
chq() { $DC exec -T clickhouse sh -c 'CLICKHOUSE_USER="${CLICKHOUSE_USER:-efn}" CLICKHOUSE_PASSWORD="$(cat /run/secrets/clickhouse_password)" clickhouse-client --host clickhouse --multiquery'; }
```

Staging lokal/prod lokal (K25-06): ganti `docker-compose.dev.yml` dengan `docker-compose.prod.yml` (+ `docker-compose.host.yml` di host) dan nama project environment yang bersangkutan.

## 1. Prasyarat sekali saja

1. **Secret ClickHouse `clickhouse_password_esb`** (jenis `random`):
   - Dev/CI: `bash scripts/gen-secrets.sh` (idempoten; hanya membuat file yang belum ada).
   - Staging/production lewat `deploy.yml`: tambahkan secret **`CLICKHOUSE_PASSWORD_ESB`** ke GitHub Environment (48 karakter base64 acak, mis. `openssl rand -base64 36`). **Tanpa ini deploy berikutnya berhenti** dengan `secret CLICKHOUSE_PASSWORD_ESB kosong` (fail-closed, bukan diam-diam).
2. **User ClickHouse `efn_esb`**: dibuat `clickhouse-init` (one-shot). Setelah skema/akses G41 ada di repo: `$DC up -d --force-recreate clickhouse-init` lalu cek log berakhir `... diterapkan` dan exit code 0 (`$DC ps -a clickhouse-init`).
3. **Image**: `$DC build esb-extract`.
4. **Username API per brand** (SEC-180): isi `ESB_USER_<BRAND>` di `infra/compose/.env` (file lokal, di-gitignore; dibuat dari `.env.example` yang nilainya **kosong**). Host staging/prod lokal: `.env` host / config rilis, dicatat di log kustodi. Jangan menulis username di `.env.example`, overlay compose, runbook, tiket, atau commit; `scripts/check-encryption.sh` gagal bila `ESB_USER_*` di file repo berisi nilai. Brand yang ada di `ESB_BRANDS` tanpa username → `esb-extract` gagal start (fail-closed).

### 1a. Prasyarat aktivasi production / prod lokal (`ESB_ENABLED=true` dengan `APP_ENV=production`)

Semua butir wajib terpenuhi dan dicatat (tanggal + penanggung jawab) sebelum kill switch dinyalakan:

| # | Prasyarat | Sumber | Bukti |
|---|---|---|---|
| 1 | Entri inventaris PDP "Datamart ESB" di `docs/compliance/PDP_DATA_INVENTORY.md` (sumber ESB Core per brand, kategori data termasuk nama supplier yang bisa perorangan, lokasi `efn_esb.raw_doc` 36 bulan + `efn_dwh.*esb*`, akses `esb_writer`/`metabase_reader`, retensi) **ditandatangani DPO** | SEC-184 | entri + tanda tangan DPO |
| 2 | **Q-ESB1**: user API read-only per brand tersedia dan menggantikan akun admin | docs/04 §25 | username baru di `.env` host, akun admin tidak dipakai |
| 3 | **Q-ESB7**: izin beban/jadwal backfill ke API produksi ESB dari pemilik akun/ESB | docs/04 §25, L-111 | persetujuan tertulis |
| 4 | **Q-ESB9**: hak EFN atas data ESB brand (dasar pemrosesan) dikonfirmasi | docs/04 §25, SEC-184 | konfirmasi Legal/DPO |
| 5 | Akses Metabase ke mart ESB (`v_esb_*`, `fact/dim_esb_*`) **dibatasi grup Holding/analis pusat**; native query dimatikan untuk grup lain; dashboard ESB tidak dibuka untuk pengguna per PT sampai row policy `company_code` G42 | SEC-182 | tangkapan izin koleksi/database Metabase |
| 6 | Denylist/allowlist redaksi `raw_doc` dicocokkan dengan nama kunci respons nyata | SEC-181 | catatan efn-data-ai-engineer |
| 7 | `ESB_ALLOW_IGNORE_WINDOW=false` di `.env` host | SEC-185 | render `docker compose config` |

## 2. Mengisi secret password per brand

Password ESB adalah kredensial **pihak ketiga** (pola `ninerouter_api_key`): `scripts/secrets.list` menandainya `host:disabled`, jadi tidak pernah di-generate, tidak ada di GitHub Environment, dan `deploy.sh` tidak menimpanya. File yang belum diisi berisi placeholder `disabled`; brand dengan placeholder **tidak boleh** ada di `ESB_BRANDS` (efn_esb menolak login).

**Migrasi file lama (brand PB).** File lama `secrets/esb_core_password.txt` berisi password brand `pb`. Ganti namanya (jangan membuka/menyalin isinya ke tempat lain):

```bash
# dari root repo; -f: menimpa placeholder `disabled` bila gen-secrets.sh sudah sempat membuatnya
mv -f secrets/esb_core_password.txt secrets/esb_core_password_pb.txt
chmod 644 secrets/esb_core_password_pb.txt   # pola gen-secrets.sh: dir 0700, file 0644 (container non-root membaca)
```

**Brand lain (dev)** — tanpa menampilkan password di layar atau riwayat shell:

```bash
b=wtz   # wtz | bacha | imd | cup
IFS= read -rs -p "Password API ESB brand ${b}: " p; echo
printf '%s' "$p" > "secrets/esb_core_password_${b}.txt"; unset p
chmod 644 "secrets/esb_core_password_${b}.txt"
test "$(wc -c < "secrets/esb_core_password_${b}.txt")" -gt 0 && ! grep -qx disabled "secrets/esb_core_password_${b}.txt" && echo OK
```

**Host (staging/prod lokal)**: sama, di `/opt/efn/secrets/esb_core_password_<brand>.txt` sebagai root, izin `0444` di direktori `0750 root:efn-superadmin` (lihat "Mekanisme uid" di [key-custody.md](key-custody.md)). Catat siapa & kapan di log kustodi; nilai tidak pernah dikirim lewat chat/email/tiket.

Larangan: jangan menaruh password di `.env`, argumen perintah (`ps`/riwayat shell), log, tiket, test fixture, atau commit (repo publik; gitleaks memblokir rilis tetapi riwayat git tetap bocor).

Setelah mengubah secret: `$DC up -d --force-recreate esb-extract` (file secret di-bind saat container dibuat).

## 3. Menyalakan

1. `infra/compose/.env`: `ESB_ENABLED=true`, `ESB_BRANDS=pb` (mulai satu brand; tambah setelah §4 lolos untuk brand itu), `ESB_USER_PB=<user API brand PB>` (§1 no. 4). Production/prod lokal: hanya setelah §1a lengkap. Parameter beban: `ESB_RATE_PER_SEC` (default 2.0/brand), `ESB_WORKERS` (4), `ESB_MAX_CALLS_PER_RUN` (20000).
2. `$DC up -d esb-extract` lalu `$DC logs -f --tail=100 esb-extract`. Yang diharapkan: login per brand sukses, run `mode=run` tercatat di `extract_run`. Yang **tidak boleh** muncul di log: password, token `Bearer`/JWT, query string berisi kredensial.
3. Loop `run`: master harian + incremental H-1 + koreksi H-7 tiap `ESB_INTERVAL_SECONDS` (3600); penjualan hari kemarin diambil setelah 03:00 WIB.

## 4. Probe (uji login, tanpa menulis data)

```bash
$DC run --rm --no-deps esb-extract python -m efn_esb probe --brand pb
```

Keluaran yang diharapkan: login OK + **jumlah** cabang (bukan daftar/nama). Gagal → lihat §7. Jalankan untuk setiap brand sebelum dimasukkan ke `ESB_BRANDS`. `--no-deps`: probe tidak butuh ClickHouse; tetap butuh network `egress-esb`.

## 5. Backfill 24 bulan (bertahap, per brand, jendela malam)

Prinsip: API yang ditarik adalah **API produksi** yang juga melayani kasir brand. Backfill hanya berjalan di `ESB_BACKFILL_WINDOW` (default `22:00-05:00` WIB); di luar jendela proses menunggu. `--ignore-window` **hanya** untuk rentang kecil (≤ 1 hari) saat uji, dengan persetujuan pemilik akun ESB brand. Di production (`APP_ENV=production`, termasuk prod lokal) `--ignore-window` ditolak kecuali `ESB_ALLOW_IGNORE_WINDOW=true` **dan** rentang ≤ 1 hari (SEC-185); setelah selesai kembalikan `ESB_ALLOW_IGNORE_WINDOW=false`.

**Satu proses per brand (L-111).** Token bucket berlaku per proses, jadi `run` dan `backfill` untuk brand yang sama bersamaan menggandakan beban ke API produksi. Sebelum backfill brand X: keluarkan X dari `ESB_BRANDS` dan recreate service (`$DC up -d esb-extract`), atau hentikan loop (`$DC stop esb-extract`) bila hanya X yang aktif. Masukkan X lagi setelah backfill selesai/dihentikan. Brand lain boleh tetap di loop `run`.

Urutan yang disarankan per brand (satu brand per malam pada awalnya):

1. `master` (cabang, produk, BoM, menu, supplier) — kecil; **tidak** bisa dipilih lewat `--entity` (hanya entitas harian), ditarik otomatis di awal setiap `backfill`/`run` bila belum ada checkpoint hari ini.
2. `production`, `delivery`, `transfer` — sedang (bukan `flow`: itu nama view, bukan entitas).
3. `sales` — terbesar: `sales-menu-cogs` dibatasi 20 baris/halaman (≈ 450 baris → ≈ 23 halaman per toko per hari).

```bash
# Contoh brand pb, 24 bulan, satu entitas; jalan di background, aman dihentikan (resumable via checkpoint)
$DC run -d --name efn-esb-backfill-pb esb-extract \
  python -m efn_esb backfill --brand pb --from 2024-11-17 --to 2026-11-16 --entity sales
docker logs -f efn-esb-backfill-pb
```

Perkiraan durasi (hitung ulang dengan angka nyata dari `extract_run`): panggilan ≈ jumlah toko × hari × ±23 (sales) + ±3 (resep FIN). Contoh 30 toko × 730 hari × 26 ≈ 570 rb panggilan; pada 2 panggilan/detik ≈ 79 jam ≈ 11–12 malam jendela 7 jam untuk satu brand. Jangan menaikkan `ESB_RATE_PER_SEC` di atas 2 tanpa persetujuan ESB/pemilik akun; percepat dengan menjalankan brand berbeda di malam berbeda, bukan paralel berlebihan.

Jangan menjalankan loop `run` dan `backfill` untuk brand yang sama bersamaan (lihat "Satu proses per brand" di atas). Penulisan tetap idempoten (ReplacingMergeTree + checkpoint), tetapi aturan ini soal beban ke server klien, bukan konsistensi data. Bila 429 muncul (§7), turunkan rate dulu.

### 5a. G65 — histori pembelian & stok awal (PB, docs/04 §27.13)

Prasyarat: `clickhouse-init` sudah dijalankan ulang dengan `23-g65-esb-purchase-stock.sql` (tabel `fact_esb_purchase_line`, `fact_esb_stock_opening`, kolom `dim_esb_supplier.supplier_id`, view `v_esb_g65_quality`).

1. **`purchase_line` (harian, default mati).** Backfill 24 bulan ±1,5–2,2 rb GET (±15–20 menit), satu malam:
   ```bash
   $DC run -d --name efn-esb-backfill-pb-po esb-extract \
     python -m efn_esb backfill --brand pb --from 2024-12-01 --to <kemarin> --entity purchase_line
   ```
   Bila `dim_esb_supplier` belum punya `supplier_id > 0`, proses otomatis menarik `master` sekali dulu. Agar ikut loop harian: tambahkan `purchase_line` ke `ESB_ENTITIES` (opsional). Status PO yang berubah > 7 hari (Receiving → Finished) **wajib** ditangkap ulang dengan `--force` bulanan dan **sekali tepat sebelum export cutover** (L-114).
2. **`stock_opening` (snapshot per cabang, setelah freeze ESB).** `--date` = **tanggal cutover** (bukan cutover − 1), wajib eksplisit; tanpa `--allow-unverified`. ±477 GET ≈ 4–8 menit untuk PB:
   ```bash
   $DC run --rm esb-extract python -m efn_esb snapshot --brand pb --entity stock_opening --date 2026-12-01
   ```
   Exit 1 / `status=partial` = ada cabang gagal (cabang lain tetap tersimpan); jalankan ulang perintah yang sama. Dry-run boleh tanggal uji lebih awal (mis. 2026-11-25).
3. Cek:
   ```sql
   SELECT * FROM efn_dwh.v_esb_g65_quality;   -- purchase_unmapped (map_source name/none), stock_opening_missing_branch
   SELECT map_source, count() FROM efn_dwh.fact_esb_purchase_line FINAL WHERE brand = 'pb' GROUP BY map_source;
   SELECT countIf(supplier_id > 0), count() FROM efn_dwh.dim_esb_supplier FINAL WHERE brand = 'pb';
   ```
   `v_esb_g65_quality` hanya terbaca admin/`efn_mig` (tabel G65 tidak di-grant ke Metabase/Insight, L-258).

### 5b. G68 — laba rugi outlet, waste & pemakaian internal, fill rate (PB, docs/04 §32)

(Semula G67 / §31; dinomori ulang G68 saat merge #165 — "G67" = margin bahan, §6b.) Prasyarat: `clickhouse-init`
dijalankan ulang dengan `25-g68-esb-pl-waste-fillrate.sql` + access terbaru (5 tabel, view `v_esb_*` G68, grant
`esb_writer`/`efn_insight_definer`, policy ESB = 25 termasuk 3 view margin bahan). Semua GET (tanpa tulis ESB, tanpa
`services/prodplan`). Entitas opt-in: default `ESB_ENTITIES` tidak berubah. Satu brand per proses (aturan §5 berlaku);
selama backfill PB berjalan, keluarkan `pb` dari loop `run` atau turunkan `ESB_RATE_PER_SEC` keduanya ke `1.0`.

Rentang 24 bulan PB: `--from 2024-12-01 --to <kemarin WIB>` (`--to` wajib < hari ini). Urutan (§32.1.6):

| Malam | Perintah | GET ≈ | Durasi ≈ |
|---|---|---|---|
| 0 (kapan saja, tanpa ESB) | `retransform --entity delivery_ref` 24 bulan | 0 | menit |
| 1 | `backfill --entity profit_loss` lalu `--entity item_journal` | 126 + 16 rb | ±2,5 jam |
| 1–2 | `backfill --entity transfer_request` (resumable, lanjut malam berikut) | 45 rb | ±6,2 jam |
| 2 | `backfill --entity delivery --entity delivery_ref --force` hanya hari `raw_missing` | kecil | — |

```bash
# Malam 0: isi fact_esb_delivery_ref_line dari raw_doc goods-delivery G41 -- TANPA login/panggilan ESB, boleh di luar
# jendela. Keluaran mencetak hari raw_missing (nomor dokumen dipakai ulang ESB / detail hilang) -> malam 2.
$DC run --rm esb-extract \
  python -m efn_esb retransform --brand pb --entity delivery_ref --from 2024-12-01 --to <kemarin>

# Malam 1 (22:00-05:00 WIB): P&L per cabang x tahun (semua tipe cabang + nonaktif bila
# ESB_BACKFILL_INCLUDE_INACTIVE=true) + total brand, lalu jurnal item (detail per dokumen).
# PENTING (satu proses per brand, §5): tiga perintah `run -d` di bawah JANGAN berjalan bersamaan -- masing-masing punya token
# bucket sendiri sehingga beban ke ESB berlipat. Jalankan SATU per satu; mulai berikutnya setelah `docker ps` tidak lagi
# menampilkan container sebelumnya (log: `backfill brand=pb status=ok`). Alternatif: satu proses dengan beberapa
# `--entity` (mis. `--entity profit_loss --entity item_journal`).
$DC run -d --name efn-esb-backfill-pb-pl esb-extract \
  python -m efn_esb backfill --brand pb --from 2024-12-01 --to <kemarin> --entity profit_loss
$DC run -d --name efn-esb-backfill-pb-ij esb-extract \
  python -m efn_esb backfill --brand pb --from 2024-12-01 --to <kemarin> --entity item_journal

# Malam 1-2: transfer request (±45 rb GET). Berhenti sendiri saat jendela tutup (status=window_closed); jalankan ulang
# perintah yang SAMA malam berikutnya (slice ber-checkpoint dilewati).
$DC run -d --name efn-esb-backfill-pb-gtr esb-extract \
  python -m efn_esb backfill --brand pb --from 2024-12-01 --to <kemarin> --entity transfer_request

# Malam 2: hanya hari raw_missing dari malam 0 (satu perintah per rentang hari).
$DC run --rm esb-extract python -m efn_esb backfill --brand pb --from <hari> --to <hari> \
  --entity delivery --entity delivery_ref --force
```

`delivery_ref` tanpa `delivery` ditolak `backfill` (exit 2): itu turunan tanpa GET sendiri; isi dari raw lewat
`retransform`. `retransform` entitas lain → exit 2 "tidak didukung".

Sebelum mengaktifkan `delivery_ref` harian, ulangi `retransform --entity delivery_ref` untuk rentang malam 0 sampai kemarin
(hari setelah malam 0 tidak punya checkpoint `delivery_ref`; gap-fill harian hanya melihat checkpoint `delivery`, dan hari tanpa
checkpoint dibaca fill rate sebagai "tidak ada kiriman" sehingga rasio terlihat terlalu rendah).

Sesudah backfill, harian: tambahkan `profit_loss,item_journal,transfer_request,delivery_ref` ke `ESB_ENTITIES`
(beban PB ±210 GET/hari). `profit_loss` ditarik sekali/hari setelah `ESB_SALES_READY_HOUR_WIB` untuk tahun yang memuat
`ESB_PL_REFRESH_MONTHS` (default 2) bulan terakhir. Jadwal ulang paksa (jendela malam):

- tiap awal bulan: `backfill --force` 35 hari terakhir untuk `item_journal` dan `transfer_request` (jurnal susulan/void
  > 7 hari);
- hari kerja ke-10 bulan berikut (penutupan buku, Q-ESB33): `backfill --force --entity profit_loss` dengan `--from`
  1 Januari tahun lalu dan `--to` kemarin (±84 GET).

Cek setelah backfill (laporkan ke lead untuk mengisi konstanta status, §32.13):

```sql
SELECT * FROM efn_dwh.v_esb_g68_quality ORDER BY check_name, item;   -- admin/lead saja
SELECT status_id, status_name, count() FROM efn_dwh.fact_esb_transfer_request_line FINAL WHERE brand = 'pb'
GROUP BY status_id, status_name ORDER BY count() DESC;                 -- isi GTR_COUNTED_STATUS (sementara 3, 6)
SELECT status_name, count() FROM efn_dwh.fact_esb_item_journal_line FINAL WHERE brand = 'pb'
GROUP BY status_name ORDER BY count() DESC;                            -- isi ITEM_JOURNAL_COUNTED_STATUS
SELECT entity, first_day, last_day, days FROM efn_dwh.v_esb_coverage WHERE brand = 'pb';
SELECT min(period_month), max(period_month) FROM efn_dwh.v_esb_pl_monthly WHERE brand = 'pb';
```

Uji perilaku view (fixture sintetis, ClickHouse 24.8): `services/esb-extract/tests/clickhouse/g68_views_harness.sh`.

## 6. Memantau

```bash
chq <<'SQL'
-- run terakhir per brand/mode (status, panggilan, baris, error)
SELECT brand, mode, status, started_at, finished_at, calls, rows, errors
FROM efn_esb.extract_run ORDER BY started_at DESC LIMIT 20;
-- kemajuan backfill: hari terakhir yang selesai per brand x entitas
SELECT brand, entity, max(day) AS last_day, countIf(status != 'done') AS not_done, sum(calls) AS calls
FROM efn_esb.extract_checkpoint FINAL GROUP BY brand, entity ORDER BY brand, entity;
-- kualitas data (alias tak terpetakan, cabang aktif tanpa penjualan, umur data terakhir)
SELECT * FROM efn_dwh.v_esb_data_quality LIMIT 50;
SQL
```

Sinyal yang perlu ditindaklanjuti:

| Gejala | Arti | Tindakan |
|---|---|---|
| Tidak ada run `mode=run` baru > 2 × `ESB_INTERVAL_SECONDS` | proses macet/idle | `$DC ps esb-extract`, log, `ESB_ENABLED` |
| `errors` > 0 berulang untuk satu brand | 401/403/429/5xx | §7 |
| `status = 'auth_suspended'` untuk satu brand | login gagal 2 siklus berturut-turut → brand ditangguhkan (tidak mencoba login lagi) sampai proses di-restart | §7 baris 401 |
| `last_day` tidak bergerak selama jendela malam | backfill menunggu jendela / berhenti | cek `docker ps -a`, log container backfill |
| Umur data penjualan > 1 hari setelah 06:00 WIB | incremental H-1 gagal | §7, lalu ulangi `backfill --from <tgl> --to <tgl>` |

Belum ada alert Prometheus untuk ESB (service tanpa endpoint metrik). Usulan G42: metrik kesegaran per brand dari `v_esb_data_quality` → alert `EfnEsbExtractStale` (warning, > 26 jam) — dicatat di laporan devsecops G41.

## 6a. Tabel agregat Insight (`refresh-aggregates`, 22 Nov 2026)

Halaman Insight `v_esb_sales_branch_daily`, `v_esb_sales_daily`, `v_esb_flow_daily` membaca **tabel** `efn_dwh.agg_esb_sales_branch_daily`, `agg_esb_sales_daily`, `agg_esb_flow_daily` (skema `infra/clickhouse/init/20-esb-aggregates.sql`), bukan lagi view yang dihitung ulang setiap query (galat 159/241 setelah data 1 tahun). View `efn_dwh.v_esb_*` asli tetap ada dan menjadi sumber refresh. Row policy `efn_insight_scope` tetap pada view `efn_insight.*` (tidak berubah).

Bauran menu (`efn_insight.v_esb_menu_mix`) membaca `efn_dwh.v_esb_menu_mix_agg` (badan sama dengan `v_esb_menu_mix`, di atas `agg_esb_sales_daily`).

Agg **dan** build permanen `<agg>_build` dibuat `clickhouse-init` (admin; MergeTree, `storage_policy = 'encrypted'`); esb-extract **tidak** punya CREATE/DROP TABLE (review SEC-AG-01). Refresh per bulan kalender: cek kunci → `TRUNCATE` build → `INSERT` bulan itu dari view → pagar penyusutan → cek kunci → `ALTER TABLE agg REPLACE PARTITION ID '<yyyymm>' FROM build` → heartbeat kunci; lalu `DROP PARTITION` untuk bulan agg yang tidak ada lagi di sumber. **Atomik per bulan, tidak atomik lintas bulan**: selama refresh (±12 dtk untuk 1 tahun PB) sebagian bulan sudah baru dan sebagian masih lama; bila refresh gagal di tengah, bulan yang sudah diganti tetap baru dan sisanya tetap isi lama.

**Pagar penyusutan (SEC-AG-03).** Bulan yang hasil barunya 0 baris padahal agg berisi, turun > 50%, atau bulan agg yang tidak ada lagi di rentang sumber **dilewati**; agregat berstatus `error` dengan `error = shrink <yyyymm,...>` (Insight tetap membaca isi lama bulan itu). Penyebab umum: checkpoint/fakta belum lengkap setelah restore DR, `CH_RAW_DATABASE` salah, data sumber benar-benar dihapus. Bila penyusutan memang disengaja (data sumber dikoreksi/dihapus): `... refresh-aggregates --allow-shrink` (manual saja; loop `run`/`backfill` tidak pernah memakai flag ini).

**Kunci (SEC-AG-02).** `efn_esb.aggregate_refresh_lock`: satu refresh pada satu waktu. Pemegang = run yang belum melepas kunci dan heartbeat terakhirnya < 15 menit (heartbeat tiap bulan selesai). Proses mati → kunci basi sendiri setelah 15 menit lalu boleh diambil alih. Pemegang dicek ulang sebelum setiap TRUNCATE/REPLACE/DROP PARTITION; kehilangan kunci → berhenti tanpa menulis (`error = lock_lost`).

| Kapan | Siapa |
|---|---|
| Akhir setiap siklus `run` yang menulis slice `sales`/`fin_sold`/`production`/`delivery`/`transfer`/`master` | otomatis (gagal = log `ERROR aggregate=... status=error`, dicoba lagi siklus berikutnya; ekstraksi tidak terpengaruh) |
| Akhir `backfill` yang menulis slice | otomatis (exit code backfill tidak berubah; baris `refresh-aggregates status=gagal` → jalankan manual) |
| **Setelah deploy pertama** skema ini (tabel awalnya KOSONG → halaman Insight kosong) | **manual, wajib** |
| Setelah menyalin data ESB dari DR / mesin lain (`INSERT ... SELECT` / restore ke tabel fakta) | **manual, wajib** |
| Setelah `clickhouse-init` membuat ulang agg/build (log `tabel agregat Insight dibuat ulang`: kolom view sumber berubah) | **manual, wajib** |
| Setelah pemulihan tabel agg (lihat di bawah) | **manual, wajib** |

```bash
# Manual (hanya ClickHouse, tidak memanggil ESB; jalan juga dengan ESB_ENABLED=false). Exit 0 = semua ok.
$DC run --rm --no-deps esb-extract python -m efn_esb refresh-aggregates
# Penyusutan disengaja (data sumber dikoreksi/dihapus) -- lihat pagar penyusutan di atas
$DC run --rm --no-deps esb-extract python -m efn_esb refresh-aggregates --allow-shrink
```

Log per agregat: `aggregate=<nama> status=ok|busy|error rows=<n> months=<n> seconds=<n>` (tanpa isi data). `busy` = refresh lain memegang kunci (mis. loop `run`); tunggu (maks 15 menit bila proses itu mati) lalu ulangi. `error=shrink ...` → pagar penyusutan; `error=lock_lost` → proses lain mengambil alih kunci; `error=<Kelas> code=<n>` → galat ClickHouse (mis. 159 timeout, 241 memori, 497 hak akses).

**Pemulihan bila tabel agg hilang / kosong / rusak (SEC-AG-05).** Gejala: halaman Insight galat `UNKNOWN_TABLE` (60) atau kosong, `refresh-aggregates` gagal `code=60`, atau `clickhouse-init` gagal dengan `tabel agregat Insight hilang / bukan MergeTree+encrypted`.

1. Bila init gagal karena engine/policy: hapus tabel yang disebut (admin): `chq <<< "DROP TABLE IF EXISTS efn_dwh.<tabel> SYNC"`; hak tulis esb_writer pada tabel itu sudah dicabut otomatis dan dipasang ulang oleh init berikutnya.
2. `$DC up -d --force-recreate clickhouse-init` → log berakhir `... diterapkan`, exit 0 (membuat ulang agg/build kosong, MergeTree + encrypted).
3. `$DC run --rm --no-deps esb-extract python -m efn_esb refresh-aggregates` → exit 0. (Agg kosong bukan penyusutan; `--allow-shrink` tidak perlu.)
4. Verifikasi di bawah + `SELECT name, engine, storage_policy FROM system.tables WHERE database = 'efn_dwh' AND name LIKE 'agg_esb_%'` (6 baris, `MergeTree`, `encrypted`).

**Kesegaran** (kapan terakhir dibangun):

```bash
chq <<'SQL'
SELECT name, max(finished_at) AS last_ok, argMax(rows, finished_at) AS rows
FROM efn_esb.aggregate_refresh WHERE status = 'ok' GROUP BY name ORDER BY name;
SELECT name, status, finished_at, error FROM efn_esb.aggregate_refresh ORDER BY finished_at DESC LIMIT 10;
SQL
```

**Verifikasi setelah refresh** (angka per bulan view sumber = tabel agregat; jalankan sebagai admin, berat untuk view → satu bulan per baris lewat `WHERE`, mulai dari bulan terbaru):

```bash
chq <<'SQL'
-- 1) Jumlah agregat per bulan (cepat)
SELECT toStartOfMonth(sales_date) AS m, sum(net) AS net, sum(trx_count) AS trx, count() AS rows
FROM efn_dwh.agg_esb_sales_branch_daily GROUP BY m ORDER BY m;
-- 2) Bandingkan satu bulan dengan view sumber (ulangi per bulan; selisih harus 0)
SELECT 'branch' AS t,
  (SELECT sum(net) FROM efn_dwh.v_esb_sales_branch_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31')
- (SELECT sum(net) FROM efn_dwh.agg_esb_sales_branch_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31') AS d_net,
  (SELECT sum(trx_count) FROM efn_dwh.v_esb_sales_branch_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31')
- (SELECT sum(trx_count) FROM efn_dwh.agg_esb_sales_branch_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31') AS d_trx;
SELECT 'sales' AS t,
  (SELECT sum(net) FROM efn_dwh.v_esb_sales_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31')
- (SELECT sum(net) FROM efn_dwh.agg_esb_sales_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31') AS d_net,
  (SELECT count() FROM efn_dwh.v_esb_sales_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31')
- (SELECT count() FROM efn_dwh.agg_esb_sales_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31') AS d_rows;
SELECT 'flow' AS t,
  (SELECT sum(sold) + sum(ck_prod) + sum(ck_out) FROM efn_dwh.v_esb_flow_daily WHERE day BETWEEN '2026-11-01' AND '2026-11-30')
- (SELECT sum(sold) + sum(ck_prod) + sum(ck_out) FROM efn_dwh.agg_esb_flow_daily WHERE day BETWEEN '2026-11-01' AND '2026-11-30') AS d_qty;
-- 3) Filter tanggal terdorong ke fakta (partisi): "Parts" terpilih harus jauh lebih kecil dari total
EXPLAIN indexes = 1 SELECT count() FROM efn_dwh.v_esb_sales_daily WHERE sales_date BETWEEN '2026-10-01' AND '2026-10-31';
SQL
```

Selisih kecil boleh terjadi bila ekstraksi menulis slice baru di antara refresh dan query verifikasi (agregat = potret saat refresh; refresh berikutnya menyusul).

**Perubahan skema/sumber.** Kolom view sumber berubah (CREATE OR REPLACE VIEW di init) → `clickhouse-init` membandingkan kolom (urutan, nama, tipe) agg/build dengan view, menghapus keduanya dan membuat ulang kosong dengan kolom baru (admin, bukan esb-extract) → jalankan `refresh-aggregates`. Wrapper `efn_insight.*` di `insight-access.sql` hanya ikut berubah bila kolom keluarannya berubah. Mengubah ORDER BY/partisi: ubah `efn_esb/aggregates.py` `table_definition` **dan** `20-esb-aggregates.sql` bersamaan (diuji sama persis); tabel lama tidak berubah oleh CREATE IF NOT EXISTS → hapus agg + build manual (admin) sebelum `clickhouse-init`, lalu refresh.

## 6b. Margin bahan (`refresh-margin`, G67, docs/04 §31)

Halaman Insight `/margin-bahan` (persona `exec`) membaca `efn_insight.v_esb_ing_price_monthly`, `v_esb_menu_cost_monthly`, `v_esb_ing_group_monthly` (DEFINER, row policy kelas PT) di atas view `efn_dwh.v_esb_*` = **versi refresh terakhir per brand** dari tabel `efn_dwh.mart_esb_*` (`infra/clickhouse/init/24-margin-bahan.sql`; MergeTree, `storage_policy = 'encrypted'`, TTL 60 hari). Penulis tunggal: esb-extract (`efn_esb/margin.py`), role esb_writer `SELECT, INSERT` saja. Algoritme: README esb-extract + docstring `margin.py` (§31.2 langkah 1–6). Harga beli & biaya resep = data komersial sensitif (L-260): tidak di-grant ke Metabase/text2sql/forecast/mig/segment.

**Env** (overlay `docker-compose.esb.yml`, nilai di `.env`):

| Env | Default | Catatan |
|---|---|---|
| `ESB_MARGIN_BRANDS` | kosong = **fitur mati** | efn-prod: `pb` (L-259). Brand wajib ada di `ESB_BRANDS`; `ESB_ENTITIES` wajib memuat `purchase_line` (harga PO harian, §5a) — jika tidak, `run` gagal start dengan pesan jelas |
| `ESB_MARGIN_MONTHS` | `13` | jendela = N bulan kalender **penuh** terakhir (2..24); PO dimuat 3 bulan lebih awal untuk rata-rata bergulir |
| `ESB_MARGIN_WEEKDAY` / `ESB_MARGIN_HOUR_WIB` | `0` (Senin) / `2` | jadwal mingguan (L-261) |

**Jadwal (otomatis, loop `run`).** Di akhir setiap siklus, untuk setiap brand margin: bila hari WIB = `ESB_MARGIN_WEEKDAY`, jam WIB ≥ `ESB_MARGIN_HOUR_WIB`, dan refresh `ok` terakhir > 6 hari → (1) `snapshot --entity bom_detail` brand itu (±1 GET per BOM aktif; PB ±1.700 GET ≈ 15 menit pada 2 req/dtk, anggaran `ESB_BACKFILL_MAX_CALLS`), (2) hitung, (3) INSERT versi baru. Paling banyak satu percobaan per brand per hari WIB (gagal → minggu depan atau manual). Snapshot tidak `ok` → catatan `error=snapshot_<status>` dan **tidak** dihitung (resep basi tidak dipakai diam-diam). Brand `auth_suspended` dilewati. Apa pun yang gagal hanya dicatat; ekstraksi harian tidak terpengaruh.

**Refresh manual** (hanya ClickHouse, tidak memanggil ESB, jalan juga dengan `ESB_ENABLED=false`; memakai resep snapshot terakhir yang ada):

```bash
# Refresh pertama setelah deploy skema 24 (tabel awalnya KOSONG -> halaman kosong). Resep dulu bila belum pernah:
$DC run --rm esb-extract python -m efn_esb snapshot --brand pb --entity bom_detail
$DC run --rm --no-deps esb-extract python -m efn_esb refresh-margin --brand pb     # satu brand
$DC run --rm --no-deps esb-extract python -m efn_esb refresh-margin                # semua ESB_MARGIN_BRANDS
```

Exit 0 = semua `ok`; 1 = ada `error`/`busy`; 2 = env tidak sah. Log: `margin=margin_<brand> status=ok|busy|error rows=<n> months=<n> seconds=<n>` (tanpa isi data). Arti `error`:

| `error` | Penyebab | Tindakan |
|---|---|---|
| `no_company` | brand tanpa cabang aktif ber-PT di `dim_esb_branch` | cek entitas `branch` / `ESB_BRANDS` |
| `no_bom` | belum ada snapshot `bom_detail` brand itu | `snapshot --brand <b> --entity bom_detail`, lalu ulangi |
| `no_purchase` | tidak ada baris PO ber-checkpoint di jendela | aktifkan `purchase_line` + backfill (§5a) |
| `no_sales` | tidak ada penjualan menu di jendela | cek `agg_esb_sales_daily` / `refresh-aggregates` (§6a) |
| `snapshot_<status>` | snapshot mingguan gagal (`partial`, `auth_failed`, `limit`, `error`) | lihat §7; lalu snapshot + `refresh-margin` manual |
| `busy` | refresh lain (margin / `refresh-aggregates`) memegang kunci | tunggu (maks 15 menit bila proses itu mati), ulangi |
| `lock_lost` / `<Kelas> code=<n>` | kunci diambil alih / galat ClickHouse (159 timeout, 241 memori, 497 hak) | ulangi; 497 → cek grant `esb-access.sql` |

**Kesegaran & verifikasi** (admin):

```bash
chq <<'SQL'
SELECT name, status, finished_at, rows, error FROM efn_esb.aggregate_refresh
WHERE startsWith(name, 'margin_') ORDER BY finished_at DESC LIMIT 10;
-- Versi yang tampil di Insight per brand + jumlah baris per tabel
SELECT brand, max(_version) AS v, count() FROM efn_dwh.mart_esb_menu_cost_monthly GROUP BY brand;
SELECT 'price', count() FROM efn_dwh.v_esb_ing_price_monthly UNION ALL
SELECT 'menu', count() FROM efn_dwh.v_esb_menu_cost_monthly UNION ALL
SELECT 'group', count() FROM efn_dwh.v_esb_ing_group_monthly;
-- Cakupan: porsi COGS ESB dari menu ber-BOM per bulan (target tinggi; rendah = nama BOM Menu tidak cocok menu jual)
SELECT month, sumIf(cogs, bom_matched = 1) / sum(cogs) AS cogs_cov, avgIf(leaf_coverage, bom_matched = 1) AS leaf_cov
FROM efn_dwh.v_esb_menu_cost_monthly WHERE brand = 'pb' GROUP BY month ORDER BY month;
-- Menu terlaris tanpa BOM (cek normalisasi nama ' T' / 'KIOSK - ')
SELECT menu_name, sum(qty) AS q FROM efn_dwh.v_esb_menu_cost_monthly
WHERE brand = 'pb' AND bom_matched = 0 GROUP BY menu_name ORDER BY q DESC LIMIT 20;
SQL
```

**Pemulihan.**
- Tabel `mart_esb_*` hilang / bukan MergeTree+encrypted → `clickhouse-init` GAGAL (`tabel agregat Insight hilang / bukan MergeTree+encrypted`, hak esb_writer pada tabel itu dicabut): admin `DROP TABLE IF EXISTS efn_dwh.<tabel> SYNC` → `$DC up -d --force-recreate clickhouse-init` → `refresh-margin`.
- Angka salah setelah koreksi data sumber (PO/BOM dibetulkan di ESB): backfill/snapshot ulang sumbernya, lalu `refresh-margin` (versi baru menggantikan tampilan; versi lama terbuang sendiri setelah 60 hari). Tidak ada `ALTER DELETE`/`TRUNCATE` dari esb-extract; penghapusan versi tertentu (mis. versi salah yang terlanjur tampil dan tidak bisa segera diganti) = admin: `ALTER TABLE efn_dwh.mart_esb_<t> DELETE WHERE brand = 'pb' AND _version = '<v>'` pada ketiga tabel.
- Refresh gagal di tengah INSERT (jarang; satu INSERT per tabel): versi terakhir ketiga tabel bisa berbeda sampai refresh `ok` berikutnya — jalankan `refresh-margin` manual.
- Tidak ada refresh `ok` > 60 hari → TTL membuang semua versi, halaman kosong (sengaja, bukan data basi): jalankan `refresh-margin`.

## 7. ESB menolak permintaan

| Respons | Perilaku klien (kontrak §25) | Tindakan operator |
|---|---|---|
| **429** Too Many Requests | backoff eksponensial, maks 5 percobaan, lalu entitas/hari itu ditandai gagal (checkpoint tidak maju → diulang run berikutnya) | Turunkan `ESB_RATE_PER_SEC` (mis. 1.0) dan/atau `ESB_WORKERS` (2) di `.env`, recreate. Pastikan tidak ada `run` + `backfill` brand yang sama bersamaan (§5) dan backfill hanya di jendela malam. Bila tetap 429 di jam sepi: hubungi ESB/pemilik akun untuk kuota; jangan mengakali dengan user lain. |
| **401** Unauthorized | login ulang **satu kali**; masih 401 → brand berhenti untuk run itu (`auth_failed`). Setelah **2 siklus berturut-turut** gagal login, brand **ditangguhkan** (`extract_run.status = 'auth_suspended'`, SEC-186): proses tidak mencoba login brand itu lagi sampai di-restart; brand lain tetap jalan | Kemungkinan password/username salah, password diganti/kedaluwarsa, atau akun terkunci. Konfirmasi ke pemilik akun brand, perbaiki secret password (§2/§8) dan/atau `ESB_USER_<BRAND>` di `.env`, jalankan `probe` (§4) **sekali**, lalu restart service: `$DC up -d --force-recreate esb-extract` (restart melepas penangguhan). Bila belum bisa diperbaiki: keluarkan brand dari `ESB_BRANDS` agar restart berikutnya tidak memicu login gagal lagi (hindari penguncian akun). |
| **403** Forbidden | slice (brand × entitas × hari/cabang) itu dicatat `partial` **tanpa memajukan checkpoint**, jadi dicoba ulang setiap siklus `run` berikutnya | User API tidak punya akses cabang/menu tersebut. Selama 403 belum diperbaiki, error akan muncul tiap siklus. Minta pemilik akun menambah hak **baca** saja untuk user teknis (Q-ESB1); bila cabang memang tidak relevan, keluarkan entitasnya lewat `ESB_ENTITIES` atau laporkan ke efn-data-ai-engineer. Jangan beralih ke akun lain untuk mengakali. |
| **400** | dicatat sebagai error parameter (mis. `limit` > 20 di `sales-menu-cogs-detail`) | Bug klien → laporkan ke efn-data-ai-engineer; jangan diulang paksa. |
| **5xx** / timeout | backoff seperti 429 | Bila meluas: `ESB_ENABLED=false` sementara; ulangi rentang yang terlewat dengan backfill setelah pulih. |
| TLS gagal (sertifikat) | ditolak, tidak ada fallback tanpa verifikasi | Cek jam host dan CA publik image (certifi). Jangan pernah mematikan verifikasi. |

## 8. Rotasi & perubahan kredensial

- **Password ESB brand** (diganti di sisi ESB oleh pemilik akun, idealnya tiap 90 hari atau saat ada personel keluar): tulis file baru (§2) → `$DC up -d --force-recreate esb-extract` → `probe`. Token JWT lama tidak disimpan (hanya di memori proses), jadi recreate cukup.
- **`clickhouse_password_esb`**: dev = hapus file lalu `bash scripts/gen-secrets.sh`; staging/prod = ganti `CLICKHOUSE_PASSWORD_ESB` di GitHub Environment lalu deploy. Setelah file berubah: `$DC up -d --force-recreate clickhouse-init` (ALTER USER) → `$DC up -d --force-recreate esb-extract`. Urutan sebaliknya membuat esb-extract gagal login sementara (tidak merusak data).
- Kebocoran dicurigai: keluarkan brand dari `ESB_BRANDS`, minta pemilik akun mengganti password, ikuti [security-incident.md](security-incident.md).

**Belum ada di rantai rilis (tindak lanjut):** image `esb-extract` sudah di-build/scan/ditandatangani `build-push.yml`, tetapi `deploy.yml`/`efn-deploy`/`deploy.sh` belum membawa `IMAGE_ESB_EXTRACT`, variabel `ESB_*` (termasuk `ESB_USER_*`, sebagai GitHub Environment *variable* non-rahasia, bukan di repo) belum ada di `EFN_RELEASE_CONFIG_VARS`, dan `deploy.sh` belum menyertakan `docker-compose.esb.yml`. Sampai itu dibangun, jalankan overlay secara manual di lingkungan lokal (K25-06), bukan lewat `efn-deploy`.

## 9. Menghentikan

| Tingkat | Langkah |
|---|---|
| Jeda (data tetap) | `.env` `ESB_ENABLED=false` → `$DC up -d esb-extract` (idle). Backfill: `docker stop efn-esb-backfill-<brand>` (lanjut dari checkpoint saat dijalankan lagi). |
| Satu brand | keluarkan dari `ESB_BRANDS`, recreate. |
| Darurat (putus jalur keluar) | `docker network disconnect <project>_egress-esb <container esb-extract>` (mis. `efn-prod_egress-esb`; cek dengan `docker network ls`); lalu `$DC stop esb-extract`. Network ini hanya dipakai `esb-extract`, jadi service lain tidak terdampak. |
| Hapus total | `$DC rm -sf esb-extract`; data ClickHouse tetap (hapus mart/raw hanya dengan keputusan pemilik data; `raw_doc` habis sendiri oleh TTL 36 bulan). |

**Sisa risiko jaringan:** network `egress-esb` hanya berisi `esb-extract` dengan `enable_icc=false` (SEC-183), jadi tidak ada jalur L3 langsung ke BFF atau service lain di `egress` bersama. Namun Docker tetap tidak membatasi **tujuan internet**: bila proses `esb-extract` disusupi, ia bisa menghubungi host internet lain. Mitigasi saat ini = allowlist host di klien (`ESB_BASE_URL` tetap, https saja, tanpa redirect lintas host, tanpa proxy env), container non-root/read-only tanpa capability, kredensial ClickHouse minimum, dan tidak berada di network `data`. Allowlist di tingkat jaringan (nftables subnet `172.30.8.0/24` → IP `services.esb.co.id`:443, atau egress proxy) belum ada untuk service mana pun (sama dengan 9router/WhatsApp); subnet khusus ini membuat aturan per service tersebut mudah ditambahkan nanti.
