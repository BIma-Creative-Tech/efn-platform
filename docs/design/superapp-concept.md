# Konsep Super-App EFN — nama, pola Eraspace, fitur, mobile, roadmap

Status: **DRAF 21 Okt 2026** (efn-lead). Usulan untuk PO/Marketing. Nama produk, copy, dan urutan gelombang **belum
disetujui**. Dokumen ini tidak mengubah kontrak; kontrak teknis booking & mode pesan = `docs/04 §15` (G23).

> **Ringkasan manajemen.** Super-App EFN mencontoh pola Eraspace (Erajaya Digital): satu aplikasi dan satu akun untuk
> semua brand, poin yang berlaku lintas brand, ambil di toko, dan halaman resmi tiap brand. Bedanya, EFN berjualan
> makanan, sehingga yang utama adalah **pesan untuk diambil, diantar, atau makan di tempat**, ditambah **reservasi meja**
> dan **pre-order kue/hampers dengan jadwal ambil**. Semua harga, stok, pajak, dan jadwal tetap dihitung **Odoo**.
> Gelombang G23 membangun reservasi meja, pre-order berjadwal, pemilih mode pesan, beranda Super-App bergaya
> Eraspace, dan aplikasi mobile terpisah (Expo).

## 1. Usulan nama produk

Konteks: Erajaya sudah memakai keluarga nama **Era-** (Eraspace, Erafone, EraPlay, MyEraspace). Nama berikut dicek
21 Okt 2026 hanya lewat DNS (`.com`/`.id`/`.co.id` tidak me-*resolve* = kemungkinan belum dipakai) dan pencarian web
singkat. **Ini bukan cek merek.** Sebelum dipilih: cek PDKI (DJKI kelas 9, 35, 39, 43), App Store/Play Store, dan
ketersediaan domain di registrar.

| # | Nama | Makna / alasan | DNS 21 Okt 2026 | Catatan |
|---|---|---|---|---|
| 1 | **EraRasa** (rekomendasi) | "Era" (Erajaya) + "rasa" (selera, cita rasa). Mudah diucap, Indonesia, cocok untuk roti, kopi, dan supermarket | `erarasa.com`/`.id`/`.co.id` tidak resolve | Program member: **MyEraRasa** / **Poin Rasa** (sejajar MyEraspace) |
| 2 | **EraSaji** | "saji" = menyajikan; kuat untuk F&B (dine-in, pre-order, catering) | tidak resolve | Kurang pas untuk GrandLucky (supermarket) |
| 3 | **EraTable** | Meja: reservasi, dine-in, keluarga | tidak resolve | Bahasa Inggris; kurang pas untuk belanja bahan |
| 4 | **EraFeast** | Kesan perayaan & premium (kue, hampers) | tidak resolve | Bahasa Inggris, lebih sempit |
| 5 | **EraNourish** | Langsung dari nama divisi (Food & **Nourishment**) | tidak resolve | Paling korporat, kurang hangat untuk konsumen |
| 6 | **Rasaspace** | Paralel langsung dengan Eraspace | **ada pemakai** (RASA Space, F&B Singapura) | Tidak disarankan |

Tagline DRAF untuk #1: *"Rasa favorit dari GrandLucky, Paris Baguette, dan Bacha Coffee — satu akun, satu poin."*

Nama aplikasi brand tetap nama brand (mis. "Paris Baguette Indonesia"), dengan baris kecil "bagian dari EraRasa".

## 2. Pola Eraspace yang diadopsi

Sumber: eraspace.com (21 Okt 2026) dan deskripsi resmi App Store/Play Store Eraspace. Yang dicontoh **polanya**, bukan
aset atau kode.

| Pola Eraspace | Padanan EFN | Status |
|---|---|---|
| Satu akun, banyak brand (Erafone, iBox, Urban Republic…), deretan logo brand di atas | Deretan brand GrandLucky · Paris Baguette · Bacha Coffee di beranda `app.` (link ke halaman brand) | **G23 dibangun** |
| MyEraspace: poin di semua jaringan, tier member | Poin EFN sudah lintas PT (ADR-020, W-29). Tier member = usulan G24 | Poin: ada · Tier: usulan |
| Click & PickUp (pesan online, ambil di toko) | Pesan online, ambil di gerai (G17–G22) | **Ada** |
| Pengiriman dari toko terdekat | Pesan-antar kurir internal (W-05) | Mode tampil "segera", backend G24 |
| Promo, voucher, flash sale khusus aplikasi | Kode promo online (G19), promo POS | Ada (kode); flash sale usulan |
| Bandingkan produk | Pantau Harga (Price Hunter, foto label kompetitor) | Ada |
| Gift card | Gift card / e-voucher Odoo CE `loyalty` (`program_type=gift_card`) | Usulan G24 |
| Lacak pesanan real-time | Status pesanan + KDS (siap diambil) | Ada |
| Halaman brand sendiri (`/asics`, `/garmin`) | Subdomain brand (`parisbaguette.`, `bacha.`, `grandlucky.`) dengan landing sendiri | Ada (PB), brand lain menyusul |

## 3. Mode pesan & reservasi (inti G23)

| Mode | Brand | Web brand | Super-App web | Mobile | Engine Odoo |
|---|---|---|---|---|---|
| **Ambil di gerai** (pickup) | semua | ada | ada | G23 | `efn_online_order` (SO) |
| **Pre-order berjadwal** (kue, hampers) — pilih tanggal & jam ambil | PB, BCF, BCR | **G23** | **G23** | **G23** | `efn_online_slot` (baru) → `sale.order.commitment_date` |
| **Makan di tempat** — scan QR meja | PB, BCF | ada (`/meja`) | ada | G23 (kamera) | `efn_pos_bff_order` (E-27) |
| **Reservasi meja** | PB, BCF | **G23** | **G23** | **G23** | `efn_table_booking` (baru) di atas `pos_restaurant` |
| **Pesan-antar** (delivery) | semua | tampil "segera hadir" | sama | sama | W-05 kurir internal (G24), 3PL menunggu vendor |

Pesan-antar sengaja **tidak** dinyalakan di G23: Odoo menolak `delivery_enabled` (Q-ON3), zona/ongkir/slot kurir dan
alamat terenkripsi (E-38b) belum ada. UI menampilkan mode ini nonaktif dengan teks jujur, bukan tombol palsu.

## 4. Fitur Super-App lain (usulan, sesuai konsep awal)

| Fitur | Nilai | Engine | Gelombang usulan |
|---|---|---|---|
| Tier member (Silver/Gold/Platinum) & reward lintas brand | Retensi (MyEraspace) | Ledger poin BFF (ADR-020) + kebijakan per PT (W-29) | G24 |
| Gift card & e-voucher digital | Hadiah, korporat | Odoo CE `loyalty` gift card | G24 |
| Pesan ulang (reorder) & favorit | Frekuensi beli | BFF (riwayat order) | G24 |
| Pesan untuk acara / katering korporat | B2B, nilai besar | `sale` + faktur atas nama (E-39) | G25 |
| Langganan roti/kopi (mingguan) | Pendapatan berulang | `sale` berulang (custom, CE tanpa subscription) | G25, perlu fit-gap |
| Member Get Member 2-tier | Akuisisi (blueprint) | BFF + OTP | sesuai rencana awal |
| Pantau Harga (Price Hunter) | Komunitas | sudah ada | — |
| Notifikasi WA (pesanan siap, pengingat reservasi H-1) | Kurangi no-show | WhatsApp Cloud API resmi, template Meta | G23 (event), template menunggu Meta |
| Ulasan & rating gerai | Kualitas | Odoo CE `rating` | G25 |

## 5. Aplikasi mobile (keputusan user 21 Okt 2026)

Repo/folder **terpisah** dari `efn-platform`: `E:\Projects\efn-mobile`. Di dalamnya setiap aplikasi berdiri sendiri:

| App | Isi | Bundle id usulan |
|---|---|---|
| `apps/superapp` | Semua brand, satu akun, poin, pesan, reservasi | `id.co.erajaya.efn.superapp` |
| `apps/parisbaguette` | Hanya Paris Baguette | `id.co.erajaya.efn.parisbaguette` |
| `apps/bacha` | Butik + kafe Bacha | `id.co.erajaya.efn.bacha` |
| `apps/grandlucky` | Supermarket GrandLucky | `id.co.erajaya.efn.grandlucky` |

Tidak ada paket kode bersama antar-app (keputusan user: "jangan disatukan"). Konsekuensi: perbaikan bug di satu app
harus diulang di app lain — biaya perawatan lebih tinggi, tetapi rilis per brand independen.

Aplikasi mobile **tidak** memanggil Odoo. Jalurnya: app → BFF (`api.`) → odoo-rpc-gateway → Odoo, sama dengan web.
Kontrak tambahan untuk mobile (token di secure storage, tanpa cookie) ada di `docs/04 §15.6`.

### Pedoman UI mobile (ScreensDesign)

Referensi: screensdesign.com (library 2.8 rb app; kategori *Food delivery apps*: Starbucks, Burger King, Subway, Too
Good To Go). Akses screen detail berbayar (Pro); yang dipakai adalah artikel publik dan daftar *chapter* alur:

- **Starbucks** (66 screen): izin notifikasi → daftar → cari toko → pesan makanan & minuman → keranjang → gift card.
  Diadopsi: cari gerai di awal, keranjang ringkas, gift card (G24).
- **Onboarding 2026** (artikel ScreensDesign, Agu 2026): tunjukkan satu hasil nyata sebelum menjelaskan fitur; minta
  akun hanya saat ada yang perlu disimpan; izin (lokasi/notifikasi) diminta saat manfaatnya terlihat. Diadopsi:
  beranda bisa dijelajah tanpa login; login OTP hanya saat checkout/reservasi; izin notifikasi setelah pesanan pertama.
- **OTP screen** (artikel ScreensDesign, Sep 2026): tampilkan nomor tujuan yang di-mask + bisa diubah, dukung paste &
  autofill (`textContentType="oneTimeCode"`), timer kirim ulang yang tidak bersaing dengan tombol utama, bedakan galat
  salah/kedaluwarsa/habis percobaan. Diadopsi di layar OTP semua app.
- Pola UI elemen: date picker & slot chip (reservasi/pre-order), QR code (kode ambil, scan meja), numeric keypad.

## 6. Bahasa visual web Super-App

Mengikuti landing Paris Baguette yang sudah berjalan (`docs/design/landing-pages.md`): tipografi Fraunces/Geist,
kartu bergaris tipis, gerak dipicu scroll/pointer (framer-motion), tanpa autoplay, `prefers-reduced-motion`, foto
produk asli dari katalog. Beranda `app.` (holding) mendapat landing sendiri bergaya Eraspace: deretan brand, panel
brand dengan foto produk asli yang bergerak mengikuti scroll (kedalaman 3D ringan: perspektif + rotasi kecil), mode
pesan, dan poin. Brand lain tidak berubah.

## 7. Belum / menunggu

- Persetujuan nama produk (PO + Marketing + Legal) dan cek PDKI.
- Teks syarat reservasi (batas telat, no-show, deposit) — Ops F&B + Legal. Pilot G23 **tanpa deposit**.
- Template WhatsApp pengingat reservasi (Meta).
- Akun Apple Developer / Google Play atas nama PT, ikon & splash resmi tiap brand.
