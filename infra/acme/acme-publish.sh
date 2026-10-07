#!/bin/sh
# acme-publish (SEC-53, gelombang 13): satu-satunya penulis direktori provider file Traefik (/etc/traefik/dynamic).
#
# Kenapa terpisah dari acme-dns: provider file Traefik memuat SETIAP *.yml di direktori itu (routers, services,
# middlewares, tls.options), bukan hanya sertifikat. acme-dns memproses respons jaringan (CA ACME, API DNS) dan
# memegang token DNS; bila prosesnya terkompromi, ia tidak boleh bisa menulis router (mis. membuka Odoo ke internet).
# Service ini: network_mode none, read-only, cap_drop ALL, user 65533:0, image lego yang sama (busybox sh).
#
# Alur per siklus ($ACME_PUBLISH_INTERVAL detik):
#   1. Baca $ACME_IN/<nama>.crt|.key (volume acme-dns-out, read-only; ditulis acme-dns).
#   2. Validasi KETAT tanpa parser YAML/PEM eksternal: crt = 1..5 blok "CERTIFICATE" base64 saja; key = tepat 1 blok
#      "EC PRIVATE KEY" atau "PRIVATE KEY" base64 saja; ukuran dibatasi. Isi lain apa pun -> ditolak, konfigurasi lama
#      tetap dipakai Traefik.
#   3. Salin ke $ACME_DYN dengan nama berversi (<nama>-<sha256 16>.crt|.key, 0640 grup 0 = Traefik) lalu tulis
#      acme-dns.yml dari TEMPLATE TETAP (hanya tls.certificates, path yang dibentuk skrip ini) secara atomik.
#      Nama berubah -> Traefik memuat ulang tanpa restart (Traefik tidak membaca ulang isi file berpath sama).
#   4. Hapus versi lama. File lain di $ACME_DYN tidak disentuh (base/ dev/ milik root, sticky bit - acme-dns-init).
# Uji: scripts/test-acme-dns.sh (Pebble).
set -eu
umask 027

log() { printf '%s acme-publish: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "FATAL: $*"; exit 1; }

ACME_IN=${ACME_IN:-/in}
ACME_DYN=${ACME_DYN:-/dyn}
ACME_TRAEFIK_DIR=${ACME_TRAEFIK_DIR:-/etc/traefik/dynamic}
ACME_CERT_NAME=${ACME_CERT_NAME:-efn-wg}
ACME_PUBLISH_INTERVAL=${ACME_PUBLISH_INTERVAL:-60}
ACME_ONESHOT=${ACME_ONESHOT:-0}
ACME_HEARTBEAT=${ACME_HEARTBEAT:-/tmp/.acme-publish-alive}
MAX_BYTES=32768

printf '%s' "$ACME_CERT_NAME" | grep -Eq '^[a-z0-9-]+$' || die "ACME_CERT_NAME tidak valid"
printf '%s' "$ACME_TRAEFIK_DIR" | grep -Eq '^/[A-Za-z0-9/_.-]+$' || die "ACME_TRAEFIK_DIR tidak valid"
printf '%s' "$ACME_PUBLISH_INTERVAL" | grep -Eq '^[1-9][0-9]*$' || die "ACME_PUBLISH_INTERVAL harus angka"
[ -d "$ACME_DYN" ] && [ -w "$ACME_DYN" ] || die "$ACME_DYN tidak dapat ditulis (acme-dns-init sudah jalan?)"

# pem_ok <file> <label-regex> <min-blok> <max-blok>: hanya baris BEGIN/END label + base64 (+ baris kosong di luar blok)
pem_ok() {
  [ -f "$1" ] && [ ! -L "$1" ] || return 1
  [ "$(wc -c < "$1")" -le "$MAX_BYTES" ] || return 1
  awk -v lbl="$2" -v min="$3" -v max="$4" '
    BEGIN { inb = 0; n = 0; ok = 1; beg = "^-----BEGIN (" lbl ")-----$"; fin = "^-----END (" lbl ")-----$" }
    { sub(/\r$/, "") }
    !inb && $0 ~ beg { inb = 1; n++; cur = $0; sub(/^-----BEGIN /, "", cur); body = 0; next }
    inb && $0 ~ fin  { e = $0; sub(/^-----END /, "", e); if (e != cur || body == 0) ok = 0; inb = 0; next }
    inb && $0 ~ /^[A-Za-z0-9+\/]+={0,2}$/ && length($0) <= 76 { body++; next }
    !inb && $0 == "" { next }
    { ok = 0 }
    END { exit !(ok && !inb && n >= min && n <= max) }' "$1"
}

publish() {
  crt="$ACME_IN/$ACME_CERT_NAME.crt"
  key="$ACME_IN/$ACME_CERT_NAME.key"
  if ! [ -s "$crt" ] || ! [ -s "$key" ]; then
    # sekali saja (dev/CI: mode nonaktif -> tidak ada PEM; jangan membanjiri log)
    [ -n "${_said_empty:-}" ] || log "belum ada sertifikat di $ACME_IN (acme-dns belum menerbitkan / mode nonaktif)"
    _said_empty=1
    return 0
  fi
  # Versi = hash pasangan crt+key: perubahan salah satu memicu validasi + pemasangan ulang
  sum=$(cat "$crt" "$key" | sha256sum | cut -c1-16)
  base="$ACME_CERT_NAME-$sum"
  if [ -s "$ACME_DYN/$base.crt" ] && [ -s "$ACME_DYN/$base.key" ] && grep -q "/$base.crt\$" "$ACME_DYN/acme-dns.yml" 2> /dev/null; then
    return 0
  fi
  pem_ok "$crt" 'CERTIFICATE' 1 5 || { log "DITOLAK: $crt bukan rantai PEM CERTIFICATE murni"; return 1; }
  pem_ok "$key" 'EC PRIVATE KEY|PRIVATE KEY' 1 1 || { log "DITOLAK: $key bukan satu kunci PEM murni"; return 1; }
  # Salin dulu ke tmp (isi yang divalidasi = isi yang dipasang; validasi ulang salinan, cegah TOCTOU)
  cp "$crt" "$ACME_DYN/.$base.crt.tmp"; cp "$key" "$ACME_DYN/.$base.key.tmp"
  if ! pem_ok "$ACME_DYN/.$base.crt.tmp" 'CERTIFICATE' 1 5 || ! pem_ok "$ACME_DYN/.$base.key.tmp" 'EC PRIVATE KEY|PRIVATE KEY' 1 1 \
     || [ "$(cat "$ACME_DYN/.$base.crt.tmp" "$ACME_DYN/.$base.key.tmp" | sha256sum | cut -c1-16)" != "$sum" ]; then
    rm -f "$ACME_DYN/.$base.crt.tmp" "$ACME_DYN/.$base.key.tmp"
    log "DITOLAK: isi berubah saat disalin"; return 1
  fi
  chmod 0640 "$ACME_DYN/.$base.crt.tmp" "$ACME_DYN/.$base.key.tmp"
  mv -f "$ACME_DYN/.$base.crt.tmp" "$ACME_DYN/$base.crt"
  mv -f "$ACME_DYN/.$base.key.tmp" "$ACME_DYN/$base.key"
  # Template TETAP: hanya tls.certificates; nilai = path yang dibentuk dari nama tervalidasi + hex
  {
    echo "# DIGENERATE oleh infra/acme/acme-publish.sh (SEC-37/SEC-53) - jangan diedit. $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "tls:"
    echo "  certificates:"
    echo "    - certFile: $ACME_TRAEFIK_DIR/$base.crt"
    echo "      keyFile: $ACME_TRAEFIK_DIR/$base.key"
  } > "$ACME_DYN/.acme-dns.yml.tmp"
  chmod 0640 "$ACME_DYN/.acme-dns.yml.tmp"
  mv -f "$ACME_DYN/.acme-dns.yml.tmp" "$ACME_DYN/acme-dns.yml"
  for f in "$ACME_DYN/$ACME_CERT_NAME"-*.crt "$ACME_DYN/$ACME_CERT_NAME"-*.key; do
    [ -e "$f" ] || continue
    case "$f" in "$ACME_DYN/$base".*) ;; *) rm -f "$f" ;; esac
  done
  log "sertifikat dipasang untuk Traefik: $base"
}

log "mulai: in=$ACME_IN dyn=$ACME_DYN interval=${ACME_PUBLISH_INTERVAL}s"
while :; do
  rc=0
  publish || rc=$?
  [ "$ACME_ONESHOT" = 1 ] && exit "$rc"
  : > "$ACME_HEARTBEAT"
  sleep "$ACME_PUBLISH_INTERVAL"
done
