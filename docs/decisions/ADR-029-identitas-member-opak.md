# ADR-029: Identitas Member Opak — JWT `sub` = UUID, Bukan Nomor HP

- **Status:** Proposed — sudah menjadi kontrak `docs/04 §8.2` (lead, 6 Okt 2026); tanda tangan klien sebagai bagian paket UU PDP.
- **Tanggal:** 2026-10-06
- **Pemutus (klien):** IT + Legal/DPO
- **Referensi review:** R-L01, SEC-11; usulan efn-architect Gelombang 3 (#7)
- **Terkait:** ADR-020 (ledger `member_hash`), ADR-022 (PII AES-GCM), `docs/compliance/PDP_DSAR_DESIGN.md`, `PDP_DATA_INVENTORY.md`

> **Ringkasan manajemen.** Token login pelanggan sebelumnya memuat nomor HP. Kini token hanya memuat **nomor acak (UUID)** yang tidak bermakna di luar sistem; nomor HP disimpan terenkripsi di satu tabel. Kebocoran token/log/cookie tidak lagi membuka nomor HP, penghapusan akun lebih bersih, dan ganti nomor HP tidak mengganti identitas. Biaya ±2–3 hari BFF, Rp0.

## Opsi
| Opsi | Kelebihan | Kekurangan |
|---|---|---|
| A. `sub` = nomor HP (kondisi awal) | Sederhana | PII di cookie, log, header, token pihak ketiga; ganti nomor = identitas baru |
| B. `sub` = `member_hash` | Tanpa tabel baru | Pseudonim tetap data pribadi dan stabil lintas sistem (Odoo/ClickHouse) → token bocor dapat dikorelasikan; ganti nomor mengubah hash |
| **C. `sub` = UUID acak `platform.member.id`** | Tidak dapat dikorelasikan tanpa DB; stabil saat ganti nomor; mendukung DSAR (`dsar_request.member_id`) | Tabel & lookup tambahan |

## Keputusan (rekomendasi)
**C**, sesuai `docs/04 §8.2`: `platform.member (id uuid PK, member_hash char(64) UNIQUE, member_ref_enc bytea NOT NULL, created_at, deleted_at)`. JWT `sub` = `member.id`. `GET /v1/me` → `{ member_ref_masked, points_balance }`. Ledger & laporan tetap memakai `member_hash` (ADR-020); `consent` & `dsar_request` memakai `member_id`. Saat hapus akun: `member_ref_enc` dihapus/dinull-kan, `deleted_at` diisi, token dicabut.

## Konsekuensi
Positif: minimisasi PII di token & log; ganti nomor via `phone-change` hanya memperbarui `member_hash`/`member_ref_enc` (ledger dipindah dengan entri `transfer`). Negatif: satu lookup per request terautentikasi (cache); migrasi token lama (dev/staging saja — belum ada produksi).

## Kapan ditinjau ulang
Bila login non-HP (email/SSO) ditambahkan.
