# Laporan agent: efn-data-ai-engineer

Tanggal: 2026-10-05. Lingkup: OCR worker Price Hunter, ETL PostgreSQL -> ClickHouse, guard Text-to-SQL, skema ClickHouse.
ADR terkait (semua `Proposed`): ADR-003, ADR-006, ADR-007, ADR-008, ADR-010, ADR-014, ADR-020. Kontrak: `docs/04_SERVICE_CONTRACTS.md` §3.3, §4, §5.

**Tidak ada yang dijalankan.** Mesin penulis tidak punya Python; semua kode, test, dan SQL belum pernah dieksekusi. Validasi pertama terjadi di CI / oleh QA.

## 1. File

### services/ai-ocr-worker (refactor; `worker.py` dihapus)
| File | Isi |
|---|---|
| `efn_ocr/config.py` | `Settings.from_env()`, `read_secret()` (`<NAME>_FILE` wajib ada bila di-set) |
| `efn_ocr/messages.py` | nama queue, argumen queue/DLQ, parse `ocr.requested`, payload `loyalty.earn.requested` & `notify.push`, header retry, mask nomor |
| `efn_ocr/pricing.py` | parser Rupiah (`Rp 12.500`, `12,500`, `12.5rb`, `1,2jt`, satuan & barcode diabaikan), toleransi harga |
| `efn_ocr/matching.py` | fuzzy nama rapidfuzz (`max(token_set_ratio, partial_ratio)`) |
| `efn_ocr/fraud.py` | geofence (haversine), pHash helper, cap harian |
| `efn_ocr/validation.py` | keputusan murni `valid/rejected/flagged` + reason |
| `efn_ocr/imaging.py`, `ocr_engines.py`, `vlm.py`, `storage.py` | Pillow+imagehash, Tesseract (default)/PaddleOCR (opsional), Ollama `/api/generate`, MinIO |
| `efn_ocr/repository.py` | psycopg 3: klaim, dedup pHash (SQL `bit_count`), cap harian, finalize+publish dalam satu transaksi, release |
| `efn_ocr/publisher.py`, `consumer.py`, `delivery.py` | pika: deklarasi topologi, publish dengan confirm, consumer ack manual + thread worker, retry/DLQ |
| `efn_ocr/processor.py` | orkestrasi per laporan (dependensi diinjeksi) |
| `efn_ocr/metrics.py`, `__main__.py` | Prometheus `:9100`, entry point `python -m efn_ocr` |
| `tests/` (8 file) | pricing, matching, fraud, validation, messages, config, engines (agregasi Tesseract, parse VLM), processor+delivery dengan fake |
| `requirements.txt`, `requirements-dev.txt`, `Dockerfile`, `.dockerignore`, `ruff.toml`, `pytest.ini`, `README.md` | |

### services/etl (refactor; `etl.py` dihapus)
`efn_etl/{config,watermark,hashing,jobs,adapters,runner,__main__}.py`, `tests/{test_watermark,test_jobs,test_runner}.py`, `requirements*.txt`, `Dockerfile`, `.dockerignore`, `ruff.toml`, `pytest.ini`, `README.md`.

### services/text2sql (baru)
`efn_text2sql/{guard,semantic,prompt,__init__}.py`, `semantic_layer.yaml`, `tests/{test_guard,test_prompt}.py` (±50 kasus negatif), `requirements*.txt`, `ruff.toml`, `pytest.ini`, `README.md`.

### infra/clickhouse
- `init/01-schema.sql` (diubah): `competitor_prices` -> `ReplacingMergeTree(updated_at)`, `ORDER BY report_id`, kolom baru `extracted_price, status, reason, ocr_confidence, user_hash, updated_at`, **`user_id` dihapus** (diganti hash); `fact_sales_line.write_date` -> `DateTime64(6,'UTC')`; `mv_sales_daily` (MV SummingMergeTree) **diganti** VIEW `v_sales_daily` ber-FINAL karena MV menghitung ganda baris overlap ETL; `_etl_watermark` dihapus (diganti `etl_watermark`).
- `init/02-loyalty-etl.sql` (baru): `loyalty_events`, `etl_watermark`, view `v_competitor_prices`, `v_price_hunter_quality`, `v_loyalty_daily`.
- `access/text2sql-access.sql` (baru, **sengaja di luar `init/`**): settings profile `text2sql_readonly`, role `text2sql_reader`, grant SELECT view + kolom tabel dasar. Dijalankan saat deploy bersama `CREATE USER` (butuh secret), agar kesalahan sintaks tidak menggagalkan init.
- Init script hanya berjalan pada volume kosong: volume dev yang sudah ada perlu `docker compose down -v` (clickhouse-data) atau DDL manual.

## 2. Env & secret yang dibaca (untuk dicocokkan devsecops/QA dengan compose)

### ocr-worker
| Env | Wajib/Default | Catatan compose |
|---|---|---|
| `RABBITMQ_URL` | `amqp://efn@rabbitmq:5672` | sudah ada |
| `RABBITMQ_PASSWORD_FILE` | secret `rabbitmq_password` | sudah ada |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_DB` (fallback `PLATFORM_DB`) | `postgres`, `5432`, `efn`, `efn_platform` | sesuai env yang disiapkan compose |
| `PG_PASSWORD_FILE` | secret `pg_password` | sesuai compose |
| `MINIO_ENDPOINT`, `MINIO_SECURE` | `minio:9000`, `false` | ada / opsional |
| `MINIO_ACCESS_KEY` -> fallback `MINIO_ROOT_USER` | | sekarang memakai root (disarankan service account read-only) |
| `MINIO_SECRET_KEY_FILE` -> fallback `MINIO_ROOT_PASSWORD_FILE` | secret | ada (root) |
| `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` | opsional |
| `OCR_ENGINE`, `OCR_TESSERACT_LANG`, `OCR_CONFIDENCE_THRESHOLD`, `OCR_VLM_ENABLED` | `tesseract`, `ind+eng`, `0.80`, `true` | |
| `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS` | `http://ollama:11434`, `qwen2.5vl:7b`, `90` | ada (kecuali timeout) |
| `NAME_SIMILARITY_THRESHOLD` | `75` (nilai `0.75` diterima) | ada |
| `PRICE_TOLERANCE_PCT`, `PRICE_HUNTER_DAILY_CAP`, `PRICE_HUNTER_POINTS` | `2.0`, `20`, `10` | |
| `PHASH_MAX_DISTANCE`, `PHASH_LOOKBACK_DAYS`, `GEOFENCE_CIRCLES` | `6`, `30`, kosong | |
| `OCR_PREFETCH`, `OCR_MAX_RETRIES`, `OCR_STALE_PROCESSING_SECONDS`, `OCR_MAX_IMAGE_BYTES` | `2`, `3`, `900`, 10 MB | |
| `METRICS_PORT`, `LOG_LEVEL` | `9100`, `INFO` | tambahkan target scrape Prometheus `ocr-worker:9100` |
| `BFF_INTERNAL_URL` | **tidak lagi dipakai** | hapus dari compose (worker tidak memanggil HTTP BFF, sesuai kontrak) |

Jaringan: worker perlu `data` (postgres, rabbitmq) dan `internal` (minio, ollama) — sudah `[internal, data]`. Tesseract menulis file sementara ke `/tmp`: bila service diberi `read_only: true`, tambahkan `tmpfs: [/tmp]`. Image berjalan sebagai UID 10001, kode milik root.

### etl
| Env | Default | Catatan |
|---|---|---|
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD_FILE`, `PG_DB` (fallback `PLATFORM_DB`) | `postgres`, `5432`, `efn`, secret `pg_password`, `efn_platform` | ada |
| `ODOO_DB` | `efn_odoo` | ada |
| `ODOO_PG_HOST`, `ODOO_PG_PORT`, `ODOO_PG_USER`, `ODOO_PG_PASSWORD_FILE` | ikut `PG_*` | baru, opsional (read replica produksi) |
| `ETL_ENABLE_ODOO` | `false` | job Odoo masih stub |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD_FILE`, `CH_DATABASE`, `CH_SECURE` | `clickhouse`, `8123`, `efn`, secret `clickhouse_password`, `efn_dwh`, `false` | ada sebagian |
| `OTP_PEPPER_FILE` | **wajib** | **compose etl perlu secret `otp_pepper`** (`OTP_PEPPER_FILE=/run/secrets/otp_pepper`). `user_hash = hex(HMAC-SHA256(key, user_id))`, `key = HKDF-SHA256(ikm=otp_pepper, salt=b"", info=b"efn:member_hash:v1", length=32)` — identik dengan `member_ref_hash` BFF (kontrak §1). Tanpa secret, job `competitor_prices` dilewati (fail closed). Dependency baru `cryptography==44.0.1`. Test vector di `tests/test_jobs.py`. |
| `ETL_INTERVAL_SECONDS`, `ETL_OVERLAP_SECONDS`, `ETL_BATCH_SIZE`, `ETL_MAX_PAGES_PER_RUN`, `ETL_STATEMENT_TIMEOUT_MS`, `ETL_RUN_ONCE` | `900`, `300`, `5000`, `200`, `300000`, `false` | |

Entry point berubah: `python -m efn_etl` / `python -m efn_ocr` (CMD Dockerfile sudah disesuaikan; compose tidak override `command`).

### text2sql
Belum service; contoh pemakaian membaca `semantic_layer.yaml` dan memanggil Ollama `qwen2.5-coder:7b`. User ClickHouse `efn_text2sql` + secret password dibuat saat deploy (lihat `infra/clickhouse/access/text2sql-access.sql`).

## 3. Kontrak RabbitMQ yang diimplementasikan (cek silang dengan BFF)

- Tidak ada `"dlx"` lagi (worker.py lama dihapus). Worker mendeklarasikan **ketiga** queue + DLQ dengan argumen persis: queue `{"x-queue-type":"quorum","x-dead-letter-exchange":"","x-dead-letter-routing-key":"<queue>.dlq"}`, durable; DLQ `{"x-queue-type":"quorum"}`, durable. BFF wajib memakai argumen identik (tanpa `x-delivery-limit`, TTL, dll.), jika tidak broker menolak dengan 406 dan worker reconnect berulang.
- Publish: default exchange, `message_id = idempotency_key`, `content_type=application/json`, `delivery_mode=2`, publisher confirm + `mandatory`.
- `loyalty.earn.requested`: `{schema_version:1, idempotency_key:"price_hunter:<report_id>", member_ref:<user_id>, points, reason:"price_hunter_valid", source:"price_hunter", source_ref:<report_id>}`.
- `notify.push`: `{schema_version:1, idempotency_key:"price_hunter_notify:<report_id>", user_id, template:"price_hunter_valid"|"price_hunter_rejected", params:{report_id, points}|{report_id, reason}}`. Status `flagged` tidak mengirim notifikasi.
- Retry `ocr.requested`: republish pesan yang sama dengan header `x-retry-count` (int) dan `message_id` asli.
- Worker hanya memakai `report_id` dan `schema_version` (wajib 1) dari pesan; data laporan dibaca dari PostgreSQL. `image_bucket` di pesan diabaikan (bucket dari konfigurasi), key harus berawalan `reports/`.

## 4. Asumsi skema

1. `platform.price_hunter_report` sesuai `infra/postgres/init/01-databases.sql`. Worker menulis `status, reason, extracted (jsonb), confidence (numeric 4,3), image_phash (16 hex huruf kecil, imagehash pHash), updated_at`. **Worker pemilik `image_phash`** (menimpa nilai BFF); BFF sebaiknya tidak mengisinya atau memakai algoritma identik.
2. `user_id` laporan = `member_ref` (E.164 tanpa `+`). Jika BFF memakai UUID user, kontrak `ocr.requested` perlu menambah `member_ref` atau BFF memetakan saat consume `loyalty.earn.requested`.
3. `platform.loyalty_ledger` (milik BFF, `02-platform-loyalty.sql` belum ada saat saya bekerja) punya kolom `id, member_ref_hash, delta_points, reason, source, source_ref, created_at`; tipe `id` bebas (ETL membandingkan `id::text`). Disarankan index `(created_at)`.
4. User PostgreSQL worker butuh SELECT/UPDATE `platform.price_hunter_report`; ETL cukup read-only.
5. PostgreSQL >= 14 (compose 16.6) untuk `bit_count`.
6. Job Odoo: kolom Odoo 17/18 (`pos_order_line.full_product_name`, `total_cost`, `price_subtotal_incl`, `pos_order.pos_reference/state/session_id`, `res_company.efn_brand_code`); belum dicek di CE 19.
7. Nilai poin per laporan (10) dan cap 20/hari adalah placeholder sampai Q-13/Q-43 dijawab.

## 5. Keputusan desain yang perlu diketahui

- **OCR tahap 1 = Tesseract**, bukan PaddleOCR (image kecil, tanpa unduh model runtime, aman Python 3.12). PaddleOCR opsional via `OCR_ENGINE=paddle` di image turunan. Menyimpang dari diagram docs/02 §3.2 yang menyebut PaddleOCR; ADR-008 mengizinkan keduanya.
- **Publish sebelum COMMIT** di transaksi finalize: publish gagal -> rollback -> retry; commit gagal setelah publish -> retry publish ulang dengan idempotency key sama (BFF dedup via `processed_message`). Outbox (`platform.outbox`) tidak dipakai worker karena relay-nya milik BFF dan belum ada.
- Klaim boleh mengambil alih status `processing` bila pesan redelivered/retry atau klaim > 15 menit (worker crash), agar laporan tidak macet. Finalize kondisional `WHERE status='processing'` mencegah tulis ganda.
- Pemrosesan di thread; ack via `add_callback_threadsafe` supaya heartbeat RabbitMQ tidak putus saat VLM 60–120 s.
- Sinyal fraud diperiksa **sebelum** OCR (hemat CPU/GPU): duplikat pHash -> `flagged`, cap harian -> `rejected`, di luar geofence -> `flagged`.
- Nilai laporan pengguna tidak dikirim ke VLM (hindari bias), prompt menyuruh abaikan instruksi di gambar.
- Guard Text-to-SQL mengeksekusi SQL hasil regenerasi sqlglot tanpa komentar, dibungkus `SELECT * FROM (...) AS _guarded LIMIT n SETTINGS max_execution_time = N`.

## 6. Belum terverifikasi / risiko sisa

- Test vector member hash (`otp_pepper="test-otp-pepper"`, member `6281234567890`) dihitung independen dengan HMAC-SHA256 .NET mengikuti RFC 5869; belum dicocokkan dengan implementasi BFF — sebaiknya BFF memakai vector yang sama.
- Seluruh test belum pernah dijalankan. Paling berisiko: kasus guard yang bergantung perilaku parser **sqlglot 26.0.0** (versi pin juga belum dicek ketersediaannya di PyPI; ganti ke rilis 26.x/27.x terbaru bila perlu), mis. `INTO OUTFILE`, `SETTINGS`, `remoteSecure(...)`; dan ambang skor rapidfuzz di `test_matching.py`.
- Versi pin lain (pika 1.3.2, psycopg 3.2.3, minio 7.2.12, requests 2.32.4, Pillow 11.3.0, ImageHash 4.3.1, rapidfuzz 3.10.1, pytesseract 0.3.13, prometheus-client 0.21.1, clickhouse-connect 0.8.9, PyYAML 6.0.2, pytest 8.3.4) belum diuji `pip-audit` — requests dinaikkan ke 2.32.4 dan Pillow ke 11.3.0 untuk CVE yang saya ketahui.
- `ruff format --check`: saya menulis mengikuti gaya ruff/black, tetapi belum dijalankan. Setiap service punya `ruff.toml` yang `extend` root + `known-first-party` agar urutan import deterministik.
- **CI**: job `python` di `.github/workflows/ci.yml` belum menjalankan pytest dan belum mencakup `services/text2sql` (ruff, compileall, pip-audit). Saya tidak punya izin menulis `.github/`; mohon devsecops menambahkan: `pip install -r requirements-dev.txt && pytest` per service dan path filter `services/text2sql/**`.
- ClickHouse: grant level-kolom + view `FINAL` dengan hak INVOKER belum diuji di 24.8 (mungkin perlu hak kolom sorting key, mis. `member_hash`); alternatif `SQL SECURITY DEFINER`.
- `clickhouse-connect` dan kolom `DateTime64(6,'UTC')`: konversi datetime aware dan nilai `argMax` pada set kosong (ditangani `normalize_watermark`) belum diuji.
- PaddleOCR path (`PaddleEngine`) memakai API 2.x dan tidak diuji sama sekali.
- Cap harian bisa terlewati 1 laporan bila dua laporan user yang sama diproses paralel.
- Deteksi foto editan/AI/dari internet belum ada (R-B05 sisa).
- ETL belum mengekspor metrik (lag watermark); belum menangani hard delete.

---

## Gelombang 3 — Enkripsi di setiap transaksi data (5 Okt 2026)

Kontrak: docs/04 v2 §4 (payload `schema_version: 2`, tanpa HP mentah, properti AMQP `user_id`) dan §7 (TLS, at rest, kredensial per service). **Tidak ada yang dijalankan** (tanpa Python/Docker); semua test belum dieksekusi. Bagian §2–§3 di atas sudah usang untuk env/payload; tabel di bawah yang berlaku.

### G3.1 File

| File | Perubahan |
|---|---|
| `services/ai-ocr-worker/efn_ocr/tls.py` (baru) | kebijakan TLS: `EFN_ALLOW_PLAINTEXT` (ditolak bila `APP_ENV`/`NODE_ENV=production`), CA wajib terbaca, `client_ssl_context` (hanya CA internal, TLS ≥ 1.2, verifikasi hostname), `pg_ssl_settings` (wajib `verify-full`), pasangan cert/key server |
| `efn_ocr/config.py` | default TLS: `amqps://efn-ocr@rabbitmq:5671`, PG `efn_ocr` + verify-full, MinIO `minio:9000` SSL + `efn-ocr`, Ollama `https://`, metrik HTTPS; fallback `MINIO_ROOT_*` dihapus; validasi fail-closed di `from_env` |
| `efn_ocr/publisher.py` | pika `SSLOptions(context, server_hostname)`, port 5671; menolak password di URL; properti AMQP `user_id` = user URL (SEC-08) |
| `efn_ocr/storage.py` | `Minio(secure=True, http_client=urllib3.PoolManager(cert_reqs=CERT_REQUIRED, ca_certs=CA))` |
| `efn_ocr/vlm.py` | `requests.Session.verify=CA`, `trust_env=False` |
| `efn_ocr/metrics.py`, `healthcheck.py` (baru), `Dockerfile` | `start_http_server(certfile, keyfile)` (prometheus-client ≥ 0.17, pin 0.21.1); healthcheck HTTPS loopback |
| `efn_ocr/messages.py`, `processor.py`, `repository.py`, `consumer.py`, `__main__.py` | event v2 (`user_hash` masuk, `member_hash`+`source`+`source_ref` keluar), cap harian & dedup per `user_hash`, PG `sslmode/sslrootcert`; `mask_ref` → `short_hash` |
| `requirements.txt` | + `urllib3==2.5.0` (dipakai langsung) |
| `tests/test_config.py`, `test_messages.py`, `test_processor.py` (ubah), `test_tls.py`, `test_repository_sql.py` (baru) | fail-closed per koneksi, payload v2 persis, tolak HP mentah, `user_id` AMQP, kolom UPDATE/SELECT yang diizinkan |
| `services/etl/efn_etl/tls.py` (baru), `config.py`, `adapters.py`, `jobs.py`, `runner.py`, `__main__.py` | PG verify-full (platform + Odoo), ClickHouse HTTPS 8443 `verify=True, ca_cert=CH_CA_FILE`, user default `efn_etl`; `user_hash`/`member_ref_hash` disalin (validasi hex sha256, fail closed); `company_code` di `loyalty_events` |
| `services/etl/efn_etl/hashing.py` | **dihapus** (SEC-16) + `cryptography` dihapus dari requirements; `TransformContext` dihapus |
| `services/etl/tests/test_jobs.py`, `test_runner.py` (ubah), `test_config.py` (baru) | test vector HKDF dihapus; test pass-through hash, tolak HP, company_code, TLS fail-closed |
| `services/text2sql/efn_text2sql/guard.py`, `semantic.py`, `semantic_layer.yaml`, `tests/test_guard.py` | SEC-04 (lihat G3.4) |
| `infra/clickhouse/init/02-loyalty-etl.sql` | `loyalty_events.company_code` + `ALTER ... ADD COLUMN IF NOT EXISTS` (volume lama), `v_loyalty_daily` per `company_code` (`CREATE OR REPLACE VIEW`) |
| `infra/clickhouse/access/text2sql-access.sql` (ubah), `etl-access.sql`, `metabase-access.sql` (baru) | role + profil + grant per user `efn_text2sql` / `efn_etl` / `efn_metabase` (lihat G3.3) |
| `infra/clickhouse/config.d/encrypted-storage.xml` (baru) | disk `encrypted` AES-256-CTR, kunci `from_env="CLICKHOUSE_DISK_KEY_HEX"`, policy default MergeTree |
| `infra/clickhouse/config.d/tls.xml` | saya tulis, lalu **ditimpa devsecops** (pemilik); versi devsecops yang berlaku |

### G3.2 Env & secret yang dibaca per service (untuk compose devsecops)

**ocr-worker** — secret: `pg_password_ocr`, `rabbitmq_password_ocr`, `minio_ocr_secret`, `efn_ca_crt`, `tls_ocr_worker_crt`, `tls_ocr_worker_key`.

| Env | Nilai produksi |
|---|---|
| `APP_ENV` | `production` (jangan set `EFN_ALLOW_PLAINTEXT`) |
| `AMQP_URL` (fallback lama `RABBITMQ_URL`) | `amqps://efn-ocr@rabbitmq:5671` |
| `RABBITMQ_PASSWORD_FILE`, `AMQP_TLS_CA_FILE` | `/run/secrets/rabbitmq_password_ocr`, `/run/secrets/efn_ca_crt` |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_DB`, `PG_PASSWORD_FILE` | `postgres`, `5432`, `efn_ocr`, `efn_platform`, `/run/secrets/pg_password_ocr` |
| `PG_SSLMODE`, `PG_SSLROOTCERT` | `verify-full`, `/run/secrets/efn_ca_crt` |
| `MINIO_ENDPOINT`, `MINIO_PORT`, `MINIO_USE_SSL`, `MINIO_CA_FILE` | `minio`, `9000`, `true`, `/run/secrets/efn_ca_crt` |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY_FILE` | `efn-ocr`, `/run/secrets/minio_ocr_secret` |
| `OLLAMA_URL`, `OLLAMA_CA_FILE` | `https://ollama:11434`, `/run/secrets/efn_ca_crt` |
| `METRICS_TLS_CERT_FILE`, `METRICS_TLS_KEY_FILE`, `METRICS_TLS_CA_FILE` | `/run/secrets/tls_ocr_worker_crt`, `…_key`, `/run/secrets/efn_ca_crt` |
| lainnya tidak berubah | `OCR_*`, `OLLAMA_MODEL`, `PRICE_HUNTER_*`, `PHASH_*`, `GEOFENCE_CIRCLES`, `METRICS_PORT=9100` |

Dihapus: `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD_FILE`, `MINIO_SECURE` (masih dibaca sebagai fallback `MINIO_USE_SSL`), `BFF_INTERNAL_URL`. Prometheus scrape `https://ocr-worker:9100` dengan CA internal.

**etl** — secret: `pg_password_etl`, `clickhouse_password_etl`, `efn_ca_crt`. **`otp_pepper` dicabut dari service etl.**

| Env | Nilai produksi |
|---|---|
| `APP_ENV` | `production` |
| `PG_HOST`, `PG_PORT`, `PG_USER`, `PG_DB`, `PG_PASSWORD_FILE` | `postgres`, `5432`, `efn_etl`, `efn_platform`, `/run/secrets/pg_password_etl` |
| `PG_SSLMODE`, `PG_SSLROOTCERT` | `verify-full`, `/run/secrets/efn_ca_crt` |
| `ODOO_PG_*` (`HOST/PORT/USER/PASSWORD_FILE/SSLMODE/SSLROOTCERT`), `ODOO_DB` | opsional; default ikut `PG_*` |
| `CH_HOST`, `CH_PORT`, `CH_USER`, `CH_PASSWORD_FILE`, `CH_DATABASE` | `clickhouse`, `8443`, `efn_etl`, `/run/secrets/clickhouse_password_etl`, `efn_dwh` |
| `CH_SECURE`, `CH_CA_FILE` | `true`, `/run/secrets/efn_ca_crt` |

**clickhouse (server)** — secret baru **`clickhouse_disk_key`** (64 karakter hex, `openssl rand -hex 32`); entrypoint compose perlu `export CLICKHOUSE_DISK_KEY_HEX="$(cat /run/secrets/clickhouse_disk_key)"` (pola yang sama dengan `CLICKHOUSE_PASSWORD`), dan mount `../clickhouse/config.d/encrypted-storage.xml:/etc/clickhouse-server/config.d/encrypted-storage.xml:ro`. Secret ini **wajib** di-backup terpisah dari volume (hilang kunci = data mart hilang; mart bisa dibangun ulang dari Postgres dengan reset watermark).

**text2sql** — belum service; saat dibuat: user `efn_text2sql`, ClickHouse HTTPS 8443 + `efn_ca_crt`, Ollama `https://ollama:11434` + CA (README).

### G3.3 Hak database yang dibutuhkan (untuk BFF / devsecops)

- **PostgreSQL `efn_ocr`** pada `platform.price_hunter_report`: `SELECT (id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object, image_phash, status, created_at, updated_at)` + `UPDATE (status, reason, extracted, confidence, image_phash, updated_at)`. `UPDATE … RETURNING` dan `WHERE` butuh SELECT atas kolom-kolom itu. Tanpa `user_enc`. Index `(user_hash, created_at)` disarankan untuk cap harian (pengganti `ix_phr_user_created`).
- **PostgreSQL `efn_etl`** (anggota `efn_readonly`): grant kolom `price_hunter_report` **harus mengganti `user_id` dengan `user_hash`** (`id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, status, extracted, confidence, reason, created_at, updated_at`); `loyalty_ledger (id, member_ref_hash, company_code, delta_points, reason, source, source_ref, created_at)` sudah sesuai.
- **RabbitMQ `efn-ocr`**: read `ocr.requested`; write via default exchange (`amq.default`); **configure** pada 3 queue + 3 DLQ (worker mendeklarasikan topologi). Catatan: dengan default exchange, izin write RabbitMQ diperiksa pada exchange `amq.default`, **bukan** per queue — pembatasan "write hanya ke loyalty/notify" di §7.3 tidak bisa ditegakkan broker; kontrol nyata = validasi `user_id` + semantik di BFF (SEC-08). Retry worker mem-publish ulang ke `ocr.requested` (butuh write `amq.default`, sudah tercakup).
- **MinIO `efn-ocr`**: `s3:GetObject` `market-scout/reports/*` (SSE-S3 transparan untuk GET).
- **ClickHouse** (`infra/clickhouse/access/*.sql`, dijalankan admin saat deploy **setelah** user dibuat devsecops; idempoten): `efn_etl` → role `etl_writer` (SELECT+INSERT 3 tabel mart + `etl_watermark`, profil `allow_ddl=0`); `efn_text2sql` → role `text2sql_reader` (SELECT 4 view + kolom non-identitas, profil `readonly=2 CONST`, `max_execution_time 15 MAX 30`, `max_result_rows 1000`, `allow_introspection_functions=0`, kuota per jam); `efn_metabase` → role `metabase_reader` (SELECT view + kolom mart tanpa `user_hash`/`member_hash`/`customer_id`, `readonly=2 CONST`). Urutan file bebas; masing-masing butuh user target sudah ada.

### G3.4 Status SEC

| ID | Status | Catatan |
|---|---|---|
| SEC-04 | **Diperbaiki (kode), belum dieksekusi** | Resolusi CTE per scope (`_ScopeChecker`), tolak shadowing (`protected_tables` + view whitelist), tolak CTE skalar sebagai sumber tabel, tolak `WITH RECURSIVE`, `IN` hanya subquery/konstanta, minimal satu view whitelist. Test: payload laporan + 20 variasi (subquery IN/FROM/scalar, UNION, self-reference, urutan CTE, `IN tabel`, `GLOBAL IN`, `IN (kolom)`, `JOIN USING`) + 7 kasus positif. Lapisan kedua lengkap (rekomendasi 4): access SQL per user; menjadikannya langkah wajib deploy = devsecops. |
| SEC-08 | **Sisi worker selesai** | Properti AMQP `user_id=efn-ocr`, payload `member_hash` + `source_ref`; validasi semantik di BFF. |
| SEC-16 | **Selesai** | ETL tidak lagi membaca `otp_pepper`; `hashing.py` + `cryptography` dihapus; hash disalin dari Postgres dan divalidasi format. devsecops: cabut secret `otp_pepper` dari service etl. |
| SEC-19 | Info, tanpa kode | Berlaku di BFF (decode gambar). Catatan data-AI: worker OCR sudah memproses gambar di proses terpisah dengan prefetch 2 dan `OCR_MAX_IMAGE_BYTES`; bila intake dipindah ke worker (rekomendasi jangka menengah), pola ini bisa dipakai ulang. Disarankan `Image.MAX_IMAGE_PIXELS` disamakan dengan batas BFF. |

### G3.5 Enkripsi at rest ClickHouse

Disk `encrypted` dipakai (praktis di 24.8, fitur stabil sejak 21.x): `config.d/encrypted-storage.xml`, AES-256-CTR, kunci dari secret `clickhouse_disk_key` lewat env, policy `encrypted` jadi default via `<merge_tree><storage_policy>`. Batasan: metadata/DDL/nama part tidak terenkripsi, tabel lama tidak otomatis pindah (dev: `docker compose down -v` volume `clickhouse-data`), log sistem (`system.query_log`) ikut MergeTree → ikut terenkripsi. LUKS host tetap wajib. **Belum diverifikasi**: bahwa `<merge_tree><storage_policy>` diterapkan ke tabel yang dibuat oleh skrip init (cek `system.tables.storage_policy`); fallback bila tidak: tambahkan `SETTINGS storage_policy = 'encrypted'` di DDL `init/*.sql`.

### G3.6 Asumsi & risiko yang perlu diketahui lead

1. **`tls.xml` devsecops menghapus port native 9000.** Entrypoint image resmi menjalankan skrip `/docker-entrypoint-initdb.d` dengan `clickhouse-client` ke `127.0.0.1` port default 9000 (readiness memakai HTTP/HTTPS ping). Tanpa 9000, inisialisasi skema pada volume kosong kemungkinan gagal. Opsi: `/etc/clickhouse-client/config.xml` dengan `<secure>true</secure><port>9440</port>` + CA dan SAN sertifikat `localhost/127.0.0.1`, atau pertahankan 9000 hanya saat init. Perlu diverifikasi devsecops.
2. Endpoint Prometheus ClickHouse `:9363` (`config.d/prometheus.xml`) masih HTTP — tidak memuat PII; perlu sidecar ghostunnel atau pengecualian ADR-022.
3. Skema BFF: `price_hunter_report.user_hash char(64)` berisi hex huruf kecil (rumus §1). Worker menolak `ocr.requested` v1 dan pesan yang memuat `user_id`/`member_ref` (→ DLQ); pesan v1 yang masih ada di queue saat deploy akan masuk DLQ.
4. ETL fail-closed: satu baris dengan `user_hash`/`member_ref_hash` bukan hex sha256, atau `company_code` di luar `EFN|RTL|FNB|FARM`, menghentikan job tersebut setiap run sampai baris diperbaiki (error di log, watermark tidak maju).
5. `loyalty_events` pada volume lama: kolom `company_code` ditambah dengan nilai `''` untuk baris lama; backfill = hapus baris `etl_watermark` job `loyalty_events` (ReplacingMergeTree men-dedup).
6. Belum diverifikasi: versi pin `urllib3==2.5.0` kompatibel dengan `minio 7.2.12`/`requests 2.32.4` (seharusnya); `clickhouse-connect 0.8.9` argumen `verify`/`ca_cert`; perilaku sqlglot 26.0.0 untuk `x IN tabel` dan CTE skalar ClickHouse (`scalar=True`); GRANT kolom + `FINAL` dengan hak INVOKER; `CREATE QUOTA … KEYED BY user_name` dan `readonly = 2 CONST` di 24.8.
7. Mutual TLS tidak dipakai; autentikasi klien tetap password per service. *(Usang — lihat Gelombang 5: mTLS Ollama & metrik.)*

---

## Gelombang 5 — SEC-27, erasure ClickHouse, TTL, mTLS, grant Odoo (5 Okt 2026)

Kontrak: docs/04 v3 §8.4, §8.5, §4, §7; review r2 SEC-11/20/27; QA G4 U1. **Tidak ada yang dijalankan** (tanpa Python/Docker/ClickHouse).

### G5.1 File
| File | Perubahan |
|---|---|
| `services/ai-ocr-worker/efn_ocr/tls.py`, `services/etl/efn_etl/tls.py` | SEC-27: `is_production` fail-closed — production kecuali `APP_ENV` (dan `NODE_ENV` bila di-set) ∈ {dev, development, local, test, ci}; kosong/`staging`/tak dikenal = production → `EFN_ALLOW_PLAINTEXT` ditolak. ETL + `client_ssl_context` |
| `efn_ocr/config.py`, `vlm.py`, `consumer.py` | mTLS ke Ollama: `OLLAMA_CLIENT_CERT_FILE/KEY_FILE` → `requests.Session.cert`; wajib di production bila VLM aktif + https |
| `efn_ocr/metrics.py`, `__main__.py`, `healthcheck.py` | `METRICS_TLS_CLIENT_CA_FILE` → `start_http_server(client_cafile, client_auth_required=True)`; wajib di production; healthcheck membawa sertifikat worker sebagai klien |
| `services/etl/efn_etl/erasure.py` (baru) | job `privacy_erasure` (logika murni): baca request, count → `ALTER TABLE ... DELETE` → verifikasi 0 → publish; builder SQL whitelist + validasi hash |
| `efn_etl/amqp.py` (baru) | publisher pika amqps, `user_id=efn-etl`, confirms + `mandatory`, tanpa deklarasi queue |
| `efn_etl/config.py`, `adapters.py`, `runner.py`, `jobs.py`, `__main__.py` | config AMQP/erasure; `PgSource.query`, `ClickHouseErasureStore` (`mutations_sync=2`); `Job.identity_field` + pengecualian hash yang sedang dihapus; SQL `sales_fact_pos` ke kolom Odoo 19.0 |
| `services/etl/requirements.txt` | + `pika==1.3.2` |
| `services/etl/sql/odoo-readonly-grants.sql` (baru) | grant kolom minimum DB `efn_odoo` + index `(write_date, id)` (U1) |
| `infra/clickhouse/access/etl-access.sql` | + `GRANT ALTER DELETE` pada `competitor_prices`, `loyalty_events`; profil `allow_ddl = 1 CONST` (lihat risiko 1), `max_execution_time MAX 900`, `ALTER SETTINGS PROFILE` untuk profil lama |
| `infra/clickhouse/clickhouse-init.sh` (milik devsecops, ditambah satu blok) | TTL mart `CH_MART_RETENTION_YEARS` (default 5, 1..30) + `ttl_only_drop_parts=1`, `materialize_ttl_after_modify=0` |
| tests | worker `test_tls.py`, `test_config.py` (SEC-27, mTLS); ETL `test_config.py` (SEC-27, AMQP), `test_erasure.py` (baru: kontrak payload, idempotensi, verifikasi, isolasi error, SQL whitelist/injeksi, runner exclusion) |
| README worker & ETL | env baru, erasure, retensi, grant Odoo |

### G5.2 Env & secret baru (devsecops: compose + gen-secrets)
**etl** — secret baru `rabbitmq_password_etl`; network yang menjangkau `rabbitmq` (`data`).
| Env | Nilai produksi |
|---|---|
| `AMQP_URL` | `amqps://efn-etl@rabbitmq:5671` (default) |
| `RABBITMQ_PASSWORD_FILE` | `/run/secrets/rabbitmq_password_etl` |
| `AMQP_TLS_CA_FILE` | `/run/secrets/efn_ca_crt` (default) |
| `ETL_ERASURE_ENABLED` / `ETL_ERASURE_BATCH_SIZE` / `ETL_ERASURE_MUTATION_TIMEOUT_SECONDS` | `true` / `100` / `600` |
| `APP_ENV` | `production` (kosong pun kini production) |

**ocr-worker** — secret `tls_ocr_worker_crt/key` (sudah ada) dipakai juga sebagai sertifikat klien (EKU client sudah ada di gen-pki.sh).
| Env | Nilai produksi |
|---|---|
| `OLLAMA_CLIENT_CERT_FILE`, `OLLAMA_CLIENT_KEY_FILE` | `/run/secrets/tls_ocr_worker_crt`, `/run/secrets/tls_ocr_worker_key` |
| `METRICS_TLS_CLIENT_CA_FILE` | `/run/secrets/efn_ca_crt` |
Tanpa ketiganya worker **gagal start** di production. Sidecar `ollama-tls`: ganti `--disable-authentication` dengan `--allow-cn=ocr-worker` (+ text2sql nanti). Prometheus job `ocr-worker`: `tls_config.cert_file/key_file` (sertifikat prometheus).

**clickhouse-init** — env opsional `CH_MART_RETENTION_YEARS` (default 5; tunggu DPO).

### G5.3 Kebutuhan grant/kontrak untuk pihak lain
- **BFF / pemilik `platform.dsar_request` (PostgreSQL):** `GRANT SELECT (id, member_hash, status, stores_pending) ON platform.dsar_request TO efn_etl;` (kolom di WHERE juga butuh SELECT). Nilai status persis `processing`; store persis `clickhouse` di `stores_pending text[]`.
- **BFF (urutan):** tombstone/rewrite hash di `price_hunter_report.user_hash` dan `loyalty_ledger.member_ref_hash` (PDP_DSAR_DESIGN §5) **sebelum atau bersamaan** dengan set `processing`, dan hapus `clickhouse` dari `stores_pending` hanya setelah menerima `privacy.erasure.completed`. ETL hanya mengecualikan hash selama request `processing`; setelah itu baris sumber yang masih ber-hash asli akan dimuat ulang pada overlap/backfill.
- **BFF:** mendeklarasikan queue `privacy.erasure.completed` (+ `.dlq`, argumen §4) dan menerima hanya `user_id = efn-etl` untuk `store=clickhouse`. ETL bisa mem-publish ulang pesan dengan idempotency key sama tiap siklus sampai BFF memperbarui `stores_pending` → dedup `processed_message` wajib.
- **devsecops (RabbitMQ):** user `efn-etl`, vhost `/`: configure `^$`, write `^amq\.default$` (izin write dicek pada exchange default), read `^$`.
- **devsecops (Odoo DB):** jalankan `services/etl/sql/odoo-readonly-grants.sql` sebagai `efn_odoo` setiap deploy setelah install/upgrade Odoo; baru kemudian `ETL_ENABLE_ODOO=true`.
- **devsecops (ClickHouse):** tidak ada user baru; `etl-access.sql` sudah dijalankan otomatis oleh `clickhouse-init.sh`.

### G5.4 Kolom Odoo 19.0 (diverifikasi terhadap sumber GitHub 19.0, ringkasan WebFetch)
`pos_order`: `config_id` kini **stored** (join `pos_session` dihapus), `state` = `draft|cancel|paid|done` (`invoiced` dihapus dari filter), `pos_reference` bisa NULL sebelum sync → `COALESCE(pos_reference, name)`. `pos_order_line`: `full_product_name, qty, price_unit, discount, price_subtotal, price_subtotal_incl, total_cost` ada. `product_category.complete_name` stored. Verifikasi berasal dari ringkasan model kecil atas file sumber — skrip grant gagal keras (`ON_ERROR_STOP`) bila kolom tidak ada, jadi deploy pertama adalah verifikasi definitifnya.

### G5.5 Risiko / belum terverifikasi
1. **`allow_ddl`:** saya menyimpulkan ClickHouse 24.8 menganggap `ALTER DELETE` sebagai DDL (diblok `allow_ddl=0`) — belum dicek. Profil dilonggarkan ke `allow_ddl=1`; batas nyata = GRANT. Alternatif bila ditolak reviewer: user `efn_etl_erasure` terpisah.
2. `mutations_sync=2` + `max_execution_time` pada `client.command(settings=...)` di clickhouse-connect 0.8.9; mutation yang timeout akan diterbitkan ulang di siklus berikutnya (mutation duplikat tidak berbahaya, hanya boros).
3. Hash disisipkan sebagai literal (regex `^[0-9a-f]{64}$`), bukan parameter server-side, karena dukungan parameter pada mutation belum terverifikasi.
4. `fact_sales_line.customer_id` (id `res.partner`) tidak dihapus: setelah Odoo menganonimkan partner, id tidak lagi terhubung ke orang. Perlu konfirmasi DPO; bila harus dihapus, ETL butuh SELECT `(id, efn_member_hash)` pada `res_partner`.
5. Request > `ETL_ERASURE_BATCH_SIZE` yang `processing` bersamaan tidak ikut dikecualikan pada siklus itu (urut `id`).
6. TTL: `MODIFY TTL` dengan `materialize_ttl_after_modify=0` dan `toDateTime(created_at)` untuk `DateTime64` belum diuji di 24.8; baris lama yang sudah kedaluwarsa baru terbuang saat merge berikutnya (`merge_with_ttl_timeout`, default 4 jam).
7. Metrik worker: prometheus-client tidak bisa membatasi CN/SAN klien — setiap sertifikat dari CA internal diterima (CA satu tingkat, SEC-23). Kontrol CN penuh butuh sidecar ghostunnel `--allow-cn=prometheus`.
8. `CREATE INDEX CONCURRENTLY IF NOT EXISTS`: bila pembuatan pertama gagal, index INVALID tertinggal dan tidak dibuat ulang otomatis (cek `pg_index.indisvalid`).
9. Semua test belum dijalankan.

---

## Gelombang 5b — topologi milik broker, verifikasi mTLS Ollama (5 Okt 2026)

| File | Perubahan |
|---|---|
| `efn_ocr/publisher.py` | `declare_topology` dihapus → `verify_topology` (`queue_declare(passive=True)`) hanya untuk `ocr.requested.dlq` + `ocr.requested`; 404 → `TopologyMissingError` dengan pesan jelas; error broker lain diteruskan |
| `efn_ocr/consumer.py` | memakai `verify_topology`; `TopologyMissingError` → log error + `SystemExit(2)` (bukan loop reconnect) |
| `efn_ocr/messages.py` | `queue_arguments` kini hanya acuan kontrak (docstring) |
| `tests/test_config.py` | passive-only & hanya queue konsumsi, gagal jelas per queue hilang (parametrize), error 403 diteruskan |
| `tests/test_tls.py` | `test_ollama_request_actually_carries_client_cert`: adapter transport palsu memastikan setiap `POST /api/generate` membawa `cert=(crt,key)` dan `verify=<CA internal>` (bukan hanya atribut session) |
| `README.md` worker | bagian topologi |

Hasil cek `vlm.py`: `OllamaVlm` menyetel `Session.cert = (crt, key)`; `requests` menggabungkan `Session.cert` ke setiap request (`merge_environment_settings`, tidak bergantung `trust_env`). `consumer.build_processor` meneruskan `OLLAMA_CLIENT_CERT_FILE/KEY_FILE` dari Settings; production gagal start tanpa keduanya.

Untuk devsecops: user `efn-ocr` cukup configure `^$`, read `^ocr\.requested$`, write `^amq\.default$` (retry publish ke `ocr.requested` + publish loyalty/notify lewat default exchange). Belum diverifikasi: bahwa passive `queue.declare` di RabbitMQ 3.13/4.x tidak memerlukan izin `configure` (perkiraan saya tidak; bila ternyata perlu, berikan configure `^ocr\.requested(\.dlq)?$`). ETL sejak Gelombang 5 tidak mendeklarasikan queue apa pun.
