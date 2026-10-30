#!/bin/sh
# Entrypoint RabbitMQ EFN (docs/04 §7.3, SEC-02, SEC-08). Dijalankan sebagai root, lalu menyerahkan ke
# docker-entrypoint.sh resmi (turun ke user `rabbitmq`).
# Membangkitkan /tmp/efn-definitions.json dari Docker secret: user per service dengan hash password
# (rabbit_password_hashing_sha256 = base64(salt4 || sha256(salt4 || password))), permission regex per service,
# tanpa tag administrator untuk user aplikasi. Plaintext password tidak pernah ditulis ke disk/log.
set -eu

OUT=/tmp/efn-definitions.json
ADMIN_USER="${RABBITMQ_ADMIN_USER:-efn-admin}"
umask 077
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

hash_pw() {
  # $1 = file secret
  openssl rand 4 > "$WORK/salt"
  { cat "$WORK/salt"; tr -d '\n' < "$1"; } | openssl dgst -sha256 -binary > "$WORK/digest"
  cat "$WORK/salt" "$WORK/digest" | openssl base64 -A
}

for s in rabbitmq_password rabbitmq_password_bff rabbitmq_password_ocr rabbitmq_password_etl; do
  [ -s "/run/secrets/$s" ] || { echo "rabbitmq-entrypoint: secret $s kosong/tidak ada" >&2; exit 1; }
done

ADMIN_HASH="$(hash_pw /run/secrets/rabbitmq_password)"
BFF_HASH="$(hash_pw /run/secrets/rabbitmq_password_bff)"
OCR_HASH="$(hash_pw /run/secrets/rabbitmq_password_ocr)"
ETL_HASH="$(hash_pw /run/secrets/rabbitmq_password_etl)"

# Queue kontrak docs/04 §4 + §8.4 (+ DLQ). Publish lewat default exchange = izin write pada `amq.default`.
#
# SEC-25 - permission dipersempit sejauh kode klien mengizinkan:
#  - Topologi (queue quorum + DLQ, argumen IDENTIK dengan services/api-gateway/src/common/amqp/queues.ts dan
#    services/ai-ocr-worker/efn_ocr/messages.py) kini dideklarasikan BROKER lewat definitions di bawah, sehingga
#    klien tidak perlu configure bila memakai passive declare.
#  - RabbitMQ mensyaratkan configure (+ read pada queue, write pada DLX) untuk queue.declare NON-pasif, juga bila
#    queue sudah ada. Sejak G5 BFF (checkQueue) & ocr-worker (queue_declare(passive=True)) hanya passive declare,
#    ETL tidak declare -> DEFAULT RABBITMQ_STRICT_PERMISSIONS=true: configure dicabut seluruhnya, read hanya queue
#    yang DIKONSUMSI. Fallback `false` = configure/read untuk queue kontrak saja (mode lama).
#  - write efn-bff/efn-ocr/efn-etl = `amq.default` saja (publish ke default exchange; tidak ada bind).
#  - efn-etl (DSAR ClickHouse): write `amq.default` saja, configure/read kosong (ETL tidak men-declare/konsumsi).
#    Batas: write ke default exchange tidak bisa dibatasi per routing key -> kontrol nyata = properti `user_id`
#    (divalidasi broker) + allowlist sumber di consumer BFF (SEC-08).
# G17 (docs/04 §10.9): saga penjualan online - publisher & consumer sama-sama BFF (outbox -> queue -> consumer):
#   order.paid, order.unpaid_closed, order.compensate, order.refund.requested (+ .dlq), argumen tambahan
#   x-delivery-limit 10 (quorum: setelah 10 kali redelivery pesan di-dead-letter ke DLQ, bukan loop poison message).
#   Diverifikasi di RabbitMQ 3.13.7 (image dipin) - laporan devsecops G17.
# G31 (docs/04 §20.3): mgm.reward.requested (+ .dlq) pola yang sama (publisher & consumer BFF, x-delivery-limit 10).
Q='^(ocr\\.requested|loyalty\\.earn\\.requested|notify\\.push|privacy\\.erasure\\.completed|order\\.(paid|unpaid_closed|compensate|refund\\.requested)|mgm\\.reward\\.requested)(\\.dlq)?$'
W='^amq\\.default$'
# G6 QA (SEC-25): default STRICT. Passive queue.declare tidak memeriksa izin configure di RabbitMQ (handler passive
# rabbit_channel tidak memanggil check_configure_permitted) - PLAUSIBLE, verifikasi di staging. Bila klien gagal
# start dengan ACCESS_REFUSED saat checkQueue, set RABBITMQ_STRICT_PERMISSIONS=false (fallback lama).
if [ "${RABBITMQ_STRICT_PERMISSIONS:-true}" != "false" ]; then
  BFF_CONF='^$'; BFF_READ='^(loyalty\\.earn\\.requested|notify\\.push|privacy\\.erasure\\.completed|order\\.(paid|unpaid_closed|compensate|refund\\.requested)|mgm\\.reward\\.requested)$'
  OCR_CONF='^$'; OCR_READ='^ocr\\.requested$'
else
  BFF_CONF="$Q"; BFF_READ="$Q"
  OCR_CONF='^(ocr\\.requested|loyalty\\.earn\\.requested|notify\\.push)(\\.dlq)?$'; OCR_READ="$OCR_CONF"
fi

queue_defs() {
  # $1 = nama queue -> deklarasi DLQ + queue utama (quorum, DLX default exchange)
  printf '    {"name": "%s.dlq", "vhost": "/", "durable": true, "auto_delete": false, "arguments": {"x-queue-type": "quorum"}},\n' "$1"
  printf '    {"name": "%s", "vhost": "/", "durable": true, "auto_delete": false, "arguments": {"x-queue-type": "quorum", "x-dead-letter-exchange": "", "x-dead-letter-routing-key": "%s.dlq"}}' "$1" "$1"
}

queue_defs_limited() {
  # $1 = nama queue, $2 = x-delivery-limit (G17 §10.9). Argumen queue tidak bisa diubah setelah queue ada -> hanya
  # untuk queue baru; queue lama (§4/§8.4) tetap tanpa argumen ini (ubah = hapus + buat ulang queue, rencanakan).
  printf '    {"name": "%s.dlq", "vhost": "/", "durable": true, "auto_delete": false, "arguments": {"x-queue-type": "quorum"}},\n' "$1"
  printf '    {"name": "%s", "vhost": "/", "durable": true, "auto_delete": false, "arguments": {"x-queue-type": "quorum", "x-dead-letter-exchange": "", "x-dead-letter-routing-key": "%s.dlq", "x-delivery-limit": %s}}' "$1" "$1" "$2"
}

cat > "$WORK/defs.json" <<EOF
{
  "users": [
    {"name": "${ADMIN_USER}", "password_hash": "${ADMIN_HASH}", "hashing_algorithm": "rabbit_password_hashing_sha256", "tags": ["administrator"]},
    {"name": "efn-bff", "password_hash": "${BFF_HASH}", "hashing_algorithm": "rabbit_password_hashing_sha256", "tags": []},
    {"name": "efn-ocr", "password_hash": "${OCR_HASH}", "hashing_algorithm": "rabbit_password_hashing_sha256", "tags": []},
    {"name": "efn-etl", "password_hash": "${ETL_HASH}", "hashing_algorithm": "rabbit_password_hashing_sha256", "tags": []}
  ],
  "vhosts": [{"name": "/"}],
  "permissions": [
    {"user": "${ADMIN_USER}", "vhost": "/", "configure": ".*", "write": ".*", "read": ".*"},
    {"user": "efn-bff", "vhost": "/", "configure": "${BFF_CONF}", "write": "${W}", "read": "${BFF_READ}"},
    {"user": "efn-ocr", "vhost": "/", "configure": "${OCR_CONF}", "write": "${W}", "read": "${OCR_READ}"},
    {"user": "efn-etl", "vhost": "/", "configure": "^\$", "write": "${W}", "read": "^\$"}
  ],
  "queues": [
$(queue_defs ocr.requested),
$(queue_defs loyalty.earn.requested),
$(queue_defs notify.push),
$(queue_defs privacy.erasure.completed),
$(queue_defs_limited order.paid 10),
$(queue_defs_limited order.unpaid_closed 10),
$(queue_defs_limited order.compensate 10),
$(queue_defs_limited order.refund.requested 10),
$(queue_defs_limited mgm.reward.requested 10)
  ]
}
EOF
install -m 0600 -o rabbitmq -g rabbitmq "$WORK/defs.json" "$OUT"
unset ADMIN_HASH BFF_HASH OCR_HASH ETL_HASH
rm -rf "$WORK"
trap - EXIT

exec docker-entrypoint.sh rabbitmq-server
