# pos — gerbang OTP promo POS (server Odoo → BFF)

Kontrak: `docs/04` §3.1 & §3.4. Alur: `docs/02` §3.1. ADR: OTP-offline opsi (a) (Proposed), D-05 (satu ledger poin).
Semua endpoint dilindungi `ServiceKeyGuard` (`X-EFN-Service-Key` = secret `odoo_bff_api_key`). Dipanggil server Odoo, bukan browser POS.

| Endpoint | Sukses | Gagal |
|---|---|---|
| `POST /v1/pos/otp/request` | `202 { challenge_id, expires_in: 180 }` | `400 invalid_request`, `401`, `429 rate_limited` (+`Retry-After`), `503 otp_delivery_failed` |
| `POST /v1/pos/otp/verify` | `200 { ok:true, discount_token, expires_in: 600 }` | `400 { ok:false, reason: invalid\|expired\|locked, attempts_left }` |
| `POST /v1/pos/discount-token/consume` `{ discount_token, order_ref, member_ref }` | `200 { ok:true }` | `400 invalid_phone`, `409 { ok:false, reason: already_used\|order_mismatch\|member_mismatch\|expired\|invalid }` |
| `POST /v1/pos/loyalty/balance` `{ member_ref }` (v3.6 R2 — GET dengan query **dihapus**: nomor HP tidak boleh di URL/access log) | `200 { points_balance }` (nomor tak dikenal → 0) | `400 invalid_phone`, `401`, `404` untuk GET lama |

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
## v4.0/v4.1 — Gelombang 13: Loyalty di POS (docs/04 §9, §9.6)
ADR: ADR-017 (redeem wajib OTP, nonaktif offline), ADR-020 (ledger tunggal, liabilitas per PT), ADR-022. Risiko: R-B05/R-B06 (fraud poin), R-O05 (clawback), R-L01 (UU PDP).
DDL: `infra/postgres/init/05-platform-loyalty-pos.sql` (`loyalty_reservation`, `loyalty_pos_earn`, indeks ledger `(source, source_ref)`).

| Endpoint (service key) | Body | Respons | Error 409 (`{ error: { code } }`) |
|---|---|---|---|
| `GET /v1/pos/loyalty/params` | — | parameter §9.1 + `reservation_commit_window_seconds` | — |
| `POST /v1/pos/otp/request` | + `purpose: pos_promo\|pos_redeem` (default promo) | sama | — |
| `POST /v1/pos/loyalty/earn` | `{ idempotency_key: "pos_earn:<uuid>", order_ref, member_ref, company_code, eligible_amount, pos_config_id? }` (pos_config_id: 13c, opsional) | `{ points_earned, points_balance }` | `idempotency_conflict`, `earn_limit_exceeded` |
| `POST /v1/pos/loyalty/redeem/reserve` | `{ discount_token, order_ref, member_ref, points, amount_total }` | `{ reservation_id, points, value_idr, expires_at, hold_until }` | `insufficient_points`, `below_minimum`, `exceeds_max_pct`, `token_invalid`, `member_mismatch`, `amount_mismatch`, `reservation_exists` |
| `POST /v1/pos/loyalty/redeem/commit` | `{ reservation_id, order_ref, member_ref, value_idr, idempotency_key: "pos_redeem:<uuid>" }` | `{ points_redeemed, points_balance }` | `order_mismatch`, `member_mismatch`, `value_mismatch`, `reservation_released`, `reservation_expired`, `idempotency_conflict`; 404 `reservation_not_found` |
| `POST /v1/pos/loyalty/redeem/release` | `{ reservation_id }` | `{ ok: true }` (idempoten) | `already_committed`; 404 `reservation_not_found` |
| `POST /v1/pos/loyalty/reverse` | `{ idempotency_key: "pos_reverse:<uuid>", original_order_ref, refund_order_ref, member_ref, refunded_eligible_amount, refund_ratio (0,1] }` | `{ points_clawed_back, points_restored, points_balance }` | `earn_pending` (retry), `member_mismatch`, `idempotency_conflict` |
| `POST /v1/pos/loyalty/balance` | `{ member_ref, pos_config_id?, company_code? }` (K-8) | `{ points_balance }` | 429 per member / per pos_config |

Aturan inti:
- **Earn**: `floor(eligible / LOYALTY_EARN_IDR_PER_POINT)`; eligible dihitung Odoo (K-1, kategori dikecualikan dikelola Odoo — K-2). Kedaluwarsa 00:00 WIB setelah `LOYALTY_POINT_EXPIRY_DAYS` hari (dibulatkan per hari). Setiap earn (juga 0 poin) dicatat di `loyalty_pos_earn` → dasar `earn_pending` (K-6).
- **FIFO kedaluwarsa**: saldo = SUM entri belum kedaluwarsa; debit dialokasikan ke "bucket" `expires_at` terdekat (entri debit menyalin `expires_at` bucket) sehingga SUM tetap benar setelah poin kedaluwarsa. Utang (saldo negatif) = bucket `NULL`; kredit berikutnya melunasi utang dulu.
- **Reserve**: serialisasi per member `pg_advisory_xact_lock(EFNM, hash32)` → reservasi paralel melebihi saldo: satu `insufficient_points` (diuji juga terhadap PostgreSQL nyata, 10 paralel). Token `pos_redeem` sekali pakai (UNIQUE `token_jti`, K-4); retry identik → reservasi sama. Satu order = satu reservasi aktif.
- **Hold & commit (K-3)**: `expires_at` (15 menit) hanya UI; poin ditahan & commit diterima sampai `hold_until` (default 24 jam, `LOYALTY_RESERVATION_COMMIT_WINDOW_SECONDS`). Commit setelah 15 menit → metrik `outcome="late"`. Lewat 24 jam → `reservation_expired`; job `LoyaltyReservationSweeper` (advisory lock `EFNL`, interval `LOYALTY_RESERVATION_SWEEP_INTERVAL_MS`) menandai `expired`, menghapus baris akhir > `LOYALTY_RESERVATION_RETENTION_DAYS` (30) dan `loyalty_pos_earn` > `LOYALTY_POS_EARN_RETENTION_DAYS` (400).
- **Reverse**: restore redeem `floor(R×ratio)` dulu, lalu clawback earn `ceil(E×ratio)` (pembulatan memihak PT, kumulatif ≤ total asal; ratio 1 = sisa penuh). Kekurangan → utang. Restore ke bucket asal; bucket kedaluwarsa → masa berlaku baru.
- Ledger: `source ∈ {pos_earn, pos_redeem, pos_reverse}`, `company_code` PT order, `member_ref_enc` AES-256-GCM. Tabel baru TIDAK memuat nomor HP (hanya `member_hash`).
- Rate limit per member per operasi (`LOYALTY_RL_MEMBER_LIMIT`/`LOYALTY_RL_WINDOW_SECONDS`), balance per member & per pos_config. Metrik: `efn_loyalty_pos_ops_total{op,outcome}`, `efn_loyalty_pos_points_total{kind,company}`, `efn_loyalty_reservation_expired_total`.

### Gelombang 13c — SEC-61: pagar earn & rekonsiliasi (defense in depth, BFF percaya `eligible_amount` dari Odoo)
DDL: `infra/postgres/init/06-platform-loyalty-guard.sql` (indeks `loyalty_pos_earn (member_hash, created_at)`). Semua batas = PLACEHOLDER menunggu Finance; 0 = mati.

| Pagar | Env (default) | Mekanisme |
|---|---|---|
| Plafon per order | `LOYALTY_POS_MAX_EARN_POINTS` (5 000, dulu 100 000) | dicek setelah cek replay |
| Per member per hari WIB | `LOYALTY_EARN_DAILY_MAX_POINTS` (2 000) | Σ `loyalty_pos_earn` hari ini di bawah kunci member (tepat walau paralel) |
| Per PT per jam | `LOYALTY_EARN_COMPANY_HOURLY_MAX_POINTS` (50 000) | counter Redis berbobot `rl:loy:earnpts:co:<PT>:<jam>`; ditolak → dikompensasi |
| Per kasir per jam | `LOYALTY_EARN_POS_CONFIG_HOURLY_MAX_POINTS` (10 000) | hanya bila body earn membawa `pos_config_id` (opsional, non-breaking — Odoo belum mengirim) |

- Pelanggaran → `409 earn_limit_exceeded` (kode lama, final di outbox Odoo → order `review`), TANPA catatan earn (reverse order itu kelak `earn_pending` → review), baris log `EFN_LOYALTY_ANOMALY {json}` dan `efn_loyalty_anomaly_total{kind,severity}`. Retry earn yang dulu diterima tetap `200 replay` walau batas diturunkan.
- **Job rekonsiliasi** `LoyaltyReconService` (`loyalty-recon.service.ts`, advisory lock `EFNC`, transaksi READ ONLY, `LOYALTY_RECON_INTERVAL_MS` = 1 jam, jendela `LOYALTY_RECON_LOOKBACK_HOURS` = 26): `earn_ledger_mismatch`, `earn_above_rule` (points > floor(eligible/tarif saat ini)), `earn_above_order_cap`, `earn_ledger_orphan` (ledger `pos_earn` tanpa catatan earn, kecuali ter-erasure), `member_day_above_limit`, `reverse_exceeds_earn`, `restore_exceeds_redeem`, `ledger_unknown_source` (source di luar `pos_*`, `migration_opening`, `dsar`, `price_hunter` → admin/SQL manual). Heartbeat tiap siklus: `EFN_LOYALTY_RECON {"status":"ok",...}` + gauge `efn_loyalty_recon_last_success_timestamp_seconds`; error → `EFN_LOYALTY_RECON {"status":"error"}` + `efn_loyalty_recon_runs_total{outcome="error"}`.
- Format log (tanpa nomor HP/member_hash/IP; nilai string disaring `[A-Za-z0-9_.:-]`): `EFN_LOYALTY_ANOMALY {"kind":"earn_limit_member_day","severity":"warning","company":"FNB","order_ref":"<uuid>","points":120,"earned_today":1950,"limit":2000}`. Daftar `kind` & severity tetap: `src/loyalty/loyalty-anomaly.ts`.
- Batas: job ini TIDAK melihat Odoo — earn untuk `order_ref` yang tidak pernah ada di `pos.order` hanya terdeteksi oleh rekonsiliasi ETL (SEC-61 rek. 3, data-ai). `earn_above_rule` memakai tarif saat ini (positif palsu bila tarif dinaikkan dalam jendela). Pagar per PT/jam dapat dihabiskan pemegang service key (DoS earn sah selama ≤ 1 jam) — itulah sebabnya severity `critical`.

Belum: kebijakan Finance atas utang poin & restore poin kedaluwarsa (placeholder), alert Prometheus untuk `outcome="late"`/`earn_pending` berulang (devsecops), skenario smoke E2E.

## G18 — kontrak §9.9 v6: redeem poin = alat bayar (K-18-2..K-18-5, K-18-8)
- **K-18-2** `POST /v1/pos/loyalty/redeem/reserve`: `amount_total` = total order **termasuk pajak**, **integer** Rupiah (desimal → `400 invalid_request`); `value_idr` = `points × LOYALTY_POINT_VALUE_IDR` (integer).
- **K-18-3** `commit` wajib `payment_amount` (integer) = nilai baris pembayaran *Poin EFN*. Urutan cek: `value_idr` ≠ reservasi → `409 value_mismatch`; `payment_amount` ≠ `value_idr` reservasi → `409 payment_mismatch` + `EFN_LOYALTY_ANOMALY commit_payment_mismatch` (critical); reservasi tetap `reserved` (Odoo hold + review).
- **K-18-4** `LOYALTY_EARN_EXCLUDE_POINT_PAYMENT` (default `true`, keputusan final Finance): diumumkan ke Odoo lewat `GET /v1/pos/loyalty/params` → `earn_exclude_point_payment`. Odoo menghitung `eligible_amount = Σ eligible tanpa pajak × (1 − bayar_poin ÷ amount_total)`; BFF memagari order ber-redeem ter-commit: `eligible_amount` > `floor(amount_total × (1 + POS_OTP_AMOUNT_TOLERANCE_PCT/100)) − value_idr` → `409 earn_limit_exceeded` + anomali `earn_exceeds_money_portion` (pagar kasar, bukan pengganti rumus Odoo). `eligible_amount`/`refunded_eligible_amount` dibulatkan ke bawah ke Rupiah.
- **K-18-5** `reverse` menerima `points_restored_value` (integer ≥ 0, opsional): poin redeem dikembalikan = `floor(nilai ÷ LOYALTY_POINT_VALUE_IDR)` (memihak PT; sisa Rupiah di log `EFN_LOYALTY_RESTORE {order_ref,value_idr,points,remainder_idr}`), dibatasi sisa redeem order asal (melebihi → dipotong + `restore_exceeds_redeem` critical). Clawback earn tetap `refund_ratio`. Tanpa field (event v4) → restore `floor(R × refund_ratio)` seperti sebelumnya.
- **K-18-6** nilai yang dilaporkan disimpan di `platform.loyalty_pos_reverse` (`refund_order_ref` PK, `original_order_ref`, `company_code`, `points_restored_value` bigint, `points_restored`, `idempotency_key` UNIQUE, `created_at`) di transaksi yang sama dengan restore; append-only (efn_bff SELECT/INSERT), `efn_readonly` SELECT kolom non-PII; retensi 10 th (tidak di-purge BFF). Retry dengan nilai berbeda → `409 idempotency_conflict`. Migrasi: `infra/postgres/init/08-platform-g18-privacy.sql`.
- **K-18-8** `POST /v1/pos/otp/request` `purpose: "pos_enroll"` wajib `age_confirmed: true` → selain itu `400 age_confirmation_required` (sebelum rate limit/OTP terkirim); `age_confirmed` non-boolean → `400 invalid_request`. `POST /v1/pos/otp/verify` untuk challenge `pos_enroll` → `200 { ok: true, enrolled: true }` (TANPA `discount_token`), upsert `platform.member` dengan `age_confirmed_at`, `age_confirmed_source='pos'`, `age_confirmed_pos_config_id` (pos.config dari request); akun sedang dihapus → `409 account_deleting`. Budget/rate limit = kanal POS. Odoo tidak menyimpan usia.
