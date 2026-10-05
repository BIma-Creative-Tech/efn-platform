#!/bin/bash
# Entrypoint EFN untuk Odoo CE 19 (menggantikan /entrypoint.sh resmi).
#
# Kenapa tidak mendelegasikan ke /entrypoint.sh resmi lagi:
#  - entrypoint resmi meneruskan password DB sebagai argumen CLI `--db_password`
#    (terlihat di `ps`/proc cmdline) dan punya fallback diam-diam PASSWORD='odoo';
#  - tidak mengenal db_sslmode / CA.
# Semua nilai (termasuk secret) ditulis ke odoo.conf mode 0600 oleh render_odoo_conf.py
# (Python + configparser, tanpa sed; lihat docstring skrip itu untuk alasan escaping, SEC-15).
#
# Env (lihat services/odoo/addons/efn_core/README.md bagian "Container"):
#   HOST (postgres) PORT (5432) DB_USER|USER (efn_odoo) PASSWORD_FILE (wajib)
#   ODOO_ADMIN_PASSWORD_FILE (wajib)  EFN_CA_FILE (/run/secrets/efn_ca_crt)
#   PGSSLROOTCERT (default = EFN_CA_FILE)  ODOO_HTTP_INTERFACE (127.0.0.1)
#   ODOO_WORKERS (2) ODOO_MAX_CRON_THREADS (1) ODOO_DEV_MODE (dilarang di produksi)
#   EFN_ALLOW_PLAINTEXT + APP_ENV (hanya non-produksi) ODOO_DB_WAIT_TIMEOUT (30)
set -euo pipefail

: "${EFN_CA_FILE:=/run/secrets/efn_ca_crt}"
: "${PGSSLROOTCERT:=$EFN_CA_FILE}"
: "${ODOO_RC:=/etc/odoo/odoo.conf}"
: "${ODOO_DEV_MODE:=}"
# libpq (dipakai Odoo) membaca PGSSLROOTCERT dari env; efn.bff.client membaca EFN_CA_FILE.
export EFN_CA_FILE PGSSLROOTCERT ODOO_RC

app_env="$(printf '%s' "${APP_ENV:-}" | tr '[:upper:]' '[:lower:]')"
if [[ -n "$ODOO_DEV_MODE" && "$app_env" == "production" ]]; then
  echo "[efn-entrypoint] FATAL: ODOO_DEV_MODE dilarang saat APP_ENV=production" >&2
  exit 1
fi

# Gagal start (exit != 0) bila secret/CA hilang atau konfigurasi tidak aman. Tidak ada fallback.
python3 /usr/local/lib/efn/render_odoo_conf.py --template /etc/odoo/odoo.conf.tpl --output "$ODOO_RC"

EXTRA_ARGS=()
if [[ -n "$ODOO_DEV_MODE" ]]; then
  EXTRA_ARGS+=(--dev="$ODOO_DEV_MODE")
fi

case "${1:-odoo}" in
  odoo | --)
    [[ $# -gt 0 ]] && shift
    # Odoo 19 membaca path config dari env ODOO_RC.
    exec odoo "$@" "${EXTRA_ARGS[@]}"
    ;;
  -*)
    exec odoo "$@" "${EXTRA_ARGS[@]}"
    ;;
  *)
    exec "$@"
    ;;
esac
