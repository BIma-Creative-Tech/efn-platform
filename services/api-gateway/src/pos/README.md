# pos — gerbang OTP promo POS (server Odoo → BFF)

Kontrak: `docs/04` §3.1 & §3.4. Alur: `docs/02` §3.1. ADR: OTP-offline opsi (a) (Proposed), D-05 (satu ledger poin).
Semua endpoint dilindungi `ServiceKeyGuard` (`X-EFN-Service-Key` = secret `odoo_bff_api_key`). Dipanggil server Odoo, bukan browser POS.

| Endpoint | Sukses | Gagal |
|---|---|---|
| `POST /v1/pos/otp/request` | `202 { challenge_id, expires_in: 180 }` | `400 invalid_request`, `401`, `429 rate_limited` (+`Retry-After`), `503 otp_delivery_failed` |
| `POST /v1/pos/otp/verify` | `200 { ok:true, discount_token, expires_in: 600 }` | `400 { ok:false, reason: invalid\|expired\|locked, attempts_left }` |
| `POST /v1/pos/discount-token/consume` `{ discount_token, order_ref, member_ref }` | `200 { ok:true }` | `400 invalid_phone`, `409 { ok:false, reason: already_used\|order_mismatch\|member_mismatch\|expired\|invalid }` |
| `GET /v1/pos/loyalty/balance?member_ref=` | `200 { points_balance }` (nomor tak dikenal → 0) | `400 invalid_phone`, `401` |

**v2 (SEC-06/SEC-07):** `member_ref` di request & consume diambil server Odoo dari nomor HP `partner_id` order. Hanya `^62[0-9]{8,13}$` (lainnya `400 invalid_phone`). Consume membandingkan `member_hash(member_ref)` dengan klaim `member_hash` token (`timingSafeEqual`) **sebelum** cek marker `used` → `409 member_mismatch`, token tidak terbakar. Budget OTP POS per jam terpisah dari login publik (`OTP_POS_HOURLY_BUDGET`). Server BFF kini HTTPS (`https://api-gateway:3000`, Odoo memverifikasi dengan `EFN_CA_FILE`).

- `order_ref`: string opaque ≤ 64 karakter ASCII tercetak (Odoo 19 mengirim `pos.order.uuid`); formatnya tidak di-parse.
- Rate limit: per member (hash) 3 / 15 menit; per `company_code:pos_config_id` 20 / 15 menit (pemanggil selalu server Odoo sehingga IP tidak bermakna).
- `discount_token`: JWT HS256 (`discount_token_secret`), klaim `{ jti, purpose:"pos_promo", order_ref, member_hash, company_code, iat, exp }`.
- Consume: urutan cek tanda tangan/exp → purpose → marker `dtok:used:<jti>` → `order_ref` → `SET NX`.
  - **Idempotent** untuk `jti`+`order_ref` yang sama (retry Odoo) → 200; marker berisi `order_ref`.
  - Token sudah dipakai untuk order lain → `already_used`. Token belum dipakai tetapi `order_ref` beda dengan klaim → `order_mismatch` (token tidak terbakar).
  - Retry setelah token kedaluwarsa menjawab `expired` (exp diverifikasi lebih dulu).

## Risiko
R-T02 (OTP vs offline — fitur dimatikan saat offline di Odoo), R-S04, R-B06/R-B03 (promo disalahgunakan → token sekali pakai terikat order).

## Belum
- `amount` divalidasi tetapi belum dipakai (mis. batas nilai promo) — menunggu aturan bisnis.
- Redeem/debit poin dari POS belum ada.

## v3 — Gelombang 5 (docs/04 §8.8, SEC-07 sisa)
- `discount_token` kini membawa klaim `amount` (nilai `amount` saat `POST /v1/pos/otp/request`, disimpan di challenge field `t`).
- `POST /v1/pos/discount-token/consume` body **`{ discount_token, order_ref, member_ref, amount_total }`** (`amount_total` WAJIB, Rupiah). Ditolak `409 { ok:false, reason:"amount_mismatch" }` bila `amount_total < 50% × amount` atau `> amount × (1 + POS_OTP_AMOUNT_TOLERANCE_PCT/100)` (default 20 → maks 120%). Batas inklusif. Urutan cek: tanda tangan/exp → purpose → member → **amount** → replay → order. `amount_mismatch` tidak membakar token (Odoo menandai order `review`).
- Alasan: OTP disetujui pelanggan untuk order bernilai X; token tidak boleh dipindah ke order yang jauh lebih besar (barang ditumpuk setelah OTP) atau jauh lebih kecil. Toleransi atas menampung tambahan item kecil.
- Token tanpa klaim `amount` (diterbitkan sebelum v3) → `invalid`.
- Payment QRIS (§8.3) ada di modul `payments`; pull erasure Odoo (§8.4) di modul `privacy` — keduanya di bawah `/v1/pos/*` dengan `ServiceKeyGuard`.