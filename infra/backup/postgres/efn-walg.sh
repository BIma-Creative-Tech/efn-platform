#!/bin/bash
# Wrapper WAL-G: menyusun konfigurasi dari env non-rahasia + file Docker secret, lalu exec wal-g.
# Dipanggil oleh archive_command (wal-push), restore_command (wal-fetch), scripts/backup.sh (backup-push, delete)
# dan efn-restore (backup-fetch). Secret dibaca per pemanggilan dan hanya hidup di environment proses wal-g;
# tidak pernah masuk ke environment postmaster atau log.
set -euo pipefail

: "${WALG_S3_PREFIX:=s3://pg-backup/efn}"
: "${WALG_S3_ENDPOINT:=https://s3-gateway:9000}"
: "${WALG_S3_CA_CERT_FILE:=/run/secrets/efn_ca_crt}"
: "${MINIO_BACKUP_ACCESS_KEY_FILE:=/run/secrets/minio_backup_access_key}"
: "${MINIO_BACKUP_PASSWORD_FILE:=/run/secrets/minio_backup_password}"
: "${WALG_LIBSODIUM_KEY_FILE:=/run/secrets/walg_libsodium_key}"

# Enkripsi in transit wajib (docs/04 §7.1): endpoint MinIO harus https dan diverifikasi dengan CA internal.
case "$WALG_S3_ENDPOINT" in
  https://*) ;;
  *) echo "efn-walg: WALG_S3_ENDPOINT harus https:// (dapat: ${WALG_S3_ENDPOINT})" >&2; exit 1 ;;
esac
export WALG_S3_CA_CERT_FILE

for f in "$MINIO_BACKUP_ACCESS_KEY_FILE" "$MINIO_BACKUP_PASSWORD_FILE" "$WALG_LIBSODIUM_KEY_FILE" "$WALG_S3_CA_CERT_FILE"; do
  if [[ ! -r "$f" ]]; then
    echo "efn-walg: secret tidak terbaca: $f" >&2
    exit 1
  fi
done

export WALG_S3_PREFIX
export AWS_ENDPOINT="$WALG_S3_ENDPOINT"
export AWS_S3_FORCE_PATH_STYLE=true
export AWS_REGION="${AWS_REGION:-us-east-1}"
# Access key = secret acak (docs/04 §8.12, ADR-030), bukan nama tetap `pgbackup`
AWS_ACCESS_KEY_ID="$(tr -d "\r\n" < "$MINIO_BACKUP_ACCESS_KEY_FILE")"
export AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY="$(< "$MINIO_BACKUP_PASSWORD_FILE")"
export AWS_SECRET_ACCESS_KEY

# Enkripsi client-side (libsodium). Kunci = 32 byte acak, disimpan base64 di secret walg_libsodium_key.
# KEHILANGAN KUNCI = BACKUP TIDAK BISA DIPULIHKAN. Simpan salinan offline (lihat runbook rotasi secret).
export WALG_LIBSODIUM_KEY_PATH="$WALG_LIBSODIUM_KEY_FILE"
export WALG_LIBSODIUM_KEY_TRANSFORM=base64

export WALG_COMPRESSION_METHOD="${WALG_COMPRESSION_METHOD:-zstd}"
export WALG_UPLOAD_CONCURRENCY="${WALG_UPLOAD_CONCURRENCY:-4}"
export WALG_DOWNLOAD_CONCURRENCY="${WALG_DOWNLOAD_CONCURRENCY:-4}"

# Koneksi lokal lewat unix socket (backup-push butuh koneksi untuk pg_backup_start/stop)
export PGHOST="${PGHOST:-/var/run/postgresql}"
export PGUSER="${PGUSER:-${POSTGRES_USER:-postgres}}"
export PGDATABASE="${PGDATABASE:-${POSTGRES_DB:-postgres}}"

exec /usr/local/bin/wal-g "$@"
