# migration — impor member & saldo pembuka (docs/04 §9.6 K-7)

Tujuan: memindahkan member dan saldo poin dari sistem lama ke ledger tunggal BFF (ADR-020) tanpa membuka jalur publik.
ADR: ADR-020, ADR-022 (enkripsi, least privilege). Risiko: R-L01 (UU PDP), R-B05 (poin fiktif lewat impor).

| Endpoint | Body | Respons |
|---|---|---|
| `POST /internal/v1/migration/members` | `{ source_ref, member_ref, consents?: [{ purpose: location\|marketing, granted, version, text_sha256, recorded_at? }] }` | `{ member: created\|existing, consents_recorded }` |
| `POST /internal/v1/migration/opening-balance` | `{ source_ref, member_ref, points (1..1e6), company_code }` | `{ applied, points_balance }` |

- Path di luar prefix global `/v1` (`src/common/http/global-prefix.ts`). Traefik publik sudah mengecualikan `/internal` (`!PathPrefix(/internal)` di compose).
- Aktif hanya bila `MIGRATION_ENABLED=true`; selain itu 404. Header `X-EFN-Migration-Key` = secret `migration_api_key` (`MIGRATION_API_KEY_FILE` → `/run/secrets/migration_api_key`), wajib berbeda dari `odoo_bff_api_key` (dicek saat start).
- Idempoten: member per nomor (UNIQUE member_hash); consent satu baris `source='migration'` per (member, purpose); saldo per `source_ref` (`idempotency_key = migration_opening:<source_ref>`, data beda → `409 idempotency_conflict`).
- Saldo pembuka diberi masa berlaku `LOYALTY_POINT_EXPIRY_DAYS` sejak tanggal migrasi (PLACEHOLDER — menunggu Finance/Q-13).
- Member yang sedang dihapus (DSAR) → `409 member_deleting`.
- **SEC-65 (Gelombang 13c)**: member DAN saldo pembuka ditolak bila subjek (a) punya `dsar_request` delete `scheduled`/`processing` → `409 member_deleting`, atau (b) tag HMAC-nya ada di `platform.erasure_suppression` (erasure sudah `completed`, `ERASURE_SUPPRESSION_DAYS`, default 90) → `409 member_erased`. Skrip migrasi harus memperlakukan keduanya sebagai "lewati baris" (bukan retry). Penolakan dicatat `EFN_LOYALTY_ANOMALY {"kind":"migration_erased_member",...}` tanpa nomor. Sisa: subjek yang dihapus > `ERASURE_SUPPRESSION_DAYS` lalu atau saat nilai 0 dapat diimpor ulang — skrip migrasi tetap wajib memakai ekspor terbaru sistem lama dan mengecualikan DSAR yang diterima di luar platform.
- **SEC-61/SEC-66**: tiap saldo pembuka yang dibukukan → `efn_loyalty_migration_points_total{company}` dan log `EFN_LOYALTY_MIGRATION {"company":"RTL","points":250,"source_ref":"legacy-001"}` (rekonsiliasi Finance per batch dari Loki); saldo > `LOYALTY_MIGRATION_ANOMALY_POINTS` (5 000) → `EFN_LOYALTY_ANOMALY kind=migration_large_balance` (tetap diterima).

Belum: rate limit (dijaga kunci + jaringan internal), laporan rekonsiliasi total saldo per PT setelah impor, skrip klien migrasi.
