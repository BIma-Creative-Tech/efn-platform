# Runbook: Antrean OCR menumpuk / tanpa consumer

Alert: `EfnOcrQueueBacklog` (`ocr.requested` > 200 pesan selama 10 menit), `EfnOcrNoConsumer` (0 consumer selama 10 menit).

## Dampak
Laporan Price Hunter tertahan di status `queued`; poin belum diberikan. **Tidak** berdampak ke POS/checkout (AI di luar jalur transaksi). Severity warning: tangani di jam kerja kecuali backlog terus naik.

## Diagnosis
```bash
$DC ps ocr-worker ollama
$DC logs --tail=200 ocr-worker
docker exec efn-rabbitmq-1 rabbitmqctl list_queues name messages_ready messages_unacknowledged consumers
```
| Gejala | Penyebab | Tindakan |
|---|---|---|
| consumers = 0 | worker crash / tidak bisa login RabbitMQ / Postgres | log worker; cek secret `rabbitmq_password_ocr`, `pg_password_ocr`, sertifikat TLS (`EfnInternalTlsHandshakeFailed`, [internal-pki.md](internal-pki.md)) |
| consumers > 0, unacked tinggi, ready naik | worker lambat (VLM fallback ke Ollama lambat / GPU penuh) | `$DC logs ollama`, `nvidia-smi`; naikkan replika worker sementara |
| lonjakan publish | kampanye Price Hunter / abuse | cek cap harian per user di BFF (default 20), rate limit `ratelimit-api` |

## Pemulihan
- Restart worker: `$DC up -d --force-recreate ocr-worker`.
- Tambah kapasitas sementara: `$DC up -d --scale ocr-worker=4 ocr-worker` (kembalikan setelah backlog habis; prod default 2).
- Ollama mati: worker menandai report `flagged` (review manual) sesuai kontrak; backlog tetap turun.
- **Jangan** purge queue: setiap pesan adalah laporan pengguna (poin). Bila harus membuang, ekspor dulu dan koordinasi dengan tim produk.
