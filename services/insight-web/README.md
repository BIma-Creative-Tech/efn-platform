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
| `GET /api/export/<tabel>?<filter>` | CSV (BOM, escape formula C3). Tabel: `branch-rank flow-outliers flow-products top-menu menu-mix-branch pay-methods pay-branches dist-matrix dist-gap mov-category dq-freshness dq-alias dq-no-sales dq-non-ea dq-last-run`. Cakupan = sesi, data = pemuat yang sama dengan halaman. 401/503/500 tanpa badan. |
| `/`, `/flow`, `/sales`, `/payments`, `/yoy`, `/distribution`, `/data-quality`, `/forecast`, `/glosarium` | halaman F&B/gabungan (Forecast: ESB belum ada, SMD sudah). Path mengikuti §26.8; `/glosarium` = daftar istilah. |
| `/exec`, `/store`, `/dc`, `/category`, `/marketing` | halaman Supermarket per persona |
| `/belum-aktif`, `/sesi-berakhir`, `/keluar` | halaman informasi publik |

## Navigasi (review pemilik proyek 21 Nov 2026)
Sidebar tiga grup yang **bisa dilipat**, dan sidebar bisa **diciutkan jadi ikon** (>= 1280 px; di bawahnya otomatis rail). Preferensi (`insight-nav`) disimpan di
`localStorage` (try/catch; nilai rusak -> bawaan). Tombol grup `aria-expanded`; saat ciut, nama tetap ada untuk pembaca layar dan tooltip lewat `title`.
- **Ringkasan** (F&B + Supermarket): Ikhtisar `/`, Penjualan `/sales`, Pembayaran `/payments`
- **Food & Beverage**: Tahun lalu vs sekarang `/yoy`, Produksi vs Penjualan `/flow`, Distribusi CK `/distribution`, Kualitas Data ESB `/data-quality`
- **Supermarket**: Eksekutif `/exec`, Toko `/store`, DC `/dc`, Kategori `/category`, Marketing `/marketing`, Forecast `/forecast`
Visibilitas = aturan sesi yang ada (`lib/sm-personas.ts navGroups`): Ringkasan bila ada PT F&B atau exec ber-SMD; F&B bila ada kode ESB (exec ber-SMD tanpa
PT F&B hanya melihat "Distribusi CK / DC" = bagian DC supermarket); Supermarket = persona di `roles` + `INSIGHT_SM_ENABLED`, Forecast hanya untuk exec ber-SMD.
Sidebar bawah: **Glosarium** (`lib/glossary.ts`; istilah juga muncul sebagai ikon "i" di label KPI, judul kartu, dan header kolom lewat `components/Term.tsx`).

**Layar memuat yang memblokir** (22 Nov 2026, `components/LoadingOverlay.tsx`, `lib/loading-store.ts`): aktif saat klik tautan internal, drill-down,
perubahan filter, dan "Coba lagi"; `.shell` diberi `inert` sehingga menu/halaman tidak bisa diklik atau difokus sampai URL berubah (halaman tanpa
Suspense: commit = semua query selesai) atau transisi selesai; batas aman 90 dtk. Muat awal memakai `app/loading.tsx` (layar penuh). Visual: batang
animasi yang bereaksi ke kursor/sentuhan, tahapan, detik berjalan, progres perkiraan, tip; `prefers-reduced-motion` mematikan animasi.

## Persona F&B: Eksekutif, Toko, Marketing (G52, docs/04 §28.15; ADR-040, ADR-041 §3.6, Proposed)
Tiga halaman F&B dari data ESB di grup sidebar Food & Beverage (item teratas), aktif hanya bila **`INSIGHT_FNB_PERSONA_ENABLED=true`** (flag terpisah dari
`INSIGHT_SM_ENABLED`; `lib/fnb-personas.ts fnbRouteDenial` tidak membaca flag SM).

| Rute | Persona | Isi | Query (`lib/queries.ts`) |
|---|---|---|---|
| `/fnb/exec` | `exec` | margin kotor (net - HPP), YoY, margin 13 bulan, perbandingan brand/PT, biaya MDR, peringkat cabang | `fexec_*` (6) |
| `/fnb/store` | `store` | penjualan hari acuan vs minggu lalu, tren 7 hari, menu terlaris, metode bayar, kiriman CK diterima; **tanpa HPP/margin/MDR** | `fstore_*` (6) |
| `/fnb/marketing` | `marketing` | diskon (nilai, % dari kotor, per menu/cabang), kanal pembayaran (GoFood/Grab/... dari nama metode, `lib/channels.ts`), takeaway vs dine-in berlabel **perkiraan** | `fmkt_*` (6) |

- **Token/sesi v3**: `eit3`/`eis3` (14/10 kunci, klaim `brs` = `<esb_brand>:<branch_id>`, urut ASCII), aturan T1..T7 di `lib/claims.ts scopeConsistentV3` (cermin Odoo),
  T8 = regex 3000. `INSIGHT_ACCEPT_EIT2` (default `true` selama rollout; set `false` setelah Odoo 19.0.3.0.0 di semua env) mengatur penerimaan `eit2` di `/sso` **dan**
  cookie `eis2`; sesi `eis2`/`eis1` bermakna `brs = []` dan halaman `/fnb/*` mengarahkannya ke `/buka-ulang`.
- **Cakupan ClickHouse**: `scopeSettings(cids, codes, brs, key)` -> `SQL_efn_cids`, `SQL_efn_codes`, `SQL_efn_brs`, `SQL_efn_sig3` (SHA256 `KEY|cids|codes|brs`); `SQL_efn_sig`
  tidak lagi dikirim Insight. **Satu rilis dengan `clickhouse-init`** (policy sig3): insight-web lama gagal tertutup terhadap view ESB baru.
- **Kepala toko ber-cabang** (`brs` tidak kosong): hanya `/fnb/store` (+ `/glosarium`); `/` dialihkan ke `/fnb/store` (`pageCtx({home:true})`); `/sales`, `/payments`, `/flow`,
  `/distribution`, `/forecast`, `/data-quality` dan ekspor CSV -> tidak-berwenang/403; layout tanpa FilterBar/kesegaran tingkat PT; pilihan `branch` dipotong ke `brs`;
  menu SM tidak muncul (butuh kode SM). Chip "Kepala toko · <nama cabang>" / "· N cabang".
- Halaman Toko memegang hari (`?day=`, bawaan = hari terakhir yang punya penjualan <= kemarin WIB, `lib/fnb-store.ts`) dan cabang (`?branch=`) lewat form GET tanpa JS.
- Alias keluaran query G52 yang bentrok dengan nama kolom view diberi sufiks (`net_sum`, `cogs_sum`, `amount_sum`, `mdr_sum`, `method`, ...) karena alias = kolom memicu
  alias siklik ClickHouse; kontrak §28.15.9 hanya meringkas keluaran. Parameter baru: `yoy_from`, `yoy_to`, `m_from`, `ref_day` (semua `Date`).
- YoY: "n/a" + catatan bila `first_day` penjualan PT > awal rentang tahun lalu (`fexec_yoy_coverage`, `lib/fnb-vm.ts yoyStatus`); gagal memuat cakupan juga "n/a".
- Halaman penolakan baru `/fnb-belum-aktif`; `smRouteDenial` kini juga menolak sesi tanpa kode SM (kepala toko F&B tidak boleh membuka `/store` supermarket).

## Laba Rugi Outlet, Waste & Pemakaian Internal, Fill Rate Outlet ke CK (G68, docs/04 §32; ADR-040 amandemen G68, ADR-041, Proposed)
Tiga halaman di grup sidebar Food & Beverage (disisipkan sebelum Forecast), aktif hanya bila **`INSIGHT_G68_ENABLED=true`** (default `false`).
Data = view `efn_insight.v_esb_*` yang dibangun paket W-A (§32.6/§32.7; kelas PT, row policy `efn_insight_scope`); SQL belum dijalankan terhadap ClickHouse.

| Rute | Siapa | Query (`lib/queries.ts`) | Filter |
|---|---|---|---|
| `/pnl` | persona `exec` saja (`fnbRouteDenial` memetakan `/pnl` ke exec; flag F&B ikut berlaku), tanpa `brs` | `pnl_kpi`, `pnl_trend_monthly`, `pnl_statement`, `pnl_branch_rank`, `pnl_accounts` | `pt`, `branch`, **`mfrom`/`mto`** (`YYYY-MM`, maks 24 bulan, bawaan 12 bulan s.d. bulan ini, `lib/pnl.ts parseMonthRange`) |
| `/waste` | pengguna F&B ber-cakupan PT (tanpa `brs`) | `waste_kpi`, `waste_trend`, `waste_by_purpose`, `waste_branch_rank`, `waste_top_products` | `pt`, `branch`, `from`/`to`, **`uclass`** (bawaan waste + internal_use) |
| `/fill-rate` | idem | `fr_kpi`, `fr_trend_weekly`, `fr_by_outlet`, `fr_by_supplier`, `fr_top_short_products`, `fr_suppliers` (opsi filter) | `pt`, `branch` (outlet peminta), `from`/`to` (tanggal permintaan), **`supplier`** (`brand:branch_id`, maks 10) |

- Gerbang: `lib/g68-access.ts` (proxy.ts lapis 1, `lib/g68-page.ts` lapis 2, row policy lapis 3). Sesi ber-cabang ditolak (`BRANCH_BLOCKED_PATHS` + `/pnl` lewat persona exec). SMD tidak ditawarkan
  dan `pt=SMD` ditolak (halaman ESB-saja).
- P&L: baris `summary` ESB dipetakan ke lima kunci tetap lewat nama pos (`lib/pnl.ts PL_SYNONYMS`, dipakai SQL dan TS); nama pos sebenarnya belum terverifikasi [B]. Pos tak dikenal tetap tampil di tabel.
  Persen (laba kotor, EBITDA, laba bersih terhadap penjualan bersih) dihitung di TS, bukan SQL. Baris "Tidak teralokasi ke outlet" = total brand - jumlah cabang (hanya bila filter cabang kosong).
- Fill rate: rasio dihitung dari kolom aditif view (`line_ratio_sum / lines`, dst.), bukan rata-rata rasio; tanpa ambang warna (target belum ditetapkan, Q-ESB31).
- Parameter baru katalog: `m_to` (`Date`), `uclass` dan `supplier` (`Array(String)`). Alias keluaran diberi sufiks (`*_sum`, `*_label`) agar tidak sama dengan kolom view.
- Glosarium: entri baru laba kotor, EBITDA, laba bersih, tidak teralokasi, waste, pemakaian internal, `fill-rate-line` ("Fill rate outlet (baris)"), % permintaan penuh, waktu kirim, permintaan matang.
  Entri `fill-rate` (SMD) tidak diubah definisinya; pola deteksinya kini mengecualikan "Fill rate outlet".
- Belum dibangun: ekspor CSV untuk tiga halaman (kontrak: `/pnl` hanya exec; lainnya mengikuti C3). Halaman info publik baru `/g68-belum-aktif`.

## Filter (URL, divalidasi server; tidak valid -> bawaan + pemberitahuan)
**Perilaku (21 Nov 2026): filter langsung berlaku**, tanpa tombol Terapkan. Chip brand ("Semua" eksplisit) dan preset tanggal memakai `router.push` seketika;
tanggal kustom (setelah kedua tanggal sah) dan pilihan ganda cabang/kategori/produk di-debounce 400-500 ms. Rentang tanggal yang dipilih ditulis eksplisit ke URL dan
dibawa ke semua halaman lewat tautan sidebar (`components/Chrome.tsx`), termasuk grup Supermarket. Halaman tanpa rentang tanggal memberi keterangan eksplisit
"Rentang tanggal tidak berlaku: ..." (`lib/page-filters.ts DATE_NA_REASON`: `/data-quality`, `/forecast`, `/store`). Perakit URL murni: `lib/filter-url.ts`.
Bawaan rentang berbeda per halaman bila URL tanpa `from/to`: F&B 30 hari, Supermarket 30 hari kecuali Kategori dan Toko 7 hari.
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
| `EFN_INSIGHT_ODOO_URL` | wajib saat aktif; **origin lengkap (scheme+host+port)** dipakai untuk cek Origin (C1), tautan "Kembali ke Odoo" `.../odoo`, dan "Buka dari Odoo" `.../efn/insight/open` (masuk ulang) |
| `NODE_EXTRA_CA_CERTS` | `/run/secrets/efn_ca_crt` |
| `PORT` / `HOSTNAME` | `3002` / `127.0.0.1` (sidecar `insight-web-tls`) |
| `EFN_ALLOW_PLAINTEXT` | hanya dev non-production (http ke ClickHouse/Odoo) |
| `INSIGHT_FNB_PERSONA_ENABLED` | `false`; aktif hanya string persis `true` (G52, §28.15.10). Production `false` sampai pemetaan cabang dimuat + QA/security G52 lulus |
| `INSIGHT_ACCEPT_EIT2` | `true` (rollout G52); bila diset hanya `true` persis yang menerima `eit2`/`eis2`. Set `false` setelah Odoo 19.0.3.0.0 di semua env |
| `INSIGHT_G68_ENABLED` | `false`; aktif hanya string persis `true` (G68, §32.8). Mati -> `/pnl`, `/waste`, `/fill-rate` rewrite ke `/g68-belum-aktif` dan item menu tersembunyi. Production `false` sampai QA + security review G68 lulus dan backfill PB selesai |
| `INSIGHT_PRODPLAN_ENABLED` | `false`; aktif hanya string persis `true` (G66, §30.3). Mati -> `/produksi*` rewrite ke `/pp-belum-aktif` |
| `PRODPLAN_URL` | `http://127.0.0.1:7443` (sidecar `insight-web-pp-tls`); nilai lain -> modul nonaktif (fail closed) |
| `PRODPLAN_ASSERT_KEY_FILE` | `/run/secrets/prodplan_assert_key` (hex >= 64, beda dari tiga kunci lain; penanda tangan `ppa2`) |
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
Review 21 Nov 2026 (belum dijalankan terhadap ClickHouse; verifikasi lead): `product_categories` (kategori ESB per produk), `esb_coverage` (`v_esb_coverage`: banner
cakupan data + perbandingan periode lalu "n/a" bila periode tidak tercakup), `mov_by_group`, `mov_by_category`, `mov_ck_product` (`v_esb_movement_daily`: kiriman CK semua
kelompok barang, qty selalu per `uom`; `mov_ck_product` = produksi vs kiriman FIN per CK asal untuk kolom Keterangan "kirim melebihi produksi"). Cabang non-penjualan
(`is_store = 0 AND is_ck = 0`: HEAD OFFICE, gudang, KACS Damage/Expired, Temporary Variance, ...) dikeluarkan dari `list_branches`, `exec_branch_rank`,
`flow_by_branch`, `flow_outliers`, `pay_by_branch` (branch_id 0 pembayaran tetap tampil); `dist_transfer` menggabungkannya menjadi baris "Non-toko (HO/gudang/damage)" per PT.
KPI agregat (`flow_summary`, `exec_sell_through`, `dist_kpi`) sengaja tidak difilter. Kolom `Keterangan` (alasan status dengan angka) dari fungsi murni `lib/status-reasons.ts`
(+ `lib/flow-notes.ts`, `lib/sm-shelf.ts shelfReason`) dan ikut CSV.
`dq_status_board` dari katalog data-engineer **tidak dipakai** (papan status dihitung dari tabel temuan); `pay_mdr` digabung ke `pay_by_method`; `sales_menu_daily` belum dibuat.
Ke-36 query dijalankan terhadap ClickHouse 24.8 oleh lead (18 Nov 2026, uji row policy + katalog).

Query G52 (18; **belum dijalankan terhadap ClickHouse 24.8**, view `efn_insight.v_esb_store_*` belum ada sampai `21-g52-insight-fnb.sql` masuk): `fexec_kpi`,
`fexec_margin_monthly`, `fexec_pt_compare`, `fexec_mdr_cost`, `fexec_branch_rank`, `fexec_yoy_coverage`, `fstore_kpi`, `fstore_daily_vs_lastweek`, `fstore_top_menu`,
`fstore_payment_mix`, `fstore_ck_receipts`, `fstore_branch_list`, `fmkt_discount_kpi`, `fmkt_discount_trend`, `fmkt_discount_menu`, `fmkt_discount_branch`,
`fmkt_channel_mix`, `fmkt_takeaway_share`. Query `fstore_*` tidak boleh memuat `cogs`/`mdr` (diuji statis di `tests/fnb-queries.test.ts`).

## Tahun lalu vs sekarang (`/yoy`, 24 Nov 2026)

Tujuan: perbandingan tahunan yang sah (temuan analisis 24 Nov: tabel bulanan lama membandingkan Nov 2025 penuh dengan
1-20 Nov 2026, hari 17 Nov 2026 hanya separuh toko, dan "harga net per unit" tercampur item gratis/paket).

- Pembanding = rentang filter digeser **364 hari** (hari dalam minggu sama). Maks. 364 hari; bagian promo/menu maks. 124
  hari (membaca baris penjualan, bukan agregat).
- Hanya pasangan cabang-hari yang slice `sales` selesai di **kedua** sisi (`efn_insight.v_esb_sales_done_day`); hari yang
  belum lengkap dikeluarkan dari dua sisi dan disebut di banner (`yoy_gaps`).
- Cabang sebanding = berjualan di kedua periode dan penjualan pertama >= 28 hari sebelum awal periode tahun lalu. Jembatan:
  efek transaksi + efek nilai per transaksi (cabang sebanding) + cabang baru/tutup/belum sebanding = selisih total.
- Harga jual vs COGS per menu (`efn_insight.v_esb_menu_econ_daily`, per **nama** menu): harga list = baris a la carte
  berbayar (price > 0); item gratis (price 0) dan komponen paket dipisah; potongan bundel = nilai list komponen - harga
  paket (tidak ada di kolom diskon ESB).
- Eksekutif F&B (`/fnb/exec`) memakai aturan pembanding yang sama: `yoy_from/yoy_to` = 364 hari (sebelumnya tanggal
  kalender -1 tahun); delta YoY dari kolom `*_cmp` vs `*_yoy` (pasangan lengkap), nilai periode ini tetap penuh.
- ADR-040/041 (Proposed). Logika murni `lib/yoy.ts` (tests/yoy.test.ts); SQL `infra/clickhouse/init/23-insight-yoy.sql`.
- Belum: COGS ESB tidak memuat roti tidak terjual, komisi ojol, MDR; apakah ESB menghitung ulang COGS transaksi lama belum
  diverifikasi terhadap laporan ESB arsip Finance.

## Margin bahan (`/margin-bahan`, G67; docs/04 §31, ADR-040/041 Proposed)

- Akses: hanya persona `exec` (gerbang sama dengan `/fnb/exec`: `lib/fnb-personas.ts` `EXEC_ONLY_PATHS`, dicek di `proxy.ts` dan
  di halaman lewat `fnbRangePage('exec')`); menu hanya untuk exec (`fnbExecOnlyNavItems`). Filter: hanya Brand / PT.
- Data: `efn_insight.v_esb_ing_price_monthly`, `v_esb_menu_cost_monthly`, `v_esb_ing_group_monthly` (query `mb_*`, hanya `pt`).
  Logika murni `lib/margin-bahan.ts` (tests/margin-bahan.test.ts). Tanggal refresh (`aggregate_refresh`) belum di-grant ke
  insight, jadi halaman menampilkan bulan terakhir data.
- Belum: render di browser dan query terhadap ClickHouse nyata (view menunggu `24-margin-bahan.sql` + grant insight).

## Production Order (`/produksi`, G66; docs/04 §30, ADR-051 + ADR-041 §3.7, Proposed)
Modul perencanaan produksi Central Kitchen di atas ESB. **insight-web tetap tanpa akses DB/ESB**: semua data lewat API `prodplan` dari sisi server
(`lib/prodplan-client.ts`, sidecar loopback `127.0.0.1:7443`, timeout 15 dtk). **Identitas per pengguna ESB (§30.17, amandemen A1 ADR-051)**:
pengguna masuk di `/produksi/masuk` dengan username + password **ESB** miliknya; password diteruskan sekali ke `POST /v1/session` (tidak
disimpan, tidak di-log) dan prodplan membalas `sid` yang disimpan di cookie `__Host-epp` (HttpOnly, Secure, SameSite=Strict, maks 1 jam).
Setiap panggilan membawa `X-EFN-Actor: ppa2...` (`lib/prodplan-token.ts`, HMAC `prodplan_assert_key`, 8 kunci, umur 60 dtk, `jti`, terikat
method + path + hash body, membawa `sid`; tanpa identitas/peran). Peran, cabang, dan nama berasal dari respons sesi prodplan; browser tidak
pernah menerima/mengirim token itu.
- Gerbang `/produksi*` = **sesi ESB**, bukan grup Odoo: `proxy.ts` (lapis 1: cookie `__Host-epp` berbentuk sah, selain itu rewrite ke
  `/produksi/masuk`; tidak menjalankan autentikasi Odoo), `lib/pp-page.ts` (halaman, `GET /v1/session`) dan `lib/prodplan-guard.ts` (route API).
  Halaman Insight lain tetap sesi Odoo dan tidak pernah membaca `__Host-epp`. Menu "Produksi" di sesi Odoo ber-peran PP tetap tampil sebagai tautan.
- Halaman (`app/(pp)/produksi/*`): daftar, `baru`, `[id]` (tab Permintaan, Kebutuhan, Usulan batch, Transfer, Posting, Riwayat), `kemasan`;
  `app/produksi/masuk` (form masuk, tanpa cangkang). Header modul: nama ESB, brand, peran, sisa waktu sesi, Keluar; tanpa navigasi ke Insight lain.
- API (route handler, di luar matcher proxy): `POST /api/prodplan/login` (10 percobaan / 15 menit per IP) dan `/logout`, `GET /api/prodplan/expired`
  (hapus cookie + alihkan ke `?e=expired`), `POST /api/prodplan/<aksi>` (daftar aksi tertutup di `lib/prodplan-actions.ts`; same-origin ketat,
  JSON, 60 mutasi/menit per sesi) dan `GET /api/prodplan/jobs/{id}` (polling job 2 dtk, maks 5 menit). 401 `session_expired` menghapus cookie.
- Posting yang berhenti karena sesi habis (`needs_login`) dilanjutkan lewat tombol "Lanjutkan posting" (`resume-posting`).
- Galat prodplan dipetakan ke pesan Indonesia dari tabel kita (`lib/prodplan-errors.ts`); teks/`details` server disaring.
- Bentuk JSON respons prodplan **diasumsikan** (`lib/prodplan-types.ts`, parser defensif) sampai `services/prodplan` ada.

## Dev
```
docker run --rm -v "$(pwd -W)":/app -w /app node:22-alpine sh -c "npm install && npm test && npm run lint && npm run typecheck && npm run build"
```
Dev lokal membutuhkan ClickHouse (profil `data`) dan secret; tanpa itu halaman menampilkan kartu galat per blok. Data contoh sintetis dev **tidak dibangun**.
Cookie `__Host-` + `Secure` hanya diterima browser di `https://` atau `http://localhost`.

## Penjualan level struk ESB OMS (G69, 25 Nov 2026; brand PB)
Dua bagian baru, **tersembunyi rapi** (tanpa kartu galat) bila view OMS belum ada, OMS belum aktif, atau tidak ada struk pada hari itu (hanya pembatasan laju
yang tampil sebagai kartu galat). Sumber = view `efn_insight.v_esb_oms_*` (kontrak `docs/04` §33.5; dibangun
`infra/clickhouse/init/26-g69-esb-oms.sql`). Tanpa dependency baru.
- `/fnb/store` **Penjualan per jam & kanal** (`components/oms/StoreHourlySection.tsx`): hari + cabang halaman; KPI struk, pax, rata-rata nilai struk
  (penjualan bersih / struk), item per struk; grafik batang per jam (tab Penjualan bersih / Struk); tabel kanal (visit purpose).
  **Pilihan hari sendiri** lewat search param `oms_day` (hanya `today` atau `date`; selain itu diabaikan): "Hari ini (berjalan)" dan "Tanggal filter halaman" tanpa
  mengubah filter tanggal lain. Bawaan = hari ini (WIB, dihitung di server) bila `foms_freshness` mengembalikan waktu, selain itu tanggal filter. Label "Hari ini, data per
  HH:MM WIB" / "Data final". Jam 23 memuat struk yang ditutup lewat tengah malam (aturan view).
- `/flow` **Sell-through per jam** (`components/oms/SellThroughSection.tsx`): hanya bila tepat satu cabang terpilih (selain itu petunjuk "Pilih satu outlet", dan hanya
  bila OMS aktif); sampai kemarin saja (pasokan Core baru ada keesokan hari); produk dengan satuan resep non-EA (EA/PCS/PC setara) ditandai "satuan beda" tanpa persen/jam
  habis. Untuk hari terakhir filter periode; per produk FIN: pasokan toko hari itu
  (`v_esb_flow_daily`, definisi SUPPLY yang sama dengan sell-through halaman), terjual, % terjual sebelum 12:00/15:00/18:00 dan sampai tutup, perkiraan jam habis,
  catatan porsi tak terpetakan.
- Query `foms_freshness | foms_hourly | foms_fin_hourly | foms_fin_supply | foms_fin_quality` di `lib/queries.ts`. Semua view OMS kelas BR (punya
  `brand` + `branch_id`): cabang memakai makro BR yang sama dengan view BR lain. `product_key` view OMS = kode produk polos; join ke pasokan dan filter produk memakai
  `<brand>:<product_key>`. Kesegaran = `max(last_finished_at)` (view per cabang, hanya hari ini); porsi tak terpetakan = Σ`unmapped_qty` / Σ`menu_qty` x 100.
  Logika murni di `lib/oms-vm.ts`.
- Test: `tests/oms-vm.test.ts` (kumulatif, persen jam patok, jam habis, kosong), `tests/oms-queries.test.ts` (view, parameter, cakupan). Fixture sintetis.
- Belum diverifikasi: Vitest/lint/typecheck/build dan SQL insight-web terhadap view final belum dijalankan; tampilan di browser/pembaca layar.

## Belum dibangun / belum diverifikasi
G52 (22 Nov 2026): kode ditulis tanpa dijalankan (tanpa Node di host): Vitest, lint, typecheck, build, dan SQL 18 query belum dijalankan; payload_b64/sig_b64 vektor D..G
belum dicocokkan dengan builder Odoo; ekspor CSV halaman F&B persona belum dibuat (C3); UX/a11y halaman baru belum diuji di browser/pembaca layar.
Lihat `docs/agent-reports/efn-frontend-engineer.md` dan `efn-qa-engineer.md` (G42). Ringkas: Vitest + lint + typecheck + build + E2E SSO->halaman (31/31) sudah
dijalankan lead; CSP + ECharts SVG belum diuji di browser nyata / pembaca layar; panel samping menu (drawer), heatmap diverging distribusi, ekspor CSV per kartu chart, fokus-trap drawer mobile, Sankey (diganti bar bertahap).
