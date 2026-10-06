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
