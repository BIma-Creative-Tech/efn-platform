# Spesifikasi UX/UI — EFN Insight untuk Supermarket (G47, dashboard per persona)

Status: DRAF 20 Nov 2026, ditulis efn-uiux-designer untuk efn-frontend-engineer dan efn-data-ai-engineer. **Spesifikasi, belum ada kode.** Melengkapi (tidak menggantikan) `docs/design/insight-web.md` (G42: token, shell, filter, komponen bersama, aturan chart) — yang tidak diubah di sini berlaku apa adanya. Kontrak data ditulis architect secara paralel; dokumen ini **hanya memakai nama metrik bisnis** (Bagian 0). Pemetaan metrik → kolom `efn_insight.v_sm_*` dilakukan di kontrak, bukan di sini. Semua angka di wireframe = ilustrasi bulat, bukan data klien. Semua ambang = **usulan**, bukan keputusan bisnis (Bagian 9).

Dasar: `docs/04_SERVICE_CONTRACTS.md` §28.4 (usulan order), §28.5/§28.5a (assortment, near-expiry, markdown), §28.6/§28.8 (event perilaku, wishlist/alert), §28.10 (flag), §28.11 (token `eit2`, klaim `wids` dan `roles`), ADR-046 (forecast, WAPE/bias), ADR-047 (segmen, k-anonim), ADR-041 §3.3 (scope outlet). Rencana persona: rencana G47 (Direksi, Category manager, Kepala toko, DC, Marketing).

Belum diuji di browser, pembaca layar, atau perangkat nyata (spesifikasi saja).

---

## 0. Kamus metrik bisnis (satu definisi, dipakai semua persona)

Nama di kolom kiri = label UI sekaligus nama yang dipakai kontrak. Definisi = maksud bisnis; rumus final milik bisnis/architect (Bagian 9).

| Metrik (label UI) | Definisi singkat | Satuan | Baik bila | Persona |
|---|---|---|---|---|
| Omzet | Penjualan bersih setelah diskon, semua kanal (POS, online, kiosk). Label jujur soal pajak: lihat §7.4 | Rp | naik | Direksi, Kepala toko (ringkas) |
| Margin kotor | (Omzet − HPP) / Omzet | % (pp untuk delta) | naik | Direksi |
| GMROI | Margin kotor (Rp) / rata-rata nilai persediaan pada HPP, periode yang sama (definisi final: Q-INS-S1) | kali (x) | naik | Direksi |
| YoY | Perubahan Omzet dibanding periode yang sama tahun lalu | % | naik | Direksi |
| Waste % | Nilai susut (scrap: rusak, kedaluwarsa, hilang) pada HPP / Omzet | % | turun | Direksi, Kepala toko |
| Galat forecast (WAPE) | Σ|aktual − forecast| / Σ aktual, SKU × outlet, 8 minggu terakhir (ADR-046). Akurasi ≈ 100% − WAPE | % | turun | Direksi |
| Bias forecast | (Σ forecast − Σ aktual) / Σ aktual; positif = cenderung kelebihan | % | mendekati 0 | Direksi |
| OOS rate | Porsi SKU × outlet × hari yang stoknya nol padahal masuk assortment outlet itu | % | turun | Category manager |
| Near-expiry | Lot yang sisa harinya ≤ ambang kategori (`efn.expiry.alert`); status Pantau / Markdown sekarang / Kedaluwarsa | lot, qty, Rp (HPP) | turun | Category manager, Kepala toko |
| Markdown aktif | Lot yang punya `efn.lot.markdown` berstatus berlaku | lot | — | Kepala toko, Category manager |
| Harga vs pasar | (Harga jual kita − median harga pasar Price Hunter tervalidasi 30 hari) / median | % | dekat 0 | Category manager |
| Sinyal terlalu mahal | SKU yang dilihat tetapi tidak dibeli **dan** harga > median pasar/median beli member (ADR-047 §3.3) | SKU | turun | Category manager, Marketing |
| Stok kritis | SKU di outlet dengan stok ≤ minimum orderpoint, atau < N hari cakupan (definisi final: Q-INS-S3) | SKU | turun | Kepala toko |
| Usulan order | `efn.replenishment.proposal` per jadwal kirim: status Draf / Diusulkan / Disetujui / Ditolak / Kedaluwarsa, batas persetujuan (`cutoff_at`) | usulan, baris | — | Kepala toko, DC |
| Fill rate DC | Qty yang dikirim DC / qty yang disetujui outlet, per pengiriman (definisi final: Q-INS-S4) | % | naik | DC, Direksi (opsional) |
| Order outlet | Permintaan outlet ke DC per hari kirim: jumlah, baris, status | usulan | — | DC |
| Umur stok DC | Sebaran qty/nilai stok DC menurut sisa umur (lot) atau lama di gudang | bucket hari | lebih sedikit di bucket tua | DC |
| PO supplier | RFQ/PO DC: Draf / Dikonfirmasi / Diterima sebagian / Terlambat (melewati tanggal terima terjanji) | PO | — | DC |
| Funnel visitor | Perangkat/pengunjung ber-consent yang lanjut: Lihat produk → Wishlist/Keranjang → Mulai checkout → Beli | pengunjung, % | konversi naik | Marketing |
| Segmen | Ukuran 5 segmen harian: lihat-tidak-beli, wishlist-menganggur, OOS-dilihat, sensitif harga, cari-tanpa-hasil | pengunjung | — | Marketing |
| Hasil kampanye | Per kampanye: jangkauan (terkirim), voucher dipakai, konversi, Omzet inkremental bila ada kelompok kontrol, biaya voucher vs anggaran | mixed | — | Marketing |

---

## 1. Prinsip khusus G47

1. **Insight hanya membaca; tindakan terjadi di Odoo.** Insight tidak punya tombol "Setujui", "Aktifkan markdown", "Kirim kampanye". Alasan: approval teraudit dan SoD (kepala toko tidak boleh menyetujui PO supplier; verifikator terpisah) hidup di Odoo, dan data Insight terlambat hingga ±15 menit. Setiap kartu yang memicu tindakan punya **tautan "Buka di Odoo"** (tab baru, `rel="noopener noreferrer"`) yang membawa ke daftar/formulir terfilter. Format URL Odoo 19 diverifikasi engineer; Insight tidak mengarang ID rekaman, hanya memakai ID yang disediakan view.
2. **Satu persona = satu pertanyaan di atas lipatan.** Setiap halaman diawali satu kalimat pertanyaan (subjudul) dan kartu paling penting di posisi pertama.
3. **Urutan kartu mengikuti urgensi tindakan**, bukan kelengkapan: yang punya batas waktu (cut-off usulan, lot kedaluwarsa hari ini) di atas, tren di bawah.
4. **Kesegaran per kartu, bukan hanya global.** Tiap kartu menampilkan "Data per 14:45" (waktu watermark mart-nya, WIB). Aturan di §5.2.
5. **Hak data = hak tampilan.** Outlet di luar `wids` sesi tidak muncul di mana pun (filter, tabel, tooltip, ekspor). UI tidak pernah mencoba "menyembunyikan" baris yang sudah ditarik: penyaringan sepenuhnya di row policy.
6. **Tanpa PII, k-anonim.** Tidak ada nama/nomor member. Angka kelompok kecil di halaman Marketing ditekan (§4.5, §6.6).
7. **Tidak ada komponen per persona.** Semua halaman dirangkai dari komponen G42 + tambahan di §8. Perbedaan persona = susunan, kartu, dan konfigurasi ambang (R-T14 semangatnya: token + config).
8. **Zona waktu:** semua "hari ini", jam cut-off, dan "per jam X" memakai **Asia/Jakarta (WIB)** per outlet (`cutoff_time` punya TZ; tampilkan WIB eksplisit).

---

## 2. Navigasi, routing, dan hak akses

### 2.1 Rute dan sidebar
Halaman baru (route group `(persona)` di `services/insight-web`), slug mengikuti nilai `roles` pada token `eit2`:

| Rute | Label sidebar | `roles` | Ikon (baru, sederhana) |
|---|---|---|---|
| `/exec` | Direksi | `exec` | `briefcase` |
| `/category` | Kategori | `category` | `grid` |
| `/store` | Toko saya | `store` | `store` |
| `/dc` | Distribution Center | `dc` | `truck` (sudah ada) |
| `/marketing` | Marketing | `marketing` | `megaphone` |

- Beranda `/` mengarahkan ke halaman persona pertama menurut `roles` (urutan prioritas: `store`, `dc`, `category`, `marketing`, `exec`). Pengguna dengan beberapa peran melihat semua rute miliknya di sidebar, dengan judul grup "Supermarket" di atas daftar.
- Halaman G42 berbasis ESB (`/flow`, `/sales`, `/payments`, `/distribution`, `/forecast`) tidak relevan untuk PT SMD dan **disembunyikan** untuk sesi yang hanya berisi kode `SMD`. Untuk sesi campuran (holding) kedua grup tampil, dipisah judul grup "Supermarket" dan "Food & Beverage". `/data-quality` tetap bersama.
- Akses ke rute di luar `roles` → halaman 403 ramah ("Halaman ini untuk peran lain. Hubungi admin bila Anda memerlukannya"), bukan 404 (agar pengguna tahu halaman itu ada). Apakah `exec` boleh membuka semua halaman persona: **Q-INS-S9** (keputusan security; default spesifikasi: tidak, hanya sesuai `roles`).
- Pengguna `store` tanpa warehouse → token tidak diterbitkan (§28.11); halaman `/belum-aktif` yang sudah ada dipakai dengan kalimat khusus: "Akun Anda belum ditautkan ke outlet. Minta admin mengisi outlet Anda di Odoo."

### 2.2 Kesadaran peran di header
Header G42 ditambah **chip peran + cakupan** di sisi pengguna: "Kepala toko · Outlet Kelapa Gading", atau "Direksi · semua outlet (5)". Tujuannya: menghindari salah baca saat satu orang punya beberapa peran. Teks, bukan hanya warna.

---

## 3. Filter

Berlaku komponen FilterBar G42 dengan perubahan berikut. State di URL (`from`, `to`, `wh`, `cat`), validasi server, chip aktif, tombol Reset — sama dengan G42 §3.2.

| Filter | Direksi | Kategori | Toko saya | DC | Marketing |
|---|---|---|---|---|---|
| Periode | ya (preset + pembanding YoY) | ya | **tidak ada** (selalu "hari ini" + 7 hari untuk tren) | ya | ya |
| Outlet (`wh`) | multi, hanya outlet (bukan DC) | multi, hanya outlet | **tidak ada kontrol** bila 1 outlet; multi bila >1 | multi (outlet tujuan) | multi |
| Kategori | ya | **wajib terlihat**, default = semua kategori yang ditugaskan ke pengguna bila Odoo menyediakannya (Q-INS-S6) | ya (menyaring near-expiry/stok) | ya | ya (untuk funnel per kategori produk) |
| Kanal (Toko/Online/Kiosk) | opsional fase 2 | tidak | tidak | tidak | ya untuk funnel (`channel`: web/app) |

**Perilaku satu outlet (persona Kepala toko dan pengguna mana pun dengan tepat satu `wid`):**
- Kontrol Outlet **tidak dirender**; diganti label statis berformat judul halaman: `<h1>Toko saya</h1>` dengan subjudul nama outlet ("Outlet Kelapa Gading · diperbarui 14:45").
- Tidak ada kolom "Outlet" di tabel, tidak ada heatmap outlet × metrik (tidak bermakna), tidak ada peringkat antar outlet. Kartu yang bergantung perbandingan antar outlet menjadi "tren outlet ini vs 4 minggu terakhir".
- Jika pengguna sebenarnya punya >1 outlet (sesi berubah di Odoo), pemilih outlet muncul di bawah judul sebagai **tab outlet** (bukan dropdown) bila ≤ 5 outlet, agar satu ketukan di ponsel.
- Bila pembandingnya tidak ada (mis. "peringkat outlet") kartu **tidak ditampilkan sama sekali**, bukan kartu kosong, kecuali untuk kartu yang memang wajib (diberi EmptyState "Hanya satu outlet pada akun Anda").

**Perilaku tanpa data (filter terlalu sempit):** EmptyState G42 dengan aksi "Reset filter".

---

## 4. Halaman per persona

Konvensi di tiap tabel kartu: **P/B** = periode default / pembanding. **Ambang** = usulan (Bagian 9, disimpan terpusat di `lib/thresholds.ts`). Semua ambang menggunakan StatusPill (ikon + kata + warna) dan DeltaBadge `goodWhen`. **Kosong** = kondisi tanpa data; **Terlambat** = perilaku bila data melewati batas kesegarannya (aturan umum §5.2). Tautan = "Buka di Odoo" kecuali disebut lain.

### 4.1 Kepala toko — `/store` (mobile-first)

**Siapa & kapan:** kepala outlet, berdiri di toko dengan ponsel (lebar 360–414 px), membuka pagi hari dan menjelang cut-off. Sesi singkat (< 1 menit), jempol satu tangan, cahaya toko terang (kontras penting, tema terang default, jangan tergantung hover).

**Pertanyaan bisnis dan tindakan**

| Pertanyaan | Tindakan yang dipicu |
|---|---|
| Apakah usulan order untuk pengiriman berikutnya sudah saya setujui, dan berapa lama lagi batasnya? | Buka usulan di Odoo, ubah qty (alasan wajib: promo lokal/event/rak penuh), setujui sebelum cut-off |
| Barang apa yang harus saya turunkan harganya atau tarik hari ini? | Buat/aktifkan markdown per lot di Odoo; tarik lot kedaluwarsa dari rak; centang "kirim ke member" |
| SKU apa yang akan habis atau sudah habis? | Cek usulan/permintaan darurat, transfer antar outlet, hubungi DC |
| Seberapa besar susut saya minggu ini, dan dari alasan apa? | Periksa praktik penerimaan/penyimpanan; catat scrap di Odoo yang belum tercatat |

**Tata letak (urutan = prioritas, satu kolom di ponsel)**

```
360 px
+------------------------------------------+
| EFN Insight              [Menu]          |  header 56 px, lengket
+------------------------------------------+
| Toko saya                                |  h1
| Outlet Kelapa Gading                     |
| Kepala toko · Data per 14:45 WIB (ok)    |
| [Usulan] [Kedaluwarsa] [Stok] [Susut]    |  jangkar lengket (chip 44 px)
+------------------------------------------+
| ST-1  USULAN ORDER            [Perlu dicek]
| Pengiriman Kamis 21 Nov                  |
| Batas persetujuan 15:00 WIB              |
| (tersisa 3 jam 15 menit)                 |
|  Status:  Diusulkan  (belum disetujui)   |
|  48 baris · 12 baris berubah dari sistem |
|  [ Buka usulan di Odoo ]   <- 48 px      |
|  Status per 14:45. Cek di Odoo sebelum   |
|  bertindak.                              |
|  > Pengiriman berikutnya lainnya (2)     |
+------------------------------------------+
| ST-2  KEDALUWARSA HARI INI    3 lot      |
|  [Kedaluwarsa]  Susu UHT 1 L   2 pak     |
|     ED hari ini · Rp 54 rb · tanpa markdown
|     [Buka lot di Odoo]                   |
|  [Markdown sekarang] Yogurt 100 g  18    |
|     sisa 1 hari · markdown aktif -30%    |
|  [Pantau] Roti tawar 400 g   9           |
|     sisa 3 hari · belum ada markdown     |
|  [Lihat semua 14 lot]                    |
+------------------------------------------+
| ST-3  STOK KRITIS             7 SKU      |
|  [Habis] Telur 10 btr    stok 0          |
|     min 24 · usulan: masuk di usulan #   |
|  [Kritis] Minyak 2 L     stok 6 / min 20 |
|  [Lihat semua 7 SKU]                     |
+------------------------------------------+
| ST-4  SUSUT (WASTE)                      |
|  Minggu ini  Rp 1,8 jt   2,1% dari omzet |
|  +0,4 pp vs minggu lalu   [Perlu dicek]  |
|  Alasan: Kedaluwarsa 62% | Rusak 25% | .. |
|  [Tampilkan sebagai tabel]               |
+------------------------------------------+
| ST-5  PENJUALAN HARI INI (ringkas)       |  fase 2
+------------------------------------------+
```

**Spesifikasi kartu**

| ID | Metrik & definisi | P/B | Visual | Ambang | Kosong / Terlambat | Tautan |
|---|---|---|---|---|---|---|
| ST-1 Usulan order | Usulan order terdekat per jadwal kirim: status, batas persetujuan, jumlah baris, jumlah baris yang diubah dari angka sistem (alasan perubahan: promo lokal/event/rak penuh). Menampilkan ≤ 1 usulan terdekat penuh; usulan lain terlipat ("Pengiriman berikutnya lainnya"). | Usulan dengan `delivery_date` ≥ hari ini; tanpa pembanding | Kartu status besar: StatusPill + teks status, **waktu batas absolut dan hitung mundur** ("15:00 WIB, tersisa 3 j 15 m"), satu tombol utama. Hitung mundur diperbarui tiap menit di klien dan **tidak** `aria-live` (hanya dibaca saat difokus; pengumuman tunggal bila melewati batas) | Batas > 2 jam: Baik bila Disetujui, Perlu dicek bila Diusulkan. Batas ≤ 2 jam dan belum Disetujui: **Bermasalah** (ikon peringatan + kata "Segera"). Kedaluwarsa tanpa persetujuan: Bermasalah + "Tidak dikirim otomatis" (L-149). Ditolak: Perlu dicek | **Kosong:** "Tidak ada usulan pengiriman dalam 7 hari. Bisa jadi forecast belum berjalan atau jadwal kirim belum diatur." + tautan ke penjelasan (tanpa mengarang penyebab pasti). **Terlambat:** bila data usulan > 30 menit: spanduk di dalam kartu "Status mungkin sudah berubah di Odoo. Cek di Odoo sebelum bertindak"; > 60 menit: status ditampilkan abu "Tidak pasti" tanpa menyatakan Disetujui/Diusulkan | Daftar usulan outlet ini (state, tanggal kirim terfilter); label tombol "Buka usulan di Odoo" |
| ST-2 Kedaluwarsa hari ini | Lot near-expiry di outlet ini diurutkan sisa hari menaik (lalu Rp HPP menurun): sisa hari, qty, nilai HPP, status markdown. Status: **Kedaluwarsa** (sisa ≤ 0), **Markdown sekarang** (sisa ≤ ambang markdown kategori, belum ada markdown aktif), **Markdown aktif**, **Pantau** (sisa ≤ ambang near-expiry). Ambang diambil dari konfigurasi kategori Odoo (`efn_near_expiry_days`, `efn_markdown_days`), **tidak di-hard-code di Insight**. | Hari ini (snapshot alert terbaru); pembanding: jumlah lot kemarin ("+2 dari kemarin") | Daftar kartu-baris (bukan tabel) di ponsel; tampilkan 3 teratas + "Lihat semua N lot" (halaman penuh, tabel di desktop). Header kartu: jumlah per status sebagai pil | Kedaluwarsa = Bermasalah (silang); Markdown sekarang = Perlu dicek (segitiga); Markdown aktif = Baik (centang) walau masih near-expiry; Pantau = Info (i) | **Kosong positif:** "Tidak ada barang mendekati kedaluwarsa. Bagus." (ikon centang). **Belum aktif:** bila `efn_expiry_monitor.enabled=false`, tampilkan "Pemantauan kedaluwarsa belum diaktifkan untuk outlet ini" (bukan kosong positif!). **Terlambat:** alert dibuat cron harian; tampilkan "Dihitung 06:00 WIB (cron harian)" bukan "per jam X" (Q-INS-S7) | Daftar `efn.expiry.alert` outlet; tombol per baris "Buat markdown" (membuka formulir `efn.lot.markdown` dengan lot terisi, bila tersedia dari view) |
| ST-3 Stok kritis | SKU assortment aktif outlet ini yang Habis (stok 0) atau Kritis (stok ≤ minimum atau cakupan < N hari). Menyertakan stok, minimum, dan apakah SKU sudah masuk usulan order terdekat (membantu memutuskan perlu darurat atau tidak) | Saat ini (snapshot stok terakhir); tanpa pembanding | Daftar kartu-baris, Habis di atas Kritis, urut nilai penjualan harian rata-rata menurun (barang laris dulu); 3 teratas + "Lihat semua" | Habis = Bermasalah; Kritis = Perlu dicek. "Sudah di usulan" = teks tambahan (bukan pengubah status) | **Kosong positif:** "Tidak ada SKU kritis." **Terlambat:** stok live > 30 menit = spanduk "Stok per 14:15, mungkin sudah berubah" | Daftar produk di Odoo terfilter outlet, atau usulan (bila SKU ada) |
| ST-4 Susut | Waste % dan Rp (HPP) minggu berjalan (Sen–hari ini) vs minggu lalu pada rentang hari yang sama; rincian menurut alasan (kedaluwarsa, rusak, gagal masak, lain). Ringkas dalam angka + 1 bar tumpuk 100% horizontal (maks 4 segmen, label langsung) | Minggu berjalan vs minggu lalu sama panjang | Angka + DeltaBadge (`goodWhen:'down'`) + bar tumpuk; tombol "Tampilkan sebagai tabel" | Waste % ≤ 2 Baik; 2–4 Perlu dicek; > 4 Bermasalah (usulan, per kategori bisa beda) | **Kosong:** "Belum ada pencatatan susut minggu ini. Pastikan scrap dicatat di Odoo." (peringatan lunak: nol scrap sering = belum dicatat, bukan nol susut). **Terlambat:** aturan umum | Daftar `stock.scrap` outlet minggu ini |
| ST-5 Penjualan hari ini (fase 2) | Omzet hari ini s.d. data terakhir vs hari yang sama minggu lalu | Hari ini vs Sen/… minggu lalu | Angka + delta + sparkline per jam **tidak** tersedia bila grain mart harian; hanya angka | Tanpa status (netral) | Terlambat: tampilkan "s.d. jam X" | — |

**Interaksi dan perilaku mobile**
- Jangkar lengket 4 chip melompat ke kartu (tautan `#usulan` dsb.; bukan tab). `scroll-padding-top` = tinggi header + baris jangkar agar fokus tidak tertutup (WCAG 2.4.11).
- Tidak ada filter global di halaman ini. Satu-satunya kontrol: tab outlet bila >1 outlet.
- Tarik-untuk-segarkan **tidak** dipakai (ETL 15 menit; tombol "Muat ulang" teks di baris kesegaran, 44 px).
- Tombol "Buka di Odoo" = tautan bergaya tombol, lebar penuh kartu, tinggi ≥ 48 px, `target="_blank"`, teks "Buka usulan di Odoo" + `<span class="sr-only">(tab baru)</span>`. Odoo di ponsel mungkin meminta login; tidak ditangani Insight.
- Landscape/tablet: kartu 2 kolom (ST-1+ST-4 kiri, ST-2+ST-3 kanan), urutan DOM tetap sama dengan urutan ponsel.

### 4.2 Direksi — `/exec` (desktop-first)

**Siapa & kapan:** direksi/manajemen, rapat mingguan atau cek harian di laptop/tablet.

| Pertanyaan | Tindakan |
|---|---|
| Bagaimana Omzet dan Margin kotor dibanding periode lalu dan tahun lalu? | Bila turun: buka peringkat outlet, lalu minta Category manager/Kepala toko menjelaskan; tidak ada tindakan di Odoo dari halaman ini |
| Outlet mana yang tertinggal (omzet, margin, susut)? | Telepon/tinjau outlet; tautan ke laporan penjualan outlet di Odoo |
| Apakah persediaan menghasilkan margin yang layak (GMROI) dan seberapa besar susut? | Minta kebijakan pemesanan/markdown ditinjau |
| Apakah forecast cukup dipercaya untuk mengotomatiskan lebih jauh? | Putuskan perluasan `v2`/otomasi (L-148); lihat bias |

**Tata letak**

```
1280 px
+---------+---------------------------------------------------------------+
| sidebar | [Periode: 30 hari v] [Outlet v] [Kategori v]   Data per 14:45 |
|         | Direksi — Bagaimana kinerja PT Supermarket Demo periode ini?  |
|         |  Chip aktif: 21 Okt - 19 Nov 2026 x                           |
|         | +--------+--------+--------+--------+--------+--------+       |
|         | | Omzet  | Margin | GMROI  |  YoY   | Waste% | Galat  |  EX-1 |
|         | | Rp 4,2M| 24,1%  | 2,8x   | +6,3%  | 2,1%   | forecast|      |
|         | | +3,1%  | -0,4pp |  +0,2x | [i]    | +0,3pp | 22%    |       |
|         | | ~~~~   | ~~~~   | ~~~~   | ~~~~   | ~~~~   | ~~~~   |       |
|         | +--------+--------+--------+--------+--------+--------+       |
|         | +-----------------------------+ +---------------------------+ |
|         | | EX-2 Tren Omzet (garis)     | | EX-3 Perbandingan outlet  | |
|         | | periode ini vs lalu vs YoY  | | tabel peringkat + bar mini| |
|         | |                             | | Outlet|Omzet|Mrg|Waste|YoY| |
|         | +-----------------------------+ +---------------------------+ |
|         | +-----------------------------+ +---------------------------+ |
|         | | EX-4 Outlet x Metrik        | | EX-5 Waste % per kategori | |
|         | | heatmap (angka di sel)      | | bar horizontal terurut    | |
|         | +-----------------------------+ +---------------------------+ |
|         | | EX-6 Akurasi forecast: galat (WAPE) & bias per outlet,      | |
|         | |      tren 8 minggu                                           | |
|         | +-------------------------------------------------------------+ |
+---------+---------------------------------------------------------------+
```

| ID | Metrik & definisi | P/B | Visual | Ambang | Kosong / Terlambat | Drill-down |
|---|---|---|---|---|---|---|
| EX-1 Baris KPI (6 `KpiTile`) | Omzet, Margin kotor, GMROI, YoY, Waste %, Galat forecast (WAPE). Tiap ubin: nilai, delta, sparkline 32 px, tombol "i" definisi. Margin dan GMROI dihitung dari HPP: bila HPP belum lengkap (SKU tanpa biaya), ubin diberi catatan "dari N% SKU yang punya HPP" | 30 hari s.d. H-1; pembanding: 30 hari sebelumnya (delta). YoY = periode yang sama tahun lalu | KpiTile G42 (`goodWhen`: Omzet up, Margin up, GMROI up, Waste down, WAPE down). YoY ubin tanpa sparkline, hanya angka + teks pembanding | Margin: dibanding target (config per kategori; tanpa target = netral). Waste: lihat ST-4. WAPE: ≤ 25% Baik, 25–40 Perlu dicek, > 40 Bermasalah (usulan; per-SKU-outlet-harian tinggi secara alami, lihat Q-INS-S5) | **YoY kosong:** bila belum ada 12 bulan histori: ubin menampilkan "Belum ada pembanding tahun lalu" (bukan 0%, bukan "-"). GMROI kosong bila nilai persediaan tidak tersedia: "Menunggu data persediaan". WAPE kosong bila forecast belum berjalan: "Forecast belum aktif" (flag `FORECAST_ENABLED`) | Klik ubin → menggulir ke kartu terkait (EX-2/3/5/6); tautan "Laporan di Odoo" hanya di EX-3 |
| EX-2 Tren Omzet | Omzet harian, 3 garis: periode ini (solid), periode lalu (putus abu), tahun lalu (titik-putus) bila ada. Toggle metrik: Omzet / Margin kotor % / Waste % | Harian, s.d. H-1 | Garis (G42 §5); label langsung di akhir garis | Tanpa status; sorot hari libur/puncak (Gajian, Ramadan, Lebaran) lewat tanda vertikal bila kalender tersedia (`efn.forecast.calendar`) — fase 2 | **Kosong:** G42 EmptyState. **Terlambat:** aturan umum | Klik hari: sorot (tidak memfilter) |
| EX-3 Perbandingan outlet | Tabel peringkat 5 outlet: Omzet, delta vs periode lalu, Margin kotor %, Waste %, YoY, status gabungan. Bar mini di sel Omzet (panjang = share). Hanya outlet (DC tidak ikut) | Periode filter; urut Omzet menurun | `DataTable` G42 + bar mini + StatusPill di kolom Status. Sorot baris terbaik/terburuk dengan teks "Tertinggi"/"Terendah" (bukan hanya warna) | Status gabungan = terburuk dari (margin vs target, waste, delta Omzet < −5%) | Satu outlet saja: kartu **disembunyikan** (§3) | Klik nama outlet → filter `wh` + chip |
| EX-4 Outlet × Metrik (heatmap) | Skala standar: tiap baris outlet, tiap kolom metrik (Omzet, Margin, Waste %, OOS rate, Galat forecast) dengan **peringkat relatif per kolom** (1–5) diwarnai skala sekuensial, nilai asli di sel | Periode filter | Heatmap tabel `<table>`; warna = peringkat (bukan nilai), angka + peringkat ("1/5") di sel; legenda | Peringkat bukan status: tidak merah/hijau (hindari kesan baik/buruk bila kolom "naik = buruk"): gunakan skala sekuensial satu warna dengan arah dibalik untuk kolom `goodWhen:'down'` dan **ikon panah + teks "terbaik/terburuk"** di sel ekstrem | Bila < 3 outlet, sembunyikan | Tautan baris = filter outlet |
| EX-5 Waste % per kategori | Bar horizontal terurut menurun (maks 8 + "Lainnya"), label nilai di ujung batang, garis acuan target waste | Periode filter | Bar (G42 §5) | Warna batang dari status waste kategori + ikon di label sumbu | **Kosong:** "Belum ada pencatatan susut pada periode ini" (lihat ST-4) | Klik kategori → filter `cat` |
| EX-6 Akurasi forecast | Dua metrik per outlet: Galat forecast (WAPE) dan Bias, tren mingguan 8 minggu; tabel kecil outlet × (WAPE, bias, jumlah SKU dievaluasi). Catatan wajib: "Forecast adalah usulan; kepala toko menyetujui qty" | 8 minggu terakhir, tiap minggu | Garis WAPE (garis acuan 25%) + bar bias di bawah (diverging, 0 di tengah, **teks "kelebihan/kekurangan"** pada tooltip) | Bias |x| ≤ 5% Baik; 5–10 Perlu dicek; > 10 Bermasalah (usulan) | **Kosong:** "Belum ada hasil evaluasi forecast (butuh minimal 4 minggu data setelah forecast aktif)." Tampilkan tanggal mulai evaluasi | Tautan ke "Log bias" di Odoo (`efn_replenishment`) |

### 4.3 Category manager — `/category`

**Siapa & kapan:** membeli/mengelola kategori lintas 5 outlet; kerja harian di laptop.

| Pertanyaan | Tindakan |
|---|---|
| SKU/kategori mana yang sering habis, di outlet mana? | Pesan ulang/ubah parameter orderpoint, minta transfer antar outlet, cek assortment (SKU kena restriksi terlalu ketat → OOS, R-O14) |
| Lot mana yang mendekati kedaluwarsa di banyak outlet sekaligus? | Rencanakan markdown serentak, transfer ke outlet dengan penjualan lebih tinggi, negosiasi retur supplier |
| Harga kita di mana di atas/bawah pasar? | Tinjau pricelist; ajukan perubahan harga di Odoo |
| SKU mana yang dilihat tapi tidak dibeli karena mahal? | Ajukan promo/voucher (usulan ke Marketing lewat Odoo `efn_campaign`) |

**Tata letak**

```
+---------+---------------------------------------------------------------+
| sidebar | [Periode] [Outlet v] [Kategori v: Susu, Roti...]   Data 14:45 |
|         | Kategori — Apa yang habis, mendekati kedaluwarsa, atau mahal? |
|         | +----------+----------+------------+----------------+         |
|         | | OOS rate | Near-    | Markdown   | Sinyal terlalu |  CM-1   |
|         | | 3,4%     | expiry   | aktif      | mahal          |         |
|         | | -0,6 pp  | 41 lot   | 12 lot     | 9 SKU          |         |
|         | +----------+----------+------------+----------------+         |
|         | +-------------------------------+ +-------------------------+ |
|         | | CM-2 OOS: kategori x outlet   | | CM-3 SKU paling sering  | |
|         | | heatmap (angka % di sel)      | | habis (tabel peringkat) | |
|         | +-------------------------------+ +-------------------------+ |
|         | | CM-4 Near-expiry lintas outlet (tabel: produk, lot, outlet,  |
|         | |      qty, sisa hari, nilai HPP, status, markdown)            |
|         | +--------------------------------------------------------------+
|         | | CM-5 Harga vs Price Hunter (tabel + titik sebaran)           | |
|         | | CM-6 Sinyal terlalu mahal (tabel SKU: harga, median pasar,   | |
|         | |      dilihat, dibeli, selisih)                               | |
|         | +--------------------------------------------------------------+
+---------+---------------------------------------------------------------+
```

| ID | Metrik & definisi | P/B | Visual | Ambang | Kosong / Terlambat | Drill-down |
|---|---|---|---|---|---|---|
| CM-1 Baris KPI (4) | OOS rate, Near-expiry (jumlah lot + nilai HPP), Markdown aktif, Sinyal terlalu mahal (jumlah SKU) | 7 hari s.d. H-1 (OOS rate dihitung rata-rata hari); Near-expiry & Markdown aktif = snapshot terbaru; Sinyal = 30 hari | KpiTile (G42) | OOS rate: ≤ 2% Baik, 2–5% Perlu dicek, > 5% Bermasalah (usulan; Q-INS-S2). Near-expiry: tanpa status sendiri (rincian di CM-4) | **Kosong/sinyal:** bila `BEHAVIOR_TRACKING_ENABLED=false` ubin Sinyal menampilkan "Pelacakan perilaku belum aktif" (netral), bukan 0 | Gulir ke kartu terkait |
| CM-2 OOS: kategori × outlet | Heatmap: baris kategori, kolom outlet, sel = OOS rate periode; sel kosong = tidak ada SKU assortment di kategori itu di outlet itu ("n/a", bukan 0%) | 7 hari; pembanding: 7 hari sebelumnya (tooltip) | Heatmap `<table>` skala sekuensial (arah: lebih tinggi = lebih gelap), angka % di sel + ikon di sel ekstrem (> 5%: segitiga), legenda | Warna = skala sekuensial; sel > 5% diberi ikon + tebal; **bukan** hanya warna | **Kosong:** G42. Satu outlet: sembunyikan kolom outlet → jadi bar kategori | Klik sel → filter kategori + outlet, CM-3 mengikuti |
| CM-3 SKU paling sering habis | Tabel peringkat 10 SKU: OOS rate, hari habis, outlet terdampak (jumlah), penjualan harian rata-rata (nilai yang hilang diperkirakan; label "perkiraan"), alasan umum bila ada (assortment, usulan tidak dikirim/expired) | 7 hari | `DataTable` peringkat + bar mini OOS rate; paginasi 10 | Status per baris dari OOS rate | **Terlambat:** aturan umum | Nama SKU → produk di Odoo |
| CM-4 Near-expiry lintas outlet | Tabel lot: Produk, Lot, Outlet, Qty, Sisa hari, Nilai HPP, Status (Pantau/Markdown sekarang/Markdown aktif/Kedaluwarsa), Markdown (persen/harga, sampai tanggal). Filter cepat "Belum ada markdown". Ringkasan atas tabel: total nilai HPP berisiko per status | Snapshot terbaru; urut sisa hari menaik | `DataTable` + StatusPill; tab "Per produk" menggabungkan lot lintas outlet (satu baris = produk, kolom outlet = jumlah lot) untuk keputusan transfer | Status dari ambang kategori Odoo (bukan Insight) | **Belum aktif**: sama dengan ST-2. **Kosong positif:** "Tidak ada lot mendekati kedaluwarsa pada kategori Anda." | Lot/produk di Odoo; "Buat markdown" bila tersedia |
| CM-5 Harga vs Price Hunter | Per SKU yang punya data pasar: Harga kita (rata-rata outlet atau per outlet bila harga beda), Median pasar (30 hari tervalidasi), Selisih %, Jumlah sampel (n), Tanggal sampel terakhir. Hanya tampil bila n ≥ batas minimum (usulan 3; Q-INS-S8). Pembanding Price Hunter adalah harga kompetitor, **bukan per PT** (tanpa row policy PT; tetap tampil per sesi) | 30 hari | Tabel + scatter opsional (X = median pasar, Y = harga kita, garis diagonal) dengan tabel alternatif | Selisih > +10% = Perlu dicek ("Di atas pasar"); > +20% = Bermasalah; < −10% = Info ("Di bawah pasar: margin mungkin terbuang") (usulan). Selalu ikon + kata | **Kosong:** "Belum ada data harga pasar untuk kategori ini" (Price Hunter hanya mencakup SKU yang difoto komunitas). Cakupan ditampilkan ("harga pasar tersedia untuk 38 dari 210 SKU") agar angka tidak menyesatkan | Produk di Odoo (harga jual/pricelist) |
| CM-6 Sinyal terlalu mahal | SKU yang masuk segmen "sensitif harga": dilihat (pengunjung), dibeli, rasio lihat→beli, harga kita vs median pasar/median beli member. **Hanya agregat ≥ k pengunjung** (default k = 10; di bawah → "< 10") | 30 hari | `DataTable`, kolom rasio dengan bar mini | Rasio lihat→beli < setengah median kategori = Perlu dicek (usulan) | **Pelacakan nonaktif:** "Pelacakan perilaku belum aktif; kartu ini terisi setelah ada persetujuan DPO." Sertakan catatan: "Hanya mencakup pengunjung yang menyetujui pelacakan" | SKU di Odoo; tautan "Usulan kampanye" ke `efn_campaign` |

### 4.4 DC — `/dc`

**Siapa & kapan:** tim DC dan buyer DC; operasional harian, cut-off pengiriman, kunjungan supplier.

| Pertanyaan | Tindakan |
|---|---|
| Berapa persen permintaan outlet yang berhasil kami penuhi? | Cari akar penyebab baris tak terpenuhi (stok kurang, terlambat PO), prioritaskan picking |
| Order outlet apa yang masuk untuk pengiriman berikutnya, dan mana yang belum disetujui? | Mulai picking untuk yang disetujui; hubungi outlet yang belum menyetujui menjelang cut-off |
| Stok DC mana yang menua? | Dorong keluar ke outlet (transfer), tahan pembelian, retur ke supplier |
| PO supplier mana yang belum dikonfirmasi atau terlambat? | Konfirmasi RFQ (buyer DC), tagih supplier |

```
+---------+---------------------------------------------------------------+
| sidebar | [Periode 30 hari v] [Outlet tujuan v] [Kategori v] Data 14:45 |
|         | DC — Apakah pesanan outlet terpenuhi dan stok kita sehat?     |
|         | +-----------+-----------+-------------+---------------+        |
|         | | Fill rate | Order     | Stok > 30 h | PO terlambat  |  DC-1 |
|         | | 94,2%     | besok: 5  | Rp 210 jt   | 3             |       |
|         | | -1,1 pp   | 3 disetujui| 11% nilai   |               |       |
|         | +-----------+-----------+-------------+---------------+        |
|         | +-------------------------------+ +-------------------------+ |
|         | | DC-2 Order outlet (hari kirim)| | DC-3 Fill rate: tren +  | |
|         | | tabel outlet x status, batas  | | per outlet (bar)        | |
|         | +-------------------------------+ +-------------------------+ |
|         | +-------------------------------+ +-------------------------+ |
|         | | DC-4 Umur stok (bucket)       | | DC-5 PO supplier        | |
|         | | bar tumpuk + tabel produk tua | | tabel status & terlambat| |
|         | +-------------------------------+ +-------------------------+ |
+---------+---------------------------------------------------------------+
```

| ID | Metrik & definisi | P/B | Visual | Ambang | Kosong / Terlambat | Drill-down |
|---|---|---|---|---|---|---|
| DC-1 Baris KPI (4) | Fill rate DC, Order outlet (jumlah usulan hari kirim berikutnya dan berapa Disetujui), Umur stok > 30 hari (Rp & % dari nilai stok DC; ambang hari per kategori bila ada), PO terlambat (jumlah) | 30 hari (fill rate); snapshot (lainnya); pembanding 30 hari sebelumnya | KpiTile | Fill rate ≥ 95% Baik, 90–95 Perlu dicek, < 90 Bermasalah (usulan; Q-INS-S4). PO terlambat: 0 Baik, 1–2 Perlu dicek, ≥ 3 Bermasalah | Fill rate kosong bila belum ada pengiriman: "Belum ada pengiriman DC → outlet pada periode ini" | Gulir ke kartu |
| DC-2 Order outlet | Tabel pengiriman berikutnya (tanggal kirim terdekat default; tab hari lain): Outlet, Status usulan, Batas persetujuan, Jumlah baris, Total qty, Perubahan dari sistem (baris/qty). Sorot usulan yang belum disetujui dan batasnya < 2 jam | Hari kirim terdekat | `DataTable` + StatusPill + kolom "Batas" (absolut WIB) | Belum disetujui & batas ≤ 2 jam = Bermasalah; Kedaluwarsa = Bermasalah ("Tidak dikirim") | **Kosong:** "Tidak ada usulan untuk hari kirim ini." **Terlambat:** > 30 menit → spanduk kartu | Daftar usulan (Odoo) terfilter hari kirim |
| DC-3 Fill rate | Tren harian/mingguan + per outlet (bar horizontal terurut menaik), garis acuan 95% dan 90%. Catatan: "Fill rate dihitung terhadap qty yang disetujui outlet" | 30 hari; pembanding periode lalu | Garis + bar | Warna bar menurut status + ikon di label sumbu | **Kosong:** sama DC-1 | Klik outlet → filter |
| DC-4 Umur stok | Stok DC dikelompokkan bucket sisa umur lot (usulan: Kedaluwarsa, ≤ 7 hari, 8–30, 31–90, > 90) atau lama di gudang untuk SKU non-perishable (Q-INS-S10). Bar tumpuk satu batang per kategori (maks 8) menunjukkan Rp per bucket + tabel produk/lot tertua (10 baris) | Snapshot | Bar tumpuk horizontal (skala sekuensial 4 langkah, label bucket di legenda DAN pola opsional), tabel di bawah | Kedaluwarsa = Bermasalah; ≤ 7 hari = Perlu dicek | **Kosong:** "Tidak ada stok DC tercatat dengan lot." | Lot/stok di Odoo |
| DC-5 PO supplier | Tabel RFQ/PO DC: Supplier, Nomor, Status (Draf RFQ, Dikonfirmasi, Diterima sebagian, Terlambat), Tanggal terjanji, Nilai, Jumlah baris. RFQ draft dari orderpoint DC tampil "Draf — menunggu buyer" | PO terbuka; urut Terlambat dulu lalu tanggal terjanji | `DataTable` + StatusPill | Terlambat (lewat tanggal terjanji, belum diterima penuh) = Bermasalah; Draf melewati jadwal kunjungan supplier = Perlu dicek | **Kosong:** "Tidak ada PO terbuka." | Buka RFQ/PO di Odoo ("Buka RFQ di Odoo" membawa buyer ke konfirmasi; **SoD**: Insight tidak mengonfirmasi) |

### 4.5 Marketing — `/marketing`

**Siapa & kapan:** tim marketing/CRM; evaluasi mingguan, menyusun kampanye. Seluruh halaman memuat dua batasan jujur: (1) hanya pengunjung yang memberi consent `tracking`; (2) segmen kecil ditekan (k-anonim).

| Pertanyaan | Tindakan |
|---|---|
| Di tahap mana pengunjung berhenti (lihat → keranjang → checkout → beli)? | Perbaiki tahap itu (harga, stok, ongkos kirim, UX), minta tim produk meninjau |
| Segmen mana besar dan layak digarap? | Buka usulan kampanye di Odoo `efn_campaign`, setujui/tolak, atur voucher |
| Kampanye yang sudah jalan menghasilkan apa? | Hentikan/ulangi/ubah kampanye; evaluasi biaya voucher vs anggaran |

```
+---------+---------------------------------------------------------------+
| sidebar | [Periode 30 hari v] [Outlet v] [Kanal: web/app v]   Data 14:45|
|         | [i] Hanya pengunjung yang menyetujui pelacakan (62% perangkat)|
|         | Marketing — Pengunjung berhenti di mana dan kampanye apa yang |
|         | bekerja?                                                      |
|         | +-----------+----------+-----------+--------------+          |
|         | | Pengunjung| Konversi | Voucher   | Biaya vs     |   MK-1   |
|         | | 12,4 rb   | 2,1%     | dipakai   | anggaran     |          |
|         | |           |          | 380       | 62%          |          |
|         | +-----------+----------+-----------+--------------+          |
|         | | MK-2 Funnel (bar horizontal 4 tahap, % dari tahap awal, % dari|
|         | |      tahap sebelumnya, tabel alternatif)                     |
|         | +--------------------------------------------------------------+
|         | | MK-3 Segmen (5 kartu-bar: ukuran, tren 7 hari, usulan kampanye)
|         | +--------------------------------------------------------------+
|         | | MK-4 Hasil kampanye (tabel: nama, kanal, status, jangkauan,   |
|         | |      voucher dipakai, konversi, inkremental, biaya, anggaran)|
|         | +--------------------------------------------------------------+
+---------+---------------------------------------------------------------+
```

| ID | Metrik & definisi | P/B | Visual | Ambang | Kosong / Terlambat | Drill-down |
|---|---|---|---|---|---|---|
| MK-0 Pita cakupan consent | Teks permanen di atas halaman: "Angka di halaman ini hanya mencakup pengunjung yang menyetujui pelacakan (≈ N% perangkat)". N% = perangkat dengan consent aktif / perangkat yang melihat banner (bila tersedia) | Periode filter | Spanduk informasi (`role="note"`), bukan peringatan | Tanpa status | Bila flag tracking mati: halaman memuat **hanya** EmptyState besar "Pelacakan perilaku belum aktif" (tidak menampilkan KPI nol) + keterangan syarat (persetujuan DPO) | — |
| MK-1 Baris KPI (4) | Pengunjung (perangkat unik ber-consent), Konversi (pembeli / pengunjung), Voucher dipakai, Biaya voucher vs anggaran (%) | Periode filter; pembanding periode lalu | KpiTile | Biaya > 90% anggaran = Perlu dicek; > 100% = Bermasalah (usulan; hanya bila cap anggaran dikonfigurasi, jika tidak: tanpa status, Q-SM24) | Konversi: bila pengunjung < k, tampil "< 10" dan delta disembunyikan | — |
| MK-2 Funnel visitor | Tahap: Lihat produk → Wishlist atau Keranjang → Mulai checkout → Beli. Tampilkan jumlah, % dari tahap awal, % dari tahap sebelumnya. Dipecah web/app lewat toggle (bukan dua funnel sekaligus) | Periode filter | **Bar horizontal berurutan** (lebar proporsional, tidak corong/Sankey); label jumlah + % di dalam/akhir batang; tabel alternatif; tahap dengan penurunan terbesar diberi teks "Penurunan terbesar" | Tanpa status warna; penanda teks pada penurunan terbesar | **Kosong:** "Belum ada event perilaku pada periode ini." **Tahap < k:** "< 10" (jangan ekstrapolasi). **Terlambat:** aturan umum (event staging 7 hari ke ClickHouse via ETL 15 menit) | Klik tahap → filter MK-3 menurut tahap terakhir yang dicapai |
| MK-3 Segmen | 5 segmen harian: Lihat-tidak-beli, Wishlist-menganggur, OOS-dilihat, Sensitif harga, Cari-tanpa-hasil. Per segmen: ukuran terbaru, tren 7 hari (sparkline), top 3 kategori/produk terkait (agregat), tindakan "Usulan kampanye" bila ada | Hari terbaru + 7 hari | Satu kartu per segmen berisi angka, sparkline, definisi satu kalimat ("Melihat produk ≥ 2 kali dalam 7 hari tanpa membeli") | Tanpa status. Segmen < k: "< 10 pengunjung, ditampilkan agar tidak dapat dikenali" | **Kosong:** "Segmen belum dihitung hari ini" bila job harian belum jalan | "Buka usulan kampanye di Odoo" (daftar `efn_campaign` status Usulan). Tidak ada daftar pengunjung/member |
| MK-4 Hasil kampanye | Per kampanye: Nama, Kanal (push/email/WA), Status (Usulan, Disetujui, Berjalan, Selesai, Dihentikan), Jangkauan (terkirim), Voucher dipakai, Konversi, Omzet inkremental (hanya bila ada kelompok kontrol; selain itu kolom "Tidak dihitung"), Biaya voucher, Anggaran | Kampanye berjalan + selesai dalam periode | `DataTable` + StatusPill status; kolom inkremental dengan info "i" definisi | Biaya vs anggaran seperti MK-1 | **Kosong:** "Belum ada kampanye pada periode ini." **Belum aktif:** `PROMO_CAMPAIGN_ENABLED=false` → "Fitur kampanye belum aktif" | Kampanye di Odoo |

---

## 5. Pola lintas halaman

### 5.1 Komponen kartu standar G47
Setiap kartu persona memakai satu kerangka (`InsightCard`): judul `h2`, baris kesegaran kartu ("Data per 14:45"), isi, baris aksi ("Buka di Odoo", "Tampilkan sebagai tabel", "Ekspor CSV"). Urutan fokus keyboard: judul (bukan fokus), kontrol isi, aksi. Kartu yang memicu tindakan membawa **label tindakan yang spesifik** ("Buka usulan di Odoo", bukan "Lihat selengkapnya").

### 5.2 Kesegaran data (ETL 15 menit) — satu aturan, dua kelas
Kesegaran dihitung dari watermark mart yang memberi makan kartu itu (bukan jam browser; selisih dihitung server). Teks selalu memuat **jam absolut WIB** dan umur relatif: "Data per 14:45 WIB (12 menit lalu)". Dua kelas ambang (disimpan di `lib/thresholds.ts`; usulan):

| Kelas | Kartu | Baik | Perlu dicek (spanduk kartu, ikon + teks) | Bermasalah |
|---|---|---|---|---|
| Hampir-langsung (ETL 15 menit) | ST-1, ST-3, DC-2, DC-5, CM-2/3 | ≤ 30 mnt | 30–60 mnt: "Data mungkin tertinggal; cek di Odoo sebelum bertindak" | > 60 mnt: nilai penting diganti "Tidak pasti" (ST-1: status disembunyikan); kartu tetap menampilkan data lama berlabel "usang" |
| Harian / cron | ST-2 (cron harian), ST-4, EX-*, CM-4..6, DC-1/3/4, MK-* | ≤ 26 jam | 26–48 jam | > 48 jam atau run terakhir gagal |

- Badge kesegaran global di header (G42 §3.1) = **terburuk** dari kartu halaman itu; klik membuka popover per kartu/mart.
- Bila ETL mati total: semua kartu mempertahankan angka terakhir dengan label "usang" dan satu spanduk halaman `role="status"`; tidak ada kartu menyatakan "0" karena data belum masuk. Prinsip G42 dipertahankan: kesegaran tidak diketahui = "Tidak diketahui", bukan "Baik".
- Pembedaan wajib antara **nol sungguhan**, **belum ada data**, **fitur belum aktif**, dan **data usang** — empat teks berbeda (ringkasan di §6.5).

### 5.3 Tautan ke Odoo (ActionLink)
- Teks aksi + ikon panah keluar (SVG, `aria-hidden`) + `sr-only` "(tab baru)". `target="_blank"` `rel="noopener noreferrer"`.
- Dasar URL = `EFN_INSIGHT_ODOO_URL` (sudah ada). Rute Odoo diverifikasi engineer; Insight hanya memakai ID yang disediakan view. Tautan tanpa ID → ke daftar terfilter (menu), bukan rekaman.
- Tidak ada pra-pengisian data sensitif di URL. Bila pengguna tak punya hak di Odoo, Odoo menolak; teks kecil di bawah tombol pertama per halaman: "Tindakan membutuhkan akses Odoo sesuai peran Anda."

### 5.4 Ekspor
CSV per tabel (G42 §7: BOM UTF-8, proteksi formula). Halaman Kepala toko: tanpa ekspor (kebutuhan rendah, ponsel). Ekspor mematuhi scope yang sama dengan tampilan (row policy); k-anonim tetap berlaku di CSV ("< 10" teks, bukan angka).

---

## 6. Aksesibilitas, bahasa, format

### 6.1 WCAG 2.2 AA — persyaratan khusus halaman ini
| Kriteria | Penerapan |
|---|---|
| 1.4.1 Penggunaan warna | Setiap status = ikon + kata (StatusPill); heatmap selalu menampilkan angka; peringkat ekstrem diberi ikon/teks. Funnel & bar tumpuk punya label langsung + pola opsional (toggle "Pola"; otomatis `prefers-contrast: more`) |
| 1.4.3 / 1.4.11 Kontras | Memakai token G42 tanpa warna baru (kontras pasangan status lihat §6.2). Border input memakai `--text-muted` (≥ 3:1). Tanda grafik ≥ 3:1 terhadap `--surface` |
| 1.1.1 / 1.3.1 Alternatif grafik | Tiap `ChartCard` punya "Tampilkan sebagai tabel"; heatmap dan funnel dirender sebagai `<table>` sungguhan (heatmap = tabel berwarna dengan `<th scope>` baris/kolom). Ringkasan satu kalimat otomatis di `aria-label` ("OOS rate tertinggi: kategori Susu di outlet Cibubur, 6,1%") |
| 1.3.4 / 1.4.10 Reflow | Dioptimalkan 360 px; 320 px tanpa gulir dua arah kecuali tabel lebar dalam wadah `tabindex="0"` ber-`aria-label`. Pada ≤ 480 px, tabel ST-2/ST-3/CM-3 menjadi **daftar kartu-baris** (`<ul>`, tiap item `<dl>` dengan label tetap terlihat), bukan tabel yang digeser |
| 1.4.12 Spasi teks | Kartu tidak memotong teks pada line-height 1,5 dan jarak huruf +0,12em; tidak ada tinggi tetap pada kartu berisi teks |
| 2.4.11 Fokus tidak tertutup | Header + jangkar lengket (halaman Toko) dihitung dalam `scroll-padding-top`; diuji pada Tab ke tautan di bawah |
| 2.4.3 Urutan fokus | Urutan DOM = urutan visual = urutan prioritas; tidak ada `tabindex` positif; jangkar loncat memindahkan fokus ke `h2` kartu (`tabindex="-1"`) |
| 2.5.8 Ukuran target | Kontrol utama ≥ 44 × 44 px di halaman Toko (jangkar, tombol Buka di Odoo 48 px, tab outlet); ≥ 40 px di halaman lain; tombol hapus chip ≥ 24 px dengan jarak |
| 2.5.7 Gerakan seret | Tidak ada fungsi yang butuh seret |
| 3.3.7 Entri berulang | Filter tersimpan di URL dan dibawa antar halaman persona |
| 4.1.3 Pesan status | Perubahan data oleh filter diumumkan `aria-live="polite"` ("Data diperbarui: 5 outlet, 21 Okt – 19 Nov 2026"). Hitung mundur cut-off **tidak** live; hanya satu pengumuman `role="status"` saat melewati batas |
| Bahasa | `<html lang="id">`; singkatan "WAPE", "GMROI", "OOS", "HPP" dijelaskan lewat tombol "i" (bukan `title`) pada pemakaian pertama di halaman |
| Gerak | Animasi chart & denyut skeleton mati bila `prefers-reduced-motion` |

### 6.2 Kontras status yang dihitung (WCAG 2.x, rumus luminans relatif)
Pasangan teks status di atas latar `*-soft` (StatusPill), yang di G42 §2.2 belum dihitung:

| Mode | Pasangan | Rasio | Syarat | Lolos |
|---|---|---|---|---|
| Terang | `--good` #0f7a3d di `--good-soft` #e2f4e9 | 4,74 | 4,5 | ya (tipis) |
| Terang | `--warn` #8a5a00 di `--warn-soft` #fdf0d5 | 5,25 | 4,5 | ya |
| Terang | `--bad` #b42318 di `--bad-soft` #fde7e4 | 5,55 | 4,5 | ya |
| Gelap | `--good` #4ade80 di `--good-soft` #0f3a25 | 7,30 | 4,5 | ya |
| Gelap | `--warn` #fbbf24 di `--warn-soft` #41310a | 7,54 | 4,5 | ya |
| Gelap | `--bad` #ff8a80 di `--bad-soft` #4a1a17 | 6,34 | 4,5 | ya |

Hasil dihitung manual; **tipis pada `--good` terang (4,74)**: jangan menggelapkan `--good-soft` atau menaruh teks `--good` ukuran < 14 px di atas latar selain `--surface`/`--good-soft`. Test Vitest (`config-contrast.test.ts`) perlu memuat pasangan ini. Untuk heatmap/skala diverging, teks sel dipilih otomatis oleh `lib/contrast.ts` (sudah ada); rancangan ini tidak menambah warna.

### 6.3 Bahasa dan nada
Tenang, lugas, orientasi tindakan; tanpa jargon teknis dan tanpa menyalahkan.
- Judul halaman: pertanyaan atau pernyataan tugas ("Toko saya", subjudul nama outlet).
- Aksi: kata kerja ("Buka usulan di Odoo", "Buat markdown di Odoo").
- Peringatan: akibat + tindakan ("Batas persetujuan 15:00 WIB. Setelah itu usulan tidak dikirim otomatis.").
- Hindari: "Gagal!", huruf kapital seluruh kalimat (kecuali label status singkat di pil), istilah "slice", "mart", "watermark" (gunakan "pembaruan data").
- Istilah baku: Omzet, Margin kotor, HPP, Waste → dalam UI gunakan **"Susut"** dengan "(waste)" hanya pada kartu Direksi pertama kali; Outlet (bukan "cabang" pada supermarket; "cabang" tetap di halaman F&B), Usulan order, Batas persetujuan, Kedaluwarsa, Markdown (istilah serapan diterima manajemen ritel; deskripsi "potongan harga lot").

### 6.4 Format angka (id-ID; ikut `lib/format.ts`)
| Kasus | Aturan | Contoh |
|---|---|---|
| Rupiah ringkas/penuh | G42 §1 | `Rp 4,2 M`, `Rp 54.000` |
| Persentase | 1 desimal, koma; selisih rasio = "pp" | `94,2%`, `-1,1 pp` |
| GMROI | 1 desimal + "x" | `2,8x` |
| Cakupan stok | 1 desimal + "hari" | `4,5 hari` |
| Sisa umur lot | bilangan bulat + "hari"; 0 = "hari ini"; negatif = "lewat 2 hari" | `sisa 3 hari`, `ED hari ini` |
| Jam | 24 jam dengan titik dua, selalu diikuti "WIB" (`Intl` id-ID memakai titik, `14.45`; spesifikasi memilih titik dua karena lebih terbaca di layar kecil dan seragam dengan Odoo; keputusan akhir **Q-INS-S11**) | `14:45 WIB` |
| Hitung mundur | "tersisa 3 j 15 m"; < 1 jam "tersisa 42 m"; setelah batas "batas lewat 12 m" | |
| Kuantitas | Pecahan dalam satuan SKU bila relevan (kg) | `12,5 kg` |
| Nilai di bawah k-anonim | teks `< 10`, bukan 0 | `< 10` |
| Nilai kosong | tanda "–" **hanya** dengan teks sr-only ("tidak ada pembanding"); jangan "0" untuk data belum ada | |

### 6.5 Empat keadaan "tidak ada angka" (teks baku)
| Keadaan | Teks (contoh) | Ikon | Berbeda dari |
|---|---|---|---|
| Nol sungguhan | "Tidak ada barang mendekati kedaluwarsa." | centang | — |
| Belum ada data | "Belum ada data untuk periode ini. Data masuk tiap 15 menit." | jam | nol |
| Fitur belum aktif | "Pemantauan kedaluwarsa belum diaktifkan untuk outlet ini." / "Pelacakan perilaku belum aktif." | info | nol (jangan tampilkan 0) |
| Data usang | "Data terakhir 14:45 WIB (3 jam lalu); mungkin belum mencakup transaksi terbaru." | segitiga | nol |

### 6.6 Privasi di UI (UU PDP)
- Tidak ada nama/telepon/email member di halaman mana pun; funnel/segmen hanya agregat; nilai < k ditekan (default k = 10, Q-SM17); **tautan antara segmen dan daftar individu tidak ada** (daftar penerima kampanye tidak pernah masuk Insight; L-155).
- Ekspor CSV mengikuti penekanan k.
- Tooltip/chart tidak menampilkan `device_key`/`subject_key`.
- Catatan consent (MK-0) adalah bagian dari kejujuran data dan pengakuan batas pelacakan, bukan sekadar disclaimer.

---

## 7. Hal-hal yang menuntut kejujuran copy

1. **Forecast = usulan.** EX-6 dan ST-1 menyebut "usulan", bukan "perintah"; qty final = persetujuan manusia.
2. **Hasil kampanye = korelasi kecuali ada kelompok kontrol.** Kolom inkremental hanya bila kelompok kontrol ada; selain itu "Tidak dihitung". Jangan menamai konversi biasa sebagai "dampak".
3. **Harga vs pasar = sampel komunitas** (Price Hunter), bukan survei lengkap; selalu tampil n dan cakupan.
4. **Pajak:** supermarket = ritel, PPN. Label **"Omzet"** harus menyatakan basis: bila angka mart tanpa PPN → "Omzet (tanpa PPN)"; bila dengan PPN → "Omzet (termasuk PPN)". Spesifikasi tidak memilih; **Q-INS-S12**. Jangan memakai "PB1" (pajak restoran) di halaman supermarket. HPP dan margin harus pada basis yang sama dengan Omzet.
5. **Hari ini ≠ sampai tutup toko.** Angka "hari ini" memakai "s.d. 14:45".

---

## 8. Komponen baru atau perluasan (acuan efn-frontend-engineer)

Semua membaca token G42; tidak ada warna baru. State `loading/empty/error` mengikuti G42 §4.0.

| Komponen | Props ringkas | Catatan a11y |
|---|---|---|
| `InsightCard` | `{ id: string; title: string; asOf: string \| null; freshnessClass: 'near-live' \| 'daily'; state: 'ready' \| 'loading' \| 'empty' \| 'error' \| 'stale' \| 'disabled' \| 'suppressed'; emptyVariant?: 'zero' \| 'no-data' \| 'feature-off'; actions?: ActionLinkProps[]; children }` | `<section aria-labelledby>`; `stale` menambah spanduk teks; `disabled` = fitur belum aktif |
| `ActionLink` | `{ label: string; href: string; external?: true }` | `sr-only` "(tab baru)"; min tinggi 44/48 px di `/store` |
| `CutoffCountdown` | `{ cutoffAtIso: string; tz: 'Asia/Jakarta'; state: ProposalState }` | Menampilkan waktu absolut + sisa; update per menit; tanpa `aria-live`; setelah batas teks "Batas lewat…" + satu `role="status"` |
| `ExpiryRow` (kartu-baris) | `{ product: string; lot: string; qty: number; daysLeft: number; hppValue: number; status: 'pantau'\|'markdown-now'\|'markdown-active'\|'expired'; markdown?: { kind: 'pct'\|'price'; value: number; until: string }; outlet?: string }` | `<li>` dengan `<dl>`; status = StatusPill |
| `RankHeatmap` | `{ rows: string[]; cols: string[]; cells: { value: number \| null; display: string; rank?: number }[][]; direction: 'higher-worse' \| 'higher-better'; scale: 'seq' }` | `<table>`; sel = angka + ikon ekstrem; navigasi panah 2D; legenda; `null` = "n/a" |
| `FunnelBars` | `{ steps: { label: string; count: number \| null; suppressed?: boolean }[] }` | Bar horizontal + tabel alternatif; tahap `suppressed` menampilkan "< 10" |
| `SuppressedValue` | `{ value: number \| null; k: number }` | Menampilkan `< 10` bila di bawah k; `title` tidak dipakai; `sr-only` "kurang dari 10, disembunyikan demi privasi" |
| `PersonaChip` | `{ role: Role; scope: string }` | Teks "Kepala toko · Outlet Kelapa Gading" |
| `ScopeBanner` (G42 `FilterNotice` diperluas) | `{ outletCount: number; roleLabel: string }` | Hanya teks; tampil bila ada >1 peran |

Perluasan `lib/thresholds.ts` (nama usulan; semua dikonfigurasi): `oosWarnPct`, `oosBadPct`, `wasteWarnPct`, `wasteBadPct`, `wapeGoodPct`, `wapeWarnPct`, `biasWarnPct`, `biasBadPct`, `fillGoodPct`, `fillWarnPct`, `priceAboveWarnPct`, `priceAboveBadPct`, `cutoffUrgentMinutes` (120), `nearLiveWarnMinutes` (30), `nearLiveBadMinutes` (60), `kAnonymity` (10), `minPriceSample` (3). Ambang near-expiry **bukan** di sini: dari konfigurasi kategori Odoo (`efn_near_expiry_days`, `efn_markdown_days`) lewat kontrak data.

---

## 9. Prioritas MVP

Kriteria: **(a)** memenuhi skenario demo supermarket (kepala toko menyetujui usulan, markdown, direksi melihat), **(b)** data tersedia dari G44–G46/G48, **(c)** risiko salah-baca rendah.

| Prioritas | Isi |
|---|---|
| **P0 — wajib demo** | Seluruh `/store` (ST-1..ST-4) di ponsel; `/exec` EX-1 (Omzet, Margin kotor, Waste %, Galat forecast; GMROI & YoY ubin ada namun boleh menampilkan "Menunggu data"), EX-2, EX-3; `/category` CM-1 (OOS rate, Near-expiry, Markdown aktif), CM-2, CM-4; `/dc` DC-1 (Fill rate, Order outlet), DC-2, DC-3; `/marketing` MK-0, MK-1, MK-2 (bila tracking menyala di demo; jika tidak, hanya EmptyState "belum aktif"). Navigasi persona, PersonaChip, perilaku satu outlet, kesegaran per kartu, 4 keadaan kosong, ActionLink |
| **P1 — setelah demo, sebelum pilot** | GMROI dan YoY dengan data nyata; EX-4, EX-5, EX-6; CM-3, CM-5, CM-6; DC-4, DC-5; MK-3, MK-4; ekspor CSV; hitung mundur ST-1 pembaruan tiap menit; ST-5 |
| **P2 — fase berikut** | Penanda kalender (Gajian/Ramadan) di EX-2; scatter harga CM-5; perbandingan kanal; peta panas per jam (butuh grain jam); notifikasi di luar Insight; pencarian global; mode kios layar besar untuk DC |

Dependensi pemblokir: row policy `eit2` (§28.11) lulus review security sebelum persona `store` bisa diuji; `BEHAVIOR_TRACKING_ENABLED`/`PROMO_CAMPAIGN_ENABLED` mati di produksi (MK-* tampil sebagai "belum aktif" sampai DPO setuju).

---

## 10. Pertanyaan terbuka untuk bisnis dan tim

| ID | Pertanyaan | Pemilik | Dampak bila belum |
|---|---|---|---|
| **Q-INS-S1** | Definisi GMROI: margin kotor ÷ **rata-rata** nilai persediaan pada HPP (harian atau akhir bulan?), pakai persediaan DC + outlet atau outlet saja? | Finance/Direksi | EX-1 GMROI tidak bisa dihitung konsisten |
| **Q-INS-S2** | Definisi dan ambang OOS rate: stok nol pada akhir hari atau sepanjang jam buka? Hanya SKU assortment aktif? Ambang baik/peringatan (usulan 2%/5%)? Apakah per kategori berbeda (segar vs kering)? | Category manager | CM-1/2 status menyesatkan |
| **Q-INS-S3** | Definisi "stok kritis": ≤ minimum orderpoint, atau < N hari cakupan? Berapa N? | Ops/Kepala toko | ST-3 tidak jelas |
| **Q-INS-S4** | Fill rate DC: dihitung terhadap qty disetujui outlet atau qty diminta forecast? Per baris atau per pengiriman? Ambang (usulan 95%/90%)? | DC/Ops | DC-1/3 |
| **Q-INS-S5** | Ambang WAPE/bias yang wajar untuk SKU × outlet × hari vs agregat mingguan (usulan WAPE 25%/40%, bias ±5%/±10%) dan tingkat agregasi yang ditampilkan Direksi | Planner/Direksi | EX-1, EX-6 |
| **Q-INS-S6** | Apakah Odoo menyimpan "kategori yang dikelola" per Category manager (untuk default filter dan row policy kategori)? Bila tidak: semua kategori, dan akses kategori tidak dibatasi | Architect/Odoo functional | CM default |
| **Q-INS-S7** | Waktu cron near-expiry dan stok: harian jam berapa? Perlu pembaruan tengah hari untuk kepala toko? | Odoo functional | ST-2 menampilkan "dihitung 06:00" benar atau tidak |
| **Q-INS-S8** | Ukuran sampel minimum Price Hunter untuk menampilkan harga pasar (usulan n ≥ 3 dalam 30 hari) dan batas "terlalu mahal" (+10% / +20%) | Category manager | CM-5/6 |
| **Q-INS-S9** | Apakah peran `exec` boleh membuka halaman persona lain (hak akses halaman, bukan scope data)? | Security/Direksi | §2.1 |
| **Q-INS-S10** | Umur stok DC: bucket menurut sisa umur lot (perishable) atau lama di gudang (kering)? Batas bucket per kategori | DC | DC-4 |
| **Q-INS-S11** | Format jam: `14:45` (usulan) atau `14.45`? Kesepakatan dengan Odoo | Bisnis | Konsistensi |
| **Q-INS-S12** | Basis pajak Omzet: sebelum atau sesudah PPN? HPP dan margin pada basis yang sama? | Finance | Label EX-1 dan seluruh angka rupiah |
| **Q-INS-S13** | Kepala toko dengan beberapa outlet: perlu tampilan gabungan atau selalu satu outlet per tab? | Ops | Bagian 3 |
| **Q-INS-S14** | Kampanye: jendela atribusi (berapa hari setelah kirim dihitung konversi) dan apakah kelompok kontrol akan ada (menentukan "Omzet inkremental") | Marketing | MK-4 |
| **Q-INS-S15** | Apakah angka usulan order (qty) perlu tampil di Insight, atau cukup status dan jumlah baris (kepala toko mengubah qty di Odoo)? Spesifikasi ini: hanya status dan jumlah baris. | Ops | ST-1 |

---

## 11. Yang belum diverifikasi
- Seluruh dokumen = rancangan; belum ada prototipe, belum diuji dengan kepala toko sungguhan, pembaca layar, atau ponsel.
- Rasio kontras §6.2 dihitung manual (rumus WCAG); belum diulang oleh test otomatis.
- Nama metrik tidak dipetakan ke kolom; kecocokan dengan kontrak architect belum diperiksa (ditulis paralel).
- Format URL Odoo 19 untuk tautan dalam (rekaman/daftar terfilter) belum diverifikasi.
- Ambang dan bucket semua = usulan; perilaku "terlambat" mengasumsikan watermark per mart tersedia dari kontrak data.
- Daftar peran, nama grup Odoo, dan label status markdown mengikuti §28.4/§28.5a dan fit-gap G45 yang statusnya "belum dibangun" untuk `efn_expiry_monitor` dan `efn_campaign`.
