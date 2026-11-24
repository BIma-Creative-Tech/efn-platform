# UAT G66 — Production Order (rencana produksi Central Kitchen di atas ESB)

Untuk: **Planner CK** dan **Kepala gudang**. Kontrak: `docs/04_SERVICE_CONTRACTS.md` §30 dan **§30.17 (login per pengguna ESB, 25 Nov 2026,
menang bila berbeda)**; keputusan ADR-051 + amandemen A1 (Proposed); runbook `docs/runbooks/prodplan.md`. Format mengikuti `docs/qa/G51_UAT.md`
(kotak centang; Given/When/Then).

> **Perubahan 25 Nov 2026 (G66b).** Modul Produksi tidak lagi memakai login Odoo/grup Insight: pengguna masuk di halaman `/produksi/masuk`
> dengan **username + password ESB milik sendiri**; sesi maksimal 1 jam; peran dan cabang diambil dari izin ESB orang itu. Bagian 0, 1, 7 dan
> 10 di bawah sudah disesuaikan; **bagian 12 baru** menguji login. Skenario bertanda **[AKUN-2]** butuh **dua akun ESB staging non-admin berbeda**
> (Q-PP17; sampai tersedia, jalankan dengan satu akun admin dan catat "tertahan"). Teks "Planner A / Kepala gudang B / Orang C" kini berarti
> **akun ESB** A, B, C, bukan akun Odoo.
**Semua langkah dijalankan terhadap ESB STAGING** (banner kuning/merah "ESB STAGING" harus tampil di setiap halaman
Produksi). Skenario ini **belum pernah dijalankan di browser** (QA hanya membaca kode); catat setiap selisih di kolom Hasil.

Angka contoh memakai data uji di dokumen kontrak (susu segar `RMT000021`, satuan ML, 1 kemasan = 1.000 ML). Ganti nama
produk/ID dengan data staging yang sebenarnya, tetapi **hitung ulang angka yang diharapkan** dengan rumus yang sama.

## 0. Prasyarat (disiapkan administrator, bukan penguji)

- [ ] `INSIGHT_PRODPLAN_ENABLED=true`, `PRODPLAN_ENABLED=true`, `PRODPLAN_WRITE_ENABLED=true` (hanya untuk bagian 8-9; bagian 1-7
      cukup `false`), `esb_env = staging`. Stack prodplan + Insight berjalan.
- [ ] `PRODPLAN_ESB_COMPANY_CODES` terisi dengan `companyCode` ESB staging; `PRODPLAN_ODOO_PROVISION_ENABLED=true` + overlay `prodplan-odoo`
      hanya untuk skenario 12.12 (tumpukan lokal dev/stg, **bukan** production).
- [ ] Akun ESB staging, masing-masing satu orang berbeda (**[AKUN-2]** bila belum ada selain admin):
      **Planner A** (hak ESB: Simple Manufacturing tambah, BOM/Stock List/Purchase Request baca), **Kepala gudang B** (hak ESB: Simple Transfer +
      Goods Transfer Request tambah, Stock List), **Orang C** yang memegang **kedua** set hak (akun admin staging memenuhi ini).
      Satu akun **D** tanpa hak modul tersebut, dan satu akun **E** yang hanya memegang cabang di luar Central Kitchen.
- [ ] Akun staging ESB: ada BOM Assembly untuk dua produk jadi (sebut **FIN-A** 45 ml susu/pcs, **FIN-B** 30 ml susu/pcs), BOM bertingkat
      (**FIN-C** memakai WIP **WIP-D**), dan setidaknya satu produk **tanpa** BOM Assembly. Stok susu di lokasi produksi = 700 ML;
      lokasi sumber satu cabang sama punya >= 1.000 ML, cabang lain punya >= 1.000 ML.
- [ ] Satu admin ESB staging siap meng-authorize transfer sederhana di ESB (bagian 8) dan memeriksa dokumen yang terbentuk.

## 1. Akses dan peran

- [ ] **Planner A masuk.** Given halaman `/produksi/masuk` (tanpa login Odoo); When A mengisi username + password ESB dan **Masuk**; Then diarahkan ke
      `/produksi`: terlihat "Rencana produksi", tombol **Buat rencana** dan **Kemasan utuh**; bilah atas menampilkan nama lengkap A (dari ESB), peran
      Planner CK, banner ESB STAGING, sisa waktu sesi (<= 60 menit) dan tombol **Keluar**; **tidak ada** tautan ke halaman Insight lain.
- [ ] **Kepala gudang B masuk.** Given login sebagai B; When membuka Produksi; Then daftar rencana tampil **tanpa** tombol "Buat rencana";
      halaman Kemasan utuh hanya baca (tidak ada "Tambah aturan kemasan"). **[AKUN-2]**
- [ ] **Negatif: pengguna tanpa izin ESB.** Given akun D (tanpa hak modul); When login; Then kembali ke halaman masuk dengan pesan
      "Akun ESB ini tidak memiliki izin modul Produksi."; tidak ada sesi (membuka `/produksi` langsung -> halaman masuk).
- [ ] **Negatif: flag mati.** Given `INSIGHT_PRODPLAN_ENABLED=false`; When membuka `/produksi` atau `/produksi/masuk`; Then halaman "belum aktif", bukan galat teknis.

## 2. Aturan kemasan utuh (Planner)

- [ ] **Tambah aturan.** Given A di Produksi > Kemasan utuh; When **Tambah aturan kemasan**, isi Kode `RMT000021`, Satuan `ML`, Isi `1000`,
      Umur setelah dibuka `48`; **Simpan**; Then pesan "Aturan kemasan ditambahkan.", baris muncul: "1.000", "48 jam (2 hari)", Aktif.
- [ ] **Ubah dengan versi.** When klik **Ubah RMT000021**, ganti isi jadi `1000` -> `500` lalu simpan; Then tersimpan. Buka halaman yang sama di dua tab,
      ubah di tab 1, simpan; lalu di tab 2 (belum dimuat ulang) ubah dan simpan; Then tab 2 menolak: "Rencana sudah diubah oleh orang lain.
      Muat ulang..." (galat versi). Kembalikan isi ke `1000`.
- [ ] **Nonaktifkan.** When klik **Nonaktifkan**; Then status "Nonaktif" dan hitungan berikutnya tidak lagi membulatkan ke kemasan. Aktifkan lagi.
- [ ] **Negatif.** Isi kemasan `0` / `abc` / kosong -> ditolak dengan pesan isian tidak valid; umur > 720 jam -> ditolak.

## 3. Buat rencana dan permintaan produksi (Planner)

- [ ] **Buat rencana.** Given A; When **Buat rencana**, pilih cabang Central Kitchen, Tanggal produksi = besok, **Buat rencana**; Then pindah ke halaman
      rencana bernomor `PP-<PT>-<tanggal>-001`, status **Draf**, hasil hitung **Belum dihitung**.
- [ ] **Negatif: tanggal.** Tanggal produksi > 60 hari ke depan atau > 30 hari lalu -> ditolak dengan pesan isian tidak valid.
- [ ] **Tambah manual.** Tab **Permintaan** > **Tambah baris**; ketik minimal 2 huruf nama BOM FIN-A; pilih dari daftar; Jumlah `30`; ulangi untuk FIN-B `20`;
      **Simpan permintaan**; Then "Permintaan disimpan", hasil hitung berubah **Perlu hitung ulang**.
- [ ] **Negatif: BOM.** Ketik 1 huruf -> tidak mencari; ketik nama produk tanpa BOM Assembly -> daftar kosong, tidak bisa dipilih.
- [ ] **Negatif: baris ganda / jumlah salah.** Dua baris FIN-A sumber sama -> ditolak ("produk tidak boleh ganda"); jumlah `0`, `-1`, `abc` -> ditolak.
- [ ] **Impor dari PR.** **Impor dari PR**, isi rentang tanggal dibutuhkan (<= 31 hari), kirim; Then muncul "Berjalan: Sedang diproses", beberapa detik kemudian
      "Proses selesai" dan halaman menyegarkan diri. Baris berumber "Impor PR" muncul dengan nomor PR.
- [ ] **PR yang tidak bisa dihitung.** Produk PR tanpa BOM Assembly muncul di tabel "Baris PR yang tidak bisa dihitung" dengan alasan "Tidak punya BOM Assembly"
      (tidak masuk perhitungan). Impor ulang menggantikan baris impor lama, tidak menggandakan.

## 4. Hitung kebutuhan, kemasan utuh (Planner)

- [ ] **Fresh milk kemasan utuh.** Given permintaan FIN-A 30 + FIN-B 20, aturan susu aktif, stok susu lokasi produksi 700; When **Hitung kebutuhan**; Then proses selesai,
      tab **Kebutuhan**: susu **Kebutuhan 1.950 ML**, **2 kemasan x 1.000 ML**, **Sisa terbuka 50 ML**, **Kebutuhan efektif 2.000 ML**, **Stok 700**,
      **Kekurangan 1.300**. Baris produksi: FIN-A 30, FIN-B 20. Daftar "Keterbatasan perhitungan" tampil (L-253..L-257).
- [ ] **Kebutuhan tepat kelipatan kemasan.** When ubah jumlah sehingga susu = 2.000 ML tepat (mis. FIN-A 100 pcs x 20 ml bila BOM mengizinkan, atau atur FIN-A 40 + FIN-B 7,
      sesuai angka BOM Anda); hitung ulang; Then **2 kemasan, sisa 0**, kebutuhan efektif = kebutuhan, **tidak ada usulan batch** untuk susu.
- [ ] **Jumlah desimal.** Permintaan `0,5` (ketik koma atau titik); hitung; Then kebutuhan susu `22,5 ML`, 1 kemasan, sisa `977,5 ML`.
- [ ] **BOM bertingkat.** Permintaan FIN-C 10 pcs; hitung; Then ada **dua** baris produksi: WIP-D (level 2, "Turunan") di atas FIN-C (level 1, "Permintaan");
      urutan posting WIP dulu; bahan baku dihitung dari kedua level.
- [ ] **Negatif: hitung tanpa permintaan.** Rencana kosong; **Hitung kebutuhan**; Then selesai tanpa baris. **Ajukan untuk persetujuan** tetap ditolak
      (catat teks pesan: seharusnya jelas bahwa tidak ada baris produksi).
- [ ] **Negatif: stok ESB tidak ada / negatif.** Bahan tanpa data stok -> catatan "Tidak ada data stok (dianggap 0)"; stok negatif -> "Stok negatif di ESB (dianggap 0)".
- [ ] **Negatif: satuan BOM beda dengan master produk.** Catatan "Satuan BOM berbeda... (dikeluarkan dari kemasan dan transfer)"; tidak ada usulan transfer untuk bahan itu.
- [ ] **Negatif: BOM siklus / terlalu dalam (data uji khusus, opsional).** Hitung gagal dengan pesan "Resep (BOM) memuat siklus" / "terlalu bertingkat"; status hasil hitung **Hitung gagal**.
- [ ] **Negatif: ubah saat hitung berjalan.** Mulai hitung, segera ubah permintaan di tab lain; Then hitung gagal/ditolak aman (tidak menimpa data baru), hasil hitung tidak "Terbaru".

## 5. Usulan batch tambahan (Planner)

- [ ] **Usulan muncul.** Given hitung susu dengan sisa 50 ML; When tab **Usulan batch**; Then dua usulan terbuka: FIN-A **+1 unit** (pakai 45, sisa 50) dan FIN-B **+1 unit** (pakai 30, sisa 50);
      kolom "Produk tambahan" dan "Memanfaatkan bahan" menampilkan **nama** produk (bukan "Produk #id").
- [ ] **Terima satu usulan.** **Terima** FIN-A; Then FIN-A diterima, usulan FIN-B menjadi **Gugur (usulan lain diterima)**, hasil hitung **Perlu hitung ulang**; di tab Permintaan baris
      "Usulan batch (diterima)" FIN-A +1 tampil dan tidak bisa diedit; **Hitung ulang** -> kebutuhan susu naik 45.
- [ ] **Tolak usulan.** Rencana lain: **Tolak** salah satu usulan; Then berstatus Ditolak. (Catat: setelah **Hitung ulang** usulan yang ditolak bisa muncul kembali; ini perilaku sekarang.)
- [ ] **Negatif: usulan basi.** Ubah permintaan (hasil hitung kedaluwarsa); buka tab Usulan; Then ada peringatan "Hasil hitung belum terbaru"; hitung ulang sebelum menerima.
- [ ] **Negatif: peran.** Login B: tombol Terima/Tolak tidak ada.

## 6. Usulan transfer antar lokasi dan antar cabang

- [ ] **Dua dokumen.** Given kebutuhan susu efektif 2.000, stok produksi 700, lokasi 8 (cabang sama) 1.500, lokasi 9 (cabang sama) 400, lokasi cabang lain 5.000; When tab **Transfer**;
      Then **dua** usulan: "Transfer sederhana (satu cabang)" lokasi 8 -> lokasi produksi **1.000 ML** dan "Permintaan transfer barang (antar cabang)" lokasi cabang lain -> lokasi produksi **1.000 ML**
      (dibulatkan ke kemasan utuh, tidak ada dari lokasi 9 yang hanya 400). Kedua status **Menunggu keputusan**.
- [ ] **Bahan tanpa kemasan.** Tepung (KG) butuh 12,5, stok produksi 4, lokasi 8 punya 6, cabang lain 10: transfer 6 dari lokasi 8 dan 2,5 dari cabang lain; **kekurangan 0**.
- [ ] **Kekurangan tidak tertutup.** Sumber total kurang dari kebutuhan; Then baris kebutuhan merah "Kurang X", banner "Ada kekurangan bahan yang belum tertutup transfer"; menyimpan dan mengajukan tetap bisa.
- [ ] **Negatif: tanpa lokasi sumber.** Pemicu hitung hanya memegang cabang Central Kitchen (sumber transfer kini = lokasi dari cabang milik **pemicu hitung**,
      bukan konfigurasi brand): tab Transfer = "Tidak ada usulan transfer..." dengan alasan.
- [ ] **Sumber mengikuti pemicu.** Given dua akun dengan daftar cabang berbeda; When masing-masing menghitung rencana yang sama; Then usulan transfer hanya memuat lokasi
      cabang milik pemicu itu (tidak ada lokasi cabang yang tidak ia pegang). **[AKUN-2]**

## 7. Pengajuan dan persetujuan dua orang

- [ ] **Ajukan.** Given hasil hitung **Terbaru** (kurang dari 30 menit sejak hitung); When A klik **Ajukan untuk persetujuan**; Then status **Diajukan**; tombol edit/hitung hilang.
- [ ] **Negatif: hasil hitung usang.** Setelah mengubah permintaan, **Ajukan** nonaktif dengan alasan "Hitung kebutuhan lebih dulu"; bila sudah > 30 menit sejak hitung, ajukan ditolak "Data stok terlalu lama. Hitung ulang...".
- [ ] **Pemisahan tugas.** Given rencana dibuat dan diajukan oleh **Orang C** (memegang hak planner **dan** gudang); When **C sendiri** membuka rencana;
      Then tombol Setujui nonaktif dengan alasan "Anda pembuat/pengaju rencana ini; pemisahan tugas melarang..."; mencoba lewat URL/aksi tetap ditolak server
      (403 "Pemisahan tugas..."). Given akun **B** (lain) login; Then Setujui aktif. **[AKUN-2]** untuk bagian B; bagian C sendiri bisa dengan akun admin.
- [ ] **Persetujuan transfer.** Login B; tab **Transfer**; **Setujui transfer** untuk transfer sederhana; **Tolak transfer** untuk transfer lain tanpa alasan -> wajib isi alasan; isi alasan -> status **Ditolak** dengan nama pemutus + alasan.
- [ ] **Negatif: setujui rencana sebelum semua transfer diputuskan.** **Setujui rencana** nonaktif: "Masih N usulan transfer belum disetujui atau ditolak".
- [ ] **Setujui rencana.** Setelah semua transfer diputuskan, B **Setujui rencana**; Then status **Disetujui**, linimasa mencatat Diajukan oleh A, Disetujui oleh B.
- [ ] **Kembalikan ke draf.** Rencana lain berstatus Diajukan; B (atau A) **Kembalikan ke draf** dengan alasan; Then status **Draf**, **keputusan transfer direset** ke Menunggu keputusan, pengaju dikosongkan.
- [ ] **Negatif: konflik versi.** Buka rencana yang sama di dua tab; di tab 1 setujui; di tab 2 (belum dimuat ulang) klik aksi lain; Then pesan "Rencana sudah diubah oleh orang lain. Muat ulang...".

## 8. Posting ke ESB staging

Prasyarat: rencana **Disetujui**, `PRODPLAN_WRITE_ENABLED=true`, tanggal produksi dalam jendela posting (kemarin sampai 7 hari ke depan), hasil hitung < 30 menit.
**Penting:** jendela 30 menit dihitung dari saat **Hitung**, bukan saat persetujuan; bila persetujuan berlangsung lama posting ditolak "Data stok terlalu lama" dan rencana yang sudah Disetujui harus dibatalkan lalu dibuat ulang (lihat catatan risiko).

- [ ] **Pratinjau fase transfer.** Tab **Posting** > **1. Transfer bahan**; Then tabel "Dokumen ESB yang akan dibuat" hanya berisi transfer berstatus Disetujui (dengan asal, tujuan, baris), tabel **Dampak stok**, dan **digest**.
- [ ] **Konfirmasi wajib.** Tombol Posting baru berfungsi setelah mencentang "Saya memahami dokumen transfer akan dibuat di ESB..." ; tanpa centang -> pesan "Isian wajib".
- [ ] **Posting transfer.** **Posting transfer ke ESB STAGING**; Then "Posting diterima"; tab **Riwayat/Status dokumen ESB**: baris berstatus Antre dikirim -> Terkirim, menunggu konfirmasi -> **Terkonfirmasi di ESB** dengan nomor dokumen (mis. `ST...`).
      Status rencana **tetap Disetujui**. Admin ESB melihat dokumen yang sama di ESB, ber-`additionalInfo` `pp:...`.
- [ ] **Negatif: posting ganda.** Muat ulang fase transfer: tombol Posting nonaktif ("Dokumen fase ini sudah dibuat..."); klik dari tab lama -> ditolak, tidak ada dokumen kedua di ESB.
- [ ] **Negatif: produksi sebelum transfer di-authorize.** Pratinjau **2. Produksi** lalu posting sebelum admin ESB meng-authorize transfer sederhana; Then ditolak "Transfer di ESB belum di-authorize..." dengan **nomor dokumen**.
- [ ] **Authorize di ESB lalu posting produksi.** Admin ESB meng-authorize transfer sederhana; kembali ke **2. Produksi**; centang pemahaman (dan bila ada kekurangan, centang penerimaan kekurangan); **Posting produksi**;
      Then dokumen produksi dibuat **WIP lebih dulu, produk jadi kemudian**, semua Terkonfirmasi; status rencana **Sudah diposting**; stok di ESB bergerak sesuai Dampak stok.
- [ ] **Negatif: kekurangan terbuka.** Rencana dengan kekurangan: posting produksi tanpa centang "Saya menerima kekurangan..." ditolak "Masih ada kekurangan bahan..."; dengan centang lolos dan tercatat di audit.
- [ ] **Negatif: penulisan dimatikan.** `PRODPLAN_WRITE_ENABLED=false`: banner di Posting dan tombol nonaktif "Penulisan ke ESB STAGING sedang dimatikan...".
- [ ] **Negatif: tanggal di luar jendela.** Rencana bertanggal > 7 hari ke depan atau > 1 hari lalu: posting ditolak "Tanggal produksi di luar jendela...".
- [ ] **Negatif: ESB menolak dokumen / tidak menjawab (dibantu developer).** Developer mensimulasikan 4xx atau timeout; Then baris menjadi **Gagal** (dengan keterangan) atau **Hasil belum pasti (sedang dicocokkan)**; sistem **tidak mengirim ulang otomatis**;
      dokumen berikutnya menunggu; **Kirim ulang** hanya tampil untuk "Gagal - tidak ditemukan di ESB setelah batas waktu". Setelah kirim ulang tidak boleh ada dokumen ganda di ESB (verifikasi ke admin ESB).

## 9. Pembatalan

- [ ] **Batal saat Draf / Diajukan.** **Batalkan rencana**, isi alasan; Then status **Dibatalkan** dengan alasan; tidak ada dokumen ESB.
- [ ] **Batal setelah posting transfer.** Rencana Disetujui dengan transfer Terkonfirmasi; **Batalkan rencana** (bantuan teks: "Dokumen ESB yang sudah terkonfirmasi akan dibatalkan (dihapus)");
      Then baris outbox menjadi "Antre dibatalkan di ESB" -> "Dibatalkan di ESB", dokumen **hilang** di ESB, urutan dari seq terbesar ke terkecil.
- [ ] **Batal setelah posting produksi.** Dokumen produksi dihapus lebih dulu (WIP terakhir), kemudian transfer; stok ESB kembali.
- [ ] **Negatif: dokumen sudah di-authorize / stok hasil terpakai.** ESB menolak hapus -> baris **Gagal dibatalkan di ESB** ("Perlu ditangani langsung di ESB"); tidak diulang otomatis; admin ESB menangani lalu mencatat di tiket.
- [ ] **Negatif: batal saat dokumen sedang dikirim.** Batalkan sewaktu baris berstatus Terkirim/Hasil belum pasti -> ditolak "tidak sesuai status"; coba lagi setelah Terkonfirmasi.
- [ ] **Negatif: peran.** Kepala gudang B tidak melihat tombol Batalkan.

## 10. Daftar pemeriksaan lintas skenario

- [ ] Setiap status memakai kata + warna (tidak hanya warna); setiap tombol nonaktif menampilkan alasan terlihat.
- [ ] Pesan galat selalu berbahasa Indonesia, tanpa SQL, URL ESB, atau tumpukan galat.
- [ ] Setelah sesi ESB berakhir (maks. 1 jam): aksi berikutnya meminta masuk lagi di `/produksi/masuk` (lihat 12.4); tidak ada lagi sesi Odoo 2 jam.
- [ ] Nama pengguna ESB lengkap hanya tampil sebagai pembuat/pengaju/penyetuju/pemutus transfer; username dan password ESB tidak tampil di layar, URL, maupun pesan galat.
- [ ] Setelah pembatalan/perubahan, daftar `/produksi` menampilkan status terbaru; filter brand, tanggal, status bekerja; halaman 51+ rencana menampilkan "Berikutnya".
- [ ] Tidak ada nomor HP/PII pada layar; nama pengguna hanya pembuat/pengaju/penyetuju.

## 12. Login per pengguna ESB (G66b, kontrak §30.17)

Semua terhadap ESB STAGING. Pesan galat login **tidak boleh** membedakan "user tidak ada" dan "password salah". Jangan menyimpan password uji di catatan UAT.

- [ ] **12.1 Login benar.** Given A di `/produksi/masuk` (banner ESB STAGING tampil, kolom username `autocomplete=username`, password bertipe password);
      When isi username (huruf besar/kecil bebas) + password benar; Then **303** ke `/produksi`, ada cookie `__Host-epp` (HttpOnly, Secure, SameSite=Strict, tanpa Domain);
      cookie `__Host-eis` (Insight Odoo) tidak berubah; password tidak muncul di URL/riwayat peramban/respons.
- [ ] **12.2 Password salah.** When username benar + password salah; Then kembali ke halaman masuk dengan "Username atau password ESB salah."; username tidak terisi ulang;
      tidak ada cookie `__Host-epp`. Ulangi dengan username yang tidak ada: pesan **identik**.
- [ ] **12.3 Terkunci.** When 5 kali berturut-turut password salah untuk username yang sama dalam 15 menit; Then percobaan ke-6 (bahkan dengan password benar)
      -> "Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit."; verifikasi ke admin ESB bahwa **tidak ada** panggilan login ke ESB untuk percobaan ke-6 (log ESB / log prodplan
      `pp_login result=login_locked`); setelah 15 menit login benar berhasil dan hitungan gagal ter-reset. Negatif tambahan: 11 percobaan dari satu IP dalam 15 menit -> pesan yang sama (batas IP insight-web).
- [ ] **12.4 Sesi 1 jam.** Given A sudah login; Then sisa waktu di bilah atas turun dari <= 60 menit; When sesi habis (tunggu 60 menit, atau minta developer mengosongkan sesi prodplan / restart
      `prodplan`); Then halaman berikutnya mengalihkan ke `/produksi/masuk` dengan "Sesi ESB berakhir (maksimal 1 jam). Silakan masuk lagi." dan cookie dihapus; tidak ada galat teknis.
- [ ] **12.5 Kedaluwarsa di tengah aksi + masuk lagi.** Given A sedang mengetik permintaan produksi lalu sesi habis; When klik **Simpan permintaan**; Then pesan "Sesi ESB berakhir..." dengan tautan
      **masuk lagi** (tab baru); setelah masuk lagi dan memuat ulang rencana, perubahan yang belum tersimpan **tidak** ada (catat: isian harus diketik ulang) dan rencana tidak rusak.
- [ ] **12.6 Dua akun untuk pemisahan tugas. [AKUN-2]** Given A (planner) membuat + mengajukan rencana; When **B** (akun ESB lain, kepala gudang) login di peramban/profil lain dan menyetujui transfer + rencana;
      Then berhasil; linimasa menampilkan nama lengkap A (pengaju) dan B (penyetuju) dari ESB. Negatif: **C** (pembuat) mencoba menyetujui -> "Pemisahan tugas...". Dua sesi berbeda
      berjalan bersamaan tanpa saling mengganggu (logout A tidak mengeluarkan B).
- [ ] **12.7 Posting tertunda `needs_login` + lanjutkan. [AKUN-2 disarankan]** Given rencana Disetujui, `PRODPLAN_WRITE_ENABLED=true`; When A memposting transfer lalu sesi A berakhir/di-logout **sebelum** worker
      mengirim (atau developer mengosongkan sesi); Then baris dokumen "Berhenti: sesi ESB berakhir, perlu masuk lagi", rencana "Perlu perhatian", pesan "Posting berhenti karena sesi ESB berakhir", **tidak ada**
      dokumen baru di ESB. When A masuk lagi dan klik **Lanjutkan posting**; Then baris berjalan sampai Terkonfirmasi; **tepat satu** dokumen di ESB per baris (verifikasi ke admin ESB). Negatif: bila dokumen
      mungkin sudah dibuat sebelum sesi putus, **akun B** (bukan pengirim awal) klik Lanjutkan posting -> ditolak "Hanya pengguna yang sama yang boleh melanjutkan..."; A bisa melanjutkan (dicocokkan lewat marker, tanpa dokumen ganda).
      Negatif: klik Lanjutkan posting saat tidak ada yang tertunda -> "Tidak ada posting yang perlu dilanjutkan."; **Batalkan rencana** saat ada baris tertunda yang mungkin sudah tercipta -> ditolak "tidak sesuai status".
- [ ] **12.8 Akun tanpa izin ESB / perusahaan salah.** Given D (tanpa hak modul): ditolak "Akun ESB ini tidak memiliki izin modul Produksi." (lihat bagian 1). Given akun ESB milik brand/perusahaan lain
      (bila ada di staging) -> pesan yang sama. Given akun ESB nonaktif -> "Username atau password ESB salah.". Admin ESB memastikan sesi ESB yang terbentuk sementara di-logout (tidak ada token menggantung).
- [ ] **12.9 Cabang di luar izin.** Given akun E hanya memegang cabang non-CK, dan akun F memegang cabang CK berbeda dari rencana milik A (bila ada); Then F **tidak melihat** rencana A di daftar dan membuka tautannya
      langsung -> "Data tidak ditemukan"; E tidak punya pilihan cabang CK di **Buat rencana**; mencoba membuat rencana di cabang yang tidak dipegang (ubah isian lewat alat pengembang) -> "Cabang atau lokasi produksi tidak termasuk daftar Central Kitchen
      yang diizinkan."; menyetujui transfer yang cabang asal/tujuannya tidak dipegang -> "Akun ESB Anda tidak memegang cabang yang diperlukan...". Posting dengan akun yang tidak punya hak "tambah" dokumen
      terkait di ESB -> "Akun ESB Anda tidak punya hak akses dokumen yang diperlukan...".
- [ ] **12.10 Keluar dan batas sesi.** Given A login; When klik **Keluar**; Then kembali ke halaman masuk, cookie `__Host-epp` terhapus; tombol Back/URL `/produksi` -> halaman masuk; admin ESB memastikan token sesi itu
      sudah di-logout. Given prodplan dimatikan saat Keluar: tetap kembali ke halaman masuk (galat diabaikan). Given A login dari empat peramban: sesi tertua dikeluarkan (batas 3 sesi per akun) dan meminta masuk lagi.
- [ ] **12.11 Halaman Insight lain tidak terbuka dengan sesi ESB.** Given A login ke Produksi **tanpa** sesi Odoo; When membuka `/`, `/fnb/exec`, `/sm/...`, `/visitors`, atau ekspor/route data; Then diarahkan ke login Odoo/SSO (bukan data);
      di bilah Produksi tidak ada tautan ke halaman itu. Sebaliknya: pengguna Insight ber-peran produksi di Odoo **tanpa** login ESB membuka `/produksi` -> halaman masuk ESB (peran Odoo tidak memberi akses).
- [ ] **12.12 Duplikasi ke Odoo -> user arsip. (butuh tumpukan lokal + provisioning aktif)** Given `PRODPLAN_ODOO_PROVISION_ENABLED=true`; When A login sukses; Then dalam beberapa detik di Odoo (Superadmin) muncul
      **Identitas ESB** untuk A (akun `pb`, username huruf kecil) dengan user Odoo **terarsip**, login berformat `esb:pb:<username>`, tanpa password/email, grup hanya Internal User (tanpa grup Insight/PP), cermin peran PP + cabang terisi,
      PT dari cabang; login kedua A **tidak** membuat user kedua dan memperbarui "Login ESB terakhir". Negatif: user Odoo biasa dengan login yang sama dengan format shadow -> provisioning ditolak (login tetap berhasil di Produksi);
      matikan Odoo/gateway -> login Produksi tetap berhasil, identitas menyusul pada login berikutnya; tidak ada password/token di log prodplan, gateway, maupun Odoo (periksa).
- [ ] **12.13 Produksi ESB production tetap terkunci.** Given `esb_env=production`; Then banner "ESB PRODUCTION"; login dengan akun admin hanya bisa membaca (GET); `PRODPLAN_WRITE_ENABLED=true` pada production membuat prodplan **tidak start**
      (konstanta pengunci L-250); tidak ada tombol posting yang berhasil menulis ke ESB production.

## 13. Hasil

| Bagian | Lulus | Gagal | Catatan | Penguji / tanggal |
|---|---|---|---|---|
| 1 Akses dan peran | | | | |
| 2 Kemasan utuh | | | | |
| 3 Permintaan | | | | |
| 4 Hitung | | | | |
| 5 Usulan batch | | | | |
| 6 Transfer | | | | |
| 7 Persetujuan dua orang | | | | |
| 8 Posting staging | | | | |
| 9 Pembatalan | | | | |
| 12 Login per pengguna ESB (12.1-12.13) | | | | |
| 12.x bertanda **[AKUN-2]** (12.6, 12.7 sebagian, 7 SoD, 6 sumber mengikuti pemicu) | | | tertahan sampai akun ESB staging kedua (Q-PP17) | |
