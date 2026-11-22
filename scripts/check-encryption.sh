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
#   - (G20/G24) secret kanal di luar gl/pb/bcr/bcf/wtz; env ODOO_API_KEY_ONLINE_<KODE>_FILE tanpa secret ter-mount yang cocok;
#     render dev tanpa ketiga kunci PII Odoo (odoo_pii_key_v1, odoo_pii_bidx_key, odoo_pii_name_key) di service odoo
#   - (G21) loyalty_policy_signing_key di luar odoo + api-gateway atau tidak di-mount ke keduanya; render prod tanpa
#     overlay PII me-mount odoo_pii_*; render prod-pii (docker-compose.pii.yml) tanpa ketiga kunci / tanpa
#     EFN_ODOO_PII_REQUIRED=true
#   - (G22) fnb_table_token_key di luar api-gateway / odoo_fnb_event_hmac di luar odoo + api-gateway atau tidak di-mount
#   - (G23) odoo_booking_event_hmac di luar odoo + api-gateway atau tidak di-mount; odoo_api_key_booking_<pt> selain pb/bcf
#           atau env ODOO_API_KEY_BOOKING_<PT>_FILE tanpa secret ter-mount
#   - (G27) odoo_api_key_promo_<pt> selain pb, di-mount selain api-gateway, atau env ODOO_API_KEY_PROMO_<PT>_FILE tanpa
#           secret ter-mount
#     dengan env *_FILE yang cocok; secret QR di luar pb/bcf; ODOO_API_KEY_QR_<PT>_FILE tanpa secret ter-mount;
#     LOYALTY_POINT_EXPIRY_DAYS (dihapus D-22-18) di env BFF; kill switch QR/poin online/hangus bukan false di render prod;
#     LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT bukan true (SEC-171) / job ETL KDS/region/recon online bukan false di render prod
#   - (G41) esb_core_password_* di luar esb-extract; clickhouse_password_esb di luar clickhouse-init/esb-extract; network
#           esb dipakai selain clickhouse/esb-extract; esb-extract di network selain esb/egress-esb, network egress-esb
#           dipakai selain esb-extract atau tanpa enable_icc=false (SEC-183), root, tanpa read_only/cap_drop ALL,
#           ESB_BASE_URL bukan https://services.esb.co.id/; ESB_ENABLED / ESB_ALLOW_IGNORE_WINDOW bukan default false di
#           render prod-esb; ESB_USER_* ter-render berisi nilai di render prod-esb tanpa .env, atau nilai ESB_USER_*
#           di-hardcode di overlay/.env.example; username akun ESB lama tertulis di repo (SEC-180)
#   - (G42) insight_sso_key di luar odoo/insight-web, insight_session_key di luar insight-web, insight_scope_key /
#           clickhouse_password_insight di luar clickhouse-init/insight-web, tls_insight_web_key di luar insight-web-tls,
#           atau secret wajib tidak di-mount / env *_FILE tidak cocok; network insight-ingress dipakai selain
#           traefik/insight-web/blackbox-exporter, insight-data selain clickhouse/insight-web; insight-web di network lain
#           (egress/internal/data), tanpa read_only/cap_drop ALL; insight-web-tls --allow-cn selain traefik/blackbox-exporter;
#           CH_URL / EFN_INSIGHT_ODOO_URL / EFN_INSIGHT_URL bukan https://; INSIGHT_ENABLED bukan boolean, atau bukan
#           default false di render prod (insight-web + odoo)
#   - (G62) login-gateway dengan secret apa pun, di network selain login-ingress, tanpa read_only/cap_drop ALL, atau
#           LOGIN_GATEWAY_*_URL bukan https://; network login-ingress beranggota selain traefik/login-gateway/
#           blackbox-exporter; tls_login_gateway_key di luar login-gateway-tls; --allow-cn selain traefik/blackbox-exporter
#   - (G44) odoo_api_key_forecast_* selain smd|pb atau di luar forecast-worker; clickhouse_password_forecast di luar
#           clickhouse-init/forecast-worker (atau tidak di clickhouse-init); tls_forecast_worker_* di luar forecast-worker;
#           network forecast-ch selain clickhouse/forecast-worker, forecast-rpc selain odoo-rpc-gateway/forecast-worker;
#           forecast-worker di network lain, root, tanpa read_only/cap_drop ALL, ODOO_RPC_URL bukan
#           https://odoo-rpc-gateway:8443, env *_FILE tidak cocok; FORECAST_ENABLED bukan default false di render
#           prod-forecast; ACL CN haproxy odoo-rpc-gateway selain cn_bff=api-gateway / cn_forecast=forecast-worker
#   - (G50) secret kampanye di luar service §28.10 (behavior_*_pepper/email_*/odoo_api_key_campaign_smd -> api-gateway;
#           odoo_api_key_segment_smd/tls_segment_worker_* -> segment-worker; clickhouse_password_segment -> clickhouse-init
#           + segment-worker; wa_unofficial_* sesuai §28.9 + prometheus untuk token metrik) atau env *_FILE tidak cocok;
#           network segment-*/rmq/wa-*/mail beranggota lain atau tidak internal (wa-internet: enable_icc=false);
#           segment-worker / wa-unofficial / wa-egress-proxy tanpa hardening; WA_UNOFFICIAL_PROXY_URL di env; port 9466
#           dipublish; flag G50 bukan default false di render prod/prod-segment/prod-wa-unofficial; ACL CN gateway
#           selain tiga CN, path kampanye bocor antar-CN
#   - (G43) clickhouse_password_mig di luar clickhouse-init/esb-migrate; migration_bundle_key di luar esb-migrate/odoo,
#           atau di odoo tanpa overlay docker-compose.migration.yml; esb-migrate di network selain esb (egress), dengan
#           port/label Traefik, root, tanpa read_only/cap_drop ALL/tmpfs /work, di luar profil migrate, CH bukan https
#           8443 + CA internal, env *_FILE tanpa secret ter-mount; overlay migration menambah network ke odoo atau
#           me-mount kode migrasi read-write (render prod-esb-migrate, prod-migration)
#   - (SEC-215/SEC-223) clickhouse-init tanpa CH_FORECAST_READER_HOST_CIDR /24../32 atau != subnet ipam forecast-ch;
#           user efn_forecast_reader tidak dibuat dengan HOST IP; pagar allowlist objek efn_forecast hilang; pagar
#           definisi view efn_forecast (SQL SECURITY DEFINER + definer) hilang; pola sama untuk efn_segment_reader
#           (CH_SEGMENT_READER_HOST_CIDR = subnet ipam segment-ch, HOST IP $SEGMENT_CIDR, K25-06)
#   - (at rest) service metabase tanpa secret metabase_encryption_key / dengan MB_ENCRYPTION_SECRET_KEY di env /
#     dengan override entrypoint apa pun termasuk `[]`/`''` / dengan image resmi metabase/metabase (G16b, SEC-86)
#   - (K25-06, identitas stack) nama project != EFN_STACK; network tanpa nama `<EFN_STACK>_<key>` atau subnet di luar
#     `<EFN_SUBNET_BASE>.<n>.0/24` / duplikat; router Traefik tanpa traefik.enable + label traefik.docker.network
#     `<EFN_STACK>_<network yang dipakai service dan Traefik>`; postgres di network selain `data` (pg_hba samenet);
#     alias scrape api-gateway-metrics / ocr-worker-metrics (internal), wa-admin-relay-ingress (wa-ingress) hilang atau
#     di network lain; TRUSTED_PROXIES tanpa `<EFN_SUBNET_BASE>.1.0/24`; IP statis relay WA / WA_RELAY_BIND_IP != <base>.21.10,
#     extra_hosts wa-unofficial / IP wa-egress-proxy != <base>.14.10; Traefik/Promtail tanpa render-config.sh + EFN_STACK;
#     stack selain `efn` memakai tag image lokal bersama (ghcr.io/efn/<svc>:dev, efn/<svc>:dev). Render tambahan
#     EFN_STACK=efn-prod EFN_SUBNET_BASE=10.250 (prod + local-edge + esb + forecast + segment + wa-unofficial) dan
#     EFN_STACK=efn-dev EFN_SUBNET_BASE=10.252 (dev + forecast + segment + wa-unofficial) wajib lolos SEMUA aturan.
#     File: traefik.yml (constraint + network ber-placeholder), promtail.yml (filter project), prometheus.yml (tanpa nama
#     network / subnet literal), pg_hba.conf (samenet, tanpa CIDR literal), wa-egress/admin-relay.cfg (bind env + .alert).
# Pengecualian hanya lewat ADR-022; tambahkan di sini dengan komentar alasan.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${ROOT_DIR}/infra/compose"
PROFILES=(--profile core --profile data --profile ai --profile observability --profile ops)
fail=0
err() { echo "::error::check-encryption: $*"; fail=1; }
# K25-06: identitas stack paksaan untuk render berikutnya (kosong = dari env shell / infra/compose/.env / default).
CC_STACK=""
CC_BASE=""

# Nilai efektif EFN_STACK / EFN_SUBNET_BASE seperti yang dilihat compose: paksaan render > env shell > .env > default.
stack_setting() {
  local var="$1" def="$2" v=""
  case "$var" in
    EFN_STACK) v="$CC_STACK" ;;
    EFN_SUBNET_BASE) v="$CC_BASE" ;;
  esac
  [[ -n "$v" ]] || v="${!var:-}"
  if [[ -z "$v" && -f "${COMPOSE_DIR}/.env" ]]; then
    v="$(sed -n "s/^[[:space:]]*${var}=//p" "${COMPOSE_DIR}/.env" | tail -n 1)"
    v="${v%%[[:space:]#]*}"
    v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
  fi
  printf '%s' "${v:-$def}"
}

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
  local json kind="$label"
  # K25-06: render stack paksaan (prod-stack/dev-stack) memakai aturan prod/dev penuh.
  case "$label" in
    prod-stack) kind=prod ;;
    dev-stack) kind=dev ;;
  esac
  local -a envp=()
  [[ -n "$CC_STACK" ]] && envp+=("EFN_STACK=${CC_STACK}")
  [[ -n "$CC_BASE" ]] && envp+=("EFN_SUBNET_BASE=${CC_BASE}")
  command -v docker > /dev/null || { echo "docker (compose) dibutuhkan" >&2; exit 2; }
  json="$(cd "$COMPOSE_DIR" && env "${envp[@]}" docker compose "$@" "${PROFILES[@]}" config --format json)"

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
    # BFF tidak boleh diizinkan langsung ke sidecar Odoo (melewati filter 15 path)
    if jq -e '[.services | to_entries[] | select(.key | test("^odoo(-ws)?-tls$")) | .value.command[]?
               | select(. == "--allow-cn=api-gateway")] | length > 0' <<< "$json" > /dev/null; then
      err "[$label] odoo-tls/odoo-ws-tls: --allow-cn=api-gateway dilarang (BFF wajib lewat odoo-rpc-gateway)"
    fi
  fi
  # 6) Least privilege secret online (docs/04 §10.10): API key Odoo kanal hanya BFF; HMAC event hanya Odoo + BFF.
  #    G19: SEMUA secret odoo_api_key_online_* yang ter-render + kunci PII Odoo odoo_pii_* hanya service odoo
  #    (ADR-036 K-36-3 / §0; dev). G20 (docs/04 §12.1): kanal = kode PT -> hanya gl/pb/bcr/bcf (G24: + wtz); kode lama ditolak.
  local sec allowed
  local -a secs
  mapfile -t secs < <(jq -r '.secrets // {} | keys[] | select(test("^(odoo_api_key_online_[a-z0-9_]+|odoo_api_key_qr_[a-z0-9_]+|odoo_api_key_booking_[a-z0-9_]+|odoo_api_key_promo_[a-z0-9_]+|odoo_pii_[a-z0-9_]+)$"))' <<< "$json")
  for sec in "${secs[@]}"; do
    if [[ "$sec" == odoo_api_key_promo_* ]]; then
      # G27 (docs/04 §18.2): user teknis promo hanya untuk PT yang punya overlay promo (EFN_PROMO_COMPANY_CODES)
      [[ "$sec" =~ ^odoo_api_key_promo_(pb)$ ]] || err "[$label] secret $sec bukan PT promo (pb, docs/04 §18.2)"
      continue
    fi
    if [[ "$sec" == odoo_api_key_booking_* ]]; then
      # G23 (docs/04 §15.3): user teknis reservasi hanya untuk PT F&B PB/BCF
      [[ "$sec" =~ ^odoo_api_key_booking_(pb|bcf)$ ]] || err "[$label] secret $sec bukan PT F&B reservasi (pb/bcf, docs/04 §15.3)"
      continue
    fi
    if [[ "$sec" == odoo_api_key_qr_* ]]; then
      # G22 (docs/04 §14.A.5): user teknis QR hanya untuk PT F&B PB/BCF
      [[ "$sec" =~ ^odoo_api_key_qr_(pb|bcf)$ ]] || err "[$label] secret $sec bukan PT F&B QR (pb/bcf, docs/04 §14.A.5)"
      continue
    fi
    [[ "$sec" == odoo_api_key_online_* ]] || continue
    [[ "$sec" =~ ^odoo_api_key_online_(gl|pb|bcr|bcf|wtz)$ ]] \
      || err "[$label] secret $sec bukan kanal G20/G24 (gl/pb/bcr/bcf/wtz; RTL/FNB & kanal G19 dihapus, docs/04 §12.1/§16.1)"
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
  # G22 (docs/04 §14.A.9): env BFF ODOO_API_KEY_QR_<PT>_FILE wajib menunjuk secret QR PT itu yang DI-MOUNT ke BFF
  while IFS=$'\t' read -r key val mounted; do
    [[ -z "$key" ]] && continue
    [[ "$val" == "/run/secrets/odoo_api_key_qr_${key,,}" && "$mounted" == 1 ]] \
      || err "[$label] api-gateway ODOO_API_KEY_QR_${key}_FILE=${val} tidak cocok dengan secret ter-mount odoo_api_key_qr_${key,,}"
  done < <(jq -r '.services["api-gateway"] // {} | [.secrets[]? | (.source // .)] as $m
      | (.environment // {}) | to_entries[] | select(.key | test("^ODOO_API_KEY_QR_[A-Z0-9_]+_FILE$"))
      | (.key | capture("^ODOO_API_KEY_QR_(?<k>[A-Z0-9_]+)_FILE$") | .k) as $k
      | [$k, (.value // ""), (if ($m | index("odoo_api_key_qr_" + ($k | ascii_downcase))) != null then "1" else "0" end)]
      | @tsv' <<< "$json")
  # G23 (docs/04 §15.3): env BFF ODOO_API_KEY_BOOKING_<PT>_FILE wajib menunjuk secret booking PT itu yang DI-MOUNT ke BFF
  while IFS=$'\t' read -r key val mounted; do
    [[ -z "$key" ]] && continue
    [[ "$val" == "/run/secrets/odoo_api_key_booking_${key,,}" && "$mounted" == 1 ]] \
      || err "[$label] api-gateway ODOO_API_KEY_BOOKING_${key}_FILE=${val} tidak cocok dengan secret ter-mount odoo_api_key_booking_${key,,}"
  done < <(jq -r '.services["api-gateway"] // {} | [.secrets[]? | (.source // .)] as $m
      | (.environment // {}) | to_entries[] | select(.key | test("^ODOO_API_KEY_BOOKING_[A-Z0-9_]+_FILE$"))
      | (.key | capture("^ODOO_API_KEY_BOOKING_(?<k>[A-Z0-9_]+)_FILE$") | .k) as $k
      | [$k, (.value // ""), (if ($m | index("odoo_api_key_booking_" + ($k | ascii_downcase))) != null then "1" else "0" end)]
      | @tsv' <<< "$json")
  # G27 (docs/04 §18.2): env BFF ODOO_API_KEY_PROMO_<PT>_FILE wajib menunjuk secret promo PT itu yang DI-MOUNT ke BFF
  while IFS=$'\t' read -r key val mounted; do
    [[ -z "$key" ]] && continue
    [[ "$val" == "/run/secrets/odoo_api_key_promo_${key,,}" && "$mounted" == 1 ]] \
      || err "[$label] api-gateway ODOO_API_KEY_PROMO_${key}_FILE=${val} tidak cocok dengan secret ter-mount odoo_api_key_promo_${key,,}"
  done < <(jq -r '.services["api-gateway"] // {} | [.secrets[]? | (.source // .)] as $m
      | (.environment // {}) | to_entries[] | select(.key | test("^ODOO_API_KEY_PROMO_[A-Z0-9_]+_FILE$"))
      | (.key | capture("^ODOO_API_KEY_PROMO_(?<k>[A-Z0-9_]+)_FILE$") | .k) as $k
      | [$k, (.value // ""), (if ($m | index("odoo_api_key_promo_" + ($k | ascii_downcase))) != null then "1" else "0" end)]
      | @tsv' <<< "$json")
  # Kanal default GL (base) & HMAC event selalu diperiksa eksplisit (nama tetap), sisanya dari render.
  # G22: kunci token sesi meja (BFF saja) & HMAC event QR (Odoo + BFF).
  for sec in odoo_api_key_online_gl odoo_online_event_hmac loyalty_policy_signing_key fnb_table_token_key \
             odoo_fnb_event_hmac odoo_booking_event_hmac "${secs[@]}"; do
    allowed='["api-gateway"]'
    [[ "$sec" == "odoo_online_event_hmac" || "$sec" == "loyalty_policy_signing_key" || "$sec" == "odoo_fnb_event_hmac" \
       || "$sec" == "odoo_booking_event_hmac" ]] \
      && allowed='["api-gateway","odoo"]'
    [[ "$sec" == odoo_pii_* ]] && allowed='["odoo"]'
    jq -e --arg s "$sec" --argjson ok "$allowed" '
        [.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null) | .key]
        | (. - $ok) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret $sec di-mount ke service selain $allowed (least privilege, docs/04 §10.10)"
  done
  # 6b) G37 (K25-01, docs/04 §22): API key 9router hanya ocr-worker, dengan NINEROUTER_API_KEY_FILE yang cocok; ocr-worker
  #     di network egress HANYA bila key 9router di-mount (overlay docker-compose.llm.yml), selain itu ADR-014 (no egress).
  jq -e '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index("ninerouter_api_key") != null)
          | .key] - ["ocr-worker", "text2sql"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] secret ninerouter_api_key di-mount ke service selain ocr-worker/text2sql (least privilege, docs/04 §22/§23)"
  # 6b2) G38 (docs/04 §23.6): secret Text-to-SQL least privilege; text2sql di egress HANYA bila key 9router di-mount
  #      (overlay docker-compose.llm-text2sql.yml) dengan NINEROUTER_API_KEY_FILE yang cocok.
  local t2s
  for t2s in "text2sql_api_key:odoo,text2sql" "text2sql_scope_key:clickhouse-init,text2sql" \
             "clickhouse_password_text2sql:clickhouse-init,text2sql" "tls_text2sql_key:text2sql"; do
    jq -e --arg s "${t2s%%:*}" --arg ok "${t2s#*:}" '
        [.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null) | .key]
        - ($ok | split(",")) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret ${t2s%%:*} di-mount ke service selain ${t2s#*:} (least privilege, docs/04 §23.6)"
  done
  jq -e '(.services["text2sql"] // null) as $w | $w == null
         or (([$w.secrets[]? | (.source // .)] | index("ninerouter_api_key") != null) as $k
             | (($w.networks // {}) | has("egress")) as $e
             | (($k | not) and ($e | not))
               or ($k and $e and (($w.environment // {}).NINEROUTER_API_KEY_FILE == "/run/secrets/ninerouter_api_key")))' \
      <<< "$json" > /dev/null \
    || err "[$label] text2sql: egress tanpa ninerouter_api_key (ADR-014) atau NINEROUTER_API_KEY_FILE tidak cocok (docs/04 §23)"
  jq -e '(.services["ocr-worker"] // null) as $w | $w == null
         or (([$w.secrets[]? | (.source // .)] | index("ninerouter_api_key") != null) as $k
             | (($w.networks // {}) | has("egress")) as $e
             | (($k | not) and ($e | not))
               or ($k and $e and (($w.environment // {}).NINEROUTER_API_KEY_FILE == "/run/secrets/ninerouter_api_key")))' \
      <<< "$json" > /dev/null \
    || err "[$label] ocr-worker: egress tanpa ninerouter_api_key (ADR-014) atau NINEROUTER_API_KEY_FILE tidak cocok (docs/04 §22)"
  # 6b3) G41 (docs/04 §25, ADR-040): ekstraksi ESB Core (overlay docker-compose.esb.yml). Password ESB per brand HANYA
  #      esb-extract; clickhouse_password_esb hanya clickhouse-init + esb-extract; network `esb` hanya clickhouse +
  #      esb-extract; esb-extract hanya di network esb + egress-esb (bukan egress bersama/data/internal, SEC-183),
  #      egress-esb hanya esb-extract dan enable_icc=false, non-root, read_only, cap_drop ALL,
  #      ESB_BASE_URL wajib host ESB https (satu-satunya tujuan egress yang diizinkan, ditegakkan aplikasi), dan setiap
  #      ESB_PASSWORD_<BRAND>_FILE menunjuk secret brand itu yang DI-MOUNT.
  jq -e '[.services | to_entries[]
          | select([.value.secrets[]? | (.source // .) | startswith("esb_core_password_")] | any) | .key]
         - ["esb-extract"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] secret esb_core_password_* di-mount ke service selain esb-extract (least privilege, docs/04 §25)"
  jq -e '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index("clickhouse_password_esb") != null)
          | .key] - ["clickhouse-init", "esb-extract"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] secret clickhouse_password_esb di-mount ke service selain clickhouse-init/esb-extract (docs/04 §25)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("esb")) | .key]
         - ["clickhouse", "esb-extract", "esb-migrate"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network esb dipakai service selain clickhouse/esb-extract/esb-migrate (docs/04 §25/§27.2)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("egress-esb")) | .key]
         - ["esb-extract"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network egress-esb dipakai service selain esb-extract (SEC-183)"
  jq -e '(.networks["egress-esb"] // null) as $n | $n == null
         or ((($n.driver_opts // {})["com.docker.network.bridge.enable_icc"] // "" | tostring) == "false")' \
      <<< "$json" > /dev/null \
    || err "[$label] network egress-esb wajib driver_opts com.docker.network.bridge.enable_icc=\"false\" (SEC-183)"
  jq -e '(.services["esb-extract"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) - ["esb", "egress-esb"] | length == 0)
             and ((($w.environment // {}).ESB_BASE_URL // "") | startswith("https://services.esb.co.id/"))
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.user // "") | tostring) | test("^[1-9][0-9]*(:[0-9]+)?$")))' <<< "$json" > /dev/null \
    || err "[$label] esb-extract: wajib hanya network esb+egress-esb, ESB_BASE_URL https://services.esb.co.id/, read_only, cap_drop ALL, user non-root (docs/04 §25)"
  while IFS=$'\t' read -r key val mounted; do
    [[ -z "$key" ]] && continue
    [[ "$val" == "/run/secrets/esb_core_password_${key,,}" && "$mounted" == 1 ]] \
      || err "[$label] esb-extract ESB_PASSWORD_${key}_FILE=${val} tidak cocok dengan secret ter-mount esb_core_password_${key,,}"
  done < <(jq -r '.services["esb-extract"] // {} | [.secrets[]? | (.source // .)] as $m
      | (.environment // {}) | to_entries[] | select(.key | test("^ESB_PASSWORD_[A-Z0-9]+_FILE$"))
      | (.key | capture("^ESB_PASSWORD_(?<k>[A-Z0-9]+)_FILE$") | .k) as $k
      | [$k, (.value // ""), (if ($m | index("esb_core_password_" + ($k | ascii_downcase))) != null then "1" else "0" end)]
      | @tsv' <<< "$json")
  # 6b3a) G43 (docs/04 §27.2/§27.10, ADR-042): migrasi ESB -> Odoo. clickhouse_password_mig hanya clickhouse-init +
  #       esb-migrate; migration_bundle_key hanya esb-migrate + odoo, dan odoo HANYA di render overlay
  #       docker-compose.migration.yml (jendela migrasi). esb-migrate: hanya network esb (tanpa egress/data/internal),
  #       tanpa port/label Traefik, non-root, read_only, cap_drop ALL, profil `migrate` saja (tidak ikut `up`),
  #       ClickHouse https 8443 + CA internal, tmpfs /work, env *_FILE menunjuk secret yang DI-MOUNT. Overlay migration
  #       me-mount kode scripts/migration ke odoo hanya-baca.
  jq -e '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index("clickhouse_password_mig") != null)
          | .key] - ["clickhouse-init", "esb-migrate"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] secret clickhouse_password_mig di-mount ke service selain clickhouse-init/esb-migrate (docs/04 §27.10)"
  jq -e '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index("migration_bundle_key") != null)
          | .key] - ["esb-migrate", "odoo"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] secret migration_bundle_key di-mount ke service selain esb-migrate/odoo (docs/04 §27.2)"
  if [[ "$label" != prod-migration ]]; then
    jq -e '(.services.odoo // null) as $o | $o == null
           or (([$o.secrets[]? | (.source // .)] | index("migration_bundle_key")) == null
               and (($o.environment // {}) | has("EFN_MIG_BUNDLE_KEY_FILE") | not))' <<< "$json" > /dev/null \
      || err "[$label] odoo: migration_bundle_key / EFN_MIG_BUNDLE_KEY_FILE hanya lewat overlay docker-compose.migration.yml (jendela migrasi, docs/04 §27.2)"
  fi
  jq -e '(.services["esb-migrate"] // null) as $w | $w == null
         or ((($w.networks // {}) | keys) == ["esb"]
             and (($w.ports // []) | length == 0)
             and ((($w.labels // {}) | keys | map(select(startswith("traefik."))) | length) == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.user // "") | tostring) | test("^[1-9][0-9]*:[1-9][0-9]*$"))
             and (($w.profiles // []) == ["migrate"])
             and (($w.environment.CH_SECURE // "") == "true")
             and (($w.environment.CH_PORT // "") == "8443")
             and (($w.environment.CH_CA_FILE // "") == "/run/secrets/efn_ca_crt")
             and (($w.environment // {}) | has("EFN_ALLOW_PLAINTEXT") | not)
             and ([$w.tmpfs[]? | tostring | select(startswith("/work"))] | length == 1))' <<< "$json" > /dev/null \
    || err "[$label] esb-migrate: wajib hanya network esb (tanpa egress), tanpa port/Traefik, read_only, cap_drop ALL, user non-root uid:gid, profil migrate saja, CH https 8443 + CA internal, tanpa EFN_ALLOW_PLAINTEXT, tmpfs /work (docs/04 §27.2)"
  local mig menv msec
  for mig in CH_PASSWORD_FILE:clickhouse_password_mig MIG_BUNDLE_KEY_FILE:migration_bundle_key CH_CA_FILE:efn_ca_crt; do
    IFS=: read -r menv msec <<< "$mig"
    jq -e --arg e "$menv" --arg s "$msec" '
        (.services["esb-migrate"] == null)
        or (([.services["esb-migrate"].secrets[]? | (.source // .)] | index($s) != null)
            and ((.services["esb-migrate"].environment // {})[$e] == ("/run/secrets/" + $s)))' <<< "$json" > /dev/null \
      || err "[$label] esb-migrate: secret ${msec} tidak di-mount atau ${menv} != /run/secrets/${msec} (docs/04 §27.2)"
  done
  if [[ "$label" == prod-esb-migrate || "$label" == prod-migration ]]; then
    jq -e '.services["esb-migrate"] != null' <<< "$json" > /dev/null \
      || err "[$label] esb-migrate tidak ter-render (overlay docker-compose.esb.yml / --profile migrate)"
  fi
  if [[ "$label" == prod-migration ]]; then
    jq -e '(([.services.odoo.secrets[]? | (.source // .)] | index("migration_bundle_key")) != null)
           and ((.services.odoo.environment // {}).EFN_MIG_BUNDLE_KEY_FILE == "/run/secrets/migration_bundle_key")
           and ([.services.odoo.volumes[]? | select(.target == "/opt/efn-migration") | select(.read_only != true)]
                | length == 0)
           and ((.services.odoo.networks // {}) | keys | sort) == ["data", "internal"]' <<< "$json" > /dev/null \
      || err "[$label] odoo: overlay migration wajib me-mount migration_bundle_key (EFN_MIG_BUNDLE_KEY_FILE cocok), kode /opt/efn-migration hanya-baca, tanpa network tambahan (L-129)"
  fi
  # 6b4) G42 (docs/04 §26.10/§26.11, ADR-041): dashboard insight-web + SSO Odoo. Least privilege secret (kunci SSO bocor =
  #      pemalsuan sesi semua brand, R-S18), secret wajib ter-mount dengan env *_FILE yang cocok, network khusus
  #      (insight-ingress: Traefik/blackbox; insight-data: ClickHouse), tanpa egress, https saja.
  local ins isvc ienv isec
  for ins in "insight_sso_key:odoo,insight-web" "insight_session_key:insight-web" \
             "insight_scope_key:clickhouse-init,insight-web" "clickhouse_password_insight:clickhouse-init,insight-web" \
             "tls_insight_web_key:insight-web-tls"; do
    jq -e --arg s "${ins%%:*}" --arg ok "${ins#*:}" '
        [.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null) | .key]
        - ($ok | split(",")) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret ${ins%%:*} di-mount ke service selain ${ins#*:} (least privilege, docs/04 §26.10)"
  done
  for ins in odoo:EFN_INSIGHT_SSO_KEY_FILE:insight_sso_key insight-web:INSIGHT_SSO_KEY_FILE:insight_sso_key \
             insight-web:INSIGHT_SESSION_KEY_FILE:insight_session_key insight-web:INSIGHT_SCOPE_KEY_FILE:insight_scope_key \
             insight-web:CH_PASSWORD_FILE:clickhouse_password_insight; do
    IFS=: read -r isvc ienv isec <<< "$ins"
    jq -e --arg svc "$isvc" --arg e "$ienv" --arg s "$isec" '
        (.services[$svc] == null)
        or (([.services[$svc].secrets[]? | (.source // .)] | index($s) != null)
            and ((.services[$svc].environment // {})[$e] == ("/run/secrets/" + $s)))' <<< "$json" > /dev/null \
      || err "[$label] ${isvc}: secret ${isec} tidak di-mount atau ${ienv} != /run/secrets/${isec} (docs/04 §26.10)"
  done
  # clickhouse-init WAJIB membawa kedua secret insight (tanpa itu init gagal di semua env, docs/04 §26.7)
  jq -e '(.services["clickhouse-init"] // null) as $c | $c == null
         or (([$c.secrets[]? | (.source // .)]) as $m
             | ($m | index("clickhouse_password_insight")) != null and ($m | index("insight_scope_key")) != null)' \
      <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: secret clickhouse_password_insight + insight_scope_key wajib di-mount (docs/04 §26.7)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("insight-ingress")) | .key]
         - ["traefik", "insight-web", "blackbox-exporter"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network insight-ingress dipakai service selain traefik/insight-web/blackbox-exporter (docs/04 §26.11)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("insight-data")) | .key]
         - ["clickhouse", "insight-web"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network insight-data dipakai service selain clickhouse/insight-web (docs/04 §26.11)"
  jq -e '(.services["insight-web"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) - ["insight-ingress", "insight-data"] | length == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.environment // {}).CH_URL // "") | startswith("https://"))
             and ((($w.environment // {}).EFN_INSIGHT_ODOO_URL // "") | startswith("https://"))
             and ((($w.environment // {}).INSIGHT_ENABLED // "") | tostring | test("^(true|false)$")))' \
      <<< "$json" > /dev/null \
    || err "[$label] insight-web: wajib hanya network insight-ingress+insight-data (tanpa egress), read_only, cap_drop ALL, CH_URL & EFN_INSIGHT_ODOO_URL https://, INSIGHT_ENABLED boolean (docs/04 §26.10/§26.11)"
  jq -e '(.services["insight-web-tls"] // null) as $t | $t == null
         or ([$t.command[]? | select(startswith("--allow-cn=")) | ltrimstr("--allow-cn=")]
             - ["traefik", "blackbox-exporter"] | length == 0)' <<< "$json" > /dev/null \
    || err "[$label] insight-web-tls: --allow-cn hanya traefik/blackbox-exporter (docs/04 §26.11)"
  jq -e '(.services.odoo // null) as $o | $o == null
         or (((($o.environment // {}).EFN_INSIGHT_URL // "") | startswith("https://"))
             and ((($o.environment // {}).INSIGHT_ENABLED // "") | tostring | test("^(true|false)$")))' \
      <<< "$json" > /dev/null \
    || err "[$label] odoo: EFN_INSIGHT_URL wajib https:// dan INSIGHT_ENABLED boolean (docs/04 §26.2)"
  # 6b4b) G62 (docs/04 §29, ADR-050): login-gateway tanpa secret aplikasi (tidak memegang password), hanya network
  #       login-ingress (Traefik/blackbox), hardening, tujuan redirect https; kunci TLS hanya sidecar; mTLS CN terbatas.
  jq -e '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index("tls_login_gateway_key") != null) | .key]
         - ["login-gateway-tls"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] secret tls_login_gateway_key di-mount ke service selain login-gateway-tls (docs/04 §29)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("login-ingress")) | .key]
         - ["traefik", "login-gateway", "blackbox-exporter"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network login-ingress dipakai service selain traefik/login-gateway/blackbox-exporter (docs/04 §29)"
  jq -e '(.services["login-gateway"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) == ["login-ingress"])
             and ((($w.secrets // []) | length) == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.environment // {}).LOGIN_GATEWAY_ERP_URL // "") | startswith("https://"))
             and ((($w.environment // {}).LOGIN_GATEWAY_INSIGHT_URL // "") | tostring | (. == "" or startswith("https://"))))' \
      <<< "$json" > /dev/null \
    || err "[$label] login-gateway: wajib hanya network login-ingress, tanpa secret, read_only, cap_drop ALL, LOGIN_GATEWAY_*_URL https:// (docs/04 §29)"
  jq -e '(.services["login-gateway-tls"] // null) as $t | $t == null
         or ([$t.command[]? | select(startswith("--allow-cn=")) | ltrimstr("--allow-cn=")]
             - ["traefik", "blackbox-exporter"] | length == 0)' <<< "$json" > /dev/null \
    || err "[$label] login-gateway-tls: --allow-cn hanya traefik/blackbox-exporter (docs/04 §29)"
  # 6b5) G44 (docs/04 §28.2/§28.10, ADR-046): forecast-worker (overlay docker-compose.forecast.yml). API key Odoo forecast
  #      per PT HANYA forecast-worker dan hanya PT yang punya user teknis (smd, pb); clickhouse_password_forecast hanya
  #      clickhouse-init + forecast-worker (clickhouse-init WAJIB me-mount-nya); kunci klien mTLS hanya forecast-worker;
  #      network forecast-ch hanya clickhouse + forecast-worker, forecast-rpc hanya odoo-rpc-gateway + forecast-worker;
  #      forecast-worker hanya di kedua network itu, non-root, read_only, cap_drop ALL, Odoo hanya lewat
  #      https://odoo-rpc-gateway:8443, env *_FILE cocok dengan secret ter-mount, FORECAST_ENABLED boolean.
  local fsec
  while IFS= read -r fsec; do
    [[ -z "$fsec" ]] && continue
    [[ "$fsec" =~ ^odoo_api_key_forecast_(smd|pb)$ ]] || err "[$label] secret $fsec bukan PT forecast (smd|pb, docs/04 §28.2)"
    jq -e --arg s "$fsec" '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null)
            | .key] - ["forecast-worker"] | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret $fsec di-mount ke service selain forecast-worker (least privilege, docs/04 §28.10)"
  done < <(jq -r '.secrets // {} | keys[] | select(startswith("odoo_api_key_forecast_"))' <<< "$json")
  for ins in "clickhouse_password_forecast:clickhouse-init,forecast-worker" "tls_forecast_worker_key:forecast-worker" \
             "tls_forecast_worker_crt:forecast-worker"; do
    jq -e --arg s "${ins%%:*}" --arg ok "${ins#*:}" '
        [.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null) | .key]
        - ($ok | split(",")) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret ${ins%%:*} di-mount ke service selain ${ins#*:} (least privilege, docs/04 §28.2)"
  done
  jq -e '(.services["clickhouse-init"] // null) as $c | $c == null
         or ([$c.secrets[]? | (.source // .)] | index("clickhouse_password_forecast") != null)' <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: secret clickhouse_password_forecast wajib di-mount (user efn_forecast_reader, G44)"
  # SEC-215: user efn_forecast_reader HOST IP = subnet network forecast-ch. clickhouse-init wajib meneruskan
  # CH_FORECAST_READER_HOST_CIDR; bila overlay forecast ter-render (satu host), nilainya HARUS sama dengan ipam
  # forecast-ch (CIDR lain = worker ditolak login, atau HOST lebih lebar dari network yang dimaksud).
  jq -e '(.services["clickhouse-init"] // null) as $c | $c == null
         or ((($c.environment // {}).CH_FORECAST_READER_HOST_CIDR // "") | test("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/(2[4-9]|3[0-2])$"))' \
      <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: CH_FORECAST_READER_HOST_CIDR wajib CIDR IPv4 /24../32 (HOST user efn_forecast_reader, SEC-215/SEC-223)"
  jq -e '(.networks["forecast-ch"] // null) as $n | (.services["clickhouse-init"] // null) as $c
         | $n == null or $c == null
         or ((($c.environment // {}).CH_FORECAST_READER_HOST_CIDR // "") == (($n.ipam.config // [])[0].subnet // "-"))' \
      <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: CH_FORECAST_READER_HOST_CIDR != subnet ipam network forecast-ch (SEC-215)"
  # K25-06 + pola SEC-215: user efn_segment_reader HOST IP = subnet network segment-ch (overlay segment).
  jq -e '(.services["clickhouse-init"] // null) as $c | $c == null
         or ((($c.environment // {}).CH_SEGMENT_READER_HOST_CIDR // "") | test("^[0-9]{1,3}(\\.[0-9]{1,3}){3}/(2[4-9]|3[0-2])$"))' \
      <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: CH_SEGMENT_READER_HOST_CIDR wajib CIDR IPv4 /24../32 (HOST user efn_segment_reader, pola SEC-215/SEC-223)"
  jq -e '(.networks["segment-ch"] // null) as $n | (.services["clickhouse-init"] // null) as $c
         | $n == null or $c == null
         or ((($c.environment // {}).CH_SEGMENT_READER_HOST_CIDR // "") == (($n.ipam.config // [])[0].subnet // "-"))' \
      <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: CH_SEGMENT_READER_HOST_CIDR != subnet ipam network segment-ch (pola SEC-215)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("forecast-ch")) | .key]
         - ["clickhouse", "forecast-worker"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network forecast-ch dipakai service selain clickhouse/forecast-worker (docs/04 §28.2)"
  jq -e '[.services | to_entries[] | select((.value.networks // {}) | has("forecast-rpc")) | .key]
         - ["odoo-rpc-gateway", "forecast-worker"] | length == 0' <<< "$json" > /dev/null \
    || err "[$label] network forecast-rpc dipakai service selain odoo-rpc-gateway/forecast-worker (docs/04 §28.2)"
  jq -e '(.services["forecast-worker"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) - ["forecast-ch", "forecast-rpc"] | length == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.user // "") | tostring) | test("^[1-9][0-9]*(:[0-9]+)?$"))
             and ((($w.environment // {}).ODOO_RPC_URL // "") == "https://odoo-rpc-gateway:8443")
             and ((($w.environment // {}).FORECAST_ENABLED // "") | tostring | test("^(true|false)$")))' \
      <<< "$json" > /dev/null \
    || err "[$label] forecast-worker: wajib hanya network forecast-ch+forecast-rpc, read_only, cap_drop ALL, user non-root, ODOO_RPC_URL https://odoo-rpc-gateway:8443, FORECAST_ENABLED boolean (docs/04 §28.2)"
  for ins in ODOO_RPC_CA_FILE:efn_ca_crt ODOO_RPC_CLIENT_CERT_FILE:tls_forecast_worker_crt \
             ODOO_RPC_CLIENT_KEY_FILE:tls_forecast_worker_key CH_PASSWORD_FILE:clickhouse_password_forecast \
             CH_CA_FILE:efn_ca_crt; do
    jq -e --arg e "${ins%%:*}" --arg s "${ins#*:}" '(.services["forecast-worker"] // null) as $w | $w == null
        or (([$w.secrets[]? | (.source // .)] | index($s) != null)
            and (($w.environment // {})[$e] == ("/run/secrets/" + $s)))' <<< "$json" > /dev/null \
      || err "[$label] forecast-worker: secret ${ins#*:} tidak di-mount atau ${ins%%:*} != /run/secrets/${ins#*:} (docs/04 §28.2)"
  done
  while IFS=$'\t' read -r key val mounted; do
    [[ -z "$key" ]] && continue
    [[ "$val" == "/run/secrets/odoo_api_key_forecast_${key,,}" && "$mounted" == 1 ]] \
      || err "[$label] forecast-worker ODOO_API_KEY_FORECAST_${key}_FILE=${val} tidak cocok dengan secret ter-mount odoo_api_key_forecast_${key,,}"
  done < <(jq -r '.services["forecast-worker"] // {} | [.secrets[]? | (.source // .)] as $m
      | (.environment // {}) | to_entries[] | select(.key | test("^ODOO_API_KEY_FORECAST_[A-Z0-9]+_FILE$"))
      | (.key | capture("^ODOO_API_KEY_FORECAST_(?<k>[A-Z0-9]+)_FILE$") | .k) as $k
      | [$k, (.value // ""), (if ($m | index("odoo_api_key_forecast_" + ($k | ascii_downcase))) != null then "1" else "0" end)]
      | @tsv' <<< "$json")
  # 6b6) G50 (docs/04 §28.7/§28.9/§28.10, ADR-044/047/048): secret kampanye supermarket hanya di service yang tercantum
  #      (§28.10 baris terakhir), network overlay segment/wa-unofficial terisolasi, hardening service baru.
  local gsec gok spec svc e s
  for gsec in "behavior_subject_pepper:api-gateway" "behavior_device_pepper:api-gateway" \
              "email_smtp_password:api-gateway" "email_unsubscribe_key:api-gateway" \
              "clickhouse_password_segment:clickhouse-init,segment-worker" \
              "tls_segment_worker_crt:segment-worker" "tls_segment_worker_key:segment-worker" \
              "wa_unofficial_hash_key:api-gateway,wa-unofficial" "wa_unofficial_session_key:wa-unofficial" \
              "wa_unofficial_admin_token:wa-unofficial" "wa_unofficial_metrics_token:wa-unofficial,prometheus" \
              "rabbitmq_password_wa_unofficial:rabbitmq,wa-unofficial" \
              "tls_wa_unofficial_crt:wa-unofficial" "tls_wa_unofficial_key:wa-unofficial"; do
    gok="${gsec#*:}"
    jq -e --arg s "${gsec%%:*}" --arg ok "$gok" '
        [.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null) | .key]
        - ($ok | split(",")) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret ${gsec%%:*} di-mount ke service selain ${gok} (least privilege, docs/04 §28.10)"
  done
  # API key Odoo kampanye per PT: user teknis efn_campaign_<pt> HANYA BFF; efn_segment_<pt> HANYA segment-worker (smd)
  while IFS= read -r fsec; do
    [[ -z "$fsec" ]] && continue
    if [[ "$fsec" == odoo_api_key_campaign_* ]]; then gok=api-gateway; else gok=segment-worker; fi
    [[ "$fsec" =~ ^odoo_api_key_(campaign|segment)_(smd)$ ]] || err "[$label] secret $fsec bukan PT kampanye (smd, docs/04 §28.7)"
    jq -e --arg s "$fsec" --arg ok "$gok" '[.services | to_entries[] | select([.value.secrets[]? | (.source // .)] | index($s) != null)
            | .key] - [$ok] | length == 0' <<< "$json" > /dev/null \
      || err "[$label] secret $fsec di-mount ke service selain $gok (least privilege, docs/04 §28.7)"
  done < <(jq -r '.secrets // {} | keys[] | select(test("^odoo_api_key_(campaign|segment)_"))' <<< "$json")
  jq -e '(.services["clickhouse-init"] // null) as $c | $c == null
         or ([$c.secrets[]? | (.source // .)] | index("clickhouse_password_segment") != null)' <<< "$json" > /dev/null \
    || err "[$label] clickhouse-init: secret clickhouse_password_segment wajib di-mount (user efn_segment_reader, G50)"
  # BFF: env *_FILE secret G50 cocok dengan secret ter-mount (base); WA hash key hanya bila overlay me-mount-nya
  for spec in api-gateway:BEHAVIOR_SUBJECT_PEPPER_FILE:behavior_subject_pepper \
              api-gateway:BEHAVIOR_DEVICE_PEPPER_FILE:behavior_device_pepper \
              api-gateway:EMAIL_SMTP_PASSWORD_FILE:email_smtp_password \
              api-gateway:EMAIL_UNSUBSCRIBE_KEY_FILE:email_unsubscribe_key \
              api-gateway:ODOO_API_KEY_CAMPAIGN_SMD_FILE:odoo_api_key_campaign_smd \
              segment-worker:ODOO_API_KEY_CAMPAIGN_SMD_FILE:odoo_api_key_segment_smd \
              segment-worker:CH_PASSWORD_FILE:clickhouse_password_segment \
              segment-worker:CH_CA_FILE:efn_ca_crt segment-worker:ODOO_RPC_CA_FILE:efn_ca_crt \
              segment-worker:SEGMENT_BFF_CA_FILE:efn_ca_crt \
              segment-worker:SEGMENT_CLIENT_CERT_FILE:tls_segment_worker_crt \
              segment-worker:SEGMENT_CLIENT_KEY_FILE:tls_segment_worker_key \
              wa-unofficial:RABBITMQ_PASSWORD_FILE:rabbitmq_password_wa_unofficial \
              wa-unofficial:WA_UNOFFICIAL_SESSION_KEY_FILE:wa_unofficial_session_key \
              wa-unofficial:WA_UNOFFICIAL_HASH_KEY_FILE:wa_unofficial_hash_key \
              wa-unofficial:WA_UNOFFICIAL_ADMIN_TOKEN_FILE:wa_unofficial_admin_token \
              wa-unofficial:WA_UNOFFICIAL_METRICS_TOKEN_FILE:wa_unofficial_metrics_token \
              wa-unofficial:TLS_CERT_FILE:tls_wa_unofficial_crt wa-unofficial:TLS_KEY_FILE:tls_wa_unofficial_key \
              wa-unofficial:AMQP_TLS_CA_FILE:efn_ca_crt; do
    IFS=: read -r svc e s <<< "$spec"
    jq -e --arg svc "$svc" --arg e "$e" --arg s "$s" '
        (.services[$svc] == null)
        or (([.services[$svc].secrets[]? | (.source // .)] | index($s) != null)
            and ((.services[$svc].environment // {})[$e] == ("/run/secrets/" + $s)))' <<< "$json" > /dev/null \
      || err "[$label] ${svc}: secret ${s} tidak di-mount atau ${e} != /run/secrets/${s} (docs/04 §28.10)"
  done
  jq -e '(.services["api-gateway"] // null) as $a | $a == null
         or (([$a.secrets[]? | (.source // .)] | index("wa_unofficial_hash_key")) == null)
         or (($a.environment // {}).WA_UNOFFICIAL_HASH_KEY_FILE == "/run/secrets/wa_unofficial_hash_key")' <<< "$json" > /dev/null \
    || err "[$label] api-gateway: wa_unofficial_hash_key ter-mount tetapi WA_UNOFFICIAL_HASH_KEY_FILE tidak cocok (docs/04 §28.7)"
  # Listener segmen BFF :9466 tidak boleh dipublish ke host (hanya network segment-bff, mTLS CN segment-worker)
  jq -e '[(.services["api-gateway"].ports // [])[] | (.target // 0 | tostring)] | index("9466") == null' <<< "$json" > /dev/null \
    || err "[$label] api-gateway: port segmen 9466 tidak boleh dipublish (docs/04 §28.7)"
  # Network overlay G50: anggota persis
  for spec in "segment-ch:clickhouse,segment-worker" "segment-rpc:odoo-rpc-gateway,segment-worker" \
              "segment-bff:api-gateway,segment-worker" "rmq:rabbitmq,wa-unofficial" \
              "wa-egress:wa-unofficial,wa-egress-proxy" "wa-internet:wa-egress-proxy" \
              "wa-admin:wa-admin-relay,wa-unofficial" "wa-ingress:traefik,prometheus,wa-admin-relay" \
              "mail:api-gateway,mailpit"; do
    jq -e --arg n "${spec%%:*}" --arg ok "${spec#*:}" '[.services | to_entries[] | select((.value.networks // {}) | has($n)) | .key]
           - ($ok | split(",")) | length == 0' <<< "$json" > /dev/null \
      || err "[$label] network ${spec%%:*} dipakai service selain ${spec#*:} (docs/04 §28.7/§28.9)"
  done
  jq -e '(.networks["wa-internet"] // null) as $n | $n == null
         or ((($n.driver_opts // {})["com.docker.network.bridge.enable_icc"] // "" | tostring) == "false")' \
      <<< "$json" > /dev/null \
    || err "[$label] network wa-internet wajib driver_opts com.docker.network.bridge.enable_icc=\"false\" (pola SEC-183)"
  for spec in rmq wa-egress wa-admin wa-ingress segment-ch segment-rpc segment-bff mail; do
    jq -e --arg n "$spec" '(.networks[$n] // null) as $x | $x == null or ($x.internal == true)' <<< "$json" > /dev/null \
      || err "[$label] network ${spec} wajib internal: true (tanpa jalur keluar)"
  done
  jq -e '(.services["segment-worker"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) - ["segment-ch", "segment-rpc", "segment-bff"] | length == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.ports // []) | length) == 0)
             and ((($w.user // "") | tostring) | test("^[1-9][0-9]*(:[0-9]+)?$"))
             and ((($w.environment // {}).ODOO_RPC_URL // "") == "https://odoo-rpc-gateway:8443")
             and ((($w.environment // {}).SEGMENT_BFF_URL // "") == "https://api-gateway:9466")
             and ((($w.environment // {}).SEGMENT_ENABLED // "") | tostring | test("^(true|false)$")))' \
      <<< "$json" > /dev/null \
    || err "[$label] segment-worker: wajib hanya network segment-ch/rpc/bff, read_only, cap_drop ALL, tanpa port, user non-root, ODOO_RPC_URL https://odoo-rpc-gateway:8443, SEGMENT_BFF_URL https://api-gateway:9466, SEGMENT_ENABLED boolean (docs/04 §28.7)"
  jq -e '(.services["wa-unofficial"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) - ["rmq", "wa-egress", "wa-admin"] | length == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.cap_add // []) | length) == 0)
             and ((($w.ports // []) | length) == 0)
             and (($w.profiles // []) == ["wa-unofficial"])
             and ((($w.user // "") | tostring) | (. != "" and (test("^(0|root)(:.*)?$") | not)))
             and ((($w.environment // {}).AMQP_URL // "") | startswith("amqps://"))
             and (($w.environment // {}) | has("WA_UNOFFICIAL_PROXY_URL") | not)
             and ((($w.environment // {}).WA_UNOFFICIAL_ENABLED // "") | tostring | test("^(true|false)$"))
             and ((($w.labels // {}) | keys | map(select(startswith("traefik."))) | length) == 0))' \
      <<< "$json" > /dev/null \
    || err "[$label] wa-unofficial: wajib hanya network rmq/wa-egress/wa-admin, read_only, cap_drop ALL tanpa cap_add, tanpa port, profil wa-unofficial, user non-root, AMQP amqps://, tanpa WA_UNOFFICIAL_PROXY_URL, flag boolean, tanpa label Traefik (rute admin lewat wa-admin-relay, SEC-G50-06)"
  # SEC-G50-06: relay admin = satu-satunya jalur Traefik/Prometheus -> wa-unofficial; listen hanya IP wa-ingress
  # K25-06: IP statis = <EFN_SUBNET_BASE>.21.10, WA_RELAY_BIND_IP (bind admin-relay.cfg) = IP itu, label network
  # <EFN_STACK>_wa-ingress; extra_hosts wa-unofficial menunjuk IP statis wa-egress-proxy (<base>.14.10).
  local wstack wbase
  wstack="$(stack_setting EFN_STACK efn)"
  wbase="$(stack_setting EFN_SUBNET_BASE 172.30)"
  jq -e --arg b "$wbase" --arg s "$wstack" '(.services["wa-admin-relay"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys | sort) == ["wa-admin", "wa-ingress"])
             and ((($w.networks // {})["wa-ingress"].ipv4_address // "") == ($b + ".21.10"))
             and ((($w.environment // {}).WA_RELAY_BIND_IP // "") == ($b + ".21.10"))
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.cap_add // []) | length) == 0)
             and ((($w.ports // []) | length) == 0)
             and ((($w.labels // {})["traefik.docker.network"] // "") == ($s + "_wa-ingress"))
             and ((($w.labels // {})["traefik.http.routers.wa-admin.entrypoints"] // "") == "wg-https")
             and ((($w.labels // {})["traefik.http.routers.wa-admin.tls.options"] // "") == "efn-mtls@file")
             and ((($w.labels // {})["traefik.http.routers.wa-admin.middlewares"] // "") | split(",") | index("internal-ops@file") != null))' \
      <<< "$json" > /dev/null \
    || err "[$label] wa-admin-relay: wajib network wa-admin + wa-ingress (IP + WA_RELAY_BIND_IP ${wbase}.21.10), read_only, cap_drop ALL, tanpa port, router admin wg-https + internal-ops + efn-mtls di network ${wstack}_wa-ingress (SEC-G50-06, K25-06)"
  jq -e --arg b "$wbase" '(.services["wa-unofficial"] // null) as $w | $w == null
         or ((($w.extra_hosts // []) | if type == "object" then to_entries | map(.key + ":" + (.value | if type == "array" then .[0] else . end | tostring)) else . end
              | map(select(startswith("web.whatsapp.com"))) | map(sub("^web\\.whatsapp\\.com[:=]"; ""))) == [$b + ".14.10"])' \
      <<< "$json" > /dev/null \
    || err "[$label] wa-unofficial: extra_hosts web.whatsapp.com wajib ${wbase}.14.10 (IP statis wa-egress-proxy, K25-06)"
  jq -e --arg b "$wbase" '(.services["wa-egress-proxy"] // null) as $w | $w == null
         or ((($w.networks // {})["wa-egress"].ipv4_address // "") == ($b + ".14.10"))' <<< "$json" > /dev/null \
    || err "[$label] wa-egress-proxy: ipv4_address wa-egress wajib ${wbase}.14.10 (tujuan extra_hosts wa-unofficial, K25-06)"
  jq -e '(.services["wa-egress-proxy"] // null) as $w | $w == null
         or (((($w.networks // {}) | keys) - ["wa-egress", "wa-internet"] | length == 0)
             and ($w.read_only == true)
             and ((($w.cap_drop // []) | index("ALL")) != null)
             and ((($w.cap_add // []) | length) == 0)
             and ((($w.ports // []) | length) == 0)
             and ((($w.labels // {}) | keys | map(select(startswith("traefik."))) | length) == 0))' <<< "$json" > /dev/null \
    || err "[$label] wa-egress-proxy: wajib hanya network wa-egress/wa-internet, read_only, cap_drop ALL tanpa cap_add, tanpa port/Traefik (ADR-044 §3.6)"
  # 6a) G21 (docs/04 §13.1 no. 3, L-61): kunci tanda tangan kebijakan poin WAJIB di-mount ke odoo (penanda tangan) DAN
  #     api-gateway (pemverifikasi) dengan env *_FILE yang cocok; di luar keduanya sudah ditolak loop di atas.
  local lp
  for lp in odoo:EFN_LOYALTY_POLICY_SIGNING_KEY_FILE api-gateway:LOYALTY_POLICY_SIGNING_KEY_FILE; do
    jq -e --arg svc "${lp%%:*}" --arg e "${lp#*:}" '
        (.services[$svc] == null)
        or (([.services[$svc].secrets[]? | (.source // .)] | index("loyalty_policy_signing_key") != null)
            and ((.services[$svc].environment // {})[$e] == "/run/secrets/loyalty_policy_signing_key"))' <<< "$json" > /dev/null \
      || err "[$label] ${lp%%:*}: secret loyalty_policy_signing_key tidak di-mount atau ${lp#*:} != /run/secrets/loyalty_policy_signing_key"
  done
  # 6a2) G22 (docs/04 §14.A.2/§14.A.4): fnb_table_token_key WAJIB di api-gateway (FNB_TABLE_TOKEN_KEY_FILE);
  #      odoo_fnb_event_hmac WAJIB di odoo (EFN_FNB_EVENT_HMAC_FILE, penanda tangan) DAN api-gateway
  #      (ODOO_FNB_EVENT_HMAC_FILE, pemverifikasi). Di luar service itu sudah ditolak loop least privilege di atas.
  local spec svc e s
  #      G23 (docs/04 §15.5): odoo_booking_event_hmac sama (EFN_BOOKING_EVENT_HMAC_FILE / ODOO_BOOKING_EVENT_HMAC_FILE).
  for spec in api-gateway:FNB_TABLE_TOKEN_KEY_FILE:fnb_table_token_key odoo:EFN_FNB_EVENT_HMAC_FILE:odoo_fnb_event_hmac \
              api-gateway:ODOO_FNB_EVENT_HMAC_FILE:odoo_fnb_event_hmac \
              odoo:EFN_BOOKING_EVENT_HMAC_FILE:odoo_booking_event_hmac \
              api-gateway:ODOO_BOOKING_EVENT_HMAC_FILE:odoo_booking_event_hmac; do
    IFS=: read -r svc e s <<< "$spec"
    jq -e --arg svc "$svc" --arg e "$e" --arg s "$s" '
        (.services[$svc] == null)
        or (([.services[$svc].secrets[]? | (.source // .)] | index($s) != null)
            and ((.services[$svc].environment // {})[$e] == ("/run/secrets/" + $s)))' <<< "$json" > /dev/null \
      || err "[$label] ${svc}: secret ${s} tidak di-mount atau ${e} != /run/secrets/${s} (docs/04 §14.A.9)"
  done
  # 6a3) G22 D-22-18 (docs/04 §14.D.1): LOYALTY_POINT_EXPIRY_DAYS dihapus - tidak boleh diteruskan ke BFF di render mana pun
  #      (BFF production gagal start; hangus kini karena tidak aktif, LOYALTY_POINT_MAX_AGE_DAYS untuk umur lot).
  jq -e '(.services["api-gateway"].environment // {}) | has("LOYALTY_POINT_EXPIRY_DAYS") | not' <<< "$json" > /dev/null \
    || err "[$label] api-gateway: LOYALTY_POINT_EXPIRY_DAYS dihapus (D-22-18); pakai LOYALTY_POINT_MAX_AGE_DAYS"
  # 6b) G20 E-38a (ADR-036 §0, docs/04 §12.5): render dev wajib me-mount KETIGA kunci PII Odoo ke odoo (key ring v1,
  #     blind index, token nama) dengan env *_FILE yang cocok; tanpa kunci token nama pencarian nama terenkripsi gagal.
  #     G21 AC-13: sama untuk render prod-pii (overlay docker-compose.pii.yml) + EFN_ODOO_PII_REQUIRED=true (mode terkunci).
  #     Render prod/prod-online TANPA overlay tidak boleh me-mount kunci PII ke odoo (aktivasi PO, L-59).
  if [[ "$kind" == prod || "$kind" == prod-online ]]; then
    jq -e '[.services.odoo.secrets[]? | (.source // .) | select(startswith("odoo_pii_"))] | length == 0' <<< "$json" > /dev/null \
      || err "[$label] odoo: kunci odoo_pii_* ter-mount tanpa overlay docker-compose.pii.yml (aktivasi PII = EFN_ODOO_PII_ENABLED, L-59)"
  fi
  if [[ "$kind" == prod-pii ]]; then
    jq -e '(.services.odoo.environment // {}).EFN_ODOO_PII_REQUIRED == "true"' <<< "$json" > /dev/null \
      || err "[$label] odoo: EFN_ODOO_PII_REQUIRED wajib true di render produksi ber-PII (AC-13)"
  fi
  if [[ "$kind" == dev || "$kind" == prod-pii ]]; then
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
  if [[ "$kind" == prod ]]; then
    jq -e '(.services["superapp-web"].environment.EFN_PAYMENT_SIMULATOR // "false") == "false"' <<< "$json" > /dev/null \
      || err "[$label] superapp-web: EFN_PAYMENT_SIMULATOR harus false di prod (simulator pembayaran hanya dev)"
    jq -e '(.services["api-gateway"].environment.ODOO_ONLINE_MOCK // "false") == "false"' <<< "$json" > /dev/null \
      || err "[$label] api-gateway: ODOO_ONLINE_MOCK dilarang di prod (stub json2 hanya dev/test, L-7)"
    jq -e '(.services.etl.environment.ETL_AUDIT_EPOCH_AUTO // "false") == "false"' <<< "$json" > /dev/null \
      || err "[$label] etl: ETL_AUDIT_EPOCH_AUTO dilarang di prod (epoch otomatis hanya dev; restore = penanda efn_audit.chain_epoch)"
    # G22: kill switch default MATI di render prod (QR meja §14.A.3, poin online §14.C.1, hangus/anonimisasi L-73)
    local ks
    for ks in FNB_QR_ENABLED FNB_QR_POINTS_ENABLED ONLINE_LOYALTY_REDEEM_ENABLED ONLINE_LOYALTY_EARN_ENABLED \
              LOYALTY_EXPIRY_ENABLED LOYALTY_INACTIVE_ANONYMIZE MGM_ENABLED; do
      jq -e --arg k "$ks" '(.services["api-gateway"].environment[$k] // "false") == "false"' <<< "$json" > /dev/null \
        || err "[$label] api-gateway: ${ks} harus default false di render prod (aktivasi lewat config rilis, docs/04 §14)"
    done
    # SEC-171 (r15): filter consent notice hangus wajib diteruskan dan default true di render prod
    jq -e '(.services["api-gateway"].environment.LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT // "") == "true"' <<< "$json" > /dev/null \
      || err "[$label] api-gateway: LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT harus diteruskan dengan default true di render prod (SEC-171)"
    # G21/G22 (QA G22 P2-3): job ETL opsional default MATI di render prod (aktivasi lewat config rilis)
    for ks in ETL_PARTNER_REGION_ENABLED ETL_LOYALTY_RECON_ONLINE_ENABLED ETL_ENABLE_KDS ETL_KDS_ONLINE_ENABLED \
              ETL_ENABLE_WASTE ETL_ENABLE_FORECAST ETL_ENABLE_FORECAST_LEGACY; do
      jq -e --arg k "$ks" '(.services.etl.environment[$k] // "") == "false"' <<< "$json" > /dev/null \
        || err "[$label] etl: ${ks} harus diteruskan dengan default false di render prod (QA G22 P2-3)"
    done
    # G42 (L-125): kill switch Insight default MATI di render prod (Odoo + insight-web; aktivasi lewat config rilis +
    # EFN_ALLOW_FEATURE_ON Superadmin, deploy-validate)
    for ks in odoo insight-web; do
      jq -e --arg s "$ks" '(.services[$s] == null) or ((.services[$s].environment.INSIGHT_ENABLED // "") == "false")' \
          <<< "$json" > /dev/null \
        || err "[$label] ${ks}: INSIGHT_ENABLED harus diteruskan dengan default false di render prod (docs/04 §26.11, L-125)"
    done
    jq -e '.services["insight-web"] != null' <<< "$json" > /dev/null \
      || err "[$label] insight-web tidak ter-render (profil data, docs/04 §26.11)"
    # G50 (docs/04 §28.10): semua flag kampanye supermarket diteruskan dengan default false di render prod
    for ks in BEHAVIOR_TRACKING_ENABLED WISHLIST_ENABLED RESTOCK_ALERT_ENABLED PRICE_ALERT_ENABLED PROMO_CAMPAIGN_ENABLED \
              CAMPAIGN_FREQUENCY_CAP_ENABLED CAMPAIGN_BUDGET_CAP_ENABLED EMAIL_ENABLED WA_UNOFFICIAL_ENABLED; do
      jq -e --arg k "$ks" '(.services["api-gateway"].environment[$k] // "") == "false"' <<< "$json" > /dev/null \
        || err "[$label] api-gateway: ${ks} harus diteruskan dengan default false di render prod (docs/04 §28.10)"
    done
    jq -e '(.services.etl.environment.ETL_ENABLE_BEHAVIOR // "") == "false"' <<< "$json" > /dev/null \
      || err "[$label] etl: ETL_ENABLE_BEHAVIOR harus diteruskan dengan default false di render prod (docs/04 §28.10)"
    # Mode TLS SMTP tanpa nilai plaintext (L-160)
    jq -e '(.services["api-gateway"].environment.EMAIL_SMTP_TLS_MODE // "") | test("^(implicit|starttls)$")' <<< "$json" > /dev/null \
      || err "[$label] api-gateway: EMAIL_SMTP_TLS_MODE wajib implicit|starttls (ADR-048, L-160)"
  fi

  # 8) G41: kill switch ekstraksi ESB default MATI di render prod + overlay (aktivasi lewat .env/config, runbook esb-extract)
  if [[ "$kind" == prod-esb || "$label" == prod-stack ]]; then
    jq -e '.services["esb-extract"] != null' <<< "$json" > /dev/null \
      || err "[$label] esb-extract tidak ter-render (overlay docker-compose.esb.yml / profil data)"
    jq -e '(.services["esb-extract"].environment.ESB_ENABLED // "") == "false"' <<< "$json" > /dev/null \
      || err "[$label] esb-extract: ESB_ENABLED harus diteruskan dengan default false di render prod (docs/04 §25)"
    # SEC-185: pagar --ignore-window di production default MATI
    jq -e '(.services["esb-extract"].environment.ESB_ALLOW_IGNORE_WINDOW // "") == "false"' <<< "$json" > /dev/null \
      || err "[$label] esb-extract: ESB_ALLOW_IGNORE_WINDOW harus diteruskan dengan default false di render prod (SEC-185)"
    # SEC-180: render dari isi repo (CI menyalin .env.example -> .env) tidak boleh membawa username akun ESB. Di luar CI
    # .env lokal (gitignored) sah berisi username, jadi hanya peringatan; cek teks file repo ada di check_files.
    if ! jq -e '[(.services["esb-extract"].environment // {}) | to_entries[]
                 | select(.key | test("^ESB_USER_")) | select(((.value // "") | tostring) != "")] | length == 0' \
         <<< "$json" > /dev/null; then
      if [[ -n "${CI:-}" ]]; then
        err "[$label] esb-extract: ESB_USER_* ter-render berisi nilai dari file repo (SEC-180: username ESB hanya di .env lokal/config rilis)"
      else
        echo "check-encryption: [$label] info: ESB_USER_* berisi nilai (dari .env lokal; pastikan tidak ada di file repo)"
      fi
    fi
  fi

  # 9) G44 (docs/04 §28.10): kill switch forecast default MATI di render prod + overlay (aktivasi lewat .env/config)
  if [[ "$kind" == prod-forecast || "$label" == prod-stack ]]; then
    jq -e '.services["forecast-worker"] != null' <<< "$json" > /dev/null \
      || err "[$label] forecast-worker tidak ter-render (overlay docker-compose.forecast.yml / profil data)"
    jq -e '(.services["forecast-worker"].environment.FORECAST_ENABLED // "") == "false"' <<< "$json" > /dev/null \
      || err "[$label] forecast-worker: FORECAST_ENABLED harus diteruskan dengan default false di render prod (docs/04 §28.10)"
  fi
  # 10) G50 (docs/04 §28.10): kill switch overlay kampanye default MATI di render prod
  # K25-06: render paksaan prod-stack memuat overlay esb/forecast/segment/wa-unofficial -> aturan kill switch ikut.
  if [[ "$label" == prod-segment || "$label" == prod-stack ]]; then
    jq -e '.services["segment-worker"] != null' <<< "$json" > /dev/null \
      || err "[$label] segment-worker tidak ter-render (overlay docker-compose.segment.yml / profil data)"
    jq -e '(.services["segment-worker"].environment.SEGMENT_ENABLED // "") == "false"' <<< "$json" > /dev/null \
      || err "[$label] segment-worker: SEGMENT_ENABLED harus diteruskan dengan default false di render prod (docs/04 §28.10)"
  fi
  if [[ "$label" == prod-wa-unofficial || "$label" == prod-stack ]]; then
    jq -e '.services["wa-unofficial"] != null and .services["wa-egress-proxy"] != null and .services["wa-admin-relay"] != null' <<< "$json" > /dev/null \
      || err "[$label] wa-unofficial/wa-egress-proxy/wa-admin-relay tidak ter-render (overlay docker-compose.wa-unofficial.yml / --profile wa-unofficial)"
    for svc in wa-unofficial api-gateway; do
      jq -e --arg s "$svc" '(.services[$s].environment.WA_UNOFFICIAL_ENABLED // "") == "false"' <<< "$json" > /dev/null \
        || err "[$label] ${svc}: WA_UNOFFICIAL_ENABLED harus diteruskan dengan default false di render prod (ADR-044, docs/04 §28.10)"
    done
  fi

  check_stack_identity "$label" "$json"
  echo "check-encryption: compose ${label} diperiksa"
}

# 10) K25-06 (docs/runbooks/multi-env-local.md): stack bisa jalan dengan nama project & subnet apa pun, berdampingan
#     dengan stack EFN / project Docker lain, tanpa melonggarkan isolasi network.
check_stack_identity() {
  local label="$1" json="$2" stack base bad
  local stack_re='^[a-z][a-z0-9-]{0,30}[a-z0-9]$'
  local octet='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
  local base_re="^${octet}\\.${octet}\$"
  stack="$(stack_setting EFN_STACK efn)"
  base="$(stack_setting EFN_SUBNET_BASE 172.30)"
  [[ "$stack" =~ $stack_re ]] \
    || err "[$label] EFN_STACK '${stack}' tidak valid (^[a-z][a-z0-9-]*[a-z0-9]\$, 2..32, tanpa '_')"
  [[ "$base" =~ $base_re ]] \
    || err "[$label] EFN_SUBNET_BASE '${base}' tidak valid (dua oktet, mis. 172.30 / 10.250)"

  # (a) project compose = EFN_STACK (label com.docker.compose.project = dasar constraint Traefik + filter Promtail)
  jq -e --arg s "$stack" '.name == $s' <<< "$json" > /dev/null \
    || err "[$label] nama project '$(jq -r '.name' <<< "$json")' != EFN_STACK '${stack}' (jangan memakai -p berbeda)"

  # (b) setiap network bernama <stack>_<key>, subnet <base>.<n>.0/24 (n 0..255), tanpa duplikat
  bad="$(jq -r --arg s "$stack" '.networks // {} | to_entries[] | select((.value.name // "") != ($s + "_" + .key)) | .key' <<< "$json")"
  [[ -z "$bad" ]] || err "[$label] network tanpa nama ${stack}_<key>: ${bad//$'\n'/ }"
  bad="$(jq -r --arg b "$base" '.networks // {} | to_entries[] | .key as $k
      | [(.value.ipam.config // [])[] | (.subnet // "")] as $sn
      | select(($sn | length) == 0 or any($sn[]; (startswith($b + ".") and test("^[0-9]+\\.[0-9]+\\.[0-9]{1,3}\\.0/24$")) | not))
      | $k' <<< "$json")"
  [[ -z "$bad" ]] || err "[$label] network tanpa subnet ${base}.<n>.0/24: ${bad//$'\n'/ }"
  jq -e '[.networks // {} | .[] | (.ipam.config // [])[] | .subnet] | length == (unique | length)' <<< "$json" > /dev/null \
    || err "[$label] subnet network duplikat"

  # (c) router Traefik: traefik.enable=true + traefik.docker.network = <stack>_<network milik service DAN Traefik>
  bad="$(jq -r --arg s "$stack" '((.services.traefik.networks // {}) | keys) as $tn
      | .services | to_entries[]
      | (.value.labels // {}) as $l | ((.value.networks // {}) | keys) as $n
      | select(([$l | keys[] | select(test("^traefik\\.(http|tcp|udp)\\."))] | length) > 0
               or ($l["traefik.enable"] // "") == "true")
      | select((($l["traefik.enable"] // "") != "true")
               or (($l["traefik.docker.network"] // "") as $dn
                   | [$n[] | . as $x | select(($s + "_" + $x) == $dn and ($tn | index($x)) != null)] | length) == 0)
      | .key' <<< "$json")"
  [[ -z "$bad" ]] \
    || err "[$label] router Traefik tanpa traefik.enable=true + traefik.docker.network=${stack}_<network bersama Traefik>: ${bad//$'\n'/ }"

  # (d) pg_hba `samenet` = subnet interface postgres -> postgres HANYA di network data (least privilege)
  jq -e '(.services.postgres == null) or (((.services.postgres.networks // {}) | keys) == ["data"])' <<< "$json" > /dev/null \
    || err "[$label] postgres wajib hanya di network data (pg_hba.conf memakai samenet)"

  # (e) alias scrape Prometheus hanya di network internal service pemiliknya
  local spec svc alias
  local anet
  for spec in api-gateway:api-gateway-metrics:internal ocr-worker:ocr-worker-metrics:internal \
              wa-admin-relay:wa-admin-relay-ingress:wa-ingress; do
    IFS=: read -r svc alias anet <<< "$spec"
    jq -e --arg svc "$svc" --arg a "$alias" --arg n "$anet" '
        (.services[$svc] == null)
        or ((((.services[$svc].networks // {})[$n] // {}).aliases // []) | index($a) != null)' <<< "$json" > /dev/null \
      || err "[$label] ${svc}: alias ${alias} hilang dari network ${anet} (target Prometheus hilang)"
    jq -e --arg svc "$svc" --arg a "$alias" --arg n "$anet" '
        [.services | to_entries[] | .key as $k | (.value.networks // {}) | to_entries[]
         | select(((.value // {}).aliases // []) | index($a) != null) | ($k + "/" + .key)]
        - [$svc + "/" + $n] | length == 0' <<< "$json" > /dev/null \
      || err "[$label] alias ${alias} dipakai di luar ${svc}/${anet} (scrape lewat network lain)"
  done

  # (f) TRUSTED_PROXIES BFF memuat subnet internal stack ini
  jq -e --arg b "$base" '(.services["api-gateway"] == null)
      or (((.services["api-gateway"].environment.TRUSTED_PROXIES // "") | split(",") | index($b + ".1.0/24")) != null)' \
      <<< "$json" > /dev/null \
    || err "[$label] api-gateway: TRUSTED_PROXIES tanpa ${base}.1.0/24 (hapus TRUSTED_PROXIES lama dari .env atau sesuaikan)"

  # (g) Traefik + Promtail merender filter discovery dengan EFN_STACK yang sama
  for svc in traefik promtail; do
    jq -e --arg svc "$svc" --arg s "$stack" '(.services[$svc] == null)
        or (((.services[$svc].environment // {}).EFN_STACK // "") == $s
            and ((.services[$svc].entrypoint // [])[1] // "") == "/efn/render-config.sh")' <<< "$json" > /dev/null \
      || err "[$label] ${svc}: wajib entrypoint infra/stack/render-config.sh + EFN_STACK=${stack} (filter discovery per stack)"
  done

  # (h) stack selain efn tidak boleh memakai tag image lokal bersama (build satu env menimpa env lain)
  if [[ "$stack" != efn ]]; then
    bad="$(jq -r '.services | to_entries[] | select((.value.image // "") | test("^(ghcr\\.io/)?efn/[^@]+$")) | .key' <<< "$json")"
    [[ -z "$bad" ]] || err "[$label] image lokal bersama stack efn (set ulang IMAGE_* di .env): ${bad//$'\n'/ }"
  fi
  return 0
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
  # G44 (docs/04 §28.2) + G50 (§28.7): hanya tiga ACL CN (cn_bff = api-gateway, cn_forecast = forecast-worker,
  # cn_segment = segment-worker), semuanya `-m str` (persis, bukan sub/beg/reg); CN lain = perubahan kontrak.
  local cn_acls
  cn_acls=$(grep -E '^[[:space:]]*acl[[:space:]]+[a-z_]+[[:space:]]+ssl_c_s_dn\(CN\)' "$hap" \
            | sed -E 's/^[[:space:]]*acl[[:space:]]+//; s/[[:space:]]+/ /g' | sort)
  [[ "$cn_acls" == "$(printf '%s\n%s\n%s' 'cn_bff ssl_c_s_dn(CN) -m str api-gateway' \
                       'cn_forecast ssl_c_s_dn(CN) -m str forecast-worker' \
                       'cn_segment ssl_c_s_dn(CN) -m str segment-worker' | sort)" ]] \
    || err "infra/odoo-rpc-gateway/haproxy.cfg: ACL CN klien hanya cn_bff=api-gateway, cn_forecast=forecast-worker, cn_segment=segment-worker (-m str), ditemukan: ${cn_acls//$'\n'/; }"
  # G50: path kampanye per CN - proposal_upsert HANYA di p_segment; claim/ack HANYA di p_allowed (BFF)
  grep -qE '^[[:space:]]*acl[[:space:]]+p_segment[[:space:]]+path[[:space:]]+-m[[:space:]]+str[[:space:]]+/json/2/efn\.campaign/efn_campaign_proposal_upsert$' "$hap" \
    || err "infra/odoo-rpc-gateway/haproxy.cfg: p_segment wajib persis /json/2/efn.campaign/efn_campaign_proposal_upsert"
  if grep -qE '^[[:space:]]*acl[[:space:]]+p_(allowed|forecast)[[:space:]].*efn_campaign_proposal_upsert' "$hap" \
     || grep -qE '^[[:space:]]*acl[[:space:]]+p_(segment|forecast)[[:space:]].*efn_campaign_(claim|ack)' "$hap"; then
    err "infra/odoo-rpc-gateway/haproxy.cfg: path kampanye bocor ke CN lain (proposal_upsert hanya segment-worker; claim/ack hanya api-gateway)"
  fi
  grep -qE '^[[:space:]]*server[[:space:]].*[[:space:]]ssl[[:space:]]+verify[[:space:]]+required[[:space:]]+ca-file[[:space:]].*[[:space:]]crt[[:space:]].*[[:space:]]verifyhost[[:space:]]+odoo([[:space:]]|$)' "$hap" \
    || err "infra/odoo-rpc-gateway/haproxy.cfg: backend Odoo tanpa 'ssl verify required ca-file .. crt .. verifyhost odoo'"
  if grep -qE 'verify[[:space:]]+none|^[[:space:]]*(stats|option[[:space:]]+forwardfor)' "$hap"; then
    err "infra/odoo-rpc-gateway/haproxy.cfg: verify none / stats / forwardfor dilarang"
  fi
  check_stack_files
  # G50 (SEC-G50-10, docs/04 §28.10): ref persetujuan production tidak boleh berisi nilai di file repo (.env.example /
  # compose) - ref hanya dari config rilis bertanda tangan; nilai contoh akan memenuhi gerbang deploy-validate diam-diam.
  local ref_var ref_line
  for ref_var in BEHAVIOR_TRACKING_DPO_APPROVAL_REF PROMO_CAMPAIGN_APPROVAL_REF WA_UNOFFICIAL_RISK_ACCEPTANCE_REF \
                 EMAIL_DPA_APPROVAL_REF; do
    ref_line="$(grep -E "^${ref_var}=" "$COMPOSE_DIR/.env.example" || true)"
    [[ -n "$ref_line" ]] || err "infra/compose/.env.example: ${ref_var} wajib ada (kosong) sebagai dokumentasi gerbang G50"
    [[ -z "$ref_line" || "$ref_line" =~ ^${ref_var}=[[:space:]]*(#.*)?$ ]] \
      || err "infra/compose/.env.example: ${ref_var} wajib kosong (ref persetujuan hanya dari config rilis, SEC-G50-10)"
    ref_line="$(grep -hE "^[[:space:]]*-?[[:space:]]*${ref_var}[:=]" "$COMPOSE_DIR"/docker-compose*.yml || true)"
    if [[ -n "$ref_line" ]] \
       && grep -vqE "${ref_var}[:=][[:space:]]*\\\$\\{${ref_var}:-\\}[[:space:]]*(#.*)?$" <<< "$ref_line"; then
      err "infra/compose/docker-compose*.yml: ${ref_var} di-hardcode; wajib \${${ref_var}:-} (SEC-G50-10)"
    fi
  done
  # SEC-215 (G44): user ClickHouse forecast dibatasi HOST IP (bukan HOST ANY) + pagar allowlist objek efn_forecast
  local chi="${ROOT_DIR}/infra/clickhouse/clickhouse-init.sh"
  # shellcheck disable=SC2016  # literal $FORECAST_CIDR di file target, bukan ekspansi
  grep -qE '^create_user efn_forecast_reader /run/secrets/clickhouse_password_forecast "\$FORECAST_CIDR"$' "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: efn_forecast_reader wajib dibuat dengan HOST IP \$FORECAST_CIDR (SEC-215)"
  # K25-06 + pola SEC-215: efn_segment_reader HOST IP subnet segment-ch, divalidasi private_cidr_ok (/24../32)
  # shellcheck disable=SC2016  # literal $SEGMENT_CIDR di file target, bukan ekspansi
  grep -qE '^create_user efn_segment_reader /run/secrets/clickhouse_password_segment "\$SEGMENT_CIDR"$' "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: efn_segment_reader wajib dibuat dengan HOST IP \$SEGMENT_CIDR (pola SEC-215)"
  # shellcheck disable=SC2016  # literal $SEGMENT_CIDR di file target, bukan ekspansi
  grep -qE '^private_cidr_ok "\$SEGMENT_CIDR"' "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: SEGMENT_CIDR wajib divalidasi private_cidr_ok (pola SEC-223)"
  grep -qF 'REVOKE SELECT ON efn_forecast.* FROM forecast_reader' "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: pagar objek efn_forecast (REVOKE bila di luar allowlist) hilang (SEC-215)"
  # SEC-223 (G45): pagar definisi view (DEFINER + SQL SECURITY DEFINER + sumber efn_dwh.v_fc_*) lewat create_table_query
  grep -qF "DEFINER = efn_forecast_definer SQL SECURITY DEFINER AS SELECT [A-Za-z0-9_, ]+ FROM efn_dwh[.]v_fc_" "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: regex pagar definisi view efn_forecast (create_table_query, SEC-223) hilang"
  # shellcheck disable=SC2016  # literal $FC_BADDEF di file target, bukan ekspansi
  grep -qE '^[[:space:]]+if \[ -n "\$FC_BADDEF" \]; then$' "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: REVOKE pagar definisi view efn_forecast (SEC-223) hilang"
  # SEC-223: prefix HOST user forecast minimal /24 (172.30.0.0/16 = semua network EFN)
  # shellcheck disable=SC2016  # literal $c_p di file target, bukan ekspansi
  grep -qE '\[ "\$c_p" -lt 24 \]' "$chi" \
    || err "infra/clickhouse/clickhouse-init.sh: private_cidr_ok wajib menolak prefix < /24 (SEC-223)"
  check_esb_users
  echo "check-encryption: file konfigurasi infra diperiksa"
}

# K25-06: file konfigurasi yang tidak bisa diinterpolasi compose tidak boleh mengandung nama network / subnet tetap.
check_stack_files() {
  local tf="${ROOT_DIR}/infra/traefik/traefik.yml" pt="${ROOT_DIR}/infra/observability/promtail.yml"
  local pm="${ROOT_DIR}/infra/observability/prometheus.yml" hba="${ROOT_DIR}/infra/postgres/pg_hba.conf"
  # shellcheck disable=SC2016  # backtick di regex = karakter literal sintaks constraint Traefik, bukan substitusi shell
  grep -qE '^[[:space:]]+constraints:[[:space:]]*"Label\(`com\.docker\.compose\.project`,[[:space:]]*`@EFN_STACK@`\)"[[:space:]]*$' "$tf" \
    || err "infra/traefik/traefik.yml: providers.docker.constraints Label(com.docker.compose.project, @EFN_STACK@) hilang (K25-06)"
  grep -qE '^[[:space:]]+network:[[:space:]]*"@EFN_STACK@_internal"[[:space:]]*$' "$tf" \
    || err "infra/traefik/traefik.yml: providers.docker.network wajib \"@EFN_STACK@_internal\" (K25-06)"
  local body
  local net_re='efn_(internal|edge|data|egress|cache|socket-proxy|objstore|insight-[a-z]+|login-ingress|esb|egress-esb|forecast-[a-z]+|segment-[a-z]+|rmq|wa-[a-z]+|mail)([^a-z_-]|$)'
  body="$(grep -vE "$COMMENT_RE" "$tf" || true)"
  if grep -qE "$net_re" <<< "$body"; then
    err "infra/traefik/traefik.yml: nama network tetap efn_* (pakai @EFN_STACK@_<network>)"
  fi
  grep -qE 'com\.docker\.compose\.project=@EFN_STACK@' "$pt" \
    || err "infra/observability/promtail.yml: filter docker_sd label com.docker.compose.project=@EFN_STACK@ hilang (K25-06)"
  body="$(grep -vE "$COMMENT_RE" "$pm" || true)"
  if grep -qE "${net_re}|172\\\\?\\.30" <<< "$body"; then
    err "infra/observability/prometheus.yml: nama network (<svc>.efn_*) / subnet 172.30 literal (K25-06: alias ber-scope network)"
  fi
  body="$(grep -vE "$COMMENT_RE" "$hba" || true)"
  if grep -qE '^host(ssl|nossl)?[[:space:]].*[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+' <<< "$body"; then
    err "infra/postgres/pg_hba.conf: CIDR literal (K25-06: pakai samenet; postgres hanya di network data)"
  fi
  grep -qE '^hostssl[[:space:]]+[^[:space:]]+[[:space:]]+efn_odoo[[:space:]]+samenet[[:space:]]+scram-sha-256' "$hba" \
    || err "infra/postgres/pg_hba.conf: baris efn_odoo wajib hostssl ... samenet scram-sha-256"
  # SEC-G50-06 + K25-06: relay admin WA bind HANYA "${WA_RELAY_BIND_IP}:8443" (IP wa-ingress dari compose) + loopback
  # health; env tidak ada/kosong = .alert (start gagal), bukan bind semua interface.
  local rc="${ROOT_DIR}/infra/wa-egress/admin-relay.cfg" binds
  binds="$({ grep -E '^[[:space:]]*bind[[:space:]]' "$rc" || true; } | sed -E 's/^[[:space:]]+//; s/[[:space:]]+/ /g' | sort)"
  # shellcheck disable=SC2016  # literal ${WA_RELAY_BIND_IP} di file target (ekspansi env HAProxy), bukan shell
  [[ "$binds" == "$(printf '%s\n%s' 'bind "${WA_RELAY_BIND_IP}:8443"' 'bind 127.0.0.1:8408' | sort)" ]] \
    || err "infra/wa-egress/admin-relay.cfg: bind wajib persis \"\${WA_RELAY_BIND_IP}:8443\" + 127.0.0.1:8408 (SEC-G50-06/K25-06), ditemukan: ${binds//$'\n'/; }"
  { grep -qE '^\.if !defined\(WA_RELAY_BIND_IP\)$' "$rc" && grep -qE '^[[:space:]]*\.alert ' "$rc"; } \
    || err "infra/wa-egress/admin-relay.cfg: pagar .if !defined(WA_RELAY_BIND_IP) + .alert hilang (fail-open bind)"
  body="$(grep -vE "$COMMENT_RE" "$rc" || true)"
  if grep -qE '172\.30\.' <<< "$body"; then
    err "infra/wa-egress/admin-relay.cfg: IP 172.30.x literal (K25-06: pakai WA_RELAY_BIND_IP)"
  fi
  return 0
}

# SEC-180 (G41): username akun API ESB (saat ini berhak admin, Q-ESB1) tidak boleh ada di repo publik.
#   (a) Setiap ESB_USER_<BRAND> di file compose infra/compose/docker-compose*.yml wajib `${ESB_USER_<BRAND>:-}` atau
#       kosong; di .env.example wajib kosong. Nilai nyata hanya di infra/compose/.env lokal (gitignored) / config rilis.
#   (b) Username lama yang sempat tertulis tidak boleh muncul lagi di file repo (tracked + untracked yang tidak
#       di-gitignore). Dikecualikan: laporan review G41 dan docs/04 (ditangani lead/reviewer).
check_esb_users() {
  local f line key val expect
  local -a files=()
  for f in "$COMPOSE_DIR"/docker-compose*.yml "$COMPOSE_DIR/.env.example"; do
    [[ -f "$f" ]] && files+=("$f")
  done
  for f in "${files[@]}"; do
    while IFS= read -r line; do
      [[ "$line" =~ ^[0-9]+:[[:space:]]*-?[[:space:]]*(ESB_USER_[A-Za-z0-9_]+)[:=](.*)$ ]] || continue
      key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
      val="${val%%[[:space:]]#*}"                       # buang komentar akhir baris
      val="${val#"${val%%[![:space:]]*}"}"              # trim kiri
      val="${val%"${val##*[![:space:]]}"}"              # trim kanan
      if [[ "$val" =~ ^\"(.*)\"$ || "$val" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
      expect="\${${key}:-}"
      if [[ "$f" == */.env.example ]]; then
        [[ -z "$val" ]] || err "${f#"${ROOT_DIR}"/}: ${key} wajib kosong di .env.example (SEC-180)"
      else
        [[ -z "$val" || "$val" == "$expect" ]] \
          || err "${f#"${ROOT_DIR}"/}: ${key} di-hardcode; wajib ${expect} (SEC-180, username ESB hanya di .env lokal)"
      fi
    done < <(grep -nE '^[[:space:]]*-?[[:space:]]*ESB_USER_[A-Za-z0-9_]+[:=]' "$f" || true)
  done

  # Pola disusun dari potongan agar file ini sendiri tidak cocok.
  local legacy_re hits
  legacy_re="$(printf '%s' 'ebpit' 'admin|sar' 'onih')"
  if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    hits="$(
      cd "$ROOT_DIR" || exit 0
      { git ls-files -z --cached --others --exclude-standard | xargs -0 -r grep -IilE "$legacy_re" -- 2> /dev/null; } || true
    )"
    [[ -z "$hits" ]] || err "username akun ESB lama (SEC-180) masih tertulis di: $(tr '\n' ' ' <<< "$hits")"
  else
    echo "check-encryption: info: bukan work tree git, cek username ESB lama (SEC-180) dilewati"
  fi
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
    # G21 AC-13: render prod + overlay kunci PII Odoo (hanya odoo, env *_FILE cocok, mode terkunci)
    check_compose prod-pii -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.pii.yml
    # G37 (K25-01): render prod + overlay gateway LLM 9router (ocr-worker egress + ninerouter_api_key)
    check_compose prod-llm -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.llm.yml
    # G38 (K25-01): render prod + overlay 9router Text-to-SQL (text2sql egress + ninerouter_api_key)
    check_compose prod-llm-text2sql -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.llm.yml \
      -f docker-compose.llm-text2sql.yml
    # G41 (docs/04 §25): render prod + overlay ekstraksi ESB (esb-extract: network esb + egress, password ESB per brand)
    check_compose prod-esb -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.esb.yml
    # G44 (docs/04 §28.2): render dev/prod + overlay forecast-worker (network forecast-ch/forecast-rpc, API key + mTLS)
    check_compose dev-forecast -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.forecast.yml
    check_compose prod-forecast -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.forecast.yml
    # G50 (docs/04 §28.7/§28.9): overlay segment-worker dan WA tidak resmi (profil wa-unofficial), dev + prod
    check_compose dev-segment -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.segment.yml
    check_compose prod-segment -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.segment.yml
    check_compose dev-wa-unofficial -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.wa-unofficial.yml \
      --profile wa-unofficial
    check_compose prod-wa-unofficial -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.wa-unofficial.yml \
      --profile wa-unofficial
    # G43 (docs/04 §27.2): esb-migrate hanya ter-render dengan --profile migrate; render kedua + overlay sisi Odoo
    # (migration_bundle_key + direktori pertukaran ke odoo selama jendela migrasi).
    check_compose prod-esb-migrate -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.esb.yml \
      --profile migrate
    check_compose prod-migration -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.esb.yml \
      -f docker-compose.migration.yml --profile migrate
    # K25-06 (docs/runbooks/multi-env-local.md): stack bernama lain + subnet lain (gladi prod lokal di host yang rentang
    # 172.16-31-nya dipakai project lain) wajib lolos SEMUA aturan di atas + identitas stack. Overlay esb, forecast,
    # segment, wa-unofficial (+ mail di dev) ikut agar semua network (oktet 0..21) ter-render.
    CC_STACK=efn-prod CC_BASE=10.250
    check_compose prod-stack -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.local-edge.yml \
      -f docker-compose.esb.yml -f docker-compose.forecast.yml -f docker-compose.segment.yml \
      -f docker-compose.wa-unofficial.yml --profile wa-unofficial
    CC_STACK=efn-dev CC_BASE=10.252
    check_compose dev-stack -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose.forecast.yml \
      -f docker-compose.segment.yml -f docker-compose.wa-unofficial.yml --profile wa-unofficial
    CC_STACK="" CC_BASE=""
    check_files
    ;;
  *) echo "pemakaian: $0 [all|files]" >&2; exit 2 ;;
esac

if (( fail )); then
  echo "check-encryption: GAGAL - lihat docs/04 §7.1 / ADR-022 (pengecualian hanya lewat ADR)" >&2
  exit 1
fi
echo "check-encryption: OK - tidak ada konfigurasi plaintext"
