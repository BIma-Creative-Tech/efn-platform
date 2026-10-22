# Audit UI/UX & Aksesibilitas — Super-App EFN (`services/superapp-web`)

Tanggal: 20 Okt 2026 · Auditor: efn-uiux-designer · Acuan: WCAG 2.2 AA, `CLAUDE.md` (R-T14, CSP), `docs/design/brand-tokens.md`.

**Metode:** membaca kode semua halaman di `app/` + komponen bersama, menghitung kontras token, dan merender HTML server lewat container dev (`next dev`) untuk 4 tenant × 5 halaman (status 200, `data-brand` benar). **Belum** diuji di browser visual, pembaca layar (NVDA/TalkBack/VoiceOver), atau perangkat nyata; data katalog dev kosong ("Belum ada toko online"), jadi kartu produk/keranjang terisi hanya diaudit dari kode.

**Halaman yang diaudit:** beranda (`app/page.tsx`), belanja (`app/belanja`), detail produk (`app/belanja/[id]`), keranjang (`app/keranjang` + `CartView`), pesanan (`app/pesanan`, `[id]`), meja/QR (`app/meja` + `components/fnb/*`), masuk (`app/masuk`), akun (`app/akun/**`), pantau harga (`app/pantau-harga/**`), privasi, 404, layout/header/footer.

**Yang sudah baik (pertahankan):** skip-link + `main` fokusabel; label form lengkap & `aria-describedby` untuk hint; `role=alert`/`aria-live` untuk galat & status; keranjang lintas PT jujur ("N pesanan terpisah, masing-masing dengan nota dan pajaknya sendiri"); harga F&B vs ritel dibedakan di belanja/detail; tombol qty 44 px; `lang="id"`; galat OTP generik; QR meja tidak menyimpan token.

## Ringkasan temuan

| ID | Prioritas | Temuan | File | Status |
|---|---|---|---|---|
| UX-01 | Tinggi | Tenant placeholder "EFN Mart"/"EFN Kitchen" tidak mewakili brand nyata; Bacha Coffee tidak punya tenant; tema tidak sesuai identitas brand | `lib/brands.ts` | **Diperbaiki** |
| UX-02 | Sedang | Navigasi utama tidak menandai halaman aktif (`aria-current`) | `components/SiteHeader.tsx` | **Diperbaiki** |
| UX-03 | Tinggi | Outline fokus memakai `--color-accent` (kuning `#f2a900` di putih = 2.01:1; PB kuning 1.83:1, Bacha emas 2.90:1) < 3:1 — gagal WCAG 1.4.11 | `app/globals.css` | **Diperbaiki** |
| UX-04 | Tinggi | Beranda menyatakan "Harga sudah termasuk pajak" untuk semua brand, padahal kanal F&B (PB, BCF) menampilkan harga sebelum pajak — informasi pajak menyesatkan | `app/page.tsx` | **Diperbaiki** |
| UX-05 | Tinggi | Beranda generik (judul = nama app, tagline), tanpa hero/identitas brand dan tanpa ajakan yang jelas | `app/page.tsx`, `app/globals.css` | **Diperbaiki** |
| UX-06 | Rendah | `.btn-small` ±33 px: lolos SC 2.5.8 (24 px) tetapi di bawah 44 px | `app/globals.css` | **Diperbaiki** (min-height dijamin; 44 px untuk `.btn`) |
| UX-07 | Sedang | Footer "bagian dari grup EFN" — singkatan tidak dikenal pelanggan; brand wajib menyebut induknya | `app/layout.tsx` | **Diperbaiki** |
| UX-08 | Sedang | Catatan pajak/satuan di kartu produk 12 px — terlalu kecil untuk informasi harga | `components/online/online.module.css` | **Diperbaiki** (13 px) |
| UX-09 | Sedang | Remah roti detail produk kehilangan `channel` → di Bacha (BCR + BCF) "Belanja"/"Kategori" kembali ke kanal pertama, bukan kanal asal | `app/belanja/[id]/page.tsx` | **Diperbaiki** |
| UX-10 | Tinggi | Di domain brand, kanal Odoo tanpa `brand_code` tidak tampil → halaman Belanja "Belum ada toko online" walau kanal ada | data Odoo `efn.online.channel.brand_code`; `components/online/OnlineUnavailable.tsx` | **Ditunda** (konfigurasi data, lihat rekomendasi) |
| UX-11 | Sedang | Bilah bayar/kirim `position: sticky` di bawah dapat menutupi elemen yang difokus (WCAG 2.2 SC 2.4.11) | `online.module.css .checkoutBar`, `fnb.module.css` | **Sebagian** (`scroll-padding-bottom`); perlu uji browser |
| UX-12 | Sedang | `<select>` toko ambil di keranjang memakai gaya inline, tanpa border token (kontras batas input tak terjamin) | `components/online/CartView.tsx` | **Sebagian** (`.field select` di globals); inline style sebaiknya dihapus |
| UX-13 | Sedang | Header: 6 item nav + logo panjang ("PARIS BAGUETTE" uppercase berjarak) membungkus 2–3 baris di layar 320–360 px; konten turun jauh | `components/SiteHeader.tsx`, `globals.css` | **Ditunda** |
| UX-14 | Sedang | Tidak ada foto/visual brand di beranda; gaya PB & Bacha bertumpu pada foto besar | `app/page.tsx` | **Ditunda** (butuh aset resmi) |
| UX-15 | Sedang | Nama PT internal tampil ke pelanggan ("Bacha Coffee Retail", "Bacha Coffee F&B", "PT Grand Lucky") di beranda, keranjang, pesanan | `lib/brands.ts COMPANIES`, `lib/catalog.ts companyDisplayName` | **Ditunda** (nama badan hukum menunggu klien) |
| UX-16 | Sedang | Menu "Pantau Harga" (lapor harga pesaing) tampil di semua brand termasuk Bacha (premium) — potensi tidak selaras positioning brand | `components/SiteHeader.tsx` | **Ditunda** (keputusan bisnis) |
| UX-17 | Rendah | `/meja` dapat dibuka di domain non-F&B (GrandLucky) dan saat memuat tidak punya `<h1>` (render client) | `app/meja/page.tsx`, `components/fnb/TableOrder.tsx` | Ditunda |
| UX-18 | Rendah | Banyak `style={{ margin: 0 }}` inline — sulit dikontrol per brand, rawan inkonsisten | banyak file `app/**` | Ditunda |
| UX-19 | Rendah | Region `aria-live` membungkus `role="alert"` (LoginForm, PriceReportForm) → sebagian pembaca layar mengumumkan dua kali | `app/masuk/LoginForm.tsx`, `app/pantau-harga/PriceReportForm.tsx` | Ditunda |
| UX-20 | Rendah | Tidak ada mode gelap / `prefers-color-scheme`; animasi minim (aman) | `app/globals.css` | Ditunda |

Tinggi: 5 — **4 diperbaiki (UX-01, 03, 04, 05)**, 1 ditunda (UX-10, butuh data Odoo).

## Detail & rekomendasi

### UX-01 — Tenant brand nyata (Tinggi, diperbaiki)
Ganti `efn-mart` → `grandlucky` (PT GL), `efn-kitchen` → `parisbaguette` (PT PB), tambah `bacha` (BCR + BCF, satu domain). `brandCode` GL/PB/BACHA (keputusan user 20 Okt; UI saja). Alias subdomain lama `mart.`/`kitchen.` tetap diterima lewat `subdomainAliases`. Token warna/tipografi/radius per brand di `BrandTheme` (lihat `docs/design/brand-tokens.md`); satu set komponen (R-T14).

### UX-03 — Fokus keyboard (Tinggi, diperbaiki)
Token baru `--color-focus` (= primer brand, ≥ 7.8:1 di latar terang). Di header (latar primer) outline dibalik ke `--color-primary-contrast`. Aksen tetap dekoratif (garis header).

### UX-04 — Kalimat pajak beranda (Tinggi, diperbaiki)
Beranda kini memakai `allPpnCompanies(brand.companies)`: hanya PT ritel → "Harga sudah termasuk pajak"; ada PT F&B → "Rincian pajak per PT ditampilkan di keranjang sebelum Anda membayar". Tidak ada perhitungan pajak di frontend.

### UX-05 — Hero beranda per brand (Tinggi, diperbaiki)
`Brand.hero` (eyebrow, judul, lead) dari config, kelas `.hero` bersama memakai `--color-accent-soft` + garis primer; langkah "pilih → bayar QRIS → ambil" sebagai daftar bernomor. Copy DRAF — perlu persetujuan tim brand/marketing.

### UX-10 — Katalog kosong di domain brand (Tinggi, ditunda)
Konsekuensi `brandCode` per tenant: BFF menyaring `?brand=GL|PB|BACHA`. Rekomendasi: (1) efn-odoo-functional mengisi `brand_code` di setiap `efn.online.channel` (GL, PB, BACHA untuk BCR & BCF) sebelum domain brand dibuka; (2) efn-frontend-engineer: di domain brand, pesan kosong menyebut nama brand dan menautkan ke Super-App holding (`app.`) bila tersedia; (3) QA: skenario UAT "domain brand, kanal tanpa brand_code".

### UX-11 — Fokus tertutup bilah sticky (Sedang, sebagian)
`html { scroll-padding-bottom: 6rem }` menjaga elemen yang di-scroll ke tampilan tidak tertutup bilah. Verifikasi dengan Tab di keranjang & meja pada ponsel; bila masih tertutup, tambahkan `padding-bottom` setinggi bilah pada kontainer halaman keranjang (seperti `.page` di fnb).

### UX-13 — Header di ponsel (Sedang, ditunda)
Spesifikasi untuk efn-frontend-engineer: ≤ 30rem tampilkan logo + tombol "Menu" (`<button aria-expanded aria-controls>`, tanpa library) yang membuka daftar nav; atau tab bar bawah 4 item (Beranda, Belanja, Keranjang, Akun) dengan ikon SVG inline + label teks. Target sentuh 44 px, fokus dikembalikan ke tombol saat menu ditutup (Esc).

### UX-14 — Visual brand (Sedang, ditunda)
Minta foto hero & logo SVG resmi dari tim brand; simpan di `public/brands/<id>/` (same-origin, CSP aman). Tambah field opsional `hero.imageSrc` dengan validasi path seperti `safeLogoSrc`. Jangan menyalin dari situs brand.

### UX-15 — Nama PT di UI (Sedang, ditunda)
Bedakan nama hukum (nota/faktur) dan label pelanggan: mis. `customerLabel` "Butik Bacha Coffee" / "Kafe Bacha Coffee" / "GrandLucky Superstore". Butuh konfirmasi klien; menyentuh `COMPANIES` yang dipakai keranjang/pesanan → paket kerja frontend tersendiri + test.

### UX-16 — "Pantau Harga" per brand (Sedang, ditunda)
Usulan: field `features.priceHunter: boolean` per tenant (default true untuk efn & grandlucky). Keputusan bisnis — ajukan ke user.

### UX-12, UX-17..UX-20 (Sedang/Rendah)
- UX-12: hapus `style` inline pada `<select>` di `CartView`; bungkus dengan `.field` (sudah) agar gaya global berlaku.
- UX-17: `/meja` di tenant tanpa PT F&B → tampilkan pesan "Pesan dari meja tersedia di gerai F&B" + `<h1>` di status memuat.
- UX-18: tambah utilitas `.m-0`, `.mt-0` di globals; ganti bertahap.
- UX-19: pilih salah satu — `role="alert"` saja, atau region `aria-live` tanpa `role=alert` di anak.
- UX-20: mode gelap setelah token brand final (butuh set warna kedua yang lolos kontras).

## Brand berikutnya
Curry Up, CHAGEE, Wetzel's Pretzels (Erajaya) belum punya company di Odoo → tidak dibuat tenant. Langkah: efn-odoo-functional membuat company + kanal (`brand_code`), lalu tambah entri `BRANDS` + token (dihitung kontrasnya) + host/subdomain.
