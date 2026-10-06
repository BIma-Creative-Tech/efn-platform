# superapp-web — EFN Super-App & brand sites (Next.js 15 App Router)

**Tujuan:** satu codebase untuk Super-App holding (`app.`) dan situs brand (`mart.`, `kitchen.`), tema per brand lewat CSS custom properties.
**ADR terkait (Proposed):** multi-tenant by domain, unified cart lintas PT (cart dipisah per PT: PPN vs PB1), OTP login via BFF.
**Risiko dimitigasi:** R-T14 (duplikasi komponen per brand), kebocoran token (cookie HttpOnly, tidak ada token di JS), PDP (lokasi opt-in per laporan, EXIF dibuang di client).

## Struktur

| Path | Isi |
|---|---|
| `middleware.ts` | Host -> brand, set request header `x-efn-brand` (dev: `?brand=efn-mart`) |
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
| `lib/tax.ts`, `lib/mock-catalog.ts`, `components/CartDemo.tsx` | **MOCK** katalog + konfigurasi pajak `{ tax_label, rate, price_include }` per PT + demo cart (tanpa checkout). Tidak ada tarif di UI |

## Env

| Nama | Default | Keterangan |
|---|---|---|
| `BFF_INTERNAL_URL` | `https://api-gateway:3000` | Wajib `https://`. Fallback ke `API_INTERNAL_URL`. Divalidasi saat boot (`instrumentation.ts`) |
| `NODE_EXTRA_CA_CERTS` | (kosong) | Path CA internal, mis. `/run/secrets/efn_ca_crt`. Wajib di production; file harus PEM terbaca |
| `EFN_ALLOW_PLAINTEXT` | (kosong) | `true` mengizinkan `http://` ke BFF — **ditolak** bila `NODE_ENV`/`APP_ENV=production` |
| `HOSTNAME` / `PORT` | `0.0.0.0` / `3001` | Alamat listen. Di balik sidecar TLS ghostunnel: `HOSTNAME=127.0.0.1` |
| `COOKIE_INSECURE` | (kosong) | `true` hanya untuk dev tanpa HTTPS; diabaikan saat `NODE_ENV=production` |
| `DSAR_DELETE_GRACE_DAYS` | `7` | Hanya untuk teks (perkiraan masa tenggang); samakan dengan nilai BFF. Tanggal pasti dari `scheduled_at` BFF |

## Skrip

`npm test` (Vitest: `tests/*.test.ts`), `npm run lint` (ESLint 9 flat, `eslint.config.mjs`), `npm run typecheck`.

## Belum

Checkout/payment, katalog & konfigurasi pajak dari BFF, refresh token / revoke saat logout, SSO lintas subdomain brand, CSP nonce, test E2E (Playwright), koreksi profil (`PATCH /v1/me`) & ganti nomor.
