# efn_setup_supermarket_online

Modul konfigurasi **data-only** (tanpa model, tanpa Python selain test) untuk kanal belanja online
**PT Supermarket Demo (SMD)**: pesan di Super-App lalu **ambil di toko** (pickup), plus **pre-order berjadwal (slot)**.
Tidak ada dine-in, QR meja, atau reservasi. Keputusan user 23 Nov 2026.

## Kenapa modul terpisah
- `efn_setup_supermarket` tidak perlu bergantung pada modul online (DB SMD tanpa kanal online tetap bisa dipasang).
- Modul ini **tidak** bergantung pada `efn_setup_online`. Modul itu berisi partner, tim, dan kanal GL/PB/BCR/BCF/WTZ
  (data brand lain). Partner generik, tim, jurnal, pricelist, pembulatan, dan user teknis SMD ditulis ulang di sini
  (SMD independen). Test memeriksa bahwa `efn_setup_online` dan modul `*esb*` tidak ada di rantai dependensi.

## Kontrak & ADR
- docs/04 §10 (E-28 kanal online), §12.1 (kode kanal = kode PT), §15.4 (slot pre-order), §28 (SMD; §28.6 `outlet_code`
  event ≤ 16 karakter; §28.13.8a pemetaan outlet → gudang).
- ADR-034 (kanal online lewat BFF, Proposed), ADR-019 A1 (jurnal "Titipan Online - Holding"), ADR-045 (SMD, Proposed),
  ADR-031 (tanpa modul pihak ketiga), K25-07 (online wajib lunas sebelum diproses).

## Isi
**data/** (selalu dimuat)
- `pos_category_data.xml`: 13 `pos.category` katalog online. Nama dipakai situs untuk halaman landing, **jangan
  diubah**: Sayur, Buah, Daging, Ikan & Seafood, Bakery, Dairy & Chilled, Frozen, Dry Grocery, Minuman, Snack,
  Personal Care, Household, Impor Premium. Alkohol sengaja tidak punya kategori online. Blok `noupdate="0"`: nama dan
  urutan dikembalikan saat upgrade.
- `efn_online_smd_data.xml`: partner generik "Pelanggan Super-App - PT Supermarket Demo" (tanpa HP, email, atau NPWP)
  dan tim penjualan "Super-App Supermarket Demo".

**demo/** (PLACEHOLDER pilot, hanya DB `--with-demo`, `noupdate="1"`)
| xmlid | Isi |
|---|---|
| `channel_smd` | Kanal `SMD`, brand `SMD`, company `efn_setup_supermarket.company_smd`, mode `confirm`, `max_order_amount` Rp5.000.000, `max_line_qty` 20, batas bayar 70 menit, `payment_modes` `qris,va,ewallet`, pickup saja |
| `user_online_smd` | User teknis `efn_online_smd`: Internal User + grup kanal, satu company, **tanpa API key** |
| `journal_titipan_smd` | Jurnal bank `TTPO` "Titipan Online - Holding" milik PT SMD |
| `pricelist_online_smd`, `cash_rounding_smd_idr_1` | Pricelist IDR SMD, pembulatan Rp1 |
| `calendar_pickup_smd_wib` / `_wita` | Jam ambil 09:00–20:00 setiap hari (WIB; WITA untuk Denpasar) |
| `outlet_smd01`..`outlet_smd05` | Kode outlet online `SMD01`..`SMD05` = gudang toko `efn_setup_supermarket.warehouse_smd01..05` (**bukan** DC `SMDDC`); persiapan 30 menit; slot 60 menit, 10 pesanan/slot, minimal 120 menit sebelum ambil, maksimal 7 hari ke depan |

Pajak: tidak ada fiscal position outlet. Harga online memakai `taxes_id` produk, yaitu PPN company SMD dari l10n_id
(tarif placeholder Q-SM5). PB1 tidak berlaku (ritel).

## Instalasi
```
# DB SMD dengan demo (efn_setup_supermarket sudah terpasang dengan demo)
odoo -d <db> -i efn_setup_supermarket_online --stop-after-init
odoo -d <db> -u efn_setup_supermarket_online --stop-after-init   # upgrade
odoo -d <db> -u efn_setup_supermarket_online --test-tags /efn_setup_supermarket_online --stop-after-init
```
Setelah itu jalankan seed (`scripts/dev/seed_demo_supermarket.py`, tahap 15) untuk mempublikasikan produk SMD
(semua kind kecuali alkohol) dan mengisi kategori online.

**Langkah pasca-instal (di luar modul):**
1. Buat API key user `efn_online_smd` lewat runbook `docs/runbooks/odoo-online-api-key.md`. Secret BFF:
   `odoo_api_key_online_smd`.
2. BFF: tambahkan `SMD` ke `ONLINE_CHANNELS` (env, bukan kode).

## Risiko
- **R-T15 / W-11 (masa berlaku API key):** kebijakan 35 hari ada di data `efn_setup_online`
  (`group_online_channel.api_key_duration` + `base.group_user` = 0). Di DB tanpa `efn_setup_online`, batas Odoo
  untuk user teknis = 90 hari (`base.group_user`). Skrip rotasi tetap membuat key 30 hari (`KEY_DAYS`). Rekomendasi
  untuk engineer: pindahkan kebijakan W-11 ke `efn_online_order`.
- **`pos.category` global (tanpa `company_id`):** POS brand lain tanpa "Restrict categories" memuat kategori SMD. Bila
  mengganggu, aktifkan `limit_categories` di `pos.config` brand lain (tanpa kode). Belum diverifikasi apakah POS 19
  menampilkan kategori yang tidak punya produk.
- Produk per kg dipesan online dalam kelipatan 1 kg (qty integer di kontrak cart).
- Produk impor non-halal (nama memuat "Babi") ikut dipublikasikan. Perlu keputusan label/filter halal (Q-SMO5).

## Keputusan klien yang dibutuhkan
- **Q-SMO1:** batas nominal per pesanan dan qty per baris (placeholder Rp5.000.000 / 20).
- **Q-SMO2:** jam ambil per toko (placeholder 09:00–20:00 setiap hari).
- **Q-SMO3:** waktu siap, kapasitas slot, minimal jeda, horizon pre-order (placeholder 30 menit / 10 per slot /
  120 menit / 7 hari).
- **Q-SMO4:** metode bayar online SMD (placeholder QRIS, VA, e-wallet; transfer bank butuh `efn_online_transfer`).
- **Q-SMO5:** penanda/penyaring produk non-halal di katalog online.
- **Q-SMO6:** penjualan alkohol online (butuh verifikasi usia; saat ini tidak dipublikasikan).
- Q-SM43: kode outlet online `SMD01`..`SMD05` (≤ 16 karakter); mohon konfirmasi.

## Belum
- Belum dijalankan di Odoo (ditulis tanpa shell; test perlu `--with-demo`).
- Modul belum ada di `EFN_TEST_MODULES` CI.
- Kanal belum diaktifkan di BFF; poin EFN (`loyalty_earn`/`loyalty_redeem`) mati.
