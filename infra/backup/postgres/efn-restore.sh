#!/bin/bash
# Entrypoint container restore SEMENTARA (dipakai oleh scripts/restore-test.sh dan prosedur DR).
# 1) backup-fetch base backup ke PGDATA kosong, 2) replay WAL dari MinIO (restore_command),
# 3) promote di akhir arsip WAL, 4) postgres tetap berjalan agar bisa diverifikasi.
# archive_mode=off WAJIB: instance hasil restore tidak boleh menulis WAL ke repo backup produksi.
set -euo pipefail

BACKUP_NAME="${1:-LATEST}"
PGDATA="${PGDATA:-/var/lib/postgresql/data/pgdata}"
export PGDATA

if [[ -s "${PGDATA}/PG_VERSION" ]]; then
  echo "efn-restore: ${PGDATA} tidak kosong; restore hanya ke volume baru" >&2
  exit 1
fi

mkdir -p "$PGDATA"
chmod 700 "$PGDATA"

echo "efn-restore: backup-fetch ${BACKUP_NAME}"
/usr/local/bin/efn-walg backup-fetch "$PGDATA" "$BACKUP_NAME"

touch "${PGDATA}/recovery.signal"

echo "efn-restore: start recovery (replay WAL hingga akhir arsip, lalu promote)"
exec postgres -D "$PGDATA" \
  -c listen_addresses= \
  -c archive_mode=off \
  -c "restore_command=/usr/local/bin/efn-walg wal-fetch %f %p" \
  -c recovery_target_timeline=latest \
  -c recovery_target_action=promote \
  -c hot_standby=off \
  -c max_connections="${RESTORE_MAX_CONNECTIONS:-300}" \
  -c shared_buffers="${RESTORE_SHARED_BUFFERS:-256MB}" \
  -c shared_preload_libraries=pg_stat_statements
