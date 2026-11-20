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
   per baris `efn:member_voucher.code_enc:v2:<id>` — SEC-G50-11, pola SEC-102; dekripsi menerima v1 kolom sebagai
   cadangan; terdaftar di `pii-columns.ts` dengan `rowAad` — `code_hash` HMAC) + outbox `promo.campaign.requested`
   (`campaign:<pt>:<id>:<rev>`, payload **tanpa kode**) → `efn_campaign_ack(ids)`. Baris invalid tidak di-ack.
2. **segment-worker** → `POST /internal/v1/segments/{run_id}/members` di listener HTTPS **mTLS terpisah**
   (`SEGMENT_INTERNAL_PORT`=9466, CN `SEGMENT_TLS_CLIENT_ALLOWED_CN`=segment-worker, CA `SEGMENT_CLIENT_CA_FILE`;
   tanpa TLS listener tidak dibuka). Idempoten per `(run_id, segment_code, chunk_no)` (isi beda → `409 chunk_conflict`),
   ≤ 5000 `subject_key`/chunk, body ≤ 512 KB, subjek ter-erasure dikecualikan. **SEC-G50-08:** body wajib
   `company_code` (∈ `COMPANY_CODES`, ikut hash idempotensi), satu PT per `run_id` (PT lain → `409 run_company_mismatch`);
   disimpan di `segment_chunk`/`segment_member` dan dispatcher hanya memilih anggota `company_code` = PT kampanye.
   Body lengkap: `{schema_version: 1, company_code, run_id, segment_code, chunk_no, subject_keys[≤5000]}`.
3. **campaign-dispatcher** (`bff.campaign-dispatcher` via status `platform.campaign` + PK `campaign_delivery`):
   target segmen (`segment_member` → `subject_member` → member, consent `tracking` **dan** `marketing`) atau markdown
   tanpa segmen (wishlist produk + consent `marketing`); per halaman 500: cap frekuensi (bila aktif) → delivery →
   voucher (`anonymous_code` tugaskan 1 kode, cap anggaran bila aktif; `public_code` kode bersama; `member_bound` hanya
   notifikasi) → inbox; setelah commit push Expo + email/WA via notifier BFF-B. Lewat `valid_to` → `expired`.
   Kode voucher **tidak** dikirim lewat email/WA (hanya inbox).
4. **`GET /v1/me/inbox`** (Bearer, usulan kontrak): item berlaku + kode voucher milik member.

## Payload klaim Odoo (kontrak = parser BFF, SEC-G50-02; SATU format, tanpa normalisasi bentuk lama)
`{items:[{id, approval_rev, payload:{campaign_id, company_code, kind, segment_run_id, segment_code, markdown:{channel_code?,
product_ids[≤200], outlet_codes[≤50], valid_to}|null, channels[], voucher_mode, voucher_program_ref, voucher_codes[≤5000]
(anonymous_code) | public_code (public_code), voucher_value_idr, budget_idr, cap_per_member_week, valid_from, valid_to,
message:{title≤48, body≤160}}}]}`. `efn_campaign_ack({ids})`.
- Tanggal `valid_from`/`valid_to`/`markdown.valid_to` = RFC3339 UTC berakhiran `Z` (`YYYY-MM-DD` ditolak), `valid_to > valid_from`.
- **`voucher_program_ref` = string `loyalty.program:<id>`** (regex parser `^[A-Za-z0-9_.:-]{1,64}$`; integer ditolak); `null`
  bila tanpa program voucher (maka `voucher_mode` juga `null`, tanpa kode).
- Kode: `anonymous_code` → `voucher_codes[]` (`^[A-Za-z0-9_-]{4,40}$`), `public_code` → `public_code`; `member_bound`/tanpa
  mode → TIDAK ada key kode (`codes_without_mode`); objek `voucher{}` bersarang dan `partner_id` tidak dikirim.
- Contoh normatif = fixture bersama `services/odoo/addons/efn_campaign/tests/fixtures/campaign_payload_v1.json`, diuji
  `campaign-payload-fixture.spec.ts` (fixture hilang = test gagal). Bentuk fixture yang dibaca: `{schema_version, company_code,
  cases:[{name, approval_rev?, payload, expected?:{codes?, payload?}}], invalid?:[{name, payload, reason}]}` (atau array kasus).

## Privasi & retensi
`campaign-privacy.ts`: erasure hapus inbox & delivery, kode voucher hanya dilepas tautannya (tidak kembali ke pool);
DSAR export inbox + voucher (tanpa kode). Retensi (placeholder): inbox 30 hari setelah berakhir, kode 90 hari.

## Belum / catatan
- segment-worker (`services/segment-worker/efn_segment/contract.py` `MEMBERS_KEYS`) belum mengirim `company_code` →
  sampai disesuaikan, ingest segmen menolak `400` (SEC-G50-08; perubahan kontrak §28.7).
- Semantik at-most-once per member (crash setelah commit sebelum kirim = notifikasi terlewat; inbox tetap ada).
- `member_bound`: member tanpa partner di PT kampanye tidak dapat dideteksi BFF (Odoo yang menolak saat dipakai).
