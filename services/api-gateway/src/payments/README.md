# payments — QRIS gateway-agnostic (POS & online)

Kontrak: `docs/04` §8.3, §8.8 (refund dicadangkan). ADR-016 (provider, **Proposed**), ADR-019 (merchant of record — settlement per PT belum).
PCI: tidak ada data kartu (SAQ A). Build/test **belum dijalankan lokal**.

## Endpoint
| Endpoint | Sukses | Gagal |
|---|---|---|
| `POST /v1/pos/payments` (service key) `{ order_ref, amount, currency:"IDR", company_code, pos_config_id, method:"qris", idempotency_key }` | `201 { payment_id, provider, status:"pending", qr_string, expires_at }` (idempotent; Odoo: `pos:<order_uuid>:<payment_uuid>`) | `400`, `409 idempotency_conflict` (key sama, payload beda), `409 payment_in_progress`, `502 payment_provider_error`, `503 payment_provider_unavailable` |
| `GET /v1/pos/payments/:payment_id` | `200 { payment_id, status, paid_at, provider_ref, amount }` | `404` |
| `POST /v1/pos/payments/:payment_id/cancel` | `200 { status }` (status saat ini bila sudah final) | `404`, `502` |
| `POST /v1/webhooks/payments/:provider` (publik) | `200 { ok:true }` (juga untuk duplikat & provider_ref tak dikenal) | `401 invalid_signature`, `400`, `404` (provider ≠ aktif) |
| `POST /v1/pos/payments/:payment_id/simulate` `{ status }` | **dev/staging saja** (mock, bukan production) → `200` status | `404` di production / provider lain |

## Adapter (`PAYMENT_PROVIDER`)
| Nilai | Status | Webhook |
|---|---|---|
| `mock` (default non-production; **production gagal start**) | QR dummy `EFN-MOCK-QRIS|…|BUKAN-QR-SUNGGUHAN`; `getStatus` → `expired` setelah `expires_at` | `X-EFN-Signature: t=,v1=` HMAC `webhook_payment_secret` atas `"<t>." + rawBody`, replay 300 s |
| `xendit` | **KERANGKA, BELUM DIUJI SANDBOX**; wajib `PAYMENT_PROVIDER_EXPERIMENTAL=true` (config + konstruktor). QR Codes API `api-version 2022-07-31`, Basic auth `payment_provider_api_key` | `x-callback-token` = `webhook_payment_secret` (tanpa timestamp → replay ditangani idempotensi + transisi monoton). Cancel tidak didukung → `cancelled` lokal |
| `midtrans` | **KERANGKA, BELUM DIUJI SANDBOX**; wajib flag yang sama. Core API `/v2/charge` QRIS, status/cancel per `order_id` (= payment_id) | `signature_key = SHA512(order_id+status_code+gross_amount+server_key)` |
| `disabled` (default production) | semua endpoint `503 payment_provider_unavailable` | `404` |

Egress vendor memakai `createPublicHttp()` (SEC-23: root store publik saja).

**G20 L-55 (gerbang deploy pilot bayar di kasir):** `ONLINE_PAYMENT_METHODS` (`qris`, `counter`; default `qris,counter` di dev/test, `qris` selain itu). Production + `ONLINE_ENABLED=true` + `qris` di daftar → wajib `PAYMENT_PROVIDER=xendit|midtrans`, selain itu **gagal start** (ConfigError tanpa nilai rahasia). Pilot tanpa vendor = `ONLINE_PAYMENT_METHODS=counter` + `PAYMENT_PROVIDER=disabled`; `mock` tetap ditolak di production. Order `counter` tidak pernah membuat intent / memanggil provider (refund-nya lewat POS, `manual_required`). Jurnal titipan Holding (A1) hanya untuk `qris`.

## Aturan
- Status final hanya lewat webhook terverifikasi atau `getStatus` provider (maks 1× per `PAYMENT_STATUS_REFRESH_SECONDS` per intent, Redis `rl:paychk:<id>`), tidak pernah dari klien.
- Webhook: verifikasi → `processed_message (webhook.payments.<provider>, event_id)` → `SELECT … FOR UPDATE` → transisi monoton dalam satu transaksi: `pending → paid|expired|failed|cancelled`; `expired|cancelled|failed → paid` (pembayaran terlambat: dicatat + log peringatan untuk refund manual); `paid` final. Nominal webhook ≠ intent → tidak diubah, log error.
- Env: `PAYMENT_PROVIDER`, `PAYMENT_PROVIDER_EXPERIMENTAL`, `PAYMENT_PROVIDER_API_KEY(_FILE)` (hanya xendit/midtrans), `PAYMENT_PROVIDER_BASE_URL` (https, mis. sandbox), `PAYMENT_QR_TTL_SECONDS` (600), `PAYMENT_TIMEOUT_MS` (8000), `PAYMENT_STATUS_REFRESH_SECONDS` (10), `MIDTRANS_QRIS_ACQUIRER` (`gopay`).

## Belum
Refund (`/refund` dicadangkan), settlement/rekonsiliasi per PT, checkout online (Super-App), metrik payment, uji sandbox vendor (format waktu `settlement_time` Midtrans, event Xendit terbaru, idempotency header Xendit).

## Fase 3 — online (docs/04 §10.7, G17)
- `payment_intent.purpose` (`pos` default | `online`) + `online_order_id`; tabel `payment_intent_portion` (porsi per bagian, Σ = amount). `createOnlineIntent` (port `OnlinePaymentsPort`, `online-payments.ts`): `company_code=EFN`, idempoten `online:intent:<order_id>`.
- `PaymentGateway.refund(intent, {refundId, amount, reason})`: `mock` sinkron, idempoten per `refundId`, Σ ≤ amount; `xendit`/`midtrans` → `PaymentOperationUnsupportedError` (BFF → `manual_required`).
- Hook saga online dipanggil DI DALAM transaksi transisi intent (`setOnlineHook`, didaftarkan `orders`), termasuk nominal beda (status tidak berubah). Perilaku POS tidak berubah.
