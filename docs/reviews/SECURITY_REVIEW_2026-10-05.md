# Security & Compliance Review — Fase 0 (2026-10-05)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Cakupan: semua kode baru Fase 0 di `services/api-gateway`, `services/odoo/addons/efn_*`, `services/superapp-web`, `services/ai-ocr-worker`, `services/etl`, `services/text2sql`, `infra/**`, `scripts/**`, `.github/workflows/**`.
Metode: membaca kode + konfigurasi, menelusuri alur data end-to-end, memverifikasi setiap temuan terhadap kode sekitar. **Tidak ada yang dijalankan** (tidak ada runtime di mesin reviewer); temuan yang bergantung pada perilaku runtime pihak ketiga ditandai **PLAUSIBLE**.

## Ringkasan eksekutif

1. Inti kriptografi & logika aplikasi BFF **solid**: OTP (HMAC + HKDF, atomic attempt counter, sekali pakai), discount token (HS256 terkunci, terikat `order_ref`, single-use idempotent), webhook HMAC, upload (magic bytes + re-encode sharp), dan masking PII sudah benar. Tidak ada temuan Critical.
2. Risiko terbesar ada di **lapisan infrastruktur**: Odoo backend terbuka penuh ke internet (R-S03), semua service berbagi **kredensial superuser** PostgreSQL / root MinIO / satu user RabbitMQ, dan rate limit per IP di BFF kemungkinan besar menghitung **IP edge Cloudflare**, bukan IP pelanggan.
3. Guard Text-to-SQL punya **bypass konkret** (shadowing nama CTE lintas scope) yang membuat whitelist view bisa dilewati ke tabel dasar `efn_dwh`; lapisan kedua (grant ClickHouse) belum dipasang otomatis.
4. Kepatuhan UU PDP: consent lokasi baru di UI, **belum ada retensi otomatis, DSAR (akses/hapus), maupun catatan consent di server**; kebijakan privasi masih draft. Wajib sebelum go-live.
5. Total **19 temuan**: 0 Critical, 3 High, 8 Medium, 6 Low, 2 Info (+ catatan Info ringkas). Item High wajib diperbaiki sebelum staging publik.

Catatan koordinasi: ketidakcocokan integrasi (nama env, prefix path, wiring hash member ETL, argumen queue, grant read-only Postgres, eksposur `/metrics` di Traefik, phash) **sedang ditangani QA** dan tidak diulang di sini kecuali berdampak keamanan.

## Tabel temuan

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-01 | High | Seluruh backend Odoo dapat diakses dari internet (tanpa allowlist/VPN/MFA) | `infra/compose/docker-compose.yml:348-361`, `infra/traefik/dynamic/middlewares.yml:20-23` | R-S03 |
| SEC-02 | High | Kredensial privileged dipakai bersama semua service (PG superuser, MinIO root, satu user RabbitMQ) | `docker-compose.yml:166,333,393,540,579,649,898` / `419-420,654-655` / `244,398-401,644-645` | R-S01, R-S10, R-T08 |
| SEC-03 | High (PLAUSIBLE) | Rate limit OTP per IP & `otp_audit.ip` memakai IP edge Cloudflare, bukan IP klien | `services/api-gateway/src/config/app-config.ts:21,116-121`, `infra/traefik/traefik.yml:43-59`, `services/superapp-web/lib/bff.ts:50-52` | R-S04 |
| SEC-04 | Medium | Bypass guard Text-to-SQL via shadowing nama CTE lintas scope (+ `IN <tabel>`) | `services/text2sql/efn_text2sql/guard.py:211-230` | R-S05, R-T06 |
| SEC-05 | Medium | Redis tanpa autentikasi + `allkeys-lru` menyimpan state keamanan OTP/rate limit | `docker-compose.yml:213-235,397` | R-S04 |
| SEC-06 | Medium | OTP pumping & lockout tertarget: tanpa captcha/budget global, BFF publik menerima nomor internasional | `src/common/pii/pii.ts:9-19`, `src/auth/auth.service.ts:46-61`, `docker-compose.yml:443` | R-S04 |
| SEC-07 | Medium | OTP promo POS tidak terikat ke pelanggan order maupun nominal | `efn_pos_otp_gate/models/pos_config.py:75-142`, `src/pos/discount-token.service.ts:40-57` | R-S10, R-B (fraud promo) |
| SEC-08 | Medium | Consumer `loyalty.earn.requested` mempercayai publisher apa pun (mint poin hingga 1 juta/pesan) | `src/loyalty/loyalty-earn.consumer.ts:39-61`, `src/loyalty/dto/loyalty-earn.message.ts:33-59` | R-S10, R-B05 |
| SEC-09 | Medium | cAdvisor berjalan root dengan `docker.sock` + rootfs host (melewati desain socket-proxy) | `docker-compose.yml:864-889` | R-S01 |
| SEC-10 | Medium | Rantai deploy: action pihak ketiga belum di-pin di job pemegang semua secret prod; identitas cosign menerima ref apa pun; verify-by-tag lalu pull-by-tag | `.github/workflows/deploy.yml:42,64,72,78`, `scripts/deploy.sh:88-91` | R-S07, R-S08 |
| SEC-11 | Medium | UU PDP: belum ada retensi otomatis, DSAR (akses/hapus), catatan consent server-side | `infra/postgres/init/01-databases.sql:20-50`, `infra/minio/minio-init.sh`, `app/privasi/page.tsx` | R-L01 |
| SEC-12 | Low (PLAUSIBLE) | Gate OTP server-side Odoo bergantung pada flag `reward_id`/`is_reward_line` dari klien | `efn_pos_otp_gate/models/pos_order.py:73-82,113-131` | R-S10 |
| SEC-13 | Low | Open redirect setelah login via `?next=/%09/evil.com` | `services/superapp-web/lib/session.ts:51-55`, `app/masuk/LoginForm.tsx:90` | ASVS V5.1.5 |
| SEC-14 | Low | Endpoint server-to-server `/v1/pos/*` ikut terekspos di host publik `api.` | `docker-compose.yml:443`, `src/pos/pos.controller.ts:14-15` | R-S03 |
| SEC-15 | Low | Fallback password master Odoo `change-me` + render config via `sed` tanpa escape | `services/odoo/entrypoint-efn.sh:17,20-24` | R-S01, R-S03 |
| SEC-16 | Low | ETL menerima `otp_pepper` mentah (IKM) alih-alih pepper turunan; hash member mudah dibalik bila bocor | `services/etl/efn_etl/config.py:57,97`, `efn_etl/hashing.py:21-31` | R-L01 |
| SEC-17 | Low | Pembulatan lokasi hanya di browser; server menyimpan koordinat presisi tinggi bersama nomor HP | `src/market-scout/dto/report.dto.ts:20,33-40`, `market-scout.service.ts:120-133` | R-L01 |
| SEC-18 | Info (PLAUSIBLE) | Log slow-query PostgreSQL dapat memuat nomor HP sebagai parameter → Loki | `infra/compose/docker-compose.prod.yml:37`, `docker-compose.yml:203` | R-L01 |
| SEC-19 | Info | Market Scout (sharp, decode ≤50 MP) berbagi proses dengan gerbang OTP POS; tanpa batas konkurensi | `src/market-scout/image/image-processor.ts:16,27`, `docker-compose.prod.yml:63-67` | R-S06, R-T (availability) |

---

## Detail temuan

### SEC-01 — High — Seluruh backend Odoo dapat diakses dari internet
**Lokasi:** `infra/compose/docker-compose.yml:348-361` (router `odoo` = `Host(erp.<domain>)` tanpa `internal-only`), `infra/traefik/dynamic/middlewares.yml:20-23` (hanya mengganti path `/web/database/*`).
**Kondisi:** Router `odoo` meneruskan **semua** path (`/web/login`, `/web/session/authenticate`, `/jsonrpc`, `/xmlrpc/2/object`, `/web/dataset/call_kw`, …) ke Odoo, dengan rate limit 100 rps/IP (`ratelimit-default`). Router `odoo-ws` (`:357-361`) bahkan tanpa middleware sama sekali. Tidak ada MFA yang dipaksakan. Ini bertentangan dengan prinsip CLAUDE.md ("Odoo tidak pernah diakses langsung dari internet") dan mitigasi R-S03 ("path allowlist").
**Skenario:** penyerang melakukan credential stuffing ke `POST /web/session/authenticate` (JSON-RPC) memakai daftar email karyawan + password bocor, ~100 percobaan/detik per IP → satu akun kasir/akuntan yang lemah = akses penuh ERP multi-company (harga, stok, pricelist, data pelanggan semua PT). `/xmlrpc/2/common` `authenticate` juga tidak dibatasi.
**Catatan positif:** `list_db = False` + `dbfilter` + blok `/web/database/*` menutup database manager (fungsi `_check_db_management_enabled` Odoo juga menolak karena `list_db=False`).
**Rekomendasi:**
1. Default: tambahkan `internal-only@file` ke router `odoo` dan `odoo-ws`; toko/POS mengakses lewat WireGuard (sudah ada mesh `10.0.0.0/8`). Perhatikan `ipStrategy.depth: 1` hanya benar untuk trafik lewat Cloudflare — untuk akses VPN langsung gunakan `ipStrategy` tanpa depth (lihat juga SEC-03).
2. Jika POS harus via internet: path allowlist (`/pos/`, `/web/login`, `/web/assets/`, `/websocket`, `/web/webclient/`), blok `/xmlrpc`, `/jsonrpc`, `/web/database`, dan **wajibkan TOTP** (`auth_totp` CE + kebijakan wajib untuk group internal), rate limit ketat khusus `/web/login` & `/web/session/authenticate` (mis. 5/menit/IP), plus fail2ban/CrowdSec dari log Odoo.
3. Pasang `secure-headers` + rate limit di router `odoo-ws`.

### SEC-02 — High — Kredensial privileged dipakai bersama semua service
**Lokasi:**
- PostgreSQL: `POSTGRES_USER: efn` (`docker-compose.yml:166`) = **superuser** cluster; dipakai Odoo (`:333`), api-gateway (`:393`), metabase (`:540`), etl (`:579`), ocr-worker (`:649`), postgres-exporter (`:898`).
- MinIO **root** (`efnadmin`) dipakai api-gateway (`:419-420`) dan ocr-worker (`:654-655`).
- RabbitMQ: satu user `efn` (default user = tag `administrator`) dipakai BFF (`:398-401`) dan ocr-worker (`:644-645`).
**Skenario:** ocr-worker memproses foto tak tepercaya dengan Pillow/Tesseract/imagehash (`efn_ocr/imaging.py`). Satu bug parsing gambar (kelas CVE yang rutin muncul di libtiff/libwebp/tesseract) → eksekusi kode di worker →
(a) `COPY (SELECT '') TO PROGRAM 'sh -c …'` dengan superuser → shell di container postgres → baca `/run/secrets/walg_libsodium_key` + `minio_backup_password`;
(b) dengan root MinIO hapus bucket `pg-backup` dan `market-scout` (tanpa versioning/object lock) → ransomware;
(c) `UPDATE` langsung tabel Odoo (harga/stok semua PT) dan `INSERT platform.loyalty_ledger`;
(d) publish `loyalty.earn.requested` sendiri (lihat SEC-08).
Metabase (UI internal) memegang password superuser yang sama → pengguna BI berhak admin dapat membuat koneksi ke DB mana pun.
**Rekomendasi (urut prioritas):**
1. PostgreSQL: role per service tanpa `SUPERUSER`/`CREATEROLE`: `odoo` (owner DB `efn_odoo`, `CREATEDB` hanya bila perlu), `bff` (owner skema `platform`), `ocr_worker` (hanya `SELECT, UPDATE(status, reason, extracted, confidence, image_phash, updated_at)` di `price_hunter_report`), `etl`/`efn_readonly` (sedang ditangani QA), `metabase` (owner DB `metabase` saja), `pg_monitor` untuk exporter. Superuser hanya untuk entrypoint/backup via socket lokal.
2. MinIO: user `bff-writer` (`s3:PutObject`, `s3:DeleteObject` pada `market-scout/reports/*`), user `ocr-reader` (`s3:GetObject` pada `market-scout/reports/*`); aktifkan versioning + object lock (mode governance) di `pg-backup`; root hanya untuk `minio-init`.
3. RabbitMQ: user per service dengan permission regex: BFF `configure/write/read` pada queue miliknya; ocr-worker `read` `ocr.requested`, `write` hanya ke default exchange untuk `loyalty.earn.requested`/`notify.push` (lihat juga SEC-08). Hapus tag `administrator` dari user aplikasi.

### SEC-03 — High (PLAUSIBLE) — Rate limit per IP memakai IP edge Cloudflare
**Lokasi:** `services/api-gateway/src/config/app-config.ts:21` (`DEFAULT_TRUSTED_PROXIES` hanya loopback/RFC1918/ULA), `src/main.ts:18`, `infra/traefik/traefik.yml:43-59` (Cloudflare dipercaya di Traefik), `services/superapp-web/lib/bff.ts:50-52` (Next.js meneruskan `X-Forwarded-For` apa adanya).
**Analisis:** Alur produksi: klien → Cloudflare (XFF=`klien`) → Traefik (percaya CF, lalu reverse proxy Go **menambahkan** IP koneksi = IP edge CF → XFF=`klien, CF`) → Next.js/BFF. Fastify `trustProxy` berjalan dari kanan dan berhenti di alamat pertama yang tidak dipercaya: `CF` (publik) → `req.ip = IP edge Cloudflare`.
**Dampak:**
- `otp:login:ip:<ip>` (20/15 menit) dan `otp:login:verify-ip:<ip>` (60/15 menit) menjadi **kuota bersama per node edge CF**. Penyerang cukup mengirim ~20 request OTP lewat PoP Jakarta untuk mengunci login OTP bagi pengguna lain yang kebetulan lewat edge yang sama (DoS login trivial), dan lalu lintas organik saat promo akan terkena 429 massal.
- `platform.otp_audit.ip` berisi IP Cloudflare → audit forensik tidak berguna.
- Bila origin ternyata dapat diakses langsung tanpa CF, perilakunya benar (Traefik membuang XFF dari sumber tak tepercaya), sehingga dua jalur menghasilkan semantik berbeda.
**Status:** PLAUSIBLE — bergantung pada Cloudflare benar-benar di depan (komentar `traefik.yml:42`) dan Traefik memakai proxy standar yang menambahkan RemoteAddr ke XFF. Verifikasi di staging: log `req.ip` saat request lewat CF.
**Rekomendasi:** tambahkan rentang IP Cloudflare ke `TRUSTED_PROXIES` BFF (aman karena Traefik sudah membuang XFF dari sumber non-CF), **atau** pada Traefik set header `X-Real-IP` dari `CF-Connecting-IP` (plugin realip) lalu BFF memakai header itu hanya bila socket dari subnet Traefik/Next. Tambahkan test integrasi: XFF palsu langsung ke origin tidak mengubah `req.ip`; XFF via CF menghasilkan IP klien. Firewall origin: hanya izinkan 443 dari rentang Cloudflare (menutup bypass WAF).

### SEC-04 — Medium — Bypass guard Text-to-SQL via shadowing nama CTE lintas scope
**Lokasi:** `services/text2sql/efn_text2sql/guard.py:212` (`cte_names` dikumpulkan dari **seluruh** pohon), `:221-222` (tabel tanpa database yang namanya sama dengan CTE di mana pun dilewati), `:227-229`.
**Eksploit konkret (lolos guard):**
```sql
SELECT company_id, customer_id, order_ref, price_unit
FROM fact_sales_line
WHERE company_id IN (WITH fact_sales_line AS (SELECT 1 AS company_id) SELECT company_id FROM fact_sales_line)
```
Guard: `cte_names = {fact_sales_line}`; kedua referensi `fact_sales_line` tanpa database → dilewati; `used = []` tetapi `cte_names` tidak kosong → lolos. Di ClickHouse, CTE di dalam subquery **tidak** terlihat oleh query luar, sehingga `FROM fact_sales_line` luar membaca tabel dasar `efn_dwh.fact_sales_line` (memuat `customer_id`, `order_ref`, `product_name`, `price_unit`). Pola yang sama berlaku untuk `competitor_prices` (`user_hash`) dan `loyalty_events` (`member_hash`).
Vektor kedua (PLAUSIBLE, tergantung parser sqlglot): sisi kanan `IN` berupa nama tabel (`x IN efn_dwh.fact_sales_line`, sintaks valid ClickHouse) diparse sqlglot sebagai `Column`, bukan `Table`, sehingga tidak masuk `_check_tables`.
**Yang sudah tertutup (diverifikasi):** table function di FROM/JOIN (`remoteSecure`, `clusterAllReplicas`, `input`, `url`, `s3`, `executable`, `view`) ditolak oleh `_check_sources` (sumber harus `Table` dengan `Identifier`) dan daftar prefix; `dictGet*`, `joinGet`, `file()`, `getSetting` diblok; `SETTINGS`/`FORMAT` di SELECT mana pun (termasuk CTE/subquery) ditolak lewat `FORBIDDEN_CLAUSES`; multi-statement ditolak; database lain (`system.*`) ditolak karena qualifier diperiksa; kapitalisasi/quoted identifier tidak membuka tabel baru (nama tabel ClickHouse case-sensitive, `"system.users"` jadi nama tabel `efn_dwh.system.users` yang ditolak).
**Dampak:** whitelist view dilewati untuk semua tabel dasar di database default. Lapisan kedua (`infra/clickhouse/access/text2sql-access.sql`, grant kolom tanpa `user_hash`/`member_hash`) akan menahan kolom identitas — **tetapi** skrip itu tidak dijalankan oleh `scripts/deploy.sh` dan belum ada service runtime yang menentukan user ClickHouse. Bila Text-to-SQL kelak memakai user `efn` (admin, `CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT=1`), dampaknya naik ke **High**. Selain itu `customer_id`, `order_ref` di `fact_sales_line` tidak di-grant sehingga aman, tetapi `geo_lat/lng` & status `rejected/flagged` di `competitor_prices` terbaca.
**Rekomendasi:**
1. Validasi berbasis scope: gunakan `sqlglot.optimizer.scope.traverse_scope(root)` dan untuk tiap `scope.tables` cek apakah nama tersebut ada di `scope.cte_sources` **scope itu sendiri atau leluhurnya**; lebih sederhana: **tolak CTE yang namanya bentrok** dengan tabel mana pun di `efn_dwh` atau di whitelist, dan tolak referensi tabel tanpa database yang bukan CTE yang didefinisikan di scope leluhurnya.
2. Tolak `exp.In` yang `field`-nya bukan `Subquery`/tuple literal.
3. Tambahkan test regresi untuk kedua payload di atas ke `tests/test_guard.py`.
4. Jadikan `text2sql-access.sql` + `CREATE USER efn_text2sql` langkah wajib deploy (idempoten) dan pastikan service Text-to-SQL hanya memakai user itu; pertimbangkan `SQL SECURITY DEFINER` pada view lalu cabut grant kolom tabel dasar.

### SEC-05 — Medium — Redis tanpa autentikasi + `allkeys-lru` untuk state keamanan
**Lokasi:** `docker-compose.yml:217` (`redis-server` tanpa `--requirepass`/ACL; `--maxmemory-policy allkeys-lru`), `:397` (`REDIS_URL` tanpa kredensial). Redis ada di network `data` bersama 13 container (termasuk metabase, clickhouse, ocr-worker, etl).
**Skenario:**
- Container mana pun di network `data` yang terkompromi: `HSET otp:<id> a 0` berulang → batas 3 percobaan hilang → brute force 10^6 kode dalam TTL 180 s lewat API; `DEL rl:otp:*` → semua rate limit reset.
- ETL dan BFF memegang `otp_pepper` (lihat SEC-16): `HGETALL otp:<id>` + HKDF `efn:otp_code:v1` → kode OTP aktif apa pun dipulihkan offline dalam milidetik → takeover akun login.
- `allkeys-lru`: di bawah tekanan memori, key rate limit (`rl:*`) dan marker dapat dibuang lebih dulu → limit terlewati diam-diam. (Marker `dtok:used:*` yang terbuang tidak berbahaya karena token terikat `order_ref` unik.)
**Rekomendasi:** aktifkan ACL Redis (user `bff` dengan `~otp:* ~rl:* ~dtok:* +@all -@dangerous`, user `exporter` read-only `+info`), secret `redis_password` via `REDIS_PASSWORD_FILE` (sudah didukung `app-config.ts:264-266`); pindahkan Redis ke network khusus `cache` yang hanya berisi api-gateway & redis-exporter; ganti policy ke `volatile-lru` (semua key keamanan sudah ber-TTL) atau `noeviction` + alert memori.

### SEC-06 — Medium — OTP pumping & lockout tertarget
**Lokasi:** `services/api-gateway/src/common/pii/pii.ts:9-19` (BFF menerima MSISDN negara mana pun 8–15 digit), `src/auth/dto/auth.dto.ts:15-20`, `src/auth/auth.service.ts:46-61`, router publik `api.` (`docker-compose.yml:443`). Next.js membatasi ke nomor Indonesia (`lib/phone.ts`), tetapi BFF dapat dipanggil langsung di `https://api.<domain>/v1/auth/otp/request`.
**Skenario:**
1. *WhatsApp authentication pumping:* skrip memanggil `/v1/auth/otp/request` langsung ke BFF dengan ribuan nomor internasional berbeda (tarif autentikasi internasional Meta lebih mahal). Batas yang ada hanya per IP (20/15 menit — dan lihat SEC-03) dan per nomor (3/15 menit); tidak ada budget global, captcha, maupun allowlist negara → tagihan WA membengkak.
2. *Lockout tertarget:* penyerang mengirim 3 request/15 menit untuk nomor korban → korban tidak pernah bisa login dan menerima spam OTP.
3. Respons `503 otp_delivery_failed` vs `202` membedakan nomor yang ditolak sinkron oleh Graph API (enumerasi lemah; Info).
**Rekomendasi:** batasi `normalizeMsisdn` login ke prefix `62` (sejalan dengan Odoo `normalize_member_ref`); budget global OTP per menit + alert (`efn_otp_request_total` sudah ada); captcha/Turnstile setelah request ke-2 per IP/perangkat (R-S04 menyebut captcha); jangan routing `/v1/auth/*` publik di Traefik bila semua klien lewat Next.js (atau batasi dengan header rahasia Next→BFF); per-nomor gunakan backoff progresif alih-alih kuota keras yang bisa dipakai untuk lockout.

### SEC-07 — Medium — OTP promo POS tidak terikat ke pelanggan order maupun nominal
**Lokasi:** `services/odoo/addons/efn_pos_otp_gate/models/pos_config.py:75-108` (nomor diketik bebas oleh kasir, tidak dibandingkan dengan `order.partner_id`), `services/api-gateway/src/pos/discount-token.service.ts:40-57` (klaim token: `order_ref`, `member_hash`, `company_code` — tanpa `amount`/`partner`).
**Skenario:** kasir memasukkan nomor HP **miliknya sendiri/kaki tangan**, menerima OTP, lalu menerapkan promo member pada order pelanggan non-member (atau menjual promo ke orang lain). Batas hanya 3 OTP/15 menit per nomor → ±288 promo/hari per nomor. Nominal di `efn_otp_request` (`amount`) tidak disimpan di token sehingga kasir dapat meminta OTP pada order kecil lalu menambah item. Gate secara teknis lolos karena OTP memang valid untuk order uuid tersebut.
**Rekomendasi:** ikat OTP ke member yang tercatat pada order (`partner_id.phone` ternormalisasi = `member_ref`, atau ambil nomor dari partner, bukan input bebas); simpan `amount` (dan `partner_id`/hash) di challenge & klaim token, lalu saat consume bandingkan dengan total akhir order (toleransi kecil); batasi jumlah promo ber-OTP per `member_hash` per hari di BFF; laporan anomali "nomor yang sama dipakai di banyak order/kasir" (R-S10).

### SEC-08 — Medium — `loyalty.earn.requested` mempercayai publisher apa pun
**Lokasi:** `services/api-gateway/src/loyalty/loyalty-earn.consumer.ts:39-61`, `src/loyalty/dto/loyalty-earn.message.ts:33-59` (`points` ≤ 1.000.000, `source` & `idempotency_key` bebas).
**Skenario:** siapa pun yang dapat publish ke default exchange (ocr-worker, atau container mana pun yang membaca `rabbitmq_password` — satu user untuk semua, SEC-02) mengirim `{ "schema_version":1, "idempotency_key":"x-<uuid>", "member_ref":"62812…", "points":1000000, "source":"price_hunter" }` berulang dengan key unik → saldo poin tak terbatas, liabilitas dibebankan ke `EFN` (default `company_code`).
**Rekomendasi:** validasi semantik per `source` di BFF: untuk `price_hunter`, `idempotency_key` wajib `price_hunter:<report_id>`, report harus berstatus `valid` di `platform.price_hunter_report` dengan `user_id = member_ref`, dan `points` = konfigurasi (`points_per_valid_report`); allowlist `source`; plafon poin per member per hari; pisahkan user RabbitMQ per publisher (SEC-02) dan, bila perlu, tanda tangan HMAC payload antar-service.

### SEC-09 — Medium — cAdvisor root dengan `docker.sock` dan rootfs host
**Lokasi:** `docker-compose.yml:864-889`: `/var/run:/var/run:ro` (memuat `/var/run/docker.sock` — flag `:ro` tidak membatasi API Docker), `/:/rootfs:ro`, `/var/lib/docker:ro`, tanpa `cap_drop`, user root, port UI 8080 tanpa autentikasi di network `internal`.
**Skenario:** desain `docker-socket-proxy` (Traefik/Promtail) dilewati: kompromi cAdvisor (atau SSRF ke API-nya dari container internal) = akses penuh Docker API (`POST /containers/create` dengan bind `/`) → root host. Tanpa eksploit pun, proses root di cAdvisor membaca `/rootfs/opt/efn/secrets/*.txt` (izin 0700 tidak berlaku untuk root) dan semua volume.
**Rekomendasi:** hapus mount `/var/run` (cAdvisor dapat memakai `--docker=tcp://docker-socket-proxy:2375` atau cukup cgroup tanpa metadata Docker), batasi mount ke `/sys/fs/cgroup`, `/var/lib/docker/:ro` hanya bila perlu; tambahkan `cap_drop: [ALL]`, `user`, dan letakkan di network `monitoring` terpisah. Dengan begitu catatan izin secret 0644-di-dir-0700 (`scripts/gen-secrets.sh:6-8`) tetap valid: hanya proses root host/container yang bisa membaca.

### SEC-10 — Medium — Rantai deploy (supply chain & provenance)
**Lokasi:** `.github/workflows/deploy.yml:64` (`sigstore/cosign-installer@v3`), `:78` (`webfactory/ssh-agent@v0.9.0`), `:62` (`actions/checkout@v4`) — tag, bukan SHA; `:72` identitas cosign `…/build-push.yml@.*`; `:42` `${{ inputs.image_tag }}` diinterpolasi langsung di `run`; `scripts/deploy.sh:88-91` pull & up berdasarkan **tag**.
**Skenario:**
1. Tag action pihak ketiga dibajak (pola insiden 2025) → action berjalan di job yang sama dengan `DEPLOY_SSH_KEY` dan seluruh secret prod; cosign-installer yang jahat cukup menaruh wrapper `ssh` di `PATH` untuk menyalin stdin (berisi semua `export SECRET=…`) → kompromi total prod (user deploy di group `docker` = root host).
2. `build-push.yml` dapat dipicu `workflow_dispatch` dari **branch apa pun**; regexp `@.*` menerima sertifikat dari ref tersebut → image dari branch yang tidak direview lolos `cosign verify`.
3. Verifikasi dilakukan pada tag, lalu host mem-pull tag lagi: pemegang `packages:write` dapat memindahkan tag di antara dua langkah (TOCTOU).
4. `inputs.image_tag` berisi `"; curl …` → injeksi perintah di runner (butuh hak write repo; Low).
**Rekomendasi:** pin semua `uses:` ke SHA 40 karakter (`pinact`) — **blocker sebelum prod**; ganti `webfactory/ssh-agent` dengan `ssh-agent`/`ssh -i` bawaan; identitas cosign `…/build-push.yml@refs/heads/main` dan `@refs/tags/v*` (`--certificate-identity` per pola); resolve digest saat verify (`cosign verify` → `crane digest`) lalu kirim `IMAGE_DIGEST` ke host dan gunakan `image: …@sha256:…`; teruskan input lewat `env:` lalu validasi regex `^[a-z0-9._-]{1,64}$`.

### SEC-11 — Medium — UU PDP: retensi, DSAR, dan bukti consent belum ada
**Lokasi & bukti:**
- `platform.price_hunter_report` menyimpan `user_id` = **nomor HP plaintext** + `geo_lat/geo_lng` + foto (`infra/postgres/init/01-databases.sql:20-36`); tidak ada job purge. Kebijakan privasi menjanjikan "foto 12 bulan" (`app/privasi/page.tsx:58`) tetapi bucket `market-scout` tanpa lifecycle rule (`infra/minio/minio-init.sh:24-25`).
- `platform.otp_audit.ip` (`01-databases.sql:41-49`), `platform.outbox.payload` (memuat `user_id`), `processed_message` — hanya "rekomendasi purge" di komentar (`02-platform-loyalty.sql:35,47`).
- ClickHouse tanpa `TTL` (`infra/clickhouse/init/01-schema.sql`).
- Tidak ada endpoint/proses hak akses & hapus (DSAR); ledger berbasis hash sehingga penghapusan perlu prosedur (anonymisasi `price_hunter_report.user_id`, hapus objek MinIO, re-hash/hapus di ClickHouse).
- Consent lokasi hanya checkbox di UI (`PriceReportForm.tsx:266-270`); server tidak mencatat versi teks consent, waktu, dan tujuan — sulit membuktikan dasar pemrosesan (Pasal 20–22 UU PDP).
- Kebijakan privasi masih DRAFT, DPO & daftar pemroses (Meta/WhatsApp — transfer lintas negara, Pasal 56) belum ditetapkan; belum ada runbook notifikasi kegagalan pelindungan data 3×24 jam (Pasal 46).
**Rekomendasi:** cron/`pg_cron` purge sesuai matriks retensi yang disetujui legal (mis. otp_audit 90 hari, outbox published 7 hari, processed_message 30 hari, foto 12 bulan → lifecycle MinIO `mc ilm rule add --expire-days 365`), ClickHouse `TTL`; tabel `platform.consent (member_hash, purpose, version, granted_at, withdrawn_at)` diisi BFF saat lat/lng dikirim; endpoint `GET /v1/me/data` + `DELETE /v1/me` dengan alur anonimisasi lintas store; runbook insiden 72 jam; finalisasi privacy notice & DPO.

### SEC-12 — Low (PLAUSIBLE) — Gate server Odoo bergantung pada flag reward dari klien
**Lokasi:** `efn_pos_otp_gate/models/pos_order.py:73-82` (baris diidentifikasi dari `is_reward_line` + `reward_id.program_id.efn_requires_otp`), `:113-131`.
**Skenario:** klien POS yang dimodifikasi (DevTools di browser kasir, R-S12) mengirim baris diskon dengan produk diskon program yang sama tetapi `is_reward_line=False`/`reward_id=False` → `_efn_get_otp_gated_lines()` kosong → order berbayar lolos tanpa OTP. Docstring modul justru menyatakan gate server dimaksudkan untuk klien yang dimodifikasi.
**Rekomendasi:** identifikasi juga berdasarkan `product_id` ∈ `reward.discount_line_product_id` / produk reward program ber-OTP, serta baris harga negatif tanpa izin; batasi hak ubah harga/diskon manual kasir (`pos.config` "Restrict price modification"), dan laporan harian baris diskon tanpa `reward_id`.

### SEC-13 — Low — Open redirect setelah login
**Lokasi:** `services/superapp-web/lib/session.ts:51-55`, dipakai `app/masuk/page.tsx:24` → `LoginForm.tsx:90` (`router.replace(nextPath)`).
**Skenario:** `https://app.<domain>/masuk?next=/%09/evil.example` → `searchParams` men-decode `%09` menjadi TAB; string `"/\t/evil.example"` lolos cek (`startsWith('/')`, bukan `//`, bukan `/\`). Parser URL WHATWG membuang TAB/LF → `//evil.example` → setelah OTP sukses, pengguna dinavigasi ke domain penyerang (halaman phishing berlogo brand, mis. "masukkan ulang kode").
**Rekomendasi:** tolak karakter kontrol & backslash di mana pun (`/[\u0000-\u001f\\]/`), lalu validasi `new URL(next, 'https://x.invalid')` → `origin === 'https://x.invalid'` dan kembalikan `pathname + search + hash`; atau allowlist prefix (`/akun`, `/pantau-harga`).

### SEC-14 — Low — Endpoint server-to-server `/v1/pos/*` terekspos publik
**Lokasi:** router `api` = `Host(api.<domain>)` tanpa pengecualian path (`docker-compose.yml:443`); `src/pos/pos.controller.ts:14-15`. Kontrak §1 menyebut `/v1/pos/*` dan `/internal/v1/*` sebagai internal.
**Dampak:** satu-satunya kontrol adalah service key (32 byte hex — tidak dapat ditebak), tetapi kebocoran key (log Odoo, backup) langsung dapat dipakai dari internet untuk memicu OTP ke nomor siapa pun dan membaca saldo poin (`GET /v1/pos/loyalty/balance?member_ref=` — nomor HP di query string juga berisiko masuk access log).
**Rekomendasi:** rule Traefik `Host(api…) && !PathPrefix(/v1/pos) && !PathPrefix(/internal)` (bersamaan dengan pengecualian `/metrics` yang sedang ditangani QA); ubah `loyalty/balance` menjadi POST body atau gunakan `member_hash`; dukung dua key (current+previous) untuk rotasi.

### SEC-15 — Low — Fallback password master Odoo & render config dengan `sed`
**Lokasi:** `services/odoo/entrypoint-efn.sh:17` (`ODOO_ADMIN_PASSWORD:=change-me` bila file secret tidak ada/tidak terbaca), `:20-24` (nilai disisipkan ke `sed s|…|…|` tanpa escape).
**Skenario:** salah mount secret di salah satu lingkungan → Odoo jalan dengan master password `change-me` (database manager memang dimatikan oleh `list_db=False`, tetapi password ini juga dipakai CLI/endpoint `db` lain). Password berisi `|`, `&`, atau baris baru merusak/menyuntik baris `odoo.conf`.
**Rekomendasi:** `exit 1` bila secret kosong; tulis config dengan `printf`/`envsubst` atau Python `configparser`; validasi charset.

### SEC-16 — Low — ETL menerima `otp_pepper` mentah
**Lokasi:** `services/etl/efn_etl/config.py:57,97`, `efn_etl/hashing.py:21-31` (wiring compose sedang ditangani QA).
**Dampak:** domain separation HKDF (`app-config.ts:131-156`) hilang bila ETL memegang IKM: container ETL (network `data`, akses Redis tanpa auth — SEC-05) dapat menurunkan kunci `efn:otp_code:v1`. Selain itu `member_hash = sha256(pepper‖nomor)` hanya pseudonim: ruang nomor HP Indonesia ~10^10, sehingga siapa pun yang memegang pepper dapat membalik seluruh hash dalam hitungan menit di GPU.
**Rekomendasi:** buat secret terpisah `member_hash_pepper` (hasil HKDF, dihitung sekali) untuk ETL; BFF tetap memakai `otp_pepper`. Dokumentasikan bahwa `member_hash`/`user_hash` tetap data pribadi (pseudonim) menurut UU PDP.

### SEC-17 — Low — Pembulatan lokasi hanya di browser
**Lokasi:** `src/market-scout/dto/report.dto.ts:20` (`COORD_RE` menerima 10 desimal), `:33-40`; disimpan apa adanya bersama `user_id` plaintext (`market-scout.service.ts:120-133`). Pembulatan 4 desimal hanya di `PriceReportForm.tsx:26,86`.
**Dampak:** klien lain/API langsung mengirim koordinat presisi sentimeter; janji di UI & privacy notice ("dibulatkan ±11 m") tidak ditegakkan server.
**Rekomendasi:** bulatkan di BFF (`Math.round(x*1e4)/1e4`) sebelum INSERT dan payload `ocr.requested`.

### SEC-18 — Info (PLAUSIBLE) — Log slow-query PostgreSQL dapat memuat nomor HP
**Lokasi:** `docker-compose.prod.yml:37` (`log_min_duration_statement=500`), base `:203`. Dengan extended protocol, PostgreSQL ≥13 mencetak `parameters: $1 = '62812…'` untuk statement lambat (`log_parameter_max_length` default -1) → stdout → Promtail → Loki (retensi 30 hari).
**Rekomendasi:** `-c log_parameter_max_length=0` (dan `log_parameter_max_length_on_error=0`); dev memakai `log_statement=all` (`docker-compose.dev.yml:35`) — jangan aktifkan stack observability dev dengan data nyata.

### SEC-19 — Info — Pemrosesan gambar berbagi proses dengan gate OTP POS
**Lokasi:** `src/market-scout/image/image-processor.ts:16,27` (`limitInputPixels` 50 MP — benar), container api-gateway 1 GiB (`docker-compose.prod.yml:63-67`).
**Catatan:** limit piksel dan cap harian sudah ada, tetapi tidak ada batas konkurensi decode; beberapa upload 50 MP paralel dari akun berbeda dapat memicu OOM restart BFF, yang juga melayani OTP POS/login. **Rekomendasi:** semaphore decode (mis. 2 per replika), `sharp.concurrency(1)`, atau turunkan `MAX_INPUT_PIXELS` ke ~24 MP (kamera ponsel 12–20 MP); jangka menengah pisahkan intake Market Scout ke worker sendiri.

---

## Catatan Info tambahan (tanpa ID)
- **Ollama** (`docker-compose.yml:607-628`) tanpa autentikasi, berada di `internal` + `egress`: container internal mana pun dapat `POST /api/pull`/`/api/create` (unduh model besar → disk penuh). Batasi dengan network `ai` khusus ocr-worker.
- **Webhook HMAC** sudah benar (timestamp ikut ditandatangani, replay window 300 s, multi-`v1`), tetapi belum ada penyimpanan event-id untuk replay dalam jendela — wajib saat endpoint payment dibuat (R-S09).
- **Logout** tidak me-revoke JWT (valid ≤1 jam; tercatat di `src/auth/README.md`). Pertimbangkan denylist `jti` di Redis.
- **CSP** masih `script-src 'unsafe-inline'` (`next.config.mjs:14`) — rencana nonce sudah dicatat.
- **`notify.push`**: `o.template in NOTIFY_TEMPLATES` (`notify-push.consumer.ts:33`) juga true untuk `constructor`/`toString` → pesan berakhir di DLQ, bukan celah; gunakan `Object.hasOwn`.
- **Middleware `internal-only`** memakai `ipStrategy.depth: 1`: akses VPN langsung (tanpa Cloudflare) tidak membawa XFF sehingga ditolak — dashboard bisa tidak terjangkau sama sekali (fail-closed, bukan celah).
- **Lisensi**: manifest `efn_*` kini `LGPL-3` sementara `LICENSES.md:9` menyebut AGPL-3 — samakan. Modul `efn_*` tidak mengimpor kode OCA AGPL, jadi LGPL sah; pemakaian internal MinIO/Grafana/Loki/Metabase (AGPL) aman selama tidak disediakan ke pihak ketiga.
- **PCI DSS SAQ A**: belum ada alur pembayaran; tidak ada field kartu di kode. Pertahankan hosted payment page/redirect gateway, CSP `form-action 'self'` perlu ditambah domain gateway saat itu.
- **Permendag 70/2019 (MGM)**: belum diimplementasikan; ledger sudah mendukung `company_code`/`source`. Saat MGM dibuat: istilah "cashback promosi", plafon, tanpa biaya keanggotaan, dan konsultasi legal (R-L02).
- **Sedang ditangani QA** (disebut singkat): nama env, prefix path `/v1`, wiring hash member ETL, argumen queue, grant `efn_readonly`, eksposur `/metrics` via Traefik, phash.

## Kontrol yang sudah benar (untuk manajemen)
- **OTP** (`src/otp/otp.service.ts`): kode `crypto.randomInt`, disimpan sebagai HMAC dengan kunci HKDF terpisah, perbandingan waktu-konstan, attempt dinaikkan atomik **sebelum** perbandingan (anti race), sekali pakai via `DEL` pemenang, kegagalan dibedakan hanya `invalid/expired/locked`, respons request selalu 202, audit memakai `member_hash`.
- **Discount token** (`src/pos/discount-token.service.ts`): HS256 dikunci (`algorithms: ['HS256']`, menolak `none`/alg confusion), `purpose` dicek, terikat `order_ref`, single-use `SET NX` dengan replay idempotent untuk order yang sama, mismatch tidak membakar token; token & `challenge_id` tidak pernah dikirim ke browser POS (disimpan di field `groups=base.group_system`).
- **Service key & JWT**: `timingSafeEqual` atas digest SHA-256 (panjang tidak bocor); JWT pelanggan dengan `iss/aud/typ`; cookie `HttpOnly; Secure; SameSite=Lax`, token tidak pernah ke JS; CSRF tambahan via cek `Origin` = `Host`.
- **Upload**: magic bytes, batas byte di busboy dan di proxy Next.js (streaming), re-encode sharp tanpa metadata, `limitInputPixels`, object key dibuat server (UUID) — tidak ada injeksi key MinIO; worker mengambil key dari DB + validasi prefix.
- **Error & log**: filter global tidak membocorkan detail; nomor HP selalu di-mask di BFF, Odoo, worker; access log Traefik membuang header `Authorization`/`Cookie`; WhatsApp adapter tidak mencetak token/kode/nomor; OTP hanya dicetak di `NODE_ENV=development`.
- **Odoo**: model challenge dengan `company_id`, `_check_company_auto`, record rule multi-company, ACL minimal, autovacuum retensi 30 hari; method yang memakai `sudo()` bersifat privat (`_efn_*`, tidak dapat dipanggil RPC); RPC publik memeriksa grup POS + sesi terbuka; API key dibaca dari file, redirect tidak diikuti.
- **Infra**: hanya Traefik yang mem-publish port di prod (CI memeriksa), network `data`/`internal` `internal: true`, `no-new-privileges`, `cap_drop: ALL`, `read_only` di sebagian besar service, image non-root, docker-socket-proxy read-only untuk Traefik/Promtail, WAL-G terenkripsi libsodium + user MinIO backup berhak minimum, secret dikirim ke host lewat stdin (tidak di argv), dir secret 0700.
- **CI/CD**: `permissions: {}` default + per job, Trivy gate HIGH/CRITICAL, SBOM, cosign keyless + verifikasi sebelum deploy, gitleaks, CodeQL, dependency review, Checkov, ZAP baseline.
- **ETL**: semua SQL terparameter (tidak ada SQL injection), transaksi read-only + `statement_timeout`, koordinat dibulatkan 3 desimal sebelum ClickHouse, identitas di-hash.

## Prioritas perbaikan
1. **Sebelum staging publik:** SEC-01, SEC-02, SEC-03, SEC-05.
2. **Sebelum go-live:** SEC-04 (sebelum Text-to-SQL dibuka), SEC-06, SEC-07, SEC-08, SEC-09, SEC-10 (pin SHA = blocker prod), SEC-11.
3. **Backlog terjadwal:** SEC-12 s.d. SEC-19 dan catatan Info.
