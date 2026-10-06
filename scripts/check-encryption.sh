#!/usr/bin/env bash
# Lint "enkripsi di setiap transaksi data" (docs/04 §7.1, ADR-022). Dijalankan CI job `infra` (dan `make lint-enc`).
# Gagal (exit 1) bila konfigurasi ter-render compose (dev & prod) atau file konfigurasi infra memuat:
#   - URL plaintext: http://, ws://, redis://, amqp:// (kecuali http(s)/ws ke 127.0.0.1/localhost = loopback di
#     network namespace yang sama), postgres(ql):// / DSN tanpa sslmode=verify-full
#   - sslmode selain verify-full, MINIO_USE_SSL/CH_SECURE != true, EFN_ALLOW_PLAINTEXT=true, *_INSECURE=true
#   - WALG_S3_ENDPOINT bukan https
#   - service Traefik tanpa `loadbalancer.server.scheme=https` + serversTransport (CA internal)
#   - insecure_skip_verify / insecureSkipVerify: true
#   - scrape Prometheus tanpa `scheme: https`
#   - sidecar ghostunnel dengan --disable-authentication atau tanpa --cacert/--allow-cn (mTLS, docs/04 §8.5)
#   - web-config exporter tanpa client_auth_type RequireAndVerifyClientCert; Loki tidak loopback (SEC-35)
#   - filter L7 s3-gateway (HAProxy) tanpa TLS masuk atau tanpa `ssl verify required` ke storage (ADR-030)
# Pengecualian hanya lewat ADR-022; tambahkan di sini dengan komentar alasan.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${ROOT_DIR}/infra/compose"
PROFILES=(--profile core --profile data --profile ai --profile observability --profile ops)
fail=0
err() { echo "::error::check-encryption: $*"; fail=1; }

command -v jq > /dev/null || { echo "jq dibutuhkan" >&2; exit 2; }

URL_RE='(http|ws)://[^[:space:]"]+'
PLAIN_SCHEME_RE='(redis|amqp)://'
LOOPBACK_RE='^(http|ws)://(127\.0\.0\.1|localhost)([:/]|$)'
WEAK_SSLMODE_RE='sslmode=(disable|allow|prefer|require|verify-ca)([^a-z-]|$)'
COMMENT_RE='^[[:space:]]*(#|//|--|<!--|;)'

check_value() {
  # $1 label, $2 service, $3 key, $4 value
  local label="$1" svc="$2" key="$3" v="${4,,}"
  if [[ "$v" =~ $URL_RE ]]; then
    local url="${BASH_REMATCH[0]}"
    [[ "$url" =~ $LOOPBACK_RE ]] || err "[$label] $svc $key: URL plaintext '$url'"
  fi
  if [[ "$v" =~ $PLAIN_SCHEME_RE ]]; then
    err "[$label] $svc $key: skema plaintext ${BASH_REMATCH[1]}:// (gunakan rediss/amqps)"
  fi
  if [[ "$v" == *postgres://* || "$v" == *postgresql://* || "$key" == "DATA_SOURCE_URI" || "$key" == "MB_DB_CONNECTION_URI" ]]; then
    [[ "$v" == *sslmode=verify-full* ]] || err "[$label] $svc $key: koneksi PostgreSQL tanpa sslmode=verify-full"
  fi
  [[ "$v" =~ $WEAK_SSLMODE_RE ]] && err "[$label] $svc $key: sslmode lemah"
  case "$key" in
    PG_SSLMODE|PGSSLMODE|ODOO_PG_SSLMODE) [[ "$v" == "verify-full" ]] || err "[$label] $svc $key=$v (wajib verify-full)" ;;
    MINIO_USE_SSL|CH_SECURE) [[ "$v" == "true" ]] || err "[$label] $svc $key=$v (wajib true)" ;;
    EFN_ALLOW_PLAINTEXT) [[ "$v" == "true" || "$v" == "1" ]] && err "[$label] $svc EFN_ALLOW_PLAINTEXT dilarang di compose" ;;
    *INSECURE*) [[ "$v" == "true" || "$v" == "1" ]] && err "[$label] $svc $key=$v" ;;
    WALG_S3_ENDPOINT) [[ "$v" == https://* ]] || err "[$label] $svc WALG_S3_ENDPOINT harus https" ;;
  esac
  return 0
}

check_compose() {
  local label="$1"; shift
  local json
  command -v docker > /dev/null || { echo "docker (compose) dibutuhkan" >&2; exit 2; }
  json="$(cd "$COMPOSE_DIR" && docker compose "$@" "${PROFILES[@]}" config --format json)"

  # 1) environment + command + entrypoint per service
  while IFS=$'\t' read -r svc key val; do
    [[ -z "$svc" ]] && continue
    check_value "$label" "$svc" "$key" "$val"
  done < <(jq -r '
      .services | to_entries[] | .key as $s |
      ( ((.value.environment // {}) | to_entries[] | [$s, .key, ((.value // "") | tostring)]),
        ((.value.command // []) | (if type == "array" then .[] else . end) | [$s, "command", tostring]),
        ((.value.entrypoint // []) | (if type == "array" then .[] else . end) | [$s, "entrypoint", tostring]) )
      | @tsv' <<< "$json")

  # 2) Setiap service Traefik wajib https + serversTransport
  while IFS=$'\t' read -r svc tsvc scheme transport; do
    [[ -z "$svc" ]] && continue
    [[ "$scheme" == "https" ]] || err "[$label] $svc: traefik service '$tsvc' tanpa loadbalancer.server.scheme=https"
    [[ -n "$transport" ]] || err "[$label] $svc: traefik service '$tsvc' tanpa serversTransport (CA internal)"
  done < <(jq -r '
      .services | to_entries[] | .key as $s | (.value.labels // {}) as $l |
      $l | keys[] | select(test("^traefik\\.http\\.services\\.[^.]+\\.loadbalancer\\.server\\.port$")) |
      capture("^traefik\\.http\\.services\\.(?<n>[^.]+)\\.") | .n as $n |
      [$s, $n, ($l["traefik.http.services.\($n).loadbalancer.server.scheme"] // ""),
       ($l["traefik.http.services.\($n).loadbalancer.serversTransport"] // "")] | @tsv' <<< "$json")

  # 3) mTLS sidecar ghostunnel (docs/04 §8.5, SEC-20): tidak boleh --disable-authentication; wajib --cacert + --allow-cn
  while IFS=$'\t' read -r svc dis cacert allow; do
    [[ -z "$svc" ]] && continue
    [[ "$dis" == "0" ]] || err "[$label] $svc: ghostunnel --disable-authentication dilarang (mTLS wajib, ADR-022)"
    [[ "$cacert" == "1" && "$allow" -ge 1 ]] || err "[$label] $svc: ghostunnel server tanpa --cacert/--allow-cn"
  done < <(jq -r '
      .services | to_entries[] | select((.value.image // "") | startswith("ghostunnel/")) |
      (.value.command // []) as $c | select(($c | index("server")) != null) |
      [.key,
       ([$c[] | select(. == "--disable-authentication")] | length | tostring),
       (if ([$c[] | select(startswith("--cacert="))] | length) > 0 then "1" else "0" end),
       ([$c[] | select(startswith("--allow-cn=") or startswith("--allow-uri="))] | length | tostring)] | @tsv' <<< "$json")

  echo "check-encryption: compose ${label} diperiksa"
}

check_files() {
  local hit content url
  # URL plaintext di file konfigurasi infra (baris komentar diabaikan)
  while IFS= read -r hit; do
    content="${hit#*:}"; content="${content#*:}"
    [[ "$content" =~ $COMMENT_RE ]] && continue
    if [[ "${content,,}" =~ $URL_RE ]]; then
      url="${BASH_REMATCH[0]}"
      [[ "$url" =~ $LOOPBACK_RE ]] || err "$hit  -> URL plaintext"
    fi
    if [[ "${content,,}" =~ $PLAIN_SCHEME_RE ]]; then err "$hit  -> skema plaintext"; fi
  done < <(find "${ROOT_DIR}/infra" -type f \
             \( -name '*.yml' -o -name '*.yaml' -o -name '*.xml' -o -name '*.conf' -o -name '*.sh' -o -name '*.tpl' \) \
             -not -path '*/grafana/provisioning/dashboards/*' -print0 \
           | xargs -0 grep -nHiE '(http|ws|redis|amqp)://' || true)

  # Verifikasi TLS tidak boleh dimatikan
  if grep -rnE 'insecure_skip_verify:[[:space:]]*true|insecureSkipVerify:[[:space:]]*true' "${ROOT_DIR}/infra"; then
    err "verifikasi TLS dimatikan di file di atas"
  fi

  # Setiap scrape job Prometheus wajib https (+1 untuk blok alerting.alertmanagers)
  local prom="${ROOT_DIR}/infra/observability/prometheus.yml" jobs https
  jobs=$(grep -cE '^[[:space:]]*- job_name:' "$prom")
  https=$(grep -cE '^[[:space:]]*(- )?scheme:[[:space:]]*https' "$prom")
  (( https >= jobs + 1 )) || err "prometheus.yml: ${jobs} job tetapi hanya $((https - 1)) yang scheme https"

  # mTLS endpoint observability (docs/04 §8.5)
  local f
  for f in web-config.yml web-config-ui.yml; do
    grep -qE '^[[:space:]]*client_auth_type:[[:space:]]*RequireAndVerifyClientCert' "${ROOT_DIR}/infra/observability/$f" \
      || err "infra/observability/$f: tanpa client_auth_type RequireAndVerifyClientCert"
  done
  # SEC-35: Loki hanya loopback (pola E1); TLS + allowlist CN oleh sidecar ghostunnel loki-tls (dicek di compose)
  grep -qE '^[[:space:]]*http_listen_address:[[:space:]]*127\.0\.0\.1' "${ROOT_DIR}/infra/observability/loki.yml" \
    || err "infra/observability/loki.yml: HTTP Loki harus 127.0.0.1 (akses network hanya lewat sidecar mTLS loki-tls)"
  # ADR-030: filter L7 object storage wajib re-encrypt ke storage dengan verifikasi CA (tidak ada hop plaintext)
  local hap="${ROOT_DIR}/infra/s3-gateway/haproxy.cfg"
  grep -qE '^[[:space:]]*server[[:space:]].*[[:space:]]ssl[[:space:]]+verify[[:space:]]+required[[:space:]]+ca-file[[:space:]]' "$hap" \
    || err "infra/s3-gateway/haproxy.cfg: backend storage tanpa 'ssl verify required ca-file'"
  grep -qE '^[[:space:]]*bind[[:space:]]+:9000[[:space:]]+ssl[[:space:]]' "$hap" \
    || err "infra/s3-gateway/haproxy.cfg: listener :9000 tanpa ssl"
  if grep -qE 'verify[[:space:]]+none' "$hap"; then err "infra/s3-gateway/haproxy.cfg: verify none dilarang"; fi
  echo "check-encryption: file konfigurasi infra diperiksa"
}

MODE="${1:-all}"
case "$MODE" in
  files) check_files ;;
  all)
    check_compose dev -f docker-compose.yml -f docker-compose.dev.yml
    check_compose prod -f docker-compose.yml -f docker-compose.prod.yml
    check_files
    ;;
  *) echo "pemakaian: $0 [all|files]" >&2; exit 2 ;;
esac

if (( fail )); then
  echo "check-encryption: GAGAL - lihat docs/04 §7.1 / ADR-022 (pengecualian hanya lewat ADR)" >&2
  exit 1
fi
echo "check-encryption: OK - tidak ada konfigurasi plaintext"
