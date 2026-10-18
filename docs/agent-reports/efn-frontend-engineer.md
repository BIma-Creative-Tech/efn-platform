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

## Gelombang 9 — R6 status penghapusan & SEC-40 step-up sekali pakai (2026-10-06)

Kontrak: docs/04 §8.13 (v3.6) + `efn-bff-engineer.md` G9.4 "frontend".

### File
| File | Perubahan |
|---|---|
| `lib/dsar-receipt.ts` (baru, murni) | Validasi bentuk `receipt_token` (`v1.<exp>.<b64url>`, ≤ 160 char — verifikasi HMAC tetap di BFF), `receiptMaxAge` = min(30 hari, sisa `exp`; kedaluwarsa → tidak disimpan), `isDsarRequestId` (UUID), `parseDeletionStatus` (allowlist `{id,status,requested_at,scheduled_at,completed_at}`, `type` wajib `delete`, `id` wajib sama), label/deskripsi status, `canCancelDeletion` |
| `lib/session.ts` | `setDsarReceiptCookie` / `clearDsarReceiptCookie` / `getDsarReceiptFromRequest`: cookie `efn_dsar_receipt` HttpOnly, Secure (kecuali dev `COOKIE_INSECURE=true`, tidak berlaku di production), SameSite=Strict, Path=`/api/dsar` |
| `app/api/me/route.ts` | `DELETE` sukses: `receipt_token` → cookie (tidak pernah di body respons/log), body = allowlist + `status_available`; cookie `efn_stepup` & `efn_session` dihapus. `403 step_up_invalid` (SEC-40, step-up dipakai ulang) → `403 step_up_required` + pesan "kode hanya berlaku untuk satu kali penghapusan" + hapus cookie step-up → UI kembali ke langkah re-OTP |
| `app/api/dsar/requests/[id]/route.ts` (baru) | `GET` → BFF `GET /v1/dsar/requests/:id` dengan header `X-DSAR-Receipt` dari cookie, tanpa Bearer. Id bukan UUID / cookie tak ada atau rusak / BFF 404/400 → `404 not_found` generik (tanpa memanggil BFF bila bisa ditolak lokal). 429 diteruskan; 5xx/respons rusak → 502 generik. Fetch Metadata lintas situs → 403. `cache-control: no-store` |
| `app/akun/penghapusan/[id]/page.tsx` + `DeletionStatus.tsx` (baru) | Halaman status tanpa sesi (`noindex`). Client memuat `/api/dsar/requests/:id` (cookie Path `/api/dsar` tidak terbaca halaman). Menampilkan status (`scheduled/processing/completed/cancelled`, lainnya "Sedang diperbarui") + tanggal diajukan/jadwal/selesai (Asia/Jakarta). Tombol **Batalkan** hanya bila status `scheduled` DAN server memastikan sesi valid yang **memiliki** request ini dalam masa tenggang (`GET /v1/me/requests` berisi id tsb) — sesi akun lain di perangkat yang sama tidak mendapat tombol. Tanpa sesi + `scheduled` → tautan "Masuk kembali" (`/masuk?next=/akun/penghapusan/<id>`) |
| `app/akun/data-saya/DataSaya.tsx` | Setelah DELETE: tombol "Lihat status penghapusan" bila `status_available` |
| `tests/dsar-receipt.test.ts` (12), `tests/dsar-routes.test.ts` (9, BFF di-mock, `server-only` di-stub) | Atribut cookie, receipt tidak bocor ke body, step-up & sesi dihapus, `step_up_invalid`, 404 generik, header `X-DSAR-Receipt` tanpa Bearer, allowlist respons, lintas situs, 429/5xx |
| `README.md` | Baris tabel modul R6 |

### Hasil uji
Container `node:22-bookworm` (`npm ci && npm run lint && npm test && npm run build`): lint 0 error (1 warning lama `safe-next.ts` unused eslint-disable, tak terkait), **10 file, 219 test lulus**, `next build` sukses (route baru `/akun/penghapusan/[id]`, `/api/dsar/requests/[id]`).

### Asumsi / belum diverifikasi
- Belum diuji E2E terhadap BFF nyata (stack dev tidak menyala); bentuk `receipt_token` diambil dari kode `dsar-receipt.service.ts`.
- Satu cookie receipt per peramban: DELETE berikutnya (setelah batal) menimpa receipt lama; halaman status request lama lalu 404 generik. Bukti hanya ada di peramban yang dipakai saat DELETE (dijelaskan di pesan 404).
- Tidak ada halaman status tanpa id (`/akun/penghapusan`): pengguna yang kehilangan URL harus masuk kembali (selama akun masih ada) atau memakai tautan dari layar konfirmasi. Bila perlu, opsi: simpan `request_id` di cookie yang sama.
- Pembatalan dari halaman status memakai `POST /api/me/delete/cancel` yang ada (tanpa step-up; perilaku BFF tidak berubah gelombang ini).
- Cookie receipt tidak dihapus otomatis saat status `completed`/`cancelled` (berakhir sendiri ≤ 30 hari; tidak memuat PII).

## Gelombang 16 — Upgrade Next.js 16 + altcha 3 (npm audit CI merah) (9 Okt 2026)

**Pemicu:** job CI `Node (superapp-web)` gagal di `npm audit --omit=dev --audit-level=high`: PostCSS ≤ 8.5.22
(high, GHSA-qx2v-qp2m-jg93 / 6g55-p6wh-862q / fxqj-rqcc-2cmp / r28c-9q8g-f849, lewat `next` ≤ 16.3.0-preview) dan
`altcha` 0.8–2.2.4 (moderate, GHSA-mpmc-qchh-r9q8, mode obfuscation — fitur yang tidak kita pakai, tetap di-upgrade).

### Versi
| Paket | Lama | Baru |
|---|---|---|
| `next` | ^15.1.2 | ^16.4.0 (postcss 8.5.23) |
| `eslint-config-next` | ^15.1.2 | ^16.4.0 (flat config native) |
| `altcha` | ^1.0.0 | ^3.3.0 |
| `react` / `react-dom` (lock) | 19.0.x | 19.3.0 (range tetap ^19.0.0) |
| `vitest` (dev) | ^3.0.0 | ^5.0.3 — menutup tinypool (critical, GHSA-5gmw-xhrv-c9v3/85c8-ppgw-ccpr) & @vitest/mocker (moderate) di dev deps |
| `@eslint/eslintrc` (dev) | ^3.2.0 | dihapus (FlatCompat tidak diperlukan) |

`package-lock.json` diregenerasi di Linux (container node:22-alpine, digest dari Dockerfile).

### Perubahan kode
- `middleware.ts` → **`proxy.ts`** (konvensi Next 16; fungsi `middleware` → `proxy`, `config.matcher` sama). Catatan:
  proxy Next 16 berjalan di runtime **Node.js**, bukan Edge; `lib/brands.ts` tetap murni. Build menampilkan `ƒ Proxy (Middleware)`.
- `components/AltchaWidget.tsx` (altcha v3): atribut `challengeurl` → `challenge`; `strings` dihapus → teks Indonesia
  didaftarkan ke registry `$altcha.i18n` (`id`, di atas default `en`) + `language="id"`; `hidefooter` → atribut JSON
  `configuration` `{hideFooter: true, humanInteractionSignature: false}` (HIS dimatikan: widget tidak mengumpulkan
  event pointer/scroll — minimisasi data UU PDP). Event `statechange` `{state, payload}` tidak berubah.
  **Kontrak BFF tidak berubah:** widget v3 mendeteksi challenge v1 (`{algorithm, challenge, salt, signature, maxnumber}`)
  dan mengirim payload v1 `base64({algorithm, challenge, number, salt, signature, took})` (diverifikasi dari kode
  `createChallengeFromV1`/`createPayloadV1` di bundle altcha 3.3.0) → `lib/altcha.ts` + test tetap.
- `eslint.config.mjs`: impor langsung `eslint-config-next/core-web-vitals` + `/typescript` (Next 16 juga tidak lagi lint saat build).
- `app/akun/penghapusan/[id]/DeletionStatus.tsx`: aturan baru `react-hooks/set-state-in-effect` (error) — muat awal
  kini `loadStatus()` murni + setState di callback async dengan guard `active` (juga mencegah update setelah unmount).
- `tsconfig.json`: `jsx: react-jsx` dan include `.next/dev/types/**/*.ts` (perubahan wajib Next 16; disimpan agar build tidak memodifikasi file).
- `lib/safe-next.ts`: hapus `eslint-disable` yang kini tidak terpakai. Komentar Next 15 → 15+/16 di beberapa file, README.
- Async `params`/`cookies()`/`headers()` sudah dipakai sejak Next 15 → tidak perlu perubahan.

### Validasi (container `node:22.23-alpine@sha256:0a7108bf…`, digest Dockerfile terbaru)
`npm ci` OK · `npm run lint` 0 error (9 warning `no-location-assign-relative-destination`, aturan baru — `window.location.assign`
sengaja dipakai untuk redirect penuh setelah sesi kedaluwarsa) · `tsc --noEmit` OK · `npm test` **219 passed** ·
`npm run build` OK (Turbopack, standalone `server.js` ada, tsconfig stabil) · `npm audit --omit=dev --audit-level=high` **0 vulnerabilities**.

### Sisa / belum diverifikasi
- `npm audit` penuh (termasuk dev): 5 high `braces` (via eslint-config-next → eslint-plugin-import/micromatch); belum ada
  versi perbaikan (`fix` npm menyarankan downgrade ke eslint-config-next 14 — tidak diambil). Dev-only, tidak di bundle.
- `eslint@9.39.5` ditandai deprecated oleh npm (ESLint 10 tersedia); belum di-upgrade (di luar lingkup).
- Widget ALTCHA v3 **belum diuji di browser** (E2E/manual): tampilan, teks Indonesia, CSP (`style-src 'unsafe-inline'`,
  `worker-src blob:` sudah ada) dan verifikasi payload v1 oleh BFF `altcha-lib` perlu smoke login OTP di stack dev.
- Proxy di runtime Node: belum diuji di stack dev (perilaku host→brand seharusnya identik).

## Gelombang 17 — Belanja online Fase 3: katalog, keranjang per PT, checkout, pesanan, kode ambil (9 Okt 2026)

Dasar: docs/04 §10 (v5.0), keputusan lead L-1..L-7 (§10.16), batas file §10.15. BFF belum ada → dikembangkan terhadap kontrak; route handler diuji dengan `bffFetch` di-mock. Tidak ada commit/push.

### Halaman (Bahasa Indonesia, mobile-first, CSS module `components/online/online.module.css` memakai variabel tema brand)
| Halaman | Isi |
|---|---|
| `/belanja` | Daftar produk per kanal (brand membatasi PT via `brand.companies`), chip kategori, cari (`q` ≤ 64), halaman berikut via cursor opak. Harga `price_incl` + label pajak dari BFF; "total akhir dihitung saat checkout" |
| `/belanja/[id]` | Detail, gambar 1024 via proxy, deskripsi teks polos (`white-space: pre-line`), pilih toko ambil + status stok per toko (`/api/catalog/stock`), jumlah 1..`max_qty`, tambah ke keranjang (tamu → `localStorage` tanpa harga; member → `PUT /api/cart` dengan versi, retry sekali pada `cart_version_conflict`) |
| `/keranjang` | Gabung keranjang tamu setelah login → `GET /api/cart` → `POST /api/cart/quote`. Grup per PT/kanal ("pesanan 1 dari 2"), baris dari quote (harga satuan & subtotal termasuk pajak, issue per barang), total per PT + rincian `tax_groups` + pembulatan, ringkasan total bayar; tombol Bayar nonaktif bila ada issue. Error `price_changed/quote_expired/cart_changed/out_of_stock/item_unavailable/cart_has_issues` → quote ulang otomatis + pesan |
| `/pesanan` | Daftar milik member, status Indonesia, cursor |
| `/pesanan/[id]` | Status order & per bagian (PT, toko, `sale_order_name`, baris, pajak, refund, alasan batal); halaman bayar: QR **dirender lokal** (`qrcode-generator` 2.0.4 → SVG React), hitung mundur ke `payment.expires_at` (koreksi selisih jam dari header `Date`), batal sebelum bayar (konfirmasi dua langkah), simulator mock (dev/staging). Polling 3 dtk saat `awaiting_payment`, 30 dtk setelahnya, berhenti di status final, maks. 400 kali, jeda saat tab tersembunyi |
| Kode ambil | `PickupCodePanel`: tombol "Tampilkan kode ambil" hanya bila ada bagian `confirmed|ready`; kode di memori komponen saja, auto-sembunyi 2 menit / saat tab disembunyikan; tidak dirender server-side, tidak di URL/storage/log; peringatan "jangan bagikan" |
| Kill switch | 503 `online_unavailable` (katalog, cart, quote, order) → `OnlineUnavailable` ("Belanja online sedang istirahat… tetap bisa belanja di toko") |
| `/` (beranda) | `CartDemo`/`mock-catalog` diganti tautan Belanja / Keranjang / Pesanan saya |

### Route handler → BFF (kontrak yang diasumsikan = §10.3 apa adanya)
| Next | BFF | Guard / header |
|---|---|---|
| `GET /api/catalog/channels`, `categories?channel`, `products?channel&category&q&cursor&limit`, `products/:id?channel` | `/v1/catalog/*` | publik, tanpa Bearer; query dibangun ulang dari input tervalidasi; allowlist respons; `public, max-age=60` |
| `GET /api/catalog/stock?channel&outlet&ids` | `/v1/catalog/stock` | publik, ≤ 50 id, `no-store` |
| `GET /api/catalog/images/:sha?size=512\|1024` | `/v1/catalog/images/:sha` | stream; hanya `image/jpeg`, ≤ 2 MB (bila `content-length` ada), `immutable`, `nosniff`, CSP `sandbox`; transport memakai `bffBaseUrl()` (https + CA internal, fail-closed 503) |
| `GET/PUT/DELETE /api/cart` | `/v1/cart` | Bearer dari cookie; PUT/DELETE `isSameOrigin`; GET `isNotCrossSite`; body ≤ 16 KB; body di-allowlist `{version, groups[{channel_code,outlet_code,fulfilment,lines[{product_id,qty}]}]}` (harga klien dibuang) |
| `POST /api/cart/quote` | `/v1/cart/quote` `{}` | body klien diabaikan; `quote_hash` tidak pernah ke browser; payable non-bulat → 502 |
| `GET /api/orders`, `POST /api/orders` | `/v1/orders` | POST: `{quote_id (UUID), accept_total (int ≥ 0)}` + **`Idempotency-Key` dibuat server-side** (lihat di bawah); browser hanya menerima `{order_id, status}` |
| `GET /api/orders/:id`, `POST …/cancel`, `GET …/pickup-code` | `/v1/orders/:id…` | UUID wajib (selain itu 404 tanpa BFF); pickup-code `no-store, max-age=0` + `Pragma: no-cache` + `Referrer-Policy: no-referrer` |
| `POST /api/orders/:id/simulate-payment` | `/v1/orders/:id/simulate-payment` (§10.7) | hanya bila `EFN_PAYMENT_SIMULATOR=true` dan bukan production (`APP_ENV`), selain itu 404 tanpa BFF |

**Peta error (K-ON5, `lib/orders.ts` + `mapOnlineFailure`)**: 400/404/409/422/429 → status sama, pesan Indonesia dari peta (pesan BFF **tidak** diteruskan; kode tak dikenal → `invalid_request`/`not_found`/`rate_limited`); 401 → cookie sesi dihapus; 503 `online_unavailable` → 503 ramah; `payment_provider_error|unavailable` → 502/503; BFF tak terjangkau → 503 `service_unavailable`; 5xx lain → 502 generik.

### Asumsi kontrak / penyimpangan (untuk lead)
1. **Idempotency-Key**: §10.3.5/§10.15 menyebut `crypto.randomUUID()` di `sessionStorage`; instruksi lead G17 = dibuat server-side di route handler. Diterapkan: `chk1_` + base64url(SHA-256(`efn:checkout:v1:` + quote_id)) (48 karakter, cocok `^[A-Za-z0-9_-]{16,64}$`). Deterministik per quote → klik ganda/reload/retry = key sama = satu order; quote baru = key baru. Header dari browser diabaikan. **Mohon §10.3.5 diralat** agar QA/BFF tidak mengharapkan key acak per percobaan.
2. `image_sha` = hex huruf kecil 40 (SHA-1, checksum lampiran Odoo) atau 64 (SHA-256) karakter — kontrak tidak menyebut format.
3. Kanal per brand: `channels.company_code ∈ brand.companies` (brand `efn-mart` hanya RTL).
4. Rate limit per IP: halaman server meneruskan `x-forwarded-for` permintaan asli ke BFF; BFF harus mempercayai XFF hanya dari jaringan internal (asumsi lama `lib/bff.ts`).
5. Daftar order menampilkan nama PT dari `channel_code` (kontrak daftar tidak memuat `company_code`; benar untuk pilot RTL).
6. Header cache: lihat lanjutan G17 (diverifikasi di runtime standalone, konfigurasi diperbaiki). CSP tidak perlu diubah: gambar & QR same-origin/inline SVG (`img-src 'self'` sudah ada).

### Dependency
`qrcode-generator` **2.0.4** (pinned exact, MIT, tanpa dependency, tipe bawaan) — lock diregenerasi di container node:22.23-alpine (digest Dockerfile).

### Validasi (container `node:22.23-alpine@sha256:0a7108bf…`)
`npm ci` OK · `npm run lint` 0 error (10 warning lama `no-location-assign-relative-destination`, termasuk 1 baru di `lib/cart-client.ts` — redirect penuh ke `/masuk` disengaja) · `tsc --noEmit` OK · `npm test` **296 passed** (219 → 296; baru: `catalog` 13, `cart` 13, `orders` 10, `online-routes` 41) · `npm run build` OK (`server.js` standalone ada) · `npm audit --omit=dev --audit-level=high` **0 vulnerabilities**.

### Di luar batas file (perlu pemilik lain / lead)
Ditangani di lanjutan G17 di bawah (batas file diperluas oleh lead).

### Belum diverifikasi
- **Belum ada uji browser nyata** (rendering, hydration, QR terpindai aplikasi bank, hitung mundur, polling, keyboard/pembaca layar, layar 320 px) dan belum ada integrasi dengan BFF sungguhan — hanya unit/route test dengan mock.
- Kecocokan bentuk respons BFF aktual (mis. `201` checkout, `204` DELETE cart, `warnings`) dengan allowlist frontend → tunggu smoke QA.
- (ditutup di lanjutan G17) batas stream gambar tanpa `content-length`.

### Lanjutan G17 (9 Okt 2026) — permintaan lead: header, pembersihan, README, verifikasi cache, batas stream
Batas file diperluas khusus untuk tugas ini. Tidak ada commit/push.

1. **`components/SiteHeader.tsx`**: tautan Belanja, Keranjang, dan Pesanan (hanya bila login) di `<nav aria-label="Navigasi utama">`. `app/globals.css`: tautan nav diberi `display:inline-block; padding .625rem .25rem` (target sentuh ±44 px), dan jarak menyempit di layar ≤ 30rem. Menu membungkus ke baris berikut.
2. **Dihapus** setelah `grep` import (hanya saling merujuk satu sama lain): `components/CartDemo.tsx`, `lib/mock-catalog.ts`, `lib/tax.ts`, `tests/tax.test.ts`. Komentar yang merujuk file itu di `lib/brands.ts` dan `app/page.tsx` diperbarui. Penghapusan hanya di working tree (tidak di-stage). Kelas CSS `.cart-group/.cart-table` di `globals.css` kini tidak terpakai, tetapi dibiarkan.
3. **README superapp-web**: tabel struktur diperbarui, ditambah bagian "Belanja online (Fase 3, G17)" (kontrak, ADR, risiko, path, catatan cache) dan baris env `EFN_PAYMENT_SIMULATOR`. `components/online/README.md` dihapus (isinya pindah ke README utama).
4. **Verifikasi header cache, runtime standalone.** Uji dijalankan dengan `node server.js` hasil `next build`, `NODE_ENV=production`, di container node:22.23-alpine. BFF tiruannya HTTPS dengan CA uji sekali pakai lewat `NODE_EXTRA_CA_CERTS`; CA, kunci, skrip mock, dan skrip probe hanya ada di scratchpad, tidak di repo.
   - **Sebelum perbaikan:** semua `/api/*` menghasilkan `Cache-Control: no-store`. Aturan `headers()` di `next.config.mjs` **menimpa** header yang di-set Route Handler, sehingga katalog dan gambar tidak pernah ter-cache.
   - **Perbaikan di `next.config.mjs`:** `source: '/api/:path((?!catalog(?:/|$)).*)'` → `no-store`, ditambah `'/api/catalog/stock'` → `no-store` sebagai lapis kedua.
   - **Hasil setelah perbaikan:**

     | Route | Cache-Control |
     |---|---|
     | `/api/catalog/channels`, `categories`, `products`, `products/11` | `public, max-age=60` |
     | `/api/catalog/images/:sha` | `public, max-age=31536000, immutable` |
     | `/api/catalog/stock` | `no-store` |
     | `/api/cart`, `/api/orders`, `/api/orders/:id`, `/api/orders/:id/pickup-code`, `/api/health` | `no-store` |
     | `/belanja` (halaman) | `private, no-cache, no-store, max-age=0, must-revalidate` (bawaan Next untuk halaman dinamis) |

   - Catatan: nilai efektif pickup-code adalah `no-store`, karena config menimpa nilai handler `no-store, max-age=0`. Keduanya setara.
5. **Batas stream gambar**: `MAX_IMAGE_BYTES` = **5 MB**, ditegakkan juga saat respons tidak membawa `content-length`. Stream dibungkus `limitStream` (`lib/catalog-route.ts`). Begitu melewati batas, upstream di-`cancel` (koneksi ke BFF ditutup), stream ke browser di-error, dan tercatat log `[catalog] gambar melewati batas … req=<id>` tanpa data sensitif. `content-length` di atas 5 MB tetap ditolak dengan 502 sebelum stream.
   - **Runtime:** mock BFF mengirim 6 MB chunked, dan klien menerima tepat 5.242.880 byte lalu koneksi `terminated`.
   - **Test baru:** chunked tak berujung terputus dan upstream dibatalkan; `content-length` > 5 MB → 502; `limitStream` meneruskan utuh bila ≤ batas dan error bila > batas.
6. **Compose tidak diubah.** Env yang dibutuhkan devsecops untuk simulator: `EFN_PAYMENT_SIMULATOR=true` pada `superapp-web`, hanya dev/staging. Di staging (build production) wajib juga `APP_ENV=staging`; tanpa `APP_ENV`, atau dengan `APP_ENV=production`, simulator selalu mati. Di sisi BFF tetap dibutuhkan `PAYMENT_PROVIDER=mock`, dan endpoint simulate BFF 404 di production.

**Validasi ulang** (container `node:22.23-alpine@sha256:0a7108bf…`):
- `npm ci`: OK.
- `npm run lint`: 0 error (10 warning lama `no-location-assign-relative-destination`).
- `tsc --noEmit`: OK.
- `npm test`: **272 passed**, 13 file. Angka ini 296 − 27 test `tax.test.ts` yang dihapus + 3 test batas stream baru.
- `npm run build`: OK.
- `npm audit --omit=dev --audit-level=high`: **0 vulnerabilities**.

**Masih belum:** uji browser nyata (tampilan header di ponsel, pembaca layar, QR, polling) dan integrasi dengan BFF sungguhan.

## Gelombang 18 — konfirmasi usia 17+ & hapus akun dengan pesanan online terbuka (W-19, W-18) (10–12 Okt 2026)

Dasar: kontrak BFF di `docs/agent-reports/efn-bff-engineer.md` bagian "Gelombang 18" (Q-P3, Q-P18). Branch `chore/wave-18`; tidak ada commit/push.

### W-19 (Q-P3) — usia minimum 17 saat login/daftar
- `lib/auth-age.ts` (baru, murni): `MIN_AGE = 17` (hanya untuk teks; aturan di BFF `MEMBER_MIN_AGE`), label, pesan, dan `isAgeConfirmed` (hanya boolean `true` persis).
- `app/masuk/LoginForm.tsx`: checkbox **"Saya berusia 17 tahun atau lebih"** di langkah kode OTP, **tidak tercentang default**, `aria-required`, teks bantuan (tanpa tanggal lahir). Kirim tanpa centang → pesan + fokus ke checkbox. Respons `400 age_confirmation_required` → pesan Indonesia; challenge tidak hangus, jadi pengguna cukup mencentang lalu mengirim kode yang sama lagi. Diminta di setiap login (login = daftar).
- `app/api/auth/otp/verify/route.ts`: `age_confirmed` bukan `true` → `400 age_confirmation_required` **tanpa memanggil BFF**. Bila dicentang, diteruskan sebagai `age_confirmed: true`. BFF `400 age_confirmation_required` dipetakan ke error yang sama (sebelumnya semua 400 dianggap "kode tidak cocok").

### W-18 (Q-P18) — hapus akun saat ada pesanan online terbuka
- `lib/dsar.ts`:
  - `parseDeleteAccepted` + `open_online_orders`, `erasure_deadline` (ISO tervalidasi; null bila tidak ada pesanan terbuka).
  - `parseOpenOrdersConflict` (allowlist `open_parts`, `max_defer_days` dibatasi 1..14).
  - `openOrdersExplanation` (draf teks).
  - `isoDateOrNull`, `deletionStatusHref`.
- `app/api/me/route.ts` (DELETE):
  - Body opsional `confirm_open_orders: true` (boolean persis) → BFF `DELETE /v1/me?confirm_open_orders=true`. Path dibangun di route; token step-up dari cookie yang sama.
  - `409 open_online_orders` → `{ error: { code, message (teks frontend), open_parts, max_defer_days } }`. Pesan BFF tidak diteruskan. Cookie step-up **tidak** dihapus (BFF belum menandai jti terpakai pada pra-cek 409), cookie sesi tetap.
  - 202 kini membawa `open_online_orders` + `erasure_deadline`.
- `app/akun/data-saya/DataSaya.tsx`:
  - Langkah baru `open_orders`: judul fokus, penjelasan bahwa ada pesanan belum selesai, **kode ambil tidak lagi tampil di aplikasi**, dan **penghapusan paling lambat N (≤ 14) hari**. Ada tautan ke `/pesanan`, tombol **"Tetap hapus akun saya"** (`sendDelete(true)`, token step-up yang sama), dan tombol Batal.
  - Panel selesai menampilkan jumlah pesanan terbuka + batas paling lambat.
- Halaman status `app/akun/penghapusan/[id]`: baris "Paling lambat dihapus (ada pesanan online belum selesai)" dari query `?batas=<ISO>`. Tautannya dibuat dari respons 202; nilainya divalidasi ulang dan hanya untuk tampilan.
- README: bagian **"Teks menunggu persetujuan Legal (G18)"** mendaftar semua teks draf (usia & hapus akun).

### Asumsi / permintaan ke pihak lain
- **BFF:** `GET /v1/dsar/requests/:id` belum memuat `erasure_deadline`/`open_online_orders`, sehingga halaman status memakai query `?batas=` dari respons 202. Bila tautan dibuka tanpa query (mis. dari riwayat), baris batas tidak tampil. Usul: tambahkan kedua field ke respons status agar sumbernya server.
- Teks pelanggan = **draf, menunggu Legal/DPO**.

### Test
- Baru `tests/g18-privacy.test.ts` (14 test):
  - usia: lib, route tanpa centang/bukan boolean → 400 tanpa BFF, dicentang → diteruskan, BFF 400 age → pesan, OTP salah tetap seperti dulu;
  - W-18: parser 409 + plafon 14, teks memuat kode ambil & batas hari, parse 202, validasi ISO/tautan;
  - DELETE: 409 → penjelasan + step-up tidak dihapus, konfirmasi → `?confirm_open_orders=true` + header step-up sama + 202 dengan deadline, nilai non-boolean tidak memicu query, 409 lain diteruskan.
- `tests/dsar.test.ts` & `tests/dsar-routes.test.ts` disesuaikan dengan 2 field baru di 202.

### Validasi (container `node:22.23-alpine@sha256:0a7108bf…`, 12 Okt 2026)
- `npm ci`: OK.
- `npm run lint`: 0 error (10 warning lama `no-location-assign-relative-destination`).
- `tsc --noEmit`: OK.
- `npm test`: **286 passed**, 14 file (272 + 14 baru).
- `npm run build`: OK (`server.js` standalone ada).
- `npm audit --omit=dev --audit-level=high`: **0 vulnerabilities**.

Validasi sempat tertunda karena Docker Desktop mati setelah sesi terputus; Docker dijalankan ulang lalu seluruh rangkaian diulang dari `npm ci`.

### Belum diverifikasi
- Uji browser nyata: checkbox usia (keyboard, pembaca layar) dan alur 409 → konfirmasi → 202 terhadap BFF G18 sungguhan (perlu migrasi `08-platform-g18-privacy.sql` di stack dev).
- Teks pelanggan masih draf, menunggu Legal.

### Lanjutan G18 — L-22: batas erasure dari respons status BFF (12 Okt 2026)
Kontrak: docs/04 §10.17 L-22 (BFF c5a2b3b). Status DSAR jalur receipt dan `GET /v1/me/requests` kini membawa `open_online_orders` dan `erasure_deadline`. Tidak ada commit/push.

**Allowlist route**
- `lib/dsar-receipt.ts` `parseDeletionStatus` (dipakai `/api/dsar/requests/:id`):
  - `open_online_orders` lewat `openOrdersCount`: bilangan bulat ≥ 0, maks 999, selain itu 0.
  - `erasure_deadline`: ISO tervalidasi atau `null`. Bila field **tidak dikirim** (BFF lama), nilainya `undefined` dan tidak ikut di JSON.
  - Field lain, mis. `part_ids` atau `member_id`, tetap dibuang.
- `lib/dsar.ts` `parseDsarRequests` (dipakai `/api/me/requests`, halaman Data Saya, dan halaman status): kedua field ditambahkan, dengan default 0/null.

**Sumber tanggal di halaman status** (`resolveErasureDeadline`, murni):
1. Field dari respons status BFF. Termasuk `null`: artinya BFF menyatakan tidak ada penundaan, misalnya pesanan sudah selesai, sehingga tanggal basi dari query tidak ditampilkan.
2. Item `GET /v1/me/requests` milik sesi, bila pengguna masuk.
3. `?batas=` sebagai cadangan, hanya untuk BFF lama.

Status selesai/dibatalkan tidak menampilkan batas. Label kini menyebut jumlah pesanan terbuka.

**Data Saya:** pemberitahuan penghapusan tertunda kini menampilkan jumlah pesanan terbuka dan batas paling lambatnya.

**Test:** baru `tests/l22-dsar-status.test.ts` (9 test):
- parser: angka dan ISO; field ada / null / rusak / tidak ada;
- urutan sumber, termasuk "null dari BFF menang atas query" dan status final;
- allowlist kedua route.

`dsar.test.ts`, `dsar-receipt.test.ts`, dan `dsar-routes.test.ts` disesuaikan.

**Validasi** (container `node:22.23-alpine@sha256:0a7108bf…`):
- `npm ci`: OK.
- `npm run lint`: 0 error (10 warning lama).
- `tsc --noEmit`: OK.
- `npm test`: **295 passed**, 15 file.
- `npm run build`: OK.
- `npm audit --omit=dev --audit-level=high`: 0 vulnerabilities.

**Belum diverifikasi:** uji browser nyata terhadap BFF G18; teks pelanggan masih draf menunggu Legal.

## Gelombang 19 — 2026-10-18 (promo online, batas bayar 60 menit, katalog per brand; ditulis lead dari laporan agent)

- **Promo (docs/04 §11.2):** panel "Kode promo (opsional)" di `CartView` (maks 3 kode, `aria-live`, kode hanya di `useRef` — tidak di URL/storage/log); baris reward tampil "Diskon promo"; `promo.invalid` → satu pesan generik, checkout tidak diblokir; 429/400 → pesan, ringkasan lama dipertahankan. Route `app/api/cart/quote` menormalisasi & membatasi `promo_codes` (body ≤ 2 KB), format salah → 400 tanpa memanggil BFF.
- **Batas bayar:** teks "15 menit" dihapus; hitung mundur memakai `expires_at` server.
- **Brand (§11.3):** `Brand.brandCode`, `channelsPath`, `?brand=` tervalidasi di route channels. **L-33:** tenant yang ada memakai `brandCode: null` sampai Q-F19-5.
- **Test:** `tests/promo.test.ts` (baru), tambahan di `online-routes.test.ts`, `brands.test.ts`. Dijalankan lead di container node:22: lint OK, 16 file test lulus, `next build` OK.
- **Belum:** test komponen React CartView (alur aria-live/fallback 429), tampilan nominal negatif di browser nyata, kode promo hilang saat reload (disengaja).

## G19 perbaikan (2026-10-18)

- **QA #5 retensi kode promo:** `CartView.settlePromo` tidak lagi menimpa `promoRef` dengan `promo.applied` kosong saat fallback 429/400. Fungsi murni baru `retainPromoCodes(current, applied, fellBack)` di `lib/cart.ts`. Pesan generik ditambah "Kode Anda tetap tersimpan; ringkasan sementara dihitung tanpa kode." Kode yang tertahan tetap tampil di daftar "Kode promo terpakai" (bisa dihapus) dan dihitung terhadap batas 3. Tetap hanya di memori (tanpa URL/storage/log).
- **`qty_limit`:** `parseIssues` kini memakai allowlist `KNOWN_ISSUE_CODES` (out_of_stock, item_unavailable, qty_exceeds_max, qty_limit, price_changed); kode lain dinormalkan ke `unknown_issue` (tetap issue, tetap memblokir). Label per baris: "Jumlah melebihi batas pembelian. Kurangi jumlah barang ini." Checkout tetap nonaktif via `quoteHasIssues`; 409 `cart_has_issues` sudah memicu hitung ulang.
- **Test:** `tests/g19-fix.test.ts` (baru): parsing qty_limit, normalisasi kode tak dikenal, label, retensi kode. Belum dijalankan (tidak ada Node lokal; lead menjalankan Vitest/build).
- **Belum:** kontrak batas kanal (default 20) tidak diketahui klien, tombol "+" masih sampai 99 sehingga issue baru muncul setelah quote; uji komponen CartView; kode yang selalu 400 akan terus dikirim ulang pada tiap hitung ulang sampai dihapus pelanggan.
