#!/bin/sh
# K25-06 (docs/runbooks/multi-env-local.md): render file konfigurasi statis yang tidak bisa membaca env (traefik.yml,
# promtail.yml) per identitas stack, lalu exec program aslinya.
#
#   render-config.sh <template> <output> <program> [argumen...]
#
# Mengganti SEMUA `@EFN_STACK@` di <template> dengan $EFN_STACK (default `efn`) dan menulis <output> (tmpfs /tmp
# container; rootfs tetap read-only). Gagal tertutup (exit != 0, program tidak dijalankan) bila EFN_STACK tidak valid
# atau masih ada placeholder `@EFN_*@` lain. Dijalankan di image alpine (Traefik, busybox sh) dan debian (Promtail).
# Tanpa secret, tanpa jaringan. Idempoten: output ditimpa setiap start.
set -eu

if [ "$#" -lt 3 ]; then
  echo "render-config: pemakaian: render-config.sh <template> <output> <program> [argumen...]" >&2
  exit 64
fi
tpl="$1"
out="$2"
shift 2

stack="${EFN_STACK:-efn}"
# Sama dengan aturan scripts/check-encryption.sh: huruf kecil/angka/'-', diawali huruf, diakhiri huruf/angka, <= 32.
# Tanpa '_' (pemisah <stack>_<network>) dan tanpa karakter khusus sed ('/', '&', '\').
case "$stack" in
  *[!a-z0-9-]*|-*|*-|[0-9]*|'')
    echo "render-config: EFN_STACK '${stack}' tidak valid (pola ^[a-z][a-z0-9-]*[a-z0-9]\$)" >&2
    exit 64
    ;;
esac
if [ "${#stack}" -gt 32 ] || [ "${#stack}" -lt 2 ]; then
  echo "render-config: EFN_STACK '${stack}' harus 2..32 karakter" >&2
  exit 64
fi
[ -r "$tpl" ] || { echo "render-config: template ${tpl} tidak terbaca" >&2; exit 66; }

tmp="${out}.tmp.$$"
umask 077
sed "s/@EFN_STACK@/${stack}/g" "$tpl" > "$tmp"
if grep -q '@EFN_[A-Z_]*@' "$tmp"; then
  rm -f "$tmp"
  echo "render-config: placeholder @EFN_*@ tidak dikenal tersisa di ${tpl}" >&2
  exit 65
fi
mv -f "$tmp" "$out"
exec "$@"
