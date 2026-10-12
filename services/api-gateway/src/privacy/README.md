# privacy — consent, DSAR, erasure lintas-store (UU PDP 27/2022)

Kontrak: `docs/04` §8.4, §8.7 (v3.1), §8.9 (v3.3), §8.10, §8.13 (v3.6: SEC-31 tombstone acak, R6 receipt, SEC-40 step-up sekali pakai). Desain: `docs/compliance/PDP_DSAR_DESIGN.md`, `PDP_CONSENT.md`.
Menutup sisi BFF dari SEC-11 / R-L01. Retensi & masa tenggang = **usulan, menunggu DPO** (semua via env).
Build/lint/test dijalankan di container `node:22-bookworm` (gelombang 9: 313 test hijau). Smoke E2E bagian DSAR belum diulang (stack dev tidak menyala).

## Endpoint pelanggan (Bearer)
| Endpoint | Sukses | Gagal |
|---|---|---|
| `GET /v1/me/consents` | `200 [{ purpose: "location"\|"marketing", granted, version, updated_at }]` (tanpa riwayat → `granted:false, version:null, updated_at:null`) | `401` |
| `PUT /v1/me/consents/:purpose` `{ granted, version }` | `200 { purpose, granted, version, updated_at }` | `400`, `404` purpose tak dikenal, `409 stale_text` (grant dengan versi ≠ `CONSENT_<P>_VERSION`), `503 consent_unavailable` (teks aktif belum dikonfigurasi). Penarikan selalu diterima. |
| `POST /v1/me/reauth` `{}` | `202 { challenge_id, expires_in }` (OTP purpose `dsar` ke nomor akun sendiri; tanpa ALTCHA) | `429` (`DSAR_REAUTH_LIMIT`/15 menit per member) |
| `POST /v1/me/reauth` `{ challenge_id, code }` | `200 { step_up_token, expires_in: 600 }` | `400 { ok:false, reason, attempts_left }` (juga bila challenge milik member lain) |
| `GET /v1/me/data` + `X-Step-Up-Token` | `200` JSON export (profil + nomor penuh, poin, laporan Price Hunter, consent + riwayat, permintaan, daftar pemroses, `truncated`) | `403 step_up_required` / `403 step_up_invalid`, `429` (`DSAR_EXPORT_HOURLY_LIMIT`/jam) |
| `DELETE /v1/me[?confirm_open_orders=true]` + `X-Step-Up-Token` | `202 { request_id, scheduled_at, points_forfeited, receipt_token, open_online_orders, erasure_deadline }` (idempotent: permintaan aktif dikembalikan, receipt baru; G18 W-18: `open_online_orders` = jumlah bagian pesanan terbuka, `erasure_deadline` = batas paling lambat erasure bila > 0, selain itu `null`) | `409 open_online_orders` (G18 W-18: ada bagian pesanan terbuka tanpa `confirm_open_orders=true`; body `error.{code,message,open_parts,max_defer_days,confirm_param}`, teks menyebut batas 14 hari; dikirim SEBELUM token step-up ditandai terpakai → ulangi dengan token yang sama + `?confirm_open_orders=true`), `400` nilai query lain, `403 step_up_required\|step_up_invalid` (v3.6 SEC-40: token step-up **sekali pakai** untuk DELETE — jti di Redis `otp:stepup-used:<jti>` sampai exp; ekspor tetap boleh memakai token yang sama) |
| `POST /v1/me/delete/cancel` | `200 { request_id, status:"cancelled" }` | `404` (tidak ada yang `scheduled`) |
| `GET /v1/me/requests` | `200 { items: [{ id, type, status, requested_at, scheduled_at, completed_at, open_online_orders, erasure_deadline }] }` (G18 W-18: jumlah bagian pesanan terbuka untuk hapus `scheduled`, lainnya `0` / `null`) | `401` |

## Endpoint status hapus akun tanpa sesi (R6, v3.6 §8.13)
| Endpoint | Sukses | Gagal |
|---|---|---|
| `GET /v1/dsar/requests/:id` + header `X-DSAR-Receipt: <receipt_token>` (publik via Traefik `api.`, tanpa Bearer) | `200 { id, type, status, requested_at, scheduled_at, completed_at, open_online_orders, erasure_deadline }` (tanpa PII; G18 W-18: jumlah saja, tanpa detail order) | `404 not_found` generik (receipt hilang/salah/kedaluwarsa/milik request lain, id bukan UUID, request tidak ada), `429` (`DSAR_RECEIPT_RL_LIMIT`/15 menit per IP-hash, default 30) |

`receipt_token` = `v1.<exp>.<base64url(HMAC-SHA256(k, "dsar_receipt
<request_id>
<exp>"))>`, `k` = HKDF-SHA256(`jwt_secret`, info `efn:dsar_receipt:v1`), berlaku `DSAR_RECEIPT_TTL_DAYS` (30). Stateless: tidak ada tabel/log yang menautkan receipt ke subjek; rotasi `jwt_secret` membatalkan semua receipt. Next.js menyimpannya di cookie HttpOnly `efn_dsar_receipt` (Path `/api/dsar`, SameSite=Strict, 30 hari) — tugas frontend.

## Endpoint Odoo (service key, in-cluster)
| Endpoint | Sukses | Gagal |
|---|---|---|
| `GET /v1/pos/privacy/erasures?store=odoo` | `200 { items: [{ request_id, member_hash }] }` (status `processing`, `odoo` ∈ `stores_pending`, maks 500) | `400` store lain |
| `POST /v1/pos/privacy/erasures/:request_id/complete` `{ store:"odoo", records:n }` (n ≥ 0) | `200 { ok:true, status }` (idempotent) | `400`, `404`, `409 not_processing` |

## Event
`privacy.erasure.completed` (consumer BFF, queue dideklarasikan broker): `{ schema_version: 2, idempotency_key: "erasure:<request_id>:clickhouse", request_id, store: "clickhouse" }`, **hanya `user_id = efn-etl`**; `idempotency_key` harus persis sesuai `request_id`. Dedup `processed_message` (ETL mengirim ulang tiap siklus). Request tak dikenal/tidak `processing|completed` → DLQ.

## Alur hapus akun
1. `DELETE /v1/me` (step-up) → `dsar_request (type delete, status scheduled, scheduled_at = now + DSAR_DELETE_GRACE_DAYS)`. Login & pembatalan tetap bisa selama masa tenggang.
2. Job `ErasureService` (`DSAR_JOB_INTERVAL_MS`, advisory lock per request) setelah `scheduled_at`, SATU transaksi: `processing` + `stores_pending={platform,odoo,clickhouse}` + `member.deleted_at` → (catat daftar foto) → erasure platform → `stores_pending -= platform`; foto MinIO dihapus SETELAH commit (v3.6, SEC-40 race batal-vs-hapus foto):
   - ledger: entri `forfeit` per PT (`source dsar`, `source_ref` NULL, `idempotency_key = dsar:forfeit:<uuid acak>`) lalu semua baris → `member_ref_hash = tombstone`, `member_ref_enc = NULL`, `erased_at = now()`;
   - `price_hunter_report`: `user_hash = tombstone`, `user_enc`/`geo_*` NULL, `updated_at` now;
   - `otp_audit`: `subject = tombstone`, `ip_enc` NULL; `consent`: `ip_enc` NULL (bukti dipertahankan); `dsar_request` lain milik member (export, delete yang dulu dibatalkan): `member_hash` NULL; `member`: DELETE.
   - **Tombstone (v3.6, SEC-31)** = 32 byte **acak** (hex 64) per erasure, sama untuk semua baris subjek (saldo per tombstone tetap 0). Tidak diturunkan dari `request_id`/`member_hash`, tidak disimpan di tabel/log lain → tidak bisa ditautkan kembali. Identitas lama hilang dari semua sumber yang dibaca ETL sehingga backfill/watermark tidak bisa memuat ulang subjek.
3. Odoo pull/complete, ClickHouse event → `stores_pending` kosong → `completed` + **`member_hash = NULL`** (SEC-31). Selama `processing` hash tetap ada (Odoo & ETL membaca hanya status `processing`). CHECK `ck_dsar_member_hash_active`: hash wajib ada selama request aktif.

### Migrasi v3.6 (`infra/postgres/init/04-platform-v3-6.sql`)
Idempoten, satu transaksi. Volume baru: otomatis. **Volume lama** (dev/staging): jalankan manual sebagai superuser sebelum menjalankan ulang 02/03:
`docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -f /docker-entrypoint-initdb.d/04-platform-v3-6.sql`. Isi: (1) SEC-39 pemilik skema → `efn_platform_owner` + grant DML minimum `efn_bff`; (2) `member_hash` NULLABLE + backfill + CHECK; (3) tombstone deterministik lama diganti acak, `source_ref`/`idempotency_key` forfeit lama yang memuat request_id dibersihkan.
Diuji di container `postgres:16.6-bookworm`: volume lama (01–03 versi sebelumnya + data contoh → 04 → 04 lagi → 02/03 baru → cek) dan volume baru (01–04 baru).

### SEC-39 — kepemilikan skema
Pemilik skema `platform` & semua objeknya = role **NOLOGIN** `efn_platform_owner` (dipakai migrasi lewat `SET ROLE` superuser). `efn_bff` bukan pemilik dan bukan anggota → tidak bisa `DISABLE TRIGGER`, `GRANT` ulang, `TRUNCATE`, DDL, atau `DELETE` ledger/consent/dsar (diverifikasi). Hak `efn_bff` per tabel ada di 04 (perubahan query BFF yang butuh hak baru = perbarui 04). Sisa: superuser/DBA tetap bisa mengubah bukti consent; `UPDATE(ip_enc)` consent masih bisa menimpa IP bukti dengan ciphertext lain (dibutuhkan erasure & re-encrypt). Bila bukti consent dipakai di sengketa: hash-chain baris consent (backlog).

## Consent
Tabel append-only `platform.consent` (hak efn_bff: SELECT, INSERT, UPDATE(ip_enc) + trigger menolak DELETE/TRUNCATE/UPDATE kolom lain). `text_sha256` = hash teks aktif (`CONSENT_<PURPOSE>_TEXT_SHA256`); penarikan dengan teks belum dikonfigurasi memakai `UNKNOWN_TEXT_SHA256` (sha256 string kosong). IP terenkripsi (AAD `efn:consent.ip_enc:v1`). Market Scout membuang koordinat tanpa consent `location`.

### SEC-65 (Gelombang 13c) — daftar tolak migrasi pasca-erasure
Masalah: setelah erasure `completed`, `member` dihapus dan `dsar_request.member_hash` di-NULL-kan, sehingga impor migrasi K-7 file lama dapat membuat ulang subjek beserta consent/saldonya.
Desain: saat request masuk `processing`, `ErasureService` menyimpan `tag = HMAC-SHA256(HKDF(otp_pepper,"efn:erasure_suppression:v1"), "efn:erasure_suppression:v1\n" + member_hash)` di `platform.erasure_suppression (tag, expires_at)` selama `ERASURE_SUPPRESSION_DAYS` (default **365** sejak G18/W-19, Q-P12; maks 730; 0 = mati). Migrasi menolak member_hash dengan DSAR aktif atau tag belum kedaluwarsa. Tag kedaluwarsa dihapus tiap siklus job erasure.
Trade-off privasi (untuk DPO):
- Yang disimpan hanya tag + waktu kedaluwarsa: tanpa request_id, waktu permintaan, PT, atau member_hash. Tag berdomain terpisah → tidak bisa di-JOIN ke tabel/mart mana pun; `efn_readonly`/`efn_etl` tidak punya akses.
- Sisa risiko: pemegang `otp_pepper` (BFF/Superadmin) yang mengetahui sebuah nomor HP dapat menguji "nomor ini pernah meminta penghapusan dalam N hari terakhir". Ini data pribadi (pseudonim) yang diproses untuk menghormati hak hapus (daftar supresi) — perlu dicantumkan di RoPA (`PDP_DATA_INVENTORY` v4) dengan dasar & retensi.
- Alternatif yang ditolak: (a) tanpa daftar → subjek bisa hidup kembali; (b) menyimpan member_hash apa adanya → dapat di-JOIN ke ledger/mart lain; (c) tolak hanya selama `processing` → celah setelah `completed` (dipakai bila DPO memilih `ERASURE_SUPPRESSION_DAYS=0`).
- Re-registrasi lewat login OTP oleh pemilik nomor TIDAK diblokir (hanya impor migrasi) — itu kehendak subjek sendiri.
- Urutan deploy: jalankan `06-platform-loyalty-guard.sql` SEBELUM BFF Gelombang 13c; tanpa tabel, transaksi erasure gagal dan dicoba ulang (DSAR tertunda, tidak ada data hilang).

## Belum / risiko terbuka
- Export > batas (1000 baris per bagian) dipotong (`truncated:true`); jalur `202` + tautan unduh MinIO belum.
- `correction`/`phone_change` (PATCH /v1/me, ganti nomor) belum diimplementasi (tipe sudah ada di CHECK).
- Konsol DPO (`/internal/v1/dsar`), fraud hold, notifikasi WA "selesai" belum.
- Retensi consent "akun + 5 th" butuh job DBA (BFF tidak punya DELETE).
- Re-login setelah erasure dengan nomor sama = akun baru (member_hash sama); bila Odoo/ETL masih memproses request lama, data baru bisa ikut terhapus (jendela sempit) — dicatat untuk DPO.
- Status `pending_reauth`, `rejected`, `failed` sudah di CHECK, belum dipakai alur otomatis.
- SEC-31 sisa: (a) korelasi waktu — `erased_at`/`created_at` entri ber-tombstone ≈ `scheduled_at` request dapat menautkan baris ke `request_id`/`member_id`; setelah `completed` request tidak lagi memuat `member_hash` sehingga rantai ke nomor HP terputus. (b) `member_hash` dapat dibalik oleh pemegang pepper selama `processing` (perlakukan sebagai pengenal langsung — PDP_DATA_INVENTORY). (c) Replay erasure setelah restore backup tidak lagi bisa memakai `dsar_request.member_hash` yang sudah NULL — butuh daftar terenkripsi kunci kustodi Superadmin (rekomendasi SEC-31 #2, belum). (d) Baris ClickHouse `loyalty_events` yang sudah dimuat dengan tombstone lama deterministik tidak ikut diganti migrasi (ETL ledger memakai watermark `created_at`) — koordinasi data-ai. (e) Log PostgreSQL dev (`log_statement=all`) memuat parameter query.
- R6: receipt hilang (cookie dihapus) = status hanya via kanal lain; tidak ada pemulihan receipt.

## Fase 3 — order online (docs/04 §10.8.3, L-4)
- `GET /v1/me/data` memuat `online_orders` (tanpa kode ambil, QR, id Odoo/payment).
- `DELETE /v1/me`: order `created|awaiting_payment` dibatalkan otomatis; bagian `paying|confirmed|ready|review` → `409 open_online_orders` (ambil/batalkan lewat CS) **kecuali** `?confirm_open_orders=true` (G18).

### G18 — W-18 (Q-P18): penundaan hapus akun maks 14 hari
Keputusan pemilik proyek 10 Okt 2026: penundaan karena pesanan berjalan sah, **maksimum 14 hari**, eskalasi CS, setelah itu erasure tetap jalan. Desain dipilih: **409 sebagai langkah informasi + 202 dengan konfirmasi** (bukan 409 permanen, bukan 202 diam-diam):
- 409 permanen (L-4 lama) tidak punya jangkar waktu — permintaan tidak tercatat sehingga batas 14 hari tidak bisa ditegakkan dan hak hapus bisa tertahan tanpa batas bila CS tidak bertindak.
- 202 tanpa konfirmasi melanggar semangat L-4: pelanggan harus tahu bahwa kode ambil akan hilang dari aplikasi.
- Jadi: `DELETE /v1/me` → 409 (teks menyebut 14 hari) → pelanggan mengonfirmasi → `DELETE /v1/me?confirm_open_orders=true` (token step-up yang sama; pra-cek 409 berjalan sebelum jti ditandai) → 202, `dsar_request.open_orders_ack_at` = bukti pilihan sadar. Checkout baru ditolak `409 account_deleting` (SEC-100).
- Job `ErasureService`: saat jatuh tempo (`scheduled_at`, masa tenggang 7 h) dan masih ada order terbuka → tunda (rollback) selama `requested_at` + `DSAR_OPEN_ORDER_MAX_DEFER_DAYS` (14, plafon 14) belum lewat; tidak ada alert per siklus.
- Eskalasi CS sekali per tingkat (`dsar_request.defer_escalation`), dihitung dari `requested_at` (juga selama masa tenggang): **hari 3** `EFN_ONLINE_ANOMALY {"kind":"erasure_deferred_open_orders","severity":"warning","request_id","day":3,"open_orders","max_defer_days"}`, **hari 10** severity `critical`. Metrik `efn_dsar_erasure_deferral_total{event=escalation_d3|escalation_d10|forced}`. Tanpa nomor HP/member_id/member_hash.
- Setelah batas: erasure **tetap** jalan — `online_order.member_id` NULL (tautan dilepas), baris order/bagian/refund tetap; anomali `erasure_forced_open_orders` (critical) memuat `part_ids` (maks 20, opak) + `parts_total`. CS menyelesaikan lewat CLI tanpa tautan akun: `online-erasure-pending` (daftar kerja read-only: request tertunda + batas + id bagian; bagian terbuka yang sudah tanpa tautan), lalu `online-refund --part=… --full` / `online-resolve` / serah terima di Odoo setelah verifikasi lewat kanal lain. Kode ambil TIDAK pernah ditampilkan ke CS.
- Belum: teks pelanggan final (Legal, PDP_DATA_INVENTORY §4 butir 6); runbook CS (architect); wrapper host `efn-online-cs` belum mengenal subcommand `erasure-pending` (devsecops).
- Erasure platform: `online_order.member_id` → NULL + `member_unlinked_at` (baris keuangan tetap).
