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

## G17-QA-03 (L-14) & review r10 (L-16)
- L-14: `confirmed|ready → review` dari Odoo selalu diterapkan; selama ada bagian `review` → `GET …/pickup-code` `409 order_in_review` dan status order pelanggan `review` (tampilan saja; status DB tetap diturunkan dari bagian). Event yang tidak diterapkan dicatat `ignored_stale`.
- SEC-95: refund inisiatif Odoo (event `cancelled|refunded`, `initiator=odoo`) → `approval_required` (tanpa gateway); CS menyetujui lewat `node dist/cli/online-refund-approve.js --refund=<id> --reason=…`.
- SEC-96: stok hanya untuk produk katalog kanal (selain itu 404 tanpa Odoo), cache negatif `none` 30 s; semaphore global json2 `ODOO_RPC_MAX_CONCURRENCY` (default 4) → `busy` → 503.
- SEC-97: klaim refund atomik `gateway_in_flight` (basi 15 menit) + refund in-flight dihitung dalam porsi; melebihi → `failed` + `failure_reason=refund_exceeds_portion` (tanpa `Math.min`).
- SEC-98: aktor CLI = `EFN_CS_OPERATOR`/`SUDO_USER`/user OS + host (`--actor` dihapus); cek porsi sebelum nota kredit Odoo; `online-refund-settle` menolak `refund_exceeds_portion`.
- SEC-99: kode ambil dihapus saat `stock_short`/final; retensi `confirmed|ready|review` memakai `COALESCE(ready_at, confirmed_at)`.
- SEC-100: `DELETE /v1/me` mengambil kunci member checkout + cek ulang order terbuka dalam transaksi INSERT; erasure ditunda (+ `EFN_ONLINE_ANOMALY erasure_deferred_open_orders`) bila masih ada order terbuka.
- SEC-102: AAD kode ambil `efn:online_order_part.pickup_code_enc:v2:<part_id>`; baris AAD v1 tetap terbaca lalu ditulis ulang (baca kode ambil & `pii-reencrypt`).
- L-19: peran CS (`EFN_CS_ROLE` cs|approver) — semua refund CS `approval_required` (ambang `ONLINE_CS_REFUND_APPROVAL_MIN_IDR`), approve/resolve-cancel hanya approver ≠ pembuat; `claim_token` CAS untuk penulisan pasca-gateway (SEC-106); pool json2 publik/saga terpisah (SEC-109); alasan CLI dari stdin (tanpa `--reason=`, atau `--reason-stdin`).

## G18-QA-01 — kunci idempotensi event Odoo stabil lintas restore DB Odoo
- Dedup event `POST /v1/orders/events` memakai kunci BFF `odoo-ev:<platform_order_ref>:<event>:<seq>[:<event_id>]` (bukan `online:<id lokal Odoo>:…` yang terpakai ulang setelah DB Odoo dibuat ulang/dipulihkan). Kunci Odoo asli disimpan di `online_order_event.source_key` (audit).
- Kunci sama + sidik isi sama (`payload_sha256`, tanpa `occurred_at`/`event_id`) → `duplicate`. Kunci sama + isi berbeda → `409 event_conflict` + anomali `odoo_event_conflict` (critical) — tidak diabaikan diam-diam.
- Kompatibel mundur: baris lama yang tercatat dengan kunci Odoo lama → `duplicate` hanya bila milik bagian/event/seq yang sama; milik bagian lain (id lokal terpakai ulang) → diproses + anomali `odoo_event_key_reused` (warning).
- Body: `idempotency_key` menerima `online:<id lokal>|<uuid bagian>:<event>:<seq>`; `event_id` (uuid) opsional.
- Sisa (DR): setelah restore, `seq` Odoo bisa mundur di bawah `last_seq` BFF → event tercatat `ignored_stale` (tidak diam: ada baris + metrik); status dipulihkan oleh rekonsiliasi pull `efn_online_status` (15 menit).

## G19 (docs/04 §11.1–11.2, W-02/W-09)
- **Reservasi 1 jam**: `ONLINE_PAYMENT_TIMEOUT_SECONDS` default **3600** (batas config 60..3600 tetap) = lama SO tercadang di Odoo (`stock_hold_mode=confirm`). Pengaman §10.3.5 no. 5 tetap: `expires_at` SO < batas order + 5 menit → batal + `503 online_unavailable` + anomali `odoo_expiry_too_short`. Dengan Odoo `payment_timeout_minutes` 70 → selisih ±10 menit (lolos); Odoo masih 25 → semua checkout ditolak. **Urutan aktivasi: Odoo 70 dulu, baru BFF 3600.**
- **Anti stock griefing**: `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` default **2** (L-32). `400 captcha_required` tetap dicadangkan (belum aktif).
- **Promo saat place**: `efn_online_place.order.promo_codes` = `promo_applied` grup dari quote tersimpan (bukan dari klien), `lines` = baris produk saja (`placeLines`); promo berakhir/kuota habis di antara quote & place → Odoo `price_changed` → `409 price_changed` + kompensasi. Baris reward (`reward: true`) disimpan di `online_order_part.lines` dan tampil di `GET /v1/orders/:id`; tidak dihitung sebagai baris refund CS.
- Kupon sekali pakai dikonsumsi Odoo saat place dan dikembalikan saat batal/kedaluwarsa (cron Odoo) — BFF tidak menyimpan status kupon.
