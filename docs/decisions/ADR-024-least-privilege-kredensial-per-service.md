# ADR-024: Least Privilege — Kredensial Terpisah per Service

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT + Security
- **Referensi review:** SEC-02 (High), SEC-05, SEC-08, SEC-16; R-S01, R-S10, R-T08
- **Terkait:** `docs/04 §7.3` (tabel kredensial yang mengikat), ADR-022, ADR-023, `docs/runbooks/secret-rotation.md`

> **Ringkasan manajemen.** Saat ini semua aplikasi memakai akun "admin" yang sama untuk database, penyimpanan file, dan antrean pesan. Satu aplikasi dibobol = seluruh data dan backup bisa dihapus. Keputusan: setiap aplikasi punya akun sendiri dengan hak seminimal mungkin; akun admin hanya untuk instalasi dan backup. Biaya: ±1 minggu devsecops + penyesuaian kecil di tiap service; tanpa biaya lisensi.

## Konteks
SEC-02: superuser PostgreSQL `efn` dipakai 6 service; root MinIO dipakai BFF & OCR worker; satu user RabbitMQ `administrator` untuk semua; Redis tanpa auth. Skenario: RCE di OCR worker (memproses foto tak tepercaya) → `COPY … TO PROGRAM` → baca kunci backup → hapus bucket backup.

## Opsi yang dipertimbangkan
| Opsi | Kelebihan | Kekurangan | Biaya |
|---|---|---|---|
| **A. Kredensial statis per service, hak minimum, dari Docker secrets** | Sederhana, sesuai Compose & ADR-022; audit per user | Rotasi manual (runbook) | ±1 minggu |
| B. Kredensial dinamis (Vault database secrets engine) | TTL pendek, rotasi otomatis | Vault = komponen berat (lihat ADR-022 A3) | 3–4 minggu + ops |
| C. Tetap satu kredensial + isolasi network | Tanpa kerja | Tidak menutup SEC-02 | 0 |

## Keputusan
Opsi **A**, sesuai tabel `docs/04 §7.3`, dengan prinsip:
1. **Satu identitas per service per sistem** (PostgreSQL, MinIO, RabbitMQ, Redis, ClickHouse, Odoo API). Tidak ada dua service berbagi password.
2. **Superuser/root hanya untuk bootstrap & backup**: `postgres` (socket lokal, entrypoint/WAL-G), MinIO root (hanya `minio-init`), RabbitMQ admin (hanya definisi/init), ClickHouse `default` dinonaktifkan untuk aplikasi.
3. **Hak minimum**: grant level kolom untuk role yang menyentuh PII (`efn_ocr`, `efn_etl`), tanpa `CREATEROLE`/`SUPERUSER`; MinIO policy per prefix; RabbitMQ permission regex per queue + `user_id` publisher divalidasi (SEC-08); Redis ACL per prefix key, user `default` nonaktif, `noeviction`.
4. **ClickHouse** (melengkapi §7.3): `efn_etl` (INSERT ke `efn_dwh`), `efn_text2sql` (SELECT view whitelist saja, `readonly=2`, `max_execution_time`), `efn_metabase` (SELECT view), `efn_exporter`. → **usulan tambah ke kontrak** (lihat laporan).
5. **Odoo**: integrasi server-to-server ke Odoo (bila nanti BFF memanggil Odoo) memakai **user teknis per service + API key** (2FA aktif membuat password RPC ditolak, ADR-023); user teknis tanpa grup admin, hanya grup yang dibutuhkan, dibatasi company.
6. **Kunci kriptografi juga least privilege**: `pii_encryption_key` hanya BFF; ETL memakai `member_hash_pepper` turunan, bukan `otp_pepper` (SEC-16); OCR worker tidak memegang kunci PII.
7. **Rotasi**: dukungan dua nilai (current + previous) untuk service key & webhook secret; password DB/broker dirotasi tahunan atau saat staf/vendor keluar (runbook).

## Konsekuensi
- **Positif:** blast radius per service; audit log menunjukkan service pelaku; mendukung PDP (akses berbasis kebutuhan).
- **Negatif:** jumlah secret naik (dari 15 menjadi ±30) → `scripts/secrets.list` & CI check wajib; migrasi grant saat skema berubah menjadi tanggung jawab pemilik skema.
- **Dampak dokumen:** `docs/03 §3.2` daftar secret (devsecops/QA), `docs/04 §7.3`.

## Risiko terkait
R-S01, R-S10, R-T08, R-S05.

## Pertanyaan terbuka untuk klien
- Siapa yang berhak memegang kredensial admin/bootstrap di produksi (break-glass, dua orang)?

## Kapan ditinjau ulang
Bersama ADR-022 saat migrasi orkestrasi (Fase 6) — pertimbangkan kredensial dinamis.
