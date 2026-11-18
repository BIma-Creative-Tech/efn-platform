# Review Keamanan & Kepatuhan — Gelombang 42 (Insight web + SSO Odoo)

> **Status perbaikan (efn-lead, 18 Nov 2026, sebelum commit G42):** SEC-189 diperbaiki (batas per pengguna 300 query/5
> menit dan ekspor 5/10 menit → 429/kartu ramah; semaphore ekspor terpisah 3 slot). SEC-190 diperbaiki (predikat format
> kanonik `SQL_efn_codes`/`SQL_efn_cids` di row policy insight **dan** text2sql; serangan length-extension dengan sig sah +
> byte padding diuji di ClickHouse 24.8 → 0 baris; `HOST` user belum dipersempit — isolasi jaringan `insight-data`).
> SEC-191 diperbaiki (`log_queries=0` untuk DDL policy, diverifikasi tidak ada entri baru di `system.query_log`;
> runbook: rotasi kedua scope key di lingkungan lama). SEC-193 diperbaiki (`efn_insight.group_insight_all_brands` masuk
> `SENSITIVE_GROUP_XMLIDS`). SEC-194 sebagian (log ekspor memuat query/PT/rentang/baris; alert Loki lonjakan
> `bad_sig`/`replay`/`bad_origin` = sisa). SEC-195 diperbaiki (dokumen diselaraskan QA). SEC-196 sebagian
> (`images.unoptimized`). SEC-192 terbuka (menunggu Q-INS5). Verifikasi: Vitest 216, test Odoo, E2E 31/31, browser
> Chromium tanpa pelanggaran CSP/hydration.

- Tanggal: 18 Nov 2026 · Reviewer: efn-security-reviewer (read-only terhadap kode)
- Cakupan: branch `feat/wave-42-insight-web` (perubahan belum di-commit): `services/odoo/addons/efn_insight`, `services/insight-web`,
  `infra/clickhouse/access/insight-access.sql`, `infra/clickhouse/init/13-g42-insight.sql`, blok `efn_insight` di
  `infra/clickhouse/clickhouse-init.sh`, compose/Traefik/PKI/secrets/check-encryption/deploy/promtail/alert, runbook `docs/runbooks/insight-web.md`.
- Acuan: CLAUDE.md, ADR-041 (Proposed), `docs/04` §26 (termasuk §26.13 C1–C7), risk register `docs/01` §4.3–4.4.
- Nomor temuan: lanjut dari SEC-189.

## Ringkasan eksekutif

Tidak ada temuan P0/P1. Rantai SSO (token `eit1` HMAC, sekali pakai, lewat POST, cek Origin), cookie `__Host-eis`, CSP nonce,
katalog query berparameter, dan row policy ClickHouse bertanda tangan sudah benar dan berlapis. Kebocoran data lintas PT lewat
input browser tidak ditemukan. Sisa risiko utama ada pada **ketersediaan**: kuota ClickHouse dan semaphore dipakai bersama tanpa batas
per pengguna (SEC-189, P2). Tanda tangan cakupan juga perlu dikeraskan secara kriptografis (SEC-190/191, P3). Butir lainnya berupa
tata kelola sesi/grup, audit ekspor, dan dokumen yang tidak sinkron. Secara keamanan, aktivasi staging tidak terhalang. Production
tetap menunggu L-125: DPO (Q-INS3), Q-INS1, Q-INS4, Q-INS5, dan Q-INS7.

## Tabel temuan

| ID | Prioritas / Severity | Judul | Lokasi | Risiko |
|---|---|---|---|---|
| SEC-189 | P2 / Medium | Kuota ClickHouse & semaphore bersama tanpa batas per pengguna: satu staf (atau beban puncak) bisa mematikan dashboard semua orang hingga 1 jam | `infra/clickhouse/access/insight-access.sql:117-122`, `services/insight-web/lib/clickhouse.ts:28-32,55`, `app/api/export/[name]/route.ts:25-59`, `infra/traefik/dynamic/middlewares.yml:107-111` | R-S10, ketersediaan |
| SEC-190 | P3 / Low (PLAUSIBLE) | Tanda tangan cakupan `SHA256(key‖pesan)` rentan length-extension; setting tidak divalidasi format; user `efn_insight` `HOST ANY` | `services/insight-web/lib/scope.ts:30`, `infra/clickhouse/clickhouse-init.sh:204,281,285` | R-S05 (pola), multi-company |
| SEC-191 | P3 / Low (PLAUSIBLE) | Literal `insight_scope_key` di DDL row policy tercatat di `system.query_log`/log server ClickHouse | `infra/clickhouse/clickhouse-init.sh:281-286` (pola sama `:255-262` text2sql) | R-S01 |
| SEC-192 | P3 / Low | Sesi stateless 8 jam: logout tidak mencabut cookie; pencabutan grup/PT/nonaktif user baru berlaku ≤ 8 jam | `services/insight-web/lib/session.ts:26,81-89`, `app/logout/route.ts:7-15` | R-S16, R-L01 |
| SEC-193 | P3 / Low | `group_insight_all_brands` belum masuk daftar grup sensitif `efn_role` (bisa ikut sinkron jabatan, tidak disorot di review akses) | `services/odoo/addons/efn_insight/security/efn_insight_security.xml:22-26`, `efn_role/models/role_policy.py:26-34` | R-S16 |
| SEC-194 | P3 / Low | Log ekspor CSV tanpa cakupan/filter; tidak ada alert lonjakan `bad_sig`/`replay`/`bad_origin` | `app/api/export/[name]/route.ts:51`, `lib/sso-handler.ts:99,123`, `infra/observability/alert-rules.yml:38-63` | R-L01, R-S10 |
| SEC-195 | Info | Dokumen tidak sinkron: `/sso` saat kill switch = 404 teks (kontrak/runbook: 503); runbook §8 menyebut kunci kembar tidak dicek padahal sudah | `lib/sso-handler.ts:82`, `docs/04` §26.4, `docs/runbooks/insight-web.md:183,206-207`, `infra/compose/docker-compose.yml:1672` | — |
| SEC-196 | Info | Hardening kecil: `/_next/image` aktif & di luar gerbang sesi (tidak dipakai); jendela grant-sebelum-policy di init; retensi `ir.logging` belum ada mekanisme | `next.config.mjs:22-37`, `proxy.ts:70`, `insight-access.sql:115` vs `clickhouse-init.sh:218-222,283-298`, `efn_insight/README.md:55-58` | R-L01 |

Pemetaan prioritas: P0 = blokir rilis apa pun, P1 = blokir production, P2 = perbaiki sebelum production/aktivasi luas, P3 = backlog hardening.

---

## Detail temuan

### SEC-189 (P2 / Medium): kuota & semaphore bersama tanpa batas per pengguna

**Fakta kode.**
- Semua staf memakai satu user ClickHouse `efn_insight`. Kuota `insight_quota` bertipe `KEYED BY user_name` (plafon service):
  `queries = 20000`, `errors = 2000` per jam (`insight-access.sql:119-122`).
- Satu halaman memicu 4 query cangkang (`lib/shell-data.ts:34-39`) ditambah 5–9 query halaman, sekitar 13 query per tampilan. Satu proses punya satu semaphore
  12 slot dengan antrean 200 (`lib/clickhouse.ts:32`, `lib/semaphore.ts:13`). Ekspor CSV (≤ 200 000 baris, ≤ 30 dtk) berbagi
  semaphore yang sama dengan halaman.
- Satu-satunya pembatas laju adalah Traefik `ratelimit-insight` 20 rps (burst 60) per IP (`middlewares.yml:107-111`). Tidak ada batas per `sub`
  di aplikasi.

**Skenario.**
1. Insider (staf sah) menjalankan loop `curl` dengan cookie miliknya terhadap `/api/export/<tabel>?from=…` (rentang 366 hari)
   atau me-reload halaman. Pada 20 rps, kira-kira 20 000 query habis dalam beberapa menit. Akibatnya ClickHouse menolak semua query `efn_insight`
   sampai jendela kuota bergulir (≤ 1 jam). Semua manajer melihat kartu galat. Ekspor berat juga memonopoli 12 slot sehingga halaman
   pengguna lain antre lalu timeout setelah 20 dtk (`QUEUE_WAIT_MS`).
2. (PLAUSIBLE, perlu diukur) Beban sah di jam sibuk juga bisa menembus 20 000/jam: ±1 500 tampilan halaman per jam untuk 6 PT.
   Galat timeout ikut menghabiskan kuota `errors = 2000`.

Kerahasiaan tidak terdampak. Dampaknya hanya ketersediaan dashboard (bukan jalur transaksi). Tetap P2 karena pemicunya sepele dan
dampaknya mengenai semua pengguna.

**Rekomendasi.**
- Batas per `sub` (memori, replika 1) di `chQuery`/route: mis. 300 query per 5 menit dan **ekspor 5 per 10 menit**. Lewat batas →
  429 tanpa detail. Catat `rate_limited_user`.
- Semaphore **terpisah untuk ekspor** (mis. 2–3 slot) agar halaman tidak antre di belakang CSV.
- Ukur biaya halaman nyata di staging, lalu tetapkan kuota (mis. `queries` 60 000/jam) dan pasang alert `system.quotas_usage` > 80 %.
  Untuk jangka panjang: `KEYED BY client_key` + `quota_key` = hash `sub` (sudah dicatat sebagai gelombang berikut di SQL).

### SEC-190 (P3 / Low, PLAUSIBLE): tanda tangan cakupan rentan length-extension

**Fakta kode.** `SQL_efn_sig = hex(SHA256(key + "|" + cids + "|" + codes))` (`scope.ts:30`). Row policy menghitung ulang
`SHA256(concat(key,'|',cids,'|',codes))` dan menerapkan `has(splitByChar(',', SQL_efn_codes), company_code)`
(`clickhouse-init.sh:257,281,285`). Konstruksi `H(key‖m)` dengan SHA-256 memungkinkan length-extension: dengan satu pasangan
(pesan, sig) yang sah, `sig'` untuk `m‖padding‖",WTZ"` bisa dihitung tanpa kunci. `splitByChar(',')` menghasilkan
`['PB\x80\x00…', 'WTZ']`, sehingga `has(…, 'WTZ')` bernilai benar. Isi setting juga tidak divalidasi formatnya.

**Skenario.** Pelaku yang memegang password `efn_insight` **tetapi tidak memegang** `insight_scope_key` mengambil satu sig sah, misalnya dari
`system.query_log` kolom `Settings` (lihat SEC-191) atau dari trafik query miliknya sendiri. Ia lalu mengirim query `SETTINGS SQL_efn_codes='PB<pad>,WTZ',
SQL_efn_sig='<ext>'` dan membaca data PT lain. User dibuat `HOST ANY` (`clickhouse-init.sh:204`), jadi login bisa dari container mana pun di
network `data`/`insight-data`. Prasyaratnya berat (password hanya di-mount ke insight-web, dan kompromi insight-web sudah memberi kunci, C7),
jadi severity Low. Polanya diwarisi dari text2sql §23.3. Pemrosesan byte `\x80`/`\x00` di setting kustom lewat HTTP belum diuji,
sehingga ditandai PLAUSIBLE.

**Rekomendasi** (murah, tanpa mengubah kontrak token):
- Tambahkan validasi format di `USING`: `match(getSetting('SQL_efn_codes'), '^[A-Z]{2,3}(,[A-Z]{2,3})*$') AND
  match(getSetting('SQL_efn_cids'), '^([0-9]+(,[0-9]+)*)?$')`. Byte padding langsung gagal.
- Ganti ke konstruksi tanpa length-extension yang bisa dihitung di ClickHouse 24.8, mis.
  `SHA256(concat(key, '|', cids, '|', codes, '|', key))` atau bentuk bersarang ala HMAC. Perbarui `scope.ts` dan vektor test bersama.
  Terapkan juga ke `efn_t2s_scope`.
- `CREATE USER efn_insight … HOST IP '172.30.10.0/24'` (subnet `insight-data`). Saat K25-02 lintas mesin, isi dengan subnet WireGuard data.

### SEC-191 (P3 / Low, PLAUSIBLE): kunci cakupan tercatat di query_log / log ClickHouse

**Fakta kode.** `CREATE ROW POLICY … concat('<insight_scope_key>', …)` dikirim lewat `clickhouse-client` (`clickhouse-init.sh:281-286`).
ClickHouse hanya menyamarkan kredensial di `CREATE/ALTER USER` dan fungsi tabel tertentu, **bukan** literal di ekspresi row policy.
`log_queries` aktif secara default. Konfigurasi logger tidak di-override (`config.d` hanya tls/prometheus/encrypted-storage), jadi level bawaan
image ikut berlaku. Di 24.8, `select_from_system_db_requires_grant` bawaannya `false`, sehingga user lain (mis. analis Metabase dengan
native query) mungkin bisa `SELECT query FROM system.query_log WHERE query LIKE '%efn_insight_scope%'`. Tingkat akses ini belum
diverifikasi, sehingga ditandai PLAUSIBLE.

**Dampak.** Kunci saja tidak cukup (tetap butuh password `efn_insight`). Namun ini melemahkan lapis kedua C7 dan menjadi bahan SEC-190.
Pola yang sama berlaku untuk `text2sql_scope_key` (`:255-262`).

**Rekomendasi.** Awali blok row policy dengan `SET log_queries = 0;` (dalam sesi `--multiquery` yang sama). Setel
`access_control_improvements.select_from_system_db_requires_grant = true` di `config.d`, lalu beri `SELECT ON system.*` hanya ke admin/
exporter. Pastikan logger ClickHouse tidak di level `trace`/`debug` di prod atau tidak dikirim ke Loki. Setelah perbaikan diterapkan, rotasi
`insight_scope_key` dan `text2sql_scope_key` (runbook §4.3) karena entri lama di `query_log` mungkin sudah memuat kuncinya.

### SEC-192 (P3 / Low): sesi stateless 8 jam, logout tidak mencabut

**Fakta kode.** Cookie `eis1` berlaku 28 800 dtk (`session.ts:26`) tanpa status di server. `POST /logout` hanya mengirim `Max-Age=0`
(`logout/route.ts:11-14`). Cakupan `codes` dibekukan di cookie. Hal ini sesuai L-119 dan diterima sebagai desain.

**Skenario.** (a) Staf yang dipindah dari PT WTZ ke PB atau dicabut grup `all_brands`-nya tetap melihat data lama ≤ 8 jam. (b) Cookie yang
tersalin (malware, perangkat bersama) tetap berlaku setelah korban menekan "Keluar". Satu-satunya pencabutan adalah rotasi
`insight_session_key`, yang mengeluarkan semua orang.

**Rekomendasi.** (1) Simpan daftar cabut di memori (replika 1): hash sig cookie dari `/logout` sampai `exp`. Biayanya kecil, dan logout menjadi
nyata. (2) Putuskan Q-INS5: usulan 4 jam untuk `user` dan **2 jam untuk `all_brands`**, atau idle timeout. (3) Cantumkan "≤ TTL sesi"
di SOP offboarding (HR → nonaktif Odoo → dampak Insight).

### SEC-193 (P3 / Low): grup "semua brand" belum diperlakukan sensitif

**Fakta kode.** `group_insight_all_brands` memberi keenam kode ESB (termasuk IMD/CUP yang belum menjadi company Odoo) tanpa
melihat `company_ids` (`efn_insight_security.xml:20-26`, `controllers/main.py:74-76`). Daftar `SENSITIVE_GROUP_XMLIDS`
(`efn_role/models/role_policy.py:26-34`) tidak memuat grup ini. Akibatnya peran yang memuatnya bisa diberi flag `sync_from_job`, dan HR yang
mengubah jabatan bisa memberikan akses lintas PT secara otomatis (SEC-154). Perubahan grup tetap tercatat `efn_audit` (R-S16).

**Rekomendasi.** Tambahkan `efn_insight.group_insight_all_brands` ke parameter `efn_role.sensitive_group_xmlids` lewat data
`efn_setup_role` (parameter ini hanya menambah, aman). Jawab Q-INS1 (4-eyes). Masukkan pemegang grup ini ke review akses bulanan.

### SEC-194 (P3 / Low): audit ekspor & alert penyalahgunaan SSO

**Fakta kode.** Ekspor mencatat `table, rows, sub` saja (`export/[name]/route.ts:51`), tanpa PT/rentang/filter. Kegagalan `/sso`
dicatat sebagai kode (`sso-handler.ts:99,123`), tetapi `alert-rules.yml` G42 hanya memuat health dan NTP.

**Dampak.** Investigasi kebocoran (UU PDP Pasal 46: notifikasi 3×24 jam) sulit menjawab "siapa mengekspor data PT mana dan
periode apa". Percobaan pemalsuan token (`bad_sig` beruntun berarti indikasi kunci bocor atau tebakan) dan pemakaian ulang token juga tidak terlihat.

**Rekomendasi.** Tambahkan `codes`, `pt`, `from`, `to`, dan jumlah filter cabang/produk ke log ekspor (tanpa nama). Pasang alert Loki, mis.
`sum(count_over_time({service="insight-web"} |= "\"event\":\"sso\"" |= "\"code\":\"bad_sig\"" [10m])) > 5` (warning) dan pola yang
sama untuk `replay`/`bad_origin`. Tetapkan retensi log Loki insight-web dalam keputusan Q-INS3.

### SEC-195 (Info): dokumen tidak sinkron

- `/sso` dengan `INSIGHT_ENABLED≠true` → `plain(404)` (`sso-handler.ts:82`). Kontrak §26.4, komentar compose `:1672`, dan
  runbook `:183` menyebut 503 "belum diaktifkan". Perilaku 404 tidak bermasalah dari sisi keamanan (lebih sedikit sinyal); samakan dokumennya.
- Runbook `:206-207` menyebut kunci kembar tidak diperiksa, padahal `config.ts:93-114` + `instrumentation-node.ts:15-20` sudah menolak
  start bila kunci lemah/kembar. Perbarui butir tersebut.

### SEC-196 (Info): hardening kecil

- `/_next/image` aktif secara default dan dikecualikan dari gerbang sesi (`proxy.ts:70`), padahal `next/image` tidak dipakai. Setel
  `images: { unoptimized: true }` di `next.config.mjs` untuk mengecilkan permukaan (CPU sharp / DoS).
- `GRANT SELECT ON efn_insight.*` (`insight-access.sql:115`) dijalankan sebelum loop row policy (`clickhouse-init.sh:283-286`). Untuk view
  **baru**, ada jendela beberapa detik tanpa policy. Risiko praktis nihil karena katalog image lama tidak merujuk view baru dan pemeriksaan fail-closed
  `:288-298` sudah ada. Opsional: pindahkan grant wildcard ke setelah loop policy.
- `ir.logging` tidak punya pembersihan otomatis, jadi retensi audit pembukaan saat ini tak terbatas sampai Q-INS3 diputuskan
  (`efn_insight/README.md:55-58`). Catat di inventaris PDP. Bila DPO menetapkan 400 hari, tambahkan cron GC bertarget `name='efn_insight'`.

---

## Penilaian area fokus (yang sudah benar)

| Area | Penilaian |
|---|---|
| Kriptografi token/cookie | HMAC-SHA256, `timingSafeEqual` / `hmac.compare_digest`, sig wajib 32 byte dengan encoding kanonik (`hmac.ts:28-33`). HMAC dihitung atas `payload_b64` yang diterima (tanpa re-serialisasi). Kunci = byte ASCII hex ≥ 64, divalidasi di Python, Node, dan shell. Tiga kunci wajib berbeda (boot fail-closed). Prefiks `eit1.`/`eis1.` ada di signing input sehingga kunci kembar pun tidak memungkinkan token dipakai sebagai cookie. Kunci `scope` ≠ `text2sql_scope_key` ditegakkan di init. Kunci dibangkitkan `openssl rand -hex 32`. |
| Validasi klaim | Tepat 11/7 kunci, tipe ketat, `codes`/`cids` unik dan urut naik, `roles` tunggal, `cids ≥ 1` untuk `user`, nama ≤ 64 code point dengan karakter kontrol dibuang di kedua sisi. |
| Replay & jti (C5) | `has` lalu `add` sinkron tanpa `await` di antaranya (tidak ada race). TTL cache 120 dtk lebih panjang dari jendela terburuk `exp` (iat+30+60). Jti hanya disimpan setelah sig sah (tidak bisa dibanjiri). Risiko restart C5 **memadai** untuk diterima: token hanya ada di body POST, halaman `no-store` (tanpa bfcache/riwayat), umur 60 dtk, dan pemakaian ulang hanya memberi identitas pemilik token sendiri. |
| Login CSRF / C1 / fixation | `Origin` wajib dan harus sama persis dengan origin Odoo (null/kosong ditolak). Halaman form Odoo `strict-origin` (controller + meta + Traefik). Cookie baru diterbitkan per login. Prefiks `__Host-` mencegah cookie tossing dari subdomain saudara (bi./ops./status.). |
| Cookie | `__Host-eis; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=28800`, tanpa `Domain`. Logout hanya untuk permintaan same-origin. |
| CSP | insight-web: nonce per request + `'strict-dynamic'`, tanpa `unsafe-inline`/`unsafe-eval` untuk skrip di prod, `theme-init.js` ber-nonce, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`. Satu-satunya pelonggaran `style-src-attr 'unsafe-inline'` sudah tercatat dan wajar (atribut style tidak mengeksekusi skrip). Odoo: `default-src 'none'; script-src 'self'; form-action <origin insight>`, tanpa inline script. |
| XSS | React meng-escape semua nama user/cabang/menu. Tooltip ECharts memakai `escapeHtml` (termasuk `'`). Tidak ada `dangerouslySetInnerHTML`/`eval`/server action. QWeb hanya `t-att-*`/`t-out`. Halaman galat `/sso` meng-escape semua nilai. |
| CSV (C3) | Prefiks `'` untuk `= + - @ TAB CR`, quoting RFC 4180, nama file dari tanggal tervalidasi + konstanta. |
| Injeksi ClickHouse | SQL hanya dari konstanta katalog. Parameter bertipe `{x:Type}` lewat `param_*`. Backslash/kontrol ditolak, `'` di-escape pada `Array(String)` dan ditolak pada `String`. Parameter di luar katalog diabaikan. Password lewat header, bukan URL. |
| Lintas PT | Scope dihitung dari cookie terverifikasi (`page-ctx.ts`, `loaders.ts:40`), filter `pt` dipotong ke kode sesi, dan row policy (codes + sig + `company_code != ''`, C4) menjadi lapis kedua. Akses `efn_dwh`/`efn_esb` langsung dicabut. Profil `readonly=2 CONST`, DDL mati. Pemeriksaan fail-closed memastikan setiap view punya policy. Definer view hanya mendapat tabel yang dibutuhkan. |
| Odoo controller | 403 tanpa grup. Kill switch diperiksa sebelum token dibuat. URL dan kunci divalidasi (https, tanpa path/query). `sudo` hanya untuk membaca `res.company` (kode/aktif). Cakupan `user` dari tabel relasi m2m dengan parameter SQL. Audit `ir.logging` tanpa token/nama/login. |
| Logging | Tidak ada jalur log untuk token/cookie (`log.ts` hanya skalar). Traefik access log membuang header (`defaultMode: drop`). Promtail meredaksi `eit1.`/`eis1.` dengan fixture test. |
| Jaringan & container | `insight-ingress`/`insight-data` `internal: true`. Traefik tidak satu network dengan ClickHouse. insight-web tanpa egress. Sidecar ghostunnel mTLS (CN `traefik`/`blackbox-exporter`). Next listen di loopback. `read_only`, `cap_drop: ALL`, `no-new-privileges`, non-root. Secret hanya di service yang tercantum, ditegakkan `check-encryption.sh`. `INSIGHT_ENABLED=false` di render prod dan butuh `EFN_ALLOW_FEATURE_ON` + Superadmin. |
| UU PDP | Token/cookie tanpa email/login/telepon (`sub` + nama tampilan ≤ 64). Mart tanpa data pelanggan/karyawan (L-110). Tidak ada transfer lintas negara (tanpa egress). Retensi menunggu Q-INS3 (SEC-196). Nama tampilan di cookie HttpOnly bisa diterima dengan catatan inventaris PDP (L-125). |
| Rate limit `/sso` | 10/menit per IP. IP diambil dari entri **paling kanan** XFF (ditambahkan Traefik, ghostunnel tidak mengubah header), jadi tidak bisa dipalsukan klien. Catatan operasional: satu NAT kantor = satu ember (sudah ada di runbook). |

## Yang tidak diverifikasi reviewer

- Perilaku byte non-ASCII di setting kustom ClickHouse lewat HTTP (SEC-190) dan hak baca `system.query_log` oleh user non-admin di 24.8
  (SEC-191). Keduanya perlu uji di container ClickHouse 24.8 oleh devsecops.
- Angka beban halaman nyata terhadap kuota (SEC-189): butuh pengukuran di staging.
- Test runtime tidak dijalankan ulang. Hasil 26 test Odoo, 168 Vitest, E2E 31/31, dan Trivy diterima dari laporan lead.
