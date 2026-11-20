# `src/notification/email` — kanal email (G50-B)

**ADR terkait:** ADR-048 (email SMTP generik, **Proposed**), ADR-047 (kampanye & consent), ADR-022 (TLS), ADR-024
(kredensial per service). Kontrak: `docs/04` §28.7 (`notify.email`), §28.9 (env, unsubscribe), §28.10 (flag).
**Status:** dibangun G50 (belum diuji end-to-end; Jest/tsc dijalankan lead di container). `EMAIL_ENABLED=false` default.
Production menunggu Q-SM19 (penyedia SMTP + DPA + SPF/DKIM/DMARC).

## Tujuan
Mengirim email promo kampanye, notifikasi stok tersedia, dan turun harga (dan kelak transaksional) lewat SMTP standar
tanpa terkunci vendor, dengan TLS wajib dan alamat email yang tidak pernah melintasi broker.

## Komponen
| File | Peran |
|---|---|
| `email.module.ts` | `EmailModule` (didaftarkan `AppModule` oleh BFF-A), ekspor `EmailNotifier` |
| `email.notifier.ts` | `EmailNotifier.enqueue(...)` → `'queued' \| 'skipped_disabled' \| 'skipped_no_consent' \| 'skipped_no_email'` |
| `email-sender.consumer.ts` | consumer `notify.email` (SMTP, kuota, List-Unsubscribe) |
| `email-unsubscribe.{controller,service}.ts` | `POST /v1/email/unsubscribe` publik |
| `unsubscribe-token.ts` | token HMAC `eu1.` sekali pakai |
| `smtp-transport.ts` | adapter nodemailer (TLS wajib) |
| `email-templates.ts` | allowlist template teks + HTML (Bahasa Indonesia) |
| `marketing-consent.ts` | baca/cabut consent `marketing` (dipakai juga kanal WA) |
| `channel-env.ts` | pembaca env/secret SEC-29 untuk kanal G50 (AppConfig tidak diubah gelombang ini) |
| `email-retention.job.ts` | purge `email_outbound` 90 hari, jti kedaluwarsa |

## Alur
1. Dispatcher BFF-A memanggil `EmailNotifier.enqueue`. Gerbang: `EMAIL_ENABLED` → member aktif → consent `marketing`
   (kecuali `transactional`; default Q-SM25) → `platform.member_email.verified_at` terisi & `status='active'`.
2. Satu transaksi: `platform.email_outbound` (`ON CONFLICT DO NOTHING`) + baris `platform.outbox` → relay outbox
   publish `notify.email` lewat confirm channel. `idempotency_key = email:<source>:<source_ref>:<member_hash>`.
   Payload **tanpa alamat email** (ADR-048 §3.6).
3. Consumer (`efn-bff`): publisher harus `efn-bff`; kuota `EMAIL_RATE_PER_MIN` global (Redis, menunggu jendela);
   klaim `processed_message` + gerbang diperiksa ulang + dekripsi alamat + kirim dalam satu transaksi.
4. Promo: footer + header `List-Unsubscribe: <https://…/berhenti-langganan#t=eu1…>[, <mailto:…>]`. Token di **fragmen**
   URL (tidak masuk access log); halaman web mem-POST `{ "token": "…" }` ke `POST /v1/email/unsubscribe`.
5. SMTP 5xx pada `RCPT TO` → `member_email.status='invalid'` (berhenti kirim). Galat lain → requeue sekali → DLQ.

## Kontrak API
`POST /v1/email/unsubscribe` (publik, tanpa Bearer, rate limit per IP `EMAIL_UNSUBSCRIBE_RATE_PER_MIN`=10/menit)
- Body: `{ "token": "eu1.<payload>.<sig>" }` (field lain → 400 `invalid_request`, maks 512 karakter).
- `200 { "status": "unsubscribed" }` — consent `marketing` dicabut (baris `granted=false`, source `email_unsubscribe`).
- `400 unsubscribe_token_invalid` — tanda tangan salah, kedaluwarsa, **atau sudah dipakai** (tidak dibedakan).
- `429 rate_limited` + `Retry-After`; `503 unsubscribe_unavailable` bila kunci belum dikonfigurasi.
- Berfungsi walau `EMAIL_ENABLED=false`.

## Konfigurasi
Lihat tabel di `email.config.ts`. Secret: `/run/secrets/email_smtp_password`, `/run/secrets/email_unsubscribe_key`.
`EMAIL_ENABLED=true` dengan TLS/SMTP tidak lengkap → **BFF gagal start** (tanpa mode plaintext, L-160).

## Risiko yang dimitigasi
R-L01 (alamat terenkripsi AES-256-GCM, tidak di payload queue, log `a***@d***.com`), R-S01 (password dari file secret),
R-B17 (consent + berhenti berlangganan sekali klik + kuota), R-B02.

## Belum / keterbatasan
- **Alur mengisi & memverifikasi alamat email member belum dibangun** (tabel `member_email` hanya dibaca). Sampai ada
  (`PUT /v1/me/email` + verifikasi tautan/OTP — butuh kontrak §8), semua enqueue menghasilkan `skipped_no_email`.
- (Selesai G50 review) `member_email.email_enc` terdaftar di `pii-columns.ts` dengan AAD v2 terikat baris
  `efn:member_email.email_enc:v2:<member_id>` (SEC-G50-11; penulis wajib `encryptMemberEmail`, pembaca
  `decryptMemberEmail` menerima v1 lama; `pii-reencrypt` menulis ulang v1 → v2). DSAR export (`channel-privacy.ts`,
  SEC-G50-05) memuat alamat email terdekripsi + status verifikasi, `email_outbound`, `wa_unofficial_outbound`, dan
  `wa_unofficial_recipient` (to_hash + nomor subjek sendiri). Erasure lewat FK CASCADE.
- Belum ada template `transactional` (sumber ditolak sebagai kesalahan pemanggil).
- One-click unsubscribe RFC 8058 (`List-Unsubscribe-Post`) tidak didukung: butuh token di URL + parser form
  (bertentangan dengan R2 "tanpa token di URL"); keputusan diserahkan ke efn-lead/DPO.
- Consent per purpose, bukan per kanal: berhenti berlangganan email mencabut `marketing` untuk semua kanal promo.
- Queue `notify.email` tidak termasuk passive check `AmqpService` (file bersama): consumer hanya dipasang saat
  `EMAIL_ENABLED=true`; queue wajib sudah dideklarasikan broker sebelum flag dinyalakan.
