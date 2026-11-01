# ai-ocr-worker — Price Hunter OCR (async)

**Tujuan:** memvalidasi laporan harga kompetitor (Price Hunter) secara asinkron.
**ADR:** ADR-008 (pipeline dua tahap async), ADR-006 (reward poin), ADR-020 (ledger di BFF), ADR-010/014 (AI di luar jalur transaksi, jaringan privat), ADR-022 (enkripsi in transit). Semua masih `Proposed`.
**Risiko yang dimitigasi:** R-B05 (fraud: pHash, cap harian, geofence), R-T05 (latensi tanpa GPU), R-S06 (upload berbahaya: decompression bomb, key MinIO dibatasi prefix), SEC-08 (properti AMQP `user_id`), SEC-02 (kredensial per service).
**Kontrak:** `docs/04_SERVICE_CONTRACTS.md` §3.3, §4 (payload **v2**, tanpa nomor HP), §7 (TLS & kredensial).

## Enkripsi & kredensial (docs/04 §7)

Semua hop memakai TLS terverifikasi terhadap CA internal (default `/run/secrets/efn_ca_crt`, hostname = nama service):

| Koneksi | Klien | Env |
|---|---|---|
| RabbitMQ | pika `SSLOptions(context, server_hostname)` | `AMQP_URL=amqps://efn-ocr@rabbitmq:5671`, `RABBITMQ_PASSWORD_FILE`, `AMQP_TLS_CA_FILE` |
| PostgreSQL | psycopg `sslmode=verify-full` | `PG_SSLMODE`, `PG_SSLROOTCERT`, user `efn_ocr`, `PG_PASSWORD_FILE` |
| MinIO | `Minio(secure=True, http_client=urllib3.PoolManager(cert_reqs=CERT_REQUIRED, ca_certs=CA))` | `MINIO_ENDPOINT`, `MINIO_PORT`, `MINIO_USE_SSL`, `MINIO_CA_FILE`, `MINIO_ACCESS_KEY` (`efn-ocr`), `MINIO_SECRET_KEY_FILE` |
| Ollama | requests `verify=CA` + `cert=(crt,key)` (mTLS ke sidecar, docs/04 §8.5) | `OLLAMA_URL=https://ollama:11434`, `OLLAMA_CA_FILE`, `OLLAMA_CLIENT_CERT_FILE`, `OLLAMA_CLIENT_KEY_FILE` (wajib di production) |
| Metrik `:9100` (server) | `start_http_server(certfile, keyfile, client_cafile, client_auth_required=True)` | `METRICS_TLS_CERT_FILE`, `METRICS_TLS_KEY_FILE`, `METRICS_TLS_CLIENT_CA_FILE` (wajib di production; Prometheus wajib membawa sertifikat klien) |

Service **gagal start** (`TlsConfigError`) bila: skema `amqp://`/`http://`, `MINIO_USE_SSL=false`, `PG_SSLMODE` ≠ `verify-full`, CA/sertifikat tidak terbaca, password di `AMQP_URL`, atau sertifikat metrik tidak ada. Pengecualian hanya `EFN_ALLOW_PLAINTEXT=true` dengan `APP_ENV` ∈ {dev, development, local, test, ci}; `APP_ENV` kosong/tidak dikenal = production dan flag ditolak (SEC-27). Fallback `MINIO_ROOT_*` dihapus (least privilege).

Setiap pesan yang dipublish membawa properti AMQP `user_id` = user di `AMQP_URL` (`efn-ocr`); RabbitMQ menolak bila tidak sama dengan user koneksi, dan BFF hanya menerima `loyalty.earn.requested` dari `efn-ocr` (SEC-08).

Worker hanya menulis kolom `status, reason, extracted, confidence, image_phash, updated_at` dan tidak pernah membaca kolom `*_enc` (dikunci `tests/test_repository_sql.py`).

## Alur

```
ocr.requested ──> klaim queued->processing (UPDATE ... RETURNING)
   ──> unduh foto MinIO (bucket+prefix dari konfigurasi, key dari DB)
   ──> pHash + cek duplikat 30 hari, cap harian per user_hash, geofence  ──(fraud)──> flagged/rejected
   ──> Tahap 1: Tesseract (CPU)  ──conf >= ambang──┐
   └─conf < ambang──> Tahap 2: VLM Ollama /api/generate ──(mati)──> flagged vlm_unavailable
   ──> parse Rupiah, toleransi harga, fuzzy nama (rapidfuzz >= 75)
   ──> UPDATE status valid|rejected|flagged + publish (sebelum COMMIT)
         valid    -> loyalty.earn.requested + notify.push(price_hunter_valid)
         rejected -> notify.push(price_hunter_rejected)
         flagged  -> (tidak ada event; review manual)
```

Payload v2: `ocr.requested` wajib `schema_version: 2` + `user_hash` (hex sha256); pesan yang masih memuat `user_id`/`member_ref` ditolak ke DLQ. Event keluar: `loyalty.earn.requested {schema_version:2, idempotency_key, member_hash, points, reason, source:"price_hunter", source_ref}` dan `notify.push {schema_version:2, idempotency_key, member_hash, source, source_ref, template, params}`. `member_hash` = `price_hunter_report.user_hash` dari DB (bukan dari pesan).

Worker **tidak** menulis ke ClickHouse maupun Odoo. ETL memuat hasil ke mart `competitor_prices`.

## Pilihan OCR tahap 1: Tesseract (default)

Tesseract dari apt Debian (`tesseract-ocr-ind`, `-eng`): image ramping, tanpa unduh model saat runtime (container tanpa egress), stabil di Python 3.12. PaddleOCR biasanya lebih akurat untuk label berwarna/miring, tetapi menambah ~1 GB (paddlepaddle + OpenCV), mengunduh model saat start, dan kompatibilitas wheel Python 3.12 perlu dicek. Kelemahan Tesseract dikompensasi tahap 2 (VLM). `OCR_ENGINE=paddle` didukung bila image turunan memasang `paddleocr` 2.x; jika import gagal worker kembali ke Tesseract. Evaluasi ulang dengan dataset 200–500 foto berlabel (ADR-008).

## Antrian & retry

- Topologi (Gelombang 5b) **dideklarasikan broker** (definitions devsecops): queue quorum durable, `x-dead-letter-exchange: ""`, `x-dead-letter-routing-key: <queue>.dlq`; DLQ quorum durable. Worker hanya `queue_declare(passive=True)` untuk `ocr.requested` + `ocr.requested.dlq` saat start; queue tidak ada → log jelas + exit code 2 (tidak reconnect berulang). Queue publish (`loyalty.earn.requested`, `notify.push`) tidak disentuh; publish `mandatory=True` mendeteksi queue hilang. User `efn-ocr` tidak butuh izin `configure`.
- Ack manual, prefetch 2, pemrosesan di thread (heartbeat tetap jalan).
- Pesan rusak/skema salah -> langsung DLQ. Error transien -> status dikembalikan `queued`, republish dengan header `x-retry-count` (backoff 2/4/8 s), maks `OCR_MAX_RETRIES` (3) lalu `flagged/processing_failed` + DLQ.
- Idempotency: klaim kondisional; event memakai `message_id = idempotency_key` (`price_hunter:<report_id>`, `price_hunter_notify:<report_id>`).

## Konfigurasi

| Env | Default | Keterangan |
|---|---|---|
| `APP_ENV` / `EFN_ALLOW_PLAINTEXT` | — / `false` | plaintext hanya dev; ditolak di production |
| `AMQP_URL` (fallback `RABBITMQ_URL`) | `amqps://efn-ocr@rabbitmq:5671` | tanpa password; user = properti `user_id` |
| `RABBITMQ_PASSWORD_FILE` / `AMQP_TLS_CA_FILE` | — / `/run/secrets/efn_ca_crt` | secret `rabbitmq_password_ocr` |
| `PG_HOST` / `PG_PORT` / `PG_USER` / `PG_DB` (fallback `PLATFORM_DB`) | `postgres` / `5432` / `efn_ocr` / `efn_platform` | SELECT kolom non-PII + UPDATE kolom hasil |
| `PG_PASSWORD_FILE` / `PG_SSLMODE` / `PG_SSLROOTCERT` | — / `verify-full` / CA internal | secret `pg_password_ocr` |
| `MINIO_ENDPOINT` / `MINIO_PORT` / `MINIO_USE_SSL` / `MINIO_CA_FILE` | `minio` / `9000` / `true` / CA internal | |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY_FILE` | `efn-ocr` / — | secret `minio_ocr_secret` (GetObject `market-scout/reports/*`) |
| `MINIO_BUCKET_MARKET_SCOUT` | `market-scout` | key wajib berawalan `reports/` |
| `OCR_ENGINE` / `OCR_TESSERACT_LANG` | `tesseract` / `ind+eng` | |
| `OCR_CONFIDENCE_THRESHOLD` | `0.80` | di bawah ini -> VLM |
| `OCR_VLM_ENABLED` | `true` | `false` -> laporan ragu jadi `flagged/vlm_disabled` |
| `OLLAMA_URL` / `OLLAMA_CA_FILE` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT_SECONDS` | `https://ollama:11434` / CA internal / `qwen2.5vl:7b` / `90` | sidecar TLS |
| `OCR_VLM_MAX_SIDE` / `OCR_VLM_JPEG_QUALITY` / `OCR_VLM_TRIM_BORDER` | `1024` / `85` / `false` | G12: gambar untuk VLM diperkecil (tidak pernah diperbesar); trim tepi polos opsional |
| `OLLAMA_NUM_PREDICT` / `OLLAMA_NUM_CTX` / `OLLAMA_KEEP_ALIVE` | `128` / `2048` / `30m` | gagal start bila `NUM_CTX` < token gambar sisi maks + 200 + `NUM_PREDICT` |
| `NAME_SIMILARITY_THRESHOLD` | `75` (atau `0.75`) | skor rapidfuzz |
| `PRICE_TOLERANCE_PCT` | `2.0` | |
| `PRICE_HUNTER_DAILY_CAP` | `20` | laporan valid per user per hari (WIB); 0 = nonaktif |
| `PHASH_MAX_DISTANCE` / `PHASH_LOOKBACK_DAYS` | `6` / `30` | |
| `GEOFENCE_CIRCLES` | kosong (nonaktif) | `lat,lng,radius_m;...` |
| `PRICE_HUNTER_POINTS` | `10` | menunggu Q-13/Q-43 |
| `OCR_PREFETCH` / `OCR_MAX_RETRIES` / `OCR_STALE_PROCESSING_SECONDS` | `2` / `3` / `900` | |
| `METRICS_PORT` / `LOG_LEVEL` | `9100` / `INFO` | |
| `METRICS_BIND_ADDRESS` | `0.0.0.0` bila HTTPS, `127.0.0.1` bila plaintext dev | literal IP; non-loopback tanpa TLS ditolak. Healthcheck memprobe 127.0.0.1 -> pakai `0.0.0.0`/`::` atau loopback |
| `METRICS_TLS_CERT_FILE` / `METRICS_TLS_KEY_FILE` / `METRICS_TLS_CA_FILE` | — | secret `tls_ocr_worker_crt` / `tls_ocr_worker_key`; CA untuk healthcheck |
| `METRICS_TLS_CLIENT_CA_FILE` | — (wajib production) | CA internal; healthcheck membawa sertifikat worker sebagai klien |
| `OLLAMA_CLIENT_CERT_FILE` / `OLLAMA_CLIENT_KEY_FILE` | — (wajib production bila VLM aktif) | secret `tls_ocr_worker_crt` / `tls_ocr_worker_key` |

Metrik Prometheus di `https://ocr-worker:9100/metrics`: `efn_ocr_reports_total{status}`, `efn_ocr_processed_total`, `efn_ocr_deliveries_total{action}`, `efn_ocr_vlm_calls_total{outcome}`, `efn_ocr_processing_seconds` (histogram).

## Performa VLM (Gelombang 12)

Diukur di stack dev (Docker Desktop WSL2, Ollama 0.35.1 CPU, `qwen2.5vl:3b`, foto smoke sintetis ber-noise 1200x800, 3 foto baru per varian, model sudah termuat):

| Varian | Token prompt | Waktu per foto (rata-rata) | Akurasi (nama+harga persis) |
|---|---|---|---|
| Sebelum: gambar asli 1200x800 q92, tanpa batas | 1333 | 66,7 s (74,1 / 64,6 / 61,3) | 3/3 |
| **Default G12**: sisi maks 1024, q85, `num_predict=128`, `num_ctx=2048`, `keep_alive=30m` | 1166 | **59,1 s** (64,7 / 55,1 / 57,5) | 3/3 |
| 768 px | 1166 | 59,3 s | 1/3 (sufiks nama hilang) |
| 512 px (probe) | 1166 | 55,7 s | 1/1 |
| 1024 + trim tepi | 1126–1144 | 59,4 s | 2/3 |

Temuan: Ollama (implementasi qwen2.5vl) **menormalkan ukuran gambar sendiri** — di bawah ~1024 px jumlah token gambar tetap ~1080, jadi resize di klien hanya memangkas ~12 % token/waktu dan mengecilkan payload (45–54 KB -> 27–31 KB); memperkecil lebih jauh menurunkan akurasi tanpa mempercepat. Waktu didominasi *prompt eval* gambar (~20 token/s di CPU). `keep_alive` mencegah muat ulang model (~78 s setelah restart). **Kesimpulan: CPU tidak layak untuk throughput produksi; resize bukan solusi.**

Rekomendasi produksi (ADR-008, keputusan klien): host Ollama dengan GPU NVIDIA >= 8 GB VRAM (mis. L4 24 GB / RTX 4060 Ti 16 GB) untuk `qwen2.5vl:7b` (atau 3b). Perkiraan (belum diukur di GPU): prompt eval ratusan-ribuan token/s -> ~1–3 s per foto, ~20–40 foto/menit per GPU dengan `OLLAMA_NUM_PARALLEL=2`, vs ~1 foto/menit di CPU 8 thread. Target operasional usulan: p95 < 10 s per foto VLM, antrean `ocr.requested` < 5 menit pada puncak. Tanpa GPU: biarkan VLM aktif sebagai jalur lambat (laporan ragu menunggu ~1 menit; timeout dev 240 s) atau `OCR_VLM_ENABLED=false` -> `flagged/vlm_disabled` (review manual).

## Menjalankan test

```
pip install -r requirements-dev.txt
pytest
```

## Belum / batasan

- Cap harian bisa terlewati 1 laporan bila dua laporan user yang sama diproses bersamaan (prefetch 2).
- VLM dapat terkecoh teks di foto (prompt injection visual); mitigasi: nilai laporan tidak dikirim ke model, harga & nama tetap harus cocok.
- Belum ada deteksi foto hasil edit/AI atau foto dari internet (R-B05 sisa).
- TLS belum diuji terhadap broker/DB/MinIO nyata (tidak ada runtime di mesin penulis); test unit memakai CA dummy.
- Mutual TLS (sertifikat klien) belum dipakai; autentikasi klien tetap password per service.

## Gateway LLM 9router + fallback Ollama (G37, K25-01, docs/04 §22, ADR-037)
- `OCR_LLM_PROVIDER`: `ollama` (default, tanpa egress) | `ninerouter` | `ninerouter_ollama` (9router dulu, Ollama bila
  gagal). Selain `ollama` butuh overlay `infra/compose/docker-compose.llm.yml`, secret `ninerouter_api_key`, dan
  `OCR_LLM_CROSS_BORDER_OK=true` (transfer lintas negara, DPO Q-T7; production ditolak deploy).
- 9router dipanggil dengan **Anthropic Messages** `POST /v1/messages` (`x-api-key`). Jalur OpenAI-compatible dengan
  `image_url` diblok WAF 9router (403).
- Model: `NINEROUTER_VISION_MODEL` (default `cc/claude-haiku-4-5-20251001`), timeout `NINEROUTER_TIMEOUT_SECONDS` (45).
- Metrik: `efn_ocr_llm_provider_calls_total{provider,outcome}`; `extracted_json.engine` = penyedia yang menjawab.

