# Security & Compliance Re-review (r2) — Gelombang 3 (2026-10-05)

Reviewer: `efn-security-reviewer` (read-only terhadap kode). Ini re-review atas `SECURITY_REVIEW_2026-10-05.md` (r1) setelah Gelombang 3/3b ("enkripsi di setiap transaksi data", least privilege, ADR-022..025).
Cakupan: `services/**`, `infra/**`, `scripts/**`, `.github/workflows/**`, `docs/04 §7`, ADR-022..025, `docs/compliance/*`, laporan agent Gelombang 3/3b.
Metode: membaca kode/konfigurasi dan memverifikasi tiap klaim laporan agent di file sumber. **Tidak ada yang dijalankan** (tanpa Docker/Node/Python/openssl di mesin reviewer); perilaku runtime pihak ketiga yang belum pasti ditandai **PLAUSIBLE**.
Koordinasi: penyelarasan integrasi (nama env, grant `efn_etl` di DB Odoo, SAN sertifikat, CI) dan pengisian tabel "Pengecualian terdaftar" ADR-022 (E1–E10 dari laporan devsecops) **ditangani QA** dan tidak diulang di sini.

## Ringkasan eksekutif (untuk manajemen)

1. **Kemajuan besar.** Dari 19 temuan r1: **15 Fixed, 3 Partially fixed, 1 Open, 0 Regressed.** Ketiga temuan High r1 (Odoo terbuka ke internet, kredensial admin bersama, IP klien salah di rate limit) sudah ditutup di kode/konfigurasi. Rantai deploy kini dipin & diverifikasi per digest.
2. **Enkripsi di setiap transaksi data sudah nyata di kode:** semua klien (BFF, OCR worker, ETL, Odoo, Next.js) memverifikasi sertifikat + hostname terhadap CA internal, tidak ditemukan `rejectUnauthorized:false`/`verify=False` tersembunyi, plaintext hanya lewat flag dev yang ditolak di produksi. Enkripsi kolom PII (AES-256-GCM) diimplementasikan dengan benar.
3. **Tidak ada temuan Critical/High baru.** Ada **11 temuan baru: 2 Medium, 7 Low, 2 Info.** Dua Medium: (a) komponen tanpa autentikasi (Ollama, Loki, exporter metrik) masih bisa diakses container mana pun karena mTLS yang diwajibkan ADR-022 belum dipasang; (b) allowlist "internal" = seluruh `10.0.0.0/8`, sehingga perangkat apa pun di LAN outlet (termasuk Wi-Fi tamu bila tidak dipisah) dapat membuka halaman login Metabase/Grafana/Uptime Kuma/Odoo.
4. **UU PDP:** enkripsi PII, retensi log OTP/outbox, lifecycle foto 12 bulan, dan draf breach-response sudah ada. **Belum:** DSAR (akses/hapus), catatan consent server-side, retensi baris `price_hunter_report` & TTL ClickHouse, finalisasi kebijakan privasi/DPO — tetap wajib sebelum go-live.
5. Rekomendasi: SEC-20 & SEC-21 sebelum staging dibuka ke outlet pilot; sisa Partial (SEC-06/07/11) sebelum go-live.

## Status temuan lama (SEC-01..SEC-19)

| ID | Sev. r1 | Status r2 | Bukti utama (file:line) | Catatan sisa |
|---|---|---|---|---|
| SEC-01 | High | **Fixed** | `infra/compose/docker-compose.yml:570,582,590,597` (semua router Odoo `internal-only` + `secure-headers`; `odoo-login` 10/menit; `/xmlrpc`,`/jsonrpc` → `deny-all`), `infra/traefik/dynamic/middlewares.yml:74-78` (`ipAllowList` tanpa `ipStrategy` = RemoteAddr, benar untuk WireGuard), `services/odoo/addons/efn_setup_security/data/ir_config_parameter.xml:9` (`auth_totp.policy=employee_required`) | Allowlist terlalu luas → SEC-21; jalur in-cluster → SEC-22. Entrypoint `wg-https` ADR-023 belum (diterima: RemoteAddr allowlist efektif) |
| SEC-02 | High | **Fixed** | `infra/postgres/init/00-roles.sh:21-28` (role per service, NOSUPERUSER/NOCREATEROLE), `infra/postgres/pg_hba.conf:12-28` (hostssl+scram per DB/role, superuser TCP ditolak), `infra/minio/minio-init.sh:99-129` (user `efn-bff`/`efn-ocr` per prefix, `pgbackup` tanpa BypassGovernance, object lock), `infra/rabbitmq/rabbitmq-entrypoint.sh:39-49` (tanpa tag administrator), `infra/clickhouse/clickhouse-init.sh:76-91` | Permission RabbitMQ lebih luas dari kontrak → SEC-25 |
| SEC-03 | High (P) | **Fixed** (verifikasi staging) | `infra/traefik/traefik.yml:52-78` (XFF hanya dari CF + 10/8), `docker-compose.yml:684` + `scripts/deploy.sh:100,129` (`TRUSTED_PROXIES` = 172.30.1.0/24 + CF), `src/config/app-config.ts:222-235`, `docker-compose.yml:55-62` (subnet dipin) | Alur CF→Traefik→(Next.js)→BFF menghasilkan `req.ip` = klien. Tetap PLAUSIBLE sampai diuji di staging; firewall origin hanya runbook |
| SEC-04 | Medium | **Fixed** | `services/text2sql/efn_text2sql/guard.py:218-240` (IN hanya subquery/literal), `:242-310` (resolusi CTE per scope, tolak shadowing & RECURSIVE), `infra/clickhouse/access/text2sql-access.sql:12-51` + `clickhouse-init.sh:80-91` (lapisan 2 otomatis) | — |
| SEC-05 | Medium | **Fixed** | `infra/redis/redis-entrypoint.sh:23-45` (user `default` off, ACL per prefix, TLS-only, `noeviction`), `docker-compose.yml:67-70,384-395` (network `cache`) | PII plaintext di Redis → SEC-24 |
| SEC-06 | Medium | **Partially fixed** | `src/common/pii/pii.ts` (62-only), `src/otp/otp.service.ts:192-201` (budget global per purpose), `src/auth/auth.service.ts:62-79` (kuota per nomor+IP, plafon per nomor) | Captcha/Turnstile belum; `/v1/auth/*` masih dapat dipanggil langsung di `api.` (`docker-compose.yml:756-758`); penyerang ≥10 IP masih bisa menahan nomor korban di plafon per nomor |
| SEC-07 | Medium | **Partially fixed** | `efn_pos_otp_gate/models/pos_config.py:92-175` (nomor dari `res.partner`, bukan input kasir), `models/pos_order.py:177-193` (consume membawa `member_ref` partner final → `409 member_mismatch`) | `amount` tidak diikat ke token (`src/pos/README.md:27`); kasir masih bisa membuat/mengedit partner dengan nomor HP sendiri lalu memakainya (hak buat/edit partner POS belum dibatasi — tindak lanjut efn-odoo-functional) |
| SEC-08 | Medium | **Fixed** | `src/loyalty/loyalty-earn.consumer.ts:57-71` (cek `user_id` AMQP per source, report `valid`, hash cocok), `src/common/amqp/amqp.service.ts:72-91`, `LOYALTY_MAX_POINTS_PER_EVENT=100` | Sisa diterima: worker terkompromi dapat menandai report miliknya `valid` (≤100 poin/report, cap harian 20) |
| SEC-09 | Medium | **Fixed** | `docker-compose.yml:1371-1403` (tanpa docker.sock/rootfs, socket-proxy via mTLS, `cap_drop: ALL`, listen loopback) | cAdvisor-tls tanpa autentikasi klien → SEC-20 |
| SEC-10 | Medium | **Fixed** | `.github/workflows/*.yml` semua `uses:` dipin SHA, `deploy.yml:41-73` (input lewat env + regex), `:94-116` (identitas cosign `build-push.yml@refs/heads/main`/`refs/tags/vX.Y.Z`, digest tunggal), `scripts/deploy.sh:28-31,160-161` (deploy by digest) | `semgrep:latest` belum dipin; proteksi branch GitHub Environment tidak dapat diverifikasi dari repo (SEC-30) |
| SEC-11 | Medium | **Partially fixed** | `src/common/retention/retention.service.ts:5-20` (purge otp_audit/processed_message/outbox), `minio-init.sh:69-73` (foto 365 hari), `infra/postgres/init/01-databases.sql` (`user_hash`+`user_enc`, `ip_enc`) | DSAR & consent = desain (`docs/compliance/PDP_DSAR_DESIGN.md:3`, `PDP_CONSENT.md:3`); baris `price_hunter_report` tanpa retensi (`retention.service.ts:19`); ClickHouse tanpa `TTL` (`infra/clickhouse/init/*`); `PDP_DATA_INVENTORY.md:58` masih menulis "belum ada lifecycle" (usang) |
| SEC-12 | Low (P) | **Fixed** | `efn_pos_otp_gate/models/pos_order.py:88-123` (gate berdasar program/produk diskon ber-OTP, termasuk arsip; flag klien diabaikan) | Diskon manual/ubah harga oleh kasir di luar gate (kontrol POS "Restrict price modification" — functional) |
| SEC-13 | Low | **Fixed** | `services/superapp-web/lib/safe-next.ts:31-56` (NFKC, kontrol/whitespace/backslash mentah & ter-decode, origin sentinel, kanonik bukan `//`) | — |
| SEC-14 | Low | **Fixed** | `docker-compose.yml:756-758` (`!PathPrefix(/metrics,/v1/pos,/internal)`) | `GET /v1/pos/loyalty/balance?member_ref=` (`src/pos/pos.controller.ts:37-40`) kini hanya in-cluster; dukungan dua service key untuk rotasi belum |
| SEC-15 | Low | **Fixed** | `services/odoo/render_odoo_conf.py:87-121,229-269` (configparser, tolak kontrol/literal khusus, hash pbkdf2, 0600 atomik, gagal bila secret kosong), `entrypoint-efn.sh:33` | Fallback plaintext bila passlib hilang (SEC-30) |
| SEC-16 | Low | **Fixed** | `docker-compose.yml:1021` (etl tanpa `otp_pepper`), ETL membaca `user_hash` dari PG | — |
| SEC-17 | Low | **Fixed** | `src/market-scout/dto/report.dto.ts:36-48` (`roundCoord` 3 desimal di server) | — |
| SEC-18 | Info | **Fixed** | `docker-compose.yml:355-359`, `docker-compose.prod.yml:39-43`, `docker-compose.dev.yml:41-45` (`log_parameter_max_length*=0`) | Dev masih `log_statement=all` (parameter tidak dicetak; teks SQL literal tetap) |
| SEC-19 | Info | **Open** | `src/market-scout/image/image-processor.ts:16,27` (50 MP, tanpa semaphore/`sharp.concurrency`) | Prod kini 2 replika × 1 GiB (`docker-compose.prod.yml:83-90`) mengurangi dampak, belum menutup |

**Rekap:** Fixed 15 · Partially fixed 3 (SEC-06, 07, 11) · Open 1 (SEC-19) · Regressed 0.

## Temuan baru

| ID | Severity | Judul | Lokasi utama | Risiko |
|---|---|---|---|---|
| SEC-20 | Medium | mTLS/autentikasi belum ada pada hop tanpa kredensial aplikasi (Ollama, cAdvisor, exporter, Loki, Prometheus) — bertentangan ADR-022 Keputusan 2 | `docker-compose.yml:1077,1416`, `infra/observability/web-config.yml:4-7`, `infra/observability/loki.yml:1` | R-S01, R-S06, R-T |
| SEC-21 | Medium | `internal-only` = seluruh `10.0.0.0/8` (+ XFF dipercaya dari 10/8): LAN outlet dapat menjangkau bi./ops./status./erp. dan memalsukan IP untuk rate limit `api.` | `infra/traefik/dynamic/middlewares.yml:74-78`, `infra/traefik/traefik.yml:54`, `infra/wireguard/wg0-server.conf.example:4-8` | R-S03, R-S10 |
| SEC-22 | Low | Odoo dapat dijangkau langsung dari container mana pun di network `internal`/`data` lewat sidecar tanpa autentikasi (melewati blok RPC, rate limit, header proxy dipercaya); `/json/2/` Odoo 19 tidak ikut diblok | `docker-compose.yml:557,616-620,639-643`, `infra/traefik/dynamic/middlewares.yml:26-31`, `services/odoo/odoo.conf.tpl:12` | R-S03 |
| SEC-23 | Low | PKI menyimpang dari ADR-022: CA satu tingkat tanpa `nameConstraints`, leaf 365 hari, CA internal ikut dipercaya untuk egress publik BFF (`NODE_EXTRA_CA_CERTS`), `PKI_SOURCE=host` tidak dibatasi ke staging | `scripts/gen-pki.sh:24-25,75-86`, `docker-compose.yml:679`, `scripts/deploy.sh:42-45` | R-S01, R-T07 |
| SEC-24 | Low | PII plaintext di Redis (nomor HP challenge login, IP di nama key rate limit) dengan AOF persisten | `src/otp/otp.service.ts:102`, `src/auth/auth.service.ts:62-63,79`, `infra/redis/redis-entrypoint.sh:43` | R-L01 |
| SEC-25 | Low | Permission RabbitMQ lebih luas dari kontrak §7.3: `efn-ocr` configure+read pada `loyalty.earn.requested`/`notify.push`(+DLQ), `efn-bff` read `ocr.requested` | `infra/rabbitmq/rabbitmq-entrypoint.sh:35,47-48` | R-S10, R-B05 |
| SEC-26 | Low | `00-roles.sh` mengirim password role lewat argv `psql -v pw=…` (terlihat di `ps` host saat deploy), bertentangan dengan komentarnya | `infra/postgres/init/00-roles.sh:43-46` | R-S01 |
| SEC-27 | Low | OCR worker & ETL *fail-open* bila `APP_ENV`/`NODE_ENV` kosong (`EFN_ALLOW_PLAINTEXT` diterima), tidak konsisten dengan BFF/Odoo/Next.js yang *fail-closed* | `services/ai-ocr-worker/efn_ocr/tls.py:28-37`, `services/etl/efn_etl/tls.py:25-33` | R-L01 |
| SEC-28 | Low (PLAUSIBLE) | Isolasi peer & firewall hub WireGuard belum dikodekan; `PostUp ip_forward=0` bertentangan dengan Docker; kunci privat peer dibuat di host (ADR-023 mensyaratkan di perangkat) | `infra/wireguard/wg0-server.conf.example:16-17`, `infra/wireguard/gen-peer.sh:24-31` | R-S03, R-T02 |
| SEC-29 | Info | Kunci PII & secret lain disalin ke `process.env` BFF (terlihat di `/proc/<pid>/environ`, diwariskan ke child process) | `src/config/secrets.loader.ts:47-70` | R-S01 |
| SEC-30 | Info | Catatan kecil: fallback `admin_passwd` plaintext bila passlib hilang; `toJSON(secrets)` di satu step; `semgrep:latest`; proteksi branch environment; dokumen PDP usang; metrik MinIO/ClickHouse/BFF tanpa auth | lihat detail | R-S07 |

---

## Detail temuan baru

### SEC-20 — Medium — mTLS belum dipasang pada hop tanpa kredensial aplikasi
**Lokasi:** `infra/compose/docker-compose.yml:1077` (`ollama-tls … --disable-authentication`), `:1416` (`cadvisor-tls … --disable-authentication`), `infra/observability/web-config.yml:4-7` (hanya `cert_file/key_file`, tanpa `client_ca_file`/`client_auth_type`), `infra/observability/loki.yml:1` (`auth_enabled: false`, `http_tls_config` tanpa client auth), `docker-compose.yml:479` (`MINIO_PROMETHEUS_AUTH_TYPE: public`).
**Kondisi:** ADR-022 Keputusan 2: *"mTLS wajib untuk hop yang tidak punya kredensial aplikasi (exporter metrik, sidecar ghostunnel → app)"*. Implementasi hanya server-auth TLS: koneksi terenkripsi, tetapi **siapa pun** di network yang sama boleh memanggil.
**Skenario:**
- Kompromi container mana pun di network `internal` (mis. `superapp-web` lewat bug Next.js, Metabase, Uptime Kuma) → `POST https://ollama:11434/api/delete {"model":"qwen2.5vl:7b"}` atau `/api/pull` model besar (disk penuh) → pipeline OCR Price Hunter mati / disk node AI penuh. Ollama tidak punya autentikasi sama sekali, jadi ghostunnel adalah satu-satunya tempat kontrol akses.
- `POST https://loki:3100/loki/api/v1/push` dari container mana pun → menyuntik log palsu (menutupi jejak insiden, memicu/menyembunyikan alert berbasis log); `GET /loki/api/v1/query_range` → membaca seluruh log semua service (request id, nomor ter-mask, error internal).
- Exporter/Prometheus: pembacaan metrik bebas (rekon topologi, nama container, volume OTP).
**Rekomendasi:** ghostunnel `ollama-tls`: ganti `--disable-authentication` dengan `--allow-cn=ocr-worker` (+ `prometheus` bila di-scrape); `cadvisor-tls`: `--allow-cn=prometheus`. `web-config.yml`: tambah `client_ca_file: /run/secrets/efn_ca_crt` + `client_auth_type: RequireAndVerifyClientCert`, dan berikan Prometheus `cert_file/key_file` di `tls_config` scrape (sertifikat `tls_prometheus_*` sudah ada dengan EKU clientAuth). Loki: `http_tls_config.client_auth_type: RequireAndVerifyClientCert` + `client_ca_file`, Promtail/Grafana memakai sertifikat klien. Tambahkan cek di `scripts/check-encryption.sh`: `--disable-authentication` hanya diizinkan untuk sidecar yang app-nya punya autentikasi sendiri (Odoo, Next.js, Metabase) dan dicatat di tabel pengecualian ADR-022 (ditangani QA).

### SEC-21 — Medium — Allowlist "internal" seluas 10.0.0.0/8
**Lokasi:** `infra/traefik/dynamic/middlewares.yml:74-78` (`internal-only` = `10.0.0.0/8`), dipakai router `odoo*`, `bi`, `ops`, `status` (`docker-compose.yml:570,582,597,952,1269,1298`); `infra/traefik/traefik.yml:54` (`forwardedHeaders.trustedIPs: 10.0.0.0/8`); `infra/wireguard/wg0-server.conf.example:4-8` (staf `10.88.1.0/24` dan outlet `10.88.10.x` di /16 yang sama).
**Kondisi:** ADR-023 butir 3 memisahkan outlet (`10.88.0.0/16`) dari staf back-office (`10.89.0.0/16`, "plus Grafana/Metabase sesuai peran") dan butir B1 mencatat bahwa router outlet membuat **semua perangkat LAN outlet** ikut terpercaya. Middleware tidak membedakan sumber: peer outlet = staf IT = node mesh.
**Skenario:**
1. Perangkat di LAN outlet (tablet kasir yang terinfeksi, atau Wi-Fi tamu bila tidak di-VLAN-kan) → `https://bi.<domain>` (Metabase, login password saja, riwayat CVE pra-autentikasi), `https://ops.<domain>` (Grafana admin), `https://status.<domain>` (Uptime Kuma: bila belum di-setup, pengunjung pertama membuat akun admin) → brute force / eksploitasi pra-auth dari 50+ outlet.
2. Peer outlet mengirim `X-Forwarded-For: <acak>` ke `api.<domain>` → Traefik mempercayainya (10/8 trusted), `ratelimit-api` (`ipStrategy.depth: 1`) menghitung IP palsu → rate limit Traefik dilewati. (BFF tidak terdampak: `TRUSTED_PROXIES` tidak memuat 10/8, sehingga `req.ip` = IP peer.)
**Rekomendasi:** pecah middleware: `internal-erp` = subnet outlet + staf + node; `internal-ops` = subnet staf IT/back-office saja untuk `bi.`/`ops.`/`status.`. Terapkan alokasi ADR-023 (outlet 10.88/16, staf 10.89/16) di `gen-peer.sh`/`wg0-server.conf.example`. Persempit `forwardedHeaders.trustedIPs` ke rentang Cloudflare saja (akses WireGuard langsung tidak lewat proxy, jadi tidak perlu XFF). Wajibkan VLAN POS terpisah dari Wi-Fi tamu di SOP pemasangan router outlet. Pastikan Uptime Kuma di-setup (akun admin) di pipeline deploy, bukan manual.

### SEC-22 — Low — Odoo terjangkau in-cluster tanpa lewat Traefik
**Lokasi:** `docker-compose.yml:557` (odoo di `internal` + `data`), `:616-620` & `:639-643` (ghostunnel `--listen=0.0.0.0:8443/8444 --disable-authentication`), `infra/traefik/dynamic/middlewares.yml:26-31` (blok RPC hanya di Traefik; komentar menyebut "service internal yang butuh RPC memanggil Odoo langsung"), `services/odoo/odoo.conf.tpl:12` (`proxy_mode = True`), `docker-compose.yml:585-591` (blok hanya `/xmlrpc`, `/jsonrpc`).
**Kondisi & skenario:** container terkompromi di network `data` (ClickHouse, ETL, OCR worker, RabbitMQ, MinIO, exporter) atau `internal` memanggil `https://odoo:8443/xmlrpc/2/common` / `/jsonrpc` / `/web/session/authenticate` langsung: tidak ada rate limit login, tidak ada blok RPC, dan header `X-Forwarded-For/Host/Proto` dipercaya Odoo (`proxy_mode`) sehingga IP di log/cooldown login dapat dipalsukan dan URL yang dibangun dari host (mis. tautan reset) dapat diracuni. Apakah password RPC user internal yang memakai 2FA **email** (`employee_required` tanpa aplikasi TOTP) masih diterima lewat XML-RPC belum dapat dipastikan (PLAUSIBLE; `auth_totp` menolak password RPC untuk user ber-TOTP). Odoo 19 juga memperkenalkan API `/json/2/` (berbasis API key) yang tidak ada di daftar blok.
**Rekomendasi:** `odoo-tls`: `--allow-cn=traefik` (+ CN service teknis yang memang perlu RPC, ADR-024 butir 5) sehingga hanya Traefik yang mencapai Odoo; pertimbangkan mengeluarkan Odoo dari network `data` dengan network `odoo-db` khusus (odoo + postgres + etl). Tambahkan `PathPrefix(/json/2)` ke router blok (atau dokumentasikan bila sengaja dipakai). Verifikasi di staging bahwa user `employee_required` tanpa TOTP ditolak saat XML-RPC dengan password.

### SEC-23 — Low — Penyimpangan PKI dari ADR-022
**Lokasi:** `scripts/gen-pki.sh:24-25` (`CA_DAYS=3650`, `LEAF_DAYS=365`), `:75-86` (CA tunggal, `pathlen:0`, tanpa `nameConstraints`), `:137-145` (setiap leaf ber-SAN `localhost`, `127.0.0.1`, EKU server+client); `docker-compose.yml:679` (`NODE_EXTRA_CA_CERTS` di api-gateway); `scripts/deploy.sh:42-45` (`PKI_SOURCE=host` menyimpan kunci CA di `/opt/efn/pki-ca` tanpa memeriksa `APP_ENV`).
**Kondisi:** ADR-022 menetapkan root offline + intermediate per lingkungan dengan `nameConstraints` (nama service + `.efn.internal`) dan leaf 90 hari. Implementasi saat ini satu tingkat, tanpa batasan nama, leaf 1 tahun (devsecops sudah mencatatnya sebagai sisa pekerjaan, G3.3). Karena `NODE_EXTRA_CA_CERTS` **menambah** CA internal ke trust store Node untuk *semua* koneksi BFF, siapa pun yang memegang kunci CA dapat menerbitkan sertifikat `graph.facebook.com` yang diterima BFF.
**Skenario:** kunci CA bocor (laptop admin, host staging dengan `PKI_SOURCE=host`, atau `PKI_SOURCE=host` keliru diset di environment production) + posisi jaringan di egress → MITM panggilan BFF → WhatsApp Cloud API → `WHATSAPP_TOKEN` dan isi pesan OTP terbaca.
**Rekomendasi:** tambahkan `nameConstraints = critical,permitted;DNS:<daftar service>,DNS:.efn.internal,DNS:localhost,IP:127.0.0.1/255.255.255.255` pada CA (atau intermediate) — langkah murah yang menutup skenario di atas; hapus `NODE_EXTRA_CA_CERTS` dari api-gateway (BFF sudah memakai CA eksplisit per klien di `app-config.ts:260-278`; `healthcheck.js` memakai `HEALTHCHECK_CA_FILE`); turunkan `LEAF_DAYS` ke 90 setelah timer rotasi ada; `deploy.sh` menolak `PKI_SOURCE=host` bila `APP_ENV=production`.

### SEC-24 — Low — PII plaintext di Redis
**Lokasi:** `src/otp/otp.service.ts:102` (`fields.m = input.memberRef` untuk challenge login), `src/auth/auth.service.ts:62-63,79` (key `otp:login:ip:<ip>`, `…member-ip:<hash>:<ip>`, `verify-ip:<ip>`), `infra/redis/redis-entrypoint.sh:43` (`--appendonly yes`).
**Kondisi:** ADR-022 S2 mengenkripsi IP (`ip_enc`) dan nomor HP di PostgreSQL, tetapi nilai yang sama tersimpan plaintext di Redis dan ditulis ke AOF di volume `redis-data` (sampai AOF di-rewrite). Backup/snapshot volume atau akses ke host membuka nomor HP + IP pelanggan; ini juga tidak tercantum di `PDP_DATA_INVENTORY.md`.
**Rekomendasi:** simpan `m` sebagai ciphertext `PiiCipher` (AAD mis. `efn:redis_otp.m:v1`) atau sebagai referensi yang hanya bisa dibalik BFF; pakai `HMAC(otpCodeKey, ip)` untuk nama key rate limit; pertimbangkan `--appendonly no --save ""` (semua state Redis berumur ≤ 24 jam dan boleh hilang saat restart, tetapi pertimbangkan dampak reset rate limit). Catat Redis di inventaris data PDP.

### SEC-25 — Low — Permission RabbitMQ lebih luas dari kontrak
**Lokasi:** `infra/rabbitmq/rabbitmq-entrypoint.sh:35` (regex `Q` mencakup ketiga queue + DLQ), `:47` (`efn-bff` read `ocr.requested`), `:48` (`efn-ocr` configure+read `loyalty.earn.requested`, `notify.push`, `.dlq`).
**Kondisi:** kontrak §7.3: `efn-ocr` hanya *read* `ocr.requested` dan *write* ke tujuan publish. Izin *configure* memungkinkan `queue.delete`; *read* memungkinkan `basic.consume`/`queue.purge`.
**Skenario:** OCR worker terkompromi (memproses gambar tak tepercaya) → `queue.delete loyalty.earn.requested.dlq` (menghapus bukti pesan gagal) atau mengonsumsi `notify.push`/`loyalty.earn.requested` sehingga pesan sah tidak pernah sampai ke BFF (poin pelanggan & notifikasi hilang diam-diam).
**Rekomendasi:** sesuai catatan devsecops: worker memakai *passive declare* untuk queue tujuan; `efn-ocr` configure/read hanya `^ocr\.requested(\.dlq)?$`; `efn-bff` read hanya `^(loyalty\.earn\.requested|notify\.push)$` (+ configure untuk deklarasi). Queue & DLQ dideklarasikan lewat definitions (bukan oleh klien) agar configure dapat dicabut seluruhnya.

### SEC-26 — Low — Password role PostgreSQL di argv
**Lokasi:** `infra/postgres/init/00-roles.sh:43-46` (`psql … -v pw="$pw"`), dijalankan tiap deploy (`scripts/deploy.sh:185`).
**Kondisi & dampak:** komentar menyatakan password "tidak lewat argv", padahal `-v pw=…` adalah argumen proses `psql` — terlihat di `/proc/<pid>/cmdline` dan `ps -ef` di host (proses container terlihat dari host) selama eksekusi, untuk enam password service.
**Rekomendasi:** kirim lewat stdin, mis. `psql … -v role="$role" -v attrs="$attrs" <<SQL` dengan baris pertama `\set pw '<escaped>'` dibangun dari file, atau gunakan `\getenv pw PGPW_TMP` (psql ≥ 15) dengan env yang hanya diset untuk proses tersebut; perbaiki komentar.

### SEC-27 — Low — Python services fail-open saat `APP_ENV` kosong
**Lokasi:** `services/ai-ocr-worker/efn_ocr/tls.py:28-37`, `services/etl/efn_etl/tls.py:25-33` (`is_production` hanya true bila `APP_ENV`/`NODE_ENV` ∈ {production, prod}).
**Kondisi:** BFF (`app-config.ts:210-213`, NODE_ENV kosong = production), Odoo (`render_odoo_conf.py:63-74`, `efn_bff_client.py:48-55`) dan Next.js fail-closed; worker/ETL menerima `EFN_ALLOW_PLAINTEXT=true` bila `APP_ENV` lupa diisi. Saat ini termitigasi oleh default compose `APP_ENV=${APP_ENV:-production}` (`docker-compose.yml:1000,1103`) dan `check-encryption.sh:45`.
**Rekomendasi:** samakan semantik: plaintext hanya bila `APP_ENV` ∈ allowlist non-produksi eksplisit (seperti Odoo).

### SEC-28 — Low (PLAUSIBLE) — Hub WireGuard: isolasi peer belum dikodekan
**Lokasi:** `infra/wireguard/wg0-server.conf.example:16-17` (`PostUp = sysctl -w net.ipv4.ip_forward=0`), `infra/wireguard/gen-peer.sh:24-31` (kunci privat peer dibuat di host).
**Kondisi:** ADR-023 butir 3 mensyaratkan firewall hub (peer outlet hanya ke `<IP WG edge>:443`, tanpa SSH/DB/antar-outlet). Satu-satunya kontrol di repo adalah `ip_forward=0`, padahal Docker di host yang sama menyalakan `ip_forward=1` (dan membutuhkannya), sehingga baris itu akan tertimpa atau mematikan jaringan container. Port host lain (SSH, 51820, port dev) terjangkau dari peer. ADR-023 butir 5 meminta kunci privat dibuat di perangkat; skrip membuatnya di host lalu mengirim berkas `.conf` berisi kunci privat.
**Rekomendasi:** ganti `PostUp` dengan aturan nftables/iptables eksplisit: `wg0` → hanya `tcp dport 443` ke IP WG host; `wg0 → wg0` drop; `wg0` → `docker*`/`br-*` drop; SSH hanya dari subnet staf IT. Tambahkan mode `gen-peer.sh --pubkey <key>` untuk peer yang membuat kunci sendiri (router MikroTik/OpenWrt mendukung).

### SEC-29 — Info — Secret disalin ke `process.env`
**Lokasi:** `src/config/secrets.loader.ts:47-70` (`env[target] = …` untuk `PII_ENCRYPTION_KEY`, `OTP_PEPPER`, `JWT_SECRET`, password, dll.).
**Catatan:** nilai terlihat di `/proc/<pid>/environ` (user yang sama) dan diwariskan ke setiap child process (mis. `sharp`/libvips tidak spawn, tetapi dependensi masa depan bisa). Bukan celah langsung; untuk kunci PII lebih baik dibaca sebagai `Buffer` seperti material TLS (`isTlsMaterialFileVar`) dan tidak pernah masuk env.

### SEC-30 — Info — Catatan kecil
- `services/odoo/render_odoo_conf.py:117-120`: bila `passlib` tidak ada, `admin_passwd` ditulis plaintext (file tetap 0600). Lebih aman gagal start di `APP_ENV=production`.
- `.github/workflows/deploy.yml:152` `ALL_SECRETS: ${{ toJSON(secrets) }}` membuat seluruh secret repo/org/environment tersedia di env satu step (tanpa action pihak ketiga di step itu, dan semua action dipin SHA — dapat diterima); `security.yml:36` `semgrep/semgrep:latest` belum dipin digest (diakui devsecops).
- Proteksi "deployment branches" GitHub Environment `staging`/`production` tidak dapat diverifikasi dari repo: tanpa itu, `workflow_dispatch` deploy dari branch lain menjalankan `scripts/deploy.sh` versi branch tersebut di host. Wajibkan hanya `main`/tag `v*`.
- `scripts/deploy.sh:146` `ACME_EMAIL` dimasukkan ke `sed` tanpa validasi (sumber: repo variable, admin saja).
- Endpoint metrik tanpa autentikasi: MinIO (`docker-compose.yml:479`), ClickHouse handler `/metrics` di 8443, BFF `/metrics` (in-cluster saja) — ikut diselesaikan bersama SEC-20 bila memungkinkan.
- `docs/compliance/PDP_DATA_INVENTORY.md:58` masih menyatakan bucket foto tanpa lifecycle — sudah ada (`minio-init.sh:69-73`); perbarui.
- `pg_hba.conf` memakai `ADDRESS all`; dapat dipersempit ke `172.30.2.0/24` (subnet `data`) sebagai lapisan tambahan.
- Healthcheck ClickHouse `--no-check-certificate` (loopback) dan healthcheck HTTP loopback lain adalah E2/E3 di daftar pengecualian — **ditangani QA** di tabel ADR-022.
- `docker-socket-proxy` `BIND_CONFIG=127.0.0.1:2375` (PLAUSIBLE, belum dipastikan didukung image 0.3.0): bila tidak, haproxy listen plaintext di network `socket-proxy` (hanya Traefik/Promtail/cAdvisor) — cek `ss -ltn` di container saat staging.

---

## Verifikasi fokus Gelombang 3 (ringkas)

**Kriptografi PII (`src/common/pii/crypto.ts`)** — benar:
- IV 12 byte `randomBytes` per enkripsi (`:70`); kunci wajib tepat 32 byte (`:58`, `app-config.ts:368-376`, base64 divalidasi); tag 16 byte dipaksa di encrypt & decrypt (`:71,86`); AAD `efn:<tabel>.<kolom>:v1` dengan identifier tervalidasi (`:32-35`), dipakai konsisten untuk tiga kolom (`:38-42`); copy antar-kolom = decrypt+encrypt ulang (`loyalty-earn.consumer.ts:76`).
- Versi: byte pertama memilih kunci, versi tanpa kunci → error, tidak ada fallback (`:80-81`); versi aktif harus ada di ring (`:61`, `app-config.ts:396-400`).
- Error oracle: semua kegagalan GCM disatukan menjadi satu pesan generik (`:90-92`); pesan tidak memuat plaintext/kunci. Tidak ada log ciphertext/kunci.
- Kunci di memori: disalin ke `Map` privat; sumbernya ada di `process.env` → SEC-29 (Info). Batas 2^32 enkripsi per kunci dengan IV acak jauh di atas volume EFN.

**TLS klien (verifikasi hostname aktif)** — tidak ditemukan `rejectUnauthorized:false`, `verify=False`, `check_hostname=False` (kecuali healthcheck loopback `efn_ocr/healthcheck.py:20`, terdokumentasi), `NODE_TLS_REJECT_UNAUTHORIZED` (justru ditolak di produksi, `superapp-web/lib/bff-config.ts:83-85`), atau `insecure_skip_verify: true`.
- pg: `ssl: { ca, rejectUnauthorized: true, minVersion TLSv1.2, servername }` (`app-config.ts:260-278`, `pg.provider.ts:15-27`; field eksplisit, bukan connectionString).
- ioredis: `tls` dengan `servername` (`redis.provider.ts:14-20`); amqplib: opsi socket TLS + `servername` (`amqp.service.ts:115-118`); MinIO: `https.Agent` CA + `rejectUnauthorized` (`object-storage.ts:24-32`).
- Python: `ssl.create_default_context(cafile)` + `check_hostname=True` + `CERT_REQUIRED` + TLS ≥1.2 (`efn_ocr/tls.py:57-66`), pika `server_hostname` (`publisher.py:36`), psycopg `sslmode=verify-full` + `sslrootcert` (`repository.py:111-123`, `etl/adapters.py:26-34`), clickhouse-connect `verify=True, ca_cert` (`etl/adapters.py:58-61`), urllib3 MinIO `ca_certs` (`efn_ocr/storage.py:18-28`), requests Ollama `verify=<CA>` (`vlm.py:68`), Odoo `requests.post(verify=<CA>)`, `allow_redirects=False` (`efn_bff_client.py:189-198,254-261`).
- Server: PostgreSQL `ssl_min_protocol_version=TLSv1.2`; Redis `--tls-protocols "TLSv1.2 TLSv1.3"`; RabbitMQ tlsv1.2/1.3; ClickHouse menonaktifkan ≤TLS1.1 (`config.d/tls.xml:25`); Traefik publik TLS1.2 + AEAD (`middlewares.yml:84-95`); exporter `min_version: TLS12`.
- Gating `EFN_ALLOW_PLAINTEXT`: BFF (`app-config.ts:239-253`), Next.js, Odoo fail-closed; Python fail-open bila `APP_ENV` kosong → SEC-27. CI lint `scripts/check-encryption.sh` menolak flag & URL plaintext di compose.

**PKI** — kunci CA di `secrets/pki-ca/` (`gen-pki.sh:23,63`), **tidak** ada di daftar secret compose dan dikecualikan dari rsync deploy (`deploy.yml:140`) → tidak pernah di-mount ke service. Kunci CA 0600, dir 0700; CA `basicConstraints critical,CA:TRUE,pathlen:0`, `keyUsage keyCertSign,cRLSign`; leaf `CA:FALSE`, `digitalSignature,keyAgreement`, EKU server+client, serial acak 128-bit. Kunci leaf 0644 di dir 0700 (kebutuhan bind mount non-swarm, terdokumentasi). Penyimpangan → SEC-23.

**Sidecar** — app benar-benar bind loopback: Odoo `http_interface=127.0.0.1` (dipaksa `render_odoo_conf.py:176-187`, juga berlaku untuk `gevent_port`), Next.js `HOSTNAME=127.0.0.1`, Metabase `MB_JETTY_HOST=127.0.0.1`, Ollama `OLLAMA_HOST=127.0.0.1:11435`, cAdvisor `--listen_ip=127.0.0.1`. Status ghostunnel loopback. docker-socket-proxy mTLS `--allow-cn=traefik|promtail|cadvisor` (`docker-compose.yml:228-230`) — benar. `--disable-authentication` pada sidecar lain → SEC-20/SEC-22.

**Traefik** — `ipAllowList` tanpa `ipStrategy` memakai RemoteAddr: perubahan dari `depth: 1` (r1, fail-closed) ke RemoteAddr **benar** untuk akses WireGuard langsung dan tidak dapat dipalsukan lewat XFF. Blok `/xmlrpc`,`/jsonrpc` prioritas 300 → `deny-all` 403; rate limit login 10/menit/peer (RemoteAddr). `forwardedHeaders.trustedIPs` = CF (disinkronkan & dicek CI) + 10/8 → SEC-21.

**Data store** — pg_hba hostssl+scram per DB/role, `hostnossl … reject`, superuser hanya socket; role tanpa SUPERUSER/CREATEROLE (`efn_odoo` CREATEDB sesuai kontrak). Redis ACL `default off`, prefix `~otp:* ~dtok:* ~rl:* ~ms:*`, `-@admin -@dangerous`, `noeviction`. MinIO: policy per prefix, root hanya `minio-init`, `pgbackup` tanpa `BypassGovernanceRetention`/`DeleteObjectVersion`, SSE-S3, object lock governance. ClickHouse: user per service, profil `readonly = 2 CONST`, `allow_ddl/allow_introspection_functions = 0 CONST`, kuota text2sql, grant kolom tanpa identitas pseudonim.

**Odoo** — `render_odoo_conf.py`: hash pbkdf2_sha512 600k, file 0600 atomik, env `PASSWORD`/`PGPASSWORD`/`ODOO_ADMIN_PASSWORD` ditolak, user `postgres` ditolak, `PGSSLMODE` konflik ditolak, `ODOO_DEV_MODE` ditolak di produksi. Gate OTP: SEC-07 (Partial), SEC-12 (Fixed). Kill switch `efn.pos.otp_required_for_promo` (`pos_order.py:85-86`) hanya dapat diubah `base.group_system`; mematikannya juga mematikan gate server — risiko terdokumentasi di README modul; perubahan `ir.config_parameter` belum diaudit (catatan efn-odoo-functional E-3).

**Frontend** — `safe-next.ts` tidak ditemukan bypass (diuji mental: `/%09/x`, `/%2F%2Fx`, `/\x`, `/.//x`, full-width solidus, `/%252F%252Fx` → tetap path relatif). Cookie `efn_session` `HttpOnly; Secure` (selalu di `NODE_ENV=production`, `session.ts:14-18`), `SameSite=Lax`.

**CI/CD** — pin SHA semua `uses:`, identitas cosign per ref, digest tunggal & deploy by digest, input lewat `env` + regex, `persist-credentials: false`, SSH tanpa action pihak ketiga, `StrictHostKeyChecking=yes`, secret via stdin. Sisa: SEC-30.

## UU PDP — tertutup vs masih desain

| Kewajiban / kontrol | Status | Bukti |
|---|---|---|
| Enkripsi PII at rest (nomor HP, IP) di platform | **Implementasi** | `user_enc`, `member_ref_enc`, `ip_enc` + `crypto.ts`; event v2 tanpa nomor HP (`notify-push.consumer.ts:21-38`) |
| Enkripsi in transit semua hop internal | **Implementasi** (verifikasi runtime di staging) | lihat bagian TLS |
| Minimisasi lokasi | **Implementasi** | `roundCoord` 3 desimal |
| Retensi otp_audit 90 h, outbox/processed_message 14 h, foto 365 h | **Implementasi** | `retention.service.ts`, `minio-init.sh:69-73` |
| Retensi baris `price_hunter_report`, ledger (pseudonim), ClickHouse `TTL`, Redis | **Belum** | SEC-11, SEC-24 |
| Log tanpa PII (parameter SQL, masking) | **Implementasi** | SEC-18, `maskMsisdn` di semua log |
| Hak subjek data (akses, hapus, koreksi) — DSAR | **Desain saja** | `docs/compliance/PDP_DSAR_DESIGN.md` (endpoint usulan, belum di `docs/04`) |
| Bukti consent server-side (versi teks, waktu, tujuan) | **Desain saja** | `docs/compliance/PDP_CONSENT.md` |
| Notifikasi kegagalan pelindungan 3×24 jam | **Draf kebijakan**; runbook teknis `security-incident.md` belum | `PDP_BREACH_RESPONSE.md` |
| Inventaris data / RoPA | **Draf** (perlu update: Redis, lifecycle) | `PDP_DATA_INVENTORY.md` |
| Kebijakan privasi final, DPO, transfer lintas negara (Meta) | **Belum ditetapkan** | `app/privasi/page.tsx` (draf) |
| LUKS disk node | **Runbook** (tidak dapat diverifikasi dari repo) | `docs/runbooks/luks-disk.md` |

## Kontrol yang sudah benar (untuk manajemen)
- **Kriptografi aplikasi**: AES-256-GCM kolom PII dengan IV acak, AAD per kolom, key ring berversi tanpa fallback; OTP HMAC + HKDF, atomik, sekali pakai; discount token terkunci HS256, terikat order **dan kini member**.
- **Enkripsi di setiap hop**: verifikasi CA + hostname di semua klien yang ditinjau (Node, Python, Odoo, Next.js, Traefik `serversTransport.serverName`), TLS ≥1.2, tidak ada port plaintext di network (Redis `port 0`, RabbitMQ `listeners.tcp = none`, ClickHouse 8123/9000 dihapus), lint CI anti-plaintext.
- **Least privilege**: role/user per service di PostgreSQL, MinIO, RabbitMQ, Redis, ClickHouse; superuser/root hanya bootstrap; kredensial admin tidak di-mount ke aplikasi; backup MinIO immutable (object lock).
- **Odoo**: tidak lagi dapat diakses dari internet (allowlist RemoteAddr), 2FA wajib user internal, RPC eksternal diblok, master password di-hash, password DB tidak di argv.
- **Supply chain**: action dipin SHA, tanda tangan cosign diverifikasi per identitas workflow+ref, deploy berdasarkan digest, produksi hanya dari tag rilis.
- **Anti-fraud loyalty**: publisher `loyalty.earn`/`notify.push` diautentikasi broker (`user_id`), poin dibatasi, report harus `valid` dan hash cocok.

## Prioritas perbaikan
1. **Sebelum staging dibuka ke outlet pilot / WireGuard:** SEC-21 (pisah allowlist outlet vs staf, VLAN tamu), SEC-20 (mTLS Ollama/Loki/exporter), SEC-28 (firewall hub WireGuard).
2. **Sebelum go-live:** sisa SEC-06 (captcha), SEC-07 (ikat `amount`, batasi buat/edit partner oleh kasir), SEC-11 (DSAR, consent, retensi `price_hunter_report`, TTL ClickHouse, kebijakan privasi/DPO), SEC-22, SEC-23 (`nameConstraints` + hapus `NODE_EXTRA_CA_CERTS` di BFF), SEC-24, SEC-25.
3. **Backlog terjadwal:** SEC-19, SEC-26, SEC-27, SEC-29, SEC-30; verifikasi runtime staging untuk SEC-03 (log `req.ip` via Cloudflare) dan item PLAUSIBLE di laporan devsecops G3.7.
