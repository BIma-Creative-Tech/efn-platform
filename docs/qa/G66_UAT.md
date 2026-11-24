# UAT G66 — Production Order (rencana produksi Central Kitchen di atas ESB)

Untuk: **Planner CK** dan **Kepala gudang**. Kontrak: `docs/04_SERVICE_CONTRACTS.md` §30; keputusan ADR-051 (Proposed);
runbook `docs/runbooks/prodplan.md`. Format mengikuti `docs/qa/G51_UAT.md` (kotak centang; Given/When/Then).
**Semua langkah dijalankan terhadap ESB STAGING** (banner kuning/merah "ESB STAGING" harus tampil di setiap halaman
Produksi). Skenario ini **belum pernah dijalankan di browser** (QA hanya membaca kode); catat setiap selisih di kolom Hasil.

Angka contoh memakai data uji di dokumen kontrak (susu segar `RMT000021`, satuan ML, 1 kemasan = 1.000 ML). Ganti nama
produk/ID dengan data staging yang sebenarnya, tetapi **hitung ulang angka yang diharapkan** dengan rumus yang sama.

## 0. Prasyarat (disiapkan administrator, bukan penguji)

- [ ] `INSIGHT_PRODPLAN_ENABLED=true`, `PRODPLAN_ENABLED=true`, `PRODPLAN_WRITE_ENABLED=true` (hanya untuk bagian 8-9; bagian 1-7
      cukup `false`), `esb_env = staging`. Stack prodplan + Insight berjalan.
- [ ] Tiga akun Odoo, masing-masing satu orang berbeda:
      **Planner A** (grup "Insight: Production planner CK"), **Kepala gudang B** (grup "Insight: Kepala gudang (persetujuan transfer)"),
      **Orang C** yang memegang **kedua** grup. Tidak satupun boleh punya cabang pada "Cabang ESB dikelola" (kepala toko ber-cabang
      kehilangan peran produksi).
- [ ] Akun staging ESB: ada BOM Assembly untuk dua produk jadi (sebut **FIN-A** 45 ml susu/pcs, **FIN-B** 30 ml susu/pcs), BOM bertingkat
      (**FIN-C** memakai WIP **WIP-D**), dan setidaknya satu produk **tanpa** BOM Assembly. Stok susu di lokasi produksi = 700 ML;
      lokasi sumber satu cabang sama punya >= 1.000 ML, cabang lain punya >= 1.000 ML.
- [ ] Satu admin ESB staging siap meng-authorize transfer sederhana di ESB (bagian 8) dan memeriksa dokumen yang terbentuk.

## 1. Akses dan peran

- [ ] **Planner A masuk.** Given login Odoo sebagai A; When klik menu **Dashboard Insight**, lalu sidebar grup **Produksi**; Then terlihat
      "Rencana produksi", tombol **Buat rencana** dan **Kemasan utuh**; peran di bilah atas = Planner CK; banner lingkungan ESB tampil.
- [ ] **Kepala gudang B masuk.** Given login sebagai B; When membuka Produksi; Then daftar rencana tampil **tanpa** tombol "Buat rencana";
      halaman Kemasan utuh hanya baca (tidak ada "Tambah aturan kemasan").
- [ ] **Negatif: pengguna tanpa grup.** Given akun Odoo biasa tanpa kedua grup; When mencoba membuka `/produksi` langsung; Then diarahkan ke
      halaman "tidak berwenang"; menu Produksi tidak ada di sidebar.
- [ ] **Negatif: flag mati.** Given `INSIGHT_PRODPLAN_ENABLED=false`; When A membuka `/produksi`; Then halaman "belum aktif", bukan galat teknis.

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
- [ ] **Negatif: tanpa lokasi sumber.** Brand tanpa sumber terkonfigurasi: tab Transfer = "Tidak ada usulan transfer..." dengan alasan.

## 7. Pengajuan dan persetujuan dua orang

- [ ] **Ajukan.** Given hasil hitung **Terbaru** (kurang dari 30 menit sejak hitung); When A klik **Ajukan untuk persetujuan**; Then status **Diajukan**; tombol edit/hitung hilang.
- [ ] **Negatif: hasil hitung usang.** Setelah mengubah permintaan, **Ajukan** nonaktif dengan alasan "Hitung kebutuhan lebih dulu"; bila sudah > 30 menit sejak hitung, ajukan ditolak "Data stok terlalu lama. Hitung ulang...".
- [ ] **Pemisahan tugas.** Given rencana diajukan oleh A; When **A sendiri** membuka rencana (A tidak punya grup gudang) atau **Orang C** (pembuat/pengaju) membuka;
      Then tombol Setujui nonaktif dengan alasan "Anda pembuat/pengaju rencana ini; pemisahan tugas melarang..."; mencoba lewat URL/aksi tetap ditolak server.
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
- [ ] Setelah 2 jam sesi berakhir: aksi berikutnya meminta membuka ulang Insight dari Odoo.
- [ ] Setelah pembatalan/perubahan, daftar `/produksi` menampilkan status terbaru; filter brand, tanggal, status bekerja; halaman 51+ rencana menampilkan "Berikutnya".
- [ ] Tidak ada nomor HP/PII pada layar; nama pengguna hanya pembuat/pengaju/penyetuju.

## 11. Hasil

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
