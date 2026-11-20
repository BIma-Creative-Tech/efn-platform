# `src/campaign` — kampanye tertarget (G50-A)

**Tujuan.** Kampanye yang disetujui marketing di Odoo (`efn_campaign`) dikirim ke anggota segmen / member wishlist
lewat inbox in-app, push, email, dan WA unofficial, dengan voucher per mode. Odoo tidak menerima data member dan tidak
publish ke RabbitMQ (L-155, L-156).

- **ADR:** ADR-047 §3.4 (Proposed), ADR-044, ADR-048, ADR-022 (mTLS). **Kontrak:** `docs/04` §28.7, §28.10; L-155..L-157, L-164.
- **Risiko:** R-B17 (spam/anggaran: approval Odoo, cap frekuensi/anggaran configurable — **default nonaktif**, pool kode
  habis = berhenti, `expires_at`), R-L13 (segmen = consent `tracking` + `marketing`), R-L01, R-S04 (endpoint segmen mTLS).
- **Flag:** `PROMO_CAMPAIGN_ENABLED=false` (production OFF sampai DPO + Marketing + Finance);
  `CAMPAIGN_FREQUENCY_CAP_ENABLED`, `CAMPAIGN_BUDGET_CAP_ENABLED` (default false).

## Alur
1. **campaign-sync** (`CAMPAIGN_SYNC_SECONDS`=60, `CAMPAIGN_COMPANIES`=SMD): `efn.campaign/efn_campaign_claim` (limit 20,
   tidak di-retry) per PT dengan API key `odoo_api_key_campaign_<pt>` → validasi allowlist (`parseClaim`) → **satu
   transaksi**: `platform.campaign` + kode voucher terenkripsi (`member_voucher.code_enc` AES-256-GCM key ring PII, AAD
   `efn:member_voucher.code_enc:v1`, `code_hash` HMAC) + outbox `promo.campaign.requested`
   (`campaign:<pt>:<id>:<rev>`, payload **tanpa kode**) → `efn_campaign_ack(ids)`. Baris invalid tidak di-ack.
2. **segment-worker** → `POST /internal/v1/segments/{run_id}/members` di listener HTTPS **mTLS terpisah**
   (`SEGMENT_INTERNAL_PORT`=9466, CN `SEGMENT_TLS_CLIENT_ALLOWED_CN`=segment-worker, CA `SEGMENT_CLIENT_CA_FILE`;
   tanpa TLS listener tidak dibuka). Idempoten per `(run_id, segment_code, chunk_no)` (isi beda → `409 chunk_conflict`),
   ≤ 5000 `subject_key`/chunk, body ≤ 512 KB, subjek ter-erasure dikecualikan.
3. **campaign-dispatcher** (`bff.campaign-dispatcher` via status `platform.campaign` + PK `campaign_delivery`):
   target segmen (`segment_member` → `subject_member` → member, consent `tracking` **dan** `marketing`) atau markdown
   tanpa segmen (wishlist produk + consent `marketing`); per halaman 500: cap frekuensi (bila aktif) → delivery →
   voucher (`anonymous_code` tugaskan 1 kode, cap anggaran bila aktif; `public_code` kode bersama; `member_bound` hanya
   notifikasi) → inbox; setelah commit push Expo + email/WA via notifier BFF-B. Lewat `valid_to` → `expired`.
   Kode voucher **tidak** dikirim lewat email/WA (hanya inbox).
4. **`GET /v1/me/inbox`** (Bearer, usulan kontrak): item berlaku + kode voucher milik member.

## Payload klaim Odoo yang diharapkan (usulan kontrak, untuk efn-odoo-engineer)
`{items:[{id, approval_rev, payload:{campaign_id, company_code, kind, segment_run_id, segment_code, markdown:{channel_code?,
product_ids[≤200], outlet_codes[≤50], valid_to}|null, channels[], voucher_mode, voucher_program_ref, voucher_codes[≤5000]
(anonymous_code) | public_code (public_code), voucher_value_idr, budget_idr, cap_per_member_week, valid_from, valid_to,
message:{title≤48, body≤160}}}]}`. `efn_campaign_ack({ids})`.

## Privasi & retensi
`campaign-privacy.ts`: erasure hapus inbox & delivery, kode voucher hanya dilepas tautannya (tidak kembali ke pool);
DSAR export inbox + voucher (tanpa kode). Retensi (placeholder): inbox 30 hari setelah berakhir, kode 90 hari.

## Belum / catatan
- `channel-bindings.ts` mengimpor `EmailNotifier`/`WaUnofficialNotifier` dari path tebakan BFF-B — sesuaikan bila beda.
- Kolom `member_voucher.code_enc` belum terdaftar di registry re-encrypt PII (`src/common/pii/pii-columns.ts`) —
  wajib sebelum rotasi kunci (`PII_RETIRED_VERSIONS`).
- Semantik at-most-once per member (crash setelah commit sebelum kirim = notifikasi terlewat; inbox tetap ada).
- `member_bound`: member tanpa partner di PT kampanye tidak dapat dideteksi BFF (Odoo yang menolak saat dipakai).
