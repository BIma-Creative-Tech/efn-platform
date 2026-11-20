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

## Gelombang 20 — kode entitas baru, bayar di kasir, issue berat (18 Okt 2026; docs/04 §12.1–§12.3)

**Kode entitas (§12.1):** `CompanyCode` = EFN/GL/PB/BCR/BCF/FARM; `COMPANIES` berisi enam nama tampilan (Holding EFN, PT Grand Lucky, PT Paris Baguette, Bacha Coffee Retail, Bacha Coffee F&B, FARM). RTL/FNB dihapus dari kode, fixture, test, dan komentar. Tenant: `efn` = GL,PB,BCR,BCF; `efn-mart` = GL; `efn-kitchen` = PB; semua tetap `brandCode: null` (L-33). Baru `BRAND_CODE_CHANNELS` + `channelsOfBrandCode()` (`BACHA` -> BCR+BCF; hanya peta, belum ada tenant Bacha karena domain belum diputuskan).

**Bayar di kasir (§12.2):**
- `lib/payment.ts` (baru, murni): `PaymentMethod`, `parsePaymentModes`, `paymentOptions` (counter hanya bila tepat satu PT dan kanal tidak menolak; multi-PT diberi penjelasan "beberapa pesanan terpisah"), `resolvePaymentMethod`, `formatCounterCode` (alfabet §10.2, tampil `ABCD-EFGH`), ejaan & pengumuman hitung mundur untuk pembaca layar.
- `CatalogChannel.payment_modes` (nullable; null = BFF belum mengirim field -> opsi tetap ditawarkan untuk satu PT, BFF memutuskan). `CartView`: fieldset radio "Metode pembayaran" (QRIS / Bayar di kasir) dengan alasan via `aria-describedby`; 422 `payment_method_unavailable` mengunci opsi counter dengan pesan; tanpa metode yang bisa dipakai (mis. pilot counter-saja + multi-PT) checkout diblokir dengan penjelasan. Tombol: "Buat pesanan & bayar di kasir".
- `POST /api/orders` meneruskan `payment_method` (qris|counter; selain itu 400 tanpa BFF). Respons ke browser tetap hanya `{order_id, status}` — `counter_code` dari respons checkout dibuang di route.
- Halaman pesanan: status `awaiting_counter_payment` ("Menunggu pembayaran di kasir"), komponen `CounterPayPanel`: kode bayar besar (bukan kode ambil), hitung mundur dari `payment.expires_at` (koreksi jam server), instruksi "Tunjukkan kode ini ke kasir", region `aria-live="polite"` yang hanya berubah tiap menit/kritis (`role=timer` tanpa live). Kode hanya di props/memori komponen: tidak di URL/storage/log, tidak ada tombol salin; tidak dirender server (`withoutCounterCode` di `page.tsx`), klien mengambilnya lewat `GET /api/orders/:id` (no-store) sekali saat mount lalu ikut polling 10 dtk. Batal pesanan diizinkan di status ini.
- Pesan error baru: `counter_payment_single_part_only`, `payment_method_unavailable`; `too_many_open_orders` memakai teks yang ada.

**Berat (§12.3):** `weight_limit` (product_id null, tingkat grup) dan `weight_missing` (per produk) masuk `KNOWN_ISSUE_CODES` + label jelas; `groupLevelIssues`/`productIssue`; `CartView` menampilkan `role=alert` per PT untuk `weight_limit`; checkout tetap diblokir lewat `quoteHasIssues`. `ProductSummary.weight_kg` (nullable) di-parse, belum ditampilkan.

**Test (belum dijalankan; tanpa shell):** `tests/g20-counter.test.ts` (baru: opsi bayar, kode bayar, parsing order, issue berat, route POST payment_method + peta error), tambahan `brands.test.ts`, `catalog.test.ts` (payment_modes, weight_kg, nama PT), fixture RTL/FNB -> GL/PB di `cart/orders/online-routes/promo/g19-fix`.

**Asumsi kontrak untuk BFF (perlu dikonfirmasi):**
1. `GET /v1/orders/:id` mengembalikan `payment: { method: "counter", counter_code, expires_at }` selama `awaiting_counter_payment` (docs/04 §12.2 hanya mencantumkan di respons checkout). Tanpa ini kode tidak bisa ditampilkan setelah navigasi/reload.
2. `GET /v1/catalog/channels` membawa `payment_modes: ["qris"|"counter"]` per kanal (opsional; bila tidak ada, UI tidak tahu kanal pilot counter-saja dan pelanggan baru melihat 422).
3. Status order `awaiting_counter_payment` juga muncul di `GET /v1/orders` (daftar) dan bisa dibatalkan lewat `POST /v1/orders/:id/cancel`.
4. Katalog produk membawa `weight_kg` nullable.

**Belum diverifikasi:** Vitest/lint/`next build` (lead); uji browser/pembaca layar untuk `CounterPayPanel` dan radio metode bayar; tampilan di perangkat kecil; teks pelanggan menunggu Legal/Produk; tenant Bacha dua kanal belum dibuat.


## Gelombang 21 — faktur pajak atas nama pembeli di checkout (docs/04 §13.5, L-63; W-06)

**Halaman/komponen**
- `components/online/InvoiceRequestForm.tsx` (baru, terkendali): checkbox "Minta faktur pajak (B2B / atas nama)" (`aria-controls`/`aria-expanded`), pemberitahuan privasi (tujuan: penerbitan faktur pajak; retensi 10 tahun sesuai aturan perpajakan), radio jenis identitas NPWP/NIK/Paspor (fieldset+legend), kolom nomor, nama, alamat, email. Semua kolom berlabel, `required`, `aria-invalid` + `aria-describedby` ke pesan `role="alert"`; fokus pindah ke kolom salah pertama saat submit gagal validasi. Tanpa atribut `name`, `autocomplete="off"`.
- `components/online/CartView.tsx`: bagian ditampilkan hanya bila SEMUA `quote.groups[].company_code` PT PPN (`invoiceRequestAvailable`). State data faktur hanya di memori (useState); tidak ke URL/storage/log/analytics. Dihapus (`resetInvoice`) setelah submit sukses, saat 401, saat 422 `invoice_not_available`, dan saat checkbox dimatikan. Pada galat yang bisa diulang (harga berubah, dll.) data dipertahankan di memori agar pelanggan tidak mengetik ulang. Bila keranjang berubah menjadi tidak memenuhi syarat, `invoice_request` tidak dikirim.
- `lib/invoice-request.ts` (baru, murni): validasi klien (NPWP/NIK 16 digit setelah pemisah `. - spasi` dibuang; paspor 5–20 alfanumerik; nama 2–100; alamat 5–255; email dasar), `buildInvoiceRequest`, `parseInvoiceRequest` (dipakai route: buang field tambahan), teks privasi. Pesan error tidak memuat nilai yang diketik.
- `lib/brands.ts`: `CompanyInfo.businessType` ('retail' GL/BCR, 'fnb' PB/BCF, null EFN/FARM) + `allPpnCompanies()`.
- `lib/orders.ts`: `invoice_not_available` -> "Faktur atas nama belum tersedia untuk pesanan ini".
- `app/api/orders/route.ts` POST: `invoice_request` opsional divalidasi ulang, hanya lima field diteruskan ke BFF; bentuk salah -> 400 `invalid_request` generik tanpa memantulkan nilai. Tidak ada log di route; `bffFetch` hanya me-log method/path/status. Respons ke browser tetap `{order_id, status}`.

**Endpoint BFF (asumsi)**: `POST /v1/orders` menerima `invoice_request: {buyer_type: "npwp"|"nik"|"passport", tax_id, name, address, email}`; 422 `invoice_not_available` (PT PB1 atau `efn_pii` tidak aktif) diteruskan ke pelanggan; BFF memvalidasi ulang dan tidak menyimpan (K-36-7). Asumsi tambahan: aturan paspor (5–20 alfanumerik) adalah tebakan klien karena §13.5 tidak menetapkan format paspor; BFF boleh lebih ketat (akan jadi 400 generik).

**Test**: `tests/g21-invoice.test.ts` (baru): validasi per jenis, normalisasi, kelayakan PT PPN (campuran PB/BCF, kosong, kode tak dikenal), teks privasi, parse buang field tambahan, route meneruskan lima field, tidak ada field tanpa opsi, 400 tanpa BFF dan tanpa echo, 422 -> pesan Indonesia, tidak ada kebocoran ke respons/console.

**Belum diverifikasi**: Vitest/lint/`next build` (tanpa shell; lead menjalankan); tidak ada uji komponen CartView/InvoiceRequestForm (belum ada runner DOM); uji pembaca layar & fokus; tampilan perangkat kecil; teks privasi menunggu Legal; pengujian bahwa pengelola kata sandi/ekstensi browser tidak menyimpan isian (hanya mitigasi `autocomplete="off"`, tanpa `name`); format paspor.


## Gelombang 22 — pesan dari meja (QR), modifier F&B online, poin di checkout, info poin hangus (19 Okt 2026; docs/04 §14.A.3, §14.B, §14.C.2, §14.D.6, §14.7)

Tanpa shell: Vitest/lint/typecheck/`next build` belum dijalankan (lead menjalankan). Tidak ada commit.

### 1. Halaman & komponen

**`/meja` (`app/meja/page.tsx` + `components/fnb/*`)**
- `TableOrder.tsx` (client): token `fts1.…` dibaca dari fragment `#t=` SEKALI → variabel modul (`lib/table-token.ts`) → `history.replaceState` ke `pathname` (fragment dan query dibuang). Token tidak pernah ke `localStorage`/`sessionStorage`/cookie/query/log/analytics (diuji dengan storage jebakan). Seluruh panggilan lewat `/api/fnb/*` dengan header `X-Table-Session` dari memori. Bentuk token hanya diperiksa (anti-sampah); tanda tangan hanya di BFF.
- Alur: muat `session` + `menu` → kategori (chip `aria-pressed`) → `MenuItem` (panel "Pilih": `AttributePicker`, catatan ≤ 140 dengan hint, jumlah, "Tambah ke pesanan") → keranjang meja di memori (identitas baris = produk + id atribut terurut + catatan) → bilah kirim → "Pesanan terkirim": daftar SEMUA kiriman sesi (L-70: satu kiriman = satu order draft; "Kiriman #n · jam", badge status, ringkasan baris), polling 8 dtk saat ada kiriman belum final / 30 dtk bila tidak, backoff sampai 2 menit, dilewati saat tab tersembunyi, berhenti saat sesi ditutup/tidak valid. `aria-live=polite` untuk pesan & daftar.
- Keadaan: tanpa token / token ditolak ("Pindai QR di meja Anda"), sesi ditutup (`409 table_session_closed`), `503 ordering_unavailable` ("Silakan pesan ke pelayan"), galat umum + "Coba lagi".
- Kirim: `Idempotency-Key` = `crypto.randomUUID()` per percobaan, disimpan di `sessionStorage` bersama sidik isi pesanan (cyrb53, BUKAN token dan BUKAN teks catatan). Isi sama → key sama (retry setelah jaringan putus aman); isi berubah → key baru; sukses atau `idempotency_conflict` → dihapus. Jaringan putus → pesan "tekan Kirim lagi (tidak akan terkirim dua kali)".
- `409 item_unavailable` + `product_ids` → menu ditandai Habis, baris di keranjang diberi tanda, kirim diblokir sampai dihapus. `400 captcha_required` → `AltchaWidget` muncul (payload ikut di body `altcha`, remount setelah dipakai). `session_limit_reached`, `rate_limited`, dll → pesan Indonesia dari peta.
- `PointsClaim.tsx`: tampil hanya bila `session.points.claimable/claimed`. Tamu: `<details>` "Masuk untuk klaim poin" dengan `LoginForm nextPath="/meja"` TERTANAM (agar token di memori tidak hilang; pindah ke `/masuk` memuat ulang dokumen). Member: tombol klaim / batalkan klaim; klaim akun lain → informasi saja. Tidak ada saldo/nominal poin di layar ini.
- Aksesibilitas/mobile-first: semua kontrol berlabel (`label`/`legend`/`aria-label`), target sentuh ≥ 44 px, error `role=alert` + fokus ke isian bermasalah, `aria-expanded/controls` pada panel menu, bilah kirim menempel di bawah. `metadata`: `robots noindex`, `referrer no-referrer`; `next.config.mjs`: `/meja` → `Cache-Control: no-store`, `X-Robots-Tag: noindex, nofollow`; `robots.txt` `Disallow: /meja`.

**Route Handler `app/api/fnb/**`** (`lib/fnb-route.ts`): `session` (GET), `menu` (GET), `orders` (GET daftar, POST kirim), `orders/[id]` (GET status; id bukan UUID → 404 tanpa BFF), `points-claim` (POST/DELETE; login wajib). Guard: GET → `isNotCrossSite`, tulis → `isSameOrigin`; header token meja salah → `401 table_session_invalid` tanpa BFF; Bearer dari cookie HttpOnly opsional; Bearer tidak valid di BFF → `unauthenticated` + cookie dihapus (klien mengulang SEKALI sebagai tamu, `fnbApi`); body ≤ 16 KB; POST di-allowlist ulang (`sanitizeSubmitBody`: hanya `product_id, qty, attribute_value_ids, note, altcha`; catatan dinormalisasi dan divalidasi → `400 note_invalid`); respons di-allowlist; semua `no-store`; pesan BFF tidak diteruskan; 503/BFF mati → `503 ordering_unavailable`.

**Checkout poin (`CartView.tsx`, `lib/points.ts`)**: `quote.points` di-parse (`parseQuotePoints`); bagian "Bayar dengan poin (opsional)" muncul bila saldo tersedia > 0 dan ada PT yang `redeem_allowed`: saldo tersedia + nilai (1 poin = Rp…), isian poin per PT (min/maks/nilai dari quote, tombol "Maks" = saldo dikurangi isian PT lain, dibatasi maks PT), porsi tunai per PT ("Dibayar poin Rp…; sisa tunai Rp…"), error per isian (`below_minimum`, `exceeds_max`, `cover_full` = sisa tunai < Rp1, format), error saldo total. Ringkasan: "Total tagihan" − "Dibayar dengan poin" = "Bayar tunai"; tombol "Bayar Rp<tunai>". Teks menegaskan poin = alat bayar bukan diskon (pajak atas harga penuh). `POST /api/orders` meneruskan `points_redeem: [{channel_code, points}]` (validasi ulang: satu entri/kanal, bulat > 0, hanya dua field); `accept_total` tetap Σ `amount_payable`; `Idempotency-Key` tetap dari `quote_id` saja (kontrak: key sama + poin beda → `409 idempotency_conflict`). Error baru dipetakan ke pesan Indonesia: `insufficient_points`, `below_minimum`, `exceeds_max_pct`, `points_unavailable`, `points_cover_full_amount` (empat pertama → quote dihitung ulang; `points_unavailable` → isian dikosongkan). `OrderView`/`CounterPayPanel`: total, "Dibayar dengan poin", "Dibayar tunai" (QRIS & kasir memakai porsi tunai `cashDue`), per PT poin ditukar / tunai / poin diperoleh / poin dikembalikan.

**Modifier F&B online**: `lib/attributes.ts` (parse format A.6, pilihan, validasi, ringkasan, tambahan harga indikatif); `AttributePicker` (dipakai bersama `/meja`): `radio`/`pills` → radio, `select` → `<select>`, `multi` → kotak centang; opsional diberi "Tanpa"; error wajib dengan `role=alert`. `AddToCart` menampilkan picker bila produk punya atribut dan mengirim `attribute_value_ids`. `lib/cart.ts`: `CartLine.attribute_value_ids?` (≤ 10, terurut, dihilangkan bila kosong → ritel tidak berubah); identitas baris = produk + atribut; `addItem/setQty/mergeGroups/sanitizeCart` ikut; keranjang tamu menyimpan ringkasan pilihan (`options`, hanya tampilan); `QuoteLine.attributes_text`; `pairQuoteLines` memasangkan baris keranjang ↔ baris quote menurut urutan kemunculan produk; issue `attribute_invalid` (label + memblokir checkout). Katalog: `ProductSummary.attributes`, `CatalogChannel.business_type/loyalty`, `priceDisplay()`: kanal F&B menampilkan harga SEBELUM pajak + "belum termasuk PB1 10%" (B-5), ritel tetap termasuk pajak; rincian pajak final dari `tax_groups` quote (frontend tidak menghitung pajak).

**Info poin hangus (`app/akun/page.tsx`)**: `MeResponse` + `points_reserved`, `points_expiry`. Bila `expires_on` terisi dan saldo > 0: kotak "Poin akan hangus" berisi tanggal (zona WIB, hari tersisa), jumlah (= seluruh saldo), nada mendesak (≤ 7 hari). Bila `points_reserved > 0`: catatan poin ditahan untuk pesanan belum dibayar. Teks DRAF (L-73).

### 2. Ketergantungan ke endpoint BFF (path + kontrak yang diasumsikan)

| Dari browser | BFF | Dipakai |
|---|---|---|
| `GET /api/fnb/session` | `GET /v1/fnb/session` | `outlet_name, table_label, company_code, brand_code, expires_at, confirm_mode, limits{max_lines,max_line_qty,submissions_left}, points{claimable,claimed,claimed_by_me}` |
| `GET /api/fnb/menu` | `GET /v1/fnb/menu` | `categories[], products[]{product_id,name,category_id,image_sha,price_excl,tax_labels,price_note,available,attributes[A.6]}`; gambar via `/api/catalog/images/:sha` yang sudah ada |
| `POST /api/fnb/orders` | `POST /v1/fnb/orders` + `Idempotency-Key` | body `{lines[{product_id,qty,attribute_value_ids,note?}], altcha?}` → `202 {submission_id,status,lines[],indicative_subtotal_excl}` (replay `200`) |
| `GET /api/fnb/orders` | `GET /v1/fnb/orders` | `items[{submission_id,created_at,status,lines[{name,qty}]}]` |
| `GET /api/fnb/orders/:id` | `GET /v1/fnb/orders/:id` | `{submission_id,status,updated_at}` (dipakai route; UI memakai daftar) |
| `POST/DELETE /api/fnb/points-claim` | `POST/DELETE /v1/fnb/session/points-claim` | `200 {claimed:true}` / `204`; header `X-Table-Session` + Bearer |
| `POST /api/cart/quote` (sudah ada) | `POST /v1/cart/quote` | respons + `points{balance_available,point_value_idr,groups[{channel_code,redeem_allowed,min_points,max_points,max_value_idr}]}`; baris quote + `attributes_text`; issue `attribute_invalid` |
| `PUT /api/cart` (sudah ada) | `PUT /v1/cart` | lines + `attribute_value_ids?` |
| `POST /api/orders` (sudah ada) | `POST /v1/orders` | + `points_redeem[{channel_code,points}]`; error `insufficient_points, below_minimum, exceeds_max_pct` (409), `points_unavailable, points_cover_full_amount` (422) |
| `GET /api/orders/:id` (sudah ada) | `GET /v1/orders/:id` | + `amount_points_value, amount_cash`; per bagian `points_redeemed, points_value_idr, amount_cash, points_earned, points_restored` |
| `GET /v1/catalog/channels`, `/products` | (sudah ada) | channel + `business_type`, `loyalty{earn,redeem}`; produk + `attributes[]` |
| `GET /v1/me` (halaman akun) | (sudah ada) | + `points_reserved`, `points_expiry{inactive_since,expires_on,notice_sent}` |

Error yang dikenali di `/api/fnb/*`: `table_session_invalid` (401), `unauthorized` (401, Bearer tidak valid), `table_session_closed`, `idempotency_conflict`, `item_unavailable` (+`product_ids`), `points_already_claimed`, `account_deleting` (409), `invalid_request`, `idempotency_key_required`, `note_invalid`, `captcha_required` (400), `session_limit_reached`, `points_unavailable` (422), `rate_limited` (429), `ordering_unavailable` (503), `not_found` (404). Kode lain → `invalid_request`/`not_found`/`rate_limited` menurut status, atau `upstream_error` (5xx).

### 3. Keputusan implementasi & asumsi yang perlu dikonfirmasi

1. **Token di memori, bukan `sessionStorage`** (instruksi lead; meralat kalimat di §14.A.2). Konsekuensi UX: memuat ulang `/meja` (atau navigasi penuh) = pindai ulang QR; keranjang meja ikut hilang. Karena itu login untuk klaim poin dilakukan di halaman (LoginForm tertanam) dan bukan lewat `/masuk`.
2. **Idempotency-Key kiriman** disimpan di `sessionStorage` (bukan token). Bila lead ingin nol penyimpanan sama sekali, retry setelah reload tidak lagi idempoten (token juga sudah hilang saat reload, jadi dampaknya kecil).
3. `pills` diperlakukan pilih-satu (stricter dari server); `multi` = pilih banyak. Kontrak hanya menyebut "satu nilai per atribut `radio/select`".
4. Pemasangan baris keranjang ↔ baris quote untuk produk sama dengan modifier berbeda memakai URUTAN (quote tidak membawa id atribut). BFF/Odoo harus menjaga urutan baris quote = urutan cart, atau menambahkan `attribute_value_ids` ke baris quote.
5. `points_earned`/`points_restored` pada `GET /v1/orders/:id` diasumsikan per bagian dan berupa JUMLAH POIN (kontrak §14.C.2 tidak merinci level/satuan; kolom DB `points_restored_value` bernilai Rp). Order-level hanya `amount_points_value` + `amount_cash`.
6. Kanal F&B: harga katalog memakai `price_excl` + `tax_labels`; `business_type` dari BFF, fallback dari kode PT (PB/BCF = fnb).
7. Tampilan `expires_on`: jumlah yang hangus = seluruh `points_balance` (semantik §14.D.3: semua lot hangus bersamaan). Teks tidak menyebut "12 bulan" karena `/v1/me` tidak membawa nilainya.
8. Menu `Cache-Control: private, max-age=60` dari BFF tidak dipakai: aturan `/api/*` di `next.config.mjs` memaksa `no-store` (menu diambil sekali per kunjungan).
9. Idempotency-Key checkout tidak diubah oleh poin (tetap dari `quote_id`): bila percobaan pertama sudah membuat order lalu pelanggan mengganti jumlah poin, BFF menjawab `409 idempotency_conflict` → pesan "sedang diproses, buka daftar pesanan" (sesuai kontrak).

### 4. Test (Vitest; belum dijalankan)

`tests/g22-fnb.test.ts` (token fragment → memori tanpa menyentuh storage, URL dibersihkan, catatan, keranjang meja, sanitasi body, Idempotency-Key, parser, polling, peta pesan), `tests/g22-points.test.ts` (parse `quote.points`, alokasi/porsi tunai ≥ Rp1/saldo lintas PT/"Maks", `sanitizePointsRedeem`, parsing pesanan dengan poin, info hangus: tanggal WIB/hari/teks), `tests/g22-attributes.test.ts` (parse A.6, pilihan, identitas baris keranjang, guest cart, `pairQuoteLines`, `attribute_invalid`, katalog F&B, `priceDisplay`), `tests/g22-routes.test.ts` (route `/api/fnb/*` dengan BFF di-mock: guard/Origin, token hilang, Bearer opsional/dihapus saat 401, Idempotency-Key, allowlist, peta error, `product_ids`, klaim poin wajib login, `points_redeem` + kode error poin). Test lama tidak diubah; keluaran parser lama kompatibel (field baru ditambahkan, `toEqual` lama tetap lolos menurut telaah).

### 5. Belum diverifikasi

- Vitest, ESLint (aturan `react-hooks` v7: sengaja menghindari `setState` sinkron di effect dan `Date.now()` di render), `tsc`, `next build` — belum dijalankan.
- Perilaku browser nyata: `history.replaceState` menghapus fragment di iOS Safari/Chrome Android; LoginForm tertanam mempertahankan state `/meja` setelah `router.replace('/meja')` + `router.refresh()` (jika remount, token hilang dan pelanggan harus pindai ulang); polling dengan 2+ ponsel di meja yang sama terhadap batas 30/menit/sesi.
- Tidak ada uji komponen (belum ada runner DOM); uji pembaca layar & fokus pada `MenuItem`, `AttributePicker`, `PointsSection`.
- Kontrak BFF belum ada saat ini ditulis: semua bentuk di tabel §2 adalah pembacaan dokumen, bukan integrasi nyata.
- Teks pelanggan (poin hangus, klaim poin, "tamu", syarat program) menunggu Legal.
- Pembayaran online di ponsel untuk `/meja` = tahap 2 (bukan cakupan G22).

## G22 r15 — perbaikan temuan QA (P3), `/meja`

1. Daftar kiriman (`lib/fnb.ts`): baris kiriman kini `{name, qty, attributes_text, attribute_value_ids}`. `attributes_text` dinormalkan (kontrol dibuang, maks 300); `attribute_value_ids` lewat `normalizeAttributeIds` (tidak valid -> `[]`). Field absen -> `''`/`[]` (kompatibel BFF lama). `TableOrder.tsx` menampilkan `2 × Kopi (Less sugar, Es)` pada daftar "Pesanan terkirim" dan tidak lagi membuang atribut saat kiriman 202 ditambahkan optimistis.
2. `item_unavailable`: helper murni `itemUnavailableFeedback(ids, lines)`. Hanya `product_ids` yang ada di keranjang yang ditandai (`menu.available=false` -> baris keranjang menampilkan "Tidak tersedia", tombol kirim terkunci) dan teks "Hapus menu yang ditandai" dipakai. `product_ids` kosong/tidak cocok -> `ITEM_UNAVAILABLE_GENERIC` tanpa klaim penandaan. Route Handler `mapFnbFailure` sudah meneruskan `product_ids` (tidak diubah).
3. Token meja tetap hanya di memori; `lib/table-token.ts` tidak disentuh.

Test (Vitest, `tests/g22-fnb.test.ts`): ekspektasi `lines` lama diperbarui; ditambah uji atribut per baris dan uji `itemUnavailableFeedback`. Belum dijalankan (tanpa shell); lead menjalankan Vitest/tsc/lint. Asumsi kontrak: BFF mengirim `attributes_text` (string) dan `attribute_value_ids` (int[]) per baris `GET /v1/fnb/orders` dan 202 submit.

## Gelombang 42 — `services/insight-web` (EFN Insight, dashboard staf; ADR-041 Proposed, docs/04 §26) (18 Nov 2026)

Status: kode ditulis TANPA shell. **Belum pernah `npm install`, `tsc`, lint, test, atau build dijalankan.** Tidak ada `package-lock.json` (lead menjalankan `npm install` di container). Tidak ada commit.

### 1. Struktur
- Konfigurasi: `package.json`, `tsconfig.json`, `next.config.mjs`, `eslint.config.mjs`, `vitest.config.mts` (alias `server-only` -> stub), `Dockerfile` (target `dev`/`build`/`runtime`, `USER app`, PORT 3002, HOSTNAME 127.0.0.1, HEALTHCHECK `/api/health`), `.dockerignore`, `.gitignore`, `README.md`.
- Gerbang/boot: `proxy.ts` (nonce CSP + gerbang sesi), `instrumentation.ts` + `lib/instrumentation-node.ts` (exit 1 bila kunci tidak hex >= 64 / kembar; saat aktif juga URL/secret/CA/origin Odoo), `lib/config.ts`, `lib/runtime.ts` (config, kunci, state memori), `lib/csp.ts`.
- Auth: `lib/hmac.ts` (kunci = byte ASCII), `lib/claims.ts`, `lib/token.ts` (eit1), `lib/session.ts` (`__Host-eis`, eis1), `lib/gate.ts`, `lib/auth.ts` (server-only), `lib/origin-guard.ts` (C1), `lib/sso-handler.ts`, `lib/jti-cache.ts`, `lib/rate-limit.ts`, `lib/log.ts`; route `app/sso`, `app/logout`, `app/api/health`, `app/api/export/[name]`.
- Data: `lib/queries.ts` (katalog, server-only), `lib/ch-params.ts`, `lib/clickhouse.ts` (server-only), `lib/scope.ts`, `lib/filters.ts`, `lib/loaders.ts` (konteks, `settle`, pemuat tabel bersama halaman+CSV), `lib/shell-data.ts`, `lib/page-ctx.ts`, `lib/vm.ts`, `lib/table-defs.ts`, `lib/csv.ts`.
- UI: `app/layout.tsx`, `app/globals.css` (token spec §2, light/dark/ikuti sistem), `app/(app)/layout.tsx` (cangkang), halaman `app/(app)/{page,flow,sales,payments,distribution,data-quality,forecast}`, halaman publik `belum-aktif`, `sesi-berakhir`, `keluar`, `not-found`; komponen `components/{ui,blocks,ChartCard,EChart,DataTable,FilterBar,MultiSelect,Chrome,FreshnessBadge,InfoPage,FilterNotice,RetryButton}.tsx`; `lib/{format,dates,brands,thresholds,contrast,chart-spec,echart-option,client-nav,page-filters}.ts`; `public/theme-init.js`.
- Tests (Vitest, `tests/`): `token`, `sso-handler`, `scope-csv`, `filters-format`, `queries`, `config-contrast`, + `helpers.ts`, `stubs/server-only.ts`.

### 2. Dependensi (di-pin)
Runtime: `next ^16.4.1`, `react ^19.0.0`, `react-dom ^19.0.0`, `echarts 5.6.0` (**exact**, impor modular `echarts/core` + LineChart/BarChart/HeatmapChart + Grid/Tooltip/Legend/Aria/MarkLine + SVGRenderer; dimuat `next/dynamic` ssr:false), `server-only ^0.0.1`, `@fontsource-variable/geist ^5.3.0`, `@fontsource-variable/geist-mono ^5.3.0`. Dev: `typescript ^5.7.2`, `vitest ^5.0.3`, `eslint ^9.17.0`, `eslint-config-next ^16.4.1`, `@types/node ^22.10.2`, `@types/react(-dom) ^19.0.x`. Sama dengan superapp-web kecuali `echarts`; tanpa pustaka UI. ECharts 6 sengaja tidak dipakai (5.6.0 diketahui stabil; ganti bila lead mau).

### 3. Keputusan CSP
Nonce per request (proxy.ts): `script-src 'self' 'nonce-X' 'strict-dynamic'` (tanpa `unsafe-inline` dan `unsafe-eval` di production); `style-src 'self' 'nonce-X'`; **`style-src-attr 'unsafe-inline'`** (tooltip ECharts memasang `style=""`; satu-satunya pelonggaran). Dev: `unsafe-eval` + `style-src 'unsafe-inline'` (React dev/Turbopack). `img-src 'self' data:`, `font-src 'self'`, `connect-src 'self'`, `object-src 'none'`, `base-uri 'none'`, `form-action 'self'`, `frame-ancestors 'none'`, `upgrade-insecure-requests`. Route `/api/*` memakai `default-src 'none'`. Halaman galat `/sso` memakai CSP statis dengan satu blok `<style>` inline (`style-src 'unsafe-inline'`, tanpa skrip/form). `theme-init.js` dimuat sebagai skrip eksternal ber-nonce. **Risiko belum diuji**: (a) renderer SVG ECharts dapat menyisipkan elemen `<style>` untuk animasi/emphasis CSS yang akan diblokir `style-src` ber-nonce (dampak: kosmetik; bila tampak rusak, fallback = `style-src 'self' 'unsafe-inline'` dan catat); (b) Next harus memasang nonce ke semua skrip; (c) `<style>` Next/fontsource tanpa nonce.

### 4. Test yang ditulis
- Vektor §26.3: `payload_b64` = base64url(JSON) (cek salin), sig kunci ASCII diterima, sig kunci hex-decode (`i5CARc...`) ditolak (dan dihitung ulang dari `Buffer.from(KEY,'hex')`), sig tidak kanonik ditolak.
- Token: kedaluwarsa, iat masa depan, umur > 60/< 1, aud/iss/v salah, kunci ekstra/kurang, jti ulang (replay), jti format, codes asing/tak urut/duplikat/kosong, cids tak urut/0/negatif/>32, roles user tanpa cids, roles ganda/salah, sub di luar rentang, name > 64 + karakter kontrol, kunci salah, payload diubah, urutan pemeriksaan, `parseKey`.
- `POST /sso` (Request/Response nyata): sukses (303, atribut cookie lengkap, nama `__Host-eis`, cookie sah di bawah kunci sesi dan TIDAK di bawah kunci SSO), replay 401 generik, Origin salah / port beda / scheme beda / `null`, tanpa Origin (+ `Sec-Fetch-Site: cross-site`), nonaktif 404, tidak terkonfigurasi 503, token ekstra/aud/codes 401 tanpa cookie, content type/field ekstra/>4 KiB 400, rate limit 429 + `Retry-After` per IP, cookie palsu (kunci lain, payload diubah, kedaluwarsa, format).
- Scope: vektor text2sql (`"ab"*16`, `1,3`, `BCR,PB`) dihitung ulang dengan `crypto`, kunci ASCII vs hex, all_brands cids kosong, urutan numerik, kode/id invalid.
- CSV C3 (= + - @ TAB CR), kutip/koma/newline, BOM, null/NaN. Origin guard. Filter (default, WIB, rentang tak valid, pt, branch, cat berulang, product, maks). Format id-ID (contoh spec: `Rp 1,2 jt`, `Rp 12 jt`, `Rp 3,4 M`, delta, tanggal, rentang, umur). Ambang. `trendSeries`/`withOther`/`summarize`.
- Katalog: setiap query memiliki placeholder = parameter terdeklarasi dengan tipe cocok, filter PT lapis 1, LIMIT literal, hanya `efn_insight.v_esb_*`; serialisasi parameter (escape, tolak backslash/kontrol/injeksi); query string request (setting tetap, `SQL_efn_*`, hanya param terdeklarasi).
- Konfigurasi/boot (kunci kembar/lemah/hilang, URL http/kredensial, origin Odoo + port, CA/password), gerbang sesi, JtiCache, RateLimiter, `clientIp`, dan **kontras WCAG token `globals.css`** (dibaca dari berkas): teks/status/aksen/chrome/series >= 4,5 atau 3, `textOn` di skala sekuensial.

### 5. Penyimpangan / catatan terhadap kontrak (lead putuskan)
1. **`/sso` nonaktif = 404** (permintaan lead) bukan 503 (§26.4). Satu konstanta di `lib/sso-handler.ts`.
2. **Origin `null`**: halaman form Odoo ber-`Referrer-Policy: no-referrer` (§26.2) membuat browser mengirim `Origin: null` pada POST lintas-origin sehingga C1 menolak SEMUA login sah. Odoo harus memakai `strict-origin` (hanya origin) pada halaman form (atau lead menyetujui pengecualian `null`). Perlu dites end-to-end Odoo -> insight-web.
3. Path halaman mengikuti §26.8 (`/flow`, `/sales`, `/payments`, `/distribution`, `/data-quality`), bukan path Indonesia di spec UI.
4. `cat` memakai parameter berulang (nama kategori boleh memuat koma); `product` wajib berawalan brand (katalog queries menang atas §26.9).
5. CH_URL http hanya dengan `EFN_ALLOW_PLAINTEXT=true` dan bukan production (lebih ketat dari §26.10).
6. `dq_status_board` tidak dipakai; `exec_attention` ada di katalog tetapi tidak dipanggil Ringkasan (cadangan).
7. Kesegaran di header = max umur `sales`/`payment` per sesi (bukan per filter `pt`).

### 6. Belum dibangun (dari spec)
Panel samping detail menu (`sales_menu_daily` tidak dipakai), heatmap diverging distribusi (diganti bar neto transfer), Sankey (diganti bar bertahap), ekspor CSV per kartu chart (hanya tabel), fokus-trap drawer mobile (hanya Esc + tombol), skeleton khusus per kartu saat ganti filter (hanya `aria-busy`/opasitas), data contoh sintetis dev, panel "Hanya satu brand: warna merek" khusus, target HPP per brand, toast filter (diganti pemberitahuan di halaman), tooltip pada rail ikon tablet (teks tautan disembunyikan secara visual dengan teknik sr-only, jadi nama aksesibel tetap ada; tooltip visual belum).

### 7. Belum diverifikasi
Semua: install/typecheck/lint/test/build; hasil Next 16 `proxy.ts` + nonce; hook React `react-hooks` v7 (aturan baru dapat menandai pola efek); kompatibilitas Request header `origin` di undici untuk test; SQL katalog terhadap ClickHouse 24.8 (`wait_end_of_query=1`, `SQL_*` custom settings di profil readonly=2, format `param_*` Array(String), `LIMIT` literal, alias `sales_channel_split`, window function `menu_mix_by_branch`); ECharts SVG di bawah CSP; kontras seluruh pasangan tema (dites otomatis hanya yang tercantum); perilaku `<details>` dropdown dengan pembaca layar; angka KPI dibandingkan Metabase; performa (tiap halaman 5-9 query paralel + 4 query layout per navigasi; tanpa cache).

### 7b. Perbaikan G42 setelah review (SEC-189/194/196, QA risiko 1/5, typecheck)
Tanpa shell; belum dijalankan. Dibaca dulu versi terkini (proxy rewrite, origin-guard tanpa-Origin-ditolak, `lib/semaphore.ts`).
1. **SEC-189**: `lib/user-limits.ts` (baru: 300 query/5 menit per `sub` untuk halaman; 5 ekspor/10 menit per `sub`; `exportConcurrencyFromEnv`), limiter disimpan di `lib/runtime.ts` (`getPageQueryLimiter`, `getExportLimiter`). `chQuery(name, session, bag, pool='page')` kini menerima `sub`, menolak `ChError('rate_limited')` bila batas halaman habis, dan memilih semaphore: `page` (12) atau `export` (3, antrean 10, env **`INSIGHT_CH_EXPORT_CONCURRENCY`** baru, 1..8). `Ctx.pool` meneruskan pool lewat `run()`. `settle()` mengembalikan `reason:'rate_limited'`; `ErrorCard`/`Block` menampilkan "Terlalu banyak permintaan. Coba lagi sebentar." Route ekspor: 429 + `Retry-After` setelah auth + validasi nama tabel (401/503/404 tidak memakai jatah), pool `export`.
2. **SEC-194**: log ekspor `{table, query, pt, from, to, rows, sub}` (`TABLE_QUERIES` di `lib/loaders.ts`); tanpa nama/token/cookie.
3. **QA risiko 1**: di `handleSso` cek Origin sebelum rate limit.
4. **SEC-196**: `images: { unoptimized: true }`.
5. **Typecheck** `tests/session-logout.test.ts`: array kasus diberi tipe `Record<string,string>[]` (test tidak dilemahkan).
6. **QA risiko 5**: parameter `secFetchSite` dihapus dari `isOriginAllowed(origin, odooOrigin)` beserta pemanggil/test.
Test baru: `tests/user-limits.test.ts`, `tests/ch-pools.test.ts` (rate_limited per sub, pool ekspor vs halaman tidak saling memblokir), `tests/export-route.test.ts` (429, jatah tidak terpakai oleh 401/404, pool, escape CSV, log audit), kasus baru di `tests/sso-handler.test.ts` (Origin salah tidak memakai jatah login). Belum diverifikasi: `vi.mock` alias `@/lib/runtime` dan impor dinamis `@/lib/clickhouse` dengan env semaphore; batas 300/5 menit terhadap beban nyata (butuh staging).

### 8. Yang lead perlu jalankan
```
cd services/insight-web
docker run --rm -v "$(pwd -W)":/app -w /app node:22.23-alpine sh -c "npm install && npm test && npm run lint && npm run typecheck && npm run build"
```
lalu commit `package-lock.json`; `docker build --target runtime services/insight-web`; uji end-to-end SSO dengan Odoo `efn_insight` (Origin/Referrer), dan SQL katalog di ClickHouse 24.8 (`docs/design/insight-web-queries.md`). Bila lint menolak `react-hooks/*`, sesuaikan pola efek di `components/Chrome.tsx`/`EChart.tsx`.

## Gelombang 50 (G50) — Super-App web: consent tracking, event perilaku, wishlist, notify-me, price alert (20 Nov 2026)

Referensi: docs/04 §28.6/§28.8/§28.10, ADR-047 (Proposed). Tanpa shell: **belum ada `npm test`, `tsc`, lint, atau `next build` yang dijalankan.** Tidak ada commit. Hanya `services/superapp-web/**` + laporan ini yang disentuh.

### 1. Halaman & komponen
- Provider: `components/engage/EngageProvider.tsx` (di `app/layout.tsx`): memuat flag dari `GET /api/config`, konteks `{features, ready, loggedIn, announce, disableTracking}`, live region/toast yang bisa ditutup, dan `TrackingRuntime` (flush periodik, `visibilitychange`/`pagehide`, status tautan akun). `FeatureGate.tsx` menyembunyikan anak bila flag mati.
- Consent: `TrackingBanner.tsx` (di alur dokumen, tidak menutupi fokus; Izinkan/Tolak setara; teks bertanda DRAFT menunggu DPO), halaman publik `app/privasi/pelacakan/page.tsx` + `TrackingSettings.tsx` (pilihan perangkat + tombol eksplisit consent akun `tracking`). Tautan di footer, `/akun`, `/akun/privasi` (semua lewat `FeatureGate`).
- Event: `lib/tracking.ts` (murni: teks/versi, UUID v4, allowlist event, `EventQueue`, `TrackingSender` mesin status 429/disabled/403, `VisibleTimer`, `parseFeatures`), `lib/tracking-client.ts` (cookie `efn_tc`/`efn_did` + cermin localStorage, try/catch, fetch + `sendBeacon`), `components/engage/BehaviorTrackers.tsx` (`ProductViewTracker`, `SearchTracker`). Hook: `AddToCart` (`cart_add`), `CartView` (`cart_add`/`cart_remove` per klik, `checkout_start` per kelompok PT), `WishlistButton` (`wishlist_add/remove`), `NotifyMe` (`notify_me_request`), `PriceAlert` (`price_alert_set`), `app/belanja/page.tsx` (`search`), `app/belanja/[id]/page.tsx` (`product_view` + `duration_s`).
- Wishlist: `WishlistButton.tsx` (hati `aria-pressed`, kartu + detail; kartu = saudara `<Link>`, bukan di dalamnya), `wishlist-store.ts` (satu GET per kanal, `useSyncExternalStore`), halaman `app/wishlist/page.tsx` + `WishlistView.tsx` (wishlist, pengingat stok, pengingat harga; nama/harga dari katalog publik maks 30 produk).
- Notify-me: `NotifyMe.tsx` dalam `AddToCart` (muncul bila stok toko terpilih `out`); kanal hanya yang ada di `features.channels`; `409 already_in_stock` -> pesan + status stok dipaksa `in_stock`; batas 50 -> pesan. Price alert: `PriceAlert.tsx` di halaman produk (validasi UX < harga tampil).
- Route Handler baru: `app/api/{config,events,events/consent,me/tracking-consent,me/wishlist,me/wishlist/items,me/wishlist/items/[channel]/[product],me/restock-alerts,me/restock-alerts/[id],me/price-alerts}/route.ts`; helper `lib/engage.ts` (parser/validasi/pesan, murni) dan `lib/engage-route.ts`.
- Perubahan di file lama: `lib/bff.ts` (+`retryAfterS` pada hasil gagal; kode error juga dibaca dari `{ error: "string" }` sesuai §28.6), `app/layout.tsx`, `app/belanja/page.tsx`, `app/belanja/[id]/page.tsx`, `components/online/{AddToCart,CartView}.tsx`, `app/akun/page.tsx`, `app/akun/privasi/page.tsx`, `README.md`. `lib/consent.ts` TIDAK diubah (test lama mengunci purpose `location|marketing`); purpose `tracking` memakai route terpisah.
- Test baru: `tests/g50-tracking.test.ts`, `tests/g50-engage.test.ts` (logika murni; tidak ada runner DOM, komponen tidak diuji).

### 2. Ketergantungan ke endpoint BFF (kontrak yang diasumsikan)
| Browser -> Next | BFF | Asumsi |
|---|---|---|
| `GET /api/config` | `GET /v1/config` (publik) | `{ features: { tracking, wishlist, restock_alert, price_alert, push?, email?, wa_unofficial? } }` boolean. **Nama kunci wishlist/restock/price & kanal TIDAK ada di kontrak (hanya `features.tracking`)**; alias `restock`, `restock_alerts`, `price_alerts`, `notify_channels.{push,email,wa_unofficial}` diterima. Hanya `true` menyalakan fitur; kunci hilang = mati; `push` aktif kecuali `false` eksplisit. Gagal/404 -> semua mati. |
| `POST /api/events[?link=1]` | `POST /v1/events` | body §28.6 persis (`schema_version 1`, `device_id`, `channel:"web"`, `events[]` allowlist). `202 {accepted,duplicates,rejected[],disabled?}`; `403` (kode `tracking_consent_required`, string atau `{error:{code}}`); `429` + `Retry-After`. 404/501 -> diperlakukan `disabled`. Bearer hanya bila `link=1` (consent akun aktif). |
| `POST /api/events/consent` | `POST /v1/events/consent` | `{device_id,granted,version}` -> 2xx (`204/200/202`; `{disabled:true}` = fitur mati). Tanpa Bearer. |
| `GET/PUT /api/me/tracking-consent` | `GET /v1/me/consents` (cari purpose `tracking`), `PUT /v1/me/consents/tracking {granted,version}` | purpose `tracking` diterima BFF (ADR-047 §2); teks versi `tracking@2026-11-20` + sha256 body harus ada di env teks consent BFF. |
| `GET /api/me/wishlist?channel=` | `GET /v1/me/wishlist?channel=` | §28.8 `{items:[{channel_code,product_id,added_at}]}` |
| `PUT /api/me/wishlist/items` | `PUT /v1/me/wishlist/items` | 204; batas 200 = 409/422 (kode asumsi `limit_reached`/`wishlist_limit`) |
| `DELETE /api/me/wishlist/items/:ch/:pid` | `DELETE /v1/me/wishlist/items/:ch/:pid` | 204; 404 -> 204 |
| `GET/POST /api/me/restock-alerts` | idem `/v1/me/restock-alerts` | POST `{channel_code,outlet_code,product_id,channels[]}` -> 201 `{id}`; `409 already_in_stock`; batas 50 |
| `DELETE /api/me/restock-alerts/:id` | idem | 204; 404 -> 204 |
| `GET/POST/DELETE /api/me/price-alerts` | idem `/v1/me/price-alerts` | **Asumsi**: GET `{items:[{channel_code,product_id,target_price_idr,created_at?,expires_at?}]}`; DELETE = body `{channel_code,product_id}` pada koleksi (kontrak hanya menulis "GET/POST/DELETE"). |

### 3. Keputusan & asumsi yang perlu dikonfirmasi
1. **Menolak consent = tidak ada panggilan server dan tidak ada ID** (kontrak `POST /v1/events/consent` mewajibkan `device_id`; ADR-047 §3.1 no. 3: ID hanya setelah consent). Hanya cookie pilihan `efn_tc=0.<versi>`. Penarikan setelah pernah setuju dicatat ke server (`granted:false`) dan ID dihapus.
2. ID disimpan setelah BFF mengonfirmasi consent (bukan sebelum). Cookie `efn_did` Max-Age 180 hari (= retensi `device_link`), `efn_tc` 365 hari; keduanya first-party, SameSite=Lax, Secure di https, tidak HttpOnly (dibaca JS, tidak berisi otorisasi).
3. Consent akun tidak otomatis; event dikirim tanpa Bearer sampai member menyalakan consent akun (route `?link=1`). Bila 403 `tracking_consent_required`, pengirim berhenti dan antrean dikosongkan sampai consent berubah.
4. `channel_code` event dibatasi <= 8 karakter (kontrak §28.6) tetapi `CHANNEL_RE` katalog mengizinkan 16: kanal >= 9 karakter tidak ikut tercatat (event dibuang, tidak dipotong). Kode kanal nyata (GL, PB, BCR...) pendek.
5. `price alert`: "harga saat ini" yang divalidasi di UI = harga yang tampil (F&B = sebelum pajak, ritel = termasuk pajak). BFF harus memakai acuan yang sama atau kontrak perlu menetapkan.
6. `notify-me` untuk member tanpa app: kanal `push` tersedia di web hanya sebagai kanal ke perangkat app member; web tidak punya Web Push. Bila `features.push` tidak dikirim BFF, push tampil sebagai pilihan.
7. `DELETE` wishlist/restock/price 404 dianggap sukses (idempoten).
8. Event `cart_add/cart_remove` per klik +/−/Hapus (qty 1 atau `l.qty`), bukan selisih total keranjang.
9. StrictMode dev memicu `product_view` ganda (mount-unmount-mount); tidak terjadi di production.

### 4. Belum diverifikasi
- `vitest`, `tsc`, `eslint` (react-hooks v7), `next build`: belum dijalankan. Risiko lint: efek di `WishlistView`/`NotifyMe`/`PriceAlert` memanggil `setState` hanya setelah `await`.
- Browser nyata: `sendBeacon` same-origin membawa header `Origin` (route menolak tanpa Origin -> event beacon hilang diam-diam bila peramban tidak mengirimnya); cookie `efn_tc` terbaca antartab; layout banner di semua tema brand (kontras token tidak diuji otomatis); pembaca layar untuk toast/live region dan hati `aria-pressed`.
- Kontrak BFF belum ada saat ini ditulis (dibangun paralel): semua bentuk respons di §2 adalah pembacaan dokumen. Nama flag `features.*` selain `tracking` dan nama kode error batas jumlah adalah tebakan.
- Teks consent `TRACKING_TEXT` DRAFT: menunggu DPO/Legal (Q-SM16/17/26); mengubah `body` wajib menaikkan `version` dan memperbarui env BFF (`text_sha256`). Belum ada test hash karena tidak ada shell untuk menghitung sha256; lead/BFF perlu menghitung `sha256(TRACKING_TEXT.body)` (UTF-8) untuk env.
- Tidak ada uji komponen (tidak ada runner DOM) dan tidak ada E2E.

## G47 — Insight supermarket (insight-web), 21 Nov 2026

Kontrak: docs/04 §28.13 v20.3, desain `docs/design/insight-supermarket.md`, ADR-041 (Proposed).

- **SSO eit2** (`lib/token.ts` `verifySsoTokenV2`, `lib/sso-handler.ts`): klaim `wids`/`roles`/`cats`; sesi `eis2`; eit1 tetap diterima untuk F&B.
- **Cakupan ClickHouse** (`lib/scope.ts`, `lib/clickhouse.ts`): setting `SQL_efn_wids/personas/cats` + `SQL_efn_sig2` dari sesi terverifikasi, tidak pernah dari query string. Vektor `scripts/print-eit2-vectors.mjs` identik dengan Odoo (diverifikasi lead).
- **Halaman** `app/(sm)/{store,exec,category,dc,marketing}`; halaman bantu `tidak-berwenang`, `sm-belum-aktif`, `buka-ulang`. KPI dibaca dari `v_sm_kpi_config` (default kontrak bila gagal baca).
- **Perbaikan lead saat verifikasi:** `cleanText` merapikan spasi sebelum membuang karakter kontrol; tipe reduce di `exec/page.tsx`; `<Link>` di `tidak-berwenang`; tipe array di test.
- **Verifikasi:** `tsc --noEmit` bersih, `eslint` 0 error (1 warning: param `now` tidak dipakai di `lib/sm-data.ts`), `vitest` 499 lulus / 1 skip, `next build` sukses.
- **Belum:** uji browser end-to-end per persona di stack prod-lokal; test komponen visual.
