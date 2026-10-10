# Status Proyek EFN Platform — Ringkasan Satu Halaman untuk Manajemen

**Per 10 Oktober 2026 · Fase 1 (poin EFN di kasir retail), Fase 2 (restoran + layar dapur), Fase 3 (belanja online Super-App) · Disusun oleh tim arsitektur (Athera)**

## Ringkasan
**Fase 3, belanja online, selesai dibangun dan lulus uji** di lingkungan pengembangan. Pelanggan memilih barang di Super-App, membayar sekali, lalu mengambil barang di toko dengan kode ambil. Reviewer keamanan menyatakan **aman untuk digabung ke kode utama**. Fitur ini **sengaja dimatikan di produksi** sampai 7 syarat aktivasi terpenuhi; yang terpenting adalah **pemilihan payment gateway** dan **server staging**. Fase 1 dan Fase 2 tidak berubah: siap pilot secara teknis dan menunggu staging serta keputusan klien.

## 1. Fase 3 belanja online: apa yang sudah ada
Semua bukti berasal dari lingkungan pengembangan karena staging belum ada.
- **Alur lengkap berjalan:** katalog → keranjang → harga final → bayar (QRIS **simulasi**; gateway sungguhan belum dipilih) → pesanan disiapkan toko → pelanggan mengambil dengan kode ambil.
- **Bila ada masalah:** pembayaran terlambat, stok habis setelah bayar, atau pesanan dibatalkan otomatis berujung **refund** ke pelanggan. Refund oleh CS **wajib disetujui orang kedua**.
- **Harga dan pajak selalu dihitung Odoo**, tidak pernah dari aplikasi pelanggan. Odoo tetap **tidak terbuka ke internet**.
- **Data pribadi minim:** Odoo tidak menerima nama/HP/alamat pembeli online. Kode ambil disimpan terenkripsi dan dihapus setelah pesanan selesai.
- **Kasir (POS) dilindungi:** online hanya boleh memakai sebagian kecil kapasitas server Odoo, sehingga lonjakan pesanan online tidak memperlambat kasir.
- **Hasil uji:** uji menyeluruh **267 skenario lulus, 0 gagal** (termasuk semua perbaikan keamanan).
- **Review keamanan:** 3 putaran. Semua temuan tingkat sedang sudah ditutup. Sisa 3 temuan ringan: satu sudah diperbaiki, satu sedang dikerjakan, dan satu ditangani lewat aturan akses (lihat §3, syarat no. 5). Hasil akhir: **GO merge**, online tetap mati di produksi.
- **Tombol darurat:** online bisa dimatikan seketika tanpa memengaruhi kasir.

## 2. Keputusan yang sudah diambil (10 Oktober 2026, pemilik proyek)
| Keputusan | Artinya | Biaya |
|---|---|---|
| **Kapasitas server pilot: opsi B** | Server Odoo tetap 6 "jalur kerja": 4 untuk kasir, 2 untuk online. Cukup untuk pilot bervolume rendah. Bila online ramai, pelanggan online bisa diminta mencoba lagi, tetapi kasir tetap lancar | Rp0 (tanpa tambah server) |
| **Bahasa Indonesia di layar Odoo belum diaktifkan** | Layar staf Odoo tetap berbahasa Inggris. Label pajak untuk pelanggan tetap Bahasa Indonesia. Mengaktifkan bahasa sekarang akan merusak label pajak yang sudah dibuat | Rp0; bila nanti diminta ±1–2 hari kerja |

Keputusan arsitektur online (ADR-034) **tetap menunggu tanda tangan** IT + Security + PO.

## 3. Syarat sebelum belanja online dinyalakan di produksi
| # | Syarat | Pemilik | Status |
|---|---|---|---|
| 1 | Uji otomatis Odoo lengkap (termasuk modul kasir–penjualan) lulus | Tim teknis | Menunggu hasil CI |
| 2 | Uji penerimaan: barang online tidak bisa keluar tanpa kode ambil; kasir tidak bisa menyelesaikan pesanan online | Tim teknis (QA) | Sebagian lulus; jalur kasir belum diuji |
| 3 | Kapasitas server diputuskan, lalu uji beban di staging dengan kasir aktif | Pemilik proyek (**selesai**), tim teknis | Keputusan **selesai**; uji menunggu **staging** |
| 4 | **Payment gateway sungguhan dipilih** dan dihubungkan (refund tidak boleh terkirim dua kali) | **Finance + Legal + IT klien** | **Belum dipilih** |
| 5 | Pemisahan tugas: **orang yang boleh membatalkan pesanan di Odoo (Sales Manager) tidak boleh sama dengan orang yang menyetujui refund**; review akses tiap bulan | **Superadmin + Finance** (aturan & prosedur sudah ditulis) | Prosedur **siap**; daftar orang & review pertama **belum** |
| 6 | Penguatan tambahan di gudang (perubahan tujuan barang ditolak) | Tim teknis | Sedang dikerjakan |
| 7 | Keputusan bisnis minimum pilot + persetujuan DPO atas masa simpan data online | **PO, Ops, Finance, Tax, Legal/DPO** | **Belum** (sementara memakai usulan tim) |

## 4. Pertanyaan klien yang masih terbuka untuk belanja online (Q-ON1..Q-ON13)
**Prioritas A (dibutuhkan sebelum pilot online):**
- **Q-ON1:** PT/toko pilot, jam ambil, jumlah barang online. *(PO + Ops)*
- **Q-ON4:** stok dipesan saat checkout atau cukup dicek; batas waktu bayar; bila stok habis setelah bayar → batal + refund penuh? *(PO + Ops + Finance)*
- **Q-ON6:** aturan pembulatan Rupiah. *(Finance)*
- **Q-ON8:** faktur dibuat saat bayar atau saat diambil. *(Finance + Tax)*
- **Q-ON9:** dana titipan: siapa menanggung biaya gateway, bukti potong, jadwal setoran Holding → PT. *(Finance + Tax + Legal)*

**Prioritas B:**
- **Q-ON2:** restoran online. *(PO + Ops F&B + IT)*
- **Q-ON3:** pesan-antar dan ongkir. *(PO + Ops + Finance)*
- **Q-ON5:** harga tampil termasuk pajak. *(Finance + Tax)*
- **Q-ON7:** faktur atas nama/NPWP pembeli. *(Finance + DPO)*
- **Q-ON10:** kupon/promo. *(PO + Marketing)*
- **Q-ON11:** aturan refund dan siapa yang menyetujui. *(Ops + Finance)*
- **Q-ON12:** katalog per PT atau per brand. *(PO + Ops)*
- **Q-ON13:** berapa lama pesanan yang tidak diambil ditahan. *(Ops + Finance)*

Jawaban sementara (default) tim tercantum di `docs/decisions/README.md`, bagian Fase 3.

**DPO / Legal:**
- 5 pertanyaan baru, **Q-P17..Q-P21**: masa simpan riwayat pesanan, penundaan hapus akun selama pesanan belum selesai, catatan CS, perjanjian data dengan payment gateway, dan data pembeli di faktur.
- Tidak ada kebocoran atau near-miss data pribadi dari Fase 3.

## 5. Fase 1 dan 2 (tidak berubah sejak 9 Okt)
- **Poin di kasir retail:** siap pilot. Menunggu staging, batas poin dari Finance (Q-13), daftar Superadmin, supervisor/petugas jaga, dan SOP retur.
- **Layar dapur F&B:** siap pilot. Menunggu staging, outlet dan stasiun dapur, perangkat, pelatihan kasir (catatan dapur tanpa nama/HP), dan SOP tablet hilang.

## 6. Risiko utama
| Risiko | Dampak bila terjadi | Penanganan |
|---|---|---|
| **Belum ada staging dan server produksi** | Ketiga pilot tidak bisa dimulai | Pengadaan server (ADR-004) |
| **Payment gateway belum dipilih** | Belanja online tidak bisa dinyalakan | Pilih vendor (ADR-016/026); KYB tiap PT memakan waktu, jadi mulai sekarang |
| **DPO belum ditunjuk**; masa simpan data belum ditetapkan | Kepatuhan UU PDP belum bisa dinyatakan | Keputusan Direksi |
| Satu orang memegang peran pembatal dan penyetuju refund | Refund fiktif tanpa empat mata | Aturan + review akses bulanan (sudah ditulis); kontrol otomatis ±1–2 hari bila diinginkan |
| Kunci akses online Odoo kedaluwarsa (90 hari) | Checkout online berhenti, kasir tidak terdampak | Alert sisa umur kunci + prosedur rotasi |
| **Pemegang kunci (Superadmin) mungkin hanya satu orang** | Backup tidak bisa dipulihkan bila orang itu tidak ada | Tunjuk minimal 2 orang |
| Online ramai melebihi kapasitas pilot (opsi B) | Pelanggan online menunggu atau diminta mencoba lagi | Pantau; naikkan ke opsi A (tambah jalur kerja, tanpa beli server) |

## 7. Yang dibutuhkan dari manajemen (urut prioritas)
1. **Server staging** (ADR-004). Ini penghambat utama ketiga pilot; staging siap ±1–2 minggu setelah server ada.
2. **Finance + Legal + IT:** pilih **payment gateway** (memblokir online).
3. **Finance / Tax:** Q-ON6, Q-ON8, Q-ON9, nilai & batas poin (Q-13), dan tarif PB1 kota pilot.
4. **PO + Ops:** Q-ON1 dan Q-ON4 (toko pilot online, aturan stok & bayar), outlet pilot retail dan F&B.
5. **IT klien / Superadmin:**
   - daftar Superadmin (minimal 2);
   - tunjuk **Sales Manager Odoo** dan **penyetuju refund** (orang berbeda);
   - jadwalkan review akses bulanan dengan saksi Finance;
   - pemegang rotasi kunci akses online.
6. **DPO / Legal:** tunjuk DPO; Q-P17..Q-P21 (online), Q-P13 (sebelum impor saldo), Q-P15 dan Q-P16.
7. **Tanda tangan ADR-034** (arsitektur kanal online) dan keputusan Fase 0 yang masih terbuka.

## 8. Biaya & waktu indikatif (belum termasuk server)
| Pos | Perkiraan |
|---|---|
| Lisensi perangkat lunak | **Rp0** (semua open source) |
| Belanja online (Fase 3) | Sudah dibangun; tanpa lisensi (toko online bawaan Odoo tidak dipakai) |
| Integrasi payment gateway sungguhan | Belum diestimasi (bergantung vendor) |
| Kontrol otomatis pemisahan peran refund (opsional) | ±1–2 hari |
| Faktur atas nama pembeli online (hanya bila Q-ON7 = ya) | ±1–2 minggu (perkiraan kasar, belum dirinci) |
| Modul Odoo buatan sendiri pengganti OCA | ±10–16 minggu-engineer (±Rp50–145 jt); tidak berubah |
| QR order restoran dari ponsel (hanya bila diwajibkan) | ±3–4 minggu-engineer (±Rp15–36 jt) |
| Konektivitas outlet (WireGuard) | Rp0 per outlet kecil; ±Rp0,4–0,9 jt per outlet besar |

## 9. Rekomendasi langkah berikutnya
1. Gabungkan PR Gelombang 17 ke kode utama (online tetap mati di produksi).
2. Paralel, karena prosesnya lama: **pilih payment gateway** dan mulai KYB tiap PT; tunjuk DPO.
3. Setelah server staging ada: pasang dan uji ulang ketiga fase (±1–2 minggu), termasuk uji beban online bersama kasir.
4. Nyalakan berurutan: **poin di 1 toko → layar dapur di 1 outlet F&B → belanja online di 1 toko**. Evaluasi 2 minggu di tiap langkah.

Daftar lengkap: `docs/decisions/README.md`, bagian "Keputusan pemilik proyek 10 Okt 2026", "Syarat aktivasi produksi online", "Syarat GO pilot loyalty", "Syarat GO pilot KDS", dan "Keputusan klien yang masih terbuka".

*Catatan jujur:*
- Angka biaya adalah perkiraan arsitek dan perlu divalidasi.
- Semua bukti "berjalan" berasal dari lingkungan pengembangan dengan pembayaran simulasi.
- Perbaikan terakhir (settle refund manual hanya oleh penyetuju, dan penguatan gudang) dibuat setelah review akhir dan belum dibaca ulang reviewer keamanan.
