# Strategi Test EFN Platform (Fase 0)

Status: **Draft** (efn-qa-engineer, 2026-10-05). Semua test di repo ini **belum pernah dijalankan** di mesin penulis (tanpa Node/Python/Docker); validasi pertama terjadi di CI (`.github/workflows/ci.yml`).

## 1. Piramida per service

| Service | Unit (banyak, cepat) | Integrasi / kontrak | E2E / manual | Runner |
|---|---|---|---|---|
| `services/api-gateway` (BFF) | `*.spec.ts` per context: OTP, discount token (replay, alg none, order mismatch), rate limiter, webhook HMAC, multipart, loyalty idempoten, notify | Spec kontrak lintas-service: test vector `member_hash` (sama dengan ETL), payload `notify.push` dari worker, argumen queue (`queues.spec.ts`) | UAT A, C, D | Jest (`npm test`) |
| `services/superapp-web` | Vitest (`tests/*.test.ts`: `safe-next`, `phone`, `brands`, `bff-config` guard TLS/plaintext) | — | Playwright (gap), UAT D | `vitest run`, `eslint`, `next build` |
| `services/odoo/addons/efn_*` | `TransactionCase` (`efn_core`: BFF client, company; `efn_kitting`: MO, Kit, constraint) | `TestPoSCommon` + `sync_from_ui` dengan BFF di-mock (`efn_pos_otp_gate`, 16 test) | Tour POS (gap), UAT A, B | Odoo `--test-tags efn` |
| `services/ai-ocr-worker` | pricing, matching, fraud, validation, config, messages (payload kontrak) | processor + delivery dengan fake repo/publisher | UAT C | pytest |
| `services/etl` | watermark, transform (hash disalin dari Postgres, tolak non-hex/nomor HP — SEC-16), config TLS fail-closed, runner (keyset, fail-closed) | — (belum ada PG/ClickHouse nyata di CI) | UAT C1 (join hash) | pytest |
| `services/text2sql` | guard sqlglot (±50 kasus negatif), prompt | — | — | pytest |
| `infra/*` | — | `docker compose config` dev+prod, secret compose = `scripts/secrets.list`, promtool, amtool, shellcheck, hadolint, yamllint | UAT E (restore test) | CI job `infra` |

Prinsip:
- Jalur gagal wajib diuji (timeout BFF, 409/400, poison message, payload rusak), bukan hanya "mock dipanggil".
- **Kontrak lintas-service dikunci di kedua sisi** dengan nilai literal yang sama (mis. test vector `member_hash` di `app-config.spec.ts`; payload v2 `loyalty.earn.requested` di `loyalty.spec.ts` dan `ai-ocr-worker/tests/test_messages.py`; params `notify.push` di `notify-push.consumer.spec.ts` dan `ai-ocr-worker/tests/test_messages.py`). Mengubah satu sisi tanpa sisi lain = test merah.
- Tidak ada secret/nomor HP nyata di fixture (pakai `6281234567890`, `test-otp-pepper-v1`, token `dummy.*`).

## 2. Yang dijalankan CI (`ci.yml`, path-filtered)

| Job | Pemicu | Isi |
|---|---|---|
| `node` | `services/api-gateway/**`, `services/superapp-web/**` | `npm ci/install`, `lint`, `test --ci`, `build`, `npm audit --omit=dev --audit-level=high` |
| `python` | Python services / Odoo | `ruff check` + `ruff format --check` (termasuk addon Odoo dengan `services/odoo/ruff.toml`), `compileall`, `pip-audit --strict` |
| `pytest` (matrix) | `ai-ocr-worker`, `etl`, `text2sql` | `pip install -r requirements.txt -r requirements-dev.txt`, `pytest tests` (tesseract dipasang untuk worker) |
| `odoo-addons` | `services/odoo/**` | pylint-odoo (non-blocking) |
| `odoo-test` | `services/odoo/**` | fetch OCA, build image Odoo, `-i efn_core,efn_pos_otp_gate,efn_kitting,efn_setup_company,efn_setup_pos,efn_setup_mrp,efn_setup_audit --test-tags efn`, lalu `-i efn_setup_security` tanpa test di DB yang sama (password policy 12 karakter memecahkan fixture test Odoo); gagal bila log `ERROR/CRITICAL`. Demo data tidak dimuat (demo `efn_setup_*` belum tervalidasi CI) |
| `infra` | `infra/**`, Dockerfile, workflows, scripts | `gen-secrets.sh` + PKI dummy, `openssl verify` + SAN, `check-encryption.sh`, compose config dev+prod, port publish hanya Traefik (prod), secret compose = `secrets.list`, promtool, amtool, dashboard JSON, shellcheck, hadolint, yamllint |
| `tls-integration` (**usulan**, §5) | `infra/**`, `services/**` | compose up dengan PKI dummy + smoke test TLS positif & negatif |
| `ci-gate` | selalu | gagal bila ada job gagal |

Security (`security.yml`, `codeql.yml`), build/scan image (`build-push.yml`) dan DAST (`dast.yml`) berjalan terpisah.

## 3. Gap yang diketahui (prioritas)

| # | Gap | Dampak | Usulan | Kapan |
|---|---|---|---|---|
| G1 | **Tour POS** (`HttpCase.start_tour`) untuk alur OTP frontend (popup nomor/kode, blokir offline, guard `_applyReward`) | Patch OWL (`pos_order_patch.js`, `control_buttons_patch.js`, `payment_screen_patch.js`) belum pernah dieksekusi; error JS hanya muncul di UAT | Tour `efn_pos_otp_gate_tour` dengan BFF di-mock via `patch.object` di server; tag `efn` | Fase 1, sebelum UAT A |
| G2 | **E2E Playwright** Super-App (login OTP, upload laporan, status) dengan BFF mock | Integrasi Route Handler ↔ BFF (stream multipart `duplex:'half'`, cookie) belum teruji | Playwright + BFF stub (Mock Service Worker / server Fastify kecil) di job `node` | Fase 1 |
| G3 | **Load test OTP** (k6) | Rate limit Redis, latensi POS < 1 s, WA Cloud API throughput belum terukur | k6 terhadap staging: 50 rps `/v1/pos/otp/request` campuran nomor; target p95 < 300 ms BFF, 0 kebocoran 5xx | Fase 2 |
| G4 | Integrasi BFF dengan PostgreSQL/Redis/RabbitMQ nyata | SQL (`ON CONFLICT`, grant kolom), deklarasi queue quorum vs worker belum diuji di broker nyata | Testcontainers / service container di job `node` | Fase 1 |
| G5 | ETL ke ClickHouse nyata | Tipe `DateTime64(6,'UTC')`, `Decimal`, `UUID` via clickhouse-connect belum diuji | Service container ClickHouse 24.8 + PG di job pytest etl | Fase 1 |
| G6 | `pos_mrp` explode menu Kit dari sesi POS | Test kitting memakai `stock.picking`, bukan sesi POS | Tambah test `TestPoSCommon` dengan produk Kit | Fase 1 |
| G7 | ~~Unit test frontend~~ | Selesai gelombang 3 (Vitest, 4 file test) | — | — |
| G8 | Restore test di CI | Hanya manual/systemd bulanan | Job terjadwal di staging (bukan GitHub runner publik) | Fase 2 |
| G9 | **Integrasi TLS end-to-end** (compose nyata dengan PKI dummy) | Semua konfigurasi TLS/kredensial gelombang 3 baru divalidasi statis (`check-encryption.sh`, `compose config`); salah SAN/flag/izin baru terlihat saat runtime | Job `tls-integration` (§5) | Fase 0 akhir, sebelum staging |
| G10 | **Kontrak v3 lintas service** (G6): ALTCHA widget `altcha@^1` ↔ format challenge BFF manual; passive declare tanpa `configure` (strict default); erasure 3-store; payment mock ↔ `efn_pos_payment_gateway` | Unit test kedua sisi memakai fake; format widget, izin broker, dan cron Odoo baru teruji saat runtime | §6 (job `contract-v3`) + UAT G1–G9 | Sebelum staging |
| G11 | Tour/Hoot `PaymentEfnGateway` (offline, polling, cancel, QR layar pelanggan) | JS terminal POS belum pernah dieksekusi | Tour dengan BFF di-mock server-side (pola G1) | Fase 1, sebelum UAT G5 |
| G12 | Test drift teks consent | Teks/versi frontend berubah tanpa env BFF → `409 stale_text` di semua pengguna | **Ada (G6):** `superapp-web/tests/consent.test.ts` mengunci versi + SHA-256 `body` = nilai compose dev | — |

## 4. Definisi selesai (test)
1. Setiap endpoint/consumer baru punya test jalur sukses + minimal satu jalur gagal.
2. Perubahan kontrak docs/04 disertai perubahan test kontrak di **kedua** sisi.
3. Skenario UAT terkait (`docs/testing/UAT_SCENARIOS.md`) diperbarui.
4. Perubahan TLS/kredensial (compose, `infra/*`, klien di service) wajib lolos `check-encryption.sh` dan — setelah §5 ada — job `tls-integration`.

## 5. Usulan job CI `tls-integration` (ADR-022, docs/04 §7)

Tujuan: membuktikan di runtime bahwa setiap hop memakai TLS terverifikasi, plaintext ditolak, dan kredensial per service bekerja dengan izin minimum. Berjalan di GitHub runner (ubuntu-latest, 7 GB RAM → profil `core` + `data` saja; `ai`/`observability` di job terpisah atau nightly).

Langkah:
1. `bash scripts/gen-secrets.sh` (secret dummy + PKI dummy dari `gen-pki.sh`), `cp infra/compose/.env.example infra/compose/.env`, set `APP_ENV=production` (bukan dev) agar guard produksi ikut teruji.
2. Build image lokal (`docker compose build api-gateway superapp-web odoo etl`), lalu
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile core --profile data up -d --wait --wait-timeout 600`
   (gagal bila ada container tidak `healthy` / init one-shot exit ≠ 0: `minio-init`, `clickhouse-init`).
3. **Smoke positif** (container uji `alpine` + `openssl`/`curl`/`postgresql-client`/`redis` di network yang sesuai, CA = `secrets/efn_ca_crt.txt`):
   - `openssl s_client -verify_return_error -verify_hostname <svc> -CAfile ca` untuk: `postgres:5432 -starttls postgres`, `redis:6379`, `rabbitmq:5671`, `minio:9000`, `clickhouse:8443/9440`, `api-gateway:3000`, `odoo:8443/8444`, `superapp-web:3443`, `metabase:3443`.
   - `curl --cacert ca https://api-gateway:3000/health` = 200; `https://odoo:8443/web/health` = 200; `https://superapp-web:3443/api/health` = 200.
   - Alur kontrak minimal: `POST /v1/auth/otp/request` (WhatsApp console) → 202; `POST /v1/pos/otp/request` dengan `X-EFN-Service-Key` dari secret dummy → 202; tanpa key → 401; nomor `659…` → 400 `invalid_phone`.
   - ETL `ETL_RUN_ONCE=true` exit 0 (membuktikan grant `efn_readonly`→`efn_etl`, TLS PG + ClickHouse).
4. **Smoke negatif** (wajib gagal):
   - `psql sslmode=disable` → ditolak pg_hba; `redis-cli` tanpa `--tls` → putus; `curl http://api-gateway:3000/health` → gagal; `curl http://odoo:8069` dari container lain → connection refused; `curl http://clickhouse:8123` → refused.
   - Sertifikat dari CA kedua (dibuat di job) → `openssl s_client -CAfile ca2` gagal verifikasi untuk semua target.
   - `docker compose run --rm -e EFN_ALLOW_PLAINTEXT=true api-gateway` (APP_ENV=production) → exit ≠ 0; sama untuk `etl` dan `odoo`.
   - Kredensial lintas service: `efn_ocr` `SELECT user_enc` → permission denied; Redis `efn-bff` `FLUSHALL` → NOPERM; MinIO `efn-ocr` PutObject → AccessDenied.
5. Artefak: `docker compose logs` (sudah ter-mask) diunggah bila gagal; `docker compose down -v` selalu.

Tambahan G6 untuk langkah 3–4: `curl --cert/--key prometheus https://api-gateway:9464/metrics` = 200, tanpa sertifikat klien → handshake gagal, CN lain → 403, `https://api-gateway:3000/metrics` = 404; Prometheus target `api-gateway:9464` `up=1`; BFF start dengan `RABBITMQ_STRICT_PERMISSIONS=true` (membuktikan passive declare tanpa `configure`); `redis-cli --user efn-bff SET altcha:used:x 1` = OK, `SET foo 1` = NOPERM.

Catatan: job ini menguji konfigurasi, bukan performa. Durasi perkiraan 15–25 menit → jalankan pada PR yang menyentuh `infra/**`, `services/*/Dockerfile`, klien TLS di service, plus nightly. Pengecualian E7 (PostgreSQL CI tanpa TLS di `odoo-test`) tetap berlaku untuk job unit Odoo, bukan untuk job ini.

## 6. Usulan job CI `contract-v3` (G6, docs/04 §8)

Di atas `tls-integration` (§5), profil `core` + `data`, `APP_ENV=staging`, `PAYMENT_PROVIDER=mock`, `CONSENT_*` dari compose dev:
1. ALTCHA: `GET /v1/auth/challenge` → selesaikan dengan skrip Node (`solveAltcha` dari BFF) → `POST /v1/auth/otp/request` 202; replay → 400 `captcha_required`.
2. Consent: grant versi `location@2026-10-06` → 200; versi lain → 409; marketing tanpa env → 503.
3. DSAR: reauth (kode dari log console WA dev) → `GET /v1/me/data` dengan step-up 200, tanpa → 403; `DELETE /v1/me` dengan `DSAR_DELETE_GRACE_DAYS=0` → job erasure → `GET /v1/pos/privacy/erasures?store=odoo` memuat request; `complete` → `stores_pending={clickhouse}`; ETL `ETL_RUN_ONCE=true` → `status=completed`.
4. Payment: `POST /v1/pos/payments` → `simulate paid` → `GET` `paid`; idempotency key sama dengan nominal beda → 409 `idempotency_conflict`.
5. Broker: `rabbitmqctl list_permissions` = definitions strict; publish `privacy.erasure.completed` sebagai `efn-ocr` → DLQ.
Odoo (`efn_privacy`, `efn_pos_payment_gateway`) tetap diuji unit di `odoo-test` dengan BFF di-mock; end-to-end Odoo ↔ BFF hanya di UAT staging (Odoo di job ini terlalu berat untuk runner standar).