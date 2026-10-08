# Status Proyek EFN Platform — Ringkasan Satu Halaman untuk Manajemen

**Per 9 Oktober 2026 · Fase 1 (pilot kasir retail + poin EFN) dan Fase 2 (pilot restoran/F&B + layar dapur) · Disusun oleh tim arsitektur (Athera)**

## Ringkasan
Fase 1 (poin EFN di kasir retail) sudah siap pilot secara teknis, tetapi masih menunggu **server staging** dan **batas poin dari Finance**. Fase 2 sudah berjalan: **layar dapur (KDS)** selesai dibangun dan diuji di lingkungan pengembangan. Reviewer keamanan menyatakan **GO bersyarat** untuk satu outlet F&B pilot dengan 8 syarat. Perbaikan teknisnya sudah dikerjakan; sisanya menunggu staging dan keputusan klien (outlet pilot, stasiun dapur, perangkat, dan apakah pelanggan boleh memesan dari ponsel).

## 1. Apa yang sudah terbukti berjalan (lingkungan pengembangan)
Staging belum ada. Semua bukti di bawah berasal dari lingkungan pengembangan.
- **Fase 1, poin EFN di kasir:** dapat poin saat belanja, tukar poin dengan OTP, poin ditarik saat retur, pengaman kecurangan, dan pencocokan otomatis harian antara poin dan transaksi kasir. Peringatan dikirim bila ada kejanggalan. Tidak ada perubahan sejak 7 Okt selain penambahan pencocokan harian.
- **Fase 2, layar dapur (baru):**
  - pesanan dari kasir otomatis muncul di layar stasiun yang tepat (dapur panas, bar, dan seterusnya);
  - juru masak menandai "dimasak / siap / disajikan", lalu kasir mendapat tanda "siap";
  - **printer dapur tetap mencetak** sebagai cadangan, sehingga bila layar atau internet mati, dapur tetap menerima pesanan;
  - bila tiket gagal dibuat, kasir mendapat peringatan dan printer mencetak ulang;
  - layar dapur **tidak menampilkan nama, HP, atau harga**, dan akun tablet dapur hanya bisa melihat tiket stasiunnya;
  - uji otomatis: **337 uji unit dan 20 skenario layar lolos**.
- **Fitur restoran standar Odoo** (meja, split bill, gabung/pindah meja, course, modifier, menu paket, takeaway, PB1 per kota) dipakai lewat konfigurasi, tanpa kode tambahan.
- **Keamanan & UU PDP:**
  - penyaringan nomor HP dan token di log diperbaiki dan kini **diuji otomatis setiap perubahan kode**;
  - pesan dari ponsel lewat fitur bawaan Odoo **dikunci** karena akan membuka Odoo ke internet;
  - daftar data pribadi diperbarui ke **versi 5** dan mencakup layar dapur.
- **Odoo hanya berisi modul standar dan modul buatan EFN** (tanpa modul pihak ketiga).

## 2. Status GO pilot
**Pilot poin (retail), 7 syarat:** syarat no. 5 (daftar data pribadi) dan no. 6 (penguatan uji otomatis) selesai. Yang masih menunggu: staging, batas poin dari Finance, daftar nama Superadmin, penunjukan supervisor/petugas jaga, dan SOP retur.

**Pilot layar dapur (F&B), 8 syarat:**
| # | Syarat | Pihak | Status |
|---|---|---|---|
| 1 | Printer dapur tetap mencetak di semua stasiun pilot | Operasional F&B | Siap (pengaturan) |
| 2 | Pesan dari ponsel lewat Odoo dikunci | Tim teknis | **Selesai** (kunci teknis); cek ulang di outlet pilot |
| 3 | Akun tablet dapur: satu per layar, 2FA, jaringan privat | Tim teknis + **IT klien** | Kode selesai; pemasangan menunggu perangkat |
| 4 | Uji ulang di **staging** | Tim teknis, butuh server | **Belum ada server** |
| 5 | SOP ganti/hilang tablet | **Operasional + IT klien** | Rotasi kunci otomatis selesai; SOP belum |
| 6 | Pelatihan kasir: jangan tulis nama/HP di catatan dapur | **Operasional F&B** | Menunggu |
| 7 | Penyaringan log diuji sebelum staging menerima data | Tim teknis | **Selesai** |
| 8 | Daftar data pribadi v5, keputusan stasiun dapur & perangkat | Tim arsitektur + **Operasional F&B** | Daftar data **selesai**; keputusan stasiun/perangkat menunggu |

Bila layar dapur bermasalah, stasiun dikembalikan ke **printer saja** (cara kerja standar Odoo). Kasir tetap berjalan.

## 3. Risiko utama yang tersisa
| Risiko | Dampak bila terjadi | Penanganan |
|---|---|---|
| **Belum ada staging dan server produksi** | Kedua pilot tidak bisa dimulai | Pengadaan server (ADR-004) |
| **DPO belum ditunjuk**; masa simpan data belum ditetapkan | Kepatuhan UU PDP belum bisa dinyatakan | Keputusan Direksi |
| **Pemegang kunci (Superadmin) mungkin hanya satu orang** | Backup tidak bisa dipulihkan bila orang itu tidak ada | Tunjuk minimal 2 orang |
| Pesan dari ponsel di meja belum tersedia | Kebutuhan "scan QR di meja" belum terpenuhi di pilot | Pilot memakai **kiosk**; jalur ponsel lewat Super-App ±3–4 minggu kerja bila diwajibkan (ADR-032) |
| Kasir menulis nama/kondisi pelanggan di catatan dapur | Data pribadi tersimpan di tiket dapur hingga 90 hari | Pelatihan + masa simpan pendek; penyaringan otomatis ±0,5–1 hari bila diperlukan |
| Nilai & batas poin masih angka sementara | Liabilitas poin belum final | Keputusan Finance (Q-13) |
| Nomor pelanggan sistem lama tersimpan permanen bila dipakai saat impor saldo | Pelanggan terhapus masih bisa dikenali | Putuskan sebelum impor (Q-P13) |
| Near-miss log (nomor HP **fiktif** data uji masuk log server pengembangan, sudah diperbaiki) | Tidak ada dampak ke pelanggan nyata; tidak wajib dilaporkan | Uji otomatis baru; DPO mengonfirmasi klasifikasi (Q-P16) |

## 4. Yang dibutuhkan dari manajemen (urut prioritas)
1. **Server staging** (ADR-004). Ini penghambat utama kedua pilot. Setelah server ada, staging siap dalam ±1–2 minggu.
2. **Finance:** nilai & batas poin (Q-13). **Finance + konsultan pajak:** tarif PB1 kota pilot dan service charge (P-16).
3. **Pilih outlet pilot:** toko retail (P-1) dan outlet F&B (P-11). Sertakan jumlah meja, kasir, layar dapur, dan kiosk.
4. **Operasional F&B:**
   - daftar stasiun dapur dan target waktu masak (P-13);
   - model printer dapur dan tablet/monitor dapur (P-18);
   - SOP tablet hilang/diganti dan pelatihan catatan dapur.
5. **PO + Operasional:** QR order cukup lewat **kiosk** di pilot, atau wajib dari **ponsel** pelanggan (Q-23, ADR-032)?
6. **IT klien:**
   - daftar Superadmin (minimal 2);
   - persetujuan model akun tablet dapur (ADR-033);
   - siapa pemegang 2FA tablet.
7. **DPO / Legal:** tunjuk DPO; Q-P13 (sebelum impor saldo); masa simpan tiket dapur (Q-P15); konfirmasi near-miss (Q-P16).
8. Keputusan Fase 0 yang masih terbuka (Gelombang 1, SILO, anggaran modul buatan sendiri) tetap berlaku.

Daftar lengkap: `docs/decisions/README.md`, bagian "Syarat GO pilot loyalty", "Syarat GO pilot KDS", dan "Keputusan klien yang masih terbuka".

## 5. Biaya & waktu indikatif (belum termasuk server)
| Pos | Perkiraan |
|---|---|
| Lisensi perangkat lunak | **Rp0** (semua open source) |
| Modul Odoo buatan sendiri pengganti OCA | ±10–16 minggu-engineer (±Rp50–145 jt); tidak berubah |
| Layar dapur (KDS) | Sudah dibangun; tanpa biaya lisensi (padanan Enterprise tidak dipakai) |
| QR order dari ponsel lewat Super-App (hanya bila diwajibkan, ADR-032) | ±3–4 minggu-engineer (±Rp15–36 jt) |
| Akun tablet dapur bertipe portal (opsional, sebelum outlet ke-2, ADR-033) | ±3–5 hari |
| Perangkat outlet F&B (tablet dapur, kiosk, printer) | Bergantung P-18; harga belum divalidasi |
| Perbaikan keamanan selama pilot (poin dan dapur) | ±1–2 minggu tim |
| Konektivitas outlet (WireGuard) | Rp0 per outlet kecil; ±Rp0,4–0,9 jt per outlet besar |

**Langkah berikutnya:** server staging → pasang dan uji ulang (±1–2 minggu) → penuhi syarat GO → nyalakan poin di satu toko dan layar dapur di satu outlet F&B → evaluasi 2 minggu sebelum outlet lain.

*Catatan jujur: angka biaya adalah perkiraan arsitek dan perlu divalidasi. Semua bukti "berjalan" berasal dari lingkungan pengembangan. Status perbaikan Gelombang 15 berasal dari laporan tim teknis dan belum diperiksa ulang oleh reviewer keamanan.*
