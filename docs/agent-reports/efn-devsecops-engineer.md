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


---

# Gelombang 7 — runtime (6 Okt 2026, Docker Desktop 29.8.1 / WSL2)

Pertama kali stack dijalankan nyata. Semua hasil di bawah **terverifikasi di runtime**, kecuali yang ditandai *belum*.

## G7.1 Langkah CI infra (direplikasi persis di container)

| Langkah | Status | Catatan |
|---|---|---|
| yamllint `infra .github` | **Lolos** (hanya warning line-length) | 2 syntax error diperbaiki: `name:` berisi `: ` tanpa kutip (`security.yml:94`, `ci.yml:226`) |
| actionlint (rhysd/actionlint:latest) | **Lolos** | — |
| shellcheck (persis ci.yml) | **Lolos** | SC2034 `deploy.sh` (`for _`), SC2115 `fetch-oca.sh` (`${TARGET:?}`) |
| secrets compose == secrets.list | **Lolos setelah perbaikan** | Bug nyata: `compose config` membuang secret top-level yang tak dipakai service, sehingga `payment_provider_api_key` (hanya di-mount `docker-compose.payment.yml`) hilang. Langkah CI kini ikut `-f docker-compose.payment.yml`. Ratusan baris `-` di harness lead berasal dari escaping awk di harness itu, bukan bug repo. |
| prod hanya publish Traefik | Lolos | — |
| promtool (prometheus.yml, web-config*.yml) | Lolos | Diulang setelah perubahan prometheus.yml |
| amtool | Lolos | — |
| hadolint `--failure-threshold warning` | **Lolos** | DL3003 di `infra/backup/postgres/Dockerfile` (hapus `cd /tmp`, path absolut). `infra/minio/Dockerfile` baru juga lolos. |
| derive self-test, check-encryption, cloudflare, grafana JSON | Lolos | — |
| `docker compose config -q` dev & prod (semua profil) | Lolos | — |

## G7.2 Status service

| Profil | Service | Status | Catatan |
|---|---|---|---|
| core | postgres, redis, rabbitmq, minio, minio-init (exit 0), docker-socket-proxy(+tls), traefik, odoo(+tls, +ws-tls), api-gateway, superapp-web(+tls) | **healthy** | lihat perbaikan G7.3 |
| data | clickhouse, clickhouse-init (exit 0), etl, metabase(+tls) | **healthy** | tabel `efn_dwh` ber-`storage_policy=encrypted` |
| observability/ops | prometheus, loki, promtail, grafana, uptime-kuma, alertmanager, node-exporter, cadvisor(+tls), postgres-/redis-/blackbox-exporter | **Up**; yang punya healthcheck = healthy | 33/33 target Prometheus `up`; Loki menerima log 29 service |
| ai | ollama, ocr-worker | *belum di-boot* | butuh pull model; alert `EfnOcrNoConsumer` dan probe `ollama:11434` wajar gagal |

Alert yang aktif di dev dan memang diharapkan: `EfnPublicEndpointDown` (blackbox memakai CA publik sementara dev memakai sertifikat self-signed), `EfnBackupMetricMissing`, `EfnOcrNoConsumer`, probe ollama.

## G7.3 Perbaikan (bug nyata yang ditemukan runtime)

1. **Image MinIO hilang dari registry**: `minio/minio` & `minio/mc` tidak ada lagi di Docker Hub, dan quay.io meminta auth. Solusi: `infra/minio/Dockerfile` membangun **dari source tag upstream** (minio `RELEASE.2025-10-15T17-29-55Z`, yang memuat fix CVE-2025-62506; mc `RELEASE.2025-08-13T08-35-41Z`), non-root uid 1000, satu image untuk `minio` dan `minio-init`. Image ditambahkan ke `build-push.yml` (scan/SBOM/sign), `deploy.yml` (cosign verify digest), `deploy.sh` (`IMAGE_MINIO` wajib digest), `.env.example`. **Keputusan untuk lead/architect**: upstream community edition kini source-only dan dalam mode maintenance. Pilihannya: build sendiri (cara sekarang), fork komunitas (pgsty/minio), atau S3 lain (Garage/SeaweedFS). Pertimbangkan ADR.
2. **CRLF di secret** (openssl Windows): `tr -d '\n'` menyisakan CR, sehingga postgres 00-roles menolak password dan init gagal di tengah jalan (volume tercatat sudah terinisialisasi, role tidak ada). Redis menolak password. Kini `gen-secrets.sh` memakai `tr -d '\r\n'` dan `gen-pki.sh` membuang CR dari serial. Secret turunan dihitung ulang.
3. **Redis**: (a) `chmod` setelah `chown` ditolak (tanpa CAP_FOWNER), urutan dibalik. (b) **Crash-loop pada setiap restart dengan data**: entrypoint resmi menjalankan `find . ! -user redis -exec chown` sebagai root tanpa DAC_OVERRIDE (`appendonlydir: Permission denied`). Wrapper kini langsung `setpriv --reuid redis` dan tidak memakai entrypoint resmi. Sudah diuji dengan 2x restart. (c) ACL exporter `+latency|histogram`.
4. **MinIO metrik**: policy `admin:Prometheus` dengan `Resource arn:minio:admin:::*` ditolak ("invalid resource"), sehingga user efn-prometheus gagal dibuat. Resource dihapus; target `minio` kini up.
5. **Traefik v3.2 tidak bisa bicara dengan Docker Engine 29** (API minimum 1.44, provider docker "Error response from daemon"). Dinaikkan ke `traefik:v3.7.13`. `aliasHeadersStrategy: delete` ditambahkan di semua entrypoint (peringatan keamanan v3.7: spoof header alias seperti `X_Forwarded_For`).
6. **TLS publik dev gagal total**: `sniStrict: true` + tidak ada sertifikat untuk `*.efn.local` (ACME tidak bisa), sehingga handshake ditolak ("unrecognized name"). Perbaikan: sertifikat edge **dev-only** self-signed (`gen-secrets.sh`, `secrets/dev_edge_*.txt`, terpisah dari CA internal) + `infra/traefik/dev/edge-dev.yml`, di-mount oleh dev override. Mount dynamic base dipindah ke `/etc/traefik/dynamic/base`; provider file membaca subdirektori secara rekursif. Prod tidak berubah perilakunya.
7. **RabbitMQ :15692 plaintext terbuka di network** (listener TCP default plugin prometheus, tidak bisa dimatikan lewat conf). Kini diikat ke `127.0.0.1`.
8. **mTLS Prometheus → RabbitMQ :15691 gagal** ("internal error"). Erlang/OTP `public_key` crash (`function_clause` di `pubkey_cert:match_name(iPAddress, ...)`) saat sertifikat klien punya SAN IP dan intermediate punya nameConstraints IP. Direproduksi di OTP RabbitMQ 3.13 **dan** 4.1. Perbaikan: `gen-pki.sh` `NO_IP_SAN_SERVICES=" prometheus "` (leaf prometheus tanpa SAN IP, terbit ulang otomatis). Perhatian: klien mTLS baru ke server Erlang harus masuk daftar ini.
9. **Alertmanager**: `--web.external-url=.../alertmanager` memindahkan semua route, sehingga scrape **dan pengiriman alert** Prometheus mendapat 404. Perbaikan: `path_prefix: /alertmanager` + `metrics_path`.
10. **Target api-gateway hilang**: DNS Docker mengembalikan IP network `data` (172.30.2.x), lalu relabel `172.30.1.x` membuangnya. Kini `api-gateway.efn_internal` / `ocr-worker.efn_internal`.
11. **Promtail**: `tcp://` menyebabkan klien docker memakai HTTP polos ke port mTLS (koneksi putus/EOF). Diganti `https://`. Discovery & push Loki jalan. Sisa: peringatan berkala `could not transfer logs ... request canceled` (stream log terputus timeout lalu tersambung ulang). Log tetap masuk, tapi perlu dipantau.
12. **ClickHouse**: disk `encrypted` di atas `default` ditolak oleh 24.8 ("wrapped disk must have been announced earlier"). Kini memakai disk lokal eksplisit `disk_local` (`/var/lib/clickhouse/disks/local/`). Volume prod lama (bila ada) perlu migrasi; untuk dev volume baru.
13. **BFF dev OOM** (`nest start --watch`, heap ~250 MB): dev override `NODE_OPTIONS=--max-old-space-size=1024`, limit 1536M.
14. **node-exporter** di Docker Desktop: `rslave` ditolak. Dev override `/:/host:ro`. Prod tetap rslave.

## G7.4 Bukti enkripsi / kontrol akses (runtime)

- Traefik publik: TLSv1.3 (`TLS_AES_128_GCM_SHA256`). `https://api.efn.local/health` 200, `app.efn.local` 200, HTTP 80 → 301 https. HSTS/CSP terpasang. `/metrics` & `/v1/pos/*` di api publik 404. `erp.` di 443 publik 404. Di wg-https :9443: `/web/login` 200 (setelah init DB), `/web/database/manager` 404, `/jsonrpc` & `/xmlrpc` 403.
- PostgreSQL: `sslmode=disable` → `pg_hba rejects ... no encryption`. Superuser via TCP+TLS ditolak. `pg_stat_ssl`: efn_bff, efn_odoo, efn_metabase semuanya `ssl=t TLSv1.3`.
- Redis: plaintext → connection reset. TLS + ACL efn-bff → PONG. User default tanpa auth ditolak. BFF tersambung sebagai `efn-bff`.
- RabbitMQ: hanya 5671/15671/15691 di network (5672, 15672, 25672, 4369 tertutup, dan 15692 kini loopback). Handshake AMQP plaintext dibalas alert TLS. `list_connections`: efn-bff `ssl=true tlsv1.3`. BFF reconnect otomatis setelah broker restart.
- MinIO: HTTP → 400. HTTPS verify CA → 200. BFF→MinIO TLS terverifikasi (`authorized=true`, TLSv1.3, CN minio). Policy efn-bff: Put `reports/*` OK, Get & Put di luar prefix ditolak. Objek SSE-S3. WAL-G archive ke MinIO berjalan (`pg_stat_archiver` archived 3; 4 gagal awal sebelum MinIO siap).
- mTLS sidecar: odoo-tls tanpa sertifikat klien / CN api-gateway → ditolak; CN traefik → 200. BFF :9464 tanpa sertifikat → ditolak. docker-socket-proxy tanpa sertifikat → `certificate required`. Port plaintext 8069/3001 tidak terjangkau dari network.
- ClickHouse: hanya 8443/9440 (TLS) yang listen.

## G7.5 File yang diubah

`.github/workflows/{ci,security,build-push,deploy}.yml`; `scripts/{deploy,fetch-oca,gen-secrets,gen-pki}.sh`; `infra/compose/{docker-compose.yml,docker-compose.dev.yml,.env.example}` (+ `.env` lokal, di-ignore); `infra/minio/Dockerfile` (baru), `infra/minio/minio-init.sh`; `infra/redis/redis-entrypoint.sh`; `infra/rabbitmq/rabbitmq.conf`; `infra/traefik/traefik.yml`, `infra/traefik/dev/edge-dev.yml` (baru); `infra/observability/{prometheus,promtail}.yml`; `infra/clickhouse/config.d/encrypted-storage.xml`; `infra/backup/postgres/Dockerfile`; `docs/runbooks/dev-stack-boot.md` (baru). Tidak ada perubahan di `services/**`.

## G7.6 Sisa / butuh agent lain

- **Lead/architect**: keputusan sumber image MinIO (G7.3 no. 1, perlu ADR); Traefik naik minor v3.2 → v3.7 (cek catatan migrasi v3.3–v3.7 terhadap middleware yang ada; peringatan `encodedCharacters` belum ditindaklanjuti); update docs/04 §7 (subjek tanpa SAN IP untuk klien Erlang).
- **efn-bff-engineer**: image dev (`target: dev`) berjalan sebagai **root** (stage dev tanpa `USER`); prod runtime non-root. Peringatan `EBADENGINE` (paket butuh Node ≥ 22.13, image 22.12): pertimbangkan bump `node:22.x-alpine`.
- **efn-odoo-engineer/functional**: DB `efn_odoo` hanya diinit `base` (runbook §3). Pemasangan modul efn_* belum diuji di stack ini.
- **efn-data-ai-engineer**: profil `ai` belum di-boot.
- *Belum*: restore-test.sh, backup base (`efn-walg backup-push`), deploy prod overlay, workflow GitHub di runner nyata.

Volume dev `efn_*` **dipertahankan** setelah `down` (pg-data sudah berisi `efn_odoo` ter-init base, plus MinIO, ClickHouse, dan lain-lain), sehingga run berikutnya tidak perlu init ulang. Reset: `down -v`.

---

# Gelombang 9 — SILO, filter L7, access key acak, SEC-35/36/37/30, R1/R5/R7 (6 Okt 2026)

Runtime: Docker Desktop 29.8.1 (WSL2), host 16 CPU/31 GB yang **sangat padat** (CPU 100 %, RAM bebas 0,2–7 GB karena
proses lain di host). Docker Desktop sempat mati sekali (pipe hilang, pull Ollama `unexpected EOF`) dan saya nyalakan
ulang; hasil di bawah diambil setelah restart. ADR-030 masih **Proposed**; lead memutuskan menerapkan rekomendasi jangka
pendek. Semua perubahan reversible (G9.5).

## G9.1 Status per butir

| # | Butir | Status | Bukti (runtime kecuali disebut) |
|---|---|---|---|
| 1 | Object storage → **SILO** dari source | **Selesai** | `infra/minio/Dockerfile`: `pgsty/silo` + `pgsty/mc` tag `RELEASE.2026-09-16T00-00-00Z` (rilis terbaru per GitHub API), commit SHA diverifikasi saat build (`2a4d514…`, `e952aa7…`), `golang:1.27.1-alpine@sha256:cf6fca…` (= `go.mod` 1.27.1, digest yang sama dipakai SILO), runtime `alpine:3.24`, non-root 1000, symlink `minio`/`mcli`. `silo version RELEASE.2026-09-16T15-50-09Z (go1.27.1)`. **Upgrade in-place volume dev lama** (data MinIO 2025-10-15): boot healthy tanpa reset; 16 objek lama `pg-backup/efn/` terbaca; versioning enabled; `Object locking 'GOVERNANCE' 15DAYS`; `sse-s3` auto-encrypt kedua bucket; objek baru `Encryption: SSE-S3`; ILM `reports/` 90 hari + noncurrent 35 hari; `minio-init` idempoten. Metrik JWT lewat filter: 219 baris `minio_*` dengan token, 403 tanpa token |
| 2 | Access key acak (§8.12) | **Selesai** | Secret `minio_{bff,ocr,backup,prometheus}_access_key` (generator `akid` = 20 hex huruf besar, 80 bit): `secrets.list`, `gen-secrets.sh`, compose (top-level + mount per service). BFF & worker `MINIO_ACCESS_KEY_FILE`; WAL-G `MINIO_BACKUP_ACCESS_KEY_FILE` (`efn-walg.sh`, `restore-test.sh`, runbook backup-restore); token JWT Prometheus `sub/accessKey` dari file (`derive-secrets.sh`, diverifikasi). `minio-init` membuat user dari access key acak dan **menghapus user lama** `pgbackup/efn-bff/efn-ocr/efn-prometheus` (log "dihapus: 4"); keluaran `mc admin user` dibuang (access key tidak masuk log). Uji: kredensial lama `efn-bff` ditolak; bff PUT `reports/` OK, PUT di luar prefix & GET ditolak; ocr GET OK, PUT ditolak. Worker: `efn_ocr/config.py` (`_minio_access_key`: file > literal hanya APP_ENV dev/development/local/test dan bukan production, regex + min 16 di luar dev) + test (fixture `MINIO_ACCESS_KEY_FILE`, 4 test baru). pytest **168 lulus**, ruff 0.16.10 bersih |
| 3 | Filter L7 | **Selesai** | Service `s3-gateway` = HAProxy 3.2.25 LTS (digest dipin), `infra/s3-gateway/{haproxy.cfg,entrypoint.sh,README.md}`. TLS masuk sertifikat `s3-gateway` (gen-pki), re-encrypt `ssl verify required ca-file … verifyhost minio`. Storage dipindah ke network baru `objstore` (172.30.6.0/24, internal): hanya `s3-gateway` + `minio-init`. Klien: BFF/worker `MINIO_ENDPOINT=s3-gateway`, WAL-G `https://s3-gateway:9000`, Prometheus job `minio` + probe TLS. Header trailer dicek di sumber dulu: minio-js 8.x `UNSIGNED-PAYLOAD`/sha256, minio-py 7.2.12 `UNSIGNED-PAYLOAD`, WAL-G v3.0.9 = aws-sdk-go **v1** (tanpa trailer) → aman diblok; dibuktikan runtime (upload BFF, GET worker, WAL-G push sha256 tetap 200 lewat filter). Smoke `g.s3gw_*` (10 cek) lulus: bypass-governance, `PUT ?retention`, `x-amz-object-lock-*`, trailer, `X-Minio-Replication-*`, `?select`, `/minio/admin/` → 403 halaman HAProxy; HTTP polos ditolak; storage tak ter-resolve dari `data`; kontrol positif: S3 biasa diteruskan (AccessDenied dari storage). `mc retention clear` oleh user backup → 403 filter |
| 4 | R1 Ollama | lihat G9.3 | `ollama/ollama:0.35.1@sha256:292ee7…` (stabil terbaru 29 Sep 2026; 0.40 masih rc), `hostname: ollama` tetap; container + sidecar mTLS + ocr-worker healthy. `ollama pull qwen2.5vl:3b`: manifest **diterima** (0.5.4 = 412) |
| 5 | R5 ETL Odoo | **Selesai** | `ETL_ENABLE_ODOO` (base `false`, dev `true`, `.env.example`, var deploy). Sebelum grant: `job=sales_fact_pos gagal … permission denied for table pos_order_line` (job lain tetap jalan). Smoke `f` menerapkan `odoo-readonly-grants.sql` (sama dengan deploy.sh: `psql -U efn_odoo` via socket) → `f.odoo_etl_grants` OK, **`f.ch_fact_sales_line rows=36`** dari order POS smoke. `deploy.sh`: setelah grant sukses dan var tidak di-set, `.env` → `ETL_ENABLE_ODOO=true` + `up -d etl` (nilai dipertahankan antar-deploy) |
| 6 | R7 nightly | **Selesai, belum pernah jalan di GitHub** | `.github/workflows/e2e-nightly.yml`: `schedule` 02:00 WIB + `workflow_dispatch` (input skenario divalidasi regex), `permissions: contents: read`, action dipin SHA, secret dummy `gen-secrets.sh`, build → core → install modul Odoo → data+ai (Ollama tanpa model) → `run-e2e-smoke.sh` (`SMOKE_OUT_DIR` baru) → artefak (hasil, `compose ps`, tail log; data fiktif) → `down -v`. Runner `vars.E2E_RUNNER` (default ubuntu-latest; stack ±12–16 GB → larger/self-hosted untuk repo privat) |
| 7a | SEC-35 | **Selesai** | Loki → `127.0.0.1:3101` + sidecar `loki-tls` (`--allow-cn` promtail, grafana, prometheus); redis_exporter → `127.0.0.1:9122` + `redis-exporter-tls` (prometheus); RabbitMQ: listener TLS 15691 plugin dicabut, `rabbitmq-metrics-tls` :15691 → 127.0.0.1:15692 (prometheus); Traefik entrypoint `metrics` → `127.0.0.1:8084` + `traefik-metrics-tls` :8082 (prometheus). Uji: sertifikat prometheus → 200 di keempatnya, grafana → Loki 200; sertifikat **ocr-worker → `alert bad certificate`** di keempatnya. Target `loki`, `redis`, `rabbitmq`, `traefik`, `minio`, `api-gateway`, `ocr-worker`, `clickhouse` = up. `loki:3100` dikeluarkan dari probe-internal-tls (blackbox tidak boleh membaca log). check-encryption: Loki wajib loopback |
| 7b | SEC-36 | **Kode + runbook selesai; host belum dipasang** | `scripts/host/efn-deploy` (wrapper root, dipasang manual Superadmin) + `deploy.yml`: bundel `infra/ scripts/ services/etl/sql` deterministik ditandatangani **cosign keyless** (`id-token: write` hanya job deploy), dikirim bersama env base64 lewat satu stdin tar ke forced command `restrict,command="sudo -n /usr/local/sbin/efn-deploy"`. Wrapper: batas 64 MB, tepat 3 file biasa, `cosign verify-blob` dengan identitas/issuer dari `/etc/efn/deploy.conf` (root), tolak symlink & path di luar `infra|scripts|services/etl/sql`, ekstrak ke `/opt/efn/releases/<sha256>` milik root, sync ke `/opt/efn`, env hanya allowlist (nama tetap + `secrets.list` rilis terverifikasi), `exec /bin/bash deploy.sh` salinan root. Uji container (cosign palsu): tanda tangan invalid, `PATH`, `LD_PRELOAD`, `$(id)`, entri tar ekstra, path traversal, stdin kosong → ditolak; kasus sah → deploy.sh jalan sebagai root, nilai ber-spasi/`$` utuh, file konfigurasi 0644 root. Runbook `key-custody.md §4a`: sudoers tanpa argumen, `deploy` keluar dari grup docker, cosign v2.6.1 + sha256, auditd |
| 7c | SEC-37 | **Prosedur + desain** | `docs/runbooks/acme-dns-token.md`: delegasi `_acme-challenge` CNAME ke zona khusus, token `DNS:Edit`+`Zone:Read` satu zona + IP filtering + TTL, uji negatif ke zona utama, opsi wildcard `*.int` untuk CT. Pemisahan ACME dari Traefik (container lego di `egress`) didesain, **belum** diterapkan |
| 7d | SEC-30 semgrep | **Selesai** | `semgrep/semgrep:1.179.0@sha256:93963d…` (digest index, `imagetools inspect`) |
| 8 | Validasi | **Lulus** | G9.2 |

Pesan lead di tengah tugas: `api-gateway` memakai `MINIO_ACCESS_KEY_FILE=/run/secrets/minio_bff_access_key` + secret di-mount,
literal dihapus (dev juga). `04-platform-v3-6.sql` dijalankan manual di volume dev (2×, idempoten) sebelum BFF start;
`deploy.sh` kini menjalankan semua `infra/postgres/init/NN-*.sql` dengan NN ≥ 04 sebagai superuser (socket lokal) setelah
`00-roles.sh` dan sebelum `up` penuh (kontrak: file ≥ 04 wajib idempoten + transaksional); runbook dev-stack-boot §2b.

## G9.2 Validasi

| Langkah CI `infra` (direplikasi di container Linux) | Hasil |
|---|---|
| yamllint `infra .github` / actionlint | 0 error / lulus (termasuk e2e-nightly.yml, deploy.yml baru) |
| shellcheck (daftar CI + `scripts/host/efn-deploy`, `scripts/smoke/*.sh`, `infra/s3-gateway/*.sh`) | bersih |
| hadolint v2.12.0 (= hadolint-action v3.1.0) | lulus (`hadolint:latest` memunculkan DL3062/DL3025 baru; CI memakai versi action) |
| `haproxy -c` (langkah CI baru) | valid |
| promtool (prometheus.yml, 25 rules) / amtool | lulus |
| compose config dev & prod, secrets == secrets.list, prod hanya publish Traefik | OK |
| check-encryption (+ cek baru: Loki loopback, s3-gateway TLS/verify) | OK |
| derive self-test (python3 & openssl), cloudflare sinkron | OK |
| PKI | `gen-pki.sh` mendeteksi intermediate dev yang tidak mencakup `s3-gateway`, mengarsipkan CA lama, membuat ulang root+intermediate dev + semua leaf; run kedua idempoten |

Runtime: core + data + ai healthy; observability/ops dinyalakan sebagian (RAM host). **Smoke E2E penuh (a b c d f e g):
PASS=88 FAIL=0** (gelombang 8: 72). Cek baru: `g.s3gw_*`, `g.storage_not_reachable_from_data`, `f.odoo_etl_grants`,
`f.ch_fact_sales_line`. Smoke sempat gagal di skenario c karena kolom `res_partner.efn_erased` (perubahan odoo-engineer
paralel) belum di-upgrade di DB dev → saya jalankan `-u efn_privacy,efn_pos_otp_gate` (dev saja).

## G9.3 R1 — VLM

- **Terverifikasi:** `ollama/ollama:0.35.1` (digest dipin) jalan di belakang sidecar mTLS, `hostname: ollama` tetap,
  healthcheck & ocr-worker healthy, probe/target tidak regresi; `ollama pull qwen2.5vl:3b` **menerima manifest**
  (0.5.4 menjawab 412) dan mengunduh blob 3,2 GB sampai 100 %.
- **Belum terverifikasi:** model tidak pernah selesai terdaftar — Docker Desktop mati di akhir unduhan pertama; unduhan
  ulang (mulai dari 0) turun ke ±12 KB/s (estimasi 61 jam) sehingga dihentikan. Jadi smoke `report_vlm` dengan model
  nyata **belum dijalankan**; jalur tanpa model (`flagged/vlm_unavailable`) tetap lulus di smoke b.
- Ulangi di jaringan normal: `docker exec efn-ollama-1 ollama pull qwen2.5vl:3b`, set `OLLAMA_VISION_MODEL=qwen2.5vl:3b`
  di `.env`, `up -d ocr-worker`, lalu jalankan subperintah `report_vlm` (runbook dev-stack-boot §3b). Default compose
  masih `qwen2.5vl:7b` (keputusan data-ai/architect, ADR-008).

## G9.3b Kesiapan penghapusan OCA (instruksi user, commit 244de70) — DISIAPKAN, BELUM DIEKSEKUSI

Menunggu kabar lead bahwa modul audit pengganti `efn_setup_audit` sudah ada (addons tidak saya sentuh).
- **Siap:** `scripts/check-odoo-deps.sh <daftar-CE>` — gagal bila `depends` efn_* di luar Odoo CE 19 / `efn_*`.
  Diuji: daftar CE dari `efn-odoo:ci` (692 modul) → saat ini **1 pelanggaran: `efn_setup_audit` → `auditlog`** (benar),
  shellcheck bersih. Belum dipasang di CI (akan memerahkan CI sampai audit diganti).
- **Inventaris yang akan dihapus/diubah:** `scripts/fetch-oca.sh`; `ci.yml` (path-filter `scripts/fetch-oca.sh` baris 40,
  langkah "Fetch OCA addons" + pengecualian auditlog + `--addons-path …/mnt/oca-addons` di job odoo-test); `build-push.yml`
  langkah "Fetch OCA addons"; `services/odoo/Dockerfile` (`COPY oca /mnt/oca-addons`, mkdir/chown) dan
  `services/odoo/odoo.conf.tpl` `addons_path` (di luar batas file saya → minta izin lead/odoo-engineer); `.gitignore`
  baris 22–24 + `services/odoo/oca/README.md`. Tidak ada rujukan di `deploy.sh`, compose, `check-encryption.sh`,
  runbook, maupun Makefile (tidak ada Makefile di repo). Lalu job CI baru: ekstrak daftar CE dari image odoo-test,
  jalankan `check-odoo-deps.sh`.

## G9.4 File yang diubah / dibuat

Baru: `infra/s3-gateway/{haproxy.cfg,entrypoint.sh,README.md}`, `scripts/host/efn-deploy`, `scripts/check-odoo-deps.sh`, `.github/workflows/e2e-nightly.yml`,
`docs/runbooks/acme-dns-token.md`.
Diubah: `infra/minio/{Dockerfile,minio-init.sh}`, `infra/compose/{docker-compose.yml,docker-compose.dev.yml,.env.example}`,
`infra/backup/postgres/efn-walg.sh`, `infra/observability/{loki.yml,prometheus.yml}`, `infra/rabbitmq/rabbitmq.conf`,
`infra/traefik/{traefik.yml,dynamic/internal-tls.yml}`, `scripts/{secrets.list,gen-secrets.sh,gen-pki.sh,derive-secrets.sh,
deploy.sh,restore-test.sh,check-encryption.sh}`, `scripts/smoke/run-e2e-smoke.sh`, `.github/workflows/{ci.yml,deploy.yml,
security.yml}`, `docs/runbooks/{dev-stack-boot,internal-pki,key-custody,backup-restore}.md`,
**di luar infra (dicatat):** `services/ai-ocr-worker/efn_ocr/config.py`, `services/ai-ocr-worker/tests/test_config.py`.
Lokal (tidak di-commit): `secrets/*` baru, CA dev lama di `secrets/pki-ca.old-*`, `.env` (hapus `MINIO_BACKUP_USER`,
tambah `ETL_ENABLE_ODOO`), image rollback `efn/minio:legacy-2025-10-15`.

## G9.5 Rollback (reversible, ADR-030 Proposed)

- Storage: ganti ARG/repo di `infra/minio/Dockerfile` kembali (atau tag `efn/minio:legacy-2025-10-15` di dev). Format
  `.minio.sys` tidak diubah oleh boot SILO (diuji satu arah saja: lama → SILO; arah balik **belum diuji**).
- Filter: kembalikan `MINIO_ENDPOINT`/`WALG_S3_ENDPOINT`/target Prometheus ke `minio` dan tambahkan `internal, data`
  ke network `minio`. Access key acak tetap dipakai (tidak bergantung filter).

## G9.6 Sisa / butuh pihak lain

- **Lead/architect:** ADR-030 masih Proposed (Q-OS1..5); docs/04 §7.3 (endpoint storage = `s3-gateway`, network `objstore`)
  dan ADR-022 (hop baru s3-gateway; loopback E1 untuk Loki/redis_exporter/RabbitMQ metrics/Traefik metrics menggantikan E12)
  perlu diperbarui architect. Staging/produksi: **intermediate PKI harus diterbitkan ulang** (service baru `s3-gateway`
  di nameConstraints; deploy.sh gagal jelas bila belum) + 4 secret `MINIO_*_ACCESS_KEY` baru di GitHub Environment.
- **Superadmin (SEC-36):** pasang `efn-deploy`, `/etc/efn/deploy.conf`, cosign, sudoers, authorized_keys forced command,
  keluarkan `deploy` dari grup docker — **deploy.yml baru tidak akan jalan sebelum ini**. Belum diuji end-to-end dengan
  cosign/Rekor asli (hanya cosign palsu di container).
- **SEC-37:** delegasi DNS & token terbatas = tugas Superadmin; komponen ACME terpisah belum dibangun.
- **SN-2026-015** masih berlaku untuk kredensial yang langsung ke storage (root via `minio-init`); filter menutup jalur
  aplikasi. COMPLIANCE menunggu Q-OS3.
- **Filter:** log HAProxy ke stdout kehilangan baris saat host sangat lambat (permintaan smoke tidak semua tercatat);
  HAProxy sempat mencetak peringatan watchdog `stuck` lalu pulih (CPU host 100 %). Belum ada metrik HAProxy. Belum diuji
  base backup besar (`backup-push`) dan `restore-test.sh` lewat filter.
- **BFF/QA:** `mc rm` dengan user bff gagal karena mc melakukan stat (GetObject) dulu; BFF memakai `removeObject` langsung
  (tidak terdampak), tetapi QA sebaiknya menguji erasure foto DSAR lewat filter (smoke e lulus).
- **R7:** workflow belum pernah jalan di GitHub; ukuran runner perlu diputuskan (biaya larger runner).
- **docs/testing/E2E_SMOKE_REPORT.md** (milik QA) perlu diperbarui dengan hasil 88/88 dan R1/R5/R7.


# Gelombang 11 — SEC-41, SEC-42, SEC-47, SEC-48, sink audit Odoo ke Loki (6 Okt 2026)

Basis: `a810b55`, review keamanan r4 (`docs/reviews/SECURITY_REVIEW_2026-10-06-r4.md`). Semua diuji di stack dev
nyata (Docker Desktop/WSL2, profil core+data+ai+observability+ops) kecuali yang ditandai.

## G11.1 Status

| SEC | Status | Bukti |
|---|---|---|
| SEC-41 (s3-gateway encoding) | **Fixed + terverifikasi runtime** | Sebelum perbaikan bypass **terkonfirmasi** (bukan lagi PLAUSIBLE): `PUT /pg-backup/efn/x?rete%6Etion` → SILO `MissingContentMD5` (= handler PutObjectRetention), `PUT ?X-Amz-Cr%65dential=..` → `MissingFields` (jalur presigned), `?x=1;retention` juga lolos filter. Sesudah: 22 varian query/path + 2 varian header → 403 filter (`g.s3gw_encoded_variants_blocked`), ListObjectsV2 dengan nilai ber-`%` tetap diteruskan (`g.s3gw_encoded_value_get_forwarded`), WAL-G `backup-push` + `wal-push` (archived naik, failed 0), BFF PUT + ocr GET (smoke `b`) lolos |
| SEC-42 (efn-deploy) | **Fixed (kode + uji container); host belum dipasang** | manifest bertanda tangan + `cosign verify` image di host + pin root CA + anti-replay/downgrade; `scripts/host/test-efn-deploy.sh` 32/32 PASS (cosign palsu); uji mutasi (cek pin root dihapus) → FAIL terdeteksi |
| SEC-47 (supply chain) | **Fixed** | semua `FROM` (7 Dockerfile) + 22 `image:` compose + image CI/smoke dipin `tag@sha256`; Dependabot `docker` (+ `/infra/minio` baru) dan `docker-compose` memperbarui. Build ulang semua image lokal sukses; `odoo:19.0@sha256:dd9013…` = base image dev yang sedang berjalan (layer pip CACHED) |
| SEC-48 (root MinIO) | **Fixed + terverifikasi** | secret `minio_root_user` (`akid`, 20 hex) → `MINIO_ROOT_USER_FILE`; `efnadmin` dihapus dari compose/.env.example/deploy.sh. Volume lama: minio healthy, minio-init exit 0, 4 user IAM utuh, objek pg-backup terbaca, `efnadmin` ditolak |
| Tambahan lead: sink audit | **Selesai, diuji dengan baris sintetis** | Promtail → `stream="efn_audit_sink"`; ruler Loki → Alertmanager (mTLS): `EfnAuditUnexpectedPurge` (critical) dan `EfnAuditControlChange` (warning) diterima; purge `retensi:` tidak memicu alert. Retensi stream 400 hari (`retention_stream`) |

## G11.2 SEC-41 — aturan (`infra/s3-gateway/haproxy.cfg`)
1. Tolak `;` literal di query (semua method) dan `%` di **nama** kunci query (semua method); tolak `%` di mana pun
   di query untuk PUT/POST/DELETE (nilai tulis sah hanya uploadId/partNumber/versionId URL-safe). Double-encoding
   (`%2572…`) karenanya ditolak.
2. Daftar blok (`retention|legal-hold|object-lock|versioning|lifecycle|policy|encryption|replication|notification|acl`,
   `select`, `action`, `x-amz-credential|signature|algorithm`) dicocokkan pada `query,url_dec`, `-i`, pemisah `[&;]`.
3. Path: `path,url_dec` + `-i` untuk `/minio/*` (sebelumnya `/%6Dinio/admin` diteruskan ke storage); segmen
   `/./`, `/../`, `//` ditolak; `p_root` = `^/+$`.
4. Header: `x-amz-content-sha256` ganda ditolak (Go `Header.Get` membaca kemunculan pertama). Body form
   `application/x-www-form-urlencoded` berisi `X-Amz-Credential`/`retention` **diuji tidak** dirutekan SILO sebagai
   presigned/retensi (AccessDenied biasa), jadi tidak diblok.
5. Path-style vs virtual-host: `MINIO_DOMAIN` tidak diset → Host `pg-backup.s3-gateway` tetap path-style (diuji).
   Komentar di cfg: bila MINIO_DOMAIN diaktifkan, `p_root` wajib ditinjau.
6. `log-format` + `%Ta`. `s3gw()` di smoke kini `--path-as-is` (tanpa itu curl menormalkan `..`).

## G11.3 SEC-42 — desain
- `deploy.yml`: langkah bundel menulis `efn-release.json` (schema 1: `app_env`, `version`, `seq` = `run_number`,
  `git_sha` (= `workflow_run.head_sha` bila ada), `signer_ref`, 7 `images`, `pki.root_sha256`,
  `pki.extra_trust_sha256[]`) **ke dalam** `release.tar.gz` sebelum `cosign sign-blob`; `gzip -n` (deterministik).
  `IMAGE_*` tidak lagi dikirim lewat env.
- `efn-deploy`: kunci conf baru wajib `EFN_APP_ENV`, `EFN_GITHUB_REPO`, `EFN_IMAGE_PREFIX`, `EFN_PKI_ROOT_SHA256`
  (+ opsional `EFN_PKI_EXTRA_TRUST_SHA256`). Urutan: verify-blob → ekstrak ke `releases/<sha>.tmp` → manifest
  (skema, env, versi/ref, anti-downgrade terhadap `/var/lib/efn/last-release`) → env allowlist (tanpa `IMAGE_*`;
  `APP_ENV` = conf) → trust anchor (root tepat 1 sertifikat = pin = manifest; intermediate `openssl verify
  -x509_strict` ke root dipin + CA:TRUE; setiap EXTRA_TRUST dipin **dan** ada di manifest) → `cosign verify` tiap
  image di host (identitas tepat `build-push.yml@<signer_ref>`; kredensial pull di tmpfs `DOCKER_CONFIG`, bukan
  argv) → baru sinkron `/opt/efn`, tulis state, exec `deploy.sh`. Downgrade sah: `/etc/efn/allow-downgrade` berisi
  sha256 arsip (root 0600), sekali pakai.
- `deploy.sh`: lapis kedua — root CA wajib cocok `EFN_PKI_ROOT_SHA256` (dari wrapper atau dibaca deploy.conf saat manual).
- Runbook: `key-custody.md` §4a (conf baru) + §4b baru (tabel cek, rollback, rotasi root, risiko residu secret env),
  `internal-pki.md` rotasi root (pin sebelum deploy transisi).
- CI `infra`: langkah `efn-deploy regression (cosign palsu)` + shellcheck `scripts/host/*.sh`.

## G11.4 Sink audit Odoo (permintaan lead)
- `promtail.yml`: `match {service="odoo"} |= "odoo.addons.efn_audit.sink: EFN_AUDIT_SINK "` → regex + json →
  label `stream=efn_audit_sink`, `audit_op`. Baris yang memuat teks `EFN_AUDIT_SINK` tanpa prefiks logger (mis. URL
  di access log) **tidak** masuk stream (diuji).
- `loki.yml`: `retention_stream` `{stream="efn_audit_sink"}` 9600h (400 hari > minimum 365 hari log kontrol Odoo);
  stream lain 30 hari. Ruler lokal (`infra/observability/loki-rules/efn-audit-sink.yml`, mount read-only),
  `enable_api: false`, Alertmanager `https://alertmanager:9093/alertmanager` dengan mTLS (sertifikat loki).
- PKI: `loki|both` (EKU clientAuth; nama sama → **tidak** perlu intermediate baru, leaf terbit ulang otomatis).
  Alertmanager kini memakai `web-config-alertmanager.yml` (allowlist prometheus, grafana, **loki**);
  `web-config-ui.yml` (Prometheus) tidak berubah → sertifikat loki tidak bisa meng-query Prometheus.
- Alert: `EfnAuditControlChange` (warning, operation ≠ purge), `EfnAuditUnexpectedPurge` (critical, note bukan
  `retensi:`/`retention`/`erasure:`), `EfnAuditRetentionPurgeBurst` (warning, > 5 purge retensi/jam). Runbook
  `docs/runbooks/audit-sink-alerts.md`. Odoo menulis `retensi:` (bukan `retention`) — regex menerima keduanya.
- Diuji dengan container sementara berlabel `service=odoo` yang mencetak baris write param, purge `x 12`, purge
  `retensi: 3 log`: series Loki `audit_op=write|purge`, Alertmanager menerima tepat 2 alert yang benar.
  **Belum** diuji dengan Odoo nyata (modul `efn_audit` gelombang 11 belum di-upgrade di container berjalan; tidak
  saya restart karena Odoo engineer bekerja paralel).

## G11.5 Validasi

| Cek | Hasil |
|---|---|
| shellcheck (daftar CI + `scripts/host/*.sh`) | bersih |
| actionlint 1.7.12 | bersih |
| yamllint `infra .github` | 0 error |
| hadolint v2.12.0 (7 Dockerfile) | lulus (info DL3059 saja) |
| promtool config (25 rule) + 3 web-config, amtool | SUCCESS |
| compose config dev & prod (semua profil), secrets compose == secrets.list, port publish prod = traefik | OK |
| check-encryption.sh (di container docker:27-cli + jq; host tanpa jq) | OK |
| haproxy -c (image CI dipin) | rc 0 |
| update-cloudflare-ips --check | sinkron |
| `test-efn-deploy.sh` | 32/32 |
| Prometheus targets | 33/33 up |
| smoke E2E penuh | lihat G11.6 |

## G11.6 Smoke E2E
Setelah semua perubahan diterapkan (stack di-recreate dengan image ber-digest, root MinIO acak, Loki ruler):
- `g` (negatif enkripsi + s3-gateway): **19/19** (termasuk 24 varian encoded dan kontrol positif), dua kali.
- `a`, `b` (login OTP, consent, Price Hunter upload lewat s3-gateway, access key lama ditolak, SSE-S3 dengan root
  acak): semua lulus; `d` (QRIS) 7/7; `f` (ETL → ClickHouse, Metabase) 5/5; `c` 6 cek POS OTP gate lulus.
- **Terhenti (bukan regresi gelombang ini):** `c.audit_*` dan `e` bagian Odoo (`odoo_partner_*`, `audit_partner`)
  gagal karena kode `efn_audit` 19.0.1.1.0 milik Odoo engineer (pekerjaan paralel, kolom baru `prev_company_id`)
  sudah ter-mount tetapi DB masih 19.0.1.0.0 (belum `-u efn_audit`): `odoo shell` → `UndefinedColumn
  prev_company_id`. Bagian platform `e` (DSAR completed, `member_hash` NULL) lulus. Jalankan ulang `c e` setelah
  upgrade modul. `e.odoo_partner_hash_matches_bff` juga butuh partner E dari skenario `c` di run yang sama.


## G11.7 File
`infra/s3-gateway/{haproxy.cfg,README.md}`, `scripts/smoke/run-e2e-smoke.sh`, `scripts/host/efn-deploy`,
`scripts/host/test-efn-deploy.sh` (baru), `scripts/deploy.sh`, `scripts/secrets.list`, `scripts/gen-pki.sh`,
`.github/workflows/{deploy.yml,ci.yml}`, `.github/dependabot.yml`,
`services/{odoo,api-gateway,superapp-web,ai-ocr-worker,etl}/Dockerfile`, `infra/minio/{Dockerfile,minio-init.sh}`,
`infra/backup/postgres/Dockerfile`, `infra/compose/{docker-compose.yml,.env.example}`,
`infra/observability/{loki.yml,promtail.yml,web-config-ui.yml,web-config-alertmanager.yml (baru),loki-rules/efn-audit-sink.yml (baru)}`,
`docs/runbooks/{key-custody.md,internal-pki.md,secret-rotation.md,dev-stack-boot.md,audit-sink-alerts.md (baru)}`.

## G11.8 Sisa / butuh pihak lain
- **Host staging/prod (Superadmin):** pasang ulang `/usr/local/sbin/efn-deploy` (sha256 dari PR), tambah kunci baru
  di `/etc/efn/deploy.conf`; GitHub Environment butuh secret baru `MINIO_ROOT_USER` (deploy.sh menolak bila kosong).
  Rilis pertama: `last-release` belum ada → seq apa pun diterima.
- cosign/Rekor/Fulcio asli belum diuji end-to-end (hanya cosign palsu); `cosign verify` image privat GHCR dengan
  `DOCKER_CONFIG` tmpfs belum dicoba di host nyata.
- Secret dari env tetap tidak bertanda tangan (risiko residu, key-custody §4b); log `efn-deploy` (journald host)
  belum dikirim ke Loki — Promtail hanya membaca log container.
- Alert sink perlu diuji dengan Odoo nyata setelah upgrade `efn_audit` (lead/QA). `docs/04` §8.5 (matriks mTLS)
  perlu baris loki → alertmanager (dokumen architect).
- Makefile `lint` memakai `hadolint/hadolint` tanpa tag (di luar batas file).
- `infra/compose/.env` lokal developer lain mungkin masih memuat `MINIO_ROOT_USER=efnadmin` (diabaikan).


# Gelombang 12 — SEC-37 ACME terpisah, E12 ocr-worker, alert log, ops kecil (7 Okt 2026)

Basis: `59d37a5`. Diuji di stack dev nyata (Docker Desktop/WSL2) kecuali yang ditandai. Tidak ada commit.
Recreate di stack dev (UTC): 03:31 `acme-dns-init`, `acme-dns`, `traefik`, `superapp-web` (+ sidecar
`traefik-metrics-tls`, `superapp-web-tls`); 03:33 `odoo`, `metabase`, `grafana`, `uptime-kuma` (+ `odoo-tls`,
`odoo-ws-tls`, `metabase-tls`) agar label router baru berlaku; 03:37 `ocr-worker` (rebuild image dev) + 2 sidecar baru;
03:46 `etl`, restart `prometheus`, `blackbox-exporter`, `loki` (03:54 recreate `loki-tls` — restart loki memutus
namespace sidecar ±8 menit, promtail sempat 500 `empty ring`/connection refused).

## G12.1 Status

| Item | Status | Bukti |
|---|---|---|
| SEC-37 ACME terpisah dari Traefik | **Fixed (kode + uji Pebble); LE/Cloudflare nyata belum** | `scripts/test-acme-dns.sh` 10/10 PASS (8 run berurutan lulus; 1 run sebelumnya gagal tanpa detail tercatat → batas tunggu hot-reload dinaikkan 30→60 s); CI: Traefik tanpa token, `acme-dns` tanpa port + hanya `egress` |
| E12 ocr-worker `/metrics` | **Ditutup** | dari network `:9100`: CN prometheus 200, grafana/ocr-worker/tanpa sertifikat ditolak (000), `:9101` tidak terjangkau; Prometheus target `ocr-worker` up |
| SEC-22 sisa (`data.debug`) | **Didokumentasikan, tidak bisa ditutup di infra** | Odoo 19 `serialize_exception` selalu mengisi `debug`, tanpa opsi konfigurasi; usulan override `efn_*` (Odoo engineer) di `odoo-access-changes.md` §2 |
| Alert log (lead) | **Selesai + diuji** | `EfnAuditReconAnomaly`, `EfnAuditCopyFailing`, `EfnS3GatewayDeniedBurst` diterima Alertmanager (container sintetis berlabel `service=etl` / `service=s3-gateway`); `EfnAltchaMaxLevelSpike` (Prometheus, promtool OK, belum firing nyata) |
| ETL GL/audit copy env | **Compose + deploy.sh siap; job belum jalan di dev** | env `true` di container etl, tetapi image `efn/etl:dev` belum di-rebuild dengan kode data-AI (job GL/audit tidak muncul di log). Rebuild setelah `-u efn_audit` 19.0.1.2.0 + grant |
| Ops kecil | Selesai | lihat G12.4 |

## G12.2 SEC-37 — desain
- `acme-dns-init` (one-shot, root CAP_CHOWN+FOWNER, `network_mode: none`) → `acme-dns` (`goacme/lego:v5.5.2@sha256:1944e8…`,
  `infra/acme/acme-dns.sh`, user 65532:0, read-only, cap_drop ALL, `init: true`, hanya `egress`, secret
  `acme_dns_api_token` via `<ACME_DNS_TOKEN_ENV>_FILE`). `lego run` tiap 12 jam (gagal: 1 jam), satu sertifikat SAN
  erp./bi./ops./status., EC256, `--force-cert-domains`. Mode `disabled` (dev/CI) = idle sehat.
- Volume `acme-dns-certs` di-mount Traefik **read-only sebagai `/etc/traefik/dynamic`**; `base/` (dan `dev/`) bind
  mount di dalamnya. Ditemukan saat uji: watch provider file Traefik **tidak rekursif** — layout awal (volume di
  subdirektori `acme/`) gagal hot-reload. Nama file berversi (`efn-wg-<sha16>.crt`) karena Traefik tidak membaca ulang
  isi file berpath sama. Kunci 0640 grup 0 (Traefik root tanpa CAP_DAC_*).
- Traefik: resolver `letsencrypt-dns` dihapus, 7 router wg-https `tls.certresolver` → `tls: "true"`; secret dan env
  token dihapus. `deploy.sh`: `ACME_SERVER` (hanya URL LE prod/staging), `.env` + `ACME_DNS_PROVIDER`, sed provider di
  traefik.yml dihapus; `efn-deploy` allowlist + `deploy.yml` var `ACME_SERVER`.
- Monitoring: blackbox module `tls_wg_public` + job `probe-wg-tls` (`traefik:8443`, SNI `erp.<domain>`, CA publik;
  deploy.sh mengganti domain) → `EfnCertExpiringSoon/Critical` kini juga untuk wg-https + alert baru
  `EfnWgCertProbeFailed` (penerbitan pertama gagal). Dev: probe gagal (self-signed), sama seperti probe-https.
- Fixture uji di `scripts/acme-test/` (bukan `infra/`: check-encryption menolak URL http challtestsrv; tidak di-mount compose).

## G12.3 E12 — ocr-worker
Worker tetap HTTPS+mTLS (kode data-AI menolak plaintext di produksi) tetapi `METRICS_BIND_ADDRESS=127.0.0.1`,
`METRICS_PORT=9101`. ghostunnel tidak bisa meneruskan ke backend TLS, jadi dua sidecar di namespace worker:
`ocr-worker-metrics-tls` (server :9100, `--allow-cn=prometheus`) → 127.0.0.1:9102 → `ocr-worker-metrics-bridge`
(client, `--verify-cn=ocr-worker`, sertifikat klien ocr-worker) → 127.0.0.1:9101. `check-encryption.sh` kini juga
memeriksa ghostunnel mode client (`--cacert`, `--verify-cn`, listen loopback, tanpa `--disable-authentication`/
`--unsafe-listen`). Alternatif lebih sederhana (satu sidecar) butuh worker mengizinkan plaintext loopback di produksi
— keputusan data-AI/lead. ADR-022 E12 & docs/04 §8.5 perlu diperbarui (architect).

## G12.4 Ops kecil
- `superapp-web` dev: diukur 330 MiB setelah start → 477 → 487 MiB/512 (95 %) setelah 6–9 route `next dev`
  dikompilasi; limit dev 1024M (base/prod tetap 512M).
- `Makefile lint`: hadolint `v2.12.0@sha256:30a8fd…`, shellcheck `v0.11.0@sha256:61862e…`, `--config .hadolint.yaml`,
  daftar file = CI (+ infra/minio, scripts/host, acme).
- Runbook `odoo-access-changes.md`: ubah grup lewat UI/RPC, bukan `odoo shell`; §2 traceback JSON-RPC.
- journald host → Loki: service `promtail-journal` (prod override saja, `infra/observability/promtail-journal.yml`,
  `/var/log/journal` + `/etc/machine-id` ro, hanya `efn-deploy`, `efn-backup`, `efn-restore-test`, label `service=host-*`,
  retensi 400 hari). `promtail -check-syntax` OK; **belum diuji di host Linux nyata**.
- ETL: `ETL_ENABLE_GL`, `ETL_AUDIT_COPY_ENABLED` (base default false, dev true); `deploy.sh` pola R5 (eksplisit >
  .env lama > false; aktif otomatis setelah grant sukses) + pra-cek kolom `efn_audit_log.origin_user_id` dengan pesan
  jelas sebelum grant. `efn-deploy` allowlist + `deploy.yml` vars.
- Loki: `loki-rules/efn-ops-logs.yml` (3 alert); `alert-rules.yml` + `EfnAltchaMaxLevelSpike`, `EfnWgCertProbeFailed`.
- Runbook baru: `etl-audit-copy.md`, `s3-gateway-denied.md`, `host-journal-loki.md`, `odoo-access-changes.md`;
  diperbarui: `acme-dns-token.md` (§3–§5 baru), `tls-cert-expiry.md`, `otp-failure-rate.md` (ALTCHA), `README.md`
  (indeks: semua 25 runbook + alert Loki, tabel pengecualian hardening).

## G12.5 Validasi

| Cek | Hasil |
|---|---|
| shellcheck (daftar CI + acme) | bersih |
| actionlint 1.7.12 | bersih |
| yamllint `infra .github` | 0 error (1 warning lama line-length baris 416) |
| hadolint v2.12.0 (7 Dockerfile) | lulus |
| promtool config (27 rule) + 3 web-config; amtool | SUCCESS |
| compose config dev & prod (semua profil); secrets compose == secrets.list; port publish prod = traefik | OK |
| check-encryption.sh (container docker:27-cli) | OK |
| haproxy -c | rc 0 |
| update-cloudflare-ips --check, derive-secrets --self-test | OK |
| test-efn-deploy.sh | 32/32 |
| test-acme-dns.sh (baru, CI) | 10/10 |
| Prometheus targets | 34 up, 0 down |
| Smoke E2E `a b g` | **57/57** (`a g` saja: 39/40 — `g.pg_clients_tls` butuh koneksi efn_ocr yang hanya ada setelah `b`; bukan regresi) |

## G12.6 Sisa / butuh pihak lain
- **Superadmin**: delegasi zona + token terbatas (runbook §1), uji perdana dengan `ACME_SERVER` staging LE (§4).
  Belum ada uji dengan Let's Encrypt/Cloudflare nyata.
- **Odoo engineer**: override `data.debug` untuk non-`group_system` (SEC-22 sisa).
- **Lead/Odoo/data-AI**: urutan dev: `-u efn_audit` 19.0.1.2.0 → grant `odoo-readonly-grants.sql` → rebuild/recreate
  `etl` (job GL + audit copy). Sebelum grant, `EfnAuditCopyFailing` akan firing di dev.
- **Architect**: ADR-022 E12 → ditutup (pola 2 sidecar); docs/04 §8.5 baris ocr-worker; ADR-023 catatan acme-dns.
- Git: file `infra/acme/*.sh` dipanggil lewat `/bin/sh <file>` (tidak butuh bit exec); hook uji disalin + chmod di
  volume uji.
- `promtail-journal` belum diuji di host nyata; `EfnAltchaMaxLevelSpike` belum pernah firing nyata.


# Gelombang 13 — SEC-50/51/52/53/54, integrasi G13 (Odoo/data-AI/BFF), load test Fase 1 (7 Okt 2026)

Basis: `67039db` (+ commit G13 agent lain s.d. `67f4462`). Diuji di stack dev nyata. Tidak ada commit.

## G13.1 Status
| Item | Status | Bukti |
|---|---|---|
| SEC-50 env deploy | **Fixed (kode + uji)** | `test-efn-deploy.sh` **74/74** (42 kasus baru) |
| SEC-51 selector sink | **Fixed (infra)** | regex berjangkar format v2 + label `audit_op`,`critical`; uji container sintetis: 2 baris sah masuk, 5 palsu (substring di pesan login, logger `sinkx`, level WARNING, v1, tanpa `log_id`) ditolak; alert Critical/Control/Unsigned diterima Alertmanager, purge palsu TIDAK memicu alert |
| SEC-52 butir 1 dead-man | **Fixed** | 5 rule heartbeat ETL (`efn-etl-heartbeat`), `absent_over_time` dibuktikan (jendela 5 s → 1, 1 h → kosong) |
| SEC-52 butir 2 jangkar 400 hari | **Fixed** | `stream="efn_audit_anchor"` (chain_head, recon) retensi 9600h; kepala rantai dikecualikan dari redaksi HP (terbukti: hash memuat `0812345678` utuh); perbandingan otomatis = data-AI |
| SEC-53 acme-dns | **Fixed (kode + Pebble)** | service `acme-publish` (network none); `test-acme-dns.sh` 16/16 (PEM berisi router YAML ditolak, kunci multi-blok ditolak, `base/` tak bisa diubah) |
| SEC-54 CGNAT | **Mitigasi infra + dokumentasi** | `OTP_RL_SOURCE_LIMIT` 20→60; `::ffff:` sudah dinormalkan BFF (`common/net/client-ip.ts`); level berbasis rasio gagal = backlog BFF |
| Load test | **Selesai** | `tests/load/` k6; 5 menit, 200 771 req, error 0 % (di luar 404 transisi Traefik) |

## G13.2 SEC-50 — desain
- `scripts/lib/deploy-validate.sh` (baru): satu sumber aturan, di-source `efn-deploy` (dari rilis TERVERIFIKASI, sebelum
  `/opt/efn` ditulis) dan `deploy.sh` (lapis 2 + jalur manual).
- Topologi (`TRAEFIK_DOMAIN`, `EFN_PUBLIC_BIND`, `EFN_WG_*`, `COMPOSE_PROFILES`, `EFN_ALLOW_CONTROL_OFF`) dipin di
  `/etc/efn/deploy.conf`; env pemanggil dibuang. Konfigurasi aplikasi → manifest `config` (schema 2, ditandatangani;
  kunci allowlist, string tanpa karakter kontrol). Stdin hanya GHCR + `EFN_PKI_*` + secret; nama lain → ditolak dengan
  pesan SEC-50. `APP_ENV` = `EFN_APP_ENV`.
- Validasi: WG bind di 10.88/16|10.89/16 (+ `deploy.sh`: harus ada di `wg0`); CIDR kanonik dalam 10.88.0.0/15, prefix
  ≥ /16; public bind ≠ 0.0.0.0/loopback/subnet WG; regex per variabel; production menolak `RABBITMQ_STRICT_PERMISSIONS=
  false`/`ETL_AUDIT_COPY_ENABLED=false` (kecuali `EFN_ALLOW_CONTROL_OFF`), profil wajib `core,data,observability`.
  `.env` ditulis lewat `efn_write_dotenv` (kunci unik, tanpa kontrol, jumlah baris diverifikasi).
- `deploy.yml`: `CFG_*` → `config` (jq `$ENV`, cek kontrol di CI); step deploy hanya mengirim secret.
- sha256 `scripts/host/efn-deploy` saat laporan ini: `9c2bb1b0074408d1f094094d6d05f720c5e80c908e9b3221a218ad930f0ea273` (cocokkan ulang di PR).

## G13.3 Integrasi permintaan lead (Odoo / data-AI / BFF)
- Secret baru `efn_audit_sink_key` [SA] (odoo + etl, `EFN_AUDIT_SINK_KEY_FILE`), `metabase_admin_password`,
  `migration_api_key` (api-gateway, `MIGRATION_API_KEY_FILE`; `MIGRATION_ENABLED` default false, prod via manifest
  `config`, peringatan di deploy.sh bila true). `/internal/*` tetap dikecualikan router Traefik (dev: 404, diuji).
- PKI klien baru `etl` (→ loki-tls `--allow-cn=etl`, loki ikut network `data`; diuji: dengan sertifikat 200, tanpa
  sertifikat ditolak) dan `metabase-provision` (→ metabase-tls). **Staging/prod: terbitkan ulang intermediate.**
- Metabase: `build: ../metabase`, image `IMAGE_METABASE` (image ke-8: build-push matrix, deploy.yml digest+manifest,
  efn-deploy `IMAGE_VARS`, deploy.sh). One-shot `metabase-provision` (python ber-digest, `-I -B`, user 65534,
  read-only, network internal, mTLS) jalan otomatis begitu metabase(+tls) sehat; deploy.sh memeriksa exit 0. Dev: exit 0
  (14 kartu `completed`). Sidecar `metabase-tls` ikut di-recreate pada `up --force-recreate` (network_mode service).
- Heartbeat ETL: rule `efn-etl-heartbeat` (cycle/sales_fact_pos 45m, audit_copy 1h, stock_snapshot 3h, audit_recon 26h
  critical); rule G13 awal berbasis `EFN_AUDIT_CHAIN_HEAD` diganti agar tidak dobel.
- deploy.sh menerapkan `05-platform-loyalty-pos.sql` lewat pola ≥ 04 yang ada (sebelum `up`). Dev volume lama belum
  punya tabelnya → `earn` 500 di load test pertama; diterapkan manual 06:37 UTC (idempoten).
- CI: langkah `pytest + ruff infra/metabase` (23 passed lokal); hadolint-action (2.12) diganti hadolint v2.14.0
  ber-digest (2.12 tidak mengenal `ADD --checksum`); DL3062 minio di-ignore dengan alasan; shellcheck `-x` +
  `scripts/lib`, `tests/load`; cek EKU klien `etl`, `metabase_provision`.

## G13.4 Load test (detail: `tests/load/README.md`)
p95: login otp/request 195 ms, POS otp/request 158 ms, balance 118 ms, earn 170 ms, scout upload 413 ms; p99 ≤ 518 ms;
±669 req/s. Bottleneck: api-gateway CPU ±128 % (event loop, mode dev), postgres 75 % rata-rata, rabbitmq puncak
315 %, promtail puncak 200 % (log per request). Temuan: (1) Traefik merutekan container baru 20–25 s setelah recreate
(404 transisi) — relevan untuk deploy rolling; (2) load test menghabiskan budget OTP global per jam → `run.sh` kini
membersihkan `rl:otp:budget:*` (dev). Belum: image produksi, verify OTP sukses, redeem.

## G13.5 Validasi
| Cek | Hasil |
|---|---|
| shellcheck v0.11.0 (`-x`, daftar CI + lib + tests/load + acme sh) | bersih |
| hadolint v2.14.0 (8 Dockerfile) | lulus (info DL3059) |
| actionlint 1.7.12 | bersih |
| yamllint | 0 error (5 warning line-length regex) |
| promtool rules (27) / promtail -check-syntax / loki -verify-config | OK |
| compose config dev & prod semua profil; secrets compose == secrets.list; publish prod = traefik | OK |
| check-encryption.sh (docker:27-cli) | OK |
| test-efn-deploy.sh | 74/74 |
| test-acme-dns.sh | 16/16 (2 run berurutan) |
| pytest infra/metabase | 23 passed |
| Prometheus targets | 34/34 up |
| Smoke E2E `a b g` | 57/57 |
| Ruler Loki | semua rule G13 dievaluasi tanpa error |

## G13.6 Restart stack dev (UTC)
05:37–05:42 promtail crash loop (selector match pakai backtick → promtail tidak menerima; diperbaiki, log container
periode itu mungkin tertunda); 05:57 recreate `acme-dns-init`, `acme-dns`, `acme-publish` (baru), `traefik`,
`traefik-metrics-tls`; 06:25 `gen-secrets.sh` → **CA dev dibuat ulang** (service PKI baru) + semua leaf; 06:26–06:28
`up -d --force-recreate` SEMUA profil; 06:33–06:47 api-gateway di-recreate beberapa kali oleh load test; 06:37 migrasi
05 di PG dev; 06:57 hapus `rl:otp:budget:*` di Redis dev; ±06:50 recreate `acme-publish`. Container uji sintetis
`g13-fake-*` (label service=odoo/etl) menulis baris palsu ke Loki dev (log_id 99000x/99010x, company 990/991) — abaikan
saat investigasi dev; memicu EfnAuditControlChange/CriticalAccessChange/SinkUnsigned di Alertmanager dev.

## G13.7 Sisa / butuh pihak lain
- **Superadmin (host):** pasang ulang `efn-deploy`; tambah kunci topologi ke `deploy.conf` (key-custody §4c); secret
  GitHub baru `EFN_AUDIT_SINK_KEY`, `METABASE_ADMIN_PASSWORD`, `MIGRATION_API_KEY`; vars `METABASE_ADMIN_EMAIL`,
  `MIGRATION_ENABLED`; terbitkan ulang intermediate (etl, metabase-provision). Rilis schema 1 ditolak.
- **BFF:** level ALTCHA dari rasio kegagalan + kuota IP CGNAT (SEC-54 butir 2–3).
- **data-AI:** pakai `LOKI_URL`/`LOKI_CLIENT_*` (CN etl) untuk verifikasi `mac` sink v2 + perbandingan kepala rantai antar
  hari (SEC-52 butir 2–3). `sales_fact_pos` alert akan firing bila `ETL_ENABLE_ODOO=false` di production.
- **Odoo:** baris sink v1 (efn_audit < 19.0.1.3.0) tidak lagi masuk stream audit — pastikan modul ter-upgrade sebelum
  deploy G13.
- Belum diuji: host Linux nyata (wg0, cosign asli), LE/Cloudflare nyata, load test image produksi.

# Gelombang 13c — Penutupan infra loyalty (SEC-61/65), 2026-10-07

Dijalankan bersama peran QA (satu agent). Tanpa commit. Detail runtime: `docs/testing/E2E_SMOKE_REPORT.md` §G13c.

## G13c.1 Perubahan
- `scripts/deploy.sh`: migrasi `>= 04` sudah otomatis lewat glob (06 ikut); ditambah **cek wajib** `04/05/06-*.sql` ada
  di bundel sebelum loop (bundel tidak lengkap = deploy gagal, bukan dilewati diam-diam).
- `infra/compose/docker-compose.yml`: api-gateway `NO_COLOR: "1"` (log NestJS tanpa ANSI). Dev override:
  `LOYALTY_RECON_INTERVAL_MS: "300000"` (rekonsiliasi 5 menit agar teramati smoke).
- `infra/observability/promtail.yml` tahap 4: regex berjangkar penuh (`[Nest] <pid>  - <waktu> WARN|LOG|ERROR
  [PosLoyaltyService|LoyaltyReconService|MigrationService] EFN_LOYALTY_* {json ber-skema}`, hanya `service=api-gateway`)
  → `stream="efn_loyalty"`, label `loyalty_event` (anomaly|recon|migration), `kind`, `severity`, `recon_status`.
- `infra/observability/loki.yml`: retensi stream `efn_loyalty` 400 hari (bukti fraud/rekonsiliasi Finance).
- `infra/observability/loki-rules/efn-loyalty.yml` (baru): `EfnLoyaltyAnomalyCritical` (critical → page, per kind,
  15 m), `EfnLoyaltyAnomalyWarning` (warning, ringkasan per kind 1 j, satu notifikasi lewat group_by alertname),
  dead-man `EfnLoyaltyReconMissing` (`absent_over_time` heartbeat ok 2 j, for 15 m), `EfnLoyaltyReconFailing`.
- `infra/observability/alert-rules.yml` grup `efn-loyalty`: `EfnLoyaltyReconStale` dari gauge
  `efn_loyalty_recon_last_success_timestamp_seconds` (`max_over_time[26h] > 0` agar restart/deploy tidak memicu alert
  palsu; max antar replika). `alertmanager.yml`: inhibit Loki-dead-man → gauge-dead-man.
- Runbook baru `docs/runbooks/loyalty-anomaly.md` (+ indeks README); `dev-stack-boot.md` §2d migrasi dev G13c.

## G13c.2 Validasi
- promtool `check config` (28 rule) + `promtool test rules` untuk `EfnLoyaltyReconStale` (2 kasus: stale 2h20m → alert;
  gauge 0 tiga jam → tidak ada alert); amtool `check-config` (5 inhibit); `docker compose config -q` dev & prod;
  yamllint (`.yamllint.yml`, hanya warning panjang baris lama/baru); actionlint 0 temuan; shellcheck `deploy.sh`,
  `run-e2e-smoke.sh` bersih; ruff 0.16.10 check + format `scripts/smoke/` bersih.
- Runtime dev: ruler Loki mengevaluasi 4 rule baru; anomali nyata → Loki → `EfnLoyaltyAnomalyWarning` active di
  Alertmanager (±1 menit); heartbeat recon di Loki dan gauge Prometheus.

## G13c.3 Restart stack dev (UTC)
08:02 SQL 06; 08:03 upgrade Odoo (odoo berhenti ±4 menit); 08:07 recreate api-gateway/odoo/promtail/loki/alertmanager/
prometheus; 08:08–08:10 promtail restart loop (selector salah, diperbaiki); 08:14 api-gateway `MIGRATION_ENABLED=true`;
08:15–08:31 smoke (api-gateway mati ±1 menit di skenario `l`); ±08:33 api-gateway kembali `MIGRATION_ENABLED=false`.

## G13c.4 Sisa
- `EfnLoyaltyAnomalyCritical` belum dipicu nyata; dead-man Loki belum ditunggu 2 jam.
- Log ruler Loki `status=500` pada semua rule (lama juga) meskipun query 200 dan alert terkirim — selidiki (bukan blocker).
- Regex promtail bergantung format waktu locale default Node (`MM/DD/YYYY, h:mm:ss AM`); perubahan format = dead-man
  menyala (didokumentasikan di runbook). Opsi lebih kuat: logger JSON di BFF (backlog BFF).
- CI: `EFN_TOUR_MIN_TESTS` 9 → 15 (usul odoo-engineer) dan `docs/04` §6 `efn.bff.client.get` → `_efn_get` belum diubah
  (di luar batas file).

---

# Gelombang 14 (8 Okt 2026) — loyalty recon ETL, bug redaksi Promtail, cutoff & urutan grant G14

## G14.1 Perubahan
- `infra/observability/promtail.yml`
  - Tahap sink audit (2) dipindah **sebelum** redaksi; redaksi kini `stream!~"efn_audit_anchor|efn_audit_sink"`
    (sink tidak memuat PII by design; redaksi merusak MAC → `sink_mac_invalid` palsu).
  - **Bug PII baru ditemukan saat uji**: stage `replace` Promtail hanya mengganti *capture group*. Ekspresi lama
    `(\+?62|0)8[1-9][0-9]{7,11}` hanya mengganti awalan `0`/`62` → sisa nomor HP **masuk Loki**
    (`[PHONE_REDACTED]81298765432`); ekspresi Bearer tanpa grup **tidak meredaksi apa pun**. Diperbaiki:
    `((?:\+?62|0)8[1-9][0-9]{7,11})` dan `(?i)bearer\s+([a-z0-9\-_\.~+/]+=*)`. Sama di `promtail-journal.yml`.
  - Tahap 5 baru: `EFN_LOYALTY_RECON_ETL` dari `service=etl`, regex berjangkar
    `<ts> ((INFO|WARNING) efn_etl.loyalty_recon|ERROR efn_etl) EFN_LOYALTY_RECON_ETL {"status":"(ok|error)"...}`
    → `stream=efn_loyalty`, `loyalty_event=etl_recon`, label `recon_status`. Catatan: baris error dicetak logger
    `efn_etl` (`__main__`), bukan `efn_etl.loyalty_recon` — regex mengikuti kode.
- `infra/observability/loki-rules/efn-loyalty.yml` grup `efn-loyalty-etl-recon` (5m): `EfnLoyaltyEtlReconAnomaly`
  (critical, `last_over_time(...unwrap anomalies [26h]) > 0`), `EfnLoyaltyEtlReconOpenItems` (warning, open_items),
  dead-man `EfnLoyaltyEtlReconMissing` (`absent_over_time` status ok 26h, for 15m). Angka diambil dengan `| regexp`
  (baris diawali timestamp → `| json` gagal JSONParserErr; regexp juga tidak membuat label kinds_*).
- `infra/compose/docker-compose.dev.yml`: `ETL_LOYALTY_RECON_NOT_BEFORE=2026-10-07T07:09:00Z`.
  `infra/compose/docker-compose.yml` (etl): `ETL_LOYALTY_RECON_NOT_BEFORE: ${ETL_LOYALTY_RECON_NOT_BEFORE:-}`.
- SEC-50: `scripts/lib/deploy-validate.sh` — var masuk `EFN_RELEASE_CONFIG_VARS` (manifest bertanda tangan) dan
  `EFN_SECURITY_CONTROLS`; validasi UTC kanonik `YYYY-MM-DDTHH:MM:SSZ`, masa depan ditolak (cutoff di masa depan =
  rekonsiliasi buta). `scripts/deploy.sh`: nilai manifest → else `.env` lama (pola R5); production menolak
  **memajukan** cutoff tanpa `EFN_ALLOW_CONTROL_OFF` berisi `ETL_LOYALTY_RECON_NOT_BEFORE`; ditulis ke `.env`.
- `scripts/deploy.sh`: sebelum file grant, cek 5 kolom G14 (`pos_order.uuid/efn_loyalty_state`,
  `pos_config.efn_loyalty_earn_enabled`, `efn_pos_loyalty_outbox.original_order_id`,
  `efn_pos_loyalty_reservation.order_uuid`) → `die` jelas "efn_pos_loyalty_ledger belum terpasang (n/5)".
- `scripts/host/test-efn-deploy.sh`: uji 75–79 (format sah, spasi, offset +07:00, masa depan, bulan 13).
- Runbook: `loyalty-anomaly.md` §Rekonsiliasi harian ETL (3 alert + Batas diperbarui); `audit-sink-alerts.md`
  catatan G14 (sink sebelum redaksi; `sink_mac_invalid` lama pada baris berpola nomor = mungkin artefak).

## G14.2 Validasi
| Cek | Hasil |
|---|---|
| promtail `-check-syntax` (promtail.yml, promtail-journal.yml) | OK |
| Promtail **dry-run** terisolasi (docker_sd filter label `efn_test=g14`, tanpa push ke Loki) | sink → `efn_audit_sink` utuh; baris biasa: `+62…`, `62857…`, `0812…` → `[PHONE_REDACTED]`, `62812****789` tetap, Bearer → `Bearer [REDACTED]`; ETL ok/warning/error → `etl_recon` + `recon_status`; logger `efn_etl.other` & sisipan teks ditolak |
| MAC sink setelah pipeline (`efn_etl.sink.parse_sink_line`, image `efn/etl:dev`, kunci dev) | `valid=True`; varian ter-redaksi `valid=False` |
| promtool check rules (28) / amtool check-config | SUCCESS |
| yamllint `infra .github` | 0 error |
| shellcheck v0.11.0 (`scripts/*.sh scripts/lib/*.sh` + host) | bersih |
| `test-efn-deploy.sh` (python:3.12 container) | PASS 79 / FAIL 0 |
| compose config dev & prod (semua profil) | OK; var muncul di etl |
| `scripts/check-encryption.sh` (container docker-cli) | OK |
| Cek kolom G14 terhadap DB dev | 5 (terpasang); kolom fiktif → 0 |
| Runtime dev: baris sintetis `anomalies:2, open_items:1` (container `service=etl`) | `EfnLoyaltyEtlReconAnomaly` (critical) + `OpenItems` diterima Alertmanager ±5 menit |
| Run ETL nyata sekali (`ETL_RUN_ONCE`, NOT_BEFORE aktif) | `anomalies 0, open_items 3` (2 order review + 1 outbox failed, sama dengan data-AI); query nilai terakhir = 0 / 3 |

## G14.3 Restart stack dev (UTC)
16:16 restart `promtail` (config baru); 16:26 recreate `etl` (env NOT_BEFORE); 16:26 & 16:34 run ETL sekali
(`compose run`, container sementara). Container uji `efn-g14-*` sudah dihapus.
**Jejak sintetis di Loki dev**: 1 baris `etl_recon` palsu (container `efn-g14-synth1`, kind `g14_synthetic_test`)
tetap di stream 400 hari (tidak bisa dihapus tanpa delete API); sudah disusul baris nyata sehingga alert anomali
padam pada evaluasi 16:40 (terverifikasi di Alertmanager). `EfnLoyaltyEtlReconOpenItems` (3) tetap menyala = kondisi dev nyata.
Sink audit TIDAK diinjeksi ke Loki (uji dry-run) → tidak ada `sink_*` anomali baru.

## G14.4 Sisa / butuh pihak lain
- **Lead/.github**: `deploy.yml` belum mengirim `CFG_ETL_LOYALTY_RECON_NOT_BEFORE: ${{ vars.ETL_LOYALTY_RECON_NOT_BEFORE }}`
  (di luar batas file). Tanpa itu production memakai nilai `.env` lama / kosong (semua order dalam lookback).
- **PDP/keamanan (baru)**: bug redaksi berarti log Loki ≤ 30 hari (dan journal host) sebelum 8 Okt 16:16 UTC dapat
  memuat nomor HP hampir utuh dan token Bearer. Dev: 34 baris pola `[PHONE_REDACTED]\d{6}` dalam 30 hari. Bila
  staging/production sudah menerima log: nilai sebagai insiden PDP (breach-response-72h.md), pertimbangkan delete
  API Loki / tunggu retensi 30 hari. Stream 400 hari (`efn_loyalty`, `efn_audit_*`) berisi payload ber-skema tanpa HP.
- Dead-man ETL belum ditunggu 26 jam nyata (hanya query `absent_over_time` diverifikasi). Setelah deploy pertama G14
  dead-man bisa menyala sekali (runbook §EfnLoyaltyEtlReconMissing butir 4).
- Data-AI: regexp rule bergantung urutan kunci `status, anomalies, open_items` di `summary_payload` (kontrak; ubah
  bersama). Odoo: `note` sink jangan pernah memuat PII (kini tidak diredaksi).

---

# Gelombang 15 (9 Okt 2026): redaksi log, uji regresi Promtail, token di query string (SEC-72, SEC-73, SEC-78 infra)

## G15.1 Temuan pelacakan sumber (SEC-72a, Loki dev, 30 hari)
Query `sum by (service, container) (count_over_time({container=~".+"} |~ "\[PHONE_REDACTED\]\d{6}" [30d]))`.
Setiap kemunculan diklasifikasi menurut karakter sebelum `[PHONE_REDACTED]`: alfanumerik berarti potongan hex/ID.

| Sumber | Kemunculan | Sifat |
|---|---|---|
| `postgres` (dev `log_statement=all`) | ±300 | **Nomor HP fixture nyata-format.** INSERT `res_partner.phone` / `pos_order.mobile` "SMOKE Member A–N" (fiktif). Odoo menginterpolasi parameter di klien |
| `efn-g14-odoo`, `efn-g14-promtail` | 39 | Baris uji sintetis G14 (fiktif) |
| ocr-worker, api-gateway, s3-gateway, cadvisor, docker-socket-proxy, promtail, loki, etl, container test Odoo | ±200 | **False positive** pola lama: UUID report, ID container, ID thread, durasi, `row_hash` |
| traefik | 0 | Tidak ada HP. Juga tidak ada `token=`/`password=`/`code=`/`signature=`/`key=`/`secret=` di traefik/odoo/BFF/s3-gateway |

Probe R2 `member_ref=` tidak ditemukan lagi di Loki. Odoo werkzeug sudah `WARNING` (`odoo.conf.tpl:26`), sehingga baris
request + query tidak dicatat.

## G15.2 Perubahan
- `infra/observability/promtail.yml` + `promtail-journal.yml` (salinan persis): redaksi baru, urutan (a)–(d):
  - **(a)** parameter rahasia query/logfmt:
    - nama: `token`, `access_token`, `refresh_token`, `id_token`, `auth_token`, `password`, `passwd`, `pwd`,
      `csrf_token`, `secret`, `client_secret`, `api_key`, `apikey`, `signature`, `x-amz-*`, `otp`, `otp_code`;
    - prefiks: non-word, `\u0026` (Traefik JSON **meng-escape `&`**, diverifikasi dengan Traefik v3.7.13 nyata), `%3F`, `%26`;
    - `=` boleh `%3D`.
  - **(a2)** `code`, `key`, `sig`, `auth`, `session_id`, `sid`, hanya sebagai parameter query.
  - **(b)** kunci JSON/dict: `"password": "…"`.
  - **(c)** `Bearer` dan `Basic` (base64 wajib memuat digit/`+`/`/`/`=`).
  - **(d)** HP berpemisah spasi/titik/strip, `(+62)`, `%2B62`:
    - struktur `8[1-9]x(x)[sep]xxx(x)[sep]xxx(xx)`;
    - batas kiri & kanan non-alfanumerik, sehingga UUID/hash/ID tidak lagi terganti dan report_id bisa dikorelasikan lagi;
    - stage dijalankan **2×** karena batas kanan mengonsumsi satu karakter (nomor berurutan).
- `infra/traefik/traefik.yml`: `accessLog.fields.headers.defaultMode: drop` dibuat eksplisit. Komentar mencatat bahwa
  RequestPath memuat query dan redaksinya dilakukan di Promtail. Static config diuji boot dengan `traefik:v3.7.13`.
- **Perbaikan di sumber (SEC-72):** `docker-compose.dev.yml` postgres `log_statement=${PG_LOG_STATEMENT:-ddl}` (sebelumnya
  `all`), `.env.example` `PG_LOG_STATEMENT=ddl`. **Belum berlaku di stack dev yang berjalan**, karena postgres tidak
  di-recreate (dipakai agent lain). Berlaku saat recreate berikutnya.
- **Uji regresi CI baru:**
  - `scripts/test-promtail-redaction.sh`:
    - yq menyalin `pipeline_stages` produksi ke `infra/observability/tests/promtail/harness.yml`;
    - `promtail -dry-run` (image digest compose) atas `fixtures/{odoo,etl,api-gateway,traefik,generic}.txt`, 41 baris
      fiktif; `generic.txt` juga lewat pipeline journal;
    - keluaran dinormalisasi, lalu `diff` dengan `expected.txt` (62 entri).
  - Penjaga independen: tidak ada `SENTINEL*`, keluaran docker == journal, jumlah entri lengkap. `--update` untuk regenerasi.
  - Fixture mencakup:
    - HP format `08…`, `+62…`, `62…`, `+62 812 3456 …`, `62-812-…`, `0812-3456-…`, `0812 3456 …`, `(+62) …`,
      `0812.3456.…`, `%2B62…`, JSON, SQL, nomor berurutan;
    - Bearer, Basic, `access_token=`, `token=`, `password=`, `code=`, `X-Amz-Signature`, `%3Ftoken%3D`, JSON secret;
    - harus utuh: sink audit, jangkar, recon, loyalty, mask `62812****789`, UUID/ID/durasi, prosa.
- `.github/workflows/ci.yml`: job baru `promtail-redaction` (`-check-syntax` kedua config + skrip uji, `contents: read`,
  path-filter `infra`), ditambahkan ke `ci-gate.needs`. Baris modul Odoo tidak disentuh.
- Runbook baru `docs/runbooks/log-redaction.md`:
  - pola dan batasnya;
  - prosedur ubah pola + uji;
  - query deteksi staging/prod;
  - prosedur delete API Loki;
  - status near-miss dev.
- Runbook lain:
  - `security-incident.md:107` dikoreksi: Traefik mencatat query string, nilai rahasia diredaksi, s3-gateway tanpa query;
  - `dev-stack-boot.md` (`PG_LOG_STATEMENT`);
  - `host-journal-loki.md`;
  - `README.md` (indeks).

## G15.3 Validasi
| Cek | Hasil |
|---|---|
| `promtail -check-syntax` (promtail.yml, promtail-journal.yml; 3.2.1) | Valid |
| `scripts/test-promtail-redaction.sh` | OK, 62 entri. **Uji negatif:** pola HP lama dipasang → gagal (diff UUID/ID/thread + journal ≠ docker) |
| Runtime dev | promtail di-recreate 17:46 UTC. Hasil di Loki (dev): |
| — container sintetis `efn-g15-synth` (31 baris fixture) | 0 `SENTINEL`, semua HP/token teredaksi |
| — probe nyata ke Traefik dev `?member_ref=%2B62…&token=SENTINEL…` | `member_ref=%2B[PHONE_REDACTED]\u0026token=[REDACTED]` |
| shellcheck v0.11.0 (`-x`) | bersih |
| actionlint 1.7.7 `ci.yml` | bersih |
| yamllint 1.35.1 `infra .github` | 0 error (warning line-length lama) |
| gitleaks v8.21.2 (`infra/observability`, skrip) | no leaks |
| `docker compose config` dev & prod (semua profil) | OK (dev: `log_statement=ddl`) |
| `scripts/check-encryption.sh` (container docker-cli) | OK |

## G15.4 Sisa / butuh pihak lain
- **Hapus baris lama Loki dev (SEC-72c):** permintaan delete API **ditolak permission classifier di sesi agent**.
  Perintah siap pakai ada di `log-redaction.md` §4 (selector `stream=""`, end 2026-10-08T17:46Z). Pilihannya: operator
  menjalankan perintah itu, atau membiarkan retensi habis (±7 Nov 2026).
- Recreate promtail membaca ulang log container (posisi di tmpfs):
  - entri yang lebih tua ±3 jam ditolak Loki (`too far behind`);
  - entri ±3 jam terakhir terkirim ulang dengan redaksi baru, sehingga muncul duplikat berbeda isi (dev saja).
- Postgres dev perlu `up -d --force-recreate postgres` agar `log_statement=ddl` aktif (koordinasi lead).
- Log mentah container di host (`json-file`) tetap tidak teredaksi. Itu batas Promtail, bukan regresi.
- SEC-78 sisi Odoo (mask `note` purge sebelum MAC) belum. Rule deteksi `{stream="efn_audit_sink"} |~ HP` → warning
  belum dibuat (kandidat G16).
- Sebelum staging pertama menerima log: jalankan query §3 `log-redaction.md` di staging.

# Gelombang 15b (9 Okt 2026): alert KDS, detektor PII sink audit, rate limit self-order (SEC-75/76/78 sisi infra)

Masukan: laporan efn-odoo-engineer G15, bagian "Untuk devsecops". Tidak ada commit.

## G15b.1 Perubahan
- **Alert `EFN_KDS_TICKET_FAILED` (SEC-76).**
  - Ditambahkan tahap **(2b)** di `infra/observability/promtail.yml`. Tahap ini berjalan **sebelum** redaksi dan hanya
    untuk `service=odoo`.
  - Regex berjangkar penuh pada format log Odoo: logger `odoo.addons.efn_pos_kds.alert`, level ERROR, kunci JSON
    `sort_keys` persis.
  - Skema ketat: hanya angka/`null`/boolean, `mode` ∈ {kds, kds_printer, printer}, dan `error` berupa identifier
    Python ≤ 80 karakter.
  - Baris yang cocok masuk `stream="efn_kds"` (`kds_event="ticket_failed"`, label `kds_only`, retensi default 30
    hari).
  - Redaksi **tetap** diterapkan sesudahnya. `promtail-journal.yml` tidak berubah (tahap ini tidak relevan untuk
    journal host).
  - Rule baru `loki-rules/efn-kds.yml`:
    - `EfnKdsTicketFailed`: warning, ≥ 1 kejadian dalam 5 menit, `by (company_id, config_id, kds_only)`;
    - `EfnKdsTicketFailedSpike`: critical, ≥ 10 kejadian dalam 15 menit per company (PLACEHOLDER);
    - `EfnKdsAlertFormatUnrecognized`: warning, untuk baris dari logger alert yang tidak masuk stream (format berubah
      atau teks bebas di `error`). Penjaga ini dipasang supaya alert KDS tidak buta diam-diam.
  - Rule ini menggantikan usulan `|= "EFN_KDS_TICKET_FAILED"` yang tidak berjangkar. Dengan usulan itu, teks tersebut
    di catatan dapur atau pesan login sudah memicu alert.
- **Detektor PII sink audit (SEC-78).** Rule `EfnAuditSinkPiiPattern` ditambahkan di
  `loki-rules/efn-audit-sink.yml` (warning, `team: security`, `by (log_id, model)`, jendela 15 menit).
  - Pipeline:
    1. ambil payload;
    2. buang `"mac": "<hex>"`, karena hex 64 dapat memuat deret mirip HP/NIK;
    3. `| json log_id, model`;
    4. cocokkan HP Indonesia (pola = redaksi tahap (d)), email, 15–16 digit, dan NPWP berformat.
  - Hasil yang diharapkan selalu 0.
- **Rate limit self-order (Traefik).** File baru `infra/traefik/dynamic/odoo-self-order.yml` berisi:
  - router `odoo-pos-self@file`, **hanya** di `wg-https`, prioritas 220;
  - middleware `internal-only` + `secure-headers` + `ratelimit-odoo-pos-self` (5 req/s, burst 40, per RemoteAddr =
    per outlet; PLACEHOLDER);
  - service `odoo@docker` (https + serversTransport CA internal, tidak ada jalur plaintext baru).
  - Path yang ditangkap: `/pos-self/`, `/pos-self-order/`, `/pos_self_order/`, `/kiosk/`. Ini = seluruh 15 route
    `auth="public"` `pos_self_order` CE 19, diverifikasi dari kode di image `efn/odoo:dev`.
  - Komentar file dan runbook mencatat bahwa mode `mobile` dikunci di Odoo (`efn_setup_pos`, parameter
    `efn.pos.self_order_mobile_allowed`, SEC-75). Bila mode itu dibuka, jalurnya lewat BFF/ADR, bukan entrypoint
    publik.
  - Label compose tidak diubah (`infra/compose` di luar batas file). Router file-provider merujuk service docker.
- **Fixture** `tests/promtail/fixtures/odoo.txt` (+5 baris fiktif) dan `expected.txt` (62 → 67 entri):
  - 2 baris KDS valid → `efn_kds`;
  - teks `EFN_KDS_TICKET_FAILED` di catatan dapur → bukan `efn_kds`, HP teredaksi;
  - baris logger alert dengan `error` berisi nama + HP → bukan `efn_kds`, HP teredaksi;
  - baris sink audit berisi HP + email → tetap utuh di `efn_audit_sink`. Ini dokumentasi bahwa sink sengaja tidak
    diredaksi dan bahwa detektor yang menangkapnya.
- **Runbook:**
  - baru: `kds-ticket-failed.md`;
  - `audit-sink-alerts.md` §EfnAuditSinkPiiPattern;
  - `wireguard-outlet.md` §Self-order kiosk + baris troubleshooting 429;
  - `log-redaction.md` (catatan G15b);
  - `README.md` (indeks).

## G15b.2 Validasi
| Cek | Hasil |
|---|---|
| `promtail -check-syntax` (promtail.yml, promtail-journal.yml; 3.2.1) | Valid |
| `scripts/test-promtail-redaction.sh` | OK, 67 entri (docker + journal, 0 SENTINEL). Run pertama sebelum `--update` gagal tepat pada 5 baris baru (diff ditinjau) |
| LogQL di Loki 3.2.1 sekali pakai (baris fiktif, push API) | KDS: warning (company 1/config 3 = 11; company 2/config null = 1), spike company 1 = 11, format = 1 (baris palsu di logger lain tidak terhitung). PII: log_id 2–6 terdeteksi (HP berpemisah, `+62 …`, email, NIK 16, NPWP berformat); log_id 1/7/8 tidak terdeteksi (ter-mask, `mac` berisi deret angka, tanggal, ID kecil, `mac: null`) |
| Ruler Loki dev | rule baru termuat otomatis tanpa restart Loki, tanpa error/warn ruler |
| E2E dev (container sintetis `efn-g15b-synth`, `service=odoo`, company/config 999) | promtail → `efn_kds` → ruler → Alertmanager (mTLS): **EfnKdsTicketFailed, EfnKdsTicketFailedSpike, EfnKdsAlertFormatUnrecognized aktif**. Container dihapus; alert resolve sendiri setelah jendela 5/15 menit |
| `EfnAuditSinkPiiPattern` pada stream sink dev nyata | 0 (jendela 15 menit dan varian 7 hari: 16 baris sink, 0 temuan) |
| Traefik dev (v3.7.13, restart) | 150 POST paralel `/pos-self/ping` → 85×200 + 65×429; `/web/health` (router `odoo`) 150×200; RouterName `odoo-pos-self@file` untuk `/pos-self/1`, `/pos-self-order/get-slots`, `/pos_self_order/kiosk/…`, `/kiosk/payment/…`; `/pos-selfish` dan `/pos/ui` → `odoo@docker` |
| promtool check rules `alert-rules.yml` | SUCCESS, 28 (tidak berubah) |
| yamllint 1.35.1 (loki-rules, promtail, tests, traefik) | 0 error, hanya warning line-length (regex) |
| `scripts/check-encryption.sh` (docker:27-cli) | OK |

## G15b.3 Restart stack dev (UTC, 8 Okt)
- 18:41 dan 18:44: `traefik` (provider file di bind mount Windows tidak hot-reload). Routing kembali dalam ±20 detik.
- 18:45: `promtail`. Baca ulang log: entri lebih tua dari ±1 jam ditolak Loki (`too far behind`), dan sebagian kecil
  entri terkirim ulang (duplikat, dev saja).
- Loki dan Alertmanager tidak di-restart.

## G15b.4 Sisa / butuh pihak lain
- **README `efn_pos_kds` (Odoo engineer):** masih menyebut rule `|= "EFN_KDS_TICKET_FAILED"`. Perlu diganti dengan
  rujukan ke `loki-rules/efn-kds.yml` dan stream `efn_kds`.
- **Kontrak format log:** bila payload `EFN_KDS_TICKET_FAILED` berubah (kunci, separator `json.dumps`, mode stasiun
  baru), regex `promtail.yml` (2b) dan fixture wajib diubah bersamaan. Penjaga `EfnKdsAlertFormatUnrecognized` akan
  menyala.
- **Ambang dan routing:** ambang spike (10/15 menit), rate limit kiosk (5/s, burst 40), dan label `team: ops-outlet`
  masih PLACEHOLDER. Kalibrasi saat pilot KDS. Alertmanager masih punya satu penerima generik.
- **Label compose:** `traefik.http.routers.odoo.*` tidak disentuh. Router file-provider bergantung pada nama service
  docker `odoo`. Bila nama itu diganti di compose, router ini error (terlihat di log Traefik) dan `/pos-self` jatuh ke
  router `odoo` (tetap `internal-only`, limit 50/s).
- **Belum diuji:**
  - kiosk nyata di bawah beban (ambang);
  - path ter-encode (`/pos%2Dself/`). Traefik v3 menolak atau menormalkan sebagian; kasus yang lolos hanya mendapat
    limit `ratelimit-odoo`, bukan bypass allowlist.
- Delete API Loki untuk baris lama (G15.4) masih menunggu operator.

---

# Gelombang 16 — CI GitHub (repo privat, paket gratis) — 9 Okt 2026

Input: run pertama di `bctech-adm/efn-platform` (CI 37857432863, Security 37857433065, Build 37857432866, CodeQL
37857432889). Penyebab gagal: kuota artifact 500 MB, code scanning tidak tersedia, temuan scanner, gate Trivy image,
shellcheck, Dependabot 33 PR. Runbook baru: `docs/runbooks/ci-github-free-plan.md` (+ indeks README).

## G16.1 Perubahan

| Area | File | Isi |
|---|---|---|
| Artifact | `ci.yml`, `e2e-nightly.yml` | upload log hanya `if: failure()`, `continue-on-error: true`, retensi 2-3 hari, `if-no-files-found: ignore` -> kuota habis tidak menggagalkan test |
| Artifact | `build-push.yml` | SBOM Syft tidak lagi artifact (`upload-artifact: false`); di-`cosign attest --type spdxjson` (keyless) ke registry |
| Artifact | `security.yml` | gitleaks `GITLEAKS_ENABLE_UPLOAD_ARTIFACT=false` (temuan tetap redacted di log + summary) |
| Code scanning | `codeql.yml` | analisis hanya bila repo publik / `vars.CODE_SCANNING_ENABLED=true`; selain itu job notice. SAST tetap semgrep |
| Code scanning | `security.yml`, `build-push.yml` | semgrep `--json-output` + `--sarif-output`, ringkasan `file:baris/rule` (tanpa potongan kode) ke summary; Trivy fs/image: tabel gate -> log + summary; SARIF + upload hanya bila code scanning ada. Gate (exit 1) tidak berubah |
| Checkov | `security.yml` | SHA `bridgecrewio/checkov-action@2fb0e6d…` tidak bisa di-resolve lagi -> image `bridgecrew/checkov:3.3.21@sha256:9aefe565…` via `docker run` (tanpa action pihak ketiga) |
| Menit | `security.yml`, `codeql.yml`, `build-push.yml` | `concurrency` cancel-in-progress; izin per job (`security-events` hanya di job SARIF) |
| Menit | `e2e-nightly.yml`, `dast.yml` | jadwal E2E hanya bila `vars.E2E_NIGHTLY_ENABLED=true` (dispatch manual tetap); DAST hanya bila `vars.STAGING_DOMAIN` ada |
| Dependabot | `dependabot.yml` | grup minor+patch per ekosistem/aplikasi, grup security update, `directories` (docker 8 dir, pip 4 dir), limit 1-2, cooldown 7/14 hari, jadwal Senin WIB; tambah `/infra/metabase` |
| Shellcheck | `tests/load/run.sh`, `scripts/smoke/run-e2e-smoke.sh`, `infra/acme/acme-{dns,publish}.sh` | SC2317/SC2329 (trap), SC2015 -> `if`, SC2120 `bal()` tanpa argumen. Bersih di shellcheck 0.9.0 (versi runner) dan 0.11.0 |
| Base image | Dockerfile | node 22.12 (Des 2024) -> `22.23-alpine@0a7108bf…`; python `3.12-slim@05cda977…`; postgres 16.6 -> `16.15-bookworm@0ea6700a…` (+ service ci.yml, smoke, komentar compose); golang 1.27.1 -> `1.27.2-alpine@85dc1069…` (builder minio); metabase 0.52.4 -> `v0.58.35@a84b5dd5…` (LTS) |
| Hardening | `services/{api-gateway,superapp-web}/Dockerfile` | stage dev/build `USER node` (semgrep missing-user); runtime: hapus npm/npx/corepack/yarn (10 CVE HIGH di npm bawaan, tidak dipakai runtime) |
| Hardening | `infra/backup/postgres/Dockerfile` | hapus `gosu` (Go lama, 22 CVE; image selalu USER postgres) + kunci snakeoil ssl-cert (temuan secret Trivy) |
| Metabase | `infra/metabase/Dockerfile`, README | v0.58 membawa driver ClickHouse bawaan -> ADD jar 1.52.0 dihapus |
| Suppression | `.trivyignore.yaml` (baru), `nosemgrep`, `checkov:skip` | semua dengan alasan + `expired_at` (G16.3) |

## G16.2 Validasi lokal (container)

| Alat | Hasil |
|---|---|
| actionlint 1.7.7 | 0 temuan (termasuk info SC2153 lama di deploy.yml, diperbaiki) |
| yamllint 1.37.1 (`.github`, `infra`, `.trivyignore.yaml`) | 0 error (hanya warning line-length lama) |
| shellcheck 0.9.0 + 0.11.0, perintah persis CI | bersih |
| hadolint 2.14.0 semua Dockerfile | lulus (2 info DL3059 lama) |
| semgrep 1.179.0 (konfigurasi CI) | 3 temuan -> 0 (2 Dockerfile diperbaiki, 1 FP Odoo di-`nosemgrep`); flag `--json-output/--sarif-output` diverifikasi |
| checkov 3.3.21 | 4 FAILED -> 0 (3 skip beralasan) |
| Trivy 0.69.3 fs (vuln+secret+misconfig) | 0 dengan `.trivyignore.yaml` (lockfile/requirements di working tree saat ini, yang sedang diubah agent lain) |
| Trivy image: base node setelah strip npm, python slim baru, alpine 3.24, postgres hasil build, metabase hasil build, `odoo:19.0` base | semua 0 HIGH/CRITICAL fixed (postgres & metabase dengan pengecualian G16.3) |

## G16.3 Pengecualian (alasan + tanggal tinjau) — perlu persetujuan lead

- `.trivyignore.yaml`:
  - WAL-G v3.0.9 (`usr/local/bin/wal-g`): 10 CVE (stdlib Go 1.25.11, grpc 1.83.0) s.d. **2026-11-09**. Rilis terbaru
    upstream; klien CLI tanpa listener. Alternatif tanpa ignore: bangun WAL-G dari source dengan Go 1.27.2 (multi-stage,
    cgo libsodium), belum dikerjakan karena jalur backup R-T08 butuh uji restore penuh.
  - CVE-2021-38599 wal-g: FP (pseudo-version `v0.0.0-…+dirty`), s.d. 2027-01-07.
  - Metabase v0.58.35 `app/metabase.jar`: 6 CVE dependency Java upstream (lz4-java, httpcore5, httpcore5-h2,
    mssql-jdbc, zookeeper x2) s.d. **2026-11-09**.
  - Misconfig DS-0002 `infra/metabase/Dockerfile` (root di Dockerfile; `run_metabase.sh` turun ke `su metabase`,
    diverifikasi di image) s.d. 2027-01-07.
- `checkov:skip`: CKV_DOCKER_3 metabase (alasan sama); CKV_GHA_7 di `deploy.yml` & `e2e-nightly.yml` (input dispatch
  tidak memengaruhi build).
- `nosemgrep`: `services/odoo/addons/efn_pos_kds/models/efn_kds_station.py:262`, FP logger-credential-disclosure (yang
  di-log id stasiun/alasan/uid, bukan token). Satu-satunya sentuhan file aplikasi (komentar saja).

## G16.4 Temuan untuk agent lain (tidak diubah)

- `npm audit` api-gateway & superapp-web, `pip-audit` (job Python): dependency aplikasi, sedang dikerjakan agent lain.
- Gate Trivy image tetap gagal bila dependency aplikasi di image rentan: `node_modules` (api-gateway, superapp-web), pip
  `services/{etl,ai-ocr-worker}/requirements.txt`, `services/odoo/requirements.txt` (base `odoo:19.0` sendiri 0 temuan).
- `infra/metabase/provision.py` (efn-data-ai-engineer): verifikasi ulang terhadap Metabase 0.58 + driver ClickHouse bawaan
  (kunci `details` koneksi: `ssl`, `sslmode=strict`, `sslrootcert`).
- Stack dev yang sedang jalan belum di-rebuild dengan base image/Dockerfile baru.

## G16.5 Belum terverifikasi / sisa

- Belum ada run GitHub dengan perubahan ini (tidak di-push). Perlu dilihat di run pertama: ekspresi
  `github.event.repository.visibility`, `skip-setup-trivy` pada step SARIF (hanya jalan bila code scanning aktif), input
  `upload-artifact: false` sbom-action v0.24.3, `cosign attest`.
- Image minio (SILO + mc dari source, Go 1.27.2) tidak dibangun/di-scan lokal; temuan modul Go upstream SILO/mc bisa
  tetap muncul -> lihat summary run Build.
- Image odoo/etl/ai-ocr-worker/api-gateway/superapp-web lengkap tidak dibangun lokal (dependency sedang diubah agent lain).
- Dev stage `USER node`: `start:dev`/`next dev` dengan bind mount read-only belum diuji ulang di stack dev.
- `dependabot.yml` (`cooldown`, `directories`, `applies-to`) belum divalidasi oleh GitHub; error konfigurasi tampil di
  Insights > Dependency graph > Dependabot.
- DAST: `zaproxy/action-baseline` tetap mengunggah artifact `zap_scan` (kuota) bila staging diaktifkan.

## G16.6 Butuh keputusan / paket berbayar

1. Code scanning repo privat = GitHub Advanced Security (berbayar) atau repo publik, lalu set
   `CODE_SCANNING_ENABLED=true`. Tanpa itu CodeQL tidak berjalan (SAST hanya semgrep).
2. Kuota menit 2.000/bulan: E2E nightly penuh butuh runner self-hosted (`E2E_RUNNER`) atau spending limit.
3. Admin repo (sekali, setelah merge): hapus artifact lama dan tutup 33 PR Dependabot lama (perintah di runbook §3, §6);
   pastikan Dependency graph aktif (dependency-review).
4. Persetujuan risk acceptance G16.3 (WAL-G, Metabase) atau perintah membangun WAL-G dari source.
5. Upgrade Metabase 0.52 -> 0.58 (migrasi DB aplikasi tidak bisa dibalik): setujui sebelum dipakai di lingkungan berdata.

# Gelombang 16b — Enkripsi at rest DB aplikasi Metabase — 9 Okt 2026

Temuan (pelanggaran prinsip enkripsi at rest): Metabase v0.58.35 dev berjalan tanpa `MB_ENCRYPTION_SECRET_KEY` →
`metabase_database.details` (termasuk password ClickHouse `efn_metabase`, read-only) plaintext di DB `metabase` dan di
dump backup pra-upgrade.

## G16b.1 Perubahan

| File | Perubahan |
|---|---|
| `scripts/secrets.list` | Secret baru `metabase_encryption_key b64key32 [SA]` (32 byte acak base64). `gen-secrets.sh`, `deploy.sh`, `deploy.yml` membaca daftar ini otomatis → GitHub Environment `METABASE_ENCRYPTION_KEY` (staging/prod) **wajib diisi Superadmin sebelum deploy berikutnya** (deploy.sh `die` bila kosong). |
| `infra/compose/docker-compose.yml` | Secret top-level + mount **hanya** ke `metabase`. Override `entrypoint` shell inline dihapus → ENTRYPOINT image; `MB_DB_PASS_FILE` (didukung `run_metabase.sh` resmi, `file_env`) menggantikan `export MB_DB_PASS=$(cat …)`. |
| `infra/metabase/efn-entrypoint.sh` (baru) | POSIX sh: secret → `MB_ENCRYPTION_SECRET_KEY` → `exec /app/run_metabase.sh "$@"` (perilaku resmi root → `su metabase` uid 2000 dipertahankan; diverifikasi `docker top`: java uid 2000). **Fail-closed di semua lingkungan** (lebih ketat dari brief "selain dev-eksplisit": dev juga punya kunci dari gen-secrets, dan instance yang sudah terenkripsi memang tidak bisa jalan tanpa kunci): file tidak ada/tidak terbaca/kosong/< 32 karakter/berisi CR-spasi, atau kunci diberikan via env → exit 1. Nilai tidak pernah dicetak. |
| `infra/metabase/Dockerfile` | `COPY --chmod=0755 efn-entrypoint.sh` + `ENTRYPOINT ["/usr/local/bin/efn-metabase-entrypoint"]`. |
| `scripts/check-encryption.sh` | Cek baru (compose dev+prod): metabase wajib mount `metabase_encryption_key`, tanpa `MB_ENCRYPTION_SECRET_KEY` di environment, tanpa override `entrypoint`. |
| `.github/workflows/ci.yml` | shellcheck `-s sh` mencakup `infra/metabase/*.sh`. |
| `infra/metabase/README.md` | Baris `efn-entrypoint.sh`; bagian *Enkripsi at rest* (prosedur rotate one-off, verifikasi tanpa mencetak nilai, rotasi berikutnya); backup DB `metabase` kini **pg_dump \| age** (tidak ada dump plaintext di disk); jalur rollback v0.52.4 setelah dump dihapus; item *Belum* diperbarui. |
| `docs/runbooks/key-custody.md` | Baris kustodi `metabase_encryption_key` (Superadmin, keputusan klien 6 Okt). |
| `docs/runbooks/secret-rotation.md` | Baris rotasi `metabase_encryption_key` (rotate-encryption-key, bukan sekadar ganti nilai); `clickhouse_password_metabase` kini lewat `metabase-provision`, bukan UI. |
| `docs/runbooks/dev-stack-boot.md` | §2e migrasi stack dev lama (sekali). |

## G16b.2 Enkripsi data yang sudah ada (stack dev, nyata)

1. `bash scripts/gen-secrets.sh` → `secrets/metabase_encryption_key.txt` (44 karakter, tanpa CR).
2. `build metabase` → `stop metabase-tls metabase` → one-off resmi `java -jar metabase.jar rotate-encryption-key <kunci dari file>`
   (tanpa kunci lama = instance plaintext): log `Encryption key rotation OK.`, exit 0. Log tidak memuat kunci maupun
   password ClickHouse (grep -F -f file secret = 0).
3. `up -d --no-deps --force-recreate metabase metabase-tls` → healthy, restart 0, log `Database encrypted and
   MB_ENCRYPTION_SECRET_KEY correctly configured`.
4. DB (tanpa mencetak nilai): sebelum = `EFN DWH (ClickHouse)` `details LIKE '%password%'` **t**, JSON **t**; sesudah =
   **f / f** (Sample Database juga f/f). `strpos(details|setting.value, <password CH dari stdin>)` = **0** baris.
5. `metabase-provision` dua kali: exit 0 / exit 0, **33/33 kartu `completed`** tiap run (idempoten; PUT koneksi tetap
   tersimpan terenkripsi, dicek ulang f/f).
6. Fail-closed diuji pada image baru: tanpa secret, kunci via env, kunci 5 karakter, kunci ber-CRLF, file kosong →
   semuanya exit 1 dengan pesan jelas.
7. Dump plaintext `scratchpad/metabase-backup/metabase-pre-v0.58.dump` (514 KB) di-`shred -u` + direktori dihapus setelah
   langkah 1–6 lolos (catatan jujur: shred di NTFS/SSD tidak menjamin penghapusan fisik). Tag sementara
   `efn/metabase:pre-g16b-encrypt` dihapus; `efn/metabase:rollback-v0.52.4` dipertahankan (README: rollback = image
   itu + wrapper + DB kosong + provision ulang).

## G16b.3 Validasi lokal (container)

shellcheck v0.10.0 (`-s sh` wrapper; `-x` check-encryption/gen-secrets) bersih · hadolint v2.14.0 (digest CI) bersih ·
checkov 3.3.21 (digest & argumen = security.yml) exit 0, 0 FAILED · `docker compose config -q` dev & prod dengan
`.env.example` OK · secrets top-level == `scripts/secrets.list` (104) · `check-encryption.sh` OK (dijalankan di
container docker:27-cli + jq; host tanpa jq) + uji negatif jq rule baru (tanpa secret / env / entrypoint → ketiganya
FAIL) · pytest `infra/metabase` 23 passed + ruff check/format bersih.

## G16b.4 Rekomendasi & sisa

- **Rotasi password `clickhouse_password_metabase`** (ClickHouse read-only `efn_metabase`): sempat plaintext di DB dev dan
  dump dev (kini dihapus). Dev: opsional (hapus file → gen-secrets → clickhouse-init → metabase-provision). Staging/prod:
  tidak terdampak — belum pernah deploy, kunci aktif sejak start pertama.
- **Superadmin**: isi GitHub Environment `METABASE_ENCRYPTION_KEY` (`openssl rand -base64 32`) di staging & production
  sebelum deploy berikutnya + salinan offline (key-custody §1).
- Ditemukan di luar lingkup: scratchpad sesi masih berisi dump Odoo plaintext `efn_odoo_pre_g10.dump`,
  `efn_odoo_pre_g13c.dump` (data dev; usul lead: hapus bila tidak dibutuhkan untuk rollback).
- Belum: rotasi kunci kedua (old→new) belum diuji; uji di staging saat ada. Build image di CI (build-push) belum dijalankan
  untuk perubahan ini (hanya build lokal). `metabase-provision` tidak memeriksa status enkripsi (cek DB manual di README).

---

# Gelombang 16c (9 Okt 2026): perbaikan Security Review r9 (SEC-80..88)

Branch `chore/wave-16b-followups`, belum di-commit. Status per temuan + bukti: `docs/reviews/SECURITY_REVIEW_2026-10-09_r9.md`
§*Status perbaikan G16c* (8 Fixed, 1 Partial).

## G16c.1 Perubahan

| File | Isi |
|---|---|
| `scripts/verify-commit-gates.sh` (baru) | Gerbang rilis SEC-80: Actions API (`actions: read`), run per file workflow + `head_sha` + event push, job wajib `success`; `--preset release` = `ci.yml` `CI gate` + 4 job `security.yml`; polling, timeout, run absen = gagal. |
| `.github/workflows/build-push.yml` | Job `scan-pr` (PR, `contents: read`, cache baca saja) · job `gate` (non-PR, `needs` build) · job `build`: push sekali → Trivy BY DIGEST → Syft → cosign sign/attest (SEC-81/83); `DOCKER_BUILD_RECORD_UPLOAD=false`. |
| `.github/workflows/deploy.yml` | `resolve`: filter `workflow_run` event push + repo sendiri + main (SEC-82), SHA penuh, gerbang rilis + Build & Push success, output `git_sha`; `cosign verify --certificate-github-workflow-sha/-repository`; manifest `git_sha` = SHA yang dideploy. |
| `.github/workflows/ci.yml` | `persist-credentials: false` (kecuali `changes`, alasan dikomentari), `npm ci` + cache npm, pin `pip-audit==2.10.1`/`pylint-odoo==10.0.11`, `CI gate` `needs: changes` (lubang: paths-filter gagal → gate lolos). |
| `.github/workflows/security.yml` | Komentar header akurat (blokir rilis, bukan merge); label checkov "Dockerfile, GitHub Actions". |
| `infra/compose/docker-compose.yml` | `metabase`: `cap_drop: [ALL]` + `cap_add: [CHOWN, SETUID, SETGID]`, tanpa `clickhouse_password_metabase` (SEC-85). |
| `.trivyignore.yaml` | Statement DS-0002 faktual (perlu persetujuan ulang pemilik proyek). |
| `scripts/check-encryption.sh`, `scripts/deploy.sh`, `scripts/smoke/run-e2e-smoke.sh` | SEC-86: `entrypoint == null`, tolak image `metabase/metabase`; cek runtime `.Config.Entrypoint`. |
| `infra/metabase/README.md`, `docs/runbooks/secret-rotation.md` | SEC-87: prosedur rotasi jujur (argv terlihat `ps` host; mitigasi). |
| `docs/runbooks/ci-github-free-plan.md`, `docs/03_DEVSECOPS_PIPELINE.md` | Gerbang rilis, fakta Rekor (SEC-84), alur PR, pin digest checkov lokal. |

## G16c.2 Verifikasi nyata

- Gerbang rilis terhadap API GitHub nyata (read-only): ef346a0 → exit 0; 7c33351 (CI failure) → exit 1; SHA tanpa run →
  exit 1; nama job salah → `absent` exit 1.
- Rekor (publik, read-only): entri 3155542984 (`dsse`) = hash + sertifikat saja, `attestation: null`; 3155540831
  (`hashedrekord`). Sertifikat memuat repo, workflow@ref, SHA commit.
- Log run 62b1bf1: trivy-action 0.35.0 "Using YAML ignorefile" + "Some vulnerabilities have been ignored/suppressed".
- Metabase dev: recreate + restart dengan cap baru → healthy, `CapBnd=0xc1`, "correctly configured", `metabase-provision`
  exit 0; sidecar `metabase-tls` di-recreate (healthy).
- `docker compose config --format json`: key `entrypoint` selalu ada; `[]`/`''` → `[]`, absen/`null` → `null`.
  check-encryption (docker:27-cli + jq): asli OK, `entrypoint: []` FAIL, `image: metabase/metabase` FAIL.
- Java `@argfile` di image Metabase TIDAK menyembunyikan argumen (`/proc/self/cmdline` terekspansi).
- actionlint 1.7.7 bersih · shellcheck (scripts, lib, host, smoke, metabase) bersih · yamllint 0 error ·
  checkov 3.3.21 (digest + argumen security.yml) 0 FAILED · `docker compose config -q` dev/prod OK ·
  secrets compose == `secrets.list`.

## G16c.3 Belum terverifikasi / sisa

- Workflow baru belum pernah berjalan di GitHub (butuh push/merge ke `main`): job `gate`, Trivy remote by digest
  (auth lewat `docker login`), `cosign verify --certificate-github-workflow-sha`, `head_branch` untuk run tag `v*`.
- Rollback ke rilis sebelum G16c lewat `workflow_dispatch` akan ditolak gerbang (nama job checkov lama → `absent`). Belum
  ada rilis, jadi tidak berdampak sekarang.
- Prosedur rotasi kunci Metabase (lama → baru) belum diuji end-to-end: rotasi kunci dev ditolak permission sesi.
- Host `efn-deploy` belum mengikat `cosign verify` ke `git_sha` manifest (hanya `deploy.yml`). Usul gelombang berikut,
  dengan kasus uji `test-efn-deploy.sh`.
- Merge/push dengan check merah tetap mungkin secara teknis (paket gratis). Opsi: hook `pre-push` lokal atau org berbayar
  + ruleset.

## G16c.4 Lanjutan: re-review G16c (SEC-89..94), di atas commit `4996aa8`, belum di-commit

| ID | Perubahan | Bukti |
|---|---|---|
| SEC-89 | CI: semua job pada push ke `main` (filter path hanya PR, tanpa checkout di `changes`); gitleaks riwayat penuh pada push/jadwal (image v8.30.1 ber-digest); gate 75 mnt; runbook: aturan tag + estimasi menit | gitleaks lokal 67 commit bersih; actionlint/checkov bersih |
| SEC-90 | push by digest tanpa tag → scan → sign → `imagetools create` tag + verifikasi digest; `sha-*` hanya dari main | uji registry lokal: digest index tetap sama setelah tag |
| SEC-91 | `efn-deploy` `cosign verify` terikat `git_sha` manifest + repo | test-efn-deploy 87/87 PASS (80, 81 baru) |
| SEC-92 | `efn-deploy` production wajib `/etc/efn/approved-release` (`<X.Y.Z> <git_sha>`); key-custody §4d | kasus 82-87 PASS; API environments: production tanpa required reviewers |
| SEC-93 | concurrency Security per event | actionlint |
| SEC-94 | deploy memilih run build-push per ref (`main`/`vX.Y.Z`); izin `build` tanpa security-events/attestations | API nyata `@main` lolos, `@v1.0.0` gagal |
| (saran SEC-85) | check-encryption: cap metabase dikunci | negatif SYS_ADMIN → FAIL |

Validasi: actionlint 1.7.7, shellcheck (scripts/lib/host/smoke/metabase), yamllint 0 error, checkov 3.3.21 0 FAILED
(github_actions 658 passed), `docker compose config -q` dev/prod, check-encryption (asli OK + 3 negatif FAIL),
test-efn-deploy 87/87.

Belum / perlu tindakan:
- Job Odoo test/tour belum pernah selesai di GitHub; run CI penuh pertama di `main` bisa merah → gerbang rilis tertahan
  sampai hijau. Estimasi biaya ±135-195 menit runner per merge ke `main` (kuota 2.000/bulan ≈ 10-14 merge).
- Superadmin: pasang ulang `efn-deploy` di host (sha256 dari PR), isi `/etc/efn/approved-release` sebelum rilis
  production; admin repo: Environment `staging` → deployment branch `main` saja (saat ini tanpa aturan).
- `head_branch` = nama tag untuk run push tag (SEC-94) dan seluruh alur build/tag/deploy baru belum terbukti di run nyata.

---

# Gelombang 17 — infra penjualan online Fase 3 (docs/04 §10, ADR-034 Proposed), 9 Okt 2026, belum di-commit

> **Ringkasan manajemen.** Jalur aman Super-App → Odoo untuk pesanan online sudah dibangun dan diuji nyata: BFF hanya
> bisa memanggil 10 fungsi pesanan online di Odoo lewat penjaga khusus (`odoo-rpc-gateway`) dengan sertifikat di kedua
> sisi; 97 uji penyimpangan/serangan berperilaku sesuai harapan (ditolak), 10 fungsi sah lolos. Antrean pesanan, kunci
> rahasia baru, alert, dan runbook (rotasi kunci API, operasi pesanan) siap. Online tetap **mati** di produksi
> (`ONLINE_ENABLED=false`).

## G17.1 File (batas §10.15)
- **Baru:** `infra/odoo-rpc-gateway/{haproxy.cfg,entrypoint.sh,test-gateway.sh,README.md}`,
  `infra/compose/docker-compose.online-fnb.yml` (mount key FNB hanya saat kanal FNB aktif, pola payment overlay),
  `infra/observability/loki-rules/efn-online.yml`, `docs/runbooks/online-orders.md`, `docs/runbooks/odoo-online-api-key.md`.
- **Ubah:** `infra/compose/docker-compose.yml` (service `odoo-rpc-gateway`; `odoo-tls --allow-cn=odoo-rpc-gateway`;
  env BFF §10.13 + secret; Odoo `EFN_ONLINE_EVENT_HMAC_FILE`; `MINIO_BUCKET_CATALOG`; superapp-web
  `EFN_PAYMENT_SIMULATOR: "false"` bernilai tetap), `docker-compose.dev.yml` (superapp-web `EFN_PAYMENT_SIMULATOR: "true"`),
  `infra/rabbitmq/rabbitmq-entrypoint.sh`, `infra/redis/redis-entrypoint.sh` (`~cart:* ~oq:* ~ocat:*`),
  `infra/minio/minio-init.sh` (bucket `catalog` SSE-S3 + policy BFF), `infra/observability/{alert-rules.yml,
  alertmanager.yml, promtail.yml, promtail-journal.yml, tests/promtail/*}`, `scripts/{secrets.list, gen-pki.sh,
  check-encryption.sh}`, `.github/workflows/ci.yml`.

## G17.2 Keputusan teknis
- **PKI:** `odoo-rpc-gateway|both` (server untuk BFF, klien ke odoo-tls); `api-gateway` server → **both** (sertifikat
  BFF dipakai sebagai klien CN `api-gateway`; tidak perlu secret `tls_api_gateway_client_*`). Konsekuensi staging/prod:
  **intermediate wajib diterbitkan ulang** (nameConstraints belum memuat `odoo-rpc-gateway`; gen-pki.sh menolak di
  staging/prod sampai Superadmin `make pki-intermediate`, runbook internal-pki.md#service-baru). Dev: CA dev dibuat ulang.
- **Secret baru:** `odoo_api_key_online_rtl`, `odoo_api_key_online_fnb` (`literal:disabled`; key dibuat DI Odoo lewat
  runbook), `odoo_online_event_hmac` (`random`; mount odoo + api-gateway saja, dicek check-encryption). **Kunci
  enkripsi kode ambil:** kontrak §10.3.6 = AES-256-GCM §7.2 = key ring `pii_encryption_key` (AAD
  `efn:online_order_part.pickup_code_enc:v1`) → tidak ada secret baru.
- **Gateway:** pola s3-gateway; path dicocokkan MENTAH (tanpa decode) dengan 10 string; allowlist header (PCRE2);
  `X-Odoo-Database` dipaksa; penolakan membawa `X-EFN-Gateway-Deny: <alasan>` + log `why=`.
- **RabbitMQ:** 4 queue §10.9 quorum + DLQ + `x-delivery-limit: 10`; `efn-bff` read 4 queue (strict), write tetap
  `amq.default`. Argumen queue lama tidak diubah.
- **Alert:** grup Prometheus `efn-online` (OrderStuck, AmountMismatch, DlqNotEmpty, OdooAuthFailure, CircuitOpen,
  CatalogStale, RefundPending [proposed], ApiKeyExpiringSoon/Critical/Unknown — R-T15 lewat metrik textfile
  `efn_odoo_api_key_expiry_timestamp_seconds{channel}` yang ditulis runbook); `EfnDeadLetterNotEmpty` mengecualikan
  `order.*`; Loki: `EfnOnlineReconDeadman` (20 m; hanya bila heartbeat ada dalam 24 j), `EfnOnlineAnomaly*`,
  `EfnOdooRpcGatewayDenied`. Promtail tahap 6 (`stream="efn_online"`) + redaksi kode ambil `ABCD-EFGH`
  (`[PICKUP_REDACTED]`) dan kunci `pickup_code|qr_string|quote_hash` (query/JSON). Idempotency-Key tidak diredaksi
  (bukan rahasia; deterministik dari id yang boleh di-log).
- **Tambahan lead:** `EFN_PAYMENT_SIMULATOR` true hanya dev; base bernilai tetap `"false"` (bukan `${..}`, tidak bisa
  diubah lewat .env, tidak ada di allowlist deploy-validate); `check-encryption.sh` menolak render prod dengan nilai
  selain false dan menolak `ODOO_ONLINE_MOCK` di api-gateway prod.

## G17.3 Verifikasi nyata (stack dev, Docker Desktop 29.8.1)
- CA dev dibuat ulang (service PKI baru) → seluruh stack `up --force-recreate`: semua container healthy; `minio-init`
  membuat bucket `catalog` (SSE-S3).
- `test-gateway.sh`: **97/97 PASS** (diulang setelah Docker Desktop restart): tanpa sertifikat / CA asing (CN
  api-gateway) / sertifikat EKU server saja / HTTP polos / BFF langsung ke `odoo:8443` → handshake gagal; CN traefik →
  403 `cn`; 6 method → 405; 23 path lain (search_read, write, action_efn_ready, handover wizard, call_kw, jsonrpc,
  xmlrpc, trailing slash, huruf besar, `//`, dot segment, `;`, backslash) + 14 varian encoded (`%61`, `%2F`, `%2e%2e`,
  `%252e`, `%00`, `%c0%ae`, `%3F`, `%23`, …) → 403; query/fragment/absolute-form/HTTP 1.0 → ditolak; kasus header
  (method override ×3, X-Original-URL, X-Rewrite-URL, X-Openerp-Session-Id, Upgrade h2c/websocket, TE, Expect,
  Proxy-Authorization, header asing, Host salah ×2, Content-Type salah/ganda, Authorization hilang/ganda/Basic/pendek)
  → ditolak; body 300 KB → 413; 10 path sah + Cookie/X-Forwarded-*/X-Odoo-Database klien (dibuang) → sampai Odoo
  (401 dari Odoo karena token uji). Health check backend = mTLS CN odoo-rpc-gateway ke odoo-tls.
- **Bug ditemukan uji & diperbaiki:** regex `^bearer [..]` di HAProxy (spasi = pemisah argumen → pola OR) meloloskan
  `Authorization: Basic …`; kini `\x20`. Upgrade tanpa `Connection: upgrade` dibuang parser H1 HAProxy sebelum aturan
  (diverifikasi dengan HAProxy echo terpisah).
- Loki: log gateway tanpa token (query `bearer|token uji` = 0); query `EfnOdooRpcGatewayDenied` mengelompokkan alasan
  dengan benar; ruler mengevaluasi 4 rule baru. Prometheus memuat 10 rule `EfnOnline*`.
- RabbitMQ 3.13.7: 8 queue dengan argumen benar; uji nyata publish + 11× reject-requeue → pesan pindah ke
  `order.compensate.dlq` dengan `x-first-death-reason: delivery_limit` (butir §10.14 "dukungan x-delivery-limit" =
  **terverifikasi**; batas 10 = maksimal 11 kali pengiriman). Izin `efn-bff` sesuai.
- Lint: shellcheck (bash + sh) OK, actionlint 1.7.7 OK, yamllint OK (warning panjang baris pola lama), hadolint OK,
  checkov 3.3.21 (argumen security.yml) 0 FAILED, promtool 38 rule OK, amtool OK, `haproxy -c` OK,
  `docker compose config -q` dev/prod OK, secrets compose (+payment +online-fnb) == secrets.list, prod hanya publish
  traefik, `check-encryption.sh` OK (jq dalam container; host tanpa jq), `test-promtail-redaction.sh` 79 entri OK.

## G17.4 Belum / untuk lead
- **Staging/prod:** terbitkan ulang intermediate PKI sebelum deploy G17; isi GitHub Environment
  `ODOO_API_KEY_ONLINE_RTL`, `ODOO_API_KEY_ONLINE_FNB` (boleh `disabled`), `ODOO_ONLINE_EVENT_HMAC`.
- ~~Config rilis `ONLINE_*`, `.env.example`, indeks runbook~~ → selesai di G17.5.
- BFF: heartbeat `EFN_ONLINE_RECON {"status":"ok|error",…}` (konteks logger `Online*`, level LOG/ERROR) belum ada di
  kode BFF — tanpa itu `EfnOnlineReconDeadman` tidak aktif (aman, tetapi buta). Gauge refund terbuka per status
  diusulkan. Placeholder secret `disabled` ditolak BFF (terlalu pendek) saat kanal aktif — dev memakai
  `ODOO_ONLINE_MOCK` atau key nyata.
- Uji gateway belum di CI (butuh stack + Odoo); CI hanya `haproxy -c`. Respons 200 dari E-28 terpasang belum diuji.
- `EfnOdooRpcGatewayDenied` menyala di dev setelah `test-gateway.sh` (sengaja).
- Odoo test CI: `efn_online_order efn_setup_online` ditambahkan ke `EFN_TEST_MODULES` (dilewati bila belum ada).

## G17.5 Lanjutan (batas file diperluas lead): config rilis online
- `scripts/lib/deploy-validate.sh`: 12 variabel (`ONLINE_ENABLED`, `ONLINE_CATALOG_ENABLED`, `ONLINE_CHANNELS`,
  `ONLINE_FULFILMENTS`, 7 angka `ONLINE_*`, `ODOO_RPC_TIMEOUT_MS`) masuk `EFN_RELEASE_CONFIG_VARS`. Validasi: boolean
  `true|false`; kanal `^[A-Z][A-Z0-9-]{1,15}` (≤ 8, koma); fulfilment allowlist `pickup|delivery`; angka bulat tanpa
  nol depan dengan rentang (timeout bayar & TTL quote 60..3600, order terbuka 1..20, sinkron katalog 60..86400,
  kode ambil 1..30 hari, retensi 1..3650 hari, `ODOO_RPC_TIMEOUT_MS` 1000..30000 = di bawah timeout gateway 30 s).
  Production `ONLINE_ENABLED=true` hanya dengan `PAYMENT_PROVIDER` xendit|midtrans. `EFN_DEV_ONLY_SWITCHES`
  (`ODOO_ONLINE_MOCK`, `EFN_PAYMENT_SIMULATOR`) ditolak bila bukan false di staging/production (jalur manual), dan
  sebagai kunci manifest tetap ditolak skema (kunci tidak dikenal).
- `scripts/deploy.sh` (dibutuhkan agar config sampai ke compose): baris `.env` `ONLINE_*` (default mati, angka kosong =
  default compose); `ONLINE_ENABLED=true` → setiap kanal wajib punya key non-placeholder; kanal `FNB` → overlay
  `docker-compose.online-fnb.yml` (kanal brand `FNB-<X>` butuh overlay baru — dicatat di komentar).
- `.github/workflows/deploy.yml`: `CFG_ONLINE_*` + `CFG_ODOO_RPC_TIMEOUT_MS` dari GitHub variables (default
  `false`/`RTL`/`pickup`, angka kosong).
- `infra/compose/.env.example`: blok G17 dengan komentar rentang (diverifikasi `docker compose --env-file .env.example
  config`: komentar inline tidak ikut nilai).
- `docs/runbooks/README.md`: 4 baris alert (Prometheus + Loki), 2 prosedur, baseline hardening `odoo-rpc-gateway`.
  `online-orders.md`: kill switch staging/prod via config rilis + syarat menyalakan di production.
- `scripts/host/test-efn-deploy.sh`: +14 kasus (88–101: online prod tanpa provider nyata ditolak, provider nyata
  diterima, boolean/kanal/fulfilment/angka/rentang tidak valid ditolak, default diterima, mock/simulator ditolak,
  kunci manifest `ODOO_ONLINE_MOCK` ditolak). **PASS=101 FAIL=0** (container python:3.12-bookworm seperti CI).
- Ulang validasi: shellcheck (seluruh `scripts/*.sh scripts/lib scripts/host` + infra) OK (SC2015 baru di deploy.sh
  diperbaiki), actionlint OK, yamllint 0 error, checkov 0 FAILED, compose config dev/prod OK, `check-encryption.sh` OK.
- Tambahan lead #2: `07-platform-online.sql` masuk daftar migrasi WAJIB `deploy.sh` (bundel tanpa 07 = deploy gagal);
  dev `api-gateway` `ONLINE_ENABLED=true` + `ONLINE_CATALOG_ENABLED=true` (render prod tetap false; `ODOO_ONLINE_MOCK`
  absen di dev dan prod). Validasi: compose config dev/prod OK, check-encryption OK, shellcheck OK, efn-deploy 101/101.
  Container `api-gateway` dev BELUM di-recreate: perlu key RTL nyata di `secrets/odoo_api_key_online_rtl.txt` (runbook
  odoo-online-api-key §1, setelah E-28 + user teknis `efn_online_rtl` ada) dan migrasi 07 di volume dev lama.

## G17.11 Sizing pilot opsi B (keputusan pemilik proyek 10 Okt 2026)
- `docker-compose.prod.yml`: gateway `ODOO_RPC_MAXCONN ${..:-2}`; api-gateway `ODOO_RPC_PUBLIC_MAX_CONCURRENCY ${..:-1}`,
  `ODOO_RPC_SAGA_MAX_CONCURRENCY ${..:-1}`. Base compose: kedua pool `${..:-2}` (dev 2 + 2 = maxconn 4). Render tanpa
  .env: dev 4/2/2, prod 2/1/1.
- `deploy-validate.sh`: kedua pool masuk `EFN_RELEASE_CONFIG_VARS` (rentang 1..16) + aturan PUBLIC + SAGA ≤ MAXCONN
  (default efektif prod 2/1/1); `deploy.sh` `.env`; `deploy.yml` `CFG_ODOO_RPC_{PUBLIC,SAGA}_MAX_CONCURRENCY`;
  `.env.example`; runbook `online-orders.md#sizing-worker` (keputusan opsi B ditandai, tabel skenario + pool).
- Uji: efn-deploy **109/109** (+6 kasus 104–109), compose config dev/prod, check-encryption, shellcheck 0.9.0, actionlint OK.
- Catatan: prod 2 replika BFF → permintaan paralel maks. 2 × (1+1) = 4 ke gateway; Odoo tetap maks. 2 koneksi online,
  sisanya antre/503 di gateway (aturan per replika sesuai instruksi lead; pertimbangkan replika BFF 1 atau aturan
  replika × pool ≤ maxconn bila antre sering).

## G17.10 Perbaikan CI PR #36 (HEAD a522da6), 10 Okt 2026
- **ruff 0.16.10:** `services/odoo/addons/efn_setup_company/tests/__init__.py` (I001, import digabung; perilaku sama).
- **shellcheck 0.9.0 (versi CI):** `scripts/smoke/run-e2e-smoke.sh` SC2001 ×2 → helper `nomsg()` (`sed -E`, perilaku
  sama: isi `message` Odoo disamarkan di detail check). CI kini juga shellcheck `test-gateway-load.sh`.
- **gitleaks 8.24.3:** 1 FP `services/superapp-web/tests/online-routes.test.ts:270` (header Idempotency-Key
  buatan klien, fixture Vitest) → `.gitleaksignore` + alasan. Bukan secret. Re-scan rentang PR: no leaks.
- **semgrep 1.179.0:** 1 FP `efn_online_event_outbox.py:222` (log nama env var HMAC, bukan nilai) → `nosemgrep` +
  alasan (pola sama dengan efn_kds_station.py). Re-scan penuh `--error`: rc 0.
- **Trivy 0.69.3 image postgres:** CVE baru HIGH di binary rilis WAL-G v3.0.9 (stdlib Go 1.25.11 CVE-2026-78667/
  78669/97031, x/net 0.58.0 CVE-2026-78669) → **diperbaiki**: WAL-G dibangun dari source tag v3.0.9 (commit
  3e49318 diverifikasi) dengan Go 1.27.2 + x/net 0.60.0 + grpc 1.83.2, tag `libsodium`. Trivy bersih (hanya FP lama
  CVE-2021-38599 yang sudah disetujui); **10 pengecualian WAL-G G16 dihapus** dari `.trivyignore.yaml`. Uji fungsional:
  `wal-g --version` v3.0.9, `efn-walg backup-list` + `wal-fetch` (dekripsi libsodium) terhadap backup dev OK.
- **Trivy image minio:** x/net 0.59.0 (CVE-2026-78669) di `silo` & `mc` → `go get golang.org/x/net@v0.60.0` sebelum build;
  Trivy bersih; versi biner tetap (RELEASE.2026-09-16, commit sama).
- hadolint, checkov 0 FAILED, actionlint OK. Tidak ada pengecualian baru. Image dev `efn/postgres:dev` & `efn/minio:dev`
  di-tag ulang dari build baru; container dev belum di-recreate.

## G17.9 Re-review G17: SEC-107 (L-19c) + SEC-108 (L-19e) di wrapper
- **Peran:** grup host `efn-cs-approver` terpisah dari `efn-cs`; wrapper menetapkan `EFN_CS_ROLE=cs|approver` dari
  keanggotaan grup (env pemanggil ditimpa); `online-refund-approve` hanya peran approver; akun di kedua grup ditolak
  untuk semua subcommand ("satu orang satu peran").
- **Alasan via stdin:** `--reason` di argv ditolak; wrapper membaca satu baris stdin (prompt bila TTY; CR akhir
  dibuang; tanpa karakter kontrol; 3..800 byte) dan meneruskannya lewat `docker exec -i` stdin tanpa flag `--reason`
  (**kontrak untuk BFF: CLI membaca alasan dari stdin bila `--reason` tidak diberikan**; BFF belum mengimplementasikan
  saat ini — perlu dicek di laporan BFF/integrasi). sudoers: `Cmnd_Alias`, `%efn-cs, %efn-cs-approver`,
  `env_reset, !setenv, !log_input, !log_output`.
- **Loki:** `promtail-journal.yml` mengirim `efn-online-cs` HANYA bila `_UID=0` (field journald tepercaya; user host
  lain tidak bisa memalsukan) → `service="host-efn-online-cs"`. Rule baru grup `efn-online-cs`:
  `EfnOnlineCsDeniedRepeated` (≥ 3 DITOLAK per aktor / 15 m), `EfnOnlineCsDualRole`, `EfnOnlineCsRefundVolumeHigh`
  (> 10/jam, PLACEHOLDER). Approve di luar jam kerja tidak dibuat (LogQL tanpa fungsi jam).
- **Uji:** `test-efn-online-cs.sh` 45/45 (peran, dua grup, approve oleh cs, alasan stdin literal/kosong/pendek/tab/
  > 800 byte/multi-baris, `--reason` argv ditolak, journald tanpa alasan); promtail redaksi 83 entri OK (fixture baris
  wrapper utuh di pipeline docker + journal), `-check-syntax` OK; query LogQL rule baru diparse Loki (status success)
  dan dievaluasi ruler. shellcheck, actionlint, yamllint 0 error, checkov 0 FAILED, compose dev/prod, check-encryption OK.
- Runbook `online-orders.md#cli-cs` (peran, larangan dua grup + cek `comm`, sudoers baru, alasan via stdin, alert),
  `host-journal-loki.md`, indeks `README.md`.

## G17.8 SEC-98 (L-16d): wrapper host `efn-online-cs`
- `scripts/host/efn-online-cs` (root, dipasang manual Superadmin seperti efn-deploy): wajib root lewat sudo,
  `SUDO_USER` valid (bukan root, konsisten dengan `SUDO_UID`), anggota grup `efn-cs`; `EFN_CS_OPERATOR`=SUDO_USER dan
  `EFN_CS_HOST`=hostname SELALU ditimpa; hanya `online-refund|online-refund-approve|online-refund-settle|online-resolve`
  + flag allowlist per subcommand, UUID/enum/pola/panjang divalidasi, tanpa karakter kontrol/flag ganda/argumen posisi;
  argv ke `docker exec <api-gateway project efn> node dist/cli/<cmd>.js` (tanpa shell); journald `efn-online-cs`
  MULAI/SELESAI/DITOLAK dengan aktor, host, cmd, id, rc — tanpa `--reason` dan `--provider-ref`; rc CLI diteruskan.
- `scripts/host/test-efn-online-cs.sh`: 35 kasus (docker/logger palsu, container sekali pakai) **PASS=35 FAIL=0**;
  CI job infra: langkah baru + shellcheck `scripts/host/efn-online-cs`. shellcheck OK.
- Runbook `online-orders.md#cli-cs`: pasang (install + sha256, grup `efn-cs`, sudoers `NOPASSWD` + `env_reset,
  !setenv, log_output`, `visudo -c`), pemakaian 4 subcommand, jejak journald, cabut akses.
- Catatan untuk BFF: pemisahan tugas approve ≠ pembuat refund belum ditegakkan kode (hanya tercatat) — kini prosedural.
- Belum: uji dengan sudo + docker asli di host staging.

## G17.7 Review r10 SEC-96 (L-16b): batas koneksi gateway → Odoo
- `haproxy.cfg`: `maxconn "${ODOO_RPC_MAXCONN}"` (dari 32 → default **4**; env tak diset = HAProxy gagal parse =
  fail-closed), `timeout queue 2s`, antrean frontend dibatasi 16 (`queue(odoo) ge 16` → `503` seketika, `why=busy`),
  `http-error 503` JSON + `X-EFN-Gateway-Error: unavailable` (dibedakan dari `X-EFN-Gateway-Deny`), log + `%Tw`.
- Konfigurasi: `entrypoint.sh` validasi 1..16 (default 4); compose `ODOO_RPC_MAXCONN: ${ODOO_RPC_MAXCONN:-4}`;
  deploy-validate (+ rentang 1..16), deploy.sh `.env`, deploy.yml `CFG_ODOO_RPC_MAXCONN`, `.env.example`, CI `haproxy -c`
  dengan env. Loki `EfnOdooRpcGatewayDenied` mengecualikan `why=busy`.
- Runbook `online-orders.md#sizing-worker`: aturan `ODOO_WORKERS ≥ POS + ODOO_RPC_MAXCONN`. **Temuan:** prod
  `ODOO_WORKERS=6` + maxconn 4 menyisakan 2 worker untuk POS → sebelum online prod: workers 9 (4 CPU) atau maxconn 2
  (keputusan lead/klien; compose prod tidak diubah).
- Verifikasi: `test-gateway.sh` 97/97; uji beban baru `test-gateway-load.sh 300 200` → 150 dilayani Odoo (antre maks.
  90–123 ms), 150 × `503 unavailable`, 0 status lain, gateway tetap healthy; efn-deploy 103/103 (+2 kasus maxconn);
  shellcheck, actionlint, yamllint (0 error), checkov 0 FAILED, compose dev/prod, check-encryption OK.
- Temuan sampingan: Odoo menjawab `HTTP/1.0`; curl paralel lalu mengirim HTTP/1.0 → ditolak gateway (`why=target`).
  BFF (undici) tidak terdampak; uji beban memakai `--http1.1` + `Connection: close`.

## G17.6 Temuan QA G17-QA-05 (watch BFF dev di Windows)
- **Penyebab `unhealthy` sebelum integrasi (dari log Loki):** setelah recreate 14:20 (9 Okt) `nest start --watch`
  mengompilasi `src/` saat paket BFF setengah diedit → `Found 4 errors` (MockGateway belum punya `refund`, TS2420/2741/
  2322) → app tidak start (tidak ada listener :3000) → healthcheck gagal. Perbaikan file di host tidak terdeteksi karena
  Docker Desktop (Windows) tidak meneruskan event inotify dari bind mount, jadi container tetap unhealthy sampai
  di-recreate 15:38.
- **Perbaikan (hanya `docker-compose.dev.yml`):** env resmi TypeScript `TSC_WATCHFILE=DynamicPriorityPolling` +
  `TSC_WATCHDIRECTORY=RecursiveDirectoryUsingDynamicPriorityPolling` di api-gateway dev (watcher `nest --watch` = API
  watch TypeScript → polling). Superapp-web dev belum punya pola polling (tidak ada yang bisa ditiru). Compose prod tidak
  diubah.
- **Verifikasi nyata:** recreate api-gateway → healthy ±55 s; tambah satu baris komentar di `src/main.ts` → `File change
  detected` ±1 s, app start ulang (`successfully started`, ±4 s); file dikembalikan persis (`git diff` bersih) → reload
  lagi; status akhir healthy. compose config dev OK, check-encryption OK.

# Gelombang 18 — W-11 rotasi API key, W-19 env retensi, W-28 kustodi brankas (10–12 Okt 2026, belum di-commit)

Sumber: `docs/10_RENCANA_GELOMBANG_18_PLUS.md` (W-11, W-19, W-28), keputusan 10 Okt (`docs/decisions/README.md` #33,
Q-E1a/b/c, bagian E), R-T15, R-B11; permintaan lead (env data-AI, BFF: migrasi 08, `online-erasure-pending`, alert forced).

## G18.1 W-11 — rotasi otomatis API key kanal online (ADR-024/034 Proposed, R-T15)

| File | Isi |
|---|---|
| `scripts/host/efn-rotate-online-key` (baru) | Skrip root fail-closed (pola efn-deploy/efn-online-cs): identitas root/systemd/sudo `efn-superadmin`; flag allowlist (`--channel --force --status --overlap-seconds`); konfigurasi `/etc/efn/online-key-rotation.conf` tidak di-source (allowlist + validasi, milik root, tidak group/world-writable); flock per kanal. Alur: list key (`odoo shell` exec di container odoo) → rotasi bila sisa < 10 h / state tak dikenal / tanpa kedaluwarsa / `--force` → key baru `with_user(efn_online_<kanal>)._generate(None, 'efn-online-<KANAL>-<UTC>', +30 h)` → tulis atomik ke file secret yang di-mount compose (path dari `compose config`, wajib di SECRETS_DIR; `root:efn-superadmin` + mode deploy.sh) → recreate pemakai secret + sidecar → tunggu sehat → verifikasi json2 `efn_online_catalog` limit 1 lewat odoo-rpc-gateway (mTLS) di **setiap** replika BFF → gagal: rollback + cabut key baru (rc 1; rc 3 bila rollback gagal) / sukses: overlap 1 jam → verifikasi ulang → cabut semua key lain → **bukti 401** key lama → hancurkan salinan. Cabut tertunda diselesaikan run berikutnya. Nilai key tidak pernah di stdout/stderr/journald/argv; stderr Odoo disaring. Metrik textfile `efn_odoo_api_key_*` (multi-kanal satu file). |
| `scripts/host/systemd/efn-rotate-online-key@.{service,timer}` (baru) | Template per kanal, harian 03:15 WIB, `Persistent`, hardening systemd ringan. |
| `scripts/host/test-efn-rotate-online-key.sh` (baru) + CI step | 42 kasus docker/Odoo/BFF palsu: bootstrap, tidak-perlu, `--force`, rollback, unhealthy → rc 3, create gagal, cabut tidak efektif, cabut tertunda/overlap, sisa < 10 h, key tanpa exp, `--status`, penolakan identitas/argumen/konfigurasi/flock, penjaga kebocoran key. |
| `scripts/secrets.list` + `gen-secrets.sh`, `deploy.sh`, `deploy.yml`, `efn-deploy` | Jenis secret baru **`host:<awal>`** untuk `odoo_api_key_online_{rtl,fnb}`: tidak dikirim deploy.yml, ditolak efn-deploy dari stdin (test 110), **tidak ditimpa** deploy.sh (file belum ada → placeholder). Tanpa ini deploy berikutnya menimpa key hasil rotasi dengan nilai GitHub lama (yang sudah dicabut) → outage. `deploy.sh` menulis `/opt/efn/compose/.efn-compose-files` (dipakai skrip host). |
| `alert-rules.yml` | ExpiringSoon 14 → **7 h** (rotasi normal di sisa 10 h); baru `EfnOnlineApiKeyRotationFailed`, `…RotationNotRunning`, `…ExtraKeys`. |
| `loki-rules/efn-online.yml`, `promtail-journal.yml` | Grup `efn-online-key-rotation`: `EfnOnlineApiKeyRollbackFailed` (critical), `…OldKeyStillAccepted`; journald `efn-rotate-online-key` hanya `_UID 0` → `service="host-efn-rotate-online-key"`. |
| `docs/runbooks/odoo-online-api-key.md` (ditulis ulang), `docs/04` baris identitas Odoo | Pemasangan, cara kerja, alert, rotasi gagal, ROLLBACK GAGAL, darurat. |

**Uji NYATA stack dev (10 Okt):** skrip dijalankan di container `docker:27-cli` (socket + repo di `/run/desktop/mnt/host/e/...`): `--status` → rotasi (key id 1 → id 2, 30 h, verifikasi 200 di BFF sebelum & sesudah, key 1 dicabut, **401 terbukti**) → run kedua "TIDAK PERLU" → metrik lengkap; `efn-api-gateway-1` sehat. `secrets/odoo_api_key_online_rtl.txt` dev kini berisi key id 2 (berlaku s.d. 9 Nov 2026).

**Selaras functional:** `api_key_duration` grup teknis 35 h (efn_setup_online) ≥ KEY_DAYS 30. ⚠ **Temuan:** Odoo memakai **maksimum** durasi dari *semua* grup user (`all_group_ids`); di DB dev grup "Role / User" juga ber-`api_key_duration` 90 → batas efektif user teknis tetap 90 h. Perlu dicek efn-odoo-functional (jangan beri durasi di grup umum).

**Deviasi dari brief:** file secret ditulis mode **0444** (bukan 0400) di direktori 0750 `root:efn-superadmin` — sama dengan deploy.sh; 0400 root membuat BFF (uid 1000) tidak bisa membaca bind mount (key-custody "Mekanisme uid"). Salinan kerja di `/var/lib/efn/online-key` = 0400 root.

## G18.2 W-19 — env retensi (+ data-AI, BFF)

compose / `.env.example` / `deploy-validate` (release config + rentang) / `deploy.yml` `CFG_*` / `deploy.sh` `.env`:
`ONLINE_ADMIN_AUDIT_RETENTION_DAYS` 3650 (30..3650), `ERASURE_SUPPRESSION_DAYS` 365 (0..730), `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` 14 (1..14), `MEMBER_MIN_AGE` 17 (17..21), `PRICE_HUNTER_GEO_RETENTION_DAYS` 90, `CH_MART_RETENTION_YEARS` **10** (1..30), `CH_PSEUDONYM_RETENTION_YEARS` 5 (clickhouse-init + etl; ≤ mart), `CH_GEO_RETENTION_DAYS` 90 (clickhouse-init + etl), `MB_AUDIT_MAX_RETENTION_DAYS` 3650 (metabase, 30..3650). Batas bawah `ONLINE_ORDER_MEMBER_LINK_DAYS` 30 / `ONLINE_EVENT_RETENTION_DAYS` 7 diselaraskan dengan BFF. Test efn-deploy +15 kasus (110–124).

- **Deploy G18:** `deploy.sh` kini `stop etl` sebelum `up -d` (profil data) → ETL baru jalan setelah `clickhouse-init` exit 0 (depends_on) — mitigasi jendela migrasi `loyalty_events` (data-AI), otomatis setiap deploy.
- Migrasi `08-platform-g18-privacy.sql` masuk daftar **wajib** deploy.sh.
- Housekeeping Metabase: `scripts/metabase-housekeeping.sh` + `scripts/host/systemd/efn-metabase-housekeeping.{service,timer}` (harian 04:00 WIB, `metabase-provision <argumen compose> --housekeeping-only`, file compose deploy terakhir), metrik + alert `EfnMetabaseHousekeepingStale`, journald unit → Loki.
- Runbook baru `docs/runbooks/retention.md` (tabel env, deploy pertama G18, housekeeping).
- ⚠ Variabel GitHub Environment lama (`ONLINE_ADMIN_AUDIT_RETENTION_DAYS=400`, `CH_MART_RETENTION_YEARS=5` bila diset) **menimpa** default baru — Superadmin hapus sebelum deploy G18. `infra/compose/.env` dev lokal juga masih bernilai lama.

## G18.3 Permintaan BFF

- Wrapper `efn-online-cs`: subcommand **`online-erasure-pending`** (alias `erasure-pending`, nama kanonik mengikuti runbook BFF) → `node dist/cli/online-erasure-pending.js`; read-only, tanpa flag, tanpa alasan, **tanpa stdin** (`docker exec` tanpa `-i`, `< /dev/null`), peran cs & approver; pemisahan peran tetap. Test 54/54. ⚠ sha256 wrapper berubah → Superadmin pasang ulang. Runbook online-orders.md diperbarui.
- Alert `EfnDsarErasureForcedOpenOrders`: `increase(efn_dsar_erasure_deferral_total{event="forced"}[1h]) > 0` (warning, runbook online-orders.md#erasure-paksa).

## G18.4 W-28 kick-off — kustodi brankas Direksi (R-B11)

`docs/runbooks/key-custody.md` §8 (baru) + penyesuaian §1/§2/§5/§6/§7 ke keputusan 1 Superadmin: isi Paket A (arsip `age` terenkripsi passphrase P-A di 2 flashdisk: root CA dir, pii key ring, walg, minio KMS, clickhouse disk, metabase, otp_pepper, audit sink, LUKS passphrase + header backup, kode pemulihan GitHub, kunci SSH break-glass, kanari PII, register SHA-256), Amplop B terpisah (P-A + passphrase root; rekomendasi lokasi kedua), pengemasan & penyegelan bernomor + berita acara, log brankas + pemeriksaan segel kuartalan, pemicu pembaruan paket, **uji pemulihan disaksikan 2×/tahun** (restore nyata dengan kunci dari paket, oleh staf selain Superadmin), prosedur Superadmin tidak tersedia (pemicu 4 jam / 2 minggu / keluar), Shamir sebagai opsi masa depan (tidak diwajibkan). Index `docs/runbooks/README.md` diperbarui.

## G18.5 Validasi (lokal, container)

shellcheck v0.10.0 (semua skrip CI + baru) bersih · `docker compose config` dev & prod OK · `check-encryption.sh` OK · promtool `check rules` SUCCESS · yamllint OK (1 warning lama: baris pytest metabase milik data-AI) · actionlint rc 0 · checkov 0 failed · test-efn-deploy **124/124** · test-efn-online-cs **54/54** · test-efn-rotate-online-key **42/42** · test-promtail-redaction OK (83 entri) · uji nyata rotasi di dev (G18.1).

## G18.6 Belum / risiko

- Belum diuji di host Linux dengan systemd/journald/promtail-journal nyata (staging belum ada, ADR-004); timer housekeeping & `stop etl` di deploy.sh belum dijalankan nyata.
- Rollback rotasi hanya diuji dengan docker palsu; jalur sukses diuji nyata.
- Recreate BFF (2 replika prod) serentak → 503 beberapa detik saat rotasi (03:15 WIB).
- `efn-online-cs`, `efn-deploy` (allowlist `host:`), skrip baru: sha256 berubah → pemasangan ulang manual Superadmin.
- Container `efn-fg18-suite`/`efn-fg18-pg` (image `efn-odoo:ci`, dimulai ±10 menit sebelum laporan) **bukan** milik devsecops dan tampak dipakai agent lain → tidak dihapus. Container uji devsecops semuanya `--rm` (tidak ada sisa).
- W-28 lanjutan (DPIA, off-site backup, PoC SeaweedFS) → G20. Nama Superadmin/pemegang brankas/saksi belum ditetapkan (G-3).

## G18.7 Perbaikan review r11 (SEC-114, SEC-120; L-25b/h) + env BFF r11 (14 Okt 2026, belum di-commit)

- **SEC-114 (L-25b):** default `ETL_LOYALTY_POINTS_MODE` = **`payment`** di compose (etl), `.env.example`, `deploy.yml`
  (`CFG_*`), `deploy.sh`. `deploy-validate` menolak `product` di **production** (staging/dev boleh untuk data historis).
  Test efn-deploy 125–127. ⚠ Default di kode ETL (`services/etl/efn_etl/config.py:51`, `loyalty_recon.py:613`) masih
  `product` — milik data-AI (lead meneruskan). Selama compose selalu mengisi env, kode default tidak dipakai.
- **SEC-120 (L-25h):** kunci host bersama **`/run/efn/deploy.lock`**:
  - `efn-deploy` mengambil kunci (fd 8, tunggu ≤ 900 s, `EFN_DEPLOY_LOCK_WAIT` hanya untuk test; sudo env_reset) sebelum
    sinkron ke `/opt/efn`; fd diwarisi `deploy.sh` lewat exec. `deploy.sh` jalur manual membuka & menunggu kunci yang
    sama (jalur efn-deploy: `flock -n` pada deskripsi warisan).
  - `efn-rotate-online-key` memegang kunci selama precheck → create → pasang → recreate → verifikasi/rollback, dan
    selama pencabutan setelah overlap (juga cabut tertunda); **overlap 1 jam tanpa kunci**. Kunci tidak didapat dalam
    `HOST_LOCK_WAIT_SECONDS` (default 900, 10..3600) → gagal **sebelum** membuat key (metrik 0, alert, coba lagi besok).
  - Key yatim: create gagal/galat setelah commit atau baris `NEW` tidak dikenali → op Odoo `revoke` per **nama berversi**
    `efn-online-<KANAL>-<UTC>` (nama divalidasi regex di sisi Python; key terpasang `CUR_ID` dikecualikan).
  - Test: rotasi +6 (43–48: keluaran rusak, galat setelah commit, kunci dipegang deploy, cabut tertunda menunggu kunci,
    kunci dilepas setelah sukses); efn-deploy 02b (kunci dipegang selama deploy.sh) dan 110b (kunci dipegang rotasi →
    deploy ditolak setelah menunggu).
  - ⚠ sha256 `efn-deploy` dan `efn-rotate-online-key` berubah lagi → pasang ulang manual Superadmin.
- **Env BFF r11:** `LOYALTY_RESTORE_TOLERANCE_PCT` (10; deploy-validate 0..50, BFF menerima 0..100) dan
  `LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS` (3650; 3650..7300) di compose api-gateway, `.env.example`,
  `deploy-validate` (release config + rentang), `deploy.yml`, `deploy.sh`. Test efn-deploy 128–131. Runbook
  `retention.md` + `odoo-online-api-key.md` (koordinasi deploy) diperbarui.
- **Validasi:** shellcheck bersih · compose config dev/prod OK · check-encryption OK · actionlint rc 0 · yamllint OK
  (1 warning lama baris pytest metabase) · checkov 0 failed · test-efn-deploy **133/133** · test-efn-rotate-online-key
  **48/48** · test-efn-online-cs **54/54**.
- **Belum:** perilaku flock lintas efn-deploy → exec deploy.sh hanya diuji dengan stub deploy.sh (bukan deploy.sh asli)
  dan belum di host Linux/systemd nyata.

## G18.8 SEC-129 (r11): modul Odoo wajib dicek saat deploy (14 Okt 2026, belum di-commit)

- `deploy-validate.sh`: `EFN_REQUIRED_ODOO_MODULES=(efn_pos_refund_origin)` + `efn_odoo_modules_missing` (stdin `nama|state`, fail-closed: kosong/absent/state lain = gagal).
- `deploy.sh`: bila DB `efn_odoo` sudah ter-install (`pos_order` ada), sebelum grant ETL: `SELECT name || '|' || state FROM ir_module_module WHERE name IN (...)` lewat jalur DB deploy (user `efn_odoo`, socket container postgres) -> deploy gagal dengan pesan `<modul>=<state>` + runbook. Catatan: `deploy.sh` tidak meng-upgrade modul Odoo (install/upgrade = langkah manual runbook); cek dipasang di blok Odoo yang sudah ada.
- Runbook baru `docs/runbooks/deploy-odoo-modules.md` (+ index README). Test efn-deploy 132-138 -> **140/140**; shellcheck bersih. Query diuji nyata di DB dev (`efn_pos_refund_origin|installed` -> lulus).
- Belum: perintah install di runbook (`odoo ... -u base -i efn_pos_refund_origin --stop-after-init` lewat entrypoint EFN) belum dijalankan di staging.
