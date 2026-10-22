# Design token per brand — Super-App EFN

Status: **DRAF 20 Okt 2026** (efn-uiux-designer). Sumber nilai: `services/superapp-web/lib/brands.ts` (`BRANDS[*].theme`) → `themeToCssVars()` → atribut `style` di `<html data-brand>`; fallback di `app/globals.css :root`. Komponen sama untuk semua brand (R-T14).

Warna dasar diambil dari situs resmi brand (riset 20 Okt 2026: grandlucky.co.id, parisbaguette.co.id, bachacoffee.com). Warna turunan (latar lembut, teks muted, border) dipilih desainer agar lolos WCAG — **wajib dikonfirmasi tim brand** sebelum go-live.

## 1. Token CSS

| CSS var | Field `BrandTheme` | Dipakai untuk | Validasi (`themeToCssVars`) |
|---|---|---|---|
| `--color-primary` | `primary` | header, tombol utama, tautan, judul hero, chip aktif | `#rrggbb` |
| `--color-primary-contrast` | `primaryContrast` | teks di atas primer | `#rrggbb` |
| `--color-accent` | `accent` | **dekoratif saja**: garis bawah header | `#rrggbb` |
| `--color-accent-soft` | `accentSoft` | latar hero beranda | `#rrggbb` |
| `--color-focus` | `focus` | outline fokus keyboard | `#rrggbb` |
| `--color-surface` / `--color-surface-alt` | `surface` / `surfaceAlt` | kartu / latar halaman | `#rrggbb` |
| `--color-text` / `--color-text-muted` | `text` / `textMuted` | teks utama / sekunder (hint, catatan pajak) | `#rrggbb` |
| `--color-border` | `border` | garis kartu (dekoratif; input memakai `--color-text-muted`) | `#rrggbb` |
| `--font-body` / `--font-heading` | `fontBody` / `fontHeading` | body / h1–h3, logo teks, nama produk | `[A-Za-z0-9 ,'-]`, ≤ 200 karakter |
| `--heading-weight`, `--heading-letter-spacing` | `headingWeight`, `headingLetterSpacing` | gaya judul | `[1-9]00`; `normal`/`0`/`.NNem` |
| `--logo-transform`, `--logo-letter-spacing` | `logoTransform`, `logoLetterSpacing` | logo teks | `none`/`uppercase`; spasi seperti di atas |
| `--radius` | `radius` | kartu, tombol, input | `N(px|rem)` |
| `--card-shadow` | `cardShadow` | kartu & kartu produk | `none` atau `0 Npx Npx rgba(r, g, b, .NN)` |

Nilai yang tidak lolos pola **dibuang** (CSS jatuh ke `:root`), tidak di-escape. Semua nilai adalah konstanta kode, bukan input user; validasi = defense in depth (diuji di `tests/brands.test.ts`).

Warna status bersama (tidak per brand): `--color-danger #b42318`, `--color-success #067647`, `--color-warning-bg #fffaeb`, `--color-warning-border #f79009`, stok menipis `#b54708`.

## 2. Nilai per brand

| Token | EFN (holding) | GrandLucky | Paris Baguette | Bacha Coffee |
|---|---|---|---|---|
| primary | `#0b5d4b` | `#39439b` (resmi) | `#002663` (resmi, navy) | `#1a2548` (resmi, navy) |
| primaryContrast | `#ffffff` | `#ffffff` | `#ffffff` | `#fffef1` (krem resmi) |
| accent (dekoratif) | `#f2a900` | `#9aa1dc` (turunan; aksen promo resmi menunggu brand) | `#fcb134` (resmi, kuning) | `#ae944d` (resmi, emas) |
| accentSoft (hero) | `#e7f2ef` | `#eef0fa` | `#fff4de` | `#ffe7d5` (peach resmi) |
| focus | `#0b5d4b` | `#39439b` | `#002663` | `#1a2548` |
| surface | `#ffffff` | `#ffffff` | `#ffffff` | `#fffef1` (krem resmi) |
| surfaceAlt | `#f3f6f5` | `#f2f3f9` | `#f3f5f9` | `#fbf6ec` |
| text | `#14211d` | `#1c2033` | `#343538` (abu resmi) | `#1a2548` |
| textMuted | `#4a5a55` | `#4d5268` | `#55575c` | `#585c6e` |
| border | `#cfd8d5` | `#d3d6e8` | `#aebcd3` (biru muda resmi) | `#e2d6b8` |
| fontBody | system-ui stack | `Lato,` + system-ui stack | `Inter,` + system-ui stack | `Lora,` + Georgia stack |
| fontHeading | system-ui stack | `Lato,` + system-ui stack | Georgia, 'Times New Roman', serif | Georgia, 'Times New Roman', serif |
| headingWeight / spacing | 800 / normal | 800 / normal | 700 / normal | 400 / 0.02em |
| logo | normal | normal | UPPERCASE, 0.08em | UPPERCASE, 0.2em |
| radius | 10px | 8px | 12px | 2px |
| cardShadow | none | none | `0 2px 8px rgba(0, 38, 99, 0.08)` | none |

Font: `Lato`/`Inter`/`Lora` di awal stack **hanya dipakai bila terpasang di perangkat** — tidak diunduh (CSP `font-src 'self'`, tanpa Google Fonts). Font proprietary (PB Signature, ITC Quorum) **tidak disalin**. Bila tim brand memberi file berlisensi: taruh di `public/fonts/<brand>/`, tambah `@font-face` di `globals.css`, lalu tambahkan nama family di depan `fontHeading`.

## 3. Kontras (dihitung, rumus WCAG 2.x relative luminance)

Ambang: teks normal ≥ 4.5:1; teks besar & indikator fokus/komponen UI ≥ 3:1. Semua pasangan teks di bawah juga diuji otomatis (`tests/brands.test.ts`, "kontras teks ≥ 4.5:1, fokus ≥ 3:1").

| Pasangan | EFN | GrandLucky | Paris Baguette | Bacha |
|---|---|---|---|---|
| text / surface | 16.60 ✅ | 16.11 ✅ | 12.26 ✅ | 14.76 ✅ |
| text / surfaceAlt | 15.27 ✅ | 14.55 ✅ | 11.24 ✅ | 13.90 ✅ |
| text / accentSoft (hero) | 14.50 ✅ | 14.17 ✅ | 11.24 ✅ | 12.58 ✅ |
| textMuted / surface | 7.28 ✅ | 7.71 ✅ | 7.23 ✅ | 6.53 ✅ |
| textMuted / surfaceAlt | 6.69 ✅ | 6.97 ✅ | 6.63 ✅ | 6.15 ✅ |
| textMuted / accentSoft | 6.36 ✅ | 6.79 ✅ | 6.63 ✅ | 5.56 ✅ |
| primary (tautan, judul) / surface | 7.83 ✅ | 8.57 ✅ | 14.41 ✅ | 14.76 ✅ |
| primary / surfaceAlt | 7.20 ✅ | 7.74 ✅ | 13.20 ✅ | 13.90 ✅ |
| primary / accentSoft | 6.84 ✅ | 7.54 ✅ | 13.21 ✅ | 12.58 ✅ |
| primaryContrast / primary (header, tombol) | 7.83 ✅ | 8.57 ✅ | 14.41 ✅ | 14.76 ✅ |
| focus / surface (≥ 3) | 7.83 ✅ | 8.57 ✅ | 14.41 ✅ | 14.76 ✅ |
| accent / primary (garis header, dekoratif) | 3.90 | 3.47 | 7.88 | 5.09 |
| **accent / surface** | **2.01 ❌** | **2.47 ❌** | **1.83 ❌** | **2.90 ❌** |

Kesimpulan: **accent tidak boleh dipakai untuk teks atau indikator di atas latar terang** di brand mana pun (kuning PB & emas Bacha gagal). Karena itu fokus memakai `--color-focus` (UX-03). Aksen boleh untuk teks di atas `primary` (PB 7.88, Bacha 5.09 — lolos 4.5:1; EFN & GL tidak).

Warna status di latar Bacha (krem): danger/`#fffef1` 6.48, success/`#fffef1` 5.61, stok menipis 5.35, danger/`#fbf6ec` 6.10, success/`#fbf6ec` 5.28 — semua ✅. Alert: danger/`#fef3f2` 6.05, success/`#ecfdf3` 5.40 ✅.

## 4. Gaya kartu produk & nada copywriting

| Brand | Kartu produk | Nada copy (Bahasa Indonesia) | Contoh |
|---|---|---|---|
| EFN (holding) | radius 10, border tipis, tanpa bayangan | netral, informatif; menonjolkan "satu akun, satu poin" | "Semua brand EFN, satu akun" |
| GrandLucky | radius 8, padat (grid 2 kolom di ponsel), harga tebal menonjol; ruang untuk badge promo (belum ada data promo per produk) | lugas, segar, fokus kualitas & kesegaran; boleh menyebut lokal/impor | "Segar setiap hari, lokal hingga impor" |
| Paris Baguette | radius 12, bayangan lembut navy, foto besar (rasio 1:1), judul serif | hangat, mengundang, kata "segar", "dipanggang"; hindari klaim yang belum disetujui brand | "Roti dan kue segar, setiap hari" |
| Bacha Coffee | radius 2 (tegas, klasik), tanpa bayangan, latar krem, serif, banyak ruang kosong, huruf logo berjarak lebar | elegan, tenang, heritage (Marrakech, 1910); jelaskan butik vs kafe = dua PT secara sopan | "Kopi pilihan dari rumah kopi bersejarah Maroko" |

Aturan copy lintas brand: pajak dijelaskan jujur (ritel: "harga sudah termasuk pajak"; F&B: "rincian pajak per PT di keranjang"); keranjang lintas PT selalu menyebut jumlah pesanan terpisah; hindari jargon teknis/kode internal di UI.

## 5. Logo & aset

- Saat ini logo = teks bergaya (`Brand.logoText`). Tidak ada file logo/foto/font yang disalin dari situs brand.
- Logo SVG resmi dari tim brand → `public/brands/<id>/logo.svg`, isi `Brand.logoSrc` (`/brands/<id>/logo.svg`). `safeLogoSrc()` hanya menerima path same-origin berpola itu (CSP `img-src 'self'`).
- Brand Erajaya lain (Curry Up, CHAGEE, Wetzel's Pretzels) belum punya company di Odoo → **belum ada tenant**; token disiapkan setelah company & kanal dibuat.
