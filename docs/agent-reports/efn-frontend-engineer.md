# Laporan efn-frontend-engineer — Fondasi Super-App (Fase 0)

Tanggal: 2026-10-05. Lingkup: `services/superapp-web/` saja. Tidak ada Node lokal: **tidak ada yang di-build/di-run**; validasi menunggu CI (`next build`, `next lint`).

## 1. Halaman & komponen

| Path | Jenis | Isi |
|---|---|---|
| `middleware.ts` | Edge | Host -> brand (`x-efn-brand`, selalu menimpa header dari klien). Dev: `?brand=<id>` + cookie `efn_dev_brand` |
| `lib/brands.ts` | data | `efn` (default; app.efn.local, efn.local, localhost; RTL+FNB), `efn-mart` (mart.*; RTL), `efn-kitchen` (kitchen.*; FNB). Cocok host eksplisit lalu prefix subdomain (domain produksi apa pun) |
| `app/layout.tsx` | RSC | Tema via CSS custom properties di `<html style>`, metadata per brand, skip-link, header/footer |
| `app/page.tsx` + `components/CartDemo.tsx` | RSC + client | Katalog **MOCK** per brand + demo keranjang dikelompokkan per PT, pajak per kelompok (PPN termasuk vs PB1 ditambahkan), banner "N pesanan terpisah, satu pembayaran", tombol checkout nonaktif |
| `app/masuk/page.tsx` + `LoginForm.tsx` | RSC + client | HP -> OTP, normalisasi `08xx`/`+628xx`/`8xx` -> `628xx`, cooldown kirim ulang 60 dtk, `autocomplete=one-time-code`, `?next=` aman (anti open-redirect) |
| `app/keluar/page.tsx` | RSC | Konfirmasi; form POST ke `/api/auth/logout` (bukan GET agar prefetch tidak logout) |
| `app/akun/page.tsx` | RSC | Baca cookie, `GET /v1/me`, tampilkan poin & HP ter-mask; 401 -> `/masuk?expired=1` |
| `app/pantau-harga/page.tsx` + `PriceReportForm.tsx` | RSC + client | Wajib login. Foto (kompres canvas JPEG ≤2048 px, q 0.85→0.6, EXIF terbuang), nama produk, harga, toko opsional, lokasi opt-in eksplisit per laporan (default mati, dibulatkan 4 desimal ≈11 m). Setelah 202 tampil "sedang divalidasi, hasil via notifikasi" + link status |
| `app/pantau-harga/[id]/page.tsx` + `components/AutoRefresh.tsx` | RSC + client | Status laporan; `router.refresh()` tiap 15 dtk selama queued/processing (maks 20x, berhenti bila tab tersembunyi). 403/404 -> notFound (tidak bocorkan keberadaan) |
| `app/privasi/page.tsx` | RSC | Placeholder kebijakan PDP bertanda **DRAFT** (data, tujuan, retensi usulan, hak hapus, kontak DPO kosong) |
| `app/not-found.tsx` | RSC | 404 |
| `app/api/auth/otp/request/route.ts` | Route Handler | Validasi + normalisasi HP, panggil BFF, respons generik, 429 diteruskan |
| `app/api/auth/otp/verify/route.ts` | Route Handler | Set cookie `efn_session` (HttpOnly, Secure, SameSite=Lax, Max-Age=`expires_in`); token tidak dikembalikan ke browser |
| `app/api/auth/logout/route.ts` | Route Handler | Hapus cookie, 303 ke `/` (Location relatif agar aman di belakang Traefik) |
| `app/api/market-scout/reports/route.ts` | Route Handler | Proxy **streaming** multipart (tidak di-buffer, `duplex:'half'`), batas 8 MB + 256 KB dipaksa saat streaming, Bearer dari cookie, 401 dari BFF -> hapus cookie |
| `lib/bff.ts` | server-only | `bffFetch` -> union `BffResult` (tidak melempar), timeout 10 s (upload 30 s), `no-store`, teruskan `x-request-id` & `X-Forwarded-For`, tidak log body/HP/token. Tipe kontrak §3.2/§3.3 |
| `lib/session.ts`, `lib/request-guard.ts`, `lib/phone.ts`, `lib/image-compress.ts`, `lib/format.ts`, `lib/mock-catalog.ts` | util | Cookie, cek Origin == Host untuk semua POST (CSRF lapis kedua selain SameSite=Lax), normalisasi/mask HP, kompres gambar, format Rupiah, katalog mock + hitung pajak per PT |
| `next.config.mjs` | config | CSP (`default-src 'self'`, `script-src 'self' 'unsafe-inline'` [+`'unsafe-eval'` dev], `connect-src 'self'`, `img-src 'self' data: blob:`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`, `upgrade-insecure-requests` prod), HSTS (prod), nosniff, Referrer-Policy, X-Frame-Options DENY, COOP, Permissions-Policy `geolocation=(self), camera=(self), microphone=(), payment=(), usb=()`; `poweredByHeader:false`; `output:'standalone'` (Dockerfile menyalin `.next/standalone`) |
| `package.json` | deps | + `server-only@^0.0.1` |
| `README.md` | doc | Tujuan, ADR, env, yang belum |

Next 15: semua `params`, `searchParams`, `cookies()`, `headers()` di-`await`.

## 2. Endpoint BFF yang diasumsikan (docs/04_SERVICE_CONTRACTS.md)

| Endpoint | Dipanggil dari | Kontrak diasumsikan |
|---|---|---|
| `POST /v1/auth/otp/request` | `/api/auth/otp/request` | Body `{ phone: "628..." }` -> `202 { challenge_id, expires_in }`; `429` rate limit. Selain itu dipetakan ke 502 generik |
| `POST /v1/auth/otp/verify` | `/api/auth/otp/verify` | `{ challenge_id, code }` -> `200 { access_token, token_type:"Bearer", expires_in }`; `400 { ok:false, reason: invalid|expired|locked, attempts_left }` (format §3.1; bila BFF memakai `{error:{...}}` untuk 400, frontend jatuh ke reason `invalid`) |
| `GET /v1/me` | `/akun` (RSC) | Bearer -> `{ member_ref_masked, points_balance }`; `401` bila token kedaluwarsa |
| `POST /v1/market-scout/reports` | `/api/market-scout/reports` | Bearer, multipart: `photo` (selalu JPEG dari client), `product_name`, `price` (integer Rupiah string), `competitor_name?`, `lat?`, `lng?` -> `202 { report_id, status:"queued" }`; `400/413/415/422/429` diteruskan dengan pesan BFF; lainnya 502 generik |
| `GET /v1/market-scout/reports/:id` | `/pantau-harga/[id]` (RSC) | Bearer, hanya pemilik -> `{ report_id, status, reason, points_awarded }`; 403/404 diperlakukan sama |

Asumsi tambahan untuk tim BFF:
1. `report_id` berupa UUID (halaman status menolak id non-UUID dengan 404).
2. BFF menerima multipart dengan `Transfer-Encoding: chunked` (tanpa `Content-Length`) karena body di-stream.
3. BFF mempercayai `X-Forwarded-For` hanya dari jaringan internal, untuk rate limit OTP per IP (tanpa ini semua pelanggan terlihat sebagai IP container Next.js).
4. Pesan `error.message` BFF sudah generik & Bahasa Indonesia (diteruskan ke UI untuk 400/413/415/422/429).

## 3. Kebutuhan ke agent lain (bukan wewenang saya)

- **DevSecOps/infra (`infra/compose/docker-compose.yml`):**
  - Env superapp-web: ~~`BFF_INTERNAL_URL: http://api-gateway:3000`~~ — **diganti Gelombang 3**: `https://api-gateway:3000` + `NODE_EXTRA_CA_CERTS` (lihat §Gelombang 3).
  - Router Traefik `web` hanya `app.` dan apex; tambahkan `` Host(`mart.${TRAEFIK_DOMAIN}`) || Host(`kitchen.${TRAEFIK_DOMAIN}`) `` + sertifikat.
  - `NEXT_PUBLIC_API_URL` tidak lagi dipakai (browser hanya ke origin sendiri); boleh dihapus dari build arg.
  - `docker-compose.dev.yml` hanya me-mount `app/`; perubahan di `lib/`, `components/`, `middleware.ts` butuh rebuild image dev — sebaiknya mount juga ketiganya.
  - Dev tanpa HTTPS di `http://localhost:3001`: set `COOKIE_INSECURE=true` hanya di compose dev (Chrome menerima Secure di localhost, browser lain belum tentu).
  - Middleware Traefik `secure-headers@file` mungkin menduplikasi header; pastikan nilainya tidak bertentangan (terutama CSP/Permissions-Policy — Traefik sebaiknya tidak memasang CSP sendiri untuk host web).
- **BFF:** endpoint logout/revoke token tidak ada di kontrak — logout hanya menghapus cookie; JWT tetap valid ≤1 jam. Tidak ada refresh token: sesi habis tiap 1 jam (pengguna login ulang).
- **Arsitek/legal:** teks `/privasi` DRAFT; retensi foto (usulan 12 bulan), log (90 hari), kontak DPO, daftar pemroses data. Tarif pajak di `lib/brands.ts` (PPN efektif 11% inklusif, PB1 10% eksklusif) adalah placeholder; konfirmasi fiscal position Odoo.

## 4. Belum diverifikasi / risiko yang diketahui

- Tidak ada build/lint/typecheck lokal. Titik yang paling mungkin bermasalah di CI: tipe `duplex` pada `RequestInit` (sudah di-cast lokal), `pipeThrough` stream Web pada body request Next, dan `next lint` tanpa file konfigurasi ESLint 9 (kondisi sebelum pekerjaan ini).
- Streaming upload melalui undici ke Fastify multipart belum diuji ujung-ke-ujung.
- CSP masih `'unsafe-inline'` untuk script (batasan Next tanpa nonce). Rekomendasi: CSP nonce via middleware (halaman sudah dinamis karena multi-tenant, jadi biaya kecil).
- Cookie host-only per domain brand: login di `mart.` tidak berlaku di `kitchen.`/`app.` — SSO lintas subdomain (cookie `Domain=`) perlu keputusan (dampak keamanan: cookie dikirim ke semua subdomain termasuk `erp.`/`ops.`).
- Pemeriksaan Origin==Host mengandalkan Traefik meneruskan Host asli (default `passHostHeader: true`).
- Orientasi EXIF saat kompres mengandalkan perilaku browser modern (`image-orientation: from-image`); browser lama bisa menghasilkan foto terputar (BFF tetap re-encode tetapi tidak memperbaiki rotasi yang sudah dibakar).
- Tidak ada test. Rekomendasi QA: Vitest untuk `lib/phone.ts`, `lib/brands.ts` (`resolveBrandIdFromHost`), `lib/mock-catalog.ts` (`groupCartByCompany`), `safeNextPath`; Playwright untuk alur OTP dengan BFF mock.
- Aksesibilitas belum diaudit dengan alat (axe/Lighthouse); kontras warna tema dipilih manual (teks putih di atas primary gelap).

---

## Gelombang 3 — Enkripsi in transit, SEC-13, test & lint (2026-10-05)

Instruksi user: **enkripsi di setiap transaksi data** (docs/04 §7.1). Tetap tanpa Node lokal: tidak ada yang dijalankan; semua menunggu CI.

### Perubahan
| File | Isi |
|---|---|
| `lib/bff-config.ts` (baru, murni) | `assertBffTransport(env, readFile?)`: default `https://api-gateway:3000`; `http://` ditolak kecuali `EFN_ALLOW_PLAINTEXT=true` (persis, case-sensitive) **dan** bukan `NODE_ENV`/`APP_ENV=production`; tolak skema lain, kredensial di URL, query/fragment; tolak `NODE_TLS_REJECT_UNAUTHORIZED=0` di production; bila https: production wajib `NODE_EXTRA_CA_CERTS`, dan file yang ditunjuk harus terbaca & berisi PEM (di semua env) |
| `lib/bff.ts` | Memakai guard di atas (cache sekali). Konfigurasi tidak aman -> `503 bff_misconfigured` (tetap tidak melempar). Log error jaringan menyertakan `err.cause.code` (diagnosa TLS, tanpa PII) |
| `instrumentation.ts` + `lib/instrumentation-node.ts` (baru) | Validasi transport saat boot server; gagal -> `process.exit(1)` (container gagal start, bukan jalan diam-diam). Import dinamis di balik `NEXT_RUNTIME === 'nodejs'` |
| `lib/safe-next.ts` (baru) | **SEC-13**: NFKC -> harus `/` tunggal, bukan `//`; tolak backslash, kontrol C0/C1/DEL, whitespace Unicode pada nilai mentah **dan** setelah satu percent-decode (`%09`, `%0a`, `%5c`, `%2f%2f`); percent-encoding rusak ditolak; `new URL(next,'https://x.invalid').origin` harus sama; hasil kanonik `pathname+search+hash` dicek ulang bukan `//` (menangkap `/.//evil`); panjang ≤1024 |
| `lib/session.ts` | `safeNextPath` di-re-export dari `safe-next`; cookie sesi **selalu `Secure` di production** (`COOKIE_INSECURE` diabaikan) |
| `app/masuk/LoginForm.tsx` | Validasi ulang `safeNextPath` di client sebelum `router.replace` (lapis kedua) |
| `Dockerfile` | `HOSTNAME`/`PORT` configurable (default `0.0.0.0`/`3001`; sidecar set `127.0.0.1`); stage dev meneruskan `-H "$HOSTNAME" -p "$PORT"` ke `next dev`; healthcheck exec-form ke `http://127.0.0.1:$PORT/api/health` (bukan `localhost`, yang bisa resolve ke `::1`); directive `# syntax=` dipindah ke baris 1 (sebelumnya tidak berlaku) |
| `package.json` | `test: vitest run`, `lint: eslint .`, `typecheck: tsc --noEmit`; devDeps `vitest@^3.0.0`, `@eslint/eslintrc@^3.2.0` |
| `eslint.config.mjs` (baru) | ESLint 9 flat: FlatCompat `next/core-web-vitals` + `next/typescript` (pola create-next-app 15). Juga dipakai `next build` |
| `vitest.config.mts` (baru) | env node, alias `@/`, `tests/**/*.test.ts` |
| `tests/{safe-next,phone,brands,bff-config}.test.ts` (baru) | ±90 kasus: payload open-redirect (TAB/`%09`/backslash/full-width/`/.//`), normalisasi & mask HP, host->brand (port, trailing dot, IPv6, `__proto__`), guard plaintext & CA |
| `README.md` | Env baru & skrip |

### Keputusan: `NODE_EXTRA_CA_CERTS`, bukan undici `Agent({ connect: { ca } })`
`fetch` global Node 22 (undici bawaan) memakai `tls.connect` tanpa opsi `ca` -> root store default + isi `NODE_EXTRA_CA_CERTS` (dibaca sekali saat proses start), verifikasi hostname tetap aktif. `dispatcher: new Agent()` butuh paket `undici` npm yang versinya harus selaras dengan undici bawaan Node dan bukan bagian API `fetch` yang di-patch Next.js — lebih rapuh. Kelemahan env (Node hanya **warning** bila file tidak terbaca) ditutup oleh pemeriksaan file sendiri di boot (fail-closed).

### Asumsi / kebutuhan ke devsecops (compose)
1. `BFF_INTERNAL_URL=https://api-gateway:3000`, `NODE_EXTRA_CA_CERTS=/run/secrets/efn_ca_crt` (secret `efn_ca_crt` di-mount & terbaca oleh user non-root `app`), `HOSTNAME=127.0.0.1` saat sidecar ghostunnel aktif. Sertifikat BFF harus punya SAN `DNS:api-gateway`.
2. Sidecar ghostunnel `--target 127.0.0.1:3001`; Traefik -> sidecar via https + `serversTransport` CA internal. Healthcheck container app tetap plaintext loopback (namespace sama) — benar karena tidak keluar namespace.
3. Compose dev tanpa PKI: wajib eksplisit `EFN_ALLOW_PLAINTEXT=true` + `BFF_INTERNAL_URL=http://...`, kalau tidak container exit 1. Stage dev tidak lagi memakai `npm run dev` (CMD langsung `next dev -H/-p`).
4. Image runtime (production) kini **menolak** `COOKIE_INSECURE`: menjalankan image prod via `http://localhost` tanpa TLS -> cookie sesi tidak terkirim browser non-Chrome. Pakai target `dev` untuk lokal.
5. Belum ada `package-lock.json`; bila CI memakai `npm ci`, lockfile perlu dibuat di CI/dev pertama kali.

### Belum diverifikasi / risiko
- Tidak ada `npm install`/`vitest`/`eslint`/`next build` lokal. Risiko CI: aturan `next/typescript` yang baru aktif bisa menandai kode lama (mis. `no-unused-vars` sebagai warning; error akan menggagalkan `next build`). Grep cepat tidak menemukan `any`/`require`/`@ts-ignore`.
- Perilaku `process.exit(1)` di `register()` pada `server.js` standalone diasumsikan menghentikan proses (perilaku Node normal); belum diuji.
- SEC-17 (pembulatan koordinat) tetap tanggung jawab BFF: Route Handler upload sengaja mem-stream multipart tanpa parsing, jadi tidak membulatkan di Next.js; browser tetap membulatkan 4 desimal.
- SEC-03: Next.js masih meneruskan `X-Forwarded-For` apa adanya; penentuan IP klien ada di Traefik/BFF (docs/04 §7.5).

---

## Gelombang 5 — ALTCHA, consent, DSAR, pajak configurable (2026-10-06)

Acuan: docs/04 **v3 §8.1, §8.2, §8.4**; `docs/compliance/PDP_CONSENT.md`, `PDP_DSAR_DESIGN.md`. Bila usulan compliance berbeda dengan §8.4 (mis. purpose `location_price_hunter` vs `location`, field `text_version` vs `version`), **§8.4 diikuti**. Tetap tanpa Node lokal: tidak ada yang dijalankan.

### Perubahan
| Area | File | Isi |
|---|---|---|
| ALTCHA | `components/AltchaWidget.tsx` (baru) | Paket npm `altcha` (MIT) di-`import()` dinamis dari bundle sendiri (chunk `/_next/static`, **tanpa CDN**) -> CSP `script-src 'self'` tidak berubah. `<altcha-widget>` dibuat imperatif (tidak SSR, tanpa tipe JSX custom element), `challengeurl=/api/auth/challenge`, `auto=onfocus`, teks Bahasa Indonesia, footer disembunyikan. Payload naik lewat event `statechange` |
| | `app/api/auth/challenge/route.ts` (baru) | Proxy `GET` -> BFF `/v1/auth/challenge`; hanya field `{algorithm, challenge, salt, signature, maxnumber?}` diteruskan; `no-store`; tolak `Sec-Fetch-Site: cross-site` |
| | `app/api/auth/otp/request/route.ts` | Wajib `altcha` (cek bentuk) -> `400 captcha_required` lokal; diteruskan `{ phone, altcha }`; `400 captcha_required` BFF dipetakan dengan kode sama |
| | `app/masuk/LoginForm.tsx` | Widget di form HP; kirim ulang OTP memakai widget kedua **di luar** form verifikasi (widget memasang listener submit pada form induk); challenge sekali pakai -> remount (`key`) setelah setiap permintaan; `captcha_required` -> pesan + reset |
| | `lib/altcha.ts`, `types/altcha.d.ts` (baru) | Validasi bentuk payload base64/JSON & challenge (bukan verifikasi kripto — itu BFF); deklarasi modul shorthand |
| Consent | `lib/consent.ts` (baru) | Teks persetujuan berversi milik frontend (`location@2026-10-06`, `marketing@2026-10-06`, DRAFT), `parseConsents` (default tidak aktif), `validateConsentUpdate` (pemberian dengan versi usang -> `409 stale_text`; **penarikan selalu diterima**, dicatat versi terbaru) |
| | `app/api/me/consents/route.ts` (baru) | `GET` & `PUT { purpose, granted, version }` -> BFF `PUT /v1/me/consents/:purpose { granted, version }` |
| | `app/akun/privasi/{page,ConsentSettings}.tsx` (baru) | Toggle per tujuan, tanpa optimistic update (layar = bukti tersimpan), tanggal & versi, ajakan setujui ulang bila versi teks berubah |
| | `app/pantau-harga/{page,PriceReportForm}.tsx` | Lokasi **hanya ditawarkan bila consent `location` aktif** (gagal memuat -> tidak ditawarkan); tetap opt-in per laporan. Tanpa consent: teks "dikirim tanpa lokasi" + tautan ke `/akun/privasi` |
| | `lib/consent-server.ts` (baru) | Muat consent untuk RSC |
| DSAR | `lib/dsar.ts` (baru) | Parser toleran (`/v1/me/requests`, 202 export, 202 hapus), `safeDownloadUrl` (hanya `https:`, tanpa kredensial), label status, `isCancellableDeletion`, `daysUntil`, nama berkas export (tanggal WIB), konfirmasi `HAPUS` |
| | `app/api/me/data/route.ts` | `GET` -> 200 lampiran JSON (`Content-Disposition`) atau 202 `{request_id, download_url?, expires_at?}` |
| | `app/api/me/reauth/route.ts` | Re-OTP step-up dua mode; token step-up di cookie **`efn_stepup` HttpOnly, SameSite=Strict, Path=/api/me, Max-Age ≤ 600** — tidak pernah ke JS |
| | `app/api/me/route.ts` (`DELETE`) | Wajib cookie step-up + body `{confirm:"HAPUS", acknowledge_points:true}` (dicek server); sukses -> hapus cookie step-up **dan** sesi (BFF mencabut sesi, PDP_DSAR §5); step-up kedaluwarsa -> `403 step_up_required` |
| | `app/api/me/delete/cancel/route.ts`, `app/api/me/requests/route.ts` | Batal hapus; riwayat |
| | `app/akun/data-saya/{page,DataSaya}.tsx` (baru) | Unduh data (blob untuk 200, tautan untuk 202), riwayat permintaan (+tautan unduh), hapus akun: penjelasan (masa tenggang, **sisa poin hangus** dari `GET /v1/me`) -> kirim OTP -> verifikasi -> centang pengakuan + ketik `HAPUS` -> hasil (`scheduled_at`, `points_forfeited`, nomor permintaan); bila ada penghapusan tertunda -> tombol **Batalkan penghapusan** |
| | `lib/me-route.ts`, `lib/session.ts`, `lib/request-guard.ts` | 401 BFF -> hapus cookie sesi; helper cookie step-up; `isNotCrossSite` (Fetch Metadata) untuk GET data pribadi |
| Privasi | `app/privasi/page.tsx`, `app/akun/page.tsx` | Retensi default §8.4 (OTP audit 90 h, foto 90 h, identitas laporan dilepas 90 h, laporan 2 th, analitik 5 th, ledger 10 th, consent akun+5 th), bertanda **DRAFT menunggu DPO**; dasar pemrosesan; tautan ke `/akun/privasi` & `/akun/data-saya`; masa tenggang & poin hangus |
| Pajak | `lib/tax.ts` (baru), `lib/mock-catalog.ts`, `lib/brands.ts`, `components/CartDemo.tsx`, `app/page.tsx` | Tarif/label pajak **dihapus dari `brands.ts` & UI**. `MOCK_TAX_CONFIG` berlabel MOCK `{ tax_label, rate, price_include }` per PT, dibaca server lewat `loadTaxConfigs()` (titik tunggal yang kelak diganti BFF) dan dikirim sebagai prop. Inklusif: "Harga sebelum pajak" + pajak (total tidak bertambah); eksklusif: "Subtotal" + "+pajak" -> total. Konfigurasi tidak valid -> "pajak dihitung saat checkout" |
| Test | `tests/{tax,altcha,consent,dsar}.test.ts` (baru) | ±100 kasus: include/exclude & pembulatan (base+tax = harga inklusif), format tarif (`1,25%`), validasi config; payload/challenge ALTCHA; consent stale/withdraw; URL unduh berbahaya, masa tenggang, konfirmasi |
| Lain | `package.json` (+`altcha@^1.0.0`), `app/globals.css` (`.btn-danger`), `README.md` | — |

### Endpoint BFF yang diasumsikan
| Endpoint | Dari | Kontrak | Status kontrak |
|---|---|---|---|
| `GET /v1/auth/challenge` | `/api/auth/challenge` | 200 `{ algorithm: "SHA-256", challenge, salt, signature, maxnumber? }` (format standar ALTCHA); 429 | §8.1 (bentuk = standar ALTCHA) |
| `POST /v1/auth/otp/request` | `/api/auth/otp/request` | `{ phone, altcha }` -> 202; `400 {error:{code:"captcha_required"}}`; `400 invalid_phone`; 429 | §3.2 + §8.1 |
| `GET /v1/me/consents` | RSC `/akun/privasi`, `/pantau-harga`; `/api/me/consents` | `[{ purpose: "location"|"marketing", granted, version, updated_at }]` (juga toleran `{items}`) | §8.4 |
| `PUT /v1/me/consents/:purpose` | `/api/me/consents` | `{ granted, version }` -> 200 | §8.4 |
| `GET /v1/me/data` | `/api/me/data` | 200 JSON export **atau** 202 `{ request_id, download_url?, expires_at? }` | §8.4; **bentuk 202 = asumsi** |
| `POST /v1/me/reauth` | `/api/me/reauth` | **Asumsi:** `{}` -> 202 `{ challenge_id, expires_in }` (OTP ke nomor akun); `{ challenge_id, code }` -> 200 `{ step_up_token, expires_in }` \| 400 format §3.1 | §8.4 hanya "token step-up purpose: dsar, 10 menit" |
| `DELETE /v1/me` | `/api/me` | **Asumsi:** header `X-Step-Up-Token: <token>`, tanpa body -> 202 `{ request_id, scheduled_at, points_forfeited }`; step-up invalid -> 403 atau `code: step_up_required|step_up_invalid` | §8.4 + PDP_DSAR §3 |
| `POST /v1/me/delete/cancel` | `/api/me/delete/cancel` | Bearer, `{}` -> 200; 404/409 bila tidak ada yang bisa dibatalkan | §8.4 |
| `GET /v1/me/requests` | RSC `/akun/data-saya`; `/api/me/requests` | Array (atau `{items}`) `{ id, type, status, requested_at, scheduled_at, completed_at, download_url? }` | §8.4 (kolom tabel); **`download_url` = asumsi** |
| `GET /v1/me` | RSC `/akun/data-saya` | `{ member_ref_masked, points_balance }` (poin yang akan hangus) | §3.2/§8.2 |

Asumsi lain untuk BFF/lead: (1) login OTP tetap diizinkan selama masa tenggang agar pengguna dapat membatalkan (frontend menghapus cookie sesi setelah `DELETE`); (2) `/v1/me/reauth` tidak memerlukan ALTCHA (Bearer + rate limit); (3) status DSAR yang dapat dibatalkan: `received|verified|scheduled|pending` dengan `scheduled_at` di masa depan; (4) `DSAR_DELETE_GRACE_DAYS` juga di-set di env superapp-web (hanya untuk teks).

### Belum diverifikasi / risiko
- Tidak ada `npm install`/vitest/eslint/`next build`. Paket `altcha` dipin `^1.0.0` (API v1: atribut `challengeurl/auto/strings/hidefooter`, event `statechange {state, payload}`); bila CI memasang v2 dan API berubah, sesuaikan. Worker proof-of-work widget diasumsikan dibuat dari `blob:` (sudah diizinkan `worker-src 'self' blob:`); perlu dicek di browser bahwa tidak ada pelanggaran CSP (konsol) dan bahwa widget tidak memuat aset eksternal.
- Perilaku widget yang menahan submit form induk saat belum terverifikasi: form HP memakai `auto=onfocus` sehingga biasanya sudah terverifikasi saat tombol ditekan; belum diuji.
- Teks persetujuan & kebijakan privasi DRAFT (DPO/Legal). Versi teks belum di-hash (`text_sha256` dari usulan PDP_CONSENT tidak ada di kontrak §8.4).
- Tautan unduh export (presigned MinIO) dibuka langsung oleh browser: host MinIO publik/subdomain dan CORS/Content-Disposition di sisi MinIO perlu keputusan devsecops (frontend hanya menerima `https:`).
- Konfigurasi pajak masih MOCK; endpoint BFF konfigurasi pajak/katalog per company belum ada di docs/04. PB1/PBJT dan PPN (DPP nilai lain) wajib dikonfirmasi tim pajak; pembulatan per kelompok hanya ilustrasi.
- Koreksi profil (`PATCH /v1/me`) dan ganti nomor (`/v1/me/phone-change`) belum dibuat (tidak diminta gelombang ini).
