# Laporan efn-bff-engineer — BFF Fase 0 (OTP, POS, auth, loyalty, notification, market-scout)

Tanggal: 2026-10-05. Area tulis: `services/api-gateway/`, `infra/postgres/init/02-platform-loyalty.sql`, laporan ini.

> **Build, lint, dan test BELUM dijalankan lokal** (mesin penulis tidak punya Node). Semua kode ditulis untuk NestJS 10 + Fastify 4,
> TypeScript strict, dan harus divalidasi di CI (`npm install && npm run build && npm run lint && npm test`). Belum ada `package-lock.json`.

## 1. Ringkasan file

| Context | File utama | Spec |
|---|---|---|
| config | `src/config/app-config.ts` (baru), `secrets.loader.ts` (diperluas) | `app-config.spec.ts` |
| common | `errors/*`, `redis/*`, `db/*`, `amqp/*`, `outbox/*`, `rate-limit/*`, `security/*` (service key, JWT, webhook HMAC), `pii/*`, `metrics/*`, `net/client-ip.ts`, `validation.ts`, `infra.lifecycle.ts`, `common.module.ts`, `testing/fakes.ts` | `contract-exception.filter.spec`, `queues.spec`, `outbox.service.spec`, `pii.spec`, `rate-limiter.spec`, `webhook-signature.spec` |
| otp | `otp.service.ts`, `otp.module.ts`, `whatsapp/{whatsapp-sender,cloud-api-whatsapp.sender,console-whatsapp.sender,whatsapp.module}.ts` | `otp.service.spec`, `whatsapp/whatsapp.spec` |
| pos | `pos.controller.ts`, `pos.service.ts`, `discount-token.service.ts`, `dto/pos.dto.ts`, `pos.module.ts` | `pos.service.spec` (replay idempotent, order mismatch, expired, forged/alg none, rate limit), `pos.controller.spec` (guard, validasi) |
| auth | `auth.controller.ts`, `me.controller.ts`, `auth.service.ts`, `dto/auth.dto.ts`, `auth.module.ts` | `auth.service.spec` (+ JwtAuthGuard) |
| loyalty | `loyalty.service.ts`, `loyalty-earn.consumer.ts`, `dto/loyalty-earn.message.ts`, `loyalty.module.ts` | `loyalty.spec` (idempotensi 2 lapis, expires_at, company_code) |
| notification | `notify-push.consumer.ts`, `templates.ts`, `notification.module.ts` | `notify-push.consumer.spec` |
| market-scout | `market-scout.controller.ts`, `market-scout.service.ts`, `multipart-form.ts`, `dto/report.dto.ts`, `image/{magic-bytes,dhash,image-processor}.ts`, `storage/object-storage.ts` | `market-scout.service.spec` (+ multipart reader), `image/magic-bytes.spec` |
| root | `app.module.ts`, `main.ts`, `package.json` | `health.controller.spec` (tidak diubah) |
| infra | `infra/postgres/init/02-platform-loyalty.sql` | — |

README per context: `src/{common,otp,pos,auth,loyalty,notification,market-scout}/README.md`.

### Perubahan pada scaffold yang ada
- Prefix global `api/v1` → **`v1`** (kontrak §1); `health`, `health/ready`, `metrics` tanpa prefix.
- `helmet` (Express) diganti **`@fastify/helmet`** — scaffold me-register middleware Express sebagai plugin Fastify (tidak valid).
- `trustProxy: true` → daftar CIDR `TRUSTED_PROXIES` (default loopback + RFC1918 + ULA).
- `rawBody: true` di `NestFactory.create` (HMAC webhook), `bodyLimit` JSON 1 MiB, `@fastify/multipart` dengan batas.
- Filter error global `ContractExceptionFilter` (APP_FILTER).

### Dependency baru (`package.json`)
`@fastify/helmet ^11.1.1`, `@fastify/multipart ^8.3.0` (keduanya garis Fastify 4 yang dipakai `@nestjs/platform-fastify` 10), `fastify ^4.28.1` (tipe), `jsonwebtoken ^9.0.2`, `minio ^8.0.2`, `prom-client ^15.1.3`, `sharp ^0.33.5`; dev: `@types/jsonwebtoken ^9.0.7`. Dihapus: `helmet`.

## 2. Env & secret yang dibaca kode (untuk dicocokkan DevSecOps & QA)

### Secret (file `/run/secrets/<nama>`, via `<ENV>_FILE` atau fallback otomatis ke `/run/secrets/<nama>`)
| Env (setelah resolve) | Env file | Secret | Wajib saat start |
|---|---|---|---|
| `JWT_SECRET` | `JWT_SECRET_FILE` | `jwt_secret` | ya (≥16 char) |
| `OTP_PEPPER` | `OTP_PEPPER_FILE` | `otp_pepper` | ya (≥16 char) |
| `DISCOUNT_TOKEN_SECRET` | `DISCOUNT_TOKEN_SECRET_FILE` | `discount_token_secret` | ya (≥16 char) |
| `ODOO_BFF_API_KEY` (alias `EFN_BFF_API_KEY`) | `ODOO_BFF_API_KEY_FILE` | `odoo_bff_api_key` | ya (≥16 char) |
| `WHATSAPP_TOKEN` | `WHATSAPP_TOKEN_FILE` | `whatsapp_token` | hanya bila `WHATSAPP_ENABLED=true` |
| `WEBHOOK_PAYMENT_SECRET` | `WEBHOOK_PAYMENT_SECRET_FILE` | `webhook_payment_secret` | tidak (dipakai saat endpoint webhook ada) |
| `PG_PASSWORD` | `PG_PASSWORD_FILE` | `pg_password` | praktis ya |
| `RABBITMQ_PASSWORD` | `RABBITMQ_PASSWORD_FILE` | `rabbitmq_password` | praktis ya |
| `MINIO_ROOT_PASSWORD` (atau `MINIO_SECRET_KEY`) | `MINIO_ROOT_PASSWORD_FILE` | `minio_root_password` | tidak (lazy; upload → 503 bila kosong) |
| `REDIS_PASSWORD` (opsional) | `REDIS_PASSWORD_FILE` | — | tidak |

Opsional: `SECRETS_DIR` (default `/run/secrets`).

### Non-rahasia
| Env | Default | Keterangan |
|---|---|---|
| `NODE_ENV` | `production` | `development` → Console sender mencetak OTP |
| `PORT` | `3000` | |
| `TRUSTED_PROXIES` (alias `TRUST_PROXY`) | `127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7` | Fastify trustProxy |
| `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER` | —, `5432`, `efn_platform`, `efn` | prioritas; fallback `DATABASE_URL` |
| `PG_POOL_MAX` | `10` | |
| `REDIS_URL` | `redis://redis:6379` | |
| `AMQP_URL` (alias `RABBITMQ_URL`) | `amqp://rabbitmq:5672` | tanpa password; password dari `RABBITMQ_PASSWORD` |
| `AMQP_PREFETCH` | `10` | |
| `AMQP_CONSUMERS_ENABLED` | `true` | |
| `OUTBOX_RELAY_INTERVAL_MS` | `5000` | 0 = mati |
| `MINIO_ENDPOINT` | `minio:9000` | host:port |
| `MINIO_ROOT_USER` (atau `MINIO_ACCESS_KEY`) | — | |
| `MINIO_USE_SSL` | `false` | |
| `MINIO_BUCKET_MARKET_SCOUT` (alias `MARKET_SCOUT_BUCKET`) | `market-scout` | |
| `MARKET_SCOUT_DAILY_CAP` | `20` | |
| `MARKET_SCOUT_MAX_BYTES` | `8388608` | juga batas multipart |
| `WHATSAPP_ENABLED` | `false` | |
| `WHATSAPP_PHONE_NUMBER_ID` | — | wajib bila enabled |
| `WHATSAPP_OTP_TEMPLATE` | `efn_otp` | |
| `WHATSAPP_TEMPLATE_LANG` | `id` | |
| `WHATSAPP_API_VERSION` | `v21.0` | |
| `WHATSAPP_GRAPH_BASE_URL` | `https://graph.facebook.com` | butuh egress HTTPS |
| `WHATSAPP_OTP_COPY_CODE_BUTTON` | `true` | |
| `WHATSAPP_TIMEOUT_MS` | `4000` | |
| `OTP_TTL_SECONDS` / `OTP_MAX_ATTEMPTS` | `180` / `3` | |
| `OTP_RL_WINDOW_SECONDS` / `OTP_RL_MEMBER_LIMIT` / `OTP_RL_SOURCE_LIMIT` / `OTP_RL_VERIFY_LIMIT` | `900` / `3` / `20` / `60` | |
| `DISCOUNT_TOKEN_TTL_SECONDS` / `ACCESS_TOKEN_TTL_SECONDS` | `600` / `3600` | |

Variabel lama di compose yang **tidak lagi dibaca**: `ODOO_URL` (BFF belum memanggil Odoo). `DATABASE_URL`, `RABBITMQ_URL` hanya fallback.

## 3. Penyesuaian atas pesan koordinator selama kerja
1. Frontend: `report_id` UUID v4; multipart chunked tanpa Content-Length diterima (batas busboy); trustProxy terbatas; pesan error generik Bahasa Indonesia; gap logout/refresh dicatat (`src/auth/README.md`), tidak ada endpoint baru.
2. Kontrak §5 loyalty: kolom `company_code` (CHECK EFN/RTL/FNB/FARM, default `EFN`) dan `expires_at` (NULL = tidak kedaluwarsa); saldo hanya entri belum kedaluwarsa; earn price_hunter → `EFN`.
3. Kontrak `order_ref` opaque ≤ 64 (ASCII tercetak, tidak di-parse); consume **idempotent** untuk `jti`+`order_ref` sama (`dtok:used:<jti>` = order_ref), order lain → `already_used`. Spec ditambahkan; contoh test memakai UUID.
4. DevSecOps: nama env compose final didukung (lihat §2); `/metrics` prom-client dengan counter yang diminta; pepper dipisah via HKDF (lihat §4).

## 4. Keputusan yang perlu diketahui agent lain
- **member_hash** = `sha256(P + member_ref)` dengan `P = hex(HKDF-SHA256(ikm=otp_pepper, salt="", info="efn:member_hash:v1", L=32))` — BUKAN `otp_pepper` mentah. Kunci HMAC OTP = HKDF `info="efn:otp_code:v1"`. ETL/worker yang menghitung member_hash wajib memakai derivasi yang sama.
- **Webhook signature**: string yang di-HMAC = `"<t>." + rawBody` (timestamp ikut ditandatangani; jika hanya body, replay window bisa diakali). Perlu dikonfirmasi di kontrak §2.
- **user_id** (JWT `sub`, `price_hunter_report.user_id`, `ocr.requested.user_id`, `notify.push.user_id`) = member_ref E.164.
- Topologi queue BFF: quorum + `x-dead-letter-exchange: ""` + `x-dead-letter-routing-key: <q>.dlq`, DLQ quorum tanpa argumen lain. `services/ai-ocr-worker/worker.py` versi lama men-declare `x-dead-letter-exchange: "dlx"` → **PRECONDITION_FAILED** bila tidak disamakan.
- `/metrics` harus dikecualikan dari router Traefik publik (`!PathPrefix(`/metrics`)`); BFF juga menolak `/metrics` yang membawa `X-Forwarded-For`.

## 5. Asumsi yang belum terverifikasi
1. Kompatibilitas tipe/versi: `@fastify/multipart@8` & `@fastify/helmet@11` dengan Fastify yang dibawa `@nestjs/platform-fastify@10.4` (registrasi memakai `as never` seperti scaffold). `@types/amqplib` (`connect` → `ChannelModel`/`Connection`) ditangani dengan `Awaited<ReturnType<typeof connect>>`.
2. Nest 10 meneruskan error content-parser Fastify (413/415/JSON rusak) ke filter global — bila tidak, respons berformat default Fastify.
3. `sharp` 0.33 prebuilt untuk `linuxmusl-x64` terpasang di image `node:22-alpine` (perhatikan lockfile yang dibuat di Windows bisa tidak memuat optional dependency Linux).
4. Template WhatsApp `efn_otp` (AUTHENTICATION + copy-code button), `efn_price_hunter_valid`, `efn_price_hunter_rejected` disetujui Meta dengan jumlah parameter sesuai.
5. Odoo mengirim `member_ref` E.164 tanpa `+` (BFF juga menerima `08…`/`+62…`) dan meneruskan IP tidak relevan (rate limit POS per `pos_config_id`).
6. Next.js meneruskan `X-Forwarded-For` klien ke BFF; jaringan docker berada di RFC1918.
7. Init script `02-…sql` hanya jalan pada volume PG kosong; volume yang sudah ada perlu dijalankan manual (idempoten).
8. Role yang menjalankan init = pemilik `ALTER DEFAULT PRIVILEGES` di 01 sehingga REVOKE/GRANT kolom di 02 efektif.

## 6. Gap / risiko terbuka
- Tidak ada refresh token, logout/revoke (token valid sampai exp 1 jam).
- ETL menyalin `price_hunter_report.user_id` (nomor HP) ke ClickHouse — perlu di-hash di ETL (R-L01).
- `otp_audit.ip` masih terbaca `efn_readonly` (diatur di 01, bukan wewenang file ini).
- Kredensial MinIO root dipakai BFF — sebaiknya user khusus dengan policy bucket.
- Readiness check dependensi, purge outbox/processed_message, captcha OTP, redeem/clawback poin belum dibuat.
- Retry consume setelah token kedaluwarsa menjawab `expired` (bukan idempotent ok).

---

# Gelombang 3 — Enkripsi di setiap transaksi data (2026-10-05)

> **Build, lint, dan test BELUM dijalankan lokal** (tidak ada Node). Tidak ada dependency npm baru (hanya `node:crypto`,
> `node:tls`, `node:https`). Kontrak acuan: `docs/04` v2 §3.1, §4, §7 + pesan lead (key ring PII, kebutuhan grant data-AI).

## G3.1 File utama

| Area | File |
|---|---|
| TLS klien/server, plaintext guard, TRUSTED_PROXIES parser, key ring PII, env baru | `src/config/app-config.ts`, `src/config/secrets.loader.ts`, `src/main.ts` |
| Klien TLS | `src/common/db/pg.provider.ts`, `src/common/redis/redis.provider.ts`, `src/common/amqp/amqp.service.ts` (amqps + `userId` publish + `meta.userId`), `src/market-scout/storage/object-storage.ts` (https.Agent CA; tanpa bucketExists/makeBucket) |
| Enkripsi PII | `src/common/pii/crypto.ts` (baru), `src/common/pii/price-hunter-subject.ts` (baru), `src/common/pii/pii.ts` (62-only) |
| Retensi | `src/common/retention/retention.service.ts` (baru), `common.module.ts` |
| Event v2 | `market-scout.service.ts` (`ocr.requested` v2), `loyalty/loyalty-earn.consumer.ts`, `loyalty/dto/loyalty-earn.message.ts`, `loyalty/loyalty.service.ts`, `notification/notify-push.consumer.ts` |
| SEC-06/07/17 | `otp/otp.service.ts` (budget global, ip_enc), `auth/auth.service.ts` (kuota per nomor+IP), `auth/dto/auth.dto.ts`, `pos/dto/pos.dto.ts`, `pos/discount-token.service.ts`, `pos/pos.service.ts`, `market-scout/dto/report.dto.ts` (`roundCoord`), `common/errors/api-error.ts` (`invalid_phone`) |
| Docker | `Dockerfile` (HEALTHCHECK → `healthcheck.js`), `healthcheck.js` (baru, HTTPS + CA + servername `api-gateway`) |
| SQL | `infra/postgres/init/01-databases.sql`, `02-platform-loyalty.sql` |
| Spec baru/ubah | `common/pii/crypto.spec.ts`, `common/net/client-ip.spec.ts` (Fastify inject, XFF palsu), `common/retention/retention.service.spec.ts`, `config/app-config.spec.ts` (TLS builder, production menolak plaintext, CIDR panjang), `loyalty/loyalty.spec.ts`, `notification/notify-push.consumer.spec.ts`, `pos/pos.service.spec.ts` (member_mismatch, invalid_phone), `pos/pos.controller.spec.ts`, `auth/auth.service.spec.ts` (non-62, lockout), `otp/otp.service.spec.ts` (budget, ip_enc), `market-scout/market-scout.service.spec.ts` (user_hash/user_enc, pembulatan), `common/pii/pii.spec.ts` |
| README | `common`, `otp`, `pos`, `auth`, `loyalty`, `notification`, `market-scout` |

## G3.2 Env & secret yang dibaca kode (untuk DevSecOps)

### Secret (file; `*_FILE` atau fallback `/run/secrets/<nama>`)
| Env hasil | Env file | Secret default | Wajib |
|---|---|---|---|
| `PII_ENCRYPTION_KEY` | `PII_ENCRYPTION_KEY_FILE` | `pii_encryption_key` (32 byte, base64) | **ya** |
| `PII_ENCRYPTION_KEY_V<N>` (N≥2) | `PII_ENCRYPTION_KEY_V<N>_FILE` | — | opsional (rotasi) |
| `PG_PASSWORD` | `PG_PASSWORD_FILE` | `pg_password_bff` (dulu `pg_password`) | praktis ya |
| `REDIS_PASSWORD` | `REDIS_PASSWORD_FILE` | `redis_password_bff` | ya bila ACL aktif |
| `RABBITMQ_PASSWORD` | `RABBITMQ_PASSWORD_FILE` | `rabbitmq_password_bff` (dulu `rabbitmq_password`) | praktis ya |
| `MINIO_SECRET_KEY` | `MINIO_SECRET_KEY_FILE` | `minio_bff_secret` (dulu `minio_root_password`) | lazy (upload 503) |
| `JWT_SECRET`, `OTP_PEPPER`, `DISCOUNT_TOKEN_SECRET`, `ODOO_BFF_API_KEY`, `WHATSAPP_TOKEN`, `WEBHOOK_PAYMENT_SECRET` | sama seperti sebelumnya | | |

Dihapus dari kode: `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD(_FILE)`.

### Material TLS (path; dibaca langsung sebagai Buffer, **tidak** disalin ke env; gagal start bila tak terbaca)
| Env | Default | Untuk |
|---|---|---|
| `TLS_CERT_FILE`, `TLS_KEY_FILE` | — (wajib kecuali plaintext non-prod) | server HTTPS BFF, mis. `/run/secrets/tls_api_gateway_crt` / `_key`; SAN harus memuat `api-gateway` |
| `EFN_CA_FILE` | `/run/secrets/efn_ca_crt` | default semua CA di bawah |
| `PG_SSLROOTCERT` | ↑ | PostgreSQL |
| `REDIS_TLS_CA_FILE` | ↑ | Redis |
| `AMQP_TLS_CA_FILE` | ↑ | RabbitMQ |
| `MINIO_CA_FILE` | ↑ | MinIO |
| `HEALTHCHECK_CA_FILE`, `HEALTHCHECK_SERVERNAME` | ↑, `api-gateway` | `healthcheck.js` |

### Non-rahasia baru/berubah
| Env | Default | Catatan |
|---|---|---|
| `PG_SSLMODE` | `verify-full` | hanya `verify-full`; `disable` butuh `EFN_ALLOW_PLAINTEXT` |
| `PG_USER` | `efn_bff` | dulu `efn` |
| `REDIS_URL` | `rediss://redis:6379` | `redis://` ditolak tanpa flag |
| `REDIS_USERNAME` | — | user ACL `efn-bff` |
| `AMQP_URL` | `amqps://efn-bff@rabbitmq:5671` | username **wajib** (= `user_id` publish) |
| `MINIO_ENDPOINT`, `MINIO_PORT`, `MINIO_USE_SSL`, `MINIO_ACCESS_KEY` | `minio`, `9000`, `true`, — | `host:port` lama masih diterima |
| `EFN_ALLOW_PLAINTEXT` | `false` | ditolak bila `NODE_ENV`/`APP_ENV`=production (NODE_ENV kosong = production) |
| `APP_ENV` | — | ikut deteksi production |
| `TRUSTED_PROXIES` | loopback+RFC1918+ULA | pemisah koma/titik koma/spasi/baris baru; tiap entri divalidasi (IP/CIDR v4/v6 atau `loopback|linklocal|uniquelocal`), entri rusak → gagal start. DevSecOps menambahkan rentang Cloudflare |
| `PII_ACTIVE_KEY_VERSION` | `1` | harus punya kunci |
| `LOYALTY_MAX_POINTS_PER_EVENT` | `100` | SEC-08 |
| `OTP_GLOBAL_HOURLY_BUDGET` | `1000` | budget WA login/jam (SEC-06) |
| `OTP_POS_HOURLY_BUDGET` | `5000` | budget WA POS/jam (terpisah) |
| `OTP_RL_MEMBER_GLOBAL_LIMIT`, `OTP_RL_MEMBER_GLOBAL_WINDOW_SECONDS` | `10`, `3600` | plafon per nomor lintas IP (login) |
| `OTP_AUDIT_RETENTION_DAYS` | `90` | |
| `PROCESSED_MESSAGE_RETENTION_DAYS`, `OUTBOX_RETENTION_DAYS` | `14`, `14` | |
| `RETENTION_PURGE_INTERVAL_MS` | `21600000` (6 jam) | 0 = mati |
| `WHATSAPP_GRAPH_BASE_URL` | `https://graph.facebook.com` | kini wajib `https://` (verifikasi CA publik default) |

Metrik baru: `efn_otp_request_total{outcome="budget_exhausted"}` (kandidat alert).

## G3.3 Perubahan skema (01/02 diubah langsung — Fase 0, tanpa data prod; volume dev lama harus di-init ulang)
- Prasyarat: role dari `00-roles.sh`; 01 gagal jelas bila `efn_bff/efn_ocr/efn_etl/efn_metabase` belum ada. `efn_readonly` dibuat idempoten. DB `metabase` dibuat `OWNER efn_metabase` (idempoten via `\gexec`).
- `REVOKE ALL ON DATABASE efn_platform FROM PUBLIC`; CONNECT ke `efn_bff`, `efn_ocr`, `efn_readonly` (+`efn_exporter` bila ada). `REVOKE CREATE ON SCHEMA public FROM PUBLIC`.
- Skema `platform` **dimiliki `efn_bff`**; semua tabel dibuat lewat `SET ROLE efn_bff` (pemilik = efn_bff).
- `price_hunter_report`: `user_id` **dihapus** → `user_hash char(64) NOT NULL CHECK '^[0-9a-f]{64}$'`, `user_enc bytea NOT NULL`; index `(user_hash, created_at DESC)`.
- `otp_audit.ip inet` → `ip_enc bytea`.
- `loyalty_ledger.member_ref_enc bytea NOT NULL`; CHECK hex pada `member_ref_hash`; `company_code` CHECK EFN/RTL/FNB/FARM (tetap).
- Index baru `ix_outbox_published` (purge).
- Grant level-kolom:
  - `efn_ocr`: SELECT `id, user_hash, product_name, reported_price, competitor_name, geo_lat, geo_lng, image_object, image_phash, status, extracted, confidence, reason, created_at, updated_at` (superset permintaan data-AI; tanpa `user_enc`) + UPDATE `(status, reason, extracted, confidence, image_phash, updated_at)`. Tidak ada akses ledger/outbox/processed_message/otp_audit.
  - `efn_readonly` (→ `efn_etl`): `price_hunter_report` kolom ETL dengan `user_hash` (tanpa `user_enc`, `image_object`, `image_phash`); `otp_audit (id, purpose, outcome, created_at)`; ledger tanpa `member_ref_enc`; outbox tanpa `payload`; processed_message.

## G3.4 Status temuan SEC (bagian BFF)
| ID | Status | Implementasi |
|---|---|---|
| SEC-02 | Selesai sisi BFF | kredensial per service (PG `efn_bff`, MinIO `efn-bff`, RabbitMQ `efn-bff`, Redis ACL `efn-bff`), grant tabel per role |
| SEC-03 | Selesai sisi BFF | parser CIDR panjang tervalidasi; spec XFF palsu langsung/lewat CF/IPv6 (`client-ip.spec.ts`). Butuh DevSecOps mengisi rentang CF |
| SEC-05 | Selesai sisi BFF | `rediss://` + username/password ACL |
| SEC-06 | Selesai sisi BFF | 62-only (`400 invalid_phone`), budget global per jam (login & POS terpisah), kuota per (nomor, IP) + plafon per nomor 10/jam, lock per challenge. Captcha/Turnstile **belum** |
| SEC-07 | Selesai sisi BFF | consume wajib `member_ref` → `409 member_mismatch` (token tidak terbakar). Odoo harus mengirim `member_ref` dari partner |
| SEC-08 | Selesai | `user_id=efn-ocr`, points ≤ 100, source allowlist, report `valid` + `user_hash` cocok, idempotency_key terikat report |
| SEC-11 | Sebagian | purge otp_audit/processed_message/outbox; DSAR/consent = backlog (sesuai instruksi lead) |
| SEC-16 | Selesai sisi DB | ETL membaca `user_hash`, tidak butuh `otp_pepper` |
| SEC-17 | Selesai | pembulatan 3 desimal di server |
| Info `notify.push` `in` | Selesai | `hasOwnProperty` |

## G3.5 Desain lockout (SEC-06) — ringkas
Kuota keras 3/15 menit dipindah dari "per nomor" ke "per (nomor, IP klien)", sehingga penyerang di IP lain tidak bisa menghabiskan kuota korban; plafon per nomor lintas IP (10/jam) tetap ada untuk membatasi spam/biaya WA ke korban (untuk mencapai plafon, penyerang harus terus mengirim → terlihat di metrik). Verifikasi tidak pernah dikunci per nomor: lock hanya pada challenge (`otp:<uuid>`, 3 percobaan) yang id-nya hanya dipegang peminta, dan rate limit verify hanya per IP. Budget global per jam dipisah per purpose agar pumping login publik tidak mematikan OTP kasir. Sisa risiko: penyerang dengan ≥10 IP/jam masih bisa menahan nomor korban di plafon per-nomor → mitigasi lanjutan captcha (belum).

## G3.6 Asumsi / dependensi ke agent lain
1. **DevSecOps**: secret & env di G3.2; sertifikat `tls_api_gateway_*` dengan SAN `api-gateway`; RabbitMQ user `efn-bff` butuh **configure** pada `ocr.requested`, `loyalty.earn.requested`, `notify.push` + `.dlq` (BFF men-declare ketiganya saat connect), write `ocr.requested`, read dua lainnya; Prometheus scrape HTTPS; Traefik `serversTransport` ke `https://api-gateway:3000`; healthcheck compose (bila override) memakai `node healthcheck.js`. `00-roles.sh` berjalan sebelum `01-*.sql` dan role memiliki password.
2. **Data-AI (worker)**: publish `loyalty.earn.requested`/`notify.push` v2 dengan properti AMQP `user_id = efn-ocr`, `member_hash` = `user_hash` report; konsumsi `ocr.requested` v2 (`user_hash`). Worker publish sebelum COMMIT → BFF menunggu ≤1,7 s lalu requeue sekali (G3 loyalty README).
3. **Odoo**: kirim `member_ref` (nomor partner) di consume; panggil `https://api-gateway:3000` dengan `EFN_CA_FILE`.
4. **Frontend**: `BFF_INTERNAL_URL=https://api-gateway:3000` + `NODE_EXTRA_CA_CERTS`; tangani `400 invalid_phone`.
5. Tipe: `FastifyAdapter` menerima `{ https, trustProxy, bodyLimit }` (cast ke `ConstructorParameters`), `minio` v8 `transportAgent`, amqplib meneruskan socket options TLS untuk `amqps`. Spec `client-ip.spec.ts` memakai `fastify.inject({ remoteAddress })`.
6. Spec TLS tidak memakai sertifikat/kunci sungguhan (kebijakan tanpa kunci di repo); validasi pasangan cert/key OpenSSL di-stub lewat `tlsKeyPairCheck` pada test positif.

## G3.7 Belum
- Job re-encrypt rotasi kunci PII; captcha OTP; plafon poin per member per hari; JWT `sub` opak & DSAR/consent (backlog lead).

# Gelombang 5 — v3 (ALTCHA, member opak, payment, privasi/DSAR, rotasi kunci, SEC-07/19/23/24) — 2026-10-05

> **Build, lint, dan test BELUM dijalankan lokal** (tidak ada Node). Tidak ada dependency npm baru (ALTCHA manual dengan
> `node:crypto`; egress publik `node:https`). Kontrak acuan: `docs/04` §8.1–§8.10 (v3–v3.3) + pesan lead selama kerja.

## G5.1 File utama
| Area | File |
|---|---|
| ALTCHA (§8.1) | `src/auth/altcha.service.ts` (+spec), `auth.controller.ts` (`GET /v1/auth/challenge`), `auth.service.ts`, `dto/auth.dto.ts` |
| Member opak (§8.2) | `src/common/member/member.service.ts`, `common/security/access-token.service.ts` (sub=UUID, step-up), `common/security/jwt-auth.guard.ts` (async, cek member aktif, `StepUpToken`) |
| Payments (§8.3) | `src/payments/` — `payment-gateway.ts`, `gateways/{mock,xendit,midtrans}.gateway.ts`, `payment-gateway.factory.ts`, `payments.service.ts`, `payments.controller.ts`, `dto/payment.dto.ts`, `payments.module.ts`, `payments.spec.ts`, README |
| Privasi (§8.4/8.7/8.9) | `src/privacy/` — `consent.service.ts`, `dsar.service.ts`, `erasure.service.ts`, `erasure-completed.consumer.ts`, `privacy.controller.ts`, `pos-privacy.controller.ts`, `dto/privacy.dto.ts`, `privacy.module.ts`, `privacy.spec.ts`, README |
| Rotasi kunci PII (U2) | `config/app-config.ts` (`PII_RETIRED_VERSIONS`), `common/pii/{pii-columns,reencrypt,pii-key-check}.ts`, `src/cli/pii-reencrypt.ts` → `node dist/cli/pii-reencrypt.js`, `common/pii/reencrypt.spec.ts` |
| SEC-19 | `common/concurrency/semaphore.ts` (+spec), `market-scout/image/image-processor.ts` |
| SEC-23 | `common/net/public-https.ts`, `otp/whatsapp/cloud-api-whatsapp.sender.ts` (default transport), adapter payment |
| SEC-24 | `common/pii/pii.ts` (`redisKeyHash`, `erasureTombstone`), `otp/otp.service.ts` (`m` terenkripsi), `auth/auth.service.ts` (key IP ter-HMAC) |
| SEC-07 sisa | `otp/otp.service.ts` (field `t`), `pos/discount-token.service.ts` (`amount`, `amountWithinBounds`), `pos/dto/pos.dto.ts`, `pos/pos.service.ts` |
| §8.10 | `common/amqp/queues.ts` (`checkQueueWithDlq`), `common/amqp/amqp.service.ts`; `common/metrics/metrics-server.ts` (+spec) menggantikan `metrics.controller.ts` (dihapus) |
| Lain | `market-scout/*` (member + consent lokasi), `common/retention/retention.service.ts`, `common/pii/price-hunter-subject.ts` (user_enc NULL), `common/db/pg.provider.ts` (`createPgPool`), `market-scout/storage/object-storage.ts` (`objectStorageProvider`), `common/errors/api-error.ts`, `common/testing/fakes.ts` (`FakeMemberTable`), `app.module.ts`, `common.module.ts`, `Dockerfile` (EXPOSE 9464) |
| SQL | **baru** `infra/postgres/init/03-platform-v3.sql` (idempoten; 01/02 tidak diubah) |
| Spec diubah | `auth/auth.service.spec.ts` (ditulis ulang), `pos/pos.service.spec.ts`, `pos/pos.controller.spec.ts`, `otp/otp.service.spec.ts`, `market-scout/market-scout.service.spec.ts`, `common/retention/retention.service.spec.ts`, `common/amqp/queues.spec.ts` |
| README | baru `privacy`, `payments`; bagian "v3 — Gelombang 5" di `auth`, `pos`, `common`, `market-scout`, `otp` |

## G5.2 Env & secret BARU yang dibaca BFF
### Secret
| Env hasil | Env file | Secret | Wajib |
|---|---|---|---|
| `ALTCHA_HMAC_KEY` | `ALTCHA_HMAC_KEY_FILE` (fallback `/run/secrets/altcha_hmac_key`) | `altcha_hmac_key` (≥16 karakter) | **ya** (gagal start) |
| `PAYMENT_PROVIDER_API_KEY` | `PAYMENT_PROVIDER_API_KEY_FILE` (fallback `/run/secrets/payment_provider_api_key`) | `payment_provider_api_key` | hanya bila `PAYMENT_PROVIDER=xendit|midtrans` (lazy; gagal → 503) |
`webhook_payment_secret` (sudah ada) kini dipakai: mock webhook HMAC, token callback Xendit. **Tidak** dibaca BFF: `member_hash_pepper` (Odoo), `rabbitmq_password_etl` (ETL).

### Non-rahasia
| Env | Default | Catatan |
|---|---|---|
| `ALTCHA_MAX_NUMBER` / `ALTCHA_TTL_SECONDS` / `ALTCHA_CHALLENGE_RL_LIMIT` | `100000` / `300` / `120` per 15 menit | |
| `POS_OTP_AMOUNT_TOLERANCE_PCT` | `20` | SEC-07 (§8.8) |
| `PAYMENT_PROVIDER` | `mock` (non-prod) / `disabled` (prod) | `mock|xendit|midtrans|disabled`; production + `mock` → **gagal start**; prod wajib eksplisit bila ingin aktif |
| `PAYMENT_PROVIDER_EXPERIMENTAL` | `false` | wajib `true` untuk xendit/midtrans (kerangka belum diuji sandbox) |
| `PAYMENT_PROVIDER_BASE_URL` | — | https saja (mis. sandbox vendor) |
| `PAYMENT_QR_TTL_SECONDS` / `PAYMENT_TIMEOUT_MS` / `PAYMENT_STATUS_REFRESH_SECONDS` / `MIDTRANS_QRIS_ACQUIRER` | `600` / `8000` / `10` / `gopay` | |
| `CONSENT_LOCATION_VERSION`, `CONSENT_LOCATION_TEXT_SHA256`, `CONSENT_MARKETING_VERSION`, `CONSENT_MARKETING_TEXT_SHA256` | — | §8.9; tanpa ini grant → `503 consent_unavailable` (penarikan tetap diterima). Frontend harus memakai versi yang sama |
| `DSAR_DELETE_GRACE_DAYS` / `DSAR_JOB_INTERVAL_MS` / `DSAR_EXPORT_HOURLY_LIMIT` / `DSAR_REAUTH_LIMIT` | `7` / `60000` / `10` / `3` | |
| `PRICE_HUNTER_USER_ENC_RETENTION_DAYS` / `PRICE_HUNTER_REPORT_RETENTION_DAYS` | `90` / `730` | §8.4 (menunggu DPO) |
| `IMAGE_MAX_CONCURRENCY` / `IMAGE_QUEUE_MAX` / `IMAGE_QUEUE_TIMEOUT_MS` / `SHARP_CONCURRENCY` | `2` / `8` / `10000` / `1` | SEC-19 |
| `PII_RETIRED_VERSIONS` / `PII_REENCRYPT_BATCH_SIZE` | — / `500` | U2 |
| `METRICS_PORT` / `METRICS_TLS_CLIENT_ALLOWED_CN` | **`9464`** / `prometheus` | §8.10; 0 = mati |
| `METRICS_CLIENT_CA_FILE` (path material TLS) | `EFN_CA_FILE` → `/run/secrets/efn_ca_crt` | CA verifikasi sertifikat klien Prometheus |

**Dihapus/tidak lagi diandalkan:** `NODE_EXTRA_CA_CERTS` di api-gateway (SEC-23; compose devsecops sudah menghapus). `/metrics` di port 3000 **dihapus**.

## G5.3 Perubahan skema (`03-platform-v3.sql`, idempoten; volume lama: jalankan manual sebagai superuser)
- **Baru** `platform.member (id uuid PK, member_hash char(64) UNIQUE, member_ref_enc bytea NOT NULL, created_at, deleted_at)`.
- **Baru** `platform.consent (id bigserial, member_id uuid, purpose ∈ {location, marketing}, granted, version, text_sha256 char(64) NOT NULL, source, ip_enc, created_at)` — append-only: hak efn_bff `SELECT, INSERT, UPDATE(ip_enc)` + trigger tolak DELETE/TRUNCATE/UPDATE kolom lain. `member_id` tanpa FK (bukti tetap ada setelah member dihapus).
- **Baru** `platform.dsar_request (id, member_id, member_hash, type ∈ {export, delete, correction, phone_change}, status ∈ {pending_reauth, scheduled, cancelled, processing, completed, rejected, failed}, stores_pending text[] ⊆ {platform,odoo,clickhouse}, requested_at, scheduled_at, completed_at, reason)` + unique parsial satu `delete` aktif per member.
- **Baru** `platform.payment_intent` persis §8.3 (+CHECK provider/status/currency/method/company_code, unique `(provider, provider_ref)`).
- `price_hunter_report.user_enc` → **NULLABLE** (retensi 90 h & erasure).
- `loyalty_ledger.member_ref_enc` → NULLABLE + kolom **`erased_at timestamptz`** + `CHECK (member_ref_enc IS NOT NULL OR erased_at IS NOT NULL)` (§8.7).
- Grant: `efn_etl` SELECT `(id, member_hash, status, stores_pending)` pada `dsar_request` (permintaan data-AI); `efn_readonly` + SELECT `loyalty_ledger.erased_at`. member/consent/payment_intent: tidak ada akses role lain.

## G5.4 Perubahan kontrak / keputusan yang perlu lead catat
1. **JWT `sub` = member.id** → semua sesi lama (sub nomor HP) tidak valid setelah deploy; pengguna login ulang.
2. **ALTCHA**: challenge standar `{algorithm, challenge, salt, signature, maxnumber}`; salt berformat `hex24?expires=<unix>&`. Prefix Redis baru **`altcha:used:*`** → DevSecOps tambahkan ke ACL `efn-bff`.
3. **Login** saat akun `processing` dihapus → `409 account_deleting` (selama masa tenggang tetap boleh, §8.9).
4. **Consent**: `503 consent_unavailable` bila teks aktif belum dikonfigurasi; penarikan menyimpan `text_sha256` teks aktif (atau sha256 string kosong bila belum dikonfigurasi). Hak `UPDATE(ip_enc)` pada consent = penyimpangan kecil dari "INSERT/SELECT saja" — dibutuhkan erasure (NULL) & re-encrypt; trigger mencegah perubahan kolom lain.
5. **Erasure platform dijalankan dalam transaksi yang sama dengan `processing`** (permintaan lead): saat commit `stores_pending = {odoo, clickhouse}`. Pilihan anonimisasi = **tombstone** `sha256("erased:"||request_id)` pada `loyalty_ledger.member_ref_hash`, `price_hunter_report.user_hash`, `otp_audit.subject` (+ `erased_at` di ledger, `updated_at` laporan dinaikkan agar ETL menerima versi teranonim). Saldo dihanguskan dengan entri `forfeit` per PT (`source = 'dsar'`, `reason = 'dsar_forfeit'`, `idempotency_key = dsar:forfeit:<request_id>:<PT>`) — perlakuan akuntansi: Finance. Baris `member` **dihapus**. `dsar_request.member_hash` dipertahankan.
6. **Export** (`GET /v1/me/data`) dicatat `dsar_request (type export, status completed)`; hasil dipotong 1000 baris per bagian (`truncated`), jalur `202` + unduhan belum.
7. **Erasure Odoo**: `GET …/erasures?store=odoo` → `{ items: [{ request_id, member_hash }] }` (dibakukan lead); complete `200 { ok, status }`, `404`, `409 not_processing`. **ClickHouse**: event wajib `idempotency_key` persis `erasure:<request_id>:clickhouse` & `user_id = efn-etl`, selain itu DLQ.
8. **Payment**: nilai `PAYMENT_PROVIDER=disabled` (default production); kode error `409 idempotency_conflict`, `409 payment_in_progress`, `502 payment_provider_error`, `503 payment_provider_unavailable`; endpoint dev `POST /v1/pos/payments/:id/simulate` (404 di production); webhook untuk `provider_ref` tak dikenal / duplikat → `200`; transisi `expired|cancelled|failed → paid` diizinkan (pembayaran terlambat, refund manual); nominal webhook berbeda → status tidak diubah. Xendit cancel tidak didukung → `cancelled` lokal.
9. **SEC-07** sesuai §8.8 (tidak ada perbedaan); batas inklusif `[50%, 100%+tol]`; token pra-v3 tanpa `amount` → `invalid`.
10. **Metrics**: listener mTLS terpisah **:9464** (`GET /metrics` saja) — DevSecOps/QA: scrape `https://api-gateway:9464/metrics` dengan sertifikat klien `tls_prometheus_*`, ekspos port di network monitoring.
11. **RabbitMQ passive declare**: semua queue + DLQ termasuk `privacy.erasure.completed(.dlq)` harus ada di definitions; broker terjangkau + queue hilang → BFF gagal start (`QueueMissingError`); broker mati → tetap start & reconnect. `efn-bff` butuh **read** `privacy.erasure.completed`. PLAUSIBLE: hak minimum untuk passive `queue.declare` perlu diverifikasi di staging.
12. Retensi BFF: `user_enc` laporan NULL > 90 h, baris laporan dihapus > 730 h. Consent (akun + 5 th) & ledger (10 th) tidak di-purge BFF.

## G5.5 Rotasi kunci PII (U2) — prosedur singkat untuk runbook
1. Tambah `PII_ENCRYPTION_KEY_V2_FILE`, set `PII_ACTIVE_KEY_VERSION=2`, deploy (v1 masih dimuat).
2. `docker compose exec api-gateway node dist/cli/pii-reencrypt.js --dry-run` lalu tanpa `--dry-run` (idempotent; exit 1 bila ada baris gagal dekripsi).
3. Tunggu ≥ 15 menit (challenge OTP login di Redis terenkripsi v1, umur ≤ 13 menit).
4. Set `PII_RETIRED_VERSIONS=1`, hapus secret v1, deploy. BFF menolak start bila masih ada ciphertext v1 (`PiiKeyRingCheck`).

## G5.6 Status SEC (bagian BFF)
| ID | Status |
|---|---|
| SEC-06 | Selesai sisi BFF: ALTCHA wajib di `/v1/auth/otp/request` |
| SEC-07 | Selesai sisi BFF: `amount` diikat ke token, `409 amount_mismatch` (Odoo menyusul) |
| SEC-11 | Sisi BFF: consent, DSAR, erasure, retensi laporan. Sisa: konsol DPO, export besar, koreksi/ganti nomor |
| SEC-19 | Selesai: semaphore + `sharp.concurrency` + cache off + 429 |
| SEC-23 | Selesai sisi BFF: egress publik memakai root store publik eksplisit |
| SEC-24 | Selesai sisi BFF: key Redis ter-HMAC, nomor HP di Redis terenkripsi. AOF/`--appendonly` keputusan DevSecOps |
| SEC-20/E12 (BFF) | `/metrics` mTLS :9464 |
| U2 | Selesai: v1 opsional bila pensiun + CLI re-encrypt + cek start |

## G5.7 Asumsi belum terverifikasi
- Format ALTCHA v1 (salt dengan `?expires=` + `&`) kompatibel dengan widget `altcha` versi yang dipakai frontend — diuji di staging.
- `sharp.concurrency()`/`sharp.cache(false)` dan `RawBodyRequest` Fastify untuk webhook (rawBody hanya untuk `application/json`; Xendit/Midtrans JSON).
- Adapter Xendit/Midtrans **belum diuji ke sandbox** (endpoint, header, format waktu `settlement_time`, event Xendit `qr.payment`).
- `https.Agent({ ca: tls.rootCertificates })` mengabaikan `NODE_EXTRA_CA_CERTS` (perilaku terdokumentasi Node; verifikasi di CI).
- `pg_try_advisory_xact_lock(int, int)` dengan `hashtext($2)`.- Penyelarasan dengan Odoo engineer (pesan lead terakhir): `GET /v1/pos/payments/:id` selalu memuat `payment_id` & `amount`; idempotency key `pos:<order>:<payment>:<n>` = key berbeda (UNIQUE per string, charset `[\x21-\x7e]{8,128}` menerima `:`); erasure pull → `{ items: [...] }`; consume `amount_total` integer Rupiah diterima (validator menerima angka ≥ 0, desimal juga). Tidak ada ketidakcocokan yang diketahui.

---

# Gelombang 9 (6 Okt 2026) — kontrak v3.6 (docs/04 §8.12, §8.13), review r3 (SEC-29/31/34/39/40), smoke R2/R6

## G9.1 Perubahan
| Item | Perubahan | File |
|---|---|---|
| **SEC-31** tombstone | `randomTombstone()` = 32 byte acak (hex 64) per erasure, sama untuk semua baris subjek; `erasureTombstone(request_id)` dihapus. Entri forfeit: `source_ref` NULL, `idempotency_key = dsar:forfeit:<uuid acak>` (tidak memuat request_id). Tombstone tidak disimpan di tabel/log lain. `completeStore`: saat `stores_pending` kosong → `completed` + `member_hash = NULL`. Erasure juga men-NULL-kan `member_hash` request DSAR lain milik member (export/cancelled). `pendingFor('odoo')` hanya `processing` ber-hash. | `common/pii/pii.ts`, `privacy/erasure.service.ts` |
| SEC-40 (race foto) | Daftar foto diambil di dalam transaksi; hapus MinIO SETELAH commit (pembatalan tidak lagi kehilangan foto). | `privacy/erasure.service.ts` |
| **R2** | `GET /v1/pos/loyalty/balance?member_ref=` **dihapus** → `POST /v1/pos/loyalty/balance` `{ member_ref }` → `200 { points_balance }`. Audit statis seluruh controller: setiap `@Query/@Param` wajib di allowlist non-PII (`store`; `id`, `payment_id`, `provider`, `request_id`, `purpose`). | `pos/pos.controller.ts`, `pos/dto/pos.dto.ts`, `common/route-pii-audit.spec.ts` |
| **R6** | `DELETE /v1/me` → `+ receipt_token` (`v1.<exp>.<b64url HMAC>`, kunci HKDF(jwt_secret, `efn:dsar_receipt:v1`), purpose `dsar_receipt`, terikat request_id, 30 hari, stateless). `GET /v1/dsar/requests/:id` + `X-DSAR-Receipt` → `{ id, type, status, requested_at, scheduled_at, completed_at }`; semua kegagalan → `404 not_found` generik; rate limit per IP-hash `DSAR_RECEIPT_RL_LIMIT` (30)/15 menit. | `privacy/dsar-receipt.service.ts`, `privacy/privacy.controller.ts` (`DsarReceiptController`), `privacy/dsar.service.ts` |
| **SEC-40** step-up | `DELETE /v1/me` memakai `consumeStepUp`: verifikasi + `SET NX EX` `otp:stepup-used:<jti>` sampai exp (prefix di bawah `otp:*` → **ACL Redis tidak berubah**). Pakai ulang → `403 step_up_invalid`. Ekspor tetap `requireStepUp`. | `common/security/access-token.service.ts` (`verifyStepUpClaims`), `privacy/dsar.service.ts` |
| **Access key MinIO** | `AppConfig.minioAccessKey`: `MINIO_ACCESS_KEY_FILE` → fallback `/run/secrets/minio_bff_access_key` → literal `MINIO_ACCESS_KEY` **hanya `APP_ENV=dev`**; di luar dev ≥ 16 karakter, charset `[A-Za-z0-9._~+=-]`. Tidak disalin ke env. | `config/app-config.ts`, `config/secrets.loader.ts` |
| **SEC-29** | `secretsLoader` tidak lagi menyalin `PII_ENCRYPTION_KEY(_V<N>)_FILE` & `MINIO_ACCESS_KEY_FILE` ke `process.env`; `PII_ENCRYPTION_KEY` keluar dari fallback env. `AppConfig` membaca file ke Buffer, decode, nol-kan buffer mentah, cache di objek config. Literal env `PII_ENCRYPTION_KEY*` ditolak saat production. ConfigModule Nest tidak lagi menyimpan salinan secret. | `config/secrets.loader.ts`, `config/app-config.ts`, `app.module.ts` |
| **SEC-34** | Default `ALTCHA_MAX_NUMBER` 100 000 → **500 000**. Rata-rata 250 rb SHA-256: ±1–2,5 s ponsel kelas bawah, < 1 s menengah, terburuk ±5 s. 1 000 000 hanya sementara saat alert budget OTP. Bot native tetap murah → kontrol utama tetap rate limit/plafon/budget. Binding challenge ke IP belum. | `config/app-config.ts`, `auth/README.md` |
| **SEC-39** | Role NOLOGIN `efn_platform_owner` memiliki skema `platform` + semua objek; `efn_bff` hanya DML minimum per tabel (tanpa DELETE ledger/consent/dsar/payment; UPDATE kolom tertentu untuk ledger/otp_audit/outbox/consent). | `infra/postgres/init/01..04` |
| Smoke | Negatif: `POST /v1/pos/loyalty/balance` publik → 404. `delete`: cek `receipt_token` + `dsar.step_up_single_use` (DELETE ulang → 403). `requests`: `dsar.receipt_status` (200, kunci persis), `dsar.receipt_wrong_404`, `dsar.receipt_missing_404`. Receipt tidak dicetak. | `scripts/smoke/bff_smoke.py` |

## G9.2 Perubahan skema
- **Baru `infra/postgres/init/04-platform-v3-6.sql`** (idempoten, satu transaksi): role `efn_platform_owner` (NOLOGIN, NOBYPASSRLS); `REVOKE efn_platform_owner FROM efn_bff` bila anggota; `ALTER SCHEMA platform OWNER`; `REASSIGN OWNED BY efn_bff TO efn_platform_owner`; pemulihan hak penuh pemilik; reset + grant DML `efn_bff`. `dsar_request.member_hash` DROP NOT NULL + backfill (delete `completed`, request terminal milik member terhapus) + CHECK `ck_dsar_member_hash_active` (wajib ada untuk `pending_reauth/scheduled/processing`). Tombstone lama `sha256("erased:"||id)` diganti acak di ledger/laporan/otp_audit; `source_ref`/`idempotency_key` forfeit lama dibersihkan.
- 01: buat `efn_platform_owner`, skema `AUTHORIZATION efn_platform_owner`. 02/03: `SET ROLE efn_platform_owner`. Hack `GRANT ALL ON consent TO efn_bff` di 03 dihapus.
- **Volume lama (dev/staging): jalankan 04 manual sebagai superuser SEBELUM deploy BFF ini dan sebelum menjalankan ulang 02/03**: `docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/04-platform-v3-6.sql`. Tanpa 04, `completeStore` gagal (kolom `member_hash` masih NOT NULL).
- ETL kompatibel (hanya membaca `processing`; grant kolom `efn_etl` tidak berubah, diverifikasi).

## G9.3 Hasil uji
- `npm run build` + `npm run lint` (0 error; 2 warning lama `no-control-regex` tak terkait) + `CI=true npm test`: **27 suite, 313 test lulus** (container `node:22-bookworm`).
- Migrasi SQL di container `postgres:16.6-bookworm`: (a) volume lama = 01–03 versi HEAD + data contoh (tombstone deterministik, forfeit ber-request_id) → 04 → 04 lagi → 02/03 baru → cek; (b) volume baru = 01–04 baru → data → 04 lagi → cek. Hasil: semua tabel/sequence/fungsi milik `efn_platform_owner`; tombstone lama hilang, satu tombstone acak dipakai bersama ledger+otp_audit; forfeit tanpa request_id; `member_hash` NULL untuk delete completed & export member terhapus, request `scheduled` tetap. Sebagai `efn_bff` **ditolak**: `DISABLE TRIGGER`, `GRANT`, UPDATE kolom consent lain, `DELETE`/UPDATE `delta_points` ledger, `TRUNCATE`, `CREATE TABLE`, `DELETE dsar_request`. **Berhasil**: seluruh DML yang dipakai BFF (termasuk `FOR UPDATE SKIP LOCKED` outbox, `FOR UPDATE` payment, `ON CONFLICT DO NOTHING`). CHECK menolak hash NULL pada request aktif. `efn_etl`/`efn_ocr` tetap bisa membaca kolomnya. (Bug yang ditemukan uji ini dan sudah diperbaiki: REASSIGN memindahkan ACL consent yang dicabut sehingga pemilik baru kehilangan hak TRIGGER → 04 kini memulihkan hak penuh pemilik.)
- **Belum**: smoke E2E (`bff_smoke.py` negatives/delete/requests) tidak dijalankan — stack dev tidak menyala saat gelombang ini. Skrip smoke lolos `ruff check` + `ruff format --check` (0.16.10).

## G9.4 Untuk agent lain / lead
- **devsecops (blocking staging/prod)**: compose `api-gateway` masih `MINIO_ACCESS_KEY: efn-bff` (literal). Di luar `APP_ENV=dev` BFF kini **menolak** literal → set `MINIO_ACCESS_KEY_FILE: /run/secrets/minio_bff_access_key` + mount secret (format `akid` gen-secrets = 20 hex, lolos validasi ≥ 16), hapus literal. Tanpa itu upload Market Scout → 503 (kredensial lazy). Deploy: jalankan 04 sebelum BFF baru. Redis ACL tidak berubah.
- **odoo-engineer (R2 klien)**: Odoo wajib memanggil `POST /v1/pos/loyalty/balance` body `{ member_ref }` (GET lama kini 404).
- **frontend (R6 UI)**: dari respons `DELETE /api/me` simpan `receipt_token` di cookie HttpOnly `efn_dsar_receipt` (Path `/api/dsar`, SameSite=Strict, Secure, 30 hari) — jangan diekspos ke JS/log. Buat route `GET /api/dsar/requests/[id]` yang meneruskan cookie sebagai header `X-DSAR-Receipt` ke `GET /v1/dsar/requests/:id`. Tampilkan status (`scheduled/processing/completed/cancelled`) + tanggal; 404 → pesan generik. **Step-up DELETE kini sekali pakai**: setelah DELETE sukses hapus cookie `efn_stepup`; DELETE ulang butuh re-OTP (403 `step_up_invalid`). Ekspor (`/api/me/data`, SEC-32) tetap boleh memakai token yang sama.
- **data-ai**: ETL tidak perlu berubah. Baris ClickHouse `loyalty_events` yang sudah memuat tombstone lama deterministik tidak ikut terganti (watermark `created_at`); opsional job satu kali di mart.
- **architect/lead**: catat di kontrak — `ck_dsar_member_hash_active`, `source_ref` forfeit NULL, `DSAR_RECEIPT_TTL_DAYS`/`DSAR_RECEIPT_RL_LIMIT`, prefix Redis `otp:stepup-used:*`, default ALTCHA 500 000, role `efn_platform_owner`. Rekomendasi SEC-31 #2 (daftar hash terhapus terenkripsi untuk replay setelah restore) **belum** — butuh keputusan kustodi.

## G9.5 Status SEC (bagian BFF)
SEC-29 Fixed. SEC-31 Fixed sisi BFF (sisa: korelasi waktu, replay restore, CH lama — README privacy). SEC-34 Partial (biaya naik; binding IP/adaptif belum). SEC-39 Fixed terhadap `efn_bff` (superuser/DBA tetap bisa; hash-chain consent backlog). SEC-40 step-up & race foto Fixed. R2, R6 Fixed sisi BFF.
---

# Gelombang 12 — SEC-29 tuntas, SEC-34 sisa (7 Okt 2026)

## G12.1 SEC-29: tidak ada secret di `process.env`
- `src/config/secrets.loader.ts`: fungsi `secretsLoader()` (yang mengisi `process.env[X]`) **dihapus**. File kini hanya daftar `KNOWN_SECRET_FILES`, alias `EFN_BFF_API_KEY`, `SECRET_ENV_NAMES`/`isSecretEnvName`.
- `src/config/app-config.ts`: resolver privat `secretValue(name)`: `<NAMA>_FILE` (wajib terbaca, ConfigError menyebut path, bukan nilai) → `${SECRETS_DIR:-/run/secrets}/<nama kontrak>` → env literal **hanya `APP_ENV=dev`**. Nilai di-cache di memori objek; buffer file di-nol-kan. Berlaku untuk JWT_SECRET, OTP_PEPPER, DISCOUNT_TOKEN_SECRET, ODOO_BFF_API_KEY (+alias), WEBHOOK_PAYMENT_SECRET, ALTCHA_HMAC_KEY, PAYMENT_PROVIDER_API_KEY, WHATSAPP_TOKEN, PG/Redis/RabbitMQ password, MinIO secret key (PII & access key MinIO sudah dari G9). Aturan literal PII diseragamkan: `APP_ENV=dev` (sebelumnya "non-production").
- Di luar `APP_ENV=dev`: env literal bernama secret **atau** password di `DATABASE_URL`/`REDIS_URL`/`AMQP_URL`/`RABBITMQ_URL` → `assertStartupSecrets` gagal start (nilai sudah ada di environ, file pun tidak menyelamatkan).
- `fromProcessEnv()` memakai snapshot `{...process.env}`, tidak menulis env. `app.module.ts`: `ConfigModule.forRoot({ isGlobal: true, ignoreEnvFile: true })` (dotenv tidak lagi bisa menyalin `.env` ke `process.env`).
- `testConfig()` (fakes) kini menyuplai secret sebagai **file** (`<NAMA>_FILE=/test-secrets/<NAMA>`), jalur yang sama dengan produksi.
- Spec statis baru `src/config/no-secret-env-write.spec.ts`: gagal bila ada `process.env.<SECRET> =`/`??=`/`||=` atau `process.env['<SECRET>'] =` di seluruh `src` (termasuk spec), dan bila kode non-test menulis `process.env` apa pun (`Object.assign(process.env…)`, dll.). Ada kontrol positif regex.
- Compose tidak perlu diubah: semua secret api-gateway sudah `*_FILE`, URL tanpa password (dicek `docker-compose.yml`, `.dev.yml`, `.payment.yml`).

## G12.2 SEC-34: ALTCHA terikat IP + biaya adaptif
- `signature = HMAC(altcha_hmac_key, "efn-altcha:v2\n" + challenge + "\n" + ip_binding)`; `ip_binding` = IPv4 utuh / IPv6 prefix /64 / `unknown`. IP tidak dikirim/simpan. `verify(payload, ip)` → solusi dari IP lain = `400 captcha_required` generik. Format JSON tidak berubah (frontend & widget tidak perlu diubah; challenge & request sama-sama lewat Next.js yang meneruskan XFF ke hop tepercaya).
- Biaya adaptif: `maxnumber = ALTCHA_MAX_NUMBER × 2^level` dipotong `ALTCHA_MAX_NUMBER_CAP` (default max(4 000 000, base), ≤ 10 000 000, harus ≥ base). Level 0..3 = maks(level per IP: rasio challenge/IP atau OTP login/IP, ambang 25/50/75 %; level global: OTP login jam ini / `OTP_GLOBAL_HOURLY_BUDGET`, ambang 50/75/90 %). Membaca counter Redis rate limit yang SUDAH ada lewat `RateLimiter.peek` (tanpa menambah, tanpa key baru). `ALTCHA_ADAPTIVE=false` mematikan. Verifikasi menerima number ≤ cap.
- Metrik baru `efn_altcha_challenge_total{level}` (tanpa IP).
- **Dampak UX** (didokumentasikan di `src/auth/README.md`): normal tidak berubah (±1–2,5 s ponsel bawah). Level 1 ±2–5 s, level 2 ±4–10 s, level 3 ±8–20 s ponsel bawah (< 4 s desktop). Terkena: IP bersama (CGNAT seluler, Wi-Fi publik/kantor) setelah ≥ 5 OTP/15 menit dari IP itu, atau semua pengguna saat budget global ≥ 50 %. Pindah jaringan antara verifikasi & kirim → `captcha_required`, ulangi verifikasi. Saat deploy, challenge lama (≤ 5 menit) ditolak sekali.
- `scripts/smoke/bff_smoke.py`: solver PoW ±2x lebih cepat (prefix salt di-hash sekali + `copy()`), komentar SEC-34. Tidak ada perubahan alur (challenge & request dari klien yang sama).

## G12.3 Validasi
- Container `node:22.12-alpine@sha256:51eff88a…` (digest Dockerfile): `npm run build` OK, `npm run lint` 0 error (2 warning lama `no-control-regex`), `CI=true npm test` **28 suite, 332 test lulus** (+19 test).
- **Cek runtime `/proc/<pid>/environ`** (container yang sama, secret file dummy acak di `SECRETS_DIR`, sebagian via `_FILE`): (1) `node dist/main.js` hidup → `/proc/<pid>/environ` **0 nilai secret** (22 var); (2) in-process setelah semua getter secret disentuh: `process.env`, `/proc/self/environ`, env child process (`sh -c env`), `process.report.environmentVariables` → **0 secret** semua. Kontrol pola lama (`process.env.X = v`): bocor ke child = true, process.report = true, `/proc/self/environ` = false — catatan teknis: `/proc/<pid>/environ` hanya memuat env awal proses, jadi vektor nyata pola lama adalah child process & diagnostic report/heap dump; keduanya kini tertutup.
- `bff_smoke.py` lolos parse Python.
- **Belum**: `run-e2e-smoke.sh a` **tidak dijalankan**. Stack dev menyala dan dipakai agent lain; bind mount Windows tidak memicu `nest --watch`, sehingga BFF dev yang berjalan **masih kode lama** sampai `docker compose restart api-gateway` (tidak saya lakukan). Setelah restart: jalankan `scripts/smoke/run-e2e-smoke.sh a`.

## G12.4 Untuk lead / agent lain
- **devsecops**: alert opsional `increase(efn_altcha_challenge_total{level="3"}[15m]) > 0`; tidak ada perubahan compose/secret. Pastikan tidak ada `.env` berisi secret di image (kini diabaikan juga).
- **frontend (opsional)**: teks progres "Memverifikasi perangkat…" saat PoW lama; reset widget pada `captcha_required` sudah ada.
- **architect**: catat di docs/04 §8.1: format signature v2 (domain + IP binding), env `ALTCHA_MAX_NUMBER_CAP`/`ALTCHA_ADAPTIVE`, metrik baru; §7.3: env literal secret hanya `APP_ENV=dev`.

## G12.5 Status SEC (bagian BFF)
SEC-29 **Fixed** (semua secret BFF; spec statis + verifikasi runtime). SEC-34 **Fixed** sisi BFF (binding IP + biaya adaptif; sisa by design: PoW memperlambat, bukan mencegah — kontrol utama tetap rate limit + budget).
