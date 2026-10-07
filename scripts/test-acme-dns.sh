#!/usr/bin/env bash
# Uji integrasi SEC-37/SEC-53: ACME DNS-01 terpisah (infra/acme/acme-dns.sh) + penulis konfigurasi Traefik terpisah
# (infra/acme/acme-publish.sh, network none) + Traefik provider file.
# Memakai Pebble + pebble-challtestsrv (server ACME/DNS uji open source Let's Encrypt) di network Docker --internal
# sekali pakai: tanpa domain, tanpa token, tanpa akses internet. Dijalankan di CI job infra dan lokal (Git Bash).
#
# Membuktikan:
#   1. acme-dns-init + acme-dns non-root (65532:0, read-only, cap_drop ALL) menerbitkan sertifikat via DNS-01.
#   2. acme-publish (65533:0, network none) memasang PEM ke volume traefik-dynamic; Traefik (image & digest sama
#      dengan compose, read-only, cap_drop ALL, TANPA token) menyajikan sertifikat itu lewat SNI.
#   3. Perpanjangan (--renew-force) -> Traefik menyajikan sertifikat baru TANPA restart; file versi lama dihapus.
#   4. Izin: kunci 0640 65533:0 di volume traefik-dynamic; kunci akun ACME tidak pernah keluar dari volume data.
#   5. Mode nonaktif (token `disabled`) tidak menghubungi CA; lego v5 membaca token dari <VAR>_FILE.
#   6. SEC-53: PEM berisi YAML/router disisipkan ke acme-dns-out -> DITOLAK acme-publish, konfigurasi lama tetap;
#      acme-publish tidak bisa mengganti nama base/ (sticky + milik root); acme-dns tidak me-mount traefik-dynamic.
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
  docker volume rm "$N-data" "$N-out" "$N-dyn" "$N-ca" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --internal "$N" > /dev/null
for v in data out dyn ca; do docker volume create "$N-$v" > /dev/null; done

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
  --read-only -v "$N-data:/data" -v "$N-out:/out" -v "$N-dyn:/dyn" -v "$ACME_SRC:/opt/efn-acme:ro" \
  --entrypoint /bin/sh "$LEGO" /opt/efn-acme/acme-dns-init.sh

acme() {
  # $@ = argumen env tambahan (-e ...)
  docker run --rm --network "$N" --user 65532:0 --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges \
    -v "$N-data:/data" -v "$N-out:/out" -v "$N-ca:/ca:ro" -v "$ACME_SRC:/opt/efn-acme:ro" -e ACME_OUT=/out \
    -e ACME_ONESHOT=1 -e ACME_DOMAINS="$DOM,bi.efn.test" -e ACME_EMAIL=ops@efn.test \
    -e ACME_SERVER=https://pebble:14000/dir -e LEGO_CA_CERTIFICATES=/ca/pebble.minica.pem \
    -e ACME_DNS_RESOLVERS=challtestsrv:8053 -e ACME_TOKEN_FILE=/run/secrets/none \
    -e EXEC_PATH=/ca/hook.sh "$@" \
    --entrypoint /bin/sh "$LEGO" /opt/efn-acme/acme-dns.sh
}
# acme-publish satu siklus (sama dengan compose: 65533:0, network none, read-only, cap_drop ALL)
publish() {
  docker run --rm --network none --user 65533:0 --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges -v "$N-out:/in:ro" -v "$N-dyn:/dyn" -v "$ACME_SRC:/opt/efn-acme:ro" \
    -e ACME_ONESHOT=1 -e ACME_IN=/in -e ACME_DYN=/dyn -e ACME_TRAEFIK_DIR=/etc/traefik/dynamic \
    --entrypoint /bin/sh "$LEGO" /opt/efn-acme/acme-publish.sh
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
docker run --rm -v "$N-data:/data" -v "$N-out:/out" --entrypoint /bin/sh "$LEGO" \
  -c 'rm -rf /data/certificates; rm -f /out/*.crt /out/*.key; true'

# 1. Penerbitan pertama
if out=$(acme -e ACME_DNS_PROVIDER=exec -e ACME_EXTRA_ARGS="--dns.propagation.wait=1s" 2>&1); then
  pass "penerbitan DNS-01 lewat Pebble"
else
  fail "penerbitan: $(tail -15 <<< "$out")"; exit 1
fi
if out=$(publish 2>&1) && grep -q 'dipasang untuk Traefik' <<< "$out"; then
  pass "acme-publish memasang PEM tervalidasi ke traefik-dynamic"
else
  fail "acme-publish: $out"
fi

# 2. Traefik membaca volume certs (read-only) di subdirektori provider file, tanpa token
docker run -d --name "$N-traefik" --network "$N" --network-alias traefik --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges -v "$N-dyn:/etc/traefik/dynamic:ro" -v "$BASE_SRC:/etc/traefik/dynamic/base:ro" "$TRAEFIK" \
  --log.level=DEBUG --entrypoints.wg.address=:8443 --entrypoints.wg.http.tls=true \
  --providers.file.directory=/etc/traefik/dynamic --providers.file.watch=true > /dev/null

served() {
  docker run --rm --network "$N" -v "$N-dyn:/certs:ro" --entrypoint /bin/sh "$TRAEFIK_CLIENT" -c '
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
perm=$(docker run --rm -v "$N-dyn:/dyn:ro" -v "$N-out:/out:ro" -v "$N-data:/data:ro" --entrypoint /bin/sh "$LEGO" -c \
  'stat -c "%a %u:%g" /dyn/*.key; stat -c "%a" /data; stat -c "%a %u:%g" /dyn /dyn/base;
   ls -R /dyn /out | grep -c "accounts\|\.json" || true')
if [[ "$perm" == $'640 65533:0\n700\n1770 0:0\n755 0:0\n0' ]]; then
  pass "izin: kunci 0640 65533:0, /data 0700, /dyn 1770 root, base/ root, akun ACME tidak keluar dari volume data"
else fail "izin: $perm"; fi

# 3. Perpanjangan paksa -> Traefik memuat sertifikat baru tanpa restart
if out=$(acme -e ACME_DNS_PROVIDER=exec -e ACME_EXTRA_ARGS="--dns.propagation.wait=1s --renew-force --no-random-sleep" 2>&1) \
   && publish > /dev/null 2>&1; then
  pass "perpanjangan paksa + publish"
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

# 6. SEC-53: acme-dns terkompromi menyisipkan konfigurasi Traefik lewat PEM -> ditolak; konfigurasi lama tetap
yml_before=$(docker run --rm -v "$N-dyn:/dyn:ro" --entrypoint /bin/cat "$LEGO" /dyn/acme-dns.yml)
docker run --rm --user 65532:0 -v "$N-out:/out" --entrypoint /bin/sh "$LEGO" -c '
  { cat /out/efn-wg.crt; printf "http:\n  routers:\n    evil:\n      rule: Host(\`api.x\`)\n      service: odoo@docker\n"; } > /out/.x
  mv -f /out/.x /out/efn-wg.crt'
out=$(publish 2>&1 || true)
yml_after=$(docker run --rm -v "$N-dyn:/dyn:ro" --entrypoint /bin/sh "$LEGO" -c 'cat /dyn/acme-dns.yml; ls /dyn')
if grep -q 'DITOLAK' <<< "$out" && [[ "$yml_after" == "$yml_before"* ]] && ! grep -q 'evil\|routers' <<< "$yml_after"; then
  pass "PEM berisi router YAML ditolak acme-publish; konfigurasi lama tetap"
else
  fail "PEM jahat: $out | $yml_after"
fi
docker run --rm --user 65532:0 -v "$N-out:/out" --entrypoint /bin/sh "$LEGO" -c \
  'printf -- "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n-----BEGIN EC PRIVATE KEY-----\nAAAA\n-----END EC PRIVATE KEY-----\n" > /out/efn-wg.key'
# crt dipulihkan (sah) agar yang diuji benar-benar penolakan kunci
docker run --rm --user 65532:0 -v "$N-out:/out" -v "$N-data:/data:ro" --entrypoint /bin/sh "$LEGO" -c 'cp /data/certificates/efn-wg.crt /out/efn-wg.crt && chmod 0640 /out/efn-wg.crt'
out=$(publish 2>&1 || true)
if grep -q 'DITOLAK: /in/efn-wg.key' <<< "$out"; then pass "kunci berisi >1 blok PEM ditolak"; else fail "kunci multi-blok tidak ditolak: $out"; fi
if docker run --rm --user 65533:0 --cap-drop ALL -v "$N-dyn:/dyn" --entrypoint /bin/sh "$LEGO" -c \
     'mv /dyn/base /dyn/x 2>/dev/null || rm -rf /dyn/base 2>/dev/null || mkdir /dyn/base/x 2>/dev/null'; then
  fail "acme-publish bisa mengubah titik mount base/"
else
  pass "acme-publish tidak bisa mengganti nama/menghapus/menulis base/ (sticky, milik root)"
fi
if awk '/^  acme-dns:$/{f=1; next} f && /^  [a-z]/{f=0} f' "$COMPOSE" | grep -q 'traefik-dynamic'; then
  fail "compose: acme-dns me-mount traefik-dynamic"
else
  pass "compose: acme-dns tidak me-mount traefik-dynamic"
fi

if [[ $FAIL == 0 ]]; then echo "test-acme-dns: SEMUA LULUS"; else echo "test-acme-dns: ADA YANG GAGAL"; exit 1; fi
