#!/usr/bin/env bash
# Load test Fase 1 di STACK DEV (gelombang 13). Lihat tests/load/README.md.
#
# Pemakaian (root repo, Git Bash/Linux; stack dev sudah up - docs/runbooks/dev-stack-boot.md):
#   bash tests/load/run.sh                       # default: 5 menit, login 20 VU, pos 30 VU, scout 5 VU
#   EFN_DURATION=1m EFN_VU_LOGIN=5 bash tests/load/run.sh   # singkat (mis. saat agent lain memakai stack)
# Langkah: tolak bila api-gateway bukan APP_ENV=dev -> recreate api-gateway dengan override load test (batas dev
# dilonggarkan) -> login 1 member uji (OTP dari log BFF dev, skrip smoke) untuk JWT scout -> k6 (image resmi, digest)
# di network efn_internal, TLS terverifikasi -> sampel `docker stats` tiap 10 s -> ringkasan -> api-gateway
# dikembalikan ke konfigurasi normal (trap EXIT).
# Keluaran: tests/load/results/<UTC>/{summary.json,k6.txt,stats.csv,peaks.txt} (diabaikan git).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
K6_IMAGE=grafana/k6:1.3.0@sha256:3ddc8b1a33a2c3d8edc6e99b6a762ae36cba08788463458f5e6a7703e14eb77d
PY_IMAGE=python:3.12-bookworm@sha256:e91fec3d1ac69f04e4eddcd29c327e630ce34658cf31075bfa7e8b0e052bafea
BFF=efn-api-gateway-1
DC=(docker compose -f "$ROOT/infra/compose/docker-compose.yml" -f "$ROOT/infra/compose/docker-compose.dev.yml"
    --env-file "$ROOT/infra/compose/.env")
OVERRIDE="$ROOT/tests/load/docker-compose.loadtest.yml"
winpath() { if command -v cygpath > /dev/null; then cygpath -w "$1"; else echo "$1"; fi; }

# ── 0. Pengaman: hanya stack dev ────────────────────────────────────────────────────────────────────────────────
app_env=$(docker inspect "$BFF" --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^APP_ENV=//p')
[[ "$app_env" == dev ]] || { echo "run.sh: $BFF APP_ENV='${app_env}' - load test hanya untuk stack dev" >&2; exit 2; }
if docker ps --format '{{.Names}} {{.Image}}' | grep -q 'bff_smoke\|run-e2e'; then
  echo "run.sh: tampaknya smoke E2E sedang berjalan - ulangi nanti" >&2; exit 2
fi

OUT="$ROOT/tests/load/results/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
WORK=$(mktemp -d)
STATS_PID=""; LOG_PID=""
# shellcheck disable=SC2317,SC2329  # dipanggil lewat trap EXIT (SC2317 = shellcheck 0.9 di runner, SC2329 = >=0.10)
restore() {
  if [[ -n "$STATS_PID" ]]; then kill "$STATS_PID" 2> /dev/null || true; fi
  if [[ -n "$LOG_PID" ]]; then kill "$LOG_PID" 2> /dev/null || true; fi
  # Budget OTP global per jam (Redis rl:otp:budget:*) habis oleh load test -> login dev lain (smoke) 429 sampai jam
  # berikutnya (temuan G13). Hapus counter itu (dev saja; batas per IP/member tidak disentuh).
  docker exec efn-redis-1 sh -c 'A="redis-cli --tls --cacert /run/secrets/efn_ca_crt -h redis -p 6379 --user efn-bff
      --pass $(cat /run/secrets/redis_password_bff) --no-auth-warning"
    for k in $($A --scan --pattern "rl:otp:budget:*"); do $A del "$k" > /dev/null; done' 2> /dev/null || true
  echo "==> Mengembalikan api-gateway ke konfigurasi dev normal"
  "${DC[@]}" --profile core up -d --no-deps --force-recreate --no-build api-gateway > /dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap restore EXIT

wait_healthy() {
  for _ in $(seq 1 60); do
    [[ "$(docker inspect "$1" --format '{{.State.Health.Status}}')" == healthy ]] && return 0
    sleep 3
  done
  echo "run.sh: $1 tidak sehat" >&2; return 1
}

echo "==> api-gateway dengan override load test (dev saja)"
"${DC[@]}" -f "$OVERRIDE" --profile core up -d --no-deps --force-recreate --no-build api-gateway > /dev/null
wait_healthy "$BFF"
# Traefik (provider docker lewat socket-proxy) baru merutekan container baru ±20-25 s setelah recreate -> tunggu
# router api. aktif agar 404 transisi tidak tercatat sebagai kegagalan beban (temuan G13; lihat README).
for _ in $(seq 1 30); do
  [[ "$(curl -s -o /dev/null -w '%{http_code}' --cacert "$ROOT/secrets/dev_edge_crt.txt" \
        --resolve api.efn.local:443:127.0.0.1 https://api.efn.local/health)" == 200 ]] && break
  sleep 2
done

# ── 1. Satu member uji untuk skenario scout (OTP dev dari log BFF, sama dengan smoke E2E) ─────────────────────────
cp "$ROOT/scripts/smoke/bff_smoke.py" "$WORK/"
cp "$ROOT/secrets/dev_edge_crt.txt" "$WORK/edge_ca.crt"
cat "$ROOT/secrets/dev_edge_crt.txt" "$ROOT/secrets/efn_ca_crt.txt" > "$WORK/ca-bundle.crt"
docker logs -f --since 1s "$BFF" > "$WORK/bff.log" 2>&1 &
LOG_PID=$!
TRAEFIK_EDGE=$(docker inspect efn-traefik-1 --format '{{(index .NetworkSettings.Networks "efn_edge").IPAddress}}')
TRAEFIK_INT=$(docker inspect efn-traefik-1 --format '{{(index .NetworkSettings.Networks "efn_internal").IPAddress}}')
PHONE="628998$(printf '%06d' $((RANDOM * RANDOM % 1000000)))"
for sub in login consent; do
  MSYS_NO_PATHCONV=1 docker run --rm --network efn_edge --add-host "api.efn.local:${TRAEFIK_EDGE}" \
    -v "$(winpath "$WORK"):/work" -v efn-pip-cache:/root/.cache/pip \
    -e SMOKE_PHONE="$PHONE" -e SMOKE_CONSENT_VERSION="location@2026-10-06" "$PY_IMAGE" \
    python /work/bff_smoke.py "$sub" 2>&1 | grep -c '"ok": true' > /dev/null || true
done
JWT=$(sed -n 's/.*"token": "\([^"]*\)".*/\1/p' "$WORK/state.json" 2> /dev/null || true)
[[ -n "$JWT" ]] || echo "PERINGATAN: login member uji gagal -> skenario scout dilewati" >&2
kill "$LOG_PID" 2> /dev/null || true; LOG_PID=""

# ── 2. Sampel CPU/memori container selama uji ─────────────────────────────────────────────────────────────────
( while :; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' 2> /dev/null \
      | sed "s/^/$(date -u +%H:%M:%S),/" >> "$OUT/stats.csv"
    sleep 10
  done ) &
STATS_PID=$!

# ── 3. k6 ─────────────────────────────────────────────────────────────────────────────────────────────────────
echo "==> k6 (${EFN_DURATION:-5m}; login ${EFN_VU_LOGIN:-20} VU, pos ${EFN_VU_POS:-30} VU, scout ${EFN_VU_SCOUT:-5} VU)"
set +e
MSYS_NO_PATHCONV=1 docker run --rm --network efn_internal --add-host "api.efn.local:${TRAEFIK_INT}" \
  -v "$(winpath "$ROOT/tests/load"):/load:ro" -v "$(winpath "$WORK"):/work:ro" -v "$(winpath "$OUT"):/out" \
  -e SSL_CERT_FILE=/work/ca-bundle.crt \
  -e EFN_SERVICE_KEY="$(tr -d '\r\n' < "$ROOT/secrets/odoo_bff_api_key.txt")" -e EFN_JWT="$JWT" \
  -e EFN_DURATION="${EFN_DURATION:-5m}" -e EFN_VU_LOGIN="${EFN_VU_LOGIN:-20}" -e EFN_VU_POS="${EFN_VU_POS:-30}" \
  -e EFN_VU_SCOUT="${EFN_VU_SCOUT:-5}" -e EFN_EDGE_RPS="${EFN_EDGE_RPS:-20}" -e EFN_DEBUG="${EFN_DEBUG:-0}" \
  -e K6_NO_USAGE_REPORT=true \
  "$K6_IMAGE" run --quiet --summary-export /out/summary.json /load/k6/efn-fase1.js > "$OUT/k6.txt" 2>&1
K6_RC=$?
set -e
kill "$STATS_PID" 2> /dev/null || true; STATS_PID=""

# ── 4. Puncak CPU/memori per container ────────────────────────────────────────────────────────────────────────
awk -F, '{ gsub("%", "", $3); split($4, m, " / "); n=$2; if ($3+0 > cpu[n]) cpu[n]=$3+0; if (!(n in mem) || length(m[1]) > 0) last[n]=m[1] }
     END { for (n in cpu) printf "%-40s cpu_max=%6.1f%%  mem_last=%s\n", n, cpu[n], last[n] }' "$OUT/stats.csv" \
  | sort -t= -k2 -rn > "$OUT/peaks.txt" || true
tail -n 60 "$OUT/k6.txt"
echo "--- puncak CPU (10 teratas) ---"; sort -k2 -t= -rn "$OUT/peaks.txt" | head -10
echo "Hasil: $OUT (k6 rc=${K6_RC})"
exit "$K6_RC"
