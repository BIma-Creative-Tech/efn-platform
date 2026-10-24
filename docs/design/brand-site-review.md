# Review fitur situs brand → peluang untuk EFN Super-App

Status: **DRAF 24 Okt 2026**. Sumber: crawl Playwright + HTML/CSS publik `parisbaguette.com` (PB AS), `parisbaguette.co.id`
(PB Indonesia/Singapura), `bachacoffee.com/en`, `wetzels.com` (robots.txt dihormati). Tujuan: daftar fitur yang terlihat di
situs resmi, dibandingkan dengan kemampuan EFN saat ini, plus rekomendasi gelombang. Bukan kontrak; fitur baru tetap
melalui docs/04 + kill switch sebelum dibangun.

## 1. Ringkasan

| # | Fitur di situs brand | Dilihat di | Status EFN (24 Okt 2026) | Rekomendasi |
|---|---|---|---|---|
| F-1 | **Katering**: halaman khusus, paket & tray ("serves 12"), jenis acara, panduan porsi, FAQ, CTA "Order catering" | PB AS `/catering` (Bundles, Breakfast, Lunch, À la carte, Beverages, Specialty Cakes, Questions?), PB ID `/catering` (4 jenis acara + WhatsApp), Wetzel's `/menu/catering` (11 paket, panduan 10–15/25–40/50+ orang, FAQ) | **Sebagian, dibangun G24**: band katering di landing PB & Wetzel's (jenis acara, rak paket dari kategori katalog, panduan porsi, CTA ke pre-order berjadwal G23 `?mode=preorder`); paket = produk biasa di Odoo (Wetzel's: kategori `Catering`) | G25: **pesanan katering** = pre-order dengan minimal waktu tunggu lebih panjang per kategori (`slot_lead_minutes` per produk/kategori), jumlah porsi (`serves`) sebagai atribut produk, form permintaan khusus (tanpa PII bebas → catatan terfilter), opsi antar menunggu L-88 |
| F-2 | **Rewards / loyalty** "Earn, Redeem, Repeat", gratis pastry saat daftar | PB AS (`/rewards`), Wetzel's (tombol "Wetzel's Pretzels Rewards") | **Ada**: poin lintas PT (G21/G22), earn saat ambil, redeem sebagai alat bayar | Tambah **bonus pendaftaran** (promo poin awal) + halaman `/poin` yang menjelaskan earn/redeem per brand |
| F-3 | **Featured / musiman** (kartu "Featured items" bergambar besar, slider promo) | PB AS (Caramel Apple, Pumpkin), Wetzel's (slider beranda), Bacha (koleksi "Pura Vida") | Belum: landing memakai kategori katalog tetap | G25: **koleksi musiman** = kategori/tag Odoo `efn_featured` + tanggal mulai/akhir, dipakai rak landing (data, bukan copy hardcode) |
| F-4 | **Pencari lokasi** (peta, jam buka, layanan per gerai) | PB AS `/locations`, PB ID `/locations`, Wetzel's `/locator` | Sebagian: tabel gerai ambil sendiri + menit siap di landing | Halaman `/gerai`: daftar outlet kanal + jam (resource calendar Odoo) + layanan (ambil, pre-order, QR meja); peta butuh tile server sendiri (CSP) → tahap kemudian |
| F-5 | **Gift card / e-voucher** | PB AS `/gift-cards`, Wetzel's `/gift` | Belum | Butuh keputusan akuntansi (liabilitas per PT, mirip poin ADR-020) → pertanyaan klien |
| F-6 | **Personalisasi hadiah & gift set**, tote bag gratis di atas nominal | Bacha eBoutique | Sebagian: katalog BCR punya kategori Gift Sets | Promo "hadiah di atas nominal" = aturan promo POS/online (Odoo `loyalty` program) → G25 bila diminta |
| F-7 | **Aplikasi ponsel** (App Store / Google Play) | PB AS, Wetzel's | Ada: repo `efn-mobile` (4 aplikasi Expo) | Tautan unduh di footer setelah aplikasi terbit |
| F-8 | **Nutrisi & alergen** | PB AS `/nutritional`, Wetzel's (Nutrition) | Belum | Atribut produk Odoo (alergen, kalori) → tampil di detail produk; data dari tim brand |
| F-9 | **Food truck / booking truk** | Wetzel's `/food-trucks`, `/book-a-truck` | Tidak relevan sekarang | Bisa memakai pola reservasi G23 bila Wetzel's Indonesia punya truk |
| F-10 | **Franchise / karier / kontak** | semua | Di luar Super-App (situs korporat) | Tautan ke situs brand saja |
| F-11 | **Pesan online + antar** (Olo / delivery) | PB AS, Wetzel's | Ambil di gerai + pre-order; antar = "Segera hadir" (L-88) | Tetap menunggu keputusan delivery |

## 2. Katering — detail rancangan (F-1)

Yang sudah ada di G24 (frontend, tanpa kontrak baru):
- `lib/landing-config.ts` → `LandingConfig.catering` (judul, isi, kategori katalog, 4 jenis acara, panduan porsi, tautan konsultasi
  situs resmi). Komponen `components/landing/Landing.tsx` (band `#katering`, `data-catering`).
- Foto & harga paket dari katalog BFF (bukan hardcode); CTA ke pre-order berjadwal bila ada outlet `slot_enabled`.
- PB: kategori `Cakes` + tautan "Konsultasi katering" ke `parisbaguette.co.id/catering/`. Wetzel's: kategori `Catering`
  (11 paket dari wetzels.com, harga DEMO placeholder).

Usulan G25 (perlu kontrak docs/04 + kill switch `CATERING_ENABLED`):
1. Atribut produk `efn_serves` (porsi) + `efn_catering_lead_minutes` (min. waktu pesan, default 24 jam) → dikirim katalog
   bila diisi (field opsional, payload lama tetap identik).
2. Slot pre-order memakai `max(slot_lead_minutes outlet, lead katering produk di keranjang)`.
3. Catatan acara terfilter (tanpa '@', tanpa deret digit mirip telepon; pola PII minimal BFF) → diteruskan ke Odoo sekali.
4. Jumlah minimal pesanan per paket = aturan Odoo (`max_qty`/`min_qty` per produk), bukan di frontend.

## 3. Font & tampilan

Lihat `docs/design/brand-tokens.md` §6 (font per peran per brand, diukur dari situs resmi) dan
`services/superapp-web/public/brands/README.md` (logo & tekstur latar section beserta sumbernya).
