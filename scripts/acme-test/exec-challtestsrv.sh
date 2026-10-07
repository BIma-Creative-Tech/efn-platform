#!/bin/sh
# HANYA UJI (scripts/test-acme-dns.sh; di luar infra/, tidak pernah di-mount compose): hook provider `exec` lego -> pebble-challtestsrv (server DNS uji Pebble).
# lego memanggil: <skrip> present|cleanup <fqdn> <value>. API manajemen challtestsrv hanya ada di network uji
# terisolasi (--internal) yang dibuat test-acme-dns.sh dan dihapus setelahnya; tidak dipakai di compose dev/prod.
set -eu
case "$1" in
  present) path=set-txt; body=$(printf '{"host":"%s","value":"%s"}' "$2" "$3") ;;
  cleanup) path=clear-txt; body=$(printf '{"host":"%s"}' "$2") ;;
  *) exit 0 ;;
esac
wget -q -O /dev/null --header 'Content-Type: application/json' --post-data "$body" "http://challtestsrv:8055/$path"
