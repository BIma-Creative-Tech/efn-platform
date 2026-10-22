# fnb — QR meja F&B dari ponsel (G22, W-07/E-27)

Kontrak: `docs/04` §14.A (A.2–A.11), §14.7 **L-70** (satu kiriman = satu `pos.order` draft baru; banyak order per sesi),
**L-71** (default konfirmasi pelayan), **L-76** (batas 10 kiriman/sesi, metrik alert, path `/v1/pos/fnb/*`),
**L-80** (earn per order dari `session_closed.close.orders[]`). ADR: **ADR-032 Accepted opsi B**, ADR-023 (Odoo tidak ke
internet), ADR-020 (ledger poin tunggal), ADR-022 (enkripsi/least privilege), ADR-028 (ALTCHA).
Risiko dimitigasi: **R-S03** (token meja dinamis), R-B01/R-B05 (fraud poin), R-L01 (PDP), R-T03.

## Alur
1. Pelayan membuka meja di POS → Odoo `POST /v1/pos/fnb/table-sessions` (mTLS + `X-EFN-Service-Key`) →
   BFF membuat sesi + token `fts1` → `201 { table_session_id, qr_url, expires_at }` (`qr_url = https://<tenant>/meja#t=<token>`).
2. Tamu memindai QR → Super-App membaca fragment, menghapusnya dari address bar, mengirim header **`X-Table-Session`**.
3. `GET /v1/fnb/session|menu`, `POST /v1/fnb/orders` (Idempotency-Key) → json2 `efn_qr_submit` (user teknis per PT) →
   `202`. Odoo membuat order draft BARU di meja (L-70) dan memberi tahu kasir; pelayan menekan *Order* (L-71).
4. Event Odoo → `POST /v1/pos/fnb/events` (HMAC `odoo_fnb_event_hmac`, replay 300 s, seq) — status kiriman, order dibayar,
   sesi ditutup. Sesi diklaim member → earn `pos_earn:<order_uuid>` di transaksi event (L-80, D-22-5).

## Kontrak API (bentuk respons persis)
| Route | Respons sukses |
|---|---|
| `GET /v1/fnb/session` | `{ outlet_name, table_label, company_code, brand_code, expires_at, confirm_mode, limits:{max_lines, max_line_qty, submissions_left}, points:{claimable, claimed, claimed_by_me?} }` |
| `GET /v1/fnb/menu` (`Cache-Control: private, max-age=60`) | `{ categories:[{category_id,name,sequence,image_sha:null}], products:[{product_id,name,category_id,image_sha,price_excl,tax_labels,price_note,available,attributes}], menu_synced_at }` |
| `POST /v1/fnb/orders` → **202** | `{ submission_id, status, lines:[{product_id,name,qty,attribute_value_ids,attributes_text}], indicative_subtotal_excl }` |
| `GET /v1/fnb/orders` | `{ items:[{submission_id, created_at, status, lines:[{name,qty,attribute_value_ids,attributes_text}]}] }` |
| `GET /v1/fnb/orders/:id` | `{ submission_id, status, updated_at }` (404 bila bukan milik sesi) |
| `POST /v1/fnb/session/points-claim` | `{ claimed: true }` |
| `DELETE /v1/fnb/session/points-claim` | `204` |
| `POST /v1/pos/fnb/table-sessions` → **201** | `{ table_session_id, qr_url, expires_at }` |
| `POST /v1/pos/fnb/events` | `{ ok: true, status: applied\|duplicate\|ignored_stale\|ignored_unknown_event }` |

Error: lihat `docs/04` §14.A.3; token salah/kedaluwarsa/tak dikenal → `401 table_session_invalid` (generik), sesi tidak
`open` → `409 table_session_closed`, kill switch / Odoo mati → `503 ordering_unavailable` (tidak diantrekan).

## Keamanan
- Token `fts1.<id>.<exp>.<HMAC>`; K = HKDF(`fnb_table_token_key`, `efn:fnb_table_token:v1`); rotasi lewat
  `fnb_table_token_key_prev`. Tanda tangan diperiksa SEBELUM DB; token tidak disimpan/di-log; tidak pernah dari path/query.
- Rate limit `FNB_RL_*` (Redis, IP di-HMAC); body ≤ 16 KB; DTO `additionalProperties:false` (klien tidak bisa mengirim
  harga/meja/config/member); catatan disaring (SEC-168, lihat di bawah) dan TIDAK disimpan (hanya `has_note`).
- json2 QR memakai semaphore publik bersama (L-20) + circuit breaker per PT; respons Odoo divalidasi skema.
- Idempotensi kiriman `UNIQUE(session, Idempotency-Key)` + `request_sha256`; kunci Odoo `fnb:submit:<submission_id>`;
  transport gagal → rekonsiliasi `efn_qr_status` (tidak ada pesanan ganda di dapur).

## Data & retensi (PDP)
`fnb_table_session`, `fnb_submission`, `fnb_menu_item`, `fnb_menu_sync` (migrasi `11-platform-g22.sql`). PII: hanya
`member_id` sesi (dilepas 30 hari setelah tutup, NULL saat erasure). Baris 180 hari. Ekspor DSAR: `qr_table_sessions`.

## Metrik/log
`efn_fnb_submissions_total{status}`, `efn_fnb_odoo_requests_total{method,outcome}`, gauge `efn_fnb_sessions_open`,
`efn_fnb_sessions_open_overdue` (sesi open > 4 jam), `efn_fnb_submissions_stuck` (submitting > 5 menit),
`efn_fnb_qr_earn_pending` (sesi diklaim kedaluwarsa tanpa event bayar > 15 menit). Log `EFN_FNB_ANOMALY {kind,…}`,
`EFN_FNB_JOB {"status":…}`, `EFN_FNB_SESSION {table_session_id,config_id,print_seq}` (tanpa token).

## Perbaikan security review r15 (SEC-164/165/167/168/173, QA G22 P3)
- **K1** `order_uuid` (`pos.order.uuid`) dari hasil `efn_qr_submit`, tiap baris `efn_qr_status`, dan event
  `submission_status` divalidasi **UUID** (null diterima; bentuk lain = respons tidak valid / `400`). Event mengisi
  `fnb_submission.order_uuid` yang masih kosong; uuid berbeda → anomali `order_uuid_mismatch`, nilai pertama tetap.
- **K2/SEC-165** `close.orders[]` = `{order_uuid, eligible_amount (NETO refund), company_code, pos_member_attached}`.
  `pos_member_attached:true` → `earn_status = pos_member` (POS meng-earn, BFF tidak). Field **absen** → tidak earn +
  `EFN_FNB_ANOMALY {"kind":"odoo_config_error","code":"pos_member_attached_missing"}` critical (fail-closed).
- **K3/SEC-164** `fnb_table_session.close_event_at` diisi di transaksi yang sama dengan klaim event + earn. Reverse
  `earn_channel:"qr"` (`pos-loyalty.service.ts` `resolveQrMember`) untuk order yang dikenal lewat K1, belum `paid|cancelled`,
  dan close sesinya belum diproses → `409 earn_pending` (Odoo retry, tanpa tulis). Setelah earn → clawback biasa; close
  diproses tetapi order tidak di `orders[]` (neto ≤ 0) → `200` nol. Sesi yang di-reissue ditandai saat close penggantinya.
  Jaminan durabilitas: `200` event baru dikirim setelah transaksi (klaim + earn ledger + penanda) COMMIT.
- **SEC-167** nomor cetak `fnb_open:…:<n>` hingga 99 999 999.
- **SEC-168** filter catatan: NFKC → hapus karakter format (zero-width, bidi, BOM) → kontrol → spasi → ≤ 140 code point →
  tolak `@`, ≥ 8 digit Nd di mana pun, atau deret ASCII lama. Aturan lengkap di `fnb.model.ts` `sanitizeNote` (harus
  identik dengan Odoo `qr_common.py`). Upaya terbaik (angka ditulis huruf tetap lolos).
- **SEC-173** `FNB_RL_EVENTS_LIMIT/_WINDOW_SECONDS` (default 1200/60 s) per `config_id`, SETELAH kedua guard; 429 = retry
  outbox Odoo (klaim belum terpakai). Anomali `events_rate_limited`.
- **QA P3** `409 item_unavailable` membawa `product_ids` (∩ kiriman): dari Odoo `efn_qr:item_unavailable:<id>,<id>` bila
  dikirim (usulan kontrak), cadangan overlay menu. `close.orders[]` ≤ 100, body event ≤ 16 KB (Odoo harus menyamakan).

## Belum
- SEC-169 (klaim "siapa cepat"), SEC-166 (sesi menyerap order tamu berikutnya) — sisi Odoo / keputusan lead.
- Order pelayan yang tidak pernah dikenal BFF (tanpa `fnb_submission`) tetap dijawab `200` nol bila reverse datang sebelum
  close; ditutup oleh jaminan Odoo (reverse QR hanya setelah close dijawab 2xx).
- Klaim poin `first_submit` ALTCHA dihitung per sesi (BFF tidak mengenal perangkat tamu).
- Bayar di ponsel (tahap 2), combo QR (D-22-9). Semua test belum dijalankan di mesin penulis.
