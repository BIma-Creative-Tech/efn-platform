# Runbook: seed data simulasi Supermarket (G48)

Skrip: `scripts/dev/seed_demo_supermarket.py` (dijalankan lewat `odoo shell`, pola `scripts/dev/seed_demo_g28.py`).
Tujuan: DB demo PT Supermarket Demo (`SMD`) yang berisi katalog, stok lot, markdown, kalender, dan histori penjualan
24 bulan untuk mendemokan forecast, near-expiry, dan restriksi DC. **Hanya untuk dev/staging lokal.** Semua data
fiktif: tidak ada merek, nama orang, atau nomor HP nyata.

**Keputusan pemilik proyek (20 Nov 2026):** data dan progres supermarket SMD tidak bergantung pada modul atau data
migrasi brand lama. Histori penjualan berupa **order POS sintetis** (`pos.order`) yang dibaca ETL dengan jalur yang
sama seperti penjualan POS sungguhan.

Terkait: ADR-045 (DC satu PT + assortment), ADR-046 (forecast), docs/04 §28.3/§28.4/§28.5/§28.5a,
`docs/fit-gap/G45_SUPERMARKET.md`, `services/odoo/addons/efn_setup_supermarket/README.md`. Semua ADR berstatus
Proposed.

## 1. Prasyarat

- Stack dev berjalan (`docs/runbooks/dev-stack-boot.md`), container Odoo 19 (contoh di bawah: `efn-odoo-1`,
  konfigurasi `/etc/odoo/odoo.conf`). `$DC` = perintah compose dev seperti di runbook boot.
- DB **terpisah** `efn_smd_demo` (cocok `dbfilter ^efn_.*$`), dibuat **dengan demo data**, dengan modul terpasang:
  `efn_setup_supermarket`, `efn_expiry_monitor`, `point_of_sale` (beserta dependensinya). Skrip berhenti dengan
  `SEED ABORT ...` (exit 2) bila modul atau data demo (`efn_setup_supermarket.warehouse_smd01`) tidak ada.
- Jangan jalankan di DB dev bersama atau DB production: skrip membuat ribuan order POS histori.

### Membuat DB demo (sekali)

Hentikan `odoo` dulu agar tidak ada dua proses yang menulis registry:

```bash
$DC --profile core stop odoo odoo-tls odoo-ws-tls
# Odoo 19: demo data tidak dimuat secara default -> --with-demo saat inisialisasi base.
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_smd_demo -i base --with-demo --stop-after-init
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_smd_demo --stop-after-init \
  -i efn_setup_supermarket,efn_expiry_monitor,point_of_sale
$DC --profile core up -d odoo odoo-tls odoo-ws-tls
```

Bila user DB Odoo tidak punya hak `CREATEDB`, buat DB dulu sebagai owner (`createdb -O efn_odoo efn_smd_demo` lewat
container postgres), lalu jalankan perintah di atas.

## 2. Menjalankan seed

Default (400 SKU, histori 730 hari sampai kemarin, satu order ringkasan per outlet per hari):

```bash
docker exec -i efn-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http \
  < scripts/dev/seed_demo_supermarket.py
```

Mode kecil untuk smoke test:

```bash
docker exec -i -e SMD_SEED_SKUS=40 -e SMD_SEED_DAYS=120 efn-odoo-1 \
  odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http < scripts/dev/seed_demo_supermarket.py
```

Git Bash: tidak perlu `MSYS_NO_PATHCONV` (tanpa mount). Output: satu baris `SEED ...` per tahap (histori: satu baris
per outlet × bulan), ringkasan hitungan, lalu `SEED_DONE`. Tidak ada `SEED_DONE` = gagal (lihat traceback); tahap dan
batch histori yang sudah selesai sudah di-commit.

### Parameter (env var, `docker exec -e`)

| Variabel | Default | Rentang | Arti |
|---|---|---|---|
| `SMD_SEED_SKUS` | 400 | 20–2000 | jumlah SKU placeholder (dibagi proporsional ke 14 kelompok; Alkohol ≥ 2, Impor ≥ 3) |
| `SMD_SEED_DAYS` | 730 | 28–1100 | panjang histori penjualan |
| `SMD_SEED_HISTORY_END` | hari ini (WIB) | `YYYY-MM-DD`, ≤ hari ini | batas eksklusif histori: `[END − DAYS, END − 1]` |
| `SMD_SEED_ORDERS_PER_DAY` | 1 | 1–50 | order ringkasan per outlet per hari (SKU hari itu dibagi rata antar-order) |
| `SMD_SEED_HISTORY_SKUS` | 0 | 0–2000 | 0 = semua SKU; N = histori hanya untuk N SKU terlaris (mempercepat run) |
| `SMD_SEED_STOCK_SKUS` | 150 | 0–2000 | SKU terlaris yang diberi stok di outlet (DC selalu semua SKU non-DSD) |
| `SMD_SEED_LOTS_MAX` | 3 | 1–5 | lot maksimum per SKU × outlet |
| `SMD_SEED_EXPIRED_LOTS` | 8 | 0–100 | lot sudah kedaluwarsa di outlet (skenario blokir POS) |
| `SMD_SEED_MARKDOWNS` | 12 | 0–200 | contoh `efn.lot.markdown` (genap aktif, ganjil draft) |
| `SMD_SEED_MEMBERS` | 50 | 0–999 | member fiktif |
| `SMD_SEED_SHELF_SKUS` | 30 | 0–2000 | SKU per outlet yang diberi kapasitas rak (`shelf_capacity_qty`) |
| `SMD_SEED_HISTORY` | 1 | 0/1 | 0 = lewati histori (tahap terlama) |
| `SMD_SEED_POS` | 1 | 0/1 | 0 = jangan buat `pos.config` per outlet (histori butuh `pos.config` demo) |

### Idempoten & deterministik

- Run kedua dengan parameter sama tidak membuat duplikat: produk dikenali dari `default_code` `SMD-<KODE>-<NNNN>`,
  supplier `ref` `SMD-SUP-NN`, member `SMD-MBR-NNN`, baris assortment/markdown/season dari catatan
  `[DEMO SUPERMARKET]`, lot dari nama `<gudang>-<NNNN>-<k>`, kalender dari kunci `(company, date, kind, name)`,
  sesi histori dari nama `[DEMO SUPERMARKET] histori SMD0N`, order histori dari `pos_reference`
  `DEMO-SMD0N-YYYYMMDD-n` (hari × outlet yang sudah ada dilewati).
- Semua angka acak dari `random.Random` berbenih tetap (4848 + kode SKU/outlet).
- **Tanggal relatif hari menjalankan:** umur lot dihitung dari hari ini. Menjalankan ulang di hari lain tidak membuat
  lot baru (nama lot tetap) dan tidak memperbarui tanggal kedaluwarsa lot lama. Tanpa `SMD_SEED_HISTORY_END`, run di
  hari lain menambah order untuk hari-hari baru; order lama tidak dihapus. Untuk demo "segar", buat ulang DB.

## 3. Isi data (default)

| Tahap | Isi | Default |
|---|---|---|
| 1 Supplier | 15 `res.partner` perusahaan fiktif (tanpa kontak), 2 di antaranya DSD | 15 |
| 2 Produk | SKU placeholder di 10 kategori standar (+5 sub-kategori Fresh dari `efn_setup_supermarket`). Semua `is_storable`, `tracking=lot`, `use_expiration_date`, `expiration_time` / `use_time` / `removal_time` / `alert_time` per kelompok (`alert_time` = ambang near-expiry kategori). Barcode EAN-13 fiktif `299…` dengan check digit benar. Produk timbang (UoM kg, `to_weight`). Capability: Frozen → freezer; Dairy, Daging, Ikan → chiller; Alkohol → izin alkohol; Impor: per produk (keju chiller, wagyu/salmon freezer, sosis/bacon babi chiller + non-halal) | 400 (55 kg) |
| 3 Supplierinfo | 1 supplier utama per SKU (`delay`, `min_qty`, harga = biaya) + ±20% SKU kering/minuman/snack punya supplier kedua | 444 |
| 4 Assortment | DSD: kategori Bakery (per outlet) + galon/es batu (per produk). Kapasitas rak untuk 30 SKU terlaris per outlet. Jadwal supplier: 13 ke DC, 2 DSD × 5 outlet | ±170 baris, 23 jadwal |
| 5 Kalender | Ramadan & 1 Syawal 2023–2027, libur nasional & cuti bersama 2024–2026 (`source=manual`, nama bertanda `DEMO, konfirmasi SKB/isbat`). 3 `efn.forecast.season` v1 (Ramadan, pra-Lebaran, hari Lebaran) khusus SMD | ±220 entri |
| 6 Stok | Inventory adjustment (`stock.quant` + `action_apply_inventory`). DC: 2 lot aman per SKU non-DSD. Outlet: 150 SKU terlaris, 1–3 lot, sebagian near-expiry / dalam ambang markdown, + 8 lot kedaluwarsa. Lot outlet di zona sesuai capability/kategori | 2.211 lot (aman 2.024, markdown 121, near 58, kedaluwarsa 8) |
| 7 Markdown | `efn.lot.markdown` pada lot near-expiry/markdown di outlet; 6 aktif (sebagai user demo Markdown Manager, fallback sudo), 6 draft; `notify_members=False` | 12 |
| 8 Member | "Member Fiktif NNN (DEMO)", HP `+62 800-0000-NNNN` (format valid, bukan nomor seluler) | 50 |
| 9 POS | `pos.config` "Kasir SMD0N (DEMO)" memakai picking type POS outlet; per outlet satu jurnal kas `SMC<n>` + metode "Tunai SMD0N (DEMO)" (CE: satu metode tunai hanya untuk satu POS) | 5 |
| 10 Persona | Verifikasi (tanpa perubahan) user demo `res_users_demo.xml`: kepala toko ↔ outlet, grup, company. Password tidak di-set | — |
| 11 Histori | Per outlet satu `pos.session` **`closed`** "[DEMO SUPERMARKET] histori SMD0N" (start/stop = rentang histori) berisi `pos.order` `state=paid`, satu order per outlet per hari (`date_order` 12:00 WIB, disimpan UTC), baris = SKU yang terjual hari itu (nama baris = nama produk). `amount_total`/`amount_tax`/`amount_paid` konsisten dengan baris, `amount_return=0`, pajak per produk dihitung sekali (`taxes_id.compute_all` harga satuan × qty), `total_cost` = qty × biaya | ±3.650 order, ±533 ribu baris (perkiraan dari generator yang sama dengan run sebelumnya) |

Pola histori: basis Pareto per SKU (sebagian intermittent), skala outlet (SMD01 1,3 · SMD02 1,0 · SMD03 0,6 ·
SMD04 1,15 · SMD05 0,8), weekday/weekend, minggu gajian (7 hari terakhir + 7 hari pertama), Ramadan per kategori
(kurma/sirup ×2–3), H-14..H-8 ×1,25, H-7..H-1 ×1,5–2,3 per kategori (nastar/kastengel ×4), hari Lebaran ×0,35,
H+2..H+6 ×0,75, libur nasional, Natal/Tahun Baru (alkohol, bakery), tren +8%/tahun, noise lognormal. Produk hanya
dijual di outlet yang lolos assortment (DSD tetap dijual).

**Order histori dibuat lewat `pos.order.create` (sudo), bukan jalur sinkronisasi kasir.** Akibatnya:

- tidak ada stock move (stok awal tetap dari tahap 6), tidak ada `pos.payment`, tidak ada jurnal/akuntansi penutupan
  sesi (sesi langsung `closed` tanpa `_validate_session`);
- tidak memicu hook efn POS: loyalty/Poin EFN, OTP, KDS, validasi lot/expiry `efn_expiry_monitor`, validasi refund.
  Semua hook itu ada di `_process_order` / `_process_saved_order`;
- laporan akuntansi POS/kas tidak mencerminkan histori ini. Laporan penjualan POS (Point of Sale → Reporting) dan
  ETL `fact_sales_line` membacanya.

Durasi terukur 20 Nov 2026 (container lokal, DB baru, default): run pertama **±14 menit** (stok 59 s, histori
757 s) → 3.650 order, 533.082 baris, DB ±343 MB; run kedua ±25 s (0 record baru, 3.650 order dilewati). Mode kecil
(`SMD_SEED_SKUS=40 SMD_SEED_DAYS=60`): ±45 s. Bila perlu lebih cepat, batasi dengan `SMD_SEED_HISTORY_SKUS` (mis.
150) atau `SMD_SEED_DAYS`.

Cek pola (total unit/hari 5 outlet, run 20 Nov 2026): baseline Feb 2026 ±1.475; Ramadan ±1.887; H-7..H-1 Lebaran
±2.564; hari Lebaran ±565; minggu gajian ±1.715 vs non-gajian ±1.528.

## 4. Mengaktifkan forecast di atas data ini

Forecast demo = **v2** (ETL → ClickHouse → forecast-worker).

1. **ETL** (`efn-data`): ETL membaca DB Odoo dari `ODOO_DB` (default `efn_odoo`). Untuk DB demo set
   `ODOO_DB=efn_smd_demo` dan terapkan grant read-only ke DB itu:
   `MSYS_NO_PATHCONV=1 docker exec -i efn-postgres-1 psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_smd_demo -f - < services/etl/sql/odoo-readonly-grants.sql`.
   Nyalakan `ETL_ENABLE_ODOO=true` dan `ETL_ENABLE_FORECAST=true` di `infra/compose/.env`, lalu recreate ETL. Order
   histori masuk `fact_sales_line` (`_SQL_SALES_FACT`: `pos_order.state IN ('paid','done')`) → `v_fc_sales_daily`
   (`store_id` = `pos.config` → `dim_outlet.warehouse_id`). Backfill pertama membaca ±533 ribu baris (cursor
   `write_date`).
2. **forecast-worker**: ikuti `docs/runbooks/forecast-worker.md` §1 dan §3 (`FORECAST_ENABLED=true`, overlay
   `docker-compose.forecast.yml`, user teknis + API key `efn_forecast_smd` **di DB demo**). Run manual:
   `docker compose ... exec forecast-worker python -m efn_forecast run --company SMD --once`.
3. **Usulan replenishment**: `efn_replenishment.enabled=true` sudah diset demo `efn_setup_supermarket`. Usulan dibuat
   setelah run `daily` ditutup ok **hari itu** (cron antrean).
4. **Near-expiry**: `efn_expiry_monitor.enabled` sengaja `false` (ambang masih placeholder Q-SM28). Untuk demo:
   Settings → Technical → System Parameters → `efn_expiry_monitor.enabled = true`, lalu jalankan cron "EFN expiry"
   (atau jalankan ulang seed: tahap 12 memindai bila flag menyala).
5. **Forecast v1 Odoo** (wizard "Hitung Forecast") membaca keluar stok dari `stock.move`. Order histori tidak membuat
   stock move, jadi **v1 tidak punya histori sebelum hari ini** di DB demo. Jangan memakai v1 untuk demo.

## 5. Pemetaan 4 skenario demo

| Skenario | Data yang di-seed | Cara mendemokan | Status |
|---|---|---|---|
| Lebaran & gajian | Order POS 2 × Ramadan/Lebaran (2025, 2026) + minggu gajian; kalender Ramadan/1 Syawal/libur | ETL → `v_fc_sales_daily` per SKU (mis. Kurma, Sirup, Nastar) di sekitar 31 Mar 2025 & 20 Mar 2026; run forecast-worker → `efn.forecast.daily` & usulan min/max (v2) | Data siap; ETL/worker di DB demo belum diuji; tampilan Insight persona = G47 |
| Near-expiry → markdown → terjual | Lot near/markdown/kedaluwarsa di outlet, 12 markdown (aktif + draft), `pos.config` outlet | Nyalakan flag expiry (§4.4) → alert; aktifkan markdown draft sebagai `efn_demo_smd_markdown_manager`; buka POS outlet (sesi baru; sesi histori sudah `closed`), pilih lot ber-markdown → harga turun; lot kedaluwarsa → blokir/override supervisor | Data siap; POS belum diuji di layar (G45) |
| Visitor → voucher → beli | Member fiktif saja | — | **Belum** (BFF `src/behavior`, segmentasi & `efn_campaign` = G46) |
| Restriksi DC | Capability produk + matriks outlet demo: SMD02 deny Impor, SMD03 closed (tanpa Alkohol/Impor), SMD05 tanpa freezer, alkohol hanya SMD01/SMD04; DSD bakery/galon; kapasitas rak | Buat transfer DC → SMD05 berisi produk Frozen → `efn_assortment:blocked missing_capability:freezer`; usulan replenishment mencatat `assortment:<kode>` / `dsd_only`; ringkasan per outlet tercetak di log tahap 4 | Data siap |

## 6. Batasan dan yang belum

- **Terverifikasi 20 Nov 2026** di Postgres + Odoo sekali-pakai, DB yang **hanya** memasang
  `efn_setup_supermarket,efn_expiry_monitor,point_of_sale` (tanpa modul migrasi ESB/brand lain): instalasi 0 ERROR,
  seed default 0 traceback/peringatan, run kedua idempoten. Order histori `paid`, `amount_total` = Σ baris, terbaca
  query bentuk `_SQL_SALES_FACT`; outbox loyalty 0, stock move 0, tanpa log audit POS; sesi histori `closed`.
  Pindai near-expiry setelah flag dinyalakan (versi sebelum revisi histori, tahap stok tidak berubah) = 187 alert
  (8 expired, 121 markdown, 58 near) + aktivitas kepala toko.
- **POS (tour Chrome, 20 Nov 2026):** `efn_expiry_monitor/tests/test_expiry_tour.py` (FEFO + harga markdown lot;
  lot kedaluwarsa → batal → PIN salah → override supervisor) lolos, suite tour CI 33/33. Tour menangkap bug G45
  (tambah produk lewat kartu produk tidak memasang FEFO/markdown/override), sudah diperbaiki di PR G45.
- **Engine forecast (dry-run, 20 Nov 2026):** `run_batch`/`compute` forecast-worker asli dengan sumber CSV yang
  meniru `v_fc_sales_daily` dari DB demo, outlet SMD01 (400 seri): `FORECAST_MODELS=AutoETS FORECAST_CV_WINDOWS=1`
  = **80 s**, 0 seri gagal, WAPE backtest 28 hari 0,65 (baseline mean-56 0,67; seasonal naive 0,79). Default
  `AutoETS,AutoARIMA` + CV 4 **> 57 menit untuk satu outlet tanpa selesai** → untuk demo pakai `FORECAST_MODELS=AutoETS`
  (atau CV 1) sampai performa AutoARIMA ditangani. WAPE tinggi karena noise sintetis + banyak SKU intermiten.
- **Belum diverifikasi:** jalur ETL → ClickHouse → forecast-worker → Odoo (ingest + usulan replenishment) di atas DB
  demo; POS manual di layar (buka sesi baru di outlet demo); transfer DC → outlet yang diblokir assortment lewat UI.
- Sesi histori ditutup tanpa akuntansi penutupan (tanpa jurnal, picking, statement kas); order tanpa `pos.payment`
  dan tanpa stock move. Jangan memakai DB demo untuk laporan keuangan.
- Forecast v1 Odoo tidak punya histori (lihat §4.5). Histori DC (keluar DC → outlet) tidak dibuat; forecast DC
  mengandalkan agregasi outlet di worker.
- Event visitor sintetis: belum (BFF `src/behavior`, G46). Mart Insight G47: belum.
- Barcode label timbang (`21…` berat, EFN Retail) tidak dibuat; produk kg memakai EAN-13 `299…` biasa. Uji katalog
  dengan nomenklatur GS1 (Q-SM3) tetap wajib sebelum pilot.
- Region kontrak supplier (`efn_region_ids`, kode `supplier_region`) tidak di-seed (alamat outlet demo tanpa
  `efn_region_id`).
- Lot impor ber-chiller ditaruh di Zona Chiller (bukan Zona Dry sesuai putaway kategori Impor Premium).
- Alkohol hanya mensyaratkan `alcohol_license` (bukan `non_halal_allowed`) sampai Q-SM21 diputuskan.
- Semua tanggal kalender adalah PLACEHOLDER (tanggal publik umum; 2027 perkiraan) dan wajib dicocokkan dengan SKB /
  sidang isbat sebelum dipakai di data nyata.
- Bila `efn_pii` terpasang di DB demo, nomor HP member ikut terenkripsi (perilaku normal).
- Catatan §3/§6 tentang "tanpa `pos.payment` / tanpa stock move" berlaku untuk tahap 1–12 saja. Tahap G51 (§7)
  menambah `pos.payment` (A4) dan stock move histori 90 hari (A1/A2/A3/A7).

## 7. G51 — data end-to-end 90 hari (A1–A4, A7)

Kontrak: `docs/design/G51_SMD_E2E_LEAD_SPEC.md` bagian A (+ keputusan lead A7). Hanya PT SMD. A6 (histori forecast)
= skrip terpisah agent data, tidak di sini. **A5 (umur simpan) tidak menambah tahap:** `product.template.expiration_time`
sudah diisi tahap 2 per kelompok (sayur 5–7, buah 6–10, daging 3–4, ikan 2–3, bakery 3–4, dairy 14–21, frozen 180–365,
kering 365–730, minuman 270–540, snack 180–300, personal/household 730–1095, alkohol 730–1460, impor 180–365 hari).
Beberapa rentang lebih panjang dari contoh spesifikasi (sayur 3–5, bakery 2–3, frozen 90–180, kering 180–365); tidak
diubah karena mengubah rentang menggeser aliran `random` tahap 6 sehingga run ulang di DB lama membuat lot baru
(melanggar idempoten). Bila lead ingin rentang spesifikasi: ubah `KIND_BASE` dan buat ulang DB demo.

### Cara menjalankan

```bash
# Hanya bagian G51 di DB demo yang sudah berisi tahap 1-12 (disarankan):
docker exec -i -e SMD_SEED_PARTS=g51 efn-odoo-1 \
  odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http < scripts/dev/seed_demo_supermarket.py
# Smoke dulu (14 hari, 10 SKU) untuk mengukur waktu:
docker exec -i -e SMD_SEED_PARTS=g51 -e SMD_SEED_E2E_DAYS=14 -e SMD_SEED_E2E_SKUS=10 efn-odoo-1 \
  odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http < scripts/dev/seed_demo_supermarket.py
```

| Variabel | Default | Arti |
|---|---|---|
| `SMD_SEED_PARTS` | `all` | `all` = tahap 1–12 + G51; `g48` = tahap 1–12 saja; `g51` = G51 saja (tahap 1–2 tetap dipanggil untuk mencari supplier/produk, idempoten); atau daftar `flow,a7,a3,a4` |
| `SMD_SEED_E2E_DAYS` | 90 | panjang jendela (7–120), berakhir di hari histori terakhir (≤ kemarin) |
| `SMD_SEED_E2E_SKUS` | 30 | SKU terlaris yang dipasok DC (bukan DSD, lolos assortment) yang ikut alur A1/A2/A7; 0 = semua (lambat) |

Urutan per hari (kronologis, commit per hari): **flow** (A2 terima PO → selesaikan backorder kemarin → A1 usulan hari
kirim DC ini) → **a7** → **a3**. `a4` berjalan sekali setelah loop. Bagian `flow` sebaiknya selalu bersama `a7`:
tanpa A7 stok outlet terus naik (order POS histori tidak mengurangi stok).

### Apa yang dibuat

| Bagian | Isi | Penanda idempoten |
|---|---|---|
| A2 `flow` | PO per (supplier, hari terima): Σ qty disetujui A1 yang dikirim DC sampai kunjungan supplier berikutnya (jadwal `SUPPLIER_WEEKDAYS`) × 1,03. `date_order` = terima − delay kategori, `date_planned` = hari terima 10:00 WIB, ±10% diterima H+1 (terlambat; `effective_date` & `date_done` = tanggal terima nyata). Penerimaan dua langkah DC (Input → Stock), lot baru `SMDDC-YYMMDD-NNNN` dengan ED = terima + `expiration_time`. Baris PO memakai orderpoint DC yang ada; bila belum ada, 70% SKU E2E diberi orderpoint DC terkelola (`_efn_managed_orderpoint`, trigger manual) → `from_orderpoint=1`. Supplier = partner perusahaan fiktif (`efn_etl.purchase_partner` → `dim_sm_supplier`) | `purchase.order.origin` `DEMO-SMD-PO-<ref>-<YYYYMMDD>` |
| A1 `flow` | Per jadwal kirim aktif (`efn.delivery.schedule`, 44 demo) × tanggal kirim di jendela: `efn.replenishment.proposal` `approved` (catatan `[DEMO SUPERMARKET] G51`), baris = SKU E2E kategori jadwal dengan qty = penjualan POS histori outlet [tanggal kirim, kirim berikutnya) × 1,00–1,08 (8% baris diubah kepala toko + alasan). Dokumen pasok lewat `_efn_create_supply_document()` modul (orderpoint outlet + `action_replenish` CE): pick (DC Stock → Output) & ship (Output → transit) jam 18/20 WIB H−lead, receipt (transit → Input outlet) 06:00 lokal hari kirim, storage (Input → Stock) 07:00. ±18% usulan punya baris kurang kirim (35% baris di usulan itu, qty 40–85%) → ±6% baris; 40% usulan itu sisanya dikirim **H+1** (backorder, `on_time=0`), sisanya dibatalkan (`qty_shipped < qty_approved`) | `UNIQUE(schedule_id, delivery_date)` |
| A7 `a7` | Per outlet per hari satu picking tipe POS outlet (`pos_type_id`) Stock → Partners/Customers, 21:00 lokal, origin `DEMO-SMD-SALE-<outlet>-<YYYYMMDD>`; move per SKU E2E = qty terjual hari itu dari baris order POS histori. Reservasi FEFO (removal strategy kategori). **Pilihan negatif: dibatasi stok tersedia** (qty tanpa stok dibatalkan, tidak pernah negatif); A1 memasok dari penjualan yang sama sehingga kekurangan hanya terjadi di hari pertama jendela dan setelah kurang kirim (stockout realistis). Ringkasan log mencetak % qty keluar vs permintaan | origin picking |
| A3 `a3` | 1–3 `stock.scrap`/outlet/hari dari quant ber-lot outlet (semua SKU SMD), 50% lot ED terdekat (alasan **Kedaluwarsa** bila ED ≤ H+1, selain itu **Kualitas**), 30% acak **Rusak**, 20% acak **Kualitas**; qty 1–3 unit / 0,3–1,5 kg (≤ tersedia). Tag `stock.scrap.reason.tag` dicari per nama (dibuat bila belum ada). Nilai = `stock_move.value` (stock_account 19.0, HPP saat validasi). `date_done` + tanggal move di-back-date | `stock.scrap.origin` `DEMO-SMD-SCRAP-<outlet>-<YYYYMMDD>-<n>` |
| A4 `a4` | Metode non-tunai bersama (jurnal bank `SMQR`/`SMDB`/`SMCR`: "QRIS (DEMO)", "Kartu Debit (DEMO)", "Kartu Kredit (DEMO)") ditautkan ke 5 `pos.config` demo (`bypass_payment_method_ids_forbidden_change`). Satu `pos.payment` per order histori DEMO tanpa pembayaran: tunai outlet 35%, QRIS 35%, debit 18%, kredit 12%; `amount` = `amount_total`, `payment_date` = `date_order`. Semua order histori (bukan hanya 90 hari) | order yang sudah punya `pos.payment` dilewati |

Penanda hari selesai: `ir.config_parameter` `efn_seed_smd.g51_done.flow|a7|a3` (daftar tanggal). Hari yang sudah
selesai dilewati; gagal di tengah hari → hari itu di-rollback, jalankan ulang. Menjalankan smoke 14 hari lalu 90 hari
menambah hari-hari yang lebih awal belakangan (tidak kronologis; dapat diterima untuk demo, buat ulang DB bila ingin
bersih). Selama loop, bila `efn_expiry_monitor.enabled=true` flag dimatikan sementara (cek sisa umur penerimaan
membandingkan ED dengan **hari ini**, bukan tanggal histori) lalu dipulihkan di `finally`. Bila proses dibunuh paksa,
periksa flag itu.

Tanggal: validasi lewat ORM (qty, quant, nilai move), lalu kolom tanggal saja (`stock_picking.date_done/scheduled_date`,
`stock_move.date`, `stock_move_line.date`, `stock_quant.in_date` lot yang tiba, `stock_scrap.date_done`,
`purchase_order.effective_date`) di-UPDATE SQL ke tanggal histori. Jurnal stok real-time (bila kategori `real_time`)
memakai `force_period_date` = tanggal histori; default kategori CE 19 periodik → hanya `stock_move.value`.

### Perkiraan jumlah (default 90 hari, 30 SKU; belum diukur)

Bergantung pada SKU E2E yang terpilih (urutan terlaris acak berbenih). Perkiraan kasar: usulan ±1.500–2.000 (jadwal
harian Fresh/Dairy + 2–3×/minggu lainnya, hanya yang punya penjualan SKU E2E), baris ±4.000–8.000, picking A1 ±4 per
usulan + backorder; PO ±500–700; A7 ±450 picking (5 outlet × 90 hari), move ±10.000; scrap ±900 (5 × 90 × rata-rata 2);
`pos.payment` ±3.650 (satu per order histori). Angka nyata tercetak di ringkasan `g51_*`. Durasi **belum diukur**:
jalankan smoke 14 hari × 10 SKU dulu dan ekstrapolasi; bila > 15 menit, turunkan `SMD_SEED_E2E_SKUS`.

### Pemetaan ke mart (ETL)

- `fact_dc_fill_rate` (`sql_fill_lines` + `SQL_FILL_MOVES`): usulan `approved`, `delivery_date` di jendela,
  `picking_id` = picking ship (move DC pertama); ship = move done → transit (+ backorder ≤ 3); recv = move transit →
  Input outlet (melintasi batas outlet); storage Input → Stock tidak dihitung.
- `fact_dc_purchase_line` / `dim_sm_supplier`: PO DC `purchase`, `qty_received` penuh, `from_orderpoint` dari orderpoint
  DC terkelola. Catatan: mart tidak punya kolom tanggal terima; keterlambatan hanya terlihat dari
  `purchase_order.effective_date` / `date_done` picking di Odoo.
- `fact_scrap` (`sm_scraps`): gudang = gudang lokasi asal scrap (zona outlet), alasan = tag terkecil, nilai =
  Σ|`stock_move.value`|.
- `fact_pos_payment`: `pos_payment` order `paid`, `payment_date`, metode & tipe.

### Belum diverifikasi (G51)

Skrip **belum pernah dijalankan** (agent penulis tanpa shell). Asumsi yang perlu dicek pada run pertama: rantai
`action_replenish` menghasilkan satu set picking per usulan (tidak bergabung); `picking_ids_not_to_backorder` membatalkan
sisa pada move ber-lot; reservasi receipt outlet dari transit mengikuti lot ship; pembuatan `stock.move.line` manual pada
receipt PO (field `product_uom_id`); `stock.scrap.origin` ada di 19.0; `pos.order.config_id` tersimpan; runtime.

## 8. A6 forecast history

Pemilik: efn-data-ai-engineer (G51). Tujuan: `fact_forecast_accuracy` (WAPE/bias 8 minggu, halaman `/forecast`,
kartu `/exec` & `/category`) terisi di DB demo. Odoo menolak baris forecast bertanggal lampau (`invalid_date`), jadi
jalurnya dua langkah: **(1) forecast-worker asli menghitung run as-of tanpa RPC → file JSON; (2) skrip odoo-shell
`scripts/dev/load_forecast_asof.py` memuatnya** sebagai run `ok`, `is_active = False` (run aktif & usulan replenishment
tidak tersentuh; `_efn_on_run_closed_ok` tidak dipanggil). ETL lalu menyalin run (`fc_runs`, `fc_daily`) dan menghitung
akurasi: forecast run `ok` terakhir yang `generated_at`-nya (WIB) sebelum tanggal proyeksi vs penjualan aktual.

Arti `--as-of D` (wajib **Senin**, ≤ 400 hari lalu): batch `daily` seolah dijalankan **Minggu D−1 23:00 WIB**; histori
ClickHouse hanya s.d. D−1, proyeksi D..D+27. `run_id` deterministik per (PT, D) → ekspor/muat ulang idempoten (run yang
sudah ada dilewati). Tidak mengubah perilaku produksi (tanpa `--as-of` tidak ada yang berubah; tidak butuh
`FORECAST_ENABLED`, tidak memanggil Odoo).

Prasyarat: §4 langkah 1 (ETL forecast menyala, `v_fc_sales_daily` berisi histori SMD) dan image forecast-worker
(overlay `docker-compose.forecast.yml`, secret ClickHouse `clickhouse_password_forecast`). Tidak butuh API key Odoo.

```bash
# (1) Hitung 8 run as-of: Senin 1..8 minggu sebelum Senin minggu ini (Git Bash; file berisi id/angka saja, jangan commit).
mkdir -p var/forecast-asof
THIS_MON=$(date -d "today - $(( $(date +%u) - 1 )) days" +%F)
for k in 1 2 3 4 5 6 7 8; do
  D=$(date -d "$THIS_MON - $((7 * k)) days" +%F)
  MSYS_NO_PATHCONV=1 docker compose -f infra/compose/docker-compose.yml -f infra/compose/docker-compose.forecast.yml \
    --profile data run --rm --no-deps \
    -e FORECAST_MODELS=AutoETS -e FORECAST_CV_WINDOWS=1 -e FORECAST_BACKTEST_ENABLED=false \
    -v "$(pwd -W)/var/forecast-asof:/out" \
    forecast-worker python -m efn_forecast run --company SMD --as-of "$D" --out "/out/asof-SMD-$D.json"
done
ls -l var/forecast-asof   # 8 file asof-SMD-YYYY-MM-DD.json (±5-15 MB per file untuk 400 SKU x 6 gudang)

# (2) Muat ke Odoo DB demo (skrip menolak jalan tanpa EFN_ASOF_CONFIRM=demo).
docker cp var/forecast-asof efn-odoo-1:/tmp/forecast-asof
docker exec -i -e EFN_ASOF_DIR=/tmp/forecast-asof -e EFN_ASOF_CONFIRM=demo efn-odoo-1 \
  odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http < scripts/dev/load_forecast_asof.py
# log: "[efn-asof] asof-SMD-...json: run <uuid> as_of ... -> N baris, 0 ditolak" lalu ringkasan

# (3) ETL: akurasi 8 minggu + hitung ulang segera (kembalikan interval ke 86400 setelah demo siap).
#     infra/compose/.env: ETL_ENABLE_FORECAST=true ETL_FORECAST_ACCURACY_WEEKS=8 ETL_FORECAST_ACCURACY_INTERVAL_SECONDS=900
docker compose -f infra/compose/docker-compose.yml --profile data up -d --force-recreate etl
```

Cek ClickHouse (admin): `SELECT count(), min(generated_at), max(generated_at) FROM efn_dwh.dim_forecast_run FINAL
WHERE company_id = <SMD> AND state = 'ok'` (≥ 9 run: 8 as-of + run harian nyata) lalu `SELECT week_start, sum(abs_error)
/ sum(actual_qty) AS wape FROM efn_dwh.fact_forecast_accuracy FINAL GROUP BY week_start ORDER BY week_start` (8 minggu).
`v_sm_forecast_daily` tetap memakai run **terbaru menurut generated_at** (run harian nyata), bukan run as-of.

Catatan & batasan:
- Sumber penjualan = data ClickHouse SAAT INI dibatasi s.d. D−1 (penjualan terlambat/koreksi setelah D ikut terbaca bila
  bertanggal ≤ D−1 — wajar untuk demo). Master produk & kalender = kondisi sekarang (bukan snapshot historis).
- Run as-of tanpa `efn.forecast.chunk` (chunk_total = jumlah chunk file); run tampil di Odoo sebagai run `ok` nonaktif
  dengan `opened_at` = generated_at. Retensi Odoo menghapus barisnya 35 hari setelah dimuat (`closed_at`); salinan
  ClickHouse tetap.
- Jangan menghapus lalu memuat ulang run as-of setelah ETL menyalinnya: ETL tidak melihat penghapusan Odoo, sehingga
  run lama tetap `ok` di `dim_forecast_run` (run_id Odoo baru = baris ganda dengan generated_at sama). Bila terpaksa,
  muat ulang di DB demo baru.
- **Wajib untuk demo (QA G51):** `ETL_SM_FILL_RECALC_DAYS=90` di `infra/compose/.env` (default 14 hanya membaca usulan
  14 hari terakhir -> `fact_dc_fill_rate` seed 90 hari tidak penuh; batas atas 120). Setelah satu siklus penuh boleh
  dikembalikan ke 14 (baris lama tetap di ClickHouse).
- Keterlambatan PO (A2 "±10% terlambat") TIDAK terlihat di mart: `v_sm_dc_purchase.is_late` hanya menyala untuk PO `purchase`
  yang `date_planned` lewat dan `qty_received < product_qty`; seed menerima semua PO penuh.
- Flag ETL G51 terkait (default mati): `ETL_SM_STOCK_RECONSTRUCT_DAYS=90` (histori stok GMROI dari stock move A1/A2/A3/A7,
  butuh `ETL_ENABLE_SM=true`), lihat `services/etl/efn_etl/sm_stock_hist.py`.
- **Belum diverifikasi** (penulis tanpa shell): perintah di atas belum pernah dijalankan; izin tulis bind mount untuk
  user 65534 di Docker Desktop; durasi (±80 s per run untuk satu outlet dengan AutoETS CV 1 → perkirakan ±8-10 menit per
  run untuk 5 outlet + DC).
