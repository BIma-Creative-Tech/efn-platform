# superapp-web — EFN Super-App & brand sites (Next.js 16 App Router)

**Tujuan:** satu codebase untuk Super-App holding (`app.`) dan situs brand (`grandlucky.`, `parisbaguette.`, `bacha.`), tema per brand lewat CSS custom properties (token: `docs/design/brand-tokens.md`).
**ADR terkait (Proposed):** multi-tenant by domain, unified cart lintas PT (cart dipisah per PT: PPN vs PB1), OTP login via BFF, ADR-034/019 (belanja online).
**Risiko dimitigasi:** R-T14 (duplikasi komponen per brand), kebocoran token (cookie HttpOnly, tidak ada token di JS), PDP (lokasi opt-in per laporan, EXIF dibuang di client).

## Struktur

| Path | Isi |
|---|---|
| `proxy.ts` | (Next 16, dulu `middleware.ts`) Host -> brand, set request header `x-efn-brand` (dev: `?brand=grandlucky`) |
| `lib/brands.ts` | Konfigurasi tenant (`efn`, `grandlucky`, `parisbaguette`, `bacha`), company (EFN/GL/PB/BCR/BCF/FARM; Bacha = BCR+BCF), token tema (warna, font stack, radius, bayangan) + copy hero; `themeToCssVars` memvalidasi setiap nilai sebelum masuk `style` |
| `lib/bff.ts` | Klien BFF server-side (`BFF_INTERNAL_URL`), tipe kontrak docs/04 |
| `lib/bff-config.ts` | Guard transport BFF (https wajib, CA internal) — murni, diuji unit |
| `lib/safe-next.ts` | Validator `?next=` anti open-redirect (SEC-13) |
| `lib/session.ts` | Cookie `efn_session` (HttpOnly, Secure, SameSite=Lax) |
| `app/api/auth/otp/{request,verify}`, `app/api/auth/logout` | Route Handler login/logout |
| `app/api/market-scout/reports` | Proxy streaming multipart ke BFF |
| `app/masuk`, `app/keluar`, `app/akun`, `app/pantau-harga`, `app/pantau-harga/[id]`, `app/privasi` | Halaman |
| `app/api/auth/challenge`, `components/AltchaWidget.tsx`, `lib/altcha.ts` | Captcha ALTCHA self-hosted (docs/04 §8.1) |
| `app/akun/privasi`, `app/api/me/consents`, `lib/consent.ts` | Consent lokasi & marketing (docs/04 §8.4) |
| `app/akun/data-saya`, `app/api/me/{data,reauth,requests,delete/cancel}`, `app/api/me` (DELETE), `lib/dsar.ts` | DSAR: unduh data, hapus akun (re-OTP step-up), batal, riwayat. G18 W-18: `409 open_online_orders` → penjelasan → konfirmasi `confirm_open_orders` (step-up sama) → 202 dengan `erasure_deadline` (maks 14 hari). L-22: status (`/api/dsar/requests/:id`, `/api/me/requests`) membawa `open_online_orders` + `erasure_deadline` dari BFF; `?batas=` hanya cadangan BFF lama |
| `lib/auth-age.ts`, `app/masuk/LoginForm.tsx`, `app/api/auth/otp/verify` | G18 W-19 (Q-P3): checkbox "Saya berusia 17 tahun atau lebih" (tidak tercentang default) → `age_confirmed: true`; tanpa tanggal lahir |
| `app/akun/penghapusan/[id]`, `app/api/dsar/requests/[id]`, `lib/dsar-receipt.ts` | R6 (docs/04 §8.13): status hapus akun tanpa sesi via cookie HttpOnly `efn_dsar_receipt` (Path `/api/dsar`, SameSite=Strict, ≤ 30 hari) |
| `app/belanja`, `app/belanja/[id]`, `app/keranjang`, `app/pesanan`, `app/pesanan/[id]`, `components/online/**` | Belanja online Fase 3 (lihat bagian di bawah) |
| `app/api/catalog/**`, `app/api/cart/**`, `app/api/orders/**`, `lib/{catalog,cart,orders}*.ts` | Route Handler & modul belanja online |

## Tenant & cara mencoba brand di dev

| Tenant (`BrandId`) | Host dev | Subdomain prod | Alias lama | Company (PT) | `brandCode` |
|---|---|---|---|---|---|
| `efn` (default, holding) | `app.efn.local`, `efn.local`, `localhost` | `app.` | — | GL, PB, BCR, BCF | `null` (semua kanal) |
| `grandlucky` | `grandlucky.efn.local` | `grandlucky.` | `mart.` | GL | `GL` |
| `parisbaguette` | `parisbaguette.efn.local` | `parisbaguette.` | `kitchen.` | PB | `PB` |
| `bacha` | `bacha.efn.local` | `bacha.` | — | BCR (butik) + BCF (kafe) | `BACHA` |

- **Hosts file** (lihat README root): `127.0.0.1 app.efn.local grandlucky.efn.local parisbaguette.efn.local bacha.efn.local`.
- **Tanpa hosts file (dev saja):** buka `http://localhost:3001/?brand=bacha` — proxy menyimpan cookie `efn_dev_brand` sehingga halaman lain tetap memakai brand itu; kembali dengan `?brand=efn`. Diabaikan saat `NODE_ENV=production`.
- Host/subdomain tak dikenal → `efn` (bukan error). `brandCode` hanya membatasi kanal di UI; BFF tidak mempercayai domain (L-33 diperbarui 20 Okt 2026). Akibatnya **kanal Odoo tanpa `brand_code` hanya tampil di domain holding** — isi `brand_code` kanal (GL/PB/BACHA) agar katalog muncul di domain brand.
- Logo: teks bergaya (`logoText`). Logo SVG resmi dari tim brand → `public/brands/<id>/logo.svg` lalu isi `logoSrc` (hanya path same-origin yang lolos `safeLogoSrc`).
- Font: hanya font stack sistem (CSP `font-src 'self'`). File font berlisensi bisa di-self-host di `public/fonts` + `@font-face` di `globals.css`, lalu nama family-nya sudah ada di stack (`Lato`, `Inter`, `Lora`).
- Container dev `next dev` dengan bind mount dari Windows **tidak selalu hot-reload** (event file tidak sampai) → `docker restart efn-superapp-web-1` setelah mengubah `lib/`/`app/`.

## Env

| Nama | Default | Keterangan |
|---|---|---|
| `BFF_INTERNAL_URL` | `https://api-gateway:3000` | Wajib `https://`. Fallback ke `API_INTERNAL_URL`. Divalidasi saat boot (`instrumentation.ts`) |
| `NODE_EXTRA_CA_CERTS` | (kosong) | Path CA internal, mis. `/run/secrets/efn_ca_crt`. Wajib di production; file harus PEM terbaca |
| `EFN_ALLOW_PLAINTEXT` | (kosong) | `true` mengizinkan `http://` ke BFF — **ditolak** bila `NODE_ENV`/`APP_ENV=production` |
| `HOSTNAME` / `PORT` | `0.0.0.0` / `3001` | Alamat listen. Di balik sidecar TLS ghostunnel: `HOSTNAME=127.0.0.1` |
| `COOKIE_INSECURE` | (kosong) | `true` hanya untuk dev tanpa HTTPS; diabaikan saat `NODE_ENV=production` |
| `DSAR_DELETE_GRACE_DAYS` | `7` | Hanya untuk teks (perkiraan masa tenggang); samakan dengan nilai BFF. Tanggal pasti dari `scheduled_at` BFF |
| `EFN_PAYMENT_SIMULATOR` | (kosong) | `true` menampilkan tombol simulasi bayar provider `mock` + mengaktifkan `/api/orders/:id/simulate-payment`. Dev (`next dev`) atau staging dengan `APP_ENV=staging`; selalu mati bila `APP_ENV=production` atau build production tanpa `APP_ENV`. BFF tetap 404 di production |

## Belanja online (Fase 3, G17)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §10.2, §10.3, §10.12, §10.15–§10.16. ADR (Proposed): ADR-034 (kanal online lewat BFF), ADR-019 (bagian per kanal/PT, satu pembayaran), ADR-016/026 (provider `mock`), ADR-027 (pajak dari Odoo), ADR-029 (member opak).
Risiko dimitigasi: manipulasi harga dari klien (cart hanya `product_id`+`qty`, total dari quote server), checkout ganda (Idempotency-Key deterministik di server), kebocoran kode ambil (tidak di URL/log/storage/render server, `no-store`), R-T14.

| Path | Isi |
|---|---|
| `app/belanja`, `app/belanja/[id]` | Daftar/kategori/cari/halaman (`?channel=&category=&q=&cursor=`); detail + toko ambil + stok per toko + tambah ke keranjang |
| `app/keranjang` (`components/online/CartView.tsx`) | Grup per PT/kanal, quote server (harga & pajak termasuk), ringkasan per PT, bayar. Tamu: `localStorage` tanpa harga, digabung setelah login |
| `app/pesanan`, `app/pesanan/[id]` (`OrderView`, `PickupCodePanel`, `QrCode`) | Daftar; status (polling 3 dtk/30 dtk), QR lokal (`qrcode-generator`), hitung mundur dari `expires_at` server (batas bayar hingga 60 menit, §11.1), batal sebelum bayar, kode ambil on-demand |
| `app/api/catalog/**` | Proxy publik tanpa Bearer. Cache `public, max-age=60`; stok `no-store`; gambar stream `image/jpeg` ≤ 5 MB, `immutable` |
| `app/api/cart/**`, `app/api/orders/**` | Proxy member: cookie -> Bearer, `isSameOrigin` untuk tulis, `isNotCrossSite` untuk baca, body ≤ 16 KB, `no-store`, error K-ON5 -> pesan Indonesia |
| `lib/catalog.ts`, `lib/cart.ts`, `lib/orders.ts` | Modul murni: tipe kontrak, validator, allowlist respons, peta error |
| `lib/catalog-route.ts`, `lib/orders-route.ts`, `lib/catalog-server.ts`, `lib/orders-server.ts` | Helper server-only (proxy, Idempotency-Key, guard, simulator) |
| `lib/cart-client.ts` | Helper browser (fetch same-origin, keranjang tamu) |

G19 (§11.2, §11.3): kolom kode promo di ringkasan keranjang (maks 3; dikirim hanya lewat body `POST /api/cart/quote`, disimpan di memori komponen, tidak di URL/storage/log), baris reward `reward: true` tampil sebagai "Diskon promo", satu pesan generik "Kode promo tidak dapat digunakan"; `Brand.brandCode` (non-rahasia) -> katalog memanggil `?brand=` di domain brand (UI saja).

Catatan: `next.config.mjs` menimpa `Cache-Control` Route Handler, jadi aturan `no-store` default mengecualikan `/api/catalog/**` (diverifikasi di runtime standalone, laporan G17). Kill switch BFF (`ONLINE_ENABLED=false`) -> 503 `online_unavailable` -> pesan ramah.

## Restoran: pesan dari meja, modifier F&B, poin di checkout, info poin hangus (G22)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §14.A.3 (`/v1/fnb`), §14.B (atribut F&B online), §14.C.2 (poin di checkout), §14.D.6 (info hangus), §14.7 (L-70..L-74). ADR: ADR-032 (QR order), ADR-020.
Risiko dimitigasi: kebocoran token sesi meja (R-S03/SEC-73: token hanya di fragment → memori → header, tidak di URL/storage/log/analytics), pesanan ganda saat jaringan putus (Idempotency-Key per percobaan), PII di catatan (validasi ≤ 140, tanpa nomor HP/email), manipulasi harga (klien hanya mengirim id, qty, atribut, catatan, jumlah poin).

| Path | Isi |
|---|---|
| `app/meja` (`components/fnb/TableOrder.tsx`, `MenuItem`, `AttributePicker`, `PointsClaim`) | QR `/meja#t=<token>`: token → memori (`lib/table-token.ts`), fragment dihapus (`history.replaceState`); menu per kategori, modifier, catatan, keranjang meja, kirim, daftar kiriman (banyak per sesi) dengan polling, klaim poin opsional (login tertanam). Tamu tanpa login boleh memesan |
| `app/api/fnb/**` | Proxy ke BFF `/v1/fnb/*`: header `X-Table-Session` wajib (bentuk diperiksa), Bearer opsional dari cookie, `Idempotency-Key` dari klien, body di-allowlist, error → pesan Indonesia, `no-store` |
| `lib/fnb.ts`, `lib/attributes.ts`, `lib/points.ts` | Modul murni: tipe/parser, keranjang meja, catatan, idempotensi, atribut (modifier), alokasi poin, info hangus |
| `lib/fnb-route.ts`, `lib/fnb-client.ts`, `lib/table-token.ts` | Guard/peta error server; helper browser (header token dari memori, `Idempotency-Key` di `sessionStorage`); penyimpan token di memori |

Modifier online: keranjang bergaris `{ product_id, qty, attribute_value_ids? }` (identitas = produk + id terurut); `AttributePicker` dipakai di detail produk kanal F&B; harga katalog F&B ditampilkan **sebelum pajak** + label pajak (B-5), rincian di keranjang. Poin: `CartView` menampilkan saldo & nilai, isian poin per PT (min/maks dari `quote.points`), porsi tunai ≥ Rp1; `POST /api/orders` meneruskan `points_redeem`. `app/akun`: poin ditahan + "Poin akan hangus" (hanya bila BFF mengisi `points_expiry.expires_on`).

Catatan penting: token meja **tidak** disimpan di storage (keputusan lead G22, meralat §14.A.2) → memuat ulang halaman berarti pindai ulang QR. Teks "poin akan hangus" adalah DRAF sampai Legal menyetujui syarat program (L-73).

## Landing page brand (scroll story foto)

Desain & aturan: `docs/design/landing-pages.md`. Brand dengan entri di `lib/landing-config.ts` (saat ini **Paris Baguette**) memakai landing animatif di `/` sebelum masuk `/belanja`; brand lain tetap memakai beranda lama.

| Path | Isi |
|---|---|
| `lib/landing-config.ts` | Konfigurasi per brand (bab → kategori katalog, produk unggulan, kata penekanan judul, copy DRAF), `pickPhotos`, `categoryHref`, `chapterStats` |
| `components/landing/*` | `Landing` (server), `PhotoStory` + `Rail` (klien, framer-motion) |

Foto produk, etalase, gerai, menit siap, dan metode bayar diambil dari katalog BFF (foto lewat proxy same-origin `/api/catalog/images/:sha`); katalog mati → landing tetap tampil tanpa foto/etalase. Warna PB = warna resmi situs (navy `#002663`, putih, `#aebcd3`, kuning `#fcb134`) dengan `scheme: 'dark'` → seluruh halaman PB ikut tema gelap via `html[data-scheme='dark']`. `prefers-reduced-motion` → gerakan mati; tanpa gerakan otomatis (semua dipicu scroll/pointer).

## Skrip

`npm test` (Vitest: `tests/*.test.ts`), `npm run lint` (ESLint 9 flat, `eslint.config.mjs`), `npm run typecheck`.

## Teks menunggu persetujuan Legal (G18)

Teks pelanggan berikut adalah **DRAF**. Tidak boleh dianggap final sebelum disetujui Legal/DPO; ubah hanya di satu tempat:
- Konfirmasi usia: `AGE_CHECKBOX_LABEL`, `AGE_REQUIRED_MESSAGE` (`lib/auth-age.ts`) dan teks bantuan di `app/masuk/LoginForm.tsx`.
- Poin akan hangus: `expiryNotice()` (`lib/points.ts`) dan blok "Poin akan hangus" di `app/akun/page.tsx` (G22, menunggu syarat program + Legal, L-73).
- Hapus akun dengan pesanan online terbuka: `openOrdersExplanation()` (`lib/dsar.ts`), panel "Masih ada pesanan online yang belum selesai" dan catatan batas penghapusan di `app/akun/data-saya/DataSaya.tsx`, label "Paling lambat dihapus" di `app/akun/penghapusan/[id]/DeletionStatus.tsx`.

## Belum

Uji browser/E2E belanja online terhadap BFF nyata, refresh token / revoke saat logout, SSO lintas subdomain brand, CSP nonce, test E2E (Playwright), koreksi profil (`PATCH /v1/me`) & ganti nomor.
