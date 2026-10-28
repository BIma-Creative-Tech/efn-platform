# otp — inti OTP + adapter WhatsApp

Dipakai bersama oleh `pos` (purpose `pos_promo`, `pos_redeem`), `auth` (`login`), dan `privacy` (`dsar`). Kontrak: `docs/04` §3.1/§3.2/§9.3.
ADR/keputusan: D-11 (WhatsApp Cloud API resmi, **Baileys dilarang**), ADR OTP-offline opsi (a) (Proposed: OTP dinonaktifkan saat POS offline — ditegakkan di Odoo).

## Perilaku
- Kode 6 digit `crypto.randomInt`, disimpan hanya sebagai HMAC-SHA256(kunci HKDF dari `otp_pepper`, `challenge_id:code`) di Redis hash `otp:<challenge_id>`.
- TTL OTP 180 s (`OTP_TTL_SECONDS`); key disimpan +600 s sebagai tombstone agar verify terlambat menjawab `expired`.
- Maks 3 percobaan (`OTP_MAX_ATTEMPTS`); attempt dinaikkan atomik (`HINCRBY`) sebelum perbandingan `timingSafeEqual`; setelah habis → `locked`.
- Sukses = `DEL` key; hanya pemanggil yang `DEL`=1 yang menang (sekali pakai, aman terhadap verify paralel).
- `pos_promo` / `pos_redeem`: `order_ref` verify harus sama dengan saat request (mismatch = attempt gagal). Verify POS menerima kedua purpose (`alsoAccept`) karena body verify tidak memuat purpose; purpose challenge diteruskan ke klaim `discount_token`. Token `pos_redeem` hanya diterima reserve redeem; token `pos_promo` hanya diterima consume promo (docs/04 §9.3).
- Audit `platform.otp_audit` untuk `sent|verified|failed|expired|locked` dengan `subject = member_hash` (bukan nomor HP) dan `ip_enc` (IP terenkripsi AES-256-GCM, AAD `efn:otp_audit.ip_enc:v1`). Retensi 90 hari (`OTP_AUDIT_RETENTION_DAYS`, job `RetentionService`). Kegagalan audit tidak memutus alur.
- SEC-06: budget global pengiriman per jam per purpose (`OTP_GLOBAL_HOURLY_BUDGET` login, `OTP_POS_HOURLY_BUDGET` POS) → 429 + metrik `budget_exhausted`.
- Gagal kirim WA → challenge dihapus, `503 otp_delivery_failed` (tidak membocorkan status keanggotaan karena semua nomor valid diperlakukan sama).
- Metrik: `efn_otp_request_total{purpose,outcome=sent|delivery_failed|rate_limited}`, `efn_otp_verify_total{purpose,outcome=verified|invalid|expired|locked}`, `efn_whatsapp_send_total{outcome}`.

## WhatsApp
- `CloudApiWhatsAppSender`: `POST {WHATSAPP_GRAPH_BASE_URL}/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages`, template `WHATSAPP_OTP_TEMPLATE` (default `efn_otp`, kategori AUTHENTICATION) dengan body param + tombol copy-code (`WHATSAPP_OTP_COPY_CODE_BUTTON`). Timeout `WHATSAPP_TIMEOUT_MS` (4 s, di bawah `efn.pos.otp_timeout_seconds` Odoo = 5 s).
- `ConsoleWhatsAppSender` saat `WHATSAPP_ENABLED=false`: kode OTP dicetak **hanya** bila `NODE_ENV=development`; di lingkungan lain tidak pernah di-log.
- Error tidak memuat token, kode, atau nomor utuh.
- Mode uji dev (docs/04 §3.2 v5.2): `DEV_OTP_REVEAL_MEMBERS` (allowlist nomor) → kode login ikut di respons sebagai `dev_code`, hanya bila `APP_ENV=dev` + `NODE_ENV=development` + `WHATSAPP_ENABLED=false`; diisi di luar itu = gagal start.

## Risiko
R-S04 (brute force/enumerasi), R-B02 (biaya WA — rate limit membatasi), R-L01.

## Belum
- Captcha setelah N request (R-S04) — belum.
- Status pengiriman WA asinkron (webhook status Meta, mis. nomor tanpa WhatsApp) belum diproses.
- Template `efn_otp` harus disetujui Meta; bahasa `id`.

## v3 — Gelombang 5
- Purpose baru `dsar` (re-OTP step-up, docs/04 §8.9) — budget publik (`OTP_GLOBAL_HOURLY_BUDGET`), tidak menyimpan nomor di Redis.
- SEC-24: field `m` (login) = ciphertext AES-256-GCM base64 (AAD `efn:redis_otp.m:v1`). Catatan rotasi kunci: tunggu ≥ 15 menit setelah menaikkan versi aktif sebelum memensiunkan versi lama (challenge hidup ≤ 13 menit).
- SEC-07: field `t` = `amount` POS, dikembalikan saat verify.
- SEC-23: `CloudApiWhatsAppSender` default memakai `createPublicHttp()` (root store publik saja), bukan `fetch` global.