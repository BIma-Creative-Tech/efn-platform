# auth — login pelanggan Super-App (OTP WhatsApp)

Kontrak: `docs/04` §2 & §3.2. Dipanggil Next.js server-side (token disimpan di cookie HttpOnly `efn_session` oleh frontend).

| Endpoint | Sukses | Gagal |
|---|---|---|
| `POST /v1/auth/otp/request` `{ phone }` | `202 { challenge_id, expires_in: 180 }` | `400`, `429`, `503 otp_delivery_failed` |
| `POST /v1/auth/otp/verify` `{ challenge_id, code }` | `200 { access_token, token_type:"Bearer", expires_in: 3600 }` | `400 { ok:false, reason, attempts_left }`, `429` |
| `GET /v1/me` (Bearer) | `200 { member_ref_masked, points_balance }` | `401 unauthorized` |

- Nomor dinormalisasi ke E.164 tanpa `+` (`08xx` → `628xx`). **Hanya nomor Indonesia** `^62[0-9]{8,13}$`; selain itu `400 invalid_phone` (SEC-06). Nomor valid selalu 202 (login = daftar).
- Rate limit & mitigasi lockout tertarget (SEC-06):
  - per IP klien 20 / 15 menit; verify per IP 60 / 15 menit;
  - per **(nomor, IP)** 3 / 15 menit — penyerang dari IP lain tidak bisa menghabiskan kuota korban;
  - per nomor lintas IP 10 / jam (`OTP_RL_MEMBER_GLOBAL_LIMIT`) — anti spam WA ke korban;
  - budget global login `OTP_GLOBAL_HOURLY_BUDGET`/jam (default 1000) → 429 + metrik `outcome="budget_exhausted"`;
  - verifikasi **tidak pernah dikunci per nomor**: lock hanya per challenge (3 percobaan), challenge_id acak hanya diketahui peminta.
  IP klien = `req.ip` dengan `TRUSTED_PROXIES` (subnet docker + rentang Cloudflare dari DevSecOps, SEC-03) → XFF palsu dari internet diabaikan (lihat `common/net/client-ip.spec.ts`).
  **Syarat:** Next.js harus meneruskan `X-Forwarded-For` klien saat memanggil BFF; bila tidak, semua pelanggan terlihat sebagai IP container Next.js.
- Access token: JWT HS256 `jwt_secret`, klaim `sub` = member_ref, `typ:"access"`, `iss:"efn-bff"`, `aud:"efn-superapp"`, exp 1 jam.

## Gap (sengaja tidak dibuat — di luar kontrak)
- **Tidak ada refresh token** — pelanggan login ulang setiap 1 jam.
- **Tidak ada logout/revoke** — token tetap valid sampai exp walau cookie dihapus. Mitigasi sementara: exp pendek. Usulan: `jti` + denylist Redis, atau session id server-side.
- `sub` berisi nomor HP plaintext (token ditandatangani, tidak dienkripsi). Diterima karena milik pemegang token dan tersimpan di cookie HttpOnly; alternatif: `sub` = id opaque + tabel member.

## Risiko
R-S04, R-B06 (1 nomor = 1 akun via OTP), R-L01.

## v3 — Gelombang 5 (docs/04 §8.1, §8.2, §8.9; SEC-06, SEC-24)
| Endpoint | Sukses | Gagal |
|---|---|---|
| `GET /v1/auth/challenge` | `200 { algorithm:"SHA-256", challenge, salt, signature, maxnumber }` (`Cache-Control: no-store`) | `429` (per IP-hash, `ALTCHA_CHALLENGE_RL_LIMIT`/15 menit) |
| `POST /v1/auth/otp/request` `{ phone, altcha }` | `202` seperti di atas | `400 captcha_required` (altcha hilang/invalid/kedaluwarsa/dipakai ulang — tidak dibedakan), `400 invalid_phone`, `429`, `503` |
| `POST /v1/auth/otp/verify` | `200` token dengan **`sub` = `member.id` (UUID)** | `400`, `409 account_deleting` (akun sedang `processing` dihapus — hanya terlihat oleh pemilik nomor yang lolos OTP) |

- **ALTCHA** (`altcha.service.ts`): implementasi manual `node:crypto` (bukan paket `altcha-lib` — versi/API tidak dapat diverifikasi tanpa npm; logika kecil). Format ALTCHA v1: `challenge = SHA-256(salt + number)`, `signature = HMAC-SHA256(altcha_hmac_key, challenge)`, `salt = hex12 + "?expires=<unix>&"` (diakhiri `&` → anti salt-splicing). Verifikasi: format salt ketat, expires (TTL 300 s), HMAC waktu-konstan, number 0..maxnumber, hash cocok, lalu **sekali pakai** `SET altcha:used:<signature> NX`. Captcha diverifikasi sebelum rate limit; nomor tidak valid ditolak sebelum captcha dikonsumsi. Endpoint POS tidak memakai captcha.
- **Member opak**: verify → `MemberService.upsertOnLogin` (`platform.member`, `member_ref_enc` AAD `efn:member.member_ref_enc:v1`) → JWT `sub` = UUID. `JwtAuthGuard` memeriksa member aktif (`deleted_at IS NULL`) di DB tiap request dan mengisi `req.user = { memberId, memberHash }`. Token lama ber-`sub` nomor HP otomatis ditolak (pengguna login ulang). Login tetap diizinkan selama masa tenggang hapus akun (§8.9).
- **SEC-24**: nama key Redis memakai `ipk = HMAC(redis_key pepper HKDF "efn:redis_key:v1", ip)` (32 hex), bukan IP mentah; nomor HP di challenge login disimpan terenkripsi (`m`, AAD `efn:redis_otp.m:v1`).
- Prefix Redis baru: `altcha:used:*` (ACL efn-bff harus mencakupnya). Rate limit tetap di `rl:*`.
- Env: `ALTCHA_HMAC_KEY(_FILE)` (wajib), `ALTCHA_MAX_NUMBER` (100000), `ALTCHA_TTL_SECONDS` (300), `ALTCHA_CHALLENGE_RL_LIMIT` (120).
- Risiko dimitigasi: R-S04 (OTP pumping), SEC-06 sisa (bot tanpa proof-of-work), SEC-24 (PII di Redis/AOF). Sisa: proof-of-work memperlambat, bukan mencegah, penyerang ber-CPU besar → budget global & plafon per nomor tetap berlaku.