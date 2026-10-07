#!/bin/sh
# ACME DNS-01 terpisah dari Traefik (SEC-37, ADR-023; runbook docs/runbooks/acme-dns-token.md).
#
# Berjalan di image resmi goacme/lego (Alpine, busybox sh) sebagai service `acme-dns`: TANPA port, hanya network
# `egress`, satu-satunya pemegang token API DNS (secret acme_dns_api_token). Traefik (internet-facing) tidak lagi
# me-mount token; ia hanya membaca sertifikat hasil skrip ini dari volume `acme-dns-certs` (read-only) lewat provider
# file (`/etc/traefik/dynamic/acme-dns.yml`; volume = direktori yang di-watch Traefik).
#
# Alur per siklus:
#   1. `lego run` (v5: menerbitkan bila belum ada, memperpanjang bila jatuh tempo/ARI; selain itu tidak melakukan
#      apa-apa). Data lego (kunci akun ACME + sertifikat) di $ACME_DATA (volume `acme-dns-data`, hanya service ini).
#   2. Bila sertifikat di $ACME_DATA berbeda dari yang terakhir dipasang: salin crt+key ke $ACME_OUT dengan nama
#      berversi (<nama>-<sha256 16>.crt/.key, mode 0640, grup 0 = Traefik) lalu tulis ulang acme-dns.yml secara atomik
#      (tmp + mv). Nama file berubah -> konfigurasi dinamis berubah -> Traefik memuat ulang sertifikat tanpa restart
#      (Traefik tidak memuat ulang isi file sertifikat bila path-nya sama). File versi lama dihapus.
#   3. Tidur $ACME_INTERVAL detik (gagal: $ACME_RETRY detik), ulangi.
#
# Mode nonaktif: isi token = `disabled` (dev/CI; deploy.sh menolak nilai ini di staging/prod) -> tidak ada request ACME,
# container idle dan sehat (penanda $ACME_DATA/.disabled).
# Healthcheck (compose): $ACME_DATA/.last-ok berumur < 25 jam, atau mode nonaktif.
# Uji: scripts/test-acme-dns.sh (Pebble, server ACME uji open source Let's Encrypt) di CI job infra.
set -eu
umask 027

log() { printf '%s acme-dns: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "FATAL: $*"; exit 1; }

ACME_DOMAINS=${ACME_DOMAINS:-}
ACME_EMAIL=${ACME_EMAIL:-}
ACME_DNS_PROVIDER=${ACME_DNS_PROVIDER:-cloudflare}
ACME_SERVER=${ACME_SERVER:-https://acme-v02.api.letsencrypt.org/directory}
ACME_DNS_RESOLVERS=${ACME_DNS_RESOLVERS:-1.1.1.1:53,9.9.9.9:53}
ACME_INTERVAL=${ACME_INTERVAL:-43200}
ACME_RETRY=${ACME_RETRY:-3600}
ACME_TOKEN_FILE=${ACME_TOKEN_FILE:-/run/secrets/acme_dns_api_token}
ACME_DATA=${ACME_DATA:-/data}
ACME_OUT=${ACME_OUT:-/certs}
ACME_TRAEFIK_DIR=${ACME_TRAEFIK_DIR:-/etc/traefik/dynamic}
ACME_CERT_NAME=${ACME_CERT_NAME:-efn-wg}
ACME_ONESHOT=${ACME_ONESHOT:-0}
# Hanya untuk uji (scripts/test-acme-dns.sh): mis. "--renew-force --dns.propagation.wait=1s". Dibatasi allowlist.
ACME_EXTRA_ARGS=${ACME_EXTRA_ARGS:-}

# ── Validasi input (nilai berasal dari env compose / deploy.sh) ──
printf '%s' "$ACME_DNS_PROVIDER" | grep -Eq '^[a-z0-9]+$' || die "ACME_DNS_PROVIDER tidak valid"
printf '%s' "$ACME_CERT_NAME" | grep -Eq '^[a-z0-9-]+$' || die "ACME_CERT_NAME tidak valid"
printf '%s' "$ACME_INTERVAL$ACME_RETRY" | grep -Eq '^[0-9]+$' || die "ACME_INTERVAL/ACME_RETRY harus angka"
printf '%s' "$ACME_SERVER" | grep -Eq '^https://[A-Za-z0-9.:/_-]+$' || die "ACME_SERVER harus https://"
printf '%s' "$ACME_DNS_RESOLVERS" | grep -Eq '^[A-Za-z0-9.:-]+(,[A-Za-z0-9.:-]+)*$' || die "ACME_DNS_RESOLVERS tidak valid"
printf '%s' "$ACME_TRAEFIK_DIR" | grep -Eq '^/[A-Za-z0-9/_.-]+$' || die "ACME_TRAEFIK_DIR tidak valid"
[ -z "$ACME_EXTRA_ARGS" ] || printf '%s' "$ACME_EXTRA_ARGS" | grep -Eq '^(--[a-z.-]+(=[A-Za-z0-9.:/_-]+)?( |$))+$' \
  || die "ACME_EXTRA_ARGS tidak valid"
[ -d "$ACME_DATA" ] && [ -w "$ACME_DATA" ] || die "$ACME_DATA tidak dapat ditulis (acme-dns-init sudah jalan?)"
[ -d "$ACME_OUT" ] && [ -w "$ACME_OUT" ] || die "$ACME_OUT tidak dapat ditulis (acme-dns-init sudah jalan?)"

token=$(head -c 64 "$ACME_TOKEN_FILE" 2> /dev/null || true)
if [ "$ACME_DNS_PROVIDER" != exec ] && { [ -z "$token" ] || [ "$token" = disabled ]; }; then
  log "token DNS = disabled/kosong -> ACME DNS-01 NONAKTIF (dev/CI). Staging/prod: deploy.sh menolak nilai ini."
  rm -f "$ACME_DATA/.last-ok"
  : > "$ACME_DATA/.disabled"
  [ "$ACME_ONESHOT" = 1 ] && exit 0
  while :; do sleep 3600; done
fi
unset token
rm -f "$ACME_DATA/.disabled"

printf '%s' "$ACME_DOMAINS" | grep -Eq '^[a-z0-9.-]+(,[a-z0-9.-]+)*$' || die "ACME_DOMAINS tidak valid: '$ACME_DOMAINS'"
printf '%s' "$ACME_EMAIL" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' || die "ACME_EMAIL tidak valid"

# Pasang sertifikat terbaru untuk Traefik bila berubah. Kembali 0 bila tidak ada yang perlu dilakukan.
deploy_cert() {
  crt="$ACME_DATA/certificates/$ACME_CERT_NAME.crt"
  key="$ACME_DATA/certificates/$ACME_CERT_NAME.key"
  [ -s "$crt" ] && [ -s "$key" ] || { log "belum ada sertifikat di $ACME_DATA/certificates"; return 0; }
  sum=$(sha256sum "$crt" | cut -c1-16)
  base="$ACME_CERT_NAME-$sum"
  if [ -s "$ACME_OUT/$base.crt" ] && [ -s "$ACME_OUT/$base.key" ] && grep -q "$base.crt" "$ACME_OUT/acme-dns.yml" 2> /dev/null; then
    return 0
  fi
  # .tmp tidak berakhiran .yml/.toml -> diabaikan provider file Traefik selama ditulis
  cp "$crt" "$ACME_OUT/.$base.crt.tmp" && chmod 0640 "$ACME_OUT/.$base.crt.tmp" && mv -f "$ACME_OUT/.$base.crt.tmp" "$ACME_OUT/$base.crt"
  cp "$key" "$ACME_OUT/.$base.key.tmp" && chmod 0640 "$ACME_OUT/.$base.key.tmp" && mv -f "$ACME_OUT/.$base.key.tmp" "$ACME_OUT/$base.key"
  {
    echo "# DIGENERATE oleh infra/acme/acme-dns.sh (SEC-37) - jangan diedit. $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "tls:"
    echo "  certificates:"
    echo "    - certFile: $ACME_TRAEFIK_DIR/$base.crt"
    echo "      keyFile: $ACME_TRAEFIK_DIR/$base.key"
  } > "$ACME_OUT/.acme-dns.yml.tmp"
  chmod 0640 "$ACME_OUT/.acme-dns.yml.tmp"
  mv -f "$ACME_OUT/.acme-dns.yml.tmp" "$ACME_OUT/acme-dns.yml"
  # Hapus versi lama (Traefik sudah/akan memuat path baru; isi sertifikat lama ada di memorinya sampai reload)
  for f in "$ACME_OUT/$ACME_CERT_NAME"-*.crt "$ACME_OUT/$ACME_CERT_NAME"-*.key; do
    [ -e "$f" ] || continue
    case "$f" in "$ACME_OUT/$base".*) ;; *) rm -f "$f" ;; esac
  done
  log "sertifikat dipasang untuk Traefik: $base ($(sed -n 's/^.*BEGIN CERTIFICATE.*$/x/p' "$crt" | wc -l) sertifikat dalam rantai)"
}

run_lego() {
  # shellcheck disable=SC2086  # ACME_EXTRA_ARGS sengaja dipisah spasi (sudah divalidasi allowlist)
  /lego --log.format text run \
    --accept-tos --email "$ACME_EMAIL" --server "$ACME_SERVER" \
    --path "$ACME_DATA" --cert.name "$ACME_CERT_NAME" --domains "$ACME_DOMAINS" --force-cert-domains \
    --key-type EC256 --dns "$ACME_DNS_PROVIDER" --dns.resolvers "$ACME_DNS_RESOLVERS" $ACME_EXTRA_ARGS
}

log "mulai: domains=$ACME_DOMAINS provider=$ACME_DNS_PROVIDER server=$ACME_SERVER interval=${ACME_INTERVAL}s"
# Pasang ulang sertifikat yang sudah ada (mis. volume certs baru) sebelum menghubungi CA.
deploy_cert
while :; do
  if run_lego; then
    deploy_cert
    : > "$ACME_DATA/.last-ok"
    wait_s=$ACME_INTERVAL
  else
    log "lego gagal (rc=$?); coba lagi dalam ${ACME_RETRY}s. Sertifikat lama tetap dipakai Traefik."
    [ "$ACME_ONESHOT" = 1 ] && exit 1
    wait_s=$ACME_RETRY
  fi
  [ "$ACME_ONESHOT" = 1 ] && exit 0
  sleep "$wait_s"
done
