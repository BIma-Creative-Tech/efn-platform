# ADR-007: Data Price Hunter Disimpan di PostgreSQL (Source of Truth), ClickHouse Hanya Turunan

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead
- **Referensi review:** D-06, R-B05, R-L01, R-L06

> **Ringkasan manajemen.** Setiap laporan harga dari pelanggan disimpan dulu di database transaksi (PostgreSQL) agar bisa diaudit, dibatalkan bila curang, dan dihapus bila pelanggan meminta (UU PDP). Gudang data analitik (ClickHouse) mendapat salinannya tiap 15 menit. Tidak ada biaya tambahan.

## Konteks
- FSD §2 langkah 4: *"Node.js mengirim insert query ke ClickHouse: INSERT INTO competitor_prices (...) VALUES (...)"*.
- Arsitektur v2: PostgreSQL.
- ClickHouse tidak dirancang untuk update/delete per baris (mutasi asinkron, berat), tidak punya transaksi; status laporan berubah (`queued → processing → valid/rejected/flagged`) dan bisa dibatalkan setelah review fraud.

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **PostgreSQL `platform.price_hunter_report` → ETL → ClickHouse** | Transaksional, status machine, idempotent, hak hapus PDP mudah | Latensi analitik ≤15 menit | Sudah di kontrak `docs/04 §3.3`; tanpa tambahan |
| B. Insert langsung ke ClickHouse (FSD) | Langsung tersedia di dashboard | Tidak bisa update status/clawback dengan aman; tidak ada audit trail; API terikat ke DWH (bila DWH down, fitur mati); hapus data PDP mahal | Rework saat fraud/PDP muncul |
| C. Tulis ganda (PG + CH) dari API | Segar di keduanya | Inkonsistensi bila salah satu gagal (dual-write) | +1 minggu + bug rekonsiliasi |

## Keputusan
Opsi A. Prinsip CLAUDE.md: *"PostgreSQL = source of truth; ClickHouse hanya turunan via ETL. Tidak ada tulis langsung API → ClickHouse."* Data yang diekspor ke ClickHouse memakai `member_hash` (bukan nomor HP) dan harga agregat untuk tampilan publik (R-L06).

## Konsekuensi
- **Positif:** audit & clawback; DWH bisa di-rebuild dari PG; kepatuhan PDP.
- **Negatif:** dashboard harga kompetitor tertinggal ≤15 menit.
- **Dampak dokumen:** FSD §2 langkah 4 dikoreksi (`docs/05` K-02).

## Risiko terkait
R-B05, R-L01 (PDP: retensi foto & lokasi), R-L06 (publikasi nama kompetitor), R-S06 (upload berbahaya).

## Pertanyaan terbuka untuk klien
- **Q-11**: aturan agregasi harga kompetitor (median/terbaru/per wilayah).
- **Q-44**: whitelist kompetitor & SKU.
- **Q-49**: retensi data & foto (usulan: foto 90 hari, data terstruktur 24 bulan — perlu persetujuan DPO).

## Kapan ditinjau ulang
Tidak dijadwalkan; hanya bila volume laporan > 100 ribu/hari.
