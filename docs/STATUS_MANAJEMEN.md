# Status Proyek EFN Platform — Ringkasan Satu Halaman untuk Manajemen

**Per 7 Oktober 2026 · Fase 1 (pilot kasir retail + poin EFN) · Disusun oleh tim arsitektur (Athera)**

## Ringkasan
Fondasi (Fase 0) selesai di lingkungan pengembangan. Fase 1 sudah berjalan: **poin EFN di kasir** (dapat poin saat belanja, tukar poin dengan kode OTP, poin ditarik kembali saat retur) dan **3 dashboard** (penjualan harian, stok & kedaluwarsa, Price Hunter) sudah dibangun dan diuji. Reviewer keamanan menyatakan **GO bersyarat** untuk menyalakan poin di **satu outlet pilot**. Ada 7 syarat. Empat di antaranya menunggu keputusan atau tindakan dari pihak klien, terutama **batas poin dari Finance** dan **server staging**.

## 1. Apa yang sudah terbukti berjalan (lingkungan pengembangan)
Staging belum ada. Semua bukti di bawah berasal dari lingkungan pengembangan.
- **Alur bisnis inti dari ujung ke ujung:** login OTP, laporan harga Price Hunter, poin, OTP promo di kasir, QRIS (simulasi), hapus akun di semua sistem, laporan analitik. Uji otomatis terakhir: **153 dari 153 pemeriksaan lolos**.
- **Poin EFN di kasir (baru):**
  - pelanggan mendapat poin saat belanja;
  - pelanggan bisa menukar poin dengan kode OTP ke HP-nya;
  - poin ditarik kembali bila barang diretur.

  Saat internet putus, kasir tetap berjalan, tetapi penukaran poin dimatikan. Poin dari transaksi offline dikirim menyusul.
- **Pengaman kecurangan poin (baru):**
  - kasir tidak bisa memanggil sistem poin secara langsung;
  - retur, refund, dan penukaran yang tidak sah ditolak atau ditahan untuk diperiksa supervisor;
  - ada batas harian per pelanggan (sementara ±Rp200.000 nilai poin per hari);
  - ada batas per PT dan per kasir per jam;
  - pencocokan otomatis berjalan tiap jam, dan peringatan dikirim ke tim bila ada kejanggalan.
- **Dashboard (baru):** penjualan harian per outlet, stok & barang mendekati kedaluwarsa, serta indeks harga pesaing dari Price Hunter. Dashboard hanya bisa dibuka lewat jaringan internal (WireGuard).
- **Keamanan & UU PDP:**
  - semua lalu lintas antar-komponen terenkripsi;
  - tidak ada nomor HP utuh di log;
  - pelanggan bisa mengunduh dan menghapus datanya;
  - data pelanggan yang sudah dihapus tidak bisa "hidup lagi" lewat impor data lama.

  Daftar data pribadi (RoPA) sudah diperbarui ke **versi 4** dan mencakup semua data fitur poin.
- **Odoo hanya berisi modul standar dan modul buatan EFN** (tanpa modul pihak ketiga, sesuai keputusan klien).

## 2. Status GO pilot poin di kasir
| # | Syarat | Pihak | Status |
|---|---|---|---|
| 1 | Uji ulang di **staging** (server sungguhan) | Tim teknis, butuh server | Belum ada server |
| 2 | **Batas poin final** ditetapkan Finance (per transaksi, per hari per pelanggan, per PT, per kasir) | **Finance** | Menunggu |
| 3 | Admin sistem Odoo **hanya Superadmin**; akun kasir tanpa hak supervisor | **IT klien** | Menunggu daftar nama |
| 4 | Supervisor meninjau daftar "Review Poin" setiap hari; ada petugas jaga yang menerima peringatan | **Operasional outlet** | Menunggu penunjukan |
| 5 | Daftar data pribadi diperbarui | Tim arsitektur | **Selesai** |
| 6 | Penguatan uji otomatis | Tim teknis | ±10 menit kerja |
| 7 | SOP: retur tanpa struk wajib disetujui supervisor | **Operasional outlet** | Menunggu |

Bila muncul peringatan kecurangan yang tidak bisa dijelaskan, fitur poin dimatikan dari pengaturan kasir. Kasir tetap bisa berjualan tanpa poin.

## 3. Risiko utama yang tersisa
| Risiko | Dampak bila terjadi | Penanganan |
|---|---|---|
| **Belum ada staging dan server produksi** | Pilot tidak bisa dimulai; semua bukti baru dari laptop pengembang | Pengadaan server (ADR-004) |
| **DPO belum ditunjuk**; masa simpan data belum ditetapkan | Kepatuhan UU PDP belum bisa dinyatakan | Keputusan Direksi |
| **Pemegang kunci (Superadmin) mungkin hanya satu orang** | Bila orang itu tidak ada, backup tidak bisa dipulihkan | Tunjuk minimal 2 orang |
| Nilai & batas poin masih angka sementara | Liabilitas poin di neraca belum bisa dihitung final | Keputusan Finance (Q-13) |
| Supervisor yang sama bisa membuat refund lalu menyetujuinya sendiri | Kerugian poin kecil, tetapi tercatat dan dibatasi plafon harian | Perbaikan selama 2 minggu pertama pilot |
| Retur tanpa struk tanpa memilih pelanggan tidak menarik poin | Kerugian kecil (±1 % nilai barang), dibatasi plafon harian | SOP supervisor (syarat 7) + perbaikan selama pilot |
| Nomor pelanggan dari sistem lama bisa tersimpan permanen bila dipakai saat impor saldo | Pelanggan yang sudah menghapus akun masih bisa dikenali lewat sistem lama | Putuskan **sebelum impor** (Q-P13, ±0,5 hari kerja) |
| Mitra pembayaran belum dipilih; satu celah pembayaran harus ditutup sebelum vendor aktif | Belum bisa menerima pembayaran online nyata | Tidak memblokir pilot poin |

## 4. Yang dibutuhkan dari manajemen (urut prioritas)
1. **Server staging** (ADR-004). Ini penghambat utama pilot. Setelah server ada, staging siap dalam ±1–2 minggu.
2. **Finance:** nilai poin, masa berlaku, perlakuan retur berpoin, dan **batas anti-kecurangan** (Q-13). Angka sementara: 1 poin per Rp10.000, 1 poin = Rp100, batas harian 2.000 poin per pelanggan.
3. **Pilih toko pilot** (P-1). Sertakan jumlah kasir, jumlah produk, perangkat, dan internet cadangan (P-6).
4. **Operasional outlet:**
   - aturan supervisor untuk refund, retur tanpa struk, void, dan toleransi selisih kas (P-3);
   - nama supervisor peninjau harian;
   - nama petugas jaga.
5. **IT klien:** daftar nama Superadmin (minimal 2 orang). Hanya mereka yang memegang hak admin sistem Odoo.
6. **Konsultan pajak:** perlakuan PPN atas penukaran poin (P-8).
7. **DPO / Legal:**
   - tunjuk DPO;
   - setujui aturan "nomor referensi data lama harus kode acak" sebelum impor saldo (Q-P13);
   - tetapkan masa simpan daftar tolak akun terhapus (Q-P12, usulan 90 hari).
8. Keputusan yang masih terbuka dari Fase 0 (7 keputusan Gelombang 1, SILO, anggaran modul buatan sendiri) tetap berlaku.

Daftar lengkap: `docs/decisions/README.md`, bagian "Syarat GO pilot loyalty" dan "Keputusan klien yang masih terbuka".

## 5. Biaya & waktu indikatif (belum termasuk server)
| Pos | Perkiraan |
|---|---|
| Lisensi perangkat lunak | **Rp0** (semua open source) |
| Modul Odoo buatan sendiri pengganti OCA | ±10–16 minggu-engineer (±Rp50–145 jt); tidak berubah |
| Perbaikan keamanan selama pilot (persetujuan refund terpisah, retur tanpa struk, rekonsiliasi ke order Odoo) | ±1–2 minggu tim |
| Fitur kasir opsional bila diminta di P-3 (persetujuan supervisor inline, log void, batas selisih kas di server) | ±1–3 minggu-engineer |
| Aturan nomor referensi opak untuk impor (Q-P13) | ±0,5 hari |
| Konektivitas outlet (WireGuard) | Rp0 per outlet kecil; ±Rp0,4–0,9 jt per outlet besar |

**Langkah berikutnya:** server staging → pasang dan uji ulang di staging (±1–2 minggu) → penuhi 7 syarat → nyalakan poin di satu outlet. Ada dua pilihan urutan: perolehan dan penukaran poin sekaligus, atau perolehan poin dulu lalu penukaran 1–2 minggu kemudian (lebih hati-hati) → evaluasi 2 minggu sebelum outlet lain.

*Catatan jujur: angka biaya adalah perkiraan arsitek dan perlu divalidasi. Semua bukti "berjalan" berasal dari lingkungan pengembangan.*
