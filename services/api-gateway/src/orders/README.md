# orders — order online, saga, event Odoo, CS (Fase 3)

Kontrak: `docs/04` §10.3.5–10.3.7, §10.5–10.9, §10.11, §10.16 (L-1..L-7), §10.17 (L-8..L-10). ADR-034 & ADR-019 (**Proposed**),
ADR-016/026 (provider `mock`), ADR-029 (member opak). Migrasi: `infra/postgres/init/07-platform-online.sql`.

## Endpoint
| Endpoint | Auth | Catatan |
|---|---|---|
| `POST /v1/orders` | Bearer | `Idempotency-Key` wajib (opak `^[A-Za-z0-9_-]{16,128}$`, L-8: dibuat route handler Next.js `chk1_…`), body `{quote_id, accept_total}` saja. `UNIQUE(member_id, idempotency_key)` + `request_sha256`: key+body sama → order yang sama (klik ganda), body beda → `409 idempotency_conflict`. Advisory lock member: batas order terbuka (`429 too_many_open_orders`), DSAR hapus aktif → `409 account_deleting`. → `201` |
| `GET /v1/orders`, `GET /v1/orders/:order_id` | Bearer | Dari DB saja (polling tidak memanggil Odoo); daftar memuat `company_code` per bagian; QR hanya saat `awaiting_payment`. |
| `POST /v1/orders/:order_id/cancel` | Bearer | Hanya `created|awaiting_payment`; intent dibatalkan di gateway; webhook menang bila sudah `paid` → `409 order_not_cancellable`. Idempoten. |
| `GET /v1/orders/:order_id/pickup-code` | Bearer | Bagian `confirmed|ready` saja, `no-store`, rate limit sendiri. |
| `POST /v1/orders/:order_id/simulate-payment` | Bearer | Dev/staging + mock saja (404 di production). |
| `POST /v1/pos/online/order-events` | service key + HMAC `odoo_online_event_hmac` | Replay 300 s, `claim` idempotensi, `seq`, transisi monoton; 404/409 final. |

Semua: kill switch `ONLINE_ENABLED`, `Cache-Control: no-store`, body ≤ 16 KB (event ≤ 8 KB), id bukan UUID / milik member lain → `404` (anti-enumeration).

## Saga (§10.6)
- Checkout: order `created` + bagian `placing` + kode ambil AES-256-GCM (key ring `pii_encryption_key`, AAD `efn:online_order_part.pickup_code_enc:v1`) → `efn_online_place` (`online:place:<part_id>`, Odoo menghitung ulang via `quote_hash`) → Σ `amount_payable` = `accept_total` & `expires_at` Odoo ≥ batas + 5 m → satu intent (`online:intent:<order_id>`, porsi per bagian) → `awaiting_payment`. Gagal → `efn_online_cancel` (`absent` = selesai) atau outbox `order.unpaid_closed`; order `failed`.
- Hook webhook (di transaksi PaymentsService): order `paid`, bagian `paying`, outbox `order.paid`; order sudah `expired|cancelled|failed` → `order.compensate(late_payment)` per porsi (tanpa `mark_paid`); nominal beda → status tetap + metrik.
- Consumer (`online-consumers.ts`, publisher wajib `efn-bff`): `order.paid` (mark_paid → confirmed / stock_short → compensate / review), `order.unpaid_closed`, `order.compensate` (cancel Odoo + `online_refund`), `order.refund.requested` (gateway refund → `refund_settled` Odoo; unsupported → `manual_required`; Σ ≤ porsi dicek). Klaim `processed_message` hanya saat efek final; pekerjaan tertinggal diterbitkan ulang rekonsiliasi.
- Job (`online-jobs.service.ts`, advisory lock): kedaluwarsa 30 s, rekonsiliasi 5 m (+ gauge `efn_online_stuck_parts`, `efn_online_refunds_open`, heartbeat `EFN_ONLINE_RECON {"status":"ok|error",…}`), pull `efn_online_status` 15 m (heartbeat juga), retensi 1 j (kode ambil, QR online, tautan member 730 h, event 180 h).
- CLI teraudit (L-3, `src/cli/online-*.ts`, tabel `online_admin_audit` + log `EFN_ONLINE_CS_AUDIT`): `online-refund`, `online-refund-settle`, `online-resolve`.
- Privasi (L-4): `OnlinePrivacyService` — export order DSAR; `DELETE /v1/me` membatalkan order belum bayar lalu `409 open_online_orders` bila ada bagian `paying|confirmed|ready|review`. Erasure: `online_order.member_id` → NULL.

Risiko dimitigasi: R-T10 (saga), R-O05 (refund ≤ porsi per bagian), R-S09 (webhook/event), R-L01 (PII minimal; kode ambil/QR tidak di log). Test: `online-saga.spec.ts` (fake in-memory `testing/online-fakes.ts`), `online-routes.spec.ts` (Fastify nyata), `online-observability.spec.ts`; SQL diverifikasi manual ke PostgreSQL 16 (lihat laporan G17).

## Belum
- Poin online, delivery, F&B online, UI CS, webhook refund vendor (§10.14).
