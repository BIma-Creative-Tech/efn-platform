# Runbook: Dead-letter queue berisi pesan

Alert: `EfnDeadLetterNotEmpty` — queue `<queue>.dlq` (mis. `ocr.requested.dlq`, `loyalty.earn.requested.dlq`, `notify.push.dlq`) > 0 selama 5 menit.

## Dampak
Pesan gagal diproses secara permanen (payload invalid, bug consumer, dependensi down melebihi retry). Untuk `loyalty.earn.requested.dlq` berarti **poin pelanggan belum tercatat** — prioritaskan.

## Diagnosis
1. Lihat isi tanpa menghapus (management hanya HTTPS :15671 via tunnel/VPN, tidak dipublish di prod; user admin
   `efn-admin`):
   ```bash
   docker exec efn-rabbitmq-1 rabbitmqadmin --ssl --port=15671 --host=localhost \
     --ssl-ca-cert-file=/run/secrets/efn_ca_crt -u efn-admin -p "$(cat /opt/efn/secrets/rabbitmq_password.txt)" \
     get queue=ocr.requested.dlq count=5 ackmode=reject_requeue_true
   ```
   Perhatikan header `x-death` (alasan: `rejected`, `expired`, `delivery_limit`) dan `message_id` (= idempotency key).
2. Cocokkan dengan log consumer di Loki: `{service=~"api-gateway|ocr-worker"} |= "<message_id>"`.
3. Data pribadi: payload bisa memuat `member_ref`. Jangan menyalin payload ke tiket/chat tanpa masking (UU PDP).

## Pemulihan
1. Perbaiki akar masalah dulu (deploy fix / pulihkan dependensi).
2. Replay ke queue asal — aman karena semua consumer idempotent (`platform.processed_message`, cek status report):
   ```bash
   docker exec efn-rabbitmq-1 rabbitmqctl set_parameter shovel dlq-replay \
     '{"src-protocol":"amqp091","src-uri":"amqp://","src-queue":"ocr.requested.dlq","src-delete-after":"queue-length",
       "dest-protocol":"amqp091","dest-uri":"amqp://","dest-queue":"ocr.requested"}'
   ```
   (butuh plugin `rabbitmq_shovel`; `amqp://` tanpa host = koneksi *direct* di dalam node Erlang, bukan TCP —
   tidak melanggar aturan TLS. Alternatif: skrip replay di BFF bila sudah tersedia.)
3. Pesan yang memang invalid: catat `message_id`, lalu purge DLQ setelah disetujui pemilik domain.
