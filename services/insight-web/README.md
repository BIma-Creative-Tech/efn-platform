# insight-web - EFN Insight (dashboard staf)

Aplikasi Next.js 16 (App Router, TypeScript strict) **read-only** di atas mart ESB di ClickHouse (`efn_insight.v_esb_*`).
Login dari Odoo (modul `efn_insight`) lewat token HMAC sekali pakai; data dibatasi PT user lewat row policy ClickHouse
bertanda tangan. Kontrak: `docs/04_SERVICE_CONTRACTS.md` §26 (termasuk C1-C4). Keputusan: **ADR-041 (Proposed)**.
Spesifikasi UI: `docs/design/insight-web.md`; katalog query: `docs/design/insight-web-queries.md` (menang atas §26 untuk
SQL dan nama view). Risiko: R-S18 (kunci SSO), R-L01 (PDP: hanya nama tampilan di cookie), R-S03, R-T06.

Mati secara default: `INSIGHT_ENABLED` harus tepat `true`. Bila tidak, semua halaman menampilkan "belum diaktifkan",
`POST /sso` = 404, dan data route menolak (termasuk untuk cookie yang masih berlaku).

## Alur
1. Odoo `GET /efn/insight/open` -> form POST auto-submit ke `POST /sso` (`t=eit1.<payload>.<sig>`).
2. `/sso`: Origin (C1) -> format -> HMAC (`timingSafeEqual`, kunci = byte ASCII string hex) -> payload (tepat 11 kunci) ->
   waktu -> `jti` belum pernah dipakai (cache memori, TTL 120 dtk) -> `codes`/`cids` -> 303 `/` + cookie `__Host-eis`.
3. Halaman (server component) memverifikasi cookie, memvalidasi filter dari URL, menjalankan query katalog bernama dengan
   `SQL_efn_cids/codes/sig` dari sesi (sha256(`kunci|cids|codes`)).

## Endpoint
| Path | Keterangan |
|---|---|
| `POST /sso` | login (rate limit 10/menit/IP -> 429; 404 bila nonaktif; 401 halaman generik) |
| `POST /logout` | hapus cookie -> 303 `/keluar` |
| `GET /api/health` | `{"status":"ok"}` tanpa detail, tidak menyentuh ClickHouse |
| `GET /api/export/<tabel>?<filter>` | CSV (BOM, escape formula C3). Tabel: `branch-rank flow-outliers flow-products top-menu menu-mix-branch pay-methods pay-branches dist-matrix dist-gap dq-freshness dq-alias dq-no-sales dq-non-ea dq-last-run`. Cakupan = sesi, data = pemuat yang sama dengan halaman. 401/503/500 tanpa badan. |
| `/`, `/flow`, `/sales`, `/payments`, `/distribution`, `/data-quality`, `/forecast` | 7 halaman (Forecast = placeholder G44). Path mengikuti §26.8. |
| `/belum-aktif`, `/sesi-berakhir`, `/keluar` | halaman informasi publik |

## Filter (URL, divalidasi server; tidak valid -> bawaan + pemberitahuan)
`from`,`to` (YYYY-MM-DD, `to` <= H-1 WIB, <= 366 hari), `pt` (koma, dipotong ke kode sesi), `branch` (`<brand>:<id>`, koma, maks 50),
`cat` (**parameter berulang** `?cat=A&cat=B`, karena nama kategori boleh memuat koma; maks 20), `product` (`<brand>:<kode>`, koma, maks 20).
Halaman Penjualan juga memakai `sort` (`net|qty|margin`) dan `rank` (`top|bottom`).

## Env dan secret (§26.10)
| Env | Default |
|---|---|
| `INSIGHT_ENABLED` | `false` |
| `APP_ENV` | - (`production` menolak http) |
| `CH_URL` / `CH_USER` / `CH_PASSWORD_FILE` | `https://clickhouse:8443` / `efn_insight` / `/run/secrets/clickhouse_password_insight` |
| `INSIGHT_SSO_KEY_FILE` / `INSIGHT_SESSION_KEY_FILE` / `INSIGHT_SCOPE_KEY_FILE` | `/run/secrets/insight_sso_key` / `insight_session_key` / `insight_scope_key` |
| `EFN_INSIGHT_ODOO_URL` | wajib saat aktif; **origin lengkap (scheme+host+port)** dipakai untuk cek Origin (C1) dan tautan `.../efn/insight/open` |
| `NODE_EXTRA_CA_CERTS` | `/run/secrets/efn_ca_crt` |
| `PORT` / `HOSTNAME` | `3002` / `127.0.0.1` (sidecar `insight-web-tls`) |
| `EFN_ALLOW_PLAINTEXT` | hanya dev non-production (http ke ClickHouse/Odoo) |
| `INSIGHT_CH_MAX_CONCURRENCY` | `12` (1..24): semaphore query **halaman** ke ClickHouse |
| `INSIGHT_CH_EXPORT_CONCURRENCY` | `3` (1..8): semaphore **ekspor CSV**, terpisah dari semaphore halaman (SEC-189). Gabungan <= 24 = `max_concurrent_queries_for_user` |

## Batas penggunaan (SEC-189, R-S10; di memori, replika = 1)
Semua staf memakai satu user ClickHouse `efn_insight` dengan kuota per jam bersama, jadi aplikasi membatasi per pengguna (`sub` dari sesi terverifikasi):
- **Query halaman**: maks **300 per 5 menit** per `sub` (halaman biasa ~13 query). Melebihi -> blok tampil kartu "Terlalu banyak permintaan. Coba lagi sebentar." (bukan 500).
- **Ekspor CSV**: maks **5 per 10 menit** per `sub` -> **429** + `Retry-After`. Permintaan yang ditolak otorisasi (401/503) atau tabel tak dikenal (404) tidak memakai jatah.
- **Semaphore ekspor terpisah** (3 slot, antrean 10) dari semaphore halaman (12 slot, antrean 200); ekspor tidak memakan slot halaman dan sebaliknya.
- `POST /sso`: batas 10/menit per IP dihitung **setelah** cek Origin lolos (permintaan lintas situs/tanpa Origin ditolak 401 tanpa memakai jatah).
- Log ekspor (SEC-194): `table`, `query` (nama katalog), `pt` (kode PT efektif), `from`, `to`, `rows`, `sub`; tanpa nama user/token/cookie.
- `next.config.mjs`: `images.unoptimized: true` (SEC-196; `/_next/image` tidak dipakai).
| `INSIGHT_CH_MAX_CONCURRENCY` | `12`; rentang 1..24 (= `max_concurrent_queries_for_user` profil ClickHouse; di luar rentang -> 12). Semaphore per proses: satu user ClickHouse dipakai semua staf, satu halaman = 9-13 query (galat 202 tanpa pembatas) |

Boot (`instrumentation.ts`, exit 1): tiga kunci harus hex >= 64 dan **saling berbeda** (dicek selalu untuk file yang ada, juga saat
nonaktif); saat aktif juga URL https, password terbaca, CA internal terbaca, `EFN_INSIGHT_ODOO_URL` terisi.

## Keamanan
- CSP berbasis nonce dibuat per request di `proxy.ts` (`lib/csp.ts`): `script-src 'self' 'nonce-..' 'strict-dynamic'` tanpa `unsafe-inline`/`unsafe-eval`
  (production); `style-src 'self' 'nonce-..'`; **`style-src-attr 'unsafe-inline'`** (tooltip ECharts memasang gaya lewat atribut) = satu-satunya pelonggaran, dicatat.
  `frame-ancestors 'none'`, `base-uri 'none'`, `form-action 'self'`. Referrer `no-referrer`, `nosniff`, no-store untuk semua kecuali aset statis.
- Cookie `__Host-eis` (C2) `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=7200 (2 jam, L-217)`, tanpa Domain. Kunci sesi terpisah dari kunci SSO.
- Semua akses ClickHouse lewat `lib/clickhouse.ts` (`server-only`) dan katalog `lib/queries.ts`; tanpa SQL dari pengguna; parameter bertipe.
- Log hanya kode hasil, `sub`, `jti`, durasi. Tidak pernah token/cookie/header Cookie.
- Cek Origin `/sso` (C1): header `Origin` **wajib ada** dan = origin `EFN_INSIGHT_ODOO_URL`; tanpa Origin, `Origin: null`, atau origin lain -> 401 generik
  sebelum token diperiksa (jti tidak terbakar). **Halaman form Odoo tidak boleh memakai `Referrer-Policy: no-referrer`** (browser lalu mengirim
  `Origin: null` pada POST lintas-origin); pakai `strict-origin` (hanya origin dikirim; `same-origin` juga menghasilkan `null` lintas-origin).
  Dipasang controller Odoo **dan** Traefik (`secure-headers-insight-sso` pada router `odoo-insight-open`; middleware Traefik menimpa nilai upstream).
- Gerbang sesi (`proxy.ts`) memakai **rewrite** internal ke `/sesi-berakhir` / `/belum-aktif` (bukan redirect): redirect ber-`Location` relatif membuat
  proxy Next 16 melempar `Invalid URL`, dan URL absolut dari `request.url` memuat host internal di belakang sidecar TLS.
- Pembatas konkurensi ClickHouse: `lib/semaphore.ts` (`INSIGHT_CH_MAX_CONCURRENCY`), antrean maks 200, tunggu maks 20 dtk -> kartu galat "timeout" per blok.

## Katalog query tambahan (di luar daftar kanonik §26.8)
`exec_pt_share`, `exec_sell_through`, `exec_attention` (dicadangkan; Ringkasan memakai `flow_by_branch`/`dq_*`), `flow_daily_trend`, `flow_product_daily`,
`sales_kpi`, `sales_weekday_heatmap`, `pay_kpi`, `pay_by_branch`, `dist_kpi`, `dist_transfer_top_products`, `dist_ck_gap_by_product`.
`dq_status_board` dari katalog data-engineer **tidak dipakai** (papan status dihitung dari tabel temuan); `pay_mdr` digabung ke `pay_by_method`; `sales_menu_daily` belum dibuat.
Ke-36 query dijalankan terhadap ClickHouse 24.8 oleh lead (18 Nov 2026, uji row policy + katalog).

## Dev
```
docker run --rm -v "$(pwd -W)":/app -w /app node:22-alpine sh -c "npm install && npm test && npm run lint && npm run typecheck && npm run build"
```
Dev lokal membutuhkan ClickHouse (profil `data`) dan secret; tanpa itu halaman menampilkan kartu galat per blok. Data contoh sintetis dev **tidak dibangun**.
Cookie `__Host-` + `Secure` hanya diterima browser di `https://` atau `http://localhost`.

## Belum dibangun / belum diverifikasi
Lihat `docs/agent-reports/efn-frontend-engineer.md` dan `efn-qa-engineer.md` (G42). Ringkas: Vitest + lint + typecheck + build + E2E SSO->halaman (31/31) sudah
dijalankan lead; CSP + ECharts SVG belum diuji di browser nyata / pembaca layar; panel samping menu (drawer), heatmap diverging distribusi, ekspor CSV per kartu chart, fokus-trap drawer mobile, Sankey (diganti bar bertahap).
