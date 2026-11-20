# Runbook: seed data simulasi Supermarket (G48)

Skrip: `scripts/dev/seed_demo_supermarket.py` (dijalankan lewat `odoo shell`, pola `scripts/dev/seed_demo_g28.py`).
Tujuan: DB demo PT Supermarket Demo (`SMD`) yang berisi katalog, stok lot, markdown, kalender, dan histori penjualan
24 bulan untuk mendemokan forecast, near-expiry, dan restriksi DC. **Hanya untuk dev/staging lokal.** Semua data
fiktif: tidak ada merek, nama orang, atau nomor HP nyata.

Terkait: ADR-045 (DC satu PT + assortment), ADR-046 (forecast), docs/04 §28.2a/§28.3/§28.4/§28.5/§28.5a,
`docs/fit-gap/G45_SUPERMARKET.md`, `services/odoo/addons/efn_setup_supermarket/README.md`. Semua ADR berstatus
Proposed.

## 1. Prasyarat

- Stack dev berjalan (`docs/runbooks/dev-stack-boot.md`), container Odoo 19 (contoh di bawah: `efn-odoo-1`,
  konfigurasi `/etc/odoo/odoo.conf`). `$DC` = perintah compose dev seperti di runbook boot.
- DB **terpisah** `efn_smd_demo` (cocok `dbfilter ^efn_.*$`), dibuat **dengan demo data**, dengan modul terpasang:
  `efn_setup_supermarket`, `efn_legacy_history`, `efn_expiry_monitor`, `point_of_sale` (beserta dependensinya).
  Skrip berhenti dengan `SEED ABORT ...` (exit 2) bila modul atau data demo (`efn_setup_supermarket.warehouse_smd01`)
  tidak ada.
- Jangan jalankan di `efn_odoo` dev bersama atau DB production: skrip menulis parameter
  `efn_mig_esb.cutover_date.SMD` dan histori legacy.

### Membuat DB demo (sekali)

Hentikan `odoo` dulu agar tidak ada dua proses yang menulis registry:

```bash
$DC --profile core stop odoo odoo-tls odoo-ws-tls
# Odoo 19: demo data tidak dimuat secara default -> --with-demo saat inisialisasi base.
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_smd_demo -i base --with-demo --stop-after-init
$DC --profile core run --rm --no-deps -e ODOO_DEV_MODE= odoo odoo -d efn_smd_demo --stop-after-init \
  -i efn_setup_supermarket,efn_legacy_history,efn_expiry_monitor,point_of_sale
$DC --profile core up -d odoo odoo-tls odoo-ws-tls
```

Bila user DB Odoo tidak punya hak `CREATEDB`, buat DB dulu sebagai owner (`createdb -O efn_odoo efn_smd_demo` lewat
container postgres), lalu jalankan perintah di atas.

## 2. Menjalankan seed

Default (400 SKU, histori 730 hari, cutover = hari ini, histori berakhir H-1):

```bash
docker exec -i efn-odoo-1 odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http \
  < scripts/dev/seed_demo_supermarket.py
```

Mode kecil untuk smoke test (± 1–2 menit):

```bash
docker exec -i -e SMD_SEED_SKUS=40 -e SMD_SEED_DAYS=120 efn-odoo-1 \
  odoo shell -c /etc/odoo/odoo.conf -d efn_smd_demo --no-http < scripts/dev/seed_demo_supermarket.py
```

Git Bash: tidak perlu `MSYS_NO_PATHCONV` (tanpa mount). Output: satu baris `SEED ...` per tahap, ringkasan hitungan,
lalu `SEED_DONE`. Tidak ada `SEED_DONE` = gagal (lihat traceback); tahap yang sudah selesai sudah di-commit.

### Parameter (env var, `docker exec -e`)

| Variabel | Default | Rentang | Arti |
|---|---|---|---|
| `SMD_SEED_SKUS` | 400 | 20–2000 | jumlah SKU placeholder (dibagi proporsional ke 14 kelompok; Alkohol ≥ 2, Impor ≥ 3) |
| `SMD_SEED_DAYS` | 730 | 28–1100 | panjang histori penjualan sebelum cutover |
| `SMD_SEED_CUTOVER` | hari ini (WIB) | `YYYY-MM-DD` | cutover ESB→Odoo; histori `[cutover − DAYS, cutover − 1]` |
| `SMD_SEED_STOCK_SKUS` | 150 | 0–2000 | SKU terlaris yang diberi stok di outlet (DC selalu semua SKU non-DSD) |
| `SMD_SEED_LOTS_MAX` | 3 | 1–5 | lot maksimum per SKU × outlet |
| `SMD_SEED_EXPIRED_LOTS` | 8 | 0–100 | lot sudah kedaluwarsa di outlet (skenario blokir POS) |
| `SMD_SEED_MARKDOWNS` | 12 | 0–200 | contoh `efn.lot.markdown` (genap aktif, ganjil draft) |
| `SMD_SEED_MEMBERS` | 50 | 0–999 | member fiktif |
| `SMD_SEED_SHELF_SKUS` | 30 | 0–2000 | SKU per outlet yang diberi kapasitas rak (`shelf_capacity_qty`) |
| `SMD_SEED_HISTORY` | 1 | 0/1 | 0 = lewati histori (tahap terlama) |
| `SMD_SEED_POS` | 1 | 0/1 | 0 = jangan buat `pos.config` per outlet |

### Idempoten & deterministik

- Run kedua dengan parameter sama tidak membuat duplikat: produk dikenali dari `default_code` `SMD-<KODE>-<NNNN>`,
  supplier `ref` `SMD-SUP-NN`, member `SMD-MBR-NNN`, baris assortment/markdown/season dari catatan
  `[DEMO SUPERMARKET]`, lot dari nama `<gudang>-<NNNN>-<k>`, kalender dari kunci `(company, date, kind, name)`,
  histori dari `bulk_upsert` (ON CONFLICT; run kedua = `inserted 0`, `unchanged` = semua).
- Semua angka acak dari `random.Random` berbenih tetap (4848 + kode SKU/outlet).
- **Tanggal relatif hari menjalankan:** umur lot dihitung dari hari ini. Menjalankan ulang di hari lain tidak membuat
  lot baru (nama lot tetap), tetapi juga tidak memperbarui tanggal kedaluwarsa lot lama. Untuk demo "segar", buat
  ulang DB. Tanpa `SMD_SEED_CUTOVER`, run di hari lain menambah baris histori untuk hari-hari baru dan memindahkan
  parameter cutover; baris lama tidak dihapus.

## 3. Isi data (default, perkiraan)

| Tahap | Isi | Perkiraan default |
|---|---|---|
| 1 Supplier | 15 `res.partner` perusahaan fiktif (tanpa kontak), 2 di antaranya DSD | 15 |
| 2 Produk | SKU placeholder di 10 kategori standar (+5 sub-kategori Fresh dari `efn_setup_supermarket`). Semua `is_storable`, `tracking=lot`, `use_expiration_date`, `expiration_time` / `use_time` / `removal_time` / `alert_time` per kelompok (`alert_time` = ambang near-expiry kategori). Barcode EAN-13 fiktif `299…` dengan check digit benar. ±55 produk timbang (UoM kg, `to_weight`). Capability: Frozen → freezer; Dairy, Daging, Ikan → chiller; Alkohol → izin alkohol; Impor: per produk (keju chiller, wagyu/salmon freezer, sosis/bacon babi chiller + non-halal) | 400 |
| 3 Supplierinfo | 1 supplier utama per SKU (`delay`, `min_qty`, harga = biaya) + ±20% SKU kering/minuman/snack punya supplier kedua | ±430 |
| 4 Assortment | DSD: kategori Bakery (per outlet) + galon/es batu (per produk). Kapasitas rak untuk 30 SKU terlaris per outlet. Jadwal supplier: 13 ke DC, 2 DSD × 5 outlet | ±170 baris, 23 jadwal |
| 5 Kalender | Ramadan & 1 Syawal 2023–2027, libur nasional & cuti bersama 2024–2026 (`source=manual`, nama bertanda `DEMO, konfirmasi SKB/isbat`). 3 `efn.forecast.season` v1 (Ramadan, pra-Lebaran, hari Lebaran) khusus SMD | ±220 entri |
| 6 Stok | Inventory adjustment (`stock.quant` + `action_apply_inventory`). DC: 2 lot aman per SKU non-DSD. Outlet: 150 SKU terlaris, 1–3 lot, ±10% near-expiry, ±12% dalam ambang markdown, + 8 lot kedaluwarsa. Lot outlet disimpan di zona sesuai capability/kategori | ±1.800 lot/quant |
| 7 Markdown | `efn.lot.markdown` pada lot near-expiry/markdown di outlet; 6 aktif (diaktifkan sebagai user demo Markdown Manager, fallback sudo), 6 draft; `notify_members=False` | 12 |
| 8 Member | 50 `res.partner` "Member Fiktif NNN (DEMO)", HP `+62 800-0000-NNNN` (format valid, bukan nomor seluler) | 50 |
| 9 POS | `pos.config` "Kasir SMD0N (DEMO)" memakai picking type POS outlet; per outlet satu jurnal kas `SMC<n>` + metode "Tunai SMD0N (DEMO)" (CE: metode tunai tidak boleh dipakai dua POS) | 5 |
| 10 Persona | Verifikasi (tanpa perubahan) user demo `res_users_demo.xml`: kepala toko ↔ outlet, grup, company. Password tidak di-set | — |
| 11 Histori | `efn.legacy.sales.daily` + `efn.legacy.flow.daily` (`sold` = qty) per SKU × outlet × hari, hanya hari qty > 0, `esb_brand=DEMO`, `esb_menu_code` = `default_code`, `run_id` tetap. Parameter `efn_mig_esb.cutover_date.SMD` | ±500–600 ribu baris **per model** (dicetak skrip) |

Pola histori: basis Pareto per SKU (sebagian intermittent), skala outlet (SMD01 1,3 · SMD02 1,0 · SMD03 0,6 ·
SMD04 1,15 · SMD05 0,8), weekday/weekend, minggu gajian (7 hari terakhir + 7 hari pertama), Ramadan per kategori
(kurma/sirup ×2–3), H-14..H-8 ×1,25, H-7..H-1 ×1,5–2,3 per kategori (nastar/kastengel ×4), hari Lebaran ×0,35,
H+2..H+6 ×0,75, libur nasional, Natal/Tahun Baru (alkohol, bakery), tren +8%/tahun, noise lognormal. Produk hanya
dijual di outlet yang lolos assortment (DSD tetap dijual).

Terukur 20 Nov 2026 (container lokal, DB baru): run pertama **±6 menit** (stok 62 s, histori 255 s), run kedua ±3
menit tanpa record baru. Hasil default: 400 SKU (55 kg), 444 supplierinfo, 2.211 lot (aman 2.024, markdown 121, near 58,
kedaluwarsa 8), 12 markdown, 5 `pos.config`, **533.082 baris histori per model**. Mode kecil (40 SKU, 120 hari): ±30 s.

## 4. Mengaktifkan forecast di atas data ini

1. **ETL** (`efn-data`): ETL membaca DB Odoo dari `ODOO_DB` (default `efn_odoo`). Untuk DB demo set
   `ODOO_DB=efn_smd_demo` dan terapkan grant read-only ke DB itu:
   `MSYS_NO_PATHCONV=1 docker exec -i efn-postgres-1 psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_smd_demo -f - < services/etl/sql/odoo-readonly-grants.sql`.
   Nyalakan `ETL_ENABLE_ODOO=true`, `ETL_ENABLE_FORECAST=true`, `ETL_ENABLE_FORECAST_LEGACY=true` di
   `infra/compose/.env`, lalu recreate ETL. Histori masuk `efn_dwh.fact_legacy_sales_daily` → `v_fc_sales_daily`.
2. **forecast-worker**: ikuti `docs/runbooks/forecast-worker.md` §1 dan §3 (`FORECAST_ENABLED=true`, overlay
   `docker-compose.forecast.yml`, user teknis + API key `efn_forecast_smd` **di DB demo**). Run manual:
   `docker compose ... exec forecast-worker python -m efn_forecast run --company SMD --once`.
3. **Usulan replenishment**: `efn_replenishment.enabled=true` sudah diset demo `efn_setup_supermarket`. Usulan dibuat
   setelah run `daily` ditutup ok **hari itu** (cron antrean).
4. **Near-expiry**: `efn_expiry_monitor.enabled` sengaja `false` (ambang masih placeholder Q-SM28). Untuk demo:
   Settings → Technical → System Parameters → `efn_expiry_monitor.enabled = true`, lalu jalankan cron "EFN expiry"
   (atau jalankan ulang seed: tahap 12 memindai bila flag menyala).
5. Forecast v1 Odoo (wizard "Hitung Forecast") membaca `efn.legacy.flow.daily` sebelum cutover (hook
   `efn_legacy_history`) + season SMD dari tahap 5.

## 5. Pemetaan 4 skenario demo

| Skenario | Data yang di-seed | Cara mendemokan | Status |
|---|---|---|---|
| Lebaran & gajian | Histori 2 × Ramadan/Lebaran (2025, 2026) + minggu gajian; kalender Ramadan/1 Syawal/libur; season v1 SMD | Insight/Metabase `v_fc_sales_daily` per SKU (mis. Kurma, Sirup, Nastar) di sekitar 31 Mar 2025 & 20 Mar 2026; run forecast-worker → `efn.forecast.daily` & usulan min/max | Data siap; tampilan Insight persona = G47 |
| Near-expiry → markdown → terjual | Lot near/markdown/kedaluwarsa di outlet, 12 markdown (aktif + draft), `pos.config` outlet | Nyalakan flag expiry (§4.4) → alert; aktifkan markdown draft sebagai `efn_demo_smd_markdown_manager`; buka POS outlet, pilih lot ber-markdown → harga turun; lot kedaluwarsa → blokir/override supervisor | Data siap; POS belum diuji di layar (G45) |
| Visitor → voucher → beli | Member fiktif saja | — | **Belum** (BFF `src/behavior`, segmentasi & `efn_campaign` = G46) |
| Restriksi DC | Capability produk + matriks outlet demo: SMD02 deny Impor, SMD03 closed (tanpa Alkohol/Impor), SMD05 tanpa freezer, alkohol hanya SMD01/SMD04; DSD bakery/galon; kapasitas rak | Buat transfer DC → SMD05 berisi produk Frozen → `efn_assortment:blocked missing_capability:freezer`; usulan replenishment mencatat `assortment:<kode>` / `dsd_only`; ringkasan per outlet tercetak di log tahap 4 | Data siap |

## 6. Batasan dan yang belum

- **Terverifikasi 20 Nov 2026** di Postgres + Odoo sekali-pakai (addons branch ini, `--with-demo`): instalasi 0 ERROR,
  seed default 0 traceback/peringatan, run kedua idempoten (histori `unchanged` 533.082), pindai near-expiry setelah flag
  dinyalakan = 187 alert (8 expired, 121 markdown, 58 near; cocok dengan peran lot) + aktivitas kepala toko.
- **Belum diverifikasi:** ETL → ClickHouse → forecast-worker di atas DB demo, wizard forecast v1, POS di layar
  (markdown/FEFO/blokir lot), transfer DC → outlet yang diblokir assortment lewat UI.
- Event visitor sintetis: belum (BFF `src/behavior` belum ada, G46). Mart Insight G47: belum. Transaksi POS nyata
  pasca-cutover: tidak dibuat (hanya `pos.config`).
- Histori DC (`efn.legacy.flow.daily` gudang SMDDC, transfer keluar) tidak dibuat; forecast DC mengandalkan
  agregasi outlet di worker.
- Barcode label timbang (`21…` berat, EFN Retail) tidak dibuat; produk kg memakai EAN-13 `299…` biasa. Uji katalog
  dengan nomenklatur GS1 (Q-SM3) tetap wajib sebelum pilot.
- Region kontrak supplier (`efn_region_ids`, kode `supplier_region`) tidak di-seed (alamat outlet demo tanpa
  `efn_region_id`).
- Lot impor ber-chiller ditaruh di Zona Chiller (bukan Zona Dry sesuai putaway kategori Impor Premium).
- Alkohol hanya mensyaratkan `alcohol_license` (bukan `non_halal_allowed`) sampai Q-SM21 diputuskan.
- Semua tanggal kalender adalah PLACEHOLDER (tanggal publik umum; 2027 perkiraan) dan wajib dicocokkan dengan SKB /
  sidang isbat sebelum dipakai di data nyata.
- Bila `efn_pii` terpasang di DB demo, nomor HP member ikut terenkripsi (perilaku normal).
