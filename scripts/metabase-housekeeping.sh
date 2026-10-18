#!/usr/bin/env bash
# Housekeeping harian Metabase (W-19, Q-P14; data-AI G18): menonaktifkan akun Metabase yang tidak login > 90 hari
# (`provision.py --housekeeping-only`, default --deactivate-inactive-days 90; OSS tidak punya fitur bawaan).
# Dijalankan systemd timer scripts/host/systemd/efn-metabase-housekeeping.{service,timer} sebagai root di host.
# Memakai file compose yang SAMA dengan deploy terakhir ($COMPOSE_DIR/.efn-compose-files, ditulis deploy.sh) dan
# argumen service metabase-provision dari `docker compose config` (mTLS + CA internal, tanpa egress) - tidak ada
# argumen dari luar. Metrik textfile efn_metabase_housekeeping_* -> alert EfnMetabaseHousekeepingStale.
# Runbook: docs/runbooks/retention.md#metabase-housekeeping.
set -euo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
COMPOSE_DIR="${COMPOSE_DIR:-/opt/efn/compose}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-efn}"
TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/efn/node-exporter}"
[[ "$COMPOSE_PROJECT" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || { echo "COMPOSE_PROJECT tidak valid" >&2; exit 2; }

metric() { # 1|0
  [[ -d "$TEXTFILE_DIR" ]] || return 0
  local tmp; tmp="$(mktemp "$TEXTFILE_DIR/.efn_metabase_housekeeping.XXXXXX")"
  {
    echo "# HELP efn_metabase_housekeeping_last_run_ok Hasil housekeeping Metabase terakhir (1 sukses, 0 gagal)"
    echo "# TYPE efn_metabase_housekeeping_last_run_ok gauge"
    echo "efn_metabase_housekeeping_last_run_ok $1"
    echo "# HELP efn_metabase_housekeeping_last_success_timestamp_seconds Waktu housekeeping Metabase sukses terakhir"
    echo "# TYPE efn_metabase_housekeeping_last_success_timestamp_seconds gauge"
    echo "efn_metabase_housekeeping_last_success_timestamp_seconds $LAST_OK"
  } > "$tmp"
  chmod 0644 "$tmp" && mv -f "$tmp" "$TEXTFILE_DIR/efn_metabase_housekeeping.prom"
}
LAST_OK="$(sed -nE 's/^efn_metabase_housekeeping_last_success_timestamp_seconds ([0-9]+)$/\1/p' \
  "$TEXTFILE_DIR/efn_metabase_housekeeping.prom" 2> /dev/null || true)"
LAST_OK="${LAST_OK:-0}"
on_exit() { local rc=$?; ((rc == 0)) || metric 0; exit "$rc"; }
# shellcheck disable=SC2317  # dipanggil lewat trap
trap on_exit EXIT

[[ -s "$COMPOSE_DIR/.efn-compose-files" ]] || { echo "$COMPOSE_DIR/.efn-compose-files tidak ada (jalankan deploy dulu)" >&2; exit 2; }
DC=(docker compose --project-name "$COMPOSE_PROJECT" --project-directory "$COMPOSE_DIR")
while IFS= read -r f; do
  [[ "$f" =~ ^docker-compose[A-Za-z0-9.-]*\.ya?ml$ && -f "$COMPOSE_DIR/$f" ]] || { echo "file compose tidak valid" >&2; exit 2; }
  DC+=(-f "$COMPOSE_DIR/$f")
done < "$COMPOSE_DIR/.efn-compose-files"

mapfile -t ARGS < <("${DC[@]}" config --format json | jq -r '.services["metabase-provision"].command[]? // empty')
((${#ARGS[@]} > 0)) || { echo "service metabase-provision tidak ada (profil data mati?)" >&2; exit 2; }
echo "housekeeping Metabase: nonaktifkan akun tidak aktif > 90 hari"
"${DC[@]}" run --rm --no-deps -T metabase-provision "${ARGS[@]}" --housekeeping-only
LAST_OK="$(date +%s)"
metric 1
echo "housekeeping Metabase selesai"
