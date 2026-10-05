#!/usr/bin/env bash
# Role login per service (docs/04 §7.3, SEC-02). Pemilik: efn-devsecops-engineer.
# HANYA membuat/memperbarui role + password (dari /run/secrets/pg_password_*) + atribut role + kepemilikan DB
# `metabase` + keanggotaan efn_etl -> efn_readonly. TIDAK ada grant tabel/skema: itu ditulis pemilik skema
# (BFF di 01-*/02-*.sql).
#
# Dijalankan:
#  1. otomatis oleh entrypoint image postgres saat volume kosong (urutan 00 -> sebelum 01-*.sql; pada saat itu
#     role efn_readonly & DB metabase belum ada, langkah terkait dilewati), dan
#  2. pada setiap deploy oleh scripts/deploy.sh (`docker compose exec -T postgres bash /docker-entrypoint-initdb.d/00-roles.sh`)
#     -> idempoten, sekaligus menerapkan rotasi password (ALTER ROLE ... PASSWORD) dan langkah yang tadi dilewati.
#
# Entrypoint resmi men-*source* file .sh yang tidak executable: skrip ini tidak memanggil `exit` dan semua logika
# ada di dalam fungsi.

efn_roles_main() {
  local secrets_dir="${EFN_SECRETS_DIR:-/run/secrets}"
  local su="${POSTGRES_USER:-postgres}"
  local role secret pw attrs
  # role:secret:atribut
  local -a roles=(
    "efn_odoo:pg_password_odoo:LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION"
    "efn_bff:pg_password_bff:LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    "efn_ocr:pg_password_ocr:LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    "efn_etl:pg_password_etl:LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    "efn_metabase:pg_password_metabase:LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
    "efn_exporter:pg_password_exporter:LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
  )

  for entry in "${roles[@]}"; do
    role="${entry%%:*}"
    secret="${entry#*:}"; secret="${secret%%:*}"
    attrs="${entry##*:}"
    if [[ ! -r "${secrets_dir}/${secret}" ]]; then
      echo "00-roles: secret ${secrets_dir}/${secret} tidak terbaca (role ${role})" >&2
      return 1
    fi
    pw="$(< "${secrets_dir}/${secret}")"
    if [[ ${#pw} -lt 16 ]]; then
      echo "00-roles: password ${secret} terlalu pendek (< 16)" >&2
      return 1
    fi
    # Karakter yang aman di dalam literal meta-command psql `\set pw '...'` (generator random/hex32 = base64/hex).
    if [[ ! "$pw" =~ ^[A-Za-z0-9+/=._~-]+$ ]]; then
      echo "00-roles: password ${secret} memuat karakter di luar [A-Za-z0-9+/=._~-]" >&2
      return 1
    fi
    # SEC-26: password TIDAK lewat argv (`-v pw=...` terlihat di `ps`/proc cmdline host). Dikirim lewat STDIN:
    # baris `\set pw '...'` di skrip SQL, lalu dipakai sebagai :'pw' (literal ter-escape). role/attrs bukan rahasia.
    # password_encryption=scram-sha-256 (compose) -> disimpan sebagai hash SCRAM; log statement dimatikan.
    {
      printf "\\\\set pw '%s'\n" "$pw"
      cat <<'SQL'
SET log_statement = 'none';
SET log_min_duration_statement = -1;
SELECT format('CREATE ROLE %I', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH %s PASSWORD %L', :'role', :'attrs', :'pw') \gexec
\unset pw
SQL
    } | psql -v ON_ERROR_STOP=1 -X -q --username "$su" --dbname postgres \
          -v role="$role" -v attrs="$attrs" || return 1
  done

  # efn_exporter: metrik saja
  psql -v ON_ERROR_STOP=1 -X -q --username "$su" --dbname postgres <<'SQL' || return 1
GRANT pg_monitor TO efn_exporter;
-- efn_etl anggota efn_readonly (role NOLOGIN dibuat 01-databases.sql) bila sudah ada
SELECT 'GRANT efn_readonly TO efn_etl'
 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'efn_readonly') \gexec
-- DB metabase (dibuat 01-databases.sql) dimiliki efn_metabase (docs/04 §7.3)
SELECT 'ALTER DATABASE metabase OWNER TO efn_metabase'
 WHERE EXISTS (SELECT 1 FROM pg_database WHERE datname = 'metabase') \gexec
SQL
  echo "00-roles: role per service siap (efn_odoo, efn_bff, efn_ocr, efn_etl, efn_metabase, efn_exporter)"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  efn_roles_main
else
  efn_roles_main || return 1
fi
