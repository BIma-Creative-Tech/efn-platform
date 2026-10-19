# shellcheck shell=bash
# Validasi konfigurasi deploy NON-RAHASIA (SEC-50, gelombang 13). Di-source oleh:
#   - scripts/host/efn-deploy  (dari rilis yang SUDAH terverifikasi tanda tangannya, sebelum apa pun ditulis ke /opt/efn)
#   - scripts/deploy.sh        (lapis kedua + jalur manual Superadmin `sudo -E bash deploy.sh`)
#   - scripts/host/test-efn-deploy.sh (uji unit fungsi di bawah)
# Tidak menjalankan apa pun saat di-source; hanya mendefinisikan fungsi + daftar nama. Fungsi mengembalikan non-0
# dan mencetak alasan ke stderr (pemanggil yang memutuskan `die`).
#
# Asal nilai (SEC-50, docs/runbooks/key-custody.md §4c):
#   EFN_HOST_PINNED_VARS  -> /etc/efn/deploy.conf milik root (topologi host). DITOLAK dari env stdin efn-deploy.
#   EFN_RELEASE_CONFIG_VARS -> objek `config` di manifest efn-release.json (DI DALAM arsip bertanda tangan cosign).
#                            DITOLAK dari env stdin efn-deploy.
#   Env stdin efn-deploy hanya: GHCR_USER, GHCR_TOKEN, EFN_PKI_* dan nama di scripts/secrets.list (secret).

# Topologi host (pemegang kunci SSH deploy tidak boleh bisa membuka erp./bi./ops. ke internet, SEC-50a)
EFN_HOST_PINNED_VARS=(EFN_PUBLIC_BIND EFN_WG_BIND EFN_WG_HTTPS_PORT EFN_WG_OUTLET_CIDR EFN_WG_STAFF_CIDR
  TRAEFIK_DOMAIN COMPOSE_PROFILES EFN_ALLOW_CONTROL_OFF)
# Konfigurasi aplikasi non-rahasia, ditandatangani bersama rilis (SEC-50b/c)
EFN_RELEASE_CONFIG_VARS=(ACME_EMAIL PAYMENT_PROVIDER PG_SUPERUSER WHATSAPP_ENABLED WHATSAPP_PHONE_NUMBER_ID
  WHATSAPP_OTP_TEMPLATE ACME_DNS_PROVIDER ACME_DNS_TOKEN_ENV ACME_SERVER RABBITMQ_STRICT_PERMISSIONS
  MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS CONSENT_LOCATION_VERSION CONSENT_LOCATION_TEXT_SHA256
  CONSENT_MARKETING_VERSION CONSENT_MARKETING_TEXT_SHA256 DSAR_DELETE_GRACE_DAYS ETL_ENABLE_ODOO ETL_ENABLE_GL
  ETL_AUDIT_COPY_ENABLED OLLAMA_VISION_MODEL MIGRATION_ENABLED METABASE_ADMIN_EMAIL ETL_LOYALTY_RECON_NOT_BEFORE
  ONLINE_ENABLED ONLINE_CATALOG_ENABLED ONLINE_CHANNELS ONLINE_FULFILMENTS ONLINE_PAYMENT_TIMEOUT_SECONDS
  ONLINE_QUOTE_TTL_SECONDS ONLINE_MAX_OPEN_ORDERS_PER_MEMBER ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER ONLINE_PAYMENT_METHODS
  ONLINE_CATALOG_SYNC_SECONDS ONLINE_PICKUP_CODE_MAX_DAYS
  ONLINE_ORDER_MEMBER_LINK_DAYS ONLINE_EVENT_RETENTION_DAYS ONLINE_ADMIN_AUDIT_RETENTION_DAYS ODOO_RPC_TIMEOUT_MS ODOO_RPC_MAXCONN
  ODOO_RPC_PUBLIC_MAX_CONCURRENCY ODOO_RPC_SAGA_MAX_CONCURRENCY
  ERASURE_SUPPRESSION_DAYS DSAR_OPEN_ORDER_MAX_DEFER_DAYS MEMBER_MIN_AGE PRICE_HUNTER_GEO_RETENTION_DAYS
  CH_PSEUDONYM_RETENTION_YEARS CH_GEO_RETENTION_DAYS MB_AUDIT_MAX_RETENTION_DAYS
  LOYALTY_EARN_EXCLUDE_POINT_PAYMENT ETL_LOYALTY_POINTS_MODE LOYALTY_POINT_VALUE_IDR LOYALTY_RESTORE_TOLERANCE_PCT
  LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS ETL_AUDIT_EPOCH_ANCHORS
  EFN_ODOO_PII_ENABLED EFN_ODOO_PII_REQUIRED)
# G21 AC-13 (docs/04 §13.2, ADR-036 §0): kunci PII Odoo produksi (scripts/secrets.list host:disabled [SA]) - di-mount ke
# odoo oleh infra/compose/docker-compose.pii.yml hanya bila EFN_ODOO_PII_ENABLED=true (scripts/deploy.sh).
EFN_ODOO_PII_KEY_NAMES=(odoo_pii_key_v1 odoo_pii_bidx_key odoo_pii_name_key)
# G17 (docs/04 §10.13): saklar KHUSUS dev/test. Bukan konfigurasi rilis (kunci manifest tidak dikenal = ditolak
# efn-deploy); efn_validate_config juga menolak nilai true di staging/production lewat jalur manual deploy.sh.
EFN_DEV_ONLY_SWITCHES=(ODOO_ONLINE_MOCK EFN_PAYMENT_SIMULATOR ETL_AUDIT_EPOCH_AUTO)
# Kontrol keamanan yang di production hanya boleh `false` bila nama-nya ada di EFN_ALLOW_CONTROL_OFF (deploy.conf)
# (ETL_LOYALTY_RECON_NOT_BEFORE: bukan boolean; izin = boleh MEMAJUKAN cutoff di production, dicek scripts/deploy.sh)
EFN_SECURITY_CONTROLS=(RABBITMQ_STRICT_PERMISSIONS ETL_AUDIT_COPY_ENABLED ETL_LOYALTY_RECON_NOT_BEFORE)
# G20 (docs/04 §12.1, L-48): kode kanal online = kode PT. Satu-satunya daftar yang diterima ONLINE_CHANNELS; GL di-mount
# compose base, sisanya lewat infra/compose/docker-compose.online-<kode huruf kecil>.yml (scripts/deploy.sh). Kode lama
# RTL/FNB dan kanal G19 berhifen DITOLAK. Kanal baru = tambah di sini + scripts/secrets.list + compose + overlay +
# ALLOWED_CHANNELS di scripts/host/efn-rotate-online-key (runbook odoo-online-api-key.md#kanal-baru).
EFN_ONLINE_CHANNEL_CODES=(GL PB BCR BCF)
EFN_ONLINE_BASE_CHANNEL=GL
# Profil compose yang dikenal; production wajib memuat core,data,observability (alert/Loki/salinan audit)
EFN_KNOWN_PROFILES=(core data ai observability ops)
EFN_REQUIRED_PROD_PROFILES=(core data observability)
# Subnet WireGuard hub (infra/wireguard: outlet 10.88.0.0/16, staf 10.89.0.0/16)
EFN_WG_SUPERNET=10.88.0.0/15

_efn_err() { echo "validasi: $*" >&2; return 1; }

# Nilai tidak boleh memuat karakter kontrol (newline, CR, tab, ESC, DEL, ...): mencegah injeksi baris ke .env (SEC-50b)
efn_no_cntrl() { # nama nilai
  local LC_ALL=C
  [[ "$2" != *[[:cntrl:]]* ]] || _efn_err "$1 memuat karakter kontrol/multi-baris (ditolak)"
}

# IPv4 kanonik: 4 oktet 0-255 tanpa nol di depan (hindari tafsir oktal/ambigu)
efn_is_ipv4() {
  [[ "$1" =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]] || return 1
  local i
  for i in 1 2 3 4; do (( BASH_REMATCH[i] <= 255 )) || return 1; done
}
efn_ip2int() {
  local a b c d
  IFS=. read -r a b c d <<< "$1"
  echo $(( (a << 24) | (b << 16) | (c << 8) | d ))
}
# CIDR kanonik: alamat jaringan (bit host nol), prefix 0-32
efn_is_cidr() {
  [[ "$1" == */* ]] || return 1
  local ip="${1%/*}" p="${1#*/}"
  efn_is_ipv4 "$ip" || return 1
  [[ "$p" =~ ^(0|[1-9][0-9]?)$ ]] && (( p <= 32 )) || return 1
  local mask=$(( p == 0 ? 0 : (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF ))
  (( ($(efn_ip2int "$ip") & mask) == $(efn_ip2int "$ip") ))
}
efn_ip_in_cidr() { # ip cidr
  local net="${2%/*}" p="${2#*/}"
  local mask=$(( p == 0 ? 0 : (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF ))
  (( ($(efn_ip2int "$1") & mask) == $(efn_ip2int "$net") ))
}
efn_cidr_within() { # inner outer
  (( ${1#*/} >= ${2#*/} )) && efn_ip_in_cidr "${1%/*}" "$2"
}

efn_list_has() { # nilai elemen...
  local x="$1"; shift
  local e
  for e in "$@"; do [[ "$e" == "$x" ]] && return 0; done
  return 1
}

# ── Topologi host (deploy.conf) ────────────────────────────────────────────────────────────────────────────────
# Mengisi default yang sama dengan deploy.sh lama lalu memvalidasi. Variabel global diubah (default).
efn_validate_topology() { # app_env
  local app_env="$1" v
  EFN_WG_BIND="${EFN_WG_BIND:-10.88.0.1}"
  EFN_WG_HTTPS_PORT="${EFN_WG_HTTPS_PORT:-443}"
  EFN_WG_OUTLET_CIDR="${EFN_WG_OUTLET_CIDR:-10.88.0.0/16}"
  EFN_WG_STAFF_CIDR="${EFN_WG_STAFF_CIDR:-10.89.0.0/16}"
  COMPOSE_PROFILES="${COMPOSE_PROFILES:-core,data,ai,observability,ops}"
  EFN_ALLOW_CONTROL_OFF="${EFN_ALLOW_CONTROL_OFF:-}"
  for v in "${EFN_HOST_PINNED_VARS[@]}"; do efn_no_cntrl "$v" "${!v:-}" || return 1; done

  [[ "${TRAEFIK_DOMAIN:-}" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] \
    || _efn_err "TRAEFIK_DOMAIN tidak valid (huruf kecil, mis. efn.example)" || return 1

  efn_is_ipv4 "${EFN_PUBLIC_BIND:-}" || _efn_err "EFN_PUBLIC_BIND wajib IPv4 (IP host untuk 80/443 websecure)" || return 1
  [[ "$EFN_PUBLIC_BIND" != 0.0.0.0 ]] && ! efn_ip_in_cidr "$EFN_PUBLIC_BIND" 127.0.0.0/8 \
    || _efn_err "EFN_PUBLIC_BIND tidak boleh 0.0.0.0/loopback" || return 1
  ! efn_ip_in_cidr "$EFN_PUBLIC_BIND" "$EFN_WG_SUPERNET" \
    || _efn_err "EFN_PUBLIC_BIND tidak boleh di subnet WireGuard ${EFN_WG_SUPERNET}" || return 1

  # SEC-50a: entrypoint wg-https (erp./bi./ops.) HANYA di IP WireGuard hub - bukan IP publik/0.0.0.0
  efn_is_ipv4 "$EFN_WG_BIND" || _efn_err "EFN_WG_BIND bukan IPv4" || return 1
  efn_ip_in_cidr "$EFN_WG_BIND" 10.88.0.0/16 || efn_ip_in_cidr "$EFN_WG_BIND" 10.89.0.0/16 \
    || _efn_err "EFN_WG_BIND ${EFN_WG_BIND} harus IP WireGuard di 10.88.0.0/16 atau 10.89.0.0/16" || return 1
  [[ "$EFN_PUBLIC_BIND" != "$EFN_WG_BIND" ]] || _efn_err "EFN_PUBLIC_BIND dan EFN_WG_BIND harus berbeda" || return 1

  [[ "$EFN_WG_HTTPS_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( EFN_WG_HTTPS_PORT <= 65535 )) \
    || _efn_err "EFN_WG_HTTPS_PORT tidak valid" || return 1

  # SEC-50a: allowlist sumber wg-https hanya subnet WireGuard (bukan 0.0.0.0/0 / RFC1918 lebar)
  for v in EFN_WG_OUTLET_CIDR EFN_WG_STAFF_CIDR; do
    efn_is_cidr "${!v}" || _efn_err "$v bukan CIDR kanonik (alamat jaringan/prefix)" || return 1
    efn_cidr_within "${!v}" "$EFN_WG_SUPERNET" && (( ${!v#*/} >= 16 )) \
      || _efn_err "$v=${!v} harus di dalam ${EFN_WG_SUPERNET} dengan prefix >= /16 (bukan 0.0.0.0/0 / terlalu lebar)" || return 1
  done

  [[ "$COMPOSE_PROFILES" =~ ^[a-z]+(,[a-z]+)*$ ]] || _efn_err "COMPOSE_PROFILES tidak valid" || return 1
  local -a profs
  IFS=, read -r -a profs <<< "$COMPOSE_PROFILES"
  for v in "${profs[@]}"; do
    efn_list_has "$v" "${EFN_KNOWN_PROFILES[@]}" || _efn_err "profil compose tidak dikenal: $v" || return 1
  done
  if [[ "$app_env" == production ]]; then
    for v in "${EFN_REQUIRED_PROD_PROFILES[@]}"; do
      efn_list_has "$v" "${profs[@]}" \
        || _efn_err "production wajib COMPOSE_PROFILES memuat ${EFN_REQUIRED_PROD_PROFILES[*]} (tidak ada: $v)" || return 1
    done
  fi
  local -a allow
  read -r -a allow <<< "$EFN_ALLOW_CONTROL_OFF"
  for v in "${allow[@]}"; do
    efn_list_has "$v" "${EFN_SECURITY_CONTROLS[@]}" || _efn_err "EFN_ALLOW_CONTROL_OFF: nama tidak dikenal $v" || return 1
  done
}

# G20 (docs/04 §12.1): daftar kanal koma -> setiap kode WAJIB di EFN_ONLINE_CHANNEL_CODES dan unik. Dipakai
# efn_validate_config dan scripts/deploy.sh (overlay + cek API key). rc 1 + alasan di stderr.
efn_validate_online_channels() { # daftar
  local raw="$1" c
  local -a list
  local -A seen=()
  [[ "$raw" =~ ^[A-Z][A-Z0-9-]{1,15}(,[A-Z][A-Z0-9-]{1,15}){0,7}$ ]] || _efn_err "ONLINE_CHANNELS tidak valid" || return 1
  IFS=, read -r -a list <<< "$raw"
  for c in "${list[@]}"; do
    efn_list_has "$c" "${EFN_ONLINE_CHANNEL_CODES[@]}" \
      || _efn_err "ONLINE_CHANNELS: kanal ${c} tidak dikenal (G20: ${EFN_ONLINE_CHANNEL_CODES[*]}; RTL/FNB & kanal G19 dihapus, docs/04 §12.1)" \
      || return 1
    [[ -z "${seen[$c]:-}" ]] || _efn_err "ONLINE_CHANNELS: kanal ${c} ganda" || return 1
    seen[$c]=1
  done
}

# ── Konfigurasi rilis (manifest bertanda tangan / env manual Superadmin) ──────────────────────────────────────
# Nilai kosong = default deploy.sh. Regex per variabel; tidak ada nilai bebas.
efn_validate_config() { # app_env
  local app_env="$1" v
  for v in "${EFN_RELEASE_CONFIG_VARS[@]}"; do efn_no_cntrl "$v" "${!v:-}" || return 1; done
  _efn_re() { [[ -z "${!1:-}" || "${!1}" =~ $2 ]] || _efn_err "$1 tidak valid"; }
  [[ "${ACME_EMAIL:-}" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$ ]] || _efn_err "ACME_EMAIL tidak valid" || return 1
  [[ "${PAYMENT_PROVIDER:-}" =~ ^(mock|disabled|xendit|midtrans)$ ]] \
    || _efn_err "PAYMENT_PROVIDER wajib eksplisit: mock|disabled|xendit|midtrans" || return 1
  [[ "$app_env" == production && "$PAYMENT_PROVIDER" == mock ]] \
    && { _efn_err "production menolak PAYMENT_PROVIDER=mock (docs/04 §8.3)"; return 1; }
  _efn_re PG_SUPERUSER '^[a-z_][a-z0-9_]{0,62}$' || return 1
  _efn_re WHATSAPP_PHONE_NUMBER_ID '^[0-9]{1,32}$' || return 1
  _efn_re WHATSAPP_OTP_TEMPLATE '^[a-z0-9_]{1,128}$' || return 1
  _efn_re ACME_DNS_PROVIDER '^[a-z0-9]{1,32}$' || return 1
  _efn_re ACME_DNS_TOKEN_ENV '^[A-Z][A-Z0-9_]{1,63}$' || return 1
  _efn_re ACME_SERVER '^https://acme-(staging-)?v02\.api\.letsencrypt\.org/directory$' || return 1
  _efn_re METABASE_ADMIN_EMAIL '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' || return 1
  _efn_re OLLAMA_VISION_MODEL '^[a-z0-9][a-z0-9._/-]{0,127}(:[a-z0-9._-]{1,64})?$' || return 1
  for v in MARKET_SCOUT_PHOTO_RETENTION_DAYS CH_MART_RETENTION_YEARS DSAR_DELETE_GRACE_DAYS; do
    _efn_re "$v" '^[0-9]{1,5}$' || return 1
  done
  for v in WHATSAPP_ENABLED RABBITMQ_STRICT_PERMISSIONS ETL_ENABLE_ODOO ETL_ENABLE_GL ETL_AUDIT_COPY_ENABLED MIGRATION_ENABLED \
           ONLINE_ENABLED ONLINE_CATALOG_ENABLED LOYALTY_EARN_EXCLUDE_POINT_PAYMENT EFN_ODOO_PII_ENABLED EFN_ODOO_PII_REQUIRED; do
    _efn_re "$v" '^(true|false)$' || return 1
  done
  # G21 AC-13 (docs/04 §13.2): production dengan kunci PII Odoo ter-mount WAJIB mode terkunci eksplisit
  # (EFN_ODOO_PII_REQUIRED=true; kosong tidak cukup). Tanpa itu kunci yang hilang saat berjalan hanya WARNING dan Odoo
  # bisa menulis PII baru tanpa enkripsi yang terdeteksi (L-63). Tidak bisa dimatikan lewat EFN_ALLOW_CONTROL_OFF.
  if [[ "$app_env" == production && "${EFN_ODOO_PII_ENABLED:-false}" == true && "${EFN_ODOO_PII_REQUIRED:-}" != true ]]; then
    _efn_err "production EFN_ODOO_PII_ENABLED=true wajib EFN_ODOO_PII_REQUIRED=true (AC-13, docs/runbooks/key-custody.md)"
    return 1
  fi
  # SEC-148 (r13, QA G21): mode migrasi `efnpii1:` (EFN_ODOO_PII_ACCEPT_V1=true) hanya untuk data PoC dev/staging.
  # Bukan konfigurasi rilis (tidak ada di EFN_RELEASE_CONFIG_VARS, tidak diteruskan compose) - ini lapis kedua untuk
  # jalur manual `sudo -E bash deploy.sh`: production menolak nilai selain kosong/false. Tidak bisa dimatikan lewat
  # EFN_ALLOW_CONTROL_OFF.
  if [[ -n "${EFN_ODOO_PII_ACCEPT_V1:-}" ]]; then
    _efn_re EFN_ODOO_PII_ACCEPT_V1 '^(true|false)$' || return 1
    if [[ "$app_env" == production && "$EFN_ODOO_PII_ACCEPT_V1" == true ]]; then
      _efn_err "production menolak EFN_ODOO_PII_ACCEPT_V1=true (mode migrasi efnpii1, SEC-148)"
      return 1
    fi
  fi
  # G17 penjualan online (docs/04 §10.1/§10.13; angka PLACEHOLDER pilot). Kosong = default compose (online MATI).
  _efn_re ONLINE_CHANNELS '^[A-Z][A-Z0-9-]{1,15}(,[A-Z][A-Z0-9-]{1,15}){0,7}$' || return 1
  efn_validate_online_channels "${ONLINE_CHANNELS:-$EFN_ONLINE_BASE_CHANNEL}" || return 1
  _efn_re ONLINE_FULFILMENTS '^(pickup|delivery)(,(pickup|delivery))?$' || return 1
  _efn_re ETL_LOYALTY_POINTS_MODE '^(product|payment)$' || return 1
  # SEC-114 (L-25b): mode `product` mematikan rekonsiliasi harian K-18-6 dan membuat redeem sah menjadi anomali palsu
  # (Odoo G18 menolak produk poin). Hanya untuk data historis di staging/dev.
  if [[ "$app_env" == production && "${ETL_LOYALTY_POINTS_MODE:-payment}" == product ]]; then
    _efn_err "production menolak ETL_LOYALTY_POINTS_MODE=product (SEC-114; pakai payment)"; return 1
  fi
  local spec name lo hi
  for spec in ONLINE_PAYMENT_TIMEOUT_SECONDS:60:3600 ONLINE_QUOTE_TTL_SECONDS:60:1800 \
              ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:1:20 ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:1:10 \
              ONLINE_CATALOG_SYNC_SECONDS:60:86400 \
              ONLINE_PICKUP_CODE_MAX_DAYS:1:30 ONLINE_ORDER_MEMBER_LINK_DAYS:30:3650 \
              ONLINE_EVENT_RETENTION_DAYS:7:3650 ONLINE_ADMIN_AUDIT_RETENTION_DAYS:30:3650 ODOO_RPC_TIMEOUT_MS:1000:30000 ODOO_RPC_MAXCONN:1:16 \
              ODOO_RPC_PUBLIC_MAX_CONCURRENCY:1:16 ODOO_RPC_SAGA_MAX_CONCURRENCY:1:16 \
              DSAR_OPEN_ORDER_MAX_DEFER_DAYS:1:14 MEMBER_MIN_AGE:17:21 PRICE_HUNTER_GEO_RETENTION_DAYS:1:3650 \
              CH_MART_RETENTION_YEARS:1:30 CH_PSEUDONYM_RETENTION_YEARS:1:30 CH_GEO_RETENTION_DAYS:1:3650 \
              MB_AUDIT_MAX_RETENTION_DAYS:30:3650 LOYALTY_POINT_VALUE_IDR:1:1000000 ETL_AUDIT_EPOCH_ANCHORS:1:1000; do
    IFS=: read -r name lo hi <<< "$spec"
    [[ -z "${!name:-}" ]] && continue
    [[ "${!name}" =~ ^[1-9][0-9]{0,5}$ ]] && (( ${!name} >= lo && ${!name} <= hi )) \
      || _efn_err "$name harus bilangan bulat ${lo}..${hi}" || return 1
  done
  # r11 (SEC-118/124, L-25f/l): toleransi restore poin 0..50 % (0 sah: harus persis); retensi reservasi committed
  # = catatan keuangan, minimal 10 th (BFF menolak < 3650, plafon 7300)
  if [[ -n "${LOYALTY_RESTORE_TOLERANCE_PCT:-}" ]]; then
    [[ "$LOYALTY_RESTORE_TOLERANCE_PCT" =~ ^(0|[1-9][0-9]?)$ ]] && (( LOYALTY_RESTORE_TOLERANCE_PCT <= 50 )) \
      || _efn_err "LOYALTY_RESTORE_TOLERANCE_PCT harus bilangan bulat 0..50" || return 1
  fi
  if [[ -n "${LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS:-}" ]]; then
    [[ "$LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS" =~ ^[1-9][0-9]{3}$ ]] \
      && (( LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS >= 3650 && LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS <= 7300 )) \
      || _efn_err "LOYALTY_COMMITTED_RESERVATION_RETENTION_DAYS harus bilangan bulat 3650..7300" || return 1
  fi
  # W-19 (Q-P12, 10 Okt 2026): daftar tolak erasure 365 h (default BFF); 0 = tidak disimpan, plafon BFF 730
  if [[ -n "${ERASURE_SUPPRESSION_DAYS:-}" ]]; then
    [[ "$ERASURE_SUPPRESSION_DAYS" =~ ^(0|[1-9][0-9]{0,2})$ ]] && (( ERASURE_SUPPRESSION_DAYS <= 730 )) \
      || _efn_err "ERASURE_SUPPRESSION_DAYS harus bilangan bulat 0..730" || return 1
  fi
  # W-19 (data-AI, clickhouse-init): kolom pseudonim tidak boleh hidup lebih lama dari barisnya (kosong = 5 / 10)
  if (( ${CH_PSEUDONYM_RETENTION_YEARS:-5} > ${CH_MART_RETENTION_YEARS:-10} )); then
    _efn_err "CH_PSEUDONYM_RETENTION_YEARS (${CH_PSEUDONYM_RETENTION_YEARS:-5}) > CH_MART_RETENTION_YEARS (${CH_MART_RETENTION_YEARS:-10})"; return 1
  fi
  # SEC-96/SEC-109 (L-19f), sizing pilot opsi B (10 Okt 2026): pool json2 BFF (publik + saga) tidak boleh melebihi
  # koneksi gateway ke Odoo (= worker yang disisihkan untuk online). Kosong = default compose PROD (overlay
  # docker-compose.prod.yml: maxconn 2, pool 1 + 1) - deploy.sh selalu memakai overlay prod (staging juga).
  local eff_max="${ODOO_RPC_MAXCONN:-2}" eff_pub="${ODOO_RPC_PUBLIC_MAX_CONCURRENCY:-1}" eff_saga="${ODOO_RPC_SAGA_MAX_CONCURRENCY:-1}"
  if (( eff_pub + eff_saga > eff_max )); then
    _efn_err "ODOO_RPC_PUBLIC_MAX_CONCURRENCY (${eff_pub}) + ODOO_RPC_SAGA_MAX_CONCURRENCY (${eff_saga}) > ODOO_RPC_MAXCONN (${eff_max}) - worker Odoo POS bisa terpakai online (docs/runbooks/online-orders.md#sizing-worker)"
    return 1
  fi
  # G20 W-30 (docs/04 §12.2): order bayar-di-kasir adalah order terbuka juga -> batas counter <= batas total
  # (kosong = default compose 1 / 2).
  if (( ${ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:-1} > ${ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:-2} )); then
    _efn_err "ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER (${ONLINE_MAX_OPEN_COUNTER_ORDERS_PER_MEMBER:-1}) > ONLINE_MAX_OPEN_ORDERS_PER_MEMBER (${ONLINE_MAX_OPEN_ORDERS_PER_MEMBER:-2})"
    return 1
  fi
  # ONLINE_PAYMENT_METHODS (L-55): daftar qris,counter tanpa duplikat.
  local pay_methods="${ONLINE_PAYMENT_METHODS:-qris,counter}"
  if [[ ! "$pay_methods" =~ ^(qris|counter)(,(qris|counter))?$ || "$pay_methods" == qris,qris || "$pay_methods" == counter,counter ]]; then
    _efn_err "ONLINE_PAYMENT_METHODS harus qris|counter|qris,counter (bukan ${pay_methods})"; return 1
  fi
  # Online di production: qris butuh payment provider nyata (mock ditolak di atas). Pilot bayar di kasir (L-55)
  # boleh tanpa vendor HANYA bila ONLINE_PAYMENT_METHODS=counter dan PAYMENT_PROVIDER=disabled.
  if [[ "$app_env" == production && "${ONLINE_ENABLED:-false}" == true && ! "${PAYMENT_PROVIDER}" =~ ^(xendit|midtrans)$ ]]; then
    if [[ "$pay_methods" != counter || "${PAYMENT_PROVIDER}" != disabled ]]; then
      _efn_err "production ONLINE_ENABLED=true butuh PAYMENT_PROVIDER xendit|midtrans, atau ONLINE_PAYMENT_METHODS=counter + PAYMENT_PROVIDER=disabled (L-55)"
      return 1
    fi
  fi
  # Saklar dev/test (stub json2 BFF L-7, simulator bayar frontend) tidak boleh aktif di staging/production
  for v in "${EFN_DEV_ONLY_SWITCHES[@]}"; do
    efn_no_cntrl "$v" "${!v:-}" || return 1
    if [[ -n "${!v:-}" && "${!v}" != false ]]; then
      _efn_err "${v}=${!v} ditolak di ${app_env} (hanya dev/test)"; return 1
    fi
  done
  # G14: go-live loyalty POS untuk rekonsiliasi ledger poin vs pos.order (ETL). UTC kanonik saja (perbandingan
  # leksikal = kronologis). Masa depan ditolak: cutoff > sekarang = rekonsiliasi buta (kontrol detektif mati diam-diam).
  _efn_re ETL_LOYALTY_RECON_NOT_BEFORE \
    '^20[0-9]{2}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$' || return 1
  if [[ -n "${ETL_LOYALTY_RECON_NOT_BEFORE:-}" && "$ETL_LOYALTY_RECON_NOT_BEFORE" > "$(date -u +%Y-%m-%dT%H:%M:%SZ)" ]]; then
    _efn_err "ETL_LOYALTY_RECON_NOT_BEFORE di masa depan (rekonsiliasi poin akan buta)"; return 1
  fi
  local p ver sha
  for p in LOCATION MARKETING; do
    ver="CONSENT_${p}_VERSION"; sha="CONSENT_${p}_TEXT_SHA256"
    _efn_re "$ver" '^[A-Za-z0-9._@-]{1,32}$' || return 1
    _efn_re "$sha" '^[0-9a-f]{64}$' || return 1
    if [[ -n "${!ver:-}" && -z "${!sha:-}" ]] || [[ -z "${!ver:-}" && -n "${!sha:-}" ]]; then
      _efn_err "$ver dan $sha harus di-set berpasangan"; return 1
    fi
  done
  # SEC-50c: kontrol keamanan tidak bisa dimatikan di production kecuali izin eksplisit Superadmin di deploy.conf
  if [[ "$app_env" == production ]]; then
    local -a allow
    read -r -a allow <<< "${EFN_ALLOW_CONTROL_OFF:-}"
    for v in "${EFN_SECURITY_CONTROLS[@]}"; do
      if [[ "${!v:-}" == false ]] && ! efn_list_has "$v" "${allow[@]}"; then
        _efn_err "production menolak ${v}=false (kontrol keamanan; izin hanya lewat EFN_ALLOW_CONTROL_OFF di /etc/efn/deploy.conf)"
        return 1
      fi
    done
  fi
}

# Baca kunci topologi yang dipin dari deploy.conf (root, tidak bisa ditulis group/other). Nilai env yang BERBEDA
# dari pin = ditolak (jalur manual tidak bisa diam-diam menimpa pin). Kunci lain di file diabaikan di sini.
efn_load_pinned_conf() { # file
  local f="$1" k v
  [[ -f "$f" && ! -L "$f" ]] || return 0
  [[ "$(stat -c '%u:%a' "$f")" =~ ^0:(6[04]4|600|640)$ ]] || _efn_err "$f harus milik root dan tidak bisa ditulis group/other" || return 1
  while IFS='=' read -r k v || [[ -n "$k" ]]; do
    efn_list_has "$k" "${EFN_HOST_PINNED_VARS[@]}" || continue
    v="${v#\'}"; v="${v%\'}"
    efn_no_cntrl "$k" "$v" || return 1
    if [[ -n "${!k:-}" && "${!k}" != "$v" ]]; then
      _efn_err "$k dari env (${!k}) berbeda dengan pin di $f ($v)"; return 1
    fi
    printf -v "$k" '%s' "$v"
    export "${k?}"
  done < "$f"
}

# Tulis .env "satu kunci satu baris": kunci unik, nilai tanpa karakter kontrol, jumlah baris diverifikasi ulang.
efn_write_dotenv() { # file KEY=val...
  local f="$1"; shift
  local tmp e k val
  local -A seen=()
  tmp=$(mktemp "${f}.XXXXXX")
  for e in "$@"; do
    k="${e%%=*}"; val="${e#*=}"
    [[ "$k" =~ ^[A-Z][A-Z0-9_]{0,63}$ ]] || { rm -f "$tmp"; _efn_err ".env: kunci tidak valid '$k'"; return 1; }
    [[ -z "${seen[$k]:-}" ]] || { rm -f "$tmp"; _efn_err ".env: kunci ganda $k"; return 1; }
    efn_no_cntrl "$k" "$val" || { rm -f "$tmp"; return 1; }
    seen[$k]=1
    printf '%s=%s\n' "$k" "$val" >> "$tmp"
  done
  local lines
  lines=$(wc -l < "$tmp")
  [[ "$lines" -eq $# ]] || { rm -f "$tmp"; _efn_err ".env: ${lines} baris != $# kunci"; return 1; }
  if grep -Evq '^[A-Z][A-Z0-9_]*=[^[:cntrl:]]*$' "$tmp"; then rm -f "$tmp"; _efn_err ".env: baris tidak valid"; return 1; fi
  chmod 0640 "$tmp"
  mv -f "$tmp" "$f"
}

# SEC-129 (r11): modul Odoo efn WAJIB berstatus `installed` di DB efn_odoo. Tanpa efn_pos_refund_origin kontrol E-31
# (refund ke metode asal) tidak aktif tanpa terdeteksi (SEC-122). Tambah modul wajib lain di sini.
EFN_REQUIRED_ODOO_MODULES=(efn_pos_refund_origin)
# stdin: baris `<nama>|<state>` hasil query ir_module_module. Cetak modul yang hilang/tidak `installed`
# (`<nama>=<state|absent>`); return 1 bila ada.
efn_odoo_modules_missing() {
  local line name state m bad=0
  declare -A st=()
  while IFS= read -r line; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^([a-z0-9_]{1,64})\|([a-z\ ]{1,32})$ ]] || continue
    name="${BASH_REMATCH[1]}"; state="${BASH_REMATCH[2]}"
    st["$name"]="$state"
  done
  for m in "${EFN_REQUIRED_ODOO_MODULES[@]}"; do
    if [[ "${st[$m]:-absent}" != installed ]]; then echo "${m}=${st[$m]:-absent}"; bad=1; fi
  done
  return "$bad"
}

# G21 AC-13 (docs/04 §13.2, ADR-036 §0): ketiga kunci PII Odoo di <dir> harus ada sebagai file biasa (bukan symlink),
# berisi TEPAT 32 byte base64 kanonik (44 karakter, satu '=' penutup; newline akhir boleh), bukan placeholder `disabled`,
# dan ketiganya BERBEDA (satu kunci untuk dua peran = kebocoran satu membuka keduanya, SEC-140 c). Nilai kunci tidak
# pernah dicetak; alasan hanya menyebut nama file. rc 1 + alasan di stderr.
efn_validate_pii_keys() { # dir
  local dir="$1" n f v bad=0
  local -A seen=()
  for n in "${EFN_ODOO_PII_KEY_NAMES[@]}"; do
    f="${dir}/${n}.txt"
    if [[ -L "$f" || ! -f "$f" || ! -s "$f" ]]; then
      _efn_err "kunci PII ${n}: file ${n}.txt tidak ada/kosong/symlink (docs/runbooks/key-custody.md §4e)"; bad=1; continue
    fi
    v=$(tr -d '\n' < "$f")
    if [[ "$v" == disabled ]]; then
      _efn_err "kunci PII ${n}: masih placeholder 'disabled' (Superadmin belum membuat kunci produksi)"; bad=1; continue
    fi
    # 32 byte -> base64 44 karakter: 42 karakter bebas + 1 karakter dengan 2 bit terakhir nol (AEIMQUYcgkosw048) + '='
    if [[ ! "$v" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]] || [[ "$(wc -l < "$f")" -gt 1 ]]; then
      _efn_err "kunci PII ${n}: bukan 32 byte base64 kanonik (openssl rand -base64 32)"; bad=1; continue
    fi
    if [[ -n "${seen[$v]:-}" ]]; then
      _efn_err "kunci PII ${n}: sama dengan ${seen[$v]} (ketiga kunci wajib berbeda, ADR-036 §0.5)"; bad=1; continue
    fi
    seen[$v]="$n"
  done
  return "$bad"
}

# G21 AC-13: status modul efn_pii di DB efn_odoo vs saklar overlay. Modul terpasang (installed / to upgrade / to remove)
# tanpa EFN_ODOO_PII_ENABLED=true = Odoo berjalan TANPA kunci (data terlindungi tidak terbaca; ADR-036 mode terkunci)
# -> deploy gagal. `uninstalled`/`uninstallable`/`to install`/kosong (modul tidak dikenal DB) = boleh tanpa kunci.
efn_pii_module_state_ok() { # enabled state
  local enabled="$1" state="${2%$'\r'}"
  case "$state" in
    installed|"to upgrade"|"to remove")
      [[ "$enabled" == true ]] \
        || _efn_err "efn_pii berstatus '${state}' di efn_odoo tetapi EFN_ODOO_PII_ENABLED bukan true (kunci PII tidak di-mount)" ;;
    *) return 0 ;;
  esac
}
