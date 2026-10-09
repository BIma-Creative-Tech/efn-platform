#!/bin/sh
# One-shot MinIO bootstrap (service `minio-init` di compose). Idempoten, aman dijalankan setiap `up`.
# docs/04 §7.1 (TLS), §7.2 (SSE-S3, versioning, object lock), §7.3 (user per service).
#  - Koneksi HTTPS ke minio:9000, diverifikasi dengan CA internal (/run/secrets/efn_ca_crt). Tanpa fallback HTTP.
#  - bucket market-scout : foto Price Hunter, privat, SSE-S3, lifecycle hapus objek reports/* setelah
#                          MINIO_MARKET_SCOUT_EXPIRE_DAYS (default 90 hari, docs/04 §8.4 - menunggu DPO; SEC-11)
#  - user metrik         : hanya admin:Prometheus (bearer JWT metrik, MINIO_PROMETHEUS_AUTH_TYPE=jwt, SEC-20)
#  - bucket pg-backup    : repo WAL-G, privat, SSE-S3, versioning + OBJECT LOCK (GOVERNANCE) + expiry versi lama
#                          (object lock HANYA bisa diaktifkan saat bucket DIBUAT -> lihat docs/runbooks/internal-pki.md
#                          bagian MinIO untuk migrasi bucket lama)
#  - bucket catalog      : (G17, docs/04 §10.10) gambar katalog online yang disalin BFF dari Odoo; privat, SSE-S3,
#                          tanpa lifecycle (retensi = job BFF: aktif + 30 hari setelah deleted_at, docs/04 §10.8)
#  - user backup (WAL-G), bff (Put/Delete market-scout/reports/*; Get/Put/Delete catalog/*), ocr (Get market-scout/reports/*)
#    `user add` = upsert -> juga dipakai untuk rotasi password.
#  - Access key (nama user) = SECRET ACAK (docs/04 §8.12, ADR-030): minio_{backup,bff,ocr,prometheus}_access_key.
#    Nama lama yang mudah ditebak (pgbackup, efn-bff, efn-ocr, efn-prometheus) DIHAPUS bila masih ada (migrasi).
#    Access key tidak pernah dicetak ke log (keluaran `mc admin user ...` dibuang).
#  - Admin API hanya dijangkau dari sini (network `objstore`); klien aplikasi lewat filter s3-gateway yang menolak
#    /minio/admin/* (infra/s3-gateway/haproxy.cfg).
set -eu

ROOT_PW="$(cat /run/secrets/minio_root_password)"
BACKUP_PW="$(cat /run/secrets/minio_backup_password)"
BFF_PW="$(cat /run/secrets/minio_bff_secret)"
OCR_PW="$(cat /run/secrets/minio_ocr_secret)"
PROM_PW="$(cat /run/secrets/minio_prometheus_secret)"
read_ak() {
  v="$(tr -d '\r\n' < "/run/secrets/$1")"
  case "$v" in
    ''|*[!A-Za-z0-9._-]*) echo "minio-init: secret $1 kosong/tidak valid" >&2; exit 1 ;;
  esac
  printf '%s' "$v"
}
BACKUP_USER="$(read_ak minio_backup_access_key)"
BFF_USER="$(read_ak minio_bff_access_key)"
OCR_USER="$(read_ak minio_ocr_access_key)"
PROM_USER="$(read_ak minio_prometheus_access_key)"
# Root user acak (SEC-48): secret minio_root_user, sama dengan MINIO_ROOT_USER_FILE service minio
MINIO_ROOT_USER="$(read_ak minio_root_user)"
LEGACY_USERS="pgbackup efn-bff efn-ocr efn-prometheus"
MINIO_URL="${MINIO_URL:-https://minio:9000}"
MS_BUCKET="${MINIO_BUCKET_MARKET_SCOUT:-market-scout}"
PG_BUCKET="${MINIO_BUCKET_PG_BACKUP:-pg-backup}"
CAT_BUCKET="${MINIO_BUCKET_CATALOG:-catalog}"
LOCK_DAYS="${MINIO_PG_BACKUP_LOCK_DAYS:-15}"
NONCURRENT_DAYS="${MINIO_PG_BACKUP_NONCURRENT_DAYS:-35}"
PHOTO_DAYS="${MINIO_MARKET_SCOUT_EXPIRE_DAYS:-90}"
case "$PHOTO_DAYS" in ''|*[!0-9]*) echo "minio-init: MINIO_MARKET_SCOUT_EXPIRE_DAYS harus angka" >&2; exit 1 ;; esac
MC_CONFIG_DIR="${MC_CONFIG_DIR:-/tmp/.mc}"
export MC_CONFIG_DIR

# mc mempercayai CA di <config>/certs/CAs
mkdir -p "${MC_CONFIG_DIR}/certs/CAs"
cp /run/secrets/efn_ca_crt "${MC_CONFIG_DIR}/certs/CAs/efn-ca.crt"

i=0
until mc alias set efn "${MINIO_URL}" "${MINIO_ROOT_USER}" "${ROOT_PW}" > /dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "minio-init: MinIO (${MINIO_URL}) tidak siap / TLS gagal diverifikasi setelah 60 detik" >&2
    exit 1
  fi
  sleep 2
done

# ── Bucket ──────────────────────────────────────────────────────────────────────
mc mb --ignore-existing "efn/${MS_BUCKET}"
mc anonymous set none "efn/${MS_BUCKET}"
mc mb --ignore-existing "efn/${CAT_BUCKET}"
mc anonymous set none "efn/${CAT_BUCKET}"

if mc stat "efn/${PG_BUCKET}" > /dev/null 2>&1; then
  if mc retention info --default "efn/${PG_BUCKET}" 2> /dev/null | grep -qi 'GOVERNANCE\|COMPLIANCE'; then
    :
  elif mc version info "efn/${PG_BUCKET}" 2> /dev/null | grep -qi 'enabled' \
       && mc retention set --default GOVERNANCE "${LOCK_DAYS}d" "efn/${PG_BUCKET}" > /dev/null 2>&1; then
    :
  else
    # Tidak menggagalkan deploy (WAL archive harus tetap jalan), tetapi berisik: lihat runbook.
    echo "minio-init: PERINGATAN bucket ${PG_BUCKET} sudah ada TANPA object lock. Object lock hanya bisa diaktifkan" >&2
    echo "minio-init: saat bucket dibuat. Ikuti docs/runbooks/internal-pki.md#minio-object-lock untuk migrasi." >&2
  fi
else
  mc mb --with-lock "efn/${PG_BUCKET}"
fi
mc anonymous set none "efn/${PG_BUCKET}"
mc version enable "efn/${PG_BUCKET}" > /dev/null
mc retention set --default GOVERNANCE "${LOCK_DAYS}d" "efn/${PG_BUCKET}" > /dev/null 2>&1 || true

# ── Enkripsi at rest SSE-S3 (kunci MINIO_KMS_SECRET_KEY_FILE = secret minio_kms_key) ────────────────
mc encrypt set sse-s3 "efn/${MS_BUCKET}"
mc encrypt set sse-s3 "efn/${PG_BUCKET}"
mc encrypt set sse-s3 "efn/${CAT_BUCKET}"

# ── Lifecycle (import = ganti seluruh konfigurasi -> idempoten) ──────────────────────
cat > /tmp/ilm-ms.json <<EOF
{"Rules":[{"ID":"efn-photo-retention","Status":"Enabled","Filter":{"Prefix":"reports/"},
 "Expiration":{"Days":${PHOTO_DAYS}}}]}
EOF
mc ilm import "efn/${MS_BUCKET}" < /tmp/ilm-ms.json > /dev/null

cat > /tmp/ilm-pg.json <<EOF
{"Rules":[{"ID":"efn-walg-noncurrent","Status":"Enabled","Filter":{"Prefix":""},
 "NoncurrentVersionExpiration":{"NoncurrentDays":${NONCURRENT_DAYS}},
 "Expiration":{"ExpiredObjectDeleteMarker":true}}]}
EOF
mc ilm import "efn/${PG_BUCKET}" < /tmp/ilm-pg.json > /dev/null

# ── Kebijakan & user per service (docs/04 §7.3) ─────────────────────────────────
cat > /tmp/pgbackup-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow",
     "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads", "s3:ListBucketVersions"],
     "Resource": ["arn:aws:s3:::${PG_BUCKET}"]},
    {"Effect": "Allow",
     "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
     "Resource": ["arn:aws:s3:::${PG_BUCKET}/*"]}
  ]
}
EOF
# Tidak ada s3:BypassGovernanceRetention / s3:PutBucketObjectLockConfiguration / s3:DeleteObjectVersion:
# kredensial backup yang bocor tidak bisa menghapus versi terkunci (ransomware, SEC-02).

# s3:GetBucketLocation (bucket-level, tanpa akses isi): SDK `minio` (JS di BFF, Python di worker) memanggil
# GetBucketLocation sebelum request pertama bila region klien tidak di-set. minio-py tidak menangani AccessDenied
# pada panggilan ini -> tanpa izin ini GetObject worker gagal (QA gelombang 4).
cat > /tmp/bff-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetBucketLocation"],
     "Resource": ["arn:aws:s3:::${MS_BUCKET}"]},
    {"Effect": "Allow", "Action": ["s3:PutObject", "s3:DeleteObject"],
     "Resource": ["arn:aws:s3:::${MS_BUCKET}/reports/*"]},
    {"Effect": "Allow", "Action": ["s3:GetBucketLocation"],
     "Resource": ["arn:aws:s3:::${CAT_BUCKET}"]},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
     "Resource": ["arn:aws:s3:::${CAT_BUCKET}/*"]}
  ]
}
EOF

cat > /tmp/ocr-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:GetBucketLocation"],
     "Resource": ["arn:aws:s3:::${MS_BUCKET}"]},
    {"Effect": "Allow", "Action": ["s3:GetObject"],
     "Resource": ["arn:aws:s3:::${MS_BUCKET}/reports/*"]}
  ]
}
EOF

mc admin policy create efn pgbackup-rw /tmp/pgbackup-policy.json > /dev/null
mc admin policy create efn efn-bff-market-scout /tmp/bff-policy.json > /dev/null
mc admin policy create efn efn-ocr-market-scout /tmp/ocr-policy.json > /dev/null

# Keluaran `mc admin user add` memuat access key -> dibuang (access key = secret, docs/04 §8.12)
mc admin user add efn "${BACKUP_USER}" "${BACKUP_PW}" > /dev/null
mc admin user add efn "${BFF_USER}" "${BFF_PW}" > /dev/null
mc admin user add efn "${OCR_USER}" "${OCR_PW}" > /dev/null

mc admin policy attach efn pgbackup-rw --user "${BACKUP_USER}" > /dev/null 2>&1 || true
mc admin policy attach efn efn-bff-market-scout --user "${BFF_USER}" > /dev/null 2>&1 || true
mc admin policy attach efn efn-ocr-market-scout --user "${OCR_USER}" > /dev/null 2>&1 || true

# Migrasi ADR-030: user bernama tetap (mudah ditebak) dihapus setelah user access-key-acak siap.
REMOVED=0
for u in $LEGACY_USERS; do
  case " ${BACKUP_USER} ${BFF_USER} ${OCR_USER} ${PROM_USER} " in *" ${u} "*) continue ;; esac
  if mc admin user info efn "$u" > /dev/null 2>&1; then
    mc admin user remove efn "$u" > /dev/null
    REMOVED=$((REMOVED + 1))
  fi
done

# Metrik Prometheus (SEC-20/SEC-30): user khusus dengan aksi admin:Prometheus saja; token JWT HS512 diturunkan
# scripts/derive-secrets.sh dari minio_prometheus_secret (setara `mc admin prometheus generate`). Kegagalan di
# sini TIDAK menggagalkan deploy (hanya metrik MinIO yang hilang -> EfnServiceDown job minio).
# Aksi admin:* TANPA "Resource" (MinIO menolak arn:minio:admin:::* -> "invalid resource", diuji gelombang 7).
cat > /tmp/prom-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["admin:Prometheus"]}
  ]
}
EOF
if mc admin policy create efn efn-prometheus-metrics /tmp/prom-policy.json > /dev/null 2>&1 \
   && mc admin user add efn "${PROM_USER}" "${PROM_PW}" > /dev/null 2>&1; then
  mc admin policy attach efn efn-prometheus-metrics --user "${PROM_USER}" > /dev/null 2>&1 || true
  PROM_STATE="siap"
else
  echo "minio-init: PERINGATAN user metrik gagal dibuat; metrik object storage tidak tersedia" >&2
  PROM_STATE="GAGAL"
fi

rm -f /tmp/*-policy.json /tmp/ilm-*.json
echo "minio-init: TLS OK; bucket ${MS_BUCKET} (SSE-S3, expiry reports/ ${PHOTO_DAYS} hari), ${PG_BUCKET} (SSE-S3, versioning," \
  "lock GOVERNANCE ${LOCK_DAYS} hari), ${CAT_BUCKET} (SSE-S3); user backup/bff/ocr (access key acak) siap; user metrik ${PROM_STATE};" \
  "user lama bernama tetap dihapus: ${REMOVED}"
