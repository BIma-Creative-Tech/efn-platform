# `services/wa-unofficial` — WhatsApp tidak resmi (Baileys), terisolasi

**ADR terkait:** ADR-044 (**Proposed**), K18-01 di `CLAUDE.md`, ADR-022 (TLS/enkripsi), ADR-024 (kredensial per
service). Kontrak: `docs/04` §28.7, §28.9, §28.10, L-158/L-159. Risiko: **R-B16** (ban/ToS), **R-S21** (supply chain &
pengambilalihan sesi), **R-B17** (spam), R-L01.

> **Status: dibangun G50 (kode + unit test), BELUM PERNAH dijalankan terhadap WhatsApp sungguhan.**
> `WA_UNOFFICIAL_ENABLED=false` default di semua environment; **OFF di production** sampai penerimaan risiko tertulis
> pemilik proyek + Legal (`WA_UNOFFICIAL_RISK_ACCEPTANCE_REF`, Q-SM14) dan entri PDP disetujui DPO (Q-SM18).
> Penggunaan ini melanggar ToS WhatsApp secara sadar (ADR-044 §4.2).

## Cakupan
- **Hanya** consume `notify.wa_unofficial` dan publish `notify.wa_unofficial.result`. Allowlist kategori di kode:
  `promo`, `notify_restock`, `price_alert`; kategori lain → ack + `failed/invalid_category`.
- Tidak punya akses PostgreSQL, Redis BFF, Odoo, ClickHouse, atau kunci PII BFF. Nomor tujuan datang sudah didekripsi
  BFF per pesan dan diverifikasi ulang terhadap `to_hash` (HMAC, kunci bersama hanya BFF + service ini).
- Nomor WhatsApp **terpisah** dari WABA resmi (Q-SM15). OTP/pesanan/pembayaran/poin tidak pernah lewat sini.

## Gerbang start
| Kondisi | Perilaku |
|---|---|
| `WA_UNOFFICIAL_ENABLED` ≠ string persis `true` | **idle**: hanya `GET /health` → `{status:"idle"}`; tidak konek RabbitMQ/WhatsApp |
| aktif + `APP_ENV=production` (atau `APP_ENV` kosong) tanpa `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` | **menolak start** (exit 78) |
| aktif + konfigurasi/secret/TLS tidak lengkap | menolak start (exit 78), pesan hanya nama variabel |
| aktif + lengkap | run |

## Pagar per pesan (`processor.ts`)
publisher `user_id` = `efn-bff` → parse + allowlist + `to_hash == HMAC(to_e164)` → dedup `idempotency_key` (state
lokal 48 jam) → `expires_at` → penerima pernah STOP (7 hari) → sesi (tunggu ≤ 30 dtk; `banned` → result `banned` +
DLQ; terputus → `disconnected` + DLQ) → `WA_UNOFFICIAL_MAX_PER_DAY` / `MAX_PER_MEMBER_PER_DAY` (→ `rate_limited`, ack)
→ `WA_UNOFFICIAL_RATE_PER_MIN` (menunggu slot) → jitter acak 2–8 dtk → cek nomor terdaftar WA → tandai diproses +
flush (at-most-once) → kirim → result `sent`/`failed`.
Pesan masuk "STOP"/"BERHENTI"/"UNSUBSCRIBE" → penekan lokal + publish `opt_out` (BFF mencabut consent `marketing`).

## Endpoint admin (port 8443, HTTPS)
Di depan: Traefik entrypoint `wg-https` + middleware `internal-ops`, mTLS CN operator (devsecops). Di service: header
`Authorization: Bearer <wa_unofficial_admin_token>`; 10 gagal/menit → 429.
| Endpoint | Respons |
|---|---|
| `GET /health` (tanpa auth) | `{ "status": "ok" \| "idle" }` |
| `GET /v1/admin/session` | `{ status: connected\|disconnected\|banned, detail, self: "62811****999"\|null, enabled, sent_today, max_per_day }` |
| `POST /v1/admin/session/pair` | `{ "method": "qr" }` → `{ qr }` (payload QR mentah, operator merender) atau `{ "method": "code", "phone": "62…" }` → `{ code }`; 409 bila sudah terhubung/banned |
| `POST /v1/admin/session/logout` | logout + hapus material sesi → `{ status }` |
| `GET /metrics` | teks Prometheus dengan `Bearer <wa_unofficial_metrics_token>` (opsional; 404 bila tidak dikonfigurasi) |
QR/kode pairing tidak pernah di-log; respons `Cache-Control: no-store`.

## Env & secret
| Nama | Default | Keterangan |
|---|---|---|
| `APP_ENV` | `production` bila kosong | |
| `WA_UNOFFICIAL_ENABLED` | — (idle) | `true` untuk aktif |
| `WA_UNOFFICIAL_RISK_ACCEPTANCE_REF` | — | wajib di production, pola `^[A-Za-z0-9._/-]{3,64}$` |
| `AMQP_URL` | `amqps://efn-wa-unofficial@rabbitmq:5671` | tanpa password |
| `RABBITMQ_PASSWORD_FILE` | `/run/secrets/rabbitmq_password_wa_unofficial` | |
| `AMQP_TLS_CA_FILE` | `/run/secrets/efn_ca_crt` | CA internal |
| `WA_UNOFFICIAL_SESSION_KEY_FILE` | `/run/secrets/wa_unofficial_session_key` | 32 byte (base64 atau hex 64) |
| `WA_UNOFFICIAL_HASH_KEY_FILE` | `/run/secrets/wa_unofficial_hash_key` | ≥ 32 karakter, **sama** dengan BFF |
| `WA_UNOFFICIAL_ADMIN_TOKEN_FILE` | `/run/secrets/wa_unofficial_admin_token` | ≥ 32 karakter |
| `WA_UNOFFICIAL_METRICS_TOKEN_FILE` | `/run/secrets/wa_unofficial_metrics_token` | opsional, ≠ token admin |
| `TLS_CERT_FILE` / `TLS_KEY_FILE` | `/run/secrets/wa_unofficial_tls_crt` / `_key` | sertifikat server CA internal (CN/SAN `wa-unofficial`) |
| `WA_UNOFFICIAL_STATE_DIR` | `/var/lib/wa-unofficial` | volume terenkripsi (sesi + state) |
| `WA_UNOFFICIAL_RATE_PER_MIN` / `_MAX_PER_DAY` / `_MAX_PER_MEMBER_PER_DAY` | 10 / 300 / 1 | |
| `WA_UNOFFICIAL_JITTER_MIN_MS` / `_MAX_MS` | 2000 / 8000 | min tidak boleh < 2000 |
| `WA_UNOFFICIAL_PROXY_URL` | — | proxy egress HTTP CONNECT (allowlist host WA di proxy) |
| `WA_UNOFFICIAL_SENDER_LABEL` | `Promo EFN` | judul pesan |
| `PORT`, `LOG_LEVEL`, `HEALTHCHECK_SERVERNAME` | 8443, info, `wa-unofficial` | |
| `EFN_ALLOW_PLAINTEXT` | — | hanya berlaku di `APP_ENV` dev/test |

## Penyimpanan
`/var/lib/wa-unofficial/session/*.bin` (auth state Baileys) dan `/var/lib/wa-unofficial/state/state.bin` (dedup,
kuota, opt-out berupa `to_hash`) — semua AES-256-GCM, AAD = nama logis berkas, mode 0600. Kunci salah → gagal muat
(tidak ada fallback ke sesi kosong). Rotasi kunci = logout + pairing ulang. Backup volume hanya lewat backup terenkripsi.

## Dependency
| Paket | Versi | Lisensi | Catatan |
|---|---|---|---|
| `@whiskeysockets/baileys` | **6.7.24 (exact)** | MIT | library tidak resmi; dist-tag `legacy` npm per 20 Nov 2026 (7.x masih RC). Minimal 6.7.22: < 6.7.22 terkena GHSA-qvv5-jq5g-4cgg (spoofing pesan, critical). 6.x menarik `libsignal` dari GitHub (git dep) |
| `amqplib` | ^0.10.5 | MIT | |
| `https-proxy-agent` | ^7.0.6 | MIT | hanya bila `WA_UNOFFICIAL_PROXY_URL` |
| dev: `typescript`, `jest`, `ts-jest`, `@types/*` | lihat package.json | Apache-2.0 / MIT | |

## Test
`npm test` (Jest + ts-jest). Spec: `config`, `processor` (allowlist, kuota, jitter, dedup, sesi, STOP), `encrypted-files`
(AES-GCM, auth state, state harian WIB), `admin-server` (auth, pairing, rate limit), `templates` (STOP, JID, to_hash).
Baileys di-mock lewat antarmuka `WaClient`; `baileys-client.ts` tidak dieksekusi di test.

## Belum terverifikasi (wajib PoC dengan nomor uji sebelum staging)
- Seluruh perilaku Baileys nyata: pairing QR/kode, reconnect, kode putus (401/403/515) → status, `onWhatsApp`,
  `sendMessage`, `requestPairingCode`, bentuk ekspor modul (CJS/ESM) untuk versi yang di-pin.
- Deteksi STOP dari pengguna beralamat **LID** (`@lid`): field nomor alternatif (`senderPn`/`remoteJidAlt`) belum
  dipastikan ada di versi yang di-pin → STOP mungkin tidak terpetakan (metrik `stop_unmapped`).
- Daftar host egress WhatsApp untuk proxy allowlist (ADR-044 §3.6).
- Apakah `npm ci --ignore-scripts` cukup untuk dependensi Baileys (native/opsional).
- Efektivitas batas anti-ban (10/menit, 300/hari, jitter) — tidak ada jaminan; ban bisa terjadi kapan saja.
