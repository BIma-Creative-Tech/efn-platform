# Spesifikasi UI — EFN Insight (dashboard staf)

Status: DRAF G42 (18 Nov 2026), ditulis efn-uiux-designer untuk efn-frontend-engineer. Kontrak: G42_CONTRACT bagian C; data: `infra/clickhouse/init/12-g41-esb.sql` (lewat view `efn_insight.v_esb_*`, row policy per `company_code`). Semua angka dalam contoh = ilustrasi bulat, bukan data klien.

Yang dihitung di dokumen ini adalah rasio kontras WCAG 2.x (rumus luminans relatif) untuk pasangan token utama; yang ditandai "diperiksa test" harus diverifikasi lagi oleh test Vitest (helper kontras sudah ada di `superapp-web/tests/brands.test.ts`, salin). Belum diuji di browser, pembaca layar, atau perangkat nyata.

## 1. Prinsip

**Audiens & tugas**
| Peran | Tugas utama | Halaman utama |
|---|---|---|
| Manajemen holding (grup `all_brands`) | Membandingkan brand/PT, melihat tren & anomali | Ringkasan, Penjualan, Kualitas Data |
| Manajer brand / outlet | Cabang saya kalah/menang di mana, menu apa laku, bayar apa | Ringkasan, Penjualan & Menu, Pembayaran |
| Planner CK | Produksi sesuai kebutuhan? Barang mengendap di toko? | Produksi vs Penjualan, Distribusi |

**Prinsip rancangan**
1. **Jawab pertanyaan, bukan pamer grafik.** Tiap halaman diawali satu kalimat pertanyaan bisnis (subjudul) dan angka jawabannya di baris KPI paling atas.
2. **Padat tetapi bernapas.** Desktop-first (min. 1280 px dioptimalkan; grid 12 kolom, gutter 16). Kartu KPI 1 baris, chart 2 per baris, tabel penuh lebar. Tablet (768-1279): chart 1 kolom, sidebar jadi ikon (rail). HP (<768): sidebar jadi drawer, KPI 2 kolom, chart 1 kolom, tabel scroll horizontal dengan kolom pertama lengket (sticky).
3. **Satu sumber warna per makna.** Warna brand/series konsisten di semua halaman (§3.3); status memakai warna + ikon + kata (tidak pernah warna saja).
4. **Jujur soal data.** Setiap halaman menampilkan kesegaran data; angka yang bergantung asumsi (mis. sell-through) punya ikon info dengan definisi.
5. **Aksesibel.** WCAG 2.2 AA: teks >= 4.5:1, komponen & tanda grafik >= 3:1, target sentuh >= 24 px (sasaran 40 px pada kontrol utama), fokus terlihat & tidak tertutup header lengket (`scroll-padding-top` = tinggi header), `prefers-reduced-motion` mematikan animasi chart, `prefers-color-scheme` + saklar manual (light / dark / ikuti sistem, disimpan di `localStorage` `insight-theme`).
6. **Bahasa Indonesia** untuk semua label; istilah baku: "Penjualan bersih", "Transaksi", "Rata-rata per transaksi", "HPP", "Cabang", "Periode lalu".
7. **Tanpa PII.** Hanya nama tampilan user (dari cookie sesi) yang tampil di header.

**Format angka (id-ID) — satu modul `lib/format.ts`, dipakai di KPI, tooltip, tabel, CSV label**
| Kasus | Aturan | Contoh |
|---|---|---|
| Rupiah ringkas (KPI, sumbu, label chart) | < 1.000.000: penuh; >= 1 jt: 1 desimal koma + "jt"; >= 1 miliar: "M"; >= 1 triliun: "T". Buang ",0". | `Rp 950.000`, `Rp 1,2 jt`, `Rp 12 jt`, `Rp 3,4 M` |
| Rupiah penuh (tabel, tooltip, CSV tampilan) | `Intl.NumberFormat('id-ID',{style:'currency',currency:'IDR',maximumFractionDigits:0})` lalu ganti `Rp` + spasi tipis biasa | `Rp 1.234.567` |
| Kuantitas | `Intl.NumberFormat('id-ID')`; desimal maks 1 bila non-bulat | `12.500`, `3,5` |
| Persen | 1 desimal, koma, tanpa spasi | `71,4%` |
| Delta | tanda eksplisit + panah, persen 1 desimal; titik persentase untuk rasio ("pp") | `+4,2%`, `-1,5 pp` |
| Tanggal | `d MMM yyyy`, bulan singkat: Jan Feb Mar Apr Mei Jun Jul Agu Sep Okt Nov Des | `18 Nov 2026` |
| Bulan | `MMM yyyy` | `Nov 2026` |
| Rentang | `1 Nov - 17 Nov 2026` (tahun sekali bila sama) | |
| Jam/umur data | `3 jam lalu`, `1 hari 2 jam lalu` | |
| CSV | angka mentah tanpa pemisah, titik desimal, tanggal ISO `2026-11-18`, BOM UTF-8, pemisah koma | |

Jangan bergantung ICU runtime Node untuk singkatan "jt/M": tulis sendiri; `Intl` hanya untuk pemisah.

## 2. Design token (CSS custom properties, siap salin)

Dasar = skema holding EFN (hijau tua `#062e2a`, teal `#0dab9d`, emas `#dfb422`; `services/superapp-web/lib/brands.ts` tenant `efn`). Insight adalah alat kerja padat data, jadi **mode terang adalah default** dan latar dibuat netral-sedikit-hijau agar tidak melelahkan; aksen holding dipakai sedikit (navigasi aktif, tombol primer, fokus). Emas TIDAK dipakai untuk teks di latar terang (gagal kontras); di mode gelap emas lolos.

### 2.1 Token inti

```css
:root, [data-theme='light'] {
  color-scheme: light;
  /* netral */
  --bg:            #f6f8f7;   /* latar halaman */
  --surface:       #ffffff;   /* kartu, tabel */
  --surface-2:     #eef3f1;   /* header tabel, chip, hover baris */
  --border:        #d5dedc;   /* garis kartu */
  --grid:          #e3eae8;   /* gridline chart (dekoratif, bukan satu-satunya penanda) */
  --text:          #10201e;
  --text-muted:    #4a5b58;
  /* aksen holding */
  --accent:        #0a5c54;   /* teks/ikon/tombol primer; putih di atasnya 7,86:1 */
  --accent-contrast:#ffffff;
  --accent-soft:   #dff0ec;   /* latar chip aktif, baris terpilih */
  --focus:         #0a5c54;   /* ring fokus 2px + offset 2px */
  /* chrome (sidebar) tetap gelap di kedua mode */
  --chrome:        #062e2a;
  --chrome-text:   #ffffff;
  --chrome-muted:  #bfe0da;
  --chrome-active: #dfb422;   /* penanda item aktif (batang 3px + teks putih) */
  /* status (selalu dipasangkan ikon + kata) */
  --good:          #0f7a3d;   --good-soft:#e2f4e9;
  --warn:          #8a5a00;   --warn-soft:#fdf0d5;
  --bad:           #b42318;   --bad-soft: #fde7e4;
  --info:          #0072b2;   --info-soft:#e0eff8;
  /* bentuk */
  --radius-sm: 6px; --radius: 10px; --radius-lg: 14px; --radius-pill: 999px;
  --shadow-1: 0 1px 2px rgba(6,46,42,.06), 0 1px 3px rgba(6,46,42,.08);
  --shadow-2: 0 8px 24px rgba(6,46,42,.14);           /* tooltip, popover, drawer */
  /* spasi 4px base */
  --s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:24px; --s-6:32px; --s-7:48px;
  /* tipografi */
  --font-body: 'Geist Variable', Inter, ui-sans-serif, system-ui, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'Geist Mono Variable', ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  --font-heading: var(--font-body);        /* Insight tidak memakai serif holding: alat kerja */
  --fs-xs:12px; --fs-sm:13px; --fs-md:14px; --fs-lg:16px; --fs-xl:20px; --fs-2xl:24px; --fs-kpi:28px;
  --lh-tight:1.2; --lh-body:1.5;
  --header-h: 56px; --sidebar-w: 232px; --sidebar-w-rail: 64px;
}

[data-theme='dark'] {
  color-scheme: dark;
  --bg:#051f1c; --surface:#0a2f2b; --surface-2:#10403a; --border:#2a6b63; --grid:#1b4a44;
  --text:#eef5f3; --text-muted:#a9c7c1;
  --accent:#dfb422; --accent-contrast:#062e2a; --accent-soft:#12433c; --focus:#dfb422;
  --chrome:#031715; --chrome-text:#ffffff; --chrome-muted:#a9c7c1; --chrome-active:#dfb422;
  --good:#4ade80; --good-soft:#0f3a25;
  --warn:#fbbf24; --warn-soft:#41310a;
  --bad:#ff8a80;  --bad-soft:#4a1a17;
  --info:#56b4e9; --info-soft:#0e3550;
  --shadow-1: 0 1px 2px rgba(0,0,0,.4);
  --shadow-2: 0 8px 24px rgba(0,0,0,.55);
}
@media (prefers-color-scheme: dark) { :root:not([data-theme]) { /* salin blok dark di sini */ } }
```

Font: sama dengan Super-App holding (Geist, self-host OFL, `font-src 'self'`); fallback stack sistem. Angka di tabel/KPI memakai `font-variant-numeric: tabular-nums`.

Skala tipografi: judul halaman `--fs-2xl`/600; judul kartu `--fs-lg`/600; badan `--fs-md`; label sumbu/tooltip `--fs-sm`; keterangan `--fs-xs` (hanya teks sekunder, kontras tetap >= 4.5:1); angka KPI `--fs-kpi`/600 tabular.

### 2.2 Kontras yang dihitung (WCAG 2.x)

| Pasangan | Rasio | Syarat | Lolos |
|---|---|---|---|
| Light: `--text` #10201e di `--surface` #fff | 16,8 | 4,5 | ya |
| Light: `--text` di `--bg` #f6f8f7 | 15,8 | 4,5 | ya |
| Light: `--text-muted` #4a5b58 di `--surface` | 7,17 | 4,5 | ya |
| Light: `--text-muted` di `--bg` | 6,72 | 4,5 | ya |
| Light: `--accent` #0a5c54 di `--surface`; putih di atas `--accent` | 7,86 | 4,5 / 3 | ya |
| Light: `--good` #0f7a3d / `--warn` #8a5a00 / `--bad` #b42318 di `--surface` | 5,42 / 5,93 / 6,57 | 4,5 | ya |
| Dark: `--text` #eef5f3 di `--surface` #0a2f2b | 13,06 | 4,5 | ya |
| Dark: `--text-muted` #a9c7c1 di `--surface` | 8,00 | 4,5 | ya |
| Dark: `--accent` emas #dfb422 di `--surface` | 7,35 | 4,5 | ya |
| Dark: `--good` #4ade80 / `--warn` #fbbf24 / `--bad` #ff8a80 di `--surface` | 8,29 / 8,65 / 6,32 | 4,5 | ya |
| Dark: `--accent-contrast` #062e2a di atas emas | 7,46 (nilai dari brands.ts) | 4,5 | ya |

Belum dihitung (wajib diperiksa test, ambang 4,5 untuk teks, 3 untuk tanda): `--border` terhadap `--surface` (garis kartu bersifat dekoratif; batas kontrol input harus >= 3:1, pakai `--text-muted` untuk border input, bukan `--border`), `*-soft` dengan teks `--text`, `--chrome-muted` di `--chrome`, `--info`.

### 2.3 Palet kategori (series) — aman buta warna

Dasar Okabe-Ito (aman deutan/protan/tritan), disesuaikan agar setiap tanda >= 3:1 terhadap `--surface` di kedua mode. Maks 7 series bersamaan (§5). Selain warna, series dibedakan oleh **bentuk penanda** (garis: lingkaran/segitiga/kotak/belah ketupat; bar: pola garis diagonal opsional lewat toggle "Pola" di ChartCard) dan label langsung.

```css
:root, [data-theme='light'] {
  --c1:#0072b2; /* biru       5,19 vs putih (dihitung) */
  --c2:#b87400; /* oranye tua 3,79 */
  --c3:#00805c; /* hijau      4,95 */
  --c4:#b0508a; /* magenta    4,80 */
  --c5:#c4470b; /* vermilion  4,92 */
  --c6:#2b8cc4; /* langit     3,72 */
  --c7:#6b7a77; /* abu "Lainnya" 4,49 */
}
[data-theme='dark'] {
  --c1:#56b4e9; /* 6,26 vs surface gelap (dihitung) */
  --c2:#e69f00; /* 6,41 */
  --c3:#009e73; /* 4,22 */
  --c4:#cc79a7; /* 4,72 */
  --c5:#d55e00; /* 3,74 */
  --c6:#8fbfef; /* >= 3 (luminans > 0,168 syarat minimum; diperiksa test) */
  --c7:#9fb3af; /* idem */
}
```

**Pemetaan brand tetap (identik di semua halaman, dari `company_code`):**
| Kode | Brand | Token | Penanda |
|---|---|---|---|
| PB | Paris Baguette | `--c1` | lingkaran |
| WTZ | Wetzel's Pretzels | `--c2` | kotak |
| BCF | Bacha Coffee (kafe) | `--c3` | segitiga |
| BCR | Bacha Coffee (butik) | `--c4` | belah ketupat |
| IMD | (menyusul G43) | `--c5` | panah atas |
| CUP | Curry Up (menyusul G43) | `--c6` | silang |
| (lainnya / agregat) | | `--c7` | titik |

Catatan: warna ini adalah warna **data**, bukan warna identitas brand (palet identitas ada di `brand-tokens.md`); tujuannya konsistensi dan keterbedaan, bukan meniru logo. Bila hanya satu brand terpilih, series utama memakai `--accent`-turunan `--c1`? Tidak: tetap warna brand tersebut agar konsisten. Bila dimensi bukan brand (kategori menu, metode bayar), ambil `--c1..--c7` berurutan menurut peringkat, dan "Lainnya" = `--c7`.

### 2.4 Skala sekuensial & diverging

```css
/* sekuensial (heatmap, peta kepadatan): teal, 5 langkah terang->gelap (light mode) */
--seq-1:#e6f2ef; --seq-2:#b4dcd5; --seq-3:#6fbcb1; --seq-4:#2a8c82; --seq-5:#0a5c54;
/* dark mode: gelap->terang agar "banyak" tetap lebih mencolok */
[data-theme='dark'] { --seq-1:#12433c; --seq-2:#1d6b62; --seq-3:#2a9a8c; --seq-4:#5cc4b5; --seq-5:#a8e6dc; }
/* diverging (delta, varians): vermilion - netral - biru; BUKAN merah-hijau (buta warna) */
--div-neg-3:#b8420c; --div-neg-2:#e08a5a; --div-neg-1:#f3cdb6; --div-0:#f2f4f3;
--div-pos-1:#b9d8ec; --div-pos-2:#5aa3d0; --div-pos-3:#0a6ea8;
[data-theme='dark'] { --div-neg-3:#ff9a66; --div-neg-2:#c9703f; --div-neg-1:#6a3a22; --div-0:#1b3a36;
                      --div-pos-1:#1f4a66; --div-pos-2:#3f86b8; --div-pos-3:#7cc4f2; }
```
Teks di dalam sel heatmap dipilih otomatis (`--text` atau putih/hitam) berdasarkan luminans latar sel agar >= 4,5:1; fungsi di `lib/contrast.ts`. Skala sekuensial/diverging WAJIB disertai legenda bergradasi dengan nilai min/maks, dan nilai angka di sel/tooltip (tidak hanya warna).

Arti **delta**: naik = bukan selalu baik (mis. HPP naik buruk). Karena itu DeltaBadge memakai prop `goodWhen: 'up'|'down'|'neutral'`: warna status mengikuti baik/buruk, panah mengikuti arah.

### 2.5 Status & aturan peringatan (terpusat di `lib/thresholds.ts`, bisa diubah satu tempat)

| Indikator | Baik | Peringatan | Buruk | Catatan |
|---|---|---|---|---|
| Sell-through (sold / pasokan) | >= 85% | 70-84,9% | < 70% | ambang proposal; konfirmasi planner CK (Q-INS1) |
| Rasio kirim CK (ck_out / ck_prod) | 90-110% | 80-89,9% atau 110,1-120% | di luar | selisih besar = stok mengendap di CK atau salah catat |
| Umur data (data_age_hours) | <= 26 jam | 26-48 jam | > 48 jam atau run terakhir tidak ok | 26 jam = harian + toleransi (default kontrak) |
| HPP % | selaras target brand (config) | +2 pp di atas target | +5 pp | target default dikosongkan bila tidak ada; tanpa target = hanya delta |
| Cabang tanpa penjualan H-1 | 0 | - | >= 1 | dari `store_no_sales_h1` |
| Alias tak terpetakan | 0 | 1-10 | > 10 | |

Tampilan status: `StatusPill` = ikon (centang / segitiga seru / silang-lingkaran) + kata ("Baik", "Perlu dicek", "Bermasalah") + warna `--good/--warn/--bad` di atas `*-soft`.

## 3. Layout

> **Revisi 21 Nov 2026 (review pemilik proyek; menang atas butir bertentangan di §3.1-§3.2 di bawah)** - dibangun di `services/insight-web`:
> - **Sidebar 3 grup yang bisa dilipat** (Ringkasan: Ikhtisar/Penjualan/Pembayaran; Food & Beverage: Produksi vs Penjualan/Distribusi CK/Kualitas Data ESB;
>   Supermarket: Eksekutif/Toko/DC/Kategori/Marketing/Forecast) dan **bisa diciutkan jadi rail ikon** (preferensi di `localStorage`, `aria-expanded`, tooltip `title`
>   saat ciut). Tidak ada lagi pil "segera" di menu. Tautan Glosarium di footer sidebar. Visibilitas grup/menu = aturan sesi (`navGroups`), tidak dilonggarkan.
> - **Filter langsung berlaku** (tanpa tombol Terapkan): chip brand dan preset tanggal seketika; tanggal kustom dan pilihan ganda di-debounce. Toolbar satu baris:
>   tombol rentang tanggal (popover preset + kustom), segmented brand dengan "Semua" eksplisit, dropdown Cabang/Kategori/Produk **hanya bila berlaku** (kontrol
>   nonaktif tidak lagi ditampilkan berderet), baris chip filter aktif. Halaman tanpa rentang tanggal (Kualitas Data, Forecast, Toko) menampilkan
>   "Rentang tanggal tidak berlaku: ...". Rentang dibawa ke semua halaman termasuk Supermarket.
> - **Cabang non-penjualan** (HEAD OFFICE, gudang, damage; `is_store = 0 AND is_ck = 0`) tidak muncul di opsi Cabang dan peringkat per cabang; di Distribusi
>   digabung sebagai "Non-toko (HO/gudang/damage)".
> - **Rekonsiliasi alur CK**: label "Hanya produk jadi (FIN)", tile Produksi toko, kartu "Kiriman CK per kelompok barang" (FIN/WIP/bahan baku/kemasan/supplies, qty
>   per satuan), kolom Keterangan "kirim melebihi produksi".
> - **Cakupan data**: banner per halaman bila rentang melebihi data tersedia; delta periode lalu "n/a" bila periode tidak tercakup.
> - **Tabel**: kotak pencarian, dropdown filter kolom kategori/status, "n dari N baris"; **kolom Keterangan** untuk setiap status (alasan dengan angka).
> - **Legend grafik** berupa chip HTML (swatch + penanda bentuk + total/nilai terakhir, toggle seri, `aria-pressed`); legend bawaan ECharts dimatikan.
> - **Istilah**: glosarium (`/glosarium`) dan ikon "i" (popover bisa dibuka keyboard/sentuh) di label KPI, judul kartu, header kolom.

### 3.1 Shell

```
+-----------+--------------------------------------------------------------+
| EFN       | [Rentang v] [Brand/PT chip...] [Cabang v] [Kategori/Produk v]  |
| Insight   | [Reset]   .... Data: 3 jam lalu (ok)   [tema]  Nama User [Keluar]
| --------- +--------------------------------------------------------------+
| Ringkasan | Chip aktif: 1 Nov - 17 Nov 2026 x | PB x | Semua cabang        |
| Produksi..| +----------------------------------------------------------+ |
| Penjualan | | judul halaman + subjudul pertanyaan bisnis    [Ekspor v] | |
| Pembayaran| | KPI row ...                                              | |
| Distribusi| | chart grid ...                                           | |
| Kualitas  | | tabel ...                                                | |
| Forecast  | +----------------------------------------------------------+ |
+-----------+--------------------------------------------------------------+
```
- **Sidebar** (`<nav aria-label="Navigasi utama">`), 7 item, ikon + teks; aktif = `aria-current="page"` + batang kiri 3 px emas + teks putih (bukan warna saja). Item "Forecast" berlabel "segera" (pil kecil), tetap tautan ke placeholder. Footer sidebar: versi + tautan "Kembali ke Odoo" (`EFN_INSIGHT_ODOO_URL`). Rail ikon di tablet (tooltip + `aria-label`), drawer di HP (tombol menu di header, fokus dikunci di drawer, Esc menutup).
- **Header** lengket (`position: sticky; top:0; z-index` di atas konten, di bawah drawer). Baris 1: filter global; baris 2 (muncul bila ada filter aktif): chip ringkasan. Di HP filter dibuka lewat tombol "Filter (n)" ke panel penuh-layar (bottom sheet) dengan tombol "Terapkan" dan "Reset".
- **Skip link** "Lompat ke konten" sebagai elemen fokus pertama; `<main id="konten" tabindex="-1">` menerima fokus setelah navigasi halaman, `<h1>` per halaman, judul dokumen `"<Halaman> - EFN Insight"`.
- **Identitas user**: nama tampilan (dari cookie sesi, dipotong dengan elipsis + `title`), peran kecil ("Semua brand" bila `all_brands`, atau daftar kode PT), tombol "Keluar" (hapus cookie, kembali ke halaman info "Anda telah keluar - buka lagi dari Odoo"). Tidak ada avatar/email.
- **FreshnessBadge** di header: titik status + "Data: 3 jam lalu". Klik membuka popover per brand x entitas (dari `data_age_hours`) dengan tautan ke Kualitas Data. Rujukan waktu = `data_age_hours` terbesar di antara brand yang terpilih (kasus terburuk).

### 3.2 Filter global

| Filter | Kontrol | Default | Aturan |
|---|---|---|---|
| Rentang tanggal | Popover: preset + kalender dua bulan + input teks `dd/mm/yyyy` | 30 hari terakhir s.d. H-1 | Preset: "7 hari", "30 hari", "Bulan ini", "Bulan lalu", "90 hari", "Tahun ini", "Kustom". Maks 366 hari. Tanggal tak boleh >= hari ini (data harian lengkap sampai H-1). Header menampilkan "Periode lalu" otomatis = rentang sama panjang tepat sebelumnya. |
| Brand / PT | Chip toggle multi-pilih (`role="group"`, tiap chip `aria-pressed`) | semua kode di sesi | Hanya kode dalam sesi; kode di luar sesi tidak ditampilkan sama sekali. Bila hanya 1 kode: tampil sebagai label statis (tanpa toggle). Warna titik di chip = warna brand §2.3. Maks semua dipilih = semua. Mengosongkan semua = kembali ke semua (bukan hasil kosong). |
| Cabang | Combobox multi-pilih dengan pencarian, dikelompokkan per brand | semua | Opsi tergantung brand terpilih; maks 50 dipilih; tampil "3 cabang". |
| Kategori / Produk | Combobox bertab: "Kategori menu" / "Produk FIN" | semua | Berlaku halaman yang punya dimensinya: kategori untuk Penjualan & Menu; produk untuk Produksi/Distribusi. Pada halaman yang tidak mendukung, kontrol dinonaktifkan dengan keterangan "Tidak berlaku di halaman ini" (`aria-disabled`, bukan disembunyikan, agar tata letak stabil). |

**Perilaku**
- State = URL search params: `from=2026-11-01&to=2026-11-17&b=PB,WTZ&br=12,15&cat=Roti&prod=P001`. Tombol Kembali browser mengembalikan filter. Param tak valid dibuang diam-diam lalu diganti default (validasi server; UI menampilkan toast "Filter tidak valid, dikembalikan ke bawaan").
- Perubahan filter diterapkan setelah ditutup/dikonfirmasi per kontrol (bukan tiap ketikan); transisi memakai `useTransition` dengan skeleton pada kartu (konten lama tidak hilang mendadak: `aria-busy="true"` + opasitas 0,6).
- **Chip aktif**: satu chip per filter non-default ("1 Nov - 17 Nov 2026", "PB", "3 cabang"), tiap chip punya tombol hapus (`aria-label="Hapus filter PB"`); tombol **Reset** (teks, bukan hanya ikon) mengembalikan semua; tampil hanya bila ada filter non-default.
- Drill-down (klik cabang/produk) menambah filter `br`/`prod` dan memunculkan chip; pesan `aria-live="polite"`: "Filter ditambahkan: cabang X. Data diperbarui."
- Filter dipertahankan antar halaman kecuali yang tidak berlaku.

## 4. Halaman

Konvensi: tiap halaman = `<h1>` + subjudul pertanyaan + (opsional) `ChartCard`/`KpiTile`/`DataTable`. "Periode lalu" = rentang sama panjang sebelum `from`. Semua kueri lewat view `efn_insight.v_esb_*`; kolom di bawah mengacu nama kolom view `efn_dwh` (sama).

### 4.0 Ringkasan umum state (berlaku semua halaman)
- **Loading:** skeleton berukuran sama dengan konten akhir (KPI: 3 baris abu berdenyut; chart: kotak dengan sumbu samar; tabel: 8 baris). Denyut dimatikan pada `prefers-reduced-motion`. Wadah `aria-busy`, teks sr-only "Memuat data".
- **Kosong:** `EmptyState` dengan ikon, judul ("Tidak ada penjualan pada periode ini"), penyebab mungkin (filter terlalu sempit; data belum masuk), dan aksi ("Reset filter", "Lihat Kualitas Data"). Pembedaan: kosong karena filter vs kosong karena data belum ada (cek kesegaran).
- **Galat:** kartu galat per komponen (bukan satu halaman putih): "Data tidak dapat dimuat. Coba lagi." + tombol "Coba lagi" + kode referensi singkat (ID korelasi, tanpa detail SQL/host). Satu kartu gagal tidak menjatuhkan halaman. Galat 401/sesi habis = halaman "Sesi berakhir, buka lagi dari Odoo".
- **Data basi:** spanduk `role="status"` di atas konten bila umur data > 26 jam: "Data terakhir diperbarui 30 jam lalu. Angka mungkin belum mencakup kemarin." (peringatan) / > 48 jam (buruk).

### 4.1 Ringkasan (`/`)
- **Tujuan:** "Bagaimana kinerja bisnis saya periode ini dibanding sebelumnya, dan di mana yang perlu perhatian?"
- **Pertanyaan:** Berapa penjualan bersih? Naik/turun? Cabang mana terbaik/terburuk? Apakah ada yang janggal (data, sell-through)?
- **Komponen & data** (`v_esb_sales_daily`, `v_esb_flow_daily`, `v_esb_data_quality`):
  1. KPI row (5 `KpiTile`, masing-masing nilai, DeltaBadge vs periode lalu, sparkline harian): Penjualan bersih (`sum(net)`), Transaksi (`sum(trx_count)`; catatan: transaksi dihitung per baris view [hari x cabang x menu] sehingga menjumlah lintas menu dapat menghitung ganda struk yang sama; definisi: lihat Q-INS2), Rata-rata per transaksi (net / trx), HPP % (`sum(cogs)/sum(gross)`? pakai `sum(cogs)/sum(net)`; label "HPP terhadap penjualan bersih"; `goodWhen:'down'`), Sell-through (§4.2; tile ke-5, status warna).
  2. **Tren penjualan bersih** (line/area, X = hari, Y = net; satu garis per brand terpilih bila > 1 brand, maks 6; garis putus tipis = periode lalu untuk total). Toggle metrik: Penjualan bersih / Transaksi / Rata-rata per transaksi.
  3. **Peringkat cabang** (bar horizontal terurut, top 10 + "10 terbawah" lewat tab; label nilai di ujung batang; warna = brand cabang; klik batang = drill-down cabang).
  4. **Kontribusi brand/PT** (stacked bar 100% atau bar sederhana; bukan donat). Hanya tampil bila > 1 PT dalam sesi.
  5. **Perlu perhatian** (daftar maks 5 baris, `role="list"`): hasil aturan §2.5 (sell-through merah, data basi, cabang tanpa penjualan H-1) masing-masing dengan StatusPill dan tautan ke halaman terkait.
- **Interaksi:** hover tooltip (tanggal, nilai periode ini, nilai periode lalu, delta); klik titik hari = menyorot hari (tidak memfilter); klik cabang = drill-down ke `?br=<id>` (chip muncul); ekspor CSV: tabel peringkat cabang.
- **Peringatan:** KPI Sell-through status; spanduk data basi.

### 4.2 Produksi vs Penjualan (`/produksi`)
- **Tujuan:** "Apakah yang diproduksi dan dikirim sebanding dengan yang terjual? Di mana barang menumpuk atau kurang?"
- **Pertanyaan:** Berapa produksi CK, berapa dikirim, berapa diterima toko, berapa terjual? Sell-through per cabang/produk? Produk/cabang outlier?
- **Data:** `v_esb_flow_daily` (`ck_prod`, `ck_out`, `in_ck`, `in_other`, `store_prod`, `st_in`, `st_out`, `sold`; `product_code/name`, `branch_name`, `day`).
- **Definisi turunan (proposal, konfirmasi planner CK, Q-INS1):** Pasokan toko = `in_ck + in_other + store_prod + st_in - st_out`; **Sell-through** = `sold / Pasokan` (tampilkan "n/a" bila pasokan <= 0); **Rasio kirim** = `ck_out / ck_prod`. Info-ikon di tiap KPI memuat definisi dan satuan (EA). Peringatan wajib dalam UI: tanpa stok awal/akhir toko, sell-through periode pendek dapat >100% atau menyesatkan; tampilkan catatan "Berdasarkan arus barang, bukan stok" (stok akhir tidak ada di view).
- **Komponen:**
  1. KPI: Produksi CK (`sum(ck_prod)`), Dikirim CK (`sum(ck_out)`), Diterima toko (`sum(in_ck+in_other)`), Terjual (`sum(sold)`), Sell-through (status), semuanya dengan delta.
  2. **Alur CK -> Kirim -> Produksi toko -> Transfer -> Terjual**: diagram Sankey (satu-satunya tempat Sankey dipakai) dengan alternatif tabel; node: Produksi CK, Produksi toko, Dikirim, Transfer masuk/keluar, Terjual. Bila terlalu rumit pada layar kecil: ganti dengan bar bertahap (funnel horizontal) otomatis < 1024 px.
  3. **Sell-through per cabang** (bar horizontal terurut menaik, garis acuan vertikal di 70% dan 85%; warna bar menurut status §2.5 + ikon di label sumbu agar bukan warna saja).
  4. **Produksi vs Terjual per hari** (bar berkelompok: produksi CK vs terjual; atau garis ganda), toggle "Harian / Mingguan".
  5. **Tabel outlier** (cabang x produk): kolom Cabang, Produk, Diterima, Terjual, Sell-through, Status; default urut sell-through terendah; filter cepat "Hanya yang bermasalah". Paginasi 25 baris.
- **Interaksi:** klik batang cabang -> filter `br` dan tabel/produk mengikuti; klik produk di tabel -> filter `prod` + tab "Detail produk" (garis harian produksi/kirim/terjual untuk produk itu); sortir semua kolom; ekspor CSV tabel outlier.
- **Peringatan:** sell-through < 70% = Bermasalah (merah + ikon silang); 70-84,9% Perlu dicek; produk `NAME:` (alias tak terpetakan) diberi lencana "Belum dipetakan" dan tautan ke Kualitas Data.

### 4.3 Penjualan & Menu (`/penjualan`)
- **Tujuan:** "Menu apa dan hari apa yang menghasilkan penjualan, dan bagaimana bauran takeaway vs dine-in?"
- **Data:** `v_esb_sales_daily` (sales_date, brand, branch, `menu_code/name`, `menu_category`, `is_takeaway`, qty, gross, discount, net, cogs, trx_count); `v_esb_menu_mix` (bulanan: share_qty, share_net per cabang x kategori x takeaway). Tidak ada data per jam (L-112): jangan sediakan heatmap jam; heatmap hanya hari-dalam-minggu x minggu.
- **Komponen:**
  1. KPI: Penjualan bersih, Diskon (`sum(discount)` dan % terhadap gross), Qty terjual, Margin kotor % (`(net-cogs)/net`).
  2. **Pola hari dalam minggu** (bar 7 batang Sen-Min rata-rata net per hari; di bawahnya heatmap minggu x hari sekuensial dengan nilai di sel).
  3. **Bauran kategori** (bar horizontal terurut, maks 8 kategori + "Lainnya"; toggle metrik Net / Qty / Margin).
  4. **Takeaway vs Dine-in** (stacked bar 100% per brand/cabang; hanya 2 segmen sehingga label langsung di segmen; bukan donat).
  5. **Top 10 menu** (tabel peringkat + sparkline tren per menu; toggle "Berdasarkan Net / Qty / Margin"; tab "Top 10 terbawah").
  6. **Bauran per cabang** (tabel/heatmap cabang x kategori memakai `share_net`; warna sekuensial + angka persen).
- **Interaksi:** klik kategori = filter `cat`; klik cabang = filter `br`; klik menu = panel samping (drawer) dengan tren harian menu itu; toggle metrik di tiap ChartCard; ekspor CSV Top menu dan Bauran per cabang.
- **Peringatan:** Diskon > 15% dari gross = Peringatan (ambang konfigurasi); margin kotor negatif = Bermasalah; menu tanpa kategori dikelompokkan "Tanpa kategori" (cacat data) dengan petunjuk ke Kualitas Data.

### 4.4 Pembayaran (`/pembayaran`)
- **Tujuan:** "Pelanggan membayar dengan apa, dan berapa biaya (MDR) yang kita tanggung?"
- **Data:** `v_esb_payment_daily` (`sales_date`, `branch_name`, `payment_type`, `payment_method`, `payment_count`, `amount`, `mdr`, `net`). Granularitas: hari x cabang x metode.
- **Komponen:**
  1. KPI: Total pembayaran (`sum(amount)`), Jumlah pembayaran (`sum(payment_count)`), Total MDR (`sum(mdr)`), MDR efektif % (`sum(mdr)/sum(amount)`; `goodWhen:'down'`).
  2. **Bauran metode** (bar horizontal terurut menurut `amount`, tipe pembayaran sebagai grup warna; maks 7 + "Lainnya"; alternatif donat DILARANG, gunakan bar).
  3. **Tren per tipe** (area bertumpuk harian maks 5 tipe; toggle "Nilai / Jumlah transaksi / Persen").
  4. **MDR per metode** (tabel: metode, jumlah, nilai, MDR, MDR %, rata-rata nilai per pembayaran) dengan sortir; sel MDR% memakai skala sekuensial.
  5. **Per cabang** (tabel: cabang x total, MDR, metode dominan).
- **Interaksi:** klik metode = sorot di tren (series lain dipudarkan 30%), tidak memfilter; klik cabang = drill-down; ekspor CSV tabel metode.
- **Peringatan:** MDR efektif naik > 0,3 pp dari periode lalu = Peringatan; metode tanpa nama ("") ditampilkan sebagai "Tidak diketahui".

### 4.5 Distribusi (`/distribusi`)
- **Tujuan:** "Barang bergerak dari CK ke toko dan antar toko seperti seharusnya?"
- **Data:** `v_esb_flow_daily` (`ck_out`, `in_ck`, `in_other`, `st_out`, `st_in`).
- **Komponen:**
  1. KPI: Dikirim CK (`sum(ck_out)`), Diterima dari CK (`sum(in_ck)`), Selisih kirim vs terima (`ck_out - in_ck`; selisih != 0 butuh penjelasan: lead time antar-hari bisa menjadi penyebab, tampilkan catatan), Transfer antar toko (`sum(st_out)`), Diterima dari non-CK (`sum(in_other)`).
  2. **Pengiriman CK per hari** (bar harian `ck_out` dengan garis `in_ck`; selisih = area bertanda).
  3. **Matriks cabang** (tabel/heatmap: cabang x [Diterima CK, Transfer masuk, Transfer keluar, Neto transfer]; diverging untuk Neto transfer, pusat = 0; nilai angka di sel).
  4. **Produk paling banyak ditransfer** (bar top 10 `st_in+st_out`), sebagai indikasi salah alokasi.
  5. **Tabel selisih** cabang x produk dengan `ck_out` vs `in_ck` hanya baris |selisih| > 0.
- **Interaksi:** klik cabang/produk = drill-down; sortir; ekspor CSV matriks cabang dan tabel selisih. Catatan peta asal-tujuan tidak tersedia di view (hanya sisi asal/tujuan terpisah): jangan menggambar garis asal -> tujuan; bila diperlukan, tambahkan view baru (Q-INS3).
- **Peringatan:** |selisih kirim vs terima| > 10% = Peringatan, > 20% = Bermasalah (ambang konfigurasi); transfer neto sangat negatif konsisten (> 3 hari) = Perlu dicek.

### 4.6 Kualitas Data (`/kualitas-data`)
- **Tujuan:** "Apakah angka yang saya lihat bisa dipercaya?"
- **Data:** `v_esb_data_quality` (`check_name`, `brand`, `company_code`, `item`, `value`, `last_day`) dengan cek `alias_unmapped`, `non_ea_lines`, `store_no_sales_h1`, `data_age_hours`, `last_run_not_ok`.
- **Peringatan implementasi (untuk efn-data-ai-engineer):** baris `data_age_hours` dan `last_run_not_ok` memiliki `company_code = ''`; row policy `has(codes, company_code)` akan membuang baris itu sehingga FreshnessBadge dan halaman ini kosong. Isi `company_code` (pemetaan brand -> kode PT) di view, atau sediakan kolom `brand`-ke-kode, seperti disebut kontrak bagian B. Tanpa itu UI menampilkan "Kesegaran tidak diketahui" (status Perlu dicek), BUKAN "baik".
- **Komponen:**
  1. **Papan status** (tile per cek: ikon status, jumlah temuan, kalimat penjelas): Kesegaran ekstraksi, Run terakhir, Alias tak terpetakan, Baris non-EA, Cabang tanpa penjualan H-1.
  2. **Kesegaran per brand x entitas** (tabel/heatmap: baris = entitas [`sales`, `payment`, `production`, `delivery`, `transfer`, `fin_sold`], kolom = brand; sel = umur dalam jam + hari terakhir; warna status + ikon).
  3. **Alias tak terpetakan** (tabel: nama, brand, qty 30 hari, terakhir terlihat), terurut qty; keterangan "Beri tahu admin data untuk memetakan" (aksi pemetaan di luar Insight).
  4. **Cabang tanpa penjualan H-1** (tabel: cabang, brand) + penjelasan kemungkinan (toko tutup, data belum ditarik).
  5. **Baris non-EA** (tabel: tabel:uom, jumlah baris).
- **Interaksi:** sortir, ekspor CSV tiap tabel; tautan ke halaman terkait (mis. alias -> Produksi).
- **State kosong positif:** "Tidak ada temuan. Semua pemeriksaan lolos." dengan ikon centang (bukan kosong biasa).

### 4.7 Forecast (`/forecast`) — placeholder G44
- Halaman statis: `<h1>Forecast</h1>`, label "Segera hadir (G44)", satu paragraf: "Prakiraan penjualan dan kebutuhan produksi per cabang dan produk, dengan rentang ketidakpastian." + wireframe abu non-interaktif (disembunyikan dari pembaca layar: `aria-hidden`) dan tombol "Kembali ke Ringkasan". Tidak memanggil data. Item sidebar tidak `disabled` (tetap bisa difokus) agar jelas.

## 5. Aturan visual & aksesibilitas chart (ECharts)

**Aturan visual**
1. **Pilih chart menurut pertanyaan:** tren -> line/area; peringkat/perbandingan -> bar horizontal terurut; komposisi 2-6 bagian -> stacked bar 100%; distribusi mingguan -> bar + heatmap; alur -> Sankey/funnel; korelasi -> scatter. **Tanpa donat/pai, 3D, atau dual-axis menyesatkan.** Dua sumbu Y hanya bila satuan beda dan dilabeli jelas di kedua sumbu (hindari).
2. **Sumbu:** bar mulai dari 0 (wajib); line boleh tidak dari 0 tetapi sumbu Y diberi label dan tidak dipotong tanpa tanda. Sumbu X tanggal: tick adaptif (`d MMM`), tahun di tooltip. Label sumbu pakai format §1 (`Rp 1,2 jt`); judul sumbu bila satuan tidak jelas.
3. **Gridline:** hanya horizontal, 1 px `--grid`, tanpa garis sumbu vertikal pada bar; tanpa border chart.
4. **Label:** label langsung di ujung batang/garis (akhir seri) bila <= 3 series atau bar; legenda hanya bila > 3 series (di atas chart, kiri, dapat diklik untuk menyembunyikan series, tombol `aria-pressed`). Label tidak boleh bertumpuk: ECharts `labelLayout: { hideOverlap: true }`; yang disembunyikan tetap tersedia di tooltip/tabel.
5. **Maks series:** 7 (6 + "Lainnya"); lebih dari itu agregasikan ke "Lainnya" (dengan tabel penuh di bawahnya). Maks 12 kategori pada sumbu bar sebelum paginasi/"Lihat semua".
6. **Warna:** brand = §2.3 (tetap lintas halaman); status = §2.5; periode lalu = garis putus abu `--text-muted` tipis; sorotan memudarkan series lain ke opasitas 0,3, bukan mengubah warna.
7. **Tooltip:** tetap (bukan mengikuti pointer cepat), memuat judul (tanggal/entitas), baris per series dengan penanda bentuk + nilai penuh format id-ID + delta; muncul pada hover dan fokus keyboard; kontras `--surface` + `--text`; `confine: true` agar tidak keluar layar.
8. **Gerak:** animasi masuk 300 ms ease-out, nonaktif bila `prefers-reduced-motion`.
9. **Tema:** objek tema ECharts dibangun dari CSS var saat mount dan dibangun ulang saat `data-theme` berubah (`getComputedStyle`), agar tidak ada warna ter-hard-code dan CSP tidak butuh `unsafe-eval`. Render: `renderer: 'svg'` (teks tajam, dapat disalin; tanpa data-URL canvas), ukuran dari kontainer via `ResizeObserver`.
10. **Ukuran:** tinggi chart standar 280 px (KPI sparkline 32 px, tanpa sumbu/tooltip kecuali fokus), minimal lebar 320 px.

**Aksesibilitas chart**
- Tiap `ChartCard` = `<figure>` dengan `<figcaption>` (judul + periode), `role="img"` pada wadah canvas/SVG dengan `aria-label` ringkasan satu kalimat yang dihitung dari data ("Penjualan bersih harian 1-17 Nov 2026: tertinggi 18 jt pada 9 Nov, terendah 7 jt pada 3 Nov, total 210 jt"), dan `aria-describedby` ke ringkasan teks panjang opsional. ECharts `aria: { enabled: true, decal: { show: false } }`; dekal (pola) diaktifkan lewat toggle "Pola" dan otomatis bila `prefers-contrast: more`.
- **Tabel alternatif**: tombol "Tampilkan sebagai tabel" (toggle `aria-pressed`) pada setiap ChartCard mengganti chart dengan `DataTable` berisi data yang sama (nilai penuh, tanpa pembulatan visual). Tabel ini juga sumber ekspor CSV kartu. Tabel itu selalu ada di DOM untuk pembaca layar dengan kelas `sr-only` bila chart sedang tampil? Tidak: hindari duplikasi bising; cukup toggle yang terjangkau keyboard + ringkasan `aria-label`.
- **Fokus keyboard:** wadah chart fokusable (`tabindex="0"`) dengan cincin fokus `--focus`; panah kiri/kanan berpindah antar titik/batang (implementasi: `dispatchAction({type:'showTip'/'highlight'})` dari handler `keydown`), Home/End = titik pertama/terakhir, Enter pada batang/titik yang bisa drill-down = aksi klik, Esc = tutup tooltip. Pengumuman titik fokus lewat `aria-live="polite"` ("Cabang A, 14 jt, peringkat 2 dari 10"). Heatmap: panah 2D.
- **Tidak hanya warna:** penanda bentuk, label langsung, ikon status, angka di sel heatmap, pola opsional.
- **Kontras:** garis/batang >= 3:1 terhadap `--surface` (diuji untuk palet §2.3), teks label >= 4,5:1, garis acuan 2 px `--text-muted`.
- **Zoom/reflow:** pada zoom 200% / lebar 320 px konten tidak hilang; tabel scroll horizontal dalam wadah dengan `tabindex="0"` dan `aria-label`.
- Target sentuh: kontrol >= 40 px (min. 24 px untuk elemen kecil seperti tombol hapus chip, dengan jarak).

## 6. Komponen bersama (props ringkas, TypeScript)

```ts
type Status = 'good' | 'warn' | 'bad' | 'neutral' | 'unknown';
type Fmt = 'idr' | 'idr-short' | 'int' | 'pct' | 'pp' | 'date';

KpiTile {
  label: string; value: number | null; fmt: Fmt; unit?: string;
  delta?: { value: number | null; fmt: 'pct' | 'pp'; goodWhen: 'up' | 'down' | 'neutral' };
  spark?: { x: string[]; y: number[] };          // sparkline 32 px
  status?: Status; info?: string;               // info = definisi (popover, tombol "i" bisa difokus)
  href?: string; loading?: boolean; error?: boolean;
}                                                // <dl> semantik; sparkline aria-hidden; delta dibaca "naik 4,2 persen dari periode lalu"

ChartCard {
  title: string; subtitle?: string; summary: string;      // summary -> aria-label dihitung
  option: EChartsOption; height?: number;
  metricToggle?: { id: string; label: string }[]; legend?: 'auto' | 'none' | 'always';
  tableData?: { columns: Column[]; rows: Row[] };         // tabel alternatif + sumber CSV
  csvName?: string; onDrill?: (datum: Drill) => void;
  state?: 'ready' | 'loading' | 'empty' | 'error'; onRetry?: () => void;
}

DataTable<T> {
  columns: { key: keyof T; header: string; fmt?: Fmt; align?: 'left'|'right'; sortable?: boolean;
             status?: (row: T) => Status; width?: string }[];
  rows: T[]; defaultSort?: { key: keyof T; dir: 'asc'|'desc' }; pageSize?: 10|25|50;
  caption: string; onRowClick?: (row: T) => void; csvName?: string; stickyFirstCol?: boolean;
  state?: 'ready'|'loading'|'empty'|'error'; emptyState?: ReactNode;
}                                                // <table> asli; <th scope>, aria-sort, baris klik = tombol di sel pertama

FilterBar { value: Filters; options: { brands: Code[]; branches: Branch[]; categories: string[]; products: Product[] };
            disabled?: Partial<Record<keyof Filters, string>>;   // alasan tidak berlaku
            onChange(next: Filters): void; onReset(): void }     // menyinkronkan URL; chip aktif di dalamnya

DeltaBadge { value: number | null; fmt: 'pct'|'pp'; goodWhen: 'up'|'down'|'neutral'; compareLabel?: string }
             // panah + angka + warna status; null -> "-" dengan sr-only "tidak ada pembanding"

EmptyState { icon?: ReactNode; title: string; reason?: string; actions?: { label: string; onClick?: () => void; href?: string }[]; tone?: 'neutral'|'positive' }

FreshnessBadge { ageHours: number | null; detail?: { brand: string; entity: string; ageHours: number }[]; warnAt?: number; badAt?: number }
             // default warnAt 26, badAt 48; null = status 'unknown' + kata "Tidak diketahui"

StatusPill { status: Status; label?: string }    // ikon + kata + warna
```
Seluruh komponen membaca token CSS (tanpa warna ter-hard-code), mendukung `loading`/`error`/`empty`, dan tidak memakai pustaka UI eksternal tanpa persetujuan (R-T14 semangatnya: satu set komponen, token yang membedakan).

## 7. Keamanan UI & copy
- Tidak ada `dangerouslySetInnerHTML`; nilai dari data (nama cabang/menu) selalu di-escape. CSV: awalan `=`, `+`, `-`, `@` di sel teks diberi `'` (cegah formula injection).
- Pesan galat tidak membocorkan SQL, host, atau nama tabel. Tidak ada log token/cookie.
- Copy bernada tenang & lugas: "Data terakhir diperbarui 3 jam lalu", "Tidak ada penjualan pada periode ini", "Coba lagi". Hindari jargon teknis ("slice", "checkpoint") di UI; gunakan "pembaruan data".
- Pajak: angka net/gross berasal dari ESB (semantik `total` ESB); UI menyebut "Penjualan bersih" = total setelah diskon, tanpa mengklaim sebelum/sesudah PB1/PPN karena belum diverifikasi (Q-INS4).

## 8. Pertanyaan terbuka untuk tim bisnis / lead
- **Q-INS1:** definisi dan ambang sell-through (pasokan = ? ; 70%/85%) dan rasio kirim; belum ada stok awal/akhir toko (tidak ada `v_esb_stock_cover`).
- **Q-INS2:** `trx_count` pada `v_esb_sales_daily` per baris [hari x cabang x menu] (`uniqExact(sales_num)`); menjumlah lintas menu menghitung ganda struk multi-menu. KPI "Transaksi" perlu view tingkat cabang-hari (tambah `v_esb_sales_trx_daily`) atau definisi lain. Sampai itu, KPI Transaksi dan Rata-rata per transaksi diberi info "perkiraan".
- **Q-INS3:** view asal-tujuan (matriks pengiriman) tidak ada; perlu bila planner butuh peta distribusi.
- **Q-INS4:** apakah `net` ESB sudah termasuk PB1/pajak layanan (label "bersih" harus jujur).
- Target HPP % per brand sebagai konfigurasi (siapa pemiliknya).
- IMD/CUP: nama tampilan & warna menunggu G43.
- Font Geist untuk Insight: file sudah ada di `superapp-web/public/fonts`? Bila `insight-web` aplikasi terpisah, salin file OFL yang sama (tidak ada aset berlisensi proprietary).
