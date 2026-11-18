#!/usr/bin/env bash
# Pembungkus impor ESB -> Odoo (G43 W2b, docs/04 §27.2, runbook docs/runbooks/migrasi-esb-odoo.md §4.2).
# SEC-200: importer jalan di container Odoo SEKALI JALAN (`docker compose run --rm --no-deps`, tanpa port/--service-ports,
# label Traefik dimatikan, tanpa worker HTTP/cron) dengan overlay infra/compose/docker-compose.migration.yml — BUKAN
# `exec` ke service odoo yang melayani pengguna. Kunci bundel /run/secrets/migration_bundle_key, direktori pertukaran
# /mnt/efn-mig dan kode efn_mig /opt/efn-migration datang dari overlay, BUKAN dari argumen. Batas memori = batas service
# `odoo` di compose (mem_limit/deploy.resources; `compose run` tidak punya opsi --memory). Tanpa secret di argumen/log.
# Default = DRY-RUN; run nyata wajib --apply (production/staging juga --confirm-brand).
#
# Pemakaian:
#   scripts/migration/run-import.sh <bundel.efnmig> [--validation validation_<run>.json] [--apply]
#       [--company PB]... [--step master|history|<langkah>]... [--confirm-brand pb] [--allow-older]
#       [--allow-missing-validation]
#   scripts/migration/run-import.sh --totals --company PB --month 2026-09 [--month 2026-10]... --parent-run-id <uuid>
#       [--brand pb]
#   <bundel>/<validation> = nama berkas DI DALAM direktori pertukaran (${MIG_EXCHANGE_DIR} host = /mnt/efn-mig).
# Env opsional: DCM (perintah docker compose lengkap, default stack dev + overlay migration), ODOO_DB (default dari
#   infra/compose/.env atau efn_odoo), ODOO_SERVICE (default odoo), EFN_MIG_MAX_BUNDLE_BYTES (default 1 GiB).
# Exit = exit importer: 0 OK · 1 data · 2 argumen/konfigurasi · 3 integritas · 4 gerbang.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
COMPOSE_DIR="$REPO/infra/compose"
SCRIPT="$HERE/odoo_import_esb.py"

die() { echo "run-import: $*" >&2; exit 2; }

NAME_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$'
bundle="" validation="" dry_run=true confirm="" allow_missing=false allow_older=false totals=false brand="" parent=""
companies=() steps=() months=()
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) dry_run=false ;;
    --dry-run) dry_run=true ;;
    --totals) totals=true ;;
    --validation) shift; validation="${1:-}" ;;
    --company) shift; [[ "${1:-}" =~ ^[A-Z0-9]{2,8}$ ]] || die "--company wajib kode PT (mis. PB)"; companies+=("$1") ;;
    --step) shift; [[ "${1:-}" =~ ^[a-z_-]{2,32}$ ]] || die "--step tidak valid"; steps+=("$1") ;;
    --month) shift; [[ "${1:-}" =~ ^[0-9]{4}-(0[1-9]|1[0-2])$ ]] || die "--month wajib YYYY-MM"; months+=("$1") ;;
    --parent-run-id) shift; [[ "${1:-}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || die "--parent-run-id wajib UUID"; parent="$1" ;;
    --brand) shift; [[ "${1:-}" =~ ^[a-z0-9]{2,16}$ ]] || die "--brand tidak valid"; brand="$1" ;;
    --confirm-brand) shift; [[ "${1:-}" =~ ^[a-z0-9]{2,16}$ ]] || die "--confirm-brand tidak valid"; confirm="$1" ;;
    --allow-older) allow_older=true ;;
    --allow-missing-validation) allow_missing=true ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) die "opsi tidak dikenal: $1" ;;
    *) [ -z "$bundle" ] || die "hanya satu bundel per run"; bundle="$1" ;;
  esac
  shift
done

json_list() {  # elemen sudah divalidasi regex (tanpa kutip/backslash)
  local out="" x
  for x in "$@"; do out="${out:+$out,}\"$x\""; done
  printf '[%s]' "$out"
}

if [ "$totals" = true ]; then
  [ -z "$bundle" ] || die "--totals tidak memakai bundel"
  if [ ${#companies[@]} -eq 0 ] || [ ${#months[@]} -eq 0 ] || [ -z "$parent" ]; then
    die "--totals wajib --company, --month, dan --parent-run-id"
  fi
  argv="{\"steps\":[\"totals\"],\"companies\":$(json_list "${companies[@]}"),\"months\":$(json_list "${months[@]}")"
  argv="$argv,\"parent_run_id\":\"$parent\""
  if [ -n "$brand" ]; then argv="$argv,\"brand\":\"$brand\""; fi
  argv="$argv}"
  label="TOTALS"
else
  [ -n "$bundle" ] || die "bundel wajib (nama berkas di direktori pertukaran)"
  bundle="$(basename -- "$bundle")"
  [[ "$bundle" =~ $NAME_RE ]] || die "nama bundel tidak valid"
  if [ -n "$validation" ]; then
    validation="$(basename -- "$validation")"
    [[ "$validation" =~ $NAME_RE ]] || die "nama berkas validasi tidak valid"
  fi
  argv="{\"bundle\":\"$bundle\",\"dry_run\":$dry_run,\"allow_missing_validation\":$allow_missing"
  argv="$argv,\"allow_older\":$allow_older"
  if [ -n "$validation" ]; then argv="$argv,\"validation\":\"$validation\""; fi
  if [ ${#companies[@]} -gt 0 ]; then argv="$argv,\"companies\":$(json_list "${companies[@]}")"; fi
  if [ ${#steps[@]} -gt 0 ]; then argv="$argv,\"steps\":$(json_list "${steps[@]}")"; fi
  if [ -n "$confirm" ]; then argv="$argv,\"confirm_brand\":\"$confirm\""; fi
  argv="$argv}"
  mode="RUN-NYATA"
  if [ "$dry_run" = true ]; then mode="DRY-RUN"; fi
  label="bundel=$bundle $mode"
fi

if [ -z "${ODOO_DB:-}" ] && [ -f "$COMPOSE_DIR/.env" ]; then
  ODOO_DB="$(grep -E '^ODOO_DB=' "$COMPOSE_DIR/.env" | cut -d= -f2 | tr -d '\r' || true)"
fi
ODOO_DB="${ODOO_DB:-efn_odoo}"
[[ "$ODOO_DB" =~ ^[A-Za-z0-9_]{1,63}$ ]] || die "ODOO_DB tidak valid"
SERVICE="${ODOO_SERVICE:-odoo}"
MAX_BYTES="${EFN_MIG_MAX_BUNDLE_BYTES:-1073741824}"
[[ "$MAX_BYTES" =~ ^[0-9]{1,13}$ ]] || die "EFN_MIG_MAX_BUNDLE_BYTES tidak valid"
DCM="${DCM:-docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.migration.yml --profile core}"

echo "run-import: $label db=$ODOO_DB (container sekali jalan)" >&2
cd "$COMPOSE_DIR"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
set +e
# shellcheck disable=SC2086  # DCM sengaja dipecah per kata (perintah compose lengkap)
$DCM run --rm --no-deps -T \
  --label traefik.enable=false \
  -e ODOO_WORKERS=0 -e ODOO_MAX_CRON_THREADS=0 \
  -e EFN_MIG_MAX_BUNDLE_BYTES="$MAX_BYTES" \
  -e EFN_MIG_ARGV="$argv" \
  "$SERVICE" odoo shell -d "$ODOO_DB" --no-http --max-cron-threads=0 --log-level=warn < "$SCRIPT" > "$out"
rc=$?
set -e
result="$(grep -E '^EFN_RESULT ' "$out" | tail -n 1 || true)"
if [ -z "$result" ]; then
  echo "run-import: odoo shell gagal tanpa EFN_RESULT (exit $rc); periksa log container" >&2
  tail -n 20 "$out" >&2 || true
  exit $(( rc == 0 ? 1 : rc ))
fi
printf '%s\n' "${result#EFN_RESULT }"
exit "$rc"
