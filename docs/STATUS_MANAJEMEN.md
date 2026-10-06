# Status Proyek EFN Platform — Ringkasan Satu Halaman untuk Manajemen

**Per 6 Oktober 2026 · Fase 0 (fondasi) · Disusun oleh tim arsitektur (Athera)**

## 1. Apa yang sudah terbukti berjalan
Semua di bawah ini sudah **dijalankan dan diuji di lingkungan pengembangan**, bukan hanya dirancang. Lingkungan uji bersama (staging) belum ada.
- **Alur bisnis inti jalan dari ujung ke ujung:** login pelanggan dengan kode OTP, laporan harga Price Hunter dengan foto, pemberian poin, kode OTP promo di kasir, pembayaran QRIS (simulasi), penghapusan akun pelanggan di semua sistem, dan laporan analitik. Uji otomatis terakhir: **108 dari 108 pemeriksaan lolos**.
- **Keamanan data:** semua lalu lintas antar-komponen terenkripsi dan sudah diuji bahwa koneksi tanpa enkripsi ditolak. Nomor HP pelanggan disimpan terenkripsi, dan uji otomatis memastikan **tidak ada nomor HP utuh di log** sistem.
- **Hak pelanggan (UU PDP):** pelanggan bisa mengunduh datanya dan menghapus akunnya. Setelah dihapus, data yang tersisa (misalnya catatan keuangan poin) **tidak bisa lagi dikaitkan** ke nomor HP.
- **Pengawasan perubahan di Odoo:** setiap perubahan harga, promo, dan data pelanggan tercatat siapa pelakunya. Upaya menghapus catatan itu memicu peringatan otomatis, dan salinannya disimpan 400 hari di luar jangkauan admin Odoo.
- **Penyimpanan foto & backup:** sudah pindah ke versi komunitas yang masih dirawat (SILO), dengan penyaring pengaman di depannya. Celah yang ditemukan reviewer keamanan sudah dibuktikan nyata, lalu ditutup dan diuji ulang.
- **Odoo hanya berisi modul standar dan modul buatan EFN**, sesuai keputusan klien. Tidak ada modul pihak ketiga.

## 2. Risiko utama yang tersisa
| Risiko | Dampak bila terjadi | Penanganan |
|---|---|---|
| **DPO belum ditunjuk** dan masa simpan data belum ditetapkan | Kepatuhan UU PDP belum bisa dinyatakan; keputusan lapor kebocoran tidak punya pemilik | Keputusan Direksi (lihat §3) |
| **Pemegang kunci (Superadmin) mungkin hanya satu orang** | Bila orang itu tidak ada, backup tidak bisa dipulihkan dan kunci tidak bisa diganti, sehingga data bisa hilang permanen | Tunjuk minimal 2 orang dan simpan salinan kunci tersegel |
| **Belum ada staging dan server produksi** | Semua bukti baru berasal dari laptop pengembang; enkripsi disk dan deploy otomatis belum diuji di server sungguhan | Pengadaan server (ADR-004) |
| Penyimpanan foto/backup bergantung pada proyek komunitas dengan satu pengelola utama | Perbaikan keamanan bisa terlambat | Tinjauan kuartalan + jalur pindah ke SeaweedFS sudah disiapkan |
| Modul yang dulu diambil dari OCA kini harus dibangun sendiri | Fase 1 bisa mundur 2–4 minggu | Keputusan anggaran (lihat §3) |
| Mitra pembayaran belum dipilih; ada satu celah pembayaran yang harus ditutup sebelum vendor diaktifkan | Belum bisa menerima pembayaran online nyata | Pilih vendor sebelum Fase 3 |

## 3. Keputusan yang dibutuhkan dari klien (urut prioritas)
1. **Tunjuk DPO** (petugas pelindungan data pribadi). Tanpa DPO, semua masa simpan data tetap berupa usulan teknis.
2. **Tetapkan jumlah Superadmin (rekomendasi ≥ 2 orang)** dan lokasi penyimpanan salinan kunci.
3. **Setujui penyimpanan berbasis SILO** dan mode kunci backup yang lebih ketat. Keduanya memblokir staging publik.
4. **Setujui anggaran modul buatan sendiri** (±Rp50–145 juta) dengan salah satu pilihan: jadwal Fase 1 mundur 2–4 minggu, **atau** menambah 1 engineer Odoo kontrak selama 2 bulan.
5. **Tanda tangani 7 keputusan Gelombang 1** (satu database Odoo multi-PT, versi Odoo 19, kitting, OTP saat offline, server, jadwal, POS F&B). Ketujuhnya memblokir Fase 1.
6. **Kebijakan kasir:** apakah kasir boleh mendaftarkan pelanggan baru di POS?
7. Untuk DPO, setelah ditunjuk: masa simpan log audit, siapa yang boleh melihat data pribadi lintas PT, dan perlakuan akun tidak aktif atau nomor HP yang didaur ulang operator.

Daftar lengkap dan rinci: `docs/decisions/README.md`, bagian "Keputusan klien yang masih terbuka".

## 4. Biaya & waktu indikatif (belum termasuk server)
| Pos | Perkiraan |
|---|---|
| Lisensi perangkat lunak | **Rp0** (semua open source) |
| Modul Odoo buatan sendiri pengganti OCA | ±10–16 minggu-engineer (±Rp50–145 jt) |
| Uji coba jalur pindah storage (SeaweedFS) | 5 hari kerja |
| Penutupan sisa temuan keamanan sebelum staging publik | ±1–2 minggu tim (sebagian besar sudah selesai di kode, tinggal pemasangan di server) |
| Konektivitas outlet (WireGuard) | Rp0 per outlet kecil; ±Rp0,4–0,9 jt per outlet besar (router) |
| Brankas/notaris untuk salinan kunci | Di luar platform, bergantung pilihan klien |

**Langkah berikutnya:** begitu server tersedia, siapkan staging dalam ±1–2 minggu, ulangi semua uji di sana, lalu uji coba di satu outlet pilot.

*Catatan jujur: angka biaya adalah perkiraan arsitek dan perlu divalidasi. Semua bukti "berjalan" berasal dari lingkungan pengembangan.*
