# Landing page brand — Super-App EFN

Status: **DRAF 21 Okt 2026 (v3: warna resmi + foto produk asli)**; G24 (24 Okt 2026) + Wetzel's; **G25 (24 Okt 2026) + Bacha Coffee & GrandLucky** (§8). Brand tanpa entri di `lib/landing-config.ts` tetap memakai beranda lama (`app/page.tsx`); holding `app.` memakai landing Super-App.

## 1. Tujuan & posisi

Beranda `/` brand = landing page (mode **Persuade**), lalu masuk e-commerce di `/belanja`. Semua angka & foto yang tampil (jumlah pilihan, harga terendah, nama favorit, gerai, menit siap, metode bayar, foto produk) berasal dari katalog BFF, bukan konten contoh.

Riwayat:
- **v2:** pola *scroll story* (referensi **AURAL — Luxury Hardware, ThreeUI**; template berbayar, hanya pola dari video pratinjau publik yang dipakai; tidak ada kode/aset/copy yang disalin).
- **v3 (21 Okt 2026, keputusan user):** (a) warna = warna resmi parisbaguette.co.id, latar navy asli `#002663` + teks putih; (b) model 3D ilustrasi diganti **foto produk asli** dari katalog; (c) rak etalase sejajar grid; (d) seluruh halaman PB mengikuti bahasa visual landing. three / R3F / drei / model CC0 dihapus.

| Bagian | Isi | Sumber data |
|---|---|---|
| Scroll story (sticky, 4 bab) | Pembuka: judul `Brand.hero` + lead + CTA + ringkasan layanan (mono). Bab 01–03 (Pastry / Kue / Roti): judul & kalimat (DRAF), statistik *Pilihan · Mulai dari · Favorit*, tautan kategori. Panggung = kartu foto produk per bab yang bergerak mengikuti scroll di zona seberang teks | `lib/landing-config.ts`, produk per kategori |
| HUD (fixed, hanya selama story) | "Foto: katalog Paris Baguette", navigasi bab (anchor), `01 / 03`, bilah progres kuning | progres scroll |
| Etalase | Chip kategori + tombol ←/→, rak 12 produk (4:5), lebar = grid konten | `/v1/catalog/*` |
| Cara pesan | 3 kolom bernomor + tabel gerai (mono, angka tabular) | `/v1/catalog/channels` |
| Penutup | Ajakan pesan + QR meja + CTA | — |

Katalog mati → story tetap tampil tanpa foto/statistik (kartu berisi nama bab); etalase & tabel gerai disembunyikan.

## 2. Visual

- Warna PB = situs resmi parisbaguette.co.id (dicek 21 Okt 2026), di `lib/brands.ts` dengan `scheme: 'dark'`: latar `surfaceAlt`/`chrome` `#002663`, kartu `surface` `#0c3170`, teks `#ffffff`, sekunder `#aebcd3`, aksen/aksi `#fcb134`, garis `#34548c`. Kontras (diuji di `tests/brands.test.ts` & `tests/landing.test.ts`): putih/navy 14,4 · `#aebcd3`/navy 7,5 · kuning/navy 7,9 · navy di tombol kuning 7,9 · putih/kartu 12,4.
- Foto katalog ~497×347 px berlatar terang → selalu di kartu putih berbingkai, lebar kartu ≤ 30rem (tidak full-bleed agar tidak pecah).
- Tipografi: **Fraunces** (display), **Geist** (UI), **Geist Mono** (label, angka, HUD). OFL-1.1, self-host via `@fontsource-variable/*` (CSP `font-src 'self'`), diimpor di `app/layout.tsx`. Fraunces = pengganti sementara font resmi PB Signature.

## 3. Scroll story foto (v5, 21 Okt 2026)

- **Desain v3 dipertahankan** (keputusan user: perbaiki tumpang tindih tanpa membuat layout baru; v4 dua kolom statis ditolak). Panggung menempel (sticky) setinggi layar; bab teks menggulir di atasnya; kartu foto produk masuk/keluar mengikuti scroll naik **dan** turun (naik/turun ±140 px, putar 3D ±28°, skala 0,86→1), foto bergerak pelan di dalam bingkai (parallax ±5%), kartu miring halus mengikuti mouse.
- Penyebab tumpang tindih v3 (terukur: teks tertutup foto 85–100%, foto saling menimpa ±82%): panggung bergeser kanan↔kiri melintasi kolom teks, 2 kartu latar sengaja ditumpuk di belakang kartu depan, dan miring mouse menggeser kartu ±16 px.
- Perbaikan:
  - **Zona foto per bab**: kartu tiap bab punya sisi tetap (pembuka kanan, lalu kiri/kanan bergantian) di kolom grid di seberang teks bab itu (`--text-w` 48% / `--hero-w` 52% dari lebar konten). Semua gerak terjadi di dalam zona itu.
  - **Satu kartu per bab**; kartu latar dihapus. Jendela tampil kartu (progres segmen 0,25–0,75) bergantian dan berseberangan sisi, jadi kartu tidak saling menimpa.
  - **Miring mouse** = rotasi kecil di tempat (±6° / ±5°), tanpa geser.
  - **Penjaga teks** (JS, per frame saat scroll/resize/pointer): baris teks yang mendekati kartu yang sedang terlihat memudar sebelum bersentuhan (jarak pudar 56 px + margin 12 px).
  - **Potret/HP**: kartu menempel di atas tengah (lebar ≤ 24rem dan ≤ tinggi 34svh), teks bab dimulai di bawah tepi bawah kartu yang **diukur langsung** (`--card-bottom`, termasuk nama produk 2 baris) + 4,5rem; segmen memanjang bila isinya tinggi.
- `prefers-reduced-motion` memakai `useReducedMotionSafe` (`useSyncExternalStore`, snapshot server `false`). `useReducedMotion` framer-motion bernilai `true` sejak render hidrasi pertama sehingga atribut beda dari SSR dan React tidak menambalnya. Style statis diberikan sebagai objek nilai, bukan `undefined`, karena melepas prop style tidak menghapus transform yang sudah ditulis framer-motion.
- Rentang `useTransform` dijepit ke [0,1] (WAAPI/ScrollTimeline menolak offset di luar [0,1]).
- **Tidak ada gerakan otomatis**: semua gerak dipicu scroll/pointer (WCAG 2.2.2 tidak berlaku).
- Terukur (Playwright, 9 viewport 1440×900 … 360×640 × 41 posisi scroll turun lalu naik, plus mouse di tengah & 4 sudut kartu): teks tertutup foto **0%**, kartu saling menimpa **0%**, 20–27 keadaan gerak kartu berbeda per putaran, semua teks bab terbaca penuh saat bab di tengah layar, tanpa scroll horizontal & tanpa error browser. Reduced motion: 1 kartu statis per bab, teks opak.

### Degradasi bertahap

| Kondisi | Perilaku |
|---|---|
| `prefers-reduced-motion` | Tanpa gerak; kartu bab aktif tampil statis, berganti per bab; teks opak |
| Tanpa JS / sebelum hidrasi | Kartu pembuka statis; semua teks bab terlihat (`<noscript>`) |
| Story di luar viewport | HUD disembunyikan (`inert`) |

## 4. Rak etalase

Lebar rak = `.inner` (grid konten), bukan full-bleed: tepi kiri & kanan kartu = tepi judul. `grid-auto-columns` dihitung agar tepat N kartu mengisi lebar (desktop 4, ≤ 64rem 3, ≤ 56rem 2; ponsel 72% → kartu berikutnya mengintip). Tombol ←/→ menggeser satu halaman kartu (nonaktif di ujung); ponsel: geser jari, tombol disembunyikan.

Terukur (Playwright, 21 Okt 2026): 1440 px → judul 176 / rak 176–1264 / tautan "Lihat semua menu" kanan 1264, 4 kartu penuh; 1280 px → 96–1184; 390 px → 20–370.

## 5. Halaman lain (skema gelap)

`<html data-scheme="dark">` (dari `BrandTheme.scheme`) mengaktifkan aturan di `app/globals.css` dan `:global(html[data-scheme='dark'])` di `components/online/online.module.css` & `components/fnb/fnb.module.css`: judul Fraunces, label mono, kartu navy bergaris tipis radius 16px, tombol pil kuning, input navy, chip putih saat aktif, foto produk & QR di atas putih, warna status dicerahkan (error `#ff9e8f`, sukses `#6fe3a1`, stok menipis `#fdb022`). Header memakai token `chrome` (terpisah dari warna aksi `primary`), sticky di layar ≥ 56rem. Brand terang tidak terpengaruh.

## 6. Struktur kode

| File | Peran |
|---|---|
| `lib/landing-config.ts` | Konfigurasi per brand (bab, kategori, produk unggulan, copy DRAF), `findCategory`, `categoryHref`, `pickPhotos`, `chapterStats` |
| `app/page.tsx` | `LandingHome`: muat kanal, kategori, produk, produk per bab (statistik + foto) |
| `components/landing/Landing.tsx` | Server Component: susun segmen & section |
| `components/landing/PhotoStory.tsx` | Klien: panggung sticky, kartu foto parallax, segmen, HUD |
| `components/landing/Rail.tsx` | Klien: rak etalase + tombol ←/→ |
| `components/landing/landing.module.css` | Sistem visual landing (token brand saja) |

Dependency: `framer-motion`, `@fontsource-variable/fraunces`, `@fontsource-variable/geist`, `@fontsource-variable/geist-mono`.

## 7. Belum / menunggu

- Foto produk resolusi tinggi & font PB Signature berlisensi dari tim brand.
- Persetujuan copy (judul/lead `Brand.hero` & copy bab masih DRAF).
- Copy bab Bacha & GrandLucky (DRAF) menunggu tim brand; katalog GL masih tipis (Buah 4, Daging & Unggas 2 produk).

## 8. Bacha Coffee & GrandLucky (G25, 24 Okt 2026)

Konsep sama dengan PB (scroll story foto produk asli, rak etalase, band mode pesan, cara pesan, penutup); tema & tata letak per brand.

| | Bacha Coffee (`bacha.*`, kanal butik BCR) | GrandLucky (`grandlucky.*`, kanal GL) |
|---|---|---|
| Referensi | bachacoffee.com/en (diukur Playwright 24 Okt 2026) | grandlucky.co.id + file logo resmi |
| Bab (kategori katalog) | Kopi (Loose Coffee), Kemasan (Packed Coffee), Aksesori, Gourmet | Buah, Daging & Unggas, Sarapan Kopi & Teh, Camilan & Cokelat |
| Band tambahan | "Hadiah" = Gift Sets (pola "Personalise your gifts") | — |
| Header | navy + lencana unta resmi (`logoKind: 'mark'`) + pita ornamen `line-header.png` | putih + logo resmi berwarna, garis merah `#c42a1c` |
| Tipografi | ITC Quorum 500 kapital, spasi 0,06em, tanpa miring; nama produk Lora | Lato 900; kata penekanan merah tua + stabilo kuning logo |
| Bentuk | radius 2px (tombol, kartu, chip) | radius 8px tombol, 14–16px kartu |
| Penutup | foto kampanye resmi di bawah lapisan navy 86% | foto "Fresh" resmi di bawah lapisan biru 88%, tombol kuning `#f0d000` |

**Palet aksen landing (`LandingConfig.palette`).** Aksen resmi brand terang gagal sebagai teks di latar terang (oranye Bacha `#e07e27` di peach 2,59). Aksen dipisah jadi `ink` (teks), `fill` (tombol/garis) dan `onFill` (teks di tombol) → CSS `--l-ink/--l-fill/--l-on-fill`. Tanpa palet = aksen tema (PB, Wetzel's tidak berubah). Bacha: ink `#9a4a0c` (5,53 di peach, 6,16 di krem), tombol oranye berteks navy 5,12. GL: ink `#c42a1c` (5,13 / 5,68), tombol biru berteks putih 8,57. Band penutup dicek di kasus terburuk (lapisan × putih): Bacha `#3a4462` krem 9,48, `#f5c08f` 5,87; GL `#515aa7` putih 6,23, `#dfe2f5` 4,85, `#ffe14d` 4,79.

Judul band mode pesan kini dari mode yang aktif (`modesHeadline`), bukan kalimat tetap; judul cara pesan & penutup per brand (`howTitle`, `closing`). Keterangan HUD "Foto: katalog <brand>".

Terukur (Playwright, 24 Okt 2026): 1440/1280/1024/390/360 px × scroll turun-naik → teks tertutup foto 0%, kartu saling menimpa 0%, 13–28 keadaan gerak, teks bab terbaca, tanpa scroll horizontal, tanpa error browser, tanpa gambar rusak; PB & Wetzel's tidak berubah.

## 9. Foto utuh, tidak terpotong (G25, 24 Okt 2026)

Foto katalog berbeda rasio per PT: PB 497×347 lanskap, Bacha 410×512 potret 4:5, GrandLucky & Wetzel's persegi. Sebelumnya semua bingkai memakai `object-fit: cover` dengan rasio tetap (kartu etalase 4:5, kartu cerita 497:347, bingkai belanja 1:1 / 4:5), jadi foto PB di etalase hanya terlihat 34–55%, foto Bacha di kartu cerita 63%, detail produk 70–80%.

- `photoVars(company_code)` (lib/catalog.ts) memasang `--photo-ratio` dan `--photo-hw` di landing, `/belanja`, dan detail produk. Bingkai memakai rasio foto PT-nya, gambar `object-fit: contain` di latar putih.
- Kartu cerita: lebar dihitung dari `--photo-hw` agar kartu potret Bacha tetap muat tinggi layar; parallax foto ±3% di dalam ruang 3% bingkai (gerak scroll tetap ada).
- Super-App (`app.`) mencampur brand: hero 3D, etalase, dan panel memakai bingkai tetap dengan `contain`.
- Tes `tests/photo-fit.test.ts` melarang `object-fit: cover` pada CSS foto katalog.
- BFF: gambar katalog punya kuota rate limit sendiri `IMAGE` 600/menit/IP (docs/04 §10.3.1). Sebelumnya berbagi kuota `CATALOG` 120/menit dengan JSON katalog, sehingga halaman berisi 24–40 foto sebagian gagal dimuat (429, log dev).

Terukur (Playwright, 10 halaman × 1440 & 390 px): semua foto katalog terlihat 100% (sebelumnya minimum 34%); overlap teks/foto tetap 0% di 4 brand.
