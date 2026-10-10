# superapp-web — EFN Super-App & brand sites (Next.js 16 App Router)

**Tujuan:** satu codebase untuk Super-App holding (`app.`) dan situs brand (`mart.`, `kitchen.`), tema per brand lewat CSS custom properties.
**ADR terkait (Proposed):** multi-tenant by domain, unified cart lintas PT (cart dipisah per PT: PPN vs PB1), OTP login via BFF, ADR-034/019 (belanja online).
**Risiko dimitigasi:** R-T14 (duplikasi komponen per brand), kebocoran token (cookie HttpOnly, tidak ada token di JS), PDP (lokasi opt-in per laporan, EXIF dibuang di client).

## Struktur

| Path | Isi |
|---|---|
| `proxy.ts` | (Next 16, dulu `middleware.ts`) Host -> brand, set request header `x-efn-brand` (dev: `?brand=efn-mart`) |
| `lib/brands.ts` | Konfigurasi brand & company (RTL/FNB, tarif pajak placeholder) |
| `lib/bff.ts` | Klien BFF server-side (`BFF_INTERNAL_URL`), tipe kontrak docs/04 |
| `lib/bff-config.ts` | Guard transport BFF (https wajib, CA internal) — murni, diuji unit |
| `lib/safe-next.ts` | Validator `?next=` anti open-redirect (SEC-13) |
| `lib/session.ts` | Cookie `efn_session` (HttpOnly, Secure, SameSite=Lax) |
| `app/api/auth/otp/{request,verify}`, `app/api/auth/logout` | Route Handler login/logout |
| `app/api/market-scout/reports` | Proxy streaming multipart ke BFF |
| `app/masuk`, `app/keluar`, `app/akun`, `app/pantau-harga`, `app/pantau-harga/[id]`, `app/privasi` | Halaman |
| `app/api/auth/challenge`, `components/AltchaWidget.tsx`, `lib/altcha.ts` | Captcha ALTCHA self-hosted (docs/04 §8.1) |
| `app/akun/privasi`, `app/api/me/consents`, `lib/consent.ts` | Consent lokasi & marketing (docs/04 §8.4) |
| `app/akun/data-saya`, `app/api/me/{data,reauth,requests,delete/cancel}`, `app/api/me` (DELETE), `lib/dsar.ts` | DSAR: unduh data, hapus akun (re-OTP step-up), batal, riwayat |
| `app/akun/penghapusan/[id]`, `app/api/dsar/requests/[id]`, `lib/dsar-receipt.ts` | R6 (docs/04 §8.13): status hapus akun tanpa sesi via cookie HttpOnly `efn_dsar_receipt` (Path `/api/dsar`, SameSite=Strict, ≤ 30 hari) |
| `app/belanja`, `app/belanja/[id]`, `app/keranjang`, `app/pesanan`, `app/pesanan/[id]`, `components/online/**` | Belanja online Fase 3 (lihat bagian di bawah) |
| `app/api/catalog/**`, `app/api/cart/**`, `app/api/orders/**`, `lib/{catalog,cart,orders}*.ts` | Route Handler & modul belanja online |

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
| `app/pesanan`, `app/pesanan/[id]` (`OrderView`, `PickupCodePanel`, `QrCode`) | Daftar; status (polling 3 dtk/30 dtk), QR lokal (`qrcode-generator`), hitung mundur 15 menit, batal sebelum bayar, kode ambil on-demand |
| `app/api/catalog/**` | Proxy publik tanpa Bearer. Cache `public, max-age=60`; stok `no-store`; gambar stream `image/jpeg` ≤ 5 MB, `immutable` |
| `app/api/cart/**`, `app/api/orders/**` | Proxy member: cookie -> Bearer, `isSameOrigin` untuk tulis, `isNotCrossSite` untuk baca, body ≤ 16 KB, `no-store`, error K-ON5 -> pesan Indonesia |
| `lib/catalog.ts`, `lib/cart.ts`, `lib/orders.ts` | Modul murni: tipe kontrak, validator, allowlist respons, peta error |
| `lib/catalog-route.ts`, `lib/orders-route.ts`, `lib/catalog-server.ts`, `lib/orders-server.ts` | Helper server-only (proxy, Idempotency-Key, guard, simulator) |
| `lib/cart-client.ts` | Helper browser (fetch same-origin, keranjang tamu) |

Catatan: `next.config.mjs` menimpa `Cache-Control` Route Handler, jadi aturan `no-store` default mengecualikan `/api/catalog/**` (diverifikasi di runtime standalone, laporan G17). Kill switch BFF (`ONLINE_ENABLED=false`) -> 503 `online_unavailable` -> pesan ramah.

## Skrip

`npm test` (Vitest: `tests/*.test.ts`), `npm run lint` (ESLint 9 flat, `eslint.config.mjs`), `npm run typecheck`.

## Belum

Uji browser/E2E belanja online terhadap BFF nyata, refresh token / revoke saat logout, SSO lintas subdomain brand, CSP nonce, test E2E (Playwright), koreksi profil (`PATCH /v1/me`) & ganti nomor.
