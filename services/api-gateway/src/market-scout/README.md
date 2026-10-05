# market-scout — Price Hunter intake

Kontrak: `docs/04` §3.3 & §4 (`ocr.requested`). Alur: `docs/02` §3.2. Keputusan: D-06 (PostgreSQL source of truth), D-07 (OCR async), D-05 (poin).

| Endpoint | Sukses | Gagal |
|---|---|---|
| `POST /v1/market-scout/reports` (Bearer, multipart: `photo`, `product_name`, `price`, `competitor_name?`, `lat?`, `lng?`) | `202 { report_id (UUID v4), status:"queued" }` | `400`, `401`, `413`, `415`, `429` (cap harian), `503` |
| `GET /v1/market-scout/reports/:id` (Bearer, hanya pemilik) | `200 { report_id, status, reason, points_awarded }` | `401`, `404` (juga untuk report milik orang lain) |

## Pipeline
1. `@fastify/multipart` streaming: batas `fileSize` (8 MB, `MARKET_SCOUT_MAX_BYTES`), 1 file, 10 field — ditegakkan per byte oleh busboy sehingga upload **chunked tanpa Content-Length** (dari Next.js) diterima dan tetap dibatasi.
2. Validasi field; magic bytes JPEG/PNG/WebP (header klien diabaikan).
3. Cap harian per user (default 20, `MARKET_SCOUT_DAILY_CAP`), reset 00:00 WIB, key Redis memakai member_hash.
4. `sharp`: decode penuh (`failOn:'error'`, batas 50 MP), rotasi EXIF, resize ≤ 2048 px, JPEG q85, metadata dibuang; dHash 64-bit dihitung tetapi **tidak** ditulis ke DB — kolom `image_phash` dimiliki ai-ocr-worker (pHash) yang juga melakukan dedup R-B05 (keputusan QA: satu pemilik kolom).
5. MinIO bucket `MINIO_BUCKET_MARKET_SCOUT` (default `market-scout`), key `reports/<yyyy>/<mm>/<report_id>.jpg` (UTC).
6. Transaksi: `INSERT platform.price_hunter_report (status queued)` + `INSERT platform.outbox (ocr.requested, message_id = "ocr.requested:<report_id>")`; setelah commit relay dipicu.

## Outbox vs publish-setelah-commit (trade-off)
Dipilih **transactional outbox**. Publish-setelah-commit lebih sederhana dan latensinya minimal, tetapi bila proses mati atau broker down di antara COMMIT dan publish, report tertahan `queued` selamanya tanpa event. Outbox menjamin event terkirim begitu baris di-commit (at-least-once) dengan biaya satu tabel + relay polling (5 s) dan kemungkinan duplikat → worker wajib idempotent (`UPDATE ... WHERE status='queued' RETURNING`). Latensi normal tetap rendah karena relay dipicu langsung setelah commit.

## Risiko
R-S06 (polyglot/bomb/EXIF), R-B05 (fraud: cap harian, pHash), R-T05 (OCR async), R-L01 (lokasi & foto = data pribadi), R-L06.

## Enkripsi & PII (Gelombang 3, docs/04 §7, ADR-022)
- Tabel menyimpan `user_hash` (member_hash) + `user_enc` (AES-256-GCM, AAD `efn:price_hunter_report.user_enc:v1`); kolom `user_id` dihapus. Cap harian (Redis) & kepemilikan `GET /reports/:id` memakai `user_hash` dari `sub` token.
- `ocr.requested` **schema_version 2**: `user_hash`, tanpa nomor HP (outbox pun tidak memuat nomor HP).
- SEC-17: `lat/lng` dibulatkan **3 desimal di server** (`roundCoord`) sebelum INSERT & payload.
- MinIO: `MINIO_USE_SSL=true` + `transportAgent` CA internal (`MINIO_CA_FILE`), user `efn-bff` (`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY_FILE` → `minio_bff_secret`) yang hanya `PutObject/DeleteObject` → BFF tidak lagi `bucketExists/makeBucket` (bucket dibuat `minio-init`). Kredensial lazy (503 saat upload), TLS divalidasi saat start.

## Belum / catatan
- Lifecycle rule bucket (foto 12 bulan, objek yatim) — wewenang DevSecOps (`minio-init`).
- Geofence/jarak ke toko kompetitor (R-B05) belum.

## v3 — Gelombang 5 (docs/04 §8.2, §8.4; SEC-19)
- Identitas dari token = `member.id`; `user_hash` = `member.member_hash`; `user_enc` dibuat dari `member.member_ref_enc` (dekripsi → enkripsi ulang AAD laporan). Kepemilikan `GET` via member_hash.
- **Consent lokasi**: koordinat dibuang di server (DB & `ocr.requested.geo = null`) bila consent `location` terbaru member tidak `granted`. Default = tidak setuju.
- **SEC-19**: `sharp.concurrency(SHARP_CONCURRENCY=1)`, `sharp.cache(false)`, semaphore per proses `IMAGE_MAX_CONCURRENCY=2`, antrean `IMAGE_QUEUE_MAX=8`, tunggu maks `IMAGE_QUEUE_TIMEOUT_MS=10000` → penuh/timeout `429 rate_limited` (Retry-After 5). Puncak memori decode ≈ 2 × 200 MB per replika.
- Retensi: `user_enc` → NULL setelah 90 hari, baris dihapus setelah 730 hari (RetentionService); foto: lifecycle MinIO 90 hari (DevSecOps); erasure akun menghapus foto + menganonimkan baris (modul `privacy`).