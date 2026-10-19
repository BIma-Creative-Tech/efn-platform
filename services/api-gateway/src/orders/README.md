# orders — order online, saga, event Odoo, CS (Fase 3)

Kontrak: `docs/04` §10.3.5–10.3.7, §10.5–10.9, §10.11, §10.16 (L-1..L-7), §10.17 (L-8..L-10). ADR-034 & ADR-019 (**Proposed**),
ADR-016/026 (provider `mock`), ADR-029 (member opak). Migrasi: `infra/postgres/init/07-platform-online.sql`.

## Endpoint
| Endpoint | Auth | Catatan |
|---|---|---|
| `POST /v1/orders` | Bearer | `Idempotency-Key` wajib (opak `^[A-Za-z0-9_-]{16,128}$`, L-8: dibuat route handler Next.js `chk1_…`), body `{quote_id, accept_total}` saja. `UNIQUE(member_id, idempotency_key)` + `request_sha256`: key+body sama → order yang sama (klik ganda), body beda → `409 idempotency_conflict`. Advisory lock member: batas order terbuka (`429 too_many_open_orders`), DSAR hapus aktif → `409 account_deleting`. → `201` |
| `GET /v1/orders`, `GET /v1/orders/:order_id` | Bearer | Dari DB saja (polling tidak memanggil Odoo); daftar memuat `company_code` per bagian; QR hanya saat `awaiting_payment`. |
| `POST /v1/orders/:order_id/cancel` | Bearer | Hanya `created|awaiting_payment|awaiting_counter_payment` (G20); intent dibatalkan di gateway; webhook menang bila sudah `paid` → `409 order_not_cancellable`. Idempoten. |
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

## G20 — bayar di kasir (docs/04 §12.2, W-30/E-45, L-50/L-52/L-55)
ADR terkait: ADR-001 (revisi 6 entitas), ADR-016 (vendor payment, **belum dipilih** → pilot `counter`), ADR-019/034 (Proposed). Migrasi: `infra/postgres/init/09-platform-g20.sql`.

**Kontrak klien**
- `POST /v1/orders` body `{ quote_id, accept_total, payment_method? }`, `payment_method` = `"qris"` (default) | `"counter"`; nilai lain → `400 invalid_request`.
  - Metode di luar `ONLINE_PAYMENT_METHODS` (L-55) atau di luar `payment_modes` kanal → `422 payment_method_unavailable`.
  - `counter` dengan > 1 bagian → `422 counter_payment_single_part_only`.
  - Order `counter` terbuka ≥ `ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER` (default 1) → `429 too_many_open_orders` (batas umum `ONLINE_MAX_OPEN_ORDERS_PER_MEMBER` tetap menghitung order counter).
  - Key sama dengan metode berbeda → `409 idempotency_conflict` (`payment_method` masuk sidik body hanya bila `counter`).
- Respons `201` (dan `GET /v1/orders/:id` selama `awaiting_counter_payment`, hanya member pemilik):
  ```json
  { "order_id": "…", "status": "awaiting_counter_payment", "amount_payable": 159800, "currency": "IDR",
    "created_at": "…", "expires_at": "…", "paid_at": null,
    "payment": { "method": "counter", "counter_code": "ABCD-EFGH", "expires_at": "…" },
    "parts": [{ "channel_code": "GL", "company_code": "GL", "status": "placed", "…": "…" }] }
  ```
  Status lain → `payment: { "method": "counter", "expires_at": "…" }` (tanpa `counter_code`). QRIS tidak berubah (`{ method: "qris", status, qr_string?, expires_at }`).
- `GET /v1/orders` item bertambah `payment_method`; status `awaiting_counter_payment` tampil apa adanya.
- `POST /v1/orders/:id/cancel` berlaku untuk `awaiting_counter_payment` (batal tanpa refund; SO dibatalkan lewat `order.unpaid_closed`).
- `POST /v1/orders/:id/simulate-payment` `{status:"paid"}` untuk order counter: hanya dengan stub `ODOO_ONLINE_MOCK` (meniru sync POS); Odoo sungguhan → 404.

**Alur**
- Checkout counter (L-56): BFF membuat kode bayar (`crypto.randomInt`, alfabet §10.2, 8 karakter, selalu ≠ kode ambil) dan menyimpannya di `online_order_part.counter_code_enc` (AES-256-GCM, AAD `efn:online_order_part.counter_code_enc:v2:<part_id>`, ikut `pii-reencrypt` / cek key ring) **di transaksi checkout yang sama dengan kode ambil, sebelum place** → `efn_online_place(order.payment_mode="counter", order.counter_code)` (Odoo menyimpan hash, tidak mengembalikan kode; field `counter_code` di respons diabaikan) → order `awaiting_counter_payment`. Kode tidak pernah di-log. **Tidak ada payment intent; provider tidak pernah dipanggil** (aman dengan `PAYMENT_PROVIDER=disabled`).
- Event Odoo `paid` + `payment: { method: "counter" }` (atau `confirmed`/`stock_short`) → order `paid` (paid_at) → bagian `confirmed` (`stock_short` → batal + refund; `review` → anomali) → order `fulfilling`; kode bayar dihapus, kode ambil tampil. `paid` tanpa method counter pada order counter / method counter pada order QRIS → `409 payment_method_mismatch` + anomali critical.
- Event hilang: job kedaluwarsa menanyakan `efn_online_status` dulu (lunas → diterapkan; Odoo tak terjangkau → coba ulang ≤ 10 menit lewat batas, lalu kedaluwarsa); pull status 15 menit juga memeriksa bagian `placed` order counter.
- Kedaluwarsa tanpa bayar = alur batal yang ada (expired + `order.unpaid_closed`), tanpa refund. Bayar terlambat di kasir pada order yang sudah ditutup BFF → bagian `review` + anomali `late_counter_payment` (ops: refund POS E-31 / CLI resolve).
- Refund setelah lunas di kasir: alur CS + approver yang ada; `order.refund.requested` → `manual_required` + `failure_reason=counter_refund_via_pos` (tanpa gateway) → CS me-refund order POS pelunasan ke metode asal → event Odoo `refund_settled` (L-58) atau `online-refund-settle --provider-ref <ref refund POS>` → `settled` (tanpa memanggil `efn_online_refund_settled` Odoo, L-57). Refund otomatis (stock_short) sama.
- **L-57**: event Odoo `review` + `review_reason` `counter_paid_after_cancel|counter_paid_after_expiry` pada order counter yang sudah `cancelled|expired|failed` → bagian `cancelled → review` (pengecualian monoton khusus ini, seperti L-14) + anomali critical `late_counter_payment`; order DB tetap ditutup, pelanggan melihat `status: "review"` (kode ambil `409 order_in_review`); **tanpa refund otomatis** (CS memutuskan). `review` tanpa alasan / alasan lain / order QRIS → tetap `ignored_stale`.
- **L-60 (SEC-145)**: refund counter (CS, Odoo, maupun sistem `stock_short`) **selalu** `approval_required` (tanpa ambang). Setelah approver → `odoo_done` → consumer: `manual_required` + `failure_reason=counter_refund_auth_pending` → `efn_online_counter_refund_authorize` (`online:counter_refund_auth:<refund_id>`, `refund_ref` = `odoo_refund_ref`/null, nominal persis). Sukses → `counter_refund_via_pos` (kasir boleh me-refund POS). Transien → tetap pending + anomali `counter_refund_auth_failed`, rekonsiliasi (≥ 15 menit) menerbitkan ulang. Ditolak Odoo → `counter_refund_auth_rejected` + anomali critical, tidak dicoba ulang (CS memutuskan). `refund_settled` / CLI settle hanya untuk refund berstatus `counter_refund_via_pos`; selain itu `unapproved` (anomali **critical**) / CLI ditolak.
- **L-57**: BFF tidak pernah memanggil `efn_online_mark_paid` maupun `efn_online_refund_settled` untuk order counter (Odoo `invalid_state`). Refund counter = `efn_online_refund` (CS) → kasir me-refund order POS pelunasan → selesai HANYA bila (a) Odoo mengirim event **`refund_settled`** `{ refund: { refund_ref, amount, settled_at, provider_refund_ref: "pos:<uuid>" } }` (L-58; HMAC + seq seperti event lain) yang cocok refund_ref + order counter + nominal persis + ≤ porsi → `settled` + notifikasi `online_order_refunded`; idempoten (replay → `duplicate`, refund sudah settled → `ignored_stale`), atau (b) CS `online-refund-settle` (dicatat BFF saja). Event `refunded`/`cancelled` = refund DIMINTA, tidak pernah menandai settled. `refund_settled` untuk refund QRIS / nominal beda / ref tak dikenal / masih approval_required → `ignored_stale` + anomali `refund_settled_rejected` (critical untuk QRIS & nominal beda).
- Retensi: kode bayar di-NULL-kan saat bagian keluar dari `placed` / order tidak lagi `awaiting_counter_payment` (metrik `efn_online_retention_rows_total{kind="counter_code_nulled"}`).

**Risiko**: R-T10 (saga tanpa intent; kedaluwarsa vs pelunasan POS — jendela balapan dibatasi pengecekan status sebelum kedaluwarsa), R-O05 (refund ≤ porsi = `amount_payable` bagian), R-L01 (kode bayar rahasia: tidak di log/event/URL, terenkripsi at rest), stock griefing (batas 1 order counter terbuka per member).
**Test**: `online-counter.spec.ts`, `online.model.spec.ts`, `online-routes.spec.ts` (parser body).
**Belum**: lookup kode di POS & sync pelunasan = Odoo (`efn_online_pos_counter`); rate limit lookup per sesi di Odoo; kanal counter-saja tidak bisa memakai QRIS sampai vendor (ADR-016).

## G21 — faktur atas nama pembeli (docs/04 §13.5, W-06/E-39, L-63, K-36-7)
ADR terkait: ADR-036 §0 (enkripsi PII Odoo, Accepted). File: `invoice-request.ts` (validasi), `orders.service.ts` (`applyInvoiceParty`), path gateway ke-12 `efn.online.order/efn_online_set_invoice_party` (`odoo-rpc/`).

**Kontrak klien**: `POST /v1/orders` body opsional `invoice_request: { buyer_type: "npwp"|"nik"|"passport", tax_id, name, address, email }` — tepat lima field. Validasi: NPWP/NIK 16 digit (titik/strip/spasi dibuang), paspor 5–20 `[A-Z0-9]` (ASUMSI, = frontend), nama 2–100, alamat 5–255 (tanpa karakter kontrol), email dasar ≤ 254. Salah → `400 invalid_request` (pesan tanpa nilai).
- Ada bagian non-PPN (PB/BCF F&B PB1, atau PT lain; hanya GL & BCR yang PPN) → `422 invoice_not_available` **sebelum** order dibuat.
- Respons checkout pertama bertambah `invoice_request: { status: "applied" | "pending_cs" }` (tidak disimpan; replay/`GET` tidak memuatnya).
- Sidik idempotensi: hanya penanda `invoice_request: true` (isi profil tidak di-hash). Key sama dengan/ tanpa faktur → `409 idempotency_conflict`.

**Alur**: setelah SEMUA bagian ter-place dan cek total/expires_at lolos, SEBELUM payment intent: `efn_online_set_invoice_party(channel_code, platform_order_ref=<part_id>, profile, idempotency_key="online:invoice:<part_id>")` per bagian.
- `UserError("efn_online:invoice_not_available")` (efn_pii tidak aktif, L-63) → order dikompensasi (cancel + `failed`, tanpa intent) → `422 invoice_not_available` diteruskan.
- Gagal lain (transport/circuit/auth/respons) → anomali `EFN_ONLINE_ANOMALY invoice_party_failed` (order/part/channel/kode saja) + lanjut (partner generik; status `pending_cs`, faktur atas nama dibuat CS). Tidak di-retry otomatis (profil tidak disimpan) — sengaja.
- Hasil RPC: hanya `state` opsional dibaca; field lain diabaikan.

**PII**: profil hanya di memori request; tidak masuk DB (order/part/event), outbox, Redis, log, metrik; klien json2 tidak me-log body. Mock Odoo mencatat SHA-256 profil saja (test).
**Test**: `invoice-request.spec.ts`.
**Belum**: retry pemasangan partner (butuh profil → sengaja tidak; CS); faktur untuk bagian PPN dalam keranjang campuran (kontrak ditafsirkan ketat: semua bagian harus PPN, sama dengan frontend).
