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

## Gelombang 12 — VLM performa, salinan audit tamper-evident, mart konsolidasi GL (7 Okt 2026)

### 1. VLM (services/ai-ocr-worker)
- `imaging.encode_for_vlm` (sisi maks `OCR_VLM_MAX_SIDE`=1024, JPEG q85, tidak pernah upscale; trim tepi polos opsional `OCR_VLM_TRIM_BORDER`=false), dipanggil `processor` sebelum `vlm.extract` (gagal encode -> `flagged/vlm_unavailable`, bukan crash).
- `OllamaVlm`: `options.num_predict`=128, `num_ctx`=2048, `keep_alive`=30m (env `OLLAMA_NUM_PREDICT/NUM_CTX/KEEP_ALIVE`); `Settings` gagal start bila `num_ctx` tidak muat token gambar sisi maks + 200 + num_predict.
- Pengukuran dev (Ollama 0.35.1 CPU, qwen2.5vl:3b, 3 foto smoke baru per varian): **sebelum 66,7 s/foto, 1333 token prompt, 3/3 benar -> sesudah (default G12) 59,1 s/foto, 1166 token, 3/3 benar (-11 %)**. 768/512 px tidak lebih cepat (token tetap 1166: Ollama menormalkan ukuran gambar) dan 768 kehilangan sufiks nama (1/3). Kesimpulan: resize bukan solusi throughput; butuh GPU (README §Performa VLM: L4/RTX ≥ 8 GB, perkiraan 1–3 s/foto, target p95 < 10 s). Angka GPU belum diukur.
- `METRICS_BIND_ADDRESS` (permintaan lead): literal IP, default `0.0.0.0` bila HTTPS metrik, `127.0.0.1` bila plaintext dev; non-loopback tanpa TLS ditolak (`TlsConfigError`); diteruskan ke `start_http_server(addr=)`.
- Skenario `run-e2e-smoke.sh v` **belum dijalankan dengan kode baru** (butuh rebuild/recreate container ocr-worker; tidak dilakukan agar stack yang sedang dipakai tidak terganggu). Pengukuran dilakukan dengan skrip benchmark lewat `docker exec` ke Ollama yang sama (mTLS, payload identik).

### 2. Salinan audit tamper-evident (services/etl/efn_etl/audit.py, audit_job.py)
- Tabel ClickHouse (`infra/clickhouse/init/03-gl-audit.sql`): `audit_log_copy`, `audit_log_line_copy` (MergeTree biasa, tanpa partisi -> INSERT atomik), `audit_recon_run`.
- `payload_hash = sha256(canonical_row)` (JSON kanonik kolom log + `lines_digest`), `row_hash = sha256(prev_hash || payload_hash)` per `chain_company` (company log; 0 = tanpa company), `chain_seq`. Kolom yang berubah sah (write_*, FK set-null `model_id/rule_id/field_id`, kolom related baris) tidak di-hash. Kepala rantai dicatat `EFN_AUDIT_CHAIN_HEAD` (Loki = jangkar eksternal).
- Late commit: id ≤ max tersalin yang muncul dalam `ETL_AUDIT_LATE_WINDOW_SECONDS` disalin `ingest_kind=late`; log baru setelah `ETL_AUDIT_SETTLE_SECONDS`. Crash antara insert baris dan log tidak menggandakan baris.
- PII: log orang/is_personal -> `res_id`/`res_name` NULL; baris ter-mask atau ref ke model orang -> nilai/ref NULL (`redacted=1`), hanya hash. Kebijakan erasure: gap karena `_efn_audit_purge_records` sah bila ada log purge `erasure: N log` untuk model itu setelah log dibuat (dalam anggaran N); retensi sah bila umur ≥ 365 hari (dalam anggaran `retensi: N log`); log purge/model kontrol hilang = selalu `unexplained`. Gap sequence PostgreSQL (rollback) bukan indikasi. Kelas & alert: lihat `services/etl/README.md`.
- Rekonsiliasi harian -> `audit_recon_run` + log `EFN_AUDIT_RECON {json}` (WARNING bila `unexplained|modified|unseen|chain_problems` > 0). **devsecops**: rule Loki `{service="etl"} |= "EFN_AUDIT_RECON" |= "WARNING"` dan `|= "job=audit_copy gagal"`; opsional bandingkan `EFN_AUDIT_CHAIN_HEAD` antar hari.
- Retensi salinan: TTL `CH_AUDIT_COPY_RETENTION_YEARS` (default 10, clickhouse-init.sh) — usulan menunggu DPO/Q-49. Role `audit_copy_reader` dibuat, tidak diikat ke user (keputusan klien).

### 3. Mart konsolidasi GL (services/etl/efn_etl/gl.py)
- Kolom Odoo 19 diverifikasi dari DB dev (information_schema): `account_account.code_store` jsonb per company root, `name` jsonb, relasi `account_account_res_company_rel`; `account_move_line.parent_state`, `balance`, dll.
- Job `companies` -> `dim_company`, `gl_accounts` -> `dim_gl_account` (kode per company), `gl_lines` (cursor `account_move_line.write_date`) + `gl_lines_by_move` (cursor `account_move.write_date`) -> `fact_gl_line` (ReplacingMergeTree(version_ts), **partisi per company** karena tanggal draft bisa berubah). Semua state dimuat; view hanya `posted`. `partner_id` tidak disalin; `ic_company_id` = company grup lawan (partner = partner company lain).
- View `v_gl_consolidated` (Metabase): period, fiscal_year, company, akun, `is_intercompany`, `counterparty_company_id`, debit/credit/balance, `balance_after_elimination`. Tidak ditambahkan ke semantic layer Text-to-SQL (bebas PII, tetapi laporan keuangan lintas PT tanpa pembatasan per company -> Q-6); tabel baru masuk `protected_tables`.

### Grant yang dibutuhkan
- PostgreSQL (Odoo, `services/etl/sql/odoo-readonly-grants.sql`, dijalankan deploy sebagai `efn_odoo` **setelah `-u efn_audit` ke 19.0.1.2.0** karena `origin_user_id`): SELECT kolom `account_move_line`, `account_move`, `account_account`, `account_account_res_company_rel`, `res_company` (+partner_id, parent_id, parent_path, currency_id, fiscalyear_*, write_date), `res_currency(id,name)`, `efn_audit_log`, `efn_audit_log_line`; index `CONCURRENTLY` `(write_date, id)` pada `account_move_line` dan `account_move`.
- ClickHouse (`etl-access.sql`): `efn_etl` SELECT+INSERT `fact_gl_line`, `dim_gl_account`, `dim_company`, `audit_log_copy`, `audit_log_line_copy`, `audit_recon_run`; REVOKE defensif ALTER DELETE/UPDATE, TRUNCATE, DROP TABLE, OPTIMIZE pada tabel audit. `metabase-access.sql`: `v_gl_consolidated` + tabel GL + `audit_recon_run`. Role `audit_copy_reader`.
- Env baru ETL: `ETL_ENABLE_GL`, `ETL_AUDIT_COPY_ENABLED` (default false), `ETL_AUDIT_*`. Compose belum diubah (milik devsecops): aktifkan dua flag di service `etl` setelah grant diterapkan.

### Validasi
- pytest (python:3.12-slim digest Dockerfile): ai-ocr-worker 184 lulus, etl 94 lulus, text2sql 93 lulus; ruff 0.16.10 check + format bersih (ketiga service).
- Stack dev: `clickhouse-init` dijalankan ulang (`compose run --rm --no-deps`, idempoten) -> 03-gl-audit.sql, TTL, grant diterapkan; grant PG diterapkan ke DB dev (tanpa kolom `origin_user_id` karena efn_audit dev masih 19.0.1.1.0). ETL baru dijalankan sekali di dalam container `efn-etl-1` (kode lewat /tmp, SQL `origin_user_id` diganti `NULL` untuk dev): `dim_company` 5, `dim_gl_account` 523, `fact_gl_line` 0 (DB dev belum punya jurnal), `audit_log_copy` 20 log (2 rantai: company 0 seq 14, company 4 seq 6), baris 21 (8 diredaksi, 0 nilai bocor), rekonsiliasi 0 anomali; run kedua: 0 baris baru, kepala rantai identik. `efn_etl` mencoba ALTER DELETE / DELETE / TRUNCATE / ALTER UPDATE / DROP / OPTIMIZE pada tabel audit -> semua ditolak (497 ACCESS_DENIED). SQL view `v_gl_consolidated` dieksekusi (0 baris).
- Catatan: Docker Desktop berhenti di tengah sesi dan dinyalakan ulang oleh agent ini (container kembali otomatis); tidak ada `down`/recreate service.

### Belum terverifikasi
- GL dengan jurnal nyata (posted/cancel, inter-company, tahun fiskal non-Desember); grant Metabase pada view (diuji hanya sebagai `efn_etl` untuk SQL body).
- Kolom `origin_user_id` di DB nyata (menunggu upgrade efn_audit 19.0.1.2.0).
- Skenario tamper nyata di PostgreSQL (hanya unit test) dan alert Loki (rule belum dibuat devsecops).
- `run-e2e-smoke.sh v` dengan image ocr-worker baru; angka GPU.

---

## Gelombang 13 — Mart Fase 1, 3 dashboard Metabase sebagai kode, heartbeat ETL (7 Okt 2026)

Konteks: docs/01 §7 Fase 1 "ETL dasar + 3 dashboard Metabase" (daftar final dashboard C-level = Q-47, masih terbuka), SEC-52 butir 1 (dead-man), tambahan lead: verifikasi sink audit v2 (HMAC).

### 1. Mart (infra/clickhouse/init/04-fase1-marts.sql, services/etl)
| Bagian | Tabel / job | View (semantic layer Metabase) |
|---|---|---|
| (a) Penjualan POS | `fact_sales_line` + `order_id`, `is_refund`, `line_kind` (product/discount/reward/points/tip), `discount_amount`; job baru `pos_payments` → `fact_pos_payment`, `pos_outlets` → `dim_outlet`, `products` → `dim_product` (`efn_etl/pos.py`) | `v_pos_sales_daily` (hari×PT×outlet×kategori: kotor, diskon baris, promo, poin EFN, refund, bersih, pajak, HPP, qty), `v_pos_outlet_daily` (order, basket), `v_pos_sales_hourly`, `v_pos_payment_daily` |
| (b) Stok & kedaluwarsa | job `stock_snapshot` (`efn_etl/stock.py`): snapshot penuh quant internal + lot + orderpoint dalam satu transaksi REPEATABLE READ, penanda `stock_snapshot_run` ditulis terakhir; `fact_stock_snapshot`, `fact_orderpoint_snapshot` | `v_stock_daily`, `v_stock_current`, `v_stock_expiring` (≤ 7 hari, termasuk yang lewat), `v_stock_below_reorder` |
| (c) Price Hunter | `dim_product.list_price` + `competitor_prices` | `v_price_index` (match SKU atau nama dinormalisasi; indeks vs harga jual & vs harga POS 30 hari), `v_price_hunter_match_rate` |
| Heartbeat | `etl_job_run` (TTL 90 hari) | `v_etl_freshness` |

Mengapa snapshot stok (bukan incremental): Odoo menghapus quant ber-qty nol, jadi incremental `write_date` tidak pernah melihat stok habis. Kolom Odoo 19 diverifikasi lewat `information_schema` DB dev (product_expiry + pos_loyalty terpasang). Backfill `fact_sales_line` di dev: watermark epoch → 84 baris dimuat ulang (prosedur di README ETL). TTL mart 5 tahun juga untuk tabel baru (`clickhouse-init.sh`). **Kontrak baru untuk Odoo engineer:** produk "Poin EFN" (docs/04 §9.3) memakai `default_code = EFN-POINTS` (atau set `ETL_POINTS_PRODUCT_CODES`).

### 2. Grant baru
- **Odoo** (`sql/odoo-readonly-grants.sql`, kolom per kolom; diterapkan ke DB dev sebagai `efn_odoo` → OK): `pos_order_line(refunded_orderline_id, is_reward_line)`, `pos_order(is_refund)`, `pos_payment(id, pos_order_id, payment_method_id, amount, is_change, payment_date, write_date)` — TANPA card_no, cardholder_name, card_brand/type, transaction_id, payment_ref_no, authcode, issuer_bank, ticket; `pos_payment_method(id, name, payment_method_type, is_cash_count)`; `pos_config(id, name, company_id, warehouse_id, discount_product_id, tip_product_id, active, write_date)`; `product_product(+default_code, active, standard_price, write_date)`; `product_template(+name, company_id, list_price, type, is_storable, tracking, use_expiration_date, sale_ok, available_in_pos, active, write_date)`; `stock_quant(id, company_id, location_id, product_id, lot_id, quantity, reserved_quantity, in_date)` — TANPA owner_id/user_id; `stock_lot(id, name, expiration_date)`; `stock_location(id, complete_name, usage, warehouse_id)`; `stock_warehouse(id, name)`; `stock_warehouse_orderpoint(id, company_id, warehouse_id, location_id, product_id, product_min_qty, product_max_qty, trigger, active)`; index `efn_etl_pos_payment_write_date_id_idx`.
- **ClickHouse:** `etl_writer` SELECT+INSERT pada 7 tabel baru; `metabase_reader` SELECT 11 view baru + tabel dasarnya (tanpa identitas) + 4 kolom baru `fact_sales_line` (tetap tanpa `customer_id` — diverifikasi ditolak 497; INSERT juga 497). Semantic layer Text-to-SQL TIDAK diubah (di luar batas file).

### 3. Dashboard (infra/metabase) — diuji di stack dev
`provision.py` (stdlib, idempoten; dijalankan 2× tanpa duplikat; 28/28 kartu `completed`; filter outlet/tanggal/PT diuji via API). Koneksi ClickHouse `efn_metabase`: HTTPS 8443 + `sslmode=strict` + CA internal (CA salah → "Failed to create connection", diverifikasi). Skrip → Metabase lewat Traefik `bi.efn.local` (HTTPS terverifikasi; CA server salah → gagal). Driver ClickHouse resmi 1.52.0 (Apache-2.0) dipin sha256 di `infra/metabase/Dockerfile`.

Kartu (semua SQL native ke `efn_dwh.v_*`; validator menolak relasi lain/DDL/fungsi tabel):
- **Penjualan Harian Outlet** (filter: dari/sampai tanggal, PT nama/kode brand, outlet): Penjualan bersih, Jumlah order, Rata-rata nilai belanja, Retur/refund (scalar, `v_pos_outlet_daily`); Tren penjualan bersih harian per outlet (line); Metode pembayaran (pie, `v_pos_payment_daily`); Penjualan per kategori (row, `v_pos_sales_daily`); Pola jam WIB (bar, `v_pos_sales_hourly`); Diskon/promo/poin EFN/refund per hari (bar stacked, `v_pos_sales_daily`); Ringkasan outlet harian (table); Kesegaran data ETL (`v_etl_freshness`).
- **Stok & Kedaluwarsa** (filter: tanggal, PT, gudang/outlet): Nilai stok saat ini, Lot kedaluwarsa ≤ 7 hari, Produk di bawah titik pesan ulang (scalar); Produk mendekati kedaluwarsa (table, `v_stock_expiring`); Stok di bawah titik pesan ulang (table, `v_stock_below_reorder`); Nilai stok harian per gudang (line, `v_stock_daily`); Stok saat ini per produk & gudang (table, `v_stock_current`); Kesegaran data ETL.
- **Price Hunter** (filter: tanggal, pesaing, produk): Laporan valid (`v_price_hunter_quality`), Indeks harga rata-rata (`v_price_index`), Laporan valid termatch % (`v_price_hunter_match_rate`) (scalar); Indeks harga per produk & pesaing (table); Indeks rata-rata per pesaing (row); Tren indeks harian (line); Kualitas laporan per status (bar stacked); Produk dilaporkan belum termatch (table, `v_competitor_prices` minus `v_price_index`); Kesegaran data ETL.

Data dev tambahan (Odoo company 4, lewat `odoo shell`): produk `ETL-DEV-UHT` (lot ETLDEV-L1 kedaluwarsa +3 hari, L2 +60 hari, orderpoint min 50/max 120) dan `ETL-DEV-INDOMIE` "Indomie Goreng" (match laporan Price Hunter smoke → indeks 94,6).

### 4. Heartbeat (SEC-52 butir 1) — nama metrik untuk devsecops
Log Loki `service="etl"`, satu baris logfmt per job per siklus (INFO bila ok, WARNING selain itu), plus baris `efn_dwh.etl_job_run`:
`EFN_ETL_HEARTBEAT job=<job> status=<ok|error|partial> rows=<n> pages=<n> duration_ms=<n> watermark=<iso|-> ts=<iso>`.
Job: `cycle` (loop hidup), `competitor_prices`, `loyalty_events`, `sales_fact_pos`, `pos_outlets`, `products`, `pos_payments`, `companies`, `gl_accounts`, `gl_lines`, `gl_lines_by_move`, `stock_snapshot`, `audit_copy`, `audit_recon`, `privacy_erasure`. Nama job divalidasi `^[a-z0-9_]{1,64}$`. Usulan alert: `absent_over_time({service="etl"} |= "EFN_ETL_HEARTBEAT" | logfmt | job="<job>" | status="ok" [<ambang>])` — cycle 45m, sales_fact_pos 45m, audit_copy 1h (prod), stock_snapshot 3h, audit_recon 26h; plus `sum by (job) (count_over_time({service="etl"} |= "EFN_ETL_HEARTBEAT" | logfmt | status!="ok" [1h])) >= 2`.

### 5. Sink audit v2 (tambahan lead)
`efn_etl/sink.py` + 16 test: verifikasi `mac` (kanonik identik `efn_audit.models.base.sink_canonical`) dan rekonsiliasi → `sink_mac_invalid`, `sink_missing_log`, `log_missing_sink`. **Belum di-wire ke `EFN_AUDIT_RECON`.** Spesifikasi wiring: (1) devsecops mount secret `efn_audit_sink_key` ke `etl` + env `EFN_AUDIT_SINK_KEY_FILE`, sertifikat klien `etl` yang di-allow `loki-tls`, akses network observability; (2) ETL membaca `GET /loki/api/v1/query_range?query={service="odoo"} |= "EFN_AUDIT_SINK"` per jendela recon (paging `limit`/`start`, ≤ retensi stream sink); (3) PG: `efn_audit_log` model kontrol + `purge` di jendela yang sama (grant sudah ada); (4) hitungan digabung ke payload `EFN_AUDIT_RECON` + kolom baru `audit_recon_run`; WARNING bila > 0. Kunci belum ada di Odoo → `mac` null → semua baris `sink_mac_invalid` (sengaja tidak diam).

### 6. Validasi
- `services/etl`: ruff check + format bersih; pytest 154 lulus (container python:3.12-slim). `infra/metabase`: ruff bersih; pytest 23 lulus.
- `clickhouse-init` dijalankan ulang di dev (semua skema/akses OK); image ETL dibangun ulang; satu siklus penuh: semua heartbeat `status=ok`.

### 7. Belum / perlu pihak lain
- **devsecops:** compose `metabase` → `build: ../metabase` (driver ClickHouse; di dev jar disalin manual dengan `docker cp` dan hilang saat recreate); secret `metabase_admin_password` di `scripts/secrets.list` (dev: dibuat lokal `secrets/metabase_admin_password.txt`); langkah `provision.py` di deploy segera setelah Metabase pertama hidup (instance yang belum di-setup bisa diklaim siapa pun di subnet staf); alert heartbeat; job CI untuk `infra/metabase/tests`. Catatan: `docker restart efn-metabase-1` membuat sidecar `metabase-tls` (network_mode service) unhealthy, jadi sidecar juga harus di-restart.
- Q-47 (dashboard C-level), Q-54 (SSO/password Metabase), izin per grup pada collection.
- WITA/WIT per outlet belum dibedakan; reorder memakai on-hand (bukan forecast Odoo); filter berupa teks (bukan dropdown).
- View baru belum masuk semantic layer Text-to-SQL.

## Gelombang 14 — Rekonsiliasi loyalty ↔ pos.order (sisa SEC-61), wiring verifikasi sink + jangkar (SEC-51/52), dashboard refund poin (8 Okt 2026)

Tanpa commit. Tidak ada volume yang dihapus.

### File
- Baru: `services/etl/efn_etl/loki.py` (klien `query_range` stdlib, mTLS CN `etl`, paginasi aman batas timestamp,
  `truncated` dilaporkan), `services/etl/efn_etl/loyalty_recon.py`, `infra/clickhouse/init/05-loyalty-recon.sql`,
  `tests/test_loki.py`, `tests/test_loyalty_recon.py`.
- Diubah: `sink.py` (jangkar `EFN_AUDIT_CHAIN_HEAD` + orkestrasi `run_sink_check`), `audit_job.py` (hasil sink digabung ke
  `EFN_AUDIT_RECON` + kolom `audit_recon_run`; Loki gagal = `sink_error=1`, WARNING), `config.py`, `adapters.py`,
  `__main__.py`, `sql/odoo-readonly-grants.sql` (bagian G14), `infra/clickhouse/init/04-fase1-marts.sql`
  (`refund_goods`, `points_returned_idr` di `v_pos_sales_daily`/`v_pos_outlet_daily`), `access/etl-access.sql`,
  `access/metabase-access.sql`, `infra/metabase/dashboards/01-penjualan-harian-outlet.json`, README ETL, tes.

### Kontrak untuk devsecops (nama baris / alert yang diusulkan)
- Baris loyalty: `<YYYY-MM-DD HH:MM:SS,mmm> (INFO|WARNING|ERROR) efn_etl.loyalty_recon EFN_LOYALTY_RECON_ETL {"status":"ok"|"error",...}`
  dari `{service="etl"}`. Usul promtail: stream `efn_loyalty`, label `loyalty_event="etl_recon"` (regex berjangkar,
  kunci pertama `"status"`). Field: `anomalies` (critical), `open_items` (warning), `kinds{kind:n}`, `sample_order_ids`,
  `sample_ledger_ids` (angka saja). Alert: `| json | anomalies > 0` → critical (Finance); `open_items > 0` → warning
  harian; dead-man `job="loyalty_recon" status="ok"` absen 26 jam (heartbeat `EFN_ETL_HEARTBEAT`).
- `EFN_AUDIT_RECON` tetap diawali `{"max_log_id":` (regex promtail lama cocok); field baru `sink_error`, `sink_*`,
  `anchor_*`. Alert lama (WARNING) sudah menangkap anomali sink/jangkar.
- Compose dev: tambahkan `ETL_LOYALTY_RECON_NOT_BEFORE` (go-live loyalty POS; dev `2026-10-07T07:09:00Z`) — tanpa itu
  42 order smoke lama (sebelum `efn_pos_loyalty_ledger` dipasang) muncul sebagai `paid_member_order_without_earn`.
- `deploy.sh`: grant G14 harus dijalankan setelah `efn_pos_loyalty_ledger` terpasang (ON_ERROR_STOP).
- Temuan promtail: tahap redaksi nomor HP berjalan SEBELUM tahap sink `efn_audit_sink` → sink yang memuat pola
  `08xxxxxxxx` (mis. di `note`) akan rusak dan terhitung `sink_mac_invalid`. Usul: cocokkan sink sebelum redaksi
  (seperti jangkar; sink tidak memuat nilai).

### Uji di stack dev (8 Okt)
- pytest ETL **192 passed**, metabase **23 passed**; `ruff check` + `ruff format --check` bersih (container python:3.12).
- Grant G14 diterapkan ke DB dev (sebagai `efn_odoo`); `clickhouse-init` ulang sukses (05 + access).
- Siklus ETL (`ETL_RUN_ONCE`, recon dipaksa): Loki mTLS berhasil. Tanpa cutoff: `earn_without_paid_order` 30.326 =
  earn load test BFF tanpa order Odoo (terdeteksi benar; 22 earn asal Odoo cocok dengan 22 outbox `done`),
  `paid_member_order_without_earn` 42 = order sebelum modul loyalty dipasang. Dengan `NOT_BEFORE`: **anomalies 0**,
  open_items 3 (2 order `review` — earn_limit & retur tanpa struk dari smoke G13c, 1 outbox `failed` earn_limit).
- Anomali sengaja: baris Poin EFN order paid #145 diubah −6.000→−9.000 → `points_line_value_mismatch` 1 (WARNING),
  lalu dikembalikan (−6.000, diverifikasi); run bersih terakhir anomalies 0.
- Recon audit: `sink_mac_invalid` 3 = baris sink injeksi QA (log_id 990001/990101/990102, mac null/tanpa mac);
  `log_missing_sink` 5 = log `purge` erasure res.partner id 39/46/55/62/71 yang dibuat lewat `odoo shell`
  (`docker exec`, stdout tidak tertangkap promtail) — deteksi benar; `anchor_missing` 1 = jangkar palsu injeksi QA
  (company 990). Setelah jendela 25 jam lewat, sink 0 anomali; 20 jangkar cocok.
- ClickHouse: `v_pos_outlet_daily` 7 Okt refund uang 341.000 = barang 350.000 − poin dikembalikan 9.000.
  `metabase-provision` exit 0, semua kartu termasuk 5 kartu baru `completed`.
- `efn-etl-1` di-recreate dengan image baru (healthy/starting).

### Belum / asumsi
- Saldo negatif per member, nominal restore/clawback refund (pembulatan BFF), dan earn yang dicatat > lookback setelah
  order belum dicek. Admin `base.group_system` memanggil `_efn_post` (SEC-61 butir 3) tidak tertutup job ini, hanya
  terdeteksi bila tidak ada order Odoo yang cocok.
- Promtail stream/alert untuk `EFN_LOYALTY_RECON_ETL` belum ada (devsecops). Jangkar tidak ber-HMAC (alarm palsu
  mungkin, penyembunyian tidak). CI GitHub belum dijalankan.

## Gelombang 16 — pip-audit CI merah: Pillow 12, requests, urllib3, pytest (9 Okt 2026)

**Pemicu:** job CI `Python (ruff, compile, pip-audit)` gagal di `services/ai-ocr-worker/requirements.txt`
(47 temuan di 3 paket). Audit ulang semua requirements di container menemukan juga pytest (dev) dan `services/odoo`.

### Versi
| File | Paket | Lama | Baru | Advisory |
|---|---|---|---|---|
| ai-ocr-worker/requirements.txt | Pillow | 11.3.0 | **12.3.0** | PYSEC-2026-165, -2249..-2257, -2874, -3451/3453/3454, -3493..-3496 |
| ai-ocr-worker/requirements.txt | requests | 2.32.4 | **2.34.2** | PYSEC-2026-2275 (fix 2.33.0) |
| ai-ocr-worker/requirements.txt | urllib3 | 2.5.0 | **2.8.0** | PYSEC-2026-141, -1994, -1996, -1998, -4175, -4177 |
| {ai-ocr-worker,etl,text2sql}/requirements-dev.txt | pytest | 8.3.4 | **9.0.3** | PYSEC-2026-1845 |

etl & text2sql runtime: tidak ada temuan. Dependensi transitif terpasang: ImageHash 4.3.1, numpy 2.5.3, scipy 1.18.1, PyWavelets 1.10.0, minio 7.2.12.

### Perubahan kode
Tidak ada. `efn_ocr/imaging.py` sudah memakai `Image.Resampling.LANCZOS` (konstanta lama `Image.ANTIALIAS`/`LANCZOS`
top-level tidak dipakai); `Image.open`, `ImageOps`, `ImageChops`, `DecompressionBombError`, `MAX_IMAGE_PIXELS` kompatibel
Pillow 12. Klien urllib3 MinIO (`PoolManager`, `Timeout`, `Retry`, `cert_reqs`/`ca_certs`) dan `requests.Session` kompatibel.

### Validasi (container `python:3.12-slim@sha256:05cda977…`, digest Dockerfile terbaru, Python 3.12.15)
`ruff==0.16.10 check` + `format --check` bersih · `compileall` OK · pytest: ai-ocr-worker **184 passed**, etl **192 passed**,
text2sql **93 passed** · `pip-audit -r requirements.txt --strict` dan `-r requirements-dev.txt --strict`: **No known vulnerabilities** untuk ketiga service.

### Sisa (di luar batas file saya)
- `services/odoo/requirements.txt`: `requests==2.32.3` rentan (PYSEC-2026-1872 fix 2.32.4, PYSEC-2026-2275 fix 2.33.0) →
  job pip-audit CI akan tetap merah setelah ai-ocr-worker lolos. Perlu dinaikkan ke `requests==2.34.2` oleh pemilik Odoo
  (cek kompatibilitas dengan urllib3 bawaan image odoo:19.0).
- Test OCR end-to-end dengan Tesseract/PaddleOCR + Pillow 12 di image runtime belum dijalankan (hanya unit test).

## Gelombang 16b — Upgrade Metabase 0.52.4 → v0.58.35 LTS di stack dev (9 Okt 2026)

**Pemicu:** user menyetujui upgrade (gate Trivy G16). Dockerfile sudah di v0.58.35 (driver ClickHouse bawaan) sejak 7c048b4.

### Langkah & hasil nyata
1. **Backup** DB aplikasi `metabase`: `pg_dump -Fc` di container postgres → scratchpad sesi (di luar repo)
   `metabase-backup/metabase-pre-v0.58.dump` (514 081 byte, sha256 `1b23087d…fc5e48`, sama di container & host);
   `pg_restore --list` terbaca (1057 baris). Isi sebelum migrasi: 440 changeset Liquibase, 70 kartu, 4 dashboard.
   Image lama di-tag `efn/metabase:rollback-v0.52.4` (v0.52.4 + jar driver 1.52.0).
2. **Build + recreate** `metabase` + `metabase-tls` (`--no-deps --force-recreate`): Liquibase "Migration complete in
   14.1 s", "Initialization COMPLETE in 24.2 s", healthy; health check koneksi lama `EFN DWH (ClickHouse)` sukses.
   Satu-satunya ERROR = `/api/health 503` sesaat sebelum init selesai; WARN = penghapusan job Quartz lama (normal).
3. **Driver bawaan** = plugin `Metabase ClickHouse Driver 1.53.3` (`metabase/clickhouse/metabase-plugin.yaml` di
   metabase.jar). Perbedaan kunci `details`: `scan-all-databases` **dihapus**, diganti `enable-multiple-db` (default
   TRUE = sinkron semua database selain system). `ssl`, `dbname`, `additional-options` (ditempel ke URL JDBC) tetap.
   → `provision.clickhouse_details` kini mengirim `enable-multiple-db: False`. TLS tidak berubah: `ssl=true`,
   `sslmode=strict&sslrootcert=/run/secrets/efn_ca_crt`.
4. **Provision** (`$DC --profile core --profile data run --rm --no-deps metabase-provision`, python:3.12-bookworm digest
   compose, mTLS ke sidecar): exit 0, database diperbarui (id=2), 3 dashboard (16/8/9 kartu), **33/33 kartu
   `completed`**. Run kedua (idempoten) exit 0, tetap 33 kartu bertag + 4 dashboard aktif.
5. **Uji TLS** (skrip sekali pakai via `/api/database/validate`): CA internal → valid; CA sistem (salah) →
   "Failed to create connection". Detail tersimpan: `enable-multiple-db=false`, `ssl=true`; metadata hanya skema `efn_dwh`.
6. `tests/test_provision.py`: assert `enable-multiple-db is False` + `scan-all-databases` tidak dikirim. CI-equivalent
   (python:3.12-bookworm, ruff 0.16.10, pytest 8.3.4): ruff check/format bersih, **23 passed**.

### File diubah
`infra/metabase/provision.py` (details driver 1.53), `infra/metabase/tests/test_provision.py`,
`infra/metabase/README.md` (status terverifikasi, prosedur upgrade/backup/rollback, item "Belum" yang basi dihapus).

### Belum diverifikasi / risiko sisa
- Rollback (restore dump + image lama) **tidak diuji coba** — migrasi sukses sehingga tidak dijalankan.
- `MB_ENCRYPTION_SECRET_KEY` tidak diset → detail koneksi (password ClickHouse read-only) plaintext di DB aplikasi dan
  di dump backup; bertentangan dengan prinsip enkripsi at rest → devsecops (secret baru + entrypoint wrapper).
- Dump backup di scratchpad sesi tidak terenkripsi; hapus setelah upgrade dinyatakan stabil.
- Rendering UI dashboard di browser (filter, visualisasi) tidak dicek — hanya eksekusi kartu via API.
- Staging/prod belum di-upgrade; ikuti urutan README (backup dulu).

## Gelombang 18 — W-19 retensi data (ClickHouse TTL 10 th / pseudonim 5 th, lokasi Price Hunter 90 h, Metabase) (10 Okt 2026)

Sumber: `docs/10_RENCANA_GELOMBANG_18_PLUS.md` W-19, `docs/compliance/PDP_DATA_INVENTORY.md` v7 §0a/§3, keputusan
Q-6/Q-49 (DWH 10 th), Q-P7 (pseudonim), Q-P8 (lokasi 90 h), Q-P14 (riwayat Metabase 10 th).
**Penafsiran Q-P7 vs Q-6** = sama dengan usulan lead: *baris fakta 10 th, kolom pseudonim dikosongkan setelah 5 th*.
Tidak ada penafsiran berbeda.

### File
| File | Perubahan |
|---|---|
| `infra/clickhouse/clickhouse-init.sh` | Blok retensi (milik data-AI) ditulis ulang: `CH_MART_RETENTION_YEARS` default **10**; baru `CH_PSEUDONYM_RETENTION_YEARS` (5, 1..mart) dan `CH_GEO_RETENTION_DAYS` (90, 1..3650); TTL kolom `competitor_prices.user_hash/geo_lat/geo_lng`, `loyalty_events.member_hash`, `fact_sales_line.customer_id`; `MATERIALIZE TTL` sekali per tabel hanya bila definisi berubah (hash `create_table_query`); migrasi sekali `loyalty_events`. Blok salinan audit 10 th **tidak disentuh**. |
| `infra/clickhouse/init/02-loyalty-etl.sql` | `loyalty_events` (volume baru) `ORDER BY (event_id)` + skip index `bloom_filter` `member_hash` + `ttl_only_drop_parts=1`. |
| `services/etl/efn_etl/jobs.py` | `AgeOut` (lapisan kedua): saat transformasi, `geo_*` -> None setelah 90 h; `user_hash`/`member_hash` -> '' dan `customer_id` -> None setelah 5 th (validasi hash tetap fail-closed). Job dibangun lewat factory (`competitor_prices_job`, `loyalty_events_job`, `sales_fact_job`). |
| `services/etl/efn_etl/config.py`, `__main__.py` | Membaca `CH_PSEUDONYM_RETENTION_YEARS` (1..30), `CH_GEO_RETENTION_DAYS` (1..3650); di luar rentang = gagal start. |
| `services/etl/tests/test_jobs.py`, `test_config.py` | +13 test (batas 90 h / 5 th, 29 Feb, fail-closed pada baris lama, kebijakan custom, rentang env). Test lama kini memakai `now` tetap (tidak membusuk setelah Jan 2027). |
| `infra/metabase/provision.py` (+ `tests/test_provision.py`, +5 test) | `--deactivate-inactive-days` (default 90, 0 = mati) dan `--housekeeping-only`; `inactive_users()` murni; log hanya id (tanpa email). |
| `services/etl/README.md` §Retensi, `infra/metabase/README.md` §Retensi riwayat & akun tidak aktif | Dokumentasi. |
| `services/ai-ocr-worker/**` | **Tidak diubah**: worker hanya memakai `geo_*` saat memproses laporan (geofence, toleran NULL — `tests/test_fraud.py:38`) dan tidak menyimpannya. |

### Temuan teknis (diverifikasi di ClickHouse dev 24.8.14.39)
1. TTL kolom didukung pada tabel yang ada (`ALTER TABLE ... MODIFY COLUMN <kolom> <tipe SAMA> TTL ...`; tanpa tipe = syntax error karena `TTL` dibaca sebagai nama tipe). Tipe sama = perubahan metadata saja.
2. **TTL kolom ditolak untuk kolom kunci**: `Trying to set TTL for key column member_hash (ILLEGAL_COLUMN)` -> `loyalty_events` (dulu `ORDER BY (member_hash, event_id)`) dimigrasi ke `ORDER BY (event_id)`.
3. TTL kolom tetap berlaku dengan `ttl_only_drop_parts=1` (setting itu hanya untuk TTL baris). Insert baris yang TTL kolomnya sudah lewat **tidak** langsung dikosongkan (baru saat merge) -> alasan lapisan ETL `AgeOut`. Baris yang TTL barisnya sudah lewat dibuang saat insert.
4. Part lama tanpa info TTL tidak otomatis diproses -> `MATERIALIZE TTL` (mutation async) sekali saat definisi berubah.

### Uji nyata (stack dev)
- `clickhouse-init` (env `CH_MART_RETENTION_YEARS=10 CH_PSEUDONYM_RETENTION_YEARS=5 CH_GEO_RETENTION_DAYS=90` di-override karena `.env` dev masih 5): run 1 exit 0 — migrasi `loyalty_events` (30 440 event unik, `sum(delta_points)` 455 891 sama sebelum/sesudah; tabel lama di-DROP; ETL tetap jalan selama migrasi tanpa kehilangan baris), 7 tabel `MATERIALIZE TTL` (semua `is_done=1`). Run 2 exit 0: tanpa migrasi, tanpa mutation (idempoten).
- Grant setelah `EXCHANGE TABLES` utuh: `etl_writer` SELECT/INSERT/ALTER DELETE, grant kolom `text2sql_reader`/`metabase_reader` pada `loyalty_events`. `audit_log_copy` tetap `TTL ... + 10 YEAR`, tanpa TTL kolom.
- Baris uji sintetis: lokasi 100 h -> `geo_*` NULL & hash tetap; 6 th -> `user_hash`/`member_hash` ''; 11 th -> dibuang saat insert; 5 903 baris lokasi baru tetap. Baris uji dihapus.
- Image ETL dibangun ulang dan container di-recreate: heartbeat `competitor_prices`, `loyalty_events`, `sales_fact_pos`, `cycle` = ok.
- `metabase-provision` 2x exit 0 (33 kartu completed); akun uji `w19-probe@efn.local` dengan `date_joined` 120 h -> `--housekeeping-only` menonaktifkan (exit 0). Akun uji dibiarkan nonaktif di DB dev.
- pytest: ETL 205 passed / 1 skipped; Metabase 28 passed. ruff check + format bersih (ETL, Metabase).
- Data dev: event ledger `4`,`5` sempat ganda di mart (hash lama + hash sumber, sisa data uji lama); kunci baru menyatukannya ke hash lama -> dikoreksi manual ke nilai sumber. Dengan `ORDER BY event_id`, perubahan hash di sumber kini menimpa baris lama (sebelumnya menghasilkan dua baris).

### Metabase 0.58 (Q-P14 & akun tidak aktif)
- Riwayat query = `query_execution`, dipangkas task OSS `TruncateAuditTables` menurut `audit-max-retention-days`: setting **internal tanpa setter** (API: "Pengaturan :audit-max-retention-days internal") -> **tidak bisa lewat `provision.py`**, hanya env `MB_AUDIT_MAX_RETENTION_DAYS`. OSS hanya memangkas `query_execution`; `audit_log`/`view_log` (Pro/EE) kosong di dev.
- Akun tidak aktif 90 h: **tidak ada fitur bawaan OSS** -> diemulasikan di `provision.py` (deactivate, bukan hapus). Butuh jadwal harian agar efektif (provision hanya saat deploy).

### Env baru / berubah untuk devsecops
| Env | Service | Nilai |
|---|---|---|
| `CH_MART_RETENTION_YEARS` | `clickhouse-init` | **10** — compose (`:-5`), `.env`, `.env.example`, `deploy.yml` (default '5'), `scripts/deploy.sh:280` masih 5 -> ubah ke 10 (default skrip kini 10) |
| `CH_PSEUDONYM_RETENTION_YEARS` | `clickhouse-init` **dan** `etl` | 5 (baru) |
| `CH_GEO_RETENTION_DAYS` | `clickhouse-init` **dan** `etl` | 90 (baru) |
| `MB_AUDIT_MAX_RETENTION_DAYS` | `metabase` | **3650** (baru) |
| timer harian | host | `metabase-provision <argumen sama> --housekeeping-only` (akun tidak aktif 90 h) |

Rekomendasi deploy pertama: `stop etl` sebelum `clickhouse-init` (migrasi `loyalty_events`), lalu `up etl`.

### Untuk lead / BFF
- Pemilik data lokasi di Postgres = BFF (`platform.price_hunter_report`; ETL/OCR hanya baca, tanpa UPDATE — least privilege). Perubahan BFF yang sedang berjalan di branch ini (`retention.service.ts`: `geo_lat/geo_lng = NULL, updated_at = now()` setelah `PRICE_HUNTER_GEO_RETENTION_DAYS`) **cocok** dengan sisi ClickHouse: `updated_at` dinaikkan -> ETL memuat versi tanpa lokasi; TTL kolom + `AgeOut` menutup sisanya.
- PDP inventory §2/§3 perlu revisi (architect): `competitor_prices`/`loyalty_events`/`fact_sales_line` baris 10 th, kolom pseudonim 5 th, geo 90 h; status Q-P8 sisi ClickHouse = jalan.

### Belum / risiko sisa
- Env compose/CI/deploy belum diubah (milik devsecops) -> sampai itu, `.env` dev/prod tetap memberi TTL baris **5 th** dan ETL memakai default 5 th/90 h.
- `MB_AUDIT_MAX_RETENTION_DAYS` belum diset dan efeknya belum diuji (task truncation terjadwal, tidak dipicu manual); default 720 h bersumber dokumentasi upstream, tidak terverifikasi dari jar.
- Jendela race migrasi: erasure DSAR yang jalan tepat di antara salin dan `EXCHANGE` diulang otomatis (verifikasi `count()=0`), tetapi penyalinan "jendela migrasi" (1 jam, `_loaded_at`) secara teori bisa mengembalikan baris subjek yang dihapus pada menit yang sama -> mitigasi: stop ETL saat deploy itu.
- Versi sama (`created_at`) di `loyalty_events`: bila hash sumber berubah tanpa mengubah `created_at`, pemenang merge = insert terakhir (reload ETL memperbaikinya).
- Tidak ada view/semantic layer yang mengekspos `member_hash`/`user_hash`; tidak diubah.

## Gelombang 18b — Rekonsiliasi pembayaran Poin EFN K-18-6 (E-30) (12 Okt 2026)

Kontrak: `docs/04_SERVICE_CONTRACTS.md` §9.9 K-18-1, K-18-5, K-18-6; desain Odoo `docs/08_ODOO_FIT_GAP.md` §9.2 (E-30).
E-30 **belum** ada di DB dev -> logika diuji dengan fixture; uji nyata mode `payment` menunggu lead.

### File
| File | Perubahan |
|---|---|
| `services/etl/efn_etl/loyalty_recon.py` | `LoyaltyReconSettings.points_mode` (`product`/`payment`) + `points_tolerance_idr`. Mode `payment`: kind 3-5 memakai `SQL_ODOO_POINTS_PAYMENTS` (metode `efn_is_loyalty_points`) dengan toleransi tetap (default 0). Baru: `settled_days`, `daily_points_recon`, `add_daily_findings`, `run_daily_points`, kind critical `points_payment_daily_mismatch`, kunci log `points_daily`, tabel `loyalty_points_daily`. |
| `services/etl/efn_etl/pos.py`, `jobs.py` | `fact_pos_payment.is_loyalty_points` (SQL membaca penanda hanya di mode `payment`; mode `product` = konstanta FALSE). |
| `services/etl/efn_etl/config.py`, `__main__.py` | Env `ETL_LOYALTY_POINTS_MODE` (default `product`), `ETL_LOYALTY_POINTS_TOLERANCE_IDR` (default 0, 0..1000); log start memuat `points_mode`. |
| `services/etl/sql/odoo-readonly-grants.sql` | Grant bersyarat (DO block) `pos_payment_method.efn_is_loyalty_points` — lolos sebelum E-30 (NOTICE), aktif saat dijalankan ulang setelah E-30. Diuji di dev: exit 0. |
| `infra/clickhouse/init/06-loyalty-points-payment.sql` | Kolom `fact_pos_payment.is_loyalty_points`, tabel `loyalty_points_daily` (TTL 400 h, tanpa PII), view `v_loyalty_points_daily`. |
| `infra/clickhouse/access/{etl,metabase}-access.sql` | `etl_writer` SELECT/INSERT tabel harian; `metabase_reader` view + tabel. |
| Test | `test_loyalty_recon.py` +11 (hari settle, atribusi lewat tengah malam, selisih per order/BFF-only/Odoo-only, toleransi, order di luar jendela, end-to-end mode payment, mode product tanpa harian), `test_pos.py` +1, `test_config.py` +1. |
| `services/etl/README.md` | Bagian "Redeem poin = pembayaran Poin EFN". |

### Desain
- Rumus K-18-6 per PT x hari WIB: Σ `pos.payment` Poin EFN (order `paid/done`, refund negatif) − (Σ `value_idr` reservasi `committed` − Σ `points_restored_value`) = 0 (toleransi 0).
- Hari = tanggal WIB pembayaran Poin EFN di Odoo; nilai BFF **diatribusikan ke order Odoo-nya** (`order_ref` = `pos.order.uuid`; restore memakai uuid order refund). Order offline yang di-commit BFF lewat tengah malam tidak memicu alarm palsu. Nilai BFF tanpa order Odoo memakai tanggal/PT BFF (= anomali). Hari yang dicek = hari utuh dalam lookback (72 j) yang sudah lewat settle (6 j).
- PDP: tidak membaca member/partner/kartu; sampel = id order Odoo.

### Butuh keputusan / pekerjaan pihak lain
1. **BFF (usulan kontrak, belum ada):** `points_restored_value` (K-18-5) belum disimpan BFF; ETL mengasumsikan tabel `platform.loyalty_pos_reverse` (`refund_order_ref` PK/unik, `original_order_ref`, `company_code`, `points_restored_value` bigint, `created_at`) + `GRANT SELECT` kolom tersebut ke `efn_readonly`. Bila lead memilih bentuk lain, cukup ubah `_PLATFORM_RESTORE_ROWS`.
2. **Odoo E-30:** `pos_payment_method.efn_is_loyalty_points` (boolean). Setelah terpasang: jalankan ulang grant Odoo, set `ETL_LOYALTY_POINTS_MODE=payment` di service `etl` (devsecops), lalu uji nyata.
3. Redeem BFF diambil dari `loyalty_reservation` (retensi 30 h) — cukup untuk jendela 72 j.

### Verifikasi
- pytest ETL 216 passed / 1 skipped; ruff check + format bersih.
- `clickhouse-init` dev exit 0 (06 diterapkan, idempoten pada run ke-2); ETL di-rebuild (mode `product`): `pos_payments` ok, `is_loyalty_points` = 0.
- Query BFF redeem & join Odoo pembayaran diuji sebagai role `efn_etl` di dev (grant cukup, tanpa kolom penanda).
- Catatan dev: `infra/compose/.env` lokal (gitignored) masih `CH_MART_RETENTION_YEARS=5` sehingga run init tanpa override sempat mengembalikan TTL baris ke 5 th; diubah ke 10 dan init dijalankan ulang (tidak ada data > 5 th di dev).

### Belum terverifikasi
- Mode `payment` terhadap Odoo/BFF nyata (E-30 + tabel reverse BFF belum ada).
- Perbandingan `payment_date` (timestamp tanpa zona, UTC) dengan parameter timestamptz mengikuti pola query recon yang ada (session TZ UTC).
- Kartu Metabase untuk `v_loyalty_points_daily` belum dibuat (view + grant sudah siap).
