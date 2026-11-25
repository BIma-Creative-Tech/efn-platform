# UAT G69 — Penjualan per struk (ESB OMS) di Insight, brand PB

Status: **Draft** (efn-qa-engineer, 25 Nov 2026). Acuan: `docs/04_SERVICE_CONTRACTS.md` §33, ADR-040 §6c, runbook `docs/runbooks/esb-extract.md` §10.
Penguji: pemilik bisnis / manajer PB. Dijalankan di **efn-dev atau efn-prod gladi** (OMS di production terkunci kode sampai Q-ESB1, Q-OMS2, Q-OMS3). Repo publik: **jangan menempelkan angka penjualan, nama cabang, atau screenshot nyata ke repo/PR**; catat hasil di berkas terpisah di luar repo.

**Persiapan**
- `ESB_OMS_ENABLED=true`, `ESB_OMS_BRANDS=pb`, siklus OMS sudah berjalan minimal satu hari penuh, `oms-backfill` untuk 3 hari terakhir selesai.
- Satu outlet PB dan satu hari lengkap (disebut **outlet-hari uji**), hari yang sudah lewat (H-1 atau lebih tua) supaya data final.
- Laporan penjualan per jam / per struk dari ESB untuk outlet-hari yang sama (diekspor oleh pengguna ESB) sebagai pembanding.
- Akses Insight: persona manajer (halaman `/flow`) dan persona kepala toko (halaman `/fnb/store`).

---

### U1. Struk dan penjualan per jam cocok dengan laporan ESB
- **Given** outlet-hari uji dipilih di `/fnb/store` (bagian "Penjualan per jam & kanal")
- **When** penguji membandingkan jumlah struk dan penjualan bersih per jam dengan laporan ESB (jam = waktu struk ditutup)
- **Then** jumlah struk per jam sama persis; penjualan bersih per jam sama dengan "subtotal dikurangi diskon" di laporan ESB (selisih pembulatan <= Rp1 per jam)
- **And** total hari di kartu "Struk" sama dengan jumlah struk selesai di laporan ESB
- **Catatan:** bila penjualan bersih selalu selisih tetap proporsional, laporkan; arti `subtotal` vs `diskon` belum terverifikasi sumber primer.

### U2. Struk batal (void) dan struk terbuka tidak dihitung
- **Given** outlet-hari uji memiliki setidaknya satu struk batal dan, bila ada, satu struk belum dibayar menurut ESB
- **When** penguji membandingkan total struk di Insight dengan laporan ESB
- **Then** struk batal/terbuka tidak ada di jumlah struk maupun penjualan Insight
- **And** menarik ulang hari itu (tunggu siklus berikutnya) tidak menggandakan angka

### U3. Jam sepi dan jam buka-tutup
- **Given** outlet punya jam tanpa transaksi di tengah hari
- **When** grafik "Penjualan per jam" dibuka
- **Then** jam tanpa transaksi tampil 0 (bukan hilang), jam pertama = jam struk pertama, jam terakhir = jam struk terakhir
- **And** struk yang ditutup lewat tengah malam (bila ada) diperiksa manual: dicatat tampil di jam berapa (lihat catatan risiko QA: jam 00 bisa muncul di awal grafik)

### U4. Bauran kanal
- **Given** outlet-hari uji dengan beberapa kanal (dine in, take away, ojol/delivery)
- **When** tabel "kanal (jenis kunjungan)" dibuka
- **Then** nama kanal sama dengan nama di ESB, jumlah struk dan penjualan bersih per kanal sama dengan laporan ESB per jenis kunjungan, porsi berjumlah 100%
- **And** struk tanpa kanal muncul sebagai "Tidak diketahui" (bukan hilang)

### U5. Rata-rata nilai struk, pax, dan item per struk
- **Given** angka U1 sudah cocok
- **When** penguji menghitung manual: penjualan bersih total / jumlah struk; total pax; total item / jumlah struk
- **Then** kartu "Rata-rata nilai struk", "Pax (tamu)", "Item per struk" sama dengan hitungan manual (item = qty menu aktif; baris batal dan komponen paket tidak ikut dobel)

### U6. Label kesegaran data
- **Given** OMS aktif dan jam WIB di dalam 07:00-23:00
- **When** penguji membuka bagian per jam untuk hari **ini** (bila halaman mengizinkan) dan untuk **kemarin**
- **Then** label "Data per HH:MM WIB" sesuai waktu tarikan terakhir (selisih <= 30 menit dari jam sekarang di jam buka)
- **And** untuk hari kemarin label tidak menyesatkan: harus menyatakan waktu tarik data hari itu, bukan waktu tarik hari ini (temuan QA: query kesegaran saat ini hanya melihat hari ini; lihat laporan)
- **And** setelah OMS dimatikan (`ESB_OMS_ENABLED=false`), data terakhir tetap tampil dengan label waktu lama dan tanpa error

### U7. Sell-through per jam vs stok sisa toko
- **Given** halaman `/flow`, filter satu outlet PB dan hari uji, produk FIN yang punya pasokan dan penjualan
- **When** penguji mengambil dua produk: satu yang habis sebelum tutup, satu yang sisa; membandingkan dengan stok sisa menurut toko (hitung fisik atau laporan stok akhir hari)
- **Then** untuk produk yang sisa: pasokan - terjual = sisa fisik (toleransi sesuai catatan retur/transfer hari itu); "% terjual tutup" = terjual / pasokan; "Perkiraan jam habis" = "Tidak habis"
- **And** untuk produk yang habis: jam habis = jam penjualan saat kumulatif mencapai pasokan (cocok dengan keterangan staf toko ± 1 jam)
- **And** "% terjual 12:00 / 15:00 / 18:00" naik tidak pernah turun
- **And** produk tanpa pasokan di data arus barang menampilkan "n/a" (bukan 0% atau NaN)

### U8. Pemetaan menu ke produk FIN dan porsi tak terpetakan
- **Given** outlet-hari uji, sell-through terbuka
- **When** penguji memilih lima menu terlaris dan membandingkan jumlah produk FIN terjual per jam dengan perhitungan resep (qty menu x isi resep) serta total terjual FIN di indikator Core halaman yang sama
- **Then** jumlah FIN per produk sama dengan perhitungan manual; satuan sama dengan pasokan (EA); selisih terhadap "Terjual" Core dijelaskan oleh porsi tak terpetakan
- **And** bila ada menu tanpa resep/kode, catatan "Porsi tak terpetakan" muncul dengan persentase yang masuk akal (>0 dan < 100)
- **And** minuman/menu tanpa komponen FIN tidak dihitung sebagai tak terpetakan

### U9. Rekonsiliasi harian (diperiksa admin data)
- **Given** hari-hari uji (7 hari terakhir) sudah final
- **When** admin menjalankan kueri rekonsiliasi `v_esb_oms_recon_daily` (kolom `diff_bills_pct`, `diff_summary_pct`, `diff_core_pct`)
- **Then** selisih jumlah struk dan total penjualan OMS vs ringkasan OMS = 0% per cabang-hari; vs pembayaran ESB Core <= 0,5% (selisih lebih besar dicatat sebagai temuan, bukan otomatis gagal)

### U10. Pembatasan akses dan tampil-bersih
- **Given** akun kepala toko ber-cabang tunggal
- **When** membuka `/fnb/store` dan mencoba parameter cabang lain di URL
- **Then** hanya cabangnya yang tampil; `/flow` ditolak
- **And** untuk brand/PT tanpa data OMS (mis. brand lain, atau OMS dimatikan sejak awal) bagian "Penjualan per jam & kanal" dan "Sell-through per jam" **tidak tampil** (tanpa kartu kosong atau pesan galat)
- **And** tidak ada nama member, nomor kartu, atau kode voucher di layar mana pun (UU PDP)

---
Hasil tiap skenario: tanggal, penguji, Lulus/Gagal, catatan selisih (angka disimpan di luar repo).
