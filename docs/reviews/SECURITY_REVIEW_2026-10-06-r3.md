# Security & Compliance Re-review (r3): Gelombang 5/5b (2026-10-06)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Ini re-review atas `SECURITY_REVIEW_2026-10-05-r2.md` setelah Gelombang 5/5b: ALTCHA, member opak, payment gateway-agnostic, privasi/DSAR/erasure, rotasi kunci PII, PKI 3 tingkat, mTLS, `wg-https`, dan kustodi kunci Superadmin.
Cakupan: `services/api-gateway/src/{auth,payments,privacy,common/pii,common/metrics,cli}`, `services/superapp-web` (route `/api/me/*`, ALTCHA, cookie), Odoo `efn_pos_payment_gateway` + `efn_privacy`, `services/etl/efn_etl/erasure.py`, `infra/**`, `scripts/{gen-pki,deploy,derive-secrets}.sh`, `.github/workflows/deploy.yml`, `docs/04` §7–§8.11, ADR-022..029, `docs/runbooks/key-custody.md`, `docs/compliance/*`, serta laporan agent bagian Gelombang 5/5b.
Metode: membaca kode dan konfigurasi. Tidak ada yang dijalankan karena mesin reviewer tidak punya Docker, Node, Python, atau openssl. Perilaku runtime yang belum pasti ditandai **PLAUSIBLE**.
Koordinasi: penyelarasan integrasi berikut **ditangani QA** dan tidak diulang sebagai temuan: Redis ACL `altcha:used:*`, scrape port 9464, RabbitMQ definitions/strict mode, env compose, dan tabel pengecualian ADR-022 (E11–E13).

## Ringkasan eksekutif (untuk manajemen)

1. **Status 30 temuan lama: 25 Fixed, 4 Partial, 1 Open, 0 Regressed.** Kedua Medium r2 sudah ditutup. SEC-20: semua sidecar dan endpoint observability kini mTLS. SEC-21: dashboard hanya bisa dibuka dari subnet staf lewat entrypoint WireGuard khusus. Captcha OTP (ALTCHA self-hosted), PKI 3 tingkat dengan `nameConstraints`, batas memori pemrosesan gambar, dan enkripsi PII di Redis juga sudah ada.
2. **Tidak ada temuan Critical/High.** Ada **10 temuan baru: 3 Medium, 5 Low, 2 Info.** Ketiga Medium:
   - (a) **Penghapusan akun (UU PDP) belum benar-benar anonim.** Penanda pengganti identitas bisa dihubungkan kembali ke nomor HP oleh pihak yang memegang DB + pepper.
   - (b) **Unduh data pribadi (hak akses UU PDP) di aplikasi selalu gagal.** Frontend tidak meneruskan token verifikasi ulang.
   - (c) **Adapter payment Xendit/Midtrans belum aman untuk diaktifkan.** Status "lunas" bisa diterima tanpa konfirmasi ke provider. Saat ini tidak dapat dieksploitasi karena adapter masih nonaktif/eksperimental.
3. **Payment POS sisi Odoo dirancang dengan baik.** Order QRIS hanya bisa disimpan bila intent milik order itu, nominal sama, dan berstatus `paid` di server. Status tidak pernah diambil dari browser.
4. **UU PDP:** consent server-side append-only, DSAR (ekspor/hapus/batal), erasure lintas Platform–Odoo–ClickHouse, retensi laporan, dan TTL mart sudah terimplementasi. Yang belum: SEC-31, SEC-32, ekspor besar, koreksi/ganti nomor, runbook insiden teknis, dan finalisasi DPO/kebijakan.
5. **Rekomendasi:** SEC-32 dan SEC-31 diselesaikan sebelum go-live. SEC-33 menjadi syarat wajib sebelum `PAYMENT_PROVIDER` vendor nyata diaktifkan.

## Status temuan lama (SEC-01..SEC-30)

| ID | Sev. awal | Status r3 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01 | High | **Fixed** | `docker-compose.yml:595-627` (semua router `erp.` hanya `entrypoints: wg-https`, `internal-only`, blok `/xmlrpc`,`/jsonrpc`,`/json/2`), `traefik.yml:83-88` | — |
| SEC-02 | High | **Fixed** | role/user per service (r2) + `rabbitmq-entrypoint.sh:64-76` (user `efn-etl` minimal) | — |
| SEC-03 | High | **Fixed** (PLAUSIBLE runtime) | `traefik.yml:54-79` (XFF hanya CF), `deploy.sh:217` | Uji `req.ip` di staging |
| SEC-04 | Medium | **Fixed** | tidak berubah | — |
| SEC-05 | Medium | **Fixed** | `redis-entrypoint.sh:30` (ACL + `~altcha:used:*`) | — |
| SEC-06 | Medium | **Fixed** | `auth.service.ts:68-69` (ALTCHA sebelum rate limit), `altcha.service.ts:66-78` | Biaya PoW rendah → SEC-34 |
| SEC-07 | Medium | **Partial** | `amount` diikat ke token + `amount_total` dari Odoo (`pos/README.md:32`, `efn_pos_otp_gate` test 254-278) | Kasir masih bisa mengubah HP partner (E-6 functional belum) |
| SEC-08 | Medium | **Fixed** | tidak berubah; pola `user_id` diterapkan juga di `erasure-completed.consumer.ts:67-70` | — |
| SEC-09 | Medium | **Fixed** | socket-proxy mTLS `docker-compose.yml:240-242` | — |
| SEC-10 | Medium | **Fixed** | tidak berubah | `semgrep:latest` → SEC-30 |
| SEC-11 | Medium | **Partial** | consent/DSAR/erasure (`privacy/*`), retensi BFF, TTL `clickhouse-init.sh:80-84`, foto 90 h | SEC-31, SEC-32, ekspor besar, koreksi/ganti nomor, DPO, `security-incident.md` belum ada |
| SEC-12 | Low | **Fixed** | — | — |
| SEC-13 | Low | **Fixed** | — | — |
| SEC-14 | Low | **Fixed** | `docker-compose.yml:800-802` (`!PathPrefix(/v1/pos)`, `/internal`, `/v1/webhooks`) | — |
| SEC-15 | Low | **Fixed** | — | fallback passlib → SEC-30 |
| SEC-16 | Low | **Fixed** | — | — |
| SEC-17 | Low | **Fixed** | — | — |
| SEC-18 | Info | **Fixed** | — | — |
| SEC-19 | Info | **Fixed** | `image-processor.ts:4,31-43` (semaphore, `sharp.concurrency`, cache off, 429) | — |
| SEC-20 | Medium | **Fixed** | ghostunnel `--cacert` + `--allow-cn` (`docker-compose.yml:240,653,678,886,1034,1144,1496`), `web-config.yml:9-12`, `web-config-ui.yml:7-11`, `loki.yml:9-13`, BFF `metrics-server.ts:47-82`, MinIO `MINIO_PROMETHEUS_AUTH_TYPE: jwt` (`:502`) | E12 tanpa allowlist CN → SEC-35 |
| SEC-21 | Medium | **Fixed** | `middlewares.yml:77-90` (`internal-only` outlet+staf, `internal-ops` staf), `traefik.yml:54-88`, `deploy.sh:58-67` | — |
| SEC-22 | Low | **Fixed** | `docker-compose.yml:650-654` (Odoo hanya CN `traefik` + `blackbox-exporter`), blok `/json/2` `:616` | — |
| SEC-23 | Low | **Fixed** | `gen-pki.sh:81-95,142-147` (pathlen:0, `nameConstraints`), `:36` leaf 90 h, `deploy.sh:43-44` (`PKI_SOURCE=host` ditolak), `public-https.ts:36-45` (egress root publik saja) | Catatan kustodi → SEC-36, SEC-40 |
| SEC-24 | Low | **Fixed** | `otp.service.ts:114` (`m` terenkripsi), `pii.ts:46-48` (`redisKeyHash`), `auth.service.ts:59-61` | `PDP_DATA_INVENTORY.md:78-79` usang; AOF tetap (`redis-entrypoint.sh:47`), kini tanpa PII |
| SEC-25 | Low | **Partial** | `rabbitmq-entrypoint.sh:46-54` (topologi oleh broker, write `amq.default`) | `RABBITMQ_STRICT_PERMISSIONS` default `false` (`deploy.sh:205`), **ditangani QA** |
| SEC-26 | Low | **Fixed** | `00-roles.sh:48-61` (`\set pw` lewat stdin) | — |
| SEC-27 | Low | **Fixed** | `efn_ocr/tls.py:29-43`, `efn_etl/tls.py:17-39` (kosong = production) | — |
| SEC-28 | Low | **Fixed** (PLAUSIBLE runtime) | `infra/wireguard/efn-wg.nft:21-48` (antar-peer drop, hanya DNAT ke 443, SSH hanya staf IT), `gen-peer.sh --pubkey` | Uji interaksi nft vs iptables-nft Docker di staging |
| SEC-29 | Info | **Open** | `secrets.loader.ts:45-69` (kunci PII masih disalin ke `process.env`) | — |
| SEC-30 | Info | **Partial** | Fixed: MinIO metrik JWT, `ACME_EMAIL` divalidasi (`deploy.sh:50`), pg_hba subnet, proteksi branch didokumentasikan (`key-custody.md:30`) | `semgrep:latest` (`security.yml:36`), fallback passlib (`render_odoo_conf.py:115-119`), `toJSON(secrets)` (diterima) |

**Rekap:** Fixed 25 · Partial 4 (SEC-07, 11, 25, 30) · Open 1 (SEC-29) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-31 | Medium | Erasure belum anonim: tombstone diturunkan dari `request_id`, sedangkan `request_id` disimpan bersama `member_hash` (5 th) dan di partner Odoo. `member_hash` dapat dibalik ke nomor HP oleh pemegang pepper | `pii.ts:51-53`, `erasure.service.ts:139,155`, `03-platform-v3.sql:72-78,142`, `efn_privacy_erasure.py:213-217` | R-L01 |
| SEC-32 | Medium | Ekspor DSAR selalu gagal: route Next.js `/api/me/data` tidak meneruskan token step-up, BFF selalu `403` | `superapp-web/app/api/me/data/route.ts:19-24`, `privacy.controller.ts:46-49` | R-L01 |
| SEC-33 | Medium | Status `paid` dari webhook tanpa konfirmasi ke provider; Midtrans `transaction_status` di luar signature; cek nominal dilewati bila `amount` null; cek nominal Odoo tautologis (blocker sebelum vendor aktif) | `midtrans.gateway.ts:133-162`, `xendit.gateway.ts:112-134`, `payments.service.ts:220-235,270,302-309`, `efn_pos_payment_intent.py:119-129` | R-S09, R-T10 |
| SEC-34 | Low | ALTCHA `maxnumber=100000` hampir tanpa biaya bagi bot native. Kuota per nomor masih bisa dihabiskan penyerang multi-IP | `app-config.ts:546-548`, `altcha.service.ts:57-63`, `auth.service.ts:76-80` | R-S04 |
| SEC-35 | Low | mTLS tanpa allowlist CN (E12) memberi ocr-worker (pemroses gambar tak tepercaya) akses baca/tulis Loki dan metrik lain | `loki.yml:9-13`, `gen-pki.sh:70`, laporan devsecops G5.3 E12 | R-S06 |
| SEC-36 | Low | Jalur deploy: `deploy NOPASSWD: /usr/bin/bash -s` + `deploy.sh` milik user deploy = root bagi pemegang kunci SSH deploy. Kunci intermediate tersimpan online di GitHub | `key-custody.md:42`, `deploy.yml:139-143,224-225`, `gen-pki.sh:315-319` | R-S01, R-S07 |
| SEC-37 | Low (PLAUSIBLE) | Token DNS ACME di Traefik (container yang menghadap internet). Bila token bisa mengedit seluruh zona, kompromi Traefik berujung pembajakan DNS `api.`/`app.` | `docker-compose.yml:277-279`, `traefik.yml:115-123` | R-S03 |
| SEC-38 | Low (PLAUSIBLE) | Nomor HP daur ulang mewarisi akun lama: poin, riwayat laporan + lokasi, dan riwayat consent tampil di ekspor DSAR | `dsar.service.ts:97-196`, `member.service.ts` (upsert by hash) | R-L01, R-B |
| SEC-39 | Info | Append-only consent hanya pagar bug aplikasi: `efn_bff` pemilik tabel dapat menonaktifkan trigger; `UPDATE(ip_enc)` dapat menimpa bukti IP | `03-platform-v3.sql:9,26-29,44-68` | R-L01 |
| SEC-40 | Info | Catatan kecil (step-up tidak sekali pakai, race hapus foto vs batal, staging gagal start dengan mock, `disabled` ditolak deploy, cakupan erasure Odoo, dll.) | lihat detail | — |

---

## Detail temuan baru

### SEC-31: Medium: Erasure UU PDP hanya pseudonimisasi, bukan anonimisasi
**Lokasi:** `services/api-gateway/src/common/pii/pii.ts:51-53` (`tombstone = sha256("erased:" || request_id)`), `privacy/erasure.service.ts:139` (tombstone dipakai di ledger/laporan/otp_audit), `:155` (entri forfeit `source_ref = requestId`), `infra/postgres/init/03-platform-v3.sql:72-78` (`dsar_request.member_hash` "dipertahankan"), `:142` (`efn_etl` boleh `SELECT id, member_hash`), `services/odoo/addons/efn_privacy/models/efn_privacy_erasure.py:213-217` (partner teranonim menyimpan `efn_erasure_request_id` = request_id penuh dan nama `Pelanggan dihapus <request_id[:8]>`), `docs/compliance/PDP_DATA_INVENTORY.md:44` (retensi `dsar_request` 5 tahun).
**Kondisi:** desain menyatakan tombstone "tidak dapat dihubungkan ke nomor HP". Kenyataannya rantai berikut tersedia di DB:
`tombstone` ← sha256 deterministik dari `request_id` → baris `dsar_request (id, member_id, member_hash)` → `member_hash = sha256(pepper_hex || nomor)`. Ruang nomor HP Indonesia ±10^10–10^11, sehingga siapa pun yang memegang `member_hash_pepper` (BFF, dan sejak Gelombang 5 juga **Odoo**) dapat membalik hash dengan brute force dalam hitungan detik–menit di GPU.
**Skenario:** orang dalam/penyerang dengan akses baca DB platform (backup WAL-G, `efn_etl`, DBA) + pepper menghitung `sha256("erased:"||id)` untuk setiap `dsar_request`, lalu mencocokkannya ke ledger 10 tahun, `price_hunter_report` 2 tahun, dan `otp_audit`. Riwayat transaksi dan laporan subjek yang sudah meminta hapus terbuka kembali, lengkap dengan nomor HP-nya. Di Odoo, partner teranonim tetap terhubung ke request yang sama lewat `efn_erasure_request_id`. Dalam UU PDP data seperti ini masih data pribadi, sehingga klaim "dihapus/dianonimkan" ke subjek dan DPO tidak akurat.
**Rekomendasi:**
1. Tombstone = nilai **acak** (`randomBytes(32)`) yang dibuat sekali saat `processing` dan **tidak disimpan** di `dsar_request`. Ganti `source_ref` entri forfeit dengan nilai yang tidak memuat `request_id`.
2. Setelah `stores_pending` kosong, kosongkan `dsar_request.member_hash` dan `member_id`. Untuk kebutuhan replay restore backup, simpan daftar `member_hash` terhapus secara **terenkripsi** dengan kunci kustodi Superadmin (atau HMAC dengan kunci terpisah). Purge setelah retensi backup habis, bukan 5 tahun.
3. Odoo: jangan simpan `request_id` penuh di partner. Cukup flag boolean atau penanda acak, dan pakai nama placeholder tanpa potongan id.
4. Catat di `PDP_DATA_INVENTORY.md` bahwa `member_hash` dapat dibalik oleh pemegang pepper (perlakukan sebagai pengenal langsung), dan batasi pemegang pepper.

### SEC-32: Medium: Ekspor data DSAR tidak berfungsi (hak akses subjek)
**Lokasi:** `services/superapp-web/app/api/me/data/route.ts:19-24` (memanggil `bffFetch('/v1/me/data', { token, incoming })` **tanpa** header `x-step-up-token`), bandingkan dengan `app/api/me/route.ts:45,58-62` (DELETE meneruskan cookie `efn_stepup`). BFF: `privacy.controller.ts:46-49` + `dsar.service.ts:67-70` mewajibkan header step-up.
**Skenario:** pengguna melakukan re-OTP. Cookie `efn_stepup` terpasang (Path `/api/me`, jadi juga terkirim ke `/api/me/data`), tetapi route tidak membacanya. BFF menjawab `403 step_up_required` dan unduhan gagal terus-menerus. Hak akses UU PDP (Pasal 7/32) tidak bisa dipenuhi lewat aplikasi. Kegagalannya fail-closed, jadi tidak ada kebocoran data.
**Rekomendasi:** baca `getStepUpTokenFromRequest(req)`. Bila tidak ada, kembalikan `403 step_up_required` seperti DELETE. Teruskan cookie sebagai `x-step-up-token`. Tambahkan uji kontrak/e2e untuk alur reauth → data.

### SEC-33: Medium: Penetapan `paid` belum cukup kuat untuk vendor nyata
**Lokasi & kondisi:**
- `payments/gateways/midtrans.gateway.ts:133-146`: `signature_key = SHA512(order_id + status_code + gross_amount + server_key)` **tidak mencakup `transaction_status`**, tetapi `parseWebhook` (`:148-162`) memetakan status dari `transaction_status`, dan `eventId = transaction_id:transaction_status`. Notifikasi sah `pending` (status_code 201) yang diubah `transaction_status: "settlement"` lolos verifikasi, punya eventId baru (tidak terdeteksi duplikat), lalu intent menjadi `paid`.
- `xendit.gateway.ts:112-119`: token callback statis tanpa timestamp/body signature. Kebocoran token sama dengan kemampuan memalsukan `paid` untuk QR mana pun, karena `data.id` (eventId) bebas dipilih penyerang.
- `payments.service.ts:220-235`: tidak ada konfirmasi `getStatus` ke provider sebelum transisi ke `paid`, padahal Midtrans dan Xendit menyarankannya. `:270`: cek nominal **dilewati bila `amount` null** (mock `:67`, Xendit `:133`, Midtrans `:155` mengembalikan null bila field hilang/format lain).
- `statusResponse` (`:302-309`) mengembalikan `amount` **milik intent**, bukan nominal yang dibayar. Akibatnya cek `amount_mismatch` di Odoo (`efn_pos_payment_intent.py:119-129`) selalu cocok dan hanya ilusi kontrol.
- `:221-233`: event di-claim di `processed_message` **sebelum** intent ditemukan. Webhook yang tiba sebelum `provider_ref` tersimpan (`:132-135`) dibuang permanen. Dampaknya kecil karena dimitigasi polling `getStatus`.
**Skenario (Midtrans, PLAUSIBLE):** seseorang yang pernah melihat satu notifikasi pending (log proxy/Cloudflare, salah konfigurasi logging, akses dashboard) mengirim ulang dengan status `settlement`. Order POS tersinkron sebagai lunas tanpa uang masuk.
**Status saat ini:** tidak dapat dieksploitasi. Xendit/Midtrans menolak start tanpa `PAYMENT_PROVIDER_EXPERIMENTAL=true` (`app-config.ts:826-829`), dan mock memakai HMAC + replay window 300 s (`mock.gateway.ts:50-58`).
**Rekomendasi (syarat wajib sebelum vendor diaktifkan):**
1. Untuk transisi ke `paid`, selalu panggil `gw.getStatus()` dan pakai hasilnya (status + nominal). Webhook hanya pemicu.
2. Midtrans: tolak bila `status_code` tidak konsisten dengan `transaction_status` (200↔settlement/capture), dan pakai `transaction_id` saja sebagai eventId.
3. Tolak `paid` bila `amount` null.
4. Simpan kolom `paid_amount` dan kembalikan ke Odoo.
5. Claim event hanya setelah intent ditemukan.
6. Allowlist IP webhook vendor di Traefik bila vendor menerbitkannya.

### SEC-34: Low: ALTCHA hanya "polisi tidur" bagi bot
**Lokasi:** `config/app-config.ts:546-548` (`ALTCHA_MAX_NUMBER` default 100 000), `auth/altcha.service.ts:57-63`, `auth.service.ts:64,76-80`.
**Kondisi:** rata-rata 50 000 SHA-256 per challenge ≈ 5 ms di kode native, sehingga biaya nyata bagi penyerang adalah rate limit challenge per IP (120/15 menit). Penyerang dengan pool proxy tetap bisa memompa OTP sampai plafon per nomor (10/jam) dan menahan nomor korban (lockout tertarget, sisa SEC-06) atau menghabiskan budget global.
**Implementasi yang sudah benar:** HMAC atas challenge dibandingkan waktu-konstan. `expires` terikat lewat `challenge = SHA-256(salt+number)`. Salt splicing dicegah (`SALT_RE` diakhiri `&`). Single-use atomik (`SET NX`, kunci = signature). Semua kegagalan dijawab seragam. Replay lintas challenge tidak mungkin.
**Rekomendasi:** naikkan `maxnumber` ke 500 000–1 000 000 (±1–2 s di ponsel) dan terapkan kenaikan adaptif saat alert budget OTP (sesuai ADR-028). Pertimbangkan mengikat challenge ke `ipKey` (parameter di salt) agar challenge yang ditambang di satu IP tidak bisa dipakai di IP lain.

### SEC-35: Low: Pengecualian E12 (mTLS tanpa allowlist CN) mencakup klien berisiko tinggi
**Lokasi:** `infra/observability/loki.yml:9-13` (klien = sertifikat CA internal apa pun ber-EKU clientAuth), `scripts/gen-pki.sh:59-71` (EKU `both` untuk traefik, prometheus, grafana, blackbox, **ocr-worker**, cadvisor). E12 juga berlaku untuk redis_exporter, RabbitMQ 15691, dan Traefik :8082 (`internal-tls.yml:56-63`).
**Skenario:** ocr-worker memproses gambar tak tepercaya, sehingga paling mungkin terkompromi. Dengan sertifikat klien sah miliknya, penyerang bisa `GET https://loki:3100/loki/api/v1/query_range` untuk membaca log semua service, atau `push` log palsu untuk menutupi jejak.
**Rekomendasi:** untuk Loki, pasang ghostunnel di depannya dengan `--allow-cn=promtail --allow-cn=grafana`, atau terbitkan intermediate/CA klien khusus observability dan pakai sebagai `client_ca_file`. Sertifikat klien ocr-worker (khusus Ollama) dapat dibuat tidak dipercaya oleh endpoint observability.

### SEC-36: Low: Batas kustodi bergantung pada jalur deploy
**Lokasi:** `docs/runbooks/key-custody.md:42` (`deploy ALL=(root) NOPASSWD: /usr/bin/bash -s`), `.github/workflows/deploy.yml:139-143` (rsync `scripts/` ke `/opt/efn/scripts` sebagai user deploy), `:224-225`, `scripts/deploy.sh:2-5`, `gen-pki.sh:315-319` (kunci intermediate disimpan sebagai secret GitHub Environment).
**Kondisi:** `sudo bash -s` adalah shell root tanpa batas. Selain itu `deploy.sh` yang dieksekusi root dimiliki dan dapat ditulis user `deploy`. Siapa pun yang memegang kunci SSH deploy atau sesi user deploy menjadi root dan dapat membaca semua secret Superadmin. Runbook mengakui hal ini; kontrol nyatanya adalah approval Environment. Kunci intermediate online di GitHub dimitigasi `nameConstraints`, tetapi kompromi admin org GitHub tetap memberi kemampuan menerbitkan sertifikat internal apa pun.
**Rekomendasi:**
1. `authorized_keys` untuk kunci CI: `restrict,command="sudo -n /usr/local/sbin/efn-deploy"`.
2. Wrapper milik root yang hanya membaca blok `export` dari stdin, menyalin rilis ke direktori milik root, dan memverifikasi checksum/tanda tangan commit sebelum menjalankan `deploy.sh`.
3. User deploy bukan anggota grup `docker`.
4. Alert auditd untuk setiap `sudo` dari user deploy.
5. Rotasi intermediate sesuai jadwal dan audit log akses secret Environment.

### SEC-37: Low (PLAUSIBLE): Token DNS ACME di Traefik
**Lokasi:** `infra/compose/docker-compose.yml:277-279` (`CF_DNS_API_TOKEN_FILE` di container Traefik), `infra/traefik/traefik.yml:115-123`.
**Kondisi & skenario:** Traefik adalah komponen yang menghadap internet. Bila token Cloudflare berizin `Zone:DNS:Edit` untuk zona utama (cakupan tidak dapat diverifikasi dari repo), RCE di Traefik memungkinkan penyerang mengubah record `api.`/`app.`, menerbitkan sertifikat, lalu melakukan phishing atau intersepsi OTP. Selain itu, sertifikat DNS-01 untuk `erp./bi./ops./status.` tercatat di Certificate Transparency, sehingga nama host internal menjadi publik (Info).
**Rekomendasi:** delegasikan `_acme-challenge.<host>` via CNAME ke zona terpisah khusus ACME, lalu batasi token ke zona itu (atau minimal ke satu zona, hanya DNS edit, dengan IP filtering). Pertimbangkan wildcard `*.int.<domain>` agar nama host individual tidak muncul di CT log.

### SEC-38: Low (PLAUSIBLE): Daur ulang nomor HP
**Lokasi:** identitas = nomor HP (`member_hash` UNIQUE, `03-platform-v3.sql:15-21`). Ekspor `dsar.service.ts:97-196` memuat seluruh laporan (termasuk `geo_lat/lng` selama 2 tahun), ledger, dan riwayat consent.
**Skenario:** operator seluler Indonesia mendaur ulang nomor yang tidak aktif. Pemilik baru lolos OTP, lalu mewarisi poin pemilik lama dan dapat mengunduh riwayat lokasi laporan Price Hunter milik pemilik lama.
**Rekomendasi:** kebijakan akun dorman, misalnya anonimisasi otomatis (alur erasure yang sama) setelah N bulan tanpa login/transaksi, atau minimal mengecualikan data lebih tua dari login terakhir + X hari dari ekspor. Bila keputusan bisnis berbeda, catat sebagai risiko residu di PDP_DSAR_DESIGN.

### SEC-39: Info: Kekuatan "append-only" consent
**Lokasi:** `03-platform-v3.sql:9` (`SET ROLE efn_bff`, sehingga pemilik tabel = role aplikasi), `:26-29`, `:44-68`.
**Catatan:** kode sendiri mengakui bahwa trigger dan REVOKE hanya pagar terhadap bug aplikasi. BFF yang terkompromi (atau eksekusi SQL apa pun sebagai `efn_bff`) dapat `ALTER TABLE … DISABLE TRIGGER`, mengembalikan hak, lalu mengubah bukti consent. `UPDATE(ip_enc)` juga dapat menimpa IP bukti dengan ciphertext lain.
**Rekomendasi:** pemilik skema `platform` = role `NOLOGIN` terpisah (misalnya `platform_owner`) yang dipakai hanya oleh migrasi; `efn_bff` cukup DML. Bila bukti consent kelak dipakai di sengketa, pertimbangkan hash-chain baris consent.

### SEC-40: Info: Catatan kecil
- **Step-up** (`access-token.service.ts:53-67`): `jti` dibuat tetapi tidak dicek sekali pakai. Token 10 menit dapat dipakai berulang untuk ekspor dan DELETE. Binding `sub`/`typ`/`purpose` sudah benar dan cookie `efn_stepup` sudah tepat (HttpOnly, SameSite=Strict, Path `/api/me`, `session.ts:49-59`). Bila ingin ketat: tandai `jti` terpakai untuk DELETE.
- **Race hapus foto vs batal** (`erasure.service.ts:107-113`): foto MinIO dihapus **sebelum** transaksi yang memeriksa status. Pembatalan di sela proses (jendela kecil, tetapi melebar bila foto banyak) membuat foto hilang padahal request batal. Pindahkan penghapusan foto setelah commit (ikuti `stores_pending`).
- **Staging** (**ditangani QA**, env compose): `NODE_ENV=production` dipaksa untuk semua env (`deploy.sh:200`, `docker-compose.yml:709`), sedangkan `AppConfig.isProduction` true bila `NODE_ENV=production` (`app-config.ts:224-226`). Akibatnya `PAYMENT_PROVIDER=mock` di staging ditolak (`:826`) dan BFF gagal start, serta `simulate` 404 di staging. Ini fail-closed, tetapi keputusan payment sebaiknya berbasis `APP_ENV`.
- **`deploy.sh:53`** hanya menerima `mock|xendit|midtrans`; nilai `disabled` (default aman produksi, §8.11) ditolak. Produksi jadi terdorong mengaktifkan adapter eksperimental sebelum SEC-33 selesai. Izinkan `disabled`.
- **Erasure Odoo** (`efn_privacy_erasure.py`): cakupan sudah luas (arsip, lintas company, `pos.order` email/mobile, chatter, tracking, auditlog), `efn_member_hash` ikut kosong karena compute dari `phone`, log tanpa PII, dan commit sebelum `complete`. Sisa (diakui odoo-engineer): kontak anak, `bank_ids`, lampiran, serta partner dengan nomor yang tidak ternormalisasi sehingga tidak cocok hash. Penggunaan `sudo()` lintas company memang diperlukan untuk kewajiban hapus. Keputusan pengendali per PT ada di DPO.
- **ClickHouse erasure** (`etl-access.sql:11-35`): `allow_ddl = 1 CONST` + `GRANT ALTER DELETE` hanya pada 2 tabel **layak**. Pembatas nyata adalah GRANT, dan literal hash divalidasi ketat (`erasure.py:125-134`). Sisa risiko: ETL terkompromi dapat menghapus baris mart sembarang. Bila perlu, pisahkan user `efn_etl_erasure`.
- **Rotasi kunci PII** (`reencrypt.ts`, `pii-reencrypt.ts`): UPDATE optimistik per baris (tanpa transaksi batch, tetapi idempoten dan aman paralel); output hanya angka; error memakai `err.message` (pesan pg tanpa nilai baris). `PiiKeyRingCheck` menolak start bila ada ciphertext versi tak dimuat, tetapi hanya memberi **peringatan** bila DB tak terjangkau saat start (`pii-key-check.ts:42-44`). Ini dapat diterima.
- **PKI:** penjaga `root` hanya memeriksa `/opt/efn/compose` (`gen-pki.sh:304,312`). Server baru, runner CI, atau mesin lain tetap bisa menjalankannya. Tambahkan konfirmasi interaktif dan cek "tidak ada jaringan". Kunci leaf sementara ada di `mktemp -d` (/tmp disk, 0700) dan dihapus tanpa `shred`; `derive-secrets.sh:111-114` sama. Setiap leaf punya SAN `localhost`/`127.0.0.1` (hanya relevan untuk loopback).
- **`PDP_DATA_INVENTORY.md:78-79`** masih menyebut nomor HP/IP plaintext di Redis (sudah diperbaiki, SEC-24). `docs/runbooks/security-incident.md` belum ada.
- **Payment SSRF:** `PAYMENT_PROVIDER_BASE_URL` wajib `https://` (`app-config.ts:840-842`) dan egress memakai **hanya** root store publik (`public-https.ts:36-45`). Host internal (sertifikat CA internal) tidak dapat dijangkau, dan nilai ini hanya diset admin. Diterima.

---

## Kontrol yang sudah benar (untuk manajemen)
- **ALTCHA manual** sudah benar secara kriptografi: HMAC waktu-konstan, expiry terikat challenge, single-use atomik di Redis, tanpa oracle, widget di-bundle sendiri tanpa CDN (CSP `script-src 'self'`, `worker-src blob:`), dan challenge dari origin sendiri.
- **Payment (inti):** webhook diverifikasi atas raw body, perbandingan waktu-konstan, replay window 300 s (mock), idempotensi `processed_message`, transisi monoton dengan `SELECT … FOR UPDATE`, dan idempotency key per intent dengan deteksi konflik payload. Endpoint POS memakai service key dan tidak dirutekan publik. Route webhook publik hanya `POST /v1/webhooks/payments/` dengan rate limit. `mock` ditolak di produksi, `simulate` 404 di produksi.
- **Odoo POS gateway:** RPC mengikuti pola `pos_adyen` (grup POS, metode milik config, config terbaca user sesuai record rule multi-company, sesi terbuka). Saat sync, server memvalidasi intent: milik order, metode dan company sama, `payment_uuid` sama, belum dipakai, `paid` (refresh ke BFF), dan nominal sama. Refund QRIS ditolak. Ada cron rekonsiliasi intent yatim. Model intent read-only untuk kasir, dengan rule company.
- **Privasi:** step-up `purpose: dsar` terikat member dan `typ` (tidak bisa ditukar dengan access token). Semua endpoint `/v1/me/*` memakai member dari token (tanpa IDOR). Ekspor dibatasi rate per member dan dicatat. DELETE idempoten (unique index parsial). Cancel dan processing saling eksklusif lewat kondisi status. Erasure platform berjalan dalam satu transaksi dengan advisory lock. Consumer erasure ClickHouse memvalidasi `user_id = efn-etl` dan idempotency key terikat request.
- **mTLS & PKI:** semua sidecar dengan `--allow-cn` (tanpa `--disable-authentication`, dicek CI). Exporter dan Prometheus memakai `client_allowed_sans`. BFF :9464 memakai `requestCert` + `rejectUnauthorized` + cek CN/SAN dan hanya `GET /metrics`. PKI 3 tingkat dengan `nameConstraints` dan EKU per profil (server murni tanpa clientAuth). Kunci intermediate hanya di `/dev/shm` selama deploy lalu dihancurkan. Root offline terenkripsi passphrase. Egress publik BFF tidak mempercayai CA internal.
- **Traefik/WireGuard:** router internal hanya di entrypoint `wg-https` yang di-bind ke IP WireGuard. Allowlist outlet dan staf terpisah. XFF hanya dipercaya dari Cloudflare. Firewall hub nftables memblok antar-peer dan hanya mengizinkan DNAT ke 443.
- **Kustodi:** `/opt/efn/secrets` `root:efn-superadmin 0750` (file 0444; risiko residu terbatas pada pemegang root/grup docker dan proses di container yang me-mount secret tsb). Secret dikirim lewat stdin. `hidepid=2` terdokumentasi. Proteksi Environment (reviewer Superadmin, tag `v*`) terdokumentasi.

## UU PDP: status

| Kewajiban | Status | Bukti / sisa |
|---|---|---|
| Consent server-side (versi, hash teks, waktu, sumber, IP terenkripsi) | **Implementasi** | `consent.service.ts`, trigger append-only (SEC-39) |
| Hak akses (ekspor) | **Implementasi BFF; rusak di frontend** | SEC-32; ekspor besar (202) belum |
| Hak hapus lintas store | **Implementasi**; anonimisasi belum tuntas | SEC-31 |
| Koreksi / ganti nomor | **Belum** | `dsar_request.type` sudah disiapkan |
| Retensi (otp_audit, laporan, foto 90 h, TTL mart 5 th) | **Implementasi** (menunggu DPO) | `retention.service.ts`, `clickhouse-init.sh:80-84` |
| Notifikasi insiden 3×24 jam | **Draf kebijakan**; runbook teknis belum | `PDP_BREACH_RESPONSE.md` |
| Inventaris data / RoPA | **Draf v2** (perlu update Redis, reversibilitas `member_hash`) | SEC-24/SEC-31 |
| DPO, kebijakan privasi final, transfer lintas negara | **Belum diputuskan** | `PDP_TRANSFER_LINTAS_NEGARA.md` |

## Prioritas perbaikan
1. **Sebelum go-live:** SEC-32 (satu baris, hak akses), SEC-31 (tombstone acak, purge `member_hash` di `dsar_request`, `request_id` di Odoo), sisa SEC-07 (kunci field HP partner untuk kasir), dan SEC-25 strict mode (**ditangani QA**).
2. **Sebelum vendor payment diaktifkan (gerbang ADR-016):** SEC-33 seluruhnya, izinkan `PAYMENT_PROVIDER=disabled` di deploy (SEC-40), dan uji sandbox.
3. **Sebelum outlet pilot / hardening ops:** SEC-36 (wrapper deploy + forced command), SEC-37 (cakupan token DNS), SEC-35 (Loki di belakang allowlist CN).
4. **Backlog terjadwal:** SEC-34, SEC-38 (keputusan bisnis/DPO), SEC-39, SEC-40, SEC-29, sisa SEC-30. Verifikasi runtime staging untuk item PLAUSIBLE (SEC-03, SEC-28, DNAT IP sumber di `wg-https`, passive declare RabbitMQ).
