# Status Proyek EFN Platform — Ringkasan Satu Halaman untuk Manajemen

**Per 10 Oktober 2026 (sore) · Fase 1 (poin di kasir ritel), Fase 2 (restoran + layar dapur), Fase 3 (belanja online Super-App) · Disusun oleh tim arsitektur (Athera)**

## Ringkasan
**Pemilik proyek sudah menjawab semua pertanyaan terbuka (60 butir) pada 10 Oktober 2026.** Delapan keputusan arsitektur kini **disetujui** (termasuk QR order dari ponsel). Sebagian besar jawaban sesuai dengan yang sudah dibangun, tetapi sekitar 25 jawaban **menambah atau mengubah pekerjaan**, sehingga tim menyusun **rencana 28 paket kerja** (`docs/10_RENCANA_GELOMBANG_18_PLUS.md`, ±52–79 minggu-engineer, sebagian paralel). Usulan urutan: **pilot ritel Grand Lucky lebih dulu**, lalu restoran (Paris Baguette, kafe Bacha), lalu pesan-antar dan portal vendor. Fase 1–3 tetap **siap secara teknis** di lingkungan pengembangan; penghambat utama tetap **server staging**, **pilihan payment gateway**, dan **opini konsultan pajak**.

## 1. Keputusan pemilik proyek 10 Oktober 2026 (sesi tanya-jawab)
| Bidang | Keputusan | Akibatnya |
|---|---|---|
| Persetujuan arsitektur | 7 keputusan dasar (satu database Odoo, versi Odoo 19, kitting dapur pusat, poin saat kasir offline, 4 server, jadwal ±12 bulan, lingkup kasir restoran) **disetujui** | Fase 1 tidak lagi menunggu tanda tangan |
| Brand & perusahaan | Grand Lucky = PT Retail; Paris Baguette = PT F&B; **Bacha Coffee = dua PT** (butik di PT Retail, kafe di PT F&B, kasir terpisah). Dapur pusat bagian dari PT F&B | Katalog & harga online **per brand** |
| Poin | Penukaran poin = **alat bayar** (seperti uang), di kasir **dan** online; poin juga **didapat** dari belanja online | **Perubahan desain** poin di kasir (±3–4 minggu) — harus selesai sebelum pilot ritel |
| Pembulatan Rupiah | Semua nominal dibulatkan ke **Rp1** di seluruh sistem | Dikerjakan **sekarang**, sebelum ada data nyata (±1,5–2,5 minggu + uji ulang semua) |
| Belanja online | Stok **dipesan 1 jam** saat checkout; promo online lengkap (kupon, beli X gratis Y); restoran online **bersamaan** dengan pilot ritel; **pesan-antar (3PL + kurir sendiri)**; faktur atas nama/NPWP pembeli **bila diminta** | Beberapa paket kerja baru (lihat rencana) |
| Restoran | QR order **dari ponsel wajib** (tamu, login opsional untuk poin; bayar di kasir sekarang, online setelah gateway dipilih); layar dapur = monitor + mini PC; stasiun Roti/Pastry + Kopi; PB1 10 % + service charge 5 % untuk makan di tempat | QR ponsel ±3–4 minggu sebelum pilot restoran. **Pajak untuk bawa pulang perlu konsultan pajak** (lihat risiko) |
| Keamanan | Pemegang kunci tetap **1 Superadmin** + salinan tersegel di **brankas Direksi**; disk server terenkripsi wajib; **data pribadi di Odoo dienkripsi**; kunci akses online diganti **otomatis tiap 30 hari** | Risiko "satu orang" **diterima** dan dicatat; enkripsi Odoo = pekerjaan baru (±3–5 minggu) |
| Portal | Akan ada akses untuk **pelanggan** (faktur/riwayat), **vendor** (PO & tagihan), **franchisee** (laporan), **karyawan luar kantor** | Keputusan arsitektur baru **ADR-035** (menunggu tanda tangan): hanya portal vendor yang dibuka ke internet, dengan pengaman berlapis |
| UU PDP | **Legal = DPO sementara**; semua masa simpan data **ditetapkan**; member tidak aktif 24 bulan dianonimkan; hapus akun boleh ditunda maks 14 hari; DPIA wajib sebelum go-live publik | Inventaris data pribadi **v7** |

Keputusan sebelumnya hari ini (kapasitas server pilot opsi B; bahasa Indonesia di layar Odoo belum diaktifkan) tetap berlaku.

## 2. Rencana kerja berikutnya (usulan, menunggu persetujuan lead/PO)
| Gelombang | Isi | Membuka |
|---|---|---|
| **G18** (±3–4 minggu) | Pembulatan Rp1, poin sebagai alat bayar, masa simpan data baru, rotasi kunci otomatis, promo & refund kasir, migrasi member; analisis fungsional semua paket Odoo | Pilot ritel (poin di kasir) |
| **G19** (±3–4 minggu) | Stok dipesan 1 jam, promo online, katalog per brand, enkripsi data pribadi di Odoo, pajak restoran | Pilot online ritel |
| **G20** (±4–5 minggu) | QR ponsel restoran, restoran online, poin online, faktur atas nama pembeli | Pilot restoran + restoran online |
| **G21+** | Pesan-antar (setelah 3PL dipilih), portal vendor, transaksi antar-PT + harga transfer, laporan keuangan per PT, anonimisasi member tidak aktif | Produksi penuh |

Restoran online "bersamaan pilot ritel" hanya realistis bila konsultan pajak dan outlet restoran siap tepat waktu; bila tidak, menyusul paling lambat satu gelombang.

## 3. Syarat sebelum belanja online dinyalakan di produksi (diperbarui)
| # | Syarat | Pemilik | Status |
|---|---|---|---|
| 1 | Uji otomatis Odoo lengkap lulus | Tim teknis | Menunggu CI |
| 2 | Uji penerimaan jalur kasir untuk pesanan online | Tim teknis (QA) | Sebagian |
| 3 | Uji beban di staging dengan kasir aktif | Tim teknis | Menunggu **staging** |
| 4 | **Payment gateway dipilih** (kini wajib: perjanjian data + pemrosesan di Indonesia) | **Finance + Legal + IT klien** | **Belum dipilih** |
| 5 | Nama Sales Manager Odoo & penyetuju refund (orang berbeda) + review akses bulanan | **Superadmin + Finance** | Prosedur siap; **nama belum** |
| 6 | Penguatan gudang | Tim teknis | Dikerjakan |
| 7 | Keputusan bisnis minimum | Pemilik proyek | **Dijawab 10 Okt** — tetapi stok 1 jam & pembulatan Rp1 harus **dibangun dulu** (G18–G19), plus rotasi kunci 30 hari dan batas penundaan hapus akun 14 hari |

## 4. Risiko utama (diperbarui)
| Risiko | Dampak bila terjadi | Penanganan |
|---|---|---|
| **Belum ada staging/produksi** | Ketiga pilot tidak bisa dimulai | Pengadaan server (disk terenkripsi wajib) |
| **Payment gateway belum dipilih** | Online & bayar QR di meja tidak bisa dinyalakan | Pilih vendor (kriteria: perjanjian data, Indonesia) |
| **Pajak restoran untuk bawa pulang** (baru) | Bila Perda tetap mengenakan PB1 untuk bawa pulang: kurang setor + denda | Konsultan pajak per kota **sebelum** go-live restoran; pengaturan bisa diubah tanpa program |
| **Hanya 1 pemegang kunci** (diterima pemilik proyek) | Bila orang itu tidak ada saat darurat, pemulihan tertunda | Salinan tersegel di brankas Direksi, prosedur darurat, uji pemulihan 2×/tahun disaksikan |
| **Data pembeli di Odoo** (baru) | Data pribadi lebih banyak tersimpan | Hanya bila pembeli meminta faktur; dienkripsi; ditinjau Legal |
| **Pembulatan Rp1** (baru) | Selisih Rupiah di pajak/laporan bila tidak diuji menyeluruh | Dikerjakan sebelum ada data nyata + uji ulang semua |
| **Portal vendor** (baru) | Pintu masuk baru dari internet | Gerbang khusus, verifikasi 2 langkah, firewall aplikasi, uji penetrasi sebelum dibuka; sementara vendor lewat email |
| Jumlah pekerjaan bertambah | Jadwal mundur | Urutan bertahap per pilot; engineer Odoo kedua sangat disarankan |

## 5. Yang masih dibutuhkan dari klien
1. **Server staging** (penghambat utama).
2. **Payment gateway** dan **vendor 3PL** (Finance + Legal + IT).
3. **Konsultan pajak:** poin sebagai alat bayar, **PB1 untuk bawa pulang**, tarif PB1 kota outlet, harga transfer antar-PT, struktur dana titipan Holding.
4. **Data outlet:** alamat, jam buka, kasir, jumlah barang untuk Grand Lucky, Paris Baguette, Bacha (butik & kafe).
5. **Model perangkat:** PC kasir, mini PC dapur, printer, scanner, timbangan, router/4G, kiosk.
6. **Daftar promo** (Marketing) dan **daftar modifier menu** (Ops F&B).
7. **Nama:** Superadmin, Sales Manager Odoo, penyetuju refund, anggota auditor lintas PT (Legal + Internal Audit), auditor eksternal.
8. **Finance:** nilai & batas poin final, markup harga transfer, aturan poin hangus saat akun dianonimkan.
9. **Tanda tangan** ADR-035 (portal) dan ADR-034 (kanal online).

## 6. Biaya & waktu indikatif (belum termasuk server)
| Pos | Perkiraan |
|---|---|
| Lisensi perangkat lunak | **Rp0** (semua open source) |
| 28 paket kerja baru dari keputusan 10 Okt | **±52–79 minggu-engineer** (±Rp0,26–0,71 M, tarif asumsi); ±7–10 minggu di antaranya sudah dianggarkan sebelumnya |
| — terbesar: pesan-antar | ±6–10 minggu (bergantung vendor 3PL) |
| — portal (empat kelompok) | ±5–6,5 minggu |
| — restoran online / QR ponsel | ±4–5 / ±3–4 minggu |
| — enkripsi data pribadi di Odoo | ±3–5 minggu |
| Uji penetrasi portal vendor (pihak ketiga) | Belum diestimasi |

Rincian per paket, agent penanggung jawab, prasyarat, dan alasan urutan: `docs/10_RENCANA_GELOMBANG_18_PLUS.md`. Daftar keputusan lengkap: `docs/decisions/README.md`, bagian "Keputusan pemilik proyek 10 Okt 2026 (sesi tanya-jawab)".

*Catatan jujur:*
- Estimasi adalah perkiraan arsitek sebelum analisis fungsional rinci; akan divalidasi di G18.
- Dua jawaban ditafsirkan tim dan perlu konfirmasi: masa simpan data analitik (10 tahun, identitas samaran dihapus setelah 5 tahun) dan "laporan keuangan resmi dari Odoo" (modul laporan baru ±4–6 minggu).
- Semua bukti "berjalan" masih dari lingkungan pengembangan dengan pembayaran simulasi.
