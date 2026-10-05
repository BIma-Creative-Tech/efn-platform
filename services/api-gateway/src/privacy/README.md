# privacy — consent, DSAR, erasure lintas-store (UU PDP 27/2022)

Kontrak: `docs/04` §8.4, §8.7 (v3.1), §8.9 (v3.3), §8.10. Desain: `docs/compliance/PDP_DSAR_DESIGN.md`, `PDP_CONSENT.md`.
Menutup sisi BFF dari SEC-11 / R-L01. Retensi & masa tenggang = **usulan, menunggu DPO** (semua via env).
Build/test **belum dijalankan lokal**.

## Endpoint pelanggan (Bearer)
| Endpoint | Sukses | Gagal |
|---|---|---|
| `GET /v1/me/consents` | `200 [{ purpose: "location"\|"marketing", granted, version, updated_at }]` (tanpa riwayat → `granted:false, version:null, updated_at:null`) | `401` |
| `PUT /v1/me/consents/:purpose` `{ granted, version }` | `200 { purpose, granted, version, updated_at }` | `400`, `404` purpose tak dikenal, `409 stale_text` (grant dengan versi ≠ `CONSENT_<P>_VERSION`), `503 consent_unavailable` (teks aktif belum dikonfigurasi). Penarikan selalu diterima. |
| `POST /v1/me/reauth` `{}` | `202 { challenge_id, expires_in }` (OTP purpose `dsar` ke nomor akun sendiri; tanpa ALTCHA) | `429` (`DSAR_REAUTH_LIMIT`/15 menit per member) |
| `POST /v1/me/reauth` `{ challenge_id, code }` | `200 { step_up_token, expires_in: 600 }` | `400 { ok:false, reason, attempts_left }` (juga bila challenge milik member lain) |
| `GET /v1/me/data` + `X-Step-Up-Token` | `200` JSON export (profil + nomor penuh, poin, laporan Price Hunter, consent + riwayat, permintaan, daftar pemroses, `truncated`) | `403 step_up_required` / `403 step_up_invalid`, `429` (`DSAR_EXPORT_HOURLY_LIMIT`/jam) |
| `DELETE /v1/me` + `X-Step-Up-Token` | `202 { request_id, scheduled_at, points_forfeited }` (idempotent: permintaan aktif dikembalikan) | `403 step_up_required\|step_up_invalid` |
| `POST /v1/me/delete/cancel` | `200 { request_id, status:"cancelled" }` | `404` (tidak ada yang `scheduled`) |
| `GET /v1/me/requests` | `200 { items: [{ id, type, status, requested_at, scheduled_at, completed_at }] }` | `401` |

## Endpoint Odoo (service key, in-cluster)
| Endpoint | Sukses | Gagal |
|---|---|---|
| `GET /v1/pos/privacy/erasures?store=odoo` | `200 { items: [{ request_id, member_hash }] }` (status `processing`, `odoo` ∈ `stores_pending`, maks 500) | `400` store lain |
| `POST /v1/pos/privacy/erasures/:request_id/complete` `{ store:"odoo", records:n }` (n ≥ 0) | `200 { ok:true, status }` (idempotent) | `400`, `404`, `409 not_processing` |

## Event
`privacy.erasure.completed` (consumer BFF, queue dideklarasikan broker): `{ schema_version: 2, idempotency_key: "erasure:<request_id>:clickhouse", request_id, store: "clickhouse" }`, **hanya `user_id = efn-etl`**; `idempotency_key` harus persis sesuai `request_id`. Dedup `processed_message` (ETL mengirim ulang tiap siklus). Request tak dikenal/tidak `processing|completed` → DLQ.

## Alur hapus akun
1. `DELETE /v1/me` (step-up) → `dsar_request (type delete, status scheduled, scheduled_at = now + DSAR_DELETE_GRACE_DAYS)`. Login & pembatalan tetap bisa selama masa tenggang.
2. Job `ErasureService` (`DSAR_JOB_INTERVAL_MS`, advisory lock per request) setelah `scheduled_at`: hapus foto MinIO (best effort), lalu SATU transaksi: `processing` + `stores_pending={platform,odoo,clickhouse}` + `member.deleted_at` → erasure platform → `stores_pending -= platform`:
   - ledger: entri `forfeit` per PT (`source dsar`, `dsar:forfeit:<id>:<PT>`) lalu semua baris → `member_ref_hash = tombstone`, `member_ref_enc = NULL`, `erased_at = now()`;
   - `price_hunter_report`: `user_hash = tombstone`, `user_enc`/`geo_*` NULL, `updated_at` now;
   - `otp_audit`: `subject = tombstone`, `ip_enc` NULL; `consent`: `ip_enc` NULL (bukti dipertahankan); `member`: DELETE.
   - **Tombstone** = `sha256("erased:" || request_id)`: identitas lama hilang dari SEMUA sumber yang dibaca ETL sebelum request selesai, sehingga backfill/watermark ETL tidak bisa memuat ulang subjek; tidak dapat dihubungkan ke nomor. `dsar_request.member_hash` tetap disimpan (Odoo/ClickHouse mencari subjek, dan restore backup wajib menjalankan ulang daftar erasure).
3. Odoo pull/complete, ClickHouse event → `stores_pending` kosong → `completed`.

## Consent
Tabel append-only `platform.consent` (hak efn_bff: SELECT, INSERT, UPDATE(ip_enc) + trigger menolak DELETE/TRUNCATE/UPDATE kolom lain). `text_sha256` = hash teks aktif (`CONSENT_<PURPOSE>_TEXT_SHA256`); penarikan dengan teks belum dikonfigurasi memakai `UNKNOWN_TEXT_SHA256` (sha256 string kosong). IP terenkripsi (AAD `efn:consent.ip_enc:v1`). Market Scout membuang koordinat tanpa consent `location`.

## Belum / risiko terbuka
- Export > batas (1000 baris per bagian) dipotong (`truncated:true`); jalur `202` + tautan unduh MinIO belum.
- `correction`/`phone_change` (PATCH /v1/me, ganti nomor) belum diimplementasi (tipe sudah ada di CHECK).
- Konsol DPO (`/internal/v1/dsar`), fraud hold, notifikasi WA "selesai" belum.
- Retensi consent "akun + 5 th" butuh job DBA (BFF tidak punya DELETE).
- Re-login setelah erasure dengan nomor sama = akun baru (member_hash sama); bila Odoo/ETL masih memproses request lama, data baru bisa ikut terhapus (jendela sempit) — dicatat untuk DPO.
- Status `pending_reauth`, `rejected`, `failed` sudah di CHECK, belum dipakai alur otomatis.
