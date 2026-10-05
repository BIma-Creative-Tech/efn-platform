#!/usr/bin/env bash
# Base backup harian PostgreSQL dengan WAL-G ke MinIO (bucket pg-backup), dijalankan DI HOST oleh systemd timer
# infra/backup/systemd/efn-backup.timer (lihat docs/runbooks/backup-restore.md).
#
# Lapisan backup (R-T08, RPO 15 menit / RTO 4 jam):
#   1. WAL archiving kontinu (archive_command -> efn-walg wal-push, archive_timeout=900)  -> RPO <= 15 menit
#   2. Base backup harian (skrip ini: efn-walg backup-push) + retensi N base backup        -> batas replay WAL
#   3. (opsional, LOGICAL_DUMP=1) pg_dumpall terenkripsi age -> MinIO, untuk migrasi lintas versi/arsitektur
#
# Env (semua opsional):
#   COMPOSE_PROJECT=efn            nama project compose (label container)
#   RETAIN_FULL=14                 jumlah base backup yang dipertahankan (WAL lebih lama ikut dihapus)
#   TEXTFILE_DIR=/var/lib/efn/node-exporter   direktori textfile collector node-exporter
#   LOGICAL_DUMP=0|1, AGE_RECIPIENT, MINIO_ALIAS, LOGICAL_BUCKET   untuk lapisan 3 (butuh zstd, age, mc di host)
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-efn}"
RETAIN_FULL="${RETAIN_FULL:-14}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/efn/node-exporter}"
LOGICAL_DUMP="${LOGICAL_DUMP:-0}"
START_TS=$(date +%s)
STATUS=0

log() { printf '%s backup: %s\n' "$(date -Is)" "$*"; }

write_metrics() {
  local status="$1" now duration tmp
  now=$(date +%s)
  duration=$((now - START_TS))
  mkdir -p "$TEXTFILE_DIR"
  tmp=$(mktemp "${TEXTFILE_DIR}/.efn_backup.XXXXXX")
  {
    echo "# HELP efn_backup_last_status Status backup terakhir (1=sukses, 0=gagal)."
    echo "# TYPE efn_backup_last_status gauge"
    echo "efn_backup_last_status{type=\"base\"} ${status}"
    echo "# HELP efn_backup_last_run_timestamp_seconds Waktu selesai backup terakhir (sukses/gagal)."
    echo "# TYPE efn_backup_last_run_timestamp_seconds gauge"
    echo "efn_backup_last_run_timestamp_seconds{type=\"base\"} ${now}"
    echo "# HELP efn_backup_last_duration_seconds Durasi backup terakhir."
    echo "# TYPE efn_backup_last_duration_seconds gauge"
    echo "efn_backup_last_duration_seconds{type=\"base\"} ${duration}"
    echo "# HELP efn_backup_last_success_timestamp_seconds Waktu backup sukses terakhir."
    echo "# TYPE efn_backup_last_success_timestamp_seconds gauge"
    if [[ "$status" == "1" ]]; then
      echo "efn_backup_last_success_timestamp_seconds{type=\"base\"} ${now}"
    elif [[ -f "${TEXTFILE_DIR}/efn_backup.prom" ]]; then
      # pertahankan timestamp sukses sebelumnya agar EfnBackupStale tetap akurat
      grep '^efn_backup_last_success_timestamp_seconds' "${TEXTFILE_DIR}/efn_backup.prom" || true
    fi
  } > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "${TEXTFILE_DIR}/efn_backup.prom"
}

on_exit() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then STATUS=0; else STATUS=1; fi
  write_metrics "$STATUS" || log "PERINGATAN: gagal menulis metrik"
  if [[ $rc -ne 0 ]]; then log "GAGAL (exit $rc)"; fi
}
trap on_exit EXIT

PG_CONTAINER=$(docker ps -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
  --filter "label=com.docker.compose.service=postgres" | head -n1)
[[ -n "$PG_CONTAINER" ]] || { log "container postgres project ${COMPOSE_PROJECT} tidak ditemukan"; exit 1; }

log "base backup (WAL-G backup-push) dari container ${PG_CONTAINER}"
# shellcheck disable=SC2016  # $PGDATA sengaja diekspansi di dalam container
docker exec "$PG_CONTAINER" bash -c 'exec /usr/local/bin/efn-walg backup-push "$PGDATA"'

log "retensi: simpan ${RETAIN_FULL} base backup terakhir"
docker exec "$PG_CONTAINER" /usr/local/bin/efn-walg delete retain FULL "$RETAIN_FULL" --confirm

log "daftar backup terbaru"
docker exec "$PG_CONTAINER" /usr/local/bin/efn-walg backup-list | tail -n 3

if [[ "$LOGICAL_DUMP" == "1" ]]; then
  : "${AGE_RECIPIENT:?AGE_RECIPIENT wajib untuk LOGICAL_DUMP=1}"
  : "${MINIO_ALIAS:?MINIO_ALIAS wajib untuk LOGICAL_DUMP=1}"
  LOGICAL_BUCKET="${LOGICAL_BUCKET:-pg-backup}"
  STAMP=$(date +%Y%m%d-%H%M%S)
  OUT=$(mktemp --suffix=.sql.zst.age "/tmp/efn-pg-${STAMP}.XXXXXX")
  log "dump logis terenkripsi (age) ${STAMP}"
  # shellcheck disable=SC2016  # $POSTGRES_USER sengaja diekspansi di dalam container
  docker exec "$PG_CONTAINER" bash -c 'exec pg_dumpall -U "$POSTGRES_USER" --no-role-passwords' \
    | zstd -T0 -19 \
    | age -r "$AGE_RECIPIENT" -o "$OUT"
  mc cp "$OUT" "${MINIO_ALIAS}/${LOGICAL_BUCKET}/logical/${STAMP}.sql.zst.age"
  rm -f "$OUT"
  mc find "${MINIO_ALIAS}/${LOGICAL_BUCKET}/logical/" --older-than 30d --exec "mc rm {}" || true
fi

log "selesai"
# Uji restore bulanan WAJIB (R-T08): scripts/restore-test.sh (timer efn-restore-test.timer).
