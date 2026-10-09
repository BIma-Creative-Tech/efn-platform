#!/bin/sh
# Entrypoint odoo-rpc-gateway (HAProxy). docs/04 §10.4.1, ADR-034 (Proposed), ADR-022.
#  - HAProxy butuh sertifikat + kunci dalam SATU PEM; Docker secret menyediakan dua file -> digabung ke tmpfs
#    (/tmp, 0600, tidak pernah ke disk/volume). PEM yang sama dipakai untuk listener (server) dan sebagai sertifikat
#    klien mTLS ke odoo-tls (EKU server+client, gen-pki.sh profil `both`).
#  - ODOO_DB divalidasi fail-closed (dipakai haproxy.cfg untuk memaksa header X-Odoo-Database).
set -eu
umask 077

case "${ODOO_DB:-}" in
  efn_*) ;;
  *) echo "odoo-rpc-gateway: ODOO_DB wajib diawali efn_ (dbfilter Odoo ^efn_.*$)" >&2; exit 1 ;;
esac
if [ "${#ODOO_DB}" -gt 44 ] || printf '%s' "$ODOO_DB" | grep -q '[^a-z0-9_]'; then
  echo "odoo-rpc-gateway: ODOO_DB hanya boleh [a-z0-9_], maks 44 karakter" >&2
  exit 1
fi
for s in efn_ca_crt tls_odoo_rpc_gateway_crt tls_odoo_rpc_gateway_key; do
  [ -s "/run/secrets/$s" ] || { echo "odoo-rpc-gateway: secret $s kosong/tidak ada" >&2; exit 1; }
done

mkdir -p /tmp/haproxy
{
  cat /run/secrets/tls_odoo_rpc_gateway_crt
  echo
  cat /run/secrets/tls_odoo_rpc_gateway_key
} > /tmp/haproxy/odoo-rpc-gateway.pem
exec haproxy -W -db -f /usr/local/etc/haproxy/haproxy.cfg
