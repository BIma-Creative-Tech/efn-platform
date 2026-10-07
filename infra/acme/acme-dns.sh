#!/bin/sh
# ACME DNS-01 terpisah dari Traefik (SEC-37, ADR-023; runbook docs/runbooks/acme-dns-token.md).
#
# Berjalan di image resmi goacme/lego (Alpine, busybox sh) sebagai service `acme-dns`: TANPA port, hanya network
# `egress`, satu-satunya pemegang token API DNS (secret acme_dns_api_token). Traefik (internet-facing) tidak lagi
# me-mount token. SEC-53 (gelombang 13): service ini HANYA menulis PEM ke volume `acme-dns-out`; ia TIDAK me-mount
# direktori provider file Traefik. Service terpisah `acme-publish` (infra/acme/acme-publish.sh, network none)
# memvalidasi PEM lalu menulis acme-dns.yml dari template tetap ke volume `traefik-dynamic`.
#
# Alur per siklus:
#   1. `lego run` (v5: menerbitkan bila belum ada, memperpanjang bila jatuh tempo/ARI; selain itu tidak melakukan
#      apa-apa). Data lego (kunci akun ACME + sertifikat) di $ACME_DATA (volume `acme-dns-data`, hanya service ini).
#   2. Bila sertifikat di $ACME_DATA berbeda dari yang ada di $ACME_OUT: salin crt+key ke $ACME_OUT/<nama>.crt|.key
#      (mode 0640, grup 0 = acme-publish) secara atomik (tmp + mv; kunci dulu, sertifikat terakhir).
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

# Salin sertifikat terbaru ke $ACME_OUT bila berubah (acme-publish yang memasangnya untuk Traefik, SEC-53).
deploy_cert() {
  crt="$ACME_DATA/certificates/$ACME_CERT_NAME.crt"
  key="$ACME_DATA/certificates/$ACME_CERT_NAME.key"
  [ -s "$crt" ] && [ -s "$key" ] || { log "belum ada sertifikat di $ACME_DATA/certificates"; return 0; }
  if cmp -s "$crt" "$ACME_OUT/$ACME_CERT_NAME.crt" && cmp -s "$key" "$ACME_OUT/$ACME_CERT_NAME.key"; then
    return 0
  fi
  # Kunci dulu, sertifikat terakhir (acme-publish memasang saat sertifikat berubah).
  cp "$key" "$ACME_OUT/.$ACME_CERT_NAME.key.tmp" && chmod 0640 "$ACME_OUT/.$ACME_CERT_NAME.key.tmp" \
    && mv -f "$ACME_OUT/.$ACME_CERT_NAME.key.tmp" "$ACME_OUT/$ACME_CERT_NAME.key"
  cp "$crt" "$ACME_OUT/.$ACME_CERT_NAME.crt.tmp" && chmod 0640 "$ACME_OUT/.$ACME_CERT_NAME.crt.tmp" \
    && mv -f "$ACME_OUT/.$ACME_CERT_NAME.crt.tmp" "$ACME_OUT/$ACME_CERT_NAME.crt"
  log "sertifikat baru di $ACME_OUT ($(sed -n 's/^.*BEGIN CERTIFICATE.*$/x/p' "$crt" | wc -l) sertifikat dalam rantai); dipasang acme-publish"
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
