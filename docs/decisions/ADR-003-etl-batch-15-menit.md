# ADR-003: ETL Batch Incremental 15 Menit (CDC Debezium Ditunda)

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead + Finance (ekspektasi "real-time" laporan)
- **Referensi review:** D-02, §3.6, R-O03, R-T11

> **Ringkasan manajemen.** Dashboard manajemen akan diperbarui setiap 15 menit, bukan detik-ke-detik. Ini cukup untuk keputusan harian, jauh lebih murah dirawat, dan tidak memperlambat kasir. "Real-time" di BRD diterjemahkan menjadi "near real-time 15 menit". Bisa dinaikkan ke streaming (CDC) di fase berikut bila terbukti dibutuhkan.

## Konteks
- BRD & Arsitektur v2: interval ETL **10 menit**.
- TSD (Documentation Suite): **15 menit**.
- Cetak Biru: **Debezium CDC** (streaming).
- BRD §5.1 meminta konsolidasi keuangan "real-time"; Odoo CE tidak punya konsolidasi bawaan (§3.6).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **Batch incremental 15 menit** (watermark `write_date`, baca dari replica) | Sederhana, mudah di-debug & di-replay, idempotent per window | Delete fisik tidak tertangkap (perlu soft-delete / rekonsiliasi harian); latensi ≤15 menit | Sudah di-scaffold (`services/etl`); ~2–3 minggu untuk mart pertama |
| B. Batch 10 menit | Sedikit lebih segar | Beban replica 1,5× untuk manfaat marginal | Sama dengan A |
| C. Debezium CDC → Kafka/RabbitMQ → ClickHouse | Latensi detik, menangkap delete | Butuh Kafka Connect (+JVM, +RAM 4–8 GB), replication slot di DB Odoo (risiko WAL menumpuk bila consumer mati), skill khusus | +4–6 minggu; +1 node atau +16 GB RAM; risiko operasional di DB primary |

## Keputusan
Opsi A. ETL Python incremental tiap 15 menit membaca **replica** PostgreSQL (bukan primary), menulis ke ClickHouse dengan pola idempotent (ReplacingMergeTree / partisi per window). Rekonsiliasi penuh harian di luar jam operasional untuk menangkap delete dan koreksi.

## Konsekuensi
- **Positif:** beban POS terlindungi (R-T11), operasional sederhana, biaya rendah.
- **Negatif:** dashboard tertinggal ≤15 menit; delete fisik baru terlihat setelah rekonsiliasi harian.
- **Dampak dokumen:** BRD §5.1 "real-time" → "near real-time (≤15 menit)"; Arsitektur v2 "10 menit" → 15 menit; Cetak Biru CDC → fase 6.

## Risiko terkait
R-O03 (ETL membebani Odoo), R-T11 (POS lambat), R-T06 (Text-to-SQL membaca data basi — tampilkan timestamp "data per").

## Pertanyaan terbuka untuk klien
- **Q-47**: 10 dashboard pertama, grain, dan refresh yang dibutuhkan.
- **Q-49**: retensi DWH & anonimisasi.
- **Q-6**: format laporan konsolidasi yang dibutuhkan auditor.

## Kapan ditinjau ulang
Bila ada use case yang terbukti butuh latensi < 1 menit (mis. dynamic pricing otomatis, fraud real-time), atau window ETL melebihi 10 menit secara konsisten.
