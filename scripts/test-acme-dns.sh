#!/usr/bin/env bash
# Uji integrasi SEC-37: komponen ACME DNS-01 terpisah (infra/acme/acme-dns.sh) + Traefik provider file.
# Memakai Pebble + pebble-challtestsrv (server ACME/DNS uji open source Let's Encrypt) di network Docker --internal
# sekali pakai: tanpa domain, tanpa token, tanpa akses internet. Dijalankan di CI job infra dan lokal (Git Bash).
#
# Membuktikan:
#   1. acme-dns-init + acme-dns non-root (65532:0, read-only, cap_drop ALL) menerbitkan sertifikat via DNS-01.
#   2. Traefik (image & digest sama dengan compose, read-only, cap_drop ALL, TANPA token) menyajikan sertifikat itu
#      lewat SNI dari volume read-only di subdirektori /etc/traefik/dynamic/acme.
#   3. Perpanjangan (--renew-force) -> Traefik menyajikan sertifikat baru TANPA restart; file versi lama dihapus.
#   4. Izin: kunci 0640 65532:0 di volume certs; kunci akun ACME tidak pernah masuk volume certs.
#   5. Mode nonaktif (token `disabled`) tidak menghubungi CA; lego v5 membaca token dari <VAR>_FILE.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: jangan ubah path container (/data, /certs, ...)

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE="$ROOT/infra/compose/docker-compose.yml"
# Path host untuk bind mount (Docker Desktop di Windows butuh path Windows)
ACME_SRC=$(cd "$ROOT/infra/acme" && { pwd -W 2> /dev/null || pwd; })
TEST_SRC=$(cd "$ROOT/scripts/acme-test" && { pwd -W 2> /dev/null || pwd; })
BASE_SRC=$(cd "$ROOT/scripts/acme-test/traefik-base" && { pwd -W 2> /dev/null || pwd; })

img_of() { grep -m1 -E "^[[:space:]]+image: $1" "$COMPOSE" | sed -E 's/^[[:space:]]+image: //'; }
LEGO=$(img_of 'goacme/lego:')
TRAEFIK=$(img_of 'traefik:')
PEBBLE=ghcr.io/letsencrypt/pebble:2.10.1@sha256:ddf230642b1a584f519f32e347de1b05a6e4c1f6c35c1863b33effeab5f78199
CHALL=ghcr.io/letsencrypt/pebble-challtestsrv:2.10.1@sha256:12ce21884def456bcf9786542113949e1f19dc7738d2c70e156c2d0c38a1405b
[[ "$LEGO" == *@sha256:* && "$TRAEFIK" == *@sha256:* ]] || { echo "image lego/traefik di compose tidak ber-digest"; exit 1; }

N="efn-acmetest-$$"
DOM=erp.efn.test
FAIL=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAIL=1; }

cleanup() {
  docker rm -f "$N-dns" "$N-pebble" "$N-traefik" > /dev/null 2>&1 || true
  docker network rm "$N" > /dev/null 2>&1 || true
  docker volume rm "$N-data" "$N-certs" "$N-ca" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal "$N" > /dev/null
for v in data certs ca; do docker volume create "$N-$v" > /dev/null; done

docker run -d --name "$N-dns" --network "$N" --network-alias challtestsrv "$CHALL" \
  -defaultIPv4 "" -defaultIPv6 "" -http01 "" -https01 "" -tlsalpn01 "" -doh "" \
  -dnsserver :8053 -management :8055 > /dev/null
docker run -d --name "$N-pebble" --network "$N" --network-alias pebble \
  -e PEBBLE_VA_NOSLEEP=1 -e PEBBLE_WFE_NONCEREJECT=0 "$PEBBLE" -dnsserver challtestsrv:8053 > /dev/null

# CA TLS Pebble (minica) -> volume ca, agar lego memverifikasi TLS server ACME (tanpa --tls-skip-verify)
sleep 2
docker cp "$N-pebble:/test/certs/pebble.minica.pem" - \
  | docker run -i --rm -v "$N-ca:/ca" -v "$TEST_SRC:/src:ro" --entrypoint /bin/sh "$LEGO" -c \
    'tar -x -C /ca && echo disabled > /ca/token-disabled && echo dummy-token > /ca/token-dummy && chmod 0644 /ca/* \
     && cp /src/exec-challtestsrv.sh /ca/hook.sh && chmod 0755 /ca/hook.sh'   # bit exec tak bergantung checkout

# 1. init (sama dengan compose acme-dns-init)
docker run --rm --user 0:0 --cap-drop ALL --cap-add CHOWN --cap-add FOWNER --security-opt no-new-privileges \
  --read-only -v "$N-data:/data" -v "$N-certs:/certs" -v "$ACME_SRC:/opt/efn-acme:ro" \
  --entrypoint /bin/sh "$LEGO" /opt/efn-acme/acme-dns-init.sh

acme() {
  # $@ = argumen env tambahan (-e ...)
  docker run --rm --network "$N" --user 65532:0 --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges \
    -v "$N-data:/data" -v "$N-certs:/certs" -v "$N-ca:/ca:ro" -v "$ACME_SRC:/opt/efn-acme:ro" \
    -e ACME_ONESHOT=1 -e ACME_DOMAINS="$DOM,bi.efn.test" -e ACME_EMAIL=ops@efn.test \
    -e ACME_SERVER=https://pebble:14000/dir -e LEGO_CA_CERTIFICATES=/ca/pebble.minica.pem \
    -e ACME_DNS_RESOLVERS=challtestsrv:8053 -e ACME_TOKEN_FILE=/run/secrets/none \
    -e EXEC_PATH=/ca/hook.sh -e ACME_TRAEFIK_DIR=/etc/traefik/dynamic "$@" \
    --entrypoint /bin/sh "$LEGO" /opt/efn-acme/acme-dns.sh
}

# 5a. Mode nonaktif: token `disabled`, provider cloudflare -> exit 0 tanpa request ACME
if out=$(acme -e ACME_DNS_PROVIDER=cloudflare -e ACME_TOKEN_FILE=/ca/token-disabled 2>&1) \
  && grep -q NONAKTIF <<< "$out" && ! grep -q 'lego' <<< "${out//acme-dns/}"; then
  pass "mode nonaktif (token disabled) tidak menghubungi CA"
else
  fail "mode nonaktif: $out"
fi

# 5b. lego v5 membaca CF_DNS_API_TOKEN_FILE (bukan 'credentials information are missing')
out=$(acme -e ACME_DNS_PROVIDER=cloudflare -e ACME_TOKEN_FILE=/ca/token-dummy \
  -e CF_DNS_API_TOKEN_FILE=/ca/token-dummy -e CLOUDFLARE_BASE_URL=https://pebble:14000/cf 2>&1 || true)
if grep -qi 'credentials information are missing\|missing.*CF_DNS_API_TOKEN' <<< "$out"; then
  fail "lego tidak membaca CF_DNS_API_TOKEN_FILE: $(tail -3 <<< "$out")"
else
  pass "lego v5 membaca token dari CF_DNS_API_TOKEN_FILE"
fi
# bersihkan sisa percobaan (akun/pesanan gagal) agar penerbitan berikut bersih
docker run --rm -v "$N-data:/data" -v "$N-certs:/certs" --entrypoint /bin/sh "$LEGO" \
  -c 'rm -rf /data/certificates; rm -f /certs/*.crt /certs/*.key /certs/*.yml; true'

# 1. Penerbitan pertama
if out=$(acme -e ACME_DNS_PROVIDER=exec -e ACME_EXTRA_ARGS="--dns.propagation.wait=1s" 2>&1); then
  pass "penerbitan DNS-01 lewat Pebble"
else
  fail "penerbitan: $(tail -15 <<< "$out")"; exit 1
fi

# 2. Traefik membaca volume certs (read-only) di subdirektori provider file, tanpa token
docker run -d --name "$N-traefik" --network "$N" --network-alias traefik --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges -v "$N-certs:/etc/traefik/dynamic:ro" -v "$BASE_SRC:/etc/traefik/dynamic/base:ro" "$TRAEFIK" \
  --log.level=DEBUG --entrypoints.wg.address=:8443 --entrypoints.wg.http.tls=true \
  --providers.file.directory=/etc/traefik/dynamic --providers.file.watch=true > /dev/null

served() {
  docker run --rm --network "$N" -v "$N-certs:/certs:ro" --entrypoint /bin/sh "$TRAEFIK_CLIENT" -c '
    set -e
    srv=$(echo | openssl s_client -connect traefik:8443 -servername '"$DOM"' 2>/dev/null | openssl x509 -noout -fingerprint -sha256)
    f=$(ls /certs/*.crt)
    want=$(openssl x509 -in "$f" -noout -fingerprint -sha256)
    echo "$srv|$want|$(ls /certs | grep -Ev "^(base|dev)\$" | tr "\n" " ")"'
}
# klien openssl: image python yang sama dengan job CI (bookworm, punya openssl)
TRAEFIK_CLIENT=python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea

wait_match() {
  local r
  for _ in $(seq 1 60); do
    r=$(served 2> /dev/null || true)
    if [[ -n "$r" && "${r%%|*}" == "$(cut -d'|' -f2 <<< "$r")" ]]; then echo "$r"; return 0; fi
    sleep 1
  done
  echo "$r"; return 1
}
if r1=$(wait_match); then pass "Traefik menyajikan sertifikat Pebble untuk $DOM (${r1%%|*})"; else fail "Traefik: $r1"; fi

# 4. Izin & isolasi
perm=$(docker run --rm -v "$N-certs:/certs:ro" -v "$N-data:/data:ro" --entrypoint /bin/sh "$LEGO" -c \
  'stat -c "%a %u:%g" /certs/*.key; stat -c "%a" /data; ls -R /certs | grep -c "accounts\|\.json" || true')
if [[ "$perm" == $'640 65532:0\n700\n0' ]]; then pass "izin kunci 0640 65532:0, /data 0700, akun ACME tidak di volume certs"; else fail "izin: $perm"; fi

# 3. Perpanjangan paksa -> Traefik memuat sertifikat baru tanpa restart
if out=$(acme -e ACME_DNS_PROVIDER=exec -e ACME_EXTRA_ARGS="--dns.propagation.wait=1s --renew-force --no-random-sleep" 2>&1); then
  pass "perpanjangan paksa"
else
  fail "perpanjangan: $(tail -15 <<< "$out")"
fi
if r2=$(wait_match) && [[ "${r2%%|*}" != "${r1%%|*}" ]]; then
  pass "Traefik hot-reload sertifikat baru tanpa restart"
else
  fail "hot-reload: sebelum=${r1%%|*} sesudah=${r2:-}"
fi
nfiles=$(awk -F'|' '{print $3}' <<< "${r2:-}" | wc -w)
if [[ "$nfiles" == 3 ]]; then pass "versi lama dihapus (acme-dns.yml + 1 crt + 1 key)"; else fail "isi volume certs: ${r2:-}"; fi
# Log ditampung dulu: `docker logs | grep -q` + pipefail = SIGPIPE -> gagal palsu
tlog=$(docker logs "$N-traefik" 2>&1)
if grep -i '"level":"error"\|level=error' <<< "$tlog" | grep -v 'acme'; then
  fail "Traefik mencatat error (lihat di atas)"
else
  pass "Traefik tanpa error konfigurasi (file .crt/.key/.tmp diabaikan provider)"
fi
if grep -q acme-test-noop <<< "$tlog"; then
  pass "konfigurasi base/ (bind mount di dalam volume ro) tetap dimuat"
else
  fail "konfigurasi base/ tidak dimuat"
fi

if [[ $FAIL == 0 ]]; then echo "test-acme-dns: SEMUA LULUS"; else echo "test-acme-dns: ADA YANG GAGAL"; exit 1; fi
