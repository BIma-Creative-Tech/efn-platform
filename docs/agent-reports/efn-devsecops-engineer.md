# Laporan Agent: efn-devsecops-engineer

Tanggal: 2026-10-05. Cakupan: secrets kontrak docs/04 §2, hardening compose, observability + alerting, backup & DR (R-T08), CI, runbook. **Tidak ada yang dijalankan** (mesin penulis tanpa Docker/Git/Python); semua validasi terjadi di CI job `infra` dan `odoo-test`. Lihat bagian "Belum terverifikasi".

## 1. Tabel secret → service → env var (untuk QA)

Sumber tunggal: `scripts/secrets.list`. File host: `/opt/efn/secrets/<secret>.txt` (dev: `./secrets/<secret>.txt`), di container: `/run/secrets/<secret>`. GitHub Environment secret = nama UPPERCASE.

| Secret (`/run/secrets/…`) | Service | Env var yang menunjuk file | Nilai dev (`gen-secrets.sh`) |
|---|---|---|---|
| `pg_password` | postgres | `POSTGRES_PASSWORD_FILE` | random |
| | odoo | `PASSWORD_FILE` | |
| | api-gateway | `PG_PASSWORD_FILE` | |
| | ocr-worker | `PG_PASSWORD_FILE` (**baru**) | |
| | etl | `PG_PASSWORD_FILE` | |
| | metabase | (entrypoint wrapper → `MB_DB_PASS`) | |
| | postgres-exporter | `DATA_SOURCE_PASS_FILE` | |
| `odoo_admin_password` | odoo | `ODOO_ADMIN_PASSWORD_FILE` | random |
| `jwt_secret` | api-gateway | `JWT_SECRET_FILE` | random |
| `rabbitmq_password` | rabbitmq | `RABBITMQ_DEFAULT_PASS_FILE` | random |
| | api-gateway | `RABBITMQ_PASSWORD_FILE` | |
| | ocr-worker | `RABBITMQ_PASSWORD_FILE` | |
| `minio_root_password` | minio | `MINIO_ROOT_PASSWORD_FILE` | random |
| | minio-init | (dibaca langsung `/run/secrets/minio_root_password`) | |
| | api-gateway | `MINIO_ROOT_PASSWORD_FILE` (**baru**) | |
| | ocr-worker | `MINIO_ROOT_PASSWORD_FILE` | |
| `clickhouse_password` | clickhouse | (entrypoint wrapper → `CLICKHOUSE_PASSWORD`) | random |
| | etl | `CH_PASSWORD_FILE` | |
| `otp_pepper` (**baru**) | api-gateway | `OTP_PEPPER_FILE` | random |
| `discount_token_secret` (**baru**) | api-gateway | `DISCOUNT_TOKEN_SECRET_FILE` | random |
| `odoo_bff_api_key` (**baru**) | api-gateway | `ODOO_BFF_API_KEY_FILE` | hex 32 byte |
| | odoo | `EFN_BFF_API_KEY_FILE` | |
| `whatsapp_token` (**baru**) | api-gateway | `WHATSAPP_TOKEN_FILE` | literal `disabled` |
| `webhook_payment_secret` (**baru**) | api-gateway | `WEBHOOK_PAYMENT_SECRET_FILE` | random |
| `minio_backup_password` (**baru**, R-T08) | postgres (efn-walg) | `MINIO_BACKUP_PASSWORD_FILE` | random |
| | minio-init | (dibaca langsung) | |
| `walg_libsodium_key` (**baru**, R-T08) | postgres (efn-walg) | `WALG_LIBSODIUM_KEY_FILE` | 32 byte base64 |
| `grafana_admin_password` (**baru**; sebelumnya Grafana memakai ulang `odoo_admin_password`) | grafana | `GF_SECURITY_ADMIN_PASSWORD__FILE` | random |
| `alertmanager_webhook_url` (**baru**) | alertmanager | `url_file` di `alertmanager.yml` | literal `http://127.0.0.1:9/disabled` |

Env non-rahasia baru:

| Service | Env | Nilai |
|---|---|---|
| api-gateway | `WHATSAPP_ENABLED` | `${WHATSAPP_ENABLED:-false}` (dev override paksa `"false"`) |
| | `WHATSAPP_PHONE_NUMBER_ID` | `${WHATSAPP_PHONE_NUMBER_ID:-}` |
| | `WHATSAPP_OTP_TEMPLATE` | `${WHATSAPP_OTP_TEMPLATE:-efn_otp}` |
| | `MINIO_ENDPOINT` / `MINIO_USE_SSL` / `MINIO_ROOT_USER` | `minio:9000` / `false` / `efnadmin` |
| | `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` |
| | `REDIS_URL` | `redis://redis:6379` |
| | `AMQP_URL` (host, tanpa password) | `amqp://efn@rabbitmq:5672` (`RABBITMQ_URL` dipertahankan untuk kompatibilitas) |
| | `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | `postgres` / `5432` / `efn_platform` / `efn` (+ `DATABASE_URL` tetap) |
| ocr-worker | `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` | sama seperti di atas |
| | `METRICS_PORT` | `9100` |
| | `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` |
| superapp-web | `BFF_INTERNAL_URL` | `http://api-gateway:3000` (`API_INTERNAL_URL` & `NEXT_PUBLIC_API_URL` dihapus) |
| | `COOKIE_INSECURE` | `"true"` **hanya** di `docker-compose.dev.yml` |
| postgres | `WALG_S3_PREFIX`, `WALG_S3_ENDPOINT`, `MINIO_BACKUP_USER` | `s3://pg-backup/efn`, `http://minio:9000`, `pgbackup` |

## 2. File yang diubah / dibuat

| File | Isi |
|---|---|
| `scripts/secrets.list` (baru) | Daftar tunggal 15 secret + generator dev |
| `scripts/gen-secrets.sh` | Membaca `secrets.list`; dir 0700, file 0644 (lihat temuan #1); idempoten |
| `scripts/deploy.sh` | Loop dari `secrets.list`, host override secret digenerate, `.env` + WhatsApp/WAL-G, `config -q` sebelum up, `up --no-build`, cek `minio-init` exit 0, domain target probe |
| `scripts/backup.sh` | WAL-G `backup-push` + `delete retain FULL 14` + metrik textfile; dump logis age opsional |
| `scripts/restore-test.sh` (baru) | Restore ke container+volume sementara, tunggu promote, verifikasi DB/tabel/row count, exit non-zero, metrik |
| `scripts/fetch-oca.sh` | + `report_xlsx` (OCA/reporting-engine), `date_range` (OCA/server-ux); gagal bila modul hilang |
| `infra/compose/docker-compose.yml` | 15 secret, mount per service, env baru, hardening, network `egress` & `socket-proxy`, `docker-socket-proxy`, `minio-init`, image postgres+WAL-G, profile `ops` (alertmanager, node-exporter, cadvisor, postgres-exporter, redis-exporter, blackbox-exporter), host `mart.`/`kitchen.` |
| `infra/compose/docker-compose.dev.yml` | Port dev bind `127.0.0.1`, mount `lib/`, `components/`, `middleware.ts`, `COOKIE_INSECURE=true`, command postgres dengan archive |
| `infra/compose/docker-compose.prod.yml` | `archive_command` WAL-G (sebelumnya merujuk pgBackRest yang tidak terpasang), limit tambahan; tanpa `ports` |
| `infra/compose/.env.example` | WhatsApp, WAL-G, textfile dir |
| `infra/backup/postgres/{Dockerfile,efn-walg.sh,efn-restore.sh}` (baru) | postgres:16.6-bookworm + WAL-G v3.0.9, wrapper & entrypoint restore |
| `infra/backup/systemd/efn-{backup,restore-test}.{service,timer}` (baru) | Jadwal harian 01:30 WIB & bulanan |
| `infra/minio/minio-init.sh` (baru) | Bucket `market-scout`, `pg-backup`, user `pgbackup` berkebijakan minimum |
| `infra/clickhouse/config.d/prometheus.xml` (baru) | Endpoint metrik :9363 (sebelumnya di-scrape tapi tidak aktif) |
| `infra/traefik/traefik.yml`, `infra/observability/promtail.yml` | Docker provider lewat `tcp://docker-socket-proxy:2375` |
| `infra/observability/prometheus.yml` | rules, alertmanager, scrape exporter, `ocr-worker:9100` (dns_sd), rabbitmq `/metrics/per-object`, minio, loki, probe internal & HTTPS |
| `infra/observability/{alert-rules,alertmanager,blackbox,postgres-exporter-queries}.yml`, `targets/blackbox-https.yml` (baru) | 22 alert + routing + probe + metrik archiver |
| `infra/observability/grafana/provisioning/datasources/datasources.yml` | uid tetap + datasource Alertmanager |
| `infra/observability/grafana/provisioning/dashboards/{dashboards.yml,json/efn-platform-overview.json}` (baru) | Dashboard "EFN - Platform Overview" (14 panel) |
| `.github/workflows/ci.yml` | `permissions: {}` + per job, pytest matrix (ai-ocr-worker/etl/text2sql), job `odoo-test`, secrets dummy via `gen-secrets.sh`, cek port prod, cek secret compose = `secrets.list`, promtool, amtool, shellcheck, komentar pin SHA |
| `.github/workflows/build-push.yml` | Matrix `context`, image `postgres` (WAL-G) ikut scan/SBOM/sign |
| `.github/workflows/deploy.yml` | Permission minimal (hapus `id-token`), rsync diperbaiki (temuan #2), secret lewat stdin, 9 secret baru, vars WhatsApp, cosign verify `postgres` |
| `.github/dependabot.yml` | docker `infra/backup/postgres`, pip `services/text2sql` |
| `Makefile` | profile `ops`, `validate` membuat secrets, `test-py`, `backup`, `restore-test`, shellcheck |
| `README.md` (hanya baris hosts, atas permintaan paket kerja) | + `mart.efn.local kitchen.efn.local` |
| `docs/runbooks/*.md` (baru, 11 file) | Indeks + runbook tiap alert + backup/restore/DR + rotasi secret |

## 3. Keputusan

- **WAL-G, bukan pgBackRest**: repo S3 pgBackRest hanya HTTPS; MinIO internal HTTP. WAL-G: endpoint HTTP, enkripsi libsodium, zstd, PITR. Dapat dibalik (hanya image `infra/backup/postgres`). ADR-001 menyebut pgBackRest sebagai contoh — architect perlu mencatat perubahan ini.
- **RPO 15 menit** = `archive_timeout=900` + alert `EfnWalArchiveFailing`. **RTO 4 jam** diukur oleh `efn_restore_test_duration_seconds` (timeout restore 3 jam).
- **Jadwal = systemd timer di host**, bukan GitHub Actions `schedule` + SSH: backup tidak bergantung pada GitHub/VPN runner, tidak membuka SSH ke runner publik.
- **docker-socket-proxy**: Traefik & Promtail tidak lagi me-mount `docker.sock` langsung.
- **Network `egress`** (non-internal) untuk api-gateway (WhatsApp Cloud API), alertmanager, blackbox, ollama (pull model), uptime-kuma. Sebelumnya api-gateway hanya di network `internal: true` → WhatsApp tidak akan pernah terjangkau.

## 4. Temuan bug pada scaffold sebelumnya (sudah diperbaiki)

1. Secret ditulis mode 0600 oleh `deploy.sh`/`gen-secrets.sh`; compose non-swarm me-bind-mount file apa adanya → user non-root (odoo, app, worker) tidak bisa membaca. Sekarang dir 0700 + file 0644.
2. `deploy.yml`: `rsync --delete infra/ scripts/ host:/opt/efn/` akan **menghapus `/opt/efn/secrets`** dan menaruh skrip di `/opt/efn/` (bukan `/opt/efn/scripts/` yang dipanggil). Diperbaiki dengan dua rsync + exclude.
3. Secret dikirim sebagai argumen `ssh` (terlihat di `ps` host). Sekarang lewat stdin `bash -s`.
4. `archive_command=pgbackrest …` di prod padahal image postgres alpine tidak memuat pgbackrest → archive selalu gagal.
5. Grafana memakai `odoo_admin_password` sebagai password admin (berbagi secret lintas sistem).
6. ClickHouse di-scrape di :9363 tanpa konfigurasi endpoint Prometheus.

## 5. Item untuk agent lain / QA

- **BFF (efn-bff-engineer)**:
  - Ekspos metrik `/metrics` (prom-client): `efn_otp_request_total{purpose,result}`, `efn_otp_verify_total{purpose,result}`, `efn_whatsapp_send_total{result}` — alert OTP sudah ditulis dan bertanda `metric_status: proposed`. Jangan ada nomor HP di label.
  - **Percayai `X-Forwarded-For` hanya dari jaringan internal** (Traefik di network `efn_internal`); Fastify `trustProxy` harus dibatasi ke subnet internal/Traefik, bukan `true`, agar rate limit per IP tidak bisa dipalsukan. QA: uji kirim header `X-Forwarded-For` palsu langsung ke container.
  - Baca semua secret via `*_FILE` sesuai tabel §1; `AMQP_URL` tidak berisi password.
  - Usulan: dukung 2 key (current+previous) untuk `odoo_bff_api_key` dan `webhook_payment_secret` agar rotasi tanpa jeda.
  - Konfirmasi apakah `otp_pepper` juga dipakai untuk `member_hash` (docs/04 §1); bila ya, pisahkan sebelum rotasi pertama.
- **Data/AI (efn-data-ai-engineer)**: ocr-worker wajib mengekspos `/metrics` di `:9100` (healthcheck compose & scrape bergantung padanya); worker dapat `PG_*` + `PG_PASSWORD_FILE`. ETL: usulan metrik `efn_etl_last_success_timestamp_seconds`. Test di `services/<svc>/tests` (pytest), dependensi test di `requirements-dev.txt` (opsional).
- **Odoo (efn-odoo-engineer)**: baca `EFN_BFF_API_KEY_FILE`; test diberi tag `efn` (`@tagged('efn', 'post_install', '-at_install')`); CI menginstal `efn_core,efn_pos_otp_gate,efn_kitting` (yang ada) di DB `efn_test`.
- **Frontend**: CSP dikirim Next.js; middleware Traefik `secure-headers@file` tidak menyetel CSP (hanya HSTS, frame deny, nosniff, referrer, permissions-policy) → tidak konflik. `NEXT_PUBLIC_API_URL` dihapus dari compose runtime; `next.config.mjs` masih merujuknya di CSP `connect-src` (nilai kosong → aman, tetapi bersihkan).
- **Architect**: perbarui docs/03 §3.2 (daftar GitHub Environment secrets + vars `WHATSAPP_ENABLED`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_OTP_TEMPLATE`, `COMPOSE_PROFILES` default kini memuat `ops`) dan catat WAL-G di ADR terkait R-T08.
- **QA**: cocokkan tabel §1 dengan `docker compose config`; CI job `infra` otomatis gagal bila secret compose ≠ `secrets.list` atau ada port publish selain Traefik di prod.

## 6. Belum terverifikasi / risiko

- Tidak ada `docker compose config`, promtool, amtool, shellcheck, hadolint, yamllint yang dijalankan lokal — pertama kali dijalankan di CI.
- Tag image belum dicek keberadaannya: `minio/mc:RELEASE.2024-11-17T19-35-25Z`, `tecnativa/docker-socket-proxy:0.3.0`, `oliver006/redis_exporter:v1.66.0-alpine`, `gcr.io/cadvisor/cadvisor:v0.49.1`, `quay.io/prometheuscommunity/postgres-exporter:v0.16.0`, `prom/blackbox-exporter:v0.25.0`, `prom/alertmanager:v0.27.0`, `prom/node-exporter:v1.8.2`. Nama aset WAL-G (`wal-g-pg-22.04-amd64` + `.sha256`, v3.0.9) dicek di halaman rilis; `WALG_SHA256` belum di-pin (verifikasi hanya terhadap file .sha256 rilis).
- Asumsi perilaku image: entrypoint postgres resmi berjalan sebagai `USER postgres`; `RABBITMQ_DEFAULT_PASS_FILE` masih didukung image 3.13; plugin `rabbitmq_prometheus` aktif bawaan; `WALG_LIBSODIUM_KEY_PATH` + `TRANSFORM=base64`; Alertmanager `url_file` (≥ 0.26); amtool tidak memeriksa keberadaan `url_file`; Loki 3.2 tanpa shell (healthcheck dinonaktifkan).
- `cap_add` untuk redis/rabbitmq ditebak dari perilaku entrypoint; bila container gagal start, longgarkan per service dan catat.
- Pindah ke postgres **bookworm** mengubah uid (70→999) dan collation (musl→glibc): volume `pg-data` lama harus di-dump/restore (runbook backup-restore §5). Dev: `make clean`.
- Trivy image gate bisa memblokir image `postgres` bila binary WAL-G membawa CVE stdlib Go HIGH; perlu bump/justifikasi.
- Repo backup MinIO berada di host yang sama → **belum DR**; off-site wajib sebelum go-live.
- Job `odoo-test` bergantung pada pola log `ERROR/CRITICAL` dan exit code Odoo; `--test-tags efn` hanya menjalankan test bertag `efn`.
- node-exporter berjalan di network compose (bukan host network) → metrik jaringan host tidak akurat; disk/CPU/memori akurat via `--path.rootfs`.

---

# Gelombang 3 — Enkripsi di setiap transaksi data (2026-10-05)

Instruksi user: **enkripsi di setiap transaksi data**, tanpa pengecualian diam-diam. Kontrak: docs/04 §7 (v2), ADR-022. **Tidak ada yang dijalankan** (tanpa Docker/Git/openssl lokal); validasi pertama di CI job `infra`. Bagian §1 di atas (tabel secret gelombang 1) **usang** — tabel G3.1 di bawah yang berlaku.

## G3.1 Tabel secret → service → env (final)

File host `/opt/efn/secrets/<secret>.txt` (dev `./secrets/`), di container `/run/secrets/<secret>`; GitHub Environment = UPPERCASE. Generator dev di `scripts/secrets.list`; jenis `pki` oleh `scripts/gen-pki.sh`.

| Secret | Service | Env / cara pakai |
|---|---|---|
| `pg_password` | postgres | `POSTGRES_PASSWORD_FILE` (superuser `postgres`, hanya socket lokal) |
| `pg_password_odoo` | postgres (00-roles.sh), odoo | odoo `PASSWORD_FILE`, `USER=efn_odoo` |
| `pg_password_bff` | postgres, api-gateway | `PG_PASSWORD_FILE`, `PG_USER=efn_bff` |
| `pg_password_ocr` | postgres, ocr-worker | `PG_PASSWORD_FILE`, `PG_USER=efn_ocr` |
| `pg_password_etl` | postgres, etl | `PG_PASSWORD_FILE` + `ODOO_PG_PASSWORD_FILE`, user `efn_etl` |
| `pg_password_metabase` | postgres, metabase | wrapper → `MB_DB_PASS`, `MB_DB_USER=efn_metabase` |
| `pg_password_exporter` | postgres, postgres-exporter | `DATA_SOURCE_PASS_FILE`, `DATA_SOURCE_USER=efn_exporter` |
| `odoo_admin_password` | odoo | `ODOO_ADMIN_PASSWORD_FILE` (48 karakter) |
| `odoo_bff_api_key` | odoo, api-gateway | `EFN_BFF_API_KEY_FILE` / `ODOO_BFF_API_KEY_FILE` |
| `rabbitmq_password` | rabbitmq | user admin `efn-admin` (definitions, ops saja) |
| `rabbitmq_password_bff` | rabbitmq, api-gateway | `RABBITMQ_PASSWORD_FILE`, `AMQP_URL=amqps://efn-bff@rabbitmq:5671` |
| `rabbitmq_password_ocr` | rabbitmq, ocr-worker | `RABBITMQ_PASSWORD_FILE`, `AMQP_URL=amqps://efn-ocr@rabbitmq:5671` |
| `redis_password_bff` | redis (ACL), api-gateway | `REDIS_PASSWORD_FILE`, `REDIS_USERNAME=efn-bff` |
| `redis_password_exporter` | redis (ACL + healthcheck), redis-exporter | wrapper → `REDIS_PASSWORD`, `REDIS_USER=efn-exporter` |
| `minio_root_password` | minio, minio-init | `MINIO_ROOT_PASSWORD_FILE` (root hanya bootstrap) |
| `minio_backup_password` | postgres (efn-walg), minio-init | `MINIO_BACKUP_PASSWORD_FILE` (user `pgbackup`) |
| `minio_bff_secret` | minio-init, api-gateway | `MINIO_SECRET_KEY_FILE`, `MINIO_ACCESS_KEY=efn-bff` |
| `minio_ocr_secret` | minio-init, ocr-worker | `MINIO_SECRET_KEY_FILE`, `MINIO_ACCESS_KEY=efn-ocr` |
| `minio_kms_key` | minio | `MINIO_KMS_SECRET_KEY_FILE` (format `efn-sse-key:<b64 32 byte>`, SSE-S3) |
| `clickhouse_password` | clickhouse, clickhouse-init | wrapper → `CLICKHOUSE_PASSWORD` (admin `efn`) |
| `clickhouse_password_etl` | clickhouse-init, etl | `CH_PASSWORD_FILE`, `CH_USER=efn_etl` |
| `clickhouse_password_text2sql` | clickhouse-init | (user `efn_text2sql`; service text2sql belum ada) |
| `clickhouse_password_metabase` | clickhouse-init, metabase | di-mount untuk ops; diisi di UI koneksi Metabase→ClickHouse |
| `clickhouse_disk_key` | clickhouse | wrapper → `CLICKHOUSE_DISK_KEY_HEX` (encrypted-storage.xml data-ai) |
| `jwt_secret`, `otp_pepper`, `discount_token_secret`, `webhook_payment_secret`, `whatsapp_token` | api-gateway | `*_FILE` masing-masing (**`otp_pepper` dicabut dari etl**, SEC-16) |
| `pii_encryption_key` | api-gateway saja | `PII_ENCRYPTION_KEY_FILE`, `PII_ACTIVE_KEY_VERSION=1` |
| `walg_libsodium_key` | postgres (efn-walg), restore-test | `WALG_LIBSODIUM_KEY_FILE` |
| `grafana_admin_password` | grafana | `GF_SECURITY_ADMIN_PASSWORD__FILE` |
| `alertmanager_webhook_url` | alertmanager | `url_file` (deploy.sh menolak selain `https://`) |
| `efn_ca_crt` | semua klien TLS | `PG_SSLROOTCERT`, `PGSSLROOTCERT`, `EFN_CA_FILE`, `REDIS_TLS_CA_FILE`, `AMQP_TLS_CA_FILE`, `MINIO_CA_FILE`, `CH_CA_FILE`, `OLLAMA_CA_FILE`, `NODE_EXTRA_CA_CERTS`, `HEALTHCHECK_CA_FILE`, `METRICS_TLS_CA_FILE`, `WALG_S3_CA_CERT_FILE`, `SSL_CERT_FILE` (minio), file config (Traefik rootCAs, Prometheus/blackbox/promtail `ca_file`, Grafana `$__file`, ghostunnel `--cacert`) |
| `tls_postgres_crt/key` | postgres | `ssl_cert_file`; kunci disalin 0600 ke `/var/run/postgresql/efn-tls/server.key` |
| `tls_redis_*` | redis | `--tls-cert-file/--tls-key-file` |
| `tls_rabbitmq_*` | rabbitmq | `ssl_options.*`, `management.ssl.*`, `prometheus.ssl.*` |
| `tls_minio_*` | minio | disalin ke `/tmp/certs/{public.crt,private.key}` |
| `tls_clickhouse_*` | clickhouse | `config.d/tls.xml` openSSL.server |
| `tls_api_gateway_*` | api-gateway | `TLS_CERT_FILE`, `TLS_KEY_FILE` |
| `tls_odoo_*` | odoo-tls, odoo-ws-tls (ghostunnel) | `--cert/--key` |
| `tls_superapp_web_*`, `tls_metabase_*`, `tls_ollama_*`, `tls_cadvisor_*`, `tls_docker_socket_proxy_*` | sidecar ghostunnel masing-masing (cadvisor juga sertifikat klien mTLS ke docker-socket-proxy) | `--cert/--key` |
| `tls_traefik_*` | traefik | `tls.certificates` (metrics :8082) + klien mTLS `providers.docker.tls` |
| `tls_promtail_*` | promtail | klien mTLS docker_sd |
| `tls_prometheus_*`, `tls_alertmanager_*`, `tls_node_exporter_*`, `tls_postgres_exporter_*`, `tls_blackbox_exporter_*` | masing-masing | di-mount sebagai `tls_server_crt/key` → `web-config.yml` |
| `tls_redis_exporter_*` | redis-exporter | `REDIS_EXPORTER_TLS_SERVER_CERT_FILE/KEY_FILE` |
| `tls_grafana_*` | grafana | `GF_SERVER_CERT_FILE/KEY` |
| `tls_loki_*` | loki | `server.http_tls_config` |
| `tls_uptime_kuma_*` | uptime-kuma | `UPTIME_KUMA_SSL_CERT/KEY` |
| `tls_ocr_worker_*` | ocr-worker | `METRICS_TLS_CERT_FILE/KEY_FILE` |

Total 82 secret (33 + 49 PKI) — di bawah batas 100 secret per GitHub Environment (ditambah `DEPLOY_*`). `deploy.yml` kini membaca nilai lewat `toJSON(secrets)` sesuai `secrets.list` (tidak perlu edit workflow saat menambah secret).

## G3.2 Hop TLS yang diterapkan (docs/04 §7.1)

| Hop | Server | Klien / verifikasi |
|---|---|---|
| * → PostgreSQL 5432 | `ssl=on`, TLSv1.2+, `hba_file` = `infra/postgres/pg_hba.conf` (hanya `hostssl` + scram per DB/role; `hostnossl` & superuser TCP ditolak) | verify-full: BFF, ocr, etl (platform+Odoo), Odoo (render_odoo_conf), Metabase (JDBC), postgres-exporter |
| BFF/exporter → Redis 6379 | `port 0`, `tls-port 6379`, ACL (`default` off, `efn-bff` prefix `otp:* dtok:* rl:* ms:*`, `efn-exporter`), `noeviction` (SEC-05), network `cache` khusus | `rediss://` + CA |
| → RabbitMQ | amqps 5671 saja, mgmt https 15671, metrik https 15691, distribusi Erlang/epmd loopback | `amqps://` + CA |
| → MinIO 9000 | HTTPS (`--certs-dir`), SSE-S3 | BFF/ocr (`MINIO_USE_SSL=true` + CA), WAL-G (`WALG_S3_CA_CERT_FILE`), mc (`certs/CAs`) |
| → ClickHouse | https 8443 + native TLS 9440; 8123/9000/9004/9005/9009 dihapus; metrik via http_handlers di 8443 | ETL (HTTPS+CA), clickhouse-init (native TLS strict) |
| Traefik → upstream | semua `scheme=https` + `serversTransport` per service (`rootCAs` + `serverName`) | dicek CI (`check-encryption.sh`) |
| → Odoo / Next.js / Metabase / Ollama / cAdvisor / docker-socket-proxy | ghostunnel `v1.11.3-alpine` (`network_mode: service:<app>`), app listen 127.0.0.1 | Traefik, Prometheus, ocr-worker (Ollama), Traefik/Promtail/cAdvisor (mTLS CN allowlist ke socket proxy) |
| Next.js → BFF, Odoo → BFF | BFF HTTPS native :3000 | `NODE_EXTRA_CA_CERTS` / `EFN_CA_FILE` |
| Prometheus → semua target | exporter-toolkit `web-config.yml`, redis_exporter TLS flags, rabbitmq 15691, traefik metrics router TLS | `scheme: https` + `ca_file` (+ `server_name` untuk dns_sd) |
| Promtail → Loki, Grafana → Prometheus/Loki/Alertmanager, Prometheus → Alertmanager | TLS | CA internal |
| Antar-node / outlet | WireGuard (lapisan kedua) | — |

## G3.3 Pengecualian enkripsi yang tersisa (untuk architect → ADR-022)

| # | Pengecualian | Alasan | Kontrol kompensasi |
|---|---|---|---|
| E1 | Plaintext **loopback** di dalam network namespace app ber-sidecar (Odoo :8069/:8072, Next.js :3001, Metabase :3000, Ollama :11435, cAdvisor :8080, docker-socket-proxy :2375) | App tanpa TLS native; sesuai §7.1 (sidecar) | Bind 127.0.0.1 (tidak terjangkau container lain); ghostunnel TLS1.2+ |
| E2 | Healthcheck Docker HTTP loopback (odoo, superapp-web, metabase, cadvisor, docker-socket-proxy, status ghostunnel `http://127.0.0.1:606x`) | Probe liveness di namespace sendiri, bukan transaksi data | Loopback saja; lint mengizinkan hanya 127.0.0.1/localhost |
| E3 | Healthcheck ClickHouse `wget --no-check-certificate https://127.0.0.1:8443/ping` | busybox wget tanpa opsi CA | Loopback; verifikasi penuh oleh clickhouse-init + `probe-internal-tls` |
| E4 | PostgreSQL socket unix `local all all trust` di container postgres | Entrypoint init, WAL-G backup-push, ops `docker exec` | Bukan TCP; socket tidak dibagikan; superuser ditolak via TCP |
| E5 | Distribusi Erlang RabbitMQ (25672) + epmd plaintext | CLI `rabbitmqctl/diagnostics` | Bind 127.0.0.1 (`inet_dist_use_interface`, `ERL_EPMD_ADDRESS`) |
| E6 | Loki gRPC/memberlist, Promtail HTTP/gRPC | Komponen internal single binary | Bind 127.0.0.1 |
| E7 | CI job `odoo-test`: PostgreSQL service tanpa TLS | DB efemeral CI, data sintetis | Hanya di runner CI, tidak ada data nyata |
| E8 | Dev: sertifikat publik = default self-signed Traefik | Laptop tanpa domain publik | Dev only; hop internal dev tetap TLS PKI |
| E9 | Cloudflare edge → origin | Wajib mode **Full (strict)** di Cloudflare (sertifikat LE origin diverifikasi) | Dicatat untuk ops; tidak bisa dicek dari repo |
| E10 | Healthcheck Docker dinonaktifkan untuk prometheus, alertmanager, grafana, loki, exporter | Endpoint hanya TLS, image tanpa klien ber-CA (bukan pengecualian enkripsi, tetapi trade-off observability) | `up`/`probe-internal-tls` + Uptime Kuma |

Bukan pengecualian tetapi sisa pekerjaan (ADR-022/023, belum dalam jangkauan gelombang ini):
- **Rantai PKI**: root offline → intermediate per lingkungan → leaf 90 hari, `efn_ca_crt` = bundle root+intermediate. Saat ini satu tingkat (CA → leaf 365 hari); `gen-pki.sh` + runbook rotasi CA (bundle transisi) sudah mendukung bundle.
- **ADR-023 entrypoint `wg-https` + ACME DNS-01** untuk `erp.`/`ops.`/`bi.`/`status.`: belum; saat ini host internal memakai router `websecure` + `internal-only` + sertifikat HTTP-01 (butuh port 80 publik). Butuh secret token DNS Cloudflare.
- `superapp-web` prod `replicas: 1` (sidecar tidak bisa menempel ke >1 replika). Opsi: TLS native di Next.js custom server (frontend) atau pasangan service ganda.

## G3.4 Status SEC yang ditangani

| ID | Status | Implementasi |
|---|---|---|
| SEC-01 | **Selesai (infra)** | Router `odoo`, `odoo-ws`, `odoo-login` = `internal-only` + `secure-headers`; `odoo-login` 10/menit/peer; `odoo-rpc-block` (`/xmlrpc`, `/jsonrpc`) → `deny-all` 403; `internal-only` tanpa `ipStrategy` (RemoteAddr, benar untuk WireGuard langsung); template WG + `gen-peer.sh` + runbook onboarding. 2FA = efn-odoo-functional |
| SEC-02 | **Selesai (infra)** | Role PG per service (`00-roles.sh`, password dari secret, dijalankan juga tiap deploy untuk rotasi), user MinIO `efn-bff`/`efn-ocr` + policy, user RabbitMQ per service tanpa tag administrator, ACL Redis, user ClickHouse per service; superuser/root hanya bootstrap |
| SEC-03 | **Selesai (infra)**, butuh verifikasi staging | `forwardedHeaders.trustedIPs` = Cloudflare (dari `cloudflare-ips.txt`, CI cek sinkron) + 10.0.0.0/8; `TRUSTED_PROXIES` = `172.30.1.0/24` (subnet `internal` di-pin) + Cloudflare (deploy.sh); runbook firewall origin |
| SEC-04 (lapisan 2) | **Selesai (infra)** | `clickhouse-init` menerapkan `access/*.sql` + mengikat `efn_text2sql` ke `text2sql_reader` setiap up/deploy; deploy gagal bila init gagal |
| SEC-05 | **Selesai** | ACL, TLS, `noeviction`, network `cache` |
| SEC-09 | **Selesai** | cAdvisor tanpa docker.sock/rootfs: docker-socket-proxy via mTLS, `cgroup: host`, mount `/sys/fs/cgroup` + `/var/lib/docker/image` ro, `cap_drop: ALL`, listen loopback + sidecar TLS |
| SEC-10 | **Selesai** | Semua `uses:` dipin SHA + komentar versi (ci, build-push, deploy, security, codeql, dast); `webfactory/ssh-agent` dihapus; identitas cosign = `build-push.yml@refs/heads/main` (staging) atau `@refs/tags/vX.Y.Z` (prod; prod menolak image non-rilis); deploy by digest (`IMAGE_<SVC>=…@sha256:`) hasil `cosign verify --output json`; input lewat `env` + regex. **trivy-action 0.29.0 dipindah ke 0.35.0** (tag lama di-force-push pada insiden CVE-2026-33634, Maret 2026) + binary Trivy `v0.69.3` |
| SEC-16 | **Selesai (compose)** | `otp_pepper` dicabut dari etl |
| SEC-18 | **Selesai** | `log_parameter_max_length=0`, `log_parameter_max_length_on_error=0` di base/dev/prod |
| SEC-11 (sebagian) | Lifecycle MinIO `market-scout/reports/` 365 hari; runbook breach 72 jam | DSAR/consent = BFF |

## G3.5 File utama gelombang 3

Baru: `scripts/gen-pki.sh`, `scripts/check-encryption.sh`, `scripts/update-cloudflare-ips.sh`, `infra/postgres/init/00-roles.sh`, `infra/postgres/pg_hba.conf`, `infra/redis/redis-entrypoint.sh`, `infra/rabbitmq/{rabbitmq.conf,rabbitmq-entrypoint.sh}`, `infra/clickhouse/{clickhouse-init.sh,client-config.xml}`, `infra/traefik/{cloudflare-ips.txt,dynamic/internal-tls.yml}`, `infra/observability/web-config.yml`, `infra/wireguard/{wg0-server.conf.example,peer-outlet.conf.example,gen-peer.sh}`, runbook `internal-pki.md`, `pii-key-rotation.md`, `wireguard-outlet.md`, `origin-firewall.md`, `luks-disk.md`, `breach-response-72h.md`.
Diubah: `infra/compose/docker-compose{,.dev,.prod}.yml`, `.env.example`, `scripts/{secrets.list,gen-secrets.sh,deploy.sh,restore-test.sh}`, `infra/minio/minio-init.sh`, `infra/clickhouse/config.d/{tls.xml,prometheus.xml}` (tls.xml versi data-ai ditimpa — pemilik devsecops), `infra/traefik/{traefik.yml,dynamic/middlewares.yml}`, `infra/observability/{prometheus,blackbox,loki,promtail,alert-rules,alertmanager}.yml`, Grafana datasources, `infra/backup/postgres/{efn-walg.sh,Dockerfile}` (komentar), semua workflow, `Makefile` (`pki`, `pki-rotate`, `lint-enc`), README (bagian menjalankan), runbook README/secret-rotation/rabbitmq-*/backup-failed.
`infra/clickhouse/config.d/zz-no-plaintext-native.xml` (data-ai) **tidak di-mount**: tidak diperlukan karena `tls.xml` menghapus port 9000 dan init berjalan lewat `clickhouse-init` (TLS 9440), bukan initdb.d bawaan.

## G3.6 Koordinasi

- **BFF**: Redis prefix yang diizinkan ACL `otp:* dtok:* rl:* ms:*` + `INFO`, `CLIENT SETNAME/GETNAME/ID`; prefix baru = ubah `infra/redis/redis-entrypoint.sh`. `00-roles.sh` tidak menulis grant tabel; `ALTER DATABASE metabase OWNER TO efn_metabase` idempoten dengan 01-*.sql. Key ring PII v2: runbook `pii-key-rotation.md` mengasumsikan `PII_ENCRYPTION_KEY_V<n>_FILE`.
- **data-AI**: RabbitMQ `efn-ocr` diberi configure+read pada 3 queue + 3 DLQ (worker men-declare non-pasif; deklarasi ber-DLX butuh read). Write ke default exchange (`amq.default`) **tidak bisa dibatasi per routing key** — kontrol nyata = properti AMQP `user_id` (divalidasi broker) + validasi semantik BFF (SEC-08). Pengetatan: worker passive-declare queue tujuan → read worker cukup `ocr.requested(.dlq)`. ClickHouse metrik pindah ke `https://clickhouse:8443/metrics` (handler `prometheus`). `clickhouse-client` config di-mount sebagai `/etc/clickhouse-client/config.xml` (menggantikan default image, bukan `config.d/`).
- **Odoo**: env sesuai permintaan (`USER=efn_odoo`, `PASSWORD_FILE`, `PGSSLROOTCERT`, `EFN_CA_FILE`, `EFN_BFF_URL=https://api-gateway:3000`, `ODOO_HTTP_INTERFACE=127.0.0.1`, `APP_ENV`); `efn_odoo` boleh connect DB `postgres` (pg_hba). CI build Odoo memakai `DOCKER_BUILDKIT=1`.
- **Frontend**: `HOSTNAME=127.0.0.1`, `PORT=3001`, `BFF_INTERNAL_URL=https://api-gateway:3000`, `NODE_EXTRA_CA_CERTS`; dev memakai PKI (`make secrets`), tanpa `EFN_ALLOW_PLAINTEXT`.
- **Architect**: catat G3.3 di ADR-022; WAL-G kini ke MinIO HTTPS (alasan awal "MinIO HTTP" di ADR-001 tidak berlaku lagi, WAL-G tetap dipilih); subnet Docker di-pin `172.30.0.0/16` (/24 per network).

## G3.7 Belum terverifikasi / risiko CI (cek pertama saat push)

1. **`docker compose config`**: secret long syntax `target: tls_server_crt` (non-swarm), `cgroup: host`, `network_mode: service:` + `depends_on`, `healthcheck.disable`, entrypoint `$$@` di postgres.
2. **`check-encryption.sh`**: regex bash bisa false positive/negative pada nilai compose ter-render; parsing `jq` label Traefik. Bila gagal, lihat pesan `::error::` per service.
3. **promtool**: `check config` memeriksa keberadaan file TLS (CI me-mount CA & sertifikat dummy); `promtool check web-config` (asumsi tersedia di 2.55).
4. **shellcheck** skrip baru (`gen-pki.sh`, `00-roles.sh` dengan `return` top-level untuk mode source, skrip POSIX sh redis/rabbitmq/clickhouse/minio).
5. **yamllint**: template Go di `middlewares.yml` (string ber-kutip), baris panjang (warning).
6. Runtime (PLAUSIBLE, belum diuji): ClickHouse `http_handlers` tipe `prometheus` (bila ditolak → server gagal start; fallback: metrik 9363 loopback + ghostunnel); `BIND_CONFIG` di `tecnativa/docker-socket-proxy:0.3.0` (bila tidak didukung, haproxy tetap :2375 di network `socket-proxy` — hanya Traefik/Promtail/cAdvisor); `rabbitmq.conf` tanpa listener TCP mgmt/prometheus default saat hanya `*.ssl.*` diset; definitions RabbitMQ dengan `password_hash` buatan openssl; cAdvisor flag `--docker-tls*` dan operasi tanpa `/rootfs`; Traefik template `env` di file provider; `MINIO_KMS_SECRET_KEY_FILE` pada volume lama; Loki `memberlist.bind_addr` loopback; Grafana `$__file{}` di `secureJsonData`; clickhouse-client `<secure>/<port>` di config.
7. Pin SHA diambil dari GitHub API (`git/matching-refs`, tag annotated di-dereference: codeql-action v3.38.2, sbom-action v0.24.3, zaproxy v0.14.0). Versi yang dipilih = minor terbaru di mayor yang sama; Dependabot akan mengusulkan mayor baru.
8. `semgrep/semgrep:latest` (container, bukan `uses:`) belum dipin digest — TODO.
9. Migrasi volume lama (PG superuser `efn`, RabbitMQ nodename, MinIO KMS, ClickHouse disk terenkripsi, object lock `pg-backup`) — langkah di `docs/runbooks/internal-pki.md`. Dev: `make clean`.

---

# Gelombang 5 — mTLS, PKI 3 tingkat, wg-https, kustodi kunci Superadmin (2026-10-06)

Kontrak: docs/04 §8 (v3: §8.1 ALTCHA, §8.3 payment, §8.4 privasi/retensi, §8.5 mTLS, §8.6 kustodi). Keputusan user: kunci hanya Superadmin; mTLS sesuai rekomendasi (hop tanpa kredensial aplikasi); payment gateway belum ditentukan; outlet memakai opsi open source. **Tidak ada yang dijalankan** (tanpa Docker/Git/openssl lokal) — validasi pertama di CI job `infra`.

## G5.1 File utama

| File | Perubahan |
|---|---|
| `scripts/gen-pki.sh` | **Ditulis ulang**: subperintah `root` (offline, passphrase, menolak di server), `intermediate <env>` (pathlen:0, **nameConstraints** = nama service + `efn-<svc>-1` + `.efn.internal` + localhost/127.0.0.1), `leaf` (90 hari, renew < 30 hari, profil EKU `server`/`both`/`client`, verifikasi rantai+nameConstraints, daftar `.pki-renewed`). Dev/CI: root+intermediate DEV otomatis |
| `scripts/derive-secrets.sh` (baru) | `member_hash_pepper` = HKDF-SHA256(otp_pepper, salt "", info `efn:member_hash:v1`) hex 64 + **self-test vector** `test-otp-pepper-v1` → `46df3d2fee0e91a91083827a5f303586e9c6b5c54afe096896bac9feb724061f`; `minio_prometheus_token` (JWT HS512). python3 (stdin, tanpa argv) atau fallback `openssl kdf` (argv → hidepid) — keduanya diuji di CI |
| `scripts/deploy.sh` | Root wajib; `/opt/efn/secrets` root:efn-superadmin 0750, file 0444 (lihat G5.5); kunci intermediate hanya di `/dev/shm` lalu `shred`; leaf diterbitkan di host + **recreate otomatis** service pemakai (+ sidecar); metrik expiry bundle (intermediate/root) + leaf; validasi `PAYMENT_PROVIDER` (prod tolak mock), `EFN_PUBLIC_BIND`/`EFN_WG_BIND` (IP WG harus ada), `ACME_EMAIL` (SEC-30), token DNS bukan placeholder; `docker-compose.payment.yml` bila provider ≠ mock; grant ETL Odoo (`/opt/efn/etl-sql/odoo-readonly-grants.sql`, sebagai `efn_odoo` via socket, ON_ERROR_STOP, dilewati bila `pos_order` belum ada); `PKI_SOURCE=host` dihapus |
| `scripts/secrets.list`, `gen-secrets.sh` | Jenis baru `derived:*`; 7 secret baru; penanda `[SA]` = kustodi Superadmin |
| `scripts/check-encryption.sh` | Tolak ghostunnel `--disable-authentication`, wajib `--cacert` + `--allow-cn`; web-config/Loki wajib `RequireAndVerifyClientCert` |
| `infra/compose/docker-compose.yml` | Semua sidecar mTLS; Traefik port publik di `EFN_PUBLIC_BIND`, `wg-https` di `EFN_WG_BIND:443→8443`; router erp./bi./ops./status. hanya `wg-https` + `letsencrypt-dns`; blok `/json/2` (SEC-22); router `api-webhook-payments` (POST, rate limit); BFF tanpa `NODE_EXTRA_CA_CERTS` (SEC-23), `ALTCHA_HMAC_KEY_FILE`, `PAYMENT_PROVIDER`; Odoo `EFN_MEMBER_HASH_PEPPER_FILE`; ETL AMQP `efn-etl`; ocr-worker `OLLAMA_CLIENT_CERT/KEY_FILE`, `METRICS_TLS_CLIENT_CA_FILE`; MinIO metrik `jwt`; lifecycle foto 90 hari; `CH_MART_RETENTION_YEARS` ke clickhouse-init |
| `infra/compose/docker-compose.payment.yml` (baru), `.prod.yml`, `.dev.yml`, `.env.example` | Mount kunci payment hanya bila provider ≠ mock; prod `PAYMENT_PROVIDER:?` wajib; dev `mock`, wg-https di `127.0.0.1:9443` |
| `infra/traefik/traefik.yml`, `dynamic/middlewares.yml`, `dynamic/internal-tls.yml` | Entrypoint `wg-https`, resolver `letsencrypt-dns` (DNS-01, provider via deploy); XFF hanya Cloudflare (10/8 dihapus, SEC-21); `internal-only` = outlet+staf, `internal-ops` = staf saja; `ratelimit-webhook`; serversTransports membawa sertifikat klien `traefik`; metrics :8082 mTLS (`efn-mtls`) |
| `infra/observability/*` | `prometheus.yml` (cert klien semua job, bearer MinIO, relabel QA U8), `web-config.yml` (exporter: SAN prometheus), `web-config-ui.yml` (baru: prometheus+grafana), Loki/Promtail/Grafana/blackbox mTLS, alert expiry internal < 21 hari, probe `erp.` publik dihapus |
| `infra/rabbitmq/*` | Queue + DLQ dideklarasikan broker (definitions); user `efn-etl` (write `amq.default`, configure/read kosong); write BFF/OCR = `amq.default` saja; `RABBITMQ_STRICT_PERMISSIONS=true` mencabut configure setelah klien passive-declare; metrik 15691 `verify_peer` |
| `infra/postgres/init/00-roles.sh`, `pg_hba.conf` | SEC-26: password lewat stdin (`\set pw`), bukan argv; pg_hba ADDRESS = `172.30.2.0/24` |
| `infra/minio/minio-init.sh` | Lifecycle `reports/` default **90 hari** (env); user `efn-prometheus` (`admin:Prometheus`, non-fatal) |
| `infra/wireguard/*` | Hub `10.88.0.1` (outlet 10.88/16, staf 10.89/16), `efn-wg.nft` (SEC-28), `gen-peer.sh --pubkey` (kunci di perangkat), contoh klien POS Windows/Android/Linux + laptop staf, **OpenWrt** `network.example` + `firewall.example` (VLAN POS vs Wi-Fi tamu) |
| `infra/backup/systemd/efn-restore-test.service` | User root (dir secret 0750) |
| `.github/workflows/deploy.yml`, `ci.yml`, `Makefile` | `sudo -n bash -s`; secret `EFN_PKI_*`; skip `pki`/`derived`; vars baru; rsync `services/etl/sql`; CI: bundle 2 cert, leaf ≤ 90 hari, EKU, **nameConstraints menolak leaf palsu**, test vector HKDF (python & openssl), promtool 2 web-config; `make pki-root`, `make pki-intermediate ENV=` |
| Runbook | **baru** `key-custody.md`; ditulis ulang `internal-pki.md`, `wireguard-outlet.md`; `origin-firewall.md`, `README.md` (runbooks), README (bagian menjalankan) |

## G5.2 Tabel secret → service → env (final, menggantikan G3.1)

File host `/opt/efn/secrets/<secret>.txt` (root:efn-superadmin, 0444 dalam dir 0750), container `/run/secrets/<secret>`. Sumber: **GH** = GitHub Environment (UPPERCASE), **HOST-PKI** = diterbitkan deploy.sh dari intermediate, **DERIVED** = derive-secrets.sh. `[SA]` = kustodi Superadmin.

| Secret | Sumber | Service → env |
|---|---|---|
| `pg_password` | GH | postgres `POSTGRES_PASSWORD_FILE` (superuser, socket saja) |
| `pg_password_{odoo,bff,ocr,etl,metabase,exporter}` | GH | postgres (00-roles.sh) + odoo `PASSWORD_FILE` / api-gateway, ocr-worker, etl `PG_PASSWORD_FILE` (+etl `ODOO_PG_PASSWORD_FILE`) / metabase wrapper `MB_DB_PASS` / postgres-exporter `DATA_SOURCE_PASS_FILE` |
| `odoo_admin_password` | GH | odoo `ODOO_ADMIN_PASSWORD_FILE` |
| `odoo_bff_api_key` | GH | odoo `EFN_BFF_API_KEY_FILE`, api-gateway `ODOO_BFF_API_KEY_FILE` |
| `rabbitmq_password` | GH | rabbitmq (admin `efn-admin`) |
| `rabbitmq_password_bff` / `_ocr` / **`_etl`** | GH | rabbitmq + api-gateway / ocr-worker / **etl** `RABBITMQ_PASSWORD_FILE` (`AMQP_URL=amqps://efn-{bff,ocr,etl}@rabbitmq:5671`, `AMQP_TLS_CA_FILE`) |
| `redis_password_bff` / `_exporter` | GH | redis ACL + api-gateway `REDIS_PASSWORD_FILE` / redis-exporter wrapper |
| `minio_root_password`, `minio_backup_password`, `minio_bff_secret`, `minio_ocr_secret` | GH | minio/minio-init; postgres `MINIO_BACKUP_PASSWORD_FILE`; api-gateway / ocr-worker `MINIO_SECRET_KEY_FILE` |
| **`minio_prometheus_secret`** | GH | minio-init (user `efn-prometheus`) |
| **`minio_prometheus_token`** | DERIVED | prometheus `authorization.credentials_file` |
| `minio_kms_key` [SA] | GH | minio `MINIO_KMS_SECRET_KEY_FILE` |
| `clickhouse_password`, `_etl`, `_text2sql`, `_metabase` | GH | clickhouse/clickhouse-init; etl `CH_PASSWORD_FILE`; metabase (UI) |
| `clickhouse_disk_key` [SA] | GH | clickhouse wrapper `CLICKHOUSE_DISK_KEY_HEX` |
| `jwt_secret`, `discount_token_secret`, `webhook_payment_secret`, `whatsapp_token` | GH | api-gateway `*_FILE` |
| `otp_pepper` [SA] | GH | api-gateway `OTP_PEPPER_FILE` |
| **`member_hash_pepper`** [SA] | DERIVED (HKDF dari `otp_pepper`) | **odoo `EFN_MEMBER_HASH_PEPPER_FILE`** |
| `pii_encryption_key` [SA] | GH | api-gateway `PII_ENCRYPTION_KEY_FILE` |
| **`altcha_hmac_key`** | GH | api-gateway `ALTCHA_HMAC_KEY_FILE` |
| **`payment_provider_api_key`** | GH (dev `disabled`) | api-gateway `PAYMENT_PROVIDER_API_KEY_FILE` **hanya bila `PAYMENT_PROVIDER` ≠ mock** (`docker-compose.payment.yml`) |
| **`acme_dns_api_token`** | GH (dev `disabled`) | traefik `${ACME_DNS_TOKEN_ENV}_FILE` (default `CF_DNS_API_TOKEN_FILE`) |
| `walg_libsodium_key` [SA] | GH | postgres `WALG_LIBSODIUM_KEY_FILE`, restore-test |
| `grafana_admin_password`, `alertmanager_webhook_url` | GH | grafana / alertmanager |
| `efn_ca_crt` (bundle intermediate+root) | HOST-PKI | semua klien TLS + server mTLS (`--cacert`, `client_ca_file`, `caFiles`) |
| `tls_<svc>_crt/key` (24 service) | HOST-PKI | server TLS masing-masing; **klien mTLS** (EKU both/client): traefik (serversTransports, docker provider), prometheus (`tls_server_*` di semua scrape), grafana (datasource `tlsClientCert`), promtail (Loki + docker_sd), ocr-worker (`OLLAMA_CLIENT_CERT_FILE/KEY_FILE`), blackbox-exporter (modul probe), cadvisor (socket-proxy) |
| — (bukan Docker secret) | GH | `EFN_PKI_INTERMEDIATE_KEY` [SA], `EFN_PKI_INTERMEDIATE_CRT`, `EFN_PKI_ROOT_CRT`, `EFN_PKI_EXTRA_TRUST` (rotasi), `DEPLOY_*` |

Env non-rahasia baru: api-gateway `PAYMENT_PROVIDER` (dev/staging `mock`; prod wajib eksplisit, deploy menolak mock); traefik `EFN_WG_OUTLET_CIDR`, `EFN_WG_STAFF_CIDR`; GitHub vars `PAYMENT_PROVIDER`, `EFN_PUBLIC_BIND`, `EFN_WG_*`, `ACME_DNS_PROVIDER`, `ACME_DNS_TOKEN_ENV`, `RABBITMQ_STRICT_PERMISSIONS`, `MARKET_SCOUT_PHOTO_RETENTION_DAYS` (90), `CH_MART_RETENTION_YEARS` (5). Jumlah GitHub Environment secret turun dari ~82 ke ~40 (leaf tidak lagi di GitHub).

## G5.3 Pengecualian enkripsi/mTLS tersisa (perbarui tabel ADR-022 — architect)

| # | Status | Pengecualian | Kontrol kompensasi |
|---|---|---|---|
| E1 | Tetap (diperketat) | Loopback app↔sidecar (Odoo, Next.js, Metabase, Ollama, cAdvisor, socket-proxy) | Sisi jaringan sidecar kini **mTLS + CN allowlist** |
| E2–E9 | Tetap | (sama seperti G3.3) | — ; E9 + catatan host internal tidak lewat Cloudflare |
| E10 | Tetap | Healthcheck Docker dinonaktifkan untuk endpoint TLS/mTLS | `up`, probe, Uptime Kuma |
| **E11** | Baru | ClickHouse `/metrics` (8443) server-TLS saja, tanpa sertifikat klien: verifikasi klien Poco berlaku global untuk port (ETL/Metabase berpassword tanpa cert) | Network `data` saja, hanya metrik server agregat tanpa PII; rencana: `predefined_query_handler` FORMAT Prometheus + user baca-saja (fase 2) |
| **E12** | Baru (parsial) | mTLS **tanpa allowlist SAN/CN** (klien = sertifikat CA internal ber-EKU clientAuth apa pun): Loki, redis_exporter, RabbitMQ 15691, Traefik :8082, ocr-worker `/metrics` (prometheus-client), BFF `/metrics` (belum memverifikasi klien — kode BFF) | EKU clientAuth hanya diterbitkan ke 7 klien (traefik, prometheus, grafana, promtail, ocr-worker, blackbox, cadvisor); server murni EKU serverAuth saja (dicek CI) |
| **E13** | Baru | `blackbox-exporter` diizinkan CN di sidecar Odoo/Next.js/Metabase/Ollama (probe kesehatan/handshake) — melebihi daftar §8.5 | Blackbox hanya dapat dipicu Prometheus (web-config SAN `prometheus`), target statis; hapus `--allow-cn=blackbox-exporter` bila lead menolak (probe sidecar akan gagal → hapus juga target probe terkait) |

Dihapus: `--disable-authentication` (SEC-20), XFF dari 10/8 (SEC-21), MinIO metrik `public` (SEC-30), `NODE_EXTRA_CA_CERTS` BFF (SEC-23).

## G5.4 Status SEC (infra)

| ID | Status | Catatan |
|---|---|---|
| SEC-20 | **Fixed (infra)**, sisa E11/E12 | Butuh kode: data-AI `vlm.py` membawa `OLLAMA_CLIENT_CERT/KEY_FILE` + `METRICS_TLS_CLIENT_CA_FILE`; BFF `/metrics` verifikasi klien |
| SEC-21 | **Fixed** | `internal-only` = 10.88/16 + 10.89/16, `internal-ops` = staf; entrypoint `wg-https` di IP WG; XFF hanya CF; VLAN tamu di runbook + contoh OpenWrt |
| SEC-22 | **Fixed** | Sidecar Odoo mTLS CN traefik(+blackbox); `/json/2` diblok |
| SEC-23 | **Fixed** | 3 tingkat, nameConstraints, leaf 90 hari, `PKI_SOURCE=host` dihapus, `NODE_EXTRA_CA_CERTS` BFF dihapus |
| SEC-25 | **Partial** | Topologi di broker, write = `amq.default` saja, `efn-etl` minimal; configure/read ekstra tetap sampai BFF & worker passive-declare → lalu `RABBITMQ_STRICT_PERMISSIONS=true` |
| SEC-26 | **Fixed** | stdin `\set pw` |
| SEC-28 | **Fixed (PLAUSIBLE)** | `efn-wg.nft` + catatan ip_forward; `gen-peer.sh --pubkey` |
| SEC-29 | Bukan infra | BFF: baca kunci PII sebagai Buffer, jangan masuk `process.env` |
| SEC-30 | **Partial** | Fixed: MinIO metrik JWT, `ACME_EMAIL` divalidasi, pg_hba subnet, proteksi branch Environment didokumentasikan (key-custody §3). Belum: `semgrep:latest` (digest belum diketahui tanpa akses registry), `toJSON(secrets)` tetap (diterima), passlib Odoo (odoo-engineer) |
| QA U1 | **Fixed (infra)** | deploy menjalankan SQL data-AI sebagai `efn_odoo` |
| QA U3/U7/U8 | Fixed / Partial / Fixed | U3 = SEC-20; U7 = SEC-25; U8 relabel subnet `internal` |

## G5.5 Keputusan & penyimpangan dari paket kerja

1. **File secret 0444 (bukan 0440)** dalam dir `root:efn-superadmin 0750`: compose non-swarm bind-mount file apa adanya; uid container beragam dan gosu/su-exec (postgres image resmi, redis, rabbitmq, clickhouse) membuang supplementary group → `group_add` + 0440 tidak andal tanpa uji runtime. Batas akses host = izin traverse direktori. `SECRET_FILE_MODE=0440` dapat diuji di staging. Detail: key-custody.md §Mekanisme uid.
2. **deploy.sh berjalan sebagai root** (`sudo -n bash -s`); user deploy (grup docker) sudah setara root — dicatat jujur di runbook.
3. **Alokasi WG = ADR-023** (outlet 10.88/16, staf 10.89/16), bukan 10.66/16 (template repo memakai 10.88).
4. **Sertifikat klien = sertifikat service yang sama dengan EKU server+client** untuk 6 service (promtail: client saja), bukan pasangan file baru — server murni kini serverAuth-only sehingga mTLS "CA + EKU" bermakna.
5. Leaf tidak lagi di GitHub; diterbitkan di host tiap deploy. Konsekuensi: rotasi leaf butuh deploy (alert < 21 hari); menambah service = intermediate baru (nameConstraints).
6. Dev override Traefik: `--api.insecure`/dashboard :8080 dihapus (diabaikan Traefik karena `--configFile` dipakai; port 8080 tidak pernah berfungsi).

## G5.6 Koordinasi

- **data-AI**: `vlm.py` → `requests(..., cert=(OLLAMA_CLIENT_CERT_FILE, OLLAMA_CLIENT_KEY_FILE))`; `/metrics` wajib `METRICS_TLS_CLIENT_CA_FILE`; ETL publish `privacy.erasure.completed` tanpa declare (efn-etl configure/read kosong; queue dibuat broker); passive declare di worker → strict mode. `services/etl/sql/odoo-readonly-grants.sql` harus idempoten & dapat dijalankan sebagai `efn_odoo`. Blok TTL `CH_MART_RETENTION_YEARS` di `clickhouse-init.sh` — env sudah diteruskan, file tidak saya ubah.
- **BFF**: `ALTCHA_HMAC_KEY_FILE`, `PAYMENT_PROVIDER`, `PAYMENT_PROVIDER_API_KEY_FILE` (opsional bila mock); jangan andalkan `NODE_EXTRA_CA_CERTS`; consumer `privacy.erasure.completed` + allowlist `user_id=efn-etl`; `assertQueue` → `checkQueue` agar strict mode bisa aktif; `/metrics` verifikasi klien (`requestCert`, CA internal, CN `prometheus`).
- **Odoo engineer**: baca `EFN_MEMBER_HASH_PEPPER_FILE` (hex 64, sama dengan BFF `memberHashPepper`; test vector di atas).
- **Architect/lead**: perbarui ADR-022 (W1 & W2 selesai, E11–E13), ADR-023 (alokasi 10.88/10.89 final, OpenWrt), docs/03 (GitHub Environment secrets/vars baru, `sudo` deploy), docs/04 §8.6 (0750/0444 vs "0700").

## G5.7 Risiko CI / runtime (cek pertama saat push)

1. **CI `infra`**: `openssl req -newkey ec -pkeyopt` & `-addext` (OpenSSL 3 di runner ✓), `-ext nameConstraints` output; `openssl kdf` di runner (OpenSSL 3.0 Ubuntu 22.04/24.04); promtool `credentials_file` + `check web-config` dengan `client_allowed_sans`; jq baru di `check-encryption.sh`; shellcheck `gen-pki.sh`/`derive-secrets.sh`/`gen-peer.sh`/`deploy.sh`/`00-roles.sh` (printf `\\\\set`); yamllint `prometheus.yml` (regex relabel) & baris panjang compose.
2. **`docker compose config` prod**: `PAYMENT_PROVIDER:?` (`.env.example` mengisi mock), env list `${ACME_DNS_TOKEN_ENV:-CF_DNS_API_TOKEN}_FILE=...`, port `${EFN_PUBLIC_BIND}`.
3. **Runtime PLAUSIBLE**: Traefik `serversTransports.certificates` + TLS option per Host pada entrypoint metrics; lego `CF_DNS_API_TOKEN_FILE`; RabbitMQ `prometheus.ssl.verify/fail_if_no_peer_cert` + definitions `queues` quorum (argumen harus identik dengan klien → kalau beda: PRECONDITION_FAILED); MinIO JWT metrik (`admin:Prometheus` untuk user non-root); `REDIS_EXPORTER_TLS_SERVER_CA_CERT_FILE`; Loki `client_auth_type`; Grafana `tlsClientCert` via `$__file{}`; nftables `ct original proto-dst` dan interaksi dengan iptables-nft Docker; Docker bind `10.88.0.1:443` (wg0 harus naik dulu); DNAT mempertahankan IP sumber peer (bila `userland-proxy` ikut campur, allowlist melihat IP gateway → 403 → set `"userland-proxy": false`).
4. **Migrasi**: deploy pertama gelombang 5 menerbitkan ulang SEMUA leaf + recreate semua container; Superadmin harus mengunggah `EFN_PKI_*` dan vars `PAYMENT_PROVIDER`, `EFN_PUBLIC_BIND`, secret `ACME_DNS_API_TOKEN`, `ALTCHA_HMAC_KEY`, `RABBITMQ_PASSWORD_ETL`, `MINIO_PROMETHEUS_SECRET`, `PAYMENT_PROVIDER_API_KEY` (`disabled` bila mock) **sebelum** deploy; host butuh grup `efn-superadmin`, sudoers deploy, wg0 + drop-in docker.
