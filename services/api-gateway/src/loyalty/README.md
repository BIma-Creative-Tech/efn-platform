# loyalty — ledger poin tunggal

Kontrak: `docs/04` §3.4, §4 (`loyalty.earn.requested` v2), §5, §7.2. ADR: D-05 (satu ledger poin), ADR-020 (Proposed: liabilitas per PT), ADR-022 (enkripsi).
DDL: `infra/postgres/init/02-platform-loyalty.sql`.

- Saldo = `SUM(delta_points)` entri dengan `expires_at IS NULL OR expires_at > now`.
- `member_ref_hash` = member_hash (§1). `member_ref_enc` **wajib** (NOT NULL): AES-256-GCM, AAD `efn:loyalty_ledger.member_ref_enc:v1`.
- `company_code` = PT penanggung liabilitas (CHECK EFN/GL/PB/BCR/BCF/FARM sejak G20 §12.1; kode lama RTL/FNB dihapus, migrasi `09-platform-g20.sql`), default `EFN`.
- `expires_at`: `NULL` (kebijakan kedaluwarsa belum diputuskan).

## Consumer `loyalty.earn.requested` v2 (SEC-08)
1. Payload `schema_version: 2`, `member_hash` (tanpa nomor HP; payload yang membawa `member_ref`/`user_id` → DLQ).
2. Publisher diautentikasi lewat properti AMQP `user_id` (divalidasi RabbitMQ): `price_hunter` hanya dari `efn-ocr`.
3. `points` ≤ `LOYALTY_MAX_POINTS_PER_EVENT` (default 100); `source` allowlist (`price_hunter`); `idempotency_key = price_hunter:<source_ref>`.
4. `price_hunter_report(id = source_ref)` harus `valid` dan `user_hash = member_hash`; `user_enc` didekripsi, hash diverifikasi ulang, lalu dienkripsi ulang ke `member_ref_enc` (AAD berbeda → bukan salin byte).
5. Worker publish sebelum COMMIT → bila report masih `queued/processing` consumer menunggu 200/500/1000 ms, lalu error transien (requeue sekali → DLQ).
Idempotensi: `processed_message('bff.loyalty-earn', key)` + UNIQUE `idempotency_key`, satu transaksi. Pelanggaran → `PermanentMessageError` → DLQ.

## Risiko
R-B05/R-B06 (fraud poin), R-S10, R-T10, R-L01.

## Belum
- Redeem/clawback POS + FIFO kedaluwarsa: SELESAI gelombang 13 (lihat `src/pos/README.md`, primitif `allocateDebit`/`splitCredit`/`lockMember` di sini). Sisa: sumber `mgm` (butuh validasi semantik sendiri); plafon poin per member per hari.
