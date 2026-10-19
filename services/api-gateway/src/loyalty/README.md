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

## G21 — kebijakan poin per PT & lot FIFO berpenerbit (docs/04 §13.1, W-29/E-44, L-61/L-62)
ADR: ADR-020 revisi (liabilitas per PT penerbit), ADR-001 §poin. Status ADR-020 masih **Proposed** — pilihan dapat dibalik: kebijakan bisa dimatikan dengan tidak mempublikasikannya (fallback env), kolom penerbit nullable.
DDL: `infra/postgres/init/10-platform-g21.sql`.

- **Lot = (expires_at, PT penerbit)**. `loyalty_ledger.issuer_company_code` dicatat di SETIAP entri baru: earn = PT order; redeem/clawback/forfeit = penerbit lot yang dikonsumsi FIFO (`allocateDebit`, seri expiry → lot terbit lebih dulu); restore = penerbit lot asal. `company_code` = PT tempat transaksi. Baris pra-G21 (NULL) dibaca `COALESCE(issuer_company_code, company_code)`.
- Pelunasan utang (`splitCredit`) dicatat atas nama penerbit lot berutang (utang tidak tersisa abadi di satu PT); sisanya lot baru penerbit kredit.
- Kedaluwarsa tidak menulis entri: lot hilang dari SUM → breakage per penerbit = SUM entri lot itu (akrual `expired_value_idr`).
- Erasure DSAR (`erasure.service.ts`): forfeit **per lot** (penerbit, expires_at) dengan expires_at sama sehingga kontribusi subjek ke liabilitas tiap penerbit = 0 sekarang dan setelah lot hangus (forfeit NULL lama membuat liabilitas negatif saat lot hangus).
- **Kebijakan** (`loyalty-policy.ts` murni, `loyalty-policy.service.ts`): tabel append-only `platform.loyalty_policy`; aktif = versi tertinggi dengan `GREATEST(effective_from, received_at) ≤ t`; `point_value_idr` hanya dari kebijakan global EFN (L-62); PT tanpa kebijakan / kebijakan di luar plafon saat ini → env lama + warning (1×/10 menit/PT). Cache `LOYALTY_POLICY_CACHE_SECONDS` (default 30).
- Plafon keras `LOYALTY_POLICY_CEILING_*` (default = env lama; `app-config.ts` `loyaltyPolicyCeilings`) — env fallback di luar plafon = gagal start.

Kontrak publikasi: lihat `src/pos/README.md` (§G21).

Risiko dimitigasi: R-B05 (rekonsiliasi poin per PT), R-B06 (fraud: plafon keras + tanda tangan + service key), R-S14 (empat mata di Odoo; BFF menolak isi beda versi sama).

## Belum
- G21: settlement antar-PT (W-15) dan jurnal breakage (W-17, G22) di Odoo; snapshot saldo harian (query saldo penutup memindai seluruh riwayat penerbit — cukup untuk volume pilot, perlu tabel snapshot bila ledger > puluhan juta baris).
- Redeem/clawback POS + FIFO kedaluwarsa: SELESAI gelombang 13 (lihat `src/pos/README.md`, primitif `allocateDebit`/`splitCredit`/`lockMember` di sini). Sisa: sumber `mgm` (butuh validasi semantik sendiri); plafon poin per member per hari.
