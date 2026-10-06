#!/usr/bin/env bash
# Restore test (R-T08): memulihkan base backup TERAKHIR + replay WAL dari MinIO ke container PostgreSQL
# SEMENTARA (volume baru, archive_mode=off, tidak listen TCP), lalu menjalankan query verifikasi.
# Keluar non-zero bila restore atau verifikasi gagal. Container & volume sementara selalu dihapus.
# Dijalankan DI HOST oleh systemd timer infra/backup/systemd/efn-restore-test.timer (bulanan) atau manual.
#
# Env (opsional):
#   COMPOSE_PROJECT=efn                 project compose (untuk menemukan container postgres hidup & network)
#   SECRETS_DIR=/opt/efn/secrets        lokasi file secret (dev: ./secrets)
#   BACKUP_NAME=LATEST                  nama backup WAL-G (atau LATEST)
#   RESTORE_TIMEOUT=10800               batas waktu recovery (detik); RTO target 4 jam
#   MAX_BACKUP_AGE_HOURS=26             base backup terbaru harus lebih muda dari ini
#   REQUIRED_DBS="efn_platform metabase"  database yang wajib ada hasil restore (selain yang ada di instance hidup)
#   ODOO_DB=efn_odoo                    bila ada: wajib >= MIN_ODOO_TABLES tabel dan res_company >= 1
#   MIN_ODOO_TABLES=100  MIN_PLATFORM_TABLES=2
#   AMCHECK=0|1                         jalankan pg_amcheck (lambat untuk DB besar)
#   TEXTFILE_DIR=/var/lib/efn/node-exporter
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:-efn}"
SECRETS_DIR="${SECRETS_DIR:-/opt/efn/secrets}"
BACKUP_NAME="${BACKUP_NAME:-LATEST}"
RESTORE_TIMEOUT="${RESTORE_TIMEOUT:-10800}"
MAX_BACKUP_AGE_HOURS="${MAX_BACKUP_AGE_HOURS:-26}"
REQUIRED_DBS="${REQUIRED_DBS:-efn_platform metabase}"
ODOO_DB="${ODOO_DB:-efn_odoo}"
MIN_ODOO_TABLES="${MIN_ODOO_TABLES:-100}"
MIN_PLATFORM_TABLES="${MIN_PLATFORM_TABLES:-2}"
AMCHECK="${AMCHECK:-0}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/efn/node-exporter}"

RUN_ID="$(date +%Y%m%d%H%M%S)"
NAME="efn-restore-test-${RUN_ID}"
VOLUME="${NAME}-data"
START_TS=$(date +%s)
FAILURES=0

log()  { printf '%s restore-test: %s\n' "$(date -Is)" "$*"; }
fail() { log "GAGAL: $*"; FAILURES=$((FAILURES + 1)); }

write_metrics() {
  local status="$1" now tmp
  now=$(date +%s)
  mkdir -p "$TEXTFILE_DIR"
  tmp=$(mktemp "${TEXTFILE_DIR}/.efn_restore.XXXXXX")
  {
    echo "# HELP efn_restore_test_last_status Status restore test terakhir (1=sukses, 0=gagal)."
    echo "# TYPE efn_restore_test_last_status gauge"
    echo "efn_restore_test_last_status ${status}"
    echo "# HELP efn_restore_test_duration_seconds Durasi restore test terakhir (indikator RTO)."
    echo "# TYPE efn_restore_test_duration_seconds gauge"
    echo "efn_restore_test_duration_seconds $((now - START_TS))"
    echo "# HELP efn_restore_test_last_success_timestamp_seconds Waktu restore test sukses terakhir."
    echo "# TYPE efn_restore_test_last_success_timestamp_seconds gauge"
    if [[ "$status" == "1" ]]; then
      echo "efn_restore_test_last_success_timestamp_seconds ${now}"
    elif [[ -f "${TEXTFILE_DIR}/efn_restore_test.prom" ]]; then
      grep '^efn_restore_test_last_success_timestamp_seconds' "${TEXTFILE_DIR}/efn_restore_test.prom" || true
    fi
  } > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "${TEXTFILE_DIR}/efn_restore_test.prom"
}

cleanup() {
  local rc=$?
  if [[ $rc -ne 0 ]] && docker inspect "$NAME" > /dev/null 2>&1; then
    log "log container sementara (50 baris terakhir):"
    docker logs --tail 50 "$NAME" 2>&1 || true
  fi
  docker rm -f "$NAME" > /dev/null 2>&1 || true
  docker volume rm -f "$VOLUME" > /dev/null 2>&1 || true
  if [[ $rc -eq 0 ]]; then write_metrics 1 || true; else write_metrics 0 || true; fi
  log "selesai dalam $(( $(date +%s) - START_TS )) detik (exit $rc)"
}
trap cleanup EXIT

command -v jq > /dev/null || { log "jq dibutuhkan di host"; exit 1; }

# ── 1. Temukan instance hidup: image, env WAL-G, network ──────────────────────────────
LIVE=$(docker ps -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT}" \
  --filter "label=com.docker.compose.service=postgres" | head -n1)
[[ -n "$LIVE" ]] || { log "container postgres project ${COMPOSE_PROJECT} tidak ditemukan"; exit 1; }

IMAGE=$(docker inspect --format '{{.Config.Image}}' "$LIVE")
NETWORK="${COMPOSE_PROJECT}_data"
PG_USER=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$LIVE" | sed -n 's/^POSTGRES_USER=//p')
PG_USER="${PG_USER:-postgres}"

ENV_ARGS=()
while IFS= read -r kv; do
  case "$kv" in
    WALG_S3_PREFIX=*|WALG_S3_ENDPOINT=*|POSTGRES_USER=*|POSTGRES_DB=*|PGDATA=*)
      ENV_ARGS+=(-e "$kv") ;;
  esac
done < <(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$LIVE")

# MinIO hanya HTTPS (docs/04 §7.1): container restore memverifikasi sertifikat dengan CA internal
for s in minio_backup_access_key minio_backup_password walg_libsodium_key efn_ca_crt; do
  [[ -r "${SECRETS_DIR}/${s}.txt" ]] || { log "secret ${SECRETS_DIR}/${s}.txt tidak terbaca"; exit 1; }
done

# ── 2. Cek umur base backup terbaru ───────────────────────────────────────────────────
LATEST_TIME=$(docker exec "$LIVE" /usr/local/bin/efn-walg backup-list --json \
  | jq -r 'if length == 0 then empty else (max_by(.time) | .time) end')
[[ -n "$LATEST_TIME" ]] || { log "tidak ada base backup di repo WAL-G"; exit 1; }
AGE_H=$(( ( $(date +%s) - $(date -d "$LATEST_TIME" +%s) ) / 3600 ))
log "base backup terbaru: ${LATEST_TIME} (${AGE_H} jam lalu)"
if (( AGE_H > MAX_BACKUP_AGE_HOURS )); then
  fail "base backup terbaru lebih tua dari ${MAX_BACKUP_AGE_HOURS} jam"
fi

# Paksa segmen WAL terkini ter-archive agar restore mencakup data sampai sekarang
docker exec "$LIVE" psql -U "$PG_USER" -d postgres -Atqc "SELECT pg_switch_wal();" > /dev/null || true

# ── 3. Jalankan container restore sementara ───────────────────────────────────────────
log "membuat volume ${VOLUME} dan container ${NAME} dari image ${IMAGE}"
docker volume create "$VOLUME" > /dev/null
docker run -d --name "$NAME" \
  --network "$NETWORK" \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --memory "${RESTORE_MEMORY:-4g}" \
  --shm-size 256m \
  "${ENV_ARGS[@]}" \
  -e MINIO_BACKUP_ACCESS_KEY_FILE=/run/secrets/minio_backup_access_key \
  -e MINIO_BACKUP_PASSWORD_FILE=/run/secrets/minio_backup_password \
  -e WALG_LIBSODIUM_KEY_FILE=/run/secrets/walg_libsodium_key \
  -e WALG_S3_CA_CERT_FILE=/run/secrets/efn_ca_crt \
  -v "${VOLUME}:/var/lib/postgresql/data" \
  -v "${SECRETS_DIR}/minio_backup_access_key.txt:/run/secrets/minio_backup_access_key:ro" \
  -v "${SECRETS_DIR}/minio_backup_password.txt:/run/secrets/minio_backup_password:ro" \
  -v "${SECRETS_DIR}/walg_libsodium_key.txt:/run/secrets/walg_libsodium_key:ro" \
  -v "${SECRETS_DIR}/efn_ca_crt.txt:/run/secrets/efn_ca_crt:ro" \
  --label "efn.purpose=restore-test" \
  --entrypoint /usr/local/bin/efn-restore \
  "$IMAGE" "$BACKUP_NAME" > /dev/null

# ── 4. Tunggu recovery selesai & promote ──────────────────────────────────────────────
log "menunggu recovery (timeout ${RESTORE_TIMEOUT} s)"
deadline=$(( $(date +%s) + RESTORE_TIMEOUT ))
while :; do
  running=$(docker inspect --format '{{.State.Running}}' "$NAME" 2> /dev/null || echo false)
  [[ "$running" == "true" ]] || { log "container restore berhenti sebelum recovery selesai"; exit 1; }
  state=$(docker exec "$NAME" psql -U "$PG_USER" -d postgres -Atqc "SELECT pg_is_in_recovery();" 2> /dev/null || true)
  if [[ "$state" == "f" ]]; then break; fi
  if (( $(date +%s) > deadline )); then log "recovery melebihi ${RESTORE_TIMEOUT} s (RTO terancam)"; exit 1; fi
  sleep 10
done
log "recovery selesai dan instance ter-promote dalam $(( $(date +%s) - START_TS )) detik"

# ── 5. Verifikasi ──────────────────────────────────────────────────────────────────────
q() { docker exec "$NAME" psql -U "$PG_USER" -v ON_ERROR_STOP=1 -Atq -d "$1" -c "$2"; }

RESTORED_DBS=$(q postgres "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1;")
LIVE_DBS=$(docker exec "$LIVE" psql -U "$PG_USER" -Atq -d postgres -c "SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY 1;")
log "database hasil restore: $(echo "$RESTORED_DBS" | tr '\n' ' ')"

for db in $REQUIRED_DBS $LIVE_DBS; do
  grep -qx "$db" <<< "$RESTORED_DBS" || fail "database ${db} tidak ada di hasil restore"
done

# Jumlah tabel per database (non-sistem)
while IFS= read -r db; do
  [[ -z "$db" ]] && continue
  n=$(q "$db" "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type='BASE TABLE';") \
    || { fail "query tabel di ${db} error"; continue; }
  log "  ${db}: ${n} tabel"
done <<< "$RESTORED_DBS"

# Platform (BFF): skema & tabel kunci
if grep -qx "efn_platform" <<< "$RESTORED_DBS"; then
  n=$(q efn_platform "SELECT count(*) FROM information_schema.tables WHERE table_schema='platform';")
  (( n >= MIN_PLATFORM_TABLES )) || fail "efn_platform.platform hanya ${n} tabel (< ${MIN_PLATFORM_TABLES})"
  for t in platform.price_hunter_report platform.otp_audit platform.loyalty_ledger platform.processed_message platform.outbox; do
    exists=$(q efn_platform "SELECT to_regclass('${t}') IS NOT NULL;")
    if [[ "$exists" == "t" ]]; then
      log "  efn_platform ${t}: $(q efn_platform "SELECT count(*) FROM ${t};") baris"
    else
      log "  efn_platform ${t}: (belum ada)"
    fi
  done
fi

# Odoo: tabel inti dan minimal satu company
if grep -qx "$ODOO_DB" <<< "$RESTORED_DBS"; then
  n=$(q "$ODOO_DB" "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
  (( n >= MIN_ODOO_TABLES )) || fail "${ODOO_DB} hanya ${n} tabel (< ${MIN_ODOO_TABLES})"
  companies=$(q "$ODOO_DB" "SELECT count(*) FROM res_company;") || companies=0
  (( companies >= 1 )) || fail "${ODOO_DB}.res_company kosong"
  for t in res_company res_partner product_product pos_order account_move stock_move; do
    exists=$(q "$ODOO_DB" "SELECT to_regclass('public.${t}') IS NOT NULL;")
    if [[ "$exists" == "t" ]]; then
      log "  ${ODOO_DB} ${t}: $(q "$ODOO_DB" "SELECT count(*) FROM ${t};") baris"
    fi
  done
fi

# Opsional: pemeriksaan korupsi indeks/heap (instance sementara, aman memasang extension)
if [[ "$AMCHECK" == "1" ]]; then
  log "pg_amcheck --all"
  docker exec "$NAME" pg_amcheck -U "$PG_USER" --all --install-missing --heapallindexed \
    || fail "pg_amcheck menemukan masalah"
fi

if (( FAILURES > 0 )); then
  log "${FAILURES} verifikasi gagal"
  exit 1
fi
log "OK: backup ${BACKUP_NAME} terpulihkan dan terverifikasi"
