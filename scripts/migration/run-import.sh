#!/usr/bin/env bash
# Pembungkus impor ESB -> Odoo (G43 W2b, docs/04 §27.2, runbook docs/runbooks/migrasi-esb-odoo.md §4.2).
# Menjalankan scripts/migration/odoo_import_esb.py di container `odoo` lewat `odoo shell` dengan overlay
# infra/compose/docker-compose.migration.yml (kunci bundel /run/secrets/migration_bundle_key, direktori pertukaran
# /mnt/efn-mig, kode efn_mig /opt/efn-migration — semuanya dari overlay, BUKAN dari argumen). Tanpa password/secret di
# argumen atau log. Default = DRY-RUN; run nyata wajib --apply (production juga --confirm-brand).
#
# Pemakaian (dari root repo atau mana pun):
#   scripts/migration/run-import.sh <bundel.efnmig> [--validation validation_<run>.json] [--apply]
#       [--company PB]... [--step master|history|<langkah>]... [--confirm-brand pb] [--allow-missing-validation]
#   <bundel>/<validation> = nama berkas DI DALAM direktori pertukaran (${MIG_EXCHANGE_DIR} host = /mnt/efn-mig).
# Env opsional: DCM (perintah docker compose lengkap, default stack dev + overlay migration), ODOO_DB (default dari
#   infra/compose/.env atau efn_odoo), ODOO_SERVICE (default odoo).
# Exit = exit importer: 0 OK · 1 data · 2 argumen/konfigurasi · 3 integritas · 4 gerbang.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
COMPOSE_DIR="$REPO/infra/compose"
SCRIPT="$HERE/odoo_import_esb.py"

die() { echo "run-import: $*" >&2; exit 2; }

NAME_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$'
bundle="" validation="" dry_run=true confirm="" allow_missing=false
companies=() steps=()
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) dry_run=false ;;
    --dry-run) dry_run=true ;;
    --validation) shift; validation="${1:-}" ;;
    --company) shift; [[ "${1:-}" =~ ^[A-Z0-9]{2,8}$ ]] || die "--company wajib kode PT (mis. PB)"; companies+=("$1") ;;
    --step) shift; [[ "${1:-}" =~ ^[a-z_-]{2,32}$ ]] || die "--step tidak valid"; steps+=("$1") ;;
    --confirm-brand) shift; [[ "${1:-}" =~ ^[a-z0-9]{2,16}$ ]] || die "--confirm-brand tidak valid"; confirm="$1" ;;
    --allow-missing-validation) allow_missing=true ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    -*) die "opsi tidak dikenal: $1" ;;
    *) [ -z "$bundle" ] || die "hanya satu bundel per run"; bundle="$1" ;;
  esac
  shift
done
[ -n "$bundle" ] || die "bundel wajib (nama berkas di direktori pertukaran)"
bundle="$(basename -- "$bundle")"
[[ "$bundle" =~ $NAME_RE ]] || die "nama bundel tidak valid"
if [ -n "$validation" ]; then
  validation="$(basename -- "$validation")"
  [[ "$validation" =~ $NAME_RE ]] || die "nama berkas validasi tidak valid"
fi

json_list() {  # elemen sudah divalidasi regex (tanpa kutip/backslash)
  local out="" x
  for x in "$@"; do out="${out:+$out,}\"$x\""; done
  printf '[%s]' "$out"
}
argv="{\"bundle\":\"$bundle\",\"dry_run\":$dry_run,\"allow_missing_validation\":$allow_missing"
[ -n "$validation" ] && argv="$argv,\"validation\":\"$validation\""
[ ${#companies[@]} -gt 0 ] && argv="$argv,\"companies\":$(json_list "${companies[@]}")"
[ ${#steps[@]} -gt 0 ] && argv="$argv,\"steps\":$(json_list "${steps[@]}")"
[ -n "$confirm" ] && argv="$argv,\"confirm_brand\":\"$confirm\""
argv="$argv}"

if [ -z "${ODOO_DB:-}" ] && [ -f "$COMPOSE_DIR/.env" ]; then
  ODOO_DB="$(grep -E '^ODOO_DB=' "$COMPOSE_DIR/.env" | cut -d= -f2 | tr -d '\r' || true)"
fi
ODOO_DB="${ODOO_DB:-efn_odoo}"
[[ "$ODOO_DB" =~ ^[A-Za-z0-9_]{1,63}$ ]] || die "ODOO_DB tidak valid"
SERVICE="${ODOO_SERVICE:-odoo}"
DCM="${DCM:-docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.migration.yml --profile core}"

echo "run-import: bundel=$bundle db=$ODOO_DB $([ "$dry_run" = true ] && echo DRY-RUN || echo RUN-NYATA)" >&2
cd "$COMPOSE_DIR"
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
set +e
# shellcheck disable=SC2086  # DCM sengaja dipecah per kata (perintah compose lengkap)
$DCM exec -T -e EFN_MIG_ARGV="$argv" "$SERVICE" \
  odoo shell -d "$ODOO_DB" --no-http --log-level=warn < "$SCRIPT" > "$out"
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
