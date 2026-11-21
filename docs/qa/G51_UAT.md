# UAT G51 — SMD end-to-end (data demo lengkap + SMD di halaman F&B Insight)

Spesifikasi: `docs/design/G51_SMD_E2E_LEAD_SPEC.md`; kontrak `docs/04_SERVICE_CONTRACTS.md` §28.14; runbook seed
`docs/runbooks/demo-supermarket-seed.md`. Semua langkah dijalankan di stack lokal demo (DB `efn_smd_demo`); belum pernah
dijalankan (QA hanya membaca kode).

## 0. Prasyarat

- [ ] Seed G48 + G51 selesai (runbook §7), forecast as-of dimuat (§8). `.env` demo: `ETL_ENABLE_SM=true`,
      `ETL_ENABLE_FORECAST=true`, `ETL_FORECAST_ACCURACY_WEEKS=8`, **`ETL_SM_FILL_RECALC_DAYS=90`**,
      `ETL_SM_STOCK_RECONSTRUCT_DAYS=90`, `INSIGHT_SM_ENABLED=true`. Tunggu satu siklus ETL penuh.
- [ ] Sedikitnya satu run forecast harian NYATA (forecast-worker tanpa `--as-of`) sudah `ok`; tanpa itu "forecast 14 hari ke
      depan" memakai run as-of terakhir (sudah lewat).
- [ ] Cek cepat ClickHouse (admin): `fact_dc_fill_rate` > 0 baris dan `min(delivery_date)` ~ 90 hari lalu; `fact_scrap`
      SMD > 0; `fact_pos_payment` SMD > 0; `dim_scrap_reason` terisi (Kedaluwarsa/Rusak/Kualitas); `agg_sm_stock_daily` ada
      baris `is_reconstructed = 1`; `fact_forecast_accuracy` 8 nilai `week_start` berbeda.

## 1. Direksi (exec admin) — SMD sebagai filter di halaman F&B

- [ ] **Bawaan semua PT.** Given sesi exec ber-kode ESB + SMD; When membuka `/` tanpa param `pt`; Then chip Brand/PT memuat semua
      brand + SMD, angka = ESB + SMD, ada catatan "basis campuran" dan label SMD "termasuk PPN"; tren menampilkan satu garis
      per brand termasuk "Supermarket Demo" (warna c7).
- [ ] **Hanya SMD.** When menyalakan hanya chip SMD (`?pt=SMD`) di `/`, `/sales`, `/payments`, `/distribution`, `/forecast`;
      Then tidak ada query/angka ESB; kartu khusus F&B (`/flow`, menu/BOM/FIN, Kualitas Data ESB) menampilkan "Tidak berlaku untuk SMD".
- [ ] **Campuran.** When memilih PB + SMD; Then kartu F&B-only memuat PB saja + catatan "Hanya memuat brand F&B".
- [ ] **Cabang `sm:<id>`.** When memilih satu outlet SMD di daftar Cabang; Then hanya SMD, hanya outlet itu, ESB tidak ikut;
      memilih satu cabang ESB saja = SMD tidak ikut; id `sm:` di luar `wids` sesi (ubah URL manual) = diabaikan + banner "Filter
      tidak valid".
- [ ] **Pembayaran.** `/payments?pt=SMD`: baris metode QRIS/Kartu Debit/Kartu Kredit/Tunai muncul, MDR kosong (bukan 0),
      tipe berawalan "SMD - "; total pembayaran SMD ~= omzet SMD termasuk PPN (selisih hanya retur/kembalian).
- [ ] **Penjualan.** `/sales?pt=SMD`: kategori diawali "SMD: ", weekday/heatmap terisi; filter kategori menu ESB aktif ->
      catatan "SMD tidak ikut dihitung".
- [ ] **Negatif: bukan exec.** Given sesi store/category tanpa exec; When `/?pt=SMD`; Then `SMD` ditolak (banner penyesuaian),
      tidak ada query SM; navigasi tetap lewat sidebar Supermarket.
- [ ] **Negatif: SM mati.** Given `INSIGHT_SM_ENABLED=false`; Then chip SMD tidak ada; halaman ESB normal.
- [ ] **Negatif: ClickHouse sisi SMD gagal.** Given view SM gagal sementara ESB hidup; Then angka ESB tampil + catatan
      "Data SMD tidak tersedia saat ini"; kedua sisi gagal = kartu galat (bukan nol).

## 2. Kepala toko — umur simpan

- [ ] Given kepala toko SMD01; When membuka `/store`; Then tabel "Umur simpan & sisa umur lot": produk, kategori, umur simpan
      (hari; 0 = "tidak dikelola"), lot terdekat kedaluwarsa, sisa hari, qty, nilai HPP; hanya outlet SMD01.
- [ ] Sayur/ikan = umur simpan 2-7 hari, kering/minuman = 180+ hari (seed A5); lot lewat tanggal tampil sisa hari negatif.
- [ ] **Negatif: outlet lain** (`wh=` diubah manual) = tidak tampil.
- [ ] Sama di `/category` untuk kategori yang dikelola.

## 3. DC / Distribusi

- [ ] `/distribution?pt=SMD` (exec) dan `/dc` (persona dc): fill rate (qty & baris) bukan 0% atau 100% seragam; ada outlet dengan
      kurang kirim ~5-8% baris; usulan backorder muncul sebagai pengiriman terlambat (`on_time = 0`).
- [ ] Top produk kurang kirim terisi (<= 10 baris); proporsi usulan per status; produk yang dikurangi kepala toko.
- [ ] PO supplier: tabel PO DC terisi, kolom "dari orderpoint" menyala untuk ~70% SKU; **catatan:** tidak ada PO "terlambat"
      dari seed (lihat risiko QA).
- [ ] Negatif: persona store tidak melihat PO/nilai stok DC.

## 4. Forecast

- [ ] `/forecast?pt=SMD`: grafik forecast vs aktual **8 minggu** (8 titik `week_start`, bukan 7), WAPE & bias per outlet dan
      per kategori terisi, tab WAPE; WAPE dihitung dari jumlah mentah.
- [ ] Forecast 14 hari ke depan dengan pita p10-p90 (keterangan "batas konservatif"); label waktu run = `generated_at` run nyata terbaru.
- [ ] ESB di halaman yang sama tetap "segera hadir"; kartu WAPE juga tampil di `/exec` dan `/category`.
- [ ] Negatif: forecast-worker mati -> halaman tetap tampil (run terakhir), tanpa galat; DB tanpa run as-of -> keadaan "belum ada
      histori akurasi", bukan WAPE 0.

## 5. Waste & GMROI (hasil seed)

- [ ] Waste % Direksi/Toko > 0 dengan alasan Kedaluwarsa/Rusak/Kualitas (bukan "Tanpa alasan" untuk semuanya).
- [ ] GMROI & "stok > 30 hari" terisi 90 hari dari rekonstruksi (kartu memberi catatan keterbatasan: tanpa lot/konsinyasi/
      revaluasi); hari yang punya snapshot nyata tidak berubah. Log ETL: "stok rekonstruksi negatif" mendekati 0.

## 6. Idempotensi seed

- [ ] Menjalankan ulang seed G51 = 0 PO/usulan/scrap/pembayaran baru (log "sudah ada"); ETL siklus berikutnya 0 baris berubah pada fill rate.
