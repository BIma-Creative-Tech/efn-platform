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
# SEC-50 (gelombang 13): sumber nilai non-rahasia - lihat scripts/lib/deploy-validate.sh:
#   topologi (TRAEFIK_DOMAIN EFN_PUBLIC_BIND EFN_WG_* COMPOSE_PROFILES EFN_ALLOW_CONTROL_OFF) = /etc/efn/deploy.conf
#     (dipin; env yang berbeda dengan pin DITOLAK), konfigurasi aplikasi = manifest rilis bertanda tangan (diekspor
#     efn-deploy) atau env Superadmin pada jalur manual. Semua divalidasi regex/subnet; karakter kontrol ditolak;
#     production menolak RABBITMQ_STRICT_PERMISSIONS=false / ETL_AUDIT_COPY_ENABLED=false tanpa izin deploy.conf.
# Env wajib : TRAEFIK_DOMAIN ACME_EMAIL GHCR_USER GHCR_TOKEN APP_ENV PAYMENT_PROVIDER
#             EFN_PUBLIC_BIND (IP host untuk 80/443 websecure)
#             IMAGE_POSTGRES IMAGE_ODOO IMAGE_API_GATEWAY IMAGE_SUPERAPP_WEB IMAGE_AI_OCR_WORKER IMAGE_ETL IMAGE_MINIO IMAGE_METABASE
#               (= <registry>/<svc>@sha256:<digest>; SEC-10: deploy by digest). Lewat efn-deploy nilainya HANYA dari
#               manifest rilis bertanda tangan + `cosign verify` di host (SEC-42); env CI tidak bisa mengisinya.
#             Pin root CA: EFN_PKI_ROOT_SHA256 (dari efn-deploy, atau dibaca dari /etc/efn/deploy.conf saat manual)
#             EFN_PKI_INTERMEDIATE_KEY EFN_PKI_INTERMEDIATE_CRT EFN_PKI_ROOT_CRT (PKI 3 tingkat, ADR-022 W1)
#             + satu env UPPERCASE per baris scripts/secrets.list yang BUKAN `pki`/`derived:*`/`host:*` (W-11)
# Env opsional: WHATSAPP_ENABLED WHATSAPP_PHONE_NUMBER_ID WHATSAPP_OTP_TEMPLATE OLLAMA_VISION_MODEL
#               COMPOSE_PROFILES (default core,data,ai,observability,ops; production wajib core,data,observability)
#               EFN_WG_BIND (default 10.88.0.1; WAJIB IP di interface wg0) EFN_WG_HTTPS_PORT (default 443)
#               EFN_WG_OUTLET_CIDR EFN_WG_STAFF_CIDR (di dalam 10.88.0.0/15, prefix >= /16)
#               ACME_DNS_PROVIDER (default cloudflare) ACME_DNS_TOKEN_ENV (default CF_DNS_API_TOKEN)
#               ACME_SERVER (default LE produksi; LE staging diizinkan) - service acme-dns (SEC-37)
#               EFN_PKI_EXTRA_TRUST (PEM CA lama selama rotasi CA) RABBITMQ_STRICT_PERMISSIONS
#               MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS PG_SUPERUSER ETL_ENABLE_ODOO (true|false)
#               ETL_ENABLE_GL ETL_AUDIT_COPY_ENABLED (true|false; kosong = aktif otomatis setelah grant, G12)
#               ETL_LOYALTY_RECON_NOT_BEFORE (UTC YYYY-MM-DDTHH:MM:SSZ, go-live loyalty POS; kosong = nilai .env lama, G14)
#               EFN_ODOO_PII_ENABLED EFN_ODOO_PII_REQUIRED (true|false; G21 AC-13: overlay docker-compose.pii.yml, kunci
#               host:* odoo_pii_* divalidasi; production ENABLED=true wajib REQUIRED=true)
#               G22 (docs/04 §14): FNB_QR_* / FNB_* (QR meja; FNB_QR_ENABLED=true -> API key QR host:* nyata per PT +
#               overlay docker-compose.online-qr-<pt>.yml), ONLINE_LOYALTY_*, LOYALTY_EXPIRY_* / LOYALTY_ACTIVITY_EPOCH /
#               LOYALTY_POINT_MAX_AGE_DAYS / LOYALTY_INACTIVE_ANONYMIZE* (L-73: production true hanya dengan
#               EFN_ALLOW_FEATURE_ON di /etc/efn/deploy.conf). LOYALTY_POINT_EXPIRY_DAYS dihapus (D-22-18): production ditolak.
#               LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT (default true, SEC-171: production false hanya dengan EFN_ALLOW_FEATURE_ON).
#               ETL_PARTNER_REGION_ENABLED ETL_LOYALTY_RECON_ONLINE_ENABLED ETL_ENABLE_KDS ETL_KDS_ONLINE_ENABLED
#               G23 (docs/04 §15): BOOKING_ENABLED BOOKING_COMPANIES BOOKING_* (reservasi meja; true -> API key booking
#               host:* nyata per PT + overlay docker-compose.online-booking-<pt>.yml), ONLINE_SLOTS_ENABLED (pre-order).
#               G27 (docs/04 §18): PROMO_ENABLED PROMO_PUSH_ENABLED PROMO_COMPANIES PROMO_SYNC_SECONDS (true -> API key
#               promo host:* nyata per PT + overlay docker-compose.online-promo-<pt>.yml), CONSENT_PRIVACY_VERSION
#               CONSENT_PRIVACY_TEXT_SHA256 PRIVACY_CONSENT_ENFORCED (persetujuan Kebijakan Privasi, §18.3a).
#               (true|false, default false; G21/G22 job ETL opsional, prasyarat dicek deploy-validate)
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
# shellcheck source=scripts/lib/deploy-validate.sh
source "$BASE/scripts/lib/deploy-validate.sh"

# ── 0. Validasi input non-rahasia (SEC-10/SEC-30: tidak ada nilai tak tervalidasi ke shell/compose/sed) ───────
[[ "${EUID}" -eq 0 ]] || die "harus dijalankan sebagai root (sudo); lihat docs/runbooks/key-custody.md"
# SEC-120 (L-25h): kunci host bersama efn-deploy / efn-rotate-online-key. Lewat efn-deploy kunci sudah dipegang di fd 8
# (diwarisi exec) -> flock pada deskripsi yang sama langsung berhasil; jalur manual Superadmin membuka & menunggu.
EFN_HOST_LOCK=/run/efn/deploy.lock
if [[ "$(readlink "/proc/$$/fd/8" 2> /dev/null)" == "$EFN_HOST_LOCK" ]]; then
  flock -n 8 || die "kunci host ${EFN_HOST_LOCK} tidak dapat diambil"
else
  install -d -o root -g root -m 0700 /run/efn
  exec 8> "$EFN_HOST_LOCK"
  flock -w 900 8 || die "kunci host ${EFN_HOST_LOCK} dipegang proses lain (rotasi API key?) > 900 s"
fi
[[ -z "${PKI_SOURCE:-}" || "${PKI_SOURCE}" == "intermediate" ]] \
  || die "PKI_SOURCE=${PKI_SOURCE} sudah dihapus (SEC-23); leaf selalu diterbitkan dari intermediate"
DIGEST_RE='^[a-z0-9.-]+(:[0-9]+)?/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$'
for v in IMAGE_POSTGRES IMAGE_ODOO IMAGE_API_GATEWAY IMAGE_SUPERAPP_WEB IMAGE_AI_OCR_WORKER IMAGE_ETL IMAGE_MINIO IMAGE_METABASE; do
  [[ "${!v:-}" =~ $DIGEST_RE ]] || die "$v harus <registry>/<image>@sha256:<64 hex> (dapat: '${!v:-}')"
done
[[ "$APP_ENV" =~ ^(production|staging)$ ]] || die "APP_ENV harus production|staging"
# SEC-50: topologi dipin di deploy.conf (env berbeda = ditolak), lalu validasi ketat topologi + konfigurasi
efn_load_pinned_conf /etc/efn/deploy.conf || die "pin topologi /etc/efn/deploy.conf"
efn_validate_topology "$APP_ENV" || die "topologi tidak valid (SEC-50)"
efn_validate_config "$APP_ENV" || die "konfigurasi tidak valid (SEC-50)"
# SEC-50a: IP wg-https WAJIB alamat di interface WireGuard (bukan sekadar IP yang ada di host, mis. IP publik)
ip -4 -o addr show dev wg0 2> /dev/null | grep -q " inet ${EFN_WG_BIND}/" \
  || die "IP WireGuard ${EFN_WG_BIND} tidak ada di interface wg0 (wg-quick@wg0 harus aktif sebelum deploy/docker)"
ACME_DNS_PROVIDER="${ACME_DNS_PROVIDER:-cloudflare}"
ACME_DNS_TOKEN_ENV="${ACME_DNS_TOKEN_ENV:-CF_DNS_API_TOKEN}"
# SEC-37: CA untuk service acme-dns (lego). Hanya Let's Encrypt produksi atau staging (divalidasi di lib).
ACME_SERVER="${ACME_SERVER:-https://acme-v02.api.letsencrypt.org/directory}"
WALG_S3_PREFIX="${WALG_S3_PREFIX:-s3://pg-backup/efn}"
[[ "$WALG_S3_PREFIX" =~ ^s3://[a-z0-9.-]+(/[A-Za-z0-9._-]+)*$ ]] || die "WALG_S3_PREFIX tidak valid"
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
  # W-11: secret yang dikelola di host (API key Odoo kanal online; dirotasi efn-rotate-online-key) TIDAK ditimpa.
  # Belum ada -> nilai awal (placeholder) agar compose bisa me-mount-nya.
  if [[ "$kind" == host:* ]]; then
    if [[ ! -s "$SECRETS_DIR/${name}.txt" ]]; then
      tmp=$(mktemp "$SECRETS_DIR/.${name}.XXXXXX")
      printf '%s' "${kind#host:}" > "$tmp"
      chmod "$SECRET_FILE_MODE" "$tmp"
      mv -f "$tmp" "$SECRETS_DIR/${name}.txt"
    fi
    continue
  fi
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
# G17 (docs/04 §10.4.1): online aktif = setiap kanal di ONLINE_CHANNELS wajib punya API key Odoo nyata
if [[ "${ONLINE_ENABLED:-false}" == true ]]; then
  IFS=, read -r -a ONLINE_CH_LIST <<< "${ONLINE_CHANNELS:-$EFN_ONLINE_BASE_CHANNEL}"
  for ch in "${ONLINE_CH_LIST[@]}"; do
    f="$SECRETS_DIR/odoo_api_key_online_$(tr '[:upper:]' '[:lower:]' <<< "$ch").txt"
    if [[ ! -s "$f" ]] || grep -qx 'disabled' "$f"; then
      die "ONLINE_ENABLED=true tetapi API key kanal ${ch} tidak ada/placeholder (docs/runbooks/odoo-online-api-key.md)"
    fi
  done
fi
# G21 AC-13 (docs/04 §13.2, ADR-036 §0): kunci PII Odoo produksi (host:disabled [SA]) dibuat Superadmin di host. Overlay
# docker-compose.pii.yml hanya dipasang bila ketiganya kunci 32 byte nyata & berbeda (bukan placeholder) - fail-closed.
if [[ "${EFN_ODOO_PII_ENABLED:-false}" == true ]]; then
  efn_validate_pii_keys "$SECRETS_DIR" \
    || die "EFN_ODOO_PII_ENABLED=true tetapi kunci PII Odoo tidak valid (docs/runbooks/key-custody.md §4e)"
  echo "==> Kunci PII Odoo valid (3 kunci, 32 byte, berbeda) - overlay docker-compose.pii.yml dipasang"
fi
# G22 (docs/04 §14.A.9/§14.A.10 API8): QR meja aktif = setiap PT di FNB_QR_COMPANIES wajib punya API key Odoo user teknis
# QR nyata (host:*, dibuat efn-rotate-online-key --channel=QR-<PT> --bootstrap). URL https sudah divalidasi lib.
FNB_QR_CO_LIST=()
if [[ "${FNB_QR_ENABLED:-false}" == true ]]; then
  efn_validate_fnb_qr_companies "${FNB_QR_COMPANIES:-PB,BCF}" || die "FNB_QR_COMPANIES tidak valid (G22)"
  IFS=, read -r -a FNB_QR_CO_LIST <<< "${FNB_QR_COMPANIES:-PB,BCF}"
  for co in "${FNB_QR_CO_LIST[@]}"; do
    f="$SECRETS_DIR/odoo_api_key_qr_$(tr '[:upper:]' '[:lower:]' <<< "$co").txt"
    if [[ ! -s "$f" ]] || grep -qx 'disabled' "$f"; then
      die "FNB_QR_ENABLED=true tetapi API key QR ${co} tidak ada/placeholder (docs/runbooks/odoo-qr-api-key.md)"
    fi
  done
fi

# G23 (docs/04 §15.3): reservasi aktif = setiap PT di BOOKING_COMPANIES wajib punya API key Odoo user teknis reservasi
# nyata (host:*, dibuat efn-rotate-online-key --channel=BOOKING-<PT> --bootstrap). HMAC event selalu random (secrets.list).
BOOKING_CO_LIST=()
if [[ "${BOOKING_ENABLED:-false}" == true ]]; then
  efn_validate_booking_companies "${BOOKING_COMPANIES:-PB,BCF}" || die "BOOKING_COMPANIES tidak valid (G23)"
  IFS=, read -r -a BOOKING_CO_LIST <<< "${BOOKING_COMPANIES:-PB,BCF}"
  for co in "${BOOKING_CO_LIST[@]}"; do
    f="$SECRETS_DIR/odoo_api_key_booking_$(tr '[:upper:]' '[:lower:]' <<< "$co").txt"
    if [[ ! -s "$f" ]] || grep -qx 'disabled' "$f"; then
      die "BOOKING_ENABLED=true tetapi API key reservasi ${co} tidak ada/placeholder (docs/runbooks/odoo-booking-api-key.md)"
    fi
  done
fi

# G27 (docs/04 §18.2): promo aktif = setiap PT di PROMO_COMPANIES wajib punya API key Odoo user teknis promo nyata
# (host:*, dibuat efn-rotate-online-key --channel=PROMO-<PT> --bootstrap). Daftar wajib eksplisit (deploy-validate).
PROMO_CO_LIST=()
if [[ "${PROMO_ENABLED:-false}" == true ]]; then
  efn_validate_promo_companies "${PROMO_COMPANIES:-}" || die "PROMO_COMPANIES tidak valid (G27)"
  IFS=, read -r -a PROMO_CO_LIST <<< "${PROMO_COMPANIES}"
  for co in "${PROMO_CO_LIST[@]}"; do
    f="$SECRETS_DIR/odoo_api_key_promo_$(tr '[:upper:]' '[:lower:]' <<< "$co").txt"
    if [[ ! -s "$f" ]] || grep -qx 'disabled' "$f"; then
      die "PROMO_ENABLED=true tetapi API key promo ${co} tidak ada/placeholder (docs/runbooks/odoo-promo-api-key.md)"
    fi
  done
fi

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
# SEC-42: trust anchor wajib = root CA yang sidik jarinya dipin di host (efn-deploy sudah memeriksa; ini lapis kedua
# untuk jalur manual Superadmin `sudo -E bash deploy.sh`).
PIN_ROOT="${EFN_PKI_ROOT_SHA256:-}"
if [[ -z "$PIN_ROOT" && -f /etc/efn/deploy.conf ]]; then
  PIN_ROOT=$(sed -n "s/^EFN_PKI_ROOT_SHA256='\{0,1\}\([0-9A-Fa-f]\{64\}\)'\{0,1\}\$/\1/p" /etc/efn/deploy.conf | tail -n1)
fi
[[ "${PIN_ROOT,,}" =~ ^[0-9a-f]{64}$ ]] || die "EFN_PKI_ROOT_SHA256 belum dipin di /etc/efn/deploy.conf (key-custody §4a)"
ROOT_FP=$(openssl x509 -in "$PKI_TMP/root.crt" -noout -fingerprint -sha256 | cut -d= -f2 | tr -d ':' | tr 'A-F' 'a-f')
[[ "$ROOT_FP" == "${PIN_ROOT,,}" ]] || die "EFN_PKI_ROOT_CRT tidak cocok dengan sidik jari root yang dipin di host"
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
# G12 (data-AI): mart GL (ETL_ENABLE_GL) dan salinan audit tamper-evident (ETL_AUDIT_COPY_ENABLED). Pola sama dengan
# R5: eksplisit menang, lalu nilai .env sebelumnya, default false; diaktifkan otomatis HANYA setelah grant sukses
# (grant butuh efn_audit >= 19.0.1.2.0 -> urutan: -u efn_audit -> deploy (grant) -> job aktif).
for v in ETL_ENABLE_GL ETL_AUDIT_COPY_ENABLED; do
  prev=$(sed -n "s/^${v}=\(true\|false\)$/\1/p" "$COMPOSE_DIR/.env" 2> /dev/null || true)
  printf -v "${v}_EFF" '%s' "${!v:-${prev:-false}}"
done

# G14 (data-AI): cutoff rekonsiliasi ledger poin vs pos.order (go-live loyalty POS, UTC). Pola R5: nilai manifest
# (tervalidasi efn_validate_config: format UTC kanonik, tidak di masa depan) menang, lalu nilai .env sebelumnya, default
# kosong (= semua order dalam jendela lookback). Production: MEMAJUKAN cutoff yang sudah ada = menyembunyikan periode
# dari rekonsiliasi -> hanya dengan izin Superadmin EFN_ALLOW_CONTROL_OFF='... ETL_LOYALTY_RECON_NOT_BEFORE' di
# /etc/efn/deploy.conf (SEC-50c). Memundurkan (lebih ketat) selalu boleh.
PREV_LOYALTY_NB=$(sed -n 's/^ETL_LOYALTY_RECON_NOT_BEFORE=\([0-9T:Z-]*\)$/\1/p' "$COMPOSE_DIR/.env" 2> /dev/null || true)
ETL_LOYALTY_RECON_NOT_BEFORE_EFF="${ETL_LOYALTY_RECON_NOT_BEFORE:-${PREV_LOYALTY_NB}}"
if [[ "$APP_ENV" == production && -n "$PREV_LOYALTY_NB" && "$ETL_LOYALTY_RECON_NOT_BEFORE_EFF" > "$PREV_LOYALTY_NB" ]]; then
  read -r -a _allow_off <<< "${EFN_ALLOW_CONTROL_OFF:-}"
  efn_list_has ETL_LOYALTY_RECON_NOT_BEFORE "${_allow_off[@]}" \
    || die "ETL_LOYALTY_RECON_NOT_BEFORE dimajukan (${PREV_LOYALTY_NB} -> ${ETL_LOYALTY_RECON_NOT_BEFORE_EFF}): ditolak di production tanpa EFN_ALLOW_CONTROL_OFF (SEC-50c)"
fi
# G21/G22 (QA G22 P2-3): job ETL opsional butuh ETL_ENABLE_ODOO=true efektif. Kosong = aktif otomatis setelah grant
# (langkah di bawah, lalu `up -d etl`); sampai saat itu container etl gagal start (efn_etl/config.py) -> peringatan.
if [[ "$ETL_ENABLE_ODOO_EFF" != true ]]; then
  for v in ETL_PARTNER_REGION_ENABLED ETL_ENABLE_KDS ETL_LOYALTY_RECON_ONLINE_ENABLED; do
    [[ "${!v:-false}" != true ]] \
      || echo "PERINGATAN: ${v}=true tetapi ETL_ENABLE_ODOO belum true - etl restart sampai grant Odoo sukses (langkah grant ETL)" >&2
  done
fi

echo "==> Menulis .env (non-secret)"
# SEC-50b: satu kunci satu baris; nilai dengan karakter kontrol ditolak; jumlah baris diverifikasi
DOTENV=(
  "TRAEFIK_DOMAIN=${TRAEFIK_DOMAIN}"
  "ACME_EMAIL=${ACME_EMAIL}"
  "IMAGE_POSTGRES=${IMAGE_POSTGRES}"
  "IMAGE_ODOO=${IMAGE_ODOO}"
  "IMAGE_API_GATEWAY=${IMAGE_API_GATEWAY}"
  "IMAGE_SUPERAPP_WEB=${IMAGE_SUPERAPP_WEB}"
  "IMAGE_AI_OCR_WORKER=${IMAGE_AI_OCR_WORKER}"
  "IMAGE_ETL=${IMAGE_ETL}"
  "IMAGE_MINIO=${IMAGE_MINIO}"
  "IMAGE_METABASE=${IMAGE_METABASE}"
  "APP_ENV=${APP_ENV}"
  "NODE_ENV=production"
  "PG_SUPERUSER=${PG_SUPERUSER:-postgres}"
  "PG_DB=efn_platform"
  "ODOO_DB=efn_odoo"
  "RABBITMQ_ADMIN_USER=efn-admin"
  "RABBITMQ_STRICT_PERMISSIONS=${RABBITMQ_STRICT_PERMISSIONS:-true}"
  "CLICKHOUSE_USER=efn"
  "GRAFANA_ADMIN_USER=admin"
  "OLLAMA_VISION_MODEL=${OLLAMA_VISION_MODEL:-qwen2.5vl:7b}"
  "WHATSAPP_ENABLED=${WHATSAPP_ENABLED:-false}"
  "WHATSAPP_PHONE_NUMBER_ID=${WHATSAPP_PHONE_NUMBER_ID:-}"
  "WHATSAPP_OTP_TEMPLATE=${WHATSAPP_OTP_TEMPLATE:-efn_otp}"
  "PAYMENT_PROVIDER=${PAYMENT_PROVIDER}"
  "PG_ARCHIVE_MODE=on"
  "WALG_S3_PREFIX=${WALG_S3_PREFIX:-s3://pg-backup/efn}"
  "TRUSTED_PROXIES=172.30.1.0/24,${CF_IPS}"
  "NODE_EXPORTER_TEXTFILE_DIR=${TEXTFILE_DIR}"
  "COMPOSE_PROFILES=${COMPOSE_PROFILES}"
  "EFN_PUBLIC_BIND=${EFN_PUBLIC_BIND}"
  "EFN_WG_BIND=${EFN_WG_BIND}"
  "EFN_WG_HTTPS_PORT=${EFN_WG_HTTPS_PORT}"
  "EFN_WG_OUTLET_CIDR=${EFN_WG_OUTLET_CIDR}"
  "EFN_WG_STAFF_CIDR=${EFN_WG_STAFF_CIDR}"
  "EFN_INTERNAL_EXTRA_CIDR=127.0.0.1/32"
  "ACME_DNS_PROVIDER=${ACME_DNS_PROVIDER}"
  "ACME_DNS_TOKEN_ENV=${ACME_DNS_TOKEN_ENV}"
  "ACME_SERVER=${ACME_SERVER}"
  "MARKET_SCOUT_PHOTO_RETENTION_DAYS=${MARKET_SCOUT_PHOTO_RETENTION_DAYS:-90}"
  "CH_MART_RETENTION_YEARS=${CH_MART_RETENTION_YEARS:-10}"
  # W-19 (data-AI): kosong = default compose (5 th / 90 h / 3650 h)
  "CH_PSEUDONYM_RETENTION_YEARS=${CH_PSEUDONYM_RETENTION_YEARS:-}"
  "CH_GEO_RETENTION_DAYS=${CH_GEO_RETENTION_DAYS:-}"
  "MB_AUDIT_MAX_RETENTION_DAYS=${MB_AUDIT_MAX_RETENTION_DAYS:-}"
  "CONSENT_LOCATION_VERSION=${CONSENT_LOCATION_VERSION:-}"
  "CONSENT_LOCATION_TEXT_SHA256=${CONSENT_LOCATION_TEXT_SHA256:-}"
  "CONSENT_MARKETING_VERSION=${CONSENT_MARKETING_VERSION:-}"
  "CONSENT_MARKETING_TEXT_SHA256=${CONSENT_MARKETING_TEXT_SHA256:-}"
  "DSAR_DELETE_GRACE_DAYS=${DSAR_DELETE_GRACE_DAYS:-7}"
  "ETL_ENABLE_ODOO=${ETL_ENABLE_ODOO_EFF}"
  "ETL_ENABLE_GL=${ETL_ENABLE_GL_EFF}"
  "ETL_AUDIT_COPY_ENABLED=${ETL_AUDIT_COPY_ENABLED_EFF}"
  "ETL_LOYALTY_RECON_NOT_BEFORE=${ETL_LOYALTY_RECON_NOT_BEFORE_EFF}"
  "MIGRATION_ENABLED=${MIGRATION_ENABLED:-false}"
  "METABASE_ADMIN_EMAIL=${METABASE_ADMIN_EMAIL:-${ACME_EMAIL}}"
  # G17 penjualan online (docs/04 §10.13): default MATI; angka kosong = default compose (PLACEHOLDER pilot)
  "ONLINE_ENABLED=${ONLINE_ENABLED:-false}"
  "ONLINE_CATALOG_ENABLED=${ONLINE_CATALOG_ENABLED:-false}"
  "LOYALTY_EARN_EXCLUDE_POINT_PAYMENT=${LOYALTY_EARN_EXCLUDE_POINT_PAYMENT:-true}"
  "LOYALTY_POINT_VALUE_IDR=${LOYALTY_POINT_VALUE_IDR:-}"
  "LOYALTY_RESTORE_TOLERANCE_PCT=${LOYALTY_RESTORE_TOLERANCE_PCT:-}"
  "LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS=${LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS:-}"
  "ETL_LOYALTY_POINTS_MODE=${ETL_LOYALTY_POINTS_MODE:-payment}"   # SEC-114 (L-25b)
  "ETL_AUDIT_EPOCH_ANCHORS=${ETL_AUDIT_EPOCH_ANCHORS:-}"   # G18: kosong = default compose 20
  "ONLINE_CHANNELS=${ONLINE_CHANNELS:-$EFN_ONLINE_BASE_CHANNEL}"
  "ONLINE_FULFILMENTS=${ONLINE_FULFILMENTS:-pickup}"
  "ONLINE_PAYMENT_TIMEOUT_SECONDS=${ONLINE_PAYMENT_TIMEOUT_SECONDS:-}"
  "ONLINE_QUOTE_TTL_SECONDS=${ONLINE_QUOTE_TTL_SECONDS:-}"
  "ONLINE_MAX_OPEN_ORDERS_PER_MEMBER=${ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:-}"
  "ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER=${ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:-}"   # G20 W-30: kosong = 1
  "ONLINE_PAYMENT_METHODS=${ONLINE_PAYMENT_METHODS:-}"   # G26 L-103: kosong = qris,bank_transfer; pilot tanpa vendor = bank_transfer
  "ONLINE_TRANSFER_MAX_UPLOADS=${ONLINE_TRANSFER_MAX_UPLOADS:-}"   # G26 §17.10: kosong = 3
  "ONLINE_TRANSFER_MIN_VERIFY_SECONDS=${ONLINE_TRANSFER_MIN_VERIFY_SECONDS:-}"   # G26 §17.10: kosong = 600
  "ONLINE_MAX_OPEN_TRANSFER_ORDERS_PER_MEMBER=${ONLINE_MAX_OPEN_TRANSFER_ORDERS_PER_MEMBER:-}"   # G26 §17.10: kosong = 1
  "ONLINE_CATALOG_SYNC_SECONDS=${ONLINE_CATALOG_SYNC_SECONDS:-}"
  "ONLINE_PICKUP_CODE_MAX_DAYS=${ONLINE_PICKUP_CODE_MAX_DAYS:-}"
  "ONLINE_ORDER_MEMBER_LINK_DAYS=${ONLINE_ORDER_MEMBER_LINK_DAYS:-}"
  "ONLINE_EVENT_RETENTION_DAYS=${ONLINE_EVENT_RETENTION_DAYS:-}"
  "ONLINE_ADMIN_AUDIT_RETENTION_DAYS=${ONLINE_ADMIN_AUDIT_RETENTION_DAYS:-}"
  "ODOO_RPC_TIMEOUT_MS=${ODOO_RPC_TIMEOUT_MS:-}"
  "ODOO_RPC_MAXCONN=${ODOO_RPC_MAXCONN:-}"
  "ODOO_RPC_PUBLIC_MAX_CONCURRENCY=${ODOO_RPC_PUBLIC_MAX_CONCURRENCY:-}"
  "ODOO_RPC_SAGA_MAX_CONCURRENCY=${ODOO_RPC_SAGA_MAX_CONCURRENCY:-}"
  # W-18/W-19 (10 Okt 2026): kosong = default compose (= keputusan: 14 / 365 / 17 / 90)
  "DSAR_OPEN_ORDER_MAX_DEFER_DAYS=${DSAR_OPEN_ORDER_MAX_DEFER_DAYS:-}"
  "ERASURE_SUPPRESSION_DAYS=${ERASURE_SUPPRESSION_DAYS:-}"
  "MEMBER_MIN_AGE=${MEMBER_MIN_AGE:-}"
  "PRICE_HUNTER_GEO_RETENTION_DAYS=${PRICE_HUNTER_GEO_RETENTION_DAYS:-}"
  # G21 AC-13: overlay docker-compose.pii.yml (kosong REQUIRED = default overlay true; production wajib eksplisit true)
  "EFN_ODOO_PII_ENABLED=${EFN_ODOO_PII_ENABLED:-false}"
  "EFN_ODOO_PII_REQUIRED=${EFN_ODOO_PII_REQUIRED:-}"
  # G22 (docs/04 §14): kosong = default compose (QR/poin online/hangus/anonimisasi MATI, DRY_RUN true, angka kontrak)
  "FNB_QR_ENABLED=${FNB_QR_ENABLED:-false}"
  "FNB_QR_COMPANIES=${FNB_QR_COMPANIES:-}"
  "FNB_QR_BASE_URL_PB=${FNB_QR_BASE_URL_PB:-}"
  "FNB_QR_BASE_URL_BCF=${FNB_QR_BASE_URL_BCF:-}"
  "FNB_QR_POINTS_ENABLED=${FNB_QR_POINTS_ENABLED:-false}"
  "FNB_TABLE_SESSION_TTL_SECONDS=${FNB_TABLE_SESSION_TTL_SECONDS:-}"
  "FNB_MAX_SUBMISSIONS_PER_SESSION=${FNB_MAX_SUBMISSIONS_PER_SESSION:-}"
  "FNB_MAX_ITEMS_PER_SESSION=${FNB_MAX_ITEMS_PER_SESSION:-}"
  "FNB_MAX_LINES=${FNB_MAX_LINES:-}"
  "FNB_MAX_LINE_QTY=${FNB_MAX_LINE_QTY:-}"
  "FNB_CAPTCHA_MODE=${FNB_CAPTCHA_MODE:-}"
  "FNB_MENU_SYNC_SECONDS=${FNB_MENU_SYNC_SECONDS:-}"
  "FNB_SESSION_RETENTION_DAYS=${FNB_SESSION_RETENTION_DAYS:-}"
  "FNB_SESSION_MEMBER_LINK_DAYS=${FNB_SESSION_MEMBER_LINK_DAYS:-}"
  "ONLINE_LOYALTY_REDEEM_ENABLED=${ONLINE_LOYALTY_REDEEM_ENABLED:-false}"
  "ONLINE_LOYALTY_EARN_ENABLED=${ONLINE_LOYALTY_EARN_ENABLED:-false}"
  "LOYALTY_EXPIRY_ENABLED=${LOYALTY_EXPIRY_ENABLED:-false}"
  "LOYALTY_EXPIRY_DRY_RUN=${LOYALTY_EXPIRY_DRY_RUN:-}"
  "LOYALTY_INACTIVITY_EXPIRY_MONTHS=${LOYALTY_INACTIVITY_EXPIRY_MONTHS:-}"
  "LOYALTY_EXPIRY_NOTICE_DAYS=${LOYALTY_EXPIRY_NOTICE_DAYS:-}"
  "LOYALTY_EXPIRY_RUN_HOUR_WIB=${LOYALTY_EXPIRY_RUN_HOUR_WIB:-}"
  "LOYALTY_EXPIRY_NOTICE_HOUR_WIB=${LOYALTY_EXPIRY_NOTICE_HOUR_WIB:-}"
  "LOYALTY_EXPIRY_BATCH_SIZE=${LOYALTY_EXPIRY_BATCH_SIZE:-}"
  "LOYALTY_EXPIRY_MAX_PER_RUN=${LOYALTY_EXPIRY_MAX_PER_RUN:-}"
  "LOYALTY_ACTIVITY_EPOCH=${LOYALTY_ACTIVITY_EPOCH:-}"
  "LOYALTY_POINT_MAX_AGE_DAYS=${LOYALTY_POINT_MAX_AGE_DAYS:-}"
  "LOYALTY_INACTIVE_ANONYMIZE=${LOYALTY_INACTIVE_ANONYMIZE:-false}"
  "LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS=${LOYALTY_INACTIVE_ANONYMIZE_GRACE_DAYS:-}"
  "LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT=${LOYALTY_EXPIRY_NOTICE_REQUIRE_CONSENT:-true}"   # SEC-171 r15
  # G23 (docs/04 §15): kosong = default compose (reservasi & slot pre-order MATI, angka kontrak)
  "BOOKING_ENABLED=${BOOKING_ENABLED:-false}"
  "BOOKING_COMPANIES=${BOOKING_COMPANIES:-}"
  "BOOKING_MAX_ACTIVE_PER_MEMBER=${BOOKING_MAX_ACTIVE_PER_MEMBER:-}"
  "BOOKING_MEMBER_LINK_DAYS=${BOOKING_MEMBER_LINK_DAYS:-}"
  "BOOKING_RETENTION_DAYS=${BOOKING_RETENTION_DAYS:-}"
  "ONLINE_SLOTS_ENABLED=${ONLINE_SLOTS_ENABLED:-false}"
  # G27 (docs/04 §18): promo Super-App + persetujuan Kebijakan Privasi; kosong = default compose (MATI)
  "PROMO_ENABLED=${PROMO_ENABLED:-false}"
  "PROMO_PUSH_ENABLED=${PROMO_PUSH_ENABLED:-false}"
  "PROMO_COMPANIES=${PROMO_COMPANIES:-}"
  "PROMO_SYNC_SECONDS=${PROMO_SYNC_SECONDS:-}"
  # G31 (docs/04 §20.8): engine MGM; kosong = default compose (MATI)
  "MGM_ENABLED=${MGM_ENABLED:-false}"
  "MGM_CLAIM_WINDOW_DAYS=${MGM_CLAIM_WINDOW_DAYS:-}"
  "MGM_SETTLE_INTERVAL_MS=${MGM_SETTLE_INTERVAL_MS:-}"
  "CONSENT_PRIVACY_VERSION=${CONSENT_PRIVACY_VERSION:-}"
  "CONSENT_PRIVACY_TEXT_SHA256=${CONSENT_PRIVACY_TEXT_SHA256:-}"
  "PRIVACY_CONSENT_ENFORCED=${PRIVACY_CONSENT_ENFORCED:-false}"
  # G21/G22 job ETL opsional (QA G22 P2-3): default MATI; prasyarat ETL_ENABLE_ODOO + grant terbaru
  "ETL_PARTNER_REGION_ENABLED=${ETL_PARTNER_REGION_ENABLED:-false}"
  "ETL_LOYALTY_RECON_ONLINE_ENABLED=${ETL_LOYALTY_RECON_ONLINE_ENABLED:-false}"
  "ETL_ENABLE_KDS=${ETL_ENABLE_KDS:-false}"
  "ETL_KDS_ONLINE_ENABLED=${ETL_KDS_ONLINE_ENABLED:-false}"
)
efn_write_dotenv "$COMPOSE_DIR/.env" "${DOTENV[@]}" || die "gagal menulis .env (SEC-50)"

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

echo "==> ACME email (resolver HTTP-01 Traefik) & domain target probe"
# DNS-01 (wg-https) bukan lagi di Traefik: service acme-dns membaca ACME_* dari .env (SEC-37)
sed -i "s|email: \".*\"|email: \"${ACME_EMAIL}\"|" "$BASE/traefik/traefik.yml"
sed -i "s|efn\.local|${TRAEFIK_DOMAIN}|g" "$BASE/observability/targets/blackbox-https.yml"
# SEC-37: SNI probe sertifikat wg-https (module tls_wg_public)
sed -i "s|server_name: erp\.efn\.local|server_name: erp.${TRAEFIK_DOMAIN}|" "$BASE/observability/blackbox.yml"

echo "==> Login GHCR"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

cd "$COMPOSE_DIR"
FILES=(-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.host.yml)
# Kunci API payment hanya di-mount bila provider nyata (docs/04 §8.3); mock/disabled tidak me-mount kunci
if [[ "$PAYMENT_PROVIDER" =~ ^(xendit|midtrans)$ ]]; then FILES+=(-f docker-compose.payment.yml); fi
# G21 AC-13: kunci PII Odoo hanya di-mount (ke odoo saja) bila diaktifkan PO + kunci tervalidasi di langkah 1 (L-59)
if [[ "${EFN_ODOO_PII_ENABLED:-false}" == true ]]; then
  [[ -f docker-compose.pii.yml ]] || die "EFN_ODOO_PII_ENABLED=true tetapi infra/compose/docker-compose.pii.yml tidak ada di bundel"
  FILES+=(-f docker-compose.pii.yml)
fi
# G17/G19/G20 (docs/04 §12.1): API key kanal selain GL (base) hanya di-mount bila kanal ada di ONLINE_CHANNELS: satu
# overlay per kanal docker-compose.online-<kode huruf kecil>.yml (PB -> online-pb, BCR -> online-bcr, BCF -> online-bcf,
# WTZ -> online-wtz, G24).
# Kode di luar EFN_ONLINE_CHANNEL_CODES (termasuk RTL/FNB/RTL-GL.. lama) atau kanal tanpa overlay = konfigurasi salah ->
# gagal (BFF akan menolak kanal tanpa key; lebih baik berhenti di sini).
efn_validate_online_channels "${ONLINE_CHANNELS:-$EFN_ONLINE_BASE_CHANNEL}" || die "ONLINE_CHANNELS tidak valid (G20)"
IFS=, read -r -a ONLINE_OVERLAY_CH <<< "${ONLINE_CHANNELS:-$EFN_ONLINE_BASE_CHANNEL}"
for ch in "${ONLINE_OVERLAY_CH[@]}"; do
  [[ "$ch" == "$EFN_ONLINE_BASE_CHANNEL" ]] && continue
  ov="docker-compose.online-$(tr '[:upper:]' '[:lower:]' <<< "$ch").yml"
  [[ -f "$ov" ]] || die "kanal ${ch} di ONLINE_CHANNELS tanpa overlay infra/compose/${ov} (docs/runbooks/odoo-online-api-key.md#kanal-baru)"
  [[ " ${FILES[*]} " == *" ${ov} "* ]] || FILES+=(-f "$ov")
done
# G22 (docs/04 §14.A.9): API key QR meja per PT hanya di-mount saat FNB_QR_ENABLED=true (daftar & key dicek di langkah 1)
for co in "${FNB_QR_CO_LIST[@]}"; do
  ov="docker-compose.online-qr-$(tr '[:upper:]' '[:lower:]' <<< "$co").yml"
  [[ -f "$ov" ]] || die "PT ${co} di FNB_QR_COMPANIES tanpa overlay infra/compose/${ov} (bundel rilis G22+?)"
  [[ " ${FILES[*]} " == *" ${ov} "* ]] || FILES+=(-f "$ov")
done
# G23 (docs/04 §15.3): API key reservasi per PT hanya di-mount saat BOOKING_ENABLED=true (daftar & key dicek di langkah 1)
for co in "${BOOKING_CO_LIST[@]}"; do
  ov="docker-compose.online-booking-$(tr '[:upper:]' '[:lower:]' <<< "$co").yml"
  [[ -f "$ov" ]] || die "PT ${co} di BOOKING_COMPANIES tanpa overlay infra/compose/${ov} (bundel rilis G23+?)"
  [[ " ${FILES[*]} " == *" ${ov} "* ]] || FILES+=(-f "$ov")
done
# G27 (docs/04 §18.2): API key promo per PT hanya di-mount saat PROMO_ENABLED=true (daftar & key dicek di langkah 1)
for co in "${PROMO_CO_LIST[@]}"; do
  ov="docker-compose.online-promo-$(tr '[:upper:]' '[:lower:]' <<< "$co").yml"
  [[ -f "$ov" ]] || die "PT ${co} di PROMO_COMPANIES tanpa overlay infra/compose/${ov} (bundel rilis G27+?)"
  [[ " ${FILES[*]} " == *" ${ov} "* ]] || FILES+=(-f "$ov")
done
DC=(docker compose "${FILES[@]}")
# W-11: daftar file compose untuk skrip host lain (efn-rotate-online-key me-recreate BFF dengan konfigurasi yang SAMA)
printf '%s\n' "${FILES[@]}" | grep -v '^-f$' > .efn-compose-files.tmp && mv -f .efn-compose-files.tmp .efn-compose-files

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
# G13c: migrasi yang WAJIB ada sebelum BFF rilis ini start (bundel tidak lengkap = gagal, bukan dilewati diam-diam):
# 05 = loyalty POS (G13), 06 = pagar earn + daftar tolak erasure (SEC-61/SEC-65; tanpa 06 job erasure BFF gagal),
# 07 = penjualan online G17 (docs/04 §10.7/§10.8: payment_intent.purpose + tabel online_*; dibutuhkan BFF G17
# termasuk saat ONLINE_ENABLED=false). 08 = privasi G18 (W-18/W-19: dsar_request.open_orders_ack_at/defer_escalation,
# member.age_confirmed_at; dibutuhkan BFF G18). 09 = G20 (docs/04 §12.1/§12.2: CHECK company_code 6 entitas,
# online_order.payment_method, status awaiting_counter_payment, counter_code_enc; dibutuhkan BFF G20). 13 = G24
# (docs/04 §16.3: CHECK kode PT + WTZ; tanpa 13 order/poin kanal WTZ ditolak CHECK).
# 14 = G26 (docs/04 §17.9: payment_method bank_transfer, kolom transfer_*, tabel online_transfer_proof; dibutuhkan BFF
# G26). 15 = G27 (docs/04 §18.2: promo_item, promo_sync, push_token; dibutuhkan BFF G27). 17 = G31 (docs/04 §20:
# tabel mgm_*; earn/reverse POS & online menulis mgm_source/mgm_reversal).
for req in 04-platform-v3-6.sql 05-platform-loyalty-pos.sql 06-platform-loyalty-guard.sql 07-platform-online.sql \
           08-platform-g18-privacy.sql 09-platform-g20.sql 10-platform-g21.sql 11-platform-g22.sql 12-platform-g23.sql \
           13-platform-g24.sql 14-platform-g26.sql 15-platform-g27.sql 16-platform-g27b.sql \
           17-platform-g31.sql; do
  [[ -s "$BASE/postgres/init/$req" ]] || die "migrasi wajib $req tidak ada di $BASE/postgres/init (bundel rilis tidak lengkap)"
done
for mig in "$BASE"/postgres/init/[0-9][0-9]-*.sql; do
  [[ -e "$mig" ]] || continue
  num=$(basename "$mig"); num=${num%%-*}
  (( 10#$num >= 4 )) || continue
  echo "    $(basename "$mig")"
  "${DC[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U "${PG_SUPERUSER:-postgres}" -d postgres \
    -f "/docker-entrypoint-initdb.d/$(basename "$mig")" || die "migrasi $(basename "$mig") gagal"
done

# G21 AC-13: efn_pii yang sudah terpasang di DB efn_odoo tanpa overlay kunci = Odoo start tanpa kunci (data partner
# terlindungi tidak terbaca, mode terkunci). Dicek SEBELUM `up` agar container Odoo lama tidak di-recreate tanpa kunci.
# DB/tabel belum ada (instalasi baru) = lewati; query gagal saat tabel ada = gagal (fail-closed).
if "${DC[@]}" exec -T postgres psql -X -q -t -A -U efn_odoo -d efn_odoo \
     -c "SELECT to_regclass('public.ir_module_module') IS NOT NULL" 2> /dev/null | grep -qx t; then
  pii_state=$("${DC[@]}" exec -T postgres psql -X -q -t -A -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo \
    -c "SELECT state FROM ir_module_module WHERE name = 'efn_pii'") || die "status modul efn_pii tidak dapat dibaca"
  efn_pii_module_state_ok "${EFN_ODOO_PII_ENABLED:-false}" "$pii_state" \
    || die "efn_pii terpasang tanpa kunci: set EFN_ODOO_PII_ENABLED=true (+ kunci, docs/runbooks/key-custody.md §4e) lalu ulangi deploy"
fi

# W-19 (data-AI G18): clickhouse-init dapat memigrasi tabel (loyalty_events: salin + EXCHANGE, jendela 1 jam). ETL lama
# yang masih berjalan selama itu bisa menyalin ulang baris subjek yang baru dihapus -> hentikan ETL dulu; `up -d`
# menyalakannya lagi SETELAH clickhouse-init exit 0 (depends_on service_completed_successfully). ETL inkremental:
# jeda beberapa menit tidak kehilangan data.
if [[ ",${COMPOSE_PROFILES}," == *",data,"* ]]; then
  echo "==> Stop etl selama clickhouse-init (migrasi/TTL ClickHouse)"
  "${DC[@]}" stop etl > /dev/null 2>&1 || true
fi

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
  # G13: Metabase yang belum di-setup dapat diklaim siapa pun di subnet staf -> provision.py jalan otomatis begitu
  # metabase sehat (depends_on di compose, tanpa jeda manual); di sini hanya dipastikan sukses.
  echo "==> metabase-provision (setup admin, koneksi ClickHouse TLS strict, dashboard) harus sukses"
  for _ in $(seq 1 60); do
    state=$("${DC[@]}" ps -a --format json metabase-provision | jq -r '(if type=="array" then .[] else . end) | .State' | head -n1)
    [[ "$state" == "exited" ]] && break
    sleep 5
  done
  check_oneshot metabase-provision || die "metabase-provision gagal - Metabase JANGAN dibiarkan belum ter-setup (runbook)"
  # SEC-86: container yang BERJALAN memakai wrapper kunci enkripsi at rest (bukan hanya konfigurasi compose).
  mb_ep=$(docker inspect --format '{{json .Config.Entrypoint}}' "$("${DC[@]}" ps -q metabase)")
  [[ "$mb_ep" == '["/usr/local/bin/efn-metabase-entrypoint"]' ]] \
    || die "metabase berjalan tanpa efn-metabase-entrypoint (entrypoint=$mb_ep): enkripsi at rest tidak terjamin"
fi
[[ "${MIGRATION_ENABLED:-false}" != true ]]   || echo "PERINGATAN: MIGRATION_ENABLED=true (endpoint migrasi K-7 aktif) - matikan lagi setelah migrasi selesai" >&2

# QA U1: grant baca level-kolom efn_etl pada tabel Odoo (file milik data-AI). Dijalankan sebagai efn_odoo
# (pemilik tabel, lewat socket lokal di container postgres) setelah Odoo ter-install; idempoten, ON_ERROR_STOP.
if [[ -s "$ETL_GRANTS_SQL" ]]; then
  if "${DC[@]}" exec -T postgres psql -X -q -t -A -U efn_odoo -d efn_odoo \
       -c "SELECT to_regclass('public.pos_order') IS NOT NULL" 2> /dev/null | grep -qx t; then
    # SEC-129 (r11): modul efn wajib (EFN_REQUIRED_ODOO_MODULES, deploy-validate.sh) harus `installed` - tanpa
    # efn_pos_refund_origin, refund POS tidak lagi dipaksa ke metode bayar asal (E-31) dan tidak ada yang tahu (SEC-122).
    mods_sql=$(printf "'%s'," "${EFN_REQUIRED_ODOO_MODULES[@]}"); mods_sql="${mods_sql%,}"
    missing=$("${DC[@]}" exec -T postgres psql -X -q -t -A -U efn_odoo -d efn_odoo \
      -c "SELECT name || '|' || state FROM ir_module_module WHERE name IN (${mods_sql})" \
      | efn_odoo_modules_missing | tr '\n' ' ') \
      || die "modul Odoo wajib belum terpasang di efn_odoo: ${missing}- install/upgrade lewat runbook rilis Odoo
    (docs/runbooks/deploy-odoo-modules.md), lalu ulangi deploy"
    echo "==> Modul Odoo wajib terpasang: ${EFN_REQUIRED_ODOO_MODULES[*]}"
    # G12: grant memuat efn_audit_log.origin_user_id (efn_audit >= 19.0.1.2.0). Pesan jelas bila modul belum
    # di-upgrade (grant sengaja gagal ON_ERROR_STOP; job lama tetap jalan dengan grant sebelumnya).
    "${DC[@]}" exec -T postgres psql -X -q -t -A -U efn_odoo -d efn_odoo -c \
      "SELECT 1 FROM information_schema.columns WHERE table_name='efn_audit_log' AND column_name='origin_user_id'" \
      | grep -qx 1 || die "efn_audit belum >= 19.0.1.2.0 (kolom origin_user_id tidak ada): jalankan upgrade modul
    (docs/runbooks/dev-stack-boot.md §3a / runbook rilis Odoo) lalu ulangi deploy - grant ETL & job GL/audit menunggu"
    # G14: blok grant rekonsiliasi loyalty butuh efn_pos_loyalty_ledger TERPASANG (kolom pos_order.efn_loyalty_state,
    # pos_config.efn_loyalty_earn_enabled, tabel outbox/reservation). Dicek SEBELUM menjalankan file grant: tanpa
    # ini psql berhenti di tengah file (ON_ERROR_STOP) dengan pesan "column does not exist" yang membingungkan.
    # Urutan wajib: install/upgrade efn_pos_loyalty_ledger -> deploy (grant) -> job loyalty_recon ETL jalan.
    g14_cols=$("${DC[@]}" exec -T postgres psql -X -q -t -A -U efn_odoo -d efn_odoo -c \
      "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND (
         (table_name='pos_order' AND column_name IN ('uuid','efn_loyalty_state'))
         OR (table_name='pos_config' AND column_name='efn_loyalty_earn_enabled')
         OR (table_name='efn_pos_loyalty_outbox' AND column_name='original_order_id')
         OR (table_name='efn_pos_loyalty_reservation' AND column_name='order_uuid'))" | tr -d '[:space:]')
    [[ "$g14_cols" == 5 ]] || die "efn_pos_loyalty_ledger belum terpasang di DB efn_odoo (${g14_cols:-0}/5 kolom G14):
    install/upgrade modul dulu (runbook rilis Odoo), lalu ulangi deploy - grant ETL (termasuk G14) belum diterapkan"
    echo "==> Grant ETL di DB Odoo ($(basename "$ETL_GRANTS_SQL"))"
    "${DC[@]}" exec -T postgres psql -X -q -v ON_ERROR_STOP=1 -U efn_odoo -d efn_odoo -f - < "$ETL_GRANTS_SQL" \
      || die "grant ETL di DB Odoo gagal"
    # R5 (gelombang 9) + G12: job ETL Odoo aktif otomatis setelah grant sukses, kecuali dimatikan eksplisit
    # (<VAR>=false di GitHub Environment vars).
    etl_changed=0
    for v in ETL_ENABLE_ODOO ETL_ENABLE_GL ETL_AUDIT_COPY_ENABLED; do
      eff_name="${v}_EFF"
      if [[ -z "${!v:-}" && "${!eff_name}" != "true" && ",${COMPOSE_PROFILES}," == *",data,"* ]]; then
        sed -i "s/^${v}=.*/${v}=true/" "$COMPOSE_DIR/.env"
        etl_changed=1
      fi
    done
    [[ "$etl_changed" == 0 ]] || "${DC[@]}" up -d etl
  else
    echo "    DB efn_odoo belum ter-install (pos_order belum ada) - grant ETL dilewati, ulangi deploy setelah install"
  fi
fi

echo "==> Bersihkan image lama"
docker image prune -af --filter "until=168h" > /dev/null || true
docker logout ghcr.io
echo "==> Deploy selesai: $(sed -n 's/^IMAGE_API_GATEWAY=//p' .env)"
