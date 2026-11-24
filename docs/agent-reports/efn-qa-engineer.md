# Laporan efn-qa-engineer — Integrasi lintas-service Fase 0

Tanggal: 2026-10-05. Cakupan diperluas oleh koordinator: memperbaiki ketidakcocokan integrasi di file mana pun, mengikuti `docs/04_SERVICE_CONTRACTS.md`, tanpa desain ulang. Folder `docs/reviews/` tidak disentuh.

> **Tidak ada yang dijalankan.** Mesin penulis tidak punya Node/Python/Docker/Git. Semua verifikasi dengan membaca kode kedua sisi. Satu-satunya eksekusi: PowerShell .NET (`HMACSHA256`/`SHA256`) untuk menghitung test vector HKDF/`member_hash` secara independen (HKDF RFC 5869 manual; divalidasi dengan RFC 5869 A.3).

## 1. Perbaikan yang dilakukan

| # | File | Sebelum → Sesudah | Alasan |
|---|---|---|---|
| 1 | `services/etl/efn_etl/hashing.py` | `hex(HMAC-SHA256(HKDF(otp_pepper), member_ref))` → `hex(sha256(pepper_hex + member_ref))`, `pepper_hex = hex(HKDF-SHA256(otp_pepper, salt b"", info "efn:member_hash:v1", 32))`; fungsi baru `derive_member_hash_pepper` | Identik dengan BFF `app-config.ts` (`memberHashPepper`) + `pii.ts` (`memberHash`), docs/04 §1. Sebelumnya `competitor_prices.user_hash` tidak bisa di-join dengan `loyalty_events.member_hash` (= `loyalty_ledger.member_ref_hash` dari BFF) |
| 1b | `services/etl/efn_etl/{jobs,runner,__main__}.py` | `TransformContext.member_hash_key: bytes` → `member_hash_pepper: str` | Mengikuti rumus baru; fail-closed tetap (pepper kosong → job dilewati) |
| 1c | `services/etl/tests/test_jobs.py`, `test_runner.py` | Vector lama (HMAC) → vector baru: `otp_pepper="test-otp-pepper-v1"`, pepper_hex `46df3d2f…061f`, hash `6281234567890` → `8930b421…6653`; + test RFC 5869 A.3 (salt kosong); + vector derivasi lama `test-otp-pepper` → `513f9f82…a344` | Vector dihitung .NET. Pepper 18 karakter karena BFF menolak secret < 16 karakter (`MIN_SECRET_LENGTH`), jadi `"test-otp-pepper"` (15) tidak bisa dipakai di spec BFF |
| 1d | `services/api-gateway/src/config/app-config.spec.ts` | + test vector yang **sama** (pepper_hex, member_hash, kunci OTP `bd7f6eb6…fadbb`) + RFC 5869 A.3 via `hkdfSync` | Kontrak terkunci di kedua sisi |
| 1e | `services/etl/README.md`, `infra/clickhouse/init/01-schema.sql` (komentar), `infra/postgres/init/02-platform-loyalty.sql` (komentar) | Rumus HMAC / `sha256(otp_pepper‖member_ref)` → rumus kontrak | Dokumentasi menyesatkan |
| 1f | `infra/compose/docker-compose.yml` service `etl` | secret `[pg_password, clickhouse_password]` → + `otp_pepper`; env + `OTP_PEPPER_FILE`, `PG_PORT`, `CH_DATABASE`; `PLATFORM_DB` → `PG_DB` (kode membaca `PG_DB` dulu, fallback `PLATFORM_DB`) | Tanpa secret, job `competitor_prices` selalu dilewati (fail closed) |
| 2 | compose `ocr-worker` | hapus `BFF_INTERNAL_URL` | Worker ↔ BFF hanya RabbitMQ (docs/04 §2). Env lain sudah cocok: `PG_DB` dibaca (fallback `PLATFORM_DB`), `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD_FILE` sebagai fallback, `tmpfs /tmp` ada, `METRICS_PORT=9100` = healthcheck = scrape Prometheus |
| 2b | `services/ai-ocr-worker/efn_ocr/config.py` | `MINIO_SECURE` saja → `MINIO_SECURE`, fallback `MINIO_USE_SSL` | Nama env yang dipakai BFF/compose |
| 3 | `services/superapp-web/Dockerfile` | hapus `ARG/ENV NEXT_PUBLIC_API_URL` | Sudah tidak dipakai (`next.config.mjs` sudah bersih, `connect-src 'self'`) |
| 4 | compose label `traefik.http.routers.api.rule` | `Host(api.*)` → `Host(api.*) && !PathPrefix(/metrics) && !PathPrefix(/v1/pos) && !PathPrefix(/internal)` | `/metrics` hanya untuk Prometheus internal (scrape `api-gateway:3000` lewat network `internal`, tidak terpengaruh). `/v1/pos/*` dan `/internal/*` adalah server-to-server (docs/04 §1, ADR-002); Odoo memanggil `http://api-gateway:3000` |
| 6 | `services/ai-ocr-worker/efn_ocr/messages.py`, `processor.py` | `notify.push.params` `{report_id, points}` / `{report_id, reason}` → + `product_name` | Template BFF (`src/notification/templates.ts`) membaca `product_name` + `points`/`reason`; sebelumnya parameter WA {{1}} selalu `-` |
| 6b | `services/ai-ocr-worker/tests/test_messages.py`, `services/api-gateway/src/notification/notify-push.consumer.spec.ts` | + assert params lengkap; + spec BFF memakai payload persis bentuk worker dan memastikan tidak ada placeholder `-` | Kontrak terkunci di kedua sisi |
| 7 | `services/api-gateway/src/market-scout/market-scout.service.ts` (+ spec, README) | INSERT menulis `image_phash` (dHash) → kolom tidak diisi BFF | Satu pemilik: worker (pHash `imagehash`, 16 hex = `char(16)`). Worker membandingkan pHash baru dengan `image_phash` laporan lain; laporan yang belum/tidak difinalisasi masih membawa dHash BFF → jarak Hamming pHash↔dHash tak bermakna (false duplicate / miss). dHash tetap dihitung di image-processor (tidak dipakai) |
| 9 | `infra/postgres/init/01-databases.sql` | `GRANT SELECT ON ALL TABLES` + `ALTER DEFAULT PRIVILEGES … SELECT` → grant level-kolom: `price_hunter_report` (kolom yang dibaca ETL) dan `otp_audit (id, purpose, outcome, created_at)` | `otp_audit.ip`/`subject`, `image_object` tidak lagi terbaca `efn_readonly`; tabel baru tidak otomatis terbaca. `price_hunter_report.user_id` (nomor HP) **terpaksa** tetap di-grant karena ETL harus meng-hash-nya |
| 9b | `02-platform-loyalty.sql` | komentar disesuaikan; REVOKE dipertahankan (idempoten untuk volume lama) | — |
| 10 | `.github/workflows/ci.yml` job `node` | `setup-node` `cache: npm` + `cache-dependency-path: package-lock.json` → dihapus (komentar cara mengaktifkan kembali) | **Belum ada `package-lock.json`** di repo; setup-node gagal "Dependencies lock file is not found" → job `node` merah di push pertama |
| 10b | `services/odoo/ruff.toml` (baru) | — → `extend` root + isort `section-order` dengan section `odoo` dan `odoo-addons` + per-file-ignores `**/__manifest__.py`, `**/__init__.py` | Job `python` menjalankan `ruff check` (rule `I`) pada addon Odoo; tanpa section ini import gaya OCA (`import requests` ⏎ `from odoo …` ⏎ `from odoo.addons …`) gagal I001 |
| 10c | `efn_pos_otp_gate/models/pos_order.py`, `tests/test_otp_gate.py` | `"consume_rejected_%s" % status` → f-string; `"Promo Uji %s" % …` → f-string; `lambda l:` → `lambda line:` | ruff UP031 dan E741 (rule set `UP`, `E` aktif di root) |
| 11 | `docs/03_DEVSECOPS_PIPELINE.md` | §3.2 hanya 6 secret aplikasi → 15 secret (= `scripts/secrets.list`) + vars WhatsApp/`COMPOSE_PROFILES`; catatan rotasi `OTP_PEPPER` & kunci WAL-G; §2 file secret 0644 di dir 0700; §6 odoo-test sudah ada | Sesuai `deploy.yml` & devsecops |
| 11b | `docs/decisions/ADR-001…md`, `ADR-004…md`, `docs/02_TARGET_ARCHITECTURE.md` | pgBackRest → catatan implementasi **WAL-G** + alasan (repo S3 pgBackRest butuh HTTPS, MinIO internal HTTP); diagram L7 `WAL-G -> MinIO`, OCR `Tesseract/PaddleOCR` | Keputusan devsecops belum tercatat di ADR (status ADR tetap *Proposed*, isi keputusan tidak diubah) |
| X1 | `infra/observability/alert-rules.yml`, `docs/runbooks/otp-failure-rate.md` | label `result="ok|error"` → `outcome` sesuai BFF (`verified|invalid|expired|locked`, `success|failure`) | **Bug serius tak terdaftar**: `efn_otp_verify_total{result!="ok"}` cocok dengan SEMUA seri (label `result` tidak ada) → `EfnOtpVerifyFailureRateHigh` menyala permanen begitu ada >20 verifikasi/15 menit; `result="error"` tidak pernah cocok → `EfnOtpSendFailureRateHigh` tidak pernah menyala |

Test yang ditambah: test vector `member_hash` + RFC 5869 (ETL & BFF), kontrak `notify.push` (worker & BFF), assert BFF tidak menulis `image_phash`.

## 2. Verifikasi item koordinator yang ternyata sudah cocok

- **#3 prefix `v1`**: `main.ts` `setGlobalPrefix('v1', exclude health, health/ready, metrics)`. Cocok dengan: healthcheck compose `/health`, blackbox internal `api-gateway:3000/health` & HTTPS `api.*/health`, smoke deploy, frontend `lib/bff.ts` (`/v1/auth/*`, `/v1/me`, `/v1/market-scout/*`), Odoo `/v1/pos/otp/request|verify`, `/v1/pos/discount-token/consume`, header `X-EFN-Service-Key` (BFF membaca lowercase — header HTTP case-insensitive). Payload Odoo (`member_ref` E.164, `order_ref` = uuid, `amount` int, `company_code` EFN/RTL/FNB/FARM, `pos_config_id` int) lolos DTO BFF. Respons: 202 request, 200/`400 {ok:false, reason, attempts_left}` verify, 200/`409 {ok:false, reason}` consume — `ApiError` meneruskan body apa adanya (tidak dibungkus `{error}`), Odoo membaca `reason`/`attempts_left`; 429 → `rate_limited`. Uptime Kuma tidak punya konfigurasi di repo (monitor dibuat via UI).
- **#5**: `next.config.mjs` sudah tidak mereferensikan `NEXT_PUBLIC_API_URL`; sisa ada di Dockerfile (diperbaiki).
- **#6 queue**: argumen identik — BFF `queues.ts` vs worker `messages.py`: utama `{x-queue-type: quorum, x-dead-letter-exchange: "", x-dead-letter-routing-key: <q>.dlq}`, DLQ `{x-queue-type: quorum}`, durable, tanpa argumen lain. `ocr.requested` BFF berisi semua field kontrak (`image_bucket`, `image_object`, `geo`, `submitted_at`, …); worker hanya memakai `report_id`/`user_id`/`schema_version` dan membaca sisanya dari PostgreSQL (by design). `loyalty.earn.requested`: field, `idempotency_key = price_hunter:<id>`, `message_id = idempotency_key`, `points` ≤ `MAX_EARN_POINTS` — diterima `parseLoyaltyEarnMessage`; `company_code` absen → default `EFN` (ADR-020). Template `price_hunter_valid|rejected` ada di `NOTIFY_TEMPLATES`.
- **#8**: kolom/tipe insert ETL = DDL `competitor_prices` (14 kolom) dan `loyalty_events` (7 kolom); `semantic_layer.yaml` hanya mencantumkan 4 view yang memang ada (`v_competitor_prices`, `v_price_hunter_quality`, `v_loyalty_daily`, `v_sales_daily`) dengan kolom yang cocok; grant `text2sql-access.sql` cocok.
- **#9**: `02-platform-loyalty.sql` vs BFF `loyalty.service.ts` (INSERT `member_ref_hash, company_code, delta_points, reason, source, source_ref, idempotency_key, expires_at`, `ON CONFLICT (idempotency_key)`) dan ETL (`id::text`, `member_ref_hash`, `created_at` + index `ix_loyalty_ledger_created`) cocok; `id bigserial` aman karena ETL membandingkan `id::text`.
- **#10**: job `pytest` matrix sudah mencakup `ai-ocr-worker`, `etl`, `text2sql` (path filter, install `requirements*.txt`, tesseract, `pytest tests`, `pytest.ini pythonpath=.`); `odoo-test` memasang `efn_core efn_pos_otp_gate efn_kitting` dengan `--test-tags efn`; semua test Odoo bertag `efn`. Secret compose (15) = `scripts/secrets.list` (15) = `deploy.yml` (15) — tidak ada secret baru ditambahkan.

## 3. Tabel secret: dibaca kode vs di-mount compose (#12)

| Service | Secret yang dibaca kode (env `*_FILE`) | Di-mount compose | Status |
|---|---|---|---|
| odoo | `pg_password` (`PASSWORD_FILE`, entrypoint), `odoo_admin_password` (`ODOO_ADMIN_PASSWORD_FILE`), `odoo_bff_api_key` (`EFN_BFF_API_KEY_FILE`, default `/run/secrets/odoo_bff_api_key`) | ketiganya | OK |
| api-gateway | `jwt_secret`, `otp_pepper`, `discount_token_secret`, `odoo_bff_api_key`, `webhook_payment_secret`, `whatsapp_token` (wajib hanya bila enabled), `pg_password`, `rabbitmq_password`, `minio_root_password` | kesembilannya | OK |
| ocr-worker | `rabbitmq_password`, `pg_password`, `minio_root_password` (fallback dari `MINIO_SECRET_KEY_FILE`) | ketiganya | OK |
| etl | `pg_password`, `clickhouse_password`, `otp_pepper` (`OTP_PEPPER_FILE`), opsional `ODOO_PG_PASSWORD_FILE` | **sebelumnya tanpa `otp_pepper`** → diperbaiki | OK |
| postgres | `pg_password`, `minio_backup_password`, `walg_libsodium_key` | ketiganya | OK |
| minio / minio-init | `minio_root_password` (+ `minio_backup_password` untuk init) | ya | OK |
| rabbitmq / clickhouse / metabase / postgres-exporter | masing-masing satu | ya | OK |
| grafana / alertmanager | `grafana_admin_password` / `alertmanager_webhook_url` | ya | OK |

## 4. Mismatch yang TIDAK saya perbaiki (dan alasannya)

1. **ETL login sebagai owner `efn`, bukan `efn_readonly`** (compose `PG_USER`); grant kolom di 01/02 baru efektif setelah deploy membuat password `efn_readonly` dan compose memakai user tersebut (butuh secret baru `pg_readonly_password` → keputusan devsecops, menambah secret).
2. **`loyalty_events` di ClickHouse tidak memuat `company_code`** (ada di ledger, diperlukan settlement antar-PT ADR-020). Perlu DDL + ETL + view + semantic layer + grant — penambahan fitur, bukan penyelarasan.
3. **Reason teknis (`price_mismatch`, dst.) dikirim mentah ke pelanggan** lewat parameter WA `price_hunter_rejected`. Perlu pemetaan ke kalimat Bahasa Indonesia (BFF `templates.ts` atau worker) — keputusan UX.
4. **Compose odoo `EFN_BFF_URL`** tidak dibaca Odoo (URL dari `ir.config_parameter efn.bff_url`, default `http://api-gateway:3000`). Tidak berbahaya; dibiarkan agar tidak mengubah perilaku entrypoint.
5. **Permissions-Policy ganda**: Traefik `secure-headers` (`camera, geolocation, microphone`) dan Next.js (+`payment=(), usb=()`). Nilai tidak bertentangan; rapikan bersama security reviewer.
6. **`entrypoint-efn.sh` default `ODOO_ADMIN_PASSWORD=change-me`** bila secret tidak ada — wilayah security reviewer.
7. **`superapp-web` tanpa konfigurasi ESLint** (`next lint` + ESLint 9) — keputusan frontend; lihat risiko CI #2.
8. **`/health/ready` & `/v1/auth/*`** tetap publik (memang untuk Next.js via internal, tetapi router `api.` juga mengeksposnya) — sesuai kontrak saat ini.
9. Retry `consume` setelah token kedaluwarsa menjawab `expired` (bukan idempoten) — sudah dicatat BFF; Odoo menandai `review`, tidak hilang.

## 5. Risiko CI-gagal teratas (cek pertama saat push)

1. **`ruff check` / `ruff format --check` pada `services/odoo/addons`** (job `python`): kode Odoo belum pernah diformat ruff; isort section `odoo.addons` mengandalkan pencocokan modul paling spesifik di ruff (`services/odoo/ruff.toml`). Bila gagal: jalankan `ruff format services/odoo/addons` + `ruff check --fix` lalu commit, atau sempitkan job ke direktori non-Odoo.
2. **`next lint` di `superapp-web`** tanpa file konfigurasi ESLint (ESLint 9 + `eslint-config-next`): bisa prompt/gagal non-interaktif. Tambah `eslint.config.mjs` (flat + `@eslint/eslintrc` FlatCompat) atau ubah script lint.
3. **Job `node` tanpa lockfile**: `npm install` menarik versi caret terbaru → kompatibilitas `@fastify/*`/Nest 10/`sharp` (linux) dan typecheck strict (`nest build`). Commit `package-lock.json` yang dibuat di Linux.
4. **`odoo-test`**: install `efn_pos_otp_gate` (asumsi API POS 19.0 & `TestPoSCommon`), demo `efn_kitting` (`base.IDR`), grep `ERROR` di log.
5. **`pytest`**: `sqlglot==26.0.0` (text2sql), ambang rapidfuzz, `pip-audit --strict` pada pin (`cryptography==44.0.1`, `Pillow`, `requests`).
6. **Job `infra`**: tag image yang belum diverifikasi, `yamllint` pada rule Traefik multi-baris (`>-`), `hadolint` warning sebagai failure.

## 6. Dokumen baru
- `docs/testing/UAT_SCENARIOS.md` — Given/When/Then: OTP POS (sukses, salah 3x, kedaluwarsa, rate limit, offline, offline setelah verifikasi, BFF timeout request/verify/consume, token replay, order berbeda, akses publik `/v1/pos`), kitting (MO, combo Kit, larangan Phantom, storable, reorder rule), Price Hunter (valid+join hash, duplikat, cap harian, Ollama mati, 413/415, akses milik orang lain, worker crash), login OTP Super-App, backup/WAL/restore test.
- `docs/testing/TEST_STRATEGY.md` — piramida per service, isi CI, gap (tour POS, Playwright, load test OTP k6, integrasi broker/DB nyata, ClickHouse nyata, `pos_mrp` dari sesi POS, Vitest).

---

# Gelombang 4 — Verifikasi integrasi enkripsi, kredensial, event v2, Odoo, ADR-022 (2026-10-05)

Metode: **membaca kedua sisi** (kode klien ↔ compose/infra ↔ server), tanpa runtime (tidak ada Docker/Node/Python/Git lokal). Acuan: `docs/04` v2 (§1–§7), laporan Gelombang 3/3b semua agent. `docs/reviews/` tidak disentuh.

## G4.1 Perbaikan yang dilakukan

| # | File | Masalah (kedua sisi) | Perbaikan |
|---|---|---|---|
| 1 | `infra/postgres/init/01-databases.sql` (setelah blok `CREATE ROLE efn_readonly`) | `00-roles.sh` berjalan SEBELUM `efn_readonly` ada → `GRANT efn_readonly TO efn_etl` dilewati pada volume baru; 01 tidak mengulanginya. Hanya `deploy.sh` (menjalankan ulang 00-roles) yang memperbaiki → di dev (`docker compose up`) ETL tidak punya grant kolom apa pun (`permission denied` di `jobs.py` SQL_COMPETITOR_PRICES/SQL_LOYALTY_EVENTS) dan bahkan tidak punya CONNECT `efn_platform` | `GRANT efn_readonly TO efn_etl;` (idempoten) |
| 2 | `infra/minio/minio-init.sh` (policy `efn-bff-market-scout`, `efn-ocr-market-scout`) | SDK `minio` (BFF `object-storage.ts`, worker `storage.py`) tanpa `region` memanggil **GetBucketLocation** sebelum request pertama; policy hanya `PutObject/DeleteObject` / `GetObject` pada `reports/*`. minio-py tidak menangani AccessDenied pada panggilan itu → `get_object` worker gagal → semua laporan masuk retry/DLQ | Tambah statement `s3:GetBucketLocation` pada ARN bucket (tanpa akses isi) untuk kedua user. **Lead:** docs/04 §7.3 baris MinIO sebaiknya menyebut izin ini |
| 3 | `services/superapp-web/app/api/auth/otp/request/route.ts` | BFF menjawab `400 invalid_phone` (SEC-06) → Route Handler memetakan semua non-202/429 ke `502 otp_unavailable` → UI menampilkan "Kode belum dapat dikirim" bukan "Nomor HP tidak valid" (`LoginForm.tsx` sudah menangani 400) | 400 dari BFF diteruskan sebagai `400 invalid_phone` / `400 invalid_request` |
| 4 | `services/odoo/addons/efn_setup_mrp/demo/mrp_demo.xml` | Orderpoint demo company FNB memakai `route_id = mrp.route_warehouse0_manufacture` (route gudang company utama) → risiko `_check_company` saat demo di-load | `route_id` dihapus; Odoo memilih route manufacture gudang company (produk punya BoM normal). Komentar diperbarui |
| 5 | `services/odoo/addons/efn_setup_mrp/README.md` | Tabel & batasan masih menyebut `route_id` manufacture dan "jangan install demo bersama efn_setup_company" (basi sejak 3b) | Diselaraskan |
| 6 | `services/odoo/addons/efn_kitting/README.md` | Catatan basi "`efn_setup_mrp` demo masih mereferensikan `efn_kitting.company_fnb_ck`" | Dihapus |
| 7 | `.github/workflows/ci.yml` job `odoo-test` | Hanya `efn_core,efn_pos_otp_gate,efn_kitting` | Install + test juga `efn_setup_company, efn_setup_pos, efn_setup_mrp, efn_setup_audit` (dilewati otomatis bila OCA `auditlog` tidak ter-fetch). `efn_setup_security` di-install di **langkah terpisah tanpa test** di DB yang sama: `auth_password_policy.minlength = 12` akan menggagalkan fixture test Odoo (user `accountman` dsb. berpassword < 12 karakter) bila terpasang sebelum test |
| 8 | `docs/decisions/ADR-022-*.md` | Tabel "Pengecualian terdaftar" kosong; Keputusan 3 menyebut Ollama `127.0.0.1:11434` (compose: app `:11435`, sidecar `:11434`) | Tabel E1–E10 (alasan, kontrol kompensasi, pemilik, tinjau 2027-04-05, status *Diusulkan*) + tabel sisa pekerjaan W1 (rantai PKI root→intermediate→leaf 90 hari), W2 (`wg-https` + ACME DNS-01), W3 (`superapp-web replicas: 1`); port Ollama dikoreksi |
| 9 | `docs/testing/UAT_SCENARIOS.md` | A1 masih "kasir memasukkan nomor" (v1); belum ada skenario enkripsi | A1 diselaraskan (pilih pelanggan); baru: A12 `member_mismatch`, A13 pelanggan tanpa HP Indonesia, D6 `invalid_phone` di Super-App, bagian **F** (F1 plaintext ditolak per hop, F2 sertifikat/CA salah & SAN, F3 `EFN_ALLOW_PLAINTEXT` di produksi, F4 PII at rest, F5 rotasi kunci PII, F6 Odoo hanya WireGuard + 2FA + blok RPC, F7, F8 least privilege PG/Redis/MinIO/RabbitMQ `user_id`) |
| 10 | `docs/testing/TEST_STRATEGY.md` | Isi CI & piramida usang (Vitest sudah ada, ETL tanpa test vector HKDF) | Diperbarui + §5 usulan job `tls-integration` (compose up `core`+`data` dengan PKI dummy, `APP_ENV=production`, smoke positif/negatif TLS & kredensial) + gap G9 |

## G4.2 Diverifikasi cocok (tidak perlu diubah)

**Enkripsi end-to-end (env kode ↔ compose):**
- BFF `app-config.ts`/`secrets.loader.ts` ↔ compose `api-gateway`: `TLS_CERT_FILE/KEY_FILE`, `PG_*` (`verify-full`, `PG_SSLROOTCERT`), `REDIS_URL=rediss://redis:6379` + `REDIS_USERNAME/PASSWORD_FILE/TLS_CA_FILE`, `AMQP_URL=amqps://efn-bff@…` + `RABBITMQ_PASSWORD_FILE`/`AMQP_TLS_CA_FILE`, `MINIO_ENDPOINT/PORT/USE_SSL/CA_FILE/ACCESS_KEY/SECRET_KEY_FILE`, `PII_ENCRYPTION_KEY_FILE`, `PII_ACTIVE_KEY_VERSION`, `ODOO_BFF_API_KEY_FILE`, `HEALTHCHECK_CA_FILE/SERVERNAME` — semua nama & path `/run/secrets/*` cocok dan secret di-mount.
- Worker `config.py` ↔ compose `ocr-worker`; ETL `config.py`/`tls.py` ↔ compose `etl` (`ODOO_PG_*` jatuh ke `PG_*`); Odoo `render_odoo_conf.py`/`entrypoint-efn.sh`/`efn_bff_client.py` ↔ compose `odoo` (`USER/PASSWORD_FILE/PGSSLROOTCERT/EFN_CA_FILE/EFN_BFF_URL=https://api-gateway:3000/ODOO_HTTP_INTERFACE=127.0.0.1/APP_ENV`); frontend `bff-config.ts` ↔ compose `superapp-web` (`BFF_INTERNAL_URL`, `NODE_EXTRA_CA_CERTS`, `HOSTNAME=127.0.0.1`, `PORT=3001`).
- `APP_ENV`/`NODE_ENV`: base `production`, dev override `dev`/`development` untuk odoo, api-gateway, superapp-web, etl, ocr-worker; `EFN_ALLOW_PLAINTEXT` tidak muncul di compose mana pun.
- SAN `gen-pki.sh` = nama service + `localhost` + `efn-<svc>-1` + `127.0.0.1` untuk 24 service → mencakup semua hostname klien (api-gateway, postgres, redis, rabbitmq, minio, clickhouse, odoo, superapp-web, ollama, metabase, grafana, loki, uptime-kuma, alertmanager, prometheus, traefik, docker-socket-proxy, exporter, cadvisor, ocr-worker) serta healthcheck `localhost` (redis, minio).
- Sidecar: odoo-tls `:8443→127.0.0.1:8069`, odoo-ws-tls `:8444→:8072` (Odoo `gevent_port` terikat `http_interface`), superapp-web-tls `:3443→:3001`, metabase-tls `:3443→:3000` (Jetty 127.0.0.1), ollama-tls `:11434→:11435` (`OLLAMA_HOST=127.0.0.1:11435`), cadvisor-tls `:8443→:8080`, docker-socket-proxy-tls `:2376→:2375` (mTLS CN traefik/promtail/cadvisor). Traefik router port = port sidecar + `scheme=https` + `serversTransport` (`serverName` sesuai SAN).
- Prometheus: 17 job + alertmanager semua `scheme: https` + `ca_file`; `server_name` untuk dns_sd (`api-gateway`, `ocr-worker`) dan probe blackbox; network Prometheus (`internal`,`data`) menjangkau semua target (redis-exporter lewat `data`).
- `check-encryption.sh` (mental run dev & prod): semua URL http di environment/command/entrypoint = loopback (`--status=http://127.0.0.1:606x`); DSN PG (`DATA_SOURCE_URI`, `MB_DB_CONNECTION_URI`) memuat `verify-full`; `rediss://`/`amqps://` tidak cocok regex plaintext; setiap service Traefik punya `https` + transport; file infra hanya punya http loopback (healthcheck) dan satu baris komentar; jumlah `scheme: https` = job + 1. **Diperkirakan lulus.**

**Kredensial:**
- Redis ACL `~otp:* ~dtok:* ~rl:* ~ms:*` ↔ semua key BFF: `otp:<challenge>`, `dtok:used:<jti>`, rate limiter selalu `rl:<key>:<bucket>` (termasuk `rl:otp:login:*`, `rl:otp:pos:*`, `rl:otp:budget:*`, `rl:ms:daily:*`); `+info` setelah `-@dangerous` untuk ready-check ioredis; EVAL (Lua incr window) bukan `@dangerous`.
- PostgreSQL: setiap query `efn_ocr` (`repository.py` CLAIM/STATUS/FIND_DUPLICATE/COUNT_VALID_TODAY/FINALIZE/RELEASE, termasuk kolom WHERE & RETURNING) dan `efn_etl` (`jobs.py` dua job platform) ter-grant di level kolom; tidak ada `*_enc` untuk role selain `efn_bff`. pg_hba cocok dengan pasangan DB/role tiap klien (termasuk Odoo ke DB `postgres` untuk tunggu-DB).
- RabbitMQ definitions: regex configure/read/write untuk `efn-bff`/`efn-ocr` cukup untuk deklarasi quorum+DLX (argumen identik `queues.ts` ↔ `messages.py`) dan publish via `amq.default`; escaping heredoc → regex `^amq\.default$` benar.
- ClickHouse: `etl_writer` SELECT+INSERT 3 mart + `etl_watermark` ↔ `adapters.py` (insert + `argMax` watermark).

**Event v2:** worker `messages.py` ↔ BFF `loyalty-earn.message.ts` / `notify-push.consumer.ts`: `schema_version: 2`, `idempotency_key = price_hunter:<id>` (cocok aturan BFF), `member_hash` hex, `source/source_ref`, tanpa `user_id/member_ref` di payload; properti AMQP `user_id = efn-ocr` (allowlist BFF); template `price_hunter_valid` (params `product_name, points`) / `price_hunter_rejected` (`product_name, reason`) ada di `templates.ts`. Urutan: worker publish **sebelum COMMIT** (`repository.finalize`) → BFF `waitUntilFinal` menunggu 200+500+1000 ms bila `queued/processing`, lalu error transien (requeue sekali → DLQ). `notify.push` tidak menunggu status (hanya dekripsi `user_enc`) — benar karena `user_enc` sudah ada sejak INSERT BFF. `ocr.requested` BFF v2 (`user_hash`) ↔ `parse_ocr_request`.

**Odoo ↔ BFF:** consume membawa `member_ref` partner final (`pos_order.py`), BFF `parseDiscountTokenConsume` mewajibkannya, `member_mismatch` dicek sebelum marker `used` (token tidak terbakar), Odoo membaca `reason` (atau `error.code`) → `review`. `400 invalid_phone` di request OTP dipetakan Odoo (`pos_config.py`); di consume jatuh ke `consume_rejected_400` → review (dapat diterima; nomor sudah divalidasi regex yang sama di Odoo).

## G4.3 Mismatch / celah yang TIDAK saya perbaiki (dan alasannya)

| # | Temuan | Kenapa tidak diperbaiki | Usulan pemilik |
|---|---|---|---|
| U1 | **ETL sumber Odoo** (`SALES_FACT`, `ETL_ENABLE_ODOO=true`): pg_hba mengizinkan `efn_etl` ke `efn_odoo`, tetapi tidak ada GRANT apa pun pada tabel Odoo (DB & tabel dibuat Odoo setelah init, pemilik `efn_odoo`) → job akan `permission denied` | Butuh skrip grant pasca-install Odoo (desain: siapa menjalankan, kolom mana — PII `res_partner` tidak boleh) | devsecops + data-AI (skrip idempoten di `deploy.sh`, grant kolom pada `pos_order(_line)`, `pos_session`, `res_company`, `product_*`) |
| U2 | **Rotasi kunci PII**: BFF `piiKeyRing` selalu mewajibkan kunci v1 (`PII_ENCRYPTION_KEY_FILE`); runbook `pii-key-rotation.md` langkah 6 (pensiunkan v1) tidak bisa dijalankan; job re-encrypt (langkah 4) belum ada | Perubahan kode/desain BFF | efn-bff-engineer (key ring tanpa v1 wajib bila versi aktif ≠ 1 + job re-encrypt) |
| U3 | **ADR-022 Keputusan 2: mTLS wajib** untuk hop tanpa kredensial aplikasi (exporter metrik, sidecar) — implementasi: server-auth TLS saja (`web-config.yml` tanpa `client_ca_file`, Prometheus tanpa `cert_file`, ghostunnel `--disable-authentication`) | Bukan penyelarasan kecil (semua exporter + Prometheus + sidecar); terkait Q-E4 | Lead/architect: putuskan Q-E4, lalu daftarkan sebagai pengecualian ADR-022 atau devsecops implementasi |
| U4 | docs/04 §7.3 MinIO hanya menyebut Put/Delete (bff) & Get (ocr); implementasi kini + `GetBucketLocation` (perbaikan #2) | Mengubah kontrak = wewenang lead | Lead: tambahkan ke §7.3 |
| U5 | Demo data Odoo tidak dimuat di `odoo-test` (Odoo 19 tidak memuat demo secara default; flag `--with-demo` belum terverifikasi di 19.0) → demo `efn_kitting`/`efn_setup_*` (eval `obj().search`, orderpoint, `pos_demo.xml`) tetap belum pernah dieksekusi | Menambah flag yang tidak ada = CI merah | odoo-engineer: verifikasi opsi demo 19.0, lalu tambah langkah install demo terpisah |
| U6 | `efn_setup_company` masih menulis `efn_business_type` (kini compute stored readonly) | Nilai konsisten, diperkirakan diterima ORM; pemilik functional | efn-odoo-functional: hapus baris agar tidak bergantung perilaku ORM |
| U7 | `efn-ocr` RabbitMQ: read/configure lebih luas dari §7.3 (dibutuhkan declare non-pasif) | Sudah dicatat devsecops; pengetatan butuh passive declare di worker | data-AI |
| U8 | Prometheus `dns_sd` `api-gateway` (container di 4 network) bisa mengembalikan beberapa IP per replika → target ganda | Minor, observability saja | devsecops: `relabel` atau network khusus scrape |

## G4.4 Lima risiko CI-gagal teratas (cek pertama saat push)

1. **`odoo-test` kini meng-install `efn_setup_company/pos/mrp/audit` untuk pertama kali** (l10n_id `try_loading` ×4 company, `pos_self_order`, `product_expiry`, OCA `auditlog` 19.0, `ir.default` `manual_discount=False`, imply grup lot/multi-lokasi ke `base.group_user`). Data XML yang salah = `ERROR` di log = job merah; perubahan default POS/grup juga bisa mengubah perilaku `TestPoSCommon` di test `efn_pos_otp_gate`. Mitigasi cepat: keluarkan modul bermasalah dari `EFN_TEST_MODULES`, laporkan ke pemilik.
2. **`scripts/fetch-oca.sh` di `odoo-test`**: bergantung jaringan & branch 19.0 OCA (fallback 18.0 untuk modul yang belum di-port, mis. `mis_builder` — hanya di addons path, tidak di-install); modul hilang = exit 1 sebelum test.
3. **Job `infra`**: `check-encryption.sh` (regex bash pada JSON ter-render, `jq` label Traefik), `docker compose config` untuk sintaks baru (secret `target:`, `cgroup: host`, `network_mode: service:`), `promtool check config/web-config`, shellcheck skrip POSIX (`minio-init.sh` berubah — hanya komentar & JSON heredoc), yamllint `ci.yml` (blok `>-` baru, baris < 180).
4. **Job `node`**: tanpa lockfile; `superapp-web` ESLint 9 `next/typescript` + `vitest`; `api-gateway` typecheck strict kode TLS baru (`FastifyAdapter` `https`, `minio` `transportAgent`, amqplib socket options).
5. **`pytest` ai-ocr-worker/etl**: kode TLS/v2 belum pernah dieksekusi (pin `urllib3==2.5.0` vs `minio 7.2.12`, kwargs `clickhouse-connect` `verify/ca_cert`, `test_repository_sql.py` mengunci daftar kolom); `pip-audit --strict` pada pin.

Risiko runtime (bukan CI, baru terlihat di `tls-integration`/staging): ClickHouse handler `prometheus` di 8443, `BIND_CONFIG` docker-socket-proxy, definitions RabbitMQ `password_hash` openssl, Grafana `$__file{}`, healthcheck busybox `wget` https ClickHouse, perilaku Odoo `--dev=all` dengan `workers=2` di dev.

---

# Gelombang 6 — Verifikasi integrasi kontrak v3 (§8.1–§8.11) lintas service (2026-10-06)

> **Tidak ada yang dijalankan** (tanpa Node/Python/Docker/Git). Verifikasi dengan membaca kode kedua sisi; vektor HKDF/SHA-256 dihitung ulang dengan PowerShell .NET. Acuan: `docs/04` v3 §8.1–§8.11. Cakupan diperluas (lead): perbaikan penyelarasan di file mana pun, tanpa desain ulang. Termasuk 4 butir tambahan dari lead (temuan security r3).

## G6.1 Perbaikan yang dilakukan

| # | Mismatch (sisi A ↔ sisi B) | Perbaikan |
|---|---|---|
| 1 | Redis ACL `efn-bff` (`infra/redis/redis-entrypoint.sh`) tanpa `altcha:used:*` ↔ BFF `auth/altcha.service.ts:77` `SET altcha:used:<sig> NX` → NOPERM, login OTP selalu gagal | ACL + `~altcha:used:*`. Key BFF lain diverifikasi: `otp:<id>`, `dtok:used:<jti>`, semua rate limit lewat `RateLimiter` → `rl:<key>:<bucket>` (termasuk `rl:altcha:ip:*`, `rl:dsar:export:*`, `rl:ms:daily:*`), `rl:paychk:<id>`; `ms:*` dipertahankan (kompatibilitas). Nama key memuat HMAC, bukan IP/HP |
| 2 | Prometheus `api-gateway` scrape `:3000/metrics` ↔ BFF kini hanya `:9464` mTLS (`common/metrics/metrics-server.ts`), `:3000/metrics` 404 | `infra/observability/prometheus.yml`: port dns_sd + relabel `:9464`, sertifikat klien `prometheus` (sudah ada). Compose: env `METRICS_PORT=9464`, `METRICS_TLS_CLIENT_ALLOWED_CN=prometheus`, `METRICS_CLIENT_CA_FILE`; komentar label Traefik. Port tidak perlu dipublish (Prometheus & BFF di network `internal`). Probe blackbox `:3000/health` tetap benar |
| 3 | **Consent versi**: frontend `lib/consent.ts` `location@2026-10-06` ↔ BFF `privacy/dto/privacy.dto.ts` & `AppConfig.consentActiveText` regex `^[A-Za-z0-9._-]{1,32}$` (tanpa `@`) → setiap grant `400` dan env versi frontend membuat BFF gagal start | Regex BFF menerima `@` (`^[A-Za-z0-9._@-]{1,32}$`) di DTO + AppConfig; test BFF baru (`privacy.spec.ts`) |
| 4 | Env consent tidak ada di compose/deploy → grant selalu `503 consent_unavailable` | Base compose `CONSENT_*` dari env; **dev**: versi persis + SHA-256 UTF-8 `body` teks DRAFT (`location` `938707dc…96e5`, `marketing` `5a04537c…76ec`, dihitung PowerShell); `deploy.yml` vars + `deploy.sh` (validasi format & berpasangan, tulis `.env`); `.env.example`. Test frontend baru `tests/consent.test.ts` mengunci versi + hash (drift = CI merah) |
| 5 | **SEC-32**: `app/api/me/data/route.ts` tidak mengirim `X-Step-Up-Token` ↔ BFF `GET /v1/me/data` wajib step-up (§8.9) → selalu 403 → UI 502 | Route meneruskan cookie `efn_stepup` → header; tanpa/invalid → `403 step_up_required` (cookie dibersihkan). `DataSaya.tsx`: 403 → kirim OTP step-up, form kode di bagian "Unduh data", lalu unduh otomatis |
| 6 | `409 account_deleting` (§8.11) tidak ditangani: route verify memetakan ke `502 otp_unavailable` | `app/api/auth/otp/verify/route.ts` meneruskan `409 account_deleting`; `LoginForm.tsx` pesan khusus |
| 7 | `503 consent_unavailable` dipetakan `502 upstream_error` | `app/api/me/consents/route.ts` meneruskan `503 consent_unavailable` |
| 8 | **deploy.sh** menolak `PAYMENT_PROVIDER=disabled` ↔ BFF prod default `disabled` (§8.11) → production tidak bisa di-deploy sebelum vendor dipilih | `deploy.sh` menerima `mock|disabled|xendit|midtrans` (prod tetap tolak `mock`); `docker-compose.payment.yml` (kunci API) hanya untuk `xendit|midtrans`; cek placeholder kunci hanya untuk provider nyata; `payment.yml` meneruskan `PAYMENT_PROVIDER_EXPERIMENTAL` (default false) |
| 9 | **Staging**: `docker-compose.prod.yml` meng-hardcode `APP_ENV: production` (5 service) + `NODE_ENV=production` → BFF staging menolak `mock` (gagal start) | prod override `APP_ENV: ${APP_ENV:-production}` (deploy.sh hanya mengizinkan production/staging). BFF: getter baru **`isProductionDeployment`** (APP_ENV bila di-set, selain itu `isProduction` fail-safe) dipakai **hanya** untuk pilihan provider payment & endpoint `simulate`. Aturan production tidak dilonggarkan: `APP_ENV=production` (atau APP_ENV kosong + NODE_ENV production) tetap menolak `mock` dan `simulate` 404; plaintext dll. tetap `isProduction`. Dicatat di kode + test `payments.spec.ts` |
| 10 | **SEC-25**: strict permissions default `false`, dan compose **tidak meneruskan** `RABBITMQ_STRICT_PERMISSIONS` ke container rabbitmq (nilai `.env` deploy tidak pernah sampai) | Compose rabbitmq env `RABBITMQ_STRICT_PERMISSIONS: ${…:-true}`; entrypoint default strict (`!= false`), `deploy.sh`/`deploy.yml` default `true`. Fallback `false` tetap. Dasar: BFF `checkQueueWithDlq` (passive), worker `queue_declare(passive=True)`, ETL tanpa declare; handler passive `queue.declare` RabbitMQ tidak memeriksa izin configure — **PLAUSIBLE**, verifikasi staging (UAT F8) |
| 11 | `deploy.yml`/`deploy.sh` tanpa `DSAR_DELETE_GRACE_DAYS`; compose BFF tanpa `PII_RETIRED_VERSIONS`, retensi | Ditambahkan (`PII_ACTIVE_KEY_VERSION`/`PII_RETIRED_VERSIONS` dari env; `PRICE_HUNTER_USER_ENC_RETENTION_DAYS` = `MARKET_SCOUT_PHOTO_RETENTION_DAYS`) |
| 12 | Docstring ETL `amqp.py` menyebut queue dideklarasikan BFF | Diperbaiki (broker definitions) |
| 13 | Dokumen | UAT bagian **G1–G9** (ALTCHA, consent, DSAR export, hapus akun lintas Platform/Odoo/ClickHouse, payment mock sukses/expired/cancel/late-paid/nominal beda/replay/offline/disabled, SEC-07, rotasi kunci + CLI, mTLS ditolak, WireGuard 10.88/10.89), F5/F8 diperbarui; `TEST_STRATEGY.md` G10–G12 + §6 job `contract-v3`; ADR-022 E1 diperbarui, **E11–E13** (E13 disetujui lead), **W1/W2 selesai (kode)**; ADR-023 alokasi final; `docs/03` daftar secret (38 item = `secrets.list` non-pki/derived) + `EFN_PKI_*` + vars + deploy via `sudo`/root + grup `efn-superadmin`; `secrets.list` komentar payment |

### Diverifikasi cocok (tanpa perubahan)
- **HKDF**: `test-otp-pepper-v1` → `46df3d2f…061f` (derive-secrets.sh = app-config.spec.ts = efn_privacy test), `6281234567890` → `8930b421…6653` — dihitung ulang independen. Compose Odoo me-mount `member_hash_pepper` + `EFN_MEMBER_HASH_PEPPER_FILE`. CI `odoo-test` memasang `efn_pos_payment_gateway efn_privacy` (+`privacy_lookup` via depends) dan `efn_setup_*`.
- **RabbitMQ definitions**: 4 queue + DLQ, argumen identik `queues.ts`/kontrak §4; izin strict: `efn-bff` read `loyalty.earn.requested|notify.push|privacy.erasure.completed`, write `amq.default`; `efn-ocr` read `ocr.requested`; `efn-etl` write `amq.default`, configure/read `^$`.
- **ALTCHA** challenge `{algorithm, challenge, maxnumber, salt, signature}` ↔ `sanitizeAltchaChallenge`; field `altcha` ↔ DTO; batas panjang kompatibel.
- **DSAR** reauth/delete/cancel/requests (§8.9) ↔ route frontend; header `x-step-up-token` ↔ `STEP_UP_HEADER`.
- **Odoo ↔ BFF**: `/v1/pos/payments` (create/get/cancel), error 409/502/503 → "Gagal membuat QRIS. Gunakan metode lain."; erasure `{items}` + complete `200/404/409`; consume `amount_total`.
- **ETL ↔ BFF**: `idempotency_key` `erasure:<id>:clickhouse`, `user_id=efn-etl`, grant kolom `dsar_request`; tombstone `sha256("erased:"||id)` = 64 hex lowercase lolos `pseudonym()` dan tidak sama dengan hash subjek.
- **Traefik** `POST` + `PathPrefix(/v1/webhooks/payments/)` ↔ `@Controller('webhooks/payments')` + prefix `v1`.
- BFF compose tanpa `NODE_EXTRA_CA_CERTS` (frontend tetap).

## G6.2 Test yang ditambah
- `api-gateway/src/privacy/privacy.spec.ts`: versi `<purpose>@<tanggal>` diterima DTO + AppConfig, versi berspasi 400.
- `api-gateway/src/payments/payments.spec.ts`: staging (`NODE_ENV=production`, `APP_ENV=staging`) mengizinkan `mock` & `simulate`; production/APP_ENV kosong tetap menolak.
- `superapp-web/tests/consent.test.ts`: versi + SHA-256 body = env compose dev, versi lolos regex BFF.

## G6.3 Mismatch / risiko yang TIDAK diperbaiki (perlu keputusan/desain)
1. **Agregat poin ClickHouse setelah erasure** (BFF `erasure.service.ts` ↔ ETL `jobs.py` ledger cursor `created_at`): baris ledger subjek dihapus dari `loyalty_events` (hash asli), baris yang di-tombstone tidak dimuat ulang (cursor `created_at`), tetapi entri `dsar_forfeit` (negatif, `created_at` baru) dimuat → `v_loyalty_daily` per PT menjadi under-count. Opsi (data-AI): cursor `GREATEST(created_at, erased_at)` (grant `erased_at` ke `efn_readonly` sudah ada) atau kecualikan `source='dsar'` dari agregat.
2. **Pembayaran terlambat** (`expired|cancelled|failed → paid`): BFF hanya log WARNING; Odoo tidak me-refresh intent final dan cron yatim hanya melihat `paid` → tidak ada alert/aktivitas akuntan. Usul: metrik `efn_payment_late_paid_total` + alert, atau cron Odoo me-refresh intent final ≤ 24 jam.
3. `efn_pos_payment_gateway`: tidak ada test JS (tour) — G11.
4. ALTCHA widget `altcha@^1` vs format salt `?expires=…&` BFF manual — belum diuji di browser (UAT G1).
5. Passive declare tanpa `configure` = PLAUSIBLE (dibaca dari pengetahuan source RabbitMQ, tidak diverifikasi runtime); fallback `RABBITMQ_STRICT_PERMISSIONS=false`.
6. `simulate` & mock di staging bergantung `APP_ENV=staging` yang ditulis deploy.sh; bila operator menjalankan prod override manual tanpa `.env`, default `production` (aman).
7. Export DSAR besar (`202` + `download_url`) belum ada di BFF; frontend siap (§8.11).
8. Adapter xendit/midtrans: compose tidak menyetel `PAYMENT_PROVIDER_EXPERIMENTAL` kecuali via `.env` host — sengaja.

## G6.4 Lima risiko CI teratas (cek pertama saat push)
1. **`node` api-gateway**: typecheck/lint kode G5 besar yang belum pernah dikompilasi (payments, privacy, metrics-server, CLI re-encrypt) + perubahan G6 (`isProductionDeployment`, regex, spec baru); Jest `payments.spec.ts` staging case bergantung `setup()` membuat intent tanpa error saat `NODE_ENV=production`.
2. **`node` superapp-web**: `next build` + ESLint pada `DataSaya.tsx` (fungsi dipanggil sebelum deklarasi, hooks setelah fungsi), `import { createHash } from 'node:crypto'` di Vitest, paket `altcha@^1` (resolusi versi npm tanpa lockfile).
3. **`infra`**: `docker compose config` dev/prod (env baru `CONSENT_*`, `APP_ENV: ${APP_ENV:-production}` di prod override, nilai dev `location@2026-10-06`), shellcheck `deploy.sh` (loop indirect `${!ver}`), promtool `prometheus.yml` (relabel `:9464`), yamllint baris panjang (warning).
4. **`odoo-test`**: dua modul baru (`efn_pos_payment_gateway`, `efn_privacy`) belum pernah di-install; banyak API 19.0 berasumsi (laporan odoo-engineer G5 #1–#10); langkah "Periksa log" gagal pada ERROR apa pun.
5. **`pytest` etl/ai-ocr-worker**: `pika==1.3.2` + `pip-audit --strict`, test erasure/passive declare/mTLS Ollama belum pernah dijalankan.

Risiko runtime (staging): RabbitMQ strict default (butir G6.3-5), ALTCHA widget, Traefik `wg-https` + ACME DNS-01, intermediate PKI nameConstraints di klien Node/Go/Erlang, Redis ACL `~altcha:used:*` (pola glob dengan `:`), consent hash bila teks frontend berubah.
# Gelombang 8 — Integrasi runtime: smoke E2E stack dev dengan TLS/mTLS (2026-10-06)

Peran gabungan QA + DevSecOps (integration engineer). **Pertama kali alur bisnis diuji di runtime nyata**, bukan
dengan membaca kode. Laporan lengkap + bukti: `docs/testing/E2E_SMOKE_REPORT.md`. Skrip: `scripts/smoke/`.

## G8.1 Hasil skenario (run 2 orkestrator: 72/72 cek lolos)
| # | Skenario | Status |
|---|---|---|
| a | Login OTP Super-App (ALTCHA PoW → OTP → JWT → `/v1/me`; juga via route handler Next.js + cookie) + negatif | Lolos |
| b | Consent + Price Hunter (foto sintetis → worker Tesseract → `valid` → poin ledger → notifikasi; duplikat; VLM tanpa model → `flagged/vlm_unavailable`; DB tanpa HP mentah; MinIO SSE-S3) | Lolos (VLM nyata: tidak bisa, R1) |
| c | POS OTP gate dari Odoo (request/verify ke BFF HTTPS, sync → `consumed`, resync idempoten, `member_mismatch`, `amount_mismatch`, tanpa OTP ditolak) | Lolos |
| d | QRIS POS mock (create → simulate paid → status → sync; tx palsu, nominal beda, intent dipakai ulang ditolak; webhook signature salah 401) | Lolos |
| e | DSAR hapus akun lintas Platform → Odoo → ClickHouse → `completed` (hash Odoo = hash BFF) | Lolos |
| f | ETL Platform → ClickHouse; Metabase sehat | Lolos (ETL Odoo tidak aktif, R5) |
| g | Negatif enkripsi (PG plaintext, mTLS Odoo/Ollama, MinIO HTTP, AMQP 5672, `pg_stat_ssl`, koneksi RabbitMQ) | Lolos |

## G8.2 Perbaikan
1. `infra/compose/docker-compose.yml` (service `ollama`): tambah `hostname: ollama`. Sebelumnya Ollama (bind loopback,
   di belakang sidecar ghostunnel) menjawab **403** untuk `Host: ollama:11434` (proteksi DNS rebinding Ollama;
   hostname container = id) → semua panggilan VLM gagal diam-diam menjadi `flagged/vlm_unavailable`. Setelah fix: 200.
2. Bukan bug produk: skrip smoke baru (`scripts/smoke/{run-e2e-smoke.sh,bff_smoke.py,odoo_pos_smoke.py,README.md}`),
   runbook `dev-stack-boot.md` (instal modul efn_*, profil `ai` butuh `--profile core`, smoke, jebakan Ollama/Git Bash).

## G8.3 Sisa (detail & bukti di E2E_SMOKE_REPORT §5)
- R1 Ollama 0.5.4 tidak bisa menarik `qwen2.5vl` (412 "requires a newer version") → devsecops/data-ai/architect.
- R2 HP di query string `GET /v1/pos/loyalty/balance?member_ref=` tercatat utuh di access log Traefik → architect/BFF.
- R3 Odoo memanggil `vies.api.odoo.com` (tanpa egress) → traceback di log → odoo-functional.
- R4 README.md modul dirender sebagai RST saat install → 27 baris `(ERROR/3)` → odoo-engineer.
- R5 ETL `fact_sales_line` (Odoo) tidak aktif di dev → data-ai/devsecops.
- R6 Setelah DSAR `completed`, `GET /v1/me/requests` → 401 (status akhir tidak terlihat pengguna) → frontend/architect.
- R7 Smoke E2E belum di CI → devsecops.
- Risiko G6.3#1 (under-count agregat poin ClickHouse setelah erasure) **tidak muncul** di runtime: total per
  `source` PG = ClickHouse (`price_hunter +30`, `dsar_forfeit -10`).

## G8.4 Test
Tidak ada kode service yang diubah → test unit tidak diulang. `ruff` 0.16.10 (check + format) dan `shellcheck`
bersih untuk skrip baru; `docker compose config -q` dev & prod lolos. Yang belum diverifikasi di runtime: frontend
POS OWL (skrip memanggil metode server yang sama), jalur VLM dengan model nyata, profil `observability`/`ops` di
gelombang ini (lihat gelombang 7), webhook payment provider nyata, export DSAR besar.

# Gelombang 10 — Regresi runtime setelah gelombang 9–10 (2026-10-06)

Integration QA di stack dev nyata (core + data + ai + observability + ops). Detail & bukti:
`docs/testing/E2E_SMOKE_REPORT.md` bagian "Gelombang 10".

## G10.1 Hasil
- **Run 2 orkestrator (a b c d f e g): 106/106 cek lolos, exit 0** (a 21, b 17, c 15, d 7, f 5, e 24, g 17).
  Run 1: 104/106; dua kegagalan berasal dari bug di skrip baru saya (diperbaiki), bukan bug produk.
- **Skenario VLM `v`: 13/13** dengan `qwen2.5vl:3b` (`vlm=ok`, ±70 s per foto di CPU) setelah timeout dev 240 s.
- Persiapan: build ulang (hanya api-gateway & superapp-web berubah), `04-platform-v3-6.sql` idempoten exit 0,
  `-u` 9 modul efn_* exit 0 (0 ERROR), DB sudah tanpa `auditlog`. Tidak ada volume yang dihapus.

## G10.2 Skenario baru (smoke)
| Permintaan | Cek | Status |
|---|---|---|
| (a) `efn.audit.log` harga produk & HP partner ter-mask | `c.audit_price_logged`, `c.audit_phone_masked`, `c.audit_partner_e_logged` | Lolos |
| (b) setelah DSAR: log audit partner terpurge, `member_hash` NULL, tombstone acak ≠ turunan request_id | `e.odoo_audit_logs_purged`, `e.odoo_pending_markers_cleared`, `e.dsar_member_hash_nulled`, `e.tombstone_random_unlinkable` | Lolos |
| (c) receipt DSAR BFF & Next.js: 200 tanpa PII, salah/hilang 404 | `dsar.receipt_*` (4), `web.dsar_receipt_*` (4, termasuk lintas situs 403) | Lolos |
| (d) `POST /v1/pos/loyalty/balance` dari Odoo 200, GET lama 404 | `c.pos_balance_post_200` (saldo = Super-App), `c.pos_balance_old_get_404`, `c.pos_balance_invalid_400` | Lolos |
| (e) upload Market Scout lewat s3-gateway, access key lama ditolak | `b.upload_via_s3_gateway`, `b.old_access_keys_rejected`, `b.random_access_keys_authenticate` | Lolos |
| VLM nyata | `report_vlm.model_used` (skenario opsional `v`) | Lolos (dengan timeout dev 240 s) |

## G10.3 Perbaikan
1. `infra/compose/docker-compose.dev.yml` (ocr-worker): `OLLAMA_TIMEOUT_SECONDS: ${OLLAMA_TIMEOUT_SECONDS:-240}`.
   Bukti: worker `VLM tidak tersedia (ReadTimeout)` pada 90 s; Ollama `prompt eval 67465 ms / 1300 token`. Setelah
   fix: `vlm=ok ms=69340`.
2. Skrip smoke (`run-e2e-smoke.sh`, `odoo_pos_smoke.py`, `bff_smoke.py`, README) + runbook §3b. ruff/shellcheck
   bersih.

## G10.4 Sisa (bukan blocker; detail G10.5 di laporan E2E)
- data-ai/devsecops: VLM CPU ±70 s per foto → resize gambar sebelum VLM dan/atau GPU produksi (ADR-008).
- devsecops: `log-format` s3-gateway pakai `%Tt` (menyesatkan, tambah `%Ta`); satu watchdog HAProxy 739 ms;
  `docker logs --since` s3-gateway tidak andal di Docker Desktop → cek ingest Loki.
- frontend: `superapp-web` dev 77 % dari limit 512 MiB.
- Test unit service tidak diulang (tidak ada kode service yang diubah). Belum diverifikasi: POS OWL, halaman
  status penghapusan di browser, VLM di GPU.

# Gelombang 11 — Penutupan runtime: regresi, sink audit nyata, SEC-44 lewat RPC (2026-10-06)

Diverifikasi di **runtime nyata** (Docker Desktop, semua profil termasuk `ai`), basis `26110ef`. Detail & bukti:
`docs/testing/E2E_SMOKE_REPORT.md` bagian Gelombang 11. Tidak ada commit.

## G11.1 Hasil
| Item | Hasil |
|---|---|
| Build ulang image (digest dipin) + `up -d` | sukses; `odoo`, `minio`, `minio-init` di-recreate; 0 container `unhealthy` |
| Upgrade `-u efn_audit,efn_setup_audit,efn_pos_otp_gate` | exit 0, 0 ERROR/WARNING; post-migrate 19.0.1.1.0 menghapus 2 rule lama; restart `healthy`, 0 ERROR sepanjang sesi |
| Smoke penuh a b c d f e g (satu run) | **PASS=108 FAIL=0** (a 21, b 17, c 16, d 7, f 5, e 24, g 19); PII di log 7 service = 0 |
| Sink audit nyata | perubahan lewat Traefik wg-https + login TOTP + `/web/dataset/call_kw` (sysadmin QA): 3 baris `EFN_AUDIT_SINK` di stdout Odoo → Loki `stream="efn_audit_sink"` (query mTLS sertifikat grafana; tanpa sertifikat ditolak) → **3 alert `EfnAuditControlChange`** di Alertmanager (≈16 s) |
| Retensi 30 | `ValidationError` "minimal 365 hari… Nilai 30 ditolak"; tidak ada baris sink untuk transaksi gagal |
| SEC-44 RPC | kasir (POS user + Contact Creation): `write`, `web_save`, kosongkan, batch, context palsu → `AccessError`; **jalur sudo nyata** portal `/my/address/submit` (`partner_sudo.write` dengan uid kasir) → 403 + rollback; manajer POS boleh + chatter ter-mask |

## G11.2 Perbaikan
Tidak ada (tidak ditemukan bug integrasi). File yang diubah: hanya laporan ini dan `docs/testing/E2E_SMOKE_REPORT.md`.

## G11.3 Sisa
- `EfnAuditUnexpectedPurge` dengan Odoo nyata **belum**: uji purge via server action ditolak pengaman izin sesi agen
  (penghapusan log audit); butuh izin eksplisit user. Jalur ini sudah diuji devsecops dengan baris sintetis.
- Observasi: traceback lengkap di `data.debug` error JSON-RPC Odoo (standar, internal-only) → security-reviewer;
  grup diubah via `odoo shell` tidak meng-invalidasi cache worker → ubah lewat UI/RPC; `group_pos_user` tanpa
  Contact Creation tidak bisa onboarding pelanggan (keputusan konfigurasi functional).
- Data QA tertinggal di DB dev: user `qa_g11_*`, partner 60, challenge `QA-G11-SEC44`, server action 591.

# Gelombang 12 — Penutupan runtime: GL, salinan audit + tamper, smoke penuh + VLM (2026-10-07)

Basis `ce7e2b0`, tidak ada commit. Verifikasi di stack dev nyata (bukan hanya baca kode). Detail & bukti:
`docs/testing/E2E_SMOKE_REPORT.md` bagian Gelombang 12.

## G12.1 Hasil
| Item | Hasil |
|---|---|
| Rebuild api-gateway/ocr-worker/etl/odoo + boot 5 profil | OK, 40 container, 0 unhealthy, Prometheus 34/34 up |
| `-u efn_audit,efn_setup_audit,efn_pos_otp_gate,efn_pos_payment_gateway` | exit 0, 0 ERROR; efn_audit 19.0.1.2.0 (`origin_user_id`) |
| Grant ETL (`efn_odoo`) + job GL & audit copy | jalan; tidak ada `audit_copy gagal` dari ETL nyata |
| GL (3 jurnal posted di company 3 & 4, 2 IC) | `fact_gl_line` 6 baris logis; `v_gl_consolidated` dibaca `efn_metabase`, eliminasi IC benar; `efn_metabase` ditolak ke `audit_log_copy`/INSERT (497) |
| Tamper `efn_audit_log` id 17 (dev) | recon `modified=1, sample_ids=[17]` → Loki → **`EfnAuditReconAnomaly` di Alertmanager ±15 s**; baris dikembalikan, recon 0, alert resolved |
| `EfnAuditCopyFailing` | tidak firing dari ETL nyata (yang aktif = baris sintetis devsecops, resolved 04:56) |
| Smoke `a b c d f e g v` satu run | **PASS=121 FAIL=0** (run 1: 108/4 karena bug skrip `v`) |
| SEC-34 | solver smoke lolos; solusi lintas IP → 400 `captcha_required`, IP sama → 202 |
| PII di log 7 service | 0 |

## G12.2 Perbaikan
- `scripts/smoke/run-e2e-smoke.sh`: `scenario_e` set `ERASED=1`; `scenario_v` login nomor fiktif baru bila akun sudah
  dihapus DSAR (sebelumnya 401 karena token akun terhapus). shellcheck bersih.

## G12.3 Sisa
- Alert dev permanen `EfnPublicEndpointDown`/`EfnWgCertProbeFailed` (self-signed) → usul silence dev (devsecops).
- Log ETL dari `compose run --rm` tidak sampai Loki (catat di runbook etl-audit-copy bila operator menjalankan recon
  manual).
- Belum: UI Metabase→ClickHouse, kelas recon erasure/retention dengan log yang sudah tersalin, tour POS di stack ini, CI.

# Gelombang 13 — Penutupan runtime: loyalty POS E2E + dashboard (2026-10-07)

Basis `c1b770a`, tidak ada commit. Verifikasi di stack dev nyata. Detail: `docs/testing/E2E_SMOKE_REPORT.md`
bagian Gelombang 13.

## G13.1 Hasil
| Item | Hasil |
|---|---|
| Rebuild api-gateway/odoo/etl/metabase + boot 5 profil | OK, semua healthy |
| `-u efn_audit,efn_pos_otp_gate,efn_setup_pos -i efn_pos_loyalty_ledger` | exit 0, 0 ERROR |
| SQL 05, secret baru, `metabase-provision` | ada / ter-mount / exit 0 |
| Skenario baru `l` (32 cek): earn → saldo → OTP `pos_redeem` → reserve → commit → release (dialog & cancel draft) → lintas purpose (Odoo + BFF) → refund proporsional → outbox saat BFF mati → PDP → ETL | 32/32 |
| Smoke `a b c d f e g l` satu run (snapshot `c1b770a`) | **PASS=140 FAIL=0** |
| Metabase 3 dashboard via API | 28 dashcard, 0 gagal; poin EFN 6.000, refund 97.000, lot kedaluwarsa, indeks Price Hunter terlihat |
| HP mentah di tabel loyalty baru / log 7 service | 0 |

## G13.2 Perbaikan (harness)
- `run-e2e-smoke.sh`: skenario `l`; `odoo()` tidak lagi menghentikan run diam-diam pada traceback `psycopg2.errors.*`;
  `scenario_b` `gw` `|| true`; trap membunuh semua follower log.
- `odoo_pos_smoke.py`: 6 langkah `loy_*` baru, `SMOKE_DRAFT`, output outbox datar, helper `bff_post/bff_get`
  kompatibel SEC-58 (`_efn_post`) dan `c1b770a` (`post`).
- `scripts/smoke/README.md`: dokumentasi skenario `l`.

## G13.3 Sisa
- Working tree agent lain (SEC-58 dkk.) belum di-upgrade di DB dev (`redeem_max_pct`, manifest naik, SQL 06) → reserve
  poin dev gagal sampai `-u` + SQL 06. Run final sengaja memakai snapshot `c1b770a`.
- Observasi data-AI: penyesuaian poin pada refund masuk `refunds` (uang), poin yang dikembalikan tidak terlihat terpisah.
- Belum: frontend POS OWL (tour refund berpoin), `reservation_expired`, redeem/refund offline, CI GitHub, `v`.

# Gelombang 13c — Regresi penuh + cek SEC-58/59/61/65 di stack nyata (2026-10-07)

Dijalankan bersama peran devsecops (satu agent), runtime dev nyata (bukan hanya baca kode). Tanpa commit.

## G13c.1 Hasil
| Cek | Hasil |
|---|---|
| Smoke `a b c d f e g l` satu run | **153/153 PASS** (G13: 140) |
| (a) SEC-58: user internal & kasir → `efn.bff.client._efn_post/_efn_get/post/get` via `/web/dataset/call_kw` (login password + TOTP lewat Traefik wg-https) | semua ditolak (AccessError metode privat / metode tidak ada), kontrol positif OK |
| (b) SEC-61: earn 2005 poin > batas harian 2000 | `earn_limit_exceeded` final, order review, 0 earn/ledger BFF; `EFN_LOYALTY_ANOMALY earn_limit_member_day` di log (tanpa ANSI/HP) → Loki → `EfnLoyaltyAnomalyWarning` active di Alertmanager |
| (c) SEC-59: retur tanpa struk | review `return_unlinked`, tanpa outbox earn/reverse, saldo tetap |
| (d) SEC-65: migrasi member saat DSAR delete | members & opening-balance `409 member_deleting`; setelah erasure `409 member_erased`, tidak hidup kembali; 3 baris anomali `migration_erased_member` (`MIGRATION_ENABLED=true` sementara, dikembalikan `false`) |

## G13c.2 Perbaikan (harness)
- `scripts/smoke/odoo_rpc_smoke.py` (baru), `scripts/smoke/obs_smoke.py` (baru); `odoo_pos_smoke.py` langkah `rpc_users`,
  `loy_refund_noreceipt`, `loy_outbox` + `loyalty_note`; `run-e2e-smoke.sh` helper `obs`, `migrate`, 13 cek baru;
  README smoke. Kredensial user smoke & kunci migrasi tidak pernah di argv/log host (file 0600 → stdin; `curl -H @file`
  di dalam container).
- Tidak ada bug produk ditemukan; kontrak BFF ↔ Odoo (`earn_limit_exceeded` → failed + review, `pos_config_id` di earn)
  cocok di runtime.

## G13c.3 Sisa
- Kasus critical (`earn_limit_company_hour`, manipulasi ledger) dan dead-man 2 jam tidak dipicu nyata.
- Belum: frontend POS OWL (tour refund berpoin / order berpoin ditolak sync), `/json/2` dengan API key, CI GitHub.
- Data QA baru di DB dev: user `smoke_rpc_internal`, `smoke_rpc_cashier` (password/TOTP acak per run, tidak tersimpan
  di host), partner `SMOKE Member O`, order 20 juta (review) + retur tanpa struk.

# Gelombang 17 — Integrasi nyata & smoke penjualan online (2026-10-09)

Berbeda dari catatan "tanpa runtime" di definisi agent: gelombang ini dijalankan di **stack dev nyata** (Docker Desktop),
atas instruksi efn-lead. Detail lengkap: `docs/testing/E2E_SMOKE_REPORT.md` bagian Gelombang 17.

## G17.1 Hasil
- Integrasi: build ulang odoo/api-gateway/superapp-web; `efn_online_order efn_setup_online` terpasang di `efn_odoo`
  (0 ERROR); kanal RTL dev dikonfigurasi lewat `odoo_online_smoke.py setup`; API key user teknis dibuat dengan prosedur
  runbook `odoo-online-api-key.md` §1 (runbook bekerja apa adanya); migrasi 07; recreate 7 container, semua healthy.
- `test-gateway.sh` 97/97; dengan key nyata 10 path `/json/2` menjawab aplikasi (200 / 422 `efn_online:<kode>`).
- Smoke penuh `a b c d f e g l o`: **PASS=241 FAIL=1** — tahap lama 151/151, tahap baru `o` 90/1.

## G17.2 Konsistensi kontrak Odoo ↔ BFF ↔ frontend (diuji runtime)
| Sambungan | Status |
|---|---|
| BFF json2 → gateway → E-28 (10 metode, argumen bernama, error `efn_online:<kode>` HTTP 422) | cocok; lihat G17-QA-02 untuk `context.lang` |
| Blok `channel` katalog (L-11) → `GET /v1/catalog/channels` | cocok (company_code, outlet, fulfilment) |
| Event Odoo → BFF (`X-EFN-Service-Key` + `X-EFN-Signature`, `seq`, idempotency key) | cocok untuk placed/paid/confirmed/ready/picked_up/cancelled/refunded; **`review` dari `confirmed|ready` tidak diterapkan** (G17-QA-03) |
| Saga paid → mark_paid → confirmed/stock_short; unpaid_closed; compensate; refund.requested → refund_settled | cocok (stock_short, late_payment, refund CS settled) |
| Next.js `app/api/{catalog,cart,orders}` ↔ BFF `/v1` | cocok: bentuk respons, 201 checkout, key server-side L-8, allowlist body cart, `isSameOrigin` 403 |
| `DELETE /v1/me` ↔ L-4 | **tidak cocok sebelum perbaikan** (G17-QA-01) |

## G17.3 Perbaikan (produk)
- **G17-QA-01** `services/api-gateway/src/privacy/dsar.service.ts:76-78`: `@Optional() @Inject(OnlinePrivacyService)`.
  Tanpa token eksplisit, metadata tipe `OnlinePrivacyService | null` = `Object` → service tidak pernah disuntik →
  `DELETE /v1/me` menjadwalkan hapus akun walau ada pesanan online dibayar (pelanggaran L-4/PDP). Test baru
  `src/privacy/dsar-injection.spec.ts`. Jest 42 suite / 501 test, build & lint 0 error.
- **G17-QA-02** `services/odoo/addons/efn_online_order/models/efn_online_order.py` (`efn_online_refund` ±587–607,
  `efn_online_refund_settled` ±620): pakai env order/kanal (context klien dibuang, §10.4.4 no. 4), bukan `self.env`.
  Sebelumnya `context.lang=id_ID` dari BFF → `Invalid language code` 422 bila id_ID tidak terpasang → refund CS dan
  `refund_settled` selalu gagal. Test baru `test_refund_with_bff_lang_context_not_installed`; 68 test modul online
  0 gagal di container; ruff lolos.

## G17.4 Test/harness ditambah
- `scripts/smoke/online_smoke.py` (klien publik + subperintah `events` internal), `scripts/smoke/odoo_online_smoke.py`
  (setup kanal dev, stok, staf ready/handover, outbox, apikey), tahap `o` di `run-e2e-smoke.sh` (default kini
  `a b c d f e g l o`), README smoke. Tanpa secret di skrip (key/HMAC/service key dibaca di dalam container dari
  `secrets/` read-only); HP fiktif acak; OTP dari log BFF dev.

## G17.5 Sisa / untuk lead
- **G17-QA-03 (lead → BFF):** status `review` dari `confirmed|ready` diabaikan BFF (peringkat sama) tetapi dicatat
  `applied`; pelanggan tetap melihat `confirmed` dan bisa meminta kode. Butuh keputusan kontrak §10.6.2.
- **G17-QA-04 (efn-odoo-functional):** label pajak `Non-luxury Good Taxes` (Inggris) ke pelanggan; pasang id_ID /
  `invoice_label`.
- **G17-QA-05 (devsecops):** watch mode BFF dev tidak menangkap perubahan bind mount Windows → perlu recreate (runbook).
- **G17-QA-06 (lead):** butir §10.14 "status HTTP json2 per exception" bisa ditutup (UserError = 422, terverifikasi).
- Artefak dev: refund Odoo `RINV/2026/00001` di `S00001` dibuat manual saat diagnosis, tidak tercatat di BFF.
- Belum: browser nyata, notifikasi WA/push, circuit breaker/alert online, CLI `online-resolve`/`online-refund-settle`,
  provider non-mock, place paralel lintas proses.

## G17.6 Run akhir (2026-10-10, HEAD `32686c3`)
- **Smoke penuh: PASS=267 FAIL=0.**

  | Tahap | PASS/FAIL |
  |---|---|
  | a | 21/0 |
  | b | 17/0 |
  | c | 15/0 |
  | d | 7/0 |
  | f | 6/0 |
  | e | 24/0 |
  | g | 19/0 |
  | l | 42/0 |
  | o | 116/0 |

- `test-gateway.sh` 97/97. Detail: `docs/testing/E2E_SMOKE_REPORT.md` §G17.8.
- **Status temuan sebelumnya:**
  - G17-QA-03 (review) tertutup lewat L-14: status `review` dan pickup-code 409 `order_in_review`.
  - G17-QA-04 (label pajak) tertutup: label `PPN 11%`. Di DB dev lama label diisi sekali lewat shell karena data
    modul noupdate.
  - G17-QA-05 (watcher dev) tertutup lewat watcher polling.
- **Skenario baru di tahap `o`:**
  - SEC-95/104/105: picking di luar wizard dan context `efn_online_internal` palsu ditolak; batal dari Odoo tanpa
    alasan ditolak.
  - Batal oleh manajer menghasilkan refund `approval_required`. Approve oleh peran `cs` ditolak; approve oleh approver
    lain → settled.
  - L-19/L-20: refund CS selalu butuh approve. Pembuat yang sama dan peran `cs` ditolak; approver lain → settled.
  - SEC-112: settle manual oleh peran `cs` atau oleh pembuat refund ditolak.
  - SEC-96: stok di luar katalog → 404.
- **Bug produk baru:** tidak ada pada run akhir.
- **Belum diuji:** jalur POS `efn_online_pos_sale` (`pos_sale` tidak terpasang di dev), dan wrapper host
  `efn-online-cs` dengan sudo + docker asli.

# Gelombang 18 — Smoke nyata G18 + r11 + re-review (2026-10-15, HEAD `bc0a06a`)

Detail di `docs/testing/E2E_SMOKE_REPORT.md` §G18. Dijalankan di stack dev nyata dengan DB Odoo **baru** (IDR Rp1).

## Hasil
**Smoke penuh: PASS=305, FAIL=0.**

| Tahap | PASS/FAIL |
|---|---|
| a | 22/0 |
| b | 17/0 |
| c | 15/0 |
| d | 7/0 |
| f | 6/0 |
| e | 24/0 |
| g | 19/0 |
| l | 43/0 |
| p (baru) | 31/0 |
| o | 121/0 |

**Cakupan G18 dan r11 yang lulus:**
- **W-03:** Rp1 dan pajak bulat.
- **E-30 / K-18-1..5:**
  - pembayaran Poin EFN;
  - DPP/PPN tetap;
  - `payment_amount`;
  - `points_restored_value`;
  - mart `fact_pos_payment`.
- **E-31:** refund ke metode asal, tunai berlebih, retur tanpa struk hanya tunai.
- **SEC-113/115/116/121/125/126:** diuji lewat RPC kasir nyata (login password + TOTP).
- **L-26:** hold → BFF 409 → supervisor restore_approve → `restore_approved`; barang excluded lolos.
- **E-34/K-18-8:** `pos_enroll` dengan/tanpa `age_confirmed`.
- **L-21/L-22:** usia 17+ saat login; hapus akun 409 → konfirmasi → 202 + `erasure_deadline` di status; `account_deleting`.
- **L-21c:** `source_ref` opak.
- **K-18-6/SEC-118:** rekonsiliasi harian mode `payment`, 0 selisih untuk order run ini.

## Perbaikan
- **Produk:** tidak ada di gelombang ini.
- **Konfigurasi dev:** `docker-compose.dev.yml` membuat `ETL_LOYALTY_RECON_NOT_BEFORE` bisa ditimpa `.env`.
- **Harness:** lihat E2E §G18.3. Skrip yang diubah: `odoo_pos_smoke.py`, `odoo_rpc_smoke.py`, `bff_smoke.py`,
  `online_smoke.py`, `run-e2e-smoke.sh`, dan file baru `etl_points_recon_smoke.py`.

## Temuan untuk lead
- **G18-QA-01 (lead → odoo-engineer + bff):** kunci idempotensi event online memakai id lokal Odoo. Setelah reset atau
  restore DB Odoo, event baru diabaikan sebagai `duplicate`.
- **G18-QA-02 (devsecops):** runbook reset DB Odoo dev belum mencakup reset data turunan (katalog BFF, mart ClickHouse
  + watermark, event online).
- **G18-QA-03 (CS dev):** rekonsiliasi menandai reverse hold yang tertinggal dari run terputus. Ini perilaku yang benar.

## Belum diuji
- UI browser POS dan POS offline.
- Eskalasi erasure hari 3/10/14.
- Rotasi key via systemd di host nyata.
- Housekeeping Metabase dan TTL ClickHouse pada data nyata.
- UI jalur POS ↔ SO online.

## G18.7 Run konfirmasi sebelum PR (2026-10-18, HEAD `bea9be4`)
- **Suite Odoo pola CI** (DB uji terpisah):
  - unit: **501 test, 0 failed / 0 error / 0 ERROR**;
  - `check-odoo-deps`: 17 modul, 0 terlarang;
  - `efn_setup_security` install-after: exit 0;
  - tour: **29/29**, 0 skip browser.
- **Stack dev:**
  - rebuild odoo, api-gateway, etl;
  - `-u efn_online_order` (19.0.1.1.0), `efn_pos_loyalty_ledger`, `efn_pos_otp_gate`: 0 ERROR;
  - migrasi 08 jalan;
  - ETL di epoch audit 1, tanpa `divergence_blocked`.
- **`online_smoke.py`:** event sintetis memakai kunci `online:<platform_order_ref>:<event>:<seq>` + `event_id`. Skenario baru:
  - kunci sama dengan isi berbeda → 409 `event_conflict`;
  - kunci lama (id lokal Odoo) milik bagian lain → diproses + anomali `odoo_event_key_reused`.
- **Smoke penuh: PASS=307, FAIL=0.**

  | Tahap | PASS/FAIL |
  |---|---|
  | a | 22/0 |
  | b | 17/0 |
  | c | 15/0 |
  | d | 7/0 |
  | f | 6/0 |
  | e | 24/0 |
  | g | 19/0 |
  | l | 43/0 |
  | p | 31/0 |
  | o | 123/0 |

- **Status temuan:** G18-QA-01 dan G18-QA-02 ditutup. Tidak ada bug baru.
- **Catatan:** Docker Desktop di mesin dev berhenti sendiri dua kali (15 Okt); run diulang penuh setelah stack di-recreate.

---

## G19. Konsistensi kontrak §11 (L-27..L-39), commit `ad4dbd1`, 2026-10-18

> **Tidak ada yang dijalankan.** Sesi QA ini tanpa shell/Docker: seluruh verifikasi dengan membaca kode kedua sisi. Test/skrip yang saya tambah **belum dieksekusi**; lead yang menjalankannya (Odoo `-i`/test tag `efn`, Jest `online-saga`, smoke tahap `o`).

### G19.1 Hasil: kontrak cocok di semua sisi yang diperiksa (tidak ada mismatch bentuk)

| Area | Odoo | BFF parser/service | Stub mock | Frontend | Hasil |
|---|---|---|---|---|---|
| `promo_codes` quote/place | `promo_codes_payload` efn_online_common.py:119 (≤3, `^[A-Za-z0-9_-]{1,40}$`, upper, dedupe, `invalid_input`); `PLACE_OPTIONAL_KEYS` efn_online_order.py:101; hash payload hanya bila ada :325 | `normalizePromoCodes` promo-codes.ts:25; json2 quote selalu kirim `promo_codes` (json2-client.ts:304-306); place kirim `g.promo_applied` (orders.service.ts:218) | `compute` menolak >3 (odoo-online.mock.ts:196) | `sanitizePromoCodes` cart.ts:280 | Cocok. Bentuk kode identik di 4 tempat |
| `promo_applied`/`promo_rejected` | `promo_rejected = codes − applied` (efn_online_channel.py:934) → subset kode yang dikirim BFF, tidak beririsan; mode `none` → semua ditolak (:697) | parser `promoList` (parse.ts:60-70) + irisan→`invalid_response` (:244); `quoteGroup` menolak kode di luar permintaan (quote.service.ts:209) | mock sama (odoo-online.mock.ts:207-217) | `parsePromo` (cart.ts:292) | Cocok |
| Baris reward | `reward: True` (efn_online_channel.py:631), nama = program, subtotal negatif; `_efn_online_promo_unacceptable` menolak positif / >20 (:791-800) | `signedNum`, `reward && subtotal>0` → bad (parse.ts:216-219); `MAX_REWARD_LINES=20` (quote.service.ts:72,197); `placeLines` membuang reward (:67) | `reward:true`, id 9001 | `reward` dipetakan, `quoteHasIssues` mengabaikan reward (cart.ts:395) | Cocok (batas 20 sama di Odoo & BFF) |
| `channel.brand_code` | blok katalog :478 (`false`→`None`), regex `BRAND_CODE_RE` | `parseChannel` parse.ts:156-161, `?brand=` → `400 invalid_request` (catalog.controller.ts:19) | mock `MOCK_CHANNELS` brand GL/BACHA/PB | `channelsPath` catalog.ts:90; `brandCode: null` untuk tenant lama (brands.ts:94,114, L-33) | Cocok |
| `out_of_stock` saat place (L-38) | `online_error("out_of_stock")` efn_online_order.py:345,417 (savepoint place di-rollback) | `mapOdooError` → `409 out_of_stock` (online-errors.ts:51) | mock `place` melempar `out_of_stock` (:255) | `REQUOTE_CODES` memuat `out_of_stock` (orders.ts:126) | Cocok; **tidak ada test BFF untuk jalur ini → ditambah (G19.3)** |
| 70 / 3600 / margin 5 menit | field default 70 (efn_online_channel.py:152-156); demo RTL + 4 kanal 70 (efn_online_demo.xml:53,193,217,243,268); smoke 70 (odoo_online_smoke.py:151) | default 3600 (app-config.ts:1331), margin `ODOO_EXPIRY_MARGIN_MS` 5 menit (orders.service.ts:26,243); test 70/65 lolos, 64/25 ditolak (online-saga.spec.ts:942) | `MOCK_PAYMENT_TIMEOUT_MINUTES=70` (mock.ts:75) | countdown dari `expires_at` BFF | Cocok; selisih 10 menit ≥ margin 5 |
| `promo: {applied, invalid}`, 400/429 | – | `invalid` = tidak terpakai di grup mana pun (quote.service.ts:142-145); `parseQuoteBody` → 400 `invalid_request` (cart.controller.ts:83-94); `PROMO_MEMBER`/`PROMO_IP` → 429 `rate_limited` (:74-76) | – | route Next 400 `promo_invalid` generik (quote/route.ts:25); `fetchQuote` memetakan 400/429 ke pesan promo, checkout tidak terblokir (CartView.tsx:55-72) | Cocok (lihat catatan 429 di G19.5) |

### G19.2 Env, secret, user teknis: cocok

- Env rate limit promo: `ONLINE_RL_PROMO_MEMBER_LIMIT/WINDOW_SECONDS`, `ONLINE_RL_PROMO_IP_LIMIT/WINDOW_SECONDS` dibentuk `ONLINE_RL_${name}_…` (app-config.ts:1388-1389; default 10/900 & 30/900 :1600-1601) = compose base (docker-compose.yml:1133-1136) = `.env.example:112-115` = runbook online-orders.md:122-123 (sengaja tidak lewat config rilis/`deploy-validate`). `ONLINE_PAYMENT_TIMEOUT_SECONDS` 3600 dan `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` 2: app-config.ts:1332,1339 = compose :1128,1131 = `.env.example:108,110` = runbook :107-115.
- Pemetaan `-`→`_`: BFF `channelCode.replace(/-/g,'_')` (app-config.ts:1610,1615), overlay `docker-compose.online-{rtl-gl,rtl-bacha,fnb-pb,fnb-bacha}.yml` (env `ODOO_API_KEY_ONLINE_RTL_GL_FILE` dst. + secret `odoo_api_key_online_rtl_gl` dst.), `scripts/secrets.list:80-83`, `efn-rotate-online-key` (`tr 'A-Z-' 'a-z_'`, baris 118-120; JS verifikasi membentuk nama env yang sama, baris 428), `deploy.sh:373-381` (overlay `online-<kode huruf kecil>`), CI menyertakan semua overlay (ci.yml:489). Regex kode kanal `^[A-Z][A-Z0-9-]{1,15}$` sama di Odoo, BFF, frontend, SQL `07-platform-online.sql`, skrip rotasi, deploy-validate.
- User teknis per kanal (L-37): demo `efn_online_rtl`→RTL, `efn_online_rtl_gl`→RTL-GL, `_rtl_bacha`, `_fnb_pb`, `_fnb_bacha` (efn_online_demo.xml:37,145-172) cocok dengan `LOGIN=efn_online_${CH_LC}` skrip rotasi; pengecekan `user_shared` di skrip (baris 343-348) sejalan dengan constraint Odoo (efn_online_channel.py:249-263). Test `test_setup_online.py:119-123` menjaga 1 user = 1 kanal.

### G19.3 Perbaikan yang dilakukan (bug kecil / dokumen)

| # | File | Perbaikan | Alasan |
|---|---|---|---|
| 1 | `scripts/lib/deploy-validate.sh:180` + `infra/compose/.env.example:109` | `ONLINE_QUOTE_TTL_SECONDS` rentang validator `60..3600` → `60..1800` | BFF `onlineQuoteTtlSeconds` menolak > 1800 (app-config.ts:1335, `ConfigError`). Nilai 1801..3600 lolos `deploy-validate` lalu BFF gagal start. Peninggalan G17 |
| 2 | `docs/04_SERVICE_CONTRACTS.md` §11.1 (baris `stock_hold_mode`) | Teks `409 cart_has_issues` dicoret + rujukan L-38 (`409 out_of_stock`) | Kontrak bertentangan dengan dirinya sendiri; pembaca tabel §11.1 bisa membangun yang lama |
| 3 | `docs/runbooks/odoo-online-api-key.md` "Batas / belum" | Klaim "data demo memakai `efn_online_rtl` bersama → `user_shared`" diganti kondisi sebenarnya (demo sudah satu user per kanal; hanya DB dev lama yang masih bersama; `FNB` demo tanpa user) | Basi sejak L-37 |

### G19.4 Test dan skrip yang ditambah (belum dijalankan)

- **Jest** `services/api-gateway/src/orders/online-saga.spec.ts` (blok G19): *L-38 reservasi gagal saat place → `409 out_of_stock`* (bukan `cart_has_issues`), order `failed`/`place_failed`, tepat 1 kompensasi `cancel`, tanpa `payment_intent_id`. Sebelumnya tidak ada test jalur ini (mock `out_of_stock` hanya terpicu setelah cek `issues`).
- **Odoo** `efn_online_order/tests/test_online_g19.py` `TestEfnOnlinePromotionsE36.test_auto_promo_still_applies_in_mode_none_only_codes_rejected`: L-38 bagian kedua (promo otomatis tetap berlaku di kanal `stock_hold_mode=none`, kode ditolak, hash quote = place, SO draf memuat reward, bayar → faktur = `amount_payable`). Sebelumnya hanya kode di mode `none` yang diuji.
- **Daftar skenario docs/08 vs `test_online_g19.py`** — §10.1 no. 7: semua 8 butir ada (`test_place_confirm_reserves_stock`, `…beyond_remaining…`, `…expiry_releases…`, `…pos_sale_revokes…`, `…ready_marks_picked…`, `…mode_none_regression`, default 70). §10.2 no. 7: 8 dari 9 ada; **kurang** "buy X get Y … pajak benar (PPN incl RTL, **PB1 excl FNB**)" — `test_buy_x_get_y_reward_line` hanya PPN incl (company uji ritel). Tidak ditambah: butuh fixture company F&B + `price_include=False` + fiscal position, tidak bisa saya validasi tanpa runtime → **sisa #1**.
- **Smoke** (lead yang menjalankan, tahap `o`):
  - `scripts/smoke/odoo_online_smoke.py`: langkah baru `free` (free_qty bulat produk smoke di gudang outlet); `describe` + `hold_minutes` (expires_at − create_date).
  - `scripts/smoke/online_smoke.py`: subperintah `quote_promo <spec> [label]` — kode benar huruf kecil + salah dalam satu permintaan (`promo == {applied:[SMOKE10], invalid:[NOSUCH99]}`, tepat 1 baris reward negatif, total ≈ 90 % ±2, tanpa `issues`, `quote_hash`/`promo_applied` tidak bocor), dua kode salah berbeda → bentuk respons & total identik (anti-enumerasi, tidak memblokir), 4 varian format salah → `400 invalid_request`, quote final berkode untuk checkout. Total 4 quote berkode per member (batas 10/15 menit).
  - `scripts/smoke/run-e2e-smoke.sh` tahap `o`: cek setup `stock_hold_mode=confirm`, `payment_timeout_minutes=70`, program SMOKE10 sah; pesanan member B (jalur kedaluwarsa) kini berpromo dan mengukur reservasi: `o.g19_place_so_sale_reserved_70min_reward` (SO `sale`, move `assigned`, batas bayar 69–70 menit, ≥1 baris reward), `o.g19_stock_drops_after_place` (free_qty minyak −1), `o.g19_stock_restored_after_expiry` (kembali ke nilai awal setelah sweeper + `payment_expired`). Jalur lama (`timeout_odoo_so_cancelled`, `late_payment_refunded`) tidak diubah selain jumlah bayar kini setelah diskon.
  - Sudah benar sebelum saya sentuh (tidak diubah): setup memakai satu user teknis per kanal dan melepas `efn_online_rtl` dari kanal lain (odoo_online_smoke.py:159-162), batas 70 menit, `stock_hold_mode=confirm`, cek `so_state == sale` + `reserved` (run-e2e-smoke.sh:667).
- **UAT**: bagian baru **H1–H13** di `docs/testing/UAT_SCENARIOS.md` (reservasi 1 jam, kedaluwarsa + bayar terlambat, rebutan stok terakhir, POS menjual stok tercadang, batas order terbuka, kode benar/salah/kedaluwarsa/kuota, kupon sekali pakai, rate limit kode, promo otomatis per brand, promo < Rp1, Bacha dua PT termasuk refund parsial lintas PT dan kegagalan sebagian, ketahanan Odoo mati + rotasi key per kanal).

### G19.5 Sisa risiko / temuan yang tidak diperbaiki (urut keparahan)

1. **Test PB1-exclusive pada promo F&B hilang** (docs/08 §10.2 no. 7 butir 3, setengah). Risiko: baris reward pada kanal F&B (pajak eksklusif, fiscal position outlet non-dine-in) belum terbukti menghasilkan `subtotal_incl ≤ 0` dan total konfirmasi stabil; PB1 non-dine-in sendiri masih menunggu konsultan pajak (R-L09). Wajib ada sebelum kanal `FNB-PB`/`FNB-BACHA` menerima kode promo.
2. **Alur stok-kurang smoke (tahap `o` no. 3) kini bergantung pada pencabutan reservasi oleh penyesuaian inventaris** (`set_stock` qty 0 → `_free_reservation`), padahal sebelum G19 tidak ada reservasi. Logikanya sama dengan unit test `consume_outside`, tetapi belum pernah dijalankan end-to-end di mode `confirm`. Bila gagal: `stock_short` tidak muncul (reservasi bertahan) — periksa dulu urutan `stock` vs `pay`.
3. **Reward produk (beli X gratis Y) yang stoknya kurang** ditandai `issues:[out_of_stock]` dengan `product_id` produk hadiah (`_efn_online_reward_short`, efn_online_channel.py:852-868) yang **tidak ada di keranjang**. BFF menerima (quote.service.ts:205 hanya memeriksa produk cart), checkout diblok `cart_has_issues`, tetapi pelanggan tidak punya barang untuk dikurangi; satu-satunya jalan menghapus pemicu promo. Perlu keputusan produk (abaikan reward itu saja, atau pesan khusus). Tidak ada test Odoo/BFF untuk kasus ini.
4. **Batas 20 baris reward** (efn_online_channel.py:68,797 & quote.service.ts:72) dan **tagihan < Rp1 dari promo otomatis** tidak punya test. Yang kedua hanya `_logger.warning` di Odoo (:827-832); komentar menyebut "BFF menolak tagihan < Rp1 (`online_config_error`)", tetapi parser BFF (`rupiah`, parse.ts:72) menerima 0 dan checkout/payment intent Rp0 tidak dicek di `orders.service.ts`. Mitigasi sekarang hanya konfigurasi Marketing yang benar; usulan guard `amount_payable ≥ 1` di `parseQuote` (perubahan kecil, tidak saya lakukan karena menyentuh kontrak parser).
5. **Setelah 429/400 pada `requote`, kode promo yang sudah terpakai ikut hilang** dari keranjang (CartView.tsx:63 fallback quote tanpa kode → `settlePromo` mengosongkan `promoRef`). Pelanggan harus mengetik ulang; bukan kebocoran. Pesan 429 ("Terlalu banyak percobaan kode promo…", cart.ts:270) berbeda dari pesan generik "tidak dapat digunakan", tetapi tidak membocorkan keabsahan kode (hanya rate limit) — saya nilai sesuai L-28.
6. **Smoke tidak menguji 429 promo** (butuh 11 quote berkode per member; dicakup Jest `online-routes.spec.ts:123`). UAT H9 menutupnya secara manual.
7. **Kanal `FNB` demo tidak punya user teknis** (efn_online_demo.xml:112-124, README setup) padahal runbook odoo-online-api-key.md:34 dan overlay `docker-compose.online-fnb.yml` menganggapnya ada. Aman karena kanal nonaktif; rotasi `--channel=FNB` akan `user_missing`.
8. **Laporan agent lama basi** (`docs/agent-reports/efn-bff-engineer.md:636,655` menyebut `invalid_input`; kode & kontrak sudah `invalid_request` per L-36). Tidak diubah (riwayat laporan agent lain).
9. **Belum terverifikasi runtime** (semua di atas bersifat baca-kode): hash quote berpromo di Odoo identik antara SO savepoint-quote dan SO place; resolusi kode huruf kecil kupon `coupons`; perilaku `FOR UPDATE NOWAIT` program pada checkout paralel; ketiganya hanya ditutup unit test Odoo yang sudah hijau menurut lead.

---

## G20. Konsistensi kontrak §12 + L-48..L-58 (commit `7d55525` + working tree), 2026-10-19

> **Tidak ada yang dijalankan.** Sesi QA tanpa shell/Docker: verifikasi dengan membaca kode semua sisi. Test dan skrip smoke yang saya tambah **belum dieksekusi**; lead yang menjalankannya. Modul `efn_pii*` tidak disentuh.

### G20.1 Mismatch ditemukan dan diperbaiki

| # | Sisi pengirim | Sisi penerima | Masalah | Perbaikan |
|---|---|---|---|---|
| 1 (**blocker L-57**) | `efn_online_order/models/efn_online_event_outbox.py` `_efn_payload` (sebelum baris 200): payload event `review` **tidak memuat `review_reason`** | `api-gateway/src/orders/order-events.service.ts:153-155` membaca `o.review_reason`; `:265` hanya menerapkan `cancelled → review` bila `ev.review_reason !== null` | Bayar-setelah-batal/kedaluwarsa di kasir (Odoo menulis `review_reason=counter_paid_after_*` dan mengirim `review`, `efn_online_order.py:343-359`) tidak pernah sampai ke BFF sebagai alasan: BFF memperlakukan sebagai review biasa → transisi `cancelled → review` ditolak monoton → `ignored_stale`. Pelanggan tetap melihat "dibatalkan" padahal uangnya sudah di kasir; anomali `late_counter_payment` tidak muncul. Semua test hijau karena sisi Odoo hanya memeriksa `review_reason` field, sisi BFF memakai event rekaan | `_efn_payload`: `if self.event == "review": payload["review_reason"] = order.review_reason or None` (kode saja, tanpa PII; BFF mengabaikan nilai selain dua alasan counter) |
| 2 | `scripts/smoke/odoo_pos_smoke.py` `step_loy_refund_noreceipt` (dipanggil `run-e2e-smoke.sh` `l.refund_noreceipt_review`) | `efn_pos_refund_origin` default E-41 `manager_approval` (`models/pos_order.py:48-51,232-234`) | Retur tanpa struk tunai di smoke kini ditolak `UserError` (tanpa persetujuan) → cek `l.refund_noreceipt_review` gagal | `step_setup` menyetel `efn.pos.refund.unlinked_return_mode=cash` untuk smoke; jalur E-41 diuji langkah baru `ret_approval` (G20.3) |
| 3 | `tests/load/k6/efn-fase1.js:137` memakai `company_code` `['EFN','RTL','FNB']` | BFF/Postgres/ETL menolak RTL/FNB (L-53) | Uji beban POS 2/3 permintaan ditolak 4xx | diganti `['EFN','GL','PB','BCR','BCF'][rnd(5)]` |
| 4 | Odoo `order_limit` (`efn_online_channel.py:1148`, `product_id: null`), BFF `KNOWN_ISSUE_CODES` memuatnya | `superapp-web/lib/cart.ts` `KNOWN_ISSUE_CODES`/`ISSUE_LABEL` tidak | Issue dinormalkan ke `unknown_issue` ("Tidak dapat dipesan"), pelanggan tidak tahu harus mengurangi nilai pesanan (peninggalan G19, tetap memblokir checkout) | `order_limit` ditambah ke allowlist + label (`cart.ts`) |
| 5 | `docs/testing/UAT_SCENARIOS.md` bagian H memakai kanal G19 (`RTL-GL`, `RTL-BACHA`, `FNB-BACHA`) | kanal G20 `GL/BCR/BCF/PB` | Pembaca UAT menjalankan kode yang sudah ditolak deploy-validate/rotasi | catatan pemetaan di kepala bagian H |

### G20.2 Diverifikasi cocok (baca kode kedua sisi)

- **Kode entitas/kanal:** Odoo `efn_core/models/res_company.py:9-13` (Selection) + `res_company_data.xml:32-83` (xmlid lama dipertahankan, kode baru) + demo `efn_online_demo.xml` (kanal `GL/PB/BCR/BCF`, user `efn_online_gl/pb/bcr/bcf`, `brand_code` GL/PB/BACHA/BACHA) = BFF `loyalty.service.ts:12` `COMPANY_CODES` = ETL `entities.py` = Postgres CHECK di `02/03/05/07/08` + migrasi `09` = ClickHouse `08-g20-entities.sql:45` = `deploy-validate.sh:43` `EFN_ONLINE_CHANNEL_CODES=(GL PB BCR BCF)` = `secrets.list:81-84` = compose `docker-compose.yml:156-159` + overlay `online-{pb,bcr,bcf}.yml` = frontend `brands.ts:94`. Grep seluruh repo untuk `RTL|FNB|RTL-GL|FNB-PB|…`: sisa hanya test penolakan, komentar sejarah, xmlid `company_rtl/company_fnb` (disengaja, L-48), contoh nama outlet WireGuard `FNB-JKT-001` (nama perangkat, bukan kode PT), dan satu dokumen basi (lihat sisa #5).
- **Payload event Odoo ↔ BFF (setelah perbaikan #1):** `paid` → `payment:{method:"counter"}` (outbox :196-199 ↔ `parseOdooEvent` :147-152, sidik hanya bila ada); `refund_settled` → `refund:{refund_ref, amount(int), kind, initiator, provider_refund_ref:"pos:<uuid pos.order>", settled_at ISO-Z}` (outbox :176-181; Odoo mengisi `provider_refund_ref` = `pos.order.uuid` lewat `refund._efn_settle(..., self.uuid, ...)` `pos_order.py:170`) ↔ BFF :126-134 (regex `^pos:[uuid36]$`, nominal bilangan bulat) ↔ `settleCounterRefund` (cocok `refund_ref` = `odoo_refund_ref`, nominal persis, ≤ porsi; `refunded` tidak pernah settle `online-saga.service.ts:199-222`, `order-events.service.ts:291-292`). Kunci idempotensi `online:<uuid>:refund_settled:<seq>` lolos `KEY_RE` (underscore diizinkan).
- **place/lookup:** BFF kirim `order.payment_mode:'counter'` + `counter_code` hanya untuk counter (`odoo-online.types.ts:181-186`, json2-client.spec :409) ↔ Odoo `PLACE_OPTIONAL_KEYS` (`efn_online_order.py:118`), `counter_code ≠ pickup_code`, alfabet `PICKUP_CODE_RE` = mock BFF; hash payload tanpa kode bayar; replay idempoten. Blok `channel.payment_modes` Odoo = array string (`efn_online_channel.py:641`) ↔ `parsePaymentModes` (array saja). Lookup POS JS memanggil `pos.config.efn_online_counter_lookup([config.id], code)` ↔ metode `pos_config.py:107` ↔ `RPC_ALLOWLIST` (`test_rpc_surface.py:112`); normalisasi kode JS = server; hasil persis `{order_label, amount_payable}`.
- **Frontend:** `parsePayment` hanya membaca `payment.counter_code`, dibuang bila status ≠ `awaiting_counter_payment` (`orders.ts:288-290`); route `POST /api/orders` membuang kode dari respons checkout; `payment_method` divalidasi `isPaymentMethod` sebelum BFF; daftar pesanan tanpa kode. Key idempotensi checkout hanya dari `quote_id`, sedangkan sidik BFF memuat `payment_method` hanya bila `counter` — aman karena penolakan 422 terjadi **sebelum** order tersimpan (`orders.service.ts:166-175`).
- **Gerbang L-55:** `app-config.ts:1598` ↔ `deploy-validate.sh:253-265` ↔ compose default `qris,counter` ↔ `.env.example:113` ↔ `deploy.yml:279`/`deploy.sh:332` identik (production + online + `qris` tanpa xendit/midtrans ditolak kedua sisi; `mock` ditolak; `counter`+`disabled` lolos); batas counter ≤ batas order terbuka dicek di deploy-validate (BFF plafon 10).
- **Batas berat:** Odoo `{product_id:null,code:"weight_limit"}` / `{product_id,code:"weight_missing"}` (`efn_online_channel.py:517-519`) ↔ BFF `ORDER_ISSUE_CODES`/`PRODUCT_ISSUE_CODES` (product_id null wajib untuk `weight_limit`, wajib ada untuk `weight_missing`, fail-closed) ↔ frontend allowlist + `groupLevelIssues`/`productIssue` + label. `weight_kg` nullable di katalog.
- **E-41 vs §12.4/L-54:** PIN pbkdf2 di `res.users` (grup `base.group_system`, tidak dimuat ke POS), approver ≠ kasir + grup `group_pos_manager` + company, TTL 300 s, token tidak keluar server (respons `ok/expires_in/max_amount`), terikat config+sesi+uuid order+kasir+nominal, sekali pakai, tukar barang nominal 0 juga butuh persetujuan, hanya tunai (cek non-tunai mendahului), append-only teraudit, offline diblok di JS (`payment_screen_patch.js:110,151`), RPC ada di allowlist.

### G20.3 Test / harness ditambah (belum dijalankan)

- **Odoo** `efn_online_pos_counter/tests/test_online_pos_counter.py`: `test_paid_after_expiry_goes_to_review` dan `test_paid_after_cancel_goes_to_review_then_pos_refund` kini juga memeriksa `event review._efn_payload()["review_reason"]` (regresi mismatch #1).
- **Jest** baru `api-gateway/src/orders/order-events.contract.spec.ts`: fixture = bentuk PERSIS payload Odoo untuk `paid`+counter, `review`+`review_reason` (dua alasan counter, alasan lain → null), `refund_settled` (kind/initiator ikut, `pos:<uuid>`), dan penolakan `provider_refund_ref` bukan `pos:`/nominal pecahan/refund kosong.
- **Smoke tahap `q`** (`run-e2e-smoke.sh`; default `a b c d f e g l p o q`; dokumentasi di `scripts/smoke/README.md`): `online_smoke.py` `counter_channel|checkout_counter|counter_view|counter_cancel`; `odoo_online_smoke.py` `counter_setup|counter_lookup|counter_pay|counter_refund` (+ `payment_mode`/`counter_hash_present` di `describe`). 30 cek `q.*`: payment_modes dari katalog; checkout counter 201 (metode asing 400, replay kode sama, `payment` persis 3 kunci, tanpa QR/pickup_code); kode terenkripsi BFF + hanya digest di Odoo + tidak ada di log; lookup salah `not_found`/benar persis 2 kunci; sync POS → `confirmed`, faktur lunas, pembayaran jurnal kliring, re-sync idempoten (1 event `paid`); BFF `fulfilling` + kode bayar dihapus; lookup ulang `not_found`; kode ambil ≠ kode bayar → serah terima → `completed`; refund CLI → `manual_required/counter_refund_via_pos` belum settled → refund POS → event `refund_settled` → BFF `settled` `pos:*`; bayar-setelah-batal → Odoo `review` (`counter_paid_after_cancel`, tanpa faktur) → BFF bagian `review`, order DB `cancelled`.
- **Smoke `l` langkah 8b'** (`odoo_pos_smoke.py ret_approval`): tanpa persetujuan ditolak, kasir bukan approver, PIN salah, PIN benar (token tidak keluar, TTL 300), sekali pakai antar order, nominal melebihi, 5 salah = `locked` walau PIN benar. PIN acak per run (tidak ada secret di repo); mode dikembalikan ke `cash` di `finally`.
- **UAT** bagian baru **I1–I9** di `docs/testing/UAT_SCENARIOS.md`: bayar di kasir normal/kedaluwarsa/setelah batal → review/refund via POS/kode salah+rate limit+POS offline/batasan metode+gerbang L-55; retur tanpa struk (benar/salah/terkunci/offline/negatif); batas berat; enam entitas (akses produk per PT, pajak per PT, kode, ETL, rotasi).

### G20.4 Sisa risiko / temuan yang tidak diperbaiki (urut keparahan)

1. **Smoke `q` dan `ret_approval` ditulis buta** (tanpa runtime). Asumsi yang paling mungkin meleset: (a) `pos.config.create` menerima `picking_type_id=wh.pos_type_id` di Odoo 19; (b) jurnal kas company GL sudah dipakai metode POS lain (tidak masalah) atau tidak ada (dibuat); (c) pola `field` bash membaca nilai `message`; (d) opsi CLI `--full`; (e) `counter_channel` menunggu siklus sinkron katalog hingga 180 s. Bila `q.odoo_counter_setup` gagal, periksa langkah itu dulu — cek berikutnya bergantung padanya.
2. **Kunci PIN E-41 bersifat jendela-geser, bukan 15 menit sejak kegagalan ke-5** (`efn_pos_return_approval.py:95-111`: hitung kegagalan dengan `create_date >= now-15 menit`). Bila lima kegagalan tersebar 10 menit, kunci berakhir ±5 menit setelah kegagalan ke-5 (L-54: "kunci 15 menit setelah 5 kali"). Keputusan desain/kecil; tidak diubah. Usul: simpan `locked_until` atau hitung dari kegagalan ke-5.
3. **Refund parsial pesanan counter lewat POS belum didukung** (README modul): refund online parsial tetap `requested` dan butuh SOP Finance; UAT I4 menyebutnya sebagai negatif.
4. **Tabrakan digest kode bayar** (`efn_online_order.py:485-486`) → Odoo `invalid_input` → place gagal untuk pelanggan (peluang ≈ n/8,5·10¹¹; BFF tidak membuat ulang kode otomatis walau komentar Odoo menyebutnya). Diterima; satu klik ulang membuat quote/kode baru.
5. **Dokumen basi:** `services/odoo/addons/efn_setup_online/README.md` baris 18-83 (bagian "Kanal `RTL` aktif", user `efn_online_rtl`, secret `odoo_api_key_online_rtl`, W-21 4 kanal per brand) masih menggambarkan kanal G19 (judul atas README sudah memberi catatan G20). Tidak diubah (hak milik builder Odoo A); `docs/agent-reports/*` lain sejarah.
6. `payment.status` tidak dikirim BFF untuk order `counter` (`QrisPaymentView` saja yang punya `status`); frontend memetakannya ke `'unknown'`. Tidak dipakai UI; hanya kosmetik di tipe.
7. **Belum terverifikasi runtime** (semua temuan di atas = baca kode): perilaku `addLineToCurrentOrder` di Odoo 19 (README modul sendiri menandai ini), tour `EfnOnlineCounterPayTour`/`efn_return_approval_tour`, urutan `refund_settled` vs konsumer `order.refund.requested` (BFF menerima status `manual_required` maupun `odoo_done`, jadi aman), kliring 0 setelah sesi POS ditutup (hanya test Odoo).

---

## G21. Konsistensi kontrak §13 + L-61..L-66 (branch `chore/wave-21`), 2026-10-19

> **Tidak ada yang dijalankan.** Sesi QA tanpa shell/Docker: semua verifikasi dengan membaca kode kedua sisi. Perbaikan, test, dan skrip smoke di bawah **belum dieksekusi**; lead yang menjalankannya. Tidak ada commit.

### G21.1 Hasil singkat

Kontrak publikasi kebijakan, akrual, dan `invoice_request` **cocok di semua sisi pada level path/payload/kode respons**. Ditemukan 1 mismatch nyata (email), 1 dokumentasi menyesatkan (nilai 0), 1 celah gerbang deploy (ACCEPT_V1), 3 celah erasure K-36-4, dan **1 celah desain akuntansi** (restore lintas PT di sisi penebus) yang saya laporkan, tidak diubah.

### G21.2 Mismatch ditemukan dan diperbaiki

| # | Sisi pengirim | Sisi penerima | Masalah | Perbaikan |
|---|---|---|---|---|
| 1 | BFF `orders/invoice-request.ts:18` dan FE `superapp-web/lib/invoice-request.ts:42`: `EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/` | Odoo `efn_online_order/models/efn_online_common.py:116` `EMAIL_RE = ^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$` (dipakai `invoice_profile_payload` :144) | Email dengan bagian lokal > 64 karakter atau TLD 1 huruf (`a@b.c`) lolos form dan BFF, lalu ditolak Odoo `invalid_input`. Efek: `efn_online_set_invoice_party` gagal, faktur atas nama diam-diam jatuh ke CS (`invoice_party_failed`, `pending_cs`) padahal input "valid" di UI | Kedua regex disamakan dengan Odoo (`{1,64}` dan `{2,}`). Test BFF `orders/invoice-request.spec.ts` ditambah 2 kasus (`budi@contoh.i`, lokal 65 karakter) di tabel `it.each` negatif. Test FE `g21-invoice.test.ts` tidak perlu diubah (`a@b.id`, `finance@contoh.example` tetap valid) |
| 2 | Odoo `efn_loyalty_accounting/models/efn_loyalty_policy.py:120-125` (help `daily_max_points_member`): "0 = tanpa batas (BFF tetap memberlakukan plafon env)" | BFF `loyalty/loyalty-policy.ts:147-155` `capped()`: plafon > 0 (default = env lama, selalu > 0) menolak nilai 0 → `422 policy_out_of_bounds` | Finance yang mengikuti help mengisi 0, persetujuan lolos, publikasi gagal **final** (outbox `failed`, kebijakan tertahan `approved`). Sama untuk `pos_config_hourly_max`/`company_hourly_max` (tanpa help) | Teks help diganti: isi ≥ 1, 0 ditolak BFF selama plafon > 0. Constraint Odoo tidak ditambah (plafon ada di env BFF; keputusan desain bila mau mirror) |
| 3 | `scripts/lib/deploy-validate.sh` (tidak ada pemeriksaan) | `efn_pii` keyring `EFN_ODOO_PII_ACCEPT_V1` (SEC-148, r13) | Tugas koordinator: production harus menolak `=true`. Compose tidak meneruskan variabel ini dan bukan `EFN_RELEASE_CONFIG_VARS`, jadi jalur `efn-deploy` sudah menolaknya; jalur manual `sudo -E bash deploy.sh` tidak | `efn_validate_config`: nilai harus `true|false`; `production` + `true` → `_efn_err` (tidak bisa di-override `EFN_ALLOW_CONTROL_OFF`). 5 test baru `scripts/host/test-efn-deploy.sh` 146a-146e (production true ditolak, + PII ENABLED/REQUIRED tetap ditolak, false diterima, staging true diterima, bukan boolean ditolak) |
| 4 | `efn_privacy/models/efn_privacy_erasure.py` `PARTNER_CLEAR_FIELDS` (sebelum: tanpa `efn_region_id`, NIK) | ADR-036 K-36-4, `efn_region/models/res_partner.py:6` ("erasure WAJIB mengosongkan") | Erasure member tidak mengosongkan `efn_region_id` (kab/kota domisili plaintext), `l10n_id_nik`, `l10n_id_buyer_document_number` (+ bidx-nya) dan tidak menyentuh antrean `mail.mail` | Tiga field ditambahkan ke `PARTNER_CLEAR_FIELDS` (dilewati bila modul tidak terpasang; menulis `False` pada field `efn_pii` ikut mengosongkan ciphertext + bidx; `efn_name_tokens` dihitung ulang dari nama placeholder). Metode baru `_efn_erase_mail_queue` menghapus `mail.mail` ber-`recipient_ids` partner (sengaja tidak mencari `email_to`: terenkripsi di `efn_pii`). README `efn_region` disesuaikan |

### G21.3 Diverifikasi cocok (baca kode kedua sisi)

1. **Kebijakan poin.** Odoo `efn_loyalty_policy_outbox.py:33,164-173` `PUT /v1/pos/loyalty/policies/{company_code}/{version}` ↔ BFF `pos-loyalty-finance.controller.ts:23` (`ServiceKeyGuard` → `LoyaltyPolicySignatureGuard`). Header: Odoo `signing.py:25,59-65` `X-EFN-Signature: t=<unix>,v1=<hex HMAC-SHA256(key, "<t>."+body)>` ↔ BFF `common/security/webhook-signature.ts:14,53-54` (`x-efn-signature`, `"<t>."`+rawBody, jendela 300 s `loyalty-policy.guard.ts:8`). Body: Odoo `_efn_publish_body` (:367-384, `canonical_json` byte persis yang ditandatangani, `rawBody: true` di `main.ts:39`) ↔ BFF `parseLoyaltyPolicy` (skema ketat `additionalProperties:false`; 8 kunci `params` identik urutan `PARAM_FIELDS` = `POLICY_PARAM_KEYS`; `effective_from` format `...Z` diterima; `point_value_idr` hanya company EFN, selaras Odoo `is_global`). Batas Odoo (earn≥1, min≥1, pct 1..100, max/order≥1) adalah subset batas BFF. **Kode respons:** 200 → `done`+`published`; 409/422/400 → `failed` final + aktivitas; 401/404/408/429/5xx/jaringan → backoff 1 mnt..1 jam sampai 72 jam (Odoo `RETRY_STATUSES` + `BffClient` melempar untuk ≥500) ↔ BFF 401 `invalid_signature`, 503 tanpa kunci, 409 `policy_conflict`, 422 `policy_out_of_bounds`, 200 `stored|duplicate`. Bentuk error `{error:{code}}` dibaca Odoo (`efn_loyalty_policy_outbox.py:183-185`; `BffResponse.data` selalu dict). DEFAULTS Odoo (:64-74) = default env BFF = default plafon (lolos tanpa 422).
2. **Nama kunci.** Odoo `EFN_LOYALTY_POLICY_SIGNING_KEY_FILE` (`signing.py:23`, compose `docker-compose.yml:855`, service `odoo`, secret :859) ↔ BFF `LOYALTY_POLICY_SIGNING_KEY_FILE` (`secrets.loader.ts:31`, compose :1175, secret :1197) ↔ `secrets.list:90` (`random`) ↔ top-level secret :162 ↔ `check-encryption.sh:185-202` (hanya `api-gateway`+`odoo`, path `/run/secrets/loyalty_policy_signing_key`) ↔ test deploy 110f. Min 32 byte dan trim sama di kedua sisi (`signing.py:44-56`, `app-config.ts:1046-1052`).
3. **GET params.** `pos_config.py:151-180` `_efn_get(PARAMS_PATH, {"company_code": <kode PT>})` (nilai "GL" lolos penjaga R2) ↔ `pos-loyalty.controller.ts:31-35` → `paramsFor`; kunci balasan yang dibaca Odoo (`point_value_idr`, `redeem_min_points`, `redeem_max_pct_of_order`, `earn_exclude_point_payment`) ada di `pos-loyalty.service.ts:229-239`. Kebijakan global EFN untuk `point_value_idr` ikut (`pointValueAt`).
4. **Akrual.** Odoo `efn_loyalty_accrual.py:52,265-270` `GET /v1/pos/loyalty/accruals?company_code=&date=` (allowlist `efn_bff_client.py:31-34`) ↔ BFF `accrual()`: 4 kunci nilai (`earned_value_idr`, `earn_reversed_value_idr`, `restored_value_idr`, `expired_value_idr`) integer ≥ 0, `closing_liability_idr` boleh negatif, `redeemed_value_idr_by_issuer` (kunci kode PT kontrak), dua peta L-66 `*_as_issuer_by_redeemer` tanpa PT sendiri (Odoo menolak kunci PT sendiri; BFF tidak pernah membuatnya dan menegakkan Σ konsisten). Field aditif BFF (`point_value_idr`, `forfeited_value_idr`, `restored_value_idr_by_issuer`, …) diabaikan parser Odoo. `date_not_closed` = `422 {error:{code}}` → `error`, `last_error=bff:date_not_closed`, dicoba lagi di run berikut (lookback 3 hari; grace BFF 300 s ≪ 02:00 WIB). Kunci `COMPANY_CODES` BFF = `EFN_BRAND_CODES` Odoo.
5. **Jurnal L-66 → recon 0.** Pada skenario kontrak (earn GL, tukar BCR, restore sebagian) jurnal GL: Dr Beban/Cr Liab, Dr Liab/Cr Utang antar-PT (BCR), kebalikan untuk restore; BCR: Dr Piutang/Cr Liab; saldo kedua PT = `closing_liability_idr` (test `test_cross_company_earn_gl_redeem_bcr_both_reconcile_zero` menghitung 7.500 dan 0). Tanda `sign`: `ledger = -balance(akun liabilitas)`, `diff = odoo − bff`.
6. **Log RECON ↔ Promtail/Loki.** Odoo `_efn_reconcile` (:459-489): logger `odoo.addons.efn_loyalty_accounting.recon`, INFO bila 0 / WARNING bila ≠ 0, `json.dumps(sort_keys=True)` → `{"bff_closing_liability_idr": …, "company_code": …, "date": …, "diff_idr": …, "odoo_liability_idr": …}` (5 kunci, int) ↔ `promtail.yml:116-125` (selector berjangkar penuh, urutan kunci sort_keys dan urutan kontrak, `(INFO|WARNING)`, sub-logger `[a-z0-9_]{1,40}`) ↔ fixture `tests/promtail/fixtures/odoo.txt` + `test_observability_liability_recon.py`. Dikecualikan dari redaksi HP (nominal ≥ Rp6,2 M berawalan 628).
7. **`invoice_request`.** FE `lib/invoice-request.ts` + `app/api/orders/route.ts:63-77` (lima field, dinormalisasi) ↔ BFF `orders.controller.ts:24,38` `CHECKOUT_KEYS` + `parseInvoiceRequest` (NPWP/NIK 16 digit, paspor 5-20 `[A-Z0-9]`, nama 2-100, alamat 5-255) ↔ `orders.service.ts:191` (`INVOICE_COMPANIES=['GL','BCR']`, sisanya `422 invoice_not_available` sebelum place) ↔ `json2-client.ts:367-374` argumen `(channel_code, platform_order_ref, profile, idempotency_key)` ↔ Odoo `efn_online_set_invoice_party(channel_code, platform_order_ref, profile, idempotency_key)` (`efn_online_order.py:1001`; validasi `invoice_profile_payload`: kunci `{buyer_type,tax_id,name,address[,email]}`, paspor/NPWP/NIK sama; kunci idempotensi `online:invoice:<uuid>` lolos `IDEMPOTENCY_KEY_RE`). `invoice_not_available` ada di `ERROR_CODES` Odoo, `ODOO_BUSINESS_CODES` BFF dan dipetakan `isOdooBusiness(err,'invoice_not_available')` → 422; kegagalan lain → anomali + lanjut (`pending_cs`). Hasil RPC `{"state":"set"}` tanpa PII ↔ `parseSetInvoiceParty`. BFF tidak menyimpan profil (`requestSha256` hanya penanda boolean; ada test). Gateway path ke-12: `haproxy.cfg:73-87` (acl `p_allowed` persis), `test-gateway.sh:108-109,189-190` (model/suffix salah ditolak), BFF `ODOO_ONLINE_PATHS.setInvoiceParty` (`odoo-online.types.ts:22`), `RPC_ALLOWLIST` `test_rpc_surface.py:104`.
8. **CI.** `ci.yml:33-38` `EFN_TEST_MODULES` memuat semua modul baru (`efn_setup_org efn_loyalty_accounting efn_l10n_id_coretax_rounding efn_role efn_region`); `odoo-test-pii` (`:323-435`) memasang `EFN_PII_MODULES` (`efn_pii efn_pii_pos efn_pii_hr efn_pii_account`) + `efn_pii_*` lain otomatis (`efn_pii_base_vat`, `efn_pii_l10n_id`) + seluruh `EFN_TEST_MODULES` (jadi `efn_region`, `efn_privacy`, modul lain ikut diuji ber-PII); tiga kunci acak dibuat di job, cek kebocoran di log. Semua 29 `__manifest__.py` di `services/odoo/addons` tercakup (`EFN_TEST_MODULES`, `EFN_PII_MODULES`, penemuan `efn_pii_*`; `efn_setup_security` di `EFN_INSTALL_ONLY_MODULES`). `deploy.sh:443-451` mewajibkan `10-platform-g21.sql` (+ 08, 09). `deploy-validate.sh:198-204` + test 139-146 untuk `EFN_ODOO_PII_REQUIRED`; `check-encryption.sh:206-214` memeriksa render prod-pii.

### G21.4 Test / harness / dokumen ditambah (belum dijalankan)

- **Jest** `orders/invoice-request.spec.ts`: 2 kasus negatif email (TLD 1 huruf; lokal 65 karakter).
- **Odoo** `efn_privacy/tests/test_erasure.py::test_erasure_clears_region_nik_and_mail_queue`: mengisi `efn_region_id` + `l10n_id_nik` (bagian yang modulnya tidak terpasang dilewati) dan `mail.mail` ber-`recipient_ids` member; setelah cron: region/NIK kosong, `mail.mail` member terhapus, `mail.mail` pelanggan lain utuh.
- **Shell** `scripts/host/test-efn-deploy.sh` 146a-146e (ACCEPT_V1).
- **Smoke tahap `r`** (`run-e2e-smoke.sh`, opsional, tidak di default; skrip baru `scripts/smoke/odoo_loyalty_accounting_smoke.py` langkah `policy|accounts|accrual`; dokumentasi di `scripts/smoke/README.md`). 9 cek `r.*`: pembuat tidak bisa menyetujui (empat mata) → penyetuju menyetujui → outbox `done` HTTP 200 + kebijakan `published` → baris `platform.loyalty_policy` (versi + nilai acak persis) → UPDATE ditolak trigger append-only → `pos.config._efn_loyalty_params()` (jalur POS, `GET params?company_code=GL`) mengembalikan nilai baru → akun akrual dev terpasang → cron akrual manual: tidak ada akrual berstatus `error`, baris RECON tertangkap berformat 5 kunci ≥ 1, run kedua tidak menambah jurnal `EFN-LOY-*`; jumlah `mismatch` hanya informasi. Keterbatasan: `odoo shell` tidak menulis ke log container sehingga RECON tidak sampai ke Loki lewat skenario ini (cek manual setelah cron 02:00 nyata).
- **UAT** `docs/testing/UAT_SCENARIOS.md` bagian **J1-J6**: kebijakan poin empat mata + publikasi/konflik/batas/tanda tangan/kunci hilang; akrual & recon lintas PT (nol, selisih, akun kurang, BFF mati, date_not_closed, lock date, dead-man); faktur atas nama (NPWP/NIK/paspor, PB1, efn_pii nonaktif, gagal pasang, erasure); peran & SoD (blok, bawaan SOD-01..03, pengecualian L-65 + negatif + kedaluwarsa, peran admin, jabatan); review akses bulanan; PPN Coretax (Rp1, idempoten, pengaman, akun/lock, di luar cakupan).

### G21.5 Sisa risiko / temuan yang TIDAK diperbaiki (urut keparahan)

1. **Restore poin lintas PT di sisi penebus tidak dijurnal, rekonsiliasi PT penebus akan selisih (desain, L-66 tidak mencakup).** Penukaran lot GL di BCR: POS mendebit liabilitas BCR, akrual BCR mengkredit liabilitas BCR dan mendebit piutang kliring → liabilitas BCR bersih 0 (= closing BFF BCR 0). Bila lalu di BCR terjadi refund POS pembayaran Poin EFN (restore ke lot GL), refund POS mengkredit liabilitas BCR, sedangkan `restored_value_idr` sengaja tidak dijurnal (`efn_loyalty_accrual.py:20,337-398`, README modul baris 139-140 menyebutnya "belum ditangani"). Akibat: saldo liabilitas BCR naik sebesar restore, closing BFF BCR tetap 0 → `diff_idr ≠ 0` (WARNING + alert) pada hari itu dan seterusnya (tidak pulih sendiri), piutang kliring BCR terlalu besar. Test cross-PT hanya menguji restore di sisi penerbit. Usul perbaikan kecil bila lead setuju (mengubah kontrak L-66): BFF sudah mengirim `restored_value_idr_by_issuer` (aditif); Odoo menambah baris `Dr Liabilitas / Cr Piutang Kliring` per penerbit I ≠ PT ini. Saya tidak mengubahnya karena menyentuh perlakuan akuntansi (keputusan Finance/lead). UAT J2 memuat langkah untuk memperlihatkannya.
2. **Erasure K-36-4 belum menyeluruh (laporan, bukan perbaikan).** (a) `res.partner.bank` partner terhapus tidak disentuh (nomor rekening terenkripsi tahap 1b; umumnya milik vendor/karyawan, bukan member; tidak bisa di-unlink bila sudah dipakai pembayaran → butuh desain placeholder unik per akun). (b) Data HR (`hr.employee`/`hr.version`, NIK/HP/alamat pribadi) memang bukan jalur member-hash; K-36-4 mengaitkannya ke retensi HR → belum ada job pembersihan (G22). (c) **Partner pembeli faktur** (`efn_online_invoice_buyer`) bukan partner member (dicari per NPWP/NIK, tidak ber-`efn_member_hash`) → erasure member tidak menjangkaunya; faktur pajak punya retensi 10 tahun, jadi keputusan DPO diperlukan (anonimisasi bidx + pertahankan faktur?). (d) `mail.mail` yang hanya ber-`email_to` tanpa partner (struk POS lewat `email_values`) tidak terjangkau (kolom terenkripsi tidak bisa dicari); `mail.message.email_from`/`incoming_email_to` dan isi chatter dokumen lain yang menyebut partner adalah "residu tetap" menurut README `efn_pii`. (e) View ETL `efn_etl.partner_region` tidak memfilter `efn_erased` — setelah erasure `efn_region_id` kosong sehingga baris hilang dari agregat; perilaku wajar.
3. **Smoke `r` ditulis tanpa runtime.** Asumsi yang paling mungkin meleset: (a) pembuatan `res.users` dengan field grup `group_ids` vs `groups_id` (kode memilih yang ada); (b) `account.account.create` dengan `company_ids` + `code` di bawah `with_company` (Odoo 19), dan akun `21990091` mungkin sudah ada dari `efn_setup_pos` (dipakai ulang); (c) pola `field` bash terhadap nilai boolean; (d) `loyalty_policy` trigger append-only memakai pesan yang saya grep sebagai "blocked" (cek memakai `exception when others`, jadi nama trigger tak relevan); (e) PT lain (PB, BCR) tanpa akun menghasilkan `missing_config` bila ada nilai akrual — bukan `error`, tidak menggagalkan cek. Bila `r.policy_published_signed_put_200` gagal, lihat `outbox_last_error` di detail (mis. `signing_key_missing`: secret belum ter-mount/`gen-secrets` belum jalan).
4. **Plafon BFF nol-tidak-ada untuk beberapa batas** (`LOYALTY_POLICY_CEILING_*` default = env lama): kebijakan Finance hanya bisa MEMPERKETAT dari default dan tidak bisa melonggarkan (mis. `redeem_max_pct` > 50) tanpa mengubah env + redeploy. Itu sesuai §13.1 no. 4 ("env lama menjadi batas keras") — disebut agar UAT/Finance tidak kaget. Bila plafon diturunkan setelah kebijakan tersimpan, BFF jatuh ke env + anomali `policy_out_of_bounds stage=use` (diuji `loyalty-policy.spec.ts`).
5. **`LoyaltyPolicySignatureGuard` tidak punya penghitung percobaan gagal khusus** (saya tidak memeriksa apakah rate limit global menutup endpoint ini). Penyerang pemegang service key bisa mencoba menebak tanda tangan; HMAC-SHA256 membuatnya tidak praktis. Catatan untuk security review, bukan blocker.
6. **Belum terverifikasi runtime** (baca kode saja): perilaku `action_post` pada lock date Odoo 19 (README modul mengakuinya), `mail.mail` `unlink` pada partner ber-`efn_pii` (tidak ada hook khusus; `pm.scan_fetch` hanya membaca), `fields.Datetime` Odoo ke ISO `Z` (BFF menerima kedua bentuk), `efn_role` SOD vs pembuatan user smoke, regex Promtail terhadap format log Odoo 19 produksi (fixture mengikuti format yang diasumsikan builder).

---

## G22. Konsistensi kontrak §14 (v9.0) + L-70..L-81 (branch `chore/wave-22`, working tree), 2026-10-19

> **Tidak ada yang dijalankan.** QA tanpa Node/Python/Docker: semua verifikasi dengan membaca kode kedua sisi. Perbaikan dan test di bawah **belum dieksekusi**; lead menjalankannya di container. Tidak ada commit.

### G22.1 Hasil singkat

Kontrak QR meja cocok pada level path, nama field, kode error, batas, rahasia, dan skema DB. Ditemukan **2 mismatch nyata yang saya perbaiki** (modifier hilang di tampilan kiriman; cetak ulang QR bisa buntu), **1 celah wiring** (flag ETL G21/G22 tidak diteruskan compose), dan beberapa risiko P3 (di bawah). Lima perbaikan lead hari ini (a-e) konsisten; (a), (b), (c), (d) sudah/kini punya test, (e) sudah ter-cover test peran.

### G22.2 Mismatch ditemukan

| # | Pengirim | Penerima | Masalah | Status |
|---|---|---|---|---|
| 1 (P2) | Odoo `efn_pos_bff_order/models/efn_pos_qr_order.py` `efn_qr_submit` hasil `lines` hanya `{product_id,name,qty}` | BFF `fnb/odoo-qr.ts:199-208` (`parsePtavIds` → `[]`, `attributes_text` → `null` bila absen) lalu `fnb/fnb.service.ts:345` `applyResult` **mengganti** `display.lines` dengan baris Odoo | Tampilan kiriman di ponsel (202, `GET /v1/fnb/orders`) kehilangan `attribute_value_ids`/`attributes_text` yang tadinya diisi BFF (`fnb.service.ts:273-278`); L-78 meminta modifier tampil. MockOdooQr (`odoo-qr.ts:461-470`) mengembalikannya sehingga spec BFF hijau menutupi celah ini | **Diperbaiki di Odoo**: result lines + `attribute_value_ids` (urut naik) + `attributes_text` (format `Atribut: nilai; …`, sama `efn_online_order._efn_online_attributes_text`, `None` bila kosong) |
| 2 (P2) | Odoo `pos_config.py:efn_qr_session_open` payload `"reissue": bool(current)` (`current` = sesi Odoo ber-`state='open'` saja) | BFF `fnb-pos.service.ts:264-268` `findOpenSession` → `409 table_session_exists` bila `reissue=false` | Bila Odoo sudah menganggap sesi `expired` (cron `_cron_efn_qr_expire`; `efn_qr_session_hours` < TTL BFF 4 jam) atau event close gagal terkirim, BFF masih punya sesi `open`. Pelayan menekan cetak ulang → JS mengirim `reissue=True` tetapi Odoo menurunkannya jadi `False` karena `current` kosong → 409 lagi → buntu sampai TTL BFF habis | **Diperbaiki**: `"reissue": bool(current) or reissue`. BFF tanpa sesi open mengabaikan flag |
| 3 (P2) | `infra/compose/docker-compose.yml:1588-1640` (service `etl`) hanya meneruskan `ETL_ENABLE_ODOO/GL`, `ETL_AUDIT_*`, `ETL_LOYALTY_RECON_NOT_BEFORE`, `ETL_LOYALTY_POINTS_MODE` | `services/etl/efn_etl/config.py:264-275`: `ETL_LOYALTY_RECON_ONLINE_ENABLED`, `ETL_ENABLE_KDS`, `ETL_KDS_ONLINE_ENABLED` (dan G21 `ETL_PARTNER_REGION_ENABLED`) default `False` | Container ETL read-only tanpa env itu → job KDS dan rekonsiliasi poin online (mart ClickHouse `10-g22-…sql`) **tidak bisa diaktifkan** di compose, hanya lewat env manual. Tidak ada di `.env.example`/`deploy-validate.sh` juga | **Tidak diubah** (menyentuh compose + `deploy.sh`/`deploy-validate.sh` allowlist + `.env.example`, milik devsecops; risiko memecah gerbang CI). Usul: tambah 4 baris `X: ${X:-false}` + dokumentasi `.env.example` + whitelist boolean di `deploy-validate.sh` |
| 4 (P3, dokumen) | `docs/04_SERVICE_CONTRACTS.md` §14.A.4/A.5/A.7 (baris 1073-1074, 1087-1090, 1099-1100, 1105, 1122) | Kode: body buka sesi tanpa `order_uuid` (L-76), `close.orders[]` (L-80), modul `efn_pos_bff_order` + grup `efn_pos_bff_order.group_qr_bff` (L-70), `FNB_MAX_SUBMISSIONS_PER_SESSION` default **10** (L-76), hasil submit memuat modifier (L-78) | Teks badan A.x masih versi lama (20 kiriman, `efn_pos_qr_order.group_qr_service`, `close.order` tunggal, `lines:[{product_id,name,qty}]`); hanya tabel L-xx yang benar. Pembaca baru bisa salah implementasi | Tidak diubah (dokumen kontrak milik lead/architect). Usul: sinkronkan badan §14.A.4/A.5/A.7 dengan L-70/L-76/L-78/L-80 |
| 5 (P3, dokumen) | `efn_audit/README.md:16` menyebut `activity_*` prefix dikecualikan | Kode `efn_audit_rule.py:47-63` kini per nama (item d) | README basi | **Diperbaiki** |

### G22.3 Diverifikasi cocok (baca kode kedua sisi)

1. **Path RPC 13-15.** BFF `fnb/odoo-qr.ts:13-17` = `haproxy.cfg:93-95` (acl `p_allowed` persis; total 15 acl) = `test-gateway.sh:113-121,201` (negatif: model salah, sufiks, slash akhir, `%61`) = `efn_core/tests/test_rpc_surface.py:136-149` `RPC_ALLOWLIST` (3 RPC BFF + `efn_qr_session_open/close` + `efn_qr_claim_send`) = Odoo `efn_pos_qr_order.py` (`efn_qr_menu/submit/status`). Argumen: BFF kirim `config_id, since, cursor, limit` / `config_id, table_session_ref, [order_uuid], submission{idempotency_key,submission_ref,lines}` / `config_id, submission_refs` = signature Odoo; `order_uuid` hanya dikirim bila BFF tahu (E-27: biasanya tidak).
2. **Kode error.** `QR_BUSINESS_CODES` (`odoo-qr.ts:20-28`) = `ERROR_CODES` Odoo (`qr_common.py:15-25`) = 7 kode; format `efn_qr:<kode>` diparse `QR_BUSINESS_RE`. Peta HTTP BFF (`fnb.service.ts:375-379`) = kontrak A.5; FE `lib/fnb.ts:78-97` memuat semua kode respons BFF yang bisa lolos (`table_session_invalid|closed`, `ordering_unavailable`, `session_limit_reached`, `item_unavailable`, `note_invalid`, `captcha_required`, `idempotency_*`, klaim poin).
3. **Batas.** Kiriman/sesi 10 (BFF `app-config.ts:1769-1771` = Odoo `pos_config.py:84` `efn_qr_max_submissions` = test_09 + spec), item 150, baris 30, qty 20, PTAV ≤ 10, catatan 140 + aturan ≥ 8 digit/`@` (BFF `fnb.model.ts:154-161`, FE `lib/fnb.ts:48-68`, Odoo `qr_common.py:91-101` — regex identik). Maks nominal Rp3 juta hanya di Odoo (BFF tidak tahu; `limit_exceeded` → `session_limit_reached`).
4. **Token & header.** `fts1.<22>.<10 digit>.<43>` (`fnb-token.ts:20`) di fragment `#t=` (BFF `fnb-pos.service.ts:241`, regex Odoo `QR_URL_RE` menerima `#t=[A-Za-z0-9_.-]{16,512}`), FE `tableTokenFromHash` → memori → header `x-table-session` (`lib/fnb.ts:20`, `fnb-route.ts:35`, BFF `fnb-token.ts:17` `TABLE_SESSION_HEADER`). Tidak dari path/query (`fnb.controller.ts:11-15`). Regex FE longgar, verifikasi tanda tangan hanya di BFF (disengaja).
5. **Event Odoo→BFF.** Payload `efn_pos_qr_event_outbox._efn_payload` (kunci: `schema_version, idempotency_key, seq, table_session_id, company_code, config_id, event, occurred_at, submission|close{reason,orders[]}`) ⊂ allowlist `parseFnbEvent` (`fnb-pos.service.ts:163`); kunci `fnb:<uuid>:<event>:<seq>` = `EVENT_KEY_RE` (:149); `orders[]` `{order_uuid, eligible_amount, company_code}` ⊂ `parseOrderClose` (:124); alasan `paid|cancelled|closed_by_staff|reissued` = `CLOSE_REASONS`; ≤ 8 KB di kedua sisi (Odoo `MAX_BODY_BYTES`, BFF `FNB_EVENT_BODY_LIMIT`). HMAC: Odoo `t=<unix>,v1=hex(HMAC(secret, "<t>."+raw))` (`sign_body`, :68) = `webhook-signature.ts` (header `x-efn-signature`, replay 300 s); `FnbEventHmacGuard` 503 tanpa secret, tidak ada fallback tanpa tanda tangan di Odoo (`hmac_missing` → retry). Respons: 2xx → done; 400/401/403/404/409/422 final; 429/5xx retry = BFF (`handleEvent` 404/409 final, duplicate/stale/unknown = 200).
6. **Secret.** `odoo_fnb_event_hmac`: compose Odoo `EFN_FNB_EVENT_HMAC_FILE` (docker-compose.yml:864) ↔ BFF `ODOO_FNB_EVENT_HMAC_FILE` (:1206, `secrets.loader.ts:37`) ↔ `secrets.list:94-101` ↔ `check-encryption.sh:205-230` ↔ default Odoo `/run/secrets/odoo_fnb_event_hmac`. `fnb_table_token_key` hanya api-gateway (:1205/:1248). `odoo_api_key_qr_{pb,bcf}` overlay `docker-compose.online-qr-*.yml` ↔ `app-config.ts:1855` ↔ `check-encryption.sh:170-196`. BFF menolak HMAC = service key / = HMAC online (`app-config.ts:1838-1845`).
7. **Buka sesi.** Odoo `pos_config.py:261-268` body `{idempotency_key=fnb_open:<cfg>:<tbl>:<n>, company_code, config_id, table_id, table_label, reissue}` ⊂ `parseOpenSession` (`additionalProperties:false`, `OPEN_KEY_RE` memeriksa cfg/tbl cocok body); respons `{table_session_id, qr_url, expires_at}` 201 = yang dibaca Odoo; 409 `table_session_exists`/422 dipetakan `session_exists`/`qr_disabled`. TTL: BFF `expires_at ≤ opened_at+4 jam` (CHECK DB) ≥ Odoo `min(hours, bff)`.
8. **Skema DB `11-platform-g22.sql` ↔ repositori/model.** Status/close_reason/earn_status/order_state/submission status CHECK = tipe `fnb.model.ts`; `expireDue` mengisi `closed_at` (CHECK `(status='open') = (closed_at IS NULL)`); kunci idempotensi `^[A-Za-z0-9_-]{16,128}$` = `IDEMPOTENCY_KEY_RE`; Odoo `fnb:submit:<uuid>` (41 char) ⊂ regex Odoo `^[A-Za-z0-9_:.-]{8,128}$`.
9. **Online F&B.** `attribute_value_ids` ≤ 10, unik, urut naik: FE→BFF `cart.service.ts:42-55` = `quote.service.ts:70-74,264` = `json2-client`/`placeLines` = Odoo `efn_online_common.cart_lines_payload` (`CART_LINE_KEYS`, ≤ 10, identitas `(pid, ptav)`); `product_no_variant_attribute_value_ids` sumber harga; `attribute_invalid`→`409 cart_has_issues`; combo dikecualikan di `_efn_online_product_domain` (`efn_online_channel.py:443-444`) + test `test_combo_not_published_online`; KDS bridge `_efn_after_confirmed` → tiket (tanpa PII/kode ambil), semua tiket ready → `_efn_set_ready("kds")`, event `preparing` + `kitchen_started_at` = BFF `parseOdooEvent` :210-213; `picked_up.loyalty.eligible_amount` (`event_outbox.py:213-215`) = BFF :214-221; `points_payment{points,value_idr}` BFF `orders.service.ts:391` = Odoo `POINTS_PAYMENT_KEYS`.
10. **Poin & hangus.** `params.inactivity_expiry_months|expiry_notice_days|activity_epoch` hanya EFN: Odoo `GLOBAL_PARAM_FIELDS` + batas 1..60/7..90, BFF `loyalty-policy.ts:132-138` parse (1..120/1..365) lalu `policyViolations` plafon env 6..36/7..60 + epoch ≤ 1 tahun (L-75); literal JSON bertanda tangan identik di `loyalty-policy.spec.ts:279`, README Odoo :86, `test_policy_outbox.py`. Job `loyalty-inactivity.job.ts` memakai `expiryParams()` (kebijakan dulu, env fallback), epoch kosong → heartbeat error; blocker mencakup reservasi, bagian online terbuka (`status` ⊂ enum `online.model.ts:7`), DSAR aktif.
11. **ETL/ClickHouse.** `KDS_TICKET_COLUMNS` (27 kolom) = `fact_kds_ticket` (tanpa `_loaded_at`); `KDS_STATION_COLUMNS` = `dim_kds_station`; kolom yang dibaca ada semuanya di grant `odoo-readonly-grants.sql:258-263` dan di model Odoo (`efn_kds_ticket.py:55-83`); kolom PII (label meja, nomor antrean, catatan) sengaja tidak di-grant. `DAILY_COLUMNS` + `channel` = ALTER `10-g22…sql:135`; baris POS `"pos"` (`loyalty_recon.py:901`), online `"online"`. Grant `etl-access.sql:44-51`, `metabase-access.sql:70-86` (termasuk `dim_outlet` yang dipakai view).
12. **Perbaikan lead (a-e).** (a) `_get_open_order` (`pos_order.py:82-107`): uuid cocok menang; scope internal tanpa uuid cocok = `exact` kosong = order baru; fallback super() tidak pernah memilih order ber-`efn_qr_order_ref` → draft non-QR terbaru, atau kosong. (b) `pos_config.py:313-340`: `current._efn_close(emit=False)` + `current.flush_recordset()` sebelum INSERT sesi baru (indeks unik parsial), relink `(carried | order).sudo().write(...)`. (c) `_efn_qr_check_caller` memakai `bare._()` untuk `denied` dari env tanpa context klien. (d) `efn_audit_rule.py:47-63` daftar nama mixin; (e) `efn_role_data.xml` 6 peran "Sales Manager Online" memuat `stock.group_stock_user` (baris 111, 213, 315, 417, 519, 614), README `efn_setup_role:35`, `test_setup_role.py:36,248`; tidak ada aturan SoD yang menyebut `stock.*` (aman).

### G22.4 Test ditambah / diubah (belum dijalankan)

- **Odoo `efn_pos_bff_order/tests/test_qr_submit.py`**: `test_01` kini mengunci bentuk `lines` dengan modifier (L-78); `test_01d_result_lines_carry_sorted_modifiers_and_null_text_without_any`; `test_12b_get_open_order_uuid_wins_and_qr_never_merged` (uuid cocok → order QR; uuid pelayan; uuid asing → pelayan; scope internal → kosong; tersisa hanya order QR → kosong); `test_12c_waiter_sync_does_not_overwrite_qr_order`.
- **Odoo `tests/test_qr_session.py`**: `test_reissue_flag_forwarded_when_odoo_session_already_expired` (perbaikan #2 + kasus tanpa reissue), `test_access_denied_message_is_clean_of_client_context` (item c: pesan "Akses QR ditolak." sama untuk bukan-grup, config tidak ada, dengan context `lang` asing).
- **Odoo `efn_audit/tests/test_audit_eligible.py`** (baru, terdaftar di `tests/__init__.py`): field bisnis `activity_epoch` eligible, semua nama mixin tidak, field `activity_*` nyata di `res.partner` tidak bocor (item d).
- **Jest `fnb/fnb.spec.ts`**: `bentuk respons Odoo E-27 apa adanya` — submit tanpa `order_uuid` + modifier, versi lama tanpa modifier tetap diterima, PTAV ganda/0/>10 ditolak, status `absent` seq 0, state rusak/bukan array ditolak (jalur gagal parser; menutup celah mock yang selalu mengembalikan `order_uuid`).
- **Dokumen**: `docs/testing/UAT_SCENARIOS.md` bagian **K1-K9** (alur QR utama, mode auto, order pelayan tak tertimpa, tolak pesanan, negatif token/sesi/cetak-ulang, batas & input, idempotensi/jaringan/HMAC/event ganda/Odoo mati, **POS offline (K7)**, refund clawback, privasi/akses).

### G22.5 Sisa risiko / temuan yang TIDAK diperbaiki (urut keparahan)

1. **(P2) Flag ETL G21/G22 tidak diteruskan compose** (mismatch #3). Sampai diperbaiki, mart KDS dan rekonsiliasi poin online tidak menyala di deploy compose; tidak mengganggu transaksi.
2. **(P3) `order_uuid` per kiriman tidak pernah terisi di produksi.** Odoo tidak mengirimnya di hasil submit (`result` tidak memuat `order_uuid`, meski `record.order_uuid` tersimpan, `efn_pos_qr_order.py:440-447`), di `efn_qr_status`, maupun di event `submission_status` (kontrak §14.A.5 #14 memang tidak memintanya; `MockOdooQr` mengisinya, `odoo-qr.ts:472`). Akibat: `fnb_submission.order_uuid/order_state/eligible_amount/earn_status/points_earned` tetap NULL/`none` (loop `fnb-pos.service.ts:360,382-388` tidak pernah cocok). Earn sesi tetap benar karena `applyOrder` memakai `orders[]` L-80 dan `fnb_table_session.earn_status` terisi. Keputusan: tambah `order_uuid` ke hasil submit/status Odoo (perubahan kontrak kecil, BFF sudah siap) atau hapus kolom/ekspektasi itu. Spec BFF `fnb.spec.ts:344-376` lulus karena mock; jangan percaya sebagai bukti per-kiriman.
3. **(P3) `item_unavailable` tidak membawa `product_ids` asli** (`fnb.service.ts:376` `FnbErrors.itemUnavailable([])`), padahal teks FE "Hapus menu yang ditandai" (`lib/fnb.ts:87`) — tidak ada yang ditandai; pelanggan harus menebak. Usul: BFF sebelum/ sesudah galat membandingkan baris dengan overlay `available=false`, atau Odoo menyertakan id di pesan `efn_qr:item_unavailable` (ubah kontrak).
4. **(P3) Daftar kiriman di FE membuang modifier**: `lib/fnb.ts:302-312` `parseSubmissionLines` hanya `{name, qty}`; BFF sudah mengirim `attributes_text` (L-78 — "Gula: …" tampil di keranjang tetapi hilang setelah dikirim).
5. **(P3) TTL token vs umur sesi Odoo.** Token fts1 berlaku sampai `FNB_TABLE_SESSION_TTL_SECONDS` (default 4 jam) sedangkan Odoo menolak kiriman setelah `efn_qr_session_hours` (default 4). Bila Ops menurunkan jam di Odoo, tamu memindai QR "valid" lalu kiriman ditolak `session_closed`. Odoo hanya memendekkan `expires_at` lokal (`pos_config.py:307-311`), tidak memberi tahu BFF. Samakan nilai (`FNB_TABLE_SESSION_TTL_SECONDS` = `efn_qr_session_hours` × 3600) atau kirim `session_hours` di body buka sesi.
6. **(P3) Job hangus: head-of-line blocking** (`loyalty-inactivity.job.ts:259-271`). Notice `queued` yang terblokir (reservasi/bagian online/DSAR) tetap `due` dan diambil lagi; bila jumlahnya ≥ `loyaltyExpiryBatchSize` dan semuanya terblokir, batch berikutnya (member lain yang sudah jatuh tempo) tidak pernah dijangkau sampai blocker lepas. Aman (fail-closed, tidak menghanguskan), hanya menunda.
7. **(P3) Batas `orders[]` event**: BFF ≤ 50 dan 8 KB, Odoo tanpa batas jumlah order sesi (tiap entri ≈ 110 byte → ~70 muat di 8 KB). Dengan ≤ 10 kiriman + order pelayan hasil gabung ini tidak tercapai; bila terlampaui Odoo `payload_too_large` (final) / BFF 400 → poin sesi itu hilang diam-diam (hanya alert `EFN_QR_EVENT_FAILED`).
8. **(P3) `pos_member_attached` tidak pernah dikirim Odoo** (selalu default `false`): BFF akan meng-earn juga untuk order yang member POS-nya sudah di-earn kasir → mengandalkan idempotensi kunci `pos_earn:<order_uuid>` (replay bila member sama; `conflict` + anomali `qr_pos_member_conflict` bila beda). Perilaku sesuai kontrak D-22-5, disebut agar UAT K1 memeriksanya.
9. **Belum terverifikasi runtime**: tanda tangan `_get_open_order(self, order)` dan perilaku `super()` di `pos_restaurant` Odoo 19 (override memanggil `super()._get_open_order(order)` dengan dict yang sama, butuh `session_id`/`table_id`); urutan `order.lines` untuk `test_01d` (diasumsikan = urutan input; bila urut lain, ubah assertion jadi per `product_id`); `sync_from_ui` untuk order pelayan kedua di meja yang sama (mungkin menimpa draft pelayan pertama — standar `pos_restaurant`, diasumsikan bukan order QR); perilaku `bus.bus` dan KDS notifikasi; seluruh ETL/ClickHouse (DDL view dengan alias `c_*`, `FINAL` pada `ReplacingMergeTree`) hanya dibaca, tidak dijalankan.

---

## G41 — QA ESB Core -> datamart (`services/esb-extract`, DDL, overlay, docs/04 §25, runbook)

Metode: baca kode saja (tanpa shell/runtime); test baru ditulis hati-hati dan **belum dijalankan** (lead menjalankan pytest + ruff di container).

### G41.1 Mismatch kontrak
| # | Sisi A | Sisi B | Status |
|---|---|---|---|
| 1 | `config.py:257-272` membaca `ESB_MAX_RETRIES`, `ESB_BACKFILL_MAX_CALLS`, `ESB_SALES_READY_HOUR_WIB`, `ESB_CORRECTION_DAYS`, `ESB_GAP_FILL_DAYS`, `ESB_BACKFILL_INCLUDE_INACTIVE`, `ESB_ENTITIES`, `ESB_NON_STORE_BRANCHES`, `ESB_HTTP_TIMEOUT_SECONDS`, `ESB_MAX_PAGES`, `ESB_COMPANY_<BRAND>`; docs/04 §25.2 dan runbook menyebutnya sebagai env operator | `docker-compose.esb.yml` (sebelum perbaikan) tidak meneruskannya: nilai di `.env` tidak berpengaruh (mis. mengaktifkan `stock`, `ESB_NON_STORE_BRANCHES` untuk `is_store`, budget backfill) | **DIPERBAIKI** (overlay meneruskan dengan default = default kode); dijaga `test_contract.py` |
| 2 | Runbook §5 langkah 2 menyuruh `--entity flow` | `__main__.py:40` `--entity` hanya entitas harian (`sales, fin_sold, payment, production, delivery, transfer, stock`); `flow` = nama view, `master`/`branch` tidak bisa dipilih | **DIPERBAIKI** (runbook) |
| 3 | Runbook §3/§6 menyebut run `incremental` | `records.py` / DDL `extract_run.mode` = `run` atau `backfill` | **DIPERBAIKI** (runbook) |
| 4 | Runbook §7 "403 -> entitas/cabang dilewati + dicatat" | `http.py:329-330` 403 = `EsbHttpError` -> slice error (`partial`), tanpa checkpoint, **dicoba ulang tiap siklus** (bukan dilewati) | Hanya dilaporkan; perilaku aman, redaksi runbook kurang tepat |
| 5 | `.env.example:192-204` hanya memuat 9 variabel ESB | 11 variabel lain kini diteruskan overlay | Dilaporkan; boleh ditambah komentar di `.env.example` (tidak diubah) |

Cocok (diverifikasi): nama secret `esb_core_password_<brand>` kode/overlay/`secrets.list`/check-encryption; `clickhouse_password_esb` (base `docker-compose.yml:144,1497` + overlay + secrets.list); kolom `MART_TABLES` vs DDL (`test_ddl`); kunci join view (`entity`, `scope_key`) vs `SliceTask.scope` (`'*'` per brand, `branchID` per toko); `substring(..., 6)` untuk `NAME:`; grant `esb_writer` = 17 tabel DDL; view §25.6 = 5 view DDL; user `ESB_USER_*` overlay = runbook = §25.1.

### G41.2 Perbaikan
- `infra/compose/docker-compose.esb.yml`: tambah passthrough `ESB_NON_STORE_BRANCHES, ESB_ENTITIES, ESB_MAX_RETRIES, ESB_BACKFILL_MAX_CALLS, ESB_SALES_READY_HOUR_WIB, ESB_CORRECTION_DAYS, ESB_GAP_FILL_DAYS, ESB_BACKFILL_INCLUDE_INACTIVE, ESB_HTTP_TIMEOUT_SECONDS, ESB_MAX_PAGES, ESB_COMPANY_BACHA` (default sama dengan `config.py`). `ESB_COMPANY_{PB,WTZ,IMD,CUP}` sengaja tidak ditambah (jarang diubah, §25.2). Bentuk `docker compose config` tidak berubah strukturnya, tetapi **jalankan ulang** `docker compose config` + `check-encryption.sh`.
- `docs/runbooks/esb-extract.md`: §3, §5 (entitas), §6 (mode `run`).

### G41.3 Test ditambah (belum dijalankan)
`tests/test_runner.py`: `test_checkpoint_not_written_when_mart_insert_fails`, `test_run_cycle_store_failure_marks_run_error_and_records_it`, `test_budget_exhausted_mid_run_keeps_finished_slices_only`, `test_budget_exhausted_inside_slice_writes_nothing`, `test_company_override_per_branch_for_bacha`, `test_backfill_window_closing_mid_run_across_midnight_stops_scheduling`.
`tests/test_contract.py` (baru): env config.py -> overlay, env config.py -> docs/04, nama secret per brand lintas kode/overlay/secrets.list, resolusi alias `NAME:` di view flow.
Sudah tertutup sebelumnya (tidak diulang): 401 dua kali (`test_http`), paging berhenti/guard halaman (`test_paging`), jendela lewat tengah malam (`test_planning`), redaksi bersarang (`test_normalize`).

### G41.4 Sisa risiko (tidak diperbaiki; keputusan desain)
1. **(P2) Tidak ada backoff antar-siklus setelah `auth_failed`.** `run` mencoba login ulang tiap `ESB_INTERVAL_SECONDS` dengan password salah -> risiko akun brand terkunci di ESB (produksi, dipakai kasir). Runbook hanya menyuruh operator mengeluarkan brand manual. Usul: setelah `auth_failed` brand ditangguhkan di memori sampai restart/N jam.
2. **(P2) Kegagalan tulis ClickHouse di tengah slice menghentikan seluruh siklus brand** (`runner.py:181`, `write_slice` di luar try -> `run_cycle` catch -> `status=error`); slice lain brand itu tidak dicoba siklus tsb. Aman (checkpoint belum ditulis) tetapi satu tabel yang ditolak (mis. grant) menghentikan semuanya; `raw_doc` yatim tertulis. Diterima bila disengaja.
3. **(P3) Baris yatim berkunci sama menimpa versi lama** (batasan terdokumentasi §25.4): hari tampak kurang sampai ditarik ulang.
4. **(P3)** `metabase_reader` tidak punya grant `fact_esb_stock_snapshot` dan `map_esb_odoo` (terdokumentasi untuk stock; G43 harus menambah grant map).
5. Belum terverifikasi: perilaku `/inventory/stock-list`, `production_result` selalu `ck_prod` untuk semua brand (asumsi L-116), alur `Retry-After` pada ESB nyata, jalankan `test_contract` di container (path `parents[3]`).

### G41.5 UAT ringkas untuk G42 (dashboard) dan G43 (migrasi) yang bergantung pada mart
Given/When/Then (data sintetis; Metabase memakai `metabase_reader`):
1. **Penjualan harian tepat.** Given slice sales PB toko 7 H-1 selesai (checkpoint ada). When dashboard "Penjualan" difilter tanggal H-1 + toko 7. Then `gross = Σ total`, `net = gross - discount`, `trx_count` = jumlah `salesNum` unik; baris PACKAGE_COMPONENT (total 0) tidak menggandakan omzet.
2. **Tarik ulang menghapus void.** Given H-1 sudah tampil 100 transaksi; di ESB 1 transaksi di-void. When siklus berikutnya (H-1/H-7) menarik ulang. Then view menampilkan 99; angka lama tidak tersisa (versi slice baru).
3. **Slice gagal tidak tampil.** Given tarik ulang H-1 gagal di halaman 10 (budget `limit`). Then dashboard tetap menampilkan versi lama (bukan data separuh) dan `v_esb_data_quality` `last_run_not_ok` muncul.
4. **Toko tanpa penjualan.** Given toko aktif tanpa transaksi H-1. Then `store_no_sales_h1` memuatnya (bukan error); toko CK/gudang tidak muncul karena `is_store=0`.
5. **Alias belakangan.** Given transfer berisi nama produk tanpa kode (`NAME:FIN ROTI X`). When master berikutnya mempelajari alias itu. Then `v_esb_flow_daily` memetakan ke kode produk pada data lama tanpa tarik ulang; `alias_unmapped` turun.
6. **PT Bacha.** Given override `bacha:12=BCR`. Then penjualan cabang 12 muncul di PT BCR, cabang lain BCF; total brand tidak berubah.
7. **Row policy per PT (G42).** Given user analis terbatas PT PB. When query `v_esb_*`. Then hanya baris PB (saat ini `metabase_reader` melihat semua PT: uji harus gagal sampai policy G42 dibuat — jadikan gerbang rilis).
8. **Tanpa PII.** When memeriksa `efn_esb.raw_doc` dan view. Then tidak ada nama kasir/pelanggan/telepon/email; supplier hanya kode/nama/status.
9. **Brand tanpa secret.** Given `ESB_BRANDS=pb,wtz` tetapi secret `wtz` = `disabled`. Then service gagal start (fail closed), bukan hanya PB berjalan diam-diam.
10. **Rekonsiliasi migrasi (G43).** Given cutover brand X. When total penjualan bulan lalu per PT di mart ESB dibandingkan Odoo hasil impor. Then selisih = 0 setelah `backfill --force` bulan itu; selisih > 0 ditandai dan `map_esb_odoo` memuat setiap `esb_key` yang diimpor tepat sekali (impor ulang tidak menggandakan).
11. **Kill switch.** Given `ESB_ENABLED=false`. Then tidak ada baris `extract_run` baru, tidak ada login ke ESB, dashboard tetap menampilkan data terakhir dengan `data_age_hours` naik.
12. **Jendela backfill.** Given backfill dijalankan pukul 12:00 WIB. Then `status=window_closed`, nol panggilan; dijalankan 23:59 dan berlanjut melewati 00:00 tetap berjalan; berhenti menjadwalkan pukul 05:00 dan lanjut dari checkpoint malam berikutnya.

---

## G42 — QA Dashboard Insight (`services/insight-web`, `efn_insight`, ClickHouse `efn_insight`, docs/04 §26, ADR-041)

Metode: **baca kode saja** (tanpa shell/runtime). Semua test baru/diubah **belum dijalankan** — lead menjalankan Vitest (+ lint, typecheck), pytest + ruff `services/esb-extract`, dan test Odoo `efn_insight` di container. Yang sudah diverifikasi lead (Odoo 26 test, Vitest 168, E2E 31/31, row policy + 36 query di CH 24.8, compose/encryption/lint) tidak diulang.

### G42.1 Hasil cek kontrak (sisi A ↔ sisi B)
Cocok (diverifikasi baris per baris):
- Token `eit1`: `insight_token.py:28-69,111-156` ↔ `token.ts:23-92` ↔ `claims.ts` ↔ docs/04 §26.3 — 11 kunci, urut abjad, `ensure_ascii`, kunci = byte ASCII hex, TTL 60, skew +30, jti 32 hex, `name` ≤ 64 code point, `codes`/`cids` urut naik; regex format, `timingSafeEqual`, vektor uji identik di `helpers.ts` dan `test_insight_token.py`.
- Cookie `eis1`: `session.ts:25-89` ↔ §26.5 (7 kunci urut abjad, 28800 dtk, atribut, kunci terpisah, prefix HMAC `eis1.` memisahkan domain dari `eit1.`).
- Scope: `scope.ts:17-32` ↔ `clickhouse-init.sh:281-285` ↔ text2sql — `sha256(key|cids|codes)`, kunci ASCII; `company_code != ''` ada.
- Secret: `secrets.list:68-71` ↔ `docker-compose.yml:162-165,1678-1688` ↔ `config.ts:76-78` ↔ `clickhouse-init.sh:216,274` ↔ `check-encryption.sh:336-346`; Odoo `controllers/main.py:30-32` ↔ compose `:919-925`.
- View/kolom: `insight-access.sql:76-111` ↔ `queries.ts` (tiap `FROM efn_insight.v_esb_*` ada; kolom yang dipakai ada di view) ↔ `insight-web-queries.md` §2; 36 query = 36 konstanta.
- Dev: `EFN_INSIGHT_URL` `docker-compose.dev.yml:99` (`:9443`) ↔ `EFN_INSIGHT_ODOO_URL` `:203` (`:9443`) — Origin C1 cocok.

Ketidakcocokan (semua dokumen tertinggal dari kode terbaru lead; **sudah diperbaiki di dokumen**):
| # | Sisi A (dokumen) | Sisi B (kode) | Status |
|---|---|---|---|
| 1 | docs/04 §26.1 baris 2653, §26.5: cookie `eis`; logout `eis=` | `session.ts:25` `__Host-eis` (C2) | DIPERBAIKI (dokumen) |
| 2 | §26.4: `INSIGHT_ENABLED` ≠ true → **503** "belum diaktifkan"; runbook & compose komentar sama | `sso-handler.ts:82` **404** teks polos (diuji `sso-handler.test.ts:96`); 503 hanya untuk konfigurasi (`:83`) | DIPERBAIKI (dokumen, runbook, komentar compose `docker-compose.yml:1672`) |
| 3 | §26.4 tidak memuat cek Origin; urutan 404→503→429→Origin→body | `sso-handler.ts:82-108`, `origin-guard.ts:9-18` (Origin WAJIB, tanpa Origin ditolak, jti tidak terbakar) | DIPERBAIKI (§26.4, runbook §6, README) |
| 4 | §26.7: definer `GRANT SELECT ON efn_dwh.*`; 7 view; `v_esb_data_quality` dari `efn_dwh.v_esb_data_quality` dengan peta statis; USING tanpa `company_code != ''` | `insight-access.sql:49-70` grant per objek (21 objek), 8 view incl. `v_esb_sales_branch_daily`, sumber `v_esb_data_quality_pt` dinamis, `clickhouse-init.sh:285` | DIPERBAIKI (§26.7 + catatan "yang menang = insight-web-queries.md") |
| 5 | §26.7 profil tanpa konkurensi | `insight-access.sql:24,37` `max_concurrent_queries_for_user = 24 MAX 24`; `clickhouse.ts:32` semaphore `INSIGHT_CH_MAX_CONCURRENCY` (default 12) tidak ada di §26.10/README/runbook | DIPERBAIKI (§26.7, §26.10, README, runbook §1/§6) |
| 6 | §26.8: kanonik `pay_mdr`, (draf) `sales_menu_daily`, `dq_status_board` | `queries.ts`: tidak ada ketiganya (`pay_by_method` melayani MDR) | DIPERBAIKI (catatan rekonsiliasi §26.8); `queries.ts:10` dan `insight-web-queries.md:8` masih berkata "belum dijalankan" → diperbarui |
| 7 | §26.10 tidak memuat `INSIGHT_CH_MAX_CONCURRENCY`, `EFN_ALLOW_PLAINTEXT`, `NODE_TLS_REJECT_UNAUTHORIZED`; `EFN_INSIGHT_ODOO_URL` hanya "tautan kembali" | `config.ts:6-10,51-67`, `semaphore.ts`, `sso-handler.ts:98` (juga dasar cek Origin) | DIPERBAIKI |
| 8 | §26.11: middleware `secure-headers@file`, "network ke ClickHouse saja" | compose `:1706,1690`: `secure-headers-insight` + `ratelimit-insight`, network `insight-ingress`+`insight-data`; router form Odoo `odoo-insight-open` `:958-963` memakai `secure-headers-insight-sso` | DIPERBAIKI |
| 9 | runbook §1/§8: "ketiga kunci berbeda tidak dicek otomatis" | `config.ts:93-114` + `instrumentation-node.ts` menolak start bila kembar; `clickhouse-init.sh:279` menolak bila = text2sql | DIPERBAIKI (runbook) |
| 10 | §26.2: "`INSIGHT_ENABLED` ≠ `true`" (README Odoo: `1/true/yes/on`) | `controllers/main.py:47-48` (`.lower()` ∈ {1,true,yes,on}) vs insight-web `config.ts:70` (`=== 'true'` persis) — nilai `1`/`yes`/`on`/`True`: Odoo **membuat token** tetapi insight-web menolak (404) | **BUG KECIL DIPERBAIKI** (kode): Odoo kini `== "true"` persis; README Odoo + test |
| 11 | `semaphore.ts` (awal) env 1..64, profil ClickHouse 24 | `INSIGHT_CH_MAX_CONCURRENCY` 25..64 pasti memicu galat 202 | **DIPERBAIKI** (kode): batas atas 24; dijaga `test_concurrency_limit_matches_insight_web_semaphore` |
| 12 | §26.13 hanya "usulan" C1–C4 | semuanya terimplementasi | DIPERBAIKI (status implementasi) |

Tidak diubah (dilaporkan): **ADR-041** tidak memuat keputusan C1 (Origin wajib), `strict-origin` + varian Traefik, rewrite gerbang sesi, semaphore konkurensi — perlu satu paragraf di ADR (milik architect). Docs/04 §26.8 menyebut hanya path; `insight-web.md` (UI) tidak diperiksa ulang terhadap nama cookie.

### G42.2 Perbaikan kode kecil
- `services/odoo/addons/efn_insight/controllers/main.py:47-50`: `_enabled()` kini hanya `os.environ.get("INSIGHT_ENABLED") == "true"` (sebelumnya 1/true/yes/on, case-insensitive) — satu saklar, dua sisi identik. README modul Odoo diselaraskan.
- `services/insight-web/lib/semaphore.ts:60-68`: `concurrencyFromEnv` rentang 1..24 (sebelumnya 1..64) agar tak bisa melampaui `max_concurrent_queries_for_user`.
- Komentar saja: `docker-compose.yml:1672` (404, bukan 503), `lib/queries.ts:10`.

### G42.3 Test ditambah (belum dijalankan)
Vitest (`services/insight-web/tests`):
- `session-logout.test.ts` (baru): `__Host-eis` + atribut; payload `eis1` byte-exact (7 kunci urut, UTF-8) dan sig `eis1.`; kunci ekstra/kurang, `exp-iat` > 28800, `iat` > now+30 (batas 30 dtk sah), klaim tak valid (codes/cids/roles/sub/name), **pemisahan domain** (sig berawalan `eit1.`, kunci SSO, token SSO bukan cookie), format cacat; `readCookie` (cookie tossing: `eis`, `x__Host-eis`); `POST /logout` (303 + hapus cookie; `Sec-Fetch-Site` cross-site/same-site → 403 tanpa menghapus).
- `sso-handler-flow.test.ts` (baru): Origin salah / tanpa Origin **tidak membakar jti**; token sig-sah-klaim-buruk tidak menyimpan jti; kedaluwarsa/sig salah tidak menyimpan jti; nama non-ASCII utuh sampai cookie; `all_brands` cids kosong; 401 generik (header aman, tanpa alasan, tautan Odoo); content-type dengan charset diterima / text/plain & multipart ditolak; badan kosong / field bukan `t` / `t` ganda → 400; batas tepat 4096 vs 4097; urutan 404 > 503 (limiter tak terpakai); kunci sesi null → 503 tanpa cookie.
- `proxy-gate.test.ts` (baru): `proxy()` dengan `NextRequest` — cookie sah → next + CSP; tanpa cookie / cookie rusak → **rewrite** `/sesi-berakhir` tanpa `Location` (+ hapus cookie bila rusak); kill switch (`false`, `1`, `True`, `yes`, kosong) → `/belum-aktif`; kunci hilang → `/belum-aktif`; halaman publik tanpa sesi; cookie bernama `eis` ditolak. **Risiko:** satu-satunya test yang memakai `next/server` + env/file sementara + reset `globalThis.__insightRuntime`; bila resolusi `next/server` di Vitest gagal, jalankan terpisah dan laporkan.
- `semaphore.test.ts` (diubah): rentang env 1..24 (25 → 12, pecahan, negatif), beban 20 tugas tak melewati batas 3, batas tak valid ditolak saat konstruksi.
pytest (`services/esb-extract/tests/test_insight_access.py`, statis): profil CREATE ≡ ALTER; `max_concurrent_queries_for_user` = `MAX` dan batas env semaphore ≤ batas profil, default < batas; nama secret lintas `secrets.list`/compose/`config.ts`/Odoo/clickhouse-init; port dev Odoo = port insight; varian Traefik `strict-origin` vs `no-referrer` + router yang memakainya + controller Odoo (satu `strict-origin`, default `no-referrer`, kill switch `== "true"`); docs/04 §26 memuat hardening terbaru (`__Host-eis`, `company_code != ''`, konkurensi, strict-origin, varian sso).
Odoo (`efn_insight/tests`): `test_insight_sso.py` — kill switch untuk `1/yes/on/True/TRUE/""` + `Referrer-Policy: no-referrer` pada halaman info, tanpa token/audit saat mati, `all_brands` + company arsip (codes tetap 6, cids tanpa arsip), company non-aktif-di-pemilih ikut / company asing tidak, POST ke rute tidak menerbitkan token. `test_insight_token.py` — `verify_token` menolak kunci ekstra/kurang, aud/iss/v salah, umur 0/61, iat masa depan, codes asing, sig berpadding/`+`/`/`/kosong.

### G42.4 Sisa risiko (tidak diperbaiki; keputusan desain / belum terverifikasi)
1. **(P2) Rate limit `/sso` per IP dihitung sebelum cek Origin** (`sso-handler.ts:88` vs `:98`): permintaan lintas situs berulang dari satu IP (mis. NAT kantor) menghabiskan jatah 10/menit dan memblokir login sah di IP itu (429). Dampak rendah (hanya subnet staf), tetapi pertimbangkan menghitung hanya permintaan ber-Origin sah.
2. **(P2) Semaphore per proses, satu user ClickHouse bersama:** dengan > 1 replika (L-124) atau klien lain di user `efn_insight`, batas efektif melewati 24 → galat 202; antrean 200/tunggu 20 dtk memunculkan kartu "timeout" bila banyak staf serentak. Perlu uji beban nyata (belum ada).
3. **(P2) Cookie sesi stateless:** pencabutan akses user (Lina) berlaku ≤ 8 jam; pencabutan darurat memutus **semua** sesi (rotasi `insight_session_key`). Sesuai L-119/Q-INS5; harus diketahui pengguna bisnis (UAT L14).
4. **(P3) Replika tunggal + restart = cache jti hilang** (C5). **(P3) `Sec-Fetch-Site` di `isOriginAllowed` diabaikan** (`void secFetchSite`) — parameter mati; aman, tetapi bersihkan agar tidak menyesatkan.
5. **(P3) `X-Forwarded-For` paling kanan sebagai IP** (`rate-limit.ts:45-50`) bergantung Traefik menambahkan entri; di dev (tanpa Traefik di depan) semua klien = `unknown` bila header tidak ada — satu ember bersama 10/menit.
6. **(P3) Ekspor CSV:** `max_result_rows` 200000 + `result_overflow_mode=throw` → CSV yang melebihi gagal 500 tanpa pesan ramah (UAT L24 hanya menguji kasus normal). Q-INS7 (siapa boleh ekspor) belum dijawab; saat ini semua pengguna berhak.
7. **Belum terverifikasi runtime (oleh saya):** seluruh test baru; CSP + ECharts di browser nyata; perilaku `Origin` di Safari/Firefox untuk form POST lintas-origin dengan `strict-origin` (hanya Chromium diuji E2E); rewrite `proxy.ts` pada `next start` standalone di belakang sidecar TLS; `max_concurrent_queries_for_user` di ClickHouse versi lain.
8. Production tetap ditahan (L-125): security review, DPO (nama di cookie, log audit Q-INS3), Q-INS1/4/7.

### G42.5 UAT
28 skenario Given/When/Then untuk pengguna bisnis (SSO, akses per PT, tiap halaman dashboard, kill switch, jaringan, privasi) di `docs/testing/UAT_SCENARIOS.md` bagian **L** (L1-L28), termasuk kasus negatif: tautan dipakai ulang/kedaluwarsa, tanpa grup/PT, Origin salah, cookie palsu, paksa PT lewat URL, ClickHouse mati, rate limit.

---

## G44 — QA Supermarket forecast/replenishment/assortment (worktree `efn-platform-g44`, branch `feat/wave-44-supermarket-forecast`, 18 Nov 2026)

Metode: **baca kode saja** (tanpa shell/runtime; tidak ada Python/Odoo/Docker yang bisa dijalankan dari sesi ini). Semua test baru/diubah di bawah **belum dijalankan**; lead menjalankan pytest forecast-worker/etl dan test Odoo `efn_demand_forecast`, `efn_replenishment`, `efn_core`, `efn_audit` di container. Tidak ada commit.

### G44.1 Diverifikasi cocok (sisi A ↔ sisi B)
- **5 metode RPC**: `forecast-worker/efn_forecast/odoo.py:28-34` (model + metode) ↔ `efn_forecast_calendar.py:64`, `efn_forecast_run.py:98,154`, `efn_forecast_daily.py:54`, `efn_demand_forecast.py:287` ↔ `haproxy.cfg:123-127` (`p_forecast`) ↔ `efn_core/tests/test_rpc_surface.py:371-392` ↔ docs/04 §28.2. Backend `odoo_forecast` maxconn 1 + `h_cl_fc_big` 512 KiB ↔ `contract.py:19` / `forecast_api.py:29`.
- **Skema/batas**: kunci payload (open/ingest/minmax/close/calendar), enum model/kind, 500 baris, 512 KB, horizon 1..120, `chunk_total` 1..10000, UUIDv4, regex company identik (kini dijaga test paritas baru, G44.3). Worker lebih ketat daripada Odoo (model_version `[a-z0-9]` vs `[A-Za-z0-9]`) = aman.
- **Urutan & idempotensi**: `runner.py:259-268` open → ingest berurutan → close; chunk duplikat = sukses (`odoo.py:200-212` ↔ `efn_forecast_run.py:260-269`); close dua kali no-op; `incomplete_run` ditangani (run_close failed, kini diuji); `_classify` 422/403 tidak di-retry, 429/5xx/jaringan di-retry (`odoo.py:157-170`); `run_close(failed)` didahului `run_open` bila belum (chunk_total 1).
- **Env compose ↔ config**: `docker-compose.forecast.yml:58-86` ↔ `config.py` (FORECAST_ENABLED/DAILY_CRON/TZ/HORIZON/CHUNK_SIZE, ODOO_RPC_*, `ODOO_API_KEY_FORECAST_SMD_FILE`, CH_*); secret `odoo_api_key_forecast_smd` / `tls_forecast_worker_*` / `clickhouse_password_forecast` ↔ `secrets.list:62,164,248-249` ↔ `clickhouse-init.sh:220` ↔ `gen-pki.sh:82` (CN `forecast-worker`) ↔ `check-encryption.sh:391-440`. Cocok.
- **ClickHouse**: kolom `forecast-access.sql:63-85` ↔ `source.py:17-33,35-66` (diuji `test_access_sql.py`) ↔ DDL sumber (`01-schema`, `04-fase1-marts` ALTER `line_kind`/`is_refund`, `11-g32-waste-yield`, `15-g44-forecast.sql`): semua kolom yang dibaca ada; `dim_calendar.warehouse_id`=0 (semua gudang) ↔ `source.py:120`.
- **ETL ↔ Odoo**: `etl/forecast.py:67-185` ↔ model Odoo (`run_id` Char UUID, `date`, `yhat/p10/p90/sigma/model`, `warehouse_id` NULL→0) ↔ `odoo-readonly-grants.sql:402-424` (grant kolom, kondisional). `dim_forecast_run.run_id` = id Odoo (join akurasi benar).
- **Kode SMD** konsisten di: `efn_core/models/res_company.py:17,29`, BFF `loyalty.service.ts:17` + `metrics.service.ts:159`, superapp-web `brands.ts:17,51`, ETL `entities.py:16`, text2sql `scope.py:18` + `semantic_layer.yaml`, Odoo `efn_data_ask.py:43`, PG `19-platform-g44.sql:43` (11 CHECK + issuer; tabel QR/reservasi sengaja tetap PB/BCF; semua 11 tabel dari 02..17 tercakup), `deploy-validate.sh:104`, ClickHouse `08-g20-entities.sql:48-49`. SMD sengaja TIDAK di insight-web (`claims.ts`), `efn_insight` (ESB_CODES), `esb-extract` (bukan brand ESB). Tidak ada daftar PT lain yang ketinggalan (grep `'FARM'` seluruh repo).
- **Payday 7+7**: `calendar.py:96-101` (worker) ↔ `efn_forecast_season.py:124-127` (Odoo) — definisi identik (N hari terakhir + M hari pertama, 0..15, Februari/kabisat).

### G44.2 Ketidakcocokan / temuan (urut keparahan)
| # | Sisi A | Sisi B | Dampak | Status |
|---|---|---|---|---|
| 1 | `efn_setup_supermarket/data/stock_config_data.xml:39-46` gudang DC `SMDDC` **tanpa** `efn_is_dc` (field milik `efn_assortment`; manifest sengaja tidak depend, `__manifest__.py:9-10`) | docs/04 §28.5 "warehouse DC ditandai `efn_is_dc`"; `efn_assortment/models/stock.py:67` (penegakan), `efn_assortment.py:269` (laporan harian), `stock_purchase.py:88` (qty orderpoint DC) hanya jalan untuk `efn_is_dc=True`; tidak ada seed/skrip/runbook yang mencentang (grep) | Di instalasi demo SMD: penegakan assortment, laporan pelanggaran, dan qty orderpoint DC → RFQ **diam-diam tidak aktif** sampai dicentang manual | **TIDAK DIPERBAIKI** (lintas modul/tahap A↔B). Usul: seed G48 atau post_init_hook `efn_assortment` men-set `efn_is_dc=True` untuk xmlid `efn_setup_supermarket.warehouse_smd_dc` bila ada |
| 2 | `efn_replenishment.py:445-449` jendela kebutuhan `[today, …)`, `today = local_today(now)`; cron antrean jalan ±23:40 (worker 23:30) | Seri forecast mulai `as_of+1` (`pipeline.py:336`): hari ini tak punya baris → diisi **rata-rata seri** (`sum_series`, baris 69-80) padahal hari itu sudah habis dan stok live sudah mengurangi penjualannya | Kebutuhan outlet ±1 hari permintaan **terlalu tinggi** (over-order) pada pola produksi; test memakai jam 08:00 WIB sehingga tidak terlihat | **TIDAK DIPERBAIKI** (semantik desain ADR-046 §3.4). Usul: mulai jendela di tanggal forecast pertama bila run ditutup setelah jam tutup toko |
| 3 | `stock_purchase.py:100` DC: `schedule._efn_runs_on(today)` memakai tanggal **kalkulasi** (malam sebelum) | Jadwal supplier = hari kunjungan/order (mis. Senin/Kamis) | Supplier ber-hari Senin dihitung hanya pada kalkulasi Senin malam (setelah hari order); kalkulasi Minggu malam dilewati → qty baru tersedia untuk kunjungan **berikutnya** | **TIDAK DIPERBAIKI** (perlu keputusan semantik "hari order = besok?"; Q-SM11) |
| 4 | `contract.py:162` `service_level` ∈ (0,1) | `efn_demand_forecast.py:383` ∈ [0,5; 1) | 0,3 lolos validasi lokal lalu ditolak per baris; config worker membatasi 0,5..0,999 sehingga tak terjadi lewat env | Dicatat (rendah); diuji di Odoo |
| 5 | Worker `code.upper()` untuk `warehouse_code` (`source.py:100`) | Odoo mencari `stock.warehouse.code` persis (`efn_forecast_run.py:292`) | Gudang berkode huruf kecil → `unknown_warehouse` pada semua baris; run tetap "ok" tanpa alarm | Dicatat (rendah; kode gudang EFN huruf besar) |
| 6 | `docker-compose.forecast.yml:55` `--company SMD`, secrets hanya `smd` | Worker mendukung PT lain via env | Menambah PT = ubah compose + `check-encryption.sh:399` (regex `(smd)`) + `secrets.list` | Dicatat (sengaja) |
| 7 | `calendar.py:105` nama dikompres spasi | Odoo `strip()` saja (`efn_forecast_calendar.py:80`) | Aman; kunci `(date,kind,name)` identik | Dicatat |
| 8 | `efn_audit/tests/test_audit_access.py:176` mengharapkan **satu** event `access_grant` (`base.group_multi_company`) | Odoo menautkan `env.user` (OdooBot) ke company baru; di instalasi minimal (1 company) OdooBot ikut mendapat grup → event kedua | Gagal hanya di instalasi minimal, lulus di run penuh. **Bukan G44**: kode/test `efn_audit` tak disentuh G44; `efn_assortment` men-depend `efn_audit` sehingga test-nya terpasang di DB minimal | **DIPERBAIKI (test; hipotesis dari baca kode, belum dijalankan)**: company awal + `postcommit.clear()` sebelum company B (`test_audit_access.py:149-157`). Bila hipotesis salah, perubahan tak berbahaya — revert bila lead ingin |

### G44.3 Perbaikan & test yang ditambah (semua belum dijalankan)
**pytest forecast-worker**
- `tests/test_odoo_contract_parity.py` (BARU, 6 test, statis): batas (500/512 KB/120/10000/schema), enum model/kind/run, kunci payload open/ingest/minmax/close/calendar, metrik, kode error yang diandalkan worker, regex model_version/company/UUID, **allowlist gateway `haproxy.cfg` = METHODS worker** (5 path).
- `tests/test_runner.py`: `test_incomplete_run_on_close_then_run_closed_failed`, `test_every_chunk_sent_once_in_order_and_within_contract_limits`.
**Odoo `efn_demand_forecast`** (`tests/test_forecast_api.py`): `test_payload_size_limit_512kb`, `test_run_open_validation_and_conflict`, `test_close_metrics_validation`, `test_minmax_chunk_idempotent_and_incomplete_close`.
**Odoo `efn_replenishment`** (`tests/test_replenishment.py`): `test_next_delivery_skips_past_cutoff_and_respects_weekday_mask`, `test_line_edit_locked_after_cutoff`, `test_reject_scope_and_no_supply_document`.
**ETL** `tests/test_jobs.py`: parametrize + `SMD`. **Odoo `efn_audit`**: perbaikan test #8.

Cakupan perilaku kunci yang **sudah ada** (dibaca): shelf-life cap (`test_shelf_life_cap`, `…_from_dc_lot`), rack cap + pack round turun (`test_pack_round_and_rack_cap`, `test_round_pack_never_exceeds_cap`), cut-off expired (`test_expired_after_cutoff_not_sent`, `test_cron_expires_unapproved`), SoD (`test_approve_scope_and_sod`), lima kode alasan assortment + override + non-overridable (`test_reason_codes`, `test_override_by_manager_except_protected`), payday 7+7/kabisat/param/fallback (worker + Odoo), kalender/Lebaran/override manual/lib nonaktif, chunk idempoten (Odoo + klien). **Belum ada**: `efn_min_receipt_days` (butuh G45), orderpoint DC dengan jadwal supplier (hari bukan kunjungan, lead `supplierinfo`), dan uji jam 23:40 (temuan #2/#3).

### G44.4 UAT
17 skenario di `docs/testing/UAT_SCENARIOS.md` bagian **M** (M1–M17), termasuk kasus negatif (chunk hilang/ulang, PT lain 403, baris ditolak, forecast mati, lewat cut-off, SoD, override alkohol/non-halal ditolak, saklar penegakan fail-closed). Catatan merge: branch G43 bisa menambah bagian UAT sendiri; rekonsiliasi huruf bagian saat merge.

### G44.5 Sisa risiko
1. Temuan #1–#3 = risiko fungsional terbesar; tidak tertangkap test karena semua test memakai jam siang dan menandai DC manual.
2. Seluruh test baru + perbaikan `efn_audit` **belum dijalankan** (hipotesis #8 dari pembacaan source Odoo core; verifikasi dengan menjalankan `efn_audit` di DB minimal).
3. Test paritas memakai regex pada source (rapuh terhadap `ruff format` pada blok `LINE_KEYS`/`RUN_OPEN`); bila gagal karena format, sesuaikan anchor, bukan nilai.
4. Run `ok` dengan seluruh baris `rejected` tidak menghasilkan alarm (hanya metrik log worker).
5. Worker belum diuji terhadap Odoo/gateway nyata (bentuk body galat json2 Odoo 19 diasumsikan `{name, message}`); retry 503 pada maxconn 1 + antrean 10 s belum diuji beban.

## G50 — QA lintas service kampanye supermarket (worktree `efn-platform-g50`, branch `feat/wave-50-supermarket-campaign`, 20 Nov 2026)

Metode: **baca kode saja** (tanpa shell/runtime). Semua perubahan dan test baru di bawah **belum dijalankan**; lead menjalankan Jest BFF, Vitest superapp-web, pytest `segment-worker` dan `etl`. Tidak ada commit. `docs/04` tidak disentuh.

### G50.1 Ketidakcocokan kontrak (urut keparahan)
| # | Sev | Sisi A | Sisi B | Dampak | Status |
|---|---|---|---|---|---|
| 1 | **Tinggi** | `segment-worker/efn_segment/contract.py:119-134` `metrics` = `{as_of: str, lookback_days, product_count, top_products: [..], reason_counts: {..}}` | Odoo `efn_campaign.py:693-698` + `campaign_api.py:32,61` `metrics` hanya `{kunci ^[a-z][a-z0-9_]{0,39}$: angka}` (<= 20); `as_of`/list/dict -> `UserError efn_campaign:invalid_input` (422) | SETIAP `proposal_upsert` dari worker ditolak -> tidak ada kampanye segmen yang pernah muncul di Odoo; demo "visitor -> voucher" macet di langkah 4. Test kedua sisi hijau karena fixture masing-masing berbeda | **TIDAK DIPERBAIKI** (desain: pilih sisi). Saran: worker meratakan metrik (`member_count` sudah ada; `product_count`, `lookback_days`, `reason_market`, `reason_habit`), `as_of` -> `generated_at` (RFC3339 UTC), `top_products` -> `summary` teks (id produk <= 5 digit lolos `PII_RE`). Ditangkap test `xfail(strict)` baru (G50.3) |
| 2 | **Tinggi** | Odoo `efn_campaign.py:575-594` `_efn_outbox_payload`: `title`/`body` datar, `voucher:{mode,codes\|code\|cards,expiration_date,quota}`, `valid_from/valid_to` = `YYYY-MM-DD`, `voucher_program_ref` = integer, `voucher: null` + `voucher_mode` default `anonymous_code` bila tanpa program (`:160`, `_efn_check_ready:469-472`) | BFF `odoo-campaign.ts` (sebelum perbaikan) `parseCampaignPayload`: wajib `message{title,body}`, `voucher_codes`/`public_code`, tanggal ISO `...T..Z`, `voucher_program_ref` string `^[A-Za-z0-9_.:-]{1,64}$` | SEMUA baris outbox nyata ditolak BFF (`message`/`valid_from`/`voucher_program_ref`/`voucher_codes`), tidak di-ack -> 5 percobaan -> `failed`. Fixture BFF (`campaign-fixtures.ts`) memakai bentuk BFF sendiri, jadi 1093 test hijau tanpa menangkapnya | **DIPERBAIKI** di BFF (G50.2 #A) + 3 test |
| 3 | Sedang | Odoo `_efn_check_ready:465` mengizinkan kampanye `kind=segment` + `public_code` **tanpa** `segment_run_id` | BFF `parseCampaignPayload` melempar `segment_required` (tidak di-ack) dan `dispatcher.targets()` (`:120-145`) mengembalikan `[]` untuk segmen tanpa run | Kampanye kode publik tanpa segmen disetujui di Odoo tetapi tidak pernah terkirim (gagal setelah 5 percobaan + aktivitas), atau bila dilonggarkan tidak punya target | **TIDAK DIPERBAIKI** (keputusan produk: apa audiens kode publik tanpa segmen? usul: wajibkan segmen/produk wishlist di Odoo atau hapus pengecualian `:465`) |
| 4 | **Tinggi** (fungsi) | superapp-web `app/api/me/price-alerts/route.ts:45` (lama) `DELETE /v1/me/price-alerts` + body `{channel_code, product_id}`; `lib/engage.ts:14` mengasumsikannya | BFF `wishlist.controller.ts:66-70` hanya `DELETE /v1/me/price-alerts/:id` (UUID dari `GET`, `wishlist.service.ts:205`) | Hapus pengingat harga selalu 404 dari BFF, route menelan 404 (`r.status !== 404`) -> UI mengira berhasil, alert tidak pernah terhapus (kuota 50 habis diam-diam) | **DIPERBAIKI** (route mencari `id` lewat GET lalu DELETE per id) |
| 5 | Sedang | superapp-web `lib/tracking.ts:114-129` `parseFeatures` membaca `features.notify_channels` / `features.email|wa_unofficial|push` agar kanal email/WA bisa dipilih | BFF `behavior.controller.ts:59-93` `GET /v1/config` hanya `tracking, wishlist, restock_alert, price_alert, campaign_inbox` | Kanal email/WA di notify-me tidak pernah bisa dipilih (selalu `false`), walau `EMAIL_ENABLED`/`WA_UNOFFICIAL_ENABLED` aktif | **DIPERBAIKI** (BFF mengirim `features.notify_channels{push,email,wa_unofficial}`; **architect: tambahkan ke docs/04 §28.6**) |
| 6 | Rendah | superapp-web `lib/engage.ts:207-224` kode `wishlist_limit`, `alert_limit`, `price_not_lower` | BFF `wishlist.dto.ts:86-92` `wishlist_full`, `alert_limit_reached`, `invalid_target_price`, `channel_unavailable` | Pesan galat khusus tidak pernah muncul (jatuh ke pesan generik) | **DIPERBAIKI** (`mapEngageCode` + 1 test) |
| 7 | Sedang | ETL `behavior.py:57,213-252` + CH `16-g50-behavior.sql` + `v_seg_subject_product_daily` + segmen `viewed_not_bought`/`wishlist_idle` mengandalkan event server `purchase` | BFF tidak menerbitkan `purchase` (grep `src/`), CHECK PG `20-platform-g50a.sql:93-95` menolaknya, DTO klien hanya `checkout_start` | `purchases`/`purchasers` selalu 0; pembeli tanpa `checkout_start` tetap masuk segmen "dilihat tidak dibeli" -> voucher ke orang yang sudah beli; funnel pembelian kosong | **TIDAK DIPERBAIKI** (desain: BFF terbitkan `purchase` pada `order.paid` + tambah ke CHECK, atau hapus dari mart). Ditandai test `xfail(strict)` |
| 8 | Sedang | BFF `behavior-writer.consumer.ts` menulis event dari antrean tanpa memeriksa `behavior.erased_subject` | `behavior-privacy.ts:48-75` erasure menghapus staging lalu mencatat `erased_subject` | Batch yang masih di antrean `behavior.event` saat erasure menulis ulang event ke staging -> ETL membawanya ke ClickHouse (hanya tertutup jendela recheck 24 jam) -> pelanggaran hak hapus (UU PDP) | **DIPERBAIKI** (writer menolak batch dengan `received_at <= erased_at`) + 1 test |
| 9 | Sedang | BFF `GET /v1/me/inbox` (`campaign-inbox.controller.ts:78-88`) + `features.campaign_inbox` | superapp-web: tidak ada klien/route/layar inbox (grep `inbox` = 0) | Voucher kampanye hanya bisa dilihat lewat API; demo "visitor -> voucher -> beli" tidak bisa ditunjukkan di UI (kanal email/WA tidak membawa kode, sengaja) | **TIDAK DIPERBAIKI** (fitur frontend belum dibangun; usul tugas G50c) |
| 10 | Sedang | Odoo `_efn_outbox_payload` tidak mengirim `voucher_value_idr` | BFF `campaign-dispatcher.consumer.ts:152-155` cap anggaran butuh `voucher_value_idr` (log peringatan, cap tidak ditegakkan) | `CAMPAIGN_BUDGET_CAP_ENABLED=true` tidak berefek untuk kampanye nyata (anggaran Odoo tidak ditegakkan BFF) | **TIDAK DIPERBAIKI** (Odoo perlu mengirim nilai voucher dari reward program, atau BFF menghitung dari `budget_idr/jumlah kode`) |
| 11 | Rendah | superapp-web `engage.ts:262,270-273` 404 = `feature_unavailable` / fitur mati | BFF juga 404 untuk produk/outlet tidak dikenal (`wishlist.service.ts:81,152`) | Gagal tambah wishlist/notify-me untuk produk yang baru dihapus dari katalog tampil "fitur tidak tersedia" dan UI bisa menyembunyikan fitur | **TIDAK DIPERBAIKI** (usul: BFF 404 dengan kode `not_found` vs `feature_unavailable`) |
| 12 | Rendah | BFF `common/amqp/queues.ts:92-99` `mainQueueArguments` tanpa `x-delivery-limit` untuk 7 queue G50 | `rabbitmq-entrypoint.sh:131-137` `queue_defs_limited ... 10` | Hanya masalah bila `assertQueueWithDlq` (non-pasif) dipakai pada queue G50: `PRECONDITION_FAILED`. Saat ini hanya passive `checkQueue` dipakai | Dicatat |
| 13 | Rendah | `campaign-sync.service.ts:134-138` `ON CONFLICT (code_hash) DO NOTHING` + `member_voucher.code_hash UNIQUE` (SQL `:337`) | kode publik yang sama dipakai kampanye/revisi lain | Baris voucher tidak dibuat -> `publicVoucherId` null -> inbox tanpa kode | Dicatat (Odoo membatasi satu kampanye aktif per program, risiko hanya pada revisi) |
| 14 | Rendah | `segment-worker/config.py:233` pesan "buat user teknis efn_campaign_<kode>" | compose/runbook memakai `efn_segment_<pt>` | Operator membuat user salah | Dicatat |
| 15 | Rendah | Runbook `supermarket-campaign-channels.md:20,93` "ETL perilaku terblokir, skema belum cocok" | `20-platform-g50a.sql` sudah direvisi (`company_code`, `ingested_at`, grant kolom) dan ETL cocok (test paritas baru) | Dokumen usang | Dicatat (devsecops/lead) |
| 16 | Rendah | Panjang kode segmen: BFF `odoo-campaign.ts` `{1,39}`, PG `{1,39}`, Odoo `campaign_api.py:31` `{1,47}` | worker `SEGMENT_CODES` <= 17 karakter | Aman sekarang; beda batas bila kode baru > 40 | Dicatat |

### G50.2 Diverifikasi cocok (sisi A <-> sisi B)
- **Klaim/ack**: BFF `ODOO_CAMPAIGN_PATHS` + body `{limit}`/`{ids}` <-> Odoo `efn_campaign_claim(limit)` / `efn_campaign_ack(ids)` (`:747,775`); respons `{items:[{id, approval_rev, payload}]}`/`{ok, acked, already_done, rejected}`; kunci idempotensi `campaign:<PT>:<id>:<rev>` (Odoo `_efn_idempotency_key:558`) = `campaignKey()` BFF = CHECK PG `platform.campaign.campaign_key`; limit 20 = `MAX_CLAIM`/`MAX_ACK`/`CLAIM_LIMIT`; claim tidak di-retry, ack di-retry (idempoten); kode galat `efn_campaign:<kode>` 422 (bisnis) vs 403 (auth) dipetakan `classify()`; allowlist gateway path #30-#32 (`haproxy.cfg:126-140`).
- **Segmen -> BFF**: kunci body `{schema_version, run_id, segment_code, subject_keys<=5000, chunk_no}` = `ingest()` KEYS; UUIDv4 worker (ketat) vs BFF `[1-8]` (longgar); dedup+urut deterministik + `body_sha256` = `duplicate_chunk` 200 / `chunk_conflict` 409; worker memperlakukan 200 dan 409 `duplicate_chunk|duplicate` sebagai sukses, `chunk_conflict` bukan (4xx tidak diulang); path mTLS CN `segment-worker` = `SEGMENT_TLS_CLIENT_ALLOWED_CN`, port 9466 = `SEGMENT_INTERNAL_PORT` = `SEGMENT_BFF_URL` compose.
- **Perilaku**: DTO klien (10 kunci event, batas 50/32 KB, `query` hanya `search`, `channel` web|app) <-> `tracking.ts` `buildEvent`/`sanitizeEvents`; kode galat `tracking_consent_required` 403, 429 + `Retry-After`, `disabled:true` <-> `tracking-client.ts sendBody`; `event_raw` kolom + CHECK <-> ETL `transform_behavior_event` (kanal `server` tidak pernah ditulis BFF) <-> grant kolom init SQL <-> DDL ClickHouse `fact_visitor_event` (`EVENT_COLUMNS` = DDL, diuji) <-> view `v_seg_*` <-> `source.py VIEW_COLUMNS` <-> `segment-access.sql` (kolom sama, diuji); `subject_erasure` (id, subject_key, request_id, created_at) = ETL `SQL_BEHAVIOR_ERASURES`/`SQL_DSAR_SUBJECT_KEYS`.
- **Template email/WA**: `promo_generic{title,body,valid_to,voucher_code?}`, `restock_available{product_name,outlet_name}`, `price_drop{product_name,new_price_idr}` identik di `email-templates.ts`, `wa-unofficial.contract.ts`, `services/wa-unofficial/src/contract.ts`+`templates.ts`; pemanggil `campaign-dispatcher` (tanpa `voucher_code`, kategori `promo`) dan `alert-dispatcher` (`notify_restock`/`price_alert`, source `restock`/`price_alert`) sesuai pasangan template-sumber-kategori.
- **Queue**: 7 nama queue di kode (`behavior-message.ts`, `campaign-message.ts`, `alert-message.ts`, `email.constants.ts`, `wa-unofficial.contract.ts`, `queues.ts G50_QUEUES`) = `rabbitmq-entrypoint.sh` (regex izin + `queue_defs_limited`); user `efn-wa-unofficial` (read `notify.wa_unofficial`, write `amq.default`) <-> `WA_PUBLISHERS`/`WA_RESULT_PUBLISHERS`; `efn-bff` read tanpa `notify.wa_unofficial`.
- **Env/secret**: `ODOO_API_KEY_CAMPAIGN_SMD` (BFF) <-> `odoo_api_key_campaign_smd`; segment-worker `ODOO_API_KEY_CAMPAIGN_SMD_FILE` -> `odoo_api_key_segment_smd` (sengaja terpisah); `CAMPAIGN_COMPANIES`, `SEGMENT_*`, `CONSENT_TRACKING_*`, flag G50 (`.env.example` <-> compose <-> `app-config.ts`); semua env wa-unofficial compose <-> `config.ts` (proxy URL sengaja tidak diset).

### G50.3 Perbaikan dan test (semua belum dijalankan)
**Perbaikan kode**
- **#A** `api-gateway/src/campaign/odoo-campaign.ts`: `normalizeOdooCampaignPayload()` dipanggil di awal `parseCampaignPayload` (title/body -> `message`; `voucher{}` -> `voucher_codes`/`public_code`; `voucher:null` -> mode null; tanggal `YYYY-MM-DD` -> ISO UTC awal/akhir hari WIB; `voucher_program_ref` int -> `loyalty.program:<id>`; `voucher.cards` (partner_id) dibuang). Bentuk lama lolos tidak berubah.
- **#B** `superapp-web/app/api/me/price-alerts/route.ts` DELETE: cari `id` lewat GET lalu `DELETE /v1/me/price-alerts/:id`.
- **#C** `api-gateway/src/behavior/behavior.controller.ts`: `features.notify_channels`.
- **#D** `superapp-web/lib/engage.ts` `mapEngageCode`: alias kode BFF.
- **#E** `api-gateway/src/behavior/behavior-writer.consumer.ts`: tolak batch dari subjek yang di-erase sesudah `received_at`.

**Test ditambah/diubah**
- Jest `odoo-campaign.spec.ts`: 3 test (payload nyata Odoo diterima+dinormalisasi; public_code/member_bound tanpa kartu partner/voucher null; periode terbalik/PT lain/judul kosong tetap ditolak).
- Jest `behavior-writer.consumer.spec.ts`: erasure saat batch di antrean. `behavior.controller.spec.ts`: bentuk `features` kini memuat `notify_channels` (assert `toEqual` lama DIUBAH) + flag email/WA. `campaign-dispatcher.consumer.spec.ts`: redelivery (campaign_delivery sudah ada), consent dievaluasi tiap halaman target.
- Vitest `tests/g50-engage.test.ts`: alias kode BFF.
- pytest baru `services/segment-worker/tests/test_odoo_contract_parity.py` (5 test statis; 1 `xfail(strict)` = temuan #1, akan gagal sebagai XPASS saat diperbaiki -> hapus marker).
- pytest baru `services/etl/tests/test_behavior_pg_ddl_parity.py` (4 test; 1 `xfail(strict)` = temuan #7).

**Jalur negatif yang sudah tercakup test lama (dibaca)**: voucher habis, cap anggaran/frekuensi, kampanye kedaluwarsa, flag mati, duplicate, publisher asing (dispatcher); PT lain/kanal asing/rentang tanggal/kode tanpa mode (parse klaim); circuit/transport/claim tidak diulang (`Json2OdooCampaignClient`). **Belum diuji dan tidak ditambah** (butuh Odoo/DB nyata): consent dicabut persis di antara dua halaman, erasure bersamaan dengan fan-out, `proposal_upsert` paralel (advisory lock), ack baris yang sudah `failed`.

### G50.4 Perlu keputusan lead/architect
1. Temuan #1: sisi mana yang berubah (usul: worker, karena Odoo README sudah mendokumentasikan metrik numerik) dan nilai `summary`.
2. Temuan #7: siapa menerbitkan event `purchase` (BFF pada `order.paid`?) atau hapus dari mart/segmen.
3. Temuan #3 dan #10: audiens kampanye kode publik tanpa segmen; sumber `voucher_value_idr`.
4. Temuan #9: tugas frontend inbox/voucher agar demo layak.
5. Architect: catat `features.notify_channels` di docs/04 §28.6 dan bentuk payload outbox nyata (§28.7: `title`/`body`, `voucher{}`, tanggal tanpa jam) agar kedua sisi punya satu rujukan; saat ini §28.7 tidak merinci keduanya, sumber kebingungan temuan #2.

### G50.5 UAT
`docs/testing/UAT_SCENARIOS.md` bagian **N** (N1 visitor -> voucher -> beli, N1b WA, N2-N10 negatif, P1-P5 notify-me/price alert), lengkap dengan tabel flag dev, data uji SMD, dan daftar blocker B1-B4.

### G50.6 Sisa risiko
1. Semua perubahan **belum dijalankan** (tanpa runtime). Risiko kompilasi/regresi terbesar: `normalizeOdooCampaignPayload` (Jest `odoo-campaign.spec.ts`, `campaign-sync.service.spec.ts`, `g50-config.spec.ts`), `behavior.controller.spec.ts` (`testConfig` dengan `WA_UNOFFICIAL_ENABLED=true` diasumsikan tidak memicu validasi tambahan), dan route price-alerts (tipe predicate `.find`).
2. Tanpa test E2E Odoo -> BFF -> worker; tiga kontrak lintas-service (#1, #2, #7) lolos suite hijau masing-masing karena fixture dibuat per sisi. Usul: satu test kontrak bersama (JSON contoh `outbox_payload.json` dibaca Odoo test dan Jest).
3. `valid_to` Odoo berupa tanggal; normalisasi memakai akhir hari WIB (UTC+7). Bila Marketing berharap zona lain, ubah di satu fungsi.
4. Retensi, consent teks, dan DPO tetap menunggu keputusan DPO (ADR-047 Proposed); production OFF.
---

## G45 — QA Supermarket near-expiry/markdown/FEFO POS (`efn_expiry_monitor`, `efn_setup_supermarket` tahap B; branch `feat/wave-45-supermarket-expiry`, 19 Nov 2026)

Metode: **baca kode saja** (tanpa runtime). Test baru/diubah di bawah **belum dijalankan**; lead menjalankan test Odoo `efn_expiry_monitor` (+ `efn_replenishment`, `efn_setup_supermarket`) di container. Tidak ada commit. Yang sudah dilaporkan lead hijau (Odoo 1080, forecast-worker, deploy 399/0, shellcheck) tidak diulang.

### G45.1 Konsistensi kontrak (cocok)
- Nama field kategori: docs/04 §28.5a (`efn_near_expiry_days`, `efn_markdown_days`, `efn_min_receipt_days`) = `product_category.py:18-34` = `product_category_expiry_data.xml` = `efn_setup_supermarket/migrations/19.0.2.0.0/post-migrate.py:22` = `efn_replenishment` (hasil grep, tidak ada nama lain).
- `efn.lot.markdown`: kontrak (`lot_id`, `warehouse_id`, `price` XOR `discount_pct`, `date_from/to`, `qty_cap`, `state`, `notify_members`) = `efn_lot_markdown.py:49-84`; `notify_members` hanya menyimpan, `notify_state='pending'` (`:155`, `:168`; label "Menunggu outbox (G46)") — G46 belum ada outbox, sesuai kontrak §28.5a/§28.7. `efn.expiry.alert` (lot × outlet × qty × sisa hari × status) = `efn_expiry_alert.py:39-61`.
- Flag `efn_expiry_monitor.enabled`: §28.10 default `false` = `efn_expiry_monitor_data.xml:6-9` = `common.py:40-42` (hanya persis `true`/`1`, fail closed); README setup tahap B `ir_config_parameter_demo.xml:8` sengaja tidak menyalakan; `test_setup_supermarket.py:256` menguncinya.
- Tahap B: grup `efn_expiry_monitor.group_markdown_manager` / `group_expiry_override` dipakai `res_users_demo.xml:83,94` = `common.py:24-25`; `depends` manifest termasuk `efn_expiry_monitor`; `efn_setup_supermarket` demo SMD01..05 (kapabilitas/deny/closed) cocok dengan skenario UAT N13.
- `efn_replenishment._efn_min_remaining_days` (`efn_replenishment.py:563-571`) memakai `categ._efn_effective_days` bila ada (helper induk yang sama dengan `stock_picking.py:82`, `efn_expiry_alert._efn_stage`), fallback field mentah, lalu 0; dikunci `test_setup_supermarket.py:245-247`.

Tidak ada mismatch nama/payload di sisi kontrak. Kontrak hanya **kurang detail** (diperbaiki di dokumen): parameter tambahan, dependensi `efn_pos_refund_origin`, perilaku cap, override sisa umur, pewarisan induk.

### G45.2 Temuan (urut keparahan)
| # | Lokasi | Temuan | Status |
|---|---|---|---|
| 1 | `pos_order.py:84-95` (sebelum perbaikan; cabang `qty < 0` di `_efn_markdown_settle`) | Retur baris markdown **tidak pernah memulihkan `qty_sold`** dari UI: cabang refund hanya jalan bila baris retur membawa `efn_markdown_ref`, padahal field itu `copy=False` dan JS hanya mengisinya saat pindai (`pos_store_patch.js:319`); baris retur CE hanya membawa `refunded_orderline_id`. Akibat: kuota markdown terpakai permanen walau barang diretur; markdown tampak habis lebih cepat. Juga cabang lama men-cek periode, jadi retur sesudah markdown berakhir ditandai `markdown_invalid`. | **DIPERBAIKI** (kode kecil, lihat G45.3) |
| 2 | `efn_lot_markdown.py:242-246` + `efn_expiry_alert.py:113-115` | `_cron_efn_expire` hanya dipanggil dari cron scan yang mati bila flag `false`: markdown aktif yang lewat `date_to` tetap berstatus Aktif selama flag mati. Dampak kecil (preload POS juga kosong saat flag mati; `_efn_markdown_settle` menerima `expired`/`active` dengan cek periode). | Dilaporkan (desain) |
| 3 | `efn_expiry_alert.py:63-66`, `:138-144` | Kunci unik alert memuat tanggal deteksi: alert yang ditutup manual (`action_efn_close`) tetapi stok masih near-expiry dibuat ulang **keesokan hari** dan memicu notifikasi baru (hari yang sama tidak). Perilaku wajar tetapi kepala toko bisa merasa "ditagih ulang" setiap hari bila terus menutup manual. | Dilaporkan; diuji (G45.3) |
| 4 | `efn_expiry.js:40-45` vs `pos_order.py:60` | Periode markdown: klien memakai **tanggal lokal perangkat**, server memakai tanggal lokal TZ konteks pada `date_order`. Perangkat dengan jam/TZ salah dapat menjual markdown di luar periode (server mencatat `markdown_invalid`, order tetap lunas). Sudah diputuskan ("keputusan terkunci"); perlu dicatat di SOP kasir. | Dilaporkan |
| 5 | `pos_order.py` `_efn_markdown_refund` (baru) | Retur mengurangi `qty_sold` sebesar qty retur walau sebagian qty baris asal mungkin dihitung sebagai `markdown_cap_exceeded` (kelebihan harga normal): bisa mengurangi terlalu banyak (dibatasi ≥ 0). Klien membatasi qty ke sisa kuota, jadi kelebihan hanya terjadi saat dua kasir menjual bersamaan/offline. | Dilaporkan (risiko rendah) |
| 6 | `README.md` modul §Batasan; seluruh `static/src` | **Tidak ada tour/Hoot test POS**: FEFO tanpa popup, blokir + dialog PIN, harga markdown, `setQuantity` kuota, perilaku offline hanya terbukti lewat test server JS-less. Bentuk `modifiedPackLotLines` (array/objek) dan `getSelectedOrderline()` di jalur edit lot belum terverifikasi di Odoo 19 nyata. | Dilaporkan; UAT N5–N8 menjadi satu-satunya uji layar (wajib manual sebelum pilot) |
| 7 | `README.md:69`, `pos_store_patch.js:243` | Override PIN **online saja**; offline kasir tanpa grup override tidak bisa menjual lot kedaluwarsa (sesuai kebijakan offline), tetapi kasir ber-grup override bisa menyetujui sendiri tanpa PIN (tercatat saat sync). Pastikan grup ini tidak diberikan ke kasir biasa (SoD). | Dilaporkan; UAT N7/N8 |

### G45.3 Perbaikan dan test baru (belum dijalankan)
**Kode (1 berkas):** `services/odoo/addons/efn_expiry_monitor/models/pos_order.py` — `_efn_expiry_after_sync` memanggil helper baru `_efn_markdown_refund(line)` untuk baris `qty < 0` tanpa klaim markdown: `origin = line.refunded_orderline_id.efn_markdown_id` (harus satu PT), kunci baris markdown (`touch_row`), `qty_sold = max(qty_sold + qty, 0)`, tautkan `efn_markdown_id`; tanpa cek periode. Idempotensi tetap dijaga `efn_expiry_checked`. Klaim klien `efn_markdown_ref` pada baris retur tetap lewat jalur lama.

**Test Odoo baru:**
- `efn_expiry_monitor/tests/test_pos_expiry.py::test_markdown_refund_restores_quota` — jual 2 (qty_sold 2) → baris retur ORM `refunded_orderline_id` qty −1 → qty_sold 1 + tautan markdown; panggil ulang tidak mengubah (idempoten); retur −5 (melebihi) → 0, tidak negatif. (Baris retur dibuat via ORM agar tidak bergantung alur PIN refund `efn_pos_refund_origin`; bila `price_subtotal*` berkonflik dengan constraint 19.0 pada create baris, ganti dengan `sync` memakai `refunded_orderline_id` seperti `efn_mgm_policy/tests/test_mgm_basis_pos.py:89-91`.)
- `test_pos_expiry.py::test_expired_markdown_cron_then_late_sync_invalid` — markdown aktif lewat `date_to` → cron → `expired`, hilang dari preload POS; order yang masih mengklaimnya → lunas + `markdown_invalid`, `qty_sold` tetap 0.
- `efn_expiry_monitor/tests/test_expiry_alert.py::test_next_day_run_no_renotify_and_new_alert_after_close` — idempotensi lintas hari (tanggal alert digeser ke kemarin): alert diperbarui tanpa alert/To-Do baru; stok habis → closed; stok kembali → alert baru bertanggal hari itu (2 baris: closed + open); run ulang hari yang sama 0.

**Sudah ada, tidak diulang (dibaca):** XOR harga/diskon, batas peran 50/90 + parameter, scope outlet, kunci setelah draft, audit, preload + RPC kandidat, PIN supervisor + consume, cap `markdown_cap_exceeded`, FEFO/sumber lot tidak sah, kedaluwarsa tanpa override, alert 3 tahap + DC/kategori tanpa ambang diabaikan, dedupe aktivitas CE, sisa umur terima + override wizard, transfer antar outlet ditolak assortment.
**Masih belum ada (disarankan):** test JS (Hoot/QUnit) `efn_expiry.js` (`efnPickFefo` lot tanpa ED terakhir, `efnMarkdownRemaining`, `efnLotBlocked` removal-date) dan tour POS; test resupply 2 langkah via transit; test kompetisi kuota markdown dua sync bersamaan (`touch_row`).

### G45.4 Dokumen
- `docs/04_SERVICE_CONTRACTS.md` §28.1: baris `efn_expiry_monitor` dan `efn_setup_supermarket` tahap B → "**Dibangun G45** (test server lulus; POS belum diuji di layar)". §28.5a: **Catatan implementasi G45** (fallback ambang ke induk per field + constraint, `notify_state` pending untuk G46, parameter `max_discount_store_head/markdown_manager`, `pos_lot_limit` 20000, `override_ttl_seconds` 1800, server penentu akhir + jenis event, cap tidak memecah baris + retur mengurangi `qty_sold`, ketergantungan `efn_pos_refund_origin`, peran override sisa umur).
- `docs/testing/UAT_SCENARIOS.md` bagian **N1–N14**: alert harian + idempotensi, flag mati, markdown kepala toko vs manager (batas 50/90, XOR, overlap, scope), kanal online tidak berlaku, FEFO EAN vs GS1 lot markdown (kuota tidak memecah baris), blokir kedaluwarsa + PIN salah/kunci + override, **POS offline**, retur memulihkan kuota, klaim klien dimanipulasi (server tetap lunas tapi tercatat), penerimaan DC sisa umur kurang + override, transfer antar outlet ditolak assortment, **restriksi demo** (SMD05 tanpa freezer, SMD02 deny impor premium, alkohol hanya SMD01/SMD04, SMD03 closed; override alkohol tidak mungkin), lintas PT & privasi.

### G45.5 Sisa risiko
1. **(P1 sebelum pilot)** POS tidak pernah dijalankan di layar: UAT N5–N8 wajib manual pada perangkat nyata (online dan offline) sebelum `efn_expiry_monitor.enabled=true` di mana pun selain dev.
2. Ambang kategori tahap B = placeholder (Q-SM28/Q-SM9); batas diskon 50/90 = default Q-SM4; produksi tetap menunggu jawaban klien.
3. Kontrol izin alkohol di kasir tidak ada (assortment tidak ditegakkan di POS, keputusan pemilik; Q-SM13) — UAT N13 mencatat sebagai temuan terbuka.
4. Temuan #2–#5 di atas (cron expire bergantung flag, alert dibuat ulang per hari, TZ perangkat, selisih retur vs kuota) = perilaku diterima/dilaporkan, bukan bug pasti.
5. Perubahan `pos_order.py` dan tiga test baru belum dijalankan; verifikasi di container (`-u efn_expiry_monitor --test-tags /efn_expiry_monitor`).

---

## G47 QA — Insight supermarket (worktree `efn-platform-g47`, branch `feat/wave-47-insight-supermarket`)

Metode: **hanya membaca kode** (tanpa runtime; tidak ada shell di sesi ini). Tidak mengulang verifikasi lead (Odoo test, ETL pytest, tsc/vitest/build, vektor eit2/sig2, harness row policy 8/8, DDL load CH 24.8, compose config).

### G47.1 Hasil pencocokan (tidak ada mismatch kolom/kunci/persona)
| Area | Hasil |
|---|---|
| Kolom ETL writer vs DDL `17-g47-insight-sm.sql` | Sama (nama + urutan) untuk 12 tabel; sudah dijaga `tests/test_sm_ddl.py`. Tipe transformer sesuai (UInt/Decimal/Nullable/Array). |
| Kolom `efn_insight.v_sm_*` (access SQL) vs `efn_dwh.v_sm_*` | CH memvalidasi saat CREATE VIEW (lead: load lulus). |
| Kolom yang diquery insight-web (`lib/queries-sm.ts`) vs daftar kolom `insight-sm-access.sql` | Semua ada; alias agregat tidak bentrok dengan kolom view; `outlet_type='dc'` = literal view. Sekarang dijaga test baru. |
| 39 kunci KPI: Odoo `kpi_registry.py` = JSON = ETL `sm_kpi.py` = literal view CH = `sm-defaults.ts` (default, enum, rentang, pasangan) | Identik. Odoo `parse` lebih ketat dari ETL (bool hanya 0/1, tanpa koma desimal) = aman (ETL superset). |
| Persona: grup Odoo (`PERSONA_GROUPS`) = `PERSONAS`/`ORG_PERSONAS` token = `claims.ts` = `insight-sm-policy.sh` | Identik (5 persona; PT-level = category, dc, exec, marketing). 29 view di policy = 29 view di access SQL. |
| Query tiap halaman persona vs daftar persona per view di row policy | Tidak ada query yang ditolak policy untuk personanya (diuji statis). `sm_cogs_sums` (store membaca margin_daily yang tidak diizinkan) tidak dipakai loader mana pun = kode mati. |
| Kolom Odoo yang dibaca ETL (usulan, bias, PO DC, expiry, markdown, kampanye, KPI, warehouse, outlet online, picking/move/location) vs model Odoo 19 + grant `odoo-readonly-grants.sql` + `22-platform-g47.sql` | Semua kolom ada di model dan di-grant per kolom; nilai selection (state/stage/kind) sama dengan konstanta ETL; `platform.v_campaign_stats` sesuai kunci `stats` yang ditulis BFF (`campaign-dispatcher.consumer.ts:28-31`). |
| Nama job ETL vs `v_sm_freshness` vs `MARTS` insight-web | 12 mart + 11 job cocok. |

### G47.2 Mismatch / temuan
| # | Temuan | Sisi A | Sisi B | Status |
|---|---|---|---|---|
| M-1 | **Flag `INSIGHT_SM_ENABLED` tidak diteruskan di compose.** Dibaca Odoo (`efn_insight/controllers/main.py:36,66`) dan insight-web (`lib/config.ts:75`), kontrak §28.13.12 (`04_SERVICE_CONTRACTS.md:4920`), tetapi tidak ada di environment service `odoo` maupun `insight-web`, jadi fitur tidak mungkin dinyalakan lewat `.env`. | `infra/compose/docker-compose.yml` (odoo ± baris 980, insight-web ± baris 1820) | — | **DIPERBAIKI**: pass-through `${INSIGHT_SM_ENABLED:-false}` di kedua service + `.env.example`. |
| M-2 | `scripts/deploy.sh:398` dan `scripts/lib/deploy-validate.sh:25,78` hanya mengenal `INSIGHT_ENABLED`; `INSIGHT_SM_ENABLED` tidak diteruskan ke render production dan tidak divalidasi boolean / tidak masuk `EFN_ALLOW_FEATURE_ON`. Produksi aman (default false) tetapi tidak ada gerbang Superadmin untuk menyalakan. `check-encryption.sh:796` juga hanya memeriksa `INSIGHT_ENABLED`. | scripts/* | kontrak §28.13.12 (syarat prod) | **Dilaporkan** (perubahan desain deploy; tugas devsecops). |
| M-3 | `thr.stock_age.days` (1..365) dipetakan ke bucket tetap 30/60/90 (`app/(sm)/dc/page.tsx:51`): N=45 tampil sebagai "> 60", N=120 sebagai "> 90". Label kartu tidak sama dengan nilai konfigurasi. | `dc/page.tsx:51` | `kpi_registry.py` rentang 1..365 | **Dilaporkan** (batasi rentang di kontrak atau pakai bucket dinamis di agregat). |
| M-4 | View/kunci tanpa konsumen UI: `v_sm_replenishment_line` (ST-3 "sudah di usulan", CM-3 alasan), `v_sm_replenishment_bias_weekly` (EX-6 konteks bias), `v_sm_segment_daily` + `v_sm_search_terms` (MK-3), `v_sm_markdown`; kunci `thr.bias.*` hanya dipakai `biasStatus` yang tidak dipanggil dari halaman, `thr.price.below_pct` hanya label netral. Kontrak §28.13.9 menyebut kartu itu. | `queries-sm.ts` (tidak ada query) | `04_SERVICE_CONTRACTS.md:4800-4812` | **Dilaporkan** (celah fitur; konfirmasi apakah ditunda ke fase 2 dan catat di kontrak/README). |
| M-5 | Catatan usang: `04_SERVICE_CONTRACTS.md:4887-4888` masih menulis #114/#117 "belum di main"; `queries-sm.ts:16` menulis "SQL BELUM dijalankan terhadap ClickHouse"; `sm-defaults.ts:8` menunjuk `lib/sm-config.ts` (tidak ada); `kpi_registry.py:4` menunjuk `lib/thresholds.ts` (nama sebenarnya `sm-thresholds.ts`/`sm-defaults.ts`). | — | — | **Dilaporkan** (dokumentasi). |
| M-6 | **Katalog 39 query insight-web belum terbukti jalan di ClickHouse 24.8.** Daftar verifikasi lead mencakup DDL, access SQL, dan harness policy, bukan eksekusi `SM_QUERIES` terhadap view dengan data (header `queries-sm.ts:16` meminta ini). Risiko spesifik: subquery `CATF` (`categ_id IN (SELECT … v_sm_product_list)` di bawah row policy kelas C), `arrayExists` lambda di dalam parameter Array, `HAVING` pada alias, `any()` pada kolom Nullable. | `queries-sm.ts` | CH 24.8 | **Terbuka** — jalankan semua query dengan setting scope sah di stg. |

### G47.3 Perbaikan yang dilakukan
1. `infra/compose/docker-compose.yml`: tambah `INSIGHT_SM_ENABLED: ${INSIGHT_SM_ENABLED:-false}` pada service `odoo` dan `insight-web` (default false, string persis `true` yang aktif; tidak mengubah network/secret/cap).
2. `infra/compose/.env.example`: tambah `INSIGHT_SM_ENABLED=false` + komentar syarat production.

### G47.4 Test yang ditambah
`services/etl/tests/test_g47_cross_service.py` (statis, pytest, baca file repo; **belum dijalankan** — tidak ada runtime):
- persona sama di Odoo / token / claims.ts / policy; PT-level persona sama Odoo dan web;
- 29 view policy = 29 view access SQL;
- setiap query yang dimuat loader halaman persona hanya membaca view yang diizinkan row policy untuk persona itu (dan view dasar terbuka untuk kelima persona);
- semua query katalog hanya merujuk view yang dideklarasikan;
- kolom literal pada SELECT sederhana katalog ada di daftar kolom `efn_insight.v_sm_*`;
- enum/rentang KPI `sm-defaults.ts` = JSON kontrak (39 kunci);
- flag `INSIGHT_SM_ENABLED` diteruskan di compose untuk Odoo dan insight-web.
Jika regex test gagal di CI karena format file berubah, perbaiki regex, bukan melonggarkan asersi.

### G47.5 UAT
`docs/qa/G47_UAT.md`: prasyarat lintas persona + checklist Given/When/Then per persona (kepala toko, direksi, category manager, DC, marketing, pengelola KPI) termasuk kasus negatif (persona salah route, outlet lain via URL, tanda tangan scope rusak, data usang, ClickHouse mati, tracking OFF, nilai KPI tidak sah, pasangan ambang terbalik, isolasi PT).

### G47.6 Sisa risiko
1. **M-6** (query katalog belum dieksekusi di CH) = P1 sebelum dipakai pilot.
2. M-2 (gerbang deploy/validasi `INSIGHT_SM_ENABLED`) sebelum production.
3. M-3/M-4 = perbedaan fitur vs kontrak; perlu keputusan lead (tunda atau bangun).
4. Fill rate diperkirakan bila `picking_shared=1` (alokasi FIFO, fit-gap §6.2); belum ada data nyata untuk mengukur frekuensi.
5. Test baru dan seluruh UAT belum dijalankan.

---

# G51 — QA konsistensi SMD end-to-end (branch feat/wave-51-smd-e2e, cba9491)

Semua verifikasi = membaca kode (tanpa runtime; QA ini tidak menjalankan pytest/vitest/odoo).

## G51.1 Mismatch yang ditemukan dan DIPERBAIKI

1. **Alasan scrap SMD tidak pernah dimuat.** `dim_scrap_reason` hanya diisi job G32 `waste_reasons` (`efn_etl/waste.py:330`, butuh
   `ETL_ENABLE_WASTE` + efn_ck_production), sedangkan `v_sm_waste_daily` men-join-nya (`infra/clickhouse/init/17-g47-insight-sm.sql:765`)
   -> semua scrap SMD "Tanpa alasan". Fix: `sm.py` `JOB_SCRAP_REASONS` + `sm_scrap_reason_job()`, dijalankan di
   `sm_cycle.run_sm_scraps` (grant `stock_scrap_reason_tag` sudah ada di blok G47, `odoo-readonly-grants.sql:497`).
2. **Stok awal (tahap 6) bertanggal hari seed.** Rekonstruksi B6 = quant sekarang - Σ move setelah hari d
   (`sm_stock_hist.py:188`), sehingga move inventori "hari ini" ikut terkurangi di semua hari lampau (stok historis tanpa stok
   awal, dipotong 0; GMROI/stok >30 hari salah). Fix: `seed_demo_supermarket.py` `backdate_opening_stock()` (dipanggil di awal
   tahap 13) menggeser `stock_move`/`stock_move_line` `is_inventory` ke H-1 sebelum `loop_start`. Idempoten.
3. **Jendela fill rate.** `ETL_SM_FILL_RECALC_DAYS` default 14, batas 60 (`config.py:416`) vs seed/spec 90 hari
   (`sm.py` `sql_fill_lines`: `delivery_date >= since`). Fix: batas 1..120; `.env.example`, README ETL, runbook (set 90 untuk demo).
4. **Minggu tertua forecast hilang.** `app/(app)/forecast/page.tsx:42` dan `lib/sm-data.ts:256` memakai `from = today-55` terhadap
   `week_start BETWEEN` (Senin); 8 minggu ETL = Senin (minggu ini -56 .. -7), jadi minggu ke-8 selalu terbuang (7 titik). Fix: `-62`.
5. **Label tipe pembayaran SMD.** Mart `method_type` = `pos_payment_method.payment_method_type` Odoo 19 (`none|terminal|qr_code`,
   `etl/pos.py:25`), tetapi `smd-merge.ts` `SMD_PAY_TYPE` hanya memetakan `cash|bank|pay_later` -> UI "SMD - none" untuk semua
   metode seed. Fix: tambah `none/terminal/qr_code` (kunci lama tetap).

## G51.2 Diverifikasi COCOK (tidak diubah)

- Seed A1 vs `sql_fill_lines`/`SQL_FILL_MOVES`: usulan `approved`, `picking_id` = picking DC pertama (pick; DC `pick_ship`) ->
  satu langkah `move_dest` ke ship (transit); recv = transit->Input outlet (`two_steps`) melintasi batas outlet, storage
  Input->Stock dikecualikan (src=dst outlet); backorder `backorder_id` dikejar <= 3; `move.date` di-backdate.
- A2 vs `_sql_dc_purchase`: `picking_type_id = dc.in_type_id` (gudang `efn_is_dc`), `orderpoint_id` + `efn_replenishment_managed`
  (`_efn_managed_orderpoint` mengisinya); 70% SKU berflag.
- A3 vs `scrap_sql`: `location_id` outlet -> gudang, tag `scrap_reason_tag_ids` -> `MIN(tag_id)`, nilai = Σ|stock_move.value|,
  `date_done` di-backdate lewat SQL (cursor memakai `write_date`, tidak terpengaruh).
- A4 vs `pos.py`: `payment_date`, `amount = amount_total` (incl. PPN, sebasis `net_sales_incl_tax`), metode per config;
  `v_sm_payment_daily` join `dim_outlet.warehouse_id`.
- A7 vs `SQL_RECON_STOCK`: picking POS->Customers = keluar W; PO/receipt transit->Input = masuk W; Input->Stock (intra-W) dan
  pick DC->output diabaikan; scrap = keluar. Grant kolom stock_move/stock_quant/standard_price cukup.
- Forecast as-of: `asof.py` (format `efn_forecast_asof/1`, generated_at Minggu 16:00 UTC, run_id UUIDv4 deterministik) = loader
  `load_forecast_asof.py` (cek format/generated_at; kolom `efn.forecast.run` semua ada; `_efn_validate_line` dengan
  `today=batch_day`, `last_day=batch_day+horizon`; `is_active=False`; tanpa `_efn_on_run_closed_ok`;
  `_cron_efn_compute_pending` butuh `is_active` + `efn_proposal_pending` -> aman). ETL: `fc_runs`/`fc_daily` tanpa filter
  `is_active`; akurasi `argMax(generated_at)` dengan `toDate(generated_at WIB) < forecast_date` memilih run as-of terdekat; 8 Senin
  runbook = tepat `accuracy_weeks(now, 8)`; `v_sm_forecast_daily` memakai run terbaru menurut `generated_at`.
- insight-web: `parseFilters` — `pt` kosong/tidak sah = semua ESB sesi + SMD (exec); `pt=SMD` = `esb=false`; `pt` hanya-ESB =
  SMD mati; cabang `sm:` hanya bila `smdOn` dan ⊆ `wids`; satu sisi cabang mengecualikan sisi lain; FilterBar menghapus param bila
  semua chip (ESB+SMD) aktif. Tidak ada hitung ganda (tabel/company terpisah; trx = orders per outlet). Legend `smd` (c7).
  `filtersToParams` tidak SMD-aware tetapi tidak dipakai di mana pun (kode mati).

## G51.3 Test yang ditambah

- `services/insight-web/tests/smd-merge.test.ts`: pemetaan `none/terminal/qr_code/''` (regresi mismatch 5).
- Fix 1 tanpa test baru: `run_sm_scraps` tercakup `test_run_sm_cycle_isolates_failures` (fake mengembalikan kosong). Saran: assert
  `sm.JOB_SCRAP_REASONS in store.marks` saat data ada.

## G51.4 Risiko / mismatch yang TIDAK diperbaiki

1. **PO "terlambat" tak terlihat di mart.** `fact_dc_purchase_line` tak punya tanggal terima; `v_sm_dc_purchase.is_late` hanya untuk
   PO `purchase` ber-`date_planned` lampau DAN `qty_received < product_qty` (`17-g47-insight-sm.sql:1064`). Seed menerima semua PO
   penuh (A2 "±10% terlambat" hanya terlihat di `effective_date` Odoo). Opsi: sisakan ~5% PO belum diterima, atau tambah kolom
   tanggal terima di mart (desain).
2. **Basis `/sales` SMD**: net incl. PPN (`sm_fb_daily`), gross & diskon tanpa PPN (`gross_sales = subtotal + discount_amount`) ->
   `gross - diskon != net` untuk porsi SMD (persentase diskon tetap konsisten). Perlu label/keputusan.
3. Qty campuran (porsi ESB + pcs/kg SMD) dijumlah di kartu qty `/sales`.
4. `payment_date` mart memakai `toDate()` UTC (konsisten dengan `outlet_daily`); order 00:00-07:00 WIB bergeser sehari. Seed jam 12:00 WIB aman.
5. Rantai backorder `pick`/`ship` (`SQL_FILL_MOVES` mengandalkan `move_orig/dest` terwariskan saat `_split`) dan seluruh seed G51
   belum pernah dijalankan; jalankan smoke 14 hari x 10 SKU dulu (runbook "Belum diverifikasi").
6. "Forecast 14 hari ke depan" kosong/lampau bila belum ada run forecast nyata (hanya as-of); dicatat di `docs/qa/G51_UAT.md` §0.

## G51.5 File yang diubah QA

`services/etl/efn_etl/sm.py`, `services/etl/efn_etl/sm_cycle.py`, `services/etl/efn_etl/config.py`, `services/etl/README.md`,
`infra/compose/.env.example`, `scripts/dev/seed_demo_supermarket.py`, `docs/runbooks/demo-supermarket-seed.md`,
`services/insight-web/lib/smd-merge.ts`, `services/insight-web/lib/sm-data.ts`, `services/insight-web/app/(app)/forecast/page.tsx`,
`services/insight-web/tests/smd-merge.test.ts`; baru: `docs/qa/G51_UAT.md`.

---

## G52 QA lintas-sisi (22 Nov 2026, worktree efn-platform-nav, branch feat/insight-nav-dual-menu)

Verifikasi hanya dengan membaca kode (tanpa runtime). Dikecualikan atas permintaan lead: 270 test Odoo, 850 vitest, harness ClickHouse E1-E9, eksekusi 18 query, vektor eit3/sig3.

### Hasil per butir
1. **T1-T8 + kode galat: IDENTIK.** `insight_token.validate_claims_v3` (+ `_validate_common`, `_valid_brs`) vs `claims.ts scopeConsistentV3/parseBrs`; kode rinci Odoo (bad_codes/bad_brs/...) dipetakan ke `bad_claims` di `token.ts`/`session.ts` (semua parse null -> bad_claims). Pencabutan store-only berlaku di eit1/eit2/flag mati (`controllers/main.py:159-162`) -> `no_company`; `no_branch` hanya eit3+flag+B kosong+punya PT ESB (`main.py:164-167`, `_token_v3`).
2. **Setting SQL_efn_brs / SQL_efn_sig3: IDENTIK.** `scope.ts:83-88` (`key|cids|codes|brs`, brs digabung koma urutan token) = `insight-esb-policy.sh:55`; regex brs `scope.ts BRS_ITEM_RE` = `insight-esb-policy.sh:53` = Odoo `_BRS_RE`. 14 policy = 9 PT + 5 BR = 14 view di `insight-access.sql`.
3. **Kolom query vs view: cocok** untuk semua `fexec_*`/`fstore_*`/`fmkt_*` (dicek manual per kolom terhadap `insight-access.sql:81-152`). `fstore_*` tidak menyentuh cogs/mdr/net pembayaran.
4. **Sesi ber-cabang hanya /fnb/store:** halaman (proxy `BRANCH_BLOCKED_PATHS` + `pageCtx`), `/fnb/exec|marketing` (`fnbRouteDenial`), halaman SM (tanpa kode SM -> `tidak-berwenang`), `/api/export` (403 bila `brs`), `/glosarium` terbuka. Lolos.
5. **Flag:** Odoo/insight-web `INSIGHT_FNB_PERSONA_ENABLED` "true" persis, default false; `INSIGHT_ACCEPT_EIT2` default true; `EFN_INSIGHT_TOKEN_VERSION` default 3 di kode.

### Mismatch / bug
| ID | Sev | Sisi A | Sisi B | Status |
|---|---|---|---|---|
| Q52-1 | **Tinggi** (fitur tidak bisa dinyalakan) | Odoo `controllers/main.py:42,81` dan insight-web `config.ts:82` membaca `INSIGHT_FNB_PERSONA_ENABLED`; `config.ts:81` membaca `INSIGHT_ACCEPT_EIT2` | `infra/compose/docker-compose.yml` (odoo ~982, insight-web ~1826) tidak meneruskan kedua env; `deploy.sh:413-415` tidak merender | **DIPERBAIKI** |
| Q52-2 | Sedang | Kontrak §28.15.10 default `EFN_INSIGHT_TOKEN_VERSION` = 3 | compose + `deploy.sh:415` + `.env.example` default 2; `deploy-validate.sh:363` regex `^[12]$` MENOLAK 3 | **DIPERBAIKI** (default 3, regex `^[123]$`) |
| Q52-3 | Sedang (UX, bug yang dilaporkan lead) | `/fnb/exec`, `/fnb/marketing` memakai FilterBar global; layout memberi `sessionCodes` + SMD untuk exec+SMD | `fnbRangePage` memanggil `parseFilters` tanpa opsi smd -> `pt=SMD` / `branch=sm:*` = "filter tidak valid" | **DIPERBAIKI** |
| Q52-4 | Info | `README efn_replenishment:73` menyebut `efn_insight_sm.group_insight_store` | grup dipindah ke `efn_insight` | dilaporkan (dokumen) |

### Perbaikan (file)
- `services/insight-web/lib/page-filters.ts`: fungsi murni `selectableCodes(pathname, codes)` (membuang `SMD` di `/fnb/*`).
- `services/insight-web/components/FilterBar.tsx`: `sessionCodes`/`smdAllowed` diturunkan lewat `selectableCodes(pathname, ...)` -> tanpa chip SMD, tanpa opsi cabang `sm:` di halaman persona F&B; `pt=SMD` di URL diabaikan.
- Test baru `services/insight-web/tests/fnb-filter-codes.test.ts` (SMD dibuang di /fnb/*, tetap ada di /sales,/forecast, tidak memutasi input, server tetap menolak `pt=SMD` dan `branch=sm:5`).
- `infra/compose/docker-compose.yml` (odoo + insight-web: `INSIGHT_FNB_PERSONA_ENABLED` default false, `INSIGHT_ACCEPT_EIT2` default true, token version default 3), `scripts/deploy.sh`, `scripts/lib/deploy-validate.sh` (variabel rilis, validasi boolean, `INSIGHT_FNB_PERSONA_ENABLED` masuk `EFN_GATED_FEATURES` = production butuh izin Superadmin, regex versi `[123]`), `infra/compose/.env.example`.

### Sisa risiko / butir terbuka
1. Perubahan infra Q52-1/2 belum dijalankan (`docker compose config`, tes `scripts/` deploy-validate). Jalankan sebelum merge; bila ada tes yang menghitung `EFN_GATED_FEATURES`, sesuaikan.
2. User ganda store F&B + store SMD (ber-cabang + outlet SM): token sah, tetapi `navGroups` untuk sesi ber-cabang hanya menampilkan "Toko" F&B sehingga menu Supermarket hilang walau `/store` bisa dibuka lewat URL. Keputusan desain (kontrak §28.15.9 diam).
3. `FilterBar` masih menampilkan chip cabang `sm:` yang tertinggal di URL (hanya tampil, server menolak); kosmetik.
4. UAT G52 belum ditulis di `docs/testing/UAT_SCENARIOS.md` (di luar permintaan ini): sarankan skenario OTP tidak relevan; yang perlu: kepala toko tanpa cabang (`no_branch`), store+marketing (store dibuang), eit2 saat `INSIGHT_ACCEPT_EIT2=false`, pt=SMD di /fnb/exec.
5. Aturan `cids` sesi dual-domain memuat id company SM di `SQL_efn_cids` view ESB: aman (policy ESB hanya cek format + sig), tetapi sig menutup string apa adanya; tidak ada mismatch.

---

# QA G66 — Production planning di atas ESB (24 Nov 2026)

Cakupan: `services/prodplan` (api, service, workflow, calc, jobs, outbox, payloads, store), `services/insight-web` (lib/prodplan-*, components/pp, rute `(pp)/produksi`, `api/prodplan`), skema `23-prodplan-g66.sql`, kontrak §30. **Semua verifikasi dari membaca kode; tidak ada runtime** (pytest/vitest/ruff dijalankan lead). Tes baru belum pernah dijalankan; angka di dalamnya dihitung tangan (komentar di file).

## 1. Ketidakcocokan kontrak / UI-service (urut dampak)
Tidak ada yang merusak layar total: parser UI defensif, semua kunci utama (plan, requirements, transfers, outbox, preview, job, meta, boms, pack-rules) cocok dengan `service.py`. Selisih yang nyata:
1. **Nama produk di Usulan batch dan baris transfer hilang** (UI menampilkan "Produk #1001"). UI membaca `suggestions[].material_code/material_name/product_code/product_name` dan `transfers[].lines[].product_name` (`PlanParts.tsx:223-225`, `:296`; `prodplan-types.ts:360-373`, `:385-391`), service tidak mengirimnya (`service.py` get_plan SELECT suggestion/transfer_line). **Diperbaiki** (lihat bawah).
2. **`details.doc_numbers` dibuang UI**: service mengirim `{"doc_numbers": [...]}` untuk `transfers_not_authorized` (`service.py:881`, `:895`); `sanitizeDetails` hanya membaca `docs`/`doc_nums` (`prodplan-errors.ts:77`) -> pesan "Dokumen: ..." tidak pernah tampil. **Diperbaiki.**
3. **Label galat outbox**: worker menulis `esb_rejected`, `not_sent`, `sent_unknown`, `doc_not_found`, `marker_mismatch`, `cancelled`, `client_error` (`outbox.py:200,206,213,221,245,250`; `service.py:1014`), `OUTBOX_ERROR_LABEL` (`prodplan-format.ts:94-99`) hanya mengenal `esb_validation`/`esb_unavailable` yang tidak pernah ditulis -> baris yang dibatalkan/ditolak tampil "Galat dari ESB (lihat administrator)". **Diperbaiki** (label ditambah).
4. Dokumen pratinjau fase **produksi** tidak menampilkan produk hasil dan jumlahnya: service mengirim `product_code`, `qty`, `level`, `bom_id` per dokumen (`service.py:189-199`), `PreviewDoc` (`prodplan-types.ts:183-188`) dan tabel (`PlanFlow.tsx:328-348`) hanya menampilkan jenis dokumen + bahan. Pengguna tidak melihat "FIN-C 10 pcs" pada konfirmasi posting. **Belum diperbaiki** (perubahan tampilan; disarankan).
5. `details` galat validasi service = `{"field": ...}` (singular, `service.py:57`), kontrak §30.8 menyebut "nama field"; UI sudah menormalkan ke `fields[]` (`prodplan-errors.ts:70`) — cocok.
6. Kontrak §30.8 baris `GET /v1/boms` menyebut galat `422 bad_input`; service mengembalikan **400 `invalid_request`** (`service.py:282,287`). UI memetakan 400/422 sama; selaraskan teks kontrak.
7. `q` pencarian BOM: UI menerima 2..64 karakter bebas (`prodplan-actions.ts:77-86`), service 2..60 dengan whitelist karakter (`jobs.py:129`) -> 61-64 karakter atau tanda lain = galat umum "Permintaan tidak valid" (kosmetik).
8. Kode galat job yang ditulis worker tetapi tidak ada di kontrak (§30.8 daftar `job.error_code`): `conflict_version`, `invalid_state`, `bom_not_assembly`, `bom_missing`, `not_found`, `forbidden`, `interrupted`, `internal` (`jobs.py:36,386`; `store.py:255`); UI menampilkan pesan umum "Proses gagal". `job.result.path` (jalur siklus BOM) diabaikan (`prodplan-call.ts:74`): planner tidak melihat jalur siklus.
9. `stock_fetched_at` dikirim (`service.py` get_plan) tetapi tidak ditampilkan UI; pengguna tidak melihat batas 30 menit sebelum `stock_too_old`.
10. `PackRuleEditor` komentar menyebut "satuan harus sama dengan satuan dasar produk (diperiksa prodplan)" (`PackRuleEditor.tsx:4`); `put_pack_rule` tidak memeriksa (hanya flag `uom_mismatch` saat hitung).
Cocok (diverifikasi baris-per-baris): semua path/method ke `ROUTES` (`api.py:61-85`) vs `prodplan-actions.ts:225-247`; peran P/W/R; kunci body (tes baru `prodplan-contract.test.ts`); status job `queued|running|done|failed` (SQL `:162`); status outbox/plan/transfer/usulan; kode galat `workflow.MESSAGES` vs `PP_ERROR_MESSAGES` (24 kode; tes baru di kedua sisi); `meta`, `boms`, `pack-rules`, `plans` list `{items,count}`.

## 2. Bug / celah logika
Perhitungan `calc.py` terhadap §30.5: **tidak ditemukan bug**; vektor + 13 kasus tepi baru lolos penelusuran tangan (kelipatan kemasan tepat, desimal, diamond, bahan di dua level, kedalaman lewat memo, stok sumber 0/negatif, kemasan > stok sumber, BOM ganda per produk).
Celah desain / perilaku (dilaporkan, tidak diubah):
- **A (tinggi). Rencana `approved` tidak bisa dihitung ulang** sedangkan posting mensyaratkan snapshot stok <= 30 menit (`service.py` `post` `_check_snapshot_age`; `config.py:229`). Persetujuan > 30 menit setelah hitung => `stock_too_old` tanpa jalan keluar selain Batalkan + buat ulang. Usul: jendela umur dihitung dari persetujuan, atau izinkan `approved -> draft`, atau umur default >= 240.
- **B (tinggi). Dokumen ditolak ESB (4xx) di tengah rangkaian = jalan buntu**: baris `failed` (`esb_rejected`) tidak bisa di-resend (`service.py:1042-1047` hanya `not_found_after_timeout`), baris berikutnya menunggu selamanya, `post` menolak fase yang sudah punya outbox (`:918`). Satu-satunya jalan = Batalkan (menghapus yang sudah confirmed; bila sudah di-authorize di ESB menjadi `delete_failed`). Status rencana `posted` dipasang saat enqueue, bukan saat semua dokumen confirmed.
- **C (sedang). `resend` tanpa pemeriksaan ulang ESB**: setelah `not_found_after_timeout` dokumen bisa muncul terlambat di ESB; resend memakai marker sama dan menerbitkan dokumen kedua. Usul: `find_by_marker` sekali lagi sebelum menjadi `pending`.
- **D (sedang). Usulan batch yang sudah diterima tidak bisa dibatalkan** dan bertahan setelah `put_demand`/hitung ulang (demand `source=suggestion` tidak dihapus `put_demand`, `_save_calculation` menyimpan usulan `accepted`): bila baris permintaan asal dihapus, batch tambahan tetap diproduksi. Usulan `rejected` dihapus dan muncul lagi setelah hitung ulang (`jobs.py:700`). Usulan terbuka basi masih bisa diterima saat `calc_status != fresh` (`decide_suggestion` tidak mengecek).
- **E (rendah). `submit` dengan 0 baris produksi** mengembalikan `stale_calculation` ("hasil hitung kedaluwarsa") yang menyesatkan (`service.py:742-744`); `decide_transfer` boleh memutus ulang transfer yang sudah diputuskan; `_transfers_authorized` dibaca di luar transaksi (TOCTOU kecil dengan cancel).
- **F (rendah). `cancel` meninggalkan baris `failed/not_found_after_timeout`** tanpa pemeriksaan ESB (dokumen bisa ada).
- `recover_running` job menandai `interrupted` tanpa mengubah `calc_status` (aman).

## 3. Perbaikan yang dilakukan
- `services/prodplan/efn_prodplan/service.py` (`get_plan`): usulan batch memuat `material_code/material_name/product_code/product_name`, baris transfer memuat `product_name` (dari `requirements`/`production_lines` yang sudah dimuat; tanpa query baru).
- `services/insight-web/lib/prodplan-errors.ts`: `sanitizeDetails` membaca `details.doc_numbers`.
- `services/insight-web/lib/prodplan-format.ts`: `OUTBOX_ERROR_LABEL` + 7 kode yang ditulis worker.
- `services/insight-web/components/pp/PlanFlow.tsx`: tombol Posting nonaktif dengan alasan bila fase itu sudah punya dokumen outbox (sebelumnya klik -> 409 generik).

## 4. Tes ditambah (belum dijalankan; jalankan `ruff check` + `ruff format` pada file baru)
- `services/prodplan/tests/test_calc_edges.py` (13 tes): kelipatan kemasan tepat, desimal, pembulatan kemasan vs stok sumber, sumber < 1 kemasan, stok 0/negatif, gabung transfer satu sumber, diamond, bahan dua level + usulan dua BOM, BOM ganda per produk, qty 0/negatif, demand ganda, siklus 3 simpul, batas kedalaman lewat memo.
- `services/prodplan/tests/test_outbox_edges.py` (14 tes): penolakan di tengah rangkaian + pembatalan, resend, auth gagal (backoff 30/60), `client_error`, brand tidak aktif, konfirmasi menunggu/gagal baca, jalur hapus (not_sent, 404, rejected-tapi-hilang, tanpa nomor, write mati), pemilihan lintas rencana, blocker hapus. Mengimpor `test_outbox` (tetangga; `# noqa: I001` di baris impor pertama).
- `services/prodplan/tests/test_service_contract.py`: pratinjau -> body ESB persis (simple, GTR, produksi actual/standard), digest tak bergantung urutan, matriks transisi 5x5, `cancelled` terminal, semua kode `ApiError` di service/api/workflow ada di `MESSAGES`.
- `services/insight-web/tests/prodplan-contract.test.ts`: fixture bentuk nyata `get_plan`/preview/job, kode galat service vs tabel UI, `doc_numbers`, kode galat outbox, kunci body semua aksi.

## 5. Area tanpa tes
`service.py` handler SQL (hanya `build_preview`/validator diuji; konkurensi `FOR UPDATE`, `row_version`, `job_running` unique index, `post`/`cancel` atomik: hanya 3 tes integrasi PG yang di-skip lokal); `store.py` (claim/candidates/known_numbers tanpa tes selain integrasi); `jobs.JobRunner` (`_calculate` stok sumber, `_save_calculation`, `_pr_import` tanpa tes; hanya parser); `esb.py` klien nyata; semua komponen React (`PlanFlow`, `PlanParts`, `PpAction` polling, `DemandEditor`, `BomPicker`, `NewPlanForm`, `PackRuleEditor`) tanpa tes render; halaman `(pp)/produksi/**` tanpa tes; Odoo grup/peran PP sudah punya 86 tes (lead).

## 6. Sisa risiko / belum terverifikasi
UI di browser (aksesibilitas fokus, polling 5 menit, `router.refresh` setelah job); BOM multi-level pada data nyata; mode `standard`; jalur gagal outbox terhadap ESB nyata; `return-to-draft`/`resend` end-to-end; dua job bersamaan; purge/retensi; `ruff format --check` untuk file tes baru (CI menjalankannya); `# noqa: I001` mungkin tidak perlu bila ruff menganggap `test_outbox` first-party. UAT: `docs/qa/G66_UAT.md` (mengikuti folder `docs/qa/` yang sudah ada, bukan `docs/uat/`).
