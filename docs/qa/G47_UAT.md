# UAT G47 — EFN Insight supermarket per persona

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §28.13 (v20.3), `docs/contracts/insight_sm_kpi_defaults.json`, `docs/fit-gap/G47_INSIGHT_SM.md`.
Status: **belum dijalankan** (QA berbasis pembacaan kode; tidak ada runtime lokal). Dijalankan di stack `efn-stg` dengan data
demo `SMD` (DC + 5 outlet) setelah `INSIGHT_ENABLED=true`, `INSIGHT_SM_ENABLED=true`, `ETL_ENABLE_SM=true` (+ `_EXPIRY`,
`_CAMPAIGN` bila kartunya diuji) dan satu siklus ETL (≤ 15 menit) selesai. Production: flag tetap OFF sampai syarat §28.13.12.

Catatan umum: setiap butir "Given/When/Then" diringkas jadi satu baris. Centang bila lulus; tulis ID tiket bila gagal.
Akun uji: dibuat lewat form user Odoo (Access Rights), PT `SMD` ada di "Companies" user.

## 0. Prasyarat lintas persona (jalankan sekali)

- [ ] **Flag mati.** Given `INSIGHT_SM_ENABLED=false`; When user persona membuka `/store`; Then halaman `/sm-belum-aktif`, tanpa query data.
- [ ] **Sesi lama (eis1).** Given sesi dari token `eit1`; When membuka halaman persona; Then diarahkan `/buka-ulang` ("Buka ulang Insight dari Odoo").
- [ ] **Tanpa persona.** Given user hanya punya `group_insight_user` (ESB); When membuka Insight dari Odoo; Then tidak ada menu Supermarket.
- [ ] **Tanda tangan scope rusak.** Given request ke ClickHouse dengan `SQL_efn_sig2` diubah satu karakter (uji harness `g47_row_policy_harness.sh`); Then 0 baris, bukan galat bocor.
- [ ] **Data usang.** Given job ETL dimatikan > `fresh.*.stale_min`; Then kartu menampilkan badge "usang" + waktu data, bukan angka nol.
- [ ] **ClickHouse mati.** Given `efn-data` mati; Then halaman tampil keadaan galat per kartu, Odoo/POS tetap normal.

## 1. Kepala toko (`group_insight_store`, route `/store`)

- [ ] Given user store dengan satu outlet di "Outlet dikelola"; When membuka `/store`; Then chip "Kepala toko · <nama outlet>" dan hanya data outlet itu (ST-1..ST-4).
- [ ] **ST-1 usulan order.** Given usulan `proposed` untuk besok; Then tampil tanggal kirim, cut-off, status; usulan lewat cut-off = `expired` (tidak dikirim otomatis, L-149); tombol setuju TIDAK ada (hak tindakan ada di Odoo).
- [ ] **ST-2 kedaluwarsa.** Given lot near-expiry + markdown aktif; Then baris lot menampilkan sisa hari, nilai HPP, status markdown.
- [ ] **ST-3 stok kritis.** Given SKU `on_hand` ≤ min atau cover < `critical.cover_days`; Then muncul di daftar dengan rata-rata jual dan penanda "sudah di usulan" bila ada.
- [ ] **ST-4 waste.** Given scrap bulan ini; Then waste % memakai penyebut `waste.basis`; tanpa scrap = keterangan "Belum ada pencatatan susut", bukan 0%.
- [ ] **Negatif: tanpa margin.** When user store membuka `/exec` atau memanggil URL `margin`; Then `/tidak-berwenang`; kartu margin/GMROI/nilai stok total/PO tidak ada.
- [ ] **Negatif: outlet lain.** When parameter `wh=<outlet lain>` diubah manual di URL; Then data outlet lain tidak muncul (filter + row policy).
- [ ] **Negatif: tanpa outlet.** Given user store tanpa outlet dikelola; Then pesan "Belum ada outlet yang ditetapkan", bukan halaman kosong tanpa penjelasan.

## 2. Direksi (`group_insight_exec`, route `/exec`)

- [ ] Given user exec; Then chip "Direksi · semua outlet (5)"; angka PT-level + per outlet (EX-1..EX-6).
- [ ] **EX-1 omzet.** Omzet memakai `net_sales_incl_tax` dan delta vs periode lalu; YoY kosong dengan keterangan sampai ada 12 bulan histori SMD (histori ESB tidak dipakai, L-196).
- [ ] **Margin & GMROI.** Given HPP tersedia; Then margin kotor % + cakupan HPP; GMROI mengikuti `gmroi.period/stock_avg/include_dc/annualize`.
- [ ] **WAPE/bias.** Then WAPE dihitung dari jumlah mentah (bukan rata-rata persentase); garis acuan = `thr.wape.warn_pct`.
- [ ] **Waste per kategori & outlet** mengikuti `waste.basis`.
- [ ] **Negatif: bukan halaman store.** When exec membuka `/store`; Then `/tidak-berwenang` (Q-INS-S9 default tidak) kecuali juga punya grup store.
- [ ] **Negatif: tanpa hak setuju.** Then tidak ada tombol approve apa pun di Insight.
- [ ] **Negatif: ESB terpisah.** Then tidak ada angka dari `efn_esb`/legacy di halaman SM.

## 3. Category manager/buyer (`group_insight_category`, route `/category`)

- [ ] Given "Kategori dikelola (Insight)" = {Buah, Sayur}; Then filter awal = kategori itu (default, bukan pembatas data); mengganti filter ke kategori lain tetap diizinkan (L-189).
- [ ] **CM-1 OOS rate.** Given universe `oos.universe`; Then OOS rate = SKU habis / SKU universe, matriks kategori × outlet hanya baris ber-universe > 0.
- [ ] **CM-3 SKU paling sering habis.** Tabel 10 SKU terurut hari habis; nama produk dari master.
- [ ] **CM-4 near-expiry** per status tampilan + total nilai.
- [ ] **Harga vs pasar.** Given sampel Price Hunter ≥ `thr.price.min_samples`; Then selisih % dan label "Di atas pasar" bila > `thr.price.warn_pct`; sampel kurang = "Sampel belum cukup" (netral).
- [ ] **Sinyal perilaku (CM-6).** Given viewers_30d < 10; Then kartu menyembunyikan baris (k-anonim, tidak configurable); tracking OFF = keadaan "belum aktif".
- [ ] **Negatif: tanpa PO/nilai stok DC di halaman.** Then tidak ada kartu PO supplier.
- [ ] **Negatif: kategori turunan.** Given filter induk; Then anak ikut (cocokkan `categ_parent_path`).

## 4. DC/logistik (`group_insight_dc`, route `/dc`)

- [ ] Given user dc; Then semua outlet + DC PT SMD (DC-1..DC-3, PO, umur stok).
- [ ] **DC-1 fill rate.** Given `fill.basis=shipped`; Then fill = Σ min(qty_dikirim, disetujui)/Σ disetujui; ganti ke `received` dan ETL berikutnya mengubah angka; `fill.level=line` memakai baris terpenuhi penuh.
- [ ] **DC-3 per outlet** terurut menaik; garis acuan 95% / 90% (`thr.fill.good_pct` / `warn_pct`).
- [ ] **Usulan masuk** (≥ hari ini) dengan status; usulan expired tampil sebagai kedaluwarsa.
- [ ] **PO supplier.** Given PO `purchase` belum diterima penuh melewati `date_planned`; Then ditandai terlambat; hitungan vs `thr.po_late.warn/bad`; supplier perorangan tampil "Supplier #<id>" (tanpa nama, PDP).
- [ ] **Umur stok.** Then bucket 0-30/31-60/61-90/>90 hari; catatan: ambang `thr.stock_age.days` dipetakan ke bucket terdekat (lihat risiko QA R-3).
- [ ] **Negatif: tanpa margin/GMROI.** When dc membuka `/exec` atau meminta margin; Then ditolak.
- [ ] **Negatif: picking dipakai >1 usulan.** Given `picking_shared=1`; Then fill rate ditandai perkiraan (alokasi FIFO), bukan angka pasti.

## 5. Marketing (`group_insight_marketing`, route `/marketing`)

- [ ] **MK-0 consent.** Then banner permanen "hanya pengunjung yang menyetujui pelacakan"; persen N tidak ditampilkan (tidak dapat dipenuhi, by design).
- [ ] **Tracking OFF.** Given flag tracking mati; Then hanya EmptyState "Pelacakan perilaku belum aktif", tanpa KPI nol.
- [ ] **MK-1 funnel.** Given tracking ON; Then pengunjung/keranjang/checkout/pembeli = Σ perangkat-hari berlabel jujur; filter kanal; jumlah < 10 = "< 10", delta disembunyikan.
- [ ] **Kampanye.** Given kampanye `approved`/`sent`; Then terkirim, voucher diterbitkan/dipakai, diskon, omzet dengan voucher (tanpa omzet inkremental, Q-INS-S14); biaya vs anggaran > 90% = perlu dicek, > 100% = bermasalah; tanpa anggaran = tanpa status.
- [ ] **Redemption online.** Given voucher dipakai di SO online `sale`; Then terhitung sekali; pelunasan di kasir (order POS tanpa kupon) tidak dobel.
- [ ] **Negatif: tanpa data anggota.** Then tidak ada nama/HP/member di halaman maupun di respons query.
- [ ] **Negatif: tanpa data toko.** When marketing membuka `/store`; Then `/tidak-berwenang`; tidak ada omzet/margin per outlet.
- [ ] **Negatif: event outlet tak dikenal.** Given event dengan (channel_code, outlet_code) tak terpetakan; Then masuk baris tingkat PT (warehouse 0), tidak hilang.

## 6. Pengelola KPI (`group_insight_kpi_admin`, Odoo: Inventory > Configuration > Definisi KPI Insight)

- [ ] **Hak.** Given user kpi_admin tanpa persona; Then dapat CRUD definisi KPI PT-nya, TIDAK melihat dashboard; user tanpa grup tidak melihat menu (hanya `base.group_system` baca).
- [ ] **Ubah ambang.** When `thr.oos.warn_pct` diubah ke 3; Then tersimpan kanonik ("3"), tercatat di audit `efn_audit`; setelah ≤ 15 menit (ETL `sm_dims_kpi`) kartu OOS memakai 3% dan tombol "i" menampilkan tanggal berlaku.
- [ ] **Negatif: nilai di luar rentang** (`critical.cover_days=0.4`) → `efn_insight_sm:kpi_range`.
- [ ] **Negatif: pilihan enum salah** (`gmroi.period=week`) → `kpi_choice`; format salah (`fill.grace_days=1.5`) → `kpi_type`.
- [ ] **Negatif: pasangan ambang terbalik** (`thr.oos.warn_pct=6` > bad 5) → ditolak; hapus baris yang membuat pasangan terbalik juga ditolak.
- [ ] **Negatif: duplikat** (company, key) → `kpi_duplicate`; ubah kunci/PT setelah dibuat → `kpi_readonly`.
- [ ] **Hapus baris** = kembali ke default; ETL menonaktifkan baris (active 0) dan dashboard memakai default registri; `is_default` benar.
- [ ] **Isolasi PT.** Given dua PT SM; Then admin PT A tidak melihat/mengubah baris PT B (record rule).
- [ ] **Nilai tidak sah lolos ke DB** (insert SQL manual) → ETL menyalin dengan `active=0` (default dipakai), dashboard tidak error.
