# loyalty — ledger poin tunggal

Kontrak: `docs/04` §3.4, §4 (`loyalty.earn.requested` v2), §5, §7.2. ADR: D-05 (satu ledger poin), ADR-020 (Proposed: liabilitas per PT), ADR-022 (enkripsi).
DDL: `infra/postgres/init/02-platform-loyalty.sql`.

- Saldo = `SUM(delta_points)` entri dengan `expires_at IS NULL OR expires_at > now`.
- `member_ref_hash` = member_hash (§1). `member_ref_enc` **wajib** (NOT NULL): AES-256-GCM, AAD `efn:loyalty_ledger.member_ref_enc:v1`.
- `company_code` = PT penanggung liabilitas (CHECK EFN/GL/PB/BCR/BCF/FARM sejak G20 §12.1, + `WTZ` sejak G24 §16.1 (migrasi `13-platform-g24.sql`); kode lama RTL/FNB dihapus, migrasi `09-platform-g20.sql`), default `EFN`.
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

## G22 — aktivitas, poin hangus tidak aktif (W-17), poin online (W-12)
Kontrak `docs/04` §14.C–§14.D, L-72 (D-22-12/13/15/17/18), **L-73** (job & anonimisasi OFF di production), **L-75**
(parameter hangus di kebijakan global EFN), **L-76** (WA tanpa jumlah poin, bukti tanpa identitas), **L-77**, **L-79**.
ADR-020, ADR-022. Risiko: R-B05, R-B06, R-L01, R-L09.
- `loyalty-activity.ts` `touchActivity(q, hash, kind, at)` — `GREATEST` di transaksi peristiwa: earn/redeem POS, impor
  migrasi, Price Hunter valid, login OTP, klaim QR, earn/redeem online. Bukan aktivitas: clawback/restore/forfeit/expire.
- `loyalty-inactivity.job.ts` (`LoyaltyInactivityJob`, logger `LoyaltyInactivityJob`): notice ≥ jam
  `LOYALTY_EXPIRY_NOTICE_HOUR_WIB` (notice + outbox `notify.push` `source: loyalty_expiry`, params `{expire_date}`),
  expire ≥ `LOYALTY_EXPIRY_RUN_HOUR_WIB` (advisory lock per fase; batal bila kandidat > `LOYALTY_EXPIRY_MAX_PER_RUN`;
  dry-run default; entri `inactivity_expire` per lot, `source='loyalty_expiry'`, kunci acak; lot utang tidak disentuh;
  blocker: reservasi aktif / pesanan online terbuka / DSAR aktif). Anonimisasi (`LOYALTY_INACTIVE_ANONYMIZE`, default
  false) → `dsar_request(type='retention')` → `ErasureService`. Heartbeat `EFN_LOYALTY_EXPIRY_RUN {"status","phase",…}`;
  metrik `efn_loyalty_expiry_total{phase,outcome}` (seri diinisialisasi 0), `efn_loyalty_expired_points_total{issuer}`,
  `efn_loyalty_expiry_notice_failed_total`.
- **SEC-171 (r15)** `LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT` (default `true` sampai Legal memutuskan dasar hukum §14.D.4;
  nilai selain true/false → gagal start): WA hanya bila consent `marketing` terbaru member = `granted` (`platform.consent`,
  sumber yang sama dengan `ConsentService`). Tanpa consent (termasuk subjek hanya-ledger POS) → notice TETAP dibuat (due ≥
  N hari, info in-app `GET /v1/me` D.6), bukti anonim `loyalty_expiry_notice_proof.channel='in_app'`, metrik outcome
  `no_consent`, heartbeat `no_consent`/`require_consent`. Consent dicek saat enqueue; penarikan consent di antara enqueue dan
  kirim (menit) belum dicek ulang di consumer.
- Kebijakan global EFN (L-75): `params` boleh memuat `inactivity_expiry_months` (plafon
  `LOYALTY_POLICY_CEILING_INACTIVITY_MONTHS_MIN/_MAX`, 6..36), `expiry_notice_days` (7..60), `activity_epoch`
  (YYYY-MM-DD, ≤ 1 tahun ke depan) — PT lain mengirim kunci ini → 400; di luar plafon → 422. Sidik kanonik memuat kunci
  ini hanya bila ada (sidik G21 tidak berubah). `LoyaltyPolicyService.expiryParams()` = nilai kebijakan per kunci, fallback env.
- `LOYALTY_POINT_EXPIRY_DAYS` DIHAPUS (production: gagal start; dev: peringatan, diabaikan) → `LOYALTY_POINT_MAX_AGE_DAYS`
  (default 0 = lot tanpa `expires_at`).
- Poin online: implementasi `src/orders/online-points.ts` (provider `ONLINE_POINTS` di modul ini).

## Belum
- G22: definisi aktivitas final (Finance+Legal, D-22-12), teks syarat program & kategori template WA (Legal/Meta, L-73),
  pasal UU PDP anonimisasi (DPO). Consent `marketing` untuk notifikasi hangus belum dicek (dasar: perjanjian program).
- G21: settlement antar-PT (W-15) dan jurnal breakage (W-17, G22) di Odoo; snapshot saldo harian (query saldo penutup memindai seluruh riwayat penerbit — cukup untuk volume pilot, perlu tabel snapshot bila ledger > puluhan juta baris).
- Redeem/clawback POS + FIFO kedaluwarsa: SELESAI gelombang 13 (lihat `src/pos/README.md`, primitif `allocateDebit`/`splitCredit`/`lockMember` di sini). Sisa: sumber `mgm` (butuh validasi semantik sendiri); plafon poin per member per hari.
