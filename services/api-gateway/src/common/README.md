# common — infrastruktur bersama BFF

Status: Fase 0 / PoC. Mengikuti `docs/04_SERVICE_CONTRACTS.md` (kontrak wajib) dan rekomendasi ADR yang masih **Proposed**.
Build/test **belum dijalankan lokal** (tidak ada Node di mesin penulis) — validasi di CI.

| Komponen | File | Catatan |
|---|---|---|
| Config & secret | `config/app-config.ts`, `config/secrets.loader.ts` | Pola `X_FILE` + fallback `/run/secrets/<nama>`; fail-fast `assertStartupSecrets()` di `main.ts` (pesan hanya nama secret). |
| Error kontrak | `errors/api-error.ts`, `errors/contract-exception.filter.ts` | `{ error: { code, message } }`, pesan generik Bahasa Indonesia (ditampilkan apa adanya oleh frontend). Body khusus OTP `{ ok:false, ... }` diteruskan apa adanya. |
| Redis | `redis/kv-store.ts` | Interface `KvStore` sempit; implementasi ioredis. Test memakai `testing/fakes.ts`. |
| PostgreSQL | `db/db.ts`, `db/pg.provider.ts` | `pg` Pool, query parametris saja, `tx()` BEGIN/COMMIT/ROLLBACK. |
| RabbitMQ | `amqp/amqp.service.ts`, `amqp/queues.ts` | Confirm channel; queue quorum + DLQ `<queue>.dlq` (§4); reconnect backoff; consumer: gagal → requeue sekali → DLQ; `PermanentMessageError` → DLQ langsung. |
| Outbox | `outbox/outbox.service.ts` | Transactional outbox `platform.outbox`, relay `FOR UPDATE SKIP LOCKED` tiap 5 s + dipicu setelah commit. At-least-once. |
| Idempotensi consumer | `db/processed-message.ts` | `platform.processed_message (consumer, idempotency_key)` dalam transaksi yang sama dengan efek. |
| Rate limit | `rate-limit/rate-limiter.ts` | Fixed window Redis (`INCR`+`EXPIRE` atomik via Lua), opsi offset WIB untuk kuota harian. Fail closed. |
| Service key | `security/service-key.guard.ts` | `X-EFN-Service-Key` vs `odoo_bff_api_key`, `timingSafeEqual` atas SHA-256 (panjang tidak bocor). |
| JWT pelanggan | `security/access-token.service.ts`, `security/jwt-auth.guard.ts` | HS256 dikunci (`algorithms:['HS256']`), `iss`/`aud`/`typ` diverifikasi. |
| Webhook HMAC | `security/webhook-signature.ts`, `security/webhook-hmac.guard.ts` | `X-EFN-Signature: t=,v1=`; signed string `"<t>." + rawBody`; window 300 s; multi-`v1` untuk rotasi. Endpoint webhook belum ada. |
| PII | `pii/pii.ts` | `maskMsisdn` → `62812****890`; `memberHash` = sha256(pepper + member_ref); `normalizeMsisdn` hanya `^62[0-9]{8,13}$`. |
| Enkripsi PII | `pii/crypto.ts`, `pii/price-hunter-subject.ts` | AES-256-GCM, format `ver ‖ IV12 ‖ ct ‖ tag16`, AAD `efn:<tabel>.<kolom>:v1`; key ring v1=`PII_ENCRYPTION_KEY_FILE`, vN=`PII_ENCRYPTION_KEY_V<N>_FILE`, enkripsi pakai `PII_ACTIVE_KEY_VERSION`. Resolve penerima event v2 dari `user_enc`. |
| TLS | `config/app-config.ts` (`tlsClientOptions`, `httpsServerOptions`, `assertTransportSecurity`) | docs/04 §7.1: pg/ioredis/amqplib/MinIO verify-full dengan CA internal, server HTTPS TLS ≥ 1.2; gagal start bila CA/sertifikat tak terbaca; `EFN_ALLOW_PLAINTEXT` ditolak di production. |
| Retensi | `retention/retention.service.ts` | Purge `otp_audit` > 90 h, `processed_message` & `outbox` terkirim > 14 h; advisory lock, batch 5000. |
| Metrik | `metrics/metrics.service.ts`, `metrics/metrics-server.ts` | `GET /metrics` di listener mTLS :9464 (v3, lihat bawah) (prom-client): `efn_otp_request_total{purpose,outcome}`, `efn_otp_verify_total{purpose,outcome}`, `efn_whatsapp_send_total{outcome}`. Tanpa nomor HP/IP di label. |

## Keputusan desain

- **Pepper terpisah via HKDF** (masukan DevSecOps): dari satu secret `otp_pepper` diturunkan dua kunci
  `HKDF-SHA256(ikm=otp_pepper, salt="", info=..., 32 byte)`:
  - `info="efn:otp_code:v1"` → kunci HMAC hash kode OTP;
  - `info="efn:member_hash:v1"` → pepper (hex) untuk `member_hash = sha256(pepper_hex + member_ref)`.
  Domain separation tanpa menambah secret baru. **Service lain (ETL) yang menghitung member_hash wajib memakai derivasi ini.**
  Rotasi `otp_pepper` mengubah semua member_hash → perlu migrasi ledger (belum ada prosedur).
- **trustProxy** dibatasi ke `TRUSTED_PROXIES` (default loopback + RFC1918 + ULA) sehingga `req.ip` = IP pelanggan dari
  `X-Forwarded-For` yang ditambahkan Traefik/Next.js, sementara XFF palsu dari internet diabaikan.
- **`/metrics` tidak boleh publik.** Router Traefik `api` saat ini `Host(api.*)` tanpa pengecualian path → **DevSecOps perlu menambah
  `&& !PathPrefix(`/metrics`)`** (bukan wewenang BFF). Defense in depth: BFF menjawab 404 untuk `/metrics` yang membawa `X-Forwarded-For`/`X-Forwarded-Host`.
- Prefix global `/v1` (kontrak §1), bukan `/api/v1` seperti scaffold awal. `health`, `health/ready`, `metrics` tanpa prefix.
- `helmet` (Express) diganti `@fastify/helmet` — registrasi middleware Express sebagai plugin Fastify di scaffold awal tidak valid.

## Risiko dimitigasi
R-S01 (secret), R-S04 (brute force OTP), R-S06 (upload), R-S09 (webhook palsu), R-T10 (outbox/idempotensi), R-L01 (PII di log/DB), R-T09 (metrik).

- **`/metrics`** tetap internal (Traefik `!PathPrefix(/metrics)`), kini lewat HTTPS server BFF; Prometheus scrape `https://api-gateway:3000/metrics` dengan CA internal.
- **AMQP `user_id`**: publish menyetel `userId` = username koneksi (`efn-bff`); consumer menerima `meta.userId` untuk autentikasi publisher (SEC-08).

## Belum
- Readiness `/health/ready` belum memeriksa Redis/PG/RabbitMQ.
- Job re-encrypt saat rotasi kunci PII.
- Idempotency key untuk endpoint webhook (akan disimpan di PG saat endpoint dibuat).

## v3 — Gelombang 5
| Komponen | File | Catatan |
|---|---|---|
| Member opak (§8.2) | `member/member.service.ts` | upsert saat login, `findActive` (dipakai JwtAuthGuard), `memberRef` (dekripsi + cek konsistensi hash). |
| Access & step-up token | `security/access-token.service.ts`, `security/jwt-auth.guard.ts` | `sub` = member.id; step-up `typ:"step_up", purpose:"dsar"`, 10 menit, header `X-Step-Up-Token` (`StepUpToken` decorator). |
| Key ring PII tanpa v1 (U2) | `config/app-config.ts` (`PII_RETIRED_VERSIONS`), `pii/pii-key-check.ts` | v1 tidak wajib bila dipensiunkan & versi aktif ≠ 1; saat start BFF gagal bila masih ada ciphertext versi pensiun. |
| Re-encrypt PII (U2) | `pii/pii-columns.ts`, `pii/reencrypt.ts`, `../cli/pii-reencrypt.ts` | `node dist/cli/pii-reencrypt.js [--dry-run] [--batch-size=N] [--columns=t.c,...]`; batch keyset, idempotent, UPDATE optimistik; semua kolom `*_enc` (price_hunter_report.user_enc, loyalty_ledger.member_ref_enc, otp_audit.ip_enc, member.member_ref_enc, consent.ip_enc). |
| Egress publik (SEC-23) | `net/public-https.ts` | https.Agent dengan `ca: tls.rootCertificates` (root Mozilla saja) untuk WhatsApp Graph API & payment provider — CA internal tidak pernah dipercaya untuk host publik walau `NODE_EXTRA_CA_CERTS` diset. Koneksi internal tetap CA eksplisit per klien. |
| Redis key pseudonim (SEC-24) | `pii/pii.ts` (`redisKeyHash`), `AppConfig.redisKeyPepper` | HMAC turunan HKDF `efn:redis_key:v1`. |
| Semaphore (SEC-19) | `concurrency/semaphore.ts` | FIFO, antrean terbatas, timeout. |
| RabbitMQ passive declare (§8.10) | `amqp/queues.ts` (`checkQueueWithDlq`), `amqp/amqp.service.ts` | Queue dideklarasikan broker; BFF hanya `checkQueue` (queue + DLQ). Broker terjangkau tetapi queue hilang → **gagal start** (`QueueMissingError`); broker mati → reconnect. Queue baru: `privacy.erasure.completed`. |
| Metrics mTLS (§8.10) | `metrics/metrics-server.ts` | `/metrics` pindah ke listener HTTPS terpisah **:9464** (`METRICS_PORT`), `requestCert + rejectUnauthorized` (CA `METRICS_CLIENT_CA_FILE`), hanya CN/SAN `METRICS_TLS_CLIENT_ALLOWED_CN` (default `prometheus`). Port 3000 tidak lagi melayani `/metrics`. |
| Retensi (§8.4) | `retention/retention.service.ts` | + `price_hunter_report.user_enc` → NULL > 90 h, baris > 730 h. |