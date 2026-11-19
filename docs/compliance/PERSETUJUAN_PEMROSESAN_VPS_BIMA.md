# Lampiran Persetujuan Pemrosesan Data pada Server Bima Creative (DRAF)

- **Status:** **DRAF untuk ditinjau Legal/DPO klien** — belum berlaku, belum ditandatangani. Bukan pengganti perjanjian
  pemrosesan data (DPA); lampiran ini merangkum hal yang perlu disetujui dan menjadi lampiran DPA.
- **Tanggal draf:** 18 November 2026 · Penyusun: efn-architect (tim proyek EFN Platform)
- **Rujukan teknis:** `docs/decisions/ADR-043-vps-bima-creative-dev-dan-ekstraksi-esb.md`, ADR-040 (datamart ESB),
  `docs/compliance/PDP_DATA_INVENTORY.md` §2.8.11 dan §2.8.13, `docs/compliance/PDP_TRANSFER_LINTAS_NEGARA.md` P9
- **Catatan Legal:** rujukan pasal UU No. 27 Tahun 2022 tentang Pelindungan Data Pribadi di bawah adalah rujukan kerja
  tim teknis dan **wajib diverifikasi Legal**. Isian bertanda `[…]` dilengkapi para pihak.

> **Ringkasan.** Tim proyek meminta izin menjalankan penarikan data dari sistem ESB (hanya membaca) di server sewa milik
> Bima Creative, karena prosesnya berjalan beberapa malam berturut-turut. Data pelanggan dan member **tidak** ditarik.
> Server dienkripsi, hanya bisa diakses lewat jaringan privat oleh orang yang namanya disetujui klien, dan data dihapus
> dari server itu setelah dipindah ke infrastruktur EFN atau saat kerja sama berakhir. Klien dapat menghentikan
> pemrosesan kapan saja.

## 1. Para pihak dan peran
| Peran | Pihak | Keterangan |
|---|---|---|
| Pengendali data | [PT Holding EFN dan/atau PT pemilik masing-masing brand: Paris Baguette, Wetzel's Pretzels, Bacha Coffee, Im Donut, Curry Up — ditentukan Legal] | Menentukan tujuan dan cara pemrosesan |
| Pemroses data | Bima Creative [nama badan hukum, alamat] | Memproses hanya atas perintah tertulis pengendali |
| Sub-pemroses | Penyedia VPS: [nama penyedia], data center: [kota, negara] | Wajib disebut sebelum pemrosesan dimulai; penggantian wajib pemberitahuan tertulis ke pengendali paling lambat [14] hari sebelumnya |

## 2. Tujuan pemrosesan
1. Menarik data operasional dan penjualan dari ESB Core (sistem lama klien) **hanya dengan membaca**, untuk:
   (a) dashboard manajemen, (b) bahan migrasi ke Odoo, (c) prediksi permintaan dapur pusat.
2. Termasuk penarikan histori **24 bulan** untuk lima brand dan pembaruan harian sampai tanggal peralihan (cutover) tiap brand.
3. Data **tidak** dipakai untuk tujuan lain, tidak dijual, tidak dibagikan ke pihak lain, dan tidak dipakai untuk
   melatih model kecerdasan buatan.

Di luar lampiran ini: server yang sama/berbeda milik Bima Creative juga dipakai sebagai **tempat kerja pengembangan**
(kode program dan data uji buatan). Tempat kerja itu **tidak boleh** memuat data atau password klien.

## 3. Data yang diproses
| Termasuk | Tidak termasuk |
|---|---|
| Data cabang, produk/menu, resep | Data pelanggan dan member |
| Penjualan per baris menu per hari (nomor transaksi, tanpa nama pelanggan/kasir) | Nama/identitas kasir dan staf pembuat dokumen (dibuang sebelum disimpan) |
| Ringkasan pembayaran per metode (tanpa data kartu) | Nomor kartu, data rekening pelanggan |
| Produksi, pengiriman, transfer stok | Catatan teks bebas dan lampiran dokumen (dibuang) |
| Pemasok: kode, nama, status (nama pemasok perorangan = data pribadi) | Alamat, telepon, kontak pemasok |
| Kredensial akun API ESB **khusus baca** per brand | Akun ESB admin (dilarang dipasang di server Bima Creative) |

Kategori data pribadi: hanya residu nama pemasok perorangan. Sebagian besar data adalah **informasi bisnis rahasia**
klien dan dilindungi kewajiban kerahasiaan yang sama.

## 4. Lokasi
- Data center: **[kota, negara]**. Usulan tim proyek: **wajib di Indonesia**.
- Bila di luar Indonesia: tidak boleh dimulai sebelum DPO menyetujui dasar transfer lintas negara (rujukan kerja UU PDP
  Pasal 56; pertanyaan Q-T8).
- Data tidak dipindahkan ke lokasi lain tanpa persetujuan tertulis pengendali.

## 5. Kontrol keamanan yang wajib
1. Server/mesin virtual **khusus** untuk pemrosesan ini, terpisah dari tempat kerja pengembangan.
2. Penyimpanan **terenkripsi** (disk atau volume terenkripsi) untuk data, password, dan cadangan.
3. Koneksi terenkripsi di setiap jalur; akses hanya lewat **jaringan privat (WireGuard)**; tidak ada layanan yang
   terbuka ke internet selain pintu jaringan privat.
4. Koneksi keluar hanya ke ESB, GitHub, dan registry perangkat lunak yang diperlukan.
5. Password per brand hanya dipakai di server ini, tidak dikirim lewat chat/email, dan diganti saat orang yang
   mengetahuinya keluar dan saat pemrosesan berakhir.
6. Asisten AI/agen otomatis **tidak** dijalankan di server data ini dan tidak diberi akses ke data mentah; verifikasi
   hanya memakai angka ringkasan (jumlah baris, total per brand per hari).
7. Server data ini **tidak** terhubung ke Odoo atau sistem transaksi klien.
8. Penarikan mematuhi batas beban yang disetujui klien/ESB (saat ini usulan: 2 permintaan per detik per brand, malam
   pukul 22.00–05.00 WIB, satu brand pada satu waktu).

## 6. Akses
- Hanya orang yang namanya tercantum di bawah, masing-masing dengan akun sendiri (tidak ada akun bersama).
- Akses administrator dicatat; daftar akses ditinjau setiap bulan dan dicabut di hari yang sama saat orang tersebut
  tidak lagi bertugas.
- Klien berhak meminta laporan akses dan bukti kontrol keamanan (audit) dengan pemberitahuan [7] hari.

| Nama | Peran | Hak akses | Disetujui klien (paraf) |
|---|---|---|---|
| [ … ] | Administrator server | root/sudo | |
| [ … ] | Operator ekstraksi | user data | |
| [ … ] | Pengguna dashboard (bila diizinkan) | baca dashboard | |

## 7. Masa simpan dan penghapusan
- Server Bima Creative adalah **tempat sementara**. Data dihapus dari server ini, termasuk cadangan dan snapshot, pada
  saat yang **paling awal** dari:
  1. data telah dipindahkan ke infrastruktur milik EFN dan pencocokan data dinyatakan lulus;
  2. perjanjian dengan Bima Creative berakhir;
  3. [30] hari setelah peralihan (cutover) brand terakhir ke Odoo;
  4. permintaan tertulis pengendali.
- Cara hapus: pemusnahan kunci enkripsi penyimpanan dan penghapusan volume, disertai **berita acara penghapusan**
  yang ditandatangani Bima Creative dan diserahkan ke pengendali paling lambat [7] hari kerja.
- Data yang perlu disimpan jangka panjang (sesuai kebijakan retensi klien) disimpan di infrastruktur EFN, bukan di
  server Bima Creative.

## 8. Insiden
- Bima Creative wajib memberi tahu pengendali/DPO **paling lambat 24 jam** setelah mengetahui dugaan kebocoran,
  akses tidak sah, atau kehilangan data/password, agar pengendali dapat memenuhi kewajiban pemberitahuan 3×24 jam
  (rujukan kerja UU PDP Pasal 46 — diverifikasi Legal).
- Langkah segera: hentikan penarikan, ganti password ESB dan password server, amankan jejak audit, ikuti prosedur
  respons insiden klien.

## 9. Berakhirnya pemrosesan
- Pengendali dapat menghentikan pemrosesan kapan saja dengan pemberitahuan tertulis; Bima Creative menghentikan
  penarikan paling lambat [1] hari kerja dan menjalankan penghapusan sesuai butir 7.
- Saat berakhir: akun API ESB khusus baca dinonaktifkan atau passwordnya diganti oleh IT klien.

## 10. Prasyarat sebelum pemrosesan dimulai (diisi tim proyek)
| # | Prasyarat | Status |
|---|---|---|
| 1 | Perjanjian pemrosesan data (DPA) ditandatangani | [ ] |
| 2 | Akun API ESB **khusus baca** per brand tersedia (Q-ESB1) | [ ] |
| 3 | Izin beban penarikan histori dari klien/ESB (Q-ESB7) | [ ] |
| 4 | Konfirmasi hak klien atas data dan penggunaan API ESB, termasuk oleh pihak ketiga (Q-ESB9) | [ ] |
| 5 | Lokasi data center, spesifikasi, dan daftar pemegang akses diketahui (Q-VPS1..Q-VPS3) | [ ] |
| 6 | Persetujuan DPO atas catatan inventaris data (PDP_DATA_INVENTORY §2.8.13) dan, bila perlu, transfer lintas negara (Q-T8) | [ ] |
| 7 | Pemeriksaan kontrol keamanan butir 5 oleh tim keamanan proyek | [ ] |

## 11. Persetujuan
| Pihak | Nama | Jabatan | Tanda tangan | Tanggal |
|---|---|---|---|---|
| Pengendali (Direksi) | | | | |
| DPO / Legal | | | | |
| Bima Creative | | | | |
