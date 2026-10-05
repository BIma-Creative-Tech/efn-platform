# Desain Hak Subjek Data (DSAR) — Akses, Koreksi, Hapus Akun & Poin

- **Status:** Draf untuk DPO/Legal. **v2 (2026-10-06): diselaraskan dengan kontrak `docs/04 §8.2` & §8.4** (endpoint, tabel, alur penghapusan lintas store kini mengikat engineer). Yang masih usulan ditandai "(usulan, belum di kontrak)".
- **Tanggal:** 2026-10-05 · revisi 2026-10-06 · Penyusun: efn-architect · Menutup sebagian SEC-11 / R-L01
- **Terkait:** `PDP_DATA_INVENTORY.md`, `PDP_CONSENT.md`, `PDP_TRANSFER_LINTAS_NEGARA.md`, ADR-020 (ledger), ADR-022 (enkripsi), ADR-029 (member opak)
- **Keputusan klien:** kebijakan UU PDP (DPO, retensi, transfer lintas negara) **belum diputuskan** (6 Okt 2026). Nilai default di sini dipakai sementara dan semuanya configurable.

> **Ringkasan manajemen.** Pelanggan dapat (1) mengunduh salinan datanya, (2) memperbaiki data, dan (3) menghapus akunnya dari aplikasi setelah verifikasi OTP ulang. Penghapusan punya **masa tenggang 7 hari** (bisa dibatalkan), lalu dijalankan otomatis di semua penyimpanan: platform, Odoo (ERP), dan gudang data analitik — tanpa pernah mengirim nomor HP mentah antar-sistem. Catatan keuangan yang wajib disimpan (poin, faktur) tetap ada **tanpa identitas**. Masa simpan data memakai angka usulan sampai DPO menetapkan.

## 1. Hak yang didukung (UU PDP — nomor pasal diverifikasi Legal)
| Hak | Kanal | Otomatis? |
|---|---|---|
| Informasi pemrosesan | Kebijakan privasi `/privasi` | — |
| Akses & salinan (portabilitas) | Super-App → `GET /v1/me/data` | Ya |
| Koreksi | Super-App (nomor HP via OTP ganda — usulan), CS untuk data Odoo | Sebagian |
| Hapus / akhiri pemrosesan | Super-App → `DELETE /v1/me` | Ya (platform, Odoo, ClickHouse) |
| Tarik persetujuan | `PUT /v1/me/consents/:purpose` (`PDP_CONSENT.md`) | Ya |
| Keberatan atas keputusan otomatis | CS → review manual (laporan Price Hunter `rejected/flagged`) | Manual |

## 2. SLA usulan (menunggu DPO/Legal)
| Langkah | SLA |
|---|---|
| Tanda terima (`request_id`) | Seketika |
| Verifikasi identitas | Seketika via step-up OTP (`POST /v1/me/reauth`) |
| Export data | ≤ 3×24 jam (target < 1 jam) |
| Koreksi | ≤ 3×24 jam |
| Penghapusan selesai di semua store | Masa tenggang `DSAR_DELETE_GRACE_DAYS` (default **7**) + ≤ 7 hari eksekusi (Odoo cron & siklus ETL) |
| Backup | Data hilang dari backup saat siklus retensi berakhir (14 full); restore wajib menjalankan ulang daftar `dsar_request` berstatus selesai sebelum sistem dibuka |

## 3. Endpoint BFF (kontrak `docs/04 §8.4`)
Bearer pelanggan (JWT `sub` = `member.id` UUID, ADR-029), rate limit ketat. Respons tidak pernah mengungkap apakah nomor lain terdaftar.

| Method & path | Fungsi | Status kontrak |
|---|---|---|
| `GET /v1/me/data` | Export: profil (HP termask), riwayat poin, laporan Price Hunter milik sendiri, consent, daftar pemroses | **Kontrak** |
| `POST /v1/me/reauth` | Re-OTP → token step-up `purpose: dsar`, exp 10 menit | **Kontrak** |
| `DELETE /v1/me` | Minta hapus akun; **step-up wajib**; masa tenggang `DSAR_DELETE_GRACE_DAYS` | **Kontrak** |
| `POST /v1/me/delete/cancel` | Batalkan dalam masa tenggang | **Kontrak** |
| `GET /v1/me/requests` | Status DSAR milik sendiri | **Kontrak** |
| `GET /v1/me/consents`, `PUT /v1/me/consents/:purpose` | Consent (`location`, `marketing`) | **Kontrak** |
| `PATCH /v1/me` | Koreksi profil | Usulan, belum di kontrak |
| `POST /v1/me/phone-change` | Ganti nomor: OTP ke nomor lama **dan** baru; `member.id` tetap, `member_hash`/`member_ref_enc` diperbarui, ledger dipindah entri `transfer` | Usulan, belum di kontrak |
| `GET /internal/v1/dsar`, `POST /internal/v1/dsar/:id/{approve,reject}` | Konsol DPO/CS (WireGuard + 2FA) — untuk penolakan berdasar kewajiban hukum / fraud hold | Usulan, belum di kontrak |
| `GET /v1/pos/privacy/erasures?store=odoo` | Odoo menarik daftar penghapusan (service key) → `[{ request_id, member_hash }]` | **Kontrak** |
| `POST /v1/pos/privacy/erasures/:request_id/complete` | Odoo melapor selesai `{ store: "odoo", records: n }` | **Kontrak** |

## 4. Tabel (kontrak `docs/04 §8.2, §8.4`)
```sql
-- §8.2
CREATE TABLE platform.member (
  id              uuid PRIMARY KEY,
  member_hash     char(64) UNIQUE NOT NULL,
  member_ref_enc  bytea NOT NULL,           -- dikosongkan saat hapus (lihat catatan kolom NOT NULL di §7)
  created_at      timestamptz NOT NULL DEFAULT now(),
  deleted_at      timestamptz NULL
);
-- §8.4
CREATE TABLE platform.dsar_request (
  id              uuid PRIMARY KEY,
  member_id       uuid NOT NULL,
  member_hash     char(64) NOT NULL,
  type            text NOT NULL,            -- access | delete (| rectify | phone_change: usulan)
  status          text NOT NULL,            -- mis. received → scheduled → processing → done | cancelled | rejected (nilai final: lead)
  stores_pending  text[] NOT NULL DEFAULT '{}',   -- {platform, odoo, clickhouse}
  requested_at    timestamptz NOT NULL,
  scheduled_at    timestamptz,
  completed_at    timestamptz
);
```
Kolom `reason` (alasan penolakan/penundaan, mis. kewajiban hukum, fraud hold) **belum** ada di kontrak — usulan ke lead.

## 5. Alur hapus akun (kontrak §8.4)
```mermaid
sequenceDiagram
  participant U as Pelanggan
  participant BFF
  participant PG as PG platform
  participant S3 as MinIO
  participant O as Odoo (cron)
  participant ETL
  participant CH as ClickHouse
  participant MQ as RabbitMQ
  U->>BFF: POST /v1/me/reauth (OTP) → step-up token
  U->>BFF: DELETE /v1/me (step-up)
  BFF->>PG: INSERT dsar_request(type=delete, scheduled_at = now + DSAR_DELETE_GRACE_DAYS)
  BFF-->>U: 202 {request_id, scheduled_at, points_forfeited}
  Note over BFF: Sesi dicabut segera; pembatalan via /v1/me/delete/cancel
  BFF->>PG: saat scheduled_at: status = processing, stores_pending = {platform, odoo, clickhouse}
  BFF->>PG: platform: forfeit poin, ledger.member_ref_enc = NULL, report.user_enc = NULL, member dianonimkan (deleted_at), consent dipertahankan tanpa PII
  BFF->>S3: hapus objek foto milik member
  BFF->>PG: stores_pending -= platform
  O->>BFF: GET /v1/pos/privacy/erasures?store=odoo
  BFF-->>O: [{request_id, member_hash}]
  O->>O: anonimkan res.partner WHERE efn_member_hash = member_hash
  O->>BFF: POST .../erasures/:request_id/complete {store:"odoo", records:n}
  ETL->>PG: baca dsar_request (id, member_hash, status, stores_pending) tiap siklus
  ETL->>CH: DELETE / ALTER ... DELETE pada mart WHERE member_hash = ...
  ETL->>MQ: privacy.erasure.completed {schema_version:2, idempotency_key:"erasure:<request_id>:clickhouse", request_id, store:"clickhouse"}
  MQ->>BFF: stores_pending -= clickhouse
  BFF->>PG: stores_pending kosong → status done, completed_at
```

Aturan:
- **Tanpa HP mentah antar-sistem**: Odoo menemukan partner lewat field `efn_member_hash` (stored, indexed) yang dihitung dari `phone` ternormalisasi dengan rumus `docs/04 §1` memakai secret `member_hash_pepper` (`EFN_MEMBER_HASH_PEPPER_FILE`). ClickHouse menerima `member_hash` dari PostgreSQL (tidak memegang pepper).
- **Idempotent per store**: `erasure:<request_id>:<store>`; Odoo `complete` boleh diulang; consumer punya DLQ.
- **Ledger**: dipertahankan (catatan keuangan, usulan 10 tahun, ADR-020) dengan `member_ref_enc` = NULL; kolom menjadi NULLABLE **hanya** untuk baris akun terhapus (CHECK terhadap status member). Poin tersisa di-*forfeit* dengan entri ledger (perlakuan akuntansi: Finance).
- **Odoo**: partner dianonimkan (nama "Pelanggan dihapus", phone/email/alamat/`efn_member_hash` dikosongkan, diarsipkan); order & faktur tetap 10 tahun. Faktur atas nama NPWP pelanggan yang wajib utuh **tidak** dianonimkan — pelanggan diberi tahu alasannya.
- **Fraud hold**: penghapusan member dalam investigasi (R-B05) ditunda dengan alasan tercatat (verifikasi Legal).
- **Saldo poin tidak diuangkan** saat hapus (konfirmasi Finance/Legal, ADR-006).

## 6. Retensi default (kontrak §8.4 — **usulan, menunggu DPO**; semua configurable via env)
| Data | Default | Mekanisme |
|---|---|---|
| `otp_audit` | 90 hari | Purge BFF |
| Foto Market Scout (MinIO) | **90 hari** (lifecycle objek) | MinIO ILM — **implementasi saat ini 365 hari, perlu diubah devsecops** |
| `price_hunter_report.user_enc` | NULL setelah 90 hari | Job BFF |
| Baris `price_hunter_report` | 2 tahun | Job BFF |
| ClickHouse mart | TTL 5 tahun | `TTL` tabel (data-ai) |
| Ledger poin | 10 tahun | Tidak dipurge; PII dikosongkan saat hapus akun |
| Consent | Selama akun + 5 tahun | Tidak dipurge sebelum batas |
| `dsar_request` | 5 tahun (usulan, belum di kontrak) | — |

## 7. Catatan untuk lead (bukan perubahan kontrak oleh architect)
1. `platform.member.member_ref_enc` NOT NULL di §8.2, tetapi §8.4 meminta anonimisasi → perlu NULLABLE untuk `deleted_at IS NOT NULL` (CHECK), sama seperti ledger.
2. **Pseudonim di ledger/report setelah hapus**: kontrak mempertahankan `member_hash` di ledger dan report. Hash berpepper tetap data pribadi (dapat dicocokkan ulang bila nomor & pepper diketahui). Usulan: ganti dengan tombstone `sha256("erased:" || request_id)` setelah semua store selesai — keputusan **DPO** (Q-P6).
3. Nilai status `dsar_request` dan kolom `reason` belum didefinisikan.
4. ClickHouse `DELETE` (lightweight delete) vs `ALTER ... DELETE` (mutation) — biaya mutation pada mart besar; data-ai memilih, catat di README ETL.

## 8. Pertanyaan untuk klien (DPO/Legal/Finance — belum diputuskan)
- Masa tenggang 7 hari dan SLA; perlakuan poin saat hapus (hangus vs konversi).
- Pemilik konsol DSAR (CS atau DPO).
- **Q-P6** Tombstone pseudonim di ledger setelah hapus akun (lihat §7.2).
