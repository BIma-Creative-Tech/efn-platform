#!/usr/bin/env bash
# Dijalankan DI HOST target sebagai ROOT oleh wrapper root /usr/local/sbin/efn-deploy (scripts/host/efn-deploy,
# SEC-36): wrapper memverifikasi tanda tangan cosign bundel rilis dari deploy.yml, menyalinnya ke /opt/efn milik
# root, mengekspor env tervalidasi (secret lewat stdin, tidak di argv/ps), lalu exec skrip ini. Manual oleh
# Superadmin: `sudo -E bash /opt/efn/scripts/deploy.sh`.
# Kenapa root: /opt/efn/secrets dimiliki root:efn-superadmin 0750 (docs/04 §8.6, docs/runbooks/key-custody.md).
# User deploy TIDAK di grup docker dan tidak bisa menulis /opt/efn (runbook key-custody §4).
#
# Prasyarat host: docker + compose plugin, jq, openssl (>= 3.0), grup `efn-superadmin`, direktori /opt/efn,
#                 WireGuard wg0 aktif SEBELUM docker (EFN_WG_BIND harus sudah ada di host; runbook wireguard-outlet).
# Layout host: /opt/efn/{compose,traefik,observability,backup,minio,postgres,clickhouse,redis,rabbitmq,wireguard}
#              (= isi infra/), /opt/efn/scripts (= scripts/), /opt/efn/etl-sql (= services/etl/sql/, opsional),
#              /opt/efn/secrets (TIDAK disinkronkan; root:efn-superadmin 0750).
# Env wajib : TRAEFIK_DOMAIN ACME_EMAIL GHCR_USER GHCR_TOKEN COMPOSE_PROFILES APP_ENV PAYMENT_PROVIDER
#             EFN_PUBLIC_BIND (IP publik host untuk 80/443 websecure)
#             IMAGE_POSTGRES IMAGE_ODOO IMAGE_API_GATEWAY IMAGE_SUPERAPP_WEB IMAGE_AI_OCR_WORKER IMAGE_ETL IMAGE_MINIO
#               (= <registry>/<svc>@sha256:<digest> hasil `cosign verify` di workflow; SEC-10: deploy by digest)
#             EFN_PKI_INTERMEDIATE_KEY EFN_PKI_INTERMEDIATE_CRT EFN_PKI_ROOT_CRT (PKI 3 tingkat, ADR-022 W1)
#             + satu env UPPERCASE per baris scripts/secrets.list yang BUKAN `pki`/`derived:*`
# Env opsional: WHATSAPP_ENABLED WHATSAPP_PHONE_NUMBER_ID WHATSAPP_OTP_TEMPLATE OLLAMA_VISION_MODEL
#               EFN_WG_BIND (default 10.88.0.1) EFN_WG_HTTPS_PORT (default 443) EFN_WG_OUTLET_CIDR EFN_WG_STAFF_CIDR
#               ACME_DNS_PROVIDER (default cloudflare) ACME_DNS_TOKEN_ENV (default CF_DNS_API_TOKEN)
#               EFN_PKI_EXTRA_TRUST (PEM CA lama selama rotasi CA) RABBITMQ_STRICT_PERMISSIONS
#               MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS PG_SUPERUSER ETL_ENABLE_ODOO (true|false)
set -euo pipefail

BASE=/opt/efn
COMPOSE_DIR="$BASE/compose"
SECRETS_DIR="$BASE/secrets"
SECRETS_LIST="$BASE/scripts/secrets.list"
TEXTFILE_DIR=/var/lib/efn/node-exporter
ETL_GRANTS_SQL="$BASE/etl-sql/odoo-readonly-grants.sql"
APP_ENV="${APP_ENV:-production}"
SECRET_GROUP="${EFN_SECRET_GROUP:-efn-superadmin}"
# Izin file secret: lihat bagian "Mekanisme uid" di docs/runbooks/key-custody.md. Direktori 0750 root:efn-superadmin
# adalah batas akses host; file 0444 karena compose non-swarm me-bind-mount file apa adanya dan proses container
# berjalan dengan uid beragam (65534, 999, 472, 101, 1000, ...) yang sebagian membuang supplementary group (gosu/
# su-exec) -> 0440 + group_add tidak andal. Container hanya melihat secret yang di-mount ke service-nya.
export SECRET_FILE_MODE="${SECRET_FILE_MODE:-0444}"

die() { echo "deploy: $*" >&2; exit 1; }

# ── 0. Validasi input non-rahasia (SEC-10/SEC-30: tidak ada nilai tak tervalidasi ke shell/compose/sed) ───────
[[ "${EUID}" -eq 0 ]] || die "harus dijalankan sebagai root (sudo); lihat docs/runbooks/key-custody.md"
[[ -z "${PKI_SOURCE:-}" || "${PKI_SOURCE}" == "intermediate" ]] \
  || die "PKI_SOURCE=${PKI_SOURCE} sudah dihapus (SEC-23); leaf selalu diterbitkan dari intermediate"
DIGEST_RE='^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$'
for v in IMAGE_POSTGRES IMAGE_ODOO IMAGE_API_GATEWAY IMAGE_SUPERAPP_WEB IMAGE_AI_OCR_WORKER IMAGE_ETL IMAGE_MINIO; do
  [[ "${!v:-}" =~ $DIGEST_RE ]] || die "$v harus <registry>/<image>@sha256:<64 hex> (dapat: '${!v:-}')"
done
[[ "${TRAEFIK_DOMAIN:-}" =~ ^[a-z0-9.-]+$ ]] || die "TRAEFIK_DOMAIN tidak valid"
[[ "${ACME_EMAIL:-}" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || die "ACME_EMAIL tidak valid"
[[ "$APP_ENV" =~ ^(production|staging)$ ]] || die "APP_ENV harus production|staging"
[[ "${COMPOSE_PROFILES:-}" =~ ^[a-z,]+$ ]] || die "COMPOSE_PROFILES tidak valid"
# `disabled` = default production BFF selama vendor belum dipilih (docs/04 §8.11, ADR-016/026) - G6 QA
[[ "${PAYMENT_PROVIDER:-}" =~ ^(mock|disabled|xendit|midtrans)$ ]] || die "PAYMENT_PROVIDER wajib eksplisit: mock|disabled|xendit|midtrans"
[[ "$APP_ENV" == "production" && "$PAYMENT_PROVIDER" == "mock" ]] && die "production menolak PAYMENT_PROVIDER=mock (docs/04 §8.3)"
IPV4_RE='^([0-9]{1,3}\.){3}[0-9]{1,3}$'
CIDR_RE='^([0-9]{1,3}\.){3}[0-9]{1,3}/[0-9]{1,2}$'
[[ "${EFN_PUBLIC_BIND:-}" =~ $IPV4_RE ]] || die "EFN_PUBLIC_BIND (IP publik host untuk websecure) wajib"
EFN_WG_BIND="${EFN_WG_BIND:-10.88.0.1}"
EFN_WG_HTTPS_PORT="${EFN_WG_HTTPS_PORT:-443}"
EFN_WG_OUTLET_CIDR="${EFN_WG_OUTLET_CIDR:-10.88.0.0/16}"
EFN_WG_STAFF_CIDR="${EFN_WG_STAFF_CIDR:-10.89.0.0/16}"
[[ "$EFN_WG_BIND" =~ $IPV4_RE ]] || die "EFN_WG_BIND tidak valid"
[[ "$EFN_WG_HTTPS_PORT" =~ ^[0-9]{2,5}$ ]] || die "EFN_WG_HTTPS_PORT tidak valid"
[[ "$EFN_WG_OUTLET_CIDR" =~ $CIDR_RE && "$EFN_WG_STAFF_CIDR" =~ $CIDR_RE ]] || die "EFN_WG_*_CIDR tidak valid"
[[ "$EFN_PUBLIC_BIND" != "$EFN_WG_BIND" ]] || die "EFN_PUBLIC_BIND dan EFN_WG_BIND harus berbeda"
ip -4 addr show 2> /dev/null | grep -q "inet ${EFN_WG_BIND}/" \
  || die "IP WireGuard ${EFN_WG_BIND} belum ada di host (wg-quick@wg0 harus aktif sebelum deploy/docker)"
ACME_DNS_PROVIDER="${ACME_DNS_PROVIDER:-cloudflare}"
ACME_DNS_TOKEN_ENV="${ACME_DNS_TOKEN_ENV:-CF_DNS_API_TOKEN}"
[[ "$ACME_DNS_PROVIDER" =~ ^[a-z0-9]+$ ]] || die "ACME_DNS_PROVIDER tidak valid"
[[ "$ACME_DNS_TOKEN_ENV" =~ ^[A-Z][A-Z0-9_]+$ ]] || die "ACME_DNS_TOKEN_ENV tidak valid"
for v in MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS DSAR_DELETE_GRACE_DAYS; do
  [[ -z "${!v:-}" || "${!v}" =~ ^[0-9]+$ ]] || die "$v harus angka"
done
# Teks consent aktif (docs/04 §8.9; BFF menolak start bila format salah). Kosong = grant consent 503 (G6 QA).
for p in LOCATION MARKETING; do
  ver="CONSENT_${p}_VERSION"; sha="CONSENT_${p}_TEXT_SHA256"
  [[ -z "${!ver:-}" || "${!ver}" =~ ^[A-Za-z0-9._@-]{1,32}$ ]] || die "$ver tidak valid"
  [[ -z "${!sha:-}" || "${!sha}" =~ ^[0-9a-f]{64}$ ]] || die "$sha harus hex 64 huruf kecil"
  if [[ -n "${!ver:-}" && -z "${!sha:-}" ]] || [[ -z "${!ver:-}" && -n "${!sha:-}" ]]; then
    die "$ver dan $sha harus di-set berpasangan"
  fi
done
getent group "$SECRET_GROUP" > /dev/null || die "grup ${SECRET_GROUP} belum ada (docs/runbooks/key-custody.md §Host)"
for v in EFN_PKI_INTERMEDIATE_KEY EFN_PKI_INTERMEDIATE_CRT EFN_PKI_ROOT_CRT; do
  [[ -n "${!v:-}" ]] || die "$v kosong (Superadmin: make pki-intermediate ENV=${APP_ENV}, unggah ke GitHub Environment)"
done

# ── 1. Secrets (docs/04 §8.6) ────────────────────────────────────────────────────────────────────────────────────
mapfile -t SECRET_ROWS < <(awk '!/^[[:space:]]*#/ && NF { print $1, $2 }' "$SECRETS_LIST")
echo "==> Menulis secrets ke $SECRETS_DIR (dir 0750 root:${SECRET_GROUP}, file ${SECRET_FILE_MODE})"
install -d -o root -g "$SECRET_GROUP" -m 0750 "$SECRETS_DIR"

SECRET_NAMES=()
for row in "${SECRET_ROWS[@]}"; do
  name="${row%% *}"; kind="${row##* }"
  SECRET_NAMES+=("$name")
  [[ "$kind" == "pki" || "$kind" == derived:* ]] && continue
  var=$(tr '[:lower:]' '[:upper:]' <<< "$name")
  [[ -n "${!var:-}" ]] || die "secret $var kosong (tambahkan ke GitHub Environment)"
  tmp=$(mktemp "$SECRETS_DIR/.${name}.XXXXXX")
  printf '%s' "${!var}" > "$tmp"
  chmod "$SECRET_FILE_MODE" "$tmp"
  mv -f "$tmp" "$SECRETS_DIR/${name}.txt"
done

# Webhook alert produksi wajib HTTPS (enkripsi di setiap transaksi data, juga ke luar)
grep -q '^https://' "$SECRETS_DIR/alertmanager_webhook_url.txt" \
  || die "ALERTMANAGER_WEBHOOK_URL harus https:// di ${APP_ENV}"
if [[ "$PAYMENT_PROVIDER" =~ ^(xendit|midtrans)$ ]] && grep -qx 'disabled' "$SECRETS_DIR/payment_provider_api_key.txt"; then
  die "PAYMENT_PROVIDER=${PAYMENT_PROVIDER} tetapi PAYMENT_PROVIDER_API_KEY masih placeholder"
fi
grep -qx 'disabled' "$SECRETS_DIR/acme_dns_api_token.txt" \
  && die "ACME_DNS_API_TOKEN masih placeholder (wajib untuk sertifikat wg-https, ADR-023)"

echo "==> Secret turunan (member_hash_pepper = HKDF(otp_pepper), token metrik MinIO)"
bash "$BASE/scripts/derive-secrets.sh" "$SECRETS_DIR"

# ── 2. PKI: leaf 90 hari dari intermediate; kunci intermediate HANYA di tmpfs selama langkah ini ────────────────
echo "==> PKI: terbitkan/perbarui leaf (< 30 hari) dari intermediate ${APP_ENV}"
PKI_TMP=$(mktemp -d -p /dev/shm efn-pki.XXXXXX)
cleanup_pki() {
  if [[ -d "$PKI_TMP" ]]; then
    shred -u "$PKI_TMP"/* 2> /dev/null || true
    rm -rf "$PKI_TMP"
  fi
}
trap cleanup_pki EXIT
( umask 077
  printf '%s\n' "$EFN_PKI_INTERMEDIATE_KEY" > "$PKI_TMP/intermediate.key"
  printf '%s\n' "$EFN_PKI_INTERMEDIATE_CRT" > "$PKI_TMP/intermediate.crt"
  printf '%s\n' "$EFN_PKI_ROOT_CRT" > "$PKI_TMP/root.crt" )
unset EFN_PKI_INTERMEDIATE_KEY
[[ "$(openssl x509 -in "$PKI_TMP/intermediate.crt" -noout -pubkey | openssl sha256)" == \
   "$(openssl pkey -in "$PKI_TMP/intermediate.key" -pubout | openssl sha256)" ]] || die "kunci intermediate != sertifikat"
openssl x509 -in "$PKI_TMP/intermediate.crt" -noout -checkend $((30 * 86400)) > /dev/null \
  || echo "PERINGATAN: intermediate kedaluwarsa < 30 hari - Superadmin: make pki-intermediate ENV=${APP_ENV}" >&2
rm -f "$SECRETS_DIR/.pki-renewed"
SECRETS_DIR="$SECRETS_DIR" CA_DIR="$PKI_TMP" APP_ENV="$APP_ENV" LEAF_DAYS=90 RENEW_DAYS=30 \
  EFN_PKI_EXTRA_TRUST="${EFN_PKI_EXTRA_TRUST:-}" bash "$BASE/scripts/gen-pki.sh" leaf || die "gen-pki.sh gagal"
cleanup_pki
trap - EXIT
RENEWED=()
if [[ -s "$SECRETS_DIR/.pki-renewed" ]]; then mapfile -t RENEWED < <(sort -u "$SECRETS_DIR/.pki-renewed"); fi
rm -f "$SECRETS_DIR/.pki-renewed"
echo "    leaf/bundle diperbarui: ${#RENEWED[@]}"

chown root:"$SECRET_GROUP" "$SECRETS_DIR"/*.txt
chmod "$SECRET_FILE_MODE" "$SECRETS_DIR"/*.txt
# Sisa layout lama (gelombang 3: kunci CA di host) TIDAK boleh ada di server (SEC-23)
if [[ -d "$BASE/pki-ca" ]]; then
  echo "PERINGATAN: $BASE/pki-ca (kunci CA lama) masih ada di host - musnahkan (docs/runbooks/internal-pki.md §Migrasi)" >&2
fi

echo "==> Verifikasi PKI: rantai ke bundle (nameConstraints), kunci cocok, belum kedaluwarsa"
CA="$SECRETS_DIR/efn_ca_crt.txt"
for name in "${SECRET_NAMES[@]}"; do
  [[ "$name" =~ ^tls_(.+)_crt$ ]] || continue
  base="tls_${BASH_REMATCH[1]}"
  crt="$SECRETS_DIR/${base}_crt.txt"; key="$SECRETS_DIR/${base}_key.txt"
  openssl verify -CAfile "$CA" "$crt" > /dev/null || die "${base}: tidak terverifikasi terhadap bundle CA"
  openssl x509 -in "$crt" -noout -checkend 0 > /dev/null || die "${base}: kedaluwarsa"
  [[ "$(openssl x509 -in "$crt" -noout -pubkey | openssl sha256)" == "$(openssl pkey -in "$key" -pubout | openssl sha256)" ]] \
    || die "${base}: sertifikat dan kunci tidak cocok"
done

echo "==> Metrik masa berlaku sertifikat internal (alert EfnInternalCertExpiring*)"
if [[ -d "$TEXTFILE_DIR" ]]; then
  tmp=$(mktemp "$TEXTFILE_DIR/.efn_pki.XXXXXX")
  split_dir=$(mktemp -d)
  {
    echo "# HELP efn_internal_cert_expiry_timestamp_seconds Waktu kedaluwarsa sertifikat PKI internal (docs/04 §7.1)."
    echo "# TYPE efn_internal_cert_expiry_timestamp_seconds gauge"
    # Bundle: intermediate (efn_ca_crt_0), root (efn_ca_crt_1), trust transisi (efn_ca_crt_2..)
    awk -v d="$split_dir" '/-----BEGIN CERTIFICATE-----/{n++} {print > (d "/" (n - 1) ".pem")}' "$CA"
    for f in "$split_dir"/*.pem; do
      end=$(openssl x509 -in "$f" -noout -enddate | cut -d= -f2)
      echo "efn_internal_cert_expiry_timestamp_seconds{cert=\"efn_ca_crt_$(basename "$f" .pem)\"} $(date -d "$end" +%s)"
    done
    for name in "${SECRET_NAMES[@]}"; do
      [[ "$name" =~ ^tls_.+_crt$ ]] || continue
      end=$(openssl x509 -in "$SECRETS_DIR/${name}.txt" -noout -enddate | cut -d= -f2)
      echo "efn_internal_cert_expiry_timestamp_seconds{cert=\"${name}\"} $(date -d "$end" +%s)"
    done
  } > "$tmp"
  rm -rf "$split_dir"
  chmod 644 "$tmp"
  mv -f "$tmp" "$TEXTFILE_DIR/efn_pki.prom"
else
  echo "PERINGATAN: $TEXTFILE_DIR belum ada; buat sekali: install -d -m 0755 $TEXTFILE_DIR"
fi

# ── 3. .env non-rahasia ───────────────────────────────────────────────────────────────────────────────────────────
# SEC-03: BFF mempercayai XFF dari subnet network `internal` (Traefik, Next.js) + rentang Cloudflare
CF_IPS=$(grep -Ev '^[[:space:]]*(#|$)' "$BASE/traefik/cloudflare-ips.txt" | paste -sd, -)
[[ -n "$CF_IPS" ]] || die "infra/traefik/cloudflare-ips.txt kosong"

# R5: job ETL fact_sales_line (Odoo POS -> ClickHouse). Nilai eksplisit (vars ETL_ENABLE_ODOO) menang; bila tidak
# di-set, nilai .env sebelumnya dipertahankan (diaktifkan otomatis setelah grant Odoo sukses, langkah di bawah).
[[ -z "${ETL_ENABLE_ODOO:-}" || "$ETL_ENABLE_ODOO" =~ ^(true|false)$ ]] || die "ETL_ENABLE_ODOO harus true|false"
PREV_ETL_ODOO=$(sed -n 's/^ETL_ENABLE_ODOO=\(true\|false\)$/\1/p' "$COMPOSE_DIR/.env" 2> /dev/null || true)
ETL_ENABLE_ODOO_EFF="${ETL_ENABLE_ODOO:-${PREV_ETL_ODOO:-false}}"

echo "==> Menulis .env (non-secret)"
cat > "$COMPOSE_DIR/.env" <<EOF
TRAEFIK_DOMAIN=${TRAEFIK_DOMAIN}
ACME_EMAIL=${ACME_EMAIL}
IMAGE_POSTGRES=${IMAGE_POSTGRES}
IMAGE_ODOO=${IMAGE_ODOO}
IMAGE_API_GATEWAY=${IMAGE_API_GATEWAY}
IMAGE_SUPERAPP_WEB=${IMAGE_SUPERAPP_WEB}
IMAGE_AI_OCR_WORKER=${IMAGE_AI_OCR_WORKER}
IMAGE_ETL=${IMAGE_ETL}
IMAGE_MINIO=${IMAGE_MINIO}
APP_ENV=${APP_ENV}
NODE_ENV=production
PG_SUPERUSER=${PG_SUPERUSER:-postgres}
PG_DB=efn_platform
ODOO_DB=efn_odoo
RABBITMQ_ADMIN_USER=efn-admin
RABBITMQ_STRICT_PERMISSIONS=${RABBITMQ_STRICT_PERMISSIONS:-true}
MINIO_ROOT_USER=efnadmin
CLICKHOUSE_USER=efn
GRAFANA_ADMIN_USER=admin
OLLAMA_VISION_MODEL=${OLLAMA_VISION_MODEL:-qwen2.5vl:7b}
WHATSAPP_ENABLED=${WHATSAPP_ENABLED:-false}
WHATSAPP_PHONE_NUMBER_ID=${WHATSAPP_PHONE_NUMBER_ID:-}
WHATSAPP_OTP_TEMPLATE=${WHATSAPP_OTP_TEMPLATE:-efn_otp}
PAYMENT_PROVIDER=${PAYMENT_PROVIDER}
PG_ARCHIVE_MODE=on
WALG_S3_PREFIX=${WALG_S3_PREFIX:-s3://pg-backup/efn}
TRUSTED_PROXIES=172.30.1.0/24,${CF_IPS}
NODE_EXPORTER_TEXTFILE_DIR=${TEXTFILE_DIR}
COMPOSE_PROFILES=${COMPOSE_PROFILES}
EFN_PUBLIC_BIND=${EFN_PUBLIC_BIND}
EFN_WG_BIND=${EFN_WG_BIND}
EFN_WG_HTTPS_PORT=${EFN_WG_HTTPS_PORT}
EFN_WG_OUTLET_CIDR=${EFN_WG_OUTLET_CIDR}
EFN_WG_STAFF_CIDR=${EFN_WG_STAFF_CIDR}
EFN_INTERNAL_EXTRA_CIDR=127.0.0.1/32
ACME_DNS_TOKEN_ENV=${ACME_DNS_TOKEN_ENV}
MARKET_SCOUT_PHOTO_RETENTION_DAYS=${MARKET_SCOUT_PHOTO_RETENTION_DAYS:-90}
CH_MART_RETENTION_YEARS=${CH_MART_RETENTION_YEARS:-5}
CONSENT_LOCATION_VERSION=${CONSENT_LOCATION_VERSION:-}
CONSENT_LOCATION_TEXT_SHA256=${CONSENT_LOCATION_TEXT_SHA256:-}
CONSENT_MARKETING_VERSION=${CONSENT_MARKETING_VERSION:-}
CONSENT_MARKETING_TEXT_SHA256=${CONSENT_MARKETING_TEXT_SHA256:-}
DSAR_DELETE_GRACE_DAYS=${DSAR_DELETE_GRACE_DAYS:-7}
ETL_ENABLE_ODOO=${ETL_ENABLE_ODOO_EFF}
EOF
chmod 0640 "$COMPOSE_DIR/.env"

# compose base merujuk ../../secrets (layout repo). Di host layout-nya /opt/efn/{compose,secrets},
# jadi lokasi secrets dioverride lewat compose file khusus host (tidak di-commit).
echo "==> Menulis docker-compose.host.yml"
{
  echo "# DIGENERATE oleh scripts/deploy.sh - jangan diedit manual"
  echo "secrets:"
  for name in "${SECRET_NAMES[@]}"; do
    printf '  %s:\n    file: %s/%s.txt\n' "$name" "$SECRETS_DIR" "$name"
  done
} > "$COMPOSE_DIR/docker-compose.host.yml"

echo "==> ACME email/provider & domain target probe"
sed -i "s|email: \".*\"|email: \"${ACME_EMAIL}\"|" "$BASE/traefik/traefik.yml"
sed -i "s|provider: cloudflare|provider: ${ACME_DNS_PROVIDER}|" "$BASE/traefik/traefik.yml"
sed -i "s|efn\.local|${TRAEFIK_DOMAIN}|g" "$BASE/observability/targets/blackbox-https.yml"

echo "==> Login GHCR"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

cd "$COMPOSE_DIR"
FILES=(-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml)
# Kunci API payment hanya di-mount bila provider nyata (docs/04 §8.3); mock/disabled tidak me-mount kunci
if [[ "$PAYMENT_PROVIDER" =~ ^(xendit|midtrans)$ ]]; then FILES+=(-f docker-compose.payment.yml); fi
DC=(docker compose "${FILES[@]}")

echo "==> Validasi konfigurasi"
"${DC[@]}" config -q
# Lint enkripsi (scripts/check-encryption.sh) dijalankan di CI terhadap repo; host hanya menerima commit main/tag.

echo "==> Pull image (by digest, hasil cosign verify)"
"${DC[@]}" pull --quiet

wait_healthy() {
  # $1 = service (opsional: kosong = semua)
  local unhealthy starting
  for _ in $(seq 1 36); do
    unhealthy=$("${DC[@]}" ps --format json "$@" | jq -r '(if type=="array" then .[] else . end) | select(.Health=="unhealthy") | .Service' | tr '\n' ' ')
    starting=$("${DC[@]}" ps --format json "$@" | jq -r '(if type=="array" then .[] else . end) | select(.Health=="starting") | .Service' | tr '\n' ' ')
    if [[ -z "${unhealthy// }" && -z "${starting// }" ]]; then return 0; fi
    if [[ -n "${unhealthy// }" ]]; then
      echo "UNHEALTHY: $unhealthy"
      # shellcheck disable=SC2086  # daftar service sengaja di-split
      "${DC[@]}" logs --tail=50 $unhealthy
      return 1
    fi
    sleep 10
  done
  echo "timeout menunggu healthcheck: $starting"
  return 1
}

echo "==> PostgreSQL dulu, lalu role per service (00-roles.sh: idempoten + rotasi password, docs/04 §7.3)"
"${DC[@]}" up -d --no-build postgres
wait_healthy postgres || die "postgres tidak sehat"
"${DC[@]}" exec -T postgres bash /docker-entrypoint-initdb.d/00-roles.sh || die "00-roles.sh gagal"

# Migrasi skema idempoten (gelombang 9): file init bernomor >= 04 (mis. 04-platform-v3-6.sql, SEC-31/SEC-39) wajib
# idempoten + transaksional (kontrak pemilik file: efn-bff-engineer). Volume baru menjalankannya lewat initdb; volume
# lama hanya lewat langkah ini. Dijalankan sebagai superuser lewat socket lokal, SEBELUM BFF baru start.
echo "==> Migrasi skema idempoten (infra/postgres/init/[0-9][0-9]-*.sql >= 04)"
for mig in "$BASE"/postgres/init/[0-9][0-9]-*.sql; do
  [[ -e "$mig" ]] || continue
  num=$(basename "$mig"); num=${num%%-*}
  (( 10#$num >= 4 )) || continue
  echo "    $(basename "$mig")"
  "${DC[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "${PG_SUPERUSER:-postgres}" -d postgres \
    -f "/docker-entrypoint-initdb.d/$(basename "$mig")" || die "migrasi $(basename "$mig") gagal"
done

echo "==> Up (--no-build: host hanya memakai image bertanda tangan dari GHCR)"
"${DC[@]}" up -d --remove-orphans --no-build

# Compose tidak me-recreate container saat ISI file secret berubah (bind mount menunjuk inode lama).
# Recreate service yang me-mount leaf/bundle yang baru diterbitkan + sidecar `network_mode: service:<app>`-nya.
if [[ ${#RENEWED[@]} -gt 0 ]]; then
  renewed_json=$(printf '%s\n' "${RENEWED[@]}" | jq -R . | jq -s .)
  mapfile -t RECREATE < <("${DC[@]}" config --format json | jq -r --argjson r "$renewed_json" '
      .services as $s
      | [ $s | to_entries[] | select([.value.secrets[]?.source] | any(. as $x | $r | index($x))) | .key ] as $direct
      | ($direct + [ $s | to_entries[]
                     | select((.value.network_mode // "") | startswith("service:"))
                     | select((.value.network_mode | ltrimstr("service:")) as $p | $direct | index($p))
                     | .key ])
      | unique[]')
  if [[ ${#RECREATE[@]} -gt 0 ]]; then
    echo "==> Recreate pemakai sertifikat baru: ${RECREATE[*]}"
    "${DC[@]}" up -d --no-build --no-deps --force-recreate "${RECREATE[@]}"
  fi
fi

echo "==> Menunggu healthcheck"
wait_healthy || die "healthcheck gagal"
echo "semua service sehat"

check_oneshot() {
  local svc="$1" code
  code=$("${DC[@]}" ps -a --format json "$svc" | jq -r '(if type=="array" then .[] else . end) | .ExitCode' | head -n1)
  [[ "$code" == "0" ]] || { echo "$svc gagal (exit $code)"; "${DC[@]}" logs "$svc"; return 1; }
}
echo "==> minio-init (bucket, SSE-S3, object lock, lifecycle, user per service) harus sukses"
check_oneshot minio-init || die "minio-init gagal"
if [[ ",${COMPOSE_PROFILES}," == *",data,"* ]]; then
  echo "==> clickhouse-init (skema, user per service, grant text2sql/etl/metabase - SEC-04) harus sukses"
  for _ in $(seq 1 30); do
    state=$("${DC[@]}" ps -a --format json clickhouse-init | jq -r '(if type=="array" then .[] else . end) | .State' | head -n1)
    [[ "$state" == "exited" ]] && break
    sleep 5
  done
  check_oneshot clickhouse-init || die "clickhouse-init gagal"
fi

# QA U1: grant baca level-kolom efn_etl pada tabel Odoo (file milik data-AI). Dijalankan sebagai efn_odoo
# (pemilik tabel, lewat socket lokal di container postgres) setelah Odoo ter-install; idempoten, ON_ERROR_STOP.
if [[ -s "$ETL_GRANTS_SQL" ]]; then
  if "${DC[@]}" exec -T postgres psql -X -q -t -A -U efn_odoo -d efn_odoo \
       -c "SELECT to_regclass('public.pos_order') IS NOT NULL" 2> /dev/null | grep -qx t; then
    echo "==> Grant ETL di DB Odoo ($(basename "$ETL_GRANTS_SQL"))"
    "${DC[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo -f - < "$ETL_GRANTS_SQL" \
      || die "grant ETL di DB Odoo gagal"
    # R5 (gelombang 9): job fact_sales_line aktif otomatis setelah grant sukses, kecuali dimatikan eksplisit
    # (ETL_ENABLE_ODOO=false di GitHub Environment vars).
    if [[ -z "${ETL_ENABLE_ODOO:-}" && "$ETL_ENABLE_ODOO_EFF" != "true" && ",${COMPOSE_PROFILES}," == *",data,"* ]]; then
      sed -i 's/^ETL_ENABLE_ODOO=.*/ETL_ENABLE_ODOO=true/' "$COMPOSE_DIR/.env"
      "${DC[@]}" up -d etl
    fi
  else
    echo "    DB efn_odoo belum ter-install (pos_order belum ada) - grant ETL dilewati, ulangi deploy setelah install"
  fi
fi

echo "==> Bersihkan image lama"
docker image prune -af --filter "until=168h" > /dev/null || true
docker logout ghcr.io
echo "==> Deploy selesai: $(sed -n 's/^IMAGE_API_GATEWAY=//p' .env)"
