#!/usr/bin/env bash
# Mengambil modul OCA yang dibutuhkan ke direktori target (default services/odoo/oca) tanpa men-commit kode OCA ke repo.
# Versi: 19.0; bila branch belum ada (R-T04), skrip mencoba 18.0 dan mencetak PERINGATAN (modul perlu porting).
set -euo pipefail
TARGET="${1:-services/odoo/oca}"
VERSION="${OCA_VERSION:-19.0}"
FALLBACK="18.0"

# repo => modul yang dipakai (hanya ini yang disalin, agar image kecil & lisensi jelas).
# Sumber daftar: docs/06_ODOO_CE19_MODULE_AUDIT.md §2 (terverifikasi ada di branch 19.0).
# JANGAN fetch OCA/l10n-indonesia (branch 19.0 kosong); stock_picking_batch adalah modul CE, bukan OCA.
declare -A MODULES=(
  ["multi-company"]="purchase_sale_inter_company account_invoice_inter_company"
  ["server-tools"]="auditlog"
  ["mis-builder"]="mis_builder"
  ["reporting-engine"]="report_xlsx"   # dependensi mis_builder
  ["server-ux"]="date_range"           # dependensi mis_builder
)

mkdir -p "$TARGET"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
MISSING=0
for repo in "${!MODULES[@]}"; do
  url="https://github.com/OCA/${repo}.git"
  if git ls-remote --exit-code --heads "$url" "$VERSION" > /dev/null 2>&1; then
    branch="$VERSION"
  else
    echo "PERINGATAN: OCA/${repo} belum punya branch ${VERSION}; memakai ${FALLBACK} (perlu porting)"
    branch="$FALLBACK"
  fi
  git clone --depth 1 --branch "$branch" "$url" "$TMP/$repo"
  # shellcheck disable=SC2086  # daftar modul dipisah spasi, sengaja di-split
  for m in ${MODULES[$repo]}; do
    if [[ -d "$TMP/$repo/$m" ]]; then
      rm -rf "${TARGET:?}/$m" && cp -r "$TMP/$repo/$m" "$TARGET/$m"
      echo "ok  $repo/$m ($branch)"
    else
      echo "MISSING $repo/$m di branch $branch"
      MISSING=$((MISSING + 1))
    fi
  done
done
# Modul hilang = image Odoo tidak bisa meng-install dependensi -> gagal lebih awal (build/CI)
if (( MISSING > 0 )); then
  echo "GAGAL: ${MISSING} modul OCA tidak ditemukan" >&2
  exit 1
fi
