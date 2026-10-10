# auth — login pelanggan Super-App (OTP WhatsApp)

Kontrak: `docs/04` §2 & §3.2. Dipanggil Next.js server-side (token disimpan di cookie HttpOnly `efn_session` oleh frontend).

| Endpoint | Sukses | Gagal |
|---|---|---|
| `POST /v1/auth/otp/request` `{ phone }` | `202 { challenge_id, expires_in: 180 }` | `400`, `429`, `503 otp_delivery_failed` |
| `POST /v1/auth/otp/verify` `{ challenge_id, code, age_confirmed: true }` | `200 { access_token, token_type:"Bearer", expires_in: 3600 }` | `400 { ok:false, reason, attempts_left }`, `400 age_confirmation_required` (G18 W-19/Q-P3: `age_confirmed` tidak `true`; dicek sebelum OTP sehingga challenge tidak hangus; pesan menyebut `MEMBER_MIN_AGE` = 17), `400 invalid_request` (`age_confirmed` bukan boolean), `429` |
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

- **ALTCHA** (`altcha.service.ts`): implementasi manual `node:crypto` (bukan paket `altcha-lib` — versi/API tidak dapat diverifikasi tanpa npm; logika kecil). Format ALTCHA v1: `challenge = SHA-256(salt + number)`, `signature = HMAC-SHA256(altcha_hmac_key, …)` (lihat SEC-34 di bawah), `salt = hex12 + "?expires=<unix>&"` (diakhiri `&` → anti salt-splicing). Verifikasi: format salt ketat, expires (TTL 300 s), HMAC waktu-konstan, number 0..maxnumber, hash cocok, lalu **sekali pakai** `SET altcha:used:<signature> NX`. Captcha diverifikasi sebelum rate limit; nomor tidak valid ditolak sebelum captcha dikonsumsi. Endpoint POS tidak memakai captcha.
- **Member opak**: verify → `MemberService.upsertOnLogin` (`platform.member`, `member_ref_enc` AAD `efn:member.member_ref_enc:v1`) → JWT `sub` = UUID. `JwtAuthGuard` memeriksa member aktif (`deleted_at IS NULL`) di DB tiap request dan mengisi `req.user = { memberId, memberHash }`. Token lama ber-`sub` nomor HP otomatis ditolak (pengguna login ulang). Login tetap diizinkan selama masa tenggang hapus akun (§8.9).
- **SEC-24**: nama key Redis memakai `ipk = HMAC(redis_key pepper HKDF "efn:redis_key:v1", ip)` (32 hex), bukan IP mentah; nomor HP di challenge login disimpan terenkripsi (`m`, AAD `efn:redis_otp.m:v1`).
- Prefix Redis baru: `altcha:used:*` (ACL efn-bff harus mencakupnya). Rate limit tetap di `rl:*`.
- **SEC-34 (Gelombang 12) — pengikatan IP**: `signature = HMAC-SHA256(altcha_hmac_key, "efn-altcha:v2\n" + challenge + "\n" + ip_binding)`; `ip_binding` = IPv4 utuh / prefix IPv6 /64 / `unknown`. IP tidak dikirim ke klien dan tidak disimpan. Solusi dari IP lain → `400 captcha_required` (sama dengan kegagalan lain, tanpa oracle). Format JSON challenge/payload tidak berubah (widget `altcha` tidak memverifikasi signature). Saat deploy, challenge v1 yang sedang beredar (≤ 5 menit) ditolak sekali → pengguna cukup mengulang verifikasi.
- **SEC-34 — biaya adaptif**: `maxnumber = ALTCHA_MAX_NUMBER × 2^level`, dipotong `ALTCHA_MAX_NUMBER_CAP`. Level 0..3 = maksimum dari (a) rasio per IP = max(challenge per IP / `ALTCHA_CHALLENGE_RL_LIMIT`, OTP login per IP / `OTP_RL_SOURCE_LIMIT`) dengan ambang 25/50/75 %, (b) rasio global = OTP login jam ini / `OTP_GLOBAL_HOURLY_BUDGET` dengan ambang 50/75/90 %. Sumber = counter Redis rate limit yang sudah ada (`RateLimiter.peek`, tanpa key baru, tanpa IP mentah). Metrik `efn_altcha_challenge_total{level}`. `ALTCHA_ADAPTIVE=false` mematikan.
- **SEC-54 (Gelombang 13c) — level berbasis rasio GAGAL per IP**: komponen ketiga (c) = max(ALTCHA gagal / (ALTCHA gagal + OTP lolos captcha), verify OTP gagal / verify OTP) per IP per `OTP_RL_WINDOW_SECONDS`, ambang 30/50/75 %, hanya bila percobaan ≥ `ALTCHA_FAIL_MIN_SAMPLE` (default 5). Counter baru `rl:altcha:fail:ip:<ipk>` dan `rl:otp:login:verify-fail-ip:<ipk>` (IP di-HMAC, SEC-24); best effort (Redis gagal tidak mengubah respons). Tujuan: IP CGNAT/kantor ramai dengan pengguna sah (volume tinggi, rasio gagal rendah) tidak dihukum oleh rasio, sedangkan solver/penebak kode dari IP yang sama cepat naik ke level 3. Sisa: kuota per IP untuk CGNAT tetap memakai `OTP_RL_SOURCE_LIMIT` (belum ada kuota khusus prefix CGNAT).
- **Dampak UX (untuk manajemen & product)**: kondisi normal tidak berubah (level 0 = 500 000 → rata-rata ±1–2,5 s ponsel kelas bawah). Level 1 (×2) ±2–5 s, level 2 (×4) ±4–10 s, level 3 (×8 = cap default 4 000 000) ±8–20 s di ponsel kelas bawah, < 4 s desktop. Siapa yang terkena: (1) satu IP yang sudah memakai ≥ 25 % kuota OTP per 15 menit — termasuk pengguna di belakang **CGNAT operator seluler** atau Wi-Fi publik/kantor yang berbagi IP (≥ 5 permintaan OTP dari IP yang sama dalam 15 menit); (2) semua pengguna saat budget OTP global jam itu ≥ 50 % (indikasi serangan pumping). Kembali normal otomatis di window berikutnya. Pengguna yang berpindah jaringan (Wi-Fi ↔ seluler) di antara verifikasi dan kirim akan mendapat `captcha_required` dan cukup mengulang verifikasi (widget di-reset frontend). Rekomendasi: tampilkan teks "Memverifikasi perangkat…" + progres widget saat level > 0 (frontend, belum).
- Env: `ALTCHA_HMAC_KEY(_FILE)` (wajib), `ALTCHA_MAX_NUMBER` (500000 = biaya dasar), `ALTCHA_MAX_NUMBER_CAP` (default max(4 000 000, base), ≤ 10 000 000), `ALTCHA_ADAPTIVE` (default `true`), `ALTCHA_TTL_SECONDS` (300), `ALTCHA_CHALLENGE_RL_LIMIT` (120).
- Risiko dimitigasi: R-S04 (OTP pumping), SEC-06 sisa (bot tanpa proof-of-work), SEC-24 (PII di Redis/AOF). Sisa: proof-of-work memperlambat, bukan mencegah, penyerang ber-CPU besar → budget global & plafon per nomor tetap berlaku.

## G18 — W-19 (Q-P3): usia minimum member 17 tahun
- Login = daftar, sehingga pernyataan usia diminta di **setiap** verify login Super-App (frontend: checkbox "Saya berusia minimal 17 tahun", tidak dicentang default; kirim `age_confirmed: true`). Tidak ada tanggal lahir yang dikumpulkan (minimisasi data).
- `platform.member.age_confirmed_at` (migrasi `08-platform-g18-privacy.sql`) diisi saat member dibuat; member lama hasil migrasi/POS dilengkapi sekali saat login Super-App pertama (`WHERE age_confirmed_at IS NULL`), tidak pernah ditimpa. Ikut terhapus bersama baris member saat erasure.
- `MEMBER_MIN_AGE` (default 17, rentang 17..21) hanya untuk teks pesan.
- Belum: pendaftaran member oleh kasir POS (W-25, Odoo) — pernyataan usia ditangani SOP/UI kasir (efn-odoo-functional).
