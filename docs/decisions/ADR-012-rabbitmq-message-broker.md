# ADR-012: RabbitMQ sebagai Message Broker (bukan Kafka, bukan tanpa broker)

- **Status:** Proposed
- **Tanggal:** 2026-10-05
- **Pemutus (klien):** IT Lead
- **Referensi review:** D-12, R-T10, R-T05

> **Ringkasan manajemen.** Beberapa pekerjaan tidak boleh membuat pelanggan menunggu (membaca foto, kirim WhatsApp, hitung reward, menerima konfirmasi pembayaran). Pekerjaan itu dimasukkan ke antrean RabbitMQ dan diproses di belakang. RabbitMQ open source, ringan, dan cukup untuk skala EFN beberapa tahun ke depan; Kafka berlebihan.

## Konteks
- Blueprint, Arsitektur v2, TSD: **tidak ada** broker.
- Cetak Biru: *RabbitMQ/Kafka*.
- Kebutuhan nyata: OCR async (ADR-008), notifikasi, MGM settle, webhook payment/3PL (saga, R-T10).

## Opsi yang dipertimbangkan

| Opsi | Kelebihan | Kekurangan | Biaya / waktu |
|---|---|---|---|
| A. **RabbitMQ 3.13, quorum queue + DLQ** | Ringan (<1 GB RAM), routing fleksibel, DLQ native, library matang (amqplib, pika) | Bukan log replay jangka panjang | Sudah di compose; 0 tambahan |
| B. Kafka | Replay, throughput sangat tinggi, basis CDC | JVM, RAM 4–8 GB+, operasional berat untuk tim kecil | +1 node / +16 GB RAM; +3–4 minggu |
| C. Tanpa broker (cron + tabel job di PG) | Komponen lebih sedikit | Polling, retry & DLQ ditulis sendiri | +2–3 minggu, kualitas lebih rendah |
| D. Redis Streams | Sudah ada Redis | Redis dipakai untuk OTP/rate limit; mencampur peran; durabilitas lebih lemah | Kecil, risiko menengah |

## Keputusan
Opsi A. Konvensi: queue `<domain>.<event>`, DLQ `<queue>.dlq`, `message_id = idempotency_key`, `schema_version` di payload, consumer idempotent via `platform.processed_message`, publish lewat **outbox pattern** (`platform.outbox`) — semua sesuai `docs/04 §4–5`.

## Konsekuensi
- **Positif:** API responsif; kegagalan eksternal di-retry tanpa kehilangan pesan.
- **Negatif:** satu komponen stateful lagi untuk dipantau (metrics Prometheus plugin) dan di-backup definisinya.

## Risiko terkait
R-T10 (distributed transaction), R-T05, R-T09 (monitoring antrean & DLQ alert).

## Pertanyaan terbuka untuk klien
Tidak ada yang bersifat bisnis.

## Kapan ditinjau ulang
Bila CDC Debezium diadopsi (ADR-003 ditinjau) atau throughput > 5.000 msg/detik sustained.
