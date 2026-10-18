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
#   - (G17) filter odoo-rpc-gateway tanpa mTLS masuk (CN api-gateway) / mTLS keluar verify ke odoo-tls; BFF ke Odoo
#     tidak lewat gateway; gateway di-label Traefik / publish port; secret API key online / HMAC event di-mount ke
#     service selain BFF (+ Odoo untuk HMAC); saklar dev EFN_PAYMENT_SIMULATOR / ODOO_ONLINE_MOCK aktif di render prod
#   - (G20) secret kanal di luar gl/pb/bcr/bcf; env ODOO_API_KEY_ONLINE_<KODE>_FILE tanpa secret ter-mount yang cocok;
#     render dev tanpa ketiga kunci PII Odoo (odoo_pii_key_v1, odoo_pii_bidx_key, odoo_pii_name_key) di service odoo
#   - (at rest) service metabase tanpa secret metabase_encryption_key / dengan MB_ENCRYPTION_SECRET_KEY di env /
#     dengan override entrypoint apa pun termasuk `[]`/`''` / dengan image resmi metabase/metabase (G16b, SEC-86)
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

  # 3b) ghostunnel mode client (bridge E12): wajib --cacert + --verify-cn, listen hanya loopback, tanpa
  #     --disable-authentication / --unsafe-listen
  while IFS=$'\t' read -r svc bad cacert verify loopback; do
    [[ -z "$svc" ]] && continue
    [[ "$bad" == "0" ]] || err "[$label] $svc: ghostunnel client dengan --disable-authentication/--unsafe-listen dilarang"
    [[ "$cacert" == "1" && "$verify" -ge 1 ]] || err "[$label] $svc: ghostunnel client tanpa --cacert/--verify-cn"
    [[ "$loopback" == "1" ]] || err "[$label] $svc: ghostunnel client harus --listen=127.0.0.1:<port>"
  done < <(jq -r '
      .services | to_entries[] | select((.value.image // "") | startswith("ghostunnel/")) |
      (.value.command // []) as $c | select(($c | index("client")) != null) |
      [.key,
       ([$c[] | select(. == "--disable-authentication" or . == "--unsafe-listen")] | length | tostring),
       (if ([$c[] | select(startswith("--cacert="))] | length) > 0 then "1" else "0" end),
       ([$c[] | select(startswith("--verify-cn="))] | length | tostring),
       (if ([$c[] | select(startswith("--listen=127.0.0.1:"))] | length) > 0 then "1" else "0" end)] | @tsv' <<< "$json")

  # 4) At rest Metabase (G16b): service metabase wajib me-mount secret metabase_encryption_key, tidak boleh membawa
  #    MB_ENCRYPTION_SECRET_KEY di environment (terlihat di docker inspect), dan tidak boleh meng-override entrypoint
  #    image (efn-metabase-entrypoint = satu-satunya jalur kunci -> env, fail-closed).
  if jq -e '.services.metabase != null' <<< "$json" > /dev/null; then
    jq -e '[.services.metabase.secrets[]? | (.source // .)] | index("metabase_encryption_key") != null' <<< "$json" > /dev/null \
      || err "[$label] metabase: secret metabase_encryption_key tidak di-mount (detail koneksi tersimpan plaintext)"
    jq -e '(.services.metabase.environment // {}) | has("MB_ENCRYPTION_SECRET_KEY") | not' <<< "$json" > /dev/null \
      || err "[$label] metabase: MB_ENCRYPTION_SECRET_KEY di environment dilarang (pakai secret file)"
    # SEC-86: `docker compose config --format json` SELALU memuat key `entrypoint` (null = ENTRYPOINT image). Nilai
    # apa pun selain null adalah override, termasuk `entrypoint: []` / `''` (keduanya ter-render `[]` = MENGHAPUS
    # ENTRYPOINT image -> run_metabase.sh tanpa kunci). Diuji docker compose v2 (9 Okt 2026).
    jq -e '.services.metabase.entrypoint == null' <<< "$json" > /dev/null \
      || err "[$label] metabase: override entrypoint (termasuk [] / '') melewati efn-metabase-entrypoint (kunci enkripsi at rest)"
    # Image resmi metabase/metabase tidak memuat wrapper -> tanpa kunci. Hanya image EFN (infra/metabase/Dockerfile).
    jq -e '(.services.metabase.image // "") | test("^(docker\\.io/)?(library/)?metabase/metabase([:@]|$)") | not' \
      <<< "$json" > /dev/null \
      || err "[$label] metabase: image resmi metabase/metabase tanpa efn-metabase-entrypoint (pakai image EFN)"
    # Pengecualian Trivy DS-0002 (.trivyignore.yaml) bergantung pada ini (SEC-85, re-review G16c)
    jq -e '(.services.metabase.cap_drop // []) as $d | (.services.metabase.cap_add // []) as $a
           | ($d | index("ALL")) != null and (($a - ["CHOWN", "SETUID", "SETGID"]) | length) == 0' <<< "$json" > /dev/null \
      || err "[$label] metabase: wajib cap_drop [ALL] dan cap_add hanya CHOWN/SETUID/SETGID (dasar pengecualian DS-0002)"
  fi

  # 5) G17 (docs/04 §10.4.1/§10.10, ADR-034): jalur BFF -> Odoo /json/2 hanya lewat filter odoo-rpc-gateway.
  if jq -e '.services["odoo-rpc-gateway"] != null' <<< "$json" > /dev/null; then
    jq -e '(.services["api-gateway"].environment.ODOO_RPC_URL // "") == "https://odoo-rpc-gateway:8443"' <<< "$json" > /dev/null \
      || err "[$label] api-gateway: ODOO_RPC_URL wajib https://odoo-rpc-gateway:8443 (bukan Odoo langsung / plaintext)"
    jq -e '(.services["odoo-rpc-gateway"].labels // {}) | keys | map(select(startswith("traefik."))) | length == 0' \
      <<< "$json" > /dev/null || err "[$label] odoo-rpc-gateway: tidak boleh di-label Traefik (internal saja)"
    jq -e '(.services["odoo-rpc-gateway"].ports // []) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] odoo-rpc-gateway: tidak boleh mem-publish port"
    jq -e '[.services["odoo-tls"].command[]? | select(. == "--allow-cn=odoo-rpc-gateway")] | length == 1' <<< "$json" > /dev/null \
      || err "[$label] odoo-tls: --allow-cn=odoo-rpc-gateway hilang (gateway tidak bisa mTLS ke Odoo)"
    # BFF tidak boleh diizinkan langsung ke sidecar Odoo (melewati filter 10 path)
    if jq -e '[.services | to_entries[] | select(.key | test("^odoo(-ws)?-tls$")) | .value.command[]?
               | select(. == "--allow-cn=api-gateway")] | length > 0' <<< "$json" > /dev/null; then
      err "[$label] odoo-tls/odoo-ws-tls: --allow-cn=api-gateway dilarang (BFF wajib lewat odoo-rpc-gateway)"
    fi
  fi
  # 6) Least privilege secret online (docs/04 §10.10): API key Odoo kanal hanya BFF; HMAC event hanya Odoo + BFF.
  #    G19: SEMUA secret odoo_api_key_online_* yang ter-render + kunci PII Odoo odoo_pii_* hanya service odoo
  #    (ADR-036 K-36-3 / §0; dev). G20 (docs/04 §12.1): kanal = kode PT -> hanya gl/pb/bcr/bcf; kode lama ditolak.
  local sec allowed
  local -a secs
  mapfile -t secs < <(jq -r '.secrets // {} | keys[] | select(test("^(odoo_api_key_online_[a-z0-9_]+|odoo_pii_[a-z0-9_]+)$"))' <<< "$json")
  for sec in "${secs[@]}"; do
    [[ "$sec" == odoo_api_key_online_* ]] || continue
    [[ "$sec" =~ ^odoo_api_key_online_(gl|pb|bcr|bcf)$ ]] \
      || err "[$label] secret $sec bukan kanal G20 (gl/pb/bcr/bcf; RTL/FNB & kanal G19 dihapus, docs/04 §12.1)"
  done
  # Env BFF ODOO_API_KEY_ONLINE_<KODE>_FILE wajib menunjuk /run/secrets/odoo_api_key_online_<kode> yang DI-MOUNT ke BFF
  # (env tanpa secret = kanal gagal start; secret tanpa env = key ter-mount tetapi tidak terpakai).
  while IFS=$'\t' read -r key val mounted; do
    [[ -z "$key" ]] && continue
    [[ "$val" == "/run/secrets/odoo_api_key_online_${key,,}" && "$mounted" == 1 ]] \
      || err "[$label] api-gateway ODOO_API_KEY_ONLINE_${key}_FILE=${val} tidak cocok dengan secret ter-mount odoo_api_key_online_${key,,}"
  done < <(jq -r '.services["api-gateway"] // {} | [.secrets[]? | (.source // .)] as $m
      | (.environment // {}) | to_entries[] | select(.key | test("^ODOO_API_KEY_ONLINE_[A-Z0-9_]+_FILE$"))
      | (.key | capture("^ODOO_API_KEY_ONLINE_(?<k>[A-Z0-9_]+)_FILE$") | .k) as $k
      | [$k, (.value // ""), (if ($m | index("odoo_api_key_online_" + ($k | ascii_downcase))) != null then "1" else "0" end)]
      | @tsv' <<< "$json")
  # Kanal default GL (base) & HMAC event selalu diperiksa eksplisit (nama tetap), sisanya dari render
  for sec in odoo_api_key_online_gl odoo_online_event_hmac "${secs[@]}"; do
    allowed='["api-gateway"]'
    [[ "$sec" == "odoo_online_event_hmac" ]] && allowed='["api-gateway","odoo"]'
    [[ "$sec" == odoo_pii_* ]] && allowed='["odoo"]'
    jq -e --arg s "$sec" --argjson ok "$allowed" '
        [.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null) | .key]
        | (. - $ok) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret $sec di-mount ke service selain $allowed (least privilege, docs/04 §10.10)"
  done
  # 6b) G20 E-38a (ADR-036 §0, docs/04 §12.5): render dev wajib me-mount KETIGA kunci PII Odoo ke odoo (key ring v1,
  #     blind index, token nama) dengan env *_FILE yang cocok; tanpa kunci token nama pencarian nama terenkripsi gagal.
  if [[ "$label" == dev ]]; then
    local pk
    for pk in odoo_pii_key_v1:EFN_ODOO_PII_KEY_V1_FILE odoo_pii_bidx_key:EFN_ODOO_PII_BIDX_KEY_FILE \
              odoo_pii_name_key:EFN_ODOO_PII_NAME_KEY_FILE; do
      jq -e --arg s "${pk%%:*}" --arg e "${pk#*:}" '
          ([.services.odoo.secrets[]? | (.source // .)] | index($s) != null)
          and ((.services.odoo.environment // {})[$e] == ("/run/secrets/" + $s))' <<< "$json" > /dev/null \
        || err "[$label] odoo: secret ${pk%%:*} tidak di-mount atau ${pk#*:} != /run/secrets/${pk%%:*} (ADR-036 §0)"
    done
  fi

  # 7) G17: saklar khusus dev tidak boleh aktif di render prod (simulator bayar frontend, stub json2 BFF L-7)
  if [[ "$label" == prod ]]; then
    jq -e '(.services["superapp-web"].environment.EFN_PAYMENT_SIMULATOR // "false") == "false"' <<< "$json" > /dev/null \
      || err "[$label] superapp-web: EFN_PAYMENT_SIMULATOR harus false di prod (simulator pembayaran hanya dev)"
    jq -e '(.services["api-gateway"].environment.ODOO_ONLINE_MOCK // "false") == "false"' <<< "$json" > /dev/null \
      || err "[$label] api-gateway: ODOO_ONLINE_MOCK dilarang di prod (stub json2 hanya dev/test, L-7)"
    jq -e '(.services.etl.environment.ETL_AUDIT_EPOCH_AUTO // "false") == "false"' <<< "$json" > /dev/null \
      || err "[$label] etl: ETL_AUDIT_EPOCH_AUTO dilarang di prod (epoch otomatis hanya dev; restore = penanda efn_audit.chain_epoch)"
  fi

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
  # G17 (docs/04 §10.4.1): filter /json/2 - mTLS masuk (verify required + CA) dan mTLS keluar ke odoo-tls
  hap="${ROOT_DIR}/infra/odoo-rpc-gateway/haproxy.cfg"
  grep -qE '^[[:space:]]*bind[[:space:]]+:8443[[:space:]]+ssl[[:space:]].*ca-file[[:space:]]+/run/secrets/efn_ca_crt[[:space:]]+verify[[:space:]]+required' "$hap" \
    || err "infra/odoo-rpc-gateway/haproxy.cfg: listener :8443 tanpa ssl + 'ca-file ... verify required' (mTLS)"
  grep -qE '^[[:space:]]*acl[[:space:]]+cn_bff[[:space:]]+ssl_c_s_dn\(CN\)[[:space:]]+-m[[:space:]]+str[[:space:]]+api-gateway$' "$hap" \
    || err "infra/odoo-rpc-gateway/haproxy.cfg: allowlist CN klien harus persis api-gateway"
  grep -qE '^[[:space:]]*server[[:space:]].*[[:space:]]ssl[[:space:]]+verify[[:space:]]+required[[:space:]]+ca-file[[:space:]].*[[:space:]]crt[[:space:]].*[[:space:]]verifyhost[[:space:]]+odoo([[:space:]]|$)' "$hap" \
    || err "infra/odoo-rpc-gateway/haproxy.cfg: backend Odoo tanpa 'ssl verify required ca-file .. crt .. verifyhost odoo'"
  if grep -qE 'verify[[:space:]]+none|^[[:space:]]*(stats|option[[:space:]]+forwardfor)' "$hap"; then
    err "infra/odoo-rpc-gateway/haproxy.cfg: verify none / stats / forwardfor dilarang"
  fi
  echo "check-encryption: file konfigurasi infra diperiksa"
}

MODE="${1:-all}"
case "$MODE" in
  files) check_files ;;
  all)
    check_compose dev -f docker-compose.yml -f docker-compose.dev.yml
    check_compose prod -f docker-compose.yml -f docker-compose.prod.yml
    # G19: render prod + SEMUA overlay kanal online (secret kanal hanya ter-render bila di-mount) -> cek least privilege
    online_overlays=()
    for f in "$COMPOSE_DIR"/docker-compose.online-*.yml; do
      [[ -f "$f" ]] && online_overlays+=(-f "$(basename -- "$f")")
    done
    check_compose prod-online -f docker-compose.yml -f docker-compose.prod.yml "${online_overlays[@]}"
    check_files
    ;;
  *) echo "pemakaian: $0 [all|files]" >&2; exit 2 ;;
esac

if (( fail )); then
  echo "check-encryption: GAGAL - lihat docs/04 §7.1 / ADR-022 (pengecualian hanya lewat ADR)" >&2
  exit 1
fi
echo "check-encryption: OK - tidak ada konfigurasi plaintext"
